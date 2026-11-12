# wensiku-cli — 问思库本地文件评审（文件不出你的机器）

让查理·芒格、巴菲特、霍华德·马克斯与量化/创业导师们评审你的文件——**敏感文件全程不离开你的电脑**。

## 隐私模型（开源可审计，本仓库就是这个承诺的凭证）

| 步骤 | 发生在哪 | 外发什么 |
|---|---|---|
| 文件解析（txt/md/csv/json/log/pdf ≤1.5MB） | 你的机器 | 无 |
| 语料检索（`--raw` 之外才发生） | 问思库 API | **只有你的评审指令文本**，不含文件内容 |
| 答案生成 | 你的机器 → 智谱开放平台 | 文件内容 + 检索语料（你自己的 API key，你与智谱的协议关系） |
| 引文核验（逐字比对金句库） | 你的机器 | 无 |

问思库服务器**接触不到你的文件**；不写历史、不占网页对话额度（CLI 检索调用耗邀请码额度 1 次/评审）。每一步的代码都在本仓库单文件 [`wensiku.py`](wensiku.py) 里，无混淆、无二进制、无遥测。

## 使用

```bash
pip install pypdf          # 可选，仅 PDF 需要
export WENSIKU_CODE=WSK-…  # 问思库网页免费领取的邀请码
export ZHIPU_API_KEY=sk-…  # 任选一家模型厂商的 key（见下表），或写进 .env

./wensiku.py 复盘.txt                          # 芒格评审
./wensiku.py bp.md -m pg,altman -f '重点看风险'  # 圆桌 + 附言
./wensiku.py 合同.pdf --raw                    # 只解析，零外发（先验证工具读得懂）
```

## 自带模型（BYOK，不绑定厂商）

| --provider | 默认模型 | API key 环境变量 |
|---|---|---|
| `glm`（智谱） | glm-4.6 | `ZHIPU_API_KEY` |
| `openai` | gpt-4o | `OPENAI_API_KEY` |
| `claude` | anthropic 兼容层 | `ANTHROPIC_API_KEY` |
| `qwen`（阿里百炼） | qwen-max | `DASHSCOPE_API_KEY` 或 `ALIYUN_BAILIAN_API_KEY` |
| `deepseek` | deepseek-chat | `DEEPSEEK_API_KEY` |
| `kimi`（月之暗面） | kimi-k2-turbo-preview | `MOONSHOT_API_KEY` |
| `ollama`（本地，零外发） | qwen3:8b | 无需 key |

- 不指定 `--provider` 时按已设置的环境变量**自动探测**
- `--model` 覆盖默认模型；自托管端点（vLLM/llama.cpp 等）用 `--provider ollama --base-url http://你的端点/v1`
- 全部走 OpenAI 兼容调用层；引文核验与"每人至多一句"纪律**与厂商无关**，换模型不换纪律

## 质量纪律（与问思库网页版同源）

- 导师观点**只来自真实语料检索**，逐字引文**只许出自已核验金句库**、每人至多一句，违规自动重写
- 语料未覆盖的方面会**明确说明边界**而不是编造
- 每份评审末尾附**可核验的出处清单**（含原文链接）

## 边界与诚实声明

- 生成调用使用你自己的智谱 key（GLM 系列）；若文件连模型服务商都不能看，请使用 `--raw` 或不要使用本工具
- AI 生成内容仅供参考，不构成投资建议
- 个人维护项目：issue 不保证响应，但本仓库永远保持可审计

## License

MIT（代码）。问思库语料检索服务与导师语料库本身不开源、不随本仓库分发。
