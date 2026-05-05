# In-process TTL cache using cachetools.
import threading
from cachetools import TTLCache

# ── Query cache ──────────────────────────────────────────────────────────────
# Stores serialised query results for 60 seconds.
# maxsize=500: at most 500 unique filter+page combinations are held in memory.
# If the cache fills, the least-recently-used entry is evicted automatically.
_query_cache: TTLCache = TTLCache(maxsize=500, ttl=60)

# ── User cache ───────────────────────────────────────────────────────────────
# Stores user row dicts for 5 minutes.
# User data (role, is_active) changes rarely — 5 min staleness is acceptable.
_user_cache: TTLCache = TTLCache(maxsize=200, ttl=300)

# ── Lock ─────────────────────────────────────────────────────────────────────
# cachetools is NOT thread-safe by default.  FastAPI's asyncio event loop runs
# all coroutines on a single OS thread, but the lock costs almost nothing and
# protects against any edge case where threads touch the cache.
_lock = threading.Lock()


# ─── Query cache helpers ──────────────────────────────────────────────────────

def get_query_cache(key: str):
    """Return cached query result or None on miss."""
    with _lock:
        return _query_cache.get(key)


def set_query_cache(key: str, value: dict) -> None:
    """Store a query result in the cache."""
    with _lock:
        _query_cache[key] = value


def invalidate_query_cache() -> None:
    """
    Clear all cached query results.
    Called after any write (create / delete / CSV upload) so stale
    results are not served to the next reader.
    """
    with _lock:
        _query_cache.clear()


# ─── User cache helpers ────────────────────────────────────────────────────────

def get_user_cache(user_id: str):
    """Return a cached user dict or None on miss."""
    with _lock:
        return _user_cache.get(user_id)


def set_user_cache(user_id: str, value: dict) -> None:
    """Store a user dict in the cache keyed by user_id."""
    with _lock:
        _user_cache[user_id] = value


def invalidate_user_cache(user_id: str) -> None:
    """Remove a single user from the cache (e.g. after role change)."""
    with _lock:
        _user_cache.pop(user_id, None)
