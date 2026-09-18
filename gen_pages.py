# -*- coding: utf-8 -*-
"""把合规条目生成为静态 HTML 页面（AI 爬虫不执行 JS，必须静态化）。

输出：site/answers/compliance/index.html + <slug>.html
用法：python gen_pages.py
"""
import html
import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "site", "answers", "compliance")

SITE = "https://savantcat.cn"
HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<style>
:root{{--fg:#1a1a1a;--mut:#666;--line:#e6e6e6;--bg:#fff;--acc:#0b6e4f}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:760px;margin:0 auto;padding:28px 20px 80px}}
h1{{font-size:24px;line-height:1.45;margin:0 0 6px}}
h2{{font-size:18px;margin:32px 0 10px;padding-left:10px;border-left:4px solid var(--acc)}}
h3{{font-size:16px;margin:20px 0 8px}}
.meta{{color:var(--mut);font-size:13px;margin-bottom:18px}}
.answer{{background:#f5faf7;border:1px solid #d8ece2;border-radius:8px;padding:14px 16px;margin:14px 0}}
.basis{{background:#fafafa;border-left:3px solid #bbb;padding:10px 14px;margin:10px 0;font-size:14px;color:#333}}
.basis b{{color:#000}}
ul,ol{{padding-left:22px}} li{{margin:6px 0}}
.faq{{border-top:1px solid var(--line);padding-top:8px}}
.faq dt{{font-weight:600;margin-top:14px}} .faq dd{{margin:6px 0 0;color:#333}}
a{{color:var(--acc)}}
.foot{{margin-top:44px;border-top:1px solid var(--line);padding-top:14px;color:var(--mut);font-size:13px}}
.tag{{display:inline-block;background:#eef3f0;color:#0b6e4f;border-radius:4px;padding:2px 8px;font-size:12px;margin-right:6px}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{border:1px solid var(--line);padding:6px 8px;text-align:left}}
</style>
</head><body><div class="wrap">
"""
FOOT = """
<div class="foot">
<p><b>我们是谁</b>：面向小微企业的知识库与 AI 客服服务团队，做「AI 客服过国标、过备案」的落地交付。</p>
<p>本站内容依据公开发布的法规原文整理，用于企业自查参考；具体申报口径以属地网信部门要求为准，不构成法律意见。</p>
<p>本页由 <a href="{site}/mcp-compliance">中国 AI 合规与备案 MCP</a> 语料库生成 —— Agent 可直接调用同一份数据。</p>
</div></div></body></html>
"""


def md2html(md):
    out, in_ul, in_ol = [], False, False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for raw in (md or "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            close_lists()
            continue
        line = html.escape(line)
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"`(.+?)`", r"<code>\1</code>", line)
        if line.startswith("#### "):
            close_lists(); out.append("<h4>%s</h4>" % line[5:])
        elif line.startswith("### "):
            close_lists(); out.append("<h3>%s</h3>" % line[4:])
        elif line.startswith("## "):
            close_lists(); out.append("<h2>%s</h2>" % line[3:])
        elif line.startswith("# "):
            close_lists(); out.append("<h2>%s</h2>" % line[2:])
        elif re.match(r"^[-*] ", line):
            if not in_ul:
                close_lists(); out.append("<ul>"); in_ul = True
            out.append("<li>%s</li>" % line[2:])
        elif re.match(r"^\d+[.、] ", line):
            if not in_ol:
                close_lists(); out.append("<ol>"); in_ol = True
            out.append("<li>%s</li>" % re.sub(r"^\d+[.、] ", "", line))
        elif line.startswith("&gt; "):
            close_lists(); out.append("<blockquote>%s</blockquote>" % line[6:])
        else:
            close_lists(); out.append("<p>%s</p>" % line)
    close_lists()
    return "\n".join(out)


def main():
    topics = json.load(open(os.path.join(DATA, "topics.json"), encoding="utf-8"))
    meta = json.load(open(os.path.join(DATA, "meta.json"), encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)
    clusters = meta["clusters"]
    ordered = sorted(topics.values(), key=lambda x: (x.get("cluster", ""), x.get("order", 0)))

    # 单条页
    for it in ordered:
        slug = it["slug"]
        url = "%s/answers/compliance/%s.html" % (SITE, slug)
        parts = [HEAD.format(title="%s | 中国 AI 合规与备案" % html.escape(it["question"]),
                             desc=html.escape((it.get("short_answer") or "")[:150]),
                             url=url)]
        cl = it.get("cluster")
        parts.append('<p class="tag">%s</p>' % html.escape((clusters.get(cl) or {}).get("name", cl)))
        parts.append("<h1>%s</h1>" % html.escape(it["question"]))
        parts.append('<div class="meta">依据：%s</div>' % html.escape(
            "、".join(sorted({b.get("law", "") for b in it.get("legal_basis") or []})) or "公开法规原文"))
        parts.append('<div class="answer"><b>结论</b>：%s</div>' % html.escape(it.get("short_answer") or ""))
        parts.append("<h2>你可能要做的</h2>\n<ol>%s</ol>" % "".join(
            "<li>%s</li>" % html.escape(a) for a in it.get("actions") or []))
        parts.append("<h2>条文依据</h2>")
        for b in it.get("legal_basis") or []:
            parts.append('<div class="basis"><b>%s %s</b><br>%s</div>' % (
                html.escape(b.get("law", "")), html.escape(b.get("article", "")),
                html.escape(b.get("quote", ""))))
        parts.append(md2html(it.get("body_md")))
        if it.get("faqs"):
            parts.append('<h2>常见追问</h2><dl class="faq">%s</dl>' % "".join(
                "<dt>%s</dt><dd>%s</dd>" % (html.escape(f.get("q", "")), html.escape(f.get("a", "")))
                for f in it["faqs"]))
        srcs = it.get("sources") or []
        if srcs:
            parts.append("<h2>原文来源</h2><ul>%s</ul>" % "".join(
                '<li><a href="%s" rel="nofollow noopener" target="_blank">%s</a></li>' % (
                    html.escape(s.get("url", "")), html.escape(s.get("title", ""))) for s in srcs))
        parts.append(FOOT.format(site=SITE))
        open(os.path.join(OUT, slug + ".html"), "w", encoding="utf-8").write("\n".join(parts))

    # 索引页
    rows = []
    for cl, info in clusters.items():
        items = [x for x in ordered if x.get("cluster") == cl]
        rows.append("<h2>%s<span class=\"meta\"> · %d 条</span></h2><ul>%s</ul>" % (
            html.escape(info["name"]), len(items),
            "".join('<li><a href="%s.html">%s</a><br><span class="meta">%s</span></li>' % (
                x["slug"], html.escape(x["question"]), html.escape((x.get("short_answer") or "")[:70]))
                     for x in items)))
    idx = HEAD.format(title="中国 AI 合规与备案 · 条文级自查清单",
                      desc="生成式AI备案、AI生成内容标识、深度合成、算法备案的条文级要求与应办事项，按法规逐条整理。",
                      url=SITE + "/answers/compliance/")
    idx += ("<h1>中国 AI 合规与备案 · 条文级自查</h1>"
            "<div class=\"answer\">共 <b>%d</b> 条，覆盖 %d 部法规。<b>Agent 可直接调用同一份数据</b>："
            "<code>https://savantcat.cn/mcp-compliance</code></div>" % (len(ordered), len(meta["regulations"])))
    idx += "\n".join(rows) + FOOT.format(site=SITE)
    open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(idx)
    print("✅ 生成 %d 个页面 + 索引 -> %s" % (len(ordered), OUT))


if __name__ == "__main__":
    main()
