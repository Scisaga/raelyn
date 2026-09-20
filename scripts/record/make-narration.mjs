#!/usr/bin/env node
// 由分镜配置 + 实测时长生成独立的中文配音演讲稿。
// 画面字幕由 make-subtitles.mjs 生成；两套文案共享时间线，但不互相复制。
//
//   node make-narration.mjs
//
// 产物：out/narration-script.md  out/narration.zh.srt

import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { buildTimeline, collectNarration } from "./lib/timeline.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, process.env.RAELYN_TOUR_OUT || "out");

function srtTime(seconds) {
  const ms = Math.max(0, Math.round(seconds * 1000));
  const h = String(Math.floor(ms / 3600000)).padStart(2, "0");
  const m = String(Math.floor((ms % 3600000) / 60000)).padStart(2, "0");
  const s = String(Math.floor((ms % 60000) / 1000)).padStart(2, "0");
  const f = String(ms % 1000).padStart(3, "0");
  return `${h}:${m}:${s},${f}`;
}

function clockTime(seconds) {
  const tenths = Math.max(0, Math.round(seconds * 10));
  const minutes = Math.floor(tenths / 600);
  const remainder = tenths % 600;
  return `${String(minutes).padStart(2, "0")}:${String(Math.floor(remainder / 10)).padStart(2, "0")}.${remainder % 10}`;
}

function speechUnits(text) {
  return [...String(text || "").replace(/[\s，。；：！？、,.!?;:'"“”‘’（）()—-]/g, "")].length;
}

function toSrt(entries) {
  return entries.map((entry, index) => [
    index + 1,
    `${srtTime(entry.start)} --> ${srtTime(entry.end)}`,
    entry.zh,
    "",
  ].join("\n")).join("\n");
}

function toMarkdown(entries, duration) {
  const rows = entries.map((entry, index) => {
    const available = entry.end - entry.start;
    const rate = speechUnits(entry.zh) / available;
    return `| ${index + 1} | ${clockTime(entry.start)}–${clockTime(entry.end)} | ${entry.segment} | ` +
      `${available.toFixed(1)}s | ${rate.toFixed(1)} 字/s | ${entry.zh} |`;
  });
  return [
    "# 产品导览配音演讲稿",
    "",
    "> 配音稿与画面字幕分开维护；这里的文字用于朗读，不会烧进画面。",
    `> 当前无 SVG 幕间卡，预计成片时长 ${duration.toFixed(1)} 秒。`,
    "",
    "## 分镜与节奏",
    "",
    "| # | 时间 | 镜头 | 可用时长 | 建议语速 | 配音 |",
    "| ---: | --- | --- | ---: | ---: | --- |",
    ...rows,
    "",
    "## 连续演讲稿",
    "",
    ...entries.map((entry) => entry.zh),
    "",
  ].join("\n");
}

async function main() {
  const manifest = JSON.parse(await readFile(path.join(OUT, "manifest.json"), "utf8"));
  const timeline = buildTimeline(manifest);
  const entries = collectNarration(timeline);
  if (!entries.length) throw new Error("tour.config.mjs 中没有 narration 配音稿");

  for (let index = 1; index < entries.length; index += 1) {
    if (entries[index].start < entries[index - 1].end) {
      throw new Error(`配音时间重叠: ${entries[index - 1].segment} → ${entries[index].segment}`);
    }
  }
  for (const entry of entries) {
    const rate = speechUnits(entry.zh) / (entry.end - entry.start);
    if (rate > 5.5) {
      console.warn(`[warn] 配音语速偏快(${rate.toFixed(1)} 字/s): ${entry.segment} ${entry.zh}`);
    }
  }

  await writeFile(path.join(OUT, "narration.zh.srt"), toSrt(entries), "utf8");
  await writeFile(
    path.join(OUT, "narration-script.md"),
    toMarkdown(entries, timeline.duration),
    "utf8",
  );

  process.stdout.write(`✓ ${entries.length} 段配音稿，成片时长 ${timeline.duration.toFixed(1)}s\n`);
  process.stdout.write(`  ${path.join(OUT, "narration-script.md")}（演讲稿审阅用）\n`);
  process.stdout.write(`  ${path.join(OUT, "narration.zh.srt")}（录音对时用）\n`);
}

main().catch((error) => { console.error(error); process.exit(1); });
