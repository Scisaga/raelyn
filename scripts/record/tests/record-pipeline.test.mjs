import assert from "node:assert/strict";
import test from "node:test";

import { output, shots } from "../tour.config.mjs";
import { mergeShotManifest } from "../lib/manifest.mjs";
import { buildTimeline } from "../lib/timeline.mjs";

function recordedShot(shot, duration) {
  return {
    id: shot.id,
    act: "stale-act",
    file: `shots/${shot.id}.mp4`,
    duration,
    playbackRate: output.playbackRate,
    configuredDuration: 999,
    joinPrev: 999,
    captions: [{ at: 0, hold: 1, zh: "旧字幕", en: "stale" }],
    narration: [{ at: 0, hold: 1, zh: "旧配音稿" }],
  };
}

test("时间线始终读取当前配置中的字幕、配音稿与转场", () => {
  const shot = shots[0];
  const timeline = buildTimeline({ shots: [recordedShot(shot, 8.25)] });
  const segment = timeline.segments.find((item) => item.kind === "shot");

  assert.equal(segment.sourceDuration, 8.25);
  assert.equal(segment.playbackRate, output.playbackRate);
  assert.equal(segment.duration, 8.25 / output.playbackRate);
  assert.equal(segment.overlap, shot.joinPrev);
  assert.deepEqual(segment.captions, shot.captions);
  assert.deepEqual(segment.narration, shot.narration);
});

test("完整时间线只包含录制镜头，不插入 SVG 幕间卡", () => {
  const timeline = buildTimeline({ shots: shots.map((shot) => recordedShot(shot, shot.duration)) });

  assert.equal(timeline.segments.length, shots.length);
  assert.ok(timeline.segments.every((segment) => segment.kind === "shot"));
});

test("项目地址只作为最后一条画面字幕，不进入配音稿", () => {
  const projectUrl = "https://github.com/scisaga/raelyn";
  const lastShot = shots.at(-1);
  const lastCaption = lastShot.captions.at(-1);
  const narration = shots.flatMap((shot) => shot.narration || []).map((item) => item.zh).join("\n");

  assert.match(lastCaption.zh, new RegExp(projectUrl.replaceAll(".", "\\.")));
  assert.match(lastCaption.en, new RegExp(projectUrl.replaceAll(".", "\\.")));
  assert.doesNotMatch(narration, new RegExp(projectUrl.replaceAll(".", "\\.")));
});

test("所有画面字幕都保持单行", () => {
  for (const caption of shots.flatMap((shot) => shot.captions || [])) {
    assert.doesNotMatch(caption.zh, /\r|\n/);
    assert.doesNotMatch(caption.en, /\r|\n/);
  }
});

test("局部重录只替换目标镜头并保持配置顺序", () => {
  const configuredShots = shots.slice(0, 3);
  const existingManifest = {
    snapshotId: "snapshot-1",
    shots: configuredShots.map((shot, index) => recordedShot(shot, index + 1)),
  };
  const replacement = recordedShot(configuredShots[1], 12.5);

  const manifest = mergeShotManifest({
    configuredShots,
    recordedShots: [replacement],
    existingManifest,
    partial: true,
    metadata: { snapshotId: "snapshot-1", recordedAt: "now" },
  });

  assert.deepEqual(manifest.shots.map((shot) => shot.id), configuredShots.map((shot) => shot.id));
  assert.deepEqual(manifest.shots.map((shot) => shot.duration), [1, 12.5, 3]);
  assert.deepEqual(manifest.shots[0].captions, configuredShots[0].captions);
  assert.equal(manifest.shots[1].configuredDuration, configuredShots[1].duration);
});

test("没有完整清单时拒绝局部重录", () => {
  assert.throws(
    () => mergeShotManifest({
      configuredShots: shots.slice(0, 1),
      recordedShots: [],
      partial: true,
      metadata: {},
    }),
    /需要已有的完整/,
  );
});
