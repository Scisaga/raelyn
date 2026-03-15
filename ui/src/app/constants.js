export const YTDLP_FORMAT_PRESET_1080 =
  "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best";

export const YTDLP_FORMAT_PRESET_720 =
  "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/bestvideo[height<=720]+bestaudio/best[height<=720]/best";

export const SETTINGS_TABS = ["cookies", "subtitles", "members", "format", "llm", "cleanup", "install"];

export const PLAYLIST_ASSETS_CACHE_TTL_MS = 60_000;
export const PLAYLIST_TRANSCRIPT_CACHE_TTL_MS = 10 * 60_000;
export const PLAYLIST_BRIEF_CACHE_TTL_MS = 10 * 60_000;
