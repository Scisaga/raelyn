from __future__ import annotations

from pathlib import Path
import unittest


_ROOT = Path(__file__).resolve().parents[3]


class EventMapCleanupTests(unittest.TestCase):
    def test_active_code_does_not_reference_legacy_analysis_chain(self) -> None:
        forbidden = [
            "/" + "regime/",
            "Event" + "Regime",
            "EventGraph" + "ProjectionPoint",
            "playlist." + "mark_event_regime_dirty",
            "playlist." + "build_event_regime_snapshot",
            "playlist" + "Analysis",
        ]
        roots = [
            _ROOT / "backend" / "raelyn" / "api",
            _ROOT / "backend" / "raelyn" / "jobs",
            _ROOT / "backend" / "raelyn" / "services",
            _ROOT / "backend" / "raelyn" / "models.py",
            _ROOT / "ui" / "src",
            _ROOT / "ui" / "templates",
        ]
        findings: list[str] = []
        for root in roots:
            paths = [root] if root.is_file() else root.rglob("*")
            for path in paths:
                if not path.is_file() or "__tests__" in path.parts:
                    continue
                if path.suffix not in {".py", ".js", ".mjs", ".html"}:
                    continue
                text = path.read_text(encoding="utf-8")
                for token in forbidden:
                    if token in text:
                        findings.append(f"{path.relative_to(_ROOT)}: {token}")
        self.assertEqual(findings, [])

    def test_removed_graph_and_pseudo_projection_symbols_do_not_return(self) -> None:
        forbidden = [
            "/events/" + "graph",
            "playlist" + "Analysis" + "Graph",
            "playlist" + "Analysis" + "Projection" + "Points",
            "playlist" + "Analysis" + "Projection" + "Scale",
            "playlist" + "Analysis" + "Projection" + "Centroids",
            "焦点窗口周期语义" + "轨迹",
        ]
        roots = [
            _ROOT / "backend" / "raelyn",
            _ROOT / "ui" / "src",
            _ROOT / "ui" / "templates",
            _ROOT / "docs",
        ]
        excluded = {
            Path(__file__).resolve(),
            (_ROOT / "backend" / "raelyn" / "db.py").resolve(),
            (_ROOT / "backend" / "raelyn" / "tools" / "migrate_data.py").resolve(),
        }
        findings: list[str] = []
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or path.resolve() in excluded:
                    continue
                if path.suffix not in {".py", ".js", ".mjs", ".html", ".md"}:
                    continue
                text = path.read_text(encoding="utf-8")
                for token in forbidden:
                    if token in text:
                        findings.append(f"{path.relative_to(_ROOT)}: {token}")
        self.assertEqual(findings, [])

    def test_signal_model_and_api_do_not_expose_time_sequence_projection_fields(self) -> None:
        fields = [
            "projection_" + suffix
            for suffix in ("id", "x", "y", "z", "explained_variance_ratio")
        ]
        paths = [
            _ROOT / "backend" / "raelyn" / "models.py",
            _ROOT / "backend" / "raelyn" / "api" / "playlists.py",
        ]
        findings = [f"{path.relative_to(_ROOT)}: {field}" for path in paths for field in fields if field in path.read_text(encoding="utf-8")]
        self.assertEqual(findings, [])

    def test_built_ui_uses_current_event_map_entrypoint(self) -> None:
        bundle = (_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("playlistLoad" + "AnalysisView", bundle)
        self.assertIn("playlistEventMap" + "LoadView", bundle)

    def test_pinned_map_api_never_reads_live_event_entities(self) -> None:
        source = (_ROOT / "backend" / "raelyn" / "api" / "playlists.py").read_text(encoding="utf-8")
        start = source.index('@router.get("/playlists/{playlist_id}/events/map/manifest")')
        end = source.index('@router.get("/playlists/{playlist_id}/events/export")')
        map_api = source[start:end]

        self.assertNotIn("MarketEventEntity.", map_api)
        self.assertNotIn("_event_map_window_member_rows", source)
        self.assertIn("EventMapEntityIndex", map_api)


if __name__ == "__main__":
    unittest.main()
