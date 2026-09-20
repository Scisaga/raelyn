#!/usr/bin/env node
// 自动录制真实界面操作片段。
//
// 页面先完成加载和 settle，再按分镜直接推进页面状态，并为每个目标状态主动截取
// 一张 1440×960 JPEG。镜头结束后才交给 ffmpeg 封装原片并生成待审 MP4；不运行
// 连续 screencast，也不依赖 wall-clock 动画。镜头级倍速在最终组装时应用；需要局部
// 变速的时间轴镜头在待审片阶段先按分段烘焙。
//
//   node record-tour.mjs --check
//   node record-tour.mjs
//   node record-tour.mjs --shot field-topic,stories-page
//
// 产物：out/raw/<id>.mkv、out/shots/<id>.mp4、out/manifest.json

import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { content, output, SEL, shots, target } from "./tour.config.mjs";
import {
  createCursor,
  createUi,
  hygieneInitScript,
  sleepFactory,
  warnings,
} from "./lib/ui.mjs";
import { mergeShotManifest } from "./lib/manifest.mjs";
import { validateRecordingData } from "./lib/preflight.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const OUT = path.resolve(HERE, process.env.RAELYN_TOUR_OUT || "out");
const FFMPEG = process.env.RAELYN_FFMPEG || "ffmpeg";
const FFPROBE = process.env.RAELYN_FFPROBE || "ffprobe";

const argv = process.argv.slice(2);
const CHECK_ONLY = argv.includes("--check");
const HEADED = argv.includes("--headed");
const RESUME = argv.includes("--resume");
const value = (name) => {
  const inline = argv.find((arg) => arg.startsWith(`--${name}=`));
  if (inline) return inline.split("=").slice(1).join("=");
  const index = argv.indexOf(`--${name}`);
  return index >= 0 ? argv[index + 1] : null;
};
const ONLY = (value("shot") || "").split(",").map((id) => id.trim()).filter(Boolean);

function run(command, args, { captureStdout = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: ["ignore", captureStdout ? "pipe" : "ignore", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (data) => { stdout += data.toString(); });
    child.stderr.on("data", (data) => { stderr += data.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(`${command} exited ${code}\n${stderr.slice(-3000)}`));
    });
  });
}

async function probeVideo(file) {
  const stdout = await run(FFPROBE, [
    "-v", "error",
    "-show_entries", "format=duration:stream=codec_type,width,height",
    "-of", "json",
    file,
  ], { captureStdout: true });
  const metadata = JSON.parse(stdout);
  const video = (metadata.streams || []).find((stream) => stream.codec_type === "video");
  const duration = Number(metadata.format?.duration);
  if (!video || !Number.isFinite(duration) || duration <= 0) {
    throw new Error(`无法读取视频: ${file}`);
  }
  return { duration, video };
}

async function readBearerToken() {
  if (process.env.API_BEARER_TOKEN) return process.env.API_BEARER_TOKEN;
  const envPath = path.join(REPO, ".env");
  if (!existsSync(envPath)) return "";
  const text = await readFile(envPath, "utf8");
  const line = text.split("\n").find((entry) => entry.startsWith("API_BEARER_TOKEN="));
  return line ? line.split("=").slice(1).join("=").trim() : "";
}

async function createContext(browser, token) {
  const context = await browser.newContext({
    viewport: output.viewport,
    deviceScaleFactor: 1,
    reducedMotion: "reduce",
    colorScheme: "dark",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    serviceWorkers: "block",
  });
  if (token) {
    await context.addCookies([{ name: "raelyn_api_token", value: token, url: target.baseUrl }]);
  }
  await context.addInitScript(() => {
    try { sessionStorage.setItem("raelyn.ui.startupGateSeen", "1"); } catch { /* 无痕环境忽略 */ }
  });
  await context.addInitScript(hygieneInitScript);
  return context;
}

async function openShot(page, shot) {
  const startedAt = Date.now();
  const relativeUrl = shot.url(target, content);
  await page.goto(`${target.baseUrl}${relativeUrl}`, { waitUntil: "domcontentloaded", timeout: 60000 });
  process.stdout.write(`   页面打开 ${((Date.now() - startedAt) / 1000).toFixed(1)}s；等待数据就绪\n`);
  const ui = createUi(page, shot.id);
  if (relativeUrl.startsWith("/?")) await ui.waitFieldReady({ snapshotId: target.snapshotId });
  else await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
  if (typeof shot.ready === "function") await shot.ready({ page, ui });
  process.stdout.write(`   数据就绪 ${((Date.now() - startedAt) / 1000).toFixed(1)}s；settle ${shot.settle.toFixed(1)}s\n`);
  await page.waitForTimeout(shot.settle * 1000);
  process.stdout.write(`   开始录制 ${((Date.now() - startedAt) / 1000).toFixed(1)}s\n`);
}

async function checkH264(page) {
  await page.goto(target.baseUrl, { waitUntil: "domcontentloaded" }).catch(() => {});
  return page.evaluate(() => {
    const video = document.createElement("video");
    return video.canPlayType('video/mp4; codecs="avc1.42E01E"') || "";
  });
}

async function checkShots(page, selected) {
  const failures = [];
  for (const shot of selected) {
    process.stdout.write(`\n▶ ${shot.id}  ${target.baseUrl}${shot.url(target, content)}\n`);
    await openShot(page, shot);
    const referenced = new Set(
      [...String(shot.perform || "").matchAll(/SEL\.(\w+)/g)].map((match) => match[1]),
    );
    for (const name of shot.selectors || []) referenced.add(name);
    const used = Object.entries(SEL).filter(([name]) => referenced.has(name));
    if (!used.length) process.stdout.write("   （本镜头不依赖选择器）\n");
    for (const [name, selector] of used) {
      const hit = await page.locator(selector).first().isVisible({ timeout: 1500 }).catch(() => false);
      process.stdout.write(`   ${hit ? "✓" : "✗"} ${name}  ${hit ? "" : selector}\n`);
      if (!hit) failures.push(`${shot.id}:${name}`);
    }
  }
  return failures;
}

async function createFrameRecorder(page) {
  const frames = [];
  let timestamp = 0;

  async function captureAt(nextTimestamp) {
    // screenshot 会提交当前 Chromium 合成帧；不再额外等 rAF，避免无头 WebGL 节流。
    const data = await page.screenshot({ type: "jpeg", quality: 82 });
    frames.push({ data, timestamp: nextTimestamp });
  }

  await captureAt(0);
  return {
    fps: output.captureFps,
    frames,
    get duration() { return timestamp; },
    async step(seconds) {
      const delta = Math.max(1 / 240, Number(seconds));
      timestamp += delta;
      await captureAt(timestamp);
    },
    async hold(seconds) {
      // 静止画面无需重复截几十张相同图片；一张帧配合持续时间即可。
      await this.step(seconds);
    },
    async snapshot() {
      await captureAt(timestamp);
    },
  };
}

async function captureTimelineFrames(page, shot) {
  const plannedDuration = Number(
    shot.sourceDuration || shot.duration * Number(shot.playbackRate || output.playbackRate || 1),
  );
  const timelineDuration = Number(shot.timelineSourceDuration);
  const rotationDuration = Number(shot.rotationDuration);
  if (timelineDuration + rotationDuration !== plannedDuration) {
    throw new Error(`field-timeline 原片分段之和必须等于 ${plannedDuration}s`);
  }
  const frameCount = Math.max(2, Math.round(timelineDuration * output.captureFps));
  const stepDuration = timelineDuration / (frameCount - 1);

  const setup = await page.evaluate(() => {
    const root = document.querySelector("[x-data]");
    const app = globalThis.Alpine?.$data(root);
    const controller = app?.playlistEventMapController?.();
    if (!app || !controller) throw new Error("星域控制器未就绪");
    app.playlistEventMapStopPolling?.();
    app.playlistEventMapStopPlayback?.({ refresh: false, loadEntities: false });
    app.playlistEventMapClearSelection?.({ update: false });
    app.playlistEventMapFiltersOpen = false;
    controller.introActive = false;
    controller.controls.enableDamping = false;
    controller.controls.autoRotate = false;
    controller.setSceneInteractive?.(true);
    controller.progressiveRevealActive = false;
    controller.progressiveRevealPrepared = false;
    controller.progressiveRevealComplete = true;
    controller.semanticMembraneTransitionStartedAt = 0;
    controller.transitionStartedAt = 0;
    controller.storyAnimationStartedAt = 0;
    if (controller.coreMaterial?.uniforms?.uRevealProgress) {
      controller.coreMaterial.uniforms.uRevealProgress.value = 1;
    }
    if (controller.pickMaterial?.uniforms?.uRevealProgress) {
      controller.pickMaterial.uniforms.uRevealProgress.value = 1;
    }
    if (controller.semanticMembrane?.material?.uniforms?.uFieldMix) {
      controller.semanticMembrane.material.uniforms.uFieldMix.value = 1;
    }
    const range = app.playlistEventMapWindowIndices();
    globalThis.__raelynTourTimeline = {
      snapshotId: app.playlistEventMapSnapshotId,
      minEnd: range.length - 1,
      maxEnd: range.max,
      windowLength: range.length,
    };
    const start = Math.max(0, range.length - range.length);
    // 只逐帧推进窗口，不启动产品自己的连续播放循环；SwiftShader 下持续重绘会拖慢截图。
    app.playlistEventMapPlaying = false;
    app.playlistEventMapWindowPreviewStartIndex = start;
    app.playlistEventMapWindowPreviewEndIndex = range.length - 1;
    const dates = app.playlistEventMapWindowDatesForIndices(start, range.length - 1);
    const days = controller.windowDays(dates.start, dates.end);
    controller.previewWindowStartDay = days.start;
    controller.previewWindowEndDay = days.end;
    controller.applyWindowUniforms(days.start, days.end, days.start, days.end, 1);
    if (controller.animationFrame !== null) cancelAnimationFrame(controller.animationFrame);
    controller.animationFrame = null;
    // 录制期间由脚本逐帧渲染；禁用控制器自己的动画调度，避免重复重绘。
    controller.invalidate = () => {};
    if (controller.mediaRoot) controller.mediaRoot.style.visibility = "hidden";
    controller.updateHorizonGrid();
    controller.renderer.render(controller.scene, controller.camera);
    return {
      snapshotId: app.playlistEventMapSnapshotId,
      maxEnd: range.max,
      todayReady: app.playlistEventMapHighlightAvailable("today")
        && !!app.playlistEventMapTodayHighlights,
      weekReady: app.playlistEventMapHighlightAvailable("week")
        && !!app.playlistEventMapWeekHighlights,
    };
  });
  if (String(setup.snapshotId) !== target.snapshotId) {
    throw new Error(`时间轴镜头快照不一致: ${setup.snapshotId}`);
  }
  if (!setup.todayReady || !setup.weekReady) {
    throw new Error("field-timeline 的 24H / 本周视频卡片尚未就绪");
  }

  const recorder = await createFrameRecorder(page);
  for (let frame = 1; frame < frameCount; frame += 1) {
    await page.evaluate((progress) => {
      const app = globalThis.Alpine.$data(document.querySelector("[x-data]"));
      const controller = app.playlistEventMapController();
      const timeline = globalThis.__raelynTourTimeline;
      if (app.playlistEventMapSnapshotId !== timeline.snapshotId) {
        throw new Error("录制期间星域快照发生变化");
      }
      const eased = progress < 0.5
        ? 2 * progress * progress
        : 1 - ((-2 * progress + 2) ** 2) / 2;
      const end = timeline.minEnd + Math.round((timeline.maxEnd - timeline.minEnd) * eased);
      const start = Math.max(0, end - timeline.windowLength + 1);
      app.playlistEventMapPlaying = false;
      // 时间轴 UI 与点云着色器直接使用同一窗口；不在每帧重算 28 万点的
      // CPU activeMask、主题密度和标签，这些在录制画面中没有可见收益。
      app.playlistEventMapWindowPreviewStartIndex = start;
      app.playlistEventMapWindowPreviewEndIndex = end;
      const dates = app.playlistEventMapWindowDatesForIndices(start, end);
      const days = controller.windowDays(dates.start, dates.end);
      controller.previewWindowStartDay = days.start;
      controller.previewWindowEndDay = days.end;
      controller.applyWindowUniforms(days.start, days.end, days.start, days.end, 1);
      controller.updateHorizonGrid();
      controller.renderer.render(controller.scene, controller.camera);
    }, frame / (frameCount - 1));
    await recorder.step(stepDuration);
    if (frame % 20 === 0 || frame === frameCount - 1) {
      process.stdout.write(`   时间轴抓帧 ${frame + 1}/${frameCount}\n`);
    }
  }
  await page.evaluate(() => {
    const app = globalThis.Alpine.$data(document.querySelector("[x-data]"));
    const controller = app.playlistEventMapController();
    const timeline = globalThis.__raelynTourTimeline;
    app.playlistEventMapSetWindowEndIndex(timeline.maxEnd, { update: true, pause: false });
    app.playlistEventMapPlaying = false;
    app.playlistEventMapSetTimeFocusScope("today");
    if (controller.mediaRoot) controller.mediaRoot.style.visibility = "";
    controller.updateLabels();
    controller.updateMediaCards?.();
    controller.updateHorizonGrid();
    controller.renderer.render(controller.scene, controller.camera);
  });

  const rotationFrames = Math.round(rotationDuration * Number(shot.rotationCaptureFps));
  const weekSwitchFrame = Math.round(Number(shot.weekSwitchAt) * Number(shot.rotationCaptureFps)) + 1;
  for (let frame = 1; frame <= rotationFrames; frame += 1) {
    if (frame === weekSwitchFrame) {
      await page.evaluate(() => {
        const app = globalThis.Alpine.$data(document.querySelector("[x-data]"));
        app.playlistEventMapSetTimeFocusScope("week");
      });
    }
    await page.evaluate((progress) => {
      const app = globalThis.Alpine.$data(document.querySelector('[x-data="appShell()"]'));
      const controller = app.playlistEventMapController();
      const timeline = globalThis.__raelynTourTimeline;
      if (!timeline.rotationCamera) {
        timeline.rotationCamera = controller.camera.position.clone();
        timeline.rotationTarget = controller.controls.target.clone();
      }
      const eased = progress * progress * (3 - 2 * progress);
      const offset = timeline.rotationCamera.clone().sub(timeline.rotationTarget);
      offset.applyAxisAngle(new controller.THREE.Vector3(0, 1, 0), eased * 0.48);
      controller.controls.target.copy(timeline.rotationTarget);
      controller.camera.position.copy(timeline.rotationTarget).add(offset);
      controller.camera.lookAt(controller.controls.target);
      controller.camera.updateProjectionMatrix();
      controller.updateLabels();
      controller.updateMediaCards?.();
      controller.updateHorizonGrid();
      controller.renderer.render(controller.scene, controller.camera);
    }, frame / rotationFrames);
    await recorder.step(rotationDuration / rotationFrames);
  }
  process.stdout.write(
    `   离线时间轴 ${recorder.duration.toFixed(2)}s / ${recorder.frames.length} 帧；封装原片\n`,
  );
  return recorder.frames;
}

async function freezeFieldForCapture(page) {
  await page.evaluate(() => {
    const app = globalThis.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
    const controller = app?.playlistEventMapController?.();
    if (!app || !controller) throw new Error("星域控制器未就绪");
    app.playlistEventMapStopPolling?.();
    app.playlistEventMapStopPlayback?.({ refresh: false, loadEntities: false });
    controller.introActive = false;
    controller.controls.enableDamping = false;
    controller.controls.autoRotate = false;
    controller.setSceneInteractive?.(true);
    controller.progressiveRevealActive = false;
    controller.semanticMembraneTransitionStartedAt = 0;
    controller.transitionStartedAt = 0;
    controller.storyAnimationStartedAt = 0;
    if (controller.coreMaterial?.uniforms?.uRevealProgress) controller.coreMaterial.uniforms.uRevealProgress.value = 1;
    if (controller.pickMaterial?.uniforms?.uRevealProgress) controller.pickMaterial.uniforms.uRevealProgress.value = 1;
    if (controller.animationFrame !== null) cancelAnimationFrame(controller.animationFrame);
    controller.animationFrame = null;
    controller.invalidate = () => {};
    if (controller.mediaRoot) controller.mediaRoot.style.visibility = "hidden";
    controller.updateHorizonGrid();
    controller.renderer.render(controller.scene, controller.camera);
  });
}

async function prepareFieldTransition(page, kind, id) {
  await page.evaluate(async ({ targetKind, targetId }) => {
    const app = globalThis.Alpine.$data(document.querySelector('[x-data="appShell()"]'));
    const controller = app.playlistEventMapController();
    const startPosition = controller.camera.position.clone();
    const startTarget = controller.controls.target.clone();
    if (targetKind === "topic") {
      const topic = app.playlistEventMapManifest.topics.find(
        (item) => String(item.topic_id || "") === targetId,
      );
      if (!topic) throw new Error(`主题不在当前快照: ${targetId}`);
      app.playlistEventMapSelectTopic(topic, { focus: true });
    } else {
      const pointIndex = app.playlistEventMapScene.canonicalIds.findIndex(
        (canonicalId) => String(canonicalId || "") === targetId,
      );
      if (pointIndex < 0) throw new Error(`事件不在当前快照: ${targetId}`);
      await app.playlistEventMapSelectCanonical(pointIndex, targetId);
      controller.focusPoint(pointIndex);
    }
    globalThis.__raelynTourFieldTransition = {
      startPosition,
      startTarget,
      endPosition: controller.camera.position.clone(),
      endTarget: controller.controls.target.clone(),
    };
    controller.camera.position.copy(startPosition);
    controller.controls.target.copy(startTarget);
    controller.camera.lookAt(startTarget);
    controller.updateHorizonGrid();
    controller.renderer.render(controller.scene, controller.camera);
  }, { targetKind: kind, targetId: id });
  await page.waitForFunction(({ targetKind, targetId }) => {
    const app = globalThis.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
    if (!app || String(app.playlistEventMapSelectedId || "") !== targetId) return false;
    return targetKind === "topic" ? !app.playlistEventMapTopicDetailLoading : !app.playlistEventMapDetailLoading;
  }, { targetKind: kind, targetId: id }, { timeout: 20000 });
}

async function runFieldTransition(page, recorder, seconds) {
  const frames = 8;
  for (let frame = 1; frame <= frames; frame += 1) {
    await page.evaluate((progress) => {
      const app = globalThis.Alpine.$data(document.querySelector('[x-data="appShell()"]'));
      const controller = app.playlistEventMapController();
      const transition = globalThis.__raelynTourFieldTransition;
      const eased = progress * progress * (3 - 2 * progress);
      controller.camera.position.lerpVectors(transition.startPosition, transition.endPosition, eased);
      controller.controls.target.lerpVectors(transition.startTarget, transition.endTarget, eased);
      controller.camera.lookAt(controller.controls.target);
      controller.camera.updateProjectionMatrix();
      controller.updateSemanticLevel();
      controller.updateLabels();
      controller.updateHorizonGrid();
      controller.renderer.render(controller.scene, controller.camera);
    }, frame / frames);
    await recorder.step(seconds / frames);
  }
}

async function captureTopicFrames(page, shot) {
  await freezeFieldForCapture(page);
  const recorder = await createFrameRecorder(page);
  await recorder.hold(0.75);
  for (const step of [
    { kind: "topic", id: content.topicL0, hold: 0.75 },
    { kind: "topic", id: content.topicL1, hold: 0.75 },
    { kind: "canonical", id: content.canonical, hold: 3.75 },
  ]) {
    await prepareFieldTransition(page, step.kind, step.id);
    await runFieldTransition(page, recorder, 1.5);
    await recorder.hold(step.hold);
  }
  process.stdout.write(
    `   星域下钻 ${recorder.duration.toFixed(2)}s / ${recorder.frames.length} 帧；封装原片\n`,
  );
  return recorder.frames;
}

async function captureFrames(page, shot) {
  if (shot.id === "field-timeline") return captureTimelineFrames(page, shot);
  if (shot.id === "field-topic") return captureTopicFrames(page, shot);
  const timeScale = Number(shot.playbackRate || output.playbackRate || 1);
  const plannedDuration = shot.duration * timeScale;
  const recorder = await createFrameRecorder(page);
  if (typeof shot.perform === "function") {
    const ui = createUi(page, shot.id, timeScale, recorder);
    const cursor = createCursor(page, ui, shot.id, timeScale, recorder);
    await shot.perform({
      page,
      ui,
      cursor,
      sleep: sleepFactory(page, timeScale, recorder),
      recorder,
      SEL,
    });
  }
  if (recorder.duration < plannedDuration) {
    await recorder.hold(plannedDuration - recorder.duration);
  }
  process.stdout.write(
    `   录制完成 ${recorder.duration.toFixed(2)}s / ${recorder.frames.length} 帧；封装原片\n`,
  );
  return recorder.frames;
}

async function buildRawVideo(frames, shot, rawFile) {
  const frameDir = path.join(OUT, "frames", shot.id);
  await rm(frameDir, { recursive: true, force: true });
  await mkdir(frameDir, { recursive: true });
  const fallbackDuration = 1 / output.captureFps;
  const lines = [];

  for (let index = 0; index < frames.length; index += 1) {
    const name = `f${String(index).padStart(5, "0")}.jpg`;
    await writeFile(path.join(frameDir, name), frames[index].data);
    const nextTimestamp = frames[index + 1]?.timestamp;
    const duration = Number.isFinite(nextTimestamp)
      ? Math.max(1 / 240, nextTimestamp - frames[index].timestamp)
      : fallbackDuration;
    lines.push(`file '${name}'`);
    lines.push(`duration ${duration.toFixed(6)}`);
  }
  lines.push(`file 'f${String(frames.length - 1).padStart(5, "0")}.jpg'`);
  const listFile = path.join(frameDir, "frames.txt");
  await writeFile(listFile, `${lines.join("\n")}\n`, "utf8");
  await run(FFMPEG, [
    "-y", "-hide_banner", "-loglevel", "error",
    "-f", "concat", "-safe", "0", "-i", listFile,
    "-fps_mode", "vfr", "-an", "-c:v", "copy", rawFile,
  ]);
  await rm(frameDir, { recursive: true, force: true });
  return probeVideo(rawFile);
}

async function makeReviewClip(rawFile, shot, outFile) {
  const filter = `scale=${output.finalWidth}:${output.finalHeight}:flags=lanczos,` +
    `fps=${output.fps},format=yuv420p`;
  const filterArgs = Array.isArray(shot.reviewSegments) && shot.reviewSegments.length
    ? (() => {
        const parts = shot.reviewSegments.map((segment, index) => {
          const end = Number.isFinite(segment.end) ? `:end=${segment.end}` : "";
          // 先把 VFR 原片展开为 CFR，再切段；否则单帧停留的下一时间戳会跨过 trim
          // 边界，导致停留时长在 concat 时被重复或丢失。
          return `[0:v]fps=${output.fps},trim=start=${segment.start}${end},` +
            `setpts=(PTS-STARTPTS)/${segment.rate}[segment${index}]`;
        });
        const inputs = shot.reviewSegments.map((_, index) => `[segment${index}]`).join("");
        parts.push(`${inputs}concat=n=${shot.reviewSegments.length}:v=1:a=0,${filter}[review]`);
        return ["-filter_complex", parts.join(";"), "-map", "[review]"];
      })()
    : ["-vf", filter];
  await run(FFMPEG, [
    "-y", "-hide_banner", "-loglevel", "error", "-i", rawFile, "-an",
    ...filterArgs,
    // 待审片段优先快速生成；最终合成会按 output.preset 重新编码。
    "-c:v", "libx264", "-preset", "veryfast", "-crf", String(output.crf),
    "-r", String(output.fps), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    outFile,
  ]);
  return probeVideo(outFile);
}

async function loadExistingManifest(selected) {
  if (!ONLY.length || CHECK_ONLY) return null;
  let manifest;
  try {
    manifest = JSON.parse(await readFile(path.join(OUT, "manifest.json"), "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error("局部重录需要已有的完整 out/manifest.json；请先执行一次全量录制");
    }
    throw error;
  }
  if (String(manifest.snapshotId || "") !== target.snapshotId) {
    throw new Error("已有 manifest 与当前固定快照不一致；请先执行一次全量录制");
  }
  const selectedIds = new Set(selected.map((shot) => shot.id));
  const existingById = new Map((manifest.shots || []).map((entry) => [entry.id, entry]));
  for (const shot of shots) {
    if (selectedIds.has(shot.id)) continue;
    const entry = existingById.get(shot.id);
    if (!entry || !existsSync(path.join(OUT, entry.file))) {
      throw new Error(`局部重录缺少旧片段: ${shot.id}`);
    }
  }
  return manifest;
}

async function main() {
  const unknown = ONLY.filter((id) => !shots.some((shot) => shot.id === id));
  if (unknown.length) throw new Error(`未知镜头: ${unknown.join(", ")}`);
  const selected = ONLY.length ? shots.filter((shot) => ONLY.includes(shot.id)) : shots;
  const existingManifest = await loadExistingManifest(selected);
  const token = await readBearerToken();

  process.stdout.write("▶ 录制数据预检\n");
  const preflight = await validateRecordingData({ target, content, token });
  process.stdout.write(`   ✓ 固定快照 ${preflight.snapshotId} / ${preflight.canonicalCount} 个真实事件\n`);
  process.stdout.write(`   ✓ ${preflight.topicL0} / ${preflight.canonical}\n`);
  process.stdout.write(`   ✓ ${preflight.story} / ${preflight.playableVideos} 条可播放记录\n`);

  const rawDir = path.join(OUT, "raw");
  const shotsDir = path.join(OUT, "shots");
  await mkdir(rawDir, { recursive: true });
  await mkdir(shotsDir, { recursive: true });

  const browser = await chromium.launch({
    headless: !HEADED,
    channel: process.env.RAELYN_TOUR_BROWSER_CHANNEL || undefined,
    args: [
      "--no-proxy-server",
      "--use-gl=angle",
      "--use-angle=swiftshader",
      "--enable-webgl",
      "--enable-unsafe-swiftshader",
      "--ignore-gpu-blocklist",
      "--hide-scrollbars",
      "--force-color-profile=srgb",
      "--disable-features=CalculateNativeWinOcclusion",
    ],
  });
  try {
    const context = await createContext(browser, token);
    try {
      const page = await context.newPage();
      const canPlayH264 = await checkH264(page);
      if (!canPlayH264 && selected.some((shot) => shot.id === "playlist")) {
        throw new Error("当前浏览器不支持 H.264，playlist 会黑屏");
      }
      if (CHECK_ONLY) {
        const failures = await checkShots(page, selected);
        if (failures.length) throw new Error(`选择器未命中: ${failures.join(", ")}`);
        process.stdout.write("\n✓ 巡检完成：数据、快照、选择器与 H.264 均可用。\n");
        return;
      }

      const recordedShots = [];
      for (const shot of selected) {
        const rawFile = path.join(rawDir, `${shot.id}.mkv`);
        const outFile = path.join(shotsDir, `${shot.id}.mp4`);
        if (RESUME && existsSync(rawFile) && existsSync(outFile)) {
          const raw = await probeVideo(rawFile);
          const review = await probeVideo(outFile);
          const playbackRate = review.duration / shot.duration;
          process.stdout.write(
            `\n↪ ${shot.id} 已存在，继续使用 ${review.duration.toFixed(2)}s 待审片段\n`,
          );
          recordedShots.push({
            id: shot.id,
            act: shot.act,
            file: path.relative(OUT, outFile),
            rawFile: path.relative(OUT, rawFile),
            duration: review.duration,
            configuredDuration: shot.duration,
            playbackRate,
            joinPrev: shot.joinPrev,
            captions: shot.captions || [],
          });
          continue;
        }
        if (RESUME && existsSync(rawFile)) {
          const raw = await probeVideo(rawFile);
          const review = await makeReviewClip(rawFile, shot, outFile);
          const playbackRate = review.duration / shot.duration;
          process.stdout.write(
            `\n↪ ${shot.id} 复用原片 ${raw.duration.toFixed(2)}s，重新编码待审片 ${review.duration.toFixed(2)}s\n`,
          );
          recordedShots.push({
            id: shot.id,
            act: shot.act,
            file: path.relative(OUT, outFile),
            rawFile: path.relative(OUT, rawFile),
            duration: review.duration,
            configuredDuration: shot.duration,
            playbackRate,
            joinPrev: shot.joinPrev,
            captions: shot.captions || [],
          });
          continue;
        }
        process.stdout.write(`\n▶ ${shot.id}  ${target.baseUrl}${shot.url(target, content)}\n`);
        await openShot(page, shot);
        const frames = await captureFrames(page, shot);
        const raw = await buildRawVideo(frames, shot, rawFile);
        const review = await makeReviewClip(rawFile, shot, outFile);
        // 原片按分镜录得更长，组装阶段再倍速到配置时长。
        const playbackRate = review.duration / shot.duration;
        process.stdout.write(
          `   原片 ${raw.duration.toFixed(2)}s → 待审 ${review.duration.toFixed(2)}s → ` +
          `组装 ${playbackRate.toFixed(2)}× / ${shot.duration.toFixed(2)}s\n`,
        );
        recordedShots.push({
          id: shot.id,
          act: shot.act,
          file: path.relative(OUT, outFile),
          rawFile: path.relative(OUT, rawFile),
          duration: review.duration,
          configuredDuration: shot.duration,
          playbackRate,
          joinPrev: shot.joinPrev,
          captions: shot.captions || [],
        });
      }

      const manifest = mergeShotManifest({
        configuredShots: shots,
        recordedShots,
        existingManifest,
        partial: Boolean(ONLY.length),
        metadata: {
          recordedAt: new Date().toISOString(),
          snapshotId: target.snapshotId,
          viewport: output.viewport,
          captureFps: output.captureFps,
          fps: output.fps,
        },
      });
      await writeFile(path.join(OUT, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
      process.stdout.write(
        `\n✓ 已录制 ${manifest.shots.length} 个待审片段。现在停止；请审查 out/shots/。\n`,
      );
      if (warnings.length) process.stdout.write(`⚠ ${warnings.length} 条交互警告，请优先检查对应镜头。\n`);
    } finally {
      await context.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
