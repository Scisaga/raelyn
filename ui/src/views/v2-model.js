import { periodDiff, periodStartIso } from "../shared/playlist-periods.js";

const FIELD_MODES = new Set(["now", "replay", "story", "verify"]);
const LIBRARY_TABS = new Set(["sources", "records"]);
const FIELD_CAMERA_FRAMING_VERSION = 5;
const LATEST_SNAPSHOT_NAVIGATION_TARGET = "@latest";

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

function fieldWindowMonths(start, end) {
  const startMatch = String(start || "").match(/^(\d{4})-(\d{2})/);
  const endMatch = String(end || "").match(/^(\d{4})-(\d{2})/);
  if (!startMatch || !endMatch) return 12;
  const raw = (Number(endMatch[1]) - Number(startMatch[1])) * 12 + Number(endMatch[2]) - Number(startMatch[2]) + 1;
  return [3, 6, 9, 12].reduce((best, item) => Math.abs(item - raw) < Math.abs(best - raw) ? item : best, 3);
}

function localDateTimeLabel(value) {
  const date = new Date(String(value || ""));
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part) => String(part).padStart(2, "0");
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function queryValue(name) {
  try {
    return new URLSearchParams(window.location.search || "").get(name) || "";
  } catch {
    return "";
  }
}

function optionalPointIndex(value) {
  if (value === null || value === undefined || value === "") return NaN;
  const index = Number(value);
  return Number.isInteger(index) && index >= 0 ? index : NaN;
}

export function createV2ViewMethods() {
  return {
    async loadDomains({ force = false, compact = false } = {}) {
      const loadKey = compact ? "compact" : "full";
      if (this._domainsLoadPromise) {
        if (!compact && this._domainsLoadKey === "compact") {
          try {
            await this._domainsLoadPromise;
          } catch {
            // 完整目录请求在下方自行报告错误。
          }
          return await this.loadDomains({ force: true, compact: false });
        }
        return await this._domainsLoadPromise;
      }
      if (
        !force
        && Array.isArray(this.domains)
        && this.domains.length
        && (compact || this._domainsComplete)
      ) return this.domains;
      this.domainsLoading = true;
      const request = (async () => {
        const payload = await this.api(compact ? "/domains?compact=true" : "/domains");
        this.domains = Array.isArray(payload?.items) ? payload.items : [];
        if (!compact) this._domainsComplete = true;
        return this.domains;
      })();
      this._domainsLoadPromise = request;
      this._domainsLoadKey = loadKey;
      try {
        return await request;
      } finally {
        if (this._domainsLoadPromise === request) {
          this._domainsLoadPromise = null;
          this._domainsLoadKey = "";
          this.domainsLoading = false;
        }
      }
    },

    currentDomain() {
      const id = String(this.selectedPlaylistId || this.playlistPageId || "");
      return (this.domains || []).find((domain) => String(domain.id) === id) || null;
    },

    currentDomainLabel() {
      if (!String(this.selectedPlaylistId || this.playlistPageId || "")) return "选择观测域";
      const domain = this.currentDomain();
      return domain?.name || this.playlistDetail?.name || "选择观测域";
    },

    domainSwitcherTriggerLabel() {
      if (this.domainsLoading && !String(this.selectedPlaylistId || this.playlistPageId || "")) return "正在恢复观测域…";
      return this.currentDomainLabel();
    },

    domainSwitcherFilteredDomains() {
      const query = String(this.domainSwitcherQuery || "").trim().toLocaleLowerCase();
      const domains = Array.isArray(this.domains) ? this.domains : [];
      if (!query) return domains;
      return domains.filter((domain) => {
        const name = String(domain?.name || "").toLocaleLowerCase();
        const description = String(domain?.description || "").toLocaleLowerCase();
        return name.includes(query) || description.includes(query);
      });
    },

    domainSwitcherMeta(domain) {
      if (!domain) return "";
      if (domain.observation_enabled === false) return "观测已停用";
      const status = String(domain?.snapshot?.status || "");
      if (status && status !== "ready") return "正在建立星域";
      const changes = Number(domain?.unobserved_change_count || 0);
      if (changes > 0) return `${changes} 条新变化`;
      const stories = Number(domain?.snapshot?.story_count ?? domain?.story_count);
      if (Number.isFinite(stories) && stories > 0) return `${stories} 条故事`;
      return domain?.snapshot ? "星域已形成" : "尚未形成星域";
    },

    domainAvatarLabel(domain) {
      const name = String(domain?.name || "").trim();
      return Array.from(name).slice(0, 2).join("") || "域";
    },

    domainSwitcherActiveOptionId() {
      const domains = this.domainSwitcherFilteredDomains();
      if (!domains.length || this.domainSwitcherActiveIndex < 0) return "";
      return `raelyn-domain-option-${Math.min(this.domainSwitcherActiveIndex, domains.length - 1)}`;
    },

    domainSwitcherResetActiveIndex() {
      const domains = this.domainSwitcherFilteredDomains();
      const currentId = String(this.selectedPlaylistId || "");
      const currentIndex = domains.findIndex((domain) => String(domain?.id || "") === currentId);
      this.domainSwitcherActiveIndex = currentIndex >= 0 ? currentIndex : (domains.length ? 0 : -1);
    },

    async openDomainSwitcherMenu() {
      if (this.domainSwitcherOpen) {
        this.closeDomainSwitcherMenu();
        return;
      }
      this.domainSwitcherOpen = true;
      this.domainSwitcherQuery = "";
      this.domainSwitcherError = "";
      this.domainSwitcherRetryMode = "domains";
      const openToken = ++this._domainSwitcherOpenToken;
      this.$nextTick?.(() => {
        if (this.domainSwitcherOpen && openToken === this._domainSwitcherOpenToken) {
          this.$refs?.domainSwitcherSearch?.focus();
        }
      });
      try {
        await this.loadDomains();
        if (!this.domainSwitcherOpen || openToken !== this._domainSwitcherOpenToken) return;
        this.domainSwitcherResetActiveIndex();
      } catch (error) {
        if (!this.domainSwitcherOpen || openToken !== this._domainSwitcherOpenToken) return;
        this.domainSwitcherError = error?.message || String(error);
      }
    },

    closeDomainSwitcherMenu({ restoreFocus = true, hideMobile = false } = {}) {
      this._domainSwitcherOpenToken += 1;
      this.domainSwitcherOpen = false;
      this.domainSwitcherQuery = "";
      this.domainSwitcherActiveIndex = 0;
      this.domainSwitcherRetryMode = "domains";
      const shouldHideMobile = Boolean(hideMobile && this.sidebarMobilePortrait && !this.sidebarHidden);
      if (shouldHideMobile) {
        if (typeof this.toggleSidebar === "function") this.toggleSidebar();
        else this.sidebarHidden = true;
      }
      if (shouldHideMobile) this.$nextTick?.(() => this.$refs?.mainStage?.focus());
      else if (restoreFocus) this.$nextTick?.(() => this.$refs?.domainSwitcherTrigger?.focus());
    },

    async retryDomainSwitcherLoad() {
      if (this.domainSwitcherRetryMode === "page") {
        this.domainSwitcherSwitching = true;
        this.domainSwitcherPendingId = String(this.selectedPlaylistId || "");
        this.domainSwitcherError = "";
        let loaded = false;
        try {
          await this.refreshActive({ throwOnError: true });
          loaded = true;
        } catch (error) {
          this.domainSwitcherError = error?.message || String(error);
          this.domainSwitcherRetryMode = "page";
        } finally {
          this.domainSwitcherSwitching = false;
          this.domainSwitcherPendingId = "";
        }
        if (loaded) this.closeDomainSwitcherMenu({ hideMobile: true });
        return;
      }
      const openToken = this._domainSwitcherOpenToken;
      this.domainSwitcherError = "";
      this.domainSwitcherRetryMode = "domains";
      try {
        await this.loadDomains({ force: true });
        if (!this.domainSwitcherOpen || openToken !== this._domainSwitcherOpenToken) return;
        this.domainSwitcherResetActiveIndex();
        this.$nextTick?.(() => this.$refs?.domainSwitcherSearch?.focus());
      } catch (error) {
        if (!this.domainSwitcherOpen || openToken !== this._domainSwitcherOpenToken) return;
        this.domainSwitcherError = error?.message || String(error);
      }
    },

    domainSwitcherMove(step) {
      const domains = this.domainSwitcherFilteredDomains();
      if (!domains.length) {
        this.domainSwitcherActiveIndex = -1;
        return;
      }
      const current = Number.isInteger(this.domainSwitcherActiveIndex) ? this.domainSwitcherActiveIndex : 0;
      this.domainSwitcherActiveIndex = (current + Number(step || 0) + domains.length) % domains.length;
      this.$nextTick?.(() => {
        const optionId = this.domainSwitcherActiveOptionId();
        if (optionId && typeof document !== "undefined") document.getElementById(optionId)?.scrollIntoView({ block: "nearest" });
      });
    },

    async domainSwitcherChooseActive() {
      const domains = this.domainSwitcherFilteredDomains();
      const domain = domains[this.domainSwitcherActiveIndex];
      if (domain) await this.domainSwitcherChoose(domain.id);
    },

    async domainSwitcherChoose(domainId) {
      const nextId = String(domainId || "").trim();
      if (!nextId || this.domainSwitcherSwitching) return false;
      if (nextId === String(this.selectedPlaylistId || "")) {
        this.closeDomainSwitcherMenu({ hideMobile: true });
        return true;
      }
      const selected = await this.selectDomain(nextId);
      if (selected && !this.domainSwitcherError) this.closeDomainSwitcherMenu({ hideMobile: true });
      return selected;
    },

    domainSwitcherBrowseAll() {
      if (this.domainSwitcherSwitching) return;
      this.closeDomainSwitcherMenu({ restoreFocus: false, hideMobile: true });
      this.switchView("domains");
    },

    domainSwitcherCreate() {
      if (this.domainSwitcherSwitching) return;
      this.closeDomainSwitcherMenu({ restoreFocus: false, hideMobile: true });
      if (typeof this.openCreateDomain === "function") this.openCreateDomain();
      else this.openCreatePlaylist?.({ asDomain: true });
    },

    async ensureCurrentDomain({ compact = false } = {}) {
      const domains = await this.loadDomains({ compact });
      let storedDomainId = "";
      try {
        storedDomainId = String(localStorage.getItem(this.v2LastDomainKey) || "").trim();
      } catch {
        storedDomainId = "";
      }
      const validIds = new Set((domains || []).map((domain) => String(domain.id)));
      const candidates = [
        queryValue("domain_id"),
        this.selectedPlaylistId,
        this.playlistPageId,
        storedDomainId,
      ].map((value) => String(value || "").trim());
      const defaultDomain = (domains || []).find((domain) => domain?.snapshot?.status === "ready") || domains?.[0];
      const domainId = candidates.find((candidate) => validIds.has(candidate)) || String(defaultDomain?.id || "");
      this.selectedPlaylistId = domainId || null;
      this.playlistPageId = domainId || null;
      if (domainId) {
        try {
          localStorage.setItem(this.v2LastDomainKey, domainId);
        } catch {
          // 本地存储不可用时仍允许本次会话继续。
        }
        if (queryValue("domain_id") !== domainId) this._syncUrl?.({ push: false });
      } else {
        try {
          localStorage.removeItem?.(this.v2LastDomainKey);
        } catch {
          // 本地存储不可用时保持明确的无域状态。
        }
      }
      return domainId;
    },

    async selectDomain(domainId) {
      const nextId = String(domainId || "").trim();
      const previousId = String(this.selectedPlaylistId || "");
      if (!nextId || nextId === previousId || this.domainSwitcherSwitching) return false;
      const viewAtStart = this.activeView;

      this.domainSwitcherSwitching = true;
      this.domainSwitcherPendingId = nextId;
      this.domainSwitcherError = "";
      this.domainSwitcherRetryMode = "domains";
      const requestToken = ++this._domainSwitchRequestToken;
      let previousStoredId = "";
      try {
        previousStoredId = String(localStorage.getItem(this.v2LastDomainKey) || "");
      } catch {
        previousStoredId = previousId;
      }

      const previousPageId = this.playlistPageId;
      let committed = false;
      try {
        const domains = await this.loadDomains();
        if (!(domains || []).some((domain) => String(domain?.id || "") === nextId)) {
          throw new Error("选择的观测域已不存在，请刷新后重试");
        }

        let preparedStoryQueue = null;
        if (viewAtStart === "stories") {
          const scope = this.storyQueueTabs().some((item) => item.scope === this.storyScope) ? this.storyScope : "attention";
          const params = new URLSearchParams({
            scope,
            limit: String(this.storiesPageSize),
            offset: "0",
          });
          const query = String(this.storyQueueQueries?.[scope] || "").trim();
          if (query) params.set("q", query);
          const payload = await this.api(`/domains/${encodeURIComponent(nextId)}/stories?${params}`);
          const items = Array.isArray(payload?.items) ? payload.items : [];
          const total = Number.isFinite(Number(payload?.total)) ? Number(payload.total) : items.length;
          preparedStoryQueue = {
            scope,
            items,
            total,
            hasMore: typeof payload?.has_more === "boolean" ? payload.has_more : items.length < total,
          };
        } else if (viewAtStart === "field") {
          await Promise.all([
            this.api(`/playlists/${encodeURIComponent(nextId)}/detail`),
            this.api(`/domains/${encodeURIComponent(nextId)}/observation/cursor`),
            this.api(`/playlists/${encodeURIComponent(nextId)}/events/map/manifest?compact=true`, { cache: "no-store" }),
          ]);
        } else if (viewAtStart === "playlist") {
          await this.api(`/playlists/${encodeURIComponent(nextId)}/detail`);
        } else if (viewAtStart === "briefs") {
          await Promise.all([
            this.api(`/playlists/${encodeURIComponent(nextId)}/detail`),
            this.api(`/briefs?playlist_id=${encodeURIComponent(nextId)}&limit=100&offset=0`),
          ]);
        } else if (viewAtStart === "library" && this.libraryScope === "domain") {
          await this.api(`/library/sources?domain_id=${encodeURIComponent(nextId)}&scope=domain&limit=500`);
        }
        if (this.activeView !== viewAtStart) return false;
        if (requestToken !== this._domainSwitchRequestToken) return false;

        if (viewAtStart === "field") await this.saveFieldCursor({ immediate: true });
        if (this.activeView !== viewAtStart || requestToken !== this._domainSwitchRequestToken) return false;
        if (viewAtStart === "field" && typeof this.leaveField === "function") this.leaveField();
        if (viewAtStart === "playlist" && typeof this.leavePlaybackPage === "function") this.leavePlaybackPage();

        this.selectedPlaylistId = nextId;
        this.playlistPageId = nextId;
        committed = true;
        try {
          localStorage.setItem(this.v2LastDomainKey, nextId);
        } catch {
          // 本地存储不可用不影响本次切换。
        }

        this.playlistEventMapReset?.();
        if (viewAtStart === "field") this.playlistEventMapLoading = true;
        this.fieldMode = "now";
        this.fieldRequestedCanonicalId = "";
        this.fieldRequestedTopicId = "";
        this.fieldRequestedEvidenceId = "";
        this.fieldRequestedSnapshotId = "";
        this.fieldRequestedWindowStart = "";
        this.fieldRequestedWindowEnd = "";
        this.fieldRequestedEntity = null;
        this.fieldEvidenceDetail = null;
        this.fieldSelectedChange = null;

        this._storiesRequestToken += 1;
        this._storyDetailRequestToken += 1;
        this.storyDetailLoading = false;
        this.storyCloseInspector?.({ restoreFocus: false });
        this.storySelectedId = "";
        this.storySelectedSnapshotId = "";
        this.storyDetail = null;
        const emptyQueue = () => ({ items: [], total: 0, hasMore: false, loaded: false });
        this.storyQueues = {
          attention: emptyQueue(),
          followed: emptyQueue(),
          established: emptyQueue(),
          emerging: emptyQueue(),
        };
        if (preparedStoryQueue) {
          this.storyQueues[preparedStoryQueue.scope] = {
            items: preparedStoryQueue.items,
            total: preparedStoryQueue.total,
            hasMore: preparedStoryQueue.hasMore,
            loaded: true,
            loading: false,
            loadingMore: false,
            error: "",
          };
          this.storiesDomainMissing = false;
          this.storiesError = "";
        }
        this.storySyncQueueState?.();
        this.briefV2SelectedId = "";
        this.briefV2Detail = null;
        this._syncUrl({ push: false });

        if (!preparedStoryQueue) await this.refreshActive({ throwOnError: true });
        return true;
      } catch (error) {
        if (requestToken !== this._domainSwitchRequestToken) return false;
        if (committed) {
          if (["field", "playlist", "briefs", "library", "domain-settings"].includes(viewAtStart)) {
            const message = error?.message || String(error);
            this.domainSwitcherError = message;
            this.domainSwitcherRetryMode = "page";
            this.globalStatus = `error: ${message}`;
            this.toastError?.(`观测域已切换，但页面读取失败：${message}`);
            return true;
          }
          this.selectedPlaylistId = previousId || null;
          this.playlistPageId = previousPageId || previousId || null;
          try {
            if (previousStoredId) localStorage.setItem(this.v2LastDomainKey, previousStoredId);
            else localStorage.removeItem?.(this.v2LastDomainKey);
          } catch {
            // 回滚仍以当前会话内存状态为准。
          }
          this._syncUrl({ push: false });
          await this.refreshActive();
        }
        this.domainSwitcherError = error?.message || String(error);
        this.domainSwitcherRetryMode = "domains";
        return false;
      } finally {
        if (requestToken === this._domainSwitchRequestToken) {
          this.domainSwitcherSwitching = false;
          this.domainSwitcherPendingId = "";
        }
      }
    },

    async loadDomainDirectory() {
      await this.loadDomains({ force: true });
      this.pageTitle = "观测域";
    },

    openGlobalSources() {
      this.libraryTab = "sources";
      this.libraryScope = "global";
      this.videoMediaIds = [];
      this.videoMediaDraftIds = [];
      this.switchView("library");
    },

    openCurrentDomainLibrary() {
      this.libraryScope = "domain";
      this.videoMediaIds = [];
      this.videoMediaDraftIds = [];
      this.switchView("library");
    },

    async openDomain(domainId) {
      const nextId = String(domainId || "").trim();
      if (!nextId) return;
      if (nextId !== String(this.selectedPlaylistId || "") && !(await this.selectDomain(nextId))) return;
      this.switchView("field");
    },

    domainFreshnessLabel(domain) {
      if (domain?.observation_enabled === false) return "观测已停用 · 仅归档来源记录";
      const raw = domain?.snapshot?.observed_at;
      if (!raw) return "尚未形成星域";
      try {
        return `认知更新于 ${new Date(raw).toLocaleString()}`;
      } catch {
        return String(raw);
      }
    },

    async loadField() {
      const fieldRequestToken = Number(this._fieldLoadRequestToken || 0) + 1;
      this._fieldLoadRequestToken = fieldRequestToken;
      this._fieldSnapshotNavigationToken = Number(this._fieldSnapshotNavigationToken || 0) + 1;
      this.fieldCompleteSnapshotNavigation(this._fieldSnapshotNavigationToken);
      this._playlistEventMapSelectionToken = Number(this._playlistEventMapSelectionToken || 0) + 1;
      this._fieldEvidenceRequestToken = Number(this._fieldEvidenceRequestToken || 0) + 1;
      this._storyDetailRequestToken = Number(this._storyDetailRequestToken || 0) + 1;
      this._playlistEventMapDetailToken = Number(this._playlistEventMapDetailToken || 0) + 1;
      this._abortCtrl?.("_playlistEventMapDetailAbortCtrl");
      this.fieldEvidenceLoading = false;
      const requestedSnapshotId = String(this.fieldRequestedSnapshotId || "");
      const requestedWindowStart = String(this.fieldRequestedWindowStart || "").slice(0, 10);
      const requestedWindowEnd = String(this.fieldRequestedWindowEnd || "").slice(0, 10);
      let domainId = String(
        queryValue("domain_id") || this.selectedPlaylistId || this.playlistPageId || ""
      ).trim();
      if (!domainId) domainId = await this.ensureCurrentDomain({ compact: true });
      if (Number(this._fieldLoadRequestToken || 0) !== fieldRequestToken) return;
      if (!domainId) {
        this.playlistEventMapLoading = false;
        this.playlistDetail = null;
        this.pageTitle = "星域";
        return;
      }
      this.selectedPlaylistId = domainId;
      this.playlistPageId = domainId;
      this.playlistSubview = "analysis";
      if (requestedWindowStart && requestedWindowEnd && requestedWindowStart <= requestedWindowEnd) {
        this.playlistEventMapWindowStart = requestedWindowStart;
        this.playlistEventMapWindowEnd = requestedWindowEnd;
        this.playlistEventMapWindowMonths = fieldWindowMonths(requestedWindowStart, requestedWindowEnd);
      } else if (!requestedSnapshotId) {
        this.playlistEventMapWindowStart = "";
        this.playlistEventMapWindowEnd = "";
        this.playlistEventMapWindowMonths = 12;
      }
      const isCurrentFieldRequest = () => (
        Number(this._fieldLoadRequestToken || 0) === fieldRequestToken
        && this.activeView === "field"
        && String(this.selectedPlaylistId || "") === String(domainId)
        && String(this.fieldRequestedSnapshotId || "") === requestedSnapshotId
      );
      this._fieldHydrating = true;
      this._fieldLinearRequestToken = Number(this._fieldLinearRequestToken || 0) + 1;
      this.fieldLinearView = false;
      this.fieldLinearItems = [];
      this.fieldObservationRailOpen = false;
      this.playlistEventMapSearchOpen = false;
      this.playlistEventMapFiltersOpen = false;
      this.fieldObservationRailTab = "newly_occurred";
      this.domainObservationFeed = emptyObservationFeed();
      this.fieldObservationFeedLoading = true;
      this.fieldObservationFeedLoaded = false;
      this.fieldObservationFeedError = "";
      if (this._playlistEventMapPlaylistId && String(this._playlistEventMapPlaylistId) !== String(domainId)) {
        this.playlistEventMapReset();
      }
      this._playlistEventMapPlaylistId = String(domainId);
      if (!this.playlistEventMapController()) this.playlistEventMapLoading = true;
      try {
        let startupCursor = null;
        const mapPromise = this.playlistEventMapLoadView({
          initialCursor: () => startupCursor,
        });
        this._attachInitialFieldVisual?.(mapPromise);
        const cursorPromise = this.api(
          `/domains/${encodeURIComponent(domainId)}/observation/cursor`
        ).then((cursor) => {
          if (!isCurrentFieldRequest()) return cursor;
          startupCursor = cursor;
          this.fieldMode = "now";
          if (Object.prototype.hasOwnProperty.call(cursor?.filter_state || {}, "event_type")) this.playlistEventMapTypeFilter = cursor.filter_state.event_type || "";
          if (["normal", "full"].includes(cursor?.filter_state?.timeline_scope)) this.playlistEventMapTimelineScope = cursor.filter_state.timeline_scope;
          this.playlistEventMapInitializeWindow();
          this.playlistEventMapUpdateLayers();
          return cursor;
        });
        const [detail, cursor] = await Promise.all([
          this.api(`/playlists/${encodeURIComponent(domainId)}/detail`),
          cursorPromise,
        ]);
        if (!isCurrentFieldRequest()) return;
        this.playlistDetail = detail;
        const domain = this.currentDomain();
        this.domainObservation = {
          domain: domain ? { id: domain.id, name: domain.name, description: domain.description } : null,
          snapshot: domain?.snapshot || null,
          cursor,
          unobserved_change_count: Number(domain?.unobserved_change_count || 0),
        };
        this.playlistEventMapInitializeWindow();
        this.playlistEventMapUpdateLayers();
        const mapResult = await mapPromise;
        if (!isCurrentFieldRequest()) return;
        const controller = this.playlistEventMapController();
        // 首次画布会在隐藏状态下一次性确定镜头；只有复用既有画布的路径
        // 才在这里恢复游标，避免网格显示后再次瞬移。
        if (!mapResult?.initialCameraApplied) {
          if (this.fieldCameraStateCompatible(cursor?.camera_state)) controller?.restoreCameraState(cursor.camera_state);
          else controller?.fitActiveWindow();
        }

        const entity = this.fieldRequestedEntity || cursor?.filter_state?.entity;
        const canonicalId = queryValue("canonical_id") || this.fieldRequestedCanonicalId;
        const topicId = queryValue("topic_id") || this.fieldRequestedTopicId;
        const storyId = queryValue("story_id");
        const evidenceId = queryValue("evidence_id") || this.fieldRequestedEvidenceId;
        if (entity?.normalized_key || canonicalId || topicId || storyId || evidenceId) {
          await this.playlistEventMapWaitForCompleteScene({ includeMetadata: Boolean(topicId) });
          if (!isCurrentFieldRequest()) return;
        }
        if (entity?.normalized_key) {
          await this.playlistEventMapApplyEntityFilter(entity, { resume: true, focus: Boolean(this.fieldRequestedEntity) });
          if (!isCurrentFieldRequest()) return;
        }
        this.pageTitle = domain?.name || this.playlistDetail?.name || "星域";
        if (canonicalId) {
          await this.fieldOpenCanonical(canonicalId, null, { requestGuard: isCurrentFieldRequest });
          if (!isCurrentFieldRequest()) return;
        }
        if (topicId) {
          const topic = (this.playlistEventMapManifest?.topics || []).find(
            (item) => String(item.topic_id || "") === String(topicId)
          );
          if (topic) this.playlistEventMapSelectTopic(topic, { focus: true });
        }
        if (storyId) {
          this.fieldMode = "story";
          await this.fieldOpenStory(storyId, { requestGuard: isCurrentFieldRequest });
          if (!isCurrentFieldRequest()) return;
        }
        if (evidenceId) {
          await this.fieldOpenEvidence(evidenceId, { requestGuard: isCurrentFieldRequest });
          if (!isCurrentFieldRequest()) return;
        }

        try {
          const feed = await this.api(`/domains/${encodeURIComponent(domainId)}/observation/feed?limit=40`);
          if (isCurrentFieldRequest()) {
            this.domainObservationFeed = feed || emptyObservationFeed();
            this.fieldObservationFeedLoaded = true;
            this.fieldSelectAvailableFeedTab();
          }
        } catch (error) {
          if (isCurrentFieldRequest()) this.fieldObservationFeedError = error?.message || String(error);
        }
        if (!isCurrentFieldRequest()) return;
        if (["story", "verify"].includes(this.fieldMode)) {
          this.fieldObservationRailOpen = true;
          this.fieldSelectAvailableFeedTab({ preferred: this.fieldMode === "story" ? "story_updates" : "needs_review" });
        }
      } catch (error) {
        if (isCurrentFieldRequest()) throw error;
      } finally {
        if (Number(this._fieldLoadRequestToken || 0) === fieldRequestToken) {
          if (!this._playlistEventMapAbortCtrl) this.playlistEventMapLoading = false;
          this._fieldHydrating = false;
          this.fieldObservationFeedLoading = false;
        }
      }
      if (isCurrentFieldRequest()) {
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
      this._fieldLoadRequestToken = Number(this._fieldLoadRequestToken || 0) + 1;
      this._fieldLinearRequestToken = Number(this._fieldLinearRequestToken || 0) + 1;
      this._fieldSnapshotNavigationToken = Number(this._fieldSnapshotNavigationToken || 0) + 1;
      this.fieldCompleteSnapshotNavigation(this._fieldSnapshotNavigationToken);
      this._playlistEventMapSelectionToken = Number(this._playlistEventMapSelectionToken || 0) + 1;
      this._fieldEvidenceRequestToken = Number(this._fieldEvidenceRequestToken || 0) + 1;
      this._storyDetailRequestToken = Number(this._storyDetailRequestToken || 0) + 1;
      this.fieldEvidenceLoading = false;
      this.fieldLinearLoading = false;
    },

    async fieldSetMode(mode) {
      const next = FIELD_MODES.has(String(mode || "")) ? String(mode) : "now";
      this.fieldMode = next;
      if (next === "replay" && this.playlistEventMapCanPlay()) this.playlistEventMapTogglePlayback();
      if (next !== "replay" && this.playlistEventMapPlaying) this.playlistEventMapStopPlayback();
      this.fieldObservationRailTab = next === "verify" ? "needs_review" : next === "story" ? "story_updates" : this.fieldObservationRailTab;
      if (["story", "verify"].includes(next)) {
        this.fieldLinearView = false;
        this.playlistEventMapCloseSearch?.();
        this.playlistEventMapFiltersOpen = false;
        this.fieldObservationRailOpen = true;
      }
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
      if (this.playlistEventMapSelectedId) {
        this.fieldLinearView = false;
        this.playlistEventMapCloseSearch?.();
        this.playlistEventMapFiltersOpen = false;
        this.fieldObservationRailOpen = true;
        this.fieldCloseSelectedInspector();
        this.fieldSelectAvailableFeedTab();
        return;
      }
      this.fieldObservationRailOpen = !this.fieldObservationRailOpen;
      if (!this.fieldObservationRailOpen) return;
      this.fieldLinearView = false;
      this.playlistEventMapCloseSearch?.();
      this.playlistEventMapFiltersOpen = false;
      this.fieldSelectAvailableFeedTab();
    },

    fieldObservationRailVisible() {
      return Boolean(this.fieldObservationRailOpen && !this.fieldLinearView && !this.playlistEventMapSelectedId);
    },

    fieldObservationRailActionLabel() {
      if (this.playlistEventMapSelectedId && this.fieldObservationRailOpen) return "返回变化";
      return "星域变化";
    },

    fieldInspectorCloseLabel() {
      return this.fieldObservationRailOpen ? "返回变化" : "关闭";
    },

    fieldCloseSelectedInspector() {
      if (this.playlistEventMapSelectedKind === "topic") this.playlistEventMapClearTopicFocus();
      else if (this.playlistEventMapSelectedKind === "evidence") this.fieldCloseEvidence();
      else if (this.playlistEventMapSelectedId) this.playlistEventMapClearSelection();
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

    fieldCameraStateCompatible(state, {
      controller = this.playlistEventMapController(),
      snapshotId = this.playlistEventMapSnapshotId,
      windowStart = this.playlistEventMapWindowStart,
      windowEnd = this.playlistEventMapWindowEnd,
    } = {}) {
      if (Number(state?.framing_version || 0) !== FIELD_CAMERA_FRAMING_VERSION) return false;
      if (!state || String(state.snapshot_id || "") !== String(snapshotId || "")) return false;
      if (String(state.window_start || "") !== String(windowStart || "")) return false;
      if (String(state.window_end || "") !== String(windowEnd || "")) return false;
      const savedAspect = Number(state.viewport_aspect || 0);
      const currentAspect = Number(controller?.cameraState()?.viewport_aspect || 0);
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

    fieldCompleteSnapshotNavigation(navigationToken) {
      if (Number(this._fieldSnapshotNavigationToken || 0) !== Number(navigationToken)) return false;
      this._fieldSnapshotNavigationTarget = "";
      this._fieldSnapshotNavigationBaseline = "";
      this._fieldSnapshotNavigationOrigin = "";
      return true;
    },

    fieldSnapshotNavigationStillPending(
      actualSnapshotId = this.playlistEventMapSnapshotId,
      requestedSnapshotId = this.fieldRequestedSnapshotId
    ) {
      const targetSnapshotId = String(this._fieldSnapshotNavigationTarget || "");
      if (!targetSnapshotId) return false;
      const targetStillRequested = targetSnapshotId === LATEST_SNAPSHOT_NAVIGATION_TARGET
        ? String(requestedSnapshotId || "") === ""
        : String(requestedSnapshotId || "") === targetSnapshotId;
      return Boolean(
        String(actualSnapshotId || "")
        && String(actualSnapshotId || "") === String(this._fieldSnapshotNavigationOrigin || "")
        && targetStillRequested
      );
    },

    fieldCancelPendingSnapshotNavigation({ resumePolling = true } = {}) {
      if (!this.fieldSnapshotNavigationStillPending()) return false;
      const baselineSnapshotId = String(this._fieldSnapshotNavigationBaseline || "");
      this._fieldSnapshotNavigationToken = Number(this._fieldSnapshotNavigationToken || 0) + 1;
      this._abortCtrl?.("_playlistEventMapStagedAbortCtrl");
      this.fieldRequestedSnapshotId = baselineSnapshotId;
      this._fieldSnapshotNavigationTarget = "";
      this._fieldSnapshotNavigationBaseline = "";
      this._fieldSnapshotNavigationOrigin = "";
      this._syncUrl?.({ push: false });
      if (resumePolling) {
        if (baselineSnapshotId) this.playlistEventMapStopPolling?.();
        else this.playlistEventMapSchedulePoll?.({ immediate: true });
      }
      return true;
    },

    async fieldOpenFeedItem(item) {
      if (!item || !new Set(["canonical", "story"]).has(String(item.object_type || ""))) return false;
      const domainId = String(this.selectedPlaylistId || "").trim();
      const initialSnapshotId = String(this.playlistEventMapSnapshotId || "").trim();
      const initialRequestedSnapshotId = String(this.fieldRequestedSnapshotId || "");
      const targetSnapshotId = String(
        item.after_revision ? item.to_snapshot_id || "" : item.from_snapshot_id || ""
      );
      const switchesSnapshot = Boolean(targetSnapshotId && targetSnapshotId !== initialSnapshotId);
      const continuesPendingNavigation = switchesSnapshot
        && this.fieldSnapshotNavigationStillPending(initialSnapshotId, initialRequestedSnapshotId);
      const repeatsPendingTarget = continuesPendingNavigation
        && targetSnapshotId === String(this._fieldSnapshotNavigationTarget || "");
      const previousSelectionToken = Number(this._playlistEventMapSelectionToken || 0);
      const fieldSelectionToken = repeatsPendingTarget && previousSelectionToken > 0
        ? previousSelectionToken
        : previousSelectionToken + 1;
      this._playlistEventMapSelectionToken = fieldSelectionToken;
      if (!switchesSnapshot && targetSnapshotId === initialSnapshotId) {
        this.fieldCancelPendingSnapshotNavigation();
      }
      let snapshotNavigationToken = Number(this._fieldSnapshotNavigationToken || 0);
      if (switchesSnapshot) {
        this.playlistEventMapStopPolling?.();
        snapshotNavigationToken += 1;
        this._fieldSnapshotNavigationToken = snapshotNavigationToken;
        this._fieldSnapshotNavigationTarget = targetSnapshotId;
        this._fieldSnapshotNavigationBaseline = continuesPendingNavigation
          ? String(this._fieldSnapshotNavigationBaseline || "")
          : initialRequestedSnapshotId;
        this._fieldSnapshotNavigationOrigin = continuesPendingNavigation
          ? String(this._fieldSnapshotNavigationOrigin || "")
          : initialSnapshotId;
        this.fieldRequestedSnapshotId = targetSnapshotId;
        this._syncUrl({ push: false });
        await this.playlistEventMapLoadView({ silent: false, schedulePolling: false, selectionToken: fieldSelectionToken });
      }
      const expectedSnapshotId = targetSnapshotId || initialSnapshotId;
      const requestStillCurrent = () => (
        Number(this._playlistEventMapSelectionToken || 0) === fieldSelectionToken
        && Number(this._fieldSnapshotNavigationToken || 0) === snapshotNavigationToken
        && (!this.activeView || this.activeView === "field")
        && String(this.selectedPlaylistId || "").trim() === domainId
        && String(this.playlistEventMapSnapshotId || "").trim() === expectedSnapshotId
        && (!switchesSnapshot || String(this.fieldRequestedSnapshotId || "") === targetSnapshotId)
      );
      if (!requestStillCurrent()) {
        if (
          switchesSnapshot
          && Number(this._fieldSnapshotNavigationToken || 0) === snapshotNavigationToken
          && (!this.activeView || this.activeView === "field")
          && String(this.selectedPlaylistId || "").trim() === domainId
          && String(this.playlistEventMapSnapshotId || "").trim() === initialSnapshotId
          && String(this.fieldRequestedSnapshotId || "") === targetSnapshotId
        ) {
          this.fieldCancelPendingSnapshotNavigation();
        }
        return false;
      }
      if (switchesSnapshot) this.fieldCompleteSnapshotNavigation(snapshotNavigationToken);
      if (typeof this.playlistEventMapWaitForCompleteScene === "function") {
        try {
          await this.playlistEventMapWaitForCompleteScene();
        } catch (error) {
          if (requestStillCurrent()) throw error;
          return false;
        }
        if (!requestStillCurrent()) return false;
      }
      const selected = item.object_type === "canonical"
        ? await this.fieldOpenCanonical(item.object_id, item.after_revision?.point_index, {
          requestGuard: requestStillCurrent,
          selectionToken: fieldSelectionToken,
        })
        : await this.fieldOpenStory(item.object_id, {
          requestGuard: requestStillCurrent,
          selectionToken: fieldSelectionToken,
        });
      if (selected !== true || !requestStillCurrent()) return false;
      this.fieldSelectedChange = item;
      return true;
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

    async fieldOpenCanonical(canonicalId, pointIndex = null, { requestGuard = null, selectionToken = null } = {}) {
      const id = String(canonicalId || "").trim();
      const domainId = String(this.selectedPlaylistId || "").trim();
      const snapshotId = String(this.playlistEventMapSnapshotId || "").trim();
      if (
        !id
        || !domainId
        || !snapshotId
        || (requestGuard && !requestGuard())
        || (this.activeView && this.activeView !== "field")
      ) return false;
      this.fieldCancelPendingSnapshotNavigation();
      const suppliedSelectionToken = Number(selectionToken);
      const fieldSelectionToken = Number.isInteger(suppliedSelectionToken) && suppliedSelectionToken > 0
        ? suppliedSelectionToken
        : Number(this._playlistEventMapSelectionToken || 0) + 1;
      if (!(Number.isInteger(suppliedSelectionToken) && suppliedSelectionToken > 0)) {
        this._playlistEventMapSelectionToken = fieldSelectionToken;
      }
      this._playlistEventMapDetailToken = Number(this._playlistEventMapDetailToken || 0) + 1;
      this._abortCtrl?.("_playlistEventMapDetailAbortCtrl");
      const isCurrentRequest = () => (
        (!requestGuard || requestGuard())
        && Number(this._playlistEventMapSelectionToken || 0) === fieldSelectionToken
        && (!this.activeView || this.activeView === "field")
        && String(this.selectedPlaylistId || "") === domainId
        && String(this.playlistEventMapSnapshotId || "") === snapshotId
      );
      if (!isCurrentRequest()) return false;
      let index = optionalPointIndex(pointIndex);
      if (!Number.isInteger(index)) {
        try {
          const history = await this.api(
            `/domains/${encodeURIComponent(domainId)}/canonicals/${encodeURIComponent(id)}/history`
          );
          if (!isCurrentRequest()) return false;
          const revision = (history?.revisions || [])
            .slice()
            .reverse()
            .find((item) => String(item.snapshot_id) === snapshotId);
          index = optionalPointIndex(revision?.revision?.point_index);
        } catch {
          if (!isCurrentRequest()) return false;
          index = NaN;
        }
      }
      if (!Number.isInteger(index)) {
        try {
          const result = await this.api(
            `/playlists/${encodeURIComponent(domainId)}/events/map/search?snapshot_id=${encodeURIComponent(snapshotId)}&q=${encodeURIComponent(id)}&limit=10`
          );
          if (!isCurrentRequest()) return false;
          const item = (Array.isArray(result?.items) ? result.items : Array.isArray(result) ? result : []).find(
            (entry) => String(entry.id || entry.canonical_id) === id
          );
          index = optionalPointIndex(item?.point_index);
        } catch {
          if (!isCurrentRequest()) return false;
          index = NaN;
        }
      }
      if (Number.isInteger(index) && isCurrentRequest()) {
        return await this.playlistEventMapSelectCanonical(index, id, { selectionToken: fieldSelectionToken });
      }
      return false;
    },

    async fieldOpenStory(storyIdentityId, { requestGuard = null, selectionToken = null } = {}) {
      const id = String(storyIdentityId || "").trim();
      const domainId = String(this.selectedPlaylistId || "").trim();
      const snapshotId = String(this.playlistEventMapSnapshotId || "").trim();
      if (
        !id
        || !domainId
        || !snapshotId
        || (requestGuard && !requestGuard())
        || (this.activeView && this.activeView !== "field")
      ) return false;
      this.fieldCancelPendingSnapshotNavigation();
      const suppliedSelectionToken = Number(selectionToken);
      const fieldSelectionToken = Number.isInteger(suppliedSelectionToken) && suppliedSelectionToken > 0
        ? suppliedSelectionToken
        : Number(this._playlistEventMapSelectionToken || 0) + 1;
      if (!(Number.isInteger(suppliedSelectionToken) && suppliedSelectionToken > 0)) {
        this._playlistEventMapSelectionToken = fieldSelectionToken;
      }
      if (Number(this._playlistEventMapSelectionToken || 0) !== fieldSelectionToken) return false;
      this._playlistEventMapDetailToken = Number(this._playlistEventMapDetailToken || 0) + 1;
      this._abortCtrl?.("_playlistEventMapDetailAbortCtrl");
      const isCurrentRequest = () => (
        (!requestGuard || requestGuard())
        && Number(this._playlistEventMapSelectionToken || 0) === fieldSelectionToken
        && (!this.activeView || this.activeView === "field")
        && String(this.selectedPlaylistId || "") === domainId
        && String(this.playlistEventMapSnapshotId || "") === snapshotId
        && String(this.storySelectedId || "") === id
      );
      this.fieldRequestedEvidenceId = "";
      this.fieldEvidenceDetail = null;
      this.storySelectedId = id;
      const storyDetailPromise = this.loadStoryDetail(id);
      const storyRequestToken = Number(this._storyDetailRequestToken || 0);
      await storyDetailPromise;
      if (!isCurrentRequest() || Number(this._storyDetailRequestToken || 0) !== storyRequestToken) return false;
      const trajectorySnapshotId = String(this.storyTrajectory()?.snapshot_id || "");
      const revision = (this.storyDetail?.revisions || []).find(
        (item) => String(item.snapshot_id || "") === trajectorySnapshotId
      ) || this.storyDetail?.revisions?.at(-1);
      const firstCanonicalId = revision?.member_ids?.[0];
      if (firstCanonicalId) {
        const canonicalSelected = await this.fieldOpenCanonical(firstCanonicalId, null, {
          requestGuard: isCurrentRequest,
          selectionToken: fieldSelectionToken,
        });
        if (canonicalSelected === false) return false;
        if (!isCurrentRequest() || Number(this._storyDetailRequestToken || 0) !== storyRequestToken) return false;
        this.playlistEventMapSetDetailTab("story");
      }
      const trajectory = this.storyTrajectory();
      if (isCurrentRequest() && String(trajectory?.snapshot_id || "") === snapshotId) {
        this.playlistEventMapController()?.setStoryPath(trajectory);
        const trajectoryIndices = new Set(
          (trajectory.nodes || []).map((node) => optionalPointIndex(node.point_index)).filter(Number.isInteger)
        );
        if (trajectoryIndices.size) this.playlistEventMapFitIndices(trajectoryIndices);
      }
      return isCurrentRequest() && Number(this._storyDetailRequestToken || 0) === storyRequestToken;
    },

    async fieldOpenEvidence(revisionId, { requestGuard = null } = {}) {
      const id = String(revisionId || "").trim();
      const domainId = String(this.selectedPlaylistId || "");
      const snapshotId = String(this.playlistEventMapSnapshotId || "");
      if (
        !id
        || !domainId
        || !snapshotId
        || (requestGuard && !requestGuard())
        || (this.activeView && this.activeView !== "field")
      ) return;
      this.fieldCancelPendingSnapshotNavigation();
      const fieldSelectionToken = Number(this._playlistEventMapSelectionToken || 0) + 1;
      this._playlistEventMapSelectionToken = fieldSelectionToken;
      const evidenceRequestToken = Number(this._fieldEvidenceRequestToken || 0) + 1;
      this._fieldEvidenceRequestToken = evidenceRequestToken;
      const isCurrentRequest = () => (
        (!requestGuard || requestGuard())
        && Number(this._playlistEventMapSelectionToken || 0) === fieldSelectionToken
        && Number(this._fieldEvidenceRequestToken || 0) === evidenceRequestToken
        && (!this.activeView || this.activeView === "field")
        && String(this.selectedPlaylistId || "") === domainId
        && String(this.playlistEventMapSnapshotId || "") === snapshotId
        && String(this.fieldRequestedEvidenceId || "") === id
        && this.playlistEventMapSelectedKind === "evidence"
        && String(this.playlistEventMapSelectedId || "") === id
      );
      this._playlistEventMapDetailToken = Number(this._playlistEventMapDetailToken || 0) + 1;
      this._playlistEventMapTopicDetailToken = Number(this._playlistEventMapTopicDetailToken || 0) + 1;
      this._abortCtrl?.("_playlistEventMapDetailAbortCtrl");
      this._abortCtrl?.("_playlistEventMapTopicDetailAbortCtrl");
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
        const detail = await this.api(
          `/domains/${encodeURIComponent(domainId)}/evidence/${encodeURIComponent(id)}`
        );
        if (isCurrentRequest()) this.fieldEvidenceDetail = detail;
      } finally {
        if (Number(this._fieldEvidenceRequestToken || 0) === evidenceRequestToken) {
          this.fieldEvidenceLoading = false;
        }
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
      this.fieldCancelPendingSnapshotNavigation();
      this._playlistEventMapSelectionToken = Number(this._playlistEventMapSelectionToken || 0) + 1;
      this._fieldEvidenceRequestToken = Number(this._fieldEvidenceRequestToken || 0) + 1;
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

    async fieldSetPrimaryView(view) {
      const showList = String(view || "") === "list";
      this.playlistEventMapCloseSearch?.();
      this.playlistEventMapFiltersOpen = false;
      if (!showList) {
        this.fieldLinearView = false;
        if (["story", "verify"].includes(this.fieldMode)) {
          this.fieldObservationRailOpen = true;
          this.fieldSelectAvailableFeedTab({ preferred: this.fieldMode === "story" ? "story_updates" : "needs_review" });
        }
        this.$nextTick?.(() => this.playlistEventMapController?.()?.resize?.());
        return;
      }
      this.playlistEventMapStopPlayback?.();
      this.fieldLinearView = true;
      this.fieldObservationRailOpen = false;
      if (this.playlistEventMapSelectedKind === "topic") this.playlistEventMapClearTopicFocus();
      else if (this.playlistEventMapSelectedKind === "evidence") this.fieldCloseEvidence();
      else if (this.playlistEventMapSelectedId) this.playlistEventMapClearSelection();
      await this.fieldLoadLinearItems();
    },

    async fieldLoadLinearItems() {
      const domainId = String(this.selectedPlaylistId || "");
      if (!domainId) return;
      const token = Number(this._fieldLinearRequestToken || 0) + 1;
      this._fieldLinearRequestToken = token;
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
        if (this.playlistEventMapEntityFilter?.normalized_key) {
          params.set("normalized_key", String(this.playlistEventMapEntityFilter.normalized_key));
          if (this.playlistEventMapEntityFilter.entity_type) {
            params.set("entity_type", String(this.playlistEventMapEntityFilter.entity_type));
          }
        }
        const payload = await this.api(`/domains/${encodeURIComponent(domainId)}/canonicals?${params.toString()}`);
        if (Number(this._fieldLinearRequestToken || 0) !== token) return;
        this.fieldLinearItems = Array.isArray(payload?.items) ? payload.items : [];
      } catch (error) {
        if (Number(this._fieldLinearRequestToken || 0) === token) {
          this.fieldLinearItems = [];
          this.playlistEventMapError = error?.message || String(error);
        }
      } finally {
        if (Number(this._fieldLinearRequestToken || 0) === token) this.fieldLinearLoading = false;
      }
    },

    async fieldToggleLinearView() { return this.fieldSetPrimaryView(this.fieldLinearView ? "map" : "list"); },

    async fieldOpenLinearItem(item) {
      if (!item?.canonical_id) return;
      await this.fieldSetPrimaryView("map");
      await this.fieldOpenCanonical(item.canonical_id, item.point_index);
    },

    async fieldReturnToCurrentSnapshot() {
      if (!this.fieldRequestedSnapshotId) return;
      this.fieldCancelPendingSnapshotNavigation({ resumePolling: false });
      this.playlistEventMapStopPolling?.();
      const domainId = String(this.selectedPlaylistId || "").trim();
      const originSnapshotId = String(this.playlistEventMapSnapshotId || "");
      const baselineSnapshotId = String(this.fieldRequestedSnapshotId || "");
      const navigationToken = Number(this._fieldSnapshotNavigationToken || 0) + 1;
      this._fieldSnapshotNavigationToken = navigationToken;
      const selectionToken = Number(this._playlistEventMapSelectionToken || 0) + 1;
      this._playlistEventMapSelectionToken = selectionToken;
      this._fieldSnapshotNavigationTarget = LATEST_SNAPSHOT_NAVIGATION_TARGET;
      this._fieldSnapshotNavigationBaseline = baselineSnapshotId;
      this._fieldSnapshotNavigationOrigin = originSnapshotId;
      this.fieldRequestedSnapshotId = "";
      this._syncUrl({ push: false });
      const loadResult = await this.playlistEventMapLoadView({
        silent: false,
        schedulePolling: true,
        selectionToken,
        forceLatest: true,
      });
      const returnStillCurrent = (
        Number(this._fieldSnapshotNavigationToken || 0) === navigationToken
        && Number(this._playlistEventMapSelectionToken || 0) === selectionToken
        && (!this.activeView || this.activeView === "field")
        && String(this.selectedPlaylistId || "").trim() === domainId
        && String(this.fieldRequestedSnapshotId || "") === ""
      );
      if (returnStillCurrent) {
        if (loadResult?.confirmed === true) this.fieldCompleteSnapshotNavigation(navigationToken);
        else this.fieldCancelPendingSnapshotNavigation();
      }
      return loadResult?.confirmed === true;
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
          view_mode: "now",
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

    storyQueueTabs() {
      return [
        { scope: "attention", label: "值得阅读", empty: "暂时没有值得阅读的更新" },
        { scope: "followed", label: "已关注", empty: "还没有关注故事" },
        { scope: "established", label: "已形成", empty: "当前还没有已形成的故事" },
        { scope: "emerging", label: "初步线索", empty: "当前还没有初步线索" },
      ];
    },

    storyQueue(scope = this.storyScope) {
      return this.storyQueues?.[scope] || { items: [], total: 0, hasMore: false, loaded: false };
    },

    storySyncQueueState(scope = this.storyScope) {
      if (scope !== this.storyScope) return;
      const queue = this.storyQueue(scope);
      this.stories = queue.items || [];
      this.storiesTotal = Number(queue.total || 0);
      this.storiesHasMore = Boolean(queue.hasMore);
      this.storiesLoading = Boolean(queue.loading);
      this.storiesLoadingMore = Boolean(queue.loadingMore);
      this.storiesError = String(queue.error || "");
    },

    async storySetScope(scope) {
      const next = this.storyQueueTabs().some((item) => item.scope === scope) ? scope : "attention";
      if (next === this.storyScope && this.storyQueue(next).loaded) return;
      this.storyScope = next;
      this._storiesRequestToken += 1;
      this.storySyncQueueState(next);
      if (!this.storyQueue(next).loaded) await this.loadStories({ scope: next });
    },

    async storySearch() {
      await this.loadStories({ scope: this.storyScope });
    },

    async loadStories({ append = false, scope = this.storyScope } = {}) {
      const selectedScope = this.storyQueueTabs().some((item) => item.scope === scope) ? scope : "attention";
      this.storiesPreparing = true;
      this.storiesDomainMissing = false;
      this.storiesError = "";
      let domainId = "";
      try {
        domainId = await this.ensureCurrentDomain();
      } catch (error) {
        this.storiesError = error?.message || String(error);
        return;
      } finally {
        this.storiesPreparing = false;
      }
      if (!domainId) {
        this.storiesDomainMissing = true;
        return;
      }
      const queue = this.storyQueue(selectedScope);
      if (append && (queue.loading || queue.loadingMore || !queue.hasMore)) return;
      const requestToken = ++this._storiesRequestToken;
      const offset = append ? (queue.items || []).length : 0;
      const loadingQueue = {
        ...queue,
        loading: !append,
        loadingMore: append,
        error: "",
      };
      this.storyQueues = { ...this.storyQueues, [selectedScope]: loadingQueue };
      this.storySyncQueueState(selectedScope);
      try {
        const params = new URLSearchParams({
          scope: selectedScope,
          limit: String(this.storiesPageSize),
          offset: String(offset),
        });
        const query = String(this.storyQueueQueries?.[selectedScope] || "").trim();
        if (query) params.set("q", query);
        const payload = await this.api(`/domains/${encodeURIComponent(domainId)}/stories?${params}`);
        if (requestToken !== this._storiesRequestToken) return;
        const items = Array.isArray(payload?.items) ? payload.items : [];
        const nextItems = append ? [...(queue.items || []), ...items] : items;
        const total = Number.isFinite(Number(payload?.total)) ? Number(payload.total) : nextItems.length;
        this.storyQueues = {
          ...this.storyQueues,
          [selectedScope]: {
            items: nextItems,
            total,
            hasMore: typeof payload?.has_more === "boolean" ? payload.has_more : nextItems.length < total,
            loaded: true,
            loading: false,
            loadingMore: false,
          },
        };
        this.storySyncQueueState(selectedScope);
        if (!append) {
          const requested = queryValue("story_id") || this.storySelectedId;
          if (requested && (!this.storyDetail || String(this.storySelectedId) !== String(requested))) {
            await this.loadStoryDetail(requested);
          }
        }
      } catch (error) {
        if (requestToken === this._storiesRequestToken) {
          const current = this.storyQueue(selectedScope);
          this.storyQueues = {
            ...this.storyQueues,
            [selectedScope]: {
              ...current,
              loaded: true,
              loading: false,
              loadingMore: false,
              error: error?.message || String(error),
            },
          };
          this.storySyncQueueState(selectedScope);
        }
      } finally {
        if (requestToken === this._storiesRequestToken) {
          const current = this.storyQueue(selectedScope);
          this.storyQueues = {
            ...this.storyQueues,
            [selectedScope]: { ...current, loading: false, loadingMore: false },
          };
          this.storySyncQueueState(selectedScope);
        }
      }
    },

    async loadMoreStories() {
      await this.loadStories({ append: true, scope: this.storyScope });
    },

    async storyOpen(storyId) {
      const id = String(storyId || "").trim();
      if (!id) return;
      this.storySelectedSnapshotId = "";
      this.storyCloseInspector({ restoreFocus: false });
      this.storySetSnapshotUrl("");
      await this.loadStoryDetail(id, { snapshotId: "" });
      this._syncUrl?.({ push: false });
    },

    storyBackToQueue() {
      this.storySelectedId = "";
      this.storySelectedSnapshotId = "";
      this.storyDetail = null;
      this.storyCloseInspector({ restoreFocus: false });
      this.storySetSnapshotUrl("");
      this._syncUrl?.({ push: false });
    },

    async loadStoryDetail(storyId, { snapshotId } = {}) {
      const domainId = String(this.selectedPlaylistId || "");
      const id = String(storyId || "").trim();
      if (!domainId || !id) return;
      const requestToken = ++this._storyDetailRequestToken;
      const fieldSelectionToken = this.activeView === "field"
        ? Number(this._playlistEventMapSelectionToken || 0)
        : null;
      this.storySelectedId = id;
      this.storyDetailLoading = true;
      this.storyCloseInspector({ restoreFocus: false });
      let requestedSnapshot = snapshotId;
      if (requestedSnapshot === undefined) {
        requestedSnapshot = this.activeView === "field"
          ? this.fieldRequestedSnapshotId
          : (this.storySelectedSnapshotId || queryValue("snapshot_id"));
      }
      requestedSnapshot = String(requestedSnapshot || "").trim();
      try {
        const params = new URLSearchParams();
        if (requestedSnapshot) params.set("snapshot_id", requestedSnapshot);
        const payload = await this.api(
          `/domains/${encodeURIComponent(domainId)}/stories/${encodeURIComponent(id)}${params.size ? `?${params}` : ""}`
        );
        if (
          requestToken !== this._storyDetailRequestToken
          || (fieldSelectionToken !== null && Number(this._playlistEventMapSelectionToken || 0) !== fieldSelectionToken)
        ) return;
        this.storyDetail = payload;
        this.storySelectedSnapshotId = requestedSnapshot;
        this.storyFocusIndex = this.storySuggestedStartIndex();
        this.storyScheduleInitialPosition();
      } finally {
        if (requestToken === this._storyDetailRequestToken) this.storyDetailLoading = false;
      }
    },

    storySetSnapshotUrl(snapshotId) {
      if (typeof window === "undefined" || !window.location || !window.history?.replaceState) return;
      try {
        const url = new URL(window.location.href);
        if (snapshotId) url.searchParams.set("snapshot_id", String(snapshotId));
        else url.searchParams.delete("snapshot_id");
        window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
      } catch {
        // 浏览器拒绝改写历史时，当前阅读仍可继续。
      }
    },

    async storySelectHistory(snapshotId) {
      const id = String(snapshotId || "").trim();
      if (!id || !this.storySelectedId) return;
      this.storySelectedSnapshotId = id;
      this.storySetSnapshotUrl(id);
      await this.loadStoryDetail(this.storySelectedId, { snapshotId: id });
    },

    async storyReturnCurrent() {
      if (!this.storySelectedId) return;
      this.storySelectedSnapshotId = "";
      this.storySetSnapshotUrl("");
      await this.loadStoryDetail(this.storySelectedId, { snapshotId: "" });
    },

    storyIsHistorical() {
      if (typeof this.storyDetail?.is_historical === "boolean") return this.storyDetail.is_historical;
      if (typeof this.storyDetail?.can_mark_read === "boolean") return !this.storyDetail.can_mark_read;
      return Boolean(this.storySelectedSnapshotId);
    },

    storySelectedRevision() {
      const selected = this.storyDetail?.selected_revision;
      if (selected?.revision) return { ...selected.revision, snapshot_id: selected.snapshot_id || selected.revision.snapshot_id };
      if (selected) return selected;
      const snapshotId = this.storyTrajectory()?.snapshot_id;
      return (this.storyDetail?.revisions || []).find((item) => String(item.snapshot_id) === String(snapshotId))
        || this.storyDetail?.revisions?.at(-1)
        || null;
    },

    storyTrajectory() {
      return this.storyDetail?.trajectory || this.storyDetail?.current_trajectory || { snapshot_id: null, nodes: [], edges: [] };
    },

    storySortedNodes() {
      return (this.storyTrajectory()?.nodes || [])
        .map((node, originalIndex) => ({ ...node, _storyOriginalIndex: originalIndex }))
        .sort((left, right) => {
          const leftTime = Date.parse(left.event_time_start || left.start_at || left.occurred_at || "");
          const rightTime = Date.parse(right.event_time_start || right.start_at || right.occurred_at || "");
          if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) return leftTime - rightTime;
          if (Number.isFinite(leftTime) !== Number.isFinite(rightTime)) return Number.isFinite(leftTime) ? -1 : 1;
          return left._storyOriginalIndex - right._storyOriginalIndex;
        })
        .map((node, index) => ({ ...node, _storyIndex: index }));
    },

    storyTimelineGroups() {
      const groups = [];
      for (const node of this.storySortedNodes()) {
        const raw = String(node.event_time_start || node.start_at || node.occurred_at || "");
        const key = /^\d{4}-\d{2}-\d{2}/.test(raw) ? raw.slice(0, 10) : "unknown";
        let group = groups.at(-1);
        if (!group || group.key !== key) {
          group = { key, label: key === "unknown" ? "日期待确认" : key.replaceAll("-", "/"), nodes: [] };
          groups.push(group);
        }
        group.nodes.push(node);
      }
      return groups;
    },

    storyIncomingEdges(node) {
      const id = String(node?.canonical_id || node?.id || "");
      return (this.storyTrajectory()?.edges || []).filter(
        (edge) => String(edge.target_canonical_id || edge.target_id || "") === id
      );
    },

    storyRelationLabel(value) {
      const labels = {
        continuation: "阶段推进",
        causes: "因果承接",
        response: "事件响应",
        corrects: "事实纠正",
      };
      return labels[String(value || "")] || "关系待核验";
    },

    storyEdgeSourceTitle(edge) {
      if (edge?.source_title) return edge.source_title;
      const id = String(edge?.source_canonical_id || edge?.source_id || "");
      return this.storyTrajectory()?.nodes?.find((node) => String(node.canonical_id || node.id) === id)?.title || "较早事件";
    },

    storyEdgeTargetTitle(edge) {
      if (edge?.target_title) return edge.target_title;
      const id = String(edge?.target_canonical_id || edge?.target_id || "");
      return this.storyTrajectory()?.nodes?.find((node) => String(node.canonical_id || node.id) === id)?.title || "后续事件";
    },

    storyDateTimeLabel(value) {
      const raw = String(value || "");
      if (!raw) return "时间待确认";
      const normalized = raw.slice(0, 16).replace("T", " ");
      const visible = normalized.length > 10 && !normalized.endsWith(" 00:00") ? normalized : normalized.slice(0, 10);
      return visible.replace(/^(\d{4})-(\d{2})-(\d{2})/, "$1/$2/$3");
    },

    storyObservedDateTimeLabel(value) {
      return localDateTimeLabel(value) || "时间待确认";
    },

    storyEventTimeLabel(node) {
      const start = node?.event_time_start || node?.start_at || node?.occurred_at;
      const end = node?.event_time_end || node?.end_at;
      const precision = { exact: "精确时间", day: "日期", month: "月份", year: "年份", inferred: "推定" }[node?.time_precision] || "";
      const range = end && String(end) !== String(start)
        ? `${this.storyDateTimeLabel(start)} 至 ${this.storyDateTimeLabel(end)}`
        : this.storyDateTimeLabel(start);
      return precision ? `${range} · ${precision}` : range;
    },

    storyDateSpan() {
      const revision = this.storySelectedRevision() || {};
      const nodes = this.storySortedNodes();
      const start = revision.event_time_start || nodes[0]?.event_time_start || nodes[0]?.occurred_at;
      const end = revision.event_time_end || nodes.at(-1)?.event_time_end || nodes.at(-1)?.event_time_start || nodes.at(-1)?.occurred_at;
      if (!start && !end) return "故事时间待确认";
      return `${this.storyDateTimeLabel(start)} 至 ${this.storyDateTimeLabel(end || start)}`;
    },

    storyMaturityLabel(value) {
      return String(value || "") === "established" ? "已形成" : "初步线索";
    },

    storyLatestNode() {
      return this.storySortedNodes().at(-1) || null;
    },

    storyHeaderProgressTitle() {
      return this.storyLatestNode()?.title || this.storyDetail?.latest_progress?.title || this.storySelectedRevision()?.title || "尚无事件进展";
    },

    storyHeaderProgressSummary() {
      return this.storyLatestNode()?.summary || this.storyDetail?.latest_progress?.summary || this.storySelectedRevision()?.summary || "";
    },

    storyStableTitle(item = this.storyDetail) {
      return item?.stable_title || item?.title || this.storySelectedRevision()?.title || "故事";
    },

    storyDirectoryProgress(item) {
      return item?.latest_progress?.title || item?.latest_progress_title || item?.current_title || item?.summary || "等待新的事件进展";
    },

    storyMaterialChangeLabel(item) {
      if (item?.baseline_suggestion) return "推荐阅读";
      const types = item?.material_change_types || item?.change_types || (item?.last_material_change_type ? [item.last_material_change_type] : []);
      const labels = {
        story_added: "新故事",
        story_members_changed: "事件更新",
        story_relations_changed: "关系更新",
        story_evidence_changed: "来源更新",
        story_correction_added: "事实纠正",
        story_maturity_changed: "成熟度更新",
      };
      const output = [...new Set(types.map((type) => labels[type]).filter(Boolean))];
      return output.join(" · ") || (item?.unread ? "有新进展" : "已读");
    },

    storySupportRecordCount() {
      const detailCount = this.storyDetail?.support_record_count ?? this.storyDetail?.counts?.support_records;
      if (Number.isFinite(Number(detailCount))) return Number(detailCount);
      return (this.storyTrajectory()?.edges || []).reduce(
        (total, edge) => total + Number(edge.support_record_count ?? edge.evidence_revision_ids?.length ?? 0),
        0
      );
    },

    storyReadingUpdateItems() {
      const update = this.storyDetail?.reading_update || {};
      if (Array.isArray(update.items)) return update.items.map((item) => typeof item === "string" ? item : item.label || item.description).filter(Boolean);
      if (update.returned_to_previous_state) {
        return ["期间发生过实质变化，当前故事结构已恢复到上次阅读时的状态"];
      }
      const output = [];
      const eventLabel = (event) => event?.title || event?.label || "一个事件";
      for (const event of update.events_added || update.members_added || []) output.push(`新增事件「${eventLabel(event)}」`);
      for (const event of update.events_removed || update.members_removed || []) output.push(`移除事件「${eventLabel(event)}」`);
      const relationLabel = (edge) => `${edge?.relation_label || this.storyRelationLabel(edge?.relation_type)}：${edge?.source_title || "源事件"} → ${edge?.target_title || "目标事件"}`;
      for (const edge of update.relations_added || update.edges_added || []) output.push(`新增${relationLabel(edge)}`);
      for (const edge of update.relations_removed || update.edges_removed || []) output.push(`移除${relationLabel(edge)}`);
      for (const edge of update.relations_changed || []) {
        output.push(`调整「${edge?.relation_label || this.storyRelationLabel(edge?.relation_type)}」依据：${edge?.source_title || "源事件"} → ${edge?.target_title || "目标事件"}`);
      }
      if (update.member_order_changed) output.push("事件发生顺序已调整");
      const supportAdded = update.support_records_added_count ?? update.support_records_added ?? update.evidence_added;
      const supportRemoved = update.support_records_removed_count ?? update.support_records_removed ?? update.evidence_removed;
      if (Array.isArray(supportAdded) ? supportAdded.length : Number(supportAdded || 0)) output.push(`新增 ${Array.isArray(supportAdded) ? supportAdded.length : supportAdded} 份支持记录`);
      if (Array.isArray(supportRemoved) ? supportRemoved.length : Number(supportRemoved || 0)) output.push(`移除 ${Array.isArray(supportRemoved) ? supportRemoved.length : supportRemoved} 份支持记录`);
      if (update.support_assignments_changed) output.push("支持记录与关系的对应已调整");
      if (update.maturity_change || update.maturity_changed) {
        const maturity = update.maturity_change?.to || update.maturity_change?.after || update.maturity_to || update.maturity;
        output.push(`故事成熟度更新为「${this.storyMaturityLabel(maturity)}」`);
      }
      if (update.correction_added || update.fact_corrected) output.push("新增事实纠正");
      if ((update.change_types || []).includes("story_correction_added") && !output.includes("新增事实纠正")) output.push("新增事实纠正");
      return output;
    },

    storyHasReadingUpdate() {
      const update = this.storyDetail?.reading_update;
      if (!update || update.baseline || update.is_first_read) return false;
      if (update.has_material_changes === false || update.has_material_change === false) return false;
      return this.storyReadingUpdateItems().length > 0;
    },

    storyCanMarkRead() {
      if (this.storyIsHistorical() || this.storyDetail?.can_mark_read === false) return false;
      if (this.storyDetail?.unread || this.storyHasReadingUpdate()) return true;
      return this.storyDetail?.can_mark_read === true && !this.storyDetail?.last_read_at;
    },

    storySuggestedStartIndex() {
      const update = this.storyDetail?.reading_update || {};
      const nodes = this.storySortedNodes();
      const supplied = Number(update.first_new_position ?? update.first_added_position);
      if (this.storyHasReadingUpdate() && Number.isInteger(supplied) && supplied >= 0) return Math.min(supplied, Math.max(0, nodes.length - 1));
      const added = update.events_added || update.members_added || [];
      if (this.storyHasReadingUpdate() && added.length) {
        const ids = new Set(added.map((item) => String(item?.canonical_id || item?.id || item)));
        const index = nodes.findIndex((node) => ids.has(String(node.canonical_id || node.id)));
        if (index >= 0) return index;
      }
      const lastPosition = Number(this.storyDetail?.last_position);
      if (!update.baseline && Number.isInteger(lastPosition) && lastPosition >= 0) return Math.min(lastPosition, Math.max(0, nodes.length - 1));
      return 0;
    },

    storyScheduleInitialPosition() {
      const run = () => this.storyScrollToIndex(this.storyFocusIndex, { behavior: "auto", focus: false });
      if (typeof this.$nextTick === "function") this.$nextTick(run);
      else if (typeof window !== "undefined" && typeof window.setTimeout === "function") window.setTimeout(run, 0);
    },

    storyScrollToIndex(index, { behavior = "smooth", focus = true } = {}) {
      const normalized = Math.max(0, Number(index || 0));
      this.storyFocusIndex = normalized;
      if (typeof document === "undefined") return;
      const element = document.getElementById(`story-event-${normalized}`);
      element?.scrollIntoView?.({ block: "center", behavior });
      if (focus) element?.focus?.({ preventScroll: true });
    },

    storyContinueFromUpdate() {
      this.storyScrollToIndex(this.storySuggestedStartIndex());
    },

    storyJumpLatest() {
      this.storyScrollToIndex(Math.max(0, this.storySortedNodes().length - 1));
    },

    async storyUpdateReadState(values, { reloadDetail = false } = {}) {
      const domainId = String(this.selectedPlaylistId || "");
      const id = String(this.storySelectedId || "");
      if (!domainId || !id) return;
      const readState = await this.api(`/domains/${encodeURIComponent(domainId)}/stories/${encodeURIComponent(id)}/read-state`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values || {}),
      });
      if (String(this.selectedPlaylistId || "") !== domainId || String(this.storySelectedId || "") !== id) {
        return readState;
      }
      if (this.storyDetail) {
        if (Object.prototype.hasOwnProperty.call(readState || {}, "followed")) this.storyDetail.followed = Boolean(readState.followed);
        else if (Object.prototype.hasOwnProperty.call(values || {}, "followed")) this.storyDetail.followed = Boolean(values.followed);
        if (Object.prototype.hasOwnProperty.call(readState || {}, "last_position")) this.storyDetail.last_position = readState.last_position;
        else if (Number.isInteger(Number(values?.position))) this.storyDetail.last_position = Number(values.position);
        if (Object.prototype.hasOwnProperty.call(readState || {}, "last_read_at")) this.storyDetail.last_read_at = readState.last_read_at;
        if (Object.prototype.hasOwnProperty.call(readState || {}, "last_read_snapshot_id")) this.storyDetail.last_read_snapshot_id = readState.last_read_snapshot_id;
        if (values?.mark_read) this.storyDetail.unread = false;
      }
      for (const queue of Object.values(this.storyQueues || {})) {
        const row = (queue.items || []).find((item) => String(item.story_identity_id) === id);
        if (!row) continue;
        if (Object.prototype.hasOwnProperty.call(values || {}, "followed")) row.followed = Boolean(values.followed);
        if (values?.mark_read) row.unread = false;
      }
      this.storySyncQueueState();
      if (reloadDetail) await this.loadStoryDetail(id, { snapshotId: this.storySelectedSnapshotId });
      return readState;
    },

    async storyMarkCurrentUpdateRead() {
      if (this.storyIsHistorical()) return;
      const currentQueue = this.storyQueue(this.storyScope);
      const preserveRecommendation = this.storyScope === "attention" && (currentQueue.items || []).some(
        (item) => Boolean(item?.baseline_suggestion || item?.recommended)
      );
      await this.storyUpdateReadState({
        mark_read: true,
        snapshot_id: this.storyTrajectory()?.snapshot_id || null,
      }, { reloadDetail: !preserveRecommendation });
      if (!preserveRecommendation) await this.loadStories({ scope: this.storyScope });
    },

    async storyToggleFollowed() {
      await this.storyUpdateReadState({ followed: !this.storyDetail?.followed });
    },

    async storyOpenEventInspector(node, position, event) {
      const requestToken = ++this._storyInspectorRequestToken;
      this.storyInspectorOpen = true;
      this.storyInspectorMode = "event";
      this.storyInspectorItem = node;
      this.storyInspectorEventDetail = null;
      this.storyInspectorSupport = null;
      this.storyInspectorError = "";
      this._storyInspectorReturnFocus = event?.currentTarget || null;
      const domainId = String(this.selectedPlaylistId || "");
      const storyId = String(this.storySelectedId || "");
      const canonicalId = String(node?.canonical_id || node?.id || "");
      const snapshotId = String(this.storyTrajectory()?.snapshot_id || "");
      if (!domainId || !storyId || !canonicalId || !snapshotId) {
        this.storyInspectorError = "当前事件没有可读取的来源详情。";
        this.storyInspectorLoading = false;
        return;
      }
      this.storyInspectorLoading = true;
      try {
        await this.storyUpdateReadState({ position: Number(position) });
      } catch (error) {
        if (requestToken === this._storyInspectorRequestToken) {
          this.globalStatus = `error: 阅读位置保存失败：${error?.message || String(error)}`;
        }
      }
      if (
        requestToken !== this._storyInspectorRequestToken
        || !this.storyInspectorOpen
        || this.storyInspectorMode !== "event"
        || String(this.selectedPlaylistId || "") !== domainId
        || String(this.storySelectedId || "") !== storyId
      ) return;
      try {
        const params = new URLSearchParams({ snapshot_id: snapshotId });
        const payload = await this.api(
          `/playlists/${encodeURIComponent(domainId)}/events/map/canonical/${encodeURIComponent(canonicalId)}?${params}`
        );
        if (
          requestToken === this._storyInspectorRequestToken
          && this.storyInspectorOpen
          && this.storyInspectorMode === "event"
          && String(this.selectedPlaylistId || "") === domainId
          && String(this.storySelectedId || "") === storyId
        ) {
          this.storyInspectorEventDetail = payload;
        }
      } catch (error) {
        if (requestToken === this._storyInspectorRequestToken) this.storyInspectorError = error?.message || String(error);
      } finally {
        if (requestToken === this._storyInspectorRequestToken) this.storyInspectorLoading = false;
      }
    },

    async storyOpenEdgeInspector(edge, event) {
      const requestToken = ++this._storyInspectorRequestToken;
      this.storyInspectorOpen = true;
      this.storyInspectorMode = "relation";
      this.storyInspectorItem = edge;
      this.storyInspectorEventDetail = null;
      this.storyInspectorSupport = null;
      this.storyInspectorError = "";
      this._storyInspectorReturnFocus = event?.currentTarget || null;
      const domainId = String(this.selectedPlaylistId || "");
      const storyId = String(this.storySelectedId || "");
      const edgeId = String(edge?.edge_id || "");
      const snapshotId = String(this.storyTrajectory()?.snapshot_id || "");
      if (!domainId || !storyId || !edgeId || !snapshotId) {
        this.storyInspectorError = "当前关系没有可读取的支持记录。";
        this.storyInspectorLoading = false;
        return;
      }
      this.storyInspectorLoading = true;
      try {
        const params = new URLSearchParams({ snapshot_id: snapshotId });
        const payload = await this.api(
          `/domains/${encodeURIComponent(domainId)}/stories/${encodeURIComponent(storyId)}/edges/${encodeURIComponent(edgeId)}/support?${params}`
        );
        if (
          requestToken === this._storyInspectorRequestToken
          && this.storyInspectorOpen
          && this.storyInspectorMode === "relation"
          && String(this.selectedPlaylistId || "") === domainId
          && String(this.storySelectedId || "") === storyId
        ) this.storyInspectorSupport = payload;
      } catch (error) {
        if (requestToken === this._storyInspectorRequestToken) this.storyInspectorError = error?.message || String(error);
      } finally {
        if (requestToken === this._storyInspectorRequestToken) this.storyInspectorLoading = false;
      }
    },

    storyCloseInspector({ restoreFocus = true } = {}) {
      this._storyInspectorRequestToken += 1;
      this.storyInspectorOpen = false;
      this.storyInspectorMode = "";
      this.storyInspectorItem = null;
      this.storyInspectorEventDetail = null;
      this.storyInspectorSupport = null;
      this.storyInspectorLoading = false;
      this.storyInspectorError = "";
      const target = this._storyInspectorReturnFocus;
      this._storyInspectorReturnFocus = null;
      if (restoreFocus) target?.focus?.();
    },

    storySupportRecords() {
      return this.storyInspectorSupport?.support_records || this.storyInspectorSupport?.records || [];
    },

    storyRelationExplanation() {
      return this.storyInspectorSupport?.edge?.explanation || this.storyInspectorItem?.explanation || {};
    },

    storyEventDetail() {
      return this.storyInspectorEventDetail || this.storyInspectorItem || {};
    },

    storyEventSourceRecords() {
      return this.storyInspectorEventDetail?.members || this.storyInspectorItem?.source_records || this.storyInspectorItem?.sources || [];
    },

    storyEventEvidence() {
      return this.storyInspectorEventDetail?.evidence || [];
    },

    storySupportStatusLabel(record) {
      if (this.storySupportVersionReplaced(record)) return "来源版本已替换";
      const evidence = record?.evidence || [];
      const verified = record?.verified ?? (evidence.length ? evidence.every((item) => item?.verified === true) : null);
      if (verified === true) return "已验证";
      if (verified === false) return "未验证";
      return "验证状态未知";
    },

    storySupportVersionReplaced(record) {
      return Boolean(record?.source_version_replaced || ["replaced", "superseded"].includes(record?.source_version_status || record?.version_status));
    },

    storySupportVersionWarning(record) {
      const status = record?.source_version_status || record?.version_status;
      if (["replaced", "superseded"].includes(status)) return "来源版本已替换，内容可能与识别时不同。";
      if (status === "unavailable") return "识别时使用的来源版本当前不可用。";
      if (status === "unknown") return "无法确认识别时使用的来源版本。";
      return "";
    },

    storySupportPlaybackPosition(record) {
      const direct = record?.playback_position_seconds;
      if (direct !== null && direct !== undefined) return Number(direct);
      const evidence = (record?.evidence || []).find((item) =>
        item?.playback_position_seconds !== null && item?.playback_position_seconds !== undefined
        || item?.playback_start_sec !== null && item?.playback_start_sec !== undefined
        || item?.playback_start_seconds !== null && item?.playback_start_seconds !== undefined
      );
      const value = evidence?.playback_position_seconds ?? evidence?.playback_start_sec ?? evidence?.playback_start_seconds;
      return value === null || value === undefined ? null : Number(value);
    },

    storySupportExcerpt(record) {
      if (record?.excerpt) return record.excerpt;
      const excerpts = (record?.evidence || []).map((item) => item?.text || item?.evidence_text || item?.excerpt).filter(Boolean);
      return excerpts[0] || record?.summary || "该记录没有可展示的摘录。";
    },

    storySupportSourceLabel(record) {
      return record?.source_title || record?.title || record?.media_name || record?.source?.title || "来源记录";
    },

    storyOpenSupportRecord(record) {
      const revisionId = record?.evidence_revision_id || record?.record_revision_id || record?.revision_id || record?.id;
      if (revisionId) return this.openEvidenceInField(revisionId);
      const videoId = record?.video_id || record?.source_record_id || record?.source?.video_id;
      if (videoId) this.fieldOpenSourceRecord(videoId);
    },

    storyRevisionDeltaLabel(revision) {
      const delta = revision?.delta || {};
      if (delta.baseline) return "首次识别到这条故事";
      const added = (delta.members_added || []).length;
      const removed = (delta.members_removed || []).length;
      const edgeAdded = (delta.edges_added || []).length;
      const edgeRemoved = (delta.edges_removed || []).length;
      const evidenceAdded = (delta.evidence_added || []).length;
      const evidenceRemoved = (delta.evidence_removed || []).length;
      const parts = [];
      if (added) parts.push(`新增 ${added} 个事件`);
      if (removed) parts.push(`移除 ${removed} 个事件`);
      if (delta.member_order_changed) parts.push("事件顺序调整");
      if (edgeAdded) parts.push(`新增 ${edgeAdded} 条关系`);
      if (edgeRemoved) parts.push(`移除 ${edgeRemoved} 条关系`);
      if (delta.relation_details_changed) parts.push("关系依据调整");
      if (evidenceAdded) parts.push(`新增 ${evidenceAdded} 份支持记录`);
      if (evidenceRemoved) parts.push(`移除 ${evidenceRemoved} 份支持记录`);
      if (delta.maturity_changed) parts.push("成熟度变化");
      return parts.join(" · ") || "本次识别未发现实质变化";
    },

    storyRevisionDeltaExplanation(revision) {
      if (revision?.delta?.baseline) return "系统首次把这些事件识别为同一条持续发展的故事。";
      const label = this.storyRevisionDeltaLabel(revision);
      if (label === "本次识别未发现实质变化") return "系统再次复核了这条故事，但事实结构没有变化。";
      return `与上一次实质变化相比：${label.replaceAll(" · ", "；")}。`;
    },

    storyRevisionObservedAtLabel(revision) {
      return localDateTimeLabel(revision?.observed_at || revision?.changed_at);
    },

    storyMaterialHistoryLabel(change) {
      if (change?.label || change?.description) return change.label || change.description;
      const labels = {
        story_added: "故事首次形成",
        story_members_changed: "事件成员发生变化",
        story_relations_changed: "事件关系发生变化",
        story_evidence_changed: "支持记录发生变化",
        story_correction_added: "新增事实纠正",
        story_maturity_changed: "故事成熟度发生变化",
      };
      if ((change?.change_types || []).includes("story_added")) {
        const count = (change?.events_added || []).length;
        return count ? `故事首次形成（${count} 个事件）` : "故事首次形成";
      }
      const delta = change?.delta || change || {};
      const parts = [];
      for (const event of delta.events_added || []) parts.push(`新增「${event?.title || "事件"}」`);
      for (const event of delta.events_removed || []) parts.push(`移除「${event?.title || "事件"}」`);
      for (const edge of delta.relations_added || []) parts.push(`新增${edge?.relation_label || this.storyRelationLabel(edge?.relation_type)}：${edge?.source_title || "源事件"} → ${edge?.target_title || "目标事件"}`);
      for (const edge of delta.relations_removed || []) parts.push(`移除${edge?.relation_label || this.storyRelationLabel(edge?.relation_type)}：${edge?.source_title || "源事件"} → ${edge?.target_title || "目标事件"}`);
      const typeLabels = (change?.change_types || []).map((type) => labels[type]).filter(Boolean);
      return parts.join("；") || [...new Set(typeLabels)].join(" · ") || labels[change?.change_type] || this.storyRevisionDeltaLabel(change);
    },

    storyBriefLabel(reference) {
      const granularity = { day: "日简报", week: "周简报", month: "月简报" }[reference?.granularity] || "相关简报";
      return `${granularity} · ${String(reference?.period_start || reference?.event_time_start || "").slice(0, 10) || "日期待确认"}`;
    },

    async storyOpenCanonical(canonicalId, position) {
      const id = String(canonicalId || "").trim();
      if (!id) return;
      if (Number.isInteger(Number(position))) await this.storyUpdateReadState({ position: Number(position) });
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
      this.playbackContentTab = "brief";
      if (reference.period_start) this.playlistSelectedDate = String(reference.period_start);
      this.switchView("playlist");
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
        if (this.libraryScope === "domain") {
          const allowed = new Set(this.libraryDomainMediaIds);
          this.videoMediaIds = (this.videoMediaIds || []).filter((id) => allowed.has(String(id)));
        }
        await this.loadVideos();
      }
    },

    async setLibraryTab(tab) {
      this.libraryTab = LIBRARY_TABS.has(String(tab)) ? String(tab) : "sources";
      this._syncUrl({ push: false });
      await this.loadLibrary();
    },

    async setLibraryScope(scope) {
      const nextScope = scope === "global" ? "global" : "domain";
      if (nextScope !== this.libraryScope) {
        this.videoMediaIds = [];
        this.videoMediaDraftIds = [];
      }
      this.libraryScope = nextScope;
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

    async loadDomainSettings() {
      const domainId = await this.ensureCurrentDomain();
      if (!domainId) return;
      const domain = this.currentDomain() || {};
      const previousDetail = String(this.playlistDetail?.id || "") === String(domainId)
        ? this.playlistDetail
        : {};
      this.playlistDetail = {
        ...previousDetail,
        ...domain,
        id: String(domainId),
        name: String(domain?.name || previousDetail?.name || ""),
        description: String(domain?.description || previousDetail?.description || ""),
        observation_enabled: domain?.observation_enabled !== false,
        brief_granularity: String(domain?.brief_granularity || previousDetail?.brief_granularity || "day"),
      };
      this.domainSettingsNameDraft = this.playlistDetail.name;
      this.domainSettingsDescriptionDraft = this.playlistDetail.description;
      this.briefGenerationGranularityDraft = this.playlistDetail.brief_granularity;
      this.briefGenerationPromptDraft = String(this.playlistDetail.brief_prompt || "");
      this.briefGenerationDraftTouched = false;
      this.domainDeleteConfirmDraft = "";
      this.domainDangerOpen = false;
      this.domainDeletionImpact = null;
      this.domainDeletionImpactLoading = false;
      this.domainDeletionImpactError = "";
      this.domainObservationLoading = true;
      this.domainObservationError = "";
      this.domainObservation = null;
      const requestToken = ++this._domainSettingsRequestToken;
      await this.loadDomainSettingsObservation(domainId, requestToken);
    },

    async loadDomainSettingsObservation(domainId = String(this.selectedPlaylistId || ""), requestToken = this._domainSettingsRequestToken) {
      if (!domainId || (this.domainObservationLoading && this.domainObservation)) return;
      this.domainObservationLoading = true;
      this.domainObservationError = "";
      try {
        const observation = await this.api(`/domains/${encodeURIComponent(domainId)}/observation`);
        if (requestToken !== this._domainSettingsRequestToken || String(this.selectedPlaylistId || "") !== String(domainId)) return;
        this.domainObservation = observation;
        const domain = observation?.domain || {};
        this.playlistDetail = {
          ...this.playlistDetail,
          brief_granularity: String(domain.brief_granularity || this.playlistDetail?.brief_granularity || "day"),
          brief_prompt: domain.brief_prompt ?? this.playlistDetail?.brief_prompt ?? null,
        };
        if (!this.briefGenerationDraftTouched) {
          this.briefGenerationGranularityDraft = this.playlistDetail.brief_granularity;
          this.briefGenerationPromptDraft = String(this.playlistDetail.brief_prompt || "");
        }
      } catch (error) {
        if (requestToken === this._domainSettingsRequestToken) {
          this.domainObservationError = String(error?.message || error || "分析进度加载失败");
        }
      } finally {
        if (requestToken === this._domainSettingsRequestToken) this.domainObservationLoading = false;
      }
    },

    async toggleDomainDanger() {
      this.domainDangerOpen = !this.domainDangerOpen;
      if (this.domainDangerOpen && !this.domainDeletionImpact && !this.domainDeletionImpactLoading) {
        await this.loadDomainDeletionImpact();
      }
    },

    async loadDomainDeletionImpact() {
      const domainId = String(this.selectedPlaylistId || "");
      if (!domainId || this.domainDeletionImpactLoading) return;
      this.domainDeletionImpactLoading = true;
      this.domainDeletionImpactError = "";
      const requestToken = this._domainSettingsRequestToken;
      try {
        const impact = await this.api(`/domains/${encodeURIComponent(domainId)}/deletion-impact`);
        if (requestToken !== this._domainSettingsRequestToken || String(this.selectedPlaylistId || "") !== domainId) return;
        this.domainDeletionImpact = impact;
      } catch (error) {
        if (requestToken === this._domainSettingsRequestToken) {
          this.domainDeletionImpactError = String(error?.message || error || "删除影响加载失败");
        }
      } finally {
        if (requestToken === this._domainSettingsRequestToken) this.domainDeletionImpactLoading = false;
      }
    },

    domainObservationEnabled() {
      if (this.playlistDetail && Object.prototype.hasOwnProperty.call(this.playlistDetail, "observation_enabled")) {
        return this.playlistDetail.observation_enabled !== false;
      }
      return this.currentDomain()?.observation_enabled !== false;
    },

    domainCoverageRatio(key) {
      const item = this.domainObservation?.coverage?.[key];
      if (!item) return "—";
      return `${this.formatInteger(item.numerator || 0)} / ${this.formatInteger(item.denominator || 0)}`;
    },

    domainCoveragePercent(key) {
      const item = this.domainObservation?.coverage?.[key];
      const numerator = Number(item?.numerator || 0);
      const denominator = Number(item?.denominator || 0);
      if (denominator <= 0) return 0;
      return Math.max(0, Math.min(100, Math.round(numerator / denominator * 100)));
    },

    domainCoverageDetail(key, detailKey) {
      const value = Number(this.domainObservation?.coverage?.[key]?.details?.[detailKey] || 0);
      return this.formatInteger(Number.isFinite(value) ? value : 0);
    },

    domainEventExtractionSpecLabel() {
      const details = this.domainObservation?.coverage?.event_extraction?.details || {};
      const promptVersion = String(details.prompt_version || "").trim();
      const versionMatch = promptVersion.match(/(?:^|_)v(\d+)(?:_|:|$)/i);
      const versionLabel = versionMatch ? `v${versionMatch[1]}` : "当前提示词";
      const model = String(details.model || "").trim();
      return model ? `${versionLabel} · ${model}` : versionLabel;
    },

    async setDomainObservationEnabled(enabled) {
      const domainId = String(this.selectedPlaylistId || "");
      if (!domainId || this.domainObservationSaving) return;
      this.domainObservationSaving = true;
      try {
        const result = await this.api(`/domains/${encodeURIComponent(domainId)}/observation`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: Boolean(enabled) }),
        });
        const nextEnabled = result?.observation_enabled !== false;
        if (this.playlistDetail) this.playlistDetail.observation_enabled = nextEnabled;
        if (this.domainObservation?.domain) this.domainObservation.domain.observation_enabled = nextEnabled;
        const domain = this.currentDomain();
        if (domain) domain.observation_enabled = nextEnabled;
        this.toastSuccess(nextEnabled ? "观测已启用，正在补齐停用期间的事件分析" : "观测已停用，后续只归档匹配的来源记录");
      } finally {
        this.domainObservationSaving = false;
      }
    },

    async saveDomainSettingsIdentity() {
      const domainId = String(this.selectedPlaylistId || "");
      const name = String(this.domainSettingsNameDraft || "").trim();
      if (!domainId || !name || this.domainSettingsSaving) return;
      this.domainSettingsSaving = true;
      try {
        const result = await this.api(`/domains/${encodeURIComponent(domainId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name,
            description: String(this.domainSettingsDescriptionDraft || "").trim(),
          }),
        });
        this.playlistDetail = { ...this.playlistDetail, ...result };
        const domain = this.currentDomain();
        if (domain) Object.assign(domain, result);
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
        this.closeDomainSwitcherMenu({ restoreFocus: false });
        this.playlistDetail = null;
        this.selectedPlaylistId = null;
        this.playlistPageId = null;
        try {
          localStorage.removeItem(this.v2LastDomainKey);
        } catch {
          // 本地存储不可用不影响删除后的域恢复。
        }
        this.switchView("domains", { refresh: false });
        this.toastSuccess("观测域已删除；共享资料已保留");
        this.domains = [];
        try {
          await this.loadDomains({ force: true });
          const nextDomain = this.domains.find((domain) => domain?.snapshot?.status === "ready") || this.domains[0];
          const nextId = String(nextDomain?.id || "");
          if (!nextId) return;
          const selected = await this.selectDomain(nextId);
          if (selected && !this.domainSwitcherError) {
            this.switchView("field");
            return;
          }
          const message = this.domainSwitcherError || "下一个观测域暂时无法打开";
          this.globalStatus = `error: 观测域已删除，但${message}`;
          this.toastError?.(`观测域已删除，但${message}`);
        } catch (error) {
          const message = error?.message || String(error);
          this.domainSwitcherError = message;
          this.domainSwitcherRetryMode = "domains";
          this.globalStatus = `error: 观测域已删除，但目录恢复失败：${message}`;
          this.toastError?.(`观测域已删除，但目录恢复失败：${message}`);
        }
      } finally {
        this.domainDeleteSubmitting = false;
      }
    },

    async saveBriefGenerationSettings() {
      const domainId = String(this.selectedPlaylistId || "");
      if (!domainId || this.briefGenerationSaving) return;
      this.briefGenerationSaving = true;
      try {
        const prompt = String(this.briefGenerationPromptDraft || "").trim() || null;
        const result = await this.api(`/playlists/${encodeURIComponent(domainId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            brief_granularity: this.briefGenerationGranularityDraft,
            brief_prompt: prompt,
          }),
        });
        this.playlistDetail = { ...this.playlistDetail, ...result, brief_prompt: prompt };
        const domain = this.currentDomain();
        if (domain) {
          domain.brief_granularity = this.briefGenerationGranularityDraft;
          domain.brief_prompt = prompt;
        }
        this.briefGenerationDraftTouched = false;
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
      const granularity = ["day", "week", "month"].includes(this.briefBatchGranularity)
        ? this.briefBatchGranularity
        : "day";
      const from = periodStartIso(String(this.briefBatchFrom || ""), granularity);
      const to = periodStartIso(String(this.briefBatchTo || ""), granularity);
      if (!from || !to || to < from) return 0;
      return periodDiff(from, to, granularity) + 1;
    },

    async submitBriefBatchGeneration() {
      const domainId = String(this.selectedPlaylistId || "");
      if (!domainId || !this.domainObservationEnabled() || !this.briefBatchEstimatedPeriods() || this.briefBatchSubmitting) return;
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
