"""Credential handling with explicit ordering-host scopes."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .services import ServiceHost


class SessionCredentials:
    """Caller-provided request headers, scoped to one ordering host each.

    No header name or value is inferred from the captured browser traffic. A
    caller must provide credentials obtained through its own approved session
    integration, and remains responsible for their validity and lifecycle.
    """

    __slots__ = ("_headers",)

    def __init__(
        self,
        *,
        restaurant_headers: Mapping[str, str] | None = None,
        consumer_headers: Mapping[str, str] | None = None,
        payment_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._headers = MappingProxyType(
            {
                ServiceHost.RESTAURANT: _freeze_headers(restaurant_headers),
                ServiceHost.CONSUMER: _freeze_headers(consumer_headers),
                ServiceHost.PAYMENT: _freeze_headers(payment_headers),
            }
        )

    def headers_for(self, service: ServiceHost) -> Mapping[str, str]:
        """Return the immutable headers configured for one known service."""

        return self._headers[service]

    def __repr__(self) -> str:
        configured = ", ".join(
            service.value for service, headers in self._headers.items() if headers
        )
        return f"SessionCredentials(configured_services=({configured}))"


def _freeze_headers(headers: Mapping[str, str] | None) -> Mapping[str, str]:
    if headers is None:
        return MappingProxyType({})
    if not isinstance(headers, Mapping):
        raise TypeError("Session credential headers must be a mapping.")

    frozen: dict[str, str] = {}
    for name, value in headers.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or "\r" in name
            or "\n" in name
            or not isinstance(value, str)
            or "\r" in value
            or "\n" in value
        ):
            raise ValueError("Session credential headers must contain valid strings.")
        frozen[name] = value
    return MappingProxyType(frozen)
