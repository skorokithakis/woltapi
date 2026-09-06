from __future__ import annotations

import io
import json
import unittest
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from woltapi import (
    Basket,
    HTTPStatusError,
    RequestFailedError,
    RequestTimeoutError,
    ResponseShapeError,
    SessionCredentials,
    WoltClient,
    WoltTransport,
)
from woltapi.services import ServiceHost


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
    def test_documented_saved_basket_rebuild_posts_recomputed_items(self) -> None:
        restaurant_slug = "restaurant-slug-placeholder"
        saved_item_id = "saved-item-placeholder"
        selected_configuration_id = "selected-configuration-placeholder"
        empty_configuration_id = "empty-configuration-placeholder"
        selected_value_id = "selected-value-placeholder"
        baskets_page = {
            "baskets": [
                {
                    "venue": {
                        "id": "venue-id-placeholder",
                        "name": "Restaurant name placeholder",
                        "slug": restaurant_slug,
                        "country": "FI",
                        "available": True,
                    },
                    "items": [
                        {
                            "id": saved_item_id,
                            "name": "Saved item name placeholder",
                            "count": 2,
                            "price": 1,
                            "substitution_settings": {"is_allowed": True},
                            "options": [
                                {
                                    "id": selected_configuration_id,
                                    "values": [{"id": selected_value_id, "count": 2}],
                                },
                                {"id": empty_configuration_id, "values": []},
                            ],
                        }
                    ],
                }
            ]
        }
        assortment = {
            "items": [
                {
                    "id": saved_item_id,
                    "name": [{"lang": "en", "value": "Menu item placeholder"}],
                    "price": 500,
                    "checksum": "checksum-placeholder",
                    "allowed_delivery_methods": ["homedelivery"],
                    "restrictions": [],
                    "alcohol_permille": 0,
                    "product_hierarchy_tags": [],
                    "vat_percentage": 14,
                    "vat_percentage_decimal": "14.0",
                    "options": [
                        {
                            "id": selected_configuration_id,
                            "option_id": "selected-root-option-placeholder",
                            "prerequisite_values": [],
                            "multi_choice_config": {
                                "total_range": {"min": 0, "max": 2},
                                "max_single_selections": 2,
                                "free_selections": 0,
                            },
                        },
                        {
                            "id": empty_configuration_id,
                            "option_id": "empty-root-option-placeholder",
                            "prerequisite_values": [],
                            "multi_choice_config": {
                                "total_range": {"min": 0, "max": 1},
                                "max_single_selections": 1,
                                "free_selections": 0,
                            },
                        },
                    ],
                }
            ],
            "categories": [{"id": "category-placeholder", "item_ids": [saved_item_id]}],
            "options": [
                {
                    "id": "selected-root-option-placeholder",
                    "type": "multi_choice",
                    "values": [{"id": selected_value_id, "price": 75}],
                },
                {
                    "id": "empty-root-option-placeholder",
                    "type": "choice",
                    "values": [{"id": "empty-value-placeholder", "price": 20}],
                },
            ],
        }
        client, opener = make_client(
            FakeResponse(baskets_page),
            FakeResponse(assortment),
            FakeResponse(
                {
                    "venue": {
                        "id": "venue-id-placeholder",
                        "country": "FI",
                        "currency": "EUR",
                        "self_delivery": False,
                    }
                }
            ),
            FakeResponse(
                {
                    "id": "saved-basket-id-placeholder",
                    "venue_id": "venue-id-placeholder",
                }
            ),
        )

        page = client.get_baskets_page(60.17, 24.94)
        saved = next(
            entry
            for entry in page["baskets"]
            if entry["venue"]["slug"] == restaurant_slug
        )
        menu = client.get_assortment(restaurant_slug)
        basket = Basket.from_saved_basket(menu, saved, "en")
        basket.set_count(saved_item_id, 3)
        items = basket.item_selections()
        self.assertEqual(
            [option.configuration_id for option in items[0].options],
            [selected_configuration_id],
        )
        client.save_basket_items(
            menu,
            venue=client.get_venue_checkout_context(restaurant_slug),
            items=items,
        )

        posted = json.loads(opener.requests[-1].data.decode("utf-8"))
        self.assertEqual(
            posted,
            {
                "venue_id": "venue-id-placeholder",
                "currency": "EUR",
                "items": [
                    {
                        "id": saved_item_id,
                        "count": 3,
                        "name": "Menu item placeholder",
                        "price": 1950,
                        "options": [
                            {
                                "id": selected_configuration_id,
                                "values": [
                                    {
                                        "id": selected_value_id,
                                        "count": 2,
                                        "price": 75,
                                    }
                                ],
                            },
                            {"id": empty_configuration_id, "values": []},
                        ],
                        "substitution_settings": {"is_allowed": True},
                    }
                ],
            },
        )
        self.assertEqual(
            urlsplit(opener.requests[-1].full_url).path,
            "/order-xp/v1/baskets",
        )
        self.assertIn(
            empty_configuration_id,
            [option["id"] for option in posted["items"][0]["options"]],
        )

    def test_basket_reads_use_consumer_routes_and_preserve_raw_pages(self) -> None:
        venue_basket = {"id": "basket-1", "venue_id": "venue-1", "items": []}
        baskets_page = {"baskets": [venue_basket], "future_field": {"kept": True}}
        client, opener = make_client(
            FakeResponse({"count": 1}),
            FakeResponse(baskets_page),
        )

        count = client.get_basket_count()
        returned_baskets_page = client.get_baskets_page(60.17, 24.94)

        self.assertEqual(count, 1)
        self.assertEqual(returned_baskets_page, baskets_page)
        routes = [urlsplit(request.full_url) for request in opener.requests]
        self.assertEqual(
            [(route.netloc, route.path) for route in routes],
            [
                ("consumer-api.wolt.com", "/order-xp/v1/baskets/count"),
                ("consumer-api.wolt.com", "/order-xp/web/v1/pages/baskets"),
            ],
        )
        self.assertEqual(
            parse_qs(routes[1].query), {"lat": ["60.17"], "lon": ["24.94"]}
        )
        for request in opener.requests:
            headers = _headers(request)
            self.assertEqual(headers["x-consumer-session"], "consumer-secret")
            self.assertNotIn("x-restaurant-session", headers)

    def test_delete_baskets_posts_ids_and_accepts_null_response(self) -> None:
        client, opener = make_client(FakeResponse(None))

        result = client.delete_baskets(["basket-1", "basket-2"])

        self.assertIsNone(result)
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(urlsplit(request.full_url).netloc, "consumer-api.wolt.com")
        self.assertEqual(
            urlsplit(request.full_url).path,
            "/order-xp/v1/baskets/bulk/delete",
        )
        headers = _headers(request)
        self.assertEqual(headers["x-consumer-session"], "consumer-secret")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")), {"ids": ["basket-1", "basket-2"]}
        )

    def test_transport_rejects_null_body_when_response_is_expected(self) -> None:
        client, _ = make_client(FakeResponse(None))

        with self.assertRaises(ResponseShapeError):
            client._transport.request(
                ServiceHost.CONSUMER, "POST", "/order-xp/v1/baskets/bulk/delete"
            )

    def test_delete_baskets_validates_ids_before_request(self) -> None:
        for basket_ids, error_type in (
            ([], ValueError),
            ("basket-1", TypeError),
            (None, TypeError),
            (1, TypeError),
            (b"basket-1", TypeError),
            ([1], ValueError),
            (["  "], ValueError),
        ):
            with self.subTest(basket_ids=basket_ids):
                client, opener = make_client()

                with self.assertRaises(error_type):
                    client.delete_baskets(basket_ids)  # type: ignore[arg-type]

                self.assertEqual(opener.requests, [])

    def test_get_basket_count_rejects_non_integer_counts(self) -> None:
        for payload in ({}, {"count": True}, {"count": "1"}):
            with self.subTest(payload=payload):
                client, _ = make_client(FakeResponse(payload))
                with self.assertRaises(ResponseShapeError):
                    client.get_basket_count()

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

    def test_get_venue_checkout_context_extracts_static_venue_fields(self) -> None:
        client, opener = make_client(
            FakeResponse(
                {
                    "venue": {
                        "id": "venue-1",
                        "country": "FI",
                        "currency": "EUR",
                        "self_delivery": False,
                    }
                }
            )
        )

        context = client.get_venue_checkout_context("venue-one")

        self.assertEqual(context.id, "venue-1")
        self.assertEqual(context.country, "FI")
        self.assertEqual(context.currency, "EUR")
        self.assertIs(context.self_delivery, False)
        self.assertIsNone(context.preorder_config)
        self.assertEqual(
            urlsplit(opener.requests[0].full_url).path,
            "/order-xp/web/v1/pages/venue/slug/venue-one/static",
        )

    def test_get_venue_checkout_context_rejects_invalid_venue_fields(self) -> None:
        valid_venue = {
            "id": "venue-1",
            "country": "FI",
            "currency": "EUR",
            "self_delivery": False,
        }
        invalid_venues = [{}, {"venue": []}]
        for field, invalid_value in (
            ("id", True),
            ("country", True),
            ("currency", True),
            ("self_delivery", 1),
        ):
            venue = dict(valid_venue)
            venue[field] = invalid_value
            invalid_venues.append({"venue": venue})
        for field in valid_venue:
            venue = dict(valid_venue)
            del venue[field]
            invalid_venues.append({"venue": venue})
        for field in ("id", "country", "currency"):
            venue = dict(valid_venue)
            venue[field] = ""
            invalid_venues.append({"venue": venue})

        for payload in invalid_venues:
            with self.subTest(payload=payload):
                client, _ = make_client(FakeResponse(payload))
                with self.assertRaises(ResponseShapeError) as raised:
                    client.get_venue_checkout_context("venue-one")
                self.assertEqual(raised.exception.service, "consumer")

    def test_platform_header_defaults_to_web_and_respects_overrides(self) -> None:
        client, opener = make_client(FakeResponse({"orders": []}))
        client.get_orders_page()
        self.assertEqual(_headers(opener.requests[0])["platform"], "Web")

        credentials = SessionCredentials(
            consumer_headers={"platform": "Synthetic-Platform"}
        )
        override_opener = FakeOpener([FakeResponse({"orders": []})])
        override_client = WoltClient(
            credentials,
            _transport=WoltTransport(credentials, _opener=override_opener),
        )
        override_client.get_orders_page()
        self.assertEqual(
            _headers(override_opener.requests[0])["platform"], "Synthetic-Platform"
        )

    def test_delivery_and_payment_summaries_include_display_fields(self) -> None:
        client, opener = make_client(
            FakeResponse(
                {
                    "results": [
                        {
                            "id": "delivery-1",
                            "alias": "Example home",
                            "label_type": "home",
                            "address": "PRIVATE ADDRESS",
                            "phone_number": "PRIVATE PHONE",
                            "location": {
                                "address": "1 Example Street",
                                "city": "Example City",
                                "postcode": "12345",
                                "latitude": 1,
                                "longitude": 2,
                            },
                        },
                        {
                            "id": "delivery-2",
                            "alias": 1,
                            "label_type": [],
                            "location": {
                                "address": {},
                                "city": 2,
                                "postcode": None,
                            },
                        },
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
                                        "title": "Example card",
                                        "subtitle": "Example bank",
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
                                        "is_enabled": True,
                                        "title": [],
                                        "subtitle": {"unexpected": "shape"},
                                        "method": {"id": "card-2", "type": "card"},
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
        self.assertEqual(targets[0].alias, "Example home")
        self.assertEqual(targets[0].label_type, "home")
        self.assertEqual(targets[0].address, "1 Example Street")
        self.assertEqual(targets[0].city, "Example City")
        self.assertEqual(targets[0].postcode, "12345")
        self.assertEqual(targets[1].alias, None)
        self.assertEqual(targets[1].label_type, None)
        self.assertEqual(targets[1].address, None)
        self.assertEqual(targets[1].city, None)
        self.assertEqual(targets[1].postcode, None)
        self.assertEqual(client._delivery_target_ids, {"delivery-1", "delivery-2"})
        target_repr = repr(targets[0])
        self.assertIn("delivery-1", target_repr)
        for private in (
            "Example home",
            "home",
            "1 Example Street",
            "Example City",
            "12345",
            "PRIVATE ADDRESS",
            "PRIVATE PHONE",
        ):
            self.assertNotIn(private, target_repr)
        self.assertEqual(cards[0].id, "card-1")
        self.assertEqual(cards[0].type, "card")
        self.assertTrue(cards[0].is_selected)
        self.assertEqual(cards[0].title, "Example card")
        self.assertEqual(cards[0].subtitle, "Example bank")
        self.assertEqual(cards[1].title, None)
        self.assertEqual(cards[1].subtitle, None)
        self.assertFalse(hasattr(cards[0], "masked_number"))
        card_repr = repr(cards[0])
        self.assertIn("card-1", card_repr)
        for private in ("Example card", "Example bank", "masked-synthetic-card"):
            self.assertNotIn(private, card_repr)
        self.assertEqual(set(client._payment_eligibility_by_id), {"card-1", "card-2"})
        self.assertNotIn(
            "masked-synthetic-card", repr(client._payment_eligibility_by_id)
        )
        self.assertNotIn("synthetic-bin", repr(client._payment_eligibility_by_id))
        self.assertEqual(len(cards), 2)
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
