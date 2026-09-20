// 录制清单合并：全量录制生成新清单；局部重录按配置顺序替换对应镜头，
// 保留其他镜头的实测时长。创作元数据始终来自当前 tour.config.mjs。

function normalizeEntry(configuredShot, recordedEntry) {
  return {
    ...recordedEntry,
    id: configuredShot.id,
    act: configuredShot.act,
    configuredDuration: configuredShot.duration,
    playbackRate: configuredShot.playbackRate || recordedEntry.playbackRate,
    joinPrev: configuredShot.joinPrev,
    captions: configuredShot.captions || [],
  };
}

export function mergeShotManifest({
  configuredShots,
  recordedShots,
  existingManifest = null,
  partial = false,
  metadata,
}) {
  if (partial && !existingManifest) {
    throw new Error("局部重录需要已有的完整 out/manifest.json；请先执行一次全量录制");
  }

  const recordedById = new Map(recordedShots.map((shot) => [shot.id, shot]));
  const existingById = new Map((existingManifest?.shots || []).map((shot) => [shot.id, shot]));
  const mergedShots = configuredShots.map((configuredShot) => {
    const recordedEntry = recordedById.get(configuredShot.id) || existingById.get(configuredShot.id);
    if (!recordedEntry) {
      throw new Error(`manifest 缺少镜头 ${configuredShot.id}；请先执行一次全量录制`);
    }
    return normalizeEntry(configuredShot, recordedEntry);
  });

  return {
    ...(existingManifest || {}),
    ...metadata,
    shots: mergedShots,
  };
}
