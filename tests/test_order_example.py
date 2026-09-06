import argparse
import builtins
import json
import runpy
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_client import FakeOpener, FakeResponse
from tests.test_selection import make_discovered_client, selection_inputs
from woltapi import (
    SelectionError,
    SessionCredentials,
    UnsupportedSelectionError,
    WoltClient,
    WoltTransport,
)


def load_example(monkeypatch):
    examples = Path(__file__).resolve().parents[1] / "examples"
    monkeypatch.syspath_prepend(str(examples))
    return runpy.run_path(str(examples / "order.py"))


def setup_checkout(
    monkeypatch,
    *,
    save_basket=False,
    final_answer="QUOTE",
    configure_catalog=None,
    input_answers=None,
):
    assortment, venue, delivery, _, item = selection_inputs()
    catalog = assortment["items"][0]
    catalog["name"] = "Synthetic pizza"
    payment_fields = {
        k: item.post_checkout_fields[k]
        for k in (
            "product_hierarchy_tags",
            "vat_percentage",
            "vat_percentage_decimal",
        )
    }
    catalog.update(payment_fields)
    assortment["options"][0]["name"] = [{"lang": "en", "value": "Toppings"}]
    assortment["options"][0]["values"][0]["name"] = [
        {"lang": "en", "value": "Extra cheese"}
    ]
    if configure_catalog is not None:
        configure_catalog(assortment, catalog)
    responses = [
        {
            "sections": [
                {
                    "items": [
                        {
                            "title": "Test Restaurant",
                            "venue": {
                                "id": venue.id,
                                "slug": "test-restaurant",
                                "currency": "EUR",
                            },
                        }
                    ]
                }
            ]
        },
        {
            "venue": {
                "id": venue.id,
                "country": venue.country,
                "currency": venue.currency,
                "self_delivery": False,
            }
        },
        assortment,
        {
            "results": [
                {
                    "id": delivery.delivery_info_id,
                    "alias": "Example home",
                    "address": "PRIVATE ADDRESS",
                    "phone_number": "PRIVATE PHONE",
                    "location": {
                        "address": "1 Example Street",
                        "city": "Example City",
                        "postcode": "12345",
                    },
                }
            ]
        },
        {
            "root_element": {
                "element_type": "list",
                "children": [
                    {
                        "element_type": "payment-method",
                        "is_enabled": True,
                        "title": "Example card",
                        "subtitle": "Example bank",
                        "method": {
                            "id": "card-1",
                            "type": "card",
                            "masked_number": "PRIVATE CARD",
                        },
                    }
                ],
            }
        },
    ]
    answers = (
        input_answers
        if input_answers is not None
        else [
            "1",
            "1",
            "1",
            "1",
            "YES",
            "0",
            "1",
            "YES",
            "1",
        ]
    )
    if save_basket:
        responses.append({"id": "basket-1", "venue_id": venue.id})
        answers.append("SAVE BASKET")
    responses.append(
        {
            "id": "quote-1",
            "payable_amount": 3100,
            "purchase_validation": {"end_amount": 2700, "delivery_price": 400},
            "payment_breakdown": {"unallocated": {"amount": 0}},
            "purchasing_disabled": None,
            "call_to_action": {"enabled": False},
            "is_age_verification_required": False,
            "private_extension": "PRIVATE QUOTE DATA",
        }
    )
    answers.append(final_answer)
    iterator = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt: next(iterator))
    credentials = SessionCredentials(
        restaurant_headers={"Authorization": "Bearer synthetic-secret"},
        consumer_headers={"Authorization": "Bearer synthetic-secret"},
        payment_headers={"Authorization": "Bearer synthetic-secret"},
    )
    opener = FakeOpener([FakeResponse(response) for response in responses])
    client = WoltClient(
        credentials, _transport=WoltTransport(credentials, _opener=opener)
    )
    args = argparse.Namespace(
        query="pizza", latitude=1, longitude=2, language="en", save_basket=save_basket
    )
    return client, args, opener


@pytest.mark.parametrize("save_basket", [False, True])
def test_checkout_requests_and_stops_without_purchase(monkeypatch, capsys, save_basket):
    example = load_example(monkeypatch)
    client, args, opener = setup_checkout(monkeypatch, save_basket=save_basket)
    example["checkout"](client, args, {})
    routes = [(r.get_method(), urlsplit(r.full_url).path) for r in opener.requests]
    assert routes == [
        ("POST", "/v1/pages/search"),
        ("GET", "/order-xp/web/v1/pages/venue/slug/test-restaurant/static"),
        (
            "GET",
            "/consumer-api/consumer-assortment/v1/venues/slug/test-restaurant/assortment",
        ),
        ("GET", "/v2/delivery/info"),
        ("POST", "/v1/payment-methods/checkout"),
        *([("POST", "/order-xp/v1/baskets")] if save_basket else []),
        ("POST", "/order-xp/web/v2/pages/checkout"),
    ]
    plan = json.loads(opener.requests[-1].data)["purchase_plan"]
    checkout_item = plan["menu_items"][0]
    assert checkout_item["category_id"] == "category-1"
    assert checkout_item["category_ids"] == ["category-1"]
    assert checkout_item["exclude_from_credits"] is False
    assert checkout_item["exclude_from_discounts"] is False
    assert checkout_item["exclude_from_discounts_min_basket"] is False
    assert checkout_item["end_amount"] == 2700
    assert checkout_item["options"] == [
        {"id": "item-config-1", "values": [{"id": "value-1", "count": 1, "price": 700}]}
    ]
    assert plan["payment_methods"] == [{"id": "card-1", "type": "card"}]
    assert plan["delivery"]["delivery_info_id"] == "delivery-1"
    output = capsys.readouterr().out
    for expected in (
        "31.00 EUR",
        "27.00 EUR",
        "Extra cheese",
        "Example home",
        "1 Example Street",
        "Example City",
        "Example card",
        "Example bank",
        "Server action enabled: False",
        "STOPPED",
    ):
        assert expected in output
    assert "CHECKOUT TEST ONLY" not in output
    for private in (
        "PRIVATE ADDRESS",
        "PRIVATE PHONE",
        "PRIVATE CARD",
        "PRIVATE QUOTE DATA",
        "synthetic-secret",
    ):
        assert private not in output


def test_cancel_before_quote(monkeypatch):
    example = load_example(monkeypatch)
    client, args, opener = setup_checkout(monkeypatch, final_answer="no")
    example["checkout"](client, args, {})
    assert len(opener.requests) == 5


def test_declining_computed_price_stops_before_delivery_card_and_quote(monkeypatch):
    example = load_example(monkeypatch)
    client, args, opener = setup_checkout(
        monkeypatch,
        input_answers=["1", "1", "1", "1", "no"],
    )
    example["checkout"](client, args, {})
    assert [(r.get_method(), urlsplit(r.full_url).path) for r in opener.requests] == [
        ("POST", "/v1/pages/search"),
        ("GET", "/order-xp/web/v1/pages/venue/slug/test-restaurant/static"),
        (
            "GET",
            "/consumer-api/consumer-assortment/v1/venues/slug/test-restaurant/assortment",
        ),
    ]


def test_basket_flag_still_needs_explicit_confirmation(monkeypatch):
    example = load_example(monkeypatch)
    client, args, opener = setup_checkout(monkeypatch, save_basket=True)
    original = builtins.input
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: "no" if "SAVE BASKET" in prompt else original(prompt),
    )
    example["checkout"](client, args, {})
    assert len(opener.requests) == 5


def test_invalid_option_count_stops_before_card_and_quote(monkeypatch):
    example = load_example(monkeypatch)
    client, args, opener = setup_checkout(monkeypatch)
    answers = iter(["1", "1", "1", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    with pytest.raises(example["CheckoutInputError"], match="catalog limits"):
        example["checkout"](client, args, {})
    assert len(opener.requests) == 3


@pytest.mark.parametrize("price", [None, "700"])
def test_selected_option_price_must_be_an_integer(monkeypatch, price):
    example = load_example(monkeypatch)
    item = {
        "options": [
            {
                "id": "item-config-1",
                "option_id": "option-1",
                "prerequisite_values": [],
                "multi_choice_config": {
                    "total_range": {"min": 1, "max": 1},
                    "max_single_selections": 1,
                    "free_selections": 0,
                },
            }
        ]
    }
    assortment = {
        "options": [
            {
                "id": "option-1",
                "type": "choice",
                "name": [{"lang": "en", "value": "Toppings"}],
                "values": [
                    {
                        "id": "value-1",
                        "name": [{"lang": "en", "value": "Extra cheese"}],
                        **({"price": price} if price is not None else {}),
                    }
                ],
            }
        ]
    }
    monkeypatch.setattr("builtins.input", lambda prompt: "1")
    with pytest.raises(example["CheckoutInputError"], match="option value price"):
        example["select_options"](item, assortment, "en")


def test_empty_option_count_selects_no_value(monkeypatch):
    example = load_example(monkeypatch)
    item = {
        "options": [
            {
                "id": "item-config-1",
                "option_id": "option-1",
                "prerequisite_values": [],
                "multi_choice_config": {
                    "total_range": {"min": 0, "max": 1},
                    "max_single_selections": 1,
                    "free_selections": 0,
                },
            }
        ]
    }
    assortment = {
        "options": [
            {
                "id": "option-1",
                "type": "choice",
                "name": [{"lang": "en", "value": "Extras"}],
                "values": [
                    {
                        "id": "value-1",
                        "name": [{"lang": "en", "value": "Extra cheese"}],
                        "price": 700,
                    }
                ],
            }
        ]
    }
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert example["select_options"](item, assortment, "en") == ([], [], 0)


@pytest.mark.parametrize("price", [None, "2000"])
def test_catalog_item_price_must_be_an_integer(monkeypatch, price):
    example = load_example(monkeypatch)

    def configure_catalog(_, catalog):
        if price is None:
            del catalog["price"]
        else:
            catalog["price"] = price

    client, args, opener = setup_checkout(
        monkeypatch,
        configure_catalog=configure_catalog,
        input_answers=["1", "1"],
    )
    with pytest.raises(example["CheckoutInputError"], match="catalog item price"):
        example["checkout"](client, args, {})
    assert len(opener.requests) == 3


def test_configured_unit_price_is_not_multiplied_by_item_quantity(monkeypatch):
    example = load_example(monkeypatch)

    def configure_catalog(assortment, _):
        assortment["options"][0]["values"].append(
            {
                "id": "value-2",
                "price": 300,
                "name": [{"lang": "en", "value": "Extra pepperoni"}],
            }
        )

    client, args, opener = setup_checkout(
        monkeypatch,
        save_basket=True,
        configure_catalog=configure_catalog,
        input_answers=[
            "1",
            "1",
            "2",
            "2",
            "1",
            "YES",
            "0",
            "1",
            "YES",
            "1",
            "SAVE BASKET",
            "QUOTE",
        ],
    )
    example["checkout"](client, args, {})
    basket = json.loads(opener.requests[-2].data)
    checkout = json.loads(opener.requests[-1].data)["purchase_plan"]
    assert basket["items"][0]["count"] == 2
    assert basket["items"][0]["price"] == 3700
    assert checkout["menu_items"][0]["count"] == 2
    assert checkout["menu_items"][0]["end_amount"] == 3700


def test_missing_metadata_does_not_invent_values(monkeypatch):
    example = load_example(monkeypatch)
    with pytest.raises(
        example["CheckoutInputError"], match="Missing payment_fields: vat_percentage"
    ):
        example["fields"]({}, {}, ("vat_percentage",), "payment_fields")
    with pytest.raises(example["CheckoutInputError"], match="conflicts"):
        example["fields"](
            {"vat_percentage": 0},
            {"vat_percentage": 5},
            ("vat_percentage",),
            "payment_fields",
        )


def test_context_must_match_selected_item(monkeypatch):
    example = load_example(monkeypatch)
    client, args, opener = setup_checkout(monkeypatch)
    with pytest.raises(example["CheckoutInputError"], match="must match"):
        example["checkout"](
            client, args, {"venue_id": "venue-1", "item_id": "another-item"}
        )
    assert len(opener.requests) == 3


def test_quote_only_tax_fields_do_not_require_purchase_context():
    client, _ = make_discovered_client(
        FakeResponse(
            {
                "id": "quote-only",
                "payable_amount": 3100,
                "purchase_validation": {"end_amount": 2700},
                "call_to_action": {"enabled": False},
            }
        )
    )
    assortment, venue, delivery, payment_method, item = selection_inputs()
    tax = {
        k: item.post_checkout_fields[k]
        for k in (
            "product_hierarchy_tags",
            "vat_percentage",
            "vat_percentage_decimal",
        )
    }
    selection = client.create_selection(
        assortment,
        venue=venue,
        delivery=delivery,
        payment_method=payment_method,
        courier_tip=0,
        items=[replace(item, payment_fields=tax, post_checkout_fields=None)],
    )
    quote = client.quote_checkout(selection)
    assert quote.is_current_for(selection)
    response = quote.response
    response["call_to_action"]["enabled"] = True
    assert quote.response["call_to_action"]["enabled"] is False
    with pytest.raises(UnsupportedSelectionError):
        selection.to_post_checkout_payload()


def test_payment_fields_cannot_conflict_with_purchase_tax_fields():
    client, _ = make_discovered_client()
    assortment, venue, delivery, payment_method, item = selection_inputs()
    with pytest.raises(SelectionError, match="disagree"):
        client.create_selection(
            assortment,
            venue=venue,
            delivery=delivery,
            payment_method=payment_method,
            courier_tip=0,
            items=[replace(item, payment_fields={"vat_percentage": 99})],
        )


def test_script_does_not_accept_submit_option(monkeypatch):
    example = load_example(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["order.py", "--latitude", "1", "--longitude", "2", "--submit"]
    )
    with pytest.raises(SystemExit) as exc:
        example["main"]()
    assert exc.value.code == 2
