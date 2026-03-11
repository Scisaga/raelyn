export function createOverviewViewMethods() {
  return {
    async loadStats() {
      try {
        const stats = await this.api(`/stats`);
        this.stats.mediaCount = stats.media_count || 0;
        this.stats.videoCount = stats.video_count || 0;
        this.stats.pendingJobs = stats.pending_jobs || 0;
        this.stats.failedJobs = stats.failed_jobs || 0;
        this.stats.recentMedia = Array.isArray(stats.recent_media) ? stats.recent_media : [];
        this.stats.recentVideos = Array.isArray(stats.recent_videos) ? stats.recent_videos : [];
        this.stats.recentPlaylists = Array.isArray(stats.recent_playlists) ? stats.recent_playlists : [];
        this.stats.s3TrackedSizeBytes =
          stats.s3_tracked_size_bytes === null || stats.s3_tracked_size_bytes === undefined ? null : Number(stats.s3_tracked_size_bytes);
        this.stats.asrCalls = Number(stats.asr_calls || 0);
        this.stats.llmCalls = Number(stats.llm_calls || 0);
        this.stats.llmInputTokens = Number(stats.llm_input_tokens || 0);
        this.stats.llmOutputTokens = Number(stats.llm_output_tokens || 0);
        this.stats.llmTotalTokens = Number(stats.llm_total_tokens || 0);
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },
  };
}
