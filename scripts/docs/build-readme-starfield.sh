#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ $# -ne 1 ]]; then
  echo "usage: $0 <raw-starfield-recording.mp4>" >&2
  exit 2
fi

source_video="$1"
chrome="$repo_root/docs/assets/readme/raelyn-v2-chrome.svg"
output_mp4="$repo_root/docs/assets/readme/raelyn-semantic-starfield.mp4"
output_gif="$repo_root/docs/assets/readme/raelyn-semantic-starfield.gif"
# SVG 应用壳需要 librsvg；项目内下载的静态 ffmpeg 不包含该解码器。
ffmpeg_bin="/usr/bin/ffmpeg"
if [[ ! -x "$ffmpeg_bin" ]]; then
  ffmpeg_bin="$(command -v ffmpeg)"
fi
if [[ ! -f "$source_video" ]]; then
  echo "source video not found: $source_video" >&2
  exit 1
fi
if [[ "$(realpath "$source_video")" == "$(realpath "$output_mp4")" ]]; then
  echo "source video must be the raw recording, not the generated README asset" >&2
  exit 2
fi

temp_dir="$(mktemp -d /tmp/raelyn-readme-v2.XXXXXX)"
trap 'rm -rf "$temp_dir"' EXIT

"$ffmpeg_bin" -hide_banner -loglevel warning -y \
  -i "$source_video" -loop 1 -framerate 20 -i "$chrome" \
  -filter_complex "[0:v]crop=1864:1015:56:185,scale=1864:1063:flags=lanczos,setpts=PTS-STARTPTS[body];color=c=#02040b:s=1920x1200:r=20:d=8[bg];[bg][body]overlay=56:137:shortest=1[scene];[1:v]format=rgba[chrome];[scene][chrome]overlay=0:0:shortest=1,format=yuv420p[out]" \
  -map "[out]" -an -t 8 -r 20 -c:v libx264 -preset slow -crf 18 -movflags +faststart \
  "$temp_dir/raelyn-semantic-starfield.mp4"

"$ffmpeg_bin" -hide_banner -loglevel warning -y \
  -i "$temp_dir/raelyn-semantic-starfield.mp4" \
  -filter_complex "[0:v]fps=12,scale=1280:-2:flags=lanczos,split[gif][palette_input];[palette_input]palettegen=max_colors=192:stats_mode=diff[palette];[gif][palette]paletteuse=dither=sierra2_4a:diff_mode=rectangle" \
  -loop 0 "$temp_dir/raelyn-semantic-starfield.gif"

mv "$temp_dir/raelyn-semantic-starfield.mp4" "$output_mp4"
mv "$temp_dir/raelyn-semantic-starfield.gif" "$output_gif"

echo "built: $output_mp4"
echo "built: $output_gif"
