import { wsUrl } from "../services/ws.js";

export function createJobsViewMethods() {
  return {
    async loadJobs() {
      await this.refreshJobs();
    },

    jobsActiveCount() {
      if (!String(this.jobsTypeFilter || "").trim()) {
        const pending = Number(this.jobStats && this.jobStats.pending);
        const running = Number(this.jobStats && this.jobStats.running);
        if (Number.isFinite(pending) && Number.isFinite(running)) return pending + running;
      }

      const server = Array.isArray(this.jobListActive) ? this.jobListActive.length : 0;
      const extra = Array.isArray(this.jobsOptimisticActive) ? this.jobsOptimisticActive.length : 0;
      return server + extra;
    },

    _cleanupJobsOptimisticActive() {
      const now = Date.now();
      const maxAgeMs = 60 * 1000;
      const items = Array.isArray(this.jobsOptimisticActive) ? this.jobsOptimisticActive : [];
      this.jobsOptimisticActive = items.filter((item) => item && item.ts && now - item.ts < maxAgeMs);
    },

    _addJobsOptimisticActive(key, meta) {
      const normalizedKey = String(key || "").trim();
      if (!normalizedKey) return;
      if (!Array.isArray(this.jobsOptimisticActive)) this.jobsOptimisticActive = [];
      if (this.jobsOptimisticActive.some((item) => item && item.key === normalizedKey)) return;
      const safeMeta = meta && typeof meta === "object" ? meta : {};
      this.jobsOptimisticActive.push({ key: normalizedKey, ts: Date.now(), type: safeMeta.type ? String(safeMeta.type) : null });
      this._cleanupJobsOptimisticActive();
    },

    _removeJobsOptimisticActive(key) {
      const normalizedKey = String(key || "").trim();
      if (!normalizedKey) return;
      this.jobsOptimisticActive = (Array.isArray(this.jobsOptimisticActive) ? this.jobsOptimisticActive : []).filter(
        (item) => !(item && item.key === normalizedKey)
      );
    },

    _reconcileJobsOptimisticActiveByDelta(delta) {
      const next = Number(delta || 0);
      if (!Number.isFinite(next) || next <= 0) return;
      if (!Array.isArray(this.jobsOptimisticActive) || this.jobsOptimisticActive.length === 0) return;
      this.jobsOptimisticActive = this.jobsOptimisticActive.slice(Math.min(next, this.jobsOptimisticActive.length));
    },

    _reconcileJobsOptimisticActiveByJobs(jobs) {
      const list = Array.isArray(jobs) ? jobs : [];
      if (!Array.isArray(this.jobsOptimisticActive) || this.jobsOptimisticActive.length === 0 || !list.length) return;
      const resolved = new Set();
      for (const item of this.jobsOptimisticActive) {
        if (!item || !item.key || !item.ts || !item.type) continue;
        const since = Number(item.ts) - 5000;
        const ok = list.some((job) => {
          if (!job || !job.type) return false;
          if (String(job.type) !== String(item.type)) return false;
          const created = Date.parse(job.created_at || "");
          return Number.isFinite(created) && created >= since;
        });
        if (ok) resolved.add(String(item.key));
      }
      if (resolved.size) {
        this.jobsOptimisticActive = this.jobsOptimisticActive.filter((item) => !(item && resolved.has(String(item.key))));
      }
    },

    _disconnectJobStatsWs() {
      try {
        if (this.jobStatsWs) this.jobStatsWs.close();
      } catch {
        // ignore
      }
      this.jobStatsWs = null;
      this.jobStatsWsConnected = false;
    },

    _connectJobStatsWs() {
      if (this.jobStatsWs) return;
      try {
        if (this._jobStatsWsRetryTimer) clearTimeout(this._jobStatsWsRetryTimer);
      } catch {
        // ignore
      }
      this._jobStatsWsRetryTimer = null;

      const qs = new URLSearchParams();
      qs.set("interval_seconds", "1");
      qs.set("window_hours", "24");
      const url = wsUrl(`/api/ws/job_stats?${qs.toString()}`);

      const ws = new WebSocket(url);
      this.jobStatsWs = ws;
      this.jobStatsWsError = "";

      ws.onopen = () => {
        this.jobStatsWsConnected = true;
      };
      ws.onclose = () => {
        this.jobStatsWsConnected = false;
        this.jobStatsWs = null;
        try {
          if (this._jobStatsWsRetryTimer) clearTimeout(this._jobStatsWsRetryTimer);
        } catch {
          // ignore
        }
        this._jobStatsWsRetryTimer = setTimeout(() => this._connectJobStatsWs(), 1500);
      };
      ws.onerror = () => {
        this.jobStatsWsError = "ws error";
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data || "{}");
          if (!msg || msg.type !== "job_stats") return;
          this.jobStats = {
            pending: Number.isFinite(Number(msg.pending || 0)) ? Number(msg.pending || 0) : 0,
            running: Number.isFinite(Number(msg.running || 0)) ? Number(msg.running || 0) : 0,
            succeeded24h: Number.isFinite(Number(msg.succeeded_24h || 0)) ? Number(msg.succeeded_24h || 0) : 0,
            failed: Number.isFinite(Number(msg.failed || 0)) ? Number(msg.failed || 0) : 0,
          };
        } catch {
          // ignore
        }
      };
    },

    openJobsFromHeader(kind) {
      const next = String(kind || "").trim();
      let tab = "active";
      if (next === "succeeded_24h") tab = "succeeded";
      if (next === "failed") tab = "failed";

      if (tab !== "active") {
        this.jobsDoneFrom = "";
        this.jobsDoneTo = "";
      }

      if (this.activeView !== "jobs") {
        this.jobsTab = tab;
        this.switchView("jobs");
        return;
      }
      this.setJobsTab(tab);
    },

    setJobsTab(tab) {
      const prev = this.jobsTab;
      this.jobsTab = tab;
      this._syncUrl({ push: false });
      if (tab === "active") this._destroyJobsDoneChart();
      if (prev === "active" && tab !== "active") this.jobsSeriesLastAt = 0;
      this.refreshJobs();
    },

    _ensureJobsDoneRange() {
      if (this.jobsDoneFrom && this.jobsDoneTo) return;
      const now = new Date();
      const since = new Date(now.getTime() - 24 * 3600 * 1000);
      this.jobsDoneFrom = this._toLocalInputValue(since);
      this.jobsDoneTo = this._toLocalInputValue(now);
    },

    _disconnectJobsWs() {
      try {
        if (this.jobsWs) this.jobsWs.close();
      } catch {
        // ignore
      }
      this.jobsWs = null;
      this.jobsWsConnected = false;
    },

    _connectJobsWs() {
      if (this.jobsWs) return;
      const qs = new URLSearchParams();
      qs.set("status_in", "pending,running");
      qs.set("limit", "200");
      qs.set("interval_seconds", "1");
      if (this.jobsTypeFilter) qs.set("type", this.jobsTypeFilter);
      const ws = new WebSocket(wsUrl(`/api/ws/jobs?${qs.toString()}`));
      this.jobsWs = ws;
      this.jobsWsError = "";

      ws.onopen = () => {
        this.jobsWsConnected = true;
      };
      ws.onclose = () => {
        this.jobsWsConnected = false;
        this.jobsWs = null;
        if (this.activeView === "jobs") setTimeout(() => this._connectJobsWs(), 800);
      };
      ws.onerror = () => {
        this.jobsWsError = "WebSocket error";
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data || "{}");
          if (msg.type !== "jobs") return;
          const jobs = Array.isArray(msg.jobs) ? msg.jobs : [];
          const prevServer = Number(this._jobsActiveServerCount || 0);
          this._jobsActiveServerCount = jobs.length;
          const delta = jobs.length - prevServer;
          if (delta > 0) this._reconcileJobsOptimisticActiveByDelta(delta);
          this._reconcileJobsOptimisticActiveByJobs(jobs);

          const hidden = this.jobsHiddenActiveIds || {};
          const seen = new Set(jobs.map((job) => String(job && job.id)));
          for (const id of Object.keys(hidden || {})) {
            if (!seen.has(String(id))) delete hidden[id];
          }
          this.jobsHiddenActiveIds = hidden;

          const filtered = jobs.filter((job) => job && job.id && !hidden[String(job.id)]);
          this.jobListActive = filtered;
          this.ensureJobContextForList(filtered);
          this._cleanupJobsOptimisticActive();
        } catch {
          // ignore
        }
      };
    },

    async _fetchJobsActiveSnapshot({ force = false } = {}) {
      try {
        if (this.activeView !== "jobs") return;
        const now = Date.now();
        if (!force && this._jobsActiveLastFetchAt && now - this._jobsActiveLastFetchAt < 2500) return;
        this._jobsActiveLastFetchAt = now;

        const qs = new URLSearchParams();
        qs.set("status_in", "pending,running");
        qs.set("limit", "200");
        qs.set("offset", "0");
        if (this.jobsTypeFilter) qs.set("type", this.jobsTypeFilter);
        const items = await this.api(`/jobs?${qs.toString()}`);
        const jobs = Array.isArray(items) ? items : [];

        const prevServer = Number(this._jobsActiveServerCount || 0);
        this._jobsActiveServerCount = jobs.length;
        const delta = jobs.length - prevServer;
        if (delta > 0) this._reconcileJobsOptimisticActiveByDelta(delta);
        this._reconcileJobsOptimisticActiveByJobs(jobs);

        const hidden = this.jobsHiddenActiveIds || {};
        const seen = new Set(jobs.map((job) => String(job && job.id)));
        for (const id of Object.keys(hidden || {})) {
          if (!seen.has(String(id))) delete hidden[id];
        }
        this.jobsHiddenActiveIds = hidden;

        const filtered = jobs.filter((job) => job && job.id && !hidden[String(job.id)]);
        this.jobListActive = filtered;
        this.ensureJobContextForList(filtered);
        this._cleanupJobsOptimisticActive();
      } catch {
        // ignore
      }
    },

    jobProgressPct(job) {
      const cur = job && typeof job.progress_current === "number" ? job.progress_current : null;
      const total = job && typeof job.progress_total === "number" ? job.progress_total : null;
      if (!total || total <= 0 || cur == null) return null;
      const pct = Math.round((cur * 100) / total);
      return Math.max(0, Math.min(100, pct));
    },

    async loadJobsDone() {
      this.jobsDoneLoading = true;
      try {
        this._ensureJobsDoneRange();
        this._syncUrl({ push: false });

        const fromIso = this.jobsDoneFrom ? new Date(this.jobsDoneFrom).toISOString() : "";
        const toIso = this.jobsDoneTo ? new Date(this.jobsDoneTo).toISOString() : "";
        const statusIn = this.jobsTab === "succeeded" ? "succeeded" : "failed";

        const qs = new URLSearchParams();
        qs.set("status_in", statusIn);
        qs.set("limit", "200");
        qs.set("offset", "0");
        if (this.jobsTypeFilter) qs.set("type", this.jobsTypeFilter);
        if (fromIso) qs.set("finished_since", fromIso);
        if (toIso) qs.set("finished_until", toIso);

        const items = await this.api(`/jobs?${qs.toString()}`);
        const hidden = this.jobsHiddenDoneIds || {};
        const all = Array.isArray(items) ? items : [];
        this.jobListDone = all.filter((job) => job && job.id && !hidden[String(job.id)]);
        this.ensureJobContextForList(this.jobListDone);
      } finally {
        this.jobsDoneLoading = false;
      }
    },

    jobsTypeOptions() {
      const known = [
        "media.sync_profile",
        "media.sync_videos",
        "video.download",
        "video.extract_audio",
        "video.normalize_subtitle",
        "video.asr_transcribe",
        "video.generate_note",
        "brief.generate_daily",
      ];
      const set = new Set(known);
      for (const job of Array.isArray(this.jobListActive) ? this.jobListActive : []) {
        if (job && job.type) set.add(String(job.type));
      }
      for (const job of Array.isArray(this.jobListDone) ? this.jobListDone : []) {
        if (job && job.type) set.add(String(job.type));
      }
      return Array.from(set).filter(Boolean).sort();
    },

    _workersKnownRoleOrder() {
      return ["download_youtube", "download_bilibili", "audio", "process", "asr", "sync", "ai", "all"];
    },

    workerRoleLabel(role) {
      const value = String(role || "").trim().toLowerCase();
      const labels = {
        download_youtube: "下载YT",
        download_bilibili: "下载B站",
        audio: "音频",
        process: "处理",
        asr: "ASR",
        sync: "同步",
        ai: "AI",
        all: "ALL",
      };
      return labels[value] || value || "unknown";
    },

    workersRoleStatusList() {
      const roles = Array.isArray(this.workersRoles) ? this.workersRoles : [];
      const byRole = new Map();
      for (const item of roles) {
        const role = item && item.role ? String(item.role) : "";
        if (role) byRole.set(role, item);
      }

      const out = [];
      const known = this._workersKnownRoleOrder();
      const seen = new Set();
      for (const role of known) {
        out.push(byRole.get(role) || { role, online: 0, total: 0, last_seen_at: null });
        seen.add(role);
      }
      for (const item of roles) {
        const role = item && item.role ? String(item.role) : "";
        if (!role || seen.has(role)) continue;
        out.push(item);
      }
      return out;
    },

    workerRolePillClass(roleInfo) {
      const online = Number((roleInfo && roleInfo.online) || 0);
      const total = Number((roleInfo && roleInfo.total) || 0);
      if (!total || total <= 0) return "border-slate-700 bg-slate-950/20 text-slate-400";
      if (online > 0) return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
      return "border-rose-900/60 bg-rose-950/20 text-rose-200";
    },

    workerRoleDotClass(roleInfo) {
      const online = Number((roleInfo && roleInfo.online) || 0);
      const total = Number((roleInfo && roleInfo.total) || 0);
      if (!total || total <= 0) return "bg-slate-500";
      return online > 0 ? "bg-emerald-400" : "bg-rose-400";
    },

    workerRoleTitle(roleInfo) {
      const role = roleInfo && roleInfo.role ? String(roleInfo.role) : "";
      const lastSeen = roleInfo && roleInfo.last_seen_at ? String(roleInfo.last_seen_at) : "";
      const online = Number((roleInfo && roleInfo.online) || 0);
      const total = Number((roleInfo && roleInfo.total) || 0);
      const stale = Number(this._workersStaleAfterSeconds || 20);
      const windowSec = Number(this._workersWindowSeconds || 0);
      const parts = [`role=${role || "unknown"}`, `online=${online}/${total}`, `stale_after=${stale}s`];
      if (windowSec > 0) parts.push(`window=${windowSec}s`);
      if (lastSeen) parts.push(`last_seen_at=${lastSeen}`);
      return parts.join("  ");
    },

    async refreshWorkers({ force = false } = {}) {
      try {
        if (this.activeView !== "jobs" || this.jobsTab !== "active") return;
        const now = Date.now();
        if (!force && this._workersLastFetchAt && now - this._workersLastFetchAt < 3000) return;
        this._workersLastFetchAt = now;

        this.workersLoading = true;
        if (force) this.workersError = "";

        const payload = await this.api(`/workers`);
        this.workersRoles = payload && Array.isArray(payload.roles) ? payload.roles : [];
        const stale = payload && typeof payload.stale_after_seconds === "number" ? payload.stale_after_seconds : null;
        if (stale != null && isFinite(stale) && stale > 0) this._workersStaleAfterSeconds = Math.floor(stale);
        const windowSec = payload && typeof payload.window_seconds === "number" ? payload.window_seconds : null;
        if (windowSec != null && isFinite(windowSec) && windowSec > 0) this._workersWindowSeconds = Math.floor(windowSec);
      } catch (e) {
        this.workersError = e && e.message ? e.message : String(e);
      } finally {
        this.workersLoading = false;
      }
    },

    applyJobsTypeFilter() {
      this._disconnectJobsWs();
      this._syncUrl({ push: false });
      this.refreshJobs();
      this.refreshJobsSeries({ force: true });
    },

    clearJobsTypeFilter() {
      this.jobsTypeFilter = "";
      this.applyJobsTypeFilter();
    },

    async deleteFailedJobsBulk() {
      if (this.activeView !== "jobs" || this.jobsTab !== "failed" || this.jobsDeleteFailedSubmitting) return;
      this._ensureJobsDoneRange();

      const type = String(this.jobsTypeFilter || "").trim();
      const fromIso = this.jobsDoneFrom ? new Date(this.jobsDoneFrom).toISOString() : "";
      const toIso = this.jobsDoneTo ? new Date(this.jobsDoneTo).toISOString() : "";
      const details = [];
      if (type) details.push(`类型：${type}`);
      if (this.jobsDoneFrom && this.jobsDoneTo) details.push(`完成时间：${this.jobsDoneFrom} ~ ${this.jobsDoneTo}`);
      const detailText = details.length ? `\n\n${details.join("\n")}` : "";
      if (!confirm(`确认删除失败任务？${detailText}\n\n该操作不可恢复。`)) return;

      try {
        this.jobsDeleteFailedSubmitting = true;
        this.globalStatus = "正在删除失败任务…";
        const qs = new URLSearchParams();
        if (type) qs.set("type", type);
        if (fromIso) qs.set("finished_since", fromIso);
        if (toIso) qs.set("finished_until", toIso);
        const suffix = qs.toString() ? `?${qs.toString()}` : "";
        const res = await this.api(`/jobs/delete_failed${suffix}`, { method: "POST" });
        const deleted = res && typeof res.deleted === "number" ? res.deleted : 0;
        this.jobListDone = [];
        this.jobsSeriesLastAt = 0;
        await Promise.all([this.loadJobsDone(), this.refreshJobsSeries({ force: true })]);
        this.globalStatus = `已删除失败任务：${deleted}`;
        this.toastSuccess(`已删除失败任务：${deleted}`);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.globalStatus = `error: ${msg}`;
        this.toastError(`删除失败：${msg}`);
      } finally {
        this.jobsDeleteFailedSubmitting = false;
      }
    },

    async applyJobsRange() {
      if (this.activeView !== "jobs" || this.jobsTab === "active") return;
      this._syncUrl({ push: false });
      await Promise.all([this.loadJobsDone(), this.refreshJobsSeries({ force: true })]);
    },

    _jobsSeriesMax(series) {
      let max = 0;
      for (const point of Array.isArray(series) ? series : []) {
        const total = point && typeof point.total === "number" ? point.total : 0;
        if (total > max) max = total;
      }
      return max;
    },

    _destroyJobsDoneChart() {
      try {
        clearTimeout(this._jobsDoneChartRetryTimer);
      } catch {
        // ignore
      }
      this._jobsDoneChartRetryTimer = null;
      try {
        if (this.jobsDoneChart) this.jobsDoneChart.remove();
      } catch {
        // ignore
      }
      this.jobsDoneChart = null;
      this.jobsDoneSeries = null;
    },

    _ensureJobsDoneChart() {
      if (this.jobsDoneChart && this.jobsDoneSeries) return true;
      const el = this.$refs && this.$refs.jobsDoneChart;
      if (!el || el.clientWidth < 10 || el.clientHeight < 10) return false;
      const LC = window.LightweightCharts;
      if (!LC || typeof LC.createChart !== "function") return false;

      function JobsDoneStackedBarsRenderer() {
        this._bars = [];
        this._barSpacing = 6;
        this._visibleRange = null;
        this._conflationFactor = 1;
      }

      JobsDoneStackedBarsRenderer.prototype.update = function (data) {
        this._bars = (data && data.bars) || [];
        this._barSpacing = (data && data.barSpacing) || 6;
        this._visibleRange = (data && data.visibleRange) || null;
        this._conflationFactor = (data && data.conflationFactor) || 1;
      };

      JobsDoneStackedBarsRenderer.prototype.draw = function (target, priceToCoordinate) {
        const bars = Array.isArray(this._bars) ? this._bars : [];
        if (!bars.length) return;
        const visible = this._visibleRange;
        const from = visible && typeof visible.from === "number" ? Math.max(0, Math.floor(visible.from)) : 0;
        const to = visible && typeof visible.to === "number" ? Math.min(bars.length, Math.ceil(visible.to)) : bars.length;
        const spacing = (this._barSpacing || 6) * (this._conflationFactor || 1);
        const widthFactor = 0.72;

        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio, verticalPixelRatio }) => {
          const widthPx = Math.max(1, Math.floor(spacing * widthFactor * horizontalPixelRatio));
          const half = Math.floor(widthPx / 2);
          const y0v = priceToCoordinate(0);
          if (y0v == null) return;
          const y0 = Math.round(y0v * verticalPixelRatio);

          for (let i = from; i < to; i++) {
            const bar = bars[i];
            if (!bar || !bar.originalData) continue;
            const data = bar.originalData;
            const succeeded = Number(data.succeeded || 0);
            const failed = Number(data.failed || 0);
            const total = succeeded + failed;
            if (!total) continue;

            const x = Math.round(bar.x * horizontalPixelRatio);
            const left = x - half;
            const y1v = priceToCoordinate(succeeded);
            const y2v = priceToCoordinate(total);
            if (y1v == null || y2v == null) continue;

            const y1 = Math.round(y1v * verticalPixelRatio);
            const y2 = Math.round(y2v * verticalPixelRatio);

            const drawSeg = (yBottom, yTop, color) => {
              const top = Math.min(yBottom, yTop);
              const bottom = Math.max(yBottom, yTop);
              const height = bottom - top;
              if (height <= 0) return;
              context.fillStyle = color;
              context.fillRect(left, top, widthPx, height);
            };

            drawSeg(y0, y1, "rgba(16, 185, 129, 0.75)");
            drawSeg(y1, y2, "rgba(244, 63, 94, 0.75)");

            context.strokeStyle = "rgba(30, 41, 59, 0.55)";
            context.lineWidth = Math.max(1, Math.floor(horizontalPixelRatio));
            const outW = Math.max(1, widthPx - 1);
            const outH = Math.max(1, Math.abs(y0 - y2) - 1);
            context.strokeRect(left + 0.5, Math.min(y0, y2) + 0.5, outW, outH);
          }
        });
      };

      function JobsDoneStackedBarsPaneView() {
        this._renderer = new JobsDoneStackedBarsRenderer();
      }

      JobsDoneStackedBarsPaneView.prototype.renderer = function () {
        return this._renderer;
      };
      JobsDoneStackedBarsPaneView.prototype.update = function (data) {
        this._renderer.update(data);
      };
      JobsDoneStackedBarsPaneView.prototype.priceValueBuilder = function (row) {
        const succeeded = Number((row && row.succeeded) || 0);
        const failed = Number((row && row.failed) || 0);
        const total = succeeded + failed;
        return [0, total, total];
      };
      JobsDoneStackedBarsPaneView.prototype.isWhitespace = function (row) {
        return !row || row.time == null;
      };
      JobsDoneStackedBarsPaneView.prototype.defaultOptions = function () {
        return LC.customSeriesDefaultOptions;
      };

      const chart = LC.createChart(el, {
        autoSize: true,
        localization: {
          timeFormatter: (time) => {
            try {
              if (typeof time === "number") return new Date(time * 1000).toLocaleString();
              if (time && typeof time === "object" && typeof time.year === "number") {
                return new Date(time.year, (time.month || 1) - 1, time.day || 1).toLocaleDateString();
              }
              return String(time);
            } catch {
              return String(time);
            }
          },
        },
        layout: {
          background: { type: LC.ColorType.Solid, color: "rgba(0,0,0,0)" },
          textColor: "rgba(148, 163, 184, 0.85)",
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Trebuchet MS', Roboto, Ubuntu, sans-serif",
          fontSize: 11,
          attributionLogo: true,
        },
        rightPriceScale: { visible: false, scaleMargins: { top: 0.18, bottom: 0.1 } },
        leftPriceScale: { visible: false },
        grid: {
          vertLines: { visible: true, color: "rgba(30, 41, 59, 0.35)" },
          horzLines: { visible: true, color: "rgba(30, 41, 59, 0.35)" },
        },
        timeScale: {
          borderVisible: true,
          borderColor: "rgba(30, 41, 59, 0.55)",
          timeVisible: true,
          secondsVisible: false,
          tickMarkFormatter: (time, tickMarkType, locale) => {
            try {
              const loc = locale || undefined;
              let date = null;
              if (typeof time === "number") date = new Date(time * 1000);
              else if (time && typeof time === "object" && typeof time.year === "number") {
                date = new Date(time.year, (time.month || 1) - 1, time.day || 1);
              }
              if (!date || Number.isNaN(date.getTime())) return "";
              if (tickMarkType === LC.TickMarkType.DayOfMonth || tickMarkType === LC.TickMarkType.Month || tickMarkType === LC.TickMarkType.Year) {
                return date.toLocaleDateString(loc, { month: "2-digit", day: "2-digit" });
              }
              return date.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" });
            } catch {
              return "";
            }
          },
        },
        crosshair: { mode: LC.CrosshairMode.Hidden },
        handleScroll: false,
        handleScale: false,
      });

      const series = chart.addCustomSeries(new JobsDoneStackedBarsPaneView(), {
        lastValueVisible: false,
        priceLineVisible: false,
      });

      this.jobsDoneChart = chart;
      this.jobsDoneSeries = series;
      try {
        const range = this._jobsDoneRangeSeconds();
        if (range) chart.timeScale().setVisibleRange(range);
      } catch {
        // ignore
      }
      return true;
    },

    _jobsDoneRangeSeconds() {
      try {
        const fromSec = this.jobsDoneFrom ? Math.floor(new Date(this.jobsDoneFrom).getTime() / 1000) : null;
        const toSec = this.jobsDoneTo ? Math.floor(new Date(this.jobsDoneTo).getTime() / 1000) : null;
        if (fromSec == null || toSec == null) return null;
        if (!Number.isFinite(fromSec) || !Number.isFinite(toSec) || toSec <= fromSec) return null;
        return { from: fromSec, to: toSec };
      } catch {
        return null;
      }
    },

    _updateJobsDoneChart() {
      if (!this._ensureJobsDoneChart()) {
        if (this.activeView === "jobs" && this.jobsTab !== "active") {
          clearTimeout(this._jobsDoneChartRetryTimer);
          this._jobsDoneChartRetryTimer = setTimeout(() => this._updateJobsDoneChart(), 80);
        }
        return;
      }
      const series = this.jobsDoneSeries;
      const chart = this.jobsDoneChart;
      if (!series || !chart) return;

      const points = Array.isArray(this.jobsSeriesDone) ? this.jobsSeriesDone : [];
      const countsByMinute = new Map();
      for (const point of points) {
        const ts = point && point.ts ? new Date(point.ts) : null;
        if (!ts || Number.isNaN(ts.getTime())) continue;
        const sec = Math.floor(ts.getTime() / 1000);
        const minute = Math.floor(sec / 60) * 60;
        countsByMinute.set(minute, {
          succeeded: Number(point.succeeded || 0),
          failed: Number(point.failed || 0),
        });
      }

      const range = this._jobsDoneRangeSeconds();
      const data = [];
      if (range) {
        const start = Math.floor(range.from / 60) * 60;
        for (let t = start; t < range.to; t += 60) {
          const count = countsByMinute.get(t) || { succeeded: 0, failed: 0 };
          data.push({ time: t, ...count });
        }
      } else {
        for (const [time, count] of countsByMinute.entries()) data.push({ time, ...count });
        data.sort((a, b) => a.time - b.time);
      }

      series.setData(data);
      try {
        if (range) chart.timeScale().setVisibleRange(range);
        else chart.timeScale().fitContent();
      } catch {
        // ignore
      }
    },

    jobStatusPillClass(status) {
      const value = String(status || "").toLowerCase();
      if (value === "succeeded") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
      if (value === "failed") return "border-rose-500/30 bg-rose-500/10 text-rose-200";
      if (value === "canceled") return "border-slate-600 bg-slate-800/40 text-slate-200";
      if (value === "running") return "border-sky-500/30 bg-sky-500/10 text-sky-200";
      if (value === "pending") return "border-amber-500/30 bg-amber-500/10 text-amber-200";
      return "border-slate-700 bg-slate-950/30 text-slate-200";
    },

    jobStatusLabel(job) {
      const status = String((job && job.status) || "").toLowerCase() || "-";
      const pct = status === "running" ? this.jobProgressPct(job) : null;
      return pct != null ? `${status} · ${pct}%` : status;
    },

    jobMetaLabel(job) {
      if (!job || typeof job !== "object") return "";
      const type = job.type ? String(job.type) : "";
      if (type === "brief.generate_period" || type === "brief.generate_daily") {
        const playlistId = this.jobPlaylistId(job);
        const dateStr = this.jobPlaylistDate(job);
        const playlistLabel = playlistId ? this.jobPlaylistLabel(job) : "";
        return [playlistLabel, dateStr].filter(Boolean).join(" · ");
      }
      if (job.media_name) return String(job.media_name || "");
      const mediaId = job.params && job.params.media_id ? String(job.params.media_id) : "";
      if (!mediaId) return "";
      const media = (this.mediaIndex || []).find((item) => String(item.id) === mediaId);
      return media ? this.mediaDisplayName(media) : "";
    },

    jobParamId(job, key) {
      if (!job || typeof job !== "object") return "";
      const params = job.params && typeof job.params === "object" ? job.params : null;
      const raw = params && params[key] ? String(params[key]) : "";
      return String(raw || "").trim();
    },

    jobMediaId(job) {
      return this.jobParamId(job, "media_id");
    },

    jobVideoId(job) {
      return this.jobParamId(job, "video_id");
    },

    jobPlaylistId(job) {
      return this.jobParamId(job, "playlist_id");
    },

    jobPlaylistDate(job) {
      return this.jobParamId(job, "date") || this.jobParamId(job, "period_start");
    },

    jobVideoLabel(job) {
      const videoId = this.jobVideoId(job);
      if (!videoId) return "";
      const video = this.jobVideoById && this.jobVideoById[videoId] ? this.jobVideoById[videoId] : null;
      const title = video && video.title ? String(video.title).trim() : "";
      if (title) return title;
      const providerVideoId = video && video.provider_video_id ? String(video.provider_video_id).trim() : "";
      if (providerVideoId) return providerVideoId;
      return this._shortId(videoId);
    },

    jobPlaylistLabel(job) {
      const playlistId = this.jobPlaylistId(job);
      if (!playlistId) return "";
      const playlist = this.jobPlaylistById && this.jobPlaylistById[playlistId] ? this.jobPlaylistById[playlistId] : null;
      const name = playlist && playlist.name ? String(playlist.name).trim() : "";
      return name || this._shortId(playlistId);
    },

    ensureJobContextForList(list) {
      const jobs = Array.isArray(list) ? list : [];
      const videoIds = new Set();
      const playlistIds = new Set();
      for (const job of jobs) {
        const videoId = this.jobVideoId(job);
        if (videoId) videoIds.add(videoId);
        const playlistId = this.jobPlaylistId(job);
        if (playlistId) playlistIds.add(playlistId);
      }
      for (const videoId of videoIds) this._ensureJobVideo(videoId);
      for (const playlistId of playlistIds) this._ensureJobPlaylist(playlistId);
    },

    async _ensureJobVideo(videoId) {
      const normalized = String(videoId || "").trim();
      if (!normalized || (this.jobVideoById && this.jobVideoById[normalized])) return;
      if (this._jobVideoFetchInFlight && this._jobVideoFetchInFlight[normalized]) return;
      this._jobVideoFetchInFlight[normalized] = true;
      try {
        const video = await this.api(`/videos/${encodeURIComponent(normalized)}`);
        if (video && typeof video === "object") {
          if (!this.jobVideoById) this.jobVideoById = {};
          this.jobVideoById[normalized] = video;
        }
      } catch {
        // best-effort
      } finally {
        try {
          delete this._jobVideoFetchInFlight[normalized];
        } catch {
          this._jobVideoFetchInFlight[normalized] = false;
        }
      }
    },

    async _ensureJobPlaylist(playlistId) {
      const normalized = String(playlistId || "").trim();
      if (!normalized || (this.jobPlaylistById && this.jobPlaylistById[normalized])) return;
      if (this._jobPlaylistFetchInFlight && this._jobPlaylistFetchInFlight[normalized]) return;
      this._jobPlaylistFetchInFlight[normalized] = true;
      try {
        const playlist = await this.api(`/playlists/${encodeURIComponent(normalized)}`);
        if (playlist && typeof playlist === "object") {
          if (!this.jobPlaylistById) this.jobPlaylistById = {};
          this.jobPlaylistById[normalized] = playlist;
        }
      } catch {
        // best-effort
      } finally {
        try {
          delete this._jobPlaylistFetchInFlight[normalized];
        } catch {
          this._jobPlaylistFetchInFlight[normalized] = false;
        }
      }
    },

    async openJobVideo(job) {
      const videoId = this.jobVideoId(job);
      if (!videoId) return;
      try {
        if (!this.jobVideoById || !this.jobVideoById[videoId]) await this._ensureJobVideo(videoId);
        const video = this.jobVideoById && this.jobVideoById[videoId] ? this.jobVideoById[videoId] : null;
        if (video && video.id) await this.openVideoPlayer(video);
      } catch {
        // ignore
      }
    },

    openJobPlaylist(job) {
      const playlistId = this.jobPlaylistId(job);
      if (!playlistId) return;
      this.openPlaylistPage(playlistId, this.jobPlaylistDate(job));
    },

    async openJobMedia(job) {
      const mediaId = this.jobMediaId(job);
      if (!mediaId) return;
      this.mediaQuery = mediaId;
      this.switchView("media");
      await this.loadMedia();
    },

    async refreshJobsSeries({ force = false } = {}) {
      try {
        if (this.activeView !== "jobs" || this.jobsTab === "active") return;
        this._ensureJobsDoneRange();
        const now = Date.now();
        if (!force && this.jobsSeriesLastAt && now - this.jobsSeriesLastAt < 5000) return;

        const fromIso = this.jobsDoneFrom ? new Date(this.jobsDoneFrom).toISOString() : "";
        const toIso = this.jobsDoneTo ? new Date(this.jobsDoneTo).toISOString() : "";
        if (!fromIso || !toIso) return;
        this.jobsSeriesLastAt = now;
        this.jobsSeriesLoading = true;
        this.jobsSeriesError = "";

        const commonQs = new URLSearchParams();
        commonQs.set("since", fromIso);
        commonQs.set("until", toIso);
        commonQs.set("bucket", "minute");
        if (this.jobsTypeFilter) commonQs.set("type", this.jobsTypeFilter);

        const qsDone = new URLSearchParams(commonQs);
        qsDone.set("status_in", "succeeded,failed");
        qsDone.set("ts_field", "finished_at");

        const done = await this.api(`/jobs/series?${qsDone.toString()}`);
        const points = Array.isArray(done) ? done : [];
        this.jobsSeriesDone = points.map((point) => {
          const counts = (point && point.counts) || {};
          const succeeded = Number(counts.succeeded || 0);
          const failed = Number(counts.failed || 0);
          return { ts: point.ts, succeeded, failed, total: succeeded + failed };
        });
        this.jobsSeriesDoneMax = this._jobsSeriesMax(this.jobsSeriesDone);
        this._updateJobsDoneChart();
      } catch (e) {
        this.jobsSeriesError = e && e.message ? e.message : String(e);
      } finally {
        this.jobsSeriesLoading = false;
      }
    },

    async refreshJobs() {
      try {
        if (this.activeView !== "jobs") {
          this._disconnectJobsWs();
          return;
        }
        this._connectJobsWs();
        this._fetchJobsActiveSnapshot();
        this.refreshWorkers();

        if (this.jobsTab === "active") {
          this.jobListDone = [];
          return;
        }
        await this.loadJobsDone();
        this.refreshJobsSeries();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    async retryJob(jobId) {
      const id = String(jobId || "").trim();
      if (!id || (this.jobActionInFlight && this.jobActionInFlight[id])) return;

      const done = Array.isArray(this.jobListDone) ? this.jobListDone : [];
      const idx = done.findIndex((job) => job && String(job.id) === id);
      const snapshot = idx >= 0 ? done[idx] : null;
      const jobType = snapshot && snapshot.type ? String(snapshot.type) : null;

      if (!this.jobActionInFlight) this.jobActionInFlight = {};
      this.jobActionInFlight[id] = "retry";
      if (!this.jobsHiddenDoneIds) this.jobsHiddenDoneIds = {};
      this.jobsHiddenDoneIds[id] = Date.now();
      if (idx >= 0) this.jobListDone = done.filter((job) => job && String(job.id) !== id);
      this._addJobsOptimisticActive(`retry:${id}`, { type: jobType });

      try {
        this.globalStatus = "正在投递重试…";
        await this.api(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" });
        this.globalStatus = "已投递重试任务";
        if (this.activeView === "jobs" && this.jobsTab !== "active") this.refreshJobs();
      } catch (e) {
        try {
          if (this.jobsHiddenDoneIds) delete this.jobsHiddenDoneIds[id];
        } catch {
          // ignore
        }
        this._removeJobsOptimisticActive(`retry:${id}`);
        if (snapshot) {
          const cur = Array.isArray(this.jobListDone) ? this.jobListDone : [];
          const insertAt = Math.max(0, Math.min(idx, cur.length));
          this.jobListDone = cur.slice(0, insertAt).concat([snapshot]).concat(cur.slice(insertAt));
        }
        this.globalStatus = `error: ${e.message}`;
      } finally {
        try {
          if (this.jobActionInFlight) delete this.jobActionInFlight[id];
        } catch {
          // ignore
        }
      }
    },

    jobErrorDetailsText(jobId) {
      const id = String(jobId || "").trim();
      if (!id) return "";
      const details = this.jobErrorDetailsById ? this.jobErrorDetailsById[id] : null;
      if (!details) return "";
      const parts = [];
      const msg = details.error_message != null ? String(details.error_message).trim() : "";
      if (msg) parts.push(msg);
      const stack = details.error_stack != null ? String(details.error_stack).trim() : "";
      if (stack) parts.push(stack);
      return parts.filter(Boolean).join("\n\n");
    },

    async toggleJobErrorDetails(jobId) {
      const id = String(jobId || "").trim();
      if (!id) return;
      if (!this.jobErrorDetailsOpen) this.jobErrorDetailsOpen = {};
      const isOpen = !!this.jobErrorDetailsOpen[id];
      this.jobErrorDetailsOpen[id] = !isOpen;
      if (isOpen || (this.jobErrorDetailsById && this.jobErrorDetailsById[id])) return;
      await this.loadJobErrorDetails(id);
    },

    async loadJobErrorDetails(jobId) {
      const id = String(jobId || "").trim();
      if (!id) return;
      if (!this.jobErrorDetailsLoading) this.jobErrorDetailsLoading = {};
      if (!this.jobErrorDetailsError) this.jobErrorDetailsError = {};
      if (!this.jobErrorDetailsById) this.jobErrorDetailsById = {};
      if (this.jobErrorDetailsLoading[id]) return;
      this.jobErrorDetailsLoading[id] = true;
      this.jobErrorDetailsError[id] = "";
      try {
        this.jobErrorDetailsById[id] = (await this.api(`/jobs/${encodeURIComponent(id)}`)) || {};
      } catch (e) {
        this.jobErrorDetailsError[id] = e && e.message ? e.message : String(e);
      } finally {
        this.jobErrorDetailsLoading[id] = false;
      }
    },

    async cancelJob(jobId) {
      const id = String(jobId || "").trim();
      if (!id || (this.jobActionInFlight && this.jobActionInFlight[id])) return;

      if (!this.jobActionInFlight) this.jobActionInFlight = {};
      this.jobActionInFlight[id] = "cancel";
      if (!this.jobsHiddenActiveIds) this.jobsHiddenActiveIds = {};
      this.jobsHiddenActiveIds[id] = Date.now();

      try {
        this.globalStatus = "正在取消…";
        await this.api(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
        this.globalStatus = "已取消任务";
      } catch (e) {
        try {
          if (this.jobsHiddenActiveIds) delete this.jobsHiddenActiveIds[id];
        } catch {
          // ignore
        }
        this.globalStatus = `error: ${e.message}`;
      } finally {
        try {
          if (this.jobActionInFlight) delete this.jobActionInFlight[id];
        } catch {
          // ignore
        }
      }
    },
  };
}
