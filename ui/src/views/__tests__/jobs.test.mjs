import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createJobsViewMethods } from "../jobs-model.js";

test("任务类型使用本地 SVG 图标和独立语义色，不与运行状态混用", () => {
  const methods = createJobsViewMethods();
  const videoIcon = methods.jobTypeIcon({ type: "video.download" });
  const asrIcon = methods.jobTypeIcon({ type: "video.asr_transcribe" });
  const fieldIcon = methods.jobTypeIcon({ type: "playlist.build_event_map_snapshot" });
  const briefIcon = methods.jobTypeIcon({ type: "brief.generate_period" });

  assert.match(videoIcon, /^<svg[\s\S]*<rect/);
  assert.notEqual(asrIcon, videoIcon);
  assert.notEqual(fieldIcon, videoIcon);
  assert.notEqual(briefIcon, fieldIcon);
  assert.match(methods.jobTypeIconClass({ type: "video.download" }), /text-sky-300/);
  assert.match(methods.jobTypeIconClass({ type: "playlist.build_event_map_snapshot" }), /text-violet-300/);
  assert.match(methods.jobTypeIconClass({ type: "brief.generate_period" }), /text-amber-300/);
  assert.doesNotMatch(methods.jobTypeIconClass({ type: "video.download", status: "failed" }), /rose/);
});

test("活动任务与历史任务行都在标题左侧渲染固定尺寸图标", async () => {
  const template = await readFile(new URL("../../../templates/app/views/jobs.html", import.meta.url), "utf8");
  assert.equal((template.match(/x-html="jobTypeIcon\(j\)"/g) || []).length, 2);
  assert.equal((template.match(/:class="jobTypeIconClass\(j\)"/g) || []).length, 2);
  assert.equal((template.match(/h-8 w-8 shrink-0/g) || []).length, 2);
});
