"""美投讲美股（宏观系列播放列表）的笔记：Summizer 每晚本地转写、Claude 按主题整理成 Markdown，
这里只做解析，原样放进网页，不改写内容。笔记格式见 Summizer 的 YOUTUBE_INSTRUCTIONS.md。"""
import glob
import os
import re

NOTES = os.path.expanduser(os.environ.get("MEITOU_NOTES", "~/Desktop/Summizer/data/youtube/meitou/notes"))
TICKER = re.compile(r"^\s*([A-Z][A-Z.\-]{0,5})(?:\s*[/、]\s*[A-Z][A-Z.\-]{0,5})*\s*[:：]")


def _items(lines):
    """把 Markdown 列表转成 [(层级, 文本)]，保留缩进层级。"""
    out = []
    for ln in lines:
        m = re.match(r"^(\s*)[-*]\s+(.*)$", ln)
        if m:
            out.append((len(m.group(1)) // 2, m.group(2).strip()))
        elif ln.strip() and out:
            out[-1] = (out[-1][0], out[-1][1] + " " + ln.strip())
    return out


def load(limit=12):
    eps = []
    for f in sorted(glob.glob(os.path.join(NOTES, "*.md")), reverse=True)[:limit]:
        text = open(f, encoding="utf-8").read()
        date = os.path.basename(f)[:-3]
        for block in re.split(r"(?m)^## ", text)[1:]:
            lines = block.splitlines()
            title = lines[0].strip()
            video = ""
            m = re.search(r"\((https?://(?:www\.)?youtube\.com/[^)]+)\)", block)
            if m:
                video = m.group(1)
            secs, cur, buf = [], None, []
            for ln in lines[1:]:
                if ln.startswith("### "):
                    if cur:
                        secs.append({"h": cur, "items": _items(buf)})
                    cur, buf = ln[4:].strip(), []
                elif cur:
                    buf.append(ln)
            if cur:
                secs.append({"h": cur, "items": _items(buf)})
            tickers = {}
            for s in secs:
                if "公司" in s["h"]:
                    for lvl, t in s["items"]:
                        mm = TICKER.match(t)
                        if mm:
                            for tk in re.findall(r"[A-Z][A-Z.\-]{0,5}", t[:mm.end()]):
                                tickers.setdefault(tk, t[mm.end():].strip())
            eps.append({"date": date, "title": title, "video": video, "sections": secs,
                        "tickers": tickers, "mtime": int(os.path.getmtime(f))})
    return {"source": "美投讲美股 · 宏观系列", "playlist": "https://www.youtube.com/playlist?list=PLdx_la-W4WdCMcNlGbOdAGCjEQHtoduSW",
            "episodes": eps}
