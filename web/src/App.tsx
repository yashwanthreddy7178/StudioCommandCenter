import React, { useState, useEffect, useCallback } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { Header } from './components/Header';
import { DeliveryCountdown } from './components/DeliveryCountdown';
import { PanelBoundary } from './components/PanelBoundary';
import { ProductionBoard } from './components/ProductionBoard';
import { EvidenceLedger } from './components/EvidenceLedger';
import { HypothesisPanel } from './components/HypothesisPanel';
import { ApprovalModal } from './components/ApprovalModal';
import { StatusBanner } from './components/StatusBanner';
import { AgentMetrics } from './components/AgentMetrics';
import { useTenantLease } from './hooks/useTenantLease';
import { useRunStream } from './hooks/useRunStream';
import { useTheme } from './hooks/useTheme';
import { Theme } from './lib/theme';
import { WorldState } from './types/api';
import { Login } from './components/Login';
import { getToken, clearToken, UNAUTHORIZED_EVENT } from './lib/auth';
import { apiFetch } from './lib/auth';

export const App: React.FC = () => {
  // One shared operator credential gates the whole app: every service route is
  // published to the internet by nginx, and the approval gate behind them
  // executes real remediations.
  const [signedIn, setSignedIn] = useState<boolean>(() => getToken() !== null);

  // Owned here rather than inside the command centre so the sign-in screen is
  // themed too -- otherwise a viewer who chose dark signs in on a light page.
  const { theme, toggleTheme } = useTheme();

  if (!signedIn) {
    return <Login onSignedIn={() => setSignedIn(true)} theme={theme} onToggleTheme={toggleTheme} />;
  }

  return (
    <CommandCentre
      onSignedOut={() => { clearToken(); setSignedIn(false); }}
      theme={theme}
      onToggleTheme={toggleTheme}
    />
  );
};

interface CommandCentreProps {
  /** Called when the server rejects the stored token mid-session. */
  onSignedOut: () => void;
  theme: Theme;
  onToggleTheme: () => void;
}

const CommandCentre: React.FC<CommandCentreProps> = ({ onSignedOut, theme, onToggleTheme }) => {
  // A token that expires mid-session shows up as a 401 on whichever poll fires
  // first. Any of them means the same thing, so the shell listens once.
  useEffect(() => {
    window.addEventListener(UNAUTHORIZED_EVENT, onSignedOut);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onSignedOut);
  }, [onSignedOut]);

  const { lease, tenants, pool, switchTenant, refreshTenants } = useTenantLease();
  const [world, setWorld] = useState<WorldState | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [isExecutingApproval, setIsExecutingApproval] = useState<boolean>(false);
  const [isApprovalModalOpen, setIsApprovalModalOpen] = useState<boolean>(false);
  /** Why the last attempt to start an investigation was refused, if it was. */
  const [runError, setRunError] = useState<string | null>(null);

  const {
    events,
    runState,
    hypothesis,
    impact,
    options,
    verificationImpact,
    isStreaming,
  } = useRunStream(activeRunId);

  // Fetch tenant world state from simulator
  const [productionDeadline, setProductionDeadline] = useState<string | null>(null);

  const fetchWorld = useCallback(async () => {
    if (!lease) return;
    try {
      const res = await apiFetch(`/api/sim/worlds/${lease.tenant_id}`);
      if (res.ok) {
        const data: WorldState = await res.json();
        setWorld(data);
      }
    } catch (err) {
      console.warn('Failed to fetch world state', err);
    }
  }, [lease]);

  useEffect(() => {
    fetchWorld();
    const interval = setInterval(fetchWorld, 3000);
    return () => clearInterval(interval);
  }, [fetchWorld]);

  // The delivery deadline is a property of the production, not an output of an
  // investigation, so it is shown from the moment the board loads rather than
  // staying blank until a run produces a projection.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch('/api/impact/deliverables');
        if (!res.ok) return;
        const rows = await res.json();
        if (!cancelled && rows.length > 0) {
          const soonest = rows
            .map((d: { deadline_utc: string }) => d.deadline_utc)
            .sort()[0];
          setProductionDeadline(soonest);
        }
      } catch (err) {
        console.warn('Failed to fetch production deadline', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Open approval modal automatically when run state reaches AWAITING_APPROVAL
  useEffect(() => {
    if (runState === 'AWAITING_APPROVAL' && options.length > 0) {
      setIsApprovalModalOpen(true);
    }
  }, [runState, options]);

  // Actions
  const handleTriggerIncident = async () => {
    if (!lease) return;
    try {
      await apiFetch('/api/gateway/scenario/trigger-incident', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenant_id: lease.tenant_id,
          scenario_type: 'renderer_tile_regression',
          affected_worker_ids: ['w-03', 'w-07', 'w-11', 'w-17'],
          new_renderer_version: 'v2.4.1',
          new_tile_size: 2048,
        }),
      });
      fetchWorld();
    } catch (err) {
      console.error('Failed to trigger incident', err);
    }
  };

  const handleResetWorld = async () => {
    if (!lease) return;
    try {
      await apiFetch(`/api/gateway/scenario/reset/${lease.tenant_id}`, {
        method: 'POST',
      });
      setActiveRunId(null);
      fetchWorld();
    } catch (err) {
      console.error('Failed to reset world', err);
    }
  };

  const handleStartInvestigation = async () => {
    if (!lease) return;
    setRunError(null);
    try {
      const res = await apiFetch('/api/gateway/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenant_id: lease.tenant_id,
          session_id: lease.session_id,
          user_id: lease.user_id,
          objective: 'Will Shadow Protocol miss the 18:00 VFX delivery deadline?',
        }),
      });
      const data = await res.json().catch(() => null);

      // A refusal carries a usable explanation -- the demo quota says how long
      // to wait, a lease conflict says which world is not yours. Dropping the
      // response on the floor left the button looking broken instead.
      if (!res.ok) {
        setRunError(data?.detail || `Could not start the investigation (HTTP ${res.status}).`);
        return;
      }

      setActiveRunId(data.run_id);
    } catch (err) {
      console.error('Failed to start investigation', err);
      setRunError('Could not reach the gateway to start an investigation.');
    }
  };

  const handleApproveOption = async (optionId: string) => {
    if (!lease || !activeRunId) return;
    setIsExecutingApproval(true);
    try {
      await apiFetch(`/api/gateway/runs/${activeRunId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          run_id: activeRunId,
          option_id: optionId,
          tenant_id: lease.tenant_id,
          user_id: lease.user_id,
          session_id: lease.session_id,
        }),
      });

      setIsApprovalModalOpen(false);
      // Refresh world state
      setTimeout(fetchWorld, 1500);
    } catch (err) {
      console.error('Failed to execute approval', err);
    } finally {
      setIsExecutingApproval(false);
    }
  };

  const handleSwitchTenant = async (tenantId: string) => {
    if (!lease || tenantId === lease.tenant_id) return;
    // The run belongs to the world it was investigating, so it is dropped
    // rather than carried across. useRunStream clears everything on the change.
    setActiveRunId(null);
    setIsApprovalModalOpen(false);
    setWorld(null);
    await switchTenant(tenantId);
  };

  const effectiveImpact = verificationImpact || impact;
  // A verification that ran is the authority on whether the fleet recovered.
  //
  // This was an `||` chain, so a run that verified as PARTIALLY_RECOVERED --
  // workers healthy, deadline still missed because of the backlog -- fell
  // through to the heuristic below, which sees no active incident and a
  // completed run and declares "REMEDIATION VERIFIED: the fleet returned to
  // baseline". That is a claim the verification had already contradicted. The
  // heuristic is only for runs that never reached verification at all.
  const isRecovered = verificationImpact
    ? Boolean(verificationImpact.is_remediated)
    : world?.is_incident_active === false && activeRunId !== null && runState === 'COMPLETED';

  return (
    <div className="min-h-screen bg-studio-bg text-studio-fg flex flex-col">
      <Header
        lease={lease}
        tenants={tenants}
        pool={pool}
        onSwitchTenant={handleSwitchTenant}
        onRefreshTenants={refreshTenants}
        world={world}
        runState={runState}
        onTriggerIncident={handleTriggerIncident}
        onResetWorld={handleResetWorld}
        onStartInvestigation={handleStartInvestigation}
        isInvestigating={isStreaming || runState === 'RUNNING'}
        theme={theme}
        onToggleTheme={onToggleTheme}
      />

      <main className="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">
        {/* Status / Degraded Banner */}
        <StatusBanner runState={runState} isRecovered={Boolean(isRecovered)} />

        {runError && (
          <div
            role="alert"
            className="flex items-start gap-3 px-4 py-3 rounded-lg bg-studio-warning/10 border border-studio-warning/40 text-studio-warning"
          >
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <span className="text-xs font-mono leading-relaxed flex-1">{runError}</span>
            <button
              onClick={() => setRunError(null)}
              aria-label="Dismiss"
              className="text-studio-warning/70 hover:text-studio-warning shrink-0"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* Top: Delivery Countdown & Shift */}
        <PanelBoundary name="Delivery Projection">
          <DeliveryCountdown
            impact={effectiveImpact}
            world={world}
            productionDeadline={productionDeadline}
          />
        </PanelBoundary>

        {/* Middle: Production Board (Sequences & Worker Fleet) */}
        <PanelBoundary name="Production Board">
          <ProductionBoard
            world={world}
            affectedSequences={effectiveImpact?.sequences ?? []}
          />
        </PanelBoundary>

        {/* Bottom Split: Autonomous Evidence Ledger & Falsifiable Hypothesis Matrix */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <PanelBoundary name="Evidence Ledger">
            <EvidenceLedger events={events} />
          </PanelBoundary>
          <PanelBoundary name="Hypothesis Panel">
            <HypothesisPanel hypothesis={hypothesis} />
          </PanelBoundary>
        </div>

        {/* Concurrency & Gateway Telemetry Stats */}
        <PanelBoundary name="Agent Metrics">
          <AgentMetrics />
        </PanelBoundary>
      </main>

      {/* Approval Modal (Human-in-the-loop Gate) */}
      <ApprovalModal
        isOpen={isApprovalModalOpen}
        options={options}
        onApprove={handleApproveOption}
        isExecuting={isExecutingApproval}
      />
    </div>
  );
};
