"""Demo quota on investigation runs.

Every run is a chain of Gemini calls billed to the project behind the deployment,
and the service is published with a public link. Nothing else in the stack caps
this: the MCP gateway's token bucket protects Grafana's quota, not the model
spend, and it sits downstream of the decision to start a run at all.

A sliding window rather than a token bucket, because the two questions differ. A
bucket answers "may this proceed right now" and refills continuously, which is
right for smoothing query traffic. Here the question is "how many runs have
happened in the last hour", and the caller deserves a truthful answer about when
they can try again -- which a bucket cannot give without inverting its own
refill maths.

Two limits, because they fail differently. The per-session limit stops one
visitor monopolising the demo; the global limit is what actually protects the
bill when a link is shared widely.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

from src.config import settings
from services.common.telemetry import setup_logging

logger = setup_logging("api-gateway-quota")


@dataclass(frozen=True)
class QuotaExceeded:
    """Why a run was refused, and when it is worth trying again."""
    scope: str          # "session" or "global"
    limit: int
    window_sec: float
    retry_after_sec: int

    @property
    def detail(self) -> str:
        minutes = max(1, round(self.window_sec / 60))
        if self.scope == "session":
            return (
                f"This session has started {self.limit} investigations in the last "
                f"{minutes} minutes, which is the per-session demo limit. "
                f"Try again in {self.retry_after_sec // 60 + 1} minute(s)."
            )
        return (
            f"The demo has run {self.limit} investigations in the last {minutes} "
            "minutes and has reached its shared limit. Every run calls a language "
            f"model, so the ceiling protects the deployment's budget. "
            f"Try again in {self.retry_after_sec // 60 + 1} minute(s)."
        )


class RunQuota:
    """Counts investigation starts in a rolling window, globally and per session."""

    def __init__(
        self,
        per_session: int,
        per_deployment: int,
        window_sec: float,
    ) -> None:
        self.per_session = per_session
        self.per_deployment = per_deployment
        self.window_sec = window_sec
        self._global: Deque[float] = deque()
        self._sessions: Dict[str, Deque[float]] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        """A non-positive limit disables that half; both off disables the quota.

        Local development should not trip a ceiling meant for a public URL.
        """
        return self.per_session > 0 or self.per_deployment > 0

    def _prune(self, stamps: Deque[float], now: float) -> None:
        cutoff = now - self.window_sec
        while stamps and stamps[0] <= cutoff:
            stamps.popleft()

    def _retry_after(self, stamps: Deque[float], now: float) -> int:
        """Seconds until the oldest run leaves the window and a slot frees up."""
        if not stamps:
            return 0
        return max(1, int(stamps[0] + self.window_sec - now) + 1)

    async def reserve(self, session_id: str) -> Optional[QuotaExceeded]:
        """Records a run against the quota, or explains why it cannot start."""
        async with self._lock:
            now = time.time()

            self._prune(self._global, now)
            session = self._sessions.setdefault(session_id, deque())
            self._prune(session, now)

            # Sessions that have aged out entirely are dropped rather than kept
            # forever: the dict is keyed by a browser-generated id, so on a
            # public link it would otherwise grow without bound.
            if not session and session_id in self._sessions:
                empty = [key for key, stamps in self._sessions.items() if not stamps]
                for key in empty:
                    del self._sessions[key]
                session = self._sessions.setdefault(session_id, deque())

            # The session limit is checked first so a single heavy visitor is
            # told it is their own doing rather than blamed on the deployment.
            if 0 < self.per_session <= len(session):
                return QuotaExceeded(
                    scope="session", limit=self.per_session, window_sec=self.window_sec,
                    retry_after_sec=self._retry_after(session, now),
                )

            if 0 < self.per_deployment <= len(self._global):
                return QuotaExceeded(
                    scope="global", limit=self.per_deployment, window_sec=self.window_sec,
                    retry_after_sec=self._retry_after(self._global, now),
                )

            self._global.append(now)
            session.append(now)
            return None

    async def refund(self, session_id: str) -> None:
        """Returns the most recent reservation after a run failed to start.

        A worker that could not be reached never cost anything, so it must not
        consume a slot -- otherwise an outage silently spends the demo's budget
        on runs that never happened.
        """
        async with self._lock:
            if self._global:
                self._global.pop()
            session = self._sessions.get(session_id)
            if session:
                session.pop()

    async def snapshot(self) -> Dict[str, object]:
        """Current usage, for the status endpoint."""
        async with self._lock:
            now = time.time()
            self._prune(self._global, now)
            return {
                "enabled": self.enabled,
                "window_sec": self.window_sec,
                "runs_in_window": len(self._global),
                "limit_per_deployment": self.per_deployment,
                "limit_per_session": self.per_session,
                "retry_after_sec": (
                    self._retry_after(self._global, now)
                    if 0 < self.per_deployment <= len(self._global)
                    else 0
                ),
            }


run_quota = RunQuota(
    per_session=settings.max_runs_per_session,
    per_deployment=settings.max_runs_per_deployment,
    window_sec=settings.run_quota_window_sec,
)
