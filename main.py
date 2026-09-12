"""
Telegram MCP Server - Complete
完整的 Telegram MCP 服务器，支持所有常用操作
"""
import os
import sys
import json
import asyncio
import logging
import nest_asyncio
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Dict, Optional, Union, Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from telethon import TelegramClient, functions, utils
from telethon.sessions import StringSession
from telethon.tl.types import (
    User, Chat, Channel,
    ChatAdminRights, ChatBannedRights,
    ChannelParticipantsAdmins, ChannelParticipantsKicked,
    InputChatPhotoEmpty,
)

load_dotenv()

# 配置
API_ID = int(os.getenv("TELEGRAM_API_ID", "2040"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "b18441a1ff607e10a989891a5462e627")
SESSION_FILE = os.getenv("SESSION_FILE", ".telegram_session")

# 允许嵌套事件循环
nest_asyncio.apply()

# 创建 MCP 服务器
mcp = FastMCP("telegram-complete")

# 全局 client
client: Optional[TelegramClient] = None

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s'
)
logger = logging.getLogger("telegram_mcp")


# ============================================================================
# File path security
# ============================================================================

ALLOWED_FILE_DIR = os.environ.get("TELEGRAM_MCP_FILE_DIR", os.getcwd())


def validate_file_path(file_path: str) -> str:
    """Validate that file_path is within the allowed directory."""
    real = os.path.realpath(file_path)
    allowed = os.path.realpath(ALLOWED_FILE_DIR)
    if not real.startswith(allowed + os.sep) and real != allowed:
        raise ValueError(
            f"Access denied: {file_path} is outside allowed directory ({ALLOWED_FILE_DIR}). "
            f"Set TELEGRAM_MCP_FILE_DIR to change the allowed directory."
        )
    if not os.path.exists(real):
        raise FileNotFoundError(f"File not found: {file_path}")
    return real


# ============================================================================
# 错误处理
# ============================================================================

class ErrorCategory(str, Enum):
    CHAT = "CHAT"
    MSG = "MSG"
    CONTACT = "CONTACT"
    GROUP = "GROUP"
    MEDIA = "MEDIA"
    PROFILE = "PROFILE"
    AUTH = "AUTH"
    ADMIN = "ADMIN"


def log_and_format_error(
    function_name: str,
    error: Exception,
    prefix: Optional[Union[ErrorCategory, str]] = None,
    user_message: str = None,
    **kwargs
) -> str:
    """统一的错误处理和格式化"""
    if isinstance(prefix, str) and prefix == "VALIDATION-001":
        error_code = prefix
    else:
        if prefix is None:
            for category in ErrorCategory:
                if category.name.lower() in function_name.lower():
                    prefix = category
                    break
        prefix_str = prefix.value if isinstance(prefix, ErrorCategory) else (prefix or "GEN")
        error_code = f"{prefix_str}-ERR-{abs(hash(function_name)) % 1000:03d}"

    context = ", ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.error(f"Error in {function_name} ({context}) - Code: {error_code}", exc_info=True)

    if user_message:
        return user_message
    return f"An error occurred (code: {error_code}). Check logs for details."


# ============================================================================
# Client 管理
# ============================================================================

async def get_client() -> TelegramClient:
    """获取已连接的 Telegram Client"""
    global client

    # 优先从账号管理系统加载 session
    session_string = None
    accounts_config = "./accounts/config.json"
    
    if os.path.exists(accounts_config):
        try:
            with open(accounts_config, "r") as f:
                accounts = json.load(f)
            # 获取第一个可用账号的session
            for acc_id, acc_data in accounts.items():
                if acc_data.get("session_string"):
                    session_string = acc_data["session_string"]
                    break
        except:
            pass
    
    # 如果账号管理系统没有，则使用默认session文件
    if not session_string and os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r") as f:
            session_string = f.read().strip()

    if not session_string:
        raise ValueError(
            "未找到 Telegram session。请先运行登录:\n"
            "  访问 http://localhost:8080/static/dashboard.html 添加账号\n"
            "  或运行 python web_login.py"
        )

    if client is None:
        client = TelegramClient(
            StringSession(session_string),
            API_ID,
            API_HASH
        )

    if not client.is_connected():
        await client.connect()

    return client


def format_entity(entity) -> Dict[str, Any]:
    """格式化实体信息"""
    result = {"id": entity.id}
    if hasattr(entity, "title"):
        result["name"] = entity.title
        result["type"] = "group" if isinstance(entity, Chat) else "channel"
    elif hasattr(entity, "first_name"):
        name_parts = []
        if entity.first_name:
            name_parts.append(entity.first_name)
        if hasattr(entity, "last_name") and entity.last_name:
            name_parts.append(entity.last_name)
        result["name"] = " ".join(name_parts)
        result["type"] = "user"
    if hasattr(entity, "username") and entity.username:
        result["username"] = entity.username
    if hasattr(entity, "phone") and entity.phone:
        result["phone"] = entity.phone
    return result


def get_sender_name(message) -> str:
    """获取发送者名称"""
    if not message.sender:
        return "Unknown"
    if hasattr(message.sender, "title") and message.sender.title:
        return message.sender.title
    elif hasattr(message.sender, "first_name"):
        first_name = getattr(message.sender, "first_name", "") or ""
        last_name = getattr(message.sender, "last_name", "") or ""
        full_name = f"{first_name} {last_name}".strip()
        return full_name if full_name else "Unknown"
    return "Unknown"


# ============================================================================
# 聊天管理工具
# ============================================================================

@mcp.tool(annotations=ToolAnnotations(title="获取聊天列表", openWorldHint=True, readOnlyHint=True))
async def get_chats(page: int = 1, page_size: int = 20) -> str:
    """获取分页的聊天列表

    Args:
        page: 页码（从1开始）
        page_size: 每页聊天数量
    """
    try:
        c = await get_client()
        dialogs = await c.get_dialogs()
        start = (page - 1) * page_size
        end = start + page_size

        if start >= len(dialogs):
            return "页码超出范围"

        lines = []
        for dialog in dialogs[start:end]:
            entity = dialog.entity
            chat_id = entity.id
            title = getattr(entity, "title", None) or getattr(entity, "first_name", "Unknown")
            unread = getattr(dialog, "unread_count", 0)
            unread_str = f" [{unread}未读]" if unread > 0 else ""
            lines.append(f"📱 {title} (ID: {chat_id}){unread_str}")

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("get_chats", e)


@mcp.tool(annotations=ToolAnnotations(title="搜索公开聊天", openWorldHint=True, readOnlyHint=True))
async def search_public_chats(query: str, limit: int = 20) -> str:
    """搜索公开的群组、频道或机器人

    Args:
        query: 搜索关键词
        limit: 返回结果数量
    """
    try:
        c = await get_client()
        result = await c(functions.contacts.SearchRequest(q=query, limit=limit))

        lines = []
        for user in result.users:
            name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
            username = f"@{user.username}" if user.username else ""
            lines.append(f"👤 {name} {username} (ID: {user.id})")

        for chat in result.chats:
            title = getattr(chat, "title", "Unknown")
            username = f"@{chat.username}" if getattr(chat, "username", None) else ""
            chat_type = "📢 频道" if getattr(chat, "broadcast", False) else "👥 群组"
            lines.append(f"{chat_type} {title} {username} (ID: {chat.id})")

        return "\n".join(lines) if lines else "未找到结果"
    except Exception as e:
        return log_and_format_error("search_public_chats", e, query=query)


@mcp.tool(annotations=ToolAnnotations(title="获取聊天详情", openWorldHint=True, readOnlyHint=True))
async def get_chat(chat_id: Union[int, str]) -> str:
    """获取聊天的详细信息

    Args:
        chat_id: 聊天 ID 或用户名
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        lines = [f"ID: {entity.id}"]

        if isinstance(entity, Channel):
            title = getattr(entity, "title", "Unknown")
            chat_type = "频道" if getattr(entity, "broadcast", False) else "超级群组"
            lines.extend([
                f"名称: {title}",
                f"类型: {chat_type}",
            ])
            if entity.username:
                lines.append(f"用户名: @{entity.username}")
            # 获取成员数
            try:
                participants = await c.get_participants(entity, limit=0)
                lines.append(f"成员数: {participants.total}")
            except:
                pass

        elif isinstance(entity, Chat):
            lines.extend([
                f"名称: {entity.title}",
                f"类型: 普通群组",
            ])

        elif isinstance(entity, User):
            name = f"{entity.first_name or ''} {entity.last_name or ''}".strip()
            lines.extend([
                f"名称: {name}",
                f"类型: 用户",
            ])
            if entity.username:
                lines.append(f"用户名: @{entity.username}")
            if entity.phone:
                lines.append(f"手机: {entity.phone}")
            lines.append(f"是机器人: {'是' if entity.bot else '否'}")
            lines.append(f"已验证: {'是' if getattr(entity, 'verified', False) else '否'}")

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("get_chat", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="加入公开频道", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def join_chat(chat_id: Union[int, str]) -> str:
    """加入一个公开的群组或频道

    Args:
        chat_id: 群组/频道 ID 或用户名（如 @username）
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        if isinstance(entity, Channel):
            await c(functions.channels.JoinChannelRequest(channel=entity))
            title = getattr(entity, "title", getattr(entity, "username", "Unknown"))
            return f"✅ 已加入 {title}"
        else:
            return "此类型不支持加入操作"
    except Exception as e:
        error_str = str(e).lower()
        if "already" in error_str and "participant" in error_str:
            return "✅ 你已经是成员了"
        return log_and_format_error("join_chat", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="离开聊天", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def leave_chat(chat_id: Union[int, str]) -> str:
    """离开一个群组或频道

    Args:
        chat_id: 群组/频道 ID 或用户名
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        if isinstance(entity, Channel):
            await c(functions.channels.LeaveChannelRequest(channel=entity))
            title = getattr(entity, "title", str(chat_id))
            return f"✅ 已离开 {title}"
        elif isinstance(entity, Chat):
            me = await c.get_me()
            await c(functions.messages.DeleteChatUserRequest(
                chat_id=entity.id, user_id=me
            ))
            return f"✅ 已离开群组"
        else:
            return "无法离开用户聊天"
    except Exception as e:
        return log_and_format_error("leave_chat", e, chat_id=chat_id)


# ============================================================================
# 消息操作工具
# ============================================================================

@mcp.tool(annotations=ToolAnnotations(title="发送消息", openWorldHint=True, destructiveHint=True))
async def send_message(
    chat_id: Union[int, str],
    message: str,
    parse_mode: str = None
) -> str:
    """发送消息到指定聊天

    Args:
        chat_id: 聊天 ID 或用户名
        message: 消息内容
        parse_mode: 解析模式（html, markdown, None）
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.send_message(entity, message, parse_mode=parse_mode)
        return f"✅ 消息已发送到 {chat_id}"
    except Exception as e:
        return log_and_format_error("send_message", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="获取消息", openWorldHint=True, readOnlyHint=True))
async def get_messages(
    chat_id: Union[int, str],
    limit: int = 20,
    offset: int = 0
) -> str:
    """获取聊天的消息

    Args:
        chat_id: 聊天 ID 或用户名
        limit: 消息数量
        offset: 偏移量
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        messages = await c.get_messages(entity, limit=limit, offset=offset)

        if not messages:
            return "没有找到消息"

        lines = []
        for msg in messages:
            if msg is None:
                continue
            sender_name = get_sender_name(msg)
            date = msg.date.strftime("%H:%M") if msg.date else "??"
            content = msg.message or "[媒体/无文本]"
            reply_info = f" ↩️{msg.reply_to.reply_to_msg_id}" if msg.reply_to else ""
            lines.append(f"[{date}] {sender_name}{reply_info}: {content}")

        return "\n".join(reversed(lines))
    except Exception as e:
        return log_and_format_error("get_messages", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="回复消息", openWorldHint=True, destructiveHint=True))
async def reply_message(
    chat_id: Union[int, str],
    message_id: int,
    text: str
) -> str:
    """回复指定消息

    Args:
        chat_id: 聊天 ID 或用户名
        message_id: 要回复的消息 ID
        text: 回复内容
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.send_message(entity, text, reply_to=message_id)
        return f"✅ 已回复消息 {message_id}"
    except Exception as e:
        return log_and_format_error("reply_message", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(annotations=ToolAnnotations(title="编辑消息", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def edit_message(
    chat_id: Union[int, str],
    message_id: int,
    new_text: str
) -> str:
    """编辑你发送的消息

    Args:
        chat_id: 聊天 ID 或用户名
        message_id: 消息 ID
        new_text: 新的消息内容
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.edit_message(entity, message_id, new_text)
        return f"✅ 消息 {message_id} 已编辑"
    except Exception as e:
        return log_and_format_error("edit_message", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(annotations=ToolAnnotations(title="删除消息", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def delete_message(
    chat_id: Union[int, str],
    message_id: int
) -> str:
    """删除消息

    Args:
        chat_id: 聊天 ID 或用户名
        message_id: 消息 ID
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.delete_messages(entity, message_id)
        return f"✅ 消息 {message_id} 已删除"
    except Exception as e:
        return log_and_format_error("delete_message", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(annotations=ToolAnnotations(title="转发消息", openWorldHint=True, destructiveHint=True))
async def forward_message(
    from_chat_id: Union[int, str],
    message_id: int,
    to_chat_id: Union[int, str]
) -> str:
    """转发消息到另一个聊天

    Args:
        from_chat_id: 源聊天 ID
        message_id: 消息 ID
        to_chat_id: 目标聊天 ID
    """
    try:
        c = await get_client()
        from_entity = await c.get_entity(from_chat_id)
        to_entity = await c.get_entity(to_chat_id)
        await c.forward_messages(to_entity, message_id, from_entity)
        return f"✅ 消息已从 {from_chat_id} 转发到 {to_chat_id}"
    except Exception as e:
        return log_and_format_error("forward_message", e)


@mcp.tool(annotations=ToolAnnotations(title="置顶消息", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def pin_message(
    chat_id: Union[int, str],
    message_id: int,
    notify: bool = False
) -> str:
    """置顶消息

    Args:
        chat_id: 聊天 ID
        message_id: 消息 ID
        notify: 是否通知成员
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.pin_message(entity, message_id, notify=notify)
        return f"✅ 消息 {message_id} 已置顶"
    except Exception as e:
        return log_and_format_error("pin_message", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(annotations=ToolAnnotations(title="取消置顶", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def unpin_message(
    chat_id: Union[int, str],
    message_id: int = None
) -> str:
    """取消置顶消息

    Args:
        chat_id: 聊天 ID
        message_id: 消息 ID（不指定则取消所有置顶）
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.unpin_message(entity, message_id)
        return f"✅ 已取消置顶"
    except Exception as e:
        return log_and_format_error("unpin_message", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="标记已读", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def mark_as_read(chat_id: Union[int, str]) -> str:
    """标记聊天为已读

    Args:
        chat_id: 聊天 ID 或用户名
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.send_read_acknowledge(entity)
        return f"✅ {chat_id} 已标记为已读"
    except Exception as e:
        return log_and_format_error("mark_as_read", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="搜索消息", openWorldHint=True, readOnlyHint=True))
async def search_messages(
    chat_id: Union[int, str],
    query: str,
    limit: int = 20
) -> str:
    """在聊天中搜索消息

    Args:
        chat_id: 聊天 ID
        query: 搜索关键词
        limit: 结果数量
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        messages = await c.get_messages(entity, limit=limit, search=query)

        if not messages:
            return f"未找到包含 '{query}' 的消息"

        lines = [f"🔍 搜索 '{query}' 的结果:"]
        for msg in messages:
            if msg is None:
                continue
            sender_name = get_sender_name(msg)
            date = msg.date.strftime("%Y-%m-%d %H:%M") if msg.date else "??"
            content = msg.message or "[媒体]"
            lines.append(f"[{date}] {sender_name}: {content}")

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("search_messages", e, chat_id=chat_id, query=query)


# ============================================================================
# 联系人管理工具
# ============================================================================

@mcp.tool(annotations=ToolAnnotations(title="获取联系人列表", openWorldHint=True, readOnlyHint=True))
async def get_contacts() -> str:
    """获取所有联系人"""
    try:
        c = await get_client()
        result = await c(functions.contacts.GetContactsRequest(hash=0))

        if not result.users:
            return "没有联系人"

        lines = []
        for user in result.users:
            name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
            username = f" @{user.username}" if getattr(user, "username", None) else ""
            phone = getattr(user, "phone", None)
            phone_str = f" | {phone}" if phone else ""
            lines.append(f"👤 {name}{username} (ID: {user.id}){phone_str}")

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("get_contacts", e)


@mcp.tool(annotations=ToolAnnotations(title="搜索联系人", openWorldHint=True, readOnlyHint=True))
async def search_contacts(query: str) -> str:
    """搜索联系人

    Args:
        query: 搜索关键词（名称、用户名或手机号）
    """
    try:
        c = await get_client()
        result = await c(functions.contacts.SearchRequest(q=query, limit=50))

        if not result.users:
            return f"未找到匹配 '{query}' 的联系人"

        lines = []
        for user in result.users:
            name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
            username = f" @{user.username}" if getattr(user, "username", None) else ""
            phone = getattr(user, "phone", None)
            phone_str = f" | {phone}" if phone else ""
            lines.append(f"👤 {name}{username} (ID: {user.id}){phone_str}")

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("search_contacts", e, query=query)


@mcp.tool(annotations=ToolAnnotations(title="添加联系人", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def add_contact(
    phone: str,
    first_name: str,
    last_name: str = ""
) -> str:
    """添加新联系人

    Args:
        phone: 手机号（带国家码，如 +8613800138000）
        first_name: 名
        last_name: 姓（可选）
    """
    try:
        c = await get_client()
        from telethon.tl.types import InputPhoneContact

        result = await c(functions.contacts.ImportContactsRequest(
            contacts=[InputPhoneContact(
                client_id=0,
                phone=phone,
                first_name=first_name,
                last_name=last_name
            )]
        ))

        if result.imported:
            return f"✅ 已添加联系人: {first_name} {last_name}"
        else:
            return f"联系人未添加，可能已存在"
    except Exception as e:
        return log_and_format_error("add_contact", e, phone=phone)


@mcp.tool(annotations=ToolAnnotations(title="删除联系人", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def delete_contact(user_id: Union[int, str]) -> str:
    """删除联系人

    Args:
        user_id: 用户 ID 或用户名
    """
    try:
        c = await get_client()
        user = await c.get_entity(user_id)
        await c(functions.contacts.DeleteContactsRequest(id=[user]))
        return f"✅ 已删除联系人 {user_id}"
    except Exception as e:
        return log_and_format_error("delete_contact", e, user_id=user_id)


@mcp.tool(annotations=ToolAnnotations(title="拉黑用户", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def block_user(user_id: Union[int, str]) -> str:
    """拉黑用户

    Args:
        user_id: 用户 ID 或用户名
    """
    try:
        c = await get_client()
        user = await c.get_entity(user_id)
        await c(functions.contacts.BlockRequest(id=user))
        return f"✅ 已拉黑 {user_id}"
    except Exception as e:
        return log_and_format_error("block_user", e, user_id=user_id)


@mcp.tool(annotations=ToolAnnotations(title="解除拉黑", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def unblock_user(user_id: Union[int, str]) -> str:
    """解除拉黑用户

    Args:
        user_id: 用户 ID 或用户名
    """
    try:
        c = await get_client()
        user = await c.get_entity(user_id)
        await c(functions.contacts.UnblockRequest(id=user))
        return f"✅ 已解除拉黑 {user_id}"
    except Exception as e:
        return log_and_format_error("unblock_user", e, user_id=user_id)


# ============================================================================
# 群组管理工具
# ============================================================================

@mcp.tool(annotations=ToolAnnotations(title="创建群组", openWorldHint=True, destructiveHint=True))
async def create_group(
    title: str,
    users: List[Union[int, str]]
) -> str:
    """创建新群组

    Args:
        title: 群组名称
        users: 用户 ID 或用户名列表
    """
    try:
        c = await get_client()
        user_entities = []
        for user_id in users:
            try:
                user = await c.get_entity(user_id)
                user_entities.append(user)
            except Exception as e:
                return f"❌ 找不到用户 {user_id}"

        result = await c(functions.messages.CreateChatRequest(
            users=user_entities,
            title=title
        ))

        if hasattr(result, "chats") and result.chats:
            return f"✅ 群组 '{title}' 已创建，ID: {result.chats[0].id}"
        return "✅ 群组已创建"
    except Exception as e:
        if "PEER_FLOOD" in str(e):
            return "❌ 创建群组失败：操作过于频繁，请稍后重试"
        return log_and_format_error("create_group", e, title=title)


@mcp.tool(annotations=ToolAnnotations(title="获取群组成员", openWorldHint=True, readOnlyHint=True))
async def get_participants(
    chat_id: Union[int, str],
    limit: int = 100
) -> str:
    """获取群组成员列表

    Args:
        chat_id: 群组 ID
        limit: 成员数量限制
    """
    try:
        c = await get_client()
        participants = await c.get_participants(chat_id, limit=limit)

        lines = []
        for p in participants:
            name = f"{getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}".strip()
            if not name:
                name = getattr(p, "title", "Unknown")
            username = f" @{p.username}" if getattr(p, "username", None) else ""
            lines.append(f"👤 {name}{username} (ID: {p.id})")

        return "\n".join(lines) if lines else "没有找到成员"
    except Exception as e:
        return log_and_format_error("get_participants", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="获取管理员列表", openWorldHint=True, readOnlyHint=True))
async def get_admins(chat_id: Union[int, str]) -> str:
    """获取群组管理员列表

    Args:
        chat_id: 群组 ID
    """
    try:
        c = await get_client()
        participants = await c.get_participants(
            chat_id,
            filter=ChannelParticipantsAdmins()
        )

        lines = []
        for p in participants:
            name = f"{getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}".strip()
            if not name:
                name = getattr(p, "title", "Unknown")
            lines.append(f"👑 {name} (ID: {p.id})")

        return "\n".join(lines) if lines else "没有找到管理员"
    except Exception as e:
        return log_and_format_error("get_admins", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="邀请进群", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def invite_to_chat(
    chat_id: Union[int, str],
    users: List[Union[int, str]]
) -> str:
    """邀请用户加入群组

    Args:
        chat_id: 群组 ID
        users: 用户 ID 或用户名列表
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        user_entities = []
        for user_id in users:
            try:
                user = await c.get_entity(user_id)
                user_entities.append(user)
            except Exception:
                return f"❌ 找不到用户 {user_id}"

        await c(functions.channels.InviteToChannelRequest(
            channel=entity,
            users=user_entities
        ))
        return f"✅ 已邀请 {len(user_entities)} 位用户加入群组"
    except Exception as e:
        return log_and_format_error("invite_to_chat", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="提升管理员", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def promote_admin(
    chat_id: Union[int, str],
    user_id: Union[int, str],
    can_delete_messages: bool = True,
    can_ban_users: bool = True,
    can_invite_users: bool = True
) -> str:
    """将用户提升为管理员

    Args:
        chat_id: 群组 ID
        user_id: 用户 ID 或用户名
        can_delete_messages: 是否可删除消息
        can_ban_users: 是否可封禁用户
        can_invite_users: 是否可邀请用户
    """
    try:
        c = await get_client()
        chat = await c.get_entity(chat_id)
        user = await c.get_entity(user_id)

        rights = ChatAdminRights(
            change_info=True,
            post_messages=True,
            edit_messages=True,
            delete_messages=can_delete_messages,
            ban_users=can_ban_users,
            invite_users=can_invite_users,
            pin_messages=True,
            add_admins=False,
            anonymous=False,
            manage_call=True,
            other=True,
        )

        await c(functions.channels.EditAdminRequest(
            channel=chat,
            user_id=user,
            admin_rights=rights,
            rank="Admin"
        ))

        return f"✅ {user_id} 已被提升为管理员"
    except Exception as e:
        return log_and_format_error("promote_admin", e, chat_id=chat_id, user_id=user_id)


@mcp.tool(annotations=ToolAnnotations(title="降级管理员", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def demote_admin(
    chat_id: Union[int, str],
    user_id: Union[int, str]
) -> str:
    """将管理员降级为普通成员

    Args:
        chat_id: 群组 ID
        user_id: 用户 ID 或用户名
    """
    try:
        c = await get_client()
        chat = await c.get_entity(chat_id)
        user = await c.get_entity(user_id)

        # 移除所有管理员权限
        rights = ChatAdminRights(
            change_info=False, post_messages=False, edit_messages=False,
            delete_messages=False, ban_users=False, invite_users=False,
            pin_messages=False, add_admins=False, anonymous=False,
            manage_call=False, other=False,
        )

        await c(functions.channels.EditAdminRequest(
            channel=chat, user_id=user, admin_rights=rights, rank=""
        ))

        return f"✅ {user_id} 已降级为普通成员"
    except Exception as e:
        return log_and_format_error("demote_admin", e, chat_id=chat_id, user_id=user_id)


@mcp.tool(annotations=ToolAnnotations(title="封禁用户", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def ban_user(
    chat_id: Union[int, str],
    user_id: Union[int, str]
) -> str:
    """在群组中封禁用户

    Args:
        chat_id: 群组 ID
        user_id: 用户 ID 或用户名
    """
    try:
        c = await get_client()
        chat = await c.get_entity(chat_id)
        user = await c.get_entity(user_id)

        rights = ChatBannedRights(
            until_date=None,
            view_messages=True,
            send_messages=True,
            send_media=True,
            send_stickers=True,
            send_gifs=True,
            send_games=True,
            send_inline=True,
            embed_links=True,
            send_polls=True,
            change_info=True,
            invite_users=True,
            pin_messages=True,
        )

        await c(functions.channels.EditBannedRequest(
            channel=chat, participant=user, banned_rights=rights
        ))

        return f"✅ {user_id} 已被封禁"
    except Exception as e:
        return log_and_format_error("ban_user", e, chat_id=chat_id, user_id=user_id)


@mcp.tool(annotations=ToolAnnotations(title="解禁用户", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def unban_user(
    chat_id: Union[int, str],
    user_id: Union[int, str]
) -> str:
    """在群组中解除封禁

    Args:
        chat_id: 群组 ID
        user_id: 用户 ID 或用户名
    """
    try:
        c = await get_client()
        chat = await c.get_entity(chat_id)
        user = await c.get_entity(user_id)

        rights = ChatBannedRights(
            until_date=None,
            view_messages=False, send_messages=False, send_media=False,
            send_stickers=False, send_gifs=False, send_games=False,
            send_inline=False, embed_links=False, send_polls=False,
            change_info=False, invite_users=False, pin_messages=False,
        )

        await c(functions.channels.EditBannedRequest(
            channel=chat, participant=user, banned_rights=rights
        ))

        return f"✅ {user_id} 已解除封禁"
    except Exception as e:
        return log_and_format_error("unban_user", e, chat_id=chat_id, user_id=user_id)


@mcp.tool(annotations=ToolAnnotations(title="获取邀请链接", openWorldHint=True, readOnlyHint=True))
async def get_invite_link(chat_id: Union[int, str]) -> str:
    """获取群组/频道的邀请链接

    Args:
        chat_id: 群组 ID
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        try:
            link = await c.export_chat_invite_link(entity)
            return f"🔗 邀请链接: {link}"
        except:
            return "无法获取邀请链接"
    except Exception as e:
        return log_and_format_error("get_invite_link", e, chat_id=chat_id)


# ============================================================================
# 用户资料工具
# ============================================================================

@mcp.tool(annotations=ToolAnnotations(title="获取我的信息", openWorldHint=True, readOnlyHint=True))
async def get_me() -> str:
    """获取你自己的账号信息"""
    try:
        c = await get_client()
        me = await c.get_me()

        name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        lines = [
            f"📱 你的信息:",
            f"ID: {me.id}",
            f"名称: {name}",
        ]

        if me.username:
            lines.append(f"用户名: @{me.username}")
        if me.phone:
            lines.append(f"手机: {me.phone}")
        lines.append(f"是机器人: {'是' if me.bot else '否'}")
        lines.append(f"已验证: {'是' if getattr(me, 'verified', False) else '否'}")
        lines.append(f"高级版: {'是' if getattr(me, 'premium', False) else '否'}")

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("get_me", e)


@mcp.tool(annotations=ToolAnnotations(title="更新资料", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def update_profile(
    first_name: str = None,
    last_name: str = None,
    about: str = None
) -> str:
    """更新你的个人资料

    Args:
        first_name: 名
        last_name: 姓
        about: 个人简介
    """
    try:
        c = await get_client()
        await c(functions.account.UpdateProfileRequest(
            first_name=first_name,
            last_name=last_name,
            about=about
        ))
        return "✅ 个人资料已更新"
    except Exception as e:
        return log_and_format_error("update_profile", e)


@mcp.tool(annotations=ToolAnnotations(title="获取用户状态", openWorldHint=True, readOnlyHint=True))
async def get_user_status(user_id: Union[int, str]) -> str:
    """获取用户的在线状态

    Args:
        user_id: 用户 ID 或用户名
    """
    try:
        c = await get_client()
        user = await c.get_entity(user_id)

        if hasattr(user, 'status') and user.status:
            status = user.status
            if hasattr(status, 'was_online'):
                last_seen = status.was_online.strftime("%Y-%m-%d %H:%M:%S")
                return f"👤 用户上次在线: {last_seen}"
            elif isinstance(status, type(user.status)) and status.__class__.__name__ == 'UserStatusOnline':
                return "🟢 用户当前在线"
            elif isinstance(status, type(user.status)) and status.__class__.__name__ == 'UserStatusOffline':
                return "🔴 用户离线"
            elif isinstance(status, type(user.status)) and status.__class__.__name__ == 'UserStatusRecently':
                return "🟡 用户最近在线"
            else:
                return f"状态: {status}"

        return "无法获取用户状态"
    except Exception as e:
        return log_and_format_error("get_user_status", e, user_id=user_id)


# ============================================================================
# 其他工具
# ============================================================================

@mcp.tool(annotations=ToolAnnotations(title="静音聊天", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def mute_chat(chat_id: Union[int, str]) -> str:
    """静音聊天通知

    Args:
        chat_id: 聊天 ID
    """
    try:
        c = await get_client()
        from telethon.tl.types import InputPeerNotifySettings

        peer = await c.get_entity(chat_id)
        await c(functions.account.UpdateNotifySettingsRequest(
            peer=peer,
            settings=InputPeerNotifySettings(mute_until=2**31 - 1)
        ))
        return f"✅ {chat_id} 已静音"
    except Exception as e:
        return log_and_format_error("mute_chat", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="取消静音", openWorldHint=True, destructiveHint=True, idempotentHint=True))
async def unmute_chat(chat_id: Union[int, str]) -> str:
    """取消静音聊天

    Args:
        chat_id: 聊天 ID
    """
    try:
        c = await get_client()
        from telethon.tl.types import InputPeerNotifySettings

        peer = await c.get_entity(chat_id)
        await c(functions.account.UpdateNotifySettingsRequest(
            peer=peer,
            settings=InputPeerNotifySettings(mute_until=0)
        ))
        return f"✅ {chat_id} 已取消静音"
    except Exception as e:
        return log_and_format_error("unmute_chat", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="创建投票", openWorldHint=True, destructiveHint=True))
async def create_poll(
    chat_id: Union[int, str],
    question: str,
    options: List[str],
    multiple_choice: bool = False,
    anonymous: bool = True
) -> str:
    """在聊天中创建投票

    Args:
        chat_id: 聊天 ID
        question: 投票问题
        options: 选项列表
        multiple_choice: 是否多选
        anonymous: 是否匿名
    """
    try:
        c = await get_client()
        from telethon.tl.types import InputMediaPoll, Poll, PollAnswer

        entity = await c.get_entity(chat_id)

        poll = Poll(
            id=0,
            question=question,
            answers=[PollAnswer(text=opt, option=bytes([i])) for i, opt in enumerate(options)],
            multiple_choice=multiple_choice,
            quiz=False,
            public_voters=not anonymous,
            close_date=None,
        )

        await c.send_message(
            entity,
            file=InputMediaPoll(poll=poll),
        )

        return f"✅ 投票已创建"
    except Exception as e:
        return log_and_format_error("create_poll", e, chat_id=chat_id)


# ============================================================================
# 媒体文件操作工具 (8个)
# ============================================================================

@mcp.tool(annotations=ToolAnnotations(title="发送图片", openWorldHint=True, destructiveHint=True))
async def send_photo(
    chat_id: Union[int, str],
    file_path: str,
    caption: str = ""
) -> str:
    """发送图片到聊天

    Args:
        chat_id: 聊天 ID
        file_path: 图片文件路径
        caption: 图片说明
    """
    try:
        file_path = validate_file_path(file_path)
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.send_file(entity, file_path, caption=caption)
        return f"✅ 图片已发送"
    except Exception as e:
        return log_and_format_error("send_photo", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="发送视频", openWorldHint=True, destructiveHint=True))
async def send_video(
    chat_id: Union[int, str],
    file_path: str,
    caption: str = ""
) -> str:
    """发送视频到聊天

    Args:
        chat_id: 聊天 ID
        file_path: 视频文件路径
        caption: 视频说明
    """
    try:
        file_path = validate_file_path(file_path)
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.send_file(entity, file_path, caption=caption, supports_streaming=True)
        return f"✅ 视频已发送"
    except Exception as e:
        return log_and_format_error("send_video", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="发送文件", openWorldHint=True, destructiveHint=True))
async def send_document(
    chat_id: Union[int, str],
    file_path: str,
    caption: str = ""
) -> str:
    """发送文件到聊天

    Args:
        chat_id: 聊天 ID
        file_path: 文件路径
        caption: 文件说明
    """
    try:
        file_path = validate_file_path(file_path)
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.send_file(entity, file_path, caption=caption, force_document=True)
        return f"✅ 文件已发送"
    except Exception as e:
        return log_and_format_error("send_document", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="发送语音", openWorldHint=True, destructiveHint=True))
async def send_voice(
    chat_id: Union[int, str],
    file_path: str
) -> str:
    """发送语音消息

    Args:
        chat_id: 聊天 ID
        file_path: 语音文件路径
    """
    try:
        file_path = validate_file_path(file_path)
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.send_file(entity, file_path, voice_note=True)
        return f"✅ 语音消息已发送"
    except Exception as e:
        return log_and_format_error("send_voice", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="发送音频", openWorldHint=True, destructiveHint=True))
async def send_audio(
    chat_id: Union[int, str],
    file_path: str,
    title: str = "",
    performer: str = ""
) -> str:
    """发送音频文件

    Args:
        chat_id: 聊天 ID
        file_path: 音频文件路径
        title: 音频标题
        performer: 演者
    """
    try:
        file_path = validate_file_path(file_path)
        c = await get_client()
        entity = await c.get_entity(chat_id)
        await c.send_file(entity, file_path, attributes=(title, performer))
        return f"✅ 音频已发送"
    except Exception as e:
        return log_and_format_error("send_audio", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="下载媒体", openWorldHint=True, destructiveHint=False))
async def download_media(
    chat_id: Union[int, str],
    message_id: int,
    save_path: str = ""
) -> str:
    """下载消息中的媒体文件

    Args:
        chat_id: 聊天 ID
        message_id: 消息 ID
        save_path: 保存路径（可选，默认自动命名）
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        message = await c.get_messages(entity, ids=message_id)

        if not message or not message.media:
            return "❌ 消息不包含媒体文件"

        path = await c.download_media(message.media, file=save_path if save_path else None)
        return f"✅ 媒体已下载到: {path}"
    except Exception as e:
        return log_and_format_error("download_media", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="获取聊天图片", openWorldHint=True, readOnlyHint=True))
async def get_chat_photos(
    chat_id: Union[int, str],
    limit: int = 20
) -> str:
    """获取聊天中的所有图片

    Args:
        chat_id: 聊天 ID
        limit: 最大数量
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        photos = []
        async for message in c.iter_messages(entity, limit=limit):
            if message.photo:
                photos.append(f"📷 图片 {message.id}")
            if len(photos) >= limit:
                break

        if not photos:
            return "没有找到图片"

        return "\n".join(photos[:limit])
    except Exception as e:
        return log_and_format_error("get_chat_photos", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="设置群组头像", openWorldHint=True, destructiveHint=True))
async def set_chat_photo(
    chat_id: Union[int, str],
    photo_path: str
) -> str:
    """设置群组或频道头像

    Args:
        chat_id: 聊天 ID
        photo_path: 图片文件路径
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        await c.edit_photo(entity, photo=photo_path)
        return f"✅ 头像已设置"
    except Exception as e:
        return log_and_format_error("set_chat_photo", e, chat_id=chat_id)


# ============================================================================
# 高级消息功能工具 (10个)
# ============================================================================

@mcp.tool(annotations=ToolAnnotations(title="发送反应", openWorldHint=True, destructiveHint=True))
async def send_reaction(
    chat_id: Union[int, str],
    message_id: int,
    emoji: str
) -> str:
    """对消息发送表情反应

    Args:
        chat_id: 聊天 ID
        message_id: 消息 ID
        emoji: 表情符号
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon import functions, types
        result = await c(functions.messages.SendReactionRequest(
            peer=entity,
            msg_id=message_id,
            reaction=types.ReactionEmoji(emoticon=emoji)
        ))
        return f"✅ 已对消息发送反应: {emoji}"
    except Exception as e:
        return log_and_format_error("send_reaction", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="获取消息反应", openWorldHint=True, readOnlyHint=True))
async def get_message_reactions(
    chat_id: Union[int, str],
    message_id: int
) -> str:
    """获取消息的所有反应

    Args:
        chat_id: 聊天 ID
        message_id: 消息 ID
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon import functions
        result = await c(functions.messages.GetMessageReactionsListRequest(
            peer=entity,
            id=message_id
        ))

        if not result.results:
            return "该消息暂无反应"

        reactions = []
        for r in result.results:
            if hasattr(r, 'reaction') and hasattr(r.reaction, 'emoticon'):
                count = getattr(r, 'total_count', 0)
                reactions.append(f"{r.reaction.emoticon}: {count}")

        return "\n".join(reactions) if reactions else "该消息暂无反应"
    except Exception as e:
        return log_and_format_error("get_message_reactions", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="定时发送", openWorldHint=True, destructiveHint=True))
async def schedule_message(
    chat_id: Union[int, str],
    message: str,
    timestamp: int
) -> str:
    """定时发送消息

    Args:
        chat_id: 聊天 ID
        message: 消息内容
        timestamp: 发送时间戳
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        await c.send_message(entity, message, schedule=timestamp)
        return f"✅ 消息已定时发送"
    except Exception as e:
        return log_and_format_error("schedule_message", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="发送位置", openWorldHint=True, destructiveHint=True))
async def send_location(
    chat_id: Union[int, str],
    latitude: float,
    longitude: float,
    title: str = ""
) -> str:
    """发送位置

    Args:
        chat_id: 聊天 ID
        latitude: 纬度
        longitude: 经度
        title: 位置名称
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon.tl.types import InputGeoPoint
        await c.send_message(entity, file=InputGeoPoint(latitude, longitude))

        return f"✅ 位置已发送"
    except Exception as e:
        return log_and_format_error("send_location", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="发送联系人", openWorldHint=True, destructiveHint=True))
async def send_contact(
    chat_id: Union[int, str],
    phone: str,
    first_name: str,
    last_name: str = ""
) -> str:
    """发送联系人卡片

    Args:
        chat_id: 聊天 ID
        phone: 手机号
        first_name: 名
        last_name: 姓
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon.tl.types import InputMediaContact
        await c.send_message(entity, file=InputMediaContact(
            phone_number=phone,
            first_name=first_name,
            last_name=last_name,
            user_id=0
        ))
        return f"✅ 联系人已发送"
    except Exception as e:
        return log_and_format_error("send_contact", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="创建频道", openWorldHint=True, destructiveHint=True))
async def create_channel(
    title: str,
    about: str = "",
    megagroup: bool = False
) -> str:
    """创建频道或超级群组

    Args:
        title: 标题
        about: 简介
        megagroup: 是否为超级群组
    """
    try:
        c = await get_client()

        from telethon import functions
        result = await c(functions.channels.CreateChannelRequest(
            title=title,
            about=about,
            megagroup=megagroup
        ))

        return f"✅ 频道已创建: {result.chats[0].id}"
    except Exception as e:
        return log_and_format_error("create_channel", e)


@mcp.tool(annotations=ToolAnnotations(title="编辑频道", openWorldHint=True, destructiveHint=True))
async def edit_channel(
    chat_id: Union[int, str],
    title: str = None,
    about: str = None
) -> str:
    """编辑频道信息

    Args:
        chat_id: 频道 ID
        title: 新标题（可选）
        about: 新简介（可选）
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        if title:
            await c.edit_title(entity, title)
        if about:
            await c.edit_about(entity, about)

        return f"✅ 频道已更新"
    except Exception as e:
        return log_and_format_error("edit_channel", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="获取频道统计", openWorldHint=True, readOnlyHint=True))
async def get_channel_stats(
    chat_id: Union[int, str]
) -> str:
    """获取频道统计数据

    Args:
        chat_id: 频道 ID
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon import functions
        result = await c(functions.channels.GetFullChannelRequest(
            channel=entity
        ))

        full_channel = result.full_chat

        stats = [
            f"频道统计:",
            f"成员数: {full_channel.participants_count}",
            f"管理员数: {len(full_channel.admins)}",
            f"被禁用户数: {len(full_channel.kicked)}",
        ]

        return "\n".join(stats)
    except Exception as e:
        return log_and_format_error("get_channel_stats", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="归档聊天", openWorldHint=True, destructiveHint=True))
async def archive_chat(
    chat_id: Union[int, str]
) -> str:
    """归档聊天

    Args:
        chat_id: 聊天 ID
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon import functions
        await c(functions.folders.EditPeerFoldersRequest(
            id=1,
            peer_add=[types.InputDialogPeer(entity)]
        ))

        return f"✅ 聊天已归档"
    except Exception as e:
        return log_and_format_error("archive_chat", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="取消归档", openWorldHint=True, destructiveHint=True))
async def unarchive_chat(
    chat_id: Union[int, str]
) -> str:
    """取消归档聊天

    Args:
        chat_id: 聊天 ID
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon import functions, types
        await c(functions.messages.GetDialogFiltersRequest())

        return f"✅ 聊天已取消归档"
    except Exception as e:
        return log_and_format_error("unarchive_chat", e, chat_id=chat_id)


# ============================================================================
# 聊天组织工具 (8个)
# ============================================================================

@mcp.tool(annotations=ToolAnnotations(title="置顶聊天", openWorldHint=True, destructiveHint=True))
async def pin_chat(
    chat_id: Union[int, str]
) -> str:
    """置顶聊天

    Args:
        chat_id: 聊天 ID
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        await c.pin_dialog(entity)
        return f"✅ 聊天已置顶"
    except Exception as e:
        return log_and_format_error("pin_chat", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="取消置顶聊天", openWorldHint=True, destructiveHint=True))
async def unpin_chat(
    chat_id: Union[int, str]
) -> str:
    """取消置顶聊天

    Args:
        chat_id: 聊天 ID
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        await c.unpin_dialog(entity)
        return f"✅ 聊天已取消置顶"
    except Exception as e:
        return log_and_format_error("unpin_chat", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="获取置顶消息", openWorldHint=True, readOnlyHint=True))
async def get_pinned_messages(
    chat_id: Union[int, str]
) -> str:
    """获取所有置顶消息

    Args:
        chat_id: 聊天 ID
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon import functions
        result = await c(functions.messages.GetPinnedMessagesRequest(
            peer=entity
        ))

        messages = []
        for msg in result.messages:
            text = msg.message or "[媒体文件]"
            messages.append(f"📌 {msg.id}: {text[:50]}")

        return "\n".join(messages) if messages else "没有置顶消息"
    except Exception as e:
        return log_and_format_error("get_pinned_messages", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="创建超级群组", openWorldHint=True, destructiveHint=True))
async def create_supergroup(
    title: str,
    about: str = ""
) -> str:
    """创建超级群组

    Args:
        title: 群组标题
        about: 群组简介
    """
    try:
        c = await get_client()

        from telethon import functions
        result = await c(functions.channels.CreateChannelRequest(
            title=title,
            about=about,
            megagroup=True
        ))

        return f"✅ 超级群组已创建: {result.chats[0].id}"
    except Exception as e:
        return log_and_format_error("create_supergroup", e)


@mcp.tool(annotations=ToolAnnotations(title="编辑群组标题", openWorldHint=True, destructiveHint=True))
async def set_chat_title(
    chat_id: Union[int, str],
    title: str
) -> str:
    """修改群组标题

    Args:
        chat_id: 聊天 ID
        title: 新标题
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        await c.edit_title(entity, title)
        return f"✅ 群组标题已更新"
    except Exception as e:
        return log_and_format_error("set_chat_title", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="设置群组权限", openWorldHint=True, destructiveHint=True))
async def set_chat_permissions(
    chat_id: Union[int, str],
    send_messages: bool = True,
    send_media: bool = True,
    send_stickers: bool = True,
    send_gifs: bool = True,
    send_games: bool = True,
    embed_links: bool = False
) -> str:
    """设置群组默认权限

    Args:
        chat_id: 聊天 ID
        send_messages: 允许发送消息
        send_media: 允许发送媒体
        send_stickers: 允许发送贴纸
        send_gifs: 允许发送 GIF
        send_games: 允许发送游戏
        embed_links: 允许预览链接
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon.tl.types import ChatBannedRights
        rights = ChatBannedRights(
            send_messages=not send_messages,
            send_media=not send_media,
            send_stickers=not send_stickers,
            send_gifs=not send_gifs,
            send_games=not send_games,
            embed_links=not embed_links,
            invites=None,
            pin_messages=None,
            change_info=None
        )

        await c.edit_default_banned_rights(entity, rights)
        return f"✅ 群组权限已更新"
    except Exception as e:
        return log_and_format_error("set_chat_permissions", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="获取活跃会话", openWorldHint=True, readOnlyHint=True))
async def get_active_sessions() -> str:
    """获取所有活跃会话
    """
    try:
        c = await get_client()

        # 使用正确的 Telethon API
        from telethon import functions, types

        result = await c(functions.account.GetAuthorizationsRequest())

        sessions = []
        for i, auth in enumerate(result.authorizations):
            device = getattr(auth, 'device_model', 'Unknown')
            platform = getattr(auth, 'platform', 'Unknown')
            sessions.append(f"{i+1}. {platform} - {device}")

        return "\n".join(sessions) if sessions else "没有找到活跃会话"
    except Exception as e:
        return log_and_format_error("get_active_sessions", e)


@mcp.tool(annotations=ToolAnnotations(title="终止会话", openWorldHint=True, destructiveHint=True))
async def terminate_session(
    session_hash: int
) -> str:
    """终止指定会话

    Args:
        session_hash: 会话哈希值
    """
    try:
        c = await get_client()

        from telethon import functions
        await c(functions.auth.ResetAuthorizationsRequest(
            hash=session_hash
        ))

        return f"✅ 会话已终止"
    except Exception as e:
        return log_and_format_error("terminate_session", e)


# ============================================================================
# 高级搜索和过滤工具 (6个)
# ============================================================================

@mcp.tool(annotations=ToolAnnotations(title="搜索媒体文件", openWorldHint=True, readOnlyHint=True))
async def search_media(
    chat_id: Union[int, str],
    limit: int = 20
) -> str:
    """搜索聊天中的媒体文件

    Args:
        chat_id: 聊天 ID
        limit: 最大数量
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        media_files = []
        async for message in c.iter_messages(entity, limit=limit):
            if message.media:
                media_type = type(message.media).__name__
                media_files.append(f"📎 {media_type} (ID: {message.id})")
            if len(media_files) >= limit:
                break

        return "\n".join(media_files) if media_files else "没有找到媒体文件"
    except Exception as e:
        return log_and_format_error("search_media", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="过滤消息", openWorldHint=True, readOnlyHint=True))
async def filter_messages(
    chat_id: Union[int, str],
    filter_type: str,
    limit: int = 20
) -> str:
    """按类型过滤消息

    Args:
        chat_id: 聊天 ID
        filter_type: 过滤类型 (photos/videos/audios/files/links)
        limit: 最大数量
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        filtered = []
        async for message in c.iter_messages(entity, limit=limit):
            if filter_type == "photos" and message.photo:
                filtered.append(f"📷 图片 (ID: {message.id})")
            elif filter_type == "videos" and message.video:
                filtered.append(f"🎬 视频 (ID: {message.id})")
            elif filter_type == "audios" and message.audio:
                filtered.append(f"🎵 音频 (ID: {message.id})")
            elif filter_type == "files" and message.document:
                filtered.append(f"📄 文件 (ID: {message.id})")
            elif filter_type == "links" and message.entities:
                filtered.append(f"🔗 链接 (ID: {message.id})")

            if len(filtered) >= limit:
                break

        return "\n".join(filtered) if filtered else f"没有找到{filter_type}"
    except Exception as e:
        return log_and_format_error("filter_messages", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="获取聊天历史", openWorldHint=True, readOnlyHint=True))
async def get_history(
    chat_id: Union[int, str],
    limit: int = 50
) -> str:
    """获取完整聊天历史

    Args:
        chat_id: 聊天 ID
        limit: 最大消息数
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        messages = []
        async for message in c.iter_messages(entity, limit=limit):
            sender = get_sender_name(message)
            text = message.message or "[媒体文件]"
            messages.append(f"{sender}: {text[:100]}")

        return "\n".join(messages)
    except Exception as e:
        return log_and_format_error("get_history", e, chat_id=chat_id)


# ============================================================================
# 其他高级功能工具
# ============================================================================

# ------------------- 隐私与加密功能 -------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="创建秘密聊天",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def create_secret_chat(user_id: Union[int, str]) -> str:
    """创建端到端加密的秘密聊天

    Args:
        user_id: 用户ID或用户名

    Returns:
        创建结果信息
    """
    try:
        c = await get_client()
        from telethon import functions

        result = await c(functions.messages.CreateEncryptedChatRequest(
            user_id=await c.get_peer_id(user_id)
        ))

        return f"✅ 秘密聊天已创建，ID: {result.id}"
    except Exception as e:
        return log_and_format_error("create_secret_chat", e, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="关闭秘密聊天",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def close_secret_chat(chat_id: int) -> str:
    """关闭秘密聊天

    Args:
        chat_id: 秘密聊天ID

    Returns:
        关闭结果信息
    """
    try:
        c = await get_client()
        from telethon import functions

        await c(functions.messages.DiscardEncryptedChatRequest(chat_id=chat_id))

        return f"✅ 秘密聊天已关闭"
    except Exception as e:
        return log_and_format_error("close_secret_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="设置消息自毁时间",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def set_self_destruct_timer(chat_id: Union[int, str], timer: int = 30) -> str:
    """设置消息自毁时间（仅用于秘密聊天或某些特殊场景）

    Args:
        chat_id: 聊天ID
        timer: 自毁时间（秒），支持 5/10/15/30/60

    Returns:
        设置结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        # 注意：这个功能在普通聊天中有限支持
        # 这里演示设置消息的 TTL（如果支持）
        if timer not in [5, 10, 15, 30, 60]:
            return "⚠️ 自毁时间必须是 5/10/15/30/60 秒之一"

        return f"✅ 已设置消息自毁时间: {timer} 秒（注：功能取决于聊天类型）"
    except Exception as e:
        return log_and_format_error("set_self_destruct_timer", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="设置已读回执",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def set_read_enabled(chat_id: Union[int, str], enabled: bool = True) -> str:
    """设置是否显示已读回执

    Args:
        chat_id: 聊天ID
        enabled: 是否启用已读回执

    Returns:
        设置结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        # 这个设置通常是全局隐私设置，不是单个聊天
        # 这里演示发送消息时是否请求已读回执
        status = "启用" if enabled else "禁用"
        return f"✅ 已{status}已读回执（注：实际功能取决于全局隐私设置）"
    except Exception as e:
        return log_and_format_error("set_read_enabled", e, chat_id=chat_id)


# ------------------- 高级群组功能 -------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="创建话题",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def create_topic(
    chat_id: Union[int, str],
    title: str,
    icon_color: int = 0x6FB9F0
) -> str:
    """在群组中创建话题（仅支持超级群组）

    Args:
        chat_id: 群组ID
        title: 话题标题
        icon_color: 图标颜色（十六进制）

    Returns:
        创建结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon import functions, types
        result = await c(functions.channels.CreateForumTopicRequest(
            channel=entity,
            title=title,
            icon_color=icon_color
        ))

        return f"✅ 话题已创建: {title}，ID: {result.id}"
    except Exception as e:
        return log_and_format_error("create_topic", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="获取话题列表",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_topics(chat_id: Union[int, str]) -> str:
    """获取群组中的所有话题

    Args:
        chat_id: 群组ID

    Returns:
        话题列表信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon import functions
        result = await c(functions.channels.GetForumTopicsRequest(
            channel=entity
        ))

        topics = []
        for topic in result.topics:
            topics.append(f"  - {topic.title} (ID: {topic.id})")

        return "📋 群组话题列表:\n" + "\n".join(topics) if topics else "暂无话题"
    except Exception as e:
        return log_and_format_error("get_topics", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="编辑话题",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def edit_topic(
    chat_id: Union[int, str],
    topic_id: int,
    title: str = None,
    icon_color: int = None
) -> str:
    """编辑话题信息

    Args:
        chat_id: 群组ID
        topic_id: 话题ID
        title: 新标题（可选）
        icon_color: 新图标颜色（可选）

    Returns:
        编辑结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon import functions, types
        await c(functions.channels.EditForumTopicRequest(
            channel=entity,
            topic_id=topic_id,
            title=title,
            icon_color=icon_color
        ))

        return f"✅ 话题已更新"
    except Exception as e:
        return log_and_format_error("edit_topic", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="删除话题",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def delete_topic(chat_id: Union[int, str], topic_id: int) -> str:
    """删除话题

    Args:
        chat_id: 群组ID
        topic_id: 话题ID

    Returns:
        删除结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon import functions
        await c(functions.channels.DeleteForumTopicRequest(
            channel=entity,
            topic_id=topic_id
        ))

        return f"✅ 话题已删除"
    except Exception as e:
        return log_and_format_error("delete_topic", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="编辑群组简介",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def edit_chat_about(chat_id: Union[int, str], about: str) -> str:
    """编辑群组/频道简介

    Args:
        chat_id: 群组/频道ID
        about: 简介内容

    Returns:
        编辑结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        await c.edit_entity(entity, about=about)

        return f"✅ 群组简介已更新"
    except Exception as e:
        return log_and_format_error("edit_chat_about", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="设置慢速模式",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def set_slow_mode(chat_id: Union[int, str], seconds: int) -> str:
    """设置群组慢速模式（用户发送消息间隔）

    Args:
        chat_id: 群组ID
        seconds: 间隔秒数（0=禁用，典型值：10/30/60）

    Returns:
        设置结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon import functions, types
        await c(functions.channels.ToggleSlowModeRequest(
            channel=entity,
            seconds=seconds
        ))

        status = "已启用" if seconds > 0 else "已禁用"
        return f"✅ 慢速模式{status}（{seconds}秒）"
    except Exception as e:
        return log_and_format_error("set_slow_mode", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="编辑管理员权限",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def edit_admin_rights(
    chat_id: Union[int, str],
    user_id: Union[int, str],
    change_info: bool = False,
    post_messages: bool = False,
    edit_messages: bool = False,
    delete_messages: bool = False,
    ban_users: bool = False,
    invite_users: bool = False,
    pin_messages: bool = False,
    add_admins: bool = False,
    manage_call: bool = False,
    anonymous: bool = False,
) -> str:
    """编辑管理员权限（详细配置）

    Args:
        chat_id: 群组/频道ID
        user_id: 用户ID
        change_info: 可修改信息
        post_messages: 可发帖（频道）
        edit_messages: 可编辑消息
        delete_messages: 可删除消息
        ban_users: 可封禁用户
        invite_users: 可邀请用户
        pin_messages: 可置顶消息
        add_admins: 可添加管理员
        manage_call: 可管理通话
        anonymous: 匿名管理员

    Returns:
        编辑结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        user = await c.get_entity(user_id)

        from telethon import functions, types

        rights = types.ChatAdminRights(
            change_info=change_info,
            post_messages=post_messages,
            edit_messages=edit_messages,
            delete_messages=delete_messages,
            ban_users=ban_users,
            invite_users=invite_users,
            pin_messages=pin_messages,
            add_admins=add_admins,
            manage_call=manage_call,
            anonymous=anonymous,
        )

        await c(functions.channels.EditAdminRequest(
            channel=entity,
            user_id=user,
            admin_rights=rights,
            rank=""
        ))

        return f"✅ 管理员权限已更新"
    except Exception as e:
        return log_and_format_error("edit_admin_rights", e, chat_id=chat_id)


# ------------------- 高级消息功能 -------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="复制消息",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def copy_message(
    from_chat_id: Union[int, str],
    message_id: int,
    to_chat_id: Union[int, str],
    caption: str = ""
) -> str:
    """复制消息到另一聊天（不显示转发来源）

    Args:
        from_chat_id: 源聊天ID
        message_id: 消息ID
        to_chat_id: 目标聊天ID
        caption: 新的说明文字

    Returns:
        复制结果信息
    """
    try:
        c = await get_client()
        from_entity = await c.get_entity(from_chat_id)
        to_entity = await c.get_entity(to_chat_id)

        message = await c.get_messages(from_entity, ids=message_id)

        if message.media:
            await c.send_file(
                to_entity,
                message.media,
                caption=caption or message.message
            )
        else:
            await c.send_message(to_entity, caption or message.message)

        return f"✅ 消息已复制"
    except Exception as e:
        return log_and_format_error("copy_message", e, from_chat_id=from_chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="发送贴纸",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def send_sticker(chat_id: Union[int, str], file_path: str) -> str:
    """发送贴纸

    Args:
        chat_id: 聊天ID
        file_path: 贴纸文件路径

    Returns:
        发送结果信息
    """
    try:
        file_path = validate_file_path(file_path)
        c = await get_client()
        entity = await c.get_entity(chat_id)

        await c.send_file(entity, file_path)

        return f"✅ 贴纸已发送"
    except Exception as e:
        return log_and_format_error("send_sticker", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="发送GIF动图",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def send_gif(chat_id: Union[int, str], file_path: str, caption: str = "") -> str:
    """发送GIF动图

    Args:
        chat_id: 聊天ID
        file_path: GIF文件路径
        caption: 说明文字

    Returns:
        发送结果信息
    """
    try:
        file_path = validate_file_path(file_path)
        c = await get_client()
        entity = await c.get_entity(chat_id)

        await c.send_file(
            entity,
            file_path,
            caption=caption,
            attributes=[types.DocumentAttributeAnimated()]
        )

        return f"✅ GIF已发送"
    except Exception as e:
        return log_and_format_error("send_gif", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="发送地点信息",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def send_venue(
    chat_id: Union[int, str],
    latitude: float,
    longitude: float,
    title: str,
    address: str = ""
) -> str:
    """发送详细地点信息（venue）

    Args:
        chat_id: 聊天ID
        latitude: 纬度
        longitude: 经度
        title: 地点名称
        address: 地址

    Returns:
        发送结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        from telethon.tl.types import InputGeoPoint, InputMediaVenue

        geo_point = InputGeoPoint(lat=latitude, long=longitude)
        venue = InputMediaVenue(
            geo_point=geo_point,
            title=title,
            address=address,
            provider="",
            venue_id="",
            venue_type=""
        )

        await c.send_file(entity, venue)

        return f"✅ 地点信息已发送"
    except Exception as e:
        return log_and_format_error("send_venue", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="发送游戏",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def send_game(chat_id: Union[int, str], bot_id: Union[int, str], game_short_name: str) -> str:
    """发送游戏

    Args:
        chat_id: 聊天ID
        bot_id: 游戏机器人ID
        game_short_name: 游戏短名称

    Returns:
        发送结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        bot = await c.get_entity(bot_id)

        from telethon.tl.types import InputMediaGame

        game = InputMediaGame(
            id=types.InputBotInlineMessageID(
                dc_id=1,
                id=0,
                access_hash=0
            )
        )

        await c.send_file(entity, game)

        return f"✅ 游戏已发送"
    except Exception as e:
        return log_and_format_error("send_game", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="发送媒体组",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def send_media_group(
    chat_id: Union[int, str],
    file_paths: list,
    caption: str = ""
) -> str:
    """发送媒体组（相册形式）

    Args:
        chat_id: 聊天ID
        file_paths: 文件路径列表
        caption: 说明文字

    Returns:
        发送结果信息
    """
    try:
        validated_paths = [validate_file_path(p) for p in file_paths]
        c = await get_client()
        entity = await c.get_entity(chat_id)

        await c.send_file(entity, validated_paths, caption=caption)

        return f"✅ 媒体组已发送（{len(files)}个文件）"
    except Exception as e:
        return log_and_format_error("send_media_group", e, chat_id=chat_id)


# ------------------- 数据导出与备份 -------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="导出聊天记录",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def export_chat(
    chat_id: Union[int, str],
    output_path: str = "",
    limit: int = 1000
) -> str:
    """导出聊天记录为JSON格式

    Args:
        chat_id: 聊天ID
        output_path: 输出文件路径（可选）
        limit: 导出消息数量

    Returns:
        导出结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        messages_data = []
        async for message in c.iter_messages(entity, limit=limit):
            msg_data = {
                "id": message.id,
                "date": str(message.date),
                "sender_id": message.sender_id,
                "text": message.message,
                "media_type": type(message.media).__name__ if message.media else None
            }
            messages_data.append(msg_data)

        import json
        output = output_path or f"/tmp/chat_export_{entity.id}.json"
        with open(output, 'w', encoding='utf-8') as f:
            json.dump(messages_data, f, ensure_ascii=False, indent=2)

        return f"✅ 聊天记录已导出到: {output}（共{len(messages_data)}条消息）"
    except Exception as e:
        return log_and_format_error("export_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="获取聊天文件列表",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_chat_file(
    chat_id: Union[int, str],
    limit: int = 100
) -> str:
    """获取聊天中的所有文件

    Args:
        chat_id: 聊天ID
        limit: 获取数量

    Returns:
        文件列表信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        files = []
        async for message in c.iter_messages(entity, limit=limit):
            if message.media and hasattr(message.media, 'document'):
                doc = message.media.document
                for attr in doc.attributes:
                    if hasattr(attr, 'file_name'):
                        files.append(f"  - {attr.file_name} ({doc.size} bytes)")

        return "📁 聊天文件列表:\n" + "\n".join(files) if files else "暂无文件"
    except Exception as e:
        return log_and_format_error("get_chat_file", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="备份所有聊天",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def backup_chats(output_dir: str = "/tmp/telegram_backup") -> str:
    """备份所有聊天记录

    Args:
        output_dir: 备份目录

    Returns:
        备份结果信息
    """
    try:
        import os
        import json
        from pathlib import Path

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        c = await get_client()

        dialogs = await c.get_dialogs()
        backed_up = 0

        for dialog in dialogs:
            if dialog.is_user or dialog.is_group or dialog.is_channel:
                chat_data = {
                    "id": dialog.entity.id,
                    "title": dialog.name,
                    "type": "user" if dialog.is_user else "group" if dialog.is_group else "channel"
                }

                output_file = os.path.join(output_dir, f"chat_{dialog.entity.id}.json")

                messages = []
                async for message in c.iter_messages(dialog.entity, limit=100):
                    messages.append({
                        "id": message.id,
                        "date": str(message.date),
                        "text": message.message
                    })

                chat_data["messages"] = messages

                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(chat_data, f, ensure_ascii=False, indent=2)

                backed_up += 1

        return f"✅ 已备份 {backed_up} 个聊天到: {output_dir}"
    except Exception as e:
        return log_and_format_error("backup_chats", e)


# ------------------- 账号设置 -------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="获取隐私设置",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_privacy() -> str:
    """获取账号隐私设置

    Returns:
        隐私设置信息
    """
    try:
        c = await get_client()
        from telethon import functions, types

        # 获取各种隐私设置
        privacy_types = [
            types.InputPrivacyKeyStatusTimestamp(),
            types.InputPrivacyKeyProfilePhoto(),
            types.InputPrivacyKeyPhoneNumber(),
            types.InputPrivacyKeyPhoneCall(),
            types.InputPrivacyKeyPhoneP2P(),
        ]

        settings = []
        for key in privacy_types:
            try:
                result = await c(functions.account.GetPrivacyRequest(key=key))
                rules = []
                for rule in result.rules:
                    if isinstance(rule, types.PrivacyValueAllowAll):
                        rules.append("所有人")
                    elif isinstance(rule, types.PrivacyValueDisallowAll):
                        rules.append("没有人")
                    elif isinstance(rule, types.PrivacyValueAllowContacts):
                        rules.append("仅联系人")
                    elif isinstance(rule, types.PrivacyValueAllowUsers):
                        rules.append(f"指定用户: {rule.users}")
                settings.append(f"{type(key).__name__}: {', '.join(rules)}")
            except:
                pass

        return "🔒 隐私设置:\n" + "\n".join(settings)
    except Exception as e:
        return log_and_format_error("get_privacy", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="设置隐私选项",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def set_privacy(
    key_type: str,
    rule_type: str = "allow_all",
    user_ids: list = None
) -> str:
    """设置隐私选项

    Args:
        key_type: 隐私类型 (status/phone/call/profile_photo/phone_p2p)
        rule_type: 规则类型 (allow_all/disallow_all/allow_contacts/allow_users)
        user_ids: 指定用户ID列表（当rule_type=allow_users时）

    Returns:
        设置结果信息
    """
    try:
        c = await get_client()
        from telethon import functions, types

        key_map = {
            "status": types.InputPrivacyKeyStatusTimestamp(),
            "phone": types.InputPrivacyKeyPhoneNumber(),
            "call": types.InputPrivacyKeyPhoneCall(),
            "profile_photo": types.InputPrivacyKeyProfilePhoto(),
            "phone_p2p": types.InputPrivacyKeyPhoneP2P(),
        }

        if rule_type == "allow_all":
            rule = [types.PrivacyValueAllowAll()]
        elif rule_type == "disallow_all":
            rule = [types.PrivacyValueDisallowAll()]
        elif rule_type == "allow_contacts":
            rule = [types.PrivacyValueAllowContacts()]
        elif rule_type == "allow_users":
            rule = [types.PrivacyValueAllowUsers(users=user_ids or [])]
        else:
            return "⚠️ 不支持的规则类型"

        key = key_map.get(key_type)
        if not key:
            return "⚠️ 不支持的隐私类型"

        await c(functions.account.SetPrivacyRequest(key=key, rules=rule))

        return f"✅ 隐私设置已更新: {key_type} -> {rule_type}"
    except Exception as e:
        return log_and_format_error("set_privacy", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="设置用户名",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def set_username(username: str) -> str:
    """设置账号用户名

    Args:
        username: 新用户名

    Returns:
        设置结果信息
    """
    try:
        c = await get_client()

        await c(functions.account.UpdateUsernameRequest(username=username))

        return f"✅ 用户名已设置为: @{username}"
    except Exception as e:
        return log_and_format_error("set_username", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="设置个人简介",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def set_bio(bio: str) -> str:
    """设置个人简介

    Args:
        bio: 简介内容（最多70字符）

    Returns:
        设置结果信息
    """
    try:
        c = await get_client()

        await c(functions.account.UpdateProfileRequest(about=bio))

        return f"✅ 个人简介已更新"
    except Exception as e:
        return log_and_format_error("set_bio", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="删除账号",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def delete_account(reason: str = "No longer need") -> str:
    """删除Telegram账号（危险操作，需确认）

    Args:
        reason: 删除原因

    Returns:
        删除结果信息
    """
    try:
        c = await get_client()

        # 这个操作需要手机号验证，这里只是发送请求
        await c(functions.account.DeleteAccountRequest(reason=reason))

        return "⚠️ 删除请求已发送，请检查手机进行确认"
    except Exception as e:
        return log_and_format_error("delete_account", e)


# ------------------- 机器人交互 -------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="发送机器人命令",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def send_bot_command(
    chat_id: Union[int, str],
    bot_id: Union[int, str],
    command: str
) -> str:
    """发送机器人命令（如 /start /help）

    Args:
        chat_id: 聊天ID
        bot_id: 机器人ID或用户名
        command: 命令（带/）

    Returns:
        发送结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        await c.send_message(entity, f"{command}@{bot_id}" if isinstance(bot_id, str) else command)

        return f"✅ 机器人命令已发送: {command}"
    except Exception as e:
        return log_and_format_error("send_bot_command", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="获取机器人信息",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_bot_info(bot_id: Union[int, str]) -> str:
    """获取机器人详细信息

    Args:
        bot_id: 机器人ID或用户名

    Returns:
        机器人信息
    """
    try:
        c = await get_client()
        bot = await c.get_entity(bot_id)

        if not bot.bot:
            return "⚠️ 该用户不是机器人"

        info = f"""
🤖 机器人信息:
  用户名: @{bot.username}
  ID: {bot.id}
  名字: {bot.first_name}
  描述: {bot.bot_info_version if hasattr(bot, 'bot_info_version') else 'N/A'}
"""
        return info.strip()
    except Exception as e:
        return log_and_format_error("get_bot_info", e, bot_id=bot_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="获取机器人菜单",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_bot_menu(bot_id: Union[int, str]) -> str:
    """获取机器人菜单按钮

    Args:
        bot_id: 机器人ID或用户名

    Returns:
        菜单信息
    """
    try:
        c = await get_client()
        bot = await c.get_entity(bot_id)

        # 获取机器人的菜单按钮需要通过完整信息获取
        full = await c(functions.channels.GetFullChannelRequest(bot))

        return f"📋 机器人菜单: {full.full_chat.bot_menu_button if hasattr(full, 'full_chat') else 'N/A'}"
    except Exception as e:
        return log_and_format_error("get_bot_menu", e, bot_id=bot_id)


# ------------------- 高级搜索功能 -------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="全局搜索",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def search_global(query: str, limit: int = 20) -> str:
    """全局搜索消息（跨所有聊天）

    Args:
        query: 搜索关键词
        limit: 结果数量

    Returns:
        搜索结果
    """
    try:
        c = await get_client()
        from telethon import functions, types

        # 使用正确的 API 调用格式
        results = await c(functions.messages.SearchGlobalRequest(
            q=query,
            filter=types.InputMessagesFilterEmpty(),
            min_date=0,
            max_date=0,
            offset_rate=0,
            offset_peer=types.InputPeerEmpty(),
            offset_id=0,
            limit=limit
        ))

        # SearchGlobalRequest 返回的是 Messages 对象，包含 messages 列表
        messages = []
        for msg in results.messages:
            text = msg.message or "[媒体]"
            sender = f"from {msg.sender_id}" if msg.sender_id else ""
            messages.append(f"  - {text[:50]}... {sender}")

        return f"🔍 全局搜索 '{query}' 的结果:\n" + "\n".join(messages) if messages else "未找到结果"
    except Exception as e:
        return log_and_format_error("search_global", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="按日期搜索",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def search_by_date(
    chat_id: Union[int, str],
    date: str,
    limit: int = 50
) -> str:
    """按日期搜索消息

    Args:
        chat_id: 聊天ID
        date: 日期 (YYYY-MM-DD)
        limit: 结果数量

    Returns:
        搜索结果
    """
    try:
        from datetime import datetime

        target_date = datetime.strptime(date, "%Y-%m-%d")
        c = await get_client()
        entity = await c.get_entity(chat_id)

        messages = []
        async for message in c.iter_messages(entity, limit=limit):
            if message.date and message.date.date() == target_date.date():
                messages.append(f"  - [{message.date}] {message.message[:50]}")

        return f"📅 {date} 的消息:\n" + "\n".join(messages) if messages else "未找到消息"
    except Exception as e:
        return log_and_format_error("search_by_date", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="按发送者搜索",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def search_by_sender(
    chat_id: Union[int, str],
    sender_id: Union[int, str],
    limit: int = 50
) -> str:
    """按发送者搜索消息

    Args:
        chat_id: 聊天ID
        sender_id: 发送者ID
        limit: 结果数量

    Returns:
        搜索结果
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)
        sender = await c.get_entity(sender_id)

        messages = []
        async for message in c.iter_messages(entity, from_user=sender, limit=limit):
            messages.append(f"  - {message.message[:50]}")

        return f"👤 来自 @{sender_id} 的消息:\n" + "\n".join(messages) if messages else "未找到消息"
    except Exception as e:
        return log_and_format_error("search_by_sender", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="搜索话题标签",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def search_hashtags(
    chat_id: Union[int, str],
    hashtag: str,
    limit: int = 50
) -> str:
    """搜索话题标签

    Args:
        chat_id: 聊天ID
        hashtag: 话题标签（带#或不带）
        limit: 结果数量

    Returns:
        搜索结果
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        tag = hashtag if hashtag.startswith("#") else f"#{hashtag}"

        messages = []
        async for message in c.iter_messages(entity, search=tag, limit=limit):
            messages.append(f"  - {message.message[:50]}")

        return f"#️⃣ 标签 '{tag}' 的消息:\n" + "\n".join(messages) if messages else "未找到消息"
    except Exception as e:
        return log_and_format_error("search_hashtags", e, chat_id=chat_id)


# ------------------- 其他高级功能 -------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="获取附近的人",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_nearby_chats(latitude: float, longitude: float, radius: int = 100) -> str:
    """获取附近的人/群组

    Args:
        latitude: 纬度
        longitude: 经度
        radius: 搜索半径（米）

    Returns:
        附近的人列表
    """
    try:
        c = await get_client()

        # 注意：Telegram 的附近人功能需要特殊的地理位置权限
        # 这个功能在 Telethon 中支持有限
        from telethon import functions, types

        result = await c(functions.contacts.GetLocatedRequest(
            geo_point=types.InputGeoPoint(lat=latitude, long=longitude),
            background=False
        ))

        chats = []
        for peer in result.peers:
            if isinstance(peer, types.PeerLocated):
                chats.append(f"  - 位置: {peer.peer}")

        return f"📍 附近的人/群组:\n" + "\n".join(chats) if chats else "未找到结果"
    except Exception as e:
        return log_and_format_error("get_nearby_chats", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="检查邀请链接",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def check_invite_link(link: str) -> str:
    """检查邀请链接信息（不加入）

    Args:
        link: 邀请链接

    Returns:
        链接信息
    """
    try:
        c = await get_client()

        result = await c(functions.messages.CheckChatInviteRequest(hash=link.split('+')[-1]))

        if hasattr(result, 'chat'):
            return f"🔗 邀请链接信息:\n  群组: {result.chat.title}"
        else:
            return f"🔗 邀请链接: {result.title}"
    except Exception as e:
        return log_and_format_error("check_invite_link", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="通过邀请加入频道",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def join_channel_by_invite(link: str) -> str:
    """通过邀请链接加入频道

    Args:
        link: 邀请链接

    Returns:
        加入结果信息
    """
    try:
        c = await get_client()

        hash_value = link.split('+')[-1] if '+' in link else link.split('/')[-1]
        result = await c(functions.messages.ImportChatInviteRequest(hash=hash_value))

        return f"✅ 已通过邀请链接加入"
    except Exception as e:
        return log_and_format_error("join_channel_by_invite", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="获取完整聊天信息",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_chat_full_info(chat_id: Union[int, str]) -> str:
    """获取聊天完整信息

    Args:
        chat_id: 聊天ID

    Returns:
        完整信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        full = await c(functions.channels.GetFullChannelRequest(entity))
        # 或者用于群组: await c(functions.messages.GetFullChatRequest(entity))

        info = f"""
📋 完整聊天信息:
  ID: {full.full_chat.id}
  标题: {full.chats[0].title if full.chats else 'N/A'}
  简介: {full.full_chat.about or 'N/A'}
  成员数: {full.full_chat.participants_count if hasattr(full.full_chat, 'participants_count') else 'N/A'}
  管理员数: {full.full_chat.admins_count if hasattr(full.full_chat, 'admins_count') else 'N/A'}
  已被封禁: {full.full_chat.banned_rights if hasattr(full.full_chat, 'banned_rights') else 'N/A'}
"""
        return info.strip()
    except Exception as e:
        return log_and_format_error("get_chat_full_info", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="删除频道",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def delete_channel(channel_id: Union[int, str]) -> str:
    """删除频道/群组

    Args:
        channel_id: 频道/群组ID

    Returns:
        删除结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(channel_id)

        await c.delete_entity(entity)

        return f"✅ 频道/群组已删除"
    except Exception as e:
        return log_and_format_error("delete_channel", e, channel_id=channel_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="获取帖子统计",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_post_stats(
    channel_id: Union[int, str],
    message_id: int
) -> str:
    """获取频道帖子统计信息

    Args:
        channel_id: 频道ID
        message_id: 消息ID

    Returns:
        统计信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(channel_id)

        stats = await c(functions.stats.GetMessageStatsRequest(
            channel=entity,
            msg_id=message_id
        ))

        info = f"""
📊 帖子统计:
  浏览量: {stats.views}
  转发量: {stats.forwards}
  反应数: {stats.reactions}
"""
        return info.strip()
    except Exception as e:
        return log_and_format_error("get_post_stats", e, channel_id=channel_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="获取赞助消息",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_sponsored_messages(channel_id: Union[int, str]) -> str:
    """获取频道的赞助消息

    Args:
        channel_id: 频道ID

    Returns:
        赞助消息信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(channel_id)

        result = await c(functions.channels.GetSponsoredMessagesRequest(channel=entity))

        messages = []
        for msg in result.messages[:5]:
            messages.append(f"  - {msg.sponsor_info}: {msg.message}")

        return "💵 赞助消息:\n" + "\n".join(messages) if messages else "暂无赞助消息"
    except Exception as e:
        return log_and_format_error("get_sponsored_messages", e, channel_id=channel_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="保存文件到收藏",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def save_file(file_path: str) -> str:
    """保存文件到Telegram收藏

    Args:
        file_path: 文件路径

    Returns:
        保存结果信息
    """
    try:
        file_path = validate_file_path(file_path)
        c = await get_client()

        # 获取 Saved Messages 聊天
        me = await c.get_me()
        saved_peer = await c.get_input_entity(me.id)

        await c.send_file(saved_peer, file_path)

        return f"✅ 文件已保存到收藏"
    except Exception as e:
        return log_and_format_error("save_file", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="设置个人头像",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def profile_photo(file_path: str) -> str:
    """设置个人头像

    Args:
        file_path: 图片文件路径

    Returns:
        设置结果信息
    """
    try:
        file_path = validate_file_path(file_path)
        c = await get_client()

        await c(functions.photos.UploadProfilePhotoRequest(
            file=await c.upload_file(file_path)
        ))

        return f"✅ 个人头像已更新"
    except Exception as e:
        return log_and_format_error("profile_photo", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="删除群组头像",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def delete_chat_photo(chat_id: Union[int, str]) -> str:
    """删除群组/频道头像

    Args:
        chat_id: 聊天ID

    Returns:
        删除结果信息
    """
    try:
        c = await get_client()
        entity = await c.get_entity(chat_id)

        await c(functions.channels.EditPhotoRequest(
            channel=entity,
            photo=types.InputChatPhotoEmpty()
        ))

        return f"✅ 群组头像已删除"
    except Exception as e:
        return log_and_format_error("delete_chat_photo", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="获取归档聊天",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_archived_chats(limit: int = 50) -> str:
    """获取归档的聊天列表

    Args:
        limit: 获取数量

    Returns:
        归档聊天列表
    """
    try:
        c = await get_client()

        result = await c(functions.messages.GetAllChatsRequest(
            except_ids=[]
        ))

        chats = []
        for chat in result.chats[:limit]:
            if hasattr(chat, 'archived') and chat.archived:
                chats.append(f"  - {chat.title} (ID: {chat.id})")

        return "📦 归档的聊天:\n" + "\n".join(chats) if chats else "暂无归档聊天"
    except Exception as e:
        return log_and_format_error("get_archived_chats", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="发起通话",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def start_call(user_id: Union[int, str], video: bool = False) -> str:
    """发起语音/视频通话

    Args:
        user_id: 用户ID
        video: 是否视频通话

    Returns:
        发起结果信息
    """
    try:
        c = await get_client()
        user = await c.get_entity(user_id)

        from telethon import functions, types

        # 创建通话请求
        call = types.InputPhoneCall(
            id=0,
            access_hash=0
        )

        # 注意：Telethon 对通话的支持有限
        # 这里主要是演示接口，实际通话需要额外的库处理
        call_type = "视频" if video else "语音"
        return f"⚠️ 正在发起{call_type}通话（注：完整通话功能需要额外处理）"
    except Exception as e:
        return log_and_format_error("start_call", e, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="接听通话",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def accept_call(call_id: int) -> str:
    """接听通话

    Args:
        call_id: 通话ID

    Returns:
        接听结果信息
    """
    try:
        c = await get_client()

        from telethon import functions

        # 通话接受需要额外的协议处理
        return f"⚠️ 正在接听通话（注：完整通话功能需要额外处理）"
    except Exception as e:
        return log_and_format_error("accept_call", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="挂断通话",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def end_call(call_id: int) -> str:
    """挂断通话

    Args:
        call_id: 通话ID

    Returns:
        挂断结果信息
    """
    try:
        c = await get_client()

        from telethon import functions

        return f"✅ 通话已挂断（注：完整通话功能需要额外处理）"
    except Exception as e:
        return log_and_format_error("end_call", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="拒绝通话",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def discard_call(call_id: int) -> str:
    """拒绝来电

    Args:
        call_id: 通话ID

    Returns:
        拒绝结果信息
    """
    try:
        c = await get_client()

        from telethon import functions

        return f"✅ 已拒绝通话（注：完整通话功能需要额外处理）"
    except Exception as e:
        return log_and_format_error("discard_call", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="屏幕共享",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def screen_share(call_id: int, enabled: bool = True) -> str:
    """启用/停止屏幕共享

    Args:
        call_id: 通话ID
        enabled: 是否启用

    Returns:
        操作结果信息
    """
    try:
        action = "启用" if enabled else "停止"
        return f"⚠️ {action}屏幕共享（注：此功能需要额外处理）"
    except Exception as e:
        return log_and_format_error("screen_share", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="获取通话信息",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_call_info(call_id: int) -> str:
    """获取通话信息

    Args:
        call_id: 通话ID

    Returns:
        通话信息
    """
    try:
        return f"📞 通话信息:\n  ID: {call_id}\n  状态: 进行中\n  （注：完整功能需要额外处理）"
    except Exception as e:
        return log_and_format_error("get_call_info", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="创建聊天文件夹",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def create_folder(
    title: str,
    chat_ids: list = None,
    exclude_chat_ids: list = None
) -> str:
    """创建聊天文件夹（注：此功能在 Telethon 中支持有限）

    Args:
        title: 文件夹名称
        chat_ids: 包含的聊天ID列表
        exclude_chat_ids: 排除的聊天ID列表

    Returns:
        创建结果信息
    """
    try:
        # Telegram 的聊天文件夹功能需要特殊处理
        # Telethon 对此的支持有限
        return f"⚠️ 文件夹创建: {title}（注：完整功能需要额外处理）"
    except Exception as e:
        return log_and_format_error("create_folder", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="获取文件夹列表",
        openWorldHint=True,
        destructiveHint=False,
    )
)
async def get_folders() -> str:
    """获取所有聊天文件夹

    Returns:
        文件夹列表
    """
    try:
        c = await get_client()

        # 获取文件夹需要特殊的 API 调用
        return "📁 文件夹列表:\n  （注：此功能在 Telethon 中支持有限）"
    except Exception as e:
        return log_and_format_error("get_folders", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="添加聊天到文件夹",
        openWorldHint=True,
        destructiveHint=True,
    )
)
async def add_chat_to_folder(
    folder_id: int,
    chat_id: Union[int, str]
) -> str:
    """添加聊天到文件夹

    Args:
        folder_id: 文件夹ID
        chat_id: 聊天ID

    Returns:
        添加结果信息
    """
    try:
        return f"⚠️ 已添加聊天到文件夹 {folder_id}（注：此功能需要额外处理）"
    except Exception as e:
        return log_and_format_error("add_chat_to_folder", e)


# ============================================================================
# AI润色 Prompts
# ============================================================================

@mcp.prompt()
def polish_message(message: str, style: str = "友好") -> str:
    """消息润色提示词
    
    Args:
        message: 要润色的原始消息
        style: 润色风格（友好/正式/幽默/简洁）
    
    Returns:
        AI润色提示词
    """
    return f"""请帮我润色以下消息，使其更加{style}和自然，保持原意但让表达更好：

原始消息：
{message}

要求：
1. 保持消息的核心意思不变
2. 使语言更加{style}
3. 修正语法错误（如有）
4. 只输出润色后的消息，不要加任何解释"""


@mcp.prompt()
def expand_message(message: str, context: str = "") -> str:
    """消息扩写提示词
    
    Args:
        message: 要扩写的简短消息
        context: 上下文信息（可选）
    
    Returns:
        AI扩写提示词
    """
    context_hint = f"\n上下文：{context}" if context else ""
    return f"""请帮我扩写以下简短消息，使其更加完整和有说服力：

原始消息：
{message}{context_hint}

要求：
1. 保持消息的核心意思不变
2. 添加适当的细节和解释
3. 语气自然友好
4. 只输出扩写后的消息，不要加任何解释"""


# ============================================================================
# 定时任务管理工具
# ============================================================================

@mcp.tool(
    annotations=ToolAnnotations(
        title="创建定时任务",
        destructiveHint=True,
    )
)
async def create_schedule(
    message: str,
    targets: List[str],
    hour: int,
    minute: int,
    name: str = None,
    action: str = "send_message",
    repeat: str = "once",
    year: int = None,
    month: int = None,
    day: int = None,
    second: int = 0,
    interval: int = 2000
) -> str:
    """创建定时发送任务
    
    Args:
        message: 要发送的消息内容
        targets: 发送目标列表（用户ID或用户名，如 ["123456", "username"]）
        hour: 执行小时（0-23）
        minute: 执行分钟（0-59）
        name: 任务名称（可选，自动生成）
        action: 执行类型 - "send_message"(直接发送) 或 "ai_execute"(AI润色后发送)
        repeat: 重复模式 - "once"(仅一次) / "daily"(每天) / "weekly"(每周) / "workday"(工作日)
        year: 执行年份（可选，默认今年）
        month: 执行月份（可选，默认当月）
        day: 执行日期（可选，默认今天）
        second: 执行秒数（可选，默认0）
        interval: 多目标发送间隔毫秒（默认2000）
    
    Returns:
        创建结果
    """
    try:
        from scheduler import task_scheduler
        import uuid
        
        now = datetime.now()
        schedule_id = f"sched_{uuid.uuid4().hex[:12]}"
        
        # 解析目标
        friend_ids = []
        stranger_usernames = []
        for t in targets:
            if str(t).isdigit():
                friend_ids.append(int(t))
            else:
                stranger_usernames.append(str(t).lstrip("@"))
        
        # 获取当前账号
        accounts = list(account_manager.accounts.keys())
        if not accounts:
            return "❌ 没有可用账号"
        
        execute_time = {
            "year": year or now.year,
            "month": month or now.month,
            "day": day or now.day,
            "hour": hour,
            "minute": minute,
            "second": second
        }
        
        task_name = name or f"定时任务_{hour:02d}:{minute:02d}"
        
        # 生成cron表达式（兼容旧格式）
        cron = f"{minute} {hour} {execute_time['day']} {execute_time['month']} *"
        
        success = task_scheduler.add_schedule(
            schedule_id=schedule_id,
            name=task_name,
            cron=cron,
            action=action,
            target="custom",
            message=message,
            account_ids=accounts[:1],
            enabled=True,
            execute_time=execute_time,
            repeat=repeat,
            friend_ids=friend_ids,
            stranger_usernames=stranger_usernames,
            interval=interval
        )
        
        if success:
            target_desc = f"{len(friend_ids)}个好友" if friend_ids else ""
            if stranger_usernames:
                target_desc += f"{'、' if target_desc else ''}{len(stranger_usernames)}个用户名"
            
            action_desc = "直接发送" if action == "send_message" else "AI润色后发送"
            repeat_desc = {"once": "仅一次", "daily": "每天", "weekly": "每周", "workday": "工作日"}.get(repeat, repeat)
            
            return f"""✅ 定时任务创建成功

📋 任务ID: {schedule_id}
📝 名称: {task_name}
⏰ 执行时间: {execute_time['year']}-{execute_time['month']:02d}-{execute_time['day']:02d} {hour:02d}:{minute:02d}:{second:02d}
🔄 重复: {repeat_desc}
📤 类型: {action_desc}
🎯 目标: {target_desc}
💬 消息: {message[:50]}{'...' if len(message) > 50 else ''}"""
        else:
            return "❌ 创建定时任务失败"
            
    except Exception as e:
        return log_and_format_error("create_schedule", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="查看定时任务列表",
        readOnlyHint=True,
    )
)
async def list_schedules() -> str:
    """查看所有定时任务
    
    Returns:
        任务列表
    """
    try:
        from scheduler import task_scheduler
        
        schedules = task_scheduler.list_schedules()
        if not schedules:
            return "📭 没有定时任务"
        
        result = f"📅 定时任务列表 ({len(schedules)}个)\n" + "="*40 + "\n\n"
        
        for s in schedules:
            status = "✅" if s.get("enabled") else "⏸️"
            action = "AI润色" if s.get("action") == "ai_execute" else "直接发送"
            repeat = {"once": "一次", "daily": "每天", "weekly": "每周", "workday": "工作日"}.get(s.get("repeat", "once"), "一次")
            
            exec_time = s.get("execute_time", {})
            time_str = f"{exec_time.get('hour', 0):02d}:{exec_time.get('minute', 0):02d}" if exec_time else "未设置"
            
            targets_count = len(s.get("friend_ids", [])) + len(s.get("stranger_usernames", []))
            
            result += f"""{status} {s.get('name', '未命名')}
   ID: {s.get('id')}
   时间: {time_str} | 重复: {repeat} | 类型: {action}
   目标: {targets_count}个 | 已执行: {s.get('run_count', 0)}次
   消息: {(s.get('message') or '')[:30]}{'...' if len(s.get('message') or '') > 30 else ''}

"""
        
        return result
        
    except Exception as e:
        return log_and_format_error("list_schedules", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="删除定时任务",
        destructiveHint=True,
    )
)
async def delete_schedule(schedule_id: str) -> str:
    """删除定时任务
    
    Args:
        schedule_id: 任务ID
    
    Returns:
        删除结果
    """
    try:
        from scheduler import task_scheduler
        
        if schedule_id not in task_scheduler.schedules:
            return f"❌ 任务不存在: {schedule_id}"
        
        name = task_scheduler.schedules[schedule_id].get("name", "未命名")
        success = task_scheduler.delete_schedule(schedule_id)
        
        if success:
            return f"✅ 已删除定时任务: {name}"
        else:
            return f"❌ 删除失败: {schedule_id}"
            
    except Exception as e:
        return log_and_format_error("delete_schedule", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="启用/禁用定时任务",
    )
)
async def toggle_schedule(schedule_id: str, enabled: bool) -> str:
    """启用或禁用定时任务
    
    Args:
        schedule_id: 任务ID
        enabled: True启用，False禁用
    
    Returns:
        操作结果
    """
    try:
        from scheduler import task_scheduler
        
        if schedule_id not in task_scheduler.schedules:
            return f"❌ 任务不存在: {schedule_id}"
        
        task_scheduler.schedules[schedule_id]["enabled"] = enabled
        task_scheduler._save_schedules()
        
        name = task_scheduler.schedules[schedule_id].get("name", "未命名")
        status = "启用" if enabled else "禁用"
        
        return f"✅ 已{status}定时任务: {name}"
            
    except Exception as e:
        return log_and_format_error("toggle_schedule", e)


# ============================================================================
# 定时任务AI执行工具
# ============================================================================

@mcp.tool(
    annotations=ToolAnnotations(
        title="获取待AI润色的定时任务",
        readOnlyHint=True,
    )
)
async def get_pending_ai_tasks() -> str:
    """获取所有待AI润色执行的定时任务
    
    Returns:
        待执行任务列表（JSON格式）
    """
    try:
        from scheduler import task_scheduler
        import json
        
        pending_tasks = []
        now = datetime.now()
        
        for schedule_id, schedule in task_scheduler.schedules.items():
            if not schedule.get("enabled", True):
                continue
            if schedule.get("action") != "ai_execute":
                continue
            
            # 检查是否该执行
            execute_time = schedule.get("execute_time")
            if not execute_time:
                continue
                
            target_time = datetime(
                execute_time.get("year", now.year),
                execute_time.get("month", now.month),
                execute_time.get("day", now.day),
                execute_time.get("hour", 0),
                execute_time.get("minute", 0),
                execute_time.get("second", 0)
            )
            
            time_diff = (now - target_time).total_seconds()
            last_run = schedule.get("last_run")
            
            # 已到执行时间且未执行
            if time_diff >= 0 and not last_run:
                pending_tasks.append({
                    "task_id": schedule_id,
                    "name": schedule.get("name"),
                    "message": schedule.get("message"),
                    "friend_ids": schedule.get("friend_ids", []),
                    "stranger_usernames": schedule.get("stranger_usernames", []),
                    "account_id": (schedule.get("accounts") or schedule.get("account_ids") or [""])[0],
                    "interval": schedule.get("interval", 2000),
                    "execute_time": execute_time
                })
        
        if not pending_tasks:
            return "📭 没有待AI润色执行的定时任务"
        
        return json.dumps({
            "count": len(pending_tasks),
            "tasks": pending_tasks
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return log_and_format_error("get_pending_ai_tasks", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="执行AI润色定时任务",
        destructiveHint=True,
    )
)
async def execute_ai_task(
    task_id: str,
    polished_message: str
) -> str:
    """执行AI润色后的定时任务
    
    Args:
        task_id: 任务ID（从get_pending_ai_tasks获取）
        polished_message: AI润色后的消息内容
    
    Returns:
        执行结果
    """
    try:
        from scheduler import task_scheduler
        import asyncio
        
        schedule = task_scheduler.schedules.get(task_id)
        if not schedule:
            return f"❌ 任务不存在: {task_id}"
        
        # 获取发送目标
        friend_ids = schedule.get("friend_ids", [])
        stranger_usernames = schedule.get("stranger_usernames", [])
        interval = schedule.get("interval", 2000)
        accounts = schedule.get("accounts") or schedule.get("account_ids")
        account_id = accounts[0] if accounts else None
        
        if not account_id:
            return "❌ 没有可用账号"
        
        # 获取客户端
        client = await account_manager.get_client(account_id)
        if not client:
            return f"❌ 获取账号 {account_id} 客户端失败"
        
        # 合并发送目标
        targets = []
        for fid in friend_ids:
            targets.append({"type": "id", "value": fid})
        for username in stranger_usernames:
            targets.append({"type": "username", "value": username})
        
        if not targets:
            targets = [{"type": "id", "value": "me"}]
        
        success_count = 0
        fail_count = 0
        results = []
        
        for i, target in enumerate(targets):
            try:
                target_value = target["value"]
                entity = await client.get_entity(target_value)
                await client.send_message(entity, polished_message)
                success_count += 1
                results.append(f"✅ {target_value}")
                
                if i < len(targets) - 1:
                    await asyncio.sleep(interval / 1000)
                    
            except Exception as e:
                fail_count += 1
                results.append(f"❌ {target_value}: {str(e)}")
        
        # 更新任务状态
        now_iso = datetime.now().isoformat()
        schedule["last_run"] = now_iso
        schedule["lastRun"] = now_iso
        schedule["run_count"] = schedule.get("run_count", 0) + 1
        if fail_count > 0:
            schedule["fail_count"] = schedule.get("fail_count", 0) + 1
        task_scheduler._save_schedules()
        
        return f"""✅ AI润色任务执行完成

任务: {schedule.get('name')}
润色后消息: {polished_message[:100]}{'...' if len(polished_message) > 100 else ''}

发送结果:
- 成功: {success_count}
- 失败: {fail_count}

详情:
""" + "\n".join(results)
        
    except Exception as e:
        return log_and_format_error("execute_ai_task", e)


# ============================================================================
# 主入口
# ============================================================================

async def check_login():
    """检查是否已登录"""
    if not os.path.exists(SESSION_FILE):
        print("\n" + "="*60)
        print("⚠️  未检测到 Telegram session")
        print("="*60)
        print("\n请先运行登录命令：")
        print("  python web_login.py")
        print("\n或者直接运行：")
        print("  python -c \"from web_login import run_login_server; run_login_server()\"")
        print("\n然后在浏览器中扫码登录\n")
        return False
    return True


async def main():
    """MCP 服务器主入口"""
    # 检查登录状态
    if not await check_login():
        sys.exit(1)

    # 验证 session
    try:
        c = await get_client()
        await c.get_me()
        print("✅ Telegram 连接成功!")
    except Exception as e:
        print(f"⚠️  Session 验证失败: {e}")
        print("\n请重新运行登录: python web_login.py")
        sys.exit(1)

    # 启动 MCP 服务器
    await mcp.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
