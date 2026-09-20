// 成片时间线：把镜头按 xfade 重叠排成一条序列，并计算每段的绝对起止时刻。
// 字幕与合成共用这份计算，避免两边各算一次算出不同结果。

import { output, transition, shots as configuredShots } from "../tour.config.mjs";

const configuredShotById = new Map(configuredShots.map((shot) => [shot.id, shot]));

export function buildTimeline(manifest) {
  const segments = [];

  for (const recordedShot of manifest.shots) {
    // 时长和文件来自录制结果；幕、转场和字幕始终读取当前配置。这样只改字幕
    // 后重跑 make-subtitles.mjs 就会生效，不依赖 manifest 里的旧配置副本。
    const configuredShot = configuredShotById.get(recordedShot.id);
    const shot = configuredShot
      ? {
          ...recordedShot,
          act: configuredShot.act,
          joinPrev: configuredShot.joinPrev,
          playbackRate: configuredShot.playbackRate || recordedShot.playbackRate || output.playbackRate,
          captions: configuredShot.captions || [],
          narration: configuredShot.narration || [],
        }
      : recordedShot;
    segments.push({
      kind: "shot",
      id: shot.id,
      file: shot.file,
      sourceDuration: shot.duration,
      playbackRate: shot.playbackRate || output.playbackRate,
      duration: shot.duration / (shot.playbackRate || output.playbackRate),
      overlap: segments.length
        ? (shot.joinPrev === null || shot.joinPrev === undefined ? transition.duration : shot.joinPrev)
        : 0,
      captions: shot.captions || [],
      narration: shot.narration || [],
    });
  }

  let cursor = 0;
  for (const seg of segments) {
    cursor = Math.max(0, cursor - seg.overlap);
    seg.start = cursor;
    seg.end = cursor + seg.duration;
    cursor = seg.end;
  }

  return { segments, duration: segments.length ? segments[segments.length - 1].end : 0 };
}

// 字幕条目：绝对时刻 + 中英两种文案。
export function collectCaptions(timeline, { minHoldSeconds = 1.2 } = {}) {
  const entries = [];
  for (const seg of timeline.segments) {
    for (const cap of seg.captions) {
      const start = seg.start + (cap.at || 0);
      const hold = Math.max(minHoldSeconds, cap.hold || minHoldSeconds);
      // 不让字幕越过本镜头结束，越界就截到镜头尾。
      const end = Math.min(seg.end, start + hold);
      if (end - start < minHoldSeconds * 0.75) {
        console.warn(`[warn] 字幕过短被压缩: ${seg.id} "${cap.zh}"`);
      }
      entries.push({ segment: seg.id, start, end, zh: cap.zh, en: cap.en });
    }
  }
  entries.sort((a, b) => a.start - b.start);
  return entries;
}

// 配音演讲稿条目：与画面字幕分开维护，但使用同一镜头绝对时间线。
export function collectNarration(timeline) {
  const entries = [];
  for (const seg of timeline.segments) {
    for (const item of seg.narration || []) {
      const start = seg.start + (item.at || 0);
      const end = Math.min(seg.end, start + Number(item.hold || seg.duration));
      entries.push({ segment: seg.id, start, end, zh: item.zh });
    }
  }
  entries.sort((a, b) => a.start - b.start);
  return entries;
}
