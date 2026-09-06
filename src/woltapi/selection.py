"""Local selections, immutable quote snapshots, and explicit wire serializers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import ResponseShapeError, SelectionError, UnsupportedSelectionError


@dataclass(frozen=True)
class VenueCheckoutContext:
    """Explicit venue fields for the observed immediate-delivery quote shape."""

    id: str
    country: str
    currency: str
    self_delivery: bool
    preorder_config: Any


@dataclass(frozen=True)
class DeliverySelection:
    """A saved delivery reference with caller-supplied numeric coordinates."""

    delivery_info_id: str
    latitude: int | float = field(repr=False)
    longitude: int | float = field(repr=False)


@dataclass(frozen=True)
class OptionValueSelection:
    """A selected root-option value and its explicit count."""

    id: str
    count: int


@dataclass(frozen=True)
class OptionSelection:
    """A selected item option configuration and its selected values.

    ``post_checkout_fields`` is an exact, caller-supplied mapping for the
    distinct post-checkout wire format. In particular, its ``type`` is not
    inferred from the assortment root option type.
    """

    configuration_id: str
    values: Sequence[OptionValueSelection]
    post_checkout_fields: Mapping[str, Any] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ItemSelection:
    """Explicit local selection data for one catalog item.

    Prices that cannot be derived safely are caller inputs: ``basket_price``
    and ``end_amount`` remain independent. ``checkout_fields`` and
    ``post_checkout_fields`` carry exact field names for their respective wire
    representations and are copied without filling omitted keys. Quote-only
    callers can supply ``payment_fields`` (product tags and VAT fields) without
    supplying the complete post-checkout purchase representation.
    """

    id: str
    count: int
    basket_name: str
    basket_price: int
    end_amount: int
    substitution_allowed: bool
    checkout_fields: Mapping[str, Any]
    options: Sequence[OptionSelection] = ()
    post_checkout_fields: Mapping[str, Any] | None = field(default=None, repr=False)
    payment_fields: Mapping[str, Any] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class SavedBasket:
    """Reference returned after the explicit persisted-basket mutation."""

    id: str
    venue_id: str


class PostCheckoutConfig:
    """Required-consent data returned for a selected post-checkout basket."""

    __slots__ = ("_required_consents", "_selection_identity")

    def __init__(
        self, required_consents: Sequence[Any], selection_identity: object | None = None
    ) -> None:
        self._required_consents = deepcopy(list(required_consents))
        self._selection_identity = selection_identity

    @property
    def required_consents(self) -> list[Any]:
        """Return a detached copy of the server-provided consent list."""

        return deepcopy(self._required_consents)

    def __repr__(self) -> str:
        return f"PostCheckoutConfig(required_consents={len(self._required_consents)})"

    def is_current_for(self, selection: OrderSelection) -> bool:
        """Whether these consents were fetched for the same local selection."""

        return (
            isinstance(selection, OrderSelection)
            and self._selection_identity is selection._identity_token()
        )

    @classmethod
    def _from_response(
        cls,
        response: Mapping[str, Any],
        service: str,
        selection: OrderSelection,
    ) -> PostCheckoutConfig:
        required_consents = response.get("required_consents")
        if not _is_array(required_consents):
            raise ResponseShapeError(service)
        return cls(required_consents, selection._identity_token())


class QuoteSnapshot:
    """Private, independent snapshots of selected catalog data, plan, and quote."""

    __slots__ = ("_selection_data", "_selection_identity", "_plan", "_quote")

    def __init__(
        self,
        selection_data: Mapping[str, Any],
        selection_identity: object,
        plan: Mapping[str, Any],
        quote: Mapping[str, Any],
    ) -> None:
        self._selection_data = deepcopy(dict(selection_data))
        self._selection_identity = selection_identity
        self._plan = deepcopy(dict(plan))
        self._quote = deepcopy(dict(quote))

    @property
    def checkout_id(self) -> str:
        return self._quote["id"]

    @property
    def payable_amount(self) -> int | float:
        return deepcopy(self._quote["payable_amount"])

    @property
    def purchase_validation(self) -> dict[str, Any]:
        """Return an unchanged, detached copy of the server validation object."""

        return deepcopy(self._quote["purchase_validation"])

    @property
    def response(self) -> dict[str, Any]:
        """Return a detached quote response for checking server action state.

        This can contain private checkout data. Do not log the whole response.
        """
        return deepcopy(self._quote)

    def is_current_for(self, selection: OrderSelection) -> bool:
        """Whether this snapshot belongs to the unchanged local selection."""

        return (
            isinstance(selection, OrderSelection)
            and self._selection_identity is selection._identity_token()
        )

    def __repr__(self) -> str:
        return f"QuoteSnapshot(checkout_id={self.checkout_id!r})"

    @classmethod
    def _capture(
        cls,
        selection: OrderSelection,
        plan: Mapping[str, Any],
        quote: Mapping[str, Any],
        service: str,
    ) -> QuoteSnapshot:
        checkout_id = quote.get("id")
        payable_amount = quote.get("payable_amount")
        validation = quote.get("purchase_validation")
        if (
            not _is_nonempty_text(checkout_id)
            or not _is_number(payable_amount)
            or not isinstance(validation, Mapping)
        ):
            raise ResponseShapeError(service)
        return cls(
            selection._snapshot_data(),
            selection._identity_token(),
            plan,
            quote,
        )

    def _plan_snapshot(self) -> dict[str, Any]:
        """Private handoff for a later, explicitly approved purchase phase."""

        return deepcopy(self._plan)

    def _quote_snapshot(self) -> dict[str, Any]:
        """Private handoff for a later, explicitly approved purchase phase."""

        return deepcopy(self._quote)

    def _selection_snapshot(self) -> dict[str, Any]:
        """Private handoff for a later, explicitly approved purchase phase."""

        return deepcopy(self._selection_data)


class OrderSelection:
    """An immutable local selection with separate basket, quote, and consent serializers."""

    __slots__ = ("_data", "_identity")

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = deepcopy(dict(data))
        self._identity = object()

    def __repr__(self) -> str:
        return f"OrderSelection(item_count={len(self._data['items'])})"

    @classmethod
    def _from_assortment(
        cls,
        assortment: Mapping[str, Any],
        *,
        venue: VenueCheckoutContext,
        delivery: DeliverySelection,
        payment_method: Mapping[str, Any],
        courier_tip: int,
        items: Sequence[ItemSelection],
    ) -> OrderSelection:
        if not isinstance(assortment, Mapping):
            raise SelectionError("A selection requires an assortment mapping.")
        if not isinstance(venue, VenueCheckoutContext):
            raise TypeError("venue must be a VenueCheckoutContext instance.")
        if not isinstance(delivery, DeliverySelection):
            raise TypeError("delivery must be a DeliverySelection instance.")
        if not _is_array(items) or not items:
            raise SelectionError("A selection requires at least one item.")

        catalog_items = _index_by_id(assortment.get("items"))
        root_options = _index_by_id(assortment.get("options"))
        normalized_items = [
            _normalize_item(item, catalog_items, root_options) for item in items
        ]
        item_ids = [item["id"] for item in normalized_items]
        if len(item_ids) != len(set(item_ids)):
            raise SelectionError("A selection cannot contain duplicate item IDs.")

        return cls(
            {
                "venue": _normalize_venue(venue),
                "delivery": _normalize_delivery(delivery),
                "payment_method": _normalize_payment_method(payment_method),
                "courier_tip": _nonnegative_integer(courier_tip),
                "items": normalized_items,
            }
        )

    def to_basket_payload(self) -> dict[str, Any]:
        """Serialize the selected items for ``POST /order-xp/v1/baskets``."""

        return {
            "venue_id": self._data["venue"]["id"],
            "currency": self._data["venue"]["currency"],
            "items": [_serialize_basket_item(item) for item in self._data["items"]],
        }

    def to_checkout_payload(self) -> dict[str, Any]:
        """Serialize the selected items for the checkout-quote endpoint."""

        return {
            "purchase_plan": {
                "courier_tip": self._data["courier_tip"],
                "delivery": {
                    "delivery_coordinates": {
                        "longitude": self._data["delivery"]["longitude"],
                        "latitude": self._data["delivery"]["latitude"],
                    },
                    "delivery_info_id": self._data["delivery"]["delivery_info_id"],
                },
                "delivery_method": "homedelivery",
                "delivery_config": {
                    "method": "homedelivery",
                    "schedule": "standard",
                    "time_slot": None,
                },
                "payment_methods": [deepcopy(self._data["payment_method"])],
                "menu_items": [
                    _serialize_checkout_item(item) for item in self._data["items"]
                ],
                "selected_offer_ids": [],
                "use_cash": False,
                "use_credits_and_tokens": False,
                "use_loyalty_points_amount": 0,
                "use_promo_surcharge_ids": [],
                "venue": deepcopy(self._data["venue"]),
            }
        }

    def to_post_checkout_payload(self) -> dict[str, Any]:
        """Serialize the distinct post-checkout basket representation."""

        return {
            "venue_id": self._data["venue"]["id"],
            "country": self._data["venue"]["country"],
            "basket": [
                _serialize_post_checkout_item(item) for item in self._data["items"]
            ],
        }

    def _payment_eligibility_context(self) -> dict[str, Any]:
        """Return the minimal selected-item context used for card eligibility."""

        items: list[dict[str, Any]] = []
        for item in self._data["items"]:
            # Quote-only callers need tax data, not purchase-builder fields.
            payment_fields = item.get("payment_fields")
            if payment_fields is None:
                payment_fields = _serialize_post_checkout_item(item)
            items.append(
                {
                    "id": item["id"],
                    "alcohol_permille": item["checkout_fields"]["alcohol_permille"],
                    "product_hierarchy_tags": deepcopy(
                        payment_fields.get("product_hierarchy_tags")
                    ),
                    "vat_percentage": payment_fields.get("vat_percentage"),
                    "vat_percentage_decimal": payment_fields.get(
                        "vat_percentage_decimal"
                    ),
                }
            )
        return {
            "venue_id": self._data["venue"]["id"],
            "delivery_method": "homedelivery",
            "items": items,
        }

    def _snapshot_data(self) -> dict[str, Any]:
        return deepcopy(self._data)

    def _identity_token(self) -> object:
        return self._identity


_CHECKOUT_REQUIRED_FIELDS = {
    "category_id",
    "category_ids",
    "exclude_from_credits",
    "exclude_from_discounts",
    "exclude_from_discounts_min_basket",
    "alcohol_permille",
    "restrictions",
}
_CHECKOUT_RESERVED_FIELDS = {"id", "count", "options", "base_price", "end_amount"}
_POST_CHECKOUT_REQUIRED_FIELDS = {
    "configIndex",
    "name",
    "category",
    "exclude_from_credits",
    "exclude_from_discounts",
    "exclude_from_discounts_min_basket",
    "from_recommendation",
    "alcohol_percentage",
    "product_hierarchy_tags",
    "vat_percentage",
    "vat_percentage_decimal",
}
_POST_CHECKOUT_RESERVED_FIELDS = {
    "id",
    "count",
    "baseprice",
    "end_amount",
    "restrictions",
    "options",
    "checksum",
}
_POST_CHECKOUT_OPTION_RESERVED_FIELDS = {"id", "values"}


def _normalize_venue(venue: VenueCheckoutContext) -> dict[str, Any]:
    if (
        not _is_nonempty_text(venue.id)
        or not _is_nonempty_text(venue.country)
        or not _is_nonempty_text(venue.currency)
        or not isinstance(venue.self_delivery, bool)
    ):
        raise SelectionError("The checkout venue context is incomplete.")
    if venue.preorder_config is not None:
        raise UnsupportedSelectionError(
            "Scheduled or preorder selections are unsupported."
        )
    return {
        "id": venue.id,
        "country": venue.country,
        "currency": venue.currency,
        "self_delivery": venue.self_delivery,
        "preorder_config": None,
    }


def _normalize_delivery(delivery: DeliverySelection) -> dict[str, Any]:
    if not _is_nonempty_text(delivery.delivery_info_id):
        raise SelectionError("A saved delivery reference is required.")
    return {
        "delivery_info_id": delivery.delivery_info_id,
        "latitude": _finite_coordinate(delivery.latitude),
        "longitude": _finite_coordinate(delivery.longitude),
    }


def _normalize_payment_method(payment_method: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payment_method, Mapping):
        raise TypeError("payment_method must be a mapping.")
    method = deepcopy(dict(payment_method))
    if not _is_nonempty_text(method.get("id")) or method.get("type") != "card":
        raise SelectionError("A saved card payment method is required.")
    return method


def _normalize_item(
    selected_item: ItemSelection,
    catalog_items: Mapping[str, Mapping[str, Any]],
    root_options: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(selected_item, ItemSelection):
        raise TypeError("items must contain ItemSelection instances.")
    if not _is_nonempty_text(selected_item.id):
        raise SelectionError("An item ID is required.")
    catalog_item = catalog_items.get(selected_item.id)
    if catalog_item is None:
        raise SelectionError("The selected item is absent from the assortment.")

    count = _positive_integer(selected_item.count)
    _validate_catalog_item_for_home_delivery(catalog_item)
    base_price = _integer(catalog_item.get("price"))
    checksum = catalog_item.get("checksum")
    if not _is_nonempty_text(checksum):
        raise SelectionError("The selected catalog item is missing a checksum.")
    _validate_item_quantity(catalog_item, count)

    if not _is_nonempty_text(selected_item.basket_name):
        raise SelectionError("A basket item name is required.")
    if not isinstance(selected_item.substitution_allowed, bool):
        raise SelectionError("The substitution setting must be explicit.")

    options = _normalize_options(selected_item.options, catalog_item, root_options)
    checkout_fields = _normalize_checkout_fields(
        selected_item.checkout_fields,
        catalog_item["restrictions"],
    )
    post_checkout_fields = _copy_optional_mapping(selected_item.post_checkout_fields)
    payment_fields = _copy_optional_mapping(selected_item.payment_fields)
    if payment_fields is not None and post_checkout_fields is not None:
        for name in (
            "product_hierarchy_tags",
            "vat_percentage",
            "vat_percentage_decimal",
        ):
            if (
                name in post_checkout_fields
                and payment_fields.get(name) != post_checkout_fields[name]
            ):
                raise SelectionError("Payment and post-checkout tax fields disagree.")

    return {
        "id": selected_item.id,
        "count": count,
        "basket_name": selected_item.basket_name,
        "basket_price": _integer(selected_item.basket_price),
        "end_amount": _integer(selected_item.end_amount),
        "substitution_allowed": selected_item.substitution_allowed,
        "base_price": base_price,
        "checksum": checksum,
        "checkout_fields": checkout_fields,
        "post_checkout_fields": post_checkout_fields,
        "payment_fields": payment_fields,
        "options": options,
        "catalog": {
            "item": deepcopy(dict(catalog_item)),
            "options": [deepcopy(option["catalog"]) for option in options],
        },
    }


def _validate_catalog_item_for_home_delivery(catalog_item: Mapping[str, Any]) -> None:
    delivery_methods = catalog_item.get("allowed_delivery_methods")
    restrictions = catalog_item.get("restrictions")
    if not _is_array(delivery_methods) or "homedelivery" not in delivery_methods:
        raise UnsupportedSelectionError(
            "The selected item does not support home delivery."
        )
    if not _is_array(restrictions) or restrictions:
        raise UnsupportedSelectionError("Items with restrictions are unsupported.")


def _validate_item_quantity(catalog_item: Mapping[str, Any], count: int) -> None:
    minimum = catalog_item.get("min_quantity_per_purchase")
    maximum = catalog_item.get("max_quantity_per_purchase")
    if minimum is not None and (not _is_integer(minimum) or count < minimum):
        raise SelectionError("The item count is outside the catalog minimum.")
    if maximum is not None and (not _is_integer(maximum) or count > maximum):
        raise SelectionError("The item count is outside the catalog maximum.")


def _normalize_checkout_fields(
    fields: Mapping[str, Any], catalog_restrictions: Sequence[Any]
) -> dict[str, Any]:
    fields_copy = _copy_mapping(fields, "checkout_fields")
    _validate_wire_fields(
        fields_copy,
        required=_CHECKOUT_REQUIRED_FIELDS,
        reserved=_CHECKOUT_RESERVED_FIELDS,
        label="checkout_fields",
    )
    if (
        not _is_nonempty_text(fields_copy["category_id"])
        or not _is_array(fields_copy["category_ids"])
        or not all(
            _is_nonempty_text(category) for category in fields_copy["category_ids"]
        )
        or any(
            not isinstance(fields_copy[field], bool)
            for field in (
                "exclude_from_credits",
                "exclude_from_discounts",
                "exclude_from_discounts_min_basket",
            )
        )
    ):
        raise SelectionError("The checkout item fields are incomplete.")
    if not _is_zero_number(fields_copy["alcohol_permille"]):
        raise UnsupportedSelectionError("Age-restricted items are unsupported.")
    if (
        not _is_array(fields_copy["restrictions"])
        or fields_copy["restrictions"]
        or fields_copy["restrictions"] != catalog_restrictions
    ):
        raise UnsupportedSelectionError("Items with restrictions are unsupported.")
    return fields_copy


def _normalize_options(
    selected_options: Sequence[OptionSelection],
    catalog_item: Mapping[str, Any],
    root_options: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not _is_array(selected_options):
        raise SelectionError("Item options must be a sequence.")
    configurations = _index_by_id(catalog_item.get("options"))
    normalized: list[dict[str, Any]] = []
    configuration_ids: set[str] = set()
    for selected_option in selected_options:
        if not isinstance(selected_option, OptionSelection):
            raise TypeError("options must contain OptionSelection instances.")
        config_id = selected_option.configuration_id
        if not _is_nonempty_text(config_id) or config_id in configuration_ids:
            raise SelectionError("Option configuration IDs must be unique.")
        configuration_ids.add(config_id)
        configuration = configurations.get(config_id)
        if configuration is None:
            raise SelectionError("The selected option is not an item configuration.")
        root_option = _root_option_for_configuration(configuration, root_options)
        constraints = _option_constraints(configuration, root_option)
        root_values = _index_by_id(root_option.get("values"))
        if not _is_array(selected_option.values) or not selected_option.values:
            raise SelectionError("Each selected option requires at least one value.")

        values: list[dict[str, Any]] = []
        value_ids: set[str] = set()
        selected_value_catalog: list[dict[str, Any]] = []
        for selected_value in selected_option.values:
            if not isinstance(selected_value, OptionValueSelection):
                raise TypeError(
                    "option values must contain OptionValueSelection instances."
                )
            value_id = selected_value.id
            if not _is_nonempty_text(value_id) or value_id in value_ids:
                raise SelectionError("Option value IDs must be unique.")
            value_ids.add(value_id)
            catalog_value = root_values.get(value_id)
            if catalog_value is None:
                raise SelectionError(
                    "The selected option value is absent from the root option."
                )
            values.append(
                {
                    "id": value_id,
                    "count": _positive_integer(selected_value.count),
                    "price": _integer(catalog_value.get("price")),
                }
            )
            selected_value_catalog.append(deepcopy(dict(catalog_value)))

        selected_total = sum(value["count"] for value in values)
        if (
            selected_total < constraints.total_minimum
            or selected_total > constraints.total_maximum
            or any(
                value["count"] > constraints.maximum_single_selections
                for value in values
            )
        ):
            raise SelectionError("Selected option counts violate catalog constraints.")

        normalized.append(
            {
                "configuration_id": config_id,
                "values": values,
                "post_checkout_fields": _copy_optional_mapping(
                    selected_option.post_checkout_fields
                ),
                "catalog": {
                    "configuration": deepcopy(dict(configuration)),
                    "root_option": deepcopy(dict(root_option)),
                    "values": selected_value_catalog,
                },
            }
        )

    for configuration_id, configuration in configurations.items():
        if configuration_id in configuration_ids:
            continue
        root_option = _root_option_for_configuration(configuration, root_options)
        constraints = _option_constraints(configuration, root_option)
        if constraints.total_minimum > 0:
            raise SelectionError("A required option configuration was omitted.")
    return normalized


@dataclass(frozen=True)
class _OptionConstraints:
    total_minimum: int
    total_maximum: int
    maximum_single_selections: int


def _root_option_for_configuration(
    configuration: Mapping[str, Any], root_options: Mapping[str, Mapping[str, Any]]
) -> Mapping[str, Any]:
    prerequisites = configuration.get("prerequisite_values")
    if not _is_array(prerequisites) or prerequisites:
        raise UnsupportedSelectionError("Option prerequisites are unsupported.")
    root_option_id = configuration.get("option_id")
    if not _is_nonempty_text(root_option_id):
        raise SelectionError("The item option is missing its root option reference.")
    root_option = root_options.get(root_option_id)
    if root_option is None:
        raise SelectionError("The item option root is absent from the assortment.")
    return root_option


def _option_constraints(
    configuration: Mapping[str, Any], root_option: Mapping[str, Any]
) -> _OptionConstraints:
    if root_option.get("type") not in {"choice", "multi_choice"}:
        raise UnsupportedSelectionError("The root option type is unsupported.")
    configuration_rules = configuration.get("multi_choice_config")
    if not isinstance(configuration_rules, Mapping):
        raise UnsupportedSelectionError("Option selection constraints are unavailable.")
    total_range = configuration_rules.get("total_range")
    if not isinstance(total_range, Mapping):
        raise UnsupportedSelectionError("Option selection constraints are unavailable.")
    minimum = total_range.get("min")
    maximum = total_range.get("max")
    maximum_single = configuration_rules.get("max_single_selections")
    free_selections = configuration_rules.get("free_selections")
    if (
        not _is_integer(minimum)
        or not _is_integer(maximum)
        or not _is_integer(maximum_single)
        or not _is_integer(free_selections)
        or minimum < 0
        or maximum < minimum
        or maximum_single <= 0
        or free_selections != 0
    ):
        raise UnsupportedSelectionError("Option selection constraints are unsupported.")
    return _OptionConstraints(minimum, maximum, maximum_single)


def _serialize_basket_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "count": item["count"],
        "name": item["basket_name"],
        "price": item["basket_price"],
        "options": [
            {
                "id": option["configuration_id"],
                "values": deepcopy(option["values"]),
            }
            for option in item["options"]
        ],
        "substitution_settings": {"is_allowed": item["substitution_allowed"]},
    }


def _serialize_checkout_item(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(item["checkout_fields"])
    payload.update(
        {
            "id": item["id"],
            "count": item["count"],
            "options": [
                {
                    "id": option["configuration_id"],
                    "values": deepcopy(option["values"]),
                }
                for option in item["options"]
            ],
            "base_price": item["base_price"],
            "end_amount": item["end_amount"],
        }
    )
    return payload


def _serialize_post_checkout_item(item: Mapping[str, Any]) -> dict[str, Any]:
    fields = item["post_checkout_fields"]
    if fields is None:
        raise UnsupportedSelectionError(
            "Post-checkout item fields must be supplied explicitly."
        )
    fields_copy = _copy_mapping(fields, "post_checkout_fields")
    _validate_wire_fields(
        fields_copy,
        required=_POST_CHECKOUT_REQUIRED_FIELDS,
        reserved=_POST_CHECKOUT_RESERVED_FIELDS,
        label="post_checkout_fields",
    )
    if (
        not _is_integer(fields_copy["configIndex"])
        or not _is_array(fields_copy["name"])
        or not _is_nonempty_text(fields_copy["category"])
        or not _is_array(fields_copy["product_hierarchy_tags"])
        or any(
            not isinstance(fields_copy[field], bool)
            for field in (
                "exclude_from_credits",
                "exclude_from_discounts",
                "exclude_from_discounts_min_basket",
            )
        )
    ):
        raise SelectionError("The post-checkout item fields are incomplete.")
    if fields_copy["from_recommendation"] is not False:
        raise UnsupportedSelectionError("Recommendation selections are unsupported.")
    if not _is_zero_number(fields_copy["alcohol_percentage"]):
        raise UnsupportedSelectionError("Age-restricted items are unsupported.")

    fields_copy.update(
        {
            "id": item["id"],
            "count": item["count"],
            "baseprice": item["base_price"],
            "end_amount": item["end_amount"],
            "restrictions": deepcopy(item["checkout_fields"]["restrictions"]),
            "options": [
                _serialize_post_checkout_option(option) for option in item["options"]
            ],
            "checksum": item["checksum"],
        }
    )
    return fields_copy


def _serialize_post_checkout_option(option: Mapping[str, Any]) -> dict[str, Any]:
    fields = option["post_checkout_fields"]
    if fields is None:
        raise UnsupportedSelectionError(
            "Post-checkout option fields must be supplied explicitly."
        )
    fields_copy = _copy_mapping(fields, "option post_checkout_fields")
    _validate_wire_fields(
        fields_copy,
        required={"type", "name"},
        reserved=_POST_CHECKOUT_OPTION_RESERVED_FIELDS,
        label="option post_checkout_fields",
    )
    if not _is_nonempty_text(fields_copy["type"]) or not _is_array(fields_copy["name"]):
        raise SelectionError("The post-checkout option fields are incomplete.")
    fields_copy.update(
        {
            "id": option["configuration_id"],
            "values": {value["id"]: value["count"] for value in option["values"]},
        }
    )
    return fields_copy


def _index_by_id(value: Any) -> dict[str, Mapping[str, Any]]:
    if not _is_array(value):
        raise SelectionError("The assortment has an unexpected collection shape.")
    indexed: dict[str, Mapping[str, Any]] = {}
    for record in value:
        if not isinstance(record, Mapping):
            continue
        record_id = record.get("id")
        if not _is_nonempty_text(record_id):
            continue
        if record_id in indexed:
            raise SelectionError("The assortment has duplicate IDs.")
        indexed[record_id] = record
    return indexed


def _validate_wire_fields(
    fields: Mapping[str, Any],
    *,
    required: set[str],
    reserved: set[str],
    label: str,
) -> None:
    if required.difference(fields) or reserved.intersection(fields):
        raise SelectionError(f"{label} is missing required or has reserved fields.")


def _copy_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping.")
    return deepcopy(dict(value))


def _copy_optional_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return _copy_mapping(value, "post_checkout_fields")


def _is_array(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _is_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _integer(value: Any) -> int:
    if not _is_integer(value):
        raise SelectionError("Amounts and counts must be integer values.")
    return value


def _positive_integer(value: Any) -> int:
    value = _integer(value)
    if value <= 0:
        raise SelectionError("Counts must be positive integers.")
    return value


def _nonnegative_integer(value: Any) -> int:
    value = _integer(value)
    if value < 0:
        raise SelectionError("Amounts must be nonnegative integers.")
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_zero_number(value: Any) -> bool:
    return _is_number(value) and value == 0


def _finite_coordinate(value: Any) -> int | float:
    if not _is_number(value):
        raise SelectionError("Delivery coordinates must be finite numbers.")
    try:
        if not math.isfinite(value):
            raise SelectionError("Delivery coordinates must be finite numbers.")
    except OverflowError:
        raise SelectionError("Delivery coordinates must be finite numbers.") from None
    return value
