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
        if (this.activeView === "videos" && viewKey !== "videos") this._teardownVideoIo();
        if (this.activeView === "jobs" && viewKey !== "jobs") this._destroyJobsDoneChart();
        if (this.activeView === "video" && viewKey !== "video" && typeof this.leaveVideoPage === "function") this.leaveVideoPage();
        if (this.activeView === "playlist" && viewKey !== "playlist" && typeof this.leavePlaylistPage === "function") {
          this.leavePlaylistPage();
        }
        if (this.activeView !== viewKey && typeof this._stopDocumentMediaPlayback === "function") {
          this._stopDocumentMediaPlayback({ clearSources: true });
        }
        this.activeView = viewKey;
        const item = this.navItems.find((nav) => nav.key === viewKey);
        this.pageTitle = item ? item.label : viewKey;
        this._applyQueryFromLocation(viewKey);
        this.refreshActive();
      });
    },

    _initShellRouteState() {
      const initialView = this._parseViewFromLocation();
      this.activeView = initialView;
      const item = this.navItems.find((nav) => nav.key === initialView);
      this.pageTitle = item ? item.label : initialView;
      this._applyQueryFromLocation(initialView);
      this._syncUrl({ push: false });
    },

    async _refreshHealthStatus({ payload = null, silent = false, throwOnError = false } = {}) {
      try {
        const health = payload || (await this.api(`/health`));
        this.healthOk = !!health.ok;
        this.services.db = health.db || this.services.db;
        this.services.s3 = health.s3 || this.services.s3;
        this.services.asr = health.asr || this.services.asr;
        this.services.llm = health.llm || this.services.llm;
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
      try {
        if (!this._workersPollId) this._workersPollId = setInterval(() => this.refreshWorkers(), 5000);
      } catch {
        // ignore
      }
    },

    async _refreshProtectedData({ statsReady = false } = {}) {
      this.mediaIndex = await this.api(`/media?limit=500&offset=0`);
      if (typeof this._syncMediaDeleteTrackingFromList === "function") this._syncMediaDeleteTrackingFromList(this.mediaIndex);
      if (this.activeView !== "overview") {
        await this.refreshActive();
        if (!statsReady) await this.loadStats();
        return;
      }
      if (!statsReady) await this.loadStats();
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

    async _finishInitAfterStartupGate({ healthReady = false, statsReady = false } = {}) {
      await this.initPwa();
      this.initMediaSession();
      if (!healthReady) await this._refreshHealthStatus();
      this._resumeProtectedRealtime();
      await this._refreshProtectedData({ statsReady });
    },

    async _resumeAfterApiReauth({ healthReady = false, statsReady = false } = {}) {
      this.initMediaSession();
      if (!healthReady) await this._refreshHealthStatus();
      this._resumeProtectedRealtime();
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
          this.startupGateVisible = true;
          try {
            if (this.$nextTick) this.$nextTick(() => this.focusStartupTokenInput());
          } catch {
            setTimeout(() => this.focusStartupTokenInput(), 0);
          }
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
        this._initShellListeners();
        this._initShellRouteState();
        if (this.startupGateVisible) {
          this._markStartupGateSeenInSession();
          await this.waitForStartupGatePaint();
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
