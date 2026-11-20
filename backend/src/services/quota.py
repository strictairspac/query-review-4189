"""Per-group daily database-time allowance.

Ledger invariant, always true for every (group, day) row::

    balance_ms = granted_ms - used_ms - reserved_ms

* ``granted_ms`` is replenished by manual top-ups (and the first-touch grant).
* ``reserved_ms`` is an upper-bound hold taken by a batch before it starts.
* ``used_ms`` is the actual time both sides spent inside the database, posted
  when the batch settles.

Settlement always posts to the day the hold was taken, even when the batch
finishes after midnight, and releases whatever the hold over-covered.  The
ledger is reconciled from durable :class:`UsageEvidence` rows, so a settlement
retry can never deduct twice.
"""
from datetime import date, datetime
from sqlalchemy import select, text, func
from sqlalchemy.exc import IntegrityError
from ..config.settings import (QUOTA_RESERVE_MS, QUOTA_DEFAULT_GRANT_MS, QUOTA_MAX_TOPUP_MS,
                               DEFAULT_GROUP_NAME)
from ..models.quota import GroupQuota, QuotaReservation, UsageEvidence, SettlementGroup


def current_day(db):
    return db.scalar(text('SELECT CURRENT_DATE'))


def _as_day(value):
    """Accept a date, a datetime or an ISO string from the API layer."""
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


def ensure_group(db, name):
    group = db.scalar(select(SettlementGroup).where(SettlementGroup.name == name))
    if group:
        return group
    group = SettlementGroup(name=name)
    db.add(group)
    db.commit()
    return group


def default_group(db):
    return ensure_group(db, DEFAULT_GROUP_NAME)


def ensure_quota(db, group_id, day=None, grant_ms=None):
    """Create the (group, day) ledger row on first touch.  Never resets usage."""
    day = _as_day(day) or current_day(db)
    grant = QUOTA_DEFAULT_GRANT_MS if grant_ms is None else grant_ms
    db.execute(
        text('INSERT INTO group_quotas (group_id, day, granted_ms, used_ms, reserved_ms) '
             'VALUES (:g, :d, :grant, 0, 0) ON CONFLICT (group_id, day) DO NOTHING'),
        {'g': group_id, 'd': day, 'grant': grant},
    )
    return db.scalar(select(GroupQuota).where(GroupQuota.group_id == group_id, GroupQuota.day == day))


def quota_view(db, group_id, day=None):
    day = _as_day(day) or current_day(db)
    ensure_quota(db, group_id, day)
    db.commit()
    row = db.scalar(select(GroupQuota).where(GroupQuota.group_id == group_id, GroupQuota.day == day))
    held = db.scalar(select(func.count()).select_from(QuotaReservation).where(
        QuotaReservation.group_id == group_id, QuotaReservation.day == day,
        QuotaReservation.state.in_(('reserved', 'unverified')))) or 0
    return {'day': str(row.day), 'granted_ms': row.granted_ms, 'used_ms': row.used_ms,
            'reserved_ms': row.reserved_ms, 'balance_ms': row.balance_ms, 'open_holds': held}


def top_up(db, group_id, amount_ms, day=None):
    """Replenish the day's allowance.  Amount is additive and never negative."""
    if not isinstance(amount_ms, int) or isinstance(amount_ms, bool):
        raise ValueError('补额必须是整数毫秒')
    if amount_ms <= 0:
        raise ValueError('补额必须大于 0')
    if amount_ms > QUOTA_MAX_TOPUP_MS:
        raise ValueError(f'单次补额不能超过 {QUOTA_MAX_TOPUP_MS} 毫秒')
    if not db.get(SettlementGroup, group_id):
        raise ValueError('结算组不存在')
    day = _as_day(day) or current_day(db)
    ensure_quota(db, group_id, day)
    # Lock the ledger row: concurrent top-ups are serialised instead of lost.
    db.execute(text('SELECT id FROM group_quotas WHERE group_id=:g AND day=:d FOR UPDATE'),
               {'g': group_id, 'd': day})
    db.execute(text('UPDATE group_quotas SET granted_ms=granted_ms+:a, updated_at=now() '
                    'WHERE group_id=:g AND day=:d'), {'a': amount_ms, 'g': group_id, 'd': day})
    db.commit()
    return quota_view(db, group_id, day)


def reservation_for_batch(db, batch_id):
    return db.scalar(select(QuotaReservation).where(QuotaReservation.batch_id == batch_id))


def try_reserve(db, batch_id, group_id, day=None, amount_ms=None):
    """Atomically take the upper-bound hold for a batch.

    Returns the reservation, or ``None`` when the group balance cannot cover
    the hold.  The guarded UPDATE is the contention point: two workers racing
    for the last of a group's balance can never both succeed.
    """
    existing = reservation_for_batch(db, batch_id)
    if existing:
        return existing
    day = _as_day(day) or current_day(db)
    amount = QUOTA_RESERVE_MS if amount_ms is None else amount_ms
    ensure_quota(db, group_id, day)
    result = db.execute(
        text('UPDATE group_quotas SET reserved_ms=reserved_ms+:a, updated_at=now() '
             'WHERE group_id=:g AND day=:d AND granted_ms-used_ms-reserved_ms>=:a'),
        {'a': amount, 'g': group_id, 'd': day},
    )
    if result.rowcount == 0:
        db.rollback()
        return None
    reservation = QuotaReservation(batch_id=batch_id, group_id=group_id, day=day,
                                   reserved_ms=amount, state='reserved')
    db.add(reservation)
    try:
        db.commit()
    except IntegrityError:
        # Another worker reserved this same batch first: keep exactly one hold.
        db.rollback()
        return reservation_for_batch(db, batch_id)
    db.refresh(reservation)
    return reservation


def record_usage(db, reservation, original_ms, candidate_ms, attempt_no=None):
    """Persist the durable usage proof before any deduction is attempted.

    One reservation has at most one proof: a retried execution of the same
    batch reuses the stored proof instead of creating a second chargeable
    record, so settlement can never deduct the same hold twice.
    """
    existing = latest_usage(db, reservation.id)
    if existing:
        return existing
    original_ms = max(0.0, round(float(original_ms), 3))
    candidate_ms = max(0.0, round(float(candidate_ms), 3))
    attempt_no = 1 if attempt_no is None else attempt_no
    db.execute(
        text('INSERT INTO usage_evidence '
             '(reservation_id, batch_id, attempt_no, original_ms, candidate_ms, total_ms) '
             'VALUES (:r, :b, :n, :o, :c, :t) ON CONFLICT (reservation_id, attempt_no) DO NOTHING'),
        {'r': reservation.id, 'b': reservation.batch_id, 'n': attempt_no,
         'o': original_ms, 'c': candidate_ms, 't': original_ms + candidate_ms},
    )
    db.execute(text('UPDATE quota_reservations SET attempts=GREATEST(attempts, :n) WHERE id=:r'),
               {'n': attempt_no, 'r': reservation.id})
    db.commit()
    return latest_usage(db, reservation.id)


def latest_usage(db, reservation_id):
    return db.scalar(select(UsageEvidence).where(UsageEvidence.reservation_id == reservation_id)
                     .order_by(UsageEvidence.attempt_no.desc()).limit(1))


def settle_reservation(db, reservation_id, actual_ms=None):
    """Post actual usage to the reservation's original day and release the rest.

    Idempotent: once the row is ``settled`` a retry is a no-op, so reconciliation
    after a crash cannot deduct the same batch twice.
    """
    reservation = db.get(QuotaReservation, reservation_id)
    if not reservation or reservation.state == 'settled':
        db.commit()
        return reservation
    # Lock order is always quota row -> reservation, matching try_reserve.
    ensure_quota(db, reservation.group_id, reservation.day)
    db.execute(text('SELECT id FROM group_quotas WHERE group_id=:g AND day=:d FOR UPDATE'),
               {'g': reservation.group_id, 'd': reservation.day})
    reservation = db.scalar(select(QuotaReservation)
                            .where(QuotaReservation.id == reservation_id).with_for_update())
    if reservation.state == 'settled':
        db.commit()
        return reservation
    if actual_ms is None:
        proof = latest_usage(db, reservation.id)
        if proof is None:
            # No usage proof: keep the hold rather than silently refunding it.
            reservation.state = 'unverified'
            db.commit()
            return reservation
        actual_ms = proof.total_ms
    actual = max(0.0, round(float(actual_ms), 3))
    db.execute(text('UPDATE group_quotas SET used_ms=used_ms+:a, '
                    'reserved_ms=GREATEST(0, reserved_ms-:r), updated_at=now() '
                    'WHERE group_id=:g AND day=:d'),
               {'a': actual, 'r': reservation.reserved_ms,
                'g': reservation.group_id, 'd': reservation.day})
    reservation.actual_ms = actual
    reservation.state = 'settled'
    reservation.settled_at = func.now()
    db.commit()
    return reservation


def mark_unverified(db, reservation_id):
    """Crash with no durable proof: retain the hold for later reconciliation."""
    db.execute(text("UPDATE quota_reservations SET state='unverified' "
                    "WHERE id=:r AND state='reserved'"), {'r': reservation_id})
    db.commit()


def reconcile(db, batch_id=None):
    """Recover after an interrupted worker.

    * proof exists -> settle exactly once from the stored proof;
    * no proof     -> keep the hold and mark it for verification.

    Returns the list of reconciled reservation ids.
    """
    query = select(QuotaReservation).where(QuotaReservation.state.in_(('reserved', 'unverified')))
    if batch_id is not None:
        query = query.where(QuotaReservation.batch_id == batch_id)
    touched = []
    for reservation in list(db.scalars(query)):
        proof = latest_usage(db, reservation.id)
        if proof:
            settle_reservation(db, reservation.id, actual_ms=proof.total_ms)
            touched.append(reservation.id)
    return touched


def quota_state(db, batch_id, batch_status=None):
    """Derived quota status shown on the batch page."""
    reservation = reservation_for_batch(db, batch_id)
    base = {'reserved_ms': 0, 'actual_ms': None, 'original_ms': None,
            'candidate_ms': None, 'day': None}
    if not reservation:
        # A terminal batch from before quota tracking (or one whose worker
        # never picked it up) has no ledger entry; say so instead of
        # pretending it is still waiting for balance.
        if batch_status in ('passed', 'different', 'failed'):
            return {**base, 'state': 'unrecorded', 'label': '未记录额度'}
        return {**base, 'state': 'waiting_quota', 'label': '等额度',
                'day': str(current_day(db))}
    proof = latest_usage(db, reservation.id)
    detail = {'reserved_ms': reservation.reserved_ms, 'day': str(reservation.day),
              'original_ms': proof.original_ms if proof else None,
              'candidate_ms': proof.candidate_ms if proof else None,
              'actual_ms': (proof.total_ms if proof else reservation.actual_ms)}
    if reservation.state == 'settled':
        return {**detail, 'state': 'settled', 'label': '已结清'}
    if reservation.state == 'unverified':
        return {**detail, 'state': 'unverified', 'label': '用量待核'}
    return {**detail, 'state': 'reserved', 'label': '已预留'}
