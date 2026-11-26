"""Run the frozen quality suite alongside hardware studies, with one measurement at a time.

For example, use queued_run.py run with the same arguments as study.py run.
The measured runner prints progress only after it flushes and fsyncs a completed
case. Yielding there keeps queue time outside every recorded trial. All model
calls, measurements, resume checks, and grading use the original runner.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tempfile
import time

import study


class DeviceQueue:
    def __init__(self):
        self.gate = None

    @contextmanager
    def lock(self):
        path = Path(tempfile.gettempdir()) / f"afmcli-moe-system-{os.getuid()}.lock"
        with path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self.gate = handle
            try:
                yield
            finally:
                self.gate = None

    def report_progress(self, *fields, **options):
        print(*fields, **options)
        if self.gate is None or len(fields) != 6 or fields[0] not in ("qa", "calc"):
            return
        fcntl.flock(self.gate, fcntl.LOCK_UN)
        try:
            time.sleep(0.1)
        finally:
            fcntl.flock(self.gate, fcntl.LOCK_EX)


if __name__ == "__main__":
    queue = DeviceQueue()
    # Only substitute lock ownership and the post-fsync progress notification.
    study.system_lock = queue.lock
    study.print = queue.report_progress
    study.main()
