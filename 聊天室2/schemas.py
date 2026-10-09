from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator


# 统一约束用户输入，避免路由函数里重复写判断。
Nickname = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20),
]
UserId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
MessageContent = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]


class SessionRequest(BaseModel):
    """登录或刷新页面时提交的数据。"""

    user_id: UserId | None = None
    nickname: Nickname


class HeartbeatRequest(BaseModel):
    """心跳请求只携带当前用户 ID。"""

    user_id: UserId


class SendMessageRequest(BaseModel):
    """发送消息时，由服务端决定消息 ID 和时间。"""

    sender_id: UserId
    receiver_id: UserId
    content: MessageContent

    @model_validator(mode="after")
    def prevent_self_message(self) -> "SendMessageRequest":
        if self.sender_id == self.receiver_id:
            raise ValueError("不能给自己发送消息")
        return self


class UserPublic(BaseModel):
    """返回给前端的用户信息。"""

    id: str
    nickname: str
    is_online: bool
    last_seen: str


class MessagePublic(BaseModel):
    """一条聊天消息。"""

    id: int
    sender_id: str
    receiver_id: str
    content: str
    created_at: str


class ConversationPublic(BaseModel):
    """最近会话列表中的一项。"""

    peer: UserPublic
    last_message: MessagePublic


class SessionResponse(BaseModel):
    """建立会话后，前端一次拿到所需的基础数据。"""

    user: UserPublic
    online_users: list[UserPublic]
    conversations: list[ConversationPublic]
    server_time: str


class HeartbeatResponse(BaseModel):
    """一次心跳返回最新的在线用户和最近会话。"""

    online_users: list[UserPublic]
    conversations: list[ConversationPublic]
    server_time: str


class MessagesResponse(BaseModel):
    messages: list[MessagePublic]


class SendMessageResponse(BaseModel):
    message: MessagePublic


class HealthResponse(BaseModel):
    status: str
    server_time: str
