"""Make every Supabase call in the ETL survive a transient gateway failure.

Supabase's PostgREST edge occasionally answers a perfectly valid request with a 504
"Gateway Timeout" that is gone a second later. postgrest-py turns that into an exception
and the ETL had no retry anywhere, so a blip killed the whole cron run: 2 of the 60 runs
to 2026-09-14 died this way (12 Sep 11:40 UTC and 13 Sep 20:21 UTC), both inside
`intl.topup_live` before the run ever reached the alert step. A verdict transition landing
in that window would have been missed outright, because `compute.main()` never got far
enough to look for one.

WHY A CHOKE POINT, NOT 24 EDITS. The ETL has 24 `.execute()` call sites spread over eight
modules. Retrying at each one means the next blip finds whichever one was forgotten, and
the reviewer cannot tell from any single call site whether the policy is applied. Every one
of those calls ends up in one of postgrest's four sync request builders, and `load.client()`
is the only place the ETL constructs a client — so the policy is installed there, once, and
holds for code not yet written.

WHY RETRYING WRITES IS SAFE HERE. Every write in this ETL is an idempotent upsert keyed by
a primary key (`trade_date`, `trade_date,series`, `as_time,seq`), so replaying one that may
already have landed rewrites the same row with the same values. Do not relax that: if an
append-only or counter-style write is ever added, it must not go through this path.

Only *transient* failures are retried. A 4xx is a bug in the query and must fail loudly and
immediately, the way it does today.
"""

from __future__ import annotations

import functools
import random
import time

import httpx
from postgrest._sync import request_builder as _request_builder
from postgrest.exceptions import APIError
from pydantic import ValidationError

# 5xx and gateway codes are the edge failing, not the query. 408/429 are timing, not logic.
# 52x are Cloudflare-fronted variants of the same thing.
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})

_ATTEMPTS = 4          # 3 retries; worst case adds ~5 s to a job with a 15-minute budget
_BASE_DELAY = 0.75     # seconds, doubled each attempt, with jitter

_INSTALLED_FLAG = "_etl_retry_installed"


def _status_of(exc: Exception) -> int | None:
    """Best-effort HTTP status behind a postgrest/httpx exception."""
    if isinstance(exc, APIError):
        code = getattr(exc, "code", None)
        try:
            return int(code)
        except (TypeError, ValueError):
            return None
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None


def _is_transient(exc: Exception) -> bool:
    """True when retrying the identical request could plausibly succeed."""
    # A connection reset or read timeout never reached a query planner.
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    # A gateway that returns `{"message": "Gateway Timeout"}` does not match PostgREST's
    # error schema, so postgrest-py fails while parsing the error itself and raises
    # pydantic's ValidationError instead of APIError. That is how the 12 Sep run died, and
    # matching only APIError would miss it. This is reachable only from postgrest's error
    # path, so the response was already a failure by the time we see it.
    if isinstance(exc, ValidationError):
        return True
    status = _status_of(exc)
    return status in _RETRY_STATUS if status is not None else False


def _with_retries(execute):
    @functools.wraps(execute)
    def wrapper(self, *args, **kwargs):
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                return execute(self, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — re-raised below unless transient
                if attempt == _ATTEMPTS or not _is_transient(exc):
                    raise
                delay = _BASE_DELAY * (2 ** (attempt - 1)) * (1 + random.random() * 0.25)
                # Printed, not swallowed: a run that quietly needed three attempts every
                # time is telling you something about the database that a green tick hides.
                print(
                    f"supabase: {type(exc).__name__} on attempt {attempt}/{_ATTEMPTS} "
                    f"({_status_of(exc) or 'no status'}) — retrying in {delay:.1f}s"
                )
                time.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    return wrapper


def install() -> None:
    """Wrap postgrest's sync request builders with the retry policy. Idempotent."""
    for name in (
        "SyncQueryRequestBuilder",
        "SyncSingleRequestBuilder",
        "SyncMaybeSingleRequestBuilder",
        "SyncExplainRequestBuilder",
    ):
        cls = getattr(_request_builder, name, None)
        if cls is None or "execute" not in cls.__dict__:
            continue  # postgrest moved it; the ETL still works, just without retries
        if getattr(cls.execute, _INSTALLED_FLAG, False):
            continue
        wrapped = _with_retries(cls.execute)
        setattr(wrapped, _INSTALLED_FLAG, True)
        cls.execute = wrapped
