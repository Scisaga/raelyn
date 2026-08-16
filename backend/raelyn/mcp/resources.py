from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ResourceError

from raelyn.mcp import queries


def _raise_resource_error(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise ResourceError(f"not_found: {exc}") from exc
    if isinstance(exc, ValueError):
        raise ResourceError(f"invalid_argument: {exc}") from exc
    if isinstance(exc, RuntimeError):
        raise ResourceError(f"not_ready: {exc}") from exc
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

    @mcp.resource("raelyn://brief/{brief_id}", mime_type="application/json")
    def brief_resource(brief_id: str):
        try:
            return queries.get_brief(brief_id, include_body=False)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://brief/{brief_id}/body", mime_type="text/markdown")
    def brief_body_resource(brief_id: str):
        try:
            return queries.read_brief_body(brief_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://playlist/{playlist_id}/briefs/by-date/{date}", mime_type="application/json")
    def playlist_brief_resource(playlist_id: str, date: str):
        try:
            return queries.get_playlist_brief(playlist_id, date=date, include_body=False)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://playlist/{playlist_id}/briefs/by-date/{date}/body", mime_type="text/markdown")
    def playlist_brief_body_resource(playlist_id: str, date: str):
        try:
            return queries.read_playlist_brief_body(playlist_id, date=date)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://job/{job_id}", mime_type="application/json")
    def job_resource(job_id: str):
        try:
            return queries.get_job(job_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://domain/{playlist_id}/observation", mime_type="application/json")
    def domain_observation_resource(playlist_id: str):
        try:
            return queries.get_domain_observation(playlist_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://domain/{playlist_id}/changes", mime_type="application/json")
    def domain_changes_resource(playlist_id: str):
        try:
            return queries.get_domain_changes(playlist_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://domain/{playlist_id}/canonical/{canonical_id}", mime_type="application/json")
    def canonical_history_resource(playlist_id: str, canonical_id: str):
        try:
            return queries.get_canonical_history(playlist_id, canonical_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://domain/{playlist_id}/topic/{topic_id}", mime_type="application/json")
    def domain_topic_resource(playlist_id: str, topic_id: str):
        try:
            return queries.get_domain_topic(playlist_id, topic_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://domain/{playlist_id}/story/{story_identity_id}", mime_type="application/json")
    def story_history_resource(playlist_id: str, story_identity_id: str):
        try:
            return queries.get_story_history(playlist_id, story_identity_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://brief/{brief_id}/structured", mime_type="application/json")
    def structured_brief_resource(brief_id: str):
        try:
            return queries.get_structured_brief(brief_id)
        except Exception as exc:
            _raise_resource_error(exc)

    @mcp.resource("raelyn://domain/{playlist_id}/evidence/{revision_id}", mime_type="application/json")
    def evidence_context_resource(playlist_id: str, revision_id: str):
        try:
            return queries.get_evidence_context(playlist_id, revision_id)
        except Exception as exc:
            _raise_resource_error(exc)
