#!/usr/bin/env python3
"""
账号管理核心模块
负责账号的增删改查、QR登录、状态管理
"""
import asyncio
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
from telethon import TelegramClient
from telethon.sessions import StringSession
import qrcode
from io import BytesIO
import base64


API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
ACCOUNTS_DIR = "./accounts"
CONFIG_FILE = os.path.join(ACCOUNTS_DIR, "config.json")


class AccountManager:
    """账号管理器"""

    def __init__(self):
        self.accounts: Dict[str, Dict] = {}
        self.clients: Dict[str, TelegramClient] = {}
        self.qr_sessions: Dict[str, Dict] = {}  # QR登录会话
        self.phone_sessions: Dict[str, Dict] = {}  # 手机号登录会话
        self._load_config()
        self._ensure_dir()

    def _ensure_dir(self):
        """确保目录存在"""
        os.makedirs(ACCOUNTS_DIR, exist_ok=True)

    def _load_config(self):
        """加载账号配置"""
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                self.accounts = json.load(f)

    def _save_config(self):
        """保存账号配置"""
        self._ensure_dir()
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.accounts, f, ensure_ascii=False, indent=2)

    def list_accounts(self) -> List[Dict]:
        """
        获取账号列表

        Returns:
            账号列表，每个账号包含：
            - account_id: 账号ID
            - username: 用户名
            - user_id: 用户号
            - phone: 手机号
            - status: 状态 (online/offline)
            - last_online: 上次在线时间
            - use_count: 使用次数
        """
        result = []
        for account_id, account in self.accounts.items():
            # 检查实时状态
            is_online = self._check_account_status(account_id)

            result.append({
                "account_id": account_id,
                "username": account.get("username", "N/A"),
                "user_id": account.get("user_id", "N/A"),
                "phone": account.get("phone", "N/A"),
                "first_name": account.get("first_name", ""),
                "last_name": account.get("last_name", ""),
                "is_premium": account.get("is_premium", False),
                "status": "online" if is_online else "offline",
                "last_online": account.get("last_online", "N/A"),
                "use_count": account.get("use_count", 0)
            })
        return result

    def _check_account_status(self, account_id: str) -> bool:
        """
        检查账号状态

        Args:
            account_id: 账号ID

        Returns:
            True=在线, False=离线
        """
        if account_id in self.clients:
            client = self.clients[account_id]
            try:
                return client.is_connected()
            except:
                return False
        return False

    async def add_account_with_session(
        self,
        account_id: str,
        session_string: str,
        phone: str = None,
        username: str = None,
        user_id: int = None
    ) -> bool:
        """
        使用 Session 字符串添加账号

        Args:
            account_id: 账号ID
            session_string: Session 字符串
            phone: 手机号
            username: 用户名
            user_id: 用户号

        Returns:
            是否成功
        """
        try:
            # 验证 session
            client = TelegramClient(
                StringSession(session_string),
                API_ID,
                API_HASH
            )
            await client.connect()

            if not await client.is_user_authorized():
                await client.disconnect()
                return False

            # 获取账号信息
            me = await client.get_me()
            await client.disconnect()

            self.accounts[account_id] = {
                "account_id": account_id,
                "session_string": session_string,
                "phone": phone or me.phone,
                "username": username or me.username or "N/A",
                "user_id": user_id or me.id,
                "first_name": me.first_name or "",
                "last_name": me.last_name or "",
                "is_premium": getattr(me, 'premium', False) or False,
                "status": "offline",
                "created_at": datetime.now().isoformat(),
                "last_online": datetime.now().isoformat(),
                "use_count": 0
            }
            self._save_config()
            return True
        except Exception as e:
            print(f"添加账号失败: {e}")
            return False

    async def generate_qr_code(self, account_id: str, proxy: Dict = None) -> Dict:
        """
        生成二维码用于登录

        Args:
            account_id: 账号ID
            proxy: 代理配置

        Returns:
            {
                "success": True/False,
                "qr_code": "data:image/png;base64,...",
                "login_url": "tg://login?...",
                "expires_in": 120
            }
        """
        if account_id in self.accounts:
            return {"success": False, "error": "账号ID已存在"}

        try:
            # 创建临时客户端
            client_kwargs = {
                "session": StringSession(),
                "api_id": API_ID,
                "api_hash": API_HASH,
                "device_model": "Desktop",
                "system_version": "Windows 10",
                "app_version": "4.8.1",
                "lang_code": "en",
                "system_lang_code": "en-US"
            }
            
            # 添加代理配置
            if proxy:
                client_kwargs["proxy"] = proxy
            
            temp_client = TelegramClient(**client_kwargs)
            
            # 连接时增加重试和超时控制（优化速度：减少超时时间）
            connect_attempts = 3
            for attempt in range(connect_attempts):
                try:
                    await asyncio.wait_for(temp_client.connect(), timeout=15)  # 从30秒减少到15秒
                    break
                except asyncio.TimeoutError:
                    if attempt == connect_attempts - 1:
                        return {"success": False, "error": "连接超时，请检查网络或设置代理"}
                except Exception as e:
                    if attempt == connect_attempts - 1:
                        return {"success": False, "error": f"连接失败: {str(e)}"}
                    await asyncio.sleep(0.5)  # 从1秒减少到0.5秒

            # 生成二维码登录（优化速度：减少超时时间）
            try:
                qr_login = await asyncio.wait_for(temp_client.qr_login(), timeout=15)  # 从30秒减少到15秒
            except asyncio.TimeoutError:
                await temp_client.disconnect()
                return {"success": False, "error": "生成二维码超时，请检查网络或设置代理"}

            # 生成二维码图片
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(qr_login.url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            buffer = BytesIO()
            img.save(buffer, format='PNG')
            img_base64 = base64.b64encode(buffer.getvalue()).decode()

            # 保存QR会话
            self.qr_sessions[account_id] = {
                "client": temp_client,
                "qr_login": qr_login,
                "status": "waiting",
                "created_at": datetime.now(),
                "proxy": proxy
            }

            # 后台等待登录
            asyncio.create_task(self._wait_for_qr_login(account_id))

            return {
                "success": True,
                "account_id": account_id,
                "qr_code": f"data:image/png;base64,{img_base64}",
                "login_url": qr_login.url,
                "status": "waiting",
                "expires_in": 120
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _wait_for_qr_login(self, account_id: str):
        """
        后台等待二维码登录

        Args:
            account_id: 账号ID
        """
        if account_id not in self.qr_sessions:
            return

        session = self.qr_sessions[account_id]
        client = session["client"]
        qr_login = session["qr_login"]

        try:
            # 等待登录，最多120秒
            await asyncio.wait_for(qr_login.wait(), timeout=120)

            # 检查是否需要两步验证密码
            if await client.is_user_authorized():
                # 无需2FA，直接成功
                await self._complete_login(account_id, client, session)
            else:
                # 可能需要2FA密码
                session["status"] = "need_password"
                session["password_hint"] = ""
                # 不断开连接，等待用户输入密码
                
        except asyncio.TimeoutError:
            session["status"] = "timeout"
            await client.disconnect()
        except Exception as e:
            error_msg = str(e)
            if "password" in error_msg.lower() or "2fa" in error_msg.lower() or "two-step" in error_msg.lower():
                # 需要两步验证
                session["status"] = "need_password"
                session["password_hint"] = ""
            else:
                session["status"] = "failed"
                session["error"] = error_msg
                await client.disconnect()

    async def _complete_login(self, account_id: str, client, session: Dict):
        """完成登录，保存账号信息"""
        try:
            me = await client.get_me()
            session_string = client.session.save()

            # 保存账号
            self.accounts[account_id] = {
                "account_id": account_id,
                "session_string": session_string,
                "phone": me.phone,
                "username": me.username or "N/A",
                "user_id": me.id,
                "first_name": me.first_name or "",
                "last_name": me.last_name or "",
                "is_premium": getattr(me, 'premium', False) or False,
                "status": "online",
                "created_at": datetime.now().isoformat(),
                "last_online": datetime.now().isoformat(),
                "use_count": 0
            }
            self._save_config()
            session["status"] = "success"
        except Exception as e:
            session["status"] = "failed"
            session["error"] = str(e)
        finally:
            await client.disconnect()

    async def submit_2fa_password(self, account_id: str, password: str) -> Dict:
        """
        提交两步验证密码

        Args:
            account_id: 账号ID
            password: 两步验证密码

        Returns:
            {"success": True/False, "error": "..."}
        """
        if account_id not in self.qr_sessions:
            return {"success": False, "error": "会话不存在"}

        session = self.qr_sessions[account_id]
        if session["status"] != "need_password":
            return {"success": False, "error": "当前状态不需要密码"}

        client = session["client"]

        try:
            # 使用密码登录
            from telethon.errors import PasswordHashInvalidError
            await client.sign_in(password=password)

            if await client.is_user_authorized():
                await self._complete_login(account_id, client, session)
                return {"success": True}
            else:
                return {"success": False, "error": "验证失败"}

        except PasswordHashInvalidError:
            return {"success": False, "error": "密码错误"}
        except Exception as e:
            session["status"] = "failed"
            await client.disconnect()
            return {"success": False, "error": str(e)}

    def check_qr_status(self, account_id: str) -> Dict:
        """
        检查二维码登录状态

        Args:
            account_id: 账号ID

        Returns:
            {
                "status": "waiting"/"success"/"timeout"/"failed"/"need_password",
                "elapsed": 已过秒数,
                "remaining": 剩余秒数
            }
        """
        if account_id in self.accounts:
            return {"status": "success", "account_id": account_id}

        if account_id not in self.qr_sessions:
            return {"status": "not_found"}

        session = self.qr_sessions[account_id]
        elapsed = (datetime.now() - session["created_at"]).total_seconds()
        remaining = max(0, 120 - elapsed)

        result = {
            "status": session["status"],
            "elapsed": elapsed,
            "remaining": remaining
        }

        # 如果需要密码，返回提示
        if session["status"] == "need_password":
            result["password_hint"] = session.get("password_hint", "")

        return result

    async def refresh_qr_code(self, account_id: str, proxy: Dict = None) -> Dict:
        """
        刷新二维码

        Args:
            account_id: 账号ID
            proxy: 代理配置

        Returns:
            新的二维码信息
        """
        # 清除旧的QR会话
        if account_id in self.qr_sessions:
            old_session = self.qr_sessions[account_id]
            try:
                await old_session["client"].disconnect()
            except:
                pass
            del self.qr_sessions[account_id]

        return await self.generate_qr_code(account_id, proxy)

    async def remove_account(self, account_id: str) -> bool:
        """
        删除账号

        Args:
            account_id: 账号ID

        Returns:
            是否成功
        """
        if account_id == "default":
            return False

        if account_id not in self.accounts:
            return False

        # 断开客户端连接
        if account_id in self.clients:
            try:
                await self.clients[account_id].disconnect()
            except:
                pass
            del self.clients[account_id]

        # 删除账号
        del self.accounts[account_id]
        self._save_config()
        return True

    async def get_client(self, account_id: str = "default", proxy: Dict = None) -> Optional[TelegramClient]:
        """
        获取指定账号的客户端

        Args:
            account_id: 账号ID
            proxy: 代理配置（可选，如果为 None 则自动从 proxy_manager 获取）

        Returns:
            TelegramClient 实例
        """
        # 处理默认账号
        if account_id == "default":
            if "default" not in self.clients:
                # 默认账号使用文件session
                from main import get_client
                self.clients["default"] = await get_client()
            return self.clients["default"]

        # 检查账号是否存在
        if account_id not in self.accounts:
            return None

        # 如果已连接，直接返回
        if account_id in self.clients:
            client = self.clients[account_id]
            if client.is_connected():
                # 更新使用统计
                self._update_use_count(account_id)
                return client

        # 如果没有指定代理，自动从 proxy_manager 获取
        if proxy is None:
            from proxy_manager import proxy_manager
            proxy = proxy_manager.get_proxy_for_account(account_id)

        # 创建新连接
        account = self.accounts[account_id]
        session_string = account["session_string"]

        client = TelegramClient(
            StringSession(session_string),
            API_ID,
            API_HASH,
            proxy=proxy
        )
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return None

        self.clients[account_id] = client
        self._update_use_count(account_id)
        self._update_online_status(account_id)

        return client

    def _update_use_count(self, account_id: str):
        """更新使用次数"""
        if account_id in self.accounts:
            self.accounts[account_id]["use_count"] = self.accounts[account_id].get("use_count", 0) + 1
            self._save_config()

    def _update_online_status(self, account_id: str):
        """更新在线状态"""
        if account_id in self.accounts:
            self.accounts[account_id]["last_online"] = datetime.now().isoformat()
            self.accounts[account_id]["status"] = "online"
            self._save_config()

    def export_session(self, account_id: str) -> Optional[str]:
        """
        导出 Session 字符串

        Args:
            account_id: 账号ID

        Returns:
            Session 字符串
        """
        if account_id not in self.accounts:
            return None
        return self.accounts[account_id].get("session_string")

    # ==================== 手机号登录功能 ====================

    async def send_phone_code(
        self,
        account_id: str,
        phone: str,
        proxy: Dict = None
    ) -> Dict:
        """
        发送验证码到手机号（支持所有国际号码）

        Args:
            account_id: 账号ID
            phone: 手机号（支持国际格式，如 +8613800138000 或 +1234567890）
            proxy: 代理配置

        Returns:
            {
                "success": True/False,
                "phone_code_hash": "...",  # 用于后续验证
                "error": "..."
            }
        """
        if account_id in self.accounts:
            return {"success": False, "error": "账号ID已存在"}

        try:
            # 标准化手机号（确保以 + 开头）
            if not phone.startswith('+'):
                phone = '+' + phone

            # 创建临时客户端
            client_kwargs = {
                "session": StringSession(),
                "api_id": API_ID,
                "api_hash": API_HASH,
                "device_model": "Desktop",
                "system_version": "Windows 10",
                "app_version": "4.8.1",
                "lang_code": "en",
                "system_lang_code": "en-US"
            }

            if proxy:
                client_kwargs["proxy"] = proxy

            temp_client = TelegramClient(**client_kwargs)

            # 快速连接（缩短超时）
            await asyncio.wait_for(temp_client.connect(), timeout=15)

            # 发送验证码（新版 Telethon 需要 settings 参数）
            from telethon.tl.functions.auth import SendCodeRequest
            from telethon.tl.types import CodeSettings
            result = await temp_client(SendCodeRequest(
                phone,
                API_ID,
                API_HASH,
                settings=CodeSettings()
            ))

            # 获取 phone_code_hash
            phone_code_hash = result.phone_code_hash

            # 检查是否需要 2FA
            has_2fa = False
            password_hint = None
            if hasattr(result, 'next_type') and result.next_type is None:
                # 可能需要 2FA，需要进一步检查
                try:
                    from telethon.tl.types import AuthPasswordRecovery
                    has_2fa = isinstance(result.next_type, AuthPasswordRecovery) or result.next_type is None
                except:
                    pass

            # 保存会话
            self.phone_sessions[account_id] = {
                "client": temp_client,
                "phone": phone,
                "phone_code_hash": phone_code_hash,
                "status": "code_sent",
                "has_2fa": has_2fa,
                "password_hint": password_hint,
                "created_at": datetime.now(),
                "proxy": proxy
            }

            return {
                "success": True,
                "account_id": account_id,
                "phone": phone,
                "has_2fa": has_2fa,
                "password_hint": password_hint
            }

        except asyncio.TimeoutError:
            return {"success": False, "error": "连接超时，请检查网络或代理"}
        except Exception as e:
            error_msg = str(e)
            if "flood" in error_msg.lower():
                return {"success": False, "error": "请求过于频繁，请稍后再试"}
            if "invalid" in error_msg.lower() and "phone" in error_msg.lower():
                return {"success": False, "error": "手机号格式无效"}
            return {"success": False, "error": error_msg}

    async def verify_phone_code(
        self,
        account_id: str,
        code: str
    ) -> Dict:
        """
        验证手机验证码

        Args:
            account_id: 账号ID
            code: 验证码

        Returns:
            {
                "success": True/False,
                "needs_2fa": True/False,
                "error": "..."
            }
        """
        if account_id not in self.phone_sessions:
            return {"success": False, "error": "会话不存在或已过期"}

        session = self.phone_sessions[account_id]

        if session["status"] != "code_sent":
            return {"success": False, "error": "当前状态不允许验证码"}

        client = session["client"]
        phone = session["phone"]
        phone_code_hash = session["phone_code_hash"]

        try:
            from telethon.tl.functions.auth import SignInRequest

            # 尝试验证码登录
            result = await client(SignInRequest(phone, phone_code_hash, code))

            # 登录成功，保存账号
            await self._complete_phone_login(account_id, client, session)
            return {"success": True, "needs_2fa": False}

        except Exception as e:
            error_msg = str(e)
            error_msg_lower = error_msg.lower()

            # 检查是否需要 2FA
            if "password" in error_msg_lower or "2fa" in error_msg_lower or "two-step" in error_msg_lower:
                session["status"] = "need_2fa"
                return {"success": False, "needs_2fa": True, "error": "需要两步验证密码"}

            # 其他错误
            if "invalid" in error_msg_lower and "code" in error_msg_lower:
                return {"success": False, "needs_2fa": False, "error": "验证码错误"}
            if "code" in error_msg_lower and "expired" in error_msg_lower:
                return {"success": False, "needs_2fa": False, "error": "验证码已过期"}

            return {"success": False, "needs_2fa": False, "error": error_msg}

    async def submit_2fa_for_phone(
        self,
        account_id: str,
        password: str
    ) -> Dict:
        """
        提交手机号登录的 2FA 密码

        Args:
            account_id: 账号ID
            password: 两步验证密码

        Returns:
            {"success": True/False, "error": "..."}
        """
        if account_id not in self.phone_sessions:
            return {"success": False, "error": "会话不存在"}

        session = self.phone_sessions[account_id]
        if session["status"] != "need_2fa":
            return {"success": False, "error": "当前状态不需要密码"}

        client = session["client"]

        try:
            from telethon.tl.functions.auth import CheckPasswordRequest
            from telethon.tl.types import InputCheckPasswordSRP

            # 获取密码信息
            password_info = await client.get_password_hint()

            # 使用密码登录
            result = await client(CheckPasswordRequest(password=password))

            # 登录成功
            await self._complete_phone_login(account_id, client, session)
            return {"success": True}

        except Exception as e:
            error_msg = str(e)
            if "password" in error_msg.lower() and "invalid" in error_msg.lower():
                return {"success": False, "error": "密码错误"}
            return {"success": False, "error": error_msg}

    async def _complete_phone_login(self, account_id: str, client, session: Dict):
        """完成手机号登录，保存账号信息"""
        try:
            me = await client.get_me()
            session_string = client.session.save()

            self.accounts[account_id] = {
                "account_id": account_id,
                "session_string": session_string,
                "phone": me.phone,
                "username": me.username or "N/A",
                "user_id": me.id,
                "first_name": me.first_name or "",
                "last_name": me.last_name or "",
                "is_premium": getattr(me, 'premium', False) or False,
                "status": "online",
                "created_at": datetime.now().isoformat(),
                "last_online": datetime.now().isoformat(),
                "use_count": 0
            }
            self._save_config()
            session["status"] = "success"
        except Exception as e:
            session["status"] = "failed"
            session["error"] = str(e)
        finally:
            await client.disconnect()
            # 清理会话
            if account_id in self.phone_sessions:
                del self.phone_sessions[account_id]

    def get_phone_login_status(self, account_id: str) -> Dict:
        """获取手机号登录状态"""
        if account_id in self.accounts:
            return {"status": "success", "account_id": account_id}

        if account_id not in self.phone_sessions:
            return {"status": "not_found"}

        session = self.phone_sessions[account_id]
        return {
            "status": session["status"],
            "phone": session["phone"],
            "has_2fa": session.get("has_2fa", False),
            "elapsed": (datetime.now() - session["created_at"]).total_seconds()
        }

    async def cancel_phone_login(self, account_id: str) -> bool:
        """取消手机号登录"""
        if account_id in self.phone_sessions:
            session = self.phone_sessions[account_id]
            try:
                await session["client"].disconnect()
            except:
                pass
            del self.phone_sessions[account_id]
            return True
        return False

    # ==================== 批量导入 ====================

    async def get_friends(self, account_id: str) -> List[Dict]:
        """
        获取账号的好友/联系人列表

        Args:
            account_id: 账号ID

        Returns:
            好友列表 [{"id": ..., "name": ..., "username": ...}, ...]
        """
        if account_id not in self.accounts:
            raise ValueError(f"账号 {account_id} 不存在")

        account = self.accounts[account_id]
        session_string = account.get("session_string")

        if not session_string:
            raise ValueError("账号没有有效的Session")

        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        friends = []

        try:
            await client.connect()

            if not await client.is_user_authorized():
                raise ValueError("Session已失效")

            # 获取所有对话
            dialogs = await client.get_dialogs()

            for dialog in dialogs:
                entity = dialog.entity
                # 只获取用户（好友），排除群组和频道
                if hasattr(entity, 'first_name'):
                    name_parts = []
                    if entity.first_name:
                        name_parts.append(entity.first_name)
                    if hasattr(entity, 'last_name') and entity.last_name:
                        name_parts.append(entity.last_name)

                    friends.append({
                        "id": entity.id,
                        "name": " ".join(name_parts) if name_parts else str(entity.id),
                        "username": entity.username or None,
                        "phone": getattr(entity, 'phone', None)
                    })

        finally:
            await client.disconnect()

        return friends

    async def validate_usernames(self, account_id: str, usernames: List[str]) -> Dict:
        """
        验证用户名是否有效

        Args:
            account_id: 账号ID
            usernames: 用户名列表

        Returns:
            {"valid": [有效用户名], "invalid": [无效用户名]}
        """
        if account_id not in self.accounts:
            raise ValueError(f"账号 {account_id} 不存在")

        account = self.accounts[account_id]
        session_string = account.get("session_string")

        if not session_string:
            raise ValueError("账号没有有效的Session")

        client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
        valid = []
        invalid = []

        try:
            await client.connect()

            if not await client.is_user_authorized():
                raise ValueError("Session已失效")

            for username in usernames:
                try:
                    # 尝试获取用户实体
                    entity = await client.get_entity(username)
                    if entity:
                        valid.append({
                            "username": username,
                            "id": entity.id,
                            "name": getattr(entity, 'first_name', '') or username
                        })
                except Exception:
                    invalid.append(username)

                # 避免请求太频繁
                await asyncio.sleep(0.3)

        finally:
            await client.disconnect()

        return {
            "valid": valid,
            "invalid": invalid,
            "valid_count": len(valid),
            "invalid_count": len(invalid)
        }

    async def batch_import(self, accounts_data: List[Dict]) -> Dict:
        """
        批量导入账号

        Args:
            accounts_data: 账号数据列表
                [{"account_id": "...", "session_string": "..."}, ...]

        Returns:
            {"success": 成功数, "failed": 失败数, "details": 详细信息}
        """
        success_count = 0
        failed_count = 0
        details = []

        for data in accounts_data:
            account_id = data.get("account_id")
            session_string = data.get("session_string")

            if not account_id or not session_string:
                failed_count += 1
                details.append({"account_id": account_id, "status": "failed", "error": "缺少必要参数"})
                continue

            result = await self.add_account_with_session(
                account_id=account_id,
                session_string=session_string,
                phone=data.get("phone"),
                username=data.get("username"),
                user_id=data.get("user_id")
            )

            if result:
                success_count += 1
                details.append({"account_id": account_id, "status": "success"})
            else:
                failed_count += 1
                details.append({"account_id": account_id, "status": "failed", "error": "验证失败"})

        return {
            "success": success_count,
            "failed": failed_count,
            "total": len(accounts_data),
            "details": details
        }


# 全局实例
account_manager = AccountManager()
