"""Typed exceptions for Hunar API failures.

Every one of these is built from the status code and Hunar's parsed error body
and nothing else. The API key is never passed into this module, so it cannot
reach a message, a repr, or a traceback frame, and these are safe to log whole.

Hunar's error body is consistently:

    {"success": false, "message": "...", "details": [...]}

with `details` carrying per-field errors on a 422.
"""

from typing import Any


class HunarError(Exception):
    """Base for every Hunar API failure. Callers can catch just this."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details: list[dict[str, Any]] = details or []

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"[{self.status_code}] {self.message}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.__str__()!r})"


class HunarAuthError(HunarError):
    """401. The key is wrong, revoked, or expired."""


class HunarQuotaError(HunarError):
    """402. Subscription expired or calling minutes exhausted.

    Called out separately because on a trial key this is the failure that
    actually happens, and it needs a different message to the user than "the
    integration is broken".
    """


class HunarNotFoundError(HunarError):
    """404. Unknown agent id, call id, or path."""


class HunarBadRequestError(HunarError):
    """400. Telephony or business rejection, e.g. a from_phone_number that does
    not belong to the organisation."""


class HunarValidationError(HunarError):
    """422. The request shape or field values were rejected."""

    @property
    def field_errors(self) -> dict[str, str]:
        """Hunar's `details` flattened to {field_name: error_msg}, so a caller can
        put each message next to the field the recruiter actually filled in."""
        errors: dict[str, str] = {}
        for detail in self.details:
            field = detail.get("field_name")
            if isinstance(field, str):
                errors[field] = str(detail.get("error_msg", ""))
        return errors


class HunarServerError(HunarError):
    """5xx, after the retries are exhausted."""


class HunarTimeoutError(HunarError):
    """Connection failure or timeout, after the retries are exhausted. No
    status_code, because no response was ever received."""
