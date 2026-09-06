import argparse
import runpy
from pathlib import Path

import pytest

from woltapi import Venue

from tests.test_client import FakeOpener, FakeResponse, make_client


def load_script(monkeypatch):
    examples = Path(__file__).resolve().parents[1] / "examples"
    monkeypatch.syspath_prepend(str(examples))
    return runpy.run_path(str(examples / "browse.py"))


def load_order_script(monkeypatch):
    examples = Path(__file__).resolve().parents[1] / "examples"
    monkeypatch.syspath_prepend(str(examples))
    return runpy.run_path(str(examples / "order.py"))


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


def test_refresh_token_file_is_stripped_without_prompt(monkeypatch, tmp_path):
    script = load_script(monkeypatch)
    token_file = tmp_path / "refresh-token"
    token_file.write_text(" synthetic.token \n", encoding="utf-8")

    def unexpected_prompt(*args):
        raise AssertionError("Token file should prevent prompting")

    monkeypatch.setattr("getpass.getpass", unexpected_prompt)
    assert script["read_refresh_token"](token_file) == "synthetic.token"


@pytest.mark.parametrize("contents", [None, " \n"])
def test_refresh_token_file_prompts_when_missing_or_blank(
    monkeypatch, tmp_path, contents
):
    script = load_script(monkeypatch)
    token_file = tmp_path / "refresh-token"
    if contents is not None:
        token_file.write_text(contents, encoding="utf-8")
    monkeypatch.setattr("getpass.getpass", lambda prompt: " synthetic.token ")
    assert script["read_refresh_token"](token_file) == "synthetic.token"
    assert token_file.read_text(encoding="utf-8") == "synthetic.token"
    assert token_file.stat().st_mode & 0o777 == 0o600


def test_refresh_token_file_rejects_empty_prompt(monkeypatch, tmp_path):
    script = load_script(monkeypatch)
    token_file = tmp_path / "refresh-token"
    monkeypatch.setattr("getpass.getpass", lambda prompt: " ")
    with pytest.raises(ValueError):
        script["read_refresh_token"](token_file)


def test_refresh_credentials_rejects_embedded_whitespace(monkeypatch, tmp_path):
    script = load_script(monkeypatch)
    token_file = tmp_path / "refresh-token"
    token_file.write_text("synthetic\ttoken", encoding="utf-8")

    with pytest.raises(ValueError):
        script["refresh_credentials"]("en", token_file)


def test_refresh_credentials_persists_exchanged_token(monkeypatch, tmp_path, capsys):
    script = load_script(monkeypatch)
    token_file = tmp_path / "refresh-token"
    token_file.write_text("initial-synthetic-token", encoding="utf-8")
    opener = FakeOpener(
        [
            FakeResponse(
                {
                    "access_token": "access-synthetic-token",
                    "refresh_token": "rotated-synthetic-token",
                    "expires_in": 120,
                    "token_type": "Bearer",
                }
            )
        ]
    )
    monkeypatch.setattr("woltapi.auth.urlrequest.build_opener", lambda *args: opener)
    original_replace = script["os"].replace

    def replace_in_token_directory(source, target):
        assert Path(source).parent == token_file.parent
        assert Path(target) == token_file
        return original_replace(source, target)

    monkeypatch.setattr(script["os"], "replace", replace_in_token_directory)

    credentials = script["refresh_credentials"]("en", token_file)
    credentials.refresh()

    assert credentials.refresh_token == "rotated-synthetic-token"
    assert token_file.read_text(encoding="utf-8") == "rotated-synthetic-token"
    assert token_file.stat().st_mode & 0o777 == 0o600
    assert capsys.readouterr().out == ""


def test_refresh_credentials_propagates_token_file_write_failure(
    monkeypatch, tmp_path, capsys
):
    script = load_script(monkeypatch)
    initial_token = "initial-synthetic-token"
    rotated_token = "rotated-synthetic-token"
    token_file = tmp_path / "refresh-token"
    token_file.write_text(initial_token, encoding="utf-8")
    opener = FakeOpener(
        [
            FakeResponse(
                {
                    "access_token": "access-synthetic-token",
                    "refresh_token": rotated_token,
                    "expires_in": 120,
                    "token_type": "Bearer",
                }
            )
        ]
    )
    monkeypatch.setattr("woltapi.auth.urlrequest.build_opener", lambda *args: opener)

    def fail_replace(*args):
        raise OSError

    monkeypatch.setattr(script["os"], "replace", fail_replace)
    credentials = script["refresh_credentials"]("en", token_file)

    with pytest.raises(script["TokenFileError"]):
        credentials.refresh()

    assert token_file.read_text(encoding="utf-8") == initial_token
    assert list(tmp_path.iterdir()) == [token_file]
    captured = capsys.readouterr()
    assert initial_token not in captured.out + captured.err
    assert rotated_token not in captured.out + captured.err


def test_write_refresh_token_leaves_no_temporary_file(monkeypatch, tmp_path):
    script = load_script(monkeypatch)
    token_file = tmp_path / "refresh-token"

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(script["os"], "replace", interrupt)

    with pytest.raises(KeyboardInterrupt):
        script["write_refresh_token"](token_file, "synthetic.token")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "loader", [load_script, load_order_script], ids=["browse", "order"]
)
def test_examples_require_token_file(monkeypatch, capsys, loader):
    script = loader(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["example.py", "--latitude", "1", "--longitude", "2"]
    )

    with pytest.raises(SystemExit) as raised:
        script["main"]()

    assert raised.value.code == 2
    assert "--token-file" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("path_kind", "message"),
    [
        ("missing-parent", "The folder for --token-file does not exist."),
        ("directory", "--token-file must name a file."),
    ],
)
@pytest.mark.parametrize(
    "loader", [load_script, load_order_script], ids=["browse", "order"]
)
def test_examples_reject_unusable_token_files(
    monkeypatch, tmp_path, capsys, path_kind, message, loader
):
    script = loader(monkeypatch)
    if path_kind == "missing-parent":
        token_file = tmp_path / "missing" / "refresh-token"
    else:
        token_file = tmp_path / "token-directory"
        token_file.mkdir()
    monkeypatch.setattr(
        "sys.argv",
        [
            "example.py",
            "--latitude",
            "1",
            "--longitude",
            "2",
            "--token-file",
            str(token_file),
        ],
    )

    def unexpected_prompt(*args):
        raise AssertionError("Invalid token paths should fail before prompting")

    monkeypatch.setattr("getpass.getpass", unexpected_prompt)
    with pytest.raises(SystemExit) as raised:
        script["main"]()

    assert raised.value.code == 2
    assert message in capsys.readouterr().err
