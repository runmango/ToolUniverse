import { useState } from 'react';

export const palette = ['#2d7058','#ba8146','#617eb1','#9a6ea6','#bd6d67','#658c85','#9b983e','#6d7680'];
export const scoreText = (value: number | null | undefined) => value == null ? '—' : value.toFixed(3);
type Series = { id: string; name: string; metrics: any };

export default function BenchmarkCharts({ series, comparable }: { series: Series[]; comparable: boolean }) {
  const [k, setK] = useState('10');
  const [hidden, setHidden] = useState<string[]>([]);
  const usable = series.filter(s => s.metrics?.scored_query_count > 0 && !hidden.includes(s.id));
  if (!series.length) return <div className="bench-empty">选择实验记录后，在这里查看同一数据版本下的性能图表。</div>;
  if (!comparable) return <div className="bench-warning">实验的数据、Gold 或评价配置不同，暂不绘制模型排名。请选择相同评价条件的实验。</div>;
  return <section className="bench-chart-section" aria-label="检索性能图表">
    <div className="bench-chart-toolbar"><label>图表 K <select aria-label="图表 K" value={k} onChange={e => setK(e.target.value)}>{['5','10','20'].map(v => <option key={v}>{v}</option>)}</select></label><span>点击图例显示 / 隐藏模型</span>{series.map((s,i) => <button key={s.id} onClick={() => setHidden(old => old.includes(s.id) ? old.filter(x => x !== s.id) : [...old,s.id])} className={hidden.includes(s.id) ? 'muted' : ''}><i style={{background:palette[i%8]}} />{s.name}</button>)}</div>
    {!usable.length ? <div className="bench-empty">暂无可评分结果。真实检索排名会保留；完成人工 Gold 后，在新数据版本运行评价。</div> : <div className="bench-charts">
      <Chart title="Precision@K 趋势" description="横轴 K=5、10、20；纵轴精确率。">
        {[0,.25,.5,.75,1].map(v => <g key={v}><line x1="45" x2="410" y1={195-v*160} y2={195-v*160} className="grid"/><text x="30" y={199-v*160} textAnchor="end">{v}</text></g>)}
        {['5','10','20'].map((v,i) => <text key={v} x={70+i*150} y="220" textAnchor="middle">K={v}</text>)}
        {usable.map(s => { const color=palette[series.indexOf(s)%8]; const points=['5','10','20'].map((v,i) => [70+i*150,195-s.metrics.at_k[v].Precision*160]); return <g key={s.id}><polyline points={points.map(p => p.join(',')).join(' ')} fill="none" stroke={color} strokeWidth="2"/>{points.map((p,i) => <circle key={i} cx={p[0]} cy={p[1]} r="5" fill={color}><title>{s.name} · P@{[5,10,20][i]} = {scoreText(s.metrics.at_k[[5,10,20][i]].Precision)}</title></circle>)}</g>; })}
      </Chart>
      <Chart title={`Recall@${k} 对比`} description="相关文献的召回率；仅使用人工已审核的 Gold。">
        {usable.map((s,i) => <g key={s.id}><text x="15" y={36+i*200/usable.length}>{s.name.slice(0,24)}</text><rect x="15" y={44+i*200/usable.length} width="330" height="10" rx="4" className="bar-track"/><rect x="15" y={44+i*200/usable.length} width={330*s.metrics.at_k[k].Recall} height="10" rx="4" fill={palette[series.indexOf(s)%8]}/><text x="360" y={54+i*200/usable.length}>{scoreText(s.metrics.at_k[k].Recall)}</text></g>)}
      </Chart>
      <Chart title={`MRR / MAP / NDCG@${k}`} description="MRR、MAP 按实验保存的检索深度截断。">
        {['MRR','MAP','NDCG'].map((metric,mi) => <g key={metric}><text x={90+mi*140} y="224" textAnchor="middle">{metric}</text>{usable.map((s,i) => { const v=metric==='NDCG'?s.metrics.at_k[k].NDCG:s.metrics[metric];const w=Math.min(24,90/usable.length);return <rect key={s.id} x={50+mi*140+i*w} y={195-v*160} width={w-3} height={v*160} fill={palette[series.indexOf(s)%8]}><title>{s.name} · {metric} = {scoreText(v)}</title></rect>;})}</g>)}
        {[0,.5,1].map(v => <text key={v} x="20" y={199-v*160}>{v}</text>)}
      </Chart>
      <Chart title={`模型性能雷达 · K=${k}`} description="六个维度统一为0–1，不含生成质量指标。">
        {[.25,.5,.75,1].map(scale => <polygon key={scale} points={Array.from({length:6},(_,i)=>point(i,scale).join(',')).join(' ')} className="radar-grid"/>)}
        {['Precision','Recall','MRR','MAP','NDCG','Hit Rate'].map((name,i) => { const p=point(i,1.25);return <text key={name} x={p[0]} y={p[1]+4} textAnchor="middle">{name}</text>;})}
        {usable.map(s => { const m=s.metrics;const values=[m.at_k[k].Precision,m.at_k[k].Recall,m.MRR,m.MAP,m.at_k[k].NDCG,m.at_k[k].HitRate];return <polygon key={s.id} points={values.map((v,i)=>point(i,v).join(',')).join(' ')} fill={palette[series.indexOf(s)%8]+'16'} stroke={palette[series.indexOf(s)%8]} strokeWidth="2"><title>{s.name}：{values.map(scoreText).join(' / ')}</title></polygon>;})}
      </Chart>
    </div>}
    <div className="bench-table-wrap"><table><caption>图表数值及标注覆盖率</caption><thead><tr><th>模型</th><th>已评分 Query</th><th>P@{k}</th><th>R@{k}</th><th>MRR</th><th>MAP</th><th>NDCG@{k}</th><th>Hit@{k}</th><th>Judged@{k}</th></tr></thead><tbody>{series.map(s => <tr key={s.id}><td>{s.name}</td><td>{s.metrics?.scored_query_count ?? 0}/{s.metrics?.query_count ?? '—'}</td>{[s.metrics?.at_k[k]?.Precision,s.metrics?.at_k[k]?.Recall,s.metrics?.MRR,s.metrics?.MAP,s.metrics?.at_k[k]?.NDCG,s.metrics?.at_k[k]?.HitRate,s.metrics?.at_k[k]?.judged_fraction].map((v,i)=><td key={i}>{scoreText(v)}</td>)}</tr>)}</tbody></table></div>
  </section>;
}

function point(index: number, value: number) { const angle=index*Math.PI/3-Math.PI/2;return [225+Math.cos(angle)*82*value,126+Math.sin(angle)*82*value]; }
function Chart({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <figure className="bench-chart"><figcaption>{title}<small>{description}</small></figcaption><svg viewBox="0 0 450 250" role="img" aria-label={title}>{children}</svg></figure>;
}
