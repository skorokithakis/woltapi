"""Mutable local basket builder for catalog-derived item selections."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from .errors import SelectionError
from .selection import (
    ItemSelection,
    OptionSelection,
    OptionValueSelection,
    derive_checkout_fields,
)


_CATALOG_CHECKOUT_FIELDS = (
    "alcohol_permille",
    "restrictions",
)
_PAYMENT_FIELDS = (
    "product_hierarchy_tags",
    "vat_percentage",
    "vat_percentage_decimal",
)


@dataclass(frozen=True)
class _BasketItem:
    id: str
    count: int
    options: tuple[OptionSelection, ...]
    substitution_allowed: bool


class Basket:
    """Build mutable catalog-backed items without making network requests.

    The builder only derives local :class:`ItemSelection` values. Catalog feature
    support and checkout-specific selection validation remain the responsibility
    of ``WoltClient.create_selection``.
    """

    def __init__(
        self,
        assortment: Mapping[str, Any],
        language: str,
        *,
        substitution_allowed: bool = False,
    ) -> None:
        if not isinstance(assortment, Mapping):
            raise SelectionError("A basket requires an assortment mapping.")
        if not _is_nonempty_text(language):
            raise SelectionError("A basket requires a language.")
        if not isinstance(substitution_allowed, bool):
            raise TypeError("substitution_allowed must be a boolean.")

        assortment_copy = deepcopy(dict(assortment))
        self._assortment = assortment_copy
        self._catalog_items = _index_by_id(assortment_copy.get("items"))
        self._root_options = _index_by_id(assortment_copy.get("options"))
        self._language = language
        self._default_substitution_allowed = substitution_allowed
        self._items: dict[str, _BasketItem] = {}

    @property
    def contents(self) -> Mapping[str, ItemSelection]:
        """Return a detached, read-only view of the current basket contents."""

        return MappingProxyType(
            {selection.id: selection for selection in self.item_selections()}
        )

    def add_item(
        self,
        item_id: str,
        count: int = 1,
        options: Sequence[OptionSelection] = (),
        *,
        substitution_allowed: bool | None = None,
    ) -> None:
        """Add one catalog item, rejecting duplicate item IDs."""

        item_id = _item_id(item_id)
        if item_id in self._items:
            raise SelectionError("A basket cannot contain duplicate item IDs.")
        item = _BasketItem(
            id=item_id,
            count=_positive_integer(count),
            options=_copy_options(options),
            substitution_allowed=self._substitution_allowed(substitution_allowed),
        )
        self._validate_item(item)
        self._items[item.id] = item

    def remove_item(self, item_id: str) -> None:
        """Remove one item from the basket."""

        item_id = _item_id(item_id)
        if item_id not in self._items:
            raise SelectionError("The item is not in the basket.")
        del self._items[item_id]

    def set_count(self, item_id: str, count: int) -> None:
        """Replace an item's count."""

        item = self._stored_item(item_id)
        updated = _BasketItem(
            item.id,
            _positive_integer(count),
            item.options,
            item.substitution_allowed,
        )
        self._validate_item(updated)
        self._items[updated.id] = updated

    def set_options(self, item_id: str, options: Sequence[OptionSelection]) -> None:
        """Replace an item's selected option values."""

        item = self._stored_item(item_id)
        updated = _BasketItem(
            item.id,
            item.count,
            _copy_options(options),
            item.substitution_allowed,
        )
        self._validate_item(updated)
        self._items[updated.id] = updated

    def item_selections(self) -> tuple[ItemSelection, ...]:
        """Return detached catalog-derived selections in insertion order."""

        return tuple(self._item_selection(item) for item in self._items.values())

    def _stored_item(self, item_id: str) -> _BasketItem:
        item_id = _item_id(item_id)
        item = self._items.get(item_id)
        if item is None:
            raise SelectionError("The item is not in the basket.")
        return item

    def _substitution_allowed(self, value: bool | None) -> bool:
        if value is None:
            return self._default_substitution_allowed
        if not isinstance(value, bool):
            raise TypeError("substitution_allowed must be a boolean.")
        return value

    def _validate_item(self, item: _BasketItem) -> None:
        self._item_selection(item)

    def _item_selection(self, item: _BasketItem) -> ItemSelection:
        catalog_item = self._catalog_items.get(item.id)
        if catalog_item is None:
            raise SelectionError("The selected item is absent from the assortment.")

        base_price = _price(catalog_item.get("price"))
        unit_price = base_price + self._option_price(item.options, catalog_item)
        line_price = item.count * unit_price
        checkout_fields = derive_checkout_fields(self._assortment, catalog_item)
        checkout_fields.update(_required_fields(catalog_item, _CATALOG_CHECKOUT_FIELDS))
        return ItemSelection(
            id=item.id,
            count=item.count,
            basket_name=_translated_name(catalog_item.get("name"), self._language),
            basket_price=line_price,
            end_amount=line_price,
            substitution_allowed=item.substitution_allowed,
            checkout_fields=checkout_fields,
            payment_fields=_required_fields(catalog_item, _PAYMENT_FIELDS),
            options=deepcopy(item.options),
        )

    def _option_price(
        self,
        selected_options: Sequence[OptionSelection],
        catalog_item: Mapping[str, Any],
    ) -> int:
        configurations = _index_by_id(catalog_item.get("options"))
        total = 0
        for selected_option in selected_options:
            if not isinstance(selected_option, OptionSelection):
                raise TypeError("options must contain OptionSelection instances.")
            configuration_id = selected_option.configuration_id
            if not _is_nonempty_text(configuration_id):
                raise SelectionError("An option configuration ID is required.")
            configuration = configurations.get(configuration_id)
            if configuration is None:
                raise SelectionError(
                    "The selected option is not an item configuration."
                )
            option_id = configuration.get("option_id")
            if not _is_nonempty_text(option_id):
                raise SelectionError(
                    "The item option is missing its root option reference."
                )
            root_option = self._root_options.get(option_id)
            if root_option is None:
                raise SelectionError(
                    "The item option root is absent from the assortment."
                )
            values = _index_by_id(root_option.get("values"))
            if not _is_array(selected_option.values):
                raise SelectionError("Option values must be a sequence.")
            for selected_value in selected_option.values:
                if not isinstance(selected_value, OptionValueSelection):
                    raise TypeError(
                        "option values must contain OptionValueSelection instances."
                    )
                value_id = selected_value.id
                if not _is_nonempty_text(value_id):
                    raise SelectionError("An option value ID is required.")
                catalog_value = values.get(value_id)
                if catalog_value is None:
                    raise SelectionError(
                        "The selected option value is absent from the root option."
                    )
                total += _price(catalog_value.get("price")) * _positive_integer(
                    selected_value.count
                )
        return total


def _index_by_id(value: Any) -> dict[str, Mapping[str, Any]]:
    if not _is_array(value):
        raise SelectionError("The assortment has an unexpected collection shape.")
    records: dict[str, Mapping[str, Any]] = {}
    for record in value:
        if not isinstance(record, Mapping):
            continue
        record_id = record.get("id")
        if not _is_nonempty_text(record_id):
            continue
        if record_id in records:
            raise SelectionError("The assortment has duplicate IDs.")
        records[record_id] = record
    return records


def _translated_name(value: Any, language: str) -> str:
    if not _is_array(value):
        raise SelectionError("The selected item is missing translated names.")
    for translation in value:
        if not isinstance(translation, Mapping):
            continue
        translation_language = translation.get("lang", translation.get("language"))
        name = translation.get("value")
        if translation_language == language and _is_nonempty_text(name):
            return name
    raise SelectionError("The selected item has no name for the basket language.")


def _required_fields(
    catalog_item: Mapping[str, Any], field_names: Sequence[str]
) -> dict[str, Any]:
    if any(field not in catalog_item for field in field_names):
        raise SelectionError("The selected item is missing required catalog fields.")
    return {field: deepcopy(catalog_item[field]) for field in field_names}


def _copy_options(value: Any) -> tuple[OptionSelection, ...]:
    if not _is_array(value):
        raise SelectionError("Item options must be a sequence.")
    return tuple(deepcopy(value))


def _item_id(value: Any) -> str:
    if not _is_nonempty_text(value):
        raise SelectionError("An item ID is required.")
    return value


def _price(value: Any) -> int:
    if not _is_integer(value):
        raise SelectionError("Catalog prices must be integer values.")
    return value


def _positive_integer(value: Any) -> int:
    if not _is_integer(value) or value <= 0:
        raise SelectionError("Counts must be positive integers.")
    return value


def _is_array(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
