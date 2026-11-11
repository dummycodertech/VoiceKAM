"""In-memory per-session state with SellerContext, TTL expiry, and bounded LRU eviction."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from app.data.context import SellerContext, SnapshotType
from app.data.seed_data import get_scenario_context
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
    seller_context: SellerContext = field(default_factory=get_scenario_context)
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
    created_at: float = field(default_factory=time.monotonic)
    last_accessed: float = field(default_factory=time.monotonic)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "mode": self.mode,
            "snapshot": self.snapshot,
            "language": self.language,
            "phase": self.phase.value,
            "turn_count": self.turn_count,
            "clarify_questions_asked": self.clarify_questions_asked,
            "dataset": self.seller_context.summary_dict(),
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
    """In-memory bounded session registry with TTL expiry and LRU eviction (no disk/DB)."""

    def __init__(self, ttl_seconds: int = 7200, max_sessions: int = 200) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_sessions = max(10, int(max_sessions))
        self._sessions: OrderedDict[str, SessionState] = OrderedDict()

    def _evict_expired_and_overflow(self, now: float | None = None) -> None:
        ts = time.monotonic() if now is None else now
        expired = [k for k, v in self._sessions.items() if (ts - v.last_accessed) > self.ttl_seconds]
        for k in expired:
            self._sessions.pop(k, None)
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)

    def create(
        self,
        mode: CallMode = "inbound",
        language: LanguageCode = "en-IN",
        prior_session_id: str | None = None,
        seller_context: SellerContext | None = None,
    ) -> SessionState:
        now = time.monotonic()
        self._evict_expired_and_overflow(now)

        session_id = uuid.uuid4().hex[:12]
        snapshot: SnapshotType = "followup" if mode == "followup" else "current"
        prior_commitments: list[dict[str, Any]] = []

        resolved_context = seller_context
        prev = self._sessions.get(prior_session_id) if prior_session_id else None
        if prev is not None and resolved_context is None:
            resolved_context = prev.seller_context
        if resolved_context is None:
            resolved_context = get_scenario_context()

        if mode == "followup":
            if prev is not None and prev.commitments:
                prior_commitments = [{**c, "status": "partially_done"} for c in prev.commitments]
            elif resolved_context.followup and resolved_context.followup.default_prior_commitments:
                prior_commitments = [
                    dict(item) for item in resolved_context.followup.default_prior_commitments
                ]

        state = SessionState(
            session_id=session_id,
            seller_context=resolved_context,
            mode=mode,
            snapshot=snapshot,
            language=language,
            phase=Phase.GREETING,
            prior_commitments=prior_commitments,
            created_at=now,
            last_accessed=now,
        )
        self._sessions[session_id] = state
        self._evict_expired_and_overflow(now)
        return state

    def get(self, session_id: str) -> SessionState | None:
        now = time.monotonic()
        self._evict_expired_and_overflow(now)
        state = self._sessions.get(session_id)
        if state is None:
            return None
        state.last_accessed = now
        self._sessions.move_to_end(session_id)
        return state

    def __len__(self) -> int:
        self._evict_expired_and_overflow()
        return len(self._sessions)
