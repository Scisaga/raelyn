import {
  PLAYLIST_ASSETS_CACHE_TTL_MS,
  PLAYLIST_BRIEF_CACHE_TTL_MS,
  PLAYLIST_TRANSCRIPT_CACHE_TTL_MS,
} from "../app/constants.js";
import { assetPresignQueryValue } from "../shared/asset-delivery.js";
import { addUniqueMediaId, filterUnselectedMediaOptions, removeMediaId, resolveMediaItemsByIds } from "../shared/media-tags.js";
import {
  formatIsoRangeShort,
  isoParts,
  isoMs,
  mdLabel,
  periodAddIso,
  periodClampIso,
  periodDiff,
  periodEndIso,
  periodStartIso,
  todayIsoLocal,
  weekdayZh,
} from "../shared/playlist-periods.js";

const PLAYLIST_TRANSCRIPT_VARIANTS = ["plain", "polished"];
const PLAYLIST_ANALYSIS_PROJECTION_PLAYBACK_INTERVAL_MS = 420;
const PLAYLIST_ANALYSIS_BREAKPOINT_Z_MIN = 1.5;
const PLAYLIST_ANALYSIS_BREAKPOINT_Z_MAX = 5.0;
const PLAYLIST_ANALYSIS_BREAKPOINT_AUTO_MONTH_LIMIT = 18;
const PLAYLIST_ANALYSIS_BREAKPOINT_AUTO_COUNT_LIMIT = 24;
const PLAYLIST_ANALYSIS_BREAKPOINT_CLUSTER_MIN_PX = 32;
const PLAYLIST_ANALYSIS_BREAKPOINT_TOP_VISIBLE_COUNT = 8;
const PLAYLIST_ANALYSIS_BREAKPOINT_IMPORTANT_COUNT = 12;
const PLAYLIST_ANALYSIS_BREAKPOINT_DISPLAY_MODES = [
  { key: "auto", label: "自动" },
  { key: "important", label: "重要" },
  { key: "density", label: "密度" },
  { key: "all", label: "全部" },
];
const PLAYLIST_ANALYSIS_PROJECTION_WINDOW_OPTIONS = [
  { months: 3, label: "3个月" },
  { months: 6, label: "6个月" },
  { months: 12, label: "1年" },
  { months: 36, label: "3年" },
];
const PLAYLIST_ANALYSIS_CENTROID_ARROW_MIN_LENGTH = 8;
const PLAYLIST_ANALYSIS_CENTROID_ARROW_MAX_LENGTH = 22;
const PLAYLIST_ANALYSIS_CENTROID_ARROW_MIN_DISTANCE = 2.5;

function emptyPlaylistTranscriptVariants() {
  return { plain: false, polished: false };
}

function normalizePlaylistTranscriptVariant(value) {
  const normalized = String(value || "")
    .trim()
    .toLowerCase();
  return PLAYLIST_TRANSCRIPT_VARIANTS.includes(normalized) ? normalized : "";
}

function normalizePlaylistTranscriptSource(value) {
  return String(value || "").trim();
}

export function createPlaylistViewMethods() {
  return {
    async loadPlaylistPage() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const pageLoadToken = Number(this.playlistLoadToken || 0) + 1;
      this.playlistLoadToken = pageLoadToken;
      this._playlistSyncBriefSpeechSupport();
      this.playlistStopBriefSpeech({ clearError: true });
      if (!pid) {
        this.playlistCalendarResetDragState();
        this.playlistDayListResetSwipeState();
        this.playlistBriefResetSwipeState();
        this.playlistAnalysisStopPolling();
        this.playlistAnalysisStopSignalsReload();
        this.playlistAnalysisStopTimelineDensityLoad();
        this.playlistAnalysisStopProjectionPlayback();
        this.playlistAnalysisDestroyChart();
        this.playlistAnalysisReleaseRangeDrag();
        this.playlistAnalysisReleaseProjectionWindowDrag();
        this.playlistDetail = null;
        this.playlistDayVideos = [];
        this.playlistPlayerNeedsDownload = false;
        this.playlistBriefHtml = "";
        this.playlistBriefMarkdown = "";
        this.playlistBriefSpeechText = "";
        this.playlistBriefSourceState = "";
        this.playlistBriefSourceMessage = "";
        this.playlistEventsSummary = null;
        this.playlistEventsAllSummary = null;
        this.playlistEventsAllSummaryLoading = false;
        this.playlistEventsAllSummaryError = "";
        this.playlistEvents = [];
        this.playlistEventDetail = null;
        this.playlistEventDetailLoading = false;
        this.playlistEventEntitySuggestions = [];
        this.playlistEventEntitySuggestOpen = false;
        this.playlistEventEntitySuggestLoading = false;
        this.playlistEventEntitySuggestToken = 0;
        this.playlistEventsError = "";
        this.playlistSelectedEventId = "";
        this._abortCtrl("_playlistCountsAbortCtrl");
        this.playlistPeriodCounts = new Map();
        this.playlistPeriodCountsKey = "";
        this.playlistPeriodCountsLoading = false;
        this.playlistPeriodCountsError = "";
        this.playlistNameEditing = false;
        this.playlistNameDraft = "";
        this.playlistNameSaving = false;
        this.playlistDescEditing = false;
        this.playlistDescDraft = "";
        this.playlistAnalysisSummary = null;
        this.playlistAnalysisSummaryLoading = false;
        this.playlistAnalysisSummaryError = "";
        this.playlistAnalysisPeriods = [];
        this.playlistAnalysisSignals = [];
        this.playlistAnalysisInvalidateSignalCaches();
        this.playlistAnalysisCandidates = [];
        this.playlistAnalysisCandidatesLoaded = false;
        this.playlistAnalysisCandidatesVersion = 0;
        this.playlistAnalysisBreakpointDisplayMode = "auto";
        this.playlistAnalysisHoveredBreakpointItemId = "";
        this._playlistAnalysisCandidateHydrationRunId = "";
        this.playlistAnalysisSelectedCandidateId = "";
        this.playlistAnalysisCandidateDetail = null;
        this.playlistAnalysisTab = "trend";
        this.playlistAnalysisRangeStart = "";
        this.playlistAnalysisRangeEnd = "";
        this.playlistAnalysisFullRangeStart = "";
        this.playlistAnalysisFullRangeEnd = "";
        this.playlistAnalysisTimelineScope = "normal";
        this.playlistAnalysisTimelineDensity = [];
        this.playlistAnalysisTimelineDensityLoading = false;
        this.playlistAnalysisTimelineDensityLoadedRangeStart = "";
        this.playlistAnalysisTimelineDensityLoadedRangeEnd = "";
        this.playlistAnalysisSignalsLoadedRangeStart = "";
        this.playlistAnalysisSignalsLoadedRangeEnd = "";
        this.playlistAnalysisProjectionWindowStart = "";
        this.playlistAnalysisProjectionWindowEnd = "";
        this.playlistAnalysisProjectionWindowAnchorDate = "";
        this.playlistAnalysisProjectionWindowPreviewStartIndex = null;
        this.playlistAnalysisTimelinePreviewStartIndex = null;
        this.playlistAnalysisTimelinePreviewEndIndex = null;
        this.playlistAnalysisSignalsVersion = 0;
        this.playlistAnalysisProjectionPlaying = false;
        this.playlistAnalysisProjectionPlayTimer = null;
        this.playlistAnalysisError = "";
        this.playlistAnalysisBackfillSubmitting = false;
        this.playlistAnalysisBackfillCanceling = false;
        this.playlistAnalysisBackfillJob = null;
        this.playlistAnalysisExportOpen = false;
        this.playlistAnalysisExportPayload = null;
        this.playlistPendingAutoPlayId = "";
        this.playlistAudioThumbnailFailures = new Map();
        this.playlistAudioThumbnailSources = new Map();
        this.pageTitle = "播放列表页";
        return;
      }

      try {
        this.playlistCalendarResetDragState();
        this.playlistDayListResetSwipeState();
        this.playlistBriefResetSwipeState();
        this._syncUrl({ push: false });
        this.playlistDayVideosError = "";
        this.playlistBriefError = "";
        this.playlistBriefSourceState = "";
        this.playlistBriefSourceMessage = "";
        this.playlistEventsError = "";
        this.playlistEventsSummary = null;
        this.playlistEventsAllSummary = null;
        this.playlistEventsAllSummaryLoading = false;
        this.playlistEventsAllSummaryError = "";
        this.playlistEventDetail = null;
        this.playlistEventDetailLoading = false;
        this.playlistEventEntitySuggestions = [];
        this.playlistEventEntitySuggestOpen = false;
        this.playlistEventEntitySuggestLoading = false;
        this.playlistEventEntitySuggestToken = 0;
        this.playlistPlayerNeedsDownload = false;

        const detail = await this.api(`/playlists/${encodeURIComponent(pid)}/detail`);
        if (Number(this.playlistLoadToken || 0) !== pageLoadToken) return;
        if (this.activeView !== "playlist") return;
        const currentPid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
        if (currentPid !== pid) return;
        this.playlistDetail = detail || null;
        this.pageTitle = (detail && detail.name) || "播放列表页";
        this._abortCtrl("_playlistCountsAbortCtrl");
        this.playlistPeriodCounts = new Map();
        this.playlistPeriodCountsKey = "";
        this.playlistPeriodCountsLoading = false;
        this.playlistPeriodCountsError = "";
        this.playlistAudioThumbnailSources = new Map();
        this.playlistAudioThumbnailFailures = new Map();

        this.playlistCalendarUpdateCount();
        try {
          if (this.$nextTick) this.$nextTick(() => this.playlistCalendarUpdateCount());
        } catch {
          // ignore
        }
        setTimeout(() => this.playlistCalendarUpdateCount(), 0);
        const granularity = detail && detail.brief_granularity ? String(detail.brief_granularity).trim().toLowerCase() : "day";
        const today = todayIsoLocal();
        const start = periodStartIso((detail && detail.earliest_date) || today, granularity);
        const end = periodStartIso(today, granularity);
        const max = Math.max(0, periodDiff(start, end, granularity));

        this.playlistTimelineStart = start;
        this.playlistTimelineEnd = end;
        this.playlistTimelineMax = max;

        if (!this.playlistSelectedDate) this.playlistSelectedDate = end;
        this.playlistSelectedDate = periodClampIso(periodStartIso(this.playlistSelectedDate, granularity), start, end);
        this.playlistTimelineValue = Math.max(0, Math.min(max, periodDiff(start, this.playlistSelectedDate, granularity)));
        this.playlistCalendarAnchor = periodClampIso(
          periodAddIso(this.playlistSelectedDate, granularity, -(Number(this.playlistCalendarCount || 14) - 1)),
          start,
          end
        );

        this.playlistCalendarEnsureVisible();
        this.playlistPrefetchCalendarCounts();
        this.playlistEditResetFromDetail();
        if (Number(this.playlistLoadToken || 0) !== pageLoadToken) return;
        if (this.activeView !== "playlist") return;
        if (this.playlistSubview === "analysis") {
          await this.playlistLoadAnalysisView();
        } else {
          await this.playlistLoadDay(this.playlistSelectedDate, { autoPlay: false });
          this.playlistLoadEventsPanel({ silent: true }).catch(() => {});
        }
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    playlistRangeLabel() {
      const detail = this.playlistDetail;
      const start = detail && detail.earliest_date ? String(detail.earliest_date).trim() : "";
      const end = todayIsoLocal();
      if (!start && !end) return "-";
      if (start && end) return `${start} ~ ${end}`;
      return start || end || "-";
    },

    playlistRangeLabelShort() {
      const detail = this.playlistDetail;
      const start = detail && detail.earliest_date ? String(detail.earliest_date).trim() : "";
      return formatIsoRangeShort(start, todayIsoLocal());
    },

    playlistCalendarUpdateCount() {
      try {
        let compact = false;
        try {
          compact = window.matchMedia("(max-width: 639px)").matches;
        } catch {
          compact = !!(window && window.innerWidth && Number(window.innerWidth) < 640);
        }
        this.playlistCalendarCompact = compact;
        const refWidth =
          this.$refs && this.$refs.playlistCalendarStrip && this.$refs.playlistCalendarStrip.clientWidth
            ? Number(this.$refs.playlistCalendarStrip.clientWidth)
            : 0;
        const width = refWidth || (window && window.innerWidth ? Number(window.innerWidth) : 1280);
        const gap = 8;
        const minCard = 64;
        let count = Math.floor((width + gap) / (minCard + gap));
        if (!Number.isFinite(count) || count <= 0) count = 14;
        count = compact ? Math.max(5, Math.min(8, Math.floor(count))) : Math.max(5, Math.min(14, Math.floor(count)));
        if (count !== this.playlistCalendarCount) {
          this.playlistCalendarCount = count;
          this.playlistCalendarEnsureVisible();
          this.playlistPrefetchCalendarCounts();
        }
      } catch {
        // ignore
      }
    },

    playlistCalendarLayoutStyle() {
      const count = Math.max(5, Number(this.playlistCalendarCount || 14));
      if (this.playlistCalendarCompact) return `grid-template-columns:repeat(${count}, minmax(0,1fr));`;
      return `grid-template-columns:repeat(${count + 4}, minmax(0,1fr));`;
    },

    playlistCalendarStripStyle() {
      const count = Math.max(5, Number(this.playlistCalendarCount || 14));
      if (this.playlistCalendarCompact) return "grid-column:1 / -1;";
      return `grid-column:span ${count} / span ${count};`;
    },

    playlistCalendarStripWidthPx() {
      try {
        const width =
          this.$refs && this.$refs.playlistCalendarStrip && this.$refs.playlistCalendarStrip.clientWidth
            ? Number(this.$refs.playlistCalendarStrip.clientWidth)
            : 0;
        return Number.isFinite(width) && width > 0 ? width : 0;
      } catch {
        return 0;
      }
    },

    playlistCalendarInteractionBusy() {
      return !!(this.playlistCalendarDragging || this.playlistCalendarSettling);
    },

    playlistCalendarResolvedAnchor(rawAnchor = null) {
      const g = this.playlistGranularity();
      const rawStart = String(this.playlistTimelineStart || "").trim();
      const rawEnd = String(this.playlistTimelineEnd || "").trim();
      const start = this._periodStartIso(rawStart, g);
      const end = this._periodStartIso(rawEnd, g);
      const selected = this._periodStartIso(String(this.playlistSelectedDate || "").trim(), g);
      const n = Math.max(5, Number(this.playlistCalendarCount || 14));
      if (!start || !end) return "";

      const source = rawAnchor == null ? this.playlistCalendarAnchor : rawAnchor;
      let anchor = this._periodStartIso(String(source || "").trim(), g);
      if (!anchor) anchor = selected ? this._periodAddIso(selected, g, -(n - 1)) : start;

      const maxAnchor = this._periodAddIso(end, g, -(n - 1));
      if (this._isoMs(maxAnchor) < this._isoMs(start)) return start;
      return this._periodStartIso(this._periodClampIso(anchor, start, maxAnchor), g);
    },

    playlistCalendarPreviewStep() {
      if (this.playlistCalendarSettling) {
        const settlingStep = Number(this.playlistCalendarSettleStep || 0);
        return Number.isFinite(settlingStep) ? settlingStep : 0;
      }
      if (!this.playlistCalendarDragging) return 0;
      const offset = Number(this.playlistCalendarDragOffsetX || 0);
      if (!Number.isFinite(offset) || offset === 0) return 0;
      const step = this.playlistCalendarDragStep();
      if (offset > 0) return this.playlistCalendarCanJump(-step) ? -step : 0;
      return this.playlistCalendarCanJump(step) ? step : 0;
    },

    playlistCalendarCurrentTrackStyle() {
      const offset = Number(this.playlistCalendarDragOffsetX || 0);
      const transition = this.playlistCalendarDragging || this.playlistCalendarSuppressTransition ? "none" : "transform 180ms ease";
      return `transform:translate3d(${offset}px,0,0);transition:${transition};`;
    },

    playlistCalendarPreviewTrackStyle() {
      const step = this.playlistCalendarPreviewStep();
      const offset = Number(this.playlistCalendarDragOffsetX || 0);
      const width = this.playlistCalendarStripWidthPx();
      const transition =
        this.playlistCalendarDragging || this.playlistCalendarSuppressTransition ? "none" : "transform 180ms ease,opacity 160ms ease";
      if (!step || width <= 0) return `transform:translate3d(${offset}px,0,0);transition:${transition};opacity:0;`;
      const base = step < 0 ? -width : width;
      return `transform:translate3d(${base + offset}px,0,0);transition:${transition};opacity:1;`;
    },

    playlistCalendarTracks() {
      const interactive = !this.playlistCalendarInteractionBusy();
      const tracks = [
        {
          key: "current",
          items: this.playlistCalendarItems(),
          style: this.playlistCalendarCurrentTrackStyle(),
          interactive,
        },
      ];
      const previewItems = this.playlistCalendarPreviewItems();
      if (previewItems.length) {
        tracks.push({
          key: "preview",
          items: previewItems,
          style: this.playlistCalendarPreviewTrackStyle(),
          interactive: false,
        });
      }
      return tracks;
    },

    playlistCalendarClearSettleTimer() {
      try {
        if (this._playlistCalendarSettleTimer != null) window.clearTimeout(this._playlistCalendarSettleTimer);
      } catch {
        // ignore
      }
      this._playlistCalendarSettleTimer = null;
    },

    playlistCalendarClearTransitionFrames() {
      try {
        if (this._playlistCalendarTransitionRaf1 != null) window.cancelAnimationFrame(this._playlistCalendarTransitionRaf1);
      } catch {
        // ignore
      }
      try {
        if (this._playlistCalendarTransitionRaf2 != null) window.cancelAnimationFrame(this._playlistCalendarTransitionRaf2);
      } catch {
        // ignore
      }
      this._playlistCalendarTransitionRaf1 = null;
      this._playlistCalendarTransitionRaf2 = null;
    },

    playlistCalendarScheduleTransitionRestore() {
      this.playlistCalendarClearTransitionFrames();
      this._playlistCalendarTransitionRaf1 = window.requestAnimationFrame(() => {
        this._playlistCalendarTransitionRaf1 = null;
        this._playlistCalendarTransitionRaf2 = window.requestAnimationFrame(() => {
          this._playlistCalendarTransitionRaf2 = null;
          this.playlistCalendarSuppressTransition = false;
        });
      });
    },

    playlistCalendarReleasePointerTracking() {
      try {
        if (this._playlistCalendarDragMove) window.removeEventListener("pointermove", this._playlistCalendarDragMove);
      } catch {
        // ignore
      }
      try {
        if (this._playlistCalendarDragEnd) {
          window.removeEventListener("pointerup", this._playlistCalendarDragEnd);
          window.removeEventListener("pointercancel", this._playlistCalendarDragEnd);
        }
      } catch {
        // ignore
      }
      this._playlistCalendarDragMove = null;
      this._playlistCalendarDragEnd = null;
      this.playlistCalendarDragging = false;
      this.playlistCalendarDragPointerId = null;
      this.playlistCalendarDragStartX = 0;
      this.playlistCalendarDragStartY = 0;
      this.playlistCalendarDragDidMove = false;
    },

    playlistCalendarResetDragState() {
      this.playlistCalendarClearSettleTimer();
      this.playlistCalendarClearTransitionFrames();
      this.playlistCalendarReleasePointerTracking();
      this.playlistCalendarSettling = false;
      this.playlistCalendarSettleStep = 0;
      this.playlistCalendarSuppressTransition = false;
      this.playlistCalendarDragOffsetX = 0;
    },

    playlistCalendarStartSettle(step, targetOffset, onDone = null) {
      const settleStep = Number(step || 0);
      const target = Number(targetOffset || 0);
      this.playlistCalendarClearSettleTimer();
      this.playlistCalendarSettling = true;
      this.playlistCalendarSettleStep = Number.isFinite(settleStep) ? settleStep : 0;
      this.playlistCalendarDragOffsetX = Number.isFinite(target) ? target : 0;
      this._playlistCalendarSettleTimer = window.setTimeout(() => {
        this._playlistCalendarSettleTimer = null;
        if (typeof onDone === "function") {
          onDone();
          return;
        }
        this.playlistCalendarResetDragState();
      }, 210);
    },

    playlistCalendarCommitSettledStep(step) {
      const delta = Number(step || 0);
      const g = this.playlistGranularity();
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      const selected = this._periodStartIso(String(this.playlistSelectedDate || "").trim(), g);
      const anchor = this.playlistCalendarResolvedAnchor();

      this.playlistCalendarClearSettleTimer();
      this.playlistCalendarClearTransitionFrames();
      this.playlistCalendarSuppressTransition = true;
      this.playlistCalendarSettling = false;
      this.playlistCalendarSettleStep = 0;
      this.playlistCalendarDragOffsetX = 0;

      if (!start || !end || !selected || !Number.isFinite(delta) || delta === 0) {
        this.playlistCalendarScheduleTransitionRestore();
        return;
      }

      try {
        if (anchor) this.playlistCalendarAnchor = this._periodAddIso(anchor, g, delta);
      } catch {
        // ignore
      }

      const next = this._periodClampIso(this._periodAddIso(selected, g, delta), start, end);
      if (next !== selected) {
        this.playlistSelectedDate = next;
        this.playlistCalendarEnsureVisible();
        this.playlistPrefetchCalendarCounts();
        if (this.playlistTimelineStart) {
          const max = Number(this.playlistTimelineMax || 0);
          this.playlistTimelineValue = Math.max(0, Math.min(max, this._periodDiff(this.playlistTimelineStart, next, g)));
        }
        this._syncUrl({ push: false });
        this.playlistLoadDay(next, { autoPlay: true });
      } else {
        this.playlistCalendarEnsureVisible();
        this.playlistPrefetchCalendarCounts();
      }
      this.playlistCalendarScheduleTransitionRestore();
    },

    playlistCalendarCanJump(deltaPeriods) {
      const g = this.playlistGranularity();
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      const selected = String(this.playlistSelectedDate || "").trim();
      const delta = Number(deltaPeriods || 0);
      if (!start || !end || !selected || !Number.isFinite(delta) || delta === 0) return false;
      const next = this._periodClampIso(this._periodAddIso(selected, g, delta), start, end);
      return !!next && next !== selected;
    },

    playlistCalendarGestureThresholdPx() {
      const count = Math.max(5, Number(this.playlistCalendarCount || 14));
      const width = this.playlistCalendarStripWidthPx();
      const cardWidth = width > 0 ? width / count : 72;
      return Math.max(28, Math.min(88, Math.round(cardWidth * 0.35)));
    },

    playlistCalendarResolveDragOffset(rawDx) {
      const dx = Number(rawDx || 0);
      if (!Number.isFinite(dx) || dx === 0) return 0;
      const width = this.playlistCalendarStripWidthPx();
      const maxOffset = Math.max(48, Math.min(width > 0 ? width * 0.72 : 220, 240));
      const canMove = dx > 0 ? this.playlistCalendarCanJump(-1) : this.playlistCalendarCanJump(1);
      const factor = canMove ? 1 : 0.28;
      return Math.max(-maxOffset, Math.min(maxOffset, dx * factor));
    },

    playlistCalendarDragStep() {
      const count = Math.max(1, Math.floor(Number(this.playlistCalendarCount || 1)));
      return count;
    },

    playlistCalendarPointerDown(ev) {
      if (this.playlistSubview !== "main") return;
      if (this.playlistCalendarSettling) return;
      if (this.playlistCalendarDragPointerId !== null) return;
      if (!ev || (ev.pointerType === "mouse" && ev.button !== 0)) return;
      if (ev && ev.isPrimary === false) return;

      this.playlistCalendarResetDragState();
      this.playlistCalendarDragPointerId = ev.pointerId;
      this.playlistCalendarDragStartX = Number(ev.clientX || 0);
      this.playlistCalendarDragStartY = Number(ev.clientY || 0);

      const onMove = (nextEv) => this.playlistCalendarPointerMove(nextEv);
      const onEnd = (nextEv) => this.playlistCalendarPointerEnd(nextEv);
      this._playlistCalendarDragMove = onMove;
      this._playlistCalendarDragEnd = onEnd;

      try {
        window.addEventListener("pointermove", onMove, { passive: false });
        window.addEventListener("pointerup", onEnd, { passive: false });
        window.addEventListener("pointercancel", onEnd, { passive: false });
      } catch {
        // ignore
      }
    },

    playlistCalendarPointerMove(ev) {
      if (!ev || this.playlistCalendarDragPointerId === null || ev.pointerId !== this.playlistCalendarDragPointerId) return;

      const dx = Number(ev.clientX || 0) - Number(this.playlistCalendarDragStartX || 0);
      const dy = Number(ev.clientY || 0) - Number(this.playlistCalendarDragStartY || 0);
      const absX = Math.abs(dx);
      const absY = Math.abs(dy);

      if (!this.playlistCalendarDragDidMove) {
        if (absX < 6 && absY < 6) return;
        if (absY > absX) {
          this.playlistCalendarResetDragState();
          return;
        }
        this.playlistCalendarDragDidMove = true;
      }

      this.playlistCalendarDragging = true;
      this.playlistCalendarDragOffsetX = this.playlistCalendarResolveDragOffset(dx);
      try {
        if (ev.cancelable) ev.preventDefault();
      } catch {
        // ignore
      }
    },

    playlistCalendarPointerEnd(ev) {
      if (!ev || this.playlistCalendarDragPointerId === null || ev.pointerId !== this.playlistCalendarDragPointerId) return;

      const offset = Number(this.playlistCalendarDragOffsetX || 0);
      const didMove = !!this.playlistCalendarDragDidMove;
      const threshold = this.playlistCalendarGestureThresholdPx();
      const step = this.playlistCalendarPreviewStep();

      if (didMove) {
        this.playlistCalendarDragSuppressClickUntil = Date.now() + 300;
        try {
          if (ev.cancelable) ev.preventDefault();
        } catch {
          // ignore
        }
      }

      this.playlistCalendarReleasePointerTracking();

      if (!didMove) {
        this.playlistCalendarResetDragState();
        return;
      }

      if (!step || Math.abs(offset) < threshold) {
        this.playlistCalendarStartSettle(step, 0);
        return;
      }

      const width = this.playlistCalendarStripWidthPx();
      const magnitude = width > 0 ? width : Math.max(Math.abs(offset), 120);
      const targetOffset = step < 0 ? magnitude : -magnitude;
      this.playlistCalendarStartSettle(step, targetOffset, () => this.playlistCalendarCommitSettledStep(step));
    },

    playlistCalendarSelectDate(date, ev = null) {
      if (Date.now() < Number(this.playlistCalendarDragSuppressClickUntil || 0)) {
        try {
          if (ev && ev.preventDefault) ev.preventDefault();
        } catch {
          // ignore
        }
        return;
      }
      this.playlistSetDate(date);
    },

    _playlistSwipeTargetConfig(target) {
      return target === "brief"
        ? {
            statePrefix: "playlistBrief",
            moveHandlerKey: "_playlistBriefDragMove",
            endHandlerKey: "_playlistBriefDragEnd",
            settleTimerKey: "_playlistBriefSettleTimer",
            scrollerRef: "playlistBriefScroller",
          }
        : {
            statePrefix: "playlistDayList",
            moveHandlerKey: "_playlistDayListDragMove",
            endHandlerKey: "_playlistDayListDragEnd",
            settleTimerKey: "_playlistDayListSettleTimer",
            scrollerRef: "playlistDayListScroller",
          };
    },

    _playlistSwipeStateKey(target, suffix) {
      const config = this._playlistSwipeTargetConfig(target);
      return `${config.statePrefix}${suffix}`;
    },

    _playlistSwipeEnabled() {
      try {
        return !!(window.matchMedia && window.matchMedia("(max-width: 1023px)").matches);
      } catch {
        return !!(window && window.innerWidth && Number(window.innerWidth) <= 1023);
      }
    },

    _playlistSwipeScrollerWidth(target) {
      const config = this._playlistSwipeTargetConfig(target);
      try {
        const width =
          this.$refs && this.$refs[config.scrollerRef] && this.$refs[config.scrollerRef].clientWidth
            ? Number(this.$refs[config.scrollerRef].clientWidth)
            : 0;
        return Number.isFinite(width) && width > 0 ? width : 0;
      } catch {
        return 0;
      }
    },

    _playlistSwipeGestureThresholdPx(target) {
      const width = this._playlistSwipeScrollerWidth(target);
      if (width > 0) return Math.max(44, Math.min(96, Math.round(width * 0.14)));
      return 56;
    },

    _playlistSwipePanelStyle(target) {
      const offset = Number(this[this._playlistSwipeStateKey(target, "DragOffsetX")] || 0);
      const settling = !!this[this._playlistSwipeStateKey(target, "Settling")];
      const transition = settling ? "transform 180ms cubic-bezier(0.22, 1, 0.36, 1)" : "none";
      return `transform:translate3d(${offset}px,0,0);transition:${transition};will-change:transform;`;
    },

    _playlistPeriodCanSwipe(step) {
      const delta = Number(step || 0);
      if (!Number.isFinite(delta) || delta === 0) return false;
      const g = this.playlistGranularity();
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      const selected = String(this.playlistSelectedDate || "").trim();
      if (!start || !end || !selected) return false;
      const next = this._periodClampIso(this._periodAddIso(selected, g, delta), start, end);
      return !!next && next !== selected;
    },

    _playlistSwipeResolveDragOffset(target, rawDx) {
      const dx = Number(rawDx || 0);
      if (!Number.isFinite(dx) || dx === 0) return 0;
      const width = this._playlistSwipeScrollerWidth(target);
      const maxOffset = Math.max(56, Math.min(width > 0 ? width * 0.72 : 260, 280));
      const canMove = dx > 0 ? this._playlistPeriodCanSwipe(-1) : this._playlistPeriodCanSwipe(1);
      const factor = canMove ? 1 : 0.28;
      return Math.max(-maxOffset, Math.min(maxOffset, dx * factor));
    },

    _playlistSwipeClearSettleTimer(target) {
      const config = this._playlistSwipeTargetConfig(target);
      try {
        if (this[config.settleTimerKey]) clearTimeout(this[config.settleTimerKey]);
      } catch {
        // ignore
      }
      this[config.settleTimerKey] = null;
    },

    _playlistSwipeReleasePointerTracking(target) {
      const config = this._playlistSwipeTargetConfig(target);
      const statePrefix = config.statePrefix;
      try {
        if (this[config.moveHandlerKey]) window.removeEventListener("pointermove", this[config.moveHandlerKey]);
      } catch {
        // ignore
      }
      try {
        if (this[config.endHandlerKey]) {
          window.removeEventListener("pointerup", this[config.endHandlerKey]);
          window.removeEventListener("pointercancel", this[config.endHandlerKey]);
        }
      } catch {
        // ignore
      }
      this[config.moveHandlerKey] = null;
      this[config.endHandlerKey] = null;
      this[`${statePrefix}DragPointerId`] = null;
      this[`${statePrefix}DragStartX`] = 0;
      this[`${statePrefix}DragStartY`] = 0;
      this[`${statePrefix}DragDidMove`] = false;
    },

    _playlistSwipeStartSettle(target, targetOffset, onDone = null) {
      const swipeTarget = String(target || "").trim();
      const config = this._playlistSwipeTargetConfig(swipeTarget);
      const statePrefix = config.statePrefix;
      const resolvedOffset = Number(targetOffset || 0);
      this._playlistSwipeClearSettleTimer(swipeTarget);
      this[`${statePrefix}Settling`] = true;
      this[`${statePrefix}DragOffsetX`] = Number.isFinite(resolvedOffset) ? resolvedOffset : 0;
      this[config.settleTimerKey] = window.setTimeout(() => {
        this[config.settleTimerKey] = null;
        if (typeof onDone === "function") {
          onDone();
          return;
        }
        this._playlistSwipeResetState(swipeTarget);
      }, 180);
    },

    _playlistSwipeResetState(target) {
      const config = this._playlistSwipeTargetConfig(target);
      const statePrefix = config.statePrefix;
      this._playlistSwipeClearSettleTimer(target);
      this._playlistSwipeReleasePointerTracking(target);
      this[`${statePrefix}Settling`] = false;
      this[`${statePrefix}DragOffsetX`] = 0;
    },

    _playlistSwipePointerDown(target, ev, onMoveFactory, onEndFactory) {
      const config = this._playlistSwipeTargetConfig(target);
      const statePrefix = config.statePrefix;
      if (!this._playlistSwipeEnabled()) return;
      if (this[`${statePrefix}Settling`]) return;
      if (this[`${statePrefix}DragPointerId`] !== null) return;
      if (!ev || (ev.pointerType === "mouse" && ev.button !== 0)) return;
      if (ev && ev.isPrimary === false) return;

      this._playlistSwipeResetState(target);
      this[`${statePrefix}DragPointerId`] = ev.pointerId;
      this[`${statePrefix}DragStartX`] = Number(ev.clientX || 0);
      this[`${statePrefix}DragStartY`] = Number(ev.clientY || 0);

      const onMove = onMoveFactory();
      const onEnd = onEndFactory();
      this[config.moveHandlerKey] = onMove;
      this[config.endHandlerKey] = onEnd;

      try {
        window.addEventListener("pointermove", onMove, { passive: false });
        window.addEventListener("pointerup", onEnd, { passive: false });
        window.addEventListener("pointercancel", onEnd, { passive: false });
      } catch {
        // ignore
      }
    },

    _playlistSwipePointerMove(target, ev) {
      const config = this._playlistSwipeTargetConfig(target);
      const statePrefix = config.statePrefix;
      if (!ev || this[`${statePrefix}DragPointerId`] === null || ev.pointerId !== this[`${statePrefix}DragPointerId`]) return;

      const dx = Number(ev.clientX || 0) - Number(this[`${statePrefix}DragStartX`] || 0);
      const dy = Number(ev.clientY || 0) - Number(this[`${statePrefix}DragStartY`] || 0);
      const absX = Math.abs(dx);
      const absY = Math.abs(dy);

      if (!this[`${statePrefix}DragDidMove`]) {
        if (absX < 8 && absY < 8) return;
        if (absY > absX) {
          this._playlistSwipeResetState(target);
          return;
        }
        this[`${statePrefix}DragDidMove`] = true;
      }

      this[`${statePrefix}DragOffsetX`] = this._playlistSwipeResolveDragOffset(target, dx);
      try {
        if (ev.cancelable) ev.preventDefault();
      } catch {
        // ignore
      }
    },

    _playlistSwipePointerEnd(target, ev, onCommit) {
      const config = this._playlistSwipeTargetConfig(target);
      const statePrefix = config.statePrefix;
      if (!ev || this[`${statePrefix}DragPointerId`] === null || ev.pointerId !== this[`${statePrefix}DragPointerId`]) return;

      const dx = Number(ev.clientX || 0) - Number(this[`${statePrefix}DragStartX`] || 0);
      const didMove = !!this[`${statePrefix}DragDidMove`];
      const threshold = this._playlistSwipeGestureThresholdPx(target);

      if (didMove) {
        this[`${statePrefix}DragSuppressClickUntil`] = Date.now() + 300;
        try {
          if (ev.cancelable) ev.preventDefault();
        } catch {
          // ignore
        }
      }

      this._playlistSwipeReleasePointerTracking(target);

      if (!didMove) {
        this._playlistSwipeResetState(target);
        return;
      }

      if (Math.abs(dx) < threshold) {
        this._playlistSwipeStartSettle(target, 0);
        return;
      }

      const width = this._playlistSwipeScrollerWidth(target);
      const magnitude = width > 0 ? Math.max(width * 0.82, Math.abs(dx)) : Math.max(Math.abs(dx), 180);
      const targetOffset = dx > 0 ? magnitude : -magnitude;
      this._playlistSwipeStartSettle(target, targetOffset, () => {
        this._playlistSwipeResetState(target);
        if (typeof onCommit === "function") onCommit(dx > 0 ? -1 : 1);
      });
    },

    playlistDayListSwipeEnabled() {
      return this._playlistSwipeEnabled();
    },

    playlistDayListGestureThresholdPx() {
      return this._playlistSwipeGestureThresholdPx("dayList");
    },

    playlistDayListPanelStyle() {
      return this._playlistSwipePanelStyle("dayList");
    },

    playlistDayListCanSwipe(step) {
      return this._playlistPeriodCanSwipe(step);
    },

    playlistDayListResolveDragOffset(rawDx) {
      return this._playlistSwipeResolveDragOffset("dayList", rawDx);
    },

    playlistDayListClearSettleTimer() {
      this._playlistSwipeClearSettleTimer("dayList");
    },

    playlistDayListReleasePointerTracking() {
      this._playlistSwipeReleasePointerTracking("dayList");
    },

    playlistDayListStartSettle(targetOffset, onDone = null) {
      this._playlistSwipeStartSettle("dayList", targetOffset, onDone);
    },

    playlistDayListResetSwipeState() {
      this._playlistSwipeResetState("dayList");
    },

    playlistDayListPointerDown(ev) {
      this._playlistSwipePointerDown(
        "dayList",
        ev,
        () => (nextEv) => this.playlistDayListPointerMove(nextEv),
        () => (nextEv) => this.playlistDayListPointerEnd(nextEv)
      );
    },

    playlistDayListPointerMove(ev) {
      this._playlistSwipePointerMove("dayList", ev);
    },

    playlistDayListPointerEnd(ev) {
      this._playlistSwipePointerEnd("dayList", ev, (delta) => {
        if (delta < 0) this.playlistPrevDay();
        else this.playlistNextDay();
      });
    },

    playlistBriefSwipeEnabled() {
      return this._playlistSwipeEnabled();
    },

    playlistBriefGestureThresholdPx() {
      return this._playlistSwipeGestureThresholdPx("brief");
    },

    playlistBriefPanelStyle() {
      return this._playlistSwipePanelStyle("brief");
    },

    playlistBriefCanSwipe(step) {
      return this._playlistPeriodCanSwipe(step);
    },

    playlistBriefResolveDragOffset(rawDx) {
      return this._playlistSwipeResolveDragOffset("brief", rawDx);
    },

    playlistBriefClearSettleTimer() {
      this._playlistSwipeClearSettleTimer("brief");
    },

    playlistBriefReleasePointerTracking() {
      this._playlistSwipeReleasePointerTracking("brief");
    },

    playlistBriefStartSettle(targetOffset, onDone = null) {
      this._playlistSwipeStartSettle("brief", targetOffset, onDone);
    },

    playlistBriefResetSwipeState() {
      this._playlistSwipeResetState("brief");
    },

    playlistBriefPointerDown(ev) {
      this._playlistSwipePointerDown(
        "brief",
        ev,
        () => (nextEv) => this.playlistBriefPointerMove(nextEv),
        () => (nextEv) => this.playlistBriefPointerEnd(nextEv)
      );
    },

    playlistBriefPointerMove(ev) {
      this._playlistSwipePointerMove("brief", ev);
    },

    playlistBriefPointerEnd(ev) {
      this._playlistSwipePointerEnd("brief", ev, (delta) => {
        if (delta < 0) this.playlistPrevDay({ autoPlay: false });
        else this.playlistNextDay({ autoPlay: false });
      });
    },

    playlistSelectVideoFromList(v, ev = null) {
      if (Date.now() < Number(this.playlistDayListDragSuppressClickUntil || 0)) {
        try {
          if (ev && ev.preventDefault) ev.preventDefault();
        } catch {
          // ignore
        }
        return;
      }
      this.playlistSelectVideo(v, { autoPlay: true });
    },

    playlistGranularity() {
      try {
        const detailGranularity = this.playlistDetail && this.playlistDetail.brief_granularity ? String(this.playlistDetail.brief_granularity) : "";
        const next = (detailGranularity || this.playlistSettingsGranularityDraft || "day").trim().toLowerCase();
        return ["day", "week", "month"].includes(next) ? next : "day";
      } catch {
        return "day";
      }
    },

    playlistBriefTitle() {
      const granularity = this.playlistGranularity();
      if (granularity === "week") return "周报简报";
      if (granularity === "month") return "月报简报";
      return "日报简报";
    },

    playlistPeriodLabel() {
      const granularity = this.playlistGranularity();
      const start = String(this.playlistSelectedDate || "").trim();
      if (!start) return "-";
      const end = periodEndIso(start, granularity);
      if (granularity === "day") return start;
      return `${start} ~ ${end}`;
    },

    _playlistBriefSpeechApi() {
      try {
        if (typeof window === "undefined") return null;
        const synth = window.speechSynthesis;
        return synth && typeof window.SpeechSynthesisUtterance === "function" ? synth : null;
      } catch {
        return null;
      }
    },

    _playlistSyncBriefSpeechSupport() {
      const supported = !!this._playlistBriefSpeechApi();
      this.playlistBriefSpeechSupported = supported;
      return supported;
    },

    _playlistBriefSpeechKeyForSelectedDate() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const g = this.playlistGranularity();
      const day = String(this.playlistSelectedDate || "").trim();
      return this._playlistBriefKey(pid, g, day);
    },

    playlistBriefSpeakingForSelectedDate() {
      const key = this._playlistBriefSpeechKeyForSelectedDate();
      return !!key && this.playlistBriefSpeaking && String(this.playlistBriefSpeakingKey || "") === key;
    },

    playlistBriefCanSpeak() {
      if (this.playlistBriefSpeakingForSelectedDate()) return true;
      if (!this._playlistSyncBriefSpeechSupport()) return false;
      if (this.playlistBriefLoading) return false;
      return !!String(this.playlistBriefSpeechText || "").trim();
    },

    playlistBriefSpeechButtonLabel() {
      return this.playlistBriefSpeakingForSelectedDate() ? "停止" : "朗读";
    },

    playlistBriefSpeechButtonTitle() {
      if (this.playlistBriefSpeakingForSelectedDate()) return "停止朗读当前简报";
      if (!this._playlistSyncBriefSpeechSupport()) return "当前浏览器不支持朗读";
      if (this.playlistBriefLoading) return "简报加载中";
      if (!String(this.playlistBriefSpeechText || "").trim()) return "暂无可朗读内容";
      return "朗读当前简报";
    },

    _playlistResetBriefSpeechState({ clearError = false } = {}) {
      this.playlistBriefSpeaking = false;
      this.playlistBriefSpeakingKey = "";
      this._playlistBriefSpeechUtterance = null;
      if (clearError) this.playlistBriefSpeechError = "";
    },

    playlistStopBriefSpeech({ clearError = true } = {}) {
      const synth = this._playlistBriefSpeechApi();
      this._playlistResetBriefSpeechState({ clearError });
      if (!synth) return;
      try {
        if (synth.speaking || synth.pending) synth.cancel();
      } catch {
        // ignore
      }
    },

    _playlistSetBriefSourceState(state = "", message = "") {
      this.playlistBriefSourceState = String(state || "").trim();
      this.playlistBriefSourceMessage = String(message || "").trim();
    },

    _playlistBriefErrorDetailMessage(error) {
      const msg = error && error.message ? String(error.message) : String(error || "");
      const idx = msg.indexOf(": ");
      return idx >= 0 ? msg.slice(idx + 2).trim() : msg.trim();
    },

    playlistBriefCanCopyPrompt() {
      if (this.playlistBriefPromptCopying) return false;
      if (!this.playlistDetail || !this.playlistSelectedDate) return false;
      return String(this.playlistBriefSourceState || "") === "ready";
    },

    playlistBriefPromptButtonTitle() {
      if (this.playlistBriefPromptCopying) return "提示词复制中…";
      if (!this.playlistDetail || !this.playlistSelectedDate) return "缺少播放列表或日期";
      const state = String(this.playlistBriefSourceState || "");
      if (state === "ready") return "复制简报生成提示词（合并内容）";
      if (state === "no_videos" || state === "no_transcript") {
        return String(this.playlistBriefSourceMessage || "").trim() || "当前周期暂无可复制的提示词";
      }
      if (this.playlistBriefLoading) return "正在检查简报文本";
      return "当前周期暂无可复制的提示词";
    },

    playlistBriefCanGenerate() {
      if (!this.playlistDetail || !this.playlistSelectedDate) return false;
      if (this.playlistBriefGeneratingForSelectedDate()) return false;
      const state = String(this.playlistBriefSourceState || "");
      return state !== "no_videos" && state !== "no_transcript";
    },

    playlistBriefGenerateButtonTitle() {
      const base = `生成${this.playlistBriefTitle()}`;
      if (!this.playlistDetail || !this.playlistSelectedDate) return "缺少播放列表或日期";
      if (this.playlistBriefGeneratingForSelectedDate()) return `${base}中…`;
      const state = String(this.playlistBriefSourceState || "");
      if (state === "no_videos" || state === "no_transcript") {
        return String(this.playlistBriefSourceMessage || "").trim() || base;
      }
      return base;
    },

    _playlistBriefResolveVoice(voices = null) {
      const list = Array.isArray(voices) ? voices.filter(Boolean) : [];
      return (
        list.find((voice) => /^zh[-_]cn$/i.test(String(voice && voice.lang || "").trim())) ||
        list.find((voice) => /^zh[-_]/i.test(String(voice && voice.lang || "").trim())) ||
        list.find((voice) => /^zh/i.test(String(voice && voice.lang || "").trim())) ||
        null
      );
    },

    _briefToSpeechText(md) {
      const src = String(md || "").replace(/\r\n?/g, "\n");
      if (!src.trim()) return "";

      const out = [];
      const lines = src.split("\n");
      for (const rawLine of lines) {
        let line = String(rawLine || "").trim();
        if (!line) {
          if (out.length && out[out.length - 1] !== "") out.push("");
          continue;
        }

        line = line.replace(/(?:[（(]\s*)?来源\s*[:：].*$/u, "");
        line = line.replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1");
        line = line.replace(/^\s*#{1,6}\s+/, "");
        line = line.replace(/^\s*\d+[\).]\s+/, "");
        line = line.replace(/^\s*[-*+]\s+/, "");
        line = line.replace(/^\s*>\s?/, "");
        line = line.replace(/\[([^\]\n]+)\]\(\s*(https?:\/\/[^\s\)）]+)\s*[\)）]+\s*/g, "$1");
        line = line.replace(/\bhttps?:\/\/[^\s\)）]+/g, "");
        line = line.replace(/`([^`]+)`/g, "$1");
        line = line.replace(/\*\*([^*]+)\*\*/g, "$1");
        line = line.replace(/__([^_]+)__/g, "$1");
        line = line.replace(/~~([^~]+)~~/g, "$1");
        line = line.replace(/(^|[\s(（\["'])\*([^*]+)\*(?=[$\s,，。！？!?:：;；)）\]"'])/g, "$1$2");
        line = line.replace(/(^|[\s(（\["'])_([^_]+)_(?=[$\s,，。！？!?:：;；)）\]"'])/g, "$1$2");
        line = line.replace(/[ \t]+/g, " ").trim();
        line = line.replace(/^[,，、;；:：)\]】）]+/u, "").trim();
        if (!line) continue;
        if (!/[。！？!?；;：:]$/u.test(line)) line += "。";
        out.push(line);
      }

      return out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
    },

    playlistSpeakBrief() {
      if (this.playlistBriefSpeakingForSelectedDate()) {
        this.playlistStopBriefSpeech({ clearError: true });
        return;
      }

      this.playlistBriefSpeechError = "";
      if (!this._playlistSyncBriefSpeechSupport()) {
        this.playlistBriefSpeechError = "当前浏览器不支持朗读";
        return;
      }

      const key = this._playlistBriefSpeechKeyForSelectedDate();
      if (!key) return;

      let text = String(this.playlistBriefSpeechText || "").trim();
      if (!text) {
        text = this._briefToSpeechText(this.playlistBriefMarkdown);
        this.playlistBriefSpeechText = text;
      }
      if (!text) {
        this.playlistBriefSpeechError = "当前简报暂无可朗读内容";
        return;
      }

      const synth = this._playlistBriefSpeechApi();
      if (!synth) {
        this.playlistBriefSpeechSupported = false;
        this.playlistBriefSpeechError = "当前浏览器不支持朗读";
        return;
      }

      this.playlistStopBriefSpeech({ clearError: true });

      let utterance;
      try {
        utterance = new window.SpeechSynthesisUtterance(text);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.playlistBriefSpeechError = `朗读初始化失败：${msg}`;
        this.toastError(this.playlistBriefSpeechError);
        return;
      }

      const voice = this._playlistBriefResolveVoice(typeof synth.getVoices === "function" ? synth.getVoices() : []);
      if (voice) utterance.voice = voice;
      utterance.lang = voice && voice.lang ? String(voice.lang) : "zh-CN";
      utterance.onend = () => {
        if (this._playlistBriefSpeechUtterance !== utterance) return;
        this._playlistResetBriefSpeechState({ clearError: true });
      };
      utterance.onerror = (event) => {
        if (this._playlistBriefSpeechUtterance !== utterance) return;
        const detail = event && event.error ? `：${event.error}` : "";
        this._playlistResetBriefSpeechState({ clearError: false });
        this.playlistBriefSpeechError = `朗读失败${detail}`;
        this.toastError(this.playlistBriefSpeechError);
      };

      try {
        this._playlistBriefSpeechUtterance = utterance;
        this.playlistBriefSpeaking = true;
        this.playlistBriefSpeakingKey = key;
        synth.cancel();
        synth.speak(utterance);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this._playlistResetBriefSpeechState({ clearError: false });
        this.playlistBriefSpeechError = `朗读失败：${msg}`;
        this.toastError(this.playlistBriefSpeechError);
      }
    },

    _todayIsoLocal() {
      return todayIsoLocal();
    },

    _weekdayZh(iso) {
      return weekdayZh(iso);
    },

    _mdLabel(iso) {
      return mdLabel(iso);
    },

    _isoParts(iso) {
      return isoParts(iso);
    },

    _periodStartIso(iso, granularity) {
      return periodStartIso(iso, granularity);
    },

    _periodAddIso(periodStart, granularity, delta) {
      return periodAddIso(periodStart, granularity, delta);
    },

    _periodDiff(startIso, otherIso, granularity) {
      return periodDiff(startIso, otherIso, granularity);
    },

    _isoMs(iso) {
      return isoMs(iso);
    },

    _periodClampIso(iso, startIso, endIso) {
      return periodClampIso(iso, startIso, endIso);
    },

    _periodEndIso(periodStart, granularity) {
      return periodEndIso(periodStart, granularity);
    },

    async playlistSetSubview(nextSubview) {
      if (!this.playlistDetail) return;
      const target = ["main", "analysis", "settings"].includes(String(nextSubview || "").trim()) ? String(nextSubview).trim() : "main";
      if (this.playlistSubview === target) return;
      this.playlistCalendarResetDragState();
      this.playlistBriefResetSwipeState();
      this.playlistStopBriefSpeech({ clearError: true });
      if (target !== "main" && typeof this.playlistMediaPause === "function") {
        this.playlistMediaPause();
      }
      if (target !== "analysis") {
        this.playlistAnalysisStopProjectionPlayback();
        this.playlistAnalysisReleaseProjectionWindowDrag();
      }
      this.playlistSubview = target;
      if (this.playlistSubview === "main") {
        try {
          if (this.$nextTick) this.$nextTick(() => this.playlistCalendarUpdateCount());
        } catch {
          // ignore
        }
      } else if (this.playlistSubview === "analysis") {
        await this.playlistLoadAnalysisView();
      } else if (this.playlistSubview === "settings") {
        const [, summary] = await Promise.all([
          this.playlistLoadEventsAllSummary({ silent: true }).catch(() => null),
          this.playlistLoadAnalysisSummary({ silent: true }).catch(() => null),
        ]);
        if (summary && (summary.running || this.playlistAnalysisActiveBackfillJob())) this.playlistAnalysisSchedulePoll();
        else this.playlistAnalysisStopPolling();
      }
      this._syncUrl({ push: false });
    },

    async playlistToggleSubview() {
      const nextSubview = this.playlistSubview === "settings" ? "main" : "settings";
      await this.playlistSetSubview(nextSubview);
    },

    leavePlaylistPage() {
      this.playlistLoadToken = Number(this.playlistLoadToken || 0) + 1;
      this.playlistPeriodCountsToken = Number(this.playlistPeriodCountsToken || 0) + 1;
      this._abortCtrl("_playlistCountsAbortCtrl");
      this._abortCtrl("_playlistDayAbortCtrl");
      this._abortCtrl("_playlistBriefAbortCtrl");
      this._abortCtrl("_playlistBriefMdAbortCtrl");
      this._abortCtrl("_playlistPlayableProbeAbortCtrl");
      this._abortCtrl("_playlistSelectAbortCtrl");
      this._abortCtrl("_playlistTranscriptVariantAbortCtrl");
      this.playlistAnalysisStopPolling();
      this.playlistAnalysisStopSignalsReload();
      this.playlistAnalysisStopTimelineDensityLoad();
      this.playlistAnalysisStopProjectionPlayback();
      this.playlistAnalysisDestroyChart();
      this.playlistAnalysisReleaseRangeDrag();
      this.playlistAnalysisReleaseProjectionWindowDrag();
      try {
        if (typeof this.playlistStopBriefSpeech === "function") {
          this.playlistStopBriefSpeech({ clearError: true });
        }
      } catch {
        // ignore
      }
      this.playlistPendingAutoPlayId = "";
      this.playlistPlayerVideoUrl = "";
      this.playlistPlayerAudioUrl = "";
      this.playlistPlayerError = "";
      this.playlistPlayerNeedsDownload = false;
      this.playlistCurrentVideo = null;
      this.playlistDayVideosLoading = false;
      this.playlistTranscriptLoading = false;
      this.playlistTranscriptError = "";
      this.playlistTranscriptLanguage = "";
      this.playlistTranscriptSource = "";
      this.playlistTranscriptActiveSource = "";
      this.playlistTranscriptVariant = "";
      this.playlistTranscriptSelectedVariant = "";
      this.playlistTranscriptAvailableVariants = emptyPlaylistTranscriptVariants();
      this.playlistTranscriptUpdatedAt = "";
      this.playlistTranscriptSwitching = false;
      this.playlistAnalysisSummary = null;
      this.playlistAnalysisSummaryLoading = false;
      this.playlistAnalysisSummaryError = "";
      this.playlistAnalysisPeriods = [];
      this.playlistAnalysisSignals = [];
      this.playlistAnalysisInvalidateSignalCaches();
      this.playlistAnalysisCandidates = [];
      this.playlistAnalysisCandidatesLoaded = false;
      this.playlistAnalysisCandidatesVersion = 0;
      this.playlistAnalysisBreakpointDisplayMode = "auto";
      this.playlistAnalysisHoveredBreakpointItemId = "";
      this._playlistAnalysisCandidateHydrationRunId = "";
      this.playlistAnalysisSelectedCandidateId = "";
      this.playlistAnalysisCandidateDetail = null;
      this.playlistAnalysisTab = "trend";
      this.playlistAnalysisRangeStart = "";
      this.playlistAnalysisRangeEnd = "";
      this.playlistAnalysisFullRangeStart = "";
      this.playlistAnalysisFullRangeEnd = "";
      this.playlistAnalysisTimelineScope = "normal";
      this.playlistAnalysisTimelineDensity = [];
      this.playlistAnalysisTimelineDensityLoading = false;
      this.playlistAnalysisTimelineDensityLoadedRangeStart = "";
      this.playlistAnalysisTimelineDensityLoadedRangeEnd = "";
      this.playlistAnalysisSignalsLoadedRangeStart = "";
      this.playlistAnalysisSignalsLoadedRangeEnd = "";
      this.playlistAnalysisProjectionWindowStart = "";
      this.playlistAnalysisProjectionWindowEnd = "";
      this.playlistAnalysisProjectionWindowAnchorDate = "";
      this.playlistAnalysisProjectionWindowPreviewStartIndex = null;
      this.playlistAnalysisTimelinePreviewStartIndex = null;
      this.playlistAnalysisTimelinePreviewEndIndex = null;
      this.playlistAnalysisSignalsVersion = 0;
      this.playlistAnalysisProjectionPlaying = false;
      this.playlistAnalysisLoading = false;
      this.playlistAnalysisPeriodsLoading = false;
      this.playlistAnalysisSignalsLoading = false;
      this.playlistAnalysisCandidatesLoading = false;
      this.playlistAnalysisError = "";
      this.playlistAnalysisBackfillSubmitting = false;
      this.playlistAnalysisBackfillCanceling = false;
      this.playlistAnalysisBackfillJob = null;
      this.playlistAnalysisExportOpen = false;
      this.playlistAnalysisExportPayload = null;
      this.playlistMediaDurationSec = 0;
      this.playlistMediaCurrentTimeSec = 0;
      this.playlistMediaPlaying = false;
      try {
        if (typeof this.playlistMediaPause === "function") this.playlistMediaPause();
      } catch {
        // ignore
      }
      try {
        if (typeof this.playlistResetMediaElements === "function") {
          this.playlistResetMediaElements({ cancelAutoPlay: true });
        }
      } catch {
        // ignore
      }
      try {
        if (typeof this.syncSystemMediaSession === "function") {
          this.syncSystemMediaSession({ forcePosition: true });
        }
      } catch {
        // ignore
      }
    },

    playlistPeriodUnitZh() {
      const g = this.playlistGranularity();
      if (g === "week") return "周";
      if (g === "month") return "月";
      return "天";
    },

    playlistPrevPeriodTitle() {
      return `前一${this.playlistPeriodUnitZh()}`;
    },

    playlistNextPeriodTitle() {
      return `后一${this.playlistPeriodUnitZh()}`;
    },

    playlistJumpDelta(kind) {
      const g = this.playlistGranularity();
      const k = String(kind || "").trim();
      if (g === "day") {
        if (k === "back_big") return -30;
        if (k === "back") return -7;
        if (k === "forward") return 7;
        if (k === "forward_big") return 30;
        return 0;
      }
      if (g === "week") {
        if (k === "back_big") return -12;
        if (k === "back") return -1;
        if (k === "forward") return 1;
        if (k === "forward_big") return 12;
        return 0;
      }
      // month
      if (k === "back_big") return -6;
      if (k === "back") return -1;
      if (k === "forward") return 1;
      if (k === "forward_big") return 6;
      return 0;
    },

    playlistJumpTitle(kind) {
      const g = this.playlistGranularity();
      const k = String(kind || "").trim();
      if (g === "day") {
        if (k === "back_big") return "-1月";
        if (k === "back") return "-1周";
        if (k === "forward") return "+1周";
        if (k === "forward_big") return "+1月";
        return "";
      }
      if (g === "week") {
        if (k === "back_big") return "-12周";
        if (k === "back") return "-1周";
        if (k === "forward") return "+1周";
        if (k === "forward_big") return "+12周";
        return "";
      }
      if (k === "back_big") return "-6月";
      if (k === "back") return "-1月";
      if (k === "forward") return "+1月";
      if (k === "forward_big") return "+6月";
      return "";
    },

    playlistCalendarEnsureVisible() {
      const g = this.playlistGranularity();
      const rawStart = String(this.playlistTimelineStart || "").trim();
      const rawEnd = String(this.playlistTimelineEnd || "").trim();
      const start = this._periodStartIso(rawStart, g);
      const end = this._periodStartIso(rawEnd, g);
      const selected = this._periodStartIso(String(this.playlistSelectedDate || "").trim(), g);
      const n = Math.max(5, Number(this.playlistCalendarCount || 14));
      if (!start || !end || !selected) return;

      const maxAnchor = this._periodAddIso(end, g, -(n - 1));
      let anchor = this._periodStartIso(String(this.playlistCalendarAnchor || "").trim(), g);
      if (!anchor) anchor = this._periodAddIso(selected, g, -(n - 1));

      const windowEnd = this._periodAddIso(anchor, g, n - 1);
      const selMs = this._isoMs(selected);
      if (selMs < this._isoMs(anchor)) {
        anchor = selected;
      } else if (selMs > this._isoMs(windowEnd)) {
        anchor = this._periodAddIso(selected, g, -(n - 1));
      }

      if (this._isoMs(maxAnchor) < this._isoMs(start)) {
        anchor = start;
      } else {
        anchor = this._periodClampIso(anchor, start, maxAnchor);
      }
      this.playlistCalendarAnchor = this._periodStartIso(anchor, g);
    },

    playlistCalendarVisibleEnabledRange() {
      const g = this.playlistGranularity();
      const rawStart = String(this.playlistTimelineStart || "").trim();
      const rawEnd = String(this.playlistTimelineEnd || "").trim();
      const start = this._periodStartIso(rawStart, g);
      const end = this._periodStartIso(rawEnd, g);
      const n = Math.max(5, Number(this.playlistCalendarCount || 14));
      if (!start || !end) return null;

      const anchor = this._periodStartIso(String(this.playlistCalendarAnchor || "").trim(), g);
      if (!anchor) return null;

      let winStart = anchor;
      let winEnd = this._periodAddIso(anchor, g, n - 1);

      if (this._isoMs(winStart) < this._isoMs(start)) winStart = start;
      if (this._isoMs(winEnd) > this._isoMs(end)) winEnd = end;
      if (this._isoMs(winEnd) < this._isoMs(winStart)) return null;

      return { start: this._periodStartIso(winStart, g), end: this._periodStartIso(winEnd, g) };
    },

    playlistCalendarCountsRange() {
      const g = this.playlistGranularity();
      const rawStart = String(this.playlistTimelineStart || "").trim();
      const rawEnd = String(this.playlistTimelineEnd || "").trim();
      const start = this._periodStartIso(rawStart, g);
      const end = this._periodStartIso(rawEnd, g);
      const visible = this.playlistCalendarVisibleEnabledRange();
      if (!start || !end || !visible || !visible.start || !visible.end) return null;

      const step = this.playlistCalendarDragStep();
      let rangeStart = this._periodAddIso(visible.start, g, -step);
      let rangeEnd = this._periodAddIso(visible.end, g, step);

      if (this._isoMs(rangeStart) < this._isoMs(start)) rangeStart = start;
      if (this._isoMs(rangeEnd) > this._isoMs(end)) rangeEnd = end;
      if (this._isoMs(rangeEnd) < this._isoMs(rangeStart)) return null;

      return {
        start: this._periodStartIso(rangeStart, g),
        end: this._periodStartIso(rangeEnd, g),
      };
    },

    async playlistPrefetchCalendarCounts() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      const g = this.playlistGranularity();
      const r = this.playlistCalendarCountsRange();
      if (!r || !r.start || !r.end) return;

      const key = `${pid}:${g}:${r.start}:${r.end}`;
      if (String(this.playlistPeriodCountsKey || "") === key) {
        if (this.playlistPeriodCountsLoading) return;
        if (!this.playlistPeriodCountsError) return;
      }

      const nextToken = Number(this.playlistPeriodCountsToken || 0) + 1;
      this.playlistPeriodCountsToken = nextToken;
      const token = nextToken;

      this.playlistPeriodCountsKey = key;
      this.playlistPeriodCountsLoading = true;
      this.playlistPeriodCountsError = "";

      this._abortCtrl("_playlistCountsAbortCtrl");
      const ctrl = new AbortController();
      this._playlistCountsAbortCtrl = ctrl;
      try {
        const rows = await this.api(
          `/playlists/${encodeURIComponent(pid)}/video_counts_by_period?granularity=${encodeURIComponent(g)}&start=${encodeURIComponent(
            r.start
          )}&end=${encodeURIComponent(r.end)}`,
          { signal: ctrl.signal }
        );
        if (Number(this.playlistPeriodCountsToken || 0) !== token) return;

        const next = new Map();
        if (Array.isArray(rows)) {
          for (const row of rows) {
            const ps = row && row.period_start ? String(row.period_start).slice(0, 10) : "";
            const c = row && row.count != null ? Number(row.count) : 0;
            if (!ps || !Number.isFinite(c) || c <= 0) continue;
            next.set(this._periodStartIso(ps, g), Math.floor(c));
          }
        }
        this.playlistPeriodCounts = next;
      } catch (e) {
        if (Number(this.playlistPeriodCountsToken || 0) !== token) return;
        if (this._isAbortError(e)) return;
        this.playlistPeriodCountsError = e && e.message ? e.message : String(e);
      } finally {
        if (Number(this.playlistPeriodCountsToken || 0) === token) this.playlistPeriodCountsLoading = false;
      }
    },

    playlistCalendarDotCount(periodStartIso) {
      try {
        const g = this.playlistGranularity();
        const iso = this._periodStartIso(String(periodStartIso || "").trim(), g);
        if (!iso) return 0;
        const map = this.playlistPeriodCounts;
        const c =
          map && typeof map.get === "function"
            ? Number(map.get(iso) || 0)
            : 0;
        if (!Number.isFinite(c) || c <= 0) return 0;
        if (c <= 3) return 1;
        if (c <= 10) return 2;
        return 3;
      } catch {
        return 0;
      }
    },

    playlistCalendarDots(periodStartIso) {
      const n = this.playlistCalendarDotCount(periodStartIso);
      if (!n) return [];
      try {
        return Array.from({ length: n }, (_, i) => i);
      } catch {
        return [];
      }
    },

    playlistCalendarItemsForAnchor(rawAnchor = null) {
      const g = this.playlistGranularity();
      const rawStart = String(this.playlistTimelineStart || "").trim();
      const rawEnd = String(this.playlistTimelineEnd || "").trim();
      const start = this._periodStartIso(rawStart, g);
      const end = this._periodStartIso(rawEnd, g);
      const selected = this._periodStartIso(String(this.playlistSelectedDate || "").trim(), g);
      const n = Math.max(5, Number(this.playlistCalendarCount || 14));
      if (!start || !end) return [];

      const today = this._periodStartIso(this._todayIsoLocal(), g);
      const anchor = this.playlistCalendarResolvedAnchor(rawAnchor);
      if (!anchor) return [];

      const out = [];
      for (let i = 0; i < n; i++) {
        const date = this._periodAddIso(anchor, g, i);
        const disabled = this._isoMs(date) < this._isoMs(start) || this._isoMs(date) > this._isoMs(end);

        let md = this._mdLabel(date);
        let weekday = this._weekdayZh(date);
        if (g === "week") {
          md = this._mdLabel(date);
          weekday = `~${this._mdLabel(this._periodEndIso(date, "week"))}`;
        } else if (g === "month") {
          const p = this._isoParts(date);
          md = p ? `${p.y}/${String(p.m).padStart(2, "0")}` : date.slice(0, 7);
          weekday = "";
        }

        out.push({
          date,
          md,
          weekday,
          selected: !!selected && date === selected,
          today: date === today,
          disabled,
        });
      }
      return out;
    },

    playlistCalendarPreviewItems() {
      const step = this.playlistCalendarPreviewStep();
      if (!step) return [];
      const g = this.playlistGranularity();
      const anchor = this.playlistCalendarResolvedAnchor();
      if (!anchor) return [];
      return this.playlistCalendarItemsForAnchor(this._periodAddIso(anchor, g, step));
    },

    playlistCalendarItems() {
      return this.playlistCalendarItemsForAnchor();
    },

    playlistTimelineApply() {
      const g = this.playlistGranularity();
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      if (!start || !end) return;
      const v = Number(this.playlistTimelineValue || 0);
      const next = this._periodAddIso(start, g, v);
      this.playlistSetDate(this._periodClampIso(next, start, end));
    },

    playlistSetDate(iso, { autoPlay = true } = {}) {
      if (this.playlistCalendarDragging || this.playlistCalendarSettling || this.playlistCalendarDragPointerId !== null) {
        this.playlistCalendarResetDragState();
      }
      if (this.playlistDayListDragPointerId !== null) this.playlistDayListResetSwipeState();
      if (this.playlistBriefDragPointerId !== null || this.playlistBriefSettling) this.playlistBriefResetSwipeState();
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      const g = this.playlistGranularity();
      const next = this._periodStartIso(String(iso || "").trim(), g);
      if (!next || next === this.playlistSelectedDate) return;
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      const clamped = start && end ? this._periodClampIso(next, start, end) : next;
      this.playlistStopBriefSpeech({ clearError: true });
      this.playlistSelectedDate = clamped;
      this.playlistCalendarEnsureVisible();
      this.playlistPrefetchCalendarCounts();
      if (this.playlistTimelineStart) {
        const max = Number(this.playlistTimelineMax || 0);
        this.playlistTimelineValue = Math.max(0, Math.min(max, this._periodDiff(this.playlistTimelineStart, clamped, g)));
      }
      this._syncUrl({ push: false });
      this.playlistLoadDay(clamped, { autoPlay });
      this.playlistLoadEventsPanel({ silent: true }).catch(() => null);
    },

    playlistTranscriptCacheKey(videoId, { variant = "", source = "" } = {}) {
      const vid = String(videoId || "").trim();
      if (!vid) return "";
      const normalizedVariant = normalizePlaylistTranscriptVariant(variant);
      const normalizedSource = normalizePlaylistTranscriptSource(source);
      if (!normalizedVariant && !normalizedSource) return `${vid}::default`;
      return `${vid}::${normalizedSource || "*"}::${normalizedVariant || "default"}`;
    },

    playlistTranscriptTextAssets(assets = null) {
      const list = Array.isArray(assets) ? assets : [];
      return list.filter(
        (asset) => asset && asset.type === "transcript" && String(asset.format || "").trim().toLowerCase() === "txt"
      );
    },

    playlistTranscriptAvailableVariantsForSource(source = "", assets = null) {
      const sourceValue = normalizePlaylistTranscriptSource(source);
      const available = emptyPlaylistTranscriptVariants();
      const items = this.playlistTranscriptTextAssets(assets);
      for (const asset of items) {
        if (sourceValue && normalizePlaylistTranscriptSource(asset.source) !== sourceValue) continue;
        const variant = normalizePlaylistTranscriptVariant(asset.variant);
        if (variant) available[variant] = true;
      }
      return available;
    },

    playlistTranscriptVariantAvailable(variant) {
      const normalized = normalizePlaylistTranscriptVariant(variant);
      return !!(normalized && this.playlistTranscriptAvailableVariants && this.playlistTranscriptAvailableVariants[normalized]);
    },

    playlistTranscriptVariantActive(variant) {
      const normalized = normalizePlaylistTranscriptVariant(variant);
      const current = normalizePlaylistTranscriptVariant(this.playlistTranscriptSelectedVariant || this.playlistTranscriptVariant);
      return !!normalized && normalized === current;
    },

    playlistTranscriptRequestPath(videoId, { variant = "", source = "" } = {}) {
      const params = new URLSearchParams();
      const normalizedVariant = normalizePlaylistTranscriptVariant(variant);
      const normalizedSource = normalizePlaylistTranscriptSource(source);
      if (normalizedVariant) params.set("variant", normalizedVariant);
      if (normalizedSource) params.set("source", normalizedSource);
      const query = params.toString();
      return `/videos/${encodeURIComponent(videoId)}/transcript${query ? `?${query}` : ""}`;
    },

    playlistApplyTranscriptState(transcript, { assets = null, updateSelection = true } = {}) {
      const payload = transcript && typeof transcript === "object" ? transcript : null;
      const ok = !!(payload && payload.ok);
      const source = normalizePlaylistTranscriptSource(ok ? payload.source : payload && payload.source);
      const variant = normalizePlaylistTranscriptVariant(ok ? payload.variant : payload && payload.variant);
      const availability = this.playlistTranscriptAvailableVariantsForSource(source, assets);

      this.playlistTranscriptText = ok ? payload.text || "" : "";
      this.playlistTranscriptLanguage = ok ? payload.language || "" : "";
      this.playlistTranscriptSource = source;
      this.playlistTranscriptVariant = variant;
      this.playlistTranscriptUpdatedAt = ok ? payload.updated_at || payload.created_at || "" : "";
      this.playlistTranscriptAvailableVariants = availability;

      if (updateSelection) {
        this.playlistTranscriptActiveSource = source;
        this.playlistTranscriptSelectedVariant = variant;
      }
    },

    async playlistSwitchTranscriptVariant(targetVariant) {
      const currentVideo = this.playlistCurrentVideo;
      const selectingId = currentVideo && currentVideo.id ? String(currentVideo.id).trim() : "";
      if (!selectingId || this.playlistTranscriptSwitching) return;

      const nextVariant = normalizePlaylistTranscriptVariant(targetVariant);
      const currentVariant = normalizePlaylistTranscriptVariant(this.playlistTranscriptSelectedVariant || this.playlistTranscriptVariant);
      const source = normalizePlaylistTranscriptSource(this.playlistTranscriptActiveSource || this.playlistTranscriptSource);
      if (!nextVariant || !source || nextVariant === currentVariant) return;

      this.playlistTranscriptAvailableVariants = this.playlistTranscriptAvailableVariantsForSource(
        source,
        this._cacheGet(this.playlistVideoAssetsCache, selectingId) || []
      );
      if (!this.playlistTranscriptVariantAvailable(nextVariant)) return;

      const token = Number(this.playlistLoadToken || 0);
      this._abortCtrl("_playlistTranscriptVariantAbortCtrl");
      const ctrl = new AbortController();
      this._playlistTranscriptVariantAbortCtrl = ctrl;
      this.playlistTranscriptSwitching = true;
      this.playlistTranscriptError = "";

      try {
        const cacheKey = this.playlistTranscriptCacheKey(selectingId, { variant: nextVariant, source });
        let transcript = this._cacheGet(this.playlistVideoTranscriptCache, cacheKey);
        if (!transcript) {
          transcript = await this.api(
            this.playlistTranscriptRequestPath(selectingId, {
              variant: nextVariant,
              source,
            }),
            { signal: ctrl.signal }
          );
          this._cacheSet(this.playlistVideoTranscriptCache, cacheKey, transcript, PLAYLIST_TRANSCRIPT_CACHE_TTL_MS);
        }
        if (Number(this.playlistLoadToken || 0) !== token) return;
        if (!this.playlistCurrentVideo || String(this.playlistCurrentVideo.id || "").trim() !== selectingId) return;
        this.playlistApplyTranscriptState(transcript, {
          assets: this._cacheGet(this.playlistVideoAssetsCache, selectingId) || [],
        });
      } catch (e) {
        if (!this._isAbortError(e)) {
          const msg = e && e.message ? e.message : String(e);
          this.toastError(`切换转写版本失败：${msg}`);
        }
      } finally {
        if (this._playlistTranscriptVariantAbortCtrl === ctrl) this._playlistTranscriptVariantAbortCtrl = null;
        if (Number(this.playlistLoadToken || 0) === token) this.playlistTranscriptSwitching = false;
      }
    },

    async playlistLoadDay(iso, { autoPlay = false } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const g = this.playlistGranularity();
      const day = this._periodStartIso(String(iso || "").trim(), g);
      if (!pid || !day) return;

      const nextToken = Number(this.playlistLoadToken || 0) + 1;
      this.playlistLoadToken = nextToken;
      const loadToken = nextToken;

      this._abortCtrl("_playlistDayAbortCtrl");
      this._abortCtrl("_playlistBriefAbortCtrl");
      this._abortCtrl("_playlistBriefMdAbortCtrl");
      this._abortCtrl("_playlistPlayableProbeAbortCtrl");
      this._abortCtrl("_playlistSelectAbortCtrl");
      this._abortCtrl("_playlistTranscriptVariantAbortCtrl");
      this.playlistStopBriefSpeech({ clearError: true });
      this.playlistResetMediaElements({ cancelAutoPlay: true });

      const prevCurrentId =
        this.playlistCurrentVideo && this.playlistCurrentVideo.id ? String(this.playlistCurrentVideo.id) : "";
      this.playlistPlayerError = "";
      this.playlistPlayerNeedsDownload = false;
      this.playlistPlayerVideoUrl = "";
      this.playlistPlayerAudioUrl = "";
      this.playlistTranscriptText = "";
      this.playlistTranscriptLoading = false;
      this.playlistTranscriptError = "";
      this.playlistTranscriptLanguage = "";
      this.playlistTranscriptSource = "";
      this.playlistTranscriptActiveSource = "";
      this.playlistTranscriptVariant = "";
      this.playlistTranscriptSelectedVariant = "";
      this.playlistTranscriptAvailableVariants = emptyPlaylistTranscriptVariants();
      this.playlistTranscriptUpdatedAt = "";
      this.playlistTranscriptSwitching = false;

      this.playlistDayVideosLoading = true;
      this.playlistDayVideosError = "";
      try {
        const ctrl = new AbortController();
        this._playlistDayAbortCtrl = ctrl;
        const items = await this.api(
          `/playlists/${encodeURIComponent(pid)}/videos_by_period?granularity=${encodeURIComponent(g)}&date=${encodeURIComponent(day)}`,
          { signal: ctrl.signal }
        );
        if (Number(this.playlistLoadToken || 0) !== loadToken) return;
        this.playlistDayVideos = (Array.isArray(items) ? items.slice() : []).sort((a, b) => {
          const diff = this.playlistVideoSortValue(a) - this.playlistVideoSortValue(b);
          if (diff !== 0) return diff;
          return String((a && a.id) || "").localeCompare(String((b && b.id) || ""));
        });
        const keep = prevCurrentId ? this.playlistDayVideos.find((x) => x && String(x.id) === prevCurrentId) : null;
        if (keep) this.playlistCurrentVideo = keep;
        else this.playlistCurrentVideo = this.playlistDayVideos.length ? this.playlistDayVideos[0] : null;
      } catch (e) {
        if (Number(this.playlistLoadToken || 0) !== loadToken) return;
        if (this._isAbortError(e)) return;
        this.playlistDayVideosError = e && e.message ? e.message : String(e);
        this.playlistDayVideos = [];
        this.playlistCurrentVideo = null;
        this.playlistTranscriptText = "";
        this.playlistTranscriptLoading = false;
        this.playlistTranscriptError = "";
        this.playlistTranscriptLanguage = "";
        this.playlistTranscriptSource = "";
        this.playlistTranscriptActiveSource = "";
        this.playlistTranscriptVariant = "";
        this.playlistTranscriptSelectedVariant = "";
        this.playlistTranscriptAvailableVariants = emptyPlaylistTranscriptVariants();
      } finally {
        if (Number(this.playlistLoadToken || 0) === loadToken) this.playlistDayVideosLoading = false;
      }

      if (Number(this.playlistLoadToken || 0) !== loadToken) return;
      if (!this.playlistCurrentVideo) {
        this.playlistPendingAutoPlayId = "";
        this.syncSystemMediaSession({ forcePosition: true });
      }
      const selectPromise = this.playlistCurrentVideo
        ? this.playlistSelectVideo(this.playlistCurrentVideo, {
            autoPlay,
            loadToken,
            fallbackVideos: autoPlay ? this.playlistDayVideos : null,
          })
        : Promise.resolve();
      const briefPromise = this.playlistLoadBrief(day, { loadToken });
      try {
        await Promise.allSettled([selectPromise, briefPromise]);
      } catch {
        // ignore
      }
      if (Number(this.playlistLoadToken || 0) === loadToken) this.syncSystemMediaSession({ forcePosition: true });
    },

    playlistResolveAssetSources(assets) {
      const list = Array.isArray(assets) ? assets : [];
      const videos = list.filter((a) => a && a.type === "video");
      const audios = list.filter((a) => a && a.type === "audio");
      const videoAsset = videos.find((a) => String(a.format || "").toLowerCase() === "mp4") || videos[0] || null;
      const audioAsset = audios.find((a) => String(a.format || "").toLowerCase() === "m4a") || audios[0] || null;
      return {
        list,
        videoAsset,
        audioAsset,
        videoUrl: (videoAsset && this.assetContentUrl(videoAsset)) || "",
        audioUrl: (audioAsset && this.assetContentUrl(audioAsset)) || "",
      };
    },

    playlistModeSourceUrlFromAssets(assets, audioOnly = this.playlistAudioOnly) {
      const resolved = this.playlistResolveAssetSources(assets);
      return String(audioOnly ? resolved.audioUrl : resolved.videoUrl).trim();
    },

    playlistCurrentModeUnavailableMessage(resolvedAssets = null, audioOnly = this.playlistAudioOnly) {
      const assets = resolvedAssets && typeof resolvedAssets === "object" ? resolvedAssets : null;
      const hasVideo = !!String((assets && assets.videoUrl) || "").trim();
      const hasAudio = !!String((assets && assets.audioUrl) || "").trim();
      if (audioOnly) {
        if (hasVideo) return "当前条目暂无可播放音频，可切换到视频模式或发起下载";
        return "当前条目暂无可播放音频，可发起下载";
      }
      if (hasAudio) return "当前条目暂无可播放视频，可切换到音频模式或发起下载";
      return "当前条目暂无可播放视频，可发起下载";
    },

    playlistRefreshCurrentModeAvailability(resolvedAssets = null, audioOnly = this.playlistAudioOnly) {
      const assets =
        resolvedAssets && typeof resolvedAssets === "object"
          ? resolvedAssets
          : {
              videoUrl: this.playlistPlayerVideoUrl || "",
              audioUrl: this.playlistPlayerAudioUrl || "",
            };
      const currentModeUrl = String(audioOnly ? assets.audioUrl || "" : assets.videoUrl || "").trim();
      this.playlistPlayerNeedsDownload = !currentModeUrl;
      if (currentModeUrl) {
        if (String(this.playlistPlayerError || "").startsWith("当前条目暂无可播放")) this.playlistPlayerError = "";
        return currentModeUrl;
      }
      if (!String(this.playlistPlayerError || "").trim()) {
        this.playlistPlayerError = this.playlistCurrentModeUnavailableMessage(assets, audioOnly);
      }
      return "";
    },

    async playlistFindFirstPlayableVideo(videos, { loadToken = null, excludeIds = [], audioOnly = this.playlistAudioOnly } = {}) {
      const items = Array.isArray(videos) ? videos : [];
      if (!items.length) return null;

      const token = Number(loadToken || this.playlistLoadToken || 0);
      const excluded = new Set(
        (Array.isArray(excludeIds) ? excludeIds : [])
          .map((id) => String(id || "").trim())
          .filter(Boolean)
      );

      this._abortCtrl("_playlistPlayableProbeAbortCtrl");
      const ctrl = new AbortController();
      this._playlistPlayableProbeAbortCtrl = ctrl;

      try {
        const assetPresign = assetPresignQueryValue(this.assetDelivery);
        for (const video of items) {
          if (Number(this.playlistLoadToken || 0) !== token) return null;

          const vid = video && video.id ? String(video.id).trim() : "";
          if (!vid || excluded.has(vid)) continue;

          let assets = this._cacheGet(this.playlistVideoAssetsCache, vid);
          if (!assets) {
            try {
              assets = await this.api(`/videos/${encodeURIComponent(vid)}/assets?presign=${assetPresign}&download=0&localize_title=0`, {
                signal: ctrl.signal,
              });
              if (Number(this.playlistLoadToken || 0) !== token) return null;
              this._cacheSet(this.playlistVideoAssetsCache, vid, assets, PLAYLIST_ASSETS_CACHE_TTL_MS);
            } catch (e) {
              if (this._isAbortError(e)) return null;
              continue;
            }
          }

          if (this.playlistModeSourceUrlFromAssets(assets, audioOnly)) return video;
        }
      } finally {
        if (this._playlistPlayableProbeAbortCtrl === ctrl) this._playlistPlayableProbeAbortCtrl = null;
      }

      return null;
    },

    async playlistSelectVideo(v, { autoPlay = false, loadToken = null, fallbackVideos = null } = {}) {
      if (!v) return;
      const vid = String(v.id || "").trim();
      if (!vid) return;
      const token = Number(loadToken || this.playlistLoadToken || 0);
      const fallbackItems = Array.isArray(fallbackVideos) ? fallbackVideos : null;
      if (autoPlay) this.playlistPendingAutoPlayId = vid;
      else if (String(this.playlistPendingAutoPlayId || "").trim() === vid) this.playlistPendingAutoPlayId = "";
      this.playlistCurrentVideo = v;
      this.playlistPlayerError = "";
      this.playlistPlayerNeedsDownload = false;
      const selectingId = vid;
      this.syncSystemMediaSession({ forcePosition: true });

      this._abortCtrl("_playlistSelectAbortCtrl");
      this._abortCtrl("_playlistTranscriptVariantAbortCtrl");
      const ctrl = new AbortController();
      this._playlistSelectAbortCtrl = ctrl;

      this.playlistMediaDurationSec = 0;
      this.playlistMediaCurrentTimeSec = 0;
      this.playlistMediaPlaying = false;
      this.playlistTranscriptSwitching = false;
      this.playlistTranscriptError = "";
      this.playlistTranscriptActiveSource = "";
      this.playlistTranscriptVariant = "";
      this.playlistTranscriptSelectedVariant = "";
      this.playlistTranscriptAvailableVariants = emptyPlaylistTranscriptVariants();

      const isStale = () => {
        if (Number(this.playlistLoadToken || 0) !== token) return true;
        if (!this.playlistCurrentVideo || String(this.playlistCurrentVideo.id || "") !== String(selectingId)) return true;
        return false;
      };

      this.playlistRememberAudioThumbnailSources(v);

      const applyAssets = (assets) => {
        if (isStale()) return;
        const resolved = this.playlistResolveAssetSources(assets);
        const list = resolved.list;
        this.playlistRememberAudioThumbnailSources(v, list);
        this.playlistPlayerVideoUrl = resolved.videoUrl;
        this.playlistPlayerAudioUrl = resolved.audioUrl;
        this.playlistTranscriptAvailableVariants = this.playlistTranscriptAvailableVariantsForSource(
          this.playlistTranscriptActiveSource || this.playlistTranscriptSource,
          list
        );
        this.$nextTick(() => {
          if (isStale()) return;
          this.handleVisibilityMediaPolicy();
          const el = this.playlistMediaElForMode(this.playlistAudioOnly);
          this.playlistEnsureActiveMediaSource(el);
          if (autoPlay) {
            void this.playlistTryAutoPlay({ el });
            this.syncSystemMediaSession({ forcePosition: true });
            return;
          }
          this.playlistSyncMediaState();
        });
        return resolved;
      };

      const applyTranscript = (transcript, assets = null) => {
        if (isStale()) return;
        this.playlistApplyTranscriptState(transcript, { assets, updateSelection: true });
      };

      let resolvedAssets = null;
      const cachedAssets = this._cacheGet(this.playlistVideoAssetsCache, selectingId);
      if (cachedAssets) resolvedAssets = applyAssets(cachedAssets) || resolvedAssets;
      else {
        this.playlistPlayerVideoUrl = "";
        this.playlistPlayerAudioUrl = "";
      }

      const defaultTranscriptCacheKey = this.playlistTranscriptCacheKey(selectingId);
      const cachedTranscript = this._cacheGet(this.playlistVideoTranscriptCache, defaultTranscriptCacheKey);
      if (cachedTranscript) {
        applyTranscript(cachedTranscript, cachedAssets || []);
        this.playlistTranscriptLoading = false;
      } else {
        this.playlistTranscriptText = "";
        this.playlistTranscriptLoading = true;
        this.playlistTranscriptLanguage = "";
        this.playlistTranscriptSource = "";
        this.playlistTranscriptActiveSource = "";
        this.playlistTranscriptVariant = "";
        this.playlistTranscriptSelectedVariant = "";
        this.playlistTranscriptAvailableVariants = emptyPlaylistTranscriptVariants();
        this.playlistTranscriptUpdatedAt = "";
      }

      try {
        const assetPresign = assetPresignQueryValue(this.assetDelivery);
        const assetsPromise = cachedAssets
          ? Promise.resolve(cachedAssets)
          : this.api(`/videos/${encodeURIComponent(vid)}/assets?presign=${assetPresign}&download=0&localize_title=0`, {
              signal: ctrl.signal,
            });
        const transcriptPromise = cachedTranscript
          ? Promise.resolve(cachedTranscript)
          : this.api(`/videos/${encodeURIComponent(vid)}/transcript`, { signal: ctrl.signal });

        const [assetsRes, transcriptRes] = await Promise.allSettled([assetsPromise, transcriptPromise]);

        if (assetsRes.status === "fulfilled") {
          resolvedAssets = applyAssets(assetsRes.value) || resolvedAssets;
          if (!cachedAssets) this._cacheSet(this.playlistVideoAssetsCache, selectingId, assetsRes.value, PLAYLIST_ASSETS_CACHE_TTL_MS);
        } else if (!cachedAssets) {
          const err = assetsRes.reason;
          if (!this._isAbortError(err) && !isStale()) this.playlistPlayerError = err && err.message ? err.message : String(err);
        }

        if (transcriptRes.status === "fulfilled") {
          applyTranscript(transcriptRes.value, (resolvedAssets && resolvedAssets.list) || cachedAssets || []);
          if (!cachedTranscript) {
            this._cacheSet(this.playlistVideoTranscriptCache, defaultTranscriptCacheKey, transcriptRes.value, PLAYLIST_TRANSCRIPT_CACHE_TTL_MS);
          }
        } else if (!cachedTranscript) {
          const err2 = transcriptRes.reason;
          if (!this._isAbortError(err2) && !isStale()) this.playlistTranscriptError = err2 && err2.message ? err2.message : String(err2);
        }
      } catch (e) {
        if (!this._isAbortError(e) && !isStale()) this.playlistPlayerError = e && e.message ? e.message : String(e);
      }

      if (!isStale()) {
        this.playlistRefreshCurrentModeAvailability(resolvedAssets, this.playlistAudioOnly);
      }

      if (!isStale() && autoPlay && fallbackItems && fallbackItems.length > 1) {
        const currentModeUrl = String(
          resolvedAssets ? (this.playlistAudioOnly ? resolvedAssets.audioUrl : resolvedAssets.videoUrl) || "" : ""
        ).trim();
        if (!currentModeUrl) {
          const fallback = await this.playlistFindFirstPlayableVideo(fallbackItems, {
            loadToken: token,
            excludeIds: [selectingId],
            audioOnly: this.playlistAudioOnly,
          });
          if (!isStale()) {
            const fallbackId = fallback && fallback.id ? String(fallback.id).trim() : "";
            if (fallbackId && fallbackId !== selectingId) {
              return this.playlistSelectVideo(fallback, { autoPlay: true, loadToken: token });
            }
          }
        }
      }

      if (!isStale()) {
        this.playlistTranscriptLoading = false;
        if (!this.playlistCurrentVideo && String(this.playlistPendingAutoPlayId || "").trim() === selectingId) {
          this.playlistPendingAutoPlayId = "";
        }
      }
    },

    async playlistTryAutoPlay({ el = null } = {}) {
      const pendingId = String(this.playlistPendingAutoPlayId || "").trim();
      const currentId = this.playlistCurrentVideo && this.playlistCurrentVideo.id ? String(this.playlistCurrentVideo.id) : "";
      if (!pendingId || !currentId || pendingId !== currentId) return false;

      const mediaEl = el || this.playlistActiveMediaEl();
      if (!this.playlistIsActiveMediaEl(mediaEl)) return false;
      if (!mediaEl || typeof mediaEl.play !== "function") return false;

      const src = this.playlistEnsureActiveMediaSource(mediaEl);
      if (!src) return false;

      try {
        const playing = mediaEl.play();
        if (playing && typeof playing.then === "function") await playing;
        this.playlistPendingAutoPlayId = "";
        this.playlistPlayerError = "";
        this.playlistSyncMediaState();
        return true;
      } catch (e) {
        if (this._isAbortError(e)) return false;

        const name = String((e && e.name) || "").trim();
        const msg = e && e.message ? String(e.message) : String(e);

        if (name === "NotAllowedError") {
          this.playlistPendingAutoPlayId = "";
          this.playlistPlayerError = "浏览器阻止自动播放，请手动点击播放";
          this.globalStatus = "error: 浏览器阻止自动播放，请手动点击播放";
          this.playlistSyncMediaState();
          return false;
        }

        try {
          if (Number(mediaEl.readyState || 0) < 2) return false;
        } catch {
          // ignore
        }

        this.playlistPendingAutoPlayId = "";
        this.playlistPlayerError = `自动播放失败：${msg}`;
        this.globalStatus = `error: 自动播放失败：${msg}`;
        this.playlistSyncMediaState();
        return false;
      }
    },

    playlistMaybeAutoPlay(ev = null) {
      const el = ev && ev.target ? ev.target : null;
      if (!this.playlistIsActiveMediaEl(el)) return;
      void this.playlistTryAutoPlay({ el });
    },

    playlistMediaElForMode(audioOnly = this.playlistAudioOnly) {
      try {
        return audioOnly ? this.$refs && this.$refs.playlistAudioEl : this.$refs && this.$refs.playlistVideoEl;
      } catch {
        return null;
      }
    },

    playlistActiveMediaEl() {
      return this.playlistMediaElForMode(this.playlistAudioOnly);
    },

    playlistMediaSourceUrlForMode(audioOnly = this.playlistAudioOnly) {
      return String(audioOnly ? this.playlistPlayerAudioUrl || "" : this.playlistPlayerVideoUrl || "").trim();
    },

    playlistActiveMediaSourceUrl() {
      return this.playlistMediaSourceUrlForMode(this.playlistAudioOnly);
    },

    playlistEnsureMediaSource(el, expectedSrc = "") {
      const mediaEl = el || this.playlistActiveMediaEl();
      const nextSrc = String(expectedSrc || "").trim();
      if (!mediaEl || !nextSrc) return "";

      try {
        const currentAttr = String(mediaEl.getAttribute("src") || "").trim();
        const currentSrc = String(mediaEl.currentSrc || mediaEl.src || "").trim();
        const currentMatches =
          currentAttr === nextSrc || currentSrc === nextSrc || (currentSrc && currentSrc.endsWith(nextSrc));
        if (!currentMatches) {
          mediaEl.src = nextSrc;
          if (typeof mediaEl.load === "function") mediaEl.load();
        }
      } catch {
        // ignore
      }

      return nextSrc;
    },

    playlistEnsureActiveMediaSource(el) {
      const mediaEl = el || this.playlistActiveMediaEl();
      const expectedSrc = this.playlistActiveMediaSourceUrl();
      if (!mediaEl || !expectedSrc) return "";
      return this.playlistEnsureMediaSource(mediaEl, expectedSrc);
    },

    playlistIsActiveMediaEl(el) {
      if (!el) return false;
      try {
        const activeEl = this.playlistActiveMediaEl();
        return !!activeEl && activeEl === el;
      } catch {
        return false;
      }
    },

    playlistMediaElements() {
      const els = [];
      try {
        const videoEl = this.$refs && this.$refs.playlistVideoEl ? this.$refs.playlistVideoEl : null;
        const audioEl = this.$refs && this.$refs.playlistAudioEl ? this.$refs.playlistAudioEl : null;
        if (videoEl) els.push(videoEl);
        if (audioEl) els.push(audioEl);
      } catch {
        // ignore
      }
      return els;
    },

    playlistAnyMediaPlaying() {
      return this.playlistMediaElements().some((el) => {
        try {
          return !!el && !el.paused && !el.ended;
        } catch {
          return false;
        }
      });
    },

    playlistResetMediaElements({ cancelAutoPlay = false } = {}) {
      if (cancelAutoPlay) this.playlistPendingAutoPlayId = "";

      this.playlistMediaElements().forEach((el) => {
        try {
          if (typeof el.pause === "function") el.pause();
        } catch {
          // ignore
        }
        try {
          el.removeAttribute("src");
        } catch {
          // ignore
        }
        try {
          if (typeof el.load === "function") el.load();
        } catch {
          // ignore
        }
      });

      this.playlistMediaPlaying = false;
      this.playlistMediaDurationSec = 0;
      this.playlistMediaCurrentTimeSec = 0;
    },

    playlistCurrentAudioThumbnail() {
      const video = this.playlistCurrentVideo;
      const vid = video && video.id ? String(video.id) : "";
      if (!vid) return { url: "", source: "" };

      const failures = this.playlistAudioThumbnailFailures instanceof Map ? this.playlistAudioThumbnailFailures.get(vid) || {} : {};
      const remembered = this.playlistAudioThumbnailSources instanceof Map ? this.playlistAudioThumbnailSources.get(vid) || {} : {};
      const cachedAssets = this._cacheGet(this.playlistVideoAssetsCache, vid);
      const assets = Array.isArray(cachedAssets) ? cachedAssets : [];

      const rememberedLocalUrl = remembered && remembered.localUrl ? String(remembered.localUrl).trim() : "";
      if (rememberedLocalUrl && !failures.local) return { url: rememberedLocalUrl, source: "local" };

      if (!failures.local) {
        const thumb = assets.find((asset) => asset && asset.type === "thumbnail");
        const localUrl = thumb ? String(this.assetContentUrl(thumb) || "").trim() : "";
        if (localUrl) return { url: localUrl, source: "local" };
      }

      const remoteUrl = remembered && remembered.remoteUrl ? String(remembered.remoteUrl).trim() : video && video.thumbnail_url ? String(video.thumbnail_url).trim() : "";
      if (remoteUrl && !failures.remote) return { url: remoteUrl, source: "remote" };

      return { url: "", source: "" };
    },

    playlistRememberAudioThumbnailSources(video, assets = null) {
      const vid = video && video.id ? String(video.id) : "";
      if (!vid) return;

      const current = this.playlistAudioThumbnailSources instanceof Map ? new Map(this.playlistAudioThumbnailSources) : new Map();
      const prev = current.get(vid) || {};
      const list = Array.isArray(assets) ? assets : [];
      const thumb = list.find((asset) => asset && asset.type === "thumbnail");
      const localUrl = thumb ? String(this.assetContentUrl(thumb) || "").trim() : "";
      const remoteUrl = video && video.thumbnail_url ? String(video.thumbnail_url).trim() : "";

      const next = { ...prev };
      if (localUrl) next.localUrl = localUrl;
      if (remoteUrl) next.remoteUrl = remoteUrl;
      if (!next.localUrl && !next.remoteUrl) return;

      current.set(vid, next);
      this.playlistAudioThumbnailSources = current;
    },

    playlistAudioThumbnailOnError() {
      const video = this.playlistCurrentVideo;
      const vid = video && video.id ? String(video.id) : "";
      if (!vid) return;

      const current = this.playlistCurrentAudioThumbnail();
      const source = String((current && current.source) || "").trim();
      if (!source) return;

      const failures = this.playlistAudioThumbnailFailures instanceof Map ? new Map(this.playlistAudioThumbnailFailures) : new Map();
      const prev = failures.get(vid) || {};
      failures.set(vid, { ...prev, [source]: true });
      this.playlistAudioThumbnailFailures = failures;
      this.syncSystemMediaSession({ forcePosition: true });
    },

    playlistSyncMediaState() {
      const el = this.playlistActiveMediaEl();
      if (!el) {
        this.playlistMediaDurationSec = 0;
        this.playlistMediaCurrentTimeSec = 0;
        this.playlistMediaPlaying = false;
        this.syncSystemMediaSession();
        return;
      }
      const dur = Number(el.duration || 0);
      const cur = Number(el.currentTime || 0);
      this.playlistMediaDurationSec = Number.isFinite(dur) && dur > 0 ? dur : 0;
      this.playlistMediaCurrentTimeSec = Number.isFinite(cur) && cur >= 0 ? cur : 0;
      this.playlistMediaPlaying = !!el && !el.paused && !el.ended;
      this.playlistMediaMuted = !!el.muted;
      const vol = Number(el.volume);
      this.playlistMediaVolume = Number.isFinite(vol) ? Math.max(0, Math.min(1, vol)) : this.playlistMediaVolume;
      this.syncSystemMediaSession();
    },

    playlistMediaOnLoadedMetadata(ev) {
      try {
        const el = ev && ev.target ? ev.target : this.playlistActiveMediaEl();
        if (!this.playlistIsActiveMediaEl(el)) return;
        const dur = Number(el.duration || 0);
        this.playlistMediaDurationSec = Number.isFinite(dur) && dur > 0 ? dur : 0;
        const cur = Number(el.currentTime || 0);
        this.playlistMediaCurrentTimeSec = Number.isFinite(cur) && cur >= 0 ? cur : this.playlistMediaCurrentTimeSec;
        this.playlistMediaPlaying = !!el && !el.paused && !el.ended;
      } catch {
        // ignore
      }
      this.playlistSyncMediaState();
    },

    playlistMediaOnTimeUpdate(ev) {
      try {
        const el = ev && ev.target ? ev.target : this.playlistActiveMediaEl();
        if (!this.playlistIsActiveMediaEl(el)) return;
        const cur = Number(el.currentTime || 0);
        if (Number.isFinite(cur) && cur >= 0) this.playlistMediaCurrentTimeSec = cur;
        this.playlistMediaPlaying = !!el && !el.paused && !el.ended;
      } catch {
        // ignore
      }
      this.syncSystemMediaSession();
    },

    playlistMediaOnPlay(ev) {
      const el = ev && ev.target ? ev.target : this.playlistActiveMediaEl();
      if (!this.playlistIsActiveMediaEl(el)) return;
      this.playlistSyncMediaState();
      const currentId = this.playlistCurrentVideo && this.playlistCurrentVideo.id ? String(this.playlistCurrentVideo.id) : "";
      if (currentId && String(this.playlistPendingAutoPlayId || "").trim() === currentId) this.playlistPendingAutoPlayId = "";
    },

    playlistMediaOnPause(ev) {
      const el = ev && ev.target ? ev.target : this.playlistActiveMediaEl();
      if (!this.playlistIsActiveMediaEl(el)) return;
      this.playlistSyncMediaState();
    },

    playlistMediaOnVolumeChange(ev) {
      try {
        const el = ev && ev.target ? ev.target : this.playlistActiveMediaEl();
        if (!this.playlistIsActiveMediaEl(el)) return;
        this.playlistMediaMuted = !!el.muted;
        const vol = Number(el.volume);
        if (Number.isFinite(vol)) this.playlistMediaVolume = Math.max(0, Math.min(1, vol));
      } catch {
        // ignore
      }
    },

    playlistMediaTimeLabel() {
      const cur = Number(this.playlistMediaCurrentTimeSec || 0);
      const dur = Number(this.playlistMediaDurationSec || 0);
      const left = this.formatDuration(cur) || "0:00";
      const right = this.formatDuration(dur) || "--:--";
      return `${left} / ${right}`;
    },

    playlistMediaProgressPct() {
      const cur = Number(this.playlistMediaCurrentTimeSec || 0);
      const dur = Number(this.playlistMediaDurationSec || 0);
      if (!Number.isFinite(dur) || dur <= 0) return "0";
      const pct = (Math.max(0, Math.min(dur, cur)) / dur) * 100;
      return String(Math.max(0, Math.min(100, pct)));
    },

    playlistMediaVolumePct() {
      const vol = Number(this.playlistMediaVolume);
      const muted = !!this.playlistMediaMuted;
      const v = muted ? 0 : Number.isFinite(vol) ? Math.max(0, Math.min(1, vol)) : 1;
      return String(v * 100);
    },

    playlistMediaPlay() {
      if (String(this.playlistPendingAutoPlayId || "").trim()) this.playlistPendingAutoPlayId = "";

      const el = this.playlistActiveMediaEl();
      if (!el) return;

      this.playlistMediaElements().forEach((mediaEl) => {
        if (!mediaEl || mediaEl === el) return;
        try {
          if (typeof mediaEl.pause === "function") mediaEl.pause();
        } catch {
          // ignore
        }
      });

      try {
        const src = this.playlistEnsureActiveMediaSource(el);
        if (!src) return;
        if (el.ended) el.currentTime = 0;
        if (el.paused || el.ended) el.play();
      } catch {
        // ignore
      }

      this.playlistSyncMediaState();
    },

    playlistMediaPause() {
      if (String(this.playlistPendingAutoPlayId || "").trim()) this.playlistPendingAutoPlayId = "";

      this.playlistMediaElements().forEach((el) => {
        try {
          if (typeof el.pause === "function") el.pause();
        } catch {
          // ignore
        }
      });

      this.playlistSyncMediaState();
    },

    playlistMediaTogglePlay() {
      if (this.playlistAnyMediaPlaying()) {
        this.playlistMediaPause();
        return;
      }

      this.playlistMediaPlay();
    },

    playlistSeekBy(deltaSec) {
      const el = this.playlistActiveMediaEl();
      const delta = Number(deltaSec || 0);
      if (!el || !Number.isFinite(delta) || delta === 0) return;

      try {
        const dur = Number(el.duration || 0);
        const cur = Number(el.currentTime || 0);
        const next = cur + delta;
        if (Number.isFinite(dur) && dur > 0) el.currentTime = Math.max(0, Math.min(dur, next));
        else el.currentTime = Math.max(0, next);
      } catch {
        // ignore
      }
      this.playlistSyncMediaState();
    },

    playlistMediaToggleMute() {
      const el = this.playlistActiveMediaEl();
      if (!el) return;
      try {
        el.muted = !el.muted;
      } catch {
        // ignore
      }
      this.playlistSyncMediaState();
    },

    playlistSeekPointerDown(ev) {
      const bar = ev && ev.currentTarget ? ev.currentTarget : null;
      const el = this.playlistActiveMediaEl();
      const dur = Number(this.playlistMediaDurationSec || 0);
      if (!bar || !el || !Number.isFinite(dur) || dur <= 0) return;

      const update = (e) => {
        try {
          const rect = bar.getBoundingClientRect();
          const x = Number(e && e.clientX);
          if (!rect || !Number.isFinite(x) || rect.width <= 0) return;
          const pct = (x - rect.left) / rect.width;
          const clamped = Math.max(0, Math.min(1, pct));
          const t = dur * clamped;
          el.currentTime = t;
          this.playlistMediaCurrentTimeSec = t;
          this.syncSystemMediaSession();
        } catch {
          // ignore
        }
      };

      const onMove = (e) => update(e);
      const onUp = () => {
        try {
          window.removeEventListener("pointermove", onMove);
        } catch {
          // ignore
        }
      };

      try {
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp, { once: true });
      } catch {
        // ignore
      }
      update(ev);
    },

    playlistVolumePointerDown(ev) {
      const bar = ev && ev.currentTarget ? ev.currentTarget : null;
      const el = this.playlistActiveMediaEl();
      if (!bar || !el) return;

      const update = (e) => {
        try {
          const rect = bar.getBoundingClientRect();
          const x = Number(e && e.clientX);
          if (!rect || !Number.isFinite(x) || rect.width <= 0) return;
          const pct = (x - rect.left) / rect.width;
          const clamped = Math.max(0, Math.min(1, pct));
          el.volume = clamped;
          el.muted = false;
          this.playlistMediaVolume = clamped;
          this.playlistMediaMuted = false;
        } catch {
          // ignore
        }
      };

      const onMove = (e) => update(e);
      const onUp = () => {
        try {
          window.removeEventListener("pointermove", onMove);
        } catch {
          // ignore
        }
      };

      try {
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp, { once: true });
      } catch {
        // ignore
      }
      update(ev);
    },

    playlistToggleAudioOnly() {
      const next = !this.playlistAudioOnly;
      const fromEl = this.playlistMediaElForMode(this.playlistAudioOnly);

      let t = 0;
      let wasPlaying = false;
      try {
        if (fromEl) {
          t = Number(fromEl.currentTime || 0);
          wasPlaying = !fromEl.paused && !fromEl.ended;
          if (typeof fromEl.pause === "function") fromEl.pause();
        }
      } catch {
        // ignore
      }

      this.playlistAudioOnly = next;
      this.$nextTick(() => {
        try {
          const toEl = this.playlistMediaElForMode(next);
          this.playlistEnsureMediaSource(toEl, this.playlistMediaSourceUrlForMode(next));
          if (toEl) {
            if (Number.isFinite(t) && t > 0) toEl.currentTime = t;
            if (wasPlaying && typeof toEl.play === "function") toEl.play();
          }
        } catch {
          // ignore
        }
        this.playlistRefreshCurrentModeAvailability(null, next);
        this.playlistSyncMediaState();
      });
    },

    playlistActivateBackgroundAudio() {
      if (!this.playlistCurrentVideo) return false;

      const audioUrl = String(this.playlistPlayerAudioUrl || "").trim();
      const audioEl = this.playlistMediaElForMode(true);
      if (!audioUrl || !audioEl) return false;

      const fromEl = this.playlistActiveMediaEl();
      let currentTime = 0;
      let wasPlaying = false;
      let muted = !!this.playlistMediaMuted;
      let volume = Number(this.playlistMediaVolume);
      if (!Number.isFinite(volume)) volume = 1;

      try {
        if (fromEl) {
          currentTime = Number(fromEl.currentTime || 0);
          wasPlaying = !fromEl.paused && !fromEl.ended;
          muted = !!fromEl.muted;
          const nextVolume = Number(fromEl.volume);
          if (Number.isFinite(nextVolume)) volume = Math.max(0, Math.min(1, nextVolume));
        }
      } catch {
        // ignore
      }

      this.playlistAudioOnly = true;

      try {
        this.playlistEnsureMediaSource(audioEl, audioUrl);
        audioEl.muted = muted;
        audioEl.volume = volume;
        if (Number.isFinite(currentTime) && currentTime >= 0) audioEl.currentTime = currentTime;
      } catch {
        // ignore
      }

      let playPromise = null;
      if (wasPlaying) {
        try {
          playPromise = typeof audioEl.play === "function" ? audioEl.play() : null;
          if (playPromise && typeof playPromise.catch === "function") {
            playPromise.catch(() => {
              this.playlistSyncMediaState();
            });
          }
        } catch {
          // ignore
        }
      }

      try {
        if (fromEl && fromEl !== audioEl && typeof fromEl.pause === "function") fromEl.pause();
      } catch {
        // ignore
      }

      this.playlistRefreshCurrentModeAvailability(null, true);
      this.playlistSyncMediaState();
      return true;
    },

    playlistCanDownloadCurrentVideo() {
      const video = this.playlistCurrentVideo;
      const videoId = video && video.id ? String(video.id).trim() : "";
      return !!videoId && !!this.playlistPlayerNeedsDownload;
    },

    async playlistDownloadCurrentVideo() {
      const video = this.playlistCurrentVideo;
      const videoId = video && video.id ? String(video.id).trim() : "";
      if (!videoId || this.playlistDownloadSubmitting) return;

      this.playlistDownloadSubmitting = true;
      try {
        const res = await this.api(`/videos/${encodeURIComponent(videoId)}/download`, { method: "POST" });
        const reused = !!(res && res.reused);
        const msg = reused ? "已更新下载任务优先级" : "已添加新下载任务";
        this.playlistPlayerError = "";
        this.playlistPlayerNeedsDownload = false;
        this.globalStatus = msg;
        this.toastSuccess(msg, { action: this.toastJobsAction() });
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.globalStatus = `error: ${msg}`;
        this.toastError(`下载任务提交失败：${msg}`, { action: this.toastJobsAction() });
      } finally {
        this.playlistDownloadSubmitting = false;
      }
    },

    playlistCurrentVideoIndex() {
      const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
      if (!items.length) return -1;
      const id = this.playlistCurrentVideo && this.playlistCurrentVideo.id ? String(this.playlistCurrentVideo.id) : "";
      if (!id) return -1;
      return items.findIndex((x) => x && String(x.id) === id);
    },

    playlistHasPrevVideo() {
      return this.playlistCurrentVideoIndex() > 0;
    },

    playlistHasNextVideo() {
      const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
      const idx = this.playlistCurrentVideoIndex();
      return idx >= 0 && idx < items.length - 1;
    },

    playlistPrevVideo() {
      const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
      const idx = this.playlistCurrentVideoIndex();
      if (!items.length || idx <= 0) return;
      const next = items[idx - 1];
      if (next) this.playlistSelectVideo(next, { autoPlay: true });
    },

    playlistNextVideo() {
      const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
      const idx = this.playlistCurrentVideoIndex();
      if (!items.length || idx < 0 || idx >= items.length - 1) return;
      const next = items[idx + 1];
      if (next) this.playlistSelectVideo(next, { autoPlay: true });
    },

    playlistOnEnded(ev) {
      const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
      if (!items.length) return;

      try {
        const target = ev && ev.target ? ev.target : null;
        const videoEl = this.$refs && this.$refs.playlistVideoEl;
        const audioEl = this.$refs && this.$refs.playlistAudioEl;
        if (this.playlistAudioOnly) {
          if (target && audioEl && target !== audioEl) return;
        } else {
          if (target && videoEl && target !== videoEl) return;
        }
      } catch {
        // ignore
      }

      const id = this.playlistCurrentVideo && this.playlistCurrentVideo.id ? String(this.playlistCurrentVideo.id) : "";
      const idx = id ? items.findIndex((x) => x && String(x.id) === id) : -1;
      if (idx < 0 || idx >= items.length - 1) return;

      const next = items[idx + 1];
      if (next) this.playlistSelectVideo(next, { autoPlay: true });
    },

    playlistPrevDay({ autoPlay = true } = {}) {
      const g = this.playlistGranularity();
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      if (!start || !end || !this.playlistSelectedDate) return;
      const next = this._periodClampIso(this._periodAddIso(this.playlistSelectedDate, g, -1), start, end);
      this.playlistSetDate(next, { autoPlay });
    },

    playlistNextDay({ autoPlay = true } = {}) {
      const g = this.playlistGranularity();
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      if (!start || !end || !this.playlistSelectedDate) return;
      const next = this._periodClampIso(this._periodAddIso(this.playlistSelectedDate, g, 1), start, end);
      this.playlistSetDate(next, { autoPlay });
    },

    playlistJump(deltaPeriods) {
      if (this.playlistCalendarDragging || this.playlistCalendarSettling || this.playlistCalendarDragPointerId !== null) {
        this.playlistCalendarResetDragState();
      }
      const g = this.playlistGranularity();
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      if (!start || !end || !this.playlistSelectedDate) return;
      const delta = Number(deltaPeriods || 0);
      if (!Number.isFinite(delta) || delta === 0) return;
      try {
        const anchor = String(this.playlistCalendarAnchor || "").trim();
        if (anchor) this.playlistCalendarAnchor = this._periodAddIso(anchor, g, delta);
      } catch {
        // ignore
      }
      const next = this._periodClampIso(this._periodAddIso(this.playlistSelectedDate, g, delta), start, end);
      if (next !== this.playlistSelectedDate) {
        this.playlistSetDate(next);
      } else {
        this.playlistCalendarEnsureVisible();
        this.playlistPrefetchCalendarCounts();
      }
    },

    _playlistBriefKey(pid, granularity, periodStart) {
      const p = String(pid || "").trim();
      const g = String(granularity || "").trim();
      const d = String(periodStart || "").trim();
      if (!p || !g || !d) return "";
      return `${p}:${g}:${d}`;
    },

    playlistBriefGeneratingForSelectedDate() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const g = this.playlistGranularity();
      const day = String(this.playlistSelectedDate || "").trim();
      const k = this._playlistBriefKey(pid, g, day);
      return !!k && String(this.playlistBriefGeneratingKey || "") === k;
    },

    async playlistGenerateBriefForSelectedDate() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const g = this.playlistGranularity();
      const day = String(this.playlistSelectedDate || "").trim();
      if (!pid || !day || !this.playlistBriefCanGenerate()) return;
      const k = this._playlistBriefKey(pid, g, day);
      if (k) this.playlistBriefGeneratingKey = k;
      this.playlistStopBriefSpeech({ clearError: true });
      this.playlistBriefError = "";
      this.playlistBriefHtml = "";
      this.playlistBriefMarkdown = "";
      this.playlistBriefSpeechText = "";
      try {
        await this.api(`/briefs/generate`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ playlist_id: pid, granularity: g, date: day }),
        });
        this.globalStatus = "已投递简报生成任务（Jobs 可查看进度）";
        this.toastSuccess("已提交简报生成任务", { action: this.toastJobsAction() });
        this.playlistLoadBrief(day);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.toastError(`简报生成提交失败：${msg}`, { action: this.toastJobsAction() });
        this.globalStatus = `error: ${msg}`;
        if (String(this.playlistBriefGeneratingKey || "") === k) this.playlistBriefGeneratingKey = "";
      }
    },

    async playlistRegenerateAllBriefs() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const g = this.playlistGranularity();
      const detail = this.playlistDetail;
      const from = detail && detail.earliest_date ? String(detail.earliest_date) : "";
      const to = this._todayIsoLocal();
      if (!pid || !from) {
        const msg = "没有可用的日期范围（可能还没同步出视频）";
        this.toastError(msg);
        this.globalStatus = msg;
        return;
      }
      if (
        !confirm(
          `确认重新生成全部简报？\n\n粒度：${g}\n范围：${from} ~ ${to}\n\n该操作会投递大量任务，可在 Jobs 查看进度。`
        )
      )
        return;
      try {
        await this.api(`/briefs/generate_range`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ playlist_id: pid, granularity: g, from_date: from, to_date: to }),
        });
        this.globalStatus = "已投递全量简报生成任务（Jobs 可查看进度）";
        this.toastSuccess("已提交全量简报生成任务", { action: this.toastJobsAction() });
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.toastError(`全量简报提交失败：${msg}`, { action: this.toastJobsAction() });
        this.globalStatus = `error: ${msg}`;
      }
    },

    async playlistDelete() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      const name = (this.playlistDetail && this.playlistDetail.name) || pid;
      if (!confirm(`确认删除播放列表：${name}？\n\n删除后将无法恢复。`)) return;
      try {
        this.playlistStopBriefSpeech({ clearError: true });
        await this.api(`/playlists/${encodeURIComponent(pid)}`, { method: "DELETE" });
        this.globalStatus = "已删除播放列表";
        this.playlistDetail = null;
        this.playlistPageId = null;
        if (this.selectedPlaylistId === pid) this.selectedPlaylistId = null;
        await this.loadPlaylists();
        this.switchView("playlists");
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    _escapeHtml(s) {
      return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#39;");
    },

    _briefToHtml(md) {
      const src = String(md || "");
      const lines = src.split(/\r?\n/);
      const out = [];
      let inList = false;

      const flushList = () => {
        if (!inList) return;
        out.push("</ul>");
        inList = false;
      };

      const formatInlineEsc = (escaped) => {
        let s = String(escaped || "");
        s = s.replace(/`([^`]+)`/g, '<code class="px-1 py-0.5 rounded bg-slate-800/60 text-slate-100 text-[12px]">$1</code>');
        s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        return s;
      };

      const BRIEF_REF_MAX_UNITS = 10;
      const briefRefCharUnits = (ch) => {
        const code = String(ch || "").codePointAt(0) || 0;
        return code > 0x7f ? 2 : 1;
      };
      const briefTruncLabel = (value) => {
        const raw = String(value || "").trim().replace(/\s+/g, " ");
        if (!raw) return "";
        const max = Math.max(1, Number(BRIEF_REF_MAX_UNITS || 0) || 10);
        let used = 0;
        let text = "";
        for (const ch of Array.from(raw)) {
          const units = briefRefCharUnits(ch);
          if (used + units > max) break;
          used += units;
          text += ch;
        }
        return text === raw ? raw : `${text || "…"}${text ? "…" : ""}`;
      };

      const briefUrlTitle = (url) => {
        try {
          const u = String(url || "").trim();
          if (!u) return "";
          const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
          const hit = items.find((v) => v && String(v.url || "").trim() === u);
          const title = hit && hit.title ? String(hit.title).trim() : "";
          return title;
        } catch {
          return "";
        }
      };

      const briefLooksLikeUrl = (value) => /^https?:\/\//i.test(String(value || "").trim());

      const briefRefPill = ({ label, url }) => {
        const u = String(url || "").trim();
        if (!u) return "";
        const enc = encodeURIComponent(u);
        const safeUrl = this._escapeHtml(u);
        const title = briefUrlTitle(u);
        const rawLabel = String(label || "").trim();
        const rawText = title || (rawLabel && !briefLooksLikeUrl(rawLabel) ? rawLabel : "") || "视频...";
        const text = briefTruncLabel(rawText) || "视频...";
        const safeText = formatInlineEsc(this._escapeHtml(text));
        const safeTitle = this._escapeHtml(title || rawText || u);
        return [
          '<span class="inline-flex items-stretch rounded-md border border-slate-700 bg-slate-950/30 overflow-hidden align-middle ml-1 mr-1">',
          `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" title="${safeTitle}" class="min-w-0 max-w-xs pl-1.5 pr-1 py-0.5 text-[11px] text-slate-200 hover:bg-slate-800/60 truncate no-underline">${safeText}</a>`,
          `<button type="button" class="shrink-0 pl-1.5 pr-1.5 py-0.5 border-l border-slate-700 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20 text-[11px]" data-play-url="${enc}" title="播放该视频">▶</button>`,
          "</span>",
        ].join("");
      };

      const stripSourcePrefix = (s) => {
        const raw = String(s || "");
        const stripped = raw.replace(/(?:[（(]\s*)?来源\s*[:：]\s*$/u, "");
        return { text: stripped, stripped: stripped !== raw };
      };

      let briefSourceCarry = false;
      const linkifyAndFormat = (rawText) => {
        const raw = String(rawText || "");
        const re = /\[([^\]\n]+)\]\(\s*(https?:\/\/[^\s,，、;；\)）]+)\s*[\)）]+\s*|\b(https?:\/\/[^\s,，、;；\)）]+)\b/g;
        let last = 0;
        let html = "";
        let inSourceGroup = briefSourceCarry;
        for (const m of raw.matchAll(re)) {
          const idx = m.index ?? 0;
          const beforeRaw = raw.slice(last, idx);
          const { text: beforeStripped, stripped } = stripSourcePrefix(beforeRaw);
          if (stripped) inSourceGroup = true;

          let beforeOut = beforeStripped;
          if (inSourceGroup) {
            const t = String(beforeOut || "").trim();
            if (t === "，" || t === "," || t === "、" || t === ";" || t === "；") beforeOut = "";
          }
          html += formatInlineEsc(this._escapeHtml(beforeOut));

          if (m[1] && m[2]) html += briefRefPill({ label: m[1], url: m[2] });
          else if (m[3]) html += briefRefPill({ label: "", url: m[3] });

          let nextLast = idx + m[0].length;
          if (inSourceGroup) {
            const tail = raw.slice(nextLast);
            const close = tail.match(/^(\s*[)）])/);
            if (close) {
              nextLast += close[0].length;
              inSourceGroup = false;
            }
          }
          last = nextLast;
        }
        html += formatInlineEsc(this._escapeHtml(raw.slice(last)));
        briefSourceCarry = inSourceGroup;
        return html;
      };

      for (const rawLine of lines) {
        let line = rawLine || "";
        let trimmed = line.trim();
        if (/(?:[（(]\s*)?来源\s*[:：]\s*$/u.test(trimmed)) {
          line = line.replace(/(?:[（(]\s*)?来源\s*[:：]\s*$/u, "");
          trimmed = line.trim();
          briefSourceCarry = true;
          if (!trimmed) continue;
        }
        if (/^[)）]\s*$/.test(trimmed)) {
          briefSourceCarry = false;
          continue;
        }
        if (!trimmed) {
          flushList();
          out.push("<div class=\"h-2\"></div>");
          continue;
        }
        const m = trimmed.match(/^(#{1,4})\s+(.*)$/);
        if (m) {
          flushList();
          const level = m[1].length;
          const body = linkifyAndFormat(m[2] || "");
          out.push(`<h${level} class="mt-2">${body}</h${level}>`);
          continue;
        }
        const m2 = trimmed.match(/^\d+[\).]\s+(.*)$/);
        if (m2) {
          flushList();
          const body = linkifyAndFormat(m2[1] || "");
          out.push(`<h3 class="mt-3">${body}</h3>`);
          continue;
        }
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
	          if (!inList) {
	            out.push('<ul class="list-disc pl-5 space-y-2 leading-relaxed">');
	            inList = true;
	          }
	          const body = linkifyAndFormat(trimmed.slice(2));
	          out.push(`<li>${body}</li>`);
	          continue;
	        }
	        flushList();
	        out.push(`<p class="my-2 leading-relaxed">${linkifyAndFormat(line)}</p>`);
	      }
      flushList();
      return out.join("");
    },

    playlistBriefClick(ev) {
      try {
        if (Date.now() < Number(this.playlistBriefDragSuppressClickUntil || 0)) {
          ev.preventDefault();
          ev.stopPropagation();
          return;
        }
        const btn = ev && ev.target && ev.target.closest ? ev.target.closest("button[data-play-url]") : null;
        if (!btn) return;
        const enc = btn.getAttribute("data-play-url") || "";
        const url = decodeURIComponent(enc);
        this.playlistSelectVideoByUrl(url);
        ev.preventDefault();
        ev.stopPropagation();
      } catch {
        // ignore
      }
    },

    playlistBriefCanCopy() {
      const md = String(this.playlistBriefMarkdown || "").trim();
      if (md) return true;
      const html = String(this.playlistBriefHtml || "").trim();
      return !!html;
    },

    _playlistBriefTextFromDom() {
      try {
        const el = this.$refs && this.$refs.playlistBriefContentEl ? this.$refs.playlistBriefContentEl : null;
        if (!el) return "";
        const clone = el.cloneNode(true);
        try {
          const btns = clone.querySelectorAll ? clone.querySelectorAll("button[data-play-url]") : [];
          for (const b of btns || []) {
            try {
              b.remove();
            } catch {
              // ignore
            }
          }
        } catch {
          // ignore
        }
        const text = String(clone.innerText || clone.textContent || "").trim();
        return text;
      } catch {
        return "";
      }
    },

    async _copyToClipboard(text) {
      const s = String(text || "");
      if (!s.trim()) throw new Error("empty");
      try {
        if (navigator && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
          await navigator.clipboard.writeText(s);
          return;
        }
      } catch {
        // fallback below
      }
      const ta = document.createElement("textarea");
      ta.value = s;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.top = "0";
      ta.style.left = "0";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      try {
        ta.select();
        const ok = document.execCommand("copy");
        if (!ok) throw new Error("copy failed");
      } finally {
        try {
          document.body.removeChild(ta);
        } catch {
          // ignore
        }
      }
    },

	    async playlistCopyBriefText() {
	      if (this.playlistBriefCopying) return;
	      this.playlistBriefCopying = true;
	      try {
        let text = String(this.playlistBriefMarkdown || "").trim();
        if (!text) text = this._playlistBriefTextFromDom();
        if (!text) throw new Error("没有可复制的简报内容");
        await this._copyToClipboard(text);
        this.toastSuccess("已复制简报文本");
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.toastError(`复制失败：${msg}`);
	      } finally {
	        this.playlistBriefCopying = false;
	      }
	    },

	    async playlistCopyBriefPrompt() {
	      if (this.playlistBriefPromptCopying || !this.playlistBriefCanCopyPrompt()) return;
	      this.playlistBriefPromptCopying = true;
	      try {
	        const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
	        const g = this.playlistGranularity();
	        const day = String(this.playlistSelectedDate || "").trim();
	        if (!pid || !day) throw new Error("缺少播放列表或日期");
	        const data = await this.api(
	          `/briefs/prompt_by_period?playlist_id=${encodeURIComponent(pid)}&granularity=${encodeURIComponent(g)}&date=${encodeURIComponent(day)}`
	        );
	        const prompt = data && data.prompt ? String(data.prompt) : "";
	        if (!prompt.trim()) throw new Error("提示词为空");
          this._playlistSetBriefSourceState("ready", "");
	        await this._copyToClipboard(prompt);
	        this.toastSuccess("已复制简报提示词");
	      } catch (e) {
	        const msg = e && e.message ? e.message : String(e);
          const detail = this._playlistBriefErrorDetailMessage(e);
          if (detail.includes("暂无视频")) this._playlistSetBriefSourceState("no_videos", detail);
          else if (detail.includes("无可用文本")) this._playlistSetBriefSourceState("no_transcript", detail);
	        this.toastError(`复制失败：${msg}`);
	      } finally {
	        this.playlistBriefPromptCopying = false;
	      }
	    },

    async _playlistDetectBriefSource(pid, granularity, day) {
      try {
        const data = await this.api(
          `/briefs/prompt_by_period?playlist_id=${encodeURIComponent(pid)}&granularity=${encodeURIComponent(granularity)}&date=${encodeURIComponent(day)}`
        );
        const prompt = data && data.prompt ? String(data.prompt) : "";
        if (!prompt.trim()) return { state: "unknown", message: "" };
        return { state: "ready", message: "" };
      } catch (e) {
        if (this._isAbortError(e)) throw e;
        const detail = this._playlistBriefErrorDetailMessage(e);
        if (detail.includes("暂无视频")) return { state: "no_videos", message: detail };
        if (detail.includes("无可用文本")) return { state: "no_transcript", message: detail };
        return { state: "unknown", message: detail };
      }
    },

	    playlistSelectVideoByUrl(url) {
	      const u = String(url || "").trim();
	      if (!u) return;
      const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
      const found = items.find((v) => v && String(v.url || "").trim() === u);
      if (found) {
        this.playlistSelectVideo(found, { autoPlay: true });
        return;
      }
      this.globalStatus = "该链接不在本周期视频列表中";
    },

    async playlistLoadBrief(day, { loadToken = null } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const g = this.playlistGranularity();
      const d = String(day || "").trim();
      if (!pid || !d) return;
      const token = Number(loadToken || this.playlistLoadToken || 0);
      const k = this._playlistBriefKey(pid, g, d);
      const manualGenerating = !!k && String(this.playlistBriefGeneratingKey || "") === k;
      this.playlistStopBriefSpeech({ clearError: true });
      if (!manualGenerating) {
        const cached = this._cacheGet(this.playlistBriefHtmlCache, k);
        if (cached && Number(this.playlistLoadToken || 0) === token) {
          this.playlistBriefLoading = false;
          this.playlistBriefError = "";
          this._playlistSetBriefSourceState("ready", "");
          this.playlistBriefHtml = cached;
          const cachedMd = this._cacheGet(this.playlistBriefMarkdownCache, k);
          this.playlistBriefMarkdown = cachedMd ? String(cachedMd) : "";
          this.playlistBriefSpeechText = cachedMd ? this._briefToSpeechText(cachedMd) : "";
          return;
        }
      }
      this.playlistBriefLoading = true;
      this.playlistBriefError = "";
      this.playlistBriefHtml = "";
      this.playlistBriefMarkdown = "";
      this.playlistBriefSpeechText = "";
      this._playlistSetBriefSourceState("", "");
      try {
        this._abortCtrl("_playlistBriefAbortCtrl");
        this._abortCtrl("_playlistBriefMdAbortCtrl");
        const ctrl = new AbortController();
        this._playlistBriefAbortCtrl = ctrl;
        const brief = await this.api(
          `/briefs/by_period?playlist_id=${encodeURIComponent(pid)}&granularity=${encodeURIComponent(g)}&date=${encodeURIComponent(d)}`,
          { signal: ctrl.signal }
        );
        if (Number(this.playlistLoadToken || 0) !== token) return;
        if (!brief || brief.status !== "ready" || !brief.markdown_asset) {
          const s = brief && brief.status ? String(brief.status) : "pending";
          if (s === "empty") {
            this._playlistSetBriefSourceState("no_videos", "本周期暂无视频");
            this.playlistBriefHtml = `<div class="text-slate-400 text-sm">本周期暂无视频，不生成简报</div>`;
            this.playlistBriefMarkdown = "";
            this.playlistBriefSpeechText = "";
            if (manualGenerating) this.playlistBriefGeneratingKey = "";
            return;
          }
          if (s === "failed") {
            const em = brief && brief.error_message ? String(brief.error_message) : "";
            if (em.includes("无可用文本")) this._playlistSetBriefSourceState("no_transcript", em);
            else this._playlistSetBriefSourceState("unknown", em);
            this.playlistBriefHtml = `<div class="text-rose-200 text-sm">生成失败${em ? `：${this._escapeHtml(em)}` : ""}</div>`;
            this.playlistBriefSpeechText = "";
            if (manualGenerating) this.playlistBriefGeneratingKey = "";
            return;
          }
          const source = await this._playlistDetectBriefSource(pid, g, d);
          if (Number(this.playlistLoadToken || 0) !== token) return;
          this._playlistSetBriefSourceState(source.state, source.message);
          if (source.state === "no_videos") {
            this.playlistBriefHtml = `<div class="text-slate-400 text-sm">本周期暂无视频，不生成简报</div>`;
            this.playlistBriefSpeechText = "";
            if (manualGenerating) this.playlistBriefGeneratingKey = "";
            return;
          }
          if (source.state === "no_transcript") {
            this.playlistBriefHtml = `<div class="text-slate-400 text-sm">本周期暂无可用文本（字幕/文字稿缺失）</div>`;
            this.playlistBriefSpeechText = "";
            if (manualGenerating) this.playlistBriefGeneratingKey = "";
            return;
          }
          if (manualGenerating) {
            this.playlistBriefHtml = `<div class="text-slate-400 text-sm">生成中…</div>`;
          } else {
            this.playlistBriefHtml = `<div class="text-slate-400 text-sm">简报状态：${this._escapeHtml(s)}</div>`;
          }
          if (source.state === "ready") {
            this._playlistEnsureBriefEnqueued(pid, g, d);
            this._playlistPollBrief(pid, g, d);
          }
          return;
        }
        const mdCtrl = new AbortController();
        this._playlistBriefMdAbortCtrl = mdCtrl;
        const markdownUrl = this.assetContentUrl(brief.markdown_asset);
        const resp = await this.fetchWithApiAuth(markdownUrl, { signal: mdCtrl.signal });
        if (resp.status === 401) this.handleApiUnauthorized({});
        if (!resp.ok) throw new Error(`${resp.status}: brief markdown fetch failed`);
        const md = await resp.text();
        if (Number(this.playlistLoadToken || 0) !== token) return;
        this._playlistSetBriefSourceState("ready", "");
        this.playlistBriefMarkdown = md;
        this.playlistBriefSpeechText = this._briefToSpeechText(md);
        this.playlistBriefHtml = this._briefToHtml(md);
        this._cacheSet(this.playlistBriefHtmlCache, k, this.playlistBriefHtml, PLAYLIST_BRIEF_CACHE_TTL_MS);
        this._cacheSet(this.playlistBriefMarkdownCache, k, md, PLAYLIST_BRIEF_CACHE_TTL_MS);
        if (manualGenerating) this.playlistBriefGeneratingKey = "";
        try {
          const k = this._playlistBriefKey(pid, g, d);
          if (this.playlistBriefAutoPoll) this.playlistBriefAutoPoll.delete(k);
        } catch {}
      } catch (e) {
        if (Number(this.playlistLoadToken || 0) !== token) return;
        if (this._isAbortError(e)) return;
        const msg = e && e.message ? e.message : String(e);
        if (String(msg).startsWith("404:") || String(msg).includes(" 404")) {
          const source = await this._playlistDetectBriefSource(pid, g, d);
          if (Number(this.playlistLoadToken || 0) !== token) return;
          this._playlistSetBriefSourceState(source.state, source.message);
          if (source.state === "no_videos") {
            this.playlistBriefHtml = `<div class="text-slate-400 text-sm">本周期暂无视频，不生成简报</div>`;
            this.playlistBriefSpeechText = "";
            if (manualGenerating) this.playlistBriefGeneratingKey = "";
            return;
          }
          if (source.state === "no_transcript") {
            this.playlistBriefHtml = `<div class="text-slate-400 text-sm">本周期暂无可用文本（字幕/文字稿缺失）</div>`;
            this.playlistBriefSpeechText = "";
            if (manualGenerating) this.playlistBriefGeneratingKey = "";
            return;
          }
          this.playlistBriefHtml = manualGenerating
            ? `<div class="text-slate-400 text-sm">生成中…</div>`
            : `<div class="text-slate-400 text-sm">暂无简报，已自动触发生成…</div>`;
          if (source.state === "ready") {
            this._playlistEnsureBriefEnqueued(pid, g, d);
            this._playlistPollBrief(pid, g, d);
          }
        } else {
          this._playlistSetBriefSourceState("unknown", msg);
          this.playlistBriefError = msg;
        }
      } finally {
        if (Number(this.playlistLoadToken || 0) === token) this.playlistBriefLoading = false;
      }
    },

    _playlistEnsureBriefEnqueued(pid, granularity, day) {
      try {
        const g = String(granularity || "").trim() || "day";
        const k = this._playlistBriefKey(pid, g, day);
        if (this.playlistBriefAutoRequests && this.playlistBriefAutoRequests.has(k)) return;
        if (!this.playlistBriefAutoRequests) this.playlistBriefAutoRequests = new Set();
        this.playlistBriefAutoRequests.add(k);
        this.api(`/briefs/generate`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ playlist_id: pid, granularity: g, date: day }),
        }).catch(() => {});
      } catch {
        // ignore
      }
    },

    _playlistPollBrief(pid, granularity, day) {
      try {
        const g = String(granularity || "").trim() || "day";
        const k = this._playlistBriefKey(pid, g, day);
        if (!this.playlistBriefAutoPoll) this.playlistBriefAutoPoll = new Map();
        const tries = Number(this.playlistBriefAutoPoll.get(k) || 0);
        const manual = String(this.playlistBriefGeneratingKey || "") === k;
        const maxTries = manual ? 60 : 8;
        if (tries >= maxTries) return;
        this.playlistBriefAutoPoll.set(k, tries + 1);
        const rawDelay = 1200 + tries * 700;
        const delay = manual ? Math.min(8000, rawDelay) : rawDelay;
        setTimeout(() => {
          if (String(this.playlistPageId || this.selectedPlaylistId || "").trim() !== String(pid)) return;
          if (this.playlistGranularity() !== g) return;
          if (String(this.playlistSelectedDate || "").trim() !== String(day)) return;
          this.playlistLoadBrief(day);
        }, delay);
      } catch {
        // ignore
      }
    },

    async playlistUploadAvatar(ev) {
      try {
        const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
        const f = ev && ev.target && ev.target.files && ev.target.files[0] ? ev.target.files[0] : null;
        if (!pid || !f) return;
        if (f.size > 2 * 1024 * 1024) throw new Error("头像超过 2MB");
        const updated = await this._uploadPlaylistImage(pid, "avatar", f);
        if (this.playlistDetail) this.playlistDetail.avatar_asset = updated.avatar_asset || this.playlistDetail.avatar_asset;
        await this.loadPlaylists();
        this.globalStatus = "已更新头像";
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      } finally {
        try {
          if (ev && ev.target) ev.target.value = "";
        } catch {}
      }
    },

    async playlistUploadBackground(ev) {
      try {
        const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
        const f = ev && ev.target && ev.target.files && ev.target.files[0] ? ev.target.files[0] : null;
        if (!pid || !f) return;
        if (f.size > 2 * 1024 * 1024) throw new Error("背景超过 2MB");
        const updated = await this._uploadPlaylistImage(pid, "background", f);
        if (this.playlistDetail) this.playlistDetail.background_asset = updated.background_asset || this.playlistDetail.background_asset;
        await this.loadPlaylists();
        this.globalStatus = "已更新背景";
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      } finally {
        try {
          if (ev && ev.target) ev.target.value = "";
        } catch {}
      }
    },

    async playlistClearBackground() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid || !this.playlistDetail) return;
      try {
        const updated = await this.api(`/playlists/${encodeURIComponent(pid)}/background`, { method: "DELETE" });
        if (this.playlistDetail) this.playlistDetail.background_asset = (updated && updated.background_asset) || null;
        await this.loadPlaylists();
        this.globalStatus = "已清除背景";
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    playlistSettingsResetPrompt() {
      this.playlistSettingsPromptError = "";
      this.playlistSettingsPromptDraft = this.briefDefaultDailyPrompt();
    },

    async playlistSettingsSavePrompt() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid || !this.playlistDetail) return;
      if (this.playlistSettingsPromptSaving) return;
      try {
        this.playlistSettingsPromptSaving = true;
        this.playlistSettingsPromptError = "";
        const v = String(this.playlistSettingsPromptDraft || "").trim();
        const payload = { brief_prompt: v ? v : null };
        await this.api(`/playlists/${encodeURIComponent(pid)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (this.playlistDetail) this.playlistDetail.brief_prompt = v || null;
        this.globalStatus = "已保存播放列表提示词";
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.playlistSettingsPromptError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.playlistSettingsPromptSaving = false;
      }
    },

    async playlistSettingsSaveGranularity() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid || !this.playlistDetail) return;
      if (this.playlistSettingsGranularitySaving) return;
      const g = String(this.playlistSettingsGranularityDraft || "day")
        .trim()
        .toLowerCase();
      if (!["day", "week", "month"].includes(g)) {
        this.playlistSettingsGranularityError = "无效的聚合粒度";
        return;
      }
      try {
        this.playlistSettingsGranularitySaving = true;
        this.playlistSettingsGranularityError = "";
        await this.api(`/playlists/${encodeURIComponent(pid)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ brief_granularity: g }),
        });
        if (this.playlistDetail) this.playlistDetail.brief_granularity = g;
        this.globalStatus = "已保存聚合粒度";
        await this.loadPlaylistPage();
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.playlistSettingsGranularityError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.playlistSettingsGranularitySaving = false;
      }
    },

    playlistEventStatusOptions() {
      return [
        { key: "", label: "全部" },
        { key: "accepted", label: "Accepted" },
        { key: "draft", label: "Draft" },
        { key: "rejected", label: "Rejected" },
      ];
    },

    playlistEventStatusClass(status) {
      const value = String(status || "").toLowerCase();
      if (value === "accepted") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
      if (value === "rejected") return "border-rose-500/30 bg-rose-500/10 text-rose-200";
      return "border-amber-500/30 bg-amber-500/10 text-amber-200";
    },

    playlistEventProvenanceLabel(event) {
      const status = String((event && event.provenance_status) || "").trim().toLowerCase();
      const count = Number(event && event.evidence_count);
      const countText = Number.isFinite(count) && count > 0 ? ` ${Math.trunc(count)}` : "";
      if (status === "verified") return `证据已定位${countText}`;
      if (status === "unverified") return `证据未校验${countText}`;
      return "缺少证据";
    },

    playlistEventProvenanceClass(event) {
      const status = String((event && event.provenance_status) || "").trim().toLowerCase();
      if (status === "verified") return "border-sky-500/30 bg-sky-500/10 text-sky-200";
      if (status === "unverified") return "border-amber-500/30 bg-amber-500/10 text-amber-200";
      return "border-rose-500/30 bg-rose-500/10 text-rose-200";
    },

    playlistEventSourceLabel(event) {
      const media = String((event && event.source_media_name) || "").trim();
      const title = String((event && event.source_video_title) || "").trim();
      if (media && title) return `${media} · ${title}`;
      return title || media || "";
    },

    playlistEventDateOnly(value) {
      const text = String(value || "").trim();
      return text ? text.slice(0, 10) : "";
    },

    playlistEventPrecisionLabel(precision) {
      const value = String(precision || "").trim().toLowerCase();
      if (value === "year") return "年";
      if (value === "month") return "月";
      if (value === "day") return "日";
      if (value === "second") return "秒";
      if (value === "range") return "区间";
      return "未知";
    },

    playlistEventDateLabel(event) {
      const eventDate = this.playlistEventDateOnly(event && event.event_time_start);
      const precision = String((event && event.time_precision) || "").trim().toLowerCase();
      if (eventDate) {
        if (precision === "year") return `${eventDate.slice(0, 4)}年`;
        if (precision === "month") return eventDate.slice(0, 7);
        return eventDate;
      }

      const availableDate = this.playlistEventDateOnly(event && event.available_at);
      return availableDate ? `观测 ${availableDate}` : "unknown";
    },

    playlistEventDateTitle(event) {
      const eventDate = this.playlistEventDateOnly(event && event.event_time_start);
      const availableDate = this.playlistEventDateOnly(event && event.available_at);
      const precision = this.playlistEventPrecisionLabel(event && event.time_precision);
      if (eventDate) {
        const target = this.playlistEventDateLabel(event);
        return availableDate ? `事件目标时间：${target}（精度：${precision}）；可观察时间：${availableDate}` : `事件目标时间：${target}（精度：${precision}）`;
      }
      if (availableDate) return `事件时间未解析；可观察时间：${availableDate}`;
      return "事件时间未解析";
    },

    playlistEventEntitiesLabel(event) {
      const entities = Array.isArray(event && event.entities) ? event.entities : [];
      return entities.slice(0, 4).map((item) => item.name).filter(Boolean).join(" · ");
    },

    playlistEventEntitiesPreview(event) {
      const entities = Array.isArray(event && event.entities) ? event.entities : [];
      return entities.slice(0, 6);
    },

    playlistEventEntityTypeLabel(entity) {
      return String((entity && (entity.entity_type || entity.type)) || "").trim();
    },

    playlistEventEntityNameLabel(entity) {
      return String((entity && entity.name) || "").trim();
    },

    playlistEventEntityTagTitle(entity) {
      const type = this.playlistEventEntityTypeLabel(entity);
      const name = this.playlistEventEntityNameLabel(entity);
      return [type, name].filter(Boolean).join(" ");
    },

    playlistEventSummaryLine(event) {
      return String((event && (event.summary || event.title)) || "").trim();
    },

    playlistEventEntityLabelById(eventDetail, entityId) {
      const id = String(entityId || "").trim();
      if (!id) return "";
      const entities = Array.isArray(eventDetail && eventDetail.entities) ? eventDetail.entities : [];
      const entity = entities.find((item) => String(item && item.id) === id);
      if (!entity) return "";
      const type = String(entity.entity_type || "").trim();
      const name = String(entity.name || "").trim();
      return type && name ? `${type}:${name}` : name || type;
    },

    playlistEventRelationTitle(relation) {
      const source = this.playlistEventEntityLabelById(this.playlistEventDetail, relation && relation.source_entity_id);
      const target = this.playlistEventEntityLabelById(this.playlistEventDetail, relation && relation.target_entity_id);
      if (source && target) return `${source} -> ${target}`;
      if (source) return source;
      if (target) return target;
      return String((relation && relation.relation_type) || "relation");
    },

    playlistEventRelationMeta(relation) {
      const parts = [];
      const relationType = String((relation && relation.relation_type) || "").trim();
      const direction = String((relation && relation.direction) || "").trim();
      const magnitude =
        relation && relation.magnitude && relation.magnitude.description
          ? String(relation.magnitude.description).trim()
          : "";
      const confidence = relation && relation.confidence != null ? Number(relation.confidence) : null;
      if (relationType) parts.push(relationType);
      if (direction) parts.push(direction);
      if (magnitude) parts.push(magnitude);
      if (Number.isFinite(confidence)) parts.push(confidence.toFixed(2));
      return parts.join(" · ");
    },

    playlistEvidenceSourceLabel(evidence) {
      const kind = String((evidence && evidence.source_kind) || "").trim();
      const label = String((evidence && evidence.source_label) || "").trim();
      if (label) return label;
      if (kind === "title") return "标题";
      if (kind === "description") return "描述";
      if (kind === "transcript") return "转写片段";
      return kind || "证据";
    },

    playlistEventsMaintenanceSummary() {
      return this.playlistEventsAllSummary || {};
    },

    playlistEventsAllSummaryLoaded() {
      return Boolean(this.playlistEventsAllSummary && typeof this.playlistEventsAllSummary === "object");
    },

    playlistEventsCoverageNumber(key) {
      const summary = this.playlistEventsMaintenanceSummary();
      const value = Number(summary[key] || 0);
      if (!Number.isFinite(value) || value < 0) return 0;
      return Math.trunc(value);
    },

    playlistEventsCoverageLabel() {
      if (!this.playlistEventsAllSummaryLoaded()) {
        if (this.playlistEventsAllSummaryLoading) return "事件覆盖 加载中";
        if (this.playlistEventsAllSummaryError) return "事件覆盖 加载失败";
        return "事件覆盖 未加载";
      }
      const withEvents = this.playlistEventsCoverageNumber("video_with_events");
      const total = this.playlistEventsCoverageNumber("video_total");
      if (total <= 0) return "事件覆盖 暂无视频";
      return `事件覆盖 ${this.formatInteger(withEvents)}/${this.formatInteger(total)} 视频`;
    },

    playlistEventsCoverageTitle() {
      if (!this.playlistEventsAllSummaryLoaded()) {
        if (this.playlistEventsAllSummaryLoading) return "事件覆盖数据加载中";
        if (this.playlistEventsAllSummaryError) return `事件覆盖数据加载失败：${this.playlistEventsAllSummaryError}`;
        return "事件覆盖数据尚未加载";
      }
      const withEvents = this.playlistEventsCoverageNumber("video_with_events");
      const total = this.playlistEventsCoverageNumber("video_total");
      const eventTotal = this.playlistEventsCoverageNumber("event_total");
      if (total <= 0) return "播放列表暂无可统计视频";
      const ratio = total > 0 ? (withEvents / total) * 100 : 0;
      const ratioText = total > 0 ? `${ratio.toFixed(ratio >= 10 ? 0 : 1)}%` : "0%";
      return `已有事件的视频 ${this.formatInteger(withEvents)}/${this.formatInteger(total)}（${ratioText}），事件总数 ${this.formatInteger(eventTotal)}`;
    },

    playlistEventsAllStatusLabel(key, statusLabel) {
      const label = String(statusLabel || "").trim();
      if (!this.playlistEventsAllSummaryLoaded()) {
        if (this.playlistEventsAllSummaryLoading) return `全量 ${label} 加载中`;
        if (this.playlistEventsAllSummaryError) return `全量 ${label} 加载失败`;
        return `全量 ${label} 未加载`;
      }
      return `全量 ${this.formatInteger(this.playlistEventsCoverageNumber(key))} ${label}`;
    },

    playlistEventsAcceptedSummaryLabel() {
      return this.playlistEventsAllStatusLabel("accepted", "accepted");
    },

    playlistEventsDraftSummaryLabel() {
      return this.playlistEventsAllStatusLabel("draft", "draft");
    },

    playlistEventPeriodParams() {
      const params = new URLSearchParams();
      const periodStart = String(this.playlistSelectedDate || "").trim();
      if (periodStart) {
        params.set("period_start", periodStart);
        params.set("granularity", this.playlistGranularity());
      }
      return params;
    },

    playlistEventFilterQuery() {
      const params = this.playlistEventPeriodParams();
      const status = String(this.playlistEventStatusFilter || "").trim();
      const eventType = String(this.playlistEventTypeFilter || "").trim();
      const entity = String(this.playlistEventEntityFilter || "").trim();
      if (status) params.set("status", status);
      if (eventType) params.set("event_type", eventType);
      if (entity) params.set("entity", entity);
      params.set("limit", "200");
      const suffix = params.toString();
      return suffix ? `?${suffix}` : "";
    },

    playlistEventPeriodSummaryQuery() {
      const params = this.playlistEventPeriodParams();
      const suffix = params.toString();
      return suffix ? `?${suffix}` : "";
    },

    playlistEventEntitySuggestionQuery() {
      const params = this.playlistEventPeriodParams();
      const status = String(this.playlistEventStatusFilter || "").trim();
      const q = String(this.playlistEventEntityFilter || "").trim();
      if (status) params.set("status", status);
      if (q) params.set("q", q);
      params.set("limit", "18");
      const suffix = params.toString();
      return suffix ? `?${suffix}` : "";
    },

    async playlistLoadEventEntitySuggestions({ silent = false } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      const token = Number(this.playlistEventEntitySuggestToken || 0) + 1;
      this.playlistEventEntitySuggestToken = token;
      this.playlistEventEntitySuggestLoading = true;
      try {
        const suggestions = await this.api(
          `/playlists/${encodeURIComponent(pid)}/events/entities${this.playlistEventEntitySuggestionQuery()}`
        );
        if (Number(this.playlistEventEntitySuggestToken || 0) !== token) return;
        this.playlistEventEntitySuggestions = Array.isArray(suggestions) ? suggestions : [];
      } catch (e) {
        if (!silent) this.playlistEventsError = e && e.message ? e.message : String(e);
      } finally {
        if (Number(this.playlistEventEntitySuggestToken || 0) === token) {
          this.playlistEventEntitySuggestLoading = false;
        }
      }
    },

    playlistOpenEventEntitySuggestions() {
      this.playlistEventEntitySuggestOpen = true;
      this.playlistLoadEventEntitySuggestions({ silent: true }).catch(() => null);
    },

    playlistCloseEventEntitySuggestions() {
      setTimeout(() => {
        this.playlistEventEntitySuggestOpen = false;
      }, 120);
    },

    async playlistApplyEventEntitySuggestion(item) {
      const name = String((item && item.name) || "").trim();
      if (!name) return;
      this.playlistEventEntityFilter = name;
      this.playlistEventEntitySuggestOpen = false;
      await Promise.all([
        this.playlistLoadEventsPanel({ silent: true }),
        this.playlistLoadEventEntitySuggestions({ silent: true }).catch(() => null),
      ]);
    },

    async playlistClearEventEntityFilter() {
      this.playlistEventEntityFilter = "";
      this.playlistEventEntitySuggestOpen = false;
      await Promise.all([
        this.playlistLoadEventsPanel({ silent: true }),
        this.playlistLoadEventEntitySuggestions({ silent: true }).catch(() => null),
      ]);
    },

    async playlistLoadEventsAllSummary({ silent = false } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return null;
      this.playlistEventsAllSummaryLoading = true;
      this.playlistEventsAllSummaryError = "";
      try {
        const summary = await this.api(`/playlists/${encodeURIComponent(pid)}/events/summary`);
        this.playlistEventsAllSummary = summary || null;
        return this.playlistEventsAllSummary;
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.playlistEventsAllSummaryError = msg;
        if (!silent) this.playlistEventsError = msg;
        throw e;
      } finally {
        this.playlistEventsAllSummaryLoading = false;
      }
    },

    async playlistLoadEventsPanel({ silent = false } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      if (!silent) this.playlistEventsLoading = true;
      this.playlistEventsError = "";
      try {
        const [summary, events] = await Promise.all([
          this.api(`/playlists/${encodeURIComponent(pid)}/events/summary${this.playlistEventPeriodSummaryQuery()}`),
          this.api(`/playlists/${encodeURIComponent(pid)}/events${this.playlistEventFilterQuery()}`),
        ]);
        this.playlistEventsSummary = summary || null;
        this.playlistEvents = Array.isArray(events) ? events : [];
        const selectedId = String(this.playlistSelectedEventId || "").trim();
        const target = this.playlistEvents.find((item) => String(item.id) === selectedId) || null;
        if (target) await this.playlistSelectEvent(target.id);
        else {
          this.playlistSelectedEventId = "";
          this.playlistEventDetail = null;
        }
        this.playlistLoadEventEntitySuggestions({ silent: true }).catch(() => null);
      } catch (e) {
        this.playlistEventsError = e && e.message ? e.message : String(e);
      } finally {
        if (!silent) this.playlistEventsLoading = false;
      }
    },

    async playlistToggleEventDetail(eventId) {
      const id = String(eventId || "").trim();
      if (!id) return;
      if (String(this.playlistSelectedEventId || "") === id && this.playlistEventDetail) {
        this.playlistSelectedEventId = "";
        this.playlistEventDetail = null;
        this.playlistEventDetailLoading = false;
        return;
      }
      await this.playlistSelectEvent(id);
    },

    async playlistSelectEvent(eventId) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const id = String(eventId || "").trim();
      if (!pid || !id) {
        this.playlistSelectedEventId = "";
        this.playlistEventDetail = null;
        this.playlistEventDetailLoading = false;
        return;
      }
      this.playlistSelectedEventId = id;
      this.playlistEventDetail = null;
      this.playlistEventDetailLoading = true;
      try {
        const detail = await this.api(`/playlists/${encodeURIComponent(pid)}/events/${encodeURIComponent(id)}`);
        if (String(this.playlistSelectedEventId || "") === id) this.playlistEventDetail = detail || null;
      } catch (e) {
        this.playlistEventsError = e && e.message ? e.message : String(e);
        throw e;
      } finally {
        if (String(this.playlistSelectedEventId || "") === id) this.playlistEventDetailLoading = false;
      }
    },

    async playlistSubmitEventExtraction({ force = false } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const shouldForce = Boolean(force);
      if (!pid || this.playlistAnalysisBackfillSubmitting) return;
      if (!shouldForce && this.playlistAnalysisActiveBackfillJob()) return;
      if (shouldForce && this.playlistAnalysisActiveForceBackfillJob()) return;
      try {
        this.playlistAnalysisBackfillSubmitting = true;
        this.playlistEventsExtractSubmitting = true;
        const result = await this.api(`/playlists/${encodeURIComponent(pid)}/events/extract`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ force: shouldForce }),
        });
        this.playlistAnalysisBackfillJob = result && result.backfill_job ? result.backfill_job : result || null;
        const jobId = result && result.job_id ? String(result.job_id).slice(0, 8) : "";
        const label = shouldForce ? "全部重新抽取" : "事件抽取";
        this.globalStatus = jobId ? `已投递${label} ${jobId}` : `已投递${label}`;
        await Promise.all([
          this.playlistLoadEventsPanel({ silent: true }).catch(() => null),
          this.playlistLoadEventsAllSummary({ silent: true }).catch(() => null),
          this.playlistLoadAnalysisSummary({ silent: true }).catch(() => null),
        ]);
        if (this.playlistAnalysisActiveBackfillJob()) this.playlistAnalysisSchedulePoll();
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.playlistEventsError = msg;
        this.playlistAnalysisError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.playlistAnalysisBackfillSubmitting = false;
        this.playlistEventsExtractSubmitting = false;
      }
    },

    async playlistExtractEvents() {
      await this.playlistSubmitEventExtraction({ force: false });
    },

    async playlistSettingsBackfillEvents() {
      await this.playlistSubmitEventExtraction({ force: false });
    },

    async playlistSettingsForceExtractEvents() {
      const ok =
        typeof window === "undefined" ||
        window.confirm(
          "全部重新抽取会先停止当前播放列表相关的事件抽取、事件 embedding 与语义快照构建任务，然后重新抽取所有已有 plain transcript 的视频事件。任务历史会保留。确认继续？"
        );
      if (!ok) return;
      await this.playlistSubmitEventExtraction({ force: true });
    },

    async playlistPatchEventStatus(status) {
      await this.playlistPatchEventStatusFor(this.playlistSelectedEventId, status);
    },

    async playlistPatchEventStatusFor(eventId, status) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const id = String(eventId || "").trim();
      if (!pid || !id) return;
      this.playlistSelectedEventId = id;
      await this.api(`/playlists/${encodeURIComponent(pid)}/events/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ status }),
      });
      await Promise.all([
        this.playlistLoadEventsPanel({ silent: true }),
        this.playlistLoadEventEntitySuggestions({ silent: true }).catch(() => null),
        this.playlistLoadEventsAllSummary({ silent: true }).catch(() => null),
        this.playlistLoadAnalysisSummary().catch(() => null),
      ]);
      this.globalStatus = status === "accepted" ? "已确认事件" : status === "rejected" ? "已拒绝事件" : "已恢复为 draft";
    },

    playlistAnalysisStopPolling() {
      try {
        clearTimeout(this.playlistAnalysisPollTimer);
      } catch {
        // ignore
      }
      this.playlistAnalysisPollTimer = null;
    },

    playlistAnalysisStopSignalsReload() {
      try {
        clearTimeout(this._playlistAnalysisSignalsReloadTimer);
      } catch {
        // ignore
      }
      this._playlistAnalysisSignalsReloadTimer = null;
      this._playlistAnalysisSignalsRequestToken = Number(this._playlistAnalysisSignalsRequestToken || 0) + 1;
      this._abortCtrl("_playlistAnalysisSignalsAbortCtrl");
      this.playlistAnalysisSignalsLoading = false;
    },

    playlistAnalysisStopTimelineDensityLoad() {
      this._playlistAnalysisTimelineDensityRequestToken = Number(this._playlistAnalysisTimelineDensityRequestToken || 0) + 1;
      this._abortCtrl("_playlistAnalysisTimelineDensityAbortCtrl");
      this.playlistAnalysisTimelineDensityLoading = false;
    },

    playlistAnalysisInvalidateSignalCaches() {
      this.playlistAnalysisSignalsVersion = Number(this.playlistAnalysisSignalsVersion || 0) + 1;
      this._playlistAnalysisGranularityCache = null;
      this._playlistAnalysisMetricDataCache = null;
      this._playlistAnalysisProjectionScaleCache = null;
      this._playlistAnalysisProjectionPointsCache = null;
      this._playlistAnalysisBreakpointsCache = null;
      this._playlistAnalysisBreakpointDisplayCache = null;
      this._playlistAnalysisProjectionCentroidsCache = null;
    },

    playlistAnalysisStopProjectionPlayback() {
      try {
        clearInterval(this.playlistAnalysisProjectionPlayTimer);
      } catch {
        // ignore
      }
      this.playlistAnalysisProjectionPlayTimer = null;
      this.playlistAnalysisProjectionPlaying = false;
    },

    playlistAnalysisDestroyChart() {
      try {
        if (this._playlistAnalysisChartRenderFrame && window.cancelAnimationFrame) {
          window.cancelAnimationFrame(this._playlistAnalysisChartRenderFrame);
        }
      } catch {
        // ignore
      }
      this._playlistAnalysisChartRenderFrame = null;
      try {
        if (this.playlistAnalysisChart) this.playlistAnalysisChart.remove();
      } catch {
        // ignore
      }
      this.playlistAnalysisChart = null;
      this.playlistAnalysisChartAxisSeries = null;
      this.playlistAnalysisChartDriftSeries = null;
      this.playlistAnalysisChartDispersionSeries = null;
      this.playlistAnalysisChartSeries = {};
    },

    _ensurePlaylistAnalysisChart() {
      if (this.playlistAnalysisChart && this.playlistAnalysisChartSeries) return true;
      const el = this.$refs && this.$refs.playlistAnalysisTrendChart;
      if (!el || el.clientWidth < 10 || el.clientHeight < 10) return false;
      const LC = window.LightweightCharts;
      if (!LC || typeof LC.createChart !== "function") return false;
      const chart = LC.createChart(el, {
        autoSize: true,
        handleScroll: false,
        handleScale: false,
        layout: {
          background: { type: LC.ColorType.Solid, color: "rgba(0,0,0,0)" },
          textColor: "rgba(148, 163, 184, 0.85)",
          attributionLogo: false,
        },
        grid: {
          vertLines: { color: "rgba(30, 41, 59, 0.35)" },
          horzLines: { color: "rgba(30, 41, 59, 0.35)" },
        },
        rightPriceScale: { borderColor: "rgba(30, 41, 59, 0.55)" },
        timeScale: {
          borderColor: "rgba(30, 41, 59, 0.55)",
          fixLeftEdge: true,
          fixRightEdge: true,
          rightOffset: 0,
        },
        crosshair: { mode: LC.CrosshairMode.Normal },
      });
      const addLineSeries = (options) => {
        if (typeof chart.addSeries === "function" && LC.LineSeries) return chart.addSeries(LC.LineSeries, options);
        return chart.addLineSeries(options);
      };
      const axisSeries = addLineSeries({
        color: "rgba(0,0,0,0)",
        lineWidth: 1,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        title: "",
      });
      const metricDefs = this.playlistAnalysisMetricDefs();
      const series = {};
      metricDefs.forEach((metric) => {
        if (!this.playlistAnalysisVisibleMetrics[metric.key]) return;
        series[metric.key] = addLineSeries({
          color: metric.color,
          lineWidth: metric.lineWidth || 1,
          lastValueVisible: false,
          priceLineVisible: false,
          title: metric.title,
        });
      });
      this.playlistAnalysisChart = chart;
      this.playlistAnalysisChartAxisSeries = axisSeries;
      this.playlistAnalysisChartSeries = series;
      return true;
    },

    _renderPlaylistAnalysisChart() {
      if (String(this.playlistAnalysisTab || "trend") !== "trend") return;
      if (!this._ensurePlaylistAnalysisChart()) {
        if (!window.LightweightCharts && typeof this.ensureLightweightCharts === "function") {
          this.ensureLightweightCharts()
            .then(() => this.playlistAnalysisRequestChartRender())
            .catch((e) => {
              this.playlistAnalysisError = e && e.message ? e.message : String(e);
            });
          return;
        }
        setTimeout(() => this._renderPlaylistAnalysisChart(), 80);
        return;
      }
      const bounds = this.playlistAnalysisChartRangeBounds();
      if (this.playlistAnalysisChartAxisSeries) {
        this.playlistAnalysisChartAxisSeries.setData(this.playlistAnalysisCalendarAxisData(bounds));
      }
      const series = this.playlistAnalysisChartSeries || {};
      this.playlistAnalysisMetricDefs().forEach((metric) => {
        const target = series[metric.key];
        if (!target) return;
        const valueData = this.playlistAnalysisMetricValueData(metric);
        target.setData(this.playlistAnalysisChartDataWithBounds(valueData, bounds));
      });
      try {
        if (bounds) this.playlistAnalysisChart.timeScale().setVisibleRange({ from: bounds.start, to: bounds.end });
        else this.playlistAnalysisChart.timeScale().fitContent();
      } catch {
        // ignore
      }
    },

    playlistAnalysisChartRangeBounds() {
      const start = String(this.playlistAnalysisRangeStart || this.playlistAnalysisFullRangeStart || "").trim();
      const end = String(this.playlistAnalysisRangeEnd || this.playlistAnalysisFullRangeEnd || "").trim();
      if (!start || !end || start > end) return null;
      return { start, end };
    },

    playlistAnalysisChartDataWithBounds(valueData, bounds) {
      const data = Array.isArray(valueData) ? valueData.filter((item) => item && item.time) : [];
      if (!bounds) return data.sort((a, b) => String(a.time).localeCompare(String(b.time)));
      const seen = new Set(data.map((item) => String(item.time)));
      if (!seen.has(bounds.start)) data.push({ time: bounds.start });
      if (!seen.has(bounds.end)) data.push({ time: bounds.end });
      return data.sort((a, b) => String(a.time).localeCompare(String(b.time)));
    },

    playlistAnalysisCalendarAxisData(bounds) {
      if (!bounds || !bounds.start || !bounds.end || bounds.start > bounds.end) return [];
      const key = `${bounds.start}|${bounds.end}`;
      if (this._playlistAnalysisCalendarAxisCache && this._playlistAnalysisCalendarAxisCache.key === key) {
        return this._playlistAnalysisCalendarAxisCache.value;
      }
      const data = [];
      const totalDays = Math.max(1, this.playlistAnalysisDateDiffDays(bounds.start, bounds.end));
      const stepDays = totalDays > 2190 ? 30 : totalDays > 730 ? 7 : 1;
      let current = bounds.start;
      let guard = 0;
      while (current && current <= bounds.end && guard < 10000) {
        data.push({ time: current });
        current = this.playlistAnalysisAddDays(current, stepDays);
        guard += 1;
      }
      if (!data.length || String(data[data.length - 1].time) !== String(bounds.end)) {
        data.push({ time: bounds.end });
      }
      this._playlistAnalysisCalendarAxisCache = { key, value: data };
      return data;
    },

    playlistAnalysisMetricValueData(metric) {
      const metricKey = String((metric && metric.key) || (metric && metric.field) || "");
      const cacheKey = [
        Number(this.playlistAnalysisSignalsVersion || 0),
        String(this.playlistAnalysisRangeStart || ""),
        String(this.playlistAnalysisRangeEnd || ""),
        metricKey,
      ].join("|");
      if (this._playlistAnalysisMetricDataCache && this._playlistAnalysisMetricDataCache.has(cacheKey)) {
        return this._playlistAnalysisMetricDataCache.get(cacheKey);
      }
      const rows = this.playlistAnalysisSignalsForGranularity(metric && metric.granularity).filter((item) => item && item.period_date);
      const data = [];
      const seen = new Set();
      let previousTime = "";
      rows.forEach((item) => {
        const time = String(item.period_date || "");
        if (!time || seen.has(time)) return;
        const maxGapDays = Number((metric && metric.maxGapDays) || 0);
        if (previousTime && maxGapDays > 0 && this.playlistAnalysisDateDiffDays(previousTime, time) > maxGapDays) {
          const breakTime = this.playlistAnalysisAddDays(previousTime, 1);
          if (breakTime && breakTime < time && !seen.has(breakTime)) {
            data.push({ time: breakTime });
            seen.add(breakTime);
          }
        }
        const minReadyCount = Number((metric && metric.minReadyCount) || 0);
        const readyCount = Number(item.ready_embedding_count || item.event_count || item.video_count || 0);
        const rawValue = item[metric.field];
        const value = Number(rawValue);
        if ((minReadyCount > 0 && readyCount < minReadyCount) || rawValue == null || !Number.isFinite(value)) {
          data.push({ time });
          seen.add(time);
          previousTime = time;
          return;
        }
        data.push({ time, value });
        seen.add(time);
        previousTime = time;
      });
      if (!this._playlistAnalysisMetricDataCache) this._playlistAnalysisMetricDataCache = new Map();
      this._playlistAnalysisMetricDataCache.set(cacheKey, data);
      return data;
    },

    playlistAnalysisDateDiffDays(leftDate, rightDate) {
      const left = this.playlistAnalysisDateToUtcMs(leftDate);
      const right = this.playlistAnalysisDateToUtcMs(rightDate);
      if (!Number.isFinite(left) || !Number.isFinite(right)) return 0;
      return Math.round((right - left) / 86400000);
    },

    playlistAnalysisAddDays(dateValue, dayOffset) {
      const base = this.playlistAnalysisDateToUtcMs(dateValue);
      if (!Number.isFinite(base)) return "";
      const date = new Date(base + Number(dayOffset || 0) * 86400000);
      const year = date.getUTCFullYear();
      const month = String(date.getUTCMonth() + 1).padStart(2, "0");
      const day = String(date.getUTCDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    },

    playlistAnalysisDateToUtcMs(dateValue) {
      const value = String(dateValue || "").slice(0, 10);
      const parts = value.split("-").map((item) => Number(item));
      if (parts.length !== 3 || !parts[0] || !parts[1] || !parts[2]) return NaN;
      return Date.UTC(parts[0], parts[1] - 1, parts[2]);
    },

    playlistAnalysisRequestChartRender() {
      if (String(this.playlistAnalysisTab || "trend") !== "trend") return;
      if (this._playlistAnalysisChartRenderFrame) return;
      const render = () => {
        this._playlistAnalysisChartRenderFrame = null;
        this._renderPlaylistAnalysisChart();
      };
      try {
        if (window.requestAnimationFrame) {
          this._playlistAnalysisChartRenderFrame = window.requestAnimationFrame(render);
          return;
        }
      } catch {
        // ignore
      }
      setTimeout(render, 0);
    },

    playlistAnalysisMetricDefs() {
      return [
        {
          key: "day_z",
          title: "日标准化语义变化",
          granularity: "day",
          field: "drift_rolling_z",
          color: "rgba(16, 185, 129, 0.95)",
          minReadyCount: 3,
          maxGapDays: 3,
          description: "日级标准化语义变化对低样本日期很敏感，默认隐藏；少于 3 个事件 embedding 的日期会断线。",
        },
        { key: "week_z", title: "周标准化语义变化", granularity: "week", field: "drift_rolling_z", color: "rgba(56, 189, 248, 0.92)", maxGapDays: 21 },
        { key: "month_z", title: "月标准化语义变化", granularity: "month", field: "drift_rolling_z", color: "rgba(251, 191, 36, 0.88)", maxGapDays: 70 },
        { key: "day_drift", title: "日相邻周期语义变化", granularity: "day", field: "drift_score", color: "rgba(129, 140, 248, 0.86)", lineWidth: 1, minReadyCount: 3, maxGapDays: 3 },
        { key: "day_uncertainty", title: "日不确定性", granularity: "day", field: "dispersion_mean", color: "rgba(244, 114, 182, 0.84)", lineWidth: 1, minReadyCount: 2, maxGapDays: 3 },
      ];
    },

    playlistAnalysisToggleMetric(metricKey) {
      const key = String(metricKey || "").trim();
      if (!key) return;
      this.playlistAnalysisVisibleMetrics[key] = !this.playlistAnalysisVisibleMetrics[key];
      this.playlistAnalysisDestroyChart();
      this.$nextTick(() => this.playlistAnalysisRequestChartRender());
    },

    playlistAnalysisTabClass(tab) {
      const active = String(this.playlistAnalysisTab || "trend") === String(tab || "");
      return active
        ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"
        : "border-slate-700 bg-slate-950/20 text-slate-300 hover:bg-slate-900/60";
    },

    playlistAnalysisSetTab(tab) {
      this.playlistAnalysisTab = String(tab || "trend");
      if (this.playlistAnalysisTab === "trend") {
        this.$nextTick(() => this.playlistAnalysisRequestChartRender());
        return;
      }
      this.playlistAnalysisStopProjectionPlayback();
      this.playlistAnalysisReleaseProjectionWindowDrag();
      if (!this.playlistAnalysisHasReadySnapshot()) return;
      if (this.playlistAnalysisTab === "events" && !this.playlistAnalysisCandidatesLoaded && !this.playlistAnalysisCandidatesLoading) {
        this.playlistLoadAnalysisCandidates();
      } else if (this.playlistAnalysisTab === "events" && this.playlistAnalysisCandidatesLoaded && !this.playlistAnalysisCandidateDetail) {
        const selectedId = String(this.playlistAnalysisSelectedCandidateId || "").trim();
        const target =
          (Array.isArray(this.playlistAnalysisCandidates) ? this.playlistAnalysisCandidates : []).find((item) => String(item.id) === selectedId) ||
          (Array.isArray(this.playlistAnalysisCandidates) ? this.playlistAnalysisCandidates[0] : null);
        if (target) this.playlistSelectAnalysisCandidate(target.id);
      }
    },

    playlistAnalysisSummaryBadgeClass() {
      const summary = this.playlistAnalysisSummary;
      if (!summary) {
        if (this.playlistAnalysisSummaryError) return "border-rose-500/30 bg-rose-500/10 text-rose-200";
        return "border-slate-700 bg-slate-900/40 text-slate-300";
      }
      if (summary.running) return "border-amber-500/30 bg-amber-500/10 text-amber-200";
      if (!summary.last_ready_run_id) {
        const eventTotal = Number(summary.event_total || 0);
        return eventTotal > 0
          ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
          : "border-slate-700 bg-slate-900/40 text-slate-300";
      }
      if (summary.analysis_dirty) return "border-sky-500/30 bg-sky-500/10 text-sky-200";
      if (Number(summary.event_total || 0) <= 0) return "border-slate-700 bg-slate-900/40 text-slate-300";
      return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
    },

    playlistAnalysisSummaryBadgeText({ short = false } = {}) {
      const prefix = short ? "快照" : "语义快照";
      const summary = this.playlistAnalysisSummary;
      if (!summary) {
        if (this.playlistAnalysisSummaryLoading) return `${prefix}加载中`;
        if (this.playlistAnalysisSummaryError) return `${prefix}加载失败`;
        return `${prefix}未加载`;
      }
      if (summary.running) return summary.last_ready_run_id ? `${prefix}更新中` : `${prefix}构建中`;
      const eventTotal = Number(summary.event_total || 0);
      if (!summary.last_ready_run_id) return eventTotal > 0 ? `${prefix}未构建` : `${prefix}无事件`;
      if (summary.analysis_dirty) return `${prefix}待更新`;
      if (eventTotal <= 0) return `${prefix}无事件`;
      return `${prefix}已就绪`;
    },

    playlistAnalysisSummaryShortBadgeLabel() {
      return this.playlistAnalysisSummaryBadgeText({ short: true });
    },

    playlistAnalysisSummaryBadgeLabel() {
      return this.playlistAnalysisSummaryBadgeText();
    },

    playlistAnalysisSummaryMetricLabel(key) {
      const summary = this.playlistAnalysisSummary;
      if (!summary) {
        if (this.playlistAnalysisSummaryLoading) return "加载中";
        if (this.playlistAnalysisSummaryError) return "加载失败";
        return "未加载";
      }
      const value = Number(summary[key] || 0);
      if (!Number.isFinite(value) || value < 0) return "0";
      return this.formatInteger(Math.trunc(value));
    },

    playlistAnalysisEmbeddingCoverageLabel() {
      const summary = this.playlistAnalysisSummary;
      if (!summary) {
        if (this.playlistAnalysisSummaryLoading) return "加载中";
        if (this.playlistAnalysisSummaryError) return "加载失败";
        return "未加载";
      }
      const embedded = Number(summary.event_embedded || 0);
      const total = Number(summary.event_total || 0);
      const safeEmbedded = Math.max(0, Math.trunc(Number.isFinite(embedded) ? embedded : 0));
      const safeTotal = Math.max(0, Math.trunc(Number.isFinite(total) ? total : 0));
      return `${this.formatInteger(safeEmbedded)}/${this.formatInteger(safeTotal)}`;
    },

    playlistAnalysisEmbeddingStatusLabel() {
      const summary = this.playlistAnalysisSummary;
      if (!summary) {
        if (this.playlistAnalysisSummaryLoading) return "加载中";
        if (this.playlistAnalysisSummaryError) return "加载失败";
        return "未加载";
      }
      const embedded = Math.max(0, Number(summary.event_embedded || 0));
      const total = Math.max(0, Number(summary.event_total || 0));
      if (total <= 0) return "无事件";
      if (embedded >= total) return "已就绪";
      if (embedded > 0) return "部分就绪";
      return "未就绪";
    },

    playlistAnalysisEmbeddingStatusClass() {
      const summary = this.playlistAnalysisSummary;
      if (!summary) return this.playlistAnalysisSummaryError ? "text-rose-200" : "text-slate-500";
      const embedded = Math.max(0, Number(summary.event_embedded || 0));
      const total = Math.max(0, Number(summary.event_total || 0));
      if (total > 0 && embedded >= total) return "text-emerald-200";
      if (embedded > 0) return "text-amber-200";
      return "text-slate-500";
    },

    playlistAnalysisHasReadySnapshot() {
      return Boolean(this.playlistAnalysisSummary && this.playlistAnalysisSummary.last_ready_run_id);
    },

    playlistAnalysisCandidatesEmptyLabel() {
      const summary = this.playlistAnalysisSummary;
      if (!summary) return this.playlistAnalysisSummaryLoading ? "语义快照状态加载中…" : "语义快照状态未加载。";
      if (!summary.last_ready_run_id) return summary.running ? "语义快照正在构建…" : "尚未构建语义快照。";
      return "当前语义快照未发现语义变化点。";
    },

    playlistAnalysisActiveBackfillJob() {
      const summaryJob = this.playlistAnalysisSummary && this.playlistAnalysisSummary.backfill_job ? this.playlistAnalysisSummary.backfill_job : null;
      const localJob = this.playlistAnalysisBackfillJob && this.playlistAnalysisBackfillJob.job_id ? this.playlistAnalysisBackfillJob : null;
      const job = summaryJob || (localJob && localJob.backfill_job ? localJob.backfill_job : localJob);
      if (!job || !job.job_id) return null;
      const status = String(job.status || "").trim().toLowerCase();
      if (!["pending", "running"].includes(status)) return null;
      return job;
    },

    playlistAnalysisActiveForceBackfillJob() {
      const job = this.playlistAnalysisActiveBackfillJob();
      return job && job.force ? job : null;
    },

    playlistAnalysisBackfillVideoProgressText() {
      const job = this.playlistAnalysisActiveBackfillJob();
      if (!job) return "";
      const extracted = Math.max(0, Number(job.video_extracted || 0));
      const total = Math.max(0, Number(job.video_total || 0));
      const failed = Math.max(0, Number(job.video_failed || 0));
      if (!total && !extracted && !failed) return "";
      const running = Math.max(0, Number(job.video_running || 0));
      const pending = Math.max(0, Number(job.video_pending || 0));
      const parts = [`视频已抽取 ${this.formatInteger(extracted)} / 已投递待抽取 ${this.formatInteger(pending)}`];
      if (total) parts.push(`已投递视频 ${this.formatInteger(total)}`);
      if (running) parts.push(`视频执行中 ${this.formatInteger(running)}`);
      if (failed) parts.push(`视频失败 ${this.formatInteger(failed)}`);
      return parts.join(" · ");
    },

    playlistAnalysisBackfillRangeProgressText() {
      const job = this.playlistAnalysisActiveBackfillJob();
      if (!job) return "";
      const finished = Math.max(0, Number(job.range_finished || 0));
      const pending = Math.max(0, Number(job.range_pending || 0));
      const running = Math.max(0, Number(job.range_running || 0));
      const failed = Math.max(0, Number(job.range_failed || 0));
      const total = Math.max(0, Number(job.range_total || 0));
      if (!total && !finished && !pending && !running && !failed) return "";
      const parts = [`月任务完成 ${this.formatInteger(finished)} / 待执行 ${this.formatInteger(pending)}`];
      if (total) parts.push(`总月任务 ${this.formatInteger(total)}`);
      if (running) parts.push(`月任务执行中 ${this.formatInteger(running)}`);
      if (failed) parts.push(`月任务失败 ${this.formatInteger(failed)}`);
      return parts.join(" · ");
    },

    playlistAnalysisBackfillTimeProgressText() {
      const job = this.playlistAnalysisActiveBackfillJob();
      if (!job) return "";
      const elapsed = Number(job.elapsed_seconds);
      if (!Number.isFinite(elapsed) || elapsed < 0) return "";
      const estimated = Number(job.estimated_total_seconds);
      const elapsedText = this.formatDuration(elapsed) || "0:00";
      const estimatedText = Number.isFinite(estimated) && estimated > 0 ? this.formatDuration(estimated) : "估算中";
      return `已运行 ${elapsedText} / 预估总时长 ${estimatedText}`;
    },

    playlistAnalysisBackfillJobText() {
      const job = this.playlistAnalysisActiveBackfillJob();
      if (!job) return "";
      const status = String(job.status || "").trim().toLowerCase() === "running" ? "执行中" : "排队中";
      const id = String(job.job_id || "").slice(0, 8);
      const mode = job.force ? "全部重抽" : "事件抽取";
      const parts = [`${mode}${status}`, `任务 ${id}`];
      const scanned = Number(job.scanned || job.progress_current || 0);
      const enqueued = Number(job.enqueued || 0);
      const skipped = Number(job.skipped || 0);
      const rangeProgress = this.playlistAnalysisBackfillRangeProgressText();
      if (rangeProgress) parts.push(rangeProgress);
      if (scanned || enqueued || skipped) {
        parts.push(`累计扫描视频 ${this.formatInteger(scanned)}`);
        parts.push(`累计投递视频 ${this.formatInteger(enqueued)}`);
        if (skipped) parts.push(`累计跳过视频 ${this.formatInteger(skipped)}`);
      }
      const videoProgress = this.playlistAnalysisBackfillVideoProgressText();
      if (videoProgress) parts.push(videoProgress);
      const timeProgress = this.playlistAnalysisBackfillTimeProgressText();
      if (timeProgress) parts.push(timeProgress);
      if (job.cancel_requested_at) parts.push("停止中");
      return parts.join(" · ");
    },

    playlistAnalysisNeedsMaintenance() {
      const summary = this.playlistAnalysisSummary;
      return Boolean(
        this.playlistAnalysisActiveBackfillJob() ||
          (summary && (summary.analysis_dirty || !summary.last_ready_run_id || summary.event_total === 0))
      );
    },

    playlistAnalysisCandidateStatusClass(status) {
      const value = String(status || "").trim().toLowerCase();
      if (value === "accepted") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
      if (value === "rejected") return "border-rose-500/30 bg-rose-500/10 text-rose-200";
      return "border-amber-500/30 bg-amber-500/10 text-amber-200";
    },

    playlistAnalysisSelectedCandidate() {
      const selectedId = String(this.playlistAnalysisSelectedCandidateId || "").trim();
      const items = Array.isArray(this.playlistAnalysisCandidates) ? this.playlistAnalysisCandidates : [];
      return items.find((item) => String(item.id) === selectedId) || null;
    },

    playlistAnalysisSignalsForGranularity(granularity) {
      const target = String(granularity || "day");
      const start = String(this.playlistAnalysisRangeStart || "");
      const end = String(this.playlistAnalysisRangeEnd || "");
      const key = `${Number(this.playlistAnalysisSignalsVersion || 0)}|${target}|${start}|${end}`;
      if (this._playlistAnalysisGranularityCache && this._playlistAnalysisGranularityCache.has(key)) {
        return this._playlistAnalysisGranularityCache.get(key);
      }
      const rows = [];
      (Array.isArray(this.playlistAnalysisSignals) ? this.playlistAnalysisSignals : []).forEach((item) => {
        if (String(item && item.granularity ? item.granularity : "") !== target) return;
        const date = String(item.period_date || "");
        if (!date) return;
        if (start && date < start) return;
        if (end && date > end) return;
        rows.push(item);
      });
      rows.sort((left, right) => String(left.period_date || "").localeCompare(String(right.period_date || "")));
      if (!this._playlistAnalysisGranularityCache) this._playlistAnalysisGranularityCache = new Map();
      this._playlistAnalysisGranularityCache.set(key, rows);
      return rows;
    },

    playlistAnalysisInitializeRange() {
      const daySignals = (Array.isArray(this.playlistAnalysisSignals) ? this.playlistAnalysisSignals : [])
        .filter((item) => String(item.granularity || "") === "day" && item.period_date)
        .map((item) => String(item.period_date))
        .sort();
      if (!daySignals.length) {
        this.playlistAnalysisFullRangeStart = "";
        this.playlistAnalysisFullRangeEnd = "";
        this.playlistAnalysisRangeStart = "";
        this.playlistAnalysisRangeEnd = "";
        return;
      }
      const minDate = daySignals[0];
      const maxDate = daySignals[daySignals.length - 1];
      this.playlistAnalysisFullRangeStart = minDate;
      this.playlistAnalysisFullRangeEnd = maxDate;
      if (this.playlistAnalysisRangeStart && this.playlistAnalysisRangeEnd) return;
      const maxMonth = maxDate.slice(0, 7);
      const defaultStartMonth = this.playlistAnalysisShiftMonth(maxMonth, -11);
      this.playlistAnalysisSetTimelineRangeByMonth(defaultStartMonth, maxMonth, { render: false });
    },

    playlistAnalysisInitializeRangeFromSummary(summary) {
      const start = summary && summary.signal_start_date ? String(summary.signal_start_date).slice(0, 10) : "";
      const end = summary && summary.signal_end_date ? String(summary.signal_end_date).slice(0, 10) : "";
      if (!start || !end || start > end) {
        this.playlistAnalysisFullRangeStart = "";
        this.playlistAnalysisFullRangeEnd = "";
        this.playlistAnalysisRangeStart = "";
        this.playlistAnalysisRangeEnd = "";
        this.playlistAnalysisTimelineDensity = [];
        return;
      }
      this.playlistAnalysisFullRangeStart = start;
      this.playlistAnalysisFullRangeEnd = end;
      const bounds = this.playlistAnalysisTimelineBounds();
      if (
        this.playlistAnalysisRangeStart &&
        this.playlistAnalysisRangeEnd &&
        bounds &&
        this.playlistAnalysisRangeStart >= bounds.start &&
        this.playlistAnalysisRangeEnd <= bounds.end
      ) {
        return;
      }
      this.playlistAnalysisRangeStart = "";
      this.playlistAnalysisRangeEnd = "";
    },

    playlistAnalysisFullTimelineBounds() {
      const start = String(this.playlistAnalysisFullRangeStart || "").slice(0, 10);
      const end = String(this.playlistAnalysisFullRangeEnd || "").slice(0, 10);
      if (!start || !end || start > end) return null;
      return { start, end };
    },

    playlistAnalysisNormalTimelineBounds() {
      const full = this.playlistAnalysisFullTimelineBounds();
      if (!full) return null;
      const today = String(this._todayIsoLocal ? this._todayIsoLocal() : todayIsoLocal()).slice(0, 10);
      const contentStart = String(this.playlistTimelineStart || "").slice(0, 10);
      const start = contentStart && contentStart > full.start ? contentStart : full.start;
      const end = today && today < full.end ? today : full.end;
      if (start <= end) return { start, end };

      if (full.start > today) {
        const firstMonth = full.start.slice(0, 7);
        const cappedEnd = this.playlistAnalysisMonthEndDate(this.playlistAnalysisShiftMonth(firstMonth, 11));
        return { start: full.start, end: cappedEnd < full.end ? cappedEnd : full.end };
      }

      const lastMonth = full.end.slice(0, 7);
      const cappedStart = `${this.playlistAnalysisShiftMonth(lastMonth, -11)}-01`;
      return { start: cappedStart > full.start ? cappedStart : full.start, end: full.end };
    },

    playlistAnalysisTimelineBounds() {
      return String(this.playlistAnalysisTimelineScope || "normal") === "full"
        ? this.playlistAnalysisFullTimelineBounds()
        : this.playlistAnalysisNormalTimelineBounds();
    },

    playlistAnalysisTimelineHasExtendedRange() {
      const full = this.playlistAnalysisFullTimelineBounds();
      const normal = this.playlistAnalysisNormalTimelineBounds();
      return Boolean(full && normal && (full.start !== normal.start || full.end !== normal.end));
    },

    playlistAnalysisTimelineScopeClass(scope) {
      const active = String(this.playlistAnalysisTimelineScope || "normal") === String(scope || "normal");
      return active
        ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"
        : "border-slate-700 bg-slate-950/20 text-slate-400 hover:bg-slate-900/60 hover:text-slate-200";
    },

    playlistAnalysisTimelineScopeLabel() {
      const bounds = this.playlistAnalysisTimelineBounds();
      if (!bounds) return "暂无时间范围";
      const prefix = String(this.playlistAnalysisTimelineScope || "normal") === "full" ? "完整范围" : "正常观察域";
      return `${prefix} ${bounds.start} ~ ${bounds.end}`;
    },

    playlistAnalysisInitializeDefaultRange() {
      if (this.playlistAnalysisRangeStart && this.playlistAnalysisRangeEnd) return;
      const bounds = this.playlistAnalysisTimelineBounds();
      if (!bounds) return;
      const rows = (Array.isArray(this.playlistAnalysisTimelineDensity) ? this.playlistAnalysisTimelineDensity : [])
        .filter((item) => item && item.period_date && String(item.period_date) >= bounds.start && String(item.period_date) <= bounds.end)
        .sort((left, right) => String(left.period_date).localeCompare(String(right.period_date)));
      const today = String(this._todayIsoLocal ? this._todayIsoLocal() : todayIsoLocal()).slice(0, 10);
      const nonFutureRows = rows.filter((item) => String(item.period_date) <= today);
      const anchor = nonFutureRows.length ? nonFutureRows[nonFutureRows.length - 1] : rows[0] || null;
      const anchorMonth = String((anchor && anchor.period_date) || bounds.end).slice(0, 7);
      const futureOnly = anchor && String(anchor.period_date) > today;
      const startMonth = futureOnly ? anchorMonth : this.playlistAnalysisShiftMonth(anchorMonth, -11);
      const endMonth = futureOnly ? this.playlistAnalysisShiftMonth(anchorMonth, 11) : anchorMonth;
      this.playlistAnalysisSetTimelineRangeByMonth(startMonth, endMonth, { render: false });
    },

    async playlistAnalysisSetTimelineScope(scope) {
      const next = String(scope || "normal") === "full" ? "full" : "normal";
      if (String(this.playlistAnalysisTimelineScope || "normal") === next) return;
      this.playlistAnalysisStopProjectionPlayback();
      this.playlistAnalysisReleaseProjectionWindowDrag();
      this.playlistAnalysisReleaseRangeDrag();
      this.playlistAnalysisTimelineScope = next;
      this._playlistAnalysisTimelineMonthsCache = null;
      if (next === "normal") {
        this.playlistAnalysisRangeStart = "";
        this.playlistAnalysisRangeEnd = "";
      }
      await this.playlistLoadAnalysisTimelineDensity();
      const hadRange = Boolean(this.playlistAnalysisRangeStart && this.playlistAnalysisRangeEnd);
      this.playlistAnalysisInitializeDefaultRange();
      if (!hadRange && this.playlistAnalysisRangeStart && this.playlistAnalysisRangeEnd) {
        await this.playlistLoadAnalysisSignals();
      }
    },

    playlistAnalysisScheduleSignalsReload() {
      this.playlistAnalysisStopSignalsReload();
      this.$nextTick(() => this.playlistAnalysisRequestChartRender());
      this._playlistAnalysisSignalsReloadTimer = setTimeout(() => {
        this.playlistLoadAnalysisSignals().catch((e) => {
          this.playlistAnalysisError = e && e.message ? e.message : String(e);
        });
      }, 180);
    },

    playlistAnalysisSelectedTimelineMonths() {
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return [];
      const start = Math.min(this.playlistAnalysisTimelineStartIndex(), this.playlistAnalysisTimelineEndIndex());
      const end = Math.max(this.playlistAnalysisTimelineStartIndex(), this.playlistAnalysisTimelineEndIndex());
      return months.slice(start, end + 1);
    },

    playlistAnalysisProjectionWindowOptions() {
      return PLAYLIST_ANALYSIS_PROJECTION_WINDOW_OPTIONS;
    },

    playlistAnalysisProjectionWindowOptionClass(months) {
      const active = Number(this.playlistAnalysisProjectionWindowMonths || 3) === Number(months || 0);
      return active
        ? "border-sky-400/40 bg-sky-500/15 text-sky-100"
        : "border-slate-700 bg-slate-950/30 text-slate-400 hover:bg-slate-900/70 hover:text-slate-200";
    },

    playlistAnalysisProjectionWindowSize() {
      const months = this.playlistAnalysisSelectedTimelineMonths();
      if (!months.length) return 0;
      const configured = Math.max(1, Number(this.playlistAnalysisProjectionWindowMonths || 3));
      return Math.min(configured, months.length);
    },

    playlistAnalysisProjectionWindowCenterIndex() {
      const startIndex = this.playlistAnalysisTimelineMonthIndex(this.playlistAnalysisProjectionWindowStart || this.playlistAnalysisRangeStart);
      const endIndex = this.playlistAnalysisTimelineMonthIndex(this.playlistAnalysisProjectionWindowEnd || this.playlistAnalysisRangeEnd);
      if (Number.isFinite(startIndex) && Number.isFinite(endIndex)) return Math.round((startIndex + endIndex) / 2);
      return this.playlistAnalysisTimelineEndIndex();
    },

    playlistAnalysisSetProjectionWindowByCenterIndex(index, { anchorDate = "" } = {}) {
      const selected = this.playlistAnalysisSelectedTimelineMonths();
      const size = this.playlistAnalysisProjectionWindowSize();
      if (!selected.length || size <= 0) return;
      const selectedStart = Number(selected[0].index || 0);
      const selectedEnd = Number(selected[selected.length - 1].index || 0);
      const maxStart = Math.max(selectedStart, selectedEnd - size + 1);
      const centerIndex = Math.max(selectedStart, Math.min(selectedEnd, Number(index || selectedStart)));
      const startIndex = Math.max(selectedStart, Math.min(maxStart, centerIndex - Math.floor(size / 2)));
      this.playlistAnalysisSetProjectionWindowByStartIndex(startIndex, { anchorDate });
    },

    playlistAnalysisSetProjectionWindowCenteredAtDate(dateValue, { anchorDate = "" } = {}) {
      const date = String(dateValue || "").slice(0, 10);
      if (!date) return;
      this.playlistAnalysisSetProjectionWindowByCenterIndex(this.playlistAnalysisTimelineMonthIndex(date), { anchorDate });
    },

    playlistAnalysisSetProjectionWindowMonths(months) {
      const next = Number(months || 0);
      if (!PLAYLIST_ANALYSIS_PROJECTION_WINDOW_OPTIONS.some((item) => Number(item.months) === next)) return;
      if (Number(this.playlistAnalysisProjectionWindowMonths || 3) === next) return;
      this.playlistAnalysisStopProjectionPlayback();
      this.playlistAnalysisReleaseProjectionWindowDrag();
      const anchorDate = String(this.playlistAnalysisProjectionWindowAnchorDate || "").slice(0, 10);
      const centerIndex = anchorDate ? this.playlistAnalysisTimelineMonthIndex(anchorDate) : this.playlistAnalysisProjectionWindowCenterIndex();
      this.playlistAnalysisProjectionWindowMonths = next;
      this.playlistAnalysisSetProjectionWindowByCenterIndex(centerIndex, { anchorDate });
      this._playlistAnalysisProjectionPointsCache = null;
      this._playlistAnalysisProjectionCentroidsCache = null;
    },

    playlistAnalysisEnsureProjectionWindow({ forceEnd = false } = {}) {
      const months = this.playlistAnalysisTimelineMonths();
      const selected = this.playlistAnalysisSelectedTimelineMonths();
      const size = this.playlistAnalysisProjectionWindowSize();
      if (!months.length || !selected.length || size <= 0) {
        this.playlistAnalysisProjectionWindowStart = "";
        this.playlistAnalysisProjectionWindowEnd = "";
        this.playlistAnalysisProjectionWindowAnchorDate = "";
        this.playlistAnalysisStopProjectionPlayback();
        return;
      }
      const selectedStart = Number(selected[0].index || 0);
      const selectedEnd = Number(selected[selected.length - 1].index || 0);
      const maxStart = Math.max(selectedStart, selectedEnd - size + 1);
      let startIndex = maxStart;
      if (!forceEnd && this.playlistAnalysisProjectionWindowStart && this.playlistAnalysisProjectionWindowEnd) {
        startIndex = this.playlistAnalysisTimelineMonthIndex(this.playlistAnalysisProjectionWindowStart);
        startIndex = Math.max(selectedStart, Math.min(maxStart, startIndex));
      }
      const endIndex = Math.min(selectedEnd, startIndex + size - 1);
      const startDate = months[startIndex].ym + "-01";
      const endDate = this.playlistAnalysisMonthEndDate(months[endIndex].ym);
      this.playlistAnalysisProjectionWindowStart = this.playlistAnalysisClampDateToRange(startDate);
      this.playlistAnalysisProjectionWindowEnd = this.playlistAnalysisClampDateToRange(endDate);
      if (forceEnd) this.playlistAnalysisProjectionWindowAnchorDate = "";
    },

    playlistAnalysisSetProjectionWindowByStartIndex(index, { anchorDate = "" } = {}) {
      const months = this.playlistAnalysisTimelineMonths();
      const selected = this.playlistAnalysisSelectedTimelineMonths();
      const size = this.playlistAnalysisProjectionWindowSize();
      if (!months.length || !selected.length || size <= 0) return;
      const selectedStart = Number(selected[0].index || 0);
      const selectedEnd = Number(selected[selected.length - 1].index || 0);
      const maxStart = Math.max(selectedStart, selectedEnd - size + 1);
      const startIndex = Math.max(selectedStart, Math.min(maxStart, Number(index || 0)));
      const endIndex = Math.min(selectedEnd, startIndex + size - 1);
      const startDate = months[startIndex].ym + "-01";
      const endDate = this.playlistAnalysisMonthEndDate(months[endIndex].ym);
      this.playlistAnalysisProjectionWindowStart = this.playlistAnalysisClampDateToRange(startDate);
      this.playlistAnalysisProjectionWindowEnd = this.playlistAnalysisClampDateToRange(endDate);
      this.playlistAnalysisProjectionWindowAnchorDate = String(anchorDate || "").slice(0, 10);
    },

    playlistAnalysisProjectionStep(delta = 1, { wrap = false } = {}) {
      this.playlistAnalysisEnsureProjectionWindow();
      const selected = this.playlistAnalysisSelectedTimelineMonths();
      const size = this.playlistAnalysisProjectionWindowSize();
      if (!selected.length || size <= 0) return;
      const selectedStart = Number(selected[0].index || 0);
      const selectedEnd = Number(selected[selected.length - 1].index || 0);
      const maxStart = Math.max(selectedStart, selectedEnd - size + 1);
      const current = this.playlistAnalysisTimelineMonthIndex(this.playlistAnalysisProjectionWindowStart || this.playlistAnalysisRangeStart);
      let next = current + Number(delta || 1);
      if (next > maxStart) next = wrap ? selectedStart : maxStart;
      if (next < selectedStart) next = wrap ? maxStart : selectedStart;
      this.playlistAnalysisSetProjectionWindowByStartIndex(next);
    },

    playlistAnalysisToggleProjectionPlayback() {
      if (this.playlistAnalysisProjectionPlaying) {
        this.playlistAnalysisStopProjectionPlayback();
        return;
      }
      this.playlistAnalysisEnsureProjectionWindow({ forceEnd: false });
      const selected = this.playlistAnalysisSelectedTimelineMonths();
      if (selected.length <= this.playlistAnalysisProjectionWindowSize()) return;
      const selectedStart = Number(selected[0].index || 0);
      const selectedEnd = Number(selected[selected.length - 1].index || 0);
      const maxStart = Math.max(selectedStart, selectedEnd - this.playlistAnalysisProjectionWindowSize() + 1);
      if (this.playlistAnalysisTimelineMonthIndex(this.playlistAnalysisProjectionWindowStart) >= maxStart) {
        this.playlistAnalysisSetProjectionWindowByStartIndex(selectedStart);
      }
      this.playlistAnalysisProjectionPlaying = true;
      this.playlistAnalysisProjectionPlayTimer = setInterval(() => {
        if (this.activeView !== "playlist" || this.playlistSubview !== "analysis" || this.playlistAnalysisTab !== "trend") {
          this.playlistAnalysisStopProjectionPlayback();
          return;
        }
        this.playlistAnalysisProjectionStep(1, { wrap: true });
      }, PLAYLIST_ANALYSIS_PROJECTION_PLAYBACK_INTERVAL_MS);
    },

    playlistAnalysisProjectionRangeLabel() {
      const start = this.playlistAnalysisProjectionWindowStart || "";
      const end = this.playlistAnalysisProjectionWindowEnd || "";
      if (!start || !end) return "暂无焦点窗口";
      return `${start} ~ ${end}`;
    },

    playlistAnalysisProjectionWindowPreviewActive() {
      const index = this.playlistAnalysisProjectionWindowPreviewStartIndex;
      return index !== null && index !== undefined && Number.isFinite(Number(index));
    },

    playlistAnalysisProjectionWindowRangeForIndex(index) {
      const months = this.playlistAnalysisTimelineMonths();
      const selected = this.playlistAnalysisSelectedTimelineMonths();
      const size = this.playlistAnalysisProjectionWindowSize();
      if (!months.length || !selected.length || size <= 0) return null;
      const selectedStart = Number(selected[0].index || 0);
      const selectedEnd = Number(selected[selected.length - 1].index || 0);
      const maxStart = Math.max(selectedStart, selectedEnd - size + 1);
      const startIndex = Math.max(selectedStart, Math.min(maxStart, Number(index || 0)));
      const endIndex = Math.min(selectedEnd, startIndex + size - 1);
      if (!months[startIndex] || !months[endIndex]) return null;
      return {
        start: this.playlistAnalysisClampDateToRange(`${months[startIndex].ym}-01`),
        end: this.playlistAnalysisClampDateToRange(this.playlistAnalysisMonthEndDate(months[endIndex].ym)),
        startIndex,
      };
    },

    playlistAnalysisProjectionDisplayWindowRange() {
      if (this.playlistAnalysisProjectionWindowPreviewActive()) {
        const preview = this.playlistAnalysisProjectionWindowRangeForIndex(this.playlistAnalysisProjectionWindowPreviewStartIndex);
        if (preview && preview.start && preview.end) return preview;
      }
      return {
        start: this.playlistAnalysisClampDateToRange(this.playlistAnalysisProjectionWindowStart),
        end: this.playlistAnalysisClampDateToRange(this.playlistAnalysisProjectionWindowEnd),
        startIndex: this.playlistAnalysisTimelineMonthIndex(this.playlistAnalysisProjectionWindowStart || this.playlistAnalysisRangeStart),
      };
    },

    playlistAnalysisClampDateToRange(dateValue) {
      const value = String(dateValue || "").slice(0, 10);
      if (!value) return "";
      const rangeStart = String(this.playlistAnalysisRangeStart || this.playlistAnalysisFullRangeStart || "").slice(0, 10);
      const rangeEnd = String(this.playlistAnalysisRangeEnd || this.playlistAnalysisFullRangeEnd || "").slice(0, 10);
      if (rangeStart && value < rangeStart) return rangeStart;
      if (rangeEnd && value > rangeEnd) return rangeEnd;
      return value;
    },

    playlistAnalysisDateRangeRatio(dateValue, bounds) {
      const value = String(dateValue || "").slice(0, 10);
      if (!value || !bounds || !bounds.start || !bounds.end) return null;
      const totalDays = this.playlistAnalysisDateDiffDays(bounds.start, bounds.end) + 1;
      if (!Number.isFinite(totalDays) || totalDays <= 0) return null;
      const offsetDays = this.playlistAnalysisDateDiffDays(bounds.start, value);
      if (!Number.isFinite(offsetDays)) return null;
      return Math.max(0, Math.min(1, offsetDays / totalDays));
    },

    playlistAnalysisProjectionChartWindowStyle() {
      const selected = this.playlistAnalysisSelectedTimelineMonths();
      const displayRange = this.playlistAnalysisProjectionDisplayWindowRange();
      const windowStart = displayRange.start;
      const windowEnd = displayRange.end;
      const bounds = this.playlistAnalysisChartRangeBounds();
      const startRatio = this.playlistAnalysisDateRangeRatio(windowStart, bounds);
      const endRatio = this.playlistAnalysisDateRangeRatio(this.playlistAnalysisAddDays(windowEnd, 1), bounds);
      if (startRatio != null && endRatio != null) {
        const left = Math.max(0, Math.min(100, Math.min(startRatio, endRatio) * 100));
        const right = Math.max(0, Math.min(100, 100 - Math.max(startRatio, endRatio) * 100));
        return [`left:${left}%`, `right:${right}%`].join(";");
      }
      if (!selected.length || !windowStart || !windowEnd) {
        return "left:0%;right:100%;";
      }
      const selectedStart = Number(selected[0].index || 0);
      const selectedEnd = Number(selected[selected.length - 1].index || 0);
      const slotCount = Math.max(1, selectedEnd - selectedStart + 1);
      const startIndex = Math.max(selectedStart, Math.min(selectedEnd, this.playlistAnalysisTimelineMonthIndex(windowStart)));
      const endIndex = Math.max(selectedStart, Math.min(selectedEnd, this.playlistAnalysisTimelineMonthIndex(windowEnd)));
      const left = Math.max(0, Math.min(100, ((Math.min(startIndex, endIndex) - selectedStart) / slotCount) * 100));
      const right = Math.max(0, Math.min(100, 100 - ((Math.max(startIndex, endIndex) - selectedStart + 1) / slotCount) * 100));
      return [`left:${left}%`, `right:${right}%`].join(";");
    },

    playlistAnalysisBreakpoints() {
      const bounds = this.playlistAnalysisChartRangeBounds();
      if (!bounds) return [];
      const cacheKey = [
        Number(this.playlistAnalysisSignalsVersion || 0),
        Number(this.playlistAnalysisCandidatesVersion || 0),
        String(this.playlistAnalysisRangeStart || ""),
        String(this.playlistAnalysisRangeEnd || ""),
        String(this.playlistAnalysisCandidatesLoaded || false),
        String((Array.isArray(this.playlistAnalysisCandidates) ? this.playlistAnalysisCandidates.length : 0)),
      ].join("|");
      if (this._playlistAnalysisBreakpointsCache && this._playlistAnalysisBreakpointsCache.key === cacheKey) {
        return this._playlistAnalysisBreakpointsCache.value;
      }
      const byEvent = new Map();
      const priority = { day: 0, week: 1, month: 2 };
      const remember = (item) => {
        const id = String(item && item.id ? item.id : "").trim();
        const date = String(item && item.date ? item.date : "").slice(0, 10);
        if (!id || !date || date < bounds.start || date > bounds.end) return;
        const granularity = String(item.granularity || "event");
        const rank = priority[granularity] ?? 3;
        const sourceRank = String(item.source || "") === "candidate" ? 2 : 1;
        const current = byEvent.get(id);
        if (current) {
          if (Number(current.sourceRank || 0) > sourceRank) return;
          if (Number(current.sourceRank || 0) === sourceRank && (current.rank < rank || (current.rank === rank && String(current.date || "") <= date))) return;
        }
        byEvent.set(id, { ...item, id, date, granularity, rank, sourceRank });
      };
      (Array.isArray(this.playlistAnalysisSignals) ? this.playlistAnalysisSignals : []).forEach((signal) => {
        const id = String((signal && (signal.linked_candidate_id || signal.linked_event_id)) || "").trim();
        if (!id) return;
        remember({
          id,
          date: signal.period_date,
          granularity: signal.granularity,
          score: null,
          strengthReady: false,
          source: "signal",
          label: "语义变化点",
        });
      });
      if (this.playlistAnalysisCandidatesLoaded) {
        (Array.isArray(this.playlistAnalysisCandidates) ? this.playlistAnalysisCandidates : []).forEach((candidate) => {
          remember({
            id: candidate.event_id || candidate.id,
            date: candidate.breakpoint_date || candidate.event_date || candidate.candidate_date,
            granularity: candidate.detection_granularity || "event",
            score: candidate.boundary_z ?? candidate.score,
            boundaryScore: candidate.boundary_score,
            strengthReady: candidate.boundary_z != null || candidate.score != null,
            source: "candidate",
            label: this.playlistAnalysisEventTypeLabel(candidate.event_type),
          });
        });
      }
      const result = Array.from(byEvent.values())
        .map((item) => {
          const ratio = this.playlistAnalysisDateRangeRatio(item.date, bounds);
          return ratio == null ? null : { ...item, left: ratio * 100 };
        })
        .filter(Boolean)
        .sort((left, right) => String(left.date || "").localeCompare(String(right.date || "")));
      this._playlistAnalysisBreakpointsCache = { key: cacheKey, value: result };
      return result;
    },

    playlistAnalysisBreakpointDisplayModeOptions() {
      return PLAYLIST_ANALYSIS_BREAKPOINT_DISPLAY_MODES;
    },

    playlistAnalysisBreakpointDisplayModeClass(mode) {
      const active = String(this.playlistAnalysisBreakpointDisplayMode || "auto") === String(mode || "auto");
      return active
        ? "border-rose-400/40 bg-rose-500/15 text-rose-100"
        : "border-slate-700 bg-slate-950/30 text-slate-400 hover:bg-slate-900/70 hover:text-slate-200";
    },

    playlistAnalysisSetBreakpointDisplayMode(mode) {
      const next = String(mode || "auto").trim();
      const valid = PLAYLIST_ANALYSIS_BREAKPOINT_DISPLAY_MODES.some((item) => item.key === next);
      this.playlistAnalysisBreakpointDisplayMode = valid ? next : "auto";
      this.playlistAnalysisHoveredBreakpointItemId = "";
      this._playlistAnalysisBreakpointDisplayCache = null;
    },

    playlistAnalysisBreakpointScoreValue(item) {
      const score = item && item.score != null ? Number(item.score) : NaN;
      return Number.isFinite(score) ? score : -Infinity;
    },

    playlistAnalysisSortBreakpointsByStrength(items) {
      return (Array.isArray(items) ? items : [])
        .slice()
        .sort((left, right) => {
          const scoreDiff = this.playlistAnalysisBreakpointScoreValue(right) - this.playlistAnalysisBreakpointScoreValue(left);
          if (Number.isFinite(scoreDiff) && scoreDiff !== 0) return scoreDiff;
          const readyDiff = Number(!!(right && right.strengthReady)) - Number(!!(left && left.strengthReady));
          if (readyDiff) return readyDiff;
          return String(left && left.date ? left.date : "").localeCompare(String(right && right.date ? right.date : ""));
        });
    },

    playlistAnalysisBreakpointClusterItems(breakpoints) {
      const points = (Array.isArray(breakpoints) ? breakpoints : [])
        .slice()
        .sort((left, right) => Number(left.left || 0) - Number(right.left || 0));
      if (!points.length) return [];
      const width = this.playlistAnalysisTrendChartWidthPx() || 960;
      const bucketPercent = (PLAYLIST_ANALYSIS_BREAKPOINT_CLUSTER_MIN_PX / Math.max(1, width)) * 100;
      const groups = [];
      let group = [];
      let lastLeft = null;
      points.forEach((point) => {
        const left = Number(point.left || 0);
        if (group.length && lastLeft != null && Math.abs(left - lastLeft) > bucketPercent) {
          groups.push(group);
          group = [];
        }
        group.push(point);
        lastLeft = left;
      });
      if (group.length) groups.push(group);
      return groups.map((items, index) => {
        const sortedByDate = items.slice().sort((left, right) => String(left.date || "").localeCompare(String(right.date || "")));
        const strongest = this.playlistAnalysisSortBreakpointsByStrength(items)[0] || sortedByDate[0];
        const minLeft = Math.min(...items.map((item) => Number(item.left || 0)));
        const maxLeft = Math.max(...items.map((item) => Number(item.left || 0)));
        return {
          kind: "cluster",
          displayId: `cluster:${index}:${String(sortedByDate[0] && sortedByDate[0].id ? sortedByDate[0].id : "")}:${items.length}`,
          breakpoints: items,
          topBreakpoint: strongest,
          date: strongest && strongest.date ? strongest.date : sortedByDate[0].date,
          rangeStart: sortedByDate[0].date,
          rangeEnd: sortedByDate[sortedByDate.length - 1].date,
          left: (minLeft + maxLeft) / 2,
          rangeLeft: minLeft,
          rangeRight: maxLeft,
          count: items.length,
          score: strongest ? strongest.score : null,
          strengthReady: !!(strongest && strongest.strengthReady),
          granularity: strongest ? strongest.granularity : "event",
        };
      });
    },

    playlistAnalysisBreakpointDisplayItems() {
      const breakpoints = this.playlistAnalysisBreakpoints();
      if (!breakpoints.length) return [];
      const selectedId = String(this.playlistAnalysisSelectedCandidateId || "");
      const mode = PLAYLIST_ANALYSIS_BREAKPOINT_DISPLAY_MODES.some((item) => item.key === String(this.playlistAnalysisBreakpointDisplayMode || "auto"))
        ? String(this.playlistAnalysisBreakpointDisplayMode || "auto")
        : "auto";
      const width = Math.round(this.playlistAnalysisTrendChartWidthPx() || 0);
      const cacheKey = [
        Number(this.playlistAnalysisSignalsVersion || 0),
        Number(this.playlistAnalysisCandidatesVersion || 0),
        String(this.playlistAnalysisRangeStart || ""),
        String(this.playlistAnalysisRangeEnd || ""),
        String(this.playlistAnalysisCandidatesLoaded || false),
        String(breakpoints.length),
        selectedId,
        mode,
        width,
      ].join("|");
      if (this._playlistAnalysisBreakpointDisplayCache && this._playlistAnalysisBreakpointDisplayCache.key === cacheKey) {
        return this._playlistAnalysisBreakpointDisplayCache.value;
      }
      const selectedMonths = this.playlistAnalysisSelectedTimelineMonths().length;
      const strongest = this.playlistAnalysisSortBreakpointsByStrength(breakpoints);
      const labelIds = new Set(
        strongest
          .filter((item) => item && item.strengthReady)
          .slice(0, PLAYLIST_ANALYSIS_BREAKPOINT_TOP_VISIBLE_COUNT)
          .map((item) => String(item.id || ""))
      );
      const asSingle = (breakpoint, { showLabel = false, overlay = false } = {}) => ({
        ...breakpoint,
        kind: "single",
        displayId: `${overlay ? "overlay" : "single"}:${String(breakpoint.id || "")}`,
        breakpoint,
        showLabelByDefault: showLabel,
        overlay,
      });
      let result = [];
      if (mode === "all") {
        result = breakpoints.map((item) => asSingle(item, { showLabel: labelIds.has(String(item.id || "")) }));
      } else if (mode === "important") {
        result = strongest.slice(0, PLAYLIST_ANALYSIS_BREAKPOINT_IMPORTANT_COUNT).map((item) => asSingle(item, { showLabel: true }));
      } else {
        const shouldExpand =
          mode === "auto" &&
          selectedMonths > 0 &&
          selectedMonths <= PLAYLIST_ANALYSIS_BREAKPOINT_AUTO_MONTH_LIMIT &&
          breakpoints.length <= PLAYLIST_ANALYSIS_BREAKPOINT_AUTO_COUNT_LIMIT;
        if (shouldExpand) {
          result = breakpoints.map((item) => asSingle(item, { showLabel: labelIds.has(String(item.id || "")) }));
        } else {
          result = this.playlistAnalysisBreakpointClusterItems(breakpoints);
          if (mode === "auto") {
            const overlayIds = new Set(
              strongest
                .filter((item) => item && item.strengthReady)
                .slice(0, PLAYLIST_ANALYSIS_BREAKPOINT_TOP_VISIBLE_COUNT)
                .map((item) => String(item.id || ""))
            );
            if (selectedId) overlayIds.add(selectedId);
            const overlays = breakpoints
              .filter((item) => overlayIds.has(String(item.id || "")))
              .map((item) => asSingle(item, { showLabel: String(item.id || "") === selectedId, overlay: true }));
            result = result.concat(overlays);
          }
        }
      }
      this._playlistAnalysisBreakpointDisplayCache = { key: cacheKey, value: result };
      return result;
    },

    playlistAnalysisHoveredBreakpointDisplayClusters() {
      return this.playlistAnalysisBreakpointDisplayItems().filter((item) => item.kind === "cluster" && this.playlistAnalysisBreakpointDisplayItemHovered(item));
    },

    playlistAnalysisBreakpointDisplayItemClass(item) {
      if (item && item.kind === "cluster") {
        return "top-0 h-9 w-10 -translate-x-1/2";
      }
      return "bottom-7 top-4 w-0 -translate-x-1/2 border-l border-dashed border-rose-300/65 hover:border-rose-200 focus:border-rose-200";
    },

    playlistAnalysisBreakpointDisplayItemStyle(item) {
      if (item && item.kind === "cluster") {
        const left = Math.max(0, Math.min(100, Number(item.left || 0)));
        return `left:${left}%;z-index:11;`;
      }
      return `${this.playlistAnalysisBreakpointStyle(item)}z-index:${item && item.overlay ? 13 : 12};`;
    },

    playlistAnalysisBreakpointClusterLineStyle(item) {
      const intensity = this.playlistAnalysisBreakpointIntensity(item);
      const alpha = item && item.strengthReady ? 0.34 + intensity * 0.5 : 0.26;
      const height = Math.min(28, 14 + Math.sqrt(Math.max(1, Number(item && item.count ? item.count : 1))) * 3);
      return `height:${height}px;border-color:rgba(251,113,133,${alpha});`;
    },

    playlistAnalysisBreakpointClusterDotStyle(item) {
      const intensity = this.playlistAnalysisBreakpointIntensity(item);
      const count = Math.max(1, Number(item && item.count ? item.count : 1));
      const size = Math.min(18, 6 + Math.sqrt(count) * 2 + intensity * 3);
      const fillAlpha = item && item.strengthReady ? 0.58 + intensity * 0.36 : 0.46;
      const glowAlpha = item && item.strengthReady ? 0.16 + intensity * 0.28 : 0.12;
      return [
        `width:${size}px`,
        `height:${size}px`,
        `background:rgba(251,113,133,${fillAlpha})`,
        `box-shadow:0 0 ${8 + intensity * 10}px rgba(251,113,133,${glowAlpha})`,
      ].join(";");
    },

    playlistAnalysisBreakpointClusterRangeStyle(item) {
      const left = Math.max(0, Math.min(100, Number(item && item.rangeLeft != null ? item.rangeLeft : item && item.left != null ? item.left : 0)));
      const right = Math.max(left, Math.min(100, Number(item && item.rangeRight != null ? item.rangeRight : left)));
      const width = Math.max(0.6, right - left);
      return `left:${left}%;width:${width}%;`;
    },

    playlistAnalysisBreakpointClusterCountLabel(item) {
      const count = Math.max(1, Number(item && item.count ? item.count : 1));
      return count > 99 ? "99+" : `+${count}`;
    },

    playlistAnalysisBreakpointDisplayItemTarget(item) {
      if (!item) return null;
      if (item.kind === "cluster") return item.topBreakpoint || null;
      return item.breakpoint || item;
    },

    playlistAnalysisSelectBreakpointDisplayItem(item) {
      const target = this.playlistAnalysisBreakpointDisplayItemTarget(item);
      if (target) this.playlistAnalysisSelectBreakpoint(target);
    },

    playlistAnalysisHoverBreakpointDisplayItem(item) {
      this.playlistAnalysisHoveredBreakpointItemId = item && item.displayId ? String(item.displayId) : "";
    },

    playlistAnalysisBreakpointDisplayItemHovered(item) {
      return !!item && String(item.displayId || "") === String(this.playlistAnalysisHoveredBreakpointItemId || "");
    },

    playlistAnalysisBreakpointDisplayItemSelected(item) {
      const selectedId = String(this.playlistAnalysisSelectedCandidateId || "");
      if (!selectedId || !item) return false;
      if (item.kind === "cluster") {
        return (Array.isArray(item.breakpoints) ? item.breakpoints : []).some((breakpoint) => String(breakpoint.id || "") === selectedId);
      }
      const target = this.playlistAnalysisBreakpointDisplayItemTarget(item);
      return !!target && String(target.id || "") === selectedId;
    },

    playlistAnalysisBreakpointShowLabel(item) {
      if (!item || item.kind !== "single") return false;
      return !!item.showLabelByDefault || this.playlistAnalysisBreakpointDisplayItemHovered(item) || this.playlistAnalysisBreakpointDisplayItemSelected(item);
    },

    playlistAnalysisBreakpointDisplayItemTitle(item) {
      if (item && item.kind === "cluster") {
        const count = Number(item.count || 0);
        const score = item.score != null ? Number(item.score) : NaN;
        const scoreText = Number.isFinite(score) ? ` · 最高标准化语义变化 ${score.toFixed(2)}` : " · 强度加载中";
        const topDate = item.topBreakpoint && item.topBreakpoint.date ? ` · 最强 ${item.topBreakpoint.date}` : "";
        const range = item.rangeStart && item.rangeEnd && item.rangeStart !== item.rangeEnd ? `${item.rangeStart} ~ ${item.rangeEnd}` : String(item.rangeStart || item.date || "");
        return `语义变化点簇 ${range} · ${count} 个${scoreText}${topDate}`;
      }
      return this.playlistAnalysisBreakpointTitle(item);
    },

    playlistAnalysisBreakpointStyle(breakpoint) {
      const left = Math.max(0, Math.min(100, Number(breakpoint && breakpoint.left != null ? breakpoint.left : 0)));
      const intensity = this.playlistAnalysisBreakpointIntensity(breakpoint);
      const alpha = breakpoint && breakpoint.strengthReady ? 0.28 + intensity * 0.62 : 0.32;
      return `left:${left}%;border-color:rgba(251,113,133,${alpha});`;
    },

    playlistAnalysisBreakpointIntensity(breakpoint) {
      const score = breakpoint && breakpoint.score != null ? Number(breakpoint.score) : NaN;
      if (!Number.isFinite(score)) return 0;
      const span = PLAYLIST_ANALYSIS_BREAKPOINT_Z_MAX - PLAYLIST_ANALYSIS_BREAKPOINT_Z_MIN;
      return Math.max(0, Math.min(1, (score - PLAYLIST_ANALYSIS_BREAKPOINT_Z_MIN) / span));
    },

    playlistAnalysisBreakpointDotStyle(breakpoint) {
      const intensity = this.playlistAnalysisBreakpointIntensity(breakpoint);
      const ready = !!(breakpoint && breakpoint.strengthReady);
      const size = ready ? 6 + intensity * 6 : 6;
      const fillAlpha = ready ? 0.62 + intensity * 0.34 : 0.5;
      const glowAlpha = ready ? 0.2 + intensity * 0.32 : 0.16;
      const spread = ready ? 7 + intensity * 9 : 7;
      return [
        `width:${size}px`,
        `height:${size}px`,
        `background:rgba(251,113,133,${fillAlpha})`,
        `box-shadow:0 0 ${spread}px rgba(251,113,133,${glowAlpha})`,
      ].join(";");
    },

    playlistAnalysisBreakpointLabelStyle(breakpoint) {
      const intensity = this.playlistAnalysisBreakpointIntensity(breakpoint);
      const ready = !!(breakpoint && breakpoint.strengthReady);
      const alpha = ready ? 0.72 + intensity * 0.24 : 0.58;
      return `opacity:${alpha};`;
    },

    playlistAnalysisBreakpointTitle(breakpoint) {
      const date = String((breakpoint && breakpoint.date) || "");
      const score = breakpoint && breakpoint.score != null ? Number(breakpoint.score) : null;
      const granularity = String((breakpoint && breakpoint.granularity) || "").trim();
      if (!breakpoint || !breakpoint.strengthReady || !Number.isFinite(score)) return `语义变化点 ${date} · 强度加载中`;
      const granularityText = granularity ? ` · ${granularity}` : "";
      return `语义变化点 ${date} · 标准化语义变化 ${score.toFixed(2)}${granularityText}`;
    },

    playlistAnalysisBreakpointShortLabel(breakpoint) {
      const date = String((breakpoint && breakpoint.date) || "");
      if (date.length >= 10) return `${date.slice(2, 4)}/${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`;
      return date || "变化点";
    },

    playlistAnalysisSelectBreakpoint(breakpoint) {
      const date = String((breakpoint && breakpoint.date) || "").slice(0, 10);
      if (!date) return;
      this.playlistAnalysisStopProjectionPlayback();
      if (breakpoint && breakpoint.id) this.playlistAnalysisSelectedCandidateId = String(breakpoint.id);
      this.playlistAnalysisSetProjectionWindowCenteredAtDate(date, { anchorDate: date });
    },

    playlistAnalysisProjectionWindowDragClass() {
      if (!this.playlistAnalysisProjectionCanPlay()) return "cursor-not-allowed opacity-60";
      return this.playlistAnalysisProjectionWindowDragging
        ? "cursor-grabbing border-sky-200/70 bg-sky-400/20 shadow-[0_0_18px_rgba(56,189,248,0.22)]"
        : "cursor-grab border-sky-400/40 bg-sky-400/10 hover:border-sky-200/70 hover:bg-sky-400/20";
    },

    playlistAnalysisProjectionWindowStepPx() {
      const selected = this.playlistAnalysisSelectedTimelineMonths();
      if (selected.length <= 1) return 0;
      const width = this.playlistAnalysisTrendChartWidthPx();
      return width > 0 ? width / Math.max(1, selected.length) : 0;
    },

    playlistAnalysisTrendChartWidthPx() {
      try {
        const el = this.$refs && (this.$refs.playlistAnalysisProjectionWindowLayer || this.$refs.playlistAnalysisTrendChartFrame || this.$refs.playlistAnalysisTrendChart);
        const width = el && el.clientWidth ? Number(el.clientWidth) : 0;
        return Number.isFinite(width) && width > 0 ? width : 0;
      } catch {
        return 0;
      }
    },

    playlistAnalysisReleaseProjectionWindowDrag() {
      try {
        if (this._playlistAnalysisProjectionWindowDragMove) window.removeEventListener("pointermove", this._playlistAnalysisProjectionWindowDragMove);
      } catch {
        // ignore
      }
      try {
        if (this._playlistAnalysisProjectionWindowDragEnd) {
          window.removeEventListener("pointerup", this._playlistAnalysisProjectionWindowDragEnd);
          window.removeEventListener("pointercancel", this._playlistAnalysisProjectionWindowDragEnd);
        }
      } catch {
        // ignore
      }
      this._playlistAnalysisProjectionWindowDragMove = null;
      this._playlistAnalysisProjectionWindowDragEnd = null;
      this.playlistAnalysisProjectionWindowDragging = false;
      this.playlistAnalysisProjectionWindowDragPointerId = null;
      this.playlistAnalysisProjectionWindowPreviewStartIndex = null;
    },

    playlistAnalysisStartProjectionWindowDrag(ev) {
      if (!this.playlistAnalysisProjectionCanPlay()) return;
      if (!ev || (ev.pointerType === "mouse" && ev.button !== 0)) return;
      this.playlistAnalysisReleaseRangeDrag();
      this.playlistAnalysisReleaseProjectionWindowDrag();
      this.playlistAnalysisStopProjectionPlayback();
      this.playlistAnalysisEnsureProjectionWindow();
      this.playlistAnalysisProjectionWindowDragging = true;
      this.playlistAnalysisProjectionWindowDragPointerId = ev.pointerId;
      this.playlistAnalysisProjectionWindowDragStartX = Number(ev.clientX || 0);
      this.playlistAnalysisProjectionWindowDragStartIndex = this.playlistAnalysisTimelineMonthIndex(this.playlistAnalysisProjectionWindowStart || this.playlistAnalysisRangeStart);
      this.playlistAnalysisProjectionWindowPreviewStartIndex = this.playlistAnalysisProjectionWindowDragStartIndex;
      this._playlistAnalysisProjectionWindowDragMove = (nextEv) => this.playlistAnalysisMoveProjectionWindowDrag(nextEv);
      this._playlistAnalysisProjectionWindowDragEnd = (nextEv) => this.playlistAnalysisEndProjectionWindowDrag(nextEv);
      try {
        window.addEventListener("pointermove", this._playlistAnalysisProjectionWindowDragMove, { passive: false });
        window.addEventListener("pointerup", this._playlistAnalysisProjectionWindowDragEnd, { passive: false });
        window.addEventListener("pointercancel", this._playlistAnalysisProjectionWindowDragEnd, { passive: false });
      } catch {
        // ignore
      }
      try {
        if (ev.currentTarget && ev.currentTarget.setPointerCapture) ev.currentTarget.setPointerCapture(ev.pointerId);
      } catch {
        // ignore
      }
      try {
        if (ev.cancelable) ev.preventDefault();
        ev.stopPropagation();
      } catch {
        // ignore
      }
    },

    playlistAnalysisMoveProjectionWindowDrag(ev) {
      if (!this.playlistAnalysisProjectionWindowDragging) return;
      if (ev && this.playlistAnalysisProjectionWindowDragPointerId !== null && ev.pointerId !== this.playlistAnalysisProjectionWindowDragPointerId) return;
      const selected = this.playlistAnalysisSelectedTimelineMonths();
      const size = this.playlistAnalysisProjectionWindowSize();
      if (!selected.length || size <= 0) return;
      const selectedStart = Number(selected[0].index || 0);
      const selectedEnd = Number(selected[selected.length - 1].index || 0);
      const maxStart = Math.max(selectedStart, selectedEnd - size + 1);
      const stepPx = this.playlistAnalysisProjectionWindowStepPx();
      if (stepPx <= 0) return;
      const delta = Math.round((Number((ev && ev.clientX) || 0) - Number(this.playlistAnalysisProjectionWindowDragStartX || 0)) / stepPx);
      const nextStart = Math.max(selectedStart, Math.min(maxStart, Number(this.playlistAnalysisProjectionWindowDragStartIndex || selectedStart) + delta));
      if (nextStart !== Number(this.playlistAnalysisProjectionWindowPreviewStartIndex)) {
        this.playlistAnalysisProjectionWindowPreviewStartIndex = nextStart;
      }
      try {
        if (ev && ev.cancelable) ev.preventDefault();
        if (ev) ev.stopPropagation();
      } catch {
        // ignore
      }
    },

    playlistAnalysisEndProjectionWindowDrag(ev) {
      if (!this.playlistAnalysisProjectionWindowDragging) return;
      if (ev && this.playlistAnalysisProjectionWindowDragPointerId !== null && ev.pointerId !== this.playlistAnalysisProjectionWindowDragPointerId) return;
      this.playlistAnalysisMoveProjectionWindowDrag(ev);
      const nextStart = this.playlistAnalysisProjectionWindowPreviewActive()
        ? Number(this.playlistAnalysisProjectionWindowPreviewStartIndex)
        : this.playlistAnalysisTimelineMonthIndex(this.playlistAnalysisProjectionWindowStart || this.playlistAnalysisRangeStart);
      this.playlistAnalysisReleaseProjectionWindowDrag();
      this.playlistAnalysisSetProjectionWindowByStartIndex(nextStart);
    },

    playlistAnalysisProjectionWindowKeydown(ev) {
      const key = String((ev && ev.key) || "");
      const delta = key === "ArrowLeft" ? -1 : key === "ArrowRight" ? 1 : 0;
      if (!delta || !this.playlistAnalysisProjectionCanPlay()) return;
      this.playlistAnalysisStopProjectionPlayback();
      this.playlistAnalysisProjectionStep(delta);
      try {
        if (ev.cancelable) ev.preventDefault();
        ev.stopPropagation();
      } catch {
        // ignore
      }
    },

    playlistAnalysisProjectionCanPlay() {
      const selected = this.playlistAnalysisSelectedTimelineMonths();
      return selected.length > this.playlistAnalysisProjectionWindowSize();
    },

    playlistAnalysisRangeLabel() {
      const start = this.playlistAnalysisRangeStart || this.playlistAnalysisFullRangeStart || "-";
      const end = this.playlistAnalysisRangeEnd || this.playlistAnalysisFullRangeEnd || "-";
      return `${start} ~ ${end}`;
    },

    playlistAnalysisHeaderContextLabel() {
      const summary = this.playlistAnalysisSummary;
      if (summary && !summary.last_ready_run_id) return summary.running ? "语义快照构建中" : "尚未构建语义快照";
      if (String(this.playlistAnalysisTab || "trend") === "events") {
        if (this.playlistAnalysisCandidatesLoading) return "事件加载中";
        if (!this.playlistAnalysisCandidatesLoaded) return "事件未加载";
        const items = Array.isArray(this.playlistAnalysisCandidates) ? this.playlistAnalysisCandidates : [];
        if (!items.length) return "暂无事件";
        const accepted = items.filter((item) => String(item.status || "").trim().toLowerCase() === "accepted").length;
        return `${items.length} 个候选 · ${accepted} 已确认`;
      }
      const start = this.playlistAnalysisRangeStart || this.playlistAnalysisFullRangeStart || "";
      const end = this.playlistAnalysisRangeEnd || this.playlistAnalysisFullRangeEnd || "";
      if (!start || !end) return "暂无信号范围";
      return `信号范围 ${start} ~ ${end}`;
    },

    playlistAnalysisHandleRangeChange() {
      if (this.playlistAnalysisRangeStart && this.playlistAnalysisRangeEnd && this.playlistAnalysisRangeStart > this.playlistAnalysisRangeEnd) {
        const start = this.playlistAnalysisRangeStart;
        this.playlistAnalysisRangeStart = this.playlistAnalysisRangeEnd;
        this.playlistAnalysisRangeEnd = start;
      }
      this.playlistAnalysisDestroyChart();
      this.$nextTick(() => this.playlistAnalysisRequestChartRender());
    },

    playlistAnalysisShiftMonth(ym, offset) {
      const value = String(ym || "").slice(0, 7);
      const parts = value.split("-").map((item) => Number(item));
      if (parts.length !== 2 || !parts[0] || !parts[1]) return "";
      const date = new Date(parts[0], parts[1] - 1 + Number(offset || 0), 1);
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      return `${year}-${month}`;
    },

    playlistAnalysisMonthEndDate(ym) {
      const value = String(ym || "").slice(0, 7);
      const parts = value.split("-").map((item) => Number(item));
      if (parts.length !== 2 || !parts[0] || !parts[1]) return "";
      const day = new Date(parts[0], parts[1], 0).getDate();
      return `${value}-${String(day).padStart(2, "0")}`;
    },

    playlistAnalysisTimelineMonths() {
      const bounds = this.playlistAnalysisTimelineBounds();
      const start = String((bounds && bounds.start) || "").slice(0, 7);
      const end = String((bounds && bounds.end) || "").slice(0, 7);
      if (!start || !end || start > end) return [];
      const cacheKey = `${String(this.playlistAnalysisTimelineScope || "normal")}|${start}|${end}`;
      if (this._playlistAnalysisTimelineMonthsCache && this._playlistAnalysisTimelineMonthsCache.key === cacheKey) {
        return this._playlistAnalysisTimelineMonthsCache.value;
      }
      const months = [];
      for (let ym = start; ym && ym <= end; ym = this.playlistAnalysisShiftMonth(ym, 1)) {
        months.push({
          index: months.length,
          ym,
        });
      }
      this._playlistAnalysisTimelineMonthsCache = { key: cacheKey, value: months };
      return months;
    },

    playlistAnalysisTimelineTickStyle() {
      const count = this.playlistAnalysisTimelineMonths().length;
      const step = count <= 1 ? 100 : 100 / Math.max(1, count - 1);
      return [
        "background-image:linear-gradient(90deg,rgba(100,116,139,0.38) 1px,transparent 1px)",
        `background-size:${step}% 12px`,
        "background-position:left center",
        "background-repeat:repeat-x",
      ].join(";");
    },

    playlistAnalysisTimelineDensityBars() {
      const months = this.playlistAnalysisTimelineMonths();
      const rows = Array.isArray(this.playlistAnalysisTimelineDensity) ? this.playlistAnalysisTimelineDensity : [];
      if (!months.length || !rows.length) return [];
      const monthIndex = new Map(months.map((item) => [String(item.ym || ""), Number(item.index || 0)]));
      const visible = rows
        .map((item) => ({
          ym: String((item && item.period_date) || "").slice(0, 7),
          count: Math.max(0, Number((item && item.event_count) || 0)),
        }))
        .filter((item) => item.ym && monthIndex.has(item.ym) && item.count > 0);
      if (!visible.length) return [];
      const maxCount = Math.max(...visible.map((item) => item.count), 1);
      const maxIndex = Math.max(1, months.length - 1);
      const width = Math.max(0.16, Math.min(1.2, 82 / Math.max(1, months.length)));
      return visible.map((item) => {
        const index = Number(monthIndex.get(item.ym) || 0);
        const height = 2 + Math.round(Math.sqrt(item.count / maxCount) * 9);
        return {
          key: `${item.ym}-${item.count}`,
          label: `${item.ym} · ${this.formatInteger(item.count)} 个事件`,
          style: `left:${(index / maxIndex) * 100}%;width:${width}%;height:${height}px;transform:translateX(-50%);`,
        };
      });
    },

    playlistAnalysisTimelineTrackWidthPx() {
      try {
        const el = this.$refs && this.$refs.playlistAnalysisTimelineTrack;
        const width = el && el.clientWidth ? Number(el.clientWidth) : 0;
        return Number.isFinite(width) && width > 0 ? width : 0;
      } catch {
        return 0;
      }
    },

    playlistAnalysisTimelineYearMarks() {
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return [];
      const marks = [];
      months.forEach((month) => {
        const ym = String(month.ym || "");
        const year = ym.slice(0, 4);
        const monthPart = ym.slice(5, 7);
        if (!year) return;
        if (Number(month.index || 0) === 0 || monthPart === "01") {
          marks.push({ key: `${year}-${month.index}`, label: year, index: Number(month.index || 0) });
        }
      });
      if (marks.length <= 1) return marks;
      let maxMarks = 18;
      try {
        const width = this.playlistAnalysisTimelineTrackWidthPx() || (window && window.innerWidth ? Number(window.innerWidth) : 1280);
        if (width < 520) maxMarks = 5;
        else if (width < 900) maxMarks = 8;
        else if (width < 1280) maxMarks = 12;
      } catch {
        // ignore
      }
      const step = Math.max(1, Math.ceil(marks.length / maxMarks));
      return marks.filter((mark, index) => index === 0 || index === marks.length - 1 || index % step === 0);
    },

    playlistAnalysisTimelineYearTicks() {
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return [];
      return months
        .filter((month) => Number(month.index || 0) === 0 || String(month.ym || "").slice(5, 7) === "01")
        .map((month) => ({ key: `year-tick-${month.ym}-${month.index}`, index: Number(month.index || 0) }));
    },

    playlistAnalysisTimelineYearMarkStyle(mark) {
      const months = this.playlistAnalysisTimelineMonths();
      const maxIndex = Math.max(1, months.length - 1);
      const index = Math.max(0, Math.min(Number(mark && mark.index != null ? mark.index : 0), maxIndex));
      const percent = (index / maxIndex) * 100;
      const transform = index <= 0 ? "translateX(0)" : index >= maxIndex ? "translateX(-100%)" : "translateX(-50%)";
      return `left:${percent}%;transform:${transform};`;
    },

    playlistAnalysisTimelineMonthIndex(dateValue) {
      const ym = String(dateValue || "").slice(0, 7);
      const months = this.playlistAnalysisTimelineMonths();
      const index = months.findIndex((item) => item.ym === ym);
      if (index >= 0) return index;
      if (!months.length) return 0;
      if (ym && ym < months[0].ym) return 0;
      return months.length - 1;
    },

    playlistAnalysisTimelineStartIndex() {
      return this.playlistAnalysisTimelineMonthIndex(this.playlistAnalysisRangeStart || this.playlistAnalysisFullRangeStart);
    },

    playlistAnalysisTimelineEndIndex() {
      return this.playlistAnalysisTimelineMonthIndex(this.playlistAnalysisRangeEnd || this.playlistAnalysisFullRangeEnd);
    },

    playlistAnalysisTimelinePreviewActive() {
      const start = this.playlistAnalysisTimelinePreviewStartIndex;
      const end = this.playlistAnalysisTimelinePreviewEndIndex;
      if (start === null || start === undefined || end === null || end === undefined) return false;
      return (
        Number.isFinite(Number(start)) &&
        Number.isFinite(Number(end))
      );
    },

    playlistAnalysisTimelineDisplayStartIndex() {
      if (this.playlistAnalysisTimelinePreviewActive()) {
        return Math.min(Number(this.playlistAnalysisTimelinePreviewStartIndex), Number(this.playlistAnalysisTimelinePreviewEndIndex));
      }
      return Math.min(this.playlistAnalysisTimelineStartIndex(), this.playlistAnalysisTimelineEndIndex());
    },

    playlistAnalysisTimelineDisplayEndIndex() {
      if (this.playlistAnalysisTimelinePreviewActive()) {
        return Math.max(Number(this.playlistAnalysisTimelinePreviewStartIndex), Number(this.playlistAnalysisTimelinePreviewEndIndex));
      }
      return Math.max(this.playlistAnalysisTimelineStartIndex(), this.playlistAnalysisTimelineEndIndex());
    },

    playlistAnalysisSetTimelinePreview(startIndex, endIndex) {
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return;
      const maxIndex = months.length - 1;
      const start = Math.max(0, Math.min(maxIndex, Number(startIndex || 0)));
      const end = Math.max(0, Math.min(maxIndex, Number(endIndex || 0)));
      this.playlistAnalysisTimelinePreviewStartIndex = Math.min(start, end);
      this.playlistAnalysisTimelinePreviewEndIndex = Math.max(start, end);
    },

    playlistAnalysisClearTimelinePreview() {
      this.playlistAnalysisTimelinePreviewStartIndex = null;
      this.playlistAnalysisTimelinePreviewEndIndex = null;
    },

    playlistAnalysisSignalsCacheKey(pid, start, end) {
      const runId = this.playlistAnalysisSummary && this.playlistAnalysisSummary.last_ready_run_id ? String(this.playlistAnalysisSummary.last_ready_run_id) : "";
      return [String(pid || ""), runId, String(start || ""), String(end || "")].join("|");
    },

    playlistAnalysisApplySignalsPayload(payload, requestedStart, requestedEnd) {
      this.playlistAnalysisSignals = Array.isArray(payload) ? payload : [];
      this.playlistAnalysisPeriods = this.playlistAnalysisSignals.filter((item) => String(item.granularity || "") === "day");
      this.playlistAnalysisInvalidateSignalCaches();
      this.playlistAnalysisSignalsLoadedRangeStart = String(requestedStart || "");
      this.playlistAnalysisSignalsLoadedRangeEnd = String(requestedEnd || "");
      if (!this.playlistAnalysisFullRangeStart || !this.playlistAnalysisFullRangeEnd) this.playlistAnalysisInitializeRange();
      this.playlistAnalysisEnsureProjectionWindow();
      this.$nextTick(() => this.playlistAnalysisRequestChartRender());
      this.playlistAnalysisScheduleBreakpointCandidateHydration();
    },

    playlistAnalysisScheduleBreakpointCandidateHydration() {
      if (this.playlistAnalysisCandidatesLoaded || this.playlistAnalysisCandidatesLoading) return;
      if (String(this.playlistAnalysisTab || "trend") !== "trend") return;
      const hasLinkedBreakpoints = (Array.isArray(this.playlistAnalysisSignals) ? this.playlistAnalysisSignals : []).some((item) =>
        String((item && (item.linked_candidate_id || item.linked_event_id)) || "").trim()
      );
      if (!hasLinkedBreakpoints) return;
      const runId = this.playlistAnalysisSummary && this.playlistAnalysisSummary.last_ready_run_id ? String(this.playlistAnalysisSummary.last_ready_run_id) : "";
      if (!runId || String(this._playlistAnalysisCandidateHydrationRunId || "") === runId) return;
      this._playlistAnalysisCandidateHydrationRunId = runId;
      setTimeout(() => {
        if (String(this.playlistAnalysisTab || "trend") !== "trend") return;
        if (this.playlistAnalysisCandidatesLoaded || this.playlistAnalysisCandidatesLoading) return;
        this.playlistLoadAnalysisCandidates({ preserveSelection: true, selectDetail: false }).catch((e) => {
          if (String(this._playlistAnalysisCandidateHydrationRunId || "") === runId) this._playlistAnalysisCandidateHydrationRunId = "";
          this.playlistAnalysisError = e && e.message ? e.message : String(e);
        });
      }, 0);
    },

    playlistAnalysisTimelineSelectionStyle() {
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return "left:0%;right:100%";
      if (months.length === 1) return "left:0%;right:0%";
      const maxIndex = Math.max(1, months.length - 1);
      const start = this.playlistAnalysisTimelineDisplayStartIndex();
      const end = this.playlistAnalysisTimelineDisplayEndIndex();
      return [`left:${(start / maxIndex) * 100}%`, `right:${100 - (end / maxIndex) * 100}%`].join(";");
    },

    playlistAnalysisTimelineRangeCanMove() {
      const months = this.playlistAnalysisTimelineMonths();
      if (months.length <= 1) return false;
      const start = Math.min(this.playlistAnalysisTimelineStartIndex(), this.playlistAnalysisTimelineEndIndex());
      const end = Math.max(this.playlistAnalysisTimelineStartIndex(), this.playlistAnalysisTimelineEndIndex());
      return end - start < months.length - 1;
    },

    playlistAnalysisTimelineSelectionDragClass() {
      if (!this.playlistAnalysisTimelineRangeCanMove()) return "cursor-not-allowed opacity-50";
      return this.playlistAnalysisRangeDragging
        ? "cursor-grabbing bg-emerald-300/10 ring-1 ring-emerald-200/40"
        : "cursor-grab hover:bg-emerald-300/10";
    },

    playlistAnalysisTimelineHandleStyle(edge) {
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return "left:0%;transform:translateX(0);";
      const maxIndex = Math.max(1, months.length - 1);
      const index = String(edge || "") === "end" ? this.playlistAnalysisTimelineDisplayEndIndex() : this.playlistAnalysisTimelineDisplayStartIndex();
      const clampedIndex = Math.max(0, Math.min(Number(index || 0), maxIndex));
      const percent = (clampedIndex / maxIndex) * 100;
      let transform = String(edge || "") === "end" ? "translateX(0)" : "translateX(-100%)";
      if (clampedIndex <= 0) transform = "translateX(0)";
      if (clampedIndex >= maxIndex) transform = "translateX(-100%)";
      return `left:${percent}%;transform:${transform};`;
    },

    playlistAnalysisTimelineHandleClass(edge) {
      const active = String(this.playlistAnalysisTimelineHandleDragging || "") === String(edge || "");
      return active
        ? "cursor-grabbing border-emerald-300/70 bg-slate-900/95 shadow-[0_0_14px_rgba(16,185,129,0.22)]"
        : "cursor-ew-resize border-slate-500/70 bg-slate-950/85 hover:border-emerald-300/70 hover:bg-slate-900/95";
    },

    playlistAnalysisReleaseRangeDrag() {
      try {
        if (this._playlistAnalysisRangeDragMove) window.removeEventListener("pointermove", this._playlistAnalysisRangeDragMove);
      } catch {
        // ignore
      }
      try {
        if (this._playlistAnalysisRangeDragEnd) {
          window.removeEventListener("pointerup", this._playlistAnalysisRangeDragEnd);
          window.removeEventListener("pointercancel", this._playlistAnalysisRangeDragEnd);
        }
      } catch {
        // ignore
      }
      this._playlistAnalysisRangeDragMove = null;
      this._playlistAnalysisRangeDragEnd = null;
      this.playlistAnalysisRangeDragging = false;
      this.playlistAnalysisRangeDragPointerId = null;
      this.playlistAnalysisReleaseTimelineHandleDrag();
    },

    playlistAnalysisReleaseTimelineHandleDrag() {
      try {
        if (this._playlistAnalysisTimelineHandleDragMove) window.removeEventListener("pointermove", this._playlistAnalysisTimelineHandleDragMove);
      } catch {
        // ignore
      }
      try {
        if (this._playlistAnalysisTimelineHandleDragEnd) {
          window.removeEventListener("pointerup", this._playlistAnalysisTimelineHandleDragEnd);
          window.removeEventListener("pointercancel", this._playlistAnalysisTimelineHandleDragEnd);
        }
      } catch {
        // ignore
      }
      this._playlistAnalysisTimelineHandleDragMove = null;
      this._playlistAnalysisTimelineHandleDragEnd = null;
      this.playlistAnalysisTimelineHandleDragging = "";
      this.playlistAnalysisTimelineHandlePointerId = null;
      if (!this.playlistAnalysisRangeDragging) this.playlistAnalysisClearTimelinePreview();
    },

    playlistAnalysisStartRangeDrag(ev) {
      if (!this.playlistAnalysisTimelineRangeCanMove()) return;
      if (!ev || (ev.pointerType === "mouse" && ev.button !== 0)) return;
      const months = this.playlistAnalysisTimelineMonths();
      if (months.length <= 1) return;
      this.playlistAnalysisReleaseProjectionWindowDrag();
      this.playlistAnalysisReleaseRangeDrag();
      this.playlistAnalysisRangeDragging = true;
      this.playlistAnalysisRangeDragPointerId = ev.pointerId;
      this.playlistAnalysisRangeDragStartX = Number(ev.clientX || 0);
      this.playlistAnalysisRangeDragStartStartIndex = Math.min(this.playlistAnalysisTimelineStartIndex(), this.playlistAnalysisTimelineEndIndex());
      this.playlistAnalysisRangeDragStartEndIndex = Math.max(this.playlistAnalysisTimelineStartIndex(), this.playlistAnalysisTimelineEndIndex());
      this.playlistAnalysisSetTimelinePreview(this.playlistAnalysisRangeDragStartStartIndex, this.playlistAnalysisRangeDragStartEndIndex);
      this._playlistAnalysisRangeDragMove = (nextEv) => this.playlistAnalysisMoveRangeDrag(nextEv);
      this._playlistAnalysisRangeDragEnd = (nextEv) => this.playlistAnalysisEndRangeDrag(nextEv);
      try {
        window.addEventListener("pointermove", this._playlistAnalysisRangeDragMove, { passive: false });
        window.addEventListener("pointerup", this._playlistAnalysisRangeDragEnd, { passive: false });
        window.addEventListener("pointercancel", this._playlistAnalysisRangeDragEnd, { passive: false });
      } catch {
        // ignore
      }
      try {
        if (ev.currentTarget && ev.currentTarget.setPointerCapture) ev.currentTarget.setPointerCapture(ev.pointerId);
      } catch {
        // ignore
      }
      try {
        if (ev.cancelable) ev.preventDefault();
        ev.stopPropagation();
      } catch {
        // ignore
      }
    },

    playlistAnalysisMoveRangeDrag(ev) {
      if (!this.playlistAnalysisRangeDragging) return;
      if (ev && this.playlistAnalysisRangeDragPointerId !== null && ev.pointerId !== this.playlistAnalysisRangeDragPointerId) return;
      const months = this.playlistAnalysisTimelineMonths();
      const maxIndex = months.length - 1;
      const width = this.playlistAnalysisTimelineTrackWidthPx();
      if (maxIndex <= 0 || width <= 0) return;
      const stepPx = width / maxIndex;
      const delta = Math.round((Number((ev && ev.clientX) || 0) - Number(this.playlistAnalysisRangeDragStartX || 0)) / stepPx);
      const span = Math.max(0, Number(this.playlistAnalysisRangeDragStartEndIndex || 0) - Number(this.playlistAnalysisRangeDragStartStartIndex || 0));
      const nextStart = Math.max(0, Math.min(maxIndex - span, Number(this.playlistAnalysisRangeDragStartStartIndex || 0) + delta));
      const nextEnd = nextStart + span;
      if (nextStart !== this.playlistAnalysisTimelineDisplayStartIndex() || nextEnd !== this.playlistAnalysisTimelineDisplayEndIndex()) {
        this.playlistAnalysisSetTimelinePreview(nextStart, nextEnd);
      }
      try {
        if (ev && ev.cancelable) ev.preventDefault();
      } catch {
        // ignore
      }
    },

    playlistAnalysisEndRangeDrag(ev) {
      if (!this.playlistAnalysisRangeDragging) return;
      if (ev && this.playlistAnalysisRangeDragPointerId !== null && ev.pointerId !== this.playlistAnalysisRangeDragPointerId) return;
      this.playlistAnalysisMoveRangeDrag(ev);
      const months = this.playlistAnalysisTimelineMonths();
      const startIndex = this.playlistAnalysisTimelineDisplayStartIndex();
      const endIndex = this.playlistAnalysisTimelineDisplayEndIndex();
      this.playlistAnalysisReleaseRangeDrag();
      if (months[startIndex] && months[endIndex]) {
        this.playlistAnalysisSetTimelineRangeByMonth(months[startIndex].ym, months[endIndex].ym);
      }
    },

    playlistAnalysisTimelineIndexFromClientX(clientX) {
      const months = this.playlistAnalysisTimelineMonths();
      const maxIndex = months.length - 1;
      if (maxIndex <= 0) return 0;
      let left = 0;
      let width = this.playlistAnalysisTimelineTrackWidthPx();
      try {
        const el = this.$refs && this.$refs.playlistAnalysisTimelineTrack;
        if (el && el.getBoundingClientRect) {
          const rect = el.getBoundingClientRect();
          left = Number(rect.left || 0);
          width = Number(rect.width || width || 0);
        }
      } catch {
        // ignore
      }
      if (!Number.isFinite(width) || width <= 0) return 0;
      const ratio = (Number(clientX || 0) - left) / width;
      return Math.max(0, Math.min(maxIndex, Math.round(ratio * maxIndex)));
    },

    playlistAnalysisStartTimelineHandleDrag(edge, ev) {
      const targetEdge = String(edge || "") === "end" ? "end" : "start";
      if (!ev || (ev.pointerType === "mouse" && ev.button !== 0)) return;
      if (!this.playlistAnalysisTimelineMonths().length) return;
      this.playlistAnalysisReleaseProjectionWindowDrag();
      this.playlistAnalysisReleaseRangeDrag();
      this.playlistAnalysisTimelineHandleDragging = targetEdge;
      this.playlistAnalysisTimelineHandlePointerId = ev.pointerId;
      this.playlistAnalysisSetTimelinePreview(this.playlistAnalysisTimelineStartIndex(), this.playlistAnalysisTimelineEndIndex());
      this._playlistAnalysisTimelineHandleDragMove = (nextEv) => this.playlistAnalysisMoveTimelineHandleDrag(nextEv);
      this._playlistAnalysisTimelineHandleDragEnd = (nextEv) => this.playlistAnalysisEndTimelineHandleDrag(nextEv);
      try {
        window.addEventListener("pointermove", this._playlistAnalysisTimelineHandleDragMove, { passive: false });
        window.addEventListener("pointerup", this._playlistAnalysisTimelineHandleDragEnd, { passive: false });
        window.addEventListener("pointercancel", this._playlistAnalysisTimelineHandleDragEnd, { passive: false });
      } catch {
        // ignore
      }
      this.playlistAnalysisMoveTimelineHandleDrag(ev);
      try {
        if (ev.currentTarget && ev.currentTarget.setPointerCapture) ev.currentTarget.setPointerCapture(ev.pointerId);
      } catch {
        // ignore
      }
      try {
        if (ev.cancelable) ev.preventDefault();
        ev.stopPropagation();
      } catch {
        // ignore
      }
    },

    playlistAnalysisMoveTimelineHandleDrag(ev) {
      const edge = String(this.playlistAnalysisTimelineHandleDragging || "");
      if (!edge) return;
      if (ev && this.playlistAnalysisTimelineHandlePointerId !== null && ev.pointerId !== this.playlistAnalysisTimelineHandlePointerId) return;
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return;
      const index = this.playlistAnalysisTimelineIndexFromClientX(ev && ev.clientX);
      if (edge === "end") {
        const startIndex = this.playlistAnalysisTimelineDisplayStartIndex();
        const nextIndex = Math.max(startIndex, index);
        this.playlistAnalysisSetTimelinePreview(startIndex, nextIndex);
      } else {
        const endIndex = this.playlistAnalysisTimelineDisplayEndIndex();
        const nextIndex = Math.min(endIndex, index);
        this.playlistAnalysisSetTimelinePreview(nextIndex, endIndex);
      }
      try {
        if (ev && ev.cancelable) ev.preventDefault();
        if (ev) ev.stopPropagation();
      } catch {
        // ignore
      }
    },

    playlistAnalysisEndTimelineHandleDrag(ev) {
      if (!this.playlistAnalysisTimelineHandleDragging) return;
      if (ev && this.playlistAnalysisTimelineHandlePointerId !== null && ev.pointerId !== this.playlistAnalysisTimelineHandlePointerId) return;
      this.playlistAnalysisMoveTimelineHandleDrag(ev);
      const months = this.playlistAnalysisTimelineMonths();
      const startIndex = this.playlistAnalysisTimelineDisplayStartIndex();
      const endIndex = this.playlistAnalysisTimelineDisplayEndIndex();
      this.playlistAnalysisReleaseTimelineHandleDrag();
      if (months[startIndex] && months[endIndex]) {
        this.playlistAnalysisSetTimelineRangeByMonth(months[startIndex].ym, months[endIndex].ym);
      }
    },

    playlistAnalysisTimelineHandleKeydown(edge, ev) {
      const key = String((ev && ev.key) || "");
      const delta = key === "ArrowLeft" ? -1 : key === "ArrowRight" ? 1 : 0;
      if (!delta) return;
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return;
      if (String(edge || "") === "end") {
        const startIndex = this.playlistAnalysisTimelineStartIndex();
        const endIndex = this.playlistAnalysisTimelineEndIndex();
        const nextIndex = Math.max(startIndex, Math.min(months.length - 1, endIndex + delta));
        this.playlistAnalysisSetTimelineRangeByMonth(months[startIndex].ym, months[nextIndex].ym);
      } else {
        const startIndex = this.playlistAnalysisTimelineStartIndex();
        const endIndex = this.playlistAnalysisTimelineEndIndex();
        const nextIndex = Math.max(0, Math.min(endIndex, startIndex + delta));
        this.playlistAnalysisSetTimelineRangeByMonth(months[nextIndex].ym, months[endIndex].ym);
      }
      try {
        if (ev.cancelable) ev.preventDefault();
        ev.stopPropagation();
      } catch {
        // ignore
      }
    },

    playlistAnalysisSetTimelineRangeByMonth(startMonth, endMonth, { render = true } = {}) {
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return;
      const bounds = this.playlistAnalysisTimelineBounds();
      if (!bounds) return;
      const monthValues = months.map((item) => item.ym);
      let start = String(startMonth || monthValues[0]).slice(0, 7);
      let end = String(endMonth || monthValues[monthValues.length - 1]).slice(0, 7);
      if (start > end) {
        const swapped = start;
        start = end;
        end = swapped;
      }
      if (start < monthValues[0]) start = monthValues[0];
      if (end > monthValues[monthValues.length - 1]) end = monthValues[monthValues.length - 1];
      const startDate = `${start}-01`;
      const endDate = this.playlistAnalysisMonthEndDate(end);
      const nextRangeStart = startDate < bounds.start ? bounds.start : startDate;
      const nextRangeEnd = endDate > bounds.end ? bounds.end : endDate;
      const changed =
        String(this.playlistAnalysisRangeStart || "") !== String(nextRangeStart || "") ||
        String(this.playlistAnalysisRangeEnd || "") !== String(nextRangeEnd || "");
      this.playlistAnalysisRangeStart = nextRangeStart;
      this.playlistAnalysisRangeEnd = nextRangeEnd;
      this.playlistAnalysisClearTimelinePreview();
      if (!changed) return;
      this.playlistAnalysisStopProjectionPlayback();
      this.playlistAnalysisEnsureProjectionWindow({ forceEnd: true });
      if (render) {
        this.playlistAnalysisScheduleSignalsReload();
      }
    },

    playlistAnalysisSetTimelineStartIndex(index) {
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return;
      const endIndex = this.playlistAnalysisTimelineEndIndex();
      const nextIndex = Math.max(0, Math.min(Number(index || 0), endIndex));
      this.playlistAnalysisSetTimelineRangeByMonth(months[nextIndex].ym, months[endIndex].ym);
    },

    playlistAnalysisSetTimelineEndIndex(index) {
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return;
      const startIndex = this.playlistAnalysisTimelineStartIndex();
      const nextIndex = Math.min(months.length - 1, Math.max(Number(index || 0), startIndex));
      this.playlistAnalysisSetTimelineRangeByMonth(months[startIndex].ym, months[nextIndex].ym);
    },

    playlistAnalysisSelectRecentTimelineMonths(count) {
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return;
      const endIndex = months.length - 1;
      const startIndex = Math.max(0, endIndex - Math.max(1, Number(count || 1)) + 1);
      this.playlistAnalysisSetTimelineRangeByMonth(months[startIndex].ym, months[endIndex].ym);
    },

    playlistAnalysisSelectFullTimelineRange() {
      const months = this.playlistAnalysisTimelineMonths();
      if (!months.length) return;
      this.playlistAnalysisSetTimelineRangeByMonth(months[0].ym, months[months.length - 1].ym);
    },

    playlistAnalysisProjectionPoints() {
      const scalePoints = this.playlistAnalysisSignalsForGranularity("day");
      const start = String(this.playlistAnalysisProjectionWindowStart || "");
      const end = String(this.playlistAnalysisProjectionWindowEnd || "");
      const cacheKey = [
        Number(this.playlistAnalysisSignalsVersion || 0),
        String(this.playlistAnalysisRangeStart || ""),
        String(this.playlistAnalysisRangeEnd || ""),
        start,
        end,
      ].join("|");
      if (this._playlistAnalysisProjectionPointsCache && this._playlistAnalysisProjectionPointsCache.key === cacheKey) {
        return this._playlistAnalysisProjectionPointsCache.value;
      }
      const points = [];
      scalePoints.forEach((item) => {
        const date = String(item && item.period_date ? item.period_date : "");
        if (!date) return;
        if (start && date < start) return;
        if (end && date > end) return;
        points.push(item);
      });
      if (!points.length) {
        this._playlistAnalysisProjectionPointsCache = { key: cacheKey, value: [] };
        return [];
      }
      const scaleKey = `${Number(this.playlistAnalysisSignalsVersion || 0)}|${String(this.playlistAnalysisRangeStart || "")}|${String(this.playlistAnalysisRangeEnd || "")}`;
      let scale = this._playlistAnalysisProjectionScaleCache && this._playlistAnalysisProjectionScaleCache.key === scaleKey ? this._playlistAnalysisProjectionScaleCache.value : null;
      if (!scale) {
        const coordinatePoints = scalePoints.length ? scalePoints : points;
        let minX = Infinity;
        let maxX = -Infinity;
        let minY = Infinity;
        let maxY = -Infinity;
        let minZ = Infinity;
        let maxZ = -Infinity;
        let minDispersion = Infinity;
        let maxDispersion = -Infinity;
        coordinatePoints.forEach((item) => {
          const x = Number(item.projection_x || 0);
          const y = Number(item.projection_y || 0);
          const z = Number(item.projection_z || 0);
          const dispersion = Number(item.dispersion_mean || 0);
          minX = Math.min(minX, x);
          maxX = Math.max(maxX, x);
          minY = Math.min(minY, y);
          maxY = Math.max(maxY, y);
          minZ = Math.min(minZ, z);
          maxZ = Math.max(maxZ, z);
          minDispersion = Math.min(minDispersion, dispersion);
          maxDispersion = Math.max(maxDispersion, dispersion);
        });
        scale = {
          minX,
          minY,
          minZ,
          minDispersion,
          spanX: Math.max(1e-6, maxX - minX),
          spanY: Math.max(1e-6, maxY - minY),
          spanZ: Math.max(1e-6, maxZ - minZ),
          spanDispersion: Math.max(1e-6, maxDispersion - minDispersion),
        };
        this._playlistAnalysisProjectionScaleCache = { key: scaleKey, value: scale };
      }
      const rendered = points.map((item, index) => {
        const xRatio = (Number(item.projection_x || 0) - scale.minX) / scale.spanX;
        const yRatio = (Number(item.projection_y || 0) - scale.minY) / scale.spanY;
        const zRatio = (Number(item.projection_z || 0) - scale.minZ) / scale.spanZ;
        const uncertainty = (Number(item.dispersion_mean || 0) - scale.minDispersion) / scale.spanDispersion;
        const x = xRatio * 76 + 12 + (zRatio - 0.5) * 10;
        const y = 88 - yRatio * 72 - (zRatio - 0.5) * 12;
        const ratio = points.length <= 1 ? 0 : index / Math.max(1, points.length - 1);
        return {
          ...item,
          x,
          y,
          zRatio,
          ratio,
          uncertainty,
          diameter: 4,
        };
      });
      this._playlistAnalysisProjectionPointsCache = { key: cacheKey, value: rendered };
      return rendered;
    },

    playlistAnalysisProjectionPointStyle(point) {
      const uncertainty = Number(point && point.uncertainty != null ? point.uncertainty : 0);
      const hue = Math.round(160 - uncertainty * 125);
      const x = Number(point && point.x != null ? point.x : 50);
      const y = Number(point && point.y != null ? point.y : 50);
      const zRatio = Number(point && point.zRatio != null ? point.zRatio : 0.5);
      const diameter = Math.max(4, Math.min(8, Number(point && point.diameter != null ? point.diameter : 4) + zRatio * 3));
      const opacity = Math.max(0.55, Math.min(0.96, 0.58 + uncertainty * 0.34));
      return [
        `left:${x}%`,
        `top:${y}%`,
        `width:${diameter}px`,
        `height:${diameter}px`,
        "transform:translate(-50%,-50%)",
        `background:hsl(${hue} 78% 58% / ${opacity})`,
        "border-color:rgba(15,23,42,0.88)",
        `box-shadow:0 0 0 ${Math.round(1 + uncertainty * 4)}px hsl(${hue} 78% 58% / 0.12)`,
      ].join(";");
    },

    playlistAnalysisProjectionActiveBreakpoint() {
      const start = String(this.playlistAnalysisProjectionWindowStart || "");
      const end = String(this.playlistAnalysisProjectionWindowEnd || "");
      if (!start || !end) return null;
      const selectedId = String(this.playlistAnalysisSelectedCandidateId || "").trim();
      const candidates = this.playlistAnalysisBreakpoints().filter((item) => {
        const date = String((item && item.date) || "");
        return date && date >= start && date <= end;
      });
      if (!candidates.length) return null;
      if (selectedId) {
        const selected = candidates.find((item) => String(item.id || "") === selectedId);
        if (selected) return selected;
      }
      const midpoint = (this.playlistAnalysisDateToUtcMs(start) + this.playlistAnalysisDateToUtcMs(end)) / 2;
      return candidates
        .slice()
        .sort((left, right) => {
          const leftDistance = Math.abs(this.playlistAnalysisDateToUtcMs(left.date) - midpoint);
          const rightDistance = Math.abs(this.playlistAnalysisDateToUtcMs(right.date) - midpoint);
          return leftDistance - rightDistance;
        })[0];
    },

    playlistAnalysisProjectionCentroids() {
      const breakpoint = this.playlistAnalysisProjectionActiveBreakpoint();
      if (!breakpoint) return null;
      const cacheKey = [
        Number(this.playlistAnalysisSignalsVersion || 0),
        String(this.playlistAnalysisProjectionWindowStart || ""),
        String(this.playlistAnalysisProjectionWindowEnd || ""),
        String(breakpoint.id || ""),
        String(breakpoint.date || ""),
      ].join("|");
      if (this._playlistAnalysisProjectionCentroidsCache && this._playlistAnalysisProjectionCentroidsCache.key === cacheKey) {
        return this._playlistAnalysisProjectionCentroidsCache.value;
      }
      const date = String(breakpoint.date || "");
      const points = this.playlistAnalysisProjectionPoints();
      const beforePoints = points.filter((point) => String(point.period_date || "") < date);
      const afterPoints = points.filter((point) => String(point.period_date || "") >= date);
      if (!beforePoints.length || !afterPoints.length) {
        this._playlistAnalysisProjectionCentroidsCache = { key: cacheKey, value: null };
        return null;
      }
      const average = (items) => {
        const total = items.reduce(
          (acc, item) => {
            acc.x += Number(item.x || 0);
            acc.y += Number(item.y || 0);
            return acc;
          },
          { x: 0, y: 0 }
        );
        return { x: total.x / items.length, y: total.y / items.length };
      };
      const value = {
        breakpoint,
        before: average(beforePoints),
        after: average(afterPoints),
      };
      const dx = value.after.x - value.before.x;
      const dy = value.after.y - value.before.y;
      const distance = Math.hypot(dx, dy);
      const midpoint = {
        x: (value.before.x + value.after.x) / 2,
        y: (value.before.y + value.after.y) / 2,
      };
      value.midpoint = midpoint;
      value.arrowDistance = distance;
      value.arrowAngle = Number.isFinite(distance) && distance > 0 ? (Math.atan2(dy, dx) * 180) / Math.PI : 0;
      value.arrowVisible = Number.isFinite(distance) && distance >= PLAYLIST_ANALYSIS_CENTROID_ARROW_MIN_DISTANCE;
      if (value.arrowVisible) {
        const length = Math.max(
          PLAYLIST_ANALYSIS_CENTROID_ARROW_MIN_LENGTH,
          Math.min(PLAYLIST_ANALYSIS_CENTROID_ARROW_MAX_LENGTH, distance)
        );
        const ux = dx / distance;
        const uy = dy / distance;
        value.arrowLength = length;
        value.arrowStart = {
          x: Math.max(3, Math.min(97, midpoint.x - (ux * length) / 2)),
          y: Math.max(3, Math.min(97, midpoint.y - (uy * length) / 2)),
        };
        value.arrowEnd = {
          x: Math.max(3, Math.min(97, midpoint.x + (ux * length) / 2)),
          y: Math.max(3, Math.min(97, midpoint.y + (uy * length) / 2)),
        };
      } else {
        value.arrowLength = 0;
        value.arrowStart = null;
        value.arrowEnd = null;
      }
      this._playlistAnalysisProjectionCentroidsCache = { key: cacheKey, value };
      return value;
    },

    playlistAnalysisProjectionCentroidStyle(point) {
      if (!point) return "left:50%;top:50%;";
      const x = Math.max(0, Math.min(100, Number(point.x || 0)));
      const y = Math.max(0, Math.min(100, Number(point.y || 0)));
      return `left:${x}%;top:${y}%;transform:translate(-50%,-50%);`;
    },

    async playlistAnalysisSelectEventFromDate(periodDate) {
      const date = String(periodDate || "").trim();
      if (!date) return;
      if (!this.playlistAnalysisCandidatesLoaded) {
        await this.playlistLoadAnalysisCandidates();
      }
      const item = (Array.isArray(this.playlistAnalysisCandidates) ? this.playlistAnalysisCandidates : []).find(
        (candidate) => String(candidate.event_date || candidate.candidate_date || "") === date
      );
      if (!item) return;
      this.playlistAnalysisSetTab("events");
      this.playlistSelectAnalysisCandidate(item.id);
    },

    playlistAnalysisEventTypeLabel(value) {
      const type = String(value || "").trim().toLowerCase();
      if (type === "event_regime_shift") return "语义转折";
      if (type === "regime") return "语义阶段";
      if (type === "transition") return "过渡";
      return "主题爆发";
    },

    playlistAnalysisEventTypeClass(value) {
      const type = String(value || "").trim().toLowerCase();
      if (type === "event_regime_shift") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
      if (type === "regime") return "border-purple-500/30 bg-purple-500/10 text-purple-200";
      if (type === "transition") return "border-sky-500/30 bg-sky-500/10 text-sky-200";
      return "border-amber-500/30 bg-amber-500/10 text-amber-200";
    },

    playlistAnalysisDetectionKindLabel(item) {
      const method = String((item && item.detection_method) || "").trim();
      if (method === "event_embedding_regime_v1") return "语义漂移";
      return "候选";
    },

    playlistAnalysisDetectionKindClass(item) {
      const method = String((item && item.detection_method) || "").trim();
      if (method === "event_embedding_regime_v1") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
      return "border-slate-500/30 bg-slate-500/10 text-slate-200";
    },

    playlistAnalysisDisplayCandidates() {
      return (Array.isArray(this.playlistAnalysisCandidates) ? this.playlistAnalysisCandidates : [])
        .slice()
        .sort((left, right) => {
          const leftDate = String((left && (left.event_date || left.candidate_date)) || "");
          const rightDate = String((right && (right.event_date || right.candidate_date)) || "");
          const dateOrder = rightDate.localeCompare(leftDate);
          if (dateOrder !== 0) return dateOrder;
          return String((left && left.id) || "").localeCompare(String((right && right.id) || ""));
        });
    },

    playlistAnalysisExportJson() {
      try {
        return JSON.stringify(this.playlistAnalysisExportPayload || { events: [] }, null, 2);
      } catch {
        return '{"events":[]}';
      }
    },

    playlistAnalysisSchedulePoll() {
      this.playlistAnalysisStopPolling();
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const subview = String(this.playlistSubview || "");
      if (!pid || this.activeView !== "playlist" || !["analysis", "settings"].includes(subview)) return;
      this.playlistAnalysisPollTimer = setTimeout(async () => {
        if (String(this.playlistSubview || "") === "settings") {
          const summary = await this.playlistLoadAnalysisSummary({ silent: true }).catch(() => null);
          await this.playlistLoadEventsAllSummary({ silent: true }).catch(() => null);
          if (summary && (summary.running || this.playlistAnalysisActiveBackfillJob())) this.playlistAnalysisSchedulePoll();
          else this.playlistAnalysisStopPolling();
          return;
        }
        this.playlistLoadAnalysisView({ silent: true });
      }, 3000);
    },

    async playlistLoadAnalysisSummary({ silent = false } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return null;
      this.playlistAnalysisSummaryLoading = true;
      this.playlistAnalysisSummaryError = "";
      try {
        const summary = await this.api(`/playlists/${encodeURIComponent(pid)}/regime/summary`);
        this.playlistAnalysisSummary = summary || null;
        if (summary && summary.last_ready_run_id) this.playlistAnalysisInitializeRangeFromSummary(summary);
        this.playlistAnalysisBackfillJob = summary && summary.backfill_job ? summary.backfill_job : null;
        return summary || null;
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.playlistAnalysisSummaryError = msg;
        if (!silent) this.playlistAnalysisError = msg;
        throw e;
      } finally {
        this.playlistAnalysisSummaryLoading = false;
      }
    },

    async playlistLoadAnalysisPeriods() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return [];
      this.playlistAnalysisPeriodsLoading = true;
      try {
        const payload = [];
        this.playlistAnalysisPeriods = Array.isArray(payload) ? payload : [];
        this.$nextTick(() => this.playlistAnalysisRequestChartRender());
        return this.playlistAnalysisPeriods;
      } finally {
        this.playlistAnalysisPeriodsLoading = false;
      }
    },

    playlistAnalysisTimelineDensityCacheKey(pid, start, end) {
      const runId = this.playlistAnalysisSummary && this.playlistAnalysisSummary.last_ready_run_id ? String(this.playlistAnalysisSummary.last_ready_run_id) : "";
      return [String(pid || ""), runId, String(start || ""), String(end || "")].join("|");
    },

    playlistAnalysisApplyTimelineDensityPayload(payload, requestedStart, requestedEnd) {
      this.playlistAnalysisTimelineDensity = (Array.isArray(payload) ? payload : [])
        .filter((item) => item && String(item.granularity || "") === "month" && item.period_date)
        .sort((left, right) => String(left.period_date).localeCompare(String(right.period_date)));
      this.playlistAnalysisTimelineDensityLoadedRangeStart = String(requestedStart || "");
      this.playlistAnalysisTimelineDensityLoadedRangeEnd = String(requestedEnd || "");
    },

    async playlistLoadAnalysisTimelineDensity() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const bounds = this.playlistAnalysisTimelineBounds();
      if (!pid || !bounds || !this.playlistAnalysisHasReadySnapshot()) {
        this.playlistAnalysisTimelineDensity = [];
        return [];
      }
      const requestedStart = bounds.start;
      const requestedEnd = bounds.end;
      const cacheKey = this.playlistAnalysisTimelineDensityCacheKey(pid, requestedStart, requestedEnd);
      if (this._playlistAnalysisTimelineDensityResponseCache && this._playlistAnalysisTimelineDensityResponseCache.has(cacheKey)) {
        this._playlistAnalysisTimelineDensityRequestToken = Number(this._playlistAnalysisTimelineDensityRequestToken || 0) + 1;
        this._abortCtrl("_playlistAnalysisTimelineDensityAbortCtrl");
        this.playlistAnalysisTimelineDensityLoading = false;
        const cached = this._playlistAnalysisTimelineDensityResponseCache.get(cacheKey);
        this.playlistAnalysisApplyTimelineDensityPayload(cached, requestedStart, requestedEnd);
        return this.playlistAnalysisTimelineDensity;
      }
      const token = Number(this._playlistAnalysisTimelineDensityRequestToken || 0) + 1;
      this._playlistAnalysisTimelineDensityRequestToken = token;
      this._abortCtrl("_playlistAnalysisTimelineDensityAbortCtrl");
      const ctrl = new AbortController();
      this._playlistAnalysisTimelineDensityAbortCtrl = ctrl;
      this.playlistAnalysisTimelineDensityLoading = true;
      try {
        const params = new URLSearchParams({
          granularity: "month",
          since: requestedStart,
          until: requestedEnd,
        });
        const payload = await this.api(`/playlists/${encodeURIComponent(pid)}/regime/signals?${params.toString()}`, { signal: ctrl.signal });
        if (Number(this._playlistAnalysisTimelineDensityRequestToken || 0) !== token) return [];
        if (!this._playlistAnalysisTimelineDensityResponseCache) this._playlistAnalysisTimelineDensityResponseCache = new Map();
        this._playlistAnalysisTimelineDensityResponseCache.set(cacheKey, Array.isArray(payload) ? payload : []);
        this.playlistAnalysisApplyTimelineDensityPayload(payload, requestedStart, requestedEnd);
        return this.playlistAnalysisTimelineDensity;
      } catch (e) {
        if (this._isAbortError && this._isAbortError(e)) return [];
        throw e;
      } finally {
        if (Number(this._playlistAnalysisTimelineDensityRequestToken || 0) === token) {
          this._playlistAnalysisTimelineDensityAbortCtrl = null;
          this.playlistAnalysisTimelineDensityLoading = false;
        }
      }
    },

    async playlistLoadAnalysisSignals() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return [];
      if (!this.playlistAnalysisRangeStart || !this.playlistAnalysisRangeEnd) {
        this.playlistAnalysisInitializeDefaultRange();
      }
      const requestedStart = String(this.playlistAnalysisRangeStart || "");
      const requestedEnd = String(this.playlistAnalysisRangeEnd || "");
      const cacheKey = this.playlistAnalysisSignalsCacheKey(pid, requestedStart, requestedEnd);
      if (this._playlistAnalysisSignalsResponseCache && this._playlistAnalysisSignalsResponseCache.has(cacheKey)) {
        this._playlistAnalysisSignalsRequestToken = Number(this._playlistAnalysisSignalsRequestToken || 0) + 1;
        this._abortCtrl("_playlistAnalysisSignalsAbortCtrl");
        this.playlistAnalysisSignalsLoading = false;
        const cached = this._playlistAnalysisSignalsResponseCache.get(cacheKey);
        this.playlistAnalysisApplySignalsPayload(cached, requestedStart, requestedEnd);
        return this.playlistAnalysisSignals;
      }
      const token = Number(this._playlistAnalysisSignalsRequestToken || 0) + 1;
      this._playlistAnalysisSignalsRequestToken = token;
      this._abortCtrl("_playlistAnalysisSignalsAbortCtrl");
      const ctrl = new AbortController();
      this._playlistAnalysisSignalsAbortCtrl = ctrl;
      this.playlistAnalysisSignalsLoading = true;
      try {
        const params = new URLSearchParams();
        if (requestedStart) params.set("since", requestedStart);
        if (requestedEnd) params.set("until", requestedEnd);
        const suffix = params.toString() ? `?${params.toString()}` : "";
        const payload = await this.api(`/playlists/${encodeURIComponent(pid)}/regime/signals${suffix}`, { signal: ctrl.signal });
        if (Number(this._playlistAnalysisSignalsRequestToken || 0) !== token) return [];
        if (!this._playlistAnalysisSignalsResponseCache) this._playlistAnalysisSignalsResponseCache = new Map();
        this._playlistAnalysisSignalsResponseCache.set(cacheKey, Array.isArray(payload) ? payload : []);
        this.playlistAnalysisApplySignalsPayload(payload, requestedStart, requestedEnd);
        return this.playlistAnalysisSignals;
      } catch (e) {
        if (this._isAbortError && this._isAbortError(e)) return [];
        throw e;
      } finally {
        if (Number(this._playlistAnalysisSignalsRequestToken || 0) === token) {
          this._playlistAnalysisSignalsAbortCtrl = null;
          this.playlistAnalysisSignalsLoading = false;
        }
      }
    },

    async playlistLoadAnalysisCandidates({ preserveSelection = true, selectDetail = true } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid || !this.playlistAnalysisHasReadySnapshot()) return [];
      if (this.playlistAnalysisCandidatesLoading && this._playlistAnalysisCandidatesPromise) {
        return this._playlistAnalysisCandidatesPromise;
      }
      this.playlistAnalysisCandidatesLoading = true;
      const request = (async () => {
        const payload = await this.api(`/playlists/${encodeURIComponent(pid)}/regime/candidates`);
        this.playlistAnalysisCandidates = Array.isArray(payload) ? payload : [];
        this.playlistAnalysisCandidatesLoaded = true;
        this.playlistAnalysisCandidatesVersion = Number(this.playlistAnalysisCandidatesVersion || 0) + 1;
        const selectedId = preserveSelection ? String(this.playlistAnalysisSelectedCandidateId || "").trim() : "";
        const shouldSelectDetail = !!selectDetail || String(this.playlistAnalysisTab || "trend") === "events";
        if (!shouldSelectDetail) {
          if (selectedId && !this.playlistAnalysisCandidates.some((item) => String(item.id) === selectedId)) {
            this.playlistAnalysisSelectedCandidateId = "";
          }
          this._playlistAnalysisBreakpointsCache = null;
          return this.playlistAnalysisCandidates;
        }
        const target =
          this.playlistAnalysisCandidates.find((item) => String(item.id) === selectedId) ||
          this.playlistAnalysisCandidates[0] ||
          null;
        this.playlistAnalysisSelectedCandidateId = target ? String(target.id) : "";
        if (target) {
          await this.playlistSelectAnalysisCandidate(target.id);
        } else {
          this.playlistAnalysisCandidateDetail = null;
        }
        return this.playlistAnalysisCandidates;
      })();
      this._playlistAnalysisCandidatesPromise = request;
      try {
        return await request;
      } finally {
        if (this._playlistAnalysisCandidatesPromise === request) this._playlistAnalysisCandidatesPromise = null;
        this.playlistAnalysisCandidatesLoading = false;
      }
    },

    async playlistSelectAnalysisCandidate(candidateId) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const id = String(candidateId || "").trim();
      if (!pid || !id) {
        this.playlistAnalysisSelectedCandidateId = "";
        this.playlistAnalysisCandidateDetail = null;
        return;
      }
      this.playlistAnalysisSelectedCandidateId = id;
      this.playlistAnalysisCandidateDetail = await this.api(
        `/playlists/${encodeURIComponent(pid)}/regime/candidates/${encodeURIComponent(id)}`
      );
    },

    async playlistLoadAnalysisView({ silent = false } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      if (!silent) this.playlistAnalysisLoading = true;
      this.playlistAnalysisError = "";
      try {
        const summary = await this.playlistLoadAnalysisSummary();
        if (!summary) return;
        if (summary.last_ready_run_id) {
          await this.playlistLoadAnalysisTimelineDensity();
          this.playlistAnalysisInitializeDefaultRange();
          if (String(this.playlistAnalysisTab || "trend") === "events") {
            await Promise.all([this.playlistLoadAnalysisSignals(), this.playlistLoadAnalysisCandidates()]);
          } else {
            await this.playlistLoadAnalysisSignals();
          }
        } else {
          this.playlistAnalysisStopProjectionPlayback();
          this.playlistAnalysisPeriods = [];
          this.playlistAnalysisSignals = [];
          this.playlistAnalysisInvalidateSignalCaches();
          this.playlistAnalysisCandidates = [];
          this.playlistAnalysisCandidatesLoaded = false;
          this.playlistAnalysisCandidatesVersion = Number(this.playlistAnalysisCandidatesVersion || 0) + 1;
          this._playlistAnalysisCandidateHydrationRunId = "";
          this.playlistAnalysisHoveredBreakpointItemId = "";
          this.playlistAnalysisCandidateDetail = null;
          this.playlistAnalysisSelectedCandidateId = "";
          this.playlistAnalysisRangeStart = "";
          this.playlistAnalysisRangeEnd = "";
          this.playlistAnalysisFullRangeStart = "";
          this.playlistAnalysisFullRangeEnd = "";
          this.playlistAnalysisTimelineScope = "normal";
          this.playlistAnalysisTimelineDensity = [];
          this.playlistAnalysisTimelineDensityLoadedRangeStart = "";
          this.playlistAnalysisTimelineDensityLoadedRangeEnd = "";
          this.playlistAnalysisSignalsLoadedRangeStart = "";
          this.playlistAnalysisSignalsLoadedRangeEnd = "";
          this.playlistAnalysisProjectionWindowAnchorDate = "";
          this.playlistAnalysisDestroyChart();
        }
        if (summary.running || this.playlistAnalysisActiveBackfillJob()) this.playlistAnalysisSchedulePoll();
        else this.playlistAnalysisStopPolling();
      } catch (e) {
        this.playlistAnalysisError = e && e.message ? e.message : String(e);
      } finally {
        if (!silent) this.playlistAnalysisLoading = false;
      }
    },

    async playlistAnalysisRebuild({ silent = false } = {}) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid || this.playlistAnalysisRebuilding) return;
      try {
        this.playlistAnalysisRebuilding = true;
        const result = await this.api(`/playlists/${encodeURIComponent(pid)}/regime/rebuild`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: "{}",
        });
        if (!silent) {
          this.globalStatus = result && result.created ? "已触发语义快照构建" : "语义快照构建已在执行或排队，已复用现有任务";
        }
        await this.playlistLoadAnalysisSummary();
        this.playlistAnalysisSchedulePoll();
      } catch (e) {
        this.playlistAnalysisError = e && e.message ? e.message : String(e);
        this.globalStatus = `error: ${this.playlistAnalysisError}`;
      } finally {
        this.playlistAnalysisRebuilding = false;
      }
    },

    async playlistAnalysisBackfillEmbeddings() {
      await this.playlistSubmitEventExtraction({ force: false });
    },

    async playlistAnalysisCancelBackfill() {
      const job = this.playlistAnalysisActiveBackfillJob();
      const jobId = job && job.job_id ? String(job.job_id) : "";
      if (!jobId || this.playlistAnalysisBackfillCanceling) return;
      try {
        this.playlistAnalysisBackfillCanceling = true;
        const result = await this.api(`/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
        const status = result && result.status ? String(result.status) : "";
        this.globalStatus = status === "canceled" ? "已停止事件抽取任务" : "已请求停止事件抽取任务";
        const summary = await this.playlistLoadAnalysisSummary({ silent: true });
        if (summary && (summary.running || this.playlistAnalysisActiveBackfillJob())) this.playlistAnalysisSchedulePoll();
        else this.playlistAnalysisStopPolling();
      } catch (e) {
        this.playlistAnalysisError = e && e.message ? e.message : String(e);
        this.globalStatus = `error: ${this.playlistAnalysisError}`;
      } finally {
        this.playlistAnalysisBackfillCanceling = false;
      }
    },

    async playlistAnalysisPatchCandidate(patch) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const candidateId = String(this.playlistAnalysisSelectedCandidateId || "").trim();
      if (!pid || !candidateId) return;
      await this.api(`/playlists/${encodeURIComponent(pid)}/regime/candidates/${encodeURIComponent(candidateId)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(patch || {}),
      });
      await this.playlistLoadAnalysisCandidates();
    },

    async playlistAnalysisConfirmCandidate() {
      await this.playlistAnalysisPatchCandidate({ status: "accepted" });
      this.globalStatus = "已确认候选事件";
    },

    async playlistAnalysisRejectCandidate() {
      await this.playlistAnalysisPatchCandidate({ status: "rejected" });
      this.globalStatus = "已拒绝候选事件";
    },

    async playlistAnalysisOpenExport() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      const payload = await this.api(`/playlists/${encodeURIComponent(pid)}/regime/export/events`);
      this.playlistAnalysisExportPayload = payload && typeof payload === "object" ? payload : { events: [] };
      this.playlistAnalysisExportOpen = true;
    },

    playlistStartEditName() {
      if (!this.playlistDetail) return;
      this.playlistNameDraft = String(this.playlistDetail.name || "");
      this.playlistNameEditing = true;
      this.$nextTick(() => {
        try {
          const el = this.$refs && this.$refs.playlistNameInput;
          if (!el) return;
          el.focus();
          if (typeof el.select === "function") el.select();
        } catch {
          // ignore
        }
      });
    },

    playlistCancelEditName() {
      if (this.playlistNameSaving) return;
      this.playlistNameEditing = false;
      this.playlistNameDraft = "";
    },

    async playlistSaveName() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid || !this.playlistDetail) return;
      if (this.playlistNameSaving) return;

      const current = String(this.playlistDetail.name || "").trim();
      const next = String(this.playlistNameDraft || "").trim();
      if (!next) {
        this.globalStatus = "error: 标题不能为空";
        this.$nextTick(() => {
          try {
            const el = this.$refs && this.$refs.playlistNameInput;
            if (el && typeof el.focus === "function") el.focus();
          } catch {}
        });
        return;
      }
      if (current === next) {
        this.playlistCancelEditName();
        return;
      }

      try {
        this.playlistNameSaving = true;
        const updated = await this.api(`/playlists/${encodeURIComponent(pid)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: next }),
        });
        if (this.playlistDetail) this.playlistDetail.name = updated.name || next;
        this.pageTitle = (updated && updated.name) || next;
        await this.loadPlaylists();
        this.globalStatus = "已更新标题";
        this.playlistNameEditing = false;
        this.playlistNameDraft = "";
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      } finally {
        this.playlistNameSaving = false;
      }
    },

    playlistStartEditDescription() {
      if (!this.playlistDetail) return;
      this.playlistDescDraft = String(this.playlistDetail.description || "");
      this.playlistDescEditing = true;
      this.$nextTick(() => {
        try {
          const el = this.$refs && this.$refs.playlistDescInput;
          if (!el) return;
          el.focus();
          if (typeof el.select === "function") el.select();
        } catch {
          // ignore
        }
      });
    },

    playlistCancelEditDescription() {
      if (this.playlistDescSaving) return;
      this.playlistDescEditing = false;
      this.playlistDescDraft = "";
    },

    async playlistSaveDescription() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid || !this.playlistDetail) return;
      if (this.playlistDescSaving) return;

      const current = String(this.playlistDetail.description || "").trim();
      const next = String(this.playlistDescDraft || "").trim();
      if (current === next) {
        this.playlistCancelEditDescription();
        return;
      }

      try {
        this.playlistDescSaving = true;
        const updated = await this.api(`/playlists/${encodeURIComponent(pid)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ description: next || null }),
        });
        if (this.playlistDetail) this.playlistDetail.description = updated.description || null;
        await this.loadPlaylists();
        this.globalStatus = "已更新描述";
        this.playlistDescEditing = false;
        this.playlistDescDraft = "";
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      } finally {
        this.playlistDescSaving = false;
      }
    },

    playlistEditResetFromDetail() {
      const d = this.playlistDetail;
      const media = d && Array.isArray(d.media) ? d.media : [];
      this.playlistEditMediaIds = media.map((m) => String(m.id)).filter(Boolean);
      this.playlistEditMediaTagQuery = "";
      this.playlistEditMediaTagOpen = false;
      this.playlistEditMediaOptionsError = "";

      const g = d && d.brief_granularity ? String(d.brief_granularity).trim().toLowerCase() : "day";
      this.playlistSettingsGranularityDraft = ["day", "week", "month"].includes(g) ? g : "day";
      this.playlistSettingsGranularityError = "";

      const prompt = d && d.brief_prompt ? String(d.brief_prompt).trim() : "";
      this.playlistSettingsPromptDraft = prompt || this.briefDefaultDailyPrompt();
      this.playlistSettingsPromptError = "";
    },

    async playlistOpenEditMedia() {
      if (!this.playlistDetail) return;
      this.playlistEditResetFromDetail();
      this.playlistEditMediaOpen = true;
      if (Array.isArray(this.mediaIndex) && this.mediaIndex.length > 0) return;
      if (typeof this.loadMediaIndex !== "function") return;

      try {
        this.playlistEditMediaOptionsLoading = true;
        this.playlistEditMediaOptionsError = "";
        await this.loadMediaIndex({ lightweight: true });
      } catch (e) {
        this.playlistEditMediaOptionsError = e && e.message ? e.message : String(e);
        this.globalStatus = `error: ${this.playlistEditMediaOptionsError}`;
      } finally {
        this.playlistEditMediaOptionsLoading = false;
      }
    },

    playlistEditMediaOptionIndex() {
      const items = [];
      const seen = new Set();
      const push = (media) => {
        const id = media && media.id ? String(media.id) : "";
        if (!id || seen.has(id)) return;
        seen.add(id);
        items.push(media);
      };
      const detailMedia = this.playlistDetail && Array.isArray(this.playlistDetail.media) ? this.playlistDetail.media : [];
      detailMedia.forEach(push);
      (Array.isArray(this.mediaIndex) ? this.mediaIndex : []).forEach(push);
      return items;
    },

    playlistEditSelectedMedia() {
      return resolveMediaItemsByIds(this.playlistEditMediaOptionIndex(), this.playlistEditMediaIds);
    },

    playlistEditFilteredMediaOptions() {
      return filterUnselectedMediaOptions({
        index: this.playlistEditMediaOptionIndex(),
        selectedIds: this.playlistEditMediaIds,
        query: this.playlistEditMediaTagQuery,
        displayName: (media) => this.mediaDisplayName(media),
      });
    },

    playlistEditAddMediaTag(mediaId) {
      this.playlistEditMediaIds = addUniqueMediaId(this.playlistEditMediaIds, mediaId);
      this.playlistEditMediaTagQuery = "";
      this.playlistEditMediaTagOpen = false;
    },

    playlistEditAddFirstFilteredMediaTag() {
      const items = this.playlistEditFilteredMediaOptions();
      if (!items.length) return;
      this.playlistEditAddMediaTag(items[0].id);
    },

    playlistEditRemoveMediaTag(mediaId) {
      this.playlistEditMediaIds = removeMediaId(this.playlistEditMediaIds, mediaId);
    },

    async playlistEditSaveMedia() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      try {
        await this.api(`/playlists/${encodeURIComponent(pid)}/media`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ media_ids: Array.isArray(this.playlistEditMediaIds) ? this.playlistEditMediaIds : [] }),
        });
        this.globalStatus = "已更新播放列表媒体（已触发全量简报生成）";
        this.playlistEditMediaOpen = false;
        this.playlistEditMediaTagOpen = false;
        await this.loadPlaylistPage();
        await this.loadPlaylists();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },
  };
}
