import React from 'react';
import { Film, Shield, RotateCcw, AlertTriangle, Play, Sun, Moon } from 'lucide-react';
import { Theme } from '../lib/theme';
import { TenantLease, WorldState, RunState } from '../types/api';
import { PoolStatus, TenantOption } from '../hooks/useTenantLease';

interface HeaderProps {
  lease: TenantLease | null;
  /** Worlds available to switch into, and whether each is taken. */
  tenants?: TenantOption[];
  /** How much of the pool is left, or null when it could not be read. */
  pool?: PoolStatus | null;
  onSwitchTenant?: (tenantId: string) => void;
  /** Re-reads availability, so the list is current at the moment of choosing. */
  onRefreshTenants?: () => void;
  world: WorldState | null;
  runState: RunState;
  onTriggerIncident: () => void;
  onResetWorld: () => void;
  onStartInvestigation: () => void;
  isInvestigating: boolean;
  theme: Theme;
  onToggleTheme: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  lease,
  tenants = [],
  pool = null,
  onSwitchTenant,
  onRefreshTenants,
  world,
  onTriggerIncident,
  onResetWorld,
  onStartInvestigation,
  isInvestigating,
  theme,
  onToggleTheme }) => {
  const isIncident = world?.is_incident_active ?? false;

  return (
    <header className="border-b border-studio-border bg-studio-surface/80 backdrop-blur px-6 py-3.5 sticky top-0 z-40">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        {/* Brand */}
        <div className="flex items-center space-x-3.5">
          <div className="bg-gradient-to-tr from-studio-accent to-studio-violet p-2.5 rounded-lg shadow-lg shadow-studio-accent/20">
            <Film className="w-5 h-5 text-studio-on-accent" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="text-lg font-bold tracking-tight text-studio-fg">
                Studio Production Commander
              </h1>
              <span className="bg-studio-border text-studio-fg2 text-xs px-2 py-0.5 rounded font-mono font-medium">
                v0.1.0
              </span>
            </div>
            <p className="text-xs text-studio-fg3">
              Autonomous VFX Render Pipeline Investigation & Delivery Defense
            </p>
          </div>
        </div>

        {/* Tenant & World Status */}
        <div className="flex items-center space-x-3">
          {lease && (
            <div className="flex items-center space-x-2 bg-studio-card border border-studio-border px-3 py-1.5 rounded-md text-xs font-mono">
              <Shield className="w-3.5 h-3.5 text-studio-cyan" />
              <label htmlFor="tenant-select" className="text-studio-fg3">
                Tenant:
              </label>
              {onSwitchTenant && tenants.length > 0 ? (
                <select
                  id="tenant-select"
                  value={lease.tenant_id}
                  onChange={(e) => onSwitchTenant(e.target.value)}
                  onMouseDown={onRefreshTenants}
                  onFocus={onRefreshTenants}
                  className="bg-transparent font-semibold text-studio-fg uppercase focus:outline-none cursor-pointer"
                  title="Switch to another tenant world"
                >
                  {/* The current world is always listed, even in observer mode
                      where it is not one of the writable options. */}
                  {!tenants.some((t) => t.tenant_id === lease.tenant_id) && (
                    <option value={lease.tenant_id}>{lease.tenant_id}</option>
                  )}
                  {tenants.map((t) => (
                    <option key={t.tenant_id} value={t.tenant_id} className="bg-studio-card">
                      {t.tenant_id}
                      {t.leased && t.tenant_id !== lease.tenant_id ? ' (in use)' : ''}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="font-semibold text-studio-fg uppercase">{lease.tenant_id}</span>
              )}
              {lease.is_observer && (
                <span
                  className="bg-studio-warning/20 text-studio-warning px-1.5 py-0.5 rounded text-[10px]"
                  title={
                    pool
                      ? `The tenant pool was full. ${pool.observerSessions} session(s) share this world.`
                      : 'The tenant pool was full when this session started.'
                  }
                >
                  OBSERVER
                  {pool && pool.observerSessions > 1 ? ` ×${pool.observerSessions}` : ''}
                </span>
              )}
              {/* Visible without opening the picker: the number a judge wants is
                  whether any world is left, not which ones. Absent rather than
                  guessed when availability could not be read. */}
              <span
                className="text-studio-fg4 border-l border-studio-border pl-2"
                title="Tenant worlds currently free"
              >
                {pool ? `${pool.free}/${pool.total} free` : '--/-- free'}
              </span>
            </div>
          )}

          {/* Incident Badge */}
          <div
            className={`flex items-center space-x-2 px-3 py-1.5 rounded-md text-xs font-mono font-semibold border ${
              isIncident
                ? 'bg-studio-danger/10 border-studio-danger/40 text-studio-danger animate-glow-danger'
                : 'bg-studio-success/10 border-studio-success/30 text-studio-success'
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isIncident ? 'bg-studio-danger animate-ping' : 'bg-studio-success'
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
              className="flex items-center space-x-1.5 bg-studio-danger/90 hover:bg-studio-danger text-studio-on-accent px-3 py-1.5 rounded-md text-xs font-medium transition shadow-lg shadow-studio-danger/20 active:scale-95"
            >
              <AlertTriangle className="w-3.5 h-3.5" />
              <span>Simulate Incident</span>
            </button>
          ) : (
            <button
              onClick={onResetWorld}
              className="flex items-center space-x-1.5 bg-studio-card hover:bg-studio-border text-studio-fg border border-studio-border px-3 py-1.5 rounded-md text-xs font-medium transition active:scale-95"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Reset World</span>
            </button>
          )}

          <button
            onClick={onToggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            className="flex items-center justify-center w-8 h-8 rounded-md border border-studio-border bg-studio-card text-studio-fg3 hover:text-studio-fg transition active:scale-95"
          >
            {theme === 'dark'
              ? <Sun className="w-3.5 h-3.5" />
              : <Moon className="w-3.5 h-3.5" />}
          </button>

          <button
            onClick={onStartInvestigation}
            disabled={isInvestigating}
            className={`flex items-center space-x-1.5 px-4 py-1.5 rounded-md text-xs font-semibold transition shadow-panel ${
              isInvestigating
                ? 'bg-studio-accent/40 text-studio-fg2 cursor-not-allowed'
                : 'bg-studio-accent hover:bg-studio-accent text-studio-on-accent shadow-studio-accent/25 active:scale-95'
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
