export type RunState =
  | 'QUEUED'
  | 'RUNNING'
  | 'AWAITING_APPROVAL'
  | 'VERIFYING'
  | 'COMPLETED'
  | 'DEGRADED'
  | 'FAILED';

export type EventType =
  | 'PLAN'
  | 'TOOL_CALL'
  | 'EVIDENCE'
  | 'HYPOTHESIS'
  | 'IMPACT'
  | 'APPROVAL_REQUIRED'
  | 'ACTION_EXECUTED'
  | 'VERIFICATION'
  | 'COMPLETED'
  | 'DEGRADED'
  | 'ERROR';

export type ConfidenceLevel = 'HIGH' | 'MEDIUM' | 'LOW';

export type ActionType =
  | 'rollback_renderer_config'
  | 'scale_render_workers'
  | 'reprioritize_queue'
  | 'drain_worker';

export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH';

export interface TenantLease {
  tenant_id: string;
  session_id: string;
  user_id: string;
  leased_at: string;
  heartbeat_at: string;
  expires_at: string;
  is_observer: boolean;
  status: 'LEASED' | 'OBSERVER' | 'AVAILABLE';
}

/**
 * Mirrors RenderWorkerNode in services/render-sim/src/models.py.
 *
 * This previously declared `duration_avg_sec` and a per-worker `queue_depth`,
 * neither of which the API returns: the field is `current_frame_duration_sec`
 * and queue depth is fleet-level. Reading the absent field yielded undefined and
 * `.toFixed()` on it took down the whole render tree. Keep this in step with the
 * Python model.
 */
export interface WorkerStatus {
  worker_id: string;
  tenant_id: string;
  gpu_type: string;
  renderer_version: string;
  tile_size: number;
  gpu_utilization_pct: number;
  gpu_memory_used_mb: number;
  temperature_celsius: number;
  cpu_utilization_pct: number;
  memory_used_mb: number;
  active_jobs: number;
  completed_frames: number;
  failed_frames: number;
  current_frame_duration_sec: number;
  is_degraded: boolean;
  degraded_reason: string | null;
  is_drained: boolean;
}

export interface WorldState {
  tenant_id: string;
  production_id: string;
  title: string;
  renderer_version: string;
  tile_size: number;
  is_incident_active: boolean;
  incident_type?: string;
  baseline_throughput_fpm: number;
  observed_throughput_fpm: number;
  queue_depth: number;
  total_workers: number;
  healthy_workers: number;
  degraded_workers: number;
  workers: WorkerStatus[];
  last_updated: string;
}

export interface FalsifiableTestResult {
  test_id: string;
  name: string;
  description: string;
  passed: boolean;
  evidence_source: string;
  evidence_snippet: string;
  explanation: string;
  /**
   * False when the connected Grafana MCP server exposes no tool capable of
   * gathering this evidence. Such a test is excluded from the score rather than
   * counted as a failure, and must not be rendered as one. Optional so a payload
   * predating the field still parses, defaulting to applicable.
   */
  applicable?: boolean;
}

export interface HypothesisScorecard {
  primary_hypothesis: string;
  suspected_cause: string;
  tests: FalsifiableTestResult[];
  passed_count: number;
  /** Count of applicable tests, not of criteria defined. */
  total_tests: number;
  /** Names of the criteria that were skipped as inapplicable. */
  skipped_tests?: string[];
  confidence: ConfidenceLevel;
  /** Whether the hypothesis was corroborated, refuted, or left undecided. */
  verdict?: 'SUPPORTED' | 'REJECTED' | 'INCONCLUSIVE';
  headline?: string;
  missing_evidence_summary?: string;
}

export interface RemediationOption {
  option_id: string;
  action_type: ActionType;
  title: string;
  description: string;
  parameters: Record<string, any>;
  estimated_recovery_minutes: number;
  risk_level: RiskLevel;
  production_consequence: string;
}

export interface ImpactProjection {
  tenant_id: string;
  affected_shots: number;
  high_priority_shots: number;
  sequences: string[];
  deadline_utc: string;
  projected_completion_utc: string;
  delay_minutes: number;
  at_risk_deliverables: string[];
  baseline_throughput_fpm: number;
  observed_throughput_fpm: number;
  queue_depth: number;
  method: string;
  is_remediated: boolean;
  as_of: string;
}

export interface StepEvent {
  seq: number;
  run_id: string;
  tenant_id: string;
  event_type: EventType;
  title: string;
  description: string;
  evidence_id?: string;
  step_turn?: number;
  payload: Record<string, any>;
  timestamp: string;
}

export interface EvidencePayload {
  evidence_id: string;
  run_id: string;
  step_seq: number;
  tool_name: string;
  parameters: Record<string, any>;
  latency_ms: number;
  cache_hit: boolean;
  is_stale: boolean;
  raw_data: any;
  timestamp: string;
}

export interface RunDocument {
  run_id: string;
  tenant_id: string;
  user_id: string;
  session_id: string;
  objective: string;
  state: RunState;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  confidence?: ConfidenceLevel;
  hypothesis?: HypothesisScorecard;
  impact?: ImpactProjection;
  options: RemediationOption[];
  chosen_option_id?: string;
  chosen_option?: RemediationOption;
  verification_impact?: ImpactProjection;
  step_count: number;
  error_message?: string;
}

export interface ToolCallLog {
  tool_name: string;
  parameters: Record<string, any>;
  latency_ms: number;
  cache_hit: boolean;
  is_stale: boolean;
  tenant_id: string;
  run_id?: string;
  timestamp: string;
}
