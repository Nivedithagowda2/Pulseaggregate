"""Per-tenant concurrency limit (bonus B-3): one tenant cannot hog the API/database."""
import threading


class TenantLimiter:
    def __init__(self, max_concurrent: int):
        self.max_concurrent = max_concurrent
        self._lock = threading.Lock()
        self._slots = {}

    def _sem(self, tenant_id: int) -> threading.BoundedSemaphore:
        with self._lock:
            sem = self._slots.get(tenant_id)
            if sem is None:
                sem = self._slots[tenant_id] = threading.BoundedSemaphore(self.max_concurrent)
            return sem

    def try_acquire(self, tenant_id: int) -> bool:
        return self._sem(tenant_id).acquire(blocking=False)

    def release(self, tenant_id: int) -> None:
        self._sem(tenant_id).release()
