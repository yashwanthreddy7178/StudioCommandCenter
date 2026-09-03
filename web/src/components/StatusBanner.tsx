import React from 'react';
import { AlertTriangle, CheckCircle2, Zap } from 'lucide-react';
import { RunState } from '../types/api';

interface StatusBannerProps {
  runState: RunState;
  isRecovered: boolean;
}

export const StatusBanner: React.FC<StatusBannerProps> = ({ runState, isRecovered }) => {
  if (isRecovered) {
    return (
      <div className="bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 px-4 py-2.5 rounded-lg flex items-center justify-between text-xs font-mono">
        <div className="flex items-center space-x-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          <span className="font-semibold">
            REMEDIATION VERIFIED: renderer rolled back and the fleet returned to baseline throughput.
          </span>
        </div>
        <span className="bg-emerald-500/20 text-emerald-300 px-2 py-0.5 rounded text-[10px] font-bold">
          DEADLINE ON-TIME
        </span>
      </div>
    );
  }

  if (runState === 'AWAITING_APPROVAL') {
    return (
      <div className="bg-amber-500/10 border border-amber-500/30 text-amber-300 px-4 py-2.5 rounded-lg flex items-center justify-between text-xs font-mono">
        <div className="flex items-center space-x-2">
          <AlertTriangle className="w-4 h-4 text-amber-400" />
          <span className="font-semibold">
            INVESTIGATION COMPLETE: Root cause identified. Human approval required to execute remediation.
          </span>
        </div>
        <span className="bg-amber-500/20 text-amber-300 px-2 py-0.5 rounded text-[10px] font-bold">
          ACTION REQUIRED
        </span>
      </div>
    );
  }

  if (runState === 'RUNNING') {
    return (
      <div className="bg-blue-500/10 border border-blue-500/30 text-blue-300 px-4 py-2.5 rounded-lg flex items-center justify-between text-xs font-mono">
        <div className="flex items-center space-x-2">
          <Zap className="w-4 h-4 text-blue-400 animate-pulse" />
          <span className="font-semibold">
            AUTONOMOUS AGENT ACTIVE: Reasoning over Grafana MCP telemetry across Mimir, Loki, and Tempo...
          </span>
        </div>
        <span className="bg-blue-500/20 text-blue-300 px-2 py-0.5 rounded text-[10px] font-bold">
          INVESTIGATING
        </span>
      </div>
    );
  }

  return null;
};
