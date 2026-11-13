"""把快照数据塞进 web/template.html，生成一个自包含的 index.html（不依赖任何 CDN）。"""
import json

from . import config


def build(snap):
    html = config.TEMPLATE.read_text()
    try:   # 博主观点（视野环球财经每日笔记整理成的结构化数据）
        snap["blogger"] = json.loads((config.DATA / "blogger.json").read_text())
    except Exception:
        snap["blogger"] = None
    data = json.dumps(snap, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    three = (config.ROOT / "web" / "vendor" / "three.min.js").read_text().replace("</script", "<\\/script")
    html = html.replace("/*__THREE__*/", three, 1)
    config.SITE.write_text(html.replace("/*__DATA__*/null", data))
