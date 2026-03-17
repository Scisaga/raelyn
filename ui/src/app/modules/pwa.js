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
    pwaDeferredPrompt: null,
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
      if (this.pwaInstalled) {
        this.pwaDeferredPrompt = null;
        this.pwaGuideOpen = false;
      }
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

    pwaCanInstallDirectly() {
      return !this.pwaInstalled && !!this.pwaDeferredPrompt;
    },

    pwaCanAddOnIos() {
      return !this.pwaInstalled && this._isIosDevice() && this._isSafariBrowser();
    },

    pwaNeedsSafariHint() {
      return !this.pwaInstalled && this._isIosDevice() && !this._isSafariBrowser();
    },

    pwaHasInstallEntry() {
      return this.pwaInstalled || this.pwaCanInstallDirectly() || this.pwaCanAddOnIos() || this.pwaNeedsSafariHint();
    },

    pwaCardTitle() {
      if (this.pwaInstalled) return "已安装到主屏幕";
      if (this.pwaCanInstallDirectly()) return "安装应用";
      if (this.pwaCanAddOnIos()) return "添加到主屏幕";
      if (this.pwaNeedsSafariHint()) return "请用 Safari 添加到主屏幕";
      return "主屏幕";
    },

    pwaCardDescription() {
      if (this.pwaInstalled) return "当前已在独立应用模式中运行。";
      if (this.pwaCanInstallDirectly()) return "Android / Chromium 浏览器可直接安装，安装后可像原生应用一样从主屏幕启动。";
      if (this.pwaCanAddOnIos()) return "iPhone / iPad 请使用 Safari 的分享菜单，将 RAELYN 添加到主屏幕。";
      if (this.pwaNeedsSafariHint()) return "当前浏览器不支持 iOS 的主屏幕添加，请改用 Safari 打开此页面。";
      return "";
    },

    pwaActionLabel() {
      if (this.pwaInstalled) return "已安装";
      if (this.pwaCanInstallDirectly()) return "安装应用";
      if (this.pwaCanAddOnIos()) return "查看添加步骤";
      if (this.pwaNeedsSafariHint()) return "查看说明";
      return "主屏幕";
    },

    pwaHintText() {
      if (this.pwaCanInstallDirectly()) return "可安装到主屏幕，后续可像应用一样直接打开。";
      if (this.pwaCanAddOnIos()) return "可添加到主屏幕，建议从 Safari 的分享菜单完成。";
      if (this.pwaNeedsSafariHint()) return "若要添加到主屏幕，请改用 Safari 打开。";
      return "";
    },

    pwaShouldShowHint() {
      return (
        !this.pwaInstalled &&
        !this.pwaInstallHintDismissed &&
        this._isMobileInstallViewport() &&
        (this.pwaCanInstallDirectly() || this.pwaCanAddOnIos() || this.pwaNeedsSafariHint())
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
      if (this.pwaInstalled) return;
      if (this.pwaCanInstallDirectly()) {
        const promptEvent = this.pwaDeferredPrompt;
        this.pwaDeferredPrompt = null;
        try {
          await promptEvent.prompt();
          const choice = await promptEvent.userChoice;
          const outcome = choice && choice.outcome ? String(choice.outcome) : "";
          this.globalStatus = outcome === "accepted" ? "已触发安装提示" : "已取消安装提示";
        } catch (e) {
          const msg = e && e.message ? e.message : String(e);
          this.globalStatus = `error: ${msg}`;
        } finally {
          this.dismissPwaHint();
        }
        return;
      }
      if (this.pwaCanAddOnIos()) {
        this.pwaGuideMode = "ios";
        this.pwaGuideOpen = true;
        this.dismissPwaHint();
        return;
      }
      if (this.pwaNeedsSafariHint()) {
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

      window.addEventListener("beforeinstallprompt", (event) => {
        this.pwaDeferredPrompt = event;
      });

      window.addEventListener("appinstalled", () => {
        this.pwaInstalled = true;
        this.pwaDeferredPrompt = null;
        this.closePwaGuide();
        this.dismissPwaHint();
      });

      if (!("serviceWorker" in window) || !isInstallSupportedContext()) return;

      try {
        await window.navigator.serviceWorker.register("/sw.js", { scope: "/" });
        this.pwaRegistrationOk = true;
        this.pwaRegistrationError = "";
      } catch (e) {
        this.pwaRegistrationOk = false;
        this.pwaRegistrationError = e && e.message ? e.message : String(e);
      }
    },
  };
}
