import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const uiDir = resolve(here, "..");
const root = resolve(uiDir, "..");
const vendorDir = resolve(root, "static/vendor");
mkdirSync(vendorDir, { recursive: true });
copyFileSync(
  resolve(uiDir, "node_modules/alpinejs/dist/cdn.min.js"),
  resolve(vendorDir, "alpine.min.js")
);
console.log("[ui] ensured alpine.min.js");
