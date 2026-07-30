import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const uiDir = resolve(here, "..");
const root = resolve(uiDir, "..");
const brandDir = resolve(root, "static/brand");

const eventHorizonSvg = resolve(uiDir, "assets/brand/raelyn-event-horizon.svg");
const eventHorizonPng = resolve(uiDir, "assets/brand/raelyn-event-horizon.png");
const faviconSvg = resolve(uiDir, "assets/brand/raelyn-favicon.svg");
const faviconPng = resolve(uiDir, "assets/brand/raelyn-favicon.png");
const glyphSvg = resolve(uiDir, "assets/brand/raelyn-glyph.svg");
const glyphPng = resolve(uiDir, "assets/brand/raelyn-glyph.png");

mkdirSync(brandDir, { recursive: true });

function resolveSvgRenderer() {
  const candidates = [
    process.env.FFMPEG_BIN,
    "/usr/bin/ffmpeg",
    "ffmpeg",
  ].filter((candidate, index, values) => candidate && values.indexOf(candidate) === index);

  for (const candidate of candidates) {
    try {
      const decoders = execFileSync(candidate, ["-hide_banner", "-decoders"], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      });
      if (/\blibrsvg\b/.test(decoders)) return candidate;
    } catch {
      // 继续尝试下一个本地 ffmpeg。
    }
  }
  throw new Error("[ui] 生成高清品牌 PNG 需要带 librsvg 解码器的 ffmpeg");
}

function renderSvgPng(ffmpegBin, sourcePath, outputPath, size) {
  execFileSync(
    ffmpegBin,
    [
      "-y",
      "-v",
      "error",
      "-c:v",
      "librsvg",
      "-width",
      String(size),
      "-height",
      String(size),
      "-i",
      sourcePath,
      "-frames:v",
      "1",
      "-update",
      "1",
      "-pix_fmt",
      "rgba",
      outputPath,
    ],
    { stdio: "inherit" }
  );
}

const svgRenderer = resolveSvgRenderer();
// 必须在目标尺寸直接栅格化；先按 SVG 默认 64px 解码再放大会导致边缘失焦。
renderSvgPng(svgRenderer, eventHorizonSvg, eventHorizonPng, 2048);
renderSvgPng(svgRenderer, faviconSvg, faviconPng, 1024);
renderSvgPng(svgRenderer, glyphSvg, glyphPng, 2048);

for (const outputPath of [
  resolve(root, "logo.png"),
  resolve(brandDir, "logo.png"),
  resolve(brandDir, "logo-y.png"),
]) {
  copyFileSync(eventHorizonPng, outputPath);
}
copyFileSync(glyphPng, resolve(brandDir, "logo-r.png"));

copyFileSync(eventHorizonSvg, resolve(brandDir, "raelyn-event-horizon.svg"));
copyFileSync(faviconSvg, resolve(brandDir, "raelyn-favicon.svg"));
copyFileSync(glyphSvg, resolve(brandDir, "raelyn-glyph.svg"));

console.log("[ui] 已生成 2048px 品牌/字形母版、1024px favicon 母版与高清兼容图标");
