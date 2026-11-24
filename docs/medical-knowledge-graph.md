# 医学知识图谱模块交付说明

2026-11-12 更新：医学实体定义已统一至 `medical_core/model.py`，原模型文件仅兼容导出。图谱数据库现与 Benchmark、科研证据链共用；增加修订、冻结版本、追加人工审核和 `/api/v1` 兼容别名。详见 [统一科研平台实施记录](unified-medical-research-platform.md)。

医学诊疗 Lab 新增独立的“医学知识图谱”页面。第一阶段以 MDS、AML、HSCT、DLI、GVHD 为领域词表，从现有 ToolUniverse 数据接口导入带来源的实体和关系。入口为 `http://127.0.0.1:8090` 左侧导航，接口文档为 `http://127.0.0.1:8090/api/docs`。

## 系统架构

源码检查确认现有项目使用 React 19、TypeScript、Vite、FastAPI 和 SQLite。模块沿用这套架构，没有引入 Vue、PostgreSQL、MySQL、Elasticsearch 或 Neo4j。图谱采用独立的关系型数据库，便于后续迁移，也避免修改会话数据库。

| 层次 | 实现 | 职责 |
| --- | --- | --- |
| 页面 | `frontend/src/KnowledgeGraph.tsx`、`knowledge-graph.css` | 搜索、交互网络、证据详情、路径和导出；随页面访问延迟加载 |
| 可视化 | Cytoscape.js | 节点拖动、缩放、选择、实线与虚线关系 |
| HTTP API | `local/workbench/knowledge_graph/api.py` | `/api/graph` 接口及输入约束 |
| 导入 | `local/workbench/knowledge_graph/ingest.py` | 后台任务、限定条数检索、来源解析、保守的候选关联抽取 |
| 语义模型 | `local/workbench/medical_core/model.py` | 三个模块共用实体类型、领域同义词、关系方向和类型约束 |
| 存储 | `local/workbench/knowledge_graph/store.py`、`schema.sql` | 实体消歧、来源快照、节点与边、邻域和最短连接路径 |
| 外部接口 | 原有 `ScientificTools` → 本机 8088 科研 API | PubMed、ClinicalTrials.gov v2、UniProt、ChEMBL |

数据流为“页面发起同步 → 后端调用已有工具 → 保存来源响应快照 → 归一实体 → 写入有证据的关系 → 页面查询邻域”。同步不调用语言模型；现有科研对话继续使用原有会话和模型调用链。第一阶段不自动读取历史对话内容入图，可通过主题、PMID 或 NCT ID 显式同步。

为保留必要证据，仅对现有 PubMed、ClinicalTrials 解析器增加返回字段：MeSH 编码、结构化摘要、DataBank 注册号、来源修订日期、干预别名、参考文献类型。原字段保持兼容。

## 实体模型与标准化

支持 Disease、Drug、Gene、Protein、ClinicalTrial、Publication、Intervention、Outcome、Biomarker、ClinicalEvent 共十类实体。HSCT、DLI 归为干预；GVHD 在本模块中归为临床事件。登记中的 `BIOLOGICAL` 类型不会覆盖明确匹配的 HSCT、DLI 领域类型。

归一过程依次进行 Unicode NFKC、大小写及空白处理、精确同义词匹配、来源标识匹配和稳定 UUID 分配。保留原始名称及其来源。MDS、Myelodysplastic Syndrome、Myelodysplastic Syndromes 和中文名对应同一个实体；临床亚型和不明确的近似名称不会被强行合并。

Publication、ClinicalTrial、Protein 优先按 PMID、NCT、UniProt 标识解析；不同 PMID 的同名论文仍保留为不同实体。标识冲突会拒绝自动合并。其他实体按类型及规范名称匹配，冲突消解仍需后续人工审核功能。

标准术语只来自实际响应：PubMed MeSH 描述符、ClinicalTrials 精确匹配的 MeSH 条目、UniProt 与 NCBIGene 交叉引用，以及唯一精确匹配的 ChEMBL 首选名称。`kg_identifiers` 可存放 SNOMED CT、RxNorm、LOINC，但第一阶段没有部署这些体系的映射服务；不为缺失映射生成编码。英语名和来源更新时间在来源未提供时允许为空。

首次启动仅建立五个领域的名称和同义词，不建立演示关系。来源不可用时显示失败或部分完成，保留已有数据；不会使用假数据填充图谱。

## 数据库表结构

图谱数据库位于 `.tooluniverse/runtime/workbench/knowledge-graph.sqlite`，会话仍位于原来的 `conversations.sqlite`。采用 SQLite WAL、参数化 SQL、外键和事务。完整 DDL 见 [schema.sql](../local/workbench/knowledge_graph/schema.sql)。

| 表 | 主要字段 | 约束及用途 |
| --- | --- | --- |
| `kg_meta` | `key`, `value` | 数据结构版本，当前为 1 |
| `kg_entities` | `id`, `entity_type`, `standard_name`, `english_name`, `normalized_name`, `properties`, `created_at`, `updated_at` | 稳定实体 ID；扩展属性为 JSON |
| `kg_aliases` | `entity_id`, `raw_name`, `normalized_name`, `source` | 保留原始名称、同义词及来源；规范名称索引 |
| `kg_identifiers` | `scheme`, `value`, `entity_id`, `source` | `(scheme,value)` 主键，保留 PMID、DOI、NCT 等 |
| `kg_sources` | `id`, `provider`, `source_id`, `source_url`, `source_updated_at`, `fetched_at`, `checksum`, `payload` | 来源响应的结构化快照；同一来源不同内容保留独立版本 |
| `kg_entity_sources` | `entity_id`, `source_id` | 实体与多个证据来源的关联 |
| `kg_edges` | `id`, `relation_type`, `source_entity`, `target_entity`, `confidence`, `evidence_source`, `evidence_type`, `verification_status`, `evidence_text`, `evidence_field`, `qualifiers`, `created_at`, `updated_at`, `active` | 置信度限制 0–1；证据外键；起点、终点和证据索引 |
| `kg_sync_runs` | `id`, `query`, `status`, `started_at`, `finished_at`, `report` | 同步进度、逐来源错误、导入条数及检索限制 |

`source_updated_at` 是外部来源日期，`fetched_at` 是本地获取时间，两者独立。快照保存 ToolUniverse 解析后的完整响应，不是上游原始 XML 字节。重复导入保持实体稳定，同一来源更新后停用旧关系并重新写入当前关系，历史来源快照继续保留。

## 关系与证据规则

| 方向 | 关系类型 | 建立条件 |
| --- | --- | --- |
| Disease → Drug | `STUDIED_DRUG` | 同一登记明确包含该疾病和药物，附 NCT 和范围说明；表示研究用药，不表示获益或获批适应症 |
| Disease → Intervention | `STUDIED_INTERVENTION` | 同一登记列明疾病与干预 |
| Disease → Gene | `ASSOCIATED_GENE` | 摘要同句提及，始终是待核验候选 |
| Drug / Intervention → ClinicalTrial | `INVESTIGATED_IN` | 试验干预字段明确列出 |
| Disease / ClinicalEvent / Intervention → ClinicalTrial | `HAS_TRIAL` | 试验 conditions 字段明确列出并归一 |
| ClinicalTrial → Publication | `HAS_RESULT_PUBLICATION` / `HAS_DERIVED_PUBLICATION` | 登记参考文献明确标记 RESULT / DERIVED，按 PMID 关联 |
| ClinicalTrial → Publication | `REFERENCES_PUBLICATION` | BACKGROUND 或未知类型，仅作为登记参考文献 |
| Publication → ClinicalTrial | `MENTIONS_TRIAL` | 摘要或 DataBank 明确出现 NCT ID；单凭注册号不认定结果论文 |
| Publication → Outcome | `REPORTS_OUTCOME` | 结构化摘要中 RESULTS、CONCLUSIONS、FINDINGS 等段落的原文 |
| ClinicalTrial → Outcome | `PLANS_OUTCOME` | 登记的主要终点，与观测到的研究结果分开 |
| Gene → Biomarker | `HAS_BIOMARKER` | 明确出现基因突变词组，但位点与用途未核验，标为候选 |
| Disease → ClinicalEvent | `ASSOCIATED_EVENT` | 同句提及的候选，不建立因果或疾病进展断言 |
| Gene → Protein | `ENCODES` | UniProt 人类记录与预期基因名精确匹配 |
| Publication → Entity | `MENTIONS` | 原文直接提及；仅表示文本出现 |

每条关系都含证据文本、字段位置、来源、置信度、创建日期和核验状态。`source_asserted` 在界面显示“来源明确记载”；`inferred` 显示“推断待核验”并使用虚线。`unverified`、`rejected` 已预留存储值，但不参与当前浏览和路径查询，尚未提供人工审核界面。置信度代表抽取可信程度，不是疗效概率或证据分级。

当前候选来自可解释规则，未启用模型生成关系。疾病基因、基因突变标志物和疾病事件的规则置信度分别为 0.40、0.55、0.35，未经概率校准。不能把这类候选当作已证实医学结论。两篇论文共享药物不会触发试验合并或试验论文关系。

## API 接口

所有接口位于工作台 `8090`。已有本机访问及写请求保护继续生效。同步请求需要请求头 `X-Workbench-Client: 1`。

| 方法及路径 | 参数 | 返回 |
| --- | --- | --- |
| `GET /api/graph/stats` | 无 | 实体分类数量、有效边数、来源数、候选数和最近同步记录 |
| `GET /api/graph/entities` | `q`，`entity_types` 逗号分隔，`limit` 1–100 | `entities`；含同义词、原始标识和来源 |
| `GET /api/graph/entities/{id}` | 实体 UUID | 完整实体属性 |
| `GET /api/graph/neighborhood` | `center`，`depth` 1 或 2，`entity_types`，`include_inferred` 默认 true，`limit` 默认160、最大400 | `nodes`, `edges`, `center`, `depth`, `truncated`, `data_mode` |
| `GET /api/graph/edges/{id}` | 关系 UUID | 关系及 `source_record`，含来源快照 |
| `GET /api/graph/paths` | `source`, `target`，`max_hops` 默认5、最大6，`include_inferred` 默认 false | `found`, `nodes`, `edges`, `truncated`, `traversal` |
| `POST /api/graph/sync` | JSON：`query`，`limit` 默认5、最大10，`molecular` 默认 true | HTTP 202，任务 ID；已有任务时 409 |
| `GET /api/graph/sync/{id}` | 任务 ID | 状态、实际导入计数、逐来源错误 |
| `GET /api/graph/export` | `center`，`depth` 默认2，`format=json或csv`，分类及推断过滤参数 | 最多400节点的邻域导出；JSON 带证据快照，CSV 带节点、边和来源字段 |

不存在的实体、关系或任务返回 404；超范围参数返回 422；无写请求保护头的同步返回 403。节点分类过滤始终保留中心实体，并限制途经的节点类型。路径使用最多6跳的无向连通搜索，保留每条边本来的方向；“存在连接”不代表医学因果，默认不经过推断关系。遍历最多5000个实体，达到上限返回截断标记。

PowerShell 同步示例：

```powershell
$body = @{ query='MDS'; limit=3; molecular=$true } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8090/api/graph/sync `
  -Method Post -Headers @{'X-Workbench-Client'='1'} `
  -ContentType 'application/json' -Body $body

$matches = Invoke-RestMethod -Uri 'http://127.0.0.1:8090/api/graph/entities?q=MDS'
$center = $matches.entities[0].id
Invoke-RestMethod -Uri "http://127.0.0.1:8090/api/graph/neighborhood?center=$center&depth=2"
```

将 `query` 改为 `NCT03682536` 可导入指定试验和登记的 RESULT / DERIVED 论文；改为 `PMID40737597` 可导入指定论文并补全其匹配的人类基因蛋白信息。同步是有界搜索，最多读取12篇关联论文，并分别尝试最多3个药物和3个基因的标准化。失败保留详细原因；重启中断的任务标记为 interrupted，需要重新发起。

## 页面使用

1. 打开“医学知识图谱”，在中心实体搜索框输入 MDS 或其同义词，选择实体。MDS、AML、HSCT、DLI、GVHD 提供快捷入口。
2. 点击“同步真实数据”，查看来源进度、成功计数和失败详情。未导入的主题不会凭空显示关系。
3. 选择一度、二度关系和实体分类；可关闭“显示推断候选”。点击节点查看属性、编码、原始名称和来源，继续展开或设为中心。
4. 点击关系查看证据原文、来源链接、来源更新时间、抽取置信度、限定条件和响应快照。右侧节点与关系列表也支持键盘操作。
5. 搜索目标疾病、药物或其他实体，查询最多6跳的连接路径。没有找到路径仅表示当前已导入数据中没有匹配连接。
6. 使用缩放、适应画布、定位中心按钮调整网络。JSON / CSV 按当前可见图谱导出，包含继续展开和路径结果；界面导出的每条关系都带来源详情。CSV 对公式前缀进行转义。

页面支持深色主题和手机布局。Cytoscape 及图谱页面延迟加载，原有科研对话、连接设置和工具详情页面保留。

## 实测数据和测试结果

2026年11月11日本机通过实际 API 同步 MDS、NCT03682536、PMID40737597 和 GVHD 后，共有65个实体、95条有效关系、16份来源快照，其中5条关系标记为推断候选。22个论文实体包含10份已读取的 PubMed 详情和12个由登记引用建立、尚未补全详情的 PMID 节点；不能将22个节点理解为22篇全文。

ClinicalTrials.gov、PubMed 和 UniProt 实测成功。COMMANDS 登记的论文按实际返回的 DERIVED 等参考类型显示，TP53 成功关联 UniProt P04637。ChEMBL 接口已接入并有精确匹配测试，但本机这次名称查询出现 HTTP 500 / 超时，未写入猜测的 ChEMBL 编码。页面同步报告保留这些失败记录。

| 验证 | 结果 | 覆盖 |
| --- | --- | --- |
| 图谱单元与 API 测试 | 17项通过 | 归一化、幂等、来源版本、同名不同 PMID、禁止按共享药物合并试验、背景引用、候选、路径、过滤、导出、接口保护、断网无假数据、重启、UniProt 物种核验 |
| 来源适配器回归 | 16项通过 | 新增来源字段、NCT 输入验证、原有 DOI 回退和摘要解析 |
| 原有工作台测试 | 7项通过 | 会话持久化、流式行为、错误和工具边界 |
| 浏览器验收 | 8组通过 | 真实 MDS 搜索、二度关系、过滤、画布点击、证据、扩大图谱、路径、两种导出、主题、手机布局、返回对话；无未捕获 JS 错误 |
| TypeScript 与 Vite 构建 | 通过 | 图谱独立代码块及样式，现有前端可构建 |

测试命令：

```powershell
cd D:\code6\ToolUniverse
.\.venv\Scripts\python.exe local\workbench\test_knowledge_graph.py
.\.venv\Scripts\python.exe local\workbench\test_workbench.py
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest tests\unit\test_medical_graph_source_fields.py tests\unit\test_ctgov_nct_id_validation.py tests\unit\test_pubmed_doi_articleids_fallback.py tests\tools\test_pubmed_search_include_abstract.py -q -o addopts=
cd frontend
npm.cmd run build
npm.cmd run test:graph
```

来源测试需要项目 dev 依赖中的 pytest、pytest-timeout、pytest-asyncio。UTF-8 模式用于兼容原有上游测试的文件读取。图谱后端测试使用隔离临时数据库，测试记录不会写入运行数据库。浏览器验收需要工作台运行，并先完成上述真实数据同步；不会发送模型消息或修改现有对话。可用 `WORKBENCH_URL`、`PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指定测试地址及浏览器。

截图和浏览器结果保存在 `.tooluniverse/runtime/workbench/screenshots/knowledge-graph-*.png`、`knowledge-graph-browser-results.json`。运行数据和截图不进入版本控制。

## 后续扩展

1. 增加证据审核与合并队列：人工接受、拒绝或纠正候选，记录审核人、版本和原因；为模型抽取提供独立的模型版本、提示版本和原文跨度，避免混入来源事实。
2. 增加术语服务：配置有授权的 SNOMED CT、RxNorm、LOINC 及 MeSH 版本库；加入 HGNC、变异位点、疾病亚型和药物盐型的消歧规则。
3. 抽取更细的研究结局：区分人群、分组、随访时间、分母、终点定义和统计量，补充全文来源及证据等级；保留注册终点和论文结果的差别。
4. 增加增量任务和规模支持：将单进程同步迁入任务队列，支持取消、分页、重试及来源限流；数据量增长后迁移 PostgreSQL，并按需增加 Elasticsearch 名称与全文检索。
5. 使用稳定实体 ID、关系类型和证据表生成 RDF/OWL 或 JSON-LD，映射 PROV-O 等来源模型；复杂图查询需求明确后再接入图数据库。
6. 在科研对话的来源记录中增加“保存到图谱”入口，并让模型通过只读图谱工具检索已核验关系；历史对话中未经验证的自然语言不直接成为事实边。
