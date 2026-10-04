import os

METRICS_PORT = 8125
JOBS_PORT = int(os.environ.get("JOBS_PORT", "7000"))
