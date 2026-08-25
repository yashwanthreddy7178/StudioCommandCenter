"""Simulation models and Blender benchmark grounding data for render-sim."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


@dataclass(frozen=True)
class GPUBenchmarkProfile:
    """Grounded hardware profile derived from Blender Open Data (opendata.blender.org)."""
    gpu_type: str
    vram_mb: int
    cycles_score: float         # Blender Open Data benchmark score
    baseline_duration_sec: float # Average seconds to render 1 complex 4K frame
    baseline_gpu_util: float     # Expected GPU load under normal rendering
    baseline_temp_c: float       # Operating temperature in Celsius


# Blender Open Data benchmark profiles for production farm nodes
GPU_PROFILES: Dict[str, GPUBenchmarkProfile] = {
    "NVIDIA RTX 4090": GPUBenchmarkProfile(
        gpu_type="NVIDIA RTX 4090",
        vram_mb=24576,
        cycles_score=12500.0,
        baseline_duration_sec=22.0,
        baseline_gpu_util=94.5,
        baseline_temp_c=68.0,
    ),
    "NVIDIA A100-SXM4-80GB": GPUBenchmarkProfile(
        gpu_type="NVIDIA A100-SXM4-80GB",
        vram_mb=81920,
        cycles_score=11800.0,
        baseline_duration_sec=24.0,
        baseline_gpu_util=92.0,
        baseline_temp_c=62.0,
    ),
    "NVIDIA RTX 3080": GPUBenchmarkProfile(
        gpu_type="NVIDIA RTX 3080",
        vram_mb=10240,
        cycles_score=6200.0,
        baseline_duration_sec=42.0,
        baseline_gpu_util=96.0,
        baseline_temp_c=74.0,
    ),
}


class RenderWorkerNode(BaseModel):
    """In-memory state of a worker node in a tenant world."""
    worker_id: str
    tenant_id: str
    gpu_type: str
    renderer_version: str = "v2.4.0"
    tile_size: int = 256
    gpu_utilization_pct: float = 94.0
    gpu_memory_used_mb: float = 14200.0
    temperature_celsius: float = 68.0
    cpu_utilization_pct: float = 35.0
    memory_used_mb: float = 24000.0
    active_jobs: int = 1
    completed_frames: int = 0
    failed_frames: int = 0
    current_frame_duration_sec: float = 22.0
    is_degraded: bool = False
    degraded_reason: Optional[str] = None
    is_drained: bool = False


class IncidentTriggerRequest(BaseModel):
    """Payload to inject an incident scenario into a specific tenant world."""
    tenant_id: str
    scenario_type: str = "renderer_tile_regression"
    affected_worker_ids: List[str] = Field(default_factory=lambda: ["w-03", "w-07", "w-11", "w-17"])
    new_renderer_version: str = "v2.4.1"
    new_tile_size: int = 2048


class ControlActionRequest(BaseModel):
    """Payload sent by action-executor to the render control plane."""
    tenant_id: str
    action_type: str
    parameters: Dict[str, object] = Field(default_factory=dict)
    run_id: Optional[str] = None
