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
    assetDelivery: {
      strategy: "startup_probe",
      directProbeUrl: "",
      directProbeTimeoutMs: 1000,
      proxyBasePath: "/api/assets",
      presignEnabled: true,
      mode: "proxy",
      probed: false,
      probeError: "",
    },
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
        const assetDelivery = payload && payload.asset_delivery ? payload.asset_delivery : null;
        if (assetDelivery && typeof assetDelivery === "object") {
          this.assetDelivery = {
            strategy: String(assetDelivery.strategy || "startup_probe"),
            directProbeUrl: String(assetDelivery.direct_probe_url || ""),
            directProbeTimeoutMs: Math.max(0, Number(assetDelivery.direct_probe_timeout_ms || 1000) || 1000),
            proxyBasePath: String(assetDelivery.proxy_base_path || "/api/assets").replace(/\/+$/, ""),
            presignEnabled: assetDelivery.presign_enabled !== false,
            mode: this.assetDelivery && this.assetDelivery.mode ? this.assetDelivery.mode : "proxy",
            probed: this.assetDelivery && this.assetDelivery.probed ? this.assetDelivery.probed : false,
            probeError: "",
          };
        }
      } catch (e) {
        if (!silent) this.globalStatus = `error: ${e.message}`;
      }
    },

    async initAssetDelivery() {
      const current = this.assetDelivery || {};
      const directProbeUrl = String(current.directProbeUrl || "").trim();
      if (!current.presignEnabled || !directProbeUrl) {
        this.assetDelivery = { ...current, mode: "proxy", probed: true, probeError: "" };
        return;
      }
      const timeoutMs = Math.max(0, Number(current.directProbeTimeoutMs || 1000) || 1000);
      const controller = typeof AbortController === "function" ? new AbortController() : null;
      const timeoutId = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : 0;
      try {
        await fetch(directProbeUrl, {
          method: "GET",
          mode: "no-cors",
          cache: "no-store",
          signal: controller ? controller.signal : undefined,
        });
        this.assetDelivery = { ...current, mode: "direct", probed: true, probeError: "" };
      } catch (e) {
        const message = e && e.message ? e.message : String(e);
        this.assetDelivery = { ...current, mode: "proxy", probed: true, probeError: message };
      } finally {
        if (timeoutId) window.clearTimeout(timeoutId);
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
        if (typeof this.setSettingsCookiesTab === "function") this.setSettingsCookiesTab("bilibili");
        this.globalStatus = "B站任务已暂停：请更新 YTDLP_COOKIES_BILIBILI 后重试";
        return;
      }
      if (key === "youtube") {
        if (typeof this.setSettingsCookiesTab === "function") this.setSettingsCookiesTab("youtube");
        this.globalStatus = "YouTube任务已暂停：请更新 YTDLP_COOKIES_YOUTUBE 后重试";
        return;
      }
      this.globalStatus = "系统已暂停：请检查对应平台 Cookies 配置";
    },
  };
}
