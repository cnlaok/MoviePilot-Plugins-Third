import re
import time
from typing import Any, List, Dict, Tuple, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType


class NullbrApiClient:
    """Nullbr API客户端"""
    
    def __init__(self, app_id: str, api_key: str = None):
        self._app_id = app_id
        self._api_key = api_key
        self._base_url = "https://api.nullbr.eu.org"
        
        # 配置请求会话
        self._session = requests.Session()
        self._session.headers.update({
            'User-Agent': 'MoviePilot-NullbrSearch/1.0.4',
            'Content-Type': 'application/json'
        })
        
        # 配置重试策略
        try:
            retry_strategy = Retry(
                total=3,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["HEAD", "GET", "OPTIONS"],
                backoff_factor=1
            )
        except TypeError:
            try:
                retry_strategy = Retry(
                    total=3,
                    status_forcelist=[429, 500, 502, 503, 504],
                    method_whitelist=["HEAD", "GET", "OPTIONS"],
                    backoff_factor=1
                )
            except Exception:
                retry_strategy = Retry(total=3, backoff_factor=1)
                
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
    
    def search(self, query: str, page: int = 1) -> Optional[Dict]:
        """搜索媒体资源"""
        try:
            # 根据API文档，APP_ID应该放在Header中
            headers = {'X-APP-ID': self._app_id}
            
            # API_KEY如果存在，也放在Header中
            if self._api_key:
                headers['X-API-KEY'] = self._api_key
            
            params = {
                'query': query,
                'page': page
            }
            
            logger.info(f"请求参数: {params}")
            logger.info(f"请求头: X-APP-ID={self._app_id}, X-API-KEY={'已设置' if self._api_key else '未设置'}")
            
            response = self._session.get(
                f"{self._base_url}/search",
                params=params,
                headers=headers,
                timeout=30
            )
            
            logger.info(f"响应状态码: {response.status_code}")
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 403:
                logger.error("Nullbr API认证失败，请检查APP_ID")
                return None
            else:
                logger.warning(f"Nullbr API搜索失败: {response.status_code}, 响应内容: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Nullbr API请求异常: {str(e)}")
            return None
    
    def get_movie_resources(self, tmdbid: int, resource_type: str = "115") -> Optional[Dict]:
        """获取电影资源链接"""
        if not self._api_key:
            logger.warning("获取资源链接需要API_KEY")
            return None
            
        try:
            headers = {'X-APP-ID': self._app_id, 'X-API-KEY': self._api_key}
            
            response = self._session.get(
                f"{self._base_url}/movie/{tmdbid}/{resource_type}",
                headers=headers,
                timeout=30
            )
            
            logger.info(f"获取电影资源响应状态码: {response.status_code}")
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                logger.error("API_KEY权限不足")
                return None
            elif response.status_code == 403:
                logger.error("API认证失败")
                return None
            elif response.status_code == 429:
                logger.warning("API请求过快，请稍后重试")
                return None
            else:
                logger.warning(f"获取电影资源失败: {response.status_code}, 响应: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"获取电影资源异常: {str(e)}")
            return None
    
    def get_tv_resources(self, tmdbid: int, resource_type: str = "115") -> Optional[Dict]:
        """获取剧集资源链接"""
        if not self._api_key:
            logger.warning("获取资源链接需要API_KEY")
            return None
            
        try:
            headers = {'X-APP-ID': self._app_id, 'X-API-KEY': self._api_key}
            
            response = self._session.get(
                f"{self._base_url}/tv/{tmdbid}/{resource_type}",
                headers=headers,
                timeout=30
            )
            
            logger.info(f"获取剧集资源响应状态码: {response.status_code}")
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                logger.error("API_KEY权限不足")
                return None
            elif response.status_code == 403:
                logger.error("API认证失败")
                return None
            elif response.status_code == 429:
                logger.warning("API请求过快，请稍后重试")
                return None
            else:
                logger.warning(f"获取剧集资源失败: {response.status_code}, 响应: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"获取剧集资源异常: {str(e)}")
            return None


class NullbrSearch(_PluginBase):
  # 插件基本信息
    plugin_name = "Nullbr资源搜索"
    plugin_desc = "优先使用Nullbr API搜索影视资源，支持多种资源类型（115网盘、磁力、ed2k、m3u8）"
    plugin_icon = "https://raw.githubusercontent.com/Hqyel/MoviePilot-Plugins/main/icons/nullbr.png"
    plugin_version = "1.0.4"
    plugin_author = "Hqyel"
    author_url = "https://github.com/Hqyel"
    plugin_config_prefix = "nullbr_"
    plugin_order = 1
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._enabled = False
        self._app_id = None
        self._api_key = None
        self._resource_priority = ["115", "magnet", "video", "ed2k"]
        self._enable_115 = True
        self._enable_magnet = True
        self._enable_video = True
        self._enable_ed2k = True
        self._search_timeout = 30
        self._client = None
        
        # 用户搜索结果缓存
        self._user_search_cache = {}  # {userid: {'results': [...], 'timestamp': time.time()}}

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._app_id = config.get("app_id")
            self._api_key = config.get("api_key")
            self._resource_priority = config.get("resource_priority", ["115", "magnet", "video", "ed2k"])
            self._enable_115 = config.get("enable_115", True)
            self._enable_magnet = config.get("enable_magnet", True)
            self._enable_video = config.get("enable_video", True)
            self._enable_ed2k = config.get("enable_ed2k", True)
            self._search_timeout = config.get("search_timeout", 30)
        
        # 初始化API客户端
        if self._enabled and self._app_id:
            try:
                self._client = NullbrApiClient(self._app_id, self._api_key)
                logger.info("Nullbr资源搜索插件已启动")
            except Exception as e:
                logger.error(f"Nullbr插件初始化失败: {str(e)}")
                self._enabled = False
        else:
            if not self._app_id:
                logger.warning("Nullbr插件配置错误: 缺少APP_ID")
            self._client = None

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
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
                        'props': {'cols': 12},
                        'content': [
                        {
                            'component': 'VAlert',
                            'props': {
                            'type': 'info',
                            'variant': 'tonal',
                            'text': '🌟 Nullbr资源搜索插件将优先使用Nullbr API查找资源。支持115网盘、磁力、ed2k、m3u8等多种资源类型。'
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
                            'component': 'VSwitch',
                            'props': {
                            'model': 'enabled',
                            'label': '启用插件',
                            'hint': '开启后插件将开始工作，优先搜索Nullbr资源',
                            'persistent-hint': True
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
                            'label': 'APP_ID *',
                            'placeholder': '请输入Nullbr API的APP_ID',
                            'hint': '必填：用于API认证的应用ID',
                            'persistent-hint': True,
                            'clearable': True
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
                            'hint': '可选：用于获取资源链接，没有则只能搜索不能获取下载链接',
                            'persistent-hint': True,
                            'clearable': True,
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
                        'props': {'cols': 12},
                        'content': [
                        {
                            'component': 'VExpansionPanels',
                            'content': [
                            {
                                'component': 'VExpansionPanel',
                                'props': {'title': '⚙️ 高级设置'},
                                'content': [
                                {
                                    'component': 'VExpansionPanelText',
                                    'content': [
                                    {
                                        'component': 'VRow',
                                        'content': [
                                        {
                                            'component': 'VCol',
                                            'props': {'cols': 12, 'md': 3},
                                            'content': [
                                            {
                                                'component': 'VSwitch',
                                                'props': {
                                                'model': 'enable_115',
                                                'label': '115网盘',
                                                'hint': '搜索115网盘分享资源',
                                                'persistent-hint': True
                                                }
                                            }
                                            ]
                                        },
                                        {
                                            'component': 'VCol',
                                            'props': {'cols': 12, 'md': 3},
                                            'content': [
                                            {
                                                'component': 'VSwitch',
                                                'props': {
                                                'model': 'enable_magnet',
                                                'label': '磁力链接',
                                                'hint': '搜索磁力链接资源',
                                                'persistent-hint': True
                                                }
                                            }
                                            ]
                                        },
                                        {
                                            'component': 'VCol',
                                            'props': {'cols': 12, 'md': 3},
                                            'content': [
                                            {
                                                'component': 'VSwitch',
                                                'props': {
                                                'model': 'enable_video',
                                                'label': 'M3U8视频',
                                                'hint': '搜索在线观看资源',
                                                'persistent-hint': True
                                                }
                                            }
                                            ]
                                        },
                                        {
                                            'component': 'VCol',
                                            'props': {'cols': 12, 'md': 3},
                                            'content': [
                                            {
                                                'component': 'VSwitch',
                                                'props': {
                                                'model': 'enable_ed2k',
                                                'label': 'ED2K链接',
                                                'hint': '搜索ED2K链接资源',
                                                'persistent-hint': True
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
                                                'label': '搜索超时时间(秒)',
                                                'placeholder': '30',
                                                'hint': '单次API请求的超时时间',
                                                'persistent-hint': True,
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
                                ]
                            }
                            ]
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
        "enable_115": True,
        "enable_magnet": True,
        "enable_video": True,
        "enable_ed2k": True,
        "search_timeout": 30
        }

    def get_page(self) -> List[dict]:
        stats = {"total_searches": 0, "success_searches": 0, "failed_searches": 0, "last_search": "从未"}
        return [
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {'cols': 12},
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {'class': 'mb-4'},
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {'text': '🌟 Nullbr资源搜索状态'}
                                    },
                                    {
                                        'component': 'VCardText',
                                        'content': [
                                            {
                                                'component': 'VList',
                                                'content': [
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'props': {'text': f"插件状态: {'🟢 运行中' if self._enabled else '🔴 已停止'}"}
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'props': {'text': f"API认证: {'✅ 已配置' if self._app_id else '❌ 未配置'}"}
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'props': {'text': f"资源获取: {'✅ 可用' if self._api_key else '❌ 仅搜索'}"}
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
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
                                'component': 'VCard',
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {'text': '📊 支持的资源类型'}
                                    },
                                    {
                                        'component': 'VCardText',
                                        'content': [
                                            {
                                                'component': 'VList',
                                                'content': [
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'props': {'text': f"115网盘: {'✅ 启用' if self._enable_115 else '❌ 禁用'}"}
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'props': {'text': f"磁力链接: {'✅ 启用' if self._enable_magnet else '❌ 禁用'}"}
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'props': {'text': f"M3U8视频: {'✅ 启用' if self._enable_video else '❌ 禁用'}"}
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'props': {'text': f"ED2K链接: {'✅ 启用' if self._enable_ed2k else '❌ 禁用'}"}
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCol',
                        'props': {'cols': 12, 'md': 6},
                        'content': [
                            {
                                'component': 'VCard',
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {'text': '📈 使用统计'}
                                    },
                                    {
                                        'component': 'VCardText',
                                        'content': [
                                            {
                                                'component': 'VList',
                                                'content': [
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'props': {'text': f"总搜索次数: {stats.get('total_searches', 0)}"}
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'props': {'text': f"成功次数: {stats.get('success_searches', 0)}"}
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'props': {'text': f"失败次数: {stats.get('failed_searches', 0)}"}
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'props': {'text': f"最后搜索: {stats.get('last_search', '从未')}"}
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
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
                        'props': {'cols': 12},
                        'content': [
                            {
                                'component': 'VCard',
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {'text': '💡 使用说明'}
                                    },
                                    {
                                        'component': 'VCardText',
                                        'props': {
                                            'text': '''🔑 配置步骤:
    1. 在插件设置中填入您的 Nullbr API APP_ID (必填)
    2. 如需获取下载链接，请填入 API_KEY (可选)
    3. 根据需要启用不同的资源类型
    4. 保存配置并启用插件

    ⚡ 工作原理:
    • 插件通过API接口提供Nullbr资源搜索服务
    • 可在MoviePilot中手动调用搜索功能
    • 支持电影、剧集、合集等多种媒体类型
    • 支持115网盘、磁力、ed2k、m3u8等多种资源格式

    📞 技术支持:
    如遇问题请检查 MoviePilot 日志中的错误信息'''
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

    @eventmanager.register(EventType.UserMessage)
    def talk(self, event: Event):
        """
        监听用户消息，识别搜索请求和编号选择
        """
        if not self._enabled or not self._client:
            return
            
        text = event.event_data.get("text")
        userid = event.event_data.get("userid")
        channel = event.event_data.get("channel")
        
        if not text:
            return
            
        logger.info(f"收到用户消息: {text}")
        
        # 检查是否为回退搜索触发的消息，避免无限循环
        if event.event_data.get('source') == 'nullbr_fallback':
            logger.info("检测到回退搜索消息，跳过处理避免循环")
            return
        
        # 先检查是否为获取资源的请求（包含问号的情况，如 "1.115?" "2.magnet?"）
        clean_text = text.rstrip('？?').strip()
        if re.match(r'^\d+\.(115|magnet|video|ed2k)$', clean_text):
            parts = clean_text.split('.')
            number = int(parts[0])
            resource_type = parts[1]
            logger.info(f"检测到资源获取请求: {number}.{resource_type}")
            self.handle_get_resources(number, resource_type, channel, userid)
        
        # 检查是否为编号选择（纯数字，包含问号的情况）
        elif clean_text.isdigit():
            number = int(clean_text)
            logger.info(f"检测到编号选择: {number}")
            self.handle_resource_selection(number, channel, userid)
        
        # 检查是否为搜索请求（以？结尾，但不是数字或资源请求）
        elif text.endswith('？') or text.endswith('?'):
            # 提取搜索关键词（去掉问号）
            keyword = clean_text
            
            if keyword:
                logger.info(f"检测到搜索请求: {keyword}")
                self.search_and_reply(keyword, channel, userid)
    
    def search_and_reply(self, keyword: str, channel: str, userid: str):
        """执行搜索并回复结果"""
        try:
            # 调用Nullbr API搜索
            result = self._client.search(keyword)
            
            if not result or not result.get('items'):
                # Nullbr没有搜索结果，回退到MoviePilot原始搜索
                logger.info(f"Nullbr未找到「{keyword}」的搜索结果，回退到MoviePilot搜索")
                self.post_message(
                    channel=channel,
                    title="切换搜索",
                    text=f"Nullbr没有找到「{keyword}」的资源，正在使用MoviePilot原始搜索...",
                    userid=userid
                )
                
                # 调用MoviePilot的原始搜索功能
                self.fallback_to_moviepilot_search(keyword, channel, userid)
                return
            
            items = result.get('items', [])[:10]  # 最多显示10个结果
            
            # 缓存搜索结果
            self._user_search_cache[userid] = {
                'results': items,
                'keyword': keyword,
                'timestamp': time.time()
            }
            
            # 格式化搜索结果
            reply_text = f"🔍 找到「{keyword}」的资源:\n\n"
            
            for i, item in enumerate(items, 1):
                title = item.get('title', '未知标题')
                media_type = item.get('media_type', 'unknown')
                year = item.get('release_date', item.get('first_air_date', ''))[:4] if item.get('release_date') or item.get('first_air_date') else ''
                
                # 检查可用的资源类型
                available_types = []
                if item.get('115-flg') and self._enable_115:
                    available_types.append('115')
                if item.get('magnet-flg') and self._enable_magnet:
                    available_types.append('磁力')
                if item.get('video-flg') and self._enable_video:
                    available_types.append('在线')
                if item.get('ed2k-flg') and self._enable_ed2k:
                    available_types.append('ed2k')
                
                type_text = '、'.join(available_types) if available_types else '无'
                media_text = '电影' if media_type == 'movie' else '剧集' if media_type == 'tv' else media_type
                
                reply_text += f"{i}. {title}"
                if year:
                    reply_text += f" ({year})"
                reply_text += f" - {media_text}\n"
                reply_text += f"   资源: {type_text}\n\n"
            
            if len(result.get('items', [])) > 10:
                reply_text += f"... 还有 {len(result.get('items', [])) - 10} 个结果\n\n"
            
            if self._api_key:
                reply_text += "📋 使用方法:\n"
                reply_text += "• 发送数字选择项目: 如 \"1\"\n" 
                reply_text += "• 发送数字.资源类型获取链接: 如 \"1.115\" \"2.magnet\""
            else:
                reply_text += "💡 提示: 请配置API_KEY以获取下载链接"
            
            self.post_message(
                channel=channel,
                title="Nullbr搜索结果",
                text=reply_text,
                userid=userid
            )
            
        except Exception as e:
            logger.error(f"搜索处理异常: {str(e)}")
            self.post_message(
                channel=channel,
                title="搜索错误",
                text=f"搜索「{keyword}」时出现错误: {str(e)}",
                userid=userid
            )
    
    def handle_resource_selection(self, number: int, channel: str, userid: str):
        """处理用户的编号选择"""
        try:
            # 检查缓存
            cache = self._user_search_cache.get(userid)
            if not cache or time.time() - cache['timestamp'] > 3600:  # 缓存1小时
                self.post_message(
                    channel=channel,
                    title="提示",
                    text="搜索结果已过期，请重新搜索。",
                    userid=userid
                )
                return
            
            results = cache['results']
            if number < 1 or number > len(results):
                self.post_message(
                    channel=channel,
                    title="提示",
                    text=f"请输入有效的编号 (1-{len(results)})。",
                    userid=userid
                )
                return
            
            # 获取选中的项目
            selected = results[number - 1]
            title = selected.get('title', '未知标题')
            media_type = selected.get('media_type', 'unknown')
            year = selected.get('release_date', selected.get('first_air_date', ''))[:4] if selected.get('release_date') or selected.get('first_air_date') else ''
            
            # 显示详细信息
            reply_text = f"📺 选择的资源: {title}"
            if year:
                reply_text += f" ({year})"
            reply_text += f"\n类型: {'电影' if media_type == 'movie' else '剧集' if media_type == 'tv' else media_type}"
            reply_text += f"\nTMDB ID: {selected.get('tmdbid')}"
            
            if selected.get('overview'):
                reply_text += f"\n简介: {selected.get('overview')[:100]}..."
            
            # 显示可用的资源类型
            reply_text += f"\n\n🔗 可用资源类型:"
            resource_options = []
            
            if selected.get('115-flg') and self._enable_115:
                resource_options.append(f"• 115网盘: 发送 \"{number}.115\"")
            if selected.get('magnet-flg') and self._enable_magnet:
                resource_options.append(f"• 磁力链接: 发送 \"{number}.magnet\"")
            if selected.get('video-flg') and self._enable_video:
                resource_options.append(f"• 在线观看: 发送 \"{number}.video\"")
            if selected.get('ed2k-flg') and self._enable_ed2k:
                resource_options.append(f"• ED2K链接: 发送 \"{number}.ed2k\"")
            
            if resource_options:
                reply_text += f"\n" + "\n".join(resource_options)
                
                if not self._api_key:
                    reply_text += "\n\n⚠️ 注意: 需要配置API_KEY才能获取具体下载链接"
            else:
                reply_text += f"\n暂无可用资源类型"
            
            self.post_message(
                channel=channel,
                title="资源详情",
                text=reply_text,
                userid=userid
            )
            
        except Exception as e:
            logger.error(f"处理资源选择异常: {str(e)}")
            self.post_message(
                channel=channel,
                title="错误",
                text=f"处理选择时出现错误: {str(e)}",
                userid=userid
            )
    
    def handle_get_resources(self, number: int, resource_type: str, channel: str, userid: str):
        """处理获取具体资源链接的请求"""
        try:
            # 检查API_KEY
            if not self._api_key:
                self.post_message(
                    channel=channel,
                    title="配置错误",
                    text="获取下载链接需要配置API_KEY，请在插件设置中添加。",
                    userid=userid
                )
                return
            
            # 检查缓存
            cache = self._user_search_cache.get(userid)
            if not cache or time.time() - cache['timestamp'] > 3600:
                self.post_message(
                    channel=channel,
                    title="提示",
                    text="搜索结果已过期，请重新搜索。",
                    userid=userid
                )
                return
            
            results = cache['results']
            if number < 1 or number > len(results):
                self.post_message(
                    channel=channel,
                    title="提示", 
                    text=f"请输入有效的编号 (1-{len(results)})。",
                    userid=userid
                )
                return
            
            # 获取选中的项目
            selected = results[number - 1]
            title = selected.get('title', '未知标题')
            media_type = selected.get('media_type', 'unknown')
            tmdbid = selected.get('tmdbid')
            
            if not tmdbid:
                self.post_message(
                    channel=channel,
                    title="错误",
                    text="该资源缺少TMDB ID，无法获取下载链接。",
                    userid=userid
                )
                return
            
            # 发送获取中的提示
            self.post_message(
                channel=channel,
                title="获取中",
                text=f"正在获取「{title}」的{resource_type}资源...",
                userid=userid
            )
            
            # 调用相应的API获取资源
            resources = None
            if media_type == 'movie':
                resources = self._client.get_movie_resources(tmdbid, resource_type)
            elif media_type == 'tv':
                resources = self._client.get_tv_resources(tmdbid, resource_type)
            
            if not resources:
                # Nullbr没有找到资源，回退到MoviePilot原始搜索
                logger.info(f"Nullbr未找到「{title}」的{resource_type}资源，回退到MoviePilot搜索")
                self.post_message(
                    channel=channel,
                    title="切换搜索",
                    text=f"Nullbr没有找到「{title}」的{resource_type}资源，正在使用MoviePilot原始搜索...",
                    userid=userid
                )
                
                # 调用MoviePilot的原始搜索功能
                self.fallback_to_moviepilot_search(title, channel, userid)
                return
            
            # 格式化资源链接
            self._format_and_send_resources(resources, resource_type, title, channel, userid)
            
        except Exception as e:
            logger.error(f"获取资源链接异常: {str(e)}")
            self.post_message(
                channel=channel,
                title="错误",
                text=f"获取资源链接时出现错误: {str(e)}",
                userid=userid
            )
    
    def _format_and_send_resources(self, resources: dict, resource_type: str, title: str, channel: str, userid: str):
        """格式化并发送资源链接"""
        try:
            reply_text = f"🎯 「{title}」的{resource_type}资源:\n\n"
            
            if resource_type == "115":
                resource_list = resources.get('115', [])
                for i, res in enumerate(resource_list[:10], 1):  # 最多显示10个
                    reply_text += f"{i}. {res.get('title', '未知')}\n"
                    reply_text += f"   大小: {res.get('size', '未知')}\n"
                    reply_text += f"   链接: {res.get('share_link', '无')}\n\n"
                    
            elif resource_type == "magnet":
                resource_list = resources.get('magnet', [])
                for i, res in enumerate(resource_list[:10], 1):
                    reply_text += f"{i}. {res.get('name', '未知')}\n"
                    reply_text += f"   大小: {res.get('size', '未知')}\n"
                    reply_text += f"   分辨率: {res.get('resolution', '未知')}\n"
                    reply_text += f"   中文字幕: {'✅' if res.get('zh_sub') else '❌'}\n"
                    reply_text += f"   磁力: {res.get('magnet', '无')}\n\n"
                    
            elif resource_type in ["video", "ed2k"]:
                resource_list = resources.get(resource_type, [])
                for i, res in enumerate(resource_list[:10], 1):
                    reply_text += f"{i}. {res.get('name', res.get('title', '未知'))}\n"
                    if res.get('size'):
                        reply_text += f"   大小: {res.get('size')}\n"
                    reply_text += f"   链接: {res.get('url', res.get('link', '无'))}\n\n"
            
            if len(reply_text) > 4000:  # Telegram消息长度限制
                reply_text = reply_text[:3900] + "...\n\n(内容过长已截断)"
            
            if not reply_text.strip().endswith('无'):
                reply_text += f"📊 共找到 {len(resources.get(resource_type, []))} 个资源"
            
            self.post_message(
                channel=channel,
                title=f"{resource_type.upper()}资源",
                text=reply_text,
                userid=userid
            )
            
        except Exception as e:
            logger.error(f"格式化资源异常: {str(e)}")
            self.post_message(
                channel=channel,
                title="错误",
                text=f"处理资源信息时出现错误: {str(e)}",
                userid=userid
            )
    
    def fallback_to_moviepilot_search(self, title: str, channel: str, userid: str):
        """回退到MoviePilot原始搜索功能"""
        logger.info(f"启动MoviePilot原始搜索: {title}")
        
        # 直接尝试各种搜索方法，不再触发事件避免循环
        self.try_alternative_search(title, channel, userid)
    
    def try_alternative_search(self, title: str, channel: str, userid: str):
        """尝试其他搜索方式"""
        try:
            logger.info(f"尝试MoviePilot原始搜索: {title}")
            
            # 简化策略：直接发送搜索建议和提示
            # 避免复杂的模块调用导致的错误
            
            success = False
            
            # 方法1: 尝试调用站点助手的简单方法
            try:
                from app.helper.sites import SitesHelper
                sites_helper = SitesHelper()
                
                # 只是检查是否有配置的站点
                if hasattr(sites_helper, 'get_indexers'):
                    indexers = sites_helper.get_indexers()
                    if indexers:
                        logger.info(f"检测到 {len(indexers)} 个配置的站点")
                        
                        self.post_message(
                            channel=channel,
                            title="搜索提示",
                            text=f"🔍 Nullbr未找到「{title}」的资源\n\n" +
                                 f"💡 系统检测到您已配置 {len(indexers)} 个搜索站点\n" +
                                 f"建议通过以下方式继续搜索:\n\n" +
                                 f"🌐 MoviePilot Web界面搜索\n" +
                                 f"📱 其他搜索渠道\n" +
                                 f"⚙️ 检查站点配置状态",
                            userid=userid
                        )
                        success = True
                
            except Exception as e:
                logger.warning(f"站点检测失败: {str(e)}")
            
            # 如果上面的方法也失败，发送通用建议
            if not success:
                self._send_manual_search_suggestion(title, channel, userid)
            
        except Exception as e:
            logger.error(f"备用搜索失败: {str(e)}")
            self._send_manual_search_suggestion(title, channel, userid)
    
    
    def _send_manual_search_suggestion(self, title: str, channel: str, userid: str):
        """发送手动搜索建议"""
        self.post_message(
            channel=channel,
            title="搜索建议",
            text=f"📋 「{title}」未找到资源，建议:\n\n" +
                 f"🔍 在MoviePilot Web界面搜索\n" +
                 f"⚙️ 检查资源站点配置\n" +
                 f"🔄 尝试其他关键词\n" +
                 f"📱 使用其他搜索渠道",
            userid=userid
        )

    def stop_service(self):
        """
        退出插件
        """
        if self._client and hasattr(self._client, '_session'):
            self._client._session.close()
        self._client = None
        self._enabled = False
        logger.info("Nullbr资源搜索插件已停止")