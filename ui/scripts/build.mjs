import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { buildIndexHtml } from "./html.mjs";
import { buildAppBundle } from "./js-bundle.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const uiDir = resolve(here, "..");
const root = resolve(uiDir, "..");

const cssDir = resolve(root, "static/css");
const cssOut = resolve(cssDir, "tailwind.min.css");
const vendorDir = resolve(root, "static/vendor");
const dataDir = resolve(root, "static/data");
const brandDir = resolve(root, "static/brand");
const pwaDir = resolve(root, "static/pwa");
const timelineVendorDir = resolve(vendorDir, "timelinejs");
const brandSourceSvg = resolve(uiDir, "assets/brand/raelyn-event-horizon.svg");
const faviconSourceSvg = resolve(uiDir, "assets/brand/raelyn-favicon.svg");
const glyphSourceSvg = resolve(uiDir, "assets/brand/raelyn-glyph.svg");
// 项目内置 ffmpeg 不含 librsvg；常规构建从已直接矢量栅格化的高清 PNG 母版派生固定尺寸。
const brandSourcePng = resolve(uiDir, "assets/brand/raelyn-event-horizon.png");
const faviconSourcePng = resolve(uiDir, "assets/brand/raelyn-favicon.png");
const glyphSourcePng = resolve(uiDir, "assets/brand/raelyn-glyph.png");
const brandOutputSvg = resolve(brandDir, "raelyn-event-horizon.svg");
const faviconOutputSvg = resolve(brandDir, "raelyn-favicon.svg");
const glyphOutputSvg = resolve(brandDir, "raelyn-glyph.svg");

mkdirSync(cssDir, { recursive: true });
mkdirSync(vendorDir, { recursive: true });
mkdirSync(dataDir, { recursive: true });
mkdirSync(brandDir, { recursive: true });
mkdirSync(pwaDir, { recursive: true });
rmSync(timelineVendorDir, { recursive: true, force: true });

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
copyFileSync(
  resolve(uiDir, "assets/startup-event-field.bin"),
  resolve(dataDir, "startup-event-field.bin")
);
copyFileSync(brandSourceSvg, brandOutputSvg);
copyFileSync(faviconSourceSvg, faviconOutputSvg);
copyFileSync(glyphSourceSvg, glyphOutputSvg);

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
  throw new Error("[ui] ffmpeg not found; required to generate brand icon sizes");
}

function renderBrandPng(ffmpegBin, sourcePath, outputPath, size) {
  execFileSync(
    ffmpegBin,
    [
      "-y",
      "-v",
      "error",
      "-i",
      sourcePath,
      "-vf",
      `scale=${size}:${size}:flags=lanczos`,
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

function buildBrandAssets() {
  const ffmpegBin = resolveFfmpegBinary();
  for (const outputPath of [
    resolve(root, "logo.png"),
    resolve(brandDir, "logo.png"),
    resolve(brandDir, "logo-y.png"),
  ]) {
    copyFileSync(brandSourcePng, outputPath);
  }
  copyFileSync(glyphSourcePng, resolve(brandDir, "logo-r.png"));
  renderBrandPng(ffmpegBin, faviconSourcePng, resolve(brandDir, "favicon-32.png"), 32);
  renderBrandPng(ffmpegBin, brandSourcePng, resolve(pwaDir, "icon-192.png"), 192);
  renderBrandPng(ffmpegBin, brandSourcePng, resolve(pwaDir, "icon-512.png"), 512);
  renderBrandPng(ffmpegBin, brandSourcePng, resolve(pwaDir, "icon-maskable-512.png"), 512);
  renderBrandPng(ffmpegBin, brandSourcePng, resolve(pwaDir, "apple-touch-icon.png"), 180);

  copyFileSync(resolve(uiDir, "pwa/manifest.webmanifest"), resolve(root, "static/manifest.webmanifest"));
  copyFileSync(resolve(uiDir, "pwa/sw.js"), resolve(root, "static/sw.js"));
  console.log("[ui] built:", brandOutputSvg);
  console.log("[ui] built:", faviconOutputSvg);
  console.log("[ui] built:", glyphOutputSvg);
  console.log("[ui] built:", resolve(root, "static/manifest.webmanifest"));
  console.log("[ui] built:", resolve(root, "static/sw.js"));
}

console.log("[ui] built:", cssOut);
buildBrandAssets();

await buildAppBundle({ uiDir, root, minify: true });
const assetVersion = createHash("sha256")
  .update(readFileSync(cssOut))
  .update(readFileSync(resolve(root, "static/app.js")))
  .update(readFileSync(brandSourceSvg))
  .update(readFileSync(brandSourcePng))
  .update(readFileSync(faviconSourceSvg))
  .update(readFileSync(faviconSourcePng))
  .update(readFileSync(glyphSourceSvg))
  .update(readFileSync(glyphSourcePng))
  .digest("hex")
  .slice(0, 12);
buildIndexHtml({ uiDir, root, assetVersion });
