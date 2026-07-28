"""Logging filters for quieter, intentional client-error noise."""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, SuspiciousOperation


class SuppressClientErrorTracebackFilter(logging.Filter):
    """
    Keep the one-line django.request warning for expected denials,
    but drop the Python stack trace (PermissionDenied / 403 / 404).
    """

    _STATUS_CODES = {403, 404}

    def filter(self, record: logging.LogRecord) -> bool:
        status = getattr(record, "status_code", None)
        if status in self._STATUS_CODES:
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            return True

        if record.exc_info:
            exc = record.exc_info[1]
            if isinstance(exc, (PermissionDenied, SuspiciousOperation)):
                record.exc_info = None
                record.exc_text = None
                record.stack_info = None

        return True
