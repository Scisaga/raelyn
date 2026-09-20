#!/usr/bin/env node
// 合成：界面镜头 → 无字幕母版 → 单一成片（内嵌中英双字幕轨、混配乐）。
//
//   node compose-tour.mjs
//   node compose-tour.mjs --reuse-master   # 只改字幕或音轨时复用现有母版
//
// 两段式是故意的：母版永远保持无字幕、无配乐；成片只复用视频流并封装可开关的
// 中英文 mov_text 字幕轨，改文案或换语言不必重录镜头，也不必再次编码画面。
//
// 产物：
//   out/tour-master.mp4                  无字幕无声母版
//   out/raelyn-tour-1440x960.mp4         内嵌中文（默认）与英文字幕轨的成片

import { spawn } from "node:child_process";
import { readFile, access } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { output, music, subtitles, transition } from "./tour.config.mjs";
import { buildTimeline } from "./lib/timeline.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, process.env.RAELYN_TOUR_OUT || "out");
const FFMPEG = process.env.RAELYN_FFMPEG || "ffmpeg";

const argv = process.argv.slice(2);
const REUSE_MASTER = argv.includes("--reuse-master");

function run(args, cwd = OUT) {
  return new Promise((resolve, reject) => {
    const child = spawn(FFMPEG, args, { cwd, stdio: ["ignore", "ignore", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (d) => { stderr += d.toString(); });
    child.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`ffmpeg exited ${code}\n${stderr.slice(-3000)}`))));
  });
}

async function exists(file) {
  try { await access(file); return true; } catch { return false; }
}

// 段与段的接法：overlap>0 走 xfade，overlap=0 直接硬切（星域内部的语义缩放
// 本身就是转场，再叠一次溶解只会糊掉细节）。
//
// 每路输入先归一化帧率、像素格式、起始 PTS 和时基。少了 settb=AVTB，concat
// 之后的时基会变成 1/1000000，与后面 xfade 的另一路输入不匹配而直接报错。
function buildChain(segments) {
  const parts = segments.map((segment, i) => {
    const rate = Number(segment.playbackRate || 1);
    return `[${i}:v]setpts=(PTS-STARTPTS)/${rate},fps=${output.fps},format=yuv420p,settb=AVTB[s${i}]`;
  });
  if (segments.length === 1) {
    parts.push("[s0]null[vchain]");
    return { filter: parts.join(";"), last: "[vchain]" };
  }
  let current = "[s0]";
  for (let i = 1; i < segments.length; i += 1) {
    const seg = segments[i];
    const label = i === segments.length - 1 ? "[vchain]" : `[v${i}]`;
    if (seg.overlap > 0) {
      parts.push(`${current}[s${i}]xfade=transition=${transition.kind}:duration=${seg.overlap.toFixed(3)}:offset=${seg.start.toFixed(3)}${label}`);
    } else {
      parts.push(`${current}[s${i}]concat=n=2:v=1:a=0,settb=AVTB${label}`);
    }
    current = label;
  }
  return { filter: parts.join(";"), last: "[vchain]" };
}

async function buildMaster(timeline) {
  const segments = timeline.segments;
  const inputs = segments.flatMap((s) => ["-i", s.file]);
  const { filter, last } = buildChain(segments);
  const total = timeline.duration;
  const fade = `${last}fade=t=in:st=0:d=0.6,fade=t=out:st=${Math.max(0, total - 0.9).toFixed(3)}:d=0.9,format=yuv420p[vout]`;

  await run([
    "-y", "-hide_banner", "-loglevel", "error",
    ...inputs,
    "-filter_complex", `${filter};${fade}`,
    "-map", "[vout]",
    "-c:v", "libx264", "-preset", output.preset, "-crf", String(output.crf),
    "-r", String(output.fps), "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
    "tour-master.mp4",
  ]);
  return total;
}

async function buildTour(total, musicSelection) {
  const subtitleFiles = ["tour.zh.srt", "tour.en.srt"];
  for (const subtitleFile of subtitleFiles) {
    if (!await exists(path.join(OUT, subtitleFile))) {
      throw new Error(`缺少字幕文件: ${subtitleFile}；请先运行 make-subtitles.mjs`);
    }
  }
  const hasMusic = musicSelection.mode === "file";
  const musicPath = hasMusic ? path.resolve(HERE, musicSelection.file) : "";
  if (hasMusic && !await exists(musicPath)) {
    throw new Error(`已选择的音轨不存在: ${musicPath}；请重新运行 select-music.mjs`);
  }
  const outFile = `raelyn-tour-${output.finalWidth}x${output.finalHeight}.mp4`;

  const args = ["-y", "-hide_banner", "-loglevel", "error", "-i", "tour-master.mp4"];
  if (hasMusic) args.push("-ss", String(musicSelection.startAt), "-i", musicPath);
  const subtitleInput = hasMusic ? 2 : 1;
  args.push(
    "-i", subtitleFiles[0],
    "-i", subtitleFiles[1],
    "-map", "0:v:0",
  );
  if (hasMusic) args.push("-map", "1:a:0");
  args.push(
    "-map", `${subtitleInput}:s:0`,
    "-map", `${subtitleInput + 1}:s:0`,
    "-c:v", "copy",
    "-c:s", "mov_text",
    "-metadata:s:s:0", "language=zho",
    "-metadata:s:s:0", "handler_name=简体中文",
    "-metadata:s:s:1", "language=eng",
    "-metadata:s:s:1", "handler_name=English",
    "-disposition:s:0", "default",
    "-disposition:s:1", "0",
  );

  if (hasMusic) {
    const fadeOutStart = Math.max(0, total - musicSelection.fadeOutSeconds).toFixed(3);
    args.push(
      "-af", `volume=${musicSelection.gainDb}dB,afade=t=in:st=0:d=${musicSelection.fadeInSeconds},afade=t=out:st=${fadeOutStart}:d=${musicSelection.fadeOutSeconds}`,
      "-c:a", "aac", "-b:a", "192k",
    );
  } else {
    args.push("-an");
  }

  args.push("-movflags", "+faststart", "-t", total.toFixed(3), outFile);

  await run(args);
  return { outFile, hasMusic };
}

async function main() {
  const manifest = JSON.parse(await readFile(path.join(OUT, "manifest.json"), "utf8"));
  const timeline = buildTimeline(manifest);
  let musicSelection;
  try {
    musicSelection = JSON.parse(await readFile(path.join(OUT, "music-selection.json"), "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error("缺少 out/music-selection.json；请先运行 node select-music.mjs 明确选择音轨或静音");
    }
    throw error;
  }
  if (!["file", "silent"].includes(musicSelection.mode)) {
    throw new Error("music-selection.json 的 mode 无效；请重新运行 node select-music.mjs");
  }
  if (musicSelection.requestedFile !== String(music.file || "").trim()) {
    throw new Error("配乐配置在选择音轨后发生变化；请重新运行 node select-music.mjs");
  }
  if (Math.abs(Number(musicSelection.timelineDuration) - timeline.duration) > 0.01) {
    throw new Error("成片时长在选择音轨后发生变化；请重新运行 node select-music.mjs");
  }

  process.stdout.write(`合成 ${timeline.segments.length} 段，成片 ${timeline.duration.toFixed(1)}s\n`);
  let total = timeline.duration;
  if (REUSE_MASTER) {
    if (!await exists(path.join(OUT, "tour-master.mp4"))) {
      throw new Error("缺少 out/tour-master.mp4，不能使用 --reuse-master");
    }
    process.stdout.write("✓ 复用 tour-master.mp4（无字幕母版）\n");
  } else {
    total = await buildMaster(timeline);
    process.stdout.write("✓ tour-master.mp4（无字幕母版）\n");
  }
  const { outFile, hasMusic } = await buildTour(total, musicSelection);
  process.stdout.write(`✓ ${outFile}（内嵌中英双字幕轨${hasMusic ? "" : "，已明确选择静音"}）\n`);
}

main().catch((err) => { console.error(err); process.exit(1); });
