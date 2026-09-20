#!/usr/bin/env node
// 在最终合成前明确选择并校验音轨。
//
// 音轨路径仍由 tour.config.mjs（或 RAELYN_TOUR_MUSIC）指定；本阶段只做三件事：
// 1. 记录本次构建究竟选择了哪个文件；
// 2. 用 ffprobe 确认它有音频流且剩余时长覆盖整部成片；
// 3. 没有配置文件时明确选择静音，而不是让合成阶段悄悄降级。
//
// 产物：out/music-selection.json

import { spawn } from "node:child_process";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { music } from "./tour.config.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, process.env.RAELYN_TOUR_OUT || "out");
const FFPROBE = process.env.RAELYN_FFPROBE || "ffprobe";

async function exists(file) {
  try {
    await access(file);
    return true;
  } catch {
    return false;
  }
}

function probe(file) {
  return new Promise((resolve, reject) => {
    const child = spawn(FFPROBE, [
      "-v", "error",
      "-show_entries", "format=duration:stream=codec_type,codec_name,duration",
      "-of", "json",
      file,
    ], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (data) => { stdout += data.toString(); });
    child.stderr.on("data", (data) => { stderr += data.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`ffprobe exited ${code}\n${stderr.slice(-2000)}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (error) {
        reject(new Error(`ffprobe 返回了无效 JSON: ${error.message}`));
      }
    });
  });
}

async function main() {
  const timeline = JSON.parse(await readFile(path.join(OUT, "timeline.json"), "utf8"));
  const requestedFile = String(music.file || "").trim();
  const candidate = requestedFile
    ? (path.isAbsolute(requestedFile) ? requestedFile : path.resolve(HERE, requestedFile))
    : "";

  let selection;
  if (!candidate || !await exists(candidate)) {
    selection = {
      version: 1,
      mode: "silent",
      requestedFile,
      timelineDuration: timeline.duration,
      selectedAt: new Date().toISOString(),
    };
    process.stdout.write(`✓ 已明确选择静音（未找到音轨：${requestedFile || "未配置"}）\n`);
  } else {
    const metadata = await probe(candidate);
    const audio = (metadata.streams || []).find((stream) => stream.codec_type === "audio");
    if (!audio) throw new Error(`音轨没有音频流: ${candidate}`);

    const duration = Number(metadata.format?.duration ?? audio.duration);
    if (!Number.isFinite(duration)) throw new Error(`无法读取音轨时长: ${candidate}`);
    const required = Number(music.startAt) + Number(timeline.duration);
    if (duration + 0.05 < required) {
      throw new Error(
        `音轨剩余时长不足：需要 ${timeline.duration.toFixed(1)}s（从 ${music.startAt}s 开始），` +
        `文件总长只有 ${duration.toFixed(1)}s`,
      );
    }

    selection = {
      version: 1,
      mode: "file",
      requestedFile,
      file: path.relative(HERE, candidate),
      codec: audio.codec_name || null,
      sourceDuration: duration,
      timelineDuration: timeline.duration,
      startAt: music.startAt,
      gainDb: music.gainDb,
      fadeInSeconds: music.fadeInSeconds,
      fadeOutSeconds: music.fadeOutSeconds,
      selectedAt: new Date().toISOString(),
    };
    process.stdout.write(
      `✓ 已选择音轨 ${selection.file}（${selection.codec || "unknown"}，` +
      `从 ${selection.startAt}s 开始，${selection.gainDb}dB）\n`,
    );
  }

  await mkdir(OUT, { recursive: true });
  await writeFile(
    path.join(OUT, "music-selection.json"),
    `${JSON.stringify(selection, null, 2)}\n`,
    "utf8",
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
