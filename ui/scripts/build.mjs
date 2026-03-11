import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { buildIndexHtml } from "./html.mjs";
import { buildAppBundle } from "./js-bundle.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const uiDir = resolve(here, "..");
const root = resolve(uiDir, "..");

const cssOut = resolve(root, "static/css/tailwind.min.css");
const vendorDir = resolve(root, "static/vendor");

mkdirSync(resolve(root, "static/css"), { recursive: true });
mkdirSync(vendorDir, { recursive: true });

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

console.log("[ui] built:", cssOut);

buildIndexHtml({ uiDir, root });
await buildAppBundle({ uiDir, root, minify: true });
