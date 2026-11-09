#!/bin/bash
# 把最新的 index.html 发布到 GitHub Pages（gh-pages 分支，每次覆盖，只保留一版，避免仓库越来越大）。
# 主分支只放代码、知识库和研判记录。
set -e
cd "$(dirname "$0")"
REMOTE=$(git remote get-url origin 2>/dev/null) || { echo "没有配置 origin，跳过发布"; exit 0; }
[ -f index.html ] || { echo "没有 index.html，跳过发布"; exit 0; }
TMP=$(mktemp -d)
cp index.html "$TMP/index.html"
touch "$TMP/.nojekyll"
cd "$TMP"
git init -q
git checkout -q -b gh-pages
git add -A
git -c user.name="stock-radar" -c user.email="stock-radar@users.noreply.github.com" commit -q -m "发布 $(date '+%Y-%m-%d %H:%M')"
git push -q -f "$REMOTE" gh-pages
cd /; rm -rf "$TMP"
echo "已发布到 GitHub Pages。"
