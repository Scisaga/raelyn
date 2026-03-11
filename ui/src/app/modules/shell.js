import { createApiDocLinks, createNavItems } from "../navigation.js";

function readStoredBool(key) {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

export function createShellModule({ sidebarCollapsedKey, sidebarHiddenKey }) {
  return {
    sidebarCollapsed: readStoredBool(sidebarCollapsedKey),
    sidebarHidden: readStoredBool(sidebarHiddenKey),
    sidebarMobilePortrait: false,
    activeView: "overview",
    pageTitle: "概览",
    healthOk: false,
    globalStatus: "",
    pause: { paused: false, reason: null, message: null, set_at: null },
    providerPauses: {},
    _pausePollId: null,
    services: {
      db: { ok: false, url: "", error: null },
      s3: { ok: false, bucket: "", error: null },
      asr: { ok: false, configured: false, url: "", error: null },
      llm: { ok: false, configured: false, url: "", error: null },
    },
    navItems: createNavItems(),
    apiDocLinks: createApiDocLinks(),
    stats: {
      mediaCount: 0,
      videoCount: 0,
      pendingJobs: 0,
      failedJobs: 0,
      recentMedia: [],
      recentVideos: [],
      recentPlaylists: [],
      s3TrackedSizeBytes: null,
      asrCalls: 0,
      llmCalls: 0,
      llmInputTokens: 0,
      llmOutputTokens: 0,
      llmTotalTokens: 0,
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
        if (!this.sidebarMobilePortrait) this.sidebarHidden = true;
        this.sidebarCollapsed = true;
      } else {
        this.sidebarHidden = false;
        if (this.sidebarMobilePortrait) {
          try {
            this.sidebarCollapsed = localStorage.getItem(sidebarCollapsedKey) === "1";
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
          localStorage.setItem(sidebarHiddenKey, this.sidebarHidden ? "1" : "0");
        } catch {
          // ignore
        }
        return;
      }

      this.sidebarCollapsed = !this.sidebarCollapsed;
      try {
        localStorage.setItem(sidebarCollapsedKey, this.sidebarCollapsed ? "1" : "0");
      } catch {
        // ignore
      }
    },

    async loadSystemStatus({ silent = true } = {}) {
      try {
        const payload = await this.api(`/system`);
        const pause = payload && payload.pause ? payload.pause : null;
        const providerPauses = payload && payload.provider_pauses ? payload.provider_pauses : null;
        if (pause && typeof pause === "object") {
          this.pause = {
            paused: !!pause.paused,
            reason: pause.reason || null,
            message: pause.message || null,
            set_at: pause.set_at || null,
          };
        } else {
          this.pause = { paused: false, reason: null, message: null, set_at: null };
        }
        if (providerPauses && typeof providerPauses === "object") {
          this.providerPauses = Object.fromEntries(
            Object.entries(providerPauses).map(([provider, item]) => [
              String(provider || "").trim().toLowerCase(),
              {
                paused: !!(item && item.paused),
                reason: item && item.reason ? item.reason : null,
                message: item && item.message ? item.message : null,
                set_at: item && item.set_at ? item.set_at : null,
              },
            ])
          );
        } else {
          this.providerPauses = {};
        }
      } catch (e) {
        if (!silent) this.globalStatus = `error: ${e.message}`;
      }
    },

    pauseBadgeText() {
      try {
        if (!this.pause || !this.pause.paused) return "";
        const msg = String(this.pause.message || "").trim();
        if (!msg) return "已暂停";
        if (msg.length <= 24) return msg;
        return `${msg.slice(0, 24)}…`;
      } catch {
        return "已暂停";
      }
    },

    providerPause(provider) {
      const key = String(provider || "").trim().toLowerCase();
      if (!key) return { paused: false, reason: null, message: null, set_at: null };
      const item = this.providerPauses && this.providerPauses[key] ? this.providerPauses[key] : null;
      if (!item) return { paused: false, reason: null, message: null, set_at: null };
      return {
        paused: !!item.paused,
        reason: item.reason || null,
        message: item.message || null,
        set_at: item.set_at || null,
      };
    },

    hasProviderPause(provider) {
      return !!this.providerPause(provider).paused;
    },

    providerPauseLabel(provider) {
      const key = String(provider || "").trim().toLowerCase();
      if (key === "bilibili") return "B站已暂停";
      if (key === "youtube") return "YouTube已暂停";
      return `${key || "Provider"}已暂停`;
    },

    providerPauseHint(provider) {
      const key = String(provider || "").trim().toLowerCase();
      if (key === "bilibili" || key === "youtube") return "请更新 Cookie";
      return "请检查配置";
    },

    gotoCookiesSettings(provider = null) {
      this.switchView("settings");
      if (typeof this.setSettingsTab === "function") this.setSettingsTab("cookies");
      const key = String(provider || "").trim().toLowerCase();
      if (key === "bilibili") {
        this.globalStatus = "B站任务已暂停：请更新 YTDLP_COOKIES 后重试";
        return;
      }
      if (key === "youtube") {
        this.globalStatus = "YouTube任务已暂停：请更新 YTDLP_COOKIES 后重试";
        return;
      }
      this.globalStatus = "系统已暂停：请更新 YTDLP_COOKIES 后自动恢复";
    },
  };
}
