const FIELD_MODES = new Set(["now", "replay", "story", "verify"]);
const LIBRARY_TABS = new Set(["sources", "records"]);
const FIELD_CAMERA_FRAMING_VERSION = 4;

function emptyObservationFeed() {
  return {
    newly_occurred: [],
    newly_mapped: [],
    story_updates: [],
    needs_review: [],
    definitions: {},
  };
}

function todayLocalIso() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function queryValue(name) {
  try {
    return new URLSearchParams(window.location.search || "").get(name) || "";
  } catch {
    return "";
  }
}

export function createV2ViewMethods() {
  return {
    async loadDomains({ force = false } = {}) {
      if (this.domainsLoading) return this.domains;
      if (!force && Array.isArray(this.domains) && this.domains.length) return this.domains;
      this.domainsLoading = true;
      try {
        const payload = await this.api("/domains");
        this.domains = Array.isArray(payload?.items) ? payload.items : [];
        return this.domains;
      } finally {
        this.domainsLoading = false;
      }
    },

    currentDomain() {
      const id = String(this.selectedPlaylistId || this.playlistPageId || "");
      return (this.domains || []).find((domain) => String(domain.id) === id) || null;
    },

    currentDomainLabel() {
      const domain = this.currentDomain();
      return domain?.name || this.playlistDetail?.name || "选择观测域";
    },

    async ensureCurrentDomain() {
      await this.loadDomains();
      let domainId = String(queryValue("domain_id") || this.selectedPlaylistId || "").trim();
      if (!domainId) {
        try {
          domainId = String(localStorage.getItem(this.v2LastDomainKey) || "").trim();
        } catch {
          domainId = "";
        }
      }
      if (!domainId || !(this.domains || []).some((domain) => String(domain.id) === domainId)) {
        domainId = String(this.domains?.[0]?.id || "");
      }
      this.selectedPlaylistId = domainId || null;
      this.playlistPageId = domainId || null;
      if (domainId) {
        try {
          localStorage.setItem(this.v2LastDomainKey, domainId);
        } catch {
          // 本地存储不可用时仍允许本次会话继续。
        }
      }
      return domainId;
    },

    async selectDomain(domainId) {
      const nextId = String(domainId || "").trim();
      if (!nextId || nextId === String(this.selectedPlaylistId || "")) return;
      if (this.activeView === "field") await this.saveFieldCursor({ immediate: true });
      if (this.activeView === "field" && typeof this.leaveField === "function") this.leaveField();
      if (this.activeView === "playlist" && typeof this.leavePlaybackPage === "function") this.leavePlaybackPage();
      this.selectedPlaylistId = nextId;
      this.playlistPageId = nextId;
      try {
        localStorage.setItem(this.v2LastDomainKey, nextId);
      } catch {
        // ignore
      }
      this.storySelectedId = "";
      this.briefV2SelectedId = "";
      this._syncUrl({ push: false });
      await this.refreshActive();
    },

    async loadDomainDirectory() {
      await this.loadDomains({ force: true });
      this.pageTitle = "观测域";
    },

    async openDomain(domainId) {
      const nextId = String(domainId || "").trim();
      if (!nextId) return;
      if (nextId !== String(this.selectedPlaylistId || "")) await this.selectDomain(nextId);
      this.switchView("field");
    },

    domainPreviewColor(code) {
      const colors = ["#60a5fa", "#22d3ee", "#f59e0b", "#a78bfa", "#34d399", "#f472b6", "#fb7185", "#94a3b8"];
      return colors[Math.abs(Number(code || 0)) % colors.length];
    },

    domainFreshnessLabel(domain) {
      const raw = domain?.snapshot?.observed_at;
      if (!raw) return "尚未形成星域";
      try {
        return `认知更新于 ${new Date(raw).toLocaleString()}`;
      } catch {
        return String(raw);
      }
    },

    async loadField() {
      const domainId = await this.ensureCurrentDomain();
      if (!domainId) {
        this.playlistDetail = null;
        this.pageTitle = "星域";
        return;
      }
      this.playlistSubview = "analysis";
      this._fieldHydrating = true;
      this.fieldObservationRailOpen = false;
      this.fieldObservationRailTab = "newly_occurred";
      this.domainObservationFeed = emptyObservationFeed();
      this.fieldObservationFeedLoading = true;
      this.fieldObservationFeedLoaded = false;
      this.fieldObservationFeedError = "";
      if (this._playlistEventMapPlaylistId && String(this._playlistEventMapPlaylistId) !== String(domainId)) {
        this.playlistEventMapReset();
      }
      this._playlistEventMapPlaylistId = String(domainId);
      try {
        const [detail, cursor] = await Promise.all([
          this.api(`/playlists/${encodeURIComponent(domainId)}/detail`),
          this.api(`/domains/${encodeURIComponent(domainId)}/observation/cursor`),
        ]);
        if (this.activeView !== "field" || String(this.selectedPlaylistId || "") !== String(domainId)) return;
        this.playlistDetail = detail;
        const domain = this.currentDomain();
        this.domainObservation = {
          domain: domain ? { id: domain.id, name: domain.name, description: domain.description } : null,
          snapshot: domain?.snapshot || null,
          cursor,
          unobserved_change_count: Number(domain?.unobserved_change_count || 0),
        };
        if (FIELD_MODES.has(String(cursor?.view_mode || ""))) this.fieldMode = cursor.view_mode;
        if (cursor?.event_time_start) this.playlistEventMapWindowStart = String(cursor.event_time_start).slice(0, 10);
        if (cursor?.event_time_end) this.playlistEventMapWindowEnd = String(cursor.event_time_end).slice(0, 10);
        if (Object.prototype.hasOwnProperty.call(cursor?.filter_state || {}, "event_type")) this.playlistEventMapTypeFilter = cursor.filter_state.event_type || "";
        if (["normal", "full"].includes(cursor?.filter_state?.timeline_scope)) this.playlistEventMapTimelineScope = cursor.filter_state.timeline_scope;
        await this.playlistEventMapLoadView();
        if (this.activeView !== "field" || String(this.selectedPlaylistId || "") !== String(domainId)) return;
        const controller = this.playlistEventMapController();
        if (this.fieldCameraStateCompatible(cursor?.camera_state)) controller?.restoreCameraState(cursor.camera_state);
        else controller?.fitActiveWindow();

        const entity = this.fieldRequestedEntity || cursor?.filter_state?.entity;
        if (entity?.normalized_key) await this.playlistEventMapApplyEntityFilter(entity, { resume: true, focus: Boolean(this.fieldRequestedEntity) });
        this.pageTitle = domain?.name || this.playlistDetail?.name || "星域";
        const canonicalId = queryValue("canonical_id") || this.fieldRequestedCanonicalId;
        if (canonicalId) await this.fieldOpenCanonical(canonicalId);
        const topicId = queryValue("topic_id") || this.fieldRequestedTopicId;
        if (topicId) {
          const topic = (this.playlistEventMapManifest?.topics || []).find(
            (item) => String(item.topic_id || "") === String(topicId)
          );
          if (topic) this.playlistEventMapSelectTopic(topic, { focus: true });
        }
        const storyId = queryValue("story_id");
        if (storyId) {
          this.fieldMode = "story";
          await this.fieldOpenStory(storyId);
        }
        const evidenceId = queryValue("evidence_id") || this.fieldRequestedEvidenceId;
        if (evidenceId) await this.fieldOpenEvidence(evidenceId);

        try {
          const feed = await this.api(`/domains/${encodeURIComponent(domainId)}/observation/feed?limit=40`);
          if (this.activeView === "field" && String(this.selectedPlaylistId || "") === String(domainId)) {
            this.domainObservationFeed = feed || emptyObservationFeed();
            this.fieldObservationFeedLoaded = true;
            this.fieldSelectAvailableFeedTab();
          }
        } catch (error) {
          this.fieldObservationFeedError = error?.message || String(error);
        }
        if (["story", "verify"].includes(this.fieldMode)) {
          this.fieldObservationRailOpen = true;
          this.fieldSelectAvailableFeedTab({ preferred: this.fieldMode === "story" ? "story_updates" : "needs_review" });
        }
      } finally {
        this._fieldHydrating = false;
        this.fieldObservationFeedLoading = false;
      }
      if (this.activeView === "field" && String(this.selectedPlaylistId || "") === String(domainId)) {
        this.saveFieldCursor({ immediate: true });
      }
    },

    leaveField() {
      if (this._fieldCursorSaveTimer) {
        window.clearTimeout(this._fieldCursorSaveTimer);
        this._fieldCursorSaveTimer = null;
      }
      this.playlistEventMapStopPolling();
      this.playlistEventMapReset();
      this.fieldEvidenceLoading = false;
      this.fieldLinearLoading = false;
    },

    async fieldSetMode(mode) {
      const next = FIELD_MODES.has(String(mode || "")) ? String(mode) : "now";
      this.fieldMode = next;
      if (next === "replay" && this.playlistEventMapCanPlay()) this.playlistEventMapTogglePlayback();
      if (next !== "replay" && this.playlistEventMapPlaying) this.playlistEventMapStopPlayback();
      this.fieldObservationRailTab = next === "verify" ? "needs_review" : next === "story" ? "story_updates" : this.fieldObservationRailTab;
      if (["story", "verify"].includes(next)) this.fieldObservationRailOpen = true;
      await this.saveFieldCursor({ immediate: true });
    },

    fieldModeLabel(mode) {
      return { now: "现在", replay: "回放", story: "故事", verify: "验证" }[mode] || mode;
    },

    fieldFeedTabs() {
      const feed = this.domainObservationFeed || {};
      return [
        { key: "newly_occurred", label: "新发生", count: (feed.newly_occurred || []).length },
        { key: "newly_mapped", label: "认知变化", count: (feed.newly_mapped || []).length },
        { key: "story_updates", label: "故事更新", count: (feed.story_updates || []).length },
        { key: "needs_review", label: "待验证", count: (feed.needs_review || []).length },
      ];
    },

    fieldSelectAvailableFeedTab({ preferred = "" } = {}) {
      const tabs = this.fieldFeedTabs();
      const preferredTab = tabs.find((tab) => tab.key === preferred && tab.count > 0);
      const currentTab = tabs.find((tab) => tab.key === this.fieldObservationRailTab && tab.count > 0);
      this.fieldObservationRailTab = preferredTab?.key || currentTab?.key || tabs.find((tab) => tab.count > 0)?.key || preferred || "newly_occurred";
    },

    fieldToggleObservationRail() {
      this.fieldObservationRailOpen = !this.fieldObservationRailOpen;
      if (this.fieldObservationRailOpen) this.fieldSelectAvailableFeedTab();
    },

    fieldObservationUnreadLabel() {
      const count = Number(this.domainObservation?.unobserved_change_count || 0);
      return count > 99 ? "99+" : count > 0 ? String(count) : "";
    },

    fieldFeedItems() {
      return this.domainObservationFeed?.[this.fieldObservationRailTab] || [];
    },

    fieldFeedItemTitle(item) {
      return item?.title || item?.after_revision?.title || item?.after_revision?.summary || item?.change_type || "语义对象变化";
    },

    fieldFeedItemKindLabel(item) {
      if (item?.change_type) return this.fieldChangeTypeLabel(item.change_type);
      if (this.fieldObservationRailTab === "newly_occurred") return "事件发生";
      if (this.fieldObservationRailTab === "needs_review") return "待验证";
      return "系统认知变化";
    },

    fieldFeedEmptyLabel() {
      if (this.fieldObservationFeedError) return `变化读取失败：${this.fieldObservationFeedError}`;
      if (!this.fieldObservationFeedLoaded) return "正在读取自上次观察后的变化…";
      return this.fieldObservationRailTab === "needs_review" ? "当前快照没有待验证事件" : "这个口径下暂时没有新变化";
    },

    fieldCameraStateCompatible(state) {
      if (Number(state?.framing_version || 0) !== FIELD_CAMERA_FRAMING_VERSION) return false;
      if (!state || String(state.snapshot_id || "") !== String(this.playlistEventMapSnapshotId || "")) return false;
      if (String(state.window_start || "") !== String(this.playlistEventMapWindowStart || "")) return false;
      if (String(state.window_end || "") !== String(this.playlistEventMapWindowEnd || "")) return false;
      const savedAspect = Number(state.viewport_aspect || 0);
      const currentAspect = Number(this.playlistEventMapController()?.cameraState()?.viewport_aspect || 0);
      return savedAspect > 0 && currentAspect > 0 && Math.max(savedAspect, currentAspect) / Math.min(savedAspect, currentAspect) <= 1.25;
    },

    fieldFeedItemTime(item) {
      const raw = item?.occurred_at || item?.observed_at;
      if (!raw) return "时间未知";
      try {
        return new Date(raw).toLocaleString();
      } catch {
        return String(raw);
      }
    },

    async fieldOpenFeedItem(item) {
      if (!item) return;
      const targetSnapshotId = String(
        item.after_revision ? item.to_snapshot_id || "" : item.from_snapshot_id || ""
      );
      if (targetSnapshotId && targetSnapshotId !== String(this.playlistEventMapSnapshotId || "")) {
        this.fieldRequestedSnapshotId = targetSnapshotId;
        this._syncUrl({ push: false });
        await this.playlistEventMapLoadView({ silent: false, schedulePolling: false });
      }
      if (item.object_type === "canonical") await this.fieldOpenCanonical(item.object_id, item.after_revision?.point_index);
      if (item.object_type === "story") await this.fieldOpenStory(item.object_id);
      this.fieldSelectedChange = item;
    },

    fieldChangeTypeLabel(changeType) {
      return {
        canonical_added: "首次进入星域",
        canonical_updated: "真实事件修订",
        canonical_members_changed: "来源成员变化",
        canonical_evidence_changed: "证据变化",
        canonical_merge: "真实事件合并",
        canonical_split: "真实事件拆分",
        canonical_retired: "真实事件退休",
        story_added: "新故事",
        story_members_changed: "故事成员变化",
        story_relations_changed: "故事关系变化",
        story_evidence_changed: "故事证据变化",
        story_summary_changed: "故事叙述修订",
        story_correction_added: "有证据的纠正",
        layout_rebased: "星域坐标重建",
      }[String(changeType || "")] || String(changeType || "语义对象变化");
    },

    fieldChangeRevisionLabel(revision) {
      if (!revision) return "无";
      return revision.title || revision.summary ||
        `${(revision.member_ids || revision.member_revision_ids || []).length} 个成员 · ${(revision.evidence_revision_ids || []).length} 份证据`;
    },

    async fieldOpenCanonical(canonicalId, pointIndex = null) {
      const id = String(canonicalId || "").trim();
      if (!id || !this.playlistEventMapSnapshotId) return;
      let index = pointIndex === null || pointIndex === undefined || pointIndex === "" ? NaN : Number(pointIndex);
      if (!Number.isInteger(index)) {
        try {
          const history = await this.api(
            `/domains/${encodeURIComponent(this.selectedPlaylistId)}/canonicals/${encodeURIComponent(id)}/history`
          );
          const revision = (history?.revisions || [])
            .slice()
            .reverse()
            .find((item) => String(item.snapshot_id) === String(this.playlistEventMapSnapshotId));
          index = Number(revision?.revision?.point_index);
        } catch {
          index = NaN;
        }
      }
      if (!Number.isInteger(index)) {
        try {
          const result = await this.api(
            `/playlists/${encodeURIComponent(this.selectedPlaylistId)}/events/map/search?snapshot_id=${encodeURIComponent(this.playlistEventMapSnapshotId)}&q=${encodeURIComponent(id)}&limit=10`
          );
          const item = (Array.isArray(result?.items) ? result.items : Array.isArray(result) ? result : []).find(
            (entry) => String(entry.id || entry.canonical_id) === id
          );
          index = Number(item?.point_index);
        } catch {
          index = NaN;
        }
      }
      if (Number.isInteger(index)) await this.playlistEventMapSelectCanonical(index, id);
    },

    async fieldOpenStory(storyIdentityId) {
      const id = String(storyIdentityId || "").trim();
      if (!id) return;
      this.fieldRequestedEvidenceId = "";
      this.fieldEvidenceDetail = null;
      this.storySelectedId = id;
      await this.loadStoryDetail(id, { markRead: true });
      const trajectorySnapshotId = String(this.storyDetail?.current_trajectory?.snapshot_id || "");
      const revision = (this.storyDetail?.revisions || []).find(
        (item) => String(item.snapshot_id || "") === trajectorySnapshotId
      ) || this.storyDetail?.revisions?.at(-1);
      const firstCanonicalId = revision?.member_ids?.[0];
      if (firstCanonicalId) {
        await this.fieldOpenCanonical(firstCanonicalId);
        this.playlistEventMapSetDetailTab("story");
      }
      const trajectory = this.storyDetail?.current_trajectory;
      if (String(trajectory?.snapshot_id || "") === String(this.playlistEventMapSnapshotId || "")) {
        this.playlistEventMapController()?.setStoryPath(trajectory);
        this.playlistEventMapFitIndices(new Set((trajectory.nodes || []).map((node) => Number(node.point_index))));
      }
    },

    async fieldOpenEvidence(revisionId) {
      const id = String(revisionId || "").trim();
      const domainId = String(this.selectedPlaylistId || "");
      if (!id || !domainId) return;
      this.fieldMode = "verify";
      this.fieldRequestedEvidenceId = id;
      this.fieldRequestedCanonicalId = "";
      this.fieldRequestedTopicId = "";
      this.fieldSelectedChange = null;
      this.playlistEventMapSelectedKind = "evidence";
      this.playlistEventMapSelectedId = id;
      this.playlistEventMapSelectedIndex = null;
      this.playlistEventMapTopicFocus = null;
      this.playlistEventMapController()?.clearTopicFocus();
      this.playlistEventMapController()?.setSelection(null, null);
      this.playlistEventMapUpdateLayers();
      this.fieldEvidenceLoading = true;
      this.fieldEvidenceDetail = null;
      if (this.activeView === "field") this._syncUrl({ push: false });
      try {
        this.fieldEvidenceDetail = await this.api(
          `/domains/${encodeURIComponent(domainId)}/evidence/${encodeURIComponent(id)}`
        );
      } finally {
        this.fieldEvidenceLoading = false;
      }
    },

    openEvidenceInField(revisionId) {
      const id = String(revisionId || "").trim();
      if (!id) return;
      this.fieldMode = "verify";
      this.fieldRequestedEvidenceId = id;
      this.playlistEventMapSelectedKind = "evidence";
      this.playlistEventMapSelectedId = id;
      this.switchView("field");
    },

    fieldCloseEvidence() {
      this.fieldRequestedEvidenceId = "";
      this.fieldEvidenceDetail = null;
      this.fieldEvidenceLoading = false;
      this.playlistEventMapSelectedKind = "";
      this.playlistEventMapSelectedId = "";
      if (this.activeView === "field") this._syncUrl({ push: false });
    },

    fieldOpenSourceRecord(videoId) {
      const id = String(videoId || "").trim();
      if (!id) return;
      this.playerPageVideoId = id;
      this.switchView("video");
    },

    async fieldToggleLinearView() {
      this.fieldLinearView = !this.fieldLinearView;
      if (!this.fieldLinearView) return;
      const domainId = String(this.selectedPlaylistId || "");
      if (!domainId) return;
      this.fieldLinearLoading = true;
      try {
        const params = new URLSearchParams({ limit: "500" });
        if (this.playlistEventMapWindowStart) params.set("event_time_start", `${this.playlistEventMapWindowStart}T00:00:00Z`);
        if (this.playlistEventMapWindowEnd) params.set("event_time_end", `${this.playlistEventMapWindowEnd}T23:59:59Z`);
        if (this.playlistEventMapTypeFilter) {
          const option = this.playlistEventMapTypeOptions().find(
            (item) => String(item.code) === String(this.playlistEventMapTypeFilter)
          );
          if (option?.value) params.set("event_type", String(option.value));
        }
        const payload = await this.api(`/domains/${encodeURIComponent(domainId)}/canonicals?${params.toString()}`);
        this.fieldLinearItems = Array.isArray(payload?.items) ? payload.items : [];
      } finally {
        this.fieldLinearLoading = false;
      }
    },

    async fieldReturnToCurrentSnapshot() {
      if (!this.fieldRequestedSnapshotId) return;
      this.fieldRequestedSnapshotId = "";
      this._syncUrl({ push: false });
      await this.playlistEventMapLoadView({ silent: false, schedulePolling: true });
    },

    fieldLinearItemTime(item) {
      const raw = item?.event_time_start;
      if (!raw) return "时间未知";
      try {
        return new Date(raw).toLocaleString();
      } catch {
        return String(raw);
      }
    },

    saveFieldCursor({ immediate = false } = {}) {
      if (this._fieldHydrating) return null;
      if (this._fieldCursorSaveTimer) window.clearTimeout(this._fieldCursorSaveTimer);
      const save = async () => {
        this._fieldCursorSaveTimer = null;
        const domainId = String(this.selectedPlaylistId || "");
        if (!domainId) return;
        const latestChange = this.fieldFeedItems()?.[0]?.id || null;
        const rawCameraState = this.playlistEventMapController()?.cameraState() || null;
        const cameraState = rawCameraState ? {
          ...rawCameraState,
          framing_version: FIELD_CAMERA_FRAMING_VERSION,
          snapshot_id: this.playlistEventMapSnapshotId || null,
          window_start: this.playlistEventMapWindowStart || null,
          window_end: this.playlistEventMapWindowEnd || null,
        } : null;
        const payload = {
          snapshot_id: this.playlistEventMapSnapshotId || this.domainObservation?.snapshot?.id || null,
          observed_at: this.domainObservation?.snapshot?.observed_at || this.currentDomain()?.snapshot?.observed_at || null,
          event_time_start: this.playlistEventMapWindowStart ? `${this.playlistEventMapWindowStart}T00:00:00Z` : null,
          event_time_end: this.playlistEventMapWindowEnd ? `${this.playlistEventMapWindowEnd}T23:59:59Z` : null,
          view_mode: this.fieldMode,
          last_page: "field",
          camera_state: cameraState,
          filter_state: {
            event_type: this.playlistEventMapTypeFilter || null,
            entity: this.playlistEventMapEntityFilter || null,
            timeline_scope: this.playlistEventMapTimelineScope || "normal",
          },
          selected_object_type: this.playlistEventMapSelectedKind || null,
          selected_object_id: this.playlistEventMapSelectedId || null,
          last_change_id: latestChange,
        };
        try {
          await this.api(`/domains/${encodeURIComponent(domainId)}/observation/cursor`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
        } catch {
          // 游标保存失败不打断观察；下一次状态变化会再次提交。
        }
      };
      if (immediate) return save();
      this._fieldCursorSaveTimer = window.setTimeout(save, 350);
      return null;
    },

    async loadStories() {
      const domainId = await this.ensureCurrentDomain();
      if (!domainId) return;
      this.storiesLoading = true;
      try {
        const payload = await this.api(`/domains/${encodeURIComponent(domainId)}/stories`);
        this.stories = Array.isArray(payload?.items) ? payload.items : [];
        const requested = queryValue("story_id");
        const nextId = requested || this.storySelectedId || this.stories?.[0]?.story_identity_id || "";
        if (nextId) await this.loadStoryDetail(nextId, { markRead: false });
      } finally {
        this.storiesLoading = false;
      }
    },

    async loadStoryDetail(storyId, { markRead = false } = {}) {
      const domainId = String(this.selectedPlaylistId || "");
      const id = String(storyId || "").trim();
      if (!domainId || !id) return;
      this.storySelectedId = id;
      this.storyDetailLoading = true;
      try {
        const params = new URLSearchParams();
        if (this.activeView === "field" && this.fieldRequestedSnapshotId) {
          params.set("snapshot_id", String(this.fieldRequestedSnapshotId));
        }
        this.storyDetail = await this.api(
          `/domains/${encodeURIComponent(domainId)}/stories/${encodeURIComponent(id)}${params.size ? `?${params}` : ""}`
        );
        if (markRead) {
          await this.storyUpdateReadState({
            mark_read: true,
            snapshot_id: this.storyDetail?.current_trajectory?.snapshot_id || null,
          });
        }
      } finally {
        this.storyDetailLoading = false;
      }
    },

    async storyUpdateReadState(values) {
      const domainId = String(this.selectedPlaylistId || "");
      const id = String(this.storySelectedId || "");
      if (!domainId || !id) return;
      await this.api(`/domains/${encodeURIComponent(domainId)}/stories/${encodeURIComponent(id)}/read-state`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values || {}),
      });
      await this.loadStoryDetail(id, { markRead: false });
      const row = (this.stories || []).find((item) => String(item.story_identity_id) === id);
      if (row && Object.prototype.hasOwnProperty.call(values || {}, "followed")) row.followed = !!values.followed;
      if (row && values?.mark_read) row.unread = false;
    },

    storyRevisionDeltaLabel(revision) {
      const delta = revision?.delta || {};
      if (delta.baseline) return "初始版本";
      const added = (delta.members_added || []).length;
      const removed = (delta.members_removed || []).length;
      const edgeAdded = (delta.edges_added || []).length;
      const edgeRemoved = (delta.edges_removed || []).length;
      const evidenceAdded = (delta.evidence_added || []).length;
      const evidenceRemoved = (delta.evidence_removed || []).length;
      const parts = [];
      if (added) parts.push(`+${added} 节点`);
      if (removed) parts.push(`−${removed} 节点`);
      if (edgeAdded) parts.push(`+${edgeAdded} 关系`);
      if (edgeRemoved) parts.push(`−${edgeRemoved} 关系`);
      if (evidenceAdded) parts.push(`+${evidenceAdded} 证据`);
      if (evidenceRemoved) parts.push(`−${evidenceRemoved} 证据`);
      return parts.join(" · ") || "结构未变，叙述修订";
    },

    async storyOpenCanonical(canonicalId, position) {
      const id = String(canonicalId || "").trim();
      if (!id) return;
      const latest = this.storyDetail?.revisions?.at(-1);
      await this.storyUpdateReadState({
        mark_read: true,
        snapshot_id: latest?.snapshot_id || null,
        position: Number.isInteger(Number(position)) ? Number(position) : null,
      });
      this.fieldMode = "now";
      this.fieldRequestedCanonicalId = id;
      this.playlistEventMapSelectedKind = "canonical";
      this.playlistEventMapSelectedId = id;
      this.switchView("field");
    },

    storyOpenInField() {
      if (!this.storySelectedId) return;
      this.fieldMode = "story";
      this.switchView("field");
    },

    async loadBriefsV2() {
      const domainId = await this.ensureCurrentDomain();
      if (!domainId) return;
      this.briefsV2Loading = true;
      try {
        this.playlistDetail = await this.api(`/playlists/${encodeURIComponent(domainId)}/detail`);
        const rows = await this.api(`/briefs?playlist_id=${encodeURIComponent(domainId)}&limit=100&offset=0`);
        this.briefsV2 = Array.isArray(rows) ? rows : [];
        const requested = queryValue("brief_id");
        const nextId = requested || this.briefV2SelectedId || this.briefsV2?.[0]?.id || "";
        if (nextId) await this.loadBriefV2Detail(nextId);
      } finally {
        this.briefsV2Loading = false;
      }
    },

    async loadBriefV2Detail(briefId) {
      const id = String(briefId || "").trim();
      if (!id) return;
      this.briefV2SelectedId = id;
      this.briefV2Loading = true;
      try {
        const detail = await this.api(`/briefs/${encodeURIComponent(id)}/structured`);
        this.briefV2Detail = detail;
        this.briefV2Markdown = "";
        this.briefV2Html = "";
        if (detail?.markdown_url) {
          const response = await this.fetchWithApiAuth(detail.markdown_url);
          if (!response.ok) throw new Error(`${response.status}: brief markdown fetch failed`);
          this.briefV2Markdown = await response.text();
          this.briefV2Html = this._briefToHtml(this.briefV2Markdown);
          const anchor = String(this.briefV2RequestedAnchor || "");
          if (anchor) {
            await new Promise((resolve) => requestAnimationFrame(resolve));
            document.getElementById(anchor)?.scrollIntoView({ block: "center", behavior: "smooth" });
            this.briefV2RequestedAnchor = "";
          }
        }
      } finally {
        this.briefV2Loading = false;
      }
    },

    briefOpenReference(reference) {
      if (!reference) return;
      const objectId = String(reference.object_id || "");
      if (reference.object_type === "story") {
        this.storySelectedId = objectId;
        this.fieldMode = "story";
      } else if (reference.object_type === "evidence") {
        this.openEvidenceInField(reference.evidence_revision_id || objectId);
        return;
      } else if (["source", "source_record"].includes(reference.object_type)) {
        this.fieldOpenSourceRecord(objectId);
        return;
      } else if (reference.object_type === "canonical") {
        this.playlistEventMapSelectedKind = reference.object_type;
        this.playlistEventMapSelectedId = objectId;
        this.fieldRequestedCanonicalId = objectId;
      }
      this.switchView("field");
    },

    openBriefAtReference(reference) {
      if (!reference?.brief_id) return;
      this.briefV2SelectedId = String(reference.brief_id);
      this.briefV2RequestedAnchor = String(reference.anchor || "");
      this.switchView("briefs");
    },

    async loadLibrary() {
      const domainId = await this.ensureCurrentDomain();
      const tab = queryValue("tab");
      if (LIBRARY_TABS.has(tab)) this.libraryTab = tab;
      const scope = queryValue("scope");
      if (["domain", "global"].includes(scope)) this.libraryScope = scope;
      if (domainId && this.libraryScope === "domain") {
        const payload = await this.api(`/library/sources?domain_id=${encodeURIComponent(domainId)}&scope=domain&limit=500`);
        this.libraryDomainMediaIds = (payload?.items || []).map((item) => String(item.id));
      } else {
        this.libraryDomainMediaIds = [];
      }
      if (this.libraryTab === "sources") {
        if (this.libraryScope === "domain") {
          this.mediaLoadingList = true;
          try {
            const params = new URLSearchParams({ limit: "500", offset: "0" });
            if (this.mediaQuery) params.set("q", this.mediaQuery);
            const rows = await this.api(`/media?${params.toString()}`);
            this.mediaList = Array.isArray(rows) ? rows : [];
            this.mediaOffset = this.mediaList.length;
          } finally {
            this.mediaLoadingList = false;
          }
          const allowed = new Set(this.libraryDomainMediaIds);
          this.mediaList = (this.mediaList || []).filter((item) => allowed.has(String(item.id)));
          this.mediaHasMore = false;
          this._teardownMediaIo();
        } else await this.loadMedia();
      } else {
        this.videoMediaIds = this.libraryScope === "domain" ? [...this.libraryDomainMediaIds] : [];
        await this.loadVideos();
      }
    },

    async setLibraryTab(tab) {
      this.libraryTab = LIBRARY_TABS.has(String(tab)) ? String(tab) : "sources";
      this._syncUrl({ push: false });
      await this.loadLibrary();
    },

    async setLibraryScope(scope) {
      this.libraryScope = scope === "global" ? "global" : "domain";
      this._syncUrl({ push: false });
      await this.loadLibrary();
    },

    async openDomainSourcePicker() {
      const domainId = await this.ensureCurrentDomain();
      if (!domainId) return;
      this.domainSourcePickerOpen = true;
      this.domainSourceQuery = "";
      await this.loadDomainSourceCandidates();
    },

    async loadDomainSourceCandidates() {
      const domainId = String(this.selectedPlaylistId || "");
      if (!domainId) return;
      this.domainSourceCandidatesLoading = true;
      try {
        const params = new URLSearchParams({
          domain_id: domainId,
          scope: "global",
          limit: "500",
          offset: "0",
        });
        if (String(this.domainSourceQuery || "").trim()) params.set("q", String(this.domainSourceQuery).trim());
        const payload = await this.api(`/library/sources?${params.toString()}`);
        this.domainSourceCandidates = Array.isArray(payload?.items) ? payload.items : [];
      } finally {
        this.domainSourceCandidatesLoading = false;
      }
    },

    async attachExistingSourceToDomain(source) {
      const domainId = String(this.selectedPlaylistId || "");
      const mediaId = String(source?.id || "");
      if (!domainId || !mediaId || source?.in_current_domain) return;
      this.domainSourceAttachSubmittingId = mediaId;
      try {
        await this.api(`/domains/${encodeURIComponent(domainId)}/sources`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ media_id: mediaId }),
        });
        source.in_current_domain = true;
        this.toastSuccess("已加入当前观测域");
        await this.loadLibrary();
      } finally {
        this.domainSourceAttachSubmittingId = "";
      }
    },

    async removeSourceFromCurrentDomain(source) {
      const domainId = String(this.selectedPlaylistId || "");
      const mediaId = String(source?.id || "");
      if (!domainId || !mediaId) return;
      if (!window.confirm(`从当前观测域移除“${source?.name || source?.url || mediaId}”？\n\n全局信源、来源记录和播放资产都会保留。`)) return;
      await this.api(`/domains/${encodeURIComponent(domainId)}/sources/${encodeURIComponent(mediaId)}`, {
        method: "DELETE",
      });
      this.toastSuccess("已从当前观测域移除；全局资料已保留");
      await this.loadLibrary();
    },

    async openDomainSettings() {
      const domainId = await this.ensureCurrentDomain();
      if (!domainId) return;
      this.domainSettingsOpen = true;
      this.domainSettingsLoading = true;
      this.domainDeleteConfirmDraft = "";
      try {
        const [detail, impact, observation] = await Promise.all([
          this.api(`/playlists/${encodeURIComponent(domainId)}/detail`),
          this.api(`/domains/${encodeURIComponent(domainId)}/deletion-impact`),
          this.api(`/domains/${encodeURIComponent(domainId)}/observation`).catch(() => null),
        ]);
        this.playlistDetail = detail;
        this.domainDeletionImpact = impact;
        if (observation) this.domainObservation = observation;
        this.domainSettingsNameDraft = String(detail?.name || "");
        this.domainSettingsDescriptionDraft = String(detail?.description || "");
      } finally {
        this.domainSettingsLoading = false;
      }
    },

    async saveDomainSettingsIdentity() {
      const domainId = String(this.selectedPlaylistId || "");
      const name = String(this.domainSettingsNameDraft || "").trim();
      if (!domainId || !name || this.domainSettingsSaving) return;
      this.domainSettingsSaving = true;
      try {
        this.playlistDetail = await this.api(`/playlists/${encodeURIComponent(domainId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name,
            description: String(this.domainSettingsDescriptionDraft || "").trim(),
          }),
        });
        await this.loadDomains({ force: true });
        this.toastSuccess("观测域信息已保存");
      } finally {
        this.domainSettingsSaving = false;
      }
    },

    async deleteCurrentDomainSafely() {
      const domainId = String(this.selectedPlaylistId || "");
      if (!domainId || this.domainDeleteSubmitting) return;
      this.domainDeleteSubmitting = true;
      try {
        await this.api(`/domains/${encodeURIComponent(domainId)}`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm_name: String(this.domainDeleteConfirmDraft || "") }),
        });
        this.domainSettingsOpen = false;
        this.playlistDetail = null;
        this.selectedPlaylistId = null;
        this.playlistPageId = null;
        this.domains = [];
        await this.loadDomains({ force: true });
        const nextId = String(this.domains?.[0]?.id || "");
        if (nextId) {
          this.selectedPlaylistId = nextId;
          this.playlistPageId = nextId;
          this.switchView("field");
        } else this.switchView("domains");
        this.toastSuccess("观测域已删除；共享资料已保留");
      } finally {
        this.domainDeleteSubmitting = false;
      }
    },

    openBriefGenerationSettings() {
      this.briefGenerationGranularityDraft = String(this.playlistDetail?.brief_granularity || "day");
      this.briefGenerationPromptDraft = String(this.playlistDetail?.brief_prompt || "");
      this.briefGenerationSettingsOpen = true;
    },

    async saveBriefGenerationSettings() {
      const domainId = String(this.selectedPlaylistId || "");
      if (!domainId || this.briefGenerationSaving) return;
      this.briefGenerationSaving = true;
      try {
        this.playlistDetail = await this.api(`/playlists/${encodeURIComponent(domainId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            brief_granularity: this.briefGenerationGranularityDraft,
            brief_prompt: String(this.briefGenerationPromptDraft || "").trim() || null,
          }),
        });
        this.briefGenerationSettingsOpen = false;
        this.toastSuccess("简报生成设置已保存");
      } finally {
        this.briefGenerationSaving = false;
      }
    },

    openBriefBatchGeneration() {
      this.briefBatchGranularity = String(this.playlistDetail?.brief_granularity || "day");
      this.briefBatchFrom = String(this.playlistDetail?.earliest_date || todayLocalIso());
      this.briefBatchTo = todayLocalIso();
      this.briefBatchOpen = true;
    },

    briefBatchEstimatedPeriods() {
      const from = new Date(`${this.briefBatchFrom}T00:00:00Z`);
      const to = new Date(`${this.briefBatchTo}T00:00:00Z`);
      if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || to < from) return 0;
      const days = Math.floor((to.getTime() - from.getTime()) / 86400000) + 1;
      if (this.briefBatchGranularity === "week") return Math.ceil(days / 7);
      if (this.briefBatchGranularity === "month") {
        return (to.getUTCFullYear() - from.getUTCFullYear()) * 12 + to.getUTCMonth() - from.getUTCMonth() + 1;
      }
      return days;
    },

    async submitBriefBatchGeneration() {
      const domainId = String(this.selectedPlaylistId || "");
      if (!domainId || !this.briefBatchEstimatedPeriods() || this.briefBatchSubmitting) return;
      this.briefBatchSubmitting = true;
      try {
        await this.api("/briefs/generate_range", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            playlist_id: domainId,
            granularity: this.briefBatchGranularity,
            from_date: this.briefBatchFrom,
            to_date: this.briefBatchTo,
          }),
        });
        this.briefBatchOpen = false;
        this.toastSuccess("批量简报任务已提交", { action: this.toastJobsAction() });
      } finally {
        this.briefBatchSubmitting = false;
      }
    },

    async runDomainOperation(action) {
      const domainId = String(this.selectedPlaylistId || "");
      if (!domainId || this.operationsActionSubmitting) return;
      if (action === "force_events" && !window.confirm("确认全部重新抽取当前观测域的事件？现有任务会按后端规则停止，任务历史保留。")) return;
      this.operationsActionSubmitting = action;
      try {
        if (action === "build_field") {
          await this.api(`/playlists/${encodeURIComponent(domainId)}/events/map/rebuild`, { method: "POST" });
        } else {
          await this.api(`/playlists/${encodeURIComponent(domainId)}/events/extract`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ force: action === "force_events" }),
          });
        }
        this.toastSuccess(action === "build_field" ? "星域构建任务已提交" : action === "force_events" ? "全部重抽任务已提交" : "事件补齐任务已提交", { action: this.toastJobsAction() });
        await this.loadJobs();
      } finally {
        this.operationsActionSubmitting = "";
      }
    },

    async loadOperations() {
      await this.loadJobs();
      const domainId = await this.ensureCurrentDomain();
      if (domainId) {
        try {
          this.domainObservation = await this.api(`/domains/${encodeURIComponent(domainId)}/observation`);
        } catch {
          // 运行中心仍可在没有星域快照时展示任务。
        }
      }
    },

    openOperations(context = null) {
      if (this.activeView !== "operations") {
        this.operationsReturnUrl = `${window.location.pathname || "/"}${window.location.search || ""}`;
      }
      this.operationsContext = context && typeof context === "object" ? { ...context } : null;
      this.switchView("operations");
    },

    operationsContextTitle() {
      if (this.operationsContext?.type === "field") return "正在诊断星域异常";
      if (this.operationsContext?.type === "health") return "正在查看观测站健康状态";
      return "全局任务与服务运行状态";
    },

    operationsContextDetail() {
      const domain = this.operationsContext?.domain_name || this.currentDomainLabel();
      const message = String(this.operationsContext?.message || "").trim();
      if (this.operationsContext?.type === "field") {
        return [domain ? `观测域：${domain}` : "", message].filter(Boolean).join(" · ");
      }
      return message || "任务状态、处理覆盖率与依赖健康汇聚在同一视图。";
    },

    returnFromOperations() {
      const fallback = this.selectedPlaylistId
        ? `/?domain_id=${encodeURIComponent(this.selectedPlaylistId)}`
        : "/";
      const target = String(this.operationsReturnUrl || fallback);
      const parsed = new URL(target, window.location.origin);
      history.pushState({}, "", `${parsed.pathname}${parsed.search}`);
      const view = this._parseViewFromLocation();
      this._applyQueryFromLocation(view);
      this.operationsReturnUrl = "";
      this.operationsContext = null;
      this.switchView(view, { push: false });
    },

    async runSemanticSearch() {
      const value = String(this.semanticSearchQuery || "").trim();
      if (!value) {
        this.semanticSearchGroups = {};
        return;
      }
      this.semanticSearchLoading = true;
      this.semanticSearchOpen = true;
      try {
        const domainId = String(this.selectedPlaylistId || "");
        const params = new URLSearchParams({ q: value, limit: "20" });
        if (domainId) params.set("domain_id", domainId);
        const payload = await this.api(`/search/semantic?${params.toString()}`);
        this.semanticSearchGroups = payload?.groups || {};
      } finally {
        this.semanticSearchLoading = false;
      }
    },

    semanticSearchGroupList() {
      const labels = { domains: "观测域", topics: "主题", canonicals: "真实事件", stories: "故事", entities: "实体", briefs: "简报", sources: "信源", records: "来源记录" };
      return Object.entries(this.semanticSearchGroups || {})
        .filter(([, items]) => Array.isArray(items) && items.length)
        .map(([key, items]) => ({ key, label: labels[key] || key, items }));
    },

    openSemanticSearchResult(item) {
      if (!item?.web_url) return;
      history.pushState({}, "", item.web_url);
      const view = this._parseViewFromLocation();
      this._applyQueryFromLocation(view);
      this.semanticSearchOpen = false;
      this.switchView(view, { push: false });
    },
  };
}
