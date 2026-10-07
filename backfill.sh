#!/bin/bash
# 被 Yahoo 限流后用：每隔 10 分钟补抓一轮财报，直到全部公司都是新版数据或跑满 12 轮。
cd "$(dirname "$0")"; source .venv/bin/activate
for i in $(seq 1 12); do
  python -m src.main --no-push 2>&1 | grep -E "财报：|完成：" 
  left=$(python - <<'P'
import json,glob
print(sum(json.load(open(f)).get('v')!=2 for f in glob.glob('data/cache/fund_*.json')))
P
)
  echo "$(date '+%H:%M') 第 $i 轮结束，还有 $left 家是旧版数据"
  [ "$left" -lt 40 ] && break
  sleep 600
done
