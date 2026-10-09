import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage import ChatStore, ReceiverOfflineError


class FakeClock:
    """测试中手动控制时间，让在线和离线结果稳定可复现。"""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class ChatStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "chat_data.json"
        self.clock = FakeClock()
        self.store = ChatStore(self.data_file, clock=self.clock)
        await self.store.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_online_users_messages_and_conversations(self) -> None:
        await self.store.register_user("alice-id", "Alice")
        await self.store.register_user("bob-id", "Bob")

        online_users = await self.store.list_online_users("alice-id")
        self.assertEqual([user["nickname"] for user in online_users], ["Bob"])

        message = await self.store.add_message(
            "alice-id", "bob-id", "你好，Bob"
        )
        self.assertEqual(message["id"], 1)

        history = await self.store.list_messages("bob-id", "alice-id")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["content"], "你好，Bob")

        newer_messages = await self.store.list_messages(
            "alice-id", "bob-id", after_id=1
        )
        self.assertEqual(newer_messages, [])

        conversations = await self.store.list_recent_conversations("alice-id")
        self.assertEqual(conversations[0]["peer"]["nickname"], "Bob")
        self.assertEqual(
            conversations[0]["last_message"]["content"], "你好，Bob"
        )

    async def test_json_data_survives_store_restart(self) -> None:
        await self.store.register_user("alice-id", "Alice")
        await self.store.register_user("bob-id", "Bob")
        await self.store.add_message("alice-id", "bob-id", "刷新后还能看到")

        restarted_store = ChatStore(self.data_file, clock=self.clock)
        await restarted_store.initialize()
        history = await restarted_store.list_messages("alice-id", "bob-id")

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["content"], "刷新后还能看到")
        self.assertEqual(history[0]["id"], 1)

    async def test_offline_receiver_rejects_new_message(self) -> None:
        await self.store.register_user("alice-id", "Alice")
        await self.store.register_user("bob-id", "Bob")
        await self.store.add_message("alice-id", "bob-id", "第一条消息")

        self.clock.advance(7)
        await self.store.heartbeat("alice-id")

        with self.assertRaises(ReceiverOfflineError):
            await self.store.add_message("alice-id", "bob-id", "离线消息")

        history = await self.store.list_messages("alice-id", "bob-id")
        conversations = await self.store.list_recent_conversations("alice-id")
        self.assertEqual(len(history), 1)
        self.assertFalse(conversations[0]["peer"]["is_online"])


if __name__ == "__main__":
    unittest.main()
