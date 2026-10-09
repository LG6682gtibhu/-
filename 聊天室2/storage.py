from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


ONLINE_TIMEOUT_SECONDS = 6

Clock = Callable[[], datetime]


class ReceiverOfflineError(Exception):
    """接收者当前不在线，消息不能被发送。"""


def utc_now() -> datetime:
    """返回带时区的当前 UTC 时间。"""

    return datetime.now(timezone.utc)


def datetime_to_text(value: datetime) -> str:
    """把时间统一转换为 ISO 8601 文本，方便保存和前端展示。"""

    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def text_to_datetime(value: str) -> datetime:
    """把 JSON 中的时间文本转回可比较的时间对象。"""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def create_empty_data() -> dict[str, Any]:
    """创建一份全新的 JSON 数据结构。"""

    return {
        "next_message_id": 1,
        "users": {},
        "messages": [],
    }


class ChatStore:
    """负责内存状态、JSON 持久化和并发安全。"""

    def __init__(self, data_file: Path, clock: Clock | None = None) -> None:
        self.data_file = Path(data_file)
        self._clock = clock or utc_now
        self._lock = asyncio.Lock()
        self._data = create_empty_data()

    async def initialize(self) -> None:
        """启动时读取已有 JSON；文件不存在时自动创建。"""

        async with self._lock:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            if self.data_file.exists():
                self._data = self._read_data_from_disk()
            else:
                self._write_data_to_disk(self._data)

    def _read_data_from_disk(self) -> dict[str, Any]:
        """读取并检查 JSON 的基本结构。"""

        try:
            raw = json.loads(self.data_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"数据文件不是合法的 JSON：{self.data_file}"
            ) from exc

        if not isinstance(raw, dict):
            raise RuntimeError(f"数据文件顶层必须是对象：{self.data_file}")

        data = create_empty_data()
        users = raw.get("users", {})
        messages = raw.get("messages", [])
        next_message_id = raw.get("next_message_id", 1)

        if not isinstance(users, dict) or not isinstance(messages, list):
            raise RuntimeError(f"数据文件结构不正确：{self.data_file}")

        data["users"] = users
        data["messages"] = messages
        data["next_message_id"] = max(1, int(next_message_id))
        return data

    def _write_data_to_disk(self, data: dict[str, Any]) -> None:
        """先写临时文件再原子替换，避免写到一半留下损坏的 JSON。"""

        temp_file = self.data_file.with_name(f"{self.data_file.name}.tmp")
        with temp_file.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_file, self.data_file)

    def _is_online(self, user: dict[str, Any], now: datetime) -> bool:
        """最近一次心跳距离现在不超过阈值时，用户视为在线。"""

        try:
            last_seen = text_to_datetime(str(user["last_seen"]))
        except (KeyError, TypeError, ValueError):
            return False
        return now - last_seen <= timedelta(seconds=ONLINE_TIMEOUT_SECONDS)

    @staticmethod
    def _public_user(user: dict[str, Any], is_online: bool) -> dict[str, Any]:
        """裁掉内部字段，只返回前端真正需要的信息。"""

        return {
            "id": str(user["id"]),
            "nickname": str(user["nickname"]),
            "is_online": is_online,
            "last_seen": str(user["last_seen"]),
        }

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        """按 ID 查询用户，供接口检查用户是否存在。"""

        async with self._lock:
            user = self._data["users"].get(user_id)
            return deepcopy(user) if user else None

    async def register_user(self, user_id: str, nickname: str) -> dict[str, Any]:
        """创建新用户，或更新刷新页面后的旧用户。"""

        async with self._lock:
            now = self._clock()
            timestamp = datetime_to_text(now)
            new_data = deepcopy(self._data)
            existing = new_data["users"].get(user_id)

            if existing:
                existing["nickname"] = nickname
                existing["last_seen"] = timestamp
            else:
                existing = {
                    "id": user_id,
                    "nickname": nickname,
                    "created_at": timestamp,
                    "last_seen": timestamp,
                }
                new_data["users"][user_id] = existing

            self._write_data_to_disk(new_data)
            self._data = new_data
            return self._public_user(existing, True)

    async def heartbeat(self, user_id: str) -> dict[str, Any]:
        """收到心跳时更新时间；用户不存在则抛出 KeyError。"""

        async with self._lock:
            if user_id not in self._data["users"]:
                raise KeyError(user_id)

            now = self._clock()
            new_data = deepcopy(self._data)
            user = new_data["users"][user_id]
            user["last_seen"] = datetime_to_text(now)

            self._write_data_to_disk(new_data)
            self._data = new_data
            return self._public_user(user, True)

    async def list_online_users(self, current_user_id: str) -> list[dict[str, Any]]:
        """返回除当前用户之外的在线用户。"""

        async with self._lock:
            now = self._clock()
            users = [
                self._public_user(user, True)
                for user_id, user in self._data["users"].items()
                if user_id != current_user_id and self._is_online(user, now)
            ]
            users.sort(key=lambda item: (item["nickname"].casefold(), item["id"]))
            return users

    async def list_recent_conversations(
        self, current_user_id: str
    ) -> list[dict[str, Any]]:
        """从全部消息中找出每位聊天对象的最后一条消息。"""

        async with self._lock:
            now = self._clock()
            latest_by_peer: dict[str, dict[str, Any]] = {}

            for message in self._data["messages"]:
                sender_id = str(message["sender_id"])
                receiver_id = str(message["receiver_id"])

                if sender_id == current_user_id:
                    peer_id = receiver_id
                elif receiver_id == current_user_id:
                    peer_id = sender_id
                else:
                    continue

                previous = latest_by_peer.get(peer_id)
                if previous is None or int(message["id"]) > int(previous["id"]):
                    latest_by_peer[peer_id] = message

            conversations: list[dict[str, Any]] = []
            for peer_id, last_message in latest_by_peer.items():
                peer = self._data["users"].get(peer_id)
                if peer is None:
                    continue
                conversations.append(
                    {
                        "peer": self._public_user(
                            peer, self._is_online(peer, now)
                        ),
                        "last_message": deepcopy(last_message),
                    }
                )

            conversations.sort(
                key=lambda item: int(item["last_message"]["id"]), reverse=True
            )
            return conversations

    async def add_message(
        self, sender_id: str, receiver_id: str, content: str
    ) -> dict[str, Any]:
        """保存在线用户之间的一条消息，并返回给发送方。"""

        async with self._lock:
            users = self._data["users"]
            if sender_id not in users:
                raise KeyError("sender")
            if receiver_id not in users:
                raise KeyError("receiver")

            now = self._clock()
            if not self._is_online(users[receiver_id], now):
                raise ReceiverOfflineError

            new_data = deepcopy(self._data)
            message_id = int(new_data["next_message_id"])
            message = {
                "id": message_id,
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "content": content,
                "created_at": datetime_to_text(now),
            }
            new_data["messages"].append(message)
            new_data["next_message_id"] = message_id + 1
            new_data["users"][sender_id]["last_seen"] = datetime_to_text(now)

            self._write_data_to_disk(new_data)
            self._data = new_data
            return deepcopy(message)

    async def list_messages(
        self,
        user_id: str,
        peer_id: str,
        after_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """查询双方消息；传入 after_id 时只返回更新的消息。"""

        async with self._lock:
            result = []
            for message in self._data["messages"]:
                sender_id = str(message["sender_id"])
                receiver_id = str(message["receiver_id"])
                belongs_to_pair = {sender_id, receiver_id} == {user_id, peer_id}

                if not belongs_to_pair:
                    continue
                if after_id is not None and int(message["id"]) <= after_id:
                    continue
                result.append(deepcopy(message))

            result.sort(key=lambda item: int(item["id"]))
            return result
