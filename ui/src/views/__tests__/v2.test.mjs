import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createV2Module } from "../../app/modules/v2.js";
import { createV2ViewMethods } from "../v2-model.js";

function context(overrides = {}) {
  return {
    ...createV2Module(),
    ...createV2ViewMethods(),
    selectedPlaylistId: "domain-1",
    playlistEventMapSnapshotId: "snapshot-1",
    playlistEventMapWindowStart: "2026-01-01",
    playlistEventMapWindowEnd: "2026-12-31",
    playlistEventMapTypeFilter: "",
    ...overrides,
  };
}

test("星域变化默认收起，并优先打开第一个有内容的口径", () => {
  const ctx = context({
    domainObservationFeed: {
      newly_occurred: [],
      newly_mapped: [{ id: "change-1" }],
      story_updates: [],
      needs_review: [{ id: "review-1" }],
    },
  });
  assert.equal(ctx.fieldObservationRailOpen, false);
  assert.equal(ctx.fieldObservationRailTab, "newly_occurred");
  ctx.fieldToggleObservationRail();
  assert.equal(ctx.fieldObservationRailOpen, true);
  assert.equal(ctx.fieldObservationRailTab, "newly_mapped");
  assert.equal(ctx.fieldFeedTabs()[1].label, "认知变化");
});

test("旧版取景、旧快照、旧时间窗或不同视口的相机状态不会覆盖当前窗口取景", () => {
  const ctx = context({
    playlistEventMapController: () => ({ cameraState: () => ({ viewport_aspect: 2 }) }),
  });
  const compatible = {
    framing_version: 4,
    snapshot_id: "snapshot-1",
    window_start: "2026-01-01",
    window_end: "2026-12-31",
    viewport_aspect: 2.2,
  };
  assert.equal(ctx.fieldCameraStateCompatible(compatible), true);
  assert.equal(ctx.fieldCameraStateCompatible({ ...compatible, framing_version: 3 }), false);
  assert.equal(ctx.fieldCameraStateCompatible({ ...compatible, snapshot_id: "snapshot-old" }), false);
  assert.equal(ctx.fieldCameraStateCompatible({ ...compatible, viewport_aspect: 3 }), false);
});

test("星域首屏读取轻量游标，不请求昂贵的覆盖率摘要", async () => {
  const source = await readFile(new URL("../v2-model.js", import.meta.url), "utf8");
  const start = source.indexOf("async loadField()");
  const loadField = source.slice(start, source.indexOf("\n    leaveField()", start));
  assert.match(loadField, /observation\/cursor/);
  assert.doesNotMatch(loadField, /observation`/);
  assert.ok(loadField.indexOf("playlistEventMapLoadView") < loadField.indexOf("observation/feed"));
});

test("canonical 深链接通过当前快照历史定位，不把空 point_index 误当成第 0 点", async () => {
  const calls = [];
  const selected = [];
  const ctx = context({
    async api(path) {
      calls.push(path);
      return {
        revisions: [
          { snapshot_id: "snapshot-0", revision: { point_index: 1 } },
          { snapshot_id: "snapshot-1", revision: { point_index: 37 } },
        ],
      };
    },
    async playlistEventMapSelectCanonical(index, id) {
      selected.push([index, id]);
    },
  });

  await ctx.fieldOpenCanonical("canonical-1", null);

  assert.equal(calls.length, 1);
  assert.match(calls[0], /\/domains\/domain-1\/canonicals\/canonical-1\/history$/);
  assert.deepEqual(selected, [[37, "canonical-1"]]);
});

test("线性替代视图复用当前时间窗，并把类型码还原为事件类型值", async () => {
  let requested = "";
  const ctx = context({
    playlistEventMapTypeFilter: "4",
    playlistEventMapTypeOptions: () => [{ code: 4, value: "policy", label: "政策" }],
    async api(path) {
      requested = path;
      return { items: [{ canonical_id: "canonical-1", point_index: 8 }] };
    },
  });

  await ctx.fieldToggleLinearView();

  const url = new URL(`http://local${requested}`);
  assert.equal(url.pathname, "/domains/domain-1/canonicals");
  assert.equal(url.searchParams.get("event_time_start"), "2026-01-01T00:00:00Z");
  assert.equal(url.searchParams.get("event_time_end"), "2026-12-31T23:59:59Z");
  assert.equal(url.searchParams.get("event_type"), "policy");
  assert.equal(ctx.fieldLinearItems[0].point_index, 8);
});

test("证据深链接清理旧主题与星点选择，再读取可核验上下文", async () => {
  const calls = [];
  const controllerCalls = [];
  const ctx = context({
    activeView: "field",
    playlistEventMapTopicFocus: { topic_id: "topic-1" },
    playlistEventMapController: () => ({
      clearTopicFocus() { controllerCalls.push("topic"); },
      setSelection(index, detail) { controllerCalls.push([index, detail]); },
    }),
    playlistEventMapUpdateLayers() { controllerCalls.push("layers"); },
    _syncUrl() {},
    async api(path) {
      calls.push(path);
      return { revision_id: "evidence-1", verified: true };
    },
  });

  await ctx.fieldOpenEvidence("evidence-1");

  assert.equal(ctx.fieldMode, "verify");
  assert.equal(ctx.playlistEventMapSelectedKind, "evidence");
  assert.deepEqual(controllerCalls, ["topic", [null, null], "layers"]);
  assert.match(calls[0], /\/domains\/domain-1\/evidence\/evidence-1$/);
  assert.equal(ctx.fieldEvidenceDetail.verified, true);
});

test("故事版本差异区分初始版本与成员、关系、证据增减", () => {
  const ctx = context();
  assert.equal(ctx.storyRevisionDeltaLabel({ delta: { baseline: true } }), "初始版本");
  assert.equal(
    ctx.storyRevisionDeltaLabel({
      delta: {
        members_added: ["a"],
        members_removed: ["b"],
        edges_added: [{ edge_id: "e" }],
        edges_removed: [],
        evidence_added: ["v"],
        evidence_removed: [],
      },
    }),
    "+1 节点 · −1 节点 · +1 关系 · +1 证据"
  );
});

test("星域中的故事请求固定到 URL 指定的历史快照", async () => {
  const calls = [];
  const ctx = context({
    activeView: "field",
    fieldRequestedSnapshotId: "snapshot-history",
    async api(path) {
      calls.push(path);
      return { current_trajectory: { snapshot_id: "snapshot-history", nodes: [], edges: [] } };
    },
  });

  await ctx.loadStoryDetail("story-1");

  const url = new URL(`http://local${calls[0]}`);
  assert.equal(url.pathname, "/domains/domain-1/stories/story-1");
  assert.equal(url.searchParams.get("snapshot_id"), "snapshot-history");
});

test("星域变化中的退休对象切到最后包含它的快照并保留本次变化", async () => {
  const calls = [];
  const ctx = context({
    activeView: "field",
    _syncUrl() { calls.push("sync"); },
    async playlistEventMapLoadView(options) { calls.push(["load", options]); },
    async fieldOpenCanonical(id, pointIndex) { calls.push(["canonical", id, pointIndex]); },
  });
  const change = {
    id: "change-1",
    object_type: "canonical",
    object_id: "canonical-retired",
    change_type: "canonical_retired",
    from_snapshot_id: "snapshot-previous",
    to_snapshot_id: "snapshot-1",
    before_revision: { title: "退休前事件" },
    after_revision: null,
  };

  await ctx.fieldOpenFeedItem(change);

  assert.equal(ctx.fieldRequestedSnapshotId, "snapshot-previous");
  assert.deepEqual(calls, ["sync", ["load", { silent: false, schedulePolling: false }], ["canonical", "canonical-retired", undefined]]);
  assert.equal(ctx.fieldSelectedChange, change);
});

test("当前域移除信源只调用解除关联接口，不调用全局删除", async () => {
  const calls = [];
  globalThis.window = { confirm: () => true };
  const ctx = context({
    async api(path, options) { calls.push([path, options]); return { ok: true, detached: true }; },
    toastSuccess() {},
    async loadLibrary() {},
  });

  await ctx.removeSourceFromCurrentDomain({ id: "media-1", name: "测试信源" });

  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/domains/domain-1/sources/media-1");
  assert.equal(calls[0][1].method, "DELETE");
  assert.equal(calls.some(([path]) => path === "/media/media-1"), false);
});

test("V2 主界面保留四种观察模式、线性视图和完整故事航迹", async () => {
  const template = await readFile(new URL("../../../templates/app/views/field-v2.html", import.meta.url), "utf8");
  const controller = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  const navigation = await readFile(new URL("../../app/navigation.js", import.meta.url), "utf8");
  const domains = await readFile(new URL("../../../templates/app/views/domains-v2.html", import.meta.url), "utf8");
  const operations = await readFile(new URL("../../../templates/app/views/jobs.html", import.meta.url), "utf8");
  const stories = await readFile(new URL("../../../templates/app/views/stories.html", import.meta.url), "utf8");
  const playlistModel = await readFile(new URL("../event-map-model.js", import.meta.url), "utf8");

  assert.match(template, /\['now','replay','story','verify'\]/);
  assert.match(template, /线性列表/);
  assert.match(template, /真实事件 · 线性视图/);
  assert.doesNotMatch(template, /3D 空间|平面俯视|playlistEventMapCameraMode|playlistEventMapSetCameraMode/);
  assert.match(template, /三维语义空间 · 左键旋转/);
  assert.match(template, /星域变化/);
  assert.match(template, /返回当前星域/);
  assert.match(controller, /setStoryPath\(path\)/);
  assert.match(controller, /source_point_index/);
  assert.match(controller, /target_point_index/);
  assert.match(controller, /cameraState\(\)/);
  assert.match(controller, /restoreCameraState\(state\)/);
  assert.match(controller, /storyVisibleEdgeCount/);
  assert.match(domains, /preview_points/);
  assert.match(operations, /返回观测现场/);
  assert.match(stories, /storyRevisionDeltaLabel/);
  assert.match(playlistModel, /"briefs"/);
  assert.match(navigation, /key: "field"/);
  assert.match(navigation, /key: "stories"/);
  assert.match(navigation, /key: "briefs"/);
  assert.match(navigation, /key: "library"/);
  assert.match(navigation, /key: "operations"/);
  assert.doesNotMatch(template, /playlistSubview==='main'|Playlist Settings \(subview\)/);
});
