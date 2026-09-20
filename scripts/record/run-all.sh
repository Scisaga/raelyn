#!/usr/bin/env bash
# 一键跑当前流水线：接入原片 → README 截图 → 画面字幕 → 配音稿 → 选择音轨 → 合成。
# 配音阶段当前只生成审阅稿与对时 SRT，不录制或混入人声。
# 任一步失败立即停止，不产出半成品。
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d node_modules ]; then
  echo "▶ 安装锁定版本的 Node 依赖"
  env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
      -u http_proxy -u https_proxy -u all_proxy \
      -u NODE_TLS_REJECT_UNAUTHORIZED npm ci
fi

if ! node --input-type=module -e \
  'import { chromium } from "playwright"; import { existsSync } from "node:fs"; process.exit(existsSync(chromium.executablePath()) ? 0 : 1)'; then
  echo "▶ 安装 Playwright Chromium"
  env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
      -u http_proxy -u https_proxy -u all_proxy \
      -u NODE_TLS_REJECT_UNAUTHORIZED npx playwright install chromium
fi

echo "▶ 1/6 接入并标准化原始界面录屏"
node record-tour.mjs "$@"

echo "▶ 2/6 从已审镜头生成 README 截图"
node make-readme-screenshots.mjs

echo "▶ 3/6 生成画面字幕"
node make-subtitles.mjs

echo "▶ 4/6 生成配音演讲稿"
node make-narration.mjs

echo "▶ 5/6 选择音轨"
node select-music.mjs

echo "▶ 6/6 合成成片"
node compose-tour.mjs

echo
echo "完成。成片在 scripts/record/out/："
ls -lh out/raelyn-tour-*.mp4 2>/dev/null || true
ls -lh ../../docs/assets/tour/readme-*.webp 2>/dev/null || true
