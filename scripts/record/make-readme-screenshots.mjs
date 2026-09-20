#!/usr/bin/env node
// 从已审查的单镜头视频精确取帧，生成 README 三联图。
// 镜头、时间点与输出参数只在 tour.config.mjs 中维护。

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { readmeScreenshots, target } from "./tour.config.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const OUT = path.resolve(HERE, process.env.RAELYN_TOUR_OUT || "out");
const REPO_FFMPEG = path.join(REPO, "bin/ffmpeg");
const FFMPEG = process.env.RAELYN_FFMPEG || (existsSync(REPO_FFMPEG) ? REPO_FFMPEG : "ffmpeg");

function inside(parent, child) {
  const relative = path.relative(parent, child);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ["ignore", "ignore", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (data) => { stderr += data.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} exited ${code}\n${stderr.slice(-3000)}`));
    });
  });
}

async function main() {
  const manifestPath = path.join(OUT, "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  if (String(manifest.snapshotId || "") !== target.snapshotId) {
    throw new Error("录制清单与 tour.config.mjs 的固定快照不一致，请先完成一次全量录制");
  }

  const shotById = new Map((manifest.shots || []).map((shot) => [shot.id, shot]));
  const destinationDir = path.resolve(REPO, readmeScreenshots.outputDir);
  if (!inside(path.join(REPO, "docs", "assets"), destinationDir)) {
    throw new Error(`README 截图输出目录必须位于 docs/assets/: ${destinationDir}`);
  }
  await mkdir(destinationDir, { recursive: true });

  for (const frame of readmeScreenshots.frames) {
    const shot = shotById.get(frame.shotId);
    if (!shot) throw new Error(`录制清单缺少镜头: ${frame.shotId}`);
    if (!(Number(frame.atSeconds) >= 0 && Number(frame.atSeconds) < Number(shot.duration))) {
      throw new Error(`${frame.shotId} 的 README 取帧时间超出镜头时长 ${shot.duration}s`);
    }

    const source = path.resolve(OUT, shot.file);
    if (!inside(OUT, source)) throw new Error(`镜头路径越界: ${shot.file}`);
    await stat(source);

    const destination = path.resolve(destinationDir, frame.file);
    if (!inside(destinationDir, destination)) throw new Error(`截图输出路径越界: ${frame.file}`);
    await run(FFMPEG, [
      "-y", "-hide_banner", "-loglevel", "error",
      "-i", source,
      "-ss", String(frame.atSeconds),
      "-frames:v", "1", "-an",
      "-vf", `scale=${readmeScreenshots.width}:${readmeScreenshots.height}:flags=lanczos`,
      "-c:v", "libwebp", "-preset", "picture",
      "-quality", String(readmeScreenshots.quality), "-compression_level", "6",
      "-map_metadata", "-1",
      destination,
    ]);
    const generated = await stat(destination);
    if (generated.size <= 0) throw new Error(`README 截图为空: ${destination}`);
    process.stdout.write(`✓ ${frame.id}: ${path.relative(REPO, destination)} (${generated.size} bytes)\n`);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : error);
  process.exitCode = 1;
});
