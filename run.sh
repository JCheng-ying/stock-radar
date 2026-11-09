#!/bin/bash
# 一次完整刷新：抓数据 -> 算五个面和浪位 -> 推送变化 -> 生成 index.html
# 如果这个目录配了 git 远端，就顺手提交并推送（GitHub Pages 会自动更新）。
# 用法：./run.sh            正常刷新
#       ./run.sh --no-push  只刷新网页，不发手机推送
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
python -m src.main "$@"

# 发布网页（gh-pages 分支，覆盖式）；代码和研判记录的提交由每日研判任务负责
./publish.sh || echo "发布失败（网络或权限问题），下次刷新会重试"
