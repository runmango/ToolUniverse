# 医学检索 Benchmark 首轮真实验证报告

本报告用于验证真实数据、模型执行、排名保存和评价流程。当前没有人工 Gold，**不能据此判断哪种算法医学检索质量更好**。所有 IR 分数保持 null，页面显示待标注。

## 验证范围

- 实验记录起始时间：2026-11-11T16:30:36.049565+00:00（记录使用 UTC）。
- 5 个主题数据集，11 个待审核研究问题，共用同一批 10 篇真实 PubMed 标题/摘要。
- 18 条最终验收记录：15 条真实检索完成、3 条服务未配置而未运行；真实完成共保存 33 组 Query/方法排名。
- 0 个有人工 Gold 的 Query；0 个可评分 Query；没有写入模拟临床标签。
- top_k=20，评价 K=5/10/20，二值相关阈值 ≥ 2。当前文献池只有 10 篇，不能验证大规模召回覆盖。
- 公共文献池哈希：`ce2e95e7047fabd5e16e9f709b5da7a4687dd71421359d7fccfa292a6959856c`。
- 实验引擎代码哈希：`06a3cfc139174170dda5ea58a989bfe0736f914a59ee2296dea145de3edf451c`。

## 实际模型

Embedding：`sentence-transformers/all-MiniLM-L6-v2 本地 Embedding`，revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`；CPU、384 维、最大 256 token、batch_size=16。

BM25 使用 Okapi 实现；Hybrid 使用相同的本地 Embedding 与 BM25 加权 RRF。模型文件已进行 SHA256 核验，真实推理结果已保存。此 MiniLM 是通用英文轻量模型，首轮用于流程基线；中文及医学专门能力尚未经过本项目人工评价。

Qwen3 Embedding/Reranker 和自定义 API 保留为配置方案；没有将 MiniLM 的结果冒充 Qwen3。Elasticsearch、Reranker、LLM-assisted 的 3 条未运行记录均保存配置缺失原因。

## 分主题结果

| 主题 | Query | 候选文献 | 实际完成 | 报告（完整 JSON） |
| --- | ---: | ---: | --- | --- |
| MDS | 3 | 10 | BM25 / Embedding / Hybrid；均待 Gold | [BM25](../.tooluniverse/runtime/workbench/benchmark-reports/MDS-bm25.json) · [Embedding](../.tooluniverse/runtime/workbench/benchmark-reports/MDS-embedding.json) · [Hybrid](../.tooluniverse/runtime/workbench/benchmark-reports/MDS-hybrid.json) |
| AML | 2 | 10 | BM25 / Embedding / Hybrid；均待 Gold | [BM25](../.tooluniverse/runtime/workbench/benchmark-reports/AML-bm25.json) · [Embedding](../.tooluniverse/runtime/workbench/benchmark-reports/AML-embedding.json) · [Hybrid](../.tooluniverse/runtime/workbench/benchmark-reports/AML-hybrid.json) |
| HSCT | 2 | 10 | BM25 / Embedding / Hybrid；均待 Gold | [BM25](../.tooluniverse/runtime/workbench/benchmark-reports/HSCT-bm25.json) · [Embedding](../.tooluniverse/runtime/workbench/benchmark-reports/HSCT-embedding.json) · [Hybrid](../.tooluniverse/runtime/workbench/benchmark-reports/HSCT-hybrid.json) |
| DLI | 2 | 10 | BM25 / Embedding / Hybrid；均待 Gold | [BM25](../.tooluniverse/runtime/workbench/benchmark-reports/DLI-bm25.json) · [Embedding](../.tooluniverse/runtime/workbench/benchmark-reports/DLI-embedding.json) · [Hybrid](../.tooluniverse/runtime/workbench/benchmark-reports/DLI-hybrid.json) |
| GVHD | 2 | 10 | BM25 / Embedding / Hybrid；均待 Gold | [BM25](../.tooluniverse/runtime/workbench/benchmark-reports/GVHD-bm25.json) · [Embedding](../.tooluniverse/runtime/workbench/benchmark-reports/GVHD-embedding.json) · [Hybrid](../.tooluniverse/runtime/workbench/benchmark-reports/GVHD-hybrid.json) |

所有实验的 Precision、Recall、MRR、MAP、NDCG、Hit Rate 均为未评分，而非零分。医学实体标注、试验关联、引用真实性和结论支持性也需要单独人工审核；目前没有相应质量结论。

每条真实完成的实验都执行了保存排名的指标重算，结果与原记录一致。这证明存储与评价可以追溯，不证明检索结果具有临床相关性。

## 文献来源

以下清单只说明本次实际候选文献池，不表示每篇都相关，也不表示这 10 篇覆盖五个主题。标题、摘要、来源记录和各数据集原始问题均已导出 JSONL。

| PMID | 标题 |
| --- | --- |
| [2672599](https://pubmed.ncbi.nlm.nih.gov/2672599/) | Myelodysplastic syndromes. |
| [31466596](https://pubmed.ncbi.nlm.nih.gov/31466596/) | Review of Graft-Versus-Host Disease. |
| [32997910](https://pubmed.ncbi.nlm.nih.gov/32997910/) | Myelodysplastic Syndromes. |
| [33221366](https://pubmed.ncbi.nlm.nih.gov/33221366/) | Myelodysplastic syndromes: ESMO Clinical Practice Guidelines for diagnosis, treatment and follow-up†☆. |
| [37311468](https://pubmed.ncbi.nlm.nih.gov/37311468/) | Efficacy and safety of luspatercept versus epoetin alfa in erythropoiesis-stimulating agent-naive, transfusion-dependent, lower-risk myelodysplastic syndromes (COMMANDS): interim analysis of a phase 3, open-label, randomised controlled trial. |
| [39038479](https://pubmed.ncbi.nlm.nih.gov/39038479/) | Luspatercept versus epoetin alfa in erythropoiesis-stimulating agent-naive, transfusion-dependent, lower-risk myelodysplastic syndromes (COMMANDS): primary analysis of a phase 3, open-label, randomised, controlled trial. |
| [40377899](https://pubmed.ncbi.nlm.nih.gov/40377899/) | Long-Term Transfusion Independence with Luspatercept Versus Epoetin Alfa in Erythropoiesis-Stimulating Agent-Naive, Lower-Risk Myelodysplastic Syndromes in the COMMANDS Trial. |
| [40737597](https://pubmed.ncbi.nlm.nih.gov/40737597/) | XPO1 drives resistance to eprenetapopt and azacitidine and can be targeted in TP53-mutated myeloid malignancies. |
| [41549790](https://pubmed.ncbi.nlm.nih.gov/41549790/) | Impact of Mutational Landscape and Burden on RBC Transfusion Response in Patients With Lower-Risk Myelodysplastic Syndromes (LR-MDS) in the COMMANDS Study. |
| [42060875](https://pubmed.ncbi.nlm.nih.gov/42060875/) | Randomized, Placebo-Controlled Trial of B-Cell Depletion for Prevention of Corticosteroid-Requiring Chronic Graft-Versus-Host Disease. |

## 报告与输入文件

- [MDS Embedding 可阅读报告](../.tooluniverse/runtime/workbench/benchmark-reports/MDS-embedding.md)
- [MDS Embedding 指标 CSV](../.tooluniverse/runtime/workbench/benchmark-reports/MDS-embedding.csv)
- [MDS Query JSONL](../.tooluniverse/runtime/workbench/benchmark-reports/MDS-queries.jsonl)
- [公共候选文献 JSONL](../.tooluniverse/runtime/workbench/benchmark-reports/MDS-documents.jsonl)
- [全部最终实验 ID、版本和运行环境](../.tooluniverse/runtime/workbench/benchmark-validation-runs.json)
- [Elasticsearch 未运行报告](../.tooluniverse/runtime/workbench/benchmark-reports/MDS-elasticsearch.md) · [Reranker 未运行报告](../.tooluniverse/runtime/workbench/benchmark-reports/MDS-embedding_reranker.md) · [LLM-assisted 未运行报告](../.tooluniverse/runtime/workbench/benchmark-reports/MDS-llm_assisted.md)

其余各主题文件同样位于 `.tooluniverse/runtime/workbench/benchmark-reports/`，按“主题-方法”命名，提供 JSON、CSV、Markdown 三种格式。这些运行数据和本地模型不提交到 Git；迁移时需单独保存该目录和数据库。

## 自动化测试结果

| 验证 | 结果 |
| --- | --- |
| Benchmark 后端、指标、存储和协议 | 38 项通过 |
| 既有科研工作台 | 7 项通过 |
| 既有知识图谱 | 17 项通过 |
| 浏览器交互 | 9 组通过，无 JavaScript 错误 |
| TypeScript + Vite 生产构建 | 通过 |
| Python 依赖一致性 pip check | 通过 |

后端及浏览器里的人工标签只存在于明确 TEST ONLY 的临时夹具，不进入正式 Benchmark 数据库。ES、Reranker 和 LLM 的协议单元测试采用隔离响应夹具；本报告没有将它们列为真实服务实验。

浏览器验收记录：[benchmark-browser-results.json](../.tooluniverse/runtime/workbench/screenshots/benchmark-browser-results.json)。正式数据对比截图：[benchmark-real-comparison.png](../.tooluniverse/runtime/workbench/screenshots/benchmark-real-comparison.png)。

## 下一步

由医学专家审核现有问题，扩充五主题候选池，逐条形成 0–3 级 Gold，并建立疾病/药物实体及试验关联标注。冻结新版本后重跑同组模型，才可比较 Recall、MRR、MAP 和 NDCG。接入真实 ES/Reranker/LLM 服务后，可在相同冻结版本上加入六组完整对照。

实现、API 和全部口径见 [模块交付文档](medical-retrieval-benchmark.md)。
