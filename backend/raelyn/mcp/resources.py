from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ResourceError

from raelyn.mcp import queries


def _raise_resource_error(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise ResourceError(f"not_found: {exc}") from exc
    if isinstance(exc, ValueError):
        raise ResourceError(f"invalid_argument: {exc}") from exc
    raise ResourceError(str(exc)) from exc


def register_resources(mcp: FastMCP) -> None:
    @mcp.resource("raelyn://media/{media_id}", mime_type="application/json")
    def media_resource(media_id: str):
        try:
            return queries.get_media(media_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://video/{video_id}", mime_type="application/json")
    def video_resource(video_id: str):
        try:
            return queries.get_video(video_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://video/{video_id}/transcript", mime_type="application/json")
    def video_transcript_resource(video_id: str):
        try:
            return queries.get_video_transcript(video_id, chunk_index=0, chunk_size=12000)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://video/{video_id}/transcript/chunks/{chunk_index}", mime_type="application/json")
    def video_transcript_chunk_resource(video_id: str, chunk_index: int):
        try:
            return queries.get_video_transcript(video_id, chunk_index=chunk_index, chunk_size=12000)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://video/{video_id}/assets", mime_type="application/json")
    def video_assets_resource(video_id: str):
        try:
            return queries.list_video_assets(video_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://playlist/{playlist_id}", mime_type="application/json")
    def playlist_resource(playlist_id: str):
        try:
            return queries.get_playlist(playlist_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://brief/{playlist_id}/{granularity}/{date_in_period}", mime_type="application/json")
    def brief_resource(playlist_id: str, granularity: str, date_in_period: str):
        try:
            return queries.get_brief(playlist_id, granularity=granularity, date_in_period=date_in_period)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://job/{job_id}", mime_type="application/json")
    def job_resource(job_id: str):
        try:
            return queries.get_job(job_id)
        except Exception as exc:
            _raise_resource_error(exc)
