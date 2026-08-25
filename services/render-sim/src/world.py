"""Tenant production world state and discrete-event simulation logic."""
from __future__ import annotations

import random
from datetime import datetime
from typing import Dict, List, Optional
from src.models import GPU_PROFILES, RenderWorkerNode


class TenantProductionWorld:
    """Represents the complete simulated render farm state for a single tenant."""

    def __init__(self, tenant_id: str, num_workers: int = 8) -> None:
        self.tenant_id = tenant_id
        self.production_id = "prod-shadow-protocol"
        self.title = "Shadow Protocol"
        self.renderer_version = "v2.4.0"
        self.tile_size = 256
        self.is_incident_active = False
        self.incident_type: Optional[str] = None
        self.queue_depth = 18432
        self.last_updated = datetime.utcnow()
        self.workers: Dict[str, RenderWorkerNode] = {}

        self._initialize_workers(num_workers)

        # Derived from the same grounded GPU profiles the workers run on, so a
        # healthy fleet sits at its own baseline. A hardcoded constant here would
        # make every world read as permanently degraded and would feed a bogus
        # shortfall into the impact projection.
        self.baseline_throughput_fpm = self._fleet_throughput_fpm(baseline=True)
        self.observed_throughput_fpm = self.baseline_throughput_fpm

    def _fleet_throughput_fpm(self, baseline: bool = False) -> float:
        """Frames per minute across the fleet.

        The sum of per-worker rates, not the mean duration scaled by worker count:
        a slow worker contributes fewer frames, it does not slow the others down.
        """
        total = 0.0
        for worker in self.workers.values():
            if worker.is_drained:
                continue
            if baseline:
                duration = GPU_PROFILES[worker.gpu_type].baseline_duration_sec
            else:
                duration = worker.current_frame_duration_sec
            if duration > 0:
                total += 60.0 / duration
        return round(total, 1)

    def _initialize_workers(self, num_workers: int) -> None:
        """Initializes worker nodes using grounded GPU benchmark profiles."""
        gpu_types = ["NVIDIA RTX 4090", "NVIDIA A100-SXM4-80GB", "NVIDIA RTX 3080"]
        for idx in range(1, num_workers + 1):
            worker_id = f"w-{idx:02d}"
            gpu_type = gpu_types[(idx - 1) % len(gpu_types)]
            profile = GPU_PROFILES[gpu_type]

            self.workers[worker_id] = RenderWorkerNode(
                worker_id=worker_id,
                tenant_id=self.tenant_id,
                gpu_type=gpu_type,
                renderer_version=self.renderer_version,
                tile_size=self.tile_size,
                gpu_utilization_pct=profile.baseline_gpu_util + random.uniform(-1.5, 1.5),
                gpu_memory_used_mb=profile.vram_mb * 0.65,
                temperature_celsius=profile.baseline_temp_c + random.uniform(-2.0, 2.0),
                cpu_utilization_pct=32.0 + random.uniform(-3.0, 3.0),
                memory_used_mb=24000.0 + random.uniform(-1000.0, 1000.0),
                active_jobs=1,
                current_frame_duration_sec=profile.baseline_duration_sec + random.uniform(-1.0, 1.0),
                is_degraded=False,
            )

    def trigger_incident(
        self,
        scenario_type: str,
        affected_worker_ids: List[str],
        new_version: str = "v2.4.1",
        new_tile_size: int = 2048,
    ) -> None:
        """Injects a render regression into specific worker nodes."""
        self.is_incident_active = True
        self.incident_type = scenario_type
        self.renderer_version = new_version
        self.tile_size = new_tile_size

        for wid in affected_worker_ids:
            if wid in self.workers:
                worker = self.workers[wid]
                worker.is_degraded = True
                worker.renderer_version = new_version
                worker.tile_size = new_tile_size
                worker.degraded_reason = f"Tile size {new_tile_size} caused GPU VRAM thrashing"
                # Mechanism: GPU utilization drops because threads stall on memory bus
                worker.gpu_utilization_pct = 28.5 + random.uniform(-2.0, 2.0)
                worker.current_frame_duration_sec = 145.0 + random.uniform(-5.0, 5.0)
                worker.temperature_celsius = 52.0 + random.uniform(-1.0, 1.0)
                worker.cpu_utilization_pct = 85.0 + random.uniform(-4.0, 4.0)

    def rollback_renderer(self, target_version: str = "v2.4.0", target_tile_size: int = 256) -> None:
        """Restores workers to baseline configuration."""
        self.is_incident_active = False
        self.incident_type = None
        self.renderer_version = target_version
        self.tile_size = target_tile_size

        for worker in self.workers.values():
            profile = GPU_PROFILES.get(worker.gpu_type, GPU_PROFILES["NVIDIA RTX 4090"])
            worker.is_degraded = False
            worker.degraded_reason = None
            worker.renderer_version = target_version
            worker.tile_size = target_tile_size
            worker.gpu_utilization_pct = profile.baseline_gpu_util + random.uniform(-1.0, 1.0)
            worker.current_frame_duration_sec = profile.baseline_duration_sec + random.uniform(-1.0, 1.0)
            worker.temperature_celsius = profile.baseline_temp_c + random.uniform(-1.0, 1.0)
            worker.cpu_utilization_pct = 32.0 + random.uniform(-2.0, 2.0)

    def tick(self) -> None:
        """Executes a discrete simulation step."""
        self.last_updated = datetime.utcnow()
        degraded_count = sum(1 for w in self.workers.values() if w.is_degraded and not w.is_drained)
        active_count = sum(1 for w in self.workers.values() if not w.is_drained)

        # Update jitter on each worker
        for worker in self.workers.values():
            if worker.is_drained:
                worker.gpu_utilization_pct = 0.0
                worker.active_jobs = 0
                continue

            profile = GPU_PROFILES.get(worker.gpu_type, GPU_PROFILES["NVIDIA RTX 4090"])
            if worker.is_degraded:
                worker.gpu_utilization_pct = max(10.0, min(40.0, 28.0 + random.uniform(-3.0, 3.0)))
                worker.current_frame_duration_sec = max(120.0, 145.0 + random.uniform(-8.0, 8.0))
            else:
                worker.gpu_utilization_pct = max(80.0, min(99.0, profile.baseline_gpu_util + random.uniform(-2.0, 2.0)))
                worker.current_frame_duration_sec = max(18.0, profile.baseline_duration_sec + random.uniform(-2.0, 2.0))

        # Recalculate fleet throughput, smoothed so a single slow frame does not
        # whipsaw the projection.
        if active_count > 0:
            current_fpm = self._fleet_throughput_fpm()
            self.observed_throughput_fpm = round(
                0.8 * self.observed_throughput_fpm + 0.2 * current_fpm, 1
            )
        
        # In degraded state, queue depth accumulates
        if degraded_count > 0:
            self.queue_depth += int(random.uniform(5, 20))
        else:
            self.queue_depth = max(100, self.queue_depth - int(random.uniform(10, 30)))

    def to_dict(self) -> Dict[str, object]:
        """Serializes world state to dict."""
        return {
            "tenant_id": self.tenant_id,
            "production_id": self.production_id,
            "title": self.title,
            "renderer_version": self.renderer_version,
            "tile_size": self.tile_size,
            "is_incident_active": self.is_incident_active,
            "incident_type": self.incident_type,
            "baseline_throughput_fpm": self.baseline_throughput_fpm,
            "observed_throughput_fpm": self.observed_throughput_fpm,
            "queue_depth": self.queue_depth,
            "total_workers": len(self.workers),
            "healthy_workers": sum(1 for w in self.workers.values() if not w.is_degraded and not w.is_drained),
            "degraded_workers": sum(1 for w in self.workers.values() if w.is_degraded and not w.is_drained),
            "workers": [w.model_dump() for w in self.workers.values()],
            "last_updated": self.last_updated.isoformat(),
        }
