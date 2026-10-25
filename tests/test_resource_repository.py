from pathlib import Path

from telegram_media_automation.domain.resources import (
    EpisodeRange,
    MediaResource,
    ResourceQuality,
    ResourceSource,
)
from telegram_media_automation.infrastructure.database import Database
from telegram_media_automation.infrastructure.resource_repository import SqlResourceRepository


def test_upsert_uses_provider_locator_as_stable_identity(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'resources.sqlite3'}")
    database.create_schema_for_development()
    repository = SqlResourceRepository(database)
    first = MediaResource(
        provider="telegram",
        locator="message-1",
        file_name="old.mkv",
        file_size=10,
        identity_confidence=60,
    )
    stored = repository.upsert_many([first])[0]
    changed = MediaResource(
        provider="telegram",
        locator="message-1",
        file_name="new.mkv",
        file_size=11,
        identity_confidence=90,
        episode_range=EpisodeRange(1, 2, 2),
        quality=ResourceQuality(resolution=1080, languages=("zh",)),
        source=ResourceSource(
            channel_id=-100123,
            message_id=9,
            channel_title="Source Channel",
            channel_username="source_channel",
            message_link="https://t.me/source_channel/9",
            forwarded_from_channel_id=-100456,
            forwarded_from_message_id=8,
            forwarded_from_title="Original Channel",
            forwarded_from_link="https://t.me/c/456/8",
        ),
    )

    updated = repository.upsert_many([changed])[0]

    assert updated.id == stored.id
    assert updated.file_name == "new.mkv"
    assert updated.episode_range == EpisodeRange(1, 2, 2)
    assert updated.source == changed.source
    assert repository.get(stored.id) == updated
    assert repository.list() == [updated]
