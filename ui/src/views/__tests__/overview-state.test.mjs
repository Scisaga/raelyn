import assert from "node:assert/strict";
import test from "node:test";

import { createOverviewViewMethods } from "../overview-model.js";

test("概览统计保存数据库占用字节数，并保留未知状态", () => {
  const methods = createOverviewViewMethods();
  const state = {
    stats: {
      mediaCount: 0,
      videoCount: 0,
      pendingJobs: 0,
      failedJobs: 0,
      databaseSizeBytes: null,
      recentMedia: [],
      recentVideos: [],
      recentPlaylists: [],
      s3TrackedSizeBytes: null,
      asrCalls: 0,
      llmCalls: 0,
      llmInputTokens: 0,
      llmOutputTokens: 0,
      llmTotalTokens: 0,
    },
  };

  methods.applyStatsPayload.call(state, { database_size_bytes: 123456789 });
  assert.equal(state.stats.databaseSizeBytes, 123456789);

  methods.applyStatsPayload.call(state, { database_size_bytes: null });
  assert.equal(state.stats.databaseSizeBytes, null);
});
