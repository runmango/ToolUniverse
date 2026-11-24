# 医学诊疗 Lab：Medical Retrieval Benchmark

2026-11-12 更新：已完成统一科研架构迁移，Benchmark 与知识图谱、科研证据链共用实体、来源及 `knowledge-graph.sqlite`。新增固定图谱检索对照和独立图谱/证据链评价；实施与迁移说明见 [统一科研平台](unified-medical-research-platform.md)。早期验证报告保留当时事实。

本模块为现有工作台增加独立的检索实验、人工标注、版本对比和报告导出能力。入口为工作台侧栏的“检索评测 Benchmark”，地址 `http://127.0.0.1:8090`。实际验证记录见 [首轮验证报告](benchmark-validation-report.md)。

## 架构与代码位置

源码检查确认本项目使用 **React 19 + TypeScript + Vite、FastAPI、SQLite**。现有环境没有配置 Elasticsearch 或专用 Embedding/Reranker 服务，因此沿用当前架构，增加可配置检索适配器。没有迁移前端框架或替换原有对话系统。

```text
Benchmark Dashboard（React，独立懒加载页面）
    ↓ /api/benchmark
FastAPI → 数据集与人工审核 → 不可变数据快照（SQLite）
    ↓ 固定 query + 文献标题/摘要；不传 Gold
实验执行器 → BM25 / ES / Dense / Reranker / Hybrid / LLM 查询扩展
    ↓ 保存文献 ID、排名、模型参数、执行轨迹
评价器 ← 同一快照中的人工 Gold
    ↓ 检索指标、独立医学指标、历史对比、JSON/CSV/Markdown 报告
```

| 位置 | 职责 |
| --- | --- |
| `local/workbench/benchmark/spec.py` | 数据规范、导入校验、标注来源校验 |
| `local/workbench/benchmark/store.py` | 数据集版本、审计、模型配置、实验存储 |
| `local/workbench/benchmark/retrieval.py` | 六种检索方法与真实模型适配 |
| `local/workbench/benchmark/metrics.py` | 可独立测试的评价函数 |
| `local/workbench/benchmark/experiments.py` | 实验调度、重算、负样本、医学审核、报告 |
| `local/workbench/benchmark/api.py` | FastAPI 路由及请求结构 |
| `frontend/src/Benchmark*.tsx`、`benchmark.css` | 仪表盘、图表、数据标注、模型对比 |
| `src/tooluniverse/database_setup/embedder.py` | 复用现有模型加载器；新增可选 revision/缓存/设备参数 |

通过 `medical_core` 复用真实 PubMed 元数据、统一实体、来源记录和疾病同义词归一机制。Benchmark 与知识图谱共用科研数据库，会话数据库独立；生成候选、审核或运行实验不会改写对话。

## Benchmark 数据规范

采用 UTF-8 JSONL，一行一个对象。问题和文献分别导入；先导入文献，再导入标注。一次导入最多 20 MB，第一阶段每个数据集最多 500 个 Query、2,000 篇文献。整批校验失败会回滚，不会保留半批数据。相同 ID 更新创建新版本；同批重复 ID 拒绝。

预置 MDS、AML、HSCT、DLI、GVHD 五个数据集，共 11 个**待审核问题**。初始代码不内置医学 Gold 或虚构文献。当前机器上的真实文献来自已有知识图谱的 PubMed 详情，重新安装时需从图谱或 JSONL 导入。

### Query

以下为可导入的待标注问题，没有赋予任何论文相关性等级：

```json
{"query_id":"MDS_001","query":"MDS患者使用Venetoclax联合Azacitidine的相关临床研究","domain":"MDS","query_type":"clinical_research","entities":[{"name":"MDS","type":"Disease"},{"name":"Venetoclax","type":"Drug"}],"relevant_documents":[],"annotation_source":"ai","verification_status":"candidate","annotator":"","review_complete":false}
```

| 字段 | 规则 |
| --- | --- |
| `query_id` | 数据集内唯一，最长 100 字符 |
| `query` | 实际送入检索器的文本，最长 3,000 字符 |
| `domain` | MDS / AML / HSCT / DLI / GVHD；数据集本身还支持 MIXED |
| `query_type` | 如 clinical_research、trial_matching、biomarker，可自定义 |
| `entities` | `{name, type, entity_id?}` 数组；作为元数据保存，不私下提供给某个模型 |
| `relevant_documents` | `{document_id 或 pmid, relevance, annotation_source, verification_status, annotator, note?}` 数组 |
| `relevance` | 严格整数：0 不相关、1 弱相关、2 相关、3 高度相关 |
| `annotation_source` | human / ai / model / rule / synthetic / imported |
| `verification_status` | verified / candidate；导入的 unverified、pending 归为 candidate |
| `annotator` | 审核人；真正进入 Gold 必须有人名或审核身份记录 |
| `review_complete` | 人工确认整个固定文献池已逐条审核，不能仅凭勾选跳过未标注文献 |
| `medical_gold` | 可选，疾病/药物实体召回的独立人工标注，结构见下文 |

仅逐条满足 `annotation_source=human`、`verification_status=verified`、非空审核人的标签参与 Gold 评价。JSONL 中仅写 `verified` 不足以成为 Gold：导入必须显式提交 `attest_human_gold=true` 和审核人。`ai/model/rule/synthetic` 标签即使同时带 verified，也保持候选；需通过逐条人工审核再保存。

人工维护会保存审核记录及新数据版本。修改已有问题文本时，相关性标签和医学实体 Gold 必须先降为候选，再重新审核。修改文献标题或摘要会使对应的相关性标签失效。增加文献会清除“全池已审核”状态。已有人工数据不能被普通候选导入静默覆盖。

### Document

文献字段：`document_id`、`title`、`text`（也接受输入 `abstract`）、`identifiers`、`source`、`entities`、`entity_annotation`。`title` 必填；摘要缺失允许空文本，但不能伪造摘要。所有算法统一检索 `title + 换行 + text`。

未提供 document_id 时，用数字 PMID 生成 `PMID:<PMID>`，或使用 `NCT` 加 8 位数字的试验编号。支持显式内部 document_id。`identifiers` 保留 PMID、DOI、NCT；`source` 保留提供方、URL、来源记录等。标识格式校验不能证明论文或试验真实存在，应从真实来源导入并保留来源信息。

从图谱导入只选已有 PMID、标题且详情已完成的 Publication；只存在试验引用占位信息的节点不会被当成完整文献。导入不会擅自更新已冻结的旧文献。

### 独立的医学标注

Query 的 `medical_gold` 和文献的实体标注与相关性等级分开维护。以下结构仅说明字段，不是已完成的人工标注：

```json
{
  "medical_gold": {
    "entities": [{"name":"MDS","type":"Disease"},{"name":"Venetoclax","type":"Drug"}],
    "annotation_source":"ai",
    "verification_status":"candidate",
    "annotator":""
  }
}
```

文献的 `entities` 保存识别到的实体，`entity_annotation` 保存相同的来源/状态/审核人字段。优先按 `entity_id` 匹配；否则复用图谱领域同义词及规范化名称。药物别名建议显式提供相同 entity_id，系统不会虚构 RxNorm 等编码。

实验完成后，可为指定实验、指定 Query 另行提交关联与生成质量标注：

| 数组 | 每条字段 | 审核规则 |
| --- | --- | --- |
| `trial_matches` | document_id、nct_id、correct、verification_reference | 文献必须在本次结果中；NCT 格式校验；判真/假需证据来源 |
| `citations` | citation_id、document_id、exists、verification_reference | citation_id 唯一；判真/假需实际核验依据 |
| `claims` | claim_id、claim_text、citation_ids、supported、evidence_document_id、evidence_quote | 判支持时必须有被引用且核验为真的文献，以及该检索文献中的原文片段 |
| `answer` | 可选 text、model_name、model_version 等 | 保存待评估答案及模型信息，不自动生成临床结论 |

`correct/exists/supported` 接受 true、false、null。null 表示未审核，不计入已审核分母。引用真实与引用支持结论是两个独立判断。原文片段匹配只是可追溯约束，不能取代人工对研究对象、干预和结局是否支持结论的判断。不得凭同药物自动认定同一试验。

## 数据库设计

当前数据库路径：`.tooluniverse/runtime/workbench/knowledge-graph.sqlite`；原 `benchmark.sqlite` 保留为迁移前副本。DDL 见 [schema.sql](../local/workbench/benchmark/schema.sql)，共同版本、绑定和审核表见 [medical_core/schema.sql](../local/workbench/medical_core/schema.sql)。采用 SQLite WAL、外键、参数化 SQL 和版本变更事务。

| 表 | 主键及主要内容 |
| --- | --- |
| `benchmark_datasets` | id；名称、领域、说明、current_version、创建时间 |
| `benchmark_versions` | id；dataset_id 外键、version、content_hash、corpus_hash、payload、变更说明；dataset_id/version 唯一 |
| `benchmark_models` | id；名称、版本、method、config、时间；配置另存为新记录 |
| `benchmark_experiments` | id；group_id、dataset_version_id、model_snapshot、config、status、metrics、runtime、error、时间、parent_id |
| `benchmark_results` | experiment_id/query_id 复合主键；ranking、逐 Query metrics、trace |
| `benchmark_audit` | id；数据集和版本外键、query_id、actor、action、details、时间 |
| `benchmark_negatives` | id；版本、query、document、挖掘方法、医学类别、分数、状态、审核记录；版本/query/doc/方法唯一 |
| `benchmark_assessments` | id；实验/query、独立医学标注 payload、metrics、审核人、时间 |

第一阶段将 Query、文献和标注放入不可变版本 JSON 快照，避免历史实验读取被修改的 Gold；逐 Query 排名单独存表。`corpus_hash` 只包含文献 ID、标题、正文；`content_hash` 覆盖问题、全部标注和文献元数据。实验复制模型配置，不引用可变的“最新配置”。

## 评价指标及边界

实现位于 [metrics.py](../local/workbench/benchmark/metrics.py)，版本 `medical-ir-v1`。默认二值相关阈值为等级 ≥ 2，也可统一配置为 ≥ 1 或 ≥ 3。所有模型使用相同阈值、K 和快照。

设 R 为该 Query 的全部人工相关文献集合，`rel(i)` 表示第 i 位是否属于 R：

| 指标 | 公式 / 口径 |
| --- | --- |
| Precision@K | 前 K 位相关文献数 / K；不足 K 条仍除以 K |
| Recall@K | 前 K 位相关文献数 / \|R\| |
| MRR | 首个相关文献排名的倒数；在保存的 top_k 内计算，再按 Query 宏平均 |
| MAP | AP = Σ(Precision@i × rel(i)) / \|R\|；在保存的 top_k 内计算，再宏平均 |
| NDCG@K | DCG = Σ((2^grade−1)/log2(i+1))；除以该 Query 人工等级降序的 IDCG@K |
| Hit Rate@K | 前 K 位是否至少命中一条相关文献，再按 Query 宏平均 |

固定输出 K=5、10、20。实验 top_k 为 20–200；**MRR/MAP 是该保存深度内的截断指标**。不同 top_k 不允许直接做优劣对比。等级 1 即使低于二值相关阈值，仍在 NDCG 中有分级收益。

- 有人工相关标签而没有返回结果：相关指标为 0。
- 没有人工 Gold：指标为 null，状态为 awaiting_gold；不作为 0 分计入宏平均。
- 只有负例且全池尚未审核：仍为待标注，不能推断“没有相关文献”。
- 全池明确审核且没有二值相关文献：二值指标为 0 并纳入宏平均；NDCG 按实际分级标签计算。
- 未标注文献按零收益处理，同时报告 `judged_fraction`、`unjudged_count` 和完整审核 Query 数，暴露标注不足。
- 去重后评价，重复结果不会增加命中数；实验执行器拒绝重复或文献池外的结果。
- 聚合报告区分总 Query 数、已评分数、未评分数、全池审核数。不能把部分标注的 Recall 当成真实全集召回率。

医学指标独立展示：

| 指标 | 分母与缺失处理 |
| --- | --- |
| Disease Entity Recall@K | 返回文献人工实体中命中的目标疾病数 / Query 的人工目标疾病数 |
| Drug Entity Recall@K | 同上，限定药物；Query 或任一返回文献实体未审核则为 null，并报告实体标注覆盖率 |
| Clinical Trial Matching Accuracy | 已人工审核的文献–NCT 关联中，正确关联比例 |
| Citation Accuracy | 已人工审核引用中，真实存在的比例 |
| Evidence Support Rate | 已人工审核结论中，被所引证据支持的比例 |

后三项分别保存分母、总数和覆盖率；不合并为 IR 分数。疾病/药物目标集为空时为 null；有目标但没有结果时实体召回为 0。

## 检索算法与配置

模型名称只存在于配置数据，不写死在业务分支。配置包含 `name`、`version`、`method`、`config`；通过页面另存或 `POST /models` 创建。

| 方法 | 实现 | 当前真实验证情况 |
| --- | --- | --- |
| BM25 | Okapi BM25；默认 k1=1.5、b=0.75；英文词项和中文单字/双字；无匹配返回空集 | 已运行 |
| Elasticsearch | 独立固定语料索引；索引名包含语料/分析器哈希；单分片、写锁、数量/哈希核对、ID 限定、稳定并列排序 | 已实现 REST 适配和协议测试，尚无真实服务 |
| Embedding | 复用 ToolUniverse Embedder，Sentence Transformers 或自定义 API；规范化余弦相似度 | 已运行本地模型 |
| Embedding + Reranker | Dense 召回后，CrossEncoder / Qwen3 yes-no 打分 / 自定义 API 重排；recall_depth ≥ top_k | 适配已实现，未下载 Reranker 权重 |
| Hybrid | BM25 和 Dense 的加权 RRF；默认 rrf_k=60、dense_weight=0.5 | 已运行 |
| LLM-assisted | LLM 扩展查询并抽取候选实体，再使用配置的基础检索方法；保存扩展及 prompt 哈希 | 适配已实现，尚无已配置服务 |

每个检索器只接收相同的 Query 原始文本和固定文献池的 ID/标题/摘要。Gold、医学实体 Gold、标注状态和答案不传入检索器。LLM 仅接收查询及统一扩展提示，不能通过额外实时搜索扩大文献范围。

### 已配置的轻量本地模型

- `sentence-transformers/all-MiniLM-L6-v2`
- 固定 revision：`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`
- 384 维，CPU，max_seq_length=256，batch_size=16。
- 模型位于工作区 `.tooluniverse/runtime/workbench/benchmark-cache/models/`；配置文件为 `benchmark-local-model.json`。
- 记录权重/分词文件 SHA256；文件变化时拒绝复用旧模型版本。向量缓存由文献、模型配置、检查点和依赖版本共同确定。

这是通用英文语义模型，当前用于建立真实、轻量的可运行基线；不代表已经验证适合医学中文或高精度临床检索。长摘要截断参数也属于实验配置。模型能力说明见 [官方模型卡](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)。

重建本地推理依赖及下载指定版本：

```powershell
.\.venv\Scripts\python.exe -m pip install -r local\requirements-benchmark.txt
.\.venv\Scripts\python.exe scripts\setup_benchmark_embedding.py --model sentence-transformers/all-MiniLM-L6-v2 --revision 1110a243fdf4706b3f48f1d95db1a4f5529b4d41
```

下载工具会真实编码测试文本，记录维度，生成可导入的配置；不会创建 Gold 或指标。Qwen3 Embedding、Qwen3 Reranker 模板保留在 [starter.json](../local/workbench/benchmark/starter.json)，启用前需要固定 revision、取得真实权重并另存配置。参考 [Qwen3 Embedding 模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) 和 [Qwen3 Reranker 模型卡](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)。

### 自定义 API 协议

`api_url` 是完整调用端点；必须填 model_name、model_version。认证用 `api_key_env` 引用服务进程环境变量，默认 Bearer，可选 ApiKey/Basic；不在配置里保存密钥。端点不允许带账号、密码或查询参数。

| 适配器 | 请求体 | 支持的响应 |
| --- | --- | --- |
| Embedding | `{model,input:[文本...]}` | `{data:[{index,embedding:[数值...]}],model?}` 或 `{embeddings:[[数值...]],model?}` |
| Reranker | `{model,query,documents:[文本...],top_n}` | `{results:[{index,relevance_score}]}` 或 data 数组/score 字段；必须覆盖全部请求候选 |
| LLM 扩展 | `{model,messages,temperature,max_tokens,seed?}` | `choices[0].message.content` 内的 JSON：expanded_query、entities |

Embedding 校验返回数量、索引、维度、有限数及非零向量。Reranker 校验索引完整性及有限分数。外部服务不可用时保留未运行/失败及原因，不降级成随机向量或模拟分数。远程提供方声明的模型版本不能保证权重永不变化，实验同时保存提供方实际返回的模型信息和 LLM fingerprint；严格重现建议使用固定本地权重。

## 负样本挖掘

支持三种方法，结果先进入审核队列：

1. Random：固定 seed 的候选采样，排除已知等级 > 0 的文献。
2. Hard：从同一文献池、同一问题文本的已完成 Embedding / Reranker / Hybrid 实验中取高排名非正例；语义相似不等于已确认不相关。
3. In-batch：使用同版本其他 Query 的人工正例作为当前 Query 候选，排除当前 Query 已知正例。

只有已有人工 0 级标签的文献能直接沿用负例状态，其余仍待审核。审核需记录人、依据、医学混淆类别；确认负例写入新版本的 0 级人工标签，旧实验不变。问题或文献内容已变化时拒绝使用过期候选审核。

医学类别包括同疾病不同亚型、同药物不同适应症、同药物不同临床试验、同主题不同研究对象、相似术语不同含义；系统不会仅凭相似度替审核人做这些判定。

## API

前缀 `/api/benchmark`，完整交互文档：`http://127.0.0.1:8090/api/docs`。写请求沿用工作台的本机/同源限制及 `X-Workbench-Client: 1`；JSON 请求需 `Content-Type: application/json`。

| 方法和路径 | 参数/功能 |
| --- | --- |
| GET `/stats` | 数据集、问题、已标注 Query、去重文献、模型配置数量 |
| GET/POST `/datasets` | 列表 / 创建 `{name,domain,description?}` |
| GET `/datasets/{id}?version=N` | 指定或最新快照及版本列表 |
| POST `/datasets/{id}/import` | `{kind:queries或documents,content:JSONL文本,attest_human_gold:false,actor}` |
| POST `/datasets/{id}/import-graph` | 从既有图谱导入真实 PubMed 详情 |
| POST `/datasets/{id}/queries` | `{record:Query,actor,confirm_human_review:true}` |
| POST `/datasets/{id}/documents` | `{record:Document,actor,confirm_human_review:true}` |
| GET `/datasets/{id}/export?kind=queries&version=N` | 下载问题/文献 JSONL |
| GET `/datasets/{id}/audit` | 人工变更审计 |
| GET/POST `/models` | 模型列表 / 新增不可变配置版本 |
| POST `/experiments` | `{dataset_version_id,model_ids:[1–8个配置ID],top_k:20,relevance_threshold:2}`；返回 202 和 group_id/experiment_ids |
| GET `/experiments?dataset_id=...` | 最近 200 条实验记录 |
| GET `/experiments/{id}` | 模型及数据版本、运行环境、逐 Query 排名、指标、独立医学评价 |
| POST `/experiments/{id}/replay` | 用保存的排名与冻结 Gold 重算指标，核对是否一致 |
| POST `/experiments/{id}/rerun` | 用原始快照和模型配置重新执行，保留 parent_id |
| GET `/experiments/{id}/report?format=json或csv或markdown` | 完整实验报告 |
| GET `/compare?ids=ID1,ID2&query_id=...` | 并列结果、标签、文献属性、可比较性判定 |
| POST `/negatives/mine` | `{dataset_version_id,query_id,method,count:10,seed:42,experiment_id?}` |
| GET `/negatives?dataset_version_id=...` | 当前版本负样本审核队列 |
| POST `/negatives/{id}/review` | `{actor,accepted,category,note}` |
| POST `/experiments/{id}/queries/{query_id}/assessments` | `{record:独立医学标注,actor,confirm_human_review:true}` |

常见状态码：404 对象不存在；422 参数/人工审核校验未通过；403 本机写入边界未通过；409 已有实验组运行。异步运行失败在实验 status/error 中查询，不伪装为成功。

状态：queued → running → completed / completed_unscored / not_run / failed；服务重启将未完成任务标为 interrupted。completed_unscored 表示检索真实完成、尚无可评分 Gold。

## 可复现性与报告

每次实验保存数据版本及两个哈希、模型名称/版本/完整参数、向量维度、检索方法、top_k、重排配置、时间、依赖版本、代码哈希、逐 Query 文献 ID/分数/轨迹、指标及冻结标注。重跑不修改历史记录。LLM 重跑复用原实验保存的查询扩展，避免把新一次随机扩展误认为原实验复现。

对比检查 content_hash、corpus_hash、top_k、阈值、代码哈希；有差异时显示原因并停止直接性能图表对比。不同模型版本可在相同条件下比较。真实排序分数仅用于各模型内部排序，不能把 BM25 分数与余弦相似度直接比较。

- JSON：完整数据快照、模型配置、排名、指标、医学标注，适合程序复核。
- CSV：逐 Query、逐 K 的检索指标和 result_document_ids；空指标保持空值；防止导出字符串触发表格公式。
- Markdown：实验概况、指标、配置、逐 Query 证据和医学评价，适合审阅。

当前机器已导出的真实报告位于 `.tooluniverse/runtime/workbench/benchmark-reports/`；`*-queries.jsonl` 和 `*-documents.jsonl` 保存对应输入。JSON 报告中的完整快照可独立追溯本次指标的输入。

## 前端使用流程

1. 在“数据集与 Gold”创建/选择数据集，导入文献及 Query；在顶部选择待比较的数据版本。
2. 逐篇查看标题摘要、标注等级、确认审核人。AI 候选需逐条确认；完整审核选项要求全池都有等级。
3. 在“模型配置”选择/复制模板，固定模型版本和参数，保存新配置。
4. 在“实验与仪表盘”同时选 BM25、Embedding、Reranker、Hybrid 等配置，运行对照。记录表显示未运行、待 Gold 或评价完成。
5. 勾选同条件实验后，查看 Precision@K 趋势、Recall@K 对照、MRR/MAP/NDCG 和六项雷达图；可切 K，并提供数值表。无 Gold 时图表展示空状态。
6. “模型结果对比”选择同一 Query，横向查看文献、排名、相对位次变化及 Gold；下方单独维护医学关联和生成评价。
7. “负样本审核”生成并人工审核候选；回到新版本再运行实验。实验记录中可直接重算、重跑或导出。

页面支持深色主题及手机窄屏；既有科研对话、知识库、医学知识图谱入口保留。

## 自动化验证

```powershell
# 项目根目录
.\.venv\Scripts\python.exe local\workbench\test_benchmark.py
.\.venv\Scripts\python.exe local\workbench\test_workbench.py
.\.venv\Scripts\python.exe local\workbench\test_knowledge_graph.py

# frontend 目录；先启动正式工作台并完成一次真实 Embedding 实验
npm run build
node tests\benchmark.mjs
```

Benchmark 38 项自动化测试覆盖手算指标、等级收益、无结果/无 Gold、AI 标签隔离、实体指标、生成证据约束、版本不可变、输入校验、负样本、模型适配协议、数据泄漏隔离、报告、复现和 API。协议测试中的模拟响应只用于临时测试，不进入真实实验库。既有工作台 7 项、知识图谱 17 项回归通过。

浏览器 9 组测试在独立临时数据库中验证标注、导入、模型版本、图表、单 Query 对比、导出、医学审核、负样本、移动端及返回对话；只读检查正式库真实排名。测试图片带 TEST ONLY 的属于夹具，不作为医学性能报告。

## 当前范围与后续扩展

当前交付包含可运行的检索平台和真实 CPU 基线；没有人工 Gold，不能给出医学性能优胜结论。当前共用 10 篇真实论文只是链路验证语料，不能代表五个医学主题的覆盖度。

建议下一阶段先由医学专家扩大并分层标注五领域文献池，建立双人独立标注、分歧裁决和独立测试集，再做模型选择。随后接入实际 ES、医学多语 Embedding、Qwen3 Reranker 和 LLM 服务，并在相同快照上运行六组对照。

更大规模时，将版本快照及标注拆为 PostgreSQL 的规范化表/内容寻址对象，增加任务队列、断点续跑和用户权限；保持当前实验 ID、数据哈希和导出协议。可增加 bootstrap 置信区间、配对显著性检验、时序测试集、分病种/语言/问题类型分析，以及药物别名和标准术语映射。当前为本机单用户、单实验组后台执行模式，不声称具备多机构标注权限或大规模训练能力。
