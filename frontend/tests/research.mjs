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
const child=spawn(path.join(project,'.venv/Scripts/python.exe'),['local/workbench/research_qa_server.py','--port',String(port)],{cwd:project,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1'}});
let log='';child.stdout.on('data',x=>log+=x);child.stderr.on('data',x=>log+=x);
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const checks=[],errors=[];let browser,page;
try {
  let ready=false;
  for(let i=0;i<100;i++){if(child.exitCode!==null)throw Error(log);try{const r=await fetch(base+'/api/health');if(r.ok){ready=true;break;}}catch{}await pause(200);}
  assert.ok(ready,'isolated research server did not start: '+log);
  const root=path.join(process.env.LOCALAPPDATA,'ms-playwright');
  const version=(await readdir(root)).filter(n=>/^chromium-\d+$/.test(n)).sort().at(-1);
  browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||path.join(root,version,'chrome-win64/chrome.exe'),headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1050},acceptDownloads:true});
  page.on('pageerror',e=>errors.push(e.message));await page.goto(base);
  await page.getByRole('button',{name:'科研证据链',exact:true}).click();
  const list=await(await fetch(base+'/api/v1/research/releases')).json();
  const release=await(await fetch(base+'/api/v1/research/releases/'+list.releases[0].id)).json();
  const start=release.payload.nodes.find(n=>n.properties.domain==='MDS');
  const pub=release.payload.nodes.find(n=>n.entity_type==='Publication'&&n.identifiers.some(i=>i.scheme==='PMID'&&i.value==='99999991'));
  await page.getByLabel('证据链起点').selectOption(start.id);await page.getByLabel('证据链终点').selectOption(pub.id);
  await page.getByLabel('证据查询说明').fill('TEST ONLY MDS evidence chain');
  await page.getByRole('button',{name:'构建并保存证据链',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.research-chain>li').length>0);
  const chains=await(await fetch(base+'/api/v1/evidence-chains')).json();
  const chain=await(await fetch(base+'/api/v1/evidence-chains/'+chains.chains[0].id)).json();
  assert.ok(chain.payload.steps.length);assert.equal(chain.release_id,release.id);
  await page.locator('.research-chain').screenshot({path:path.join(output,'research-TEST-ONLY-chain.png')});
  checks.push('临时库真实关系组装，引用同一实体及关系版本');
  const ev=page.waitForEvent('download');await page.getByRole('button',{name:'导出证据与完整来源快照',exact:true}).click();
  const file=await ev;const exported=JSON.parse(await readFile(await file.path(),'utf8'));
  assert.equal(exported.release.id,release.id);assert.ok(Object.keys(exported.release.payload.sources).length);
  checks.push('证据链导出包含冻结来源快照');
  const edge=release.payload.edges.find(e=>e.relation_type==='HAS_TRIAL');
  await page.getByLabel('待审核关系').selectOption(edge.id);
  await page.getByLabel('关系审核人').fill('TEST ONLY UI reviewer');await page.getByLabel('关系审核依据').fill('TEST ONLY fixture review');
  await page.getByLabel('我已人工审核来源和关系语义').check();await page.getByRole('button',{name:'追加审核记录',exact:true}).click();
  await page.getByRole('status').filter({hasText:'审核已追加保存'}).waitFor();
  const reviews=await(await fetch(base+'/api/v1/research/reviews?object_id='+edge.id)).json();assert.equal(reviews.reviews.length,1);
  await page.getByRole('button',{name:'冻结当前图谱',exact:true}).click();await page.getByRole('status').filter({hasText:'已冻结当前实体'}).waitFor();
  const old=await(await fetch(base+'/api/v1/research/releases/'+release.id)).json();assert.equal(old.payload.reviews.length,0);
  checks.push('人工审核追加留痕，新冻结版本不改旧证据链');
  await page.getByRole('button',{name:'检索评测 Benchmark',exact:true}).click();
  await page.getByRole('button',{name:'图谱与证据链评测',exact:true}).click();
  await page.getByLabel('证据查询说明').fill('MDS');
  await page.getByLabel('科研评测审核人').fill('TEST ONLY UI reviewer');
  await page.getByLabel('图谱 Gold JSON').fill(JSON.stringify({entity_ids:[start.id],relation_ids:[],paths:[]}));
  await page.getByLabel('这些判断由我人工核验，未使用 AI 候选冒充 Gold').check();
  await page.getByRole('button',{name:'执行并保存独立评价',exact:true}).click();await page.getByRole('status').filter({hasText:'评价已保存'}).waitFor();
  let evaluations=await(await fetch(base+'/api/v1/benchmark/research-evaluations')).json();
  assert.equal(evaluations.evaluations[0].payload.metrics.EntityRecall,1);
  checks.push('图谱检索与人工实体 Gold 独立评价');
  await page.getByLabel('科研评测任务').selectOption('evidence_chain');await page.getByLabel('查看证据链').selectOption(chain.id);
  await page.getByLabel('证据链判断 JSON').fill(JSON.stringify([{relation_id:chain.payload.steps[0].relation_id,citation_exists:true,supports_statement:false,reason:'TEST ONLY unsupported fixture'}]));
  await page.getByLabel('这些判断由我人工核验，未使用 AI 候选冒充 Gold').check();const assessmentSaved=page.waitForResponse(r=>r.url().endsWith('/research-evaluations')&&r.request().method()==='POST'&&r.status()===201);await page.getByRole('button',{name:'执行并保存独立评价',exact:true}).click();await assessmentSaved;
  await page.waitForFunction(()=>document.querySelectorAll('.research-json').length>=2);
  evaluations=await(await fetch(base+'/api/v1/benchmark/research-evaluations')).json();
  const assessment=evaluations.evaluations.find(e=>e.task_type==='evidence_chain');assert.ok(assessment);
  assert.equal(assessment.payload.metrics.CitationAccuracy.value,1);assert.equal(assessment.payload.metrics.EvidenceSupportRate.value,0);
  checks.push('引用真实性与结论支持性分开计算');
  await page.getByRole('button',{name:'加入文献检索对照',exact:true}).click();await page.getByRole('status').filter({hasText:'已保存图谱检索配置'}).waitFor();
  const models=await(await fetch(base+'/api/benchmark/models')).json();assert.ok(models.models.find(m=>m.method==='graph'));
  checks.push('固定图谱版本可加入 Benchmark 算法对照');
  await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=0);
  assert.ok(await page.evaluate(()=>document.body.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(output,'research-TEST-ONLY-mobile.png')});
  await page.setViewportSize({width:1440,height:1050});
  await page.getByRole('button',{name:'科研对话',exact:true}).click();await page.getByRole('textbox',{name:'输入研究问题'}).waitFor();
  await page.getByRole('button',{name:'连接设置',exact:true}).click();await page.getByRole('button',{name:'保存并连接',exact:true}).waitFor();
  await page.getByText('知识库',{exact:true}).waitFor();
  checks.push('手机布局、科研对话、连接设置、知识库展示保留');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(output,'research-browser-results.json'),JSON.stringify({passed:checks.length,checks,errors,fixture_isolation:'temporary database; no production review or Gold written'},null,2));
  console.log(JSON.stringify({passed:checks.length,checks,errors},null,2));
}catch(error){
  if(page)await page.screenshot({path:path.join(output,'research-test-failure.png')}).catch(()=>{});
  console.error(log.slice(-3000));throw error;
}finally{
  if(browser)await browser.close();
  await fetch(base+'/api/test/shutdown',{method:'POST',headers:{'X-Workbench-Client':'1'}}).catch(()=>{});
  for(let i=0;i<30&&child.exitCode===null;i++)await pause(100);
  if(child.exitCode===null){if(process.platform==='win32')await new Promise(resolve=>execFile('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true},()=>resolve()));else child.kill('SIGTERM');}
}
