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
      <div className="bg-studio-success/10 border border-studio-success/30 text-studio-success px-4 py-2.5 rounded-lg flex items-center justify-between text-xs font-mono">
        <div className="flex items-center space-x-2">
          <CheckCircle2 className="w-4 h-4 text-studio-success" />
          <span className="font-semibold">
            REMEDIATION VERIFIED: renderer rolled back and the fleet returned to baseline throughput.
          </span>
        </div>
        <span className="bg-studio-success/20 text-studio-success px-2 py-0.5 rounded text-[10px] font-bold">
          DEADLINE ON-TIME
        </span>
      </div>
    );
  }

  if (runState === 'AWAITING_APPROVAL') {
    return (
      <div className="bg-studio-warning/10 border border-studio-warning/30 text-studio-warning px-4 py-2.5 rounded-lg flex items-center justify-between text-xs font-mono">
        <div className="flex items-center space-x-2">
          <AlertTriangle className="w-4 h-4 text-studio-warning" />
          <span className="font-semibold">
            INVESTIGATION COMPLETE: Root cause identified. Human approval required to execute remediation.
          </span>
        </div>
        <span className="bg-studio-warning/20 text-studio-warning px-2 py-0.5 rounded text-[10px] font-bold">
          ACTION REQUIRED
        </span>
      </div>
    );
  }

  if (runState === 'RUNNING') {
    return (
      <div className="bg-studio-accent/10 border border-studio-accent/30 text-studio-accent px-4 py-2.5 rounded-lg flex items-center justify-between text-xs font-mono">
        <div className="flex items-center space-x-2">
          <Zap className="w-4 h-4 text-studio-accent animate-pulse" />
          <span className="font-semibold">
            AUTONOMOUS AGENT ACTIVE: Reasoning over Grafana MCP telemetry across Mimir, Loki, and Tempo...
          </span>
        </div>
        <span className="bg-studio-accent/20 text-studio-accent px-2 py-0.5 rounded text-[10px] font-bold">
          INVESTIGATING
        </span>
      </div>
    );
  }

  return null;
};
