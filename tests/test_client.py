from __future__ import annotations

import io
import json
import unittest
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from woltapi import (
    HTTPStatusError,
    RequestFailedError,
    RequestTimeoutError,
    ResponseShapeError,
    SessionCredentials,
    WoltClient,
    WoltTransport,
)


class FakeResponse:
    def __init__(self, payload: Any, *, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")
        self.closed = False

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = responses
        self.requests: list[Any] = []
        self.timeouts: list[float] = []

    def open(
        self, request: Any, data: bytes | None = None, timeout: float = 0
    ) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def make_client(
    *responses: Any, timeout: float = 10.0
) -> tuple[WoltClient, FakeOpener]:
    credentials = SessionCredentials(
        restaurant_headers={"X-Restaurant-Session": "restaurant-secret"},
        consumer_headers={"X-Consumer-Session": "consumer-secret"},
        payment_headers={"X-Payment-Session": "payment-secret"},
    )
    opener = FakeOpener(list(responses))
    transport = WoltTransport(credentials, timeout=timeout, _opener=opener)
    return WoltClient(credentials, _transport=transport), opener


class WoltClientTests(unittest.TestCase):
    def test_documented_routes_use_the_correct_hosts_and_preserve_catalog_data(
        self,
    ) -> None:
        assortment = {
            "items": [
                {
                    "id": "item-1",
                    "checksum": "synthetic-checksum",
                    "restrictions": ["synthetic-restriction"],
                    "options": [{"id": "item-option-1", "option_id": "root-option-1"}],
                }
            ],
            "options": [{"id": "root-option-1", "values": [{"id": "value-1"}]}],
            "future_catalog_field": {"kept": True},
        }
        client, opener = make_client(
            FakeResponse(
                {
                    "sections": [
                        {
                            "items": [
                                {
                                    "title": "Synthetic venue",
                                    "venue": {
                                        "id": "venue-1",
                                        "slug": "venue-one",
                                        "currency": "EUR",
                                        "delivers": True,
                                        "online": False,
                                    },
                                }
                            ]
                        },
                        {"items": [{"title": "Not a venue"}]},
                    ]
                }
            ),
            FakeResponse({"venue": {"id": "venue-1"}}),
            FakeResponse({"delivery_configs": []}),
            FakeResponse({"sections": []}),
            FakeResponse(assortment),
            FakeResponse({"item": {"id": "item-1", "checksum": "detail-checksum"}}),
        )

        venues = client.search_venues(
            "synthetic lunch", latitude=60.17, longitude=24.94
        )
        static = client.get_venue_static("venue-one")
        dynamic = client.get_venue_dynamic("venue-one", latitude=60.17, longitude=24.94)
        content = client.get_venue_content("venue-one")
        returned_assortment = client.get_assortment("venue-one")
        item = client.get_item("venue-1", "item-1", language="en")

        self.assertEqual(venues[0].id, "venue-1")
        self.assertEqual(venues[0].slug, "venue-one")
        self.assertEqual(venues[0].title, "Synthetic venue")
        self.assertEqual(static["venue"]["id"], "venue-1")
        self.assertEqual(dynamic, {"delivery_configs": []})
        self.assertEqual(content, {"sections": []})
        self.assertEqual(returned_assortment, assortment)
        self.assertEqual(item["item"]["checksum"], "detail-checksum")

        routes = [urlsplit(request.full_url) for request in opener.requests]
        self.assertEqual(
            [(route.netloc, route.path) for route in routes],
            [
                ("restaurant-api.wolt.com", "/v1/pages/search"),
                (
                    "consumer-api.wolt.com",
                    "/order-xp/web/v1/pages/venue/slug/venue-one/static",
                ),
                (
                    "consumer-api.wolt.com",
                    "/order-xp/web/v1/venue/slug/venue-one/dynamic/",
                ),
                (
                    "consumer-api.wolt.com",
                    "/consumer-api/venue-content-api/v3/web/venue-content/slug/venue-one",
                ),
                (
                    "consumer-api.wolt.com",
                    "/consumer-api/consumer-assortment/v1/venues/slug/venue-one/assortment",
                ),
                (
                    "consumer-api.wolt.com",
                    "/order-xp/web/v1/pages/venue/venue-1/item/item-1",
                ),
            ],
        )
        self.assertEqual(
            json.loads(opener.requests[0].data.decode("utf-8")),
            {"q": "synthetic lunch", "target": None, "lat": 60.17, "lon": 24.94},
        )
        self.assertEqual(
            parse_qs(routes[2].query),
            {
                "lat": ["60.17"],
                "lon": ["24.94"],
                "selected_delivery_method": ["homedelivery"],
            },
        )
        self.assertEqual(parse_qs(routes[5].query), {"language": ["en"]})

        first_headers = _headers(opener.requests[0])
        consumer_headers = _headers(opener.requests[1])
        self.assertEqual(first_headers["x-restaurant-session"], "restaurant-secret")
        self.assertNotIn("x-consumer-session", first_headers)
        self.assertEqual(consumer_headers["x-consumer-session"], "consumer-secret")
        self.assertNotIn("x-restaurant-session", consumer_headers)

    def test_delivery_and_payment_summaries_omit_sensitive_context(self) -> None:
        client, opener = make_client(
            FakeResponse(
                {
                    "results": [
                        {
                            "id": "delivery-1",
                            "address": "Synthetic Secret Street",
                            "phone": "+00000000",
                            "location": {"latitude": 1, "longitude": 2},
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "root_element": {
                        "element_type": "list",
                        "children": [
                            {
                                "element_type": "group",
                                "children": [
                                    {
                                        "element_type": "payment-method",
                                        "is_enabled": True,
                                        "is_selected": True,
                                        "is_default": False,
                                        "method": {
                                            "id": "card-1",
                                            "type": "card",
                                            "masked_number": "masked-synthetic-card",
                                            "card_bin": "synthetic-bin",
                                            "expiry": {"month": 1, "year": 2099},
                                        },
                                    },
                                    {
                                        "element_type": "payment-method",
                                        "is_enabled": False,
                                        "method": {"id": "card-2", "type": "card"},
                                    },
                                    {
                                        "element_type": "payment-method",
                                        "is_enabled": True,
                                        "method": {"id": "cash-1", "type": "cash"},
                                    },
                                ],
                            }
                        ],
                    }
                }
            ),
        )

        payment_context = {
            "venue_id": "venue-1",
            "delivery_method": "homedelivery",
            "available_methods": ["card"],
            "items": [
                {
                    "id": "item-1",
                    "alcohol_permille": 0,
                    "product_hierarchy_tags": [],
                    "vat_percentage": 0,
                    "vat_percentage_decimal": "0",
                }
            ],
        }
        targets = client.list_delivery_targets()
        cards = client.get_payment_methods(payment_context)

        self.assertEqual(targets[0].id, "delivery-1")
        self.assertFalse(hasattr(targets[0], "address"))
        self.assertNotIn("Synthetic Secret Street", repr(targets[0]))
        self.assertEqual(client._delivery_target_ids, {"delivery-1"})
        self.assertNotIn("Synthetic Secret Street", repr(client._delivery_target_ids))
        self.assertEqual(cards[0].id, "card-1")
        self.assertEqual(cards[0].type, "card")
        self.assertTrue(cards[0].is_selected)
        self.assertFalse(hasattr(cards[0], "masked_number"))
        self.assertNotIn("masked-synthetic-card", repr(cards[0]))
        self.assertEqual(set(client._payment_eligibility_by_id), {"card-1"})
        self.assertNotIn(
            "masked-synthetic-card", repr(client._payment_eligibility_by_id)
        )
        self.assertNotIn("synthetic-bin", repr(client._payment_eligibility_by_id))
        self.assertEqual(len(cards), 1)
        self.assertEqual(
            json.loads(opener.requests[1].data.decode("utf-8")),
            payment_context,
        )
        payment_headers = _headers(opener.requests[1])
        self.assertEqual(payment_headers["x-payment-session"], "payment-secret")
        self.assertNotIn("x-restaurant-session", payment_headers)

    def test_order_status_preserves_an_unknown_status_string(self) -> None:
        client, _ = make_client(
            FakeResponse(
                {
                    "order_details": {
                        "order_id": "purchase-1",
                        "status": "future-state-v9",
                        "currency": "EUR",
                        "payment_amount": 1234,
                        "total_price": 1234,
                        "delivery_price": 99,
                        "delivery_method": "homedelivery",
                    }
                }
            )
        )

        status = client.get_order_status("purchase-1")

        self.assertEqual(status.status, "future-state-v9")
        self.assertEqual(status.payment_amount, 1234)
        self.assertEqual(status.delivery_price, 99)

    def test_order_status_requires_the_requested_tracking_id(self) -> None:
        for order_id in (None, "different-purchase"):
            with self.subTest(order_id=order_id):
                client, _ = make_client(
                    FakeResponse(
                        {
                            "order_details": {
                                "order_id": order_id,
                                "status": "future-state-v9",
                            }
                        }
                    )
                )
                with self.assertRaises(ResponseShapeError):
                    client.get_order_status("purchase-1")

    def test_missing_or_null_responses_fail_without_exposing_response_data(
        self,
    ) -> None:
        for payload in (None, {}, {"results": None}):
            with self.subTest(payload=payload):
                client, _ = make_client(FakeResponse(payload))
                with self.assertRaises(ResponseShapeError) as raised:
                    client.list_delivery_targets()
                self.assertNotIn("Synthetic Secret Street", str(raised.exception))

    def test_http_error_and_network_failure_are_privacy_safe(self) -> None:
        http_error = HTTPError(
            "https://restaurant-api.wolt.com/v2/delivery/info?location=SyntheticSecret",
            404,
            "not found",
            None,
            io.BytesIO(b'{"address":"Synthetic Secret Street"}'),
        )
        client, _ = make_client(http_error)
        with self.assertRaises(HTTPStatusError) as raised:
            client.list_delivery_targets()
        self.assertEqual(raised.exception.status_code, 404)
        self.assertNotIn("Synthetic Secret Street", str(raised.exception))
        self.assertNotIn("SyntheticSecret", str(raised.exception))

        client, _ = make_client(URLError("Synthetic Secret Street"))
        with self.assertRaises(RequestFailedError) as raised:
            client.list_delivery_targets()
        self.assertNotIn("Synthetic Secret Street", str(raised.exception))

    def test_timeouts_and_redirect_responses_are_not_retried(self) -> None:
        client, opener = make_client(
            TimeoutError("Synthetic Secret Street"), timeout=3.5
        )
        with self.assertRaises(RequestTimeoutError):
            client.list_delivery_targets()
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(opener.timeouts, [3.5])

        client, opener = make_client(FakeResponse({"redirect": "ignored"}, status=302))
        with self.assertRaises(HTTPStatusError) as raised:
            client.list_delivery_targets()
        self.assertEqual(raised.exception.status_code, 302)
        self.assertEqual(len(opener.requests), 1)

    def test_credentials_do_not_appear_in_their_representation(self) -> None:
        credentials = SessionCredentials(
            restaurant_headers={"X-Session": "synthetic-secret"},
        )

        self.assertNotIn("synthetic-secret", repr(credentials))
        self.assertIn("restaurant", repr(credentials))


def _headers(request: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


if __name__ == "__main__":
    unittest.main()
