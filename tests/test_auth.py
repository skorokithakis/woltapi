from __future__ import annotations

import io
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from tests.test_client import FakeOpener, FakeResponse
from woltapi import (
    HTTPStatusError,
    RefreshTokenCredentials,
    RequestFailedError,
    RequestTimeoutError,
    ResponseDecodeError,
    ResponseShapeError,
    WoltClient,
    WoltTransport,
)
from woltapi.services import ServiceHost
from woltapi.transport import _NoRedirect


def _token_response(
    *,
    access_token: str = "access-token-1",
    refresh_token: str = "refresh-token-1",
    expires_in: Any = 120,
    token_type: Any = "Bearer",
) -> dict[str, Any]:
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "token_type": token_type,
    }


def _make_credentials(
    *responses: Any,
    refresh_token: str = "initial-refresh-token",
    **credential_kwargs: Any,
) -> tuple[RefreshTokenCredentials, FakeOpener]:
    opener = FakeOpener(list(responses))
    credentials = RefreshTokenCredentials(
        refresh_token,
        _opener=opener,
        **credential_kwargs,
    )
    return credentials, opener


def _headers(request: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


def _form_values(request: Any) -> dict[str, list[str]]:
    assert isinstance(request.data, bytes)
    return parse_qs(
        request.data.decode("ascii"), keep_blank_values=True, strict_parsing=True
    )


class BlockingOpener:
    """A synthetic opener that holds one refresh in flight for worker contention."""

    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self._lock = threading.Lock()
        self.requests: list[Any] = []
        self.timeouts: list[float] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def open(
        self, request: Any, data: bytes | None = None, timeout: float = 0
    ) -> FakeResponse:
        with self._lock:
            self.requests.append(request)
            self.timeouts.append(timeout)
        self.started.set()
        if not self.release.wait(timeout=2.0):
            raise TimeoutError("synthetic opener was not released")
        return self._response


class RefreshTokenCredentialsTests(unittest.TestCase):
    def test_initial_refresh_uses_an_isolated_form_request(self) -> None:
        response = FakeResponse(_token_response())
        credentials, opener = _make_credentials(
            response,
            refresh_token="initial+refresh/token=value",
            restaurant_headers={
                "Authorization": "Basic restaurant-secret",
                "X-Restaurant-Session": "restaurant-secret",
            },
            consumer_headers={"X-Consumer-Session": "consumer-secret"},
            payment_headers={"X-Payment-Session": "payment-secret"},
        )

        self.assertEqual(opener.requests, [])
        headers = credentials.headers_for(ServiceHost.RESTAURANT)

        self.assertEqual(headers["Authorization"], "Bearer access-token-1")
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        endpoint = urlsplit(request.full_url)
        self.assertEqual(
            (endpoint.scheme, endpoint.netloc, endpoint.path, endpoint.query),
            ("https", "authentication.wolt.com", "/v1/wauth2/access_token", ""),
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            request.data,
            b"grant_type=refresh_token&refresh_token=initial%2Brefresh%2Ftoken%3Dvalue",
        )
        self.assertEqual(
            _form_values(request),
            {
                "grant_type": ["refresh_token"],
                "refresh_token": ["initial+refresh/token=value"],
            },
        )
        form_headers = _headers(request)
        self.assertEqual(
            form_headers["content-type"], "application/x-www-form-urlencoded"
        )
        self.assertEqual(form_headers["accept"], "application/json")
        self.assertEqual(form_headers["origin"], "https://wolt.com")
        self.assertNotIn("authorization", form_headers)
        self.assertNotIn("x-restaurant-session", form_headers)
        self.assertNotIn("x-consumer-session", form_headers)
        self.assertNotIn("x-payment-session", form_headers)
        self.assertTrue(response.closed)

    def test_refresh_is_lazy_cached_and_repeated_at_early_expiry(self) -> None:
        first_response = FakeResponse(
            _token_response(
                access_token="access-token-1",
                refresh_token="rotated-refresh-token-1",
                expires_in=120,
            )
        )
        second_response = FakeResponse(
            _token_response(
                access_token="access-token-2",
                refresh_token="rotated-refresh-token-2",
                expires_in=120,
            )
        )
        clock = [100.0]

        with patch("woltapi.auth.time.monotonic", side_effect=lambda: clock[0]):
            credentials, opener = _make_credentials(first_response, second_response)
            self.assertEqual(opener.requests, [])

            first_headers = credentials.headers_for(ServiceHost.RESTAURANT)
            self.assertEqual(first_headers["Authorization"], "Bearer access-token-1")
            self.assertEqual(len(opener.requests), 1)

            clock[0] = 189.999
            cached_headers = credentials.headers_for(ServiceHost.CONSUMER)
            self.assertEqual(cached_headers["Authorization"], "Bearer access-token-1")
            self.assertEqual(len(opener.requests), 1)

            clock[0] = 190.0
            renewed_headers = credentials.headers_for(ServiceHost.PAYMENT)
            self.assertEqual(renewed_headers["Authorization"], "Bearer access-token-2")

        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(
            [_form_values(request)["refresh_token"] for request in opener.requests],
            [["initial-refresh-token"], ["rotated-refresh-token-1"]],
        )
        self.assertTrue(first_response.closed)
        self.assertTrue(second_response.closed)

    def test_explicit_refresh_reuses_rotated_token_and_notifies_callback(self) -> None:
        first_response = FakeResponse(
            _token_response(
                access_token="access-token-1",
                refresh_token="rotated-refresh-token-1",
                expires_in=3600,
            )
        )
        second_response = FakeResponse(
            _token_response(
                access_token="access-token-2",
                refresh_token="rotated-refresh-token-2",
                expires_in=3600,
            )
        )
        refreshed_tokens: list[str] = []
        credentials, opener = _make_credentials(
            first_response,
            second_response,
            on_refresh=refreshed_tokens.append,
        )

        credentials.refresh()
        credentials.refresh()
        headers = credentials.headers_for(ServiceHost.CONSUMER)

        self.assertEqual(headers["Authorization"], "Bearer access-token-2")
        self.assertEqual(credentials.refresh_token, "rotated-refresh-token-2")
        self.assertEqual(
            refreshed_tokens,
            ["rotated-refresh-token-1", "rotated-refresh-token-2"],
        )
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(
            [_form_values(request)["refresh_token"] for request in opener.requests],
            [["initial-refresh-token"], ["rotated-refresh-token-1"]],
        )
        self.assertTrue(first_response.closed)
        self.assertTrue(second_response.closed)

    def test_per_host_headers_replace_authorization_and_are_immutable(self) -> None:
        restaurant_headers = {
            "Authorization": "Basic restaurant-secret",
            "X-Restaurant-Session": "restaurant-session",
        }
        consumer_headers = {
            "authorization": "Basic consumer-secret",
            "X-Consumer-Session": "consumer-session",
        }
        payment_headers = {
            "Authorization": "Basic payment-secret",
            "X-Payment-Session": "payment-session",
        }
        credentials, opener = _make_credentials(
            FakeResponse(_token_response()),
            restaurant_headers=restaurant_headers,
            consumer_headers=consumer_headers,
            payment_headers=payment_headers,
        )
        restaurant_headers["X-Added-Later"] = "must-not-appear"
        consumer_headers["X-Consumer-Session"] = "mutated-after-construction"

        returned = {
            ServiceHost.RESTAURANT: credentials.headers_for(ServiceHost.RESTAURANT),
            ServiceHost.CONSUMER: credentials.headers_for(ServiceHost.CONSUMER),
            ServiceHost.PAYMENT: credentials.headers_for(ServiceHost.PAYMENT),
        }

        self.assertEqual(
            dict(returned[ServiceHost.RESTAURANT]),
            {
                "X-Restaurant-Session": "restaurant-session",
                "Authorization": "Bearer access-token-1",
            },
        )
        self.assertEqual(
            dict(returned[ServiceHost.CONSUMER]),
            {
                "X-Consumer-Session": "consumer-session",
                "Authorization": "Bearer access-token-1",
            },
        )
        self.assertEqual(
            dict(returned[ServiceHost.PAYMENT]),
            {
                "X-Payment-Session": "payment-session",
                "Authorization": "Bearer access-token-1",
            },
        )
        self.assertEqual(restaurant_headers["Authorization"], "Basic restaurant-secret")
        self.assertEqual(consumer_headers["authorization"], "Basic consumer-secret")
        self.assertEqual(payment_headers["Authorization"], "Basic payment-secret")
        for service, headers in returned.items():
            with self.subTest(service=service), self.assertRaises(TypeError):
                headers["X-Injected"] = "not-allowed"
        self.assertEqual(len(opener.requests), 1)

    def test_api_401_is_not_retried_after_a_successful_refresh(self) -> None:
        auth_response = FakeResponse(_token_response())
        api_response = FakeResponse({"error": "synthetic"}, status=401)
        credentials, opener = _make_credentials(auth_response, api_response)
        transport = WoltTransport(credentials, _opener=opener)
        client = WoltClient(credentials, _transport=transport)

        with self.assertRaises(HTTPStatusError) as raised:
            client.list_delivery_targets()

        self.assertEqual(raised.exception.service, "restaurant")
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(
            [urlsplit(request.full_url).netloc for request in opener.requests],
            ["authentication.wolt.com", "restaurant-api.wolt.com"],
        )
        self.assertEqual(
            _headers(opener.requests[1])["authorization"], "Bearer access-token-1"
        )
        self.assertTrue(auth_response.closed)
        self.assertTrue(api_response.closed)

    def test_auth_failures_prevent_downstream_api_calls(self) -> None:
        failures: tuple[tuple[str, Any, type[BaseException]], ...] = (
            (
                "http status",
                FakeResponse({"error": "synthetic"}, status=401),
                HTTPStatusError,
            ),
            (
                "network failure",
                URLError("synthetic private detail"),
                RequestFailedError,
            ),
            ("timeout", TimeoutError("synthetic private detail"), RequestTimeoutError),
        )

        for name, failure, expected_error in failures:
            with self.subTest(name=name):
                credentials, opener = _make_credentials(failure)
                transport = WoltTransport(credentials, _opener=opener)
                client = WoltClient(credentials, _transport=transport)

                with self.assertRaises(expected_error) as raised:
                    client.list_delivery_targets()

                self.assertEqual(raised.exception.service, "authentication")
                self.assertEqual(len(opener.requests), 1)
                self.assertEqual(
                    urlsplit(opener.requests[0].full_url).netloc,
                    "authentication.wolt.com",
                )
                if isinstance(failure, FakeResponse):
                    self.assertTrue(failure.closed)

    def test_tokens_are_absent_from_repr_and_errors(self) -> None:
        initial_token = "initial-private-refresh-token"
        access_token = "access-private-token"
        rotated_token = "rotated-private-refresh-token"
        credentials, _ = _make_credentials(
            FakeResponse(
                _token_response(
                    access_token=access_token,
                    refresh_token=rotated_token,
                )
            ),
            refresh_token=initial_token,
        )
        credentials.headers_for(ServiceHost.RESTAURANT)

        self.assertEqual(repr(credentials), "RefreshTokenCredentials()")
        for token in (initial_token, access_token, rotated_token):
            self.assertNotIn(token, repr(credentials))

        malformed_response = FakeResponse(
            _token_response(
                access_token=access_token,
                refresh_token=rotated_token,
                token_type="private-non-bearer-token-type",
            )
        )
        malformed_credentials, _ = _make_credentials(
            malformed_response,
            refresh_token=initial_token,
        )
        with self.assertRaises(ResponseShapeError) as malformed_error:
            malformed_credentials.headers_for(ServiceHost.RESTAURANT)

        error_body = io.BytesIO(b'{"access_token":"access-private-token"}')
        http_error = HTTPError(
            (
                "https://authentication.wolt.com/v1/wauth2/access_token"
                "?refresh_token=initial-private-refresh-token"
            ),
            401,
            "initial-private-refresh-token was rejected",
            None,
            error_body,
        )
        failed_credentials, _ = _make_credentials(
            http_error,
            refresh_token=initial_token,
        )
        with self.assertRaises(HTTPStatusError) as http_status_error:
            failed_credentials.headers_for(ServiceHost.RESTAURANT)

        for token in (initial_token, access_token, rotated_token):
            self.assertNotIn(token, str(malformed_error.exception))
            self.assertNotIn(token, str(http_status_error.exception))
        self.assertTrue(malformed_response.closed)
        self.assertTrue(error_body.closed)

    def test_malformed_auth_fields_are_rejected_and_responses_closed(self) -> None:
        valid = _token_response()
        malformed_payloads: tuple[tuple[str, dict[str, Any]], ...] = (
            ("empty access token", {**valid, "access_token": ""}),
            ("non-ascii access token", {**valid, "access_token": "t\u00f6k\u00e9n"}),
            (
                "refresh token with whitespace",
                {**valid, "refresh_token": "refresh token"},
            ),
            ("missing refresh token", {**valid, "refresh_token": None}),
            ("boolean expiry", {**valid, "expires_in": True}),
            ("zero expiry", {**valid, "expires_in": 0}),
            ("non-finite expiry", {**valid, "expires_in": float("nan")}),
            ("non-bearer token type", {**valid, "token_type": "Basic"}),
            ("missing token type", {**valid, "token_type": None}),
        )

        for name, payload in malformed_payloads:
            with self.subTest(name=name):
                response = FakeResponse(payload)
                credentials, opener = _make_credentials(response)

                with self.assertRaises(ResponseShapeError) as raised:
                    credentials.headers_for(ServiceHost.RESTAURANT)

                self.assertEqual(raised.exception.service, "authentication")
                self.assertEqual(len(opener.requests), 1)
                self.assertTrue(response.closed)

    def test_default_opener_installs_a_redirect_refusal_handler(self) -> None:
        credentials = RefreshTokenCredentials("initial-refresh-token")
        handler = next(
            handler
            for handler in credentials._opener.handlers
            if isinstance(handler, _NoRedirect)
        )
        request = Request("https://authentication.wolt.com/v1/wauth2/access_token")

        redirected_request = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://untrusted.example/access_token",
        )

        self.assertIsNone(redirected_request)

    def test_invalid_input_is_rejected_without_a_request(self) -> None:
        for token in (None, "", "Bearer secret", "secret\n", "secret\x7f", "\u00e9"):
            with self.subTest(token=token):
                opener = FakeOpener([])
                with self.assertRaises(ValueError):
                    RefreshTokenCredentials(token, _opener=opener)
                self.assertEqual(opener.requests, [])

    def test_invalid_response_body_does_not_change_tokens(self) -> None:
        for body, expected in (
            (b"not-json-private-secret", ResponseDecodeError),
            (b"\xff", ResponseDecodeError),
            (b"[]", ResponseShapeError),
            (b"", ResponseShapeError),
        ):
            with self.subTest(body=body):
                response = FakeResponse({})
                response._body = body
                credentials, _ = _make_credentials(response)
                with self.assertRaises(expected) as raised:
                    credentials.refresh()
                self.assertNotIn("private-secret", str(raised.exception))
                self.assertEqual(credentials.refresh_token, "initial-refresh-token")
                self.assertTrue(response.closed)

    def test_callback_failure_keeps_rotated_token_and_stops_api_call(self) -> None:
        def fail_to_save(token: str) -> None:
            self.assertEqual(token, "refresh-token-1")
            raise OSError("Secret store unavailable")

        credentials, opener = _make_credentials(
            FakeResponse(_token_response()),
            on_refresh=fail_to_save,
        )
        client = WoltClient(
            credentials,
            _transport=WoltTransport(credentials, _opener=opener),
        )
        with self.assertRaises(OSError):
            client.get_orders_page()
        with self.assertRaises(OSError):
            client.get_orders_page()
        with self.assertRaises(OSError):
            credentials.refresh()
        self.assertEqual(credentials.refresh_token, "refresh-token-1")
        self.assertEqual(len(opener.requests), 1)

    def test_failed_persistence_is_retried_without_another_exchange(self) -> None:
        saved = []

        def save(token: str) -> None:
            saved.append(token)
            if len(saved) == 1:
                raise OSError("Secret store unavailable")

        credentials, opener = _make_credentials(
            FakeResponse(_token_response()),
            FakeResponse({}),
            on_refresh=save,
        )
        client = WoltClient(
            credentials,
            _transport=WoltTransport(credentials, _opener=opener),
        )
        with self.assertRaises(OSError):
            client.get_orders_page()
        self.assertEqual(client.get_orders_page(), {})
        self.assertEqual(saved, ["refresh-token-1", "refresh-token-1"])
        self.assertEqual(len(opener.requests), 2)

    def test_concurrent_first_use_shares_one_refresh_request(self) -> None:
        response = FakeResponse(
            _token_response(
                access_token="concurrent-access-token",
                refresh_token="concurrent-refresh-token",
                expires_in=3600,
            )
        )
        opener = BlockingOpener(response)
        credentials = RefreshTokenCredentials(
            "initial-refresh-token",
            _opener=opener,
        )
        services = (
            ServiceHost.RESTAURANT,
            ServiceHost.CONSUMER,
            ServiceHost.PAYMENT,
            ServiceHost.RESTAURANT,
        )
        start = threading.Barrier(len(services))

        def get_headers(service: ServiceHost) -> dict[str, str]:
            start.wait(timeout=2.0)
            return dict(credentials.headers_for(service))

        with ThreadPoolExecutor(max_workers=len(services)) as executor:
            futures = [executor.submit(get_headers, service) for service in services]
            try:
                self.assertTrue(opener.started.wait(timeout=2.0))
            finally:
                opener.release.set()
            headers = [future.result(timeout=2.0) for future in futures]

        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(
            headers,
            [{"Authorization": "Bearer concurrent-access-token"}] * len(services),
        )
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
