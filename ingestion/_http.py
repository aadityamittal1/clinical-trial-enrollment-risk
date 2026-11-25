"""Shared HTTP helper: GET with exponential backoff on 429 / 5xx / transport errors."""

import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.wait import wait_base

DEFAULT_WAIT = wait_exponential(multiplier=1, min=1, max=60)


class RetryableStatus(httpx.HTTPStatusError):
    """429 or 5xx response."""


MAX_RETRY_AFTER_S = 120.0


def _retry_after(state: RetryCallState) -> float:
    """Seconds from a numeric ``Retry-After`` header on a 429/503 response, else 0."""
    outcome = state.outcome
    exc = outcome.exception() if outcome is not None else None
    if isinstance(exc, RetryableStatus):
        value = exc.response.headers.get("Retry-After", "")
        try:
            return min(max(float(value), 0.0), MAX_RETRY_AFTER_S)
        except ValueError:
            return 0.0
    return 0.0


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, RetryableStatus | httpx.TransportError)


def get_with_retry(
    client: httpx.Client,
    url: str,
    params: Mapping[str, Any],
    *,
    max_attempts: int = 6,
    wait: wait_base = DEFAULT_WAIT,
    ok_statuses: tuple[int, ...] = (),
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """GET ``url``; retry 429/5xx/timeouts; raise ``httpx.HTTPStatusError`` otherwise.

    Statuses in ``ok_statuses`` (e.g. 404) are returned instead of raised.
    """

    def attempt() -> httpx.Response:
        resp = client.get(url, params=params)
        if resp.status_code in ok_statuses:
            return resp
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RetryableStatus(
                f"{resp.status_code} from {url}", request=resp.request, response=resp
            )
        resp.raise_for_status()
        return resp

    retrying = Retrying(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(max_attempts),
        wait=lambda state: max(float(wait(state)), _retry_after(state)),
        sleep=sleep,
        reraise=True,
    )
    return retrying(attempt)
