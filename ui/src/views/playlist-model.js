import {
  PLAYLIST_ASSETS_CACHE_TTL_MS,
  PLAYLIST_BRIEF_CACHE_TTL_MS,
  PLAYLIST_TRANSCRIPT_CACHE_TTL_MS,
} from "../app/constants.js";
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

export function createPlaylistViewMethods() {
  return {
    async loadPlaylistPage() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) {
        this.playlistCalendarResetDragState();
        this.playlistDetail = null;
        this.playlistDayVideos = [];
        this.playlistBriefHtml = "";
        this.playlistBriefMarkdown = "";
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
        this.pageTitle = "播放列表页";
        return;
      }

      try {
        this.playlistCalendarResetDragState();
        this._syncUrl({ push: false });
        this.playlistDayVideosError = "";
        this.playlistBriefError = "";

        const detail = await this.api(`/playlists/${encodeURIComponent(pid)}/detail`);
        this.playlistDetail = detail || null;
        this.pageTitle = (detail && detail.name) || "播放列表页";
        this._abortCtrl("_playlistCountsAbortCtrl");
        this.playlistPeriodCounts = new Map();
        this.playlistPeriodCountsKey = "";
        this.playlistPeriodCountsLoading = false;
        this.playlistPeriodCountsError = "";

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
        await this.playlistLoadDay(this.playlistSelectedDate);
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

    playlistCalendarTrackStyle() {
      const offset = Number(this.playlistCalendarDragOffsetX || 0);
      const transition = this.playlistCalendarDragging ? "none" : "transform 180ms ease";
      return `transform:translate3d(${offset}px,0,0);transition:${transition};`;
    },

    playlistCalendarResetDragState() {
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
      this.playlistCalendarDragOffsetX = 0;
      this.playlistCalendarDragDidMove = false;
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
      const width =
        this.$refs && this.$refs.playlistCalendarStrip && this.$refs.playlistCalendarStrip.clientWidth
          ? Number(this.$refs.playlistCalendarStrip.clientWidth)
          : 0;
      const cardWidth = width > 0 ? width / count : 72;
      return Math.max(28, Math.min(88, Math.round(cardWidth * 0.35)));
    },

    playlistCalendarResolveDragOffset(rawDx) {
      const dx = Number(rawDx || 0);
      if (!Number.isFinite(dx) || dx === 0) return 0;
      const width =
        this.$refs && this.$refs.playlistCalendarStrip && this.$refs.playlistCalendarStrip.clientWidth
          ? Number(this.$refs.playlistCalendarStrip.clientWidth)
          : 0;
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

      if (didMove) {
        this.playlistCalendarDragSuppressClickUntil = Date.now() + 300;
        try {
          if (ev.cancelable) ev.preventDefault();
        } catch {
          // ignore
        }
      }

      this.playlistCalendarResetDragState();

      if (!didMove || Math.abs(offset) < threshold) return;
      const step = this.playlistCalendarDragStep();
      this.playlistJump(offset > 0 ? -step : step);
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

    playlistToggleSubview() {
      if (!this.playlistDetail) return;
      this.playlistCalendarResetDragState();
      this.playlistSubview = this.playlistSubview === "settings" ? "main" : "settings";
      if (this.playlistSubview === "main") {
        try {
          if (this.$nextTick) this.$nextTick(() => this.playlistCalendarUpdateCount());
        } catch {
          // ignore
        }
      }
      this._syncUrl({ push: false });
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

    async playlistPrefetchCalendarCounts() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      const g = this.playlistGranularity();
      const r = this.playlistCalendarVisibleEnabledRange();
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

    playlistCalendarItems() {
      const g = this.playlistGranularity();
      const rawStart = String(this.playlistTimelineStart || "").trim();
      const rawEnd = String(this.playlistTimelineEnd || "").trim();
      const start = this._periodStartIso(rawStart, g);
      const end = this._periodStartIso(rawEnd, g);
      const selected = this._periodStartIso(String(this.playlistSelectedDate || "").trim(), g);
      const n = Math.max(5, Number(this.playlistCalendarCount || 14));
      if (!start || !end) return [];

      const today = this._periodStartIso(this._todayIsoLocal(), g);
      let anchor = this._periodStartIso(String(this.playlistCalendarAnchor || "").trim(), g);
      if (!anchor) anchor = selected ? this._periodAddIso(selected, g, -(n - 1)) : start;

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

    playlistTimelineApply() {
      const g = this.playlistGranularity();
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      if (!start || !end) return;
      const v = Number(this.playlistTimelineValue || 0);
      const next = this._periodAddIso(start, g, v);
      this.playlistSetDate(this._periodClampIso(next, start, end));
    },

    playlistSetDate(iso) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      const g = this.playlistGranularity();
      const next = this._periodStartIso(String(iso || "").trim(), g);
      if (!next || next === this.playlistSelectedDate) return;
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      const clamped = start && end ? this._periodClampIso(next, start, end) : next;
      this.playlistSelectedDate = clamped;
      this.playlistCalendarEnsureVisible();
      this.playlistPrefetchCalendarCounts();
      if (this.playlistTimelineStart) {
        const max = Number(this.playlistTimelineMax || 0);
        this.playlistTimelineValue = Math.max(0, Math.min(max, this._periodDiff(this.playlistTimelineStart, clamped, g)));
      }
      this._syncUrl({ push: false });
      this.playlistLoadDay(clamped);
    },

    async playlistLoadDay(iso) {
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
      this._abortCtrl("_playlistSelectAbortCtrl");

      const prevCurrentId =
        this.playlistCurrentVideo && this.playlistCurrentVideo.id ? String(this.playlistCurrentVideo.id) : "";
      this.playlistPlayerError = "";
      this.playlistPlayerVideoUrl = "";
      this.playlistPlayerAudioUrl = "";
      this.playlistTranscriptText = "";
      this.playlistTranscriptLoading = false;
      this.playlistTranscriptError = "";
      this.playlistTranscriptLanguage = "";
      this.playlistTranscriptSource = "";
      this.playlistTranscriptUpdatedAt = "";

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
      } finally {
        if (Number(this.playlistLoadToken || 0) === loadToken) this.playlistDayVideosLoading = false;
      }

      if (Number(this.playlistLoadToken || 0) !== loadToken) return;
      const selectPromise = this.playlistCurrentVideo
        ? this.playlistSelectVideo(this.playlistCurrentVideo, { autoPlay: false, loadToken })
        : Promise.resolve();
      const briefPromise = this.playlistLoadBrief(day, { loadToken });
      try {
        await Promise.allSettled([selectPromise, briefPromise]);
      } catch {
        // ignore
      }
    },

    async playlistSelectVideo(v, { autoPlay = false, loadToken = null } = {}) {
      if (!v) return;
      const vid = String(v.id || "").trim();
      if (!vid) return;
      const token = Number(loadToken || this.playlistLoadToken || 0);
      this.playlistCurrentVideo = v;
      this.playlistPlayerError = "";
      const selectingId = vid;

      this._abortCtrl("_playlistSelectAbortCtrl");
      const ctrl = new AbortController();
      this._playlistSelectAbortCtrl = ctrl;

      this.playlistMediaDurationSec = 0;
      this.playlistMediaCurrentTimeSec = 0;
      this.playlistMediaPlaying = false;
      this.playlistTranscriptError = "";

      const isStale = () => {
        if (Number(this.playlistLoadToken || 0) !== token) return true;
        if (!this.playlistCurrentVideo || String(this.playlistCurrentVideo.id || "") !== String(selectingId)) return true;
        return false;
      };

      const applyAssets = (assets) => {
        if (isStale()) return;
        const list = Array.isArray(assets) ? assets : [];
        const videos = list.filter((a) => a && a.type === "video");
        const audios = list.filter((a) => a && a.type === "audio");
        const mp4 = videos.find((a) => String(a.format || "").toLowerCase() === "mp4") || videos[0] || null;
        const m4a = audios.find((a) => String(a.format || "").toLowerCase() === "m4a") || audios[0] || null;
        this.playlistPlayerVideoUrl = (mp4 && this.assetContentUrl(mp4)) || "";
        this.playlistPlayerAudioUrl = (m4a && this.assetContentUrl(m4a)) || "";
        this.$nextTick(() => {
          if (isStale()) return;
          try {
            const el = this.playlistAudioOnly ? this.$refs && this.$refs.playlistAudioEl : this.$refs && this.$refs.playlistVideoEl;
            if (autoPlay && el && typeof el.play === "function") el.play();
          } catch {
            // ignore
          }
          this.playlistSyncMediaState();
        });
      };

      const applyTranscript = (transcript) => {
        if (isStale()) return;
        if (transcript && typeof transcript === "object" && transcript.ok) {
          this.playlistTranscriptText = transcript.text || "";
          this.playlistTranscriptLanguage = transcript.language || "";
          this.playlistTranscriptSource = transcript.source || "";
          this.playlistTranscriptUpdatedAt = transcript.updated_at || transcript.created_at || "";
        } else {
          this.playlistTranscriptText = "";
          this.playlistTranscriptLanguage = "";
          this.playlistTranscriptSource = "";
          this.playlistTranscriptUpdatedAt = "";
        }
      };

      const cachedAssets = this._cacheGet(this.playlistVideoAssetsCache, selectingId);
      if (cachedAssets) applyAssets(cachedAssets);
      else {
        this.playlistPlayerVideoUrl = "";
        this.playlistPlayerAudioUrl = "";
      }

      const cachedTranscript = this._cacheGet(this.playlistVideoTranscriptCache, selectingId);
      if (cachedTranscript) {
        applyTranscript(cachedTranscript);
        this.playlistTranscriptLoading = false;
      } else {
        this.playlistTranscriptText = "";
        this.playlistTranscriptLoading = true;
        this.playlistTranscriptLanguage = "";
        this.playlistTranscriptSource = "";
        this.playlistTranscriptUpdatedAt = "";
      }

      try {
        const assetsPromise = cachedAssets
          ? Promise.resolve(cachedAssets)
          : this.api(`/videos/${encodeURIComponent(vid)}/assets?presign=1&download=0&localize_title=0`, { signal: ctrl.signal });
        const transcriptPromise = cachedTranscript
          ? Promise.resolve(cachedTranscript)
          : this.api(`/videos/${encodeURIComponent(vid)}/transcript`, { signal: ctrl.signal });

        const [assetsRes, transcriptRes] = await Promise.allSettled([assetsPromise, transcriptPromise]);

        if (assetsRes.status === "fulfilled") {
          applyAssets(assetsRes.value);
          if (!cachedAssets) this._cacheSet(this.playlistVideoAssetsCache, selectingId, assetsRes.value, PLAYLIST_ASSETS_CACHE_TTL_MS);
        } else if (!cachedAssets) {
          const err = assetsRes.reason;
          if (!this._isAbortError(err) && !isStale()) this.playlistPlayerError = err && err.message ? err.message : String(err);
        }

        if (transcriptRes.status === "fulfilled") {
          applyTranscript(transcriptRes.value);
          if (!cachedTranscript) this._cacheSet(this.playlistVideoTranscriptCache, selectingId, transcriptRes.value, PLAYLIST_TRANSCRIPT_CACHE_TTL_MS);
        } else if (!cachedTranscript) {
          const err2 = transcriptRes.reason;
          if (!this._isAbortError(err2) && !isStale()) this.playlistTranscriptError = err2 && err2.message ? err2.message : String(err2);
        }
      } catch (e) {
        if (!this._isAbortError(e) && !isStale()) this.playlistPlayerError = e && e.message ? e.message : String(e);
      }
      if (!isStale()) this.playlistTranscriptLoading = false;
    },

    playlistActiveMediaEl() {
      try {
        return this.playlistAudioOnly ? this.$refs && this.$refs.playlistAudioEl : this.$refs && this.$refs.playlistVideoEl;
      } catch {
        return null;
      }
    },

    playlistSyncMediaState() {
      const el = this.playlistActiveMediaEl();
      if (!el) return;
      const dur = Number(el.duration || 0);
      const cur = Number(el.currentTime || 0);
      this.playlistMediaDurationSec = Number.isFinite(dur) && dur > 0 ? dur : 0;
      this.playlistMediaCurrentTimeSec = Number.isFinite(cur) && cur >= 0 ? cur : 0;
      this.playlistMediaPlaying = !!el && !el.paused && !el.ended;
      this.playlistMediaMuted = !!el.muted;
      const vol = Number(el.volume);
      this.playlistMediaVolume = Number.isFinite(vol) ? Math.max(0, Math.min(1, vol)) : this.playlistMediaVolume;
    },

    playlistMediaOnLoadedMetadata(ev) {
      try {
        const el = ev && ev.target ? ev.target : this.playlistActiveMediaEl();
        if (!el) return;
        const dur = Number(el.duration || 0);
        this.playlistMediaDurationSec = Number.isFinite(dur) && dur > 0 ? dur : 0;
        const cur = Number(el.currentTime || 0);
        this.playlistMediaCurrentTimeSec = Number.isFinite(cur) && cur >= 0 ? cur : this.playlistMediaCurrentTimeSec;
      } catch {
        // ignore
      }
      this.playlistSyncMediaState();
    },

    playlistMediaOnTimeUpdate(ev) {
      try {
        const el = ev && ev.target ? ev.target : this.playlistActiveMediaEl();
        if (!el) return;
        const cur = Number(el.currentTime || 0);
        if (Number.isFinite(cur) && cur >= 0) this.playlistMediaCurrentTimeSec = cur;
      } catch {
        // ignore
      }
    },

    playlistMediaOnPlay() {
      this.playlistMediaPlaying = true;
    },

    playlistMediaOnPause() {
      this.playlistMediaPlaying = false;
    },

    playlistMediaOnVolumeChange(ev) {
      try {
        const el = ev && ev.target ? ev.target : this.playlistActiveMediaEl();
        if (!el) return;
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

    playlistMediaTogglePlay() {
      const el = this.playlistActiveMediaEl();
      if (!el) return;
      try {
        if (el.paused || el.ended) el.play();
        else el.pause();
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
      const videoEl = this.$refs && this.$refs.playlistVideoEl;
      const audioEl = this.$refs && this.$refs.playlistAudioEl;
      const next = !this.playlistAudioOnly;
      const fromEl = next ? videoEl : audioEl;
      const toEl = next ? audioEl : videoEl;

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
          if (toEl) {
            if (Number.isFinite(t) && t > 0) toEl.currentTime = t;
            if (wasPlaying && typeof toEl.play === "function") toEl.play();
          }
        } catch {
          // ignore
        }
        this.playlistSyncMediaState();
      });
    },

    playlistPrevVideo() {
      const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
      if (!items.length) return;
      const id = this.playlistCurrentVideo && this.playlistCurrentVideo.id ? String(this.playlistCurrentVideo.id) : "";
      const idx = id ? items.findIndex((x) => x && String(x.id) === id) : -1;
      const next = idx > 0 ? items[idx - 1] : items[0];
      if (next) this.playlistSelectVideo(next, { autoPlay: true });
    },

    playlistNextVideo() {
      const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
      if (!items.length) return;
      const id = this.playlistCurrentVideo && this.playlistCurrentVideo.id ? String(this.playlistCurrentVideo.id) : "";
      const idx = id ? items.findIndex((x) => x && String(x.id) === id) : -1;
      const next = idx >= 0 && idx < items.length - 1 ? items[idx + 1] : items[items.length - 1];
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

    playlistPrevDay() {
      const g = this.playlistGranularity();
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      if (!start || !end || !this.playlistSelectedDate) return;
      const next = this._periodClampIso(this._periodAddIso(this.playlistSelectedDate, g, -1), start, end);
      this.playlistSetDate(next);
    },

    playlistNextDay() {
      const g = this.playlistGranularity();
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      if (!start || !end || !this.playlistSelectedDate) return;
      const next = this._periodClampIso(this._periodAddIso(this.playlistSelectedDate, g, 1), start, end);
      this.playlistSetDate(next);
    },

    playlistJump(deltaPeriods) {
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
      if (!pid || !day) return;
      const k = this._playlistBriefKey(pid, g, day);
	      if (k) this.playlistBriefGeneratingKey = k;
	      this.playlistBriefError = "";
	      this.playlistBriefHtml = "";
	      this.playlistBriefMarkdown = "";
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

      const BRIEF_REF_MAX_CHARS = 8;
      const briefTruncLabel = (value) => {
        const raw = String(value || "").trim().replace(/\s+/g, " ");
        if (!raw) return "";
        const chars = Array.from(raw);
        const max = Math.max(1, Number(BRIEF_REF_MAX_CHARS || 0) || 8);
        if (chars.length <= max) return raw;
        if (max === 1) return "…";
        return chars.slice(0, max - 1).join("") + "…";
      };

      const briefUrlLabel = (url) => {
        try {
          const u = String(url || "").trim();
          if (!u) return "视频...";
          const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
          const hit = items.find((v) => v && String(v.url || "").trim() === u);
          const title = hit && hit.title ? String(hit.title).trim() : "";
          if (!title) return "视频...";
          return briefTruncLabel(title) || "视频...";
        } catch {
          return "视频...";
        }
      };

      const briefRefPill = ({ label, url }) => {
        const u = String(url || "").trim();
        if (!u) return "";
        const enc = encodeURIComponent(u);
        const safeUrl = this._escapeHtml(u);
        const rawText = String(label || "").trim() || briefUrlLabel(u);
        const text = briefTruncLabel(rawText) || "视频...";
        const safeText = formatInlineEsc(this._escapeHtml(text));
        return [
          '<span class="inline-flex items-stretch rounded-none border border-slate-700 bg-slate-950/30 overflow-hidden align-middle ml-1 mr-1">',
          `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" title="${safeUrl}" class="min-w-0 max-w-xs px-1 py-0.5 text-[11px] text-slate-200 hover:bg-slate-800/60 truncate no-underline">${safeText}</a>`,
          `<button type="button" class="shrink-0 px-1 py-0.5 border-l border-slate-700 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20 text-[11px]" data-play-url="${enc}" title="播放该视频">▶</button>`,
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
        const re = /\[([^\]\n]+)\]\(\s*(https?:\/\/[^\s\)）]+)\s*[\)）]+\s*|\b(https?:\/\/[^\s\)）]+)\b/g;
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
	      if (this.playlistBriefPromptCopying) return;
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
	        await this._copyToClipboard(prompt);
	        this.toastSuccess("已复制简报提示词");
	      } catch (e) {
	        const msg = e && e.message ? e.message : String(e);
	        this.toastError(`复制失败：${msg}`);
	      } finally {
	        this.playlistBriefPromptCopying = false;
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
	      if (!manualGenerating) {
	        const cached = this._cacheGet(this.playlistBriefHtmlCache, k);
	        if (cached && Number(this.playlistLoadToken || 0) === token) {
	          this.playlistBriefLoading = false;
	          this.playlistBriefError = "";
	          this.playlistBriefHtml = cached;
	          const cachedMd = this._cacheGet(this.playlistBriefMarkdownCache, k);
	          this.playlistBriefMarkdown = cachedMd ? String(cachedMd) : "";
	          return;
	        }
	      }
	      this.playlistBriefLoading = true;
	      this.playlistBriefError = "";
	      this.playlistBriefHtml = "";
	      this.playlistBriefMarkdown = "";
	      const hasVideos = Array.isArray(this.playlistDayVideos) && this.playlistDayVideos.length > 0;
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
          if (s === "failed") {
            const em = brief && brief.error_message ? String(brief.error_message) : "";
            this.playlistBriefHtml = `<div class="text-rose-200 text-sm">生成失败${em ? `：${this._escapeHtml(em)}` : ""}</div>`;
            if (manualGenerating) this.playlistBriefGeneratingKey = "";
            return;
          }
          if (manualGenerating) {
            this.playlistBriefHtml = `<div class="text-slate-400 text-sm">生成中…</div>`;
          } else {
            this.playlistBriefHtml = `<div class="text-slate-400 text-sm">简报状态：${this._escapeHtml(s)}</div>`;
          }
          if (hasVideos) {
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
	        this.playlistBriefMarkdown = md;
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
          if (!hasVideos) {
            this.playlistBriefHtml = `<div class="text-slate-400 text-sm">本周期暂无视频，不生成简报</div>`;
            if (manualGenerating) this.playlistBriefGeneratingKey = "";
            return;
          }
          this.playlistBriefHtml = manualGenerating
            ? `<div class="text-slate-400 text-sm">生成中…</div>`
            : `<div class="text-slate-400 text-sm">暂无简报，已自动触发生成…</div>`;
          this._playlistEnsureBriefEnqueued(pid, g, d);
          this._playlistPollBrief(pid, g, d);
        } else {
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
        this.playlistCancelEditName();
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
        this.playlistCancelEditDescription();
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

      const g = d && d.brief_granularity ? String(d.brief_granularity).trim().toLowerCase() : "day";
      this.playlistSettingsGranularityDraft = ["day", "week", "month"].includes(g) ? g : "day";
      this.playlistSettingsGranularityError = "";

      const prompt = d && d.brief_prompt ? String(d.brief_prompt).trim() : "";
      this.playlistSettingsPromptDraft = prompt || this.briefDefaultDailyPrompt();
      this.playlistSettingsPromptError = "";
    },

    playlistEditSelectedMedia() {
      return resolveMediaItemsByIds(this.mediaIndex, this.playlistEditMediaIds);
    },

    playlistEditFilteredMediaOptions() {
      return filterUnselectedMediaOptions({
        index: this.mediaIndex,
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
