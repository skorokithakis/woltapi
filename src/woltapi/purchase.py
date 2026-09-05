"""Explicit purchase preparation, authorization, and one-attempt submission."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from typing import Any
from uuid import uuid4

from .errors import (
    DuplicatePurchaseAttempt,
    OrderOutcomeUnknown,
    PurchaseAttemptStoreError,
    PurchaseAuthorizationError,
    PurchasePreparationError,
)
from .selection import OrderSelection, PostCheckoutConfig, QuoteSnapshot
from .services import ServiceHost
from .transport import WoltTransport


@dataclass(frozen=True)
class PurchaseContext:
    """Caller-supplied fields whose provenance is unresolved by the evidence.

    The values are copied as supplied. This class deliberately does not create
    a client nonce, device identifier, timestamp, signature, browser context,
    return URL, purchase-item names, or purchase-only item fields.
    """

    client_nonce: str = field(repr=False)
    ravelin_device_id: str = field(repr=False)
    signature_datetime: Mapping[str, Any] = field(repr=False)
    language: str
    client_pre_estimate: str = field(repr=False)
    signature: str = field(repr=False)
    pricing_model_version: int
    device_channel: str
    browser_info: Mapping[str, Any] = field(repr=False)
    payment_links: Mapping[str, Any] = field(repr=False)
    delivery_info_options: Mapping[str, Any] = field(repr=False)
    additional_checkout_options: Mapping[str, Any] = field(repr=False)
    menu_items_source: str
    use_self_service_cancellation: bool
    purchase_items: Sequence[Mapping[str, Any]] = field(repr=False)
    optional_fields: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class PurchaseNameSummary:
    """One readable localized name from the exact purchase representation."""

    value: str
    language: str


@dataclass(frozen=True)
class PurchaseOptionValueSummary:
    """One exact selected purchase-option value for caller confirmation."""

    value_id: str
    count: int
    names: tuple[PurchaseNameSummary, ...]


@dataclass(frozen=True)
class PurchaseOptionSummary:
    """One exact selected option and its confirmed values."""

    option_id: str
    names: tuple[PurchaseNameSummary, ...]
    values: tuple[PurchaseOptionValueSummary, ...]


@dataclass(frozen=True)
class PurchaseLineSummary:
    """One exact item, readable names, and selected options for confirmation."""

    item_id: str
    count: int
    names: tuple[PurchaseNameSummary, ...]
    options: tuple[PurchaseOptionSummary, ...]


@dataclass(frozen=True)
class PurchaseConfirmationSummary:
    """Amounts and references that must be shown before caller authorization."""

    checkout_id: str
    venue_id: str
    delivery_info_id: str
    payment_method_id: str
    courier_tip: int
    payable_amount: int | float
    end_amount: int
    items: tuple[PurchaseLineSummary, ...]


@dataclass(frozen=True)
class PurchaseResult:
    """Minimal successful-purchase response without raw operational details."""

    purchase_id: str
    status: str
    currency: str
    amount: int | float
    delivery_method: str
    delivery_price: int | float
    payment_method_type: str


@dataclass(frozen=True)
class _AttemptRecord:
    attempt_id: str
    state: str
    purchase_id: str | None


class PurchaseAttemptStore:
    """SQLite-backed minimal state that prevents re-sending one prepared order.

    Only a hash-derived attempt key, a local attempt ID, state, and an available
    purchase ID are stored. Credentials, authorization text, prepared payloads,
    delivery details, and payment metadata are never written to this store.
    """

    __slots__ = ("_path",)

    def __init__(self, path: str | os.PathLike[str]) -> None:
        if not isinstance(path, (str, os.PathLike)) or str(path) == ":memory:":
            raise ValueError("A durable filesystem path is required for attempt state.")
        self._path = Path(path).expanduser()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            raise PurchaseAttemptStoreError(
                "Durable purchase-attempt state could not be initialized."
            ) from None
        self._ensure_private_file()
        self._initialize()

    def begin(self, attempt_key: str) -> tuple[bool, _AttemptRecord]:
        """Atomically persist a started attempt before a network submission."""

        attempt_id = uuid4().hex
        connection = self._connect()
        try:
            with connection:
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO purchase_attempts
                    (attempt_key, attempt_id, state, purchase_id)
                    VALUES (?, ?, 'started', NULL)
                    """,
                    (attempt_key, attempt_id),
                ).rowcount
                if inserted:
                    return True, _AttemptRecord(attempt_id, "started", None)
                row = connection.execute(
                    """
                    SELECT attempt_id, state, purchase_id
                    FROM purchase_attempts
                    WHERE attempt_key = ?
                    """,
                    (attempt_key,),
                ).fetchone()
        except sqlite3.Error:
            raise PurchaseAttemptStoreError(
                "Durable purchase-attempt state could not be recorded."
            ) from None
        finally:
            connection.close()

        if row is None:
            raise PurchaseAttemptStoreError(
                "Durable purchase-attempt state could not be read."
            )
        return False, _AttemptRecord(row[0], row[1], row[2])

    def mark_succeeded(self, attempt_key: str, purchase_id: str) -> None:
        self._update(attempt_key, "succeeded", purchase_id)

    def mark_unknown(self, attempt_key: str, purchase_id: str | None = None) -> None:
        """Record an ambiguous result without making the attempt sendable again."""

        if purchase_id is not None and not _is_text(purchase_id):
            raise PurchaseAttemptStoreError("A returned purchase ID was invalid.")
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE purchase_attempts
                    SET state = 'unknown', purchase_id = COALESCE(?, purchase_id)
                    WHERE attempt_key = ? AND state = 'started'
                    """,
                    (purchase_id, attempt_key),
                )
        except sqlite3.Error:
            raise PurchaseAttemptStoreError(
                "Durable purchase-attempt state could not be updated."
            ) from None
        finally:
            connection.close()

    def _update(self, attempt_key: str, state: str, purchase_id: str) -> None:
        connection = self._connect()
        try:
            with connection:
                updated = connection.execute(
                    """
                    UPDATE purchase_attempts
                    SET state = ?, purchase_id = ?
                    WHERE attempt_key = ? AND state = 'started'
                    """,
                    (state, purchase_id, attempt_key),
                ).rowcount
        except sqlite3.Error:
            raise PurchaseAttemptStoreError(
                "Durable purchase-attempt state could not be updated."
            ) from None
        finally:
            connection.close()
        if updated != 1:
            raise PurchaseAttemptStoreError(
                "Durable purchase-attempt state was not in a sendable state."
            )

    def _ensure_private_file(self) -> None:
        try:
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            pass
        except OSError:
            raise PurchaseAttemptStoreError(
                "Durable purchase-attempt state could not be initialized."
            ) from None
        else:
            os.close(descriptor)
        try:
            if not self._path.is_file() or self._path.is_symlink():
                raise OSError
            os.chmod(self._path, 0o600)
        except OSError:
            raise PurchaseAttemptStoreError(
                "Durable purchase-attempt state could not be initialized."
            ) from None

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS purchase_attempts (
                        attempt_key TEXT PRIMARY KEY,
                        attempt_id TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        purchase_id TEXT
                    )
                    """
                )
        except sqlite3.Error:
            raise PurchaseAttemptStoreError(
                "Durable purchase-attempt state could not be initialized."
            ) from None
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self._path, timeout=5.0)
            connection.execute("PRAGMA busy_timeout = 5000")
            return connection
        except sqlite3.Error:
            raise PurchaseAttemptStoreError(
                "Durable purchase-attempt state could not be opened."
            ) from None


_PURCHASE_AUTHORIZATION_TOKEN = object()


class PurchaseAuthorization:
    """An explicit caller confirmation hash-bound to one prepared order."""

    __slots__ = ("_order_binding",)

    def __init__(
        self,
        private_token: object,
        order_binding: str,
    ) -> None:
        if private_token is not _PURCHASE_AUTHORIZATION_TOKEN:
            raise TypeError(
                "Use authorize_prepared_order() to create an authorization."
            )
        self._order_binding = order_binding

    def __repr__(self) -> str:
        return "PurchaseAuthorization(<redacted>)"

    @classmethod
    def _for_order(
        cls, prepared_order: PreparedOrder, confirmation_id: str
    ) -> PurchaseAuthorization:
        if not _is_text(confirmation_id):
            raise PurchaseAuthorizationError(
                "An explicit caller confirmation identifier is required."
            )
        return cls(
            _PURCHASE_AUTHORIZATION_TOKEN,
            prepared_order._binding(),
        )

    def _matches(self, prepared_order: PreparedOrder) -> bool:
        return self._order_binding == prepared_order._binding()


class PreparedOrder:
    """An immutable purchase payload bound to its exact retained quote."""

    __slots__ = (
        "_attempt_key_value",
        "_payload",
        "_payload_binding",
        "_binding_value",
        "_summary",
    )

    def __init__(
        self,
        payload: Mapping[str, Any],
        summary: PurchaseConfirmationSummary,
        quote: Mapping[str, Any],
    ) -> None:
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping.")
        if not isinstance(summary, PurchaseConfirmationSummary):
            raise TypeError("summary must be a PurchaseConfirmationSummary instance.")
        if not isinstance(quote, Mapping):
            raise TypeError("quote must be a mapping.")
        payload_snapshot = deepcopy(dict(payload))
        quote_snapshot = deepcopy(dict(quote))
        payload_checkout_id = payload_snapshot.get("checkout_id")
        quote_checkout_id = quote_snapshot.get("id")
        summary_checkout_id = summary.checkout_id
        if (
            not _is_text(payload_checkout_id)
            or not _is_text(quote_checkout_id)
            or not _is_text(summary_checkout_id)
            or payload_checkout_id != quote_checkout_id
            or quote_checkout_id != summary_checkout_id
        ):
            raise PurchasePreparationError(
                "The prepared order checkout identities must match."
            )
        checkout_id = quote_checkout_id
        self._payload = payload_snapshot
        self._payload_binding = _payload_digest(self._payload)
        self._binding_value = _payload_digest(
            {
                "purchase_payload": self._payload,
                "quote": quote_snapshot,
                "summary": _summary_binding_data(summary),
            }
        )
        self._summary = summary
        self._attempt_key_value = _digest_text(f"woltapi-attempt-v3:{checkout_id}")

    @property
    def confirmation_summary(self) -> PurchaseConfirmationSummary:
        return self._summary

    def __repr__(self) -> str:
        return f"PreparedOrder(checkout_id={self._summary.checkout_id!r})"

    def _payload_snapshot(self) -> dict[str, Any]:
        return deepcopy(self._payload)

    def _binding(self) -> str:
        if _payload_digest(self._payload) != self._payload_binding:
            raise PurchaseAuthorizationError(
                "The prepared order changed after preparation."
            )
        return self._binding_value

    def _attempt_key(self) -> str:
        """Identify the retained checkout independently of mutable purchase context."""

        return self._attempt_key_value


def _prepare_purchase_order(
    selection: OrderSelection,
    quote: QuoteSnapshot,
    consents: PostCheckoutConfig,
    context: PurchaseContext,
    *,
    enabled_payment_ids: set[str],
    delivery_target_ids: set[str],
) -> PreparedOrder:
    """Prepare one exact payload after conservative local safety checks."""

    if not isinstance(selection, OrderSelection):
        raise TypeError("selection must be an OrderSelection instance.")
    if not isinstance(quote, QuoteSnapshot):
        raise TypeError("quote must be a QuoteSnapshot instance.")
    if not isinstance(consents, PostCheckoutConfig):
        raise TypeError("consents must be a PostCheckoutConfig instance.")
    if not isinstance(context, PurchaseContext):
        raise TypeError("context must be a PurchaseContext instance.")
    current_payment_ids = _normalize_reference_ids(
        enabled_payment_ids, "enabled payment references"
    )
    current_delivery_ids = _normalize_reference_ids(
        delivery_target_ids, "saved delivery references"
    )
    if not quote.is_current_for(selection) or not consents.is_current_for(selection):
        raise PurchasePreparationError(
            "The selection, quote, and consent snapshot must match exactly."
        )
    if consents.required_consents:
        raise PurchasePreparationError(
            "Outstanding post-checkout consents block purchase."
        )

    plan = quote._plan_snapshot()
    quote_response = quote._quote_snapshot()
    selection_data = quote._selection_snapshot()
    if plan != selection.to_checkout_payload()["purchase_plan"]:
        raise PurchasePreparationError(
            "The quote plan no longer matches the selection."
        )
    payment_method, delivery_info_id, venue = _validate_plan_scope(
        plan,
        enabled_payment_ids=current_payment_ids,
        delivery_target_ids=current_delivery_ids,
    )
    validation, payable_amount = _validate_quote_scope(quote_response, payment_method)
    normalized_context = _normalize_context(context)
    purchase_items = _validate_purchase_items(
        normalized_context["purchase_items"],
        plan["menu_items"],
        selection_data["items"],
    )

    payload = deepcopy(normalized_context["optional_fields"])
    payload.update(
        {
            "client_nonce": normalized_context["client_nonce"],
            "ravelin_device_id": normalized_context["ravelin_device_id"],
            "signature_datetime": normalized_context["signature_datetime"],
            "language": normalized_context["language"],
            "currency": venue["currency"],
            "client_pre_estimate": normalized_context["client_pre_estimate"],
            "delivery_method": "homedelivery",
            "items": purchase_items,
            "type": "purchase",
            "signature": normalized_context["signature"],
            "pricing_model_version": normalized_context["pricing_model_version"],
            "payment_method_type": payment_method["type"],
            "payment_method_id": payment_method["id"],
            "end_amount": validation["end_amount"],
            "tip_amount": plan["courier_tip"],
            "no_credits_or_tokens": True,
            "device_channel": normalized_context["device_channel"],
            "to_type": "venue",
            "venue_id": venue["id"],
            "browser_info": normalized_context["browser_info"],
            "payment_links": normalized_context["payment_links"],
            "delivery_info": {
                "id": {"$oid": delivery_info_id},
                **normalized_context["delivery_info_options"],
            },
            "delivery_price": validation["delivery_price"],
            "additional_checkout_options": normalized_context[
                "additional_checkout_options"
            ],
            "discounts": [],
            "offers": [],
            "surcharges": [],
            "use_token": False,
            "price_shadowing": deepcopy(validation),
            "menu_items_source": normalized_context["menu_items_source"],
            "checkout_id": quote.checkout_id,
            "use_self_service_cancellation": normalized_context[
                "use_self_service_cancellation"
            ],
        }
    )
    summary = PurchaseConfirmationSummary(
        checkout_id=quote.checkout_id,
        venue_id=venue["id"],
        delivery_info_id=delivery_info_id,
        payment_method_id=payment_method["id"],
        courier_tip=plan["courier_tip"],
        payable_amount=payable_amount,
        end_amount=validation["end_amount"],
        items=tuple(_purchase_line_summary(item) for item in purchase_items),
    )
    return PreparedOrder(payload, summary, quote_response)


def authorize_prepared_order(
    prepared_order: PreparedOrder, *, confirmation_id: str
) -> PurchaseAuthorization:
    """Bind an explicit caller confirmation identifier to one prepared payload."""

    if not isinstance(prepared_order, PreparedOrder):
        raise TypeError("prepared_order must be a PreparedOrder instance.")
    return PurchaseAuthorization._for_order(prepared_order, confirmation_id)


def _submit_prepared_order(
    transport: WoltTransport,
    store: PurchaseAttemptStore,
    prepared_order: PreparedOrder,
    authorization: PurchaseAuthorization,
) -> PurchaseResult:
    """Send exactly one purchase request for a durable authorized attempt."""

    if not isinstance(transport, WoltTransport):
        raise TypeError("transport must be a WoltTransport instance.")
    if not isinstance(store, PurchaseAttemptStore):
        raise PurchaseAttemptStoreError(
            "A durable PurchaseAttemptStore is required for submission."
        )
    if not isinstance(prepared_order, PreparedOrder):
        raise TypeError("prepared_order must be a PreparedOrder instance.")
    if not isinstance(
        authorization, PurchaseAuthorization
    ) or not authorization._matches(prepared_order):
        raise PurchaseAuthorizationError(
            "Caller authorization does not match the prepared order."
        )

    attempt_key = prepared_order._attempt_key()
    is_new, record = store.begin(attempt_key)
    if not is_new:
        raise DuplicatePurchaseAttempt(
            record.attempt_id,
            record.state,
            record.purchase_id,
        )

    purchase_id: str | None = None
    try:
        response = transport.request(
            ServiceHost.RESTAURANT,
            "POST",
            "/v2/purchases",
            json_body=prepared_order._payload_snapshot(),
        )
        purchase_id = _extract_purchase_id(response)
        result = _parse_purchase_result(response)
        store.mark_succeeded(attempt_key, result.purchase_id)
    except Exception:
        _mark_unknown_without_masking(store, attempt_key, purchase_id)
        raise OrderOutcomeUnknown(record.attempt_id, purchase_id) from None
    return result


def _validate_plan_scope(
    plan: Mapping[str, Any],
    *,
    enabled_payment_ids: Collection[str],
    delivery_target_ids: Collection[str],
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    if not isinstance(plan, Mapping):
        raise PurchasePreparationError("The retained checkout plan is unavailable.")
    venue = _copy_mapping(plan.get("venue"), "checkout venue")
    delivery = _copy_mapping(plan.get("delivery"), "checkout delivery")
    delivery_config = _copy_mapping(plan.get("delivery_config"), "delivery config")
    payment_methods = plan.get("payment_methods")
    if (
        plan.get("delivery_method") != "homedelivery"
        or delivery_config.get("method") != "homedelivery"
        or delivery_config.get("schedule") != "standard"
        or delivery_config.get("time_slot") is not None
        or plan.get("use_cash") is not False
        or plan.get("use_credits_and_tokens") is not False
        or plan.get("use_loyalty_points_amount") != 0
        or plan.get("selected_offer_ids") != []
        or plan.get("use_promo_surcharge_ids") != []
        or not _is_array(payment_methods)
        or len(payment_methods) != 1
    ):
        raise PurchasePreparationError("The checkout plan uses an unsupported flow.")
    payment_method = _copy_mapping(payment_methods[0], "payment method")
    payment_id = payment_method.get("id")
    delivery_info_id = delivery.get("delivery_info_id")
    if (
        not _is_text(venue.get("id"))
        or not _is_text(venue.get("currency"))
        or not _is_text(payment_id)
        or payment_method.get("type") != "card"
        or payment_id not in enabled_payment_ids
        or not _is_text(delivery_info_id)
        or delivery_info_id not in delivery_target_ids
        or not _is_array(plan.get("menu_items"))
        or not plan["menu_items"]
    ):
        raise PurchasePreparationError("The checkout plan is not currently eligible.")
    return payment_method, delivery_info_id, venue


def _validate_quote_scope(
    quote: Mapping[str, Any], payment_method: Mapping[str, Any]
) -> tuple[dict[str, Any], int | float]:
    if not isinstance(quote, Mapping):
        raise PurchasePreparationError("The retained quote response is unavailable.")
    purchasing_disabled = quote.get("purchasing_disabled", _MISSING)
    if purchasing_disabled is _MISSING or (
        purchasing_disabled is not None and purchasing_disabled is not False
    ):
        raise PurchasePreparationError(
            "The quote has unresolved purchasing restrictions."
        )
    call_to_action = quote.get("call_to_action", _MISSING)
    if call_to_action is not _MISSING and (
        not isinstance(call_to_action, Mapping)
        or call_to_action.get("enabled") is not True
    ):
        raise PurchasePreparationError("The purchase action is unavailable.")
    if (
        quote.get("is_age_verification_required") is not False
        or quote.get("use_address_matching_for_age_verification") is not False
    ):
        raise PurchasePreparationError("Age-verification flows are unsupported.")
    payable_amount = quote.get("payable_amount")
    validation = quote.get("purchase_validation")
    breakdown = quote.get("payment_breakdown")
    if (
        not _is_number(payable_amount)
        or not isinstance(validation, Mapping)
        or not isinstance(breakdown, Mapping)
    ):
        raise PurchasePreparationError(
            "The quote is incomplete for purchase preparation."
        )
    validation_copy = deepcopy(dict(validation))
    if not _is_integer(validation_copy.get("end_amount")) or not _is_integer(
        validation_copy.get("delivery_price")
    ):
        raise PurchasePreparationError("The quote validation is incomplete.")
    total = _copy_mapping(breakdown.get("total"), "payment total")
    unallocated = _copy_mapping(breakdown.get("unallocated"), "payment allocation")
    if (
        not _is_number(total.get("amount"))
        or not _is_number(unallocated.get("amount"))
        or total.get("amount") != payable_amount
        or unallocated.get("amount") != 0
    ):
        raise PurchasePreparationError("The quote payment allocation is incomplete.")
    _validate_single_card_allocation(
        breakdown.get("parts"), payment_method, payable_amount
    )
    if (
        validation_copy.get("credits_amount", _MISSING) is not None
        or validation_copy.get("use_token", _MISSING) is not None
        or validation_copy.get("wolt_loyalty_currency_amount_converted", _MISSING)
        is not None
        or validation_copy.get("discounts") != []
        or validation_copy.get("offers") != []
        or validation_copy.get("surcharges") != []
    ):
        raise PurchasePreparationError("The quote uses an unsupported payment flow.")
    return validation_copy, payable_amount


def _validate_single_card_allocation(
    parts: Any, payment_method: Mapping[str, Any], payable_amount: int | float
) -> None:
    if not _is_array(parts) or len(parts) != 1:
        raise PurchasePreparationError("The quote payment allocation is incomplete.")
    part = _copy_mapping(parts[0], "payment allocation")
    amount = _copy_mapping(part.get("amount"), "payment allocation amount")
    method = _copy_mapping(part.get("payment_method"), "payment allocation method")
    if (
        not _is_number(amount.get("amount"))
        or amount.get("amount") != payable_amount
        or method.get("id") != payment_method.get("id")
        or method.get("type") != payment_method.get("type")
    ):
        raise PurchasePreparationError("The quote payment allocation is incomplete.")


def _normalize_context(context: PurchaseContext) -> dict[str, Any]:
    if (
        not _is_text(context.client_nonce)
        or not _is_text(context.ravelin_device_id)
        or not _is_text(context.language)
        or not isinstance(context.client_pre_estimate, str)
        or not _is_text(context.signature)
        or not _is_integer(context.pricing_model_version)
        or not _is_text(context.device_channel)
        or not _is_text(context.menu_items_source)
        or not isinstance(context.use_self_service_cancellation, bool)
    ):
        raise PurchasePreparationError(
            "Required caller-supplied purchase inputs are absent."
        )
    signature_datetime = _copy_mapping(context.signature_datetime, "signature_datetime")
    if not _is_integer(signature_datetime.get("$date")):
        raise PurchasePreparationError("signature_datetime must be caller supplied.")
    browser_info = _copy_mapping(context.browser_info, "browser_info")
    payment_links = _copy_mapping(context.payment_links, "payment_links")
    delivery_info_options = _copy_mapping(
        context.delivery_info_options, "delivery_info_options"
    )
    additional_checkout_options = _copy_mapping(
        context.additional_checkout_options, "additional_checkout_options"
    )
    if (
        set(delivery_info_options) != {"use_last_100m_address_picker"}
        or not isinstance(delivery_info_options["use_last_100m_address_picker"], bool)
        or set(additional_checkout_options) != {"no_contact_delivery"}
        or not isinstance(additional_checkout_options["no_contact_delivery"], bool)
    ):
        raise PurchasePreparationError("Caller checkout options are unsupported.")
    optional_fields = _copy_mapping(context.optional_fields, "optional_fields")
    if set(optional_fields).difference({"consumer_comment", "corporate_order_comment"}):
        raise PurchasePreparationError(
            "Unsupported optional purchase fields were supplied."
        )
    if not _is_array(context.purchase_items) or not context.purchase_items:
        raise PurchasePreparationError(
            "Exact caller-supplied purchase items are required."
        )
    return {
        "client_nonce": context.client_nonce,
        "ravelin_device_id": context.ravelin_device_id,
        "signature_datetime": signature_datetime,
        "language": context.language,
        "client_pre_estimate": context.client_pre_estimate,
        "signature": context.signature,
        "pricing_model_version": context.pricing_model_version,
        "device_channel": context.device_channel,
        "browser_info": browser_info,
        "payment_links": payment_links,
        "delivery_info_options": delivery_info_options,
        "additional_checkout_options": additional_checkout_options,
        "menu_items_source": context.menu_items_source,
        "use_self_service_cancellation": context.use_self_service_cancellation,
        "purchase_items": list(context.purchase_items),
        "optional_fields": optional_fields,
    }


def _validate_purchase_items(
    supplied_items: Sequence[Mapping[str, Any]],
    plan_items: Sequence[Mapping[str, Any]],
    selected_items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if len(supplied_items) != len(plan_items) or len(plan_items) != len(selected_items):
        raise PurchasePreparationError(
            "Purchase items do not match the retained selection."
        )
    validated: list[dict[str, Any]] = []
    for supplied, plan_item, selected_item in zip(
        supplied_items, plan_items, selected_items, strict=True
    ):
        item = _copy_mapping(supplied, "purchase item")
        _validate_purchase_item(item, plan_item, selected_item)
        validated.append(item)
    return validated


def _validate_purchase_item(
    item: Mapping[str, Any],
    plan_item: Mapping[str, Any],
    selected_item: Mapping[str, Any],
) -> None:
    required = {
        "id",
        "configIndex",
        "count",
        "name",
        "exclude_from_credits",
        "exclude_from_discounts_min_basket",
        "from_recommendation",
        "alcohol_percentage",
        "product_hierarchy_tags",
        "vat_percentage",
        "vat_percentage_decimal",
        "baseprice",
        "end_amount",
        "restrictions",
        "options",
        "checksum",
    }
    if (
        required.difference(item)
        or {"category", "exclude_from_discounts"}.intersection(item)
        or item.get("id") != plan_item.get("id")
        or not _is_integer(item.get("count"))
        or item.get("count") != plan_item.get("count")
        or not _is_integer(item.get("baseprice"))
        or item.get("baseprice") != plan_item.get("base_price")
        or not _is_integer(item.get("end_amount"))
        or item.get("end_amount") != plan_item.get("end_amount")
        or item.get("checksum") != selected_item.get("checksum")
        or item.get("restrictions") != plan_item.get("restrictions")
        or item.get("exclude_from_credits") != plan_item.get("exclude_from_credits")
        or item.get("exclude_from_discounts_min_basket")
        != plan_item.get("exclude_from_discounts_min_basket")
        or not isinstance(item.get("exclude_from_credits"), bool)
        or not isinstance(item.get("exclude_from_discounts_min_basket"), bool)
        or not _is_integer(item.get("configIndex"))
        or not _is_array(item.get("name"))
        or not _is_array(item.get("product_hierarchy_tags"))
        or item.get("from_recommendation") is not False
        or not _is_number(item.get("alcohol_percentage"))
        or item.get("alcohol_percentage") != 0
        or not _is_array(item.get("restrictions"))
        or item.get("restrictions")
    ):
        raise PurchasePreparationError(
            "Caller purchase items do not match the safe scope."
        )
    post_fields = selected_item.get("post_checkout_fields")
    if (
        not isinstance(post_fields, Mapping)
        or item["configIndex"] != post_fields.get("configIndex")
        or item["name"] != post_fields.get("name")
        or item["from_recommendation"] != post_fields.get("from_recommendation")
        or item["alcohol_percentage"] != post_fields.get("alcohol_percentage")
        or item["product_hierarchy_tags"] != post_fields.get("product_hierarchy_tags")
        or item["vat_percentage"] != post_fields.get("vat_percentage")
        or item["vat_percentage_decimal"] != post_fields.get("vat_percentage_decimal")
    ):
        raise PurchasePreparationError(
            "Purchase item fields do not match the selection."
        )
    _validate_purchase_options(
        item["options"], plan_item.get("options"), selected_item.get("options")
    )


def _validate_purchase_options(
    supplied_options: Any,
    plan_options: Any,
    selected_options: Any,
) -> None:
    if (
        not _is_array(supplied_options)
        or not _is_array(plan_options)
        or not _is_array(selected_options)
        or len(supplied_options) != len(plan_options)
        or len(plan_options) != len(selected_options)
    ):
        raise PurchasePreparationError(
            "Purchase option fields do not match the selection."
        )
    for supplied, plan_option, selected_option in zip(
        supplied_options, plan_options, selected_options, strict=True
    ):
        option = _copy_mapping(supplied, "purchase option")
        if (
            set(option) != {"type", "id", "name", "values"}
            or option.get("id") != plan_option.get("id")
            or not _is_text(option.get("type"))
            or not _is_array(option.get("name"))
            or not _is_array(option.get("values"))
        ):
            raise PurchasePreparationError(
                "Purchase option fields do not match the selection."
            )
        post_fields = selected_option.get("post_checkout_fields")
        if (
            not isinstance(post_fields, Mapping)
            or option["type"] != post_fields.get("type")
            or option["name"] != post_fields.get("name")
        ):
            raise PurchasePreparationError(
                "Purchase option fields do not match the selection."
            )
        _validate_purchase_option_values(option["values"], plan_option.get("values"))


def _validate_purchase_option_values(supplied_values: Any, plan_values: Any) -> None:
    if (
        not _is_array(supplied_values)
        or not _is_array(plan_values)
        or len(supplied_values) != len(plan_values)
    ):
        raise PurchasePreparationError(
            "Purchase option values do not match the checkout plan."
        )
    for supplied, plan_value in zip(supplied_values, plan_values, strict=True):
        value = _copy_mapping(supplied, "purchase option value")
        if (
            set(value) != {"count", "name", "price", "id"}
            or value.get("id") != plan_value.get("id")
            or not _is_integer(value.get("count"))
            or value.get("count") != plan_value.get("count")
            or not _is_integer(value.get("price"))
            or value.get("price") != plan_value.get("price")
            or not _is_array(value.get("name"))
        ):
            raise PurchasePreparationError(
                "Purchase option values do not match the checkout plan."
            )


def _purchase_line_summary(item: Mapping[str, Any]) -> PurchaseLineSummary:
    return PurchaseLineSummary(
        item_id=item["id"],
        count=item["count"],
        names=_purchase_name_summaries(item["name"], "purchase item names"),
        options=tuple(_purchase_option_summary(option) for option in item["options"]),
    )


def _purchase_option_summary(option: Mapping[str, Any]) -> PurchaseOptionSummary:
    return PurchaseOptionSummary(
        option_id=option["id"],
        names=_purchase_name_summaries(option["name"], "purchase option names"),
        values=tuple(
            PurchaseOptionValueSummary(
                value_id=value["id"],
                count=value["count"],
                names=_purchase_name_summaries(
                    value["name"], "purchase option value names"
                ),
            )
            for value in option["values"]
        ),
    )


def _purchase_name_summaries(names: Any, label: str) -> tuple[PurchaseNameSummary, ...]:
    if not _is_array(names) or not names:
        raise PurchasePreparationError(f"{label} are incomplete.")
    summaries: list[PurchaseNameSummary] = []
    for record in names:
        name = _copy_mapping(record, label)
        value = name.get("value")
        language = name.get("lang")
        if not _is_text(value) or not _is_text(language):
            raise PurchasePreparationError(f"{label} are incomplete.")
        summaries.append(PurchaseNameSummary(value=value, language=language))
    return tuple(summaries)


def _summary_binding_data(summary: PurchaseConfirmationSummary) -> dict[str, Any]:
    return {
        "checkout_id": summary.checkout_id,
        "venue_id": summary.venue_id,
        "delivery_info_id": summary.delivery_info_id,
        "payment_method_id": summary.payment_method_id,
        "courier_tip": summary.courier_tip,
        "payable_amount": summary.payable_amount,
        "end_amount": summary.end_amount,
        "items": [_line_summary_binding_data(item) for item in summary.items],
    }


def _line_summary_binding_data(item: PurchaseLineSummary) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "count": item.count,
        "names": [_name_summary_binding_data(name) for name in item.names],
        "options": [_option_summary_binding_data(option) for option in item.options],
    }


def _option_summary_binding_data(option: PurchaseOptionSummary) -> dict[str, Any]:
    return {
        "option_id": option.option_id,
        "names": [_name_summary_binding_data(name) for name in option.names],
        "values": [
            {
                "value_id": value.value_id,
                "count": value.count,
                "names": [_name_summary_binding_data(name) for name in value.names],
            }
            for value in option.values
        ],
    }


def _name_summary_binding_data(name: PurchaseNameSummary) -> dict[str, str]:
    return {"value": name.value, "language": name.language}


def _parse_purchase_result(response: Mapping[str, Any]) -> PurchaseResult:
    results = response.get("results")
    if not isinstance(results, Mapping):
        raise PurchasePreparationError(
            "Purchase response did not establish an order ID."
        )
    purchase_id = _extract_purchase_id(response)
    fields = (
        purchase_id,
        results.get("status"),
        results.get("currency"),
        results.get("amount"),
        results.get("delivery_method"),
        results.get("delivery_price"),
        results.get("payment_method_type"),
    )
    if (
        not _is_text(fields[0])
        or fields[1] != "received"
        or not _is_text(fields[2])
        or not _is_number(fields[3])
        or fields[4] != "homedelivery"
        or not _is_number(fields[5])
        or fields[6] != "card"
    ):
        raise PurchasePreparationError(
            "Purchase response did not establish an order ID."
        )
    return PurchaseResult(
        purchase_id=fields[0],
        status=fields[1],
        currency=fields[2],
        amount=fields[3],
        delivery_method=fields[4],
        delivery_price=fields[5],
        payment_method_type=fields[6],
    )


def _extract_purchase_id(response: Mapping[str, Any]) -> str | None:
    results = response.get("results")
    if not isinstance(results, Mapping):
        return None
    identifier = results.get("id")
    if not isinstance(identifier, Mapping):
        return None
    purchase_id = identifier.get("$oid")
    return purchase_id if _is_text(purchase_id) else None


def _mark_unknown_without_masking(
    store: PurchaseAttemptStore, attempt_key: str, purchase_id: str | None
) -> None:
    try:
        store.mark_unknown(attempt_key, purchase_id)
    except PurchaseAttemptStoreError:
        pass


def _payload_digest(payload: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise PurchasePreparationError(
            "Caller purchase inputs cannot be safely serialized."
        ) from None
    return hashlib.sha256(encoded).hexdigest()


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _copy_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PurchasePreparationError(f"{label} must be caller supplied.")
    return deepcopy(dict(value))


def _is_array(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _normalize_reference_ids(value: Any, label: str) -> frozenset[str]:
    if not isinstance(value, (set, frozenset)) or not all(
        _is_text(item) for item in value
    ):
        raise PurchasePreparationError(f"Current {label} are required.")
    return frozenset(value)


_MISSING = object()
