"""Telethon adapter for user-account authentication, search and byte-range reads."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from telegram_media_automation.application.telegram_types import ChannelScan
from telegram_media_automation.domain.resources import (
    MediaResource,
    ResourceQuality,
    ResourceSource,
)
from telegram_media_automation.domain.rules.episode_parser import parse_episode


class TelegramNotConfigured(RuntimeError):
    """Telegram credentials are absent or incomplete."""


class TelegramResourceMissing(LookupError):
    """The referenced message or its media is no longer available."""


class TelegramPasswordRequired(RuntimeError):
    """Telegram requires the account's two-step-verification password."""


class TelegramChannelUnavailable(LookupError):
    """A channel is not available to the authorized Telegram account."""


VIDEO_EXTENSIONS = frozenset(
    {
        ".avi",
        ".flv",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".ts",
        ".webm",
        ".wmv",
    }
)
VIDEO_MIME_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/mpeg": ".mpeg",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-flv": ".flv",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
}


@dataclass(frozen=True, slots=True)
class TelegramLocator:
    account: str
    chat_id: int
    message_id: int
    media_index: int = 0

    def encode(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def decode(cls, value: str) -> TelegramLocator:
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("Telegram locator must be an object")
        locator = cls(
            account=str(data["account"]),
            chat_id=int(data["chat_id"]),
            message_id=int(data["message_id"]),
            media_index=int(data.get("media_index", 0)),
        )
        if not locator.account or locator.chat_id == 0 or locator.message_id <= 0:
            raise ValueError("Telegram locator contains invalid identity fields")
        if locator.media_index != 0:
            raise ValueError("Telethon messages currently expose one media object")
        return locator


@dataclass(frozen=True, slots=True)
class TelegramChannelInfo:
    identifier: str
    title: str
    visibility: Literal["public", "private"]
    username: str | None
    chat_id: int


def _quality(text: str) -> ResourceQuality:
    resolution_match = re.search(r"(?i)(2160|1080|720|576|480)p?", text)
    codec_match = re.search(r"(?i)(AV1|HEVC|H[.]?265|H[.]?264|AVC)", text)
    codec = codec_match.group(1).replace(".", "").casefold() if codec_match else None
    if codec == "avc":
        codec = "h264"
    languages: list[str] = []
    for pattern, value in (
        (r"(?i)\b(?:CHS|CHI|ZH)\b|中文字幕|中字|国语|國語", "zh"),
        (r"(?i)\bENG?\b|英文", "en"),
    ):
        if re.search(pattern, text):
            languages.append(value)
    return ResourceQuality(
        resolution=int(resolution_match.group(1)) if resolution_match else None,
        codec=codec,
        hdr=bool(re.search(r"(?i)\b(?:HDR10[+]?|DOLBY[ .]?VISION|DV)\b", text)),
        languages=tuple(languages),
    )


def _message_link(chat_id: int, message_id: int, username: str | None = None) -> str | None:
    if message_id <= 0:
        return None
    if username:
        return f"https://t.me/{username}/{message_id}"
    serialized = str(chat_id)
    if serialized.startswith("-100") and serialized[4:].isdigit():
        return f"https://t.me/c/{serialized[4:]}/{message_id}"
    return None


def _channel_id_from_peer(peer: Any) -> int | None:
    raw_channel_id = getattr(peer, "channel_id", None)
    if raw_channel_id is None:
        return None
    try:
        channel_id = int(raw_channel_id)
    except (TypeError, ValueError):
        return None
    return int(f"-100{channel_id}") if channel_id > 0 else None


class TelethonGateway:
    def __init__(
        self,
        session_path: Path,
        api_id: int,
        api_hash: str,
        *,
        account: str = "default",
        client_factory: Callable[..., Any] | None = None,
        proxy: tuple[str, str, int, bool, str | None, str | None] | None = None,
        connect_timeout: int = 8,
        connection_retries: int = 1,
    ) -> None:
        if api_id <= 0 or not api_hash:
            raise TelegramNotConfigured("Telegram API ID and hash are required")
        if connect_timeout <= 0 or connection_retries < 0:
            raise ValueError("Telegram connection timeout or retry count is invalid")
        if client_factory is None:
            try:
                from telethon import TelegramClient  # type: ignore[import-untyped]
            except ImportError as error:
                raise TelegramNotConfigured(
                    "install the 'telegram' extra to enable Telegram access"
                ) from error
            client_factory = TelegramClient
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self.account = account
        client_options: dict[str, object] = {
            "timeout": connect_timeout,
            "connection_retries": connection_retries,
        }
        if proxy is not None:
            client_options["proxy"] = proxy
        self.client = client_factory(session_path, api_id, api_hash, **client_options)
        self._channel_entities: dict[int, Any] = {}

    async def connect(self) -> None:
        if not self.client.is_connected():
            await self.client.connect()

    async def disconnect(self) -> None:
        if self.client.is_connected():
            await self.client.disconnect()

    async def is_authorized(self) -> bool:
        await self.connect()
        # Telethon caches is_user_authorized(), which can remain true after
        # the proxy stops working. get_me() performs an RPC and returns None
        # for an unauthenticated session without exposing account details.
        return await self.client.get_me() is not None

    async def request_login_code(self, phone: str) -> str:
        await self.connect()
        result = await self.client.send_code_request(phone)
        return str(result.phone_code_hash)

    async def sign_in(self, phone: str, code: str, phone_code_hash: str) -> None:
        await self.connect()
        try:
            await self.client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except Exception as error:
            if type(error).__name__ == "SessionPasswordNeededError":
                raise TelegramPasswordRequired from error
            raise

    async def sign_in_password(self, password: str) -> None:
        await self.connect()
        await self.client.sign_in(password=password)

    async def search(
        self, channels: list[str | int], query: str, limit: int = 100
    ) -> list[MediaResource]:
        if not query.strip():
            raise ValueError("search query cannot be empty")
        if limit <= 0 or limit > 500:
            raise ValueError("search limit must be between 1 and 500")
        await self.connect()
        resources: list[MediaResource] = []
        for channel in channels:
            peer = await self._resolve_channel_peer(channel)
            source_info = await self._source_info(peer)
            async for message in self.client.iter_messages(peer, search=query, limit=limit):
                resource = self._safe_map_message(message, query, source_info)
                if resource is not None:
                    resources.append(resource)
        return resources

    def _remember_channel(self, entity: Any) -> TelegramChannelInfo:
        info = self._channel_info(entity)
        self._channel_entities[info.chat_id] = entity
        return info

    async def _find_joined_channel(self, chat_id: int) -> Any:
        async for dialog in self.client.iter_dialogs(limit=500):
            entity = getattr(dialog, "entity", None)
            try:
                info = self._remember_channel(entity)
            except (TypeError, ValueError):
                continue
            if info.chat_id == chat_id:
                return entity
        raise TelegramChannelUnavailable("channel is not joined or no longer available")

    async def _resolve_channel_peer(self, channel: str | int) -> Any:
        """Resolve private numeric IDs even if Telethon did not persist their access hash."""
        if not isinstance(channel, int):
            return channel
        cached = self._channel_entities.get(channel)
        if cached is not None:
            return cached
        get_input_entity = getattr(self.client, "get_input_entity", None)
        if get_input_entity is not None:
            try:
                return await get_input_entity(channel)
            except (IndexError, ValueError):
                # Telethon 1.x may index an empty GetChannels result when the
                # session cache lacks a private channel's access hash.
                pass
        return await self._find_joined_channel(channel)

    async def _source_info(self, peer: Any) -> TelegramChannelInfo | None:
        try:
            entity = await self.client.get_entity(peer)
            return self._remember_channel(entity)
        except (IndexError, TypeError, ValueError):
            try:
                return self._remember_channel(peer)
            except (TypeError, ValueError):
                return None

    @staticmethod
    def _channel_info(entity: Any) -> TelegramChannelInfo:
        if not (
            bool(getattr(entity, "broadcast", False)) or bool(getattr(entity, "megagroup", False))
        ):
            raise ValueError("the Telegram peer is not a channel or supergroup")
        raw_id = int(getattr(entity, "id", 0) or 0)
        if raw_id <= 0:
            raise ValueError("the Telegram channel has no stable ID")
        raw_username = getattr(entity, "username", None)
        username = str(raw_username).strip() if raw_username else None
        chat_id = int(f"-100{raw_id}")
        title = str(getattr(entity, "title", "") or username or chat_id).strip()
        return TelegramChannelInfo(
            identifier=username or str(chat_id),
            title=title,
            visibility="public" if username else "private",
            username=username,
            chat_id=chat_id,
        )

    async def inspect_channel(self, channel: str | int) -> TelegramChannelInfo:
        """Resolve a joined channel and report whether it has a public username."""
        await self.connect()
        try:
            entity = await self.client.get_entity(channel)
        except (IndexError, ValueError):
            if not isinstance(channel, int):
                raise
            entity = await self._find_joined_channel(channel)
        return self._remember_channel(entity)

    async def list_channels(self, limit: int = 200) -> list[TelegramChannelInfo]:
        """List channels and supergroups visible to the authorized user account."""
        if limit <= 0 or limit > 500:
            raise ValueError("channel discovery limit must be between 1 and 500")
        await self.connect()
        channels: dict[int, TelegramChannelInfo] = {}
        async for dialog in self.client.iter_dialogs(limit=limit):
            try:
                info = self._remember_channel(getattr(dialog, "entity", None))
            except (TypeError, ValueError):
                continue
            channels[info.chat_id] = info
        return sorted(
            channels.values(),
            key=lambda item: (item.visibility != "private", item.title.casefold()),
        )

    def _map_message(
        self,
        message: Any,
        query: str,
        source_info: TelegramChannelInfo | None = None,
    ) -> MediaResource | None:
        file = getattr(message, "file", None)
        media = getattr(message, "media", None)
        chat_id = getattr(message, "chat_id", None)
        message_id = getattr(message, "id", None)
        if file is None or media is None or chat_id is None or message_id is None:
            return None
        file_size = int(getattr(file, "size", 0) or 0)
        if file_size <= 0:
            return None
        mime_type = str(getattr(file, "mime_type", "") or "").casefold()
        raw_name = getattr(file, "name", None)
        generated_extension = VIDEO_MIME_EXTENSIONS.get(mime_type, ".bin")
        file_name = str(raw_name or f"telegram-{chat_id}-{message_id}{generated_extension}")
        if (
            not mime_type.startswith("video/")
            and Path(file_name).suffix.casefold() not in VIDEO_EXTENSIONS
        ):
            return None
        message_text = str(getattr(message, "message", "") or "")
        evidence = f"{file_name} {message_text}"
        file_episode = parse_episode(file_name)
        caption_episode = parse_episode(message_text)
        episode_conflict = bool(
            file_episode and caption_episode and file_episode.value != caption_episode.value
        )
        episode = None if episode_conflict else file_episode or caption_episode
        confidence = 80 if query.casefold() in evidence.casefold() else 55
        if episode_conflict:
            confidence = min(confidence, 40)
        numeric_chat_id = int(chat_id)
        if source_info is None or source_info.chat_id != numeric_chat_id:
            entity = getattr(message, "chat", None) or self._channel_entities.get(numeric_chat_id)
            try:
                source_info = self._remember_channel(entity)
            except (TypeError, ValueError):
                source_info = None
        source_title = source_info.title if source_info else None
        source_username = source_info.username if source_info else None

        forwarded = getattr(message, "forward", None) or getattr(message, "fwd_from", None)
        forwarded_peer = None
        if forwarded is not None:
            forwarded_peer = getattr(forwarded, "from_id", None) or getattr(
                forwarded, "saved_from_peer", None
            )
        forwarded_chat_id = _channel_id_from_peer(forwarded_peer)
        forwarded_message_id = None
        forwarded_title = None
        forwarded_username = None
        if forwarded is not None:
            raw_forwarded_message_id = getattr(forwarded, "channel_post", None) or getattr(
                forwarded, "saved_from_msg_id", None
            )
            if raw_forwarded_message_id is not None:
                try:
                    forwarded_message_id = int(raw_forwarded_message_id)
                except (TypeError, ValueError):
                    forwarded_message_id = None
            raw_forwarded_title = getattr(forwarded, "from_name", None)
            forwarded_title = str(raw_forwarded_title).strip() if raw_forwarded_title else None
        if forwarded_chat_id is not None:
            forwarded_entity = getattr(forwarded, "chat", None) or self._channel_entities.get(
                forwarded_chat_id
            )
            try:
                forwarded_info = self._channel_info(forwarded_entity)
            except (TypeError, ValueError):
                forwarded_info = None
            if forwarded_info is not None:
                forwarded_title = forwarded_info.title
                forwarded_username = forwarded_info.username
        source = ResourceSource(
            channel_id=numeric_chat_id,
            message_id=int(message_id),
            channel_title=source_title,
            channel_username=source_username,
            message_link=_message_link(numeric_chat_id, int(message_id), source_username),
            forwarded_from_channel_id=forwarded_chat_id,
            forwarded_from_message_id=forwarded_message_id,
            forwarded_from_title=forwarded_title,
            forwarded_from_username=forwarded_username,
            forwarded_from_link=(
                _message_link(forwarded_chat_id, forwarded_message_id, forwarded_username)
                if forwarded_chat_id is not None and forwarded_message_id is not None
                else None
            ),
        )
        return MediaResource(
            provider="telegram",
            locator=TelegramLocator(self.account, numeric_chat_id, int(message_id)).encode(),
            file_name=file_name,
            file_size=file_size,
            identity_confidence=confidence,
            episode_range=episode.value if episode else None,
            episode_conflict=episode_conflict,
            quality=_quality(evidence),
            source=source,
        )

    def _safe_map_message(self, message: Any, query: str, source_info: Any) -> MediaResource | None:
        try:
            return self._map_message(message, query, source_info)
        except (ValueError, TypeError) as error:
            logging.getLogger(__name__).warning(
                "invalid Telegram media skipped: message_id=%s error_type=%s",
                getattr(message, "id", None),
                type(error).__name__,
            )
            return None

    async def scan_channel(
        self, channel: str | int, query: str, min_message_id: int, limit: int = 100
    ) -> ChannelScan:
        await self.connect()
        peer = await self._resolve_channel_peer(channel)
        source_info = await self._source_info(peer)
        resources: list[MediaResource] = []
        last_message_id = min_message_id
        async for message in self.client.iter_messages(
            peer,
            search=query,
            min_id=min_message_id,
            reverse=True,
            limit=limit,
        ):
            message_id = int(getattr(message, "id", 0) or 0)
            last_message_id = max(last_message_id, message_id)
            resource = self._safe_map_message(message, query, source_info)
            if resource is not None:
                resources.append(resource)
        return ChannelScan(resources, last_message_id)

    async def source(self, locator: TelegramLocator) -> TelethonDownloadSource:
        if locator.account != self.account:
            raise ValueError("Telegram locator belongs to a different account")
        await self.connect()
        message = await self.client.get_messages(locator.chat_id, ids=locator.message_id)
        if message is None or getattr(message, "media", None) is None:
            raise TelegramResourceMissing(locator.encode())
        return TelethonDownloadSource(self.client, message.media)

    async def download_source(self, resource: MediaResource) -> TelethonDownloadSource:
        if resource.provider != "telegram":
            raise ValueError(f"unsupported resource provider: {resource.provider}")
        return await self.source(TelegramLocator.decode(resource.locator))


class TelethonDownloadSource:
    def __init__(self, client: Any, media: Any) -> None:
        self.client = client
        self.media = media

    async def chunks(self, offset: int, chunk_size: int) -> AsyncIterator[bytes]:
        async for chunk in self.client.iter_download(
            self.media,
            offset=offset,
            chunk_size=chunk_size,
            request_size=chunk_size,
        ):
            yield bytes(chunk)
