"""Doubles for code that goes through `app/services/api/with_retry.py::with_retry`."""

from __future__ import annotations


class RetryableError(Exception):
    """A provider error `with_retry` retries: an HTTP 503."""

    status_code = 503
