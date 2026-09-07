import React, { useState, useEffect } from 'react';
import { Activity } from 'lucide-react';
import { apiFetch } from '../lib/auth';
import { deriveStats, EMPTY_STATS, GatewayStats } from '../lib/gatewayStats';

/** One measured figure, or a placeholder that cannot be mistaken for one.
 *
 * The same rule the delivery panel follows: a number on screen has been
 * measured. This panel previously seeded itself with invented values and fell
 * back to them whenever a reading was zero or a fetch failed, so it showed a
 * 91.4% cache hit ratio on a gateway that had served nothing.
 */
const Metric: React.FC<{
  label: string;
  value: number | null;
  format?: (value: number) => string;
  className?: string;
}> = ({ label, value, format = (v) => v.toLocaleString(), className = 'text-white' }) => (
  <div className="bg-studio-card/80 p-2.5 rounded-lg border border-studio-border/40">
    <span className="text-[10px] text-slate-400 block">{label}</span>
    {value === null ? (
      <span className="text-base font-bold text-slate-500 mt-0.5 block">--</span>
    ) : (
      <span className={`text-base font-bold mt-0.5 block ${className}`}>{format(value)}</span>
    )}
  </div>
);

export const AgentMetrics: React.FC = () => {
  const [stats, setStats] = useState<GatewayStats>(EMPTY_STATS);
  const [qpsLimit, setQpsLimit] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    const fetchStats = async () => {
      try {
        const res = await apiFetch('/api/mcp/stats');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) setStats(deriveStats(data));
      } catch {
        // Nothing is retained from a failed read. Holding the last good figures
        // would present stale numbers as current, and inventing them is worse.
        if (!cancelled) setStats(EMPTY_STATS);
      }
    };

    // Polled every five seconds, and once immediately: waiting for the first
    // interval left the panel showing its placeholder state for five seconds
    // after every load.
    fetchStats();
    const interval = setInterval(fetchStats, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // The ceiling is the gateway's own configured limit, reported by /readyz,
  // rather than a constant typed into the markup.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch('/api/mcp/readyz');
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled && typeof data.qps_limit === 'number') setQpsLimit(data.qps_limit);
      } catch {
        // Left unknown rather than guessed.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="bg-studio-surface border border-studio-border rounded-xl p-4 shadow-lg font-mono text-xs text-slate-300">
      <div className="flex items-center justify-between pb-2 mb-3 border-b border-studio-border/60">
        <div className="flex items-center space-x-2">
          <Activity className="w-3.5 h-3.5 text-studio-cyan" />
          <span className="font-semibold text-white uppercase tracking-wider text-[11px]">
            MCP Gateway Concurrency Metrics
          </span>
        </div>
        <span className="text-[10px] text-slate-400 font-bold">
          {qpsLimit === null ? 'QPS CAP: --' : `QPS CAP: ${qpsLimit.toFixed(1)}`}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
        <Metric
          label="Cache Hit Ratio"
          value={stats.cacheHitRatioPct}
          format={(v) => `${v.toFixed(1)}%`}
          className="text-emerald-400"
        />
        {/* Upstream calls being shared between concurrent callers right now. The
            panel used to print a hardcoded "100% Dedupe" here, which was not a
            measurement of anything. */}
        <Metric
          label="Coalesced In Flight"
          value={stats.activeSingleflights}
          className="text-studio-cyan"
        />
        <Metric label="Logical MCP Calls" value={stats.totalCalls} />
        <Metric
          label="Upstream Real Calls"
          value={stats.upstreamCalls}
          className="text-studio-violet"
        />
      </div>
    </div>
  );
};
