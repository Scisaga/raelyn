export function createOverviewViewMethods() {
  return {
    applyStatsPayload(stats) {
      const payload = stats && typeof stats === "object" ? stats : {};
      this.stats.mediaCount = payload.media_count || 0;
      this.stats.videoCount = payload.video_count || 0;
      this.stats.pendingJobs = payload.pending_jobs || 0;
      this.stats.failedJobs = payload.failed_jobs || 0;
      this.stats.recentMedia = Array.isArray(payload.recent_media) ? payload.recent_media : [];
      this.stats.recentVideos = Array.isArray(payload.recent_videos) ? payload.recent_videos : [];
      this.stats.recentPlaylists = Array.isArray(payload.recent_playlists) ? payload.recent_playlists : [];
      this.stats.s3TrackedSizeBytes =
        payload.s3_tracked_size_bytes === null || payload.s3_tracked_size_bytes === undefined
          ? null
          : Number(payload.s3_tracked_size_bytes);
      this.stats.asrCalls = Number(payload.asr_calls || 0);
      this.stats.llmCalls = Number(payload.llm_calls || 0);
      this.stats.llmInputTokens = Number(payload.llm_input_tokens || 0);
      this.stats.llmOutputTokens = Number(payload.llm_output_tokens || 0);
      this.stats.llmTotalTokens = Number(payload.llm_total_tokens || 0);
      return payload;
    },

    async loadStats({ payload = null, silent = false, throwOnError = false } = {}) {
      try {
        const stats = payload || (await this.api(`/stats`));
        return this.applyStatsPayload(stats);
      } catch (e) {
        if (!silent) this.globalStatus = `error: ${e.message}`;
        if (throwOnError) throw e;
        return null;
      }
    },
  };
}
