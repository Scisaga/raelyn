import {
  EventMapController,
  countEventMapVisiblePoints,
  parseEventMapIndices,
  parseEventMapScene,
} from "./event-map.js";

const MAP_PLAY_INTERVAL_MS = 1000;
const EVENT_MAP_ACTIVE_POLL_INTERVAL_MS = 1800;
const EVENT_MAP_IDLE_POLL_INTERVAL_MS = 10000;

const EVENT_MAP_LEVELS = {
  overview: { label: "语义星域", hint: "点的疏密表示当前观察窗口的事件聚集程度，颜色表示事件语义；拖动旋转，滚轮缩放。" },
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
function niceMonthStep(minimum) {
  const value = Math.max(1, Math.ceil(Number(minimum || 1)));
  return [1, 2, 3, 6, 12, 24, 36, 60, 120, 240, 600, 1200].find((item) => item >= value) || Math.ceil(value / 1200) * 1200;
}
function primaryPointer(event) { return event && event.isPrimary !== false && (event.pointerType !== "mouse" || Number(event.button || 0) === 0); }
function abortError(error) { return error && (error.name === "AbortError" || String(error.message || "").includes("aborted")); }
function statusJob(manifest) { return manifest?.backfill_job || null; }
function documentIsHidden() {
  return typeof document !== "undefined" && (document.hidden === true || document.visibilityState === "hidden");
}
function rawEventMapController(controller) {
  const unwrap = globalThis.Alpine?.raw;
  return typeof unwrap === "function" ? unwrap(controller) : controller;
}
function normalizeTimeline(manifest) {
  const source = manifest?.monthly_distribution || manifest?.timeline || manifest?.months || [];
  return Array.isArray(source) ? source.map((item) => ({ month: isoMonth(item.month || item.period_date || item.date), canonical_count: Number(item.canonical_count ?? item.event_count ?? item.count ?? 0), record_count: Number(item.record_count ?? item.event_count ?? item.count ?? 0) })).filter((item) => item.month).sort((left, right) => left.month.localeCompare(right.month)) : [];
}

export function createPlaylistEventMapMethods() {
  return {
    playlistEventMapController() { return rawEventMapController(this._playlistEventMapController); },
    playlistEventMapStopLoad() {
      this._playlistEventMapLifecycleGeneration = Number(this._playlistEventMapLifecycleGeneration || 0) + 1;
      this._playlistEventMapRequestToken = Number(this._playlistEventMapRequestToken || 0) + 1;
      this._playlistEventMapDetailToken = Number(this._playlistEventMapDetailToken || 0) + 1;
      this._playlistEventMapTopicDetailToken = Number(this._playlistEventMapTopicDetailToken || 0) + 1;
      this._playlistEventMapSearchToken = Number(this._playlistEventMapSearchToken || 0) + 1;
      this._playlistEventMapEntityIndexToken = Number(this._playlistEventMapEntityIndexToken || 0) + 1;
      this._abortCtrl("_playlistEventMapAbortCtrl");
      this._abortCtrl("_playlistEventMapEntityAbortCtrl");
      this._abortCtrl("_playlistEventMapEntityIndexAbortCtrl");
      this._abortCtrl("_playlistEventMapSearchAbortCtrl");
      this._abortCtrl("_playlistEventMapDetailAbortCtrl");
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
        playlistEventMapViewportSummary: { sceneLevel: "overview", viewportCanonicalCount: 0, renderedTopicLabelCount: 0, ready: false }, playlistEventMapObjectGuideOpen: false, playlistEventMapFiltersOpen: false, playlistEventMapCameraMode: "perspective", playlistEventMapWindowStart: "", playlistEventMapWindowEnd: "", playlistEventMapTimelineScope: "normal", playlistEventMapTimelineMonths: [], playlistEventMapWindowMonths: 12, playlistEventMapWindowPreviewStartIndex: null, playlistEventMapWindowPreviewEndIndex: null, playlistEventMapTimelineWindowDragging: false, playlistEventMapTimelineWindowPointerId: null, playlistEventMapTimelineTrackWidth: 0, playlistEventMapTypeFilter: "", playlistEventMapSearchQuery: "", playlistEventMapSearchResults: [], playlistEventMapSearchLoading: false, playlistEventMapSearchActiveIndex: -1,
      });
      this._playlistEventMapTimelineItemsCache = null; this._playlistEventMapTimelineMaxCache = null;
      this._playlistEventMapPlaylistId = "";
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
    playlistEventMapResetSnapshotPinnedState(stableEntityFilter = null) {
      this._playlistEventMapEntityIndexToken = Number(this._playlistEventMapEntityIndexToken || 0) + 1;
      this._abortCtrl("_playlistEventMapEntityAbortCtrl"); this._abortCtrl("_playlistEventMapEntityIndexAbortCtrl");
      this._playlistEventMapEntityInFlight = false; this._playlistEventMapEntityPending = false;
      this.playlistEventMapEntities = []; this.playlistEventMapEntityIndices = new Set(); this.playlistEventMapEntityFilter = stableEntityFilter;
      this.playlistEventMapClearSearch(); this.playlistEventMapClearSelection({ update: false });
      this.playlistEventMapTopicFocus = null; this.playlistEventMapTopicDetail = null; this.playlistEventMapTopicDetailLoading = false;
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
    async playlistEventMapLoadManifest({ silent = false, compact = false, signal = null } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); if (!pid) return null;
      const lifecycleGeneration = Number(this._playlistEventMapLifecycleGeneration || 0);
      if (!silent) this.playlistEventMapStatusLoading = true;
      try {
        const params = new URLSearchParams();
        if (compact) params.set("compact", "true");
        if (this.activeView === "field" && this.fieldRequestedSnapshotId) {
          params.set("snapshot_id", String(this.fieldRequestedSnapshotId));
        }
        const manifest = await this.api(
          `/playlists/${encodeURIComponent(pid)}/events/map/manifest${params.size ? `?${params}` : ""}`,
          { signal, cache: "no-store" }
        );
        const currentPid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
        if (signal?.aborted || currentPid !== pid || Number(this._playlistEventMapLifecycleGeneration || 0) !== lifecycleGeneration) return null;
        this.playlistEventMapStatus = compact ? { ...(this.playlistEventMapStatus || {}), ...(manifest || {}) } : manifest || null;
        this.playlistEventMapStatusError = "";
        if (manifest && Object.prototype.hasOwnProperty.call(manifest, "backfill_job")) this.playlistEventMapBackfillJob = statusJob(manifest);
        return manifest || null;
      } catch (error) {
        if (!abortError(error) && Number(this._playlistEventMapLifecycleGeneration || 0) === lifecycleGeneration) {
          this.playlistEventMapStatusError = error?.message || String(error);
        }
        if (!silent && !abortError(error)) throw error;
        return null;
      }
      finally {
        if (!silent && Number(this._playlistEventMapLifecycleGeneration || 0) === lifecycleGeneration) this.playlistEventMapStatusLoading = false;
      }
    },

    playlistEventMapInitializeWindow() {
      const bounds = this.playlistEventMapTimelineBounds(); const items = this.playlistEventMapTimelineItems();
      if (!bounds.start || !bounds.end || !items.length) return;
      const currentStart = String(this.playlistEventMapWindowStart || "").slice(0, 10); const currentEnd = String(this.playlistEventMapWindowEnd || "").slice(0, 10);
      if (currentStart && currentEnd && currentStart <= currentEnd && currentStart >= bounds.start && currentEnd <= bounds.end) return;
      const populated = items.filter((item) => Number(item.record_count || 0) > 0);
      const anchor = populated.length ? populated.at(-1).index : items.length - 1;
      this.playlistEventMapSetWindowEndIndex(anchor, { update: false, pause: false });
    },

    async playlistEventMapLoadView({ silent = false, schedulePolling = true } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); if (!pid) return;
      if (this._playlistEventMapPlaylistId && String(this._playlistEventMapPlaylistId) !== pid) this.playlistEventMapReset();
      this._playlistEventMapPlaylistId = pid;
      this.playlistEventMapObserveVisibility();
      if (documentIsHidden()) { this.playlistEventMapStopLoad(); return; }
      this.playlistEventMapObserveTimelineTrack(); const token = Number(this._playlistEventMapRequestToken || 0) + 1; this._playlistEventMapRequestToken = token; this._abortCtrl("_playlistEventMapAbortCtrl"); const requestController = new AbortController(); this._playlistEventMapAbortCtrl = requestController;
      if (!silent) this.playlistEventMapLoading = true; this.playlistEventMapError = "";
      let pendingMount = null; let pendingController = null;
      try {
        const manifest = await this.playlistEventMapLoadManifest({ silent: true, signal: requestController.signal }); if (Number(this._playlistEventMapRequestToken || 0) !== token) return;
        if (!manifest) return;
        const snapshotId = String(manifest?.snapshot_id || "");
        if (!snapshotId || manifest?.status !== "ready") { this.playlistEventMapDestroy(); this.playlistEventMapManifest = manifest || null; this.playlistEventMapTimelineMonths = normalizeTimeline(manifest); this.playlistEventMapScene = null; this.playlistEventMapSnapshotId = ""; return; }
        if (snapshotId === this.playlistEventMapSnapshotId && this.playlistEventMapScene && this.playlistEventMapController()) { this.playlistEventMapManifest = manifest; this.playlistEventMapTimelineMonths = normalizeTimeline(manifest); this.playlistEventMapInitializeWindow(); this.playlistEventMapUpdateLayers(); return; }
        const buffer = await this.playlistEventMapFetchBinary(
          `/playlists/${encodeURIComponent(pid)}/events/map/scene?${new URLSearchParams({ snapshot_id: snapshotId })}`,
          { signal: requestController.signal, cache: "no-store" }
        );
        if (Number(this._playlistEventMapRequestToken || 0) !== token) return;
        if (Number(manifest.dimension || 0) !== 3 || Number(manifest.scene_record_size || 0) !== 56) throw new Error("事件语义星域快照不是三维 v2 协议，请先完成星域重建");
        const scene = parseEventMapScene(buffer, Number(manifest.canonical_count || 0)); const target = this.$refs?.playlistEventMap; if (!target) throw new Error("事件语义星域容器尚未就绪");
        pendingMount = document.createElement("div"); pendingMount.style.cssText = "position:absolute;inset:0;width:100%;height:100%;visibility:hidden"; target.appendChild(pendingMount);
        pendingController = await EventMapController.create({ target: pendingMount, scene, manifest, onSelect: (index, canonicalId) => this.playlistEventMapSelectCanonical(index, canonicalId), onTopic: (_index, topic) => this.playlistEventMapSelectTopic(topic), onViewport: (summary) => this.playlistEventMapHandleViewportSummary(summary) });
        if (Number(this._playlistEventMapRequestToken || 0) !== token) { pendingController.destroy(); pendingMount.remove(); return; }
        const stableEntityFilter = this.playlistEventMapStableEntityFilter();
        const previousController = this.playlistEventMapController(); const previousMount = this._playlistEventMapMount; this._playlistEventMapController = pendingController; this._playlistEventMapMount = pendingMount; pendingController = null; pendingMount = null;
        this.playlistEventMapManifest = manifest; this.playlistEventMapTimelineMonths = normalizeTimeline(manifest); this.playlistEventMapScene = scene; this.playlistEventMapSnapshotId = snapshotId;
        this.playlistEventMapResetSnapshotPinnedState(stableEntityFilter);
        this.playlistEventMapInitializeWindow(); previousController?.destroy(); previousMount?.remove(); if (!this._playlistEventMapMount.isConnected) target.appendChild(this._playlistEventMapMount); this._playlistEventMapMount.style.visibility = "visible"; this.playlistEventMapController().setCameraMode(this.playlistEventMapCameraMode); this.playlistEventMapUpdateLayers();
        await this.playlistEventMapLoadEntities({ silent: true });
        if (stableEntityFilter && Number(this._playlistEventMapRequestToken || 0) === token) await this.playlistEventMapApplyEntityFilter(stableEntityFilter, { resume: true });
      } catch (error) { pendingController?.destroy(); pendingMount?.remove(); if (!abortError(error) && Number(this._playlistEventMapRequestToken || 0) === token) this.playlistEventMapError = error?.message || String(error); }
      finally {
        if (Number(this._playlistEventMapRequestToken || 0) === token) {
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
      if (!resume) this.playlistEventMapStopPlayback(); this.playlistEventMapEntityFilter = entity; this.fieldRequestedEntity = entity; if (this.activeView === "field") this._syncUrl({ push: false });
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
    playlistEventMapClearEntityFilter() { this._playlistEventMapEntityIndexToken = Number(this._playlistEventMapEntityIndexToken || 0) + 1; this._abortCtrl("_playlistEventMapEntityIndexAbortCtrl"); this.playlistEventMapEntityFilter = null; this.fieldRequestedEntity = null; this.playlistEventMapEntityIndices = new Set(); this.playlistEventMapUpdateLayers(); if (this.activeView === "field") this._syncUrl({ push: false }); },
    playlistEventMapFitIndices(indices) {
      const scene = this.playlistEventMapScene; if (!scene || !indices) return; let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity; let minZ = Infinity; let maxZ = -Infinity;
      const controller = this.playlistEventMapController();
      for (const raw of indices) { const index = Number(raw); if (!controller?.isActiveIndex(index)) continue; minX = Math.min(minX, scene.x[index]); maxX = Math.max(maxX, scene.x[index]); minY = Math.min(minY, scene.y[index]); maxY = Math.max(maxY, scene.y[index]); minZ = Math.min(minZ, scene.z[index]); maxZ = Math.max(maxZ, scene.z[index]); }
      controller?.fitBounds({ minX, maxX, minY, maxY, minZ, maxZ });
    },

    async playlistEventMapSelectCanonical(index, canonicalId) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); const snapshotId = String(this.playlistEventMapSnapshotId || "").trim(); const pointIndex = Number(index); const id = String(canonicalId || "").trim();
      const controller = this.playlistEventMapController(); if (!pid || !snapshotId || !id || !Number.isInteger(pointIndex) || !controller?.isActiveIndex(pointIndex)) return;
      this.fieldSelectedChange = null; this.playlistEventMapStopPlayback(); const token = Number(this._playlistEventMapDetailToken || 0) + 1; this._playlistEventMapDetailToken = token; this.playlistEventMapSelectedIndex = pointIndex; this.playlistEventMapSelectedKind = "canonical"; this.playlistEventMapSelectedId = id; this.fieldRequestedCanonicalId = id; this.fieldRequestedTopicId = ""; this.fieldRequestedEvidenceId = ""; this.fieldEvidenceDetail = null; this.playlistEventMapSelectedDetail = null; this.playlistEventMapDetailLoading = true; this.playlistEventMapDetailTab = "overview"; controller.setSelection(pointIndex, null); this.playlistEventMapUpdateLayers();
      if (this.activeView === "field") this._syncUrl({ push: false });
      this._abortCtrl("_playlistEventMapDetailAbortCtrl"); const requestController = new AbortController(); this._playlistEventMapDetailAbortCtrl = requestController;
      try {
        const detail = await this.api(
          `/playlists/${encodeURIComponent(pid)}/events/map/canonical/${encodeURIComponent(id)}?${new URLSearchParams({ snapshot_id: snapshotId })}`,
          { signal: requestController.signal, cache: "no-store" }
        );
        if (requestController.signal.aborted || Number(this._playlistEventMapDetailToken || 0) !== token || String(this.playlistEventMapSnapshotId || "") !== snapshotId) return;
        const references = await this.api(
          `/domains/${encodeURIComponent(pid)}/objects/canonical/${encodeURIComponent(id)}/brief-references`,
          { signal: requestController.signal, cache: "no-store" }
        );
        if (requestController.signal.aborted || Number(this._playlistEventMapDetailToken || 0) !== token || String(this.playlistEventMapSnapshotId || "") !== snapshotId) return;
        this.playlistEventMapSelectedDetail = detail ? { ...detail, brief_references: references?.items || [] } : null; this.playlistEventMapApplyDetailOverlay();
      }
      catch (error) { if (!abortError(error) && Number(this._playlistEventMapDetailToken || 0) === token) this.playlistEventMapError = error?.message || String(error); }
      finally {
        if (this._playlistEventMapDetailAbortCtrl === requestController) this._playlistEventMapDetailAbortCtrl = null;
        if (Number(this._playlistEventMapDetailToken || 0) === token) this.playlistEventMapDetailLoading = false;
      }
    },
    playlistEventMapClearSelection({ update = true } = {}) {
      this._playlistEventMapDetailToken = Number(this._playlistEventMapDetailToken || 0) + 1; this._abortCtrl("_playlistEventMapDetailAbortCtrl"); if (this.playlistEventMapSelectedKind === "topic") this.playlistEventMapTopicFocus = null;
      this.playlistEventMapSelectedIndex = null; this.playlistEventMapSelectedKind = ""; this.playlistEventMapSelectedId = ""; this.fieldRequestedCanonicalId = ""; this.fieldSelectedChange = null; this.playlistEventMapSelectedDetail = null; this.playlistEventMapDetailLoading = false; this.playlistEventMapDetailTab = "overview"; this.playlistEventMapController()?.setSelection(null, null); if (update) this.playlistEventMapUpdateLayers();
      if (this.activeView === "field") this._syncUrl({ push: false });
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
    playlistEventMapCanonicalBreadcrumb() {
      const detail = this.playlistEventMapSelectedDetail || {};
      const topicId = String(detail?.topic?.topic_id || "");
      const topic = topicId
        ? (Array.isArray(this.playlistEventMapManifest?.topics) ? this.playlistEventMapManifest.topics : []).find((item) => String(item?.topic_id || "") === topicId)
        : null;
      const parent = topic ? this.playlistEventMapTopicParent(topic) : null;
      return [parent, topic, detail?.title ? { label: detail.title, canonical: true } : null].filter(Boolean);
    },
    async playlistEventMapLoadTopicDetail(topic = this.playlistEventMapSelectedDetail) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim(); const snapshotId = String(this.playlistEventMapSnapshotId || "").trim(); const topicId = String(topic?.topic_id || "").trim();
      if (!pid || !snapshotId || !topicId) return null;
      const token = Number(this._playlistEventMapTopicDetailToken || 0) + 1; this._playlistEventMapTopicDetailToken = token; this.playlistEventMapTopicDetailLoading = true;
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
        if (requestController.signal.aborted || Number(this._playlistEventMapTopicDetailToken || 0) !== token || String(this.playlistEventMapSnapshotId || "") !== snapshotId || this.playlistEventMapSelectedKind !== "topic" || String(this.playlistEventMapSelectedId || "") !== topicId) return null;
        this.playlistEventMapTopicDetail = detail ? { ...detail, brief_references: briefReferences?.items || [] } : null;
        return this.playlistEventMapTopicDetail;
      } catch (error) {
        if (!abortError(error) && Number(this._playlistEventMapTopicDetailToken || 0) === token) this.playlistEventMapError = error?.message || String(error);
        return null;
      } finally {
        if (this._playlistEventMapTopicDetailAbortCtrl === requestController) this._playlistEventMapTopicDetailAbortCtrl = null;
        if (Number(this._playlistEventMapTopicDetailToken || 0) === token) this.playlistEventMapTopicDetailLoading = false;
      }
    },
    playlistEventMapSelectTopicRepresentative(item) {
      if (!Number.isInteger(Number(item?.point_index))) return;
      this.playlistEventMapSelectCanonical(Number(item.point_index), item.canonical_id);
    },
    playlistEventMapFocusSelectedCanonical() {
      if (this.playlistEventMapSelectedKind !== "canonical") return;
      this.playlistEventMapController()?.focusPoint(Number(this.playlistEventMapSelectedIndex));
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
    playlistEventMapSelectSearchResult(item) { if (item?.kind === "canonical" && Number.isInteger(Number(item.point_index))) { this.playlistEventMapController()?.focusPoint(Number(item.point_index)); this.playlistEventMapSelectCanonical(Number(item.point_index), item.id || item.canonical_id); } else if (item?.kind === "topic") this.playlistEventMapSelectTopic(item, { focus: true }); else if (item?.kind === "entity") this.playlistEventMapApplyEntityFilter(item); this.playlistEventMapClearSearch(); },
    playlistEventMapSearchGroups() { const indexed = (this.playlistEventMapSearchResults || []).map((item, index) => ({ ...item, _eventMapSearchIndex: index })); return [{ key: "canonical", label: "真实事件" }, { key: "topic", label: "主题" }, { key: "entity", label: "实体" }].map((group) => ({ ...group, items: indexed.filter((item) => item.kind === group.key) })).filter((group) => group.items.length); },
    playlistEventMapSearchKindLabel(kind) { return { canonical: "真实事件", topic: "主题", entity: "实体" }[String(kind || "")] || "对象"; },
    playlistEventMapSearchKeydown(event) { const key = String(event?.key || ""); const count = (this.playlistEventMapSearchResults || []).length; if (key === "Escape") { this.playlistEventMapClearSearch(); event?.preventDefault?.(); return; } if (!["ArrowDown", "ArrowUp", "Enter"].includes(key) || !count) return; if (key === "Enter") this.playlistEventMapSelectSearchResult(this.playlistEventMapSearchResults[Math.max(0, Math.min(count - 1, Number(this.playlistEventMapSearchActiveIndex || 0)))]); else { const delta = key === "ArrowDown" ? 1 : -1; this.playlistEventMapSearchActiveIndex = (Number(this.playlistEventMapSearchActiveIndex || 0) + delta + count) % count; } event?.preventDefault?.(); },
    playlistEventMapClearSearch() { clearTimeout(this._playlistEventMapSearchTimer); this._playlistEventMapSearchTimer = null; this._playlistEventMapSearchToken = Number(this._playlistEventMapSearchToken || 0) + 1; this._abortCtrl("_playlistEventMapSearchAbortCtrl"); this.playlistEventMapSearchQuery = ""; this.playlistEventMapSearchResults = []; this.playlistEventMapSearchLoading = false; this.playlistEventMapSearchActiveIndex = -1; },

    playlistEventMapSelectTopic(item, { focus = false } = {}) {
      const controller = this.playlistEventMapController(); const topics = Array.isArray(this.playlistEventMapManifest?.topics) ? this.playlistEventMapManifest.topics : []; const id = String(item?.id || item?.topic_id || ""); const index = Number.isInteger(Number(item?.topic_index)) ? Number(item.topic_index) : Number(topics.find((topic) => String(topic.topic_id || "") === id)?.topic_index); if (!Number.isInteger(index) || index < 0 || !controller?.hasActiveTopic(index)) return;
      if (!focus && this.playlistEventMapSelectedKind === "topic" && Number(this.playlistEventMapTopicFocus?.topic_index) === index) { this.playlistEventMapClearTopicFocus(); return; }
      const topic = { ...(topics.find((entry, fallback) => Number(entry?.topic_index ?? fallback) === index) || {}), ...item, topic_index: index }; this.fieldSelectedChange = null; this.playlistEventMapStopPlayback(); this.playlistEventMapTopicFocus = topic; this.playlistEventMapSelectedIndex = null; this.playlistEventMapSelectedKind = "topic"; this.playlistEventMapSelectedId = String(topic.topic_id || id); this.fieldRequestedCanonicalId = ""; this.fieldRequestedTopicId = this.playlistEventMapSelectedId; this.fieldRequestedEvidenceId = ""; this.fieldEvidenceDetail = null; this.playlistEventMapSelectedDetail = topic; this.playlistEventMapTopicDetail = null; this.playlistEventMapTopicDetailLoading = true; this.playlistEventMapDetailLoading = false; controller.setSelection(null, null); this.playlistEventMapUpdateLayers(); if (focus) controller.focusTopic(index); void this.playlistEventMapLoadTopicDetail(topic);
      if (this.activeView === "field") this._syncUrl({ push: false });
    },
    playlistEventMapClearTopicFocus({ update = true } = {}) { const wasSelected = this.playlistEventMapSelectedKind === "topic"; this._playlistEventMapTopicDetailToken = Number(this._playlistEventMapTopicDetailToken || 0) + 1; this._abortCtrl("_playlistEventMapTopicDetailAbortCtrl"); this.playlistEventMapTopicFocus = null; this.playlistEventMapTopicDetail = null; this.playlistEventMapTopicDetailLoading = false; this.fieldRequestedTopicId = ""; this.playlistEventMapController()?.clearTopicFocus(); if (wasSelected) { this.playlistEventMapSelectedKind = ""; this.playlistEventMapSelectedId = ""; this.playlistEventMapSelectedDetail = null; } if (update) this.playlistEventMapUpdateLayers(); if (this.activeView === "field") this._syncUrl({ push: false }); },

    playlistEventMapFullBounds() { const manifest = this.playlistEventMapManifest || {}; const bounds = manifest.time_bounds || {}; const months = this.playlistEventMapTimelineMonths || []; const start = String(bounds.start || (months[0] && `${months[0].month}-01`) || "").slice(0, 10); const end = String(bounds.end || (months.at(-1) && monthEnd(months.at(-1).month)) || "").slice(0, 10); return start && end && start <= end ? { start, end } : { start: "", end: "" }; },
    playlistEventMapNormalBounds() { const full = this.playlistEventMapFullBounds(); if (!full.start || !full.end) return full; const contentStart = String(this.playlistDetail?.earliest_date || "").slice(0, 10); const today = String(typeof this._todayIsoLocal === "function" ? this._todayIsoLocal() : localToday()).slice(0, 10); return contentStart && today && contentStart <= today ? { start: contentStart, end: today } : full; },
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
    playlistEventMapSetWindowEndIndex(value, { update = true, pause = true } = {}) {
      const items = this.playlistEventMapTimelineItems(); const bounds = this.playlistEventMapTimelineBounds(); if (!items.length) return false; if (pause) this.playlistEventMapStopPlayback({ refresh: false, loadEntities: false }); const length = Math.max(1, Math.min(items.length, Number(this.playlistEventMapWindowMonths || 12))); const end = Math.max(length - 1, Math.min(items.length - 1, Math.round(Number(value)))); const start = Math.max(0, end - length + 1); const nextStart = monthStart(items[start].month) < bounds.start ? bounds.start : monthStart(items[start].month); const nextEnd = monthEnd(items[end].month) > bounds.end ? bounds.end : monthEnd(items[end].month); const changed = nextStart !== this.playlistEventMapWindowStart || nextEnd !== this.playlistEventMapWindowEnd; this.playlistEventMapWindowStart = nextStart; this.playlistEventMapWindowEnd = nextEnd; this.playlistEventMapClearWindowPreview(); if (update && changed) { this.playlistEventMapEntityIndices = new Set(); this.playlistEventMapEntityFilter = null; this.playlistEventMapUpdateLayers(); this.playlistEventMapRefreshTopicDetail(); if (!this.playlistEventMapPlaying) this.playlistEventMapScheduleEntities(); } return changed;
    },
    playlistEventMapSetWindowMonths(value) { const months = Math.max(1, Math.min(12, Number(value || 12))); if (months === this.playlistEventMapWindowMonths) return; this.playlistEventMapWindowMonths = months; this.playlistEventMapSetWindowEndIndex(this.playlistEventMapWindowIndices().end, { update: true, pause: true }); },
    playlistEventMapSelectTimelineWindowEnd(event) { if (!primaryPointer(event)) return; const index = this.playlistEventMapTimelineIndexFromClientX(event.clientX); if (index !== null) this.playlistEventMapSetWindowEndIndex(index); },
    playlistEventMapSelectionStyle() { const items = this.playlistEventMapTimelineItems(); if (!items.length) return "display:none"; const range = this.playlistEventMapWindowDisplayIndices(); return `left:${(range.start / items.length * 100).toFixed(4)}%;right:${(100 - (range.end + 1) / items.length * 100).toFixed(4)}%`; },
    playlistEventMapTimelineIndexFromClientX(clientX) { const items = this.playlistEventMapTimelineItems(); const track = this.$refs?.playlistEventMapTimelineTrack; const rect = track?.getBoundingClientRect?.(); const width = Number(rect?.width || track?.clientWidth || 0); if (!items.length || !Number.isFinite(Number(clientX)) || width <= 0) return null; return Math.max(0, Math.min(items.length - 1, Math.floor(Math.max(0, Math.min(.999999, (Number(clientX) - Number(rect?.left || 0)) / width)) * items.length))); },
    playlistEventMapStartTimelineWindowDrag(event) { if (!primaryPointer(event) || !this.playlistEventMapTimelineItems().length) return; this.playlistEventMapStopPlayback({ refresh: false, loadEntities: false }); const range = this.playlistEventMapWindowIndices(); const pointerIndex = this.playlistEventMapTimelineIndexFromClientX(event.clientX); this.playlistEventMapTimelineWindowDragging = true; this.playlistEventMapTimelineWindowPointerId = event.pointerId; this._playlistEventMapTimelineWindowOffset = pointerIndex === null ? 0 : range.start - pointerIndex; this.playlistEventMapSetWindowPreview(range.start, range.end); event.currentTarget?.setPointerCapture?.(event.pointerId); event.preventDefault(); event.stopPropagation(); },
    playlistEventMapMoveTimelineWindowDrag(event) { if (!this.playlistEventMapTimelineWindowDragging || event.pointerId !== this.playlistEventMapTimelineWindowPointerId) return; const index = this.playlistEventMapTimelineIndexFromClientX(event.clientX); if (index === null) return; const range = this.playlistEventMapWindowIndices(); const length = range.length; const start = Math.max(0, Math.min(range.max - length + 1, index + Number(this._playlistEventMapTimelineWindowOffset || 0))); this.playlistEventMapSetWindowPreview(start, start + length - 1); event.preventDefault(); },
    playlistEventMapEndTimelineWindowDrag(event) { if (!this.playlistEventMapTimelineWindowDragging || event.pointerId !== this.playlistEventMapTimelineWindowPointerId) return; this.playlistEventMapMoveTimelineWindowDrag(event); const range = this.playlistEventMapWindowDisplayIndices(); this.playlistEventMapReleaseTimelineWindowDrag({ restore: false }); this.playlistEventMapSetWindowEndIndex(range.end); },
    playlistEventMapReleaseTimelineWindowDrag({ restore = true } = {}) { this.playlistEventMapTimelineWindowDragging = false; this.playlistEventMapTimelineWindowPointerId = null; this._playlistEventMapTimelineWindowOffset = null; this.playlistEventMapClearWindowPreview(); if (restore) this.playlistEventMapController()?.cancelWindowPreview(); },
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
    playlistEventMapHandleTypeFilterChange() { this.playlistEventMapStopPlayback({ refresh: false, loadEntities: false }); this.playlistEventMapUpdateLayers(); this.playlistEventMapRefreshTopicDetail(); this.playlistEventMapScheduleEntities(); },
    playlistEventMapToggleFilters() { this.playlistEventMapFiltersOpen = !this.playlistEventMapFiltersOpen; },
    playlistEventMapSetCameraMode(mode) { this.playlistEventMapCameraMode = mode === "orthographic" ? "orthographic" : "perspective"; this.playlistEventMapController()?.setCameraMode(this.playlistEventMapCameraMode); },
    playlistEventMapResetCamera() { this.playlistEventMapController()?.resetCamera(); },
    playlistEventMapLevelInfo() { return EVENT_MAP_LEVELS[String(this.playlistEventMapViewportSummary?.sceneLevel || "overview")] || EVENT_MAP_LEVELS.overview; },
    playlistEventMapObjectGuide() { return EVENT_MAP_OBJECT_GUIDE; },
    playlistEventMapSemanticLegend() { return Array.isArray(this.playlistEventMapManifest?.semantic_families) ? this.playlistEventMapManifest.semantic_families : []; },
    playlistEventMapSummaryCards() { const manifest = this.playlistEventMapManifest || {}; return [{ label: "全期真实事件", value: Number(manifest.canonical_count || 0) }, { label: "当前窗口", value: Number(this.playlistEventMapVisibleCount || 0) }, { label: "窗口长度（月）", value: Number(this.playlistEventMapWindowMonths || 0) }, { label: "本视野", value: Number(this.playlistEventMapViewportSummary?.viewportCanonicalCount || 0) }]; },
    playlistEventMapFilterChips() { const chips = []; if (this.playlistEventMapTopicFocus) chips.push({ key: "topic", label: `主题：${this.playlistEventMapTopicFocus.label || "已聚焦"}` }); if (this.playlistEventMapEntityFilter) chips.push({ key: "entity", label: `实体：${this.playlistEventMapEntityFilter.name || this.playlistEventMapEntityFilter.normalized_key}` }); if (this.playlistEventMapTypeFilter) { const option = this.playlistEventMapTypeOptions().find((item) => String(item.code) === String(this.playlistEventMapTypeFilter)); chips.push({ key: "type", label: `事件类型：${option?.label || option?.value || this.playlistEventMapTypeFilter}` }); } return chips; },
    playlistEventMapClearFilter(key) { if (key === "topic") this.playlistEventMapClearTopicFocus(); if (key === "entity") this.playlistEventMapClearEntityFilter(); if (key === "type") { this.playlistEventMapTypeFilter = ""; this.playlistEventMapHandleTypeFilterChange(); } },
    playlistEventMapLegendItems() { const items = this.playlistEventMapViewportSummary?.sceneLevel === "event" ? ["金色＝选中事件", "绿色＝实体命中"] : ["点密度＝事件聚集程度", "颜色＝事件语义族"]; if (this.playlistEventMapTopicFocus) items.push("主题选中＝加亮与细金边"); return items; },
    playlistEventMapMetaLabel() { const manifest = this.playlistEventMapManifest || {}; if (!Number(manifest.canonical_count || 0)) return "暂无可进入地图的真实事件"; return `${this.formatInteger(manifest.canonical_count)} 个真实事件 · ${this.formatInteger(manifest.record_count || 0)} 条记录 · 当前窗口 ${this.formatInteger(this.playlistEventMapVisibleCount || 0)}`; },
    playlistEventMapEmptyLabel() { const manifest = this.playlistEventMapManifest || {}; if (this.playlistEventMapError) return this.playlistEventMapError; if (this.playlistEventMapLoading) return "事件语义星域加载中…"; if (["pending", "running"].includes(String(manifest.build_status || manifest.status || ""))) return "首个事件语义星域快照正在构建。"; if (manifest.build_status === "failed" || manifest.status === "failed") return manifest.build_error || manifest.last_error || "事件语义星域构建失败。"; if (!manifest.snapshot_id) return "尚未构建事件语义星域快照。"; if (!Number(manifest.canonical_count || 0)) return "没有同时具备事件发生时间和语义向量的已确认事件。"; return this.playlistEventMapScene && !this.playlistEventMapVisibleCount ? "当前时间窗口没有可显示的真实事件。" : ""; },
    playlistEventMapStatusBadgeLabel() { const manifest = this.playlistEventMapStatus || this.playlistEventMapManifest || {}; if (manifest.building || ["pending", "running"].includes(String(manifest.build_status || ""))) return "地图构建中"; if (manifest.build_status === "failed") return manifest.snapshot_id ? "地图更新失败" : "构建失败"; if (manifest.status === "failed") return "构建失败"; if (manifest.dirty || manifest.state?.dirty) return manifest.snapshot_id ? "地图待更新" : "地图未构建"; return manifest.snapshot_id ? "地图已就绪" : "地图未构建"; },
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
        if (subview === "analysis" && manifest && (snapshotChanged || snapshotMissingLocally)) {
          await this.playlistEventMapLoadView({ silent: true, schedulePolling: false });
          if (!stillCurrent()) return;
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
