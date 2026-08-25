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
        current_len = len(world.workers)
        for i in range(1, add_workers + 1):
            new_id = f"w-{current_len + i:02d}"
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
        priority_sequence = str(parameters.get("priority_sequence", "Final Chase"))
        return {
            "status": "APPLIED",
            "action": action_type,
            "tenant_id": tenant_id,
            "priority_sequence": priority_sequence,
            "message": f"Queue reprioritized to prioritize sequence '{priority_sequence}'",
        }

    else:
        raise ValueError(f"Unknown action type: {action_type}")
