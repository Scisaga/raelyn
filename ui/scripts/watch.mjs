import { spawn } from "node:child_process";
import { mkdirSync, copyFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { buildIndexHtml, templatesSignature } from "./html.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const uiDir = resolve(here, "..");
const root = resolve(uiDir, "..");

const cssOut = resolve(root, "static/css/tailwind.min.css");
const vendorDir = resolve(root, "static/vendor");

mkdirSync(resolve(root, "static/css"), { recursive: true });
mkdirSync(vendorDir, { recursive: true });

copyFileSync(resolve(uiDir, "node_modules/alpinejs/dist/cdn.min.js"), resolve(vendorDir, "alpine.min.js"));
copyFileSync(
  resolve(uiDir, "node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js"),
  resolve(vendorDir, "lightweight-charts.min.js")
);

buildIndexHtml({ uiDir, root });

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
      buildIndexHtml({ uiDir, root });
    } catch (err) {
      console.error("[ui] html build failed:", err);
    }
  }
}, 750);
