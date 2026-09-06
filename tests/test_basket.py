from __future__ import annotations

from typing import Any

import pytest

from woltapi import (
    Basket,
    DeliverySelection,
    OptionSelection,
    OptionValueSelection,
    SelectionError,
    VenueCheckoutContext,
)
from woltapi.selection import OrderSelection


def assortment() -> dict[str, Any]:
    return {
        "items": [
            {
                "id": "item-1",
                "name": [{"lang": "en", "value": "Pizza"}],
                "price": 645,
                "checksum": "checksum-1",
                "allowed_delivery_methods": ["homedelivery"],
                "restrictions": [],
                "alcohol_permille": 0,
                "product_hierarchy_tags": ["food", "pizza"],
                "vat_percentage": 14,
                "vat_percentage_decimal": "14.0",
                "options": [
                    {
                        "id": "pizza-toppings",
                        "option_id": "toppings",
                        "prerequisite_values": [],
                        "multi_choice_config": {
                            "total_range": {"min": 0, "max": 3},
                            "max_single_selections": 3,
                            "free_selections": 0,
                        },
                    }
                ],
            },
            {
                "id": "item-2",
                "name": [{"language": "en", "value": "Salad"}],
                "price": 400,
                "checksum": "checksum-2",
                "allowed_delivery_methods": ["homedelivery"],
                "restrictions": [],
                "exclude_from_credits": True,
                "alcohol_permille": 0,
                "product_hierarchy_tags": ["food", "salad"],
                "vat_percentage": 14,
                "vat_percentage_decimal": "14.0",
                "options": [
                    {
                        "id": "salad-toppings",
                        "option_id": "toppings",
                        "prerequisite_values": [],
                        "multi_choice_config": {
                            "total_range": {"min": 0, "max": 3},
                            "max_single_selections": 3,
                            "free_selections": 0,
                        },
                    }
                ],
            },
            {
                "id": "item-removed",
                "name": [{"lang": "en", "value": "Removed item"}],
                "price": 100,
                "checksum": "checksum-removed",
                "allowed_delivery_methods": ["homedelivery"],
                "restrictions": [],
                "alcohol_permille": 0,
                "product_hierarchy_tags": [],
                "vat_percentage": 14,
                "vat_percentage_decimal": "14.0",
                "options": [],
            },
        ],
        "categories": [
            {"id": "pizza", "item_ids": ["item-1"]},
            {"id": "salad", "item_ids": ["item-2"]},
            {"id": "other", "item_ids": ["item-removed"]},
        ],
        "options": [
            {
                "id": "toppings",
                "type": "multi_choice",
                "values": [
                    {"id": "cheese", "price": 170},
                    {"id": "olives", "price": 50},
                ],
            }
        ],
    }


def topping(configuration_id: str, value_id: str, count: int = 1) -> OptionSelection:
    return OptionSelection(
        configuration_id=configuration_id,
        values=[OptionValueSelection(value_id, count)],
    )


def test_basket_derives_line_totals_and_feeds_order_selection() -> None:
    basket = Basket(assortment(), "en", substitution_allowed=True)
    basket.add_item("item-1", options=[topping("pizza-toppings", "olives")])
    basket.set_count("item-1", 2)
    basket.set_options("item-1", [topping("pizza-toppings", "cheese")])
    basket.add_item(
        "item-2",
        options=[topping("salad-toppings", "olives", count=2)],
        substitution_allowed=False,
    )
    basket.add_item("item-removed")
    basket.remove_item("item-removed")

    items = basket.item_selections()

    assert [
        (item.id, item.basket_name, item.basket_price, item.end_amount)
        for item in items
    ] == [
        ("item-1", "Pizza", 1630, 1630),
        ("item-2", "Salad", 500, 500),
    ]
    assert items[0].substitution_allowed is True
    assert items[1].substitution_allowed is False
    assert items[0].checkout_fields["category_id"] == "pizza"
    assert items[1].checkout_fields["exclude_from_credits"] is True
    assert items[1].payment_fields == {
        "product_hierarchy_tags": ["food", "salad"],
        "vat_percentage": 14,
        "vat_percentage_decimal": "14.0",
    }

    selection = OrderSelection._from_assortment(
        assortment(),
        venue=VenueCheckoutContext("venue-1", "FI", "EUR", False, None),
        delivery=DeliverySelection("delivery-1", 1, 2),
        payment_method={"id": "card-1", "type": "card"},
        courier_tip=0,
        items=items,
    )

    assert selection.to_basket_payload()["items"] == [
        {
            "id": "item-1",
            "count": 2,
            "name": "Pizza",
            "price": 1630,
            "options": [
                {
                    "id": "pizza-toppings",
                    "values": [{"id": "cheese", "count": 1, "price": 170}],
                }
            ],
            "substitution_settings": {"is_allowed": True},
        },
        {
            "id": "item-2",
            "count": 1,
            "name": "Salad",
            "price": 500,
            "options": [
                {
                    "id": "salad-toppings",
                    "values": [{"id": "olives", "count": 2, "price": 50}],
                }
            ],
            "substitution_settings": {"is_allowed": False},
        },
    ]
    checkout_items = selection.to_checkout_payload()["purchase_plan"]["menu_items"]
    assert [item["end_amount"] for item in checkout_items] == [1630, 500]
    assert [item["base_price"] for item in checkout_items] == [645, 400]


def test_basket_mutation_rejects_duplicates_and_contents_are_detached() -> None:
    basket = Basket(assortment(), "en")
    basket.add_item("item-1", options=[topping("pizza-toppings", "cheese")])

    with pytest.raises(SelectionError, match="duplicate"):
        basket.add_item("item-1")
    with pytest.raises(SelectionError, match="not in the basket"):
        basket.set_count("missing", 1)
    with pytest.raises(TypeError):
        basket.contents["item-2"] = basket.contents["item-1"]  # type: ignore[index]

    contents = basket.contents
    contents["item-1"].checkout_fields["category_id"] = "mutated"
    contents["item-1"].options[0].values.append(OptionValueSelection("olives", 1))

    current = basket.item_selections()[0]
    assert current.checkout_fields["category_id"] == "pizza"
    assert current.options[0].values == [OptionValueSelection("cheese", 1)]


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data["categories"][0]["item_ids"].clear(),
        lambda data: data["items"][0].update(price=1.5),
        lambda data: data["options"][0]["values"][0].update(price=True),
        lambda data: data["items"][0].update(name=[{"lang": "fi", "value": "Pizza"}]),
    ],
)
def test_basket_rejects_missing_catalog_fields_invalid_prices_and_names(change) -> None:
    data = assortment()
    change(data)

    basket = Basket(data, "en")
    with pytest.raises(SelectionError):
        basket.add_item("item-1", options=[topping("pizza-toppings", "cheese")])


def test_basket_copies_assortment_and_option_input() -> None:
    data = assortment()
    selected = [topping("pizza-toppings", "cheese")]
    basket = Basket(data, "en")
    basket.add_item("item-1", options=selected)
    data["items"][0]["price"] = 1
    selected[0].values[0] = OptionValueSelection("olives", 1)

    assert basket.item_selections()[0].basket_price == 815
