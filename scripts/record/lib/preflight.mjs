// 录制前只读校验：证明固定快照、主题、事件、故事和播放列表素材仍然可用，
// 避免浏览器跑到一半才发现深链接已经漂移。

function apiUrl(baseUrl, path) {
  return new URL(path, baseUrl).toString();
}

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function getJson(baseUrl, token, path, fetchImpl) {
  const response = await fetchImpl(apiUrl(baseUrl, path), {
    headers: authHeaders(token),
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(`${path}: HTTP ${response.status} ${body.detail || response.statusText}`);
  }
  return response.json();
}

export async function validateRecordingData({ target, content, token, fetchImpl = fetch }) {
  const domain = encodeURIComponent(target.domainId);
  const snapshot = encodeURIComponent(target.snapshotId);
  const withSnapshot = (path) => `${path}${path.includes("?") ? "&" : "?"}snapshot_id=${snapshot}`;

  const manifest = await getJson(
    target.baseUrl,
    token,
    withSnapshot(`/api/playlists/${domain}/events/map/manifest?compact=true`),
    fetchImpl,
  );
  if (manifest.status !== "ready" || String(manifest.snapshot_id || "") !== target.snapshotId) {
    throw new Error(`固定快照不可录制: status=${manifest.status || "-"}, snapshot_id=${manifest.snapshot_id || "-"}`);
  }

  const [topicL0, topicL1, canonical, story, storyIdentity, videos, brief] = await Promise.all([
    getJson(target.baseUrl, token, withSnapshot(`/api/playlists/${domain}/events/map/topic/${encodeURIComponent(content.topicL0)}?limit=1`), fetchImpl),
    getJson(target.baseUrl, token, withSnapshot(`/api/playlists/${domain}/events/map/topic/${encodeURIComponent(content.topicL1)}?limit=1`), fetchImpl),
    getJson(target.baseUrl, token, withSnapshot(`/api/playlists/${domain}/events/map/canonical/${encodeURIComponent(content.canonical)}`), fetchImpl),
    getJson(target.baseUrl, token, withSnapshot(`/api/playlists/${domain}/events/map/story/${encodeURIComponent(content.storyId)}`), fetchImpl),
    getJson(target.baseUrl, token, `/api/domains/${domain}/stories/${encodeURIComponent(content.storyIdentityId)}`, fetchImpl),
    getJson(target.baseUrl, token, `/api/playlists/${domain}/videos_by_period?granularity=day&date=${encodeURIComponent(content.playlistDate)}&limit=500&time_basis=content`, fetchImpl),
    getJson(target.baseUrl, token, `/api/briefs/by_period?playlist_id=${domain}&granularity=day&date=${encodeURIComponent(content.playlistDate)}`, fetchImpl),
  ]);

  if (Number(topicL0.level) !== 0) throw new Error(`一级星域层级异常: ${content.topicL0}`);
  if (Number(topicL1.level) !== 1 || String(topicL1.parent_topic_id || "") !== content.topicL0) {
    throw new Error(`二级主题不再属于配置的一级星域: ${content.topicL1}`);
  }
  if (!canonical.canonical_id) throw new Error(`真实事件不可用: ${content.canonical}`);
  if (!story.story_id) throw new Error(`快照故事不可用: ${content.storyId}`);
  if (!storyIdentity.story_identity_id) throw new Error(`稳定故事不可用: ${content.storyIdentityId}`);
  if (!Array.isArray(videos) || videos.length === 0) throw new Error(`播放日期没有可播放记录: ${content.playlistDate}`);
  const playlistVideo = videos.find((item) => String(item.id || "") === content.playlistVideoId);
  if (!playlistVideo || playlistVideo.media_name !== "Reuters") {
    throw new Error(`录制用 Reuters 记录不可用: ${content.playlistVideoId}`);
  }
  const playlistSecondVideo = videos.find(
    (item) => String(item.id || "") === content.playlistSecondVideoId,
  );
  if (
    !playlistSecondVideo
    || playlistSecondVideo.media_name !== "CME Group"
    || !String(playlistSecondVideo.title || "").startsWith("WTI Crude Oil futures surge")
  ) {
    throw new Error(`录制用 WTI Crude Oil 记录不可用: ${content.playlistSecondVideoId}`);
  }
  if (brief.status !== "ready" || !brief.markdown_asset) throw new Error(`播放日期的简报未就绪: ${content.playlistDate}`);

  return {
    snapshotId: manifest.snapshot_id,
    canonicalCount: Number(manifest.canonical_count || 0),
    topicL0: topicL0.label,
    topicL1: topicL1.label,
    canonical: canonical.title,
    story: storyIdentity.stable_title || story.title,
    playableVideos: videos.length,
    playlistVideo: playlistVideo.title,
    briefId: brief.id,
  };
}
