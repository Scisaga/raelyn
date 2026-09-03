import { getCachedValue, setCachedValue } from "../services/cache.js";
import { assetDirectModeEnabled } from "./asset-delivery.js";
import {
  formatBytes,
  formatCompactInteger,
  formatDateTime,
  formatDateTimeShort,
  formatDateTimeShortWithSeconds,
  formatDuration,
  formatInteger,
} from "./format.js";
import {
  addUniqueMediaId,
  filterUnselectedMediaOptions,
  mediaAvatarText,
  removeMediaId,
  resolveMediaItemsByIds,
} from "./media-tags.js";
import { mdLabel } from "./playlist-periods.js";

function isMixedContentDirectUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return false;
  try {
    if (window.location.protocol !== "https:") return false;
    const resolved = new URL(raw, window.location.origin);
    return resolved.protocol === "http:";
  } catch {
    return false;
  }
}

export function createCommonViewMethods() {
  return {
    async ensureLightweightCharts() {
      if (window.LightweightCharts && typeof window.LightweightCharts.createChart === "function") return window.LightweightCharts;
      if (this._lightweightChartsLoadingPromise) return this._lightweightChartsLoadingPromise;
      this._lightweightChartsLoadingPromise = new Promise((resolve, reject) => {
        const existing = document.querySelector('script[data-raelyn-vendor="lightweight-charts"]');
        if (existing) {
          existing.addEventListener("load", () => resolve(window.LightweightCharts), { once: true });
          existing.addEventListener("error", () => reject(new Error("lightweight-charts load failed")), { once: true });
          return;
        }
        const script = document.createElement("script");
        script.src = "/static/vendor/lightweight-charts.min.js";
        script.defer = true;
        script.dataset.raelynVendor = "lightweight-charts";
        script.onload = () => resolve(window.LightweightCharts);
        script.onerror = () => reject(new Error("lightweight-charts load failed"));
        document.head.appendChild(script);
      }).finally(() => {
        this._lightweightChartsLoadingPromise = null;
      });
      return this._lightweightChartsLoadingPromise;
    },

    mediaDisplayName(media) {
      return (media && (media.name || media.provider_media_id || media.url)) || "";
    },

    mediaAvatarLabel(media) {
      return mediaAvatarText(media && (media.name || media.provider_media_id || ""));
    },

    mediaAvatarClasses(media) {
      const provider = (media && media.provider) || "";
      if (provider === "youtube") return "bg-rose-500/15 text-rose-200 ring-rose-400/20";
      if (provider === "bilibili") return "bg-sky-500/15 text-sky-200 ring-sky-400/20";
      return "bg-slate-800 text-slate-200 ring-slate-700/60";
    },

    videoMediaDisplayName(video) {
      return (video && (video.media_name || video.media_id)) || "";
    },

    videoMediaAvatarLabel(video) {
      return mediaAvatarText(video && video.media_name);
    },

    videoSelectedMedia() {
      return resolveMediaItemsByIds(this.mediaIndex, this.videoMediaIds);
    },

    videoMediaOptionIndex() {
      const items = Array.isArray(this.mediaIndex) ? this.mediaIndex : [];
      const isDomainLibrary = this.activeView === "library" && this.libraryScope === "domain";
      if (!isDomainLibrary) return items;
      const allowed = new Set((this.libraryDomainMediaIds || []).map((id) => String(id)));
      return items.filter((media) => allowed.has(String(media?.id || "")));
    },

    videoFilteredMediaOptions() {
      const needle = String(this.videoMediaTagQuery || "").trim().toLowerCase();
      return this.videoMediaOptionIndex()
        .filter((media) => {
          if (!needle) return true;
          const text = [this.mediaDisplayName(media), media?.provider, media?.url]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();
          return text.includes(needle);
        })
        .slice(0, 200);
    },

    videoMediaFilterLabel() {
      const selected = this.videoSelectedMedia();
      if (!this.videoMediaIds?.length) return `全部媒体（${this.videoMediaOptionIndex().length}）`;
      if (this.videoMediaIds.length === 1) {
        return selected[0] ? this.mediaDisplayName(selected[0]) : "已选 1 个媒体";
      }
      return `已选 ${this.videoMediaIds.length} 个媒体`;
    },

    videoOpenMediaFilter() {
      this.videoMediaDraftIds = [...new Set((this.videoMediaIds || []).map((id) => String(id)))];
      this.videoMediaTagQuery = "";
      this.videoMediaTagOpen = true;
    },

    videoMediaDraftSelected(mediaId) {
      return (this.videoMediaDraftIds || []).map((id) => String(id)).includes(String(mediaId || ""));
    },

    videoToggleMediaDraft(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      if (this.videoMediaDraftSelected(id)) {
        this.videoMediaDraftIds = (this.videoMediaDraftIds || []).filter((item) => String(item) !== id);
      } else {
        this.videoMediaDraftIds = [...(this.videoMediaDraftIds || []), id];
      }
    },

    videoClearMediaDraft() {
      this.videoMediaDraftIds = [];
    },

    videoApplyMediaFilter() {
      this.videoMediaIds = [...new Set((this.videoMediaDraftIds || []).map((id) => String(id)).filter(Boolean))];
      this.videoMediaTagOpen = false;
      this.loadVideos();
    },

    videoOpenTimeFilter() {
      this.videoTimeDraftFrom = this.videoFrom || "";
      this.videoTimeDraftTo = this.videoTo || "";
      this.videoTimeFilterOpen = true;
    },

    videoTimeFilterLabel() {
      if (!this.videoFrom && !this.videoTo) return "全部时间";
      const from = this.videoFrom ? new Date(this.videoFrom) : null;
      const to = this.videoTo ? new Date(this.videoTo) : null;
      if (from && to && Number.isFinite(from.getTime()) && Number.isFinite(to.getTime())) {
        const hours = (to.getTime() - from.getTime()) / 3600000;
        if (Math.abs(hours - 24) < 0.1) return "最近 24 小时";
        if (Math.abs(hours - 168) < 0.1) return "最近 7 天";
        if (Math.abs(hours - 720) < 0.1) return "最近 30 天";
      }
      const format = (value) => {
        if (!value) return "不限";
        const date = new Date(value);
        if (!Number.isFinite(date.getTime())) return "不限";
        return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
      };
      return `${format(this.videoFrom)} 至 ${format(this.videoTo)}`;
    },

    videoSetTimePreset(hours) {
      if (!hours) {
        this.videoFrom = "";
        this.videoTo = "";
      } else {
        const now = new Date();
        this.videoFrom = this._toLocalInputValue(new Date(now.getTime() - Number(hours) * 3600000));
        this.videoTo = this._toLocalInputValue(now);
      }
      this.videoTimeDraftFrom = this.videoFrom;
      this.videoTimeDraftTo = this.videoTo;
      this.videoTimeFilterOpen = false;
      this.loadVideos();
    },

    videoApplyTimeFilter() {
      const from = this.videoTimeDraftFrom ? new Date(this.videoTimeDraftFrom) : null;
      const to = this.videoTimeDraftTo ? new Date(this.videoTimeDraftTo) : null;
      if (from && to && from.getTime() >= to.getTime()) {
        this.globalStatus = "error: 起始时间必须早于结束时间";
        return;
      }
      this.videoFrom = this.videoTimeDraftFrom || "";
      this.videoTo = this.videoTimeDraftTo || "";
      this.videoTimeFilterOpen = false;
      this.loadVideos();
    },

    videoResetFilters() {
      this.videoMediaIds = [];
      this.videoMediaDraftIds = [];
      this.videoStatus = "";
      this.videoQuery = "";
      this.videoMediaTagQuery = "";
      this.videoMediaTagOpen = false;
      this.videoTimeFilterOpen = false;
      const now = new Date();
      this.videoFrom = this._toLocalInputValue(new Date(now.getTime() - 24 * 3600000));
      this.videoTo = this._toLocalInputValue(now);
      this.videoTimeDraftFrom = this.videoFrom;
      this.videoTimeDraftTo = this.videoTo;
      this.loadVideos();
    },

    openMediaVideos(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return;

      this.videoMediaIds = [id];
      this.videoStatus = "";
      this.videoQuery = "";
      this.videoMediaDraftIds = [id];
      this.videoMediaTagQuery = "";
      this.videoMediaTagOpen = false;

      const now = new Date();
      const since = new Date(now.getTime() - 90 * 24 * 3600 * 1000);
      this.videoFrom = this._toLocalInputValue(since);
      this.videoTo = this._toLocalInputValue(now);

      this.switchView("videos");
    },

    createPlaylistSelectedMedia() {
      return resolveMediaItemsByIds(this.mediaIndex, this.createPlaylistMediaIds);
    },

    createPlaylistFilteredMediaOptions() {
      return filterUnselectedMediaOptions({
        index: this.mediaIndex,
        selectedIds: this.createPlaylistMediaIds,
        query: this.createPlaylistMediaTagQuery,
        displayName: (media) => this.mediaDisplayName(media),
      });
    },

    createPlaylistAddMediaTag(mediaId) {
      this.createPlaylistMediaIds = addUniqueMediaId(this.createPlaylistMediaIds, mediaId);
      this.createPlaylistMediaTagQuery = "";
      this.createPlaylistMediaTagOpen = false;
    },

    createPlaylistAddFirstFilteredMediaTag() {
      const items = this.createPlaylistFilteredMediaOptions();
      if (items.length) this.createPlaylistAddMediaTag(items[0].id);
    },

    createPlaylistRemoveMediaTag(mediaId) {
      this.createPlaylistMediaIds = removeMediaId(this.createPlaylistMediaIds, mediaId);
    },

    formatDuration(sec) {
      return formatDuration(sec);
    },

    formatDateTime(ts) {
      return formatDateTime(ts);
    },

    formatDateTimeShort(ts) {
      return formatDateTimeShort(ts);
    },

    formatDateTimeShortWithSeconds(ts) {
      return formatDateTimeShortWithSeconds(ts);
    },

    formatBytes(n) {
      return formatBytes(n);
    },

    formatInteger(n) {
      return formatInteger(n);
    },

    formatCompactInteger(n) {
      return formatCompactInteger(n);
    },

    mdLabel(iso) {
      return mdLabel(iso);
    },

    playlistVideoTimelineAt(video) {
      if (!video) return "";
      return video.timeline_at || video.published_at || "";
    },

    playlistVideoSortValue(video) {
      const raw = this.playlistVideoTimelineAt(video);
      if (!raw) return 0;
      try {
        const ts = new Date(raw).getTime();
        return Number.isFinite(ts) ? ts : 0;
      } catch {
        return 0;
      }
    },

    servicePillClass(ok) {
      return ok
        ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-200"
        : "border-rose-500/20 bg-rose-500/10 text-rose-200";
    },

    serviceText(service) {
      if (!service) return "unknown";
      if (service.configured === false) return "未配置";
      return service.ok ? "OK" : "Error";
    },

    assetDirectUrlUsable(url) {
      const raw = String(url || "").trim();
      if (!raw) return false;
      return !isMixedContentDirectUrl(raw);
    },

    assetContentUrl(asset) {
      if (!asset || !asset.id) return "";
      const presignedUrl = String((asset && asset.presigned_url) || "").trim();
      if (assetDirectModeEnabled(this.assetDelivery) && this.assetDirectUrlUsable(presignedUrl)) return presignedUrl;
      const base = (this.assetDelivery && this.assetDelivery.proxyBasePath) || "/api/assets";
      return `${String(base).replace(/\/+$/, "")}/${encodeURIComponent(String(asset.id))}/content`;
    },

    assetDownloadUrl(asset) {
      if (!asset || !asset.id) return "";
      if (assetDirectModeEnabled(this.assetDelivery)) {
        const downloadUrl = String((asset && asset.download_presigned_url) || "").trim();
        if (this.assetDirectUrlUsable(downloadUrl)) return downloadUrl;
        const presignedUrl = String((asset && asset.presigned_url) || "").trim();
        if (this.assetDirectUrlUsable(presignedUrl)) return presignedUrl;
      }
      const base = (this.assetDelivery && this.assetDelivery.proxyBasePath) || "/api/assets";
      return `${String(base).replace(/\/+$/, "")}/${encodeURIComponent(String(asset.id))}/download`;
    },

    clearAssetRef(owner, key) {
      try {
        if (owner && key) owner[key] = null;
      } catch {
        // ignore
      }
    },

    _cacheGet(cache, key) {
      return getCachedValue(cache, key);
    },

    _cacheSet(cache, key, value, ttlMs) {
      setCachedValue(cache, key, value, ttlMs);
    },

    _toLocalInputValue(date) {
      const pad = (n) => String(n).padStart(2, "0");
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(
        date.getMinutes()
      )}`;
    },

    formatTs(value) {
      if (!value) return "-";
      try {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return date.toLocaleString();
      } catch {
        return String(value);
      }
    },

    formatTsShort(value) {
      if (!value) return "-";
      try {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        const pad = (n) => String(n).padStart(2, "0");
        return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
      } catch {
        return String(value);
      }
    },

    _shortId(value, n = 8) {
      const raw = String(value || "").trim();
      if (!raw) return "";
      return raw.length <= n ? raw : raw.slice(0, n);
    },
  };
}
