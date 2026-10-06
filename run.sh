#!/bin/bash
# 一次完整刷新：抓数据 -> 算五个面和浪位 -> 推送变化 -> 生成 index.html
# 如果这个目录配了 git 远端，就顺手提交并推送（GitHub Pages 会自动更新）。
# 用法：./run.sh            正常刷新
#       ./run.sh --no-push  只刷新网页，不发手机推送
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
python -m src.main "$@"

if git remote get-url origin >/dev/null 2>&1; then
  git add -A
  if ! git diff --cached --quiet; then
    git commit -q -m "刷新数据 $(date '+%Y-%m-%d %H:%M')"
    git push -q && echo "已推送，GitHub Pages 一两分钟内更新。"
  fi
fi
