import { useCallback, useEffect, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import { api, post, download } from './api';
import type { MedicalEntity } from './medical';
import './benchmark.css';
import './evidence-intelligence.css';

const BASE='/v1/evidence-intelligence';
const json=(v:unknown)=>JSON.stringify(v,null,2);
const url=(v:string)=>/^https?:\/\//i.test(v||'')?v:undefined;
const status=(s:string)=>({queued:'排队中',running:'分析中',completed:'分析完成 · 待人工核验',partial:'部分完成 · 查看来源或模型问题',empty:'证据不足',failed:'分析失败',interrupted:'已停止'}[s]||s);
const dirs:Record<string,string>={benefit:'原文报告改善',no_significant_benefit:'未观察到显著获益',harm:'原文报告不利结果',mixed:'结果混合',unclear:'方向待核验'};
const nr=(v:unknown)=>v==null||v===''?'Not Reported':String(v);

function SupportGraph({graph,onSelect}:{graph:any;onSelect:(item:any)=>void}) {
  const ref=useRef<HTMLDivElement>(null);const cy=useRef<cytoscape.Core|undefined>(undefined);const select=useRef(onSelect);select.current=onSelect;
  useEffect(()=>{
    if(!ref.current||!graph)return;
    const colors:Record<string,string>={Disease:'#246a55',Drug:'#a5773b',ClinicalTrial:'#507e9d',Publication:'#879849',EvidenceStatement:'#92699e',PICO:'#45988d',ResearchQuestion:'#285f50'};
    const instance=cytoscape({container:ref.current,elements:[...graph.nodes.map((n:any)=>({data:{...n,color:colors[n.kind]||'#71867c'}})),...graph.edges.map((e:any)=>({data:e}))],
      style:[{selector:'node',style:{'label':'data(label)','background-color':'data(color)','width':28,'height':28,'font-size':10,'text-wrap':'ellipsis','text-max-width':'130px','text-valign':'bottom','text-margin-y':5}},
        {selector:'edge',style:{'width':1.4,'line-color':'#b2c3b5','target-arrow-color':'#91a697','target-arrow-shape':'triangle','curve-style':'bezier'}},
        {selector:':selected',style:{'border-width':3,'border-color':'#bd8d42'}}],layout:{name:'cose',animate:false,nodeRepulsion:()=>12000},minZoom:.12,maxZoom:3});
    cy.current=instance;instance.on('tap','node,edge',e=>select.current(e.target.data()));
    return()=>{instance.destroy();cy.current=undefined;};
  },[graph]);
  return <><div className="ei-graph-tools"><button className="quiet-button" onClick={()=>cy.current?.fit(undefined,35)}>适配视图</button><button className="quiet-button" onClick={()=>cy.current?.zoom((cy.current?.zoom()||1)*1.3)}>放大</button><button className="quiet-button" onClick={()=>cy.current?.zoom((cy.current?.zoom()||1)/1.3)}>缩小</button><span>点击论文、陈述或关系查看出处；连通不代表因果。</span></div><div className="ei-graph" ref={ref} aria-label="证据支持关系图"/></>;
}

function SourceText({text,quote}:{text:string;quote?:string}) {
  const start=quote?text.indexOf(quote):-1;
  return <p className="ei-source-text">{start<0?text:<>{text.slice(0,start)}<mark>{quote}</mark>{text.slice(start+quote!.length)}</>}</p>;
}

export default function EvidenceIntelligence({model:initialModel,models}:{model:string;models:any[]}) {
  const [question,setQuestion]=useState('MDS患者接受Venetoclax联合Azacitidine治疗的效果如何？');
  const [search,setSearch]=useState(''),[mode,setMode]=useState(initialModel?'account':'rules'),[model,setModel]=useState(initialModel),[profile,setProfile]=useState('');
  const [live,setLive]=useState(true),[limit,setLimit]=useState(3),[schema,setSchema]=useState<any>(),[profiles,setProfiles]=useState<any[]>([]);
  const [records,setRecords]=useState<any[]>([]),[id,setId]=useState(''),[analysis,setAnalysis]=useState<any>(),[pico,setPico]=useState<any>();
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[selected,setSelected]=useState(''),[quote,setQuote]=useState(''),[graphItem,setGraphItem]=useState<any>();
  const [actor,setActor]=useState(''),[reason,setReason]=useState(''),[decision,setDecision]=useState('needs_review'),[confirmed,setConfirmed]=useState(false);
  const [tool,setTool]=useState(''),[target,setTarget]=useState(''),[judgments,setJudgments]=useState('{}'),[overall,setOverall]=useState('');
  const [statement,setStatement]=useState(''),[citations,setCitations]=useState('[]');
  const work=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  const refreshList=async()=>setRecords((await api<any>(BASE+'/analyses')).analyses);
  useEffect(()=>{void work(async()=>{const [s,m]=await Promise.all([api<any>(BASE+'/schema'),api<any>('/benchmark/models')]);setSchema(s);setProfiles(m.models.filter((m:any)=>m.method==='llm_assisted'));await refreshList();});},[]);
  const refresh=useCallback(async()=>{if(!id)return;const a=await api<any>(BASE+'/analyses/'+id);setAnalysis(a);setPico(a.payload.pico);setSelected(old=>a.extractions.some((x:any)=>x.id===old)?old:a.extractions[0]?.id||'');},[id]);
  useEffect(()=>{void refresh().catch(e=>setError(e.message));},[refresh]);
  const running=analysis&&['queued','running'].includes(analysis.status);
  useEffect(()=>{if(!running)return;const timer=setInterval(()=>refresh().catch(e=>setError(e.message)),1500);return()=>clearInterval(timer);},[running,refresh]);
  const payload=analysis?.payload, rows=analysis?.extractions||[],paper=rows.find((r:any)=>r.id===selected),p=paper?.payload;
  useEffect(()=>{setQuote('');setConfirmed(false);setTool('');setJudgments('{}');setOverall('');},[selected]);
  const body=()=>({question,search_query:search,live_search:live,limit,mode,model,model_profile_id:profile||null,release_id:null});
  const trials:MedicalEntity[]=Array.from(new Map<string,MedicalEntity>([...(payload?.trial_candidates||[]),...rows.flatMap((r:any)=>r.payload.trials)].map((n:any)=>[n.id,n] as [string,MedicalEntity])).values());
  const selectFinding=(row:any,finding:any)=>{setSelected(row.id);setTimeout(()=>{setQuote(finding.span.quote);document.getElementById('ei-source')?.scrollIntoView({behavior:'auto',block:'center'});},100);};
  const openGraph=(item:any)=>{setGraphItem(item);const row=rows.find((r:any)=>r.id===item.extraction_id||r.entity_id===item.entity_id);if(row){setSelected(row.id);const f=row.payload.findings.find((f:any)=>f.id===item.finding_id);if(f)selectFinding(row,f);}};
  return <section className="bench-page ei-page" aria-label="Evidence Intelligence">
    <div className="bench-heading"><div><div className="page-eyebrow">EVIDENCE INTELLIGENCE</div><h1>科研证据智能分析</h1><p>从问题到研究设计、结局和来源。首阶段为摘要级分析。</p></div><button className="secondary-button" onClick={()=>work(async()=>{await refreshList();await refresh();})}>刷新</button></div>
    {error&&<div className="error-banner" role="alert">{error}</div>}{notice&&<div className="bench-notice" role="status">{notice}</div>}
    <div className="bench-panel"><label>医学科研问题<textarea aria-label="证据分析问题" rows={3} value={question} onChange={e=>setQuestion(e.target.value)}/></label>
      <div className="bench-form-grid"><label>抽取方式<select aria-label="证据抽取方式" value={mode} onChange={e=>setMode(e.target.value)}><option value="account">已连接账号 · LLM 语义抽取</option><option value="rules">保守规则 · 不调用模型</option><option value="api">已有 LLM API 配置</option></select></label>
      {mode==='account'&&<label>模型<select aria-label="证据抽取模型" value={model} onChange={e=>setModel(e.target.value)}><option value="">选择模型</option>{models.map(m=><option key={m.id} value={m.model}>{m.displayName||m.model}</option>)}</select></label>}
      {mode==='api'&&<label>API 配置<select value={profile} onChange={e=>setProfile(e.target.value)}><option value="">在 Benchmark 模型配置中启用 LLM API</option>{profiles.map(m=><option key={m.id} value={m.id} disabled={!m.config.enabled}>{m.name}</option>)}</select></label>}
      <label>最多分析论文<input aria-label="证据论文上限" type="number" min={1} max={8} value={limit} onChange={e=>setLimit(Number(e.target.value))}/></label></div>
      <label>检索表达式（留空则按问题中的已知术语生成）<input aria-label="证据检索式" value={search} onChange={e=>setSearch(e.target.value)}/></label>
      <label className="bench-check"><input aria-label="实时检索医学来源" type="checkbox" checked={live} onChange={e=>setLive(e.target.checked)}/>检索 PubMed 与 ClinicalTrials.gov，并复用图谱来源；关闭则仅查询已保存文献</label>
      <div className="bench-actions"><button className="secondary-button" disabled={busy||running} onClick={()=>work(async()=>{const r=await post<any>(BASE+'/pico',body());setPico(r.pico);setSearch(r.search_query);setNotice('PICO 已按问题原文解析，缺失项保持为空。');})}>解析 PICO</button>
      <button className="primary-button" disabled={busy||running||question.trim().length<3||(mode==='account'&&!model)} onClick={()=>work(async()=>{const a=await post<any>(BASE+'/analyses',body());setAnalysis(a);setId(a.id);setGraphItem(undefined);await refreshList();})}>检索并分析证据</button>
      {running&&<button className="quiet-button" onClick={()=>work(async()=>{await post(BASE+'/analyses/'+id+'/cancel',{});await refresh();})}>停止分析</button>}</div>
      <p>LLM 模式会将问题和公开摘要发送给所选模型。未通过原文校验的输出不会作为有效抽取；降级会明确显示。</p>
    </div>
    <div className="bench-panel"><label>历史分析<select aria-label="历史证据分析" value={id} onChange={e=>{setId(e.target.value);setAnalysis(undefined);}}><option value="">选择分析记录</option>{records.map(a=><option key={a.id} value={a.id}>{a.question} · {status(a.status)}</option>)}</select></label>
      {analysis&&<><p>{status(analysis.status)} · {payload.phase} · 已抽取 {payload.processed||0} / {payload.candidate_ids?.length||0}</p><p>{payload.candidate_note}</p>{payload.errors?.map((e:any,i:number)=><p className="bench-warning" key={i}>{e.source||e.phase} {e.pmid||''}：{e.message||e.error}</p>)}<div className="bench-actions">{['json','markdown'].map(format=><button key={format} className="quiet-button" disabled={running} onClick={()=>work(async()=>{const r=await fetch('/api'+BASE+'/analyses/'+id+'/export?format='+format);if(!r.ok)throw Error('导出失败');download('evidence-'+id+(format==='json'?'.json':'.md'),await r.text(),format==='json'?'application/json':'text/markdown');})}>{format==='json'?'导出完整 JSON':'导出分析报告'}</button>)}</div></>}
    </div>
    {pico&&<div className="ei-pico">{[['population','P · 研究人群'],['intervention','I · 干预'],['comparison','C · 对照'],['outcome','O · 结局']].map(([key,label])=><article key={key}><h3>{label}</h3><p>{nr(pico[key]?.value)}</p>{!pico[key]?.value&&<small>问题未明确提供，未自行补充</small>}</article>)}</div>}
    {!!rows.length&&<>
      <div className="bench-panel"><h2>研究设计分布</h2><p>研究设计分类不等于实际质量；所有自动分类待审核。</p><div className="ei-designs">{Object.entries(payload.design_distribution||{}).map(([label,count])=><div key={label}><span>{label}</span><meter min={0} max={rows.length} value={Number(count)}/><b>{String(count)}</b></div>)}</div></div>
      <div className="bench-panel"><h2>相关临床试验</h2>{!trials.length?<p>未找到有来源的试验关联；不会按相同药物猜测 NCT。</p>:trials.map(n=><article className="ei-trial" key={n.id}><strong>{n.standard_name}</strong><p>{n.identifiers.map(i=>i.value).join(' · ')} · 登记状态：{nr(n.properties.status)} · {n.properties.metadata_pending?'仅注册号提及，尚无登记详情':'已保存注册记录'}</p><p>登记中的研究与预设终点不能作为已证明疗效。论文关联类型见各篇详情。</p>{n.sources.map(s=><a key={s.id} href={url(s.source_url)} target="_blank" rel="noreferrer">{s.provider} · {s.source_id} </a>)}</article>)}</div>
      <div className="bench-panel"><h2>主要结局与效应比较</h2><p>原文未注明“主要”的结局保持普通结局；HR、OR、RR 和 P 值只展示来源数值。</p><div className="bench-table-wrap"><table><thead><tr><th>论文</th><th>人群 / 样本</th><th>设计</th><th>主要结局</th><th>效应量 / 显著性</th></tr></thead><tbody>{rows.map((r:any)=><tr key={r.id}><td><button className="quiet-button" onClick={()=>setSelected(r.id)}>PMID {r.payload.publication.pmid}</button><small>{r.review_status==='rejected'?'已拒绝，不参与当前差异比较':r.review_status}</small></td><td>{nr(r.payload.fields.population.value)}<small>{nr(r.payload.fields.sample_size.span?.quote||r.payload.fields.sample_size.value)}</small></td><td>{r.payload.design.label}</td><td>{nr(r.payload.fields.primary_outcomes.value)}</td><td>{r.payload.numbers.ratios.map((x:any,i:number)=><p key={i}>{x.literal} · 95% CI {x.ci95?`${x.ci95.lower}–${x.ci95.upper}`:'Not Reported'} {x.validation_issues.join(', ')}</p>)}{!r.payload.numbers.ratios.length&&<p>HR / OR / RR: Not Reported</p>}{r.payload.numbers.p_values.map((v:any,i:number)=><small key={i}>{v.literal}{!v.valid?' · 数值异常':''}</small>)}{!r.payload.numbers.p_values.length&&<small>P-value: Not Reported</small>}</td></tr>)}</tbody></table></div></div>
      <div className="bench-panel"><h2>研究结论差异</h2>{!analysis.current_comparisons.length?<p>尚无可匹配结局的研究对；不能据此认定不存在冲突。</p>:analysis.current_comparisons.map((c:any,i:number)=><details key={i}><summary>{c.outcome} · {c.status==='potential_conflict'?'潜在冲突，需人工核验':c.status==='direction_difference'?'方向差异，研究条件需核验':'结局可比较'} · {c.directions.map((d:string)=>dirs[d]).join(' / ')}</summary><p>{c.note}</p><p>{c.shared_trial_ids.length?'可能包含同一试验的重叠人群，不算独立重复证据。':'研究人群是否重叠尚未确认。'}</p><div className="bench-actions">{[c.left_extraction_id,c.right_extraction_id].map((key:string)=><button className="quiet-button" key={key} onClick={()=>setSelected(key)}>查看 PMID {rows.find((r:any)=>r.id===key)?.payload.publication.pmid}</button>)}</div><pre className="research-json">{json(c.differences)}</pre></details>)}</div>
      <div className="bench-panel"><h2>证据支持关系图</h2><SupportGraph graph={analysis.current_graph||payload.graph} onSelect={openGraph}/>{graphItem&&<details open><summary>选中对象与来源定位</summary><pre className="research-json">{json(graphItem)}</pre></details>}</div>
      <div className="bench-panel"><h2>论文原文与结构化证据</h2><select aria-label="选择证据论文" value={selected} onChange={e=>setSelected(e.target.value)}>{rows.map((r:any)=><option key={r.id} value={r.id}>PMID {r.payload.publication.pmid} · {r.payload.publication.title}</option>)}</select>
      {p&&<><h3>{p.publication.title}</h3><p>PMID {p.publication.pmid} · DOI {nr(p.publication.doi)} · {nr(p.publication.journal)} · {nr(p.publication.publication_date)}</p><a href={url(p.publication.url)} target="_blank" rel="noreferrer">打开 PubMed 原始记录</a><p className="ei-scope">仅 PubMed 摘要 · 未核验全文 · 自动提取待人工审核 · {p.generation.origin==='ai'?`模型 ${p.generation.model_name} / ${p.generation.model_version}`:'保守规则提取'}</p>
      <div className="ei-fields">{Object.entries(p.fields).map(([key,v]:[string,any])=><article key={key}><h4>{schema?.fields[key]||key}</h4><p>{nr(v.value)}</p>{['sample_size','follow_up'].includes(key)&&v.span&&v.span.quote!==v.value&&<blockquote className="research-quote">{v.span.quote}</blockquote>}{v.span&&<button className="quiet-button" onClick={()=>{setQuote(v.span.quote);document.getElementById('ei-source')?.scrollIntoView({behavior:'smooth'});}}>定位摘要原文</button>}</article>)}</div>
      <h3>作者证据陈述</h3>{!p.findings.length&&<p>未抽取到可定位的结局陈述。</p>}{p.findings.map((f:any)=><article className="ei-finding" key={f.id}><span className="bench-badge">{f.outcome} · {dirs[f.direction]}</span><blockquote>{f.statement}</blockquote><button className="quiet-button" onClick={()=>selectFinding(paper,f)}>定位原始证据</button><button className="quiet-button" onClick={()=>setCitations(json([{extraction_id:paper.id,quote:f.statement,relationship:'supports'}]))}>用于人工多论文结论</button></article>)}
      <details><summary>明确的论文—试验关联及原始证据</summary><pre className="research-json">{json(p.trial_links)}</pre></details>
      <details id="ei-source" open><summary>原始摘要（高亮定位）</summary><SourceText text={p.source.abstract||'Not Reported：PubMed 未提供摘要'} quote={quote}/></details>
      <details><summary>来源、模型、数值及版本追溯</summary><pre className="research-json">{json({source_id:paper.source_id,revision_id:paper.revision_id,generation:p.generation,extracted_at:p.extracted_at,numbers:p.numbers})}</pre></details>
      <h3>抽取审核</h3><div className="bench-form-grid"><label>审核人<input aria-label="证据审核人" value={actor} onChange={e=>setActor(e.target.value)}/></label><label>决定<select aria-label="证据审核决定" value={decision} onChange={e=>setDecision(e.target.value)}><option value="needs_review">需要进一步核验</option><option value="accepted">接受抽取</option><option value="rejected">拒绝抽取</option></select></label></div><label>审核依据<textarea aria-label="证据审核依据" value={reason} onChange={e=>setReason(e.target.value)}/></label><label className="bench-check"><input aria-label="确认人工证据审核" type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已核验本次提交的来源、条件与语义</label>
      <button className="primary-button" disabled={busy||!confirmed||!actor.trim()||!reason.trim()} onClick={()=>work(async()=>{await post(BASE+'/extractions/'+selected+'/review',{actor,reason,decision,confirm_human_review:true});setConfirmed(false);await refresh();setNotice('审核已追加保存，原抽取记录保持可追溯。');})}>保存抽取审核</button>
      <h3>研究质量评价</h3><p>当前 {p.quality_status==='awaiting_appraisal'?'默认待专业评价':'已记录'}。使用版本化人工工作表，不计算 AI 可信度总分。只看到摘要时，无法判断的域应记录无信息。</p>
      <label>适用工具<select aria-label="证据质量工具" value={tool} onChange={e=>{setTool(e.target.value);setOverall('');const t=schema.appraisal_tools[e.target.value];setJudgments(json(t?Object.fromEntries(Object.keys(t.domains).map(k=>[k,{judgment:t.choices.includes('no_information')?'no_information':t.choices.includes('unclear')?'unclear':t.choices[0],reason:''}])):{}));}}><option value="">选择工具版本；不适用时不强行分级</option>{p.appraisal_tools.map((key:string)=><option key={key} value={key}>{schema?.appraisal_tools[key].name} · {schema?.appraisal_tools[key].version}</option>)}</select></label>
      {tool&&<><p>{schema.appraisal_tools[tool].note} <a href={schema.appraisal_tools[tool].url} target="_blank" rel="noreferrer">官方工具</a></p><details><summary>评价域与合法判断</summary><pre className="research-json">{json({domains:schema.appraisal_tools[tool].domains,choices:schema.appraisal_tools[tool].choices})}</pre></details><label>评价目标（具体结局 / 效应）<input aria-label="质量评价目标" value={target} onChange={e=>setTarget(e.target.value)}/></label><label>逐域人工判断 JSON<textarea aria-label="质量评价判断" className="bench-code" rows={8} value={judgments} onChange={e=>setJudgments(e.target.value)}/></label><label>总体判断（可空；非算法生成）<select value={overall} onChange={e=>setOverall(e.target.value)}><option value="">未完成</option>{schema.appraisal_tools[tool].overall.map((v:string)=><option key={v}>{v}</option>)}</select></label><button className="secondary-button" disabled={busy||!confirmed||!actor.trim()||!target.trim()} onClick={()=>work(async()=>{await post(BASE+'/extractions/'+selected+'/appraisals',{actor,tool,target,judgments:JSON.parse(judgments),overall:overall||null,rationale:reason,confirm_human_review:true});setConfirmed(false);await refresh();setNotice('人工质量评价已按工具版本保存。');})}>保存质量评价记录</button></>}
      {analysis.appraisals.filter((a:any)=>a.extraction_id===selected).map((a:any)=><details key={a.id}><summary>{a.payload.tool} · {a.actor} · {a.payload.status} · {a.created_at}</summary><pre className="research-json">{json(a.payload)}</pre></details>)}
      </>}</div>
      <div className="bench-panel"><h2>多论文证据结论（人工审核）</h2><p>引用可标为支持、矛盾或背景。每项都必须引用本次论文的原始摘要，不依据“同药物”自动合并试验。</p><label>结论<textarea aria-label="多论文结论" value={statement} onChange={e=>setStatement(e.target.value)}/></label><label>引用 JSON<textarea aria-label="多论文引用" className="bench-code" rows={6} value={citations} onChange={e=>setCitations(e.target.value)}/></label><p>复用上方审核人、依据与确认。每项填写 extraction_id、quote、relationship（supports / contradicts / context）。</p><button className="primary-button" disabled={busy||!confirmed||!actor.trim()||!reason.trim()||!statement.trim()} onClick={()=>work(async()=>{await post(BASE+'/analyses/'+id+'/statements',{text:statement,citations:JSON.parse(citations),actor,reason,confirm_human_review:true});setConfirmed(false);await refresh();setNotice('多来源结论及支持/矛盾关系已保存。');})}>保存人工证据结论</button>{analysis.statements.map((s:any)=><details key={s.id}><summary>{s.payload.text} · {s.actor}</summary>{s.payload.citations.map((c:any,i:number)=><blockquote key={i}>PMID {c.publication.pmid} · {c.relationship}<p>{c.quote}</p></blockquote>)}</details>)}</div>
    </>}
    <footer className="bench-footer">原始来源、抽取结果与人工判断分开保存 · 研究设计不等于证据质量 · 不把摘要陈述上升为临床指南</footer>
  </section>;
}
