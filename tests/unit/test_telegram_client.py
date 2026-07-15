"""Offline contract tests for the Telegram Bot API client."""

import json

import httpx
import pytest

from personal_agent.telegram.client import TelegramApiError, TelegramBotClient


async def test_client_polls_updates_and_sends_messages() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((request.url.path.rsplit("/", maxsplit=1)[-1], payload))
        if request.url.path.endswith("getUpdates"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 10,
                            "message": {
                                "message_id": 5,
                                "from": {"id": 22, "is_bot": False},
                                "chat": {"id": 11, "type": "private"},
                                "text": "hello",
                            },
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": 6,
                    "chat": {"id": 11, "type": "private"},
                    "text": payload.get("text"),
                },
            },
        )

    async with TelegramBotClient(
        bot_token="secret-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        updates = await client.get_updates(offset=10, timeout_seconds=3)
        sent = await client.send_message(chat_id=11, text="response")

    assert updates[0].message is not None
    assert updates[0].message.from_user is not None
    assert updates[0].message.from_user.id == 22
    assert sent.message_id == 6
    assert requests[0] == (
        "getUpdates",
        {
            "timeout": 3,
            "allowed_updates": ["message", "callback_query"],
            "offset": 10,
        },
    )
    assert requests[1] == ("sendMessage", {"chat_id": 11, "text": "response"})


async def test_client_errors_do_not_expose_bot_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    client = TelegramBotClient(
        bot_token="highly-sensitive-token",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(TelegramApiError) as captured:
        await client.delete_webhook()
    await client.aclose()

    assert "highly-sensitive-token" not in str(captured.value)
    assert "Unauthorized" not in str(captured.value)
