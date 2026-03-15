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

function buildPwaAssets() {
  const ffmpegBin = resolveFfmpegBinary();
  const logoPng = resolve(root, "static/brand/logo.png");
  const icon192 = resolve(pwaDir, "icon-192.png");
  const icon512 = resolve(pwaDir, "icon-512.png");
  const iconMaskable512 = resolve(pwaDir, "icon-maskable-512.png");
  const appleTouch = resolve(pwaDir, "apple-touch-icon.png");

  if (!existsSync(logoPng)) {
    throw new Error(`[ui] missing logo asset for PWA icons: ${logoPng}`);
  }

  copyFileSync(logoPng, icon512);
  copyFileSync(logoPng, iconMaskable512);
  execFileSync(ffmpegBin, ["-y", "-i", logoPng, "-vf", "scale=192:192:flags=lanczos", icon192], { stdio: "inherit" });
  execFileSync(ffmpegBin, ["-y", "-i", logoPng, "-vf", "scale=180:180:flags=lanczos", appleTouch], { stdio: "inherit" });

  copyFileSync(resolve(uiDir, "pwa/manifest.webmanifest"), resolve(root, "static/manifest.webmanifest"));
  copyFileSync(resolve(uiDir, "pwa/sw.js"), resolve(root, "static/sw.js"));
  console.log("[ui] built:", resolve(root, "static/manifest.webmanifest"));
  console.log("[ui] built:", resolve(root, "static/sw.js"));
}

console.log("[ui] built:", cssOut);
buildPwaAssets();

buildIndexHtml({ uiDir, root });
await buildAppBundle({ uiDir, root, minify: true });
