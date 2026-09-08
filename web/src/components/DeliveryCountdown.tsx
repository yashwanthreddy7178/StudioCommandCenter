import React from 'react';
import { AlertCircle, CheckCircle2, Gauge, Clock } from 'lucide-react';
import { ImpactProjection, WorldState } from '../types/api';
import { utcTime } from '../lib/time';

interface DeliveryCountdownProps {
  impact: ImpactProjection | null;
  world: WorldState | null;
  /** Deadline from production metadata, known before any investigation runs. */
  productionDeadline?: string | null;
}

/** Placeholder shown until a figure has actually been measured. */
const Pending: React.FC<{ label?: string }> = ({ label = 'awaiting telemetry' }) => (
  <span className="text-sm font-mono text-studio-fg4">{label}</span>
);

export const DeliveryCountdown: React.FC<DeliveryCountdownProps> = ({
  impact,
  world,
  productionDeadline = null,
}) => {
  // Every figure below comes from the impact projection or live world state.
  // Nothing is substituted when a value is missing: a placeholder number here
  // would be indistinguishable on screen from a measured one.
  const delayMinutes = impact?.delay_minutes ?? null;
  const isLate = (delayMinutes ?? 0) > 0;
  const throughput = world?.observed_throughput_fpm ?? impact?.observed_throughput_fpm ?? null;
  const baseline = world?.baseline_throughput_fpm ?? impact?.baseline_throughput_fpm ?? null;
  const queueDepth = world?.queue_depth ?? impact?.queue_depth ?? null;
  const atRisk = impact?.at_risk_deliverables ?? [];
  // The deadline exists independently of a projection.
  const deadline = impact?.deadline_utc ?? productionDeadline;
  // Before a run there is no projection to date the deadline against, which is
  // precisely when the deadline is furthest out and most likely to fall on the
  // next UTC day. The panel re-renders on the world-state poll, so this rolls
  // over on its own at midnight.
  const nowIso = new Date().toISOString();
  const degraded = baseline !== null && throughput !== null && throughput < baseline * 0.9;

  return (
    <div className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-panel relative overflow-hidden">
      <div
        className={`absolute -right-20 -top-20 w-64 h-64 rounded-full blur-3xl pointer-events-none opacity-20 ${
          isLate ? 'bg-studio-danger' : 'bg-studio-success'
        }`}
      />

      <div className="flex items-center space-x-2 relative">
        <Clock className="w-4 h-4 text-studio-fg3" />
        <h2 className="text-sm font-semibold text-studio-fg tracking-wide">DELIVERY PROJECTION</h2>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mt-4">
        {/* Deadline, from the deliverable record in production metadata */}
        <div className="bg-studio-card/80 border border-studio-border/60 rounded-lg p-3.5 flex flex-col justify-between">
          <span className="text-xs text-studio-fg3 font-medium">Target Deadline</span>
          <div className="text-xl font-bold font-mono text-studio-fg mt-1">
            {deadline ? utcTime(deadline, impact?.as_of ?? nowIso) : <Pending label="--:--:-- UTC" />}
          </div>
          <span className="text-[11px] text-studio-fg4 font-mono mt-1">
            {atRisk.length > 0 ? atRisk.join(', ') : 'Hard delivery lock'}
          </span>
        </div>

        {/* Projected completion, from queue depth over observed throughput */}
        <div className="bg-studio-card/80 border border-studio-border/60 rounded-lg p-3.5 flex flex-col justify-between">
          <span className="text-xs text-studio-fg3 font-medium">Projected Completion</span>
          <div
            className={`text-xl font-bold font-mono mt-1 ${
              isLate ? 'text-studio-danger font-extrabold' : 'text-studio-success'
            }`}
          >
            {impact ? utcTime(impact.projected_completion_utc, impact.as_of) : <Pending label="--:--:-- UTC" />}
          </div>
          <span className="text-[11px] text-studio-fg4 font-mono mt-1">
            {impact ? (isLate ? 'Misses target deadline' : 'Inside the delivery window') : 'No projection yet'}
          </span>
        </div>

        {/* Delivery status */}
        <div
          className={`border rounded-lg p-3.5 flex flex-col justify-between ${
            isLate ? 'bg-studio-danger/10 border-studio-danger/30' : 'bg-studio-success/10 border-studio-success/30'
          }`}
        >
          <span className="text-xs font-medium text-studio-fg2">Delivery Status</span>
          <div className="flex items-center space-x-2 mt-1">
            {delayMinutes === null ? (
              <Pending />
            ) : isLate ? (
              <>
                <AlertCircle className="w-5 h-5 text-studio-danger" />
                <span className="text-lg font-bold font-mono text-studio-danger">+{delayMinutes}m DELAY</span>
              </>
            ) : (
              <>
                <CheckCircle2 className="w-5 h-5 text-studio-success" />
                <span className="text-lg font-bold font-mono text-studio-success">ON TIME</span>
              </>
            )}
          </div>
          <span className="text-[11px] font-mono text-studio-fg3 mt-1">
            {impact
              ? `${impact.affected_shots.toLocaleString()} shots (${impact.high_priority_shots.toLocaleString()} high priority)`
              : 'Awaiting impact projection'}
          </span>
        </div>

        {/* Fleet throughput */}
        <div className="bg-studio-card/80 border border-studio-border/60 rounded-lg p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs text-studio-fg3 font-medium">Fleet Throughput</span>
            <Gauge className="w-3.5 h-3.5 text-studio-fg3" />
          </div>
          <div className="flex items-baseline space-x-1.5 mt-1">
            {throughput === null ? (
              <Pending />
            ) : (
              <>
                <span
                  className={`text-xl font-bold font-mono ${degraded ? 'text-studio-warning' : 'text-studio-fg'}`}
                >
                  {throughput.toFixed(1)}
                </span>
                <span className="text-xs text-studio-fg3 font-mono">FPM</span>
              </>
            )}
          </div>
          <span className="text-[11px] text-studio-fg4 font-mono mt-1">
            {baseline !== null ? `Baseline: ${baseline.toFixed(1)} FPM` : 'Baseline: pending'}
            {' | '}
            {queueDepth !== null ? `Queue: ${queueDepth.toLocaleString()} frames` : 'Queue: pending'}
          </span>
        </div>
      </div>

      {/* The derivation is shown so the number can be checked, per section 7. */}
      {impact && (
        <div className="mt-3 text-[11px] font-mono text-studio-fg4 border-t border-studio-border/50 pt-2">
          method: {impact.method}
        </div>
      )}
    </div>
  );
};
