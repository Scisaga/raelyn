const PWA_RUNTIME_VERSION = "20260331.1";

function readStoredBool(key) {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function writeStoredBool(key, value) {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    // ignore
  }
}

function isIos() {
  try {
    return /iphone|ipad|ipod/i.test(window.navigator.userAgent || "");
  } catch {
    return false;
  }
}

function isSafari() {
  try {
    const ua = String(window.navigator.userAgent || "").toLowerCase();
    return ua.includes("safari") && !ua.includes("crios") && !ua.includes("fxios") && !ua.includes("edgios");
  } catch {
    return false;
  }
}

function isStandalone() {
  try {
    if (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) return true;
  } catch {
    // ignore
  }
  try {
    return window.navigator.standalone === true;
  } catch {
    return false;
  }
}

function isInstallSupportedContext() {
  try {
    if (!window.isSecureContext) {
      const host = String(window.location.hostname || "").trim().toLowerCase();
      return host === "localhost" || host === "127.0.0.1";
    }
    return true;
  } catch {
    return false;
  }
}

export function createPwaModule({ installHintDismissedKey }) {
  return {
    pwaInstalled: false,
    pwaGuideOpen: false,
    pwaGuideMode: "",
    pwaInstallHintDismissed: readStoredBool(installHintDismissedKey),
    pwaRegistrationOk: false,
    pwaRegistrationError: "",
    _pwaBound: false,
    _pwaStandaloneQuery: null,

    _refreshPwaState() {
      this.pwaInstalled = isStandalone();
      if (this.pwaInstalled) this.pwaGuideOpen = false;
    },

    _isIosDevice() {
      return isIos();
    },

    _isSafariBrowser() {
      return isSafari();
    },

    _isMobileInstallViewport() {
      try {
        return window.matchMedia("(max-width: 767px)").matches;
      } catch {
        return window.innerWidth < 768;
      }
    },

    pwaInstallState() {
      if (this.pwaInstalled) return "installed";
      if (this._isIosDevice()) {
        return this._isSafariBrowser() ? "ios" : "ios-browser";
      }
      return isInstallSupportedContext() ? "native" : "unsupported";
    },

    pwaHasInstallEntry() {
      return this.pwaInstallState() !== "unsupported";
    },

    pwaCardTitle() {
      const state = this.pwaInstallState();
      if (state === "installed") return "已安装到主屏幕";
      if (state === "native" || state === "ios") return "添加到主屏幕";
      if (state === "ios-browser") return "请用 Safari 添加到主屏幕";
      return "主屏幕";
    },

    pwaCardDescription() {
      const state = this.pwaInstallState();
      if (state === "installed") return "当前已在独立应用模式中运行。";
      if (state === "native") return "请使用浏览器原生“添加到主屏幕/安装应用”入口完成安装或添加。";
      if (state === "ios") return "iPhone / iPad 请使用 Safari 的分享菜单，将 RAELYN 添加到主屏幕。";
      if (state === "ios-browser") return "当前浏览器不支持 iOS 的主屏幕添加，请改用 Safari 打开此页面。";
      return "";
    },

    pwaActionLabel() {
      const state = this.pwaInstallState();
      if (state === "installed") return "已安装";
      if (state === "native" || state === "ios") return "查看步骤";
      if (state === "ios-browser") return "查看说明";
      return "主屏幕";
    },

    pwaHintText() {
      const state = this.pwaInstallState();
      if (state === "native") return "请从地址栏或浏览器菜单进入“添加到主屏幕/安装应用”。";
      if (state === "ios") return "可添加到主屏幕，建议从 Safari 的分享菜单完成。";
      if (state === "ios-browser") return "若要添加到主屏幕，请改用 Safari 打开。";
      return "";
    },

    pwaShouldShowHint() {
      return (
        !this.pwaInstalled &&
        !this.pwaInstallHintDismissed &&
        this._isMobileInstallViewport() &&
        this.pwaHasInstallEntry()
      );
    },

    dismissPwaHint() {
      this.pwaInstallHintDismissed = true;
      writeStoredBool(installHintDismissedKey, true);
    },

    closePwaGuide() {
      this.pwaGuideOpen = false;
      this.pwaGuideMode = "";
    },

    async openPwaInstall() {
      const state = this.pwaInstallState();
      if (state === "installed" || state === "unsupported") return;
      if (state === "native") {
        this.pwaGuideMode = "native";
        this.pwaGuideOpen = true;
        this.dismissPwaHint();
        return;
      }
      if (state === "ios") {
        this.pwaGuideMode = "ios";
        this.pwaGuideOpen = true;
        this.dismissPwaHint();
        return;
      }
      if (state === "ios-browser") {
        this.pwaGuideMode = "ios-browser";
        this.pwaGuideOpen = true;
        this.dismissPwaHint();
      }
    },

    async initPwa() {
      this._refreshPwaState();
      if (this._pwaBound) return;
      this._pwaBound = true;

      if (window.matchMedia) {
        try {
          this._pwaStandaloneQuery = window.matchMedia("(display-mode: standalone)");
          const handler = () => this._refreshPwaState();
          if (typeof this._pwaStandaloneQuery.addEventListener === "function") {
            this._pwaStandaloneQuery.addEventListener("change", handler);
          } else if (typeof this._pwaStandaloneQuery.addListener === "function") {
            this._pwaStandaloneQuery.addListener(handler);
          }
        } catch {
          // ignore
        }
      }

      window.addEventListener("appinstalled", () => {
        this.pwaInstalled = true;
        this.closePwaGuide();
        this.dismissPwaHint();
      });

      if (!("serviceWorker" in window) || !isInstallSupportedContext()) return;

      try {
        const registration = await window.navigator.serviceWorker.register(
          `/sw.js?v=${encodeURIComponent(PWA_RUNTIME_VERSION)}`,
          { scope: "/" }
        );
        try {
          await registration.update();
        } catch {
          // ignore
        }
        this.pwaRegistrationOk = true;
        this.pwaRegistrationError = "";
      } catch (e) {
        this.pwaRegistrationOk = false;
        this.pwaRegistrationError = e && e.message ? e.message : String(e);
      }
    },
  };
}
