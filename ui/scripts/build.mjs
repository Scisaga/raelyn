import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { buildIndexHtml } from "./html.mjs";
import { buildAppBundle } from "./js-bundle.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const uiDir = resolve(here, "..");
const root = resolve(uiDir, "..");

const cssOut = resolve(root, "static/css/tailwind.min.css");
const vendorDir = resolve(root, "static/vendor");
const pwaDir = resolve(root, "static/pwa");
const pwaGlyphColor = [2, 6, 23];

mkdirSync(resolve(root, "static/css"), { recursive: true });
mkdirSync(vendorDir, { recursive: true });
mkdirSync(pwaDir, { recursive: true });

const tailwindCli = resolve(uiDir, "node_modules/.bin/tailwindcss");
execFileSync(tailwindCli, ["-i", resolve(uiDir, "input.css"), "-o", cssOut, "--minify"], {
  stdio: "inherit",
  cwd: uiDir,
});

copyFileSync(
  resolve(uiDir, "node_modules/alpinejs/dist/cdn.min.js"),
  resolve(vendorDir, "alpine.min.js")
);

copyFileSync(
  resolve(uiDir, "node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js"),
  resolve(vendorDir, "lightweight-charts.min.js")
);

function resolveFfmpegBinary() {
  const candidates = [
    process.env.FFMPEG_BIN,
    resolve(root, "bin/ffmpeg"),
    "ffmpeg",
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      execFileSync(candidate, ["-version"], { stdio: "ignore" });
      return candidate;
    } catch {
      // try next candidate
    }
  }
  throw new Error("[ui] ffmpeg not found; required to generate PWA icon sizes");
}

function clampByte(value) {
  if (value <= 0) return 0;
  if (value >= 255) return 255;
  return Math.round(value);
}

function solve3x3(matrix, vector) {
  const rows = [
    [matrix[0][0], matrix[0][1], matrix[0][2], vector[0]],
    [matrix[1][0], matrix[1][1], matrix[1][2], vector[1]],
    [matrix[2][0], matrix[2][1], matrix[2][2], vector[2]],
  ];
  for (let pivot = 0; pivot < 3; pivot += 1) {
    let bestRow = pivot;
    for (let row = pivot + 1; row < 3; row += 1) {
      if (Math.abs(rows[row][pivot]) > Math.abs(rows[bestRow][pivot])) {
        bestRow = row;
      }
    }
    if (Math.abs(rows[bestRow][pivot]) < 1e-9) {
      throw new Error("[ui] failed to fit PWA gradient");
    }
    if (bestRow !== pivot) {
      const temp = rows[pivot];
      rows[pivot] = rows[bestRow];
      rows[bestRow] = temp;
    }
    const pivotValue = rows[pivot][pivot];
    for (let col = pivot; col < 4; col += 1) {
      rows[pivot][col] /= pivotValue;
    }
    for (let row = 0; row < 3; row += 1) {
      if (row === pivot) continue;
      const factor = rows[row][pivot];
      for (let col = pivot; col < 4; col += 1) {
        rows[row][col] -= factor * rows[pivot][col];
      }
    }
  }
  return [rows[0][3], rows[1][3], rows[2][3]];
}

function readPngRgba(ffmpegBin, inputPath, width, height) {
  const buffer = execFileSync(
    ffmpegBin,
    [
      "-v",
      "error",
      "-i",
      inputPath,
      "-vf",
      `scale=${width}:${height}:flags=lanczos`,
      "-f",
      "rawvideo",
      "-pix_fmt",
      "rgba",
      "pipe:1",
    ],
    { encoding: "buffer", maxBuffer: width * height * 8 }
  );
  const expected = width * height * 4;
  if (buffer.length !== expected) {
    throw new Error(`[ui] unexpected RGBA payload size: got ${buffer.length}, expected ${expected}`);
  }
  return new Uint8ClampedArray(buffer);
}

function writePngRgba(ffmpegBin, outputPath, width, height, rgba) {
  execFileSync(
    ffmpegBin,
    [
      "-y",
      "-v",
      "error",
      "-f",
      "rawvideo",
      "-pix_fmt",
      "rgba",
      "-video_size",
      `${width}x${height}`,
      "-i",
      "pipe:0",
      "-frames:v",
      "1",
      "-update",
      "1",
      outputPath,
    ],
    { input: Buffer.from(rgba.buffer, rgba.byteOffset, rgba.byteLength), maxBuffer: width * height * 8 }
  );
}

function fitLinearGradient(rgba, width, height) {
  let sumW = 0;
  let sumX = 0;
  let sumY = 0;
  let sumXX = 0;
  let sumXY = 0;
  let sumYY = 0;
  let sumR = 0;
  let sumG = 0;
  let sumB = 0;
  let sumXR = 0;
  let sumXG = 0;
  let sumXB = 0;
  let sumYR = 0;
  let sumYG = 0;
  let sumYB = 0;

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = (y * width + x) * 4;
      const alpha = rgba[index + 3];
      if (alpha <= 16) continue;
      const weight = alpha / 255;
      const nx = x / Math.max(1, width - 1);
      const ny = y / Math.max(1, height - 1);
      const r = rgba[index];
      const g = rgba[index + 1];
      const b = rgba[index + 2];

      sumW += weight;
      sumX += weight * nx;
      sumY += weight * ny;
      sumXX += weight * nx * nx;
      sumXY += weight * nx * ny;
      sumYY += weight * ny * ny;
      sumR += weight * r;
      sumG += weight * g;
      sumB += weight * b;
      sumXR += weight * nx * r;
      sumXG += weight * nx * g;
      sumXB += weight * nx * b;
      sumYR += weight * ny * r;
      sumYG += weight * ny * g;
      sumYB += weight * ny * b;
    }
  }

  const matrix = [
    [sumXX, sumXY, sumX],
    [sumXY, sumYY, sumY],
    [sumX, sumY, sumW],
  ];

  return {
    r: solve3x3(matrix, [sumXR, sumYR, sumR]),
    g: solve3x3(matrix, [sumXG, sumYG, sumG]),
    b: solve3x3(matrix, [sumXB, sumYB, sumB]),
  };
}

function evaluateGradient(gradient, x, y, width, height) {
  const nx = x / Math.max(1, width - 1);
  const ny = y / Math.max(1, height - 1);
  return [
    clampByte(gradient.r[0] * nx + gradient.r[1] * ny + gradient.r[2]),
    clampByte(gradient.g[0] * nx + gradient.g[1] * ny + gradient.g[2]),
    clampByte(gradient.b[0] * nx + gradient.b[1] * ny + gradient.b[2]),
  ];
}

function floodOutside(alpha, width, height) {
  const outside = new Uint8Array(width * height);
  const queue = new Int32Array(width * height);
  let head = 0;
  let tail = 0;

  const push = (index) => {
    if (index < 0 || index >= alpha.length) return;
    if (outside[index] !== 0 || alpha[index] > 16) return;
    outside[index] = 1;
    queue[tail] = index;
    tail += 1;
  };

  for (let x = 0; x < width; x += 1) {
    push(x);
    push((height - 1) * width + x);
  }
  for (let y = 0; y < height; y += 1) {
    push(y * width);
    push(y * width + (width - 1));
  }

  while (head < tail) {
    const current = queue[head];
    head += 1;
    const x = current % width;
    const y = Math.floor(current / width);
    if (x > 0) push(current - 1);
    if (x + 1 < width) push(current + 1);
    if (y > 0) push(current - width);
    if (y + 1 < height) push(current + width);
  }

  return outside;
}

function extractGlyphAlpha(rgba, width, height) {
  const sourceAlpha = new Uint8ClampedArray(width * height);
  for (let index = 0; index < sourceAlpha.length; index += 1) {
    sourceAlpha[index] = rgba[index * 4 + 3];
  }

  const outside = floodOutside(sourceAlpha, width, height);
  const glyphAlpha = new Uint8ClampedArray(width * height);
  for (let index = 0; index < glyphAlpha.length; index += 1) {
    if (outside[index] !== 0) continue;
    glyphAlpha[index] = clampByte(255 - sourceAlpha[index]);
  }

  return glyphAlpha;
}

function sampleBilinearAlpha(alpha, width, height, sourceX, sourceY) {
  const x0 = Math.floor(sourceX);
  const y0 = Math.floor(sourceY);
  const tx = sourceX - x0;
  const ty = sourceY - y0;
  let output = 0;

  for (let yOffset = 0; yOffset <= 1; yOffset += 1) {
    const sy = y0 + yOffset;
    if (sy < 0 || sy >= height) continue;
    const wy = yOffset === 0 ? 1 - ty : ty;
    for (let xOffset = 0; xOffset <= 1; xOffset += 1) {
      const sx = x0 + xOffset;
      if (sx < 0 || sx >= width) continue;
      const wx = xOffset === 0 ? 1 - tx : tx;
      output += alpha[sy * width + sx] * wx * wy;
    }
  }

  return clampByte(output);
}

function renderPwaIcon({ gradient, glyphAlpha, sourceSize, outputSize, glyphScale }) {
  const output = new Uint8ClampedArray(outputSize * outputSize * 4);
  const destCenter = (outputSize - 1) / 2;
  const sourceCenter = (sourceSize - 1) / 2;

  for (let y = 0; y < outputSize; y += 1) {
    for (let x = 0; x < outputSize; x += 1) {
      const pixel = (y * outputSize + x) * 4;
      const [bgR, bgG, bgB] = evaluateGradient(gradient, x, y, outputSize, outputSize);
      output[pixel] = bgR;
      output[pixel + 1] = bgG;
      output[pixel + 2] = bgB;
      output[pixel + 3] = 255;

      const sourceX = sourceCenter + (x - destCenter) / glyphScale;
      const sourceY = sourceCenter + (y - destCenter) / glyphScale;
      const glyphPixelAlpha = sampleBilinearAlpha(glyphAlpha, sourceSize, sourceSize, sourceX, sourceY);
      if (glyphPixelAlpha <= 0) continue;

      const fgAlpha = glyphPixelAlpha / 255;
      output[pixel] = clampByte(pwaGlyphColor[0] * fgAlpha + bgR * (1 - fgAlpha));
      output[pixel + 1] = clampByte(pwaGlyphColor[1] * fgAlpha + bgG * (1 - fgAlpha));
      output[pixel + 2] = clampByte(pwaGlyphColor[2] * fgAlpha + bgB * (1 - fgAlpha));
    }
  }

  return output;
}

function buildPwaAssets() {
  const ffmpegBin = resolveFfmpegBinary();
  const pwaSourcePng = resolve(root, "static/brand/logo.png");
  const icon192 = resolve(pwaDir, "icon-192.png");
  const icon512 = resolve(pwaDir, "icon-512.png");
  const iconMaskable512 = resolve(pwaDir, "icon-maskable-512.png");
  const appleTouch = resolve(pwaDir, "apple-touch-icon.png");

  if (!existsSync(pwaSourcePng)) {
    throw new Error(`[ui] missing PWA icon source asset: ${pwaSourcePng}`);
  }

  const sourceSize = 512;
  const sourceRgba = readPngRgba(ffmpegBin, pwaSourcePng, sourceSize, sourceSize);
  const gradient = fitLinearGradient(sourceRgba, sourceSize, sourceSize);
  const glyphAlpha = extractGlyphAlpha(sourceRgba, sourceSize, sourceSize);

  writePngRgba(
    ffmpegBin,
    icon192,
    192,
    192,
    renderPwaIcon({ gradient, glyphAlpha, sourceSize, outputSize: 192, glyphScale: 0.8 })
  );
  writePngRgba(
    ffmpegBin,
    icon512,
    512,
    512,
    renderPwaIcon({ gradient, glyphAlpha, sourceSize, outputSize: 512, glyphScale: 0.8 })
  );
  writePngRgba(
    ffmpegBin,
    iconMaskable512,
    512,
    512,
    renderPwaIcon({ gradient, glyphAlpha, sourceSize, outputSize: 512, glyphScale: 0.68 })
  );
  writePngRgba(
    ffmpegBin,
    appleTouch,
    180,
    180,
    renderPwaIcon({ gradient, glyphAlpha, sourceSize, outputSize: 180, glyphScale: 0.78 })
  );

  copyFileSync(resolve(uiDir, "pwa/manifest.webmanifest"), resolve(root, "static/manifest.webmanifest"));
  copyFileSync(resolve(uiDir, "pwa/sw.js"), resolve(root, "static/sw.js"));
  console.log("[ui] built:", resolve(root, "static/manifest.webmanifest"));
  console.log("[ui] built:", resolve(root, "static/sw.js"));
}

console.log("[ui] built:", cssOut);
buildPwaAssets();

buildIndexHtml({ uiDir, root });
await buildAppBundle({ uiDir, root, minify: true });
