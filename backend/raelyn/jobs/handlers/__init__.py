from __future__ import annotations

from raelyn.services.transcript_polish_prompt import (
    load_transcript_polish_prompt_template,
    render_transcript_polish_prompt,
)

from . import briefs as _briefs  # noqa: F401
from . import media_delete as _media_delete  # noqa: F401
from . import media_sync as _media_sync  # noqa: F401
from . import event_analysis as _event_analysis  # noqa: F401
from . import video_ai as _video_ai  # noqa: F401
from . import video_download as _video_download  # noqa: F401
from . import video_process as _video_process  # noqa: F401


def _build_transcript_polish_prompt(*, session=None, chunk: str, index: int, total: int) -> str:
    template = load_transcript_polish_prompt_template(session)
    return render_transcript_polish_prompt(template=template, chunk=chunk, index=index, total=total)


def _polish_transcript_via_llm(*, session, text: str):
    return _video_ai._polish_transcript_via_llm(session=session, text=text)


def _split_text_for_llm(text: str, *, max_chars: int):
    return _video_ai._split_text_for_llm(text, max_chars=max_chars)


__all__ = [
    "_build_transcript_polish_prompt",
    "_polish_transcript_via_llm",
    "_split_text_for_llm",
    "load_transcript_polish_prompt_template",
    "render_transcript_polish_prompt",
]
