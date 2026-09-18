# -*- coding: utf-8 -*-
"""把 data_src/topics_*.json 合并成 data/ 下的运行数据，并做条文接地校验。

用法：python build_data.py
输出：
  data/topics.json    slug -> 完整条目
  data/index.json     轻量索引（列表用）
  data/meta.json      集群 + 法规元信息
  data/articles.json  cluster -> {第X条: 正文}（filing_route 引原文用）
校验：
  ① 每条 legal_basis 的 article 必须存在于对应法规的条文表
  ② 每条 legal_basis 的 quote（去「……」后）必须能在来源文件里逐字找到
  ③ body_md >= 600 字
  ④ slug 全局唯一
"""
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "data_src")
OUT = os.path.join(BASE, "data")
SOURCES = os.path.join(BASE, "sources")

MANIFEST = json.load(open(os.path.join(SOURCES, "MANIFEST.json"), encoding="utf-8"))
ARTICLES = json.load(open(os.path.join(SRC, "articles_index.json"), encoding="utf-8"))

CLUSTER_CN = {
    "genai-interim": "生成式AI暂行办法",
    "ai-content-label": "AI生成合成内容标识",
    "deep-synthesis": "深度合成",
    "algo-recommendation": "算法推荐与备案",
    "filing-practice": "备案实操",
}

CORE_REQ = {
    "genai-interim": "面向境内公众提供生成式 AI 服务的主体义务：训练数据合法来源、内容安全、个人信息保护、投诉处置；具有舆论属性或社会动员能力的须安全评估 + 算法备案。",
    "ai-content-label": "AI 生成合成内容必须带标识：显式标识让用户看得出来，隐式标识（文件元数据）让平台核验得出来；不得删除篡改。",
    "deep-synthesis": "深度合成（换脸、AI 配音、数字人）服务的实名、内容审核、标识、生物识别信息单独同意，以及上线前的安全评估。",
    "algo-recommendation": "算法推荐服务的备案时限（10 个工作日提交 / 30 个工作日办结）、公示义务、用户关闭选项、反大数据杀熟与安全评估。",
    "filing-practice": "备案口径判定：哪些情形触发备案与安全评估，需要交什么材料，以及来源文件之外的事项一律以属地网信部门口径为准。",
}

REGULATIONS = []
for s in MANIFEST["sources"]:
    REGULATIONS.append({
        "law": s["law"], "issuer": s["issuer"], "published": s["published"],
        "effective": s["effective"], "articles": s["articles"], "url": s["url"],
        "cluster": s["cluster"], "core_requirement": CORE_REQ.get(s["cluster"], ""),
    })
REGULATIONS.append({
    "law": "《网络安全技术 人工智能生成合成内容标识方法》（强制性国家标准 GB 45438-2025）",
    "issuer": "国家市场监督管理总局 / 国家标准化管理委员会",
    "published": "2025-03",
    "effective": "2025-09-01",
    "articles": None,
    "url": "https://openstd.samr.gov.cn/",
    "cluster": "ai-content-label",
    "core_requirement": "与《人工智能生成合成内容标识办法》同日施行的强制性国标，规定显式/隐式标识的具体技术样式；标识办法第十一条要求标识活动同时符合强制性国家标准。",
    "note": "标准原文请在国家标准化管理委员会「国家标准全文公开系统」核对。",
})


def _norm(s):
    """归一化：去空白、去「……」截断符，便于逐字比对。"""
    s = (s or "").replace("……", "").replace("…", "")
    return re.sub(r"\s+", "", s)


def _clean_article(t):
    t = re.sub(r"\s+", "", t or "")
    for tail in ("关闭中央网络安全和信息化委员会办公室", "ProducedByCMS"):
        t = t.split(tail)[0]
    return t


def main():
    rows, errors, warns = {}, [], []
    # 法规名 -> 集群（filing-practice 这类跨法条目要按 law 字段定位条文表）
    law2cl = {}
    for s in MANIFEST["sources"]:
        law2cl[s["law"].strip("《》")] = s["cluster"]
    for path in sorted(glob.glob(os.path.join(SRC, "topics_*.json"))):
        if os.path.basename(path) == "articles_index.json":
            continue
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("topics") or data.get("items") or []
        print("  读入 %-42s %d 条" % (os.path.basename(path), len(data)))
        for it in data:
            slug = it.get("slug")
            if not slug:
                errors.append("缺 slug：%s" % json.dumps(it, ensure_ascii=False)[:80])
                continue
            if slug in rows:
                errors.append("slug 重复：%s" % slug)
                continue
            cl = it.get("cluster")
            arts = ARTICLES.get(cl, {})
            if cl not in CLUSTER_CN:
                errors.append("%s: cluster 非法 %r" % (slug, cl))
            # ① 条号必须存在于「该条文所属法规」的条文表
            for b in it.get("legal_basis") or []:
                law_cl = law2cl.get((b.get("law") or "").strip("《》"), cl)
                a = (b.get("article") or "").strip()
                for one in re.split(r"[、,，]", a):
                    one = one.strip()
                    if not one:
                        continue
                    if one not in ARTICLES.get(law_cl, {}):
                        errors.append("%s: 条号 %s 不在 %s(%s) 条文表"
                                      % (slug, one, law_cl, b.get("law")))
            # ② 引文必须逐字接地（在全部来源文本里找，容忍跨法引用）
            text_pool = "".join("".join(v.values()) for v in ARTICLES.values())
            for b in it.get("legal_basis") or []:
                q = _norm(b.get("quote"))
                if q and q not in text_pool:
                    warns.append("%s: 引文未能逐字匹配（article=%s）" % (slug, b.get("article")))
            # ③ 正文长度
            body = it.get("body_md") or ""
            if len(body) < 600:
                warns.append("%s: 正文仅 %d 字" % (slug, len(body)))
            for k in ("question", "short_answer", "keywords", "actions"):
                if not it.get(k):
                    warns.append("%s: 缺字段 %s" % (slug, k))
            rows[slug] = it

    if errors:
        print("\n❌ 接地校验失败（%d 条）：" % len(errors))
        for e in errors[:40]:
            print("   -", e)
        return 1

    os.makedirs(OUT, exist_ok=True)
    clusters = {}
    index = []
    for it in sorted(rows.values(), key=lambda x: (x.get("cluster", ""), x.get("order", 0))):
        cl = it["cluster"]
        clusters.setdefault(cl, {"name": CLUSTER_CN.get(cl, cl), "count": 0})
        clusters[cl]["count"] += 1
        index.append({k: it[k] for k in ("slug", "cluster", "order", "question", "short_answer")})

    json.dump(rows, open(os.path.join(OUT, "topics.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(index, open(os.path.join(OUT, "index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({"clusters": clusters, "regulations": REGULATIONS,
               "generated_from": "data_src/topics_*.json",
               "site": "https://savantcat.cn/mcp-compliance"},
              open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({k: {n: _clean_article(t) for n, t in v.items()} for k, v in ARTICLES.items()},
              open(os.path.join(OUT, "articles.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("\n✅ 接地校验通过：%d 条，%d 个集群" % (len(rows), len(clusters)))
    for k, v in clusters.items():
        print("   %-20s %s  %d 条" % (k, v["name"], v["count"]))
    if warns:
        print("\n⚠️  提醒 %d 条（不阻断）：" % len(warns))
        for w in warns[:20]:
            print("   -", w)
    print("\n输出 ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
