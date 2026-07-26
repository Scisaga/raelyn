import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createPlaylistsModule } from "../../app/modules/playlists.js";
import { createPlaylistViewMethods } from "../playlist-model.js";

function context(manifest = {}, overrides = {}) {
  return {
    ...createPlaylistsModule(), ...createPlaylistViewMethods(),
    playlistEventMapStatus: manifest, playlistEventMapManifest: manifest, playlistEventMapTimelineMonths: manifest.monthly_distribution || [], playlistEventMapTimelineScope: "normal", playlistDetail: { earliest_date: "2026-01-01" },
    _todayIsoLocal: () => "2026-06-30", formatInteger: (value) => Number(value || 0).toLocaleString("en-US"),
    _abortCtrl(name) {
      this[name]?.abort?.();
      this[name] = null;
    },
    ...overrides,
  };
}

const monthly = Array.from({ length: 6 }, (_, index) => ({ month: `2026-0${index + 1}`, canonical_count: index + 1, record_count: index + 1 }));

function pollingContext(manifest, overrides = {}) {
  return context(manifest, {
    activeView: "playlist",
    playlistSubview: "analysis",
    playlistPageId: "playlist",
    playlistEventMapSnapshotId: String(manifest?.snapshot_id || ""),
    playlistEventMapScene: manifest?.snapshot_id ? { count: 1 } : null,
    _playlistEventMapController: manifest?.snapshot_id ? {} : null,
    _abortCtrl(name) {
      this[name]?.abort?.();
      this[name] = null;
    },
    ...overrides,
  });
}

async function withFakePollingEnvironment(callback) {
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  const originalDocument = globalThis.document;
  let nextTimerId = 1;
  const timers = new Map();
  const visibilityListeners = new Set();
  const fakeDocument = {
    hidden: false,
    visibilityState: "visible",
    addEventListener(type, listener) {
      if (type === "visibilitychange") visibilityListeners.add(listener);
    },
    removeEventListener(type, listener) {
      if (type === "visibilitychange") visibilityListeners.delete(listener);
    },
  };
  globalThis.setTimeout = (fn, delay = 0) => {
    const id = nextTimerId++;
    timers.set(id, { id, fn, delay: Number(delay) });
    return id;
  };
  globalThis.clearTimeout = (id) => timers.delete(id);
  globalThis.document = fakeDocument;
  const nextTimer = () => Array.from(timers.values()).sort((left, right) => left.id - right.id)[0] || null;
  const startNextTimer = () => {
    const timer = nextTimer();
    if (!timer) return null;
    timers.delete(timer.id);
    return timer.fn();
  };
  try {
    await callback({
      fakeDocument,
      timers,
      visibilityListeners,
      nextTimer,
      startNextTimer,
      dispatchVisibility(hidden) {
        fakeDocument.hidden = hidden;
        fakeDocument.visibilityState = hidden ? "hidden" : "visible";
        for (const listener of [...visibilityListeners]) listener();
      },
    });
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
}

test("时间域仍区分正常与全量，但地图只有一个固定长度窗口", () => {
  const ctx = context({ time_bounds: { start: "2020-01-01", end: "2026-06-30" }, monthly_distribution: monthly });
  assert.deepEqual(ctx.playlistEventMapTimelineBounds(), { start: "2026-01-01", end: "2026-06-30" });
  ctx.playlistEventMapTimelineScope = "full";
  assert.deepEqual(ctx.playlistEventMapTimelineBounds(), { start: "2020-01-01", end: "2026-06-30" });
  assert.equal(ctx.playlistEventMapHasExtendedRange(), true);
});

test("窗口长度固定、点击结束月和拖动预览不会出现第二范围", () => {
  const previews = [];
  const ctx = context(
    { time_bounds: { start: "2026-01-01", end: "2026-06-30" }, monthly_distribution: monthly },
    {
      playlistEventMapTimelineScope: "full",
      playlistEventMapWindowMonths: 3,
      _playlistEventMapController: {
        previewWindow: (value) => previews.push(value),
        cancelWindowPreview() {},
      },
    }
  );
  ctx.playlistEventMapInitializeWindow();
  assert.equal(ctx.playlistEventMapWindowStart, "2026-04-01"); assert.equal(ctx.playlistEventMapWindowEnd, "2026-06-30");
  ctx.playlistEventMapSetWindowEndIndex(2, { update: false });
  assert.equal(ctx.playlistEventMapWindowStart, "2026-01-01"); assert.equal(ctx.playlistEventMapWindowEnd, "2026-03-31");
  ctx.playlistEventMapSetWindowPreview(1, 3);
  assert.equal(ctx.playlistEventMapSelectionStyle(), "left:16.6667%;right:33.3333%");
  assert.deepEqual(ctx.playlistEventMapWindowDisplayDates(), { start: "2026-02-01", end: "2026-04-30" });
  assert.deepEqual(previews.at(-1), { windowStart: "2026-02-01", windowEnd: "2026-04-30" });
  assert.equal(ctx.playlistEventMapWindowStart, "2026-01-01");
  ctx.playlistEventMapClearWindowPreview();
  assert.equal(ctx.playlistEventMapSelectionStyle(), "left:0.0000%;right:50.0000%");
});

test("取消时间轴拖动恢复已提交窗口，完成拖动保留 GPU 预览直到提交", () => {
  let canceled = 0;
  const ctx = context(
    { time_bounds: { start: "2026-01-01", end: "2026-06-30" }, monthly_distribution: monthly },
    {
      playlistEventMapTimelineScope: "full",
      playlistEventMapWindowMonths: 3,
      _playlistEventMapController: {
        previewWindow() {},
        cancelWindowPreview: () => { canceled += 1; },
      },
    }
  );
  ctx.playlistEventMapSetWindowPreview(1, 3);
  ctx.playlistEventMapReleaseTimelineWindowDrag();
  assert.equal(canceled, 1);
  ctx.playlistEventMapSetWindowPreview(2, 4);
  ctx.playlistEventMapReleaseTimelineWindowDrag({ restore: false });
  assert.equal(canceled, 1);
});

test("Three.js 控制器在 Alpine 响应式状态中始终解包为原始对象", () => {
  const rawController = { id: "raw-controller" };
  const reactiveController = { id: "reactive-proxy" };
  const previousAlpine = globalThis.Alpine;
  globalThis.Alpine = { raw: (value) => value === reactiveController ? rawController : value };
  try {
    const ctx = context({}, { _playlistEventMapController: reactiveController });
    assert.equal(ctx.playlistEventMapController(), rawController);
  } finally {
    if (previousAlpine === undefined) delete globalThis.Alpine;
    else globalThis.Alpine = previousAlpine;
  }
});

test("窗口键盘按月与整窗移动，边界被钳制", () => {
  const ctx = context({ time_bounds: { start: "2026-01-01", end: "2026-06-30" }, monthly_distribution: monthly }, { playlistEventMapTimelineScope: "full", playlistEventMapWindowMonths: 2 });
  ctx.playlistEventMapInitializeWindow();
  ctx.playlistEventMapTimelineWindowKeydown({ key: "Home", preventDefault() {}, stopPropagation() {} });
  assert.equal(ctx.playlistEventMapWindowEnd, "2026-02-28");
  ctx.playlistEventMapTimelineWindowKeydown({ key: "PageDown", preventDefault() {}, stopPropagation() {} });
  assert.equal(ctx.playlistEventMapWindowEnd, "2026-04-30");
  ctx.playlistEventMapTimelineWindowKeydown({ key: "End", preventDefault() {}, stopPropagation() {} });
  assert.equal(ctx.playlistEventMapWindowEnd, "2026-06-30");
});

test("播放到末尾停止，不循环；末尾标签要求从头播放", () => {
  const ctx = context({ time_bounds: { start: "2026-01-01", end: "2026-06-30" }, monthly_distribution: monthly }, { playlistEventMapTimelineScope: "full", playlistEventMapWindowMonths: 3, playlistEventMapScene: { count: 1 } });
  ctx.playlistEventMapInitializeWindow();
  assert.equal(ctx.playlistEventMapPlaybackLabel(), "从头播放");
  assert.equal(ctx.playlistEventMapShiftWindow(1), false);
});

test("播放中不调实体排行，结束后只排入最新窗口一次", async () => {
  let calls = 0;
  const ctx = context({}, { playlistEventMapPlaying: true, playlistPageId: "p", playlistEventMapSnapshotId: "s", api: async () => { calls += 1; return []; } });
  await ctx.playlistEventMapLoadEntities({ silent: true });
  assert.equal(calls, 0);
  ctx.playlistEventMapPlaying = false;
  ctx._playlistEventMapEntityInFlight = true;
  await ctx.playlistEventMapLoadEntities({ silent: true });
  assert.equal(ctx._playlistEventMapEntityPending, true);
});

test("实体排行的过期窗口结果不会覆盖当前窗口", async () => {
  let resolve;
  const ctx = context({}, {
    playlistPageId: "p",
    playlistEventMapSnapshotId: "s",
    playlistEventMapWindowStart: "2026-01-01",
    playlistEventMapWindowEnd: "2026-01-31",
    api: async () => new Promise((done) => { resolve = done; }),
  });
  const pending = ctx.playlistEventMapLoadEntities({ silent: true });
  ctx.playlistEventMapWindowStart = "2026-02-01";
  ctx.playlistEventMapWindowEnd = "2026-02-28";
  resolve([{ name: "过期实体" }]);
  assert.deepEqual(await pending, []);
  assert.deepEqual(ctx.playlistEventMapEntities, []);
});

test("右侧按星域、主题、代表事件逐层展开，并按当前窗口请求事件", async () => {
  const topics = [
    { topic_index: 0, topic_id: "macro", level: 0, parent_topic_index: null, label: "市场与资产" },
    { topic_index: 1, topic_id: "local", level: 1, parent_topic_index: 0, label: "半导体 · equity" },
    { topic_index: 2, topic_id: "inactive", level: 1, parent_topic_index: 0, label: "无活动主题" },
  ];
  const calls = [];
  const ctx = context(
    { topics },
    {
      playlistPageId: "playlist", playlistEventMapSnapshotId: "snapshot", playlistEventMapWindowStart: "2025-01-01", playlistEventMapWindowEnd: "2025-12-31", playlistEventMapTypeFilter: "4",
      _playlistEventMapController: { topicActiveCount: (index) => ({ 0: 12, 1: 7, 2: 0 }[index] || 0) },
      api: async (path) => { calls.push(path); return { representatives: [{ canonical_id: "event", point_index: 9, title: "代表事件" }] }; },
      playlistEventMapSelectedKind: "topic", playlistEventMapSelectedId: "local",
    }
  );
  assert.equal(ctx.playlistEventMapTopicParent(topics[1]).label, "市场与资产");
  assert.deepEqual(ctx.playlistEventMapTopicChildren(topics[0]).map((item) => item.topic_id), ["local"]);
  const detail = await ctx.playlistEventMapLoadTopicDetail(topics[1]);
  assert.equal(detail.representatives[0].title, "代表事件");
  assert.match(calls[0], /snapshot_id=snapshot/);
  assert.match(calls[0], /start_date=2025-01-01/);
  assert.match(calls[0], /end_date=2025-12-31/);
  assert.match(calls[0], /event_type_code=4/);
});

test("右侧显式定位才调用镜头聚焦，普通选择不移动相机", () => {
  let focused = null;
  const ctx = context({}, {
    playlistEventMapSelectedKind: "canonical", playlistEventMapSelectedIndex: 37,
    _playlistEventMapController: { focusPoint: (index) => { focused = index; } },
  });
  ctx.playlistEventMapFocusSelectedCanonical();
  assert.equal(focused, 37);
});

test("analysis 空闲时常驻 compact manifest 单飞轮询，同快照不重复加载 scene", async () => {
  await withFakePollingEnvironment(async ({ nextTimer, startNextTimer, timers, visibilityListeners }) => {
    const calls = [];
    let fullLoads = 0;
    const manifest = { snapshot_id: "snapshot-1", status: "ready", build_status: "idle", building: false, backfill_job: null };
    const ctx = pollingContext(manifest, {
      api: async (path, options) => {
        calls.push({ path, options });
        return manifest;
      },
      playlistEventMapLoadView: async () => { fullLoads += 1; },
    });
    ctx.playlistEventMapSchedulePoll();
    assert.equal(nextTimer()?.delay, 10000);
    await startNextTimer();
    assert.equal(calls.length, 1);
    assert.match(calls[0].path, /manifest\?compact=true$/);
    assert.equal(calls[0].options.cache, "no-store");
    assert.equal(calls[0].options.signal instanceof AbortSignal, true);
    assert.equal(fullLoads, 0);
    assert.equal(timers.size, 1);
    assert.equal(nextTimer()?.delay, 10000);
    ctx.playlistEventMapStopPolling();
    assert.equal(timers.size, 0);
    assert.equal(visibilityListeners.size, 0);
  });
});

test("ready + building 按活动态 1.8 秒轮询，settings 在转为空闲后停止", async () => {
  await withFakePollingEnvironment(async ({ nextTimer, startNextTimer, timers, visibilityListeners }) => {
    const active = { snapshot_id: "snapshot-1", status: "ready", build_status: "running", building: true, backfill_job: null };
    const idle = { ...active, build_status: "idle", building: false };
    const ctx = pollingContext(active, {
      playlistSubview: "settings",
      api: async () => idle,
    });
    assert.equal(ctx.playlistEventMapPollIsActive(active), true);
    ctx.playlistEventMapSchedulePoll();
    assert.equal(nextTimer()?.delay, 1800);
    await startNextTimer();
    assert.equal(timers.size, 0);
    assert.equal(visibilityListeners.size, 0);

    const idleSettings = pollingContext(idle, { playlistSubview: "settings" });
    idleSettings.playlistEventMapSchedulePoll();
    assert.equal(timers.size, 0);
  });
});

test("compact manifest 发现新 snapshot 时只完整加载一次", async () => {
  await withFakePollingEnvironment(async ({ startNextTimer }) => {
    const next = { snapshot_id: "snapshot-2", status: "ready", build_status: "idle", building: false, backfill_job: null };
    let fullLoads = 0;
    const ctx = pollingContext(
      { ...next, snapshot_id: "snapshot-1" },
      {
        api: async () => next,
        playlistEventMapLoadView: async ({ schedulePolling }) => {
          assert.equal(schedulePolling, false);
          fullLoads += 1;
          ctx.playlistEventMapSnapshotId = "snapshot-2";
          ctx.playlistEventMapScene = { count: 2 };
        },
      }
    );
    ctx.playlistEventMapSchedulePoll();
    await startNextTimer();
    await startNextTimer();
    assert.equal(fullLoads, 1);
    ctx.playlistEventMapStopPolling();
  });
});

test("新快照完整 manifest 请求失败时保留当前 scene", async () => {
  await withFakePollingEnvironment(async () => {
    let destroyed = 0;
    const controller = { destroy: () => { destroyed += 1; } };
    const scene = { count: 1 };
    const ctx = pollingContext(
      { snapshot_id: "snapshot-1", status: "ready" },
      {
        _playlistEventMapController: controller,
        playlistEventMapScene: scene,
        playlistEventMapLoadManifest: async () => null,
        $refs: {},
      }
    );

    await ctx.playlistEventMapLoadView({ silent: true, schedulePolling: false });

    assert.equal(ctx.playlistEventMapController(), controller);
    assert.equal(ctx.playlistEventMapScene, scene);
    assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-1");
    assert.equal(destroyed, 0);
  });
});

test("页面隐藏会停止并中止请求，恢复可见后立即追平", async () => {
  await withFakePollingEnvironment(async ({ dispatchVisibility, nextTimer, startNextTimer, timers }) => {
    let resolveManifest;
    let requestSignal = null;
    const manifest = { snapshot_id: "snapshot-1", status: "ready", build_status: "idle", building: false, backfill_job: null };
    const ctx = pollingContext(manifest, {
      api: async (_path, options) => {
        requestSignal = options.signal;
        return new Promise((resolve) => { resolveManifest = resolve; });
      },
    });
    ctx.playlistEventMapSchedulePoll({ immediate: true });
    assert.equal(nextTimer()?.delay, 0);
    const pendingPoll = startNextTimer();
    await Promise.resolve();
    dispatchVisibility(true);
    assert.equal(requestSignal.aborted, true);
    assert.equal(timers.size, 0);
    resolveManifest(manifest);
    await pendingPoll;
    dispatchVisibility(false);
    assert.equal(nextTimer()?.delay, 0);
    ctx.playlistEventMapStopPolling();
  });
});

test("切换 snapshot 清理点索引、选择和搜索，但保留可重解析的实体键", () => {
  const ctx = pollingContext(
    { snapshot_id: "snapshot-1" },
    {
      playlistEventMapEntityFilter: { normalized_key: "openai", entity_type: "organization", name: "OpenAI", point_index: 7 },
      playlistEventMapEntityIndices: new Set([7, 8]),
      playlistEventMapEntities: [{ normalized_key: "openai", point_index: 7 }],
      playlistEventMapSelectedIndex: 8,
      playlistEventMapSelectedKind: "canonical",
      playlistEventMapSelectedId: "canonical-old",
      playlistEventMapSelectedDetail: { point_index: 8 },
      playlistEventMapSearchQuery: "旧快照",
      playlistEventMapSearchResults: [{ point_index: 8 }],
      playlistEventMapTopicFocus: { topic_index: 2 },
      playlistEventMapTopicDetail: { topic_index: 2 },
      _playlistEventMapController: { setSelection() {} },
    }
  );
  const stableFilter = ctx.playlistEventMapStableEntityFilter();
  ctx.playlistEventMapResetSnapshotPinnedState(stableFilter);
  assert.deepEqual(ctx.playlistEventMapEntityFilter, {
    normalized_key: "openai",
    entity_type: "organization",
    name: "OpenAI",
  });
  assert.deepEqual([...ctx.playlistEventMapEntityIndices], []);
  assert.deepEqual(ctx.playlistEventMapEntities, []);
  assert.equal(ctx.playlistEventMapSelectedId, "");
  assert.equal(ctx.playlistEventMapSearchQuery, "");
  assert.deepEqual(ctx.playlistEventMapSearchResults, []);
  assert.equal(ctx.playlistEventMapTopicFocus, null);
  assert.equal(ctx.playlistEventMapTopicDetail, null);
});

test("代码与模板只保留单窗口时间状态", () => {
  const files = ["../event-map.js", "../event-map-model.js", "../../../templates/app/views/playlist.html", "../../app/modules/playlists.js"];
  const content = files.map((file) => readFileSync(new URL(file, import.meta.url), "utf8")).join("\n");
  assert.match(content, /playlistEventMapWindowStart/);
  assert.match(content, /playlistEventMapWindowEnd/);
  assert.match(content, /playlistEventMapTimelineWindowDragging/);
});

test("三维事件星图不再保留 Atlas、全期参照或实体卫星链", () => {
  const files = [
    "../event-map.js",
    "../event-map-model.js",
    "../../../templates/app/views/playlist.html",
    "../../../scripts/js-bundle.mjs",
  ];
  const content = files.map((file) => readFileSync(new URL(file, import.meta.url), "utf8")).join("\n");
  assert.match(content, /event-map-three/);
  assert.match(content, /setCameraMode/);
  assert.match(content, /anchor_title/);
  assert.match(content, /level === "topic" \? 80 : 24/);
  assert.match(content, /playlistEventMapSemanticLegend/);
  assert.match(content, /previewWindow/);
  assert.doesNotMatch(content, /event-map-atlas|embedding-atlas|playlistEventMapReference|全期参照|UnrealBloomPass|cloudPoints|haloPoints/);
  assert.doesNotMatch(content, /this\._playlistEventMapController(?:\?)*\./);
});
