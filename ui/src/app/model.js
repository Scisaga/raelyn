import { SETTINGS_TABS, YTDLP_FORMAT_PRESET_1080, YTDLP_FORMAT_PRESET_720 } from "./constants.js";
import { createAppInitMethods } from "./init-model.js";
import { createApiMethods } from "../services/api.js";
import { createUrlStateMethods } from "../services/url-state.js";
import { createCommonViewMethods } from "../shared/view-helpers.js";
import { createOverviewViewMethods } from "../views/overview-model.js";
import { createMediaViewMethods } from "../views/media-model.js";
import { createVideosViewMethods } from "../views/videos-model.js";
import { createJobsViewMethods } from "../views/jobs-model.js";
import { createSettingsViewMethods } from "../views/settings-model.js";
import { createPlaylistsViewMethods } from "../views/playlists-model.js";
import { createPlaylistViewMethods } from "../views/playlist-model.js";
import { mergeModelSegments } from "./model-segments.js";
import { createShellModule } from "./modules/shell.js";
import { createToastModule } from "./modules/toast.js";
import { createMediaVideosModule } from "./modules/media-videos.js";
import { createJobsModule } from "./modules/jobs.js";
import { createSettingsModule } from "./modules/settings.js";
import { createPlayerModule } from "./modules/player.js";
import { createPlaylistsModule } from "./modules/playlists.js";
import { createPwaModule } from "./modules/pwa.js";
import { createMediaSessionModule } from "./modules/media-session.js";
import { createV2Module } from "./modules/v2.js";
import { createV2ViewMethods } from "../views/v2-model.js";
import { createPlaybackModule } from "./modules/playback.js";
import { createPlaybackViewMethods } from "../views/playback-model.js";

const SIDEBAR_COLLAPSED_KEY = "raelyn.ui.sidebarCollapsed";
const SIDEBAR_HIDDEN_KEY = "raelyn.ui.sidebarHidden";
const PWA_INSTALL_HINT_DISMISSED_KEY = "raelyn.ui.pwaInstallHintDismissed";
const API_TOKEN_COOKIE_KEY = "raelyn_api_token";
const STARTUP_GATE_SEEN_SESSION_KEY = "raelyn.ui.startupGateSeen";

export function createAppModel() {
  return mergeModelSegments(
    {
      name: "shell",
      value: createShellModule({
        apiTokenCookieKey: API_TOKEN_COOKIE_KEY,
        sidebarCollapsedKey: SIDEBAR_COLLAPSED_KEY,
        sidebarHiddenKey: SIDEBAR_HIDDEN_KEY,
        startupGateSeenSessionKey: STARTUP_GATE_SEEN_SESSION_KEY,
      }),
    },
    { name: "toast", value: createToastModule() },
    { name: "mediaVideos", value: createMediaVideosModule() },
    { name: "jobs", value: createJobsModule() },
    { name: "settings", value: createSettingsModule() },
    { name: "player", value: createPlayerModule() },
    { name: "playlists", value: createPlaylistsModule() },
    { name: "pwa", value: createPwaModule({ installHintDismissedKey: PWA_INSTALL_HINT_DISMISSED_KEY }) },
    { name: "mediaSession", value: createMediaSessionModule() },
    { name: "v2", value: createV2Module() },
    { name: "playback", value: createPlaybackModule() },
    { name: "api", value: createApiMethods() },
    { name: "shared", value: createCommonViewMethods() },
    { name: "urlState", value: createUrlStateMethods({ settingsTabs: SETTINGS_TABS }) },
    { name: "overviewView", value: createOverviewViewMethods() },
    { name: "mediaView", value: createMediaViewMethods() },
    { name: "videosView", value: createVideosViewMethods() },
    { name: "jobsView", value: createJobsViewMethods() },
    {
      name: "settingsView",
      value: createSettingsViewMethods({
        ytdlpFormatPreset1080: YTDLP_FORMAT_PRESET_1080,
        ytdlpFormatPreset720: YTDLP_FORMAT_PRESET_720,
      }),
    },
    { name: "playlistsView", value: createPlaylistsViewMethods() },
    { name: "playlistView", value: createPlaylistViewMethods() },
    { name: "v2View", value: createV2ViewMethods() },
    { name: "playbackView", value: createPlaybackViewMethods() },
    { name: "init", value: createAppInitMethods() }
  );
}
