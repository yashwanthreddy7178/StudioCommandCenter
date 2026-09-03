"""Pure deterministic impact calculation engine.

Guarantees 100% reproducible delivery-deadline impact projections with zero model arithmetic.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Sequence as TypingSequence, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from src.schema import Deliverable, RenderJob, Scene, Sequence, Shot
from services.common.models import ImpactProjection


def compute_deterministic_projection(
    tenant_id: str,
    affected_shots_count: int,
    high_priority_count: int,
    sequences: List[str],
    deadline_utc: datetime,
    observed_throughput_fpm: float,
    baseline_throughput_fpm: float,
    queue_depth: int,
    at_risk_deliverables: List[str],
    as_of: datetime,
) -> ImpactProjection:
    """Pure calculation function for delivery delay projection."""
    safe_throughput = max(0.1, observed_throughput_fpm)
    minutes_needed = queue_depth / safe_throughput
    projected_completion = as_of + timedelta(minutes=minutes_needed)

    diff_seconds = (projected_completion - deadline_utc).total_seconds()
    delay_minutes = max(0, int(diff_seconds // 60))

    is_remediated = (delay_minutes == 0) or (observed_throughput_fpm >= baseline_throughput_fpm * 0.95)

    method_str = (
        f"queue_depth ({queue_depth}) / observed_throughput ({observed_throughput_fpm:.1f} fpm) = "
        f"{minutes_needed:.1f} min remaining; joined against SQL production deliverables deadline"
    )

    return ImpactProjection(
        tenant_id=tenant_id,
        affected_shots=affected_shots_count,
        high_priority_shots=high_priority_count,
        sequences=sorted(sequences),
        deadline_utc=deadline_utc,
        projected_completion_utc=projected_completion,
        delay_minutes=delay_minutes,
        at_risk_deliverables=at_risk_deliverables,
        baseline_throughput_fpm=baseline_throughput_fpm,
        observed_throughput_fpm=observed_throughput_fpm,
        queue_depth=queue_depth,
        method=method_str,
        is_remediated=is_remediated,
        as_of=as_of,
    )


async def calculate_production_impact(
    session: AsyncSession,
    tenant_id: str,
    affected_workers: List[str],
    observed_throughput_fpm: float,
    baseline_throughput_fpm: float,
    queue_depth: int,
    as_of: datetime,
) -> ImpactProjection:
    """Traces failing workers through to at-risk deliverables and projects delay.

    The join is worker -> render_jobs -> shots -> scenes -> sequences, then back
    down to every unfinished shot in those sequences and the deliverables they
    feed. Nothing is assumed: when the metadata yields no match the projection
    reports zero affected shots rather than substituting a plausible figure.
    """
    # 1. What the affected workers were actually rendering.
    jobs_query = (
        select(RenderJob)
        .where(RenderJob.worker_id.in_(affected_workers))
        .options(
            selectinload(RenderJob.shot)
            .selectinload(Shot.scene)
            .selectinload(Scene.sequence)
        )
    )
    jobs_res = await session.execute(jobs_query)
    jobs = jobs_res.scalars().all()

    # 2. The sequences those shots belong to. Degrading the workers rendering a
    # sequence puts the rest of that sequence at risk, not just the frames that
    # happened to be in flight.
    affected_sequence_ids = {
        job.shot.scene.sequence_id
        for job in jobs
        if job.shot is not None and job.shot.scene is not None
    }

    affected_shots: List[Shot] = []
    sequence_names: List[str] = []

    if affected_sequence_ids:
        shots_query = (
            select(Shot)
            .join(Scene, Shot.scene_id == Scene.scene_id)
            .where(
                Scene.sequence_id.in_(affected_sequence_ids),
                Shot.status != "COMPLETE",
            )
            .options(selectinload(Shot.deliverables))
        )
        shots_res = await session.execute(shots_query)
        affected_shots = list(shots_res.scalars().all())

        seq_res = await session.execute(
            select(Sequence.name).where(Sequence.sequence_id.in_(affected_sequence_ids))
        )
        sequence_names = list(seq_res.scalars().all())

    high_priority_shots = [s for s in affected_shots if s.priority >= 1]

    # 3. The deliverables those shots feed, earliest deadline first.
    at_risk = {
        deliverable.deliverable_id: deliverable
        for shot in affected_shots
        for deliverable in shot.deliverables
    }
    ordered = sorted(at_risk.values(), key=lambda d: d.deadline_utc)

    at_risk_names = [d.deliverable_id for d in ordered]

    if ordered:
        deadline_utc = ordered[0].deadline_utc
    else:
        # Nothing traced to a deliverable, but the production still has one. The
        # deadline is a fact about the production, so the queue is still measured
        # against it; only the at-risk list is empty. Defaulting the deadline to
        # `as_of` here would report the whole drain time as delay and make a
        # healthy fleet look further behind than a degraded one.
        earliest = await session.execute(
            select(Deliverable).order_by(Deliverable.deadline_utc.asc()).limit(1)
        )
        primary = earliest.scalars().first()
        deadline_utc = primary.deadline_utc if primary else as_of

    behind_baseline = observed_throughput_fpm < baseline_throughput_fpm * 0.9
    at_risk_now = at_risk_names if behind_baseline else []

    projection = compute_deterministic_projection(
        tenant_id=tenant_id,
        affected_shots_count=len(affected_shots),
        high_priority_count=len(high_priority_shots),
        sequences=sequence_names,
        deadline_utc=deadline_utc,
        observed_throughput_fpm=observed_throughput_fpm,
        baseline_throughput_fpm=baseline_throughput_fpm,
        queue_depth=queue_depth,
        at_risk_deliverables=at_risk_now,
        as_of=as_of,
    )

    # delay_minutes answers "what does this incident cost". With no affected work
    # and nothing at risk there is no incident-attributable delay, whatever the
    # deadline happens to be: a deadline that has simply passed would otherwise
    # be reported as a large delay on a completely healthy fleet, contradicting
    # the zero affected shots reported alongside it.
    if not at_risk_now and not affected_shots:
        projection.delay_minutes = 0
        projection.is_remediated = True
        projection.method = (
            f"no work traced to the affected workers; queue of {queue_depth} frames "
            f"drains in {queue_depth / max(0.1, observed_throughput_fpm):.0f} min "
            "with no deliverable at risk"
        )

    return projection
