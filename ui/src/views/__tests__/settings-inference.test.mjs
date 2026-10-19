import assert from "node:assert/strict";
import test from "node:test";

import { createSettingsModule } from "../../app/modules/settings.js";
import { createCommonViewMethods } from "../../shared/view-helpers.js";
import { createJobsViewMethods } from "../jobs-model.js";
import { createSettingsViewMethods } from "../settings-model.js";

function settings() {
  return {
    ...createSettingsModule(),
    ...createCommonViewMethods(),
    ...createSettingsViewMethods({ ytdlpFormatPreset1080: "", ytdlpFormatPreset720: "" }),
    services: {
      asr: { ok: true, configured: true, mode: "local", provider: "local", source: "env" },
      llm: { ok: true, configured: true, mode: "local", provider: "local", source: "env" },
    },
  };
}

test("读取已保存的 Codex 选择，草稿继续使用 local 模式并保留火山配置", () => {
  const state = settings();
  state.applyInferenceConfigPayload({
    mode: "local",
    mode_source: "app_config",
    local_llm_provider: "codex",
    local_llm_provider_source: "app_config",
    codex: { model: "configured-model", timeout_seconds: 180 },
    volcengine: { llm_model: "existing-model", asr_model: "existing-asr" },
  });

  const draft = state.buildInferenceDraftPayload();
  assert.equal(draft.mode, "local");
  assert.equal(draft.local_llm_provider, "codex");
  assert.equal(draft.volcengine.llm_model, "existing-model");
  assert.equal(draft.volcengine.asr_model, "existing-asr");
  assert.equal(state.localLlmProviderSource, "app_config");
  assert.equal(state.codexModel, "configured-model");
  assert.equal(state.codexTimeoutSeconds, 180);
});

test("未保存的 Codex 草稿不会显示原本地 LLM 的 OK，ASR 继续显示本地状态", () => {
  const state = settings();
  const asr = state.services.asr;
  state.inferenceTestResult = { ok: true, mode: "local", llm: state.services.llm };
  state.setLocalLlmProvider("codex");

  assert.equal(state.inferenceTestResult, null);
  assert.equal(state.localInferenceServiceText("llm"), "待测试");
  assert.equal(state.localInferenceServiceStatus("llm").ok, null);
  assert.equal(state.localInferenceServiceStatus("asr"), asr);
  assert.equal(state.localInferenceServiceText("asr"), "OK");
});

test("Codex 安装登录健康与真实生成测试分别展示", () => {
  const state = settings();
  state.setLocalLlmProvider("codex");
  state.services.llm = {
    ok: true, configured: true, mode: "local", provider: "codex", source: "app_config",
    installed: true, authenticated: true, version: "installed-version",
  };
  assert.equal(state.localInferenceServiceText("llm"), "已就绪");
  assert.equal(state.localInferenceServiceStatus("llm").authenticated, true);

  state.inferenceTestResult = { mode: "local", llm: { ...state.services.llm, source: "request" } };
  assert.equal(state.localInferenceServiceText("llm"), "可用");
  assert.equal(state.localInferenceServiceStatus("llm").source, "request");
});

test("真实生成测试失败时保留失败证据，不用安装登录成功覆盖结果", () => {
  const state = settings();
  state.setLocalLlmProvider("codex");
  state.services.llm = { ok: true, configured: true, mode: "local", provider: "codex" };
  state.inferenceTestResult = {
    mode: "local",
    llm: { ok: false, configured: true, provider: "codex", installed: true, authenticated: true, error: "生成超时" },
  };

  assert.equal(state.localInferenceServiceText("llm"), "Error");
  assert.equal(state.localInferenceServiceStatus("llm").error, "生成超时");
  assert.equal(state.localInferenceServiceStatus("llm").installed, true);
});

test("从 Codex 切回本地 LLM 也不能沿用 Codex 的健康结果", () => {
  const state = settings();
  state.services.llm = { ok: true, configured: true, mode: "local", provider: "codex" };
  state.inferenceTestResult = { mode: "local", llm: state.services.llm };
  state.setLocalLlmProvider("local");

  assert.equal(state.localInferenceServiceText("llm"), "待测试");
  assert.equal(state.localInferenceServiceStatus("llm").provider, "local");
});

test("切换火山模式清除旧测试，保留本地 Codex 选择，且不误用火山 ASR 健康", () => {
  const state = settings();
  state.setLocalLlmProvider("codex");
  state.inferenceTestResult = { mode: "local", llm: { ok: true, provider: "codex" } };
  state.setInferenceMode("volcengine");

  assert.equal(state.inferenceTestResult, null);
  assert.equal(state.buildInferenceDraftPayload().mode, "volcengine");
  assert.equal(state.buildInferenceDraftPayload().local_llm_provider, "codex");
  state.services.asr = { ok: true, mode: "volcengine", provider: "volcengine" };
  state.setInferenceMode("local");
  assert.equal(state.localInferenceServiceText("asr"), "待测试");
  assert.equal(state.localLlmProvider, "codex");
});

test("每日用量按周额度展示，读取服务器当前 50% 上限且不硬编码旧值", () => {
  const state = settings();
  state.applyInferenceConfigPayload({
    local_llm_provider: "codex",
    codex: { budget: {
      enabled: true, daily_limit_percent: 50, reserve_percent: 0, effective_limit_percent: 50,
      used_percent: 12.5, remaining_percent: 37.5,
      paused: false, status: "ready", weekly_used_percent: 60,
    } },
  });

  assert.equal(state.codexBudgetPercent(state.codexBudget.used_percent), "12.5%");
  assert.equal(state.codexBudgetPercent(state.codexBudget.remaining_percent), "37.5%");
  assert.equal(state.codexBudgetProgress(), 25);
  assert.equal(state.codexBudgetPaused(), false);
  assert.equal(state.codexBudgetStatusLabel(), "额度可用");
  assert.equal(state.codexBudgetPercent(state.codexBudget.daily_limit_percent), "50%");
  assert.equal(state.codexBudgetPercent(state.codexBudget.effective_limit_percent), "50%");
  assert.equal(state.codexBudget.reserve_percent, 0);
  assert.match(state.codexBudgetNotice(), /仅挂起 AI/);
  assert.match(state.codexBudgetNotice(), /按新到旧处理/);
});

test("达到每日 50% 上限只提示 AI 挂起，次日恢复时间固定显示北京时间", () => {
  const state = settings();
  state.codexBudget = {
    enabled: true, paused: true, status: "daily_limit", daily_limit_percent: 50,
    reserve_percent: 0, effective_limit_percent: 50, used_percent: 50, remaining_percent: 0,
    resume_at: "2026-10-16T16:00:00Z", timezone: "Asia/Shanghai",
  };

  assert.equal(state.codexBudgetPaused(), true);
  assert.equal(state.codexBudgetStatusLabel(), "已达今日上限");
  assert.equal(state.codexBudgetResumeLabel(), "自动恢复");
  assert.equal(state.codexBudgetResumeText(), "2026/10/17 00:00（北京时间）");
  assert.match(state.codexBudgetNotice(), /采集、下载与 ASR 不受影响/);
  assert.doesNotMatch(state.codexBudgetNotice(), /预留/);
  assert.equal(state.codexBudgetProgress(), 100);
});

test("额度读取失败显示待确认，不把未知用量当作零或承诺次日恢复", () => {
  const state = settings();
  state.codexBudget = {
    enabled: true, paused: true, status: "quota_unavailable", used_percent: 0, remaining_percent: 50,
    baseline_started_at: null,
  };

  assert.equal(state.codexBudgetPercent(null), "待确认");
  assert.equal(state.codexBudgetPercent(0), "0%");
  assert.equal(state.codexBudgetPercent(state.codexBudgetDailyValue("used_percent")), "待确认");
  assert.equal(state.codexBudgetPercent(state.codexBudgetDailyValue("remaining_percent")), "待确认");
  assert.equal(state.codexBudgetProgress(), 0);
  assert.equal(state.codexBudgetStatusLabel(), "额度待确认");
  assert.equal(state.codexBudgetResumeLabel(), "下次额度检查");
  assert.equal(state.codexBudgetResumeText(), "额度检查恢复后自动继续");
  assert.match(state.codexBudgetNotice(), /暂时无法确认 Codex 订阅额度/);
});

test("已有基线的检查失败保留历史读数及最近检查时间", () => {
  const state = settings();
  state.codexBudget = {
    enabled: true, paused: true, status: "quota_unavailable", used_percent: 12, remaining_percent: 38,
    baseline_started_at: "2026-10-16T00:00:00Z", last_checked_at: "2026-10-16T03:00:00Z",
  };

  assert.equal(state.codexBudgetPercent(state.codexBudgetDailyValue("used_percent")), "12%");
  assert.equal(state.codexBudgetPercent(state.codexBudgetDailyValue("remaining_percent")), "38%");
  assert.equal(state.codexBudgetTime(state.codexBudget.last_checked_at), "2026/10/16 11:00");
  assert.equal(state.codexBudgetStatusLabel(), "额度待确认");
});

test("用量不足上限的保护挂起展示具体原因，不误称额度已经用完", () => {
  const state = settings();
  state.codexBudget = {
    enabled: true, paused: true, status: "daily_limit", daily_limit_percent: 50,
    effective_limit_percent: 50, reserve_percent: 0, used_percent: 16, remaining_percent: 34,
    error: "周窗口已重置，无法核实旧窗口尾部用量；今日暂停，次日建立新基线。",
  };

  assert.equal(state.codexBudgetStatusLabel(), "额度保护已挂起");
  assert.match(state.codexBudgetNotice(), /周窗口已重置，无法核实旧窗口尾部用量/);
  assert.match(state.codexBudgetNotice(), /按新到旧处理/);
  assert.doesNotMatch(state.codexBudgetNotice(), /已达今日额度上限|已用完/);
});

test("周额度用完显示周重置时间，不与每日限额混淆", () => {
  const state = settings();
  state.codexBudget = {
    enabled: true, paused: true, status: "weekly_limit", resume_at: "2026-10-20T12:30:00Z",
  };

  assert.equal(state.codexBudgetStatusLabel(), "本周额度用完");
  assert.equal(state.codexBudgetResumeText(), "2026/10/20 20:30（北京时间）");
  assert.match(state.codexBudgetNotice(), /等待周额度重置/);
});

test("额度保护挂起时 AI worker 不显示手动启动，其他 role 保持控制能力", () => {
  const state = { ...settings(), ...createJobsViewMethods() };
  state.codexBudget = { enabled: true, paused: true, status: "daily_limit" };
  assert.equal(state.workerRoleControllable({ role: "ai", paused: true, controllable: true }), false);
  assert.equal(state.workerRoleControllable({ role: "download_youtube", controllable: true }), true);
  assert.equal(state.workerRoleControllable({ role: "asr", controllable: true }), true);
  state.codexBudget = { enabled: true, paused: false, status: "ready" };
  assert.equal(state.workerRoleControllable({ role: "ai", paused: true, controllable: true }), true);
});
