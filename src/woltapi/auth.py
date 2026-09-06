"""Consumer refresh-token authentication, separate from Converse widget auth."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from threading import Lock
from types import MappingProxyType
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlencode

from .credentials import SessionCredentials
from .errors import (
    HTTPStatusError,
    RequestFailedError,
    RequestTimeoutError,
    ResponseDecodeError,
    ResponseShapeError,
)
from .services import ServiceHost
from .transport import (
    DEFAULT_TIMEOUT_SECONDS,
    _close_response,
    _NoRedirect,
    _Opener,
    _validate_timeout,
)


class RefreshTokenCredentials(SessionCredentials):
    """Obtain and cache bearer tokens for the three ordering hosts.

    Construction makes no requests. Refresh happens on first use and shortly
    before expiry, or explicitly through refresh(). No request is retried.
    Additional per-host headers are preserved except for Authorization.

    on_refresh receives the latest refresh token after every successful exchange,
    before an ordering request can proceed. It should persist the token securely
    and must not call back into refresh() or headers_for(). Callback exceptions
    propagate; the new tokens remain in memory and persistence is attempted
    again before the next refresh or ordering request.
    """

    __slots__ = (
        "_access_token",
        "_lock",
        "_on_refresh",
        "_opener",
        "_pending_persistence",
        "_refresh_at",
        "_refresh_token",
        "_timeout",
    )

    def __init__(
        self,
        refresh_token: str,
        *,
        on_refresh: Callable[[str], None] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        restaurant_headers: Mapping[str, str] | None = None,
        consumer_headers: Mapping[str, str] | None = None,
        payment_headers: Mapping[str, str] | None = None,
        _opener: _Opener | None = None,
    ) -> None:
        if not _is_token(refresh_token):
            raise ValueError(
                "refresh_token must be a nonempty ASCII token without whitespace."
            )
        if on_refresh is not None and not callable(on_refresh):
            raise TypeError("on_refresh must be callable when provided.")
        super().__init__(
            restaurant_headers=restaurant_headers,
            consumer_headers=consumer_headers,
            payment_headers=payment_headers,
        )
        self._refresh_token = refresh_token
        self._access_token: str | None = None
        self._refresh_at = 0.0
        self._lock = Lock()
        self._timeout = _validate_timeout(timeout)
        self._opener = _opener or urlrequest.build_opener(_NoRedirect())
        self._on_refresh = on_refresh
        self._pending_persistence = False

    @property
    def refresh_token(self) -> str:
        """Latest refresh token, including any server-side rotation. Do not log it."""

        return self._refresh_token

    def headers_for(self, service: ServiceHost) -> Mapping[str, str]:
        headers = super().headers_for(service)
        with self._lock:
            self._persist_refresh_token()
            if self._access_token is None or time.monotonic() >= self._refresh_at:
                self._refresh()
            return MappingProxyType(
                {
                    **{
                        k: v for k, v in headers.items() if k.lower() != "authorization"
                    },
                    "Authorization": f"Bearer {self._access_token}",
                }
            )

    def refresh(self) -> None:
        """Exchange the current refresh token now, without retrying failures."""

        with self._lock:
            self._persist_refresh_token()
            self._refresh()

    def _refresh(self) -> None:
        started = time.monotonic()
        request = urlrequest.Request(
            "https://authentication.wolt.com/v1/wauth2/access_token",
            data=urlencode(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                }
            ).encode("ascii"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "Origin": "https://wolt.com",
            },
            method="POST",
        )
        try:
            response = self._opener.open(request, timeout=self._timeout)
            try:
                status = response.getcode()
                body = response.read()
            finally:
                _close_response(response)
        except urlerror.HTTPError as error:
            _close_response(error)
            raise HTTPStatusError("authentication", error.code) from None
        except TimeoutError:
            raise RequestTimeoutError("authentication") from None
        except (urlerror.URLError, OSError):
            raise RequestFailedError("authentication") from None
        if isinstance(status, bool) or not isinstance(status, int):
            raise ResponseShapeError("authentication")
        if not 200 <= status < 300:
            raise HTTPStatusError("authentication", status)
        if not isinstance(body, bytes) or not body:
            raise ResponseShapeError("authentication")
        try:
            result = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ResponseDecodeError("authentication") from None
        if not isinstance(result, dict):
            raise ResponseShapeError("authentication")
        access_token = result.get("access_token")
        refresh_token = result.get("refresh_token")
        expires_in = result.get("expires_in")
        token_type = result.get("token_type")
        if (
            not _is_token(access_token)
            or not _is_token(refresh_token)
            or not isinstance(token_type, str)
            or token_type.lower() != "bearer"
            or isinstance(expires_in, bool)
            or not isinstance(expires_in, (int, float))
        ):
            raise ResponseShapeError("authentication")
        try:
            lifetime = float(expires_in)
        except OverflowError:
            raise ResponseShapeError("authentication") from None
        if not math.isfinite(lifetime) or lifetime <= 0:
            raise ResponseShapeError("authentication")
        self._access_token = access_token
        self._refresh_token = refresh_token
        # Start at request time so network latency cannot extend token validity.
        self._refresh_at = started + lifetime - min(30.0, lifetime / 2)
        self._pending_persistence = self._on_refresh is not None
        self._persist_refresh_token()

    def _persist_refresh_token(self) -> None:
        if self._pending_persistence and self._on_refresh is not None:
            self._on_refresh(self._refresh_token)
            self._pending_persistence = False

    def __repr__(self) -> str:
        return "RefreshTokenCredentials()"


def _is_token(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.isascii()
        and all(32 < ord(char) < 127 for char in value)
    )
