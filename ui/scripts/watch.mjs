import { spawn } from "node:child_process";
import { mkdirSync, copyFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { buildIndexHtml, templatesSignature } from "./html.mjs";
import { buildAppBundle, jsSourceSignature } from "./js-bundle.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const uiDir = resolve(here, "..");
const root = resolve(uiDir, "..");

const cssOut = resolve(root, "static/css/tailwind.min.css");
const vendorDir = resolve(root, "static/vendor");
const dataDir = resolve(root, "static/data");
const brandDir = resolve(root, "static/brand");

mkdirSync(resolve(root, "static/css"), { recursive: true });
mkdirSync(vendorDir, { recursive: true });
mkdirSync(dataDir, { recursive: true });
mkdirSync(brandDir, { recursive: true });

copyFileSync(resolve(uiDir, "node_modules/alpinejs/dist/cdn.min.js"), resolve(vendorDir, "alpine.min.js"));
copyFileSync(
  resolve(uiDir, "node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js"),
  resolve(vendorDir, "lightweight-charts.min.js")
);
copyFileSync(
  resolve(uiDir, "assets/startup-event-field.bin"),
  resolve(dataDir, "startup-event-field.bin")
);
copyFileSync(
  resolve(uiDir, "assets/brand/raelyn-event-horizon.svg"),
  resolve(brandDir, "raelyn-event-horizon.svg")
);
copyFileSync(
  resolve(uiDir, "assets/brand/raelyn-favicon.svg"),
  resolve(brandDir, "raelyn-favicon.svg")
);

await buildAppBundle({ uiDir, root, minify: false });
buildIndexHtml({ uiDir, root, assetVersion: "dev" });

const tailwindCli = resolve(uiDir, "node_modules/.bin/tailwindcss");
const args = ["-i", resolve(uiDir, "input.css"), "-o", cssOut, "--minify", "--watch"];
const child = spawn(tailwindCli, args, { stdio: "inherit", cwd: uiDir });
child.on("exit", (code) => process.exit(code ?? 0));

let lastSig = "";
try {
  lastSig = templatesSignature(uiDir);
} catch {
  // ignore
}

let lastJsSig = "";
try {
  lastJsSig = jsSourceSignature(uiDir);
} catch {
  // ignore
}

setInterval(() => {
  let sig = "";
  try {
    sig = templatesSignature(uiDir);
  } catch (err) {
    console.error("[ui] template scan failed:", err);
    return;
  }
  if (sig && sig !== lastSig) {
    lastSig = sig;
    try {
      buildIndexHtml({ uiDir, root, assetVersion: "dev" });
    } catch (err) {
      console.error("[ui] html build failed:", err);
    }
  }

  let jsSig = "";
  try {
    jsSig = jsSourceSignature(uiDir);
  } catch (err) {
    console.error("[ui] js source scan failed:", err);
    return;
  }
  if (jsSig && jsSig !== lastJsSig) {
    lastJsSig = jsSig;
    try {
      await buildAppBundle({ uiDir, root, minify: false });
    } catch (err) {
      console.error("[ui] js build failed:", err);
    }
  }
}, 750);
