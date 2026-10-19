export function createSettingsViewMethods({ ytdlpFormatPreset1080, ytdlpFormatPreset720 }) {
  return {
    briefDefaultDailyPrompt() {
      return [
        "## 提示词",
        "",
        "你是一个**财经内容分析助手**。请基于下面提供的多条视频文字内容（可能含转写、字幕、摘要、片段拼接），生成一份可直接发布的**Markdown**财经简报。",
        "",
        "### 核心约束（必须遵守）",
        "",
        "1. **只使用文本中明确出现的信息**：",
        "",
        "   * 不要补充常识性“背景”来充当事实。",
        "   * 任何无法从文本直接验证的内容，一律写：**“文本未提及”** 或 **“文本表述不充分，无法确认”**。",
        "2. **每条要点必须附 1–3 个来源链接**：",
        "",
        "   * 来源链接必须来自文本中的视频链接/来源字段。",
        "   * 统一写法：句末用 `（来源：https://...，https://...）`（可用纯 URL 或 Markdown 链接）。",
        "3. 输出语言：**中文**。",
        "4. 输出必须是 **Markdown**，段落清晰，便于直接发布。",
        "5. 不需要：**主体归类**、**今日视频清单**。",
        "6. **不要输出“总标题/日期/分割线”**：",
        "",
        "   * 不要输出 H1（例如 `# ...`）或任何“总标题”。",
        "   * 不要输出“每日财经简报”字样。",
        "   * 不要输出“日期：...”或任何单独的日期行。",
        "   * 不要输出 Markdown 水平分割线（例如 `---`）。",
        "",
        "### 输出结构",
        "",
        "你必须严格按以下结构输出，并且**标题行必须用 Markdown 标题语法，单独成行**（不要写成正文里的小标题）：",
        "",
        "## 今日要点",
        "",
        "* 用 **bullet** 列出关键信息点（只写要点，不要写散文段落）。",
        "* 每条要点：",
        "",
        "  * 句式尽量短，先给“结论/信息”，再给“条件/范围/时间”。",
        "  * 必须在句末附 **1–3 个来源链接**，格式：`（来源：...）`。",
        "  * 若文本出现具体数值（涨跌幅、利率、通胀、盈利、库存、产量等），必须原样保留，并注明它属于谁/哪个时间窗口；若时间窗口不清楚，写“文本未提及”。",
        "",
        "## 影响与逻辑链",
        "",
        "* 写清楚“**因 → 果**”或“**事件 → 资产影响**”的链条。",
        "* 每条链条必须满足：链条中的每个关键节点都能在文本中找到依据；找不到就标注“文本未提及”。",
        "* 仍然要在句末附来源链接。",
        "",
        "## 风险与不确定性",
        "",
        "* 重点写：口径不一致、数据缺失、时间不明、推断过度、样本偏差、叙述互相矛盾之处。",
        "* 如不同视频说法冲突：明确写出“视频 A 说…；视频 B 说…；无法判定”（并分别给来源）。",
        "",
        "## 关注清单",
        "",
        "* 给出“接下来应继续跟踪”的观察项。",
        "* 每一条都要说明：为什么要跟踪（依据文本哪个说法），并附来源链接。",
        "* 如果文本没有足够依据，写“文本未提及”。",
        "",
        "## 行动建议",
        "",
        "* 给“**条件触发式**”建议。",
        "* **不允许直接给确定性买卖指令**；只能写“可考虑/可关注/需验证”。",
        "* 每条建议仍需来源链接；若建议的关键条件缺失，标注“文本未提及”。",
        "",
        "简报日期（仅供你理解上下文，不要在输出中出现）：{{date}}",
        "",
        "以下是视频文本（多条，可能包含标题与链接；若未包含链接，请在输出中把来源写为“文本未提供链接”）：",
        "{{blocks}}",
      ].join("\n").trim();
    },

    async loadSettings({ force = false } = {}) {
      await this.loadInferenceSettings({ force });
      await this.loadYtdlpCookies({ force });
      await this.loadYtdlpSubtitles({ force });
      await this.loadYtdlpMembersOnly({ force });
      await this.loadYtdlpFormat({ force });
      await this.loadLlmPolishPrompt({ force });
      await this.loadStaleVideosCleanup({ force });
    },

    inferenceModeSourceText(source) {
      const key = String(source || "").trim().toLowerCase();
      if (key === "app_config") return "运行时配置";
      if (key === "request") return "当前草稿";
      return "环境变量";
    },

    setInferenceMode(mode) {
      this.inferenceMode = mode;
      this.inferenceTestResult = null;
      this.inferenceError = "";
    },

    setLocalLlmProvider(provider) {
      this.localLlmProvider = provider;
      this.inferenceTestResult = null;
      this.inferenceError = "";
    },

    localInferenceServiceStatus(kind) {
      const provider = kind === "llm" ? this.localLlmProvider : "local";
      const tested = this.inferenceTestResult;
      if (tested?.mode === "local" && tested[kind]?.provider === provider) {
        return { ...tested[kind], source: "request", tested: true };
      }
      const service = this.services[kind];
      if (service.mode === "local" && service.provider === provider) return service;
      return { ok: null, provider, source: "request", pending: true };
    },

    localInferenceServiceText(kind) {
      const service = this.localInferenceServiceStatus(kind);
      if (service.pending) return "待测试";
      if (service.provider === "codex" && service.ok) return service.tested ? "可用" : "已就绪";
      return this.serviceText(service);
    },

    codexBudgetPaused() {
      return !!(this.codexBudget?.enabled && this.codexBudget.paused);
    },

    codexBudgetPercent(value) {
      if (value == null) return "待确认";
      return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value)}%`;
    },

    codexBudgetDailyValue(key) {
      const budget = this.codexBudget;
      if (budget?.status === "quota_unavailable" && !budget.baseline_started_at) return null;
      return budget?.[key];
    },

    codexBudgetStatusLabel() {
      const budget = this.codexBudget;
      if (!budget?.enabled) return "未启用每日额度保护";
      const labels = {
        daily_limit: budget.error ? "额度保护已挂起" : budget.reserve_percent > 0 ? "已达今日暂停阈值" : "已达今日上限",
        weekly_limit: "本周额度用完",
        quota_unavailable: "额度待确认",
      };
      return labels[budget.status] || "额度可用";
    },

    codexBudgetNotice() {
      const budget = this.codexBudget;
      if (!budget?.enabled) return "每日额度保护未启用。";
      const status = budget.status;
      if (status === "daily_limit") {
        const reason = budget.error || (budget.reserve_percent > 0 ? "已达今日暂停阈值，预留额度不再用于新任务。" : "已达今日额度上限。");
        return `${reason}AI 保留进度，次日自动继续；任务按新到旧处理，采集、下载与 ASR 不受影响。`;
      }
      if (status === "weekly_limit") return "本周订阅额度已用完，AI 等待周额度重置。任务按新到旧处理，采集、下载与 ASR 不受影响。";
      if (status === "quota_unavailable") return `${budget.error || "暂时无法确认 Codex 订阅额度。"}AI 等待额度检查恢复。任务按新到旧处理，采集、下载与 ASR 不受影响。`;
      return "每日可用额度用完后仅挂起 AI，保留进度，恢复后继续。任务按新到旧处理，采集、下载与 ASR 继续运行。";
    },

    codexBudgetTime(value) {
      if (!value) return "待确认";
      return new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false,
      }).format(new Date(value));
    },

    codexBudgetResumeText() {
      const budget = this.codexBudget;
      if (budget?.resume_at) return `${this.codexBudgetTime(budget.resume_at)}（北京时间）`;
      return budget?.status === "quota_unavailable" ? "额度检查恢复后自动继续" : "等待服务端确认";
    },

    codexBudgetResumeLabel() {
      return this.codexBudget?.status === "quota_unavailable" ? "下次额度检查" : "自动恢复";
    },

    codexBudgetProgress() {
      const budget = this.codexBudget;
      if (this.codexBudgetDailyValue("used_percent") == null) return 0;
      return Math.min(100, budget.used_percent / budget.effective_limit_percent * 100);
    },

    gotoInferenceSettings() {
      this.settingsTab = "inference";
      this.switchView("settings");
      this.setSettingsTab("inference");
    },

    applyInferenceConfigPayload(payload) {
      const inference = payload && typeof payload === "object" ? payload : {};
      const volcengine = inference.volcengine && typeof inference.volcengine === "object" ? inference.volcengine : {};
      const codex = inference.codex || {};
      this.inferenceMode = String(inference.mode || "local").trim().toLowerCase() === "volcengine" ? "volcengine" : "local";
      this.inferenceModeSource = String(inference.mode_source || "env").trim().toLowerCase() || "env";
      this.localLlmProvider = inference.local_llm_provider === "codex" ? "codex" : "local";
      this.localLlmProviderSource = inference.local_llm_provider_source || "env";
      this.codexModel = String(codex.model || "");
      this.codexTimeoutSeconds = Number(codex.timeout_seconds || 600);
      this.codexBudget = codex.budget || null;
      this.volcengineApiKeyMasked = String(volcengine.api_key_masked || "");
      this.volcengineApiKeyPresent = !!volcengine.api_key_present;
      this.volcengineLlmModel = String(volcengine.llm_model || "");
      this.volcengineAsrModel = String(volcengine.asr_model || "");
      this.volcengineAsrAppKeyMasked = String(volcengine.asr_app_key_masked || "");
      this.volcengineAsrAppKeyPresent = !!volcengine.asr_app_key_present;
      this.volcengineAsrAccessKeyMasked = String(volcengine.asr_access_key_masked || "");
      this.volcengineAsrAccessKeyPresent = !!volcengine.asr_access_key_present;
      this.volcengineLlmTimeoutSeconds = Number(volcengine.llm_timeout_seconds || 600) || 600;
      this.volcengineAsrTimeoutSeconds = Number(volcengine.asr_timeout_seconds || 600) || 600;
    },

    async loadInferenceSettings({ force = false } = {}) {
      try {
        if (this.inferenceLoaded && !force) return;
        this.inferenceLoading = true;
        this.inferenceError = "";
        const payload = await this.api(`/config/inference`);
        this.applyInferenceConfigPayload(payload);
        this.volcengineApiKey = "";
        this.volcengineAsrAppKey = "";
        this.volcengineAsrAccessKey = "";
        this.inferenceLoaded = true;
      } catch (e) {
        this.inferenceError = e && e.message ? e.message : String(e);
      } finally {
        this.inferenceLoading = false;
      }
    },

    buildInferenceDraftPayload() {
      return {
        mode: this.inferenceMode === "volcengine" ? "volcengine" : "local",
        local_llm_provider: this.localLlmProvider === "codex" ? "codex" : "local",
        volcengine: {
          api_key: String(this.volcengineApiKey || ""),
          llm_model: String(this.volcengineLlmModel || "").trim(),
          asr_model: String(this.volcengineAsrModel || "").trim(),
          asr_app_key: String(this.volcengineAsrAppKey || ""),
          asr_access_key: String(this.volcengineAsrAccessKey || ""),
          llm_timeout_seconds: Number(this.volcengineLlmTimeoutSeconds || 600) || 600,
          asr_timeout_seconds: Number(this.volcengineAsrTimeoutSeconds || 600) || 600,
        },
      };
    },

    async saveInferenceSettings() {
      try {
        this.inferenceSaving = true;
        this.inferenceError = "";
        this.inferenceTestResult = null;
        const payload = this.buildInferenceDraftPayload();
        await this.api(`/config/inference`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
        await this.loadInferenceSettings({ force: true });
        await this._refreshHealthStatus({ silent: true });
        await this.loadSystemStatus({ silent: true });
        this.globalStatus = this.inferenceMode === "volcengine"
          ? "已保存火山模式推理配置"
          : this.localLlmProvider === "codex" ? "已切换到 Codex，ASR 沿用本地服务" : "已切换到本地服务";
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.inferenceError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.inferenceSaving = false;
      }
    },

    async testInferenceSettings() {
      try {
        this.inferenceTesting = true;
        this.inferenceError = "";
        this.inferenceTestResult = null;
        const payload = this.buildInferenceDraftPayload();
        const result = await this.api(`/config/inference/test`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (JSON.stringify(payload) !== JSON.stringify(this.buildInferenceDraftPayload())) return;
        this.inferenceTestResult = result;
        this.globalStatus = result && result.ok ? "推理连接测试通过" : "推理连接测试失败";
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.inferenceError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.inferenceTesting = false;
      }
    },

    async loadYtdlpCookies({ force = false } = {}) {
      try {
        if (this.ytdlpCookiesLoaded && !force) return;
        this.ytdlpCookiesYoutubeError = "";
        this.ytdlpCookiesBilibiliError = "";
        const payload = await this.api(`/config`);
        const data = (payload && payload.data) || {};
        const ytCfg = data && data.ytdlp_cookies_youtube ? data.ytdlp_cookies_youtube : null;
        this.ytdlpCookiesYoutubeConfigured = !!(ytCfg && typeof ytCfg === "object" && ytCfg.configured);
        this.ytdlpCookiesYoutubeText = "";
        const biliCfg = data && data.ytdlp_cookies_bilibili ? data.ytdlp_cookies_bilibili : null;
        this.ytdlpCookiesBilibiliConfigured = !!(biliCfg && typeof biliCfg === "object" && biliCfg.configured);
        this.ytdlpCookiesBilibiliText = "";
        this.ytdlpCookiesLoaded = true;
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.ytdlpCookiesYoutubeError = msg;
        this.ytdlpCookiesBilibiliError = msg;
      }
    },

    async saveYtdlpCookiesYoutube({ clear = false } = {}) {
      const text = clear ? "" : String(this.ytdlpCookiesYoutubeText || "");
      if (!clear && !text.trim()) {
        this.ytdlpCookiesYoutubeError = "请先粘贴新的 YouTube cookies.txt。";
        return;
      }
      try {
        this.ytdlpCookiesYoutubeSaving = true;
        this.ytdlpCookiesYoutubeError = "";
        await this.api(`/config/ytdlp_cookies_youtube`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ value: { text } }),
        });
        this.ytdlpCookiesYoutubeConfigured = !clear;
        this.ytdlpCookiesYoutubeText = "";
        this.ytdlpCookiesLoaded = true;
        this.globalStatus = clear ? "已清空 YouTube Cookies" : "已保存 YouTube Cookies";
        await this.loadSystemStatus({ silent: true });
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.ytdlpCookiesYoutubeError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.ytdlpCookiesYoutubeSaving = false;
      }
    },

    async clearYtdlpCookiesYoutube() {
      this.ytdlpCookiesYoutubeText = "";
      await this.saveYtdlpCookiesYoutube({ clear: true });
    },

    async saveYtdlpCookiesBilibili({ clear = false } = {}) {
      const text = clear ? "" : String(this.ytdlpCookiesBilibiliText || "");
      if (!clear && !text.trim()) {
        this.ytdlpCookiesBilibiliError = "请先粘贴新的 B站 cookies.txt。";
        return;
      }
      try {
        this.ytdlpCookiesBilibiliSaving = true;
        this.ytdlpCookiesBilibiliError = "";
        await this.api(`/config/ytdlp_cookies_bilibili`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ value: { text } }),
        });
        this.ytdlpCookiesBilibiliConfigured = !clear;
        this.ytdlpCookiesBilibiliText = "";
        this.ytdlpCookiesLoaded = true;
        this.globalStatus = clear ? "已清空 B站 Cookies" : "已保存 B站 Cookies";
        await this.loadSystemStatus({ silent: true });
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.ytdlpCookiesBilibiliError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.ytdlpCookiesBilibiliSaving = false;
      }
    },

    async clearYtdlpCookiesBilibili() {
      this.ytdlpCookiesBilibiliText = "";
      await this.saveYtdlpCookiesBilibili({ clear: true });
    },

    async loadYtdlpSubtitles({ force = false } = {}) {
      try {
        if (this.ytdlpSubtitlesLoaded && !force) return;
        this.ytdlpSubtitlesError = "";
        const payload = await this.api(`/config`);
        const data = (payload && payload.data) || {};
        const cfg = data && data.ytdlp_subtitles ? data.ytdlp_subtitles : null;
        const enabled = cfg && typeof cfg === "object" ? cfg.enabled : false;
        this.ytdlpSubtitlesEnabled = typeof enabled === "boolean" ? enabled : false;
        this.ytdlpSubtitlesLoaded = true;
      } catch (e) {
        this.ytdlpSubtitlesError = e && e.message ? e.message : String(e);
      }
    },

    async loadYtdlpMembersOnly({ force = false } = {}) {
      try {
        if (this.ytdlpMembersOnlyLoaded && !force) return;
        this.ytdlpMembersOnlyError = "";
        const payload = await this.api(`/config`);
        const data = (payload && payload.data) || {};
        const cfg = data && data.ytdlp_members_only ? data.ytdlp_members_only : null;
        const enabled = cfg && typeof cfg === "object" ? cfg.enabled : false;
        this.ytdlpMembersOnlyEnabled = typeof enabled === "boolean" ? enabled : false;
        this.ytdlpMembersOnlyLoaded = true;
      } catch (e) {
        this.ytdlpMembersOnlyError = e && e.message ? e.message : String(e);
      }
    },

    async saveYtdlpMembersOnly() {
      try {
        this.ytdlpMembersOnlySaving = true;
        this.ytdlpMembersOnlyError = "";
        await this.api(`/config/ytdlp_members_only`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ value: { enabled: !!this.ytdlpMembersOnlyEnabled } }),
        });
        this.ytdlpMembersOnlyLoaded = true;
        this.globalStatus = "已保存会员视频下载设置";
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.ytdlpMembersOnlyError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.ytdlpMembersOnlySaving = false;
      }
    },

    async saveYtdlpSubtitles() {
      try {
        this.ytdlpSubtitlesSaving = true;
        this.ytdlpSubtitlesError = "";
        await this.api(`/config/ytdlp_subtitles`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ value: { enabled: !!this.ytdlpSubtitlesEnabled } }),
        });
        this.ytdlpSubtitlesLoaded = true;
        this.globalStatus = "已保存字幕下载设置";
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.ytdlpSubtitlesError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.ytdlpSubtitlesSaving = false;
      }
    },

    ytdlpFormatEffectiveText() {
      const preset = String(this.ytdlpFormatPreset || "1080").trim();
      const custom = String(this.ytdlpFormatCustomText || "");
      if (preset === "custom") return custom;
      if (preset === "720") return ytdlpFormatPreset720;
      return ytdlpFormatPreset1080;
    },

    async loadYtdlpFormat({ force = false } = {}) {
      try {
        if (this.ytdlpFormatLoaded && !force) return;
        this.ytdlpFormatError = "";

        const payload = await this.api(`/config`);
        const data = (payload && payload.data) || {};
        const cfg = data && data.ytdlp_format ? data.ytdlp_format : null;
        const rawText = cfg && typeof cfg === "object" ? cfg.text : "";
        const text = typeof rawText === "string" ? rawText.trim() : "";
        const rawPreset = cfg && typeof cfg === "object" ? cfg.preset : "";
        const preset = typeof rawPreset === "string" ? rawPreset.trim() : "";

        if (preset === "1080" || preset === "720" || preset === "custom") this.ytdlpFormatPreset = preset;
        else if (text === ytdlpFormatPreset1080) this.ytdlpFormatPreset = "1080";
        else if (text === ytdlpFormatPreset720) this.ytdlpFormatPreset = "720";
        else if (text) this.ytdlpFormatPreset = "custom";
        else this.ytdlpFormatPreset = "1080";

        if (this.ytdlpFormatPreset === "custom") this.ytdlpFormatCustomText = text;
        this.ytdlpFormatLoaded = true;
      } catch (e) {
        this.ytdlpFormatError = e && e.message ? e.message : String(e);
      }
    },

    async saveYtdlpFormat() {
      try {
        this.ytdlpFormatSaving = true;
        this.ytdlpFormatError = "";
        const preset = String(this.ytdlpFormatPreset || "1080").trim();
        const custom = String(this.ytdlpFormatCustomText || "").trim();
        const text = preset === "custom" ? custom : this.ytdlpFormatEffectiveText().trim();

        if (preset === "custom" && !custom) {
          this.ytdlpFormatError = "自定义格式不能为空";
          return;
        }

        await this.api(`/config/ytdlp_format`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ value: { preset, text } }),
        });
        this.ytdlpFormatLoaded = true;
        this.globalStatus = "已保存 YTDLP_FORMAT";
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.ytdlpFormatError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.ytdlpFormatSaving = false;
      }
    },

    async loadLlmPolishPrompt({ force = false } = {}) {
      try {
        if (this.llmPolishPromptLoaded && !force) return;
        this.llmPolishPromptError = "";
        this.llmPolishPromptLoadingDefault = true;

        const [payload, defaults] = await Promise.all([this.api(`/config`), this.api(`/config/defaults`)]);
        const data = (payload && payload.data) || {};
        const cfg = data && data.llm_transcript_polish_prompt ? data.llm_transcript_polish_prompt : null;
        const defaultCfg = defaults && defaults.llm_transcript_polish_prompt ? defaults.llm_transcript_polish_prompt : null;
        const rawText = cfg && typeof cfg === "object" ? cfg.text : "";
        const defaultText = defaultCfg && typeof defaultCfg === "object" ? defaultCfg.text : "";

        this.llmPolishPromptDefaultText = typeof defaultText === "string" ? defaultText : "";
        this.llmPolishPromptText = typeof rawText === "string" && rawText.trim() ? rawText : this.llmPolishPromptDefaultText;
        this.llmPolishPromptLoaded = true;
      } catch (e) {
        this.llmPolishPromptError = e && e.message ? e.message : String(e);
      } finally {
        this.llmPolishPromptLoadingDefault = false;
      }
    },

    async saveLlmPolishPrompt() {
      try {
        this.llmPolishPromptSaving = true;
        this.llmPolishPromptError = "";
        await this.api(`/config/llm_transcript_polish_prompt`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ value: { text: String(this.llmPolishPromptText || "") } }),
        });
        this.llmPolishPromptLoaded = true;
        this.globalStatus = "已保存 LLM 转写润色提示词";
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.llmPolishPromptError = msg;
        this.globalStatus = `error: ${msg}`;
      } finally {
        this.llmPolishPromptSaving = false;
      }
    },

    resetLlmPolishPromptToDefault() {
      this.llmPolishPromptText = String(this.llmPolishPromptDefaultText || "");
      this.llmPolishPromptError = "";
    },

    async loadStaleVideosCleanup({ force = false } = {}) {
      try {
        if (this.staleVideosCleanupLoaded && !force) return;
        this.staleVideosCleanupLoading = true;
        this.staleVideosCleanupError = "";
        const payload = await this.api(`/cleanup/stale-videos?limit=20`);
        this.staleVideosCleanupCount = Number((payload && payload.count) || 0);
        this.staleVideosCleanupHasMore = !!(payload && payload.has_more);
        this.staleVideosCleanupItems = Array.isArray(payload && payload.items) ? payload.items : [];
        this.staleVideosCleanupLoaded = true;
      } catch (e) {
        this.staleVideosCleanupError = e && e.message ? e.message : String(e);
      } finally {
        this.staleVideosCleanupLoading = false;
      }
    },

    async cleanupStaleVideos() {
      if (this.staleVideosCleanupDeleting) return;
      const currentCount = Math.max(Number(this.staleVideosCleanupCount || 0), this.staleVideosCleanupItems.length);
      if (currentCount <= 0) {
        await this.loadStaleVideosCleanup({ force: true });
        const refreshedCount = Math.max(Number(this.staleVideosCleanupCount || 0), this.staleVideosCleanupItems.length);
        if (refreshedCount <= 0) {
          this.globalStatus = "没有可清理的遗留视频记录";
          return;
        }
      }

      const count = Math.max(Number(this.staleVideosCleanupCount || 0), this.staleVideosCleanupItems.length);
      const countLabel = this.staleVideosCleanupHasMore ? `${count}+` : String(count);
      const ok = confirm(
        `确认清理这 ${countLabel} 条遗留视频记录？\n\n只会删除“媒体已停用监控、视频仍是 discovered、且没有有效下载任务/视频文件”的记录。`
      );
      if (!ok) return;

      try {
        this.staleVideosCleanupDeleting = true;
        this.staleVideosCleanupError = "";
        const payload = await this.api(`/cleanup/stale-videos`, { method: "POST" });
        const deleted = Number((payload && payload.deleted) || 0);
        await this.loadStaleVideosCleanup({ force: true });
        await this.loadMedia();
        await this.loadMediaIndex({ lightweight: true });
        await this.loadStats();
        const message = deleted > 0 ? `已清理 ${deleted} 条遗留视频记录` : "没有可清理的遗留视频记录";
        this.globalStatus = message;
        this.toastSuccess(message);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.staleVideosCleanupError = msg;
        this.globalStatus = `error: ${msg}`;
        this.toastError(`清理失败：${msg}`);
      } finally {
        this.staleVideosCleanupDeleting = false;
      }
    },

    staleVideosCleanupCountLabel() {
      const count = Math.max(Number(this.staleVideosCleanupCount || 0), this.staleVideosCleanupItems.length);
      if (this.staleVideosCleanupHasMore && count > 0) return `${count}+`;
      return String(count);
    },
  };
}
