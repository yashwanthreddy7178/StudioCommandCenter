"""Seed script to populate realistic VFX production metadata."""
from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.schema import (
    Deliverable,
    Production,
    RenderJob,
    Scene,
    Sequence,
    Shot,
)


def _shot_status(index: int, total: int, complete_through: float) -> str:
    """Assigns a shot lifecycle state by position within its sequence.

    A production in flight has finished, in-progress and queued shots. The
    proportions differ per sequence so the board shows real variation, and the UI
    counts these rather than being told a percentage.
    """
    completed_count = int(total * complete_through)
    if index <= completed_count:
        return "COMPLETE"
    if index <= completed_count + 40:
        return "RENDERING"
    return "QUEUED"


async def seed_production_database(session: AsyncSession) -> None:
    """Populates production metadata if tables are empty."""
    # Check if production already exists
    res = await session.execute(select(Production).limit(1))
    if res.scalars().first() is not None:
        return

    # Base deadline 18:00 UTC today
    now = datetime.utcnow()
    deadline = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if deadline < now:
        deadline = now + timedelta(hours=3, minutes=5)

    # 1. Production
    prod = Production(
        production_id="prod-shadow-protocol",
        title="Shadow Protocol",
        studio="Apex VFX Studios",
        status="IN_PRODUCTION",
    )
    session.add(prod)

    # 2. Deliverables
    deliv1 = Deliverable(
        deliverable_id="SP_VFX_R04",
        production_id="prod-shadow-protocol",
        name="Shadow Protocol - 4K Review Master R04",
        deadline_utc=deadline,
    )
    session.add(deliv1)

    # 3. Sequences
    seq_chase = Sequence(sequence_id="seq-chase", production_id="prod-shadow-protocol", name="Final Chase")
    seq_rooftop = Sequence(sequence_id="seq-rooftop", production_id="prod-shadow-protocol", name="Rooftop Pursuit")
    seq_lab = Sequence(sequence_id="seq-lab", production_id="prod-shadow-protocol", name="Laboratory Infiltration")
    session.add_all([seq_chase, seq_rooftop, seq_lab])

    # 4. Scenes
    sc_chase = Scene(scene_id="sc-04-chase", sequence_id="seq-chase", code="SC_04_CHASE")
    sc_roof = Scene(scene_id="sc-02-rooftop", sequence_id="seq-rooftop", code="SC_02_ROOF")
    sc_lab = Scene(scene_id="sc-01-lab", sequence_id="seq-lab", code="SC_01_LAB")
    session.add_all([sc_chase, sc_roof, sc_lab])

    # 5. Shots
    created_shots = []
    # 1200 shots for Chase (High priority)
    for i in range(1, 1201):
        shot = Shot(
            shot_id=f"shot-chase-{i:04d}",
            scene_id="sc-04-chase",
            code=f"CHASE_{i:04d}",
            frame_start=1001,
            frame_end=1120,
            priority=1,
            status=_shot_status(i, 1200, complete_through=0.62),
        )
        shot.deliverables.append(deliv1)
        created_shots.append(shot)

    # 642 shots for Rooftop (High priority)
    for i in range(1, 643):
        shot = Shot(
            shot_id=f"shot-roof-{i:04d}",
            scene_id="sc-02-rooftop",
            code=f"ROOF_{i:04d}",
            frame_start=1001,
            frame_end=1096,
            priority=1,
            status=_shot_status(i, 642, complete_through=0.71),
        )
        shot.deliverables.append(deliv1)
        created_shots.append(shot)

    # 800 shots for Lab (Normal priority)
    for i in range(1, 801):
        shot = Shot(
            shot_id=f"shot-lab-{i:04d}",
            scene_id="sc-01-lab",
            code=f"LAB_{i:04d}",
            frame_start=1001,
            frame_end=1080,
            priority=0,
            status=_shot_status(i, 800, complete_through=0.12),
        )
        created_shots.append(shot)

    session.add_all(created_shots)

    # 6. Render jobs. Each worker takes frames from several shots spread across
    # all three sequences, which is how a render farm actually schedules work.
    # Assigning every worker a shot from the same sequence would make the
    # worker-to-deliverable join incapable of distinguishing anything.
    by_scene = {
        "sc-04-chase": [s for s in created_shots if s.scene_id == "sc-04-chase"],
        "sc-02-rooftop": [s for s in created_shots if s.scene_id == "sc-02-rooftop"],
        "sc-01-lab": [s for s in created_shots if s.scene_id == "sc-01-lab"],
    }

    # Workers are pooled per sequence, the way a farm actually schedules. If every
    # worker drew from every sequence, any set of failing workers would implicate
    # all of them and the worker-to-deliverable join could not localise anything.
    worker_pools = {
        "w-01": "sc-04-chase", "w-02": "sc-04-chase", "w-03": "sc-04-chase",
        "w-04": "sc-02-rooftop", "w-05": "sc-02-rooftop", "w-06": "sc-02-rooftop",
        "w-07": "sc-01-lab", "w-08": "sc-01-lab",
    }

    jobs = []
    for idx, (worker_id, scene_id) in enumerate(worker_pools.items()):
        scene_shots = by_scene[scene_id]
        for job_no in range(4):
            shot = scene_shots[(idx * 7 + job_no * 3) % len(scene_shots)]
            jobs.append(
                RenderJob(
                    job_id=f"job-{worker_id}-{job_no:02d}",
                    shot_id=shot.shot_id,
                    worker_id=worker_id,
                    frame=1042 + job_no,
                    state="RUNNING",
                    started_at=now - timedelta(minutes=10 + job_no),
                )
            )

    session.add_all(jobs)
    await session.commit()
