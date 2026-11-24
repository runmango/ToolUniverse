// Mutating UI tests use a separate temporary database labelled TEST ONLY.
// Production verification is read-only and never creates clinical Gold.
import { chromium } from 'playwright-core';
import { readdir, mkdir, readFile, writeFile } from 'node:fs/promises';
import { spawn, execFile } from 'node:child_process';
import { createServer } from 'node:net';
import path from 'node:path';
import assert from 'node:assert/strict';

const project=path.resolve('..');
const output=path.join(project,'.tooluniverse/runtime/workbench/screenshots');
await mkdir(output,{recursive:true});
const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
const base=`http://127.0.0.1:${port}`;
const child=spawn(path.join(project,'.venv/Scripts/python.exe'),['local/workbench/benchmark_qa_server.py','--port',String(port)],{cwd:project,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1'}});
let log='';child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const checks=[],errors=[];let browser,page;
try {
  let ready=false;
  for(let i=0;i<100;i++){if(child.exitCode!==null)throw Error(log);try{const r=await fetch(base+'/api/health');if(r.ok){ready=true;break;}}catch{}await pause(200);}
  assert.ok(ready,'isolated UI server did not start: '+log);
  const root=path.join(process.env.LOCALAPPDATA,'ms-playwright');
  const version=(await readdir(root)).filter(n=>/^chromium-\d+$/.test(n)).sort().at(-1);
  browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||path.join(root,version,'chrome-win64/chrome.exe'),headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1050},acceptDownloads:true});
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);
  await page.getByRole('button',{name:'检索评测 Benchmark',exact:true}).click();
  const datasets=await(await fetch(base+'/api/benchmark/datasets')).json();
  const fixture=datasets.datasets.find(d=>d.name.startsWith('TEST ONLY'));
  await page.getByLabel('Benchmark 数据集').selectOption(fixture.id);
  await page.getByRole('button',{name:'数据集与 Gold',exact:true}).click();
  await page.getByLabel('选择标注 Query').selectOption('TEST_001');
  await page.getByLabel('相关性 TEST_A').waitFor();
  assert.equal(await page.getByLabel('相关性 TEST_A').inputValue(),'');
  checks.push('独立 TEST 数据集与未标注初始状态');

  await page.locator('summary').filter({hasText:'导入 JSONL'}).click();
  await page.getByLabel('JSONL 导入文本').fill('{broken');
  await page.getByRole('button',{name:'导入并创建新版本',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'第1行'}).waitFor();
  await page.getByLabel('关闭评测错误').click();
  const ai={query_id:'TEST_AI',query:'TEST other population',domain:'MDS',annotation_source:'ai',verification_status:'verified',relevant_documents:[{document_id:'TEST_A',relevance:3}]};
  await page.getByLabel('JSONL 导入文本').fill(JSON.stringify(ai));
  const imported=page.waitForResponse(r=>r.url().endsWith('/import')&&r.ok());
  await page.getByRole('button',{name:'导入并创建新版本',exact:true}).click();await imported;
  await page.getByLabel('选择标注 Query').selectOption('TEST_AI');
  await page.getByText('AI 候选',{exact:true}).waitFor();
  const afterImport=await(await fetch(base+'/api/benchmark/datasets/'+fixture.id)).json();
  assert.equal(afterImport.snapshot.payload.queries.find(q=>q.query_id==='TEST_AI').relevant_documents[0].verification_status,'candidate');
  checks.push('JSONL 错误提示；AI verified 字样不能成为 Gold');

  await page.getByRole('button',{name:'新增 Query',exact:true}).click();
  await page.getByLabel('Query ID',{exact:true}).fill('TEST_NEW');
  await page.getByLabel('Benchmark 研究问题').fill('TEST new medical question');
  await page.getByLabel('Gold 审核人').fill('TEST ONLY UI automation');
  await page.getByLabel('我已人工审核本次修改').check();
  const savedNew=page.waitForResponse(r=>r.url().endsWith('/queries')&&r.ok());
  await page.getByRole('button',{name:'保存 Gold 与数据版本',exact:true}).click();await savedNew;
  await page.getByLabel('选择标注 Query').selectOption('TEST_001');
  for(const [id,grade] of [['TEST_A','3'],['TEST_B','0'],['TEST_C','0']])await page.getByLabel('相关性 '+id,{exact:true}).selectOption(grade);
  await page.getByLabel('Gold 审核人').fill('TEST ONLY UI automation');
  await page.getByLabel('我已人工审核本次修改').check();
  await page.getByLabel('已审核当前候选范围的全部文献',{exact:false}).check();
  const savedGold=page.waitForResponse(r=>r.url().endsWith('/queries')&&r.ok());
  await page.getByRole('button',{name:'保存 Gold 与数据版本',exact:true}).click();await savedGold;
  await page.getByText('人工 Gold',{exact:true}).first().waitFor();
  checks.push('新增问题、逐条人工确认、Gold 版本保存');

  await page.getByRole('button',{name:'模型配置',exact:true}).click();
  await page.getByLabel('模型配置 JSON').fill(JSON.stringify({name:'TEST ONLY BM25 v2',version:'test-v2',method:'bm25',config:{enabled:true,bm25:{k1:1.2,b:.65}}}));
  await page.getByRole('button',{name:'保存为新模型版本',exact:true}).click();
  await page.getByRole('status').filter({hasText:'已保存 TEST ONLY BM25 v2'}).waitFor();
  await page.getByRole('button',{name:'实验与仪表盘',exact:true}).click();
  await page.locator('.bench-model-select label').filter({hasText:'TEST ONLY BM25 v2'}).getByRole('checkbox').check();
  await page.getByRole('button',{name:'运行对照实验',exact:true}).click();
  await page.getByText('评价完成',{exact:true}).first().waitFor();
  await page.waitForFunction(()=>document.querySelectorAll('.bench-chart').length===4);
  const all=await(await fetch(base+'/api/benchmark/experiments?dataset_id='+fixture.id)).json();
  assert.equal(all.experiments.length,2);
  assert.ok(all.experiments.every(e=>e.metrics.scored_query_count===1&&e.metrics.MRR===1));
  await page.locator('.bench-chart-section').screenshot({path:path.join(output,'benchmark-TEST-ONLY-charts.png')});
  checks.push('模型版本另存；真实 BM25 在隔离夹具上评分；四种图表');

  await page.getByRole('button',{name:'模型结果对比',exact:true}).click();
  await page.getByLabel('选择对比 Query').selectOption('TEST_001');
  await page.waitForFunction(()=>document.querySelectorAll('.bench-result-column').length===2&&document.querySelector('.bench-result-column .bench-rank')?.textContent.includes('TEST_A'));
  assert.equal(await page.locator('.bench-result-column').count(),2);
  await page.locator('.bench-comparison').screenshot({path:path.join(output,'benchmark-TEST-ONLY-comparison.png')});
  const downloadEvent=page.waitForEvent('download');
  await page.getByLabel('导出报告 '+all.experiments[0].id,{exact:true}).selectOption('json');
  const file=await downloadEvent;const exported=JSON.parse(await readFile(await file.path(),'utf8'));
  assert.equal(exported.dataset_snapshot.payload.documents[0].document_id,'TEST_A');
  assert.equal(exported.experiment.metrics.MRR,1);
  await page.getByRole('button',{name:'指标重算',exact:true}).first().click();
  await page.getByRole('status').filter({hasText:'重算结果一致'}).waitFor();
  checks.push('单 Query 排名对照、完整报告下载、保存排名精确重算');

  await page.getByLabel('医学评价实验').selectOption(all.experiments[0].id);
  await page.getByLabel('医学生成评价 JSON').fill(JSON.stringify({citations:[{citation_id:'c1',document_id:'TEST_A',exists:true,verification_reference:'TEST ONLY reviewer fixture'}],claims:[{claim_id:'claim1',claim_text:'TEST ONLY assertion',citation_ids:['c1'],supported:true,evidence_document_id:'TEST_A',evidence_quote:'TEST fixture evidence statement.'}]}));
  await page.getByLabel('医学评价审核人').fill('TEST ONLY UI automation');
  await page.getByLabel('我已人工核验这些判断').check();
  await page.getByRole('button',{name:'保存独立医学评价',exact:true}).click();
  await page.getByText('EvidenceSupportRate',{exact:true}).waitFor();
  checks.push('生成质量独立标注及证据片段验证');

  await page.getByRole('button',{name:'负样本审核',exact:true}).click();
  await page.getByLabel('负样本 Query').selectOption('TEST_AI');
  await page.getByLabel('负样本方法').selectOption('in_batch');
  await page.getByRole('button',{name:'生成待审核候选',exact:true}).click();
  await page.getByText('待审核',{exact:true}).waitFor();
  await page.getByLabel('负样本审核人').fill('TEST ONLY UI automation');
  await page.getByLabel('困难负样本类别').selectOption('different_population');
  await page.getByLabel('负样本审核依据').fill('TEST ONLY disjoint fixture population');
  await page.getByRole('button',{name:'确认不相关',exact:true}).click();
  await page.getByText('已接受为负例',{exact:true}).waitFor();
  checks.push('批内候选不自动标负；人工审核后写入新版本');

  await page.getByLabel('切换深色主题').click();
  assert.equal(await page.evaluate(()=>document.body.scrollWidth<=innerWidth),true);
  await page.getByLabel('切换浅色主题').click();
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=0);
  await page.locator('.bench-heading').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.body.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(output,'benchmark-TEST-ONLY-mobile.png')});
  checks.push('深色主题、手机布局无横向溢出');

  // Read-only real-data smoke: no annotation writes and no model chat calls.
  const production=process.env.WORKBENCH_URL||'http://127.0.0.1:8090';
  await page.setViewportSize({width:1440,height:1050});await page.goto(production);
  await page.getByRole('button',{name:'检索评测 Benchmark',exact:true}).click();
  const real=await(await fetch(production+'/api/benchmark/experiments')).json();
  // Select one completed comparison group; latest individual runs may use different datasets.
  const groups=[...new Set(real.experiments.map(e=>e.group_id))].map(group=>real.experiments.filter(e=>e.group_id===group&&e.status.startsWith('completed')));
  const choices=groups.find(group=>['bm25','embedding','hybrid'].every(method=>group.some(e=>e.retrieval_method===method)));
  assert.ok(choices,'Run a completed BM25 / Embedding / Hybrid comparison group before this test');
  await page.getByLabel('Benchmark 数据集').selectOption(choices[0].dataset_id);
  for(const e of choices)await page.getByLabel('对比实验 '+e.id,{exact:true}).check();
  await page.getByRole('button',{name:'模型结果对比',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.bench-result-column article').length>0);
  await page.locator('.bench-comparison').screenshot({path:path.join(output,'benchmark-real-comparison.png')});
  assert.match(await page.locator('.bench-comparison').innerText(),/PMID:/);
  await page.getByRole('button',{name:'科研对话',exact:true}).click();
  await page.getByRole('textbox',{name:'输入研究问题'}).waitFor();
  assert.deepEqual(errors,[]);
  checks.push('正式库同组真实 '+choices.map(e=>e.retrieval_method).join(' / ')+' 排名；返回科研对话；无 JS 错误');
  await writeFile(path.join(output,'benchmark-browser-results.json'),JSON.stringify({passed:checks.length,checks,errors,fixture_isolation:'temporary database; no clinical Gold written'},null,2));
  console.log(JSON.stringify({passed:checks.length,checks,errors},null,2));
}catch(error){
  if(page)await page.screenshot({path:path.join(output,'benchmark-test-failure.png')}).catch(()=>{});
  console.error(log.slice(-3000));throw error;
}finally{
  if(browser)await browser.close();
  await fetch(base+'/api/test/shutdown',{method:'POST',headers:{'X-Workbench-Client':'1'}}).catch(()=>{});
  for(let i=0;i<30&&child.exitCode===null;i++)await pause(100);
  if(child.exitCode===null){if(process.platform==='win32')await new Promise(resolve=>execFile('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true},()=>resolve()));else child.kill('SIGTERM');}
}
