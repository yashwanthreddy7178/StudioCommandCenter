"""Immutable audit trail and idempotency store for approved remediation actions."""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional
from services.common.models import ApprovalRecord, AuditRecord
from services.common.telemetry import setup_logging

logger = setup_logging("action-executor-audit")


class AuditStore:
    """Manages idempotent approvals and immutable audit records."""

    def __init__(self) -> None:
        self._approvals: Dict[str, ApprovalRecord] = {} # idempotency_key -> ApprovalRecord
        self._audit_log: List[AuditRecord] = []
        self._lock = asyncio.Lock()

    async def get_approval(self, idempotency_key: str) -> Optional[ApprovalRecord]:
        """Checks if this approval idempotency key has already been executed."""
        async with self._lock:
            return self._approvals.get(idempotency_key)

    async def save_approval(self, approval: ApprovalRecord) -> None:
        """Stores executed approval."""
        async with self._lock:
            self._approvals[approval.idempotency_key] = approval

    async def record_audit(self, record: AuditRecord) -> None:
        """Appends an immutable audit record."""
        async with self._lock:
            self._audit_log.append(record)
            logger.info(
                "Recorded action audit",
                extra={
                    "audit_id": record.audit_id,
                    "action": record.action_type.value,
                    "user": record.user_id,
                    "tenant": record.tenant_id,
                }
            )

    async def list_audit(
        self,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List[AuditRecord]:
        """Returns filtered audit records."""
        async with self._lock:
            records = self._audit_log
            if tenant_id:
                records = [r for r in records if r.tenant_id == tenant_id]
            if run_id:
                records = [r for r in records if r.run_id == run_id]
            return records.copy()


audit_store = AuditStore()
