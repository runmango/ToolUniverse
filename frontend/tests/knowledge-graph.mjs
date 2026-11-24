// Live, read-only browser acceptance test. Import MDS, NCT03682536 and
// PMID40737597 first. Never sends a research-chat message or modifies sessions.
import { chromium } from 'playwright-core';
import { readdir, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const base = process.env.WORKBENCH_URL || 'http://127.0.0.1:8090';
const output = path.resolve('../.tooluniverse/runtime/workbench/screenshots');
await mkdir(output, { recursive: true });
const browserRoot = path.join(process.env.LOCALAPPDATA, 'ms-playwright');
const version = (await readdir(browserRoot)).filter(x => /^chromium-\d+$/.test(x)).sort().at(-1);
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || path.join(browserRoot, version, 'chrome-win64', 'chrome.exe'), headless: true });
const checks = [], errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, acceptDownloads: true });
  page.on('pageerror', error => errors.push(error.message));
  const settled = async () => {
    await page.waitForFunction(() => !document.querySelector('.kg-canvas-loading') && document.querySelector('.kg-canvas')?._cyreg?.cy?.nodes().length);
  };
  const network = () => page.locator('.kg-canvas').evaluate(el => {
    const cy = el._cyreg.cy;
    return { nodes: cy.nodes().map(n => n.data()), edges: cy.edges().map(e => e.data()) };
  });
  const changeGraph = async action => {
    const response = page.waitForResponse(r => r.url().includes('/graph/neighborhood?') && r.ok());
    await action();
    const expected = await (await response).json();
    await page.waitForFunction(ids => {
      const cy = document.querySelector('.kg-canvas')?._cyreg?.cy;
      return !document.querySelector('.kg-canvas-loading') && cy &&
        JSON.stringify(cy.elements().map(e => e.id()).sort()) === JSON.stringify(ids);
    }, [...expected.nodes, ...expected.edges].map(item => item.id).sort());
  };
  await page.goto(base);
  await page.getByRole('button', { name: '医学知识图谱', exact: true }).click();
  await settled();
  assert.ok((await network()).edges.length, '先通过真实 API 同步 MDS，再运行浏览器验收');
  const centerId = (await network()).nodes.find(n => n.isCenter).id;
  await page.getByLabel('搜索中心实体').fill('Myelodysplastic Syndrome');
  await page.waitForResponse(r => r.url().includes('/graph/entities?q=Myelodysplastic') && r.ok());
  await changeGraph(() => page.locator('.kg-search-results button').filter({ hasText: '骨髓增生异常综合征' }).click());
  assert.equal((await network()).nodes.find(n => n.isCenter).id, centerId);
  await changeGraph(() => page.getByLabel('关系展开深度').selectOption('2'));
  assert.ok((await network()).nodes.some(n => n.entityType === 'ClinicalTrial'));
  assert.ok((await network()).nodes.some(n => n.entityType === 'Publication'));
  checks.push('MDS 同义词归一；二度展开到试验、论文');

  await changeGraph(() => page.locator('.kg-filters').getByRole('button', { name: '药物', exact: true }).click());
  assert.ok((await network()).nodes.every(n => n.isCenter || n.entityType === 'Drug'));
  await changeGraph(() => page.locator('.kg-filters').getByRole('button', { name: '全部', exact: true }).click());
  await changeGraph(() => page.getByLabel('显示推断候选').uncheck());
  assert.ok((await network()).edges.every(e => !e.uncertain));
  await changeGraph(() => page.getByLabel('显示推断候选').check());
  checks.push('分类筛选与推断过滤');

  const relationList = page.locator('.kg-node-list').filter({ hasText: '当前关系列表' });
  await relationList.locator('summary').click();
  await relationList.getByRole('button', { name: /登记的(?:结果|衍生)论文/ }).first().click();
  await page.locator('.kg-detail .kg-source a').waitFor();
  assert.match(await page.locator('.kg-detail .kg-source a').getAttribute('href'), /clinicaltrials.gov\/study\/NCT/);
  assert.match(await page.locator('.kg-verification').innerText(), /来源明确记载/);
  assert.match(await page.locator('.kg-detail blockquote').innerText(), /RESULT|DERIVED/);
  await relationList.getByRole('button', { name: /基因关联候选/ }).first().click();
  await page.locator('.kg-verification.uncertain').waitFor();
  checks.push('关系证据原文、原始来源与推断标识');

  // Exercise the canvas tap handler, then expand from that actual node.
  const position = await page.locator('.kg-canvas').evaluate(el => el._cyreg.cy.nodes().filter(n => n.data('entityType') === 'Gene' && n.data('label') === 'TP53')[0].renderedPosition());
  await page.locator('.kg-canvas').click({ position });
  await page.locator('.kg-detail h2').filter({ hasText: /^TP53$/ }).waitFor();
  const before = (await network()).nodes.length;
  await page.getByRole('button', { name: '展开二度关系', exact: true }).click();
  await page.waitForFunction(count => document.querySelector('.kg-canvas')._cyreg.cy.nodes().length > count, before);
  await settled();
  assert.ok((await network()).nodes.some(n => n.entityType === 'Protein'));
  for (const label of ['放大图谱', '缩小图谱', '适应图谱', '定位中心实体']) await page.getByLabel(label).click();
  checks.push('画布节点点击、进一步展开、定位与缩放');
  await page.locator('.kg-workspace').screenshot({ path: path.join(output, 'knowledge-graph-network.png') });

  for (const format of ['JSON', 'CSV']) {
    const event = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出 ' + format, exact: true }).click();
    const file = await event;
    const content = await readFile(await file.path(), 'utf8');
    if (format === 'JSON') {
      const data = JSON.parse(content);
      assert.equal(data.nodes.length, (await network()).nodes.length);
      assert.equal(data.evidence.length, data.edges.length);
      assert.ok(data.evidence.every(e => e.source_record.source_id && e.evidence_text));
    } else {
      assert.ok(content.includes('source_record') && content.includes('relation') && content.includes('entity'));
    }
  }
  checks.push('可见图谱 JSON / CSV 导出，含逐条证据');

  await page.getByLabel('搜索路径目标').fill('Cellular tumor antigen p53');
  await page.getByLabel('选择路径目标').locator('option').filter({ hasText: 'Cellular tumor antigen p53' }).waitFor({ state: 'attached' });
  await page.getByRole('button', { name: '查询路径', exact: true }).click();
  await page.getByRole('status').filter({ hasText: /找到 \d+ 跳连接路径/ }).waitFor();
  const connected = await network();
  assert.ok(connected.edges.length >= 2);
  assert.ok(connected.edges.every(e => !e.uncertain));
  await page.locator('.kg-workspace').screenshot({ path: path.join(output, 'knowledge-graph-path.png') });
  checks.push('默认排除推断的多跳路径');

  await page.getByLabel('切换深色主题').click();
  await page.locator('.kg-workspace').screenshot({ path: path.join(output, 'knowledge-graph-dark.png') });
  assert.equal(await page.evaluate(() => document.body.scrollWidth <= innerWidth), true);
  await page.getByLabel('切换浅色主题').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 0);
  await page.locator('.kg-heading').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.body.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(output, 'knowledge-graph-mobile.png') });
  await page.getByLabel('打开导航').click();
  await page.getByRole('button', { name: '医学知识图谱', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 0);
  checks.push('深色主题与手机宽度无横向溢出');

  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.getByRole('button', { name: '科研对话', exact: true }).click();
  await page.getByRole('textbox', { name: '输入研究问题' }).waitFor();
  assert.deepEqual(errors, []);
  checks.push('返回科研对话；浏览器无未捕获错误');
  await writeFile(path.join(output, 'knowledge-graph-browser-results.json'), JSON.stringify({ checks, errors }, null, 2));
  console.log(JSON.stringify({ passed: checks.length, checks, errors }, null, 2));
} catch (error) {
  for (const context of browser.contexts()) for (const page of context.pages()) {
    await page.screenshot({ path: path.join(output, 'knowledge-graph-failure.png') }).catch(() => {});
    console.error((await page.locator('.kg-detail').innerText().catch(() => '')).slice(-3500));
  }
  throw error;
} finally {
  await browser.close();
}
