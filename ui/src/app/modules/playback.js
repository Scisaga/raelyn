const PLAYBACK_STATE_KEY_PREFIX = "raelyn.v2.playback";

export function createPlaybackModule() {
  return {
    playbackStateKeyPrefix: PLAYBACK_STATE_KEY_PREFIX,
    playbackMobileTab: "records",
    playbackRate: 1,
    playbackAutoAdvance: true,
    playbackDayTruncated: false,
    playbackPendingSeekSec: null,
    playbackRestoring: false,
    playbackLastPersistAt: 0,
  };
}
