from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import unittest
from typing import Any
from urllib.parse import urlsplit

from woltapi import (
    DeliverySelection,
    ItemSelection,
    OptionSelection,
    OptionValueSelection,
    ResponseShapeError,
    SelectionError,
    SessionCredentials,
    UnsupportedSelectionError,
    VenueCheckoutContext,
    WoltClient,
    WoltTransport,
    derive_checkout_fields,
)


class FakeResponse:
    def __init__(self, payload: Any, *, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        pass


class FakeOpener:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = responses
        self.requests: list[Any] = []

    def open(
        self, request: Any, data: bytes | None = None, timeout: float = 0
    ) -> FakeResponse:
        self.requests.append(request)
        return self._responses.pop(0)


def make_discovered_client(
    *operation_responses: FakeResponse,
) -> tuple[WoltClient, FakeOpener]:
    credentials = SessionCredentials(
        restaurant_headers={"X-Restaurant-Session": "synthetic-restaurant"},
        consumer_headers={"X-Consumer-Session": "synthetic-consumer"},
        payment_headers={"X-Payment-Session": "synthetic-payment"},
    )
    opener = FakeOpener(
        [
            FakeResponse(
                {
                    "results": [
                        {
                            "id": "delivery-1",
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
                                "element_type": "payment-method",
                                "is_enabled": True,
                                "is_selected": True,
                                "is_default": False,
                                "method": {"id": "card-1", "type": "card"},
                            }
                        ],
                    }
                }
            ),
            *operation_responses,
        ]
    )
    transport = WoltTransport(credentials, _opener=opener)
    client = WoltClient(credentials, _transport=transport)
    client.list_delivery_targets()
    client.get_payment_methods(
        {
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
    )
    return client, opener


def selection_inputs() -> tuple[
    dict[str, Any],
    VenueCheckoutContext,
    DeliverySelection,
    dict[str, Any],
    ItemSelection,
]:
    assortment = {
        "items": [
            {
                "id": "item-1",
                "price": 2000,
                "checksum": "catalog-checksum-1",
                "min_quantity_per_purchase": 1,
                "max_quantity_per_purchase": 2,
                "allowed_delivery_methods": ["homedelivery"],
                "alcohol_permille": 0,
                "restrictions": [],
                "options": [
                    {
                        "id": "item-config-1",
                        "option_id": "root-option-1",
                        "prerequisite_values": [],
                        "multi_choice_config": {
                            "total_range": {"min": 1, "max": 3},
                            "max_single_selections": 3,
                            "free_selections": 0,
                        },
                    }
                ],
            }
        ],
        "categories": [{"id": "category-1", "item_ids": ["item-1"]}],
        "options": [
            {
                "id": "root-option-1",
                "type": "choice",
                "values": [{"id": "value-1", "price": 700}],
            }
        ],
    }
    venue = VenueCheckoutContext(
        id="venue-1",
        country="XX",
        currency="EUR",
        self_delivery=False,
        preorder_config=None,
    )
    delivery = DeliverySelection("delivery-1", latitude=1, longitude=2)
    payment_method = {
        "id": "card-1",
        "type": "card",
        "title": None,
        "number": "synthetic-card-context",
    }
    item = ItemSelection(
        id="item-1",
        count=2,
        basket_name="Synthetic item",
        basket_price=2700,
        end_amount=2700,
        substitution_allowed=False,
        checkout_fields={
            "category_id": "category-1",
            "category_ids": ["category-1"],
            "exclude_from_credits": False,
            "exclude_from_discounts": False,
            "exclude_from_discounts_min_basket": False,
            "alcohol_permille": 0,
            "restrictions": [],
        },
        options=[
            OptionSelection(
                configuration_id="item-config-1",
                values=[OptionValueSelection("value-1", count=3)],
                post_checkout_fields={
                    "type": "Choice",
                    "name": [{"value": "Synthetic option", "lang": "en"}],
                },
            )
        ],
        post_checkout_fields={
            "configIndex": 7,
            "name": [{"value": "Synthetic item", "lang": "en"}],
            "category": "category-1",
            "exclude_from_credits": False,
            "exclude_from_discounts": False,
            "exclude_from_discounts_min_basket": False,
            "from_recommendation": False,
            "alcohol_percentage": 0,
            "product_hierarchy_tags": [],
            "vat_percentage": 0,
            "vat_percentage_decimal": "0",
        },
    )
    return assortment, venue, delivery, payment_method, item


class SelectionTests(unittest.TestCase):
    def test_derives_checkout_fields_from_a_single_category(self) -> None:
        assortment, _, _, _, _ = selection_inputs()

        checkout_fields = derive_checkout_fields(assortment, assortment["items"][0])

        self.assertEqual(
            checkout_fields,
            {
                "category_id": "category-1",
                "category_ids": ["category-1"],
                "exclude_from_credits": False,
                "exclude_from_discounts": False,
                "exclude_from_discounts_min_basket": False,
                "alcohol_permille": 0,
                "restrictions": [],
            },
        )

    def test_checkout_field_derivation_rejects_zero_or_multiple_categories(
        self,
    ) -> None:
        assortment, _, _, _, _ = selection_inputs()
        for categories in (
            [],
            [
                {"id": "category-1", "item_ids": ["item-1"]},
                {"id": "category-2", "item_ids": ["item-1"]},
            ],
        ):
            with self.subTest(categories=categories):
                candidate = deepcopy(assortment)
                candidate["categories"] = categories
                with self.assertRaises(UnsupportedSelectionError):
                    derive_checkout_fields(candidate, candidate["items"][0])

    def test_checkout_field_derivation_keeps_item_exclusion_flags(self) -> None:
        assortment, _, _, _, _ = selection_inputs()
        assortment["items"][0]["exclude_from_discounts"] = True

        checkout_fields = derive_checkout_fields(assortment, assortment["items"][0])

        self.assertTrue(checkout_fields["exclude_from_discounts"])

    def test_checkout_field_derivation_requires_item_fields(self) -> None:
        assortment, _, _, _, _ = selection_inputs()
        for name in ("alcohol_permille", "restrictions"):
            with self.subTest(name=name):
                candidate = deepcopy(assortment)
                del candidate["items"][0][name]
                with self.assertRaises(UnsupportedSelectionError):
                    derive_checkout_fields(candidate, candidate["items"][0])

    def test_distinct_serializers_keep_configuration_ids_counts_and_amounts(
        self,
    ) -> None:
        client, opener = make_discovered_client()
        assortment, venue, delivery, payment_method, item = selection_inputs()

        selection = client.create_selection(
            assortment,
            venue=venue,
            delivery=delivery,
            payment_method=payment_method,
            courier_tip=0,
            items=[item],
        )
        payment_method["number"] = "mutated-after-selection"
        assortment["items"][0]["price"] = 9999
        item.checkout_fields["category_id"] = "mutated-after-selection"

        basket = selection.to_basket_payload()
        checkout = selection.to_checkout_payload()["purchase_plan"]
        post_checkout = selection.to_post_checkout_payload()

        self.assertEqual(len(opener.requests), 2)
        basket_item = basket["items"][0]
        checkout_item = checkout["menu_items"][0]
        post_item = post_checkout["basket"][0]
        self.assertEqual(basket_item["price"], 2700)
        self.assertEqual(basket_item["count"], 2)
        self.assertEqual(basket_item["options"][0]["id"], "item-config-1")
        self.assertNotEqual(basket_item["options"][0]["id"], "root-option-1")
        self.assertEqual(
            basket_item["options"][0]["values"][0],
            {"id": "value-1", "count": 3, "price": 700},
        )
        self.assertEqual(checkout_item["base_price"], 2000)
        self.assertEqual(checkout_item["end_amount"], 2700)
        self.assertEqual(checkout_item["category_id"], "category-1")
        self.assertEqual(checkout_item["options"][0]["id"], "item-config-1")
        self.assertEqual(checkout["payment_methods"][0]["title"], None)
        self.assertNotIn("card_bin", checkout["payment_methods"][0])
        self.assertEqual(
            checkout["payment_methods"][0]["number"], "synthetic-card-context"
        )
        self.assertEqual(post_item["configIndex"], 7)
        self.assertEqual(post_item["baseprice"], 2000)
        self.assertEqual(post_item["end_amount"], 2700)
        self.assertEqual(post_item["options"][0]["type"], "Choice")
        self.assertEqual(post_item["options"][0]["id"], "item-config-1")
        self.assertEqual(post_item["options"][0]["values"], {"value-1": 3})

    def test_basket_and_checkout_include_unselected_options_in_catalog_order(
        self,
    ) -> None:
        client, _ = make_discovered_client()
        assortment, venue, delivery, payment_method, item = selection_inputs()
        assortment["items"][0]["options"].insert(
            0,
            {
                "id": "item-config-2",
                "option_id": "root-option-2",
                "prerequisite_values": [],
                "multi_choice_config": {
                    "total_range": {"min": 0, "max": 1},
                    "max_single_selections": 1,
                    "free_selections": 0,
                },
            },
        )
        assortment["options"].append(
            {
                "id": "root-option-2",
                "type": "choice",
                "values": [{"id": "value-2", "price": 100}],
            }
        )

        selection = client.create_selection(
            assortment,
            venue=venue,
            delivery=delivery,
            payment_method=payment_method,
            courier_tip=0,
            items=[item],
        )

        expected_options = [
            {"id": "item-config-2", "values": []},
            {
                "id": "item-config-1",
                "values": [{"id": "value-1", "count": 3, "price": 700}],
            },
        ]
        self.assertEqual(
            selection.to_basket_payload()["items"][0]["options"], expected_options
        )
        self.assertEqual(
            selection.to_checkout_payload()["purchase_plan"]["menu_items"][0][
                "options"
            ],
            expected_options,
        )
        self.assertEqual(
            selection.to_post_checkout_payload()["basket"][0]["options"],
            [
                {
                    "type": "Choice",
                    "name": [{"value": "Synthetic option", "lang": "en"}],
                    "id": "item-config-1",
                    "values": {"value-1": 3},
                }
            ],
        )

    def test_serializers_skip_malformed_catalog_configurations(self) -> None:
        client, _ = make_discovered_client()
        assortment, venue, delivery, payment_method, item = selection_inputs()
        assortment["items"][0]["options"].insert(0, {"option_id": "root-option-1"})

        selection = client.create_selection(
            assortment,
            venue=venue,
            delivery=delivery,
            payment_method=payment_method,
            courier_tip=0,
            items=[item],
        )

        expected_options = [
            {
                "id": "item-config-1",
                "values": [{"id": "value-1", "count": 3, "price": 700}],
            }
        ]
        self.assertEqual(
            selection.to_basket_payload()["items"][0]["options"], expected_options
        )
        self.assertEqual(
            selection.to_checkout_payload()["purchase_plan"]["menu_items"][0][
                "options"
            ],
            expected_options,
        )

    def test_explicit_basket_quote_and_consent_requests_capture_independent_snapshots(
        self,
    ) -> None:
        client, opener = make_discovered_client(
            FakeResponse({"id": "basket-1", "venue_id": "venue-1"}),
            FakeResponse(
                {
                    "id": "checkout-1",
                    "payable_amount": 3100,
                    "purchase_validation": {
                        "end_amount": 2700,
                        "delivery_price": 400,
                        "credits_amount": None,
                        "server_extension": {"preserved": True},
                    },
                }
            ),
            FakeResponse({"required_consents": [{"type": "synthetic-consent"}]}),
        )
        assortment, venue, delivery, payment_method, item = selection_inputs()
        selection = client.create_selection(
            assortment,
            venue=venue,
            delivery=delivery,
            payment_method=payment_method,
            courier_tip=0,
            items=[item],
        )

        self.assertEqual(len(opener.requests), 2)
        basket = client.save_basket(selection)
        quote = client.quote_checkout(selection)
        consents = client.get_post_checkout_config(selection)

        self.assertEqual(basket.id, "basket-1")
        self.assertEqual(basket.venue_id, "venue-1")
        self.assertEqual(quote.checkout_id, "checkout-1")
        self.assertEqual(quote.payable_amount, 3100)
        self.assertEqual(quote.purchase_validation["end_amount"], 2700)
        self.assertEqual(quote.purchase_validation["credits_amount"], None)
        self.assertEqual(
            quote.purchase_validation["server_extension"], {"preserved": True}
        )
        validation = quote.purchase_validation
        validation["end_amount"] = 0
        self.assertEqual(quote.purchase_validation["end_amount"], 2700)
        plan = quote._plan_snapshot()
        plan["menu_items"][0]["end_amount"] = 0
        self.assertEqual(quote._plan_snapshot()["menu_items"][0]["end_amount"], 2700)
        self.assertEqual(consents.required_consents, [{"type": "synthetic-consent"}])
        returned_consents = consents.required_consents
        returned_consents.clear()
        self.assertEqual(consents.required_consents, [{"type": "synthetic-consent"}])
        self.assertTrue(quote.is_current_for(selection))

        changed_selection = client.create_selection(
            assortment,
            venue=venue,
            delivery=delivery,
            payment_method=payment_method,
            courier_tip=0,
            items=[replace(item, count=1)],
        )
        self.assertFalse(quote.is_current_for(changed_selection))

        routes = [urlsplit(request.full_url) for request in opener.requests]
        self.assertEqual(
            [(route.netloc, route.path) for route in routes[2:]],
            [
                ("consumer-api.wolt.com", "/order-xp/v1/baskets"),
                ("consumer-api.wolt.com", "/order-xp/web/v2/pages/checkout"),
                ("restaurant-api.wolt.com", "/v1/post-checkout-config"),
            ],
        )
        self.assertEqual(
            json.loads(opener.requests[2].data.decode("utf-8")),
            selection.to_basket_payload(),
        )
        self.assertEqual(
            json.loads(opener.requests[3].data.decode("utf-8")),
            selection.to_checkout_payload(),
        )
        self.assertEqual(
            json.loads(opener.requests[4].data.decode("utf-8")),
            selection.to_post_checkout_payload(),
        )

    def test_rejects_root_ids_restrictions_and_missing_post_checkout_provenance(
        self,
    ) -> None:
        client, _ = make_discovered_client()
        assortment, venue, delivery, payment_method, item = selection_inputs()
        root_option = replace(item.options[0], configuration_id="root-option-1")
        with self.assertRaises(SelectionError):
            client.create_selection(
                assortment,
                venue=venue,
                delivery=delivery,
                payment_method=payment_method,
                courier_tip=0,
                items=[replace(item, options=[root_option])],
            )

        restricted_assortment = deepcopy(assortment)
        restricted_assortment["items"][0]["restrictions"] = ["synthetic-restriction"]
        with self.assertRaises(UnsupportedSelectionError):
            client.create_selection(
                restricted_assortment,
                venue=venue,
                delivery=delivery,
                payment_method=payment_method,
                courier_tip=0,
                items=[item],
            )

        missing_config_index = deepcopy(item.post_checkout_fields)
        del missing_config_index["configIndex"]
        with self.assertRaises(SelectionError):
            client.create_selection(
                assortment,
                venue=venue,
                delivery=delivery,
                payment_method=payment_method,
                courier_tip=0,
                items=[replace(item, post_checkout_fields=missing_config_index)],
            )

    def test_card_eligibility_context_must_match_the_selection(self) -> None:
        client, _ = make_discovered_client()
        assortment, venue, delivery, payment_method, item = selection_inputs()
        mismatched_post_fields = deepcopy(item.post_checkout_fields)
        mismatched_post_fields["vat_percentage"] = 1

        with self.assertRaises(SelectionError):
            client.create_selection(
                assortment,
                venue=venue,
                delivery=delivery,
                payment_method=payment_method,
                courier_tip=0,
                items=[replace(item, post_checkout_fields=mismatched_post_fields)],
            )

    def test_validates_option_types_and_cardinality(self) -> None:
        client, _ = make_discovered_client()
        assortment, venue, delivery, payment_method, item = selection_inputs()

        def create(
            candidate_assortment: dict[str, Any], candidate_item: ItemSelection
        ) -> None:
            client.create_selection(
                candidate_assortment,
                venue=venue,
                delivery=delivery,
                payment_method=payment_method,
                courier_tip=0,
                items=[candidate_item],
            )

        with self.assertRaises(SelectionError):
            create(assortment, replace(item, options=[]))

        below_minimum = deepcopy(assortment)
        below_minimum["items"][0]["options"][0]["multi_choice_config"][
            "total_range"
        ] = {"min": 4, "max": 4}
        below_minimum["items"][0]["options"][0]["multi_choice_config"][
            "max_single_selections"
        ] = 4
        with self.assertRaises(SelectionError):
            create(below_minimum, item)

        above_maximum = deepcopy(assortment)
        above_maximum["items"][0]["options"][0]["multi_choice_config"][
            "total_range"
        ] = {"min": 1, "max": 2}
        with self.assertRaises(SelectionError):
            create(above_maximum, item)

        above_single_limit = deepcopy(assortment)
        above_single_limit["items"][0]["options"][0]["multi_choice_config"][
            "max_single_selections"
        ] = 2
        with self.assertRaises(SelectionError):
            create(above_single_limit, item)

        multi_choice = deepcopy(assortment)
        multi_choice["options"][0]["type"] = "multi_choice"
        create(multi_choice, item)

        unsupported_type = deepcopy(assortment)
        unsupported_type["options"][0]["type"] = "unobserved"
        with self.assertRaises(UnsupportedSelectionError):
            create(unsupported_type, item)

        unsupported_free_selection = deepcopy(assortment)
        unsupported_free_selection["items"][0]["options"][0]["multi_choice_config"][
            "free_selections"
        ] = 1
        with self.assertRaises(UnsupportedSelectionError):
            create(unsupported_free_selection, item)

    def test_post_checkout_requires_a_non_null_consent_list(self) -> None:
        client, _ = make_discovered_client(FakeResponse({"required_consents": None}))
        assortment, venue, delivery, payment_method, item = selection_inputs()
        selection = client.create_selection(
            assortment,
            venue=venue,
            delivery=delivery,
            payment_method=payment_method,
            courier_tip=0,
            items=[item],
        )

        with self.assertRaises(ResponseShapeError):
            client.get_post_checkout_config(selection)


if __name__ == "__main__":
    unittest.main()
