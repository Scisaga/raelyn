from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any


_USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "call_count", "cached_input_tokens")
_META_FIELDS = (
    "provider", "transport", "stream", "done", "done_reason", "model", "reasoning_effort",
    "service_tier", "duration_seconds", "tool_calls",
)


def checkpoint_key(prompt: str, model_options: dict[str, Any], response_format: Any) -> str:
    request = json.dumps(
        {"prompt": prompt, "model_options": model_options, "response_format": response_format},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(request.encode("utf-8")).hexdigest()


def _job_directory(job_id: uuid.UUID, root: Path | None) -> Path:
    base = root if root is not None else Path("tmp/codex-checkpoints")
    return base / str(uuid.UUID(str(job_id)))


def _checkpoint_path(job_id: uuid.UUID, key: str, root: Path | None) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", key) is None:
        raise ValueError("Codex checkpoint key 必须是 SHA256 摘要。")
    return _job_directory(job_id, root) / f"{key}.json"


def _response_payload(result: dict[str, Any]) -> dict[str, Any]:
    # 响应只保留业务正文、真实用量和已知安全元数据；请求与认证信息不落盘。
    return {
        "text": result["text"],
        "usage": {key: result["usage"][key] for key in _USAGE_FIELDS if key in result["usage"]},
        "meta": {key: result["meta"][key] for key in _META_FIELDS if key in result["meta"]},
    }


def load_checkpoint(job_id: uuid.UUID, key: str, *, root: Path | None = None) -> dict[str, Any] | None:
    path = _checkpoint_path(job_id, key, root)
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    result = _response_payload(json.loads(contents))
    result["meta"]["cached_checkpoint"] = True
    return result


def save_checkpoint(
    job_id: uuid.UUID, key: str, result: dict[str, Any], *, root: Path | None = None
) -> None:
    path = _checkpoint_path(job_id, key, root)
    contents = json.dumps(_response_payload(result), ensure_ascii=False, allow_nan=False)
    path.parent.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".response-", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def clear_checkpoints(job_id: uuid.UUID, *, root: Path | None = None) -> None:
    try:
        shutil.rmtree(_job_directory(job_id, root))
    except FileNotFoundError:
        return
