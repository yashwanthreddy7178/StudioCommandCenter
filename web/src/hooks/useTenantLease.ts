import { useState, useEffect, useCallback } from 'react';
import { TenantLease } from '../types/api';
import { apiFetch } from '../lib/auth';

/** Tenant worlds and whether each is currently held by someone. */
export interface TenantOption {
  tenant_id: string;
  leased: boolean;
}

/** How much of the pool is left, for an at-a-glance count. */
export interface PoolStatus {
  free: number;
  total: number;
  observerSessions: number;
}

const SESSION_KEY = 'studio_session_id';
const PREFERRED_TENANT_KEY = 'studio_preferred_tenant';

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // A browser refusing storage still works for this session.
  }
}

export function useTenantLease() {
  const [lease, setLease] = useState<TenantLease | null>(null);
  const [tenants, setTenants] = useState<TenantOption[]>([]);
  const [pool, setPool] = useState<PoolStatus | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Generate or retrieve persistent browser session ID
  const getSessionId = useCallback(() => {
    let sid = readStored(SESSION_KEY);
    if (!sid) {
      sid = 'sess-' + Math.random().toString(36).substring(2, 10);
      writeStored(SESSION_KEY, sid);
    }
    return sid;
  }, []);

  /** Acquires a lease, optionally asking for a specific world.
   *
   * The choice is remembered so a reload returns to the same world rather than
   * to whichever happens to be free, which matters when a local stack and a
   * deployed one share a Grafana instance and have to stay on separate tenants.
   */
  const acquireLease = useCallback(
    async (preferredTenantId?: string) => {
      setLoading(true);
      setError(null);
      const sessionId = getSessionId();
      const preferred = preferredTenantId ?? readStored(PREFERRED_TENANT_KEY) ?? undefined;

      try {
        const res = await apiFetch('/api/gateway/leases/acquire', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: sessionId,
            user_id: 'usr-supervisor',
            preferred_tenant_id: preferred ?? null,
          }),
        });

        if (!res.ok) {
          throw new Error(`Failed to acquire lease: ${res.statusText}`);
        }

        const data: TenantLease = await res.json();
        setLease(data);

        // Remember what was actually granted, not what was asked for: a
        // requested world can already be taken, and the server falls back.
        if (!data.is_observer) writeStored(PREFERRED_TENANT_KEY, data.tenant_id);
        if (preferredTenantId && data.tenant_id !== preferredTenantId) {
          setError(`Tenant ${preferredTenantId} is in use; assigned ${data.tenant_id}.`);
        }
      } catch (err: any) {
        setError(err.message || 'Lease acquisition error');
      } finally {
        setLoading(false);
      }
    },
    [getSessionId],
  );

  /** Refreshes which worlds are free, for the picker. */
  const refreshTenants = useCallback(async () => {
    try {
      const res = await apiFetch('/api/gateway/tenants');
      if (!res.ok) return;
      const data = await res.json();
      setTenants(data.tenants ?? []);
      setPool({
        free: data.free ?? 0,
        total: data.total ?? 0,
        observerSessions: data.observer_sessions ?? 0,
      });
    } catch {
      // The picker falls back to showing only the current tenant, and the count
      // is left absent rather than shown as a stale or invented number.
      setPool(null);
    }
  }, []);

  // Availability goes stale the moment someone else takes a world, and a picker
  // that is wrong at the moment of choosing is worse than one that admits it.
  // Polled on the same cadence as the heartbeat below.
  useEffect(() => {
    const interval = setInterval(refreshTenants, 30000);
    return () => clearInterval(interval);
  }, [refreshTenants]);

  // Periodic heartbeat every 30s
  useEffect(() => {
    if (!lease) return;

    const interval = setInterval(async () => {
      try {
        await apiFetch('/api/gateway/leases/heartbeat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tenant_id: lease.tenant_id, session_id: lease.session_id }),
        });
      } catch (err) {
        console.warn('Lease heartbeat failed', err);
      }
    }, 30000);

    return () => clearInterval(interval);
  }, [lease]);

  useEffect(() => {
    acquireLease();
    refreshTenants();
  }, [acquireLease, refreshTenants]);

  return {
    lease,
    tenants,
    pool,
    loading,
    error,
    refreshLease: acquireLease,
    refreshTenants,
    /** Moves this session to another tenant world. */
    switchTenant: async (tenantId: string) => {
      await acquireLease(tenantId);
      await refreshTenants();
    },
  };
}
