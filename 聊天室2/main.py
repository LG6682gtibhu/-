from __future__ import annotations

import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from schemas import (
    ConversationPublic,
    HealthResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    MessagesResponse,
    SendMessageRequest,
    SendMessageResponse,
    SessionRequest,
    SessionResponse,
    UserPublic,
)
from storage import ChatStore, ReceiverOfflineError, utc_now, datetime_to_text

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_FILE = Path(
    os.getenv("CHAT_DATA_FILE", str(BASE_DIR / "data" / "chat_data.json"))
).expanduser()

store = ChatStore(DATA_FILE)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用启动时加载 JSON；结束时 FastAPI 自动清理生命周期。"""

    await store.initialize()
    yield


app = FastAPI(
    title="局域网一对一聊天室",
    version="1.0.0",
    description="使用 HTTP 轮询和 JSON 持久化的轻量聊天室。",
    lifespan=lifespan,
)


@app.middleware("http")
async def prevent_stale_ui_cache(request, call_next):
    """开发阶段禁止浏览器缓存页面和静态资源，避免看到旧界面。"""

    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """返回聊天室页面。"""

    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """供前端和测试程序检查服务是否正常。"""

    return HealthResponse(status="ok", server_time=datetime_to_text(utc_now()))


@app.post("/api/session", response_model=SessionResponse)
async def create_session(payload: SessionRequest) -> SessionResponse:
    """创建或恢复用户，并返回在线列表和最近会话。"""

    user_id = payload.user_id or str(uuid4())
    user = await store.register_user(user_id, payload.nickname)
    online_users = await store.list_online_users(user_id)
    conversations = await store.list_recent_conversations(user_id)

    return SessionResponse(
        user=UserPublic(**user),
        online_users=[UserPublic(**item) for item in online_users],
        conversations=[ConversationPublic(**item) for item in conversations],
        server_time=datetime_to_text(utc_now()),
    )


@app.post("/api/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(payload: HeartbeatRequest) -> HeartbeatResponse:
    """更新在线状态，同时返回最新的用户与会话列表。"""

    try:
        await store.heartbeat(payload.user_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在，请刷新页面后重新进入",
        ) from exc

    online_users = await store.list_online_users(payload.user_id)
    conversations = await store.list_recent_conversations(payload.user_id)
    return HeartbeatResponse(
        online_users=[UserPublic(**item) for item in online_users],
        conversations=[ConversationPublic(**item) for item in conversations],
        server_time=datetime_to_text(utc_now()),
    )


@app.get("/api/messages", response_model=MessagesResponse)
async def get_messages(
    me: str = Query(..., min_length=1, max_length=64),
    peer: str = Query(..., min_length=1, max_length=64),
    after_id: int | None = Query(default=None, ge=0),
) -> MessagesResponse:
    """获取双方历史消息，或只获取某条消息之后的新消息。"""

    if await store.get_user(me) is None:
        raise HTTPException(status_code=404, detail="当前用户不存在")
    if await store.get_user(peer) is None:
        raise HTTPException(status_code=404, detail="聊天对象不存在")

    messages = await store.list_messages(me, peer, after_id)
    return MessagesResponse(messages=messages)


@app.post("/api/messages", response_model=SendMessageResponse)
async def send_message(payload: SendMessageRequest) -> SendMessageResponse:
    """保存消息；接收者离线时拒绝发送。"""

    try:
        message = await store.add_message(
            payload.sender_id,
            payload.receiver_id,
            payload.content,
        )
    except KeyError as exc:
        missing = "发送者" if str(exc) == "'sender'" else "接收者"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{missing}不存在",
        ) from exc
    except ReceiverOfflineError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="对方当前离线，消息未发送",
        ) from exc

    return SendMessageResponse(message=message)


# 前端通过 /static/app.js 和 /static/style.css 加载资源。
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_suggested_lan_ip() -> str:
    """探测当前系统访问局域网时最可能使用的 IPv4 地址。"""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def print_startup_addresses(port: int) -> None:
    """启动前把本机和局域网访问地址打印出来。"""

    lan_ip = get_suggested_lan_ip()
    print("\n聊天室服务即将启动：")
    print(f"  本机访问：http://127.0.0.1:{port}")
    if lan_ip != "127.0.0.1":
        print(f"  局域网访问：http://{lan_ip}:{port}")
    print("  手机需与本电脑连接同一 Wi-Fi。\n")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print_startup_addresses(port)
    # 0.0.0.0 表示监听所有网卡，局域网中的其他设备才能访问。
    uvicorn.run(app, host="0.0.0.0", port=port)
