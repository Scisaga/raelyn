import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createPlaylistsModule } from "../../app/modules/playlists.js";
import { createUrlStateMethods } from "../../services/url-state.js";
import { createPlaylistViewMethods } from "../playlist-model.js";
import { EventMapController } from "../event-map.js";
import { createV2ViewMethods } from "../v2-model.js";

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
    _playlistEventMapSceneComplete: Boolean(manifest?.snapshot_id),
    _playlistEventMapMetadataComplete: Boolean(manifest?.snapshot_id),
    _abortCtrl(name) {
      this[name]?.abort?.();
      this[name] = null;
    },
    ...overrides,
  });
}

test("已有快照在后台构建时说明当前星域可浏览", () => {
  const updating = context({ snapshot_id: "snapshot-1", building: true });
  assert.equal(updating.playlistEventMapStatusBadgeLabel(), "星域后台更新中");
  assert.match(updating.playlistEventMapStatusBadgeHint(), /当前星域可以正常浏览/);

  const firstBuild = context({ building: true });
  assert.equal(firstBuild.playlistEventMapStatusBadgeLabel(), "首次生成中");
  assert.match(firstBuild.playlistEventMapStatusBadgeHint(), /首个可浏览的星域快照/);
});

test("首屏 manifest 明确拆为 compact 启动、无覆盖主题元数据和延后覆盖统计", async () => {
  const calls = [];
  const ctx = context({}, {
    activeView: "field",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    api: async (path) => {
      calls.push(path);
      return { snapshot_id: "snapshot", status: "ready" };
    },
  });

  await ctx.playlistEventMapLoadManifest({ compact: true, snapshotId: "snapshot" });
  await ctx.playlistEventMapLoadManifest({ includeCoverage: false, snapshotId: "snapshot" });
  await ctx.playlistEventMapLoadManifest({ compact: true, includeCoverage: true, snapshotId: "snapshot" });

  assert.match(calls[0], /compact=true/);
  assert.doesNotMatch(calls[0], /include_coverage/);
  assert.match(calls[1], /include_coverage=false/);
  assert.doesNotMatch(calls[1], /compact=true/);
  assert.match(calls[2], /compact=true/);
  assert.match(calls[2], /include_coverage=true/);
  assert.ok(calls.every((path) => path.includes("snapshot_id=snapshot")));
});

test("同一星域启动请求单飞，旧请求结束不会清理新请求", async () => {
  const resolvers = [];
  let requestCount = 0;
  const ctx = context({}, {
    activeView: "field",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    _playlistEventMapPlaylistId: "playlist",
    fieldRequestedSnapshotId: "",
    _playlistEventMapLoadViewRequest() {
      requestCount += 1;
      return new Promise((resolve) => resolvers.push(resolve));
    },
  });

  const first = ctx.playlistEventMapLoadView();
  const firstRequest = ctx._playlistEventMapLoadPromise;
  const shared = ctx.playlistEventMapLoadView();
  assert.equal(requestCount, 1);
  assert.equal(ctx._playlistEventMapLoadPromise, firstRequest);

  ctx.playlistEventMapStopLoad();
  const second = ctx.playlistEventMapLoadView();
  const secondRequest = ctx._playlistEventMapLoadPromise;
  assert.equal(requestCount, 2);
  assert.notEqual(secondRequest, firstRequest);

  resolvers[0]();
  await Promise.all([first, shared]);
  assert.equal(ctx._playlistEventMapLoadPromise, secondRequest);

  resolvers[1]();
  await second;
  assert.equal(ctx._playlistEventMapLoadPromise, null);
});

test("跨快照场景下载期间出现新选择时，旧导航不提交快照或清空选择", async () => {
  let releaseScene;
  let oldControllerDestroyed = false;
  let resetCount = 0;
  const oldController = { destroy() { oldControllerDestroyed = true; } };
  const ctx = context({}, {
    activeView: "field",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    fieldRequestedSnapshotId: "snapshot-history",
    playlistEventMapSnapshotId: "snapshot-current",
    playlistEventMapStatus: { snapshot_id: "snapshot-current", status: "ready" },
    playlistEventMapScene: { count: 1 },
    _playlistEventMapSceneComplete: true,
    _playlistEventMapMetadataComplete: true,
    _playlistEventMapSelectionToken: 4,
    _playlistEventMapController: oldController,
    playlistEventMapController: () => oldController,
    playlistEventMapObserveVisibility() {},
    playlistEventMapObserveTimelineTrack() {},
    playlistEventMapPreloadRenderer: async () => ({}),
    playlistEventMapStableEntityFilter: () => null,
    api: async (path) => path.includes("compact=true")
      ? { snapshot_id: "snapshot-history", status: "ready", dimension: 3, scene_record_size: 56, canonical_count: 0 }
      : { snapshot_id: "snapshot-history", status: "ready" },
    playlistEventMapFetchBinary: () => new Promise((resolve) => { releaseScene = resolve; }),
    playlistEventMapResetSnapshotPinnedState() { resetCount += 1; return true; },
  });

  const pending = ctx._playlistEventMapLoadViewRequest({ selectionToken: 4, schedulePolling: false });
  while (!releaseScene) await Promise.resolve();
  ctx._playlistEventMapSelectionToken = 5;
  releaseScene(new ArrayBuffer(0));
  await pending;

  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-current");
  assert.equal(ctx.playlistEventMapStatus.snapshot_id, "snapshot-current");
  assert.equal(ctx.playlistEventMapController(), oldController);
  assert.equal(oldControllerDestroyed, false);
  assert.equal(resetCount, 0);
});

test("当前点集仍在补齐时，显式历史快照请求不会被当前加载快速吞掉", async () => {
  let manifestCalls = 0;
  const currentController = {};
  const ctx = context({}, {
    activeView: "field",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    fieldRequestedSnapshotId: "snapshot-history",
    playlistEventMapSnapshotId: "snapshot-current",
    _playlistEventMapSceneComplete: false,
    _playlistEventMapSceneLoadingSnapshotId: "snapshot-current",
    _playlistEventMapMetadataComplete: false,
    _playlistEventMapMetadataPromise: Promise.resolve(null),
    _playlistEventMapMetadataSnapshotId: "snapshot-current",
    _playlistEventMapController: currentController,
    playlistEventMapController: () => currentController,
    playlistEventMapObserveVisibility() {},
    playlistEventMapObserveTimelineTrack() {},
    playlistEventMapPreloadRenderer: async () => ({}),
    async playlistEventMapLoadManifest() { manifestCalls += 1; return null; },
  });

  await ctx._playlistEventMapLoadViewRequest({ schedulePolling: false });

  assert.equal(manifestCalls, 1);
});

test("普通轮询加载启动后 URL 固定到历史快照时不得提交新快照", async () => {
  let resolveManifest;
  let sceneRequests = 0;
  const currentController = { destroy() {} };
  const ctx = context({}, {
    activeView: "field",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    fieldRequestedSnapshotId: "",
    playlistEventMapSnapshotId: "snapshot-current",
    playlistEventMapStatus: { snapshot_id: "snapshot-current", status: "ready" },
    playlistEventMapScene: { count: 0 },
    _playlistEventMapSceneComplete: true,
    _playlistEventMapMetadataComplete: true,
    _playlistEventMapController: currentController,
    playlistEventMapController: () => currentController,
    playlistEventMapObserveVisibility() {},
    playlistEventMapObserveTimelineTrack() {},
    playlistEventMapPreloadRenderer: async () => ({}),
    playlistEventMapLoadManifest() {
      return new Promise((resolve) => { resolveManifest = resolve; });
    },
    playlistEventMapFetchBinary() { sceneRequests += 1; return Promise.resolve(new ArrayBuffer(0)); },
  });

  const pollLoad = ctx._playlistEventMapLoadViewRequest({ silent: true, schedulePolling: false });
  while (!resolveManifest) await Promise.resolve();
  ctx.fieldRequestedSnapshotId = "snapshot-history";
  resolveManifest({
    snapshot_id: "snapshot-next",
    status: "ready",
    dimension: 3,
    scene_record_size: 56,
    canonical_count: 0,
  });
  await pollLoad;

  assert.equal(sceneRequests, 0);
  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-current");
  assert.equal(ctx.playlistEventMapController(), currentController);
});

test("跨快照隐藏准备会保留当前已完整点集仍在补齐的标签", async () => {
  let resolveTargetScene;
  const currentMetadataController = new AbortController();
  const currentMetadataPromise = new Promise(() => {});
  const currentController = { destroy() {} };
  const ctx = context({}, {
    activeView: "field",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    fieldRequestedSnapshotId: "snapshot-history",
    playlistEventMapSnapshotId: "snapshot-current",
    playlistEventMapStatus: { snapshot_id: "snapshot-current", status: "ready" },
    playlistEventMapScene: { count: 0 },
    _playlistEventMapSceneComplete: true,
    _playlistEventMapMetadataComplete: false,
    _playlistEventMapMetadataPromise: currentMetadataPromise,
    _playlistEventMapMetadataSnapshotId: "snapshot-current",
    _playlistEventMapMetadataAbortCtrl: currentMetadataController,
    _playlistEventMapSelectionToken: 4,
    _playlistEventMapController: currentController,
    playlistEventMapController: () => currentController,
    playlistEventMapObserveVisibility() {},
    playlistEventMapObserveTimelineTrack() {},
    playlistEventMapPreloadRenderer: async () => ({}),
    playlistEventMapStableEntityFilter: () => null,
    playlistEventMapLoadManifest({ compact }) {
      return Promise.resolve({
        snapshot_id: "snapshot-history",
        status: "ready",
        dimension: 3,
        scene_record_size: 56,
        canonical_count: 0,
        compact,
      });
    },
    playlistEventMapFetchBinary() {
      return new Promise((resolve) => { resolveTargetScene = resolve; });
    },
  });

  const historyLoad = ctx._playlistEventMapLoadViewRequest({ selectionToken: 4, schedulePolling: false });
  while (!resolveTargetScene) await Promise.resolve();
  assert.equal(currentMetadataController.signal.aborted, false);
  assert.equal(ctx._playlistEventMapMetadataPromise, currentMetadataPromise);

  ctx._playlistEventMapSelectionToken = 5;
  resolveTargetScene(new ArrayBuffer(0));
  await historyLoad;

  assert.equal(currentMetadataController.signal.aborted, false);
  assert.equal(ctx._playlistEventMapMetadataPromise, currentMetadataPromise);
  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-current");
});

test("历史快照隐藏准备失权后，当前预览继续补齐完整点集与标签", async () => {
  const originalDocument = globalThis.document;
  const originalCreate = EventMapController.create;
  let resolveCurrentScene;
  let resolveCurrentMetadata;
  let resolveHistoryScene;
  const fakeController = {
    destroyed: false,
    async whenFirstFrame() {},
    setSceneInteractive() {},
    finishInitialHydration() {},
    replaceScene() {},
    prepareProgressiveReveal: () => false,
    updateLayers() {},
    fitActiveWindow() {},
    startProgressiveReveal() {},
    updateManifest() {},
    setSelection() {},
    updateLabels() {},
    setStoryPath() {},
    isActiveIndex: () => true,
    hasActiveTopic: () => true,
    destroy() { this.destroyed = true; },
  };
  const mountTarget = { appendChild() {} };
  const compactManifest = (snapshotId) => ({
    snapshot_id: snapshotId,
    status: "ready",
    dimension: 3,
    scene_record_size: 56,
    canonical_count: 0,
    monthly_distribution: [],
  });

  globalThis.document = {
    hidden: false,
    visibilityState: "visible",
    createElement: () => ({ style: {}, remove() {} }),
  };
  EventMapController.create = async () => fakeController;
  try {
    const ctx = context(compactManifest("snapshot-current"), {
      activeView: "field",
      playlistSubview: "analysis",
      playlistPageId: "playlist",
      selectedPlaylistId: "playlist",
      fieldRequestedSnapshotId: "",
      $refs: { playlistEventMap: mountTarget },
      playlistEventMapObserveVisibility() {},
      playlistEventMapObserveTimelineTrack() {},
      playlistEventMapPreloadRenderer: async () => ({}),
      playlistEventMapStableEntityFilter: () => null,
      playlistEventMapResetSnapshotPinnedState: () => true,
      playlistEventMapLoadDeferredCoverage() {},
      playlistEventMapLoadEntities: async () => [],
      playlistEventMapSchedulePoll() {},
      playlistEventMapLoadManifest({ compact, snapshotId }) {
        const targetSnapshotId = String(snapshotId || "snapshot-current");
        if (compact) return Promise.resolve(compactManifest(targetSnapshotId));
        if (targetSnapshotId === "snapshot-current") {
          return new Promise((resolve) => { resolveCurrentMetadata = resolve; });
        }
        return Promise.resolve(compactManifest(targetSnapshotId));
      },
      playlistEventMapFetchBinary(path) {
        if (path.includes("preview_limit=")) return Promise.resolve(new ArrayBuffer(0));
        if (path.includes("snapshot_id=snapshot-current")) {
          return new Promise((resolve) => { resolveCurrentScene = resolve; });
        }
        return new Promise((resolve) => { resolveHistoryScene = resolve; });
      },
    });

    await ctx._playlistEventMapLoadViewRequest({ schedulePolling: false });
    const currentScenePromise = ctx._playlistEventMapFullScenePromise;
    const currentMetadataPromise = ctx._playlistEventMapMetadataPromise;
    const currentSceneController = ctx._playlistEventMapSceneAbortCtrl;
    const currentMetadataController = ctx._playlistEventMapMetadataAbortCtrl;

    assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-current");
    assert.equal(ctx._playlistEventMapSceneLoadingSnapshotId, "snapshot-current");
    assert.ok(currentScenePromise);
    assert.ok(currentMetadataPromise);

    ctx.fieldRequestedSnapshotId = "snapshot-history";
    ctx._playlistEventMapSelectionToken = 4;
    const historyLoad = ctx._playlistEventMapLoadViewRequest({ selectionToken: 4, schedulePolling: false });
    while (!resolveHistoryScene) await Promise.resolve();

    assert.equal(currentSceneController.signal.aborted, false);
    assert.equal(currentMetadataController.signal.aborted, false);
    assert.equal(ctx._playlistEventMapFullScenePromise, currentScenePromise);
    assert.equal(ctx._playlistEventMapMetadataPromise, currentMetadataPromise);

    ctx._playlistEventMapSelectionToken = 5;
    resolveHistoryScene(new ArrayBuffer(0));
    await historyLoad;

    assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-current");
    assert.equal(ctx._playlistEventMapSceneLoadingSnapshotId, "snapshot-current");
    assert.equal(currentSceneController.signal.aborted, false);
    assert.equal(currentMetadataController.signal.aborted, false);
    assert.equal(ctx._playlistEventMapFullScenePromise, currentScenePromise);
    assert.equal(ctx._playlistEventMapMetadataPromise, currentMetadataPromise);

    resolveCurrentScene(new ArrayBuffer(0));
    resolveCurrentMetadata(compactManifest("snapshot-current"));
    await Promise.all([currentScenePromise, currentMetadataPromise]);

    assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-current");
    assert.equal(ctx._playlistEventMapSceneComplete, true);
    assert.equal(ctx._playlistEventMapMetadataComplete, true);
    assert.equal(fakeController.destroyed, false);
  } finally {
    EventMapController.create = originalCreate;
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test("当前预览补齐期间返回当前星域会废弃隐藏准备的历史快照", async () => {
  let resolveHistoryScene;
  let currentControllerDestroyed = false;
  const currentController = {
    destroy() { currentControllerDestroyed = true; },
  };
  const ctx = context({}, {
    ...createV2ViewMethods(),
    activeView: "field",
    playlistSubview: "analysis",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    fieldRequestedSnapshotId: "snapshot-history",
    playlistEventMapSnapshotId: "snapshot-current",
    playlistEventMapStatus: { snapshot_id: "snapshot-current", status: "ready" },
    playlistEventMapScene: { count: 0 },
    _playlistEventMapSceneComplete: false,
    _playlistEventMapSceneLoadingSnapshotId: "snapshot-current",
    _playlistEventMapMetadataComplete: true,
    _playlistEventMapSelectionToken: 4,
    _playlistEventMapController: currentController,
    playlistEventMapController: () => currentController,
    playlistEventMapObserveVisibility() {},
    playlistEventMapObserveTimelineTrack() {},
    playlistEventMapPreloadRenderer: async () => ({}),
    playlistEventMapStableEntityFilter: () => null,
    playlistEventMapSchedulePoll() {},
    _syncUrl() {},
    playlistEventMapLoadManifest({ compact, snapshotId }) {
      const targetSnapshotId = String(snapshotId || "snapshot-current");
      return Promise.resolve(compact
        ? { snapshot_id: targetSnapshotId, status: "ready", dimension: 3, scene_record_size: 56, canonical_count: 0 }
        : { snapshot_id: targetSnapshotId, status: "ready" });
    },
    playlistEventMapFetchBinary() {
      return new Promise((resolve) => { resolveHistoryScene = resolve; });
    },
  });

  const historyLoad = ctx._playlistEventMapLoadViewRequest({ selectionToken: 4, schedulePolling: false });
  while (!resolveHistoryScene) await Promise.resolve();
  await ctx.fieldReturnToCurrentSnapshot();

  assert.equal(ctx.fieldRequestedSnapshotId, "");
  assert.equal(ctx._playlistEventMapSelectionToken, 5);
  resolveHistoryScene(new ArrayBuffer(0));
  await historyLoad;

  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-current");
  assert.equal(ctx.playlistEventMapStatus.snapshot_id, "snapshot-current");
  assert.equal(ctx.playlistEventMapController(), currentController);
  assert.equal(currentControllerDestroyed, false);
});

test("已显示历史快照返回当前时会直接请求未固定的最新快照", async () => {
  const originalDocument = globalThis.document;
  const originalCreate = EventMapController.create;
  let oldControllerDestroyed = false;
  let oldMountRemoved = false;
  const manifestSnapshotIds = [];
  const oldController = { destroy() { oldControllerDestroyed = true; } };
  const nextController = {
    async whenFirstFrame() {},
    updateLayers() {},
    fitActiveWindow() {},
    updateLabels() {},
    destroy() {},
  };
  globalThis.document = {
    hidden: false,
    visibilityState: "visible",
    createElement: () => ({ style: {}, remove() {} }),
  };
  EventMapController.create = async () => nextController;
  try {
    const ctx = context({}, {
      ...createV2ViewMethods(),
      activeView: "field",
      playlistSubview: "analysis",
      playlistPageId: "playlist",
      selectedPlaylistId: "playlist",
      fieldRequestedSnapshotId: "snapshot-history",
      playlistEventMapSnapshotId: "snapshot-history",
      playlistEventMapStatus: { snapshot_id: "snapshot-history", status: "ready" },
      playlistEventMapScene: { count: 0 },
      _playlistEventMapSceneComplete: true,
      _playlistEventMapMetadataComplete: true,
      _playlistEventMapSelectionToken: 9,
      _playlistEventMapController: oldController,
      _playlistEventMapMount: { remove() { oldMountRemoved = true; } },
      $refs: { playlistEventMap: { appendChild() {} } },
      playlistEventMapController() { return this._playlistEventMapController; },
      playlistEventMapObserveVisibility() {},
      playlistEventMapObserveTimelineTrack() {},
      playlistEventMapPreloadRenderer: async () => ({}),
      playlistEventMapStableEntityFilter: () => null,
      playlistEventMapResetSnapshotPinnedState: () => true,
      playlistEventMapLoadDeferredCoverage() {},
      playlistEventMapLoadEntities: async () => [],
      playlistEventMapSchedulePoll() {},
      _syncUrl() {},
      playlistEventMapLoadManifest({ compact, snapshotId }) {
        manifestSnapshotIds.push(String(snapshotId || ""));
        return Promise.resolve({
          snapshot_id: "snapshot-current",
          status: "ready",
          dimension: 3,
          scene_record_size: 56,
          canonical_count: 0,
          monthly_distribution: [],
          compact,
        });
      },
      playlistEventMapFetchBinary: async () => new ArrayBuffer(0),
    });

    await ctx.fieldReturnToCurrentSnapshot();

    assert.equal(manifestSnapshotIds[0], "");
    assert.equal(ctx.fieldRequestedSnapshotId, "");
    assert.equal(ctx._playlistEventMapSelectionToken, 10);
    assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-current");
    assert.equal(ctx.playlistEventMapStatus.snapshot_id, "snapshot-current");
    assert.equal(ctx.playlistEventMapController(), nextController);
    assert.equal(oldControllerDestroyed, true);
    assert.equal(oldMountRemoved, true);
  } finally {
    EventMapController.create = originalCreate;
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test("返回当前的场景请求失败时恢复原历史快照 URL", async () => {
  const historyController = { destroy() {} };
  const ctx = context({}, {
    ...createV2ViewMethods(),
    activeView: "field",
    playlistSubview: "analysis",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    fieldRequestedSnapshotId: "snapshot-history",
    playlistEventMapSnapshotId: "snapshot-history",
    playlistEventMapStatus: { snapshot_id: "snapshot-history", status: "ready" },
    playlistEventMapScene: { count: 0 },
    _playlistEventMapSceneComplete: true,
    _playlistEventMapMetadataComplete: true,
    _playlistEventMapController: historyController,
    playlistEventMapController: () => historyController,
    playlistEventMapObserveVisibility() {},
    playlistEventMapObserveTimelineTrack() {},
    playlistEventMapPreloadRenderer: async () => ({}),
    playlistEventMapStableEntityFilter: () => null,
    playlistEventMapSchedulePoll() {},
    playlistEventMapStopPolling() {},
    _syncUrl() {},
    playlistEventMapLoadManifest({ compact }) {
      return Promise.resolve({
        snapshot_id: "snapshot-current",
        status: "ready",
        dimension: 3,
        scene_record_size: 56,
        canonical_count: 0,
        compact,
      });
    },
    playlistEventMapFetchBinary: async () => { throw new Error("scene 下载失败"); },
  });

  const returned = await ctx.fieldReturnToCurrentSnapshot();

  assert.equal(returned, false);
  assert.equal(ctx.fieldRequestedSnapshotId, "snapshot-history");
  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-history");
  assert.equal(ctx.playlistEventMapStatus.snapshot_id, "snapshot-history");
  assert.equal(ctx.playlistEventMapController(), historyController);
  assert.equal(ctx._fieldSnapshotNavigationTarget, "");
  assert.match(ctx.playlistEventMapError, /scene 下载失败/);
});

test("返回当前时最新快照尚未就绪会保留历史画布并恢复 URL", async () => {
  let historyDestroyed = false;
  const historyController = { destroy() { historyDestroyed = true; } };
  const ctx = context({}, {
    ...createV2ViewMethods(),
    activeView: "field",
    playlistSubview: "analysis",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    fieldRequestedSnapshotId: "snapshot-history",
    playlistEventMapSnapshotId: "snapshot-history",
    playlistEventMapStatus: { snapshot_id: "snapshot-history", status: "ready" },
    playlistEventMapManifest: { snapshot_id: "snapshot-history", status: "ready" },
    playlistEventMapScene: { count: 0 },
    _playlistEventMapSceneComplete: true,
    _playlistEventMapMetadataComplete: true,
    _playlistEventMapController: historyController,
    playlistEventMapController: () => historyController,
    playlistEventMapObserveVisibility() {},
    playlistEventMapObserveTimelineTrack() {},
    playlistEventMapPreloadRenderer: async () => ({}),
    playlistEventMapStableEntityFilter: () => null,
    playlistEventMapSchedulePoll() {},
    playlistEventMapStopPolling() {},
    _syncUrl() {},
    playlistEventMapLoadManifest() {
      return Promise.resolve({ snapshot_id: null, status: "running", build_status: "running" });
    },
  });

  const returned = await ctx.fieldReturnToCurrentSnapshot();

  assert.equal(returned, false);
  assert.equal(ctx.fieldRequestedSnapshotId, "snapshot-history");
  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-history");
  assert.equal(ctx.playlistEventMapStatus.snapshot_id, "snapshot-history");
  assert.equal(ctx.playlistEventMapController(), historyController);
  assert.equal(historyDestroyed, false);
});

test("返回当前加载期间选择历史星点会取消切换并恢复历史 URL", async () => {
  let currentSceneRequestStarted = false;
  let currentSceneRequestAborted = false;
  const urlStates = [];
  const historyController = {
    destroyed: false,
    isActiveIndex: (index) => index === 0,
    hasActiveTopic: () => true,
    setSelection() {},
    updateLayers() {},
    destroy() { this.destroyed = true; },
  };
  const historyScene = {
    count: 1,
    x: new Float32Array([0]),
    y: new Float32Array([0]),
    z: new Float32Array([0]),
    startDay: new Int32Array([0]),
    endDay: new Int32Array([0]),
    eventType: new Uint8Array([0]),
    timePrecision: new Uint8Array([0]),
    flags: new Uint8Array([0]),
    memberCount: new Uint32Array([1]),
    macroTopicIndex: new Uint32Array([0]),
    localTopicIndex: new Uint32Array([0]),
    canonicalIds: ["canonical-history"],
  };
  const ctx = context({}, {
    ...createV2ViewMethods(),
    activeView: "field",
    playlistSubview: "analysis",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    fieldRequestedSnapshotId: "snapshot-history",
    playlistEventMapSnapshotId: "snapshot-history",
    playlistEventMapStatus: { snapshot_id: "snapshot-history", status: "ready" },
    playlistEventMapManifest: { snapshot_id: "snapshot-history", status: "ready", topics: [] },
    playlistEventMapScene: historyScene,
    _playlistEventMapSceneComplete: true,
    _playlistEventMapMetadataComplete: true,
    _playlistEventMapSelectionToken: 9,
    _playlistEventMapController: historyController,
    playlistEventMapController: () => historyController,
    playlistEventMapObserveVisibility() {},
    playlistEventMapObserveTimelineTrack() {},
    playlistEventMapPreloadRenderer: async () => ({}),
    playlistEventMapStableEntityFilter: () => null,
    playlistEventMapSchedulePoll() {},
    playlistEventMapStopPolling() {},
    _syncUrl() {
      urlStates.push({
        snapshotId: String(this.fieldRequestedSnapshotId || ""),
        canonicalId: String(this.fieldRequestedCanonicalId || ""),
      });
    },
    playlistEventMapLoadManifest({ compact }) {
      return Promise.resolve({
        snapshot_id: "snapshot-current",
        status: "ready",
        dimension: 3,
        scene_record_size: 56,
        canonical_count: 0,
        monthly_distribution: [],
        compact,
      });
    },
    playlistEventMapFetchBinary(_path, { signal }) {
      currentSceneRequestStarted = true;
      return new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => {
          currentSceneRequestAborted = true;
          reject(new DOMException("已取消", "AbortError"));
        }, { once: true });
      });
    },
    async api(path) {
      if (path.includes("brief-references")) return { items: [] };
      return { canonical_id: "canonical-history", title: "历史事件" };
    },
  });

  const returning = ctx.fieldReturnToCurrentSnapshot();
  while (!currentSceneRequestStarted) await Promise.resolve();
  const selected = await ctx.playlistEventMapSelectCanonical(0, "canonical-history");
  await returning;

  assert.equal(selected, true);
  assert.equal(currentSceneRequestAborted, true);
  assert.equal(ctx.fieldRequestedSnapshotId, "snapshot-history");
  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-history");
  assert.equal(ctx.playlistEventMapStatus.snapshot_id, "snapshot-history");
  assert.equal(ctx.playlistEventMapController(), historyController);
  assert.equal(historyController.destroyed, false);
  assert.equal(ctx.playlistEventMapSelectedId, "canonical-history");
  assert.deepEqual(urlStates.at(-1), {
    snapshotId: "snapshot-history",
    canonicalId: "canonical-history",
  });
});

test("当前画布已有历史导航时返回最新，再选当前星点会同时取消两次隐藏加载", async () => {
  let latestSceneStarted = false;
  let latestSceneAborted = false;
  const urlStates = [];
  const oldHistoryStage = new AbortController();
  const currentController = {
    destroyed: false,
    isActiveIndex: (index) => index === 0,
    hasActiveTopic: () => true,
    setSelection() {},
    updateLayers() {},
    destroy() { this.destroyed = true; },
  };
  const currentScene = {
    count: 1,
    x: new Float32Array([0]),
    y: new Float32Array([0]),
    z: new Float32Array([0]),
    startDay: new Int32Array([0]),
    endDay: new Int32Array([0]),
    eventType: new Uint8Array([0]),
    timePrecision: new Uint8Array([0]),
    flags: new Uint8Array([0]),
    memberCount: new Uint32Array([1]),
    macroTopicIndex: new Uint32Array([0]),
    localTopicIndex: new Uint32Array([0]),
    canonicalIds: ["canonical-current"],
  };
  const ctx = context({}, {
    ...createV2ViewMethods(),
    activeView: "field",
    playlistSubview: "analysis",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    fieldRequestedSnapshotId: "snapshot-history",
    playlistEventMapSnapshotId: "snapshot-current",
    playlistEventMapStatus: { snapshot_id: "snapshot-current", status: "ready" },
    playlistEventMapManifest: { snapshot_id: "snapshot-current", status: "ready", topics: [] },
    playlistEventMapScene: currentScene,
    _playlistEventMapSceneComplete: true,
    _playlistEventMapMetadataComplete: true,
    _playlistEventMapSelectionToken: 8,
    _fieldSnapshotNavigationToken: 3,
    _fieldSnapshotNavigationTarget: "snapshot-history",
    _fieldSnapshotNavigationBaseline: "",
    _fieldSnapshotNavigationOrigin: "snapshot-current",
    _playlistEventMapStagedAbortCtrl: oldHistoryStage,
    _playlistEventMapController: currentController,
    playlistEventMapController: () => currentController,
    playlistEventMapObserveVisibility() {},
    playlistEventMapObserveTimelineTrack() {},
    playlistEventMapPreloadRenderer: async () => ({}),
    playlistEventMapStableEntityFilter: () => null,
    playlistEventMapSchedulePoll() {},
    playlistEventMapStopPolling() {},
    _syncUrl() {
      urlStates.push({
        snapshotId: String(this.fieldRequestedSnapshotId || ""),
        canonicalId: String(this.fieldRequestedCanonicalId || ""),
      });
    },
    playlistEventMapLoadManifest({ compact }) {
      return Promise.resolve({
        snapshot_id: "snapshot-next",
        status: "ready",
        dimension: 3,
        scene_record_size: 56,
        canonical_count: 0,
        monthly_distribution: [],
        compact,
      });
    },
    playlistEventMapFetchBinary(_path, { signal }) {
      latestSceneStarted = true;
      return new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => {
          latestSceneAborted = true;
          reject(new DOMException("已取消", "AbortError"));
        }, { once: true });
      });
    },
    async api(path) {
      if (path.includes("brief-references")) return { items: [] };
      return { canonical_id: "canonical-current", title: "当前事件" };
    },
  });

  const returning = ctx.fieldReturnToCurrentSnapshot();
  while (!latestSceneStarted) await Promise.resolve();
  assert.equal(oldHistoryStage.signal.aborted, true);

  const selected = await ctx.playlistEventMapSelectCanonical(0, "canonical-current");
  await returning;

  assert.equal(selected, true);
  assert.equal(latestSceneAborted, true);
  assert.equal(ctx.fieldRequestedSnapshotId, "");
  assert.equal(ctx.playlistEventMapSnapshotId, "snapshot-current");
  assert.equal(ctx.playlistEventMapController(), currentController);
  assert.equal(currentController.destroyed, false);
  assert.equal(ctx.playlistEventMapSelectedId, "canonical-current");
  assert.equal(ctx._fieldSnapshotNavigationTarget, "");
  assert.deepEqual(urlStates.at(-1), {
    snapshotId: "",
    canonicalId: "canonical-current",
  });
});

test("返回当前加载期间关闭事件检查器会取消切换并恢复历史 URL", () => {
  const stagedController = new AbortController();
  const ctx = context({}, {
    ...createV2ViewMethods(),
    activeView: "field",
    fieldRequestedSnapshotId: "",
    playlistEventMapSnapshotId: "snapshot-history",
    _fieldSnapshotNavigationTarget: "@latest",
    _fieldSnapshotNavigationBaseline: "snapshot-history",
    _fieldSnapshotNavigationOrigin: "snapshot-history",
    _playlistEventMapStagedAbortCtrl: stagedController,
    playlistEventMapSelectedKind: "canonical",
    playlistEventMapSelectedId: "canonical-history",
    playlistEventMapSelectedIndex: 0,
    _playlistEventMapController: { setSelection() {}, updateLayers() {}, isActiveIndex: () => true },
    playlistEventMapStopPolling() {},
    _syncUrl() {},
  });

  ctx.playlistEventMapClearSelection();

  assert.equal(stagedController.signal.aborted, true);
  assert.equal(ctx.fieldRequestedSnapshotId, "snapshot-history");
  assert.equal(ctx.playlistEventMapSelectedKind, "");
  assert.equal(ctx._fieldSnapshotNavigationTarget, "");
});

test("返回当前加载期间再次点击历史主题会取消切换并恢复历史 URL", () => {
  const stagedController = new AbortController();
  const topic = { topic_id: "topic-history", topic_index: 0, label: "历史主题" };
  const controller = {
    hasActiveTopic: () => true,
    clearTopicFocus() {},
    updateLayers() {},
  };
  const ctx = context({ topics: [topic] }, {
    ...createV2ViewMethods(),
    activeView: "field",
    fieldRequestedSnapshotId: "",
    playlistEventMapSnapshotId: "snapshot-history",
    _fieldSnapshotNavigationTarget: "@latest",
    _fieldSnapshotNavigationBaseline: "snapshot-history",
    _fieldSnapshotNavigationOrigin: "snapshot-history",
    _playlistEventMapStagedAbortCtrl: stagedController,
    playlistEventMapSelectedKind: "topic",
    playlistEventMapSelectedId: "topic-history",
    playlistEventMapTopicFocus: topic,
    _playlistEventMapController: controller,
    playlistEventMapController: () => controller,
    playlistEventMapStopPolling() {},
    _syncUrl() {},
  });

  ctx.playlistEventMapSelectTopic(topic);

  assert.equal(stagedController.signal.aborted, true);
  assert.equal(ctx.fieldRequestedSnapshotId, "snapshot-history");
  assert.equal(ctx.playlistEventMapSelectedKind, "");
  assert.equal(ctx._fieldSnapshotNavigationTarget, "");
});

test("隐藏快照准备期间修改实体筛选会取消旧导航并保留当前快照", async () => {
  const stagedController = new AbortController();
  const ctx = context({}, {
    ...createV2ViewMethods(),
    activeView: "field",
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    fieldRequestedSnapshotId: "",
    playlistEventMapSnapshotId: "snapshot-history",
    playlistEventMapScene: { count: 0 },
    _fieldSnapshotNavigationTarget: "@latest",
    _fieldSnapshotNavigationBaseline: "snapshot-history",
    _fieldSnapshotNavigationOrigin: "snapshot-history",
    _playlistEventMapStagedAbortCtrl: stagedController,
    playlistEventMapPlaying: false,
    playlistEventMapWindowStart: "",
    playlistEventMapWindowEnd: "",
    playlistEventMapUpdateLayers() {},
    playlistEventMapStopPolling() {},
    _syncUrl() {},
    playlistEventMapFetchBinary: async () => new ArrayBuffer(0),
  });

  await ctx.playlistEventMapApplyEntityFilter({
    normalized_key: "apple",
    entity_type: "company",
  });

  assert.equal(stagedController.signal.aborted, true);
  assert.equal(ctx.fieldRequestedSnapshotId, "snapshot-history");
  assert.equal(ctx._fieldSnapshotNavigationTarget, "");
  assert.equal(ctx.playlistEventMapEntityFilter.normalized_key, "apple");
});

test("筛选计数只包含持续条件，不把主题点选当作筛选", () => {
  const ctx = context({}, {
    playlistEventMapTypeFilter: "4",
    playlistEventMapEntityFilter: { normalized_key: "apple", entity_type: "company" },
    playlistEventMapTopicFocus: { topic_id: "topic-1" },
  });

  assert.equal(ctx.playlistEventMapActiveFilterCount(), 2);
});

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

test("播放列表详情尚未返回时，普通星域也以今天而不是最后事件月作为截止日", () => {
  const ctx = context(
    { time_bounds: { start: "2020-01-01", end: "2021-07-31" }, monthly_distribution: [] },
    { playlistDetail: null, _todayIsoLocal: () => "2026-09-02" }
  );
  ctx.playlistEventMapInitializeWindow();
  assert.equal(ctx.playlistEventMapTimelineBounds().end, "2026-09-02");
  assert.equal(ctx.playlistEventMapWindowEnd, "2026-09-02");
  assert.equal(ctx.playlistEventMapWindowMonths, 12);
});

test("窗口宽度按三个月步进，点击结束月和拖动预览不会出现第二范围", () => {
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

test("窗口键盘按月与整窗移动，最小窗口为三个月且边界被钳制", () => {
  const ctx = context({ time_bounds: { start: "2026-01-01", end: "2026-06-30" }, monthly_distribution: monthly }, { playlistEventMapTimelineScope: "full", playlistEventMapWindowMonths: 3 });
  ctx.playlistEventMapInitializeWindow();
  ctx.playlistEventMapTimelineWindowKeydown({ key: "Home", preventDefault() {}, stopPropagation() {} });
  assert.equal(ctx.playlistEventMapWindowEnd, "2026-03-31");
  ctx.playlistEventMapTimelineWindowKeydown({ key: "PageDown", preventDefault() {}, stopPropagation() {} });
  assert.equal(ctx.playlistEventMapWindowEnd, "2026-06-30");
  ctx.playlistEventMapTimelineWindowKeydown({ key: "End", preventDefault() {}, stopPropagation() {} });
  assert.equal(ctx.playlistEventMapWindowEnd, "2026-06-30");
});

test("播放到末尾停止，不循环；末尾标签要求从头播放", () => {
  const ctx = context({ time_bounds: { start: "2026-01-01", end: "2026-06-30" }, monthly_distribution: monthly }, { playlistEventMapTimelineScope: "full", playlistEventMapWindowMonths: 3, playlistEventMapScene: { count: 1 } });
  ctx.playlistEventMapInitializeWindow();
  assert.equal(ctx.playlistEventMapPlaybackLabel(), "从头播放");
  assert.equal(ctx.playlistEventMapShiftWindow(1), false);
});

test("时间窗宽度只允许 3、6、9、12 个月", () => {
  const ctx = context(
    { time_bounds: { start: "2025-01-01", end: "2026-06-30" }, monthly_distribution: monthly },
    { playlistEventMapTimelineScope: "full" }
  );
  ctx.playlistEventMapInitializeWindow();
  ctx.playlistEventMapSetWindowMonths(5);
  assert.equal(ctx.playlistEventMapWindowMonths, 6);
  ctx.playlistEventMapSetWindowMonths(20);
  assert.equal(ctx.playlistEventMapWindowMonths, 12);
});

test("今日与本周焦点按事件发生日期取事件，并最多投影十张关联视频卡", async () => {
  const applied = [];
  const calls = [];
  const today = new Date();
  const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const ctx = context({}, {
    playlistPageId: "domain-1",
    selectedPlaylistId: "domain-1",
    playlistEventMapSnapshotId: "snapshot-1",
    playlistEventMapWindowStart: `${todayIso.slice(0, 7)}-01`,
    playlistEventMapWindowEnd: todayIso,
    playlistEventMapTypeFilter: "2",
    playlistEventMapEntityFilter: { normalized_key: "openai", entity_type: "organization" },
    playlistEventMapTopicFocus: { topic_id: "topic-1" },
    _playlistEventMapController: {
      setTimeHighlights: (payload) => applied.push(payload),
      setSelection() {},
    },
    assetContentUrl: (asset) => `/api/assets/${asset.id}/content`,
    api: async (path) => {
      calls.push(path);
      return {
        matched_event_total: 12,
        total: 12,
        shown_total: 10,
        video_total: 10,
        point_indices: Array.from({ length: 10 }, (_, index) => index + 1),
        items: Array.from({ length: 12 }, (_, index) => ({
          canonical_id: `canonical-${index}`,
          point_index: index + 1,
          primary_video: {
            video_id: `video-${index}`,
            thumbnail_asset: { id: `thumb-${index}` },
            media_avatar_asset: { id: `avatar-${index}` },
          },
        })),
      };
    },
  });

  await ctx.playlistEventMapLoadHighlights();

  assert.equal(calls.length, 2);
  assert.ok(calls.every((path) => path.includes("event_type_code=2")));
  assert.ok(calls.every((path) => path.includes("normalized_key=openai")));
  assert.ok(calls.every((path) => path.includes("topic_id=topic-1")));
  assert.ok(calls.every((path) => path.includes("limit=10")));
  assert.ok(calls.every((path) => path.includes("event_date_start=")));
  assert.ok(calls.every((path) => path.includes("event_date_end=")));
  assert.ok(calls.every((path) => path.includes(`window_start=${todayIso.slice(0, 7)}-01`)));
  assert.ok(calls.every((path) => path.includes(`window_end=${todayIso}`)));
  assert.ok(calls.every((path) => !path.includes("source_date_start=")));
  assert.equal(ctx.playlistEventMapTodayHighlights.items[0].primary_video.poster_url, "/api/assets/thumb-0/content");
  assert.equal(ctx.playlistEventMapTodayHighlights.items[0].primary_video.media_avatar_url, "/api/assets/avatar-0/content");
  assert.deepEqual(ctx.playlistEventMapTodayHighlights.items[0].point_indices, [1]);
  assert.equal(applied.at(-1).scope, "today");
  assert.equal(applied.at(-1).items.length, 10);
  assert.equal(ctx.playlistEventMapHighlightCount("today"), 10);
  assert.equal(ctx.playlistEventMapHighlightTotal("today"), 12);
  assert.equal(ctx.playlistEventMapHighlightVideoCount("today"), 10);
  assert.equal(ctx.playlistEventMapHighlightStatusLabel("today"), "10/12");
});

test("事件焦点规范化限制同一媒体最多两张卡片", () => {
  const ctx = context({}, {
    assetContentUrl: (asset) => `/api/assets/${asset.id}/content`,
  });
  const normalized = ctx.playlistEventMapNormalizeHighlightData({
    total: 7,
    shown_total: 7,
    video_total: 7,
    point_indices: [0, 1, 2, 3, 4, 5, 6],
    items: [
      ...Array.from({ length: 4 }, (_, index) => ({
        canonical_id: `media-a-event-${index}`,
        point_index: index,
        primary_video: {
          video_id: `media-a-video-${index}`,
          media_id: "media-a",
          media_name: "媒体 A",
        },
      })),
      ...Array.from({ length: 3 }, (_, index) => ({
        canonical_id: `media-b-event-${index}`,
        point_index: index + 4,
        primary_video: {
          video_id: `media-b-video-${index}`,
          media_id: "media-b",
          media_name: "媒体 B",
        },
      })),
    ],
  });

  assert.deepEqual(
    normalized.items.map((item) => item.primary_video.media_id),
    ["media-a", "media-a", "media-b", "media-b"],
  );
  assert.equal(normalized.shown_total, 4);
  assert.equal(normalized.video_total, 4);
  assert.equal(normalized.max_cards_per_media, 2);
  assert.equal(normalized.total, 7);
  assert.deepEqual(normalized.point_indices, [0, 1, 2, 3, 4, 5, 6]);
});

test("多个事件关联同一视频时合并成一张卡片并保留全部事件锚点", () => {
  const ctx = context({}, {
    assetContentUrl: (asset) => `/api/assets/${asset.id}/content`,
  });
  const normalized = ctx.playlistEventMapNormalizeHighlightData({
    total: 4,
    point_indices: [2, 7, 9, 11],
    items: [
      {
        canonical_id: "event-a",
        point_index: 2,
        title: "事件 A",
        representative_rank: 1,
        primary_video: { video_id: "video-shared", media_id: "media-a", media_name: "媒体 A" },
      },
      {
        canonical_id: "event-b",
        point_index: 7,
        title: "事件 B",
        representative_rank: 2,
        primary_video: { video_id: "video-shared", media_id: "media-a", media_name: "媒体 A" },
      },
      {
        canonical_id: "event-c",
        point_index: 9,
        title: "事件 C",
        representative_rank: 3,
        primary_video: { video_id: "video-second", media_id: "media-a", media_name: "媒体 A" },
      },
      {
        canonical_id: "event-d",
        point_index: 11,
        title: "事件 D",
        representative_rank: 4,
        primary_video: { video_id: "video-third", media_id: "media-a", media_name: "媒体 A" },
      },
    ],
  });

  assert.equal(normalized.items.length, 2);
  assert.equal(normalized.shown_total, 3);
  assert.equal(normalized.video_total, 2);
  assert.equal(normalized.items[0].primary_video.video_id, "video-shared");
  assert.deepEqual(normalized.items[0].point_indices, [2, 7]);
  assert.deepEqual(normalized.items[0].canonical_ids, ["event-a", "event-b"]);
  assert.equal(normalized.items[0].linked_event_count, 2);
  assert.deepEqual(normalized.items[0].linked_events.map((item) => item.title), ["事件 A", "事件 B"]);
  assert.deepEqual(normalized.point_indices, [2, 7, 9, 11]);
});

test("所选主题没有今日事件时保持主题边界，不以全域或其他日期事件补齐", async () => {
  const applied = [];
  const calls = [];
  const today = new Date();
  const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const ctx = context({}, {
    playlistPageId: "domain-1",
    selectedPlaylistId: "domain-1",
    playlistEventMapSnapshotId: "snapshot-1",
    playlistEventMapWindowStart: `${todayIso.slice(0, 7)}-01`,
    playlistEventMapWindowEnd: todayIso,
    playlistEventMapTopicFocus: { topic_id: "topic-without-current-events" },
    _playlistEventMapController: { setTimeHighlights: (payload) => applied.push(payload) },
    api: async (path) => {
      calls.push(path);
      return {
        matched_event_total: 0,
        total: 0,
        shown_total: 0,
        video_total: 0,
        point_indices: [],
        items: [],
      };
    },
  });

  await ctx.playlistEventMapLoadHighlights();

  assert.equal(calls.length, 2);
  assert.equal(calls.filter((path) => path.includes("topic_id=")).length, 2);
  assert.equal(ctx.playlistEventMapTodayHighlights.total, 0);
  assert.equal(ctx.playlistEventMapHighlightVideoCount("today"), 0);
  assert.equal(ctx.playlistEventMapHighlightStatusLabel("today"), "0");
  assert.deepEqual(applied.at(-1).items, []);
});

test("切换快照时立即废弃旧快照的焦点点位与视频卡", () => {
  let aborted = false;
  const applied = [];
  const ctx = context({}, {
    playlistEventMapTodayHighlights: { total: 2, point_indices: [91, 92], items: [{ canonical_id: "old" }] },
    playlistEventMapWeekHighlights: { total: 3, point_indices: [91, 92, 93], items: [{ canonical_id: "old" }] },
    playlistEventMapHighlightsLoading: true,
    playlistEventMapHighlightsError: "old error",
    _playlistEventMapHighlightsKey: "old-snapshot",
    _playlistEventMapHighlightsToken: 7,
    _playlistEventMapHighlightsAbortCtrl: { abort() { aborted = true; } },
    _playlistEventMapController: {
      setTimeHighlights: (payload) => applied.push(payload),
      setSelection() {},
    },
  });

  assert.equal(ctx.playlistEventMapResetSnapshotPinnedState(), true);

  assert.equal(aborted, true);
  assert.equal(ctx._playlistEventMapHighlightsToken, 8);
  assert.equal(ctx._playlistEventMapHighlightsKey, "");
  assert.equal(ctx.playlistEventMapTodayHighlights, null);
  assert.equal(ctx.playlistEventMapWeekHighlights, null);
  assert.equal(ctx.playlistEventMapHighlightsLoading, false);
  assert.equal(ctx.playlistEventMapHighlightsError, "");
  assert.deepEqual(applied.at(-1).point_indices, []);
});

test("事件焦点请求失败时不保留旧焦点计数或允许旧卡片复活", async () => {
  const applied = [];
  const today = new Date();
  const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const ctx = context({}, {
    playlistPageId: "domain-1",
    selectedPlaylistId: "domain-1",
    playlistEventMapSnapshotId: "snapshot-2",
    playlistEventMapWindowStart: `${todayIso.slice(0, 7)}-01`,
    playlistEventMapWindowEnd: todayIso,
    playlistEventMapTodayHighlights: { total: 8, point_indices: [1], items: [{ canonical_id: "stale" }] },
    playlistEventMapWeekHighlights: { total: 9, point_indices: [2], items: [{ canonical_id: "stale" }] },
    _playlistEventMapHighlightsKey: "old-key",
    _playlistEventMapController: { setTimeHighlights: (payload) => applied.push(payload) },
    api: async () => { throw new Error("network unavailable"); },
  });

  await ctx.playlistEventMapLoadHighlights();

  assert.equal(ctx.playlistEventMapTodayHighlights, null);
  assert.equal(ctx.playlistEventMapWeekHighlights, null);
  assert.equal(ctx._playlistEventMapHighlightsKey, "");
  assert.match(ctx.playlistEventMapHighlightsError, /network unavailable/);
  assert.deepEqual(applied.at(-1).point_indices, []);
});

test("筛选或主题在防抖期间变化时旧焦点响应不得提交", async () => {
  const resolvers = [];
  const applied = [];
  const today = new Date();
  const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const ctx = context({}, {
    playlistPageId: "domain-1",
    selectedPlaylistId: "domain-1",
    playlistEventMapSnapshotId: "snapshot-1",
    playlistEventMapWindowStart: `${todayIso.slice(0, 7)}-01`,
    playlistEventMapWindowEnd: todayIso,
    playlistEventMapTopicFocus: { topic_id: "topic-old" },
    _playlistEventMapSceneComplete: true,
    _playlistEventMapController: { setTimeHighlights: (payload) => applied.push(payload) },
    api: () => new Promise((resolve) => resolvers.push(resolve)),
  });

  const pending = ctx.playlistEventMapLoadHighlights();
  while (resolvers.length < 2) await Promise.resolve();
  ctx.playlistEventMapTopicFocus = { topic_id: "topic-new" };
  ctx.playlistEventMapScheduleHighlights({ delay: 60_000 });
  for (const resolve of resolvers) {
    resolve({ total: 1, shown_total: 1, video_total: 1, point_indices: [99], items: [] });
  }
  await pending;
  clearTimeout(ctx._playlistEventMapHighlightsTimer);

  assert.equal(ctx.playlistEventMapTodayHighlights, null);
  assert.equal(ctx.playlistEventMapWeekHighlights, null);
  assert.deepEqual(applied.at(-1).point_indices, []);
});

test("事件焦点按钮只显示最多十个，并在提示中说明证据排序与模糊日期排除", () => {
  const ctx = context({ snapshot_id: "snapshot-1", building: true }, {
    playlistEventMapTodayHighlights: {
      matched_event_total: 18,
      total: 16,
      shown_total: 10,
      video_total: 9,
      excluded_imprecise_total: 3,
      point_indices: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
      items: Array.from({ length: 10 }, (_, index) => ({ canonical_id: `event-${index}` })),
    },
  });

  assert.equal(ctx.playlistEventMapHighlightProcessing("today"), true);
  assert.equal(ctx.playlistEventMapHighlightStatusLabel("today"), "10/16");
  assert.match(ctx.playlistEventMapHighlightStatusHint("today"), /按事件发生日期筛选/);
  assert.match(ctx.playlistEventMapHighlightStatusHint("today"), /多信源佐证强度排序/);
  assert.match(ctx.playlistEventMapHighlightStatusHint("today"), /每个媒体最多 2 个视频/);
  assert.match(ctx.playlistEventMapHighlightStatusHint("today"), /同一视频只显示一张卡片并连接全部入选事件/);
  assert.match(ctx.playlistEventMapHighlightStatusHint("today"), /2 个事件没有可播放关联视频/);
  assert.match(ctx.playlistEventMapHighlightStatusHint("today"), /排除 3 个只有月\/年级日期/);
  assert.match(ctx.playlistEventMapHighlightStatusHint("today"), /星域后台任务正在运行/);
});

test("没有后台任务时事件焦点不显示运行指示", () => {
  const ctx = context({ snapshot_id: "snapshot-1", building: false }, {
    playlistEventMapTodayHighlights: {
      matched_event_total: 4,
      total: 4,
      shown_total: 4,
      video_total: 4,
      point_indices: [1, 2, 3, 4],
      items: Array.from({ length: 4 }, (_, index) => ({ canonical_id: `event-${index}` })),
    },
  });

  assert.equal(ctx.playlistEventMapHighlightProcessing("today"), false);
  assert.equal(ctx.playlistEventMapHighlightStatusLabel("today"), "4");
  assert.doesNotMatch(ctx.playlistEventMapHighlightStatusHint("today"), /后台任务正在运行/);
});

test("星域 URL 不再保存观察模式，只显式保存可分享时间窗", () => {
  const methods = createUrlStateMethods({ settingsTabs: [] });
  const ctx = {
    ...methods,
    selectedPlaylistId: "domain-1",
    playlistEventMapWindowStart: "2025-10-01",
    playlistEventMapWindowEnd: "2026-09-02",
    playlistEventMapSelectedKind: "",
    playlistEventMapSelectedId: "",
    fieldRequestedCanonicalId: "",
    fieldRequestedTopicId: "",
    fieldRequestedEvidenceId: "",
    fieldRequestedSnapshotId: "",
    fieldRequestedEntity: null,
    storySelectedId: "",
    fieldMode: "replay",
  };

  const query = new URLSearchParams(ctx._buildSearchForView("field").slice(1));

  assert.equal(query.get("mode"), null);
  assert.equal(query.get("window_start"), "2025-10-01");
  assert.equal(query.get("window_end"), "2026-09-02");
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

test("canonical 选择入口统一拒绝空 point_index", async () => {
  let selected = null;
  let apiCalls = 0;
  const ctx = context({}, {
    playlistPageId: "playlist",
    playlistEventMapSnapshotId: "snapshot-1",
    _playlistEventMapController: {
      isActiveIndex: () => true,
      setSelection: (index) => { selected = index; },
    },
    api: async () => { apiCalls += 1; return {}; },
  });

  await ctx.playlistEventMapSelectCanonical(null, "canonical-null");
  ctx.playlistEventMapSelectTopicRepresentative({ canonical_id: "canonical-null", point_index: null });

  assert.equal(selected, null);
  assert.equal(apiCalls, 0);
});

test("空点索引集合不会用无穷边界驱动镜头", () => {
  let fitCalls = 0;
  const ctx = context({}, {
    playlistEventMapScene: {
      x: new Float32Array([1]),
      y: new Float32Array([2]),
      z: new Float32Array([3]),
    },
    _playlistEventMapController: {
      isActiveIndex: () => false,
      fitBounds: () => { fitCalls += 1; },
    },
  });

  ctx.playlistEventMapFitIndices(new Set([null]));

  assert.equal(fitCalls, 0);
});

test("canonical 详情等待期间选择主题，旧详情不会覆盖新主题", async () => {
  let releaseCanonical;
  let apiCalls = 0;
  const topic = { topic_id: "topic-new", topic_index: 2, label: "新主题" };
  const ctx = context({ topics: [topic] }, {
    activeView: "field",
    playlistPageId: "playlist",
    playlistEventMapSnapshotId: "snapshot-1",
    playlistEventMapScene: { count: 10 },
    _playlistEventMapController: {
      isActiveIndex: () => true,
      hasActiveTopic: () => true,
      setSelection() {},
    },
    playlistEventMapUpdateLayers() {},
    playlistEventMapLoadTopicDetail: async () => {},
    _syncUrl() {},
    api: async () => {
      apiCalls += 1;
      return await new Promise((resolve) => { releaseCanonical = resolve; });
    },
  });

  const pending = ctx.playlistEventMapSelectCanonical(7, "canonical-old");
  await Promise.resolve();
  ctx.playlistEventMapSelectTopic(topic);
  releaseCanonical({ canonical_id: "canonical-old", title: "旧事件" });
  const committed = await pending;

  assert.equal(committed, false);
  assert.equal(apiCalls, 1);
  assert.equal(ctx.playlistEventMapSelectedKind, "topic");
  assert.equal(ctx.playlistEventMapSelectedId, "topic-new");
  assert.equal(ctx.playlistEventMapSelectedDetail.topic_id, "topic-new");
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

test("星域轮询在快照未切换时也会刷新今日与本周事件焦点", async () => {
  await withFakePollingEnvironment(async ({ startNextTimer }) => {
    const refreshes = [];
    const manifest = { snapshot_id: "snapshot-1", status: "ready", build_status: "idle", building: false, backfill_job: null };
    const ctx = pollingContext(manifest, {
      activeView: "field",
      api: async () => manifest,
      playlistEventMapScheduleHighlights: (options) => refreshes.push(options),
    });

    ctx.playlistEventMapSchedulePoll({ immediate: true });
    await startNextTimer();

    assert.deepEqual(refreshes, [{ delay: 0, force: true }]);
    ctx.playlistEventMapStopPolling();
  });
});

test("活动态 1.8 秒 manifest 轮询不会等频触发两次聚合焦点查询", async () => {
  await withFakePollingEnvironment(async ({ startNextTimer }) => {
    const refreshes = [];
    const manifest = { snapshot_id: "snapshot-1", status: "ready", build_status: "running", building: true, backfill_job: null };
    const ctx = pollingContext(manifest, {
      activeView: "field",
      _playlistEventMapHighlightsLastRequestAt: Date.now(),
      api: async () => manifest,
      playlistEventMapScheduleHighlights: (options) => refreshes.push(options),
    });

    ctx.playlistEventMapSchedulePoll({ immediate: true });
    await startNextTimer();

    assert.deepEqual(refreshes, []);
    ctx.playlistEventMapStopPolling();
  });
});

test("完整点集已就绪但标签元数据失败时，空闲轮询会单独触发恢复", async () => {
  await withFakePollingEnvironment(async ({ startNextTimer }) => {
    const manifest = { snapshot_id: "snapshot-1", status: "ready", build_status: "idle", building: false, backfill_job: null };
    let recoveryLoads = 0;
    const ctx = pollingContext(manifest, {
      _playlistEventMapSceneComplete: true,
      _playlistEventMapMetadataComplete: false,
      api: async () => manifest,
      playlistEventMapLoadView: async ({ schedulePolling }) => {
        assert.equal(schedulePolling, false);
        recoveryLoads += 1;
      },
    });

    ctx.playlistEventMapSchedulePoll({ immediate: true });
    await startNextTimer();
    assert.equal(recoveryLoads, 1);
    ctx.playlistEventMapStopPolling();
  });
});

test("预览点重编号期间搜索选择会等待完整场景", async () => {
  let releaseScene;
  let focused = null;
  let selected = null;
  const ctx = context({}, {
    activeView: "field",
    playlistEventMapSnapshotId: "snapshot-1",
    playlistEventMapWaitForCompleteScene: () => new Promise((resolve) => { releaseScene = resolve; }),
    playlistEventMapController: () => ({ focusPoint: (index) => { focused = index; } }),
    playlistEventMapSelectCanonical: (index, id) => { selected = [index, id]; },
    playlistEventMapCloseSearch() {},
  });

  const pending = ctx.playlistEventMapSelectSearchResult({ kind: "canonical", point_index: 37, id: "canonical-37" });
  await Promise.resolve();
  assert.equal(focused, null);
  assert.equal(selected, null);
  releaseScene();
  await pending;
  assert.equal(focused, 37);
  assert.deepEqual(selected, [37, "canonical-37"]);
});

test("搜索结果连续点击时只有后一次选择会在完整场景就绪后提交", async () => {
  const releases = [];
  const focused = [];
  const selected = [];
  const ctx = context({}, {
    activeView: "field",
    playlistEventMapSnapshotId: "snapshot-1",
    playlistEventMapWaitForCompleteScene: () => new Promise((resolve) => { releases.push(resolve); }),
    playlistEventMapController: () => ({ focusPoint: (index) => { focused.push(index); } }),
    playlistEventMapSelectCanonical: (index, id) => { selected.push([index, id]); },
    playlistEventMapCloseSearch() {},
  });

  const first = ctx.playlistEventMapSelectSearchResult({ kind: "canonical", point_index: 11, id: "canonical-a" });
  const second = ctx.playlistEventMapSelectSearchResult({ kind: "canonical", point_index: 22, id: "canonical-b" });
  assert.equal(releases.length, 2);

  releases[0]();
  await first;
  assert.deepEqual(selected, []);

  releases[1]();
  await second;
  assert.deepEqual(focused, [22]);
  assert.deepEqual(selected, [[22, "canonical-b"]]);
});

test("搜索结果等待完整场景期间关闭搜索，不再提交旧选择", async () => {
  let releaseScene;
  let selected = null;
  const ctx = context({}, {
    activeView: "field",
    playlistEventMapSnapshotId: "snapshot-1",
    _playlistEventMapSearchToken: 4,
    playlistEventMapWaitForCompleteScene: () => new Promise((resolve) => { releaseScene = resolve; }),
    playlistEventMapController: () => ({ focusPoint() {} }),
    playlistEventMapSelectCanonical: (index, id) => { selected = [index, id]; },
  });

  const pending = ctx.playlistEventMapSelectSearchResult({ kind: "canonical", point_index: 37, id: "canonical-37" });
  await Promise.resolve();
  ctx.playlistEventMapCloseSearch();
  releaseScene();
  await pending;

  assert.equal(selected, null);
});

test("搜索结果的空 point_index 不会误选第 0 点", async () => {
  let focused = null;
  let selected = null;
  const ctx = context({}, {
    activeView: "field",
    playlistEventMapSnapshotId: "snapshot-1",
    playlistEventMapWaitForCompleteScene: async () => {},
    playlistEventMapController: () => ({ focusPoint: (index) => { focused = index; } }),
    playlistEventMapSelectCanonical: (index, id) => { selected = [index, id]; },
  });

  await ctx.playlistEventMapSelectSearchResult({ kind: "canonical", point_index: null, id: "canonical-null" });

  assert.equal(focused, null);
  assert.equal(selected, null);
});

test("主题详情等待期间出现其他对象选择时不再提交旧主题", async () => {
  const resolvers = [];
  const ctx = context({}, {
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    playlistEventMapSnapshotId: "snapshot-1",
    playlistEventMapSelectedKind: "topic",
    playlistEventMapSelectedId: "topic-old",
    _playlistEventMapSelectionToken: 7,
    api: () => new Promise((resolve) => { resolvers.push(resolve); }),
  });

  const pending = ctx.playlistEventMapLoadTopicDetail({ topic_id: "topic-old" });
  assert.equal(resolvers.length, 2);
  ctx._playlistEventMapSelectionToken += 1;
  resolvers[0]({ topic_id: "topic-old", label: "旧主题" });
  resolvers[1]({ items: [{ id: "brief-old" }] });

  assert.equal(await pending, null);
  assert.equal(ctx.playlistEventMapTopicDetail, null);
});

test("旧主题详情失败不会覆盖新选择的错误状态", async () => {
  let rejectDetail;
  const ctx = context({}, {
    playlistPageId: "playlist",
    selectedPlaylistId: "playlist",
    playlistEventMapSnapshotId: "snapshot-1",
    playlistEventMapSelectedKind: "topic",
    playlistEventMapSelectedId: "topic-old",
    playlistEventMapError: "",
    _playlistEventMapSelectionToken: 9,
    api: (path) => path.includes("/brief-references")
      ? Promise.resolve({ items: [] })
      : new Promise((_resolve, reject) => { rejectDetail = reject; }),
  });

  const pending = ctx.playlistEventMapLoadTopicDetail({ topic_id: "topic-old" });
  ctx._playlistEventMapSelectionToken += 1;
  rejectDetail(new Error("旧主题请求失败"));
  await pending;

  assert.equal(ctx.playlistEventMapError, "");
});

test("等待标签期间切换快照会拒绝旧场景继续提交", async () => {
  let releaseMetadata;
  const metadata = new Promise((resolve) => { releaseMetadata = resolve; });
  const ctx = context({}, {
    playlistEventMapSnapshotId: "snapshot-1",
    playlistEventMapScene: { count: 1 },
    _playlistEventMapSceneComplete: true,
    _playlistEventMapMetadataComplete: false,
    _playlistEventMapMetadataPromise: metadata,
    _playlistEventMapMetadataSnapshotId: "snapshot-1",
  });

  const pending = ctx.playlistEventMapWaitForCompleteScene({ includeMetadata: true });
  ctx.playlistEventMapSnapshotId = "snapshot-2";
  ctx._playlistEventMapMetadataComplete = true;
  releaseMetadata();
  await assert.rejects(pending, /加载已中断/);
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

test("变化卡主动切换快照时可清空旧对象但保留已预占的选择意图", () => {
  const ctx = context({}, {
    _playlistEventMapSelectionToken: 6,
    playlistEventMapSelectedKind: "canonical",
    playlistEventMapSelectedId: "canonical-old",
    fieldRequestedCanonicalId: "canonical-next",
  });

  ctx.playlistEventMapResetSnapshotPinnedState(null, { selectionToken: 6 });

  assert.equal(ctx._playlistEventMapSelectionToken, 6);
  assert.equal(ctx.playlistEventMapSelectedKind, "");
  assert.equal(ctx.fieldRequestedCanonicalId, "canonical-next");
});

test("代码与模板只保留单窗口时间状态", () => {
  const files = ["../event-map.js", "../event-map-model.js", "../../../templates/app/views/field-v2.html", "../../app/modules/playlists.js"];
  const content = files.map((file) => readFileSync(new URL(file, import.meta.url), "utf8")).join("\n");
  assert.match(content, /playlistEventMapWindowStart/);
  assert.match(content, /playlistEventMapWindowEnd/);
  assert.match(content, /playlistEventMapTimelineWindowDragging/);
});

test("时间轴数据柱使用平直顶边", () => {
  const template = readFileSync(new URL("../../../templates/app/views/field-v2.html", import.meta.url), "utf8");
  assert.match(template, /playlistEventMapTimelineBarStyle\(item\)/);
  assert.doesNotMatch(template, /rounded-t/);
});

test("三维事件星图不再保留 Atlas、全期参照或实体卫星链", () => {
  const files = [
    "../event-map.js",
    "../event-map-model.js",
    "../../../templates/app/views/field-v2.html",
    "../../../scripts/js-bundle.mjs",
  ];
  const content = files.map((file) => readFileSync(new URL(file, import.meta.url), "utf8")).join("\n");
  assert.match(content, /event-map-three/);
  assert.doesNotMatch(content, /setCameraMode|playlistEventMapCameraMode|OrthographicCamera/);
  assert.match(content, /anchor_title/);
  assert.match(content, /level === "topic" \? 80 : 24/);
  assert.match(content, /playlistEventMapSemanticLegend/);
  assert.match(content, /previewWindow/);
  assert.doesNotMatch(content, /event-map-atlas|embedding-atlas|playlistEventMapReference|全期参照|UnrealBloomPass|cloudPoints|haloPoints/);
  assert.doesNotMatch(content, /this\._playlistEventMapController(?:\?)*\./);
});
