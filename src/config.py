"""路径和可调参数都集中在这里。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CHAINS_DIR = DATA / "chains"
CACHE = DATA / "cache"
SNAPSHOT = DATA / "snapshot.json"
ALERT_STATE = DATA / "alert_state.json"
ALERT_LOG = DATA / "alert_log.jsonl"
NOTES = DATA / "notes.json"
TEMPLATE = ROOT / "web" / "template.html"
SITE = ROOT / "index.html"

# Summizer（资讯分析与投资图谱）项目的图谱文件；不存在就跳过，不影响其它功能
SUMMIZER_GRAPH = Path(os.environ.get(
    "SUMMIZER_GRAPH", Path.home() / "Desktop" / "Summizer" / "data" / "graph.json"))

# SEC 要求请求带上能联系到人的 User-Agent
# （必须是"名字 + 邮箱"的格式，否则 403）。建议在 .env 里把 SEC_UA 换成你自己的邮箱。
SEC_UA = os.environ.get("SEC_UA", "stock-radar/1.0 (personal research; contact@stock-radar.dev)")

PRICE_PERIOD = "2y"        # 日线历史长度（算 200 日线、52 周位置）
SPARK_DAYS = 180           # 打包进网页的日线根数
FUND_CACHE_HOURS = 20      # 基本面/一致预期缓存多久（财报季每天刷新一次就够）
SEC_CACHE_HOURS = 72
MAX_WORKERS = 6


def load_env():
    """读 .env（NTFY_TOPIC 等），不依赖 python-dotenv。"""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
