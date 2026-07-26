const THREE_MODULE_URL = "/static/vendor/event-map-three.js";
const PREVIEW_DATA_URL = "/static/data/startup-event-field.bin";
const HEADER_SIZE = 24;
const RECORD_SIZE = 16;
const MAGIC = "RLYNSTR1";
const LOOP_DURATION_MS = 24_000;

const INACTIVE_HANDLE = {
  active: false,
  destroy() {},
};

let threeModulePromise = null;
let previewDataPromise = null;

function loadThreeModule() {
  if (!threeModulePromise) threeModulePromise = import(THREE_MODULE_URL);
  return threeModulePromise;
}

function loadPreviewData() {
  if (!previewDataPromise) {
    previewDataPromise = fetch(PREVIEW_DATA_URL, { cache: "force-cache" })
      .then((response) => {
        if (!response.ok) throw new Error(`启动星域数据加载失败：${response.status}`);
        return response.arrayBuffer();
      })
      .then(parseStartupStarfield);
  }
  return previewDataPromise;
}

export function parseStartupStarfield(buffer) {
  if (!(buffer instanceof ArrayBuffer)) throw new TypeError("启动星域数据必须是 ArrayBuffer");
  if (buffer.byteLength < HEADER_SIZE) throw new Error("启动星域数据头不完整");
  const magic = new TextDecoder().decode(new Uint8Array(buffer, 0, 8));
  if (magic !== MAGIC) throw new Error("启动星域数据标识不匹配");

  const view = new DataView(buffer);
  const version = view.getUint16(8, true);
  const recordSize = view.getUint16(10, true);
  const count = view.getUint32(12, true);
  const minDay = view.getInt32(16, true);
  const maxDay = view.getInt32(20, true);
  if (version !== 1 || recordSize !== RECORD_SIZE) throw new Error("启动星域数据版本不支持");
  if (buffer.byteLength !== HEADER_SIZE + count * recordSize) throw new Error("启动星域数据长度不匹配");
  if (count <= 0 || maxDay <= minDay) throw new Error("启动星域数据范围为空");

  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const days = new Float32Array(count);
  const weights = new Float32Array(count);
  const seeds = new Float32Array(count);
  const daySpan = maxDay - minDay;
  for (let index = 0; index < count; index += 1) {
    const offset = HEADER_SIZE + index * recordSize;
    const pointOffset = index * 3;
    positions[pointOffset] = view.getInt16(offset, true) / 32767;
    positions[pointOffset + 1] = view.getInt16(offset + 2, true) / 32767;
    positions[pointOffset + 2] = view.getInt16(offset + 4, true) / 32767;
    days[index] = (view.getInt32(offset + 6, true) - minDay) / daySpan;
    colors[pointOffset] = view.getUint8(offset + 10) / 255;
    colors[pointOffset + 1] = view.getUint8(offset + 11) / 255;
    colors[pointOffset + 2] = view.getUint8(offset + 12) / 255;
    weights[index] = view.getUint8(offset + 13) / 255;
    seeds[index] = view.getUint16(offset + 14, true) / 65535;
  }
  return { count, minDay, maxDay, positions, colors, days, weights, seeds };
}

function selectPreviewPoints(data, limit) {
  const count = Math.min(data.count, Math.max(1, limit));
  if (count === data.count) return data;
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const days = new Float32Array(count);
  const weights = new Float32Array(count);
  const seeds = new Float32Array(count);
  const stride = data.count / count;
  for (let index = 0; index < count; index += 1) {
    const source = Math.min(data.count - 1, Math.floor(index * stride));
    positions.set(data.positions.subarray(source * 3, source * 3 + 3), index * 3);
    colors.set(data.colors.subarray(source * 3, source * 3 + 3), index * 3);
    days[index] = data.days[source];
    weights[index] = data.weights[source];
    seeds[index] = data.seeds[source];
  }
  return { ...data, count, positions, colors, days, weights, seeds };
}

function createStarMaterial(THREE, pixelRatio) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uProgress: { value: 0.68 },
      uWindow: { value: 0.22 },
      uPixelRatio: { value: pixelRatio },
    },
    vertexShader: `
      precision highp float;
      attribute vec3 aColor;
      attribute float aDay;
      attribute float aWeight;
      attribute float aSeed;
      uniform float uProgress;
      uniform float uWindow;
      uniform float uPixelRatio;
      varying vec3 vColor;
      varying float vAlpha;

      void main() {
        float age = mod(uProgress - aDay + 1.0, 1.0);
        float arrival = smoothstep(0.0, 0.018, age);
        float departure = 1.0 - smoothstep(uWindow * 0.64, uWindow, age);
        float visibility = arrival * departure;
        float twinkle = 0.88 + 0.12 * sin((uProgress + aSeed) * 37.6991118);
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        float depthFade = 1.0 - smoothstep(2.2, 8.0, -mvPosition.z);
        vColor = aColor;
        vAlpha = (0.035 + visibility * (0.58 + aWeight * 0.34)) * twinkle * depthFade;
        float pointSize = (1.05 + visibility * 1.28 + aWeight * 1.3) * (6.4 / max(2.4, -mvPosition.z));
        gl_PointSize = clamp(pointSize * uPixelRatio, 1.0, 5.2 * uPixelRatio);
        gl_Position = projectionMatrix * mvPosition;
      }
    `,
    fragmentShader: `
      precision highp float;
      varying vec3 vColor;
      varying float vAlpha;

      void main() {
        float radius = length(gl_PointCoord - vec2(0.5));
        if (radius > 0.5) discard;
        float halo = smoothstep(0.5, 0.06, radius);
        float core = smoothstep(0.18, 0.0, radius);
        vec3 color = mix(vColor, vec3(0.94, 0.98, 1.0), core * 0.58);
        gl_FragColor = vec4(color, halo * vAlpha);
      }
    `,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.AdditiveBlending,
  });
}

export function mountStartupStarfield({ canvas } = {}) {
  if (!canvas || typeof canvas.getContext !== "function") return INACTIVE_HANDLE;

  let destroyed = false;
  let renderer = null;
  let geometry = null;
  let material = null;
  let points = null;
  let scene = null;
  let camera = null;
  let resizeObserver = null;
  let rafId = 0;
  let startTime = 0;
  const reduceMotion =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const mobile =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(max-width: 767px)").matches;

  function stopFrame() {
    if (rafId && typeof window !== "undefined") window.cancelAnimationFrame(rafId);
    rafId = 0;
  }

  function resize() {
    if (!renderer || !camera) return;
    const width = Math.max(1, canvas.clientWidth || window.innerWidth || 1);
    const height = Math.max(1, canvas.clientHeight || window.innerHeight || 1);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.fov = camera.aspect < 0.8 ? 52 : 43;
    camera.updateProjectionMatrix();
    if (points) {
      points.scale.setScalar(camera.aspect < 0.8 ? 2.4 : 3.15);
      points.position.x = camera.aspect > 1.3 ? -0.5 : 0;
    }
  }

  function renderFrame(now) {
    if (destroyed || !renderer || !scene || !camera || !material || !points) return;
    const elapsed = reduceMotion ? LOOP_DURATION_MS * 0.68 : Math.max(0, now - startTime);
    const progress = (elapsed / LOOP_DURATION_MS + 0.64) % 1;
    material.uniforms.uProgress.value = progress;
    const orbit = reduceMotion ? 0.08 : Math.sin(elapsed / LOOP_DURATION_MS * Math.PI * 2) * 0.12;
    camera.position.set(orbit, 0.1 + orbit * 0.24, 5.25);
    camera.lookAt(0, 0, 0);
    points.rotation.y = -0.18 + orbit * 0.7;
    points.rotation.x = -0.1 + orbit * 0.18;
    renderer.render(scene, camera);
    if (!reduceMotion && document.visibilityState !== "hidden") {
      rafId = window.requestAnimationFrame(renderFrame);
    }
  }

  function startFrame() {
    if (destroyed || reduceMotion || rafId || !renderer) return;
    startTime = performance.now();
    rafId = window.requestAnimationFrame(renderFrame);
  }

  function handleVisibility() {
    if (document.visibilityState === "hidden") {
      stopFrame();
      return;
    }
    startFrame();
  }

  async function initialize() {
    try {
      const [modules, sourceData] = await Promise.all([loadThreeModule(), loadPreviewData()]);
      if (destroyed) return;
      const { THREE } = modules;
      const data = selectPreviewPoints(sourceData, mobile ? 1800 : 6000);
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.5);
      renderer = new THREE.WebGLRenderer({
        canvas,
        alpha: true,
        antialias: false,
        powerPreference: "high-performance",
      });
      renderer.setPixelRatio(pixelRatio);
      renderer.setClearColor(0x01040b, 0);
      renderer.outputColorSpace = THREE.SRGBColorSpace;

      scene = new THREE.Scene();
      camera = new THREE.PerspectiveCamera(43, 1, 0.1, 30);
      geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(data.positions, 3));
      geometry.setAttribute("aColor", new THREE.BufferAttribute(data.colors, 3));
      geometry.setAttribute("aDay", new THREE.BufferAttribute(data.days, 1));
      geometry.setAttribute("aWeight", new THREE.BufferAttribute(data.weights, 1));
      geometry.setAttribute("aSeed", new THREE.BufferAttribute(data.seeds, 1));
      material = createStarMaterial(THREE, pixelRatio);
      points = new THREE.Points(geometry, material);
      scene.add(points);

      resize();
      if (typeof ResizeObserver === "function") {
        resizeObserver = new ResizeObserver(resize);
        resizeObserver.observe(canvas);
      } else {
        window.addEventListener("resize", resize, { passive: true });
      }
      document.addEventListener("visibilitychange", handleVisibility);
      startTime = performance.now();
      if (reduceMotion) renderFrame(startTime);
      else rafId = window.requestAnimationFrame(renderFrame);
    } catch {
      // 静态渐变是启动门面的明确降级路径。
    }
  }

  void initialize();

  return {
    active: true,
    destroy() {
      if (destroyed) return;
      destroyed = true;
      stopFrame();
      document.removeEventListener("visibilitychange", handleVisibility);
      if (resizeObserver) resizeObserver.disconnect();
      else if (typeof window !== "undefined") window.removeEventListener("resize", resize);
      if (scene && points) scene.remove(points);
      if (geometry) geometry.dispose();
      if (material) material.dispose();
      if (renderer) renderer.dispose();
      renderer = null;
      scene = null;
      camera = null;
      points = null;
      geometry = null;
      material = null;
    },
  };
}
