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
  assert.match(html, /<p id="field-ref-7"/);
});
