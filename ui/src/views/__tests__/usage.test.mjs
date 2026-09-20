import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createAppInitMethods } from "../../app/init-model.js";
import { createUsageModule } from "../../app/modules/usage.js";
import { createNavItems } from "../../app/navigation.js";
import { createUrlStateMethods } from "../../services/url-state.js";
import { createUsageViewMethods } from "../usage-model.js";

function usagePayload() {
  return {
    generated_at: "2026-09-03T08:30:00Z",
    timezone: "Asia/Shanghai",
    window: { days: 90, start: "2026-06-06", end: "2026-09-03" },
    summary: {
      video_count: 125,
      downloaded_video_count: 118,
      video_downloaded: 7,
      external_calls: null,
      llm_calls: null,
      asr_calls: null,
      embedding_calls: null,
      llm_input_tokens: null,
      llm_output_tokens: null,
      llm_total_tokens: null,
      asset_count: 18,
      asset_size_bytes: 2048,
      asset_missing_size_count: 2,
      database_size_bytes: null,
    },
    series: [
      {
        date: "2026-09-01",
        video_downloaded: 1,
        external_calls: null,
        llm_calls: null,
        asr_calls: null,
        embedding_calls: null,
        llm_input_tokens: null,
        llm_output_tokens: null,
        llm_total_tokens: null,
        asset_size_bytes: null,
        database_size_bytes: null,
        usage_sampled: false,
        resource_sampled: false,
      },
      {
        date: "2026-09-02",
        video_downloaded: 2,
        external_calls: 0,
        llm_calls: 0,
        asr_calls: 0,
        embedding_calls: 0,
        llm_input_tokens: 0,
        llm_output_tokens: 0,
        llm_total_tokens: 0,
        asset_size_bytes: 0,
        database_size_bytes: null,
        usage_sampled: true,
        resource_sampled: true,
      },
    ],
    service_breakdown: [
      {
        service: "llm",
        operation: "summarize",
        provider: "openai",
        model: "gpt-test",
        calls: 2,
        successes: 1,
        failures: 1,
        input_tokens: 120,
        output_tokens: 30,
        total_tokens: 150,
        duration_ms: 2400,
        usage_missing_calls: 1,
        last_called_at: "2026-09-02T10:00:00Z",
      },
    ],
    asset_breakdown: [
      { asset_type: "video", count: 4, size_bytes: 2048, missing_size_count: 2 },
    ],
    collection: {
      usage_started_at: null,
      last_usage_at: null,
      resource_started_at: "2026-09-02",
      last_resource_snapshot_at: "2026-09-02T23:00:00Z",
      physical_storage_available: false,
      physical_storage_reason: "未接入部署侧指标",
    },
  };
}

function usageContext() {
  return {
    ...createUsageModule(),
    ...createUsageViewMethods(),
    activeView: "usage",
    globalStatus: "",
    formatInteger(value) { return String(value); },
    formatCompactInteger(value) { return String(value); },
    formatBytes(value) { return `${value} B`; },
  };
}

test("系统导航把资源用量放在运行中心与智能体接入之间，并支持 /usage 直达", () => {
  const navItems = createNavItems();
  assert.deepEqual(
    navItems.filter((item) => item.system && !item.hidden).map((item) => item.label),
    ["运行中心", "资源用量", "智能体接入", "设置"],
  );

  globalThis.window = { location: { pathname: "/usage", search: "" } };
  const ctx = { ...createUrlStateMethods({ settingsTabs: [] }), navItems };
  assert.equal(ctx._parseViewFromLocation(), "usage");
  assert.equal(ctx._viewPath("usage"), "/usage");
});

test("直接进入资源用量会加载接口，浏览器后退离开时会执行清理", async () => {
  let loadCount = 0;
  const route = {
    ...createUrlStateMethods({ settingsTabs: [] }),
    activeView: "usage",
    _disconnectJobsWs() {},
    _destroyJobsDoneChart() {},
    async loadUsage() { loadCount += 1; },
  };
  await route.refreshActive();
  assert.equal(loadCount, 1);

  const listeners = {};
  globalThis.window = {
    location: { pathname: "/settings", search: "" },
    addEventListener(type, listener) { listeners[type] = listener; },
  };
  let leaveCount = 0;
  let refreshCount = 0;
  const shell = {
    ...createAppInitMethods(),
    activeView: "usage",
    navItems: createNavItems(),
    _applySidebarMode() {},
    playlistCalendarUpdateCount() {},
    _parseViewFromLocation() { return "settings"; },
    leaveUsagePage() { leaveCount += 1; },
    _applyQueryFromLocation() {},
    refreshActive() { refreshCount += 1; },
  };
  shell._initShellListeners();
  listeners.popstate();
  assert.equal(leaveCount, 1);
  assert.equal(shell.activeView, "settings");
  assert.equal(refreshCount, 1);
});

test("资源用量启动只加载专用接口，不再后台扫描旧概览统计", async () => {
  let refreshCount = 0;
  let statsCount = 0;
  const shell = {
    ...createAppInitMethods(),
    activeView: "usage",
    async refreshActive() { refreshCount += 1; },
    async loadStats() { statsCount += 1; },
  };

  await shell._refreshProtectedData({ statsReady: false });

  assert.equal(refreshCount, 1);
  assert.equal(statsCount, 0);
});

test("资源用量 payload 按契约映射，并保留可空统计", () => {
  const ctx = usageContext();
  ctx.applyUsagePayload(usagePayload());

  assert.equal(ctx.usageSummary.downloadedVideoCount, 118);
  assert.equal(ctx.usageSummary.videoDownloaded, 7);
  assert.equal(ctx.usageSummary.externalCalls, null);
  assert.equal(ctx.usageSummary.asrCalls, null);
  assert.equal(ctx.usageSummary.embeddingCalls, null);
  assert.equal(ctx.usageSummary.llmTotalTokens, null);
  assert.equal(ctx.usageSummary.databaseSizeBytes, null);
  assert.deepEqual(ctx.usageServiceBreakdown[0], {
    service: "llm",
    operation: "summarize",
    provider: "openai",
    model: "gpt-test",
    calls: 2,
    successes: 1,
    failures: 1,
    inputTokens: 120,
    outputTokens: 30,
    totalTokens: 150,
    durationMs: 2400,
    usageMissingCalls: 1,
    lastCalledAt: "2026-09-02T10:00:00Z",
  });
  assert.deepEqual(ctx.usageAssetBreakdown[0], {
    assetType: "video",
    count: 4,
    sizeBytes: 2048,
    missingSizeCount: 2,
  });
  assert.equal(ctx.usageCollection.physicalStorageAvailable, false);
  assert.equal(ctx.usageDateLabel("2026-09-02"), "2026/09/02");
  assert.match(ctx.usageGeneratedLabel(), /^更新于 /);
  assert.equal(ctx.usageCollectionModeLabel(), "实时采集中");
  assert.equal(ctx.usageOperationLabel("legacy.event_extraction"), "历史事件抽取");
  assert.equal(ctx.usageProviderLabel({ operation: "legacy.event_extraction" }), "历史任务记录");
  assert.equal(ctx.usageDurationLabel(0, "legacy.event_extraction"), "历史未记录");
  assert.equal(ctx.usageAssetLabel("video"), "视频");
  assert.equal(ctx.usageSuccessRateLabel(ctx.usageServiceBreakdown[0]), "50.0%");

  assert.equal(ctx.usageAssetShare(ctx.usageAssetBreakdown[0]), 100);
});

test("趋势序列把缺失采样保留为空白，同时保留已采样的零值", () => {
  const ctx = usageContext();
  ctx.applyUsagePayload(usagePayload());

  assert.deepEqual(ctx._usageMetricData("externalCalls", "usageSampled"), [
    { time: "2026-09-01" },
    { time: "2026-09-02", value: 0 },
  ]);
  assert.deepEqual(ctx._usageMetricData("assetSizeBytes", "resourceSampled"), [
    { time: "2026-09-01" },
    { time: "2026-09-02", value: 0 },
  ]);
  assert.equal(ctx.usageCountLabel(ctx.usageSummary.externalCalls), "未采集");
  assert.equal(ctx.usageBytesLabel(ctx.usageSummary.databaseSizeBytes), "暂无采样");
  assert.equal(ctx.usageDatabaseSizeLabel(), "不可用");
});

test("外部调用长窗口使用移动均值，视频下载量保留每日实际值", () => {
  const ctx = usageContext();

  ctx.usageSeries = [
    { date: "2026-09-01", llmCalls: 0, videoDownloaded: 1, usageSampled: true },
    { date: "2026-09-02", llmCalls: 3, videoDownloaded: 8, usageSampled: true },
    { date: "2026-09-03", llmCalls: 6, videoDownloaded: 2, usageSampled: true },
    { date: "2026-09-04", llmCalls: null, videoDownloaded: 5, usageSampled: false },
    { date: "2026-09-05", llmCalls: 9, videoDownloaded: 3, usageSampled: true },
  ];

  assert.equal(ctx.usageCallTrendWindow(), 7);
  assert.equal(ctx.usageCallTrendGranularityLabel(), "7 日移动均值");
  assert.deepEqual(ctx._usageCallTrendData("llmCalls", "usageSampled"), [
    { time: "2026-09-01", value: 0 },
    { time: "2026-09-02", value: 1.5 },
    { time: "2026-09-03", value: 3 },
    { time: "2026-09-04" },
    { time: "2026-09-05", value: 9 },
  ]);
  assert.deepEqual(ctx._usageMetricData("videoDownloaded"), [
    { time: "2026-09-01", value: 1 },
    { time: "2026-09-02", value: 8 },
    { time: "2026-09-03", value: 2 },
    { time: "2026-09-04", value: 5 },
    { time: "2026-09-05", value: 3 },
  ]);


  assert.equal(ctx.usageCallTrendWindow(), 7);
  assert.equal(ctx.usageCallTrendGranularityLabel(), "7 日移动均值");

});

test("固定读取90天趋势，离开页面会丢弃在途结果", async () => {
  const ctx = usageContext();
  assert.equal(ctx.usageRangeLabel(), "最近 90 天");
  const calls = [];
  let updateCount = 0;
  ctx.api = async (path) => {
    calls.push(path);
    return usagePayload();
  };
  ctx._updateUsageCharts = () => { updateCount += 1; };
  ctx.globalStatus = "error: 404: Not Found";

  await ctx.loadUsage();
  assert.deepEqual(calls, ["/usage?days=90"]);
  assert.equal(ctx.usageRangeLabel(), "最近 90 天");
  assert.equal(updateCount, 1);
  assert.equal(ctx.globalStatus, "");
  await ctx.refreshUsage();
  assert.deepEqual(calls, ["/usage?days=90", "/usage?days=90"]);

  let resolveRequest;
  ctx.usagePayload = null;
  ctx.api = () => new Promise((resolve) => { resolveRequest = resolve; });
  const pending = ctx.loadUsage();
  ctx.leaveUsagePage();
  resolveRequest(usagePayload());
  assert.equal(await pending, null);
  assert.equal(ctx.usagePayload, null);
  assert.equal(ctx.usageLoading, false);
});

test("图表读数保留零值和未采集差异，调用读数与移动均值一致", () => {
  const ctx = usageContext();
  ctx.applyUsagePayload(usagePayload());
  assert.equal(ctx.usageChartValue("calls", "llmCalls"), "0");
  assert.equal(ctx.usageChartValue("storage", "databaseSizeBytes"), "—");
  assert.equal(ctx.usageChartDateLabel("calls"), "09/02 · 当日");
  ctx.usageChartDates = { calls: "2026-09-01" };
  assert.equal(ctx.usageChartValue("calls", "llmCalls"), "—");
  assert.equal(ctx.usageChartDateLabel("calls"), "09/01");
  ctx.usageSeries[0].usageSampled = true;
  ctx.usageSeries[0].llmCalls = 10;
  ctx.usageChartDates.calls = "2026-09-02";
  assert.equal(ctx.usageChartValue("calls", "llmCalls"), "5");
  ctx.leaveUsagePage();
  assert.deepEqual(ctx.usageChartDates, {});
});

test("资源大屏保留五项摘要、四图与明细，移除周期及未知大小展示", async () => {
  const template = await readFile(new URL("../../../templates/app/views/usage.html", import.meta.url), "utf8");
  const icons = await readFile(new URL("../../../templates/app/components/usage-icons.html", import.meta.url), "utf8");
  assert.equal((template.match(/class="raelyn-usage-metric"/g) || []).length, 5);
  assert.equal((template.match(/x-ref="usage(?:Video|Calls|Tokens|Storage)Chart"/g) || []).length, 4);
  for (const label of ["累计外部调用", "累计 LLM Token", "外部服务调用", "Token 消耗", "视频下载", "存储规模", "服务调用画像", "资产构成", "主机物理磁盘", "采集信息与统计说明"]) {
    assert.ok(template.includes(label), label);
  }
  assert.doesNotMatch(template, /setUsageDays|大小未知资产|缺大小|missingSizeCount|assetMissingSizeCount|\[7,30,90\]/);
  assert.match(template, /usageTokenDialog.showModal/);
  assert.match(template, /usageCollectionDialog.showModal/);
  assert.match(template, /usageProviderLabel/);
  for (const name of ["video", "calls", "token", "storage", "database"]) assert.ok(icons.includes(`id="usage-icon-${name}"`));
});

test("累计指标区分未采集、实际零值与缺失调用，并使用中文大数单位", () => {
  const ctx = usageContext();
  ctx.applyUsagePayload(usagePayload());
  assert.deepEqual(ctx.usageMetricParts(null), { value: "未采集", unit: "" });
  assert.deepEqual(ctx.usageMetricParts(0), { value: "0", unit: "" });
  assert.deepEqual(ctx.usageMetricParts(2283574221), { value: "22.84", unit: "亿" });
  assert.deepEqual(ctx.usageMetricParts(781591), { value: "78.16", unit: "万" });
  assert.match(ctx.usageTokenCoverageLabel(), /尚未采集/);
  ctx.usageSummary.llmUsageMissingCalls = 16;
  assert.match(ctx.usageTokenCoverageLabel(), /16 次调用缺失用量，按 0 计/);
  ctx.usageSummary.llmUsageMissingCalls = 0;
  assert.match(ctx.usageTokenCoverageLabel(), /均已记录/);
});
