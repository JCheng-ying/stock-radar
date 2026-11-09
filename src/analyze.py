"""把原始数据变成五个面的打分、三浪位置和点位信号。

所有判断都是写死的、可追溯的规则——每个分数都带一组"依据"文字，网页上
原样展示，方便核对"为什么是这个结论"。这是按固定框架做的信息整理，不是投资建议。
"""
import statistics as st

import numpy as np
import pandas as pd


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def _clip(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def _ret(close, days):
    if len(close) <= days:
        return None
    return float(close.iloc[-1] / close.iloc[-1 - days] - 1)


def _pctile(series, value):
    s = series.dropna()
    return float((s <= value).mean() * 100) if len(s) else None


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return float(100 - 100 / (1 + up.iloc[-1] / dn.iloc[-1])) if dn.iloc[-1] else 100.0


def _median(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def _share(xs, pred):
    xs = [x for x in xs if x is not None]
    return (sum(1 for x in xs if pred(x)) / len(xs)) if xs else None


# ---------- 单只股票的技术/资金数据 ----------

def tech(df, spy):
    c, v = df["Close"], df["Volume"]
    last = float(c.iloc[-1])
    ma50 = float(c.tail(50).mean())
    ma200 = float(c.tail(200).mean()) if len(c) >= 200 else None
    yr = c.tail(252)
    hi, lo = float(yr.max()), float(yr.min())
    dv = (c * v)
    dv20, dv60 = float(dv.tail(20).mean()), float(dv.tail(60).mean())
    out = {
        "price": _r(last, 2), "chg1d": _r(_ret(c, 1)), "r1w": _r(_ret(c, 5)),
        "r1m": _r(_ret(c, 21)), "r3m": _r(_ret(c, 63)), "r6m": _r(_ret(c, 126)), "r1y": _r(_ret(c, 252)),
        "ma50": _r(ma50, 2), "ma200": _r(ma200, 2),
        "vs50": _r(last / ma50 - 1), "vs200": _r(last / ma200 - 1) if ma200 else None,
        "hi52": _r(hi, 2), "lo52": _r(lo, 2),
        "pos52": _r((last - lo) / (hi - lo)) if hi > lo else None,
        "dd": _r(last / hi - 1), "rsi": _r(rsi(c), 1),
        "flow": _r(dv20 / dv60 - 1) if dv60 else None,   # 近20日成交额相对近60日：放量/缩量
        "dv20": _r(dv20, 0),
    }
    for k, d in (("rs1m", 21), ("rs3m", 63)):
        a, b = _ret(c, d), _ret(spy, d)
        out[k] = _r(a - b) if a is not None and b is not None else None
    return out


# ---------- 基本面 ----------

def fundamentals(f, rpo):
    q = f.get("quarters") or []
    info = f.get("info") or {}
    m = {"quarters": []}
    def ratios(x):
        rev = x.get("rev")
        d = dict(x)
        for key, src in (("gm", "gp"), ("em", "ebitda"), ("om", "op"), ("nm", "ni"), ("rdp", "rd"), ("fcfm", "fcf")):
            d[key] = _r(x[src] / rev) if x.get(src) is not None and rev else None
        return d

    m["quarters"] = [ratios(x) for x in q]
    m["annual"] = [ratios(x) for x in (f.get("annual") or [])]
    qs = m["quarters"]
    cur = qs[-1] if qs else {}
    prev = qs[-2] if len(qs) >= 2 else {}
    yago = qs[-5] if len(qs) >= 5 else {}

    def growth(a, b):
        return _r(a / b - 1) if a is not None and b and b > 0 else None

    def delta(a, b):
        return _r(a - b) if a is not None and b is not None else None

    m["q_end"] = cur.get("end")
    m["rev_yoy"] = growth(cur.get("rev"), yago.get("rev")) if yago else _r(info.get("revenueGrowth"))
    m["rev_qoq"] = growth(cur.get("rev"), prev.get("rev"))
    m["gm"] = cur.get("gm") if cur.get("gm") is not None else _r(info.get("grossMargins"))
    m["gm_yoy"] = delta(cur.get("gm"), yago.get("gm"))
    m["gm_qoq"] = delta(cur.get("gm"), prev.get("gm"))
    m["em"] = cur.get("em") if cur.get("em") is not None else _r(info.get("ebitdaMargins"))
    m["em_yoy"] = delta(cur.get("em"), yago.get("em"))
    m["em_qoq"] = delta(cur.get("em"), prev.get("em"))
    m["om"] = cur.get("om")
    m["eps"] = cur.get("eps")
    e0, e4 = cur.get("eps"), yago.get("eps")
    m["eps_yoy"] = _r((e0 - e4) / abs(e4)) if e0 is not None and e4 not in (None, 0) else None
    fcfs = [x["fcf"] for x in qs[-4:] if x.get("fcf") is not None]
    revs = [x["rev"] for x in qs[-4:] if x.get("rev")]
    m["fcf_ttm"] = sum(fcfs) if len(fcfs) == 4 else None
    m["fcf_margin"] = _r(m["fcf_ttm"] / sum(revs)) if m["fcf_ttm"] is not None and len(revs) == 4 else None
    m["net_cash"] = (info.get("totalCash") or 0) - (info.get("totalDebt") or 0) if info.get("totalCash") is not None else None

    if rpo:
        m["rpo"] = rpo[-6:]
        m["rpo_latest"] = rpo[-1]["val"]
        m["rpo_qoq"] = growth(rpo[-1]["val"], rpo[-2]["val"]) if len(rpo) >= 2 else None
        m["rpo_yoy"] = growth(rpo[-1]["val"], rpo[-5]["val"]) if len(rpo) >= 5 else None

    # 打分：增长 30 + 毛利率趋势 20 + 经营效率 20 + EPS 15 + 自由现金流 15
    score, why, known = 50.0, [], 0
    g = m["rev_yoy"]
    if g is not None:
        known += 1
        score += _clip(g * 60, -15, 15)
        why.append(f"收入同比 {g:+.0%}")
    d = m["gm_yoy"] if m["gm_yoy"] is not None else m["gm_qoq"]
    if d is not None:
        known += 1
        score += _clip(d * 300, -10, 10)
        why.append(f"毛利率 {m['gm']:.1%}，{'同比' if m['gm_yoy'] is not None else '环比'}{d * 100:+.1f}pp（产品力）")
    d = m["em_yoy"] if m["em_yoy"] is not None else m["em_qoq"]
    if d is not None:
        known += 1
        score += _clip(d * 200, -10, 10)
        why.append(f"EBITDA 利润率 {m['em']:.1%}，{'同比' if m['em_yoy'] is not None else '环比'}{d * 100:+.1f}pp（经营效率）")
    if m["eps_yoy"] is not None:
        known += 1
        score += _clip(m["eps_yoy"] * 15, -7.5, 7.5)
        why.append(f"EPS {m['eps']:.2f}，同比 {m['eps_yoy']:+.0%}")
    if m["fcf_margin"] is not None:
        known += 1
        score += _clip(m["fcf_margin"] * 40, -7.5, 7.5)
        why.append(f"近四季自由现金流率 {m['fcf_margin']:+.0%}")
    if m.get("rpo_yoy") is not None:
        why.append(f"RPO（长期订单）同比 {m['rpo_yoy']:+.0%}")
        score += _clip(m["rpo_yoy"] * 10, -3, 5)
    m["score"] = round(_clip(score)) if known >= 2 else None
    m["why"] = why
    return m


# ---------- 预期面 ----------

def expectation(f, price):
    info = f.get("info") or {}
    e = {"target": info.get("targetMeanPrice"), "target_hi": info.get("targetHighPrice"),
         "target_lo": info.get("targetLowPrice"), "analysts": info.get("numberOfAnalystOpinions"),
         "rec": info.get("recommendationMean"), "next_earnings": f.get("next_earnings"),
         "eps_est": f.get("eps_est"), "rev_est": f.get("rev_est"), "surprises": f.get("surprises") or []}
    e["upside"] = _r(e["target"] / price - 1) if e["target"] and price else None
    tr = f.get("eps_trend") or {}

    def rev(period):
        x = tr.get(period) or {}
        return _r((x["now"] - x["d90"]) / abs(x["d90"])) if x.get("now") is not None and x.get("d90") else None

    e["rev_cy"], e["rev_ny"] = rev("0y"), rev("+1y")
    last = e["surprises"][-1] if e["surprises"] else {}
    e["last_surprise"] = last.get("pct")
    fe, te = info.get("forwardEps"), info.get("trailingEps")
    e["eps_fwd_growth"] = _r((fe - te) / abs(te)) if fe is not None and te else None

    score, why, known = 50.0, [], 0
    if e["upside"] is not None and (e["analysts"] or 0) >= 3:
        known += 1
        score += _clip(e["upside"] * 80, -20, 25)
        why.append(f"现价距一致目标价 {e['upside']:+.0%}（{e['analysts']} 位分析师）")
    r = e["rev_ny"] if e["rev_ny"] is not None else e["rev_cy"]
    if r is not None:
        known += 1
        score += _clip(r * 100, -15, 15)
        why.append(f"近90天盈利预测{'上修' if r >= 0 else '下修'} {abs(r):.0%}——预期在{'抬升' if r >= 0 else '走低'}")
    if e["last_surprise"] is not None:
        known += 1
        score += _clip(e["last_surprise"] * 100, -8, 8)
        why.append(f"上次财报 EPS {'超' if e['last_surprise'] >= 0 else '低于'}预期 {abs(e['last_surprise']):.0%}")
    if e["rec"] is not None:
        score += _clip((2.5 - e["rec"]) * 4, -5, 5)
    e["score"] = round(_clip(score)) if known else None
    e["why"] = why
    return e


# ---------- 资金面（个股层面：贵不贵 + 钱有没有进来） ----------

def capital(f, t):
    info = f.get("info") or {}
    c = {"fpe": _r(info.get("forwardPE"), 1), "pe": _r(info.get("trailingPE"), 1),
         "ps": _r(info.get("priceToSalesTrailing12Months"), 1),
         "ev_ebitda": _r(info.get("enterpriseToEbitda"), 1), "peg": _r(info.get("trailingPegRatio"), 2),
         "mcap": info.get("marketCap"), "inst": _r(info.get("heldPercentInstitutions")),
         "short": _r(info.get("shortPercentOfFloat"))}
    score, why = 50.0, []
    if c["peg"] and c["peg"] > 0:
        score += _clip((1.5 - c["peg"]) * 12, -15, 15)
        why.append(f"PEG {c['peg']}（<1 偏便宜，>2 偏贵）")
    elif c["fpe"] and c["fpe"] > 0:
        score += _clip((30 - c["fpe"]) * 0.5, -15, 15)
        why.append(f"远期市盈率 {c['fpe']}x")
    elif c["ps"]:
        score += _clip((10 - c["ps"]) * 1.0, -15, 10)
        why.append(f"尚未盈利，市销率 {c['ps']}x")
    if t.get("flow") is not None:
        score += _clip(t["flow"] * 30, -10, 10)
        why.append(f"近20日成交额较60日均值 {t['flow']:+.0%}（{'放量' if t['flow'] > 0 else '缩量'}）")
    if t.get("rs3m") is not None:
        score += _clip(t["rs3m"] * 50, -12, 12)
        why.append(f"近3个月相对标普500 {t['rs3m']:+.0%}")
    c["score"] = round(_clip(score))
    c["why"] = why
    return c


# ---------- 情绪面（个股热度：0 极度悲观 ~ 100 极度乐观） ----------

def heat(t, c):
    parts, why = [], []
    if t.get("rsi") is not None:
        parts.append(_clip((t["rsi"] - 30) / 40 * 100))
        why.append(f"RSI {t['rsi']:.0f}")
    if t.get("pos52") is not None:
        parts.append(t["pos52"] * 100)
        why.append(f"处于52周区间的 {t['pos52']:.0%} 位置")
    if t.get("vs200") is not None:
        parts.append(_clip(50 + t["vs200"] * 125))
        why.append(f"偏离200日线 {t['vs200']:+.0%}")
    if t.get("dd") is not None and t["dd"] < -0.2:
        why.append(f"距52周高点回撤 {t['dd']:.0%}")
    if c.get("short") is not None and c["short"] > 0.1:
        why.append(f"做空比例 {c['short']:.0%}，分歧大")
    return (round(sum(parts) / len(parts)) if parts else None), why


def position_signal(fs, es, h, mkt_heat, burning=False):
    """把基本面 + 预期面 + 情绪面合成一个点位判断。"""
    if h is None:
        return {"zone": "数据不足", "label": "—", "level": 0, "text": "行情数据不足"}
    zone = "极度悲观" if h <= 20 else "偏悲观" if h <= 38 else "中性" if h < 65 else "偏乐观" if h < 80 else "极度乐观"
    good_f = fs is not None and fs >= 60
    good_e = es is not None and es >= 58
    if h <= 38 and good_f and good_e:
        lab, lvl = "关注买点", 2
        txt = "基本面在改善、预期差为正，而价格处在悲观区——利空大概率已充分定价。"
    elif h <= 38 and (good_f or good_e):
        lab, lvl = "观察", 1
        txt = "价格在悲观区，但基本面和预期面只有一项支持，等另一项确认（通常是下一次财报）。"
    elif h <= 38:
        lab, lvl = "弱势", 0
        txt = "价格弱且基本面/预期没有支撑，便宜可能有便宜的道理。"
    elif h >= 80:
        lab, lvl = "回调风险", -2
        txt = "价格处在极度乐观区，利好大概率已被消化，追高的盈亏比差。"
    elif h >= 65 and not (good_f and good_e):
        lab, lvl = "谨慎", -1
        txt = "价格偏热，而基本面/预期没有同步跟上。"
    elif good_f and good_e:
        lab, lvl = "趋势中", 1
        txt = "基本面和预期都支持，价格不算过热——处在趋势中段，等回踩均线更好。"
    else:
        lab, lvl = "中性", 0
        txt = "没有明显的错配。"
    if lvl == 2 and burning:
        lab, lvl = "观察", 1
        txt = "价格在悲观区、增速和预期看起来都好，但公司仍在大幅亏损烧钱——高增长来自低基数，先当作观察对象。"
    if mkt_heat is not None:
        if mkt_heat <= 25 and lvl >= 1:
            txt += " 大盘情绪也在恐慌区，系统性的错杀概率更高。"
        if mkt_heat >= 75 and lvl <= 0:
            txt += " 大盘整体偏热，注意系统性回调。"
    return {"zone": zone, "label": lab, "level": lvl, "text": txt}


# ---------- 大盘：资金面 ----------

YF = "https://finance.yahoo.com/quote/"


def macro_funding(px):
    out = {"items": [], "why": [], "calc": []}

    def add(sym, name, unit, scale=1.0):
        if sym not in px:
            return None
        c = px[sym]["Close"] * scale
        last = float(c.iloc[-1])
        d = {"sym": sym, "name": name, "unit": unit, "last": _r(last, 2),
             "d1m": _r(last - float(c.iloc[-22]), 2) if len(c) > 22 else None,
             "d3m": _r(last - float(c.iloc[-64]), 2) if len(c) > 64 else None,
             "d1y": _r(last - float(c.iloc[-253]), 2) if len(c) > 253 else None,
             "pct1y": _r(_pctile(c.tail(252), last), 0),
             "series": [_r(x, 2) for x in c.tail(252).iloc[::3].tolist()]}
        out["items"].append(d)
        return d

    def calc(name, value, rule, pts, sym, read):
        out["calc"].append({"name": name, "value": value, "rule": rule, "pts": _r(pts, 1),
                            "src": f"Yahoo Finance {sym}", "url": YF + sym.replace("^", "%5E"), "read": read})
        return pts

    y10 = add("^TNX", "10年期美债收益率", "%")
    add("^FVX", "5年期美债收益率", "%")
    y3m = add("^IRX", "3个月美债收益率（贴近联储政策利率）", "%")
    dxy = add("DX-Y.NYB", "美元指数", "")
    score = 50.0  # 越高 = 钱越便宜、对成长股估值越友好
    out["calc"].append({"name": "起始分", "value": "", "rule": "中性起点", "pts": 50, "src": "", "url": "", "read": ""})
    if y10 and y10["d1y"] is not None:
        read = "钱在变贵，压制科技股估值" if y10["d1y"] > 0.25 else "钱在变便宜，支撑估值扩张" if y10["d1y"] < -0.25 else "利率大体走平"
        score += calc("10年期美债：一年变化", f"{y10['last']}%（一年 {y10['d1y'] * 100:+.0f}bp）",
                      "每上行 100bp 扣 20 分，下行加分；上下限 ±20", -_clip(y10["d1y"] * 20, -20, 20), "^TNX", read)
        out["why"].append(f"10年期美债 {y10['last']}%，一年{'上行' if y10['d1y'] > 0 else '下行'} {abs(y10['d1y']) * 100:.0f}bp——{read}")
    if y3m and y3m["d1y"] is not None:
        read = "联储处在加息 / 偏紧周期" if y3m["d1y"] > 0.25 else "联储处在降息周期" if y3m["d1y"] < -0.25 else "政策利率按兵不动"
        score += calc("3个月美债（联储政策代理）：一年变化", f"{y3m['last']}%（一年 {y3m['d1y'] * 100:+.0f}bp）",
                      "每上行 100bp 扣 12 分，下行加分；上下限 ±15", -_clip(y3m["d1y"] * 12, -15, 15), "^IRX", read)
        out["why"].append(f"3个月美债 {y3m['last']}%，一年变化 {y3m['d1y'] * 100:+.0f}bp——{read}")
    if y10 and y3m:
        sp = y10["last"] - y3m["last"]
        out["curve"] = _r(sp, 2)
        calc("收益率曲线：10年 − 3个月", f"{sp:+.2f}%", "只作参考，不计分。小于 0 为倒挂", 0, "^TNX",
             "倒挂，市场在担心衰退" if sp < 0 else "正常向上")
        out["why"].append(f"10年 - 3个月利差 {sp:+.2f}%（{'倒挂，衰退担忧' if sp < 0 else '正常向上'}）")
    if dxy and dxy["d1y"] is not None:
        chg = dxy["d1y"] / (dxy["last"] - dxy["d1y"])
        score += calc("美元指数：一年涨跌", f"{dxy['last']}（一年 {chg:+.1%}）", "每涨 1% 扣 1 分；上下限 ±8",
                      -_clip(chg * 100, -8, 8), "DX-Y.NYB", "美元走强 = 全球流动性收紧" if chg > 0 else "美元走弱 = 流动性改善")
        out["why"].append(f"美元指数 {dxy['last']}，一年 {chg:+.1%}（美元走强 = 全球流动性收紧）")
    if "HYG" in px and "TLT" in px:
        r = _ret(px["HYG"]["Close"], 63) - _ret(px["TLT"]["Close"], 63)
        score += calc("信用风险偏好：高收益债 HYG − 长期国债 TLT（近3个月）", f"{r:+.1%}", "每 1% 加 1 分；上下限 ±7",
                      _clip(r * 100, -7, 7), "HYG", "资金愿意承担信用风险" if r > 0 else "资金在避险")
        out["why"].append(f"高收益债相对长期国债近3个月 {r:+.1%}（{'信用风险偏好在' if r > 0 else '资金在避险'}）")
    out["score"] = round(_clip(score))
    s = out["score"]
    out["regime"] = "钱便宜（宽松）" if s >= 62 else "钱贵（紧缩）" if s <= 38 else "中性"
    return out


def etf_flows(px, names):
    """板块 ETF 的相对强弱 + 成交额变化，用来看资金往哪个板块流。"""
    spy = px["SPY"]["Close"] if "SPY" in px else None
    rows = []
    for sym, name in names.items():
        if sym not in px:
            continue
        t = tech(px[sym], spy)
        rows.append({"sym": sym, "name": name, "r1w": t["r1w"], "r1m": t["r1m"], "r3m": t["r3m"],
                     "rs1m": t["rs1m"], "rs3m": t["rs3m"], "flow": t["flow"], "vs50": t["vs50"]})
    rows.sort(key=lambda r: -(r["rs1m"] or -9))
    return rows


# ---------- 大盘：情绪面 ----------

def market_sentiment(px, fg, companies, lev_syms, lev_base, margin_note):
    out = {"why": [], "parts": []}
    parts = []

    def part(name, val, heat_val, text, rule="", src="", url=""):
        out["parts"].append({"name": name, "value": val, "heat": round(heat_val), "text": text,
                             "rule": rule, "src": src, "url": url})
        parts.append(heat_val)

    if "^VIX" in px:
        v = px["^VIX"]["Close"]
        last = float(v.iloc[-1])
        pc = _pctile(v.tail(252), last)
        out["vix"] = {"last": _r(last, 2), "pct1y": _r(pc, 0),
                      "series": [_r(x, 2) for x in v.tail(252).iloc[::2].tolist()]}
        h = _clip(50 + (18 - last) * 5)   # 18 附近是长期中枢；12 以下极度平静，28 以上极度恐慌
        txt = ("极度恐慌，利空大概率已充分定价" if last >= 30 else "恐慌升温" if last >= 22
               else "过度平静，市场没有为风险买保险" if last <= 13 else "正常区间")
        if "^VIX3M" in px:
            ts = last / float(px["^VIX3M"]["Close"].iloc[-1])
            out["vix"]["term"] = _r(ts, 3)
            if ts > 1:
                txt += "；期限结构倒挂（短期恐慌高于远期，往往接近情绪底）"
        part("VIX 恐慌指数", _r(last, 1), h, f"VIX {last:.1f}，处于近一年 {pc:.0f}% 分位——{txt}",
             "热度 = 50 + (18 − VIX) × 5，限制在 0~100。18 是长期中枢；≥30 极度恐慌，≥22 恐慌升温，≤13 过度平静。"
             "另看 VIX ÷ 3个月VIX：大于 1 表示期限结构倒挂，短期恐慌高于远期，往往接近情绪底。",
             "Yahoo Finance ^VIX、^VIX3M（CBOE 指数）", YF + "%5EVIX")
    if fg:
        out["fg"] = fg
        zh = {"extreme fear": "极度恐惧", "fear": "恐惧", "neutral": "中性", "greed": "贪婪", "extreme greed": "极度贪婪"}
        part("CNN 恐贪指数", fg["score"], fg["score"],
             f"{fg['score']:.0f}（{zh.get(fg['rating'], fg['rating'])}），一周前 {fg['prev_week']:.0f}，一月前 {fg['prev_month']:.0f}",
             "热度直接取指数值（0~100）。CNN 用七项合成：股价动量、新高新低、涨跌广度、Put/Call、VIX、垃圾债利差、避险需求。"
             "≤25 极度恐惧，≥75 极度贪婪。", "CNN Fear & Greed Index", "https://www.cnn.com/markets/fear-and-greed")
        if fg.get("put_call"):
            out["why"].append(f"Put/Call 比 {fg['put_call']:.2f}（>1 偏恐慌，<0.7 偏贪婪）")
    # 估值拥挤度：产业链公司里有多少已经远离200日线 + 远期市盈率中位数
    vs200 = [c["tech"].get("vs200") for c in companies.values() if c.get("tech")]
    above = _share(vs200, lambda x: x > 0)
    stretched = _share(vs200, lambda x: x > 0.3)
    fpes = [c["capital"]["fpe"] for c in companies.values() if c.get("capital") and c["capital"].get("fpe") and 0 < c["capital"]["fpe"] < 300]
    if above is not None:
        med_fpe = _median(fpes)
        out["crowding"] = {"above200": _r(above), "stretched": _r(stretched), "median_fpe": _r(med_fpe, 1), "n": len(vs200)}
        h = _clip(above * 60 + stretched * 130)
        part("估值拥挤度", f"{above:.0%}", h,
             (f"本页全部 {len(vs200)} 家公司中 {above:.0%} 站上200日线，{stretched:.0%} 高出200日线三成以上；"
              f"远期市盈率中位数 {med_fpe:.0f}x") if med_fpe else f"{above:.0%} 站上200日线",
             "热度 = 站上200日线的公司占比 × 60 + 高出200日线 30% 以上的公司占比 × 130，限制在 0~100。"
             "衡量的是“大家是不是都已经涨上去了”。远期市盈率中位数只展示、不计分。",
             "Yahoo Finance 日线（本页全部公司）+ 一致预期 EPS", "")
    # 杠杆风险：三倍做多 ETF 成交额占比
    try:
        num = sum((px[s]["Close"] * px[s]["Volume"]) for s in lev_syms if s in px)
        den = sum((px[s]["Close"] * px[s]["Volume"]) for s in lev_base if s in px)
        ratio = (num.rolling(20).mean() / den.rolling(20).mean()).dropna()
        last = float(ratio.iloc[-1])
        pc = _pctile(ratio.tail(252), last)
        out["leverage"] = {"ratio": _r(last, 3), "pct1y": _r(pc, 0),
                           "series": [_r(x, 3) for x in ratio.tail(252).iloc[::3].tolist()]}
        part("杠杆风险", f"{pc:.0f}%分位", pc,
             f"三倍做多 ETF 成交额占比处于近一年 {pc:.0f}% 分位（越高说明散户加杠杆越激进）",
             "(TQQQ + SOXL + UPRO + TECL 的成交额) ÷ (QQQ + SPY + SMH 的成交额)，取 20 日均值，热度 = 它在近一年里的分位数。"
             "这是融资余额的替代指标：FINRA 的融资余额月度数据网站挡爬虫，拿不到。",
             "Yahoo Finance 各 ETF 日线", YF + "TQQQ")
    except Exception:
        pass
    if margin_note:
        out["margin"] = margin_note
    out["heat"] = round(sum(parts) / len(parts)) if parts else None
    h = out["heat"]
    if h is not None:
        out["zone"] = "极度悲观" if h <= 20 else "偏悲观" if h <= 38 else "中性" if h < 62 else "偏乐观" if h < 78 else "极度乐观"
        out["verdict"] = {
            "极度悲观": "利空已充分定价，是分批找买点的窗口。",
            "偏悲观": "情绪偏冷，好公司的回调值得盯。",
            "中性": "情绪没有给出方向，回到个股基本面和预期差。",
            "偏乐观": "利好正在被消化，新开仓要挑位置。",
            "极度乐观": "利好已被充分消化，存在回调风险，不追高。"}[out["zone"]]
    return out


# ---------- 三浪位置 ----------

def wave_status(sector, companies, px, spy):
    """对每个板块的一/二/三浪各算一组价格 + 财报指标，给出阶段判断。"""
    by_wave = {1: set(), 2: set(), 3: set()}
    for chain in sector["chains"]:
        for node in chain["nodes"]:
            for c in node["cos"]:
                if c["ticker"] and c.get("core", True) and c["ticker"] in companies:
                    by_wave[node["wave"]].add(c["ticker"])
    out = []
    spy3 = _ret(spy, 63)
    for w in (1, 2, 3):
        tks = sorted(by_wave[w])
        meta = sector["waves"][str(w)]
        row = {"wave": w, "name": meta["name"], "desc": meta["desc"], "tickers": tks, "checks": []}
        if len(tks) < 3:
            row.update(stage="样本不足", level=0, score=None)
            out.append(row)
            continue
        cs = [companies[t] for t in tks]
        r3 = [c["tech"]["r3m"] for c in cs]
        r6 = [c["tech"]["r6m"] for c in cs]
        avg3, avg6 = np.nanmean([x for x in r3 if x is not None]), _median(r6)
        rel3 = avg3 - spy3 if spy3 is not None else None
        b50 = _share([c["tech"]["vs50"] for c in cs], lambda x: x > 0)
        b200 = _share([c["tech"]["vs200"] for c in cs], lambda x: x > 0)
        xs = [x for x in r3 if x is not None]
        disp = float(np.percentile(xs, 75) - np.percentile(xs, 25)) if len(xs) >= 4 else None
        gm_up = _share([c["fund"].get("gm_yoy") if c["fund"].get("gm_yoy") is not None else c["fund"].get("gm_qoq") for c in cs], lambda x: x > 0)
        em_up = _share([c["fund"].get("em_yoy") if c["fund"].get("em_yoy") is not None else c["fund"].get("em_qoq") for c in cs], lambda x: x > 0)
        rev_med = _median([c["fund"].get("rev_yoy") for c in cs])
        rpos = [c["fund"].get("rpo_yoy") for c in cs if c["fund"].get("rpo_yoy") is not None]
        rpo_med = _median(rpos)
        rsi_med = _median([c["tech"]["rsi"] for c in cs])
        row.update(r3m=_r(avg3), r6m=_r(avg6), rel3m=_r(rel3), b50=_r(b50), b200=_r(b200), disp=_r(disp),
                   gm_up=_r(gm_up), em_up=_r(em_up), rev_med=_r(rev_med), rpo_med=_r(rpo_med),
                   rpo_n=len(rpos), rsi_med=_r(rsi_med, 0))

        def chk(name, ok, text):
            row["checks"].append({"name": name, "ok": None if ok is None else bool(ok), "text": text})

        def chk(name, ok, text, rule):
            row["checks"].append({"name": name, "ok": None if ok is None else bool(ok), "text": text, "rule": rule})

        chk("相对强度", None if rel3 is None else rel3 > 0.02,
            f"篮子近3个月 {avg3:+.0%}，相对标普500 {rel3:+.0%}" if rel3 is not None else "无数据",
            "这一浪全部纯正标的等权平均的近 63 个交易日涨幅 − 标普500 同期涨幅 > 2%")
        chk("上涨广度", None if b50 is None else b50 >= 0.55,
            f"{b50:.0%} 的公司站上50日线，{b200:.0%} 站上200日线" if b50 is not None and b200 is not None else "无数据",
            "收盘价高于 50 日均线的公司占比 ≥ 55%")
        chk("收入增速", None if rev_med is None else rev_med >= 0.15,
            f"收入同比增速中位数 {rev_med:+.0%}" if rev_med is not None else "无数据",
            "最新一季收入同比增速的中位数 ≥ 15%")
        chk("毛利率（产品力）", None if gm_up is None else gm_up >= 0.55,
            f"{gm_up:.0%} 的公司毛利率在改善" if gm_up is not None else "无数据",
            "最新一季毛利率高于去年同期（没有去年同期就比上一季）的公司占比 ≥ 55%")
        chk("EBITDA 利润率（经营效率）", None if em_up is None else em_up >= 0.55,
            f"{em_up:.0%} 的公司 EBITDA 利润率在改善" if em_up is not None else "无数据",
            "最新一季 EBITDA ÷ 收入 高于去年同期的公司占比 ≥ 55%")
        chk("RPO（长期大单）", None if rpo_med is None else rpo_med >= 0.15,
            f"{len(rpos)} 家披露 RPO，同比增速中位数 {rpo_med:+.0%}" if rpo_med is not None else "披露 RPO 的公司不足",
            "披露了 RPO（剩余履约义务）的公司，其 RPO 同比增速的中位数 ≥ 15%")
        valid = [c for c in row["checks"] if c["ok"] is not None]
        passed = sum(1 for c in valid if c["ok"])
        price_ok = sum(1 for c in row["checks"][:2] if c["ok"])
        fund_ok = sum(1 for c in row["checks"][2:] if c["ok"])
        fund_n = sum(1 for c in row["checks"][2:] if c["ok"] is not None)
        ratio = passed / len(valid) if valid else 0
        r1y = _median([c["tech"]["r1y"] for c in cs])
        row["r1y"] = _r(r1y)
        has_run = (r1y or 0) >= 0.4          # 过去一年已经涨了一大段
        fund_strong = fund_n > 0 and fund_ok >= 2 and fund_ok / fund_n >= 0.6
        overheated = (rsi_med or 0) >= 68 and (b200 or 0) >= 0.85 and (avg6 or 0) >= 0.5
        if overheated:
            stage, level = "主升浪后段 / 过热", 4
        elif price_ok == 2 and fund_strong:
            stage, level = "主升浪进行中", 3
        elif price_ok == 1 and fund_strong and has_run:
            stage, level = "主升浪中的整理", 3
        elif price_ok == 1 and fund_strong:
            stage, level = "启动初期", 2
        elif price_ok == 0 and fund_strong and has_run:
            stage, level = "回调中（财报仍在改善）", 2
        elif price_ok == 0 and fund_strong:
            stage, level = "财报已改善，价格未反映", 1
        elif price_ok >= 1:
            stage, level = "价格先行，财报待验证", 1
        else:
            stage, level = "尚未启动", 0
        # 分化上涨：篮子在涨，但内部涨跌差距很大——说明资金在挑真正受益的公司，是浪的早期特征
        if disp is not None and avg3 > 0 and b50 is not None:
            row["diverging"] = bool(disp >= 0.25 and 0.35 <= b50 <= 0.75)
        row["path"] = (f"价格 {price_ok}/2 项满足；财报 {fund_ok}/{fund_n} 项满足（{'强' if fund_strong else '不强'}：需 ≥2 项且 ≥60%）；"
                       f"过去一年涨幅中位数 {r1y:+.0%}（{'已涨过一大段' if has_run else '还没涨过'}：阈值 40%）；"
                       f"过热条件（RSI 中位 ≥68、≥85% 站上200日线、6月涨幅中位 ≥50%）{'成立' if overheated else '不成立'}"
                       if r1y is not None else f"价格 {price_ok}/2；财报 {fund_ok}/{fund_n}")
        row["members"] = [{"t": t, "r3m": c["tech"]["r3m"], "a50": (c["tech"]["vs50"] or 0) > 0,
                           "a200": (c["tech"]["vs200"] or 0) > 0 if c["tech"]["vs200"] is not None else None,
                           "rev": c["fund"].get("rev_yoy"),
                           "gm": c["fund"].get("gm_yoy") if c["fund"].get("gm_yoy") is not None else c["fund"].get("gm_qoq"),
                           "em": c["fund"].get("em_yoy") if c["fund"].get("em_yoy") is not None else c["fund"].get("em_qoq"),
                           "rpo": c["fund"].get("rpo_yoy")} for t, c in zip(tks, cs)]
        row.update(stage=stage, level=level, score=round(ratio * 100), passed=passed, total=len(valid))
        out.append(row)
    return out


def igv_check(px, holdings, companies):
    """你提到的二浪判据：IGV 是不是"有分化的上涨"。"""
    if "IGV" not in px or "SMH" not in px:
        return None
    igv, smh = px["IGV"]["Close"], px["SMH"]["Close"]
    out = {"igv_r1m": _r(_ret(igv, 21)), "igv_r3m": _r(_ret(igv, 63)), "igv_r6m": _r(_ret(igv, 126)),
           "smh_r3m": _r(_ret(smh, 63)), "smh_r6m": _r(_ret(smh, 126)),
           "igv_vs200": _r(float(igv.iloc[-1] / igv.tail(200).mean() - 1)),
           "ratio": [_r(x, 4) for x in (igv / smh).dropna().tail(252).iloc[::3].tolist()]}
    members = []
    for t in holdings:
        if t in px:
            c = px[t]["Close"]
            members.append({"t": t, "r3m": _r(_ret(c, 63)), "above50": bool(c.iloc[-1] > c.tail(50).mean())})
    members.sort(key=lambda m: -(m["r3m"] or -9))
    out["members"] = members
    rs = [m["r3m"] for m in members if m["r3m"] is not None]
    if len(rs) >= 5:
        out["spread"] = _r(max(rs) - min(rs))
        out["up_share"] = _r(sum(1 for r in rs if r > 0) / len(rs))
    rising = (out["igv_r3m"] or 0) > 0.03
    diverging = out.get("spread") is not None and out["spread"] >= 0.35 and 0.3 <= out["up_share"] <= 0.8
    lead = (out["igv_r3m"] or 0) - (out["smh_r3m"] or 0)
    out["rising"], out["diverging"], out["lead_vs_smh"] = rising, bool(diverging), _r(lead)
    if rising and diverging:
        out["verdict"] = "IGV 在上涨，且内部明显分化——资金在挑真正受益于 AI 的软件公司，符合二浪启动的特征。"
    elif rising:
        out["verdict"] = "IGV 在上涨，但成分股普涨、没有分化——更像估值修复或大盘带动，还需要财报确认。"
    elif diverging:
        out["verdict"] = "IGV 整体没涨，但内部已经分化——少数公司先走出来，留意它们的财报是否有毛利率和 RPO 支撑。"
    else:
        out["verdict"] = "IGV 既没上涨也没分化——二浪的价格信号还没出现。"
    return out


# ---------- 前瞻估值：2026 / 2027 / 2028 的市值区间 → 股价区间 ----------
#
# 全部是写死的规则，判断标准页上有完整说明：
#   1. 盈利指标：盈利的公司用 EPS，还没盈利的用收入。数据 = 分析师一致预期（本财年 0y、下财年 +1y，各有
#      低 / 平均 / 高）。已公布的最近一个财年用实际值。第三年没有一致预期时，用下财年增速 × 0.7 外推，区间放宽 15%。
#   2. 财年 → 日历年：财年在 1~5 月结束的算作前一年（英伟达截至 2027 年 1 月的财年 = 2026 年）。
#   3. 估值倍数：以“市场现在给的倍数”为基准（股价 ÷ 下财年 EPS，或 企业价值 ÷ 下财年收入），再按增速
#      应得的倍数（市盈率 12 + 增速%，限制 12~45 倍；市销率 (1 + 0.2 × 增速%) × 毛利率修正，限制 1~20 倍）
#      向它靠拢，但最多上下调 22%（= 两者之比限制在 0.67~1.5 后开平方）。区间 = 中值 × 0.75 ~ × 1.3。
#   4. 市值区间 = 预期低值 × 低倍数 ~ 预期高值 × 高倍数；按收入估值时先算企业价值再扣净债务。除以总股本得股价区间。
# 含义：“如果分析师预期兑现、市场维持与增速相称的倍数，那一年这家公司值多少”。它不是目标价，
# 也反映不了预期本身会不会被下修——那部分看“趋势判断”。

def _fy_label(end):
    y, m = int(end[:4]), int(end[5:7])
    return y - 1 if m <= 5 else y


def forward_valuation(f, price, today):
    info = f.get("info") or {}
    est = f.get("estimates") or {}
    annual = f.get("annual") or []
    mcap = info.get("marketCap")
    e0, e1 = est.get("0y") or {}, est.get("+1y") or {}
    if not price or not mcap or not (e0 or e1):
        return None
    shares = mcap / price
    net_debt = (info.get("totalDebt") or 0) - (info.get("totalCash") or 0)
    last = annual[-1] if annual else None
    fy0 = f"{int(last['end'][:4]) + 1}{last['end'][4:]}" if last else f"{today[:4]}-12-31"

    def g(a, b):
        return (a - b) / abs(b) if a is not None and b not in (None, 0) else None

    eps_g = g(e1.get("eps_avg"), e0.get("eps_avg"))
    rev_g = g(e1.get("rev_avg"), e0.get("rev_avg"))
    gm = info.get("grossMargins") or 0.5
    per_share = (e0.get("eps_avg") or 0) > 0 and (e1.get("eps_avg") or e0.get("eps_avg") or 0) > 0
    ref = e1 if e1.get("eps_avg" if per_share else "rev_avg") else e0
    if per_share:
        basis = "EPS（市盈率）"
        gg = max(0.0, min(0.6, eps_g if eps_g is not None else (rev_g or 0.1)))
        rule = _clip(12 + gg * 100, 12, 45)
        market = price / ref["eps_avg"]
    else:
        if not ref.get("rev_avg"):
            return None
        basis = "收入（企业价值 / 销售额）"
        gg = max(0.0, min(0.8, rev_g if rev_g is not None else 0.2))
        rule = _clip((1 + 0.2 * gg * 100) * _clip(gm / 0.5, 0.4, 1.6), 1, 20)
        market = (mcap + net_debt) / ref["rev_avg"]
    if not market or market <= 0:
        return None
    mid = market * _clip(rule / market, 0.67, 1.5) ** 0.5
    mult = (mid * 0.75, mid, mid * 1.3)

    def value(x, m):
        return x * m * shares if per_share else x * m - net_debt

    def row(label, lo, av, hi, kind, fy_end):
        caps = [value(x, m) for x, m in zip((lo, av, hi), mult)]
        nd = 2 if per_share else 0
        return {"y": label, "fy_end": fy_end, "kind": kind, "metric": [_r(lo, nd), _r(av, nd), _r(hi, nd)],
                "mcap": [round(c) for c in caps], "px": [_r(max(c, 0) / shares, 2) for c in caps]}

    k = "eps" if per_share else "rev"
    rows = {}
    if last and last.get(k) is not None and last.get(k) > 0:
        v = last[k]
        rows[_fy_label(last["end"])] = row(_fy_label(last["end"]), v, v, v, "实际（已公布财年）", last["end"][:7])
    y0 = _fy_label(fy0)
    for i, e in enumerate((e0, e1)):
        av = e.get(k + "_avg")
        if av is None:
            continue
        lo = e.get(k + "_lo") if e.get(k + "_lo") is not None else av * 0.9
        hi = e.get(k + "_hi") if e.get(k + "_hi") is not None else av * 1.1
        fy = f"{int(fy0[:4]) + i}{fy0[4:7]}"
        rows[y0 + i] = row(y0 + i, lo, av, hi, f"一致预期（{e.get(k + '_n') and int(e.get(k + '_n')) or '?'} 位分析师）", fy)
    top = max(rows) if rows else None
    if top is not None and top < 2028 and rows[top]["kind"].startswith("一致"):
        g2 = max(-0.1, min(0.5, 0.7 * ((eps_g if per_share else rev_g) or 0.1)))
        lo, av, hi = rows[top]["metric"]
        for yy in range(top + 1, 2029):
            lo, av, hi = lo * (1 + g2) / 1.15, av * (1 + g2), hi * (1 + g2) * 1.15
            rows[yy] = row(yy, lo, av, hi, f"外推（增速 {g2:+.0%}）", "外推")
    years = [rows[y] for y in (2026, 2027, 2028) if y in rows]
    if not years:
        return None
    out = {"basis": basis, "mult": [_r(x, 1) for x in mult], "market_mult": _r(market, 1), "rule_mult": _r(rule, 1),
           "eps_g": _r(eps_g), "rev_g": _r(rev_g), "net_debt": round(net_debt), "years": years}
    y27 = rows.get(2027)
    if y27:
        lo, mid_, hi = y27["px"]
        out["below27hi"] = bool(price < hi)
        out["pos27"] = _r((price - lo) / (hi - lo)) if hi > lo else None
        out["zone27"] = ("低于下沿" if price < lo else "下沿~中值" if price < mid_ else "中值~上沿" if price < hi else "高于上沿")
        out["up27mid"] = _r(mid_ / price - 1)
        out["up27hi"] = _r(hi / price - 1)
    return out
