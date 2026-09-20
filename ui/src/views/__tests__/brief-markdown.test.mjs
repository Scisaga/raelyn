import assert from "node:assert/strict";
import test from "node:test";

import { createPlaylistViewMethods } from "../playlist-model.js";

function renderer() {
  const methods = createPlaylistViewMethods();
  return {
    ...methods,
    playlistDayVideos: [],
  };
}

test("简报正文按 Markdown 渲染表格、换行和有序列表", () => {
  const ctx = renderer();
  const html = ctx._briefToHtml([
    "## 宏观判断",
    "",
    "| 维度 | 核心判断 | 依据 |",
    "| :--- | :--- | ---: |",
    "| 政策 | **结构优化**<br>保持定力 | 2 万亿元 |",
    "",
    "1. 第一项",
    "2. 第二项",
  ].join("\n"));

  assert.match(html, /<table>/);
  assert.match(html, /<th scope="col" data-align="left">维度<\/th>/);
  assert.match(html, /<th scope="col" data-align="right">依据<\/th>/);
  assert.match(html, /<strong>结构优化<\/strong><br>保持定力/);
  assert.match(html, /<ol[^>]*><li>第一项<\/li><li>第二项<\/li><\/ol>/);
  assert.doesNotMatch(html, /:---|\| 核心判断 \|/);
});

test("简报 Markdown 只放行显式换行和结构化引用锚点", () => {
  const ctx = renderer();
  const html = ctx._briefToHtml([
    '<script>alert("x")</script>',
    '<img src=x onerror=alert("x")>',
    '<br onclick="alert(1)">',
    '<a id="field-ref-7"></a>引用段落',
  ].join("\n\n"));

  assert.doesNotMatch(html, /<script>|<img\s|<br\s+onclick=/);
  assert.match(html, /&lt;script&gt;alert\(&quot;x&quot;\)&lt;\/script&gt;/);
  assert.match(html, /&lt;br onclick=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html, /<div id="field-ref-7"/);
});

test("星域引用合并结构化数据并渲染为可跳转的紧凑列表", () => {
  const ctx = {
    ...renderer(),
    briefV2Detail: {
      references: [
        {
          anchor: "field-ref-1",
          position: 1,
          object_type: "canonical",
          label: "政策发布",
          web_url: "/field?canonical_id=event-1&snapshot_id=snapshot-1",
          context: { source_video_id: "video-1" },
        },
        {
          anchor: "field-ref-2",
          position: 2,
          object_type: "source",
          object_id: "video-2",
          label: "来源记录 · 发布会回放",
          web_url: "/video?video_id=video-2",
          context: {},
        },
        {
          anchor: "field-ref-3",
          position: 3,
          object_type: "story",
          object_id: "story-1",
          snapshot_id: "snapshot-1",
          label: "故事 · 政策传导",
          web_url: "/stories?domain_id=domain-1&story_id=story-1&focus_canonical_id=event-stage-2",
          context: { focus_canonical_id: "event-stage-2" },
        },
        {
          anchor: "field-ref-4",
          position: 4,
          object_type: "evidence",
          object_id: "revision-1",
          evidence_revision_id: "revision-1",
          label: "证据 · 政策发布",
          web_url: "/field?mode=verify&evidence_id=revision-1&snapshot_id=snapshot-1",
          context: { source_video_id: "video-1" },
        },
      ],
      playlist_id: "domain-1",
    },
  };
  const html = ctx._briefToHtml([
    "## 星域引用",
    "",
    '<a id="field-ref-1"></a> [1] 政策发布',
    '<a id="field-ref-2"></a> [2] 来源记录 · 发布会回放',
    '<a id="field-ref-3"></a> [3] 故事 · 政策传导',
    '<a id="field-ref-4"></a> [4] 证据 · 政策发布',
  ].join("\n"));

  assert.match(html, /raelyn-brief-reference-heading/);
  assert.equal((html.match(/raelyn-brief-reference-list"/g) || []).length, 1);
  assert.match(html, /data-brief-reference-anchor="field-ref-1"/);
  assert.match(html, /href="\/video\?video_id=video-1" data-brief-video-id="video-1"/);
  assert.match(html, /播放证据/);
  assert.match(html, /data-brief-video-id="video-2"/);
  assert.match(html, /href="\/stories\?domain_id=domain-1&amp;story_id=story-1&amp;focus_canonical_id=event-stage-2"/);
  assert.match(html, />查看故事</);
  assert.doesNotMatch(html, /查看航迹/);
  assert.doesNotMatch(html, /story_id=story-1[^\"]*snapshot_id/);
  assert.match(html, /data-brief-evidence-id="revision-1" data-brief-evidence-video-id="video-1"/);
  assert.match(html, />发布会回放</);
  assert.doesNotMatch(html, />来源记录 · 发布会回放</);
});
