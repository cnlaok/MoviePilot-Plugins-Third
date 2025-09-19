import time
from typing import List, Dict, Optional, Tuple, Any

from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import MediaType
try:
    from app.modules.search import SearchResult
except ImportError:
    # 兼容旧版MoviePilot
    SearchResult = dict  # 或实现一个兼容类
from app.db.systemconfig_oper import SystemConfigOper


class NullbrSearch(_PluginBase):
    # 插件基本信息
    plugin_name = "Nullbr资源搜索"
    plugin_desc = "通过Nullbr API搜索115网盘资源，支持电影和电视剧。"
    plugin_icon = "https://raw.githubusercontent.com/Hqyel/MoviePilot-Plugins/main/icons/nullbr.png"
    plugin_version = "2.0"
    plugin_author = "Hqyel"
    author_url = "https://github.com/Hqyel"
    plugin_config_prefix = "nullbr_search_"
    plugin_order = 100  # 搜索优先级（数值越小越优先）
    auth_level = 1

    # 私有变量
    _client = None
    _enabled = False
    _app_id = ""
    _api_key = ""
    _search_timeout = 30

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        :param config: 插件配置
        """
        if config:
            self._enabled = config.get("enabled", False)
            self._app_id = config.get("app_id", "")
            self._api_key = config.get("api_key", "")
            self._search_timeout = config.get("search_timeout", 30)

            # 初始化API客户端
            if self._enabled and self._app_id:
                try:
                    from .nullbr_client import NullbrApiClient
                    self._client = NullbrApiClient(
                        app_id=self._app_id,
                        api_key=self._api_key
                    )
                    logger.info("Nullbr API客户端初始化成功")
                except Exception as e:
                    logger.error(f"Nullbr API客户端初始化失败: {str(e)}")
                    self._client = None
                    self._enabled = False
            else:
                self._client = None
                if not self._app_id:
                    logger.warning("Nullbr插件配置错误: 缺少APP_ID")

    def get_state(self) -> bool:
        """
        获取插件状态
        :return: 是否启用
        """
        return self._enabled

    def get_search_order(self) -> int:
        """
        获取搜索优先级
        :return: 优先级数值
        """
        return self.plugin_order

    def search(self, keyword: str, 
               media_type: MediaType = None,
               season: int = None,
               page: int = 1) -> Optional[List[SearchResult]]:
        """
        执行搜索
        :param keyword: 关键词
        :param media_type: 媒体类型
        :param season: 季号
        :param page: 页码
        :return: 搜索结果列表
        """
        if not self._enabled or not self._client:
            return None

        try:
            # 调用API搜索
            api_result = self._client.search(
                query=keyword,
                media_type="movie" if media_type == MediaType.MOVIE else "tv",
                page=page
            )

            if not api_result or not api_result.get("items"):
                logger.warning(f"未找到关键词'{keyword}'的相关资源")
                return None

            # 转换为SearchResult格式
            search_results = []
            for item in api_result.get("items", []):
                # 只处理115资源
                if not item.get("115-flg"):
                    continue

                # 获取资源详情
                resources = self._get_resources(
                    tmdbid=item.get("tmdbid"),
                    media_type=media_type
                )

                search_results.append(SearchResult(
                    title=item.get("title", ""),
                    year=item.get("release_date", "")[:4] if item.get("release_date") else "",
                    type=media_type or (MediaType.MOVIE if item.get("media_type") == "movie" else MediaType.TV),
                    tmdbid=item.get("tmdbid"),
                    vote=item.get("vote_average", 0),
                    image=item.get("poster_path", ""),
                    overview=item.get("overview", ""),
                    resource_type="115",
                    url=resources[0]["url"] if resources else "",
                    size=resources[0]["size"] if resources else "",
                    resolution=resources[0].get("resolution", "")
                ))

            return search_results

        except Exception as e:
            logger.error(f"搜索'{keyword}'时发生错误: {str(e)}")
            return None

    def _get_resources(self, tmdbid: str, media_type: MediaType) -> List[Dict]:
        """
        获取资源详情（内部方法）
        :param tmdbid: TMDB ID
        :param media_type: 媒体类型
        :return: 资源详情列表
        """
        if not self._client or not self._api_key or not tmdbid:
            return []

        try:
            if media_type == MediaType.MOVIE:
                resources = self._client.get_movie_resources(tmdbid, "115")
            else:
                resources = self._client.get_tv_resources(tmdbid, "115")

            if not resources or not resources.get("115"):
                return []

            return [
                {
                    "title": res.get("title", ""),
                    "size": res.get("size", ""),
                    "url": res.get("share_link", ""),
                    "resolution": res.get("resolution", ""),
                    "type": "115"
                }
                for res in resources.get("115", [])
            ]

        except Exception as e:
            logger.error(f"获取资源详情失败: {str(e)}")
            return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        获取插件配置表单
        :return: (表单组件列表, 表单默认值)
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'app_id',
                                            'label': 'APP_ID',
                                            'placeholder': '请输入Nullbr API的APP_ID',
                                            'required': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'api_key',
                                            'label': 'API_KEY',
                                            'placeholder': '请输入Nullbr API的API_KEY',
                                            'type': 'password'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'search_timeout',
                                            'label': '搜索超时(秒)',
                                            'type': 'number',
                                            'min': 10,
                                            'max': 120
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "app_id": "",
            "api_key": "",
            "search_timeout": 30
        }

    def get_page(self) -> List[dict]:
        """
        获取插件详情页面
        :return: 页面配置
        """
        return [
            {
                'component': 'VCard',
                'content': [
                    {
                        'component': 'VCardTitle',
                        'props': {
                            'class': 'text-h5',
                            'style': 'padding-bottom: 0.5rem'
                        },
                        'content': 'Nullbr资源搜索'
                    },
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'VAlert',
                                'props': {
                                    'type': 'info',
                                    'variant': 'tonal',
                                    'text': '本插件通过Nullbr API搜索115网盘资源，请确保已正确配置APP_ID和API_KEY。'
                                }
                            }
                        ]
                    }
                ]
            }
        ]

    def stop_service(self):
        """
        停止插件服务
        """
        try:
            if self._client:
                logger.info("正在关闭Nullbr API客户端...")
                self._client = None
            self._enabled = False
        except Exception as e:
            logger.error(f"停止插件时发生错误: {str(e)}")
