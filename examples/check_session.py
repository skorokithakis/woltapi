#!/usr/bin/env python3
"""Check saved-delivery access using a local browser 'Copy as cURL' file.

Only the bearer header is extracted. The supplied command is never executed.
"""

import argparse
import shlex
from pathlib import Path

from woltapi import HTTPStatusError, SessionCredentials, WoltApiError, WoltClient


def extract_authorization(command: str) -> str:
    try:
        words = shlex.split(command.replace("\\\n", ""))
    except ValueError:
        raise ValueError("The curl command has invalid quoting.") from None
    if not words or words[0] != "curl":
        raise ValueError("Expected a browser Copy as cURL command.")
    authorizations = []
    for index, word in enumerate(words[:-1]):
        if word not in ("-H", "--header"):
            continue
        name, separator, value = words[index + 1].partition(":")
        if separator and name.strip().lower() == "authorization":
            if "\r" in value or "\n" in value:
                raise ValueError("Invalid authorization header characters.")
            authorizations.append(value.strip())
    if len(authorizations) != 1:
        raise ValueError("Expected exactly one authorization header.")
    parts = authorizations[0].split(" ", 1)
    if (
        len(parts) != 2
        or parts[0].lower() != "bearer"
        or not parts[1]
        or not parts[1].isascii()
        or any(char.isspace() or ord(char) < 33 for char in parts[1])
    ):
        raise ValueError("Expected a valid Bearer authorization header.")
    return "Bearer " + parts[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_file", type=Path)
    args = parser.parse_args()
    try:
        authorization = extract_authorization(args.request_file.read_text())
        client = WoltClient(
            SessionCredentials(restaurant_headers={"Authorization": authorization})
        )
        targets = client.list_delivery_targets()
    except HTTPStatusError as exc:
        print(f"Saved-delivery lookup: HTTP {exc.status_code}.")
        return 1
    except (OSError, UnicodeError, ValueError, WoltApiError) as exc:
        # Do not print exception values: file contents and responses are private.
        print(f"Session check failed: {type(exc).__name__}. No automatic retry.")
        return 1
    print(f"Saved-delivery lookup succeeded. Target count: {len(targets)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
