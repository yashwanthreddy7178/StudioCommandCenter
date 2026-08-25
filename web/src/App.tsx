import React, { useState, useEffect, useCallback } from 'react';
import { Header } from './components/Header';
import { DeliveryCountdown } from './components/DeliveryCountdown';
import { ProductionBoard } from './components/ProductionBoard';
import { EvidenceLedger } from './components/EvidenceLedger';
import { HypothesisPanel } from './components/HypothesisPanel';
import { ApprovalModal } from './components/ApprovalModal';
import { StatusBanner } from './components/StatusBanner';
import { AgentMetrics } from './components/AgentMetrics';
import { useTenantLease } from './hooks/useTenantLease';
import { useRunStream } from './hooks/useRunStream';
import { WorldState } from './types/api';

export const App: React.FC = () => {
  const { lease } = useTenantLease();
  const [world, setWorld] = useState<WorldState | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [isExecutingApproval, setIsExecutingApproval] = useState<boolean>(false);
  const [isApprovalModalOpen, setIsApprovalModalOpen] = useState<boolean>(false);

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
  const fetchWorld = useCallback(async () => {
    if (!lease) return;
    try {
      const res = await fetch(`/api/sim/worlds/${lease.tenant_id}`);
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
      await fetch('/api/gateway/scenario/trigger-incident', {
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
      await fetch(`/api/gateway/scenario/reset/${lease.tenant_id}`, {
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
    try {
      const res = await fetch('/api/gateway/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenant_id: lease.tenant_id,
          session_id: lease.session_id,
          user_id: lease.user_id,
          objective: 'Will Shadow Protocol miss the 18:00 VFX delivery deadline?',
        }),
      });
      const data = await res.json();
      setActiveRunId(data.run_id);
    } catch (err) {
      console.error('Failed to start investigation', err);
    }
  };

  const handleApproveOption = async (optionId: string) => {
    if (!lease || !activeRunId) return;
    setIsExecutingApproval(true);
    try {
      await fetch(`/api/gateway/runs/${activeRunId}/approve`, {
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

  const effectiveImpact = verificationImpact || impact;
  const isRecovered = verificationImpact?.is_remediated || (world?.is_incident_active === false && activeRunId !== null && runState === 'COMPLETED');

  return (
    <div className="min-h-screen bg-studio-bg text-slate-100 flex flex-col">
      <Header
        lease={lease}
        world={world}
        runState={runState}
        onTriggerIncident={handleTriggerIncident}
        onResetWorld={handleResetWorld}
        onStartInvestigation={handleStartInvestigation}
        isInvestigating={isStreaming || runState === 'RUNNING'}
      />

      <main className="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">
        {/* Status / Degraded Banner */}
        <StatusBanner runState={runState} isRecovered={Boolean(isRecovered)} />

        {/* Top: Delivery Countdown & Shift */}
        <DeliveryCountdown impact={effectiveImpact} world={world} />

        {/* Middle: Production Board (Sequences & Worker Fleet) */}
        <ProductionBoard world={world} />

        {/* Bottom Split: Autonomous Evidence Ledger & Falsifiable Hypothesis Matrix */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <EvidenceLedger events={events} />
          <HypothesisPanel hypothesis={hypothesis} />
        </div>

        {/* Concurrency & Gateway Telemetry Stats */}
        <AgentMetrics />
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
