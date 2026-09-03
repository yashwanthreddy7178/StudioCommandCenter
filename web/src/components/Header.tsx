import React from 'react';
import { Film, Shield, RotateCcw, AlertTriangle, Play } from 'lucide-react';
import { TenantLease, WorldState, RunState } from '../types/api';

interface HeaderProps {
  lease: TenantLease | null;
  world: WorldState | null;
  runState: RunState;
  onTriggerIncident: () => void;
  onResetWorld: () => void;
  onStartInvestigation: () => void;
  isInvestigating: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  lease,
  world,
  onTriggerIncident,
  onResetWorld,
  onStartInvestigation,
  isInvestigating }) => {
  const isIncident = world?.is_incident_active ?? false;

  return (
    <header className="border-b border-studio-border bg-studio-surface/80 backdrop-blur px-6 py-3.5 sticky top-0 z-40">
      <div className="flex items-center justify-between">
        {/* Brand */}
        <div className="flex items-center space-x-3.5">
          <div className="bg-gradient-to-tr from-studio-accent to-studio-violet p-2.5 rounded-lg shadow-lg shadow-blue-500/20">
            <Film className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="text-lg font-bold tracking-tight text-white">
                Studio Production Commander
              </h1>
              <span className="bg-studio-border text-slate-300 text-xs px-2 py-0.5 rounded font-mono font-medium">
                v0.1.0
              </span>
            </div>
            <p className="text-xs text-slate-400">
              Autonomous VFX Render Pipeline Investigation & Delivery Defense
            </p>
          </div>
        </div>

        {/* Tenant & World Status */}
        <div className="flex items-center space-x-3">
          {lease && (
            <div className="flex items-center space-x-2 bg-studio-card border border-studio-border px-3 py-1.5 rounded-md text-xs font-mono">
              <Shield className="w-3.5 h-3.5 text-studio-cyan" />
              <span className="text-slate-400">Tenant:</span>
              <span className="font-semibold text-white uppercase">{lease.tenant_id}</span>
              {lease.is_observer && (
                <span className="bg-studio-warning/20 text-studio-warning px-1.5 py-0.5 rounded text-[10px]">
                  OBSERVER
                </span>
              )}
            </div>
          )}

          {/* Incident Badge */}
          <div
            className={`flex items-center space-x-2 px-3 py-1.5 rounded-md text-xs font-mono font-semibold border ${
              isIncident
                ? 'bg-red-500/10 border-red-500/40 text-red-400 animate-glow-danger'
                : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isIncident ? 'bg-red-500 animate-ping' : 'bg-emerald-400'
              }`}
            />
            <span>{isIncident ? 'INCIDENT ACTIVE (v2.4.1 REGRESSION)' : 'FLEET NORMAL (v2.4.0)'}</span>
          </div>
        </div>

        {/* Quick Action Controls */}
        <div className="flex items-center space-x-2.5">
          {!isIncident ? (
            <button
              onClick={onTriggerIncident}
              className="flex items-center space-x-1.5 bg-red-600/90 hover:bg-red-600 text-white px-3 py-1.5 rounded-md text-xs font-medium transition shadow-lg shadow-red-600/20 active:scale-95"
            >
              <AlertTriangle className="w-3.5 h-3.5" />
              <span>Simulate Incident</span>
            </button>
          ) : (
            <button
              onClick={onResetWorld}
              className="flex items-center space-x-1.5 bg-studio-card hover:bg-slate-700 text-slate-200 border border-studio-border px-3 py-1.5 rounded-md text-xs font-medium transition active:scale-95"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Reset World</span>
            </button>
          )}

          <button
            onClick={onStartInvestigation}
            disabled={isInvestigating}
            className={`flex items-center space-x-1.5 px-4 py-1.5 rounded-md text-xs font-semibold transition shadow-lg ${
              isInvestigating
                ? 'bg-studio-accent/40 text-slate-300 cursor-not-allowed'
                : 'bg-studio-accent hover:bg-blue-600 text-white shadow-blue-500/25 active:scale-95'
            }`}
          >
            <Play className="w-3.5 h-3.5 fill-current" />
            <span>{isInvestigating ? 'Investigating...' : 'Launch Agent'}</span>
          </button>
        </div>
      </div>
    </header>
  );
};
