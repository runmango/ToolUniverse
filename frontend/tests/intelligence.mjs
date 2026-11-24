// All mutations and human labels use an isolated TEST ONLY database.
import {chromium} from 'playwright-core';
import {readdir,mkdir,readFile,writeFile} from 'node:fs/promises';
import {spawn,execFile} from 'node:child_process';
import {createServer} from 'node:net';
import path from 'node:path';
import assert from 'node:assert/strict';
const project=path.resolve('..'),output=path.join(project,'.tooluniverse/runtime/workbench/screenshots');
await mkdir(output,{recursive:true});const probe=createServer();await new Promise(r=>probe.listen(0,'127.0.0.1',r));const port=probe.address().port;await new Promise(r=>probe.close(r));
const base=`http://127.0.0.1:${port}`,pause=ms=>new Promise(r=>setTimeout(r,ms));
const child=spawn(path.join(project,'.venv/Scripts/python.exe'),['local/workbench/intelligence_qa_server.py','--port',String(port)],{cwd:project,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1'}});
let log='',browser,page;child.stdout.on('data',v=>log+=v);child.stderr.on('data',v=>log+=v);const errors=[],checks=[];
try {
  let ready=false;for(let i=0;i<100;i++){if(child.exitCode!==null)throw Error(log);try{if((await fetch(base+'/api/health')).ok){ready=true;break;}}catch{}await pause(200);}assert.ok(ready,log);
  const root=path.join(process.env.LOCALAPPDATA,'ms-playwright'),version=(await readdir(root)).filter(n=>/^chromium-\d+$/.test(n)).sort().at(-1);
  browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||path.join(root,version,'chrome-win64/chrome.exe'),headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1050},acceptDownloads:true});page.on('pageerror',e=>errors.push(e.message));await page.goto(base);
  await page.getByRole('button',{name:'证据智能分析',exact:true}).click();await page.getByLabel('证据抽取方式').selectOption('rules');
  await page.getByLabel('实时检索医学来源').uncheck();await page.getByRole('button',{name:'解析 PICO',exact:true}).click();
  await page.getByRole('status').filter({hasText:'PICO 已按问题原文解析'}).waitFor();assert.equal(await page.locator('.ei-pico article').filter({hasText:'Not Reported'}).count(),2);
  checks.push('PICO 未指定对照与结局保持 Not Reported');
  await page.getByLabel('证据论文上限').fill('2');await page.getByLabel('证据检索式').fill('MDS venetoclax');
  await page.getByRole('button',{name:'检索并分析证据',exact:true}).click();
  await page.locator('p').filter({hasText:'分析完成 · 待人工核验'}).first().waitFor();await page.locator('.ei-fields article').first().waitFor();
  const list=await(await fetch(base+'/api/v1/evidence-intelligence/analyses')).json();const id=list.analyses[0].id;
  const result=await(await fetch(base+'/api/v1/evidence-intelligence/analyses/'+id)).json();assert.equal(result.extractions.length,2);
  assert.ok(result.current_comparisons.some(c=>c.status==='direction_difference'));await page.getByText('方向差异，研究条件需核验',{exact:false}).first().waitFor();
  checks.push('真实执行规则提取、研究设计分布、结局与研究差异比较（TEST ONLY 语料）');
  await page.getByRole('button',{name:'定位原始证据',exact:true}).first().click();await page.locator('#ei-source mark').waitFor();
  assert.ok((await page.locator('#ei-source mark').innerText()).includes('survival'));
  await page.locator('#ei-source').screenshot({path:path.join(output,'intelligence-TEST-ONLY-source.png')});checks.push('结论点击定位原始摘要，明确非全文');
  const exportButton=page.getByRole('button',{name:'导出完整 JSON',exact:true});await exportButton.scrollIntoViewIfNeeded();
  const [file]=await Promise.all([page.waitForEvent('download'),exportButton.click()]);const exported=JSON.parse(await readFile(await file.path(),'utf8'));
  assert.equal(exported.release.id,result.payload.release_id);assert.equal(exported.extractions[0].payload.source_scope,'abstract');checks.push('报告导出含固定图谱、原始摘要、数值与生成版本');
  const rct=result.extractions.find(r=>r.payload.design.type==='randomized_controlled_trial');await page.getByLabel('选择证据论文').selectOption(rct.id);
  await page.getByLabel('证据审核人').fill('TEST ONLY UI reviewer');await page.getByLabel('证据审核依据').fill('TEST fixture matched source text');await page.getByLabel('确认人工证据审核').check();
  await page.getByLabel('证据审核决定').selectOption('accepted');await page.getByRole('button',{name:'保存抽取审核',exact:true}).click();await page.getByRole('status').filter({hasText:'审核已追加保存'}).waitFor();
  await page.getByLabel('证据质量工具').selectOption('rob2_2019');await page.getByLabel('质量评价目标').fill('TEST ONLY OS');
  await page.getByLabel('质量评价判断').fill(JSON.stringify({randomization:{judgment:'no_information',reason:'TEST abstract lacks concealment details'}}));await page.getByLabel('确认人工证据审核').check();
  await page.getByRole('button',{name:'保存质量评价记录',exact:true}).click();await page.getByRole('status').filter({hasText:'人工质量评价已按工具版本保存'}).waitFor();
  checks.push('抽取审核追加留痕；RoB 2 部分人工评价不产生总分');
  await page.getByLabel('多论文结论').fill('TEST ONLY study conclusions differ; not clinical evidence');
  await page.getByLabel('多论文引用').fill(JSON.stringify(result.extractions.map((r,i)=>({extraction_id:r.id,quote:r.payload.findings[0].statement,relationship:i===0?'supports':'contradicts'}))));
  await page.getByLabel('确认人工证据审核').check();await page.getByRole('button',{name:'保存人工证据结论',exact:true}).click();await page.getByRole('status').filter({hasText:'多来源结论及支持/矛盾关系已保存'}).waitFor();checks.push('多论文结论保存支持与矛盾关系及逐篇原文');
  const evaluation=await fetch(base+'/api/v1/benchmark/evidence-extraction-evaluations',{method:'POST',headers:{'X-Workbench-Client':'1','Content-Type':'application/json'},body:JSON.stringify({analysis_id:id,actor:'TEST ONLY evaluator',confirm_human_review:true,labels:[{extraction_id:rct.id,field:'limitations',expected:null,reason:'TEST source does not report limitations'}]})});assert.equal(evaluation.status,201);assert.equal((await evaluation.json()).metrics.FieldExactMatch,1);checks.push('Benchmark 独立人工字段 Gold 评测可追溯');
  await page.setViewportSize({width:390,height:844});await page.locator('.bench-heading').scrollIntoViewIfNeeded();await page.waitForFunction(()=>document.body.scrollWidth<=innerWidth && document.querySelector('.sidebar').getBoundingClientRect().right<=0);await page.screenshot({path:path.join(output,'intelligence-TEST-ONLY-mobile.png')});
  await page.getByLabel('打开导航').click();await page.getByRole('button',{name:'科研对话',exact:true}).click();await page.getByRole('textbox',{name:'输入研究问题'}).waitFor();assert.deepEqual(errors,[]);checks.push('手机布局与原科研对话入口，无 JavaScript 错误');
  if(process.env.EVIDENCE_REAL_ANALYSIS_ID){
    // Optional production verification is strictly read-only.
    await page.setViewportSize({width:1440,height:1050});await page.goto(process.env.WORKBENCH_URL||'http://127.0.0.1:8090');
    await page.getByRole('button',{name:'证据智能分析',exact:true}).click();await page.getByLabel('历史证据分析').selectOption(process.env.EVIDENCE_REAL_ANALYSIS_ID);
    await page.locator('.ei-fields article').first().waitFor();await page.getByRole('button',{name:'定位原始证据',exact:true}).first().click();await page.locator('#ei-source mark').waitFor();
    await page.locator('#ei-source').screenshot({path:path.join(output,'intelligence-real-source.png')});
    await page.locator('.ei-graph').screenshot({path:path.join(output,'intelligence-real-graph.png')});
    assert.deepEqual(errors,[]);checks.push('只读验证正式库真实论文摘要与知识图谱支持网络');
  }
  await writeFile(path.join(output,'intelligence-browser-results.json'),JSON.stringify({passed:checks.length,checks,errors,fixture_isolation:'temporary TEST ONLY database; no production Gold'},null,2));console.log(JSON.stringify({passed:checks.length,checks,errors},null,2));
}catch(error){if(page)await page.screenshot({path:path.join(output,'intelligence-test-failure.png')}).catch(()=>{});console.error(log.slice(-2000));throw error;
}finally{if(browser)await browser.close();await fetch(base+'/api/test/shutdown',{method:'POST',headers:{'X-Workbench-Client':'1'}}).catch(()=>{});for(let i=0;i<30&&child.exitCode===null;i++)await pause(100);if(child.exitCode===null)await new Promise(r=>execFile('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true},()=>r()));}
