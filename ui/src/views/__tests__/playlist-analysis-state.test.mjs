import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createPlaylistViewMethods } from "../playlist-model.js";

function createContext(summary, overrides = {}) {
  const methods = createPlaylistViewMethods();
  return {
    ...methods,
    playlistAnalysisSummary: summary,
    playlistAnalysisSummaryLoading: false,
    playlistAnalysisSummaryError: "",
    playlistAnalysisTab: "trend",
    playlistAnalysisCandidates: [],
    playlistAnalysisCandidatesLoaded: false,
    playlistAnalysisCandidatesLoading: false,
    formatInteger(value) {
      return Number(value).toLocaleString("en-US");
    },
    $nextTick(callback) {
      callback();
    },
    ...overrides,
  };
}

test("embedding 已就绪但没有 ready run 时明确显示语义快照未构建", () => {
  const ctx = createContext({
    analysis_dirty: true,
    running: false,
    last_ready_run_id: null,
    event_total: 4,
    event_embedded: 4,
    event_skipped: 1,
    event_failed: 0,
  });

  assert.equal(ctx.playlistAnalysisEmbeddingCoverageLabel(), "4/4");
  assert.equal(ctx.playlistAnalysisEmbeddingStatusLabel(), "已就绪");
  assert.equal(ctx.playlistAnalysisSummaryShortBadgeLabel(), "快照未构建");
  assert.equal(ctx.playlistAnalysisSummaryBadgeLabel(), "语义快照未构建");
  assert.equal(ctx.playlistAnalysisHeaderContextLabel(), "尚未构建语义快照");
  assert.equal(ctx.playlistAnalysisCandidatesEmptyLabel(), "尚未构建语义快照。");
});

test("已有 ready run 的 dirty 状态显示语义快照待更新", () => {
  const ctx = createContext({
    analysis_dirty: true,
    running: false,
    last_ready_run_id: "run-1",
    event_total: 4,
    event_embedded: 3,
  });

  assert.equal(ctx.playlistAnalysisEmbeddingStatusLabel(), "部分就绪");
  assert.equal(ctx.playlistAnalysisSummaryShortBadgeLabel(), "快照待更新");
  assert.equal(ctx.playlistAnalysisSummaryBadgeLabel(), "语义快照待更新");
});

test("没有 ready run 时切换或加载事件页都不会请求候选接口", async () => {
  let loadCount = 0;
  let apiCount = 0;
  const ctx = createContext(
    {
      analysis_dirty: true,
      running: false,
      last_ready_run_id: null,
      event_total: 4,
      event_embedded: 4,
    },
    {
      playlistPageId: "playlist-1",
      playlistAnalysisStopProjectionPlayback() {},
      playlistAnalysisReleaseProjectionWindowDrag() {},
      playlistLoadAnalysisCandidates() {
        loadCount += 1;
      },
      async api() {
        apiCount += 1;
        return [];
      },
    }
  );

  ctx.playlistAnalysisSetTab("events");
  await createPlaylistViewMethods().playlistLoadAnalysisCandidates.call(ctx);

  assert.equal(ctx.playlistAnalysisTab, "events");
  assert.equal(loadCount, 0);
  assert.equal(apiCount, 0);
});

test("ready 语义快照确实没有变化点时才显示未发现语义变化点", () => {
  const ctx = createContext({
    analysis_dirty: false,
    running: false,
    last_ready_run_id: "run-1",
    event_total: 4,
    event_embedded: 4,
  });

  assert.equal(ctx.playlistAnalysisCandidatesEmptyLabel(), "当前语义快照未发现语义变化点。");
  assert.equal(ctx.playlistAnalysisEventTypeLabel("event_regime_shift"), "语义转折");
  assert.equal(ctx.playlistAnalysisDetectionKindLabel({ detection_method: "event_embedding_regime_v1" }), "语义漂移");
});

test("周期语义轨迹与标准化语义变化文案不冒充事件级 embedding 地图", () => {
  const ctx = createContext(null);
  const metricTitles = ctx.playlistAnalysisMetricDefs().map((item) => item.title);
  const template = readFileSync(new URL("../../../templates/app/views/playlist.html", import.meta.url), "utf8");

  assert.deepEqual(metricTitles.slice(0, 3), ["日标准化语义变化", "周标准化语义变化", "月标准化语义变化"]);
  assert.equal(
    ctx.playlistAnalysisBreakpointTitle({ date: "2026-07-21", score: 2.5, strengthReady: true, granularity: "day" }),
    "语义变化点 2026-07-21 · 标准化语义变化 2.50 · day"
  );
  assert.match(template, /事件时间演化/);
  assert.match(template, /焦点窗口周期语义轨迹/);
  assert.match(template, /每个点代表一个日级周期 centroid，仅用于观察相邻周期变化；不是事件级二维 embedding 地图。/);
  assert.match(template, /代表证据视频/);
  assert.doesNotMatch(template, /候选断点|焦点窗口事件语义分布/);
});

test("趋势页默认使用正常观察域并选中截至今天最近有数据的一年", () => {
  const summary = {
    last_ready_run_id: "run-1",
    signal_start_date: "1920-01-01",
    signal_end_date: "2030-01-01",
  };
  const ctx = createContext(summary, {
    playlistTimelineStart: "2008-05-24",
    playlistTimelineEnd: "2026-07-21",
    playlistAnalysisTimelineScope: "normal",
    playlistAnalysisTimelineDensity: [],
    playlistAnalysisRangeStart: "",
    playlistAnalysisRangeEnd: "",
    playlistAnalysisFullRangeStart: "",
    playlistAnalysisFullRangeEnd: "",
    playlistAnalysisProjectionWindowMonths: 3,
    _todayIsoLocal() {
      return "2026-07-21";
    },
  });

  ctx.playlistAnalysisInitializeRangeFromSummary(summary);
  ctx.playlistAnalysisTimelineDensity = [
    { granularity: "month", period_date: "2025-06-01", event_count: 8 },
    { granularity: "month", period_date: "2026-07-01", event_count: 32 },
  ];
  ctx.playlistAnalysisInitializeDefaultRange();

  assert.deepEqual(ctx.playlistAnalysisTimelineBounds(), { start: "2008-05-24", end: "2026-07-21" });
  assert.equal(ctx.playlistAnalysisRangeStart, "2025-08-01");
  assert.equal(ctx.playlistAnalysisRangeEnd, "2026-07-21");
  assert.equal(ctx.playlistAnalysisTimelineMonths()[0].ym, "2008-05");
  assert.equal(ctx.playlistAnalysisTimelineMonths().at(-1).ym, "2026-07");
  assert.equal(ctx.playlistAnalysisTimelineDensityBars().length, 2);
  assert.equal(ctx.playlistAnalysisTimelineHasExtendedRange(), true);

  ctx.playlistAnalysisTimelineScope = "full";
  ctx._playlistAnalysisTimelineMonthsCache = null;
  assert.deepEqual(ctx.playlistAnalysisTimelineBounds(), { start: "1920-01-01", end: "2030-01-01" });
});

test("时间轴模板展示月事件量提示并允许主动查看全量范围", () => {
  const template = readFileSync(new URL("../../../templates/app/views/playlist.html", import.meta.url), "utf8");

  assert.match(template, /月事件量/);
  assert.match(template, /正常范围/);
  assert.match(template, /playlistAnalysisSetTimelineScope\('full'\)/);
  assert.match(template, /playlistAnalysisTimelineDensityBars\(\)/);
});
