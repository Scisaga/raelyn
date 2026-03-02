function _raelyn_icon(svgBody) {
  return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${svgBody}</svg>`;
}

function RaelynApp() {
  const SIDEBAR_COLLAPSED_KEY = "raelyn.ui.sidebarCollapsed";
  const SIDEBAR_HIDDEN_KEY = "raelyn.ui.sidebarHidden";
  const initialCollapsed = (() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
    } catch {
      return false;
    }
  })();
  const initialHidden = (() => {
    try {
      return localStorage.getItem(SIDEBAR_HIDDEN_KEY) === "1";
    } catch {
      return false;
    }
  })();

  const YTDLP_FORMAT_PRESET_1080 =
    "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best";
  const YTDLP_FORMAT_PRESET_720 =
    "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/bestvideo[height<=720]+bestaudio/best[height<=720]/best";

  return {
    // layout
    sidebarCollapsed: initialCollapsed,
    sidebarHidden: initialHidden,
    sidebarMobilePortrait: false,

    // state
    activeView: "overview",
    pageTitle: "概览",
    healthOk: false,
    globalStatus: "",
    toasts: [],
    _toastSeq: 0,
    _toastTimers: new Map(),
    services: {
      db: { ok: false, error: null },
      s3: { ok: false, bucket: "", error: null },
      asr: { ok: false, configured: false, url: "", error: null },
      llm: { ok: false, configured: false, url: "", error: null },
    },
    navItems: [
      { key: "overview", label: "概览", icon: _raelyn_icon('<path d="M4 4h7v7H4z"/><path d="M13 4h7v7h-7z"/><path d="M4 13h7v7H4z"/><path d="M13 13h7v7h-7z"/>') },
      { key: "media", label: "媒体", icon: _raelyn_icon('<path d="M16 18a4 4 0 0 0-8 0"/><circle cx="12" cy="10" r="4"/><path d="M5 20h14"/>') },
      { key: "videos", label: "视频", icon: _raelyn_icon('<path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>') },
      { key: "jobs", label: "任务", icon: _raelyn_icon('<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>') },
      { key: "playlists", label: "播放列表", icon: _raelyn_icon('<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>') },
      { key: "playlist", label: "播放列表页", hidden: true, icon: _raelyn_icon('<path d="M4 19V5"/><path d="M8 5h12"/><path d="M8 12h12"/><path d="M8 19h12"/>') },
      { key: "briefs", label: "提示词", icon: _raelyn_icon('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h8"/>') },
      { key: "settings", label: "设置", icon: _raelyn_icon('<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"/><path d="M19.4 15a1.8 1.8 0 0 0 .36 1.98l.04.04a2.2 2.2 0 0 1-1.56 3.76 2.2 2.2 0 0 1-1.56-.64l-.04-.04a1.8 1.8 0 0 0-1.98-.36 1.8 1.8 0 0 0-1.08 1.64V21a2.2 2.2 0 1 1-4.4 0v-.06a1.8 1.8 0 0 0-1.08-1.64 1.8 1.8 0 0 0-1.98.36l-.04.04a2.2 2.2 0 0 1-3.76-1.56 2.2 2.2 0 0 1 .64-1.56l.04-.04A1.8 1.8 0 0 0 4.6 15a1.8 1.8 0 0 0-1.64-1.08H2.9a2.2 2.2 0 1 1 0-4.4h.06A1.8 1.8 0 0 0 4.6 8.4a1.8 1.8 0 0 0-.36-1.98l-.04-.04A2.2 2.2 0 0 1 5.76 2.6a2.2 2.2 0 0 1 1.56.64l.04.04A1.8 1.8 0 0 0 9.34 3.6 1.8 1.8 0 0 0 10.42 2h.06a2.2 2.2 0 1 1 4.4 0h-.06a1.8 1.8 0 0 0 1.08 1.64 1.8 1.8 0 0 0 1.98-.36l.04-.04a2.2 2.2 0 0 1 3.76 1.56 2.2 2.2 0 0 1-.64 1.56l-.04.04a1.8 1.8 0 0 0-.36 1.98 1.8 1.8 0 0 0 1.64 1.08h.06a2.2 2.2 0 1 1 0 4.4h-.06A1.8 1.8 0 0 0 19.4 15z"/>') },
    ],
    stats: { mediaCount: 0, videoCount: 0, pendingJobs: 0, failedJobs: 0 },

    mediaIndex: [],
    mediaList: [],
    mediaQuery: "",
    videoList: [],
    videoStatus: "",
    videoMediaIds: [],
    videoMediaTagQuery: "",
    videoMediaTagOpen: false,
    videoQuery: "",
    videoFrom: "",
    videoTo: "",
    videoLimit: 20,
    videoOffset: 0,
    videoHasMore: true,
    videoLoadingList: false,
    videoLoadingMore: false,
    videoIo: null,
    jobsTab: "active", // active | succeeded | failed
    jobListActive: [],
    jobListDone: [],
    jobsDoneLoading: false,
    jobVideoById: {},
    jobPlaylistById: {},
    _jobVideoFetchInFlight: {},
    _jobPlaylistFetchInFlight: {},
    jobsTypeFilter: "",
    jobsDoneFrom: "",
    jobsDoneTo: "",
    jobsSeriesDone: [],
    jobsSeriesDoneMax: 0,
    jobsSeriesLoading: false,
    jobsSeriesError: "",
    jobsSeriesLastAt: 0,
    jobsWs: null,
    jobsWsConnected: false,
    jobsWsError: "",
    jobActionInFlight: {},
    jobsHiddenDoneIds: {},
    jobsHiddenActiveIds: {},
    jobsOptimisticActive: [],
    _jobsActiveServerCount: 0,
    _jobsActiveLastFetchAt: 0,
    jobsDoneChart: null,
    jobsDoneSeries: null,
    playlistList: [],
    playlistListQuery: "",
    selectedPlaylistId: null,
    briefDailyPrompt: "",
    briefDailyPromptLoaded: false,
    briefDailyPromptSaving: false,
    briefDailyPromptError: "",
    ytdlpCookiesText: "",
    ytdlpCookiesLoaded: false,
    ytdlpCookiesSaving: false,
    ytdlpCookiesError: "",
    ytdlpSubtitlesEnabled: false,
    ytdlpSubtitlesLoaded: false,
    ytdlpSubtitlesSaving: false,
    ytdlpSubtitlesError: "",
    ytdlpFormatPreset: "1080", // 1080 | 720 | custom
    ytdlpFormatCustomText: "",
    ytdlpFormatLoaded: false,
    ytdlpFormatSaving: false,
    ytdlpFormatError: "",
    playerVideo: null,
    playerAssets: [],
    playerVideoUrl: "",
    playerVideoDownloadUrl: "",
    playerVideoDownloadName: "",
    playerAudioDownloadUrl: "",
    playerAudioDownloadName: "",
    playerTranscriptText: "",
    playerTranscriptAssetId: "",
    playerTranscriptSource: "",
    playerTranscriptVariant: "",
    playerTranscriptPolishMethod: "",
    playerTranscriptUpdatedAt: "",
    playerTranscriptNotice: "",
    playerTranscriptRefreshing: false,
    playerRetranscribeSubmitting: false,
    playerTranscriptPollKey: "",
    playerTranscriptPollTries: 0,
    playerDescription: "",
    playerLoading: false,
    playerError: "",
    playerTab: "transcript", // transcript

    modals: { addMedia: false, createPlaylist: false, videoPlayer: false },
    addMediaUrl: "",
    addMediaSubmitting: false,
    addMediaError: "",
    createPlaylistName: "",
    createPlaylistDesc: "",
    createPlaylistMediaIds: [],
    createPlaylistMediaTagQuery: "",
    createPlaylistMediaTagOpen: false,
    createPlaylistAvatarFile: null,
    createPlaylistBackgroundFile: null,

    playlistPageId: null,
    playlistDetail: null,
    playlistSelectedDate: "",
    playlistDayVideos: [],
    playlistDayVideosLoading: false,
    playlistDayVideosError: "",
    playlistCurrentVideo: null,
    playlistAudioOnly: false,
    playlistPlayerVideoUrl: "",
    playlistPlayerAudioUrl: "",
    playlistPlayerError: "",
    playlistTranscriptText: "",
    playlistTranscriptLoading: false,
    playlistTranscriptError: "",
    playlistTranscriptLanguage: "",
    playlistTranscriptSource: "",
    playlistTranscriptUpdatedAt: "",
    playlistBriefHtml: "",
    playlistBriefLoading: false,
    playlistBriefError: "",
    playlistBriefGeneratingKey: "",
    playlistBriefAutoRequests: new Set(),
    playlistBriefAutoPoll: new Map(),
    playlistMediaDurationSec: 0,
    playlistMediaCurrentTimeSec: 0,
    playlistMediaPlaying: false,
    playlistMediaMuted: false,
    playlistMediaVolume: 1,
    playlistTimelineStart: "",
    playlistTimelineEnd: "",
    playlistTimelineMax: 0,
    playlistTimelineValue: 0,
    playlistCalendarCount: 14,
    playlistCalendarAnchor: "",
    playlistNameEditing: false,
    playlistNameDraft: "",
    playlistNameSaving: false,
    playlistDescEditing: false,
    playlistDescDraft: "",
    playlistDescSaving: false,
    playlistEditMediaOpen: false,
    playlistEditMediaIds: [],
    playlistEditMediaTagQuery: "",
    playlistEditMediaTagOpen: false,

    mediaDisplayName(m) {
      return (m && (m.name || m.provider_media_id || m.url)) || "";
    },

    mediaAvatarLabel(m) {
      const base = (m && (m.name || m.provider_media_id || "")) || "";
      const s = String(base).trim() || "?";
      // Prefer handle without leading '@'
      const t = s.startsWith("@") ? s.slice(1) : s;
      const cleaned = t.replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, "");
      if (!cleaned) return "?";
      return cleaned.slice(0, 2).toUpperCase();
    },

    mediaAvatarClasses(m) {
      const p = (m && m.provider) || "";
      if (p === "youtube") return "bg-rose-500/15 text-rose-200 ring-rose-400/20";
      if (p === "bilibili") return "bg-sky-500/15 text-sky-200 ring-sky-400/20";
      return "bg-slate-800 text-slate-200 ring-slate-700/60";
    },

    videoMediaDisplayName(v) {
      return (v && (v.media_name || v.media_id)) || "";
    },

    videoMediaAvatarLabel(v) {
      const base = (v && (v.media_name || "")) || "";
      const s = String(base).trim() || "?";
      const t = s.startsWith("@") ? s.slice(1) : s;
      const cleaned = t.replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, "");
      if (!cleaned) return "?";
      return cleaned.slice(0, 2).toUpperCase();
    },

    videoSelectedMedia() {
      const ids = Array.isArray(this.videoMediaIds) ? this.videoMediaIds : [];
      if (!ids.length) return [];
      const idx = new Map((this.mediaIndex || []).map((m) => [String(m.id), m]));
      return ids.map((id) => idx.get(String(id))).filter(Boolean);
    },

    videoFilteredMediaOptions() {
      const q = String(this.videoMediaTagQuery || "")
        .trim()
        .toLowerCase();
      const selected = new Set(Array.isArray(this.videoMediaIds) ? this.videoMediaIds : []);
      let items = Array.isArray(this.mediaIndex) ? this.mediaIndex : [];
      items = items.filter((m) => m && !selected.has(String(m.id)));
      if (q) {
        items = items.filter((m) => {
          const name = String(this.mediaDisplayName(m) || "").toLowerCase();
          const prov = String(m.provider || "").toLowerCase();
          return name.includes(q) || prov.includes(q);
        });
      }
      return items.slice(0, 50);
    },

    videoAddMediaTag(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      if (!Array.isArray(this.videoMediaIds)) this.videoMediaIds = [];
      if (!this.videoMediaIds.includes(id)) this.videoMediaIds.push(id);
      this.videoMediaTagQuery = "";
      this.videoMediaTagOpen = false;
      this.loadVideos();
    },

    videoAddFirstFilteredMediaTag() {
      const items = this.videoFilteredMediaOptions();
      if (!items.length) return;
      this.videoAddMediaTag(items[0].id);
    },

    videoRemoveMediaTag(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      this.videoMediaIds = (Array.isArray(this.videoMediaIds) ? this.videoMediaIds : []).filter((x) => String(x) !== id);
      this.loadVideos();
    },

    videoClearMediaTags() {
      this.videoMediaIds = [];
      this.videoMediaTagQuery = "";
      this.videoMediaTagOpen = false;
      this.loadVideos();
    },

    createPlaylistSelectedMedia() {
      const ids = Array.isArray(this.createPlaylistMediaIds) ? this.createPlaylistMediaIds : [];
      if (!ids.length) return [];
      const idx = new Map((this.mediaIndex || []).map((m) => [String(m.id), m]));
      return ids.map((id) => idx.get(String(id))).filter(Boolean);
    },

    createPlaylistFilteredMediaOptions() {
      const q = String(this.createPlaylistMediaTagQuery || "")
        .trim()
        .toLowerCase();
      const selected = new Set(Array.isArray(this.createPlaylistMediaIds) ? this.createPlaylistMediaIds : []);
      let items = Array.isArray(this.mediaIndex) ? this.mediaIndex : [];
      items = items.filter((m) => m && !selected.has(String(m.id)));
      if (q) {
        items = items.filter((m) => {
          const name = String(this.mediaDisplayName(m) || "").toLowerCase();
          const prov = String(m.provider || "").toLowerCase();
          return name.includes(q) || prov.includes(q);
        });
      }
      return items.slice(0, 50);
    },

    createPlaylistAddMediaTag(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      if (!Array.isArray(this.createPlaylistMediaIds)) this.createPlaylistMediaIds = [];
      if (!this.createPlaylistMediaIds.includes(id)) this.createPlaylistMediaIds.push(id);
      this.createPlaylistMediaTagQuery = "";
      this.createPlaylistMediaTagOpen = false;
    },

    createPlaylistAddFirstFilteredMediaTag() {
      const items = this.createPlaylistFilteredMediaOptions();
      if (!items.length) return;
      this.createPlaylistAddMediaTag(items[0].id);
    },

    createPlaylistRemoveMediaTag(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      this.createPlaylistMediaIds = (Array.isArray(this.createPlaylistMediaIds) ? this.createPlaylistMediaIds : []).filter(
        (x) => String(x) !== id
      );
    },

    formatDuration(sec) {
      const s = Number(sec || 0);
      if (!Number.isFinite(s) || s <= 0) return "";
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const ss = Math.floor(s % 60);
      const pad = (n) => String(n).padStart(2, "0");
      return h > 0 ? `${h}:${pad(m)}:${pad(ss)}` : `${m}:${pad(ss)}`;
    },

    formatDateTime(ts) {
      if (!ts) return "";
      try {
        const d = new Date(ts);
        if (Number.isNaN(d.getTime())) return "";
        return d.toLocaleString();
      } catch {
        return "";
      }
    },

    formatDateTimeShort(ts) {
      if (!ts) return "";
      try {
        const d = new Date(ts);
        if (Number.isNaN(d.getTime())) return "";
        return d.toLocaleString(undefined, {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        });
      } catch {
        return "";
      }
    },

    servicePillClass(ok) {
      return ok
        ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-200"
        : "border-rose-500/20 bg-rose-500/10 text-rose-200";
    },

    serviceText(svc) {
      if (!svc) return "unknown";
      if (svc.configured === false) return "未配置";
      return svc.ok ? "OK" : "Error";
    },

    async openVideoPlayer(v) {
      if (!v || !v.id) return;
      this.modals.addMedia = false;
      this.modals.createPlaylist = false;
      this.modals.videoPlayer = true;
      this.playerVideo = v;
      this.playerAssets = [];
      this.playerVideoUrl = "";
      this.playerVideoDownloadUrl = "";
      this.playerVideoDownloadName = "";
      this.playerAudioDownloadUrl = "";
      this.playerAudioDownloadName = "";
      this.playerTranscriptText = "";
      this.playerTranscriptAssetId = "";
      this.playerTranscriptSource = "";
      this.playerTranscriptVariant = "";
      this.playerTranscriptPolishMethod = "";
      this.playerTranscriptUpdatedAt = "";
      this.playerTranscriptNotice = "";
      this.playerTranscriptRefreshing = false;
      this.playerRetranscribeSubmitting = false;
      this.playerTranscriptPollKey = "";
      this.playerTranscriptPollTries = 0;
      this.playerDescription = "";
      this.playerError = "";
      this.playerLoading = true;
      this.playerTab = "transcript";

      try {
        const [assets, transcript, detail] = await Promise.all([
          this.api(`/videos/${v.id}/assets?presign=true&download=true`),
          this.api(`/videos/${v.id}/transcript`),
          this.api(`/videos/${v.id}`),
        ]);

        this.playerAssets = Array.isArray(assets) ? assets : [];
        const detailDesc = detail && typeof detail === "object" ? detail.description || "" : "";
        this.playerDescription = detailDesc || "";

        const videos = this.playerAssets.filter((a) => a.type === "video" && a.presigned_url);
        const mp4 = videos.find((a) => String(a.format || "").toLowerCase() === "mp4") || videos[0] || null;
        this.playerVideoUrl = (mp4 && mp4.presigned_url) || "";
        this.playerVideoDownloadUrl = (mp4 && (mp4.download_url || mp4.presigned_url)) || "";
        this.playerVideoDownloadName = (mp4 && mp4.filename) || "";

        const audios = this.playerAssets.filter((a) => a.type === "audio" && (a.download_url || a.presigned_url));
        const m4a = audios.find((a) => String(a.format || "").toLowerCase() === "m4a") || audios[0] || null;
        this.playerAudioDownloadUrl = (m4a && (m4a.download_url || m4a.presigned_url)) || "";
        this.playerAudioDownloadName = (m4a && m4a.filename) || "";

        this.playerTranscriptText = transcript && transcript.ok ? transcript.text || "" : "";
        this.playerTranscriptAssetId = transcript && transcript.ok ? transcript.asset_id || "" : "";
        this.playerTranscriptSource = transcript && transcript.ok ? transcript.source || "" : "";
        this.playerTranscriptVariant = transcript && transcript.ok ? transcript.variant || "" : "";
        this.playerTranscriptPolishMethod = transcript && transcript.ok ? transcript.polish_method || "" : "";
        this.playerTranscriptUpdatedAt = transcript && transcript.ok ? transcript.updated_at || transcript.created_at || "" : "";
      } catch (e) {
        this.playerError = e && e.message ? e.message : String(e);
      } finally {
        this.playerLoading = false;
      }
    },

    closeVideoPlayer() {
      this.modals.videoPlayer = false;
      this.playerVideoUrl = "";
      this.playerVideoDownloadUrl = "";
      this.playerAudioDownloadUrl = "";
      this.playerVideo = null;
      this.playerTranscriptPollKey = "";
    },

    async refreshPlayerTranscript() {
      const vid = this.playerVideo && this.playerVideo.id ? String(this.playerVideo.id) : "";
      if (!vid) return;
      if (this.playerTranscriptRefreshing) return;
      this.playerTranscriptRefreshing = true;
      try {
        const transcript = await this.api(`/videos/${encodeURIComponent(vid)}/transcript`);
        if (transcript && transcript.ok) {
          this.playerTranscriptText = transcript.text || "";
          this.playerTranscriptAssetId = transcript.asset_id || this.playerTranscriptAssetId || "";
          this.playerTranscriptSource = transcript.source || this.playerTranscriptSource || "";
          this.playerTranscriptVariant = transcript.variant || this.playerTranscriptVariant || "";
          this.playerTranscriptPolishMethod = transcript.polish_method || this.playerTranscriptPolishMethod || "";
          this.playerTranscriptUpdatedAt = transcript.updated_at || transcript.created_at || this.playerTranscriptUpdatedAt || "";
        }
      } finally {
        this.playerTranscriptRefreshing = false;
      }
    },

    _playerPollTranscriptAfterRetranscribe(vid, prevAssetId) {
      try {
        const key = `${String(vid)}:${Date.now()}`;
        this.playerTranscriptPollKey = key;
        this.playerTranscriptPollTries = 0;

        const tick = async () => {
          if (this.playerTranscriptPollKey !== key) return;
          if (!this.modals.videoPlayer) return;
          if (!this.playerVideo || String(this.playerVideo.id || "") !== String(vid)) return;

          const tries = Number(this.playerTranscriptPollTries || 0);
          if (tries >= 40) {
            this.playerTranscriptNotice = "重新转写任务已提交；稍后可再次点击或手动刷新文本。";
            return;
          }
          this.playerTranscriptPollTries = tries + 1;

          try {
            const transcript = await this.api(`/videos/${encodeURIComponent(vid)}/transcript`);
            if (transcript && transcript.ok) {
              const nextAssetId = transcript.asset_id || "";
              const nextText = transcript.text || "";
              const nextVariant = transcript.variant || "";
              const nextMethod = transcript.polish_method || "";
              const nextUpdatedAt = transcript.updated_at || transcript.created_at || "";
              const changed =
                (nextText && String(nextText) !== String(this.playerTranscriptText || "")) ||
                (prevAssetId && nextAssetId && nextAssetId !== prevAssetId) ||
                (nextVariant && nextVariant !== String(this.playerTranscriptVariant || "")) ||
                (nextMethod && nextMethod !== String(this.playerTranscriptPolishMethod || "")) ||
                (nextUpdatedAt && nextUpdatedAt !== String(this.playerTranscriptUpdatedAt || ""));
              if (changed) {
                this.playerTranscriptText = nextText;
                this.playerTranscriptAssetId = nextAssetId || this.playerTranscriptAssetId || "";
                this.playerTranscriptSource = transcript.source || this.playerTranscriptSource || "";
                this.playerTranscriptVariant = transcript.variant || this.playerTranscriptVariant || "";
                this.playerTranscriptPolishMethod = transcript.polish_method || this.playerTranscriptPolishMethod || "";
                this.playerTranscriptUpdatedAt = nextUpdatedAt || this.playerTranscriptUpdatedAt || "";
                this.playerTranscriptNotice = "转写文本已更新。";
                this.playerTranscriptPollKey = "";
                return;
              }
            }
          } catch {
            // ignore
          }

          const rawDelay = 1200 + tries * 650;
          const delay = Math.min(9000, rawDelay);
          setTimeout(tick, delay);
        };

        setTimeout(tick, 1200);
      } catch {
        // ignore
      }
    },

    async retranscribePlayerTranscript() {
      const vid = this.playerVideo && this.playerVideo.id ? String(this.playerVideo.id) : "";
      if (!vid) return;
      if (this.playerRetranscribeSubmitting) return;
      this.playerRetranscribeSubmitting = true;
      this.playerTranscriptNotice = "";
      const prevAssetId = this.playerTranscriptAssetId || "";
      try {
        await this.api(`/videos/${encodeURIComponent(vid)}/transcript/retranscribe`, { method: "POST" });
        this.playerTranscriptNotice = "已提交重新转写，正在等待生成…";
        this.toastSuccess("已提交重新转写任务", { action: this.toastJobsAction() });
        this._playerPollTranscriptAfterRetranscribe(vid, prevAssetId);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.toastError(`重新转写提交失败：${msg}`, { action: this.toastJobsAction() });
        this.playerTranscriptNotice = `重新转写失败：${msg}`;
      } finally {
        this.playerRetranscribeSubmitting = false;
      }
    },

    maximizePlayer() {
      try {
        const el = this.$refs && this.$refs.playerVideoEl;
        if (el && el.requestFullscreen) el.requestFullscreen();
      } catch {
        // ignore
      }
    },

    _isMobilePortrait() {
      try {
        return window.matchMedia("(max-width: 767px) and (orientation: portrait)").matches;
      } catch {
        return window.innerWidth < 768 && window.innerHeight > window.innerWidth;
      }
    },

    _applySidebarMode() {
      const mobilePortrait = this._isMobilePortrait();

      if (mobilePortrait) {
        // Portrait mode: auto-hide by default and only allow hidden/collapsed (no expanded state).
        if (!this.sidebarMobilePortrait) this.sidebarHidden = true;
        this.sidebarCollapsed = true;
      } else {
        // Non-portrait mode: sidebar is always visible; keep collapsed/expanded behavior.
        this.sidebarHidden = false;
        if (this.sidebarMobilePortrait) {
          // Restore the user's collapsed/expanded preference when leaving portrait mode.
          try {
            this.sidebarCollapsed = localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
          } catch {
            // ignore
          }
        }
      }

      this.sidebarMobilePortrait = mobilePortrait;
    },

    toggleSidebar() {
      if (this.sidebarMobilePortrait) {
        this.sidebarHidden = !this.sidebarHidden;
        try {
          localStorage.setItem(SIDEBAR_HIDDEN_KEY, this.sidebarHidden ? "1" : "0");
        } catch {
          // ignore
        }
        return;
      }

      this.sidebarCollapsed = !this.sidebarCollapsed;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, this.sidebarCollapsed ? "1" : "0");
      } catch {
        // ignore
      }
    },

    async api(path, options) {
      const resp = await fetch(`/api${path}`, options || {});
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`${resp.status}: ${text}`);
      }
      const ct = resp.headers.get("content-type") || "";
      return ct.includes("application/json") ? resp.json() : resp.text();
    },

    toastPush({ level = "info", title = "", message = "", action = null } = {}) {
      const msg = String(message || "").trim();
      if (!msg) return;
      const lv = String(level || "info").trim() || "info";

      const nextSeq = Number(this._toastSeq || 0) + 1;
      this._toastSeq = nextSeq;
      const id = `t${Date.now()}_${nextSeq}`;

      const t = {
        id,
        level: lv,
        title: String(title || "").trim() || "",
        message: msg,
        action: action && typeof action === "object" ? action : null,
        open: true,
        ts: Date.now(),
      };

      if (!Array.isArray(this.toasts)) this.toasts = [];
      this.toasts.push(t);

      while (this.toasts.length > 3) {
        const removed = this.toasts.shift();
        if (removed && removed.id) this._toastClearTimer(removed.id);
      }

      if (lv !== "error") {
        this._toastClearTimer(id);
        try {
          const timer = setTimeout(() => this.toastDismiss(id), 3500);
          if (this._toastTimers && this._toastTimers.set) this._toastTimers.set(id, timer);
        } catch {
          // ignore
        }
      }
    },

    _toastClearTimer(id) {
      const key = String(id || "").trim();
      if (!key) return;
      try {
        const timer = this._toastTimers && this._toastTimers.get ? this._toastTimers.get(key) : null;
        if (timer) clearTimeout(timer);
      } catch {
        // ignore
      }
      try {
        if (this._toastTimers && this._toastTimers.delete) this._toastTimers.delete(key);
      } catch {
        // ignore
      }
    },

    toastDismiss(id) {
      const key = String(id || "").trim();
      if (!key) return;
      this._toastClearTimer(key);

      const list = Array.isArray(this.toasts) ? this.toasts : [];
      const idx = list.findIndex((x) => x && String(x.id) === key);
      if (idx < 0) return;
      try {
        list[idx].open = false;
      } catch {
        // ignore
      }
      this.toasts = list;

      setTimeout(() => {
        const cur = Array.isArray(this.toasts) ? this.toasts : [];
        this.toasts = cur.filter((x) => x && String(x.id) !== key);
      }, 180);
    },

    toastSuccess(message, { action = null, title = "" } = {}) {
      this.toastPush({ level: "success", title, message, action });
    },

    toastError(message, { action = null, title = "" } = {}) {
      this.toastPush({ level: "error", title, message, action });
    },

    toastJobsAction() {
      return { type: "jobs", label: "查看任务" };
    },

    toastHandleAction(t) {
      try {
        const a = t && t.action ? t.action : null;
        if (!a || a.type !== "jobs") return;
        if (this.modals) {
          this.modals.videoPlayer = false;
          this.modals.addMedia = false;
          this.modals.createPlaylist = false;
        }
        this.jobsTab = "active";
        this.switchView("jobs");
        if (t && t.id) this.toastDismiss(t.id);
      } catch {
        // ignore
      }
    },

    _viewPath(key) {
      if (!key || key === "overview") return "/";
      return `/${encodeURIComponent(key)}`;
    },

    _parseViewFromLocation() {
      const path = (window.location.pathname || "/").replace(/\/+$/, "") || "/";
      if (path === "/" || path === "") return "overview";
      const key = decodeURIComponent(path.slice(1));
      const exists = this.navItems.some((x) => x.key === key);
      return exists ? key : "overview";
    },

    _applyQueryFromLocation(viewKey) {
      const sp = new URLSearchParams(window.location.search || "");
      if (viewKey === "media") {
        this.mediaQuery = sp.get("q") || "";
      }
      if (viewKey === "videos") {
        this.videoStatus = sp.get("status") || "";
        const mediaIn = sp.get("media_id_in") || "";
        const mediaOne = sp.get("media_id") || "";
        if (mediaIn) {
          this.videoMediaIds = mediaIn
            .split(",")
            .map((x) => x.trim())
            .filter(Boolean);
        } else if (mediaOne) {
          this.videoMediaIds = [mediaOne];
        } else {
          this.videoMediaIds = [];
        }
        this.videoQuery = sp.get("q") || "";
        const fromIso = sp.get("from") || "";
        const toIso = sp.get("to") || "";
        if (fromIso) this.videoFrom = this._toLocalInputValue(new Date(fromIso));
        if (toIso) this.videoTo = this._toLocalInputValue(new Date(toIso));
        if (!fromIso && !toIso) this._ensureVideoRange();
      }
      if (viewKey === "jobs") {
        this.jobsTab = sp.get("tab") || this.jobsTab || "active";
        this.jobsTypeFilter = sp.get("type") || this.jobsTypeFilter || "";
        const fromIso = sp.get("from") || "";
        const toIso = sp.get("to") || "";
        if (fromIso) this.jobsDoneFrom = this._toLocalInputValue(new Date(fromIso));
        if (toIso) this.jobsDoneTo = this._toLocalInputValue(new Date(toIso));
      }
      if (viewKey === "playlists") {
        this.selectedPlaylistId = sp.get("playlist_id") || this.selectedPlaylistId;
      }
      if (viewKey === "playlist") {
        const pid = sp.get("playlist_id") || this.playlistPageId || this.selectedPlaylistId;
        this.playlistPageId = pid || null;
        this.selectedPlaylistId = pid || this.selectedPlaylistId;
        this.playlistSelectedDate = sp.get("date") || this.playlistSelectedDate || "";
        if (!this.playlistSelectedDate) {
          this.playlistSelectedDate = this._todayIsoLocal();
        }
      }
    },

    _buildSearchForView(viewKey) {
      const sp = new URLSearchParams();
      if (viewKey === "media" && this.mediaQuery) sp.set("q", this.mediaQuery);
      if (viewKey === "videos" && this.videoStatus) sp.set("status", this.videoStatus);
      if (viewKey === "videos" && Array.isArray(this.videoMediaIds) && this.videoMediaIds.length) {
        sp.set("media_id_in", this.videoMediaIds.join(","));
      }
      if (viewKey === "videos" && this.videoQuery) sp.set("q", this.videoQuery);
      if (viewKey === "videos" && this.videoFrom) sp.set("from", new Date(this.videoFrom).toISOString());
      if (viewKey === "videos" && this.videoTo) sp.set("to", new Date(this.videoTo).toISOString());
      if (viewKey === "jobs") {
        sp.set("tab", this.jobsTab || "active");
        if (this.jobsTypeFilter) sp.set("type", this.jobsTypeFilter);
        if (this.jobsTab !== "active") {
          const fromIso = this.jobsDoneFrom ? new Date(this.jobsDoneFrom).toISOString() : "";
          const toIso = this.jobsDoneTo ? new Date(this.jobsDoneTo).toISOString() : "";
          if (fromIso) sp.set("from", fromIso);
          if (toIso) sp.set("to", toIso);
        }
      }
      if (viewKey === "playlists" && this.selectedPlaylistId) sp.set("playlist_id", this.selectedPlaylistId);
      if (viewKey === "playlist") {
        const pid = this.playlistPageId || this.selectedPlaylistId;
        if (pid) sp.set("playlist_id", String(pid));
        if (this.playlistSelectedDate) sp.set("date", String(this.playlistSelectedDate));
      }
      const s = sp.toString();
      return s ? `?${s}` : "";
    },

    _syncUrl({ push = false } = {}) {
      const path = this._viewPath(this.activeView);
      const search = this._buildSearchForView(this.activeView);
      const url = `${path}${search}`;
      const state = { view: this.activeView };
      if (push) history.pushState(state, "", url);
      else history.replaceState(state, "", url);
    },

    switchView(key) {
      if (this.activeView === "videos" && key !== "videos") this._teardownVideoIo();
      if (key === "videos") this._ensureVideoRange();
      this.activeView = key;
      const item = this.navItems.find((x) => x.key === key);
      this.pageTitle = item ? item.label : key;
      this._syncUrl({ push: true });
      this.refreshActive();
    },

    async refreshActive() {
      try {
        if (this.activeView !== "jobs") {
          this._disconnectJobsWs();
          this._destroyJobsDoneChart();
        }
        if (this.activeView === "overview") return await this.loadStats();
        if (this.activeView === "media") return await this.loadMedia();
        if (this.activeView === "videos") return await this.loadVideos();
        if (this.activeView === "jobs") return await this.loadJobs();
        if (this.activeView === "playlists") return await this.loadPlaylists();
        if (this.activeView === "playlist") return await this.loadPlaylistPage();
        if (this.activeView === "briefs") return await this.loadBriefs();
        if (this.activeView === "settings") return await this.loadSettings();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    async loadStats() {
      try {
        const s = await this.api(`/stats`);
        this.stats.mediaCount = s.media_count || 0;
        this.stats.videoCount = s.video_count || 0;
        this.stats.pendingJobs = s.pending_jobs || 0;
        this.stats.failedJobs = s.failed_jobs || 0;
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    async loadMedia() {
      try {
        this._syncUrl({ push: false });
        const q = this.mediaQuery ? `&q=${encodeURIComponent(this.mediaQuery)}` : "";
        this.mediaList = await this.api(`/media?limit=50&offset=0${q}`);
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    async loadVideos() {
      try {
        this.videoLoadingList = true;
        this._syncUrl({ push: false });
        if (!this.mediaIndex || this.mediaIndex.length === 0) {
          this.mediaIndex = await this.api(`/media?limit=500&offset=0`);
        }
        this.videoOffset = 0;
        this.videoHasMore = true;
        this.videoLoadingMore = false;

        const qs = new URLSearchParams();
        qs.set("limit", String(this.videoLimit || 20));
        qs.set("offset", "0");
        if (this.videoStatus) qs.set("status", this.videoStatus);
        if (Array.isArray(this.videoMediaIds) && this.videoMediaIds.length) qs.set("media_id_in", this.videoMediaIds.join(","));
        if (this.videoQuery) qs.set("q", this.videoQuery);
        if (this.videoFrom) qs.set("published_since", new Date(this.videoFrom).toISOString());
        if (this.videoTo) qs.set("published_until", new Date(this.videoTo).toISOString());

        const items = await this.api(`/videos?${qs.toString()}`);
        this.videoList = Array.isArray(items) ? items : [];
        this.videoOffset = this.videoList.length;
        const lim = Number(this.videoLimit || 20);
        this.videoHasMore = this.videoList.length >= lim;
        if (this.videoHasMore) this._setupVideoIo();
        else this._teardownVideoIo();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      } finally {
        this.videoLoadingList = false;
      }
    },

    _teardownVideoIo() {
      try {
        if (this.videoIo) this.videoIo.disconnect();
      } catch {
        // ignore
      }
      this.videoIo = null;
    },

    _setupVideoIo() {
      if (this.activeView !== "videos") return;
      this.$nextTick(() => {
        const el = this.$refs && this.$refs.videoInfiniteSentinel;
        if (!el) return;
        this._teardownVideoIo();
        const io = new IntersectionObserver(
          (entries) => {
            if (!entries || !entries.some((e) => e.isIntersecting)) return;
            this.loadMoreVideos();
          },
          { root: null, rootMargin: "800px 0px", threshold: 0 }
        );
        this.videoIo = io;
        io.observe(el);
      });
    },

    async loadMoreVideos() {
      if (this.activeView !== "videos") return;
      if (this.videoLoadingList || this.videoLoadingMore) return;
      if (!this.videoHasMore) return;

      try {
        this.videoLoadingMore = true;
        const qs = new URLSearchParams();
        qs.set("limit", String(this.videoLimit || 20));
        qs.set("offset", String(this.videoOffset || 0));
        if (this.videoStatus) qs.set("status", this.videoStatus);
        if (Array.isArray(this.videoMediaIds) && this.videoMediaIds.length) qs.set("media_id_in", this.videoMediaIds.join(","));
        if (this.videoQuery) qs.set("q", this.videoQuery);
        if (this.videoFrom) qs.set("published_since", new Date(this.videoFrom).toISOString());
        if (this.videoTo) qs.set("published_until", new Date(this.videoTo).toISOString());

        const items = await this.api(`/videos?${qs.toString()}`);
        const arr = Array.isArray(items) ? items : [];
        const cur = Array.isArray(this.videoList) ? this.videoList : [];
        const seen = new Set(cur.map((v) => String(v && v.id)));
        const fresh = arr.filter((v) => v && v.id && !seen.has(String(v.id)));
        this.videoList = cur.concat(fresh);
        this.videoOffset = (this.videoOffset || 0) + arr.length;

        const lim = Number(this.videoLimit || 20);
        this.videoHasMore = arr.length >= lim;
        if (!this.videoHasMore) this._teardownVideoIo();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      } finally {
        this.videoLoadingMore = false;
      }
    },

    async loadJobs() {
      await this.refreshJobs();
    },

    jobsActiveCount() {
      const server = Array.isArray(this.jobListActive) ? this.jobListActive.length : 0;
      const extra = Array.isArray(this.jobsOptimisticActive) ? this.jobsOptimisticActive.length : 0;
      return server + extra;
    },

    _cleanupJobsOptimisticActive() {
      const now = Date.now();
      const maxAgeMs = 60 * 1000;
      const arr = Array.isArray(this.jobsOptimisticActive) ? this.jobsOptimisticActive : [];
      this.jobsOptimisticActive = arr.filter((x) => x && x.ts && now - x.ts < maxAgeMs);
    },

    _addJobsOptimisticActive(key, meta) {
      const k = String(key || "").trim();
      if (!k) return;
      if (!Array.isArray(this.jobsOptimisticActive)) this.jobsOptimisticActive = [];
      if (this.jobsOptimisticActive.some((x) => x && x.key === k)) return;
      const m = meta && typeof meta === "object" ? meta : {};
      this.jobsOptimisticActive.push({ key: k, ts: Date.now(), type: m.type ? String(m.type) : null });
      this._cleanupJobsOptimisticActive();
    },

    _removeJobsOptimisticActive(key) {
      const k = String(key || "").trim();
      if (!k) return;
      this.jobsOptimisticActive = (Array.isArray(this.jobsOptimisticActive) ? this.jobsOptimisticActive : []).filter(
        (x) => !(x && x.key === k)
      );
    },

    _reconcileJobsOptimisticActiveByDelta(delta) {
      const d = Number(delta || 0);
      if (!Number.isFinite(d) || d <= 0) return;
      if (!Array.isArray(this.jobsOptimisticActive) || this.jobsOptimisticActive.length === 0) return;
      this.jobsOptimisticActive = this.jobsOptimisticActive.slice(Math.min(d, this.jobsOptimisticActive.length));
    },

    _reconcileJobsOptimisticActiveByJobs(jobs) {
      const list = Array.isArray(jobs) ? jobs : [];
      if (!Array.isArray(this.jobsOptimisticActive) || this.jobsOptimisticActive.length === 0) return;
      if (!list.length) return;
      const resolved = new Set();
      for (const e of this.jobsOptimisticActive) {
        if (!e || !e.key || !e.ts || !e.type) continue;
        const since = Number(e.ts) - 5000;
        const ok = list.some((j) => {
          if (!j || !j.type) return false;
          if (String(j.type) !== String(e.type)) return false;
          const created = Date.parse(j.created_at || "");
          return Number.isFinite(created) && created >= since;
        });
        if (ok) resolved.add(String(e.key));
      }
      if (resolved.size) {
        this.jobsOptimisticActive = this.jobsOptimisticActive.filter((x) => !(x && resolved.has(String(x.key))));
      }
    },

    _toLocalInputValue(d) {
      const pad = (n) => String(n).padStart(2, "0");
      const yyyy = d.getFullYear();
      const mm = pad(d.getMonth() + 1);
      const dd = pad(d.getDate());
      const hh = pad(d.getHours());
      const mi = pad(d.getMinutes());
      return `${yyyy}-${mm}-${dd}T${hh}:${mi}`;
    },

    _wsUrl(path) {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      return `${proto}://${window.location.host}${path}`;
    },

    setJobsTab(tab) {
      const prev = this.jobsTab;
      this.jobsTab = tab;
      this._syncUrl({ push: false });
      if (tab === "active") this._destroyJobsDoneChart();
      // If the chart was destroyed (or we're switching back from active quickly),
      // bypass the refresh throttle so we can repopulate the chart immediately.
      if (prev === "active" && tab !== "active") this.jobsSeriesLastAt = 0;
      this.refreshJobs();
    },

    _ensureJobsDoneRange() {
      if (this.jobsDoneFrom && this.jobsDoneTo) return;
      const now = new Date();
      const since = new Date(now.getTime() - 24 * 3600 * 1000);
      this.jobsDoneFrom = this._toLocalInputValue(since);
      this.jobsDoneTo = this._toLocalInputValue(now);
    },

    _ensureVideoRange() {
      if (this.videoFrom && this.videoTo) return;
      const now = new Date();
      const since = new Date(now.getTime() - 24 * 3600 * 1000);
      if (!this.videoFrom) this.videoFrom = this._toLocalInputValue(since);
      if (!this.videoTo) this.videoTo = this._toLocalInputValue(now);
    },

    _disconnectJobsWs() {
      try {
        if (this.jobsWs) this.jobsWs.close();
      } catch {
        // ignore
      }
      this.jobsWs = null;
      this.jobsWsConnected = false;
    },

    _connectJobsWs() {
      if (this.jobsWs) return;
      const qs = new URLSearchParams();
      qs.set("status_in", "pending,running");
      qs.set("limit", "200");
      qs.set("interval_seconds", "1");
      if (this.jobsTypeFilter) qs.set("type", this.jobsTypeFilter);
      const url = this._wsUrl(`/api/ws/jobs?${qs.toString()}`);
      const ws = new WebSocket(url);
      this.jobsWs = ws;
      this.jobsWsError = "";

      ws.onopen = () => {
        this.jobsWsConnected = true;
      };
      ws.onclose = () => {
        this.jobsWsConnected = false;
        this.jobsWs = null;
        if (this.activeView === "jobs") {
          setTimeout(() => this._connectJobsWs(), 800);
        }
      };
      ws.onerror = () => {
        this.jobsWsError = "WebSocket error";
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data || "{}");
          if (msg.type !== "jobs") return;
          const jobs = Array.isArray(msg.jobs) ? msg.jobs : [];
          const prevServer = Number(this._jobsActiveServerCount || 0);
          this._jobsActiveServerCount = jobs.length;

          // Keep "optimistic" active count consistent: if server count goes up,
          // assume some of the optimistic enqueues have materialized.
          const delta = jobs.length - prevServer;
          if (delta > 0) this._reconcileJobsOptimisticActiveByDelta(delta);
          this._reconcileJobsOptimisticActiveByJobs(jobs);

          const hidden = this.jobsHiddenActiveIds || {};
          const seen = new Set(jobs.map((j) => String(j && j.id)));
          for (const id of Object.keys(hidden || {})) {
            if (!seen.has(String(id))) delete hidden[id];
          }
          this.jobsHiddenActiveIds = hidden;

          const filtered = jobs.filter((j) => j && j.id && !hidden[String(j.id)]);
          this.jobListActive = filtered;
          this.ensureJobContextForList(filtered);
          this._cleanupJobsOptimisticActive();
        } catch {
          // ignore
        }
      };
    },

    async _fetchJobsActiveSnapshot({ force = false } = {}) {
      try {
        if (this.activeView !== "jobs") return;
        const now = Date.now();
        if (!force && this._jobsActiveLastFetchAt && now - this._jobsActiveLastFetchAt < 2500) return;
        this._jobsActiveLastFetchAt = now;

        const qs = new URLSearchParams();
        qs.set("status_in", "pending,running");
        qs.set("limit", "200");
        qs.set("offset", "0");
        if (this.jobsTypeFilter) qs.set("type", this.jobsTypeFilter);
        const items = await this.api(`/jobs?${qs.toString()}`);
        const jobs = Array.isArray(items) ? items : [];

        const prevServer = Number(this._jobsActiveServerCount || 0);
        this._jobsActiveServerCount = jobs.length;
        const delta = jobs.length - prevServer;
        if (delta > 0) this._reconcileJobsOptimisticActiveByDelta(delta);
        this._reconcileJobsOptimisticActiveByJobs(jobs);

        const hidden = this.jobsHiddenActiveIds || {};
        const seen = new Set(jobs.map((j) => String(j && j.id)));
        for (const id of Object.keys(hidden || {})) {
          if (!seen.has(String(id))) delete hidden[id];
        }
        this.jobsHiddenActiveIds = hidden;

        const filtered = jobs.filter((j) => j && j.id && !hidden[String(j.id)]);
        this.jobListActive = filtered;
        this.ensureJobContextForList(filtered);
        this._cleanupJobsOptimisticActive();
      } catch {
        // ignore: WS is the primary channel
      }
    },

    jobProgressPct(j) {
      const cur = j && typeof j.progress_current === "number" ? j.progress_current : null;
      const tot = j && typeof j.progress_total === "number" ? j.progress_total : null;
      if (!tot || tot <= 0 || cur == null) return null;
      const pct = Math.round((cur * 100) / tot);
      return Math.max(0, Math.min(100, pct));
    },

    async loadJobsDone() {
      this.jobsDoneLoading = true;
      try {
        this._ensureJobsDoneRange();
        this._syncUrl({ push: false });

        const fromIso = this.jobsDoneFrom ? new Date(this.jobsDoneFrom).toISOString() : "";
        const toIso = this.jobsDoneTo ? new Date(this.jobsDoneTo).toISOString() : "";
        const statusIn = this.jobsTab === "succeeded" ? "succeeded" : "failed,canceled";

        const qs = new URLSearchParams();
        qs.set("status_in", statusIn);
        qs.set("limit", "200");
        qs.set("offset", "0");
        if (this.jobsTypeFilter) qs.set("type", this.jobsTypeFilter);
        if (fromIso) qs.set("finished_since", fromIso);
        if (toIso) qs.set("finished_until", toIso);
        const items = await this.api(`/jobs?${qs.toString()}`);
        const hidden = this.jobsHiddenDoneIds || {};
        const all = Array.isArray(items) ? items : [];
        this.jobListDone = all.filter((j) => j && j.id && !hidden[String(j.id)]);
        this.ensureJobContextForList(this.jobListDone);
      } finally {
        this.jobsDoneLoading = false;
      }
    },

    jobsTypeOptions() {
      const known = [
        "media.sync_profile",
        "media.sync_videos",
        "video.download",
        "video.extract_audio",
        "video.normalize_subtitle",
        "video.asr_transcribe",
        "video.generate_note",
        "brief.generate_daily",
      ];
      const s = new Set(known);
      for (const j of Array.isArray(this.jobListActive) ? this.jobListActive : []) {
        if (j && j.type) s.add(String(j.type));
      }
      for (const j of Array.isArray(this.jobListDone) ? this.jobListDone : []) {
        if (j && j.type) s.add(String(j.type));
      }
      return Array.from(s).filter(Boolean).sort();
    },

    applyJobsTypeFilter() {
      this._disconnectJobsWs();
      this._syncUrl({ push: false });
      this.refreshJobs();
      this.refreshJobsSeries({ force: true });
    },

    clearJobsTypeFilter() {
      this.jobsTypeFilter = "";
      this.applyJobsTypeFilter();
    },

    async applyJobsRange() {
      if (this.activeView !== "jobs" || this.jobsTab === "active") return;
      this._syncUrl({ push: false });
      await Promise.all([this.loadJobsDone(), this.refreshJobsSeries({ force: true })]);
    },

    _jobsSeriesMax(series) {
      let max = 0;
      for (const p of Array.isArray(series) ? series : []) {
        const t = p && typeof p.total === "number" ? p.total : 0;
        if (t > max) max = t;
      }
      return max;
    },

    _destroyJobsDoneChart() {
      try {
        clearTimeout(this._jobsDoneChartRetryTimer);
      } catch {
        // ignore
      }
      this._jobsDoneChartRetryTimer = null;
      try {
        if (this.jobsDoneChart) this.jobsDoneChart.remove();
      } catch {
        // ignore
      }
      this.jobsDoneChart = null;
      this.jobsDoneSeries = null;
    },

    _ensureJobsDoneChart() {
      if (this.jobsDoneChart && this.jobsDoneSeries) return true;
      const el = this.$refs && this.$refs.jobsDoneChart;
      if (!el) return false;
      if (el.clientWidth < 10 || el.clientHeight < 10) return false;
      const LC = window.LightweightCharts;
      if (!LC || typeof LC.createChart !== "function") return false;

      function JobsDoneStackedBarsRenderer() {
        this._bars = [];
        this._barSpacing = 6;
        this._visibleRange = null;
        this._conflationFactor = 1;
      }

      JobsDoneStackedBarsRenderer.prototype.update = function (data) {
        this._bars = (data && data.bars) || [];
        this._barSpacing = (data && data.barSpacing) || 6;
        this._visibleRange = (data && data.visibleRange) || null;
        this._conflationFactor = (data && data.conflationFactor) || 1;
      };

      JobsDoneStackedBarsRenderer.prototype.draw = function (target, priceToCoordinate) {
        const bars = Array.isArray(this._bars) ? this._bars : [];
        if (!bars.length) return;
        const visible = this._visibleRange;
        const from = visible && typeof visible.from === "number" ? Math.max(0, Math.floor(visible.from)) : 0;
        const to = visible && typeof visible.to === "number" ? Math.min(bars.length, Math.ceil(visible.to)) : bars.length;
        const spacing = (this._barSpacing || 6) * (this._conflationFactor || 1);
        const widthFactor = 0.72;

        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio, verticalPixelRatio }) => {
          const wPx = Math.max(1, Math.floor(spacing * widthFactor * horizontalPixelRatio));
          const half = Math.floor(wPx / 2);

          const y0v = priceToCoordinate(0);
          if (y0v == null) return;
          const y0 = Math.round(y0v * verticalPixelRatio);

          for (let i = from; i < to; i++) {
            const b = bars[i];
            if (!b || !b.originalData) continue;
            const d = b.originalData;
            const succeeded = Number(d.succeeded || 0);
            const failed = Number(d.failed || 0);
            const canceled = Number(d.canceled || 0);
            const total = succeeded + failed + canceled;
            if (!total) continue;

            const x = Math.round(b.x * horizontalPixelRatio);
            const left = x - half;

            const y1v = priceToCoordinate(succeeded);
            const y2v = priceToCoordinate(succeeded + failed);
            const y3v = priceToCoordinate(total);
            if (y1v == null || y2v == null || y3v == null) continue;

            const y1 = Math.round(y1v * verticalPixelRatio);
            const y2 = Math.round(y2v * verticalPixelRatio);
            const y3 = Math.round(y3v * verticalPixelRatio);

            const drawSeg = (yBottom, yTop, color) => {
              const top = Math.min(yBottom, yTop);
              const bottom = Math.max(yBottom, yTop);
              const h = bottom - top;
              if (h <= 0) return;
              context.fillStyle = color;
              context.fillRect(left, top, wPx, h);
            };

            // Stack: succeeded (bottom) -> failed -> canceled (top)
            drawSeg(y0, y1, "rgba(16, 185, 129, 0.75)"); // emerald-500
            drawSeg(y1, y2, "rgba(244, 63, 94, 0.75)"); // rose-500
            drawSeg(y2, y3, "rgba(148, 163, 184, 0.55)"); // slate-400

            // outline
            context.strokeStyle = "rgba(30, 41, 59, 0.55)"; // slate-800-ish
            context.lineWidth = Math.max(1, Math.floor(horizontalPixelRatio));
            const outW = Math.max(1, wPx - 1);
            const outH = Math.max(1, Math.abs(y0 - y3) - 1);
            context.strokeRect(left + 0.5, Math.min(y0, y3) + 0.5, outW, outH);
          }
        });
      };

      function JobsDoneStackedBarsPaneView() {
        this._renderer = new JobsDoneStackedBarsRenderer();
      }

      JobsDoneStackedBarsPaneView.prototype.renderer = function () {
        return this._renderer;
      };

      JobsDoneStackedBarsPaneView.prototype.update = function (data) {
        this._renderer.update(data);
      };

      JobsDoneStackedBarsPaneView.prototype.priceValueBuilder = function (row) {
        const succeeded = Number((row && row.succeeded) || 0);
        const failed = Number((row && row.failed) || 0);
        const canceled = Number((row && row.canceled) || 0);
        const total = succeeded + failed + canceled;
        return [0, total, total];
      };

      JobsDoneStackedBarsPaneView.prototype.isWhitespace = function (row) {
        return !row || row.time == null;
      };

      JobsDoneStackedBarsPaneView.prototype.defaultOptions = function () {
        return LC.customSeriesDefaultOptions;
      };

      const chart = LC.createChart(el, {
        autoSize: true,
        localization: {
          timeFormatter: (time) => {
            try {
              if (typeof time === "number") return new Date(time * 1000).toLocaleString();
              if (time && typeof time === "object" && typeof time.year === "number") {
                const d = new Date(time.year, (time.month || 1) - 1, time.day || 1);
                return d.toLocaleDateString();
              }
              return String(time);
            } catch {
              return String(time);
            }
          },
        },
        layout: {
          background: { type: LC.ColorType.Solid, color: "rgba(0,0,0,0)" },
          textColor: "rgba(148, 163, 184, 0.85)",
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Trebuchet MS', Roboto, Ubuntu, sans-serif",
          fontSize: 11,
          attributionLogo: true,
        },
        rightPriceScale: { visible: false, scaleMargins: { top: 0.18, bottom: 0.1 } },
        leftPriceScale: { visible: false },
        grid: {
          vertLines: { visible: true, color: "rgba(30, 41, 59, 0.35)" },
          horzLines: { visible: true, color: "rgba(30, 41, 59, 0.35)" },
        },
        timeScale: {
          borderVisible: true,
          borderColor: "rgba(30, 41, 59, 0.55)",
          timeVisible: true,
          secondsVisible: false,
          tickMarkFormatter: (time, tickMarkType, locale) => {
            try {
              const loc = locale || undefined;
              let d = null;
              if (typeof time === "number") d = new Date(time * 1000);
              else if (time && typeof time === "object" && typeof time.year === "number") {
                d = new Date(time.year, (time.month || 1) - 1, time.day || 1);
              }
              if (!d || Number.isNaN(d.getTime())) return "";
              if (tickMarkType === LC.TickMarkType.DayOfMonth || tickMarkType === LC.TickMarkType.Month || tickMarkType === LC.TickMarkType.Year) {
                return d.toLocaleDateString(loc, { month: "2-digit", day: "2-digit" });
              }
              return d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" });
            } catch {
              return "";
            }
          },
        },
        crosshair: { mode: LC.CrosshairMode.Hidden },
        handleScroll: false,
        handleScale: false,
      });

      const series = chart.addCustomSeries(new JobsDoneStackedBarsPaneView(), {
        lastValueVisible: false,
        priceLineVisible: false,
      });

      this.jobsDoneChart = chart;
      this.jobsDoneSeries = series;
      // Apply the current control range if available (even before data arrives),
      // so the displayed window always matches the time range inputs.
      try {
        const r = this._jobsDoneRangeSeconds();
        if (r) chart.timeScale().setVisibleRange(r);
      } catch {
        // ignore
      }
      return true;
    },

    _jobsDoneRangeSeconds() {
      try {
        const fromSec = this.jobsDoneFrom ? Math.floor(new Date(this.jobsDoneFrom).getTime() / 1000) : null;
        const toSec = this.jobsDoneTo ? Math.floor(new Date(this.jobsDoneTo).getTime() / 1000) : null;
        if (fromSec == null || toSec == null) return null;
        if (!Number.isFinite(fromSec) || !Number.isFinite(toSec)) return null;
        if (toSec <= fromSec) return null;
        return { from: fromSec, to: toSec };
      } catch {
        return null;
      }
    },

    _updateJobsDoneChart() {
      if (!this._ensureJobsDoneChart()) {
        if (this.activeView === "jobs" && this.jobsTab !== "active") {
          clearTimeout(this._jobsDoneChartRetryTimer);
          this._jobsDoneChartRetryTimer = setTimeout(() => this._updateJobsDoneChart(), 80);
        }
        return;
      }
      const series = this.jobsDoneSeries;
      const chart = this.jobsDoneChart;
      if (!series || !chart) return;

      const points = Array.isArray(this.jobsSeriesDone) ? this.jobsSeriesDone : [];
      const countsByMinute = new Map();
      for (const p of points) {
        const ts = p && p.ts ? new Date(p.ts) : null;
        if (!ts || Number.isNaN(ts.getTime())) continue;
        const sec = Math.floor(ts.getTime() / 1000);
        const minute = Math.floor(sec / 60) * 60;
        countsByMinute.set(minute, {
          succeeded: Number(p.succeeded || 0),
          failed: Number(p.failed || 0),
          canceled: Number(p.canceled || 0),
        });
      }

      const r = this._jobsDoneRangeSeconds();
      const data = [];
      if (r) {
        const start = Math.floor(r.from / 60) * 60;
        for (let t = start; t < r.to; t += 60) {
          const c = countsByMinute.get(t) || { succeeded: 0, failed: 0, canceled: 0 };
          data.push({ time: t, ...c });
        }
      } else {
        for (const [t, c] of countsByMinute.entries()) data.push({ time: t, ...c });
        data.sort((a, b) => a.time - b.time);
      }

      series.setData(data);
      try {
        if (r) chart.timeScale().setVisibleRange(r);
        else chart.timeScale().fitContent();
      } catch {
        // ignore
      }
    },

    jobStatusPillClass(status) {
      const s = String(status || "").toLowerCase();
      if (s === "succeeded") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
      if (s === "failed") return "border-rose-500/30 bg-rose-500/10 text-rose-200";
      if (s === "canceled") return "border-slate-600 bg-slate-800/40 text-slate-200";
      if (s === "running") return "border-sky-500/30 bg-sky-500/10 text-sky-200";
      if (s === "pending") return "border-amber-500/30 bg-amber-500/10 text-amber-200";
      return "border-slate-700 bg-slate-950/30 text-slate-200";
    },

    jobStatusLabel(j) {
      const s = String((j && j.status) || "").toLowerCase() || "-";
      const pct = s === "running" ? this.jobProgressPct(j) : null;
      if (pct != null) return `${s} · ${pct}%`;
      return s;
    },

    formatTs(v) {
      if (!v) return "-";
      try {
        const d = new Date(v);
        if (Number.isNaN(d.getTime())) return String(v);
        return d.toLocaleString();
      } catch {
        return String(v);
      }
    },

    formatTsShort(v) {
      if (!v) return "-";
      try {
        const d = new Date(v);
        if (Number.isNaN(d.getTime())) return String(v);
        const pad = (n) => String(n).padStart(2, "0");
        return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
      } catch {
        return String(v);
      }
    },

    jobMetaLabel(j) {
      if (!j || typeof j !== "object") return "";
      if (j.media_name) return String(j.media_name || "");
      const mid = j.params && j.params.media_id ? String(j.params.media_id) : "";
      if (!mid) return "";
      const m = (this.mediaIndex || []).find((x) => String(x.id) === mid);
      return m ? this.mediaDisplayName(m) : "";
    },

    _shortId(v, n = 8) {
      const s = String(v || "").trim();
      if (!s) return "";
      return s.length <= n ? s : s.slice(0, n);
    },

    jobParamId(j, key) {
      if (!j || typeof j !== "object") return "";
      const params = j.params && typeof j.params === "object" ? j.params : null;
      const raw = params && params[key] ? String(params[key]) : "";
      return String(raw || "").trim();
    },

    jobMediaId(j) {
      return this.jobParamId(j, "media_id");
    },

    jobVideoId(j) {
      return this.jobParamId(j, "video_id");
    },

    jobPlaylistId(j) {
      return this.jobParamId(j, "playlist_id");
    },

    jobPlaylistDate(j) {
      return this.jobParamId(j, "date");
    },

    jobVideoLabel(j) {
      const vid = this.jobVideoId(j);
      if (!vid) return "";
      const v = this.jobVideoById && this.jobVideoById[vid] ? this.jobVideoById[vid] : null;
      const title = v && v.title ? String(v.title).trim() : "";
      if (title) return title;
      const pv = v && v.provider_video_id ? String(v.provider_video_id).trim() : "";
      if (pv) return pv;
      return this._shortId(vid);
    },

    jobPlaylistLabel(j) {
      const pid = this.jobPlaylistId(j);
      if (!pid) return "";
      const p = this.jobPlaylistById && this.jobPlaylistById[pid] ? this.jobPlaylistById[pid] : null;
      const name = p && p.name ? String(p.name).trim() : "";
      if (name) return name;
      return this._shortId(pid);
    },

    ensureJobContextForList(list) {
      const jobs = Array.isArray(list) ? list : [];
      const videoIds = new Set();
      const playlistIds = new Set();
      for (const j of jobs) {
        const vid = this.jobVideoId(j);
        if (vid) videoIds.add(vid);
        const pid = this.jobPlaylistId(j);
        if (pid) playlistIds.add(pid);
      }
      for (const vid of videoIds) this._ensureJobVideo(vid);
      for (const pid of playlistIds) this._ensureJobPlaylist(pid);
    },

    async _ensureJobVideo(videoId) {
      const vid = String(videoId || "").trim();
      if (!vid) return;
      if (this.jobVideoById && this.jobVideoById[vid]) return;
      if (this._jobVideoFetchInFlight && this._jobVideoFetchInFlight[vid]) return;
      this._jobVideoFetchInFlight[vid] = true;
      try {
        const v = await this.api(`/videos/${encodeURIComponent(vid)}`);
        if (v && typeof v === "object") {
          if (!this.jobVideoById) this.jobVideoById = {};
          this.jobVideoById[vid] = v;
        }
      } catch {
        // best-effort
      } finally {
        try {
          delete this._jobVideoFetchInFlight[vid];
        } catch {
          this._jobVideoFetchInFlight[vid] = false;
        }
      }
    },

    async _ensureJobPlaylist(playlistId) {
      const pid = String(playlistId || "").trim();
      if (!pid) return;
      if (this.jobPlaylistById && this.jobPlaylistById[pid]) return;
      if (this._jobPlaylistFetchInFlight && this._jobPlaylistFetchInFlight[pid]) return;
      this._jobPlaylistFetchInFlight[pid] = true;
      try {
        const p = await this.api(`/playlists/${encodeURIComponent(pid)}`);
        if (p && typeof p === "object") {
          if (!this.jobPlaylistById) this.jobPlaylistById = {};
          this.jobPlaylistById[pid] = p;
        }
      } catch {
        // best-effort
      } finally {
        try {
          delete this._jobPlaylistFetchInFlight[pid];
        } catch {
          this._jobPlaylistFetchInFlight[pid] = false;
        }
      }
    },

    async openJobVideo(j) {
      const vid = this.jobVideoId(j);
      if (!vid) return;
      try {
        if (!this.jobVideoById || !this.jobVideoById[vid]) await this._ensureJobVideo(vid);
        const v = this.jobVideoById && this.jobVideoById[vid] ? this.jobVideoById[vid] : null;
        if (v && v.id) {
          await this.openVideoPlayer(v);
        }
      } catch {
        // ignore
      }
    },

    openJobPlaylist(j) {
      const pid = this.jobPlaylistId(j);
      if (!pid) return;
      const dateStr = this.jobPlaylistDate(j);
      this.openPlaylistPage(pid, dateStr);
    },

    async openJobMedia(j) {
      const mid = this.jobMediaId(j);
      if (!mid) return;
      this.mediaQuery = mid;
      this.switchView("media");
      await this.loadMedia();
    },

    async refreshJobsSeries({ force = false } = {}) {
      try {
        if (this.activeView !== "jobs") return;
        if (this.jobsTab === "active") return;
        this._ensureJobsDoneRange();
        const now = Date.now();
        if (!force && this.jobsSeriesLastAt && now - this.jobsSeriesLastAt < 5000) return;

        const fromIso = this.jobsDoneFrom ? new Date(this.jobsDoneFrom).toISOString() : "";
        const toIso = this.jobsDoneTo ? new Date(this.jobsDoneTo).toISOString() : "";
        if (!fromIso || !toIso) return;
        this.jobsSeriesLastAt = now;

        this.jobsSeriesLoading = true;
        this.jobsSeriesError = "";

        const commonQs = new URLSearchParams();
        commonQs.set("since", fromIso);
        commonQs.set("until", toIso);
        commonQs.set("bucket", "minute");
        if (this.jobsTypeFilter) commonQs.set("type", this.jobsTypeFilter);

        const qsDone = new URLSearchParams(commonQs);
        qsDone.set("status_in", "succeeded,failed,canceled");
        qsDone.set("ts_field", "finished_at");

        const done = await this.api(`/jobs/series?${qsDone.toString()}`);
        const donePoints = Array.isArray(done) ? done : [];

        this.jobsSeriesDone = donePoints.map((p) => {
          const c = (p && p.counts) || {};
          const succeeded = Number(c.succeeded || 0);
          const failed = Number(c.failed || 0);
          const canceled = Number(c.canceled || 0);
          return { ts: p.ts, succeeded, failed, canceled, total: succeeded + failed + canceled };
        });

        this.jobsSeriesDoneMax = this._jobsSeriesMax(this.jobsSeriesDone);
        this._updateJobsDoneChart();
      } catch (e) {
        this.jobsSeriesError = e && e.message ? e.message : String(e);
      } finally {
        this.jobsSeriesLoading = false;
      }
    },

    async refreshJobs() {
      try {
        if (this.activeView !== "jobs") {
          this._disconnectJobsWs();
          return;
        }
        // Keep the active-jobs WS connected even when browsing done tabs,
        // so the "待处理 / 运行中" count updates in real time.
        this._connectJobsWs();
        this._fetchJobsActiveSnapshot();

        if (this.jobsTab === "active") {
          this.jobListDone = [];
          return;
        }
        await this.loadJobsDone();
        this.refreshJobsSeries();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    async loadPlaylists() {
      try {
        this.playlistList = await this.api(`/playlists?limit=100&offset=0`);
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    briefDefaultDailyPrompt() {
      return [
        "## 提示词",
        "",
        "你是一个**财经内容分析助手**。请基于下面提供的多条视频文字内容（可能含转写、字幕、摘要、片段拼接），生成一份可直接发布的**Markdown**财经简报。",
        "",
        "### 核心约束（必须遵守）",
        "",
        "1. **只使用文本中明确出现的信息**：",
        "",
        "   * 不要补充常识性“背景”来充当事实。",
        "   * 任何无法从文本直接验证的内容，一律写：**“文本未提及”** 或 **“文本表述不充分，无法确认”**。",
	        "2. **每条要点必须附 1–3 个来源链接**：",
	        "",
	        "   * 来源链接必须来自文本中的视频链接/来源字段。",
	        "   * 统一写法：句末用 `（来源：https://...，https://...）`（可用纯 URL 或 Markdown 链接）。",
	        "3. 输出语言：**中文**。",
	        "4. 输出必须是 **Markdown**，段落清晰，便于直接发布。",
	        "5. 不需要：**主体归类**、**今日视频清单**。",
	        "6. **不要输出“总标题/日期/分割线”**：",
	        "",
	        "   * 不要输出 H1（例如 `# ...`）或任何“总标题”。",
	        "   * 不要输出“每日财经简报”字样。",
	        "   * 不要输出“日期：...”或任何单独的日期行。",
	        "   * 不要输出 Markdown 水平分割线（例如 `---`）。",
	        "",
	        "### 输出结构",
	        "",
	        "你必须严格按以下结构输出，并且**标题行必须用 Markdown 标题语法，单独成行**（不要写成正文里的小标题）：",
	        "",
	        "## 今日要点",
	        "",
	        "* 用 **bullet** 列出关键信息点（只写要点，不要写散文段落）。",
	        "* 每条要点：",
	        "",
	        "  * 句式尽量短，先给“结论/信息”，再给“条件/范围/时间”。",
	        "  * 必须在句末附 **1–3 个来源链接**，格式：`（来源：...）`。",
	        "  * 若文本出现具体数值（涨跌幅、利率、通胀、盈利、库存、产量等），必须原样保留，并注明它属于谁/哪个时间窗口；若时间窗口不清楚，写“文本未提及”。",
	        "",
	        "示例格式（示例仅展示格式，不要复用示例内容）：",
	        "",
	        "* 美债收益率在文本中被描述为____，并被归因于____（来源：[视频标题](https://...)）",
	        "* 某公司业绩/指引被提到____，但对同比/环比口径未说明（文本未提及）（来源：[视频标题](https://...)）",
	        "",
	        "## 影响与逻辑链",
	        "",
	        "* 写清楚“**因 → 果**”或“**事件 → 资产影响**”的链条。",
	        "* 每条链条必须满足：链条中的每个关键节点都能在文本中找到依据；找不到就标注“文本未提及”。",
	        "* 仍然要在句末附来源链接。",
	        "",
	        "## 风险与不确定性",
	        "",
	        "* 重点写：口径不一致、数据缺失、时间不明、推断过度、样本偏差、叙述互相矛盾之处。",
	        "* 如不同视频说法冲突：明确写出“视频 A 说…；视频 B 说…；无法判定”（并分别给来源）。",
	        "",
	        "## 关注清单",
	        "",
	        "* 给出“接下来应继续跟踪”的观察项（不是预测结论），如：",
	        "",
	        "  * 关键数据发布、会议/财报、政策口径、价格/利差/汇率阈值、行业库存、地缘事件进展等。",
	        "* 每一条都要说明：为什么要跟踪（依据文本哪个说法），并附来源链接。",
	        "* 如果文本没有足够依据，写“文本未提及”。",
	        "",
	        "## 行动建议",
	        "",
	        "* 给“**条件触发式**”建议，形式如：",
	        "",
	        "  * “若文本中提到的 A 指标继续…，可考虑…；否则…（文本未提及具体阈值）”",
	        "* **不允许直接给确定性买卖指令**；只能写“可考虑/可关注/需验证”。",
	        "* 每条建议仍需来源链接；若建议的关键条件缺失，标注“文本未提及”。",
        "",
        "简报日期（仅供你理解上下文，不要在输出中出现）：{{date}}",
        "",
        "以下是视频文本（多条，可能包含标题与链接；若未包含链接，请在输出中把来源写为“文本未提供链接”）：",
        "{{blocks}}",
      ].join("\n").trim();
    },

    async loadBriefs({ force = false } = {}) {
      try {
        if (this.briefDailyPromptLoaded && !force) return;
        this.briefDailyPromptError = "";

        const payload = await this.api(`/config`);
        const data = (payload && payload.data) || {};
        const briefs = data && data.briefs ? data.briefs : null;
        const p = briefs && typeof briefs === "object" ? briefs.daily_prompt : "";
        const text = typeof p === "string" ? p.trim() : "";
        this.briefDailyPrompt = text || this.briefDefaultDailyPrompt();
        this.briefDailyPromptLoaded = true;
      } catch (e) {
        this.briefDailyPromptError = e && e.message ? e.message : String(e);
        if (!String(this.briefDailyPrompt || "").trim()) this.briefDailyPrompt = this.briefDefaultDailyPrompt();
      }
    },

    resetBriefDailyPrompt() {
      this.briefDailyPromptError = "";
      this.briefDailyPrompt = this.briefDefaultDailyPrompt();
    },

    async saveBriefDailyPrompt() {
      try {
        this.briefDailyPromptSaving = true;
        this.briefDailyPromptError = "";
        const v = String(this.briefDailyPrompt || "").trim();
        if (!v) {
          this.briefDailyPromptError = "提示词不能为空";
          return;
        }
        await this.api(`/config/briefs`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ value: { daily_prompt: v } }),
        });
        this.briefDailyPromptLoaded = true;
        this.globalStatus = "已保存简报提示词";
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.briefDailyPromptError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.briefDailyPromptSaving = false;
      }
    },

    async loadSettings({ force = false } = {}) {
      await this.loadYtdlpCookies({ force });
      await this.loadYtdlpSubtitles({ force });
      await this.loadYtdlpFormat({ force });
    },

    async loadYtdlpCookies({ force = false } = {}) {
      try {
        if (this.ytdlpCookiesLoaded && !force) return;
        this.ytdlpCookiesError = "";

        const payload = await this.api(`/config`);
        const data = (payload && payload.data) || {};
        const cfg = data && data.ytdlp_cookies ? data.ytdlp_cookies : null;
        const t = cfg && typeof cfg === "object" ? cfg.text : "";
        this.ytdlpCookiesText = typeof t === "string" ? t : "";
        this.ytdlpCookiesLoaded = true;
      } catch (e) {
        this.ytdlpCookiesError = e && e.message ? e.message : String(e);
      }
    },

    async saveYtdlpCookies() {
      try {
        this.ytdlpCookiesSaving = true;
        this.ytdlpCookiesError = "";
        const v = String(this.ytdlpCookiesText || "");
        await this.api(`/config/ytdlp_cookies`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ value: { text: v } }),
        });
        this.ytdlpCookiesLoaded = true;
        this.globalStatus = "已保存 yt-dlp Cookies";
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.ytdlpCookiesError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.ytdlpCookiesSaving = false;
      }
    },

    async clearYtdlpCookies() {
      this.ytdlpCookiesText = "";
      await this.saveYtdlpCookies();
    },

    async loadYtdlpSubtitles({ force = false } = {}) {
      try {
        if (this.ytdlpSubtitlesLoaded && !force) return;
        this.ytdlpSubtitlesError = "";

        const payload = await this.api(`/config`);
        const data = (payload && payload.data) || {};
        const cfg = data && data.ytdlp_subtitles ? data.ytdlp_subtitles : null;
        const enabled = cfg && typeof cfg === "object" ? cfg.enabled : false;
        this.ytdlpSubtitlesEnabled = typeof enabled === "boolean" ? enabled : false;
        this.ytdlpSubtitlesLoaded = true;
      } catch (e) {
        this.ytdlpSubtitlesError = e && e.message ? e.message : String(e);
      }
    },

    async saveYtdlpSubtitles() {
      try {
        this.ytdlpSubtitlesSaving = true;
        this.ytdlpSubtitlesError = "";
        const enabled = !!this.ytdlpSubtitlesEnabled;
        await this.api(`/config/ytdlp_subtitles`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ value: { enabled } }),
        });
        this.ytdlpSubtitlesLoaded = true;
        this.globalStatus = "已保存字幕下载设置";
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.ytdlpSubtitlesError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.ytdlpSubtitlesSaving = false;
      }
    },

    ytdlpFormatEffectiveText() {
      const preset = String(this.ytdlpFormatPreset || "1080").trim();
      const custom = String(this.ytdlpFormatCustomText || "");
      if (preset === "custom") return custom;
      if (preset === "720") return YTDLP_FORMAT_PRESET_720;
      return YTDLP_FORMAT_PRESET_1080;
    },

    async loadYtdlpFormat({ force = false } = {}) {
      try {
        if (this.ytdlpFormatLoaded && !force) return;
        this.ytdlpFormatError = "";

        const payload = await this.api(`/config`);
        const data = (payload && payload.data) || {};
        const cfg = data && data.ytdlp_format ? data.ytdlp_format : null;
        const rawText = cfg && typeof cfg === "object" ? cfg.text : "";
        const text = typeof rawText === "string" ? rawText.trim() : "";
        const rawPreset = cfg && typeof cfg === "object" ? cfg.preset : "";
        const preset = typeof rawPreset === "string" ? rawPreset.trim() : "";

        if (preset === "1080" || preset === "720" || preset === "custom") {
          this.ytdlpFormatPreset = preset;
        } else if (text === YTDLP_FORMAT_PRESET_1080) {
          this.ytdlpFormatPreset = "1080";
        } else if (text === YTDLP_FORMAT_PRESET_720) {
          this.ytdlpFormatPreset = "720";
        } else if (text) {
          this.ytdlpFormatPreset = "custom";
        } else {
          this.ytdlpFormatPreset = "1080";
        }

        if (this.ytdlpFormatPreset === "custom") {
          this.ytdlpFormatCustomText = text;
        }

        this.ytdlpFormatLoaded = true;
      } catch (e) {
        this.ytdlpFormatError = e && e.message ? e.message : String(e);
      }
    },

    async saveYtdlpFormat() {
      try {
        this.ytdlpFormatSaving = true;
        this.ytdlpFormatError = "";
        const preset = String(this.ytdlpFormatPreset || "1080").trim();
        const custom = String(this.ytdlpFormatCustomText || "").trim();
        const text = preset === "custom" ? custom : this.ytdlpFormatEffectiveText().trim();

        if (preset === "custom" && !custom) {
          this.ytdlpFormatError = "自定义格式不能为空";
          return;
        }

        await this.api(`/config/ytdlp_format`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ value: { preset, text } }),
        });
        this.ytdlpFormatLoaded = true;
        this.globalStatus = "已保存 YTDLP_FORMAT";
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.ytdlpFormatError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.ytdlpFormatSaving = false;
      }
    },

    playlistListFiltered() {
      const q = String(this.playlistListQuery || "").trim().toLowerCase();
      const list = Array.isArray(this.playlistList) ? this.playlistList : [];
      if (!q) return list;
      return list.filter((p) => {
        const name = String((p && p.name) || "").toLowerCase();
        const desc = String((p && p.description) || "").toLowerCase();
        const id = String((p && p.id) || "").toLowerCase();
        return name.includes(q) || desc.includes(q) || id.includes(q);
      });
    },

    async _uploadPlaylistImage(playlistId, kind, file) {
      const pid = String(playlistId || "").trim();
      if (!pid) throw new Error("missing playlist id");
      const k = String(kind || "").trim();
      if (k !== "avatar" && k !== "background") throw new Error("invalid upload kind");
      if (!file) throw new Error("missing file");

      const fd = new FormData();
      fd.set("file", file, file.name || "image");
      const resp = await fetch(`/api/playlists/${encodeURIComponent(pid)}/${encodeURIComponent(k)}`, { method: "POST", body: fd });
      if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
      return resp.json();
    },

    openPlaylistPage(playlistId, dateStr) {
      const pid = String(playlistId || "").trim();
      if (!pid) return;
      this.modals.addMedia = false;
      this.modals.createPlaylist = false;
      this.closeVideoPlayer();
      this.playlistNameEditing = false;
      this.playlistNameDraft = "";
      this.playlistNameSaving = false;
      this.playlistDescEditing = false;
      this.playlistDescDraft = "";
      this.playlistPageId = pid;
      this.selectedPlaylistId = pid;
      this.playlistSelectedDate = String(dateStr || "").trim() || this._todayIsoLocal();
      this.playlistCalendarUpdateCount();
      this.switchView("playlist");
    },

    _dateAddDays(iso, days) {
      try {
        const d = new Date(`${iso}T00:00:00Z`);
        if (Number.isNaN(d.getTime())) return iso;
        d.setUTCDate(d.getUTCDate() + Number(days || 0));
        return d.toISOString().slice(0, 10);
      } catch {
        return iso;
      }
    },

    _dateDiffDays(aIso, bIso) {
      try {
        const a = new Date(`${aIso}T00:00:00Z`);
        const b = new Date(`${bIso}T00:00:00Z`);
        if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return 0;
        return Math.round((b.getTime() - a.getTime()) / (24 * 3600 * 1000));
      } catch {
        return 0;
      }
    },

    _dateClamp(iso, startIso, endIso) {
      try {
        const d = new Date(`${iso}T00:00:00Z`).getTime();
        const s = new Date(`${startIso}T00:00:00Z`).getTime();
        const e = new Date(`${endIso}T00:00:00Z`).getTime();
        if ([d, s, e].some((x) => Number.isNaN(x))) return iso;
        if (d < s) return startIso;
        if (d > e) return endIso;
        return iso;
      } catch {
        return iso;
      }
    },

    _todayIsoLocal() {
      try {
        const d = new Date();
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, "0");
        const day = String(d.getDate()).padStart(2, "0");
        return `${y}-${m}-${day}`;
      } catch {
        return new Date().toISOString().slice(0, 10);
      }
    },

    async loadPlaylistPage() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) {
        this.playlistDetail = null;
        this.playlistDayVideos = [];
        this.playlistBriefHtml = "";
        this.playlistNameEditing = false;
        this.playlistNameDraft = "";
        this.playlistNameSaving = false;
        this.playlistDescEditing = false;
        this.playlistDescDraft = "";
        this.pageTitle = "播放列表页";
        return;
      }

      try {
        this._syncUrl({ push: false });
        this.playlistDayVideosError = "";
        this.playlistBriefError = "";

        const detail = await this.api(`/playlists/${encodeURIComponent(pid)}/detail`);
        this.playlistDetail = detail || null;
        this.pageTitle = (detail && detail.name) || "播放列表页";

        this.playlistCalendarUpdateCount();
        try {
          if (this.$nextTick) this.$nextTick(() => this.playlistCalendarUpdateCount());
        } catch {
          // ignore
        }
        setTimeout(() => this.playlistCalendarUpdateCount(), 0);
        const today = this._todayIsoLocal();
        const start = (detail && detail.earliest_date) || today;
        const end = today;
        this.playlistTimelineStart = start;
        this.playlistTimelineEnd = end;
        const max = Math.max(0, this._dateDiffDays(start, end));
        this.playlistTimelineMax = max;

        if (!this.playlistSelectedDate) this.playlistSelectedDate = today;
        this.playlistSelectedDate = this._dateClamp(this.playlistSelectedDate, start, end);
        this.playlistTimelineValue = Math.max(0, Math.min(max, this._dateDiffDays(start, this.playlistSelectedDate)));

        this.playlistCalendarAnchor = this._dateClamp(
          this._dateAddDays(this.playlistSelectedDate, -(Number(this.playlistCalendarCount || 14) - 1)),
          start,
          end
        );
        this.playlistCalendarEnsureVisible();
        this.playlistEditResetFromDetail();
        await this.playlistLoadDay(this.playlistSelectedDate);
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    playlistRangeLabel() {
      const a = String(this.playlistTimelineStart || "").trim();
      const b = String(this.playlistTimelineEnd || "").trim();
      if (!a && !b) return "-";
      if (a && b) return `${a} ~ ${b}`;
      return a || b || "-";
    },

    playlistRangeLabelShort() {
      const a = String(this.playlistTimelineStart || "").trim();
      const b = String(this.playlistTimelineEnd || "").trim();
      if (!a && !b) return "-";
      if (!a || !b) return this._mdLabel(a || b);

      const parse = (iso) => {
        try {
          const parts = String(iso || "").split("-");
          if (parts.length !== 3) return null;
          const y = Number(parts[0]);
          const m = Number(parts[1]);
          const d = Number(parts[2]);
          if (![y, m, d].every((x) => Number.isFinite(x))) return null;
          return { y, m, d };
        } catch {
          return null;
        }
      };

      const pa = parse(a);
      const pb = parse(b);
      if (pa && pb && pa.y === pb.y) return `${pa.m}/${pa.d} ~ ${pb.m}/${pb.d}`;
      if (pa && pb) return `${String(pa.y).slice(-2)}/${pa.m}/${pa.d} ~ ${String(pb.y).slice(-2)}/${pb.m}/${pb.d}`;
      return `${this._mdLabel(a)} ~ ${this._mdLabel(b)}`;
    },

    playlistCalendarUpdateCount() {
      try {
        const refW =
          this.$refs && this.$refs.playlistCalendarStrip && this.$refs.playlistCalendarStrip.clientWidth
            ? Number(this.$refs.playlistCalendarStrip.clientWidth)
            : 0;
        const w = refW || (window && window.innerWidth ? Number(window.innerWidth) : 1280);
        const gap = 8; // gap-2
        const minCard = 64;
        let n = Math.floor((w + gap) / (minCard + gap));
        if (!Number.isFinite(n) || n <= 0) n = 14;
        n = Math.max(5, Math.min(14, Math.floor(n)));
        if (n !== this.playlistCalendarCount) {
          this.playlistCalendarCount = n;
          this.playlistCalendarEnsureVisible();
        }
      } catch {
        // ignore
      }
    },

    _weekdayZh(iso) {
      try {
        const d = new Date(`${iso}T00:00:00Z`);
        if (Number.isNaN(d.getTime())) return "";
        const map = ["日", "一", "二", "三", "四", "五", "六"];
        return map[d.getUTCDay()] || "";
      } catch {
        return "";
      }
    },

    _mdLabel(iso) {
      try {
        const d = new Date(`${iso}T00:00:00Z`);
        if (Number.isNaN(d.getTime())) return iso;
        const mm = String(d.getUTCMonth() + 1);
        const dd = String(d.getUTCDate());
        return `${mm}/${dd}`;
      } catch {
        return iso;
      }
    },

    playlistCalendarEnsureVisible() {
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      const selected = String(this.playlistSelectedDate || "").trim();
      const n = Math.max(5, Number(this.playlistCalendarCount || 14));
      if (!start || !end || !selected) return;
      const maxAnchor = this._dateAddDays(end, -(n - 1));
      let anchor = String(this.playlistCalendarAnchor || "").trim();
      if (!anchor) anchor = this._dateAddDays(selected, -(n - 1));
      // Ensure selected within window
      const windowEnd = this._dateAddDays(anchor, n - 1);
      if (this._dateDiffDays(selected, anchor) > 0) {
        // selected < anchor
        anchor = selected;
      } else if (this._dateDiffDays(windowEnd, selected) > 0) {
        // selected > windowEnd
        anchor = this._dateAddDays(selected, -(n - 1));
      }
      // Clamp to range
      if (this._dateDiffDays(maxAnchor, start) > 0) {
        anchor = start;
      } else {
        anchor = this._dateClamp(anchor, start, maxAnchor);
      }
      this.playlistCalendarAnchor = anchor;
    },

    playlistCalendarItems() {
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      const selected = String(this.playlistSelectedDate || "").trim();
      const n = Math.max(5, Number(this.playlistCalendarCount || 14));
      if (!start || !end) return [];
      const today = end || this._todayIsoLocal();
      let anchor = String(this.playlistCalendarAnchor || "").trim();
      if (!anchor) anchor = selected ? this._dateAddDays(selected, -(n - 1)) : start;
      const out = [];
      for (let i = 0; i < n; i++) {
        const date = this._dateAddDays(anchor, i);
        const disabled = this._dateDiffDays(date, start) > 0 || this._dateDiffDays(end, date) > 0;
        out.push({
          date,
          md: this._mdLabel(date),
          weekday: this._weekdayZh(date),
          selected: !!selected && date === selected,
          today: date === today,
          disabled,
        });
      }
      return out;
    },

    playlistTimelineApply() {
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      if (!start || !end) return;
      const v = Number(this.playlistTimelineValue || 0);
      const next = this._dateAddDays(start, v);
      this.playlistSetDate(this._dateClamp(next, start, end));
    },

    playlistSetDate(iso) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (!pid) return;
      const next = String(iso || "").trim();
      if (!next || next === this.playlistSelectedDate) return;
      this.playlistSelectedDate = next;
      this.playlistCalendarEnsureVisible();
      if (this.playlistTimelineStart) {
        const max = Number(this.playlistTimelineMax || 0);
        this.playlistTimelineValue = Math.max(0, Math.min(max, this._dateDiffDays(this.playlistTimelineStart, next)));
      }
      this._syncUrl({ push: false });
      this.playlistLoadDay(next);
    },

    async playlistLoadDay(iso) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const day = String(iso || "").trim();
      if (!pid || !day) return;

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
        const items = await this.api(`/playlists/${encodeURIComponent(pid)}/videos_by_date?date=${encodeURIComponent(day)}`);
        this.playlistDayVideos = Array.isArray(items) ? items : [];
        const keep = prevCurrentId ? this.playlistDayVideos.find((x) => x && String(x.id) === prevCurrentId) : null;
        if (keep) this.playlistCurrentVideo = keep;
        else this.playlistCurrentVideo = this.playlistDayVideos.length ? this.playlistDayVideos[0] : null;
        if (this.playlistCurrentVideo) await this.playlistSelectVideo(this.playlistCurrentVideo, { autoPlay: false });
      } catch (e) {
        this.playlistDayVideosError = e && e.message ? e.message : String(e);
        this.playlistDayVideos = [];
        this.playlistCurrentVideo = null;
        this.playlistTranscriptText = "";
        this.playlistTranscriptLoading = false;
        this.playlistTranscriptError = "";
        this.playlistTranscriptLanguage = "";
        this.playlistTranscriptSource = "";
      } finally {
        this.playlistDayVideosLoading = false;
      }

      await this.playlistLoadBrief(day);
    },

    async playlistSelectVideo(v, { autoPlay = false } = {}) {
      if (!v) return;
      const vid = String(v.id || "").trim();
      if (!vid) return;
      this.playlistCurrentVideo = v;
      this.playlistPlayerError = "";
      const selectingId = vid;
      this.playlistMediaDurationSec = 0;
      this.playlistMediaCurrentTimeSec = 0;
      this.playlistMediaPlaying = false;
      this.playlistTranscriptText = "";
      this.playlistTranscriptLoading = true;
      this.playlistTranscriptError = "";
      this.playlistTranscriptLanguage = "";
      this.playlistTranscriptSource = "";
      this.playlistTranscriptUpdatedAt = "";
      try {
        const assets = await this.api(`/videos/${encodeURIComponent(vid)}/assets?presign=1&download=0`);
        const list = Array.isArray(assets) ? assets : [];
        const videos = list.filter((a) => a && a.type === "video" && a.presigned_url);
        const audios = list.filter((a) => a && a.type === "audio" && a.presigned_url);
        const mp4 = videos.find((a) => String(a.format || "").toLowerCase() === "mp4") || videos[0] || null;
        const m4a = audios.find((a) => String(a.format || "").toLowerCase() === "m4a") || audios[0] || null;
        this.playlistPlayerVideoUrl = (mp4 && mp4.presigned_url) || "";
        this.playlistPlayerAudioUrl = (m4a && m4a.presigned_url) || "";
        this.$nextTick(() => {
          try {
            const el = this.playlistAudioOnly ? this.$refs && this.$refs.playlistAudioEl : this.$refs && this.$refs.playlistVideoEl;
            if (autoPlay && el && typeof el.play === "function") el.play();
          } catch {
            // ignore
          }
          this.playlistSyncMediaState();
        });

        try {
          const transcript = await this.api(`/videos/${encodeURIComponent(vid)}/transcript`);
          if (!this.playlistCurrentVideo || String(this.playlistCurrentVideo.id || "") !== String(selectingId)) return;
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
        } catch (e2) {
          if (!this.playlistCurrentVideo || String(this.playlistCurrentVideo.id || "") !== String(selectingId)) return;
          this.playlistTranscriptError = e2 && e2.message ? e2.message : String(e2);
        } finally {
          if (!this.playlistCurrentVideo || String(this.playlistCurrentVideo.id || "") !== String(selectingId)) return;
          this.playlistTranscriptLoading = false;
        }
      } catch (e) {
        this.playlistPlayerError = e && e.message ? e.message : String(e);
        this.playlistPlayerVideoUrl = "";
        this.playlistPlayerAudioUrl = "";
        if (this.playlistCurrentVideo && String(this.playlistCurrentVideo.id || "") === String(selectingId)) {
          this.playlistTranscriptLoading = false;
        }
      }
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
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      if (!start || !end || !this.playlistSelectedDate) return;
      const next = this._dateClamp(this._dateAddDays(this.playlistSelectedDate, -1), start, end);
      this.playlistSetDate(next);
    },

    playlistNextDay() {
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      if (!start || !end || !this.playlistSelectedDate) return;
      const next = this._dateClamp(this._dateAddDays(this.playlistSelectedDate, 1), start, end);
      this.playlistSetDate(next);
    },

    playlistJump(days) {
      const start = String(this.playlistTimelineStart || "").trim();
      const end = String(this.playlistTimelineEnd || "").trim();
      if (!start || !end || !this.playlistSelectedDate) return;
      try {
        const anchor = String(this.playlistCalendarAnchor || "").trim();
        if (anchor) this.playlistCalendarAnchor = this._dateAddDays(anchor, Number(days || 0));
      } catch {
        // ignore
      }
      const next = this._dateClamp(this._dateAddDays(this.playlistSelectedDate, Number(days || 0)), start, end);
      this.playlistSetDate(next);
    },

    _playlistBriefKey(pid, day) {
      const p = String(pid || "").trim();
      const d = String(day || "").trim();
      if (!p || !d) return "";
      return `${p}:${d}`;
    },

    playlistBriefGeneratingForSelectedDate() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const day = String(this.playlistSelectedDate || "").trim();
      const k = this._playlistBriefKey(pid, day);
      return !!k && String(this.playlistBriefGeneratingKey || "") === k;
    },

    async playlistGenerateBriefForSelectedDate() {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const day = String(this.playlistSelectedDate || "").trim();
      if (!pid || !day) return;
      const k = this._playlistBriefKey(pid, day);
      if (k) this.playlistBriefGeneratingKey = k;
      this.playlistBriefError = "";
      this.playlistBriefHtml = "";
      try {
        await this.api(`/briefs/generate`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ playlist_id: pid, date: day }),
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
      const detail = this.playlistDetail;
      const from = detail && detail.earliest_date ? String(detail.earliest_date) : "";
      const to = this._todayIsoLocal();
      if (!pid || !from) {
        const msg = "没有可用的日期范围（可能还没同步出视频）";
        this.toastError(msg);
        this.globalStatus = msg;
        return;
      }
      try {
        await this.api(`/briefs/generate_range`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ playlist_id: pid, from_date: from, to_date: to }),
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

      const briefUrlLabel = (url) => {
        try {
          const u = String(url || "").trim();
          if (!u) return "视频...";
          const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
          const hit = items.find((v) => v && String(v.url || "").trim() === u);
          const title = hit && hit.title ? String(hit.title).trim() : "";
          if (!title) return "视频...";
          const head = Array.from(title).slice(0, 4).join("");
          return `${head}...`;
        } catch {
          return "视频...";
        }
      };

      const briefRefPill = ({ label, url }) => {
        const u = String(url || "").trim();
        if (!u) return "";
        const enc = encodeURIComponent(u);
        const safeUrl = this._escapeHtml(u);
        const text = String(label || "").trim() || briefUrlLabel(u);
        const safeText = formatInlineEsc(this._escapeHtml(text));
        return [
          '<span class="inline-flex items-stretch rounded-full border border-slate-700 bg-slate-950/30 overflow-hidden align-middle ml-1 mr-1">',
          `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" title="${safeUrl}" class="min-w-0 max-w-xs px-3 py-0.5 text-[11px] text-slate-200 hover:bg-slate-800/60 truncate no-underline">${safeText}</a>`,
          `<button type="button" class="shrink-0 px-2.5 py-0.5 border-l border-slate-700 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20 text-[11px]" data-play-url="${enc}" title="播放该视频">▶</button>`,
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
            out.push('<ul class="list-disc pl-5 space-y-1">');
            inList = true;
          }
          const body = linkifyAndFormat(trimmed.slice(2));
          out.push(`<li>${body}</li>`);
          continue;
        }
        flushList();
        out.push(`<p class="my-1">${linkifyAndFormat(line)}</p>`);
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

    playlistSelectVideoByUrl(url) {
      const u = String(url || "").trim();
      if (!u) return;
      const items = Array.isArray(this.playlistDayVideos) ? this.playlistDayVideos : [];
      const found = items.find((v) => v && String(v.url || "").trim() === u);
      if (found) {
        this.playlistSelectVideo(found, { autoPlay: true });
        return;
      }
      this.globalStatus = "该链接不在当天视频列表中";
    },

    async playlistLoadBrief(day) {
      const pid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      const d = String(day || "").trim();
      if (!pid || !d) return;
      const k = this._playlistBriefKey(pid, d);
      const manualGenerating = !!k && String(this.playlistBriefGeneratingKey || "") === k;
      this.playlistBriefLoading = true;
      this.playlistBriefError = "";
      this.playlistBriefHtml = "";
      const hasVideos = Array.isArray(this.playlistDayVideos) && this.playlistDayVideos.length > 0;
      try {
        const brief = await this.api(`/briefs/by_date?playlist_id=${encodeURIComponent(pid)}&date=${encodeURIComponent(d)}`);
        if (!brief || brief.status !== "ready" || !brief.markdown_url) {
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
            this._playlistEnsureBriefEnqueued(pid, d);
            this._playlistPollBrief(pid, d);
          }
          return;
        }
        const resp = await fetch(brief.markdown_url);
        if (!resp.ok) throw new Error(`${resp.status}: brief markdown fetch failed`);
        const md = await resp.text();
        this.playlistBriefHtml = this._briefToHtml(md);
        if (manualGenerating) this.playlistBriefGeneratingKey = "";
        try {
          const k = `${String(pid)}:${String(d)}`;
          if (this.playlistBriefAutoPoll) this.playlistBriefAutoPoll.delete(k);
        } catch {}
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        if (String(msg).startsWith("404:") || String(msg).includes(" 404")) {
          if (!hasVideos) {
            this.playlistBriefHtml = `<div class="text-slate-400 text-sm">当天暂无视频，不生成简报</div>`;
            if (manualGenerating) this.playlistBriefGeneratingKey = "";
            return;
          }
          this.playlistBriefHtml = manualGenerating
            ? `<div class="text-slate-400 text-sm">生成中…</div>`
            : `<div class="text-slate-400 text-sm">暂无简报，已自动触发生成…</div>`;
          this._playlistEnsureBriefEnqueued(pid, d);
          this._playlistPollBrief(pid, d);
        } else {
          this.playlistBriefError = msg;
        }
      } finally {
        this.playlistBriefLoading = false;
      }
    },

    _playlistEnsureBriefEnqueued(pid, day) {
      try {
        const k = `${String(pid)}:${String(day)}`;
        if (this.playlistBriefAutoRequests && this.playlistBriefAutoRequests.has(k)) return;
        if (!this.playlistBriefAutoRequests) this.playlistBriefAutoRequests = new Set();
        this.playlistBriefAutoRequests.add(k);
        this.api(`/briefs/generate`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ playlist_id: pid, date: day }),
        }).catch(() => {});
      } catch {
        // ignore
      }
    },

    _playlistPollBrief(pid, day) {
      try {
        const k = `${String(pid)}:${String(day)}`;
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
        if (this.playlistDetail) this.playlistDetail.avatar_url = updated.avatar_url || this.playlistDetail.avatar_url;
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
        if (this.playlistDetail) this.playlistDetail.background_url = updated.background_url || this.playlistDetail.background_url;
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
    },

    playlistEditSelectedMedia() {
      const ids = Array.isArray(this.playlistEditMediaIds) ? this.playlistEditMediaIds : [];
      if (!ids.length) return [];
      const idx = new Map((this.mediaIndex || []).map((m) => [String(m.id), m]));
      return ids.map((id) => idx.get(String(id))).filter(Boolean);
    },

    playlistEditFilteredMediaOptions() {
      const q = String(this.playlistEditMediaTagQuery || "")
        .trim()
        .toLowerCase();
      const selected = new Set(Array.isArray(this.playlistEditMediaIds) ? this.playlistEditMediaIds : []);
      let items = Array.isArray(this.mediaIndex) ? this.mediaIndex : [];
      items = items.filter((m) => m && !selected.has(String(m.id)));
      if (q) {
        items = items.filter((m) => {
          const name = String(this.mediaDisplayName(m) || "").toLowerCase();
          const prov = String(m.provider || "").toLowerCase();
          return name.includes(q) || prov.includes(q);
        });
      }
      return items.slice(0, 50);
    },

    playlistEditAddMediaTag(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      if (!Array.isArray(this.playlistEditMediaIds)) this.playlistEditMediaIds = [];
      if (!this.playlistEditMediaIds.includes(id)) this.playlistEditMediaIds.push(id);
      this.playlistEditMediaTagQuery = "";
      this.playlistEditMediaTagOpen = false;
    },

    playlistEditAddFirstFilteredMediaTag() {
      const items = this.playlistEditFilteredMediaOptions();
      if (!items.length) return;
      this.playlistEditAddMediaTag(items[0].id);
    },

    playlistEditRemoveMediaTag(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      this.playlistEditMediaIds = (Array.isArray(this.playlistEditMediaIds) ? this.playlistEditMediaIds : []).filter(
        (x) => String(x) !== id
      );
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
        await this.loadPlaylistPage();
        await this.loadPlaylists();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    openAddMedia() {
      this.modals.createPlaylist = false;
      this.closeVideoPlayer();
      this.addMediaUrl = "";
      this.addMediaError = "";
      this.addMediaSubmitting = false;
      this.modals.addMedia = true;
    },

    async submitAddMedia() {
      if (this.addMediaSubmitting) return;
      const url = (this.addMediaUrl || "").trim();
      if (!url) {
        this.addMediaError = "请填写媒体 URL";
        return;
      }
      try {
        this.addMediaSubmitting = true;
        this.addMediaError = "";
        await this.api(`/media`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ url }),
        });
        this.modals.addMedia = false;
        if (this.activeView !== "media") this.switchView("media");
        await this.loadMedia();
        this.mediaIndex = await this.api(`/media?limit=500&offset=0`);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.addMediaError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.addMediaSubmitting = false;
      }
    },

    async setMediaMonitor(mediaId, enabled) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      const next = !!enabled;

      const all = Array.isArray(this.mediaList) ? this.mediaList : [];
      const item = all.find((m) => m && String(m.id) === id);
      const prev = item ? item.monitor_enabled !== false : true;
      if (item) item.monitor_enabled = next;
      const idx = Array.isArray(this.mediaIndex) ? this.mediaIndex : [];
      const idxItem = idx.find((m) => m && String(m.id) === id);
      if (idxItem) idxItem.monitor_enabled = next;

      try {
        await this.api(`/media/${id}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ monitor_enabled: next }),
        });
        this.globalStatus = next ? "已启用监控" : "已关闭监控";
      } catch (e) {
        if (item) item.monitor_enabled = prev;
        if (idxItem) idxItem.monitor_enabled = prev;
        this.globalStatus = `error: ${e.message}`;
      }
    },

    async _syncMedia(mediaId, scope) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      const qs = scope ? `?scope=${encodeURIComponent(scope)}` : "";
      await this.api(`/media/${id}/sync${qs}`, { method: "POST" });
      const msg = scope === "all" ? "已投递全量同步任务" : "已投递近期同步任务";
      this.globalStatus = msg;
      this.toastSuccess(msg, { action: this.toastJobsAction() });
      await this.loadJobs();
    },

    async syncMediaRecent(mediaId) {
      try {
        await this._syncMedia(mediaId, "recent");
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.toastError(`同步任务提交失败：${msg}`, { action: this.toastJobsAction() });
        this.globalStatus = `error: ${msg}`;
      }
    },

    async syncMediaAll(mediaId) {
      try {
        await this._syncMedia(mediaId, "all");
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.toastError(`同步任务提交失败：${msg}`, { action: this.toastJobsAction() });
        this.globalStatus = `error: ${msg}`;
      }
    },

    async syncMedia(mediaId) {
      return await this.syncMediaRecent(mediaId);
    },

    async deleteMedia(mediaId) {
      try {
        await this.api(`/media/${mediaId}`, { method: "DELETE" });
        await this.loadMedia();
        this.mediaIndex = await this.api(`/media?limit=500&offset=0`);
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },


    async retryJob(jobId) {
      const id = String(jobId || "").trim();
      if (!id) return;
      if (this.jobActionInFlight && this.jobActionInFlight[id]) return;

      const done = Array.isArray(this.jobListDone) ? this.jobListDone : [];
      const idx = done.findIndex((j) => j && String(j.id) === id);
      const snapshot = idx >= 0 ? done[idx] : null;
      const jobType = snapshot && snapshot.type ? String(snapshot.type) : null;

      if (!this.jobActionInFlight) this.jobActionInFlight = {};
      this.jobActionInFlight[id] = "retry";

      // Optimistic UX:
      // - hide the failed job from the current list immediately
      // - bump the active count by +1 immediately
      if (!this.jobsHiddenDoneIds) this.jobsHiddenDoneIds = {};
      this.jobsHiddenDoneIds[id] = Date.now();
      if (idx >= 0) this.jobListDone = done.filter((j) => j && String(j.id) !== id);
      this._addJobsOptimisticActive(`retry:${id}`, { type: jobType });

      try {
        this.globalStatus = "正在投递重试…";
        await this.api(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" });
        this.globalStatus = "已投递重试任务";
        // Refresh done list/series if we're on done tabs, without blocking active count.
        if (this.activeView === "jobs" && this.jobsTab !== "active") {
          this.refreshJobs();
        }
      } catch (e) {
        // Roll back optimistic changes.
        try {
          if (this.jobsHiddenDoneIds) delete this.jobsHiddenDoneIds[id];
        } catch {
          // ignore
        }
        this._removeJobsOptimisticActive(`retry:${id}`);
        if (snapshot) {
          const cur = Array.isArray(this.jobListDone) ? this.jobListDone : [];
          const insertAt = Math.max(0, Math.min(idx, cur.length));
          this.jobListDone = cur.slice(0, insertAt).concat([snapshot]).concat(cur.slice(insertAt));
        }
        this.globalStatus = `error: ${e.message}`;
      } finally {
        try {
          if (this.jobActionInFlight) delete this.jobActionInFlight[id];
        } catch {
          // ignore
        }
      }
    },

    async cancelJob(jobId) {
      const id = String(jobId || "").trim();
      if (!id) return;
      if (this.jobActionInFlight && this.jobActionInFlight[id]) return;

      if (!this.jobActionInFlight) this.jobActionInFlight = {};
      this.jobActionInFlight[id] = "cancel";

      // Optimistic: hide from the active list immediately so the count updates.
      if (!this.jobsHiddenActiveIds) this.jobsHiddenActiveIds = {};
      this.jobsHiddenActiveIds[id] = Date.now();

      try {
        this.globalStatus = "正在取消…";
        await this.api(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
        this.globalStatus = "已取消任务";
      } catch (e) {
        try {
          if (this.jobsHiddenActiveIds) delete this.jobsHiddenActiveIds[id];
        } catch {
          // ignore
        }
        this.globalStatus = `error: ${e.message}`;
      } finally {
        try {
          if (this.jobActionInFlight) delete this.jobActionInFlight[id];
        } catch {
          // ignore
        }
      }
    },

    openCreatePlaylist() {
      this.modals.addMedia = false;
      this.closeVideoPlayer();
      this.createPlaylistName = "";
      this.createPlaylistDesc = "";
      this.createPlaylistMediaIds = [];
      this.createPlaylistMediaTagQuery = "";
      this.createPlaylistMediaTagOpen = false;
      this.createPlaylistAvatarFile = null;
      this.createPlaylistBackgroundFile = null;
      if (!this.mediaIndex || this.mediaIndex.length === 0) {
        this.api(`/media?limit=500&offset=0`)
          .then((items) => {
            this.mediaIndex = Array.isArray(items) ? items : [];
          })
          .catch(() => {});
      }
      this.modals.createPlaylist = true;
    },

    async submitCreatePlaylist() {
      try {
        const name = String(this.createPlaylistName || "").trim();
        if (!name) {
          this.globalStatus = "请填写播放列表名称";
          return;
        }
        const created = await this.api(`/playlists`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            name,
            description: (this.createPlaylistDesc || "").trim() || null,
            media_ids: Array.isArray(this.createPlaylistMediaIds) ? this.createPlaylistMediaIds : [],
          }),
        });

        const pid = created && created.id ? String(created.id) : "";
        const maxBytes = 2 * 1024 * 1024;
        if (pid && this.createPlaylistAvatarFile) {
          if (this.createPlaylistAvatarFile.size > maxBytes) throw new Error("头像超过 2MB");
          await this._uploadPlaylistImage(pid, "avatar", this.createPlaylistAvatarFile);
        }
        if (pid && this.createPlaylistBackgroundFile) {
          if (this.createPlaylistBackgroundFile.size > maxBytes) throw new Error("背景超过 2MB");
          await this._uploadPlaylistImage(pid, "background", this.createPlaylistBackgroundFile);
        }

        this.modals.createPlaylist = false;
        await this.loadPlaylists();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    async deletePlaylist(id) {
      try {
        await this.api(`/playlists/${id}`, { method: "DELETE" });
        if (this.selectedPlaylistId === id) this.selectedPlaylistId = null;
        await this.loadPlaylists();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    selectPlaylist(id) {
      this.selectedPlaylistId = id;
      this._syncUrl({ push: false });
      this.globalStatus = `已选择播放列表：${id}`;
    },

    async init() {
      try {
        this._applySidebarMode();

        const onResize = () => {
          this.playlistCalendarUpdateCount();
          this._applySidebarMode();
        };
        window.addEventListener("resize", onResize, { passive: true });
        window.addEventListener("orientationchange", onResize, { passive: true });
        window.addEventListener("popstate", () => {
          const viewKey = this._parseViewFromLocation();
          if (this.activeView === "videos" && viewKey !== "videos") this._teardownVideoIo();
          if (this.activeView === "jobs" && viewKey !== "jobs") this._destroyJobsDoneChart();
          this.activeView = viewKey;
          const item = this.navItems.find((x) => x.key === viewKey);
          this.pageTitle = item ? item.label : viewKey;
          this._applyQueryFromLocation(viewKey);
          this.refreshActive();
        });

        const initialView = this._parseViewFromLocation();
        this.activeView = initialView;
        const item = this.navItems.find((x) => x.key === initialView);
        this.pageTitle = item ? item.label : initialView;
        this._applyQueryFromLocation(initialView);
        this._syncUrl({ push: false });

        const h = await this.api(`/health`);
        this.healthOk = !!h.ok;
        this.services.db = h.db || this.services.db;
        this.services.s3 = h.s3 || this.services.s3;
        this.services.asr = h.asr || this.services.asr;
        this.services.llm = h.llm || this.services.llm;
        this.globalStatus = h.deps_ok ? "" : "部分依赖不可用";
        this.mediaIndex = await this.api(`/media?limit=500&offset=0`);
        await this.refreshActive();
        // Always load stats for overview cards even if user lands on other views.
        await this.loadStats();
      } catch (e) {
        this.healthOk = false;
        this.globalStatus = `error: ${e.message}`;
      }
    },
  };
}
