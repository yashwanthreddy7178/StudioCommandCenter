"""Render Farm Control Plane API handlers."""
from __future__ import annotations

from typing import Any, Dict
from src.engine import engine
from services.common.models import ActionType
from services.common.telemetry import setup_logging

logger = setup_logging("render-sim-control")


def execute_control_action(tenant_id: str, action_type: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Executes an approved remediation on the render farm control plane."""
    world = engine.get_world(tenant_id)
    if not world:
        raise KeyError(f"Tenant world '{tenant_id}' not found")

    if action_type == ActionType.ROLLBACK_RENDERER_CONFIG.value:
        target_version = str(parameters.get("target_version", "v2.4.0"))
        target_tile_size = int(parameters.get("target_tile_size", 256))
        engine.rollback_renderer(tenant_id, target_version=target_version, target_tile_size=target_tile_size)
        return {
            "status": "APPLIED",
            "action": action_type,
            "tenant_id": tenant_id,
            "target_version": target_version,
            "target_tile_size": target_tile_size,
            "message": f"Successfully rolled back renderer to {target_version} (tile_size={target_tile_size})",
        }

    elif action_type == ActionType.DRAIN_WORKER.value:
        worker_id = str(parameters.get("worker_id", ""))
        if worker_id in world.workers:
            world.workers[worker_id].is_drained = True
            return {
                "status": "APPLIED",
                "action": action_type,
                "tenant_id": tenant_id,
                "worker_id": worker_id,
                "message": f"Worker {worker_id} successfully drained from queue",
            }
        else:
            return {
                "status": "FAILED",
                "action": action_type,
                "tenant_id": tenant_id,
                "error": f"Worker {worker_id} not found in tenant fleet",
            }

    elif action_type == ActionType.SCALE_RENDER_WORKERS.value:
        add_workers = int(parameters.get("additional_workers", 4))
        # Numbered from the highest id in use, not from the worker count. With a
        # count, draining or removing a worker lowers it and the next scale-up
        # regenerates an id that already exists, overwriting a live worker
        # instead of adding one.
        highest = 0
        for existing_id in world.workers:
            _, _, suffix = existing_id.rpartition("-")
            if suffix.isdigit():
                highest = max(highest, int(suffix))

        for i in range(1, add_workers + 1):
            new_id = f"w-{highest + i:02d}"
            # Add healthy RTX 4090
            from src.models import RenderWorkerNode
            world.workers[new_id] = RenderWorkerNode(
                worker_id=new_id,
                tenant_id=tenant_id,
                gpu_type="NVIDIA RTX 4090",
                renderer_version=world.renderer_version,
                tile_size=world.tile_size,
                gpu_utilization_pct=95.0,
                current_frame_duration_sec=22.0,
            )
        return {
            "status": "APPLIED",
            "action": action_type,
            "tenant_id": tenant_id,
            "added_workers": add_workers,
            "total_workers": len(world.workers),
            "message": f"Scaled fleet by {add_workers} workers. Total active: {len(world.workers)}",
        }

    elif action_type == ActionType.REPRIORITIZE_QUEUE.value:
        # The planner sends `priority_sequences`, a list taken from the impact
        # projection. This read `priority_sequence` -- singular -- so it never
        # matched, silently fell back to a hardcoded "Final Chase", and reported
        # that name back whatever the projection had actually flagged. The
        # singular spelling is still accepted so an older caller keeps working.
        sequences = parameters.get("priority_sequences")
        if isinstance(sequences, str):
            sequences = [sequences]
        if not sequences:
            single = parameters.get("priority_sequence")
            sequences = [str(single)] if single else []
        sequences = [str(name) for name in sequences if str(name).strip()]

        if not sequences:
            return {
                "status": "FAILED",
                "action": action_type,
                "tenant_id": tenant_id,
                "error": "No sequences given to prioritize",
            }

        # Recorded on the world rather than only described in the response: this
        # branch used to return APPLIED without touching anything.
        world.priority_sequences = sequences
        return {
            "status": "APPLIED",
            "action": action_type,
            "tenant_id": tenant_id,
            "priority_sequences": sequences,
            # Says what actually changed. Reordering the queue moves these shots
            # ahead of the rest; it adds no capacity, so fleet throughput is
            # unchanged and claiming otherwise would not be substantiable.
            "message": (
                f"Queue reordered to drain {', '.join(sequences)} first. "
                "Fleet throughput is unchanged; the remaining sequences move behind."
            ),
        }

    else:
        raise ValueError(f"Unknown action type: {action_type}")
