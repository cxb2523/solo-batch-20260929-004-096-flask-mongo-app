"""Thread-safe per-DAO call counters."""

import threading
from collections import defaultdict


class Metrics:
    """Counts every DAO method invocation (success or failure)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._calls = defaultdict(int)
        self._failures = defaultdict(int)

    def record(self, name, success=True):
        with self._lock:
            self._calls[name] += 1
            if not success:
                self._failures[name] += 1

    def snapshot(self):
        with self._lock:
            calls = dict(self._calls)
            failures = dict(self._failures)
        return {
            "calls": calls,
            "failures": failures,
            "total_calls": sum(calls.values()),
            "total_failures": sum(failures.values()),
        }

    def reset(self):
        with self._lock:
            self._calls.clear()
            self._failures.clear()
