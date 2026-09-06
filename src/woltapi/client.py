"""Synchronous Wolt discovery, selection, quote, and purchase client."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from .credentials import SessionCredentials
from .errors import ResponseShapeError, SelectionError
from .models import DeliveryTarget, OrderStatus, PaymentMethod, Venue
from .purchase import (
    PurchaseAttemptStore,
    PurchaseAuthorization,
    PurchaseContext,
    PurchaseResult,
    PreparedOrder,
    _prepare_purchase_order,
    _submit_prepared_order,
)
from .selection import (
    DeliverySelection,
    ItemSelection,
    OrderSelection,
    PostCheckoutConfig,
    QuoteSnapshot,
    SavedBasket,
    VenueCheckoutContext,
)
from .services import ServiceHost
from .transport import DEFAULT_TIMEOUT_SECONDS, WoltTransport


class WoltClient:
    """Read discovery data and make explicit selection and purchase operations.

    Purchase submission requires a caller-created authorization and an explicit,
    durable attempt store. Credentials may manage token refresh. The class has no
    login, asynchronous, WebSocket, card-enrollment, challenge, cancellation,
    refund, or autonomous ordering methods. Local selections are immutable; a
    changed selection must be rebuilt and cannot silently reuse an earlier quote
    snapshot.
    """

    def __init__(
        self,
        credentials: SessionCredentials,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        _transport: WoltTransport | None = None,
    ) -> None:
        if not isinstance(credentials, SessionCredentials):
            raise TypeError("credentials must be a SessionCredentials instance.")
        if _transport is not None and not isinstance(_transport, WoltTransport):
            raise TypeError("_transport must be a WoltTransport instance.")
        self._transport = _transport or WoltTransport(credentials, timeout=timeout)
        self._delivery_target_ids: set[str] = set()
        self._payment_eligibility_by_id: dict[str, str] = {}

    def search_venues(
        self, query: str, latitude: int | float, longitude: int | float
    ) -> tuple[Venue, ...]:
        """Search venues and extract results with a usable ID and slug."""

        query = _required_text(query, "query")
        latitude = _coordinate(latitude, "latitude")
        longitude = _coordinate(longitude, "longitude")
        response = self._transport.request(
            ServiceHost.RESTAURANT,
            "POST",
            "/v1/pages/search",
            json_body={"q": query, "target": None, "lat": latitude, "lon": longitude},
        )
        sections = _required_list(response, "sections", ServiceHost.RESTAURANT)

        venues: list[Venue] = []
        for section in sections:
            if not isinstance(section, Mapping):
                continue
            items = section.get("items")
            if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
                continue
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                venue = item.get("venue")
                if not isinstance(venue, Mapping):
                    continue
                venue_id = venue.get("id")
                venue_slug = venue.get("slug")
                if not _is_nonempty_string(venue_id) or not _is_nonempty_string(
                    venue_slug
                ):
                    continue
                venues.append(
                    Venue(
                        id=venue_id,
                        slug=venue_slug,
                        title=_optional_string(item.get("title")),
                        currency=_optional_string(venue.get("currency")),
                        delivers=_optional_bool(venue.get("delivers")),
                        online=_optional_bool(venue.get("online")),
                    )
                )
        return tuple(venues)

    def get_orders_page(self) -> dict[str, Any]:
        """Read the current order-history page in server-provided order.

        The detached page can contain private order data. Do not log it raw.
        """
        return self._transport.request(
            ServiceHost.CONSUMER,
            "GET",
            "/order-xp/web/v1/pages/orders",
        )

    def get_venue_static(self, venue_slug: str) -> dict[str, Any]:
        """Read the static venue page for a slug."""

        slug = _path_segment(venue_slug, "venue_slug")
        return self._transport.request(
            ServiceHost.CONSUMER,
            "GET",
            f"/order-xp/web/v1/pages/venue/slug/{slug}/static",
        )

    def get_venue_dynamic(
        self,
        venue_slug: str,
        latitude: int | float,
        longitude: int | float,
        *,
        selected_delivery_method: str = "homedelivery",
    ) -> dict[str, Any]:
        """Read the location-aware venue page, preserving its trailing slash."""

        slug = _path_segment(venue_slug, "venue_slug")
        latitude = _coordinate(latitude, "latitude")
        longitude = _coordinate(longitude, "longitude")
        selected_delivery_method = _required_text(
            selected_delivery_method, "selected_delivery_method"
        )
        return self._transport.request(
            ServiceHost.CONSUMER,
            "GET",
            f"/order-xp/web/v1/venue/slug/{slug}/dynamic/",
            query={
                "lat": latitude,
                "lon": longitude,
                "selected_delivery_method": selected_delivery_method,
            },
        )

    def get_venue_content(self, venue_slug: str) -> dict[str, Any]:
        """Read the server-driven venue-content document for a slug."""

        slug = _path_segment(venue_slug, "venue_slug")
        return self._transport.request(
            ServiceHost.CONSUMER,
            "GET",
            f"/consumer-api/venue-content-api/v3/web/venue-content/slug/{slug}",
        )

    def get_assortment(self, venue_slug: str) -> dict[str, Any]:
        """Read the normalized assortment without dropping catalog fields."""

        slug = _path_segment(venue_slug, "venue_slug")
        return self._transport.request(
            ServiceHost.CONSUMER,
            "GET",
            f"/consumer-api/consumer-assortment/v1/venues/slug/{slug}/assortment",
        )

    def get_item(
        self, venue_id: str, menu_item_id: str, *, language: str
    ) -> dict[str, Any]:
        """Read an item-detail page without transforming its catalog data."""

        venue_id = _path_segment(venue_id, "venue_id")
        menu_item_id = _path_segment(menu_item_id, "menu_item_id")
        language = _required_text(language, "language")
        return self._transport.request(
            ServiceHost.CONSUMER,
            "GET",
            f"/order-xp/web/v1/pages/venue/{venue_id}/item/{menu_item_id}",
            query={"language": language},
        )

    def list_delivery_targets(self) -> tuple[DeliveryTarget, ...]:
        """List saved delivery IDs with limited display details."""

        self._delivery_target_ids = set()
        response = self._transport.request(
            ServiceHost.RESTAURANT,
            "GET",
            "/v2/delivery/info",
        )
        results = _required_list(response, "results", ServiceHost.RESTAURANT)

        targets: list[DeliveryTarget] = []
        target_ids: set[str] = set()
        for result in results:
            if not isinstance(result, Mapping):
                continue
            target_id = result.get("id")
            if not _is_nonempty_string(target_id):
                continue
            location = result.get("location")
            if not isinstance(location, Mapping):
                location = {}
            targets.append(
                DeliveryTarget(
                    id=target_id,
                    alias=_optional_string(result.get("alias")),
                    label_type=_optional_string(result.get("label_type")),
                    address=_optional_string(location.get("address")),
                    city=_optional_string(location.get("city")),
                    postcode=_optional_string(location.get("postcode")),
                )
            )
            target_ids.add(target_id)
        self._delivery_target_ids = target_ids
        return tuple(targets)

    def get_payment_methods(
        self, context: Mapping[str, Any]
    ) -> tuple[PaymentMethod, ...]:
        """Return enabled saved-card references from a payment element tree."""

        if not isinstance(context, Mapping):
            raise TypeError("context must be a mapping.")
        eligibility_binding = _payment_eligibility_binding(context)
        self._payment_eligibility_by_id = {}
        response = self._transport.request(
            ServiceHost.PAYMENT,
            "POST",
            "/v1/payment-methods/checkout",
            json_body=dict(context),
        )
        root = response.get("root_element")
        if not isinstance(root, Mapping):
            raise ResponseShapeError(ServiceHost.PAYMENT.value)

        methods: list[PaymentMethod] = []
        eligibility_by_id: dict[str, str] = {}
        stack: list[Mapping[str, Any]] = [root]
        while stack:
            node = stack.pop()
            children = node.get("children")
            if isinstance(children, Sequence) and not isinstance(
                children, (str, bytes)
            ):
                stack.extend(
                    child for child in reversed(children) if isinstance(child, Mapping)
                )

            if (
                node.get("element_type") != "payment-method"
                or node.get("is_enabled") is not True
            ):
                continue
            method = node.get("method")
            if not isinstance(method, Mapping):
                continue
            method_id = method.get("id")
            method_type = method.get("type")
            if not _is_nonempty_string(method_id) or method_type != "card":
                continue
            methods.append(
                PaymentMethod(
                    id=method_id,
                    type=method_type,
                    is_selected=node.get("is_selected") is True,
                    is_default=node.get("is_default") is True,
                    title=_optional_string(node.get("title")),
                    subtitle=_optional_string(node.get("subtitle")),
                )
            )
            eligibility_by_id[method_id] = eligibility_binding
        self._payment_eligibility_by_id = eligibility_by_id
        return tuple(methods)

    def create_selection(
        self,
        assortment: Mapping[str, Any],
        *,
        venue: VenueCheckoutContext,
        delivery: DeliverySelection,
        payment_method: Mapping[str, Any],
        courier_tip: int,
        items: Sequence[ItemSelection],
    ) -> OrderSelection:
        """Build an immutable local selection from current catalog data.

        A caller must first discover the saved delivery target and enabled card
        through this client. The supplied payment mapping is copied exactly;
        this client does not derive checkout-card fields from payment UI data.
        """

        if not isinstance(delivery, DeliverySelection):
            raise TypeError("delivery must be a DeliverySelection instance.")
        if delivery.delivery_info_id not in self._delivery_target_ids:
            raise SelectionError("The saved delivery reference is not current.")
        if not isinstance(payment_method, Mapping):
            raise TypeError("payment_method must be a mapping.")
        payment_method_id = payment_method.get("id")
        if (
            not _is_nonempty_string(payment_method_id)
            or payment_method_id not in self._payment_eligibility_by_id
        ):
            raise SelectionError("The saved card reference is not current.")
        selection = OrderSelection._from_assortment(
            assortment,
            venue=venue,
            delivery=delivery,
            payment_method=payment_method,
            courier_tip=courier_tip,
            items=items,
        )
        if self._payment_eligibility_by_id[
            payment_method_id
        ] != _payment_eligibility_binding(selection._payment_eligibility_context()):
            raise SelectionError(
                "The saved card eligibility does not match this selection."
            )
        return selection

    def save_basket(self, selection: OrderSelection) -> SavedBasket:
        """Persist a basket explicitly; this mutation does not place an order."""

        selection = _order_selection(selection)
        response = self._transport.request(
            ServiceHost.CONSUMER,
            "POST",
            "/order-xp/v1/baskets",
            json_body=selection.to_basket_payload(),
        )
        basket_id = response.get("id")
        venue_id = response.get("venue_id")
        if not _is_nonempty_string(basket_id) or not _is_nonempty_string(venue_id):
            raise ResponseShapeError(ServiceHost.CONSUMER.value)
        return SavedBasket(id=basket_id, venue_id=venue_id)

    def quote_checkout(self, selection: OrderSelection) -> QuoteSnapshot:
        """Request one checkout quote and capture independent plan/response snapshots."""

        selection = _order_selection(selection)
        payload = selection.to_checkout_payload()
        response = self._transport.request(
            ServiceHost.CONSUMER,
            "POST",
            "/order-xp/web/v2/pages/checkout",
            json_body=payload,
        )
        return QuoteSnapshot._capture(
            selection,
            payload["purchase_plan"],
            response,
            ServiceHost.CONSUMER.value,
        )

    def get_post_checkout_config(self, selection: OrderSelection) -> PostCheckoutConfig:
        """Discover required consents using the distinct post-checkout payload."""

        selection = _order_selection(selection)
        response = self._transport.request(
            ServiceHost.RESTAURANT,
            "POST",
            "/v1/post-checkout-config",
            json_body=selection.to_post_checkout_payload(),
        )
        return PostCheckoutConfig._from_response(
            response,
            ServiceHost.RESTAURANT.value,
            selection,
        )

    def prepare_purchase(
        self,
        selection: OrderSelection,
        quote: QuoteSnapshot,
        consents: PostCheckoutConfig,
        context: PurchaseContext,
    ) -> PreparedOrder:
        """Prepare a one-card, home-delivery purchase without a network effect.

        The selected delivery target and saved card must still be present in this
        client's most recently discovered eligible references. This does not
        establish a server quote expiry or replace the caller confirmation step.
        """

        selection = _order_selection(selection)
        selection_eligibility = _payment_eligibility_binding(
            selection._payment_eligibility_context()
        )
        eligible_payment_ids = {
            payment_id
            for payment_id, eligibility in self._payment_eligibility_by_id.items()
            if eligibility == selection_eligibility
        }
        return _prepare_purchase_order(
            selection,
            quote,
            consents,
            context,
            enabled_payment_ids=eligible_payment_ids,
            delivery_target_ids=set(self._delivery_target_ids),
        )

    def submit_prepared_order(
        self,
        store: PurchaseAttemptStore,
        prepared_order: PreparedOrder,
        authorization: PurchaseAuthorization,
    ) -> PurchaseResult:
        """Send one explicit purchase attempt through this client's transport."""

        return _submit_prepared_order(
            self._transport,
            store,
            prepared_order,
            authorization,
        )

    def get_order_status(self, purchase_id: str) -> OrderStatus:
        """Read the dedicated tracking state without constraining status values."""

        purchase_id = _required_text(purchase_id, "purchase_id")
        encoded_purchase_id = quote(purchase_id, safe="")
        response = self._transport.request(
            ServiceHost.RESTAURANT,
            "GET",
            f"/v2/order_details/purchase_tracking/{encoded_purchase_id}",
        )
        details = response.get("order_details")
        if not isinstance(details, Mapping):
            raise ResponseShapeError(ServiceHost.RESTAURANT.value)
        order_id = details.get("order_id")
        status = details.get("status")
        if (
            not _is_nonempty_string(order_id)
            or order_id != purchase_id
            or not isinstance(status, str)
        ):
            raise ResponseShapeError(ServiceHost.RESTAURANT.value)

        return OrderStatus(
            purchase_id=order_id,
            status=status,
            currency=_optional_string(details.get("currency")),
            payment_amount=_optional_number(details.get("payment_amount")),
            total_price=_optional_number(details.get("total_price")),
            delivery_price=_optional_number(details.get("delivery_price")),
            delivery_method=_optional_string(details.get("delivery_method")),
        )


def _required_text(value: object, name: str) -> str:
    if not _is_nonempty_string(value):
        raise ValueError(f"{name} must be a non-empty string.")
    return value


def _path_segment(value: object, name: str) -> str:
    return quote(_required_text(value, name), safe="")


def _coordinate(value: object, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number.")
    try:
        is_finite = math.isfinite(value)
    except OverflowError:
        is_finite = False
    if not is_finite:
        raise ValueError(f"{name} must be a finite number.")
    return value


def _required_list(
    response: Mapping[str, Any], field: str, service: ServiceHost
) -> Sequence[Any]:
    value = response.get(field)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ResponseShapeError(service.value)
    return value


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _order_selection(value: object) -> OrderSelection:
    if not isinstance(value, OrderSelection):
        raise TypeError("selection must be an OrderSelection instance.")
    return value


def _payment_eligibility_binding(context: Mapping[str, Any]) -> str:
    """Hash the minimum observed card-eligibility context without retaining it."""

    venue_id = context.get("venue_id")
    delivery_method = context.get("delivery_method")
    items = context.get("items")
    if (
        not _is_nonempty_string(venue_id)
        or not _is_nonempty_string(delivery_method)
        or not isinstance(items, Sequence)
        or isinstance(items, (str, bytes))
        or not items
    ):
        raise SelectionError("The payment eligibility context is incomplete.")

    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise SelectionError("The payment eligibility context is incomplete.")
        item_id = item.get("id")
        alcohol_permille = item.get("alcohol_permille")
        tags = item.get("product_hierarchy_tags")
        vat_percentage = item.get("vat_percentage")
        vat_percentage_decimal = item.get("vat_percentage_decimal")
        if (
            not _is_nonempty_string(item_id)
            or not _is_number(alcohol_permille)
            or not isinstance(tags, Sequence)
            or isinstance(tags, (str, bytes))
            or not _is_number(vat_percentage)
            or not _is_nonempty_string(vat_percentage_decimal)
        ):
            raise SelectionError("The payment eligibility context is incomplete.")
        normalized_items.append(
            {
                "id": item_id,
                "alcohol_permille": alcohol_permille,
                "product_hierarchy_tags": list(tags),
                "vat_percentage": vat_percentage,
                "vat_percentage_decimal": vat_percentage_decimal,
            }
        )

    try:
        encoded = json.dumps(
            {
                "venue_id": venue_id,
                "delivery_method": delivery_method,
                "items": normalized_items,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise SelectionError(
            "The payment eligibility context is unsupported."
        ) from None
    return hashlib.sha256(encoded).hexdigest()


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
