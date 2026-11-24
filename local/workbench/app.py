"""Loopback-only web application for the Chinese research workbench."""
import asyncio
from collections import Counter
from contextlib import asynccontextmanager, suppress
import json
from pathlib import Path
import time
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .codex_bridge import CodexBridge, clean_error
from .storage import ConversationStore
from .tools import ScientificTools, logical_error
from .knowledge_graph.store import GraphStore
from .knowledge_graph.ingest import GraphImporter
from .knowledge_graph.api import router as graph_router
from .benchmark.store import BenchmarkStore
from .benchmark.experiments import ExperimentRunner
from .benchmark.api import router as benchmark_router
from .medical_core.service import ResearchService
from .medical_core.sources import MedicalSources
from .medical_core.migration import migrate
from .medical_core.api import router as research_router
from .medical_core.http import install, versioned, error as api_error, configure_logging
from .evidence_chain.service import EvidenceService
from .evidence_chain.api import router as evidence_router
from .evidence_intelligence.api import router as intelligence_router
from .evidence_intelligence.service import IntelligenceService

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".tooluniverse" / "runtime" / "workbench"
FRONTEND = ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app):
    app.state.store = ConversationStore(RUNTIME / "conversations.sqlite")
    configure_logging(RUNTIME)
    app.state.tools = ScientificTools()
    app.state.research_error = None
    try:
        migrate(RUNTIME)
        path = RUNTIME / 'knowledge-graph.sqlite'
        app.state.graph = GraphStore(path)
        app.state.research = ResearchService(path)
        app.state.medical_sources = MedicalSources(app.state.tools, app.state.research)
        app.state.graph_importer = GraphImporter(app.state.graph, app.state.medical_sources)
        app.state.benchmark = BenchmarkStore(path)
        app.state.benchmark_runner = ExperimentRunner(app.state.benchmark, app.state.research)
        app.state.evidence = EvidenceService(app.state.research)
        for dataset in app.state.benchmark.datasets():
            snapshot = app.state.benchmark.snapshot(dataset['version_id'])
            app.state.research.bind_benchmark(snapshot['id'], snapshot['payload'])
        if not app.state.research.releases(): app.state.research.create_release('统一架构迁移基线；此前未保存的关系历史未知')
    except Exception as exc:
        import logging
        app.state.research_error = type(exc).__name__
        logging.getLogger('medical_lab').error(json.dumps({'event':'research_startup_failed','type':type(exc).__name__}))
    app.state.bridge = CodexBridge(RUNTIME, app.state.tools)
    app.state.intelligence_error = None
    if not app.state.research_error:
        try:
            app.state.intelligence = IntelligenceService(app.state.research, app.state.graph_importer, app.state.benchmark, app.state.bridge)
        except Exception as exc:
            app.state.intelligence_error = type(exc).__name__
    app.state.active_sessions = set()
    yield
    if getattr(app.state, 'intelligence', None): await app.state.intelligence.close()
    if getattr(app.state, 'graph_importer', None): await app.state.graph_importer.close()
    if getattr(app.state, 'benchmark_runner', None): await app.state.benchmark_runner.close()
    await app.state.bridge.close()
    await app.state.tools.client.aclose()


app = FastAPI(title="ToolUniverse 中文工作台", version="0.1.0", lifespan=lifespan, docs_url="/api/docs")
app.include_router(graph_router)
app.include_router(benchmark_router)
app.include_router(research_router)
app.include_router(evidence_router)
app.include_router(intelligence_router)
for router in (graph_router, benchmark_router, research_router, evidence_router, intelligence_router): app.include_router(versioned(router))


@app.middleware("http")
async def local_boundary(request, call_next):
    authority = request.headers.get("host", "")
    host = urlparse("http://" + authority).hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return api_error(request,421,"此工作台只允许本机访问。","LOCAL_ONLY")
    origin = request.headers.get("origin")
    if origin and origin != "http://" + authority:
        return api_error(request,403,"不允许跨站请求。","CROSS_ORIGIN")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("x-workbench-client") != "1":
        return api_error(request,403,"请求缺少工作台标识。","CLIENT_REQUIRED")
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RuntimeError)
async def runtime_error(_request, exc):
    return JSONResponse({"detail": clean_error(exc)}, status_code=502)


@app.exception_handler(ValueError)
async def value_error(_request, exc):
    return JSONResponse({"detail": clean_error(exc)}, status_code=400)


@app.get("/api/health")
async def health():
    return {"status": "healthy", "service": "tooluniverse-workbench", "research_status": "unavailable" if getattr(app.state,"research_error",None) else "ready",
            "evidence_intelligence_status":"unavailable" if getattr(app.state,"research_error",None) or getattr(app.state,"intelligence_error",None) else "ready"}


async def account_status():
    try:
        return await app.state.bridge.account()
    except Exception as exc:
        return {"installed": bool(app.state.bridge.executable), "connected": False, "error": clean_error(exc)}


@app.get("/api/status")
async def status():
    tool_status, account = await asyncio.gather(app.state.tools.health(), account_status())
    return {"tools": tool_status, "connection": account, "version": "0.1.0"}


@app.get("/api/account")
async def account():
    return await account_status()


class LoginRequest(BaseModel):
    mode: str = "chatgpt"
    api_key: str | None = Field(default=None, max_length=1024)


@app.post("/api/account/login")
async def login(body: LoginRequest):
    if body.mode not in {"chatgpt", "chatgptDeviceCode", "apiKey"}:
        raise HTTPException(400, "不支持的登录方式")
    if body.mode == "apiKey" and not (body.api_key and body.api_key.strip()):
        raise HTTPException(400, "请输入 API Key")
    if app.state.active_sessions:
        raise HTTPException(409, "请先结束正在进行的研究，再切换账号。")
    return await app.state.bridge.login(body.mode, body.api_key.strip() if body.api_key else None)


@app.post("/api/account/cancel")
async def cancel_login():
    bridge = app.state.bridge
    if bridge.login_id:
        await bridge.rpc("account/login/cancel", {"loginId": bridge.login_id})
        bridge.login_id = None
    bridge.login_result = None
    return {"ok": True}


@app.post("/api/account/logout")
async def logout():
    if app.state.active_sessions:
        raise HTTPException(409, "请先结束正在进行的研究，再退出账号。")
    await app.state.bridge.start()
    await app.state.bridge.rpc("account/logout")
    app.state.bridge.login_id = None
    app.state.bridge.login_result = None
    return {"ok": True}


@app.get("/api/models")
async def models():
    return {"models": await app.state.bridge.models()}


class SessionRequest(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=100)


@app.get("/api/sessions")
async def sessions():
    return {"sessions": app.state.store.list()}


@app.post("/api/sessions")
async def create_session(body: SessionRequest):
    return app.state.store.create(body.title.strip() or "新对话")


def get_session(session_id):
    try:
        return app.state.store.get(session_id)
    except KeyError:
        raise HTTPException(404, "会话不存在") from None


@app.get("/api/sessions/{session_id}")
async def session(session_id: str):
    return {**get_session(session_id), "active": session_id in app.state.active_sessions}


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, body: SessionRequest):
    get_session(session_id)
    app.state.store.update(session_id, title=body.title.strip() or "新对话")
    return get_session(session_id)


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    if session_id in app.state.active_sessions:
        raise HTTPException(409, "请先停止此会话的回答。")
    get_session(session_id)
    app.state.store.delete(session_id)
    return {"ok": True}


@app.get("/api/tools")
async def list_tools(q: str = Query("", max_length=200), category: str = "",
                     limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    return await app.state.tools.search(q, category, limit, offset)


@app.get("/api/categories")
async def categories():
    catalog = await app.state.tools.load()
    counts = Counter(item.get("category", "other") for item in catalog)
    return {"total": len(catalog), "categories": [{"name": name, "count": count} for name, count in counts.most_common()]}


@app.get("/api/tools/{name}")
async def tool_info(name: str):
    return await app.state.tools.info(name)


class ToolRequest(BaseModel):
    arguments: dict = Field(default_factory=dict)


@app.post("/api/tools/{name}/run")
async def run_tool(name: str, body: ToolRequest):
    started = time.monotonic()
    result = await app.state.tools.execute(name, body.arguments)
    return {"success": logical_error(result) is None, "result": result, "elapsed": round(time.monotonic() - started, 2)}


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=20000)
    model: str | None = Field(default=None, max_length=150)


def sse(payload):
    return "data: " + json.dumps(payload, ensure_ascii=False, default=str) + "\n\n"


@app.post("/api/chat")
async def chat(body: ChatRequest, request: Request):
    record = get_session(body.session_id)
    if body.session_id in app.state.active_sessions:
        raise HTTPException(409, "此会话正在回答，请等待或先停止。")
    if not body.text.strip():
        raise HTTPException(400, "请输入研究问题")
    bridge = app.state.bridge
    connection = await bridge.account()
    if not connection["connected"]:
        raise HTTPException(401, "请先在连接设置中登录 ChatGPT 或配置 API Key。")
    # The authentication call yields to the event loop. Check again before
    # acquiring the session so simultaneous requests cannot start two turns.
    if body.session_id in app.state.active_sessions:
        raise HTTPException(409, "此会话正在回答，请等待或先停止。")
    app.state.active_sessions.add(body.session_id)

    async def stream():
        thread_id = None
        items = {}
        tool_events = {}
        completed = False
        final_status = "interrupted"
        error_message = None
        generation={'origin':'ai','model_name':body.model or record.get('model') or 'unknown',
                    'model_version':'unknown','verification_status':'unverified','metadata_status':'incomplete'}
        try:
            if not record["messages"]:
                app.state.store.update(body.session_id, title=body.text.strip()[:32])
            user = app.state.store.message(body.session_id, "user", body.text.strip())
            yield sse({"type": "user", "message": user, "session_id": body.session_id})
            thread_id = await bridge.thread(record.get("thread_id"), body.model)
            app.state.store.update(body.session_id, thread_id=thread_id, model=body.model)
            queue = asyncio.Queue()
            bridge.queues[thread_id] = queue
            params = {"threadId": thread_id, "input": [{"type": "text", "text": body.text.strip()}]}
            if body.model:
                params["model"] = body.model
            started = await bridge.rpc("turn/start", params, timeout=60)
            generation['turn_id']=started['turn']['id']
            bridge.active_turns[thread_id] = started["turn"]["id"]
            yield sse({"type": "started", "turn_id": started["turn"]["id"]})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 20)
                except TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": keepalive\n\n"
                    continue
                method, params = event["method"], event["params"]
                if method == "item/agentMessage/delta":
                    item_id = params.get("itemId", "assistant")
                    items[item_id] = items.get(item_id, "") + params.get("delta", "")
                    yield sse({"type": "text", "content": "\n\n".join(items.values())})
                elif method in {"item/started", "item/completed"}:
                    item = params.get("item", {})
                    kind = item.get("type")
                    if kind == "agentMessage" and method == "item/completed":
                        items[item.get("id", "assistant")] = item.get("text", "")
                        yield sse({"type": "text", "content": "\n\n".join(items.values())})
                    elif kind in {"dynamicToolCall", "mcpToolCall"}:
                        tool_id = item.get("id", "tool")
                        output = item.get("contentItems") or item.get("result")
                        tool_events[tool_id] = {"id": tool_id, "name": item.get("tool", "科研工具"),
                            "arguments": item.get("arguments", {}), "status": item.get("status", "inProgress"),
                            "success": item.get("success"), "output": output}
                        yield sse({"type": "tool", "tool": tool_events[tool_id]})
                elif method == "turn/completed":
                    turn = params.get("turn", {})
                    final_status = turn.get("status", "failed")
                    completed = True
                    if final_status == "failed":
                        error_message = clean_error((turn.get("error") or {}).get("message", "本次回答失败，请重试。"))
                    break
                elif method == "bridge/closed":
                    error_message = "模型连接已断开，请重试。"
                    final_status = "failed"
                    break
                elif method == "error":
                    if not params.get("willRetry"):
                        error_message = clean_error((params.get("error") or {}).get("message", "模型请求失败"))
                        final_status = "failed"
                        break
            content = "\n\n".join(items.values())
            if error_message:
                yield sse({"type": "error", "message": error_message})
            assistant = app.state.store.message(body.session_id, "assistant", content or error_message or "回答已停止。",
                                                list(tool_events.values()), final_status, generation=generation)
            yield sse({"type": "done", "message": assistant})
        except asyncio.CancelledError:
            content = "\n\n".join(items.values())
            if content or tool_events:
                app.state.store.message(body.session_id, "assistant", content or "回答已停止。", list(tool_events.values()), "interrupted", generation=generation)
            raise
        except Exception as exc:
            error_message = clean_error(exc)
            app.state.store.message(body.session_id, "assistant", error_message, list(tool_events.values()), "failed", generation=generation)
            yield sse({"type": "error", "message": error_message})
        finally:
            if thread_id:
                if not completed:
                    with suppress(Exception):
                        await bridge.interrupt(thread_id)
                bridge.queues.pop(thread_id, None)
                bridge.active_turns.pop(thread_id, None)
            app.state.active_sessions.discard(body.session_id)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.post("/api/sessions/{session_id}/interrupt")
async def interrupt(session_id: str):
    record = get_session(session_id)
    if record.get("thread_id"):
        await app.state.bridge.interrupt(record["thread_id"])
    return {"ok": True}


if (FRONTEND / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")


@app.get("/favicon.svg")
async def favicon():
    return FileResponse(ROOT / "frontend" / "public" / "favicon.svg")


@app.get("/")
async def index():
    if not (FRONTEND / "index.html").exists():
        raise HTTPException(503, "前端尚未构建，请在 frontend 目录执行 npm.cmd install 和 npm.cmd run build。")
    return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description="ToolUniverse Chinese workbench")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")

install(app)
