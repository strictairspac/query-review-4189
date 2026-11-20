"""Idempotent schema bootstrap.

The project has no migration runner; schema v1 relied on ``create_all`` alone.
Schema v2 adds the settlement-group quota tables plus ``group_id`` columns on
existing tables, so we apply add-column statements defensively for deployments
that already hold v1 data.
"""
from sqlalchemy import text
from .database import Base, engine
from .. import models  # noqa: F401  (register every model on Base.metadata)


def init_schema():
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE users ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES groups(id)'))
        conn.execute(text('ALTER TABLE batches ADD COLUMN IF NOT EXISTS group_id INTEGER REFERENCES groups(id)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_batches_group_id ON batches(group_id)'))
        conn.execute(text('ALTER TABLE batches ADD COLUMN IF NOT EXISTS lease_owner VARCHAR(80)'))
        conn.execute(text('ALTER TABLE batches ADD COLUMN IF NOT EXISTS lease_until TIMESTAMP'))
