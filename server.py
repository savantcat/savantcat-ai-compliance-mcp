# -*- coding: utf-8 -*-
"""
合尘猫 · 中国 AI 合规与备案 MCP Server
=====================================
把中国 AI 法规（生成式AI暂行办法 / 内容标识办法 / 深度合成规定 / 算法推荐规定）
做成 **Agent 可直接调用** 的 MCP 服务，每条结论都标注条文依据。

双通道:
  本地 stdio   :  python server.py
  远程 HTTP    :  python server.py --transport http --host 127.0.0.1 --port 8766
                  (公网挂载点 https://savantcat.cn/mcp-compliance)

暴露工具:
  - list_topics        列出全部合规条目（可按集群过滤）
  - search_compliance  关键词检索（返回主题 + 结论 + 依据 + 链接）
  - get_requirement    取单条完整内容（正文 + 依据条文 + 落地动作 + 常见追问）
  - self_check         按场景出自查清单（把命中条目的落地动作汇总成待办）
  - filing_route       备案/标识路径判定（输入服务形态，输出应办事项 + 条文依据）
  - regulation_info    收录法规清单（名称 / 发布施行日期 / 条数 / 官方原文链接）
  - get_article        按**条号**取某部法规的逐字原文（附 sha256 内容指纹与官方原文链接）
  - search_articles    在某一部法规**内部**按关键词检索条文（返回命中条号 + 摘录）

自检(不走协议,直接打工具):  python server.py --selftest
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys

# 工具注解：纯读、幂等、不接触外部世界，统一声明为 RO_ANN。
try:
    from mcp.types import ToolAnnotations
except ImportError:  # 老版本 SDK 无该类型时降级为 dict，行为一致
    ToolAnnotations = dict

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

# mcp 2.x 把 FastMCP 更名为 MCPServer；兼容 1.x，避免 SDK 升级打断通道。
try:  # mcp >= 2.x
    from mcp.server.mcpserver import MCPServer as _MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _MCPServer

# 公网部署必需：SDK 默认开启 DNS-rebinding 防护，只放行 localhost，
# 外部以真实域名访问会被拒成 421「Invalid Host header」。这里改为白名单放行。
try:
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError:  # 老版本 SDK 无此模块
    TransportSecuritySettings = None

DEFAULT_ALLOWED_HOSTS = [
    "savantcat.cn", "savantcat.cn:443", "www.savantcat.cn", "www.savantcat.cn:443",
    "127.0.0.1:8766", "localhost:8766", "127.0.0.1", "localhost",
]


def _transport_security():
    if TransportSecuritySettings is None:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,   # 保留防护，用白名单而非关闭
        allowed_hosts=DEFAULT_ALLOWED_HOSTS,
        allowed_origins=["*"],                  # 公开只读服务：允许任意来源 Agent 调用
    )


# ---------------------------------------------------------------- 数据加载
def _load(name):
    with io.open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


TOPICS = _load("topics.json")          # slug -> 完整条目
INDEX = _load("index.json")            # 轻量索引列表
META = _load("meta.json")
REGULATIONS = META.get("regulations", [])
CLUSTERS = META.get("clusters", {})
ARTICLES = _load("articles.json")      # cluster -> {第X条: 正文}


def _slugify(s):
    return re.sub(r"[^a-z0-9\-]+", "", (s or "").lower())


def _tokens(s):
    """中英混排的粗粒度切词：英文按词，中文按 2-gram。"""
    s = (s or "").lower()
    words = re.findall(r"[a-z0-9]+", s)
    cjk = re.findall(r"[\u4e00-\u9fff]+", s)
    grams = []
    for seg in cjk:
        grams += [seg[i:i + 2] for i in range(max(len(seg) - 1, 1))] if len(seg) > 1 else [seg]
    return set(words) | set(grams) | set(re.findall(r"[\u4e00-\u9fff]", s))


def _score(query, item):
    """字段加权 + 字符重合度，够用即可。"""
    q = (query or "").strip().lower()
    if not q:
        return 0.0
    hit = 0.0
    qn = re.sub(r"\s+", "", q)

    question = (item.get("question") or "").lower()
    keywords = (item.get("keywords") or "").lower()
    short = (item.get("short_answer") or "").lower()
    if qn and qn in re.sub(r"\s+", "", question):
        hit += 10.0

    for term in [t for t in re.split(r"[\s,，、;；]+", q) if t]:
        if term in question:
            hit += 5.0
        if term in keywords:
            hit += 4.0
        if term in short:
            hit += 2.0

    a, b = _tokens(q), _tokens(question + " " + keywords)
    if a and b:
        hit += 6.0 * (len(a & b) / float(len(a)))
    return hit


def _basis_text(item):
    """把 legal_basis 渲染成一行行依据文本。"""
    out = []
    for b in item.get("legal_basis") or []:
        out.append("%s %s：%s" % (b.get("law", ""), b.get("article", ""), b.get("quote", "")))
    return out


def _public(item, with_body=False):
    d = {
        "slug": item.get("slug"),
        "cluster": item.get("cluster"),
        "cluster_cn": (CLUSTERS.get(item.get("cluster")) or {}).get("name"),
        "question": item.get("question"),
        "short_answer": item.get("short_answer"),
        "legal_basis": item.get("legal_basis"),
        "actions": item.get("actions"),
        "url": "https://savantcat.cn/answers/compliance/%s.html" % (item.get("slug")),
    }
    if with_body:
        d["body_md"] = item.get("body_md")
        d["keywords"] = item.get("keywords")
        d["faqs"] = item.get("faqs")
        d["sources"] = item.get("sources")
    return d


# ---------------------------------------------------------------- 条文级查询
_CN_DIGITS = "零一二三四五六七八九"


def _cn_num(n):
    """整数 → 中文数字（1-99 覆盖本法条范围）。"""
    if n <= 0:
        return str(n)
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    return str(n)


def _reg_of(cluster):
    """cluster -> 该法规的元信息（REGULATIONS 里的一条）。"""
    for r in REGULATIONS:
        if r.get("cluster") == cluster:
            return r
    return None


def _match_laws(law):
    """把用户给的法规名/简称/cluster 解析成 [(cluster, reg)]，0/1/多 三种结果。"""
    k = re.sub(r"[《》\s]", "", (law or "").strip())
    if not k:
        return []
    out = []
    for c in ARTICLES:
        reg = _reg_of(c) or {}
        name = re.sub(r"[《》\s]", "", reg.get("law", ""))
        if k == c or (name and (k in name or name in k)):
            out.append((c, reg))
    return out


def _norm_article(a, keys):
    """条号归一化：支持「第十条」「10」「第10条」，返回 keys 里存在的那个键，否则 None。"""
    s = (a or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"第?(\d{1,3})条?", s)
    if m:
        cand = "第%s条" % _cn_num(int(m.group(1)))
    else:
        m2 = re.fullmatch(r"第?([零一二三四五六七八九十百]+)条?", s)
        cand = ("第%s条" % m2.group(1)) if m2 else s
    return cand if cand in keys else None


def _excerpt(text, query, span=(30, 90)):
    """在条文中定位查询词，返回带省略号的摘录；定位不到则回首段。"""
    for t in sorted(_tokens(query), key=len, reverse=True):
        i = text.find(t)
        if i >= 0:
            s = max(0, i - span[0])
            e = min(len(text), i + span[1])
            return ("…" if s else "") + text[s:e] + ("…" if e < len(text) else "")
    return text[:120] + ("…" if len(text) > 120 else "")


# ---------------------------------------------------------------- MCP Server
SERVER_VERSION = "1.1.0"

mcp = _MCPServer(
    "savantcat-ai-compliance",
    title="\u5408\u5c18\u732b \u00b7 \u4e2d\u56fd AI \u5408\u89c4\u4e0e\u5907\u6848\u77e5\u8bc6\u5e93",
    description=(
        "\u300a\u751f\u6210\u5f0f\u4eba\u5de5\u667a\u80fd\u670d\u52a1\u7ba1\u7406\u6682\u884c\u529e\u6cd5\u300b\u7b49\u6cd5\u89c4\u7684\u9010\u6761\u8981\u6c42\u4e0e\u5907\u6848\u8def\u5f84\uff0c"
        "\u542b filing_route \u5e94\u529e\u4e8b\u9879\u6e05\u5355\u3002"
    ),
    version=SERVER_VERSION,
    website_url="https://savantcat.cn/mcp-compliance",
    instructions=(
        "合尘猫 · 中国 AI 合规与备案知识库。收录《生成式人工智能服务管理暂行办法》"
        "《人工智能生成合成内容标识办法》《互联网信息服务深度合成管理规定》"
        "《互联网信息服务算法推荐管理规定》的逐条要求，回答「要不要备案」「要不要标注 AI 生成」"
        "「AI 客服上线前要做什么」「用第三方大模型 API 义务在谁身上」这类问题。"
        "所有结论均标注法规条文依据，并用 filing_route 给出应办事项清单。"
    ),
)

RO_ANN = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                         idempotentHint=True, openWorldHint=False)


@mcp.tool(annotations=RO_ANN)
def list_topics(cluster: str = "") -> str:
    """列出全部合规条目的标题清单。

    Args:
        cluster: 可选，按集群过滤。可选值 genai-interim(生成式AI暂行办法) /
                 ai-content-label(AI生成合成内容标识) / deep-synthesis(深度合成) /
                 algo-recommendation(算法推荐与备案) / filing-practice(备案实操)
    """
    rows = INDEX
    if cluster:
        rows = [r for r in rows if r.get("cluster") == cluster]
    out = {
        "total": len(rows),
        "clusters": {k: v.get("count") for k, v in CLUSTERS.items()},
        "topics": [{"slug": r["slug"], "cluster": r["cluster"], "question": r["question"],
                    "short_answer": r["short_answer"]} for r in rows],
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool(annotations=RO_ANN)
def search_compliance(query: str, top_k: int = 5) -> str:
    """按关键词检索中国 AI 合规条目，返回最相关的若干条（含结论与条文依据）。

    Args:
        query: 检索词，如「AI 客服 备案」「AI 生成内容 标注」「训练数据 合法来源」「大数据杀熟」
        top_k: 返回条数，默认 5
    """
    scored = sorted(((_score(query, a), a) for a in TOPICS.values()), key=lambda x: -x[0])
    hits = [{"score": round(s, 2), **_public(a)} for s, a in scored if s > 0][:max(1, top_k)]
    if not hits:
        return json.dumps({"query": query, "hits": 0,
                           "hint": "换个说法，或先用 list_topics 看全部条目",
                           "clusters": list(CLUSTERS)}, ensure_ascii=False, indent=2)
    return json.dumps({"query": query, "hits": len(hits), "results": hits},
                      ensure_ascii=False, indent=2)


@mcp.tool(annotations=RO_ANN)
def get_requirement(slug: str) -> str:
    """按 slug 取一条合规要求的完整内容（正文 + 依据条文 + 落地动作 + 常见追问）。

    Args:
        slug: 条目标识，先用 list_topics 或 search_compliance 取得
    """
    item = TOPICS.get(slug) or TOPICS.get(_slugify(slug))
    if not item:
        cand = [k for k in TOPICS if slug and slug in k]
        return json.dumps({"error": "未找到该 slug", "slug": slug,
                           "did_you_mean": cand[:5],
                           "hint": "先用 list_topics 或 search_compliance 取 slug"},
                          ensure_ascii=False, indent=2)
    return json.dumps(_public(item, with_body=True), ensure_ascii=False, indent=2)


@mcp.tool(annotations=RO_ANN)
def self_check(scope: str = "") -> str:
    """按场景出合规自查清单：把命中条目的「落地动作」汇总成可勾选待办，并列出条文依据。

    Args:
        scope: 场景或关键词，如「AI 客服」「营销文案 标注」「小程序 上架」「全量」；
               留空则返回全量自查清单
    """
    if not scope or scope.strip() in ("全量", "全部", "all"):
        items = list(TOPICS.values())
    else:
        scored = sorted(((_score(scope, a), a) for a in TOPICS.values()), key=lambda x: -x[0])
        items = [a for s, a in scored if s > 3][:8] or list(TOPICS.values())[:5]
    checklist = []
    for it in items:
        for act in it.get("actions") or []:
            checklist.append({"item": act, "from": it.get("slug"),
                              "basis": _basis_text(it)[:1]})
    return json.dumps({
        "scope": scope or "全量",
        "topics_matched": len(items),
        "checklist_count": len(checklist),
        "checklist": checklist,
        "note": "本清单依据公开法规原文整理，具体申报口径以属地网信部门要求为准。",
    }, ensure_ascii=False, indent=2)


@mcp.tool(annotations=RO_ANN)
def filing_route(public_facing: bool = True, generates_content: bool = True,
                 edits_face_or_voice: bool = False, only_internal_use: bool = False) -> str:
    """判断一项 AI 服务要办哪些手续（备案/安全评估/标识），逐条给出法规依据。

    Args:
        public_facing: 是否面向中国境内公众提供服务（仅内部使用请传 false）
        generates_content: 是否能生成文本/图片/音频/视频等内容
        edits_face_or_voice: 是否提供人脸、人声等生物识别信息编辑功能（换脸、AI 配音、数字人）
        only_internal_use: 是否仅企业内部使用、不对外提供
    """
    todo = []

    def add(item, law, article, quote, why):
        todo.append({"应办事项": item, "法规": law, "条文": article, "原文": quote, "为什么": why})

    if only_internal_use or not public_facing:
        add("不触发备案与安全评估，但内容安全、标识、个人信息保护义务仍在",
            "《生成式人工智能服务管理暂行办法》", "第九条",
            ARTICLES.get("genai-interim", {}).get("第九条", "")[:120],
            "备案与安全评估针对「向境内公众提供服务」；内部使用不落入第十七条情形，"
            "但第九条的责任与个人信息义务不因内部使用而免除。")
        if generates_content:
            add("对外发布 AI 生成内容时仍须标注", "《人工智能生成合成内容标识办法》", "第十条",
                ARTICLES.get("ai-content-label", {}).get("第十条", "")[:120],
                "标识义务跟着「发布行为」走，不跟着服务是否对外走。")
        return json.dumps({"结论": "以内部使用为主，暂不涉及备案", "应办事项": todo,
                           "提示": "具体口径以属地网信部门要求为准。"},
                          ensure_ascii=False, indent=2)

    if generates_content:
        add("按《互联网信息服务算法推荐管理规定》履行算法备案手续",
            "《生成式人工智能服务管理暂行办法》", "第十七条",
            ARTICLES.get("genai-interim", {}).get("第十七条", "")[:130],
            "第十七条要求具有舆论属性或社会动员能力的生成式 AI 服务开展安全评估并履行算法备案。")
        add("提供服务之日起十个工作日内提交算法备案，材料齐全的三十个工作日内予以备案",
            "《互联网信息服务算法推荐管理规定》", "第二十四条、第二十五条",
            ARTICLES.get("algo-recommendation", {}).get("第二十四条", "")[:130],
            "备案有明确时限：十个工作日内提交、三十个工作日内办结。")
        add("在网站/应用显著位置标明备案编号并提供公示信息链接",
            "《互联网信息服务算法推荐管理规定》", "第二十六条",
            ARTICLES.get("algo-recommendation", {}).get("第二十六条", "")[:120],
            "备案不是办完就完了，公示义务是持续性的。")
        add("开展安全评估并留存评估材料",
            "《互联网信息服务算法推荐管理规定》", "第二十七条",
            ARTICLES.get("algo-recommendation", {}).get("第二十七条", "")[:120],
            "具有舆论属性或社会动员能力的算法推荐服务提供者应当按规定开展安全评估。")
        add("对生成合成内容添加显式标识 + 在文件元数据中添加隐式标识",
            "《人工智能生成合成内容标识办法》", "第四条、第五条",
            ARTICLES.get("ai-content-label", {}).get("第四条", "")[:130],
            "显式标识让用户看得出来，隐式标识让平台核验得出来，两者都要做。")
        add("在用户服务协议中说明标识的方法与样式",
            "《人工智能生成合成内容标识办法》", "第八条",
            ARTICLES.get("ai-content-label", {}).get("第八条", "")[:120],
            "协议是标识义务的落地载体，也是纠纷时的证据。")
        add("履行算法备案、安全评估手续时一并提交标识相关材料",
            "《人工智能生成合成内容标识办法》", "第十二条",
            ARTICLES.get("ai-content-label", {}).get("第十二条", "")[:120],
            "标识材料与备案材料同批提交，避免二次补件。")
        add("若通过应用商店/小程序分发，配合分发平台核验标识材料",
            "《人工智能生成合成内容标识办法》", "第七条",
            ARTICLES.get("ai-content-label", {}).get("第七条", "")[:120],
            "上架审核环节会要你说明是否提供生成合成服务。")

    if edits_face_or_voice:
        add("对人脸、人声等生物识别信息编辑功能，提示使用者告知被编辑人并取得单独同意",
            "《互联网信息服务深度合成管理规定》", "第十四条",
            ARTICLES.get("deep-synthesis", {}).get("第十四条", "")[:130],
            "生物识别信息编辑是单独同意，不是一揽子同意。")
        add("对可能导致公众混淆或误认的内容，在合理位置添加显著标识",
            "《互联网信息服务深度合成管理规定》", "第十七条",
            ARTICLES.get("deep-synthesis", {}).get("第十七条", "")[:130],
            "第十七条针对的是「可能混淆误认」的服务形态。")
        add("执行真实身份信息认证，并对合成内容进行审核",
            "《互联网信息服务深度合成管理规定》", "第九条、第十条",
            ARTICLES.get("deep-synthesis", {}).get("第九条", "")[:120],
            "实名 + 内容审核是深度合成服务的基础义务。")
        add("上线具有舆论属性或社会动员能力的新产品、新功能前开展安全评估",
            "《互联网信息服务深度合成管理规定》", "第二十条",
            ARTICLES.get("deep-synthesis", {}).get("第二十条", "")[:120],
            "新功能上线前评估，不是上线后补。")

    if not generates_content and not edits_face_or_voice:
        add("若属于算法推荐服务，仍可能触发算法备案与安全评估",
            "《互联网信息服务算法推荐管理规定》", "第二十四条、第二十七条",
            ARTICLES.get("algo-recommendation", {}).get("第二十四条", "")[:130],
            "判断标准是「是否具有舆论属性或者社会动员能力」，不是「是否生成内容」。")

    add("不得恶意删除、篡改、伪造、隐匿标识",
        "《人工智能生成合成内容标识办法》", "第十条",
        ARTICLES.get("ai-content-label", {}).get("第十条", "")[:120],
        "标识义务的另一面是禁止性义务，对提供者和用户同时生效。")

    return json.dumps({
        "输入": {"面向公众": public_facing, "生成内容": generates_content,
                 "人物编辑": edits_face_or_voice, "仅内部使用": only_internal_use},
        "应办事项": todo,
        "提示": "本判定依据法规原文整理，具体申报口径以属地网信部门要求为准。",
    }, ensure_ascii=False, indent=2)


@mcp.tool(annotations=RO_ANN)
def regulation_info() -> str:
    """获取收录法规清单（名称、发布/施行日期、条数、官方原文链接、核心要求）。"""
    return json.dumps({"count": len(REGULATIONS), "regulations": REGULATIONS},
                      ensure_ascii=False, indent=2)


@mcp.tool(annotations=RO_ANN)
def get_article(law: str, article: str) -> str:
    """按条号取某部法规的条文**逐字原文**，并附内容指纹与官方原文链接。

    与 get_requirement 的分工：get_requirement 按「问题」取整条合规要求（含结论与落地动作）；
    get_article 按「条号」取条文原文，用于逐字核对、引用与溯源。

    Args:
        law: 法规名或集群标识。可写全称（《生成式人工智能服务管理暂行办法》）、
             简称（标识办法 / 算法推荐规定）或 cluster
             （genai-interim / ai-content-label / deep-synthesis / algo-recommendation）
        article: 条号，支持「第十条」「10」「第10条」三种写法
    """
    cands = _match_laws(law)
    if not cands:
        return json.dumps({
            "error": "未识别该法规",
            "law_input": law,
            "available": [{"cluster": c, "law": (_reg_of(c) or {}).get("law")} for c in ARTICLES],
            "hint": "用 regulation_info 看收录清单，或直接传 cluster 标识",
        }, ensure_ascii=False, indent=2)
    if len(cands) > 1:
        return json.dumps({
            "error": "法规名不唯一，请指明",
            "law_input": law,
            "candidates": [{"cluster": c, "law": (r or {}).get("law")} for c, r in cands],
        }, ensure_ascii=False, indent=2)

    cluster, reg = cands[0]
    arts = ARTICLES.get(cluster) or {}
    key = _norm_article(article, arts)
    if not key:
        ks = list(arts.keys())
        return json.dumps({
            "error": "该法规没有这一条",
            "law": (reg or {}).get("law"),
            "cluster": cluster,
            "article_input": article,
            "article_count": len(ks),
            "available_range": ("%s … %s" % (ks[0], ks[-1])) if ks else None,
            "hint": "条号支持「第十条」「10」「第10条」",
        }, ensure_ascii=False, indent=2)

    text = arts.get(key, "")
    return json.dumps({
        "law": (reg or {}).get("law"),
        "cluster": cluster,
        "article": key,
        "text": text,
        "chars": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "published": (reg or {}).get("published"),
        "effective": (reg or {}).get("effective"),
        "official_url": (reg or {}).get("url"),
        "disclaimer": "条文按公开发布文本整理；正式引用请以官方公布文本为准（见 official_url）。",
    }, ensure_ascii=False, indent=2)


@mcp.tool(annotations=RO_ANN)
def search_articles(law: str, keywords: str, top_k: int = 5) -> str:
    """在**某一部法规内部**按关键词检索条文，返回命中条号 + 条文摘录。

    用途：已经知道是哪部法规，要定位「哪一条讲了这件事」。

    Args:
        law: 法规名或集群标识（同 get_article 的 law 参数）
        keywords: 检索词，如「训练数据 合法来源」「标识 元数据」「备案 十日」
        top_k: 返回条数，默认 5
    """
    cands = _match_laws(law)
    if not cands:
        return json.dumps({
            "error": "未识别该法规", "law_input": law,
            "available": [{"cluster": c, "law": (_reg_of(c) or {}).get("law")} for c in ARTICLES],
            "hint": "用 regulation_info 看收录清单",
        }, ensure_ascii=False, indent=2)
    if len(cands) > 1:
        return json.dumps({
            "error": "法规名不唯一，请指明", "law_input": law,
            "candidates": [{"cluster": c, "law": (r or {}).get("law")} for c, r in cands],
        }, ensure_ascii=False, indent=2)

    cluster, reg = cands[0]
    arts = ARTICLES.get(cluster) or {}
    qt = _tokens(keywords)
    if not qt:
        return json.dumps({"error": "keywords 不能为空", "law": (reg or {}).get("law")},
                          ensure_ascii=False, indent=2)

    rows = []
    for k, v in arts.items():
        inter = len(qt & _tokens(v))
        if inter:
            rows.append((inter / float(len(qt)), k, v))
    rows.sort(key=lambda x: -x[0])
    hits = [{"article": k, "score": round(s, 3), "excerpt": _excerpt(v, keywords)}
            for s, k, v in rows[:max(1, top_k)]]
    if not hits:
        return json.dumps({
            "law": (reg or {}).get("law"), "cluster": cluster, "keywords": keywords, "hits": 0,
            "hint": "换个说法，或先用 get_article 逐条看；也可用 search_compliance 按问题检索",
        }, ensure_ascii=False, indent=2)
    return json.dumps({
        "law": (reg or {}).get("law"), "cluster": cluster,
        "keywords": keywords, "hits": len(hits), "results": hits,
        "hint": "用 get_article(law, article) 取命中条号的逐字原文",
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 入口
def _selftest():
    print("[1] regulation_info    ->", json.loads(regulation_info())["count"], "部法规")
    r = json.loads(list_topics())
    print("[2] list_topics        -> total=%d clusters=%s" % (r["total"], list(r["clusters"])))
    r = json.loads(search_compliance("AI客服 要不要备案"))
    print("[3] search_compliance  -> hits=%d top=%s" % (r["hits"], r["results"][0]["question"] if r.get("results") else None))
    slug = r["results"][0]["slug"] if r.get("results") else list(TOPICS)[0]
    r = json.loads(get_requirement(slug))
    print("[4] get_requirement    -> %s | body=%d字 basis=%d faqs=%d" % (
        r.get("question"), len(r.get("body_md") or ""), len(r.get("legal_basis") or []), len(r.get("faqs") or [])))
    r = json.loads(self_check("AI客服"))
    print("[5] self_check         -> topics=%d checklist=%d" % (r["topics_matched"], r["checklist_count"]))
    r = json.loads(filing_route(True, True, True, False))
    print("[6] filing_route       -> 应办事项 %d 条" % len(r["应办事项"]))
    r = json.loads(get_requirement("不存在的slug"))
    print("[7] 容错               ->", r.get("error"), "did_you_mean=", r.get("did_you_mean"))
    r = json.loads(get_article("标识办法", "10"))
    print("[8] get_article        -> %s %s chars=%s sha=%s" % (
        r.get("law", "")[:14], r.get("article"), r.get("chars"), (r.get("sha256") or "")[:12]))
    r = json.loads(get_article("genai-interim", "第七条"))
    assert r.get("article") == "第七条" and r.get("text"), "get_article 集群写法失败"
    r = json.loads(search_articles("生成式人工智能服务管理暂行办法", "训练数据 合法来源"))
    print("[9] search_articles    -> hits=%d top=%s" % (
        r.get("hits", 0), (r.get("results") or [{}])[0].get("article")))
    r = json.loads(get_article("标识办法", "第九十九条"))
    print("[10] 条号容错          ->", r.get("error"), "| range=", r.get("available_range"))
    print("\n✅ 10/10 工具自检通过")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", default="stdio", choices=["stdio", "http", "streamable-http", "sse"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--path", default="/mcp")
    ap.add_argument("--stateless", action="store_true",
                    help="无状态模式（反代/公网部署更稳，不依赖会话粘滞）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return

    t = "streamable-http" if args.transport == "http" else args.transport
    if t == "stdio":
        mcp.run()
        return
    sys.stderr.write("[savantcat-ai-compliance] serving on %s:%d%s (%s, stateless=%s)\n"
                     % (args.host, args.port, args.path, t, args.stateless))
    ts = _transport_security()
    kw = {} if ts is None else {"transport_security": ts}
    try:  # mcp 2.x：run() 直收 kwargs
        mcp.run(transport=t, host=args.host, port=args.port,
                streamable_http_path=args.path,
                stateless_http=args.stateless,
                max_request_body_size=1024 * 1024,
                **kw)
    except TypeError:  # mcp 1.x：kwargs 不被接受，走 settings
        s = getattr(mcp, "settings", None)
        if s is not None:
            for k, v in (("host", args.host), ("port", args.port)):
                try:
                    setattr(s, k, v)
                except Exception:
                    pass
        try:
            mcp.run(transport=t, **kw)
        except TypeError:
            mcp.run(transport=t)


if __name__ == "__main__":
    main()
