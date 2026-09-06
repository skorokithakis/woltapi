#!/usr/bin/env python3
"""Verify saved baskets can be rebuilt from their current assortments.

Read-only: this script only reads saved baskets and assortments. Its output is
restricted to venue slugs, item counts, and integer cent totals.
"""

import argparse
import math
import re
from collections.abc import Mapping
from pathlib import Path

from browse import TokenFileError, check_token_file, refresh_credentials

from woltapi import Basket, WoltApiError, WoltClient


class VerificationInputError(ValueError):
    """A local saved-basket shape problem safe to report to the owner."""


def venue_slug(saved_basket):
    """Return the saved basket's public venue slug without exposing other fields."""
    if not isinstance(saved_basket, Mapping):
        raise VerificationInputError("Saved basket is not an object.")
    venue = saved_basket.get("venue")
    if not isinstance(venue, Mapping):
        raise VerificationInputError("Saved basket venue is not an object.")
    slug = venue.get("slug")
    if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug) is None:
        raise VerificationInputError("Saved basket venue has no usable slug.")
    return slug


def failure_line(slug, error):
    """Format only local static error messages, never response-derived data."""
    if isinstance(error, VerificationInputError):
        message = str(error)
    else:
        message = "Details omitted."
    return f"FAIL venue={slug}: {type(error).__name__}: {message}"


def verify_baskets(client, args):
    """Read, rebuild, and report each saved basket; return whether all passed."""
    try:
        page = client.get_baskets_page(args.latitude, args.longitude)
        baskets = page.get("baskets") if isinstance(page, Mapping) else None
        if not isinstance(baskets, list):
            raise VerificationInputError("Saved baskets page has no baskets list.")
    except (VerificationInputError, WoltApiError, TypeError, ValueError) as exc:
        print(failure_line("unknown", exc))
        return False

    if not baskets:
        # Keep the zero-basket note in the same privacy-safe output format.
        print("PASS venue=no-saved-baskets items=0 total=0")
        return True

    passed = True
    for saved_basket in baskets:
        slug = "unknown"
        try:
            slug = venue_slug(saved_basket)
            assortment = client.get_assortment(slug)
            basket = Basket.from_saved_basket(assortment, saved_basket, args.language)
            selections = basket.item_selections()
            item_count = sum(selection.count for selection in selections)
            total = sum(selection.basket_price for selection in selections)
            print(f"PASS venue={slug} items={item_count} total={total}")
        except (VerificationInputError, WoltApiError, TypeError, ValueError) as exc:
            print(failure_line(slug, exc))
            passed = False
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
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
        return 0 if verify_baskets(client, args) else 1
    except TokenFileError:
        print("FAIL venue=unknown: TokenFileError: Could not save the refresh token.")
        return 1
    except (EOFError, KeyboardInterrupt) as exc:
        print(f"FAIL venue=unknown: {type(exc).__name__}: Cancelled.")
        return 1
    except (OSError, UnicodeError, ValueError, WoltApiError) as exc:
        print(failure_line("unknown", exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
