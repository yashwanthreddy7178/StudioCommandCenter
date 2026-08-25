"""Firestore state and event streaming persistence layer."""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional
from services.common.models import EvidencePayload, RunDocument, StepEvent
from services.common.telemetry import setup_logging

logger = setup_logging("agent-worker-store")


class RunStore:
    """Manages run documents, step event streams, and evidence payloads."""

    def __init__(self) -> None:
        self._runs: Dict[str, RunDocument] = {}
        self._events: Dict[str, List[StepEvent]] = {}
        self._evidence: Dict[str, EvidencePayload] = {}
        self._lock = asyncio.Lock()

    async def save_run(self, run: RunDocument) -> None:
        """Saves or updates a run document."""
        async with self._lock:
            self._runs[run.run_id] = run
            if run.run_id not in self._events:
                self._events[run.run_id] = []

    async def get_run(self, run_id: str) -> Optional[RunDocument]:
        """Retrieves a run document by ID."""
        async with self._lock:
            return self._runs.get(run_id)

    async def emit_event(self, event: StepEvent) -> None:
        """Appends a step event to the run's event stream."""
        async with self._lock:
            if event.run_id not in self._events:
                self._events[event.run_id] = []
            self._events[event.run_id].append(event)
            logger.info("Emitted run step event", extra={"run_id": event.run_id, "seq": event.seq, "type": event.event_type.value})

    async def record_evidence(self, evidence: EvidencePayload) -> None:
        """Stores a raw evidence payload."""
        async with self._lock:
            self._evidence[evidence.evidence_id] = evidence

    async def get_events(self, run_id: str, since_seq: int = 0) -> List[StepEvent]:
        """Returns events for a run since sequence number."""
        async with self._lock:
            events = self._events.get(run_id, [])
            return [e for e in events if e.seq > since_seq]

    async def get_evidence(self, evidence_id: str) -> Optional[EvidencePayload]:
        """Retrieves raw evidence payload by ID."""
        async with self._lock:
            return self._evidence.get(evidence_id)


store = RunStore()
