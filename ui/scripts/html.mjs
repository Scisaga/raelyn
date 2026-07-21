import { readFileSync, readdirSync, statSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";

const includeRe = /<!--\s*@include\s+(.+?)\s*-->/g;

function normalizeIncludePath(raw) {
  const trimmed = raw.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1).trim();
  }
  return trimmed;
}

function assertInsideRoot(rootDir, filePath) {
  const rel = relative(rootDir, filePath);
  if (!rel || rel.startsWith("..") || rel.includes("..")) {
    throw new Error(`[ui] include path escapes root: ${filePath}`);
  }
}

function assembleFile(filePath, rootDir, stack) {
  assertInsideRoot(rootDir, filePath);
  if (stack.includes(filePath)) {
    throw new Error(`[ui] circular include detected:\n${[...stack, filePath].join(" -> ")}`);
  }

  const nextStack = [...stack, filePath];
  const src = readFileSync(filePath, "utf8");

  return src.replace(includeRe, (_, includeRaw) => {
    const includePath = normalizeIncludePath(includeRaw);
    const resolved = resolve(dirname(filePath), includePath);
    return assembleFile(resolved, rootDir, nextStack);
  });
}

export function buildIndexHtml({ uiDir, root, assetVersion = "dev" }) {
  const templatesRoot = resolve(uiDir, "templates/app");
  const entry = resolve(templatesRoot, "index.html");
  const outFile = resolve(root, "static/index.html");

  mkdirSync(dirname(outFile), { recursive: true });
  const html = assembleFile(entry, templatesRoot, []).replaceAll(
    "__RAELYN_ASSET_VERSION__",
    encodeURIComponent(String(assetVersion || "dev"))
  );
  writeFileSync(outFile, html, "utf8");
  console.log("[ui] built:", outFile);
}

function listFilesRecursive(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = resolve(dir, entry.name);
    if (entry.isDirectory()) out.push(...listFilesRecursive(full));
    else if (entry.isFile()) out.push(full);
  }
  return out;
}

export function templatesSignature(uiDir) {
  const templatesRoot = resolve(uiDir, "templates/app");
  const files = listFilesRecursive(templatesRoot).sort();
  const parts = [];
  for (const file of files) {
    const st = statSync(file);
    parts.push(`${relative(templatesRoot, file)}:${st.size}:${st.mtimeMs}`);
  }
  return parts.join("|");
}
