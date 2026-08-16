import {
  periodAddIso,
  periodClampIso,
  periodDiff,
  todayIsoLocal,
} from "../shared/playlist-periods.js";

function playbackQueryValue(name) {
  try {
    return new URLSearchParams(window.location.search || "").get(name) || "";
  } catch {
    return "";
  }
}

function numericPosition(value) {
  const next = Number(value);
  return Number.isFinite(next) && next >= 0 ? next : 0;
}

export function createPlaybackViewMethods() {
  return {
    playbackStorageKey(domainId = this.selectedPlaylistId) {
      const id = String(domainId || "").trim();
      return id ? `${this.playbackStateKeyPrefix}.${id}` : "";
    },

    playbackReadState(domainId = this.selectedPlaylistId) {
      const key = this.playbackStorageKey(domainId);
      if (!key) return { last_date: "", dates: {} };
      try {
        const parsed = JSON.parse(localStorage.getItem(key) || "{}");
        return {
          last_date: String(parsed?.last_date || ""),
          dates: parsed?.dates && typeof parsed.dates === "object" ? parsed.dates : {},
        };
      } catch {
        return { last_date: "", dates: {} };
      }
    },

    playbackWriteState(values, domainId = this.selectedPlaylistId) {
      const key = this.playbackStorageKey(domainId);
      if (!key) return;
      try {
        localStorage.setItem(key, JSON.stringify(values || {}));
      } catch {
        // 本地存储不可用时，URL 仍保留当前恢复位置。
      }
    },

    playbackRememberPosition({ syncUrl = true } = {}) {
      const domainId = String(this.selectedPlaylistId || "");
      const date = String(this.playlistSelectedDate || "");
      const videoId = String(this.playlistCurrentVideo?.id || "");
      if (!domainId || !date || !videoId) return;
      const state = this.playbackReadState(domainId);
      const position = this.playbackPendingSeekSec !== null
        ? numericPosition(this.playbackPendingSeekSec)
        : numericPosition(this.playlistMediaCurrentTimeSec);
      state.last_date = date;
      state.dates[date] = {
        video_id: videoId,
        t: Math.max(0, Math.floor(position)),
      };
      this.playbackWriteState(state, domainId);
      if (syncUrl && this.activeView === "playlist") this._syncUrl({ push: false });
    },

    playbackSelectionChanged() {
      if (this.activeView !== "playlist") return;
      if (this.playbackRestoring) return;
      if (!this.playbackRestoring) this.playlistMediaCurrentTimeSec = 0;
      this.playbackRememberPosition({ syncUrl: true });
    },

    async loadPlaybackPage() {
      const domainId = await this.ensureCurrentDomain();
      if (!domainId) {
        this.playlistDetail = null;
        this.playlistDayVideos = [];
        this.pageTitle = "播放列表";
        return;
      }

      const token = Number(this.playlistLoadToken || 0) + 1;
      this.playlistLoadToken = token;
      this.playbackRestoring = true;
      this.playbackDayTruncated = false;
      try {
        const detail = await this.api(`/playlists/${encodeURIComponent(domainId)}/detail`);
        if (this.activeView !== "playlist" || Number(this.playlistLoadToken || 0) !== token) return;
        this.playlistDetail = detail || null;
        this.pageTitle = `${detail?.name || "当前观测域"} · 播放列表`;

        const today = todayIsoLocal();
        const start = String(detail?.earliest_date || today);
        const end = today;
        this.playlistTimelineStart = start;
        this.playlistTimelineEnd = end;
        this.playlistTimelineMax = Math.max(0, periodDiff(start, end, "day"));

        const localState = this.playbackReadState(domainId);
        const requestedDate = playbackQueryValue("date") || localState.last_date || end;
        const date = periodClampIso(requestedDate, start, end);
        this.playlistSelectedDate = date;
        this.playlistTimelineValue = Math.max(0, periodDiff(start, date, "day"));
        this.playlistCalendarAnchor = periodClampIso(
          periodAddIso(date, "day", -(Number(this.playlistCalendarCount || 14) - 1)),
          start,
          end
        );
        this.playlistCalendarUpdateCount();
        this.playlistCalendarEnsureVisible();
        this.playlistPrefetchCalendarCounts();

        const localDay = localState.dates?.[date] || {};
        const requestedVideoId = playbackQueryValue("video_id") || String(localDay.video_id || "");
        const requestedPosition = numericPosition(playbackQueryValue("t") || localDay.t || 0);
        this.playbackPendingSeekSec = requestedPosition;
        await this.playlistLoadDay(date, {
          autoPlay: false,
          preferredVideoId: requestedVideoId,
        });
        if (this.activeView !== "playlist") return;
        this.playbackDayTruncated = this.playlistDayVideos.length >= 500;
        this.playbackRememberPosition({ syncUrl: true });
        this.api(`/domains/${encodeURIComponent(domainId)}/observation/cursor`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ last_page: "playlist" }),
        }).catch(() => null);
      } finally {
        this.playbackRestoring = false;
      }
    },

    async playbackSetDate(date) {
      const next = periodClampIso(
        String(date || ""),
        String(this.playlistTimelineStart || ""),
        String(this.playlistTimelineEnd || "")
      );
      if (!next || next === this.playlistSelectedDate) return;
      this.playbackRememberPosition({ syncUrl: false });
      this.playlistMediaPause();
      this.playlistSelectedDate = next;
      this.playlistTimelineValue = Math.max(
        0,
        periodDiff(String(this.playlistTimelineStart || next), next, "day")
      );
      this.playlistCalendarEnsureVisible();
      this.playlistPrefetchCalendarCounts();
      const saved = this.playbackReadState().dates?.[next] || {};
      this.playbackPendingSeekSec = numericPosition(saved.t || 0);
      await this.playlistLoadDay(next, {
        autoPlay: false,
        preferredVideoId: String(saved.video_id || ""),
      });
      this.playbackDayTruncated = this.playlistDayVideos.length >= 500;
      this.playbackRememberPosition({ syncUrl: true });
    },

    async playbackJumpDays(delta) {
      const amount = Number(delta || 0);
      if (!Number.isFinite(amount) || !amount) return;
      const current = String(this.playlistSelectedDate || this.playlistTimelineEnd || "");
      const next = periodClampIso(
        periodAddIso(current, "day", amount),
        String(this.playlistTimelineStart || current),
        String(this.playlistTimelineEnd || current)
      );
      if (next === current) return;
      this.playlistCalendarAnchor = periodClampIso(
        periodAddIso(String(this.playlistCalendarAnchor || current), "day", amount),
        String(this.playlistTimelineStart || current),
        String(this.playlistTimelineEnd || current)
      );
      await this.playbackSetDate(next);
    },

    async playbackSelectVideo(video, { autoPlay = true } = {}) {
      if (!video?.id) return;
      this.playbackPendingSeekSec = 0;
      await this.playlistSelectVideo(video, { autoPlay });
      this.playbackMobileTab = "transcript";
    },

    playbackMediaOnLoadedMetadata(event) {
      this.playlistMediaOnLoadedMetadata(event);
      const element = event?.target || this.playlistActiveMediaEl();
      if (!element || !this.playlistIsActiveMediaEl(element)) return;
      try {
        element.playbackRate = Number(this.playbackRate || 1);
        const position = numericPosition(this.playbackPendingSeekSec);
        if (position > 0) {
          const duration = Number(element.duration || 0);
          element.currentTime = duration > 0 ? Math.min(position, Math.max(0, duration - 0.25)) : position;
        }
      } catch {
        // 元数据可能仍未允许 seek；后续 timeupdate 会继续保存实际位置。
      }
      this.playbackPendingSeekSec = null;
      this.playlistSyncMediaState();
    },

    playbackMediaOnTimeUpdate(event) {
      this.playlistMediaOnTimeUpdate(event);
      const now = Date.now();
      if (now - Number(this.playbackLastPersistAt || 0) < 1000) return;
      this.playbackLastPersistAt = now;
      this.playbackRememberPosition({ syncUrl: true });
    },

    playbackSetRate(value) {
      const rate = Number(value);
      this.playbackRate = [0.75, 1, 1.25, 1.5, 2].includes(rate) ? rate : 1;
      this.playlistMediaElements().forEach((element) => {
        try {
          element.playbackRate = this.playbackRate;
        } catch {
          // ignore
        }
      });
    },

    playbackSetMobileTab(tab) {
      this.playbackMobileTab = tab === "transcript" ? "transcript" : "records";
    },

    leavePlaybackPage() {
      this.playbackRememberPosition({ syncUrl: false });
      this.playlistLoadToken = Number(this.playlistLoadToken || 0) + 1;
      this.playlistPeriodCountsToken = Number(this.playlistPeriodCountsToken || 0) + 1;
      this._abortCtrl("_playlistCountsAbortCtrl");
      this._abortCtrl("_playlistDayAbortCtrl");
      this._abortCtrl("_playlistPlayableProbeAbortCtrl");
      this._abortCtrl("_playlistSelectAbortCtrl");
      this._abortCtrl("_playlistTranscriptVariantAbortCtrl");
      this.playlistPendingAutoPlayId = "";
      this.playlistMediaPause();
      this.playlistResetMediaElements({ cancelAutoPlay: true });
      this.playlistPlayerVideoUrl = "";
      this.playlistPlayerAudioUrl = "";
      this.playlistCurrentVideo = null;
      this.playlistDayVideosLoading = false;
      this.playlistTranscriptLoading = false;
      this.playbackPendingSeekSec = null;
      this.playbackRestoring = false;
    },
  };
}
