"""Privacy-safe exceptions raised by the Wolt client."""

from __future__ import annotations


class WoltApiError(Exception):
    """Base exception for this package."""


class SelectionError(WoltApiError):
    """Caller-provided selection data cannot be safely serialized."""


class UnsupportedSelectionError(SelectionError):
    """A catalog or selection feature is outside the supported checkout scope."""


class PurchasePreparationError(WoltApiError):
    """A selection, quote, consent state, or purchase input is unsafe to submit."""


class PurchaseAuthorizationError(WoltApiError):
    """Caller authorization is absent or bound to a different prepared order."""


class PurchaseAttemptStoreError(WoltApiError):
    """Durable local purchase-attempt state could not be safely updated."""


class DuplicatePurchaseAttempt(WoltApiError):
    """The same authorized attempt was already recorded locally."""

    def __init__(
        self, attempt_id: str, state: str, purchase_id: str | None = None
    ) -> None:
        self.attempt_id = attempt_id
        self.state = state
        self.purchase_id = purchase_id
        super().__init__(
            "This authorized purchase attempt was already recorded locally."
        )


class OrderOutcomeUnknown(WoltApiError):
    """A purchase may have reached the server but no usable success was established."""

    def __init__(self, attempt_id: str, purchase_id: str | None = None) -> None:
        self.attempt_id = attempt_id
        self.purchase_id = purchase_id
        super().__init__("Purchase outcome is unknown; no automatic retry was sent.")


class WoltTransportError(WoltApiError):
    """A request could not safely produce a usable response."""


class HTTPStatusError(WoltTransportError):
    """A service returned a non-success HTTP status."""

    def __init__(self, service: str, status_code: int) -> None:
        self.service = service
        self.status_code = status_code
        super().__init__(
            f"Wolt {service} request failed with HTTP status {status_code}."
        )


class RequestTimeoutError(WoltTransportError):
    """A request timed out before a usable response arrived."""

    def __init__(self, service: str) -> None:
        self.service = service
        super().__init__(f"Wolt {service} request timed out.")


class RequestFailedError(WoltTransportError):
    """A request failed before a usable response arrived."""

    def __init__(self, service: str) -> None:
        self.service = service
        super().__init__(f"Wolt {service} request failed before a response arrived.")


class ResponseError(WoltApiError):
    """A service response could not be safely interpreted."""


class ResponseDecodeError(ResponseError):
    """A response was not valid JSON."""

    def __init__(self, service: str) -> None:
        self.service = service
        super().__init__(f"Wolt {service} response was not valid JSON.")


class ResponseShapeError(ResponseError):
    """A response was missing the expected object or collection shape."""

    def __init__(self, service: str) -> None:
        self.service = service
        super().__init__(f"Wolt {service} response had an unexpected shape.")
