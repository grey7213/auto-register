#!/usr/bin/env python3
"""Watch Clash Verge 2GB trial subscriptions, auto-switch, and auto-register when empty.

Behavior:
1. Read Clash Verge ``profiles.yaml`` remote subscriptions.
2. Exclude the main large sub (default: ilovesushi.cc / total >= 50GB).
3. Refresh traffic (profiles extra + live subscription-userinfo / API).
4. If the active 2GB profile is at/over the threshold, switch to the next eligible one.
5. If no eligible profile remains and ``--auto-register`` is on, register a new sandbox
   account, import its subscription into Clash Verge, and activate it.
6. Apply the chosen profile YAML to the running verge-mihomo core via named pipe (Windows)
   or Unix socket (Linux).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import string
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
import yaml

# Never route control-plane traffic (register / traffic refresh / sub download)
# through the system Clash proxy — it drops during profile reload.
_NO_PROXY = {"http": None, "https": None}

from mihomo_controller import DEFAULT_UNIX_SOCKET, DEFAULT_WINDOWS_PIPE, controller_request
from register_account import (
    REGISTER_PATH,
    api_url,
    append_result,
    apply_random_client,
    build_credentials,
    get_email_domains,
    normalize_base_url,
    pick_email_domain,
    registration_succeeded,
    response_payload,
)
from rotate_subscription import (
    Account,
    Subscription,
    download_profile,
    fetch_subscription,
    load_accounts,
    profile_runtime_names,
    save_subscription_backup,
)


APP_ID = "io.github.clash-verge-rev.clash-verge-rev"
MAIN_URL_MARKERS = ("ilovesushi.cc",)
MAIN_TOTAL_BYTES = 50 * 1024**3  # treat >= 50GB as main long-term plan
TWO_GIG_MIN = 1 * 1024**3
TWO_GIG_MAX = 5 * 1024**3


def default_profile_root() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / APP_ID
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_ID
    return Path.home() / ".local" / "share" / APP_ID


def default_state_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "clash-verge-auto-switch" / "state.json"
    return Path.home() / ".local" / "state" / "clash-verge-auto-switch.json"


@dataclass
class RemoteProfile:
    uid: str
    name: str
    file: str
    url: str
    upload: int
    download: int
    total: int
    expire: int
    raw: dict[str, Any]

    @property
    def used(self) -> int:
        return max(0, self.upload) + max(0, self.download)

    @property
    def ratio(self) -> float:
        if self.total <= 0:
            return 1.0
        return min(1.0, self.used / self.total)

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used) if self.total > 0 else 0

    def token_hint(self) -> str:
        parts = [p for p in urlsplit(self.url).path.split("/") if p]
        return parts[-1] if parts else self.uid


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logging.warning("Ignoring invalid state file: %s", path)
        return {}


def save_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def is_main_remote(item: dict[str, Any]) -> bool:
    url = str(item.get("url") or "")
    extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
    total = int(extra.get("total") or 0)
    if any(marker in url for marker in MAIN_URL_MARKERS):
        return True
    if total >= MAIN_TOTAL_BYTES:
        return True
    return False


def looks_like_trial(item: dict[str, Any]) -> bool:
    if item.get("type") != "remote":
        return False
    if is_main_remote(item):
        return False
    extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
    total = int(extra.get("total") or 0)
    if total == 0:
        # Unknown quota still allowed if URL looks like sushi trial host.
        url = str(item.get("url") or "")
        return "103.135.251.14" in url or "/sushi/" in url
    return TWO_GIG_MIN <= total <= TWO_GIG_MAX


def parse_remote(item: dict[str, Any]) -> RemoteProfile | None:
    if not looks_like_trial(item):
        return None
    uid = item.get("uid")
    file_name = item.get("file")
    url = item.get("url")
    if not isinstance(uid, str) or not isinstance(file_name, str) or not isinstance(url, str):
        return None
    extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
    return RemoteProfile(
        uid=uid,
        name=str(item.get("name") or uid),
        file=file_name,
        url=url,
        upload=int(extra.get("upload") or 0),
        download=int(extra.get("download") or 0),
        total=int(extra.get("total") or 0),
        expire=int(extra.get("expire") or 0),
        raw=item,
    )


def list_trial_profiles(index: dict[str, Any]) -> list[RemoteProfile]:
    items = index.get("items")
    if not isinstance(items, list):
        return []
    profiles: list[RemoteProfile] = []
    for item in items:
        if isinstance(item, dict):
            remote = parse_remote(item)
            if remote:
                profiles.append(remote)
    return profiles


def parse_userinfo_header(value: str) -> dict[str, int]:
    result = {"upload": 0, "download": 0, "total": 0, "expire": 0}
    for part in value.split(";"):
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        key = key.strip().lower()
        raw = raw.strip()
        if key in result:
            try:
                result[key] = int(raw)
            except ValueError:
                continue
    return result


def refresh_profile_traffic(profile: RemoteProfile, insecure: bool) -> RemoteProfile:
    """Update traffic counters from Subscription-Userinfo response header."""
    try:
        response = requests.get(
            profile.url,
            timeout=25,
            verify=not insecure,
            headers={"User-Agent": "ClashVerge/auto-switch"},
            proxies=_NO_PROXY,
        )
        header = response.headers.get("Subscription-Userinfo") or response.headers.get(
            "subscription-userinfo"
        )
        if header:
            info = parse_userinfo_header(header)
            profile.upload = info["upload"]
            profile.download = info["download"]
            if info["total"] > 0:
                profile.total = info["total"]
            if info["expire"] > 0:
                profile.expire = info["expire"]
            extra = profile.raw.setdefault("extra", {})
            if not isinstance(extra, dict):
                extra = {}
                profile.raw["extra"] = extra
            extra.update(
                {
                    "upload": profile.upload,
                    "download": profile.download,
                    "total": profile.total,
                    "expire": profile.expire,
                }
            )
            profile.raw["updated"] = int(time.time())
        # keep body only if we later need it; ignore content here
    except requests.RequestException as error:
        logging.warning("Traffic refresh failed for %s: %s", profile.uid, error)
    return profile


def new_uid(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def apply_profile_to_mihomo(
    profile_path: Path,
    *,
    pipe: str | None,
    socket_path: Path | None,
    http_url: str | None,
    secret: str,
    close_connections: bool,
) -> None:
    expected = profile_runtime_names(profile_path)
    controller_request(
        "PUT",
        "/configs?force=true",
        {"path": str(profile_path.resolve())},
        pipe=pipe,
        socket_path=socket_path,
        http_url=http_url,
        secret=secret,
    )
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        active = controller_request(
            "GET",
            "/proxies",
            pipe=pipe,
            socket_path=socket_path,
            http_url=http_url,
            secret=secret,
        )
        if isinstance(active, dict):
            proxies = active.get("proxies")
            if isinstance(proxies, dict) and expected.issubset(proxies):
                if close_connections:
                    try:
                        controller_request(
                            "DELETE",
                            "/connections",
                            pipe=pipe,
                            socket_path=socket_path,
                            http_url=http_url,
                            secret=secret,
                        )
                    except RuntimeError as error:
                        logging.warning("Could not close old connections: %s", error)
                return
        time.sleep(0.25)
    raise RuntimeError("Mihomo did not activate the selected profile")


def set_current_profile(index_path: Path, index: dict[str, Any], uid: str) -> None:
    index["current"] = uid
    atomic_write_text(index_path, dump_yaml(index))


def upsert_remote_profile(
    index: dict[str, Any],
    *,
    uid: str,
    name: str,
    file_name: str,
    url: str,
    upload: int,
    download: int,
    total: int,
    expire: int,
) -> dict[str, Any]:
    items = index.setdefault("items", [])
    if not isinstance(items, list):
        raise RuntimeError("profiles.yaml items is not a list")
    item = {
        "uid": uid,
        "type": "remote",
        "name": name,
        "file": file_name,
        "url": url,
        "extra": {
            "upload": upload,
            "download": download,
            "total": total,
            "expire": expire,
        },
        "updated": int(time.time()),
        "option": {
            "update_interval": 1440,
            "allow_auto_update": True,
            "merge": "Merge",
            "script": "Script",
        },
    }
    replaced = False
    for i, existing in enumerate(items):
        if isinstance(existing, dict) and existing.get("uid") == uid:
            # preserve selected node preferences if any
            if isinstance(existing.get("selected"), list):
                item["selected"] = existing["selected"]
            if isinstance(existing.get("option"), dict):
                merged_option = dict(existing["option"])
                merged_option.update(item["option"])
                item["option"] = merged_option
            items[i] = item
            replaced = True
            break
    if not replaced:
        items.append(item)
    return item


def register_new_account(
    *,
    base_url: str,
    accounts_path: Path,
    email_domain: str | None,
    insecure: bool,
    timeout: float,
) -> Account:
    origin = normalize_base_url(base_url)
    session = requests.Session()
    session.trust_env = False  # ignore HTTP(S)_PROXY / system proxy
    session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    session.verify = not insecure
    domains = get_email_domains(session, origin, timeout)
    domain = pick_email_domain(domains, email_domain)
    email, password = build_credentials(domain)
    client_headers = apply_random_client(session)
    response = session.post(
        api_url(origin, REGISTER_PATH),
        json={"email_ssyun": email, "password_ssyun": password, "invite_code": ""},
        timeout=timeout,
    )
    payload = response_payload(response)
    if not registration_succeeded(response.status_code, payload):
        raise RuntimeError(f"register failed status={response.status_code} payload={payload}")
    result = {
        "index": 1,
        "email": email,
        "password": password,
        "client_ip": client_headers.get("X-Real-IP"),
        "user_agent": client_headers.get("User-Agent"),
        "http_status": response.status_code,
        "success": True,
        "response": payload,
        "created_by": "auto_switch",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    append_result(accounts_path, result)
    logging.info(
        "Registered new account: %s (domain=%s ip=%s)",
        email,
        domain,
        client_headers.get("X-Real-IP"),
    )
    return Account(email=email, password=password)


def account_subscription_map(
    accounts: list[Account], insecure: bool
) -> dict[str, Subscription]:
    mapping: dict[str, Subscription] = {}
    for account in accounts:
        try:
            sub = fetch_subscription(account, insecure)
            mapping[account.email] = sub
            logging.info(
                "Account %s: %.1f%% used (%s / %s)",
                account.email,
                sub.ratio * 100,
                sub.used,
                sub.quota,
            )
        except (requests.RequestException, RuntimeError, ValueError) as error:
            logging.warning("Account %s unavailable: %s", account.email, error)
    return mapping


def match_account_for_profile(
    profile: RemoteProfile, subs: dict[str, Subscription]
) -> Subscription | None:
    hint = profile.token_hint().lower()
    for sub in subs.values():
        if hint and hint in sub.url.lower():
            return sub
        if profile.url.rstrip("/") == sub.url.rstrip("/"):
            return sub
    return None


def choose_next_profile(
    profiles: list[RemoteProfile],
    current_uid: str | None,
    threshold: float,
    min_remaining: int,
) -> RemoteProfile | None:
    def eligible(profile: RemoteProfile) -> bool:
        if profile.expire and profile.expire < time.time():
            return False
        if profile.total <= 0:
            return False
        if profile.ratio >= threshold:
            return False
        if profile.remaining < min_remaining:
            return False
        return True

    eligible_profiles = [p for p in profiles if eligible(p)]
    if not eligible_profiles:
        return None

    if current_uid:
        current = next((p for p in profiles if p.uid == current_uid), None)
        if current and eligible(current):
            return current
        if current:
            try:
                idx = profiles.index(current)
            except ValueError:
                idx = -1
            for offset in range(1, len(profiles) + 1):
                candidate = profiles[(idx + offset) % len(profiles)]
                if eligible(candidate):
                    return candidate

    # Prefer least used remaining
    return min(eligible_profiles, key=lambda p: p.ratio)


def choose_next_account(
    subs: dict[str, Subscription],
    used_urls: set[str],
    threshold: float,
    min_remaining: int,
    current_email: str | None,
) -> Subscription | None:
    def eligible(sub: Subscription) -> bool:
        if not sub.eligible:
            return False
        if sub.ratio >= threshold:
            return False
        if sub.quota - sub.used < min_remaining:
            return False
        return True

    ordered = list(subs.values())
    if current_email:
        ordered.sort(key=lambda s: 0 if s.account.email == current_email else 1)

    # Prefer accounts not already imported as exhausted profiles; still allow unused urls
    for sub in ordered:
        if eligible(sub) and sub.url.rstrip("/") not in used_urls:
            return sub
    for sub in ordered:
        if eligible(sub):
            return sub
    return None


def human_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return f"{value:.2f}{unit}"
        value /= 1024
    return f"{num}B"


def run_once(args: argparse.Namespace) -> int:
    index_path = args.profiles_index
    profiles_dir = args.profiles_dir
    if not index_path.is_file():
        raise RuntimeError(f"profiles.yaml not found: {index_path}")

    index = load_yaml(index_path) or {}
    if not isinstance(index, dict):
        raise RuntimeError("profiles.yaml root must be a mapping")

    state = load_state(args.state)
    current_uid = str(index.get("current") or state.get("current_uid") or "")

    trial_profiles = list_trial_profiles(index)
    logging.info("Found %d trial/remote 2GB-like profiles (main excluded)", len(trial_profiles))

    # Refresh traffic for known profiles
    for profile in trial_profiles:
        refresh_profile_traffic(profile, args.insecure)
        logging.info(
            "Profile %s (%s): %s / %s (%.1f%%) remaining %s",
            profile.uid,
            profile.token_hint()[:12],
            human_bytes(profile.used),
            human_bytes(profile.total),
            profile.ratio * 100,
            human_bytes(profile.remaining),
        )

    # Persist refreshed extra back into profiles.yaml (UI traffic bars)
    if not args.dry_run:
        items = index.get("items")
        if isinstance(items, list):
            by_uid = {p.uid: p for p in trial_profiles}
            for i, item in enumerate(items):
                if isinstance(item, dict) and item.get("uid") in by_uid:
                    remote = by_uid[item["uid"]]
                    items[i] = remote.raw
            atomic_write_text(index_path, dump_yaml(index))

    selected_profile = choose_next_profile(
        trial_profiles, current_uid or None, args.threshold, args.min_remaining_bytes
    )
    selected_sub: Subscription | None = None
    created_new = False
    accounts: list[Account] = []
    subs: dict[str, Subscription] = {}

    # Only hit the account API when no in-Clash trial profile is still usable,
    # or when the user explicitly wants full inventory (--scan-accounts).
    need_account_scan = selected_profile is None or args.scan_accounts
    if need_account_scan and args.accounts.is_file():
        try:
            accounts = load_accounts(args.accounts)
        except ValueError as error:
            logging.warning("accounts file issue: %s", error)
        subs = account_subscription_map(accounts, args.insecure) if accounts else {}

        # Merge live account traffic into matching profiles (best-effort)
        for profile in trial_profiles:
            matched = match_account_for_profile(profile, subs)
            if matched:
                profile.upload = 0
                profile.download = matched.used
                profile.total = matched.quota
                profile.expire = matched.expires_at
                extra = profile.raw.setdefault("extra", {})
                if isinstance(extra, dict):
                    extra.update(
                        {
                            "upload": 0,
                            "download": matched.used,
                            "total": matched.quota,
                            "expire": matched.expires_at,
                        }
                    )
        # Re-evaluate after merging live account stats
        selected_profile = choose_next_profile(
            trial_profiles, current_uid or None, args.threshold, args.min_remaining_bytes
        )

    if selected_profile is None:
        used_urls = {p.url.rstrip("/") for p in trial_profiles if p.ratio >= args.threshold}
        for p in trial_profiles:
            if p.ratio >= args.threshold or p.remaining < args.min_remaining_bytes:
                used_urls.add(p.url.rstrip("/"))

        if not subs and args.accounts.is_file():
            try:
                accounts = load_accounts(args.accounts)
            except ValueError as error:
                logging.warning("accounts file issue: %s", error)
            subs = account_subscription_map(accounts, args.insecure) if accounts else {}

        selected_sub = choose_next_account(
            subs,
            used_urls,
            args.threshold,
            args.min_remaining_bytes,
            state.get("email"),
        )
        if selected_sub is None and args.auto_register:
            logging.info("No eligible subscription left; auto-registering...")
            if args.dry_run:
                logging.info("[dry-run] would register a new account")
                return 0
            account = register_new_account(
                base_url=args.base_url,
                accounts_path=args.accounts,
                email_domain=args.email_domain,
                insecure=args.insecure,
                timeout=args.timeout,
            )
            selected_sub = fetch_subscription(account, args.insecure)
            created_new = True
        elif selected_sub is None:
            logging.error("No eligible 2GB subscription and --auto-register is disabled")
            return 2

    if selected_sub is not None:
        # Import subscription as a Clash Verge remote profile (or reuse matching uid)
        existing = next(
            (p for p in trial_profiles if p.url.rstrip("/") == selected_sub.url.rstrip("/")),
            None,
        )
        uid = existing.uid if existing else new_uid()
        file_name = existing.file if existing else f"{uid}.yaml"
        profile_content = download_profile(selected_sub.url, args.insecure)
        profile_path = profiles_dir / file_name
        logging.info(
            "Importing %s (%.1f%% used) as profile %s",
            selected_sub.account.email,
            selected_sub.ratio * 100,
            uid,
        )
        if args.dry_run:
            return 0
        atomic_write_text(profile_path, profile_content)
        try:
            save_subscription_backup(args.backup_dir, selected_sub, profile_content)
        except OSError as error:
            logging.warning("Backup failed: %s", error)

        # reload index after possible earlier write
        index = load_yaml(index_path) or {}
        upsert_remote_profile(
            index,
            uid=uid,
            name=args.profile_name,
            file_name=file_name,
            url=selected_sub.url,
            upload=0,
            download=selected_sub.used,
            total=selected_sub.quota,
            expire=selected_sub.expires_at,
        )
        set_current_profile(index_path, index, uid)
        apply_profile_to_mihomo(
            profile_path,
            pipe=args.pipe,
            socket_path=args.socket,
            http_url=args.http_controller,
            secret=args.secret,
            close_connections=args.close_connections,
        )
        save_state(
            args.state,
            {
                "current_uid": uid,
                "email": selected_sub.account.email,
                "url": selected_sub.url,
                "created_new": created_new,
                "usage_ratio": selected_sub.ratio,
            },
        )
        logging.info(
            "Switched Clash Verge to %s (%s)",
            uid,
            selected_sub.account.email,
        )
        return 0

    assert selected_profile is not None
    profile_path = profiles_dir / selected_profile.file
    if not profile_path.is_file():
        # try re-download
        logging.info("Profile file missing; re-downloading %s", selected_profile.url)
        if not args.dry_run:
            atomic_write_text(profile_path, download_profile(selected_profile.url, args.insecure))

    needs_switch = selected_profile.uid != current_uid
    if not needs_switch:
        logging.info(
            "Keeping current profile %s (%.1f%% used, remaining %s)",
            selected_profile.uid,
            selected_profile.ratio * 100,
            human_bytes(selected_profile.remaining),
        )
        # still ensure core is on this profile if forced
        if args.force_apply and not args.dry_run:
            apply_profile_to_mihomo(
                profile_path,
                pipe=args.pipe,
                socket_path=args.socket,
                http_url=args.http_controller,
                secret=args.secret,
                close_connections=False,
            )
        save_state(
            args.state,
            {
                "current_uid": selected_profile.uid,
                "email": state.get("email"),
                "url": selected_profile.url,
                "usage_ratio": selected_profile.ratio,
            },
        )
        return 0

    logging.info(
        "Switching %s -> %s (%.1f%% used, remaining %s)",
        current_uid or "(none)",
        selected_profile.uid,
        selected_profile.ratio * 100,
        human_bytes(selected_profile.remaining),
    )
    if args.dry_run:
        return 0

    index = load_yaml(index_path) or {}
    set_current_profile(index_path, index, selected_profile.uid)
    apply_profile_to_mihomo(
        profile_path,
        pipe=args.pipe,
        socket_path=args.socket,
        http_url=args.http_controller,
        secret=args.secret,
        close_connections=args.close_connections,
    )
    matched = match_account_for_profile(selected_profile, subs)
    save_state(
        args.state,
        {
            "current_uid": selected_profile.uid,
            "email": matched.account.email if matched else state.get("email"),
            "url": selected_profile.url,
            "usage_ratio": selected_profile.ratio,
        },
    )
    logging.info("Clash Verge now uses profile %s", selected_profile.uid)
    return 0


def parse_args() -> argparse.Namespace:
    root = default_profile_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", type=Path, default=Path("accounts.jsonl"))
    parser.add_argument("--state", type=Path, default=default_state_path())
    parser.add_argument("--backup-dir", type=Path, default=Path("subscription_backups"))
    parser.add_argument("--profiles-index", type=Path, default=root / "profiles.yaml")
    parser.add_argument("--profiles-dir", type=Path, default=root / "profiles")
    parser.add_argument(
        "--pipe",
        default=DEFAULT_WINDOWS_PIPE if sys.platform == "win32" else None,
        help="Windows named pipe path for verge-mihomo",
    )
    parser.add_argument("--socket", type=Path, default=DEFAULT_UNIX_SOCKET)
    parser.add_argument(
        "--http-controller",
        default=None,
        help="Optional TCP external-controller, e.g. http://127.0.0.1:9097",
    )
    parser.add_argument("--secret", default="", help="Bearer secret for TCP controller")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.95,
        help="Switch when used/total >= this ratio (default 0.95)",
    )
    parser.add_argument(
        "--min-remaining-mb",
        type=float,
        default=50.0,
        help="Also switch when remaining traffic is below this many MB (default 50)",
    )
    parser.add_argument("--interval", type=int, default=120, help="Seconds between checks")
    parser.add_argument("--once", action="store_true", help="Run one check and exit")
    parser.add_argument("--dry-run", action="store_true", help="Do not modify Clash or accounts")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification")
    parser.add_argument(
        "--auto-register",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Register a new sandbox account when all 2GB subs are exhausted (default: true)",
    )
    parser.add_argument("--base-url", default="https://ssyun.org")
    parser.add_argument("--email-domain", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--profile-name", default="寿司云")
    parser.add_argument(
        "--close-connections",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Close existing connections after switch (default: true)",
    )
    parser.add_argument(
        "--force-apply",
        action="store_true",
        help="Re-apply current profile to mihomo even if already selected",
    )
    parser.add_argument(
        "--scan-accounts",
        action="store_true",
        help="Always query every account in accounts.jsonl (slow; default only when no eligible profile)",
    )
    args = parser.parse_args()
    if not 0 < args.threshold <= 1:
        parser.error("--threshold must be in (0, 1]")
    if args.interval < 1:
        parser.error("--interval must be positive")
    if args.min_remaining_mb < 0:
        parser.error("--min-remaining-mb must be non-negative")
    args.min_remaining_bytes = int(args.min_remaining_mb * 1024 * 1024)
    if sys.platform != "win32":
        args.pipe = None
    return args


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.info("Clash Verge profile root: %s", args.profiles_index.parent)
    while True:
        try:
            code = run_once(args)
        except (OSError, ValueError, yaml.YAMLError, requests.RequestException, RuntimeError) as error:
            logging.error("Auto-switch check failed: %s", error)
            code = 1
        if args.once:
            return code
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
