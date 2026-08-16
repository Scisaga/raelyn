import { assetDirectModeEnabled } from "../shared/asset-delivery.js";

export function createMediaViewMethods() {
  return {
    async loadMedia() {
      try {
        this.mediaLoadingList = true;
        this._syncUrl({ push: false });
        this.mediaOffset = 0;
        this.mediaHasMore = true;
        this.mediaLoadingMore = false;

        const params = this._mediaListParams({ offset: 0 });
        const items = await this.api(`/media?${params.toString()}`);
        this.mediaList = Array.isArray(items) ? items : [];
        this.mediaOffset = this.mediaList.length;
        const limit = Number(this.mediaLimit || 50);
        this.mediaHasMore = this.mediaList.length >= limit;
        this._syncMediaDeleteTrackingFromList(this.mediaList);
        if (this.mediaHasMore) this._setupMediaIo();
        else this._teardownMediaIo();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      } finally {
        this.mediaLoadingList = false;
      }
    },

    _mediaListParams({ offset = 0 } = {}) {
      const params = new URLSearchParams({
        limit: String(this.mediaLimit || 50),
        offset: String(offset || 0),
      });
      if (this.mediaQuery) params.set("q", this.mediaQuery);
      if (!assetDirectModeEnabled(this.assetDelivery)) params.set("presign", "false");
      return params;
    },

    _teardownMediaIo() {
      try {
        if (this.mediaIo) this.mediaIo.disconnect();
      } catch {
        // 忽略 observer 清理失败
      }
      this.mediaIo = null;
    },

    _setupMediaIo() {
      if (this.activeView !== "media" && !(this.activeView === "library" && this.libraryTab === "sources")) return;
      this.$nextTick(() => {
        const el = this.$refs && this.$refs.mediaInfiniteSentinel;
        if (!el) return;
        this._teardownMediaIo();
        const io = new IntersectionObserver(
          (entries) => {
            if (!entries || !entries.some((entry) => entry.isIntersecting)) return;
            this.loadMoreMedia();
          },
          { root: null, rootMargin: "800px 0px", threshold: 0 }
        );
        this.mediaIo = io;
        io.observe(el);
      });
    },

    async loadMoreMedia() {
      if ((this.activeView !== "media" && !(this.activeView === "library" && this.libraryTab === "sources")) || this.mediaLoadingList || this.mediaLoadingMore || !this.mediaHasMore) return;

      try {
        this.mediaLoadingMore = true;
        const params = this._mediaListParams({ offset: this.mediaOffset || 0 });
        const items = await this.api(`/media?${params.toString()}`);
        const nextItems = Array.isArray(items) ? items : [];
        const current = Array.isArray(this.mediaList) ? this.mediaList : [];
        const seen = new Set(current.map((media) => String(media && media.id)));
        const fresh = nextItems.filter((media) => media && media.id && !seen.has(String(media.id)));
        this.mediaList = current.concat(fresh);
        this.mediaOffset = (this.mediaOffset || 0) + nextItems.length;

        const limit = Number(this.mediaLimit || 50);
        this.mediaHasMore = nextItems.length >= limit;
        this._syncMediaDeleteTrackingFromList(fresh);
        if (!this.mediaHasMore) this._teardownMediaIo();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      } finally {
        this.mediaLoadingMore = false;
      }
    },

    async refreshCurrentMediaPage() {
      try {
        const currentCount = Array.isArray(this.mediaList) ? this.mediaList.length : 0;
        const limit = Math.max(Number(this.mediaLimit || 50), currentCount || Number(this.mediaLimit || 50));
        const params = new URLSearchParams({
          limit: String(limit),
          offset: "0",
        });
        if (this.mediaQuery) params.set("q", this.mediaQuery);
        if (!assetDirectModeEnabled(this.assetDelivery)) params.set("presign", "false");
        const items = await this.api(`/media?${params.toString()}`);
        this.mediaList = Array.isArray(items) ? items : [];
        this.mediaOffset = this.mediaList.length;
        this.mediaHasMore = this.mediaList.length >= limit;
        this._syncMediaDeleteTrackingFromList(this.mediaList);
        if (this.mediaHasMore) this._setupMediaIo();
        else this._teardownMediaIo();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    _mediaFindById(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return null;
      const list = Array.isArray(this.mediaList) ? this.mediaList : [];
      return list.find((item) => item && String(item.id) === id) || null;
    },

    _mediaIndexFindById(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return null;
      const list = Array.isArray(this.mediaIndex) ? this.mediaIndex : [];
      return list.find((item) => item && String(item.id) === id) || null;
    },

    mediaDisabledLabel(media) {
      const reason = String((media && media.disabled_reason) || "").trim();
      if (reason === "source_unavailable") return "来源不可用";
      return reason ? "已停用" : "";
    },

    mediaDisabledTitle(media) {
      const message = String((media && media.disabled_message) || "").trim();
      if (message) return message;
      const reason = String((media && media.disabled_reason) || "").trim();
      if (reason === "source_unavailable") return "媒体源返回 404，系统已自动停用监控";
      return reason ? `自动停用：${reason}` : "";
    },

    _markMediaDeleting(mediaId, jobId) {
      const id = String(mediaId || "").trim();
      const job = String(jobId || "").trim();
      if (!id || !job) return;
      const media = this._mediaFindById(id);
      if (media) {
        media.deleting = true;
        media.deletion_job_id = job;
        media.monitor_enabled = false;
      }
      const mediaIndexItem = this._mediaIndexFindById(id);
      if (mediaIndexItem) {
        mediaIndexItem.deleting = true;
        mediaIndexItem.deletion_job_id = job;
        mediaIndexItem.monitor_enabled = false;
      }
    },

    _removeMediaLocally(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      this.mediaList = (Array.isArray(this.mediaList) ? this.mediaList : []).filter((item) => !(item && String(item.id) === id));
      this.mediaIndex = (Array.isArray(this.mediaIndex) ? this.mediaIndex : []).filter((item) => !(item && String(item.id) === id));
    },

    _trackMediaDelete(mediaId, jobId) {
      const id = String(mediaId || "").trim();
      const job = String(jobId || "").trim();
      if (!id || !job) return;
      if (!this.mediaDeleteTracking || typeof this.mediaDeleteTracking !== "object") this.mediaDeleteTracking = {};
      this.mediaDeleteTracking[id] = { jobId: job };
      this._ensureMediaDeletePoller();
    },

    _untrackMediaDelete(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id || !this.mediaDeleteTracking || typeof this.mediaDeleteTracking !== "object") return;
      delete this.mediaDeleteTracking[id];
      if (Object.keys(this.mediaDeleteTracking).length === 0) {
        this._stopMediaDeletePoller();
      }
    },

    _syncMediaDeleteTrackingFromList(list) {
      const items = Array.isArray(list) ? list : [];
      for (const item of items) {
        if (!item || item.deleting !== true || !item.deletion_job_id) continue;
        this._trackMediaDelete(item.id, item.deletion_job_id);
      }
    },

    _ensureMediaDeletePoller() {
      if (this._mediaDeletePollId) return;
      this._mediaDeletePollId = setInterval(() => {
        this._pollMediaDeleteJobs();
      }, 1500);
      this._pollMediaDeleteJobs();
    },

    _stopMediaDeletePoller() {
      try {
        if (this._mediaDeletePollId) clearInterval(this._mediaDeletePollId);
      } catch {
        // ignore
      }
      this._mediaDeletePollId = null;
    },

    async _refreshMediaAndStatsAfterDelete() {
      await this.refreshCurrentMediaPage();
      await this.loadMediaIndex();
      await Promise.all([this.loadStats(), this.loadJobs()]);
    },

    async _handleMediaDeleteJobFinished(mediaId, job) {
      const status = String((job && job.status) || "").trim().toLowerCase();
      const id = String(mediaId || "").trim();
      if (!id) return;
      if (status === "succeeded") {
        this._removeMediaLocally(id);
        this._untrackMediaDelete(id);
        this.globalStatus = "已删除媒体";
        this.toastSuccess("已删除媒体", { action: this.toastJobsAction() });
        await this._refreshMediaAndStatsAfterDelete();
        return;
      }

      if (!["failed", "canceled"].includes(status)) return;
      const message = String((job && job.error_message) || "").trim() || (status === "canceled" ? "删除任务已取消" : "删除任务失败");
      this._untrackMediaDelete(id);
      this.globalStatus = `error: ${message}`;
      if (status === "canceled") this.toastError(message, { action: this.toastJobsAction() });
      else this.toastError(`删除失败：${message}`, { action: this.toastJobsAction() });
      await this._refreshMediaAndStatsAfterDelete();
    },

    async _pollMediaDeleteJobs() {
      if (this._mediaDeletePolling) return;
      const entries = Object.entries(this.mediaDeleteTracking || {});
      if (!entries.length) {
        this._stopMediaDeletePoller();
        return;
      }
      this._mediaDeletePolling = true;
      try {
        for (const [mediaId, item] of entries) {
          const jobId = item && item.jobId ? String(item.jobId) : "";
          if (!jobId) {
            this._untrackMediaDelete(mediaId);
            continue;
          }
          try {
            const job = await this.api(`/jobs/${encodeURIComponent(jobId)}`);
            await this._handleMediaDeleteJobFinished(mediaId, job);
          } catch (e) {
            const msg = e && e.message ? String(e.message) : String(e);
            if (msg.startsWith("404:")) {
              this._untrackMediaDelete(mediaId);
              await this._refreshMediaAndStatsAfterDelete();
            }
          }
        }
      } finally {
        this._mediaDeletePolling = false;
      }
    },

    _mediaExportFilename() {
      try {
        const date = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        return `media-export-${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(
          date.getMinutes()
        )}${pad(date.getSeconds())}.txt`;
      } catch {
        return "media-export.txt";
      }
    },

    async exportMediaAll() {
      try {
        const text = await this.api(`/media/export`);
        const blob = new Blob([text || ""], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = this._mediaExportFilename();
        anchor.rel = "noreferrer";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1200);
        this.globalStatus = "已导出媒体";
        this.toastSuccess("已导出媒体");
      } catch (e) {
        const message = e && e.message ? e.message : String(e);
        this.globalStatus = `error: ${message}`;
        this.toastError(`导出失败：${message}`);
      }
    },

    closeMediaImport() {
      if (this.modals) this.modals.mediaImport = false;
    },

    triggerMediaImportFile() {
      try {
        const fileInput = this.$refs && this.$refs.mediaImportFile ? this.$refs.mediaImportFile : null;
        if (!fileInput) return;
        fileInput.value = "";
        fileInput.click();
      } catch {
        // ignore
      }
    },

    openMediaImport() {
      if (this.modals) {
        this.modals.addMedia = false;
        this.modals.createPlaylist = false;
        this.modals.mediaImport = true;
      }
      if (typeof this.leaveVideoPage === "function") this.leaveVideoPage();
      this.mediaImportSubmitting = false;
      this.mediaImportError = "";
      this.mediaImportResult = null;
      this.$nextTick(() => this.triggerMediaImportFile());
    },

    async handleMediaImportFile(ev) {
      const file = ev && ev.target && ev.target.files ? ev.target.files[0] : null;
      if (!file) return;
      const maxBytes = 2 * 1024 * 1024;
      if (file.size > maxBytes) {
        this.mediaImportError = "文件超过 2MB";
        this.toastError("导入失败：文件超过 2MB");
        return;
      }
      try {
        const text = await file.text();
        await this.submitMediaImportText(text);
      } catch (e) {
        const message = e && e.message ? e.message : String(e);
        this.mediaImportError = message;
        this.toastError(`导入失败：${message}`);
      }
    },

    async submitMediaImportText(text) {
      if (this.mediaImportSubmitting) return;
      try {
        this.mediaImportSubmitting = true;
        this.mediaImportError = "";
        this.mediaImportResult = null;
        const result = await this.api(`/media/import`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ text: String(text || "") }),
        });
        if (!result || result.ok !== true) throw new Error("导入失败：服务端返回异常");
        this.mediaImportResult = result;
        const created = Array.isArray(result.created) ? result.created.length : 0;
        const existing = Array.isArray(result.existing) ? result.existing.length : 0;
        const invalid = Array.isArray(result.invalid) ? result.invalid.length : 0;
        const message = `导入完成：新增 ${created} / 已存在 ${existing} / 无效 ${invalid}；新媒体默认未启用监控`;
        this.globalStatus = message;
        this.toastSuccess(message);
        await this.loadMedia();
        await this.loadMediaIndex({ lightweight: true });
        await this.loadStats();
      } catch (e) {
        const message = e && e.message ? e.message : String(e);
        this.mediaImportError = message;
        this.toastError(`导入失败：${message}`);
        this.globalStatus = `error: ${message}`;
      } finally {
        this.mediaImportSubmitting = false;
      }
    },

    openAddMedia() {
      this.modals.createPlaylist = false;
      this.modals.mediaImport = false;
      if (typeof this.leaveVideoPage === "function") this.leaveVideoPage();
      this.addMediaUrl = "";
      this.addMediaError = "";
      this.addMediaSubmitting = false;
      this.modals.addMedia = true;
    },

    async submitAddMedia() {
      if (this.addMediaSubmitting) return;
      const url = String(this.addMediaUrl || "").trim();
      if (!url) {
        this.addMediaError = "请填写媒体 URL";
        return;
      }
      try {
        this.addMediaSubmitting = true;
        this.addMediaError = "";
        const attachToDomain = this.activeView === "library" && this.libraryScope === "domain" && this.selectedPlaylistId;
        await this.api(attachToDomain ? `/domains/${encodeURIComponent(this.selectedPlaylistId)}/sources` : `/media`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ url }),
        });
        this.modals.addMedia = false;
        if (!attachToDomain && this.activeView !== "media") this.switchView("media");
        if (attachToDomain) await this.loadLibrary();
        else await this.loadMedia();
        await this.loadMediaIndex({ lightweight: true });
        this.globalStatus = attachToDomain ? "已新建或复用信源并加入当前观测域" : "已添加媒体，默认未启用监控";
        this.toastSuccess(this.globalStatus);
      } catch (e) {
        const message = e && e.message ? e.message : String(e);
        this.addMediaError = message;
        this.globalStatus = `error: ${message}`;
      } finally {
        this.addMediaSubmitting = false;
      }
    },

    async setMediaMonitor(mediaId, enabled) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      const next = !!enabled;

      const mediaList = Array.isArray(this.mediaList) ? this.mediaList : [];
      const media = mediaList.find((item) => item && String(item.id) === id);
      if (media && media.deleting) return;
      const prev = media ? media.monitor_enabled !== false : true;
      if (media) media.monitor_enabled = next;
      const mediaIndex = Array.isArray(this.mediaIndex) ? this.mediaIndex : [];
      const mediaIndexItem = mediaIndex.find((item) => item && String(item.id) === id);
      if (mediaIndexItem) mediaIndexItem.monitor_enabled = next;

      try {
        await this.api(`/media/${id}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ monitor_enabled: next }),
        });
        this.globalStatus = next ? "已启用监控" : "已关闭监控，并删除待处理下载任务";
        if (!next && this.activeView === "settings" && this.settingsTab === "cleanup") {
          await this.loadStaleVideosCleanup({ force: true });
        }
      } catch (e) {
        if (media) media.monitor_enabled = prev;
        if (mediaIndexItem) mediaIndexItem.monitor_enabled = prev;
        this.globalStatus = `error: ${e.message}`;
      }
    },

    async _syncMedia(mediaId, scope) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      const media = this._mediaFindById(id);
      if (media && media.deleting) return;
      const query = scope ? `?scope=${encodeURIComponent(scope)}` : "";
      await this.api(`/media/${id}/sync${query}`, { method: "POST" });
      const message = scope === "all" ? "已投递全量同步任务" : "已投递近期同步任务";
      this.globalStatus = message;
      this.toastSuccess(message, { action: this.toastJobsAction() });
      await this.loadJobs();
    },

    async syncAllMediaHistory() {
      if (this.syncAllMediaSubmitting) return;
      const confirmed = confirm(
        "确认同步全部已启用监控媒体的历史视频？\n\n系统只会为已启用监控的媒体投递全量同步任务；已采集的视频会按 provider_video_id 自动跳过。"
      );
      if (!confirmed) return;

      try {
        this.syncAllMediaSubmitting = true;
        const result = await this.api(`/media/sync?scope=all`, { method: "POST" });
        const count = Number(result && result.count) || 0;
        const message = count > 0 ? `已投递 ${count} 个已启用监控媒体的历史同步任务` : "没有已启用监控的媒体可同步";
        this.globalStatus = message;
        this.toastSuccess(message, { action: this.toastJobsAction() });
        await this.loadJobs();
      } catch (e) {
        const message = e && e.message ? e.message : String(e);
        this.toastError(`批量同步提交失败：${message}`, { action: this.toastJobsAction() });
        this.globalStatus = `error: ${message}`;
      } finally {
        this.syncAllMediaSubmitting = false;
      }
    },

    async syncMediaRecent(mediaId) {
      try {
        await this._syncMedia(mediaId, "recent");
      } catch (e) {
        const message = e && e.message ? e.message : String(e);
        this.toastError(`同步任务提交失败：${message}`, { action: this.toastJobsAction() });
        this.globalStatus = `error: ${message}`;
      }
    },

    async syncMediaAll(mediaId) {
      try {
        await this._syncMedia(mediaId, "all");
      } catch (e) {
        const message = e && e.message ? e.message : String(e);
        this.toastError(`同步任务提交失败：${message}`, { action: this.toastJobsAction() });
        this.globalStatus = `error: ${message}`;
      }
    },

    async syncMedia(mediaId) {
      return await this.syncMediaRecent(mediaId);
    },

    async deleteMedia(mediaId) {
      const id = String(mediaId || "").trim();
      if (!id) return;
      const media = this._mediaFindById(id);
      if (media && media.deleting) return;
      try {
        if (!confirm("确认删除该媒体？该操作会删除媒体及其关联数据（视频/任务/简报等可能会受影响）。")) return;
        this.globalStatus = "正在提交删除媒体任务…";
        const result = await this.api(`/media/${encodeURIComponent(id)}`, { method: "DELETE" });
        const jobId = result && result.job_id ? String(result.job_id) : "";
        if (!jobId) throw new Error("删除任务提交失败：缺少 job_id");
        this._markMediaDeleting(id, jobId);
        this._trackMediaDelete(id, jobId);
        this.globalStatus = "已提交删除媒体任务";
        this.toastSuccess("已提交删除媒体任务", { action: this.toastJobsAction() });
        await this.loadJobs();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
        this.toastError(`删除失败：${e.message}`);
      }
    },
  };
}
