import { useEffect, useState } from 'react';
import { FileUp, Download, Plus, ShieldCheck } from 'lucide-react';
import { post } from './api';

type Props = { dataset: any; changed: (data: any) => void; work: (task: () => Promise<void>) => Promise<void>; busy: boolean; exportFile: (url: string, name: string) => Promise<void> };
const domains = ['MDS','AML','HSCT','DLI','GVHD'];
const parse = (value: string) => { try { return JSON.parse(value); } catch { throw new Error('JSON 格式不正确，请检查逗号和引号。'); } };

export default function BenchmarkDataset({ dataset, changed, work, busy, exportFile }: Props) {
  const snapshot=dataset.snapshot, documents=snapshot.payload.documents, queries=snapshot.payload.queries;
  const [queryId,setQueryId]=useState(queries[0]?.query_id || '');
  const [draft,setDraft]=useState<any>();
  const [actor,setActor]=useState('');
  const [confirmed,setConfirmed]=useState(false);
  const [medical,setMedical]=useState('[]');
  const [medicalEdited,setMedicalEdited]=useState(false);
  const [search,setSearch]=useState('');
  const [importKind,setImportKind]=useState('queries');
  const [content,setContent]=useState('');
  const [attest,setAttest]=useState(false);
  const [docId,setDocId]=useState(documents[0]?.document_id || '');
  const [docEntities,setDocEntities]=useState('[]');
  useEffect(() => {
    if(!queryId) return;
    const query=queries.find((q:any)=>q.query_id===queryId);
    setDraft(query ? structuredClone({...query,...(dataset.query_provenance?.[query.query_id]||{})}) : undefined);
    setMedical(JSON.stringify(query?.medical_gold?.entities || [],null,2));setMedicalEdited(false);setConfirmed(false);
  },[queryId,snapshot.id]);
  useEffect(() => { setDocEntities(JSON.stringify(documents.find((d:any)=>d.document_id===docId)?.entities || [],null,2)); },[docId,snapshot.id]);
  const label = (id: string) => draft?.relevant_documents.find((x:any)=>x.document_id===id);
  const setLabel = (id: string, value: string) => setDraft((old:any) => ({...old,relevant_documents:[...old.relevant_documents.filter((x:any)=>x.document_id!==id),...(value===''?[]:[{document_id:id,relevance:Number(value),annotation_source:'human',verification_status:'candidate',_reviewed:true}])]}));
  const createQuery = () => { const id=`${dataset.domain==='MIXED'?'MDS':dataset.domain}_${Date.now().toString().slice(-6)}`;setQueryId('');setDraft({query_id:id,query:'',domain:domains.includes(dataset.domain)?dataset.domain:'MDS',query_type:'clinical_research',entities:[],relevant_documents:[],review_complete:false});setMedical('[]');setMedicalEdited(false);setConfirmed(false); };
  const saveQuery = () => work(async () => {
    const record=structuredClone(draft);
    record.annotation_source='human';record.verification_status='verified';record.annotator=actor;
    record.relevant_documents=record.relevant_documents.map((x:any)=>{if(x._reviewed){x.annotation_source='human';x.verification_status='verified';x.annotator=actor;}delete x._reviewed;return x;});
    if(medicalEdited) record.medical_gold={entities:parse(medical),annotation_source:'human',verification_status:'verified',annotator:actor};
    changed(await post(`/benchmark/datasets/${dataset.id}/queries`,{record,actor,confirm_human_review:confirmed}));
  });
  const shown=documents.filter((d:any)=>(d.title+' '+d.document_id).toLowerCase().includes(search.toLowerCase())).slice(0,100);
  return <div className="bench-data">
    <div className="bench-panel"><div className="bench-panel-heading"><div><h2>固定候选文献</h2><p>当前版本 {documents.length} 篇。各检索算法使用完全相同的标题和摘要。</p></div><button className="secondary-button" disabled={busy} onClick={()=>work(async()=>changed(await post(`/benchmark/datasets/${dataset.id}/import-graph`)))}>从知识图谱导入真实文献</button></div>
      <div className="bench-actions"><button className="quiet-button" onClick={()=>work(()=>exportFile(`/benchmark/datasets/${dataset.id}/export?kind=queries&version=${snapshot.version}`,'benchmark-queries.jsonl'))}><Download size={14}/>导出 Query JSONL</button><button className="quiet-button" onClick={()=>work(()=>exportFile(`/benchmark/datasets/${dataset.id}/export?kind=documents&version=${snapshot.version}`,'benchmark-documents.jsonl'))}><Download size={14}/>导出文献 JSONL</button></div>
      <details><summary><FileUp size={14}/> 导入 JSONL</summary><div className="bench-form-grid"><label>导入内容<select aria-label="导入内容" value={importKind} onChange={e=>setImportKind(e.target.value)}><option value="queries">问题及相关性标注</option><option value="documents">候选文献</option></select></label><label>选择 JSONL 文件<input aria-label="选择 JSONL 文件" type="file" accept=".jsonl,.ndjson,.json,text/plain" onChange={e=>{const file=e.target.files?.[0];if(file)work(async()=>{if(file.size>20_000_000)throw new Error('文件不能超过20MB');setContent(await file.text());});}}/></label></div><textarea className="bench-code" aria-label="JSONL 导入文本" placeholder={'每行一个 JSON 对象；先导入文献，再导入标注。'} value={content} onChange={e=>setContent(e.target.value)} rows={5}/><label className="bench-check"><input type="checkbox" checked={attest} onChange={e=>setAttest(e.target.checked)}/>我确认文件中的 verified 标注已由人工审核；AI 标注仍保留为候选。</label><div className="bench-actions"><input aria-label="导入审核人" placeholder="审核人（导入人工 Gold 时必填）" value={actor} onChange={e=>setActor(e.target.value)}/><button className="primary-button" disabled={busy||!content.trim()||(attest&&!actor.trim())} onClick={()=>work(async()=>{changed(await post(`/benchmark/datasets/${dataset.id}/import`,{kind:importKind,content,attest_human_gold:attest,actor}));setContent('');})}>导入并创建新版本</button></div></details>
    </div>
    <div className="bench-panel"><div className="bench-panel-heading"><div><h2>问题与人工 Gold</h2><p>0 不相关 · 1 弱相关 · 2 相关 · 3 高度相关。候选标签需要逐条人工确认。</p></div><button className="secondary-button" onClick={createQuery}><Plus size={14}/>新增 Query</button></div>
      <label>选择 Query<select aria-label="选择标注 Query" value={queryId} onChange={e=>setQueryId(e.target.value)}><option value="">新问题</option>{queries.map((q:any)=><option key={q.query_id} value={q.query_id}>{q.query_id} · {q.query}</option>)}</select></label>
      {draft && <>{draft.generation&&<p className="bench-notice">生成来源：{draft.generation.origin} · 模型：{draft.generation.model_name} · 版本：{draft.generation.model_version}{draft.generation.metadata_status==='incomplete'?'（历史元数据不完整，未补造模型版本）':''}</p>}<div className="bench-form-grid"><label>Query ID<input aria-label="Query ID" value={draft.query_id} onChange={e=>setDraft({...draft,query_id:e.target.value})}/></label><label>医学主题<select aria-label="医学主题" value={draft.domain} onChange={e=>setDraft({...draft,domain:e.target.value})}>{domains.map(d=><option key={d}>{d}</option>)}</select></label><label>问题类型<input aria-label="问题类型" value={draft.query_type} onChange={e=>setDraft({...draft,query_type:e.target.value})}/></label></div>
      <label>研究问题<textarea aria-label="Benchmark 研究问题" rows={2} value={draft.query} onChange={e=>{setMedicalEdited(false);setDraft({...draft,query:e.target.value,review_complete:false,medical_gold:draft.medical_gold?{...draft.medical_gold,verification_status:'candidate'}:undefined,relevant_documents:draft.relevant_documents.map((x:any)=>({...x,verification_status:'candidate',_reviewed:false}))});}}/></label>
      <input className="bench-search" aria-label="筛选待标注文献" placeholder="按标题或 PMID 筛选文献" value={search} onChange={e=>setSearch(e.target.value)}/>
      <div className="bench-judgments">{shown.map((doc:any)=>{const item=label(doc.document_id);return <article className="bench-judgment" key={doc.document_id}><div><code>{doc.document_id}</code><strong>{doc.title}</strong><details><summary>查看摘要</summary><p>{doc.text||'来源没有提供摘要。'}</p></details></div><div><select aria-label={`相关性 ${doc.document_id}`} value={item?.relevance ?? ''} onChange={e=>setLabel(doc.document_id,e.target.value)}><option value="">未标注</option>{[0,1,2,3].map(v=><option key={v} value={v}>{v} · {['不相关','弱相关','相关','高度相关'][v]}</option>)}</select><span className={`bench-badge ${item?.verification_status==='verified'&&item?.annotation_source==='human'?'good':''}`}>{item?item._reviewed?'人工待保存':item.verification_status==='verified'&&item.annotation_source==='human'?'人工 Gold':`${item.annotation_source==='ai'?'AI ':''}候选`:'未标注'}</span>{item && !item._reviewed && !(item.verification_status==='verified'&&item.annotation_source==='human') && <button className="quiet-button" onClick={()=>setLabel(doc.document_id,String(item.relevance))}>人工确认此标签</button>}</div></article>;})}{!shown.length&&<p className="bench-empty">请先导入候选文献。</p>}</div>
      {documents.length>100&&<small>最多显示100条匹配文献，请使用筛选框定位。</small>}
      <details><summary>疾病与药物实体 Gold（单独标注）</summary><p>用于 Disease / Drug Entity Recall。使用规范名称或 entity_id，与文献实体标注保持一致。</p><textarea aria-label="医学实体 Gold" className="bench-code" rows={4} value={medical} onChange={e=>{setMedical(e.target.value);setMedicalEdited(true);}}/></details>
      <label className="bench-check"><input type="checkbox" checked={!!draft.review_complete} onChange={e=>setDraft({...draft,review_complete:e.target.checked})}/>已审核当前候选范围的全部文献（包括所有0级负例）</label>
      <div className="bench-review"><ShieldCheck size={18}/><input aria-label="Gold 审核人" placeholder="人工审核人" value={actor} onChange={e=>setActor(e.target.value)}/><label className="bench-check"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已人工审核本次修改</label><button className="primary-button" disabled={busy||!confirmed||!actor.trim()||!draft.query.trim()} onClick={saveQuery}>保存 Gold 与数据版本</button></div></>}
    </div>
    <div className="bench-panel"><h2>文献实体标注</h2><p>与相关性评分分开维护。未审核的实体抽取不进入医学实体召回率。</p><select aria-label="选择实体标注文献" value={docId} onChange={e=>setDocId(e.target.value)}>{documents.map((d:any)=><option key={d.document_id} value={d.document_id}>{d.document_id} · {d.title}</option>)}</select><textarea aria-label="文献实体标注 JSON" className="bench-code" rows={4} value={docEntities} onChange={e=>setDocEntities(e.target.value)}/><div className="bench-actions"><input aria-label="文献实体审核人" placeholder="人工审核人" value={actor} onChange={e=>setActor(e.target.value)}/><button className="secondary-button" disabled={busy||!docId||!actor.trim()} onClick={()=>work(async()=>{const doc=documents.find((d:any)=>d.document_id===docId);changed(await post(`/benchmark/datasets/${dataset.id}/documents`,{record:{...doc,entities:parse(docEntities),entity_annotation:{annotation_source:'human',verification_status:'verified',annotator:actor}},actor,confirm_human_review:true}));})}>人工确认文献实体</button></div></div>
  </div>;
}
