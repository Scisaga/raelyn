import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createNavItems } from "../../app/navigation.js";
import { createPlaybackModule } from "../../app/modules/playback.js";
import { createUrlStateMethods } from "../../services/url-state.js";
import { createPlaybackViewMethods } from "../playback-model.js";
import { createPlaylistViewMethods } from "../playlist-model.js";
import { createV2ViewMethods } from "../v2-model.js";

function installBrowserState({ pathname = "/", search = "" } = {}) {
  const values = new Map();
  globalThis.localStorage = {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
  };
  globalThis.window = {
    location: { pathname, search, origin: "http://local" },
    clearTimeout() {},
  };
  globalThis.history = {
    state: {},
    replaceState() {},
    pushState() {},
  };
}

test("主导航把观测域设置放在资料库下方", () => {
  const labels = createNavItems().filter((item) => !item.hidden && !item.system).map((item) => item.label);
  assert.deepEqual(labels, ["星域", "故事", "播放列表", "资料库", "观测域设置"]);
});

test("播放工作台按观测域周期统一加载视频与简报数据", async () => {
  installBrowserState({ pathname: "/domains/domain-1/playlist", search: "?date=2026-08-17&video_id=video-1&t=32" });
  const calls = [];
  const loadDays = [];
  const ctx = {
    ...createPlaybackModule(),
    ...createPlaylistViewMethods(),
    ...createPlaybackViewMethods(),
    activeView: "playlist",
    selectedPlaylistId: "domain-1",
    playlistPageId: "domain-1",
    playlistLoadToken: 0,
    playlistCalendarCount: 14,
    playlistDayVideos: [],
    playlistMediaCurrentTimeSec: 0,
    async ensureCurrentDomain() { return "domain-1"; },
    async api(path) {
      calls.push(path);
      if (path.includes("/detail")) return { name: "测试域", earliest_date: "2026-08-01", brief_granularity: "week" };
      return {};
    },
    playlistCalendarUpdateCount() {},
    playlistCalendarEnsureVisible() {},
    playlistPrefetchCalendarCounts() {},
    playlistMediaPause() {},
    async playlistLoadDay(date, options) {
      loadDays.push([date, options]);
      this.playlistCurrentVideo = { id: "video-1" };
      this.playlistDayVideos = [this.playlistCurrentVideo];
    },
    _syncUrl() {},
  };

  await ctx.loadPlaybackPage();

  assert.equal(ctx.playlistSelectedDate, "2026-08-17");
  assert.deepEqual(loadDays, [["2026-08-17", { autoPlay: false, preferredVideoId: "video-1" }]]);
  assert.equal(ctx.playbackPendingSeekSec, 32);
  assert.equal(calls.some((path) => path.includes("/events")), false);
  assert.equal(ctx.playlistGranularity(), "week");
});

test("简报标签仍通过统一周期入口加载视频，并忽略 URL 中的旧粒度", async () => {
  installBrowserState({ pathname: "/domains/domain-1/playlist", search: "?tab=brief&granularity=day&date=2026-08-19" });
  const loadDays = [];
  const ctx = {
    ...createPlaybackModule(),
    ...createPlaylistViewMethods(),
    ...createPlaybackViewMethods(),
    activeView: "playlist",
    playbackContentTab: "brief",
    selectedPlaylistId: "domain-1",
    playlistPageId: "domain-1",
    playlistLoadToken: 0,
    playlistCalendarCount: 14,
    playlistDayVideos: [],
    async ensureCurrentDomain() { return "domain-1"; },
    async api(path) {
      if (path.includes("/detail")) return { name: "测试域", earliest_date: "2026-08-01", brief_granularity: "week" };
      return {};
    },
    playlistCalendarUpdateCount() {},
    playlistCalendarEnsureVisible() {},
    playlistPrefetchCalendarCounts() {},
    playlistMediaPause() {},
    async playlistLoadDay(date) { loadDays.push(date); },
    _syncUrl() {},
  };

  await ctx.loadPlaybackPage();

  assert.deepEqual(loadDays, ["2026-08-17"]);
  assert.equal(ctx.playlistGranularity(), "week");
});

test("星域和播放页面离开时只清理各自资源", () => {
  installBrowserState();
  const playbackCalls = [];
  const playback = {
    ...createPlaybackModule(),
    ...createPlaybackViewMethods(),
    selectedPlaylistId: "domain-1",
    playlistSelectedDate: "2026-08-17",
    playlistCurrentVideo: { id: "video-1" },
    playlistMediaCurrentTimeSec: 9,
    playlistLoadToken: 0,
    playlistPeriodCountsToken: 0,
    _abortCtrl(name) { playbackCalls.push(name); },
    playlistMediaPause() { playbackCalls.push("media.pause"); },
    playlistStopBriefSpeech() { playbackCalls.push("brief.speech.stop"); },
    playlistResetMediaElements() { playbackCalls.push("media.reset"); },
  };
  playback.leavePlaybackPage();
  assert.ok(playbackCalls.includes("media.pause"));
  assert.equal(playbackCalls.some((value) => value.includes("EventMap")), false);

  const fieldCalls = [];
  const field = {
    ...createV2ViewMethods(),
    _fieldCursorSaveTimer: null,
    playlistEventMapStopPolling() { fieldCalls.push("field.poll.stop"); },
    playlistEventMapReset() { fieldCalls.push("field.webgl.reset"); },
  };
  field.leaveField();
  assert.deepEqual(fieldCalls, ["field.poll.stop", "field.webgl.reset"]);
});

test("新旧播放地址都恢复到独立播放视图，生成链接使用域路径", () => {
  installBrowserState({ pathname: "/domains/domain-1/playlist", search: "?date=2026-08-17" });
  const methods = createUrlStateMethods({ settingsTabs: [] });
  const ctx = {
    ...methods,
    navItems: createNavItems(),
    selectedPlaylistId: "domain-1",
    playlistPageId: "domain-1",
    playlistSelectedDate: "2026-08-17",
    playlistCurrentVideo: { id: "video-1" },
    playlistMediaCurrentTimeSec: 18,
    playbackPendingSeekSec: null,
  };
  assert.equal(ctx._parseViewFromLocation(), "playlist");
  assert.equal(ctx._viewPath("playlist"), "/domains/domain-1/playlist");
  const query = new URLSearchParams(ctx._buildSearchForView("playlist"));
  assert.equal(query.get("date"), "2026-08-17");
  assert.equal(query.get("video_id"), "video-1");
  assert.equal(query.get("t"), "18");

  window.location.pathname = "/playlist";
  window.location.search = "?playlist_id=domain-legacy&date=2026-08-01";
  assert.equal(ctx._parseViewFromLocation(), "playlist");
  ctx._applyQueryFromLocation("playlist");
  assert.equal(ctx.selectedPlaylistId, "domain-legacy");

  window.location.pathname = "/briefs";
  window.location.search = "?domain_id=domain-legacy&brief_id=brief-1&granularity=month&date=2026-08-01";
  assert.equal(ctx._parseViewFromLocation(), "playlist");
  ctx._applyQueryFromLocation("playlist");
  assert.equal(ctx.playbackContentTab, "brief");
  assert.equal(ctx.briefV2SelectedId, "brief-1");
  const briefQuery = new URLSearchParams(ctx._buildSearchForView("playlist"));
  assert.equal(briefQuery.get("tab"), "brief");
  assert.equal(briefQuery.get("brief_id"), "brief-1");
  assert.equal(briefQuery.get("granularity"), null);
  assert.equal(briefQuery.get("video_id"), "video-1");
});

test("原日历交互在播放页切换日期和跳转时走无自动播放的播放状态入口", () => {
  const selected = [];
  const jumped = [];
  const ctx = {
    ...createPlaylistViewMethods(),
    activeView: "playlist",
    playlistCalendarDragSuppressClickUntil: 0,
    playbackSetDate(date) { selected.push(date); },
    playbackJumpDays(delta) { jumped.push(delta); },
  };

  ctx.playlistCalendarSelectDate("2026-08-17");
  ctx.playlistJump(7);

  assert.deepEqual(selected, ["2026-08-17"]);
  assert.deepEqual(jumped, [7]);
});

test("播放页底部卡片按观测域配置生成周或月周期", () => {
  const ctx = {
    ...createPlaylistViewMethods(),
    activeView: "playlist",
    playlistDetail: { brief_granularity: "month" },
    playlistTimelineStart: "2026-05-01",
    playlistTimelineEnd: "2026-09-01",
    playlistSelectedDate: "2026-08-01",
    playlistCalendarAnchor: "2026-05-01",
    playlistCalendarCount: 5,
  };

  assert.equal(ctx.playlistGranularity(), "month");
  assert.equal(ctx.playlistCalendarGranularity(), "month");
  assert.equal(ctx.playlistJumpDelta("back"), -1);
  assert.deepEqual(ctx.playlistCalendarItems().map((item) => item.md), ["2026/05", "2026/06", "2026/07", "2026/08", "2026/09"]);

  ctx.playlistDetail.brief_granularity = "week";
  ctx.playlistTimelineStart = "2026-08-03";
  ctx.playlistTimelineEnd = "2026-08-31";
  ctx.playlistSelectedDate = "2026-08-17";
  ctx.playlistCalendarAnchor = "2026-08-03";
  const weeks = ctx.playlistCalendarItems();
  assert.equal(weeks[0].md, "8/3");
  assert.equal(weeks[0].weekday, "~8/9");
});

test("简报中的视频引用保持简报左栏并在右侧定位播放", async () => {
  const calls = [];
  const selected = [];
  const video = { id: "video-2", url: "https://example.test/video-2", timeline_at: "2026-08-17T09:00:00Z" };
  const ctx = {
    ...createPlaylistViewMethods(),
    activeView: "playlist",
    playbackContentTab: "brief",
    playlistDetail: { brief_granularity: "week" },
    playlistPageId: "domain-1",
    selectedPlaylistId: "domain-1",
    playlistSelectedDate: "2026-08-17",
    playlistDayVideos: [],
    async api(path) { calls.push(path); return [video]; },
    playlistVideoTimelineAt(item) { return item.timeline_at; },
    async playlistLoadDay() { this.playlistDayVideos = [video]; },
    async playbackSelectVideo(item) { selected.push(item.id); },
    playlistStopBriefSpeech() {},
    _syncUrl() {},
  };

  await ctx.playlistSelectVideoByUrl(video.url);

  assert.match(calls[0], /granularity=week/);
  assert.equal(ctx.playbackContentTab, "brief");
  assert.equal(ctx.playbackMobileTab, "transcript");
  assert.deepEqual(selected, ["video-2"]);
});

test("批量生成只按当前域周期计数，跨周边界不会少算", () => {
  const ctx = {
    ...createV2ViewMethods(),
    briefBatchGranularity: "week",
    briefBatchFrom: "2026-08-30",
    briefBatchTo: "2026-08-31",
  };

  assert.equal(ctx.briefBatchEstimatedPeriods(), 2);
});

test("播放模板左栏切换记录和简报，右栏播放器与周期导航保持不变", async () => {
  const template = await readFile(new URL("../../../templates/app/views/playback-v2.html", import.meta.url), "utf8");
  const settings = await readFile(new URL("../../../templates/app/views/domain-settings.html", import.meta.url), "utf8");
  assert.match(template, /lg:grid-cols-\[38%_62%\]/);
  assert.match(template, /md:order-1 md:h-\[62%\]/);
  assert.match(template, /md:order-2 md:h-\[38%\]/);
  assert.match(template, /md:hidden/);
  assert.match(template, /本期记录/);
  assert.match(template, /playbackSetContentTab\('records'\)/);
  assert.match(template, /playbackSetContentTab\('brief'\)/);
  assert.match(template, /x-ref="playlistBriefContentEl"/);
  assert.match(template, /playlistBriefClick\(\$event\)/);
  assert.doesNotMatch(template, /playbackSetBriefGranularity|openBriefGenerationSettings|生成当前周期/);
  assert.match(template, /x-ref="playlistVideoEl"/);
  assert.match(template, /@click="playlistMediaTogglePlay\(\)"/);
  assert.match(template, /@keydown\.space\.prevent="playlistMediaTogglePlay\(\)"/);
  assert.match(template, /data-playback-brief-source/);
  assert.match(template, /x-show="playbackContentTab==='brief' && playlistCurrentVideo"/);
  assert.match(template, /playlistCurrentVideo\?\.media_avatar_asset/);
  assert.match(template, /playlistCurrentVideo\?\.media_name/);
  assert.match(template, /playlistCurrentVideo\?\.title/);
  assert.ok(template.indexOf("data-playback-brief-source") < template.indexOf(">转写<"));
  assert.match(template, /转写/);
  assert.match(template, /固定 64px 周期导航/);
  assert.match(template, /playlistCalendarLayoutStyle/);
  assert.match(template, /playlistCalendarTracks/);
  assert.match(template, /playlistCalendarPointerDown/);
  assert.match(template, /playlistCalendarDots/);
  assert.match(template, /playlistJumpDelta\('back_big'\)/);
  assert.match(template, /bg-emerald-500\/15/);
  assert.ok(template.indexOf('x-ref="playlistBriefContentEl"') < template.indexOf('x-ref="playlistVideoEl"'));
  assert.match(settings, /简报生成/);
  assert.match(settings, /briefGenerationGranularityDraft/);
  assert.match(settings, /openBriefBatchGeneration/);
  assert.doesNotMatch(template, /overflow-x-auto/);
  assert.doesNotMatch(template, /grid-cols-\[17rem_minmax\(0,1fr\)_19rem\]|结构化引用|事件审核|周期总结|播放列表设置/);
});
