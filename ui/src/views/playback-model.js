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

        const granularity = this.playlistGranularity();
        const today = todayIsoLocal();
        const start = this._periodStartIso(String(detail?.earliest_date || today), granularity);
        const end = this._periodStartIso(today, granularity);
        this.playlistTimelineStart = start;
        this.playlistTimelineEnd = end;
        this.playlistTimelineMax = Math.max(0, periodDiff(start, end, granularity));

        const localState = this.playbackReadState(domainId);
        let requestedBrief = null;
        const requestedBriefId = this.playbackContentTab === "brief" ? String(this.briefV2SelectedId || "").trim() : "";
        if (requestedBriefId) {
          requestedBrief = await this.api(`/briefs/${encodeURIComponent(requestedBriefId)}/structured`);
          if (this.activeView !== "playlist" || Number(this.playlistLoadToken || 0) !== token) return;
          if (String(requestedBrief?.playlist_id || "") !== String(domainId)) throw new Error("简报不属于当前观测域");
          if (String(requestedBrief?.granularity || "day").toLowerCase() !== granularity) requestedBrief = null;
          else this.briefV2Detail = requestedBrief;
        }
        const requestedDate = String(requestedBrief?.period_start || playbackQueryValue("date") || localState.last_date || end);
        const date = periodClampIso(this._periodStartIso(requestedDate, granularity), start, end);
        this.playlistSelectedDate = date;
        this.playlistTimelineValue = Math.max(0, periodDiff(start, date, granularity));
        this.playlistCalendarAnchor = periodClampIso(
          periodAddIso(date, granularity, -(Number(this.playlistCalendarCount || 14) - 1)),
          start,
          end
        );
        this.playlistCalendarUpdateCount();
        this.playlistCalendarEnsureVisible();
        this.playlistPrefetchCalendarCounts();

        const localPeriod = localState.dates?.[date] || {};
        const requestedVideoId = playbackQueryValue("video_id") || String(localPeriod.video_id || "");
        const requestedPosition = numericPosition(playbackQueryValue("t") || localPeriod.t || 0);
        this.playbackPendingSeekSec = requestedPosition;
        await this.playlistLoadDay(date, {
          autoPlay: false,
          preferredVideoId: requestedVideoId,
        });
        if (this.activeView !== "playlist") return;
        if (requestedBrief?.status === "ready" && requestedBrief?.markdown_url) {
          await this.playbackApplyStructuredBrief(requestedBrief, { loadToken: this.playlistLoadToken });
        }
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
      const granularity = this.playlistGranularity();
      const next = periodClampIso(
        this._periodStartIso(String(date || ""), granularity),
        String(this.playlistTimelineStart || ""),
        String(this.playlistTimelineEnd || "")
      );
      if (!next || next === this.playlistSelectedDate) return;
      this.playbackRememberPosition({ syncUrl: false });
      this.playlistMediaPause();
      this.playlistSelectedDate = next;
      this.briefV2SelectedId = "";
      this.playlistTimelineValue = Math.max(
        0,
        periodDiff(String(this.playlistTimelineStart || next), next, granularity)
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
      const granularity = this.playlistGranularity();
      const current = String(this.playlistSelectedDate || this.playlistTimelineEnd || "");
      const next = periodClampIso(
        periodAddIso(current, granularity, amount),
        String(this.playlistTimelineStart || current),
        String(this.playlistTimelineEnd || current)
      );
      if (next === current) return;
      this.playlistCalendarAnchor = periodClampIso(
        periodAddIso(String(this.playlistCalendarAnchor || current), granularity, amount),
        String(this.playlistTimelineStart || current),
        String(this.playlistTimelineEnd || current)
      );
      await this.playbackSetDate(next);
    },

    async playbackSelectVideo(video, { autoPlay = true } = {}) {
      if (!video?.id) return;
      this.playbackPendingSeekSec = 0;
      this.playlistStopBriefSpeech({ clearError: true });
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

    playbackBriefPeriodLabel() {
      const granularity = this.playlistGranularity();
      const selected = String(this.playlistSelectedDate || "");
      if (!selected) return "选择日期";
      const start = this._periodStartIso(selected, granularity);
      const end = this._periodEndIso(start, granularity);
      return granularity === "day" ? start : `${start} ~ ${end}`;
    },

    async playbackApplyStructuredBrief(detail, { loadToken = null } = {}) {
      const token = Number(loadToken || this.playlistLoadToken || 0);
      this.playlistBriefLoading = true;
      this.playlistBriefError = "";
      this.playlistBriefHtml = "";
      this.playlistBriefMarkdown = "";
      this.playlistBriefSpeechText = "";
      try {
        this._abortCtrl("_playlistBriefMdAbortCtrl");
        const ctrl = new AbortController();
        this._playlistBriefMdAbortCtrl = ctrl;
        const response = await this.fetchWithApiAuth(String(detail.markdown_url), { signal: ctrl.signal });
        if (response.status === 401) this.handleApiUnauthorized({});
        if (!response.ok) throw new Error(`${response.status}: brief markdown fetch failed`);
        const markdown = await response.text();
        if (Number(this.playlistLoadToken || 0) !== token) return;
        this.briefV2Detail = detail;
        this.briefV2Markdown = markdown;
        this.briefV2Html = this._briefToHtml(markdown);
        this.playlistBriefMarkdown = markdown;
        this.playlistBriefSpeechText = this._briefToSpeechText(markdown);
        this.playlistBriefHtml = this.briefV2Html;
        this._playlistSetBriefSourceState("ready", "");
        const anchor = String(this.briefV2RequestedAnchor || "");
        if (anchor && typeof requestAnimationFrame === "function") {
          await new Promise((resolve) => requestAnimationFrame(resolve));
          document.getElementById(anchor)?.scrollIntoView({ block: "center", behavior: "smooth" });
          this.briefV2RequestedAnchor = "";
        }
      } catch (error) {
        if (Number(this.playlistLoadToken || 0) !== token || this._isAbortError(error)) return;
        this.playlistBriefError = error?.message || String(error);
      } finally {
        if (Number(this.playlistLoadToken || 0) === token) this.playlistBriefLoading = false;
      }
    },

    async playbackSetContentTab(tab) {
      const next = tab === "brief" ? "brief" : "records";
      if (next === this.playbackContentTab) return;
      this.playbackRememberPosition({ syncUrl: false });
      this.playbackContentTab = next;
      this.briefV2SelectedId = "";
      this.playbackMobileTab = "records";
      this._syncUrl({ push: false });
    },

    openPlaybackBriefs() {
      this.playbackContentTab = "brief";
      this.briefV2SelectedId = "";
      this.switchView("playlist");
    },

    leavePlaybackPage() {
      this.playbackRememberPosition({ syncUrl: false });
      this.playlistLoadToken = Number(this.playlistLoadToken || 0) + 1;
      this.playlistPeriodCountsToken = Number(this.playlistPeriodCountsToken || 0) + 1;
      this._abortCtrl("_playlistCountsAbortCtrl");
      this._abortCtrl("_playlistDayAbortCtrl");
      this._abortCtrl("_playlistBriefAbortCtrl");
      this._abortCtrl("_playlistBriefMdAbortCtrl");
      this._abortCtrl("_playlistPlayableProbeAbortCtrl");
      this._abortCtrl("_playlistSelectAbortCtrl");
      this._abortCtrl("_playlistTranscriptVariantAbortCtrl");
      this.playlistPendingAutoPlayId = "";
      this.playlistStopBriefSpeech({ clearError: true });
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
