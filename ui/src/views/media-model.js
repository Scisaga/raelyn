export function createMediaViewMethods() {
  return {
    async loadMedia() {
      try {
        this._syncUrl({ push: false });
        const query = this.mediaQuery ? `&q=${encodeURIComponent(this.mediaQuery)}` : "";
        this.mediaList = await this.api(`/media?limit=50&offset=0${query}`);
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
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
      this.closeVideoPlayer();
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
        this.mediaIndex = await this.api(`/media?limit=500&offset=0`);
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
      this.closeVideoPlayer();
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
        await this.api(`/media`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ url }),
        });
        this.modals.addMedia = false;
        if (this.activeView !== "media") this.switchView("media");
        await this.loadMedia();
        this.mediaIndex = await this.api(`/media?limit=500&offset=0`);
        this.globalStatus = "已添加媒体，默认未启用监控";
        this.toastSuccess("已添加媒体，默认未启用监控");
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
      try {
        if (!confirm("确认删除该媒体？该操作会删除媒体及其关联数据（视频/任务/简报等可能会受影响）。")) return;
        this.globalStatus = "正在删除媒体…";
        await this.api(`/media/${encodeURIComponent(id)}`, { method: "DELETE" });
        await this.loadMedia();
        this.mediaIndex = await this.api(`/media?limit=500&offset=0`);
        this.globalStatus = "已删除媒体";
        this.toastSuccess("已删除媒体");
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
        this.toastError(`删除失败：${e.message}`);
      }
    },
  };
}
