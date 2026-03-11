export function createPlayerModule() {
  return {
    playerVideo: null,
    playerAssets: [],
    playerVideoUrl: "",
    playerVideoDownloadUrl: "",
    playerVideoDownloadName: "",
    playerAudioDownloadUrl: "",
    playerAudioDownloadName: "",
    playerTranscriptText: "",
    playerTranscriptAssetId: "",
    playerTranscriptSource: "",
    playerTranscriptVariant: "",
    playerTranscriptPolishMethod: "",
    playerTranscriptUpdatedAt: "",
    playerTranscriptNotice: "",
    playerTranscriptRefreshing: false,
    playerRetranscribeSubmitting: false,
    playerTranscriptPollKey: "",
    playerTranscriptPollTries: 0,
    playerDescription: "",
    playerLoading: false,
    playerError: "",
    playerTab: "transcript",

    async openVideoPlayer(video) {
      if (!video || !video.id) return;
      this.modals.addMedia = false;
      this.modals.createPlaylist = false;
      this.modals.mediaImport = false;
      this.modals.videoPlayer = true;
      this.playerVideo = video;
      this.playerAssets = [];
      this.playerVideoUrl = "";
      this.playerVideoDownloadUrl = "";
      this.playerVideoDownloadName = "";
      this.playerAudioDownloadUrl = "";
      this.playerAudioDownloadName = "";
      this.playerTranscriptText = "";
      this.playerTranscriptAssetId = "";
      this.playerTranscriptSource = "";
      this.playerTranscriptVariant = "";
      this.playerTranscriptPolishMethod = "";
      this.playerTranscriptUpdatedAt = "";
      this.playerTranscriptNotice = "";
      this.playerTranscriptRefreshing = false;
      this.playerRetranscribeSubmitting = false;
      this.playerTranscriptPollKey = "";
      this.playerTranscriptPollTries = 0;
      this.playerDescription = "";
      this.playerError = "";
      this.playerLoading = true;
      this.playerTab = "transcript";

      try {
        const [assets, transcript, detail] = await Promise.all([
          this.api(`/videos/${video.id}/assets?presign=true&download=true`),
          this.api(`/videos/${video.id}/transcript`),
          this.api(`/videos/${video.id}`),
        ]);

        this.playerAssets = Array.isArray(assets) ? assets : [];
        const detailDesc = detail && typeof detail === "object" ? detail.description || "" : "";
        this.playerDescription = detailDesc || "";

        const videos = this.playerAssets.filter((asset) => asset.type === "video" && asset.presigned_url);
        const mp4 = videos.find((asset) => String(asset.format || "").toLowerCase() === "mp4") || videos[0] || null;
        this.playerVideoUrl = (mp4 && mp4.presigned_url) || "";
        this.playerVideoDownloadUrl = (mp4 && (mp4.download_url || mp4.presigned_url)) || "";
        this.playerVideoDownloadName = (mp4 && mp4.filename) || "";

        const audios = this.playerAssets.filter((asset) => asset.type === "audio" && (asset.download_url || asset.presigned_url));
        const m4a = audios.find((asset) => String(asset.format || "").toLowerCase() === "m4a") || audios[0] || null;
        this.playerAudioDownloadUrl = (m4a && (m4a.download_url || m4a.presigned_url)) || "";
        this.playerAudioDownloadName = (m4a && m4a.filename) || "";

        this.playerTranscriptText = transcript && transcript.ok ? transcript.text || "" : "";
        this.playerTranscriptAssetId = transcript && transcript.ok ? transcript.asset_id || "" : "";
        this.playerTranscriptSource = transcript && transcript.ok ? transcript.source || "" : "";
        this.playerTranscriptVariant = transcript && transcript.ok ? transcript.variant || "" : "";
        this.playerTranscriptPolishMethod = transcript && transcript.ok ? transcript.polish_method || "" : "";
        this.playerTranscriptUpdatedAt = transcript && transcript.ok ? transcript.updated_at || transcript.created_at || "" : "";
      } catch (e) {
        this.playerError = e && e.message ? e.message : String(e);
      } finally {
        this.playerLoading = false;
      }
    },

    closeVideoPlayer() {
      this.modals.videoPlayer = false;
      this.playerVideoUrl = "";
      this.playerVideoDownloadUrl = "";
      this.playerAudioDownloadUrl = "";
      this.playerVideo = null;
      this.playerTranscriptPollKey = "";
    },

    async refreshPlayerTranscript() {
      const videoId = this.playerVideo && this.playerVideo.id ? String(this.playerVideo.id) : "";
      if (!videoId || this.playerTranscriptRefreshing) return;
      this.playerTranscriptRefreshing = true;
      try {
        const transcript = await this.api(`/videos/${encodeURIComponent(videoId)}/transcript`);
        if (transcript && transcript.ok) {
          this.playerTranscriptText = transcript.text || "";
          this.playerTranscriptAssetId = transcript.asset_id || this.playerTranscriptAssetId || "";
          this.playerTranscriptSource = transcript.source || this.playerTranscriptSource || "";
          this.playerTranscriptVariant = transcript.variant || this.playerTranscriptVariant || "";
          this.playerTranscriptPolishMethod = transcript.polish_method || this.playerTranscriptPolishMethod || "";
          this.playerTranscriptUpdatedAt = transcript.updated_at || transcript.created_at || this.playerTranscriptUpdatedAt || "";
        }
      } finally {
        this.playerTranscriptRefreshing = false;
      }
    },

    _playerPollTranscriptAfterRetranscribe(videoId, prevAssetId) {
      try {
        const key = `${String(videoId)}:${Date.now()}`;
        this.playerTranscriptPollKey = key;
        this.playerTranscriptPollTries = 0;

        const tick = async () => {
          if (this.playerTranscriptPollKey !== key) return;
          if (!this.modals.videoPlayer) return;
          if (!this.playerVideo || String(this.playerVideo.id || "") !== String(videoId)) return;

          const tries = Number(this.playerTranscriptPollTries || 0);
          if (tries >= 40) {
            this.playerTranscriptNotice = "重新转写任务已提交；稍后可再次点击或手动刷新文本。";
            return;
          }
          this.playerTranscriptPollTries = tries + 1;

          try {
            const transcript = await this.api(`/videos/${encodeURIComponent(videoId)}/transcript`);
            if (transcript && transcript.ok) {
              const nextAssetId = transcript.asset_id || "";
              const nextText = transcript.text || "";
              const nextVariant = transcript.variant || "";
              const nextMethod = transcript.polish_method || "";
              const nextUpdatedAt = transcript.updated_at || transcript.created_at || "";
              const changed =
                (nextText && String(nextText) !== String(this.playerTranscriptText || "")) ||
                (prevAssetId && nextAssetId && nextAssetId !== prevAssetId) ||
                (nextVariant && nextVariant !== String(this.playerTranscriptVariant || "")) ||
                (nextMethod && nextMethod !== String(this.playerTranscriptPolishMethod || "")) ||
                (nextUpdatedAt && nextUpdatedAt !== String(this.playerTranscriptUpdatedAt || ""));
              if (changed) {
                this.playerTranscriptText = nextText;
                this.playerTranscriptAssetId = nextAssetId || this.playerTranscriptAssetId || "";
                this.playerTranscriptSource = transcript.source || this.playerTranscriptSource || "";
                this.playerTranscriptVariant = transcript.variant || this.playerTranscriptVariant || "";
                this.playerTranscriptPolishMethod = transcript.polish_method || this.playerTranscriptPolishMethod || "";
                this.playerTranscriptUpdatedAt = nextUpdatedAt || this.playerTranscriptUpdatedAt || "";
                this.playerTranscriptNotice = "转写文本已更新。";
                this.playerTranscriptPollKey = "";
                return;
              }
            }
          } catch {
            // ignore
          }

          const rawDelay = 1200 + tries * 650;
          const delay = Math.min(9000, rawDelay);
          setTimeout(tick, delay);
        };

        setTimeout(tick, 1200);
      } catch {
        // ignore
      }
    },

    async retranscribePlayerTranscript() {
      const videoId = this.playerVideo && this.playerVideo.id ? String(this.playerVideo.id) : "";
      if (!videoId || this.playerRetranscribeSubmitting) return;
      this.playerRetranscribeSubmitting = true;
      this.playerTranscriptNotice = "";
      const prevAssetId = this.playerTranscriptAssetId || "";
      try {
        await this.api(`/videos/${encodeURIComponent(videoId)}/transcript/retranscribe`, { method: "POST" });
        this.playerTranscriptNotice = "已提交重新转写，正在等待生成…";
        this.toastSuccess("已提交重新转写任务", { action: this.toastJobsAction() });
        this._playerPollTranscriptAfterRetranscribe(videoId, prevAssetId);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.toastError(`重新转写提交失败：${msg}`, { action: this.toastJobsAction() });
        this.playerTranscriptNotice = `重新转写失败：${msg}`;
      } finally {
        this.playerRetranscribeSubmitting = false;
      }
    },

    maximizePlayer() {
      try {
        const el = this.$refs && this.$refs.playerVideoEl;
        if (el && el.requestFullscreen) el.requestFullscreen();
      } catch {
        // ignore
      }
    },
  };
}
