#!/usr/bin/env node
// 由分镜配置 + 实测时长生成中英两份字幕，时间轴与合成脚本共用同一套计算。
// 保留无字幕母版的意义就在这里：改文案、出另一种语言，都不用重录。
//
//   node make-subtitles.mjs
//
// 产物：out/subtitle-script.md  out/tour.{zh,en}.ass  out/tour.{zh,en}.srt
//       out/timeline.json
//
// 默认成片把中英文 SRT 封装为两条可切换的 mov_text 软字幕轨，不烧进画面。
// ASS 仍然输出，供确实需要固定样式硬字幕的场景使用；其 PlayResX/PlayResY 保证
// 字号与边距按最终像素尺寸解释。两种格式承载的都是画面信息层，不是配音逐字稿。

import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { subtitles, output } from "./tour.config.mjs";
import { buildTimeline, collectCaptions } from "./lib/timeline.mjs";

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

function toSrt(entries, key) {
  return entries
    .map((e, i) => `${i + 1}\n${srtTime(e.start)} --> ${srtTime(e.end)}\n${e[key]}\n`)
    .join("\n");
}

function markdownTime(seconds) {
  const total = Math.max(0, Math.round(seconds * 10));
  const minutes = Math.floor(total / 600);
  const remainder = total % 600;
  return `${String(minutes).padStart(2, "0")}:${String(Math.floor(remainder / 10)).padStart(2, "0")}.${remainder % 10}`;
}

function markdownCell(value) {
  return String(value || "").replace(/\|/g, "\\|").replace(/\n/g, "<br>");
}

function toReviewMarkdown(entries, duration) {
  const table = (key) => [
    "| # | 时间 | 镜头 | 字幕 |",
    "| ---: | --- | --- | --- |",
    ...entries.map((entry, index) => (
      `| ${index + 1} | ${markdownTime(entry.start)}–${markdownTime(entry.end)} | ` +
      `${markdownCell(entry.segment)} | ${markdownCell(entry[key])} |`
    )),
  ].join("\n");
  return [
    "# 产品导览字幕文字稿",
    "",
    "> 由 `tour.config.mjs` 与实测镜头时长生成；修改文案请回到配置文件。",
    `> 当前无 SVG 幕间卡，预计成片时长 ${duration.toFixed(1)} 秒。`,
    "",
    "## 中文",
    "",
    table("zh"),
    "",
    "## English",
    "",
    table("en"),
    "",
  ].join("\n");
}

function assTime(seconds) {
  const cs = Math.max(0, Math.round(seconds * 100));
  const h = Math.floor(cs / 360000);
  const m = String(Math.floor((cs % 360000) / 6000)).padStart(2, "0");
  const s = String(Math.floor((cs % 6000) / 100)).padStart(2, "0");
  const c = String(cs % 100).padStart(2, "0");
  return `${h}:${m}:${s}.${c}`;
}

function toAss(entries, key, fontName) {
  const header = [
    "[Script Info]",
    "ScriptType: v4.00+",
    // 这两行是重点：给了 PlayRes，字号与边距才是像素。
    `PlayResX: ${output.finalWidth}`,
    `PlayResY: ${output.finalHeight}`,
    "WrapStyle: 0",
    "ScaledBorderAndShadow: yes",
    "YCbCr Matrix: TV.709",
    "",
    "[V4+ Styles]",
    "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
    // BorderStyle=3 是半透明衬底盒，保证任何画面下都读得清；Outline=8 是盒的内边距。
    `Style: Tour,${fontName},${subtitles.fontSize},&H00F2F7FF,&H00F2F7FF,&HA00A0D12,&HA0000000,0,0,0,0,100,100,0.6,0,3,8,0,2,${subtitles.marginH},${subtitles.marginH},${subtitles.marginV},1`,
    "",
    "[Events]",
    "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
  ];
  const events = entries.map((e) => {
    const text = String(e[key] || "").replace(/\n/g, "\\N");
    return `Dialogue: 0,${assTime(e.start)},${assTime(e.end)},Tour,,0,0,0,,${text}`;
  });
  return `${header.join("\n")}\n${events.join("\n")}\n`;
}

async function main() {
  const manifest = JSON.parse(await readFile(path.join(OUT, "manifest.json"), "utf8"));
  const timeline = buildTimeline(manifest);
  const entries = collectCaptions(timeline, { minHoldSeconds: subtitles.minHoldSeconds });

  await writeFile(path.join(OUT, "timeline.json"), JSON.stringify(timeline, null, 2), "utf8");
  await writeFile(path.join(OUT, "tour.zh.srt"), toSrt(entries, "zh"), "utf8");
  await writeFile(path.join(OUT, "tour.en.srt"), toSrt(entries, "en"), "utf8");
  await writeFile(path.join(OUT, "tour.zh.ass"), toAss(entries, "zh", subtitles.fontName), "utf8");
  await writeFile(path.join(OUT, "tour.en.ass"), toAss(entries, "en", subtitles.fontNameEn), "utf8");
  await writeFile(
    path.join(OUT, "subtitle-script.md"),
    toReviewMarkdown(entries, timeline.duration),
    "utf8",
  );

  // 可读性体检：过长的单条字幕在 1440 宽画面上会折行遮挡产品。
  for (const e of entries) {
    if (/\r|\n/.test(e.zh) || /\r|\n/.test(e.en)) {
      throw new Error(`画面字幕必须保持单行: ${e.segment}`);
    }
    const zhLen = Math.max(...String(e.zh || "").split("\n").map(
      (line) => /^https:\/\//.test(line) ? 0 : [...line].length,
    ));
    const enLen = Math.max(...String(e.en || "").split("\n").map((line) => line.length));
    if (zhLen > 22) console.warn(`[warn] 中文字幕偏长(${zhLen}字): ${e.zh}`);
    if (enLen > 62) console.warn(`[warn] English caption line long (${enLen} chars): ${e.en}`);
  }

  process.stdout.write(`✓ ${entries.length} 条字幕，成片时长 ${timeline.duration.toFixed(1)}s\n`);
  process.stdout.write(`  ${path.join(OUT, "tour.zh.srt")} / tour.en.srt（成片软字幕与外挂用）\n`);
  process.stdout.write(`  ${path.join(OUT, "tour.zh.ass")} / tour.en.ass（可选硬字幕用）\n`);
  process.stdout.write(`  ${path.join(OUT, "subtitle-script.md")}（文字稿审阅用）\n`);
}

main().catch((err) => { console.error(err); process.exit(1); });
