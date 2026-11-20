from datetime import date, datetime
from sqlalchemy import String, Date, DateTime, BigInteger, Numeric, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from ..config.database import Base


class SettlementGroup(Base):
    """A settlement group that owns its own daily database-time allowance."""
    __tablename__ = 'groups'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GroupQuota(Base):
    """Daily allowance ledger for one group.  balance = granted - used - reserved."""
    __tablename__ = 'group_quotas'
    __table_args__ = (UniqueConstraint('group_id', 'day', name='uq_group_quota_day'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey('groups.id'), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    granted_ms: Mapped[float] = mapped_column(Numeric(16, 3), default=0)
    used_ms: Mapped[float] = mapped_column(Numeric(16, 3), default=0)
    reserved_ms: Mapped[float] = mapped_column(Numeric(16, 3), default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    @property
    def balance_ms(self):
        return self.granted_ms - self.used_ms - self.reserved_ms


class QuotaReservation(Base):
    """Upper-bound hold taken by one batch before it starts executing.

    day is the day the hold was taken.  Settlement always posts against this
    row, even when the batch finishes after midnight.
    """
    __tablename__ = 'quota_reservations'
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('batches.id'), unique=True)
    group_id: Mapped[int] = mapped_column(ForeignKey('groups.id'), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    reserved_ms: Mapped[float] = mapped_column(Numeric(16, 3))
    actual_ms: Mapped[float | None] = mapped_column(Numeric(16, 3), nullable=True)
    state: Mapped[str] = mapped_column(String(20), default='reserved')
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UsageEvidence(Base):
    """Durable per-attempt usage proof written before settlement.

    Settlement is reconciled from this table after a crash.  (reservation_id,
    attempt_no) is unique, so a retried write cannot duplicate the proof or the
    deduction.
    """
    __tablename__ = 'usage_evidence'
    __table_args__ = (UniqueConstraint('reservation_id', 'attempt_no', name='uq_usage_attempt'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey('quota_reservations.id'), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('batches.id'), index=True)
    attempt_no: Mapped[int] = mapped_column(default=1)
    original_ms: Mapped[float] = mapped_column(Numeric(16, 3), default=0)
    candidate_ms: Mapped[float] = mapped_column(Numeric(16, 3), default=0)
    total_ms: Mapped[float] = mapped_column(Numeric(16, 3), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
