import argparse
import runpy
from pathlib import Path

import pytest

from woltapi import Venue

from tests.test_client import FakeResponse, make_client


def load_script(monkeypatch):
    examples = Path(__file__).resolve().parents[1] / "examples"
    monkeypatch.syspath_prepend(str(examples))
    return runpy.run_path(str(examples / "browse.py"))


def test_order_history_route():
    client, opener = make_client(FakeResponse({"orders": []}))
    assert client.get_orders_page() == {"orders": []}
    assert opener.requests[0].get_method() == "GET"
    assert opener.requests[0].full_url == (
        "https://consumer-api.wolt.com/order-xp/web/v1/pages/orders"
    )


@pytest.mark.parametrize(
    ("amount", "display_price"),
    [(1234, "12.34"), (0, "0.00"), (5, "0.05"), (100, "1.00"), (None, "unavailable")],
)
def test_browse_uses_only_reads_and_limits_output(
    monkeypatch, capsys, amount, display_price
):
    script = load_script(monkeypatch)

    class Client:
        def get_orders_page(self):
            return {
                "orders": [
                    {
                        "venue": {"name": "Example restaurant"},
                        "status": "delivered",
                        "items": [{"name": "Old pizza", "count": 2}],
                        "private": "DO NOT DISPLAY",
                    },
                    {"venue": {"name": "Hidden order"}},
                ]
            }

        def search_venues(self, query, latitude, longitude):
            assert (query, latitude, longitude) == ("pizza", 1, 2)
            return (Venue("id", "slug", "Example restaurant", "EUR", True, True),)

        def get_assortment(self, slug):
            assert slug == "slug"
            return {
                "items": [
                    {"name": [{"lang": "en", "value": "Menu pizza"}], "price": amount},
                    {"name": "Hidden item", "price": 100},
                ]
            }

    args = argparse.Namespace(
        orders=1,
        language="en",
        query="pizza",
        latitude=1,
        longitude=2,
        venue_index=1,
        menu_limit=1,
    )
    script["browse"](Client(), args)
    output = capsys.readouterr().out
    for expected in (
        "Example restaurant",
        "delivered",
        "2 x Old pizza",
        "Menu pizza",
        f"{display_price} EUR",
    ):
        assert expected in output
    for private in ("DO NOT DISPLAY", "Hidden order", "Hidden item"):
        assert private not in output


def test_no_search_results_does_not_fetch_menu(monkeypatch, capsys):
    script = load_script(monkeypatch)

    class Client:
        def get_orders_page(self):
            return {"orders": []}

        def search_venues(self, *args):
            return ()

    args = argparse.Namespace(orders=5, query="pizza", latitude=1, longitude=2)
    script["browse"](Client(), args)
    assert "No venues found" in capsys.readouterr().out


def test_auth_from_environment_does_not_prompt(monkeypatch):
    script = load_script(monkeypatch)
    monkeypatch.setenv("WOLT_ACCESS_TOKEN", "Bearer synthetic.token")

    def unexpected_prompt(*args):
        raise AssertionError("Environment token should prevent prompting")

    monkeypatch.setattr("getpass.getpass", unexpected_prompt)
    assert script["access_token"]() == "synthetic.token"


def test_auth_prompts_when_environment_missing(monkeypatch):
    script = load_script(monkeypatch)
    monkeypatch.delenv("WOLT_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr("getpass.getpass", lambda prompt: " synthetic.token ")
    assert script["access_token"]() == "synthetic.token"


def test_auth_rejects_embedded_whitespace(monkeypatch):
    script = load_script(monkeypatch)
    monkeypatch.setenv("WOLT_ACCESS_TOKEN", "synthetic\nheader")
    with pytest.raises(ValueError):
        script["access_token"]()
