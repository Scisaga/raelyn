export function createToastModule() {
  return {
    toasts: [],
    _toastSeq: 0,
    _toastTimers: new Map(),

    toastPush({ level = "info", title = "", message = "", action = null } = {}) {
      const msg = String(message || "").trim();
      if (!msg) return;
      const lv = String(level || "info").trim() || "info";

      const nextSeq = Number(this._toastSeq || 0) + 1;
      this._toastSeq = nextSeq;
      const id = `t${Date.now()}_${nextSeq}`;

      const toast = {
        id,
        level: lv,
        title: String(title || "").trim() || "",
        message: msg,
        action: action && typeof action === "object" ? action : null,
        open: true,
        ts: Date.now(),
      };

      if (!Array.isArray(this.toasts)) this.toasts = [];
      this.toasts.push(toast);

      while (this.toasts.length > 3) {
        const removed = this.toasts.shift();
        if (removed && removed.id) this._toastClearTimer(removed.id);
      }

      if (lv !== "error") {
        this._toastClearTimer(id);
        try {
          const timer = setTimeout(() => this.toastDismiss(id), 3500);
          if (this._toastTimers && this._toastTimers.set) this._toastTimers.set(id, timer);
        } catch {
          // ignore
        }
      }
    },

    _toastClearTimer(id) {
      const key = String(id || "").trim();
      if (!key) return;
      try {
        const timer = this._toastTimers && this._toastTimers.get ? this._toastTimers.get(key) : null;
        if (timer) clearTimeout(timer);
      } catch {
        // ignore
      }
      try {
        if (this._toastTimers && this._toastTimers.delete) this._toastTimers.delete(key);
      } catch {
        // ignore
      }
    },

    toastDismiss(id) {
      const key = String(id || "").trim();
      if (!key) return;
      this._toastClearTimer(key);

      const list = Array.isArray(this.toasts) ? this.toasts : [];
      const idx = list.findIndex((item) => item && String(item.id) === key);
      if (idx < 0) return;
      try {
        list[idx].open = false;
      } catch {
        // ignore
      }
      this.toasts = list;

      setTimeout(() => {
        const cur = Array.isArray(this.toasts) ? this.toasts : [];
        this.toasts = cur.filter((item) => item && String(item.id) !== key);
      }, 180);
    },

    toastSuccess(message, { action = null, title = "" } = {}) {
      this.toastPush({ level: "success", title, message, action });
    },

    toastError(message, { action = null, title = "" } = {}) {
      this.toastPush({ level: "error", title, message, action });
    },

    toastJobsAction() {
      return { type: "jobs", label: "查看任务" };
    },

    toastHandleAction(toast) {
      try {
        const action = toast && toast.action ? toast.action : null;
        if (!action || action.type !== "jobs") return;
        if (this.modals) {
          this.modals.addMedia = false;
          this.modals.createPlaylist = false;
          this.modals.mediaImport = false;
        }
        this.jobsTab = "active";
        this.switchView("jobs");
        if (toast && toast.id) this.toastDismiss(toast.id);
      } catch {
        // ignore
      }
    },
  };
}
