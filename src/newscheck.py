"""检查自上次研判（notes.json 的 updated）以来有没有值得写进研判的新信息。

    python -m src.newscheck            打印摘要，最后一行是 MATERIAL 或 NOTHING
    python -m src.newscheck --json     输出 JSON（给定时任务读）

三类来源：Summizer 新文章（按文件修改时间）、alert_log 里的重要推送、snapshot.newsfeed 里
主流来源且涉及人工确认公司 / 含重大事件关键词的新闻。是否“值得写”最终由 Claude 判断，
这里只做初筛，避免没有新东西时也去改网页。
"""
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

from . import config

SUMMIZER = os.path.expanduser(os.environ.get("SUMMIZER_ARTICLES", "~/Desktop/Summizer/data/articles"))
RHINO = os.path.expanduser(os.environ.get("RHINO_NOTES", "~/Desktop/Summizer/data/youtube/rhino/notes"))


def blogger_pending():
    """视野环球财经笔记里，还没整理进 data/blogger.json、或整理后又被改过的日期文件。"""
    try:
        done = {d["date"]: d.get("note_mtime", 0) for d in json.loads((config.DATA / "blogger.json").read_text())["days"]}
    except Exception:
        done = {}
    out = []
    for f in sorted(glob.glob(os.path.join(RHINO, "*.md"))):
        day = os.path.basename(f)[:-3]
        if int(os.path.getmtime(f)) > done.get(day, -1):
            out.append(f)
    return out
MAJOR = ("Reuters", "Bloomberg", "CNBC", "WSJ", "Wall Street Journal", "Financial Times", "FT", "MarketWatch",
         "Barron", "DataCenterDynamics", "SpaceNews", "Quantum Insider", "Robot Report", "Electrek",
         "Tom's Hardware", "The Information", "TechCrunch", "Axios", "Seeking Alpha")
BIG = re.compile(r"\b(earnings|results|guidance|outlook|forecast|beats?|miss(es)?|CEO|CFO|resign|ousted|steps? down|"
                 r"acqui|merger|deal|buyout|IPO|probe|investigat|indict|charged|lawsuit|sued|SEC|DOJ|FTC|ban|"
                 r"export|tariff|contract|order|award|backlog|capex|layoff|recall|crash|explod|failure|downgrade|"
                 r"upgrade|halt|bankrupt|default|offering|raise[sd]?\b)", re.I)
ALERT_KEYS = ("财报更新", "浪", "VIX", "情绪", "IGV", "下沿", "资金面")


def watermark():
    try:
        u = json.loads(config.NOTES.read_text()).get("updated")
        return datetime.fromisoformat(u).astimezone()  # 本地时间
    except Exception:
        return datetime.now().astimezone().replace(hour=0, minute=0)


def check():
    wm = watermark()
    wm_ts = wm.timestamp()
    arts = [f for f in sorted(glob.glob(os.path.join(SUMMIZER, "*.md"))) if os.path.getmtime(f) > wm_ts]

    alerts = []
    try:
        for line in config.ALERT_LOG.read_text().splitlines():
            r = json.loads(line)
            ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            if ts.timestamp() > wm_ts and any(k in r.get("title", "") for k in ALERT_KEYS) \
                    and not r.get("title", "").startswith("📝"):
                alerts.append({"ts": ts.astimezone().strftime("%m-%d %H:%M"), "title": r["title"],
                               "body": (r.get("body") or "")[:200]})
    except Exception:
        pass

    snap = json.loads(config.SNAPSHOT.read_text())
    C = snap.get("companies", {})
    manual = {t for t, c in C.items() if not all(m.get("auto") for m in c.get("members", []))}
    watch = {w["t"] for w in ((snap.get("daily") or [{}])[0].get("watch") or [])}
    news = []
    for x in snap.get("newsfeed", []):
        try:
            ts = datetime.fromisoformat(x["ts"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts.timestamp() <= wm_ts or not any(m in x.get("src", "") for m in MAJOR):
            continue
        tk = [t for t in x.get("tickers", []) if t in manual]
        big = bool(BIG.search(x["title"]))
        if (tk and big) or (set(tk) & watch) or (big and x.get("sectors")):
            news.append({"ts": ts.astimezone().strftime("%m-%d %H:%M"), "src": x["src"], "title": x["title"],
                         "url": x["url"], "tickers": tk, "big": big})
    blog = blogger_pending()
    material = bool(blog) or bool(arts) or bool(alerts) or sum(1 for n in news if n["big"]) >= 2 or len(news) >= 4
    return {"since": wm.strftime("%Y-%m-%dT%H:%M"), "now": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "snapshot_generated": snap.get("generated"), "news_refreshed": snap.get("news_refreshed"),
            "summizer_articles": arts, "blogger_notes": blog, "alerts": alerts, "news": news[:60], "material": material}


def main():
    config.load_env()
    r = check()
    if "--json" in sys.argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    print(f"上次研判：{r['since']}  现在：{r['now']}  行情快照：{r['snapshot_generated']}  新闻刷新：{r['news_refreshed']}")
    print(f"Summizer 新文章 {len(r['summizer_articles'])} 篇")
    for f in r["summizer_articles"]:
        print("  ", f)
    print(f"博主笔记待整理 {len(r['blogger_notes'])} 个")
    for f in r["blogger_notes"]:
        print("  ", f)
    print(f"重要推送 {len(r['alerts'])} 条")
    for a in r["alerts"]:
        print("  ", a["ts"], a["title"], "|", a["body"][:120].replace("\n", " "))
    print(f"相关新闻 {len(r['news'])} 条")
    for n in r["news"]:
        print("  ", n["ts"], n["src"][:20], "|", n["title"][:110], n["tickers"], "★" if n["big"] else "")
    print("MATERIAL" if r["material"] else "NOTHING")


if __name__ == "__main__":
    main()
