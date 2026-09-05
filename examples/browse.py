#!/usr/bin/env python3
"""Show recent orders, search nearby venues, and display one venue's menu.

Read-only: no basket saves, quotes, payment requests, or purchases.
"""

import argparse
import getpass
import math
import os

from woltapi import HTTPStatusError, SessionCredentials, WoltApiError, WoltClient


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--longitude", type=float, required=True)
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
    try:
        headers = {
            "Authorization": "Bearer " + access_token(),
            "app-language": args.language,
        }
        client = WoltClient(
            SessionCredentials(restaurant_headers=headers, consumer_headers=headers)
        )
        browse(client, args)
    except HTTPStatusError as exc:
        print(f"\nRequest failed: HTTP {exc.status_code}. No retry was sent.")
        if exc.status_code == 401:
            print("Supply a current access token from a successful browser request.")
        return 1
    except (EOFError, OSError, UnicodeError, ValueError, WoltApiError) as exc:
        print(f"\nBrowse failed: {type(exc).__name__}. Private error details omitted.")
        return 1
    return 0


def access_token():
    token = os.environ.get("WOLT_ACCESS_TOKEN", "").strip()
    if not token:
        token = getpass.getpass("Wolt access token (hidden): ").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("A nonempty bearer token is required.")
    return token


if __name__ == "__main__":
    raise SystemExit(main())
