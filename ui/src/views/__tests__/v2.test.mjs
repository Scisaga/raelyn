import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createV2Module } from "../../app/modules/v2.js";
import { createPlaylistsModule } from "../../app/modules/playlists.js";
import { createShellModule } from "../../app/modules/shell.js";
import { createAppInitMethods } from "../../app/init-model.js";
import { createPlaylistsViewMethods } from "../playlists-model.js";
import { createV2ViewMethods } from "../v2-model.js";
import { createCommonViewMethods } from "../../shared/view-helpers.js";

function context(overrides = {}) {
  return {
    ...createV2Module(),
    ...createCommonViewMethods(),
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

test("打开星域变化会返回星域并关闭临时工具", () => {
  const ctx = context({
    fieldLinearView: true,
    playlistEventMapSearchOpen: true,
    playlistEventMapFiltersOpen: true,
    playlistEventMapCloseSearch() { this.playlistEventMapSearchOpen = false; },
  });

  ctx.fieldToggleObservationRail();

  assert.equal(ctx.fieldLinearView, false);
  assert.equal(ctx.fieldObservationRailOpen, true);
  assert.equal(ctx.playlistEventMapSearchOpen, false);
  assert.equal(ctx.playlistEventMapFiltersOpen, false);
});

test("对象详情与星域变化复用同一轨道并提供明确返回动作", () => {
  const fromChanges = context({
    fieldObservationRailOpen: true,
    playlistEventMapSelectedKind: "topic",
    playlistEventMapSelectedId: "topic-1",
    playlistEventMapClearTopicFocus() {
      this.playlistEventMapSelectedKind = "";
      this.playlistEventMapSelectedId = "";
    },
  });

  assert.equal(fromChanges.fieldObservationRailVisible(), false);
  assert.equal(fromChanges.fieldObservationRailActionLabel(), "返回变化");
  assert.equal(fromChanges.fieldInspectorCloseLabel(), "返回变化");

  fromChanges.fieldToggleObservationRail();

  assert.equal(fromChanges.playlistEventMapSelectedId, "");
  assert.equal(fromChanges.fieldObservationRailOpen, true);
  assert.equal(fromChanges.fieldObservationRailVisible(), true);

  const directDetail = context({
    fieldObservationRailOpen: false,
    playlistEventMapSelectedKind: "canonical",
    playlistEventMapSelectedId: "canonical-1",
    playlistEventMapClearSelection() {
      this.playlistEventMapSelectedKind = "";
      this.playlistEventMapSelectedId = "";
    },
  });

  assert.equal(directDetail.fieldObservationRailActionLabel(), "星域变化");
  assert.equal(directDetail.fieldInspectorCloseLabel(), "关闭");

  directDetail.fieldToggleObservationRail();

  assert.equal(directDetail.playlistEventMapSelectedId, "");
  assert.equal(directDetail.fieldObservationRailVisible(), true);
});

test("旧版取景、旧快照、旧时间窗或不同视口的相机状态不会覆盖当前窗口取景", () => {
  const ctx = context({
    playlistEventMapController: () => ({ cameraState: () => ({ viewport_aspect: 2 }) }),
  });
  const compatible = {
    framing_version: 5,
    snapshot_id: "snapshot-1",
    window_start: "2026-01-01",
    window_end: "2026-12-31",
    viewport_aspect: 2.2,
  };
  assert.equal(ctx.fieldCameraStateCompatible(compatible), true);
  assert.equal(ctx.fieldCameraStateCompatible({ ...compatible, framing_version: 4 }), false);
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
  assert.ok(loadField.indexOf("const mapPromise = this.playlistEventMapLoadView()") < loadField.indexOf("/detail`"));
  assert.ok(loadField.indexOf("playlistEventMapUpdateLayers()") < loadField.indexOf("await mapPromise"));
  assert.match(loadField, /_attachInitialFieldVisual\?\.\(mapPromise\)/);
  assert.match(loadField, /playlistEventMapWaitForCompleteScene\(\{ includeMetadata: Boolean\(topicId\) \}\)/);
  assert.match(loadField, /const fieldRequestToken = Number\(this\._fieldLoadRequestToken/);
  assert.match(loadField, /String\(this\.fieldRequestedSnapshotId \|\| ""\) === requestedSnapshotId/);
});

test("没有历史观测域时也会在系统检查前单飞启动星图", async () => {
  let refreshCount = 0;
  let finishRefresh;
  const refreshPromise = new Promise((resolve) => { finishRefresh = resolve; });
  const ctx = {
    ...createV2Module(),
    ...createAppInitMethods(),
    activeView: "field",
    selectedPlaylistId: null,
    playlistPageId: null,
    playlistSubview: "main",
    playlistEventMapLoading: false,
    playlistEventMapController() { return null; },
    refreshActive(options) {
      assert.deepEqual(options, { throwOnError: true });
      refreshCount += 1;
      return refreshPromise;
    },
  };

  const first = ctx._startInitialFieldMap();
  const second = ctx._startInitialFieldMap();

  assert.equal(refreshCount, 1);
  assert.equal(first, refreshPromise);
  assert.equal(second, refreshPromise);
  assert.equal(ctx.playlistSubview, "analysis");
  assert.equal(ctx.playlistEventMapLoading, true);
  finishRefresh();
  await first;
});

test("首次星图鉴权失败后不会复用失效的启动请求", async () => {
  let refreshCount = 0;
  const ctx = {
    ...createV2Module(),
    ...createAppInitMethods(),
    activeView: "field",
    selectedPlaylistId: null,
    playlistPageId: null,
    playlistEventMapLoading: false,
    playlistEventMapController() { return null; },
    refreshActive() {
      refreshCount += 1;
      return refreshCount === 1
        ? Promise.reject(new Error("401: unauthorized"))
        : Promise.resolve("ready");
    },
  };

  await assert.rejects(ctx._startInitialFieldMap(), /401/);
  await Promise.resolve();
  assert.equal(ctx._initialFieldLoadPromise, null);
  assert.equal(await ctx._startInitialFieldMap(), "ready");
  assert.equal(refreshCount, 2);
});

test("星域完整点集与标签完成前不启动非可视首屏请求", async () => {
  let finishHydration;
  const hydrationPromise = new Promise((resolve) => { finishHydration = resolve; });
  const calls = [];
  const ctx = {
    ...createV2Module(),
    ...createAppInitMethods(),
    activeView: "field",
    playlistEventMapSnapshotId: "snapshot-1",
    _initialFieldLoadPromise: Promise.resolve("first-visual"),
    initMediaSession() { calls.push("media-session"); },
    _resumeProtectedRealtime() { calls.push("realtime"); },
    async playlistEventMapWaitForCompleteScene(options) {
      calls.push(["hydrate", options]);
      await hydrationPromise;
    },
    initPwa() { calls.push("pwa"); },
    _refreshHealthStatus() { calls.push("health"); },
    loadMediaIndex(options) { calls.push(["media-index", options]); return Promise.resolve([]); },
    loadStats(options) { calls.push(["stats", options]); return Promise.resolve({}); },
    loadDomains(options) { calls.push(["domains", options]); return Promise.resolve([]); },
    _reconcileRestoredFieldDomain() { calls.push("reconcile"); return Promise.resolve(false); },
  };

  await ctx._finishFieldStartup({ initializePwa: true });
  await Promise.resolve();

  assert.deepEqual(calls, [
    "media-session",
    "realtime",
    ["hydrate", { includeMetadata: true }],
  ]);

  finishHydration();
  await hydrationPromise;
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [
    "media-session",
    "realtime",
    ["hydrate", { includeMetadata: true }],
    "pwa",
    "health",
    ["media-index", { lightweight: true }],
    ["stats", { silent: true }],
    ["domains", { force: true }],
    "reconcile",
  ]);
});

test("无本地域时以真实星图首帧而不是整页请求驱动后台水合", async () => {
  let finishField;
  let finishVisual;
  let finishHydration;
  const fieldPromise = new Promise((resolve) => { finishField = resolve; });
  const visualPromise = new Promise((resolve) => { finishVisual = resolve; });
  const hydrationPromise = new Promise((resolve) => { finishHydration = resolve; });
  const calls = [];
  const ctx = {
    ...createV2Module(),
    ...createAppInitMethods(),
    activeView: "field",
    playlistEventMapSnapshotId: "snapshot-1",
    _initialFieldLoadPromise: fieldPromise,
    _initialFieldVisualPromise: visualPromise,
    initMediaSession() {},
    _resumeProtectedRealtime() {},
    async playlistEventMapWaitForCompleteScene() {
      calls.push("hydrate");
      await hydrationPromise;
    },
    loadMediaIndex() { calls.push("media-index"); return Promise.resolve([]); },
    loadDomains() { calls.push("domains"); return Promise.resolve([]); },
    _reconcileRestoredFieldDomain() { return Promise.resolve(false); },
  };

  const startupPromise = ctx._finishFieldStartup({ healthReady: true, statsReady: true });
  await Promise.resolve();
  assert.deepEqual(calls, []);

  finishVisual();
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(calls, ["hydrate"]);

  finishHydration();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, ["hydrate", "media-index", "domains"]);

  finishField();
  await startupPromise;
});

test("本地恢复的观测域已删除时，目录返回后切到有效默认域", async () => {
  let resetCount = 0;
  let refreshCount = 0;
  const ctx = {
    ...createV2Module(),
    ...createAppInitMethods(),
    activeView: "field",
    selectedPlaylistId: "domain-removed",
    playlistPageId: "domain-removed",
    _initialFieldDomainRestoredFromStorage: true,
    playlistEventMapReset() { resetCount += 1; },
    async ensureCurrentDomain() {
      this.selectedPlaylistId = "domain-ready";
      this.playlistPageId = "domain-ready";
      return "domain-ready";
    },
    async refreshActive() { refreshCount += 1; },
    _syncUrl() {},
  };

  const reconciled = await ctx._reconcileRestoredFieldDomain([{ id: "domain-ready" }]);

  assert.equal(reconciled, true);
  assert.equal(ctx.selectedPlaylistId, "domain-ready");
  assert.equal(ctx.playlistPageId, "domain-ready");
  assert.equal(resetCount, 1);
  assert.equal(refreshCount, 1);
  assert.equal(ctx._initialFieldDomainRestoredFromStorage, false);
});

test("域目录延后返回时，即使已经离开星域也会修正失效的本地域", async () => {
  let refreshCount = 0;
  const ctx = {
    ...createV2Module(),
    ...createAppInitMethods(),
    activeView: "stories",
    selectedPlaylistId: "domain-removed",
    playlistPageId: "domain-removed",
    _initialFieldDomainRestoredFromStorage: true,
    playlistEventMapReset() {},
    async ensureCurrentDomain() {
      this.selectedPlaylistId = "domain-ready";
      this.playlistPageId = "domain-ready";
      return "domain-ready";
    },
    async refreshActive() { refreshCount += 1; },
    _syncUrl() {},
  };

  const reconciled = await ctx._reconcileRestoredFieldDomain([{ id: "domain-ready" }]);

  assert.equal(reconciled, true);
  assert.equal(ctx.selectedPlaylistId, "domain-ready");
  assert.equal(ctx.playlistPageId, "domain-ready");
  assert.equal(refreshCount, 0);
  assert.equal(ctx._initialFieldDomainRestoredFromStorage, false);
});

test("canonical 深链接通过当前快照历史定位", async () => {
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

test("canonical 历史与搜索的空 point_index 都不会误选第 0 点", async () => {
  const calls = [];
  const selected = [];
  const ctx = context({
    activeView: "field",
    async api(path) {
      calls.push(path);
      if (path.includes("/history")) {
        return { revisions: [{ snapshot_id: "snapshot-1", revision: { point_index: null } }] };
      }
      return { items: [{ canonical_id: "canonical-1", point_index: null }] };
    },
    async playlistEventMapSelectCanonical(index, id) {
      selected.push([index, id]);
    },
  });

  await ctx.fieldOpenCanonical("canonical-1", null);

  assert.equal(calls.length, 2);
  assert.match(calls[0], /\/history$/);
  assert.match(calls[1], /\/events\/map\/search\?/);
  assert.deepEqual(selected, []);
});

test("canonical 定位等待期间出现新星域请求时不提交旧选择", async () => {
  let releaseHistory;
  const selected = [];
  const ctx = context({
    activeView: "field",
    _fieldLoadRequestToken: 1,
    api: () => new Promise((resolve) => { releaseHistory = resolve; }),
    async playlistEventMapSelectCanonical(index, id) {
      selected.push([index, id]);
    },
  });

  const pending = ctx.fieldOpenCanonical("canonical-old", null, {
    requestGuard: () => ctx._fieldLoadRequestToken === 1,
  });
  await Promise.resolve();
  ctx._fieldLoadRequestToken = 2;
  releaseHistory({ revisions: [{ snapshot_id: "snapshot-1", revision: { point_index: 9 } }] });
  await pending;

  assert.deepEqual(selected, []);
});

test("事件列表复用当前时间窗、类型和实体筛选", async () => {
  let requested = "";
  const ctx = context({
    playlistEventMapTypeFilter: "4",
    playlistEventMapEntityFilter: {
      normalized_key: "apple",
      entity_type: "company",
    },
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
  assert.equal(url.searchParams.get("normalized_key"), "apple");
  assert.equal(url.searchParams.get("entity_type"), "company");
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

test("关闭证据后，延迟返回的旧证据不会重新打开检查器", async () => {
  let releaseEvidence;
  const ctx = context({
    activeView: "field",
    playlistEventMapController: () => ({ clearTopicFocus() {}, setSelection() {} }),
    playlistEventMapUpdateLayers() {},
    _syncUrl() {},
    api: () => new Promise((resolve) => { releaseEvidence = resolve; }),
  });

  const pending = ctx.fieldOpenEvidence("evidence-old");
  await Promise.resolve();
  ctx.fieldCloseEvidence();
  releaseEvidence({ revision_id: "evidence-old" });
  await pending;

  assert.equal(ctx.fieldEvidenceDetail, null);
  assert.equal(ctx.playlistEventMapSelectedKind, "");
  assert.equal(ctx.fieldEvidenceLoading, false);
});

test("故事定位期间出现更新选择时，不再覆盖详情标签和镜头路径", async () => {
  let releaseCanonical;
  const calls = [];
  const ctx = context({
    activeView: "field",
    async loadStoryDetail(id) {
      this.storySelectedId = id;
      this._storyDetailRequestToken += 1;
      this.storyDetail = {
        revisions: [{ snapshot_id: "snapshot-1", member_ids: ["canonical-old"] }],
        current_trajectory: {
          snapshot_id: "snapshot-1",
          nodes: [{ canonical_id: "canonical-old", point_index: 8 }],
          edges: [],
        },
      };
    },
    fieldOpenCanonical: () => new Promise((resolve) => { releaseCanonical = resolve; }),
    playlistEventMapSetDetailTab: (tab) => { calls.push(["tab", tab]); },
    playlistEventMapController: () => ({ setStoryPath: (path) => { calls.push(["path", path]); } }),
    playlistEventMapFitIndices: (indices) => { calls.push(["fit", [...indices]]); },
  });

  const pending = ctx.fieldOpenStory("story-old");
  await Promise.resolve();
  ctx._playlistEventMapSelectionToken += 1;
  releaseCanonical(false);
  await pending;

  assert.deepEqual(calls, []);
});

test("星域里的故事详情等待期间出现更新选择时，不提交旧故事正文", async () => {
  let releaseStory;
  const ctx = context({
    activeView: "field",
    _playlistEventMapSelectionToken: 0,
    storyCloseInspector() {},
    storyScheduleInitialPosition() {},
    storySuggestedStartIndex: () => 0,
    api: () => new Promise((resolve) => { releaseStory = resolve; }),
  });

  const pending = ctx.loadStoryDetail("story-old");
  await Promise.resolve();
  ctx._playlistEventMapSelectionToken = Number(ctx._playlistEventMapSelectionToken || 0) + 1;
  releaseStory({ story_identity_id: "story-old", current_trajectory: { nodes: [], edges: [] } });
  await pending;

  assert.equal(ctx.storyDetail, null);
  assert.equal(ctx.storyDetailLoading, false);
});

test("故事轨迹取景忽略空 point_index", async () => {
  const fitted = [];
  const ctx = context({
    activeView: "field",
    async loadStoryDetail(id) {
      this.storySelectedId = id;
      this._storyDetailRequestToken += 1;
      this.storyDetail = {
        revisions: [{ snapshot_id: "snapshot-1", member_ids: ["canonical-8"] }],
        current_trajectory: {
          snapshot_id: "snapshot-1",
          nodes: [
            { canonical_id: "canonical-null", point_index: null },
            { canonical_id: "canonical-8", point_index: 8 },
          ],
          edges: [],
        },
      };
    },
    async fieldOpenCanonical() { return true; },
    playlistEventMapSetDetailTab() {},
    playlistEventMapController: () => ({ setStoryPath() {} }),
    playlistEventMapFitIndices: (indices) => { fitted.push([...indices]); },
  });

  await ctx.fieldOpenStory("story-1");

  assert.deepEqual(fitted, [[8]]);
});

test("故事识别记录区分首次识别、真实变化与重复观测", () => {
  const ctx = context();
  assert.equal(ctx.storyRevisionDeltaLabel({ delta: { baseline: true } }), "首次识别到这条故事");
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
    "新增 1 个事件 · 移除 1 个事件 · 新增 1 条关系 · 新增 1 份支持记录"
  );
  assert.equal(ctx.storyRevisionDeltaLabel({ delta: {} }), "本次识别未发现实质变化");
  assert.equal(ctx.storyRevisionDeltaLabel({ delta: { title_changed: true, summary_changed: true } }), "本次识别未发现实质变化");
  assert.match(ctx.storyRevisionDeltaExplanation({ delta: {} }), /事实结构没有变化/);
});

test("故事目录默认读取值得阅读，并让四类队列各自保存搜索和分页", async () => {
  const calls = [];
  const ctx = context({
    async ensureCurrentDomain() { return "domain-1"; },
    async api(path) {
      calls.push(path);
      const url = new URL(`http://local${path}`);
      const offset = Number(url.searchParams.get("offset"));
      return offset === 0
        ? {
            items: [{ story_identity_id: "story-1" }, { story_identity_id: "story-2" }],
            total: 3,
            has_more: true,
          }
        : {
            items: [{ story_identity_id: "story-3" }],
            total: 3,
            has_more: false,
          };
    },
  });

  await ctx.loadStories();
  await ctx.loadMoreStories();

  const first = new URL(`http://local${calls[0]}`);
  const second = new URL(`http://local${calls[1]}`);
  assert.equal(first.searchParams.get("limit"), "80");
  assert.equal(first.searchParams.get("offset"), "0");
  assert.equal(first.searchParams.get("scope"), "attention");
  assert.equal(second.searchParams.get("offset"), "2");
  assert.deepEqual(ctx.stories.map((story) => story.story_identity_id), ["story-1", "story-2", "story-3"]);
  assert.equal(ctx.storiesTotal, 3);
  assert.equal(ctx.storiesHasMore, false);
  assert.equal(ctx.storyDetail, null, "打开目录不得自动打开或标记首条故事");

  ctx.storyQueueQueries.followed = "供应链";
  await ctx.storySetScope("followed");
  const followed = new URL(`http://local${calls.at(-1)}`);
  assert.equal(followed.searchParams.get("scope"), "followed");
  assert.equal(followed.searchParams.get("q"), "供应链");
  assert.deepEqual(ctx.storyQueues.attention.items.map((story) => story.story_identity_id), ["story-1", "story-2", "story-3"]);
});

test("并发加载观测域会等待同一请求并恢复最近有效域", async () => {
  const previousWindow = globalThis.window;
  const previousLocalStorage = globalThis.localStorage;
  let resolveDomains;
  let domainRequests = 0;
  const stored = new Map([["raelyn.v2.currentDomainId", "domain-saved"]]);
  globalThis.window = { location: { search: "" } };
  globalThis.localStorage = {
    getItem(key) { return stored.get(key) || null; },
    setItem(key, value) { stored.set(key, String(value)); },
  };

  try {
    const syncs = [];
    const ctx = context({
      selectedPlaylistId: null,
      playlistPageId: null,
      domains: [],
      _syncUrl(options) { syncs.push(options); },
      api(path) {
        assert.equal(path, "/domains");
        domainRequests += 1;
        return new Promise((resolve) => { resolveDomains = resolve; });
      },
    });

    const initialLoad = ctx.loadDomains();
    const ensured = ctx.ensureCurrentDomain();
    await Promise.resolve();
    assert.equal(domainRequests, 1, "并发调用不得把尚未完成的域列表当作空结果");

    resolveDomains({
      items: [
        { id: "domain-default", snapshot: { status: "ready" } },
        { id: "domain-saved", snapshot: { status: "ready" } },
      ],
    });
    await initialLoad;
    assert.equal(await ensured, "domain-saved");
    assert.equal(ctx.selectedPlaylistId, "domain-saved");
    assert.deepEqual(syncs, [{ push: false }]);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
    if (previousLocalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousLocalStorage;
  }
});

test("星域首屏先取轻量域目录，完整目录在同一请求结束后再升级", async () => {
  let resolveCompact;
  const calls = [];
  const ctx = context({
    domains: [],
    async api(path) {
      calls.push(path);
      if (path === "/domains?compact=true") {
        return await new Promise((resolve) => { resolveCompact = resolve; });
      }
      assert.equal(path, "/domains");
      return { items: [{ id: "domain-1", name: "完整目录", media_preview: [] }] };
    },
  });

  const compact = ctx.loadDomains({ compact: true });
  const full = ctx.loadDomains();
  await Promise.resolve();
  assert.deepEqual(calls, ["/domains?compact=true"]);

  resolveCompact({ items: [{ id: "domain-1", name: "轻量目录" }] });
  assert.equal((await compact)[0].name, "轻量目录");
  assert.equal((await full)[0].name, "完整目录");
  assert.deepEqual(calls, ["/domains?compact=true", "/domains"]);
  assert.equal(ctx._domainsComplete, true);
});

test("无有效历史域时选择首个可读域，未选域和加载失败不伪装成空队列", async () => {
  const previousWindow = globalThis.window;
  const previousLocalStorage = globalThis.localStorage;
  globalThis.window = { location: { search: "" } };
  globalThis.localStorage = {
    getItem() { return "domain-removed"; },
    setItem() {},
  };

  try {
    const fallback = context({
      selectedPlaylistId: null,
      playlistPageId: null,
      domains: [],
      _syncUrl() {},
      async api(path) {
        assert.equal(path, "/domains");
        return {
          items: [
            { id: "domain-building", snapshot: { status: "building" } },
            { id: "domain-ready", snapshot: { status: "ready" } },
          ],
        };
      },
    });
    assert.equal(await fallback.ensureCurrentDomain(), "domain-ready");

    const missing = context({
      selectedPlaylistId: null,
      async ensureCurrentDomain() { return ""; },
    });
    await missing.loadStories();
    assert.equal(missing.storiesDomainMissing, true);
    assert.equal(missing.storiesError, "");

    const failed = context({
      selectedPlaylistId: null,
      async ensureCurrentDomain() { throw new Error("域目录不可用"); },
    });
    await failed.loadStories();
    assert.equal(failed.storiesDomainMissing, false);
    assert.equal(failed.storiesError, "域目录不可用");

    const template = await readFile(new URL("../../../templates/app/views/stories.html", import.meta.url), "utf8");
    assert.match(template, /storiesDomainMissing/);
    assert.match(template, /故事目录加载失败/);
    assert.match(template, /当前没有新的实质更新/);
    assert.match(template, /storySetScope\('established'\)/);
    assert.match(template, /storySetScope\('emerging'\)/);
    assert.match(template, /!storiesDomainMissing && !storiesError && !stories\.length && storyScope==='attention'/);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
    if (previousLocalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousLocalStorage;
  }
});

test("观测域切换器搜索名称与描述，并以当前域作为键盘起点", () => {
  const ctx = context({
    selectedPlaylistId: "domain-2",
    domains: [
      { id: "domain-1", name: "价格行为学", description: "市场结构" },
      { id: "domain-2", name: "财经资讯", description: "需要关注的更新" },
      { id: "domain-3", name: "决策台", description: "组合决策" },
    ],
  });

  ctx.domainSwitcherResetActiveIndex();
  assert.equal(ctx.domainSwitcherActiveIndex, 1);
  assert.equal(ctx.domainSwitcherActiveOptionId(), "raelyn-domain-option-1");
  ctx.domainSwitcherMove(1);
  assert.equal(ctx.domainSwitcherActiveIndex, 2);
  ctx.domainSwitcherQuery = "市场";
  ctx.domainSwitcherResetActiveIndex();
  assert.deepEqual(ctx.domainSwitcherFilteredDomains().map((domain) => domain.id), ["domain-1"]);
  assert.equal(ctx.domainSwitcherActiveIndex, 0);
});

test("观测域选择页打开后聚焦搜索，返回时恢复触发器且不改折叠偏好", async () => {
  let searchFocused = 0;
  let triggerFocused = 0;
  const ctx = context({
    sidebarCollapsed: true,
    domains: [{ id: "domain-1", name: "当前域" }],
    $refs: {
      domainSwitcherSearch: { focus() { searchFocused += 1; } },
      domainSwitcherTrigger: { focus() { triggerFocused += 1; } },
    },
    $nextTick(callback) { callback(); },
  });

  await ctx.openDomainSwitcherMenu();
  assert.equal(ctx.domainSwitcherOpen, true);
  assert.equal(ctx.sidebarCollapsed, true);
  assert.equal(searchFocused, 1);

  ctx.closeDomainSwitcherMenu();
  assert.equal(ctx.domainSwitcherOpen, false);
  assert.equal(ctx.sidebarCollapsed, true);
  assert.equal(triggerFocused, 1);
});

test("故事页切换观测域先读取目标目录，失败时完整保留当前故事", async () => {
  const previousLocalStorage = globalThis.localStorage;
  const stored = new Map([["raelyn.v2.currentDomainId", "domain-1"]]);
  globalThis.localStorage = {
    getItem(key) { return stored.get(key) || null; },
    setItem(key, value) { stored.set(key, String(value)); },
    removeItem(key) { stored.delete(key); },
  };

  try {
    const oldStory = { story_identity_id: "story-old" };
    const detail = { story_identity_id: "story-old" };
    const ctx = context({
      activeView: "stories",
      selectedPlaylistId: "domain-1",
      playlistPageId: "domain-1",
      domains: [{ id: "domain-1" }, { id: "domain-2" }],
      domainSwitcherOpen: true,
      storySelectedId: "story-old",
      storyDetail: detail,
      storyQueues: {
        attention: { items: [oldStory], total: 1, hasMore: false, loaded: true },
        followed: { items: [], total: 0, hasMore: false, loaded: false },
        established: { items: [], total: 0, hasMore: false, loaded: false },
        emerging: { items: [], total: 0, hasMore: false, loaded: false },
      },
      _syncUrl() { throw new Error("失败前不应改写 URL"); },
      async api(path) {
        assert.match(path, /^\/domains\/domain-2\/stories\?/);
        throw new Error("目标域暂时不可用");
      },
    });
    ctx.storySyncQueueState();

    assert.equal(await ctx.selectDomain("domain-2"), false);
    assert.equal(ctx.selectedPlaylistId, "domain-1");
    assert.equal(ctx.playlistPageId, "domain-1");
    assert.equal(ctx.storySelectedId, "story-old");
    assert.equal(ctx.storyDetail, detail);
    assert.deepEqual(ctx.stories, [oldStory]);
    assert.equal(stored.get("raelyn.v2.currentDomainId"), "domain-1");
    assert.equal(ctx.domainSwitcherError, "目标域暂时不可用");
    assert.equal(ctx.domainSwitcherSwitching, false);
    assert.equal(ctx.domainSwitcherOpen, true);
  } finally {
    if (previousLocalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousLocalStorage;
  }
});

test("故事页目标目录成功后才原子提交新观测域", async () => {
  const previousLocalStorage = globalThis.localStorage;
  const stored = new Map([["raelyn.v2.currentDomainId", "domain-1"]]);
  globalThis.localStorage = {
    getItem(key) { return stored.get(key) || null; },
    setItem(key, value) { stored.set(key, String(value)); },
    removeItem(key) { stored.delete(key); },
  };

  try {
    const syncs = [];
    const ctx = context({
      activeView: "stories",
      selectedPlaylistId: "domain-1",
      playlistPageId: "domain-1",
      domains: [{ id: "domain-1" }, { id: "domain-2" }],
      storySelectedId: "story-old",
      storyDetail: { story_identity_id: "story-old" },
      storyDetailLoading: true,
      storyInspectorOpen: true,
      storyInspectorMode: "relation",
      storyInspectorItem: { edge_id: "edge-old" },
      storyInspectorSupport: { support_records: [{ id: "record-old" }] },
      storyInspectorLoading: true,
      _storyInspectorRequestToken: 7,
      fieldRequestedCanonicalId: "canonical-old",
      fieldRequestedTopicId: "topic-old",
      fieldRequestedEvidenceId: "evidence-old",
      fieldRequestedSnapshotId: "snapshot-old",
      fieldRequestedEntity: { normalized_key: "entity-old" },
      fieldEvidenceDetail: { id: "evidence-old" },
      fieldSelectedChange: { id: "change-old" },
      playlistEventMapReset() { syncs.push("reset-field"); },
      _syncUrl(options) {
        syncs.push({
          options,
          canonical: this.fieldRequestedCanonicalId,
          snapshot: this.fieldRequestedSnapshotId,
          entity: this.fieldRequestedEntity,
        });
      },
      async api(path) {
        assert.match(path, /^\/domains\/domain-2\/stories\?/);
        return { items: [{ story_identity_id: "story-new" }], total: 1, has_more: false };
      },
    });

    assert.equal(await ctx.selectDomain("domain-2"), true);
    assert.equal(ctx.selectedPlaylistId, "domain-2");
    assert.equal(ctx.playlistPageId, "domain-2");
    assert.equal(ctx.storySelectedId, "");
    assert.equal(ctx.storyDetail, null);
    assert.equal(ctx.storyDetailLoading, false);
    assert.equal(ctx.storyInspectorOpen, false);
    assert.equal(ctx.storyInspectorItem, null);
    assert.equal(ctx.storyInspectorSupport, null);
    assert.equal(ctx.storyInspectorLoading, false);
    assert.equal(ctx._storyInspectorRequestToken, 8);
    assert.equal(ctx.fieldRequestedCanonicalId, "");
    assert.equal(ctx.fieldRequestedTopicId, "");
    assert.equal(ctx.fieldRequestedEvidenceId, "");
    assert.equal(ctx.fieldRequestedSnapshotId, "");
    assert.equal(ctx.fieldRequestedEntity, null);
    assert.equal(ctx.fieldEvidenceDetail, null);
    assert.equal(ctx.fieldSelectedChange, null);
    assert.deepEqual(ctx.stories.map((story) => story.story_identity_id), ["story-new"]);
    assert.equal(ctx.storyQueues.attention.loaded, true);
    assert.equal(ctx.storyQueues.followed.loaded, false);
    assert.equal(stored.get("raelyn.v2.currentDomainId"), "domain-2");
    assert.deepEqual(syncs, [
      "reset-field",
      { options: { push: false }, canonical: "", snapshot: "", entity: null },
    ]);
  } finally {
    if (previousLocalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousLocalStorage;
  }
});

test("目标故事目录读取期间切走页面会取消域切换", async () => {
  const previousLocalStorage = globalThis.localStorage;
  globalThis.localStorage = { getItem() { return "domain-1"; }, setItem() {}, removeItem() {} };
  try {
    let resolveTarget;
    const ctx = context({
      activeView: "stories",
      selectedPlaylistId: "domain-1",
      playlistPageId: "domain-1",
      domains: [{ id: "domain-1" }, { id: "domain-2" }],
      _syncUrl() { throw new Error("取消的切换不得改写 URL"); },
      api() { return new Promise((resolve) => { resolveTarget = resolve; }); },
    });

    const switching = ctx.selectDomain("domain-2");
    await Promise.resolve();
    await Promise.resolve();
    ctx.activeView = "library";
    resolveTarget({ items: [], total: 0, has_more: false });

    assert.equal(await switching, false);
    assert.equal(ctx.selectedPlaylistId, "domain-1");
  } finally {
    if (previousLocalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousLocalStorage;
  }
});

test("星域入口预检失败时不销毁当前星域或提交新域", async () => {
  const previousLocalStorage = globalThis.localStorage;
  globalThis.localStorage = { getItem() { return "domain-1"; }, setItem() {}, removeItem() {} };
  try {
    const calls = [];
    const ctx = context({
      activeView: "field",
      selectedPlaylistId: "domain-1",
      playlistPageId: "domain-1",
      domains: [{ id: "domain-1" }, { id: "domain-2" }],
      async saveFieldCursor() { calls.push("save"); },
      leaveField() { calls.push("leave"); },
      _syncUrl() { calls.push("sync"); },
      async api(path) {
        calls.push(path);
        if (path.includes("/observation/cursor")) throw new Error("目标星域不可用");
        return {};
      },
    });

    assert.equal(await ctx.selectDomain("domain-2"), false);
    assert.equal(ctx.selectedPlaylistId, "domain-1");
    assert.equal(calls.includes("save"), false);
    assert.equal(calls.includes("leave"), false);
    assert.equal(calls.includes("sync"), false);
    assert.equal(ctx.domainSwitcherError, "目标星域不可用");
  } finally {
    if (previousLocalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousLocalStorage;
  }
});

test("星域游标保存期间切走页面会在销毁当前画面前取消切域", async () => {
  const previousLocalStorage = globalThis.localStorage;
  globalThis.localStorage = { getItem() { return "domain-1"; }, setItem() {}, removeItem() {} };
  try {
    let resolveSave;
    const calls = [];
    const ctx = context({
      activeView: "field",
      selectedPlaylistId: "domain-1",
      playlistPageId: "domain-1",
      domains: [{ id: "domain-1" }, { id: "domain-2" }],
      api: async () => ({}),
      saveFieldCursor() { return new Promise((resolve) => { resolveSave = resolve; }); },
      leaveField() { calls.push("leave"); },
      _syncUrl() { calls.push("sync"); },
    });

    const switching = ctx.selectDomain("domain-2");
    while (!resolveSave) await Promise.resolve();
    ctx.activeView = "library";
    resolveSave();

    assert.equal(await switching, false);
    assert.equal(ctx.selectedPlaylistId, "domain-1");
    assert.deepEqual(calls, []);
  } finally {
    if (previousLocalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousLocalStorage;
  }
});

test("重复点击当前域只返回主导航，切换进行中不会再启动第二个请求", async () => {
  const previousLocalStorage = globalThis.localStorage;
  globalThis.localStorage = { getItem() { return "domain-1"; }, setItem() {}, removeItem() {} };
  try {
    let resolveTarget;
    const calls = [];
    const ctx = context({
      activeView: "stories",
      selectedPlaylistId: "domain-1",
      playlistPageId: "domain-1",
      domains: [{ id: "domain-1" }, { id: "domain-2" }, { id: "domain-3" }],
      domainSwitcherOpen: true,
      _syncUrl() {},
      api(path) {
        calls.push(path);
        return new Promise((resolve) => { resolveTarget = resolve; });
      },
    });

    assert.equal(await ctx.domainSwitcherChoose("domain-1"), true);
    assert.equal(ctx.domainSwitcherOpen, false);
    assert.deepEqual(calls, []);

    ctx.domainSwitcherOpen = true;
    const first = ctx.domainSwitcherChoose("domain-2");
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(ctx.domainSwitcherSwitching, true);
    assert.equal(await ctx.domainSwitcherChoose("domain-3"), false);
    assert.equal(calls.length, 1);
    resolveTarget({ items: [], total: 0, has_more: false });
    assert.equal(await first, true);
    assert.equal(ctx.selectedPlaylistId, "domain-2");
  } finally {
    if (previousLocalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousLocalStorage;
  }
});

test("观测域选择使用侧栏二级页替换主导航，并与故事页头共享 64px 基线", async () => {
  const sidebar = await readFile(new URL("../../../templates/app/components/sidebar.html", import.meta.url), "utf8");
  const stories = await readFile(new URL("../../../templates/app/views/stories.html", import.meta.url), "utf8");
  const modal = await readFile(new URL("../../../templates/app/components/modals/create-playlist.html", import.meta.url), "utf8");

  assert.doesNotMatch(sidebar, /<select[\s>]/);
  assert.match(sidebar, /role="combobox"/);
  assert.match(sidebar, /role="listbox"/);
  assert.match(sidebar, /id="raelyn-domain-switcher-page"/);
  assert.match(sidebar, /domainSwitcherOpen \? \(sidebarMobilePortrait \? 'w-full' : 'w-72'\)/);
  assert.match(sidebar, /<nav[^>]+x-show="!domainSwitcherOpen"/);
  assert.doesNotMatch(sidebar, /aria-haspopup="dialog"|aria-modal="true"|raelyn-domain-switcher-(?:panel|backdrop)|domainSwitcherTrapFocus/);
  assert.match(sidebar, /@keydown\.escape\.prevent\.stop="!domainSwitcherSwitching && closeDomainSwitcherMenu\(\)"/);
  assert.match(sidebar, /data-domain-context-band/);
  assert.match(sidebar, /class="relative flex h-16/);
  assert.match(sidebar, /domain\.avatar_asset/);
  assert.match(sidebar, /assetContentUrl\(domain\.avatar_asset\)/);
  assert.match(sidebar, /clearAssetRef\(domain, 'avatar_asset'\)/);
  assert.match(sidebar, /domainAvatarLabel\(domain\)/);
  assert.doesNotMatch(sidebar, /当前观测域设置/);
  assert.match(sidebar, /浏览全部观测域/);
  assert.match(sidebar, /新建观测域/);
  assert.doesNotMatch(sidebar, /chevron_right|material-symbols|>link</);

  assert.match(stories, /data-story-context-band/);
  assert.match(stories, /class="raelyn-toolbar-band flex h-16/);
  assert.match(stories, /grid-cols-4/);
  assert.match(stories, /form class="flex h-12/);
  assert.match(modal, /createPlaylistContext === 'domain' \? '新建观测域'/);
  assert.match(modal, /role="dialog" aria-modal="true"/);
  assert.match(modal, /搜索信源（回车添加第一个匹配项）/);
  assert.match(modal, /:disabled="createPlaylistSubmitting"/);
});

test("观测域设置是独立页面，首屏不等待详情与删除影响", async () => {
  const calls = [];
  const ctx = context({
    domains: [{
      id: "domain-1",
      name: "测试观测域",
      description: "测试范围",
      observation_enabled: false,
      brief_granularity: "week",
    }],
    async ensureCurrentDomain() { return "domain-1"; },
    async api(path) {
      calls.push(path);
      return {
        domain: {
          id: "domain-1",
          observation_enabled: false,
          brief_granularity: "month",
          brief_prompt: "只保留实质变化",
        },
        coverage: {
          source_processing: { numerator: 8, denominator: 10 },
          event_extraction: {
            numerator: 4,
            denominator: 8,
            details: {
              prompt_version: "llm_event_v4_explicit_relation_endpoints:test",
              model: "qwen3.6:35b",
              with_events: 3,
              zero_events: 1,
              legacy_spec_only: 2,
              current_spec_failed: 1,
              no_successful_result: 1,
              missing_transcript: 2,
            },
          },
        },
      };
    },
  });

  await ctx.loadDomainSettings();

  assert.deepEqual(calls, ["/domains/domain-1/observation"]);
  assert.equal(ctx.domainSettingsNameDraft, "测试观测域");
  assert.equal(ctx.playlistDetail.brief_granularity, "month");
  assert.equal(ctx.briefGenerationGranularityDraft, "month");
  assert.equal(ctx.briefGenerationPromptDraft, "只保留实质变化");
  assert.equal(ctx.domainObservationEnabled(), false);
  assert.equal(ctx.domainCoveragePercent("source_processing"), 80);
  assert.equal(ctx.domainCoveragePercent("event_extraction"), 50);
  assert.equal(ctx.domainCoverageDetail("event_extraction", "zero_events"), "1");
  assert.equal(ctx.domainEventExtractionSpecLabel(), "v4 · qwen3.6:35b");
  assert.equal(ctx.domainDeletionImpact, null);

  const page = await readFile(new URL("../../../templates/app/views/domain-settings.html", import.meta.url), "utf8");
  const modals = await readFile(new URL("../../../templates/app/views/v2-modals.html", import.meta.url), "utf8");
  assert.match(page, /activeView==='domain-settings'/);
  assert.match(page, /处理与产出覆盖/);
  assert.match(page, /当前口径抽取/);
  assert.match(page, /零事件（成功）/);
  assert.match(page, /仅旧口径成功/);
  assert.doesNotMatch(page, /整体分析进度/);
  assert.match(page, /role="switch"/);
  assert.match(page, /简报生成/);
  assert.match(page, /briefGenerationGranularityDraft/);
  assert.doesNotMatch(modals, /briefGenerationSettingsOpen/);
  assert.doesNotMatch(modals, /domainSettingsOpen/);
});

test("删除影响只在展开危险操作后加载", async () => {
  const calls = [];
  const ctx = context({
    _domainSettingsRequestToken: 3,
    async api(path) {
      calls.push(path);
      return { counts: { active_jobs: 0 } };
    },
  });

  assert.equal(ctx.domainDangerOpen, false);
  assert.deepEqual(calls, []);
  await ctx.toggleDomainDanger();
  assert.deepEqual(calls, ["/domains/domain-1/deletion-impact"]);
  assert.equal(ctx.domainDeletionImpact.counts.active_jobs, 0);
});

test("持续观测开关持久化到当前域并同步页面状态", async () => {
  const calls = [];
  const messages = [];
  const domain = { id: "domain-1", name: "测试域", observation_enabled: false };
  const ctx = context({
    domains: [domain],
    playlistDetail: { id: "domain-1", observation_enabled: false },
    domainObservation: { domain: { id: "domain-1", observation_enabled: false } },
    async api(path, options) {
      calls.push([path, options]);
      return { observation_enabled: true, changed: true };
    },
    toastSuccess(message) { messages.push(message); },
  });

  await ctx.setDomainObservationEnabled(true);

  assert.equal(calls[0][0], "/domains/domain-1/observation");
  assert.equal(calls[0][1].method, "PUT");
  assert.deepEqual(JSON.parse(calls[0][1].body), { enabled: true });
  assert.equal(domain.observation_enabled, true);
  assert.equal(ctx.playlistDetail.observation_enabled, true);
  assert.equal(ctx.domainObservation.domain.observation_enabled, true);
  assert.match(messages[0], /观测已启用/);
});

test("移动端切换成功后收起侧栏，降级读取失败时保留选择页", async () => {
  let mainStageFocused = 0;
  const success = context({
    domainSwitcherOpen: true,
    sidebarMobilePortrait: true,
    sidebarHidden: false,
    $refs: { mainStage: { focus() { mainStageFocused += 1; } } },
    $nextTick(callback) { callback(); },
    async selectDomain() { this.domainSwitcherError = ""; return true; },
    toggleSidebar() { this.sidebarHidden = !this.sidebarHidden; },
  });
  assert.equal(await success.domainSwitcherChoose("domain-2"), true);
  assert.equal(success.domainSwitcherOpen, false);
  assert.equal(success.sidebarHidden, true);
  assert.equal(mainStageFocused, 1);

  const degraded = context({
    domainSwitcherOpen: true,
    domainSwitcherQuery: "财经",
    async selectDomain() {
      this.domainSwitcherError = "已切换，但页面读取失败";
      return true;
    },
  });
  assert.equal(await degraded.domainSwitcherChoose("domain-2"), true);
  assert.equal(degraded.domainSwitcherOpen, true);
  assert.equal(degraded.domainSwitcherQuery, "财经");
});

test("观测域已提交但页面读取失败后可从侧栏重试当前页面", async () => {
  const refreshOptions = [];
  let mainStageFocused = 0;
  const ctx = context({
    activeView: "field",
    domainSwitcherOpen: true,
    domainSwitcherRetryMode: "page",
    domainSwitcherError: "星域暂时不可读",
    sidebarMobilePortrait: true,
    sidebarHidden: false,
    $refs: { mainStage: { focus() { mainStageFocused += 1; } } },
    $nextTick(callback) { callback(); },
    toggleSidebar() {
      assert.equal(this.domainSwitcherSwitching, false, "结束切换后才能收起移动侧栏");
      this.sidebarHidden = true;
    },
    async refreshActive(options) { refreshOptions.push(options); },
  });

  await ctx.retryDomainSwitcherLoad();

  assert.deepEqual(refreshOptions, [{ throwOnError: true }]);
  assert.equal(ctx.domainSwitcherOpen, false);
  assert.equal(ctx.domainSwitcherError, "");
  assert.equal(ctx.domainSwitcherSwitching, false);
  assert.equal(ctx.domainSwitcherPendingId, "");
  assert.equal(ctx.sidebarHidden, true);
  assert.equal(mainStageFocused, 1);
});

test("切换进行中响应式变化与侧栏按钮不会隐藏观测域选择页", () => {
  const ctx = {
    ...createShellModule({
      apiTokenCookieKey: "test-token",
      sidebarCollapsedKey: "test-collapsed",
      sidebarHiddenKey: "test-hidden",
      startupGateSeenSessionKey: "test-startup",
    }),
    domainSwitcherOpen: true,
    domainSwitcherSwitching: true,
    sidebarHidden: false,
    sidebarMobilePortrait: false,
    _isMobilePortrait() { return true; },
    closeDomainSwitcherMenu() { this.domainSwitcherOpen = false; },
  };

  ctx._applySidebarMode();
  assert.equal(ctx.sidebarMobilePortrait, true);
  assert.equal(ctx.sidebarHidden, false);
  assert.equal(ctx.domainSwitcherOpen, true);

  ctx.toggleSidebar();
  assert.equal(ctx.sidebarHidden, false);
  assert.equal(ctx.domainSwitcherOpen, true);
});

test("新建观测域禁止重复提交并使用观测域校验文案", async () => {
  let resolveCreate;
  let createCount = 0;
  const ctx = {
    ...createPlaylistsModule(),
    ...createPlaylistsViewMethods(),
    mediaIndex: [],
    async api(path) {
      if (path !== "/playlists") return [];
      createCount += 1;
      return new Promise((resolve) => { resolveCreate = resolve; });
    },
    async loadDomains() {},
    async selectDomain() { return true; },
  };

  ctx.openCreateDomain();
  await ctx.submitCreatePlaylist();
  assert.equal(ctx.globalStatus, "请填写观测域名称");

  ctx.createPlaylistName = "不会重复创建";
  const first = ctx.submitCreatePlaylist();
  await Promise.resolve();
  assert.equal(ctx.createPlaylistSubmitting, true);
  await ctx.submitCreatePlaylist();
  assert.equal(createCount, 1);

  resolveCreate({ id: "domain-new" });
  await first;
  assert.equal(ctx.createPlaylistSubmitting, false);
});

test("从切换器新建观测域后刷新域目录并选中新域", async () => {
  const calls = [];
  const ctx = {
    ...createPlaylistsModule(),
    ...createPlaylistsViewMethods(),
    mediaIndex: [],
    createPlaylistName: "新观测域",
    async api(path) {
      calls.push(path);
      if (path === "/playlists") return { id: "domain-new" };
      return [];
    },
    async loadDomains(options) { calls.push(["loadDomains", options]); },
    async selectDomain(id) { calls.push(["selectDomain", id]); return true; },
    async loadPlaylists() { calls.push("loadPlaylists"); },
  };

  ctx.openCreateDomain();
  assert.equal(ctx.createPlaylistContext, "domain");
  assert.equal(ctx.modals.createPlaylist, true);
  ctx.createPlaylistName = "新观测域";
  await ctx.submitCreatePlaylist();

  assert.deepEqual(calls.slice(-2), [["loadDomains", { force: true }], ["selectDomain", "domain-new"]]);
  assert.equal(calls.includes("loadPlaylists"), false);
  assert.equal(ctx.createPlaylistContext, "playlist");
  assert.equal(ctx.modals.createPlaylist, false);
});

test("观测域创建成功但切换失败时给出可见反馈并打开域目录", async () => {
  const calls = [];
  const ctx = {
    ...createPlaylistsModule(),
    ...createPlaylistsViewMethods(),
    mediaIndex: [],
    createPlaylistName: "新观测域",
    domainSwitcherError: "目标入口暂不可用",
    async api(path) { return path === "/playlists" ? { id: "domain-new" } : []; },
    async loadDomains(options) { calls.push(["loadDomains", options]); },
    async selectDomain() { return false; },
    toastError(message) { calls.push(["toastError", message]); },
    switchView(view) { calls.push(["switchView", view]); },
  };

  ctx.openCreateDomain();
  ctx.createPlaylistName = "新观测域";
  await ctx.submitCreatePlaylist();

  assert.equal(ctx.modals.createPlaylist, false, "实体已经创建，不应保留可重复提交的弹窗");
  assert.equal(ctx.globalStatus, "error: 目标入口暂不可用");
  assert.deepEqual(calls.slice(-2), [["toastError", "目标入口暂不可用"], ["switchView", "domains"]]);
});

test("观测域实体创建后上传失败会关闭弹窗并阻止重复创建", async () => {
  const calls = [];
  const ctx = {
    ...createPlaylistsModule(),
    ...createPlaylistsViewMethods(),
    mediaIndex: [],
    createPlaylistName: "已创建的观测域",
    createPlaylistAvatarFile: { size: 1024 },
    async api(path) { return path === "/playlists" ? { id: "domain-created" } : []; },
    async _uploadPlaylistImage() { throw new Error("头像上传失败"); },
    toastError(message) { calls.push(["toastError", message]); },
    switchView(view) { calls.push(["switchView", view]); },
  };

  ctx.openCreateDomain();
  ctx.createPlaylistName = "已创建的观测域";
  ctx.createPlaylistAvatarFile = { size: 1024 };
  await ctx.submitCreatePlaylist();

  assert.equal(ctx.modals.createPlaylist, false);
  assert.equal(ctx.createPlaylistContext, "playlist");
  assert.match(ctx.globalStatus, /观测域已创建，但后续处理失败：头像上传失败/);
  assert.deepEqual(calls.at(-1), ["switchView", "domains"]);
});

test("删除当前域后清理旧选择并优先恢复下一个可读域", async () => {
  const previousLocalStorage = globalThis.localStorage;
  const removed = [];
  globalThis.localStorage = { removeItem(key) { removed.push(key); } };
  try {
    const calls = [];
    const ctx = context({
      selectedPlaylistId: "domain-old",
      playlistPageId: "domain-old",
      domainSwitcherOpen: true,
      domainDeleteConfirmDraft: "旧域",
      async api(path, options) { calls.push([path, options]); return {}; },
      async loadDomains(options) {
        calls.push(["loadDomains", options]);
        this.domains = [
          { id: "domain-building", snapshot: { status: "building" } },
          { id: "domain-ready", snapshot: { status: "ready" } },
        ];
      },
      async selectDomain(id) { calls.push(["selectDomain", id]); return true; },
      switchView(view) { calls.push(["switchView", view]); },
      toastSuccess(message) { calls.push(["toast", message]); },
    });

    await ctx.deleteCurrentDomainSafely();

    assert.equal(ctx.domainSwitcherOpen, false);
    assert.equal(ctx.selectedPlaylistId, null);
    assert.deepEqual(removed, [ctx.v2LastDomainKey]);
    assert.equal(calls.some(([name, value]) => name === "selectDomain" && value === "domain-ready"), true);
    assert.equal(calls.some(([name, value]) => name === "switchView" && value === "field"), true);
  } finally {
    if (previousLocalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousLocalStorage;
  }
});

test("观测域删除成功但目录恢复失败时留在域目录并明确报错", async () => {
  const previousLocalStorage = globalThis.localStorage;
  globalThis.localStorage = { removeItem() {} };
  try {
    const calls = [];
    const ctx = context({
      activeView: "field",
      selectedPlaylistId: "domain-old",
      playlistPageId: "domain-old",
      domainDeleteConfirmDraft: "旧域",
      async api() { return {}; },
      async loadDomains() { throw new Error("目录服务暂不可用"); },
      switchView(view, options) {
        calls.push(["switchView", view, options]);
        this.activeView = view;
      },
      toastSuccess(message) { calls.push(["success", message]); },
      toastError(message) { calls.push(["error", message]); },
    });

    await ctx.deleteCurrentDomainSafely();

    assert.equal(ctx.activeView, "domains");
    assert.equal(ctx.selectedPlaylistId, null);
    assert.equal(ctx.playlistPageId, null);
    assert.equal(ctx.domainDeleteSubmitting, false);
    assert.deepEqual(calls[0], ["switchView", "domains", { refresh: false }]);
    assert.match(ctx.globalStatus, /观测域已删除，但目录恢复失败：目录服务暂不可用/);
    assert.equal(calls.some(([kind, message]) => kind === "error" && /目录恢复失败/.test(message)), true);
  } finally {
    if (previousLocalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousLocalStorage;
  }
});

test("故事目录丢弃过期搜索结果，旧请求不会覆盖当前队列", async () => {
  const pending = [];
  const ctx = context({
    async ensureCurrentDomain() { return "domain-1"; },
    api(path) { return new Promise((resolve) => pending.push({ path, resolve })); },
  });

  ctx.storyQueueQueries.attention = "旧查询";
  const oldRequest = ctx.loadStories();
  await Promise.resolve();
  ctx.storyQueueQueries.attention = "新查询";
  const newRequest = ctx.loadStories();
  await Promise.resolve();
  pending[1].resolve({ items: [{ story_identity_id: "new" }], total: 1, has_more: false });
  await newRequest;
  pending[0].resolve({ items: [{ story_identity_id: "old" }], total: 1, has_more: false });
  await oldRequest;

  assert.deepEqual(ctx.stories.map((story) => story.story_identity_id), ["new"]);
  assert.match(pending[0].path, /q=%E6%97%A7%E6%9F%A5%E8%AF%A2/);
  assert.match(pending[1].path, /q=%E6%96%B0%E6%9F%A5%E8%AF%A2/);
});

test("打开故事不清除未读，选择事件只保存阅读位置，显式操作才标记已读", async () => {
  const calls = [];
  const detail = {
    story_identity_id: "story-1",
    trajectory: { snapshot_id: "snapshot-current", nodes: [{ canonical_id: "event-1", title: "事件一" }], edges: [] },
    reading_update: { baseline: false, has_material_changes: true, events_added: [{ canonical_id: "event-1", title: "事件一" }] },
  };
  const ctx = context({
    activeView: "stories",
    async api(path, options = {}) {
      calls.push([path, options]);
      if (path.includes("/events/map/canonical/")) return { ...detail.trajectory.nodes[0], members: [], evidence: [] };
      if (path.includes("/stories/story-1?") || path.endsWith("/stories/story-1")) return detail;
      if (path.includes("/stories?")) return { items: [], total: 0, has_more: false };
      return {};
    },
    async ensureCurrentDomain() { return "domain-1"; },
    _syncUrl() {},
  });

  await ctx.storyOpen("story-1");
  assert.equal(calls.some(([path]) => path.endsWith("/read-state")), false);
  await ctx.storyOpenEventInspector(detail.trajectory.nodes[0], 0, null);
  const positionPatch = calls.find(([path]) => path.endsWith("/read-state"));
  assert.deepEqual(JSON.parse(positionPatch[1].body), { position: 0 });
  await ctx.storyMarkCurrentUpdateRead();
  const readPatches = calls.filter(([path]) => path.endsWith("/read-state"));
  assert.deepEqual(JSON.parse(readPatches.at(-1)[1].body), { mark_read: true, snapshot_id: "snapshot-current" });
  assert.equal(calls.some(([path]) => path.includes("/stories?scope=attention")), true, "普通值得阅读队列应在标记后刷新");
});

test("39 事件长故事全量分组，并只把真实分支与非相邻关系附到目标事件", () => {
  const nodes = Array.from({ length: 39 }, (_, index) => ({
    canonical_id: `event-${index}`,
    title: `事件 ${index}`,
    event_time_start: `2026-${String(Math.floor(index / 4) + 1).padStart(2, "0")}-${String(index % 4 + 1).padStart(2, "0")}T00:00:00Z`,
  }));
  const edges = [
    { edge_id: "edge-1", source_canonical_id: "event-0", target_canonical_id: "event-20", relation_type: "causes" },
    { edge_id: "edge-2", source_canonical_id: "event-5", target_canonical_id: "event-20", relation_type: "response" },
  ];
  const ctx = context({ storyDetail: { trajectory: { snapshot_id: "snapshot-1", nodes, edges } } });

  const grouped = ctx.storyTimelineGroups().flatMap((group) => group.nodes);
  assert.equal(grouped.length, 39, "长故事不得截断或虚拟化隐藏事件");
  assert.equal(ctx.storyIncomingEdges(grouped[20]).length, 2, "分支来源必须同时呈现");
  assert.equal(ctx.storyIncomingEdges(grouped[1]).length, 0, "相邻事件之间不得虚构连续关系");
  assert.equal(ctx.storyRelationLabel("causes"), "因果承接");
});

test("故事事件按日分隔并统一使用 yyyy/MM/dd 日期格式", () => {
  const ctx = context({
    storyDetail: {
      trajectory: {
        nodes: [
          { canonical_id: "event-1", event_time_start: "2024-08-05T00:00:00Z" },
          { canonical_id: "event-2", event_time_start: "2024-08-05T08:30:00Z" },
          { canonical_id: "event-3", event_time_start: "2024-08-06T00:00:00Z" },
        ],
        edges: [],
      },
    },
  });

  assert.deepEqual(ctx.storyTimelineGroups().map((group) => group.label), ["2024/08/05", "2024/08/06"]);
  assert.equal(ctx.storyEventTimeLabel(ctx.storyTrajectory().nodes[0]), "2024/08/05");
  assert.equal(ctx.storyEventTimeLabel(ctx.storyTrajectory().nodes[1]), "2024/08/05 08:30");
});

test("故事实质更新时间按浏览器本地时区展示同一时刻", () => {
  const ctx = context();
  const utc = ctx.storyObservedDateTimeLabel("2026-08-31T05:16:38+00:00");
  const chinaOffset = ctx.storyObservedDateTimeLabel("2026-08-31T13:16:38+08:00");

  assert.equal(utc, chinaOffset);
  assert.match(utc, /^\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}$/);
});

test("仅顺序或关系依据变化也会形成可读更新，不显示空更新块", () => {
  const ctx = context({
    storyDetail: {
      reading_update: {
        baseline: false,
        has_material_changes: true,
        member_order_changed: true,
        relations_changed: [{ relation_type: "continuation", source_title: "事件甲", target_title: "事件乙" }],
      },
    },
  });

  assert.equal(ctx.storyHasReadingUpdate(), true);
  assert.deepEqual(ctx.storyReadingUpdateItems(), [
    "调整「阶段推进」依据：事件甲 → 事件乙",
    "事件发生顺序已调整",
  ]);
});

test("支持记录并集不变但关系归属调整时仍显示可读更新", () => {
  const ctx = context({
    storyDetail: {
      reading_update: {
        baseline: false,
        has_material_changes: true,
        support_assignments_changed: true,
        support_records_added_count: 0,
        support_records_removed_count: 0,
      },
    },
  });

  assert.equal(ctx.storyHasReadingUpdate(), true);
  assert.deepEqual(ctx.storyReadingUpdateItems(), ["支持记录与关系的对应已调整"]);
});

test("故事结构恢复到上次阅读状态时只说明恢复，不伪造净增减", () => {
  const ctx = context({
    storyDetail: {
      reading_update: {
        baseline: false,
        has_material_changes: true,
        returned_to_previous_state: true,
        events_added: [{ title: "不应显示为新增" }],
        support_records_removed_count: 2,
      },
    },
  });

  assert.equal(ctx.storyHasReadingUpdate(), true);
  assert.deepEqual(ctx.storyReadingUpdateItems(), [
    "期间发生过实质变化，当前故事结构已恢复到上次阅读时的状态",
  ]);
});

test("首访从最早事件开始，回访恢复位置，有新增时优先定位首个新增事件", () => {
  const nodes = [0, 1, 2, 3].map((index) => ({ canonical_id: `event-${index}`, event_time_start: `2026-01-0${index + 1}` }));
  const ctx = context({ storyDetail: { trajectory: { nodes, edges: [] }, last_position: 2, reading_update: { baseline: true } } });
  assert.equal(ctx.storySuggestedStartIndex(), 0);

  ctx.storyDetail.reading_update = { baseline: false, has_material_changes: false };
  assert.equal(ctx.storySuggestedStartIndex(), 2);

  ctx.storyDetail.reading_update = {
    baseline: false,
    has_material_changes: true,
    first_added_position: 3,
    events_added: [{ canonical_id: "event-3", title: "新增事件" }],
  };
  assert.equal(ctx.storySuggestedStartIndex(), 3);
});

test("历史快照不能清除当前未读，支持状态会归一化 evidence 子项", async () => {
  let patchCount = 0;
  const ctx = context({
    storySelectedId: "story-1",
    storySelectedSnapshotId: "snapshot-history",
    storyDetail: { is_historical: true, can_mark_read: false },
    async api() { patchCount += 1; },
  });
  assert.equal(ctx.storyCanMarkRead(), false);
  await ctx.storyMarkCurrentUpdateRead();
  assert.equal(patchCount, 0);
  assert.equal(ctx.storySupportStatusLabel({ evidence: [{ verified: true }] }), "已验证");
  assert.equal(ctx.storySupportPlaybackPosition({ evidence: [{ playback_start_sec: 42 }] }), 42);
  assert.equal(ctx.storySupportVersionReplaced({ source_version_status: "superseded" }), true);
});

test("首访推荐按真实基线语义可显式标记已读，并保留整页推荐", async () => {
  const calls = [];
  let detailReloads = 0;
  let directoryReloads = 0;
  const recommendedItems = [{
    story_identity_id: "story-1",
    baseline_suggestion: true,
    recommended: true,
    unread: false,
  }];
  const ctx = context({
    storySelectedId: "story-1",
    storyQueues: {
      ...createV2Module().storyQueues,
      attention: { items: recommendedItems, total: 1, hasMore: false, loaded: true },
    },
    stories: recommendedItems,
    storyDetail: {
      unread: false,
      is_historical: false,
      can_mark_read: true,
      last_read_at: null,
      trajectory: { snapshot_id: "snapshot-current", nodes: [], edges: [] },
      reading_update: { baseline: true, has_material_changes: false },
    },
    async api(path, options = {}) {
      calls.push([path, options]);
      return { last_read_at: "2026-08-21T12:00:00Z", last_read_snapshot_id: "snapshot-current" };
    },
    async loadStoryDetail() { detailReloads += 1; },
    async loadStories() { directoryReloads += 1; },
  });

  assert.equal(ctx.storyHasReadingUpdate(), false);
  assert.equal(ctx.storyCanMarkRead(), true);
  await ctx.storyMarkCurrentUpdateRead();
  assert.deepEqual(JSON.parse(calls[0][1].body), { mark_read: true, snapshot_id: "snapshot-current" });
  assert.equal(ctx.storyDetail.unread, false);
  assert.equal(ctx.storyDetail.last_read_at, "2026-08-21T12:00:00Z");
  assert.equal(ctx.storyCanMarkRead(), false);
  assert.equal(detailReloads, 0);
  assert.equal(directoryReloads, 0);
  assert.equal(ctx.storyQueues.attention.items, recommendedItems);
  assert.equal(ctx.stories.length, 1);
});

test("事件与关系检查器都固定到所选历史快照并懒加载依据", async () => {
  const calls = [];
  const ctx = context({
    storySelectedId: "story-1",
    storyDetail: { trajectory: { snapshot_id: "snapshot-history", nodes: [], edges: [] } },
    async api(path, options = {}) { calls.push([path, options]); return path.includes("/read-state") ? {} : { support_records: [] }; },
  });

  await ctx.storyOpenEventInspector({ canonical_id: "event-1" }, 7, null);
  await ctx.storyOpenEdgeInspector({ edge_id: "edge-1" }, null);

  const eventUrl = new URL(`http://local${calls.find(([path]) => path.includes("/events/map/canonical/"))[0]}`);
  const edgeUrl = new URL(`http://local${calls.find(([path]) => path.includes("/edges/edge-1/support"))[0]}`);
  assert.equal(eventUrl.searchParams.get("snapshot_id"), "snapshot-history");
  assert.equal(edgeUrl.searchParams.get("snapshot_id"), "snapshot-history");
});

test("事件位置保存未完成时打开关系，不会让旧事件请求作废新关系", async () => {
  let resolvePosition;
  const calls = [];
  const ctx = context({
    storySelectedId: "story-1",
    storyDetail: { trajectory: { snapshot_id: "snapshot-1", nodes: [], edges: [] } },
    api(path) {
      calls.push(path);
      if (path.endsWith("/read-state")) return new Promise((resolve) => { resolvePosition = resolve; });
      if (path.includes("/edges/edge-1/support")) return Promise.resolve({ support_records: [{ record_revision_id: "record-1" }] });
      if (path.includes("/events/map/canonical/")) return Promise.resolve({ canonical_id: "event-1" });
      return Promise.resolve({});
    },
  });

  const openingEvent = ctx.storyOpenEventInspector({ canonical_id: "event-1" }, 0, null);
  while (!resolvePosition) await Promise.resolve();
  await ctx.storyOpenEdgeInspector({ edge_id: "edge-1" }, null);
  resolvePosition({ last_position: 0 });
  await openingEvent;

  assert.equal(ctx.storyInspectorMode, "relation");
  assert.equal(ctx.storyInspectorItem.edge_id, "edge-1");
  assert.equal(ctx.storyInspectorSupport.support_records[0].record_revision_id, "record-1");
  assert.equal(calls.some((path) => path.includes("/events/map/canonical/")), false);
});

test("旧故事阅读状态回包不会写入切域后的新故事详情", async () => {
  let resolveReadState;
  const ctx = context({
    selectedPlaylistId: "domain-old",
    storySelectedId: "story-old",
    storyDetail: { story_identity_id: "story-old", last_position: 1 },
    api() { return new Promise((resolve) => { resolveReadState = resolve; }); },
  });

  const pending = ctx.storyUpdateReadState({ position: 7 });
  ctx.selectedPlaylistId = "domain-new";
  ctx.storySelectedId = "story-new";
  ctx.storyDetail = { story_identity_id: "story-new", last_position: 2 };
  resolveReadState({ last_position: 7 });
  await pending;

  assert.equal(ctx.storyDetail.last_position, 2);
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
    async playlistEventMapLoadView(options) {
      calls.push(["load", options]);
      this.playlistEventMapSnapshotId = this.fieldRequestedSnapshotId;
    },
    async fieldOpenCanonical(id, pointIndex, options) {
      calls.push(["canonical", id, pointIndex, options.selectionToken, options.requestGuard()]);
      return true;
    },
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
  assert.deepEqual(calls, [
    "sync",
    ["load", { silent: false, schedulePolling: false, selectionToken: 1 }],
    ["canonical", "canonical-retired", undefined, 1, true],
  ]);
  assert.equal(ctx.fieldSelectedChange, change);
});

test("星域变化连续选择时后一次完成结果不会被旧变化覆盖", async () => {
  const pendingSelections = new Map();
  const ctx = context({
    activeView: "field",
    async fieldOpenCanonical(id, _pointIndex, options) {
      return await new Promise((resolve) => {
        pendingSelections.set(id, { resolve, options });
      });
    },
  });
  const firstChange = {
    id: "change-a",
    object_type: "canonical",
    object_id: "canonical-a",
    to_snapshot_id: "snapshot-1",
    after_revision: { point_index: 11 },
  };
  const secondChange = {
    id: "change-b",
    object_type: "canonical",
    object_id: "canonical-b",
    to_snapshot_id: "snapshot-1",
    after_revision: { point_index: 22 },
  };

  const first = ctx.fieldOpenFeedItem(firstChange);
  const second = ctx.fieldOpenFeedItem(secondChange);
  assert.notEqual(
    pendingSelections.get("canonical-a").options.selectionToken,
    pendingSelections.get("canonical-b").options.selectionToken
  );

  pendingSelections.get("canonical-b").resolve(true);
  assert.equal(await second, true);
  assert.equal(ctx.fieldSelectedChange, secondChange);

  pendingSelections.get("canonical-a").resolve(true);
  assert.equal(await first, false);
  assert.equal(ctx.fieldSelectedChange, secondChange);
});

test("旧星域变化的跨快照加载返回后不会重新领取选择权", async () => {
  const loadResolvers = new Map();
  const opened = [];
  const ctx = context({
    activeView: "field",
    _syncUrl() {},
    playlistEventMapLoadView() {
      const requestedSnapshotId = this.fieldRequestedSnapshotId;
      return new Promise((resolve) => {
        loadResolvers.set(requestedSnapshotId, () => {
          if (requestedSnapshotId === "snapshot-b") this.playlistEventMapSnapshotId = requestedSnapshotId;
          resolve();
        });
      });
    },
    async fieldOpenCanonical(id) {
      opened.push(id);
      return true;
    },
  });
  const firstChange = {
    id: "change-a",
    object_type: "canonical",
    object_id: "canonical-a",
    from_snapshot_id: "snapshot-a",
    after_revision: null,
  };
  const secondChange = {
    id: "change-b",
    object_type: "canonical",
    object_id: "canonical-b",
    from_snapshot_id: "snapshot-b",
    after_revision: null,
  };

  const first = ctx.fieldOpenFeedItem(firstChange);
  const second = ctx.fieldOpenFeedItem(secondChange);
  loadResolvers.get("snapshot-a")();
  assert.equal(await first, false);
  assert.deepEqual(opened, []);

  loadResolvers.get("snapshot-b")();
  assert.equal(await second, true);
  assert.deepEqual(opened, ["canonical-b"]);
  assert.equal(ctx.fieldSelectedChange, secondChange);
});

test("两个变化卡跳往同一历史快照时复用下载，旧请求不回滚新请求的 URL", async () => {
  const selectionTokens = [];
  const opened = [];
  let releaseLoad;
  let sharedLoad = null;
  const ctx = context({
    activeView: "field",
    fieldRequestedSnapshotId: "",
    _syncUrl() {},
    playlistEventMapLoadView({ selectionToken }) {
      selectionTokens.push(selectionToken);
      if (!sharedLoad) {
        sharedLoad = new Promise((resolve) => {
          releaseLoad = () => {
            this.playlistEventMapSnapshotId = "snapshot-history";
            resolve();
          };
        });
      }
      return sharedLoad;
    },
    async fieldOpenCanonical(id) {
      opened.push(id);
      return true;
    },
  });
  const firstChange = {
    id: "change-a",
    object_type: "canonical",
    object_id: "canonical-a",
    from_snapshot_id: "snapshot-history",
    after_revision: null,
  };
  const secondChange = {
    id: "change-b",
    object_type: "canonical",
    object_id: "canonical-b",
    from_snapshot_id: "snapshot-history",
    after_revision: null,
  };

  const first = ctx.fieldOpenFeedItem(firstChange);
  const second = ctx.fieldOpenFeedItem(secondChange);
  assert.deepEqual(selectionTokens, [1, 1]);
  releaseLoad();

  assert.deepEqual(await Promise.all([first, second]), [false, true]);
  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-history");
  assert.equal(ctx.fieldRequestedSnapshotId, "snapshot-history");
  assert.deepEqual(opened, ["canonical-b"]);
  assert.equal(ctx.fieldSelectedChange, secondChange);
});

test("返回当前加载中改去另一历史快照后，取消时仍恢复原历史 URL", async () => {
  let stagedController;
  const ctx = context({
    activeView: "field",
    fieldRequestedSnapshotId: "",
    playlistEventMapSnapshotId: "snapshot-history",
    _fieldSnapshotNavigationToken: 3,
    _fieldSnapshotNavigationTarget: "@latest",
    _fieldSnapshotNavigationBaseline: "snapshot-history",
    _fieldSnapshotNavigationOrigin: "snapshot-history",
    _playlistEventMapSelectionToken: 5,
    _abortCtrl(name) {
      this[name]?.abort?.();
      this[name] = null;
    },
    _syncUrl() {},
    playlistEventMapStopPolling() {},
    playlistEventMapLoadView() {
      stagedController = new AbortController();
      this._playlistEventMapStagedAbortCtrl = stagedController;
      return new Promise((resolve) => {
        stagedController.signal.addEventListener("abort", resolve, { once: true });
      });
    },
    async fieldOpenCanonical() {
      throw new Error("已取消的历史导航不应定位对象");
    },
  });
  const change = {
    id: "change-other",
    object_type: "canonical",
    object_id: "canonical-other",
    from_snapshot_id: "snapshot-other",
    after_revision: null,
  };

  const pending = ctx.fieldOpenFeedItem(change);
  assert.equal(ctx.fieldRequestedSnapshotId, "snapshot-other");
  assert.equal(ctx._fieldSnapshotNavigationBaseline, "snapshot-history");

  assert.equal(ctx.fieldCancelPendingSnapshotNavigation(), true);
  assert.equal(stagedController.signal.aborted, true);
  assert.equal(ctx.fieldRequestedSnapshotId, "snapshot-history");
  assert.equal(await pending, false);
});

test("历史导航连续改目标后回到当前选择时，恢复整条导航链的原始 URL", async () => {
  const loadRequests = [];
  let pollScheduled = false;
  const ctx = context({
    activeView: "field",
    fieldRequestedSnapshotId: "",
    _syncUrl() {},
    playlistEventMapSchedulePoll({ immediate }) { pollScheduled = immediate; },
    playlistEventMapLoadView({ selectionToken }) {
      return new Promise((resolve) => {
        loadRequests.push({
          resolve: () => {
            if (Number(this._playlistEventMapSelectionToken || 0) === selectionToken) {
              this.playlistEventMapSnapshotId = this.fieldRequestedSnapshotId;
            }
            resolve();
          },
        });
      });
    },
    async fieldOpenCanonical() {
      throw new Error("失权的历史变化卡不应定位对象");
    },
  });
  const historyChange = (id, snapshotId) => ({
    id: `change-${id}`,
    object_type: "canonical",
    object_id: `canonical-${id}`,
    from_snapshot_id: snapshotId,
    after_revision: null,
  });

  const first = ctx.fieldOpenFeedItem(historyChange("h", "snapshot-history"));
  const second = ctx.fieldOpenFeedItem(historyChange("k", "snapshot-other"));
  ctx._playlistEventMapSelectionToken += 1;
  ctx.playlistEventMapSelectedKind = "canonical";
  ctx.playlistEventMapSelectedId = "canonical-current";

  loadRequests[0].resolve();
  assert.equal(await first, false);
  assert.equal(ctx.fieldRequestedSnapshotId, "snapshot-other");

  loadRequests[1].resolve();
  assert.equal(await second, false);
  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-1");
  assert.equal(ctx.fieldRequestedSnapshotId, "");
  assert.equal(ctx.playlistEventMapSelectedId, "canonical-current");
  assert.equal(pollScheduled, true);
});

test("历史变化卡加载中改选当前星点时，保留当前快照与更新选择", async () => {
  let releaseLoad;
  const ctx = context({
    activeView: "field",
    fieldRequestedSnapshotId: "",
    _syncUrl() {},
    playlistEventMapLoadView({ selectionToken }) {
      return new Promise((resolve) => {
        releaseLoad = () => {
          if (Number(this._playlistEventMapSelectionToken || 0) === selectionToken) {
            this.playlistEventMapSnapshotId = this.fieldRequestedSnapshotId;
          }
          resolve();
        };
      });
    },
    async fieldOpenCanonical() {
      throw new Error("旧变化卡不应继续定位对象");
    },
  });
  const change = {
    id: "change-history",
    object_type: "canonical",
    object_id: "canonical-history",
    from_snapshot_id: "snapshot-history",
    after_revision: null,
  };

  const pending = ctx.fieldOpenFeedItem(change);
  ctx._playlistEventMapSelectionToken += 1;
  ctx.playlistEventMapSelectedKind = "canonical";
  ctx.playlistEventMapSelectedId = "canonical-current";
  releaseLoad();

  assert.equal(await pending, false);
  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-1");
  assert.equal(ctx.fieldRequestedSnapshotId, "");
  assert.equal(ctx.playlistEventMapSelectedId, "canonical-current");
});

test("历史变化卡加载中打开当前快照变化时，由旧导航恢复原始 URL", async () => {
  let historyLoadAborted = false;
  const opened = [];
  const ctx = context({
    activeView: "field",
    fieldRequestedSnapshotId: "",
    _abortCtrl(name) {
      this[name]?.abort?.();
      this[name] = null;
    },
    _syncUrl() {},
    playlistEventMapLoadView({ selectionToken }) {
      const controller = new AbortController();
      this._playlistEventMapStagedAbortCtrl = controller;
      return new Promise((resolve) => {
        controller.signal.addEventListener("abort", () => {
          historyLoadAborted = true;
          resolve();
        }, { once: true });
      });
    },
    playlistEventMapSchedulePoll() {},
    async fieldOpenCanonical(id) {
      opened.push(id);
      return true;
    },
  });
  const historyChange = {
    id: "change-history",
    object_type: "canonical",
    object_id: "canonical-history",
    from_snapshot_id: "snapshot-history",
    after_revision: null,
  };
  const currentChange = {
    id: "change-current",
    object_type: "canonical",
    object_id: "canonical-current",
    to_snapshot_id: "snapshot-1",
    after_revision: { point_index: 22 },
  };

  const pendingHistory = ctx.fieldOpenFeedItem(historyChange);
  const current = ctx.fieldOpenFeedItem(currentChange);

  assert.equal(await current, true);
  assert.equal(ctx.fieldRequestedSnapshotId, "");
  assert.equal(historyLoadAborted, true);
  assert.equal(await pendingHistory, false);
  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-1");
  assert.equal(ctx.fieldRequestedSnapshotId, "");
  assert.deepEqual(opened, ["canonical-current"]);
  assert.equal(ctx.fieldSelectedChange, currentChange);
});

test("变化卡等待完整点集时出现新选择，不再用预览索引打开旧对象", async () => {
  let releaseScene;
  const opened = [];
  const ctx = context({
    activeView: "field",
    playlistEventMapWaitForCompleteScene: () => new Promise((resolve) => { releaseScene = resolve; }),
    async fieldOpenCanonical(id) {
      opened.push(id);
      return true;
    },
  });
  const change = {
    id: "change-old",
    object_type: "canonical",
    object_id: "canonical-old",
    to_snapshot_id: "snapshot-1",
    after_revision: { point_index: 37 },
  };

  const pending = ctx.fieldOpenFeedItem(change);
  assert.deepEqual(opened, []);
  ctx._playlistEventMapSelectionToken += 1;
  releaseScene();

  assert.equal(await pending, false);
  assert.deepEqual(opened, []);
  assert.equal(ctx.fieldSelectedChange, null);
});

test("变化列表打开故事时复用外层选择代次", async () => {
  let canonicalOptions = null;
  const ctx = context({
    activeView: "field",
    _playlistEventMapSelectionToken: 12,
    async loadStoryDetail(id) {
      this.storySelectedId = id;
      this._storyDetailRequestToken += 1;
      this.storyDetail = {
        revisions: [{ snapshot_id: "snapshot-1", member_ids: ["canonical-1"] }],
        current_trajectory: { snapshot_id: "snapshot-1", nodes: [], edges: [] },
      };
    },
    async fieldOpenCanonical(_id, _pointIndex, options) {
      canonicalOptions = options;
      return true;
    },
    playlistEventMapSetDetailTab() {},
    playlistEventMapController: () => ({ setStoryPath() {} }),
  });

  const selected = await ctx.fieldOpenStory("story-1", { selectionToken: 12 });

  assert.equal(selected, true);
  assert.equal(ctx._playlistEventMapSelectionToken, 12);
  assert.equal(canonicalOptions.selectionToken, 12);
  assert.equal(canonicalOptions.requestGuard(), true);
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

test("V2 主界面区分主视图、临时工具与星域上下文", async () => {
  const template = await readFile(new URL("../../../templates/app/views/field-v2.html", import.meta.url), "utf8");
  const controller = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  const navigation = await readFile(new URL("../../app/navigation.js", import.meta.url), "utf8");
  const domains = await readFile(new URL("../../../templates/app/views/domains-v2.html", import.meta.url), "utf8");
  const operations = await readFile(new URL("../../../templates/app/views/jobs.html", import.meta.url), "utf8");
  const stories = await readFile(new URL("../../../templates/app/views/stories.html", import.meta.url), "utf8");
  const playlistModel = await readFile(new URL("../event-map-model.js", import.meta.url), "utf8");

  assert.match(template, /\['now','replay','story','verify'\]/);
  assert.match(template, /fieldSetPrimaryView\('map'\)[^>]*>星域</);
  assert.match(template, /fieldSetPrimaryView\('list'\)[^>]*>列表</);
  assert.match(template, />事件列表</);
  assert.match(template, />查找当前窗口</);
  assert.match(template, />筛选当前窗口</);
  assert.doesNotMatch(template, /当前条件|playlistEventMapFilterChips|搜索与筛选/);
  assert.doesNotMatch(template, /3D 空间|平面俯视|playlistEventMapCameraMode|playlistEventMapSetCameraMode/);
  assert.match(template, /三维语义空间 · 左键旋转/);
  assert.match(template, /星域变化/);
  assert.match(template, /fieldObservationRailVisible\(\)/);
  assert.match(template, /fieldObservationRailActionLabel\(\)/);
  assert.match(template, /fieldInspectorCloseLabel\(\)/);
  assert.match(template, /返回当前星域/);
  assert.match(controller, /setStoryPath\(path\)/);
  assert.match(controller, /source_point_index/);
  assert.match(controller, /target_point_index/);
  assert.match(controller, /cameraState\(\)/);
  assert.match(controller, /restoreCameraState\(state\)/);
  assert.match(controller, /storyVisibleEdgeCount/);
  assert.match(operations, /返回观测现场/);
  assert.match(stories, /storyQueueTabs\(\)/);
  assert.deepEqual(context().storyQueueTabs().map((item) => item.label), ["值得阅读", "已关注", "已形成", "初步线索"]);
  assert.match(stories, /标记本次更新已读/);
  assert.match(stories, /storyIncomingEdges\(node\)/);
  assert.match(stories, /故事变化/);
  assert.match(stories, /技术与识别审计/);
  assert.match(stories, /完整快照审计/);
  assert.match(stories, /fixed inset-y-0 right-0/);
  assert.match(stories, /storySelectedId \? 'hidden lg:flex' : 'flex'/);
  assert.doesNotMatch(stories, /Stable Story|节点 \+|evidenceId\.slice/);
  assert.match(playlistModel, /"briefs"/);
  assert.match(navigation, /key: "field"/);
  assert.match(navigation, /key: "stories"/);
  assert.doesNotMatch(navigation, /key: "briefs"/);
  assert.match(navigation, /key: "library"/);
  assert.match(navigation, /key: "operations"/);
  assert.doesNotMatch(template, /playlistSubview==='main'|Playlist Settings \(subview\)/);
});

test("观测域目录复用老板首页的正方形头像卡片", async () => {
  const domains = await readFile(new URL("../../../templates/app/views/domains-v2.html", import.meta.url), "utf8");

  assert.match(domains, /xl:grid-cols-4/);
  assert.match(domains, /rounded-xl border border-slate-800 bg-slate-950\/20/);
  assert.match(domains, /style="aspect-ratio: 1 \/ 1;"/);
  assert.match(domains, /assetContentUrl\(domain\.avatar_asset\)/);
  assert.match(domains, /h-full w-full object-cover/);
  assert.match(domains, /duration-500 ease-out group-hover:scale-110/);
  assert.match(domains, /raelyn-vignette/);
  assert.match(domains, /x-if="!domain\.avatar_asset"/);
  assert.match(domains, /clearAssetRef\(domain, 'avatar_asset'\)/);
  assert.match(domains, /domain\.media_preview/);
  assert.match(domains, /assetContentUrl\(media\.avatar_asset\)/);
  assert.match(domains, /domain\.media_count/);
  assert.match(domains, /border-2 border-black\/20/);
  assert.doesNotMatch(domains, /border-2 border-(?:slate-950|white)/);
  assert.match(domains, /domain\.video_count/);
  assert.match(domains, />待观察变化</);
  assert.match(domains, />故事</);
  assert.match(domains, />星点</);
  assert.doesNotMatch(domains, /domain\.background_asset|preview_points|relative h-44/);
});

test("观测域目录的管理信源进入全部信源列表，不再打开旧目录", async () => {
  const domains = await readFile(new URL("../../../templates/app/views/domains-v2.html", import.meta.url), "utf8");
  const switched = [];
  const ctx = context({
    libraryTab: "records",
    libraryScope: "domain",
    switchView(view) { switched.push(view); },
  });

  ctx.openGlobalSources();

  assert.equal(ctx.libraryTab, "sources");
  assert.equal(ctx.libraryScope, "global");
  assert.deepEqual(switched, ["library"]);
  assert.match(domains, /@click="openGlobalSources\(\)">管理信源<\/button>/);
  assert.doesNotMatch(domains, /管理观测域与信源|switchView\('playlists'\)/);
});

test("侧栏资料库与观测域设置固定进入当前观测域范围", async () => {
  const sidebar = await readFile(new URL("../../../templates/app/components/sidebar.html", import.meta.url), "utf8");
  const settings = await readFile(new URL("../../../templates/app/views/domain-settings.html", import.meta.url), "utf8");
  const toolbar = await readFile(new URL("../../../templates/app/views/library-toolbar.html", import.meta.url), "utf8");
  const switched = [];
  const ctx = context({
    libraryTab: "records",
    libraryScope: "global",
    videoMediaIds: ["global-media"],
    videoMediaDraftIds: ["global-media"],
    switchView(view) { switched.push(view); },
  });

  ctx.openCurrentDomainLibrary();

  assert.equal(ctx.libraryTab, "records");
  assert.equal(ctx.libraryScope, "domain");
  assert.deepEqual(ctx.videoMediaIds, []);
  assert.deepEqual(ctx.videoMediaDraftIds, []);
  assert.deepEqual(switched, ["library"]);
  assert.match(sidebar, /item\.key==='library' \? openCurrentDomainLibrary\(\) : switchView\(item\.key\)/);
  assert.match(settings, /@click="openCurrentDomainLibrary\(\)">管理资料<\/button>/);
  assert.match(toolbar, /libraryScope==='global' \? '全部媒体与视频' : '仅显示当前观测域'/);
});

test("域内来源记录不把全部信源回填成显式媒体筛选", async () => {
  const calls = [];
  let videoLoads = 0;
  const ctx = context({
    libraryTab: "records",
    libraryScope: "domain",
    videoMediaIds: ["media-1", "outside-media"],
    async ensureCurrentDomain() { return "domain-1"; },
    async api(path) {
      calls.push(path);
      return { items: [{ id: "media-1" }, { id: "media-2" }] };
    },
    async loadVideos() { videoLoads += 1; },
  });

  await ctx.loadLibrary();

  assert.match(calls[0], /^\/library\/sources\?domain_id=domain-1&scope=domain/);
  assert.deepEqual(ctx.libraryDomainMediaIds, ["media-1", "media-2"]);
  assert.deepEqual(ctx.videoMediaIds, ["media-1"]);
  assert.equal(videoLoads, 1);
});

test("故事主阅读路径不渲染 UUID、hash 或原始关系枚举", async () => {
  const stories = await readFile(new URL("../../../templates/app/views/stories.html", import.meta.url), "utf8");
  const auditStart = stories.indexOf(">技术与识别审计<");
  assert.ok(auditStart > 0, "技术审计必须存在且默认折叠");
  const readingPath = stories.slice(0, auditStart);

  assert.doesNotMatch(readingPath, /Stable Story|\.slice\(0,\s*8\)|x-text="[^"]*(?:canonical_id|edge_id|snapshot_id)/);
  assert.doesNotMatch(stories, />\s*(?:continuation|causes|response|corrects)\s*</);
  assert.doesNotMatch(stories, /before:absolute[^\n]*before:(?:top|bottom)/, "故事发展不应绘制贯穿事件的连续线");
  assert.match(stories.slice(auditStart), /story_identity_id/);
  assert.match(stories.slice(auditStart), /revision\.snapshot_id/);
});

test("故事页复用本地内联 SVG，不依赖图标字体", async () => {
  const stories = await readFile(new URL("../../../templates/app/views/stories.html", import.meta.url), "utf8");

  assert.doesNotMatch(stories, /material-symbols-/);
  for (const ligature of ["search", "arrow_back", "south", "vertical_align_bottom", "chevron_right", "link", "expand_more", "close", "open_in_new"]) {
    assert.doesNotMatch(stories, new RegExp(`>\\s*${ligature}\\s*<`), `不得把 ${ligature} 作为字体连字渲染`);
  }
  assert.match(stories, /<svg[^>]+aria-hidden="true"/);
});

test("故事页头使用可访问的书签图标切换关注状态", async () => {
  const stories = await readFile(new URL("../../../templates/app/views/stories.html", import.meta.url), "utf8");

  assert.match(stories, /:aria-label="storyDetail\?\.followed \? '取消关注' : '关注故事'"/);
  assert.match(stories, /x-show="story\.followed" aria-label="已关注"/);
  assert.doesNotMatch(stories, />\s*关注\s*</);
  assert.match(stories, /:aria-pressed="Boolean\(storyDetail\?\.followed\)"/);
  assert.match(stories, /:fill="storyDetail\?\.followed \? 'currentColor' : 'none'"/);
  assert.doesNotMatch(stories, /x-text="storyDetail\?\.followed \? '已关注' : '关注'"/);
});

test("故事时间线限制阅读宽度并保持高亮、关系和箭头对齐", async () => {
  const stories = await readFile(new URL("../../../templates/app/views/stories.html", import.meta.url), "utf8");

  assert.match(stories, /mx-auto w-full max-w-6xl px-5 py-6/);
  assert.match(stories, /article class="scroll-mt-16 px-3 py-4 outline-none"/);
  assert.match(stories, /svg class="self-center shrink-0 text-slate-600"/);
  assert.match(stories, /class="ml-9 mt-2 space-y-2"/);
  assert.match(stories, /flex items-center gap-2 border-b[^>]+px-3 py-2[^>]+tabular-nums/);
  assert.match(stories, /<rect x="3\.5" y="5\.5" width="17" height="15" rx="2"><\/rect>/);
  assert.doesNotMatch(stories, /article class="scroll-mt-16 py-5 outline-none"/);
});

test("全局深空表面系统覆盖主框架与全部核心界面", async () => {
  const css = await readFile(new URL("../../../input.css", import.meta.url), "utf8");
  const appShell = await readFile(new URL("../../../templates/app/layout/app-shell.html", import.meta.url), "utf8");
  const viewFiles = [
    "overview.html",
    "playlists.html",
    "media.html",
    "videos.html",
    "video.html",
    "jobs.html",
    "settings.html",
    "mcp-guide.html",
    "domains-v2.html",
    "stories.html",
    "playback-v2.html",
    "field-v2.html",
  ];
  const views = await Promise.all(viewFiles.map((file) => readFile(
    new URL(`../../../templates/app/views/${file}`, import.meta.url),
    "utf8",
  )));

  assert.match(appShell, /raelyn-app-environment/);
  assert.match(css, /\.raelyn-app-environment/);
  assert.match(css, /\.raelyn-surface-toolbar/);
  assert.match(css, /\.raelyn-surface-reading/);
  assert.match(css, /\.raelyn-surface-timeline/);
  assert.match(css, /\.raelyn-modal-surface/);
  views.forEach((view, index) => {
    assert.match(view, /raelyn-view/, `${viewFiles[index]} 缺少全局视图表面`);
  });
});
