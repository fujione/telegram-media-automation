"""Public API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from telegram_media_automation.domain.states import TaskState


class WebAuthRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=10, max_length=1024, repr=False)


class WebSetupRequest(WebAuthRequest):
    setup_code: str = Field(default="", max_length=256, repr=False)
    telegram_api_id: int | None = Field(default=None, gt=0, repr=False)
    telegram_api_hash: str | None = Field(default=None, min_length=1, max_length=256, repr=False)
    proxy_url: str | None = Field(default=None, max_length=2000, repr=False)
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    timezone: str = Field(default="Asia/Shanghai", max_length=128)


class WebAuthSetupResponse(BaseModel):
    setup_required: bool
    local_setup_allowed: bool
    bootstrap_required: bool = False


class WebAuthSessionResponse(BaseModel):
    authenticated: bool
    username: str
    restart_required: bool = False


class UiConfigResponse(BaseModel):
    language: str
    timezone: str


class ArchivePlanCreate(BaseModel):
    kind: str = Field(pattern=r"^(movie|series)$")
    title: str = Field(min_length=1, max_length=300)
    year: int | None = Field(default=None, ge=1870, le=2200)
    season: int | None = Field(default=None, ge=0, le=999)
    episode: int | None = Field(default=None, ge=1, le=100000)
    episode_end: int | None = Field(default=None, ge=1, le=100000)
    confirmed_identity: bool = False
    replace_existing: bool = False
    target_path: str | None = Field(default=None, min_length=1, max_length=1000)


class TaskCreate(BaseModel):
    resource_id: UUID
    request_id: UUID | None = None
    archive_plan: ArchivePlanCreate | None = None


class ArchivePlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    title: str
    year: int | None
    season: int | None
    episode: int | None
    episode_end: int | None
    confirmed_identity: bool
    replace_existing: bool
    target_path: str | None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    request_id: UUID
    resource_id: UUID
    state: TaskState
    desired_state: TaskState | None
    expected_size: int
    downloaded_bytes: int
    attempt_count: int
    version: int
    error_code: str | None
    error_message: str | None
    output_path: str | None
    next_attempt_at: datetime | None
    archive_plan: ArchivePlanResponse | None
    created_at: datetime
    updated_at: datetime


class TaskPageResponse(BaseModel):
    items: list[TaskResponse]
    total: int
    offset: int
    limit: int


class TaskSummaryResponse(BaseModel):
    total: int
    active: int
    attention: int
    completed: int


class TaskAction(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class TaskEventResponse(BaseModel):
    id: UUID
    task_id: UUID
    event_type: str
    previous_state: TaskState | None
    new_state: TaskState
    reason: str | None
    actor: str
    correlation_id: str | None
    occurred_at: datetime


class SearchCreate(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    channels: list[str] = Field(default_factory=list, max_length=100)
    limit: int = Field(default=100, ge=1, le=500)
    resolutions: list[Annotated[int, Field(gt=0, le=16384)]] = Field(
        default_factory=list, max_length=10
    )
    languages: list[Annotated[str, Field(min_length=1, max_length=16)]] = Field(
        default_factory=list, max_length=20
    )
    max_file_size: int | None = Field(default=None, gt=0)
    prefer_hdr: bool | None = None


class QualitySettingsResponse(BaseModel):
    preferred_resolutions: tuple[int, ...]
    allowed_resolutions: tuple[int, ...]
    preferred_languages: tuple[str, ...]
    allowed_languages: tuple[str, ...]
    prefer_hdr: bool
    max_file_size: int | None


class MediaSuggestionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class MediaSuggestionResponse(BaseModel):
    title: str
    year: int | None
    kind: str
    season: int | None
    episode: int | None
    episode_end: int | None


class EpisodeRangeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    season: int
    start: int
    end: int


class ResourceQualityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resolution: int | None
    codec: str | None
    hdr: bool
    languages: tuple[str, ...]


class ResourceSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    channel_id: int
    message_id: int
    channel_title: str | None
    channel_username: str | None
    message_link: str | None
    forwarded_from_channel_id: int | None
    forwarded_from_message_id: int | None
    forwarded_from_title: str | None
    forwarded_from_username: str | None
    forwarded_from_link: str | None


class ResourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    file_name: str
    file_size: int
    identity_confidence: int
    episode_range: EpisodeRangeResponse | None
    episode_conflict: bool
    quality: ResourceQualityResponse
    source: ResourceSourceResponse | None


class TelegramCodeRequest(BaseModel):
    phone: str = Field(min_length=5, max_length=40, pattern=r"^[+0-9 ()-]+$")


class TelegramCodeVerify(BaseModel):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[0-9]+$")


class TelegramPasswordVerify(BaseModel):
    password: str = Field(min_length=1, max_length=300)


class TelegramAuthResponse(BaseModel):
    status: str
    authorized: bool


class SubscriptionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    season: int = Field(default=1, ge=0, le=999)
    next_episode: int = Field(default=1, ge=1, le=100000)
    pending_episodes: list[int] = Field(default_factory=list, max_length=500)
    continue_tracking: bool = True
    query: str = Field(min_length=1, max_length=300)
    channels: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_episode_plan(self) -> SubscriptionCreate:
        if any(episode <= 0 or episode > 100000 for episode in self.pending_episodes):
            raise ValueError("pending episodes must be between 1 and 100000")
        if not self.continue_tracking and not self.pending_episodes:
            raise ValueError("fill-only subscriptions require pending episodes")
        return self


class SubscriptionUpdate(SubscriptionCreate):
    expected_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_episode_plan(self) -> SubscriptionUpdate:
        # The route validates against the persisted enabled state. Completed,
        # paused fill-only plans may be edited without inventing missing episodes.
        return self


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    season: int
    next_episode: int
    pending_episodes: tuple[int, ...]
    continue_tracking: bool
    query: str
    channels: tuple[str, ...]
    enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime
    scan_status: dict[str, Any] | None = None
    scan_health: dict[str, Any] = Field(default_factory=dict)


class ImportCreate(BaseModel):
    kind: str = Field(pattern=r"^(movie|series)$")
    title: str = Field(min_length=1, max_length=300)
    year: int | None = Field(default=None, ge=1870, le=2200)
    season: int | None = Field(default=None, ge=0, le=999)
    episode: int | None = Field(default=None, ge=1, le=100000)
    episode_end: int | None = Field(default=None, ge=1, le=100000)
    confirmed_identity: bool = False


class ReplaceCreate(ImportCreate):
    target_path: str | None = Field(default=None, min_length=1, max_length=1000)


class LibraryFileResponse(BaseModel):
    path: str
    file_name: str
    file_size: int


class DirectoryStatusResponse(BaseModel):
    path: str
    exists: bool
    writable: bool
    free_bytes: int | None


class SystemStatusResponse(BaseModel):
    setup_restart_required: bool = False
    version: str
    environment: str
    language: str
    timezone: str
    archive_mode: str
    schema_version: int
    workers_running: bool
    maintenance_running: bool
    subscription_poll_interval: float
    telegram_configured: bool
    directories: dict[str, DirectoryStatusResponse]


class SearchChannelCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    identifier: str = Field(min_length=1, max_length=300)
    name: str | None = Field(default=None, max_length=300)


class SearchChannelInspectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    identifier: str = Field(min_length=1, max_length=300)


class SearchChannelInspectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    identifier: str
    title: str
    visibility: str = Field(pattern=r"^(public|private)$")
    username: str | None
    chat_id: int


class SearchChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    identifier: str
    name: str | None
    enabled: bool
    created_at: datetime


class BackupResponse(BaseModel):
    name: str
    size: int
    created_at: datetime
