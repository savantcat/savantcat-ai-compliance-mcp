# 中国 AI 合规与备案 MCP · savantcat-ai-compliance

把中国 AI 法规做成 **Agent 可直接调用** 的 MCP 服务：**每条结论都标注法规条文依据**。

- 远程端点（免 Key）：`https://savantcat.cn/mcp-compliance`
- 条文级网页版：`https://savantcat.cn/answers/compliance/`
- Smithery：`https://smithery.ai/server/@savant0196/savantcat-ai-compliance`

覆盖 4 部法规 + 1 个强制性国标，共 **42 条**条文级原子问答：

| 集群 | 内容 | 条数 |
|---|---|---|
| 生成式AI暂行办法 | 训练数据、内容安全、个人信息、投诉处置、安全评估与算法备案 | 12 |
| AI生成合成内容标识 | 显式/隐式标识、平台核验、不得删除篡改 | 10 |
| 深度合成 | 换脸/AI配音/数字人的实名、标识、生物识别单独同意 | 8 |
| 算法推荐与备案 | 备案时限与公示、用户关闭选项、反大数据杀熟、安全评估 | 8 |
| 备案实操 | 哪些情形触发备案与评估、需要交什么材料 | 4 |

法规原文来源：中国政府网、中央网信办（cac.gov.cn）公开发布版本；《生成式人工智能服务管理暂行办法》《人工智能生成合成内容标识办法》《互联网信息服务深度合成管理规定》《互联网信息服务算法推荐管理规定》，以及强制性国标 GB 45438-2025（标识方法）。

## 工具（8 个，全部只读）

| 工具 | 作用 |
|---|---|
| `list_topics` | 列出全部条目（可按集群过滤） |
| `search_compliance` | 关键词检索，返回结论 + 条文依据 |
| `get_requirement` | 取单条完整内容（正文 + 依据 + 落地动作 + 常见追问） |
| `self_check` | 按场景汇总成自查待办清单 |
| `filing_route` | 输入服务形态，输出应办事项 + 条文依据 |
| `regulation_info` | 收录法规清单（发布/施行日期、条数、官方原文链接） |
| `get_article` | 按**条号**取某部法规的逐字原文（附 sha256 内容指纹与官方原文链接） |
| `search_articles` | 在某部法规**内部**按关键词检索条文，返回命中条号 + 摘录 |

## 用它回答的问题

- 上线 AI 客服 / 智能问答，要不要备案？要不要做安全评估？
- 用别人家的大模型 API 做产品，备案义务在我还是在模型方？
- AI 生成的文案、图片、视频要不要打标？显式标识和隐式标识分别是什么？
- 换脸、AI 配音、数字人做营销素材，要注意什么？
- 算法备案和生成式 AI 备案是一回事吗？多久要办完？

## 本地运行

```bash
pip install -r requirements.txt
python server.py                      # stdio（桌面客户端）
python server.py --selftest           # 不走协议，直接打全部工具
python server.py --transport http --host 127.0.0.1 --port 8766 --stateless
```

## 接入示例

```json
{
  "mcpServers": {
    "cn-ai-compliance": {
      "type": "streamable-http",
      "url": "https://savantcat.cn/mcp-compliance"
    }
  }
}
```

## 数据来源与边界

- 语料只来自**官方公开发布**的法规原文，逐条标注条文号，引文与原文逐字一致（`build_data.py` 内置逐字接地校验，条号或引文对不上直接构建失败）。
- 回答用于**企业自查参考**，不构成法律意见；申报口径以属地网信部门要求为准。
- 本仓库**不含任何客户语料**。

## License

Apache-2.0
