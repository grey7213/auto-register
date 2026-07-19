#!/usr/bin/env python3
"""Cross-platform Mihomo/Clash Meta external-controller client.

Supports:
- Linux/macOS Unix domain socket (e.g. /tmp/verge/verge-mihomo.sock)
- Windows named pipe (\\\\.\\pipe\\verge-mihomo)
- Optional HTTP TCP controller (127.0.0.1:9097) with Bearer secret
"""

from __future__ import annotations

import ctypes
import http.client
import json
import socket
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_WINDOWS_PIPE = r"\\.\pipe\verge-mihomo"
DEFAULT_UNIX_SOCKET = Path("/tmp/verge/verge-mihomo.sock")


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, unix_socket: Path) -> None:
        super().__init__("localhost")
        self.unix_socket = str(unix_socket)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.unix_socket)


def _decode_chunked(body: bytes) -> bytes:
    out = bytearray()
    view = memoryview(body)
    pos = 0
    length = len(body)
    while pos < length:
        nl = body.find(b"\r\n", pos)
        if nl < 0:
            break
        size_line = body[pos:nl].split(b";", 1)[0].strip()
        try:
            chunk_size = int(size_line, 16)
        except ValueError as error:
            raise RuntimeError("invalid chunked response") from error
        pos = nl + 2
        if chunk_size == 0:
            break
        if pos + chunk_size > length:
            raise RuntimeError("truncated chunked response")
        out.extend(view[pos : pos + chunk_size])
        pos += chunk_size
        if body[pos : pos + 2] == b"\r\n":
            pos += 2
    return bytes(out)


def _parse_http_response(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    header_blob, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        raise RuntimeError("invalid HTTP response from controller")
    lines = header_blob.split(b"\r\n")
    status_line = lines[0].decode("ascii", "replace")
    try:
        status = int(status_line.split()[1])
    except (IndexError, ValueError) as error:
        raise RuntimeError(f"invalid HTTP status line: {status_line}") from error
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" not in line:
            continue
        key, value = line.split(b":", 1)
        headers[key.decode("ascii", "replace").lower()] = value.strip().decode("latin-1", "replace")
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = _decode_chunked(body)
    elif "content-length" in headers:
        body = body[: int(headers["content-length"])]
    return status, headers, body


class WindowsPipeController:
    """HTTP client over a Windows named pipe."""

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self, pipe_name: str = DEFAULT_WINDOWS_PIPE) -> None:
        self.pipe_name = pipe_name
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._CreateFileW = self._k32.CreateFileW
        self._CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._CreateFileW.restype = wintypes.HANDLE
        self._ReadFile = self._k32.ReadFile
        self._WriteFile = self._k32.WriteFile
        self._CloseHandle = self._k32.CloseHandle
        self._SetNamedPipeHandleState = self._k32.SetNamedPipeHandleState

    def _open(self) -> int:
        handle = self._CreateFileW(
            self.pipe_name,
            self.GENERIC_READ | self.GENERIC_WRITE,
            0,
            None,
            self.OPEN_EXISTING,
            0,
            None,
        )
        if not handle or int(handle) in (0, -1) or handle == self.INVALID_HANDLE_VALUE:
            raise RuntimeError(
                f"cannot open Mihomo pipe {self.pipe_name!r} (err={ctypes.get_last_error()})"
            )
        mode = wintypes.DWORD(0)  # PIPE_READMODE_BYTE
        self._SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None)
        return int(handle)

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = b""
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        headers = [
            f"{method} {path} HTTP/1.1",
            "Host: localhost",
            "Connection: close",
        ]
        if body:
            headers.append("Content-Type: application/json")
            headers.append(f"Content-Length: {len(body)}")
        request_bytes = ("\r\n".join(headers) + "\r\n\r\n").encode("utf-8") + body

        handle = self._open()
        try:
            written = wintypes.DWORD()
            if not self._WriteFile(handle, request_bytes, len(request_bytes), ctypes.byref(written), None):
                raise RuntimeError(f"WriteFile failed err={ctypes.get_last_error()}")

            buf = ctypes.create_string_buffer(65536)
            read = wintypes.DWORD()
            raw = bytearray()
            while True:
                ok = self._ReadFile(handle, buf, len(buf), ctypes.byref(read), None)
                if not ok:
                    err = ctypes.get_last_error()
                    # ERROR_BROKEN_PIPE / ERROR_NO_DATA / ERROR_PIPE_NOT_CONNECTED
                    if err in (109, 232, 233) and raw:
                        break
                    raise RuntimeError(f"ReadFile failed err={err}")
                if read.value == 0:
                    break
                raw.extend(buf.raw[: read.value])
                if b"\r\n\r\n" not in raw:
                    continue
                header, _, rest = bytes(raw).partition(b"\r\n\r\n")
                header_lower = header.lower()
                if b"content-length:" in header_lower:
                    for line in header.split(b"\r\n"):
                        if line.lower().startswith(b"content-length:"):
                            length = int(line.split(b":", 1)[1].strip())
                            if len(rest) >= length:
                                raw = bytearray(header + b"\r\n\r\n" + rest[:length])
                                break
                    else:
                        continue
                    break
                if b"transfer-encoding: chunked" in header_lower and b"\r\n0\r\n\r\n" in rest:
                    break
        finally:
            self._CloseHandle(handle)

        status, _headers, response_body = _parse_http_response(bytes(raw))
        if not 200 <= status < 300:
            detail = response_body[:200].decode("utf-8", "replace")
            raise RuntimeError(f"Mihomo controller returned HTTP {status}: {detail}")
        if not response_body:
            return None
        try:
            return json.loads(response_body)
        except json.JSONDecodeError:
            return response_body.decode("utf-8", errors="replace")


class UnixSocketController:
    def __init__(self, unix_socket: Path) -> None:
        self.unix_socket = Path(unix_socket)

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.unix_socket.exists():
            raise RuntimeError(f"Mihomo controller socket not found: {self.unix_socket}")
        connection = UnixHTTPConnection(self.unix_socket)
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


class HttpController:
    def __init__(self, base_url: str, secret: str = "") -> None:
        parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 9097
        self.secret = secret

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=10)
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
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


def default_controller() -> Any:
    if sys.platform == "win32":
        return WindowsPipeController(DEFAULT_WINDOWS_PIPE)
    return UnixSocketController(DEFAULT_UNIX_SOCKET)


def controller_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    pipe: str | None = None,
    socket_path: Path | None = None,
    http_url: str | None = None,
    secret: str = "",
) -> Any:
    if http_url:
        return HttpController(http_url, secret).request(method, path, payload)
    if pipe or sys.platform == "win32":
        return WindowsPipeController(pipe or DEFAULT_WINDOWS_PIPE).request(method, path, payload)
    return UnixSocketController(socket_path or DEFAULT_UNIX_SOCKET).request(method, path, payload)
