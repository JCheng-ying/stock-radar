"""推送：对比上一次的状态，只在"发生变化"的时候发通知（ntfy.sh）。

没配置 NTFY_TOPIC 就只记录到 data/alert_log.jsonl 和网页的"推送记录"里，不发手机。
ntfy 的 topic 名相当于密码（知道名字的人都能订阅），请用一串随机字符。
"""
import json
import logging
import os
from datetime import date, datetime, timezone

import requests

from . import config

log = logging.getLogger(__name__)
PRIORITY = {"high": 4, "normal": 3, "low": 2}


def _load_state():
    try:
        return json.loads(config.ALERT_STATE.read_text())
    except Exception:
        return None


def send(title, body, priority="normal", tags=None, click=None):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    sent = False
    if topic:
        try:
            payload = {"topic": topic, "title": title, "message": body,
                       "priority": PRIORITY.get(priority, 3), "tags": tags or []}
            if click:
                payload["click"] = click
            r = requests.post(os.environ.get("NTFY_URL", "https://ntfy.sh"),
                              data=json.dumps(payload).encode("utf-8"), timeout=15)
            sent = r.ok
        except Exception:
            log.exception("ntfy push failed")
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "title": title,
             "body": body, "priority": priority, "sent": sent}
    with open(config.ALERT_LOG, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def recent(n=80):
    try:
        lines = config.ALERT_LOG.read_text().splitlines()[-n:]
        return [json.loads(x) for x in reversed(lines)]
    except Exception:
        return []


def _pct(x):
    return "—" if x is None else f"{x:+.0%}"


def detect(snap):
    """返回 [(title, body, priority, tags)]，并把新状态写回磁盘。首次运行只建立基线。"""
    prev = _load_state()
    # 只对人工确认的公司推送（全市场自动匹配进来的公司只在网页上展示）
    cos = {t: c for t, c in snap["companies"].items() if not all(m.get("auto") for m in c["members"])}
    sent = snap["market"]["sentiment"]
    state = {
        "q_end": {t: c["fund"].get("q_end") for t, c in cos.items()},
        "signal": {t: c["signal"]["label"] for t, c in cos.items()},
        "val27": {t: (c.get("val") or {}).get("zone27") for t, c in cos.items()},
        "wave": {f"{s['id']}:{w['wave']}": w["stage"] for s in snap["sectors"] for w in s["wave_status"]},
        "vix_zone": _vix_zone((sent.get("vix") or {}).get("last")),
        "mkt_zone": sent.get("zone"),
        "igv": bool((snap["market"].get("igv") or {}).get("rising") and (snap["market"].get("igv") or {}).get("diverging")),
        "earn_warned": (prev or {}).get("earn_warned", {}),
        "moved": (prev or {}).get("moved", {}),
    }
    events = []
    today = date.today()

    if prev is not None:
        # 1) 新财报落地
        for t, c in cos.items():
            old, new = prev["q_end"].get(t), state["q_end"].get(t)
            if old and new and new > old:
                f, e = c["fund"], c["expect"]
                body = (f"{c['name']} 截至 {new} 的季度：收入同比 {_pct(f.get('rev_yoy'))}；"
                        f"毛利率 {f['gm']:.1%}（同比 {((f.get('gm_yoy') or 0) * 100):+.1f}pp）；" if f.get("gm") is not None else f"{c['name']} 新季度财报：")
                if f.get("em") is not None:
                    body += f"EBITDA 利润率 {f['em']:.1%}；"
                if e.get("last_surprise") is not None:
                    body += f"EPS 较预期 {_pct(e['last_surprise'])}；"
                if f.get("rpo_yoy") is not None:
                    body += f"RPO 同比 {_pct(f['rpo_yoy'])}；"
                body += f"当前信号：{c['signal']['label']}（基本面 {f.get('score')} 分）"
                events.append((f"📊 {t} 财报更新", body, "high", ["bar_chart"]))
        # 2) 点位信号变化
        for t, c in cos.items():
            old, new = prev["signal"].get(t), state["signal"][t]
            if old and new != old and new in ("关注买点", "回调风险"):
                icon = "🟢" if new == "关注买点" else "🔴"
                events.append((f"{icon} {t} 进入「{new}」", f"{c['name']}：{c['signal']['text']}（此前：{old}）\n"
                               + "；".join(c["heat_why"][:3]), "high" if new == "关注买点" else "normal",
                               ["dart" if new == "关注买点" else "warning"]))
        # 2b) 现价跌破 2027 前瞻估值区间下沿
        for t, c in cos.items():
            old, new = (prev.get("val27") or {}).get(t), state["val27"].get(t)
            if old and new == "低于下沿" and old != new:
                v = c["val"]
                y = next((r for r in v["years"] if r["y"] == 2027), None)
                tr = (c.get("trend") or {}).get("dir")
                events.append((f"💎 {t} 跌破 2027 前瞻估值下沿",
                               f"{c['name']} 现价 {c['tech']['price']}，2027 前瞻区间 {y['px'][0]}~{y['px'][2]}（中值 {y['px'][1]}，空间 {v['up27mid']:+.0%}）。"
                               + (f"趋势判断：{tr}。" if tr else "") + "前瞻估值以分析师预期兑现为前提。", "high", ["gem"]))
        # 3) 浪的位置变化
        for s in snap["sectors"]:
            for w in s["wave_status"]:
                k = f"{s['id']}:{w['wave']}"
                old = prev["wave"].get(k)
                if old and old != w["stage"] and "样本不足" not in (old, w["stage"]):
                    ok = "、".join(c["name"] for c in w["checks"] if c["ok"])
                    events.append((f"🌊 {s['name']} {w['name'].split('：')[0]}：{w['stage']}",
                                   f"此前：{old}。已满足：{ok or '无'}", "high", ["ocean"]))
        # 4) 情绪面极端值
        if state["vix_zone"] != prev.get("vix_zone") and state["vix_zone"] != "正常":
            v = sent["vix"]["last"]
            events.append((f"😱 VIX {v:.1f}：{state['vix_zone']}",
                           next((p["text"] for p in sent["parts"] if p["name"].startswith("VIX")), ""), "high", ["rotating_light"]))
        if state["mkt_zone"] != prev.get("mkt_zone") and state["mkt_zone"] in ("极度悲观", "极度乐观"):
            events.append((f"🌡 市场情绪：{state['mkt_zone']}", sent.get("verdict", ""), "high", ["thermometer"]))
        # 5) IGV 分化上涨（二浪的价格信号）出现 / 消失
        if state["igv"] != prev.get("igv"):
            events.append(("🧩 IGV " + ("出现分化上涨" if state["igv"] else "分化上涨信号消失"),
                           snap["market"]["igv"]["verdict"], "normal", ["jigsaw"]))

    # 6) 财报日临近（三天内，每个财报日只提醒一次）
    for t, c in cos.items():
        ne = c["expect"].get("next_earnings")
        if not ne:
            continue
        try:
            days = (date.fromisoformat(ne) - today).days
        except Exception:
            continue
        if 0 <= days <= 3 and state["earn_warned"].get(t) != ne:
            state["earn_warned"][t] = ne
            if prev is not None:
                e = c["expect"]
                est = f"，一致预期 EPS {e['eps_est']:.2f}" if e.get("eps_est") is not None else ""
                events.append((f"📅 {t} {'今天' if days == 0 else f'{days} 天后'}发财报",
                               f"{c['name']} {ne}{est}。当前信号：{c['signal']['label']}，情绪热度 {c['heat']}。", "normal", ["calendar"]))
    # 7) 单日大幅波动（同一天只提醒一次）
    day_key = snap["as_of"]
    for t, c in cos.items():
        chg = c["tech"].get("chg1d")
        if chg is not None and abs(chg) >= 0.08 and state["moved"].get(t) != day_key:
            state["moved"][t] = day_key
            if prev is not None:
                events.append((f"{'🚀' if chg > 0 else '💥'} {t} 单日 {chg:+.1%}",
                               f"{c['name']} 收于 {c['tech']['price']}。信号：{c['signal']['label']}。", "low", []))

    config.ALERT_STATE.write_text(json.dumps(state, ensure_ascii=False))
    if prev is None:
        events = [("✅ stock-radar 已建立基线", f"开始跟踪 {len(cos)} 家公司、{len(snap['sectors'])} 个板块。之后只在财报、信号、浪位、情绪发生变化时推送。", "low", ["white_check_mark"])]
    return events


def _vix_zone(v):
    if v is None:
        return "正常"
    return "极度恐慌(≥30)" if v >= 30 else "恐慌升温(≥22)" if v >= 22 else "过度平静(≤13)" if v <= 13 else "正常"


def run(snap, max_push=12):
    events = detect(snap)
    order = {"high": 0, "normal": 1, "low": 2}
    events.sort(key=lambda e: order[e[2]])
    if len(events) > 30:
        # 很久没刷新后一次性积压的变化：合并成一条分类摘要，而不是按字母顺序推 12 条
        groups = {}
        for t, b, p, tags in events:
            k = t.split(" ")[0]
            groups.setdefault(k, []).append(t.split(" ", 1)[1] if " " in t else t)
        body = "\n".join(f"{k} {len(v)} 条：" + "、".join(x.split(" ")[0] for x in v[:12]) + ("…" if len(v) > 12 else "")
                         for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1])))
        return [send(f"🔄 数据已追上，期间共 {len(events)} 条变化", body + "\n详情见网页「推送记录」和各公司页面。", "normal",
                     ["arrows_counterclockwise"], click=os.environ.get("SITE_URL"))]
    out = [send(t, b, p, tags, click=os.environ.get("SITE_URL")) for t, b, p, tags in events[:max_push]]
    rest = events[max_push:]
    if rest:
        out.append(send(f"还有 {len(rest)} 条更新", "\n".join(e[0] for e in rest[:30]), "low"))
    return out
