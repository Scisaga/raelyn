#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

mkdir -p bin

echo "[download] yt-dlp (linux) -> bin/yt-dlp"
curl -fsSL -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux -o bin/yt-dlp
chmod +x bin/yt-dlp

./bin/yt-dlp --version || true

