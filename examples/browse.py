#!/usr/bin/env python3
"""Show recent orders, search nearby venues, and display one venue's menu.

Read-only: no basket saves, quotes, payment requests, or purchases.
"""

import argparse
import getpass
import math
import os
import tempfile
from pathlib import Path

from woltapi import HTTPStatusError, RefreshTokenCredentials, WoltApiError, WoltClient


def text(value, language="en"):
    """Display known text shapes, never arbitrary response dictionaries."""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        records = [v for v in value if isinstance(v, dict)]
        for record in records:
            if record.get("lang") == language and isinstance(record.get("value"), str):
                return text(record["value"], language)
        for record in records:
            if isinstance(record.get("value"), str):
                return text(record["value"], language)
    return "(not provided)"


def browse(client, args):
    print("RECENT ORDERS (server-provided order)")
    page = client.get_orders_page()
    orders = page.get("orders")
    if not isinstance(orders, list):
        raise ValueError("Unexpected order-history shape")
    if not orders:
        print("No orders on the returned page.")
    for index, order in enumerate(orders[: args.orders], 1):
        if not isinstance(order, dict):
            raise ValueError("Unexpected order shape")
        venue = order.get("venue")
        venue = venue if isinstance(venue, dict) else {}
        print(
            f"{index}. {text(venue.get('name'), args.language)}"
            f" | status: {text(order.get('status'), args.language)}"
        )
        items = order.get("items")
        if isinstance(items, list):
            for item in items[:10]:
                if isinstance(item, dict):
                    count = item.get("count")
                    prefix = f"{count} x " if type(count) is int else ""
                    print(f"   {prefix}{text(item.get('name'), args.language)}")
            if len(items) > 10:
                print(f"   ... {len(items) - 10} more items")

    print(f"\nRESTAURANT SEARCH: {args.query}")
    venues = client.search_venues(args.query, args.latitude, args.longitude)
    if not venues:
        print("No venues found. Try another query or location.")
        return
    for index, venue in enumerate(venues[:10], 1):
        print(f"{index}. {text(venue.title or venue.slug)}")
    if args.venue_index > len(venues):
        raise ValueError("Venue index exceeds result count")
    venue = venues[args.venue_index - 1]
    print(f"\nMENU: {text(venue.title or venue.slug)}")
    print("Menu prices (converted from cents):")
    assortment = client.get_assortment(venue.slug)
    items = assortment.get("items")
    if not isinstance(items, list):
        raise ValueError("Unexpected assortment shape")
    if not items:
        print("No menu items returned.")
    for item in items[: args.menu_limit]:
        if not isinstance(item, dict):
            raise ValueError("Unexpected menu item shape")
        amount = item.get("price")
        price = (
            f"{'-' if amount < 0 else ''}{abs(amount) // 100}.{abs(amount) % 100:02d}"
            if type(amount) is int
            else "unavailable"
        )
        print(
            f"- {text(item.get('name'), args.language)} | {price} {venue.currency or ''}"
        )
    if len(items) > args.menu_limit:
        print(
            f"Showing {args.menu_limit} of {len(items)} items; increase --menu-limit for more."
        )


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be positive")
    return number


def check_token_file(parser, token_file):
    """Fail early on an unusable token path, before any secret is typed."""
    if token_file.exists():
        if not token_file.is_file():
            parser.error("--token-file must name a file.")
    elif not token_file.parent.is_dir():
        parser.error("The folder for --token-file does not exist.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--query", default="pizza")
    parser.add_argument("--orders", type=positive_int, default=5)
    parser.add_argument("--menu-limit", type=positive_int, default=20)
    parser.add_argument("--venue-index", type=positive_int, default=1)
    parser.add_argument("--language", default="en")
    args = parser.parse_args()
    if not (
        math.isfinite(args.latitude)
        and -90 <= args.latitude <= 90
        and math.isfinite(args.longitude)
        and -180 <= args.longitude <= 180
    ):
        parser.error("Provide finite latitude [-90, 90] and longitude [-180, 180].")
    check_token_file(parser, args.token_file)
    try:
        client = WoltClient(refresh_credentials(args.language, args.token_file))
        browse(client, args)
    except TokenFileError:
        print(
            "\nCould not save the refresh token to the token file."
            " Wolt may have replaced it, so the stored value can be dead."
            " Put a current __wrtoken value in the file."
        )
        return 1
    except HTTPStatusError as exc:
        print(
            f"\nRequest failed: {exc.service} HTTP {exc.status_code}. No retry was sent."
        )
        if exc.status_code == 401:
            print(
                "The refresh token may be expired or revoked."
                " Put a current __wrtoken value in the token file."
            )
        return 1
    except (EOFError, OSError, UnicodeError, ValueError, WoltApiError) as exc:
        print(f"\nBrowse failed: {type(exc).__name__}. Private error details omitted.")
        return 1
    return 0


class TokenFileError(OSError):
    """The refresh token could not be saved, so the stored value may be dead."""


def write_refresh_token(token_file, token):
    """Atomically persist a Wolt consumer refresh token with private permissions."""
    temporary_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=token_file.parent,
        prefix=f".{token_file.name}.",
        delete=False,
    )
    temporary_path = Path(temporary_file.name)
    replaced = False
    try:
        with temporary_file:
            os.chmod(temporary_path, 0o600)
            temporary_file.write(token)
        os.replace(temporary_path, token_file)
        replaced = True
    except OSError:
        raise TokenFileError from None
    finally:
        # Any failure, including KeyboardInterrupt, must not leave the token
        # behind in a stray temporary file.
        if not replaced:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def read_refresh_token(token_file):
    """Read the Wolt consumer refresh token (the __wrtoken cookie value)."""
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = ""
    if not token:
        token = getpass.getpass("Wolt refresh token (hidden): ").strip()
        if not token:
            raise ValueError("A nonempty refresh token is required.")
        write_refresh_token(token_file, token)
    return token


def refresh_credentials(language, token_file):
    """Build auto-refreshing credentials from a token file or hidden prompt."""
    token = read_refresh_token(token_file)

    headers = {"app-language": language}
    return RefreshTokenCredentials(
        token,
        on_refresh=lambda current_token: write_refresh_token(token_file, current_token),
        restaurant_headers=headers,
        consumer_headers=headers,
        payment_headers=headers,
    )


if __name__ == "__main__":
    raise SystemExit(main())
