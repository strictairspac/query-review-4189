"""Independent worker process.

Concurrency model: one process, a small thread pool.  A batch must take its
group's upper-bound quota hold before it may start; when the group is short on
balance the batch simply stays queued and other groups keep running.  Actual
database time is written as durable usage evidence and then settled against the
hold, so an interrupted settlement can be reconciled without double deduction.
"""
import json
import os
import socket
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select, text

from .config.database import Session
from .config.settings import (QUOTA_LEASE_SECONDS, WORKER_CONCURRENCY, ARTIFACTS_DIR,
                               QUOTA_WAIT_RETRY_SECONDS)
from .models.batch import Batch
from .models.sample import Sample
from .models.evidence import Evidence
from .services.batch import queue
from .services.query_engine import compare, UsageMeter
from .services import quota

heartbeat = Path(ARTIFACTS_DIR) / 'worker.heartbeat'
OWNER = f'{socket.gethostname()}:{os.getpid()}'
_executor = ThreadPoolExecutor(max_workers=max(1, WORKER_CONCURRENCY))
_inflight = set()
_inflight_lock = threading.Lock()
_waiting = {}          # batch_id -> monotonic time when it may be retried
_waiting_lock = threading.Lock()


def _try_claim(db, batch_id):
    """Take the execution lease.  Succeeds for an unclaimed batch or one whose
    previous holder disappeared and whose lease has expired."""
    result = db.execute(
        text('UPDATE batches SET lease_owner=:o, lease_until=now() + make_interval(secs=>:s), status=:r '
             'WHERE id=:id AND (lease_owner IS NULL OR lease_until IS NULL OR lease_until < now())'),
        {'o': OWNER, 's': QUOTA_LEASE_SECONDS, 'r': 'running', 'id': batch_id},
    )
    db.commit()
    return result.rowcount == 1


def _release_lease(db, batch_id, status=None):
    if status is None:
        db.execute(text('UPDATE batches SET lease_owner=NULL, lease_until=NULL WHERE id=:id'),
                   {'id': batch_id})
    else:
        db.execute(text('UPDATE batches SET lease_owner=NULL, lease_until=NULL, status=:s WHERE id=:id'),
                   {'s': status, 'id': batch_id})
    db.commit()


def _split_meter(meter):
    original = sum(s['ms'] for s in meter.statements if s['label'].startswith('original'))
    candidate = sum(s['ms'] for s in meter.statements if s['label'].startswith('candidate'))
    return original, candidate


def run_one(batch_id):
    """Execute one batch end to end.  Returns 'ran' | 'waiting' | 'skipped'."""
    with Session() as db:
        batch = db.get(Batch, batch_id)
        if not batch:
            return 'skipped'
        if db.scalar(select(Evidence).where(Evidence.batch_id == batch_id)):
            return 'skipped'
        if not _try_claim(db, batch_id):
            return 'skipped'
        db.refresh(batch)
        # Legacy rows created before quota tracking have no group; adopt them
        # into the default group so no execution escapes the ledger.
        if batch.group_id is None:
            batch.group_id = quota.default_group(db).id
            db.commit()
        # Reserve before starting: the hold is the group's own upper bound.
        reservation = quota.try_reserve(db, batch_id, batch.group_id)
        if reservation is None:
            # Balance is short.  Put the batch back in the queue for this
            # group only; other groups continue untouched.
            _release_lease(db, batch_id, 'queued')
            with _waiting_lock:
                _waiting[batch_id] = time.monotonic() + QUOTA_WAIT_RETRY_SECONDS
            return 'waiting'
        sample = db.get(Sample, batch.sample_id)

    meter = UsageMeter()
    original_ms = candidate_ms = 0.0
    try:
        result = compare(sample.original_sql, batch.candidate_sql, meter)
        status = 'passed' if result['equivalent'] else 'different'
        original_ms, candidate_ms = _split_meter(meter)
    except Exception as exc:
        original_ms, candidate_ms = _split_meter(meter)
        result = {'error': str(exc)[:500], 'db_ms': round(meter.total_ms, 3)}
        status = 'failed'

    with _waiting_lock:
        _waiting.pop(batch_id, None)
    with Session() as db:
        reservation = quota.reservation_for_batch(db, batch_id)
        if reservation is not None:
            # Persist the proof first.  Settlement only ever reads stored
            # proofs, so a crash in between is recoverable and idempotent.
            try:
                proof = quota.record_usage(db, reservation, original_ms, candidate_ms)
                quota.settle_reservation(db, reservation.id, actual_ms=proof.total_ms)
            except Exception:
                # A broken settlement must not block the result itself.  The
                # proof is already durable (or will be retried); recovery
                # reconciles from it without double charging.
                db.rollback()
                traceback.print_exc()
        batch = db.get(Batch, batch_id)
        if not db.scalar(select(Evidence).where(Evidence.batch_id == batch_id)):
            db.add(Evidence(batch_id=batch_id, result=result))
        batch.status = status
        _release_lease(db, batch_id, status)
        db.commit()
        try:
            Path(ARTIFACTS_DIR, f'review-{batch_id}.json').write_text(
                json.dumps(result, ensure_ascii=False, indent=2))
        except OSError:
            traceback.print_exc()
    return 'ran'


def recover():
    """Settle or retain holds left behind by an interrupted worker.

    Called at startup and periodically.  Only rows whose batch lease has
    expired are touched, so a live thread is never interfered with.
    """
    recovered = []
    with Session() as db:
        stale = list(db.scalars(select(Batch.id).where(
            Batch.status.in_(('queued', 'running')),
            (Batch.lease_until.is_(None)) | (Batch.lease_until < text('now()')))))
        # A finished batch whose settlement failed also needs reconciliation:
        # its evidence and status are committed, but the hold is still open.
        finished = list(db.scalars(select(Batch.id).where(
            Batch.status.in_(('passed', 'different', 'failed')),
            Batch.id.in_(select(quota.QuotaReservation.batch_id).where(
                quota.QuotaReservation.state.in_(('reserved', 'unverified')))))))
        for batch_id in stale + finished:
            reservation = quota.reservation_for_batch(db, batch_id)
            if not reservation or reservation.state == 'settled':
                continue
            proof = quota.latest_usage(db, reservation.id)
            if proof:
                quota.settle_reservation(db, reservation.id, actual_ms=proof.total_ms)
                recovered.append(batch_id)
            elif batch_id in stale:
                # No usage proof and no live worker: keep the hold for manual
                # verification.  Terminal batches with no proof keep it too.
                quota.mark_unverified(db, reservation.id)
    return recovered


def _submit(batch_id):
    with _inflight_lock:
        if batch_id in _inflight:
            return
        _inflight.add(batch_id)

    def task():
        try:
            run_one(batch_id)
        except Exception:
            traceback.print_exc()
            try:
                with Session() as db:
                    _release_lease(db, batch_id, 'queued')
            except Exception:
                traceback.print_exc()
        finally:
            with _inflight_lock:
                _inflight.discard(batch_id)

    _executor.submit(task)


def _drain_wakeups(limit=64):
    ids = set()
    for _ in range(limit):
        item = queue.lpop('query-review:jobs')
        if not item:
            break
        try:
            ids.add(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _ready_batches():
    with Session() as db:
        return set(db.scalars(select(Batch.id).where(Batch.status.in_(('queued', 'running')))))


def _waiting_ready():
    now = time.monotonic()
    with _waiting_lock:
        return {bid for bid, ready_at in _waiting.items() if ready_at <= now}


def tick():
    with _inflight_lock:
        capacity = max(0, WORKER_CONCURRENCY - len(_inflight))
    if capacity <= 0:
        return
    candidates = _drain_wakeups() | _ready_batches()
    waiting_ready = _waiting_ready()
    # Retry quota-blocked batches only after their backoff; everyone else goes
    # first so a starved group cannot hold a worker slot hostage.
    ordered = sorted(candidates - set(_waiting)) + sorted(waiting_ready)
    for batch_id in ordered:
        with _inflight_lock:
            if batch_id in _inflight:
                continue
            if len(_inflight) >= WORKER_CONCURRENCY:
                break
        _submit(batch_id)


def main():
    try:
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        recover()
    except Exception:
        traceback.print_exc()
    last_recover = time.time()
    while True:
        try:
            heartbeat.write_text(str(time.time()))
            tick()
            if time.time() - last_recover > QUOTA_LEASE_SECONDS / 2:
                recover()
                last_recover = time.time()
            time.sleep(0.5)
        except Exception:
            traceback.print_exc()
            time.sleep(2)


if __name__ == '__main__':
    main()
