import React from 'react';
import { Clock, AlertCircle, CheckCircle2, TrendingDown, Gauge, Layers } from 'lucide-react';
import { ImpactProjection, WorldState } from '../types/api';

interface DeliveryCountdownProps {
  impact: ImpactProjection | null;
  world: WorldState | null;
}

export const DeliveryCountdown: React.FC<DeliveryCountdownProps> = ({ impact, world }) => {
  const isIncident = world?.is_incident_active ?? false;
  const throughput = world?.observed_throughput_fpm ?? 118.6;
  const queueDepth = world?.queue_depth ?? 18432;
  const delayMinutes = impact ? impact.delay_minutes : isIncident ? 47 : 0;
  const isRecovered = impact ? impact.is_remediated : !isIncident;

  return (
    <div className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-xl relative overflow-hidden">
      {/* Background Accent Glow */}
      <div
        className={`absolute -right-20 -top-20 w-64 h-64 rounded-full blur-3xl pointer-events-none opacity-20 ${
          delayMinutes > 0 ? 'bg-red-500' : 'bg-emerald-500'
        }`}
      />

      <div className="flex items-center justify-between pb-4 border-b border-studio-border/60">
        <div className="flex items-center space-x-2.5">
          <Clock className="w-5 h-5 text-studio-cyan" />
          <h2 className="text-sm font-semibold tracking-wide text-slate-200 uppercase font-mono">
            VFX Delivery Deadline Protection: SP_VFX_R04
          </h2>
        </div>
        <span className="text-xs font-mono text-slate-400">
          Target Deliverable: <span className="text-white font-medium">Shadow Protocol - 4K Review Master</span>
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mt-4">
        {/* Delivery Deadline */}
        <div className="bg-studio-card/80 border border-studio-border/60 rounded-lg p-3.5 flex flex-col justify-between">
          <span className="text-xs text-slate-400 font-medium">Target Deadline</span>
          <div className="text-xl font-bold font-mono text-white mt-1">18:00:00 UTC</div>
          <span className="text-[11px] text-slate-500 font-mono mt-1">Today (Hard delivery lock)</span>
        </div>

        {/* Projected Completion */}
        <div className="bg-studio-card/80 border border-studio-border/60 rounded-lg p-3.5 flex flex-col justify-between">
          <span className="text-xs text-slate-400 font-medium">Projected Completion</span>
          <div
            className={`text-xl font-bold font-mono mt-1 ${
              delayMinutes > 0 ? 'text-red-400 font-extrabold' : 'text-emerald-400'
            }`}
          >
            {delayMinutes > 0 ? '18:47:00 UTC' : '17:35:00 UTC'}
          </div>
          <span className="text-[11px] text-slate-500 font-mono mt-1">
            {delayMinutes > 0 ? 'Misses target deadline' : '25m safety margin'}
          </span>
        </div>

        {/* Status / Delay Delta */}
        <div
          className={`border rounded-lg p-3.5 flex flex-col justify-between ${
            delayMinutes > 0
              ? 'bg-red-500/10 border-red-500/30'
              : 'bg-emerald-500/10 border-emerald-500/30'
          }`}
        >
          <span className="text-xs font-medium text-slate-300">Delivery Status</span>
          <div className="flex items-center space-x-2 mt-1">
            {delayMinutes > 0 ? (
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
            {delayMinutes > 0 ? '1 Deliverable at risk' : 'All deliverables protected'}
          </span>
        </div>

        {/* Fleet Throughput */}
        <div className="bg-studio-card/80 border border-studio-border/60 rounded-lg p-3.5 flex flex-col justify-between">
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400 font-medium">Fleet Throughput</span>
            <Gauge className="w-3.5 h-3.5 text-slate-400" />
          </div>
          <div className="flex items-baseline space-x-1.5 mt-1">
            <span
              className={`text-xl font-bold font-mono ${
                throughput < 90 ? 'text-amber-400' : 'text-white'
              }`}
            >
              {throughput.toFixed(1)}
            </span>
            <span className="text-xs text-slate-400 font-mono">FPM</span>
          </div>
          <span className="text-[11px] text-slate-500 font-mono mt-1">
            Baseline: 118.6 FPM | Queue: {queueDepth.toLocaleString()} frames
          </span>
        </div>
      </div>

      {/* Impact Engine Method String */}
      {impact && (
        <div className="mt-4 pt-3 border-t border-studio-border/40 flex items-start space-x-2 text-xs font-mono text-slate-400">
          <span className="text-studio-accent font-semibold whitespace-nowrap">Impact Engine Derivation:</span>
          <span className="text-slate-300 italic">{impact.method}</span>
        </div>
      )}
    </div>
  );
};
