function mergeVideoDetail(baseVideo, detail) {
  const base = baseVideo && typeof baseVideo === "object" ? baseVideo : {};
  const next = detail && typeof detail === "object" ? detail : {};
  return { ...base, ...next };
}

const TRANSCRIPT_VARIANTS = ["plain", "polished"];

function emptyTranscriptVariants() {
  return { plain: false, polished: false };
}

function normalizeTranscriptVariant(value) {
  const normalized = String(value || "")
    .trim()
    .toLowerCase();
  return TRANSCRIPT_VARIANTS.includes(normalized) ? normalized : "";
}

function normalizeTranscriptSource(value) {
  return String(value || "").trim();
}

export function createPlayerModule() {
  return {
    playerPageVideoId: "",
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
    playerTranscriptActiveSource: "",
    playerTranscriptVariant: "",
    playerTranscriptSelectedVariant: "",
    playerTranscriptAvailableVariants: emptyTranscriptVariants(),
    playerTranscriptPolishMethod: "",
    playerTranscriptUpdatedAt: "",
    playerTranscriptNotice: "",
    playerTranscriptRefreshing: false,
    playerTranscriptSwitching: false,
    playerRetranscribeSubmitting: false,
    playerTranscriptPollKey: "",
    playerTranscriptPollTries: 0,
    playerDescription: "",
    playerLoading: false,
    playerError: "",
    playerTab: "transcript",
    playerLoadToken: 0,

    _playerResetState({ keepVideo = false, keepPageVideoId = false } = {}) {
      this.playerAssets = [];
      this.playerVideoUrl = "";
      this.playerVideoDownloadUrl = "";
      this.playerVideoDownloadName = "";
      this.playerAudioDownloadUrl = "";
      this.playerAudioDownloadName = "";
      this.playerTranscriptText = "";
      this.playerTranscriptAssetId = "";
      this.playerTranscriptSource = "";
      this.playerTranscriptActiveSource = "";
      this.playerTranscriptVariant = "";
      this.playerTranscriptSelectedVariant = "";
      this.playerTranscriptAvailableVariants = emptyTranscriptVariants();
      this.playerTranscriptPolishMethod = "";
      this.playerTranscriptUpdatedAt = "";
      this.playerTranscriptNotice = "";
      this.playerTranscriptRefreshing = false;
      this.playerTranscriptSwitching = false;
      this.playerRetranscribeSubmitting = false;
      this.playerTranscriptPollKey = "";
      this.playerTranscriptPollTries = 0;
      this.playerDescription = "";
      this.playerLoading = false;
      this.playerError = "";
      this.playerTab = "transcript";
      this.playerLoadToken = Number(this.playerLoadToken || 0) + 1;
      if (!keepVideo) this.playerVideo = null;
      if (!keepPageVideoId) this.playerPageVideoId = "";
    },

    _playerStopMediaElement() {
      const el = this.$refs && this.$refs.playerVideoEl ? this.$refs.playerVideoEl : null;
      if (!el) return;

      try {
        if (typeof el.pause === "function") el.pause();
      } catch {
        // ignore
      }
      try {
        el.removeAttribute("src");
      } catch {
        // ignore
      }
      try {
        if (typeof el.load === "function") el.load();
      } catch {
        // ignore
      }
    },

    leaveVideoPage() {
      this._playerStopMediaElement();
      this._playerResetState();
    },

    _playerViewActive(videoId = "") {
      if (this.activeView !== "video") return false;
      const currentId = String(this.playerPageVideoId || "").trim();
      if (!videoId) return !!currentId;
      return !!currentId && currentId === String(videoId || "").trim();
    },

    _playerTranscriptTextAssets(assets = null) {
      const list = Array.isArray(assets) ? assets : Array.isArray(this.playerAssets) ? this.playerAssets : [];
      return list.filter(
        (asset) => asset && asset.type === "transcript" && String(asset.format || "").trim().toLowerCase() === "txt"
      );
    },

    _playerTranscriptAvailableVariantsForSource(source = "", assets = null) {
      const sourceValue = normalizeTranscriptSource(source);
      const available = emptyTranscriptVariants();
      const items = this._playerTranscriptTextAssets(assets);
      for (const asset of items) {
        if (sourceValue && normalizeTranscriptSource(asset.source) !== sourceValue) continue;
        const variant = normalizeTranscriptVariant(asset.variant);
        if (variant) available[variant] = true;
      }
      return available;
    },

    _playerTranscriptRequestPath(videoId, { variant = "", source = "" } = {}) {
      const params = new URLSearchParams();
      const normalizedVariant = normalizeTranscriptVariant(variant);
      const normalizedSource = normalizeTranscriptSource(source);
      if (normalizedVariant) params.set("variant", normalizedVariant);
      if (normalizedSource) params.set("source", normalizedSource);
      const query = params.toString();
      return `/videos/${encodeURIComponent(videoId)}/transcript${query ? `?${query}` : ""}`;
    },

    _playerApplyTranscriptPayload(transcript, { updateSelection = true } = {}) {
      const payload = transcript && typeof transcript === "object" ? transcript : null;
      const ok = !!(payload && payload.ok);
      const source = normalizeTranscriptSource(ok ? payload.source : payload && payload.source);
      const variant = normalizeTranscriptVariant(ok ? payload.variant : payload && payload.variant);

      this.playerTranscriptText = ok ? payload.text || "" : "";
      this.playerTranscriptAssetId = ok ? payload.asset_id || "" : "";
      this.playerTranscriptSource = source;
      this.playerTranscriptVariant = variant;
      this.playerTranscriptPolishMethod = ok && variant === "polished" ? payload.polish_method || "" : "";
      this.playerTranscriptUpdatedAt = ok ? payload.updated_at || payload.created_at || "" : "";

      if (updateSelection) {
        this.playerTranscriptActiveSource = source;
        this.playerTranscriptSelectedVariant = variant;
      }

      this.playerTranscriptAvailableVariants = this._playerTranscriptAvailableVariantsForSource(source);
    },

    playerTranscriptVariantAvailable(variant) {
      const normalized = normalizeTranscriptVariant(variant);
      return !!(normalized && this.playerTranscriptAvailableVariants && this.playerTranscriptAvailableVariants[normalized]);
    },

    playerTranscriptVariantActive(variant) {
      const normalized = normalizeTranscriptVariant(variant);
      const current = normalizeTranscriptVariant(this.playerTranscriptSelectedVariant || this.playerTranscriptVariant);
      return !!normalized && normalized === current;
    },

    async openVideoPlayer(video) {
      if (!video || !video.id) return;
      this.modals.addMedia = false;
      this.modals.createPlaylist = false;
      this.modals.mediaImport = false;
      if (typeof this._stopDocumentMediaPlayback === "function") {
        this._stopDocumentMediaPlayback({ clearSources: true });
      }

      const nextId = String(video.id || "").trim();
      const currentUrl = `${window.location.pathname || "/"}${window.location.search || ""}`;
      const currentState = history.state && typeof history.state === "object" ? history.state : {};
      const returnTo = this.activeView === "video" ? String(currentState.returnTo || "").trim() : currentUrl;

      this.playerPageVideoId = nextId;
      this.playerVideo = video;
      this._playerResetState({ keepVideo: true, keepPageVideoId: true });
      this.switchView("video", {
        push: true,
        refresh: false,
        stateExtras: returnTo ? { returnTo } : null,
      });
      await this.loadVideoPage({ previewVideo: video, videoId: nextId });
    },

    async loadVideoPage({ previewVideo = null, videoId = null } = {}) {
      const nextId = String(videoId || this.playerPageVideoId || (this.playerVideo && this.playerVideo.id) || "").trim();
      if (!nextId) {
        this.closeVideoPage({ fallbackToVideos: true });
        return;
      }

      this.playerPageVideoId = nextId;
      if (!this.playerVideo || String(this.playerVideo.id || "").trim() !== nextId) {
        this.playerVideo = previewVideo && String(previewVideo.id || "").trim() === nextId ? previewVideo : { id: nextId };
      }

      this._playerResetState({ keepVideo: true, keepPageVideoId: true });
      this.playerLoading = true;
      const loadToken = Number(this.playerLoadToken || 0);

      try {
        const [assets, transcript, detail] = await Promise.all([
          this.api(`/videos/${encodeURIComponent(nextId)}/assets?presign=true&download=true`),
          this.api(`/videos/${encodeURIComponent(nextId)}/transcript`),
          this.api(`/videos/${encodeURIComponent(nextId)}`),
        ]);

        if (Number(this.playerLoadToken || 0) !== loadToken || !this._playerViewActive(nextId)) return;

        this.playerAssets = Array.isArray(assets) ? assets : [];
        this.playerVideo = mergeVideoDetail(previewVideo || this.playerVideo, detail);
        this.playerDescription = this.playerVideo && this.playerVideo.description ? this.playerVideo.description : "";

        const videos = this.playerAssets.filter((asset) => asset && asset.type === "video");
        const mp4 = videos.find((asset) => String(asset.format || "").toLowerCase() === "mp4") || videos[0] || null;
        this.playerVideoUrl = (mp4 && this.assetContentUrl(mp4)) || "";
        this.playerVideoDownloadUrl = (mp4 && this.assetDownloadUrl(mp4)) || "";
        this.playerVideoDownloadName = (mp4 && mp4.filename) || "";

        const audios = this.playerAssets.filter((asset) => asset && asset.type === "audio");
        const m4a = audios.find((asset) => String(asset.format || "").toLowerCase() === "m4a") || audios[0] || null;
        this.playerAudioDownloadUrl = (m4a && this.assetDownloadUrl(m4a)) || "";
        this.playerAudioDownloadName = (m4a && m4a.filename) || "";

        this._playerApplyTranscriptPayload(transcript);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        if (Number(this.playerLoadToken || 0) !== loadToken || !this._playerViewActive(nextId)) return;
        if (String(msg).startsWith("404:")) {
          this.closeVideoPage({ fallbackToVideos: true });
          return;
        }
        this.playerError = msg;
      } finally {
        if (Number(this.playerLoadToken || 0) === loadToken && this._playerViewActive(nextId)) this.playerLoading = false;
      }
    },

    closeVideoPage({ fallbackToVideos = false } = {}) {
      const currentState = history.state && typeof history.state === "object" ? history.state : {};
      const returnTo = String(currentState.returnTo || "").trim();
      this.leaveVideoPage();

      if (!fallbackToVideos && returnTo) {
        history.back();
        return;
      }

      this.switchView("videos", { push: false });
    },

    async switchPlayerTranscriptVariant(targetVariant) {
      const videoId = String(this.playerPageVideoId || (this.playerVideo && this.playerVideo.id) || "").trim();
      if (!videoId || this.playerTranscriptSwitching || !this._playerViewActive(videoId)) return;

      const nextVariant = normalizeTranscriptVariant(targetVariant);
      const currentVariant = normalizeTranscriptVariant(this.playerTranscriptSelectedVariant || this.playerTranscriptVariant);
      const source = normalizeTranscriptSource(this.playerTranscriptActiveSource || this.playerTranscriptSource);
      if (!nextVariant || !source || nextVariant === currentVariant) return;

      this.playerTranscriptAvailableVariants = this._playerTranscriptAvailableVariantsForSource(source);
      if (!this.playerTranscriptVariantAvailable(nextVariant)) return;

      this.playerTranscriptSwitching = true;
      try {
        const transcript = await this.api(
          this._playerTranscriptRequestPath(videoId, {
            variant: nextVariant,
            source,
          })
        );
        if (!this._playerViewActive(videoId)) return;
        if (transcript && transcript.ok) this._playerApplyTranscriptPayload(transcript);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        this.toastError(`切换转写版本失败：${msg}`);
      } finally {
        this.playerTranscriptSwitching = false;
      }
    },

    async refreshPlayerTranscript() {
      const videoId = String(this.playerPageVideoId || (this.playerVideo && this.playerVideo.id) || "").trim();
      if (!videoId || this.playerTranscriptRefreshing || !this._playerViewActive(videoId)) return;
      this.playerTranscriptRefreshing = true;
      try {
        const transcript = await this.api(
          this._playerTranscriptRequestPath(videoId, {
            variant: this.playerTranscriptSelectedVariant || this.playerTranscriptVariant,
            source: this.playerTranscriptActiveSource || this.playerTranscriptSource,
          })
        );
        if (!this._playerViewActive(videoId)) return;
        this._playerApplyTranscriptPayload(transcript);
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
          if (!this._playerViewActive(videoId)) return;
          if (!this.playerVideo || String(this.playerVideo.id || "") !== String(videoId)) return;

          const tries = Number(this.playerTranscriptPollTries || 0);
          if (tries >= 40) {
            this.playerTranscriptNotice = "重新转写任务已提交；稍后可再次点击或手动刷新文本。";
            return;
          }
          this.playerTranscriptPollTries = tries + 1;

          try {
            const transcript = await this.api(
              this._playerTranscriptRequestPath(videoId, {
                variant: this.playerTranscriptSelectedVariant || this.playerTranscriptVariant,
                source: this.playerTranscriptActiveSource || this.playerTranscriptSource,
              })
            );
            if (!this._playerViewActive(videoId)) return;
            if (transcript && transcript.ok) {
              const nextAssetId = transcript.asset_id || "";
              const nextText = transcript.text || "";
              const nextVariant = normalizeTranscriptVariant(transcript.variant);
              const nextMethod = transcript.polish_method || "";
              const nextUpdatedAt = transcript.updated_at || transcript.created_at || "";
              const changed =
                (nextText && String(nextText) !== String(this.playerTranscriptText || "")) ||
                (prevAssetId && nextAssetId && nextAssetId !== prevAssetId) ||
                (nextVariant && nextVariant !== String(this.playerTranscriptVariant || "")) ||
                (nextMethod && nextMethod !== String(this.playerTranscriptPolishMethod || "")) ||
                (nextUpdatedAt && nextUpdatedAt !== String(this.playerTranscriptUpdatedAt || ""));
              if (changed) {
                this._playerApplyTranscriptPayload(transcript);
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
      const videoId = String(this.playerPageVideoId || (this.playerVideo && this.playerVideo.id) || "").trim();
      if (!videoId || this.playerRetranscribeSubmitting || !this._playerViewActive(videoId)) return;
      this.playerRetranscribeSubmitting = true;
      this.playerTranscriptNotice = "";
      const prevAssetId = this.playerTranscriptAssetId || "";
      try {
        await this.api(`/videos/${encodeURIComponent(videoId)}/transcript/retranscribe`, { method: "POST" });
        if (!this._playerViewActive(videoId)) return;
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
