import subprocess
import sys


def test_worker_runs():
    subprocess.run([sys.executable, "-m", "app.worker"], check=True)
