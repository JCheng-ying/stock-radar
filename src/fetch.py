"""所有联网取数都在这里：行情（yfinance）、基本面/一致预期（yfinance）、
RPO（SEC XBRL）、恐贪指数（CNN）。

每个来源都是"取不到就返回空"，不让某一个接口挂掉影响整次刷新。
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
import yfinance as yf

from . import config

log = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

MACRO = {
    "^VIX": "VIX 恐慌指数", "^VIX3M": "VIX 3个月", "^TNX": "10年期美债收益率",
    "^FVX": "5年期美债收益率", "^IRX": "3个月美债收益率", "DX-Y.NYB": "美元指数",
    "SPY": "标普500", "QQQ": "纳指100", "IWM": "罗素2000", "HYG": "高收益债", "TLT": "长期国债",
}
SPDR = {
    "XLK": "科技", "XLC": "通信服务", "XLY": "可选消费", "XLF": "金融", "XLI": "工业",
    "XLV": "医疗", "XLE": "能源", "XLU": "公用事业", "XLB": "材料", "XLP": "必需消费", "XLRE": "房地产",
}
# 杠杆风险的代理指标：三倍做多 ETF 的成交额 / 对应正股 ETF 的成交额
LEVERAGED = ["TQQQ", "SOXL", "UPRO", "TECL"]
LEVERAGE_BASE = ["QQQ", "SPY", "SMH"]
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# ---------- 小工具 ----------

def _cache_get(name, max_age_hours):
    f = config.CACHE / name
    if not f.exists():
        return None
    if time.time() - f.stat().st_mtime > max_age_hours * 3600:
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def _cache_put(name, obj):
    config.CACHE.mkdir(parents=True, exist_ok=True)
    (config.CACHE / name).write_text(json.dumps(obj, ensure_ascii=False))


def _num(x):
    try:
        x = float(x)
        return None if x != x or x in (float("inf"), float("-inf")) else x
    except Exception:
        return None


# ---------- 行情 ----------

def fetch_prices(tickers):
    """返回 {ticker: DataFrame(Close, Volume)}，日线。分批下载，失败的代码直接缺席。"""
    out = {}
    tickers = sorted(set(tickers))
    _price_pass(tickers, out)
    for _ in range(2):            # 整批缺失多半是被限流，歇一会儿再补
        missing = [t for t in tickers if t not in out]
        if len(missing) <= 8:
            break
        time.sleep(60)
        _price_pass(missing, out)
    return out


def _price_pass(tickers, out):
    for i in range(0, len(tickers), 60):
        batch = tickers[i:i + 60]
        try:
            df = yf.download(batch, period=config.PRICE_PERIOD, progress=False,
                             auto_adjust=True, threads=True, group_by="ticker")
        except Exception:
            log.exception("price batch failed")
            continue
        for t in batch:
            try:
                sub = df[t][["Close", "Volume"]].dropna(subset=["Close"])
                if len(sub) >= 30:
                    out[t] = sub
            except Exception:
                pass
        time.sleep(1)


# ---------- 基本面 + 一致预期 ----------

INFO_KEYS = [
    "shortName", "longName", "industry", "marketCap", "currentPrice", "currency",
    "trailingPE", "forwardPE", "priceToSalesTrailing12Months", "enterpriseToEbitda",
    "trailingPegRatio", "targetMeanPrice", "targetHighPrice", "targetLowPrice",
    "numberOfAnalystOpinions", "recommendationMean", "recommendationKey",
    "grossMargins", "ebitdaMargins", "operatingMargins", "revenueGrowth", "earningsGrowth",
    "freeCashflow", "totalCash", "totalDebt", "trailingEps", "forwardEps",
    "shortPercentOfFloat", "heldPercentInstitutions", "beta",
]
INCOME_ROWS = {"Total Revenue": "rev", "Gross Profit": "gp", "EBITDA": "ebitda",
               "Operating Income": "op", "Net Income": "ni", "Diluted EPS": "eps"}


def _quarters(t):
    """最近几个季度的收入/毛利/EBITDA/EPS/自由现金流，按时间从旧到新。"""
    rows = {}
    try:
        inc = t.quarterly_income_stmt
        for src, key in INCOME_ROWS.items():
            if src in inc.index:
                for col, v in inc.loc[src].items():
                    rows.setdefault(str(col.date()), {})[key] = _num(v)
    except Exception:
        pass
    try:
        cf = t.quarterly_cashflow
        if "Free Cash Flow" in cf.index:
            for col, v in cf.loc["Free Cash Flow"].items():
                if str(col.date()) in rows:
                    rows[str(col.date())]["fcf"] = _num(v)
    except Exception:
        pass
    out = [dict(end=k, **v) for k, v in sorted(rows.items()) if v.get("rev")]
    return out[-6:]


def _one_fundamental(ticker):
    t = yf.Ticker(ticker)
    d = {"ticker": ticker, "fetched": datetime.now(timezone.utc).isoformat()}
    try:
        info = t.info or {}
    except Exception:
        info = {}
    d["info"] = {k: info.get(k) for k in INFO_KEYS if info.get(k) is not None}
    d["quarters"] = _quarters(t)
    try:
        cal = t.calendar or {}
        ed = cal.get("Earnings Date") or []
        d["next_earnings"] = str(ed[0]) if ed else None
        d["eps_est"] = _num(cal.get("Earnings Average"))
        d["rev_est"] = _num(cal.get("Revenue Average"))
    except Exception:
        d["next_earnings"] = None
    try:
        et = t.eps_trend
        d["eps_trend"] = {p: {"now": _num(r.get("current")), "d30": _num(r.get("30daysAgo")),
                              "d90": _num(r.get("90daysAgo"))} for p, r in et.iterrows()}
    except Exception:
        d["eps_trend"] = {}
    try:
        eh = t.earnings_history
        d["surprises"] = [{"q": str(getattr(i, "date", lambda: i)()), "actual": _num(r.get("epsActual")),
                           "est": _num(r.get("epsEstimate")), "pct": _num(r.get("surprisePercent"))}
                          for i, r in eh.iterrows()][-4:]
    except Exception:
        d["surprises"] = []
    news = []
    try:
        for n in yf.Search(ticker, news_count=8, max_results=1).news or []:
            if n.get("title") and n.get("link"):
                ts = n.get("providerPublishTime")
                news.append({"title": n["title"], "url": n["link"], "src": n.get("publisher", ""),
                             "date": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else ""})
    except Exception:
        pass
    d["news"] = news
    return d


def fetch_fundamentals(tickers, force=False):
    def work(tk):
        name = f"fund_{tk}.json"
        if not force:
            c = _cache_get(name, config.FUND_CACHE_HOURS)
            if c:
                return c
        for attempt in range(2):
            try:
                d = _one_fundamental(tk)
                if d["info"] or d["quarters"]:
                    _cache_put(name, d)
                    return d
            except Exception:
                log.exception("fundamental %s", tk)
            time.sleep(2 + attempt * 3)
        # 这次没取到，退回旧缓存（哪怕过期）总比没有好
        return _cache_get(name, 24 * 365) or {"ticker": tk, "info": {}, "quarters": []}

    with ThreadPoolExecutor(config.MAX_WORKERS) as ex:
        return {d["ticker"]: d for d in ex.map(work, sorted(set(tickers)))}


# ---------- SEC：RPO（剩余履约义务 = 已签约未确认收入的长期订单） ----------

def _sec_get(url):
    r = requests.get(url, headers={"User-Agent": config.SEC_UA}, timeout=30)
    time.sleep(0.15)  # SEC 限速 10 次/秒
    return r


def _cik_map():
    c = _cache_get("sec_tickers.json", 24 * 30)
    if c:
        return c
    r = _sec_get("https://www.sec.gov/files/company_tickers.json")
    r.raise_for_status()
    m = {v["ticker"].upper(): int(v["cik_str"]) for v in r.json().values()}
    _cache_put("sec_tickers.json", m)
    return m


def fetch_rpo(tickers, force=False):
    """{ticker: [{end, val}]}，按季度末去重、时间从旧到新。没披露 RPO 的公司不出现。"""
    try:
        ciks = _cik_map()
    except Exception:
        log.exception("sec ticker map")
        return {}
    out = {}
    for tk in sorted(set(tickers)):
        name = f"rpo_{tk}.json"
        c = None if force else _cache_get(name, config.SEC_CACHE_HOURS)
        if c is None:
            cik = ciks.get(tk.replace("-", "."), ciks.get(tk))
            c = []
            if cik:
                try:
                    r = _sec_get("https://data.sec.gov/api/xbrl/companyconcept/"
                                 f"CIK{cik:010d}/us-gaap/RevenueRemainingPerformanceObligation.json")
                    if r.status_code == 200:
                        by_end = {}
                        for u in r.json().get("units", {}).get("USD", []):
                            if u.get("form") in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
                                by_end[u["end"]] = u["val"]
                        c = [{"end": k, "val": v} for k, v in sorted(by_end.items())][-9:]
                except Exception:
                    log.warning("rpo %s failed", tk)
            _cache_put(name, c)
        if c:
            out[tk] = c
    return out


# 8-K 条款号 -> 这是一条什么性质的重大消息
ITEM_8K = {
    "1.01": "签订重大协议", "1.02": "重大协议终止", "1.03": "破产", "1.05": "网络安全事件",
    "2.01": "完成收购/资产处置", "2.02": "财报发布", "2.03": "新增重大债务", "2.05": "重组/裁员",
    "2.06": "资产减值", "3.01": "退市风险通知", "3.02": "非公开发行股票", "4.01": "更换审计师",
    "4.02": "财报不可依赖（重述）", "5.01": "控制权变更", "5.02": "高管/董事变动",
    "5.07": "股东大会表决", "7.01": "监管披露（FD）", "8.01": "其他重大事件",
}


def fetch_filings(tickers, days=60, force=False):
    """最近的 8-K（重大事件公告）。{ticker: [{date, items:[中文], url}]}"""
    try:
        ciks = _cik_map()
    except Exception:
        return {}
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    out = {}
    for tk in sorted(set(tickers)):
        name = f"8k_{tk}.json"
        c = None if force else _cache_get(name, 12)
        if c is None:
            c = []
            cik = ciks.get(tk.replace("-", "."), ciks.get(tk))
            if cik:
                try:
                    r = _sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
                    rec = r.json()["filings"]["recent"] if r.status_code == 200 else {}
                    for form, dt, acc, doc, items in zip(rec.get("form", []), rec.get("filingDate", []),
                                                         rec.get("accessionNumber", []), rec.get("primaryDocument", []),
                                                         rec.get("items", [])):
                        if form != "8-K" or datetime.fromisoformat(dt).replace(tzinfo=timezone.utc).timestamp() < cutoff:
                            continue
                        codes = [i.strip() for i in items.split(",") if i.strip() and i.strip() != "9.01"]
                        c.append({"date": dt, "codes": codes,
                                  "items": [ITEM_8K.get(i, f"条款 {i}") for i in codes],
                                  "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"})
                except Exception:
                    log.warning("8-K %s failed", tk)
            _cache_put(name, c[:8])
        if c:
            out[tk] = c[:8]
    return out


# ---------- 情绪面的外部指标 ----------

def fetch_fear_greed():
    """CNN 恐惧与贪婪指数（非官方接口，取不到就 None）。"""
    try:
        r = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                         headers={"User-Agent": BROWSER_UA}, timeout=20)
        j = r.json()
        fg = j["fear_and_greed"]
        hist = [[int(p["x"]), round(p["y"], 1)] for p in j["fear_and_greed_historical"]["data"]][-250:]
        pc = j.get("put_call_options", {}).get("data") or []
        return {"score": round(fg["score"], 1), "rating": fg["rating"],
                "prev_week": round(fg.get("previous_1_week", 0), 1),
                "prev_month": round(fg.get("previous_1_month", 0), 1),
                "prev_year": round(fg.get("previous_1_year", 0), 1),
                "put_call": round(pc[-1]["y"], 3) if pc else None, "hist": hist}
    except Exception:
        log.warning("fear&greed unavailable")
        return None


def fetch_etf_holdings(etf):
    """ETF 前十大持仓的代码（用来看 IGV 内部是不是分化上涨）。"""
    c = _cache_get(f"hold_{etf}.json", 24 * 7)
    if c:
        return c
    try:
        th = yf.Ticker(etf).funds_data.top_holdings
        out = [str(s) for s in th.index]
        _cache_put(f"hold_{etf}.json", out)
        return out
    except Exception:
        return []
