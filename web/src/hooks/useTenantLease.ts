import { useState, useEffect, useCallback } from 'react';
import { TenantLease } from '../types/api';

export function useTenantLease() {
  const [lease, setLease] = useState<TenantLease | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Generate or retrieve persistent browser session ID
  const getSessionId = useCallback(() => {
    let sid = localStorage.getItem('studio_session_id');
    if (!sid) {
      sid = 'sess-' + Math.random().toString(36).substring(2, 10);
      localStorage.setItem('studio_session_id', sid);
    }
    return sid;
  }, []);

  const acquireLease = useCallback(async () => {
    setLoading(true);
    setError(null);
    const sessionId = getSessionId();

    try {
      const res = await fetch('/api/gateway/leases/acquire', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, user_id: 'usr-supervisor' }),
      });

      if (!res.ok) {
        throw new Error(`Failed to acquire lease: ${res.statusText}`);
      }

      const data: TenantLease = await res.json();
      setLease(data);
    } catch (err: any) {
      setError(err.message || 'Lease acquisition error');
    } finally {
      setLoading(false);
    }
  }, [getSessionId]);

  // Periodic heartbeat every 30s
  useEffect(() => {
    if (!lease) return;

    const interval = setInterval(async () => {
      try {
        await fetch('/api/gateway/leases/heartbeat', {
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
  }, [acquireLease]);

  return { lease, loading, error, refreshLease: acquireLease };
}
