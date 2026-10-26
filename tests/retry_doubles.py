"""Doubles for code that goes through `app/loop/retry.py::with_retry`."""

from __future__ import annotations


class RetryableError(Exception):
    """A provider error `with_retry` retries: an HTTP 503."""

    status_code = 503
