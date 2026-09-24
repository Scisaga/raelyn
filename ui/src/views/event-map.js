const SCENE_RECORD_SIZE = 56;
const INDEX_RECORD_SIZE = 4;
const DAY_MS = 86_400_000;
const NO_INDEX = 0xffff_ffff;
const ACTIVE_WINDOW_PERSPECTIVE_DISTANCE_SCALE = 1.9;
const TOPIC_ZOOM_ENTER = 1.7;
const TOPIC_ZOOM_EXIT = 1.45;
const EVENT_ZOOM_ENTER = 4.8;
const EVENT_ZOOM_EXIT = 4.2;
const WINDOW_TRANSITION_MS = 220;
const SEMANTIC_MEMBRANE_TRANSITION_MS = 360;
const PROGRESSIVE_REVEAL_MS = 1100;
const HORIZON_GRID_RADIUS = 120;
const EVENT_MAP_HOME_CAMERA_SCALE = 0.7;
const EVENT_MAP_MEDIA_CARD_LIMIT = 10;
const EVENT_MAP_MEDIA_SAFE_INSET = 56;
const EVENT_MAP_MEDIA_COLLISION_GAP = 10;
const EVENT_MAP_MEDIA_SAFE_TOP = 58;
const EVENT_MAP_MEDIA_SAFE_BOTTOM = 64;
const EVENT_MAP_MEDIA_CARD_WIDTH = 216;
const EVENT_MAP_MEDIA_CARD_HEIGHT = 84;
const EVENT_MAP_MEDIA_MINI_WIDTH = 84;
const EVENT_MAP_MEDIA_MINI_HEIGHT = 48;
const EVENT_MAP_MEDIA_MAX_CONNECTOR = 300;
const EVENT_MAP_MEDIA_SIDE_BAND_RATIO = 0.36;
const EVENT_MAP_MEDIA_HOVER_OPEN_MS = 180;
const EVENT_MAP_MEDIA_HOVER_CLOSE_MS = 220;
const EVENT_MAP_MEDIA_RING_GAPS = [28, 72, 120, 168, 216, 252, 284, 298];
const EVENT_MAP_MEDIA_ANGLE_OFFSETS = [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6, 7, -7, 8, -8, 9, -9, 10, -10, 11, -11, 12];
const THREE_MODULE_URL = "/static/vendor/event-map-three.js";
const DEFAULT_SEMANTIC_FAMILY = {
  code: "other",
  label: "其他",
  color: "#94a3b8",
  rgb: [0.58, 0.64, 0.72],
};

let threeModulePromise = null;

function loadThreeModule() {
  if (!threeModulePromise) threeModulePromise = import(THREE_MODULE_URL);
  return threeModulePromise;
}

export function preloadEventMapRenderer() {
  return loadThreeModule();
}

function uuidFromBytes(bytes) {
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * 56 字节小端记录：
 * point_index、canonical UUID、x/y/z、起止日、类型/精度/标志、
 * 成员数、一级星域索引、二级主题索引。
 */
export function parseEventMapScene(buffer, expectedCount = null) {
  if (!(buffer instanceof ArrayBuffer)) throw new TypeError("事件语义星域场景必须是 ArrayBuffer");
  if (buffer.byteLength % SCENE_RECORD_SIZE !== 0) {
    throw new Error(`事件语义星域场景长度 ${buffer.byteLength} 不是 ${SCENE_RECORD_SIZE} 的整数倍`);
  }
  const count = buffer.byteLength / SCENE_RECORD_SIZE;
  if (expectedCount !== null && Number(expectedCount) !== count) {
    throw new Error(`事件语义星域真实事件数不一致：manifest=${Number(expectedCount)}，scene=${count}`);
  }
  const view = new DataView(buffer);
  const x = new Float32Array(count);
  const y = new Float32Array(count);
  const z = new Float32Array(count);
  const startDay = new Int32Array(count);
  const endDay = new Int32Array(count);
  const eventType = new Uint8Array(count);
  const timePrecision = new Uint8Array(count);
  const flags = new Uint8Array(count);
  const memberCount = new Uint32Array(count);
  const macroTopicIndex = new Uint32Array(count);
  const localTopicIndex = new Uint32Array(count);
  const canonicalIds = new Array(count);
  for (let record = 0; record < count; record += 1) {
    const offset = record * SCENE_RECORD_SIZE;
    const pointIndex = view.getUint32(offset, true);
    if (pointIndex !== record) throw new Error(`事件语义星域 point_index 不连续：${pointIndex} != ${record}`);
    canonicalIds[record] = uuidFromBytes(new Uint8Array(buffer, offset + 4, 16));
    x[record] = view.getFloat32(offset + 20, true);
    y[record] = view.getFloat32(offset + 24, true);
    z[record] = view.getFloat32(offset + 28, true);
    startDay[record] = view.getInt32(offset + 32, true);
    endDay[record] = view.getInt32(offset + 36, true);
    eventType[record] = view.getUint8(offset + 40);
    timePrecision[record] = view.getUint8(offset + 41);
    flags[record] = view.getUint8(offset + 42);
    memberCount[record] = view.getUint32(offset + 44, true);
    macroTopicIndex[record] = view.getUint32(offset + 48, true);
    localTopicIndex[record] = view.getUint32(offset + 52, true);
  }
  return {
    count,
    x,
    y,
    z,
    startDay,
    endDay,
    eventType,
    timePrecision,
    flags,
    memberCount,
    macroTopicIndex,
    localTopicIndex,
    canonicalIds,
  };
}

export function parseEventMapIndices(buffer) {
  if (!(buffer instanceof ArrayBuffer)) throw new TypeError("事件索引数据必须是 ArrayBuffer");
  if (buffer.byteLength % INDEX_RECORD_SIZE !== 0) throw new Error("事件索引数据长度无效");
  const view = new DataView(buffer);
  const indices = new Uint32Array(buffer.byteLength / INDEX_RECORD_SIZE);
  for (let index = 0; index < indices.length; index += 1) indices[index] = view.getUint32(index * 4, true);
  return indices;
}

export function isoDateToEventMapDay(value, fallback) {
  const normalized = String(value || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(normalized)) return fallback;
  const millis = Date.parse(`${normalized}T00:00:00Z`);
  return Number.isFinite(millis) ? Math.floor(millis / DAY_MS) : fallback;
}

function eventMapDayToIso(value) {
  const day = Number(value);
  return Number.isFinite(day) ? new Date(day * DAY_MS).toISOString().slice(0, 10) : "";
}

function clampNumber(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function eventMapNearestCardEdge(anchorX, anchorY, cardRect) {
  const right = cardRect.left + cardRect.width;
  const bottom = cardRect.top + cardRect.height;
  let x = clampNumber(anchorX, cardRect.left, right);
  let y = clampNumber(anchorY, cardRect.top, bottom);
  if (anchorX >= cardRect.left && anchorX <= right && anchorY >= cardRect.top && anchorY <= bottom) {
    const edges = [
      { distance: anchorX - cardRect.left, x: cardRect.left, y: anchorY },
      { distance: right - anchorX, x: right, y: anchorY },
      { distance: anchorY - cardRect.top, x: anchorX, y: cardRect.top },
      { distance: bottom - anchorY, x: anchorX, y: bottom },
    ].sort((left, rightEdge) => left.distance - rightEdge.distance);
    ({ x, y } = edges[0]);
  }
  return { x, y, distance: Math.hypot(x - anchorX, y - anchorY) };
}

export function eventMapMediaConnectorPath(anchorX, anchorY, cardRect) {
  const edge = eventMapNearestCardEdge(anchorX, anchorY, cardRect);
  return `M ${anchorX.toFixed(2)} ${anchorY.toFixed(2)} L ${edge.x.toFixed(2)} ${edge.y.toFixed(2)}`;
}

export function eventMapMediaItemKey(item, fallback = "") {
  return String(item?.primary_video?.video_id || item?.video_id || item?.canonical_id || fallback);
}

function eventMapCompactDateLabel(value) {
  const normalized = String(value || "").slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(normalized) ? normalized.slice(5).replace("-", "/") : "";
}

function eventMapEventDateLabel(item) {
  return eventMapCompactDateLabel(item?.event_time_start);
}

function normalizeEventMapAngle(value) {
  let angle = Number(value || 0) % (Math.PI * 2);
  if (angle > Math.PI) angle -= Math.PI * 2;
  if (angle <= -Math.PI) angle += Math.PI * 2;
  return angle;
}

function eventMapMediaCardRect(point, angle, radialGap, width, height) {
  const cosine = Math.cos(angle);
  const sine = Math.sin(angle);
  // 将矩形在候选方向上的支撑半径加到间距中，使锚点始终位于卡片外。
  const support = Math.abs(cosine) * width / 2 + Math.abs(sine) * height / 2;
  const centerDistance = support + radialGap;
  return {
    left: point.x + cosine * centerDistance - width / 2,
    top: point.y + sine * centerDistance - height / 2,
    width,
    height,
  };
}

function eventMapMediaRectFits(rect, bounds) {
  return rect.left >= bounds.left
    && rect.top >= bounds.top
    && rect.left + rect.width <= bounds.right
    && rect.top + rect.height <= bounds.bottom;
}

function eventMapMediaRectFitsSideBand(rect, viewportWidth, sideBandRatio) {
  const leftBoundary = viewportWidth * sideBandRatio;
  const rightBoundary = viewportWidth * (1 - sideBandRatio);
  return rect.left + rect.width <= leftBoundary || rect.left >= rightBoundary;
}

function eventMapMediaRectsOverlap(left, right, gap) {
  return left.left < right.left + right.width + gap
    && left.left + left.width + gap > right.left
    && left.top < right.top + right.height + gap
    && left.top + left.height + gap > right.top;
}

function eventMapMediaPlacementCandidates(point, viewportWidth, viewportHeight, mini) {
  const preferred = point.preferredPlacement;
  const candidates = [];
  const seen = new Set();
  const append = (angle, radialGap, preferredCandidate = false) => {
    const normalizedAngle = normalizeEventMapAngle(angle);
    const normalizedGap = clampNumber(Number(radialGap || 0), 0, EVENT_MAP_MEDIA_MAX_CONNECTOR);
    const key = `${normalizedAngle.toFixed(6)}:${normalizedGap.toFixed(2)}`;
    if (seen.has(key)) return;
    seen.add(key);
    candidates.push({ angle: normalizedAngle, radialGap: normalizedGap, preferredCandidate });
  };

  if (preferred && Boolean(preferred.mini) === mini && Number.isFinite(preferred.angle)) {
    append(preferred.angle, preferred.radialGap, true);
  }
  const outwardX = point.x - viewportWidth / 2;
  const outwardY = point.y - viewportHeight / 2;
  const outwardDistance = Math.hypot(outwardX, outwardY);
  const deterministicCenterAngle = normalizeEventMapAngle((point.rank + 1) * 2.399963229728653);
  const baseAngle = Number.isFinite(preferred?.angle)
    ? preferred.angle
    : (outwardDistance > 24 ? Math.atan2(outwardY, outwardX) : deterministicCenterAngle);
  for (const radialGap of EVENT_MAP_MEDIA_RING_GAPS) {
    for (const offset of EVENT_MAP_MEDIA_ANGLE_OFFSETS) {
      append(baseAngle + offset * Math.PI / 12, radialGap);
    }
  }
  return candidates;
}

/**
 * 将视频卡完整约束在画布左右两条内缩侧带，中央星云只保留事件与标签。
 * 卡片不是贴边轨道，仍会在侧带内跟随事件投影，并以有限连线保持空间关系。
 * 输入顺序就是优先级；空间不足时先缩成缩略图，仍无法消除碰撞时才隐藏。
 */
export function layoutEventMapMediaCards(points, {
  width,
  height,
  inset = EVENT_MAP_MEDIA_SAFE_INSET,
  gap = EVENT_MAP_MEDIA_COLLISION_GAP,
  topInset = EVENT_MAP_MEDIA_SAFE_TOP,
  bottomInset = EVENT_MAP_MEDIA_SAFE_BOTTOM,
  maximum = EVENT_MAP_MEDIA_CARD_LIMIT,
  maximumConnector = EVENT_MAP_MEDIA_MAX_CONNECTOR,
  sideBandRatio = EVENT_MAP_MEDIA_SIDE_BAND_RATIO,
  preservePlacement = false,
} = {}) {
  const viewportWidth = Math.max(1, Number(width || 1));
  const viewportHeight = Math.max(1, Number(height || 1));
  const bounds = {
    left: Math.max(0, Number(inset || 0)),
    top: Math.max(0, Number(topInset || 0)),
    right: viewportWidth - Math.max(0, Number(inset || 0)),
    bottom: viewportHeight - Math.max(0, Number(bottomInset || 0)),
  };
  const candidates = (Array.isArray(points) ? points : [])
    .filter((point) => point && Number.isFinite(point.x) && Number.isFinite(point.y))
    .slice(0, Math.max(0, Number(maximum || 0)))
    .map((point, rank) => ({ ...point, rank }));

  const layouts = [];
  const occupied = [];
  const ordered = candidates.slice().sort((left, right) => Number(right.expanded) - Number(left.expanded) || left.rank - right.rank);
  for (const point of ordered) {
    const regularWidth = Math.max(1, Number(point.width || EVENT_MAP_MEDIA_CARD_WIDTH));
    const regularHeight = Math.max(1, Number(point.height || EVENT_MAP_MEDIA_CARD_HEIGHT));
    const modes = point.expanded
      ? [{ mini: false, width: regularWidth, height: regularHeight }]
      : [
        { mini: false, width: regularWidth, height: regularHeight },
        {
          mini: true,
          width: Math.max(1, Number(point.miniWidth || EVENT_MAP_MEDIA_MINI_WIDTH)),
          height: Math.max(1, Number(point.miniHeight || EVENT_MAP_MEDIA_MINI_HEIGHT)),
        },
      ];
    if (preservePlacement && point.preferredPlacement?.mini && !point.expanded) modes.reverse();
    let selected = null;
    for (const mode of modes) {
      const placements = eventMapMediaPlacementCandidates(point, viewportWidth, viewportHeight, mode.mini);
      for (const placement of placements) {
        const cardRect = eventMapMediaCardRect(
          point,
          placement.angle,
          placement.radialGap,
          mode.width,
          mode.height,
        );
        if (!eventMapMediaRectFits(cardRect, bounds)) continue;
        if (!eventMapMediaRectFitsSideBand(cardRect, viewportWidth, sideBandRatio)) continue;
        const edge = eventMapNearestCardEdge(point.x, point.y, cardRect);
        if (edge.distance > maximumConnector + 0.01) continue;
        const keepDuringInteraction = preservePlacement && placement.preferredCandidate;
        if (!keepDuringInteraction && occupied.some((rect) => eventMapMediaRectsOverlap(cardRect, rect, gap))) continue;
        const side = cardRect.left + cardRect.width / 2 < point.x ? "left" : "right";
        selected = {
          ...point,
          ...cardRect,
          side,
          mini: mode.mini,
          connectorEndX: edge.x,
          connectorEndY: edge.y,
          connectorLength: edge.distance,
          connectorPath: eventMapMediaConnectorPath(point.x, point.y, cardRect),
          placement: {
            angle: placement.angle,
            radialGap: placement.radialGap,
            mini: mode.mini,
          },
        };
        break;
      }
      if (selected) break;
    }
    if (!selected) continue;
    layouts.push(selected);
    occupied.push(selected);
  }
  return layouts.sort((left, right) => left.rank - right.rank);
}

/**
 * 播放卡片展开时冻结既有排布，避免宽度变化触发全量碰撞重排。
 * 左侧卡保留原右边界、右侧卡保留原左边界，因此折叠态命中区域始终包含在展开卡中。
 */
export function layoutFrozenEventMapMediaCards(points, frozenCards, {
  width,
  height,
  inset = EVENT_MAP_MEDIA_SAFE_INSET,
  topInset = EVENT_MAP_MEDIA_SAFE_TOP,
  bottomInset = EVENT_MAP_MEDIA_SAFE_BOTTOM,
} = {}) {
  const viewportWidth = Math.max(1, Number(width || 1));
  const viewportHeight = Math.max(1, Number(height || 1));
  const leftBound = Math.max(0, Number(inset || 0));
  const topBound = Math.max(0, Number(topInset || 0));
  const rightBound = viewportWidth - leftBound;
  const bottomBound = viewportHeight - Math.max(0, Number(bottomInset || 0));
  const frozen = frozenCards instanceof Map ? frozenCards : new Map();
  const layouts = [];
  for (const point of Array.isArray(points) ? points : []) {
    const state = frozen.get(point?.entry?.key);
    if (!state?.visible) continue;
    const expanded = Boolean(point.expanded);
    const cardWidth = Math.max(1, Number(expanded ? point.width : state.width));
    const cardHeight = Math.max(1, Number(expanded ? point.height : state.height));
    let left = Number(state.left || 0);
    let top = Number(state.top || 0);
    if (expanded && state.side === "left") left += Number(state.width || 0) - cardWidth;
    left = clampNumber(left, leftBound, Math.max(leftBound, rightBound - cardWidth));
    top = clampNumber(top, topBound, Math.max(topBound, bottomBound - cardHeight));
    layouts.push({
      ...point,
      left,
      top,
      width: cardWidth,
      height: cardHeight,
      side: state.side === "left" ? "left" : "right",
      mini: expanded ? false : Boolean(state.mini),
      placement: point.preferredPlacement || null,
    });
  }
  return layouts;
}

/** 镜头距离决定语义层级，并通过迟滞避免标签层闪烁。 */
export function eventMapSemanticLevel(zoomFactor, previousLevel = "overview") {
  const zoom = Math.max(0, Number(zoomFactor || 0));
  if (previousLevel === "event") {
    if (zoom >= EVENT_ZOOM_EXIT) return "event";
    return zoom >= TOPIC_ZOOM_EXIT ? "topic" : "overview";
  }
  if (previousLevel === "topic") {
    if (zoom >= EVENT_ZOOM_ENTER) return "event";
    return zoom < TOPIC_ZOOM_EXIT ? "overview" : "topic";
  }
  if (zoom >= EVENT_ZOOM_ENTER) return "event";
  return zoom >= TOPIC_ZOOM_ENTER ? "topic" : "overview";
}

export function eventMapLayerVisibility(level, selected = false) {
  return {
    macroLabels: level === "overview",
    topicLabels: level === "topic",
    canonical: level === "event",
    story: level === "event" && selected,
  };
}

export function buildEventMapLayerState(
  scene,
  {
    windowStart = "",
    windowEnd = "",
    entityIndices = null,
    selectedIndex = null,
    typeFilter = "",
  } = {}
) {
  const categories = new Uint8Array(scene.count);
  const activeMask = new Uint8Array(scene.count);
  const entityMask = new Uint8Array(scene.count);
  const entityPointIndices = [];
  const startDay = isoDateToEventMapDay(windowStart, -2_147_483_648);
  const endDay = isoDateToEventMapDay(windowEnd, 2_147_483_647);
  const normalizedType = String(typeFilter || "");
  const entitySet = entityIndices instanceof Set ? entityIndices : new Set(entityIndices || []);
  for (let index = 0; index < scene.count; index += 1) {
    const inWindow = scene.endDay[index] >= startDay && scene.startDay[index] <= endDay;
    const typeMatches = !normalizedType || String(scene.eventType[index]) === normalizedType;
    if (!inWindow || !typeMatches) continue;
    activeMask[index] = 1;
    categories[index] = 1;
    if (entitySet.has(index)) {
      entityMask[index] = 1;
      entityPointIndices.push(index);
      categories[index] = 2;
    }
  }
  if (Number.isInteger(selectedIndex) && activeMask[selectedIndex]) categories[selectedIndex] = 3;
  return { categories, activeMask, entityMask, entityPointIndices };
}

export function buildEventMapLayerCategories(scene, options = {}) {
  return buildEventMapLayerState(scene, options).categories;
}

export function countEventMapVisiblePoints(scene, windowStart = "", windowEnd = "", typeFilter = "") {
  if (!scene || !Number(scene.count || 0)) return 0;
  const startDay = isoDateToEventMapDay(windowStart, -2_147_483_648);
  const endDay = isoDateToEventMapDay(windowEnd, 2_147_483_647);
  const normalizedType = String(typeFilter || "");
  let count = 0;
  for (let index = 0; index < scene.count; index += 1) {
    if (scene.endDay[index] < startDay || scene.startDay[index] > endDay) continue;
    if (normalizedType && String(scene.eventType[index]) !== normalizedType) continue;
    count += 1;
  }
  return count;
}

export function eventMapActiveBounds(scene, activeMask) {
  if (!scene || !activeMask || !Number(scene.count || 0)) return null;
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  let count = 0;
  for (let index = 0; index < scene.count; index += 1) {
    if (!activeMask[index]) continue;
    const x = Number(scene.x[index]);
    const y = Number(scene.y[index]);
    const z = Number(scene.z[index]);
    if (![x, y, z].every(Number.isFinite)) continue;
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
    minZ = Math.min(minZ, z);
    maxZ = Math.max(maxZ, z);
    count += 1;
  }
  return count ? { minX, maxX, minY, maxY, minZ, maxZ, count } : null;
}

function eventMapWebGl2Available() {
  if (typeof document === "undefined") return false;
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("webgl2", { antialias: false });
  if (!context) return false;
  context.getExtension("WEBGL_lose_context")?.loseContext();
  return true;
}

export async function eventMapGpuAvailable() {
  return eventMapWebGl2Available();
}

function finiteBounds(values) {
  let minimum = Infinity;
  let maximum = -Infinity;
  for (const value of values) {
    if (!Number.isFinite(value)) continue;
    minimum = Math.min(minimum, value);
    maximum = Math.max(maximum, value);
  }
  return Number.isFinite(minimum) ? { minimum, maximum } : { minimum: -1, maximum: 1 };
}

function eventMapSceneBounds(manifest, scene) {
  const source = manifest?.bounds || {};
  const axisBounds = (axis, values) => {
    const pair = Array.isArray(source[axis]) ? source[axis] : [];
    const minimum = Number(source[`min_${axis}`] ?? pair[0]);
    const maximum = Number(source[`max_${axis}`] ?? pair[1]);
    return Number.isFinite(minimum) && Number.isFinite(maximum) && maximum >= minimum
      ? { minimum, maximum }
      : finiteBounds(values);
  };
  return {
    xBounds: axisBounds("x", scene.x),
    yBounds: axisBounds("y", scene.y),
    zBounds: axisBounds("z", scene.z),
  };
}

function rectangleOverlaps(left, right, padding = 4) {
  return !(
    left.right + padding < right.left
    || left.left - padding > right.right
    || left.bottom + padding < right.top
    || left.top - padding > right.bottom
  );
}

export function eventMapLabelText(label = {}) {
  const title = String(label.label || "");
  const count = Math.max(0, Number(label.count || 0));
  return count ? `${title} · ${count}` : title;
}

export function eventMapLabelWidth(text, measureText = null, maximum = 460) {
  const label = String(text || "");
  const fallbackWidth = [...label].reduce(
    (total, character) => total + (character.codePointAt(0) > 0x2fff ? 10 : 6.1),
    0
  );
  const textWidth = typeof measureText === "function" ? Number(measureText(label)) : fallbackWidth;
  return Math.min(Math.max(48, Number(maximum || 460)), Math.max(48, Math.ceil(Number.isFinite(textWidth) ? textWidth : fallbackWidth) + 18));
}

export function eventMapLabelFocusOpacity(relation = "normal") {
  return {
    selected: 1,
    member: 0.82,
    direct: 0.62,
    context: 0.54,
    unrelated: 0.34,
    normal: 1,
  }[String(relation || "normal")] ?? 1;
}

export function buildEventMapSpacetimeGrid({
  extent = 15,
  lineCount = 23,
  segmentCount = 48,
  wells = [],
} = {}) {
  const safeExtent = Math.max(4, Number(extent || 15));
  const safeLineCount = Math.max(3, Math.floor(Number(lineCount || 23)));
  const safeSegmentCount = Math.max(8, Math.floor(Number(segmentCount || 48)));
  const safeWells = Array.isArray(wells) ? wells : [];
  const positions = [];
  const fades = [];
  const curvatures = [];
  const centerLine = Math.round((safeLineCount - 1) / 2);

  const sample = (x, y, major) => {
    let curvature = 0;
    for (const well of safeWells) {
      const radius = Math.max(0.4, Number(well?.radius || 2));
      const strength = Math.max(0, Number(well?.strength || 0));
      const dx = x - Number(well?.x || 0);
      const dy = y - Number(well?.y || 0);
      curvature += strength * Math.exp(-(dx * dx + dy * dy) / (2 * radius * radius));
    }
    curvature = Math.min(2.8, curvature);
    const edgeDistance = safeExtent - Math.max(Math.abs(x), Math.abs(y));
    const edge = Math.max(0, Math.min(1, edgeDistance / (safeExtent * 0.18)));
    const edgeFade = edge * edge * (3 - 2 * edge);
    return {
      position: [x, y, -curvature],
      fade: edgeFade * (major ? 1 : 0.72),
      curvature: curvature / 2.8,
    };
  };
  const appendSegment = (start, end) => {
    positions.push(...start.position, ...end.position);
    fades.push(start.fade, end.fade);
    curvatures.push(start.curvature, end.curvature);
  };
  for (let lineIndex = 0; lineIndex < safeLineCount; lineIndex += 1) {
    const fixed = -safeExtent + (2 * safeExtent * lineIndex) / (safeLineCount - 1);
    const major = Math.abs(lineIndex - centerLine) % 4 === 0;
    for (let segment = 0; segment < safeSegmentCount; segment += 1) {
      const start = -safeExtent + (2 * safeExtent * segment) / safeSegmentCount;
      const end = -safeExtent + (2 * safeExtent * (segment + 1)) / safeSegmentCount;
      appendSegment(sample(start, fixed, major), sample(end, fixed, major));
      appendSegment(sample(fixed, start, major), sample(fixed, end, major));
    }
  }
  return {
    positions: Float32Array.from(positions),
    fades: Float32Array.from(fades),
    curvatures: Float32Array.from(curvatures),
  };
}

function hexColorToRgb(value) {
  const match = String(value || "").trim().match(/^#([0-9a-f]{6})$/i);
  if (!match) return [...DEFAULT_SEMANTIC_FAMILY.rgb];
  const integer = Number.parseInt(match[1], 16);
  return [
    ((integer >> 16) & 255) / 255,
    ((integer >> 8) & 255) / 255,
    (integer & 255) / 255,
  ];
}

function eventMapRevealOrder(index) {
  let hash = Math.imul(Number(index) + 1, 0x9e3779b1) >>> 0;
  hash ^= hash >>> 16;
  return 1 + (hash % 65_534);
}

export function eventMapSemanticPalette(manifest = {}) {
  const families = new Map(
    (Array.isArray(manifest.semantic_families) ? manifest.semantic_families : [])
      .map((family) => {
        const code = String(family?.code || "").trim();
        if (!code) return null;
        const color = String(family?.color || DEFAULT_SEMANTIC_FAMILY.color);
        return [code, {
          code,
          label: String(family?.label || code),
          color,
          rgb: hexColorToRgb(color),
        }];
      })
      .filter(Boolean)
  );
  const byTypeCode = [];
  for (const category of Array.isArray(manifest.type_categories) ? manifest.type_categories : []) {
    const typeCode = Number(category?.code);
    if (!Number.isInteger(typeCode) || typeCode < 0) continue;
    const familyCode = String(category?.semantic_family || "other");
    const declared = families.get(familyCode);
    const color = String(category?.semantic_color || declared?.color || DEFAULT_SEMANTIC_FAMILY.color);
    byTypeCode[typeCode] = {
      code: familyCode,
      label: String(category?.semantic_family_label || declared?.label || DEFAULT_SEMANTIC_FAMILY.label),
      color,
      rgb: hexColorToRgb(color),
    };
  }
  return { families, byTypeCode, fallback: DEFAULT_SEMANTIC_FAMILY };
}

function createParticleMaterial(THREE, { size, alpha }) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uSize: { value: size },
      uAlpha: { value: alpha },
      uPixelRatio: { value: Math.min(2, Number(globalThis.devicePixelRatio || 1)) },
      uPreviousWindowStartDay: { value: -1_000_000_000 },
      uPreviousWindowEndDay: { value: 1_000_000_000 },
      uWindowStartDay: { value: -1_000_000_000 },
      uWindowEndDay: { value: 1_000_000_000 },
      uWindowMix: { value: 1 },
      uRevealProgress: { value: 1 },
      uHydrationActive: { value: 0 },
      uHydrationTime: { value: 0 },
      uHasTopicFocus: { value: 0 },
    },
    vertexShader: `
      uniform float uSize;
      uniform float uPixelRatio;
      uniform float uPreviousWindowStartDay;
      uniform float uPreviousWindowEndDay;
      uniform float uWindowStartDay;
      uniform float uWindowEndDay;
      uniform float uWindowMix;
      uniform float uRevealProgress;
      uniform float uHydrationActive;
      uniform float uHydrationTime;
      uniform float uHasTopicFocus;
      attribute float aOpacity;
      attribute float aRevealOrder;
      attribute float aTopicFocus;
      attribute float aTimeFocus;
      attribute float aSelected;
      attribute vec3 aColor;
      attribute float aStartDay;
      attribute float aEndDay;
      varying float vOpacity;
      varying float vTopicFocus;
      varying float vTimeFocus;
      varying float vSelected;
      varying float vHydrationGlint;
      varying vec3 vColor;
      void main() {
        float previousActive = step(aStartDay, uPreviousWindowEndDay) * step(uPreviousWindowStartDay, aEndDay);
        float currentActive = step(aStartDay, uWindowEndDay) * step(uWindowStartDay, aEndDay);
        float revealed = smoothstep(aRevealOrder, min(1.0, aRevealOrder + 0.075), clamp(uRevealProgress, 0.0, 1.0));
        float hydrationPhase = fract(uHydrationTime * 0.14);
        float hydrationDistance = abs(aRevealOrder - hydrationPhase);
        hydrationDistance = min(hydrationDistance, 1.0 - hydrationDistance);
        float hydrationGlint = uHydrationActive * (1.0 - smoothstep(0.025, 0.105, hydrationDistance)) * revealed;
        float activeWindow = mix(previousActive, currentActive, clamp(uWindowMix, 0.0, 1.0));
        float ageDays = max(0.0, uWindowEndDay - min(aEndDay, uWindowEndDay));
        float timeOpacity = mix(1.0, 0.22, smoothstep(30.0, 365.0, ageDays));
        float composedOpacity = aOpacity * timeOpacity;
        composedOpacity = max(composedOpacity, aTopicFocus * 0.65);
        float allowedTimeFocus = aTimeFocus * mix(1.0, mix(0.36, 1.0, aTopicFocus), uHasTopicFocus);
        composedOpacity = max(composedOpacity, allowedTimeFocus * 0.92);
        composedOpacity = max(composedOpacity, aSelected);
        vOpacity = composedOpacity * activeWindow * revealed * (1.0 + hydrationGlint * 0.30);
        vTopicFocus = aTopicFocus;
        vTimeFocus = allowedTimeFocus;
        vSelected = aSelected;
        vHydrationGlint = hydrationGlint;
        vColor = aColor;
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = uSize * uPixelRatio * (1.0 + 0.42 * aTopicFocus + 0.55 * allowedTimeFocus + 0.92 * aSelected) * (1.0 + hydrationGlint * 0.32) * clamp(7.0 / max(1.0, -mvPosition.z), 0.55, 2.8);
        gl_Position = projectionMatrix * mvPosition;
      }
    `,
    fragmentShader: `
      varying float vOpacity;
      varying float vTopicFocus;
      varying float vTimeFocus;
      varying float vSelected;
      varying float vHydrationGlint;
      varying vec3 vColor;
      uniform float uAlpha;
      void main() {
        vec2 offset = gl_PointCoord - vec2(0.5);
        float radius = length(offset) * 2.0;
        if (radius > 1.0 || vOpacity <= 0.001) discard;
        float core = 1.0 - smoothstep(0.72, 1.0, radius);
        float focusRing = vTopicFocus * smoothstep(0.64, 0.78, radius) * (1.0 - smoothstep(0.86, 0.98, radius));
        float timeRing = vTimeFocus * smoothstep(0.54, 0.68, radius) * (1.0 - smoothstep(0.88, 0.98, radius));
        float selectedRing = vSelected * smoothstep(0.46, 0.60, radius) * (1.0 - smoothstep(0.78, 0.94, radius));
        float selectedCore = vSelected * (1.0 - smoothstep(0.16, 0.38, radius));
        float opacity = max(core, max(timeRing, max(focusRing, max(selectedRing, selectedCore))));
        vec3 color = mix(vColor, vec3(1.0, 0.73, 0.12), focusRing * 0.88);
        color = mix(color, vec3(0.40, 0.94, 1.0), timeRing * (1.0 - vSelected));
        color = mix(color, vec3(1.0, 0.73, 0.12), selectedRing);
        color = mix(color, vec3(1.0), selectedCore);
        color = mix(color, vec3(0.48, 0.95, 1.0), vHydrationGlint * 0.42);
        gl_FragColor = vec4(color, opacity * vOpacity * uAlpha);
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.NormalBlending,
    toneMapped: false,
  });
}

function createPickMaterial(THREE) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uWindowStartDay: { value: -1_000_000_000 },
      uWindowEndDay: { value: 1_000_000_000 },
      uPixelRatio: { value: 1 },
      uRevealProgress: { value: 1 },
    },
    vertexShader: `
      uniform float uWindowStartDay;
      uniform float uWindowEndDay;
      uniform float uPixelRatio;
      uniform float uRevealProgress;
      attribute float aPickable;
      attribute float aRevealOrder;
      attribute vec3 aPickColor;
      attribute float aStartDay;
      attribute float aEndDay;
      varying float vOpacity;
      varying vec3 vPickColor;
      void main() {
        float eventActive = step(aStartDay, uWindowEndDay) * step(uWindowStartDay, aEndDay);
        float revealed = step(aRevealOrder, uRevealProgress);
        vOpacity = aPickable * eventActive * revealed;
        vPickColor = aPickColor;
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        // 拾取半径以 CSS 像素计，不能因高 DPR 屏幕缩小到难以点中。
        gl_PointSize = 12.0 * uPixelRatio;
        gl_Position = projectionMatrix * mvPosition;
      }
    `,
    fragmentShader: `
      varying float vOpacity;
      varying vec3 vPickColor;
      void main() {
        if (vOpacity < 0.35 || distance(gl_PointCoord, vec2(0.5)) > 0.5) discard;
        gl_FragColor = vec4(vPickColor, 1.0);
      }
    `,
    depthWrite: true,
    depthTest: true,
    blending: THREE.NoBlending,
    toneMapped: false,
  });
}

/**
 * 远场坐标穹幕跟随镜头位置但保持世界朝向，因此平移和缩放不会露底，
 * 旋转时仍能看到稳定的空间方向变化，而不是贴在屏幕上的二维背景。
 */
function createEventMapHorizonGrid(THREE) {
  const geometry = new THREE.SphereGeometry(HORIZON_GRID_RADIUS, 64, 32);
  const material = new THREE.ShaderMaterial({
    uniforms: {
      uBaseColor: { value: new THREE.Color(0x0ea5e9) },
      uMajorColor: { value: new THREE.Color(0x6366f1) },
      uOpacity: { value: 0.16 },
      uHydrationActive: { value: 0 },
      uHydrationTime: { value: 0 },
    },
    vertexShader: `
      varying vec3 vDirection;
      void main() {
        vDirection = normalize(position);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3 uBaseColor;
      uniform vec3 uMajorColor;
      uniform float uOpacity;
      uniform float uHydrationActive;
      uniform float uHydrationTime;
      varying vec3 vDirection;
      const float PI = 3.141592653589793;

      float gridLine(float coordinate, float widthScale) {
        float distanceToLine = abs(fract(coordinate - 0.5) - 0.5);
        float pixelWidth = max(fwidth(coordinate), 0.0002) * widthScale;
        return 1.0 - smoothstep(pixelWidth * 0.55, pixelWidth * 1.55, distanceToLine);
      }

      void main() {
        vec3 direction = normalize(vDirection);
        float longitude = atan(direction.z, direction.x);
        float latitude = asin(clamp(direction.y, -1.0, 1.0));
        float longitudeCoordinate = longitude / (PI / 12.0);
        float latitudeCoordinate = latitude / (PI / 12.0);
        float minor = max(gridLine(longitudeCoordinate, 0.82), gridLine(latitudeCoordinate, 0.82));
        float major = max(gridLine(longitudeCoordinate / 4.0, 1.05), gridLine(latitudeCoordinate / 4.0, 1.05));
        float poleStability = mix(0.72, 1.0, smoothstep(0.02, 0.26, 1.0 - abs(direction.y)));
        float strength = max(minor * 0.42, major * 0.92) * poleStability;
        float sweepCoordinate = fract(longitude / (2.0 * PI) - uHydrationTime * 0.055);
        float sweepDistance = min(sweepCoordinate, 1.0 - sweepCoordinate);
        float hydrationSweep = uHydrationActive * (1.0 - smoothstep(0.015, 0.115, sweepDistance));
        strength *= 1.0 + hydrationSweep * 0.68;
        if (strength <= 0.002) discard;
        vec3 color = mix(uBaseColor, uMajorColor, major * 0.34 + (direction.y * 0.5 + 0.5) * 0.12);
        color = mix(color, vec3(0.40, 0.94, 1.0), hydrationSweep * 0.42);
        gl_FragColor = vec4(color, uOpacity * strength);
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: false,
    side: THREE.BackSide,
    blending: THREE.NormalBlending,
    toneMapped: false,
  });
  const grid = new THREE.Mesh(geometry, material);
  grid.frustumCulled = false;
  grid.renderOrder = -30;
  return grid;
}

function topicIndexOf(topic, fallback) {
  const value = Number(topic?.topic_index);
  return Number.isInteger(value) && value >= 0 ? value : fallback;
}

export class EventMapController {
  static async create(options) {
    const modules = await loadThreeModule();
    try {
      // WebGLRenderer 本身就是最终能力检查；预先创建并主动丢失一个 WebGL2
      // 上下文会让部分驱动重复冷启动，反而延迟真实网格首帧。
      return new EventMapController(options, modules);
    } catch (error) {
      if (/webgl/i.test(String(error?.message || error))) {
        throw new Error("当前浏览器不支持 WebGL2，无法显示三维事件星图。", { cause: error });
      }
      throw error;
    }
  }

  constructor(
    {
      target,
      scene,
      manifest = {},
      onSelect = null,
      onTopic = null,
      onViewport = null,
      resolveVideoSource = null,
      wheelMode = "auto",
    },
    modules
  ) {
    if (!target || !scene || !modules?.THREE) throw new Error("三维事件星图初始化参数不完整");
    this.target = target;
    this.sceneData = scene;
    this.manifest = manifest;
    this.onSelect = onSelect;
    this.onTopic = onTopic;
    this.onViewport = onViewport;
    this.resolveVideoSource = resolveVideoSource;
    this.wheelMode = wheelMode;
    this.modules = modules;
    this.THREE = modules.THREE;
    this.destroyed = false;
    this.selectedIndex = null;
    this.selectedDetail = null;
    this.topicFocusIndex = null;
    this.currentLevel = "overview";
    this.initializePointState(scene);
    this.topicCounts = new Map();
    this.topicFamilyCounts = new Map();
    this.layerOptions = {
      windowStart: "",
      windowEnd: "",
      typeFilter: "",
      entityIndices: new Set(),
      playing: false,
      topicIndex: null,
    };
    this.transitionStartedAt = 0;
    this.semanticMembraneTransitionStartedAt = 0;
    this.semanticMembraneDensityKey = "";
    this.committedWindowStartDay = -1_000_000_000;
    this.committedWindowEndDay = 1_000_000_000;
    this.hasCommittedWindow = false;
    this.previewWindowStartDay = null;
    this.previewWindowEndDay = null;
    this.animationFrame = null;
    this.firstFrameRendered = false;
    this.firstFramePromise = new Promise((resolve) => { this.resolveFirstFrame = resolve; });
    this.progressiveRevealPrepared = false;
    this.progressiveRevealActive = false;
    this.progressiveRevealStartedAt = 0;
    this.progressiveRevealFrom = 0;
    this.progressiveRevealTarget = 1;
    this.progressiveRevealDuration = PROGRESSIVE_REVEAL_MS;
    this.progressiveRevealCompletes = true;
    this.progressiveRevealComplete = true;
    this.hydrationAnimationActive = false;
    this.hydrationAnimationStartedAt = 0;
    this.introStartedAt = 0;
    this.introActive = false;
    this.pointerDown = null;
    this.frameSamples = [];
    this.reducedQuality = false;
    this.pixelRatioLimit = scene.count > 100_000 ? 1.25 : 1.5;
    this.sceneInteractive = true;
    this.deferMembraneDensity = false;
    this.topics = Array.isArray(manifest.topics) ? manifest.topics : [];
    this.membraneTopics = Array.isArray(manifest.topic_geometry)
      ? manifest.topic_geometry
      : this.topics;
    this.labelMetadataReady = Array.isArray(manifest.topics);
    this.topicByIndex = new Map(this.topics.map((topic, index) => [topicIndexOf(topic, index), topic]));
    this.semanticPalette = eventMapSemanticPalette(manifest);
    this.setupDom();
    this.setupScene();
    this.setupInteractions();
    this.resize();
    // 加载动画只交给真实点和标签；镜头从第一帧起保持稳定，避免网格看起来被替换。
    this.resetCamera({ animate: false });
    this.updateLabels();
    this.invalidate();
  }

  initializePointState(scene) {
    this.sceneData = scene;
    this.activeMask = new Uint8Array(scene.count);
    this.pointVisibility = new Float32Array(scene.count);
    this.pointVisibility.fill(1);
    this.renderOpacity = new Float32Array(scene.count);
    this.renderOpacity.fill(1);
    this.topicFocusMask = new Float32Array(scene.count);
    this.timeFocusMask = new Float32Array(scene.count);
    this.selectedMask = new Float32Array(scene.count);
    this.entityMask = new Uint8Array(scene.count);
  }

  setupDom() {
    this.target.replaceChildren();
    this.target.style.position = "absolute";
    this.target.style.inset = "0";
    this.target.style.overflow = "hidden";
    this.target.style.touchAction = "none";
    this.labelsRoot = document.createElement("div");
    this.labelsRoot.className = "event-map-label-layer absolute inset-0 z-10 overflow-hidden pointer-events-none";
    this.labelsRoot.setAttribute("aria-hidden", "false");
    this.target.appendChild(this.labelsRoot);
    this.mediaRoot = document.createElement("div");
    this.mediaRoot.className = "event-map-media-layer absolute inset-0 z-20 overflow-hidden pointer-events-none";
    this.mediaRoot.setAttribute("aria-label", "24H与本周事件视频");
    this.mediaConnectors = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    this.mediaConnectors.classList.add("event-map-media-connectors");
    this.mediaConnectors.setAttribute("aria-hidden", "true");
    this.mediaCardsRoot = document.createElement("div");
    this.mediaCardsRoot.className = "event-map-media-cards absolute inset-0 pointer-events-none";
    this.mediaRoot.append(this.mediaConnectors, this.mediaCardsRoot);
    this.target.appendChild(this.mediaRoot);
    this.timeHighlightData = { point_indices: [], items: [] };
    this.mediaCardEntries = [];
    this.expandedMediaItemKey = "";
    this.expandedMediaTrigger = "";
    this.expandedMediaLayout = null;
    this.focusedMediaItemKey = "";
    this.mediaCardHoverTimer = null;
    this.mediaCardLeaveTimer = null;
    this.mediaCardsInteracting = false;
    this.labelMeasure = document.createElement("span");
    this.labelMeasure.className = "absolute invisible whitespace-nowrap text-[10px] font-medium tracking-wide";
    this.labelMeasure.setAttribute("aria-hidden", "true");
    this.labelMeasure.style.left = "-10000px";
    this.labelMeasure.style.top = "-10000px";
    this.target.appendChild(this.labelMeasure);
    this.errorOverlay = document.createElement("div");
    this.errorOverlay.className = "pointer-events-none absolute inset-0 z-40 hidden items-center justify-center bg-slate-950/90 px-8 text-center text-sm text-rose-200";
    this.target.appendChild(this.errorOverlay);
  }

  createPointGeometry() {
    const { THREE } = this;
    const positions = new Float32Array(this.sceneData.count * 3);
    const colors = new Float32Array(this.sceneData.count * 3);
    const semanticColors = new Float32Array(this.sceneData.count * 3);
    const pickColors = new Float32Array(this.sceneData.count * 3);
    const revealOrder = new Uint16Array(this.sceneData.count);
    for (let index = 0; index < this.sceneData.count; index += 1) {
      const offset = index * 3;
      positions[offset] = (this.sceneData.x[index] - this.rawCenter.x) * this.worldScale;
      positions[offset + 1] = (this.sceneData.y[index] - this.rawCenter.y) * this.worldScale;
      positions[offset + 2] = (this.sceneData.z[index] - this.rawCenter.z) * this.worldScale;
      const semantic = this.semanticPalette.byTypeCode[this.sceneData.eventType[index]]
        || this.semanticPalette.fallback;
      colors[offset] = semantic.rgb[0];
      colors[offset + 1] = semantic.rgb[1];
      colors[offset + 2] = semantic.rgb[2];
      semanticColors[offset] = semantic.rgb[0];
      semanticColors[offset + 1] = semantic.rgb[1];
      semanticColors[offset + 2] = semantic.rgb[2];
      const encoded = index + 1;
      pickColors[offset] = (encoded & 255) / 255;
      pickColors[offset + 1] = ((encoded >> 8) & 255) / 255;
      pickColors[offset + 2] = ((encoded >> 16) & 255) / 255;
      revealOrder[index] = eventMapRevealOrder(index);
    }
    this.worldPositions = positions;
    this.colors = colors;
    this.semanticColors = semanticColors;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("aColor", new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute("aOpacity", new THREE.BufferAttribute(this.renderOpacity, 1));
    geometry.setAttribute("aPickable", new THREE.BufferAttribute(this.pointVisibility, 1));
    geometry.setAttribute("aTopicFocus", new THREE.BufferAttribute(this.topicFocusMask, 1));
    geometry.setAttribute("aTimeFocus", new THREE.BufferAttribute(this.timeFocusMask, 1));
    geometry.setAttribute("aSelected", new THREE.BufferAttribute(this.selectedMask, 1));
    geometry.setAttribute("aStartDay", new THREE.BufferAttribute(new Float32Array(this.sceneData.startDay), 1));
    geometry.setAttribute("aEndDay", new THREE.BufferAttribute(new Float32Array(this.sceneData.endDay), 1));
    geometry.setAttribute("aPickColor", new THREE.BufferAttribute(pickColors, 3));
    geometry.setAttribute("aRevealOrder", new THREE.BufferAttribute(revealOrder, 1, true));
    if (this.sceneData.count) geometry.computeBoundingSphere();
    return geometry;
  }

  setupScene() {
    const { THREE, OrbitControls } = this.modules;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x01040b);
    this.renderer = new THREE.WebGLRenderer({ antialias: false, alpha: false, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(this.pixelRatioLimit, Number(globalThis.devicePixelRatio || 1)));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.9;
    this.renderer.domElement.className = "absolute inset-0 h-full w-full";
    this.renderer.domElement.style.touchAction = "none";
    this.target.insertBefore(this.renderer.domElement, this.labelsRoot);

    const { xBounds, yBounds, zBounds } = eventMapSceneBounds(this.manifest, this.sceneData);
    this.rawBounds = { xBounds, yBounds, zBounds };
    this.rawCenter = {
      x: (xBounds.minimum + xBounds.maximum) / 2,
      y: (yBounds.minimum + yBounds.maximum) / 2,
      z: (zBounds.minimum + zBounds.maximum) / 2,
    };
    const extent = Math.max(
      xBounds.maximum - xBounds.minimum,
      yBounds.maximum - yBounds.minimum,
      zBounds.maximum - zBounds.minimum,
      1e-6
    );
    this.worldScale = 13 / extent;
    this.geometry = this.createPointGeometry();

    this.coreMaterial = createParticleMaterial(THREE, { size: 3.0, alpha: 0.94 });
    this.corePoints = new THREE.Points(this.geometry, this.coreMaterial);
    this.corePoints.frustumCulled = false;
    this.scene.add(this.corePoints);
    this.setupSemanticMembrane(this.rawBounds);

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.02, 160);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;
    this.controls.minDistance = 1.3;
    this.controls.maxDistance = 70;
    this.controls.target.set(0, 0, 0);
    this.horizonGrid = createEventMapHorizonGrid(THREE);
    this.scene.add(this.horizonGrid);

    this.pickScene = new THREE.Scene();
    this.pickMaterial = createPickMaterial(THREE);
    this.pickPoints = new THREE.Points(this.geometry, this.pickMaterial);
    this.pickPoints.frustumCulled = false;
    this.pickScene.add(this.pickPoints);
    this.pickTarget = new THREE.WebGLRenderTarget(1, 1, {
      minFilter: THREE.NearestFilter,
      magFilter: THREE.NearestFilter,
      type: THREE.UnsignedByteType,
      depthBuffer: true,
      stencilBuffer: false,
    });
    this.storyGroup = new THREE.Group();
    this.storyPath = null;
    this.storyVisibleEdgeCount = 0;
    this.storyAnimationStartedAt = 0;
    this.scene.add(this.storyGroup);
    this.homeDistance = 21;
  }

  setupSemanticMembrane({ xBounds, yBounds, zBounds }) {
    const { THREE } = this;
    const homeNormal = new THREE.Vector3(13.5, 10.5, 15.5).normalize();
    const tiltAxis = new THREE.Vector3(0, 1, 0).cross(homeNormal).normalize();
    const normal = homeNormal.clone().applyAxisAngle(tiltAxis, -0.3);
    const right = new THREE.Vector3(0, 1, 0).cross(normal).normalize();
    const gridUp = normal.clone().cross(right).normalize();
    const worldBounds = {
      minimum: this.rawToWorld(xBounds.minimum, yBounds.minimum, zBounds.minimum),
      maximum: this.rawToWorld(xBounds.maximum, yBounds.maximum, zBounds.maximum),
    };
    const corners = [];
    for (const x of [worldBounds.minimum.x, worldBounds.maximum.x]) {
      for (const y of [worldBounds.minimum.y, worldBounds.maximum.y]) {
        for (const z of [worldBounds.minimum.z, worldBounds.maximum.z]) {
          corners.push(new THREE.Vector3(x, y, z));
        }
      }
    }
    const macroTopics = this.membraneTopics
      .map((topic, index) => ({ topic, index: topicIndexOf(topic, index) }))
      .filter(({ topic }) => Number(topic?.level || 0) === 0)
      .filter(({ topic }) => [topic?.center_x, topic?.center_y, topic?.center_z].every(Number.isFinite))
      .sort((left, right) => Number(right.topic?.canonical_count || 0) - Number(left.topic?.canonical_count || 0))
      .slice(0, 9);
    const halfSpan = Math.max(...corners.flatMap((corner) => [
      Math.abs(corner.dot(right)),
      Math.abs(corner.dot(gridUp)),
    ]));
    this.semanticMembraneConfig = {
      extent: Math.max(15, halfSpan * 2.1),
      macroTopics,
      right,
      gridUp,
      canonicalMaximum: Math.max(1, ...macroTopics.map(({ topic }) => Number(topic?.canonical_count || 0))),
    };
    const { gridData, densityKey } = this.semanticMembraneGridData({ useActiveCounts: false });
    const planarPositions = new Float32Array(gridData.positions);
    const depths = new Float32Array(planarPositions.length / 3);
    for (let vertex = 0; vertex < depths.length; vertex += 1) {
      depths[vertex] = planarPositions[vertex * 3 + 2];
      planarPositions[vertex * 3 + 2] = 0;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(planarPositions, 3));
    geometry.setAttribute("aFade", new THREE.BufferAttribute(gridData.fades, 1));
    geometry.setAttribute("aPreviousDepth", new THREE.BufferAttribute(new Float32Array(depths), 1));
    geometry.setAttribute("aCurrentDepth", new THREE.BufferAttribute(new Float32Array(depths), 1));
    geometry.setAttribute("aPreviousCurvature", new THREE.BufferAttribute(new Float32Array(gridData.curvatures), 1));
    geometry.setAttribute("aCurrentCurvature", new THREE.BufferAttribute(new Float32Array(gridData.curvatures), 1));
    const material = new THREE.ShaderMaterial({
      uniforms: {
        uBaseColor: { value: new THREE.Color(0x38bdf8) },
        uWarpColor: { value: new THREE.Color(0x8b5cf6) },
        uOpacity: { value: 0.24 },
        uFieldMix: { value: 1 },
      },
      vertexShader: `
        attribute float aFade;
        attribute float aPreviousDepth;
        attribute float aCurrentDepth;
        attribute float aPreviousCurvature;
        attribute float aCurrentCurvature;
        uniform float uFieldMix;
        varying float vFade;
        varying float vCurvature;
        void main() {
          vFade = aFade;
          float fieldMix = clamp(uFieldMix, 0.0, 1.0);
          vCurvature = mix(aPreviousCurvature, aCurrentCurvature, fieldMix);
          vec3 membranePosition = position;
          membranePosition.z = mix(aPreviousDepth, aCurrentDepth, fieldMix);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(membranePosition, 1.0);
        }
      `,
      fragmentShader: `
        uniform vec3 uBaseColor;
        uniform vec3 uWarpColor;
        uniform float uOpacity;
        varying float vFade;
        varying float vCurvature;
        void main() {
          if (vFade <= 0.001) discard;
          vec3 color = mix(uBaseColor, uWarpColor, smoothstep(0.08, 1.0, vCurvature) * 0.58);
          gl_FragColor = vec4(color, uOpacity * vFade);
        }
      `,
      transparent: true,
      depthWrite: false,
      depthTest: false,
      blending: THREE.NormalBlending,
      toneMapped: false,
    });
    this.semanticMembrane = new THREE.LineSegments(geometry, material);
    const basis = new THREE.Matrix4().makeBasis(right, gridUp, normal);
    const minimumProjection = Math.min(...corners.map((corner) => corner.dot(normal)));
    this.semanticMembrane.quaternion.setFromRotationMatrix(basis);
    this.semanticMembrane.position.copy(normal).multiplyScalar(minimumProjection - 2.6);
    this.semanticMembrane.frustumCulled = false;
    this.semanticMembrane.renderOrder = -20;
    this.semanticMembraneDensityKey = densityKey;
    this.scene.add(this.semanticMembrane);
  }

  semanticMembraneGridData({ useActiveCounts = true } = {}) {
    const config = this.semanticMembraneConfig;
    const densities = config.macroTopics.map(({ topic, index }) => (
      useActiveCounts
        ? Math.max(0, Number(this.topicCounts.get(index) || 0))
        : Math.max(0, Number(topic?.canonical_count || 0))
    ));
    const currentMaximum = Math.max(0, ...densities);
    const activityScale = useActiveCounts
      ? Math.max(0.34, Math.min(1, Math.sqrt(currentMaximum / config.canonicalMaximum)))
      : 1;
    const wells = [
      { x: 0, y: 0, strength: 0.18 + 0.12 * activityScale, radius: 6.5 },
      ...config.macroTopics.flatMap(({ topic }, position) => {
        const density = densities[position];
        if (density <= 0) return [];
        const center = this.rawToWorld(topic.center_x, topic.center_y, topic.center_z);
        const relativeWeight = Math.sqrt(density / Math.max(1, currentMaximum));
        return [{
          x: center.dot(config.right),
          y: center.dot(config.gridUp),
          strength: (0.38 + 0.84 * relativeWeight) * activityScale,
          radius: 1.45 + 1.12 * relativeWeight,
        }];
      }),
    ];
    return {
      densityKey: `${useActiveCounts ? "window" : "canonical"}:${densities.join(":")}`,
      gridData: buildEventMapSpacetimeGrid({ extent: config.extent, wells }),
    };
  }

  updateSemanticMembraneDensity({ animate = true } = {}) {
    if (!this.semanticMembrane?.geometry || !this.semanticMembraneConfig) return;
    if (this.deferMembraneDensity) return;
    const { gridData, densityKey } = this.semanticMembraneGridData({ useActiveCounts: this.hasCommittedWindow });
    if (densityKey === this.semanticMembraneDensityKey) return;
    const geometry = this.semanticMembrane.geometry;
    const previousDepth = geometry.getAttribute("aPreviousDepth");
    const currentDepth = geometry.getAttribute("aCurrentDepth");
    const previousCurvature = geometry.getAttribute("aPreviousCurvature");
    const currentCurvature = geometry.getAttribute("aCurrentCurvature");
    const fieldMix = Number(this.semanticMembrane.material.uniforms.uFieldMix.value || 0);
    for (let vertex = 0; vertex < currentDepth.count; vertex += 1) {
      previousDepth.array[vertex] += (currentDepth.array[vertex] - previousDepth.array[vertex]) * fieldMix;
      currentDepth.array[vertex] = gridData.positions[vertex * 3 + 2];
      previousCurvature.array[vertex] += (
        currentCurvature.array[vertex] - previousCurvature.array[vertex]
      ) * fieldMix;
      currentCurvature.array[vertex] = gridData.curvatures[vertex];
    }
    previousDepth.needsUpdate = true;
    currentDepth.needsUpdate = true;
    previousCurvature.needsUpdate = true;
    currentCurvature.needsUpdate = true;
    this.semanticMembraneDensityKey = densityKey;
    this.semanticMembrane.material.uniforms.uFieldMix.value = animate ? 0 : 1;
    this.semanticMembraneTransitionStartedAt = animate ? performance.now() : 0;
    this.invalidate();
  }

  updateSemanticMembraneTransition(now) {
    if (!this.semanticMembraneTransitionStartedAt || !this.semanticMembrane?.material) return false;
    const progress = Math.min(1, Math.max(0, (
      now - this.semanticMembraneTransitionStartedAt
    ) / SEMANTIC_MEMBRANE_TRANSITION_MS));
    const eased = progress * progress * (3 - 2 * progress);
    this.semanticMembrane.material.uniforms.uFieldMix.value = eased;
    if (progress >= 1) this.semanticMembraneTransitionStartedAt = 0;
    return progress < 1;
  }

  updateHorizonGrid() {
    if (!this.horizonGrid || !this.camera || !this.controls) return;
    this.horizonGrid.position.copy(this.camera.position);
    const closeDetail = Math.max(0, Math.min(1, (this.semanticZoom() - 1) / 4));
    this.horizonGrid.material.uniforms.uOpacity.value = 0.16 - closeDetail * 0.055;
  }

  setupInteractions() {
    this.gestureScale = null;
    this.lastWheelAt = -Infinity;
    this.wheelInputType = "discrete";
    this.handleControlsStart = () => {
      this.introActive = false;
      this.mediaCardsInteracting = true;
      this.collapseMediaCard();
      this.invalidate();
    };
    this.handleControlsChange = () => {
      this.updateLabels();
      this.updateSemanticLevel();
      this.invalidate();
    };
    this.handleControlsEnd = () => {
      this.mediaCardsInteracting = false;
      this.updateMediaCards();
      this.invalidate();
    };
    this.handlePointerDown = (event) => {
      this.pointerDown = { x: event.clientX, y: event.clientY, button: event.button };
    };
    this.handlePointerUp = (event) => {
      const start = this.pointerDown;
      this.pointerDown = null;
      if (!start || start.button !== 0 || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5) return;
      const index = this.pick(event.clientX, event.clientY);
      if (index === null) return;
      // 选择与镜头定位是两件事：直接点星点只在原位打开详情，绝不重置用户视角。
      this.onSelect?.(index, this.sceneData.canonicalIds[index]);
    };
    this.handleDoubleClick = (event) => {
      if (this.pick(event.clientX, event.clientY) === null) this.resetCamera();
    };
    this.handleContextLost = (event) => {
      event.preventDefault();
      this.showError("WebGL 上下文已丢失，正在等待浏览器恢复。");
    };
    this.handleContextRestored = () => {
      this.errorOverlay.classList.add("hidden");
      this.errorOverlay.classList.remove("flex");
      this.invalidate();
    };
    // WheelEvent 不暴露设备类型：连续的小步进按触控板滑动处理，离散滚轮保留缩放。
    this.handleWheel = (event) => {
      if (this.gestureScale !== null) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const recent = event.timeStamp - this.lastWheelAt < 180;
      const discreteStep = event.deltaMode !== 0 || (event.deltaX === 0 && Math.abs(event.deltaY) >= 100);
      const continuous = !event.ctrlKey && this.wheelMode === "auto" && !discreteStep && (
        event.deltaX !== 0 || Math.abs(event.deltaY) < 60 || (recent && this.wheelInputType === "continuous")
      );
      if (!event.ctrlKey) {
        this.lastWheelAt = event.timeStamp;
        this.wheelInputType = continuous ? "continuous" : "discrete";
      }
      if (!continuous && event.target === this.renderer.domElement) return;
      event.preventDefault();
      event.stopPropagation();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 100 : 1;
      this.handleControlsStart();
      if (!continuous) {
        const scale = Math.pow(0.95, -this.controls.zoomSpeed * event.deltaY * unit * (event.ctrlKey ? 0.1 : 0.01));
        this.controls.dollyIn(scale);
      } else {
        const radiansPerPixel = 2 * Math.PI / this.renderer.domElement.clientHeight;
        if (event.deltaX) this.controls.rotateLeft(-event.deltaX * unit * radiansPerPixel);
        if (event.deltaY) this.controls.rotateUp(-event.deltaY * unit * radiansPerPixel);
      }
      this.handleControlsEnd();
    };
    this.handleGestureStart = (event) => {
      event.preventDefault();
      this.gestureScale = event.scale;
      this.handleControlsStart();
    };
    this.handleGestureChange = (event) => {
      event.preventDefault();
      const scale = event.scale / this.gestureScale;
      this.gestureScale = event.scale;
      this.controls.dollyIn(1 / scale);
    };
    this.handleGestureEnd = (event) => {
      event.preventDefault();
      this.gestureScale = null;
      this.handleControlsEnd();
    };
    this.controls.addEventListener("start", this.handleControlsStart);
    this.controls.addEventListener("change", this.handleControlsChange);
    this.controls.addEventListener("end", this.handleControlsEnd);
    this.target.addEventListener("wheel", this.handleWheel, { capture: true, passive: false });
    this.target.addEventListener("gesturestart", this.handleGestureStart, { passive: false });
    this.target.addEventListener("gesturechange", this.handleGestureChange, { passive: false });
    this.target.addEventListener("gestureend", this.handleGestureEnd, { passive: false });
    this.renderer.domElement.addEventListener("pointerdown", this.handlePointerDown);
    this.renderer.domElement.addEventListener("pointerup", this.handlePointerUp);
    this.renderer.domElement.addEventListener("dblclick", this.handleDoubleClick);
    this.renderer.domElement.addEventListener("webglcontextlost", this.handleContextLost);
    this.renderer.domElement.addEventListener("webglcontextrestored", this.handleContextRestored);
    this.resizeObserver = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => this.resize()) : null;
    this.resizeObserver?.observe(this.target);
  }

  setWheelMode(mode) {
    this.wheelMode = mode;
    this.lastWheelAt = -Infinity;
    this.wheelInputType = "discrete";
  }

  applyTimeHighlightMask() {
    this.timeFocusMask.fill(0);
    for (const rawIndex of this.timeHighlightData?.point_indices || []) {
      const index = Number(rawIndex);
      if (Number.isInteger(index) && index >= 0 && index < this.sceneData.count) this.timeFocusMask[index] = 1;
    }
    const attribute = this.geometry?.getAttribute?.("aTimeFocus");
    if (attribute) attribute.needsUpdate = true;
  }

  setTimeHighlights(payload = {}) {
    this.collapseMediaCard();
    this.timeHighlightData = {
      point_indices: Array.isArray(payload?.point_indices) ? payload.point_indices : [],
      items: Array.isArray(payload?.items) ? payload.items.slice(0, EVENT_MAP_MEDIA_CARD_LIMIT) : [],
      scope: payload?.scope === "week" ? "week" : "today",
    };
    this.applyTimeHighlightMask();
    this.rebuildMediaCards();
    this.updateMediaCards();
    this.invalidate();
  }

  rebuildMediaCards() {
    if (!this.mediaRoot || !this.mediaConnectors || !this.mediaCardsRoot) return;
    const previousPlacements = new Map((this.mediaCardEntries || []).map((entry) => [entry.key, entry.placement]));
    this.mediaConnectors.replaceChildren();
    this.mediaCardsRoot.replaceChildren();
    this.mediaCardEntries = [];
    for (const [position, item] of (this.timeHighlightData?.items || []).entries()) {
      const pointIndices = [...new Set([
        item?.point_index,
        ...(Array.isArray(item?.point_indices) ? item.point_indices : []),
      ].map(Number).filter((pointIndex) => (
        Number.isInteger(pointIndex) && pointIndex >= 0 && pointIndex < this.sceneData.count
      )))];
      if (!pointIndices.length) continue;
      const key = eventMapMediaItemKey(item, `item-${position}`);
      const connectorGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
      connectorGroup.classList.add("event-map-video-connector");
      const connectors = pointIndices.map((pointIndex) => {
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        const anchor = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        anchor.setAttribute("r", "3");
        connectorGroup.append(path, anchor);
        return { pointIndex, path, anchor };
      });
      this.mediaConnectors.appendChild(connectorGroup);

      const card = document.createElement("article");
      card.className = "event-map-video-card pointer-events-auto";
      const canonicalIds = Array.isArray(item?.canonical_ids)
        ? item.canonical_ids.map(String).filter(Boolean)
        : [String(item?.canonical_id || "")].filter(Boolean);
      card.dataset.canonicalId = canonicalIds[0] || "";
      card.dataset.canonicalIds = canonicalIds.join(",");
      card.dataset.videoId = String(item?.primary_video?.video_id || item?.video_id || "");
      card.tabIndex = 0;
      card.setAttribute("role", "group");
      const linkedEventCount = Math.max(1, Number(item?.linked_event_count || item?.linked_events?.length || 1));
      card.setAttribute(
        "aria-label",
        `${linkedEventCount}个事件共用视频${String(item?.primary_video?.title || "")}; 代表事件${String(item?.title || "")}`,
      );

      const preview = document.createElement("button");
      preview.type = "button";
      preview.className = "event-map-video-card__preview";
      preview.title = "悬停静音预览；点击固定并播放";
      const posterUrl = String(item?.primary_video?.poster_url || item?.primary_video?.thumbnail_url || "");
      if (posterUrl) {
        const image = document.createElement("img");
        image.src = posterUrl;
        image.alt = "";
        image.loading = "lazy";
        image.className = "event-map-video-card__poster";
        preview.appendChild(image);
      }
      const play = document.createElement("span");
      play.className = "event-map-video-card__play";
      play.textContent = "▶";
      const rank = document.createElement("span");
      rank.className = "event-map-video-card__rank";
      rank.textContent = String(Number(item?.representative_rank || position + 1));
      preview.append(play, rank);
      card.appendChild(preview);

      const copy = document.createElement("div");
      copy.className = "event-map-video-card__copy";
      const scope = document.createElement("span");
      scope.className = "event-map-video-card__scope";
      const scopeLabel = this.timeHighlightData.scope === "week" ? "本周事件" : "24H事件";
      const representativeRank = Number(item?.representative_rank || position + 1);
      const mediaName = String(item?.primary_video?.media_name || "未知来源");
      const eventDate = eventMapEventDateLabel(item);
      const eventScope = linkedEventCount > 1
        ? ` · ${linkedEventCount}个事件`
        : (eventDate ? ` · ${eventDate}` : "");
      scope.textContent = `${scopeLabel}${eventScope} · #${representativeRank}`;
      scope.title = "按事件证据强度排序：跨媒体、跨视频佐证优先；不代表市场影响大小";
      const title = document.createElement("div");
      title.className = "event-map-video-card__title";
      title.textContent = String(item?.title || "未命名事件");
      const linkedEventTitles = (Array.isArray(item?.linked_events) ? item.linked_events : [])
        .map((eventItem) => String(eventItem?.title || "").trim())
        .filter(Boolean);
      title.title = linkedEventTitles.length > 1
        ? linkedEventTitles.join("\n")
        : String(item?.summary || item?.title || "");
      const source = document.createElement("div");
      source.className = "event-map-video-card__source";
      source.title = `关联视频：${String(item?.primary_video?.title || "")} · 媒体：${mediaName}`;
      const mediaIcon = document.createElement("span");
      mediaIcon.className = "event-map-video-card__media-icon";
      const mediaAvatarUrl = String(item?.primary_video?.media_avatar_url || "");
      if (mediaAvatarUrl) {
        const image = document.createElement("img");
        image.src = mediaAvatarUrl;
        image.alt = "";
        image.loading = "lazy";
        mediaIcon.appendChild(image);
      } else {
        mediaIcon.textContent = mediaName.slice(0, 1) || "媒";
      }
      const sourceText = document.createElement("span");
      sourceText.className = "event-map-video-card__source-text";
      sourceText.textContent = `${mediaName} · ${String(item?.primary_video?.title || "未命名视频")}`;
      source.append(mediaIcon, sourceText);
      copy.append(scope, title, source);
      card.appendChild(copy);

      const stop = (event) => event.stopPropagation();
      card.addEventListener("pointerdown", stop);
      card.addEventListener("pointerup", stop);
      card.addEventListener("wheel", stop, { passive: true });
      card.addEventListener("pointerenter", () => {
        this.setMediaConnectorFocus(key);
        this.scheduleMediaCardHover(item, card, preview);
      });
      card.addEventListener("pointerleave", () => {
        this.setMediaConnectorFocus("");
        this.scheduleMediaCardHoverCollapse(item);
      });
      card.addEventListener("focusin", () => this.setMediaConnectorFocus(key));
      card.addEventListener("focusout", (event) => {
        if (!card.contains(event.relatedTarget)) this.setMediaConnectorFocus("");
      });
      preview.addEventListener("click", (event) => {
        if (event.target.closest("video")) return;
        event.preventDefault();
        event.stopPropagation();
        void this.toggleMediaCard(item, card, preview, { trigger: "click" });
      });
      card.addEventListener("click", (event) => {
        if (event.target.closest("button, video")) return;
        void this.toggleMediaCard(item, card, preview, { trigger: "click" });
      });
      card.addEventListener("keydown", (event) => {
        if (event.target !== card || !["Enter", " "].includes(event.key)) return;
        event.preventDefault();
        void this.toggleMediaCard(item, card, preview, { trigger: "click" });
      });
      this.mediaCardsRoot.appendChild(card);
      this.mediaCardEntries.push({
        key,
        item,
        pointIndex: pointIndices[0],
        pointIndices,
        card,
        preview,
        connectorGroup,
        connectors,
        placement: previousPlacements.get(key) || null,
      });
    }
  }

  supportsMediaCardHover() {
    return Boolean(globalThis.matchMedia?.("(hover: hover) and (pointer: fine)")?.matches);
  }

  clearMediaCardHoverTimers() {
    if (this.mediaCardHoverTimer !== null) clearTimeout(this.mediaCardHoverTimer);
    if (this.mediaCardLeaveTimer !== null) clearTimeout(this.mediaCardLeaveTimer);
    this.mediaCardHoverTimer = null;
    this.mediaCardLeaveTimer = null;
  }

  setMediaConnectorFocus(itemKey = "") {
    this.focusedMediaItemKey = String(itemKey || "");
    this.syncMediaConnectorFocus();
  }

  syncMediaConnectorFocus() {
    if (!this.mediaConnectors) return;
    const activeKey = this.focusedMediaItemKey || this.expandedMediaItemKey;
    this.mediaConnectors.classList.toggle("event-map-media-connectors--focused", Boolean(activeKey));
    for (const entry of this.mediaCardEntries || []) {
      entry.connectorGroup.classList.toggle("event-map-video-connector--focused", entry.key === activeKey);
    }
  }

  scheduleMediaCardHover(item, card, preview) {
    if (!this.supportsMediaCardHover() || this.mediaCardsInteracting || this.destroyed) return;
    if (this.mediaCardLeaveTimer !== null) clearTimeout(this.mediaCardLeaveTimer);
    this.mediaCardLeaveTimer = null;
    const itemKey = eventMapMediaItemKey(item);
    if (itemKey && itemKey === this.expandedMediaItemKey) return;
    if (this.mediaCardHoverTimer !== null) clearTimeout(this.mediaCardHoverTimer);
    this.mediaCardHoverTimer = setTimeout(() => {
      this.mediaCardHoverTimer = null;
      if (this.destroyed || this.mediaCardsInteracting || !card.isConnected) return;
      void this.toggleMediaCard(item, card, preview, { trigger: "hover" });
    }, EVENT_MAP_MEDIA_HOVER_OPEN_MS);
  }

  scheduleMediaCardHoverCollapse(item) {
    if (!this.supportsMediaCardHover()) return;
    if (this.mediaCardHoverTimer !== null) clearTimeout(this.mediaCardHoverTimer);
    this.mediaCardHoverTimer = null;
    const itemKey = eventMapMediaItemKey(item);
    if (!itemKey || itemKey !== this.expandedMediaItemKey || this.expandedMediaTrigger !== "hover") return;
    if (this.mediaCardLeaveTimer !== null) clearTimeout(this.mediaCardLeaveTimer);
    this.mediaCardLeaveTimer = setTimeout(() => {
      this.mediaCardLeaveTimer = null;
      if (this.expandedMediaItemKey === itemKey && this.expandedMediaTrigger === "hover") this.collapseMediaCard();
    }, EVENT_MAP_MEDIA_HOVER_CLOSE_MS);
  }

  captureMediaCardLayout(itemKey) {
    const cards = new Map();
    for (const entry of this.mediaCardEntries || []) {
      const left = Number.parseFloat(entry.card.style.left);
      const top = Number.parseFloat(entry.card.style.top);
      const mini = entry.card.classList.contains("event-map-video-card--mini");
      cards.set(entry.key, {
        visible: !entry.card.hidden && Number.isFinite(left) && Number.isFinite(top),
        left: Number.isFinite(left) ? left : 0,
        top: Number.isFinite(top) ? top : 0,
        width: entry.card.offsetWidth || (mini ? EVENT_MAP_MEDIA_MINI_WIDTH : EVENT_MAP_MEDIA_CARD_WIDTH),
        height: entry.card.offsetHeight || (mini ? EVENT_MAP_MEDIA_MINI_HEIGHT : EVENT_MAP_MEDIA_CARD_HEIGHT),
        side: entry.card.dataset.side === "left" ? "left" : "right",
        mini,
      });
    }
    return { itemKey, cards };
  }

  async toggleMediaCard(item, card, preview, { trigger = "click" } = {}) {
    const playbackTrigger = trigger === "hover" ? "hover" : "click";
    this.clearMediaCardHoverTimers();
    const itemKey = eventMapMediaItemKey(item);
    if (itemKey && itemKey === this.expandedMediaItemKey) {
      if (playbackTrigger === "hover") return;
      if (this.expandedMediaTrigger === "hover") {
        this.expandedMediaTrigger = "click";
        card.dataset.playbackTrigger = "click";
        const video = card.querySelector("video");
        if (video) {
          video.defaultMuted = false;
          video.muted = false;
          video.removeAttribute("muted");
          try { await video.play(); } catch { /* 保留原生播放控件供用户继续操作。 */ }
        }
        return;
      }
      this.collapseMediaCard();
      return;
    }
    this.collapseMediaCard();
    const currentEntry = this.mediaCardEntries.find((entry) => entry.key === itemKey);
    if (currentEntry) {
      card = currentEntry.card;
      preview = currentEntry.preview;
    }
    this.expandedMediaLayout = this.captureMediaCardLayout(itemKey);
    this.expandedMediaItemKey = itemKey;
    this.expandedMediaTrigger = playbackTrigger;
    card.classList.remove("event-map-video-card--mini");
    card.classList.add("event-map-video-card--expanded");
    card.dataset.playbackTrigger = playbackTrigger;
    card.dataset.loading = "true";
    this.updateMediaCards();
    let source = null;
    try {
      source = await this.resolveVideoSource?.(item?.primary_video || {});
    } catch {
      if (this.expandedMediaItemKey === itemKey && card.isConnected) {
        card.dataset.loading = "false";
        card.dataset.error = "true";
      }
      return;
    }
    if (this.destroyed || this.expandedMediaItemKey !== itemKey || !card.isConnected) return;
    card.dataset.loading = "false";
    if (!source?.url) {
      card.dataset.error = "true";
      return;
    }
    const video = document.createElement("video");
    video.className = "event-map-video-card__video";
    video.controls = true;
    video.playsInline = true;
    video.autoplay = true;
    video.preload = "metadata";
    // 视频地址可能在悬停后异步返回；期间若用户已点击固定，应采用最新交互状态。
    const hoverPreview = this.expandedMediaTrigger === "hover";
    video.defaultMuted = hoverPreview;
    video.muted = hoverPreview;
    if (hoverPreview) video.setAttribute("muted", "");
    video.src = source.url;
    if (source.poster) video.poster = source.poster;
    const playback = Number(item?.primary_video?.playback_position_seconds || 0);
    if (playback > 0) video.addEventListener("loadedmetadata", () => { video.currentTime = playback; }, { once: true });
    video.addEventListener("pointerdown", () => {
      if (this.expandedMediaItemKey !== itemKey || this.expandedMediaTrigger !== "hover") return;
      this.expandedMediaTrigger = "click";
      card.dataset.playbackTrigger = "click";
      video.defaultMuted = false;
      video.muted = false;
      video.removeAttribute("muted");
    });
    preview.replaceChildren(video);
    try { await video.play(); } catch { /* 浏览器可能要求再次点击播放。 */ }
  }

  collapseMediaCard() {
    this.clearMediaCardHoverTimers();
    this.focusedMediaItemKey = "";
    if (!this.expandedMediaItemKey && !(this.mediaRoot?.querySelector("video"))) {
      this.syncMediaConnectorFocus();
      return;
    }
    for (const entry of this.mediaCardEntries || []) {
      entry.card.classList.remove("event-map-video-card--expanded");
      entry.card.dataset.loading = "false";
      entry.card.dataset.error = "false";
      const video = entry.card.querySelector("video");
      if (video) {
        video.pause();
        video.removeAttribute("src");
        video.load();
      }
    }
    this.expandedMediaItemKey = "";
    this.expandedMediaTrigger = "";
    this.expandedMediaLayout = null;
    this.rebuildMediaCards();
    this.updateMediaCards();
  }

  updateMediaCards() {
    if (!this.camera || !this.target || !this.mediaCardEntries?.length) return;
    const width = Math.max(1, this.target.clientWidth || 1);
    const height = Math.max(1, this.target.clientHeight || 1);
    this.camera.updateMatrixWorld?.(true);
    this.mediaConnectors?.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const projectedEntries = [];
    for (const entry of this.mediaCardEntries) {
      const projectedAnchors = [];
      for (const connector of entry.connectors) {
        const pointIndex = connector.pointIndex;
        if (!this.activeMask[pointIndex] || !this.pointVisibility[pointIndex]) continue;
        const point = this.worldPoint(pointIndex);
        const projected = point?.clone().project(this.camera);
        if (!projected || projected.z < -1 || projected.z > 1 || Math.abs(projected.x) > 1 || Math.abs(projected.y) > 1) continue;
        projectedAnchors.push({
          connector,
          x: (projected.x * 0.5 + 0.5) * width,
          y: (-projected.y * 0.5 + 0.5) * height,
        });
      }
      if (!projectedAnchors.length) {
        entry.card.hidden = true;
        entry.connectorGroup.style.display = "none";
        continue;
      }
      entry.card.hidden = false;
      const x = projectedAnchors.reduce((sum, anchor) => sum + anchor.x, 0) / projectedAnchors.length;
      const y = projectedAnchors.reduce((sum, anchor) => sum + anchor.y, 0) / projectedAnchors.length;
      const expanded = entry.card.classList.contains("event-map-video-card--expanded");
      projectedEntries.push({
        entry,
        projectedAnchors,
        x,
        y,
        preferredPlacement: entry.placement,
        width: expanded ? (entry.card.offsetWidth || 400) : EVENT_MAP_MEDIA_CARD_WIDTH,
        height: expanded ? (entry.card.offsetHeight || 128) : EVENT_MAP_MEDIA_CARD_HEIGHT,
        miniWidth: EVENT_MAP_MEDIA_MINI_WIDTH,
        miniHeight: EVENT_MAP_MEDIA_MINI_HEIGHT,
        expanded,
      });
    }
    const frozenCards = this.expandedMediaLayout?.itemKey === this.expandedMediaItemKey
      ? this.expandedMediaLayout.cards
      : null;
    const layouts = frozenCards
      ? layoutFrozenEventMapMediaCards(projectedEntries, frozenCards, { width, height })
      : layoutEventMapMediaCards(projectedEntries, {
        width,
        height,
        preservePlacement: this.mediaCardsInteracting,
      });
    const visibleEntries = new Set(layouts.map((layout) => layout.entry));
    for (const entry of this.mediaCardEntries) {
      if (visibleEntries.has(entry)) continue;
      entry.card.hidden = true;
      entry.connectorGroup.style.display = "none";
    }
    for (const layout of layouts) {
      const { entry } = layout;
      entry.placement = layout.placement;
      entry.card.hidden = false;
      entry.card.dataset.side = layout.side;
      entry.card.classList.toggle("event-map-video-card--mini", layout.mini);
      entry.card.style.left = `${layout.left}px`;
      entry.card.style.top = `${layout.top}px`;
      entry.connectorGroup.style.display = "";
      entry.connectorGroup.classList.toggle("event-map-video-connector--expanded", layout.expanded);
      const visibleConnectors = new Set(layout.projectedAnchors.map((anchor) => anchor.connector));
      for (const connector of entry.connectors) {
        if (!visibleConnectors.has(connector)) {
          connector.path.style.display = "none";
          connector.anchor.style.display = "none";
          continue;
        }
        const projectedAnchor = layout.projectedAnchors.find((anchor) => anchor.connector === connector);
        connector.path.style.display = "";
        connector.anchor.style.display = "";
        connector.path.setAttribute("d", eventMapMediaConnectorPath(projectedAnchor.x, projectedAnchor.y, layout));
        connector.anchor.setAttribute("cx", projectedAnchor.x.toFixed(2));
        connector.anchor.setAttribute("cy", projectedAnchor.y.toFixed(2));
      }
    }
    this.syncMediaConnectorFocus();
  }

  shouldPlayIntro() {
    if (globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return false;
    const snapshot = String(this.manifest.snapshot_id || this.manifest.layout_version || "unknown");
    const key = `raelyn:event-map-intro:${snapshot}`;
    try {
      if (sessionStorage.getItem(key)) return false;
      sessionStorage.setItem(key, "1");
    } catch {
      return false;
    }
    return true;
  }

  prepareProgressiveReveal() {
    if (globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return false;
    this.introActive = false;
    this.progressiveRevealPrepared = true;
    this.progressiveRevealActive = false;
    this.progressiveRevealStartedAt = 0;
    this.progressiveRevealComplete = false;
    this.coreMaterial.uniforms.uRevealProgress.value = 0;
    this.pickMaterial.uniforms.uRevealProgress.value = 0;
    this.labelsRoot.classList.add("event-map-label-layer--loading");
    this.invalidate();
    return true;
  }

  revealLabelsIfReady() {
    if (!this.labelMetadataReady || !this.progressiveRevealComplete || !this.labelsRoot) return false;
    if (!this.labelsRoot.classList.contains("event-map-label-layer--loading")) return true;
    void this.labelsRoot.offsetWidth;
    this.labelsRoot.classList.remove("event-map-label-layer--loading");
    return true;
  }

  updateManifest(manifest = {}, { refresh = true } = {}) {
    if (this.destroyed) return;
    const hadMembraneTopics = Array.isArray(this.membraneTopics) && this.membraneTopics.length > 0;
    this.manifest = manifest;
    this.topics = Array.isArray(manifest.topics) ? manifest.topics : [];
    this.membraneTopics = Array.isArray(manifest.topic_geometry)
      ? manifest.topic_geometry
      : this.topics;
    this.topicByIndex = new Map(this.topics.map((topic, index) => [topicIndexOf(topic, index), topic]));
    this.semanticPalette = eventMapSemanticPalette(manifest);
    this.labelMetadataReady = Array.isArray(manifest.topics);
    for (let index = 0; index < this.sceneData.count; index += 1) {
      const semantic = this.semanticPalette.byTypeCode[this.sceneData.eventType[index]]
        || this.semanticPalette.fallback;
      const offset = index * 3;
      this.semanticColors[offset] = semantic.rgb[0];
      this.semanticColors[offset + 1] = semantic.rgb[1];
      this.semanticColors[offset + 2] = semantic.rgb[2];
    }
    // compact manifest 已携带最终主题几何时，保留同一个网格对象，避免点集
    // 补齐时背景重新定形。仅兼容旧快照缺少轻量主题几何的情况。
    if (!hadMembraneTopics && this.membraneTopics.length) {
      this.semanticMembraneTransitionStartedAt = 0;
      this.scene.remove(this.semanticMembrane);
      this.semanticMembrane?.geometry?.dispose();
      this.semanticMembrane?.material?.dispose();
      this.setupSemanticMembrane(this.rawBounds);
    }
    if (refresh) {
      this.updateTopicActivity();
      this.updateSemanticMembraneDensity({ animate: false });
      this.updateColors();
      this.updateLabels();
      this.revealLabelsIfReady();
    }
    this.invalidate();
  }

  replaceScene(scene, { preserveRevealCount = false } = {}) {
    if (this.destroyed || !scene) return false;
    const previousCount = this.sceneData.count;
    const previousRevealProgress = Number(this.coreMaterial.uniforms.uRevealProgress.value || 0);
    const revealProgress = preserveRevealCount
      ? Math.min(1, previousRevealProgress * previousCount / Math.max(1, scene.count))
      : previousRevealProgress;
    this.scene.remove(this.corePoints);
    this.pickScene.remove(this.pickPoints);
    this.geometry.dispose();
    this.selectedIndex = null;
    this.selectedDetail = null;
    this.topicFocusIndex = null;
    this.initializePointState(scene);
    this.geometry = this.createPointGeometry();
    this.applyTimeHighlightMask();
    this.corePoints = new this.THREE.Points(this.geometry, this.coreMaterial);
    this.corePoints.frustumCulled = false;
    this.scene.add(this.corePoints);
    this.pickPoints = new this.THREE.Points(this.geometry, this.pickMaterial);
    this.pickPoints.frustumCulled = false;
    this.pickScene.add(this.pickPoints);
    this.coreMaterial.uniforms.uRevealProgress.value = revealProgress;
    this.pickMaterial.uniforms.uRevealProgress.value = revealProgress;
    this.pixelRatioLimit = scene.count > 100_000 ? 1.25 : 1.5;
    this.updateLayers(this.layerOptions);
    this.rebuildMediaCards();
    this.resize();
    this.invalidate();
    return true;
  }

  setSceneInteractive(value) {
    const interactive = Boolean(value);
    if (!interactive) this.deferMembraneDensity = true;
    this.sceneInteractive = interactive;
    const reducedMotion = Boolean(globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
    const hydrationActive = !interactive && !reducedMotion;
    if (hydrationActive && !this.hydrationAnimationActive) {
      this.hydrationAnimationStartedAt = performance.now();
    }
    this.hydrationAnimationActive = hydrationActive;
    const pointUniforms = this.coreMaterial?.uniforms;
    const gridUniforms = this.horizonGrid?.material?.uniforms;
    if (pointUniforms?.uHydrationActive) pointUniforms.uHydrationActive.value = hydrationActive ? 1 : 0;
    if (gridUniforms?.uHydrationActive) gridUniforms.uHydrationActive.value = hydrationActive ? 1 : 0;
    if (!hydrationActive) {
      this.hydrationAnimationStartedAt = 0;
      if (pointUniforms?.uHydrationTime) pointUniforms.uHydrationTime.value = 0;
      if (gridUniforms?.uHydrationTime) gridUniforms.uHydrationTime.value = 0;
    }
    this.invalidate?.();
  }

  finishInitialHydration() {
    // compact manifest 已经生成首帧最终网格。完整点集和标签无论谁先返回，
    // 都只补数据，不重算背景；下一次真实窗口交互才恢复密度变化。
    this.deferMembraneDensity = false;
  }

  startProgressiveReveal({
    target = 1,
    duration = PROGRESSIVE_REVEAL_MS,
    complete = true,
  } = {}) {
    if (!this.progressiveRevealPrepared || this.destroyed) return false;
    this.progressiveRevealFrom = Number(this.coreMaterial.uniforms.uRevealProgress.value || 0);
    this.progressiveRevealTarget = Math.max(this.progressiveRevealFrom, Math.min(1, Number(target || 0)));
    this.progressiveRevealDuration = Math.max(1, Number(duration || PROGRESSIVE_REVEAL_MS));
    this.progressiveRevealCompletes = Boolean(complete);
    this.progressiveRevealActive = true;
    this.progressiveRevealStartedAt = performance.now();
    this.invalidate();
    return true;
  }

  updateProgressiveReveal(now) {
    if (!this.progressiveRevealActive) return false;
    const duration = Math.max(1, Number(this.progressiveRevealDuration || PROGRESSIVE_REVEAL_MS));
    const progress = Math.min(1, Math.max(0, (now - this.progressiveRevealStartedAt) / duration));
    const eased = 1 - Math.pow(1 - progress, 2.4);
    const revealFrom = Number(this.progressiveRevealFrom || 0);
    const revealTarget = Number.isFinite(Number(this.progressiveRevealTarget))
      ? Number(this.progressiveRevealTarget)
      : 1;
    const revealProgress = revealFrom + (revealTarget - revealFrom) * eased;
    this.coreMaterial.uniforms.uRevealProgress.value = revealProgress;
    this.pickMaterial.uniforms.uRevealProgress.value = revealProgress;
    if (progress < 1) return true;
    this.progressiveRevealActive = false;
    if (this.progressiveRevealCompletes !== false) {
      this.progressiveRevealPrepared = false;
      this.progressiveRevealComplete = true;
      this.revealLabelsIfReady();
    }
    return false;
  }

  updateHydrationAnimation(now) {
    if (!this.hydrationAnimationActive) return false;
    const elapsedSeconds = Math.max(0, (now - this.hydrationAnimationStartedAt) / 1000);
    const pointUniforms = this.coreMaterial?.uniforms;
    const gridUniforms = this.horizonGrid?.material?.uniforms;
    if (pointUniforms?.uHydrationTime) pointUniforms.uHydrationTime.value = elapsedSeconds;
    if (gridUniforms?.uHydrationTime) gridUniforms.uHydrationTime.value = elapsedSeconds;
    return true;
  }

  rawToWorld(x, y, z) {
    return new this.THREE.Vector3(
      (Number(x) - this.rawCenter.x) * this.worldScale,
      (Number(y) - this.rawCenter.y) * this.worldScale,
      (Number(z) - this.rawCenter.z) * this.worldScale
    );
  }

  worldPoint(index) {
    if (!Number.isInteger(index) || index < 0 || index >= this.sceneData.count) return null;
    const offset = index * 3;
    return new this.THREE.Vector3(
      this.worldPositions[offset],
      this.worldPositions[offset + 1],
      this.worldPositions[offset + 2]
    );
  }

  windowDays(windowStart, windowEnd) {
    return {
      start: isoDateToEventMapDay(windowStart, -1_000_000_000),
      end: isoDateToEventMapDay(windowEnd, 1_000_000_000),
    };
  }

  applyWindowUniforms(previousStart, previousEnd, currentStart, currentEnd, mix) {
    for (const material of [this.coreMaterial]) {
      material.uniforms.uPreviousWindowStartDay.value = previousStart;
      material.uniforms.uPreviousWindowEndDay.value = previousEnd;
      material.uniforms.uWindowStartDay.value = currentStart;
      material.uniforms.uWindowEndDay.value = currentEnd;
      material.uniforms.uWindowMix.value = mix;
    }
    this.pickMaterial.uniforms.uWindowStartDay.value = currentStart;
    this.pickMaterial.uniforms.uWindowEndDay.value = currentEnd;
  }

  commitWindow(windowStart, windowEnd) {
    const next = this.windowDays(windowStart, windowEnd);
    const hadCommittedWindow = this.hasCommittedWindow;
    const sameWindow = (
      hadCommittedWindow
      && next.start === this.committedWindowStartDay
      && next.end === this.committedWindowEndDay
    );
    const previewMatches = (
      next.start === this.previewWindowStartDay
      && next.end === this.previewWindowEndDay
    );
    if (sameWindow && this.previewWindowStartDay === null) return;

    const previousStart = this.committedWindowStartDay;
    const previousEnd = this.committedWindowEndDay;
    this.committedWindowStartDay = next.start;
    this.committedWindowEndDay = next.end;
    this.hasCommittedWindow = true;
    this.previewWindowStartDay = null;
    this.previewWindowEndDay = null;

    if (hadCommittedWindow && !sameWindow && !previewMatches) {
      this.applyWindowUniforms(previousStart, previousEnd, next.start, next.end, 0);
      this.transitionStartedAt = performance.now();
    } else {
      this.applyWindowUniforms(next.start, next.end, next.start, next.end, 1);
      this.transitionStartedAt = 0;
    }
  }

  previewWindow({ windowStart = "", windowEnd = "" } = {}) {
    if (this.destroyed) return;
    const next = this.windowDays(windowStart, windowEnd);
    if (next.start === this.previewWindowStartDay && next.end === this.previewWindowEndDay) return;
    this.previewWindowStartDay = next.start;
    this.previewWindowEndDay = next.end;
    this.transitionStartedAt = 0;
    this.applyWindowUniforms(next.start, next.end, next.start, next.end, 1);
    this.applyActiveWindow(windowStart, windowEnd);
    this.invalidate();
  }

  cancelWindowPreview() {
    if (this.previewWindowStartDay === null || this.previewWindowEndDay === null) return;
    this.previewWindowStartDay = null;
    this.previewWindowEndDay = null;
    this.transitionStartedAt = 0;
    this.applyWindowUniforms(
      this.committedWindowStartDay,
      this.committedWindowEndDay,
      this.committedWindowStartDay,
      this.committedWindowEndDay,
      1
    );
    if (this.hasCommittedWindow) {
      this.applyActiveWindow(
        eventMapDayToIso(this.committedWindowStartDay),
        eventMapDayToIso(this.committedWindowEndDay)
      );
    }
    this.invalidate();
  }

  updateLayers(options = {}) {
    if (this.destroyed) return;
    this.layerOptions = {
      windowStart: String(options.windowStart || ""),
      windowEnd: String(options.windowEnd || ""),
      typeFilter: String(options.typeFilter || ""),
      entityIndices: options.entityIndices instanceof Set ? options.entityIndices : new Set(options.entityIndices || []),
      playing: Boolean(options.playing),
      topicIndex: options.topicIndex !== null
        && options.topicIndex !== undefined
        && Number.isInteger(Number(options.topicIndex))
        ? Number(options.topicIndex)
        : null,
    };
    this.coreMaterial.uniforms.uHasTopicFocus.value = this.layerOptions.topicIndex === null ? 0 : 1;
    const normalizedType = this.layerOptions.typeFilter;
    for (let index = 0; index < this.sceneData.count; index += 1) {
      this.pointVisibility[index] = (!normalizedType || String(this.sceneData.eventType[index]) === normalizedType) ? 1 : 0;
    }
    this.geometry.getAttribute("aPickable").needsUpdate = true;
    this.commitWindow(this.layerOptions.windowStart, this.layerOptions.windowEnd);
    this.applyActiveWindow(this.layerOptions.windowStart, this.layerOptions.windowEnd);
    this.invalidate();
  }

  applyActiveWindow(windowStart, windowEnd) {
    const state = buildEventMapLayerState(this.sceneData, {
      ...this.layerOptions,
      windowStart,
      windowEnd,
      selectedIndex: this.selectedIndex,
    });
    this.activeMask = state.activeMask;
    this.entityMask = state.entityMask;
    this.updateTopicActivity();
    this.updateSemanticMembraneDensity();
    this.updateTopicFocusStyles();
    this.updateColors();
    this.updateStoryLines();
    this.updateLabels();
  }

  updateColors() {
    for (let index = 0; index < this.sceneData.count; index += 1) {
      const offset = index * 3;
      let color = [
        this.semanticColors[offset],
        this.semanticColors[offset + 1],
        this.semanticColors[offset + 2],
      ];
      if (this.topicFocusMask[index]) {
        color = color.map((channel) => Math.min(1, channel * 1.12 + 0.08));
      }
      if (this.entityMask[index]) color = [0.2, 1.0, 0.55];
      if (index === this.selectedIndex) color = [1.0, 0.73, 0.12];
      this.colors[offset] = color[0];
      this.colors[offset + 1] = color[1];
      this.colors[offset + 2] = color[2];
    }
    this.geometry.getAttribute("aColor").needsUpdate = true;
  }

  isTopicMember(pointIndex, topicIndex) {
    return this.sceneData.macroTopicIndex[pointIndex] === topicIndex
      || this.sceneData.localTopicIndex[pointIndex] === topicIndex;
  }

  updateTopicFocusStyles() {
    const topicIndex = Number.isInteger(this.topicFocusIndex) ? this.topicFocusIndex : null;
    const hasFocus = topicIndex !== null && this.hasActiveTopic(topicIndex);
    for (let pointIndex = 0; pointIndex < this.sceneData.count; pointIndex += 1) {
      const focused = hasFocus
        && Boolean(this.activeMask[pointIndex])
        && this.isTopicMember(pointIndex, topicIndex);
      this.topicFocusMask[pointIndex] = focused ? 1 : 0;
      this.renderOpacity[pointIndex] = this.pointVisibility[pointIndex]
        ? (hasFocus ? (focused ? 1 : 0.22) : 1)
        : 0;
    }
    this.geometry.getAttribute("aOpacity").needsUpdate = true;
    this.geometry.getAttribute("aTopicFocus").needsUpdate = true;
  }

  updateTopicActivity() {
    this.topicCounts = new Map();
    this.topicFamilyCounts = new Map();
    for (let index = 0; index < this.sceneData.count; index += 1) {
      if (!this.activeMask[index]) continue;
      const semantic = this.semanticPalette.byTypeCode[this.sceneData.eventType[index]]
        || this.semanticPalette.fallback;
      for (const topicIndex of [
        this.sceneData.macroTopicIndex[index],
        this.sceneData.localTopicIndex[index],
      ]) {
        if (topicIndex === NO_INDEX) continue;
        this.topicCounts.set(topicIndex, (this.topicCounts.get(topicIndex) || 0) + 1);
        const familyCounts = this.topicFamilyCounts.get(topicIndex) || new Map();
        familyCounts.set(semantic.code, (familyCounts.get(semantic.code) || 0) + 1);
        this.topicFamilyCounts.set(topicIndex, familyCounts);
      }
    }
  }

  semanticFamilyForPoint(index) {
    return this.semanticPalette.byTypeCode[this.sceneData.eventType[index]]
      || this.semanticPalette.fallback;
  }

  semanticFamilyForTopic(index) {
    const counts = this.topicFamilyCounts?.get(Number(index));
    if (!counts?.size) return this.semanticPalette.fallback;
    const familyCode = [...counts.entries()]
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))[0][0];
    return this.semanticPalette.families.get(familyCode)
      || this.semanticPalette.byTypeCode.find((item) => item?.code === familyCode)
      || this.semanticPalette.fallback;
  }

  labelFocusRelation(label) {
    if (label.selected) return "selected";
    const focusedTopicIndex = Number.isInteger(this.topicFocusIndex) ? this.topicFocusIndex : null;
    const selectedPointIndex = Number.isInteger(this.selectedIndex) ? this.selectedIndex : null;

    if (focusedTopicIndex !== null) {
      if (label.kind === "event") {
        return this.isTopicMember(label.pointIndex, focusedTopicIndex) ? "member" : "unrelated";
      }
      if (label.kind === "topic") {
        const focusedTopic = this.topicByIndex.get(focusedTopicIndex);
        const labelIndex = Number(label.index);
        const focusedParent = focusedTopic?.parent_topic_index !== null
          && focusedTopic?.parent_topic_index !== undefined
          && Number.isInteger(Number(focusedTopic.parent_topic_index))
          ? Number(focusedTopic.parent_topic_index)
          : null;
        const labelParent = label.topic?.parent_topic_index !== null
          && label.topic?.parent_topic_index !== undefined
          && Number.isInteger(Number(label.topic.parent_topic_index))
          ? Number(label.topic.parent_topic_index)
          : null;
        if (labelIndex === focusedTopicIndex || labelIndex === focusedParent || labelParent === focusedTopicIndex) return "direct";
        if (focusedParent !== null && labelParent === focusedParent) return "context";
        return "unrelated";
      }
    }

    if (selectedPointIndex !== null) {
      const selectedMacro = this.sceneData.macroTopicIndex[selectedPointIndex];
      const selectedLocal = this.sceneData.localTopicIndex[selectedPointIndex];
      if (label.kind === "event") {
        const pointIndex = Number(label.pointIndex);
        if (!Number.isInteger(pointIndex)) return "unrelated";
        if (selectedLocal !== NO_INDEX && this.sceneData.localTopicIndex[pointIndex] === selectedLocal) return "member";
        if (selectedMacro !== NO_INDEX && this.sceneData.macroTopicIndex[pointIndex] === selectedMacro) return "context";
        return "unrelated";
      }
      if (label.kind === "topic") {
        const labelIndex = Number(label.index);
        if (labelIndex === selectedLocal || labelIndex === selectedMacro) return "direct";
        if (selectedMacro !== NO_INDEX && Number(label.topic?.parent_topic_index) === selectedMacro) return "context";
        return "unrelated";
      }
    }

    return "normal";
  }

  selectedTopicCandidate() {
    if (!Number.isInteger(this.topicFocusIndex)) return null;
    const index = this.topicFocusIndex;
    const topic = this.topicByIndex.get(index);
    const count = this.topicCounts?.get(index) || 0;
    if (!topic || !count) return null;
    return {
      key: `topic:${index}`,
      label: String(topic.label || "主题"),
      position: this.rawToWorld(topic.center_x, topic.center_y, topic.center_z),
      score: Number.MAX_SAFE_INTEGER,
      count,
      index,
      topic,
      selected: this.selectedIndex === null,
      kind: "topic",
      semantic: this.semanticFamilyForTopic(index),
    };
  }

  isActiveIndex(index) {
    return Number.isInteger(Number(index)) && Boolean(this.activeMask[Number(index)]);
  }

  hasActiveTopic(index) {
    return (this.topicCounts?.get(Number(index)) || 0) > 0;
  }

  topicActiveCount(index) {
    return Math.max(0, Number(this.topicCounts?.get(Number(index)) || 0));
  }

  setSelection(index, detail = null) {
    this.selectedIndex = Number.isInteger(Number(index)) && this.isActiveIndex(Number(index)) ? Number(index) : null;
    this.selectedDetail = detail;
    this.storyPath = null;
    this.storyAnimationStartedAt = 0;
    this.selectedMask.fill(0);
    if (this.selectedIndex !== null) this.selectedMask[this.selectedIndex] = 1;
    this.geometry.getAttribute("aSelected").needsUpdate = true;
    this.updateColors();
    this.updateStoryLines();
    this.updateLabels();
    this.invalidate();
  }

  setSelectionDetail(detail) {
    this.selectedDetail = detail || null;
    this.updateStoryLines();
    this.updateLabels();
    this.invalidate();
  }

  setStoryPath(path) {
    if (path && Array.isArray(path.edges)) {
      const occurredAtById = new Map(
        (path.nodes || []).map((node) => [String(node.canonical_id || ""), Date.parse(node.occurred_at || "") || 0])
      );
      this.storyPath = {
        ...path,
        edges: [...path.edges].sort((left, right) => {
          const leftTime = occurredAtById.get(String(left.target_canonical_id || "")) || 0;
          const rightTime = occurredAtById.get(String(right.target_canonical_id || "")) || 0;
          return leftTime - rightTime || String(left.edge_id || "").localeCompare(String(right.edge_id || ""));
        }),
      };
    } else this.storyPath = null;
    const reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    this.storyVisibleEdgeCount = reducedMotion ? Number(this.storyPath?.edges?.length || 0) : 0;
    this.storyAnimationStartedAt = this.storyPath && !reducedMotion ? performance.now() : 0;
    this.updateStoryLines();
    this.invalidate();
  }

  updateStoryAnimation(now) {
    if (!this.storyAnimationStartedAt || !this.storyPath) return false;
    const edgeCount = Math.min(64, this.storyPath.edges.length);
    const progress = Math.min(1, Math.max(0, (now - this.storyAnimationStartedAt) / Math.max(900, edgeCount * 180)));
    const visible = Math.min(edgeCount, Math.ceil(progress * edgeCount));
    if (visible !== this.storyVisibleEdgeCount) {
      this.storyVisibleEdgeCount = visible;
      this.updateStoryLines();
    }
    if (progress >= 1) this.storyAnimationStartedAt = 0;
    return progress < 1;
  }

  updateStoryLines() {
    const { THREE } = this.modules;
    for (const child of [...this.storyGroup.children]) {
      this.storyGroup.remove(child);
      child.geometry?.dispose();
      child.material?.dispose();
    }
    if (this.currentLevel !== "event") return;
    if (this.storyPath) {
      const edges = this.storyPath.edges.slice(0, Math.min(64, this.storyVisibleEdgeCount));
      for (let edgeIndex = 0; edgeIndex < edges.length; edgeIndex += 1) {
        const edge = edges[edgeIndex];
        const sourceIndex = Number(edge.source_point_index);
        const targetIndex = Number(edge.target_point_index);
        if (!this.isActiveIndex(sourceIndex) || !this.isActiveIndex(targetIndex)) continue;
        const start = this.worldPoint(sourceIndex);
        const end = this.worldPoint(targetIndex);
        if (!start || !end) continue;
        const middle = start.clone().add(end).multiplyScalar(0.5);
        const direction = end.clone().sub(start);
        const offset = new THREE.Vector3(-direction.y, direction.x, direction.z * 0.2).normalize();
        middle.addScaledVector(offset, Math.min(0.8, direction.length() * 0.12));
        const curve = new THREE.QuadraticBezierCurve3(start, middle, end);
        const geometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(24));
        const material = new THREE.LineBasicMaterial({
          color: edgeIndex % 2 ? 0x22d3ee : 0xa78bfa,
          transparent: true,
          opacity: 0.8,
          depthWrite: false,
        });
        this.storyGroup.add(new THREE.Line(geometry, material));
      }
      return;
    }
    if (this.selectedIndex === null) return;
    const start = this.worldPoint(this.selectedIndex);
    if (!start) return;
    const edges = Array.isArray(this.selectedDetail?.story_edges) ? this.selectedDetail.story_edges.slice(0, 24) : [];
    for (let edgeIndex = 0; edgeIndex < edges.length; edgeIndex += 1) {
      const edge = edges[edgeIndex];
      const otherIndex = Number(edge.other_point_index ?? edge.point_index);
      if (!this.isActiveIndex(otherIndex)) continue;
      const end = this.worldPoint(otherIndex);
      if (!end) continue;
      const middle = start.clone().add(end).multiplyScalar(0.5);
      const direction = end.clone().sub(start);
      const offset = new THREE.Vector3(-direction.y, direction.x, direction.z * 0.2).normalize();
      middle.addScaledVector(offset, Math.min(0.8, direction.length() * 0.12));
      const curve = new THREE.QuadraticBezierCurve3(start, middle, end);
      const geometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(24));
      const material = new THREE.LineBasicMaterial({
        color: edgeIndex % 2 ? 0x22d3ee : 0xa78bfa,
        transparent: true,
        opacity: 0.72,
        depthWrite: false,
      });
      this.storyGroup.add(new THREE.Line(geometry, material));
    }
  }

  semanticZoom() {
    const distance = Math.max(0.001, this.camera.position.distanceTo(this.controls.target));
    return this.homeDistance / distance;
  }

  updateSemanticLevel() {
    const next = eventMapSemanticLevel(this.semanticZoom(), this.currentLevel);
    if (next === this.currentLevel) return;
    this.currentLevel = next;
    this.updateStoryLines();
    this.updateLabels();
  }

  selectedEventCandidate() {
    if (this.selectedIndex === null || !this.isActiveIndex(this.selectedIndex)) return null;
    return {
      key: `event:${this.selectedIndex}`,
      label: String(this.selectedDetail?.title || "真实事件 · 加载中"),
      position: this.worldPoint(this.selectedIndex),
      pointIndex: this.selectedIndex,
      canonicalId: this.sceneData.canonicalIds[this.selectedIndex],
      score: Number.MAX_SAFE_INTEGER,
      selected: true,
      kind: "event",
      semantic: this.semanticFamilyForPoint(this.selectedIndex),
    };
  }

  labelCandidates() {
    const level = this.currentLevel;
    const requiredLevel = level === "overview" ? 0 : 1;
    const limit = level === "overview" ? 28 : level === "topic" ? 80 : 24;
    const selectedTopic = this.selectedTopicCandidate();
    const selectedEvent = this.selectedEventCandidate();
    if (level === "event") {
      const selectedIndex = this.selectedIndex;
      const representativeEvents = this.topics
        .map((topic, fallbackIndex) => {
          if (Number(topic.level || 0) !== 1) return null;
          const pointIndex = Number(topic.anchor_point_index);
          const topicIndex = topicIndexOf(topic, fallbackIndex);
          const count = this.topicCounts?.get(topicIndex) || 0;
          if (
            !Number.isInteger(pointIndex)
            || pointIndex < 0
            || pointIndex >= this.sceneData.count
            || pointIndex === selectedIndex
            || !this.isActiveIndex(pointIndex)
            || !count
          ) return null;
          return {
            key: `event:${pointIndex}`,
            label: String(topic.anchor_title || topic.label || "代表事件"),
            position: this.worldPoint(pointIndex),
            pointIndex,
            canonicalId: this.sceneData.canonicalIds[pointIndex],
            score: count * Math.max(0.1, Number(topic.distinctiveness || topic.score || 1)),
            selected: false,
            kind: "event",
            semantic: this.semanticFamilyForPoint(pointIndex),
          };
        })
        .filter(Boolean)
        .sort((left, right) => right.score - left.score || left.pointIndex - right.pointIndex);
      return [
        ...(selectedEvent ? [selectedEvent] : []),
        ...(selectedTopic ? [selectedTopic] : []),
        ...representativeEvents,
      ].slice(0, limit * 2);
    }
    const candidates = this.topics
      .map((topic, fallbackIndex) => {
        const index = topicIndexOf(topic, fallbackIndex);
        const count = this.topicCounts?.get(index) || 0;
        if (Number(topic.level || 0) !== requiredLevel || !count) return null;
        return {
          key: `topic:${index}`,
          label: String(topic.label || (requiredLevel ? "主题团" : "星域")),
          position: this.rawToWorld(topic.center_x, topic.center_y, topic.center_z),
          score: count * Math.max(0.1, Number(topic.distinctiveness || topic.score || 1)),
          count,
          index,
          topic,
          selected: index === this.topicFocusIndex,
          kind: "topic",
          semantic: this.semanticFamilyForTopic(index),
        };
      })
      .filter(Boolean)
      .sort((left, right) => right.score - left.score || left.index - right.index)
      .slice(0, limit * 2);
    return [
      ...(selectedEvent ? [selectedEvent] : []),
      ...(selectedTopic ? [selectedTopic] : []),
      ...candidates.filter((candidate) => candidate.index !== selectedTopic?.index),
    ];
  }

  updateLabels() {
    if (!this.labelsRoot || !this.camera) return;
    const width = Math.max(1, this.target.clientWidth || 1);
    const height = Math.max(1, this.target.clientHeight || 1);
    const accepted = [];
    const labels = [];
    for (const candidate of this.labelCandidates()) {
      const projected = candidate.position.clone().project(this.camera);
      if (projected.z < -1 || projected.z > 1 || Math.abs(projected.x) > 1.08 || Math.abs(projected.y) > 1.08) continue;
      const x = (projected.x * 0.5 + 0.5) * width;
      const y = (-projected.y * 0.5 + 0.5) * height;
      const labelText = eventMapLabelText(candidate);
      this.labelMeasure.textContent = labelText;
      const selectedEvent = candidate.kind === "event" && candidate.selected;
      const maximumLabelWidth = selectedEvent
        ? Math.max(180, Math.min(340, Math.floor(width * 0.36)))
        : Math.max(160, Math.min(460, Math.floor(width * 0.32)));
      const labelWidth = eventMapLabelWidth(
        labelText,
        () => this.labelMeasure.getBoundingClientRect().width,
        maximumLabelWidth
      );
      const labelHeight = selectedEvent ? 44 : 20;
      const makeRect = (left, top) => {
        const rect = {
          left: Math.max(4, Math.min(width - labelWidth - 4, left)),
          right: 0,
          top: Math.max(4, Math.min(height - labelHeight - 4, top)),
          bottom: 0,
        };
        rect.right = rect.left + labelWidth;
        rect.bottom = rect.top + labelHeight;
        return rect;
      };
      const placements = selectedEvent
        ? [
            makeRect(x + 14, y - labelHeight - 14),
            makeRect(x + 14, y + 14),
            makeRect(x - labelWidth - 14, y - labelHeight - 14),
            makeRect(x - labelWidth - 14, y + 14),
          ]
        : [makeRect(x - labelWidth / 2, y - labelHeight / 2)];
      const rect = placements.find((item) => !accepted.some((acceptedRect) => rectangleOverlaps(acceptedRect, item))) || placements[0];
      if (!selectedEvent && accepted.some((item) => rectangleOverlaps(item, rect))) continue;
      accepted.push(rect);
      labels.push({ ...candidate, labelText, rect, anchor: selectedEvent ? { x, y } : null, projectedZ: projected.z });
      if (labels.length >= (this.currentLevel === "overview" ? 28 : this.currentLevel === "topic" ? 80 : 24)) break;
    }
    this.labelsRoot.replaceChildren();
    for (const label of labels) {
      const interactive = label.kind === "topic" || (label.kind === "event" && !label.selected);
      const selectedEvent = label.kind === "event" && label.selected;
      const element = document.createElement(interactive ? "button" : "div");
      element.className = [
        "absolute text-[10px] font-medium tracking-wide",
        label.selected
          ? "rounded border px-2 py-1 shadow-lg backdrop-blur-sm"
          : "event-map-label rounded px-1.5 py-0.5",
        selectedEvent ? "whitespace-normal leading-4" : "max-w-[460px] truncate",
        interactive ? "pointer-events-auto cursor-pointer" : "pointer-events-none",
        label.selected
          ? "border-amber-300/70 bg-amber-400/15 text-amber-100"
          : "",
      ].join(" ");
      element.style.left = `${label.rect.left}px`;
      element.style.top = `${label.rect.top}px`;
      element.style.width = `${label.rect.right - label.rect.left}px`;
      if (!label.selected) {
        const semanticColor = label.semantic?.color || (label.topic?.level === 0 ? "#7dd3fc" : "#67e8f9");
        element.style.setProperty("--event-map-label-color", semanticColor);
        const focusRelation = this.labelFocusRelation(label);
        element.dataset.focusRelation = focusRelation;
        element.style.setProperty("--event-map-label-opacity", String(eventMapLabelFocusOpacity(focusRelation)));
      }
      if (selectedEvent && label.anchor) {
        const anchorX = label.anchor.x - label.rect.left;
        const anchorY = label.anchor.y - label.rect.top;
        const endX = Math.max(0, Math.min(label.rect.right - label.rect.left, anchorX));
        const endY = Math.max(0, Math.min(label.rect.bottom - label.rect.top, anchorY));
        const deltaX = endX - anchorX;
        const deltaY = endY - anchorY;
        const length = Math.hypot(deltaX, deltaY);
        if (length > 1) {
          const stem = document.createElement("span");
          stem.className = "pointer-events-none absolute h-px bg-amber-200/80";
          stem.style.left = `${anchorX}px`;
          stem.style.top = `${anchorY}px`;
          stem.style.width = `${length}px`;
          stem.style.transformOrigin = "0 0";
          stem.style.transform = `rotate(${Math.atan2(deltaY, deltaX)}rad)`;
          element.appendChild(stem);
        }
        const content = document.createElement("span");
        content.className = "relative block";
        // 选中事件的引线标签允许两行，不能把超长标题挤出卡片或退化为省略号。
        content.style.display = "-webkit-box";
        content.style.webkitBoxOrient = "vertical";
        content.style.webkitLineClamp = "2";
        content.style.overflow = "hidden";
        content.textContent = label.labelText;
        element.appendChild(content);
      } else element.textContent = label.labelText;
      element.title = label.label;
      if (label.kind === "topic") {
        element.addEventListener("click", (event) => {
          event.stopPropagation();
          this.topicFocusIndex = label.index;
          this.onTopic?.(label.index, {
            ...label.topic,
            topic_index: label.index,
            canonical_count: label.count,
          });
        });
      } else if (interactive) {
        element.addEventListener("click", (event) => {
          event.stopPropagation();
          this.onSelect?.(label.pointIndex, label.canonicalId);
        });
      }
      this.labelsRoot.appendChild(element);
    }
    this.updateMediaCards();
    const viewportCanonicalCount = this.countViewportEvents();
    this.onViewport?.({
      sceneLevel: this.currentLevel,
      viewportCanonicalCount,
      renderedTopicLabelCount: labels.filter((item) => item.kind === "topic").length,
    });
  }

  countViewportEvents() {
    let count = 0;
    const vector = new this.THREE.Vector3();
    for (let index = 0; index < this.sceneData.count; index += 1) {
      if (!this.activeMask[index]) continue;
      const offset = index * 3;
      vector.set(this.worldPositions[offset], this.worldPositions[offset + 1], this.worldPositions[offset + 2]).project(this.camera);
      if (vector.z >= -1 && vector.z <= 1 && Math.abs(vector.x) <= 1 && Math.abs(vector.y) <= 1) count += 1;
    }
    return count;
  }

  pick(clientX, clientY) {
    if (!this.sceneInteractive || this.destroyed || !this.renderer || !this.camera) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const ratio = this.renderer.getPixelRatio();
    const x = Math.max(0, Math.min(this.pickTarget.width - 1, Math.floor((clientX - rect.left) * ratio)));
    const y = Math.max(0, Math.min(this.pickTarget.height - 1, Math.floor((rect.bottom - clientY) * ratio)));
    const pixel = new Uint8Array(4);
    const previousTarget = this.renderer.getRenderTarget();
    const previousToneMapping = this.renderer.toneMapping;
    this.renderer.toneMapping = this.THREE.NoToneMapping;
    this.renderer.setRenderTarget(this.pickTarget);
    this.renderer.clear();
    this.renderer.render(this.pickScene, this.camera);
    this.renderer.readRenderTargetPixels(this.pickTarget, x, y, 1, 1, pixel);
    this.renderer.setRenderTarget(previousTarget);
    this.renderer.toneMapping = previousToneMapping;
    const encoded = pixel[0] + (pixel[1] << 8) + (pixel[2] << 16);
    const index = encoded - 1;
    return encoded && this.isActiveIndex(index) ? index : null;
  }

  focusPoint(index) {
    const point = this.worldPoint(Number(index));
    if (!point) return;
    this.focusSphere(point, 0.45);
  }

  focusTopic(index) {
    const topicIndex = Number(index);
    const points = [];
    for (let pointIndex = 0; pointIndex < this.sceneData.count; pointIndex += 1) {
      if (!this.activeMask[pointIndex]) continue;
      if (this.isTopicMember(pointIndex, topicIndex)) points.push(this.worldPoint(pointIndex));
    }
    if (!points.length) return;
    const box = new this.THREE.Box3().setFromPoints(points);
    const sphere = box.getBoundingSphere(new this.THREE.Sphere());
    this.topicFocusIndex = topicIndex;
    this.focusSphere(sphere.center, sphere.radius);
  }

  clearTopicFocus() {
    this.topicFocusIndex = null;
    this.updateLabels();
    this.invalidate();
  }

  fitBounds(bounds = {}, framing = {}) {
    if (![bounds.minX, bounds.maxX, bounds.minY, bounds.maxY].every(Number.isFinite)) return;
    const minimum = this.rawToWorld(bounds.minX, bounds.minY, Number.isFinite(bounds.minZ) ? bounds.minZ : this.rawCenter.z);
    const maximum = this.rawToWorld(bounds.maxX, bounds.maxY, Number.isFinite(bounds.maxZ) ? bounds.maxZ : this.rawCenter.z);
    const box = new this.THREE.Box3(minimum.min(maximum.clone()), maximum.max(minimum.clone()));
    const sphere = box.getBoundingSphere(new this.THREE.Sphere());
    this.focusSphere(sphere.center, sphere.radius, framing);
  }

  focusSphere(center, radius, {
    perspectiveDistanceScale = 3.1,
  } = {}) {
    this.introActive = false;
    this.controls.target.copy(center);
    const distance = Math.max(2.4, Number(radius || 0.2) * Number(perspectiveDistanceScale));
    const direction = this.camera.position.clone().sub(this.controls.target).normalize();
    this.camera.position.copy(center).addScaledVector(direction, distance);
    this.camera.updateProjectionMatrix();
    this.updateSemanticLevel();
    this.updateLabels();
    this.invalidate();
  }

  fitActiveWindow() {
    const bounds = eventMapActiveBounds(this.sceneData, this.activeMask);
    if (!bounds) return false;
    this.fitBounds(bounds, {
      perspectiveDistanceScale: ACTIVE_WINDOW_PERSPECTIVE_DISTANCE_SCALE,
    });
    return true;
  }

  cameraState() {
    if (!this.camera || !this.controls) return null;
    return {
      position: this.camera.position.toArray(),
      target: this.controls.target.toArray(),
      viewport_aspect: Math.max(1, this.target.clientWidth || 1) / Math.max(1, this.target.clientHeight || 1),
    };
  }

  restoreCameraState(state) {
    const position = Array.isArray(state?.position) ? state.position.map(Number) : [];
    const target = Array.isArray(state?.target) ? state.target.map(Number) : [];
    if (position.length !== 3 || target.length !== 3 || ![...position, ...target].every(Number.isFinite)) return false;
    this.introActive = false;
    this.controls.target.fromArray(target);
    this.camera.position.fromArray(position);
    this.camera.lookAt(this.controls.target);
    this.camera.updateProjectionMatrix();
    this.updateSemanticLevel();
    this.updateLabels();
    this.invalidate();
    return true;
  }

  resetCamera({ animate = false } = {}) {
    this.controls.target.set(0, 0, 0);
    this.camera.position.set(13.5, 10.5, 15.5).multiplyScalar(EVENT_MAP_HOME_CAMERA_SCALE);
    if (animate) {
      this.introFinalPosition = this.camera.position.clone();
      this.camera.position.multiplyScalar(1.45).applyAxisAngle(new this.THREE.Vector3(0, 1, 0), -0.24);
      this.introStartPosition = this.camera.position.clone();
      this.introStartedAt = performance.now();
      this.introActive = true;
    }
    this.camera.lookAt(this.controls.target);
    this.camera.updateProjectionMatrix();
    this.updateSemanticLevel();
    this.updateLabels();
    this.invalidate();
  }

  resize() {
    if (this.destroyed || !this.renderer) return;
    const width = Math.max(1, this.target.clientWidth || this.target.getBoundingClientRect().width || 1);
    const height = Math.max(1, this.target.clientHeight || this.target.getBoundingClientRect().height || 1);
    const ratio = Math.min(this.pixelRatioLimit, Number(globalThis.devicePixelRatio || 1));
    this.renderer.setPixelRatio(ratio);
    this.renderer.setSize(width, height, false);
    this.pickTarget.setSize(Math.max(1, Math.floor(width * ratio)), Math.max(1, Math.floor(height * ratio)));
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.coreMaterial.uniforms.uPixelRatio.value = ratio;
    this.pickMaterial.uniforms.uPixelRatio.value = ratio;
    this.updateLabels();
    this.invalidate();
  }

  updateTransition(now) {
    if (!this.transitionStartedAt) return false;
    const progress = Math.min(1, Math.max(0, (now - this.transitionStartedAt) / WINDOW_TRANSITION_MS));
    const eased = progress * progress * (3 - 2 * progress);
    this.coreMaterial.uniforms.uWindowMix.value = eased;
    if (progress >= 1) {
      this.transitionStartedAt = 0;
      this.applyWindowUniforms(
        this.committedWindowStartDay,
        this.committedWindowEndDay,
        this.committedWindowStartDay,
        this.committedWindowEndDay,
        1
      );
    }
    return progress < 1;
  }

  updateIntro(now) {
    if (!this.introActive || !this.introStartPosition || !this.introFinalPosition) return false;
    const progress = Math.min(1, (now - this.introStartedAt) / 4000);
    const eased = 1 - Math.pow(1 - progress, 3);
    this.camera.position.lerpVectors(this.introStartPosition, this.introFinalPosition, eased);
    this.camera.position.applyAxisAngle(new this.THREE.Vector3(0, 1, 0), Math.sin(progress * Math.PI) * 0.002);
    this.camera.lookAt(this.controls.target);
    if (progress >= 1) this.introActive = false;
    return progress < 1;
  }

  monitorFrameRate(now) {
    this.frameSamples.push(now);
    while (this.frameSamples.length && now - this.frameSamples[0] > 2500) this.frameSamples.shift();
    if (this.reducedQuality || this.frameSamples.length < 45) return;
    const duration = now - this.frameSamples[0];
    const fps = duration > 0 ? (this.frameSamples.length - 1) * 1000 / duration : 60;
    if (fps >= 30) return;
    this.reducedQuality = true;
    this.pixelRatioLimit = 1;
    this.resize();
  }

  invalidate() {
    if (this.destroyed || this.animationFrame !== null) return;
    this.animationFrame = requestAnimationFrame((now) => this.renderFrame(now));
  }

  whenFirstFrame() {
    return this.firstFrameRendered ? Promise.resolve() : this.firstFramePromise;
  }

  renderFrame(now) {
    this.animationFrame = null;
    if (this.destroyed) return;
    const transitioning = this.updateTransition(now);
    const membraneTransitioning = this.updateSemanticMembraneTransition(now);
    const introducing = this.updateIntro(now);
    const storyAnimating = this.updateStoryAnimation(now);
    const revealing = this.updateProgressiveReveal(now);
    const hydrating = this.updateHydrationAnimation(now);
    const moving = this.controls.update();
    this.updateHorizonGrid();
    if (transitioning || membraneTransitioning || introducing || storyAnimating || moving) this.updateLabels();
    this.renderer.render(this.scene, this.camera);
    if (!this.firstFrameRendered) {
      this.firstFrameRendered = true;
      this.resolveFirstFrame?.();
      this.resolveFirstFrame = null;
    }
    this.monitorFrameRate(now);
    if (transitioning || membraneTransitioning || introducing || storyAnimating || revealing || hydrating || moving) this.invalidate();
  }

  showError(message) {
    this.errorOverlay.textContent = String(message || "三维事件星图不可用");
    this.errorOverlay.classList.remove("hidden");
    this.errorOverlay.classList.add("flex");
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    this.clearMediaCardHoverTimers();
    for (const video of this.mediaRoot?.querySelectorAll?.("video") || []) {
      video.pause();
      video.removeAttribute("src");
    }
    this.resolveFirstFrame?.();
    this.resolveFirstFrame = null;
    if (this.animationFrame !== null) cancelAnimationFrame(this.animationFrame);
    this.resizeObserver?.disconnect();
    this.controls?.removeEventListener("start", this.handleControlsStart);
    this.controls?.removeEventListener("change", this.handleControlsChange);
    this.controls?.removeEventListener("end", this.handleControlsEnd);
    this.controls?.dispose();
    this.target.removeEventListener("wheel", this.handleWheel, true);
    this.target.removeEventListener("gesturestart", this.handleGestureStart);
    this.target.removeEventListener("gesturechange", this.handleGestureChange);
    this.target.removeEventListener("gestureend", this.handleGestureEnd);
    this.renderer?.domElement.removeEventListener("pointerdown", this.handlePointerDown);
    this.renderer?.domElement.removeEventListener("pointerup", this.handlePointerUp);
    this.renderer?.domElement.removeEventListener("dblclick", this.handleDoubleClick);
    this.renderer?.domElement.removeEventListener("webglcontextlost", this.handleContextLost);
    this.renderer?.domElement.removeEventListener("webglcontextrestored", this.handleContextRestored);
    for (const child of [...(this.storyGroup?.children || [])]) {
      child.geometry?.dispose();
      child.material?.dispose();
    }
    this.horizonGrid?.geometry?.dispose();
    this.horizonGrid?.material?.dispose();
    this.semanticMembrane?.geometry?.dispose();
    this.semanticMembrane?.material?.dispose();
    this.geometry?.dispose();
    this.coreMaterial?.dispose();
    this.pickMaterial?.dispose();
    this.pickTarget?.dispose();
    this.renderer?.dispose();
    this.renderer?.forceContextLoss?.();
    this.target?.replaceChildren();
    this.target = null;
  }
}

export const EVENT_MAP_NO_INDEX = NO_INDEX;
export const EVENT_MAP_SCENE_RECORD_SIZE = SCENE_RECORD_SIZE;
