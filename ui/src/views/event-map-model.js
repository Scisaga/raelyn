import {
  EventMapController,
  countEventMapVisiblePoints,
  parseEventMapIndices,
  parseEventMapScene,
  preloadEventMapRenderer,
} from "./event-map.js";
import { assetPresignQueryValue } from "../shared/asset-delivery.js";

const MAP_PLAY_INTERVAL_MS = 1000;
const EVENT_MAP_ACTIVE_POLL_INTERVAL_MS = 1800;
const EVENT_MAP_IDLE_POLL_INTERVAL_MS = 10000;
const EVENT_MAP_HIGHLIGHT_REFRESH_INTERVAL_MS = 15000;
const EVENT_MAP_PREVIEW_LIMIT = 4_096;
const EVENT_MAP_PREVIEW_REVEAL_TARGET = 0.94;
const EVENT_MAP_PREVIEW_REVEAL_MS = 4_600;
// 自动识别取代旧的“全部滚轮旋转”设置，首次升级使用新的默认手势。
const EVENT_MAP_WHEEL_MODE_KEY = "raelyn.ui.eventMapWheelMode.v2";

const EVENT_MAP_LEVELS = {
  overview: { label: "语义星域", hint: "点的疏密表示当前观察窗口的事件聚集程度，颜色表示事件语义；双指滑动或拖动旋转，捏合缩放。" },
  topic: { label: "主题探索", hint: "二级主题标签来自结构化实体和事件类型；继续放大可浏览真实事件。" },
  event: { label: "事件近景", hint: "每颗星是一件真实事件；点击后核验记录、实体、故事与原始证据。" },
};

const EVENT_MAP_OBJECT_GUIDE = [
  { key: "region", label: "星域", shape: "彩色点云", description: "当前窗口内语义相近事件形成的点分布；点越密表示局部事件越集中。", visible: "远景" },
  { key: "topic", label: "主题", shape: "标签", description: "由结构化实体、事件类型和代表事件确定的两级主题。", visible: "远景与中景" },
  { key: "canonical", label: "真实事件", shape: "星点", description: "保守归并后的一件现实事件；坐标来自固定三维语义投影。", visible: "近景" },
  { key: "record", label: "事件记录", shape: "文档", description: "视频分析产生、归属于真实事件的原始记录。", visible: "选中事件后" },
  { key: "story", label: "故事线", shape: "短程曲线", description: "有证据的延续、因果、回应或纠正关系。", visible: "选中事件后" },
  { key: "entity", label: "实体与证据", shape: "详情分区", description: "事件涉及的对象和原始证据，不在星图中虚构卫星节点。", visible: "选中或筛选后" },
];

function isoMonth(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})/);
  return match ? `${match[1]}-${match[2]}` : "";
}
function monthIndex(value) {
  const normalized = isoMonth(value);
  if (!normalized) return null;
  const [year, month] = normalized.split("-").map(Number);
  return year * 12 + month - 1;
}
function monthFromIndex(index) {
  const value = Math.max(0, Math.floor(Number(index)));
  return `${String(Math.floor(value / 12)).padStart(4, "0")}-${String(value % 12 + 1).padStart(2, "0")}`;
}
function monthStart(value) { const normalized = isoMonth(value); return normalized ? `${normalized}-01` : ""; }
function monthEnd(value) {
  const normalized = isoMonth(value);
  if (!normalized) return "";
  const [year, month] = normalized.split("-").map(Number);
  return new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
}
function localToday() {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
function localWeekStart(value = localToday()) {
  const date = new Date(`${value}T12:00:00`);
  const day = date.getDay() || 7;
  date.setDate(date.getDate() - day + 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
function snapWindowMonths(value) {
  const months = Math.max(3, Math.min(12, Number(value || 12)));
  return [3, 6, 9, 12].reduce((best, item) => (
    Math.abs(item - months) < Math.abs(best - months) ? item : best
  ), 3);
}
function niceMonthStep(minimum) {
  const value = Math.max(1, Math.ceil(Number(minimum || 1)));
  return [1, 2, 3, 6, 12, 24, 36, 60, 120, 240, 600, 1200].find((item) => item >= value) || Math.ceil(value / 1200) * 1200;
}
function primaryPointer(event) { return event && event.isPrimary !== false && (event.pointerType !== "mouse" || Number(event.button || 0) === 0); }
function abortError(error) { return error && (error.name === "AbortError" || String(error.message || "").includes("aborted")); }
function eventMapPointIndex(value) {
  if (value === null || value === undefined || value === "") return NaN;
  const index = Number(value);
  return Number.isInteger(index) && index >= 0 ? index : NaN;
}
function uniqueEventMapPointIndices(values) {
  return [...new Set((Array.isArray(values) ? values : [])
    .map(eventMapPointIndex)
    .filter(Number.isInteger))];
}
function statusJob(manifest) { return manifest?.backfill_job || null; }
function documentIsHidden() {
  return typeof document !== "undefined" && (document.hidden === true || document.visibilityState === "hidden");
}
function rawEventMapController(controller) {
  const unwrap = globalThis.Alpine?.raw;
  return typeof unwrap === "function" ? unwrap(controller) : controller;
}
function emptyEventMapScene() {
  return {
    count: 0,
    x: new Float32Array(0),
    y: new Float32Array(0),
    z: new Float32Array(0),
    startDay: new Int32Array(0),
    endDay: new Int32Array(0),
    eventType: new Uint8Array(0),
    timePrecision: new Uint8Array(0),
    flags: new Uint8Array(0),
    memberCount: new Uint32Array(0),
    macroTopicIndex: new Uint32Array(0),
    localTopicIndex: new Uint32Array(0),
    canonicalIds: [],
  };
}
function normalizeTimeline(manifest) {
  const source = manifest?.monthly_distribution || manifest?.timeline || manifest?.months || [];
  return Array.isArray(source) ? source.map((item) => ({ month: isoMonth(item.month || item.period_date || item.date), canonical_count: Number(item.canonical_count ?? item.event_count ?? item.count ?? 0), record_count: Number(item.record_count ?? item.event_count ?? item.count ?? 0) })).filter((item) => item.month).sort((left, right) => left.month.localeCompare(right.month)) : [];
}

export function createPlaylistEventMapMethods() {
  return {
    playlistEventMapController() { return rawEventMapController(this._playlistEventMapController); },
    playlistEventMapRestoreWheelMode() {
      try {
        this.playlistEventMapWheelMode = localStorage.getItem(EVENT_MAP_WHEEL_MODE_KEY) === "zoom" ? "zoom" : "auto";
      } catch {
        this.playlistEventMapWheelMode = "auto";
      }
    },
    playlistEventMapSetWheelMode(mode) {
      this.playlistEventMapWheelMode = mode;
      this.playlistEventMapController()?.setWheelMode(mode);
      try {
        localStorage.setItem(EVENT_MAP_WHEEL_MODE_KEY, mode);
      } catch {
        // 存储不可用时仍保留本次页面内的选择。
      }
    },
    playlistEventMapControllerOptions(target, scene, manifest) {
      return {
        target,
        scene,
        manifest,
        wheelMode: this.playlistEventMapWheelMode,
        onSelect: (index, canonicalId) => this.playlistEventMapSelectCanonical(index, canonicalId),
        onTopic: (_index, topic) => this.playlistEventMapSelectTopic(topic),
        onViewport: (summary) => this.playlistEventMapHandleViewportSummary(summary),
        resolveVideoSource: (video) => this.playlistEventMapResolveVideoSource(video),
      };
    },
    async playlistEventMapResolveVideoSource(video) {
      const videoId = String(video?.video_id || video?.id || "").trim();
      if (!videoId) return null;
      const presign = assetPresignQueryValue(this.assetDelivery);
      const assets = await this.api(`/videos/${encodeURIComponent(videoId)}/assets?presign=${presign}&download=0&localize_title=0`);
      const videoAsset = (assets || []).find((asset) => asset?.type === "video");
      const thumbnailAsset = (assets || []).find((asset) => asset?.type === "thumbnail");
      const url = videoAsset ? this.assetContentUrl(videoAsset) : "";
      if (!url) return null;
      return {
        url,
        poster: thumbnailAsset ? this.assetContentUrl(thumbnailAsset) : String(video?.poster_url || video?.thumbnail_url || ""),
      };
    },
    playlistEventMapStopLoad() {
      this._playlistEventMapLifecycleGeneration = Number(this._playlistEventMapLifecycleGeneration || 0) + 1;
      this._playlistEventMapRequestToken = Number(this._playlistEventMapRequestToken || 0) + 1;
      this._playlistEventMapLoadKey = "";
      this._playlistEventMapSceneLoadingSnapshotId = "";
      this._playlistEventMapFullScenePromise = null;
      this._playlistEventMapFullSceneSnapshotId = "";
      this._playlistEventMapMetadataPromise = null;
      this._playlistEventMapMetadataSnapshotId = "";
      this._playlistEventMapDetailToken = Number(this._playlistEventMapDetailToken || 0) + 1;
      this._playlistEventMapTopicDetailToken = Number(this._playlistEventMapTopicDetailToken || 0) + 1;
      this._playlistEventMapSelectionToken = Number(this._playlistEventMapSelectionToken || 0) + 1;
      this._playlistEventMapSearchToken = Number(this._playlistEventMapSearchToken || 0) + 1;
      this._playlistEventMapEntityIndexToken = Number(this._playlistEventMapEntityIndexToken || 0) + 1;
      this._abortCtrl("_playlistEventMapStagedAbortCtrl");
      this._abortCtrl("_playlistEventMapAbortCtrl");
      this._abortCtrl("_playlistEventMapSceneAbortCtrl");
      this._abortCtrl("_playlistEventMapMetadataAbortCtrl");
      this._abortCtrl("_playlistEventMapCoverageAbortCtrl");
      this._abortCtrl("_playlistEventMapEntityAbortCtrl");
      this._abortCtrl("_playlistEventMapEntityIndexAbortCtrl");
      this._abortCtrl("_playlistEventMapSearchAbortCtrl");
      this._abortCtrl("_playlistEventMapDetailAbortCtrl");
      this._abortCtrl("_playlistEventMapHighlightsAbortCtrl");
      this._abortCtrl("_playlistEventMapTopicDetailAbortCtrl");
      clearTimeout(this._playlistEventMapEntitiesTimer); clearTimeout(this._playlistEventMapSearchTimer);
      this._playlistEventMapEntitiesTimer = null; this._playlistEventMapSearchTimer = null;
      this._playlistEventMapEntityInFlight = false; this._playlistEventMapEntityPending = false;
      this.playlistEventMapLoading = false; this.playlistEventMapStatusLoading = false; this.playlistEventMapEntitiesLoading = false; this.playlistEventMapSearchLoading = false; this.playlistEventMapTopicDetailLoading = false;
    },

    playlistEventMapDestroy() {
      this.playlistEventMapStopPlayback();
      this.playlistEventMapReleaseTimelineWindowDrag();
      this.playlistEventMapStopTimelineTrackObserver();
      this.playlistEventMapController()?.destroy(); this._playlistEventMapController = null;
      this._playlistEventMapMount?.remove(); this._playlistEventMapMount = null;
    },
    playlistEventMapStopPlayback({ refresh = true, loadEntities = true } = {}) {
      const wasPlaying = Boolean(this.playlistEventMapPlaying);
      clearInterval(this.playlistEventMapPlayTimer); this.playlistEventMapPlayTimer = null; this.playlistEventMapPlaying = false;
      if (refresh && wasPlaying) this.playlistEventMapUpdateLayers();
      if (loadEntities && wasPlaying) this.playlistEventMapScheduleEntities({ delay: 0 });
    },
    playlistEventMapPausePolling() {
      this._playlistEventMapPollGeneration = Number(this._playlistEventMapPollGeneration || 0) + 1;
      clearTimeout(this.playlistEventMapPollTimer); this.playlistEventMapPollTimer = null;
      this._abortCtrl("_playlistEventMapPollAbortCtrl");
    },
    playlistEventMapStopPolling() {
      this.playlistEventMapPausePolling();
      if (typeof document !== "undefined" && this._playlistEventMapVisibilityHandler) {
        document.removeEventListener("visibilitychange", this._playlistEventMapVisibilityHandler);
      }
      this._playlistEventMapVisibilityHandler = null;
    },
    playlistEventMapObserveTimelineTrack() {
      this.playlistEventMapStopTimelineTrackObserver();
      const track = this.$refs?.playlistEventMapTimelineTrack;
      if (!track) return;
      const update = () => { this.playlistEventMapTimelineTrackWidth = Math.max(0, Number(track.clientWidth || track.getBoundingClientRect?.().width || 0)); };
      update();
      if (typeof ResizeObserver !== "undefined") { this._playlistEventMapTimelineResizeObserver = new ResizeObserver(update); this._playlistEventMapTimelineResizeObserver.observe(track); }
    },
    playlistEventMapStopTimelineTrackObserver() { this._playlistEventMapTimelineResizeObserver?.disconnect(); this._playlistEventMapTimelineResizeObserver = null; },

    playlistEventMapReset() {
      this.playlistEventMapStopPolling(); this.playlistEventMapStopLoad(); this.playlistEventMapStopPlayback({ refresh: false, loadEntities: false }); this.playlistEventMapDestroy();
      Object.assign(this, {
        playlistEventMapManifest: null, playlistEventMapScene: null, playlistEventMapStatus: null, playlistEventMapStatusLoading: false, playlistEventMapStatusError: "", playlistEventMapLoading: false, playlistEventMapError: "", playlistEventMapSnapshotId: "",
        playlistEventMapEntities: [], playlistEventMapEntitiesLoading: false, playlistEventMapEntityQuery: "", playlistEventMapEntityFilter: null, playlistEventMapEntityIndices: new Set(), playlistEventMapSelectedIndex: null, playlistEventMapSelectedKind: "", playlistEventMapSelectedId: "", playlistEventMapSelectedDetail: null, playlistEventMapDetailLoading: false, playlistEventMapDetailTab: "overview", playlistEventMapTopicFocus: null, playlistEventMapTopicDetail: null, playlistEventMapTopicDetailLoading: false, playlistEventMapVisibleCount: 0,
        playlistEventMapViewportSummary: { sceneLevel: "overview", viewportCanonicalCount: 0, renderedTopicLabelCount: 0, ready: false }, playlistEventMapObjectGuideOpen: false, playlistEventMapSearchOpen: false, playlistEventMapFiltersOpen: false, playlistEventMapWindowStart: "", playlistEventMapWindowEnd: "", playlistEventMapTimelineScope: "normal", playlistEventMapTimelineMonths: [], playlistEventMapWindowMonths: 12, playlistEventMapWindowPreviewStartIndex: null, playlistEventMapWindowPreviewEndIndex: null, playlistEventMapTimelineWindowDragging: false, playlistEventMapTimelineWindowPointerId: null, playlistEventMapTimelineWindowResizeEdge: "", playlistEventMapTimelineTrackWidth: 0, playlistEventMapTimeFocusScope: "today", playlistEventMapTodayHighlights: null, playlistEventMapWeekHighlights: null, playlistEventMapHighlightsLoading: false, playlistEventMapHighlightsError: "", playlistEventMapTypeFilter: "", playlistEventMapSearchQuery: "", playlistEventMapSearchResults: [], playlistEventMapSearchLoading: false, playlistEventMapSearchActiveIndex: -1,
      });
      clearTimeout(this._playlistEventMapHighlightsTimer); this._playlistEventMapHighlightsTimer = null;
      this._playlistEventMapTimelineItemsCache = null; this._playlistEventMapTimelineMaxCache = null;
      this._playlistEventMapSceneComplete = false; this._playlistEventMapSceneLoadingSnapshotId = "";
      this._playlistEventMapFullScenePromise = null; this._playlistEventMapFullSceneSnapshotId = "";
      this._playlistEventMapMetadataPromise = null; this._playlistEventMapMetadataSnapshotId = ""; this._playlistEventMapMetadataComplete = false;
      this._playlistEventMapLoadKey = "";
      this._playlistEventMapPlaylistId = "";
      this._playlistEventMapHighlightsKey = "";
      this._playlistEventMapHighlightsPendingKey = "";
      this._playlistEventMapHighlightsForceRefresh = false;
      this._playlistEventMapHighlightsLastRequestAt = 0;
      this._playlistEventMapHighlightsToken = Number(this._playlistEventMapHighlightsToken || 0) + 1;
    },
    playlistEventMapStableEntityFilter() {
      const filter = this.playlistEventMapEntityFilter;
      if (!filter?.normalized_key) return null;
      return {
        normalized_key: String(filter.normalized_key),
        entity_type: String(filter.entity_type || ""),
        name: String(filter.name || filter.normalized_key),
      };
    },
    playlistEventMapResetSnapshotPinnedState(stableEntityFilter = null, { selectionToken = null } = {}) {
      const suppliedSelectionToken = Number(selectionToken);
      const preservesSelection = Number.isInteger(suppliedSelectionToken) && suppliedSelectionToken > 0;
      if (preservesSelection && Number(this._playlistEventMapSelectionToken || 0) !== suppliedSelectionToken) return false;
      this._playlistEventMapEntityIndexToken = Number(this._playlistEventMapEntityIndexToken || 0) + 1;
      this._abortCtrl("_playlistEventMapEntityAbortCtrl"); this._abortCtrl("_playlistEventMapEntityIndexAbortCtrl");
      this._abortCtrl("_playlistEventMapHighlightsAbortCtrl");
      clearTimeout(this._playlistEventMapHighlightsTimer); this._playlistEventMapHighlightsTimer = null;
      this._playlistEventMapHighlightsForceRefresh = false;
      this._playlistEventMapHighlightsToken = Number(this._playlistEventMapHighlightsToken || 0) + 1;
      this._playlistEventMapHighlightsKey = "";
      this._playlistEventMapHighlightsPendingKey = "";
      this._playlistEventMapHighlightsLastRequestAt = 0;
      this.playlistEventMapTodayHighlights = null; this.playlistEventMapWeekHighlights = null;
      this.playlistEventMapHighlightsLoading = false; this.playlistEventMapHighlightsError = "";
      this.playlistEventMapController()?.setTimeHighlights?.({ point_indices: [], items: [], scope: this.playlistEventMapTimeFocusScope });
      this._playlistEventMapEntityInFlight = false; this._playlistEventMapEntityPending = false;
      this.playlistEventMapEntities = []; this.playlistEventMapEntityIndices = new Set(); this.playlistEventMapEntityFilter = stableEntityFilter;
      this.playlistEventMapClearSearch(); this.playlistEventMapClearSelection({ update: false, preserveRequest: true, selectionToken: preservesSelection ? suppliedSelectionToken : null });
      this.playlistEventMapTopicFocus = null; this.playlistEventMapTopicDetail = null; this.playlistEventMapTopicDetailLoading = false;
      return true;
    },

    async playlistEventMapFetchBinary(path, options = {}) {
      const response = await this.fetchWithApiAuth(`/api${path}`, options);
      if (response.status === 401 && typeof this.handleApiUnauthorized === "function") this.handleApiUnauthorized({});
      if (!response.ok) {
        const contentType = response.headers.get("content-type") || "";
        const detail = contentType.includes("application/json") ? (await response.json()).detail : await response.text();
        throw new Error(`${response.status}: ${detail || "请求失败"}`);
      }
      return response.arrayBuffer();
    },
    async playlistEventMapLoadManifest({ silent = false, compact = false, includeCoverage = null, snapshotId = "", signal = null, commitGuard = null, commitStatus = true } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); if (!pid) return null;
      const lifecycleGeneration = Number(this._playlistEventMapLifecycleGeneration || 0);
      if (!silent) this.playlistEventMapStatusLoading = true;
      try {
        const params = new URLSearchParams();
        if (compact) params.set("compact", "true");
        if (includeCoverage !== null) params.set("include_coverage", includeCoverage ? "true" : "false");
        const pinnedSnapshotId = String(snapshotId || (this.activeView === "field" && this.fieldRequestedSnapshotId) || "");
        if (pinnedSnapshotId) params.set("snapshot_id", pinnedSnapshotId);
        const manifest = await this.api(
          `/playlists/${encodeURIComponent(pid)}/events/map/manifest${params.size ? `?${params}` : ""}`,
          { signal, cache: "no-store" }
        );
        const currentPid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
        if (signal?.aborted || currentPid !== pid || Number(this._playlistEventMapLifecycleGeneration || 0) !== lifecycleGeneration || (commitGuard && !commitGuard())) return null;
        if (commitStatus) {
          const currentStatus = this.playlistEventMapStatus || {};
          const sameSnapshot = String(currentStatus.snapshot_id || "") === String(manifest?.snapshot_id || "");
          this.playlistEventMapStatus = (compact || includeCoverage === false) && sameSnapshot
            ? { ...currentStatus, ...(manifest || {}) }
            : manifest || null;
          this.playlistEventMapStatusError = "";
          if (manifest && Object.prototype.hasOwnProperty.call(manifest, "backfill_job")) this.playlistEventMapBackfillJob = statusJob(manifest);
        }
        return manifest || null;
      } catch (error) {
        if (!abortError(error) && Number(this._playlistEventMapLifecycleGeneration || 0) === lifecycleGeneration && (!commitGuard || commitGuard())) {
          this.playlistEventMapStatusError = error?.message || String(error);
        }
        if (!silent && !abortError(error)) throw error;
        return null;
      }
      finally {
        if (!silent && Number(this._playlistEventMapLifecycleGeneration || 0) === lifecycleGeneration) this.playlistEventMapStatusLoading = false;
      }
    },

    playlistEventMapLoadDeferredCoverage({ pid, snapshotId, requestToken, lifecycleGeneration } = {}) {
      this._abortCtrl("_playlistEventMapCoverageAbortCtrl");
      const coverageController = new AbortController();
      this._playlistEventMapCoverageAbortCtrl = coverageController;
      void this.playlistEventMapLoadManifest({
        silent: true,
        compact: true,
        includeCoverage: true,
        snapshotId,
        signal: coverageController.signal,
      }).then((coverage) => {
        if (
          !coverage
          || coverageController.signal.aborted
          || Number(this._playlistEventMapRequestToken || 0) !== requestToken
          || Number(this._playlistEventMapLifecycleGeneration || 0) !== lifecycleGeneration
          || String(this.playlistPageId || this.selectedPlaylistId || "") !== pid
          || String(this.playlistEventMapSnapshotId || "") !== snapshotId
        ) return;
        this.playlistEventMapManifest = { ...(this.playlistEventMapManifest || {}), ...coverage };
      }).finally(() => {
        if (this._playlistEventMapCoverageAbortCtrl === coverageController) this._playlistEventMapCoverageAbortCtrl = null;
      });
    },

    playlistEventMapInitializeWindow() {
      const bounds = this.playlistEventMapTimelineBounds(); const items = this.playlistEventMapTimelineItems();
      if (!bounds.start || !bounds.end || !items.length) return;
      const currentStart = String(this.playlistEventMapWindowStart || "").slice(0, 10); const currentEnd = String(this.playlistEventMapWindowEnd || "").slice(0, 10);
      if (currentStart && currentEnd && currentStart <= currentEnd && currentStart >= bounds.start && currentEnd <= bounds.end) return;
      this.playlistEventMapSetWindowEndIndex(items.length - 1, { update: false, pause: false });
    },

    playlistEventMapPreloadRenderer() {
      return preloadEventMapRenderer();
    },

    async playlistEventMapWaitForCompleteScene({ includeMetadata = false } = {}) {
      const snapshotId = String(this.playlistEventMapSnapshotId || "");
      if (!this._playlistEventMapSceneComplete) {
        const request = this._playlistEventMapFullScenePromise;
        if (!request || String(this._playlistEventMapFullSceneSnapshotId || "") !== snapshotId) {
          throw new Error("完整事件星域尚未就绪");
        }
        await request;
      }
      if (!this._playlistEventMapSceneComplete || String(this.playlistEventMapSnapshotId || "") !== snapshotId) {
        throw new Error("完整事件星域加载已中断");
      }
      if (includeMetadata && !this._playlistEventMapMetadataComplete) {
        const metadataRequest = this._playlistEventMapMetadataPromise;
        if (!metadataRequest || String(this._playlistEventMapMetadataSnapshotId || "") !== snapshotId) {
          throw new Error("事件星域标签元数据尚未就绪");
        }
        await metadataRequest;
      }
      if (
        String(this.playlistEventMapSnapshotId || "") !== snapshotId
        || !this._playlistEventMapSceneComplete
        || (includeMetadata && !this._playlistEventMapMetadataComplete)
      ) {
        throw new Error("完整事件星域加载已中断");
      }
      return this.playlistEventMapScene;
    },

    async playlistEventMapLoadView(options = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      if (this._playlistEventMapPlaylistId && String(this._playlistEventMapPlaylistId) !== pid) {
        this.playlistEventMapReset();
      }
      const requestedSnapshotId = String(
        (this.activeView === "field" && this.fieldRequestedSnapshotId) || ""
      );
      const suppliedSelectionToken = Number(options.selectionToken);
      const selectionMode = Number.isInteger(suppliedSelectionToken) && suppliedSelectionToken > 0
        ? `selection-${suppliedSelectionToken}`
        : "reset-selection";
      const snapshotMode = options.forceLatest === true ? "latest" : "pinned";
      const requestKey = `${pid}|${requestedSnapshotId}|${snapshotMode}|${selectionMode}|${Number(this._playlistEventMapLifecycleGeneration || 0)}`;
      if (
        this._playlistEventMapLoadPromise
        && String(this._playlistEventMapLoadKey || "") === requestKey
      ) {
        return await this._playlistEventMapLoadPromise;
      }
      const request = this._playlistEventMapLoadViewRequest(options);
      this._playlistEventMapLoadPromise = request;
      this._playlistEventMapLoadKey = requestKey;
      try {
        return await request;
      } finally {
        if (this._playlistEventMapLoadPromise === request) {
          this._playlistEventMapLoadPromise = null;
          this._playlistEventMapLoadKey = "";
        }
      }
    },

    async _playlistEventMapLoadViewRequest({ silent = false, schedulePolling = true, selectionToken = null, forceLatest = false, initialCursor = null } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); if (!pid) return;
      const suppliedSelectionToken = Number(selectionToken);
      const selectionIntentToken = Number.isInteger(suppliedSelectionToken) && suppliedSelectionToken > 0
        ? suppliedSelectionToken
        : null;
      const selectionIntentStillCurrent = () => (
        selectionIntentToken === null
        || Number(this._playlistEventMapSelectionToken || 0) === selectionIntentToken
      );
      if (!selectionIntentStillCurrent()) return;
      this._playlistEventMapPlaylistId = pid;
      this.playlistEventMapObserveVisibility();
      if (documentIsHidden()) { this.playlistEventMapStopLoad(); return; }
      const requestedSnapshotId = String(
        (this.activeView === "field" && this.fieldRequestedSnapshotId) || ""
      );
      const requestedSnapshotIdAtStart = requestedSnapshotId;
      const currentSnapshotId = String(this.playlistEventMapSnapshotId || "");
      const deferManifestStatus = forceLatest || (
        selectionIntentToken !== null
        && Boolean(requestedSnapshotId)
        && requestedSnapshotId !== currentSnapshotId
      );
      const preserveCurrentSceneBackground = (deferManifestStatus || forceLatest)
        && Boolean(this.playlistEventMapController())
        && this._playlistEventMapSceneComplete === false;
      const preserveCurrentMetadataBackground = (deferManifestStatus || forceLatest)
        && Boolean(this.playlistEventMapController())
        && this._playlistEventMapMetadataComplete === false;
      const sceneLoadInFlight = (
        this.playlistEventMapController()
        && !this._playlistEventMapSceneComplete
        && String(this._playlistEventMapSceneLoadingSnapshotId || "") === currentSnapshotId
      );
      const metadataLoadInFlight = (
        this._playlistEventMapMetadataPromise
        && String(this._playlistEventMapMetadataSnapshotId || "") === String(this.playlistEventMapSnapshotId || "")
      );
      if (
        !forceLatest
        && sceneLoadInFlight
        && (!requestedSnapshotId || requestedSnapshotId === currentSnapshotId)
        && (this._playlistEventMapMetadataComplete || metadataLoadInFlight)
      ) {
        if (schedulePolling && this.playlistSubview === "analysis") this.playlistEventMapSchedulePoll();
        return;
      }
      this.playlistEventMapObserveTimelineTrack();
      const token = Number(this._playlistEventMapRequestToken || 0) + 1;
      this._playlistEventMapRequestToken = token;
      this._abortCtrl("_playlistEventMapStagedAbortCtrl");
      this._abortCtrl("_playlistEventMapAbortCtrl");
      if (!preserveCurrentSceneBackground) {
        this._abortCtrl("_playlistEventMapSceneAbortCtrl");
      }
      if (!preserveCurrentMetadataBackground) {
        this._abortCtrl("_playlistEventMapMetadataAbortCtrl");
        this._playlistEventMapMetadataPromise = null;
        this._playlistEventMapMetadataSnapshotId = "";
      }
      this._abortCtrl("_playlistEventMapCoverageAbortCtrl");
      const requestController = new AbortController();
      const sceneController = new AbortController();
      this._playlistEventMapAbortCtrl = requestController;
      if (!preserveCurrentSceneBackground) this._playlistEventMapSceneAbortCtrl = sceneController;
      if (!silent) this.playlistEventMapLoading = true; this.playlistEventMapError = "";
      const lifecycleGeneration = Number(this._playlistEventMapLifecycleGeneration || 0);
      const snapshotCommitStillCurrent = () => (
        Number(this._playlistEventMapRequestToken || 0) === token
        && Number(this._playlistEventMapLifecycleGeneration || 0) === lifecycleGeneration
        && String(this.playlistPageId || this.selectedPlaylistId || "").trim() === pid
        && (
          this.activeView !== "field"
          || String(this.fieldRequestedSnapshotId || "") === requestedSnapshotIdAtStart
        )
        && selectionIntentStillCurrent()
      );
      let pendingMount = null; let pendingController = null; let metadataController = null; let backgroundLoadDetached = false;
      const stagedController = deferManifestStatus ? new AbortController() : null;
      if (stagedController) {
        this._playlistEventMapStagedAbortCtrl = stagedController;
        stagedController.signal.addEventListener("abort", () => {
          requestController.abort();
          sceneController.abort();
          metadataController?.abort();
        }, { once: true });
      }
      try {
        const rendererPromise = this.playlistEventMapPreloadRenderer();
        // compact manifest 未就绪时也先下载 Three.js；真正创建网格时仍会感知导入失败。
        rendererPromise.catch(() => {});
        const anticipatedSnapshotId = String(
          (forceLatest ? "" : requestedSnapshotId)
          || this.playlistEventMapStatus?.snapshot_id
          || this.currentDomain?.()?.snapshot?.id
          || ""
        );
        const existingController = this.playlistEventMapController();
        const bootManifest = await this.playlistEventMapLoadManifest({ silent: true, compact: true, snapshotId: forceLatest ? "" : anticipatedSnapshotId, signal: requestController.signal, commitGuard: snapshotCommitStillCurrent, commitStatus: !deferManifestStatus }); if (!snapshotCommitStillCurrent()) return;
        if (!bootManifest) return;
        const snapshotId = String(bootManifest?.snapshot_id || "");
        if (!snapshotId || bootManifest?.status !== "ready") {
          if (!snapshotCommitStillCurrent()) return;
          // 历史星域仍可浏览时，最新快照暂缺或仍在构建不能销毁当前画布。
          if (existingController && deferManifestStatus) return null;
          this.playlistEventMapDestroy();
          this.playlistEventMapStatus = bootManifest || null;
          this.playlistEventMapStatusError = "";
          if (bootManifest && Object.prototype.hasOwnProperty.call(bootManifest, "backfill_job")) {
            this.playlistEventMapBackfillJob = statusJob(bootManifest);
          }
          this.playlistEventMapManifest = bootManifest || null;
          this.playlistEventMapTimelineMonths = normalizeTimeline(bootManifest);
          this.playlistEventMapScene = null;
          this.playlistEventMapSnapshotId = "";
          return { confirmed: true, ready: false, snapshotId };
        }
        if (
          forceLatest
          && snapshotId === currentSnapshotId
          && this.playlistEventMapScene
          && this.playlistEventMapController() === existingController
          && !this._playlistEventMapSceneComplete
          && sceneLoadInFlight
          && (this._playlistEventMapMetadataComplete || metadataLoadInFlight)
        ) return { confirmed: true, ready: true, snapshotId };
        if (snapshotId === this.playlistEventMapSnapshotId && this.playlistEventMapScene && this.playlistEventMapController() && this._playlistEventMapSceneComplete) {
          if (!snapshotCommitStillCurrent()) return;
          const mountedController = this.playlistEventMapController();
          this.playlistEventMapManifest = { ...(this.playlistEventMapManifest || {}), ...bootManifest };
          if (!this._playlistEventMapMetadataComplete) {
            let metadataLoad = (
              this._playlistEventMapMetadataPromise
              && String(this._playlistEventMapMetadataSnapshotId || "") === snapshotId
            ) ? this._playlistEventMapMetadataPromise : null;
            let ownsMetadataLoad = false;
            if (!metadataLoad) {
              metadataController = new AbortController();
              this._playlistEventMapMetadataAbortCtrl = metadataController;
              metadataLoad = (async () => {
                const fullManifest = await this.playlistEventMapLoadManifest({
                  silent: true,
                  includeCoverage: false,
                  snapshotId,
                  signal: metadataController.signal,
                });
                if (!fullManifest) throw new Error("事件语义星域主题元数据加载失败");
                if (
                  metadataController.signal.aborted
                  || Number(this._playlistEventMapRequestToken || 0) !== token
                  || Number(this._playlistEventMapLifecycleGeneration || 0) !== lifecycleGeneration
                  || String(this.playlistPageId || this.selectedPlaylistId || "") !== pid
                  || String(this.playlistEventMapSnapshotId || "") !== snapshotId
                  || this.playlistEventMapController() !== mountedController
                ) throw new DOMException("事件星域标签元数据加载已中断", "AbortError");
                this.playlistEventMapManifest = fullManifest;
                this.playlistEventMapTimelineMonths = normalizeTimeline(fullManifest);
                this.playlistEventMapInitializeWindow();
                mountedController.updateManifest(fullManifest);
                this.playlistEventMapUpdateLayers();
                this._playlistEventMapMetadataComplete = true;
                mountedController.finishInitialHydration?.();
                return fullManifest;
              })();
              ownsMetadataLoad = true;
              this._playlistEventMapMetadataPromise = metadataLoad;
              this._playlistEventMapMetadataSnapshotId = snapshotId;
            }
            try {
              await metadataLoad;
            } finally {
              if (ownsMetadataLoad && this._playlistEventMapMetadataPromise === metadataLoad) {
                this._playlistEventMapMetadataPromise = null;
                this._playlistEventMapMetadataSnapshotId = "";
              }
            }
          }
          this.playlistEventMapUpdateLayers();
          return { confirmed: true, ready: true, snapshotId };
        }
        if (Number(bootManifest.dimension || 0) !== 3 || Number(bootManifest.scene_record_size || 0) !== 56) throw new Error("事件语义星域快照不是三维 v2 协议，请先完成星域重建");
        const previousController = existingController; const previousMount = this._playlistEventMapMount;
        const stableEntityFilter = this.playlistEventMapStableEntityFilter();

        if (previousController) {
          metadataController = new AbortController();
          if (stagedController?.signal.aborted) metadataController.abort();
          if (!preserveCurrentMetadataBackground) this._playlistEventMapMetadataAbortCtrl = metadataController;
          const metadataPromise = this.playlistEventMapLoadManifest({ silent: true, includeCoverage: false, snapshotId, signal: metadataController.signal, commitGuard: snapshotCommitStillCurrent, commitStatus: !deferManifestStatus });
          const fullScenePromise = this.playlistEventMapFetchBinary(
            `/playlists/${encodeURIComponent(pid)}/events/map/scene?${new URLSearchParams({ snapshot_id: snapshotId })}`,
            { signal: sceneController.signal, cache: "force-cache" }
          );
          const [fullBuffer, fullManifest] = await Promise.all([fullScenePromise, metadataPromise, rendererPromise]);
          if (!fullManifest) throw new Error("事件语义星域主题元数据加载失败");
          if (!snapshotCommitStillCurrent()) return;
          const fullScene = parseEventMapScene(fullBuffer, Number(bootManifest.canonical_count || 0));
          const target = this.$refs?.playlistEventMap; if (!target) throw new Error("事件语义星域容器尚未就绪");
          pendingMount = document.createElement("div"); pendingMount.style.cssText = "position:absolute;inset:0;width:100%;height:100%;visibility:hidden"; target.appendChild(pendingMount);
          pendingController = await EventMapController.create(this.playlistEventMapControllerOptions(pendingMount, fullScene, fullManifest));
          if (!snapshotCommitStillCurrent()) { pendingController.destroy(); pendingMount.remove(); return; }
          await pendingController.whenFirstFrame();
          if (!snapshotCommitStillCurrent()) { pendingController.destroy(); pendingMount.remove(); return; }
          if (preserveCurrentSceneBackground) {
            this._abortCtrl("_playlistEventMapSceneAbortCtrl");
          }
          if (preserveCurrentMetadataBackground) {
            this._abortCtrl("_playlistEventMapMetadataAbortCtrl");
            this._playlistEventMapMetadataPromise = null;
            this._playlistEventMapMetadataSnapshotId = "";
          }
          if (!this.playlistEventMapResetSnapshotPinnedState(stableEntityFilter, { selectionToken: selectionIntentToken })) { pendingController.destroy(); pendingMount.remove(); return; }
          this.playlistEventMapStatus = fullManifest;
          this.playlistEventMapStatusError = "";
          if (Object.prototype.hasOwnProperty.call(fullManifest, "backfill_job")) this.playlistEventMapBackfillJob = statusJob(fullManifest);
          this.playlistEventMapManifest = fullManifest; this.playlistEventMapTimelineMonths = normalizeTimeline(fullManifest); this.playlistEventMapScene = fullScene; this.playlistEventMapSnapshotId = snapshotId;
          this._playlistEventMapSceneComplete = true;
          this._playlistEventMapMetadataComplete = true;
          this.playlistEventMapInitializeWindow();
          pendingController.updateLayers({ windowStart: this.playlistEventMapWindowStart, windowEnd: this.playlistEventMapWindowEnd, typeFilter: this.playlistEventMapTypeFilter, entityIndices: this.playlistEventMapEntityIndices, playing: this.playlistEventMapPlaying, topicIndex: null });
          this.playlistEventMapVisibleCount = countEventMapVisiblePoints(fullScene, this.playlistEventMapWindowStart, this.playlistEventMapWindowEnd, this.playlistEventMapTypeFilter);
          pendingController.fitActiveWindow();
          previousController.destroy(); previousMount?.remove();
          this._playlistEventMapController = pendingController; this._playlistEventMapMount = pendingMount; pendingController = null; pendingMount = null;
          this._playlistEventMapMount.style.visibility = "visible"; this.playlistEventMapController().updateLabels();
          this.playlistEventMapScheduleHighlights({ delay: 0 });
          this.playlistEventMapLoadDeferredCoverage({ pid, snapshotId, requestToken: token, lifecycleGeneration });
          this.playlistEventMapLoadEntities({ silent: true });
          if (stableEntityFilter && Number(this._playlistEventMapRequestToken || 0) === token) await this.playlistEventMapApplyEntityFilter(stableEntityFilter, { resume: true });
          return { confirmed: true, ready: true, snapshotId };
        }

        await rendererPromise;
        if (!snapshotCommitStillCurrent()) return;
        const target = this.$refs?.playlistEventMap; if (!target) throw new Error("事件语义星域容器尚未就绪");
        pendingMount = document.createElement("div"); pendingMount.style.cssText = "position:absolute;inset:0;width:100%;height:100%;visibility:hidden"; target.appendChild(pendingMount);
        const emptyScene = emptyEventMapScene();
        pendingController = await EventMapController.create(this.playlistEventMapControllerOptions(pendingMount, emptyScene, bootManifest));
        if (!snapshotCommitStillCurrent()) { pendingController.destroy(); pendingMount.remove(); return; }
        pendingController.setSceneInteractive(false);
        const cursor = typeof initialCursor === "function" ? initialCursor() : null;
        const cursorCompatible = this.fieldCameraStateCompatible?.(cursor?.camera_state, {
          controller: pendingController,
          snapshotId,
          windowStart: this.playlistEventMapWindowStart,
          windowEnd: this.playlistEventMapWindowEnd,
        });
        if (cursorCompatible) pendingController.restoreCameraState(cursor.camera_state);
        await pendingController.whenFirstFrame();
        if (!snapshotCommitStillCurrent()) { pendingController.destroy(); pendingMount.remove(); return; }
        if (!this.playlistEventMapResetSnapshotPinnedState(stableEntityFilter, { selectionToken: selectionIntentToken })) { pendingController.destroy(); pendingMount.remove(); return; }
        this.playlistEventMapStatus = bootManifest;
        this.playlistEventMapStatusError = "";
        if (Object.prototype.hasOwnProperty.call(bootManifest, "backfill_job")) this.playlistEventMapBackfillJob = statusJob(bootManifest);
        this.playlistEventMapManifest = bootManifest; this.playlistEventMapTimelineMonths = normalizeTimeline(bootManifest); this.playlistEventMapScene = emptyScene; this.playlistEventMapSnapshotId = snapshotId;
        this._playlistEventMapSceneComplete = false;
        this._playlistEventMapMetadataComplete = false;
        this.playlistEventMapInitializeWindow();
        pendingController.updateLayers({ windowStart: this.playlistEventMapWindowStart, windowEnd: this.playlistEventMapWindowEnd, typeFilter: this.playlistEventMapTypeFilter, entityIndices: this.playlistEventMapEntityIndices, playing: this.playlistEventMapPlaying, topicIndex: null });
        this.playlistEventMapVisibleCount = 0;
        this._playlistEventMapController = pendingController; this._playlistEventMapMount = pendingMount; pendingController = null; pendingMount = null;
        this._playlistEventMapMount.style.visibility = "visible";
        const mountedController = this.playlistEventMapController();
        this._playlistEventMapSceneLoadingSnapshotId = snapshotId;
        const previewBuffer = await this.playlistEventMapFetchBinary(
          `/playlists/${encodeURIComponent(pid)}/events/map/scene?${new URLSearchParams({ snapshot_id: snapshotId, preview_limit: String(EVENT_MAP_PREVIEW_LIMIT) })}`,
          { signal: sceneController.signal, cache: "force-cache" }
        );
        if (
          sceneController.signal.aborted
          || Number(this._playlistEventMapRequestToken || 0) !== token
          || this.playlistEventMapController() !== mountedController
        ) return;
        const previewScene = parseEventMapScene(previewBuffer);
        this.playlistEventMapScene = previewScene;
        mountedController.replaceScene(previewScene);
        const revealPrepared = mountedController.prepareProgressiveReveal();
        this.playlistEventMapUpdateLayers();
        if (revealPrepared) mountedController.startProgressiveReveal({
          target: EVENT_MAP_PREVIEW_REVEAL_TARGET,
          duration: EVENT_MAP_PREVIEW_REVEAL_MS,
          complete: false,
        });

        // 首帧网格和预览点都已落下后，才让完整点集与标签元数据占用数据库和网络。
        metadataController = new AbortController(); this._playlistEventMapMetadataAbortCtrl = metadataController;
        const metadataPromise = this.playlistEventMapLoadManifest({ silent: true, includeCoverage: false, snapshotId, signal: metadataController.signal });
        const fullScenePromise = this.playlistEventMapFetchBinary(
          `/playlists/${encodeURIComponent(pid)}/events/map/scene?${new URLSearchParams({ snapshot_id: snapshotId })}`,
          { signal: sceneController.signal, cache: "force-cache" }
        );
        backgroundLoadDetached = true;
        const assertCurrentBackgroundLoad = (controller) => {
          if (
            controller.signal.aborted
            || Number(this._playlistEventMapLifecycleGeneration || 0) !== lifecycleGeneration
            || String(this.playlistPageId || this.selectedPlaylistId || "") !== pid
            || String(this.playlistEventMapSnapshotId || "") !== snapshotId
            || this.playlistEventMapController() !== mountedController
          ) throw new DOMException("事件星域加载已中断", "AbortError");
        };
        const fullSceneLoad = fullScenePromise.then((fullBuffer) => {
          assertCurrentBackgroundLoad(sceneController);
          const fullScene = parseEventMapScene(fullBuffer, Number(bootManifest.canonical_count || 0));
          this.playlistEventMapScene = fullScene;
          this.playlistEventMapInitializeWindow();
          mountedController.replaceScene(fullScene, { preserveRevealCount: revealPrepared });
          mountedController.setSceneInteractive(true);
          this._playlistEventMapSceneComplete = true;
          this.playlistEventMapUpdateLayers();
          if (this._playlistEventMapMetadataComplete) mountedController.finishInitialHydration();
          mountedController.startProgressiveReveal({ target: 1, duration: 1250, complete: true });
          this.playlistEventMapLoadDeferredCoverage({ pid, snapshotId, requestToken: token, lifecycleGeneration });
          this.playlistEventMapLoadEntities({ silent: true });
          if (stableEntityFilter && Number(this._playlistEventMapRequestToken || 0) === token) void this.playlistEventMapApplyEntityFilter(stableEntityFilter, { resume: true });
          return fullScene;
        });
        const metadataLoad = metadataPromise.then((fullManifest) => {
          assertCurrentBackgroundLoad(metadataController);
          if (!fullManifest) throw new Error("事件语义星域主题元数据加载失败");
          this.playlistEventMapManifest = fullManifest;
          this.playlistEventMapTimelineMonths = normalizeTimeline(fullManifest);
          this.playlistEventMapInitializeWindow();
          mountedController.updateManifest(fullManifest);
          this.playlistEventMapUpdateLayers();
          this._playlistEventMapMetadataComplete = true;
          if (this._playlistEventMapSceneComplete) mountedController.finishInitialHydration();
          return fullManifest;
        });
        this._playlistEventMapFullScenePromise = fullSceneLoad;
        this._playlistEventMapFullSceneSnapshotId = snapshotId;
        this._playlistEventMapMetadataPromise = metadataLoad;
        this._playlistEventMapMetadataSnapshotId = snapshotId;
        void fullSceneLoad.catch((error) => {
          if (!abortError(error) && Number(this._playlistEventMapRequestToken || 0) === token) this.playlistEventMapError = error?.message || String(error);
        });
        void metadataLoad.catch((error) => {
          if (!abortError(error) && Number(this._playlistEventMapRequestToken || 0) === token) this.playlistEventMapError = error?.message || String(error);
        });
        const clearFullSceneLoad = () => {
          const ownsFullSceneLoad = this._playlistEventMapFullScenePromise === fullSceneLoad;
          if (ownsFullSceneLoad) {
            this._playlistEventMapFullScenePromise = null;
            this._playlistEventMapFullSceneSnapshotId = "";
            if (String(this._playlistEventMapSceneLoadingSnapshotId || "") === snapshotId) {
              this._playlistEventMapSceneLoadingSnapshotId = "";
            }
          }
          if (this._playlistEventMapSceneAbortCtrl === sceneController) {
            this._playlistEventMapSceneAbortCtrl = null;
          }
        };
        const clearMetadataLoad = () => {
          if (this._playlistEventMapMetadataPromise === metadataLoad) {
            this._playlistEventMapMetadataPromise = null;
            this._playlistEventMapMetadataSnapshotId = "";
          }
          if (this._playlistEventMapMetadataAbortCtrl === metadataController) {
            this._playlistEventMapMetadataAbortCtrl = null;
          }
        };
        void fullSceneLoad.then(clearFullSceneLoad, clearFullSceneLoad);
        void metadataLoad.then(clearMetadataLoad, clearMetadataLoad);
        return { confirmed: true, ready: true, snapshotId, initialCameraApplied: true };
      } catch (error) { pendingController?.destroy(); pendingMount?.remove(); if (!abortError(error) && snapshotCommitStillCurrent()) this.playlistEventMapError = error?.message || String(error); return null; }
      finally {
        if (this._playlistEventMapStagedAbortCtrl === stagedController) {
          this._playlistEventMapStagedAbortCtrl = null;
        }
        if (!backgroundLoadDetached && metadataController) {
          metadataController.abort();
          if (this._playlistEventMapMetadataAbortCtrl === metadataController) this._playlistEventMapMetadataAbortCtrl = null;
        }
        if (!backgroundLoadDetached) {
          sceneController.abort();
          if (this._playlistEventMapSceneAbortCtrl === sceneController) this._playlistEventMapSceneAbortCtrl = null;
        }
        if (Number(this._playlistEventMapRequestToken || 0) === token) {
          if (!backgroundLoadDetached && !preserveCurrentSceneBackground) this._playlistEventMapSceneLoadingSnapshotId = "";
          this._playlistEventMapAbortCtrl = null; this.playlistEventMapLoading = false;
          if (schedulePolling && this.playlistSubview === "analysis") this.playlistEventMapSchedulePoll();
        }
      }
    },

    playlistEventMapUpdateLayers() {
      const controller = this.playlistEventMapController(); const scene = this.playlistEventMapScene; if (!controller || !scene) return;
      controller.updateLayers({ windowStart: this.playlistEventMapWindowStart, windowEnd: this.playlistEventMapWindowEnd, typeFilter: this.playlistEventMapTypeFilter, entityIndices: this.playlistEventMapEntityIndices, playing: this.playlistEventMapPlaying, topicIndex: Number.isInteger(Number(this.playlistEventMapTopicFocus?.topic_index)) ? Number(this.playlistEventMapTopicFocus.topic_index) : null });
      this.playlistEventMapVisibleCount = countEventMapVisiblePoints(scene, this.playlistEventMapWindowStart, this.playlistEventMapWindowEnd, this.playlistEventMapTypeFilter);
      if (this.playlistEventMapSelectedKind === "canonical" && !controller.isActiveIndex(this.playlistEventMapSelectedIndex)) this.playlistEventMapClearSelection({ update: false });
      if (this.playlistEventMapSelectedKind === "topic" && !controller.hasActiveTopic(this.playlistEventMapTopicFocus?.topic_index)) this.playlistEventMapClearTopicFocus({ update: false });
      this.playlistEventMapScheduleHighlights();
    },
    playlistEventMapHighlightRange(scope) {
      const windowStart = String(this.playlistEventMapWindowStart || "").slice(0, 10);
      const windowEnd = String(this.playlistEventMapWindowEnd || "").slice(0, 10);
      if (!windowStart || !windowEnd) return null;
      if (scope !== "week") return { scope: "24h" };
      const today = localToday();
      const start = localWeekStart(today);
      if (windowStart > today || windowEnd < start) return null;
      return { start: windowStart > start ? windowStart : start, end: windowEnd < today ? windowEnd : today };
    },
    playlistEventMapHighlightAvailable(scope) { return Boolean(this.playlistEventMapHighlightRange(scope)); },
    playlistEventMapHighlightsRequestKey() {
      return JSON.stringify({
        domainId: String(this.playlistPageId || this.selectedPlaylistId || "").trim(),
        snapshotId: String(this.playlistEventMapSnapshotId || "").trim(),
        todayRange: this.playlistEventMapHighlightRange("today"),
        weekRange: this.playlistEventMapHighlightRange("week"),
        windowStart: this.playlistEventMapWindowStart,
        windowEnd: this.playlistEventMapWindowEnd,
        type: this.playlistEventMapTypeFilter || "",
        entity: this.playlistEventMapEntityFilter?.normalized_key || "",
        entityType: this.playlistEventMapEntityFilter?.entity_type || "",
        topic: this.playlistEventMapTopicFocus?.topic_id || "",
      });
    },
    playlistEventMapHighlightData(scope = this.playlistEventMapTimeFocusScope) {
      return scope === "week" ? this.playlistEventMapWeekHighlights : this.playlistEventMapTodayHighlights;
    },
    playlistEventMapHighlightCount(scope) {
      const data = this.playlistEventMapHighlightData(scope);
      if (!data) return 0;
      return Math.max(0, Number(data.shown_total ?? data.items?.length ?? data.point_indices?.length ?? 0));
    },
    playlistEventMapHighlightTotal(scope) {
      const data = this.playlistEventMapHighlightData(scope);
      if (!data) return 0;
      return Math.max(0, Number(data.total ?? data.playable_event_total ?? data.items?.length ?? 0));
    },
    playlistEventMapHighlightVideoCount(scope) {
      const data = this.playlistEventMapHighlightData(scope);
      if (!data) return 0;
      return Math.max(0, Number(data.video_total ?? data.items?.length ?? 0));
    },
    playlistEventMapHighlightProcessing(scope) {
      return Boolean(this.playlistEventMapHighlightData(scope)) && this.playlistEventMapPollIsActive();
    },
    playlistEventMapHighlightStatusLabel(scope) {
      const data = this.playlistEventMapHighlightData(scope);
      if (!data && this.playlistEventMapHighlightsLoading) return "…";
      const shown = this.playlistEventMapHighlightCount(scope);
      const total = this.playlistEventMapHighlightTotal(scope);
      return total > shown
        ? `${this.formatInteger(shown)}/${this.formatInteger(total)}`
        : this.formatInteger(shown);
    },
    playlistEventMapHighlightStatusHint(scope) {
      const basis = scope === "week" ? "本周按事件发生日期筛选" : "24H事件按视频平台发布时间筛选过去 24 小时的视频";
      const data = this.playlistEventMapHighlightData(scope);
      const shown = this.formatInteger(this.playlistEventMapHighlightCount(scope));
      const total = this.formatInteger(this.playlistEventMapHighlightTotal(scope));
      const videos = this.formatInteger(this.playlistEventMapHighlightVideoCount(scope));
      const matched = Math.max(0, Number(data?.matched_event_total ?? data?.total ?? 0));
      const unavailable = Math.max(0, matched - Number(data?.total ?? 0));
      const imprecise = Math.max(0, Number(data?.excluded_imprecise_total || 0));
      const missingVideo = unavailable ? `；${this.formatInteger(unavailable)} 个事件没有可播放关联视频` : "";
      const precision = scope === "week" && imprecise ? `；已排除 ${this.formatInteger(imprecise)} 个只有月/年级日期的模糊事件` : "";
      const active = this.playlistEventMapHighlightProcessing(scope) ? "；星域后台任务正在运行" : "";
      return `${basis}，按多信源佐证强度排序，每个媒体最多 2 个视频；同一视频只显示一张卡片并连接全部入选事件；显示 ${shown}/${total} 个事件，关联 ${videos} 个视频${missingVideo}${precision}${active}`;
    },
    playlistEventMapNormalizeHighlightData(payload) {
      const value = payload && typeof payload === "object" ? payload : {};
      const items = [];
      const cardsByVideo = new Map();
      const mediaCardCounts = new Map();
      const selectedEventKeys = new Set();
      let selectedEventCount = 0;
      for (const [index, item] of (Array.isArray(value.items) ? value.items : []).entries()) {
        if (selectedEventCount >= 10) break;
        const video = item?.primary_video || null;
        const videoKey = video?.video_id
          ? `id:${video.video_id}`
          : `event:${item?.canonical_id || index}`;
        let card = cardsByVideo.get(videoKey);
        const mediaKey = video?.media_id
          ? `id:${video.media_id}`
          : (video?.media_name
            ? `name:${video.media_name}`
            : videoKey);
        if (!card && Number(mediaCardCounts.get(mediaKey) || 0) >= 2) continue;
        const pointIndices = uniqueEventMapPointIndices([
          item?.point_index,
          ...(Array.isArray(item?.point_indices) ? item.point_indices : []),
        ]);
        const eventKey = String(item?.canonical_id || `point:${pointIndices.join(",") || index}`);
        if (selectedEventKeys.has(eventKey)) continue;
        const posterUrl = video?.thumbnail_asset
          ? this.assetContentUrl(video.thumbnail_asset)
          : String(video?.thumbnail_url || "");
        const mediaAvatarUrl = video?.media_avatar_asset
          ? this.assetContentUrl(video.media_avatar_asset)
          : String(video?.media_avatar_url || "");
        if (!card) {
          card = {
            ...item,
            point_index: pointIndices[0],
            point_indices: [],
            canonical_ids: [],
            linked_events: [],
            linked_event_count: 0,
            ...(video ? { primary_video: { ...video, poster_url: posterUrl, media_avatar_url: mediaAvatarUrl } } : {}),
          };
          cardsByVideo.set(videoKey, card);
          items.push(card);
          mediaCardCounts.set(mediaKey, Number(mediaCardCounts.get(mediaKey) || 0) + 1);
        }
        card.point_indices = uniqueEventMapPointIndices([...card.point_indices, ...pointIndices]);
        if (!Number.isInteger(eventMapPointIndex(card.point_index))) card.point_index = card.point_indices[0];
        if (item?.canonical_id) card.canonical_ids.push(String(item.canonical_id));
        card.linked_events.push({
          canonical_id: String(item?.canonical_id || ""),
          point_indices: pointIndices,
          title: String(item?.title || "未命名事件"),
          summary: String(item?.summary || ""),
          event_time_start: String(item?.event_time_start || ""),
          representative_rank: Number(item?.representative_rank || index + 1),
        });
        card.linked_event_count = card.linked_events.length;
        selectedEventKeys.add(eventKey);
        selectedEventCount += 1;
      }
      const pointIndices = uniqueEventMapPointIndices([
        ...(Array.isArray(value.point_indices) ? value.point_indices : []),
        ...items.flatMap((item) => item.point_indices),
      ]);
      return {
        ...value,
        point_indices: pointIndices,
        total: Math.max(0, Number(value.total ?? value.playable_event_total ?? items.length)),
        shown_total: selectedEventCount,
        video_total: items.length,
        max_cards_per_media: 2,
        items,
      };
    },
    playlistEventMapApplyTimeHighlights() {
      const available = this.playlistEventMapHighlightAvailable(this.playlistEventMapTimeFocusScope);
      const payload = available ? this.playlistEventMapHighlightData() : null;
      this.playlistEventMapController()?.setTimeHighlights?.({
        ...(payload || { point_indices: [], items: [] }),
        scope: this.playlistEventMapTimeFocusScope,
      });
    },
    playlistEventMapSetTimeFocusScope(scope) {
      const normalized = scope === "week" ? "week" : "today";
      if (!this.playlistEventMapHighlightAvailable(normalized)) return;
      this.playlistEventMapTimeFocusScope = normalized;
      this.playlistEventMapApplyTimeHighlights();
    },
    playlistEventMapScheduleHighlights({ delay = 140, force = false } = {}) {
      if (typeof this.playlistEventMapController()?.setTimeHighlights !== "function") return;
      if (!this._playlistEventMapSceneComplete) {
        this.playlistEventMapController()?.setTimeHighlights?.({ point_indices: [], items: [], scope: this.playlistEventMapTimeFocusScope });
        return;
      }
      if (!force) {
        const nextKey = this.playlistEventMapHighlightsRequestKey();
        const knownKeys = [this._playlistEventMapHighlightsKey, this._playlistEventMapHighlightsPendingKey].filter(Boolean);
        if (!knownKeys.includes(nextKey)) {
          this._abortCtrl("_playlistEventMapHighlightsAbortCtrl");
          this._playlistEventMapHighlightsToken = Number(this._playlistEventMapHighlightsToken || 0) + 1;
          this._playlistEventMapHighlightsPendingKey = "";
          this._playlistEventMapHighlightsKey = "";
          this.playlistEventMapTodayHighlights = null;
          this.playlistEventMapWeekHighlights = null;
          this.playlistEventMapHighlightsLoading = false;
          this.playlistEventMapHighlightsError = "";
          this.playlistEventMapController()?.setTimeHighlights?.({ point_indices: [], items: [], scope: this.playlistEventMapTimeFocusScope });
        }
      }
      if (force) this._playlistEventMapHighlightsForceRefresh = true;
      clearTimeout(this._playlistEventMapHighlightsTimer);
      this._playlistEventMapHighlightsTimer = setTimeout(() => {
        this._playlistEventMapHighlightsTimer = null;
        const forceRefresh = Boolean(this._playlistEventMapHighlightsForceRefresh);
        this._playlistEventMapHighlightsForceRefresh = false;
        void this.playlistEventMapLoadHighlights({ force: forceRefresh });
      }, Math.max(0, Number(delay || 0)));
    },
    async playlistEventMapLoadHighlights({ force = false } = {}) {
      const domainId = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const snapshotId = String(this.playlistEventMapSnapshotId || "").trim();
      const todayRange = this.playlistEventMapHighlightRange("today");
      const weekRange = this.playlistEventMapHighlightRange("week");
      if (!domainId || !snapshotId || (!todayRange && !weekRange)) {
        this.playlistEventMapTodayHighlights = null;
        this.playlistEventMapWeekHighlights = null;
        this.playlistEventMapApplyTimeHighlights();
        return;
      }
      const key = this.playlistEventMapHighlightsRequestKey();
      if (!force && key === this._playlistEventMapHighlightsKey) {
        this.playlistEventMapApplyTimeHighlights();
        return;
      }
      this._playlistEventMapHighlightsLastRequestAt = Date.now();
      this._abortCtrl("_playlistEventMapHighlightsAbortCtrl");
      const controller = new AbortController();
      this._playlistEventMapHighlightsAbortCtrl = controller;
      const token = Number(this._playlistEventMapHighlightsToken || 0) + 1;
      this._playlistEventMapHighlightsToken = token;
      this._playlistEventMapHighlightsPendingKey = key;
      this.playlistEventMapHighlightsLoading = true;
      this.playlistEventMapHighlightsError = "";
      const request = async (range) => {
        if (!range) return null;
        const params = new URLSearchParams({
          snapshot_id: snapshotId,
          window_start: String(this.playlistEventMapWindowStart || "").slice(0, 10),
          window_end: String(this.playlistEventMapWindowEnd || "").slice(0, 10),
          limit: "10",
        });
        if (range.scope === "24h") {
          params.set("scope", "24h");
        } else {
          params.set("event_date_start", range.start);
          params.set("event_date_end", range.end);
        }
        if (this.playlistEventMapTypeFilter) params.set("event_type_code", String(this.playlistEventMapTypeFilter));
        if (this.playlistEventMapEntityFilter?.normalized_key) {
          params.set("normalized_key", String(this.playlistEventMapEntityFilter.normalized_key));
          if (this.playlistEventMapEntityFilter.entity_type) params.set("entity_type", String(this.playlistEventMapEntityFilter.entity_type));
        }
        if (this.playlistEventMapTopicFocus?.topic_id) {
          params.set("topic_id", String(this.playlistEventMapTopicFocus.topic_id));
        }
        return this.api(`/domains/${encodeURIComponent(domainId)}/event-highlights?${params}`, { signal: controller.signal });
      };
      try {
        const [today, week] = await Promise.all([request(todayRange), request(weekRange)]);
        if (
          controller.signal.aborted
          || token !== Number(this._playlistEventMapHighlightsToken || 0)
          || key !== this.playlistEventMapHighlightsRequestKey()
        ) return;
        this.playlistEventMapTodayHighlights = today ? this.playlistEventMapNormalizeHighlightData(today) : null;
        this.playlistEventMapWeekHighlights = week ? this.playlistEventMapNormalizeHighlightData(week) : null;
        this._playlistEventMapHighlightsKey = key;
        this.playlistEventMapApplyTimeHighlights();
      } catch (error) {
        if (!abortError(error) && token === Number(this._playlistEventMapHighlightsToken || 0)) {
          this.playlistEventMapHighlightsError = error?.message || String(error);
          this.playlistEventMapTodayHighlights = null;
          this.playlistEventMapWeekHighlights = null;
          this._playlistEventMapHighlightsKey = "";
          this._playlistEventMapHighlightsPendingKey = "";
          this.playlistEventMapController()?.setTimeHighlights?.({ point_indices: [], items: [], scope: this.playlistEventMapTimeFocusScope });
        }
      } finally {
        if (this._playlistEventMapHighlightsAbortCtrl === controller) {
          this._playlistEventMapHighlightsAbortCtrl = null;
          this._playlistEventMapHighlightsPendingKey = "";
        }
        if (token === Number(this._playlistEventMapHighlightsToken || 0)) this.playlistEventMapHighlightsLoading = false;
      }
    },
    playlistEventMapHandleViewportSummary(summary) {
      const value = summary && typeof summary === "object" ? summary : {}; const level = String(value.sceneLevel || value.level || "overview");
      this.playlistEventMapViewportSummary = { sceneLevel: EVENT_MAP_LEVELS[level] ? level : "overview", viewportCanonicalCount: Math.max(0, Number(value.viewportCanonicalCount ?? value.visibleCount ?? 0)), renderedTopicLabelCount: Math.max(0, Number(value.renderedTopicLabelCount || 0)), ready: true };
      if (this.activeView === "field" && typeof this.saveFieldCursor === "function") this.saveFieldCursor();
    },

    playlistEventMapScheduleEntities({ delay = 300 } = {}) {
      if (this.playlistEventMapPlaying) return;
      clearTimeout(this._playlistEventMapEntitiesTimer);
      this._playlistEventMapEntitiesTimer = setTimeout(() => { this._playlistEventMapEntitiesTimer = null; this.playlistEventMapLoadEntities({ silent: true }).then(() => { if (this.playlistEventMapEntityFilter) this.playlistEventMapApplyEntityFilter(this.playlistEventMapEntityFilter, { resume: true }); }); }, Math.max(0, Number(delay || 0)));
    },
    async playlistEventMapLoadEntities({ silent = false } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); const snapshotId = String(this.playlistEventMapSnapshotId || ""); if (!pid || !snapshotId || this.playlistEventMapPlaying) return [];
      if (this._playlistEventMapEntityInFlight) { this._playlistEventMapEntityPending = true; return []; }
      const lifecycleGeneration = Number(this._playlistEventMapLifecycleGeneration || 0);
      const requestController = new AbortController(); this._playlistEventMapEntityAbortCtrl = requestController;
      this._playlistEventMapEntityInFlight = true; if (!silent) this.playlistEventMapEntitiesLoading = true;
      try {
        const params = new URLSearchParams({ snapshot_id: snapshotId }); if (this.playlistEventMapWindowStart) params.set("start_date", this.playlistEventMapWindowStart); if (this.playlistEventMapWindowEnd) params.set("end_date", this.playlistEventMapWindowEnd); const query = String(this.playlistEventMapEntityQuery || "").trim(); if (query) params.set("q", query);
        const requestKey = `${snapshotId}|${params.get("start_date") || ""}|${params.get("end_date") || ""}|${query}`;
        const entities = await this.api(
          `/playlists/${encodeURIComponent(pid)}/events/map/entities?${params}`,
          { signal: requestController.signal, cache: "no-store" }
        );
        const currentKey = `${String(this.playlistEventMapSnapshotId || "")}|${String(this.playlistEventMapWindowStart || "")}|${String(this.playlistEventMapWindowEnd || "")}|${String(this.playlistEventMapEntityQuery || "").trim()}`;
        if (requestController.signal.aborted || Number(this._playlistEventMapLifecycleGeneration || 0) !== lifecycleGeneration || this.playlistEventMapPlaying || currentKey !== requestKey) return [];
        this.playlistEventMapEntities = Array.isArray(entities) ? entities : []; return this.playlistEventMapEntities;
      } catch (error) { if (!abortError(error) && !silent) this.playlistEventMapError = error?.message || String(error); return []; }
      finally {
        if (this._playlistEventMapEntityAbortCtrl === requestController) {
          this._playlistEventMapEntityAbortCtrl = null; this._playlistEventMapEntityInFlight = false; this.playlistEventMapEntitiesLoading = false;
          if (this._playlistEventMapEntityPending) { this._playlistEventMapEntityPending = false; this.playlistEventMapScheduleEntities({ delay: 0 }); }
        }
      }
    },

    async playlistEventMapApplyEntityFilter(entity, { resume = false, focus = !resume } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); const snapshotId = String(this.playlistEventMapSnapshotId || ""); const normalizedKey = String(entity?.normalized_key || "").trim(); if (!pid || !snapshotId || !normalizedKey || this.playlistEventMapPlaying) return;
      if (!resume && this.activeView === "field") this.fieldCancelPendingSnapshotNavigation?.();
      if (!resume) this.playlistEventMapStopPlayback(); this.playlistEventMapEntityFilter = entity; this.fieldRequestedEntity = entity; if (this.activeView === "field") this._syncUrl({ push: false }); if (this.fieldLinearView) void this.fieldLoadLinearItems?.();
      const params = new URLSearchParams({ snapshot_id: snapshotId, normalized_key: normalizedKey }); if (entity?.entity_type) params.set("entity_type", String(entity.entity_type)); if (this.playlistEventMapWindowStart) params.set("start_date", this.playlistEventMapWindowStart); if (this.playlistEventMapWindowEnd) params.set("end_date", this.playlistEventMapWindowEnd);
      const token = Number(this._playlistEventMapEntityIndexToken || 0) + 1; this._playlistEventMapEntityIndexToken = token;
      this._abortCtrl("_playlistEventMapEntityIndexAbortCtrl"); const requestController = new AbortController(); this._playlistEventMapEntityIndexAbortCtrl = requestController;
      try {
        const buffer = await this.playlistEventMapFetchBinary(
          `/playlists/${encodeURIComponent(pid)}/events/map/entity-indices?${params}`,
          { signal: requestController.signal, cache: "no-store" }
        );
        if (requestController.signal.aborted || Number(this._playlistEventMapEntityIndexToken || 0) !== token || String(this.playlistEventMapSnapshotId || "") !== snapshotId) return;
        this.playlistEventMapEntityIndices = new Set(parseEventMapIndices(buffer)); this.playlistEventMapUpdateLayers(); if (focus) this.playlistEventMapFitIndices(this.playlistEventMapEntityIndices);
      }
      catch (error) { if (!abortError(error) && Number(this._playlistEventMapEntityIndexToken || 0) === token) this.playlistEventMapError = error?.message || String(error); }
      finally { if (this._playlistEventMapEntityIndexAbortCtrl === requestController) this._playlistEventMapEntityIndexAbortCtrl = null; }
    },
    playlistEventMapClearEntityFilter({ refreshList = true } = {}) { if (this.activeView === "field") this.fieldCancelPendingSnapshotNavigation?.(); this._playlistEventMapEntityIndexToken = Number(this._playlistEventMapEntityIndexToken || 0) + 1; this._abortCtrl("_playlistEventMapEntityIndexAbortCtrl"); this.playlistEventMapEntityFilter = null; this.fieldRequestedEntity = null; this.playlistEventMapEntityIndices = new Set(); this.playlistEventMapUpdateLayers(); if (this.activeView === "field") this._syncUrl({ push: false }); if (refreshList && this.fieldLinearView) void this.fieldLoadLinearItems?.(); },
    playlistEventMapFitIndices(indices) {
      const scene = this.playlistEventMapScene; if (!scene || !indices) return; let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity; let minZ = Infinity; let maxZ = -Infinity;
      const controller = this.playlistEventMapController();
      for (const raw of indices) { const index = Number(raw); if (!controller?.isActiveIndex(index)) continue; minX = Math.min(minX, scene.x[index]); maxX = Math.max(maxX, scene.x[index]); minY = Math.min(minY, scene.y[index]); maxY = Math.max(maxY, scene.y[index]); minZ = Math.min(minZ, scene.z[index]); maxZ = Math.max(maxZ, scene.z[index]); }
      if (![minX, maxX, minY, maxY, minZ, maxZ].every(Number.isFinite)) return;
      controller?.fitBounds({ minX, maxX, minY, maxY, minZ, maxZ });
    },

    async playlistEventMapSelectCanonical(index, canonicalId, { selectionToken = null } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); const snapshotId = String(this.playlistEventMapSnapshotId || "").trim(); const pointIndex = eventMapPointIndex(index); const id = String(canonicalId || "").trim();
      const controller = this.playlistEventMapController();
      if (!pid || !snapshotId || !id || !Number.isInteger(pointIndex) || !controller?.isActiveIndex(pointIndex)) return false;
      if (this.activeView === "field") this.fieldCancelPendingSnapshotNavigation?.();
      const suppliedSelectionToken = Number(selectionToken);
      const currentSelectionToken = Number.isInteger(suppliedSelectionToken) && suppliedSelectionToken > 0
        ? suppliedSelectionToken
        : Number(this._playlistEventMapSelectionToken || 0) + 1;
      if (!(Number.isInteger(suppliedSelectionToken) && suppliedSelectionToken > 0)) {
        this._playlistEventMapSelectionToken = currentSelectionToken;
      }
      if (Number(this._playlistEventMapSelectionToken || 0) !== currentSelectionToken) return false;
      this._playlistEventMapTopicDetailToken = Number(this._playlistEventMapTopicDetailToken || 0) + 1;
      this._abortCtrl("_playlistEventMapTopicDetailAbortCtrl");
      this.fieldSelectedChange = null; this.playlistEventMapStopPlayback(); const token = Number(this._playlistEventMapDetailToken || 0) + 1; this._playlistEventMapDetailToken = token; this.playlistEventMapSelectedIndex = pointIndex; this.playlistEventMapSelectedKind = "canonical"; this.playlistEventMapSelectedId = id; this.fieldRequestedCanonicalId = id; this.fieldRequestedTopicId = ""; this.fieldRequestedEvidenceId = ""; this.fieldEvidenceDetail = null; this.playlistEventMapSelectedDetail = null; this.playlistEventMapDetailLoading = true; this.playlistEventMapDetailTab = "overview"; controller.setSelection(pointIndex, null); this.playlistEventMapUpdateLayers();
      if (this.activeView === "field") this._syncUrl({ push: false });
      this._abortCtrl("_playlistEventMapDetailAbortCtrl"); const requestController = new AbortController(); this._playlistEventMapDetailAbortCtrl = requestController;
      const selectionStillCurrent = () => (
        Number(this._playlistEventMapSelectionToken || 0) === currentSelectionToken
        && Number(this._playlistEventMapDetailToken || 0) === token
        && String(this.playlistPageId || this.selectedPlaylistId || "").trim() === pid
        && String(this.playlistEventMapSnapshotId || "") === snapshotId
        && this.playlistEventMapSelectedKind === "canonical"
        && String(this.playlistEventMapSelectedId || "") === id
      );
      try {
        const detail = await this.api(
          `/playlists/${encodeURIComponent(pid)}/events/map/canonical/${encodeURIComponent(id)}?${new URLSearchParams({ snapshot_id: snapshotId })}`,
          { signal: requestController.signal, cache: "no-store" }
        );
        if (requestController.signal.aborted || !selectionStillCurrent()) return false;
        const references = await this.api(
          `/domains/${encodeURIComponent(pid)}/objects/canonical/${encodeURIComponent(id)}/brief-references`,
          { signal: requestController.signal, cache: "no-store" }
        );
        if (requestController.signal.aborted || !selectionStillCurrent()) return false;
        this.playlistEventMapSelectedDetail = detail ? { ...detail, brief_references: references?.items || [] } : null; this.playlistEventMapApplyDetailOverlay();
      }
      catch (error) { if (!abortError(error) && Number(this._playlistEventMapDetailToken || 0) === token) this.playlistEventMapError = error?.message || String(error); }
      finally {
        if (this._playlistEventMapDetailAbortCtrl === requestController) this._playlistEventMapDetailAbortCtrl = null;
        if (Number(this._playlistEventMapDetailToken || 0) === token) this.playlistEventMapDetailLoading = false;
      }
      return selectionStillCurrent();
    },
    playlistEventMapClearSelection({ update = true, preserveRequest = false, selectionToken = null } = {}) {
      const suppliedSelectionToken = Number(selectionToken);
      const preservesSelection = Number.isInteger(suppliedSelectionToken) && suppliedSelectionToken > 0
        && Number(this._playlistEventMapSelectionToken || 0) === suppliedSelectionToken;
      if (!preservesSelection && this.activeView === "field") this.fieldCancelPendingSnapshotNavigation?.();
      if (!preservesSelection) this._playlistEventMapSelectionToken = Number(this._playlistEventMapSelectionToken || 0) + 1;
      this._playlistEventMapDetailToken = Number(this._playlistEventMapDetailToken || 0) + 1; this._abortCtrl("_playlistEventMapDetailAbortCtrl"); if (this.playlistEventMapSelectedKind === "topic") this.playlistEventMapTopicFocus = null;
      this.playlistEventMapSelectedIndex = null; this.playlistEventMapSelectedKind = ""; this.playlistEventMapSelectedId = ""; if (!preserveRequest) this.fieldRequestedCanonicalId = ""; this.fieldSelectedChange = null; this.playlistEventMapSelectedDetail = null; this.playlistEventMapDetailLoading = false; this.playlistEventMapDetailTab = "overview"; this.playlistEventMapController()?.setSelection(null, null); if (update) this.playlistEventMapUpdateLayers();
      if (this.activeView === "field" && !preserveRequest) this._syncUrl({ push: false });
    },
    playlistEventMapDetailTabs() { const detail = this.playlistEventMapSelectedDetail || {}; return [{ key: "overview", label: "概览", count: null }, { key: "records", label: "记录", count: Array.isArray(detail.members) ? detail.members.length : 0 }, { key: "entities", label: "实体", count: Array.isArray(detail.entities) ? detail.entities.length : 0 }, { key: "story", label: "故事", count: Array.isArray(detail.story_edges) ? detail.story_edges.length : 0 }, { key: "evidence", label: "证据", count: Array.isArray(detail.evidence) ? detail.evidence.length : 0 }, { key: "briefs", label: "简报", count: Array.isArray(detail.brief_references) ? detail.brief_references.length : 0 }]; },
    playlistEventMapSetDetailTab(tab) { this.playlistEventMapDetailTab = new Set(["overview", "records", "entities", "story", "evidence", "briefs"]).has(String(tab)) ? String(tab) : "overview"; this.playlistEventMapApplyDetailOverlay(); },
    playlistEventMapApplyDetailOverlay() { if (this.playlistEventMapSelectedKind !== "canonical" || !this.playlistEventMapSelectedDetail) return; const detail = this.playlistEventMapDetailTab === "story" ? this.playlistEventMapSelectedDetail : { ...this.playlistEventMapSelectedDetail, story_edges: [] }; this.playlistEventMapController()?.setSelectionDetail(detail); },
    playlistEventMapTopicByIndex(index) {
      const topicIndex = Number(index);
      return (Array.isArray(this.playlistEventMapManifest?.topics) ? this.playlistEventMapManifest.topics : [])
        .find((topic, fallback) => Number(topic?.topic_index ?? fallback) === topicIndex) || null;
    },
    playlistEventMapTopicParent(topic = this.playlistEventMapSelectedDetail) {
      const rawParentIndex = topic?.parent_topic_index;
      const parentIndex = Number(rawParentIndex);
      return rawParentIndex !== null && rawParentIndex !== undefined && Number.isInteger(parentIndex) && parentIndex >= 0 ? this.playlistEventMapTopicByIndex(parentIndex) : null;
    },
    playlistEventMapTopicActiveCount(topic) {
      return this.playlistEventMapController()?.topicActiveCount(Number(topic?.topic_index)) || 0;
    },
    playlistEventMapTopicChildren(topic = this.playlistEventMapSelectedDetail) {
      const index = Number(topic?.topic_index);
      if (!Number.isInteger(index) || index < 0) return [];
      return (Array.isArray(this.playlistEventMapManifest?.topics) ? this.playlistEventMapManifest.topics : [])
        .filter((item) => item?.parent_topic_index !== null && item?.parent_topic_index !== undefined && Number(item.parent_topic_index) === index && this.playlistEventMapTopicActiveCount(item) > 0)
        .map((item) => ({ ...item, canonical_count: this.playlistEventMapTopicActiveCount(item) }))
        .sort((left, right) => Number(right.canonical_count || 0) - Number(left.canonical_count || 0) || String(left.label || "").localeCompare(String(right.label || "")));
    },
    async playlistEventMapLoadTopicDetail(topic = this.playlistEventMapSelectedDetail) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); const snapshotId = String(this.playlistEventMapSnapshotId || "").trim(); const topicId = String(topic?.topic_id || "").trim();
      if (!pid || !snapshotId || !topicId) return null;
      const selectionToken = Number(this._playlistEventMapSelectionToken || 0);
      const token = Number(this._playlistEventMapTopicDetailToken || 0) + 1; this._playlistEventMapTopicDetailToken = token; this.playlistEventMapTopicDetailLoading = true;
      const topicRequestStillCurrent = () => (
        Number(this._playlistEventMapTopicDetailToken || 0) === token
        && Number(this._playlistEventMapSelectionToken || 0) === selectionToken
        && String(this.playlistEventMapSnapshotId || "") === snapshotId
        && this.playlistEventMapSelectedKind === "topic"
        && String(this.playlistEventMapSelectedId || "") === topicId
      );
      const params = new URLSearchParams({ snapshot_id: snapshotId, limit: "20" });
      if (this.playlistEventMapWindowStart) params.set("start_date", this.playlistEventMapWindowStart);
      if (this.playlistEventMapWindowEnd) params.set("end_date", this.playlistEventMapWindowEnd);
      if (this.playlistEventMapTypeFilter) params.set("event_type_code", String(this.playlistEventMapTypeFilter));
      this._abortCtrl("_playlistEventMapTopicDetailAbortCtrl"); const requestController = new AbortController(); this._playlistEventMapTopicDetailAbortCtrl = requestController;
      try {
        const [detail, briefReferences] = await Promise.all([
          this.api(
            `/playlists/${encodeURIComponent(pid)}/events/map/topic/${encodeURIComponent(topicId)}?${params}`,
            { signal: requestController.signal, cache: "no-store" }
          ),
          this.api(
            `/domains/${encodeURIComponent(pid)}/objects/topic/${encodeURIComponent(topicId)}/brief-references?snapshot_id=${encodeURIComponent(snapshotId)}`,
            { signal: requestController.signal, cache: "no-store" }
          ),
        ]);
        if (requestController.signal.aborted || !topicRequestStillCurrent()) return null;
        this.playlistEventMapTopicDetail = detail ? { ...detail, brief_references: briefReferences?.items || [] } : null;
        return this.playlistEventMapTopicDetail;
      } catch (error) {
        if (!abortError(error) && topicRequestStillCurrent()) this.playlistEventMapError = error?.message || String(error);
        return null;
      } finally {
        if (this._playlistEventMapTopicDetailAbortCtrl === requestController) this._playlistEventMapTopicDetailAbortCtrl = null;
        if (Number(this._playlistEventMapTopicDetailToken || 0) === token) this.playlistEventMapTopicDetailLoading = false;
      }
    },
    playlistEventMapSelectTopicRepresentative(item) {
      const pointIndex = eventMapPointIndex(item?.point_index);
      if (!Number.isInteger(pointIndex)) return;
      this.playlistEventMapSelectCanonical(pointIndex, item.canonical_id);
    },
    playlistEventMapFocusSelectedCanonical() {
      if (this.playlistEventMapSelectedKind !== "canonical") return;
      const pointIndex = eventMapPointIndex(this.playlistEventMapSelectedIndex);
      if (Number.isInteger(pointIndex)) this.playlistEventMapController()?.focusPoint(pointIndex);
    },
    playlistEventMapRefreshTopicDetail() {
      if (this.playlistEventMapSelectedKind !== "topic") return;
      void this.playlistEventMapLoadTopicDetail(this.playlistEventMapSelectedDetail);
    },

    playlistEventMapScheduleSearch() { clearTimeout(this._playlistEventMapSearchTimer); this.playlistEventMapSearchActiveIndex = -1; if (String(this.playlistEventMapSearchQuery || "").trim().length < 2) { this.playlistEventMapSearchResults = []; this.playlistEventMapSearchLoading = false; return; } this._playlistEventMapSearchTimer = setTimeout(() => { this._playlistEventMapSearchTimer = null; this.playlistEventMapSearch(); }, 250); },
    async playlistEventMapSearch() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); const snapshotId = String(this.playlistEventMapSnapshotId || ""); const query = String(this.playlistEventMapSearchQuery || "").trim(); if (!pid || !snapshotId || query.length < 2) { this.playlistEventMapSearchResults = []; return; }
      const token = Number(this._playlistEventMapSearchToken || 0) + 1; this._playlistEventMapSearchToken = token; this.playlistEventMapSearchLoading = true;
      this._abortCtrl("_playlistEventMapSearchAbortCtrl"); const requestController = new AbortController(); this._playlistEventMapSearchAbortCtrl = requestController;
      try {
        const payload = await this.api(
          `/playlists/${encodeURIComponent(pid)}/events/map/search?${new URLSearchParams({ snapshot_id: snapshotId, q: query })}`,
          { signal: requestController.signal, cache: "no-store" }
        );
        if (requestController.signal.aborted || Number(this._playlistEventMapSearchToken || 0) !== token || String(this.playlistEventMapSnapshotId || "") !== snapshotId) return;
        this.playlistEventMapSearchResults = Array.isArray(payload) ? payload : payload?.results || []; this.playlistEventMapSearchActiveIndex = this.playlistEventMapSearchResults.length ? 0 : -1;
      }
      catch (error) { if (!abortError(error) && Number(this._playlistEventMapSearchToken || 0) === token) this.playlistEventMapError = error?.message || String(error); }
      finally {
        if (this._playlistEventMapSearchAbortCtrl === requestController) this._playlistEventMapSearchAbortCtrl = null;
        if (Number(this._playlistEventMapSearchToken || 0) === token) this.playlistEventMapSearchLoading = false;
      }
    },
    async playlistEventMapSelectSearchResult(item) {
      const snapshotId = String(this.playlistEventMapSnapshotId || "");
      const selectionToken = Number(this._playlistEventMapSelectionToken || 0);
      const searchToken = Number(this._playlistEventMapSearchToken || 0) + 1;
      this._playlistEventMapSearchToken = searchToken;
      clearTimeout(this._playlistEventMapSearchTimer); this._playlistEventMapSearchTimer = null;
      this._abortCtrl("_playlistEventMapSearchAbortCtrl"); this.playlistEventMapSearchLoading = false;
      try {
        await this.playlistEventMapWaitForCompleteScene({ includeMetadata: item?.kind === "topic" });
      } catch (error) {
        if (
          !abortError(error)
          && String(this.playlistEventMapSnapshotId || "") === snapshotId
          && Number(this._playlistEventMapSearchToken || 0) === searchToken
          && Number(this._playlistEventMapSelectionToken || 0) === selectionToken
        ) this.playlistEventMapError = error?.message || String(error);
        return;
      }
      if (
        String(this.playlistEventMapSnapshotId || "") !== snapshotId
        || Number(this._playlistEventMapSearchToken || 0) !== searchToken
        || Number(this._playlistEventMapSelectionToken || 0) !== selectionToken
      ) return;
      const pointIndex = eventMapPointIndex(item?.point_index);
      if (this.activeView === "field") { this.fieldLinearView = false; this.fieldObservationRailOpen = false; }
      if (item?.kind === "canonical" && Number.isInteger(pointIndex)) { this.playlistEventMapController()?.focusPoint(pointIndex); this.playlistEventMapSelectCanonical(pointIndex, item.id || item.canonical_id); }
      else if (item?.kind === "topic") this.playlistEventMapSelectTopic(item, { focus: true });
      else if (item?.kind === "entity") this.playlistEventMapApplyEntityFilter(item);
      this.playlistEventMapCloseSearch();
    },
    playlistEventMapSearchGroups() { const indexed = (this.playlistEventMapSearchResults || []).map((item, index) => ({ ...item, _eventMapSearchIndex: index })); return [{ key: "canonical", label: "真实事件" }, { key: "topic", label: "主题" }, { key: "entity", label: "实体" }].map((group) => ({ ...group, items: indexed.filter((item) => item.kind === group.key) })).filter((group) => group.items.length); },
    playlistEventMapSearchKindLabel(kind) { return { canonical: "真实事件", topic: "主题", entity: "实体 · 选择后应用筛选" }[String(kind || "")] || "对象"; },
    playlistEventMapSearchKeydown(event) { const key = String(event?.key || ""); const count = (this.playlistEventMapSearchResults || []).length; if (key === "Escape") { this.playlistEventMapCloseSearch(); event?.preventDefault?.(); return; } if (!["ArrowDown", "ArrowUp", "Enter"].includes(key) || !count) return; if (key === "Enter") this.playlistEventMapSelectSearchResult(this.playlistEventMapSearchResults[Math.max(0, Math.min(count - 1, Number(this.playlistEventMapSearchActiveIndex || 0)))]); else { const delta = key === "ArrowDown" ? 1 : -1; this.playlistEventMapSearchActiveIndex = (Number(this.playlistEventMapSearchActiveIndex || 0) + delta + count) % count; } event?.preventDefault?.(); },
    playlistEventMapClearSearch() { clearTimeout(this._playlistEventMapSearchTimer); this._playlistEventMapSearchTimer = null; this._playlistEventMapSearchToken = Number(this._playlistEventMapSearchToken || 0) + 1; this._abortCtrl("_playlistEventMapSearchAbortCtrl"); this.playlistEventMapSearchQuery = ""; this.playlistEventMapSearchResults = []; this.playlistEventMapSearchLoading = false; this.playlistEventMapSearchActiveIndex = -1; },
    playlistEventMapOpenSearch() { this.playlistEventMapSearchOpen = true; this.playlistEventMapFiltersOpen = false; if (this.activeView === "field") this.fieldObservationRailOpen = false; },
    playlistEventMapCloseSearch() { this.playlistEventMapSearchOpen = false; this.playlistEventMapClearSearch(); },

    playlistEventMapSelectTopic(item, { focus = false } = {}) {
      const controller = this.playlistEventMapController(); const topics = Array.isArray(this.playlistEventMapManifest?.topics) ? this.playlistEventMapManifest.topics : []; const id = String(item?.id || item?.topic_id || ""); const index = Number.isInteger(Number(item?.topic_index)) ? Number(item.topic_index) : Number(topics.find((topic) => String(topic.topic_id || "") === id)?.topic_index); if (!Number.isInteger(index) || index < 0 || !controller?.hasActiveTopic(index)) return;
      if (!focus && this.playlistEventMapSelectedKind === "topic" && Number(this.playlistEventMapTopicFocus?.topic_index) === index) { this.playlistEventMapClearTopicFocus(); return; }
      if (this.activeView === "field") this.fieldCancelPendingSnapshotNavigation?.();
      this._playlistEventMapSelectionToken = Number(this._playlistEventMapSelectionToken || 0) + 1; this._playlistEventMapDetailToken = Number(this._playlistEventMapDetailToken || 0) + 1; this._abortCtrl("_playlistEventMapDetailAbortCtrl");
      const topic = { ...(topics.find((entry, fallback) => Number(entry?.topic_index ?? fallback) === index) || {}), ...item, topic_index: index }; this.fieldSelectedChange = null; this.playlistEventMapStopPlayback(); this.playlistEventMapTopicFocus = topic; this.playlistEventMapSelectedIndex = null; this.playlistEventMapSelectedKind = "topic"; this.playlistEventMapSelectedId = String(topic.topic_id || id); this.fieldRequestedCanonicalId = ""; this.fieldRequestedTopicId = this.playlistEventMapSelectedId; this.fieldRequestedEvidenceId = ""; this.fieldEvidenceDetail = null; this.playlistEventMapSelectedDetail = topic; this.playlistEventMapTopicDetail = null; this.playlistEventMapTopicDetailLoading = true; this.playlistEventMapDetailLoading = false; controller.setSelection(null, null); this.playlistEventMapUpdateLayers(); if (focus) controller.focusTopic(index); void this.playlistEventMapLoadTopicDetail(topic);
      if (this.activeView === "field") this._syncUrl({ push: false });
    },
    playlistEventMapClearTopicFocus({ update = true } = {}) { if (this.activeView === "field") this.fieldCancelPendingSnapshotNavigation?.(); const wasSelected = this.playlistEventMapSelectedKind === "topic"; this._playlistEventMapSelectionToken = Number(this._playlistEventMapSelectionToken || 0) + 1; this._playlistEventMapDetailToken = Number(this._playlistEventMapDetailToken || 0) + 1; this._playlistEventMapTopicDetailToken = Number(this._playlistEventMapTopicDetailToken || 0) + 1; this._abortCtrl("_playlistEventMapDetailAbortCtrl"); this._abortCtrl("_playlistEventMapTopicDetailAbortCtrl"); this.playlistEventMapTopicFocus = null; this.playlistEventMapTopicDetail = null; this.playlistEventMapTopicDetailLoading = false; this.fieldRequestedTopicId = ""; this.playlistEventMapController()?.clearTopicFocus(); if (wasSelected) { this.playlistEventMapSelectedKind = ""; this.playlistEventMapSelectedId = ""; this.playlistEventMapSelectedDetail = null; } if (update) this.playlistEventMapUpdateLayers(); if (this.activeView === "field") this._syncUrl({ push: false }); },

    playlistEventMapFullBounds() { const manifest = this.playlistEventMapManifest || {}; const bounds = manifest.time_bounds || {}; const months = this.playlistEventMapTimelineMonths || []; const start = String(bounds.start || (months[0] && `${months[0].month}-01`) || "").slice(0, 10); const end = String(bounds.end || (months.at(-1) && monthEnd(months.at(-1).month)) || "").slice(0, 10); return start && end && start <= end ? { start, end } : { start: "", end: "" }; },
    playlistEventMapNormalBounds() { const full = this.playlistEventMapFullBounds(); if (!full.start || !full.end) return full; const contentStart = String(this.playlistDetail?.earliest_date || full.start || "").slice(0, 10); const today = String(typeof this._todayIsoLocal === "function" ? this._todayIsoLocal() : localToday()).slice(0, 10); return contentStart && today && contentStart <= today ? { start: contentStart, end: today } : full; },
    playlistEventMapTimelineBounds() { return this.playlistEventMapTimelineScope === "full" ? this.playlistEventMapFullBounds() : this.playlistEventMapNormalBounds(); },
    playlistEventMapHasExtendedRange() { const full = this.playlistEventMapFullBounds(); const normal = this.playlistEventMapNormalBounds(); return full.start < normal.start || full.end > normal.end; },
    playlistEventMapSetTimelineScope(scope) { const normalized = scope === "full" ? "full" : "normal"; if (this.playlistEventMapTimelineScope === normalized) return; this.playlistEventMapStopPlayback({ refresh: false, loadEntities: false }); this.playlistEventMapTimelineScope = normalized; this._playlistEventMapTimelineItemsCache = null; this.playlistEventMapInitializeWindow(); this.playlistEventMapUpdateLayers(); this.playlistEventMapRefreshTopicDetail(); this.playlistEventMapScheduleEntities(); },
    playlistEventMapTimelineItems() {
      const bounds = this.playlistEventMapTimelineBounds(); const start = monthIndex(bounds.start); const end = monthIndex(bounds.end); if (start === null || end === null || end < start) return [];
      const source = this.playlistEventMapTimelineMonths || []; const key = `${this.playlistEventMapTimelineScope}|${bounds.start}|${bounds.end}`; if (this._playlistEventMapTimelineItemsCache?.source === source && this._playlistEventMapTimelineItemsCache.key === key) return this._playlistEventMapTimelineItemsCache.value;
      const counts = new Map(source.map((item) => [item.month, item])); const values = []; for (let index = start; index <= end; index += 1) { const month = monthFromIndex(index); values.push({ ...(counts.get(month) || { month, canonical_count: 0, record_count: 0 }), index: values.length }); } this._playlistEventMapTimelineItemsCache = { source, key, value: values }; return values;
    },
    playlistEventMapTimelineMaxCount() { const items = this.playlistEventMapTimelineItems(); if (this._playlistEventMapTimelineMaxCache?.items === items) return this._playlistEventMapTimelineMaxCache.value; const value = Math.max(1, ...items.map((item) => Number(item.record_count || 0))); this._playlistEventMapTimelineMaxCache = { items, value }; return value; },
    playlistEventMapTimelineBarStyle(item) { const ratio = Math.sqrt(Math.max(0, Number(item.record_count || 0)) / this.playlistEventMapTimelineMaxCount()); return `height:${Math.max(2, ratio * 100).toFixed(2)}%`; },
    playlistEventMapTimelineTicks() { const items = this.playlistEventMapTimelineItems(); if (!items.length) return []; const width = Math.max(1, Number(this.playlistEventMapTimelineTrackWidth || 960)); const step = niceMonthStep(items.length * 7 / width); const first = monthIndex(items[0].month); const indexes = new Set([0, items.length - 1, ...this.playlistEventMapTimelineMarks().map((mark) => mark.index)]); const offset = first === null ? 0 : (step - ((first % step + step) % step)) % step; for (let index = offset; index < items.length; index += step) indexes.add(index); return Array.from(indexes).sort((a, b) => a - b).map((index) => ({ key: `timeline-tick-${index}`, index })); },
    playlistEventMapTimelineMarks() { const items = this.playlistEventMapTimelineItems(); if (!items.length) return []; const width = Math.max(160, Number(this.playlistEventMapTimelineTrackWidth || 960)); const step = niceMonthStep(items.length / Math.max(1, Math.floor(width / 72) - 1)); const first = monthIndex(items[0].month); return items.filter((item, index) => index === 0 || index === items.length - 1 || (monthIndex(item.month) !== null && first !== null && monthIndex(item.month) % step === 0)).map((item) => ({ key: `timeline-mark-${item.month}`, index: item.index, label: step >= 12 && item.month.endsWith("-01") ? item.month.slice(0, 4) : item.month })); },
    playlistEventMapTimelineMarkStyle(mark) { const count = this.playlistEventMapTimelineItems().length; if (!count) return "display:none"; const index = Math.max(0, Math.min(count - 1, Number(mark?.index || 0))); return `left:${((index + .5) / count * 100).toFixed(6)}%;transform:${index === 0 ? "translateX(0)" : index === count - 1 ? "translateX(-100%)" : "translateX(-50%)"};`; },

    playlistEventMapWindowIndices() {
      const items = this.playlistEventMapTimelineItems(); if (!items.length) return { start: 0, end: 0, max: 0, length: 0 }; const first = monthIndex(items[0].month); const start = Math.max(0, Math.min(items.length - 1, (monthIndex(this.playlistEventMapWindowStart) ?? first) - first)); const end = Math.max(start, Math.min(items.length - 1, (monthIndex(this.playlistEventMapWindowEnd) ?? first) - first)); return { start, end, max: items.length - 1, length: end - start + 1 };
    },
    playlistEventMapWindowDisplayIndices() { const committed = this.playlistEventMapWindowIndices(); const start = this.playlistEventMapWindowPreviewStartIndex; const end = this.playlistEventMapWindowPreviewEndIndex; if (!Number.isInteger(start) || !Number.isInteger(end)) return committed; return { ...committed, start: Math.max(0, Math.min(committed.max, start)), end: Math.max(0, Math.min(committed.max, end)) }; },
    playlistEventMapWindowDatesForIndices(start, end) {
      const items = this.playlistEventMapTimelineItems(); const bounds = this.playlistEventMapTimelineBounds(); if (!items.length) return { start: "", end: "" }; const maximum = items.length - 1; const startIndex = Math.max(0, Math.min(maximum, Math.round(Number(start)))); const endIndex = Math.max(startIndex, Math.min(maximum, Math.round(Number(end)))); const startDate = monthStart(items[startIndex].month); const endDate = monthEnd(items[endIndex].month); return { start: startDate < bounds.start ? bounds.start : startDate, end: endDate > bounds.end ? bounds.end : endDate };
    },
    playlistEventMapWindowDisplayDates() { const range = this.playlistEventMapWindowDisplayIndices(); return this.playlistEventMapWindowDatesForIndices(range.start, range.end); },
    playlistEventMapSetWindowPreview(start, end) {
      const max = this.playlistEventMapTimelineItems().length - 1; if (max < 0) return; this.playlistEventMapWindowPreviewStartIndex = Math.max(0, Math.min(max, Math.round(start))); this.playlistEventMapWindowPreviewEndIndex = Math.max(this.playlistEventMapWindowPreviewStartIndex, Math.min(max, Math.round(end))); const dates = this.playlistEventMapWindowDisplayDates(); this.playlistEventMapController()?.previewWindow({ windowStart: dates.start, windowEnd: dates.end });
    },
    playlistEventMapClearWindowPreview() { this.playlistEventMapWindowPreviewStartIndex = null; this.playlistEventMapWindowPreviewEndIndex = null; },
    playlistEventMapCommitWindowIndices(start, end, { update = true, pause = true } = {}) {
      const items = this.playlistEventMapTimelineItems(); const bounds = this.playlistEventMapTimelineBounds(); if (!items.length) return false;
      if (pause) this.playlistEventMapStopPlayback({ refresh: false, loadEntities: false });
      const maximum = items.length - 1;
      const nextStartIndex = Math.max(0, Math.min(maximum, Math.round(Number(start))));
      const nextEndIndex = Math.max(nextStartIndex, Math.min(maximum, Math.round(Number(end))));
      const nextStart = monthStart(items[nextStartIndex].month) < bounds.start ? bounds.start : monthStart(items[nextStartIndex].month);
      const nextEnd = monthEnd(items[nextEndIndex].month) > bounds.end ? bounds.end : monthEnd(items[nextEndIndex].month);
      const changed = nextStart !== this.playlistEventMapWindowStart || nextEnd !== this.playlistEventMapWindowEnd;
      this.playlistEventMapWindowMonths = snapWindowMonths(nextEndIndex - nextStartIndex + 1);
      this.playlistEventMapWindowStart = nextStart;
      this.playlistEventMapWindowEnd = nextEnd;
      this.playlistEventMapClearWindowPreview();
      if (update && changed) {
        this.playlistEventMapEntityIndices = new Set();
        this.playlistEventMapEntityFilter = null;
        this.playlistEventMapUpdateLayers();
        this.playlistEventMapRefreshTopicDetail();
        if (!this.playlistEventMapPlaying) this.playlistEventMapScheduleEntities();
        if (this.fieldLinearView) void this.fieldLoadLinearItems?.();
        if (this.activeView === "field") {
          this.fieldRequestedWindowStart = this.playlistEventMapWindowStart;
          this.fieldRequestedWindowEnd = this.playlistEventMapWindowEnd;
          this._syncUrl?.({ push: false });
        }
      }
      return changed;
    },
    playlistEventMapSetWindowEndIndex(value, { update = true, pause = true } = {}) {
      const items = this.playlistEventMapTimelineItems(); if (!items.length) return false;
      const length = Math.max(1, Math.min(items.length, snapWindowMonths(this.playlistEventMapWindowMonths)));
      const end = Math.max(length - 1, Math.min(items.length - 1, Math.round(Number(value))));
      return this.playlistEventMapCommitWindowIndices(end - length + 1, end, { update, pause });
    },
    playlistEventMapSetWindowMonths(value) { const months = snapWindowMonths(value); if (months === this.playlistEventMapWindowMonths) return; this.playlistEventMapWindowMonths = months; this.playlistEventMapSetWindowEndIndex(this.playlistEventMapWindowIndices().end, { update: true, pause: true }); },
    playlistEventMapReturnToToday() { const items = this.playlistEventMapTimelineItems(); if (!items.length) return; this.playlistEventMapSetWindowEndIndex(items.length - 1); },
    playlistEventMapSelectTimelineWindowEnd(event) { if (!primaryPointer(event)) return; const index = this.playlistEventMapTimelineIndexFromClientX(event.clientX); if (index !== null) this.playlistEventMapSetWindowEndIndex(index); },
    playlistEventMapSelectionStyle() { const items = this.playlistEventMapTimelineItems(); if (!items.length) return "display:none"; const range = this.playlistEventMapWindowDisplayIndices(); return `left:${(range.start / items.length * 100).toFixed(4)}%;right:${(100 - (range.end + 1) / items.length * 100).toFixed(4)}%`; },
    playlistEventMapTimelineIndexFromClientX(clientX) { const items = this.playlistEventMapTimelineItems(); const track = this.$refs?.playlistEventMapTimelineTrack; const rect = track?.getBoundingClientRect?.(); const width = Number(rect?.width || track?.clientWidth || 0); if (!items.length || !Number.isFinite(Number(clientX)) || width <= 0) return null; return Math.max(0, Math.min(items.length - 1, Math.floor(Math.max(0, Math.min(.999999, (Number(clientX) - Number(rect?.left || 0)) / width)) * items.length))); },
    playlistEventMapStartTimelineWindowDrag(event) { if (!primaryPointer(event) || !this.playlistEventMapTimelineItems().length) return; this.playlistEventMapStopPlayback({ refresh: false, loadEntities: false }); this.playlistEventMapController()?.collapseMediaCard(); const range = this.playlistEventMapWindowIndices(); const pointerIndex = this.playlistEventMapTimelineIndexFromClientX(event.clientX); this.playlistEventMapTimelineWindowDragging = true; this.playlistEventMapTimelineWindowPointerId = event.pointerId; this.playlistEventMapTimelineWindowResizeEdge = ""; this._playlistEventMapTimelineWindowOffset = pointerIndex === null ? 0 : range.start - pointerIndex; this.playlistEventMapSetWindowPreview(range.start, range.end); event.currentTarget?.setPointerCapture?.(event.pointerId); event.preventDefault(); event.stopPropagation(); },
    playlistEventMapStartTimelineWindowResize(event, edge) { if (!primaryPointer(event) || !["start", "end"].includes(edge)) return; this.playlistEventMapStopPlayback({ refresh: false, loadEntities: false }); this.playlistEventMapController()?.collapseMediaCard(); const range = this.playlistEventMapWindowIndices(); this.playlistEventMapTimelineWindowDragging = true; this.playlistEventMapTimelineWindowPointerId = event.pointerId; this.playlistEventMapTimelineWindowResizeEdge = edge; this._playlistEventMapTimelineWindowOffset = null; this.playlistEventMapSetWindowPreview(range.start, range.end); event.currentTarget?.setPointerCapture?.(event.pointerId); event.preventDefault(); event.stopPropagation(); },
    playlistEventMapMoveTimelineWindowDrag(event) {
      if (!this.playlistEventMapTimelineWindowDragging || event.pointerId !== this.playlistEventMapTimelineWindowPointerId) return;
      const index = this.playlistEventMapTimelineIndexFromClientX(event.clientX); if (index === null) return;
      const range = this.playlistEventMapWindowIndices(); const edge = this.playlistEventMapTimelineWindowResizeEdge;
      if (edge) {
        const rawLength = edge === "start" ? range.end - index + 1 : index - range.start + 1;
        const maximumLength = edge === "start" ? range.end + 1 : range.max - range.start + 1;
        const choices = [3, 6, 9, 12].filter((months) => months <= maximumLength);
        const length = choices.length
          ? choices.reduce((best, months) => Math.abs(months - rawLength) < Math.abs(best - rawLength) ? months : best, choices[0])
          : Math.max(1, maximumLength);
        const start = edge === "start" ? range.end - length + 1 : range.start;
        const end = edge === "end" ? range.start + length - 1 : range.end;
        this.playlistEventMapSetWindowPreview(start, end);
      } else {
        const length = range.length;
        const start = Math.max(0, Math.min(range.max - length + 1, index + Number(this._playlistEventMapTimelineWindowOffset || 0)));
        this.playlistEventMapSetWindowPreview(start, start + length - 1);
      }
      event.preventDefault();
    },
    playlistEventMapEndTimelineWindowDrag(event) { if (!this.playlistEventMapTimelineWindowDragging || event.pointerId !== this.playlistEventMapTimelineWindowPointerId) return; this.playlistEventMapMoveTimelineWindowDrag(event); const range = this.playlistEventMapWindowDisplayIndices(); this.playlistEventMapReleaseTimelineWindowDrag({ restore: false }); this.playlistEventMapCommitWindowIndices(range.start, range.end); },
    playlistEventMapReleaseTimelineWindowDrag({ restore = true } = {}) { this.playlistEventMapTimelineWindowDragging = false; this.playlistEventMapTimelineWindowPointerId = null; this.playlistEventMapTimelineWindowResizeEdge = ""; this._playlistEventMapTimelineWindowOffset = null; this.playlistEventMapClearWindowPreview(); if (restore) this.playlistEventMapController()?.cancelWindowPreview(); },
    playlistEventMapTimelineWindowKeydown(event) { const key = String(event?.key || ""); const range = this.playlistEventMapWindowIndices(); const delta = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: range.length, PageUp: -range.length }[key]; if (key === "Home") this.playlistEventMapSetWindowEndIndex(range.length - 1); else if (key === "End") this.playlistEventMapSetWindowEndIndex(range.max); else if (delta) this.playlistEventMapSetWindowEndIndex(range.end + delta); else return; event.preventDefault(); event.stopPropagation(); },
    playlistEventMapShiftWindow(delta = 1, { playback = false } = {}) { const range = this.playlistEventMapWindowIndices(); const next = range.end + Number(delta || 0); if (next < range.length - 1 || next > range.max) return false; this.playlistEventMapSetWindowEndIndex(next, { update: true, pause: !playback }); return true; },
    playlistEventMapTogglePlayback() {
      if (this.playlistEventMapPlaying) { this.playlistEventMapStopPlayback(); return; }
      if (!this.playlistEventMapCanPlay()) return;
      const range = this.playlistEventMapWindowIndices(); if (range.end >= range.max) this.playlistEventMapSetWindowEndIndex(range.length - 1, { update: true, pause: false }); if (this.playlistEventMapSelectedKind === "canonical") this.playlistEventMapClearSelection({ update: false }); this.playlistEventMapPlaying = true; this.playlistEventMapUpdateLayers(); this.playlistEventMapPlayTimer = setInterval(() => { if (!this.playlistEventMapShiftWindow(1, { playback: true })) this.playlistEventMapStopPlayback(); }, MAP_PLAY_INTERVAL_MS);
    },
    playlistEventMapCanPlay() { const range = this.playlistEventMapWindowIndices(); const reduced = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches; return Boolean(!reduced && this.playlistEventMapScene && range.max >= range.length && range.length > 0); },
    playlistEventMapPlaybackLabel() { const range = this.playlistEventMapWindowIndices(); return range.end >= range.max && !this.playlistEventMapPlaying ? "从头播放" : this.playlistEventMapPlaying ? "暂停播放" : "播放下一月"; },

    playlistEventMapTypeOptions() { return Array.isArray(this.playlistEventMapManifest?.type_categories) ? this.playlistEventMapManifest.type_categories : []; },
    playlistEventMapHandleTypeFilterChange({ refreshList = true } = {}) { this.playlistEventMapStopPlayback({ refresh: false, loadEntities: false }); this.playlistEventMapUpdateLayers(); this.playlistEventMapRefreshTopicDetail(); this.playlistEventMapScheduleEntities(); if (refreshList && this.fieldLinearView) void this.fieldLoadLinearItems?.(); },
    playlistEventMapToggleFilters() {
      const open = !this.playlistEventMapFiltersOpen;
      this.playlistEventMapFiltersOpen = open;
      if (!open) return;
      this.playlistEventMapCloseSearch();
      if (this.activeView === "field") this.fieldObservationRailOpen = false;
    },
    playlistEventMapResetCamera() { const controller = this.playlistEventMapController(); if (!controller?.fitActiveWindow()) controller?.resetCamera(); },
    playlistEventMapLevelInfo() { return EVENT_MAP_LEVELS[String(this.playlistEventMapViewportSummary?.sceneLevel || "overview")] || EVENT_MAP_LEVELS.overview; },
    playlistEventMapObjectGuide() { return EVENT_MAP_OBJECT_GUIDE; },
    playlistEventMapSemanticLegend() { return Array.isArray(this.playlistEventMapManifest?.semantic_families) ? this.playlistEventMapManifest.semantic_families : []; },
    playlistEventMapSummaryCards() { const manifest = this.playlistEventMapManifest || {}; return [{ label: "全期真实事件", value: Number(manifest.canonical_count || 0) }, { label: "当前窗口", value: Number(this.playlistEventMapVisibleCount || 0) }, { label: "窗口长度（月）", value: Number(this.playlistEventMapWindowMonths || 0) }, { label: "本视野", value: Number(this.playlistEventMapViewportSummary?.viewportCanonicalCount || 0) }]; },
    playlistEventMapActiveFilterCount() { return Number(Boolean(this.playlistEventMapEntityFilter)) + Number(Boolean(this.playlistEventMapTypeFilter)); },
    playlistEventMapClearAllFilters() { const refreshList = Boolean(this.fieldLinearView); this.playlistEventMapClearEntityFilter({ refreshList: false }); if (this.playlistEventMapTypeFilter) { this.playlistEventMapTypeFilter = ""; this.playlistEventMapHandleTypeFilterChange({ refreshList: false }); } if (refreshList) void this.fieldLoadLinearItems?.(); },
    playlistEventMapLegendItems() { const items = this.playlistEventMapViewportSummary?.sceneLevel === "event" ? ["金色＝选中事件", "绿色＝实体命中"] : ["点密度＝事件聚集程度", "颜色＝事件语义族"]; if (this.playlistEventMapTopicFocus) items.push("主题选中＝加亮与细金边"); return items; },
    playlistEventMapMetaLabel() { const manifest = this.playlistEventMapManifest || {}; if (!Number(manifest.canonical_count || 0)) return "暂无可进入地图的真实事件"; return `${this.formatInteger(manifest.canonical_count)} 个真实事件 · ${this.formatInteger(manifest.record_count || 0)} 条记录 · 当前窗口 ${this.formatInteger(this.playlistEventMapVisibleCount || 0)}`; },
    playlistEventMapEmptyLabel() { const manifest = this.playlistEventMapManifest || {}; if (this.playlistEventMapError) return this.playlistEventMapError; if (this.playlistEventMapLoading) return "事件语义星域加载中…"; if (["pending", "running"].includes(String(manifest.build_status || manifest.status || ""))) return "首个事件语义星域快照正在构建。"; if (manifest.build_status === "failed" || manifest.status === "failed") return manifest.build_error || manifest.last_error || "事件语义星域构建失败。"; if (!manifest.snapshot_id) return "尚未构建事件语义星域快照。"; if (!Number(manifest.canonical_count || 0)) return "没有同时具备事件发生时间和语义向量的已确认事件。"; return this.playlistEventMapScene && !this.playlistEventMapVisibleCount ? "当前时间窗口没有可显示的真实事件。" : ""; },
    playlistEventMapStatusBadgeLabel() {
      const manifest = this.playlistEventMapStatus || this.playlistEventMapManifest || {};
      if (this.playlistEventMapLoading && !manifest.snapshot_id) return "星域加载中";
      if (manifest.building || ["pending", "running"].includes(String(manifest.build_status || ""))) {
        return manifest.snapshot_id ? "星域后台更新中" : "首次生成中";
      }
      if (manifest.build_status === "failed") return manifest.snapshot_id ? "星域更新失败" : "生成失败";
      if (manifest.status === "failed") return "生成失败";
      if (manifest.dirty || manifest.state?.dirty) return manifest.snapshot_id ? "有新事件待更新" : "尚未生成星域";
      return manifest.snapshot_id ? "星域已同步" : "尚未生成星域";
    },
    playlistEventMapStatusBadgeHint() {
      const manifest = this.playlistEventMapStatus || this.playlistEventMapManifest || {};
      if (this.playlistEventMapLoading && !manifest.snapshot_id) return "正在下载首个可交互的星域快照。";
      if (manifest.building || ["pending", "running"].includes(String(manifest.build_status || ""))) {
        return manifest.snapshot_id
          ? "当前星域可以正常浏览；后台正在把新增或重新分析的事件生成到下一版快照。"
          : "后台正在生成首个可浏览的星域快照。";
      }
      if (manifest.build_status === "failed" || manifest.status === "failed") {
        return manifest.snapshot_id ? "当前快照仍可浏览，但最近一次后台更新失败。" : "首个星域快照生成失败。";
      }
      if (manifest.dirty || manifest.state?.dirty) {
        return manifest.snapshot_id ? "当前星域可以正常浏览；已有新事件等待进入下一版快照。" : "已有事件，但尚未生成首个星域快照。";
      }
      return manifest.snapshot_id ? "当前星域快照已同步，可以正常浏览。" : "还没有可浏览的星域快照。";
    },
    playlistEventMapStatusBadgeClass() { const label = this.playlistEventMapStatusBadgeLabel(); return label.includes("失败") ? "border-rose-500/30 bg-rose-500/10 text-rose-200" : label.includes("中") ? "border-cyan-500/30 bg-cyan-500/10 text-cyan-200" : label.includes("待") || label.includes("未") ? "border-amber-500/30 bg-amber-500/10 text-amber-200" : "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"; },
    playlistEventMapCoverageLabel() { const manifest = this.playlistEventMapStatus || {}; return this.formatInteger(Number(manifest.event_embedded ?? manifest.embedding_ready_count ?? 0)); },
    playlistEventMapSkippedLabel() { const manifest = this.playlistEventMapStatus || {}; return this.formatInteger(Number(manifest.event_skipped ?? manifest.skipped_count ?? 0)); },
    playlistEventMapFailedLabel() { const manifest = this.playlistEventMapStatus || {}; return this.formatInteger(Number(manifest.event_failed ?? manifest.failed_count ?? 0)); },
    playlistEventMapActiveBackfillJob() { const job = this.playlistEventMapBackfillJob || statusJob(this.playlistEventMapStatus || {}); return job && ["pending", "running", "cancel_requested"].includes(String(job.status || "")) ? job : null; },
    playlistEventMapBackfillJobText() { const job = this.playlistEventMapActiveBackfillJob(); if (!job) return ""; const done = Number(job.completed_items || job.progress_current || 0); const total = Number(job.total_items || job.progress_total || 0); return total ? `事件处理 ${this.formatInteger(done)}/${this.formatInteger(total)}` : "事件处理任务正在执行"; },
    playlistEventMapPollIsActive(manifest = this.playlistEventMapStatus || {}) {
      const buildStatus = String(manifest?.build_status || "");
      const status = String(manifest?.status || "");
      const backfill = statusJob(manifest) || this.playlistEventMapBackfillJob;
      return Boolean(
        manifest?.building ||
        ["pending", "running"].includes(buildStatus) ||
        ["pending", "running"].includes(status) ||
        (backfill && ["pending", "running", "cancel_requested"].includes(String(backfill.status || "")))
      );
    },
    playlistEventMapObserveVisibility() {
      if (typeof document === "undefined" || this._playlistEventMapVisibilityHandler) return;
      this._playlistEventMapVisibilityHandler = () => {
        if (documentIsHidden()) {
          this.playlistEventMapPausePolling();
          this.playlistEventMapStopLoad();
          return;
        }
        const shouldResume = this.playlistSubview === "analysis" ||
          (this.playlistSubview === "settings" && this.playlistEventMapPollIsActive());
        const canResume = this.activeView === "playlist" ||
          (this.activeView === "field" && !this.fieldRequestedSnapshotId);
        if (canResume && shouldResume) this.playlistEventMapSchedulePoll({ immediate: true });
      };
      document.addEventListener("visibilitychange", this._playlistEventMapVisibilityHandler);
    },
    playlistEventMapSchedulePoll({ immediate = false } = {}) {
      this.playlistEventMapPausePolling();
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const subview = String(this.playlistSubview || "");
      const isField = this.activeView === "field";
      const isPlaylist = this.activeView === "playlist";
      const historicalPinned = isField && Boolean(this.fieldRequestedSnapshotId);
      const shouldPoll = !historicalPinned && Boolean(pid) && (
        (isField && subview === "analysis")
        || (isPlaylist && (subview === "analysis" || (subview === "settings" && this.playlistEventMapPollIsActive())))
      );
      if (!shouldPoll) {
        this.playlistEventMapStopPolling();
        return;
      }
      this.playlistEventMapObserveVisibility();
      if (documentIsHidden()) return;
      const generation = Number(this._playlistEventMapPollGeneration || 0);
      const stillCurrent = () => (
        Number(this._playlistEventMapPollGeneration || 0) === generation &&
        ["field", "playlist"].includes(this.activeView) &&
        String(this.playlistPageId || this.selectedPlaylistId || "").trim() === pid &&
        String(this.playlistSubview || "") === subview &&
        !(this.activeView === "field" && this.fieldRequestedSnapshotId) &&
        !documentIsHidden()
      );
      const poll = async () => {
        if (!stillCurrent()) return;
        this.playlistEventMapPollTimer = null;
        const controller = new AbortController();
        this._playlistEventMapPollAbortCtrl = controller;
        const manifest = await this.playlistEventMapLoadManifest({ silent: true, compact: true, signal: controller.signal });
        if (this._playlistEventMapPollAbortCtrl === controller) this._playlistEventMapPollAbortCtrl = null;
        if (!stillCurrent()) return;
        const snapshotChanged = String(manifest?.snapshot_id || "") !== String(this.playlistEventMapSnapshotId || "");
        const snapshotMissingLocally = Boolean(manifest?.snapshot_id) &&
          (!this.playlistEventMapScene || !this.playlistEventMapController());
        const snapshotIncomplete = Boolean(manifest?.snapshot_id)
          && String(manifest.snapshot_id) === String(this.playlistEventMapSnapshotId || "")
          && Boolean(this.playlistEventMapController())
          && this._playlistEventMapSceneComplete === false
          && !this._playlistEventMapSceneLoadingSnapshotId;
        const snapshotMetadataIncomplete = Boolean(manifest?.snapshot_id)
          && String(manifest.snapshot_id) === String(this.playlistEventMapSnapshotId || "")
          && Boolean(this.playlistEventMapController())
          && this._playlistEventMapMetadataComplete === false
          && !(
            this._playlistEventMapMetadataPromise
            && String(this._playlistEventMapMetadataSnapshotId || "") === String(manifest.snapshot_id)
          );
        if (subview === "analysis" && manifest && (snapshotChanged || snapshotMissingLocally || snapshotIncomplete || snapshotMetadataIncomplete)) {
          await this.playlistEventMapLoadView({ silent: true, schedulePolling: false });
          if (!stillCurrent()) return;
        }
        const highlightRefreshDue = Date.now() - Number(this._playlistEventMapHighlightsLastRequestAt || 0)
          >= EVENT_MAP_HIGHLIGHT_REFRESH_INTERVAL_MS;
        if (
          isField && subview === "analysis" && this._playlistEventMapSceneComplete && highlightRefreshDue
          && !snapshotChanged && !snapshotMissingLocally && !snapshotIncomplete && !snapshotMetadataIncomplete
        ) {
          this.playlistEventMapScheduleHighlights({ delay: 0, force: true });
        }
        const active = this.playlistEventMapPollIsActive(manifest || this.playlistEventMapStatus || {});
        if (subview === "settings" && !active) {
          this.playlistEventMapStopPolling();
          return;
        }
        const delay = manifest && active ? EVENT_MAP_ACTIVE_POLL_INTERVAL_MS : EVENT_MAP_IDLE_POLL_INTERVAL_MS;
        this.playlistEventMapPollTimer = setTimeout(poll, delay);
      };
      const initialDelay = immediate
        ? 0
        : this.playlistEventMapPollIsActive()
          ? EVENT_MAP_ACTIVE_POLL_INTERVAL_MS
          : EVENT_MAP_IDLE_POLL_INTERVAL_MS;
      this.playlistEventMapPollTimer = setTimeout(poll, initialDelay);
    },
    async playlistEventMapRebuild() { const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); if (!pid || this.playlistEventMapRebuilding) return; this.playlistEventMapRebuilding = true; try { const result = await this.api(`/playlists/${encodeURIComponent(pid)}/events/map/rebuild`, { method: "POST" }); this.globalStatus = result?.created ? "已提交事件语义星域构建" : "已复用排队中的事件语义星域构建"; await this.playlistEventMapLoadManifest({ silent: true }); this.playlistEventMapSchedulePoll(); } catch (error) { this.playlistEventMapError = error?.message || String(error); this.globalStatus = `error: ${this.playlistEventMapError}`; } finally { this.playlistEventMapRebuilding = false; } },
    async playlistEventMapCancelBackfill() { const id = String(this.playlistEventMapActiveBackfillJob()?.job_id || ""); if (!id || this.playlistEventMapBackfillCanceling) return; this.playlistEventMapBackfillCanceling = true; try { await this.api(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }); this.globalStatus = "已请求停止事件处理任务"; await this.playlistEventMapLoadManifest({ silent: true }); this.playlistEventMapSchedulePoll(); } catch (error) { this.playlistEventMapError = error?.message || String(error); this.globalStatus = `error: ${this.playlistEventMapError}`; } finally { this.playlistEventMapBackfillCanceling = false; } },
  };
}
