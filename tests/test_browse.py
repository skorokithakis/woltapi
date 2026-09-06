import argparse
import builtins
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


def test_refresh_token_from_environment_does_not_prompt(monkeypatch):
    script = load_script(monkeypatch)
    monkeypatch.setenv("WOLT_REFRESH_TOKEN", " synthetic.token ")

    def unexpected_prompt(*args):
        raise AssertionError("Environment token should prevent prompting")

    monkeypatch.setattr("getpass.getpass", unexpected_prompt)
    assert script["read_refresh_token"]() == "synthetic.token"


def test_refresh_token_prompts_when_environment_missing(monkeypatch):
    script = load_script(monkeypatch)
    monkeypatch.delenv("WOLT_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("getpass.getpass", lambda prompt: " synthetic.token ")
    assert script["read_refresh_token"]() == "synthetic.token"


def test_refresh_token_rejects_empty_prompt(monkeypatch):
    script = load_script(monkeypatch)
    monkeypatch.delenv("WOLT_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("getpass.getpass", lambda prompt: " ")
    with pytest.raises(ValueError):
        script["read_refresh_token"]()


def test_refresh_credentials_uses_environment_token(monkeypatch):
    script = load_script(monkeypatch)
    monkeypatch.setenv("WOLT_REFRESH_TOKEN", "synthetic.token")
    credentials = script["refresh_credentials"]("en")
    assert credentials.refresh_token == "synthetic.token"


def test_rotation_warning_is_silent_for_an_unchanged_token(monkeypatch, capsys):
    script = load_script(monkeypatch)
    warning = script["rotation_warning"]("initial-synthetic-token")

    warning("initial-synthetic-token")

    assert capsys.readouterr().out == ""


def test_rotation_warning_warns_once_for_a_rotated_token(monkeypatch, capsys):
    script = load_script(monkeypatch)
    warning = script["rotation_warning"]("initial-synthetic-token")

    warning("rotated-synthetic-token")
    warning("rotated-synthetic-token")

    assert capsys.readouterr().out == (
        "Note: Wolt replaced your refresh token. This script cannot store it. "
        "If a later run fails, copy __wrtoken again.\n"
    )


def test_rotation_warning_never_prints_tokens(monkeypatch, capsys):
    script = load_script(monkeypatch)
    initial_token = "initial-synthetic-token"
    rotated_token = "rotated-synthetic-token"
    warning = script["rotation_warning"](initial_token)

    warning(rotated_token)

    output = capsys.readouterr().out
    assert initial_token not in output
    assert rotated_token not in output


def test_rotation_warning_retries_after_a_print_failure(monkeypatch, capsys):
    script = load_script(monkeypatch)
    warning = script["rotation_warning"]("initial-synthetic-token")
    original_print = builtins.print
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise BrokenPipeError
        original_print(*args, **kwargs)

    monkeypatch.setattr("builtins.print", fail_once)
    with pytest.raises(BrokenPipeError):
        warning("rotated-synthetic-token")

    warning("rotated-synthetic-token")

    assert attempts == 2
    assert capsys.readouterr().out == (
        "Note: Wolt replaced your refresh token. This script cannot store it. "
        "If a later run fails, copy __wrtoken again.\n"
    )


def test_refresh_credentials_rejects_embedded_whitespace(monkeypatch):
    script = load_script(monkeypatch)
    monkeypatch.setenv("WOLT_REFRESH_TOKEN", "synthetic\ttoken")
    with pytest.raises(ValueError):
        script["refresh_credentials"]("en")
