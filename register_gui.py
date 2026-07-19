#!/usr/bin/env python3
"""Double-click GUI launcher for register_account.py."""

from __future__ import annotations

import json
import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import requests

from register_account import (
    REGISTER_PATH,
    api_url,
    append_result,
    apply_random_client,
    attach_subscribe_url,
    build_credentials,
    get_email_domains,
    normalize_base_url,
    pick_email_domain,
    registration_succeeded,
    response_payload,
    summarize_failure,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "accounts.jsonl"
DEFAULT_BASE_URL = "https://ssyun.org"


class RegisterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("账号注册机")
        self.geometry("720x520")
        self.minsize(640, 420)
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

        self.count_var = tk.StringVar(value="1")
        self.delay_var = tk.StringVar(value="0.5")
        self.base_url_var = tk.StringVar(value=DEFAULT_BASE_URL)
        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT))
        self.insecure_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="就绪")

        self._build_ui()
        self.after(100, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        form = ttk.Frame(self)
        form.pack(fill="x", **pad)

        ttk.Label(form, text="注册数量").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.count_var, width=10).grid(row=0, column=1, sticky="w")

        ttk.Label(form, text="间隔(秒)").grid(row=0, column=2, sticky="w", padx=(16, 0))
        ttk.Entry(form, textvariable=self.delay_var, width=10).grid(row=0, column=3, sticky="w")

        ttk.Label(form, text="站点地址").grid(row=1, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.base_url_var).grid(row=1, column=1, columnspan=3, sticky="ew")

        ttk.Label(form, text="保存文件").grid(row=2, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.output_var).grid(row=2, column=1, columnspan=3, sticky="ew")

        ttk.Checkbutton(form, text="忽略 TLS 证书错误", variable=self.insecure_var).grid(
            row=3, column=1, sticky="w"
        )
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", **pad)
        self.start_btn = ttk.Button(buttons, text="开始注册", command=self.start_register)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="停止", command=self.stop_register, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="打开结果目录", command=self.open_output_dir).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="清空日志", command=self.clear_log).pack(side="right")

        ttk.Label(self, textvariable=self.status_var).pack(anchor="w", padx=10)

        self.log = scrolledtext.ScrolledText(self, wrap="word", font=("monospace", 10))
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log.configure(state="disabled")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def open_output_dir(self) -> None:
        path = Path(self.output_var.get().strip() or DEFAULT_OUTPUT).expanduser()
        folder = path if path.is_dir() else path.parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            import os
            import subprocess

            if sys.platform == "win32":
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:
            messagebox.showerror("打开失败", str(error))

    def append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start_register(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        try:
            count = int(self.count_var.get().strip())
            delay = float(self.delay_var.get().strip())
            if count < 1 or delay < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("参数错误", "注册数量必须是 >= 1 的整数，间隔必须是 >= 0 的数字")
            return

        base_url = normalize_base_url(self.base_url_var.get().strip() or DEFAULT_BASE_URL)
        output = Path(self.output_var.get().strip() or DEFAULT_OUTPUT).expanduser()
        insecure = self.insecure_var.get()

        self._stop.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_var.set("注册中…")
        self.append_log(f"==== 开始注册 count={count} delay={delay}s output={output} ====")

        self._worker = threading.Thread(
            target=self._run_register,
            args=(count, delay, base_url, output, insecure),
            daemon=True,
        )
        self._worker.start()

    def stop_register(self) -> None:
        self._stop.set()
        self.status_var.set("正在停止…")

    def _run_register(
        self, count: int, delay: float, base_url: str, output: Path, insecure: bool
    ) -> None:
        try:
            session = requests.Session()
            session.trust_env = False  # ignore system proxy (Clash) during registration
            session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
            session.verify = not insecure
            domains = get_email_domains(session, base_url, 15)
            self._queue.put(
                ("log", f"邮箱白名单 {len(domains)} 个域名，将随机选用: {', '.join(dict.fromkeys(domains))}")
            )
            register_url = api_url(base_url, REGISTER_PATH)
            success_count = 0

            for index in range(1, count + 1):
                if self._stop.is_set():
                    self._queue.put(("log", "用户停止，结束注册。"))
                    break

                domain = pick_email_domain(domains)
                email, password = build_credentials(domain)
                client_headers = apply_random_client(session)
                try:
                    response = session.post(
                        register_url,
                        json={"email_ssyun": email, "password_ssyun": password, "invite_code": ""},
                        timeout=20,
                    )
                    payload = response_payload(response)
                    succeeded = registration_succeeded(response.status_code, payload)
                    result: dict[str, Any] = {
                        "index": index,
                        "email": email,
                        "password": password,
                        "client_ip": client_headers.get("X-Real-IP"),
                        "user_agent": client_headers.get("User-Agent"),
                        "http_status": response.status_code,
                        "success": succeeded,
                        "response": payload,
                    }
                    if succeeded:
                        attach_subscribe_url(session, base_url, 20, result, payload, email, password)
                        append_result(output, result)
                        success_count += 1
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

                self._queue.put(("result", result))
                if index < count and delay and not self._stop.is_set():
                    self._stop.wait(delay)

            self._queue.put(("done", {"success_count": success_count, "output": str(output)}))
        except Exception as error:  # noqa: BLE001 - surface full failure to GUI
            self._queue.put(("error", f"{error}\n{traceback.format_exc()}"))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self.append_log(str(payload))
                elif kind == "result":
                    self._show_result(payload)
                elif kind == "done":
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.status_var.set(f"完成，成功 {payload['success_count']} 个")
                    self.append_log(
                        f"==== 完成，成功 {payload['success_count']} 个，已写入 {payload['output']} ===="
                    )
                elif kind == "error":
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.status_var.set("失败")
                    self.append_log(str(payload))
                    messagebox.showerror("注册失败", str(payload).splitlines()[0])
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _show_result(self, result: dict[str, Any]) -> None:
        lines = [
            f"[{result.get('index')}] success={result.get('success')}",
            f"  邮箱: {result.get('email')}",
            f"  密码: {result.get('password')}",
        ]
        if result.get("client_ip"):
            lines.append(f"  伪装IP: {result.get('client_ip')}")
        # Always surface subscribe URL when present (primary deliverable).
        if result.get("subscribe_url"):
            lines.append(f"  订阅链接: {result['subscribe_url']}")
        reason = summarize_failure(result)
        if reason and not result.get("success"):
            lines.append(f"  原因: {reason}")
        elif result.get("subscribe_error"):
            lines.append(f"  订阅提取失败: {result['subscribe_error']}")
        elif result.get("error"):
            lines.append(f"  错误: {result['error']}")
        if result.get("success") and not result.get("subscribe_url") and not result.get("subscribe_error"):
            lines.append("  订阅链接: （未返回）")
        self.append_log("\n".join(lines))
        if result.get("success") and result.get("subscribe_url"):
            self.status_var.set(f"成功: {result.get('email')}")
        elif result.get("success"):
            self.status_var.set(f"已注册(无订阅链接): {result.get('email')}")
        else:
            self.status_var.set(f"失败: {result.get('email')}")

    def _on_close(self) -> None:
        self._stop.set()
        self.destroy()


def main() -> int:
    try:
        app = RegisterApp()
    except tk.TclError as error:
        print(f"无法启动图形界面: {error}", file=sys.stderr)
        print("请改用命令行: python register_account.py --output accounts.jsonl", file=sys.stderr)
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
