import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  EVENT_MAP_NO_INDEX,
  EventMapController,
  eventMapActiveBounds,
  buildEventMapLayerState,
  buildEventMapSpacetimeGrid,
  countEventMapVisiblePoints,
  eventMapLayerVisibility,
  eventMapLabelText,
  eventMapLabelWidth,
  eventMapSemanticPalette,
  eventMapSemanticLevel,
  parseEventMapIndices,
  parseEventMapScene,
} from "../event-map.js";

const DAY_MS = 86_400_000;
const day = (value) => Math.floor(Date.parse(`${value}T00:00:00Z`) / DAY_MS);

test("粒子 shader 使用单层清晰点并在 GPU 按时间窗口裁剪", async () => {
  const source = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  assert.match(source, /uniform float uSize;/);
  assert.match(source, /uniform float uPixelRatio;/);
  assert.match(source, /attribute float aStartDay;/);
  assert.match(source, /attribute float aEndDay;/);
  assert.match(source, /uniform float uWindowStartDay;/);
  assert.match(source, /uniform float uWindowEndDay;/);
  assert.match(source, /smoothstep\(0\.72, 1\.0, radius\)/);
  assert.doesNotMatch(source, /UnrealBloomPass|cloudPoints|haloPoints|composer/);
});

test("背景使用由星域密度弯曲的低透明度静态坐标网格", async () => {
  const flat = buildEventMapSpacetimeGrid({ extent: 8, lineCount: 7, segmentCount: 8 });
  const warped = buildEventMapSpacetimeGrid({
    extent: 8,
    lineCount: 7,
    segmentCount: 8,
    wells: [{ x: 0, y: 0, radius: 2, strength: 1.4 }],
  });
  const source = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  assert.equal(flat.positions.length, 7 * 2 * 8 * 2 * 3);
  assert.equal(flat.fades.length, flat.positions.length / 3);
  assert.equal(flat.curvatures.length, flat.positions.length / 3);
  assert.ok(Math.abs(Math.min(...flat.positions.filter((_, index) => index % 3 === 2))) < Number.EPSILON);
  assert.ok(Math.min(...warped.positions.filter((_, index) => index % 3 === 2)) < -1);
  assert.ok(Math.max(...warped.curvatures) > 0.45);
  assert.match(source, /this\.spacetimeGrid = new THREE\.LineSegments/);
  assert.match(source, /uOpacity: \{ value: 0\.24 \}/);
  assert.match(source, /this\.spacetimeGrid\.renderOrder = -20/);
  assert.match(source, /this\.spacetimeGrid\?\.geometry\?\.dispose\(\)/);
});

test("单击星点保持镜头，仅更新选择状态并以 CSS 像素扩大 GPU 拾取半径", async () => {
  const source = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  assert.match(source, /gl_PointSize = 12\.0 \* uPixelRatio/);
  assert.match(source, /float eventActive = step\(aStartDay, uWindowEndDay\)/);
  assert.doesNotMatch(source, /float active = step\(aStartDay, uWindowEndDay\)/);
  assert.match(source, /选择与镜头定位是两件事/);
  assert.match(source, /this\.onSelect\?\.\(index, this\.sceneData\.canonicalIds\[index\]\)/);
  assert.doesNotMatch(source, /this\.focusPoint\(index\);\s*\n\s*this\.onSelect/);
  assert.doesNotMatch(source, /this\.focusPoint\(label\.pointIndex\)/);
});

test("直接点击主题标签保留镜头，已选主题跨语义层级保留标签", async () => {
  const controllerSource = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  const modelSource = await readFile(new URL("../event-map-model.js", import.meta.url), "utf8");
  assert.match(controllerSource, /selectedTopicCandidate\(\)/);
  assert.match(controllerSource, /\.\.\.\(selectedTopic \? \[selectedTopic\] : \[\]\)/);
  assert.match(controllerSource, /candidates\.filter\(\(candidate\) => candidate\.index !== selectedTopic\?\.index\)/);
  assert.match(modelSource, /playlistEventMapSelectTopic\(item, \{ focus = false \} = \{\}\)/);
  assert.match(modelSource, /playlistEventMapSelectTopic\(item, \{ focus: true \}\)/);
  assert.match(modelSource, /if \(focus\) controller\.focusTopic\(index\)/);
});

test("选中主题保留成员语义颜色、加细金边并弱化其余事件", async () => {
  const controllerSource = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  const modelSource = await readFile(new URL("../event-map-model.js", import.meta.url), "utf8");
  const template = await readFile(new URL("../../../templates/app/views/field-v2.html", import.meta.url), "utf8");
  assert.match(controllerSource, /attribute float aTopicFocus;/);
  assert.match(controllerSource, /float focusRing = vTopicFocus/);
  assert.match(controllerSource, /attribute float aPickable;/);
  assert.match(controllerSource, /updateTopicFocusStyles\(\)/);
  assert.match(controllerSource, /focused \? 1 : 0\.48/);
  assert.match(modelSource, /主题选中＝加亮与细金边/);
  assert.match(modelSource, /playlistEventMapClearTopicFocus\(\); return;/);
  assert.match(modelSource, /\{ \.\.\.\(topics\.find[\s\S]*?\|\| \{\}\), \.\.\.item, topic_index: index \}/);
  assert.match(template, /选择不改变镜头/);
  assert.match(template, /当前窗口已高亮/);
});

test("选中真实事件在原位保留白芯金环与带引线的稳定标签", async () => {
  const source = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  assert.match(source, /attribute float aSelected;/);
  assert.match(source, /float selectedRing = vSelected/);
  assert.match(source, /float selectedCore = vSelected/);
  assert.match(source, /this\.selectedMask\.fill\(0\)/);
  assert.match(source, /真实事件 · 加载中/);
  assert.match(source, /bg-amber-200\/80/);
  assert.match(source, /selectedEvent \? \[selectedEvent\] : \[\]/);
});

test("地图标签默认透明无边框，交互时恢复承托且不应用景深透明度", async () => {
  const source = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  const styles = await readFile(new URL("../../../input.css", import.meta.url), "utf8");
  assert.match(source, /text-\[10px\] font-medium/);
  assert.match(source, /event-map-label rounded px-1\.5 py-0\.5/);
  assert.match(source, /element\.style\.setProperty\("--event-map-label-color", semanticColor\)/);
  assert.match(styles, /\.event-map-label \{[\s\S]*?background-color: transparent;[\s\S]*?border: 0;[\s\S]*?text-shadow:/);
  assert.match(styles, /\.event-map-label:is\(:hover, :focus-visible\) \{[\s\S]*?background-color: rgb\(2 6 23 \/ 86%\);[\s\S]*?box-shadow:/);
  assert.doesNotMatch(source, /element\.style\.borderColor/);
  assert.doesNotMatch(source, /element\.style\.opacity/);
  assert.doesNotMatch(source, /element\.style\.borderLeft/);
});

test("地图标签宽度按实际文字测量", () => {
  assert.equal(eventMapLabelText({ label: "中国 · 农业", count: 1503 }), "中国 · 农业 · 1503");
  assert.equal(eventMapLabelWidth("美联储", () => 21), 48);
  assert.equal(eventMapLabelWidth("很长的主题标签", () => 320), 338);
  assert.equal(eventMapLabelWidth("很长的主题标签", () => 480, 360), 360);
  assert.equal(eventMapLabelWidth("短标签", () => 86), 104);
});

test("预览窗口同步重算标签活动状态，取消后恢复已提交窗口", () => {
  const activeWindows = [];
  const preview = {
    destroyed: false,
    windowDays: () => ({ start: 10, end: 20 }),
    previewWindowStartDay: null,
    previewWindowEndDay: null,
    transitionStartedAt: 1,
    applyWindowUniforms() {},
    applyActiveWindow: (start, end) => activeWindows.push([start, end]),
    invalidate() {},
  };
  EventMapController.prototype.previewWindow.call(preview, { windowStart: "2026-01-01", windowEnd: "2026-01-31" });
  assert.deepEqual(activeWindows, [["2026-01-01", "2026-01-31"]]);

  const restore = {
    previewWindowStartDay: 10,
    previewWindowEndDay: 20,
    transitionStartedAt: 1,
    committedWindowStartDay: day("2025-12-01"),
    committedWindowEndDay: day("2025-12-31"),
    hasCommittedWindow: true,
    applyWindowUniforms() {},
    applyActiveWindow: (start, end) => activeWindows.push([start, end]),
    invalidate() {},
  };
  EventMapController.prototype.cancelWindowPreview.call(restore);
  assert.deepEqual(activeWindows.at(-1), ["2025-12-01", "2025-12-31"]);
});

function sceneBuffer(records) {
  const buffer = new ArrayBuffer(records.length * 56);
  const view = new DataView(buffer);
  records.forEach((record, index) => {
    const offset = index * 56;
    view.setUint32(offset, index, true);
    record.uuidBytes.forEach((value, position) => view.setUint8(offset + 4 + position, value));
    view.setFloat32(offset + 20, record.x, true); view.setFloat32(offset + 24, record.y, true); view.setFloat32(offset + 28, record.z, true);
    view.setInt32(offset + 32, record.startDay, true); view.setInt32(offset + 36, record.endDay, true);
    view.setUint8(offset + 40, record.eventType); view.setUint8(offset + 41, record.timePrecision); view.setUint8(offset + 42, record.flags || 0);
    view.setUint32(offset + 44, record.memberCount || 1, true);
    view.setUint32(offset + 48, record.macroTopicIndex ?? EVENT_MAP_NO_INDEX, true);
    view.setUint32(offset + 52, record.localTopicIndex ?? EVENT_MAP_NO_INDEX, true);
  });
  return buffer;
}

function fixture() {
  return parseEventMapScene(sceneBuffer([{ uuidBytes: Uint8Array.from({ length: 16 }, (_, index) => index), x: 1.25, y: -3.5, z: 2.75, startDay: day("2026-02-01"), endDay: day("2026-02-28"), eventType: 4, timePrecision: 2, flags: 3, memberCount: 17, macroTopicIndex: 2, localTopicIndex: 8 }]), 1);
}

test("56 字节三维场景协议包含固定坐标与两级主题", () => {
  const scene = fixture();
  assert.equal(scene.count, 1);
  assert.equal(scene.canonicalIds[0], "00010203-0405-0607-0809-0a0b0c0d0e0f");
  assert.equal(scene.z[0], 2.75);
  assert.equal(scene.memberCount[0], 17);
  assert.equal(scene.macroTopicIndex[0], 2);
  assert.equal(scene.localTopicIndex[0], 8);
  assert.equal("lodFarIndex" in scene, false);
  assert.throws(() => parseEventMapScene(new ArrayBuffer(55)), /不是 56 的整数倍/);
});

test("观察窗口严格排除窗口外事件，且不移动全局坐标", () => {
  const scene = {
    count: 3,
    x: new Float32Array([1, 2, 3]), y: new Float32Array([4, 5, 6]), z: new Float32Array([7, 8, 9]),
    startDay: new Int32Array([day("2025-01-01"), day("2026-02-01"), day("2026-03-01")]),
    endDay: new Int32Array([day("2025-12-31"), day("2026-02-28"), day("2026-03-01")]),
    eventType: new Uint8Array([1, 4, 7]), memberCount: new Uint32Array([1, 2, 3]), macroTopicIndex: new Uint32Array([0, 1, 1]), localTopicIndex: new Uint32Array([2, 3, 3]),
  };
  const x = Array.from(scene.x); const y = Array.from(scene.y); const z = Array.from(scene.z);
  const state = buildEventMapLayerState(scene, { windowStart: "2026-02-01", windowEnd: "2026-02-28", typeFilter: "4", entityIndices: new Set([1]), selectedIndex: 1 });
  assert.deepEqual(Array.from(state.activeMask), [0, 1, 0]);
  assert.deepEqual(Array.from(state.entityMask), [0, 1, 0]);
  assert.equal(state.categories[1], 3);
  assert.equal(countEventMapVisiblePoints(scene, "2026-02-01", "2026-02-28"), 1);
  assert.deepEqual(Array.from(scene.x), x); assert.deepEqual(Array.from(scene.y), y); assert.deepEqual(Array.from(scene.z), z);
});

test("当前窗口取景只使用活动事件边界，不被全历史坐标范围缩小", () => {
  const scene = {
    count: 4,
    x: new Float32Array([-100, -2, 3, 120]),
    y: new Float32Array([-80, -1, 4, 90]),
    z: new Float32Array([-60, -3, 2, 70]),
  };
  assert.deepEqual(eventMapActiveBounds(scene, new Uint8Array([0, 1, 1, 0])), {
    minX: -2,
    maxX: 3,
    minY: -1,
    maxY: 4,
    minZ: -3,
    maxZ: 2,
    count: 2,
  });
  assert.equal(eventMapActiveBounds(scene, new Uint8Array(4)), null);
});

test("缩放决定星域、主题与逐事件三层，并带迟滞", () => {
  assert.equal(eventMapSemanticLevel(1.6, "overview"), "overview");
  assert.equal(eventMapSemanticLevel(1.8, "overview"), "topic");
  assert.equal(eventMapSemanticLevel(4.9, "topic"), "event");
  assert.equal(eventMapSemanticLevel(4.3, "event"), "event");
  assert.equal(eventMapSemanticLevel(4.1, "event"), "topic");
  assert.equal(eventMapSemanticLevel(1.4, "topic"), "overview");
  assert.deepEqual(eventMapLayerVisibility("overview", true), { macroLabels: true, topicLabels: false, canonical: false, story: false });
  assert.deepEqual(eventMapLayerVisibility("topic", true), { macroLabels: false, topicLabels: true, canonical: false, story: false });
  assert.deepEqual(eventMapLayerVisibility("event", true), { macroLabels: false, topicLabels: false, canonical: true, story: true });
});

test("实体索引协议仍按 uint32 小端解析", () => {
  const buffer = new ArrayBuffer(8); const view = new DataView(buffer); view.setUint32(0, 2, true); view.setUint32(4, 65537, true);
  assert.deepEqual(Array.from(parseEventMapIndices(buffer)), [2, 65537]);
});

test("事件类型映射为稳定语义色，未知类型使用灰蓝色", () => {
  const palette = eventMapSemanticPalette({
    semantic_families: [
      { code: "macro_policy", label: "宏观与政策", color: "#a78bfa" },
      { code: "technology", label: "科技与网络安全", color: "#e879f9" },
    ],
    type_categories: [
      { code: 4, semantic_family: "macro_policy", semantic_family_label: "宏观与政策", semantic_color: "#a78bfa" },
      { code: 9, semantic_family: "technology", semantic_family_label: "科技与网络安全", semantic_color: "#e879f9" },
    ],
  });
  assert.equal(palette.byTypeCode[4].code, "macro_policy");
  assert.deepEqual(palette.byTypeCode[4].rgb.map((value) => Number(value.toFixed(4))), [0.6549, 0.5451, 0.9804]);
  assert.equal(palette.byTypeCode[9].color, "#e879f9");
  assert.equal(palette.byTypeCode[88] || palette.fallback, palette.fallback);
});

test("三维控制器公开相机、显式聚焦、选择与资源销毁入口", () => {
  for (const method of ["resetCamera", "focusPoint", "focusTopic", "fitBounds", "fitActiveWindow", "setSelection", "setSelectionDetail", "previewWindow", "cancelWindowPreview", "destroy"]) {
    assert.equal(typeof EventMapController.prototype[method], "function");
  }
  assert.equal(typeof EventMapController.prototype.setCameraMode, "undefined");
});

test("不可变场景使用浏览器缓存，首帧不等待实体排行，并只保留三维透视相机", async () => {
  const controller = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  const model = await readFile(new URL("../event-map-model.js", import.meta.url), "utf8");
  assert.match(model, /cache: "force-cache"/);
  assert.match(model, /const anticipatedScene = shouldPrefetchScene/);
  assert.ok(model.indexOf("const anticipatedScene = shouldPrefetchScene") < model.indexOf("const manifest = await this.playlistEventMapLoadManifest"));
  assert.doesNotMatch(model, /await this\.playlistEventMapLoadEntities\(\{ silent: true \}\)/);
  assert.match(controller, /scene\.count > 100_000 \? 1\.25 : 1\.5/);
  assert.match(controller, /ACTIVE_WINDOW_PERSPECTIVE_DISTANCE_SCALE = 1\.9/);
  assert.doesNotMatch(controller, /OrthographicCamera|orthographicCamera|cameraMode|setCameraMode/);
});

test("事件语义星域状态与控制合并在同一工具栏", async () => {
  const template = await readFile(new URL("../../../templates/app/views/field-v2.html", import.meta.url), "utf8");
  const section = template.slice(template.indexOf("<!-- V2 事件语义星域：独立于来源播放与旧设置。 -->"));
  assert.match(section, /<header class="shrink-0 border-b border-slate-800 bg-slate-950\/45">[\s\S]*?截至[\s\S]*?<\/header>/);
  assert.match(section, /事件语义星域/);
  assert.doesNotMatch(section, /x-text="activeView==='field' \? currentDomainLabel\(\)/);
  assert.match(section, /playlistEventMapStatusBadgeHint/);
  assert.match(section, /三维语义星域 · 时间只改变当前窗口星云/);
  assert.match(section, /三维语义空间 · 左键旋转/);
  assert.match(section, /代表事件/);
  assert.match(section, /playlistEventMapFocusSelectedCanonical/);
  assert.match(template, /x-show="activeView==='field'"/);
  assert.doesNotMatch(template, /<path d="M3 3v18h18"><\/path>/);
  assert.doesNotMatch(section, /<div class="text-sm font-semibold text-slate-100">三维事件星图<\/div>/);
  assert.match(section, /playlistEventMapSelectedId \? 'lg:right-\[380px\]'/);
  assert.match(section, /fieldObservationRailOpen \? 'lg:right-80'/);
});
