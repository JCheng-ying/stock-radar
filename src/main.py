"""入口：python -m src.main [--no-push] [--force] [--only AI]

抓数据 -> 算五个面 -> 判断浪位 -> 推送变化 -> 生成 index.html
"""
import argparse
import json
import logging
import re
from datetime import datetime, timezone

from . import alerts, analyze, build_site, config, discover, fetch, news, universe

log = logging.getLogger("stock-radar")


def _summizer(members):
    """从 Summizer 的图谱里取出：各板块的利好/利空信号，以及带来源的公司关系。"""
    try:
        g = json.loads(config.SUMMIZER_GRAPH.read_text())
    except Exception:
        return {}, {}
    nodes = g["nodes"] if isinstance(g["nodes"], dict) else {n["name"]: n for n in g["nodes"]}
    key_ticker = {k: n["ticker"].upper() for k, n in nodes.items() if n.get("ticker")}
    rel = {}
    for e in g.get("edges", []):
        for a, b in ((e["source"], e["target"]), (e["target"], e["source"])):
            t = key_ticker.get(a)
            if t in members and b in nodes:
                ev = (e.get("evidence") or [{}])[-1]
                rel.setdefault(t, []).append({
                    "other": nodes[b]["name"], "type": e["type"], "desc": ev.get("description", ""),
                    "url": ev.get("article_url", ""), "date": (ev.get("published") or "")[:16]})
    signals = {}
    for s in g.get("sector_signals", []):
        signals.setdefault(s["sector"], []).append({
            "desc": s["description"], "title": s.get("article_title", ""), "url": s.get("article_url", ""),
            "src": s.get("source_name", ""), "added": (s.get("added_at") or "")[:10]})
    for v in signals.values():
        v.sort(key=lambda x: x["added"], reverse=True)
    return signals, rel


def _reaction(close, filings):
    """最近一次财报（8-K 条款 2.02）发布前一日收盘 -> 发布后一个交易日收盘 的涨跌。
    盘前和盘后发布都落在这个两日窗口里。"""
    for x in filings:
        if "2.02" in (x.get("codes") or []):
            try:
                idx = close.index.searchsorted(x["date"])
                if 0 < idx < len(close) - 1:
                    return {"date": x["date"], "chg": round(float(close.iloc[idx + 1] / close.iloc[idx - 1] - 1), 4),
                            "url": x["url"]}
                return {"date": x["date"], "chg": None, "url": x["url"]}
            except Exception:
                return None
    return None


def _excerpt(summary, kws, limit=330):
    """从公司主营业务描述里摘出和这个零件相关的原句（命中关键词的句子；没有就取第一句）。"""
    if not summary:
        return ""
    sents = re.split(r"(?<=[.;])\s+(?=[A-Z])", summary)
    rx = [re.compile(r"(?<![a-z])" + re.escape(k.lower()) + r"(?![a-z])") for k in kws]
    hit = [x for x in sents if any(r.search(x.lower()) for r in rx)]
    out = " ".join((hit or sents[:1])[:2])
    return out if len(out) <= limit else out[:limit].rsplit(" ", 1)[0] + "…"


def _attach_insights(sectors, profiles):
    """把 data/insights.json 里人工研究的"产品对比 / 竞合关系"挂到零件上；
    并给每家公司配一段业务描述原文摘录（所有公司都有，自动生成）。"""
    try:
        raw = json.loads((config.DATA / "insights.json").read_text())
    except Exception:
        raw = {}
    ins = {}
    skip = set((raw.pop("_skip", None) or {}).get("keys", []))
    for key, v in raw.items():
        for k in [key] + v.get("also", []):
            ins[k] = {x: v[x] for x in v if x != "also"}
    for s in sectors:
        for ch in s["chains"]:
            for n in ch["nodes"]:
                n["ins"] = ins.get(f"{s['id']}/{ch['id']}/{n['id']}")
                n["skip"] = f"{s['id']}/{ch['id']}/{n['id']}" in skip
                for c in n["cos"]:
                    if c.get("ticker"):
                        c["ex"] = _excerpt((profiles.get(c["ticker"]) or {}).get("summary", ""), n.get("kw", []))


def build_snapshot(force=False, quick=False):
    sectors = universe.load_sectors()
    n_auto = discover.match_parts(sectors)          # 全市场扫描结果按关键词挂到零件上
    log.info("全市场自动匹配：新增 %d 条 零件-公司 关系", n_auto)
    profiles = discover.load_profiles()
    _attach_insights(sectors, profiles)
    members = universe.memberships(sectors)
    tickers = sorted(members)
    theme_etfs = universe.sector_etfs(sectors)
    try:
        notes = json.loads(config.NOTES.read_text())
    except Exception:
        notes = {}

    log.info("行情：%d 只股票 + ETF/宏观", len(tickers))
    igv_hold = fetch.fetch_etf_holdings("IGV")
    px = fetch.fetch_prices(tickers + list(fetch.MACRO) + list(fetch.SPDR) + list(theme_etfs)
                            + fetch.LEVERAGED + fetch.LEVERAGE_BASE + ["IGV", "SMH"] + igv_hold)
    spy = px["SPY"]["Close"]
    log.info("基本面与一致预期")
    funds = fetch.fetch_fundamentals(tickers, force=force, cache_only=quick)
    log.info("SEC RPO")
    rpo = fetch.fetch_rpo(tickers, force=force)
    log.info("SEC 8-K 重大事件")
    filings = fetch.fetch_filings(tickers, force=force)
    fg = fetch.fetch_fear_greed()
    sig, rel = _summizer(members)

    companies = {}
    for t in tickers:
        if t not in px:
            log.warning("没有行情，跳过：%s", t)
            continue
        f = funds.get(t) or {"info": {}, "quarters": []}
        tc = analyze.tech(px[t], spy)
        fu = analyze.fundamentals(f, rpo.get(t))
        ex = analyze.expectation(f, tc["price"])
        ca = analyze.capital(f, tc)
        h, hwhy = analyze.heat(tc, ca)
        info = f.get("info") or {}
        fl = filings.get(t, [])
        ex["estimates"] = f.get("estimates") or {}
        ex["reaction"] = _reaction(px[t]["Close"], fl)
        val = analyze.forward_valuation(f, tc["price"], str(px[t].index[-1].date()))
        companies[t] = {
            "name": info.get("shortName") or info.get("longName") or (profiles.get(t) or {}).get("short") or t,
            "industry": info.get("industry", ""),
            "about": ((profiles.get(t) or {}).get("summary") or "")[:420],
            "tech": tc, "fund": fu, "expect": ex, "capital": ca, "val": val,
            "trend": (notes.get("trend") or {}).get(t), "heat": h, "heat_why": hwhy,
            "members": members[t], "news": f.get("news") or [], "filings": [x for x in fl if not x.get("report")],
            "reports": [x for x in fl if x.get("report")], "fetched": (f.get("fetched") or "")[:16], "relations": (rel.get(t) or [])[:12],
            "notes": (notes.get("companies") or {}).get(t, []),
            "spark": [round(float(x), 2) for x in px[t]["Close"].tail(config.SPARK_DAYS).tolist()],
            "spark_end": str(px[t].index[-1].date()),
        }

    log.info("多来源新闻")
    focus = [d["t"] for d in (notes.get("daily") or [{}])[0].get("watch", [])] if notes.get("daily") else []
    focus += sorted((t for t, c in companies.items() if not all(m.get("auto") for m in c["members"])),
                    key=lambda t: -((funds.get(t) or {}).get("info", {}).get("marketCap") or 0))[:40]
    feed = news.collect(companies, focus=list(dict.fromkeys(focus)))
    for x in feed:
        for t in x["tickers"]:
            c = companies.get(t)
            if c is not None:
                c.setdefault("mnews", []).append({k: x[k] for k in ("title", "url", "src", "date")})
    for c in companies.values():
        c["mnews"] = (c.get("mnews") or [])[:10]
    sentiment = analyze.market_sentiment(px, fg, companies, fetch.LEVERAGED, fetch.LEVERAGE_BASE,
                                         notes.get("margin_debt"))
    for c in companies.values():
        fm, em = c["fund"].get("fcf_margin"), c["fund"].get("em")
        burning = (fm is not None and fm < -0.5) or (em is not None and em < -0.5)
        c["signal"] = analyze.position_signal(c["fund"].get("score"), c["expect"].get("score"),
                                              c["heat"], sentiment.get("heat"), burning)

    out_sectors = []
    for s in sectors:
        signals = []
        for tag in s.get("summizer_tags", [])[:1]:
            signals += sig.get(tag, [])
        out_sectors.append({
            "id": s["id"], "name": s["name"], "industry": s["industry"], "etfs": s.get("etfs", {}),
            "chains": s["chains"], "wave_status": analyze.wave_status(s, companies, px, spy),
            "signals": signals[:25], "notes": (notes.get("sectors") or {}).get(s["id"], []),
        })

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": str(spy.index[-1].date()),
        "market": {
            "funding": analyze.macro_funding(px),
            "sentiment": sentiment,
            "spdr": analyze.etf_flows(px, fetch.SPDR),
            "themes": analyze.etf_flows(px, {**theme_etfs, "IGV": "软件", "SMH": "半导体", "QQQ": "纳指100", "IWM": "罗素2000"}),
            "igv": analyze.igv_check(px, igv_hold, companies),
        },
        "sectors": out_sectors,
        "companies": companies,
        "daily": (notes.get("daily") or [])[:14],
        "newsfeed": feed[:250],
        "notes_updated": notes.get("updated", ""),
    }


def _apply_notes(snap):
    """把 data/notes.json 和 data/insights.json 的最新内容覆盖到已有 snapshot 上（--rebuild 用）。"""
    try:
        notes = json.loads(config.NOTES.read_text())
    except Exception:
        notes = {}
    snap["daily"] = (notes.get("daily") or [])[:14]
    snap["notes_updated"] = notes.get("updated", "")
    for t, c in snap.get("companies", {}).items():
        c["trend"] = (notes.get("trend") or {}).get(t)
        c["notes"] = (notes.get("companies") or {}).get(t, [])
    for s in snap.get("sectors", []):
        s["notes"] = (notes.get("sectors") or {}).get(s.get("id"), [])
    _attach_insights(snap.get("sectors", []), discover.load_profiles())


def refresh_news(snap):
    """只重抓新闻：更新 snapshot 的 newsfeed 和各公司 mnews（行情 / 财报不动）。"""
    from datetime import datetime, timezone
    companies = snap["companies"]
    focus = [d["t"] for d in (snap.get("daily") or [{}])[0].get("watch", [])] if snap.get("daily") else []
    focus += [t for t, c in companies.items() if not all(m.get("auto") for m in c.get("members", []))][:40]
    feed = news.collect(companies, focus=list(dict.fromkeys(focus)), cache_hours=0.3)
    for c in companies.values():
        c["mnews"] = []
    for x in feed:
        for t in x["tickers"]:
            if t in companies:
                companies[t]["mnews"].append({k: x[k] for k in ("title", "url", "src", "date")})
    for c in companies.values():
        c["mnews"] = c["mnews"][:10]
    snap["newsfeed"] = feed[:250]
    snap["news_refreshed"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info("新闻已刷新：%d 条", len(feed))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true", help="只生成网页，不检测变化、不推送")
    ap.add_argument("--force", action="store_true", help="忽略缓存，重新拉取基本面")
    ap.add_argument("--quick", action="store_true", help="只刷新行情和 SEC，财报/预期用现有缓存（几分钟内跑完）")
    ap.add_argument("--todo", action="store_true", help="列出还没做人工研究的零件")
    ap.add_argument("--rebuild", action="store_true", help="不联网，用上次的 snapshot.json 重新生成网页")
    ap.add_argument("--news", action="store_true", help="只刷新多来源新闻（约 1 分钟），其余用上次的 snapshot.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    config.load_env()
    if args.todo:
        snap = json.loads(config.SNAPSHOT.read_text())
        rows = []
        for sec in snap["sectors"]:
            lv = {w["wave"]: w["level"] for w in sec["wave_status"]}
            for ch in sec["chains"]:
                for n in ch["nodes"]:
                    k = sum(1 for c in n["cos"] if c.get("ticker") and not c.get("auto"))
                    if not n.get("ins") and not n.get("skip") and k >= 3:
                        rows.append((-(lv.get(n["wave"], 0)), -k, f"{sec['id']}/{ch['id']}/{n['id']}", n["name"]))
        for a_, k, key, name in sorted(rows):
            print(f"{key:38s} {name}（{-k} 家，浪位等级 {-a_}）")
        print(f"共 {len(rows)} 个零件待研究")
        return
    if args.news:
        snap = json.loads(config.SNAPSHOT.read_text())
        _apply_notes(snap)
        refresh_news(snap)
        config.SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False))
    elif args.rebuild:
        snap = json.loads(config.SNAPSHOT.read_text())
        _apply_notes(snap)          # 研判 / 零件研究写进 json 后，不联网也能更新到网页
    else:
        snap = build_snapshot(force=args.force, quick=args.quick)
        if not args.no_push:
            for a in alerts.run(snap):
                log.info("推送[%s] %s", "已发" if a["sent"] else "仅记录", a["title"])
        config.SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False))
    snap["alerts"] = alerts.recent()
    build_site.build(snap)
    log.info("完成：%d 家公司 -> %s", len(snap["companies"]), config.SITE)


if __name__ == "__main__":
    main()
