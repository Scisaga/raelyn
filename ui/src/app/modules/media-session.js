const MEDIA_SESSION_POSITION_SYNC_INTERVAL_MS = 1000;
const MEDIA_SESSION_SEEK_SECONDS = 15;

function imageMimeTypeFromUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  const normalized = raw.split("#", 1)[0].split("?", 1)[0].toLowerCase();
  if (normalized.endsWith(".png")) return "image/png";
  if (normalized.endsWith(".webp")) return "image/webp";
  if (normalized.endsWith(".gif")) return "image/gif";
  if (normalized.endsWith(".svg")) return "image/svg+xml";
  if (normalized.endsWith(".jpg") || normalized.endsWith(".jpeg")) return "image/jpeg";
  return "";
}

export function createMediaSessionModule() {
  return {
    mediaSessionSupported: false,
    documentPictureInPictureSupported: false,
    videoPictureInPictureSupported: false,
    _mediaSessionBound: false,
    _mediaSessionVisibilityHandler: null,
    _mediaSessionPageHideHandler: null,
    _mediaSessionFreezeHandler: null,
    _mediaSessionPositionSyncAt: 0,

    _setSystemMediaActionHandler(action, handler) {
      if (!this.mediaSessionSupported) return;
      try {
        if (!window.navigator || !window.navigator.mediaSession) return;
        if (typeof window.navigator.mediaSession.setActionHandler !== "function") return;
        window.navigator.mediaSession.setActionHandler(action, handler || null);
      } catch {
        // ignore unsupported actions on partial implementations
      }
    },

    _clearSystemMediaActionHandlers() {
      [
        "play",
        "pause",
        "previoustrack",
        "nexttrack",
        "seekbackward",
        "seekforward",
      ].forEach((action) => this._setSystemMediaActionHandler(action, null));
    },

    _systemMediaArtworkItems() {
      const current = this.playlistCurrentVideo;
      if (!current) return [];

      const preferred = this.playlistCurrentAudioThumbnail ? this.playlistCurrentAudioThumbnail() : { url: "" };
      const remote = current && current.thumbnail_url ? String(current.thumbnail_url).trim() : "";
      const seen = new Set();
      const items = [];

      [preferred && preferred.url ? String(preferred.url).trim() : "", remote].forEach((src) => {
        if (!src || seen.has(src)) return;
        seen.add(src);
        const item = { src };
        const type = imageMimeTypeFromUrl(src);
        if (type) item.type = type;
        items.push(item);
      });

      return items;
    },

    _systemMediaAllowsTrackActions() {
      if (!this.playlistCurrentVideo) return false;
      if (this.playlistAudioOnly) return true;
      return !!String(this.playlistPlayerAudioUrl || "").trim();
    },

    playlistVideoPictureInPictureActive() {
      try {
        const videoEl = this.$refs && this.$refs.playlistVideoEl ? this.$refs.playlistVideoEl : null;
        if (!videoEl) return false;
        if (document.pictureInPictureElement && document.pictureInPictureElement === videoEl) return true;
        if (typeof videoEl.webkitPresentationMode === "string" && videoEl.webkitPresentationMode === "picture-in-picture") {
          return true;
        }
      } catch {
        return false;
      }
      return false;
    },

    initMediaSession() {
      this.mediaSessionSupported = !!(window.navigator && window.navigator.mediaSession);
      this.documentPictureInPictureSupported = !!(window && "documentPictureInPicture" in window);
      this.videoPictureInPictureSupported = !!(
        document &&
        document.pictureInPictureEnabled &&
        typeof HTMLVideoElement !== "undefined" &&
        HTMLVideoElement.prototype &&
        typeof HTMLVideoElement.prototype.requestPictureInPicture === "function"
      );

      if (this._mediaSessionBound) {
        this.syncSystemMediaSession({ forcePosition: true });
        return;
      }

      const onVisibilityChange = () => this.handleVisibilityMediaPolicy();
      const onPageHide = () => this.handleVisibilityMediaPolicy();
      const onFreeze = () => this.handleVisibilityMediaPolicy();
      try {
        document.addEventListener("visibilitychange", onVisibilityChange);
        this._mediaSessionVisibilityHandler = onVisibilityChange;
      } catch {
        // ignore
      }
      try {
        window.addEventListener("pagehide", onPageHide);
        this._mediaSessionPageHideHandler = onPageHide;
      } catch {
        // ignore
      }
      try {
        document.addEventListener("freeze", onFreeze);
        this._mediaSessionFreezeHandler = onFreeze;
      } catch {
        // ignore
      }

      this._mediaSessionBound = true;
      this.bindSystemMediaActions();
      this.syncSystemMediaSession({ forcePosition: true });
    },

    bindSystemMediaActions() {
      if (!this.mediaSessionSupported) return;

      const hasCurrent = !!(this.playlistCurrentVideo && this.playlistCurrentVideo.id);
      const canTrack = hasCurrent && this._systemMediaAllowsTrackActions();
      const canSeek = canTrack && Number(this.playlistMediaDurationSec || 0) > 0;

      this._setSystemMediaActionHandler("play", hasCurrent ? () => this.playlistMediaPlay() : null);
      this._setSystemMediaActionHandler("pause", hasCurrent ? () => this.playlistMediaPause() : null);
      this._setSystemMediaActionHandler(
        "previoustrack",
        canTrack && this.playlistHasPrevVideo && this.playlistHasPrevVideo() ? () => this.playlistPrevVideo() : null
      );
      this._setSystemMediaActionHandler(
        "nexttrack",
        canTrack && this.playlistHasNextVideo && this.playlistHasNextVideo() ? () => this.playlistNextVideo() : null
      );
      this._setSystemMediaActionHandler("seekbackward", canSeek ? () => this.playlistSeekBy(-MEDIA_SESSION_SEEK_SECONDS) : null);
      this._setSystemMediaActionHandler("seekforward", canSeek ? () => this.playlistSeekBy(MEDIA_SESSION_SEEK_SECONDS) : null);
    },

    syncSystemMediaSession({ forcePosition = false } = {}) {
      if (!this.mediaSessionSupported) return;
      const mediaSession = window.navigator && window.navigator.mediaSession ? window.navigator.mediaSession : null;
      if (!mediaSession) return;

      const current = this.playlistCurrentVideo;
      if (!current) {
        this._clearSystemMediaActionHandlers();
        try {
          mediaSession.metadata = null;
        } catch {
          // ignore
        }
        try {
          mediaSession.playbackState = "none";
        } catch {
          // ignore
        }
        try {
          if (typeof mediaSession.setPositionState === "function") mediaSession.setPositionState();
        } catch {
          // ignore
        }
        this._mediaSessionPositionSyncAt = 0;
        return;
      }

      const title = String(current.title || current.url || current.provider_video_id || "").trim() || "未命名视频";
      const artist = String(current.media_name || current.media_id || "").trim();
      const album = String((this.playlistDetail && this.playlistDetail.name) || "").trim();
      const artwork = this._systemMediaArtworkItems();

      try {
        if (typeof window.MediaMetadata === "function") {
          mediaSession.metadata = new window.MediaMetadata({
            title,
            artist,
            album,
            artwork,
          });
        }
      } catch {
        // ignore metadata shape errors from partial implementations
      }

      try {
        mediaSession.playbackState = this.playlistMediaPlaying ? "playing" : "paused";
      } catch {
        // ignore
      }

      this.bindSystemMediaActions();

      const now = Date.now();
      if (!forcePosition && now - Number(this._mediaSessionPositionSyncAt || 0) < MEDIA_SESSION_POSITION_SYNC_INTERVAL_MS) return;

      try {
        if (typeof mediaSession.setPositionState !== "function") return;
        const el = this.playlistActiveMediaEl ? this.playlistActiveMediaEl() : null;
        const duration = Number(this.playlistMediaDurationSec || (el && el.duration) || 0);
        const currentTime = Number(this.playlistMediaCurrentTimeSec || (el && el.currentTime) || 0);
        const playbackRate = Number((el && el.playbackRate) || 1) || 1;

        if (Number.isFinite(duration) && duration > 0 && Number.isFinite(currentTime) && currentTime >= 0) {
          mediaSession.setPositionState({
            duration,
            playbackRate,
            position: Math.max(0, Math.min(duration, currentTime)),
          });
        } else {
          mediaSession.setPositionState();
        }
        this._mediaSessionPositionSyncAt = now;
      } catch {
        // ignore unsupported position state implementations
      }
    },

    teardownSystemMediaSession() {
      try {
        if (this._mediaSessionVisibilityHandler) {
          document.removeEventListener("visibilitychange", this._mediaSessionVisibilityHandler);
        }
      } catch {
        // ignore
      }
      try {
        if (this._mediaSessionPageHideHandler) {
          window.removeEventListener("pagehide", this._mediaSessionPageHideHandler);
        }
      } catch {
        // ignore
      }
      try {
        if (this._mediaSessionFreezeHandler) {
          document.removeEventListener("freeze", this._mediaSessionFreezeHandler);
        }
      } catch {
        // ignore
      }
      this._mediaSessionVisibilityHandler = null;
      this._mediaSessionPageHideHandler = null;
      this._mediaSessionFreezeHandler = null;
      this._mediaSessionBound = false;
      this._mediaSessionPositionSyncAt = 0;

      if (!this.mediaSessionSupported) return;
      const mediaSession = window.navigator && window.navigator.mediaSession ? window.navigator.mediaSession : null;
      if (!mediaSession) return;

      this._clearSystemMediaActionHandlers();
      try {
        mediaSession.metadata = null;
      } catch {
        // ignore
      }
      try {
        mediaSession.playbackState = "none";
      } catch {
        // ignore
      }
      try {
        if (typeof mediaSession.setPositionState === "function") mediaSession.setPositionState();
      } catch {
        // ignore
      }
    },

    handleVisibilityMediaPolicy() {
      if (!this._mediaSessionBound) return;

      const hidden = !!(document && document.hidden);
      if (!hidden) {
        this.syncSystemMediaSession({ forcePosition: true });
        return;
      }

      if (!this.playlistCurrentVideo || this.playlistAudioOnly) {
        this.syncSystemMediaSession({ forcePosition: true });
        return;
      }

      if (this.playlistVideoPictureInPictureActive()) {
        this.syncSystemMediaSession({ forcePosition: true });
        return;
      }

      if (!String(this.playlistPlayerAudioUrl || "").trim()) {
        this.syncSystemMediaSession({ forcePosition: true });
        return;
      }

      if (!this.playlistMediaPlaying && !(this.playlistAnyMediaPlaying && this.playlistAnyMediaPlaying())) {
        this.syncSystemMediaSession({ forcePosition: true });
        return;
      }

      if (typeof this.playlistActivateBackgroundAudio === "function") {
        this.playlistActivateBackgroundAudio();
        return;
      }

      this.playlistToggleAudioOnly();
    },
  };
}
