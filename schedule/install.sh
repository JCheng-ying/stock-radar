#!/bin/bash
# 安装 macOS 定时任务（launchd）：工作日自动跑 run.sh。时间是本机时区（按太平洋时间写的）。
#   06:35 ~ 13:05  交易时段每 30 分钟一次（有缓存，主要是刷新行情和信号）
#   13:35 / 14:30 / 17:30 / 20:00  收盘后 + 盘后财报陆续出来
# 电脑睡眠时错过的任务，会在唤醒后补跑一次。
# 卸载：./schedule/install.sh --uninstall
set -e
LABEL="com.stock-radar.refresh"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DIR="$(cd "$(dirname "$0")/.." && pwd)"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$1" = "--uninstall" ]; then
  rm -f "$PLIST"; echo "已卸载定时任务。"; exit 0
fi

slots=""
for wd in 1 2 3 4 5; do
  for hm in "6 35" "7 5" "7 35" "8 5" "8 35" "9 5" "9 35" "10 5" "10 35" "11 5" "11 35" "12 5" "12 35" "13 5" "13 35" "14 30" "17 30" "20 0"; do
    set -- $hm
    slots="$slots<dict><key>Weekday</key><integer>$wd</integer><key>Hour</key><integer>$1</integer><key>Minute</key><integer>$2</integer></dict>"
  done
done

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/run.sh</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartCalendarInterval</key><array>$slots</array>
  <key>StandardOutPath</key><string>$DIR/data/cache/run.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/cache/run.log</string>
</dict></plist>
PL
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "已安装：工作日交易时段每 30 分钟 + 盘后 4 次自动刷新。日志在 data/cache/run.log"
