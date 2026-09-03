"""Multi-tenant simulation engine managing all isolated worlds."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from src.config import settings
from src.world import BASELINE_QUEUE_DEPTH, TenantProductionWorld
from src.otel_export import OTelTelemetryExporter
from services.common.telemetry import setup_logging

logger = setup_logging("render-sim-engine")


class SimulationEngine:
    """Orchestrates 24 tenant worlds + observer world in a single process."""

    def __init__(self) -> None:
        self.worlds: Dict[str, TenantProductionWorld] = {}
        self.exporter = OTelTelemetryExporter()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._mirror_task: Optional[asyncio.Task] = None
        self._initialize_worlds()

    def _initialize_worlds(self) -> None:
        """Initializes 24 isolated tenant worlds plus the shared observer world."""
        # 24 tenant worlds
        for i in range(1, settings.num_tenant_worlds + 1):
            tenant_id = f"t{i:02d}"
            self.worlds[tenant_id] = TenantProductionWorld(tenant_id=tenant_id, num_workers=8)

        # Observer world
        if settings.enable_observer_world:
            self.worlds["observer"] = TenantProductionWorld(tenant_id="observer", num_workers=8)

        logger.info(
            "Initialized multi-tenant simulation worlds",
            extra={"tenant_count": len(self.worlds)}
        )

    def get_world(self, tenant_id: str) -> Optional[TenantProductionWorld]:
        """Retrieves a world by tenant ID."""
        return self.worlds.get(tenant_id)

    async def start(self) -> None:
        """Starts the background simulation loop and periodic Firestore mirroring."""
        self._running = True
        self._task = asyncio.create_task(self._simulation_loop())
        logger.info("Simulation engine started")

    async def stop(self) -> None:
        """Gracefully stops the simulation engine."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.exporter.close()
        logger.info("Simulation engine stopped")

    async def _simulation_loop(self) -> None:
        """Periodic simulation tick across all worlds."""
        while self._running:
            try:
                for world in self.worlds.values():
                    world.tick()
                    await self.exporter.emit_world_telemetry(world)
                await asyncio.sleep(settings.simulation_tick_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in simulation tick", extra={"error": str(exc)})
                await asyncio.sleep(1.0)

    def trigger_incident(
        self,
        tenant_id: str,
        scenario_type: str = "renderer_tile_regression",
        affected_worker_ids: Optional[List[str]] = None,
        new_version: str = "v2.4.1",
        new_tile_size: int = 2048,
    ) -> TenantProductionWorld:
        """Injects a regression scenario into the specified tenant world."""
        world = self.worlds.get(tenant_id)
        if not world:
            raise KeyError(f"Tenant world '{tenant_id}' not found")

        # Four of the eight workers, drawn from the two high-priority sequence
        # pools in the production metadata. The previous default named w-11 and
        # w-17, which do not exist in an eight-worker world, so half the intended
        # incident silently did nothing.
        target_workers = affected_worker_ids or ["w-01", "w-03", "w-04", "w-06"]
        world.trigger_incident(
            scenario_type=scenario_type,
            affected_worker_ids=target_workers,
            new_version=new_version,
            new_tile_size=new_tile_size,
        )
        # Written once, at the moment of the change, so its timestamp precedes the
        # metric inflection rather than being inferred from it.
        self.exporter.emit_config_event(
            world,
            event="renderer_config_loaded",
            affected_worker_ids=target_workers,
        )
        logger.info(
            "Incident triggered on tenant world",
            extra={"tenant_id": tenant_id, "scenario": scenario_type, "affected_workers": target_workers}
        )
        return world

    def rollback_renderer(
        self,
        tenant_id: str,
        target_version: str = "v2.4.0",
        target_tile_size: int = 256,
    ) -> TenantProductionWorld:
        """Applies rollback remediation to the specified tenant world."""
        world = self.worlds.get(tenant_id)
        if not world:
            raise KeyError(f"Tenant world '{tenant_id}' not found")

        world.rollback_renderer(target_version=target_version, target_tile_size=target_tile_size)
        self.exporter.emit_config_event(world, event="renderer_config_rolled_back")
        logger.info(
            "Renderer rolled back on tenant world",
            extra={"tenant_id": tenant_id, "target_version": target_version, "tile_size": target_tile_size}
        )
        return world

    def reset_world(self, tenant_id: str) -> TenantProductionWorld:
        """Resets a tenant world back to clean baseline state."""
        world = self.worlds.get(tenant_id)
        if not world:
            raise KeyError(f"Tenant world '{tenant_id}' not found")

        world.rollback_renderer()
        world.queue_depth = BASELINE_QUEUE_DEPTH
        world.observed_throughput_fpm = world.baseline_throughput_fpm
        logger.info("Reset tenant world to baseline", extra={"tenant_id": tenant_id})
        return world


# Global singleton simulation engine
engine = SimulationEngine()
