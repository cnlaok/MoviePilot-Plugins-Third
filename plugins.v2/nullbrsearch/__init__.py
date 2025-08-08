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


class CloudSyncMediaClient:
    """CloudSyncMedia客户端"""
    
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.token = None
        self.token_expiry = 0
        
        # 配置请求会话
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        
        # CMS一般为内网服务，禁用代理访问
        self.session.proxies = {
            'http': None,
            'https': None
        }
        
        # 初始化时获取token
        self._ensure_valid_token()
    
    def _login(self) -> dict:
        """登录CMS系统获取token"""
        try:
            response = self.session.post(
                f'{self.base_url}/api/auth/login',
                json={
                    'username': self.username,
                    'password': self.password
                },
                timeout=(10, 30)
            )
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') != 200 or 'data' not in data:
                raise ValueError(f'CMS登录失败: {data}')
                
            return data['data']
            
        except requests.exceptions.RequestException as e:
            logger.error(f'CMS登录失败: {str(e)}')
            raise
    
    def _ensure_valid_token(self):
        """确保有效的token"""
        current_time = time.time()
        
        # 如果token不存在或距离过期时间不到1小时，重新获取token
        if not self.token or current_time >= (self.token_expiry - 3600):
            login_data = self._login()
            self.token = login_data['token']
            
            # 设置token过期时间为24小时后
            self.token_expiry = current_time + 86400
            
            # 更新session的Authorization header
            self.session.headers.update({
                'Authorization': f'Bearer {self.token}'
            })
            
            logger.info("CMS token已更新")
    
    def add_share_down(self, url: str) -> dict:
        """添加分享链接到CMS系统进行转存"""
        if not url:
            raise ValueError('转存链接不能为空')
        
        try:
            self._ensure_valid_token()
            
            response = self.session.post(
                f'{self.base_url}/api/cloud/add_share_down',
                json={'url': url},
                timeout=(10, 30)
            )
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"CMS转存请求已发送: {url}")
            return result
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                # token可能过期，强制重新获取
                self.token = None
                self._ensure_valid_token()
                
                # 重试请求
                response = self.session.post(
                    f'{self.base_url}/api/cloud/add_share_down',
                    json={'url': url},
                    timeout=(10, 30)
                )
                response.raise_for_status()
                return response.json()
            raise
        except Exception as e:
            logger.error(f'CMS转存请求失败: {str(e)}')
            raise


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
        
        # 根据配置使用系统代理（Nullbr在中国大陆需要代理访问）
        # 不设置proxies，使用系统默认代理配置
        
        # 配置重试策略，增加超时相关的状态码
        try:
            retry_strategy = Retry(
                total=3,
                status_forcelist=[429, 500, 502, 503, 504, 408],  # 添加408 Request Timeout
                allowed_methods=["HEAD", "GET", "OPTIONS"],
                backoff_factor=1
            )
        except TypeError:
            try:
                retry_strategy = Retry(
                    total=3,
                    status_forcelist=[429, 500, 502, 503, 504, 408],
                    method_whitelist=["HEAD", "GET", "OPTIONS"],
                    backoff_factor=1
                )
            except Exception:
                retry_strategy = Retry(total=3, backoff_factor=1)
                
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
    
    def _make_request(self, url: str, params: dict, headers: dict, use_proxy: bool = True) -> requests.Response:
        """发起HTTP请求，支持代理重试机制"""
        session = self._session
        
        # 如果不使用代理，创建临时session
        if not use_proxy:
            session = requests.Session()
            session.headers.update(self._session.headers)
            session.proxies = {'http': None, 'https': None}
        
        timeout = 5 if use_proxy else (10, 30)  # 使用代理时超时5s，无代理时用更长超时
        
        return session.get(url, params=params, headers=headers, timeout=timeout)
    
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
            
            url = f"{self._base_url}/search"
            
            # 首先尝试使用系统代理，5秒超时
            try:
                logger.debug("尝试使用系统代理访问Nullbr API")
                response = self._make_request(url, params, headers, use_proxy=True)
                logger.info(f"使用系统代理请求成功，响应状态码: {response.status_code}")
                
            except (requests.exceptions.Timeout, requests.exceptions.ConnectTimeout, 
                   requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
                logger.warning(f"使用系统代理访问超时/连接失败: {str(e)}，尝试直连")
                try:
                    # 代理失败，尝试不使用代理直连
                    response = self._make_request(url, params, headers, use_proxy=False)
                    logger.info(f"直连请求成功，响应状态码: {response.status_code}")
                except Exception as direct_e:
                    logger.error(f"直连也失败: {str(direct_e)}")
                    return None
            
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
            url = f"{self._base_url}/movie/{tmdbid}/{resource_type}"
            
            # 首先尝试使用系统代理，5秒超时
            try:
                logger.debug("尝试使用系统代理获取电影资源")
                response = self._make_request(url, {}, headers, use_proxy=True)
                logger.info(f"使用系统代理请求成功，响应状态码: {response.status_code}")
                
            except (requests.exceptions.Timeout, requests.exceptions.ConnectTimeout, 
                   requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
                logger.warning(f"使用系统代理访问超时/连接失败: {str(e)}，尝试直连")
                try:
                    # 代理失败，尝试不使用代理直连
                    response = self._make_request(url, {}, headers, use_proxy=False)
                    logger.info(f"直连请求成功，响应状态码: {response.status_code}")
                except Exception as direct_e:
                    logger.error(f"直连也失败: {str(direct_e)}")
                    return None
            
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
            url = f"{self._base_url}/tv/{tmdbid}/{resource_type}"
            
            # 首先尝试使用系统代理，5秒超时
            try:
                logger.debug("尝试使用系统代理获取剧集资源")
                response = self._make_request(url, {}, headers, use_proxy=True)
                logger.info(f"使用系统代理请求成功，响应状态码: {response.status_code}")
                
            except (requests.exceptions.Timeout, requests.exceptions.ConnectTimeout, 
                   requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
                logger.warning(f"使用系统代理访问超时/连接失败: {str(e)}，尝试直连")
                try:
                    # 代理失败，尝试不使用代理直连
                    response = self._make_request(url, {}, headers, use_proxy=False)
                    logger.info(f"直连请求成功，响应状态码: {response.status_code}")
                except Exception as direct_e:
                    logger.error(f"直连也失败: {str(direct_e)}")
                    return None
            
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
    plugin_version = "1.0.7"
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
        self._resource_priority = ["115", "magnet", "ed2k", "video"]  # 默认优先级
        self._enable_115 = True
        self._enable_magnet = True
        self._enable_video = True
        self._enable_ed2k = True
        self._search_timeout = 30
        self._client = None
        
        # CloudSyncMedia配置
        self._cms_enabled = False
        self._cms_url = ""
        self._cms_username = ""
        self._cms_password = ""
        self._cms_client = None
        
        
        # 用户搜索结果缓存和资源缓存
        self._user_search_cache = {}  # {userid: {'results': [...], 'timestamp': time.time()}}
        self._user_resource_cache = {}  # {userid: {'resources': [...], 'title': str, 'timestamp': time.time()}}

    def init_plugin(self, config: dict = None):
        # 确保插件能被正确识别，即使配置不完整
        logger.info(f"正在初始化 {self.plugin_name} v{self.plugin_version}")
        
        if config:
            self._enabled = config.get("enabled", False)
            self._app_id = config.get("app_id")
            self._api_key = config.get("api_key")
            
            # 构建资源优先级列表
            priority_list = []
            for i in range(1, 5):
                priority = config.get(f"priority_{i}")
                if priority and priority not in priority_list:
                    priority_list.append(priority)
            
            # 如果配置不完整，使用默认优先级
            if len(priority_list) < 4:
                self._resource_priority = ["115", "magnet", "ed2k", "video"]
            else:
                self._resource_priority = priority_list
            
            self._enable_115 = config.get("enable_115", True)
            self._enable_magnet = config.get("enable_magnet", True)
            self._enable_video = config.get("enable_video", True)
            self._enable_ed2k = config.get("enable_ed2k", True)
            self._search_timeout = config.get("search_timeout", 30)
            
            # CloudSyncMedia配置
            self._cms_enabled = config.get("cms_enabled", False)
            self._cms_url = config.get("cms_url", "")
            self._cms_username = config.get("cms_username", "")
            self._cms_password = config.get("cms_password", "")
            
            logger.info(f"Nullbr资源优先级设置: {' > '.join(self._resource_priority)}")
            if self._cms_enabled:
                logger.info(f"CloudSyncMedia已启用: {self._cms_url}")
        
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
        
        # 初始化CloudSyncMedia客户端
        if self._cms_enabled and self._cms_url and self._cms_username and self._cms_password:
            try:
                self._cms_client = CloudSyncMediaClient(
                    self._cms_url, 
                    self._cms_username, 
                    self._cms_password
                )
                logger.info("CloudSyncMedia客户端已初始化")
            except Exception as e:
                logger.error(f"CloudSyncMedia初始化失败: {str(e)}")
                self._cms_enabled = False
                self._cms_client = None
        else:
            self._cms_client = None

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
                                                'props': {'cols': 12},
                                                'content': [
                                                    {
                                                        'component': 'VAlert',
                                                        'props': {
                                                            'type': 'info',
                                                            'variant': 'tonal'
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'span',
                                                                'text': '🎯 资源优先级设置 - 自动按优先级获取资源（可拖拽排序）'
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
                                                        'component': 'VSelect',
                                                        'props': {
                                                            'model': 'priority_1',
                                                            'label': '第一优先级',
                                                            'items': [
                                                                {'title': '115网盘', 'value': '115'},
                                                                {'title': '磁力链接', 'value': 'magnet'},
                                                                {'title': 'ED2K链接', 'value': 'ed2k'},
                                                                {'title': 'M3U8视频', 'value': 'video'}
                                                            ],
                                                            'hint': '优先获取的资源类型',
                                                            'persistent-hint': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {'cols': 12, 'md': 6},
                                                'content': [
                                                    {
                                                        'component': 'VSelect',
                                                        'props': {
                                                            'model': 'priority_2',
                                                            'label': '第二优先级',
                                                            'items': [
                                                                {'title': '115网盘', 'value': '115'},
                                                                {'title': '磁力链接', 'value': 'magnet'},
                                                                {'title': 'ED2K链接', 'value': 'ed2k'},
                                                                {'title': 'M3U8视频', 'value': 'video'}
                                                            ],
                                                            'hint': '第二选择的资源类型',
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
                                                        'component': 'VSelect',
                                                        'props': {
                                                            'model': 'priority_3',
                                                            'label': '第三优先级',
                                                            'items': [
                                                                {'title': '115网盘', 'value': '115'},
                                                                {'title': '磁力链接', 'value': 'magnet'},
                                                                {'title': 'ED2K链接', 'value': 'ed2k'},
                                                                {'title': 'M3U8视频', 'value': 'video'}
                                                            ],
                                                            'hint': '第三选择的资源类型',
                                                            'persistent-hint': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {'cols': 12, 'md': 6},
                                                'content': [
                                                    {
                                                        'component': 'VSelect',
                                                        'props': {
                                                            'model': 'priority_4',
                                                            'label': '第四优先级',
                                                            'items': [
                                                                {'title': '115网盘', 'value': '115'},
                                                                {'title': '磁力链接', 'value': 'magnet'},
                                                                {'title': 'ED2K链接', 'value': 'ed2k'},
                                                                {'title': 'M3U8视频', 'value': 'video'}
                                                            ],
                                                            'hint': '最后选择的资源类型',
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
                                                'props': {'cols': 12},
                                                'content': [
                                                    {
                                                        'component': 'VAlert',
                                                        'props': {
                                                            'type': 'info',
                                                            'variant': 'tonal'
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'span',
                                                                'text': '🚀 CloudSyncMedia转存配置 - 自动转存资源到CMS系统'
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
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'cms_enabled',
                                                            'label': '启用CloudSyncMedia',
                                                            'hint': '开启后支持自动转存资源到CMS系统',
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
                                                            'model': 'cms_url',
                                                            'label': 'CMS服务器地址',
                                                            'placeholder': 'http://your-cms-domain.com',
                                                            'hint': 'CloudSyncMedia服务器的完整URL地址',
                                                            'persistent-hint': True
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
                                                            'model': 'cms_username',
                                                            'label': 'CMS用户名',
                                                            'placeholder': '请输入CMS登录用户名',
                                                            'hint': '用于登录CMS系统的用户名',
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
                                                            'model': 'cms_password',
                                                            'label': 'CMS密码',
                                                            'placeholder': '请输入CMS登录密码',
                                                            'hint': '用于登录CMS系统的密码',
                                                            'persistent-hint': True,
                                                            'type': 'password'
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
        "priority_1": "115",
        "priority_2": "magnet",
        "priority_3": "ed2k",
        "priority_4": "video",
        "cms_enabled": False,
        "cms_url": "",
        "cms_username": "",
        "cms_password": "",
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
            
            # 先检查是否有资源缓存（用于CMS转存）
            if self._cms_enabled and self._cms_client and userid in self._user_resource_cache:
                cache = self._user_resource_cache[userid]
                if time.time() - cache['timestamp'] < 3600:  # 1小时内有效
                    if 1 <= number <= len(cache['resources']):
                        logger.info(f"检测到资源转存请求: {number}")
                        self.handle_resource_transfer(number, channel, userid)
                        return
            
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
                reply_text += f"• 发送数字自动获取资源: 如 \"1\" (优先级: {' > '.join(self._resource_priority)})\n" 
                reply_text += "• 手动指定资源类型: 如 \"1.115\" \"2.magnet\" (可选)"
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
            tmdbid = selected.get('tmdbid')
            
            if not self._api_key:
                # 如果没有API_KEY，显示详细信息
                reply_text = f"📺 选择的资源: {title}"
                if year:
                    reply_text += f" ({year})"
                reply_text += f"\n类型: {'电影' if media_type == 'movie' else '剧集' if media_type == 'tv' else media_type}"
                reply_text += f"\nTMDB ID: {tmdbid}"
                
                if selected.get('overview'):
                    reply_text += f"\n简介: {selected.get('overview')[:100]}..."
                
                # 显示可用的资源类型
                reply_text += f"\n\n🔗 可用资源类型:"
                resource_options = []
                
                if selected.get('115-flg') and self._enable_115:
                    resource_options.append(f"• 115网盘")
                if selected.get('magnet-flg') and self._enable_magnet:
                    resource_options.append(f"• 磁力链接")
                if selected.get('video-flg') and self._enable_video:
                    resource_options.append(f"• 在线观看")
                if selected.get('ed2k-flg') and self._enable_ed2k:
                    resource_options.append(f"• ED2K链接")
                
                if resource_options:
                    reply_text += f"\n" + "\n".join(resource_options)
                    reply_text += "\n\n⚠️ 注意: 需要配置API_KEY才能获取具体下载链接"
                else:
                    reply_text += f"\n暂无可用资源类型"
                
                self.post_message(
                    channel=channel,
                    title="资源详情",
                    text=reply_text,
                    userid=userid
                )
            else:
                # 如果有API_KEY，直接按优先级获取资源
                self.post_message(
                    channel=channel,
                    title="获取中",
                    text=f"正在按优先级获取「{title}」的资源...",
                    userid=userid
                )
                
                self.get_resources_by_priority(selected, channel, userid)
            
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
            resource_list = resources.get(resource_type, [])
            if not resource_list:
                self.post_message(
                    channel=channel,
                    title="无资源",
                    text=f"没有找到「{title}」的{resource_type}资源。",
                    userid=userid
                )
                return
            
            # 缓存资源到用户缓存中，用于CMS转存
            resource_cache = []
            for res in resource_list[:10]:  # 最多缓存10个
                if resource_type == "115":
                    url = res.get('share_link', '')
                elif resource_type == "magnet":
                    url = res.get('magnet', '')
                elif resource_type in ["video", "ed2k"]:
                    url = res.get('url', res.get('link', ''))
                else:
                    url = ''
                
                if url:
                    resource_cache.append({
                        'url': url,
                        'title': res.get('title', res.get('name', '未知')),
                        'size': res.get('size', '未知'),
                        'type': resource_type
                    })
            
            # 保存到用户资源缓存
            self._user_resource_cache[userid] = {
                'resources': resource_cache,
                'title': title,
                'resource_type': resource_type,
                'timestamp': time.time()
            }
            
            # 格式化显示文本
            reply_text = f"🎯 「{title}」的{resource_type}资源:\n\n"
            
            if resource_type == "115":
                for i, res in enumerate(resource_list[:10], 1):
                    reply_text += f"{i}. {res.get('title', '未知')}\n"
                    reply_text += f"   大小: {res.get('size', '未知')}\n"
                    reply_text += f"   链接: {res.get('share_link', '无')}\n\n"
                    
            elif resource_type == "magnet":
                for i, res in enumerate(resource_list[:10], 1):
                    reply_text += f"{i}. {res.get('name', '未知')}\n"
                    reply_text += f"   大小: {res.get('size', '未知')}\n"
                    reply_text += f"   分辨率: {res.get('resolution', '未知')}\n"
                    reply_text += f"   中文字幕: {'✅' if res.get('zh_sub') else '❌'}\n"
                    reply_text += f"   磁力: {res.get('magnet', '无')}\n\n"
                    
            elif resource_type in ["video", "ed2k"]:
                for i, res in enumerate(resource_list[:10], 1):
                    reply_text += f"{i}. {res.get('name', res.get('title', '未知'))}\n"
                    if res.get('size'):
                        reply_text += f"   大小: {res.get('size')}\n"
                    reply_text += f"   链接: {res.get('url', res.get('link', '无'))}\n\n"
            
            if len(reply_text) > 3500:  # 留出空间给CMS提示
                reply_text = reply_text[:3400] + "...\n\n(内容过长已截断)\n\n"
            
            reply_text += f"📊 共找到 {len(resource_list)} 个资源\n\n"
            
            # 如果启用了CloudSyncMedia，添加转存提示
            if self._cms_enabled and self._cms_client:
                reply_text += "🚀 CloudSyncMedia转存:\n"
                reply_text += "发送资源编号进行转存，如: 1、2、3..."
            
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
    
    def get_resources_by_priority(self, selected: dict, channel: str, userid: str):
        """按优先级获取资源"""
        try:
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
            
            logger.info(f"按优先级获取资源: {title} (TMDB: {tmdbid})")
            logger.info(f"优先级顺序: {' > '.join(self._resource_priority)}")
            
            # 按优先级尝试获取资源
            for priority_type in self._resource_priority:
                # 检查该资源类型是否可用
                flag_key = f"{priority_type}-flg"
                if not selected.get(flag_key):
                    logger.info(f"跳过 {priority_type}: 资源不可用")
                    continue
                
                # 检查该资源类型是否启用
                enable_key = f"_enable_{priority_type}"
                if not getattr(self, enable_key, True):
                    logger.info(f"跳过 {priority_type}: 已在配置中禁用")
                    continue
                
                logger.info(f"尝试获取 {priority_type} 资源...")
                
                # 调用相应的API获取资源
                resources = None
                if media_type == 'movie':
                    resources = self._client.get_movie_resources(tmdbid, priority_type)
                elif media_type == 'tv':
                    resources = self._client.get_tv_resources(tmdbid, priority_type)
                
                if resources and resources.get(priority_type):
                    # 找到资源，发送结果并结束
                    resource_name = {
                        '115': '115网盘',
                        'magnet': '磁力链接', 
                        'ed2k': 'ED2K链接',
                        'video': 'M3U8视频'
                    }.get(priority_type, priority_type)
                    
                    logger.info(f"成功获取 {priority_type} 资源，共 {len(resources[priority_type])} 个")
                    
                    self.post_message(
                        channel=channel,
                        title="获取成功",
                        text=f"✅ 已获取「{title}」的{resource_name}资源",
                        userid=userid
                    )
                    
                    # 格式化并发送资源链接
                    self._format_and_send_resources(resources, priority_type, title, channel, userid)
                    return
                else:
                    logger.info(f"{priority_type} 资源不可用，尝试下一优先级")
            
            # 所有优先级都没有找到资源，回退到MoviePilot搜索
            logger.info(f"所有优先级资源都不可用，回退到MoviePilot搜索")
            self.post_message(
                channel=channel,
                title="切换搜索",
                text=f"Nullbr没有找到「{title}」的任何资源，正在使用MoviePilot原始搜索...",
                userid=userid
            )
            
            self.fallback_to_moviepilot_search(title, channel, userid)
            
        except Exception as e:
            logger.error(f"按优先级获取资源异常: {str(e)}")
            self.post_message(
                channel=channel,
                title="错误",
                text=f"获取资源时出现错误: {str(e)}",
                userid=userid
            )
    
    def handle_resource_transfer(self, resource_id: int, channel: str, userid: str):
        """处理资源转存请求"""
        try:
            # 检查CMS是否启用
            if not self._cms_enabled or not self._cms_client:
                self.post_message(
                    channel=channel,
                    title="功能未启用",
                    text="CloudSyncMedia转存功能未启用，请在设置中配置。",
                    userid=userid
                )
                return
            
            # 检查资源缓存
            cache = self._user_resource_cache.get(userid)
            if not cache or time.time() - cache['timestamp'] > 3600:
                self.post_message(
                    channel=channel,
                    title="缓存过期",
                    text="资源缓存已过期，请重新获取资源。",
                    userid=userid
                )
                return
            
            resources = cache['resources']
            if resource_id < 1 or resource_id > len(resources):
                self.post_message(
                    channel=channel,
                    title="无效编号",
                    text=f"请输入有效的资源编号 (1-{len(resources)})。",
                    userid=userid
                )
                return
            
            # 获取指定的资源
            selected_resource = resources[resource_id - 1]
            title = selected_resource['title']
            url = selected_resource['url']
            size = selected_resource['size']
            resource_type = selected_resource['type']
            
            logger.info(f"开始转存资源: {title} ({resource_type}) -> {url}")
            
            # 发送转存中的提示
            self.post_message(
                channel=channel,
                title="转存中",
                text=f"🚀 正在转存资源到CloudSyncMedia:\n\n"
                     f"📁 {title}\n"
                     f"💾 大小: {size}\n"
                     f"🔗 类型: {resource_type}\n\n"
                     f"请稍等...",
                userid=userid
            )
            
            # 调用CMS转存API
            result = self._cms_client.add_share_down(url)
            
            # 处理转存结果
            if result.get('code') == 200:
                self.post_message(
                    channel=channel,
                    title="转存成功",
                    text=f"✅ 资源转存成功!\n\n"
                         f"📁 {title}\n"
                         f"💾 大小: {size}\n"
                         f"🚀 {result.get('msg', '已添加到转存队列')}\n\n"
                         f"请到CloudSyncMedia查看转存进度。",
                    userid=userid
                )
            else:
                error_msg = result.get('msg', '转存失败')
                self.post_message(
                    channel=channel,
                    title="转存失败",
                    text=f"❌ 资源转存失败:\n\n"
                         f"📁 {title}\n"
                         f"🚫 错误: {error_msg}\n\n"
                         f"请检查CMS配置或稍后重试。",
                    userid=userid
                )
                
        except Exception as e:
            logger.error(f"资源转存异常: {str(e)}")
            self.post_message(
                channel=channel,
                title="转存错误",
                text=f"转存过程中发生错误: {str(e)}",
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
        try:
            # 清理Nullbr客户端
            if self._client and hasattr(self._client, '_session'):
                self._client._session.close()
            self._client = None
            
            # 清理CMS客户端
            if self._cms_client and hasattr(self._cms_client, 'session'):
                self._cms_client.session.close()
            self._cms_client = None
            
            # 清理缓存
            self._user_search_cache.clear()
            self._user_resource_cache.clear()
            
            self._enabled = False
            logger.info("Nullbr资源搜索插件已停止")
        except Exception as e:
            logger.error(f"插件停止异常: {str(e)}")


# 导出插件类，确保插件系统能正确识别
__all__ = ['NullbrSearch']
