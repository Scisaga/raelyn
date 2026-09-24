import { assetDirectModeEnabled } from "../shared/asset-delivery.js";

export function createAppInitMethods() {
  return {
    _initShellListeners() {
      this._applySidebarMode();

      const onResize = () => {
        this.playlistCalendarUpdateCount();
        this._applySidebarMode();
      };
      window.addEventListener("resize", onResize, { passive: true });
      window.addEventListener("orientationchange", onResize, { passive: true });
      window.addEventListener("popstate", () => {
        const viewKey = this._parseViewFromLocation();
        if (this.activeView === "media" && viewKey !== "media") this._teardownMediaIo();
        if (this.activeView === "videos" && viewKey !== "videos") this._teardownVideoIo();
        if (this.activeView === "jobs" && viewKey !== "jobs") this._destroyJobsDoneChart();
        if (this.activeView === "video" && viewKey !== "video" && typeof this.leaveVideoPage === "function") this.leaveVideoPage();
        if (this.activeView === "usage" && viewKey !== "usage") this.leaveUsagePage();
        if (this.activeView === "playlist" && viewKey !== "playlist" && typeof this.leavePlaybackPage === "function") {
          this.leavePlaybackPage();
        }
        if (this.activeView === "field" && viewKey !== "field" && typeof this.leaveField === "function") {
          this.saveFieldCursor({ immediate: true });
          this.leaveField();
        }
        if (this.activeView !== viewKey && typeof this._stopDocumentMediaPlayback === "function") {
          this._stopDocumentMediaPlayback({ clearSources: true });
        }
        this.activeView = viewKey;
        if (viewKey === "field" && !this.playlistEventMapController?.()) this.playlistEventMapLoading = true;
        const item = this.navItems.find((nav) => nav.key === viewKey);
        this.pageTitle = item ? item.label : viewKey;
        this._applyQueryFromLocation(viewKey);
        this.refreshActive();
      });
    },

    _initShellRouteState() {
      const initialView = this._parseViewFromLocation();
      this.activeView = initialView;
      if (initialView === "field") this.playlistEventMapLoading = true;
      const item = this.navItems.find((nav) => nav.key === initialView);
      this.pageTitle = item ? item.label : initialView;
      this._applyQueryFromLocation(initialView);
      if (initialView === "field" && !String(this.selectedPlaylistId || this.playlistPageId || "").trim()) {
        try {
          const storedDomainId = String(localStorage.getItem(this.v2LastDomainKey) || "").trim();
          if (storedDomainId) {
            this.selectedPlaylistId = storedDomainId;
            this.playlistPageId = storedDomainId;
            this._initialFieldDomainRestoredFromStorage = true;
          }
        } catch {
          // 没有本地存储时，后续仍可以从域目录选择默认域。
        }
      }
      this._syncUrl({ push: false });
    },

    _preloadInitialFieldRenderer() {
      if (this.activeView !== "field") return;
      void this.playlistEventMapPreloadRenderer?.().catch((error) => {
        this.playlistEventMapError = error?.message || String(error);
      });
    },

    _attachInitialFieldVisual(request) {
      const settle = this._initialFieldVisualResolve;
      if (typeof settle !== "function") return request;
      this._initialFieldVisualResolve = null;
      void Promise.resolve(request).then(
        () => settle(),
        () => settle(),
      );
      return request;
    },

    _startInitialFieldMap() {
      if (this.activeView !== "field") return;
      const domainId = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if (domainId) {
        this.playlistPageId = domainId;
        this.selectedPlaylistId = domainId;
      }
      this.playlistSubview = "analysis";
      if (!this.playlistEventMapController?.()) this.playlistEventMapLoading = true;
      if (this._initialFieldLoadPromise) return this._initialFieldLoadPromise;
      let settleFirstVisual;
      const firstVisualPromise = new Promise((resolve) => { settleFirstVisual = resolve; });
      this._initialFieldVisualPromise = firstVisualPromise;
      this._initialFieldVisualResolve = settleFirstVisual;
      // 无本地观测域时，loadField 会先读取 compact 域目录；它同样必须在
      // /system 与资源通道探测之前启动，否则冷目录会留下数秒纯黑窗口。
      const request = this.refreshActive({ throwOnError: true });
      this._initialFieldLoadPromise = request;
      const settleIfNoMapStarted = () => {
        if (this._initialFieldVisualResolve !== settleFirstVisual) return;
        this._initialFieldVisualResolve = null;
        settleFirstVisual();
      };
      void request.then(settleIfNoMapStarted, settleIfNoMapStarted);
      void request.catch((error) => {
        if (this._initialFieldLoadPromise === request) {
          this._initialFieldLoadPromise = null;
          if (this._initialFieldVisualPromise === firstVisualPromise) {
            this._initialFieldVisualPromise = null;
            this._initialFieldVisualResolve = null;
          }
        }
        this.playlistEventMapError = error?.message || String(error);
      });
      return request;
    },

    async _reconcileRestoredFieldDomain(domains = []) {
      if (!this._initialFieldDomainRestoredFromStorage) return false;
      this._initialFieldDomainRestoredFromStorage = false;
      const restoredId = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      if ((domains || []).some((domain) => String(domain?.id || "") === restoredId)) return false;

      this._fieldLoadRequestToken = Number(this._fieldLoadRequestToken || 0) + 1;
      this.playlistEventMapReset?.();
      this.selectedPlaylistId = null;
      this.playlistPageId = null;
      this.fieldMode = "now";
      this.fieldRequestedCanonicalId = "";
      this.fieldRequestedTopicId = "";
      this.fieldRequestedEvidenceId = "";
      this.fieldRequestedSnapshotId = "";
      this.fieldRequestedWindowStart = "";
      this.fieldRequestedWindowEnd = "";
      this.fieldRequestedEntity = null;
      this.storySelectedId = "";
      if (!(domains || []).length) {
        try {
          localStorage.removeItem?.(this.v2LastDomainKey);
        } catch {
          // 本地存储不可用时保持明确的无域状态。
        }
        this.playlistEventMapLoading = false;
        this.pageTitle = "星域";
        this._syncUrl?.({ push: false });
        return true;
      }
      const replacementId = await this.ensureCurrentDomain();
      if (!replacementId || this.activeView !== "field") {
        this.playlistEventMapLoading = false;
        this._syncUrl?.({ push: false });
        return true;
      }
      this.globalStatus = "";
      this.playlistEventMapLoading = true;
      await this.refreshActive();
      return true;
    },

    async _refreshHealthStatus({ payload = null, silent = false, throwOnError = false } = {}) {
      try {
        const health = payload || (await this.api(`/health`));
        this.healthOk = !!health.ok;
        this.services.db = health.db || this.services.db;
        this.services.s3 = health.s3 || this.services.s3;
        this.services.asr = health.asr || this.services.asr;
        this.services.embedding = health.embedding || this.services.embedding;
        this.services.llm = health.llm || this.services.llm;
        this.inference = health.inference || this.inference;
        this.globalStatus = health.deps_ok ? "" : "部分依赖不可用";
        return health;
      } catch (e) {
        if (!silent) this.globalStatus = `error: ${e.message}`;
        if (throwOnError) throw e;
        return null;
      }
    },

    _resumeProtectedRealtime() {
      this._connectJobStatsWs();
      try {
        if (!this._pausePollId) this._pausePollId = setInterval(() => this.loadSystemStatus({ silent: true }), 15000);
      } catch {
        // ignore
      }
    },

    async loadMediaIndex({ lightweight = false } = {}) {
      const params = new URLSearchParams({ limit: "500", offset: "0" });
      if (!lightweight && !assetDirectModeEnabled(this.assetDelivery)) {
        params.set("presign", "false");
      }
      const path = lightweight ? `/media/options?${params.toString()}` : `/media?${params.toString()}`;
      this.mediaIndex = await this.api(path);
      if (!lightweight && typeof this._syncMediaDeleteTrackingFromList === "function") {
        this._syncMediaDeleteTrackingFromList(this.mediaIndex);
      }
      return this.mediaIndex;
    },

    async _refreshProtectedData({ statsReady = false } = {}) {
      if (this.activeView === "overview") {
        if (!statsReady) await this.loadStats();
        return;
      }
      if (this.activeView === "media") {
        await this.refreshActive();
        if (!statsReady) void this.loadStats({ silent: true });
        return;
      }
      if (this.activeView === "usage") {
        await this.refreshActive();
        return;
      }
      await this.loadMediaIndex({ lightweight: ["field", "stories", "briefs", "library", "domain-settings", "operations", "playlist", "playlists", "videos"].includes(this.activeView) });
      await this.refreshActive();
      if (!statsReady) {
        void this.loadStats({ silent: true });
      }
    },

    async _prepareOverviewStartupGate() {
      this.startupGateStage = "overview-data";
      const [health, stats] = await Promise.all([
        this._refreshHealthStatus({ silent: true, throwOnError: true }),
        this.loadStats({ silent: true, throwOnError: true }),
      ]);
      this.startupGateStage = "overview-images";
      await this.preloadOverviewStartupAssets(stats);
      return {
        healthReady: !!health,
        statsReady: !!stats,
      };
    },

    async _finishFieldStartup({ healthReady = false, statsReady = false, initializePwa = false } = {}) {
      this.initMediaSession();
      this._resumeProtectedRealtime();
      const fieldPromise = this._initialFieldLoadPromise || this.refreshActive();
      const firstVisualPromise = this._initialFieldVisualPromise || this._playlistEventMapLoadPromise || fieldPromise;
      // 完整 scene 与标签元数据仍属于用户正在观看的加载序列；目录、统计、
      // PWA 和媒体选项都等这两项结束，避免与点集/标签抢数据库和网络。
      const fieldHydrationPromise = firstVisualPromise
        .then(async () => {
          if (!String(this.playlistEventMapSnapshotId || "")) return;
          try {
            await this.playlistEventMapWaitForCompleteScene({ includeMetadata: true });
          } catch {
            // 对应阶段由 compact 轮询恢复；非视觉启动任务仍需继续。
          }
        })
        .catch(() => {});
      void fieldHydrationPromise.then(() => {
        if (initializePwa) void this.initPwa();
        if (!healthReady) void this._refreshHealthStatus();
        void this.loadMediaIndex({ lightweight: true }).catch((error) => {
          this.globalStatus = `error: ${error?.message || String(error)}`;
        });
        if (!statsReady) void this.loadStats({ silent: true });
      });
      // 域切换器需要的完整目录包含封面、来源预览和统计，冷查询可能耗时数秒。
      // loadField 在没有已恢复域时只取 compact 目录决定默认域；完整目录统一等首图后再补。
      const domainsPromise = fieldHydrationPromise
        .then(() => this.loadDomains({ force: true }))
        .then(async (domains) => {
          await this._reconcileRestoredFieldDomain(domains);
          return domains;
        })
        .catch(() => []);
      void domainsPromise;
      try {
        await fieldPromise;
      } finally {
        if (this._initialFieldLoadPromise === fieldPromise) this._initialFieldLoadPromise = null;
        if (this._initialFieldVisualPromise === firstVisualPromise) {
          this._initialFieldVisualPromise = null;
          this._initialFieldVisualResolve = null;
        }
      }
    },

    async _finishInitAfterStartupGate({ healthReady = false, statsReady = false } = {}) {
      if (this.activeView === "field") {
        await this._finishFieldStartup({ healthReady, statsReady, initializePwa: true });
        return;
      }
      await this.initPwa();
      this.initMediaSession();
      if (!healthReady) await this._refreshHealthStatus();
      this._resumeProtectedRealtime();
      await this.loadDomains().catch(() => []);
      await this._refreshProtectedData({ statsReady });
    },

    async _resumeAfterApiReauth({ healthReady = false, statsReady = false } = {}) {
      if (this.activeView === "field") {
        await this._finishFieldStartup({ healthReady, statsReady, initializePwa: false });
        return;
      }
      this.initMediaSession();
      if (!healthReady) await this._refreshHealthStatus();
      this._resumeProtectedRealtime();
      await this.loadDomains().catch(() => []);
      await this._refreshProtectedData({ statsReady });
    },

    async _continueStartupSequence({ resume = false } = {}) {
      if (this._startupSequenceRunning) return false;
      if (this._startupSequenceComplete && !resume) return true;

      this._startupSequenceRunning = true;
      try {
        let healthReady = false;
        let statsReady = false;
        this.apiAuthError = "";
        this._syncApiAuthTokenFromCookie();
        // 星域首图与系统状态互不依赖；先发出 compact manifest，避免慢 /system
        // 在已经显示应用外壳的会话里制造纯黑等待。
        this._startInitialFieldMap();
        if (this.startupGateVisible) this.startupGateStage = "system";
        await this.loadSystemStatus({ silent: true, throwOnError: true });
        this.apiAuthRequired = false;
        this.apiAuthPromptVisible = false;
        if (this.startupGateVisible) this.startupGateStage = "asset-delivery";
        await this.initAssetDelivery();
        if (this.startupGateVisible && this.activeView === "overview") {
          const ready = await this._prepareOverviewStartupGate();
          healthReady = ready.healthReady;
          statsReady = ready.statsReady;
        }
        if (this.startupGateVisible) {
          this.startupGateStage = "ready";
          this._closeStartupGate();
        }

        if (resume) {
          await this._resumeAfterApiReauth({ healthReady, statsReady });
        } else {
          await this._finishInitAfterStartupGate({ healthReady, statsReady });
          this._startupSequenceComplete = true;
        }
        return true;
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        if (String(msg).startsWith("401:")) {
          this.handleApiUnauthorized({ message: "访问 token 无效，请重新输入。", preserveDraft: true });
          return false;
        }
        this.healthOk = false;
        this.globalStatus = `error: ${msg}`;
        this._closeStartupGate();
        return false;
      } finally {
        this._startupSequenceRunning = false;
        this.apiAuthSubmitting = false;
      }
    },

    async submitStartupToken() {
      if (this.apiAuthSubmitting) return;
      const token = String(this.apiAuthTokenDraft || "").trim();
      if (!token) {
        this.apiAuthError = "请输入访问 token";
        this.focusStartupTokenInput();
        return;
      }

      this.apiAuthSubmitting = true;
      this.apiAuthError = "";
      this._storeApiAuthToken(token);
      const ok = await this._continueStartupSequence({ resume: !!this._startupSequenceComplete });
      if (ok) this.apiAuthTokenDraft = "";
      else if (!this.apiAuthError) this.apiAuthError = "访问 token 无效，请重试。";
    },

    async init() {
      try {
        this.playlistEventMapRestoreWheelMode();
        this._initShellListeners();
        this._initShellRouteState();
        this._preloadInitialFieldRenderer();
        if (this.startupGateVisible) {
          this._markStartupGateSeenInSession();
          await this.waitForStartupGatePaint();
          this._mountStartupStarfield();
        }
        await this._continueStartupSequence();
      } catch (e) {
        this.healthOk = false;
        this.globalStatus = `error: ${e.message}`;
        this._closeStartupGate();
      }
    },
  };
}
