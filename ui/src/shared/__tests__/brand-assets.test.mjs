import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const uiDir = resolve(fileURLToPath(new URL("../../..", import.meta.url)));

function pngSize(buffer) {
  assert.equal(buffer.subarray(1, 4).toString("ascii"), "PNG");
  return [buffer.readUInt32BE(16), buffer.readUInt32BE(20)];
}

function markPath(svg) {
  const match = svg.match(/<path\s+d="([^"]+)"\s+fill="url\(#mark\)"/s);
  assert.ok(match);
  return match[1];
}

function alphaBounds(path) {
  const result = spawnSync(
    process.env.FFMPEG_BIN || "ffmpeg",
    ["-hide_banner", "-i", path, "-vf", "alphaextract,bbox", "-frames:v", "1", "-f", "null", "-"],
    { encoding: "utf8" }
  );
  assert.equal(result.status, 0, result.stderr);
  const match = result.stderr.match(/crop=(\d+):(\d+):(\d+):(\d+)/);
  assert.ok(match, result.stderr);
  return match.slice(1).map(Number);
}

test("侧栏、启动门面与 favicon 使用同一版事件视界品牌标识", async () => {
  const [sidebar, startup, head, mark, markPng, glyph, glyphPng, favicon, faviconPng, brandBuilder] = await Promise.all([
    readFile(resolve(uiDir, "templates/app/components/sidebar.html"), "utf8"),
    readFile(resolve(uiDir, "templates/app/layout/app-shell.html"), "utf8"),
    readFile(resolve(uiDir, "templates/app/layout/head.html"), "utf8"),
    readFile(resolve(uiDir, "assets/brand/raelyn-event-horizon.svg"), "utf8"),
    readFile(resolve(uiDir, "assets/brand/raelyn-event-horizon.png")),
    readFile(resolve(uiDir, "assets/brand/raelyn-glyph.svg"), "utf8"),
    readFile(resolve(uiDir, "assets/brand/raelyn-glyph.png")),
    readFile(resolve(uiDir, "assets/brand/raelyn-favicon.svg"), "utf8"),
    readFile(resolve(uiDir, "assets/brand/raelyn-favicon.png")),
    readFile(resolve(uiDir, "scripts/build-brand-logo.mjs"), "utf8"),
  ]);

  assert.match(sidebar, /\/static\/brand\/raelyn-event-horizon\.svg/);
  assert.doesNotMatch(sidebar, /\/static\/brand\/logo-y\.png/);
  assert.match(startup, /\/static\/brand\/raelyn-glyph\.svg/);
  assert.doesNotMatch(startup, /\/static\/brand\/logo-r\.png/);
  assert.match(head, /rel="icon" href="\/static\/brand\/raelyn-favicon\.svg/);
  assert.match(head, /rel="icon" href="\/static\/brand\/favicon-32\.png/);
  assert.match(mark, /<title[^>]*>RAELYN 事件视界<\/title>/);
  const glyphPath = markPath(glyph);
  assert.equal(markPath(mark), glyphPath);
  assert.equal(markPath(favicon), glyphPath);
  assert.match(glyphPath, /^M255 1019c0-1/);
  assert.doesNotMatch(glyphPath, /M230 196/);
  assert.match(mark, /rotate\(-14 32 33\)/);
  assert.match(mark, /rx="24"\s+ry="7\.2"/s);
  assert.match(mark, /M8 33C8 38\.6 18\.8 40\.2 32 40\.2C45\.2 40\.2 56 38\.6 56 33/);
  assert.match(mark, /matrix\(0\.0510366826 0 0 -0\.0510366826 0 64\)/);
  assert.match(mark, /#818cf8" stop-opacity="0\.34"/);
  assert.match(mark, /#bef264/);
  assert.doesNotMatch(mark, /M7\.5 35\.8C15\.2 20\.7/);
  assert.doesNotMatch(mark, /M13 34C21 26/);
  assert.doesNotMatch(mark, /M8\.5 33C9 37\.7/);
  assert.match(favicon, /<title[^>]*>RAELYN favicon<\/title>/);
  assert.match(favicon, /#21485c/);
  assert.match(favicon, /matrix\(0\.0510366826 0 0 -0\.0510366826 0 64\)/);
  assert.match(glyph, /<title[^>]*>RAELYN 渐变 R 字形<\/title>/);
  assert.match(glyph, /#bef264/);
  assert.match(glyph, /#5eead4/);
  assert.match(glyph, /#38bdf8/);
  assert.deepEqual(pngSize(markPng), [2048, 2048]);
  assert.deepEqual(pngSize(glyphPng), [2048, 2048]);
  assert.deepEqual(pngSize(faviconPng), [1024, 1024]);
  const [glyphWidth, glyphHeight] = alphaBounds(resolve(uiDir, "assets/brand/raelyn-glyph.png"));
  assert.ok(glyphWidth >= 1320 && glyphWidth <= 1330, `R 字形宽度异常：${glyphWidth}`);
  assert.ok(glyphHeight >= 1280 && glyphHeight <= 1290, `R 字形高度异常：${glyphHeight}`);
  assert.ok(glyphHeight / glyphWidth >= 0.967, "R 字形不应被纵向压扁");
  assert.match(brandBuilder, /-c:v",\s*"librsvg"/);
  assert.match(brandBuilder, /renderSvgPng\(svgRenderer, eventHorizonSvg, eventHorizonPng, 2048\)/);
  assert.match(brandBuilder, /renderSvgPng\(svgRenderer, faviconSvg, faviconPng, 1024\)/);
  assert.match(brandBuilder, /renderSvgPng\(svgRenderer, glyphSvg, glyphPng, 2048\)/);
});
