import React, { useEffect, useState } from 'react';
import { Layers, Server, CheckCircle, AlertTriangle } from 'lucide-react';
import { WorldState } from '../types/api';
import { apiFetch } from '../lib/auth';

/** Mirrors GET /production/sequences on impact-engine. */
interface SequenceSummary {
  sequence_id: string;
  name: string;
  total_shots: number;
  completed_shots: number;
  rendering_shots: number;
  progress_pct: number;
  priority: string;
  deliverables: string[];
}

interface ProductionBoardProps {
  world: WorldState | null;
  /**
   * Sequences the impact projection traced back to the failing workers. This is
   * the only authority on which sequences are affected: it comes from the
   * worker-to-shot-to-sequence join, not from a global incident flag.
   */
  affectedSequences?: string[];
}

export const ProductionBoard: React.FC<ProductionBoardProps> = ({
  world,
  affectedSequences = [],
}) => {
  const workers = world?.workers ?? [];

  // Sequences come from the production metadata in impact-engine. They were
  // previously a hardcoded array whose progress was a constant that flipped on
  // is_incident_active, and which named a deliverable that does not exist.
  const [sequences, setSequences] = useState<SequenceSummary[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch('/api/impact/production/sequences');
        if (!res.ok) return;
        const rows: SequenceSummary[] = await res.json();
        if (!cancelled) setSequences(rows);
      } catch (err) {
        console.warn('Failed to fetch production sequences', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-6">
      {/* Sequences Board */}
      <div className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-panel">
        <div className="flex items-center justify-between pb-3 border-b border-studio-border/60">
          <div className="flex items-center space-x-2">
            <Layers className="w-4 h-4 text-studio-cyan" />
            <h3 className="text-sm font-semibold text-studio-fg uppercase font-mono tracking-wide">
              Production Sequences & Shots: Shadow Protocol
            </h3>
          </div>
          <span className="text-xs font-mono text-studio-fg3">
            Total Shots: {sequences.reduce((n, s) => n + s.total_shots, 0).toLocaleString() || "—"}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
          {sequences.map((seq) => {
            const isAffected = affectedSequences.includes(seq.name);
            return (
            <div
              key={seq.sequence_id}
              className={`border rounded-lg p-4 bg-studio-card/60 transition-all ${
                isAffected
                  ? 'border-studio-danger/40 bg-studio-danger/10'
                  : 'border-studio-border/60 hover:border-studio-accent/40'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold text-sm text-studio-fg">{seq.name}</span>
                <span
                  className={`text-[10px] font-mono px-2 py-0.5 rounded font-bold ${
                    seq.priority === 'HIGH'
                      ? 'bg-studio-warning/20 text-studio-warning border border-studio-warning/30'
                      : 'bg-studio-border text-studio-fg2'
                  }`}
                >
                  {seq.priority} PRIORITY
                </span>
              </div>

              <div className="mt-3 flex items-center justify-between text-xs font-mono text-studio-fg3">
                <span>{seq.total_shots.toLocaleString()} shots</span>
                <span className="text-studio-fg2 font-medium">{seq.deliverables.join(', ') || '—'}</span>
              </div>

              {/* Progress Bar */}
              <div className="mt-3">
                <div className="flex justify-between text-[11px] font-mono text-studio-fg3 mb-1">
                  <span>Progress</span>
                  <span className="text-studio-fg font-bold">{seq.progress_pct}%</span>
                </div>
                <div className="w-full bg-studio-bg rounded-full h-1.5 overflow-hidden">
                  <div
                    className={`h-1.5 rounded-full transition-all duration-500 ${
                      isAffected ? 'bg-studio-danger' : 'bg-studio-accent'
                    }`}
                    style={{ width: `${seq.progress_pct}%` }}
                  />
                </div>
              </div>
            </div>
            );
          })}
        </div>
      </div>

      {/* Render Farm Worker Fleet */}
      <div className="bg-studio-surface border border-studio-border rounded-xl p-5 shadow-panel">
        <div className="flex items-center justify-between pb-3 border-b border-studio-border/60">
          <div className="flex items-center space-x-2">
            <Server className="w-4 h-4 text-studio-cyan" />
            <h3 className="text-sm font-semibold text-studio-fg uppercase font-mono tracking-wide">
              Active Render Worker Fleet ({workers.length} Nodes)
            </h3>
          </div>
          <span className="text-xs font-mono text-studio-fg3">
            Renderer Target: <span className="text-studio-fg font-bold">{world?.renderer_version ?? 'v2.4.0'}</span> (tile_size={world?.tile_size ?? 256})
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3 mt-4">
          {workers.map((w) => {
            const isDegraded = w.is_degraded || (w.renderer_version === 'v2.4.1');
            return (
              <div
                key={w.worker_id}
                className={`p-3 rounded-lg border flex flex-col justify-between transition-all ${
                  isDegraded
                    ? 'bg-studio-danger/20 border-studio-danger/50 shadow-sm shadow-studio-danger/20'
                    : 'bg-studio-card/80 border-studio-border/60'
                }`}
              >
                <div>
                  <div className="flex items-center justify-between">
                    <span className="font-mono font-bold text-xs text-studio-fg">{w.worker_id}</span>
                    {isDegraded ? (
                      <AlertTriangle className="w-3.5 h-3.5 text-studio-danger" />
                    ) : (
                      <CheckCircle className="w-3.5 h-3.5 text-studio-success" />
                    )}
                  </div>
                  <span className="text-[10px] text-studio-fg3 font-mono block mt-0.5 truncate">
                    {w.gpu_type?.replace('NVIDIA ', '') ?? 'unknown GPU'}
                  </span>
                </div>

                <div className="my-2 space-y-1 font-mono text-[10px]">
                  <div className="flex justify-between">
                    <span className="text-studio-fg3">GPU:</span>
                    <span
                      className={`font-bold ${
                        (w.gpu_utilization_pct ?? 100) < 50 ? 'text-studio-danger' : 'text-studio-fg'
                      }`}
                    >
                      {w.gpu_utilization_pct?.toFixed(0) ?? '--'}%
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-studio-fg3">Time:</span>
                    <span
                      className={`font-bold ${
                        (w.current_frame_duration_sec ?? 0) > 60 ? 'text-studio-danger font-extrabold' : 'text-studio-fg'
                      }`}
                    >
                      {w.current_frame_duration_sec?.toFixed(0) ?? '--'}s
                    </span>
                  </div>
                </div>

                <div className="pt-1.5 border-t border-studio-border/40 text-[9px] font-mono text-center">
                  <span
                    className={`px-1.5 py-0.5 rounded font-semibold ${
                      isDegraded ? 'bg-studio-danger/30 text-studio-danger' : 'bg-studio-border text-studio-fg2'
                    }`}
                  >
                    {w.renderer_version}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
