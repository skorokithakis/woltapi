import argparse
import runpy
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.test_basket import assortment, saved_basket
from tests.test_client import FakeResponse, make_client
from woltapi import SelectionError


def load_example(monkeypatch):
    examples = Path(__file__).resolve().parents[1] / "examples"
    monkeypatch.syspath_prepend(str(examples))
    return runpy.run_path(str(examples / "verify_saved_basket_rebuild.py"))


def test_verify_baskets_rebuilds_with_read_only_requests(monkeypatch, capsys):
    example = load_example(monkeypatch)
    client, opener = make_client(
        FakeResponse({"baskets": [saved_basket()]}), FakeResponse(assortment())
    )
    args = argparse.Namespace(latitude=60.17, longitude=24.94, language="en")

    assert example["verify_baskets"](client, args) is True

    assert capsys.readouterr().out == "PASS venue=pizza-place items=2 total=1630\n"
    assert [
        (request.get_method(), urlsplit(request.full_url).path)
        for request in opener.requests
    ] == [
        ("GET", "/order-xp/web/v1/pages/baskets"),
        (
            "GET",
            "/consumer-api/consumer-assortment/v1/venues/slug/pizza-place/assortment",
        ),
    ]


def test_verify_baskets_reports_zero_baskets_without_extra_requests(
    monkeypatch, capsys
):
    example = load_example(monkeypatch)
    client, opener = make_client(FakeResponse({"baskets": []}))
    args = argparse.Namespace(latitude=60.17, longitude=24.94, language="en")

    assert example["verify_baskets"](client, args) is True

    assert capsys.readouterr().out == "PASS venue=no-saved-baskets items=0 total=0\n"
    assert len(opener.requests) == 1


def test_verify_baskets_omits_private_malformed_page_data(monkeypatch, capsys):
    example = load_example(monkeypatch)
    client, _ = make_client(FakeResponse({"baskets": "PRIVATE BASKET DATA"}))
    args = argparse.Namespace(latitude=60.17, longitude=24.94, language="en")

    assert example["verify_baskets"](client, args) is False

    output = capsys.readouterr().out
    assert output == (
        "FAIL venue=unknown: VerificationInputError: "
        "Saved baskets page has no baskets list.\n"
    )
    assert "PRIVATE" not in output


def test_verify_baskets_omits_private_library_error_message(monkeypatch, capsys):
    example = load_example(monkeypatch)
    args = argparse.Namespace(latitude=60.17, longitude=24.94, language="en")

    class FailingClient:
        def get_baskets_page(self, latitude, longitude):
            return {"baskets": [saved_basket()]}

        def get_assortment(self, slug):
            raise SelectionError("PRIVATE LIBRARY ERROR")

    assert example["verify_baskets"](FailingClient(), args) is False

    output = capsys.readouterr().out
    assert output == "FAIL venue=pizza-place: SelectionError: Details omitted.\n"
    assert "PRIVATE" not in output


@pytest.mark.parametrize(
    "slug", ["pizza place", "pizza\x00place", "Pizza-place", "pizza_place"]
)
def test_verify_baskets_rejects_invalid_venue_slug(monkeypatch, capsys, slug):
    example = load_example(monkeypatch)
    basket = saved_basket()
    basket["venue"]["slug"] = slug
    client, opener = make_client(FakeResponse({"baskets": [basket]}))
    args = argparse.Namespace(latitude=60.17, longitude=24.94, language="en")

    assert example["verify_baskets"](client, args) is False

    assert capsys.readouterr().out == (
        "FAIL venue=unknown: VerificationInputError: "
        "Saved basket venue has no usable slug.\n"
    )
    assert len(opener.requests) == 1
