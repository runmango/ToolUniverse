# ToolUniverse 本地项目

项目目录：`D:\code6\ToolUniverse`。

基于 [官方仓库](https://github.com/mims-harvard/ToolUniverse) 的源码安装，版本 **1.5.9**，初始提交 `37bb81a68b332fc5a34b83ead10d36508236ac09`。使用 Python 3.12 和项目内 `.venv`。本地辅助脚本位于 `local/`，上游源码保留原样。

2026-11-10 验证完成：依赖一致性检查通过，成功载入 **2,732** 个工具；MCP 握手及调用通过，暴露 5 个 compact 入口；HTTP API 暴露 51 个方法；真实 UniProt 查询返回 `P05067`。后台服务的停止、重启和重复启动检查均通过。工具目录中的部分功能仍需额外依赖或密钥，见下文。

## 中文科研工作台（推荐入口）

新增「证据智能分析 / Evidence Intelligence」：问题 PICO、真实摘要抽取、研究设计与结局比较、来源高亮、人工质量评价和多论文支持/矛盾关系。沿用统一科研数据库与模型连接，详见 [证据智能分析交付说明](../docs/evidence-intelligence.md)。

2026-11-12：医学知识图谱、检索 Benchmark、科研证据链已接入统一实体、来源、版本与审核层。侧栏新增「科研证据链」，Benchmark 新增「图谱与证据链评测」，支持固定语料上的图谱检索对照。数据库迁移、备份、API、审核规范及实际验收见 [统一科研平台实施记录](../docs/unified-medical-research-platform.md)。

新增「医学知识图谱」独立页面，支持 MDS、AML、HSCT、DLI、GVHD 领域实体、真实来源同步、证据追溯、多跳路径和 JSON/CSV 导出。系统架构、数据库结构、API、测试命令及后续计划见 [医学知识图谱模块交付说明](../docs/medical-knowledge-graph.md)。

浏览器打开 **http://127.0.0.1:8090**。前端参考 Hermes Agent 的工作台布局，提供中文聊天、会话历史、工具调用记录、工具库检索、直接查询、Markdown 导出和浅色/深色主题。

重启电脑后，双击项目根目录 **`start-workbench.cmd`**，会自动启动所需服务并打开浏览器。也可以执行：

```powershell
cd D:\code6\ToolUniverse
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-workbench.ps1
```

**连接 ChatGPT 账号：**

1. 打开左侧「连接设置」。
2. 点击「使用 ChatGPT 登录」，在 OpenAI 官方页面完成授权。
3. 返回工作台，连接状态自动更新。
4. 打开「科研对话」，选择账号返回的模型并输入研究问题。

也支持官方设备码授权，以及 OpenAI API Key。API Key 由后端交给 Codex 保存，不写入浏览器本地存储。模型目录从官方服务读取；列出模型不等于账号有权调用，实际访问与额度以账号和请求结果为准。[官方账号/会话协议](https://learn.chatgpt.com/docs/app-server)

无需登录即可打开「工具宇宙」，检索数据库工具、查看参数并执行只读查询。例如搜索 `UniProt_get_entry_by_accession`，打开详情后直接点击「运行工具」，默认查询 `P05067`。需要密钥的数据库仍需自己的凭据。代码执行和修改状态的工具继续通过原生 SDK/客户端使用。

**本地数据与源码：**

- 前端源码：`frontend/src/`；生产构建：`frontend/dist/`。
- 工作台后端：`local/workbench/`；现有科研 API 继续使用 `8088` 端口。
- 会话数据库：`.tooluniverse/runtime/workbench/conversations.sqlite`。
- 独立的账号授权配置：`.tooluniverse/runtime/workbench/codex-profile/`。
- 日志和验证截图：`.tooluniverse/runtime/workbench/`。运行数据已被 Git 忽略。

停止中文工作台（科研 API 保持运行）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\stop-workbench.ps1
```

前端修改后重新构建，并重启工作台加载最新后端：

```powershell
cd D:\code6\ToolUniverse\frontend
npm.cmd ci
npm.cmd run build
```

开发时可运行 `npm.cmd run dev`，访问 `http://127.0.0.1:5188`，请求代理到已运行的工作台后端 `8090`。本机已安装官方 Codex CLI；更换机器时需先安装 CLI，或在 `.env` 指定 `TOOLUNIVERSE_CODEX_PATH`。

已验证：前端构建、桌面/手机交互、真实 UniProt 查询、官方授权链接生成与取消、Codex RPC 初始化及动态科研工具注册、会话持久化和流式事件测试。**未使用你的账号进行模型推理；首次正式聊天需要你亲自完成登录或填写自己的 API Key。**

## 科研 API 的启动与停止

在 PowerShell 中执行：

```powershell
cd D:\code6\ToolUniverse
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-api.ps1
```

浏览器打开 **http://127.0.0.1:8088/docs**，可查看并试调用 API。健康检查为 http://127.0.0.1:8088/health 。这是上游自带的接口调试页面；中文聊天请使用上面的 `8090` 工作台，或通过 MCP 接入其他 AI 客户端。

启动脚本会在后台运行服务，将 PID 和日志保存到 `.tooluniverse/runtime/`。服务只监听本机，重启电脑后需要重新启动。端口冲突时使用 `-Port 8089`；需要前台日志时使用 `-Foreground`。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\stop-api.ps1
```

## 命令行与 Python

不必手动激活虚拟环境：

```powershell
.\.venv\Scripts\python.exe .\local\run.py cli status
.\.venv\Scripts\python.exe .\local\run.py cli find "protein structure"
.\.venv\Scripts\python.exe .\local\run.py cli info UniProt_get_entry_by_accession
.\.venv\Scripts\python.exe .\local\example.py
```

`local/example.py` 演示通过 Python SDK 查询 UniProt 的公开条目。`local/run.py` 自动载入项目根目录 `.env`，并将工作区和结果缓存保存在项目 `.tooluniverse/` 内。

## MCP 接入

`local/mcp.json` 已提供此机器的完整启动配置，适用于使用 `mcpServers` 格式的客户端。将其中的 `tooluniverse` 条目合并进客户端已有配置，并重新加载该客户端。不要覆盖其他 MCP 服务。若移动项目目录，需要修改配置里的两个绝对路径。

本次仅准备并测试 MCP 服务，未修改客户端的全局设置。客户端将按需启动 stdio 服务，无需先启动 HTTP API。默认使用 compact 模式，通过少量发现/执行入口访问工具库。

## HTTP 调用示例

先载入工具，再通过 `run` 调用具体工具：

```powershell
$base = 'http://127.0.0.1:8088'
$load = @{ method = 'load_tools'; kwargs = @{} } | ConvertTo-Json -Depth 8
Invoke-RestMethod -Uri "$base/api/call" -Method Post -ContentType 'application/json' -Body $load
$request = @{
    method = 'run'
    kwargs = @{
        fcall_str = @{
            name = 'UniProt_get_entry_by_accession'
            arguments = @{ accession = 'P05067'; include_publications = $false }
        }
    }
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Uri "$base/api/call" -Method Post -ContentType 'application/json' -Body $request
```

## 可选密钥和扩展

基础安装包含 SDK、CLI、MCP、HTTP API 和基础依赖。UniProt 等公开工具无需密钥即可调用。需要认证的数据库和 LLM 工具仍需要各自的密钥；在项目根目录 `.env` 中填入，参考 `.env.example` 和上游完整清单 `.env.template`。`.env` 已被 Git 忽略。

本地机器学习、分子化学和可视化等扩展按需求安装；工具出现在目录中，并不代表其额外依赖或服务凭据已经齐全。诊断命令可列出缺失项：

```powershell
.\.venv\Scripts\python.exe .\local\run.py doctor
# 按实际需要选择，例如：
uv pip install --python .venv\Scripts\python.exe --editable '.[visualization]'
```

## 重建环境与验证

`local/constraints-win-py312.txt` 记录此次基础安装的依赖版本。重建基础环境：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\local\setup.ps1
```

验证依赖、SDK、MCP、公开数据库和正在运行的 API：

```powershell
.\.venv\Scripts\python.exe .\local\verify.py --online --api-url http://127.0.0.1:8088
```

省略 `--online` 可跳过公开数据库查询；省略 `--api-url` 可跳过 HTTP 服务检查。报告保存在 `.tooluniverse/runtime/verification.json`，MCP 测试日志保存在 `.tooluniverse/runtime/mcp-verify.log`。

外部数据库请求仍依赖网络和提供方状态。基础部署不下载大型模型，也不包含本地 GPU 推理环境。

## 医学检索 Benchmark

工作台侧栏新增“检索评测 Benchmark”，支持五医学主题的数据集、JSONL 导入、人工 Gold、六类检索配置、指标计算、模型排名对比、负样本审核及实验报告。现有科研对话和知识图谱保留。

详见 [数据规范、数据库、API 与使用文档](../docs/medical-retrieval-benchmark.md) 和 [首轮真实验证报告](../docs/benchmark-validation-report.md)。可选本地 Embedding 依赖位于 `local/requirements-benchmark.txt`；当前机器已配置轻量 CPU 模型。尚无人工 Gold 的实验明确显示待标注，不产生虚构性能分数。
