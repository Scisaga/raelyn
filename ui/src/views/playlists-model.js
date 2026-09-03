import { todayIsoLocal } from "../shared/playlist-periods.js";

export function createPlaylistsViewMethods() {
  return {
    async loadPlaylists() {
      try {
        this.playlistList = await this.api(`/playlists?limit=100&offset=0`);
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    playlistListFiltered() {
      const query = String(this.playlistListQuery || "").trim().toLowerCase();
      const list = Array.isArray(this.playlistList) ? this.playlistList : [];
      if (!query) return list;
      return list.filter((playlist) => {
        const name = String((playlist && playlist.name) || "").toLowerCase();
        const desc = String((playlist && playlist.description) || "").toLowerCase();
        const id = String((playlist && playlist.id) || "").toLowerCase();
        return name.includes(query) || desc.includes(query) || id.includes(query);
      });
    },

    async _uploadPlaylistImage(playlistId, kind, file) {
      const pid = String(playlistId || "").trim();
      if (!pid) throw new Error("missing playlist id");
      const uploadKind = String(kind || "").trim();
      if (uploadKind !== "avatar" && uploadKind !== "background") throw new Error("invalid upload kind");
      if (!file) throw new Error("missing file");

      const formData = new FormData();
      formData.set("file", file, file.name || "image");
      const resp = await this.fetchWithApiAuth(`/api/playlists/${encodeURIComponent(pid)}/${encodeURIComponent(uploadKind)}`, {
        method: "POST",
        body: formData,
      });
      if (resp.status === 401) this.handleApiUnauthorized({});
      if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
      return resp.json();
    },

    openPlaylistPage(playlistId, dateStr) {
      const pid = String(playlistId || "").trim();
      if (!pid) return;
      const previousPid = String(this.playlistPageId || this.selectedPlaylistId || "").trim();
      this.modals.addMedia = false;
      this.modals.createPlaylist = false;
      this.modals.mediaImport = false;
      if (typeof this._stopDocumentMediaPlayback === "function") {
        this._stopDocumentMediaPlayback({ clearSources: true });
      }
      if (typeof this.leaveVideoPage === "function") this.leaveVideoPage();
      if (previousPid && previousPid !== pid) {
        if (typeof this.playlistEventMapStopPolling === "function") this.playlistEventMapStopPolling();
        if (typeof this.playlistEventMapReset === "function") this.playlistEventMapReset();
      }
      this.playlistNameEditing = false;
      this.playlistNameDraft = "";
      this.playlistNameSaving = false;
      this.playlistDescEditing = false;
      this.playlistDescDraft = "";
      this.playlistPageId = pid;
      this.selectedPlaylistId = pid;
      this.playlistSubview = "main";
      this.playlistSelectedDate = String(dateStr || "").trim() || todayIsoLocal();
      this.playlistCalendarUpdateCount();
      this.switchView("playlist");
    },

    openCreatePlaylist({ asDomain = false } = {}) {
      this.modals.addMedia = false;
      this.modals.mediaImport = false;
      if (typeof this.leaveVideoPage === "function") this.leaveVideoPage();
      this.createPlaylistContext = asDomain ? "domain" : "playlist";
      this.createPlaylistSubmitting = false;
      this.createPlaylistName = "";
      this.createPlaylistDesc = "";
      this.createPlaylistMediaIds = [];
      this.createPlaylistMediaTagQuery = "";
      this.createPlaylistMediaTagOpen = false;
      this.createPlaylistAvatarFile = null;
      this.createPlaylistBackgroundFile = null;
      if (!this.mediaIndex || this.mediaIndex.length === 0) {
        const loadOptions =
          typeof this.loadMediaIndex === "function"
            ? this.loadMediaIndex({ lightweight: true })
            : this.api(`/media/options?limit=500&offset=0`);
        loadOptions
          .then((items) => {
            this.mediaIndex = Array.isArray(items) ? items : [];
          })
          .catch(() => {});
      }
      this.modals.createPlaylist = true;
      this.$nextTick?.(() => this.$refs?.createPlaylistNameInput?.focus());
    },

    openCreateDomain() {
      this.openCreatePlaylist({ asDomain: true });
    },

    async submitCreatePlaylist() {
      if (this.createPlaylistSubmitting) return;
      const createdAsDomain = this.createPlaylistContext === "domain";
      let createdId = "";
      const name = String(this.createPlaylistName || "").trim();
      if (!name) {
        this.globalStatus = createdAsDomain ? "请填写观测域名称" : "请填写播放列表名称";
        return;
      }
      this.createPlaylistSubmitting = true;
      try {
        const created = await this.api(`/playlists`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            name,
            description: String(this.createPlaylistDesc || "").trim() || null,
            media_ids: Array.isArray(this.createPlaylistMediaIds) ? this.createPlaylistMediaIds : [],
          }),
        });

        const pid = created && created.id ? String(created.id) : "";
        createdId = pid;
        const maxBytes = 2 * 1024 * 1024;
        if (pid && this.createPlaylistAvatarFile) {
          if (this.createPlaylistAvatarFile.size > maxBytes) throw new Error("头像超过 2MB");
          await this._uploadPlaylistImage(pid, "avatar", this.createPlaylistAvatarFile);
        }
        if (pid && this.createPlaylistBackgroundFile) {
          if (this.createPlaylistBackgroundFile.size > maxBytes) throw new Error("背景超过 2MB");
          await this._uploadPlaylistImage(pid, "background", this.createPlaylistBackgroundFile);
        }

        this.modals.createPlaylist = false;
        if (createdAsDomain) {
          await this.loadDomains({ force: true });
          const selected = pid ? await this.selectDomain(pid) : false;
          if (!selected) {
            const message = this.domainSwitcherError || "观测域已创建，但暂时无法切换到新观测域";
            this.globalStatus = `error: ${message}`;
            this.toastError?.(message);
            this.switchView?.("domains");
          } else {
            this.toastSuccess?.("观测域已创建并切换");
          }
        } else {
          await this.loadPlaylists();
        }
        this.createPlaylistContext = "playlist";
      } catch (e) {
        const message = e?.message || String(e);
        if (!createdId) {
          this.globalStatus = `error: ${message}`;
          return;
        }
        this.modals.createPlaylist = false;
        this.createPlaylistContext = "playlist";
        const objectLabel = createdAsDomain ? "观测域" : "播放列表";
        const visibleMessage = `${objectLabel}已创建，但后续处理失败：${message}`;
        this.globalStatus = `error: ${visibleMessage}`;
        this.toastError?.(visibleMessage);
        if (createdAsDomain) this.switchView?.("domains");
        else await this.loadPlaylists().catch(() => null);
      } finally {
        this.createPlaylistSubmitting = false;
      }
    },

    async deletePlaylist(id) {
      try {
        await this.api(`/playlists/${id}`, { method: "DELETE" });
        if (this.selectedPlaylistId === id) this.selectedPlaylistId = null;
        await this.loadPlaylists();
      } catch (e) {
        this.globalStatus = `error: ${e.message}`;
      }
    },

    selectPlaylist(id) {
      this.selectedPlaylistId = id;
      this._syncUrl({ push: false });
      this.globalStatus = `已选择播放列表：${id}`;
    },
  };
}
