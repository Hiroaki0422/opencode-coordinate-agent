"""Small asynchronous client for the Telegram Bot HTTP API."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError


class TelegramApiError(RuntimeError):
    """A sanitized Telegram Bot API failure."""


class TelegramUser(BaseModel):
    id: int
    is_bot: bool = False
    username: str | None = None


class TelegramChat(BaseModel):
    id: int
    type: str


class TelegramMessage(BaseModel):
    message_id: int
    chat: TelegramChat
    from_user: TelegramUser | None = Field(default=None, alias="from")
    text: str | None = None


class TelegramCallbackQuery(BaseModel):
    id: str
    from_user: TelegramUser = Field(alias="from")
    message: TelegramMessage | None = None
    data: str | None = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


_UPDATE_LIST = TypeAdapter(list[TelegramUpdate])


class TelegramBotClient:
    """Call the subset of Bot API methods used by long polling."""

    def __init__(
        self,
        *,
        bot_token: str,
        base_url: str = "https://api.telegram.org",
        request_timeout_seconds: float = 45.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._request_timeout_seconds = request_timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/bot{bot_token}/",
            timeout=request_timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> TelegramBotClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def delete_webhook(self) -> None:
        await self._call("deleteWebhook", {"drop_pending_updates": False})

    async def get_updates(
        self,
        *,
        offset: int | None,
        timeout_seconds: int,
    ) -> list[TelegramUpdate]:
        payload: dict[str, Any] = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._call(
            "getUpdates",
            payload,
            timeout=max(self._request_timeout_seconds, timeout_seconds + 5.0),
        )
        try:
            return _UPDATE_LIST.validate_python(result)
        except ValidationError as error:
            raise TelegramApiError("Telegram returned malformed updates") from error

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramMessage:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = await self._call("sendMessage", payload)
        return self._parse_message(result)

    async def edit_message_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramMessage:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = await self._call("editMessageText", payload)
        return self._parse_message(result)

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text is not None:
            payload["text"] = text
        await self._call("answerCallbackQuery", payload)

    async def send_chat_action(self, *, chat_id: int, action: str = "typing") -> None:
        await self._call("sendChatAction", {"chat_id": chat_id, "action": action})

    async def _call(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Any:
        try:
            response = await self._client.post(method, json=payload, timeout=timeout)
        except httpx.HTTPError as error:
            raise TelegramApiError(f"Telegram API request failed: {method}") from error
        try:
            body = response.json()
        except ValueError as error:
            raise TelegramApiError(f"Telegram API returned invalid JSON: {method}") from error
        if response.is_error or not isinstance(body, dict) or body.get("ok") is not True:
            raise TelegramApiError(
                f"Telegram API rejected {method} (HTTP {response.status_code})"
            )
        if "result" not in body:
            raise TelegramApiError(f"Telegram API omitted the result: {method}")
        return body["result"]

    @staticmethod
    def _parse_message(result: Any) -> TelegramMessage:
        try:
            return TelegramMessage.model_validate(result)
        except ValidationError as error:
            raise TelegramApiError("Telegram returned a malformed message") from error
