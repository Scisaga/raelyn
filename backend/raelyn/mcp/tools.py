from __future__ import annotations

from typing import Any
from datetime import date as dt_date
import uuid

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from raelyn.mcp import actions, queries


def _raise_tool_error(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise ToolError(f"not_found: {exc}") from exc
    if isinstance(exc, ValueError):
        raise ToolError(f"invalid_argument: {exc}") from exc
    if isinstance(exc, RuntimeError):
        raise ToolError(f"conflict: {exc}") from exc
    raise ToolError(str(exc)) from exc


def register_tools(mcp: FastMCP, *, include_actions: bool = True) -> None:
    @mcp.tool(name="list_media", structured_output=True)
    def list_media(provider: str | None = None, q: str | None = None, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        try:
            return queries.list_media(provider=provider, q=q, limit=limit, offset=offset)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_media", structured_output=True)
    def get_media(media_id: str) -> dict[str, Any]:
        try:
            return queries.get_media(media_id)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="list_videos", structured_output=True)
    def list_videos(
        provider: str | None = None,
        media_id: str | None = None,
        playlist_id: str | None = None,
        status: str | None = None,
        q: str | None = None,
        published_since: str | None = None,
        published_until: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        try:
            return queries.list_videos(
                provider=provider,
                media_id=media_id,
                playlist_id=playlist_id,
                status=status,
                q=q,
                published_since=published_since,
                published_until=published_until,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_video", structured_output=True)
    def get_video(video_id: str) -> dict[str, Any]:
        try:
            return queries.get_video(video_id)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_video_transcript", structured_output=True)
    def get_video_transcript(video_id: str, chunk_index: int = 0, chunk_size: int = 12000) -> dict[str, Any]:
        try:
            return queries.get_video_transcript(video_id, chunk_index=chunk_index, chunk_size=chunk_size)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="list_video_assets", structured_output=True)
    def list_video_assets(
        video_id: str,
        type: str | None = None,
        language: str | None = None,
        variant: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            return queries.list_video_assets(video_id, type=type, language=language, variant=variant)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="list_playlists", structured_output=True)
    def list_playlists(limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        try:
            return queries.list_playlists(limit=limit, offset=offset)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_playlist", structured_output=True)
    def get_playlist(playlist_id: str) -> dict[str, Any]:
        try:
            return queries.get_playlist(playlist_id)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_playlist_videos", structured_output=True)
    def get_playlist_videos(playlist_id: str, date: str, limit: int = 50) -> list[dict[str, Any]]:
        try:
            return queries.get_playlist_videos(playlist_id, date=date, limit=limit)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="list_briefs", structured_output=True)
    def list_briefs(
        playlist_id: str | None = None,
        granularity: str | None = None,
        limit: int = 20,
        offset: int = 0,
        include_body: bool = False,
    ) -> list[dict[str, Any]]:
        try:
            return queries.list_briefs(
                playlist_id=playlist_id,
                granularity=granularity,
                limit=limit,
                offset=offset,
                include_body=include_body,
            )
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_brief", structured_output=True)
    def get_brief(brief_id: str, include_body: bool = True) -> dict[str, Any]:
        try:
            return queries.get_brief(brief_id, include_body=include_body)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_playlist_brief", structured_output=True)
    def get_playlist_brief(playlist_id: str, date: str, include_body: bool = True) -> dict[str, Any]:
        try:
            return queries.get_playlist_brief(playlist_id, date=date, include_body=include_body)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_playlist_latest_brief", structured_output=True)
    def get_playlist_latest_brief(playlist_id: str, include_body: bool = False) -> dict[str, Any]:
        try:
            return queries.get_playlist_latest_brief(playlist_id, include_body=include_body)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="list_latest_briefs", structured_output=True)
    def list_latest_briefs(limit: int = 20, offset: int = 0, include_body: bool = False) -> list[dict[str, Any]]:
        try:
            return queries.list_latest_briefs(limit=limit, offset=offset, include_body=include_body)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="list_jobs", structured_output=True)
    def list_jobs(
        status: str | None = None,
        status_in: str | None = None,
        type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        try:
            return queries.list_jobs(status=status, status_in=status_in, type=type, limit=limit, offset=offset)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_job", structured_output=True)
    def get_job(job_id: str) -> dict[str, Any]:
        try:
            return queries.get_job(job_id)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_video_context", structured_output=True)
    def get_video_context(video_id: str) -> dict[str, Any]:
        try:
            return queries.get_video_context(video_id)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_playlist_summary", structured_output=True)
    def get_playlist_summary(
        playlist_id: str,
        date: str,
        include_transcript: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        try:
            return queries.get_playlist_summary(
                playlist_id,
                date=date,
                include_transcript=include_transcript,
                limit=limit,
            )
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="list_domains", structured_output=True)
    def list_domains() -> list[dict[str, Any]]:
        try:
            return queries.list_domains()
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_domain_observation", structured_output=True)
    def get_domain_observation(playlist_id: str) -> dict[str, Any]:
        try:
            return queries.get_domain_observation(playlist_id)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_domain_changes", structured_output=True)
    def get_domain_changes(
        playlist_id: str,
        after_snapshot_id: str | None = None,
        object_type: str | None = None,
        change_type: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        try:
            return queries.get_domain_changes(
                playlist_id,
                after_snapshot_id=after_snapshot_id,
                object_type=object_type,
                change_type=change_type,
                cursor=cursor,
                limit=limit,
            )
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_canonical_history", structured_output=True)
    def get_canonical_history(playlist_id: str, canonical_id: str) -> dict[str, Any]:
        try:
            return queries.get_canonical_history(playlist_id, canonical_id)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_domain_topic", structured_output=True)
    def get_domain_topic(playlist_id: str, topic_id: str, limit: int = 50) -> dict[str, Any]:
        try:
            return queries.get_domain_topic(playlist_id, topic_id, limit=limit)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="list_domain_stories", structured_output=True)
    def list_domain_stories(playlist_id: str) -> list[dict[str, Any]]:
        try:
            return queries.list_domain_stories(playlist_id)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_story_history", structured_output=True)
    def get_story_history(playlist_id: str, story_identity_id: str) -> dict[str, Any]:
        try:
            return queries.get_story_history(playlist_id, story_identity_id)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_structured_brief", structured_output=True)
    def get_structured_brief(brief_id: str) -> dict[str, Any]:
        try:
            return queries.get_structured_brief(brief_id)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="get_evidence_context", structured_output=True)
    def get_evidence_context(playlist_id: str, revision_id: str) -> dict[str, Any]:
        try:
            return queries.get_evidence_context(playlist_id, revision_id)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="search_semantic_objects", structured_output=True)
    def search_semantic_objects(
        query: str,
        playlist_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        try:
            return queries.search_semantic_objects(query, playlist_id=playlist_id, limit=limit)
        except Exception as exc:
            _raise_tool_error(exc)

    if not include_actions:
        return

    @mcp.tool(name="sync_media", structured_output=True)
    def sync_media(media_id: str, scope: str = "recent") -> dict[str, Any]:
        try:
            return actions.sync_media(uuid.UUID(media_id), scope=scope)
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="download_video", structured_output=True)
    def download_video(video_id: str) -> dict[str, Any]:
        try:
            return actions.download_video(uuid.UUID(video_id))
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="retranscribe_video", structured_output=True)
    def retranscribe_video(video_id: str) -> dict[str, Any]:
        try:
            return actions.retranscribe_video(uuid.UUID(video_id))
        except Exception as exc:
            _raise_tool_error(exc)

    @mcp.tool(name="generate_brief", structured_output=True)
    def generate_brief(playlist_id: str, granularity: str, date: str) -> dict[str, Any]:
        try:
            return actions.generate_brief(uuid.UUID(playlist_id), granularity=granularity, date_in_period=dt_date.fromisoformat(date))
        except Exception as exc:
            _raise_tool_error(exc)
