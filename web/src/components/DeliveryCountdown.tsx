import React from 'react';
import { AlertCircle, CheckCircle2, Gauge, Clock } from 'lucide-react';
import { ImpactProjection, WorldState } from '../types/api';

interface DeliveryCountdownProps {
  impact: ImpactProjection | null;
  world: WorldState | null;
  /** Deadline from production metadata, known before any investigation runs. */
  productionDeadline?: string | null;
}

/** Formats an ISO timestamp as HH:MM:SS UTC. */
function utcTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '--:--:--';
  return `${d.toISOString().slice(11, 19)} UTC`;
}

/** Placeholder shown until a figure has actually been measured. */
const Pending: React.FC<{ label?: string }> = ({ label = 'awaiting telemetry' }) => (
  <span className="text-sm font-mono text-slate-500">{label}</span>
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
  const degraded = baseline !== null && throughput !== null && throughput < baseline * 0.9;

  return (
    <div className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-xl relative overflow-hidden">
      <div
        className={`absolute -right-20 -top-20 w-64 h-64 rounded-full blur-3xl pointer-events-none opacity-20 ${
          isLate ? 'bg-red-500' : 'bg-emerald-500'
        }`}
      />

      <div className="flex items-center space-x-2 relative">
        <Clock className="w-4 h-4 text-slate-400" />
        <h2 className="text-sm font-semibold text-slate-200 tracking-wide">DELIVERY PROJECTION</h2>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mt-4">
        {/* Deadline, from the deliverable record in production metadata */}
        <div className="bg-studio-card/80 border border-studio-border/60 rounded-lg p-3.5 flex flex-col justify-between">
          <span className="text-xs text-slate-400 font-medium">Target Deadline</span>
          <div className="text-xl font-bold font-mono text-white mt-1">
            {deadline ? utcTime(deadline) : <Pending label="--:--:-- UTC" />}
          </div>
          <span className="text-[11px] text-slate-500 font-mono mt-1">
            {atRisk.length > 0 ? atRisk.join(', ') : 'Hard delivery lock'}
          </span>
        </div>

        {/* Projected completion, from queue depth over observed throughput */}
        <div className="bg-studio-card/80 border border-studio-border/60 rounded-lg p-3.5 flex flex-col justify-between">
          <span className="text-xs text-slate-400 font-medium">Projected Completion</span>
          <div
            className={`text-xl font-bold font-mono mt-1 ${
              isLate ? 'text-red-400 font-extrabold' : 'text-emerald-400'
            }`}
          >
            {impact ? utcTime(impact.projected_completion_utc) : <Pending label="--:--:-- UTC" />}
          </div>
          <span className="text-[11px] text-slate-500 font-mono mt-1">
            {impact ? (isLate ? 'Misses target deadline' : 'Inside the delivery window') : 'No projection yet'}
          </span>
        </div>

        {/* Delivery status */}
        <div
          className={`border rounded-lg p-3.5 flex flex-col justify-between ${
            isLate ? 'bg-red-500/10 border-red-500/30' : 'bg-emerald-500/10 border-emerald-500/30'
          }`}
        >
          <span className="text-xs font-medium text-slate-300">Delivery Status</span>
          <div className="flex items-center space-x-2 mt-1">
            {delayMinutes === null ? (
              <Pending />
            ) : isLate ? (
              <>
                <AlertCircle className="w-5 h-5 text-red-400" />
                <span className="text-lg font-bold font-mono text-red-400">+{delayMinutes}m DELAY</span>
              </>
            ) : (
              <>
                <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                <span className="text-lg font-bold font-mono text-emerald-400">ON TIME</span>
              </>
            )}
          </div>
          <span className="text-[11px] font-mono text-slate-400 mt-1">
            {impact
              ? `${impact.affected_shots.toLocaleString()} shots (${impact.high_priority_shots.toLocaleString()} high priority)`
              : 'Awaiting impact projection'}
          </span>
        </div>

        {/* Fleet throughput */}
        <div className="bg-studio-card/80 border border-studio-border/60 rounded-lg p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400 font-medium">Fleet Throughput</span>
            <Gauge className="w-3.5 h-3.5 text-slate-400" />
          </div>
          <div className="flex items-baseline space-x-1.5 mt-1">
            {throughput === null ? (
              <Pending />
            ) : (
              <>
                <span
                  className={`text-xl font-bold font-mono ${degraded ? 'text-amber-400' : 'text-white'}`}
                >
                  {throughput.toFixed(1)}
                </span>
                <span className="text-xs text-slate-400 font-mono">FPM</span>
              </>
            )}
          </div>
          <span className="text-[11px] text-slate-500 font-mono mt-1">
            {baseline !== null ? `Baseline: ${baseline.toFixed(1)} FPM` : 'Baseline: pending'}
            {' | '}
            {queueDepth !== null ? `Queue: ${queueDepth.toLocaleString()} frames` : 'Queue: pending'}
          </span>
        </div>
      </div>

      {/* The derivation is shown so the number can be checked, per section 7. */}
      {impact && (
        <div className="mt-3 text-[11px] font-mono text-slate-500 border-t border-studio-border/50 pt-2">
          method: {impact.method}
        </div>
      )}
    </div>
  );
};
