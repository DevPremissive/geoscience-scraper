#!/usr/bin/env python3
"""
schema.py — the dossier data model (C4.1).

A dossier is a versioned JSON document. Two rules from PLAN_C4 4.1 are enforced
here in the types rather than left to the assembler's discipline:

  **A value without provenance fails assembly.** Every fact carries
  `(source_id, snapshot_date)`. This is a hard error by design — Master §4's
  versioning rule says nothing is reported without provenance, and a dossier is
  the document that goes in front of a decision to spend money.

  **Missing data is stated, never silent.** A section with no upstream data
  renders an explicit `NOT AVAILABLE — <reason>` block. Silence in a dossier
  reads as "nothing there", which is a different claim from "we did not look".

No dossier auto-advances past `draft`.
"""
from __future__ import annotations
from typing import Any, Literal
import datetime as dt

from pydantic import BaseModel, Field, field_validator

Status = Literal["draft", "reviewed", "approved", "rejected", "sent"]


class Provenance(BaseModel):
    """Where a value came from and when. Required on every reported fact."""
    source_id: str
    snapshot_date: str

    @field_validator("source_id", "snapshot_date")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not str(v).strip():
            raise ValueError("provenance fields cannot be empty — a value "
                             "without provenance must not reach a dossier")
        return str(v)


class Fact(BaseModel):
    """One reported value, inseparable from its provenance."""
    label: str
    value: Any
    provenance: Provenance
    unit: str | None = None
    note: str | None = None


class Section(BaseModel):
    """One dossier section. Either it has facts, or it says why it does not."""
    key: str
    title: str
    available: bool = True
    unavailable_reason: str | None = None
    facts: list[Fact] = Field(default_factory=list)
    tables: dict[str, list[dict]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @field_validator("unavailable_reason")
    @classmethod
    def _reason_required(cls, v, info):
        return v

    def model_post_init(self, _ctx) -> None:
        if not self.available and not self.unavailable_reason:
            raise ValueError(
                f"section {self.key!r} is unavailable but gives no reason; "
                f"PLAN_C4 4.1 requires an explicit 'NOT AVAILABLE — <reason>' "
                f"block, never silence")


class Signoff(BaseModel):
    by: str | None = None
    date: str | None = None
    decision: str | None = None


class Dossier(BaseModel):
    target_id: str
    dossier_version: str
    fabric_version: str
    feature_snapshot: str
    jurisdiction: str
    generated_at: str = Field(
        default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    status: Status = "draft"
    reviewer_notes: list[str] = Field(default_factory=list)
    signoff: Signoff = Field(default_factory=Signoff)
    sections: list[Section] = Field(default_factory=list)
    licence_gates: list[str] = Field(default_factory=list)

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)

    def advance(self, status: Status, by: str, decision: str):
        """Move past draft. Requires a named human — nothing auto-advances."""
        if status != "draft" and not by:
            raise ValueError("advancing a dossier requires a named reviewer")
        self.status = status
        self.signoff = Signoff(by=by, date=dt.date.today().isoformat(),
                               decision=decision)
        return self
