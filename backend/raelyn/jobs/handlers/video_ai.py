from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.jobs.log import job_log
from raelyn.jobs.registry import registry
from raelyn.models import Asset, Job, Video
from raelyn.services.assets import ensure_asset
from raelyn.services.brief_prompt import load_plain_transcript_asset
from raelyn.services.llm import llm_enabled, llm_generate
from raelyn.services.s3 import s3_download_file
from raelyn.services.transcript_polish_prompt import (
    load_transcript_polish_prompt_template,
    render_transcript_polish_prompt,
)
from raelyn.services.workdir import job_workdir


@registry.register("video.polish_transcript")
def video_polish_transcript(session: Session, job: Job) -> dict | None:
    if not llm_enabled():
        return {"skipped": "llm not configured"}

    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    language = str(job.params.get("language") or "").strip().lower()
    source = str(job.params.get("source") or "").strip()
    force = bool(job.params.get("force"))

    if not language or not source:
        job_log(session, job, "polish_transcript skipped: missing language/source", level="warn")
        return {"skipped": "missing params"}

    plain = session.execute(
        select(Asset).where(
            Asset.video_id == video.id,
            Asset.type == "transcript",
            Asset.format == "txt",
            Asset.variant == "plain",
            Asset.source == source,
            Asset.language == language,
        )
    ).scalar_one_or_none()
    if not plain:
        return {"skipped": "plain transcript not found"}

    if not force:
        exists = session.execute(
            select(Asset.id).where(
                Asset.video_id == video.id,
                Asset.type == "transcript",
                Asset.format == "txt",
                Asset.variant == "polished",
                Asset.source == source,
                Asset.language == language,
            )
        ).scalar_one_or_none()
        if exists:
            return {"skipped": "already polished"}

    base = f"{video.provider}/{video.media_id}/{video.provider_video_id}/transcript/{language}"
    s3_key = f"{base}/polished.txt" if source == "subtitle" else f"{base}/{source}-polished.txt"

    try:
        with job_workdir(job.id) as wd:
            local_plain = wd / "plain.txt"
            s3_download_file(bucket=plain.s3_bucket, key=plain.s3_key, local_path=local_plain)
            text = local_plain.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                return {"skipped": "plain transcript empty"}

            polished, usage = _polish_transcript_via_llm(session=session, text=text)
            polished = _sanitize_llm_plain_text(polished).strip()
            if not polished:
                job_log(session, job, "llm transcript polish returned empty; keep plain transcript", level="warn")
                return {"skipped": "empty polish result", "llm_usage": usage}

            out_path = wd / "polished.txt"
            out_path.write_text(polished, encoding="utf-8")
            ensure_asset(
                session,
                video_id=video.id,
                type_="transcript",
                format_="txt",
                language=language,
                source=source,
                variant="polished",
                local_path=out_path,
                s3_key=s3_key,
                content_type="text/plain; charset=utf-8",
                metadata={"variant": "polished", "polish_method": "llm", "polished": True},
                replace=force,
            )
            job_log(session, job, "llm transcript polish saved", level="info", data={"language": language, "source": source})
            return {"ok": True, "llm_usage": usage}
    except Exception as e:
        job_log(session, job, f"llm transcript polish failed: {e}", level="warn")
        return {"skipped": "llm failed"}


@registry.register("video.generate_note")
def video_generate_note(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(job.params["video_id"])
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    if not llm_enabled():
        return {"skipped": "llm not configured"}

    bucket, key = load_plain_transcript_asset(session, video.id)
    if not bucket or not key:
        return {"skipped": "transcript not found"}

    with job_workdir(job.id) as wd:
        local_txt = wd / "transcript.txt"
        s3_download_file(bucket=bucket, key=key, local_path=local_txt)
        text = local_txt.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            return {"skipped": "empty transcript"}

        prompt = (
            "你是一个内容总结助手。请根据以下视频的文字内容，生成一份**Markdown**格式的总结。\n"
            "要求：\n"
            "- 用中文\n"
            "- 输出：一句话摘要、要点（bullet）、可能的行动建议（可选）\n"
            "- 不要编造未出现的信息；不确定的地方明确说明“文本未提及”。\n\n"
            f"视频标题：{video.title or video.provider_video_id}\n"
            f"来源：{video.url}\n\n"
            "以下是视频文本：\n\n"
            f"{text}\n"
        )

        resp = llm_generate(prompt=prompt, think=True)
        md = str(resp.get("text", ""))
        out = wd / "note.md"
        out.write_text(md, encoding="utf-8")

        asset = ensure_asset(
            session,
            video_id=video.id,
            type_="note",
            format_="md",
            language="zh",
            source="llm",
            variant="summary",
            local_path=out,
            s3_key=f"{video.provider}/{video.media_id}/{video.provider_video_id}/note/summary.md",
            content_type="text/markdown; charset=utf-8",
            metadata={"video_id": str(video.id)},
        )
        return {"asset_id": str(asset.id), "llm_usage": resp.get("usage")}


def _sanitize_llm_plain_text(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    value = re.sub(r"(?is)<think>.*?</think>", "", value)
    value = re.sub(r"(?is)</?think>", "", value)
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", value)
        value = re.sub(r"\n?```$", "", value)
    return value.strip().strip("\ufeff")


def _empty_llm_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "call_count": 0}


def _merge_llm_usage(total: dict[str, int], part: dict[str, Any] | None) -> dict[str, int]:
    merged = dict(total or _empty_llm_usage())
    if not isinstance(part, dict):
        return merged

    for key in ("input_tokens", "output_tokens", "total_tokens", "call_count"):
        try:
            value = int(part.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            merged[key] = int(merged.get(key, 0)) + value
    return merged


def _split_text_for_llm(text: str, *, max_chars: int) -> list[str]:
    value = (text or "").strip()
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]
    lines = value.splitlines()
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        line_len = len(line) + 1
        if buf and size + line_len > max_chars:
            chunks.append("\n".join(buf).strip())
            buf = []
            size = 0
        if line_len > max_chars and not buf:
            start = 0
            while start < len(line):
                chunks.append(line[start : start + max_chars].strip())
                start += max_chars
            continue
        buf.append(line)
        size += line_len
    if buf:
        chunks.append("\n".join(buf).strip())
    return [chunk for chunk in chunks if chunk]


def _build_transcript_polish_prompt(*, session: Session | None = None, chunk: str, index: int, total: int) -> str:
    template = load_transcript_polish_prompt_template(session)
    return render_transcript_polish_prompt(template=template, chunk=chunk, index=index, total=total)


def _polish_transcript_via_llm(*, session: Session | None, text: str) -> tuple[str, dict[str, int]]:
    chunks = _split_text_for_llm(text, max_chars=12_000)
    if not chunks:
        return "", _empty_llm_usage()

    outputs: list[str] = []
    usage = _empty_llm_usage()
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        prompt = _build_transcript_polish_prompt(session=session, chunk=chunk, index=index, total=total)
        resp = llm_generate(prompt=prompt, think=False)
        usage = _merge_llm_usage(usage, resp.get("usage"))
        out = _sanitize_llm_plain_text(str(resp.get("text", "")))
        if out:
            outputs.append(out.strip())

    return "\n\n".join(outputs).strip(), usage


def _maybe_polish_transcript(
    session: Session,
    *,
    job: Job,
    video: Video,
    language: str,
    source: str,
    plain_text: str,
    output_path: Path,
    s3_key: str,
    force: bool = False,
) -> None:
    if not llm_enabled():
        return

    text = (plain_text or "").strip()
    if not text:
        return

    if not force:
        exists = session.execute(
            select(Asset.id).where(
                Asset.video_id == video.id,
                Asset.type == "transcript",
                Asset.format == "txt",
                Asset.language == language,
                Asset.source == source,
                Asset.variant == "polished",
            )
        ).scalar_one_or_none()
        if exists:
            return

    try:
        polished, _usage = _polish_transcript_via_llm(session=session, text=text)
        polished = (polished or "").strip()
        metadata: dict[str, Any] = {"variant": "polished"}
        if not polished:
            job_log(session, job, "llm transcript polish returned empty; keep plain transcript", level="warn")
            return

        metadata.update({"polish_method": "llm", "polished": True})
        output_path.write_text(polished, encoding="utf-8")
        ensure_asset(
            session,
            video_id=video.id,
            type_="transcript",
            format_="txt",
            language=language,
            source=source,
            variant="polished",
            local_path=output_path,
            s3_key=s3_key,
            content_type="text/plain; charset=utf-8",
            metadata=metadata,
            replace=force,
        )
        job_log(session, job, "llm transcript polish saved", level="info", data={"language": language, "source": source})
    except Exception as e:
        job_log(session, job, f"llm transcript polish failed: {e}", level="warn")
