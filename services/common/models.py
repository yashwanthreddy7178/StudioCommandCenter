"""Shared Pydantic domain models across all Studio Production Commander services.

These models define data transfer objects, database document representations,
and cross-service communication payloads.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RunState(str, Enum):
    """Lifecycle states of an autonomous investigation run."""
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class EventType(str, Enum):
    """Types of step events emitted during an investigation."""
    PLAN = "PLAN"
    TOOL_CALL = "TOOL_CALL"
    EVIDENCE = "EVIDENCE"
    HYPOTHESIS = "HYPOTHESIS"
    IMPACT = "IMPACT"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    VERIFICATION = "VERIFICATION"
    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"


class ConfidenceLevel(str, Enum):
    """Confidence level derived from the count of passing falsifiable tests."""
    HIGH = "HIGH"        # 6 of 6 tests pass
    MEDIUM = "MEDIUM"    # 4 to 5 tests pass
    LOW = "LOW"          # <= 3 tests pass


class ActionType(str, Enum):
    """Closed enum of permissible remediation actions on the render control plane."""
    ROLLBACK_RENDERER_CONFIG = "rollback_renderer_config"
    SCALE_RENDER_WORKERS = "scale_render_workers"
    REPRIORITIZE_QUEUE = "reprioritize_queue"
    DRAIN_WORKER = "drain_worker"


class RiskLevel(str, Enum):
    """Risk classification of proposed remediation actions."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TenantStatus(str, Enum):
    """Status of a tenant world in the leasing pool."""
    AVAILABLE = "AVAILABLE"
    LEASED = "LEASED"
    RESETTING = "RESETTING"
    OBSERVER = "OBSERVER"


# ---------------------------------------------------------------------------
# Telemetry & Simulator Models
# ---------------------------------------------------------------------------

class WorkerStatus(BaseModel):
    """State of an individual render worker node."""
    worker_id: str
    tenant_id: str
    renderer_version: str
    gpu_type: str
    gpu_utilization_pct: float
    gpu_memory_used_mb: float
    temperature_celsius: float
    cpu_utilization_pct: float
    memory_used_mb: float
    active_jobs: int
    queue_depth: int
    duration_avg_sec: float
    status: str = "HEALTHY"


class ProductionWorldState(BaseModel):
    """Complete snapshot of a single tenant production world."""
    tenant_id: str
    production_id: str = "prod-shadow-protocol"
    title: str = "Shadow Protocol"
    renderer_version: str = "v2.4.0"
    tile_size: int = 256
    is_incident_active: bool = False
    incident_type: Optional[str] = None
    baseline_throughput_fpm: float = 118.6
    observed_throughput_fpm: float = 118.6
    total_workers: int = 8
    healthy_workers: int = 8
    degraded_workers: int = 0
    queue_depth: int = 18432
    workers: List[WorkerStatus] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Investigation & Evidence Models
# ---------------------------------------------------------------------------

class FalsifiableTestResult(BaseModel):
    """Evaluation of a single falsifiable hypothesis test."""
    test_id: str
    name: str
    description: str
    passed: bool
    evidence_source: str
    evidence_snippet: str
    explanation: str


class HypothesisScorecard(BaseModel):
    """Scorecard derived from 6 falsifiable hypothesis tests."""
    primary_hypothesis: str
    suspected_cause: str
    tests: List[FalsifiableTestResult]
    passed_count: int
    total_tests: int = 6
    confidence: ConfidenceLevel
    missing_evidence_summary: Optional[str] = None


class RemediationOption(BaseModel):
    """Ranked remediation option presented for human approval."""
    option_id: str
    action_type: ActionType
    title: str
    description: str
    parameters: Dict[str, Any]
    estimated_recovery_minutes: int
    risk_level: RiskLevel
    production_consequence: str


class ImpactProjection(BaseModel):
    """Deterministic delivery-deadline impact calculation from impact-engine."""
    tenant_id: str
    affected_shots: int
    high_priority_shots: int
    sequences: List[str]
    deadline_utc: datetime
    projected_completion_utc: datetime
    delay_minutes: int
    at_risk_deliverables: List[str]
    baseline_throughput_fpm: float
    observed_throughput_fpm: float
    queue_depth: int
    method: str
    is_remediated: bool = False
    as_of: datetime = Field(default_factory=datetime.utcnow)


class EvidencePayload(BaseModel):
    """Raw telemetry payload stored in Firestore evidence collection."""
    evidence_id: str
    run_id: str
    step_seq: int
    tool_name: str
    parameters: Dict[str, Any]
    latency_ms: float
    cache_hit: bool
    is_stale: bool = False
    raw_data: Any
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class StepEvent(BaseModel):
    """Appended event in the run events stream sent via SSE to browsers."""
    seq: int
    run_id: str
    tenant_id: str
    event_type: EventType
    title: str
    description: str
    evidence_id: Optional[str] = None
    step_turn: Optional[int] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RunDocument(BaseModel):
    """Full Firestore document representation of an investigation run."""
    run_id: str
    tenant_id: str
    user_id: str
    session_id: str
    objective: str
    state: RunState = RunState.QUEUED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    confidence: Optional[ConfidenceLevel] = None
    hypothesis: Optional[HypothesisScorecard] = None
    impact: Optional[ImpactProjection] = None
    options: List[RemediationOption] = Field(default_factory=list)
    chosen_option_id: Optional[str] = None
    chosen_option: Optional[RemediationOption] = None
    verification_impact: Optional[ImpactProjection] = None
    step_count: int = 0
    total_mcp_calls: int = 0
    cache_hit_count: int = 0
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Approval & Action Models
# ---------------------------------------------------------------------------

class ApprovalRequest(BaseModel):
    """Payload sent by the UI when a user approves a remediation option."""
    run_id: str
    option_id: str
    tenant_id: str
    user_id: str
    session_id: str


class ApprovalRecord(BaseModel):
    """Document in the Firestore approvals collection."""
    idempotency_key: str
    run_id: str
    option_id: str
    tenant_id: str
    user_id: str
    approved_at: datetime = Field(default_factory=datetime.utcnow)
    action_type: ActionType
    parameters: Dict[str, Any]
    executor_status: str
    executor_result: Dict[str, Any] = Field(default_factory=dict)


class AuditRecord(BaseModel):
    """Immutable audit record stored in Firestore for compliance."""
    audit_id: str
    idempotency_key: str
    run_id: str
    tenant_id: str
    user_id: str
    action_type: ActionType
    parameters: Dict[str, Any]
    executed_at: datetime = Field(default_factory=datetime.utcnow)
    status: str
    message: str


# ---------------------------------------------------------------------------
# Tenant Lease Models
# ---------------------------------------------------------------------------

class TenantLease(BaseModel):
    """Lease token mapping a user session to an isolated tenant world."""
    tenant_id: str
    session_id: str
    user_id: str
    leased_at: datetime = Field(default_factory=datetime.utcnow)
    heartbeat_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    is_observer: bool = False
    status: TenantStatus = TenantStatus.LEASED


class ToolCallLog(BaseModel):
    """Structured log record for every MCP call passing through mcp-gateway."""
    tool_name: str
    parameters: Dict[str, Any]
    latency_ms: float
    cache_hit: bool
    is_stale: bool = False
    tenant_id: str
    run_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
