#!/usr/bin/env python3
"""
Web 管理后台
FastAPI + WebSocket 实现实时状态推送
"""
import asyncio
import json
import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# 导入管理模块
from account_manager import account_manager
from proxy_manager import proxy_manager
from health_monitor import health_monitor
from stats_tracker import stats_tracker
from log_manager import log_manager
from template_manager import template_manager
from scheduler import task_scheduler
from batch_operations import batch_operations


# ============ FastAPI 应用 ============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    # 初始化默认模板（如果没有模板）
    if not template_manager.list_templates():
        template_manager.add_template(
            "greeting",
            "问候消息",
            "你好 {name}，现在是 {time}，祝你今天愉快！",
            "general",
            ["name", "time"]
        )
        template_manager.add_template(
            "notification",
            "通知消息",
            "通知：{content}\n发送时间：{date} {time}",
            "general",
            ["content", "date", "time"]
        )
        print("✅ 已初始化默认消息模板")

    # 启动定时任务调度器
    scheduler_task = asyncio.create_task(task_scheduler.start())
    print("✅ 定时任务调度器已启动")

    # 启动健康监控
    await health_monitor.start_monitoring(interval=300)  # 每5分钟检查一次
    print("✅ 健康监控已启动")

    yield

    # 关闭时执行
    scheduler_task.cancel()
    health_monitor.stop_monitoring()
    print("🛴 定时任务调度器和健康监控已停止")


app = FastAPI(title="Telegram 账号管理后台", lifespan=lifespan)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")


# ============ 数据模型 ============

class AddProxyRequest(BaseModel):
    proxy_id: str
    protocol: str  # socks5, http, https, socks4
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None


class SetGlobalProxyRequest(BaseModel):
    protocol: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None


class AssignProxyRequest(BaseModel):
    account_id: str
    proxy_id: str


class BatchImportRequest(BaseModel):
    accounts: List[dict]


# ============ API 端点 ============

@app.get("/")
async def root():
    """重定向到管理页面"""
    return JSONResponse(content={"message": "管理后台运行中", "url": "/static/dashboard.html"})


# ============ 账号管理 API ============

@app.get("/api/accounts")
async def list_accounts():
    """获取所有账号列表"""
    try:
        accounts = account_manager.list_accounts()
        return {
            "success": True,
            "accounts": accounts,
            "total": len(accounts)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/accounts/generate-qr")
async def generate_qr_code(request: dict):
    """生成二维码"""
    account_id = request.get("account_id")
    proxy_id = request.get("proxy_id")  # 可选：登录时指定的代理

    if not account_id:
        raise HTTPException(status_code=400, detail="缺少 account_id")

    stats_tracker.record_use(account_id)

    # 优先使用登录时指定的代理，否则使用账号关联的代理
    if proxy_id:
        proxy = proxy_manager.get_proxy(proxy_id)
    else:
        proxy = proxy_manager.get_proxy_for_account(account_id)

    result = await account_manager.generate_qr_code(account_id, proxy)
    return result


@app.get("/api/accounts/{account_id}/qr-status")
async def get_qr_status(account_id: str):
    """获取二维码登录状态"""
    result = account_manager.check_qr_status(account_id)
    return result


@app.post("/api/accounts/{account_id}/refresh-qr")
async def refresh_qr_code(account_id: str):
    """刷新二维码"""
    proxy = proxy_manager.get_proxy_for_account(account_id)
    result = await account_manager.refresh_qr_code(account_id, proxy)
    return result


@app.post("/api/accounts/{account_id}/2fa-password")
async def submit_2fa_password(account_id: str, request: dict):
    """提交两步验证密码"""
    password = request.get("password")
    if not password:
        raise HTTPException(status_code=400, detail="缺少密码")

    stats_tracker.record_use(account_id)
    result = await account_manager.submit_2fa_password(account_id, password)
    return result


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: str):
    """删除账号"""
    if account_id == "default":
        raise HTTPException(status_code=400, detail="无法删除默认账号")

    success = await account_manager.remove_account(account_id)
    if success:
        log_manager.add_log("账号管理", account_id, "删除账号", "warning")
        return {"success": True, "message": "账号已删除"}
    else:
        raise HTTPException(status_code=404, detail="账号不存在")


@app.post("/api/accounts/add-session")
async def add_account_with_session(request: dict):
    """使用Session添加账号"""
    account_id = request.get("account_id")
    session_string = request.get("session_string")
    phone = request.get("phone")
    username = request.get("username")

    if not account_id or not session_string:
        raise HTTPException(status_code=400, detail="缺少必要参数")

    success = await account_manager.add_account_with_session(
        account_id, session_string, phone, username
    )

    if success:
        stats_tracker.record_use(account_id)
        log_manager.add_log("账号管理", account_id, "添加账号", "success")
        return {"success": True, "message": "账号添加成功"}
    else:
        raise HTTPException(status_code=400, detail="添加失败")


@app.get("/api/accounts/{account_id}/export-session")
async def export_session(account_id: str):
    """导出Session"""
    session = account_manager.export_session(account_id)
    if session:
        return {
            "success": True,
            "account_id": account_id,
            "session_string": session
        }
    else:
        raise HTTPException(status_code=404, detail="账号不存在")


@app.post("/api/accounts/batch-import")
async def batch_import(request: BatchImportRequest):
    """批量导入账号"""
    result = await account_manager.batch_import(request.accounts)
    return result


@app.get("/api/accounts/{account_id}/friends")
async def get_account_friends(account_id: str):
    """获取账号的好友列表"""
    try:
        friends = await account_manager.get_friends(account_id)
        return {
            "success": True,
            "friends": friends,
            "total": len(friends)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/accounts/validate-usernames")
async def validate_usernames(request: dict):
    """验证用户名是否有效"""
    account_id = request.get("account_id")
    usernames = request.get("usernames", [])
    
    if not account_id or not usernames:
        raise HTTPException(status_code=400, detail="缺少参数")
    
    try:
        results = await account_manager.validate_usernames(account_id, usernames)
        return {
            "success": True,
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============ 手机号登录 API ============

@app.post("/api/accounts/send-code")
async def send_phone_code(request: dict):
    """发送验证码到手机号"""
    account_id = request.get("account_id")
    phone = request.get("phone")
    proxy_id = request.get("proxy_id")  # 可选：登录时指定的代理

    if not account_id or not phone:
        raise HTTPException(status_code=400, detail="缺少必要参数")

    # 优先使用登录时指定的代理，否则使用账号关联的代理
    if proxy_id:
        proxy = proxy_manager.get_proxy(proxy_id)
    else:
        proxy = proxy_manager.get_proxy_for_account(account_id)

    result = await account_manager.send_phone_code(account_id, phone, proxy)

    if result.get("success"):
        log_manager.add_log("账号管理", account_id, f"发送验证码到 {phone}", "info")
    return result


@app.post("/api/accounts/verify-code")
async def verify_phone_code(request: dict):
    """验证手机验证码"""
    account_id = request.get("account_id")
    code = request.get("code")

    if not account_id or not code:
        raise HTTPException(status_code=400, detail="缺少必要参数")

    result = await account_manager.verify_phone_code(account_id, code)

    if result.get("success"):
        stats_tracker.record_use(account_id)
        log_manager.add_log("账号管理", account_id, "手机号登录成功", "success")

    return result


@app.post("/api/accounts/{account_id}/phone-2fa")
async def submit_phone_2fa(account_id: str, request: dict):
    """提交手机号登录的 2FA 密码"""
    password = request.get("password")
    if not password:
        raise HTTPException(status_code=400, detail="缺少密码")

    result = await account_manager.submit_2fa_for_phone(account_id, password)

    if result.get("success"):
        stats_tracker.record_use(account_id)
        log_manager.add_log("账号管理", account_id, "手机号登录成功(2FA)", "success")

    return result


@app.get("/api/accounts/{account_id}/phone-status")
async def get_phone_login_status(account_id: str):
    """获取手机号登录状态"""
    return account_manager.get_phone_login_status(account_id)


@app.delete("/api/accounts/{account_id}/phone-login")
async def cancel_phone_login(account_id: str):
    """取消手机号登录"""
    success = await account_manager.cancel_phone_login(account_id)
    if success:
        return {"success": True, "message": "已取消"}
    else:
        raise HTTPException(status_code=404, detail="会话不存在")


# ============ 代理管理 API ============

@app.get("/api/proxies")
async def list_proxies():
    """获取所有代理"""
    proxies = proxy_manager.list_proxies()
    return {
        "success": True,
        **proxies
    }


@app.post("/api/proxies/add")
async def add_proxy(request: AddProxyRequest):
    """添加代理"""
    success = proxy_manager.add_proxy(
        request.proxy_id,
        request.protocol,
        request.host,
        request.port,
        request.username,
        request.password
    )

    if success:
        log_manager.add_log("代理管理", request.proxy_id, f"添加代理 {request.protocol}://{request.host}:{request.port}", "success")
        return {"success": True, "message": "代理添加成功"}
    else:
        raise HTTPException(status_code=400, detail="代理协议不支持")


@app.delete("/api/proxies/{proxy_id}")
async def delete_proxy(proxy_id: str):
    """删除代理"""
    success = proxy_manager.delete_proxy(proxy_id)
    if success:
        log_manager.add_log("代理管理", proxy_id, "删除代理", "warning")
        return {"success": True, "message": "代理已删除"}
    else:
        raise HTTPException(status_code=404, detail="代理不存在")


@app.post("/api/proxies/set-global")
async def set_global_proxy(request: SetGlobalProxyRequest):
    """设置全局代理"""
    success = proxy_manager.set_global_proxy(
        request.protocol,
        request.host,
        request.port,
        request.username,
        request.password
    )

    if success:
        log_manager.add_log("代理管理", "global", f"设置全局代理 {request.protocol}://{request.host}:{request.port}", "success")
        return {"success": True, "message": "全局代理设置成功"}
    else:
        raise HTTPException(status_code=400, detail="代理协议不支持")


@app.delete("/api/proxies/global")
async def remove_global_proxy():
    """移除全局代理"""
    proxy_manager.remove_global_proxy()
    log_manager.add_log("代理管理", "global", "移除全局代理", "warning")
    return {"success": True, "message": "全局代理已移除"}


@app.post("/api/proxies/assign")
async def assign_proxy(request: AssignProxyRequest):
    """为账号分配代理"""
    success = proxy_manager.assign_proxy_to_account(
        request.account_id,
        request.proxy_id
    )

    if success:
        log_manager.add_log("代理管理", request.account_id, f"分配代理 {request.proxy_id}", "success")
        return {"success": True, "message": "代理分配成功"}
    else:
        raise HTTPException(status_code=404, detail="代理不存在")


@app.delete("/api/proxies/{proxy_id}/accounts/{account_id}")
async def unassign_proxy(proxy_id: str, account_id: str):
    """取消账号代理分配"""
    success = proxy_manager.unassign_proxy_from_account(account_id, proxy_id)
    if success:
        return {"success": True, "message": "代理分配已取消"}
    else:
        raise HTTPException(status_code=404, detail="代理不存在")


@app.post("/api/proxies/test")
async def test_proxies(proxy_id: Optional[str] = None):
    """测试代理"""
    if proxy_id:
        if proxy_id not in proxy_manager.proxies:
            raise HTTPException(status_code=404, detail="代理不存在")
        result = await proxy_manager.test_proxy(proxy_manager.proxies[proxy_id])
        return {"success": True, "result": result}
    else:
        result = await proxy_manager.test_all_proxies()
        return {"success": True, "results": result}


# ============ 健康监控 API ============

@app.get("/api/health/report")
async def get_health_report(account_id: Optional[str] = None):
    """获取健康报告"""
    report = health_monitor.get_health_report(account_id)
    return {
        "success": True,
        "report": report
    }


@app.post("/api/health/check/{account_id}")
async def check_account_health(account_id: str):
    """检查账号健康状态"""
    result = await health_monitor.check_account_health(account_id)
    return {
        "success": True,
        "result": result
    }


@app.get("/api/health/risk-accounts")
async def get_risk_accounts():
    """获取高风险账号"""
    accounts = health_monitor.get_risk_accounts()
    return {
        "success": True,
        "risk_accounts": accounts,
        "total": len(accounts)
    }


# ============ 统计 API ============

@app.get("/api/stats/summary")
async def get_stats_summary():
    """获取统计摘要"""
    summary = stats_tracker.get_summary()
    return {
        "success": True,
        "summary": summary
    }


@app.get("/api/stats/account/{account_id}")
async def get_account_stats(account_id: str):
    """获取账号统计"""
    stats = stats_tracker.get_account_stats(account_id)
    return {
        "success": True,
        "account_id": account_id,
        "stats": stats
    }


@app.get("/api/stats/daily")
async def get_daily_stats(date: Optional[str] = None):
    """获取每日统计"""
    stats = stats_tracker.get_daily_stats(date)
    return {
        "success": True,
        "date": date or "today",
        "stats": stats
    }


@app.get("/api/stats/weekly")
async def get_weekly_stats(week: Optional[str] = None):
    """获取每周统计"""
    stats = stats_tracker.get_weekly_stats(week)
    return {
        "success": True,
        "week": week or "current",
        "stats": stats
    }


@app.get("/api/stats/top")
async def get_top_accounts(by: str = "uses", limit: int = 10, period: str = "all"):
    """获取最活跃账号"""
    result = stats_tracker.get_top_accounts(by, limit, period)
    return {
        "success": True,
        "top_accounts": result
    }


@app.get("/api/stats/trend/{account_id}")
async def get_activity_trend(account_id: str, days: int = 7):
    """获取活跃度趋势"""
    trend = stats_tracker.get_activity_trend(account_id, days)
    return {
        "success": True,
        "account_id": account_id,
        "trend": trend
    }


# ============ 日志管理 API ============

@app.get("/api/logs")
async def get_logs(limit: int = 100, account: Optional[str] = None, action: Optional[str] = None):
    """获取操作日志"""
    logs = log_manager.get_logs(limit=limit, account=account, action=action)
    return {
        "success": True,
        "logs": logs,
        "total": len(logs)
    }


@app.get("/api/logs/stats")
async def get_log_stats():
    """获取日志统计"""
    stats = log_manager.get_stats()
    return {
        "success": True,
        "stats": stats
    }


@app.post("/api/logs/clear")
async def clear_logs():
    """清空日志"""
    log_manager.clear_logs()
    return {"success": True, "message": "日志已清空"}


# ============ 模板管理 API ============

@app.get("/api/templates")
async def list_templates(category: Optional[str] = None):
    """获取消息模板列表"""
    templates = template_manager.list_templates(category=category)
    return {
        "success": True,
        "templates": templates,
        "total": len(templates)
    }


@app.post("/api/templates")
async def add_template(request: dict):
    """添加消息模板"""
    content = request.get("content")

    # 如果只传了 content，自动生成其他字段
    if content and not request.get("template_id") and not request.get("name"):
        import re
        import time

        # 自动生成模板ID（使用时间戳）
        template_id = f"template_{int(time.time() * 1000) % 1000000}"

        # 自动提取变量
        variables = list(set(re.findall(r'\{(\w+)\}', content)))

        # 自动生成名称（取前20个字符）
        name = content[:20] + ("..." if len(content) > 20 else "")

        # 自动分类
        category = "general"
    else:
        template_id = request.get("template_id")
        name = request.get("name")
        category = request.get("category", "general")
        variables = request.get("variables", [])

    if not content:
        raise HTTPException(status_code=400, detail="缺少消息内容")

    success = template_manager.add_template(
        template_id, name, content, category, variables
    )

    if success:
        log_manager.add_log("模板管理", template_id, f"添加模板: {name}", "success")
        return {"success": True, "message": "模板添加成功"}
    else:
        raise HTTPException(status_code=400, detail="模板ID已存在")


@app.delete("/api/templates/{template_id}")
async def delete_template(template_id: str):
    """删除模板"""
    success = template_manager.delete_template(template_id)
    if success:
        log_manager.add_log("模板管理", template_id, "删除模板", "warning")
        return {"success": True, "message": "模板已删除"}
    else:
        raise HTTPException(status_code=404, detail="模板不存在")


@app.get("/api/templates/{template_id}/preview")
async def preview_template(template_id: str, vars: Optional[str] = None):
    """预览模板渲染结果"""
    import json
    try:
        template_vars = json.loads(vars) if vars else {}
        rendered = template_manager.render_template(template_id, **template_vars)
        return {
            "success": True,
            "template_id": template_id,
            "rendered": rendered
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============ 定时任务 API ============

@app.get("/api/schedules")
async def list_schedules():
    """获取定时任务列表"""
    schedules = task_scheduler.list_schedules()
    return {
        "success": True,
        "schedules": schedules,
        "total": len(schedules)
    }


@app.post("/api/schedules")
async def add_schedule(request: dict):
    """添加定时任务"""
    schedule_id = request.get("schedule_id")
    name = request.get("name")
    action = request.get("action")
    message = request.get("message")
    enabled = request.get("enabled", True)
    
    # 新格式参数
    execute_time = request.get("execute_time")
    repeat = request.get("repeat", "once")
    account_id = request.get("account_id")
    friend_ids = request.get("friend_ids", [])
    stranger_usernames = request.get("stranger_usernames", [])
    interval = request.get("interval", 2000)
    auto_dedup = request.get("auto_dedup", True)
    validate_usernames = request.get("validate_usernames", True)
    
    # 兼容旧格式
    cron = request.get("cron")
    target = request.get("target")
    template_id = request.get("template_id")
    account_ids = request.get("account_ids")

    if not schedule_id or not name:
        raise HTTPException(status_code=400, detail="缺少必要参数")
    
    # 如果是新格式，转换为cron
    if execute_time and not cron:
        hour = execute_time.get("hour", 0)
        minute = execute_time.get("minute", 0)
        second = execute_time.get("second", 0)
        day = execute_time.get("day", "*")
        month = execute_time.get("month", "*")
        
        if repeat == "once":
            cron = f"{minute} {hour} {day} {month} *"
        elif repeat == "daily":
            cron = f"{minute} {hour} * * *"
        elif repeat == "weekly":
            cron = f"{minute} {hour} * * 1"
        elif repeat == "workday":
            cron = f"{minute} {hour} * * 1-5"
        else:
            cron = f"{minute} {hour} * * *"
    
    # 设置target
    if not target:
        if friend_ids or stranger_usernames:
            target = "custom"
        else:
            target = "all"

    success = task_scheduler.add_schedule(
        schedule_id=schedule_id,
        name=name,
        cron=cron or "0 9 * * *",
        action=action or "send_message",
        target=target,
        template_id=template_id,
        message=message,
        account_ids=account_ids or ([account_id] if account_id else []),
        enabled=enabled,
        # 新字段
        execute_time=execute_time,
        repeat=repeat,
        friend_ids=friend_ids,
        stranger_usernames=stranger_usernames,
        interval=interval,
        auto_dedup=auto_dedup,
        validate_usernames=validate_usernames
    )

    if success:
        log_manager.add_log("定时任务", schedule_id, f"添加定时任务: {name}", "success")
        return {"success": True, "message": "定时任务添加成功"}
    else:
        raise HTTPException(status_code=400, detail="任务添加失败")


@app.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """删除定时任务"""
    success = task_scheduler.delete_schedule(schedule_id)
    if success:
        log_manager.add_log("定时任务", schedule_id, "删除定时任务", "warning")
        return {"success": True, "message": "定时任务已删除"}
    else:
        raise HTTPException(status_code=404, detail="定时任务不存在")


@app.post("/api/schedules/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: str):
    """切换定时任务状态"""
    success = task_scheduler.toggle_schedule(schedule_id)
    if success:
        schedule = task_scheduler.schedules.get(schedule_id, {})
        status = "启用" if schedule.get("enabled", False) else "禁用"
        log_manager.add_log("定时任务", schedule_id, f"{status}定时任务", "info")
        return {
            "success": True,
            "message": "状态已更新",
            "enabled": schedule.get("enabled", False)
        }
    else:
        raise HTTPException(status_code=404, detail="定时任务不存在")


@app.get("/api/schedules/{schedule_id}/next-run")
async def get_next_run(schedule_id: str):
    """获取下次执行时间"""
    next_run = task_scheduler.get_next_run(schedule_id)
    if next_run:
        return {
            "success": True,
            "schedule_id": schedule_id,
            "next_run": next_run
        }
    else:
        raise HTTPException(status_code=404, detail="定时任务不存在")


# ============ 批量操作 API ============

@app.post("/api/batch/send-message")
async def batch_send_message_api(request: dict):
    """批量发送消息"""
    chat_id = request.get("chat_id")
    message = request.get("message")
    account_ids = request.get("account_ids")
    delay = request.get("delay", 2.0)

    if not chat_id or not message:
        raise HTTPException(status_code=400, detail="缺少必要参数")

    result = await batch_operations.batch_send_message(
        chat_id=chat_id,
        message=message,
        account_ids=account_ids,
        delay=delay
    )
    return result


@app.post("/api/batch/send-template")
async def batch_send_template_api(request: dict):
    """批量发送模板消息"""
    chat_id = request.get("chat_id")
    template_id = request.get("template_id")
    account_ids = request.get("account_ids")
    template_vars = request.get("template_vars", {})
    delay = request.get("delay", 2.0)

    if not chat_id or not template_id:
        raise HTTPException(status_code=400, detail="缺少必要参数")

    result = await batch_operations.batch_send_template(
        chat_id=chat_id,
        template_id=template_id,
        account_ids=account_ids,
        template_vars=template_vars,
        delay=delay
    )
    return result


@app.post("/api/batch/check-health")
async def batch_check_health_api(request: dict):
    """批量检查账号健康状态"""
    account_ids = request.get("account_ids")
    result = await batch_operations.batch_check_health(account_ids)
    return result


@app.post("/api/batch/export-sessions")
async def batch_export_sessions_api(request: dict):
    """批量导出Session"""
    account_ids = request.get("account_ids")
    result = await batch_operations.batch_export_sessions(account_ids)
    return result


@app.post("/api/batch/delete-accounts")
async def batch_delete_accounts_api(request: dict):
    """批量删除账号"""
    account_ids = request.get("account_ids")
    if not account_ids:
        raise HTTPException(status_code=400, detail="缺少账号ID列表")

    result = await batch_operations.batch_delete_accounts(account_ids)
    return result


@app.post("/api/batch/get-dialogs")
async def batch_get_dialogs_api(request: dict):
    """批量获取对话列表"""
    account_ids = request.get("account_ids")
    limit = request.get("limit", 20)

    result = await batch_operations.batch_get_dialogs(
        account_ids=account_ids,
        limit=limit
    )
    return result


# ============ WebSocket 实时推送 ============

class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """广播消息到所有连接"""
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 实时推送"""
    await manager.connect(websocket)

    try:
        # 启动后台任务，定期推送状态更新
        stop_event = asyncio.Event()

        async def broadcast_status():
            """定期广播状态"""
            while not stop_event.is_set():
                try:
                    # 获取账号列表
                    accounts = account_manager.list_accounts()

                    # 获取健康报告
                    health_report = health_monitor.get_health_report()

                    # 获取统计摘要
                    stats_summary = stats_tracker.get_summary()

                    # 广播状态
                    await manager.broadcast({
                        "type": "status_update",
                        "timestamp": datetime.now().isoformat(),
                        "accounts": accounts,
                        "health": health_report,
                        "stats": stats_summary
                    })

                    await asyncio.sleep(5)  # 每5秒更新一次
                except Exception as e:
                    print(f"广播错误: {e}")
                    await asyncio.sleep(5)

        # 启动广播任务
        broadcast_task = asyncio.create_task(broadcast_status())

        # 处理客户端消息
        while True:
            data = await websocket.receive_json()

            # 处理客户端请求
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        stop_event.set()
    except Exception as e:
        print(f"WebSocket 错误: {e}")
        manager.disconnect(websocket)
        stop_event.set()


# ============ 启动入口 ============

if __name__ == "__main__":
    # 确保目录存在
    os.makedirs("./accounts", exist_ok=True)
    os.makedirs("./static", exist_ok=True)

    print("=" * 60)
    print("🚀 Telegram 账号管理后台")
    print("=" * 60)
    print(f"📱 账号数量: {len(account_manager.accounts)}")
    print(f"🌐 全局代理: {'已设置' if proxy_manager.global_proxy else '未设置'}")
    print(f"🔧 独立代理: {len(proxy_manager.proxies)} 个")
    print("")
    print("🌐 管理界面: http://localhost:8080/static/dashboard.html")
    print("📡 API 文档: http://localhost:8080/docs")
    print("🔌 WebSocket: ws://localhost:8080/ws")
    print("=" * 60)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )
