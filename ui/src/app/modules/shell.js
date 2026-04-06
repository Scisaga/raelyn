import { createApiDocLinks, createNavItems } from "../navigation.js";
import { mountStartupFluid } from "../startup-fluid/adapter.js";

function readStoredBool(key) {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function readSessionBool(key) {
  try {
    return sessionStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function writeSessionBool(key, value) {
  try {
    sessionStorage.setItem(key, value ? "1" : "0");
  } catch {
    // ignore
  }
}

function readCookieValue(key) {
  try {
    const prefix = `${encodeURIComponent(String(key || "").trim())}=`;
    const parts = String(document.cookie || "").split(/;\s*/);
    for (const part of parts) {
      if (!part || !part.startsWith(prefix)) continue;
      return decodeURIComponent(part.slice(prefix.length));
    }
  } catch {
    // ignore
  }
  return "";
}

function writeCookieValue(key, value, { maxAgeSeconds = 31536000 } = {}) {
  const encodedKey = encodeURIComponent(String(key || "").trim());
  const encodedValue = encodeURIComponent(String(value || "").trim());
  document.cookie = `${encodedKey}=${encodedValue}; Path=/; Max-Age=${Math.max(0, Number(maxAgeSeconds || 0))}; SameSite=Lax`;
}

function clearCookieValue(key) {
  const encodedKey = encodeURIComponent(String(key || "").trim());
  document.cookie = `${encodedKey}=; Path=/; Max-Age=0; SameSite=Lax`;
}

export function createShellModule({ apiTokenCookieKey, sidebarCollapsedKey, sidebarHiddenKey, startupGateSeenSessionKey }) {
  const startupGateSeenInSession = readSessionBool(startupGateSeenSessionKey);
  return {
    sidebarCollapsed: readStoredBool(sidebarCollapsedKey),
    sidebarHidden: readStoredBool(sidebarHiddenKey),
    sidebarMobilePortrait: false,
    activeView: "overview",
    pageTitle: "概览",
    healthOk: false,
    globalStatus: "",
    apiAuthToken: readCookieValue(apiTokenCookieKey),
    apiAuthTokenDraft: "",
    apiAuthRequired: false,
    apiAuthPromptVisible: false,
    apiAuthSubmitting: false,
    apiAuthError: "",
    _startupSequenceRunning: false,
    _startupSequenceComplete: false,
    startupGateSeenInSession,
    startupGateVisible: !startupGateSeenInSession,
    startupGateStage: startupGateSeenInSession ? "idle" : "boot",
    startupFluidActive: false,
    _startupFluidHandle: null,
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
    inference: {
      mode: "local",
      modeSource: "env",
      volcengine: {
        source: "env",
        apiKeyPresent: false,
        apiKeyMasked: "",
        llmModel: "",
        asrModel: "",
        asrAppKeyPresent: false,
        asrAppKeyMasked: "",
        asrAccessKeyPresent: false,
        asrAccessKeyMasked: "",
      },
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

    apiAuthTokenValue() {
      return String(this.apiAuthToken || "").trim();
    },

    _syncApiAuthTokenFromCookie() {
      this.apiAuthToken = readCookieValue(apiTokenCookieKey);
      return this.apiAuthTokenValue();
    },

    _storeApiAuthToken(token) {
      const value = String(token || "").trim();
      if (!value) {
        this._clearApiAuthToken();
        return "";
      }
      writeCookieValue(apiTokenCookieKey, value);
      this.apiAuthToken = value;
      return value;
    },

    _clearApiAuthToken() {
      clearCookieValue(apiTokenCookieKey);
      this.apiAuthToken = "";
    },

    _shouldAttachApiAuth(url) {
      try {
        const resolved = new URL(String(url || ""), window.location.origin);
        return resolved.origin === window.location.origin && resolved.pathname.startsWith("/api/");
      } catch {
        return String(url || "").startsWith("/api/");
      }
    },

    buildApiAuthRequestOptions(url, options = {}) {
      const init = options && typeof options === "object" ? { ...options } : {};
      const headers = new Headers((options && options.headers) || undefined);
      const token = this.apiAuthTokenValue();
      if (token && this._shouldAttachApiAuth(url)) headers.set("Authorization", `Bearer ${token}`);
      if ([...headers.keys()].length) init.headers = headers;
      else delete init.headers;
      return init;
    },

    async fetchWithApiAuth(url, options = {}) {
      const init = this.buildApiAuthRequestOptions(url, options);
      return fetch(url, init);
    },

    _clearTimer(name) {
      try {
        if (this[name]) clearTimeout(this[name]);
      } catch {
        // ignore
      }
      this[name] = null;
    },

    _clearIntervalSafe(name) {
      try {
        if (this[name]) clearInterval(this[name]);
      } catch {
        // ignore
      }
      this[name] = null;
    },

    _stopDocumentMediaPlayback({ clearSources = false } = {}) {
      let elements = [];
      try {
        elements = Array.from(document.querySelectorAll("video, audio"));
      } catch {
        elements = [];
      }

      for (const el of elements) {
        if (!el) continue;
        try {
          if (typeof el.pause === "function") el.pause();
        } catch {
          // ignore
        }
        if (!clearSources) continue;
        try {
          el.removeAttribute("src");
        } catch {
          // ignore
        }
        try {
          const sources = el.querySelectorAll ? Array.from(el.querySelectorAll("source")) : [];
          for (const source of sources) source.removeAttribute("src");
        } catch {
          // ignore
        }
        try {
          if (typeof el.load === "function") el.load();
        } catch {
          // ignore
        }
      }
    },

    _suspendApiAuthProtectedRealtime() {
      try {
        if (typeof this._disconnectJobStatsWs === "function") this._disconnectJobStatsWs();
      } catch {
        // ignore
      }
      try {
        if (typeof this._disconnectJobsWs === "function") this._disconnectJobsWs();
      } catch {
        // ignore
      }
      this._clearTimer("_jobStatsWsRetryTimer");
      this._clearTimer("_jobsWsRetryTimer");
      this._clearIntervalSafe("_pausePollId");
      this._clearIntervalSafe("_workersPollId");
    },

    focusStartupTokenInput() {
      try {
        const el = this.$refs && this.$refs.startupTokenInput;
        if (!el || typeof el.focus !== "function") return;
        el.focus();
        if (typeof el.select === "function") el.select();
      } catch {
        // ignore
      }
    },

    _markStartupGateSeenInSession() {
      this.startupGateSeenInSession = true;
      writeSessionBool(startupGateSeenSessionKey, true);
    },

    _destroyStartupFluid() {
      try {
        const handle = this._startupFluidHandle;
        if (handle && typeof handle.destroy === "function") handle.destroy();
      } catch {
        // ignore
      }
      this._startupFluidHandle = null;
      this.startupFluidActive = false;
    },

    _mountStartupFluid() {
      this._destroyStartupFluid();
      try {
        const canvas = this.$refs && this.$refs.startupFluidCanvas ? this.$refs.startupFluidCanvas : null;
        if (!canvas) return;
        const handle = mountStartupFluid({ canvas, interactive: true });
        if (!handle || typeof handle.destroy !== "function") return;
        this._startupFluidHandle = handle;
        this.startupFluidActive = handle.active !== false;
      } catch {
        this._startupFluidHandle = null;
        this.startupFluidActive = false;
      }
    },

    _closeStartupGate() {
      this._destroyStartupFluid();
      this.startupGateVisible = false;
      this.startupGateStage = "idle";
    },

    _openApiAuthGate({ message = "", preserveDraft = false } = {}) {
      this._clearApiAuthToken();
      this.apiAuthRequired = true;
      this.apiAuthPromptVisible = true;
      this.apiAuthSubmitting = false;
      this.startupGateVisible = true;
      this.startupGateStage = "auth";
      this.apiAuthError = String(message || "").trim();
      if (!preserveDraft) this.apiAuthTokenDraft = "";
      this._suspendApiAuthProtectedRealtime();
      try {
        if (this.$nextTick) {
          this.$nextTick(() => {
            this._mountStartupFluid();
            this.focusStartupTokenInput();
          });
        }
      } catch {
        setTimeout(() => {
          this._mountStartupFluid();
          this.focusStartupTokenInput();
        }, 0);
      }
    },

    handleApiUnauthorized({ message = "访问 token 无效，请重新输入。", preserveDraft = null } = {}) {
      const keepDraft =
        preserveDraft == null ? !!(this.apiAuthSubmitting && String(this.apiAuthTokenDraft || "").trim()) : !!preserveDraft;
      this._openApiAuthGate({ message, preserveDraft: keepDraft });
    },

    startupGateMessage() {
      if (this.apiAuthPromptVisible) return "输入访问令牌";
      if (this.startupGateStage === "system") return "正在建立连接…";
      if (this.startupGateStage === "asset-delivery") return "正在检测资源通道…";
      if (this.startupGateStage === "overview-data") return "正在载入概览…";
      if (this.startupGateStage === "overview-images") return "正在加载图片…";
      if (this.startupGateStage === "ready") return "即将完成…";
      return "Loading…";
    },

    startupGateAriaLabel() {
      if (this.apiAuthPromptVisible) return "请输入访问令牌";
      return this.startupGateMessage();
    },

    async waitForStartupGatePaint() {
      await new Promise((resolve) => {
        if (typeof window === "undefined" || typeof window.requestAnimationFrame !== "function") {
          setTimeout(resolve, 0);
          return;
        }
        window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
      });
    },

    startupGateAuthMessage() {
      return String(this.apiAuthError || "").trim() || "主站 API 已启用 token 保护，输入后按回车继续。";
    },

    _collectOverviewStartupAssets(statsPayload = null) {
      const payload = statsPayload && typeof statsPayload === "object" ? statsPayload : {};
      const recentMedia = Array.isArray(payload.recent_media) ? payload.recent_media : this.stats.recentMedia;
      const recentPlaylists = Array.isArray(payload.recent_playlists) ? payload.recent_playlists : this.stats.recentPlaylists;
      const out = [];
      const seen = new Set();
      const pushAsset = (asset) => {
        const id = String((asset && asset.id) || "").trim();
        if (!id || seen.has(id)) return;
        seen.add(id);
        out.push(asset);
      };

      for (const media of Array.isArray(recentMedia) ? recentMedia : []) {
        pushAsset(media && media.avatar_asset);
      }
      for (const playlist of Array.isArray(recentPlaylists) ? recentPlaylists : []) {
        pushAsset(playlist && playlist.avatar_asset);
        for (const media of Array.isArray(playlist && playlist.media_preview) ? playlist.media_preview : []) {
          pushAsset(media && media.avatar_asset);
        }
      }
      return out;
    },

    _preloadImageUrl(url) {
      const src = String(url || "").trim();
      if (!src) return Promise.resolve();
      if (typeof Image === "undefined") return Promise.resolve();

      return new Promise((resolve) => {
        const img = new Image();
        let settled = false;
        const done = () => {
          if (settled) return;
          settled = true;
          img.onload = null;
          img.onerror = null;
          resolve();
        };
        img.onload = done;
        img.onerror = done;
        img.decoding = "async";
        img.src = src;
        if (img.complete) done();
      });
    },

    async preloadOverviewStartupAssets(statsPayload = null) {
      const assets = this._collectOverviewStartupAssets(statsPayload);
      if (!assets.length) return;
      await Promise.all(
        assets.map((asset) => {
          const url = this.assetContentUrl(asset);
          return this._preloadImageUrl(url);
        })
      );
    },

    async loadSystemStatus({ silent = true, throwOnError = false } = {}) {
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
        const inference = payload && payload.inference ? payload.inference : null;
        if (inference && typeof inference === "object") {
          const volcengine = inference.volcengine && typeof inference.volcengine === "object" ? inference.volcengine : {};
          this.inference = {
            mode: String(inference.mode || "local"),
            modeSource: String(inference.mode_source || "env"),
            volcengine: {
              source: String(volcengine.source || "env"),
              apiKeyPresent: !!volcengine.api_key_present,
              apiKeyMasked: String(volcengine.api_key_masked || ""),
              llmModel: String(volcengine.llm_model || ""),
              asrModel: String(volcengine.asr_model || ""),
              asrAppKeyPresent: !!volcengine.asr_app_key_present,
              asrAppKeyMasked: String(volcengine.asr_app_key_masked || ""),
              asrAccessKeyPresent: !!volcengine.asr_access_key_present,
              asrAccessKeyMasked: String(volcengine.asr_access_key_masked || ""),
            },
          };
        }
      } catch (e) {
        if (!silent) this.globalStatus = `error: ${e.message}`;
        if (throwOnError) throw e;
      }
    },

    async initAssetDelivery() {
      const current = this.assetDelivery || {};
      const directProbeUrl = String(current.directProbeUrl || "").trim();
      if (!current.presignEnabled || !directProbeUrl) {
        this.assetDelivery = { ...current, mode: "proxy", probed: true, probeError: "" };
        return;
      }
      try {
        const resolved = new URL(directProbeUrl, window.location.origin);
        if (window.location.protocol === "https:" && resolved.protocol === "http:") {
          this.assetDelivery = {
            ...current,
            mode: "proxy",
            probed: true,
            probeError: "mixed-content-direct-url",
          };
          return;
        }
      } catch {
        // invalid direct probe URL falls through to fetch and proxy fallback
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
      if (key === "bilibili") return "请更新 YTDLP_COOKIES_BILIBILI";
      if (key === "youtube") return "请更新 YTDLP_COOKIES_YOUTUBE";
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
