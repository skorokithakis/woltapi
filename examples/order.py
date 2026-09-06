#!/usr/bin/env python3
"""Interactively test checkout quotes. Never submits a purchase or charges a card."""

import argparse
import json
import math
from pathlib import Path

from browse import TokenFileError, check_token_file, refresh_credentials, text

from woltapi import (
    DeliverySelection,
    HTTPStatusError,
    ItemSelection,
    OptionSelection,
    OptionValueSelection,
    VenueCheckoutContext,
    WoltApiError,
    WoltClient,
    derive_checkout_fields,
)


class CheckoutInputError(ValueError):
    """An actionable local input problem that contains no private response data."""


def money(amount, currency):
    if type(amount) is not int:
        return "not provided as integer cents"
    sign = "-" if amount < 0 else ""
    return f"{sign}{abs(amount) // 100}.{abs(amount) % 100:02d} {currency}"


def number(prompt, minimum=0, maximum=None, empty_value=None):
    try:
        answer = input(prompt)
        value = empty_value if answer == "" and empty_value is not None else int(answer)
    except ValueError:
        raise CheckoutInputError(
            "Enter a whole number, not a decimal amount."
        ) from None
    if value < minimum or (maximum is not None and value > maximum):
        raise CheckoutInputError("The number is outside the allowed range.")
    return value


def choose(label, values, describe):
    if not values:
        raise CheckoutInputError(f"No {label} available.")
    print(f"\nChoose {label}:")
    for index, value in enumerate(values, 1):
        print(f"{index}. {describe(value)}")
    return values[number("Number: ", 1, len(values)) - 1]


def delivery_description(target):
    return (
        " | ".join(
            value
            for value in (
                target.alias or target.label_type,
                target.address,
                target.city,
            )
            if value
        )
        or target.id
    )


def card_description(card):
    return " | ".join(value for value in (card.title, card.subtitle) if value) or (
        f"{card.id} ({card.type})"
    )


def fields(source, supplied, names, section):
    if not isinstance(source, dict) or not isinstance(supplied, dict):
        raise CheckoutInputError(f"Expected an object for {section}.")
    missing = [name for name in names if name not in source and name not in supplied]
    if missing:
        raise CheckoutInputError(
            f"Missing {section}: {', '.join(missing)}. "
            "Supply current browser values using --context-file; no defaults were invented."
        )
    if any(
        name in source and name in supplied and source[name] != supplied[name]
        for name in names
    ):
        raise CheckoutInputError(
            f"The {section} context conflicts with current server data."
        )
    return {name: source[name] if name in source else supplied[name] for name in names}


def select_options(item, assortment, language):
    configurations = item.get("options")
    roots = assortment.get("options")
    if not isinstance(configurations, list) or not isinstance(roots, list):
        raise CheckoutInputError("Missing catalog option lists.")
    selected = []
    descriptions = []
    option_amount = 0
    for config in configurations:
        if not isinstance(config, dict) or config.get("prerequisite_values") != []:
            raise CheckoutInputError(
                "Conditional or unknown item options are unsupported."
            )
        root = next(
            (
                r
                for r in roots
                if isinstance(r, dict) and r.get("id") == config.get("option_id")
            ),
            None,
        )
        if root is None or root.get("type") not in ("choice", "multi_choice"):
            raise CheckoutInputError("Unknown option type or missing root option.")
        rules = config.get("multi_choice_config")
        total = rules.get("total_range") if isinstance(rules, dict) else None
        if not isinstance(total, dict):
            raise CheckoutInputError("Missing option limits.")
        low, high = total.get("min"), total.get("max")
        single = rules.get("max_single_selections")
        if (
            any(type(v) is not int for v in (low, high, single))
            or low < 0
            or high < low
            or single < 1
            or type(rules.get("free_selections")) is not int
            or rules["free_selections"] != 0
        ):
            raise CheckoutInputError(
                "Unsupported option limits or free-selection pricing."
            )
        values = root.get("values")
        if not isinstance(values, list) or not all(isinstance(v, dict) for v in values):
            raise CheckoutInputError("Missing option values.")
        print(f"\n{text(root.get('name'), language)}: select {low} to {high} in total.")
        selections = []
        for value in values:
            name = text(value.get("name"), language)
            count = number(
                f"Count for {name} (0 to {single}): ", 0, single, empty_value=0
            )
            if count:
                price = value.get("price")
                if type(price) is not int:
                    raise CheckoutInputError(
                        "Missing or invalid catalog option value price."
                    )
                selections.append(OptionValueSelection(value["id"], count))
                descriptions.append(f"{count} x {name}")
                option_amount += price * count
        if not low <= sum(v.count for v in selections) <= high:
            raise CheckoutInputError("Selected options do not meet the catalog limits.")
        if selections:
            selected.append(OptionSelection(config["id"], selections))
    return selected, descriptions, option_amount


def checkout(client, args, context):
    found = client.search_venues(args.query, args.latitude, args.longitude)
    venue = choose("restaurant", found, lambda v: text(v.title or v.slug))
    static = client.get_venue_static(venue.slug).get("venue")
    if not isinstance(static, dict) or static.get("id") != venue.id:
        raise CheckoutInputError(
            "Static venue response does not match the selected restaurant."
        )
    venue_fields = fields(
        static,
        context.get("venue", {}),
        ("country", "currency", "self_delivery"),
        "venue",
    )
    if venue.currency is not None and venue_fields["currency"] != venue.currency:
        raise CheckoutInputError(
            "Venue currency changed; start again with current data."
        )
    currency = venue_fields["currency"]
    assortment = client.get_assortment(venue.slug)
    catalog_items = assortment.get("items")
    if not isinstance(catalog_items, list) or not all(
        isinstance(i, dict) for i in catalog_items
    ):
        raise CheckoutInputError("Missing assortment items.")
    item = choose(
        "menu item",
        catalog_items,
        lambda i: f"{text(i.get('name'), args.language)} | {money(i.get('price'), currency)}",
    )
    if context and (
        context.get("venue_id") != venue.id or context.get("item_id") != item.get("id")
    ):
        raise CheckoutInputError(
            "Context venue_id and item_id must match the selected restaurant and item."
        )
    if item.get("restrictions") != []:
        raise CheckoutInputError("Restricted items are unsupported.")
    methods = item.get("allowed_delivery_methods")
    if not isinstance(methods, list) or "homedelivery" not in methods:
        raise CheckoutInputError("This item does not explicitly support home delivery.")
    item_price = item.get("price")
    if type(item_price) is not int:
        raise CheckoutInputError("Missing or invalid catalog item price.")
    count = number("Item quantity: ", 1)
    options, option_names, option_amount = select_options(item, assortment, args.language)
    checkout_fields = fields(
        derive_checkout_fields(assortment, item),
        context.get("checkout_fields", {}),
        (
            "category_id",
            "category_ids",
            "exclude_from_credits",
            "exclude_from_discounts",
            "exclude_from_discounts_min_basket",
            "alcohol_permille",
            "restrictions",
        ),
        "checkout_fields",
    )
    if checkout_fields["alcohol_permille"] != 0:
        raise CheckoutInputError("Age-restricted items are unsupported.")
    payment_fields = fields(
        item,
        context.get("payment_fields", {}),
        (
            "product_hierarchy_tags",
            "vat_percentage",
            "vat_percentage_decimal",
        ),
        "payment_fields",
    )
    unit_amount = item_price + option_amount
    print(f"Computed configured unit price: {money(unit_amount, currency)}")
    if input("Does this match the Wolt UI? Type YES: ").strip() != "YES":
        print("Cancelled before card lookup or quote.")
        return
    tip = number("Courier tip in cents (0 for none): ")
    delivery = choose(
        "saved delivery target", client.list_delivery_targets(), delivery_description
    )
    print(f"Selected delivery target: {delivery_description(delivery)}")
    if (
        input("Does this target match your supplied coordinates? Type YES: ").strip()
        != "YES"
    ):
        print("Cancelled before card lookup or quote.")
        return
    card_context = {
        "venue_id": venue.id,
        "country": venue_fields["country"],
        "delivery_method": "homedelivery",
        "available_methods": ["card"],
        "items": [
            {
                "id": item["id"],
                "alcohol_permille": checkout_fields["alcohol_permille"],
                **payment_fields,
            }
        ],
    }
    card = choose(
        "enabled saved card",
        client.get_payment_methods(card_context),
        card_description,
    )
    selected_item = ItemSelection(
        id=item["id"],
        count=count,
        basket_name=text(item.get("name"), args.language),
        basket_price=unit_amount,
        end_amount=unit_amount,
        substitution_allowed=False,
        checkout_fields=checkout_fields,
        options=options,
        payment_fields=payment_fields,
    )
    selection = client.create_selection(
        assortment,
        venue=VenueCheckoutContext(id=venue.id, preorder_config=None, **venue_fields),
        delivery=DeliverySelection(delivery.id, args.latitude, args.longitude),
        payment_method={"id": card.id, "type": card.type},
        courier_tip=tip,
        items=[selected_item],
    )
    print(
        f"\n{count} x {selected_item.basket_name} at {text(venue.title or venue.slug)}"
    )
    for name in option_names:
        print(f"  Option: {name}")
    print(f"Configured unit price: {money(unit_amount, currency)}")
    print(
        f"Delivery: {delivery_description(delivery)} | Card: {card_description(card)}"
    )
    print(
        f"Tip: {money(tip, currency)} | Immediate home delivery; no credits or offers."
    )
    if args.save_basket:
        if (
            input("This changes your saved Wolt basket. Type SAVE BASKET: ").strip()
            != "SAVE BASKET"
        ):
            print("Cancelled. Basket not saved; no quote requested.")
            return
        client.save_basket(selection)
        print("Basket saved. This is not an order.")
    if (
        input("Request a live price quote without buying? Type QUOTE: ").strip()
        != "QUOTE"
    ):
        print("Cancelled. No quote requested.")
        return
    quote = client.quote_checkout(selection)
    print(f"\nServer payable amount: {money(quote.payable_amount, currency)}")
    print(
        f"Server validation end_amount (separate field): {money(quote.purchase_validation.get('end_amount'), currency)}"
    )
    response = quote.response
    breakdown = response.get("payment_breakdown")
    unallocated = breakdown.get("unallocated") if isinstance(breakdown, dict) else None
    print(
        f"Unallocated payment: {money(unallocated.get('amount') if isinstance(unallocated, dict) else None, currency)}"
    )
    restriction = "not provided"
    if "purchasing_disabled" in response:
        restriction = (
            "not set" if response["purchasing_disabled"] is None else "present"
        )
    print(f"Server purchasing restriction: {restriction}")
    for key in (
        "is_age_verification_required",
        "use_address_matching_for_age_verification",
    ):
        value = response.get(key)
        print(f"{key}: {value if type(value) is bool else 'not provided'}")
    action = response.get("call_to_action")
    enabled = action.get("enabled") if isinstance(action, dict) else None
    print(
        f"Server action enabled: {enabled if type(enabled) is bool else 'not provided'}"
    )
    print(
        "STOPPED: no purchase, no charge, and no order ID was created by this script."
    )
    print(
        "A quote is not approval to buy. Consent and purchase-context checks remain untested."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--query", default="pizza")
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--context-file",
        type=Path,
        help="Optional current browser metadata for fields missing from the catalog",
    )
    parser.add_argument(
        "--save-basket",
        action="store_true",
        help="Offer to save the remote basket, with separate confirmation",
    )
    args = parser.parse_args()
    if not (
        math.isfinite(args.latitude)
        and -90 <= args.latitude <= 90
        and math.isfinite(args.longitude)
        and -180 <= args.longitude <= 180
    ):
        parser.error("Provide valid latitude and longitude.")
    check_token_file(parser, args.token_file)
    try:
        context = json.loads(args.context_file.read_text()) if args.context_file else {}
        if not isinstance(context, dict):
            raise CheckoutInputError("The context file must contain a JSON object.")
        client = WoltClient(refresh_credentials(args.language, args.token_file))
        checkout(client, args, context)
    except CheckoutInputError as exc:
        print(f"Checkout stopped: {exc}")
        return 1
    except TokenFileError:
        print(
            "Checkout stopped: could not save the refresh token to the token file."
            " Wolt may have replaced it, so the stored value can be dead."
            " Put a current __wrtoken value in the file."
        )
        return 1
    except HTTPStatusError as exc:
        print(
            f"Checkout stopped: {exc.service} HTTP {exc.status_code}. No retry was sent."
        )
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled. No purchase was submitted.")
        return 1
    except (
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
        WoltApiError,
    ) as exc:
        print(
            f"Checkout stopped: {type(exc).__name__}. Private details omitted; no retry was sent."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
