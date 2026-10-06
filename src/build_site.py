"""把快照数据塞进 web/template.html，生成一个自包含的 index.html（不依赖任何 CDN）。"""
import json

from . import config


def build(snap):
    html = config.TEMPLATE.read_text()
    data = json.dumps(snap, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    three = (config.ROOT / "web" / "vendor" / "three.min.js").read_text().replace("</script", "<\\/script")
    html = html.replace("/*__THREE__*/", three, 1)
    config.SITE.write_text(html.replace("/*__DATA__*/null", data))
