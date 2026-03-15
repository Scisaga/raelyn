export function createAppInitMethods() {
  return {
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
          const item = this.navItems.find((nav) => nav.key === viewKey);
          this.pageTitle = item ? item.label : viewKey;
          this._applyQueryFromLocation(viewKey);
          this.refreshActive();
        });

        const initialView = this._parseViewFromLocation();
        this.activeView = initialView;
        const item = this.navItems.find((nav) => nav.key === initialView);
        this.pageTitle = item ? item.label : initialView;
        this._applyQueryFromLocation(initialView);
        this._syncUrl({ push: false });

        const health = await this.api(`/health`);
        this.healthOk = !!health.ok;
        this.services.db = health.db || this.services.db;
        this.services.s3 = health.s3 || this.services.s3;
        this.services.asr = health.asr || this.services.asr;
        this.services.llm = health.llm || this.services.llm;
        this.globalStatus = health.deps_ok ? "" : "部分依赖不可用";
        await this.loadSystemStatus({ silent: true });
        await this.initAssetDelivery();
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
        this.mediaIndex = await this.api(`/media?limit=500&offset=0`);
        await this.refreshActive();
        await this.loadStats();
      } catch (e) {
        this.healthOk = false;
        this.globalStatus = `error: ${e.message}`;
      }
    },
  };
}
