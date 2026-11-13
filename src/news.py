"""多来源新闻：财经媒体 + 行业媒体 + 按板块的 Google 新闻聚合 + 重点公司的 Seeking Alpha 动态。
Summizer 只覆盖一部分行业媒体，这里补上综合财经和公司层面的新闻。

每条新闻会按标题里出现的公司名 / 代码打上公司标签，按来源 / 查询打上板块标签。
全部只取标题和链接，不抓正文；缓存 2 小时。
"""
import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests

from . import config
from .fetch import BROWSER_UA

log = logging.getLogger(__name__)

GN = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
FEEDS = [  # (来源名, 地址, 板块标签)
    ("CNBC 科技", "https://www.cnbc.com/id/19854910/device/rss/rss.html", []),
    ("CNBC 市场", "https://www.cnbc.com/id/20910258/device/rss/rss.html", []),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", []),
    ("路透（Google 新闻）", GN + "site:reuters.com+(technology+OR+chips+OR+AI)+when:3d", []),
    ("彭博（Google 新闻）", GN + "site:bloomberg.com+(technology+OR+chips+OR+AI)+when:3d", []),
    ("DataCenterDynamics", "https://www.datacenterdynamics.com/en/rss/", ["ai"]),
    ("Tom's Hardware", "https://www.tomshardware.com/feeds/all", ["ai"]),
    ("SpaceNews", "https://spacenews.com/feed/", ["aerospace"]),
    ("The Quantum Insider", "https://thequantuminsider.com/feed/", ["quantum"]),
    ("The Robot Report", "https://www.therobotreport.com/feed/", ["robotics"]),
    ("Electrek", "https://electrek.co/feed/", ["autonomous"]),
    ("Google 新闻：AI 基础设施", GN + "(AI+chips+OR+%22data+center%22+OR+hyperscaler+capex)+when:3d", ["ai"]),
    ("Google 新闻：AI 软件", GN + "(%22AI+agents%22+OR+%22enterprise+AI%22+OR+OpenAI+OR+Anthropic)+when:3d", ["ai"]),
    ("Google 新闻：量子计算", GN + "%22quantum+computing%22+when:7d", ["quantum"]),
    ("Google 新闻：机器人", GN + "(humanoid+robot+OR+%22industrial+robots%22+OR+%22surgical+robot%22)+when:7d", ["robotics"]),
    ("Google 新闻：航天", GN + "(SpaceX+OR+%22Rocket+Lab%22+OR+satellite+OR+%22Golden+Dome%22)+when:5d", ["aerospace"]),
    ("Google 新闻：自动驾驶", GN + "(robotaxi+OR+Waymo+OR+%22autonomous+driving%22+OR+lidar)+when:5d", ["autonomous"]),
]
ELECTREK_KEEP = re.compile(r"autonom|self-driv|robotaxi|fsd|waymo|lidar|cybercab|zoox", re.I)
STOP = {"applied", "advanced", "general", "american", "united", "global", "international", "first", "digital",
        "intuitive", "micro", "power", "energy", "data", "taiwan", "texas", "teledyne", "ultra", "national",
        "rocket", "sterling", "comfort", "allied", "atlas", "monolithic", "universal", "quantum", "planet"}


def _cache(name, hours, fn):
    f = config.CACHE / name
    if f.exists() and time.time() - f.stat().st_mtime < hours * 3600:
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    try:
        v = fn()
        f.write_text(json.dumps(v, ensure_ascii=False))
        return v
    except Exception:
        log.warning("news source failed: %s", name)
        try:
            return json.loads(f.read_text())
        except Exception:
            return []


def _parse(src, url, tags):
    r = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=20)
    out = []
    for e in feedparser.parse(r.content).entries[:60]:
        title = (e.get("title") or "").strip()
        link = e.get("link") or ""
        if not title or not link:
            continue
        pub = src
        if "news.google.com" in url and " - " in title:   # Google 新闻标题末尾是 " - 媒体名"
            title, pub = title.rsplit(" - ", 1)
            pub = f"{pub}（经 Google 新闻）"
        if src == "Electrek" and not ELECTREK_KEEP.search(title):
            continue
        try:
            dt = parsedate_to_datetime(e.get("published") or e.get("updated")).astimezone(timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
        out.append({"title": title, "url": link, "src": pub, "ts": dt.isoformat(timespec="minutes"),
                    "date": dt.strftime("%Y-%m-%d"), "sectors": list(tags)})
    return out


def _company_patterns(companies):
    """公司名 → 正则。只用足够独特的名字片段，避免 'Applied'、'General' 这类泛词误配。"""
    pats = {}
    for t, c in companies.items():
        if all(m.get("auto") for m in c["members"]):
            continue
        name = re.sub(r"[,.]|\b(inc|corp|corporation|holdings|group|ltd|plc|n\.v|co|company|technologies|technology|class a)\b",
                      " ", c["name"], flags=re.I).split()
        keys = {rf"\b{re.escape(t)}\b"} if len(t) >= 3 else set()
        if name and len(name[0]) >= 4 and name[0].lower() not in STOP:
            keys.add(rf"\b{re.escape(name[0])}\b")
        elif len(name) >= 2:
            keys.add(rf"\b{re.escape(name[0])}\s+{re.escape(name[1])}\b")
        if keys:
            pats[t] = re.compile("|".join(keys), re.I if len(t) >= 4 else 0)
    return pats


def collect(companies, days=4, focus=(), cache_hours=2):
    """返回去重后的新闻列表（最近 days 天，新的在前），并给每条打上公司 / 板块标签。"""
    items = []
    jobs = [(s, u, tg) for s, u, tg in FEEDS]
    for t in list(focus)[:60]:   # 重点公司：Seeking Alpha 的公司动态
        jobs.append((f"Seeking Alpha", f"https://seekingalpha.com/api/sa/combined/{t}.xml", [], ))
    with ThreadPoolExecutor(8) as ex:
        res = ex.map(lambda j: (j, _cache(f"news_{hashlib.md5(j[1].encode()).hexdigest()[:12]}.json", cache_hours, lambda: _parse(*j))), jobs)
        for (s, u, tg), lst in res:
            focus_t = u.split("/combined/")[1].split(".")[0] if "/combined/" in u else None
            for x in lst:
                if focus_t:
                    x = dict(x, tickers=[focus_t])
                items.append(x)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    pats = _company_patterns(companies)
    seen, out = set(), []
    for x in sorted(items, key=lambda x: x["ts"], reverse=True):
        key = re.sub(r"\W+", "", x["title"].lower())[:80]
        if x["ts"] < cutoff or key in seen:
            continue
        seen.add(key)
        # 行情被限流时部分公司会缺席 companies；只保留本轮有数据的代码，避免 KeyError
        tk = {t for t in (x.get("tickers") or []) if t in companies}
        tk |= {t for t, p in pats.items() if p.search(x["title"])}
        secs = set(x["sectors"])
        for t in tk:
            secs |= {m["sector"] for m in companies[t].get("members", []) if m.get("core") and not m.get("auto")}
        out.append(dict(x, tickers=sorted(tk)[:6], sectors=sorted(secs)))
    return out[:400]
