"""Idempotent remediation action execution engine."""
from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Any, Dict, Optional
import httpx
from src.config import settings
from src.audit import audit_store
from src.grafana_writeback import grafana_writeback
from services.common.models import ActionType, ApprovalRecord, AuditRecord
from services.common.telemetry import setup_logging

logger = setup_logging("action-executor-engine")


class ActionExecutionEngine:
    """Executes human-approved remediation actions idempotently against render control plane."""

    def __init__(self) -> None:
        self._http_client = httpx.AsyncClient(timeout=10.0)

    async def execute_approved_action(
        self,
        run_id: str,
        option_id: str,
        tenant_id: str,
        user_id: str,
        action_type: ActionType,
        parameters: Dict[str, Any],
        option_title: str = "",
        production_consequence: str = "",
        at_risk_deliverables: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Idempotently executes approved remediation and records it in Grafana."""
        idempotency_key = hashlib.sha256(f"{run_id}:{option_id}".encode("utf-8")).hexdigest()

        # 1. Idempotency Check
        existing_approval = await audit_store.get_approval(idempotency_key)
        if existing_approval:
            logger.info("Idempotent duplicate approval detected, returning cached result", extra={"key": idempotency_key})
            return {
                "status": "ALREADY_APPLIED",
                "idempotency_key": idempotency_key,
                "action_type": action_type.value,
                "result": existing_approval.executor_result,
            }

        # 2. Execute on Render Control Plane
        logger.info(
            "Executing approved control action",
            extra={"action": action_type.value, "tenant_id": tenant_id, "user_id": user_id, "run_id": run_id}
        )

        control_url = f"{settings.render_sim_url}/control/apply"
        control_payload = {
            "tenant_id": tenant_id,
            "action_type": action_type.value,
            "parameters": parameters,
            "run_id": run_id,
        }

        try:
            res = await self._http_client.post(control_url, json=control_payload)
            res.raise_for_status()
            executor_result = res.json()
            status_str = "SUCCESS"
            msg = executor_result.get("message", "Action executed successfully")
        except Exception as exc:
            logger.error("Failed to execute control action", extra={"error": str(exc)})
            executor_result = {"error": str(exc)}
            status_str = "FAILED"
            msg = f"Control plane execution failed: {str(exc)}"

        # 3. Write the outcome back into Grafana.
        #
        # Reading Grafana and then acting silently leaves no trace where the
        # humans who own the stack are actually looking. This is the other half of
        # the loop: the approved change is marked on the timeline, and a
        # deliverable at risk opens an incident carrying the same summary.
        #
        # Best-effort by construction. The control plane has already been changed
        # by this point, so a Grafana failure is recorded and reported but never
        # turns a successful rollback into a failed one.
        writeback: Dict[str, Any] = {"enabled": settings.grafana_writeback_enabled}
        if settings.grafana_writeback_enabled and status_str == "SUCCESS":
            summary = production_consequence or msg
            writeback["annotation"] = await grafana_writeback.annotate_remediation(
                tenant_id=tenant_id,
                run_id=run_id,
                approval_id=idempotency_key,
                user_id=user_id,
                action_type=action_type.value,
                summary=summary,
            )
            if at_risk_deliverables and settings.grafana_incident_enabled:
                writeback["incident"] = await grafana_writeback.open_incident(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    approval_id=idempotency_key,
                    title=(
                        option_title
                        or f"Render regression on {tenant_id}: {action_type.value}"
                    ),
                    summary=(
                        f"{summary} At risk: {', '.join(at_risk_deliverables)}."
                    ),
                )
            executor_result = {**executor_result, "grafana_writeback": writeback}

        # 4. Save Approval and Audit Records
        approval_record = ApprovalRecord(
            idempotency_key=idempotency_key,
            run_id=run_id,
            option_id=option_id,
            tenant_id=tenant_id,
            user_id=user_id,
            action_type=action_type,
            parameters=parameters,
            executor_status=status_str,
            executor_result=executor_result,
        )
        await audit_store.save_approval(approval_record)

        audit_record = AuditRecord(
            audit_id=f"aud-{int(time.time()*1000)}",
            idempotency_key=idempotency_key,
            run_id=run_id,
            tenant_id=tenant_id,
            user_id=user_id,
            action_type=action_type,
            parameters=parameters,
            status=status_str,
            message=msg,
        )
        await audit_store.record_audit(audit_record)

        return {
            "status": status_str,
            "idempotency_key": idempotency_key,
            "action_type": action_type.value,
            "result": executor_result,
            "message": msg,
        }

    async def close(self) -> None:
        await self._http_client.aclose()


action_engine = ActionExecutionEngine()
