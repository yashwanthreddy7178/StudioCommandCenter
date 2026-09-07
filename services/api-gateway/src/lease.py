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

    async def acquire_lease(self, session_id: str, user_id: str = "usr-coordinator") -> TenantLease:
        """Assigns an available tenant world or attaches in observer mode."""
        async with self._lock:
            now = utc_now()
            ttl = timedelta(seconds=settings.tenant_lease_ttl_sec)

            # 1. Check if this session already holds an active lease
            for tenant_id, lease in self._leases.items():
                if lease.session_id == session_id and lease.expires_at > now:
                    lease.heartbeat_at = now
                    lease.expires_at = now + ttl
                    return lease

            # 2. Look for an available writable tenant from t01 to t24
            for i in range(1, settings.num_tenant_worlds + 1):
                tenant_id = f"t{i:02d}"
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
