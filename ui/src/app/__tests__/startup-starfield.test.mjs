import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { parseStartupStarfield } from "../startup-starfield/adapter.js";

test("启动星域解析匿名坐标、时间、颜色和权重", () => {
  const buffer = new ArrayBuffer(40);
  const bytes = new Uint8Array(buffer);
  bytes.set(new TextEncoder().encode("RLYNSTR1"), 0);
  const view = new DataView(buffer);
  view.setUint16(8, 1, true);
  view.setUint16(10, 16, true);
  view.setUint32(12, 1, true);
  view.setInt32(16, 10_000, true);
  view.setInt32(20, 20_000, true);
  view.setInt16(24, 16_384, true);
  view.setInt16(26, -16_384, true);
  view.setInt16(28, 0, true);
  view.setInt32(30, 15_000, true);
  view.setUint8(34, 56);
  view.setUint8(35, 189);
  view.setUint8(36, 248);
  view.setUint8(37, 128);
  view.setUint16(38, 32_768, true);

  const parsed = parseStartupStarfield(buffer);
  assert.equal(parsed.count, 1);
  assert.ok(Math.abs(parsed.positions[0] - 0.5) < 0.001);
  assert.ok(Math.abs(parsed.positions[1] + 0.5) < 0.001);
  assert.equal(parsed.days[0], 0.5);
  assert.ok(Math.abs(parsed.colors[2] - 248 / 255) < 0.001);
  assert.ok(Math.abs(parsed.weights[0] - 128 / 255) < 0.001);
});

test("启动星域拒绝截断记录的数据", () => {
  const buffer = new ArrayBuffer(24);
  new Uint8Array(buffer).set(new TextEncoder().encode("RLYNSTR1"), 0);
  const view = new DataView(buffer);
  view.setUint16(8, 1, true);
  view.setUint16(10, 16, true);
  view.setUint32(12, 1, true);
  view.setInt32(16, 10_000, true);
  view.setInt32(20, 20_000, true);
  assert.throws(() => parseStartupStarfield(buffer), /长度不匹配/);
});

test("登录门面内置 6000 个真实匿名事件点", async () => {
  const bytes = await readFile(new URL("../../../assets/startup-event-field.bin", import.meta.url));
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  const parsed = parseStartupStarfield(buffer);
  assert.equal(parsed.count, 6000);
  assert.ok(parsed.maxDay > parsed.minDay);
});
