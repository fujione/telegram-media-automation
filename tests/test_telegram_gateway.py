from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from telegram_media_automation.infrastructure.telegram_gateway import (
    TelegramChannelUnavailable,
    TelegramLocator,
    TelethonGateway,
)


class FakeClient:
    def __init__(self, _session: Path, _api_id: int, _api_hash: str, **_options: object) -> None:
        self.connected = False
        self.download_offsets: list[int] = []
        self.messages = [
            SimpleNamespace(
                id=7,
                chat_id=-1001,
                chat=SimpleNamespace(
                    id=1,
                    title="Download Channel",
                    username="download_channel",
                    broadcast=True,
                    megagroup=False,
                ),
                forward=SimpleNamespace(
                    from_id=SimpleNamespace(channel_id=2),
                    channel_post=6,
                    from_name="Original Channel",
                    chat=SimpleNamespace(
                        id=2,
                        title="Original Channel",
                        username="original_channel",
                        broadcast=True,
                        megagroup=False,
                    ),
                ),
                media=object(),
                message="A Show 第2集 中文字幕",
                file=SimpleNamespace(name="A.Show.S01E02.1080p.HEVC.mkv", size=12),
            ),
            SimpleNamespace(id=8, chat_id=-1001, media=None, message="plain text", file=None),
            SimpleNamespace(
                id=10,
                chat_id=-1001,
                media=object(),
                message="A Show attachment",
                file=SimpleNamespace(name="A.Show.zip", size=12, mime_type="application/zip"),
            ),
        ]

    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self) -> Any:
        return SimpleNamespace(id=123)

    def iter_messages(self, _channel: str | int, **_kwargs: object) -> AsyncIterator[Any]:
        async def values() -> AsyncIterator[Any]:
            for message in self.messages:
                yield message

        return values()

    async def get_messages(self, _chat_id: int, *, ids: int) -> Any:
        return next((message for message in self.messages if message.id == ids), None)

    async def get_entity(self, channel: str | int) -> Any:
        if channel == "public_channel":
            return SimpleNamespace(
                id=123456,
                title="Public Channel",
                username="public_channel",
                broadcast=True,
                megagroup=False,
            )
        return SimpleNamespace(
            id=987654,
            title="Private Channel",
            username=None,
            broadcast=False,
            megagroup=True,
        )

    def iter_dialogs(self, *, limit: int) -> AsyncIterator[Any]:
        async def values() -> AsyncIterator[Any]:
            assert limit > 0
            yield SimpleNamespace(entity=await self.get_entity("public_channel"))
            yield SimpleNamespace(entity=await self.get_entity(-100987654))
            yield SimpleNamespace(entity=SimpleNamespace(id=42, title="Direct message"))

        return values()

    def iter_download(self, _media: object, **kwargs: int) -> AsyncIterator[bytes]:
        async def values() -> AsyncIterator[bytes]:
            self.download_offsets.append(kwargs["offset"])
            yield b"payload"

        return values()


def gateway(tmp_path: Path) -> TelethonGateway:
    return TelethonGateway(tmp_path / "tg.session", 123, "hash", client_factory=FakeClient)


@pytest.mark.asyncio
async def test_authorization_checks_rpc_even_when_cached_authorization_is_true(
    tmp_path: Path,
) -> None:
    client = gateway(tmp_path)
    assert await client.is_authorized() is True

    async def offline() -> Any:
        raise ConnectionResetError("provider connection lost")

    client.client.get_me = offline
    with pytest.raises(ConnectionResetError):
        await client.is_authorized()


@pytest.mark.asyncio
async def test_authorization_reports_login_required_for_unauthorized_rpc(tmp_path: Path) -> None:
    client = gateway(tmp_path)

    async def unauthenticated() -> None:
        return None

    client.client.get_me = unauthenticated
    assert await client.is_authorized() is False


def test_proxy_is_passed_to_client_factory_without_exposing_credentials(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def factory(session: Path, api_id: int, api_hash: str, **kwargs: object) -> FakeClient:
        captured.update(kwargs)
        return FakeClient(session, api_id, api_hash)

    proxy = ("socks5", "proxy.example", 1080, True, "user", "password")
    TelethonGateway(tmp_path / "proxy.session", 123, "hash", client_factory=factory, proxy=proxy)

    assert captured == {"proxy": proxy, "timeout": 8, "connection_retries": 1}


def test_locator_is_stable_and_validated() -> None:
    locator = TelegramLocator("default", -1001, 7)
    assert TelegramLocator.decode(locator.encode()) == locator
    with pytest.raises(ValueError):
        TelegramLocator.decode('{"account":"","chat_id":0,"message_id":0}')


@pytest.mark.asyncio
async def test_search_maps_only_downloadable_media(tmp_path: Path) -> None:
    client = gateway(tmp_path)

    resources = await client.search(["channel"], "A Show")

    assert len(resources) == 1
    resource = resources[0]
    assert resource.file_size == 12
    assert resource.episode_range is not None and resource.episode_range.start == 2
    assert resource.quality.resolution == 1080
    assert resource.quality.codec == "hevc"
    assert resource.quality.languages == ("zh",)
    assert resource.source is not None
    assert resource.source.channel_id == -1001
    assert resource.source.channel_title == "Download Channel"
    assert resource.source.message_link == "https://t.me/download_channel/7"
    assert resource.source.forwarded_from_channel_id == -1002
    assert resource.source.forwarded_from_message_id == 6
    assert resource.source.forwarded_from_title == "Original Channel"
    assert resource.source.forwarded_from_username == "original_channel"
    assert resource.source.forwarded_from_link == "https://t.me/original_channel/6"


@pytest.mark.asyncio
async def test_channel_inspection_uses_username_to_classify_visibility(tmp_path: Path) -> None:
    client = gateway(tmp_path)

    public = await client.inspect_channel("public_channel")
    private = await client.inspect_channel(-100987654)

    assert public.identifier == "public_channel"
    assert public.visibility == "public"
    assert public.chat_id == -100123456
    assert private.identifier == "-100987654"
    assert private.visibility == "private"
    assert private.username is None


@pytest.mark.asyncio
async def test_channel_discovery_lists_private_first_and_skips_non_channels(tmp_path: Path) -> None:
    client = gateway(tmp_path)

    channels = await client.list_channels()

    assert [channel.visibility for channel in channels] == ["private", "public"]
    assert [channel.title for channel in channels] == ["Private Channel", "Public Channel"]
    with pytest.raises(ValueError, match="limit"):
        await client.list_channels(0)


@pytest.mark.asyncio
async def test_private_numeric_channel_falls_back_to_joined_dialog_entity(tmp_path: Path) -> None:
    class UncachedPrivateClient(FakeClient):
        resolved_channels: list[object]

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            self.resolved_channels = []

        async def get_input_entity(self, _channel: int) -> object:
            raise IndexError("Telethon returned an empty channel list")

        def iter_messages(self, channel: object, **kwargs: object) -> AsyncIterator[Any]:
            self.resolved_channels.append(channel)
            return super().iter_messages("resolved", **kwargs)

    client = TelethonGateway(
        tmp_path / "uncached.session",
        123,
        "hash",
        client_factory=UncachedPrivateClient,
    )

    resources = await client.search([-100987654], "A Show")

    assert len(resources) == 1
    resolved = client.client.resolved_channels[-1]
    assert isinstance(resolved, SimpleNamespace)
    assert resolved.title == "Private Channel"


@pytest.mark.asyncio
async def test_unknown_private_numeric_channel_has_stable_domain_error(tmp_path: Path) -> None:
    class UncachedPrivateClient(FakeClient):
        async def get_input_entity(self, _channel: int) -> object:
            raise IndexError("Telethon returned an empty channel list")

    client = TelethonGateway(
        tmp_path / "unknown.session",
        123,
        "hash",
        client_factory=UncachedPrivateClient,
    )

    with pytest.raises(TelegramChannelUnavailable):
        await client.search([-100111111], "A Show")


@pytest.mark.asyncio
async def test_filename_caption_episode_conflict_is_explicit(tmp_path: Path) -> None:
    client = gateway(tmp_path)
    client.client.messages.append(
        SimpleNamespace(
            id=9,
            chat_id=-1001,
            media=object(),
            message="A Show 第4集",
            file=SimpleNamespace(name="A.Show.S01E03.1080p.mkv", size=12),
        )
    )

    resources = await client.search(["channel"], "A Show")
    conflict = next(resource for resource in resources if resource.episode_conflict)

    assert conflict.episode_range is None
    assert conflict.identity_confidence == 40


@pytest.mark.asyncio
async def test_unnamed_native_video_gets_extension_from_mime_type(tmp_path: Path) -> None:
    client = gateway(tmp_path)
    client.client.messages.append(
        SimpleNamespace(
            id=11,
            chat_id=-1001,
            media=object(),
            message="A Show S01E03",
            file=SimpleNamespace(name=None, size=12, mime_type="video/mp4"),
        )
    )

    resources = await client.search(["channel"], "A Show")

    assert any(resource.file_name == "telegram--1001-11.mp4" for resource in resources)


@pytest.mark.asyncio
async def test_message_source_preserves_resume_offset(tmp_path: Path) -> None:
    client = gateway(tmp_path)
    source = await client.source(TelegramLocator("default", -1001, 7))

    chunks = [chunk async for chunk in source.chunks(5, 512 * 1024)]

    assert chunks == [b"payload"]
    assert client.client.download_offsets == [5]


@pytest.mark.asyncio
async def test_subscription_scan_reports_per_channel_cursor(tmp_path: Path) -> None:
    client = gateway(tmp_path)

    scan = await client.scan_channel("channel", "A Show", 5)

    assert scan.last_message_id == 10
    assert len(scan.resources) == 1


@pytest.mark.asyncio
async def test_invalid_media_does_not_poison_search_or_subscription_scan(tmp_path: Path) -> None:
    client = gateway(tmp_path)
    client.client.messages.extend(
        [
            SimpleNamespace(
                id=12,
                chat_id=-1001,
                media=object(),
                message="A Show 第4集",
                file=SimpleNamespace(name="A.Show.S01E00.mp4", size=12),
            ),
            SimpleNamespace(
                id=13,
                chat_id=-1001,
                media=object(),
                message="A Show",
                file=SimpleNamespace(name="A.Show.S01E05.mp4", size="bad-size"),
            ),
            SimpleNamespace(
                id=14,
                chat_id=-1001,
                media=object(),
                message="A Show",
                file=SimpleNamespace(name="A.Show.S01E06.mp4", size=12),
            ),
        ]
    )
    resources = await client.search(["channel"], "A Show")
    assert any(
        resource.episode_range and resource.episode_range.start == 4 for resource in resources
    )
    assert any(
        resource.episode_range and resource.episode_range.start == 6 for resource in resources
    )
    scan = await client.scan_channel("channel", "A Show", 5)
    assert scan.last_message_id == 14
    assert any(
        resource.episode_range and resource.episode_range.start == 6 for resource in scan.resources
    )
