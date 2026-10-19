from __future__ import annotations

from contextlib import contextmanager
import os
import unittest
import uuid
from datetime import timedelta

from sqlalchemy import event, insert, MetaData, select, text, update
from sqlalchemy.orm import Session

from raelyn.db import engine, session_scope
from raelyn.models import Base, EventMapStoryIdentity, EventMapStoryMember
from raelyn.services.event_map_snapshot import _update_current_identity_metadata
from raelyn.services.v2_observation import (
    canonical_detail, evidence_context, observation_feed, source_semantic_references,
    story_detail, story_directory, story_edge_support, structured_brief,
)


@unittest.skipUnless(
    os.getenv("RAELYN_RUN_REAL_DB_TESTS") == "1",
    "设置 RAELYN_RUN_REAL_DB_TESTS=1；复用真实星域构建产物，仅做只读查询",
)
class CurrentSemanticReferencesRealIntegrationTests(unittest.TestCase):
    @contextmanager
    def capture_queries(self, session):
        statements: list[str] = []
        connection = session.connection()

        def record_query(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.lower())

        event.listen(connection, "before_cursor_execute", record_query)
        try:
            yield statements
        finally:
            event.remove(connection, "before_cursor_execute", record_query)

    def real_source(self, session):
        if session.get_bind().dialect.name != "postgresql":
            self.skipTest("需要真实 PostgreSQL")
        session.execute(text("set transaction read only"))
        session.execute(text("set local statement_timeout = '8s'"))
        row = session.execute(text("""
            select st.playlist_id, st.current_snapshot_id as snapshot_id,
                   r.id as revision_id, r.source_video_id as video_id
            from event_map_state st
            join event_map_snapshot sn on sn.id = st.current_snapshot_id and sn.status = 'ready'
            join event_map_story_edge e on e.snapshot_id = sn.id
            join event_map_record_revision r
              on r.id = (e.evidence_revision_ids->>0)::uuid
            join video v on v.id = r.source_video_id
            join playlist_media pm on pm.media_id = v.media_id and pm.playlist_id = st.playlist_id
            where jsonb_array_length(e.evidence_revision_ids) > 0
            limit 1
        """)).mappings().first()
        if row is None:
            self.skipTest("没有带支持证据的 ready 故事；请先完成真实事件抽取和星域构建")
        return dict(row)

    def test_source_references_match_current_members_and_edges_without_history_reads(self):
        with session_scope() as session:
            source = self.real_source(session)
            with self.capture_queries(session) as statements:
                payload = source_semantic_references(session, source["playlist_id"], source["video_id"])

            self.assertFalse(any("event_map_canonical_history" in sql or "event_map_story_history" in sql for sql in statements))
            expected_canonicals = session.execute(text("""
                select distinct cm.canonical_id
                from event_map_canonical_member cm
                join event_map_record_revision r on r.id = cm.record_revision_id
                where cm.snapshot_id = :snapshot_id and r.source_video_id = :video_id
            """), source).scalars().all()
            self.assertEqual(
                {item["canonical_id"] for item in payload["canonical_references"]},
                {str(value) for value in expected_canonicals},
            )
            expected_edges = session.execute(text("""
                select distinct s.story_identity_id, e.edge_id
                from event_map_story_edge e
                join event_map_story s on s.snapshot_id = e.snapshot_id and s.story_id = e.story_id
                cross join lateral jsonb_array_elements_text(e.evidence_revision_ids) support(revision_id)
                join event_map_record_revision r on r.id = support.revision_id::uuid
                where e.snapshot_id = :snapshot_id and r.source_video_id = :video_id
            """), source).all()
            self.assertEqual(
                {(item["story_identity_id"], item["edge_id"]) for item in payload["story_references"]},
                {(str(identity), str(edge)) for identity, edge in expected_edges},
            )
            self.assertTrue(payload["story_references"])
            self.assertTrue(all(item["snapshot_id"] == str(source["snapshot_id"]) for item in payload["story_references"]))
            expected_briefs = session.execute(text("""
                select br.id from brief_reference br
                join brief b on b.id = br.brief_id
                join event_map_record_revision r on r.id = br.evidence_revision_id
                where b.playlist_id = :playlist_id and r.source_video_id = :video_id
            """), source).scalars().all()
            self.assertEqual(
                {item["id"] for item in payload["brief_references"]},
                {str(value) for value in expected_briefs},
            )

    def test_evidence_context_keeps_original_record_and_uses_current_relationships(self):
        with session_scope() as session:
            source = self.real_source(session)
            with self.capture_queries(session) as statements:
                payload = evidence_context(session, source["playlist_id"], source["revision_id"])
            self.assertFalse(any("event_map_canonical_history" in sql or "event_map_story_history" in sql for sql in statements))
            self.assertEqual(payload["revision_id"], str(source["revision_id"]))
            self.assertEqual(payload["source"]["video_id"], str(source["video_id"]))
            expected = session.execute(text("""
                select canonical_id from event_map_canonical_member
                where snapshot_id = :snapshot_id and record_revision_id = :revision_id
            """), source).scalars().all()
            self.assertEqual(set(payload["canonical_ids"]), {str(value) for value in expected})
            self.assertTrue(payload["story_references"])

    def test_current_story_detail_and_relation_support_use_live_graph(self):
        with session_scope() as session:
            source = self.real_source(session)
            edge = session.execute(text("""
                select s.story_identity_id, e.edge_id, e.source_canonical_id,
                       e.target_canonical_id, e.evidence_revision_ids
                from event_map_story_edge e join event_map_story s
                  on s.snapshot_id = e.snapshot_id and s.story_id = e.story_id
                where e.snapshot_id = :snapshot_id and e.evidence_revision_ids @> cast(:supports as jsonb)
                limit 1
            """), {**source, "supports": '["' + str(source["revision_id"]) + '"]'}).mappings().one()
            with self.capture_queries(session) as statements:
                detail = story_detail(session, source["playlist_id"], edge["story_identity_id"])
                support = story_edge_support(session, source["playlist_id"], edge["story_identity_id"],
                    edge["edge_id"], snapshot_id=source["snapshot_id"])
                canonical = canonical_detail(session, source["playlist_id"], edge["target_canonical_id"])
            self.assertFalse(any("_history_" in sql or "event_map_change" in sql for sql in statements))
            self.assertEqual(detail["trajectory"]["snapshot_id"], str(source["snapshot_id"]))
            self.assertIn(str(edge["edge_id"]), {row["edge_id"] for row in detail["trajectory"]["edges"]})
            self.assertEqual({row["record_revision_id"] for row in support["records"]}, set(edge["evidence_revision_ids"]))
            self.assertEqual(canonical["canonical_id"], str(edge["target_canonical_id"]))
            for removed in ("revisions", "changes", "reading_update", "material_history", "audit_summary"):
                self.assertNotIn(removed, detail)

    def test_current_feed_and_story_directory_do_not_scan_archives(self):
        with session_scope() as session:
            source = self.real_source(session)
            with self.capture_queries(session) as statements:
                feed = observation_feed(session, source["playlist_id"], limit=3)
                stories = story_directory(session, source["playlist_id"], limit=3)
            self.assertFalse(any("_history_" in sql or "event_map_change" in sql for sql in statements))
            self.assertTrue(stories["items"])
            self.assertTrue(all(row["snapshot_id"] == str(source["snapshot_id"]) for row in feed["story_updates"]))

    def test_migrated_brief_quotes_keep_original_evidence_without_history_tables(self):
        with session_scope() as session:
            self.real_source(session)
            old_tables = session.scalar(text("""select count(*) from information_schema.tables
                where table_schema = 'public' and table_name in (
                    'event_map_change', 'event_map_canonical_history_revision', 'event_map_canonical_history_member',
                    'event_map_story_history_revision', 'event_map_story_history_evidence')"""))
            if old_tables:
                self.skipTest("完成 finalize_current_event_map 迁移后验证引用")
            missing = session.scalar(text("""select count(*) from brief_reference r
                left join event_map_record_revision evidence on evidence.id = r.evidence_revision_id
                where r.object_type in ('canonical','story') and (
                    nullif(r.context->'quoted_object'->>'title','') is null or evidence.id is null
                    or r.context->>'source_video_id' is distinct from evidence.source_video_id::text
                    or (r.object_type = 'story' and r.context->>'focus_canonical_id' is distinct from
                        r.context->'quoted_object'->'relation'->>'target_canonical_id'))"""))
            self.assertEqual(missing, 0)
            brief_id = session.scalar(text("select brief_id from brief_reference where object_type = 'story' limit 1"))
            payload = structured_brief(session, brief_id)
            quoted = [row for row in payload["references"] if row["object_type"] in ('story','canonical')]
            self.assertTrue(quoted)
            self.assertTrue(all(row["context"]["quoted_object"]["title"] for row in quoted))
            self.assertTrue(all("snapshot_id=" not in row["web_url"] for row in quoted))
            for row in quoted:
                if row["object_type"] == "story":
                    self.assertIn("focus_canonical_id=" + row["context"]["focus_canonical_id"], row["web_url"])

    def test_replayed_real_projection_updates_only_current_metadata(self):
        """真实构建产物复制到隔离 schema；不写入业务域，结束后整笔回滚。"""
        names = (
            "playlist", "event_map_snapshot", "event_map_canonical_identity", "event_map_canonical",
            "event_map_canonical_member", "event_map_story_identity", "event_map_story",
            "event_map_story_member", "event_map_story_edge",
        )
        with session_scope() as source_session:
            source = self.real_source(source_session)
            story = source_session.execute(text("""select s.story_id, s.story_identity_id
                from event_map_story s where s.snapshot_id = :snapshot_id
                and exists (select 1 from event_map_story_edge e where e.snapshot_id = s.snapshot_id
                    and e.story_id = s.story_id) limit 1"""), source).mappings().one()
            canonical_ids = list(source_session.scalars(select(EventMapStoryMember.canonical_id).where(
                EventMapStoryMember.snapshot_id == source["snapshot_id"], EventMapStoryMember.story_id == story["story_id"],
            )))
            fixtures = {}
            for name in names:
                table = Base.metadata.tables[name]
                if name == "playlist":
                    clause = table.c.id == source["playlist_id"]
                elif name == "event_map_snapshot":
                    clause = table.c.id == source["snapshot_id"]
                elif name == "event_map_canonical_identity":
                    clause = table.c.id.in_(canonical_ids)
                elif name == "event_map_story_identity":
                    clause = table.c.id == story["story_identity_id"]
                elif name.startswith("event_map_canonical"):
                    clause = (table.c.snapshot_id == source["snapshot_id"]) & table.c.canonical_id.in_(canonical_ids)
                else:
                    clause = (table.c.snapshot_id == source["snapshot_id"]) & (table.c.story_id == story["story_id"])
                fixtures[name] = [dict(row) for row in source_session.execute(select(table).where(clause)).mappings()]

        schema = "test_current_projection_" + uuid.uuid4().hex
        metadata = MetaData()
        for name in names:
            table = Base.metadata.tables[name].to_metadata(metadata, schema=schema)
            # 此测试验证业务更新，不验证外部来源 FK；只复制被测的真实投影。
            for constraint in list(table.foreign_key_constraints):
                table.constraints.remove(constraint)
            table.foreign_keys.clear()
            for column in table.columns:
                column.foreign_keys.clear()
        replay_snapshot_id = uuid.uuid4()
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(text(f'create schema "{schema}"'))
                metadata.create_all(conn)
                for name, rows in fixtures.items():
                    conn.execute(insert(metadata.tables[f"{schema}.{name}"]), rows)
                    if name == "event_map_snapshot":
                        replay = [{**row, "id": replay_snapshot_id, "build_key": uuid.uuid4().hex,
                                   "execution_token": uuid.uuid4(),
                                   "parent_snapshot_id": source["snapshot_id"]} for row in rows]
                        conn.execute(insert(metadata.tables[f"{schema}.{name}"]), replay)
                    elif "snapshot_id" in Base.metadata.tables[name].c:
                        replay = [{**row, "snapshot_id": replay_snapshot_id} for row in rows]
                        conn.execute(insert(metadata.tables[f"{schema}.{name}"]), replay)
                with Session(bind=conn.execution_options(schema_translate_map={None: schema})) as session:
                    identity = session.get(EventMapStoryIdentity, story["story_identity_id"])
                    assert identity is not None
                    baseline = identity.last_material_changed_at
                    observed = baseline + timedelta(minutes=1)
                    with self.capture_queries(session) as statements:
                        _update_current_identity_metadata(session, playlist_id=source["playlist_id"],
                            snapshot_id=replay_snapshot_id, parent_snapshot_id=source["snapshot_id"], observed_at=observed)
                    session.expire_all()
                    self.assertEqual(identity.last_material_changed_at, baseline)
                    session.execute(update(EventMapStoryMember).where(
                        EventMapStoryMember.snapshot_id == replay_snapshot_id,
                        EventMapStoryMember.story_id == story["story_id"],
                    ).values(position=-EventMapStoryMember.position))
                    _update_current_identity_metadata(session, playlist_id=source["playlist_id"],
                        snapshot_id=replay_snapshot_id, parent_snapshot_id=source["snapshot_id"], observed_at=observed)
                    session.expire_all()
                    self.assertEqual(identity.last_material_changed_at, observed)
                    self.assertFalse(any("_history_" in sql or "event_map_change" in sql for sql in statements))
            finally:
                transaction.rollback()


if __name__ == "__main__":
    unittest.main()
