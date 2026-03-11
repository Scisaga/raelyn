import { build } from "esbuild";
import { readdirSync, rmSync, statSync } from "node:fs";
import { resolve } from "node:path";

function listFilesRecursive(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = resolve(dir, entry.name);
    if (entry.isDirectory()) out.push(...listFilesRecursive(full));
    else if (entry.isFile()) out.push(full);
  }
  return out;
}

export async function buildAppBundle({ uiDir, root, minify = true }) {
  const entry = resolve(uiDir, "src/app/index.js");
  const outfile = resolve(root, "static/app.js");

  rmSync(resolve(root, "static/js"), { recursive: true, force: true });

  await build({
    entryPoints: [entry],
    outfile,
    bundle: true,
    format: "iife",
    platform: "browser",
    target: ["es2020"],
    minify,
    sourcemap: false,
    legalComments: "none",
  });

  console.log("[ui] built:", outfile);
}

export function jsSourceSignature(uiDir) {
  const srcDir = resolve(uiDir, "src");
  const files = listFilesRecursive(srcDir).sort();
  const parts = [];
  for (const file of files) {
    const st = statSync(file);
    parts.push(`${file}:${st.size}:${st.mtimeMs}`);
  }
  return parts.join("|");
}
