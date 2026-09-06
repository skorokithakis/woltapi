"""Synchronous, non-retrying transport for fixed Wolt service hosts."""

from __future__ import annotations

import json
import math
import socket
from collections.abc import Mapping
from typing import Any, Protocol
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
from .services import BASE_URLS, ServiceHost

DEFAULT_TIMEOUT_SECONDS = 10.0


class _Opener(Protocol):
    def open(
        self, fullurl: Any, data: bytes | None = None, timeout: float = ...
    ) -> Any:
        """Open a request and return a response-like object."""


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    """Refuse redirects so credentials cannot be forwarded to a new URL."""

    def redirect_request(
        self,
        req: urlrequest.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class WoltTransport:
    """Transport that permits only the three explicitly configured hosts.

    The default opener has no cookie jar, no retry policy, and no redirect
    handler capable of forwarding caller-supplied credentials.
    """

    def __init__(
        self,
        credentials: SessionCredentials,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        _opener: _Opener | None = None,
    ) -> None:
        if not isinstance(credentials, SessionCredentials):
            raise TypeError("credentials must be a SessionCredentials instance.")
        self._credentials = credentials
        self._timeout = _validate_timeout(timeout)
        self._opener: _Opener = _opener or urlrequest.build_opener(_NoRedirect())

    def request(
        self,
        service: ServiceHost,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make one JSON request to a fixed host without retries or redirects."""

        if not isinstance(service, ServiceHost):
            raise TypeError("service must be a ServiceHost instance.")
        if method not in {"GET", "POST"}:
            raise ValueError("Only GET and POST requests are supported.")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("path must start with '/'.")
        if query is not None and not isinstance(query, Mapping):
            raise TypeError("query must be a mapping when provided.")
        if json_body is not None and not isinstance(json_body, Mapping):
            raise TypeError("json_body must be a mapping when provided.")

        data = _encode_json(json_body, service) if json_body is not None else None
        headers = _request_headers(
            self._credentials.headers_for(service), data is not None
        )
        request = urlrequest.Request(
            _build_url(service, path, query),
            data=data,
            headers=headers,
            method=method,
        )

        try:
            response = self._opener.open(request, timeout=self._timeout)
            try:
                status_code = _status_code(response, service)
                response_body = response.read()
            finally:
                _close_response(response)
        except urlerror.HTTPError as error:
            _close_response(error)
            raise HTTPStatusError(service.value, error.code) from None
        except (TimeoutError, socket.timeout):
            raise RequestTimeoutError(service.value) from None
        except (urlerror.URLError, OSError):
            raise RequestFailedError(service.value) from None

        if not 200 <= status_code < 300:
            raise HTTPStatusError(service.value, status_code)
        return _decode_json_object(response_body, service)


def _validate_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("timeout must be a positive finite number of seconds.")
    timeout_as_float = float(timeout)
    if not math.isfinite(timeout_as_float) or timeout_as_float <= 0:
        raise ValueError("timeout must be a positive finite number of seconds.")
    return timeout_as_float


def _encode_json(payload: Mapping[str, Any], service: ServiceHost) -> bytes:
    try:
        return json.dumps(payload, allow_nan=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError, UnicodeEncodeError):
        raise RequestFailedError(service.value) from None


def _request_headers(
    credential_headers: Mapping[str, str],
    has_json_body: bool,
) -> dict[str, str]:
    """Copy headers, ensure one JSON content type, and identify the platform."""

    headers: dict[str, str] = {}
    lower_names: set[str] = set()
    for name, value in credential_headers.items():
        normalized_name = name.lower()
        if normalized_name == "content-type" and has_json_body:
            continue
        if normalized_name in lower_names:
            continue
        headers[name] = value
        lower_names.add(normalized_name)
    if has_json_body:
        headers["Content-Type"] = "application/json"
    if "platform" not in lower_names:
        # The browser sends platform on every Wolt request, and
        # payment-service rejects requests without it (HTTP 422, isolated by
        # live probes 2026-09). Credential-supplied values take precedence.
        headers["platform"] = "Web"
    return headers


def _build_url(
    service: ServiceHost,
    path: str,
    query: Mapping[str, Any] | None,
) -> str:
    url = f"{BASE_URLS[service]}{path}"
    if query:
        return f"{url}?{urlencode(query, doseq=True)}"
    return url


def _status_code(response: Any, service: ServiceHost) -> int:
    getcode = getattr(response, "getcode", None)
    status_code = getcode() if callable(getcode) else getattr(response, "status", None)
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        raise ResponseShapeError(service.value)
    return status_code


def _close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except OSError:
            pass


def _decode_json_object(response_body: Any, service: ServiceHost) -> dict[str, Any]:
    if not isinstance(response_body, bytes) or not response_body:
        raise ResponseShapeError(service.value)
    try:
        payload = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ResponseDecodeError(service.value) from None
    if not isinstance(payload, dict):
        raise ResponseShapeError(service.value)
    return payload
