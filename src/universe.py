"""加载 data/chains/*.json，整理出板块 -> 产业链 -> 环节 -> 公司的结构。

公司写法："NVDA|备注" 表示美股代码；"?MSFT|备注" 表示相关但业务多元、
这个板块只占它一小部分的公司（照常展示，但不计入浪位统计）；"~OpenAI|备注" 表示没有美股代码
（未上市 / 海外上市），只展示名字，不拉数据。
"""
import json

from .config import CHAINS_DIR

SECTOR_ORDER = ["ai", "quantum", "robotics", "aerospace", "autonomous"]


def _parse_company(raw):
    head, _, note = raw.partition("|")
    if head.startswith("~"):
        return {"name": head[1:].strip(), "ticker": None, "note": note.strip()}
    core = not head.startswith("?")
    tk = head.lstrip("?").strip().upper()
    return {"name": tk, "ticker": tk, "note": note.strip(), "core": core}


def load_sectors():
    sectors = []
    for sid in SECTOR_ORDER:
        f = CHAINS_DIR / f"{sid}.json"
        if not f.exists():
            continue
        s = json.loads(f.read_text())
        for chain in s["chains"]:
            for node in chain["nodes"]:
                node["cos"] = [_parse_company(c) for c in node.get("cos", [])]
        sectors.append(s)
    return sectors


def memberships(sectors):
    """ticker -> [{sector, chain, node, wave, note}]"""
    out = {}
    for s in sectors:
        for chain in s["chains"]:
            for node in chain["nodes"]:
                for c in node["cos"]:
                    if c["ticker"]:
                        out.setdefault(c["ticker"], []).append({
                            "sector": s["id"], "chain": chain["id"], "node": node["id"],
                            "node_name": node["name"], "wave": node["wave"], "note": c["note"],
                            "core": c["core"], "sector_name": s["name"], "chain_name": chain["name"]})
    return out


def sector_etfs(sectors):
    etfs = {}
    for s in sectors:
        etfs.update(s.get("etfs", {}))
    return etfs
