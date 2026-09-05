from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
import unittest
from typing import Any
from urllib.parse import urlsplit

from woltapi import (
    DuplicatePurchaseAttempt,
    OrderOutcomeUnknown,
    PurchaseAttemptStore,
    PurchaseAuthorizationError,
    PurchaseContext,
    PurchasePreparationError,
    PreparedOrder,
    SessionCredentials,
    WoltClient,
    WoltTransport,
    authorize_prepared_order,
)

from tests.test_selection import FakeResponse, make_discovered_client, selection_inputs


def purchase_context(*, client_nonce: str = "synthetic-nonce") -> PurchaseContext:
    """Build explicit, synthetic fields that the evidence does not derive."""

    return PurchaseContext(
        client_nonce=client_nonce,
        ravelin_device_id="synthetic-ravelin-device",
        signature_datetime={"$date": 1_700_000_000_000},
        language="en",
        client_pre_estimate="10-20",
        signature="N/A",
        pricing_model_version=2023,
        device_channel="browser",
        browser_info={"language": "en", "user_agent": "synthetic-browser"},
        payment_links={"return_url": "https://example.invalid/return"},
        delivery_info_options={"use_last_100m_address_picker": False},
        additional_checkout_options={"no_contact_delivery": False},
        menu_items_source="consumer-assortment",
        use_self_service_cancellation=False,
        purchase_items=[
            {
                "id": "item-1",
                "configIndex": 7,
                "count": 2,
                "name": [{"value": "Synthetic item", "lang": "en"}],
                "exclude_from_credits": False,
                "exclude_from_discounts_min_basket": False,
                "from_recommendation": False,
                "alcohol_percentage": 0,
                "product_hierarchy_tags": [],
                "vat_percentage": 0,
                "vat_percentage_decimal": "0",
                "baseprice": 2000,
                "end_amount": 2700,
                "restrictions": [],
                "options": [
                    {
                        "type": "Choice",
                        "id": "item-config-1",
                        "name": [{"value": "Synthetic option", "lang": "en"}],
                        "values": [
                            {
                                "count": 3,
                                "name": [
                                    {
                                        "value": "Synthetic option value",
                                        "lang": "en",
                                    }
                                ],
                                "price": 700,
                                "id": "value-1",
                            }
                        ],
                    }
                ],
                "checksum": "catalog-checksum-1",
            }
        ],
        optional_fields={"consumer_comment": "synthetic-private-comment"},
    )


def refreshed_purchase_context() -> PurchaseContext:
    """Use new caller-generated values while retaining the same checkout scope."""

    return replace(
        purchase_context(),
        client_nonce="synthetic-nonce-reprepared",
        signature_datetime={"$date": 1_700_000_001_000},
        browser_info={
            "language": "en",
            "user_agent": "synthetic-browser-reprepared",
        },
        optional_fields={"consumer_comment": "synthetic-reprepared-private-comment"},
    )


def quote_response() -> dict[str, Any]:
    return {
        "id": "checkout-1",
        "payable_amount": 3100,
        "purchase_validation": {
            "end_amount": 2700,
            "delivery_price": 400,
            "credits_amount": None,
            "use_token": None,
            "wolt_loyalty_currency_amount_converted": None,
            "discounts": [],
            "offers": [],
            "surcharges": [],
            "server_extension": {"must_remain": "unchanged"},
        },
        "payment_breakdown": {
            "total": {"amount": 3100},
            "unallocated": {"amount": 0},
            "parts": [
                {
                    "amount": {"amount": 3100},
                    "payment_method": {"id": "card-1", "type": "card"},
                }
            ],
        },
        "purchasing_disabled": None,
        "is_age_verification_required": False,
        "use_address_matching_for_age_verification": False,
    }


def purchase_response(*, purchase_id: str = "purchase-1") -> dict[str, Any]:
    return {
        "results": {
            "id": {"$oid": purchase_id},
            "status": "received",
            "currency": "EUR",
            "amount": 3100,
            "delivery_method": "homedelivery",
            "delivery_price": 400,
            "payment_method_type": "card",
        }
    }


def make_purchase_client(opener: Any) -> WoltClient:
    credentials = SessionCredentials(
        restaurant_headers={"X-Restaurant-Session": "synthetic-restaurant"},
        consumer_headers={"X-Consumer-Session": "synthetic-consumer"},
        payment_headers={"X-Payment-Session": "synthetic-payment"},
    )
    return WoltClient(
        credentials,
        _transport=WoltTransport(credentials, _opener=opener),
    )


class OneResponseOpener:
    def __init__(self, response: FakeResponse | BaseException) -> None:
        self._response = response
        self.requests: list[Any] = []

    def open(
        self, request: Any, data: bytes | None = None, timeout: float = 0
    ) -> FakeResponse:
        self.requests.append(request)
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response


class BlockingPurchaseOpener:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.entered = Event()
        self.release = Event()
        self.requests: list[Any] = []
        self._lock = Lock()

    def open(
        self, request: Any, data: bytes | None = None, timeout: float = 0
    ) -> FakeResponse:
        with self._lock:
            self.requests.append(request)
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("synthetic blocking timeout")
        return self._response


class PurchaseTests(unittest.TestCase):
    def _captured_workflow(
        self,
        *,
        quote_payload: dict[str, Any] | None = None,
        required_consents: list[Any] | None = None,
        purchase_reply: FakeResponse | None = None,
        extra_responses: tuple[FakeResponse, ...] = (),
    ) -> tuple[Any, ...]:
        responses = [
            FakeResponse(quote_payload or quote_response()),
            FakeResponse({"required_consents": required_consents or []}),
        ]
        if purchase_reply is not None:
            responses.append(purchase_reply)
        responses.extend(extra_responses)
        client, opener = make_discovered_client(*responses)
        assortment, venue, delivery, payment_method, item = selection_inputs()
        selection = client.create_selection(
            assortment,
            venue=venue,
            delivery=delivery,
            payment_method=payment_method,
            courier_tip=0,
            items=[item],
        )
        quote = client.quote_checkout(selection)
        consents = client.get_post_checkout_config(selection)
        return client, opener, selection, quote, consents, item

    def _prepared_workflow(self) -> tuple[Any, ...]:
        client, opener, selection, quote, consents, _ = self._captured_workflow()
        prepared = client.prepare_purchase(
            selection,
            quote,
            consents,
            purchase_context(),
        )
        return client, opener, prepared

    def test_prepares_submits_once_and_preserves_quote_joins(self) -> None:
        client, opener, selection, quote, consents, _ = self._captured_workflow(
            purchase_reply=FakeResponse(purchase_response())
        )
        prepared = client.prepare_purchase(
            selection,
            quote,
            consents,
            purchase_context(),
        )
        summary = prepared.confirmation_summary
        authorization = authorize_prepared_order(
            prepared, confirmation_id="caller-confirmation-1"
        )

        with TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory) / "attempts.sqlite3"
            store = PurchaseAttemptStore(store_path)
            result = client.submit_prepared_order(store, prepared, authorization)

            self.assertEqual(result.purchase_id, "purchase-1")
            self.assertEqual(result.status, "received")
            self.assertEqual(summary.checkout_id, "checkout-1")
            self.assertEqual(summary.payable_amount, 3100)
            self.assertEqual(summary.end_amount, 2700)
            self.assertEqual(summary.delivery_info_id, "delivery-1")
            self.assertEqual(summary.payment_method_id, "card-1")
            self.assertEqual(summary.items[0].item_id, "item-1")
            self.assertEqual(summary.items[0].count, 2)
            self.assertEqual(summary.items[0].names[0].value, "Synthetic item")
            self.assertEqual(summary.items[0].names[0].language, "en")
            option_summary = summary.items[0].options[0]
            self.assertEqual(option_summary.option_id, "item-config-1")
            self.assertEqual(option_summary.names[0].value, "Synthetic option")
            self.assertEqual(option_summary.values[0].value_id, "value-1")
            self.assertEqual(option_summary.values[0].count, 3)
            self.assertEqual(
                option_summary.values[0].names[0].value,
                "Synthetic option value",
            )

            request = opener.requests[-1]
            route = urlsplit(request.full_url)
            self.assertEqual(route.netloc, "restaurant-api.wolt.com")
            self.assertEqual(route.path, "/v2/purchases")
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(payload["checkout_id"], quote.checkout_id)
            self.assertEqual(payload["venue_id"], "venue-1")
            self.assertEqual(payload["payment_method_id"], "card-1")
            self.assertEqual(
                payload["delivery_info"],
                {"id": {"$oid": "delivery-1"}, "use_last_100m_address_picker": False},
            )
            self.assertEqual(payload["end_amount"], 2700)
            self.assertNotEqual(payload["end_amount"], quote.payable_amount)
            self.assertEqual(payload["delivery_price"], 400)
            self.assertEqual(
                payload["price_shadowing"], quote_response()["purchase_validation"]
            )
            self.assertEqual(
                payload["items"][0]["options"][0]["values"][0]["id"], "value-1"
            )
            self.assertNotIn("category", payload["items"][0])
            self.assertNotIn("exclude_from_discounts", payload["items"][0])

            self.assertEqual(store_path.stat().st_mode & 0o777, 0o600)
            database_contents = store_path.read_bytes()
            for sensitive_value in (
                b"synthetic-restaurant",
                b"synthetic-ravelin-device",
                b"synthetic-private-comment",
                b"checkout-1",
            ):
                self.assertNotIn(sensitive_value, database_contents)

            reauthorization = authorize_prepared_order(
                prepared, confirmation_id="caller-confirmation-2"
            )
            with self.assertRaises(DuplicatePurchaseAttempt) as repeated:
                client.submit_prepared_order(store, prepared, reauthorization)
            self.assertEqual(repeated.exception.state, "succeeded")
            self.assertEqual(repeated.exception.purchase_id, "purchase-1")

            (
                restarted_client,
                restarted_opener,
                restarted_selection,
                restarted_quote,
                restarted_consents,
                _,
            ) = self._captured_workflow(
                purchase_reply=FakeResponse(purchase_response())
            )
            recreated_prepared = restarted_client.prepare_purchase(
                restarted_selection,
                restarted_quote,
                restarted_consents,
                refreshed_purchase_context(),
            )
            self.assertEqual(
                recreated_prepared.confirmation_summary.checkout_id,
                prepared.confirmation_summary.checkout_id,
            )
            restarted_store = PurchaseAttemptStore(store_path)
            with self.assertRaises(PurchaseAuthorizationError):
                restarted_client.submit_prepared_order(
                    restarted_store,
                    recreated_prepared,
                    authorization,
                )
            recreated_authorization = authorize_prepared_order(
                recreated_prepared,
                confirmation_id="caller-confirmation-after-restart",
            )
            with self.assertRaises(DuplicatePurchaseAttempt) as raised:
                restarted_client.submit_prepared_order(
                    restarted_store,
                    recreated_prepared,
                    recreated_authorization,
                )

        self.assertEqual(raised.exception.state, "succeeded")
        self.assertEqual(raised.exception.purchase_id, "purchase-1")
        self.assertEqual(len(opener.requests), 5)
        self.assertEqual(len(restarted_opener.requests), 4)

    def test_authorization_cannot_be_reused_for_a_different_prepared_order_or_quote(
        self,
    ) -> None:
        client, opener, selection, quote, consents, _ = self._captured_workflow()
        prepared = client.prepare_purchase(
            selection,
            quote,
            consents,
            purchase_context(),
        )
        changed_quote_payload = quote_response()
        changed_quote_payload["payable_amount"] = 3200
        changed_quote_payload["payment_breakdown"]["total"]["amount"] = 3200
        changed_quote_payload["payment_breakdown"]["parts"][0]["amount"]["amount"] = (
            3200
        )
        (
            different_client,
            different_opener,
            different_selection,
            different_quote,
            different_consents,
            _,
        ) = self._captured_workflow(quote_payload=changed_quote_payload)
        different_prepared = different_client.prepare_purchase(
            different_selection,
            different_quote,
            different_consents,
            purchase_context(),
        )
        authorization = authorize_prepared_order(
            prepared, confirmation_id="caller-confirmation-1"
        )

        with TemporaryDirectory() as temporary_directory:
            store = PurchaseAttemptStore(Path(temporary_directory) / "attempts.sqlite3")
            with self.assertRaises(PurchaseAuthorizationError):
                different_client.submit_prepared_order(
                    store, different_prepared, authorization
                )

            prepared._payload["end_amount"] = 1
            with self.assertRaises(PurchaseAuthorizationError):
                client.submit_prepared_order(store, prepared, authorization)

        self.assertEqual(len(opener.requests), 4)
        self.assertEqual(len(different_opener.requests), 4)

    def test_direct_prepared_order_requires_matching_checkout_ids(self) -> None:
        client, _, selection, quote, consents, _ = self._captured_workflow()
        prepared = client.prepare_purchase(
            selection,
            quote,
            consents,
            purchase_context(),
        )
        payload = prepared._payload_snapshot()
        quote_payload = quote._quote_snapshot()
        summary = prepared.confirmation_summary
        checkout_id = summary.checkout_id
        different_checkout_id = f"{checkout_id}-different"
        cases = (
            ("payload", different_checkout_id, checkout_id, checkout_id),
            ("quote", checkout_id, different_checkout_id, checkout_id),
            ("summary", checkout_id, checkout_id, different_checkout_id),
            ("invalid", "", "", ""),
        )

        for label, payload_id, quote_id, summary_id in cases:
            with self.subTest(label=label):
                with self.assertRaises(PurchasePreparationError):
                    PreparedOrder(
                        {**payload, "checkout_id": payload_id},
                        replace(summary, checkout_id=summary_id),
                        {**quote_payload, "id": quote_id},
                    )

    def test_competing_reprepared_checkout_records_one_attempt_before_one_send(
        self,
    ) -> None:
        prepared_client, _, selection, quote, consents, _ = self._captured_workflow()
        prepared = prepared_client.prepare_purchase(
            selection,
            quote,
            consents,
            purchase_context(),
        )
        reprepared = prepared_client.prepare_purchase(
            selection,
            quote,
            consents,
            refreshed_purchase_context(),
        )
        self.assertEqual(
            reprepared.confirmation_summary.checkout_id,
            prepared.confirmation_summary.checkout_id,
        )
        authorization = authorize_prepared_order(
            prepared, confirmation_id="caller-confirmation-1"
        )
        reauthorization = authorize_prepared_order(
            reprepared, confirmation_id="caller-confirmation-2"
        )
        blocking_opener = BlockingPurchaseOpener(FakeResponse(purchase_response()))
        submit_client = make_purchase_client(blocking_opener)
        outcomes: list[tuple[str, Any]] = []

        def submit(prepared_order: Any, order_authorization: Any) -> None:
            try:
                outcomes.append(
                    (
                        "result",
                        submit_client.submit_prepared_order(
                            store,
                            prepared_order,
                            order_authorization,
                        ),
                    )
                )
            except BaseException as error:
                outcomes.append(("error", error))

        with TemporaryDirectory() as temporary_directory:
            store = PurchaseAttemptStore(Path(temporary_directory) / "attempts.sqlite3")
            first = Thread(target=submit, args=(prepared, authorization))
            first.start()
            self.assertTrue(blocking_opener.entered.wait(timeout=2))

            second = Thread(target=submit, args=(reprepared, reauthorization))
            second.start()
            second.join(timeout=2)
            self.assertFalse(second.is_alive())
            self.assertEqual(len(blocking_opener.requests), 1)

            blocking_opener.release.set()
            first.join(timeout=2)
            self.assertFalse(first.is_alive())

        self.assertEqual(len(blocking_opener.requests), 1)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(sum(kind == "result" for kind, _ in outcomes), 1)
        errors = [value for kind, value in outcomes if kind == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], DuplicatePurchaseAttempt)
        self.assertEqual(errors[0].state, "started")

    def test_timeout_marks_the_attempt_unknown_without_a_retry(self) -> None:
        _, _, prepared = self._prepared_workflow()
        authorization = authorize_prepared_order(
            prepared, confirmation_id="caller-confirmation-1"
        )
        opener = OneResponseOpener(TimeoutError("synthetic timeout"))
        client = make_purchase_client(opener)

        with TemporaryDirectory() as temporary_directory:
            store = PurchaseAttemptStore(Path(temporary_directory) / "attempts.sqlite3")
            with self.assertRaises(OrderOutcomeUnknown) as raised:
                client.submit_prepared_order(store, prepared, authorization)
            self.assertIsNone(raised.exception.purchase_id)

            with self.assertRaises(DuplicatePurchaseAttempt) as duplicate:
                client.submit_prepared_order(store, prepared, authorization)

        self.assertEqual(duplicate.exception.state, "unknown")
        self.assertIsNone(duplicate.exception.purchase_id)
        self.assertEqual(len(opener.requests), 1)

    def test_malformed_success_keeps_available_purchase_id_for_recovery(self) -> None:
        _, _, prepared = self._prepared_workflow()
        authorization = authorize_prepared_order(
            prepared, confirmation_id="caller-confirmation-1"
        )
        opener = OneResponseOpener(
            FakeResponse({"results": {"id": {"$oid": "purchase-known"}}})
        )
        client = make_purchase_client(opener)

        with TemporaryDirectory() as temporary_directory:
            store_path = Path(temporary_directory) / "attempts.sqlite3"
            store = PurchaseAttemptStore(store_path)
            with self.assertRaises(OrderOutcomeUnknown) as raised:
                client.submit_prepared_order(store, prepared, authorization)
            self.assertEqual(raised.exception.purchase_id, "purchase-known")

            with self.assertRaises(DuplicatePurchaseAttempt) as duplicate:
                restarted_store = PurchaseAttemptStore(store_path)
                client.submit_prepared_order(restarted_store, prepared, authorization)

        self.assertEqual(duplicate.exception.state, "unknown")
        self.assertEqual(duplicate.exception.purchase_id, "purchase-known")
        self.assertEqual(len(opener.requests), 1)

    def test_unestablished_creation_status_is_unknown_with_the_purchase_id(
        self,
    ) -> None:
        _, _, prepared = self._prepared_workflow()
        authorization = authorize_prepared_order(
            prepared, confirmation_id="caller-confirmation-1"
        )
        reply = purchase_response(purchase_id="purchase-unconfirmed")
        reply["results"]["status"] = "acknowledged"
        opener = OneResponseOpener(FakeResponse(reply))
        client = make_purchase_client(opener)

        with TemporaryDirectory() as temporary_directory:
            store = PurchaseAttemptStore(Path(temporary_directory) / "attempts.sqlite3")
            with self.assertRaises(OrderOutcomeUnknown) as raised:
                client.submit_prepared_order(store, prepared, authorization)
            self.assertEqual(raised.exception.purchase_id, "purchase-unconfirmed")

            with self.assertRaises(DuplicatePurchaseAttempt) as duplicate:
                client.submit_prepared_order(store, prepared, authorization)

        self.assertEqual(duplicate.exception.state, "unknown")
        self.assertEqual(duplicate.exception.purchase_id, "purchase-unconfirmed")
        self.assertEqual(len(opener.requests), 1)

    def test_preparation_blocks_stale_or_unsupported_flows(self) -> None:
        scenarios: list[tuple[str, dict[str, Any], list[Any]]] = []

        incomplete_allocation = quote_response()
        incomplete_allocation["payment_breakdown"]["unallocated"]["amount"] = 1
        scenarios.append(("allocation", incomplete_allocation, []))

        age_verification = quote_response()
        age_verification["is_age_verification_required"] = True
        scenarios.append(("age verification", age_verification, []))

        restricted = quote_response()
        restricted["purchasing_disabled"] = True
        scenarios.append(("purchasing restriction", restricted, []))

        disabled_action = quote_response()
        disabled_action["call_to_action"] = {"enabled": False}
        scenarios.append(("disabled purchase action", disabled_action, []))

        malformed_action = quote_response()
        malformed_action["call_to_action"] = {"enabled": "yes"}
        scenarios.append(("malformed purchase action", malformed_action, []))

        offers = quote_response()
        offers["purchase_validation"]["offers"] = [{"id": "synthetic-offer"}]
        scenarios.append(("offers", offers, []))

        scenarios.append(("consents", quote_response(), [{"type": "synthetic"}]))

        for label, quote_payload, required_consents in scenarios:
            with self.subTest(label=label):
                client, opener, selection, quote, consents, _ = self._captured_workflow(
                    quote_payload=quote_payload,
                    required_consents=required_consents,
                )
                with self.assertRaises(PurchasePreparationError):
                    client.prepare_purchase(
                        selection,
                        quote,
                        consents,
                        purchase_context(),
                    )
                self.assertEqual(len(opener.requests), 4)

        client, _, selection, quote, consents, item = self._captured_workflow()
        assortment, venue, delivery, payment_method, _ = selection_inputs()
        changed_selection = client.create_selection(
            assortment,
            venue=venue,
            delivery=delivery,
            payment_method=payment_method,
            courier_tip=0,
            items=[replace(item, count=1)],
        )
        with self.assertRaises(PurchasePreparationError):
            client.prepare_purchase(
                changed_selection,
                quote,
                consents,
                purchase_context(),
            )

    def test_reloaded_card_eligibility_must_match_the_selection_context(self) -> None:
        payment_reply = FakeResponse(
            {
                "root_element": {
                    "element_type": "list",
                    "children": [
                        {
                            "element_type": "payment-method",
                            "is_enabled": True,
                            "method": {"id": "card-1", "type": "card"},
                        }
                    ],
                }
            }
        )
        client, opener, selection, quote, consents, _ = self._captured_workflow(
            extra_responses=(payment_reply,)
        )
        client.get_payment_methods(
            {
                "venue_id": "venue-1",
                "delivery_method": "homedelivery",
                "items": [
                    {
                        "id": "item-1",
                        "alcohol_permille": 0,
                        "product_hierarchy_tags": [],
                        "vat_percentage": 1,
                        "vat_percentage_decimal": "0",
                    }
                ],
            }
        )

        with self.assertRaises(PurchasePreparationError):
            client.prepare_purchase(selection, quote, consents, purchase_context())

        self.assertEqual(len(opener.requests), 5)


if __name__ == "__main__":
    unittest.main()
