"""SQLAlchemy database schema for production metadata."""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Association table between shots and deliverables
shot_deliverable = Table(
    "shot_deliverable",
    Base.metadata,
    Column("shot_id", String(64), ForeignKey("shots.shot_id"), primary_key=True),
    Column("deliverable_id", String(64), ForeignKey("deliverables.deliverable_id"), primary_key=True),
)


class Production(Base):
    __tablename__ = "productions"

    production_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    studio: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="IN_PRODUCTION")

    sequences: Mapped[list[Sequence]] = relationship("Sequence", back_populates="production")
    deliverables: Mapped[list[Deliverable]] = relationship("Deliverable", back_populates="production")


class Sequence(Base):
    __tablename__ = "sequences"

    sequence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    production_id: Mapped[str] = mapped_column(String(64), ForeignKey("productions.production_id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    production: Mapped[Production] = relationship("Production", back_populates="sequences")
    scenes: Mapped[list[Scene]] = relationship("Scene", back_populates="sequence")


class Scene(Base):
    __tablename__ = "scenes"

    scene_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence_id: Mapped[str] = mapped_column(String(64), ForeignKey("sequences.sequence_id"))
    code: Mapped[str] = mapped_column(String(64), nullable=False)

    sequence: Mapped[Sequence] = relationship("Sequence", back_populates="scenes")
    shots: Mapped[list[Shot]] = relationship("Shot", back_populates="scene")


class Shot(Base):
    __tablename__ = "shots"

    shot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scene_id: Mapped[str] = mapped_column(String(64), ForeignKey("scenes.scene_id"))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    frame_start: Mapped[int] = mapped_column(Integer, default=1)
    frame_end: Mapped[int] = mapped_column(Integer, default=100)
    priority: Mapped[int] = mapped_column(Integer, default=0) # 1 = High, 0 = Normal
    status: Mapped[str] = mapped_column(String(64), default="QUEUED")

    scene: Mapped[Scene] = relationship("Scene", back_populates="shots")
    deliverables: Mapped[list[Deliverable]] = relationship("Deliverable", secondary=shot_deliverable, back_populates="shots")
    render_jobs: Mapped[list[RenderJob]] = relationship("RenderJob", back_populates="shot")


class Deliverable(Base):
    __tablename__ = "deliverables"

    deliverable_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    production_id: Mapped[str] = mapped_column(String(64), ForeignKey("productions.production_id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    deadline_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    production: Mapped[Production] = relationship("Production", back_populates="deliverables")
    shots: Mapped[list[Shot]] = relationship("Shot", secondary=shot_deliverable, back_populates="deliverables")


class RenderJob(Base):
    __tablename__ = "render_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shot_id: Mapped[str] = mapped_column(String(64), ForeignKey("shots.shot_id"))
    worker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    frame: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(64), default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    shot: Mapped[Shot] = relationship("Shot", back_populates="render_jobs")
