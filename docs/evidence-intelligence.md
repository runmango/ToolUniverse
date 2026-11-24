# Evidence Intelligence：摘要级科研证据分析

实施日期：2026-11-12。入口为工作台 <http://127.0.0.1:8090> 的“证据智能分析”；API 文档：<http://127.0.0.1:8090/api/docs>。

本模块沿用现有 React + TypeScript + Vite、FastAPI、SQLite 和 Cytoscape。它在[统一科研平台](unified-medical-research-platform.md)上增加 PICO、论文结构化抽取、研究比较、人工质量评价和多来源结论，不替换原科研对话、图谱、证据链或 Benchmark。

## 数据模型与数据库

医学对象继续使用 `medical_core` 的 `kg_entities`、`kg_identifiers`、`kg_sources`、`kg_edges`。Publication、ClinicalTrial、Disease、Drug 均引用现有实体 ID，没有新增同名主表。摘要来自共享来源适配器保存的 PubMed 记录；注册试验复用已有 ClinicalTrials.gov v2 导入器。

新增表位于同一 `knowledge-graph.sqlite`，完整 DDL 见 [schema.sql](../local/workbench/evidence_intelligence/schema.sql)。

| 表 | 内容与约束 |
| --- | --- |
| `ei_analyses` | 问题、参数快照、代码哈希、任务状态、PICO、候选文献、图谱版本、设计分布和原始比较结果；异步任务支持取消，重启后标记中断 |
| `ei_extractions` | 分析 ID、统一 Publication 外键、来源外键、结构化字段、原文、数值、来源范围、模型元数据、提取时间；不可更新或删除 |
| `ei_appraisals` | 抽取记录外键、工具及版本、评价目标、逐域人工判断、总体人工判断、审核人、时间；仅追加 |
| `ei_statements` | 人工多论文结论、逐篇引用、supports/contradicts/context、审核人和依据；不可覆盖 |
| 共用 `research_revisions` / `research_reviews` | 抽取、评价和结论的内容修订及审核事件，保留审核历史 |
| 共用 `research_evaluations` | Benchmark 的 `evidence_extraction` 任务：人工字段 Gold、实际值、逐项结果、指标与来源版本 |

首次创建新表前，通过 SQLite backup 自动备份原科研库，并记录 `evidence-intelligence-v1` 迁移清单。本机备份为 `.tooluniverse/runtime/workbench/backups/evidence-intelligence-2026-11-12T15-24-20.957151+00-00.sqlite`。原表未被重建或覆盖。

结构化字段采用 Pydantic 定义，见 [models.py](../local/workbench/evidence_intelligence/models.py)。每个非空抽取字段含 `value`、`span.quote`、`span.start/end`、`status`、`verification_status`。位置以 Python Unicode code point 为单位；前端按原文引文定位，不把这个偏移误当作 JavaScript UTF-16 偏移。

## PICO 解析

支持三种方式：已连接工作台账号的模型、Benchmark 中已启用的自定义 LLM API 配置、保守原文规则。模型名称来自运行配置或动态模型目录，业务代码没有固定某个品牌/型号。

模型只接收问题及 PICO Schema；返回值必须是问题原文的子串，且有可逐字定位的引文。没有明确对照或结局时保留 null / Not Reported。仅出现“效果”“疗效”等泛称时记录原候选，但不当成具体结局，更不会自行补充 OS、CR、PFS、输血独立等条件。

检索式可人工输入；为空时使用问题中明确出现的五领域术语及干预英文词，复用既有同义词映射。规则模式是保守基线，不能覆盖所有表达方式。提供“解析 PICO”预览后可调整检索式，再执行分析。

## 论文抽取与程序校验

模型负责选择和归类原文，程序负责校验和保存。模块没有把标识核验、统计数值、关联推断和质量打分交给一个大 Prompt。

| 信息 | 实现 |
| --- | --- |
| PMID、DOI、标题、发表时间、期刊 | 直接取自保存的 PubMed 来源；不允许模型提供替代 ID |
| 研究设计 | 分类及对应标题/摘要引文或 publication_types 依据；自动标签保持未审核 |
| 人群、样本量、纳排标准、干预、对照 | 各自独立字段与原文定位，不按常识补充 |
| 主要/次要终点 | 引文必须明确包含 primary/secondary 或对应中文；普通结局不自动升级 |
| 随访、不良事件、局限性 | 保留原始文本和研究条件；缺失显示 Not Reported |
| HR / OR / RR | 程序解析原文数值和类型，不计算或补造效应量 |
| 95% CI、P 值 | 保留原始表达、比较符、区间端点；支持中点小数、科学计数法、NR 上限；异常界限明确标记 |
| Evidence Statement | 原文结局陈述、结局类别、方向候选、数值、论文和来源 ID、提取时间及验证状态 |

样本量不自动将组别相加；注册总入组人数不替代论文分析人群数。页面保留样本量和随访的完整上下文，例如论文中的 RP2D 队列限定。95% CI 与具体结局、组别、时间点的对应仍需人工核验；复杂排版中不能可靠解析的区间保留原文，不填入猜测值。

模型返回无效 JSON、不存在的引文或不符合字段规则的数据时，该论文会明确降级为规则提取，并在分析记录中显示问题，任务为部分完成。来源失败也保留失败记录，不使用演示论文替代。没有摘要的论文可以保留书目信息，抽取字段和结论保持缺失。

自动数据记录 `generation.origin`、模型名称、模型版本、Prompt 版本/哈希、响应哈希、代码版本（最终实现）与未验证状态。账号服务未返回具体模型修订号时写 `unknown` 和 `metadata_status=incomplete`，不把动态模型名伪装成精确 checkpoint。首次上线验收的早期记录没有代码哈希，保留其原始历史，不追补虚假的运行版本。

## 证据链与多论文结论

分析视图连接问题、PICO、已存在的医学实体、论文、已存证的试验关系和摘要陈述。关系继续区分“提及注册号”“登记结果论文”“登记衍生论文”“登记参考文献”。不会因研究用了相同药物而补上同一试验关系，也不强行把所有问题串成一条完整路径。

Research Question、PICO 和 Evidence Statement 是分析视图与证据记录；医学实体引用共同图谱。图中的来源关系可点击查看 relation_id、evidence_source；陈述可跳转到论文摘要并高亮对应片段。

人工可建立一项结论并引用多篇论文，每项引用标记支持、矛盾或背景。引文必须属于本次分析保存的原始摘要。保存后支持网络展示多篇论文连向同一人工结论；被拒绝的抽取保留审核标记，并从当前自动差异比较中排除。原始分析、原始抽取和旧审核不会被改写。

## 质量评价

研究设计分布只是研究类别统计，不是证据质量排名。系统不使用期刊影响因子，也不让 LLM 输出可靠性总分；默认 `awaiting_appraisal`，GRADE 为 `not_assessed`。

当前实现的是版本化人工评价记录表：逐域判断、依据、评价目标、工具链接、审核人和时间。它不声称已经复刻官方完整信号问题、决策算法或完成专业评价。

| 工具 | 本模块版本和范围 |
| --- | --- |
| [RoB 2](https://www.riskofbias.info/welcome/rob-2-0-tool) | 2019-08-22，个体随机平行组试验；按具体结局记录五域判断。集群和交叉试验需对应版本 |
| [ROBINS-I V2](https://www.riskofbias.info/welcome/robins-i-v2) | 2025-11-20 草案，非随机干预随访队列的具体结果；明确保存草案版本和六域记录 |
| [AMSTAR 2](https://pmc.ncbi.nlm.nih.gov/articles/PMC5833365/) | 2017，系统评价的 16 项人工判断；不累加为可信度总分，总体判断另需依据 |
| [QUADAS-2](https://www.bristol.ac.uk/population-health-sciences/projects/quadas/) | 按任务要求保留 2011 历史版本，偏倚与适用性分别记录；页面注明官方现推荐 QUADAS-3 |

总体人工判断只有在适用工具、完整域和依据齐备时才允许保存；部分评价保持 incomplete。病例报告、病例对照等没有适用模板时不强行套用工具。摘要缺乏分配隐藏、失访等细节时应记录无信息，不由模型补全。未来可接入人工填写的官方工作表和全文证据定位，但本阶段不自动抓取付费全文。

## 研究差异和冲突检测

系统按明确结局匹配研究对，展示设计、人群、干预、对照、样本口径、随访、主要终点、同类型效应量与区间。效应类型不会混合汇总；没有自动 Meta 分析。

原文支持的改善与未观察到显著获益可形成方向差异提示。只有记录的研究条件完整一致时才提示 potential_conflict，其余显示条件不同或不完整。显著与不显著本身不证明两研究的效应存在统计学差异，也不说明某篇论文错误。出现同一试验 ID 时提示可能存在重叠人群，不能当成两份独立重复证据。

## API

以下路径均以前缀 `/api/v1` 访问，同时保留 `/api/evidence-intelligence` 兼容入口。沿用工作台本机访问边界、`X-Workbench-Client: 1` 写入标识、统一错误结构和 `X-Request-ID` 日志。

| 方法与路径 | 作用 |
| --- | --- |
| `GET /evidence-intelligence/schema` | 字段、研究设计、人工评价工具与版本 |
| `POST /evidence-intelligence/pico` | 单独解析问题 PICO，返回来源/模型信息和检索式 |
| `GET/POST /evidence-intelligence/analyses` | 列出历史／创建后台分析任务 |
| `GET /evidence-intelligence/analyses/{id}` | 阶段、来源、抽取、历史和当前比较、支持网络、人工记录 |
| `POST /evidence-intelligence/analyses/{id}/cancel` | 停止该分析及其拥有的同步任务 |
| `GET /evidence-intelligence/analyses/{id}/export?format=json` | 包含冻结图谱与完整来源的 JSON 报告 |
| `GET /evidence-intelligence/analyses/{id}/export?format=markdown` | 可阅读的科研证据报告及结构化明细 |
| `POST /evidence-intelligence/extractions/{id}/review` | accepted/rejected/needs_review；要求审核人、依据和明确确认 |
| `POST /evidence-intelligence/extractions/{id}/appraisals` | 保存版本化人工质量评价 |
| `POST /evidence-intelligence/analyses/{id}/statements` | 多论文支持/矛盾结论及逐篇原文 |
| `POST /benchmark/evidence-extraction-evaluations` | 独立人工 Gold 的字段抽取评价 |
| `GET /benchmark/research-evaluations` | 在原 Benchmark 中读取新的证据抽取评价历史 |

分析请求示例（不含虚假医学数据）：

```json
{
  "question": "MDS患者接受Venetoclax联合Azacitidine治疗的效果如何？",
  "search_query": "",
  "live_search": true,
  "limit": 3,
  "mode": "rules",
  "model": "",
  "model_profile_id": null,
  "release_id": null
}
```

`mode=account` 时选择模型目录中的 `model`；`mode=api` 引用 Benchmark 已启用的 LLM 配置 ID，密钥继续通过环境变量引用。固定历史 `release_id` 与实时检索不可同时启用。每次最多分析 8 篇、仅一个后台分析任务，避免无界来源调用。

Benchmark 的 labels 每项为 `{extraction_id, field, expected, reason}`。`expected=null` 表示专家确认该字段未报告。标注在检索和抽取完成后才读取，绝不传给模型。FieldExactMatch 衡量字面字段一致性，不等于医学结论正确率；空 Gold 返回 null / awaiting_gold。此指标与文献 Recall@K、MRR、NDCG 和质量评价分开保存。

## 实测与限制

- 真实联网分析 `32d0c7484ba34b24b6c8a740e3f49e7c`：检索导入 2 篇论文和 2 项试验，完成实际模型抽取。PMID 39652823 有摘要，提取 6 条可定位陈述；PMID 36377194 没有摘要，保留书目、空字段和零陈述，没有虚构研究数据。
- 最终版本另行验证了真实模型 PICO：对照为空，“效果”保留为未指定具体结局。真实论文在最终规则流程中验证了来源、版本与导出。
- 后端新增 23 项测试通过；原工作台 7、图谱 17、Benchmark 38、统一科研平台 17 项回归通过，共 102 项。前端 TypeScript/Vite 构建通过；新增页面 8 组隔离浏览器测试和 1 组正式库只读测试通过，覆盖定位、导出、人工审核、质量记录、多论文结论、Benchmark、手机布局与真实来源支持网络。
- 自动化中的人工标注与示例医学文本仅存在于 TEST ONLY 临时数据库。正式库没有由本次测试写入人工质量等级或临床 Gold。

本机验收产物：[intelligence-validation](../.tooluniverse/runtime/workbench/intelligence-validation/)，含 `live-llm-report.json`、`final-llm-pico.json`、最终规则报告和汇总。浏览器记录位于 `screenshots/intelligence-browser-results.json`；运行数据与报告不提交到 Git。

自动化命令：

```powershell
.\.venv\Scripts\python.exe local\workbench\test_evidence_intelligence.py
cd frontend
npm run build
node tests\intelligence.mjs
```

本阶段完成摘要级工作流。未实现合法全文抓取、完整官方质量工具算法、自动 GRADE、自动因果推断、自动 Meta 分析或临床指南生成。后续可在授权范围内接入 PMC 开放全文、逐段来源定位、PICO 人群与时间点标准化、正式专家双人标注及全文级质量工作表；已有共同实体、固定来源和追加审核可直接复用。
