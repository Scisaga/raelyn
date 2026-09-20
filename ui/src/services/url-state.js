import { todayIsoLocal } from "../shared/playlist-periods.js";

export function createUrlStateMethods({ settingsTabs }) {
  return {
    _viewPath(key) {
      if (!key || key === "field") return "/";
      if (key === "playlist") {
        const domainId = String(this.selectedPlaylistId || this.playlistPageId || "").trim();
        return domainId ? `/domains/${encodeURIComponent(domainId)}/playlist` : "/playlist";
      }
      return `/${encodeURIComponent(key)}`;
    },

    _parseViewFromLocation() {
      const path = (window.location.pathname || "/").replace(/\/+$/, "") || "/";
      if (path === "/" || path === "") return "field";
      if (/^\/domains\/[^/]+\/playlist$/.test(path)) return "playlist";
      if (path === "/briefs") return "playlist";
      const key = decodeURIComponent(path.slice(1));
      if (key === "install") return "settings";
      const exists = this.navItems.some((item) => item.key === key);
      return exists ? key : "field";
    },

    _applyQueryFromLocation(viewKey) {
      const searchParams = new URLSearchParams(window.location.search || "");
      if (["field", "domains", "stories", "briefs", "playlist", "library", "domain-settings", "operations"].includes(viewKey)) {
        const domainPathMatch = (window.location.pathname || "").match(/^\/domains\/([^/]+)\/playlist\/?$/);
        const domainId = (domainPathMatch ? decodeURIComponent(domainPathMatch[1]) : "") || searchParams.get("domain_id") || this.selectedPlaylistId;
        if (domainId) {
          this.selectedPlaylistId = domainId;
          this.playlistPageId = domainId;
        }
      }
      if (viewKey === "field") {
        this.fieldMode = "now";
        this.fieldRequestedCanonicalId = searchParams.get("canonical_id") || "";
        this.fieldRequestedTopicId = searchParams.get("topic_id") || "";
        this.fieldRequestedEvidenceId = searchParams.get("evidence_id") || "";
        this.fieldRequestedSnapshotId = searchParams.get("snapshot_id") || "";
        this.fieldRequestedWindowStart = searchParams.get("window_start") || "";
        this.fieldRequestedWindowEnd = searchParams.get("window_end") || "";
        const entityKey = searchParams.get("entity_key") || "";
        this.fieldRequestedEntity = entityKey ? {
          normalized_key: entityKey,
          entity_type: searchParams.get("entity_type") || "unknown",
          name: searchParams.get("entity_name") || entityKey,
        } : null;
        this.storySelectedId = searchParams.get("story_id") || this.storySelectedId || "";
      }
      if (viewKey === "stories") {
        this.storySelectedId = searchParams.get("story_id") || this.storySelectedId || "";
        this.storyReferenceFocusCanonicalId = searchParams.get("focus_canonical_id") || "";
      }
      if (viewKey === "briefs") this.briefV2SelectedId = searchParams.get("brief_id") || this.briefV2SelectedId || "";
      if (viewKey === "library") {
        const tab = searchParams.get("tab") || this.libraryTab || "sources";
        this.libraryTab = ["sources", "records"].includes(tab) ? tab : "sources";
        this.libraryScope = searchParams.get("scope") === "global" ? "global" : "domain";
      }
      if (viewKey === "media") {
        this.mediaQuery = searchParams.get("q") || "";
      }
      const isVideoListing = viewKey === "videos" || (viewKey === "library" && this.libraryTab === "records");
      if (isVideoListing) {
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
      if (viewKey === "jobs" || viewKey === "operations") {
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
        const normalizedPath = ((window.location.pathname || "").replace(/\/+$/, "") || "/");
        const legacyBriefPath = normalizedPath === "/briefs";
        const playlistId = searchParams.get("playlist_id") || this.playlistPageId || this.selectedPlaylistId;
        this.playlistPageId = playlistId || null;
        this.selectedPlaylistId = playlistId || this.selectedPlaylistId;
        const contentTab = searchParams.get("tab") || (legacyBriefPath ? "brief" : "records");
        this.playbackContentTab = contentTab === "brief" ? "brief" : "records";
        this.briefV2SelectedId = this.playbackContentTab === "brief" ? (searchParams.get("brief_id") || "") : "";
        this.playlistSelectedDate = searchParams.get("date") || this.playlistSelectedDate || "";
        if (!this.playlistSelectedDate) this.playlistSelectedDate = todayIsoLocal();
        this.playlistRequestedVideoId = searchParams.get("video_id") || "";
        this.playlistRequestedPosition = Number(searchParams.get("t") || 0) || 0;
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
      if (["field", "domains", "stories", "briefs", "library", "domain-settings", "operations"].includes(viewKey) && this.selectedPlaylistId) {
        searchParams.set("domain_id", String(this.selectedPlaylistId));
      }
      if (viewKey === "field") {
        if (this.playlistEventMapSelectedKind === "canonical" && this.playlistEventMapSelectedId) searchParams.set("canonical_id", String(this.playlistEventMapSelectedId));
        else if (this.fieldRequestedCanonicalId) searchParams.set("canonical_id", String(this.fieldRequestedCanonicalId));
        if (this.playlistEventMapSelectedKind === "topic" && this.playlistEventMapSelectedId) searchParams.set("topic_id", String(this.playlistEventMapSelectedId));
        else if (this.fieldRequestedTopicId) searchParams.set("topic_id", String(this.fieldRequestedTopicId));
        if (this.playlistEventMapSelectedKind === "evidence" && this.playlistEventMapSelectedId) searchParams.set("evidence_id", String(this.playlistEventMapSelectedId));
        else if (this.fieldRequestedEvidenceId) searchParams.set("evidence_id", String(this.fieldRequestedEvidenceId));
        const entity = this.playlistEventMapEntityFilter || this.fieldRequestedEntity;
        if (entity?.normalized_key) {
          searchParams.set("entity_key", String(entity.normalized_key));
          searchParams.set("entity_type", String(entity.entity_type || "unknown"));
          if (entity.name) searchParams.set("entity_name", String(entity.name));
        }
        if (this.storySelectedId && this.fieldMode === "story") searchParams.set("story_id", String(this.storySelectedId));
        if (this.fieldRequestedSnapshotId) searchParams.set("snapshot_id", String(this.fieldRequestedSnapshotId));
        if (this.playlistEventMapWindowStart) searchParams.set("window_start", String(this.playlistEventMapWindowStart).slice(0, 10));
        if (this.playlistEventMapWindowEnd) searchParams.set("window_end", String(this.playlistEventMapWindowEnd).slice(0, 10));
      }
      if (viewKey === "stories" && this.storySelectedId) {
        searchParams.set("story_id", String(this.storySelectedId));
        if (this.storySelectedSnapshotId) searchParams.set("snapshot_id", String(this.storySelectedSnapshotId));
        if (this.storyReferenceFocusCanonicalId) {
          searchParams.set("focus_canonical_id", String(this.storyReferenceFocusCanonicalId));
        }
      }
      if (viewKey === "briefs" && this.briefV2SelectedId) searchParams.set("brief_id", String(this.briefV2SelectedId));
      if (viewKey === "library") {
        searchParams.set("tab", this.libraryTab || "sources");
        searchParams.set("scope", this.libraryScope || "domain");
      }
      if (viewKey === "media" && this.mediaQuery) searchParams.set("q", this.mediaQuery);
      const isVideoListing = viewKey === "videos" || (viewKey === "library" && this.libraryTab === "records");
      if (isVideoListing && this.videoStatus) searchParams.set("status", this.videoStatus);
      if (isVideoListing && Array.isArray(this.videoMediaIds) && this.videoMediaIds.length) {
        searchParams.set("media_id_in", this.videoMediaIds.join(","));
      }
      if (isVideoListing && this.videoQuery) searchParams.set("q", this.videoQuery);
      if (isVideoListing && this.videoFrom) searchParams.set("from", new Date(this.videoFrom).toISOString());
      if (isVideoListing && this.videoTo) searchParams.set("to", new Date(this.videoTo).toISOString());
      if (viewKey === "video" && this.playerPageVideoId) searchParams.set("video_id", String(this.playerPageVideoId));
      if (viewKey === "jobs" || viewKey === "operations") {
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
        if (this.playlistSelectedDate) searchParams.set("date", String(this.playlistSelectedDate));
        if (this.playbackContentTab === "brief") {
          searchParams.set("tab", "brief");
          if (this.briefV2SelectedId) searchParams.set("brief_id", String(this.briefV2SelectedId));
        }
        if (this.playlistCurrentVideo?.id) searchParams.set("video_id", String(this.playlistCurrentVideo.id));
        const position = this.playbackPendingSeekSec !== null
          ? Number(this.playbackPendingSeekSec || 0)
          : Number(this.playlistMediaCurrentTimeSec || 0);
        if (Number.isFinite(position) && position > 0) searchParams.set("t", String(Math.floor(position)));
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
      if (key === "operations" && this.activeView !== "operations" && !this.operationsReturnUrl) {
        this.operationsReturnUrl = `${window.location.pathname || "/"}${window.location.search || ""}`;
      }
      if ((this.activeView === "media" || (this.activeView === "library" && this.libraryTab === "sources")) && key !== "media" && key !== "library") this._teardownMediaIo();
      if ((this.activeView === "videos" || (this.activeView === "library" && this.libraryTab === "records")) && key !== "videos" && key !== "library") this._teardownVideoIo();
      if (this.activeView === "video" && key !== "video" && typeof this.leaveVideoPage === "function") this.leaveVideoPage();
      if (this.activeView === "usage" && key !== "usage") this.leaveUsagePage();
      if (this.activeView === "playlist" && key !== "playlist" && typeof this.leavePlaybackPage === "function") {
        this.leavePlaybackPage();
      }
      if (this.activeView === "field" && key !== "field" && typeof this.leaveField === "function") {
        this.saveFieldCursor({ immediate: true });
        this.leaveField();
      }
      if (this.activeView !== key && typeof this._stopDocumentMediaPlayback === "function") {
        this._stopDocumentMediaPlayback({ clearSources: true });
      }
      if (key === "videos" || (key === "library" && this.libraryTab === "records")) this._ensureVideoRange();
      this.activeView = key;
      if (key === "field" && !this.playlistEventMapController?.()) this.playlistEventMapLoading = true;
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

    async refreshActive({ throwOnError = false } = {}) {
      try {
        if (!["jobs", "operations"].includes(this.activeView)) {
          this._disconnectJobsWs();
          this._destroyJobsDoneChart();
        }
        if (this.activeView === "field") return await this.loadField();
        if (this.activeView === "domains") return await this.loadDomainDirectory();
        if (this.activeView === "stories") return await this.loadStories();
        if (this.activeView === "library") return await this.loadLibrary();
        if (this.activeView === "domain-settings") return await this.loadDomainSettings();
        if (this.activeView === "operations") return await this.loadOperations();
        if (this.activeView === "usage") return await this.loadUsage();
        if (this.activeView === "overview") return await this.loadStats();
        if (this.activeView === "media") return await this.loadMedia();
        if (this.activeView === "videos") return await this.loadVideos();
        if (this.activeView === "video") return await this.loadVideoPage();
        if (this.activeView === "jobs") return await this.loadJobs();
        if (this.activeView === "playlists") return await this.loadPlaylists();
        if (this.activeView === "playlist") return await this.loadPlaybackPage();
        if (this.activeView === "settings") return await this.loadSettings();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
        if (throwOnError) throw e;
      }
    },
  };
}
