import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["TZ"] = "UTC"  # timestamps in generated sample outputs do not depend on the machine's zone
time.tzset()
