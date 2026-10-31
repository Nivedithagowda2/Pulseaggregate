"""
Tenant authorization.

The tenant id in the URL is NEVER trusted on its own. Each request must carry an
X-API-Key header; the key is hashed and mapped to the tenant that owns it. If that
tenant differs from the one in the URL the request is rejected with 403.
(Test identities = per-tenant API keys; no full login system is needed.)
"""
import hashlib
import time
from typing import Optional

from .db import connection

_TTL_SECONDS = 60
_cache: dict = {}


def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def tenant_for_key(api_key: str) -> Optional[int]:
    digest = hash_key(api_key)
    now = time.monotonic()
    hit = _cache.get(digest)
    if hit and hit[1] > now:
        return hit[0]
    with connection() as conn:
        row = conn.execute(
            "SELECT tenant_id FROM tenants WHERE api_key_hash = %s", (digest,)
        ).fetchone()
    tenant_id = row["tenant_id"] if row else None
    if len(_cache) > 10000:
        _cache.clear()
    _cache[digest] = (tenant_id, now + _TTL_SECONDS)
    return tenant_id


def clear_cache() -> None:
    _cache.clear()
