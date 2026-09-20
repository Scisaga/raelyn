const USAGE_TREND_DAYS = 90;

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
      textColor: "#718096",
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
      fontSize: 11,
      attributionLogo: false,
    },
    rightPriceScale: {
      borderVisible: false,
      minimumWidth: 68,
      scaleMargins: { top: 0.18, bottom: 0.12 },
    },
    leftPriceScale: { visible: false },
    grid: {
      vertLines: { visible: false },
      horzLines: { visible: true, color: "rgba(51, 65, 85, 0.22)" },
    },
    timeScale: {
      borderVisible: false,
      timeVisible: false,
      secondsVisible: false,
      fixLeftEdge: true,
      fixRightEdge: true,
      lockVisibleTimeRangeOnResize: true,
      tickMarkFormatter: (time) => usageChartTimeLabel(time).slice(5).replace("-", "/"),
    },
    crosshair: {
      mode: LC.CrosshairMode.Normal,
      vertLine: { color: "rgba(148, 163, 184, 0.4)", labelVisible: false },
      horzLine: { visible: false, labelVisible: false },
    },
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
        llmUsageMissingCalls: nullableNumber(summary.llm_usage_missing_calls),
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
        llmUsageMissingCalls: nullableNumber(point.llm_usage_missing_calls),
        assetSizeBytes: nullableNumber(point.asset_size_bytes ?? point.asset_size_estimate_bytes),
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
      })).sort((a, b) => b.sizeBytes - a.sizeBytes);
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

    async loadUsage() {
      const requestToken = Number(this._usageRequestToken || 0) + 1;
      this._usageRequestToken = requestToken;
      this.usageLoading = true;
      this.usageError = "";

      try {
        const payload = await this.api(`/usage?days=${USAGE_TREND_DAYS}`);
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

    refreshUsage() {
      return this.loadUsage();
    },

    leaveUsagePage() {
      this._usageRequestToken = Number(this._usageRequestToken || 0) + 1;
      this.usageLoading = false;
      this.$refs?.usageTokenDialog?.close();
      this.$refs?.usageCollectionDialog?.close();
      this._destroyUsageCharts();
    },

    usageRangeLabel() {
      return `最近 ${USAGE_TREND_DAYS} 天`;
    },

    usageCallTrendWindow() {
      return 7;
    },

    usageCallTrendGranularityLabel() {
      const windowDays = this.usageCallTrendWindow();
      return windowDays === 1 ? "每日原值" : `${windowDays} 日移动均值`;
    },

    usageCountLabel(value) {
      if (!this.usagePayload) return this.usageLoading ? "加载中…" : "—";
      return value === null ? "未采集" : this.formatCompactInteger(value);
    },

    usageMetricParts(value, kind = "count") {
      if (!this.usagePayload) return { value: this.usageLoading ? "加载中…" : "—", unit: "" };
      if (value === null) return { value: kind === "bytes" ? "不可用" : "未采集", unit: "" };
      if (kind === "bytes") {
        const [number, unit] = this.formatBytes(value).split(" ");
        return { value: number, unit };
      }
      if (value >= 1e8) return { value: (value / 1e8).toFixed(2), unit: "亿" };
      if (value >= 1e4) return { value: (value / 1e4).toFixed(2), unit: "万" };
      return { value: this.formatInteger(value), unit: "" };
    },

    usageTokenCoverageLabel() {
      const missing = this.usageSummary.llmUsageMissingCalls;
      if (missing === null) return "LLM 用量尚未采集";
      return missing > 0
        ? `${this.formatInteger(missing)} 次调用缺失用量，按 0 计；已记录的 Token 全部保留。`
        : "已采集调用的 Token 用量均已记录。";
    },

    usageShortDateTimeLabel(value) {
      if (!value) return "未采集";
      return new Intl.DateTimeFormat("zh-CN", {
        timeZone: this.usageTimezone || undefined,
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
      }).format(new Date(value));
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
      const maximum = this.usageSummary.assetSizeBytes;
      const current = Math.max(0, Number(row && row.sizeBytes || 0));
      if (maximum <= 0 || current <= 0) return 0;
      return current * 100 / maximum;
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
        thumbnail: "缩略图",
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
      if (value < 60000) return `${(value / 1000).toFixed(1)} 秒`;
      const minutes = Math.floor(value / 60000);
      if (minutes < 60) return `${minutes} 分 ${Math.floor(value / 1000) % 60} 秒`;
      const hours = Math.floor(minutes / 60);
      if (hours < 24) return `${hours} 小时 ${minutes % 60} 分`;
      return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`;
    },

    usageChartDate(key) {
      return this.usageChartDates[key] || this.usageSeries.at(-1)?.date || "";
    },

    usageChartDateLabel(key) {
      const date = this.usageChartDate(key);
      if (!date) return "—";
      const label = date.slice(5).replace("-", "/");
      return `${label}${date === this.usageSeries.at(-1)?.date ? " · 当日" : ""}`;
    },

    usageChartValue(key, field) {
      const sampledField = key === "calls" || key === "tokens" ? "usageSampled"
        : field === "databaseSizeBytes" ? "resourceSampled" : "";
      const data = key === "calls" ? this._usageCallTrendData(field, sampledField)
        : this._usageMetricData(field, sampledField);
      const point = data.find((item) => item.time === this.usageChartDate(key));
      if (point?.value === undefined) return "—";
      return key === "storage" ? this.formatBytes(point.value) : this.formatCompactInteger(point.value);
    },

    usageAssetShareLabel(row) {
      const share = this.usageAssetShare(row);
      return share > 0 && share < 0.1 ? "<0.1%" : `${share.toFixed(1)}%`;
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
        (point) => point.assetSizeBytes !== null || point.databaseSizeBytes !== null
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
      entry.chart.subscribeCrosshairMove(({ time }) => {
        this.usageChartDates = { ...this.usageChartDates, [key]: usageChartTimeLabel(time) };
      });
      this.usageCharts = { ...this.usageCharts, [key]: entry };
      return entry;
    },

    _usageEnsureCharts() {
      const LC = window.LightweightCharts;
      if (!LC || typeof LC.createChart !== "function") return false;

      let ready = true;
      if (this.usageHasVideoSeries()) {
        const entry = this._usageCreateChart("videos", "usageVideoChart", (value) => this.formatCompactInteger(value));
        ready = Boolean(entry) && ready;
        if (entry && !entry.series.videoDownloaded) {
          entry.series.videoDownloaded = entry.chart.addSeries(LC.HistogramSeries, {
            color: "rgba(98, 213, 237, 0.55)",
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
            color: "rgba(176, 155, 255, 0.9)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
          entry.series.asrCalls = entry.chart.addSeries(LC.LineSeries, {
            color: "rgba(104, 214, 192, 0.9)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
          entry.series.embeddingCalls = entry.chart.addSeries(LC.LineSeries, {
            color: "rgba(108, 205, 163, 0.9)",
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
            lineColor: "rgba(104, 214, 192, 0.9)",
            topColor: "rgba(104, 214, 192, 0.07)",
            bottomColor: "rgba(104, 214, 192, 0)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
          entry.series.outputTokens = entry.chart.addSeries(LC.AreaSeries, {
            lineColor: "rgba(239, 202, 123, 0.9)",
            topColor: "rgba(239, 202, 123, 0.05)",
            bottomColor: "rgba(239, 202, 123, 0)",
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
            color: "rgba(115, 182, 255, 0.9)",
            lineWidth: 2,
            lastValueVisible: false,
            priceLineVisible: false,
          });
          entry.series.databaseSize = entry.chart.addSeries(LC.LineSeries, {
            color: "rgba(108, 205, 163, 0.9)",
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
        storage.series.assetSize.setData(this._usageMetricData("assetSizeBytes"));
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
      this.usageChartDates = {};
    },
  };
}
