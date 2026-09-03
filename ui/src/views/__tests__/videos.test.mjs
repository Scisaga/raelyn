import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createMediaVideosModule } from "../../app/modules/media-videos.js";
import { createUrlStateMethods } from "../../services/url-state.js";
import { createCommonViewMethods } from "../../shared/view-helpers.js";
import { createVideosViewMethods } from "../videos-model.js";

function context(overrides = {}) {
  return {
    ...createMediaVideosModule(),
    ...createCommonViewMethods(),
    ...createVideosViewMethods(),
    activeView: "library",
    libraryScope: "domain",
    selectedPlaylistId: "domain-1",
    libraryDomainMediaIds: ["media-1", "media-2"],
    mediaIndex: [
      { id: "media-1", name: "媒体一", provider: "youtube" },
      { id: "media-2", name: "媒体二", provider: "bilibili" },
      { id: "media-3", name: "域外媒体", provider: "youtube" },
    ],
    _toLocalInputValue(value) {
      const date = new Date(value);
      const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
      return local.toISOString().slice(0, 16);
    },
    loadVideos() {},
    ...overrides,
  };
}

test("域内来源记录通过 domain_id 表达基础范围，媒体筛选只表达显式子集", () => {
  const ctx = context({
    videoStatus: "ready",
    videoMediaIds: ["media-1"],
    videoQuery: "测试",
    videoFrom: "2026-09-01T08:00",
    videoTo: "2026-09-01T09:00",
  });

  const params = ctx._videoListParams(20);

  assert.equal(params.get("domain_id"), "domain-1");
  assert.equal(params.get("media_id_in"), "media-1");
  assert.equal(params.get("status"), "ready");
  assert.equal(params.get("q"), "测试");
  assert.equal(params.get("offset"), "20");
});

test("全局来源记录不携带观测域范围", () => {
  const ctx = context({ libraryScope: "global", videoMediaIds: [] });
  const params = ctx._videoListParams(0);

  assert.equal(params.has("domain_id"), false);
  assert.equal(params.has("media_id_in"), false);
});

test("资料库来源记录把显式筛选写入可恢复 URL", () => {
  const ctx = {
    ...context({
      libraryTab: "records",
      videoStatus: "ready",
      videoMediaIds: ["media-1"],
      videoQuery: "测试",
      videoFrom: "2026-09-01T08:00",
      videoTo: "2026-09-01T09:00",
    }),
    ...createUrlStateMethods({ settingsTabs: [] }),
  };

  const query = new URLSearchParams(ctx._buildSearchForView("library").slice(1));

  assert.equal(query.get("domain_id"), "domain-1");
  assert.equal(query.get("tab"), "records");
  assert.equal(query.get("scope"), "domain");
  assert.equal(query.get("media_id_in"), "media-1");
  assert.equal(query.get("status"), "ready");
  assert.equal(query.get("q"), "测试");
});

test("媒体筛选面板只展示当前范围，并在应用后提交显式选择", () => {
  let reloads = 0;
  const ctx = context({ loadVideos() { reloads += 1; } });

  assert.equal(ctx.videoMediaFilterLabel(), "全部媒体（2）");
  assert.deepEqual(ctx.videoMediaOptionIndex().map((item) => item.id), ["media-1", "media-2"]);

  ctx.videoOpenMediaFilter();
  ctx.videoToggleMediaDraft("media-2");
  assert.equal(ctx.videoMediaDraftSelected("media-2"), true);
  ctx.videoApplyMediaFilter();

  assert.deepEqual(ctx.videoMediaIds, ["media-2"]);
  assert.equal(ctx.videoMediaFilterLabel(), "媒体二");
  assert.equal(reloads, 1);
});

test("时间筛选将常用范围收进单个控件", () => {
  const ctx = context({
    videoFrom: "2026-08-31T09:00",
    videoTo: "2026-09-01T09:00",
  });

  assert.equal(ctx.videoTimeFilterLabel(), "最近 24 小时");
  ctx.videoFrom = "2026-08-25T09:00";
  assert.equal(ctx.videoTimeFilterLabel(), "最近 7 天");
});

test("来源记录工具栏不再渲染全部已选媒体标签", async () => {
  const template = await readFile(new URL("../../../templates/app/views/videos.html", import.meta.url), "utf8");

  assert.doesNotMatch(template, /x-for="m in videoSelectedMedia\(\)"/);
  assert.match(template, /videoOpenMediaFilter\(\)/);
  assert.match(template, /videoApplyMediaFilter\(\)/);
  assert.match(template, /videoOpenTimeFilter\(\)/);
  assert.match(template, />状态：已就绪</);
  assert.match(template, />应用筛选</);
});
