import { todayIsoLocal } from "../shared/playlist-periods.js";

export function createUrlStateMethods({ settingsTabs }) {
  return {
    _viewPath(key) {
      if (!key || key === "overview") return "/";
      return `/${encodeURIComponent(key)}`;
    },

    _parseViewFromLocation() {
      const path = (window.location.pathname || "/").replace(/\/+$/, "") || "/";
      if (path === "/" || path === "") return "overview";
      const key = decodeURIComponent(path.slice(1));
      if (key === "install") return "settings";
      const exists = this.navItems.some((item) => item.key === key);
      return exists ? key : "overview";
    },

    _applyQueryFromLocation(viewKey) {
      const searchParams = new URLSearchParams(window.location.search || "");
      if (viewKey === "media") {
        this.mediaQuery = searchParams.get("q") || "";
      }
      if (viewKey === "videos") {
        this.videoStatus = searchParams.get("status") || "";
        const mediaIn = searchParams.get("media_id_in") || "";
        const mediaOne = searchParams.get("media_id") || "";
        if (mediaIn) {
          this.videoMediaIds = mediaIn.split(",").map((item) => item.trim()).filter(Boolean);
        } else if (mediaOne) {
          this.videoMediaIds = [mediaOne];
        } else {
          this.videoMediaIds = [];
        }
        this.videoQuery = searchParams.get("q") || "";
        const fromIso = searchParams.get("from") || "";
        const toIso = searchParams.get("to") || "";
        if (fromIso) this.videoFrom = this._toLocalInputValue(new Date(fromIso));
        if (toIso) this.videoTo = this._toLocalInputValue(new Date(toIso));
        if (!fromIso && !toIso) this._ensureVideoRange();
      }
      if (viewKey === "video") {
        this.playerPageVideoId = searchParams.get("video_id") || "";
      }
      if (viewKey === "jobs") {
        this.jobsTab = searchParams.get("tab") || this.jobsTab || "active";
        this.jobsTypeFilter = searchParams.get("type") || this.jobsTypeFilter || "";
        const fromIso = searchParams.get("from") || "";
        const toIso = searchParams.get("to") || "";
        if (fromIso) this.jobsDoneFrom = this._toLocalInputValue(new Date(fromIso));
        if (toIso) this.jobsDoneTo = this._toLocalInputValue(new Date(toIso));
      }
      if (viewKey === "playlists") {
        this.selectedPlaylistId = searchParams.get("playlist_id") || this.selectedPlaylistId;
      }
      if (viewKey === "playlist") {
        const playlistId = searchParams.get("playlist_id") || this.playlistPageId || this.selectedPlaylistId;
        this.playlistPageId = playlistId || null;
        this.selectedPlaylistId = playlistId || this.selectedPlaylistId;
        this.playlistSelectedDate = searchParams.get("date") || this.playlistSelectedDate || "";
        if (!this.playlistSelectedDate) this.playlistSelectedDate = todayIsoLocal();
        const subview = (searchParams.get("subview") || this.playlistSubview || "main").trim();
        this.playlistSubview = ["main", "analysis", "settings"].includes(subview) ? subview : "main";
      }
      if (viewKey === "settings") {
        const legacyInstallPath = ((window.location.pathname || "").replace(/\/+$/, "") || "/") === "/install";
        const rawTab = (legacyInstallPath ? "install" : (searchParams.get("tab") || this.settingsTab || "cookies")).trim();
        const legacyDownloadTabs = ["subtitles", "members", "format"];
        const tab = legacyDownloadTabs.includes(rawTab) ? "download" : rawTab;
        this.settingsTab = settingsTabs.includes(tab) ? tab : "cookies";
        if (this.settingsTab === "cookies") {
          const cookieTab = (searchParams.get("cookie_tab") || this.settingsCookiesTab || "youtube").trim().toLowerCase();
          this.settingsCookiesTab = cookieTab === "bilibili" ? "bilibili" : "youtube";
        }
      }
    },

    _buildSearchForView(viewKey) {
      const searchParams = new URLSearchParams();
      if (viewKey === "media" && this.mediaQuery) searchParams.set("q", this.mediaQuery);
      if (viewKey === "videos" && this.videoStatus) searchParams.set("status", this.videoStatus);
      if (viewKey === "videos" && Array.isArray(this.videoMediaIds) && this.videoMediaIds.length) {
        searchParams.set("media_id_in", this.videoMediaIds.join(","));
      }
      if (viewKey === "videos" && this.videoQuery) searchParams.set("q", this.videoQuery);
      if (viewKey === "videos" && this.videoFrom) searchParams.set("from", new Date(this.videoFrom).toISOString());
      if (viewKey === "videos" && this.videoTo) searchParams.set("to", new Date(this.videoTo).toISOString());
      if (viewKey === "video" && this.playerPageVideoId) searchParams.set("video_id", String(this.playerPageVideoId));
      if (viewKey === "jobs") {
        searchParams.set("tab", this.jobsTab || "active");
        if (this.jobsTypeFilter) searchParams.set("type", this.jobsTypeFilter);
        if (this.jobsTab !== "active") {
          const fromIso = this.jobsDoneFrom ? new Date(this.jobsDoneFrom).toISOString() : "";
          const toIso = this.jobsDoneTo ? new Date(this.jobsDoneTo).toISOString() : "";
          if (fromIso) searchParams.set("from", fromIso);
          if (toIso) searchParams.set("to", toIso);
        }
      }
      if (viewKey === "playlists" && this.selectedPlaylistId) searchParams.set("playlist_id", this.selectedPlaylistId);
      if (viewKey === "playlist") {
        const playlistId = this.playlistPageId || this.selectedPlaylistId;
        if (playlistId) searchParams.set("playlist_id", String(playlistId));
        if (this.playlistSelectedDate) searchParams.set("date", String(this.playlistSelectedDate));
        searchParams.set("subview", this.playlistSubview || "main");
      }
      if (viewKey === "settings") {
        searchParams.set("tab", this.settingsTab || "cookies");
        if ((this.settingsTab || "cookies") === "cookies") {
          searchParams.set("cookie_tab", this.settingsCookiesTab || "youtube");
        }
      }
      const search = searchParams.toString();
      return search ? `?${search}` : "";
    },

    _syncUrl({ push = false, stateExtras = null } = {}) {
      const path = this._viewPath(this.activeView);
      const search = this._buildSearchForView(this.activeView);
      const url = `${path}${search}`;
      const baseState = !push && history.state && typeof history.state === "object" ? history.state : {};
      const state = { ...baseState, view: this.activeView };
      if (stateExtras && typeof stateExtras === "object") Object.assign(state, stateExtras);
      if (push) history.pushState(state, "", url);
      else history.replaceState(state, "", url);
    },

    switchView(key, { push = true, stateExtras = null, refresh = true } = {}) {
      const item = this.navItems.find((nav) => nav.key === key);
      if (this.activeView === "media" && key !== "media") this._teardownMediaIo();
      if (this.activeView === "videos" && key !== "videos") this._teardownVideoIo();
      if (this.activeView === "video" && key !== "video" && typeof this.leaveVideoPage === "function") this.leaveVideoPage();
      if (this.activeView === "playlist" && key !== "playlist" && typeof this.leavePlaylistPage === "function") {
        this.leavePlaylistPage();
      }
      if (this.activeView !== key && typeof this._stopDocumentMediaPlayback === "function") {
        this._stopDocumentMediaPlayback({ clearSources: true });
      }
      if (key === "videos") this._ensureVideoRange();
      this.activeView = key;
      this.pageTitle = item ? item.label : key;
      this._syncUrl({ push, stateExtras });
      if (refresh) this.refreshActive();
    },

    setSettingsTab(tab) {
      const next = String(tab || "").trim();
      this.settingsTab = settingsTabs.includes(next) ? next : "cookies";
      if (this.settingsTab !== "cookies") this.settingsCookiesTab = "youtube";
      this._syncUrl({ push: false });
    },

    setSettingsCookiesTab(tab) {
      const next = String(tab || "").trim().toLowerCase();
      this.settingsCookiesTab = next === "bilibili" ? "bilibili" : "youtube";
      this._syncUrl({ push: false });
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
        if (this.activeView === "video") return await this.loadVideoPage();
        if (this.activeView === "jobs") return await this.loadJobs();
        if (this.activeView === "playlists") return await this.loadPlaylists();
        if (this.activeView === "playlist") return await this.loadPlaylistPage();
        if (this.activeView === "settings") return await this.loadSettings();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },
  };
}
