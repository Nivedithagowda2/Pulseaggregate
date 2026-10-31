"""Tiny in-process latency and counter collector used by the monitoring endpoints."""
import math
import threading
from collections import defaultdict, deque


def percentile(values, p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100.0 * len(ordered)))
    return ordered[rank - 1]


class Metrics:
    def __init__(self, window: int = 2000):
        self._lock = threading.Lock()
        self._latencies = defaultdict(lambda: deque(maxlen=window))
        self.counters = defaultdict(int)

    def observe(self, name: str, ms: float) -> None:
        with self._lock:
            self._latencies[name].append(ms)

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.counters[name] += n

    def snapshot(self) -> dict:
        with self._lock:
            latency = {
                name: {
                    "count": len(vals),
                    "p50_ms": round(percentile(vals, 50), 2),
                    "p95_ms": round(percentile(vals, 95), 2),
                    "p99_ms": round(percentile(vals, 99), 2),
                    "max_ms": round(max(vals), 2),
                }
                for name, vals in self._latencies.items()
                if vals
            }
            return {"latency": latency, "counters": dict(self.counters)}
