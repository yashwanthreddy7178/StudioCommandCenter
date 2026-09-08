"""Tenant lease pool manager with 20-minute TTL, heartbeats, and observer fallback."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Dict, List, Optional
from src.config import settings
from services.common.models import TenantLease, TenantStatus
from services.common.telemetry import setup_logging
from services.common.timeutil import utc_now

logger = setup_logging("api-gateway-lease")


class TenantLeaseManager:
    """Manages multi-tenant isolation leases for up to 24 concurrent active studios + observer."""

    def __init__(self) -> None:
        self._leases: Dict[str, TenantLease] = {} # tenant_id -> TenantLease
        self._lock = asyncio.Lock()

    def writable_tenants(self) -> List[str]:
        """Every tenant world a session can be assigned, in order."""
        return [f"t{i:02d}" for i in range(1, settings.num_tenant_worlds + 1)]

    async def acquire_lease(
        self,
        session_id: str,
        user_id: str = "usr-coordinator",
        preferred_tenant_id: Optional[str] = None,
    ) -> TenantLease:
        """Assigns a tenant world, honouring a request for a specific one.

        `preferred_tenant_id` exists so an operator can choose which world to
        work in rather than taking whichever happened to be free. Two people
        demonstrating at once need to be told apart, and a local stack sharing a
        Grafana instance with a deployed one wants a tenant of its own. A
        preference that is taken by someone else falls back to the normal search
        rather than failing: the point is to pick when you can, not to queue.
        """
        async with self._lock:
            now = utc_now()
            ttl = timedelta(seconds=settings.tenant_lease_ttl_sec)

            # 1. Check if this session already holds an active lease.
            for tenant_id, lease in self._leases.items():
                if lease.session_id == session_id and lease.expires_at > now:
                    # A session asking for a different world releases the one it
                    # holds; without this the early return below would hand back
                    # the old lease and a tenant switch would silently do nothing.
                    if preferred_tenant_id and lease.tenant_id != preferred_tenant_id:
                        held = self._leases.get(preferred_tenant_id)
                        if held is None or held.expires_at <= now or held.session_id == session_id:
                            del self._leases[tenant_id]
                            break
                    lease.heartbeat_at = now
                    lease.expires_at = now + ttl
                    return lease

            # 2. The requested world first, then the rest in order.
            candidates = self.writable_tenants()
            if preferred_tenant_id in candidates:
                candidates.remove(preferred_tenant_id)
                candidates.insert(0, preferred_tenant_id)

            for tenant_id in candidates:
                existing = self._leases.get(tenant_id)
                if existing is None or existing.expires_at <= now:
                    new_lease = TenantLease(
                        tenant_id=tenant_id,
                        session_id=session_id,
                        user_id=user_id,
                        leased_at=now,
                        heartbeat_at=now,
                        expires_at=now + ttl,
                        is_observer=False,
                        status=TenantStatus.LEASED,
                    )
                    self._leases[tenant_id] = new_lease
                    logger.info("Assigned writable tenant lease", extra={"tenant_id": tenant_id, "session_id": session_id})
                    return new_lease

            # 3. All 24 writable leases occupied -> Fallback to shared observer world
            observer_lease = TenantLease(
                tenant_id="observer",
                session_id=session_id,
                user_id=user_id,
                leased_at=now,
                heartbeat_at=now,
                expires_at=now + ttl,
                is_observer=True,
                status=TenantStatus.OBSERVER,
            )
            self._leases[f"obs-{session_id}"] = observer_lease
            logger.info("Pool exhausted: assigned observer mode lease", extra={"session_id": session_id})
            return observer_lease

    @staticmethod
    def _key(tenant_id: str, session_id: str) -> str:
        """The dict key holding a lease, which is not always the tenant id.

        Writable leases are keyed by tenant because one tenant world has one
        holder. Observer leases all name the same shared world, so they are keyed
        per session instead -- and every lookup by tenant id therefore missed
        them entirely. Heartbeats from an observer session silently returned
        False and its lease expired underneath it while the browser was still
        sending them.
        """
        if tenant_id == "observer":
            return f"obs-{session_id}"
        return tenant_id

    async def heartbeat(self, tenant_id: str, session_id: str) -> bool:
        """Extends the lease TTL via periodic client heartbeat."""
        async with self._lock:
            now = utc_now()
            lease = self._leases.get(self._key(tenant_id, session_id))
            if lease and lease.session_id == session_id and lease.expires_at > now:
                lease.heartbeat_at = now
                lease.expires_at = now + timedelta(seconds=settings.tenant_lease_ttl_sec)
                return True
            return False

    async def holds_lease(self, tenant_id: str, session_id: str) -> bool:
        """Reports whether this session currently holds a live lease on a tenant.

        Separate from `heartbeat`, which answers the same question but extends
        the lease as a side effect. A permission check must not renew the thing
        it is checking.
        """
        async with self._lock:
            lease = self._leases.get(self._key(tenant_id, session_id))
            return bool(
                lease
                and lease.session_id == session_id
                and lease.expires_at > utc_now()
            )

    async def get_lease(self, tenant_id: str, session_id: str) -> Optional[TenantLease]:
        """Returns the live lease this session holds on a tenant, if any.

        `holds_lease` answers yes or no; callers that need to know *what kind* of
        lease it is -- writable or observer -- need the record itself.
        """
        async with self._lock:
            lease = self._leases.get(self._key(tenant_id, session_id))
            if lease and lease.session_id == session_id and lease.expires_at > utc_now():
                return lease
            return None

    async def release_lease(self, tenant_id: str, session_id: str) -> bool:
        """Releases a tenant lease back to the pool."""
        async with self._lock:
            key = self._key(tenant_id, session_id)
            lease = self._leases.get(key)
            if lease and lease.session_id == session_id:
                del self._leases[key]
                logger.info("Released tenant lease", extra={"tenant_id": tenant_id, "session_id": session_id})
                return True
            return False

    async def get_active_leases(self) -> List[TenantLease]:
        """Returns all currently active tenant leases."""
        async with self._lock:
            now = utc_now()
            return [l for l in self._leases.values() if l.expires_at > now]


lease_manager = TenantLeaseManager()
