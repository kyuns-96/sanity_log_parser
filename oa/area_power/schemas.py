"""Pydantic input schemas and shared request helpers for OA Area/Power APIs."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from common.area_power.errors import DomainError
from common.area_power.models import email_address

CONFIG_TYPES = {"project", "version", "block", "target", "mapping", "change"}
SCOPE_FIELDS = ("project_id", "version_id", "block_id", "snapshot_id")


def reject(message: str, code: str = "validation_error", status: int = 422):
    raise DomainError(message, code=code, status_code=status)


def addresses(values: list[str] | tuple[str, ...]) -> list[str]:
    result = []
    for value in values:
        try:
            if not isinstance(value, str) or email_address(value) != value:
                raise ValueError
        except (ValueError, TypeError, IndexError):
            reject("Recipients must be plain email addresses.", "invalid_recipient")
        if value not in result:
            result.append(value)
    return result


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigInput(Input):
    record_type: Literal["target", "mapping", "change"]
    entity_id: UUID | None = None
    expected_row_version: int | None = Field(default=None, ge=1)
    project_id: UUID | None = None
    version_id: UUID | None = None
    block_id: UUID | None = None
    snapshot_id: UUID | None = None
    payload: dict[str, Any]


class Binding(Input):
    snapshot_id: UUID
    paths: list[str] = Field(min_length=1)


class TargetPayload(Input):
    name: str = Field(min_length=1, max_length=200)
    paths: list[str] = Field(default_factory=list)
    snapshot_id: UUID | None = None
    scope: Literal["node", "subtree"] = "node"
    metrics: list[str] = Field(default_factory=list)
    bindings: list[Binding] = Field(default_factory=list)
    watch: bool = False

    @field_validator("paths", "metrics")
    @classmethod
    def unique_nonempty(cls, values):
        if any(not value.strip() for value in values) or len(values) != len(set(values)):
            raise ValueError("Values must be nonempty and unique")
        return values


class ChangePayload(Input):
    text: str = Field(min_length=1)
    applies_from_version_id: UUID | None = None
    applies_through_version_id: UUID | None = None
    baseline_project_id: UUID | None = None
    target_ids: list[UUID] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    expected: list[dict[str, Any]] = Field(default_factory=list)
    extraction_evidence: list[dict[str, Any]] = Field(default_factory=list)
    note_kind: Literal["net_ver", "project_change"] | None = None
    net_ver: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool = False


class BaselineInput(Input):
    baseline_project_id: UUID | None
    expected_row_version: int = Field(ge=1)


class ProcessInput(Input):
    process: str | None = Field(default=None, max_length=100)
    expected_row_version: int = Field(ge=1)

    @field_validator("process")
    @classmethod
    def single_line(cls, value):
        value = value.strip() if value else None
        if value and any(char in value for char in "\x00\r\n"):
            raise ValueError("A single-line process name is required")
        return value or None


class SelectionInput(Input):
    upload_id: UUID
    selection_scope: Literal["shared"] = "shared"
    expected_row_version: int | None = Field(default=None, ge=1)


class RejudgeInput(Input):
    change_ids: list[UUID] = Field(default_factory=list)
    recipients: list[str] = Field(default_factory=list)


class AlertStateInput(Input):
    action: Literal["read", "acknowledge", "resolve"]
    expected_row_version: int = Field(ge=1)
    note: str | None = None


class RerunInput(Input):
    expected_row_version: int | None = Field(default=None, ge=1)


class UploadDeleteInput(Input):
    force: bool = False
    reason: str = Field(default="", max_length=500)
