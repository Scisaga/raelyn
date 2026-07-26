import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const uiDir = resolve(fileURLToPath(new URL("../../..", import.meta.url)));

test("侧栏与 favicon 共用事件视界品牌标识", async () => {
  const [sidebar, head, mark, favicon] = await Promise.all([
    readFile(resolve(uiDir, "templates/app/components/sidebar.html"), "utf8"),
    readFile(resolve(uiDir, "templates/app/layout/head.html"), "utf8"),
    readFile(resolve(uiDir, "assets/brand/raelyn-event-horizon.svg"), "utf8"),
    readFile(resolve(uiDir, "assets/brand/raelyn-favicon.svg"), "utf8"),
  ]);

  assert.match(sidebar, /\/static\/brand\/raelyn-event-horizon\.svg/);
  assert.doesNotMatch(sidebar, /\/static\/brand\/logo-y\.png/);
  assert.match(head, /rel="icon" href="\/static\/brand\/raelyn-favicon\.svg/);
  assert.match(head, /rel="icon" href="\/static\/brand\/favicon-32\.png/);
  assert.match(mark, /<title[^>]*>RAELYN 事件视界<\/title>/);
  assert.match(mark, /M102 98.*M230 196/s);
  assert.match(mark, /rotate\(-14 32 33\)/);
  assert.match(mark, /rx="24"\s+ry="7\.2"/s);
  assert.match(mark, /M8 33C8 38\.6 18\.8 40\.2 32 40\.2C45\.2 40\.2 56 38\.6 56 33/);
  assert.match(mark, /translate\(5 5\) scale\(0\.10546875\)/);
  assert.match(mark, /#818cf8" stop-opacity="0\.34"/);
  assert.match(mark, /#bef264/);
  assert.doesNotMatch(mark, /M7\.5 35\.8C15\.2 20\.7/);
  assert.doesNotMatch(mark, /M13 34C21 26/);
  assert.doesNotMatch(mark, /M8\.5 33C9 37\.7/);
  assert.match(favicon, /<title[^>]*>RAELYN favicon<\/title>/);
  assert.match(favicon, /#21485c/);
  assert.match(favicon, /translate\(5 5\) scale\(0\.10546875\)/);
});
