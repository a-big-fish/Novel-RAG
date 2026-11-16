import os

# Unit and integration tests drive the indexer directly; the in-process worker
# must not open database connections or claim real jobs during tests.
os.environ["INDEX_WORKER_ENABLED"] = "false"
