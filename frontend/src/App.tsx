import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { Activity, ArrowDown, ArrowRight, ArrowUp, HeartPulse, BookOpen, Check, CheckCircle2, ChevronDown, ChevronRight, CircleHelp, Copy, Database, Download, ExternalLink, FlaskConical, Globe2, KeyRound, Layers3, Loader2, LogIn, LogOut, Menu, MessageSquare, Microscope, Moon, MoreHorizontal, UserRound, PanelLeftClose, Plus, Search, Settings2, ShieldCheck, Sparkles, Square, Sun, TerminalSquare, Trash2, X, Dna, Pencil, PlugZap, RefreshCw, Network, BarChart3 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkCjkFriendly from 'remark-cjk-friendly/parseOnly';
import remarkCjkFriendlyGfmStrikethrough from 'remark-cjk-friendly-gfm-strikethrough/parseOnly';
import { api, post, streamChat, download, type Connection, type Message, type Model, type ScientificTool, type Session, type Status, type ToolEvent, type ToolInfo } from './api';

const KnowledgeGraph = lazy(() => import('./KnowledgeGraph'));
const Research = lazy(() => import('./Research'));
const EvidenceIntelligence = lazy(() => import('./EvidenceIntelligence'));
const Benchmark = lazy(() => import('./Benchmark'));
type View = 'chat' | 'tools' | 'settings' | 'graph' | 'benchmark' | 'evidence' | 'intelligence';
type Notice = { text: string; error?: boolean };
const formatCount = (count: number) => new Intl.NumberFormat('en-US').format(count);
const starters = [
  { icon: BookOpen, title: '追踪前沿文献', tag: 'PubMed · Europe PMC', prompt: '检索最近两年关于 GLP-1 受体激动剂与阿尔茨海默病的研究，整理主要结论并提供论文来源。', className: 'orange' },
  { icon: Dna, title: '探索蛋白与基因', tag: 'UniProt · Ensembl', prompt: '查询 UniProt 中 P05067 蛋白的功能、编码基因和已知疾病关联，并注明数据库来源。', className: 'green' },
  { icon: FlaskConical, title: '寻找药物与靶点', tag: 'ChEMBL · Open Targets', prompt: '检索 EGFR 与非小细胞肺癌的靶点证据及相关药物，区分已获批药物和研究阶段药物。', className: 'purple' },
  { icon: Activity, title: '查找临床研究', tag: 'ClinicalTrials.gov', prompt: '检索阿尔茨海默病正在招募的临床试验，列出研究名称、阶段和注册编号。', className: 'blue' },
];

function BrandMark({ large = false }: { large?: boolean }) {
  return <span className={`brand-mark ${large ? 'large' : ''}`}><svg width={large ? 35 : 25} height={large ? 35 : 25} viewBox="0 0 32 32" aria-hidden="true"><path d="M12 3h8v9h9v8h-9v9h-8v-9H3v-8h9Z" fill="currentColor" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" /><path d="M5 16h5l3-5 5 10 3-5h6" fill="none" stroke="#1e5946" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></span>;
}

function ToolTrace({ tool }: { tool: ToolEvent }) {
  const [open, setOpen] = useState(false);
  const running = tool.status === 'inProgress';
  const failed = tool.success === false || tool.status === 'failed';
  const labels: Record<string, string> = { search_scientific_tools: '检索科研工具', inspect_scientific_tool: '查看工具参数', execute_scientific_tool: '查询科学数据库' };
  return <div className={`tool-trace ${failed ? 'failed' : ''}`}>
    <button className="tool-trace-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
      {running ? <Loader2 size={15} className="spin" /> : failed ? <X size={15} /> : <CheckCircle2 size={15} />}
      <span>{labels[tool.name] || tool.name}</span><code>{String(tool.arguments?.name || tool.arguments?.query || '')}</code>
      <span className="trace-state">{running ? '执行中' : failed ? '未完成' : '已完成'}</span><ChevronDown size={14} className={open ? 'rotate' : ''} />
    </button>
    {open && <div className="trace-details"><small>调用参数</small><pre>{JSON.stringify(tool.arguments, null, 2)}</pre>{tool.output != null && <><small>原始返回</small><pre>{typeof tool.output === 'string' ? tool.output : JSON.stringify(tool.output, null, 2)}</pre></>}</div>}
  </div>;
}

function MessageContent({ message, live, notify }: { message: Message; live?: boolean; notify: (notice: Notice) => void }) {
  const copy = async () => { try { await navigator.clipboard.writeText(message.content); notify({ text: '回答已复制' }); } catch { notify({ text: '浏览器无法访问剪贴板，请手动选择文本复制。', error: true }); } };
  if (message.role === 'user') return <div className="message user-message"><div className="user-bubble">{message.content}</div><div className="user-avatar">我</div></div>;
  return <div className="message assistant-message"><BrandMark /><div className="assistant-body">
    <div className="message-byline">医学诊疗Lab <span>科研助手</span></div>
    {message.tools.map(tool => <ToolTrace key={tool.id} tool={tool} />)}
    {message.content ? <div className={`markdown ${message.status === 'failed' ? 'error-content' : ''}`}><ReactMarkdown remarkPlugins={[remarkGfm, remarkCjkFriendly, remarkCjkFriendlyGfmStrikethrough]} components={{ a: ({ children, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer">{children}</a> }}>{message.content}</ReactMarkdown></div> : live && <div className="thinking"><span /><span /><span /><small>正在分析你的问题</small></div>}
    {!live && <div className="message-actions"><button onClick={copy} title="复制回答" aria-label="复制回答"><Copy size={14} /></button><span>{message.status === 'interrupted' ? '已停止' : message.status === 'failed' ? '请求未完成' : '回答完成'}</span></div>}
  </div></div>;
}

function ConnectionPanel({ connection, refresh, notify, models, model, onModelChange, streaming }: { connection?: Connection; refresh: () => Promise<void>; notify: (notice: Notice) => void; models: Model[]; model: string; onModelChange: (model: string) => void; streaming: boolean }) {
  const [busy, setBusy] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [login, setLogin] = useState<{ authUrl?: string; verificationUrl?: string; userCode?: string } | null>(null);
  const [localError, setLocalError] = useState('');
  useEffect(() => { if (!login) return; const timer = setInterval(refresh, 3500); return () => clearInterval(timer); }, [login, refresh]);
  useEffect(() => { if (connection?.connected) { setLogin(null); setApiKey(''); } }, [connection?.connected]);
  useEffect(() => { if (connection?.login_result && !connection.login_result.success) { setLocalError(connection.login_result.error || '授权未完成，请重新登录。'); setLogin(null); } }, [connection?.login_result]);

  const startLogin = async (mode: 'chatgpt' | 'chatgptDeviceCode' | 'apiKey') => {
    setBusy(mode); setLocalError('');
    // Open synchronously from the click so the browser does not block the OAuth tab.
    const tab = mode === 'chatgpt' ? window.open('about:blank', '_blank') : null;
    if (tab) { tab.opener = null; tab.document.title = '正在连接模型账号…'; tab.document.body.textContent = '正在准备官方登录页面，请稍候…'; }
    try {
      const result = await post<{ authUrl?: string; verificationUrl?: string; userCode?: string }>('/account/login', { mode, api_key: mode === 'apiKey' ? apiKey : undefined });
      if (mode === 'apiKey') { setApiKey(''); notify({ text: 'API Key 已配置，实际模型访问将在发送消息时验证。' }); }
      else { setLogin(result); if (tab && result.authUrl) tab.location.href = result.authUrl; }
      await refresh();
    } catch (error) { tab?.close(); setLocalError((error as Error).message); }
    finally { setBusy(''); }
  };
  const disconnect = async () => { setBusy('logout'); try { await post('/account/logout'); setLogin(null); await refresh(); notify({ text: '已退出工作台中的模型账号' }); } catch (error) { setLocalError((error as Error).message); } finally { setBusy(''); } };
  const cancel = async () => { try { await post('/account/cancel'); setLogin(null); await refresh(); } catch (error) { setLocalError((error as Error).message); } };

  return <div className="settings-page page-section">
    <div className="page-eyebrow"><PlugZap size={15} /> CONNECTIONS</div>
    <h1>连接你的研究伙伴</h1><p className="page-description">选择模型连接方式，让 AI 为你调用科学工具、整理证据。</p>
    {connection?.connected && <div className="connected-summary"><span className="success-icon"><Check size={20} /></span><div><strong>{connection.account?.type === 'apiKey' ? 'OpenAI API Key 已配置' : '模型账号已连接'}</strong><p>{connection.account?.email || '本地工作台连接'}{connection.account?.planType ? ` · ${connection.account.planType.toUpperCase()}` : ''}</p></div><button className="quiet-button" onClick={disconnect} disabled={!!busy}><LogOut size={15} />退出连接</button></div>}
    {models.length > 0 && <div className="settings-model"><label htmlFor="active-model">对话模型</label><select id="active-model" aria-label="选择模型" value={model} disabled={streaming} onChange={event => onModelChange(event.target.value)}>{models.map(item => <option value={item.model} key={item.id}>{item.displayName || item.model}</option>)}</select></div>}
    <div className="connection-grid">
      <section className="connection-card featured"><div className="connection-card-top"><span className="provider-icon"><UserRound size={28} /></span><span className="tag green-tag">账号登录</span></div><h2>模型账号</h2><p>通过 OpenAI 官方授权登录，使用账号支持的模型与额度。</p><ul><li><Check size={14} />支持官方浏览器授权</li><li><Check size={14} />模型列表自动读取</li><li><Check size={14} />无需在这里填写账号密码</li></ul><button className="primary-button" disabled={!!busy || !connection?.installed} onClick={() => startLogin('chatgpt')}>{busy === 'chatgpt' ? <Loader2 size={17} className="spin" /> : <LogIn size={17} />}使用账号登录<ArrowUp size={15} className="diagonal" /></button><button className="text-button" disabled={!!busy || !connection?.installed} onClick={() => startLogin('chatgptDeviceCode')}>浏览器授权不便？使用设备码</button></section>
      <section className="connection-card"><div className="connection-card-top"><span className="provider-icon api-icon"><KeyRound size={27} /></span><span className="tag">API 连接</span></div><h2>OpenAI API</h2><p>使用 OpenAI 平台的 API Key，按 API 账户的额度与计费规则调用。</p><label htmlFor="api-key">API Key</label><div className="secret-input"><input id="api-key" type={showKey ? 'text' : 'password'} autoComplete="off" placeholder="sk-…" value={apiKey} onChange={event => setApiKey(event.target.value)} /><button onClick={() => setShowKey(!showKey)}>{showKey ? '隐藏' : '显示'}</button></div><p className="field-hint">密钥由本机服务保存，不写入浏览器存储。</p><button className="secondary-button" disabled={!!busy || !apiKey.trim() || !connection?.installed} onClick={() => startLogin('apiKey')}>{busy === 'apiKey' ? <Loader2 size={16} className="spin" /> : <KeyRound size={16} />}保存并连接</button></section>
    </div>
    {login && <div className="login-pending"><Loader2 size={20} className="spin" /><div><strong>等待你完成官方授权</strong>{login.userCode && <p className="device-code">{login.userCode}</p>}<p>授权完成后，本页面会自动更新连接状态。</p><a href={login.authUrl || login.verificationUrl} target="_blank" rel="noopener noreferrer">打开官方授权页面 <ExternalLink size={13} /></a></div><button className="quiet-button" onClick={cancel}>取消</button></div>}
    {(localError || connection?.error) && <div className="error-banner" role="alert">{localError || connection?.error}</div>}
    <div className="connection-note"><ShieldCheck size={21} /><div><strong>你的工作台，保存在本机</strong><p>会话和授权数据保存在当前项目中。发送问题时，内容与相关工具结果会交给所选模型；查询时，参数会发送给对应数据库。</p></div></div>
    <div className="settings-links"><a href="https://learn.chatgpt.com/docs/app-server" target="_blank" rel="noopener noreferrer">官方账号接入说明 <ExternalLink size={13} /></a><a href="http://127.0.0.1:8088/docs" target="_blank" rel="noopener noreferrer">医学诊疗Lab 接口文档 <ExternalLink size={13} /></a></div>
  </div>;
}

function ToolDetail({ tool, close, notify }: { tool: ScientificTool; close: () => void; notify: (notice: Notice) => void }) {
  const [info, setInfo] = useState<ToolInfo>();
  const [loading, setLoading] = useState(true);
  const [argumentsText, setArgumentsText] = useState('{}');
  const [result, setResult] = useState<{ result: unknown; success: boolean; elapsed: number }>();
  const [error, setError] = useState('');
  const [running, setRunning] = useState(false);
  const panelRef = useRef<HTMLElement>(null);
  useEffect(() => {
    let current = true;
    api<ToolInfo>(`/tools/${encodeURIComponent(tool.name)}`).then(data => {
      if (!current) return; setInfo(data);
      const defaults: Record<string, unknown> = {};
      const schema = data.parameter;
      for (const [name, field] of Object.entries(schema?.properties || {})) {
        if (schema?.required?.includes(name) || field.required) defaults[name] = field.default ?? (field.enum?.[0] ?? (field.type === 'integer' || field.type === 'number' ? 1 : field.type === 'boolean' ? false : field.type === 'object' ? {} : field.type === 'array' ? [] : ''));
      }
      if (tool.name === 'UniProt_get_entry_by_accession') { defaults.accession = 'P05067'; defaults.include_publications = false; }
      setArgumentsText(JSON.stringify(defaults, null, 2));
    }).catch(exc => current && setError(exc.message)).finally(() => current && setLoading(false));
    return () => { current = false; };
  }, [tool.name]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    panelRef.current?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !running) close();
      if (event.key === 'Tab') {
        const items = panelRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input, textarea, a[href]');
        if (!items?.length) return;
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items[items.length - 1].focus(); }
        else if (!event.shiftKey && document.activeElement === items[items.length - 1]) { event.preventDefault(); items[0].focus(); }
      }
    };
    document.addEventListener('keydown', handler); return () => { document.removeEventListener('keydown', handler); previous?.focus(); };
  }, [close, running]);
  const execute = async () => {
    setError(''); setResult(undefined);
    let argumentsObject: unknown;
    try { argumentsObject = JSON.parse(argumentsText); if (typeof argumentsObject !== 'object' || argumentsObject === null || Array.isArray(argumentsObject)) throw new Error(); } catch { setError('请输入有效的 JSON 对象，例如 { "accession": "P05067" }。'); return; }
    setRunning(true);
    try { const data = await post<{ result: unknown; success: boolean; elapsed: number }>(`/tools/${encodeURIComponent(tool.name)}/run`, { arguments: argumentsObject }); setResult(data); } catch (exc) { setError((exc as Error).message); } finally { setRunning(false); }
  };
  return <div className="drawer-overlay" onClick={event => { if (event.target === event.currentTarget && !running) close(); }}><section className="tool-drawer" ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="tool-detail-title">
    <div className="drawer-heading"><span><Database size={18} />工具详情</span><button className="icon-button" aria-label="关闭工具详情" onClick={close} disabled={running}><X size={19} /></button></div>
    <div className="drawer-body"><span className="tag green-tag">{tool.category || '科学工具'}</span><h2 id="tool-detail-title">{tool.name}</h2><p className="tool-description-full">{tool.description}</p>
    {loading ? <div className="loading-state"><Loader2 className="spin" />加载工具参数…</div> : <>
      <div className="section-label">参数说明 <span>{Object.keys(info?.parameter?.properties || {}).length} 项</span></div><div className="parameter-list">{Object.entries(info?.parameter?.properties || {}).map(([name, field]) => <div key={name}><div><code>{name}</code><span>{Array.isArray(field.type) ? field.type.join(' / ') : field.type}</span>{(field.required || info?.parameter?.required?.includes(name)) && <b>必填</b>}</div><p>{field.description}</p></div>)}</div>
      <label className="section-label" htmlFor="tool-arguments">运行参数 <span>JSON</span></label><textarea id="tool-arguments" className="code-editor" spellCheck={false} value={argumentsText} onChange={event => setArgumentsText(event.target.value)} />
      <button className="primary-button execute-button" onClick={execute} disabled={running || !info?.mcp_annotations?.readOnlyHint || !!info?.mcp_annotations?.destructiveHint}>{running ? <Loader2 className="spin" size={16} /> : <ArrowRight size={16} />}{running ? '正在查询数据库…' : '运行工具'}</button>
      {info && !info.mcp_annotations?.readOnlyHint && <p className="field-hint">此工具不属于只读查询，请通过 医学诊疗Lab 原生客户端使用。</p>}
    </>}
    {error && <div className="error-banner" role="alert">{error}</div>}
    {result && <div className="tool-result"><div className="section-label"><span className={result.success ? 'text-success' : 'text-error'}>{result.success ? '查询完成' : '查询未成功'} · {result.elapsed} 秒</span><button className="text-button" onClick={() => { download(`${tool.name}.json`, JSON.stringify(result.result, null, 2), 'application/json'); notify({ text: '查询结果已导出' }); }}><Download size={14} />导出</button></div><pre>{JSON.stringify(result.result, null, 2)}</pre></div>}
    </div></section></div>;
}

function ToolLibrary({ notify }: { notify: (notice: Notice) => void }) {
  const [query, setQuery] = useState(''); const [category, setCategory] = useState('');
  const [categories, setCategories] = useState<{ name: string; count: number }[]>([]);
  const [tools, setTools] = useState<ScientificTool[]>([]); const [total, setTotal] = useState(0); const [catalogTotal, setCatalogTotal] = useState(0);
  const [offset, setOffset] = useState(0); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const [selected, setSelected] = useState<ScientificTool>(); const [retry, setRetry] = useState(0);
  const closeDetail = useCallback(() => setSelected(undefined), []);
  useEffect(() => { api<{ total: number; categories: { name: string; count: number }[] }>('/categories').then(data => { setCategories(data.categories); setCatalogTotal(data.total); }).catch(() => {}); }, [retry]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true); setError('');
      api<{ tools: ScientificTool[]; total: number }>(`/tools?q=${encodeURIComponent(query)}&category=${encodeURIComponent(category)}&limit=24&offset=${offset}`, { signal: controller.signal })
        .then(data => { setTools(data.tools); setTotal(data.total); })
        .catch(exc => { if (exc.name !== 'AbortError') setError(exc.message); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 220);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, category, offset, retry]);
  return <div className="tools-page page-section"><div className="page-eyebrow"><Layers3 size={15} /> SCIENTIFIC TOOLKIT</div><div className="tools-page-title"><div><h1>探索MCP工具</h1><p className="page-description">从文献到分子，让每一个问题都有工具可循。</p></div><span className="catalog-count"><strong>{catalogTotal ? formatCount(catalogTotal) : '—'}</strong> 个已加载工具</span></div>
    <div className="tools-search-bar"><div className="search-field"><Search size={18} /><input aria-label="搜索科研工具" placeholder="搜索工具、数据库或研究方向，例如：蛋白、PubMed…" value={query} onChange={event => { setQuery(event.target.value); setOffset(0); }} />{query && <button className="icon-button" aria-label="清空工具搜索" onClick={() => { setQuery(''); setOffset(0); }}><X size={15} /></button>}</div><select aria-label="筛选工具分类" value={category} onChange={event => { setCategory(event.target.value); setOffset(0); }}><option value="">全部分类</option>{categories.map(item => <option key={item.name} value={item.name}>{item.name} ({item.count})</option>)}</select></div>
    <div className="filter-chips">{[['', '全部工具'], ['文献', '文献检索'], ['蛋白', '蛋白与基因'], ['药物', '药物发现'], ['临床', '临床研究'], ['通路', '生物通路']].map(([value, label]) => <button key={label} className={query === value ? 'active' : ''} onClick={() => { setQuery(value); setCategory(''); setOffset(0); }}>{label}</button>)}</div>
    <div className="results-label">{loading ? '正在检索工具目录…' : `找到 ${formatCount(total)} 个工具`}<span>工具说明来自原始数据库 · 点击查看参数并试用</span></div>
    {error ? <div className="empty-state"><Database size={30} /><h3>暂时无法连接工具库</h3><p>{error}</p><button className="secondary-button" onClick={() => setRetry(retry + 1)}><RefreshCw size={15} />重试</button></div> : loading ? <div className="tool-grid">{Array.from({ length: 6 }, (_, index) => <div className="tool-card skeleton" key={index}><i /><i /><i /></div>)}</div> : tools.length ? <><div className="tool-grid">{tools.map(tool => <button className="tool-card" key={tool.name} onClick={() => setSelected(tool)}><div className="tool-card-top"><span className="small-tool-icon"><Database size={17} /></span><span>{tool.category || '科学工具'}</span><ArrowUp size={15} className="diagonal" /></div><h3>{tool.name.replace(/_/g, ' ')}</h3><p>{tool.description}</p><div className="tool-card-footer"><code>{tool.name}</code><ChevronRight size={14} /></div></button>)}</div><div className="pagination"><span>{offset + 1}–{Math.min(offset + 24, total)} / {formatCount(total)}</span><button className="quiet-button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 24))}>上一页</button><button className="quiet-button" disabled={offset + 24 >= total} onClick={() => setOffset(offset + 24)}>下一页</button></div></> : <div className="empty-state"><Search size={30} /><h3>没有找到匹配的工具</h3><p>试试数据库名称或更短的英文关键词。</p></div>}
    {selected && <ToolDetail tool={selected} close={closeDetail} notify={notify} />}
  </div>;
}

export default function App() {
  const [view, setView] = useState<View>('chat');
  const [status, setStatus] = useState<Status>();
  const [statusLoading, setStatusLoading] = useState(true);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSession, setCurrentSession] = useState<Session>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState(localStorage.getItem('tu.model') || '');
  const [streaming, setStreaming] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [notice, setNotice] = useState<Notice>();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [historyQuery, setHistoryQuery] = useState('');
  const [historySearch, setHistorySearch] = useState(false);
  const [menuSession, setMenuSession] = useState<string>();
  const [sessionAction, setSessionAction] = useState<{ kind: 'rename' | 'delete'; session: Session }>();
  const [renameText, setRenameText] = useState('');
  const [dark, setDark] = useState(localStorage.getItem('tu.theme') === 'dark');
  const textArea = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | undefined>(undefined);
  const activeSessionId = useRef<string | undefined>(undefined);
  const selectionVersion = useRef(0);
  const notify = useCallback((value: Notice) => setNotice(value), []);
  const refreshSessions = useCallback(async () => { const data = await api<{ sessions: Session[] }>('/sessions'); setSessions(data.sessions); }, []);
  const refreshStatus = useCallback(async () => {
    try { setStatus(await api<Status>('/status')); }
    catch (error) { setStatus({ tools: { connected: false, loaded_tools_count: 0 }, connection: { installed: false, connected: false, error: (error as Error).message }, version: '0.1.0' }); }
    finally { setStatusLoading(false); }
  }, []);
  useEffect(() => { refreshStatus(); refreshSessions().catch(error => notify({ text: error.message, error: true })); }, [refreshStatus, refreshSessions, notify]);
  useEffect(() => { const timer = setInterval(refreshStatus, 30000); return () => clearInterval(timer); }, [refreshStatus]);
  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(undefined), notice.error ? 7000 : 3200); return () => clearTimeout(timer); }, [notice]);
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; localStorage.setItem('tu.theme', dark ? 'dark' : 'light'); }, [dark]);
  useEffect(() => {
    if (!status?.connection.connected) { setModels([]); return; }
    api<{ models: Model[] }>('/models').then(data => {
      setModels(data.models);
      if (!data.models.some(item => item.model === model)) setModel(data.models.find(item => item.isDefault)?.model || data.models[0]?.model || '');
    }).catch(error => notify({ text: error.message, error: true }));
  // Fetch a fresh catalog when the identity changes, not for every polling response.
  }, [status?.connection.connected, status?.connection.account?.email, status?.connection.account?.type]);
  useEffect(() => { localStorage.setItem('tu.model', model); }, [model]);
  useEffect(() => { if (textArea.current) { textArea.current.style.height = 'auto'; textArea.current.style.height = `${Math.min(textArea.current.scrollHeight, 170)}px`; } }, [draft]);
  useEffect(() => { if (streaming) endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages, streaming]);

  const newChat = useCallback(() => {
    if (streaming) { notify({ text: '请先停止当前回答，再开始新对话。' }); return; }
    selectionVersion.current += 1; setCurrentSession(undefined); setMessages([]); setDraft(''); setView('chat'); setSidebarOpen(false); setMenuSession(undefined); setTimeout(() => textArea.current?.focus(), 50);
  }, [streaming, notify]);
  useEffect(() => { const handler = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key === 'k') { event.preventDefault(); newChat(); } if (event.key === 'Escape') { setSidebarOpen(false); setMenuSession(undefined); setSessionAction(undefined); } }; window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler); }, [newChat]);

  const selectSession = async (item: Session) => {
    if (streaming) { notify({ text: '请先停止当前回答，再切换会话。' }); return; }
    const version = ++selectionVersion.current;
    setView('chat'); setSidebarOpen(false); setSessionLoading(true); setMenuSession(undefined);
    try { const data = await api<Session>(`/sessions/${item.id}`); if (version !== selectionVersion.current) return; setCurrentSession(data); setMessages(data.messages || []); setDraft(''); }
    catch (error) { notify({ text: (error as Error).message, error: true }); }
    finally { if (version === selectionVersion.current) setSessionLoading(false); }
  };
  const send = async () => {
    const text = draft.trim(); if (!text || streaming) return;
    if (!status?.connection.connected) { setView('settings'); notify({ text: '先连接模型账号或 API Key，研究问题已为你保留。' }); return; }
    if (!status.tools.connected) { notify({ text: '医学诊疗Lab 服务未连接，请先启动后台服务。', error: true }); return; }
    setStreaming(true); setDraft(''); setView('chat');
    let session = currentSession;
    const temporaryUser = `user-${Date.now()}`; const assistantId = `assistant-${Date.now()}`;
    try {
      if (!session) { session = await post<Session>('/sessions', { title: text.slice(0, 32) }); setCurrentSession(session); }
      activeSessionId.current = session.id;
      setMessages(previous => [...previous, { id: temporaryUser, role: 'user', content: text, tools: [], status: 'completed', created_at: Date.now() / 1000 }, { id: assistantId, role: 'assistant', content: '', tools: [], status: 'inProgress', created_at: Date.now() / 1000 }]);
      abort.current = new AbortController();
      await streamChat({ session_id: session.id, text, model: model || undefined }, abort.current.signal, event => {
        if (event.type === 'user') setMessages(previous => previous.map(message => message.id === temporaryUser ? event.message as Message : message));
        if (event.type === 'text') setMessages(previous => previous.map(message => message.id === assistantId ? { ...message, content: event.content } : message));
        if (event.type === 'tool') setMessages(previous => previous.map(message => message.id === assistantId ? { ...message, tools: [...message.tools.filter(tool => tool.id !== event.tool.id), event.tool] } : message));
        if (event.type === 'error') { notify({ text: event.message, error: true }); setMessages(previous => previous.map(message => message.id === assistantId ? { ...message, content: message.content || event.message, status: 'failed' } : message)); }
        if (event.type === 'done') setMessages(previous => previous.map(message => message.id === assistantId ? event.message as Message : message));
      });
    } catch (error) {
      if ((error as Error).name !== 'AbortError') { notify({ text: (error as Error).message, error: true }); setMessages(previous => previous.map(message => message.id === assistantId ? { ...message, content: message.content || (error as Error).message, status: 'failed' } : message)); }
    } finally {
      setStreaming(false); abort.current = undefined; activeSessionId.current = undefined;
      await refreshSessions().catch(() => {});
      if (session) { const fresh = await api<Session>(`/sessions/${session.id}`).catch(() => undefined); if (fresh) { setCurrentSession(fresh); if (!fresh.active) setMessages(fresh.messages || []); } }
    }
  };
  const stop = async () => {
    if (!activeSessionId.current) return;
    try { await post(`/sessions/${activeSessionId.current}/interrupt`); }
    catch { abort.current?.abort(); }
  };
  const exportSession = () => {
    if (!messages.length) return;
    download(`医学诊疗Lab-${currentSession?.title || '研究记录'}.md`, `# ${currentSession?.title || '研究记录'}\n\n` + messages.map(message => `## ${message.role === 'user' ? '我的问题' : '医学诊疗Lab'}\n\n${message.content}\n${message.tools.length ? '\n### 工具调用\n\n' + message.tools.map(tool => `- ${tool.name}: ${JSON.stringify(tool.arguments)}`).join('\n') : ''}`).join('\n\n---\n\n'));
    notify({ text: '研究记录已导出为 Markdown' });
  };
  const performSessionAction = async () => {
    if (!sessionAction) return;
    try {
      if (sessionAction.kind === 'delete') { await api(`/sessions/${sessionAction.session.id}`, { method: 'DELETE' }); if (currentSession?.id === sessionAction.session.id) newChat(); }
      else { await api(`/sessions/${sessionAction.session.id}`, { method: 'PATCH', body: JSON.stringify({ title: renameText.trim() || '新对话' }) }); if (currentSession?.id === sessionAction.session.id) setCurrentSession({ ...currentSession, title: renameText.trim() || '新对话' }); }
      setSessionAction(undefined); await refreshSessions();
    } catch (error) { notify({ text: (error as Error).message, error: true }); }
  };
  const count = status?.tools.loaded_tools_count || 0;
  const filteredSessions = sessions.filter(session => session.title.toLowerCase().includes(historyQuery.toLowerCase()));
  const empty = !messages.length && !sessionLoading;

  return <div className="app-shell">
    {sidebarOpen && <button className="mobile-backdrop" aria-label="关闭导航" onClick={() => setSidebarOpen(false)} />}
    <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
      <button className="brand" onClick={newChat}><BrandMark /><div><strong>医学诊疗Lab</strong><span>医学研究与诊疗工作台</span></div></button>
      <button className="new-chat-button" onClick={newChat}><Plus size={18} /><span>开启新对话</span><kbd>⌃ K</kbd></button>
      <nav aria-label="主导航"><button className={view === 'chat' ? 'active' : ''} onClick={() => { setView('chat'); setSidebarOpen(false); }}><MessageSquare size={18} />科研对话<span className="nav-dot" /></button><button className={view === 'graph' ? 'active' : ''} onClick={() => { setView('graph'); setSidebarOpen(false); }}><Network size={18} />医学知识图谱</button><button className={view === 'benchmark' ? 'active' : ''} onClick={() => { setView('benchmark'); setSidebarOpen(false); }}><BarChart3 size={18} />检索评测 Benchmark</button><button className={view === 'evidence' ? 'active' : ''} onClick={() => { setView('evidence'); setSidebarOpen(false); }}><Network size={18} />科研证据链</button><button className={view === 'intelligence' ? 'active' : ''} onClick={() => { setView('intelligence'); setSidebarOpen(false); }}><FlaskConical size={18} />证据智能分析</button><button className={view === 'settings' ? 'active' : ''} onClick={() => { setView('settings'); setSidebarOpen(false); }}><PlugZap size={18} />连接设置{!status?.connection.connected && <span className="nav-connection-dot" />}</button></nav>
      <div className="history-heading"><span>最近的研究</span><button aria-label="搜索历史会话" onClick={() => setHistorySearch(!historySearch)}><Search size={14} /></button></div>
      {historySearch && <input className="history-search" aria-label="搜索会话" placeholder="搜索会话…" value={historyQuery} onChange={event => setHistoryQuery(event.target.value)} autoFocus />}
      <div className="session-list">{filteredSessions.length ? filteredSessions.map(session => <div key={session.id} className={`session-item ${view === 'chat' && currentSession?.id === session.id ? 'selected' : ''}`}><button className="session-select" onClick={() => selectSession(session)}><MessageSquare size={14} /><span>{session.title}</span></button><button className="session-menu-button" aria-label={`管理会话 ${session.title}`} onClick={() => setMenuSession(menuSession === session.id ? undefined : session.id)}><MoreHorizontal size={16} /></button>{menuSession === session.id && <div className="session-menu"><button onClick={() => { setRenameText(session.title); setSessionAction({ kind: 'rename', session }); setMenuSession(undefined); }}><Pencil size={13} />重命名</button><button onClick={() => { setSessionAction({ kind: 'delete', session }); setMenuSession(undefined); }}><Trash2 size={13} />删除会话</button></div>}</div>) : <div className="history-empty"><span className="history-line" /><p>{historyQuery ? '没有匹配的会话' : '每一次发现，都从提问开始'}</p><span>{historyQuery ? '换个关键词试试' : '你的研究记录会保存在这里'}</span></div>}</div>
      <div className="sidebar-bottom"><div className="local-card" title={statusLoading ? '正在连接工作台' : status?.tools.connected ? '工具服务已连接' : '工具服务未连接'}><span className={`status-dot ${status?.tools.connected ? '' : 'offline'}`} /><div><strong>知识库</strong></div><Database size={15} /></div><button className="profile-button" onClick={() => setView('settings')}><span className={`profile-avatar ${status?.connection.connected ? 'connected' : ''}`}><UserRound size={19} /></span><div><strong>账号</strong></div><Settings2 size={16} /></button></div>
    </aside>
    <main className="main-shell">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setSidebarOpen(true)}><Menu size={19} /></button><span>{view === 'chat' ? '科研对话' : view === 'tools' ? 'MCP工具' : view === 'graph' ? '医学知识图谱' : view === 'benchmark' ? '检索评测 Benchmark' : view === 'evidence' ? '科研证据链' : view === 'intelligence' ? '证据智能分析' : '连接设置'}</span>{view === 'chat' && <><ChevronRight size={13} /><strong>{currentSession?.title || '新对话'}</strong></>}</div><div className="topbar-actions"><span className="local-badge"><span className="status-dot" />本地工作台</span><button className="icon-button" aria-label={dark ? '切换浅色主题' : '切换深色主题'} onClick={() => setDark(!dark)}>{dark ? <Sun size={17} /> : <Moon size={17} />}</button>{view === 'chat' && <button className="icon-button" aria-label="导出研究记录" title="导出研究记录" disabled={!messages.length || streaming} onClick={exportSession}><Download size={17} /></button>}</div></header>
      {view === 'intelligence' ? <div className="content-scroll"><Suspense fallback={<div className="loading-state">正在加载证据分析…</div>}><EvidenceIntelligence model={model} models={models} /></Suspense></div> : view === 'evidence' ? <div className="content-scroll"><Suspense fallback={<div className="loading-state">正在加载证据链…</div>}><Research /></Suspense></div> : view === 'benchmark' ? <div className="content-scroll"><Suspense fallback={<div className="loading-state"><Loader2 className="spin" />正在加载检索评测…</div>}><Benchmark /></Suspense></div> : view === 'graph' ? <div className="content-scroll"><Suspense fallback={<div className="loading-state"><Loader2 className="spin" />正在加载知识图谱…</div>}><KnowledgeGraph dark={dark} /></Suspense></div> : view === 'settings' ? <div className="content-scroll"><ConnectionPanel connection={status?.connection} refresh={refreshStatus} notify={notify} models={models} model={model} onModelChange={setModel} streaming={streaming} /></div> : view === 'tools' ? <div className="content-scroll"><ToolLibrary notify={notify} /></div> : <div className="chat-layout">
        <section className="chat-column">
          <div className={`chat-scroll ${empty ? 'welcome-scroll' : ''}`}>
            {sessionLoading ? <div className="loading-state"><Loader2 className="spin" size={24} />正在加载研究记录…</div> : empty ? <div className="welcome">
              <div className="welcome-topline"><span className="welcome-orbit"><HeartPulse size={36} strokeWidth={1.15} /></span><span>SCIENCE, WITH A LITTLE MORE POSSIBILITY</span></div>
              <h1>让好问题，<br />找到<span>科学的答案。</span><span className="heading-spark">✳</span></h1>
              <p className="welcome-description">连接科学数据库与 AI，从一个灵感出发，<br className="desktop-break" />一起寻找文献、解析数据，走向下一次发现。</p>
              <div className="welcome-pills"><span><span className="status-dot" />{count ? `${formatCount(count)} 个科研工具` : '连接科研工具'}</span><span><Globe2 size={13} />跨数据库检索</span><span><ShieldCheck size={13} />来源可追溯</span></div>
              <div className="starter-heading"><span>从这里开始探索</span><span>试试这些研究方向<ArrowDown size={12} /></span></div>
              <div className="starter-grid">{starters.map(starter => <button key={starter.title} className="starter-card" onClick={() => { setDraft(starter.prompt); setTimeout(() => textArea.current?.focus(), 30); }}><span className={`starter-icon ${starter.className}`}><starter.icon size={21} strokeWidth={1.7} /></span><ArrowUp size={15} className="starter-arrow diagonal" /><strong>{starter.title}</strong><p>{starter.tag}</p></button>)}</div>
            </div> : <div className="messages">{messages.map(message => <MessageContent key={message.id} message={message} live={streaming && message.status === 'inProgress'} notify={notify} />)}<div ref={endRef} /></div>}
          </div>
          <div className="composer-area">
            {!status?.connection.connected && !statusLoading && <div className="connect-nudge"><span><Sparkles size={15} />连接模型，让科研工具开始协作</span><button onClick={() => setView('settings')}>去连接 <ArrowRight size={14} /></button></div>}
            <div className={`composer ${streaming ? 'working' : ''}`}><textarea ref={textArea} aria-label="输入研究问题" placeholder="今天，有什么科学问题值得一起探索？" rows={2} maxLength={20000} value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} disabled={sessionLoading} /><div className="composer-toolbar"><div className="composer-tools"><span className="tools-enabled"><Layers3 size={14} />MCP工具</span></div><div className="send-group"><span className="enter-hint">{streaming ? '正在研究中' : 'Enter 发送'}</span>{streaming ? <button className="send-button stop-button" aria-label="停止回答" onClick={stop}><Square size={16} fill="currentColor" /></button> : <button className="send-button" aria-label="发送研究问题" onClick={send} disabled={!draft.trim() || sessionLoading}><ArrowUp size={19} /></button>}</div></div></div>
            <p className="composer-footnote">让结论有据可循。AI 回答可能存在错误，请核对原始来源。<span>Shift + Enter 换行</span></p>
          </div>
        </section>
        <aside className="research-rail"><div className="rail-label"><span className="status-dot" />你的研究空间</div><div className="rail-illustration"><div className="orbital-ring one" /><div className="orbital-ring two" /><div className="orbital-ring three" /><span className="orbital-center"><HeartPulse size={28} strokeWidth={1.3} /></span><span className="orbit-point p1" /><span className="orbit-point p2" /><span className="orbit-point p3" /></div><h3>更多连接，<br />更近一步发现。</h3><p className="rail-description">让分散的科学知识，<br />成为你手边的研究线索。</p><div className="rail-divider" /><div className="rail-section-label">探索数据源 <button aria-label="打开MCP工具" onClick={() => setView('tools')}><ArrowUp size={13} className="diagonal" /></button></div>{[{ icon: BookOpen, name: 'PubMed', desc: '生物医学文献' }, { icon: Dna, name: 'UniProt', desc: '蛋白质知识库' }, { icon: FlaskConical, name: 'ChEMBL', desc: '生物活性与药物' }, { icon: Activity, name: 'ClinicalTrials.gov', desc: '临床试验注册' }].map(source => <button key={source.name} className="source-row" onClick={() => { setView('tools'); }}><span><source.icon size={17} /></span><div><strong>{source.name}</strong><small>{source.desc}</small></div><ChevronRight size={13} /></button>)}<button className="explore-all" onClick={() => setView('tools')}>探索全部工具 <ArrowRight size={14} /></button><div className="rail-tip"><Microscope size={17} /><p>一个清晰的问题，<br />是一次好研究的开始。</p></div></aside>
      </div>}
    </main>
    {notice && <div className={`toast ${notice.error ? 'error' : ''}`} role="status">{notice.error ? <CircleHelp size={17} /> : <CheckCircle2 size={17} />}<span>{notice.text}</span><button aria-label="关闭提示" onClick={() => setNotice(undefined)}><X size={14} /></button></div>}
    {sessionAction && <div className="modal-backdrop" onClick={event => { if (event.target === event.currentTarget) setSessionAction(undefined); }}><section className="small-modal" role="dialog" aria-modal="true" aria-labelledby="session-action-title"><h2 id="session-action-title">{sessionAction.kind === 'rename' ? '重命名研究记录' : '删除这条研究记录？'}</h2>{sessionAction.kind === 'rename' ? <input aria-label="会话名称" value={renameText} maxLength={100} autoFocus onChange={event => setRenameText(event.target.value)} onKeyDown={event => event.key === 'Enter' && performSessionAction()} /> : <p>将从工作台移除「{sessionAction.session.title}」及其消息。此操作无法撤销。</p>}<div className="modal-actions"><button className="quiet-button" onClick={() => setSessionAction(undefined)}>取消</button><button className={sessionAction.kind === 'delete' ? 'danger-button' : 'primary-button'} onClick={performSessionAction}>{sessionAction.kind === 'delete' ? '删除记录' : '保存名称'}</button></div></section></div>}
  </div>;
}
