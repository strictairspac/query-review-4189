import os

DATABASE_URL = os.environ['DATABASE_URL']
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
JWT_SECRET = os.environ['JWT_SECRET']

# Quota policy -------------------------------------------------------------
# A batch reserves this upper bound before it starts.  One comparison runs
# EXPLAIN + SELECT on both sides; every statement is capped at 2500ms, so the
# worst case is 4 * 2500ms.  The extra 2000ms covers connection overhead.
QUOTA_RESERVE_MS = int(os.getenv('QUOTA_RESERVE_MS', '12000'))
# Balance granted to a group the first time it asks for a given day.
QUOTA_DEFAULT_GRANT_MS = int(os.getenv('QUOTA_DEFAULT_GRANT_MS', '600000'))
# Upper bound for a single manual top-up.
QUOTA_MAX_TOPUP_MS = int(os.getenv('QUOTA_MAX_TOPUP_MS', '600000'))
# How long a worker owns an in-flight batch before another worker may recover it.
QUOTA_LEASE_SECONDS = int(os.getenv('QUOTA_LEASE_SECONDS', '60'))
# Settlement group used for legacy rows and users without an assignment.
DEFAULT_GROUP_NAME = os.getenv('DEFAULT_GROUP_NAME', '澄港结算一组')
# How long a batch that is short on balance waits before retrying.
QUOTA_WAIT_RETRY_SECONDS = float(os.getenv('QUOTA_WAIT_RETRY_SECONDS', '2'))
# Number of batches one worker process may execute concurrently.
WORKER_CONCURRENCY = int(os.getenv('WORKER_CONCURRENCY', '4'))

# Directory for heartbeat and per-batch JSON evidence copies.
ARTIFACTS_DIR = os.getenv('ARTIFACTS_DIR', '/artifacts')
