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
  eventMapLabelFocusOpacity,
  eventMapLabelText,
  eventMapLabelWidth,
  eventMapMediaConnectorPath,
  eventMapMediaItemKey,
  eventMapSemanticPalette,
  eventMapSemanticLevel,
  layoutFrozenEventMapMediaCards,
  layoutEventMapMediaCards,
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
  assert.doesNotMatch(source, /\bfloat active\b/);
  assert.doesNotMatch(source, /UnrealBloomPass|cloudPoints|haloPoints|composer/);
});

test("时间远近衰减与今日本周焦点在同一粒子 shader 中合成", async () => {
  const source = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  const styles = await readFile(new URL("../../../input.css", import.meta.url), "utf8");
  assert.match(source, /attribute float aTimeFocus;/);
  assert.match(source, /smoothstep\(30\.0, 365\.0, ageDays\)/);
  assert.match(source, /mix\(1\.0, 0\.22/);
  assert.match(source, /composedOpacity = max\(composedOpacity, aTopicFocus \* 0\.65\)/);
  assert.match(source, /allowedTimeFocus = aTimeFocus \* mix\(1\.0, mix\(0\.36, 1\.0, aTopicFocus\), uHasTopicFocus\)/);
  assert.match(source, /composedOpacity = max\(composedOpacity, allowedTimeFocus \* 0\.92\)/);
  assert.match(source, /this\.collapseMediaCard\(\);[\s\S]*?this\.invalidate\(\);/);
  assert.match(source, /event-map-video-card--expanded/);
  assert.match(styles, /\.event-map-video-card--expanded \{/);
});

test("空间视频卡底部直接显示媒体图标、媒体名和视频标题", async () => {
  const source = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  const styles = await readFile(new URL("../../../input.css", import.meta.url), "utf8");
  assert.match(source, /event-map-video-card__media-icon/);
  assert.match(source, /primary_video\?\.media_avatar_url/);
  assert.match(source, /sourceText\.textContent = `\$\{mediaName\} · \$\{String/);
  assert.doesNotMatch(source, /关联视频\$\{videoDate/);
  assert.doesNotMatch(source, /sourceText\.textContent = `关联视频/);
  assert.match(styles, /\.event-map-video-card__media-icon \{/);
});

test("桌面鼠标停留静音预览，点击固定并解除静音，事件连线使用分层浅黄圆点样式", async () => {
  const source = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  const styles = await readFile(new URL("../../../input.css", import.meta.url), "utf8");
  assert.match(source, /EVENT_MAP_MEDIA_HOVER_OPEN_MS = 180/);
  assert.match(source, /EVENT_MAP_MEDIA_HOVER_CLOSE_MS = 220/);
  assert.match(source, /matchMedia\?\.\("\(hover: hover\) and \(pointer: fine\)"\)/);
  assert.match(source, /addEventListener\("pointerenter", \(\) => \{[\s\S]*?this\.setMediaConnectorFocus\(key\);[\s\S]*?this\.scheduleMediaCardHover/);
  assert.match(source, /addEventListener\("pointerleave", \(\) => \{[\s\S]*?this\.setMediaConnectorFocus\(""\);[\s\S]*?this\.scheduleMediaCardHoverCollapse/);
  assert.match(source, /toggleMediaCard\(item, card, preview, \{ trigger: "hover" \}\)/);
  assert.match(source, /video\.defaultMuted = hoverPreview/);
  assert.match(source, /video\.muted = hoverPreview/);
  assert.match(source, /const hoverPreview = this\.expandedMediaTrigger === "hover"/);
  assert.doesNotMatch(source, /const hoverPreview = playbackTrigger === "hover"/);
  assert.match(source, /this\.expandedMediaTrigger = "click"/);
  assert.match(styles, /\.event-map-video-connector path \{[\s\S]*?stroke: rgb\(253 230 138 \/ 58%\);[\s\S]*?stroke-width: 1\.25;[\s\S]*?stroke-dasharray: 1 5;[\s\S]*?stroke-linecap: round;/);
  assert.match(styles, /\.event-map-video-connector--focused path \{[\s\S]*?stroke-width: 1\.5;[\s\S]*?stroke-dasharray: 1\.5 4;/);
  assert.match(styles, /\.event-map-video-connector--expanded path \{[\s\S]*?stroke: rgb\(250 204 21 \/ 90%\);[\s\S]*?stroke-dasharray: 2 3\.5;/);
  assert.match(styles, /\.event-map-media-connectors--focused \.event-map-video-connector:not\(\.event-map-video-connector--focused\) \{[\s\S]*?opacity: 0\.22;/);
  assert.match(source, /anchor\.setAttribute\("r", "3"\)/);
  assert.match(source, /const activeKey = this\.focusedMediaItemKey \|\| this\.expandedMediaItemKey/);

  let played = false;
  const video = {
    defaultMuted: true,
    muted: true,
    removeAttribute(name) { assert.equal(name, "muted"); },
    async play() { played = true; },
  };
  const context = {
    expandedMediaItemKey: "video-1",
    expandedMediaTrigger: "hover",
    clearMediaCardHoverTimers() {},
  };
  const card = { dataset: {}, querySelector: () => video };
  await EventMapController.prototype.toggleMediaCard.call(
    context,
    { primary_video: { video_id: "video-1" } },
    card,
    {},
    { trigger: "click" },
  );
  assert.equal(context.expandedMediaTrigger, "click");
  assert.equal(card.dataset.playbackTrigger, "click");
  assert.equal(video.defaultMuted, false);
  assert.equal(video.muted, false);
  assert.equal(played, true);
});

test("视频卡焦点强化全部关联线，移出后由播放固定卡继续保持焦点", () => {
  const classList = () => {
    const values = new Set();
    return {
      contains: (name) => values.has(name),
      toggle(name, force) {
        if (force) values.add(name);
        else values.delete(name);
      },
    };
  };
  const firstConnector = { classList: classList() };
  const secondConnector = { classList: classList() };
  const context = {
    focusedMediaItemKey: "",
    expandedMediaItemKey: "video-2",
    mediaConnectors: { classList: classList() },
    mediaCardEntries: [
      { key: "video-1", connectorGroup: firstConnector },
      { key: "video-2", connectorGroup: secondConnector },
    ],
    syncMediaConnectorFocus: EventMapController.prototype.syncMediaConnectorFocus,
  };

  EventMapController.prototype.setMediaConnectorFocus.call(context, "video-1");
  assert.equal(firstConnector.classList.contains("event-map-video-connector--focused"), true);
  assert.equal(secondConnector.classList.contains("event-map-video-connector--focused"), false);
  assert.equal(context.mediaConnectors.classList.contains("event-map-media-connectors--focused"), true);

  EventMapController.prototype.setMediaConnectorFocus.call(context, "");
  assert.equal(firstConnector.classList.contains("event-map-video-connector--focused"), false);
  assert.equal(secondConnector.classList.contains("event-map-video-connector--focused"), true);
});

test("视频卡展开时冻结既有排布并向画布外侧扩展，原鼠标命中区域保持不动", () => {
  const leftEntry = { key: "left-video" };
  const rightEntry = { key: "right-video" };
  const idleEntry = { key: "idle-video" };
  const frozen = new Map([
    ["left-video", { visible: true, left: 300, top: 180, width: 216, height: 84, side: "left", mini: false }],
    ["right-video", { visible: true, left: 900, top: 260, width: 216, height: 84, side: "right", mini: false }],
    ["idle-video", { visible: true, left: 80, top: 420, width: 84, height: 48, side: "left", mini: true }],
  ]);
  const layouts = layoutFrozenEventMapMediaCards([
    { entry: leftEntry, x: 600, y: 220, width: 400, height: 128, expanded: true },
    { entry: rightEntry, x: 800, y: 300, width: 400, height: 128, expanded: true },
    { entry: idleEntry, x: 500, y: 440, width: 216, height: 84, expanded: false },
  ], frozen, { width: 1400, height: 760 });

  const left = layouts.find((item) => item.entry === leftEntry);
  const right = layouts.find((item) => item.entry === rightEntry);
  const idle = layouts.find((item) => item.entry === idleEntry);
  assert.equal(left.left, 116);
  assert.equal(left.left + left.width, 516);
  assert.equal(right.left, 900);
  assert.equal(idle.left, 80);
  assert.equal(idle.top, 420);
  assert.equal(idle.mini, true);
});

test("视频作为卡片唯一身份，最多十张卡进入左右内缩侧带并以有限短线连接真实投影点", () => {
  assert.notEqual(
    eventMapMediaItemKey({ canonical_id: "event-1", primary_video: { video_id: "video-1" } }),
    eventMapMediaItemKey({ canonical_id: "event-1", primary_video: { video_id: "video-2" } }),
  );
  assert.equal(
    eventMapMediaItemKey({ canonical_id: "event-1", primary_video: { video_id: "video-1" } }),
    eventMapMediaItemKey({ canonical_id: "event-2", primary_video: { video_id: "video-1" } }),
  );
  const points = Array.from({ length: 10 }, (_, index) => ({
    id: `event-${index}`,
    x: 500 + (index % 5) * 80,
    y: 250 + Math.floor(index / 5) * 90,
    width: 200,
    height: 68,
  }));
  const layouts = layoutEventMapMediaCards(points, { width: 1400, height: 760 });
  assert.equal(layouts.length, 10);
  assert.deepEqual(layoutEventMapMediaCards(points, { width: 1400, height: 760 }), layouts);
  for (const layout of layouts) {
    assert.ok(layout.left >= 56);
    assert.ok(layout.top >= 58);
    assert.ok(layout.left + layout.width <= 1344);
    assert.ok(layout.top + layout.height <= 696);
    assert.ok(layout.left + layout.width <= 504 || layout.left >= 896);
    assert.ok(layout.connectorLength <= 300);
    assert.match(layout.connectorPath, new RegExp(`^M ${layout.x.toFixed(2)} ${layout.y.toFixed(2)} L `));
  }
  assert.ok(new Set(layouts.map((item) => Math.round(item.left))).size > 5);
  for (let leftIndex = 0; leftIndex < layouts.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < layouts.length; rightIndex += 1) {
      const left = layouts[leftIndex];
      const right = layouts[rightIndex];
      const separated = left.left + left.width + 10 <= right.left
        || right.left + right.width + 10 <= left.left
        || left.top + left.height + 10 <= right.top
        || right.top + right.height + 10 <= left.top;
      assert.equal(separated, true);
    }
  }

  const moved = points.map((point, index) => ({
    ...point,
    x: point.x + 6,
    y: point.y + 4,
    preferredPlacement: layouts[index].placement,
  }));
  const following = layoutEventMapMediaCards(moved, { width: 1400, height: 760, preservePlacement: true });
  assert.equal(following.length, 10);
  for (const layout of following) {
    assert.equal(layout.placement.angle, layouts[layout.rank].placement.angle);
    assert.equal(layout.placement.radialGap, layouts[layout.rank].placement.radialGap);
  }

  assert.equal(
    eventMapMediaConnectorPath(600, 300, { left: 700, top: 270, width: 200, height: 68 }),
    "M 600.00 300.00 L 700.00 300.00",
  );
});

test("一张视频卡为全部关联事件创建独立连线，并以事件投影中心参与布局", async () => {
  const source = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  assert.match(source, /const connectors = pointIndices\.map\(\(pointIndex\) => \{/);
  assert.match(source, /const projectedAnchors = \[\];/);
  assert.match(source, /projectedAnchors\.reduce\(\(sum, anchor\) => sum \+ anchor\.x, 0\) \/ projectedAnchors\.length/);
  assert.match(source, /eventMapMediaConnectorPath\(projectedAnchor\.x, projectedAnchor\.y, layout\)/);
  assert.match(source, /linkedEventCount > 1[\s\S]*?` · \$\{linkedEventCount\}个事件`/);
});

test("中央密集事件把视频卡推入内缩侧带，不再覆盖主体星云", () => {
  const points = [
    { id: "one", x: 940, y: 640 },
    { id: "two", x: 1030, y: 720 },
    { id: "three", x: 860, y: 550 },
    { id: "four", x: 1110, y: 820 },
    { id: "five", x: 790, y: 860 },
  ].map((point) => ({ ...point, width: 216, height: 84 }));
  const layouts = layoutEventMapMediaCards(points, { width: 2048, height: 1360 });
  assert.equal(layouts.length, points.length);
  for (const layout of layouts) {
    assert.ok(layout.left >= 56);
    assert.ok(layout.left + layout.width <= 737.28 || layout.left >= 1310.72);
    assert.ok(layout.left + layout.width <= 1992);
    assert.ok(layout.connectorLength <= 300);
  }
});

test("空间视频注记避开顶栏、图例和四周边界，视野紧张时允许低优先级缩略或隐藏", () => {
  const boundaryPoints = [
    { x: 2, y: 2 },
    { x: 1398, y: 2 },
    { x: 2, y: 758 },
    { x: 1398, y: 758 },
  ].map((point, index) => ({ ...point, id: `boundary-${index}`, width: 200, height: 68 }));
  const boundaryLayouts = layoutEventMapMediaCards(boundaryPoints, { width: 1400, height: 760 });
  assert.equal(boundaryLayouts.length, 4);
  for (const layout of boundaryLayouts) {
    assert.ok(layout.left >= 56);
    assert.ok(layout.top >= 58);
    assert.ok(layout.left + layout.width <= 1344);
    assert.ok(layout.top + layout.height <= 696);
    assert.ok(layout.left + layout.width <= 504 || layout.left >= 896);
    assert.ok(layout.connectorLength <= 300);
  }

  const crowded = Array.from({ length: 10 }, (_, index) => ({
    id: `crowded-${index}`,
    x: 350,
    y: 250,
    width: 200,
    height: 68,
  }));
  const crowdedLayouts = layoutEventMapMediaCards(crowded, { width: 700, height: 500 });
  assert.ok(crowdedLayouts.length > 0);
  assert.ok(crowdedLayouts.length <= 10);
  assert.ok(crowdedLayouts.some((layout) => layout.mini) || crowdedLayouts.length < 10);
});

test("背景由持续可见的远场坐标穹幕和密度语义引力膜组成", async () => {
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
  assert.match(source, /new THREE\.SphereGeometry\(HORIZON_GRID_RADIUS, 64, 32\)/);
  assert.match(source, /side: THREE\.BackSide/);
  assert.match(source, /this\.horizonGrid = createEventMapHorizonGrid\(THREE\)/);
  assert.match(source, /this\.horizonGrid\.position\.copy\(this\.camera\.position\)/);
  assert.doesNotMatch(source, /this\.horizonGrid\.quaternion/);
  assert.match(source, /this\.semanticMembrane = new THREE\.LineSegments/);
  assert.match(source, /attribute float aPreviousDepth/);
  assert.match(source, /attribute float aCurrentDepth/);
  assert.match(source, /uniform float uFieldMix/);
  assert.match(source, /this\.topicCounts\.get\(index\)/);
  assert.match(source, /this\.updateTopicActivity\(\);\s*this\.updateSemanticMembraneDensity\(\);/);
  assert.match(source, /uOpacity: \{ value: 0\.24 \}/);
  assert.match(source, /this\.semanticMembrane\.renderOrder = -20/);
  assert.match(source, /this\.horizonGrid\?\.geometry\?\.dispose\(\)/);
  assert.match(source, /this\.semanticMembrane\?\.geometry\?\.dispose\(\)/);
});

test("远场坐标穹幕只跟随镜头位置，并在近景降低可见度", () => {
  const cameraPosition = { x: 13, y: 10, z: 15 };
  let copiedPosition = null;
  const context = {
    horizonGrid: {
      position: { copy: (value) => { copiedPosition = value; } },
      material: { uniforms: { uOpacity: { value: 0 } } },
    },
    camera: { position: cameraPosition },
    controls: {},
    semanticZoom: () => 5,
  };

  EventMapController.prototype.updateHorizonGrid.call(context);

  assert.equal(copiedPosition, cameraPosition);
  assert.equal(Number(context.horizonGrid.material.uniforms.uOpacity.value.toFixed(3)), 0.105);
});

test("语义引力膜在窗口密度变化后平滑过渡", () => {
  const context = {
    semanticMembraneTransitionStartedAt: 100,
    semanticMembrane: { material: { uniforms: { uFieldMix: { value: 0 } } } },
  };

  assert.equal(EventMapController.prototype.updateSemanticMembraneTransition.call(context, 280), true);
  assert.equal(Number(context.semanticMembrane.material.uniforms.uFieldMix.value.toFixed(3)), 0.5);
  assert.equal(EventMapController.prototype.updateSemanticMembraneTransition.call(context, 460), false);
  assert.equal(context.semanticMembrane.material.uniforms.uFieldMix.value, 1);
  assert.equal(context.semanticMembraneTransitionStartedAt, 0);
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
  assert.match(controllerSource, /focused \? 1 : 0\.22/);
  assert.match(modelSource, /主题选中＝加亮与细金边/);
  assert.match(modelSource, /playlistEventMapClearTopicFocus\(\); return;/);
  assert.match(modelSource, /\{ \.\.\.\(topics\.find[\s\S]*?\|\| \{\}\), \.\.\.item, topic_index: index \}/);
  assert.match(template, /选择不改变镜头/);
  assert.match(template, /当前窗口已高亮/);
});

test("选中节点后标签按成员、直接关系、同级上下文和无关对象分层弱化", () => {
  const focus = {
    selectedIndex: null,
    topicFocusIndex: 4,
    topicByIndex: new Map([
      [1, { topic_index: 1, parent_topic_index: null }],
      [4, { topic_index: 4, parent_topic_index: 1 }],
      [5, { topic_index: 5, parent_topic_index: 1 }],
      [6, { topic_index: 6, parent_topic_index: 2 }],
    ]),
    isTopicMember(pointIndex, topicIndex) {
      return pointIndex === 10 && topicIndex === 4;
    },
  };

  assert.equal(EventMapController.prototype.labelFocusRelation.call(focus, { selected: true }), "selected");
  assert.equal(EventMapController.prototype.labelFocusRelation.call(focus, { kind: "event", pointIndex: 10 }), "member");
  assert.equal(EventMapController.prototype.labelFocusRelation.call(focus, { kind: "topic", index: 1, topic: focus.topicByIndex.get(1) }), "direct");
  assert.equal(EventMapController.prototype.labelFocusRelation.call(focus, { kind: "topic", index: 5, topic: focus.topicByIndex.get(5) }), "context");
  assert.equal(EventMapController.prototype.labelFocusRelation.call(focus, { kind: "topic", index: 6, topic: focus.topicByIndex.get(6) }), "unrelated");
  assert.deepEqual(
    ["selected", "member", "direct", "context", "unrelated", "normal"].map(eventMapLabelFocusOpacity),
    [1, 0.82, 0.62, 0.54, 0.34, 1]
  );
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
  assert.match(source, /element\.style\.setProperty\("--event-map-label-opacity"/);
  assert.match(styles, /\.event-map-label \{[\s\S]*?background-color: transparent;[\s\S]*?border: 0;[\s\S]*?text-shadow:/);
  assert.match(styles, /\.event-map-label \{[\s\S]*?opacity: var\(--event-map-label-opacity\)/);
  assert.match(styles, /\.event-map-label:is\(:hover, :focus-visible\) \{[\s\S]*?opacity: 1;[\s\S]*?background-color: rgb\(2 6 23 \/ 86%\);[\s\S]*?box-shadow:/);
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

test("完整 manifest 到达后原位补齐主题、语义色和标签且保留首屏网格", () => {
  const calls = [];
  const context = {
    destroyed: false,
    manifest: {},
    topics: [],
    membraneTopics: [{ topic_index: 0, level: 0 }],
    topicByIndex: new Map(),
    sceneData: { count: 2, eventType: new Uint8Array([4, 88]) },
    semanticColors: new Float32Array(6),
    semanticMembraneTransitionStartedAt: 12,
    semanticMembrane: {
      geometry: { dispose: () => calls.push("dispose-geometry") },
      material: { dispose: () => calls.push("dispose-material") },
    },
    scene: { remove: () => calls.push("remove-membrane") },
    rawBounds: {},
    setupSemanticMembrane: () => calls.push("setup-membrane"),
    updateTopicActivity: () => calls.push("topics"),
    updateSemanticMembraneDensity: ({ animate }) => calls.push(`density:${animate}`),
    updateColors: () => calls.push("colors"),
    updateLabels: () => calls.push("labels"),
    revealLabelsIfReady: () => calls.push("reveal-labels"),
    invalidate: () => calls.push("invalidate"),
  };
  const manifest = {
    topics: [{ topic_index: 3, label: "政策主题" }],
    semantic_families: [{ code: "macro_policy", label: "宏观与政策", color: "#a78bfa" }],
    type_categories: [{ code: 4, semantic_family: "macro_policy", semantic_color: "#a78bfa" }],
  };

  EventMapController.prototype.updateManifest.call(context, manifest);

  assert.equal(context.manifest, manifest);
  assert.equal(context.topicByIndex.get(3).label, "政策主题");
  assert.equal(context.labelMetadataReady, true);
  assert.ok(context.semanticColors[0] > 0.6);
  assert.deepEqual(calls, [
    "topics", "density:false", "colors", "labels", "reveal-labels", "invalidate",
  ]);
});

test("完整点集和标签返回顺序不会在加载中重算首屏网格", () => {
  const context = {
    sceneInteractive: true,
    deferMembraneDensity: false,
  };

  EventMapController.prototype.setSceneInteractive.call(context, false);
  assert.equal(context.sceneInteractive, false);
  assert.equal(context.deferMembraneDensity, true);
  assert.equal(context.hydrationAnimationActive, true);

  EventMapController.prototype.setSceneInteractive.call(context, true);
  assert.equal(context.sceneInteractive, true);
  assert.equal(context.deferMembraneDensity, true);
  assert.equal(context.hydrationAnimationActive, false);

  EventMapController.prototype.finishInitialHydration.call(context);
  assert.equal(context.deferMembraneDensity, false);
});

test("完整场景等待动画共用粒子和网格时钟，并在交互恢复时立即复位", () => {
  const context = {
    sceneInteractive: false,
    deferMembraneDensity: true,
    hydrationAnimationActive: true,
    hydrationAnimationStartedAt: 1_000,
    coreMaterial: {
      uniforms: {
        uHydrationActive: { value: 1 },
        uHydrationTime: { value: 0 },
      },
    },
    horizonGrid: {
      material: {
        uniforms: {
          uHydrationActive: { value: 1 },
          uHydrationTime: { value: 0 },
        },
      },
    },
  };

  assert.equal(EventMapController.prototype.updateHydrationAnimation.call(context, 3_500), true);
  assert.equal(context.coreMaterial.uniforms.uHydrationTime.value, 2.5);
  assert.equal(context.horizonGrid.material.uniforms.uHydrationTime.value, 2.5);

  EventMapController.prototype.setSceneInteractive.call(context, true);
  assert.equal(context.hydrationAnimationActive, false);
  assert.equal(context.hydrationAnimationStartedAt, 0);
  assert.equal(context.coreMaterial.uniforms.uHydrationActive.value, 0);
  assert.equal(context.coreMaterial.uniforms.uHydrationTime.value, 0);
  assert.equal(context.horizonGrid.material.uniforms.uHydrationActive.value, 0);
  assert.equal(context.horizonGrid.material.uniforms.uHydrationTime.value, 0);
});

test("三维控制器公开相机、元数据补齐、显式聚焦、选择与资源销毁入口", () => {
  for (const method of ["resetCamera", "focusPoint", "focusTopic", "fitBounds", "fitActiveWindow", "setSelection", "setSelectionDetail", "previewWindow", "cancelWindowPreview", "prepareProgressiveReveal", "startProgressiveReveal", "updateManifest", "replaceScene", "setSceneInteractive", "finishInitialHydration", "whenFirstFrame", "destroy"]) {
    assert.equal(typeof EventMapController.prototype[method], "function");
  }
  assert.equal(typeof EventMapController.prototype.setCameraMode, "undefined");
});

test("不可变场景使用浏览器缓存，首帧不等待实体排行，并只保留三维透视相机", async () => {
  const controller = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  const model = await readFile(new URL("../event-map-model.js", import.meta.url), "utf8");
  assert.match(model, /cache: "force-cache"/);
  assert.doesNotMatch(model, /anticipatedFullScene|anticipatedPreviewScene/);
  assert.match(model, /const rendererPromise = this\.playlistEventMapPreloadRenderer\(\)/);
  assert.match(model, /preview_limit: String\(EVENT_MAP_PREVIEW_LIMIT\)/);
  assert.match(model, /compact: true, snapshotId: forceLatest \? "" : anticipatedSnapshotId/);
  assert.match(model, /includeCoverage: false, snapshotId/);
  assert.match(model, /mountedController\.updateManifest\(fullManifest\)/);
  assert.ok(model.indexOf("const bootManifest = await this.playlistEventMapLoadManifest") < model.indexOf("const emptyScene = emptyEventMapScene()"));
  assert.ok(model.indexOf('this._playlistEventMapMount.style.visibility = "visible"') < model.indexOf("const previewBuffer = await this.playlistEventMapFetchBinary"));
  assert.ok(model.indexOf("mountedController.replaceScene(previewScene)") < model.lastIndexOf("const fullScenePromise = this.playlistEventMapFetchBinary"));
  assert.ok(model.indexOf("target: EVENT_MAP_PREVIEW_REVEAL_TARGET") < model.lastIndexOf("mountedController.updateManifest(fullManifest"));
  assert.ok(model.indexOf("mountedController.replaceScene(fullScene") < model.lastIndexOf("this.playlistEventMapLoadDeferredCoverage({ pid"));
  assert.doesNotMatch(model, /await this\.playlistEventMapLoadEntities\(\{ silent: true \}\)/);
  assert.match(controller, /const modules = await loadThreeModule\(\)/);
  const createStart = controller.indexOf("static async create(options)");
  const createEnd = controller.indexOf("\n  constructor(", createStart);
  assert.doesNotMatch(controller.slice(createStart, createEnd), /eventMapGpuAvailable\(\)/);
  assert.match(controller, /scene\.count > 100_000 \? 1\.25 : 1\.5/);
  assert.match(controller, /ACTIVE_WINDOW_PERSPECTIVE_DISTANCE_SCALE = 1\.9/);
  assert.match(controller, /EVENT_MAP_HOME_CAMERA_SCALE = 0\.7/);
  assert.match(controller, /camera\.position\.set\(13\.5, 10\.5, 15\.5\)\.multiplyScalar\(EVENT_MAP_HOME_CAMERA_SCALE\)/);
  assert.doesNotMatch(controller, /OrthographicCamera|orthographicCamera|cameraMode|setCameraMode/);
});

test("首次加载直接创建最终 WebGL 网格，等待完整点集时保持网格扫描和预览点流动", async () => {
  const controller = await readFile(new URL("../event-map.js", import.meta.url), "utf8");
  const model = await readFile(new URL("../event-map-model.js", import.meta.url), "utf8");
  const v2Model = await readFile(new URL("../v2-model.js", import.meta.url), "utf8");
  const loadFieldStart = v2Model.indexOf("async loadField()");
  const loadField = v2Model.slice(loadFieldStart, v2Model.indexOf("\n    leaveField()", loadFieldStart));
  const initModel = await readFile(new URL("../../app/init-model.js", import.meta.url), "utf8");
  const urlState = await readFile(new URL("../../services/url-state.js", import.meta.url), "utf8");
  const template = await readFile(new URL("../../../templates/app/views/field-v2.html", import.meta.url), "utf8");
  const styles = await readFile(new URL("../../../input.css", import.meta.url), "utf8");

  assert.doesNotMatch(template, /event-map-loading-field|event-map-loading-horizon|event-map-loading-plane/);
  assert.doesNotMatch(template, /role="progressbar"|正在建立可交互星域|event-map-loading-step/);
  assert.match(initModel, /if \(initialView === "field"\) this\.playlistEventMapLoading = true/);
  assert.match(initModel, /localStorage\.getItem\(this\.v2LastDomainKey\)/);
  assert.match(initModel, /this\._preloadInitialFieldRenderer\(\)/);
  assert.match(initModel, /this\._startInitialFieldMap\(\)/);
  assert.ok(initModel.indexOf("this._startInitialFieldMap();") < initModel.indexOf("await this.loadSystemStatus"));
  assert.match(v2Model, /ensureCurrentDomain\(\{ compact: true \}\)/);
  assert.match(urlState, /if \(key === "field" && !this\.playlistEventMapController\?\.\(\)\) this\.playlistEventMapLoading = true/);
  assert.match(loadField, /const mapPromise = this\.playlistEventMapLoadView\(\{[\s\S]*?initialCursor:[\s\S]*?Promise\.all/);
  assert.match(loadField, /requestedWindowStart[\s\S]*?playlistEventMapWindowMonths = 12/);
  assert.doesNotMatch(loadField, /cursor\?\.event_time_start/);
  assert.doesNotMatch(loadField, /cursor\?\.event_time_end/);
  assert.ok(loadField.indexOf("const mapPromise = this.playlistEventMapLoadView({") < loadField.indexOf("observation/cursor"));
  assert.match(loadField, /playlistEventMapWaitForCompleteScene\(\{ includeMetadata: Boolean\(topicId\) \}\)/);
  assert.match(controller, /attribute float aRevealOrder;/);
  assert.match(controller, /uniform float uRevealProgress;/);
  assert.match(controller, /uniform float uHydrationActive;/);
  assert.match(controller, /uniform float uHydrationTime;/);
  assert.match(controller, /float hydrationGlint = uHydrationActive/);
  assert.match(controller, /float hydrationSweep = uHydrationActive/);
  assert.match(controller, /updateHydrationAnimation\(now\)/);
  assert.match(controller, /this\.labelsRoot\.classList\.add\("event-map-label-layer--loading"\)/);
  assert.match(controller, /this\.labelsRoot\.classList\.remove\("event-map-label-layer--loading"\)/);
  assert.match(model, /const emptyScene = emptyEventMapScene\(\)/);
  assert.match(model, /playlistEventMapControllerOptions\(pendingMount, emptyScene, bootManifest\)/);
  assert.match(model, /const revealPrepared = mountedController\.prepareProgressiveReveal\(\)/);
  assert.match(model, /await pendingController\.whenFirstFrame\(\)/);
  assert.match(model, /mountedController\.replaceScene\(previewScene\)/);
  assert.match(model, /initialCameraApplied: true/);
  assert.match(model, /mountedController\.finishInitialHydration\(\)/);
  assert.match(model, /if \(!backgroundLoadDetached && !preserveCurrentSceneBackground\) this\._playlistEventMapSceneLoadingSnapshotId = ""/);
  assert.doesNotMatch(model, /Promise\.allSettled\(\[fullSceneLoad, metadataLoad\]\)/);
  assert.match(model, /fullSceneLoad\.then\(clearFullSceneLoad, clearFullSceneLoad\)/);
  assert.match(model, /metadataLoad\.then\(clearMetadataLoad, clearMetadataLoad\)/);
  assert.match(model, /EVENT_MAP_PREVIEW_REVEAL_TARGET = 0\.94/);
  assert.match(model, /EVENT_MAP_PREVIEW_REVEAL_MS = 4_600/);
  assert.match(model, /target: EVENT_MAP_PREVIEW_REVEAL_TARGET,[\s\S]*?duration: EVENT_MAP_PREVIEW_REVEAL_MS,[\s\S]*?complete: false/);
  assert.match(model, /mountedController\.replaceScene\(fullScene, \{ preserveRevealCount: revealPrepared \}\)/);
  assert.match(model, /startProgressiveReveal\(\{ target: 1, duration: 1250, complete: true \}\)/);
  assert.ok(model.indexOf("await pendingController.whenFirstFrame()") < model.indexOf('this._playlistEventMapMount.style.visibility = "visible"'));
  assert.ok(model.indexOf('this._playlistEventMapMount.style.visibility = "visible"') < model.indexOf("mountedController.replaceScene(previewScene)"));
  assert.ok(model.indexOf("mountedController.replaceScene(previewScene)") < model.indexOf("target: EVENT_MAP_PREVIEW_REVEAL_TARGET"));
  assert.doesNotMatch(model, /mountedController\.fitActiveWindow\(\)/);
  assert.match(controller, /this\.resetCamera\(\{ animate: false \}\)/);
  assert.match(styles, /\.event-map-label-layer--loading \{[\s\S]*?opacity: 0;[\s\S]*?filter: blur\(6px\)/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.event-map-label-layer/);
});

test("真实点与主题元数据都准备完成后才开始显示标签", () => {
  const removed = [];
  let loading = true;
  const context = {
    progressiveRevealActive: true,
    progressiveRevealPrepared: true,
    progressiveRevealStartedAt: 0,
    progressiveRevealComplete: false,
    labelMetadataReady: false,
    coreMaterial: { uniforms: { uRevealProgress: { value: 0 } } },
    pickMaterial: { uniforms: { uRevealProgress: { value: 0 } } },
    labelsRoot: {
      offsetWidth: 100,
      classList: {
        contains: () => loading,
        remove: (value) => { loading = false; removed.push(value); },
      },
    },
    revealLabelsIfReady: EventMapController.prototype.revealLabelsIfReady,
  };

  assert.equal(EventMapController.prototype.updateProgressiveReveal.call(context, 550), true);
  assert.ok(context.coreMaterial.uniforms.uRevealProgress.value > 0.5);
  assert.deepEqual(removed, []);
  assert.equal(EventMapController.prototype.updateProgressiveReveal.call(context, 1100), false);
  assert.equal(context.coreMaterial.uniforms.uRevealProgress.value, 1);
  assert.deepEqual(removed, []);
  context.labelMetadataReady = true;
  assert.equal(EventMapController.prototype.revealLabelsIfReady.call(context), true);
  assert.deepEqual(removed, ["event-map-label-layer--loading"]);
});

test("事件语义星域状态与控制合并在同一工具栏", async () => {
  const template = await readFile(new URL("../../../templates/app/views/field-v2.html", import.meta.url), "utf8");
  const styles = await readFile(new URL("../../../input.css", import.meta.url), "utf8");
  const section = template.slice(template.indexOf("<!-- V2 事件语义星域：独立于来源播放与旧设置。 -->"));
  assert.match(section, /<header class="raelyn-surface-toolbar shrink-0 border-b border-slate-800">[\s\S]*?24H事件[\s\S]*?本周事件[\s\S]*?<\/header>/);
  assert.doesNotMatch(section, />截至</);
  assert.doesNotMatch(section, /\['now','replay','story','verify'\]/);
  assert.match(section, /class="raelyn-field-toolbar-layout px-4 py-2"/);
  assert.match(styles, /\.raelyn-field-toolbar-layout \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\) max-content/);
  assert.doesNotMatch(styles, /repeat\(auto-fit, minmax\(min\(100%, 66rem\), 1fr\)\)/);
  assert.match(section, /class="raelyn-field-toolbar-summary flex min-w-0 items-center/);
  assert.match(section, /class="raelyn-field-toolbar-actions flex min-w-0 items-center justify-end/);
  assert.doesNotMatch(section, /class="px-4 py-2 flex flex-wrap items-center/);
  assert.match(section, /事件语义星域/);
  assert.doesNotMatch(section, /x-text="activeView==='field' \? currentDomainLabel\(\)/);
  assert.match(section, /playlistEventMapStatusBadgeHint/);
  assert.match(section, /<span class="inline-flex shrink-0[^>]+x-show="playlistEventMapActiveBackfillJob\(\)"/);
  assert.doesNotMatch(section, /<div[^>]+x-show="playlistEventMapActiveBackfillJob\(\)"/);
  assert.doesNotMatch(section, /三维语义星域 · 时间只改变当前窗口星云/);
  assert.match(section, /三维语义空间 · 左键旋转/);
  assert.match(section, /代表事件/);
  assert.match(section, /playlistEventMapFocusSelectedCanonical/);
  assert.match(template, /x-show="activeView==='field'"/);
  assert.doesNotMatch(template, /<path d="M3 3v18h18"><\/path>/);
  assert.doesNotMatch(section, /<div class="text-sm font-semibold text-slate-100">三维事件星图<\/div>/);
  assert.match(section, /playlistEventMapSelectedId \? 'lg:right-\[380px\]'/);
  assert.match(section, /fieldObservationRailOpen \? 'lg:right-80'/);
});
