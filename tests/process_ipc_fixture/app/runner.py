import subprocess
import sys


def run_worker():
    return subprocess.run([sys.executable, "-m", "app.worker", "--once"], check=True)
