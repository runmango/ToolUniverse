import type { MedicalEntity, MedicalRelation } from './medical';
import { useCallback, useEffect, useRef, useState } from 'react';
import cytoscape, { type Core, type StylesheetJson } from 'cytoscape';
import { Search, RefreshCw, ZoomIn, ZoomOut, Maximize, LocateFixed, Download, ArrowRight, Network, ExternalLink, Loader2, X } from 'lucide-react';
import { api, post, download } from './api';
import './knowledge-graph.css';

type Source = { id: string; provider: string; source_id: string; source_url: string; source_updated_at?: string; fetched_at: string; payload?: unknown };
type Entity = MedicalEntity;
type Edge = MedicalRelation;
type Graph = { nodes: Entity[]; edges: Edge[]; center?: string; truncated?: boolean; found?: boolean; note?: string };
type Run = { id: string; query: string; status: string; report: { errors?: { source: string; message: string }[]; imported?: Record<string, number>; current_source?: string; note?: string } };
type Stats = { counts: Record<string, number>; edge_count: number; source_count: number; inferred_count: number; runs: Run[] };
const names: Record<string, string> = { Disease: '疾病', Drug: '药物', Gene: '基因', Protein: '蛋白质', ClinicalTrial: '试验', Publication: '论文', Intervention: '干预', Outcome: '结局', Biomarker: '标志物', ClinicalEvent: '临床事件' };
const colors: Record<string, string> = { Disease: '#246a55', Drug: '#b07c3d', Gene: '#8265b0', Protein: '#a36e91', ClinicalTrial: '#4e83a1', Publication: '#84994b', Intervention: '#378b86', Outcome: '#b38372', Biomarker: '#9e72a4', ClinicalEvent: '#bc6961' };
const emptyGraph: Graph = { nodes: [], edges: [] };
const statusName = (value: string) => ({ running: '正在同步', completed: '同步完成', partial: '部分来源完成', failed: '同步失败', empty: '未找到数据', interrupted: '同步已中断' }[value] || value);
const safeUrl = (url: string) => /^https?:\/\//i.test(url) ? url : undefined;

export default function KnowledgeGraph({ dark }: { dark: boolean }) {
  const [query, setQuery] = useState('MDS');
  const [matches, setMatches] = useState<Entity[]>([]);
  const [center, setCenter] = useState<Entity>();
  const [depth, setDepth] = useState(1);
  const [filters, setFilters] = useState<string[]>([]);
  const [inferred, setInferred] = useState(true);
  const [graph, setGraph] = useState<Graph>(emptyGraph);
  const [selected, setSelected] = useState<Entity>();
  const [edge, setEdge] = useState<Edge>();
  const [stats, setStats] = useState<Stats>();
  const [run, setRun] = useState<Run>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [pathQuery, setPathQuery] = useState('');
  const [pathTargets, setPathTargets] = useState<Entity[]>([]);
  const [pathTarget, setPathTarget] = useState('');
  const [pathMessage, setPathMessage] = useState('');
  const [pathInferred, setPathInferred] = useState(false);
  const [exporting, setExporting] = useState(false);
  const graphElement = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | undefined>(undefined);
  const selectedRequest = useRef(0);
  const viewVersion = useRef(0);
  const syncing = run?.status === 'running';
  const reportError = useCallback((exc: unknown) => setError((exc as Error).message || String(exc)), []);
  const refreshStats = useCallback(async () => { const next = await api<Stats>('/graph/stats'); setStats(next); return next; }, []);

  useEffect(() => { refreshStats().then(next => { if (next.runs[0]) setRun(next.runs[0]); }).catch(reportError); }, [refreshStats, reportError]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<{ entities: Entity[] }>(`/graph/entities?q=${encodeURIComponent(query)}&limit=25`, { signal: controller.signal })
        .then(data => { setMatches(data.entities); if (!center && data.entities.length) setCenter(data.entities[0]); })
        .catch(exc => { if (exc.name !== 'AbortError') reportError(exc); });
    }, 200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, revision, center, reportError]);
  useEffect(() => {
    if (!pathQuery.trim()) { setPathTargets([]); return; }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<{ entities: Entity[] }>(`/graph/entities?q=${encodeURIComponent(pathQuery)}&limit=15`, { signal: controller.signal })
        .then(data => { setPathTargets(data.entities); setPathTarget(data.entities[0]?.id || ''); })
        .catch(exc => { if (exc.name !== 'AbortError') reportError(exc); });
    }, 200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [pathQuery, revision, reportError]);
  useEffect(() => {
    if (!run || run.status !== 'running') return;
    let disposed = false;
    const timer = setInterval(async () => {
      try {
        const next = await api<Run>(`/graph/sync/${run.id}`);
        if (disposed) return;
        setRun(next);
        if (next.status !== 'running') { setRevision(value => value + 1); await refreshStats(); }
      } catch (exc) { if (!disposed) reportError(exc); }
    }, 2000);
    return () => { disposed = true; clearInterval(timer); };
  }, [run?.id, run?.status, refreshStats, reportError]);
  const graphParams = useCallback((id: string) => `center=${encodeURIComponent(id)}&depth=${depth}&entity_types=${filters.join(',')}&include_inferred=${inferred}`, [depth, filters, inferred]);
  useEffect(() => {
    if (!center) return;
    viewVersion.current++;
    selectedRequest.current++;
    const controller = new AbortController();
    setLoading(true); setPathMessage(''); setEdge(undefined); setSelected(center);
    api<Graph>(`/graph/neighborhood?${graphParams(center.id)}`, { signal: controller.signal })
      .then(next => { setGraph(next); setSelected(next.nodes.find(node => node.id === center.id)); }).catch(exc => { if (exc.name !== 'AbortError') reportError(exc); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [center, graphParams, revision, reportError]);

  const showEntity = useCallback((node: Entity) => { selectedRequest.current++; setSelected(node); setEdge(undefined); }, []);
  const showEdge = useCallback(async (item: Edge) => {
    const version = ++selectedRequest.current;
    setSelected(undefined); setEdge(item);
    try { const detail = await api<Edge>(`/graph/edges/${item.id}`); if (selectedRequest.current === version) setEdge(detail); }
    catch (exc) { if (selectedRequest.current === version) reportError(exc); }
  }, [reportError]);
  useEffect(() => {
    if (!graphElement.current) return;
    const style: StylesheetJson = [
      { selector: 'node', style: { label: 'data(label)', 'background-color': 'data(color)', color: dark ? '#e4ebdf' : '#263c32', 'font-size': 11, 'text-wrap': 'ellipsis', 'text-max-width': '120px', 'text-valign': 'bottom', 'text-margin-y': 9, width: 34, height: 34, 'border-width': 3, 'border-color': dark ? '#28372e' : '#ffffff' } },
      { selector: 'node[isCenter = 1]', style: { width: 55, height: 55, 'font-weight': 'bold', 'border-width': 5, 'border-color': '#c1d5a8' } },
      { selector: 'node[entityType = "Publication"]', style: { shape: 'round-rectangle' } },
      { selector: 'node[entityType = "ClinicalTrial"]', style: { shape: 'diamond', width: 42, height: 42 } },
      { selector: 'edge', style: { width: 1.5, 'line-color': dark ? '#65746a' : '#b5c4b5', 'target-arrow-color': dark ? '#65746a' : '#b5c4b5', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': .8 } },
      { selector: 'edge[uncertain = 1]', style: { 'line-style': 'dashed', 'line-color': '#c49b64', 'target-arrow-color': '#c49b64' } },
      { selector: 'node:selected', style: { 'border-color': '#d7a347', 'border-width': 5 } },
      { selector: 'edge:selected', style: { 'line-color': '#d7a347', 'target-arrow-color': '#d7a347', width: 3 } },
    ];
    const instance = cytoscape({ container: graphElement.current, style, minZoom: .12, maxZoom: 3, wheelSensitivity: .25,
      elements: [...graph.nodes.map(node => ({ data: { id: node.id, label: node.standard_name.length > 22 ? node.standard_name.slice(0, 22) + '…' : node.standard_name, color: colors[node.entity_type], entityType: node.entity_type, isCenter: node.id === center?.id ? 1 : 0 } })),
        ...graph.edges.map(item => ({ data: { id: item.id, source: item.source_entity, target: item.target_entity, uncertain: item.verification_status === 'source_asserted' ? 0 : 1 } }))],
      layout: { name: 'cose', animate: false, randomize: false, nodeRepulsion: () => 8500, idealEdgeLength: () => 100, padding: 45 },
    });
    cy.current = instance;
    instance.on('tap', 'node', event => { const node = graph.nodes.find(n => n.id === event.target.id()); if (node) showEntity(node); });
    instance.on('tap', 'edge', event => { const item = graph.edges.find(e => e.id === event.target.id()); if (item) showEdge(item); });
    const resize = new ResizeObserver(() => instance.resize()); resize.observe(graphElement.current);
    return () => { resize.disconnect(); instance.destroy(); cy.current = undefined; };
  }, [graph, dark, center?.id, showEntity, showEdge]);

  const sync = async () => { setError(''); try { setRun(await post<Run>('/graph/sync', { query: query.trim(), limit: 5, molecular: true })); } catch (exc) { reportError(exc); } };
  const expand = async () => {
    if (!selected) return;
    const version = viewVersion.current;
    setLoading(true);
    try {
      const next = await api<Graph>(`/graph/neighborhood?${graphParams(selected.id)}`);
      if (version !== viewVersion.current) return;
      setGraph(previous => {
        const nodes = [...new Map([...previous.nodes, ...next.nodes].map(n => [n.id, n])).values()];
        if (nodes.length > 400) { setError('已达到400个节点，请选择新的中心实体。'); return previous; }
        return { ...previous, nodes, edges: [...new Map([...previous.edges, ...next.edges].map(e => [e.id, e])).values()], truncated: previous.truncated || next.truncated };
      });
    } catch (exc) { reportError(exc); } finally { setLoading(false); }
  };
  const findPath = async () => {
    if (!center || !pathTarget) return;
    const version = ++viewVersion.current;
    selectedRequest.current++;
    setLoading(true); setError('');
    try {
      const result = await api<Graph>(`/graph/paths?source=${center.id}&target=${pathTarget}&max_hops=6&include_inferred=${pathInferred}`);
      if (version !== viewVersion.current) return;
      setPathMessage(result.found ? `找到 ${result.edges.length} 跳连接路径。连接不代表因果或治疗建议。` : '当前已导入数据中，6跳范围内未找到符合条件的连接路径。');
      if (result.found) { setGraph(result); setEdge(undefined); setSelected(undefined); }
    } catch (exc) { if (version === viewVersion.current) reportError(exc); } finally { if (version === viewVersion.current) setLoading(false); }
  };
  const exportGraph = async (format: 'json' | 'csv') => {
    setExporting(true);
    try {
      // Export the visible graph, including expanded neighborhoods and path results.
      const evidence = await Promise.all(graph.edges.map(item => api<Edge>(`/graph/edges/${item.id}`)));
      if (format === 'json') {
        download('医学知识图谱.json', JSON.stringify({ ...graph, exported_at: new Date().toISOString(), evidence }, null, 2), 'application/json');
      } else {
        const fields = ['record_type','id','entity_type','standard_name','english_name','identifiers','synonyms','sources','relation_type','source_entity','target_entity','confidence','verification_status','evidence_type','evidence_text','evidence_field','qualifiers','source_record'];
        const cell = (value: unknown) => { let text = value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value); if (/^\s*[=+\-@]/.test(text)) text = "'" + text; return '"' + text.replaceAll('"', '""') + '"'; };
        const records: Record<string, unknown>[] = [...graph.nodes.map(n => ({ record_type: 'entity', ...n })), ...evidence.map(e => ({ record_type: 'relation', ...e }))];
        download('医学知识图谱.csv', '\ufeff' + [fields.join(','), ...records.map(row => fields.map(field => cell(row[field])).join(','))].join('\r\n'), 'text/csv;charset=utf-8');
      }
    } catch (exc) { reportError(exc); } finally { setExporting(false); }
  };

  return <section className="kg-page" aria-label="医学知识图谱">
    <div className="kg-heading"><div><div className="page-eyebrow"><Network size={15} /> MEDICAL KNOWLEDGE GRAPH</div><h1>医学知识图谱</h1><p>从实体出发，沿证据探索疾病、治疗与研究之间的联系。</p></div><div className="kg-totals"><strong>{stats ? Object.values(stats.counts).reduce((a,b) => a+b, 0) : '—'}</strong> 实体 <span>·</span> <strong>{stats?.edge_count ?? '—'}</strong> 关系</div></div>
    <div className="kg-search-row"><form onSubmit={event => { event.preventDefault(); if (matches[0]) setCenter(matches[0]); }}><Search size={17} /><input aria-label="搜索中心实体" placeholder="搜索 MDS、AML、药物、基因或 NCT / PMID" value={query} onChange={e => setQuery(e.target.value)} /><button className="secondary-button" disabled={!matches.length}>定位实体</button></form><button className="primary-button" disabled={syncing || !query.trim()} onClick={sync}>{syncing ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}{syncing ? '正在同步真实数据' : '同步真实数据'}</button></div>
    <div className="kg-domains">研究领域：{['MDS','AML','HSCT','DLI','GVHD'].map(value => <button key={value} className={query === value ? 'active' : ''} onClick={() => { setQuery(value); api<{entities:Entity[]}>(`/graph/entities?q=${value}`).then(data => { if (data.entities[0]) setCenter(data.entities[0]); }).catch(reportError); }}>{value}</button>)}</div>
    {matches.length > 0 && <div className="kg-search-results" aria-label="实体搜索结果">{matches.slice(0, 8).map(node => <button key={node.id} className={center?.id === node.id ? 'active' : ''} onClick={() => setCenter(node)}><i style={{ background: colors[node.entity_type] }} />{node.standard_name}<small>{names[node.entity_type]}</small></button>)}</div>}
    {!matches.length && <p className="kg-message">知识库中暂无匹配实体，可点击“同步真实数据”检索外部来源。</p>}
    {run && <div className={`kg-sync-status ${run.status === 'failed' ? 'failed' : ''}`} role="status"><strong>{statusName(run.status)}</strong><span>{run.query}</span>{run.report.imported && <span>试验 {run.report.imported.trials} · 论文 {run.report.imported.publications} · 蛋白 {run.report.imported.proteins} · 药物标准化 {run.report.imported.drugs}</span>}{run.status === 'running' && <span>正在读取 {run.report.current_source || '外部来源'}，可继续浏览。</span>}{!!run.report.errors?.length && <details><summary>{run.report.errors.length} 项来源未完成</summary>{run.report.errors.map((item,index) => <p key={index}>{item.source}：{item.message}</p>)}</details>}</div>}
    {error && <div className="error-banner" role="alert">{error}<button className="icon-button" aria-label="关闭图谱错误" onClick={() => setError('')}><X size={15} /></button></div>}
    <div className="kg-filters"><span>实体分类</span><button className={!filters.length ? 'active' : ''} onClick={() => setFilters([])}>全部</button>{Object.entries(names).map(([key,label]) => <button className={filters.includes(key) ? 'active' : ''} key={key} onClick={() => setFilters(previous => previous.includes(key) ? previous.filter(v => v !== key) : [...previous, key])}><i style={{ background: colors[key] }} />{label}</button>)}</div>
    <div className="kg-options"><label>关系范围 <select aria-label="关系展开深度" value={depth} onChange={e => setDepth(Number(e.target.value))}><option value={1}>一度关系</option><option value={2}>二度关系</option></select></label><label><input type="checkbox" checked={inferred} onChange={e => setInferred(e.target.checked)} />显示推断候选</label><span className="kg-legend">实线：来源记载　虚线：推断待核验</span><button className="quiet-button" onClick={() => setRevision(v => v + 1)} disabled={!center}>重置视图</button></div>
    <div className="kg-workspace"><div className="kg-network"><div className="kg-canvas" ref={graphElement} role="img" aria-label={`知识网络：${graph.nodes.length}个节点，${graph.edges.length}条关系`} />
      {loading && <div className="kg-canvas-loading"><Loader2 className="spin" size={22} />加载关系…</div>}
      {!loading && !graph.edges.length && <div className="kg-empty"><Network size={32} /><strong>当前范围内暂无关系</strong><p>可调整分类筛选或同步真实数据。网络仅展示有来源支持的关系，不会自动填入演示数据。</p></div>}
      <div className="kg-canvas-controls"><button className="icon-button" aria-label="放大图谱" onClick={() => cy.current?.zoom((cy.current?.zoom() || 1) * 1.25)}><ZoomIn size={17} /></button><button className="icon-button" aria-label="缩小图谱" onClick={() => cy.current?.zoom((cy.current?.zoom() || 1) / 1.25)}><ZoomOut size={17} /></button><button className="icon-button" aria-label="适应图谱" onClick={() => cy.current?.fit(undefined, 40)}><Maximize size={17} /></button><button className="icon-button" aria-label="定位中心实体" onClick={() => { if (center) cy.current?.center(cy.current.getElementById(center.id)); }}><LocateFixed size={17} /></button></div>
      <div className="kg-canvas-caption">{graph.nodes.length} 个节点 · {graph.edges.length} 条关系{graph.truncated && ' · 已达到显示上限，请缩小范围'}<span>拖动节点 · 滚轮缩放 · 点击关系查看证据</span></div></div>
      <aside className="kg-detail" aria-label="图谱详情">
        {selected ? <><span className="kg-type" style={{ color: colors[selected.entity_type] }}>{names[selected.entity_type]}</span><h2>{selected.standard_name}</h2>{selected.english_name && <p>{selected.english_name}</p>}<div className="kg-detail-actions"><button className="secondary-button" onClick={expand} disabled={loading}>展开{depth === 1 ? '一' : '二'}度关系</button><button className="quiet-button" onClick={() => setCenter(selected)}>设为中心</button></div><h3>原始标识与术语编码</h3>{selected.identifiers.length ? selected.identifiers.map(id => <p key={id.scheme + id.value}><b>{id.scheme}</b> · {id.value}</p>) : <p>暂无可靠标准编码映射</p>}<h3>同义词 / 原始名称</h3><p>{selected.synonyms.join(' · ')}</p><h3>数据来源</h3>{selected.sources.length ? selected.sources.map(source => <SourceView key={source.id} source={source} />) : <p>本地领域词表，尚未附加外部证据。</p>}<details><summary>全部实体属性</summary><pre>{JSON.stringify(selected.properties, null, 2)}</pre><small>实体 ID：{selected.id}</small></details></> : edge ? <><span className={`kg-verification ${edge.verification_status === 'source_asserted' ? '' : 'uncertain'}`}>{edge.verification_status === 'source_asserted' ? '来源明确记载' : '推断待核验'}</span><h2>{edge.label}</h2><p>{graph.nodes.find(n => n.id === edge.source_entity)?.standard_name} <ArrowRight size={12} /> {graph.nodes.find(n => n.id === edge.target_entity)?.standard_name}</p><p>人工审核：{edge.human_review_status==='accepted'?'已确认关系语义':edge.human_review_status==='rejected'?'已拒绝':edge.human_review_status==='needs_review'?'需进一步核验':'未审核'}</p><h3>来源证据</h3><blockquote>{edge.evidence_text}</blockquote><p>证据类型：{edge.evidence_type}</p><p>抽取置信度：{Math.round(edge.confidence * 100)}%（非疗效概率）</p>{Object.keys(edge.qualifiers).length > 0 && <pre>{JSON.stringify(edge.qualifiers, null, 2)}</pre>}{edge.source_record ? <SourceView source={edge.source_record} /> : <p>读取来源记录…</p>}<details><summary>原始来源快照</summary><pre>{JSON.stringify(edge.source_record?.payload, null, 2)}</pre></details><small>字段：{edge.evidence_field}<br />创建：{edge.created_at}</small></> : <div className="kg-detail-placeholder"><Network size={28} /><h2>沿证据探索</h2><p>点击节点查看实体属性，点击连线查看证据和核验状态。</p></div>}
        <details className="kg-node-list"><summary>当前节点列表（{graph.nodes.length}）</summary>{graph.nodes.map(node => <button key={node.id} onClick={() => { showEntity(node); cy.current?.center(cy.current.getElementById(node.id)); }}>{names[node.entity_type]} · {node.standard_name}</button>)}</details>
        <details className="kg-node-list"><summary>当前关系列表（{graph.edges.length}）</summary>{graph.edges.map(item => <button key={item.id} onClick={() => showEdge(item)}>{item.label} · {graph.nodes.find(n => n.id === item.target_entity)?.standard_name}</button>)}</details>
      </aside></div>
    <div className="kg-path"><div><h3>多跳路径查询</h3><p>起点：{center?.standard_name || '请先选择中心实体'}；按已导入关系查找最多6跳的连接。</p></div><input aria-label="搜索路径目标" placeholder="搜索目标疾病、药物或其他实体" value={pathQuery} onChange={e => setPathQuery(e.target.value)} /><select aria-label="选择路径目标" value={pathTarget} onChange={e => setPathTarget(e.target.value)}><option value="">选择目标</option>{pathTargets.map(node => <option key={node.id} value={node.id}>{names[node.entity_type]} · {node.standard_name}</option>)}</select><label><input type="checkbox" checked={pathInferred} onChange={e => setPathInferred(e.target.checked)} />允许推断关系</label><button className="secondary-button" disabled={!center || !pathTarget || loading} onClick={findPath}>查询路径</button></div>
    {pathMessage && <p className="kg-message" role="status">{pathMessage}</p>}
    <footer className="kg-footer"><p>来源记载表示原始数据库有此记录，不等于疗效证实。数据为有限范围检索；缺少关系不代表医学上不存在关联。</p><button className="quiet-button" disabled={exporting || !graph.nodes.length} onClick={() => exportGraph('json')}><Download size={14} />导出 JSON</button><button className="quiet-button" disabled={exporting || !graph.nodes.length} onClick={() => exportGraph('csv')}><Download size={14} />导出 CSV</button></footer>
  </section>;
}

function SourceView({ source }: { source: Source }) {
  return <div className="kg-source"><a href={safeUrl(source.source_url)} target="_blank" rel="noopener noreferrer">{source.provider} · {source.source_id}<ExternalLink size={12} /></a><small>来源更新：{source.source_updated_at || '来源未提供'}<br />获取时间：{new Date(source.fetched_at).toLocaleString('zh-CN')}</small></div>;
}
