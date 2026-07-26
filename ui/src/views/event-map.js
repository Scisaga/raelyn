const SCENE_RECORD_SIZE = 56;
const INDEX_RECORD_SIZE = 4;
const DAY_MS = 86_400_000;
const NO_INDEX = 0xffff_ffff;
const TOPIC_ZOOM_ENTER = 1.7;
const TOPIC_ZOOM_EXIT = 1.45;
const EVENT_ZOOM_ENTER = 4.8;
const EVENT_ZOOM_EXIT = 4.2;
const WINDOW_TRANSITION_MS = 220;
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
    },
    vertexShader: `
      uniform float uSize;
      uniform float uPixelRatio;
      uniform float uPreviousWindowStartDay;
      uniform float uPreviousWindowEndDay;
      uniform float uWindowStartDay;
      uniform float uWindowEndDay;
      uniform float uWindowMix;
      attribute float aOpacity;
      attribute float aTopicFocus;
      attribute float aSelected;
      attribute vec3 aColor;
      attribute float aStartDay;
      attribute float aEndDay;
      varying float vOpacity;
      varying float vTopicFocus;
      varying float vSelected;
      varying vec3 vColor;
      void main() {
        float previousActive = step(aStartDay, uPreviousWindowEndDay) * step(uPreviousWindowStartDay, aEndDay);
        float currentActive = step(aStartDay, uWindowEndDay) * step(uWindowStartDay, aEndDay);
        vOpacity = aOpacity * mix(previousActive, currentActive, clamp(uWindowMix, 0.0, 1.0));
        vTopicFocus = aTopicFocus;
        vSelected = aSelected;
        vColor = aColor;
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = uSize * uPixelRatio * (1.0 + 0.42 * aTopicFocus + 0.92 * aSelected) * clamp(7.0 / max(1.0, -mvPosition.z), 0.55, 2.8);
        gl_Position = projectionMatrix * mvPosition;
      }
    `,
    fragmentShader: `
      varying float vOpacity;
      varying float vTopicFocus;
      varying float vSelected;
      varying vec3 vColor;
      uniform float uAlpha;
      void main() {
        vec2 offset = gl_PointCoord - vec2(0.5);
        float radius = length(offset) * 2.0;
        if (radius > 1.0 || vOpacity <= 0.001) discard;
        float core = 1.0 - smoothstep(0.72, 1.0, radius);
        float focusRing = vTopicFocus * smoothstep(0.64, 0.78, radius) * (1.0 - smoothstep(0.86, 0.98, radius));
        float selectedRing = vSelected * smoothstep(0.46, 0.60, radius) * (1.0 - smoothstep(0.78, 0.94, radius));
        float selectedCore = vSelected * (1.0 - smoothstep(0.16, 0.38, radius));
        float opacity = max(core, max(focusRing, max(selectedRing, selectedCore)));
        vec3 color = mix(vColor, vec3(1.0, 0.73, 0.12), focusRing * 0.88);
        color = mix(color, vec3(1.0, 0.73, 0.12), selectedRing);
        color = mix(color, vec3(1.0), selectedCore);
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
    },
    vertexShader: `
      uniform float uWindowStartDay;
      uniform float uWindowEndDay;
      uniform float uPixelRatio;
      attribute float aPickable;
      attribute vec3 aPickColor;
      attribute float aStartDay;
      attribute float aEndDay;
      varying float vOpacity;
      varying vec3 vPickColor;
      void main() {
        float eventActive = step(aStartDay, uWindowEndDay) * step(uWindowStartDay, aEndDay);
        vOpacity = aPickable * eventActive;
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

function topicIndexOf(topic, fallback) {
  const value = Number(topic?.topic_index);
  return Number.isInteger(value) && value >= 0 ? value : fallback;
}

export class EventMapController {
  static async create(options) {
    if (!(await eventMapGpuAvailable())) throw new Error("当前浏览器不支持 WebGL2，无法显示三维事件星图。");
    return new EventMapController(options, await loadThreeModule());
  }

  constructor(
    {
      target,
      scene,
      manifest = {},
      onSelect = null,
      onTopic = null,
      onViewport = null,
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
    this.modules = modules;
    this.THREE = modules.THREE;
    this.destroyed = false;
    this.selectedIndex = null;
    this.selectedDetail = null;
    this.topicFocusIndex = null;
    this.currentLevel = "overview";
    this.activeMask = new Uint8Array(scene.count);
    this.pointVisibility = new Float32Array(scene.count);
    this.pointVisibility.fill(1);
    this.renderOpacity = new Float32Array(scene.count);
    this.renderOpacity.fill(1);
    this.topicFocusMask = new Float32Array(scene.count);
    this.selectedMask = new Float32Array(scene.count);
    this.entityMask = new Uint8Array(scene.count);
    this.topicCounts = new Map();
    this.topicFamilyCounts = new Map();
    this.layerOptions = { typeFilter: "", entityIndices: new Set() };
    this.transitionStartedAt = 0;
    this.committedWindowStartDay = -1_000_000_000;
    this.committedWindowEndDay = 1_000_000_000;
    this.hasCommittedWindow = false;
    this.previewWindowStartDay = null;
    this.previewWindowEndDay = null;
    this.animationFrame = null;
    this.introStartedAt = 0;
    this.introActive = false;
    this.pointerDown = null;
    this.frameSamples = [];
    this.reducedQuality = false;
    this.pixelRatioLimit = 2;
    this.topics = Array.isArray(manifest.topics) ? manifest.topics : [];
    this.topicByIndex = new Map(this.topics.map((topic, index) => [topicIndexOf(topic, index), topic]));
    this.semanticPalette = eventMapSemanticPalette(manifest);
    this.setupDom();
    this.setupScene();
    this.setupInteractions();
    this.resize();
    this.resetCamera({ animate: this.shouldPlayIntro() });
    this.updateLabels();
    this.invalidate();
  }

  setupDom() {
    this.target.replaceChildren();
    this.target.style.position = "absolute";
    this.target.style.inset = "0";
    this.target.style.overflow = "hidden";
    this.labelsRoot = document.createElement("div");
    this.labelsRoot.className = "absolute inset-0 z-10 overflow-hidden pointer-events-none";
    this.labelsRoot.setAttribute("aria-hidden", "false");
    this.target.appendChild(this.labelsRoot);
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

    const xBounds = finiteBounds(this.sceneData.x);
    const yBounds = finiteBounds(this.sceneData.y);
    const zBounds = finiteBounds(this.sceneData.z);
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
    const positions = new Float32Array(this.sceneData.count * 3);
    const colors = new Float32Array(this.sceneData.count * 3);
    const semanticColors = new Float32Array(this.sceneData.count * 3);
    const pickColors = new Float32Array(this.sceneData.count * 3);
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
    }
    this.worldPositions = positions;
    this.colors = colors;
    this.semanticColors = semanticColors;
    this.geometry = new THREE.BufferGeometry();
    this.geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    this.geometry.setAttribute("aColor", new THREE.BufferAttribute(colors, 3));
    this.geometry.setAttribute("aOpacity", new THREE.BufferAttribute(this.renderOpacity, 1));
    this.geometry.setAttribute("aPickable", new THREE.BufferAttribute(this.pointVisibility, 1));
    this.geometry.setAttribute("aTopicFocus", new THREE.BufferAttribute(this.topicFocusMask, 1));
    this.geometry.setAttribute("aSelected", new THREE.BufferAttribute(this.selectedMask, 1));
    this.geometry.setAttribute("aStartDay", new THREE.BufferAttribute(new Float32Array(this.sceneData.startDay), 1));
    this.geometry.setAttribute("aEndDay", new THREE.BufferAttribute(new Float32Array(this.sceneData.endDay), 1));
    this.geometry.setAttribute("aPickColor", new THREE.BufferAttribute(pickColors, 3));
    this.geometry.computeBoundingSphere();

    this.coreMaterial = createParticleMaterial(THREE, { size: 3.0, alpha: 0.94 });
    this.corePoints = new THREE.Points(this.geometry, this.coreMaterial);
    this.corePoints.frustumCulled = false;
    this.scene.add(this.corePoints);
    this.setupSpacetimeGrid({ xBounds, yBounds, zBounds });

    this.perspectiveCamera = new THREE.PerspectiveCamera(42, 1, 0.02, 160);
    this.orthographicCamera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.02, 160);
    this.cameraMode = "perspective";
    this.camera = this.perspectiveCamera;
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;
    this.controls.minDistance = 1.3;
    this.controls.maxDistance = 70;
    this.controls.target.set(0, 0, 0);

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
    this.scene.add(this.storyGroup);
    this.homeDistance = 21;
  }

  setupSpacetimeGrid({ xBounds, yBounds, zBounds }) {
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
    const macroTopics = this.topics
      .filter((topic) => Number(topic?.level || 0) === 0)
      .filter((topic) => [topic?.center_x, topic?.center_y, topic?.center_z].every(Number.isFinite))
      .sort((left, right) => Number(right?.canonical_count || 0) - Number(left?.canonical_count || 0))
      .slice(0, 9);
    const maximumCount = Math.max(1, ...macroTopics.map((topic) => Number(topic?.canonical_count || 0)));
    const wells = [
      { x: 0, y: 0, strength: 0.3, radius: 6.5 },
      ...macroTopics.map((topic) => {
        const center = this.rawToWorld(topic.center_x, topic.center_y, topic.center_z);
        const weight = Math.sqrt(Math.max(0, Number(topic.canonical_count || 0)) / maximumCount);
        return {
          x: center.dot(right),
          y: center.dot(gridUp),
          strength: 0.5 + 0.72 * weight,
          radius: 1.55 + 1.05 * weight,
        };
      }),
    ];
    const halfSpan = Math.max(...corners.flatMap((corner) => [
      Math.abs(corner.dot(right)),
      Math.abs(corner.dot(gridUp)),
    ]));
    const gridData = buildEventMapSpacetimeGrid({
      extent: Math.max(15, halfSpan * 2.1),
      wells,
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(gridData.positions, 3));
    geometry.setAttribute("aFade", new THREE.BufferAttribute(gridData.fades, 1));
    geometry.setAttribute("aCurvature", new THREE.BufferAttribute(gridData.curvatures, 1));
    const material = new THREE.ShaderMaterial({
      uniforms: {
        uBaseColor: { value: new THREE.Color(0x38bdf8) },
        uWarpColor: { value: new THREE.Color(0x8b5cf6) },
        uOpacity: { value: 0.24 },
      },
      vertexShader: `
        attribute float aFade;
        attribute float aCurvature;
        varying float vFade;
        varying float vCurvature;
        void main() {
          vFade = aFade;
          vCurvature = aCurvature;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
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
    this.spacetimeGrid = new THREE.LineSegments(geometry, material);
    const basis = new THREE.Matrix4().makeBasis(right, gridUp, normal);
    const minimumProjection = Math.min(...corners.map((corner) => corner.dot(normal)));
    this.spacetimeGrid.quaternion.setFromRotationMatrix(basis);
    this.spacetimeGrid.position.copy(normal).multiplyScalar(minimumProjection - 2.6);
    this.spacetimeGrid.frustumCulled = false;
    this.spacetimeGrid.renderOrder = -20;
    this.scene.add(this.spacetimeGrid);
  }

  setupInteractions() {
    this.handleControlsStart = () => {
      this.introActive = false;
      this.invalidate();
    };
    this.handleControlsChange = () => {
      this.updateLabels();
      this.updateSemanticLevel();
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
    this.controls.addEventListener("start", this.handleControlsStart);
    this.controls.addEventListener("change", this.handleControlsChange);
    this.renderer.domElement.addEventListener("pointerdown", this.handlePointerDown);
    this.renderer.domElement.addEventListener("pointerup", this.handlePointerUp);
    this.renderer.domElement.addEventListener("dblclick", this.handleDoubleClick);
    this.renderer.domElement.addEventListener("webglcontextlost", this.handleContextLost);
    this.renderer.domElement.addEventListener("webglcontextrestored", this.handleContextRestored);
    this.resizeObserver = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => this.resize()) : null;
    this.resizeObserver?.observe(this.target);
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
      typeFilter: String(options.typeFilter || ""),
      entityIndices: options.entityIndices instanceof Set ? options.entityIndices : new Set(options.entityIndices || []),
    };
    const normalizedType = this.layerOptions.typeFilter;
    for (let index = 0; index < this.sceneData.count; index += 1) {
      this.pointVisibility[index] = (!normalizedType || String(this.sceneData.eventType[index]) === normalizedType) ? 1 : 0;
    }
    this.geometry.getAttribute("aPickable").needsUpdate = true;
    this.commitWindow(options.windowStart, options.windowEnd);
    this.applyActiveWindow(options.windowStart, options.windowEnd);
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
    const topicIndex = Number(this.topicFocusIndex);
    const hasFocus = Number.isInteger(topicIndex) && this.hasActiveTopic(topicIndex);
    for (let pointIndex = 0; pointIndex < this.sceneData.count; pointIndex += 1) {
      const focused = hasFocus
        && Boolean(this.activeMask[pointIndex])
        && this.isTopicMember(pointIndex, topicIndex);
      this.topicFocusMask[pointIndex] = focused ? 1 : 0;
      this.renderOpacity[pointIndex] = this.pointVisibility[pointIndex]
        ? (hasFocus ? (focused ? 1 : 0.48) : 1)
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

  selectedTopicCandidate() {
    const index = Number(this.topicFocusIndex);
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
      selected: true,
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

  updateStoryLines() {
    const { THREE } = this.modules;
    for (const child of [...this.storyGroup.children]) {
      this.storyGroup.remove(child);
      child.geometry?.dispose();
      child.material?.dispose();
    }
    if (this.currentLevel !== "event" || this.selectedIndex === null) return;
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
    if (this.cameraMode === "orthographic") return this.homeDistance / Math.max(0.001, distance) * this.orthographicCamera.zoom;
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
    if (this.destroyed || !this.renderer || !this.camera) return null;
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

  fitBounds(bounds = {}) {
    if (![bounds.minX, bounds.maxX, bounds.minY, bounds.maxY].every(Number.isFinite)) return;
    const minimum = this.rawToWorld(bounds.minX, bounds.minY, Number.isFinite(bounds.minZ) ? bounds.minZ : this.rawCenter.z);
    const maximum = this.rawToWorld(bounds.maxX, bounds.maxY, Number.isFinite(bounds.maxZ) ? bounds.maxZ : this.rawCenter.z);
    const box = new this.THREE.Box3(minimum.min(maximum.clone()), maximum.max(minimum.clone()));
    const sphere = box.getBoundingSphere(new this.THREE.Sphere());
    this.focusSphere(sphere.center, sphere.radius);
  }

  focusSphere(center, radius) {
    const distance = Math.max(2.4, Number(radius || 0.2) * 3.1);
    const direction = this.camera.position.clone().sub(this.controls.target).normalize();
    this.controls.target.copy(center);
    this.camera.position.copy(center).addScaledVector(direction, distance);
    this.camera.updateProjectionMatrix();
    this.updateSemanticLevel();
    this.updateLabels();
    this.invalidate();
  }

  setCameraMode(mode) {
    const next = mode === "orthographic" ? "orthographic" : "perspective";
    if (next === this.cameraMode) return;
    const target = this.controls.target.clone();
    const direction = this.camera.position.clone().sub(target).normalize();
    const distance = this.camera.position.distanceTo(target);
    this.cameraMode = next;
    this.camera = next === "orthographic" ? this.orthographicCamera : this.perspectiveCamera;
    this.camera.position.copy(target).addScaledVector(direction, distance || this.homeDistance);
    this.camera.up.set(0, 1, 0);
    if (next === "orthographic") {
      this.camera.position.copy(target).add(new this.THREE.Vector3(0, 0, this.homeDistance));
      this.camera.zoom = 1;
    }
    this.camera.lookAt(target);
    this.controls.object = this.camera;
    this.resize();
    this.updateSemanticLevel();
    this.updateLabels();
    this.invalidate();
  }

  resetCamera({ animate = false } = {}) {
    this.controls.target.set(0, 0, 0);
    if (this.cameraMode === "orthographic") {
      this.camera.position.set(0, 0, this.homeDistance);
      this.camera.zoom = 1;
    } else {
      this.camera.position.set(13.5, 10.5, 15.5);
      if (animate) {
        this.introFinalPosition = this.camera.position.clone();
        this.camera.position.multiplyScalar(1.45).applyAxisAngle(new this.THREE.Vector3(0, 1, 0), -0.24);
        this.introStartPosition = this.camera.position.clone();
        this.introStartedAt = performance.now();
        this.introActive = true;
      }
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
    this.perspectiveCamera.aspect = width / height;
    this.perspectiveCamera.updateProjectionMatrix();
    const halfHeight = 8;
    this.orthographicCamera.left = -halfHeight * width / height;
    this.orthographicCamera.right = halfHeight * width / height;
    this.orthographicCamera.top = halfHeight;
    this.orthographicCamera.bottom = -halfHeight;
    this.orthographicCamera.updateProjectionMatrix();
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
    this.pixelRatioLimit = 1.5;
    this.resize();
  }

  invalidate() {
    if (this.destroyed || this.animationFrame !== null) return;
    this.animationFrame = requestAnimationFrame((now) => this.renderFrame(now));
  }

  renderFrame(now) {
    this.animationFrame = null;
    if (this.destroyed) return;
    const transitioning = this.updateTransition(now);
    const introducing = this.updateIntro(now);
    const moving = this.controls.update();
    if (transitioning || introducing || moving) this.updateLabels();
    this.renderer.render(this.scene, this.camera);
    this.monitorFrameRate(now);
    if (transitioning || introducing || moving) this.invalidate();
  }

  showError(message) {
    this.errorOverlay.textContent = String(message || "三维事件星图不可用");
    this.errorOverlay.classList.remove("hidden");
    this.errorOverlay.classList.add("flex");
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    if (this.animationFrame !== null) cancelAnimationFrame(this.animationFrame);
    this.resizeObserver?.disconnect();
    this.controls?.removeEventListener("start", this.handleControlsStart);
    this.controls?.removeEventListener("change", this.handleControlsChange);
    this.controls?.dispose();
    this.renderer?.domElement.removeEventListener("pointerdown", this.handlePointerDown);
    this.renderer?.domElement.removeEventListener("pointerup", this.handlePointerUp);
    this.renderer?.domElement.removeEventListener("dblclick", this.handleDoubleClick);
    this.renderer?.domElement.removeEventListener("webglcontextlost", this.handleContextLost);
    this.renderer?.domElement.removeEventListener("webglcontextrestored", this.handleContextRestored);
    for (const child of [...(this.storyGroup?.children || [])]) {
      child.geometry?.dispose();
      child.material?.dispose();
    }
    this.spacetimeGrid?.geometry?.dispose();
    this.spacetimeGrid?.material?.dispose();
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
