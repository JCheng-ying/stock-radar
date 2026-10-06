"""全市场扫描：从纳斯达克的上市公司名单（NYSE + Nasdaq + AMEX 全部约 7000 只）出发，
抓每家公司的主营业务描述，再用每个零件的关键词去匹配——这样股票池不依赖人工名单。

  python -m src.discover scan     # 抓名单 + 业务描述（慢，结果缓存 30 天）
匹配在每次刷新时做（match_parts），很快。
"""
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests
import yfinance as yf

from . import config
from .fetch import BROWSER_UA

log = logging.getLogger(__name__)
PROFILES = config.DATA / "profiles.json"
MIN_MCAP = 2e8
SKIP_SECTORS = {"Finance", "Real Estate"}
SKIP_NAME = re.compile(r"warrant|\bunits?\b|preferred|depositary shares representing|notes due|"
                       r"acquisition corp|\betf\b|\bfund\b|\btrust\b|%", re.I)


def listing():
    r = requests.get("https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true",
                     headers={"User-Agent": BROWSER_UA, "Accept": "application/json"}, timeout=60)
    r.raise_for_status()
    out = {}
    for x in r.json()["data"]["rows"]:
        try:
            mcap = float(x["marketCap"] or 0)
        except ValueError:
            mcap = 0
        sym = x["symbol"].strip().replace("/", "-")
        if mcap < MIN_MCAP or x["sector"] in SKIP_SECTORS or SKIP_NAME.search(x["name"]) or "^" in sym:
            continue
        out[sym] = {"name": x["name"], "mcap": mcap, "nq_sector": x["sector"], "nq_industry": x["industry"],
                    "country": x["country"]}
    return out


def _profile(sym):
    for attempt in range(2):
        try:
            i = yf.Ticker(sym).info or {}
            if i.get("longBusinessSummary"):
                return sym, {"short": i.get("shortName") or "", "industry": i.get("industry") or "",
                             "sector": i.get("sector") or "", "summary": i["longBusinessSummary"]}
            return sym, None
        except Exception:
            time.sleep(2 + 3 * attempt)
    return sym, None


def scan():
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    base = listing()
    try:
        old = json.loads(PROFILES.read_text())
    except Exception:
        old = {}
    todo = [s for s in base if not (old.get(s) or {}).get("summary")]
    print(f"名单 {len(base)} 家，其中 {len(todo)} 家需要抓业务描述", flush=True)
    done = 0
    with ThreadPoolExecutor(4) as ex:
        for sym, p in ex.map(_profile, todo):
            done += 1
            if p:
                base[sym].update(p)
            if done % 200 == 0:
                print(f"  {done}/{len(todo)}", flush=True)
    for s in base:
        if "summary" not in base[s] and (old.get(s) or {}).get("summary"):
            base[s].update({k: old[s][k] for k in ("short", "industry", "sector", "summary")})
    PROFILES.write_text(json.dumps(base, ensure_ascii=False))
    print(f"完成：{sum(1 for v in base.values() if v.get('summary'))} 家有业务描述 -> {PROFILES}")


def load_profiles():
    try:
        return json.loads(PROFILES.read_text())
    except Exception:
        return {}


SKIP_YF_SECTORS = {"Healthcare", "Consumer Defensive", "Financial Services", "Real Estate"}
LEAD = 380   # 业务描述开头这么多字符基本就是"这家公司主要做什么"


def _score(text, kws):
    """关键词证据强度：出现在描述开头 ×2；多词短语 ×1.5。单个泛词只出现在后文不算数。"""
    total, hit = 0.0, []
    for k, rx in kws:
        m = rx.search(text)
        if not m:
            continue
        w = (2.0 if m.start() < LEAD else 1.0) * (1.5 if " " in k or "-" in k else 1.0)
        total += w
        hit.append(k)
    return total, hit


def match_parts(sectors, per_part=10, min_score=2.0):
    """给每个零件（node）补上"自动发现"的公司：主营业务描述里出现了该零件的关键词。
    整词匹配 + 证据强度打分（见 _score），达到 min_score 才收；按分数、市值排序，
    每个零件最多 per_part 家；人工名单里已有的不重复加。"""
    prof = {s: p for s, p in load_profiles().items() if p.get("summary")}
    text = {s: p["summary"].lower() for s, p in prof.items()}
    added = 0
    for sec in sectors:
        for chain in sec["chains"]:
            medical = chain["id"] == "surgical"
            for node in chain["nodes"]:
                kws = [(k.lower(), re.compile(r"(?<![a-z])" + re.escape(k.lower()) + r"(?![a-z])"))
                       for k in node.get("kw", [])]
                if not kws:
                    continue
                have = {c["ticker"] for c in node["cos"] if c["ticker"]}
                hits = []
                for s, t in text.items():
                    if s in have or (prof[s].get("sector") in SKIP_YF_SECTORS and not medical):
                        continue
                    sc, m = _score(t, kws)
                    if sc >= min_score:
                        hits.append((sc, prof[s]["mcap"], s, m))
                hits.sort(reverse=True)
                for sc, mcap, s, m in hits[:per_part]:
                    node["cos"].append({"name": s, "ticker": s, "core": False, "auto": True,
                                        "note": "业务描述命中：" + " / ".join(m[:3])})
                    added += 1
    return added


if __name__ == "__main__":
    if sys.argv[1:] == ["scan"]:
        scan()
