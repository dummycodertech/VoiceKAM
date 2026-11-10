"""In-memory per-session state that survives mid-call LLM provider switches."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from app.data.seed_data import FOLLOWUP_SNAPSHOT, SnapshotType
from app.llm.base import CanonicalMessage


class Phase(str, Enum):
    GREETING = "GREETING"
    UNDERSTAND = "UNDERSTAND"
    CLARIFY = "CLARIFY"
    DIAGNOSE = "DIAGNOSE"
    REVIEW_COMMITMENTS = "REVIEW_COMMITMENTS"
    RECOMMEND = "RECOMMEND"
    COMMIT = "COMMIT"
    SUMMARY = "SUMMARY"
    CLOSE = "CLOSE"


CallMode = Literal["inbound", "proactive", "followup"]
LanguageCode = Literal["en-IN", "hi-IN", "hinglish"]


@dataclass
class SessionState:
    session_id: str
    mode: CallMode = "inbound"
    snapshot: SnapshotType = "current"
    language: LanguageCode = "en-IN"
    phase: Phase = Phase.GREETING
    turn_count: int = 0
    clarify_questions_asked: int = 0
    diagnose_turns: int = 0
    recommend_turns: int = 0
    commit_turns: int = 0
    diagnose_preloaded: bool = False
    recommend_preloaded: bool = False
    messages: list[CanonicalMessage] = field(default_factory=list)
    tool_history: list[dict[str, Any]] = field(default_factory=list)
    grounded_numbers: set[float] = field(default_factory=set)
    commitments: list[dict[str, Any]] = field(default_factory=list)
    prior_commitments: list[dict[str, Any]] = field(default_factory=list)
    followup_schedule: dict[str, Any] | None = None
    summary_card: dict[str, Any] | None = None
    active_provider: str = ""
    failover_events: list[dict[str, str]] = field(default_factory=list)
    latency_history: list[dict[str, Any]] = field(default_factory=list)
    ungrounded_warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "mode": self.mode,
            "snapshot": self.snapshot,
            "language": self.language,
            "phase": self.phase.value,
            "turn_count": self.turn_count,
            "clarify_questions_asked": self.clarify_questions_asked,
            "tool_history": self.tool_history,
            "commitments": self.commitments,
            "prior_commitments": self.prior_commitments,
            "followup_schedule": self.followup_schedule,
            "summary_card": self.summary_card,
            "active_provider": self.active_provider,
            "failover_events": self.failover_events,
            "latency_history": self.latency_history,
            "ungrounded_warnings": self.ungrounded_warnings,
        }


class SessionStore:
    """Thread/async-safe in-memory session registry (no external DB needed)."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def create(
        self,
        mode: CallMode = "inbound",
        language: LanguageCode = "en-IN",
        prior_session_id: str | None = None,
    ) -> SessionState:
        session_id = uuid.uuid4().hex[:12]
        snapshot: SnapshotType = "followup" if mode == "followup" else "current"
        prior_commitments: list[dict[str, Any]] = []

        if mode == "followup":
            if prior_session_id and prior_session_id in self._sessions:
                prev = self._sessions[prior_session_id]
                if prev.commitments:
                    prior_commitments = [
                        {**c, "status": "partially_done"} for c in prev.commitments
                    ]
            if not prior_commitments:
                prior_commitments = [
                    dict(item) for item in FOLLOWUP_SNAPSHOT["default_prior_commitments"]
                ]

        state = SessionState(
            session_id=session_id,
            mode=mode,
            snapshot=snapshot,
            language=language,
            phase=Phase.GREETING,
            prior_commitments=prior_commitments,
        )
        self._sessions[session_id] = state
        return state

    def get(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

