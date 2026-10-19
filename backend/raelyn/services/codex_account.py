from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from raelyn.config import settings
from raelyn.services.codex import _child_environment


_WEEK_MINUTES = 7 * 24 * 60


def _sanitize_window(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeError("Codex 订阅额度窗口格式无效。")
    used = value.get("usedPercent")
    minutes = value.get("windowDurationMins")
    resets_at = value.get("resetsAt")
    if (
        type(used) not in (int, float)
        or not math.isfinite(used)
        or used < 0
        or type(minutes) is not int
        or minutes <= 0
        or type(resets_at) is not int
    ):
        raise RuntimeError("Codex 订阅额度窗口缺少有效的用量、时长或重置时间。")
    return {"used_percent": used, "window_minutes": minutes, "resets_at": resets_at}


def _sanitize_rate_limits(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise RuntimeError("Codex 未返回可用的订阅额度。")
    buckets = result.get("rateLimitsByLimitId")
    if not buckets:
        single = result.get("rateLimits")
        if not isinstance(single, dict):
            raise RuntimeError("Codex 未返回可用的订阅额度。")
        buckets = {single.get("limitId") or "codex": single}
    if not isinstance(buckets, dict) or not isinstance(buckets.get("codex"), dict):
        raise RuntimeError("Codex 未返回可识别的订阅额度分组。")

    sanitized: dict[str, Any] = {}
    for limit_id, bucket in buckets.items():
        if not isinstance(bucket, dict):
            raise RuntimeError("Codex 订阅额度分组格式无效。")
        credits = bucket.get("credits")
        sanitized[str(limit_id)] = {
            "limit_id": str(limit_id),
            "plan_type": bucket.get("planType"),
            "primary": _sanitize_window(bucket.get("primary")),
            "secondary": _sanitize_window(bucket.get("secondary")),
            "credits": {
                "has_credits": credits.get("hasCredits"),
                "unlimited": credits.get("unlimited"),
            } if isinstance(credits, dict) else None,
        }
    bucket = sanitized["codex"]
    windows = [window for key in ("primary", "secondary") if (window := bucket[key]) is not None]
    # 周窗口可出现在 primary；不能按字段位置猜测窗口含义。
    weekly = next((window for window in windows if window["window_minutes"] == _WEEK_MINUTES), None)
    short_windows = [window for window in windows if window["window_minutes"] < _WEEK_MINUTES]
    reset_credits = result.get("rateLimitResetCredits")
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "limit_id": "codex",
        "plan_type": bucket["plan_type"],
        "weekly": weekly,
        "short_window": min(short_windows, key=lambda window: window["window_minutes"], default=None),
        "credits": bucket["credits"],
        "reset_credits_available_count": reset_credits.get("availableCount") if isinstance(reset_credits, dict) else None,
        "buckets": sanitized,
    }


def read_codex_rate_limits(*, timeout_seconds: float = 30) -> dict[str, Any]:
    """只读取账户额度；不创建线程、生成内容、兑换重置额度或修改登录方式。"""
    executable = shutil.which(settings.codex_command)
    if executable is None:
        raise RuntimeError("未找到 Codex CLI，无法读取订阅额度。")
    deadline = time.monotonic() + timeout_seconds
    with tempfile.TemporaryDirectory(prefix="raelyn-codex-account-") as directory:
        args = [
            executable, "app-server", "--listen", "stdio://", "--strict-config",
            "--config", "analytics.enabled=false", "--config", "project_doc_max_bytes=0",
            "--config", f"log_dir={json.dumps(directory)}",
            "--config", f"sqlite_home={json.dumps(directory)}",
            "--config", 'web_search="disabled"',
        ]
        for feature in ("system_proxy_fallback", "plugins", "remote_plugin", "apps", "hooks"):
            args.extend(["--disable", feature])
        try:
            process = subprocess.Popen(
                args,
                cwd=directory,
                env=_child_environment(proxy=settings.ytdlp_proxy),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                bufsize=0,
            )
        except OSError:
            raise RuntimeError("Codex 账户查询进程无法启动。") from None
        try:
            stdin, stdout = process.stdin, process.stdout
            if stdin is None or stdout is None:
                raise RuntimeError("Codex 账户查询通道无法建立。")
            with selectors.DefaultSelector() as selector:
                selector.register(stdout, selectors.EVENT_READ)
                buffer = b""

                def send(message: dict[str, Any]) -> None:
                    stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                    stdin.flush()

                def receive(request_id: int) -> dict[str, Any]:
                    nonlocal buffer
                    while True:
                        if b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            try:
                                message = json.loads(line)
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                raise RuntimeError("Codex 账户查询返回了无效的协议响应。") from None
                            if not isinstance(message, dict):
                                raise RuntimeError("Codex 账户查询返回了无效的协议响应。")
                            if message.get("id") != request_id:
                                continue
                            if "error" in message or not isinstance(message.get("result"), dict):
                                raise RuntimeError("Codex 订阅额度读取失败，请检查 CLI 登录和网络连接。")
                            return message["result"]
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise httpx.TimeoutException("Codex 订阅额度查询超时。")
                        piece = os.read(stdout.fileno(), 65536)
                        if not piece:
                            raise RuntimeError("Codex 账户查询进程提前退出。")
                        buffer += piece

                send({
                    "id": 0, "method": "initialize",
                    "params": {"clientInfo": {"name": "raelyn", "title": "Raelyn", "version": "0.1.0"}},
                })
                receive(0)
                send({"method": "initialized", "params": {}})
                send({"id": 1, "method": "account/rateLimits/read"})
                return _sanitize_rate_limits(receive(1))
        except OSError:
            raise RuntimeError("Codex 账户查询连接失败。") from None
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                # 进程可能恰好已经退出。
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Codex 账户查询进程未能及时关闭。") from None
            finally:
                if process.stdin is not None:
                    process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()
