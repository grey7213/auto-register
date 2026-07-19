#!/usr/bin/env python3
"""Rotate sandbox subscriptions in Clash Verge when an account reaches its quota."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import logging
import os
import socket
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import requests
import yaml


API_BASE = "https://ssyun.org/api/v1"
DEFAULT_PROFILE_ROOT = Path.home() / ".local/share/io.github.clash-verge-rev.clash-verge-rev"
DEFAULT_PROFILES_INDEX = DEFAULT_PROFILE_ROOT / "profiles.yaml"
DEFAULT_PROFILES_DIR = DEFAULT_PROFILE_ROOT / "profiles"
DEFAULT_SOCKET = Path("/tmp/verge/verge-mihomo.sock")
DEFAULT_STATE = Path.home() / ".local/state/clash-verge-subscription-rotation.json"
MANAGED_PROFILE_UID = "sandbox-rotating-subscription"
MANAGED_PROFILE_FILE = f"{MANAGED_PROFILE_UID}.yaml"
MANAGED_PROFILE_NAME = "Sandbox Rotating Subscription"


@dataclass
class Account:
    email: str
    password: str


@dataclass
class Subscription:
    account: Account
    url: str
    used: int
    quota: int
    expires_at: int

    @property
    def ratio(self) -> float:
        return self.used / self.quota if self.quota else 1.0

    @property
    def eligible(self) -> bool:
        return self.quota > 0 and (not self.expires_at or self.expires_at > time.time())


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, unix_socket: Path) -> None:
        super().__init__("localhost")
        self.unix_socket = str(unix_socket)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.unix_socket)


def api_request(session: requests.Session, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("proxies", {"http": None, "https": None})
    response = session.request(method, f"{API_BASE}{endpoint}", timeout=20, **kwargs)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(payload.get("message") or "subscription API returned an error")
    return payload["data"]


def fetch_subscription(account: Account, insecure: bool) -> Subscription:
    session = requests.Session()
    session.trust_env = False  # ignore system Clash proxy during control-plane calls
    try:
        from register_account import apply_random_client

        apply_random_client(session)
    except Exception:
        session.headers.setdefault("User-Agent", "ClashVerge/auto-switch")
    verify: bool = not insecure
    login = api_request(session, "POST", "/passport/auth/login", json={"email": account.email, "password": account.password}, verify=verify)
    auth_data = login.get("auth_data")
    if not auth_data:
        raise RuntimeError("login did not return authorization data")
    session.headers["Authorization"] = auth_data
    subscribe = api_request(session, "GET", "/user/getSubscribe", verify=verify)
    url = subscribe.get("subscribe_url")
    if not url:
        raise RuntimeError("account has no subscription URL")
    # Prefer u/d from getSubscribe; some sandboxes return zeros from getStat.
    used = int(subscribe.get("u") or 0) + int(subscribe.get("d") or 0)
    if used == 0:
        try:
            stats = api_request(session, "GET", "/user/getStat", verify=verify)
            if isinstance(stats, list) and len(stats) >= 2:
                used = int(stats[0]) + int(stats[1])
        except (RuntimeError, TypeError, ValueError):
            pass
    return Subscription(
        account=account,
        url=url,
        used=used,
        quota=int(subscribe.get("transfer_enable") or 0),
        expires_at=int(subscribe.get("expired_at") or 0),
    )


def load_accounts(path: Path) -> list[Account]:
    accounts: list[Account] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            accounts.append(Account(email=record["email"], password=record["password"]))
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError(f"invalid account record on line {line_number}") from error
    if not accounts:
        raise ValueError("no accounts found")
    return accounts


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logging.warning("Ignoring invalid state file: %s", path)
        return {}


def save_state(path: Path, subscription: Subscription) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"email": subscription.account.email, "updated_at": datetime.now(timezone.utc).isoformat(), "usage_ratio": subscription.ratio}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
        json.dump(data, output)
        output.write("\n")
        temporary_path = Path(output.name)
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(path)


def choose_account(subscriptions: list[Subscription], current_email: str | None, threshold: float) -> tuple[Subscription | None, bool]:
    eligible = [item for item in subscriptions if item.eligible and item.ratio < threshold]
    if not eligible:
        return None, False
    current = next((item for item in subscriptions if item.account.email == current_email), None)
    if current and current in eligible:
        return current, False
    if current:
        current_index = subscriptions.index(current)
        for offset in range(1, len(subscriptions) + 1):
            candidate = subscriptions[(current_index + offset) % len(subscriptions)]
            if candidate in eligible:
                return candidate, True
    return eligible[0], True


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
        output.write(content)
        temporary_path = Path(output.name)
    temporary_path.replace(path)


def secure_atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
        output.write(content)
        temporary_path = Path(output.name)
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(path)


def save_subscription_backup(backup_dir: Path, subscription: Subscription, profile_content: str) -> Path:
    identifier = hashlib.sha256(subscription.account.email.encode("utf-8")).hexdigest()[:16]
    saved_at = datetime.now(timezone.utc)
    snapshot_id = f"{saved_at.strftime('%Y%m%dT%H%M%S%fZ')}-{identifier}"
    profile_path = backup_dir / f"{snapshot_id}.yaml"
    metadata_path = backup_dir / f"{snapshot_id}.json"
    metadata = {
        "saved_at": saved_at.isoformat(),
        "account_id": identifier,
        "used_bytes": subscription.used,
        "quota_bytes": subscription.quota,
        "usage_ratio": subscription.ratio,
        "expires_at": subscription.expires_at,
        "source_url": subscription.url,
    }
    secure_atomic_write_text(profile_path, profile_content)
    secure_atomic_write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return profile_path


def unique_proxy_name(names: Counter[str]) -> str:
    names["Subscription Node"] += 1
    return f"Subscription Node {names['Subscription Node']}"


def decode_ss_authority(value: str) -> str:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_").decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("invalid Shadowsocks URI credentials") from error


def proxy_from_uri(uri: str, names: Counter[str]) -> dict[str, Any] | None:
    parsed = urlsplit(uri)
    scheme = parsed.scheme.lower()
    if scheme == "ss":
        netloc = parsed.netloc
        if "@" not in netloc:
            decoded = decode_ss_authority(netloc)
            credentials, separator, address = decoded.rpartition("@")
            if not separator:
                raise ValueError("invalid Shadowsocks URI")
        else:
            credentials, address = netloc.rsplit("@", 1)
            credentials = unquote(credentials)
            if ":" not in credentials:
                credentials = decode_ss_authority(credentials)
        method, separator, password = credentials.partition(":")
        host, separator_address, port = address.rpartition(":")
        if not (method and separator and host and separator_address and port.isdigit()):
            raise ValueError("invalid Shadowsocks URI")
        proxy: dict[str, Any] = {
            "name": unique_proxy_name(names),
            "type": "ss",
            "server": host.strip("[]"),
            "port": int(port),
            "cipher": method,
            "password": password,
        }
        plugin = parse_qs(parsed.query).get("plugin", [None])[0]
        if plugin:
            plugin_name, _, plugin_options = unquote(plugin).partition(";")
            proxy["plugin"] = plugin_name
            if plugin_options:
                proxy["plugin-opts"] = dict(
                    option.split("=", 1) if "=" in option else (option, "")
                    for option in plugin_options.split(";")
                    if option
                )
        return proxy
    if scheme == "vless":
        if not (parsed.username and parsed.hostname and parsed.port):
            raise ValueError("invalid VLESS URI")
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        proxy = {
            "name": unique_proxy_name(names),
            "type": "vless",
            "server": parsed.hostname,
            "port": parsed.port,
            "uuid": parsed.username,
            "udp": True,
            "tls": query.get("security", "none") in {"tls", "reality"},
        }
        for source, target in (("encryption", "encryption"), ("flow", "flow"), ("sni", "servername"), ("fp", "client-fingerprint")):
            if query.get(source):
                proxy[target] = query[source]
        if query.get("security") == "reality":
            reality_options = {}
            if query.get("pbk"):
                reality_options["public-key"] = query["pbk"]
            if query.get("sid"):
                reality_options["short-id"] = query["sid"]
            proxy["reality-opts"] = reality_options
        network = query.get("type", "tcp")
        if network in {"ws", "grpc", "http", "h2"}:
            proxy["network"] = "http" if network == "h2" else network
            options: dict[str, Any] = {}
            if query.get("path"):
                options["path"] = query["path"]
            if query.get("host"):
                options["headers"] = {"Host": query["host"]}
            if options:
                proxy[f"{proxy['network']}-opts"] = options
        return proxy
    return None


def uri_subscription_to_yaml(content: str) -> str:
    encoded = "".join(content.split())
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_").decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError("subscription is neither a YAML profile nor a Base64 URI list") from error
    names: Counter[str] = Counter()
    proxies: list[dict[str, Any]] = []
    for uri in decoded.splitlines():
        if not uri.strip():
            continue
        try:
            proxy = proxy_from_uri(uri.strip(), names)
        except ValueError as error:
            raise RuntimeError("subscription contains an invalid supported node URI") from error
        if proxy:
            proxies.append(proxy)
    if not proxies:
        raise RuntimeError("subscription does not contain supported node URIs")
    return yaml.safe_dump(
        {
            "mixed-port": 7890,
            "mode": "rule",
            "log-level": "warning",
            "proxies": proxies,
            "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": [proxy["name"] for proxy in proxies]}],
            "rules": ["MATCH,PROXY"],
        },
        allow_unicode=True,
        sort_keys=False,
    )


def download_profile(subscription_url: str, insecure: bool) -> str:
    response = requests.get(
        subscription_url,
        timeout=30,
        verify=not insecure,
        proxies={"http": None, "https": None},
    )
    response.raise_for_status()
    content = response.text
    try:
        config = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise RuntimeError("subscription did not return a valid YAML profile") from error
    if isinstance(config, dict):
        return content
    return uri_subscription_to_yaml(content)


def write_managed_profile(
    index_path: Path, profiles_dir: Path, subscription: Subscription, profile_content: str
) -> tuple[Path, Any]:
    try:
        index = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    except OSError as error:
        raise RuntimeError(f"cannot read Clash Verge profile index: {index_path}") from error
    if not isinstance(index, dict) or not isinstance(index.get("items"), list):
        raise RuntimeError("Clash Verge profile index has an invalid format")

    previous_current = index.get("current")
    profile_path = profiles_dir / MANAGED_PROFILE_FILE
    atomic_write_text(profile_path, profile_content)
    item = {
        "uid": MANAGED_PROFILE_UID,
        "type": "remote",
        "name": MANAGED_PROFILE_NAME,
        "file": MANAGED_PROFILE_FILE,
        "url": subscription.url,
        "selected": [],
        "extra": {},
        "updated": int(time.time()),
        "option": {"update_interval": 0, "allow_auto_update": False},
    }
    index["items"] = [entry for entry in index["items"] if not isinstance(entry, dict) or entry.get("uid") != MANAGED_PROFILE_UID]
    index["items"].append(item)
    index["current"] = MANAGED_PROFILE_UID
    atomic_write_text(index_path, yaml.safe_dump(index, allow_unicode=True, sort_keys=False))
    return profile_path, previous_current


def restore_previous_profile(index_path: Path, previous_current: Any) -> None:
    try:
        index = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        logging.error("Cannot restore Clash Verge profile index: %s", error)
        return
    if not isinstance(index, dict) or index.get("current") != MANAGED_PROFILE_UID:
        return
    if previous_current is None:
        index.pop("current", None)
    else:
        index["current"] = previous_current
    try:
        atomic_write_text(index_path, yaml.safe_dump(index, allow_unicode=True, sort_keys=False))
    except OSError as error:
        logging.error("Cannot restore Clash Verge profile index: %s", error)


def managed_profile_is_active(index_path: Path, profiles_dir: Path) -> bool:
    try:
        index = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    return (
        isinstance(index, dict)
        and index.get("current") == MANAGED_PROFILE_UID
        and (profiles_dir / MANAGED_PROFILE_FILE).is_file()
    )


def controller_request(unix_socket: Path, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    connection = UnixHTTPConnection(unix_socket)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Mihomo controller returned HTTP {response.status}")
        if not response_body:
            return None
        try:
            return json.loads(response_body)
        except json.JSONDecodeError:
            return response_body.decode("utf-8", errors="replace")
    finally:
        connection.close()


def profile_runtime_names(profile: Path) -> set[str]:
    try:
        config = yaml.safe_load(profile.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RuntimeError("cannot parse generated subscription profile") from error
    if not isinstance(config, dict):
        raise RuntimeError("generated subscription profile is not a configuration mapping")

    names: set[str] = set()
    for section in ("proxies", "proxy-groups"):
        entries = config.get(section, [])
        if not isinstance(entries, list):
            raise RuntimeError(f"generated subscription profile has an invalid {section} section")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"]:
                raise RuntimeError(f"generated subscription profile has an unnamed {section} entry")
            names.add(entry["name"])
    if not names:
        raise RuntimeError("generated subscription profile has no runtime proxy names")
    return names


def wait_for_profile_activation(unix_socket: Path, expected_names: set[str]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        active = controller_request(unix_socket, "GET", "/proxies")
        if isinstance(active, dict):
            proxies = active.get("proxies")
            if isinstance(proxies, dict) and expected_names.issubset(proxies):
                return
        time.sleep(0.25)
    raise RuntimeError("Mihomo did not activate the generated subscription profile")


def apply_to_clash(profile: Path, unix_socket: Path) -> None:
    if not unix_socket.exists():
        raise RuntimeError(f"Mihomo controller socket not found: {unix_socket}")
    expected_names = profile_runtime_names(profile)
    controller_request(unix_socket, "PUT", "/configs?force=true", {"path": str(profile)})
    active = controller_request(unix_socket, "GET", "/configs")
    if not isinstance(active, dict):
        raise RuntimeError("Mihomo controller did not return its active configuration")
    wait_for_profile_activation(unix_socket, expected_names)


def run_once(arguments: argparse.Namespace) -> int:
    state = load_state(arguments.state)
    subscriptions: list[Subscription] = []
    profiles: dict[str, str] = {}
    for account in load_accounts(arguments.accounts):
        try:
            subscription = fetch_subscription(account, arguments.insecure)
            profile_content = download_profile(subscription.url, arguments.insecure)
            subscriptions.append(subscription)
            profiles[account.email] = profile_content
            logging.info("%s: %.1f%% used", account.email, subscription.ratio * 100)
            if not arguments.dry_run:
                try:
                    backup_path = save_subscription_backup(arguments.backup_dir, subscription, profile_content)
                    logging.info("Saved manual-import backup: %s", backup_path)
                except OSError as error:
                    logging.warning("Could not save manual-import backup for %s: %s", account.email, error)
        except (requests.RequestException, RuntimeError, ValueError) as error:
            logging.warning("%s: unavailable (%s)", account.email, error)
    selected, changed = choose_account(subscriptions, state.get("email"), arguments.threshold)
    if not selected:
        logging.error("No eligible subscription is below the %.1f%% threshold", arguments.threshold * 100)
        return 2
    needs_apply = changed or not managed_profile_is_active(arguments.profiles_index, arguments.profiles_dir)
    if not needs_apply:
        logging.info("Keeping %s (%.1f%% used)", selected.account.email, selected.ratio * 100)
        return 0
    action = "Selecting" if changed else "Initializing"
    logging.info("%s %s (%.1f%% used)", action, selected.account.email, selected.ratio * 100)
    if arguments.dry_run:
        return 0
    managed_profile, previous_current = write_managed_profile(
        arguments.profiles_index, arguments.profiles_dir, selected, profiles[selected.account.email]
    )
    try:
        apply_to_clash(managed_profile, arguments.socket)
    except RuntimeError:
        restore_previous_profile(arguments.profiles_index, previous_current)
        raise
    save_state(arguments.state, selected)
    logging.info("Clash Verge now uses the selected subscription")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", type=Path, default=Path("accounts.jsonl"))
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--backup-dir", type=Path, default=Path("subscription_backups"), help="Directory for manual-import subscription backups")
    parser.add_argument("--profiles-index", type=Path, default=DEFAULT_PROFILES_INDEX)
    parser.add_argument("--profiles-dir", type=Path, default=DEFAULT_PROFILES_DIR)
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--threshold", type=float, default=1.0, help="Switch at this used-quota ratio (default: 1.0, or 100%%)")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between checks (default: 300)")
    parser.add_argument("--once", action="store_true", help="Check once and exit")
    parser.add_argument("--dry-run", action="store_true", help="Do not change Clash Verge or write state")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    args = parser.parse_args()
    if not 0 < args.threshold <= 1:
        parser.error("--threshold must be greater than 0 and at most 1")
    if args.interval < 1:
        parser.error("--interval must be positive")
    return args


def main() -> int:
    arguments = parse_args()
    while True:
        try:
            result = run_once(arguments)
        except (OSError, ValueError, yaml.YAMLError, requests.RequestException, RuntimeError) as error:
            logging.error("Rotation check failed: %s", error)
            result = 1
        if arguments.once:
            return result
        time.sleep(arguments.interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(main())
