export function createVideosViewMethods() {
  return {
    _ensureVideoRange() {
      if (this.videoFrom && this.videoTo) return;
      const now = new Date();
      const since = new Date(now.getTime() - 24 * 3600 * 1000);
      if (!this.videoFrom) this.videoFrom = this._toLocalInputValue(since);
      if (!this.videoTo) this.videoTo = this._toLocalInputValue(now);
    },

    async loadVideos() {
      try {
        this.videoLoadingList = true;
        this._syncUrl({ push: false });
        if (!this.mediaIndex || this.mediaIndex.length === 0) {
          this.mediaIndex = await this.api(`/media?limit=500&offset=0`);
        }
        this.videoOffset = 0;
        this.videoHasMore = true;
        this.videoLoadingMore = false;

        const qs = new URLSearchParams();
        qs.set("limit", String(this.videoLimit || 20));
        qs.set("offset", "0");
        if (this.videoStatus) qs.set("status", this.videoStatus);
        if (Array.isArray(this.videoMediaIds) && this.videoMediaIds.length) qs.set("media_id_in", this.videoMediaIds.join(","));
        if (this.videoQuery) qs.set("q", this.videoQuery);
        if (this.videoFrom) qs.set("published_since", new Date(this.videoFrom).toISOString());
        if (this.videoTo) qs.set("published_until", new Date(this.videoTo).toISOString());

        const items = await this.api(`/videos?${qs.toString()}`);
        this.videoList = Array.isArray(items) ? items : [];
        this.videoOffset = this.videoList.length;
        const limit = Number(this.videoLimit || 20);
        this.videoHasMore = this.videoList.length >= limit;
        if (this.videoHasMore) this._setupVideoIo();
        else this._teardownVideoIo();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      } finally {
        this.videoLoadingList = false;
      }
    },

    _teardownVideoIo() {
      try {
        if (this.videoIo) this.videoIo.disconnect();
      } catch {
        // ignore
      }
      this.videoIo = null;
    },

    _setupVideoIo() {
      if (this.activeView !== "videos") return;
      this.$nextTick(() => {
        const el = this.$refs && this.$refs.videoInfiniteSentinel;
        if (!el) return;
        this._teardownVideoIo();
        const io = new IntersectionObserver(
          (entries) => {
            if (!entries || !entries.some((entry) => entry.isIntersecting)) return;
            this.loadMoreVideos();
          },
          { root: null, rootMargin: "800px 0px", threshold: 0 }
        );
        this.videoIo = io;
        io.observe(el);
      });
    },

    async loadMoreVideos() {
      if (this.activeView !== "videos" || this.videoLoadingList || this.videoLoadingMore || !this.videoHasMore) return;

      try {
        this.videoLoadingMore = true;
        const qs = new URLSearchParams();
        qs.set("limit", String(this.videoLimit || 20));
        qs.set("offset", String(this.videoOffset || 0));
        if (this.videoStatus) qs.set("status", this.videoStatus);
        if (Array.isArray(this.videoMediaIds) && this.videoMediaIds.length) qs.set("media_id_in", this.videoMediaIds.join(","));
        if (this.videoQuery) qs.set("q", this.videoQuery);
        if (this.videoFrom) qs.set("published_since", new Date(this.videoFrom).toISOString());
        if (this.videoTo) qs.set("published_until", new Date(this.videoTo).toISOString());

        const items = await this.api(`/videos?${qs.toString()}`);
        const nextItems = Array.isArray(items) ? items : [];
        const current = Array.isArray(this.videoList) ? this.videoList : [];
        const seen = new Set(current.map((video) => String(video && video.id)));
        const fresh = nextItems.filter((video) => video && video.id && !seen.has(String(video.id)));
        this.videoList = current.concat(fresh);
        this.videoOffset = (this.videoOffset || 0) + nextItems.length;

        const limit = Number(this.videoLimit || 20);
        this.videoHasMore = nextItems.length >= limit;
        if (!this.videoHasMore) this._teardownVideoIo();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      } finally {
        this.videoLoadingMore = false;
      }
    },
  };
}
