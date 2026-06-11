import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const uiDir = resolve(here, "..");
const root = resolve(uiDir, "..");

const size = 512;
const hardAlphaThreshold = 96;
const gradientAlphaThreshold = 16;
const minComponentPixels = 64;
const defaultGlyphScale = 0.94;

const negativeLogoPath = resolve(root, "static/brand/logo.png");
const positiveLogoPath = resolve(root, "static/brand/logo-y.png");
const glyphLogoPath = resolve(root, "static/brand/logo-r.png");

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
      continue;
    }
  }
  throw new Error("ffmpeg not found");
}

function parseArgs(argv) {
  const options = {
    negativeSourcePath: negativeLogoPath,
    positiveSourcePath: positiveLogoPath,
    glyphOutputPath: glyphLogoPath,
    glyphScale: defaultGlyphScale,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--negative-source") {
      options.negativeSourcePath = resolve(process.cwd(), argv[index + 1]);
      index += 1;
      continue;
    }
    if (arg === "--positive-source") {
      options.positiveSourcePath = resolve(process.cwd(), argv[index + 1]);
      index += 1;
      continue;
    }
    if (arg === "--glyph-output") {
      options.glyphOutputPath = resolve(process.cwd(), argv[index + 1]);
      index += 1;
      continue;
    }
    if (arg === "--glyph-scale") {
      options.glyphScale = Number(argv[index + 1]);
      index += 1;
    }
  }
  if (!Number.isFinite(options.glyphScale) || options.glyphScale <= 0 || options.glyphScale > 1) {
    throw new Error(`invalid glyph scale: ${options.glyphScale}`);
  }
  return options;
}

function readPngRgba(ffmpegBin, inputPath, width, height) {
  const buffer = execFileSync(
    ffmpegBin,
    ["-v", "error", "-i", inputPath, "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
    { encoding: "buffer", maxBuffer: width * height * 8 }
  );
  const expected = width * height * 4;
  if (buffer.length !== expected) {
    throw new Error(`unexpected RGBA payload size: got ${buffer.length}, expected ${expected}`);
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
      throw new Error("singular linear system");
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
      if (alpha <= gradientAlphaThreshold) continue;
      const weight = alpha / 255;
      const nx = x / (width - 1);
      const ny = y / (height - 1);
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

function evaluateGradient(gradient, x, y) {
  const nx = x / (size - 1);
  const ny = y / (size - 1);
  return [
    clampByte(gradient.r[0] * nx + gradient.r[1] * ny + gradient.r[2]),
    clampByte(gradient.g[0] * nx + gradient.g[1] * ny + gradient.g[2]),
    clampByte(gradient.b[0] * nx + gradient.b[1] * ny + gradient.b[2]),
  ];
}

function buildAlpha(rgba) {
  const alpha = new Uint8Array(size * size);
  for (let index = 0; index < alpha.length; index += 1) {
    alpha[index] = rgba[index * 4 + 3];
  }
  return alpha;
}

function createHardMask(alpha, threshold) {
  const mask = new Uint8Array(alpha.length);
  for (let index = 0; index < alpha.length; index += 1) {
    mask[index] = alpha[index] > threshold ? 1 : 0;
  }
  return mask;
}

function findConnectedComponents(mask, width, height) {
  const labels = new Int32Array(mask.length);
  const queue = new Int32Array(mask.length);
  const components = [];
  let nextLabel = 0;

  for (let start = 0; start < mask.length; start += 1) {
    if (mask[start] === 0 || labels[start] !== 0) continue;
    nextLabel += 1;
    let head = 0;
    let tail = 0;
    queue[tail] = start;
    tail += 1;
    labels[start] = nextLabel;
    let count = 0;
    let minX = width;
    let minY = height;
    let maxX = -1;
    let maxY = -1;

    while (head < tail) {
      const current = queue[head];
      head += 1;
      const x = current % width;
      const y = Math.floor(current / width);
      count += 1;
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;

      const xStart = Math.max(0, x - 1);
      const xEnd = Math.min(width - 1, x + 1);
      const yStart = Math.max(0, y - 1);
      const yEnd = Math.min(height - 1, y + 1);
      for (let ny = yStart; ny <= yEnd; ny += 1) {
        for (let nx = xStart; nx <= xEnd; nx += 1) {
          if (nx === x && ny === y) continue;
          const neighbor = ny * width + nx;
          if (mask[neighbor] === 0 || labels[neighbor] !== 0) continue;
          labels[neighbor] = nextLabel;
          queue[tail] = neighbor;
          tail += 1;
        }
      }
    }

    components.push({
      id: nextLabel,
      count,
      minX,
      minY,
      maxX,
      maxY,
      width: maxX - minX + 1,
      height: maxY - minY + 1,
    });
  }

  return { labels, components };
}

function expandSoftLabels(alpha, hardLabels, width, height) {
  const softLabels = new Int32Array(hardLabels);
  const queue = new Int32Array(alpha.length);
  let head = 0;
  let tail = 0;

  for (let index = 0; index < alpha.length; index += 1) {
    if (softLabels[index] !== 0) {
      queue[tail] = index;
      tail += 1;
    }
  }

  while (head < tail) {
    const current = queue[head];
    head += 1;
    const x = current % width;
    const y = Math.floor(current / width);
    const label = softLabels[current];
    const xStart = Math.max(0, x - 1);
    const xEnd = Math.min(width - 1, x + 1);
    const yStart = Math.max(0, y - 1);
    const yEnd = Math.min(height - 1, y + 1);
    for (let ny = yStart; ny <= yEnd; ny += 1) {
      for (let nx = xStart; nx <= xEnd; nx += 1) {
        if (nx === x && ny === y) continue;
        const neighbor = ny * width + nx;
        if (softLabels[neighbor] !== 0 || alpha[neighbor] === 0) continue;
        softLabels[neighbor] = label;
        queue[tail] = neighbor;
        tail += 1;
      }
    }
  }

  return softLabels;
}

function chooseMainComponent(components) {
  const significant = components.filter((component) => component.count >= minComponentPixels);
  if (significant.length === 0) {
    throw new Error("no significant component found");
  }
  return significant.reduce((best, current) => {
    const currentArea = current.width * current.height;
    const bestArea = best.width * best.height;
    if (currentArea !== bestArea) return currentArea > bestArea ? current : best;
    return current.count > best.count ? current : best;
  });
}

function buildPositiveLayers(rgba) {
  const alpha = buildAlpha(rgba);
  const hard = createHardMask(alpha, hardAlphaThreshold);
  const { labels, components } = findConnectedComponents(hard, size, size);
  const border = chooseMainComponent(components);
  const softLabels = expandSoftLabels(alpha, labels, size, size);
  const glyphIds = new Set(
    components.filter((component) => component.id !== border.id && component.count >= minComponentPixels).map((component) => component.id)
  );
  const borderLayer = new Uint8ClampedArray(rgba.length);
  const glyphLayer = new Uint8ClampedArray(rgba.length);
  let minX = size;
  let minY = size;
  let maxX = -1;
  let maxY = -1;

  for (let index = 0; index < alpha.length; index += 1) {
    const label = softLabels[index];
    const pixel = index * 4;
    if (label === border.id) {
      borderLayer[pixel] = rgba[pixel];
      borderLayer[pixel + 1] = rgba[pixel + 1];
      borderLayer[pixel + 2] = rgba[pixel + 2];
      borderLayer[pixel + 3] = rgba[pixel + 3];
      continue;
    }
    if (!glyphIds.has(label)) continue;
    const x = index % size;
    const y = Math.floor(index / size);
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
    glyphLayer[pixel] = rgba[pixel];
    glyphLayer[pixel + 1] = rgba[pixel + 1];
    glyphLayer[pixel + 2] = rgba[pixel + 2];
    glyphLayer[pixel + 3] = rgba[pixel + 3];
  }

  return {
    borderLayer,
    glyphLayer,
    glyphBox: {
      minX,
      minY,
      maxX,
      maxY,
      centerX: (minX + maxX) / 2,
      centerY: (minY + maxY) / 2,
    },
  };
}

function sampleBilinearRgba(rgba, width, height, sourceX, sourceY) {
  const x0 = Math.floor(sourceX);
  const y0 = Math.floor(sourceY);
  const tx = sourceX - x0;
  const ty = sourceY - y0;
  let premultipliedR = 0;
  let premultipliedG = 0;
  let premultipliedB = 0;
  let alpha = 0;

  for (let yOffset = 0; yOffset <= 1; yOffset += 1) {
    const sy = y0 + yOffset;
    if (sy < 0 || sy >= height) continue;
    const wy = yOffset === 0 ? 1 - ty : ty;
    for (let xOffset = 0; xOffset <= 1; xOffset += 1) {
      const sx = x0 + xOffset;
      if (sx < 0 || sx >= width) continue;
      const wx = xOffset === 0 ? 1 - tx : tx;
      const weight = wx * wy;
      const index = (sy * width + sx) * 4;
      const sampleAlpha = rgba[index + 3] / 255;
      alpha += sampleAlpha * weight;
      premultipliedR += (rgba[index] / 255) * sampleAlpha * weight;
      premultipliedG += (rgba[index + 1] / 255) * sampleAlpha * weight;
      premultipliedB += (rgba[index + 2] / 255) * sampleAlpha * weight;
    }
  }

  if (alpha <= 1e-6) {
    return [0, 0, 0, 0];
  }

  return [
    clampByte((premultipliedR / alpha) * 255),
    clampByte((premultipliedG / alpha) * 255),
    clampByte((premultipliedB / alpha) * 255),
    clampByte(alpha * 255),
  ];
}

function renderScaledGlyph(glyphLayer, glyphBox, scale) {
  const output = new Uint8ClampedArray(glyphLayer.length);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const sourceX = glyphBox.centerX + (x - glyphBox.centerX) / scale;
      const sourceY = glyphBox.centerY + (y - glyphBox.centerY) / scale;
      const sample = sampleBilinearRgba(glyphLayer, size, size, sourceX, sourceY);
      const index = (y * size + x) * 4;
      output[index] = sample[0];
      output[index + 1] = sample[1];
      output[index + 2] = sample[2];
      output[index + 3] = sample[3];
    }
  }
  return output;
}

function compositeOver(background, foreground) {
  const output = new Uint8ClampedArray(background.length);
  for (let index = 0; index < background.length; index += 4) {
    const bgA = background[index + 3] / 255;
    const fgA = foreground[index + 3] / 255;
    const outA = fgA + bgA * (1 - fgA);
    if (outA <= 1e-6) continue;
    const bgR = background[index] / 255;
    const bgG = background[index + 1] / 255;
    const bgB = background[index + 2] / 255;
    const fgR = foreground[index] / 255;
    const fgG = foreground[index + 1] / 255;
    const fgB = foreground[index + 2] / 255;
    output[index] = clampByte(((fgR * fgA + bgR * bgA * (1 - fgA)) / outA) * 255);
    output[index + 1] = clampByte(((fgG * fgA + bgG * bgA * (1 - fgA)) / outA) * 255);
    output[index + 2] = clampByte(((fgB * fgA + bgB * bgA * (1 - fgA)) / outA) * 255);
    output[index + 3] = clampByte(outA * 255);
  }
  return output;
}

function floodOutside(alphaMask, width, height) {
  const outside = new Uint8Array(alphaMask.length);
  const queue = new Int32Array(alphaMask.length);
  let head = 0;
  let tail = 0;

  const push = (index) => {
    if (alphaMask[index] !== 0 || outside[index] !== 0) return;
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

function buildPlaqueAlpha(negativeRgba) {
  const alpha = buildAlpha(negativeRgba);
  const outside = floodOutside(createHardMask(alpha, 0), size, size);
  const plaque = new Uint8Array(alpha.length);
  for (let index = 0; index < plaque.length; index += 1) {
    plaque[index] = outside[index] === 0 ? 255 : 0;
  }
  return plaque;
}

function buildNegativeLogo(plaqueAlpha, scaledGlyphLayer, gradient) {
  const output = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const pixelIndex = y * size + x;
      const glyphAlpha = scaledGlyphLayer[pixelIndex * 4 + 3];
      const alpha = clampByte(plaqueAlpha[pixelIndex] * (1 - glyphAlpha / 255));
      if (alpha === 0) continue;
      const [r, g, b] = evaluateGradient(gradient, x, y);
      const index = pixelIndex * 4;
      output[index] = r;
      output[index + 1] = g;
      output[index + 2] = b;
      output[index + 3] = alpha;
    }
  }
  return output;
}

function buildStandaloneGlyph(scaledGlyphLayer) {
  const output = new Uint8ClampedArray(scaledGlyphLayer.length);
  for (let index = 0; index < scaledGlyphLayer.length; index += 4) {
    const alpha = scaledGlyphLayer[index + 3];
    if (alpha === 0) continue;
    output[index] = 3;
    output[index + 1] = 7;
    output[index + 2] = 18;
    output[index + 3] = alpha;
  }
  return output;
}

function measureAlphaBox(rgba) {
  const alpha = buildAlpha(rgba);
  let minX = size;
  let minY = size;
  let maxX = -1;
  let maxY = -1;
  for (let index = 0; index < alpha.length; index += 1) {
    if (alpha[index] === 0) continue;
    const x = index % size;
    const y = Math.floor(index / size);
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  return { bbox: [minX, minY, maxX + 1, maxY + 1] };
}

const { negativeSourcePath, positiveSourcePath, glyphOutputPath, glyphScale } = parseArgs(process.argv.slice(2));
const ffmpegBin = resolveFfmpegBinary();
const negativeSource = readPngRgba(ffmpegBin, negativeSourcePath, size, size);
const positiveSource = readPngRgba(ffmpegBin, positiveSourcePath, size, size);

const { borderLayer, glyphLayer, glyphBox } = buildPositiveLayers(positiveSource);
const scaledGlyphLayer = renderScaledGlyph(glyphLayer, glyphBox, glyphScale);
const positiveLogo = compositeOver(borderLayer, scaledGlyphLayer);

const gradient = fitLinearGradient(negativeSource, size, size);
const plaqueAlpha = buildPlaqueAlpha(negativeSource);
const negativeLogo = buildNegativeLogo(plaqueAlpha, scaledGlyphLayer, gradient);
const standaloneGlyph = buildStandaloneGlyph(scaledGlyphLayer);

writePngRgba(ffmpegBin, positiveLogoPath, size, size, positiveLogo);
writePngRgba(ffmpegBin, negativeLogoPath, size, size, negativeLogo);
writePngRgba(ffmpegBin, glyphOutputPath, size, size, standaloneGlyph);

console.log(
  JSON.stringify(
    {
      glyphScale,
      negativeSourcePath,
      positiveSourcePath,
      glyphOutputPath,
      glyphBox,
      logo: measureAlphaBox(negativeLogo),
      logoY: measureAlphaBox(positiveLogo),
      logoR: measureAlphaBox(standaloneGlyph),
    },
    null,
    2
  )
);
