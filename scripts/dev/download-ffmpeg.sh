#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

mkdir -p bin
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

echo "[download] ffmpeg static build -> bin/ffmpeg"
curl -fsSL -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o "$tmpdir/ffmpeg.tar.xz"
tar -xJf "$tmpdir/ffmpeg.tar.xz" -C "$tmpdir"
ffdir="$(find "$tmpdir" -maxdepth 1 -type d -name 'ffmpeg-*-amd64-static' | head -n 1)"
if [[ -z "${ffdir:-}" ]]; then
  echo "failed to find ffmpeg extracted directory" >&2
  exit 1
fi
cp "$ffdir/ffmpeg" bin/ffmpeg
cp "$ffdir/ffprobe" bin/ffprobe
chmod +x bin/ffmpeg bin/ffprobe

./bin/ffmpeg -version | head -n 1 || true

