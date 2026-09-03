const USAGE_DAY_OPTIONS = new Set([7, 30, 90]);

function nullableNumber(value) {
  return value === null || value === undefined ? null : Number(value);
}

function usageChartTimeLabel(time) {
  if (time && typeof time === "object" && Number.isInteger(time.year)) {
    const month = String(time.month || 1).padStart(2, "0");
    const day = String(time.day || 1).padStart(2, "0");
    return `${time.year}-${month}-${day}`;
  }
  return String(time || "");
}

function usageChartOptions(LC, priceFormatter) {
  return {
    autoSize: true,
    localization: {
      priceFormatter,
      timeFormatter: usageChartTimeLabel,
    },
    layout: {
      background: { type: LC.ColorType.Solid, color: "rgba(0,0,0,0)" },
      textColor: "rgba(148, 163, 184, 0.85)",
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
      fontSize: 11,
      attributionLogo: false,
    },
    rightPriceScale: {
      borderVisible: true,
      borderColor: "rgba(51, 65, 85, 0.55)",
      scaleMargins: { top: 0.18, bottom: 0.12 },
    },
    leftPriceScale: { visible: false },
    grid: {
      vertLines: { visible: true, color: "rgba(30, 41, 59, 0.35)" },
      horzLines: { visible: true, color: "rgba(30, 41, 59, 0.35)" },
    },
    timeScale: {
      borderVisible: true,
      borderColor: "rgba(51, 65, 85, 0.55)",
      timeVisible: false,
      secondsVisible: false,
      fixLeftEdge: true,
      fixRightEdge: true,
    },
    crosshair: { mode: LC.CrosshairMode.Normal },
    handleScroll: false,
    handleScale: false,
  };
}

export function createUsageViewMethods() {
  return {
    applyUsagePayload(payload) {
      const summary = payload.summary;
      const collection = payload.collection;

      this.usagePayload = payload;
      this.usageGeneratedAt = payload.generated_at;
      this.usageTimezone = payload.timezone;
      this.usageWindow = payload.window;
      this.usageSummary = {
        downloadedVideoCount: Number(summary.downloaded_video_count),
        videoDownloaded: Number(summary.video_downloaded),
        externalCalls: nullableNumber(summary.external_calls),
        llmCalls: nullableNumber(summary.llm_calls),
        asrCalls: nullableNumber(summary.asr_calls),
        embeddingCalls: nullableNumber(summary.embedding_calls),
        llmInputTokens: nullableNumber(summary.llm_input_tokens),
        llmOutputTokens: nullableNumber(summary.llm_output_tokens),
        llmTotalTokens: nullableNumber(summary.llm_total_tokens),
        assetCount: Number(summary.asset_count),
        assetSizeBytes: Number(summary.asset_size_bytes),
        assetMissingSizeCount: Number(summary.asset_missing_size_count),
        databaseSizeBytes: nullableNumber(summary.database_size_bytes),
      };
      this.usageSeries = payload.series.map((point) => ({
        date: point.date,
        videoDownloaded: Number(point.video_downloaded),
        externalCalls: nullableNumber(point.external_calls),
        llmCalls: nullableNumber(point.llm_calls),
        asrCalls: nullableNumber(point.asr_calls),
        embeddingCalls: nullableNumber(point.embedding_calls),
        llmInputTokens: nullableNumber(point.llm_input_tokens),
        llmOutputTokens: nullableNumber(point.llm_output_tokens),
        llmTotalTokens: nullableNumber(point.llm_total_tokens),
        assetSizeBytes: nullableNumber(point.asset_size_bytes),
        databaseSizeBytes: nullableNumber(point.database_size_bytes),
        usageSampled: Boolean(point.usage_sampled),
        resourceSampled: Boolean(point.resource_sampled),
      }));
      this.usageServiceBreakdown = payload.service_breakdown.map((row) => ({
        service: row.service,
        operation: row.operation,
        provider: row.provider,
        model: row.model,
        calls: Number(row.calls),
        successes: Number(row.successes),
        failures: Number(row.failures),
        inputTokens: Number(row.input_tokens),
        outputTokens: Number(row.output_tokens),
        totalTokens: Number(row.total_tokens),
        durationMs: Number(row.duration_ms),
        usageMissingCalls: Number(row.usage_missing_calls),
        lastCalledAt: row.last_called_at,
      }));
      this.usageAssetBreakdown = payload.asset_breakdown.map((row) => ({
        assetType: row.asset_type,
        count: Number(row.count),
        sizeBytes: Number(row.size_bytes),
        missingSizeCount: Number(row.missing_size_count),
      }));
      this.usageCollection = {
        usageStartedAt: collection.usage_started_at,
        lastUsageAt: collection.last_usage_at,
        resourceStartedAt: collection.resource_started_at,
        lastResourceSnapshotAt: collection.last_resource_snapshot_at,
        physicalStorageAvailable: Boolean(collection.physical_storage_available),
        physicalStorageReason: collection.physical_storage_reason,
      };
      return payload;
    },

    async loadUsage({ days = this.usageDays } = {}) {
      const normalizedDays = Number(days);
      if (!USAGE_DAY_OPTIONS.has(normalizedDays)) return null;

      this.usageDays = normalizedDays;
      const requestToken = Number(this._usageRequestToken || 0) + 1;
      this._usageRequestToken = requestToken;
      this.usageLoading = true;
      this.usageError = "";

      try {
        const payload = await this.api(`/usage?days=${normalizedDays}`);
        if (requestToken !== this._usageRequestToken || this.activeView !== "usage") return null;
        this.applyUsagePayload(payload);
        if (String(this.globalStatus || "").startsWith("error:")) this.globalStatus = "";
        this._updateUsageCharts();
        return payload;
      } catch (error) {
        if (requestToken !== this._usageRequestToken) return null;
        const message = error && error.message ? error.message : String(error);
        this.usageError = message;
        this.globalStatus = `error: ${message}`;
        return null;
      } finally {
        if (requestToken === this._usageRequestToken) this.usageLoading = false;
      }
    },

    async setUsageDays(days) {
      const normalizedDays = Number(days);
      if (!USAGE_DAY_OPTIONS.has(normalizedDays)) return null;
      if (normalizedDays === this.usageDays && this.usagePayload) return this.usagePayload;
      return this.loadUsage({ days: normalizedDays });
    },

    refreshUsage() {
      return this.loadUsage({ days: this.usageDays });
    },

    leaveUsagePage() {
      this._usageRequestToken = Number(this._usageRequestToken || 0) + 1;
      this.usageLoading = false;
      this._destroyUsageCharts();
    },

    usageRangeLabel() {
      return `最近 ${this.usageDays} 天`;
    },

    usageCallTrendWindow() {
      if (this.usageDays >= 90) return 7;
      if (this.usageDays >= 30) return 3;
      return 1;
    },

    usageCallTrendGranularityLabel() {
      const windowDays = this.usageCallTrendWindow();
      return windowDays === 1 ? "每日原值" : `${windowDays} 日移动均值`;
    },

    usageCountLabel(value) {
      if (!this.usagePayload) return this.usageLoading ? "加载中…" : "—";
      return value === null ? "暂无采样" : this.formatCompactInteger(value);
    },

    usageBytesLabel(value) {
      if (!this.usagePayload) return this.usageLoading ? "加载中…" : "—";
      return value === null ? "暂无采样" : this.formatBytes(value);
    },

    usageDatabaseSizeLabel() {
      if (!this.usagePayload) return this.usageLoading ? "加载中…" : "—";
      return this.usageSummary.databaseSizeBytes === null
        ? "不可用"
        : this.formatBytes(this.usageSummary.databaseSizeBytes);
    },

    usageDateTimeLabel(value) {
      if (!value) return "暂无采样";
      return new Intl.DateTimeFormat(undefined, {
        timeZone: this.usageTimezone || undefined,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(new Date(value));
    },

    usageDateLabel(value) {
      if (!value) return "暂无采样";
      const matched = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
      return matched ? `${matched[1]}/${matched[2]}/${matched[3]}` : String(value);
    },

    usageGeneratedLabel() {
      if (!this.usagePayload) return this.usageLoading ? "正在加载资源用量…" : "尚未加载";
      const timezone = this.usageTimezone ? ` · ${this.usageTimezone}` : "";
      return `更新于 ${this.usageDateTimeLabel(this.usageGeneratedAt)}${timezone}`;
    },

    usageCollectionModeLabel() {
      const operations = this.usageServiceBreakdown.map((row) => String(row.operation || ""));
      const hasLegacy = operations.some((operation) => operation.startsWith("legacy."));
      const hasLive = operations.some((operation) => !operation.startsWith("legacy."));
      if (hasLegacy && hasLive) return "历史 + 实时";
      if (hasLegacy) return "历史已回填";
      if (hasLive) return "实时采集中";
      return "等待首个调用";
    },

    usageAssetShare(row) {
      const maximum = Math.max(0, ...this.usageAssetBreakdown.map((item) => Number(item.sizeBytes || 0)));
      const current = Math.max(0, Number(row && row.sizeBytes || 0));
      if (maximum <= 0 || current <= 0) return 0;
      return Math.max(3, Math.min(100, current * 100 / maximum));
    },

    usageOperationLabel(operation) {
      const value = String(operation || "");
      const labels = {
        "legacy.brief_generation": "历史简报生成",
        "legacy.event_extraction": "历史事件抽取",
        "legacy.transcript_polish": "历史转写润色",
        "legacy.video_transcription": "历史视频转写",
        brief_generation: "简报生成",
        brief_reduction: "简报归并",
        event_extraction: "事件抽取",
        transcript_polish: "转写润色",
        event_embedding: "事件向量",
        event_embedding_backfill: "向量回填",
        video_transcription: "视频转写",
      };
      return labels[value] || value || "未知操作";
    },

    usageProviderLabel(row) {
      if (String(row && row.operation || "").startsWith("legacy.")) return "历史任务记录";
      const provider = String(row && row.provider || "").trim();
      const model = String(row && row.model || "").trim();
      return [provider, model].filter(Boolean).join(" / ") || "未标注 Provider";
    },

    usageAssetLabel(assetType) {
      const value = String(assetType || "");
      return ({
        audio: "音频",
        brief: "简报",
        image: "图像",
        subtitle: "字幕",
        transcript: "转写文本",
        video: "视频",
      })[value] || value || "其他资产";
    },

    usageSuccessRateLabel(row) {
      const calls = Math.max(0, Number(row && row.calls || 0));
      if (calls <= 0) return "—";
      return `${(Math.max(0, Number(row.successes || 0)) * 100 / calls).toFixed(1)}%`;
    },

    usageDurationLabel(durationMs, operation = "") {
      const value = Number(durationMs);
      if (value <= 0 && String(operation || "").startsWith("legacy.")) return "历史未记录";
      if (value < 1000) return `${this.formatInteger(value)} ms`;
      return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)} 秒`;
    },

    usageHasVideoSeries() {
      return this.usageSeries.some((point) => point.videoDownloaded !== null);
    },

    usageHasCallsSeries() {
      return this.usageSeries.some(
        (point) => point.usageSampled && (
          point.llmCalls !== null
          || point.asrCalls !== null
          || point.embeddingCalls !== null
        )
      );
    },

    usageHasTokenSeries() {
      return this.usageSeries.some(
        (point) => point.usageSampled && (point.llmInputTokens !== null || point.llmOutputTokens !== null)
      );
    },

    usageHasStorageSeries() {
      return this.usageSeries.some(
        (point) => point.resourceSampled && (point.assetSizeBytes !== null || point.databaseSizeBytes !== null)
      );
    },

    _usageMetricData(field, sampledField = "") {
      return this.usageSeries.map((point) => {
        if ((sampledField && !point[sampledField]) || point[field] === null) return { time: point.date };
        return { time: point.date, value: point[field] };
      });
    },

    _usageCallTrendData(field, sampledField = "") {
      const windowDays = this.usageCallTrendWindow();
      const values = [];
      return this.usageSeries.map((point) => {
        if ((sampledField && !point[sampledField]) || point[field] === null) {
          values.length = 0;
          return { time: point.date };
        }
        values.push(Number(point[field]));
        if (values.length > windowDays) values.shift();
        const average = values.reduce((total, value) => total + value, 0) / values.length;
        return { time: point.date, value: Math.round(average * 100) / 100 };
      });
    },

    _usageCreateChart(key, refName, priceFormatter) {
      const current = this.usageCharts[key];
      if (current) return current;

      const element = this.$refs && this.$refs[refName];
      if (!element || element.clientWidth < 10 || element.clientHeight < 10) return null;
      const LC = window.LightweightCharts;
      if (!LC || typeof LC.createChart !== "function") return null;

      const entry = {
        chart: LC.createChart(element, usageChartOptions(LC, priceFormatter)),
        series: {},
      };
      this.usageCharts = { ...this.usageCharts, [key]: entry };
      return entry;
    },

    _usageEnsureCharts() {
      const LC = window.LightweightCharts;
      if (!LC || typeof LC.createChart !== "function") return false;

      let ready = true;
      if (this.usageHasVideoSeries()) {
        const entry = this._usageCreateChart("videos", "usageVideoChart", (value) => this.formatInteger(value));
        ready = Boolean(entry) && ready;
        if (entry && !entry.series.videoDownloaded) {
          entry.series.videoDownloaded = entry.chart.addSeries(LC.AreaSeries, {
            lineColor: "rgba(34, 211, 238, 0.92)",
            topColor: "rgba(34, 211, 238, 0.10)",
            bottomColor: "rgba(34, 211, 238, 0.005)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
        }
      }
      if (this.usageHasCallsSeries()) {
        const entry = this._usageCreateChart("calls", "usageCallsChart", (value) => this.formatCompactInteger(value));
        ready = Boolean(entry) && ready;
        if (entry && !entry.series.llmCalls) {
          entry.series.llmCalls = entry.chart.addSeries(LC.LineSeries, {
            color: "rgba(167, 139, 250, 0.95)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
          entry.series.asrCalls = entry.chart.addSeries(LC.LineSeries, {
            color: "rgba(45, 212, 191, 0.95)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
          entry.series.embeddingCalls = entry.chart.addSeries(LC.LineSeries, {
            color: "rgba(52, 211, 153, 0.95)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
        }
      }
      if (this.usageHasTokenSeries()) {
        const entry = this._usageCreateChart("tokens", "usageTokensChart", (value) => this.formatCompactInteger(value));
        ready = Boolean(entry) && ready;
        if (entry && !entry.series.inputTokens) {
          entry.series.inputTokens = entry.chart.addSeries(LC.AreaSeries, {
            lineColor: "rgba(45, 212, 191, 0.98)",
            topColor: "rgba(45, 212, 191, 0.20)",
            bottomColor: "rgba(45, 212, 191, 0.01)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
          entry.series.outputTokens = entry.chart.addSeries(LC.AreaSeries, {
            lineColor: "rgba(251, 191, 36, 0.98)",
            topColor: "rgba(251, 191, 36, 0.15)",
            bottomColor: "rgba(251, 191, 36, 0.01)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
        }
      }
      if (this.usageHasStorageSeries()) {
        const entry = this._usageCreateChart("storage", "usageStorageChart", (value) => this.formatBytes(value));
        ready = Boolean(entry) && ready;
        if (entry && !entry.series.assetSize) {
          entry.series.assetSize = entry.chart.addSeries(LC.LineSeries, {
            color: "rgba(56, 189, 248, 0.95)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
          entry.series.databaseSize = entry.chart.addSeries(LC.LineSeries, {
            color: "rgba(52, 211, 153, 0.95)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
        }
      }
      return ready;
    },

    _updateUsageCharts() {
      if (this.activeView !== "usage") return;
      if (!window.LightweightCharts) {
        this.ensureLightweightCharts()
          .then(() => this._updateUsageCharts())
          .catch((error) => {
            this.usageChartError = error && error.message ? error.message : String(error);
          });
        return;
      }
      if (!this._usageEnsureCharts()) {
        clearTimeout(this._usageChartRetryTimer);
        this._usageChartRetryTimer = setTimeout(() => this._updateUsageCharts(), 80);
        return;
      }

      this.usageChartError = "";
      const videos = this.usageCharts.videos;
      if (videos) videos.series.videoDownloaded.setData(this._usageMetricData("videoDownloaded"));

      const calls = this.usageCharts.calls;
      if (calls) {
        calls.series.llmCalls.setData(this._usageCallTrendData("llmCalls", "usageSampled"));
        calls.series.asrCalls.setData(this._usageCallTrendData("asrCalls", "usageSampled"));
        calls.series.embeddingCalls.setData(this._usageCallTrendData("embeddingCalls", "usageSampled"));
      }

      const tokens = this.usageCharts.tokens;
      if (tokens) {
        tokens.series.inputTokens.setData(this._usageMetricData("llmInputTokens", "usageSampled"));
        tokens.series.outputTokens.setData(this._usageMetricData("llmOutputTokens", "usageSampled"));
      }

      const storage = this.usageCharts.storage;
      if (storage) {
        storage.series.assetSize.setData(this._usageMetricData("assetSizeBytes", "resourceSampled"));
        storage.series.databaseSize.setData(this._usageMetricData("databaseSizeBytes", "resourceSampled"));
      }

      Object.values(this.usageCharts).forEach((entry) => entry.chart.timeScale().fitContent());
    },

    _destroyUsageCharts() {
      clearTimeout(this._usageChartRetryTimer);
      this._usageChartRetryTimer = null;
      Object.values(this.usageCharts || {}).forEach((entry) => {
        try {
          entry.chart.remove();
        } catch {
          // 图表已经释放时无需重复处理。
        }
      });
      this.usageCharts = {};
    },
  };
}
