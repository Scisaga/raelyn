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
    briefV2SelectedId: "brief-1",
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
  assert.equal(ctx.briefV2SelectedId, "brief-1");
  assert.equal(ctx.playbackMobileTab, "transcript");
  assert.deepEqual(selected, ["video-2"]);
});

test("结构化来源引用按视频 ID 定位，事件引用进入生成时星域快照", async () => {
  const selected = [];
  const video = { id: "video-2", url: "https://example.test/video-2", timeline_at: "2026-08-17T09:00:00Z" };
  const playlistCtx = {
    ...createPlaylistViewMethods(),
    activeView: "playlist",
    playbackContentTab: "brief",
    briefV2SelectedId: "brief-1",
    playlistDetail: { brief_granularity: "week" },
    playlistSelectedDate: "2026-08-17",
    playlistDayVideos: [video],
    playlistVideoTimelineAt(item) { return item.timeline_at; },
    async playbackSelectVideo(item) { selected.push(item.id); },
    playlistStopBriefSpeech() {},
    _syncUrl() {},
  };

  await playlistCtx.playlistSelectVideoById("video-2");

  assert.deepEqual(selected, ["video-2"]);
  assert.equal(playlistCtx.briefV2SelectedId, "brief-1");
  assert.equal(playlistCtx.playbackContentTab, "brief");

  const views = [];
  const fieldCtx = {
    ...createV2ViewMethods(),
    fieldRequestedSnapshotId: "",
    selectedPlaylistId: "domain-1",
    async api() { return { status: "ready", snapshot_id: "snapshot-1" }; },
    switchView(view) { views.push(view); },
  };
  await fieldCtx.briefOpenReference({
    object_type: "canonical",
    object_id: "event-1",
    snapshot_id: "snapshot-1",
  });

  assert.equal(fieldCtx.fieldRequestedSnapshotId, "snapshot-1");
  assert.equal(fieldCtx.fieldRequestedCanonicalId, "event-1");
  assert.deepEqual(views, ["field"]);
});

test("简报故事引用直接打开当前故事并高亮引用阶段，不请求三维快照", async () => {
  const views = [];
  const loaded = [];
  let scheduled = 0;
  const ctx = {
    ...createV2ViewMethods(),
    activeView: "playlist",
    selectedPlaylistId: "domain-1",
    fieldRequestedSnapshotId: "snapshot-before",
    async api() { throw new Error("故事引用不应请求三维快照"); },
    async loadStoryDetail(id, options) {
      loaded.push([id, options.snapshotId]);
      this.storyDetail = {
        trajectory: {
          nodes: [
            { canonical_id: "event-stage-1", event_time_start: "2026-08-16T00:00:00Z" },
            { canonical_id: "event-stage-2", event_time_start: "2026-08-17T00:00:00Z" },
          ],
          edges: [],
        },
      };
    },
    switchView(view) { views.push(view); },
    storyScheduleInitialPosition() { scheduled += 1; },
  };

  await ctx.briefOpenReference({
    object_type: "story",
    object_id: "story-1",
    snapshot_id: "snapshot-old",
    context: { focus_canonical_id: "event-stage-2" },
  });

  assert.deepEqual(loaded, [["story-1", ""]]);
  assert.equal(ctx.fieldRequestedSnapshotId, "");
  assert.equal(ctx.storySelectedId, "story-1");
  assert.equal(ctx.storySelectedSnapshotId, "");
  assert.equal(ctx.storyReferenceFocusCanonicalId, "event-stage-2");
  assert.equal(ctx.storyFocusIndex, 1);
  assert.equal(scheduled, 1);
  assert.deepEqual(views, ["stories"]);
});

test("旧简报故事引用只用长期修订解析阶段，再打开当前故事", async () => {
  const calls = [];
  const loaded = [];
  const ctx = {
    ...createV2ViewMethods(),
    activeView: "playlist",
    selectedPlaylistId: "domain-1",
    async api(path) {
      calls.push(path);
      return {
        trajectory: {
          edges: [{ edge_id: "edge-old", target_canonical_id: "event-stage-2" }],
        },
      };
    },
    async loadStoryDetail(id, options) {
      loaded.push([id, options.snapshotId]);
      this.storyDetail = {
        trajectory: {
          nodes: [{ canonical_id: "event-stage-2", event_time_start: "2026-08-17T00:00:00Z" }],
          edges: [],
        },
      };
    },
    switchView() {},
    storyScheduleInitialPosition() {},
  };

  await ctx.briefOpenReference({
    object_type: "story",
    object_id: "story-1",
    snapshot_id: "snapshot-old",
    context: { edge_id: "edge-old" },
  });

  assert.deepEqual(calls, [
    "/domains/domain-1/stories/story-1?snapshot_id=snapshot-old",
  ]);
  assert.deepEqual(loaded, [["story-1", ""]]);
  assert.equal(ctx.storyReferenceFocusCanonicalId, "event-stage-2");
  assert.equal(ctx.storySelectedSnapshotId, "");
});

test("简报故事读取失败时保留原故事状态并停留当前页面", async () => {
  const errors = [];
  const views = [];
  const previousDetail = { story_identity_id: "story-before" };
  const ctx = {
    ...createV2ViewMethods(),
    activeView: "playlist",
    selectedPlaylistId: "domain-1",
    storySelectedId: "story-before",
    storySelectedSnapshotId: "snapshot-before",
    storyReferenceFocusCanonicalId: "event-before",
    storyDetail: previousDetail,
    storyFocusIndex: 3,
    async loadStoryDetail() {
      this.storySelectedId = "story-failed";
      throw new Error("故事服务暂不可用");
    },
    switchView(view) { views.push(view); },
    toastError(message) { errors.push(message); },
  };

  await ctx.briefOpenReference({
    object_type: "story",
    object_id: "story-failed",
    context: { focus_canonical_id: "event-failed" },
  });

  assert.deepEqual(views, []);
  assert.equal(ctx.storySelectedId, "story-before");
  assert.equal(ctx.storySelectedSnapshotId, "snapshot-before");
  assert.equal(ctx.storyReferenceFocusCanonicalId, "event-before");
  assert.equal(ctx.storyDetail, previousDetail);
  assert.equal(ctx.storyFocusIndex, 3);
  assert.match(errors[0], /无法打开故事：故事服务暂不可用/);
});

test("简报真实事件的历史快照失效时，仅在当前快照仍有同一身份才显式定位", async () => {
  const calls = [];
  const views = [];
  const notices = [];
  const ctx = {
    ...createV2ViewMethods(),
    activeView: "playlist",
    selectedPlaylistId: "domain-1",
    async api(path) {
      calls.push(path);
      if (path.includes("snapshot_id=snapshot-old")) {
        throw new Error("409: requested event map snapshot is not available");
      }
      if (path.includes("/history")) {
        return { revisions: [{ snapshot_id: "snapshot-current" }] };
      }
      return { status: "ready", snapshot_id: "snapshot-current" };
    },
    switchView(view) { views.push(view); },
    toastPush(payload) { notices.push(payload); },
  };

  await ctx.briefOpenReference({
    object_type: "canonical",
    object_id: "event-1",
    snapshot_id: "snapshot-old",
  });

  assert.equal(calls.length, 3);
  assert.equal(ctx.fieldRequestedSnapshotId, "");
  assert.equal(ctx.fieldRequestedCanonicalId, "event-1");
  assert.deepEqual(views, ["field"]);
  assert.match(notices[0].message, /当前星域定位同一真实事件/);
});

test("简报真实事件不存在可证明的当前版本时不进入错误星域", async () => {
  const views = [];
  const errors = [];
  let callCount = 0;
  const ctx = {
    ...createV2ViewMethods(),
    activeView: "playlist",
    selectedPlaylistId: "domain-1",
    async api() {
      callCount += 1;
      if (callCount === 1) throw new Error("409: requested event map snapshot is not available");
      return { status: "pending", snapshot_id: null };
    },
    switchView(view) { views.push(view); },
    toastError(message) { errors.push(message); },
  };

  await ctx.briefOpenReference({
    object_type: "canonical",
    object_id: "event-retired",
    snapshot_id: "snapshot-old",
  });

  assert.deepEqual(views, []);
  assert.equal(ctx.fieldRequestedSnapshotId, undefined);
  assert.match(errors[0], /历史三维星域已不可用/);
});

test("简报证据的历史快照失效时回到可长期读取的证据来源", async () => {
  const played = [];
  const notices = [];
  const ctx = {
    ...createV2ViewMethods(),
    activeView: "playlist",
    selectedPlaylistId: "domain-1",
    async api() { throw new Error("409: requested event map snapshot is not available"); },
    async playlistPlayBriefEvidence(revisionId, videoId) { played.push([revisionId, videoId]); },
    toastPush(payload) { notices.push(payload); },
  };

  await ctx.briefOpenReference({
    object_type: "evidence",
    object_id: "revision-1",
    evidence_revision_id: "revision-1",
    snapshot_id: "snapshot-old",
    context: { source_video_id: "video-1" },
  });

  assert.deepEqual(played, [["revision-1", "video-1"]]);
  assert.match(notices[0].message, /对应的来源视频/);
});

test("故事链接分别保留显式历史修订与简报引用阶段", () => {
  installBrowserState({
    pathname: "/stories",
    search: "?domain_id=domain-1&story_id=story-1&focus_canonical_id=event-stage-2",
  });
  const methods = createUrlStateMethods({ settingsTabs: [] });
  const ctx = {
    ...methods,
    selectedPlaylistId: "domain-1",
    storySelectedId: "story-1",
    storySelectedSnapshotId: "snapshot-old",
    storyReferenceFocusCanonicalId: "event-stage-2",
  };

  const params = new URLSearchParams(ctx._buildSearchForView("stories"));

  assert.equal(params.get("domain_id"), "domain-1");
  assert.equal(params.get("story_id"), "story-1");
  assert.equal(params.get("snapshot_id"), "snapshot-old");
  assert.equal(params.get("focus_canonical_id"), "event-stage-2");

  ctx.storyReferenceFocusCanonicalId = "";
  ctx._applyQueryFromLocation("stories");
  assert.equal(ctx.storyReferenceFocusCanonicalId, "event-stage-2");
});

test("简报来源记录不依赖短期三维快照", async () => {
  const played = [];
  const ctx = {
    ...createV2ViewMethods(),
    activeView: "playlist",
    selectedPlaylistId: "domain-1",
    async api() { throw new Error("来源记录不应请求星域快照"); },
    async playlistSelectVideoById(videoId) { played.push(videoId); },
  };

  await ctx.briefOpenReference({
    object_type: "source",
    object_id: "video-1",
    snapshot_id: "snapshot-old",
  });

  assert.deepEqual(played, ["video-1"]);
});

test("简报引用校验返回前切换周期时不再执行过期跳转", async () => {
  let resolveManifest;
  const manifest = new Promise((resolve) => { resolveManifest = resolve; });
  const views = [];
  const ctx = {
    ...createV2ViewMethods(),
    activeView: "playlist",
    selectedPlaylistId: "domain-1",
    playlistSelectedDate: "2026-08-17",
    briefV2Detail: { id: "brief-1" },
    async api() { return manifest; },
    switchView(view) { views.push(view); },
  };

  const opening = ctx.briefOpenReference({
    object_type: "canonical",
    object_id: "event-1",
    snapshot_id: "snapshot-1",
  });
  ctx.playlistSelectedDate = "2026-08-24";
  ctx.briefV2Detail = { id: "brief-2" };
  resolveManifest({ status: "ready", snapshot_id: "snapshot-1" });
  await opening;

  assert.deepEqual(views, []);
  assert.equal(ctx.fieldRequestedCanonicalId, undefined);
});

test("播放证据使用后端给出的精确时间，不按字符区间估算", async () => {
  const calls = [];
  const selected = [];
  const ctx = {
    ...createPlaylistViewMethods(),
    activeView: "playlist",
    selectedPlaylistId: "domain-1",
    async api(path) {
      calls.push(path);
      return {
        source: { video_id: "video-1" },
        evidence: [
          { playback_position_seconds: null, char_start: 20, char_end: 80 },
          { playback_position_seconds: 42.5, char_start: 100, char_end: 160 },
        ],
      };
    },
    async playlistSelectVideoById(videoId, options) { selected.push([videoId, options.seekSec]); },
  };

  await ctx.playlistPlayBriefEvidence("revision-1", "video-fallback");

  assert.deepEqual(calls, ["/domains/domain-1/evidence/revision-1"]);
  assert.deepEqual(selected, [["video-1", 42.5]]);
});

test("播放器选择视频时把证据秒数保留到媒体元数据就绪", async () => {
  const selected = [];
  const ctx = {
    ...createPlaybackViewMethods(),
    playlistStopBriefSpeech() {},
    async playlistSelectVideo(video, options) { selected.push([video.id, options.autoPlay]); },
  };

  await ctx.playbackSelectVideo({ id: "video-1" }, { autoPlay: true, seekSec: 42.5 });

  assert.equal(ctx.playbackPendingSeekSec, 42.5);
  assert.equal(ctx.playbackMobileTab, "transcript");
  assert.deepEqual(selected, [["video-1", true]]);
});

test("按周期读取简报时同步取得结构化引用供正文跳转", async () => {
  const calls = [];
  const markdown = [
    "## 星域引用",
    "",
    '<a id="field-ref-1"></a> [1] 政策发布',
  ].join("\n");
  const ctx = {
    ...createPlaylistViewMethods(),
    playlistPageId: "domain-1",
    selectedPlaylistId: "domain-1",
    playlistDetail: { brief_granularity: "day" },
    playlistLoadToken: 1,
    playlistBriefGeneratingKey: "",
    playlistBriefHtmlCache: new Map(),
    playlistBriefMarkdownCache: new Map(),
    playlistBriefDetailCache: new Map(),
    _cacheGet() { return null; },
    _cacheSet() {},
    _abortCtrl() {},
    playlistStopBriefSpeech() {},
    _playlistSetBriefSourceState() {},
    _briefToSpeechText(value) { return value; },
    async api(path) {
      calls.push(path);
      if (path.includes("/structured")) {
        return {
          id: "brief-1",
          snapshot_id: "snapshot-1",
          markdown_url: "/api/assets/asset-1/content",
          references: [{
            anchor: "field-ref-1",
            position: 1,
            object_type: "canonical",
            object_id: "event-1",
            snapshot_id: "snapshot-1",
            label: "政策发布",
            web_url: "/field?canonical_id=event-1&snapshot_id=snapshot-1",
            context: { source_video_id: "video-1" },
          }],
        };
      }
      return {
        id: "brief-1",
        status: "ready",
        reference_count: 1,
        markdown_asset: { id: "asset-1" },
      };
    },
    async fetchWithApiAuth() {
      return { status: 200, ok: true, async text() { return markdown; } };
    },
  };

  await ctx.playlistLoadBrief("2026-08-17", { loadToken: 1 });

  assert.ok(calls.some((path) => path === "/briefs/brief-1/structured"));
  assert.equal(ctx.briefV2Detail?.snapshot_id, "snapshot-1");
  assert.match(ctx.playlistBriefHtml, /data-brief-reference-anchor="field-ref-1"/);
  assert.match(ctx.playlistBriefHtml, /data-brief-video-id="video-1"/);
});

test("星域引用列表点击后分别播放来源或打开对象", () => {
  const played = [];
  const opened = [];
  const consumed = [];
  const ctx = {
    ...createPlaylistViewMethods(),
    playlistBriefDragSuppressClickUntil: 0,
    briefV2Detail: {
      references: [{ anchor: "field-ref-1", object_type: "canonical", object_id: "event-1" }],
    },
    playlistSelectVideoById(id) { played.push(id); },
    briefOpenReference(reference) { opened.push(reference.object_id); },
  };
  const clickEvent = (selector, attribute, value) => ({
    button: 0,
    target: {
      closest(candidate) {
        if (candidate !== selector) return null;
        return { getAttribute(name) { return name === attribute ? value : ""; } };
      },
    },
    preventDefault() { consumed.push("prevented"); },
    stopPropagation() { consumed.push("stopped"); },
  });

  ctx.playlistBriefClick(clickEvent("a[data-brief-video-id]", "data-brief-video-id", "video-1"));
  ctx.playlistBriefClick(clickEvent("a[data-brief-reference-anchor]", "data-brief-reference-anchor", "field-ref-1"));

  assert.deepEqual(played, ["video-1"]);
  assert.deepEqual(opened, ["event-1"]);
  assert.equal(consumed.length, 4);
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
