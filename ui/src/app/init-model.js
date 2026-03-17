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

    async _refreshHealthStatus() {
      const health = await this.api(`/health`);
      this.healthOk = !!health.ok;
      this.services.db = health.db || this.services.db;
      this.services.s3 = health.s3 || this.services.s3;
      this.services.asr = health.asr || this.services.asr;
      this.services.llm = health.llm || this.services.llm;
      this.globalStatus = health.deps_ok ? "" : "部分依赖不可用";
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

    async _refreshProtectedData() {
      this.mediaIndex = await this.api(`/media?limit=500&offset=0`);
      await this.refreshActive();
      await this.loadStats();
    },

    async _finishInitAfterStartupGate() {
      await this._refreshHealthStatus();
      this._resumeProtectedRealtime();
      await this._refreshProtectedData();
    },

    async _resumeAfterApiReauth() {
      await this._refreshHealthStatus();
      this._resumeProtectedRealtime();
      await this._refreshProtectedData();
    },

    async _continueStartupSequence({ resume = false } = {}) {
      if (this._startupSequenceRunning) return false;
      if (this._startupSequenceComplete && !resume) return true;

      this._startupSequenceRunning = true;
      try {
        this.apiAuthError = "";
        this._syncApiAuthTokenFromCookie();
        await this.loadSystemStatus({ silent: true, throwOnError: true });
        this.apiAuthRequired = false;
        this.apiAuthPromptVisible = false;
        await this.initAssetDelivery();
        this.startupGateVisible = false;

        if (resume) {
          await this._resumeAfterApiReauth();
        } else {
          await this._finishInitAfterStartupGate();
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
        this.startupGateVisible = false;
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
        await this.initPwa();
        await this.waitForStartupGatePaint();
        await this._continueStartupSequence();
      } catch (e) {
        this.healthOk = false;
        this.globalStatus = `error: ${e.message}`;
        this.startupGateVisible = false;
      }
    },
  };
}
