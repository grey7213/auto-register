#!/usr/bin/env python3
"""Fetch subscriptions for saved accounts and write importable Clash YAML profiles.

Cross-platform companion to rotate_subscription.py: it reuses that module's
login/download/convert logic but only writes ``.yaml`` files to a local folder,
so you can import them manually into Clash Verge (or any Clash client) on any OS.
It does NOT touch a running Clash instance, so it needs no Unix socket.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import requests

from rotate_subscription import (
    Account,
    download_profile,
    fetch_subscription,
    load_accounts,
)


def safe_stem(email: str) -> str:
    """Turn an email into a filesystem-safe file stem (Windows-safe)."""
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in email)


def export_account(account: Account, output_dir: Path, insecure: bool) -> Path:
    subscription = fetch_subscription(account, insecure)
    profile_content = download_profile(subscription.url, insecure)
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_path = output_dir / f"{safe_stem(account.email)}.yaml"
    profile_path.write_text(profile_content, encoding="utf-8")
    logging.info(
        "%s: %.1f%% used -> %s", account.email, subscription.ratio * 100, profile_path
    )
    logging.info("  subscription url: %s", subscription.url)
    return profile_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", type=Path, default=Path("accounts.jsonl"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("clash_profiles"),
        help="Directory to write generated Clash YAML profiles",
    )
    parser.add_argument(
        "--print-url-only",
        action="store_true",
        help="Only print subscription URLs; do not download or write YAML",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Only process the most recently added account (last line of the file)",
    )
    parser.add_argument(
        "--insecure", action="store_true", help="Disable TLS certificate verification"
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    accounts = load_accounts(arguments.accounts)
    if arguments.latest:
        accounts = accounts[-1:]
    exit_code = 0
    for account in accounts:
        try:
            if arguments.print_url_only:
                subscription = fetch_subscription(account, arguments.insecure)
                print(f"{account.email}\t{subscription.url}")
            else:
                export_account(account, arguments.output_dir, arguments.insecure)
        except (requests.RequestException, RuntimeError, ValueError) as error:
            logging.error("%s: failed (%s)", account.email, error)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
