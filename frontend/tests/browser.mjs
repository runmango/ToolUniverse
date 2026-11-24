import { chromium } from 'playwright-core';
import { readdir, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const base = process.env.WORKBENCH_URL || 'http://127.0.0.1:8090';
const output = path.resolve('../.tooluniverse/runtime/workbench/screenshots');
await mkdir(output, { recursive: true });
const browserRoot = path.join(process.env.LOCALAPPDATA, 'ms-playwright');
const chromiumDirectory = (await readdir(browserRoot)).filter(name => /^chromium-\d+$/.test(name)).sort().at(-1);
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || path.join(browserRoot, chromiumDirectory, 'chrome-win64', 'chrome.exe');
const browser = await chromium.launch({ executablePath, headless: true });
const results = [];
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: /让好问题/ }).waitFor();
  await page.locator('.profile-button').getByText('账号', { exact: true }).waitFor();
  assert.equal(await page.locator('body').evaluate(el => el.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(output, 'home-desktop.png'), fullPage: true });
  results.push('desktop welcome, live status, no overflow');

  await page.getByRole('button', { name: /探索蛋白与基因/ }).click();
  await page.getByRole('textbox', { name: '输入研究问题' }).waitFor();
  assert.match(await page.getByRole('textbox', { name: '输入研究问题' }).inputValue(), /P05067/);
  await page.getByRole('button', { name: '发送研究问题' }).click();
  await page.getByRole('heading', { name: '连接你的研究伙伴' }).waitFor();
  await page.getByRole('button', { name: '使用账号登录' }).waitFor();
  await page.screenshot({ path: path.join(output, 'connections-desktop.png'), fullPage: true });
  results.push('suggested prompt, unauthenticated chat redirects to connection settings');

  await page.getByRole('button', { name: /^科研对话/ }).click();
  await page.getByRole('button', { name: '打开MCP工具', exact: true }).click();
  const search = page.getByRole('textbox', { name: '搜索科研工具' });
  await search.fill('UniProt_get_entry_by_accession');
  await page.locator('.tool-card').filter({ hasText: 'UniProt get entry by accession' }).first().click();
  await page.getByRole('heading', { name: 'UniProt_get_entry_by_accession' }).waitFor();
  await page.waitForFunction(() => document.querySelector('#tool-arguments')?.value.includes('P05067'));
  await page.getByRole('textbox', { name: '运行参数 JSON' }).fill('{broken');
  await page.getByRole('button', { name: '运行工具', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: '有效的 JSON' }).waitFor();
  await page.locator('#tool-arguments').fill(JSON.stringify({ accession: 'P05067', include_publications: false }, null, 2));
  await page.getByRole('button', { name: '运行工具', exact: true }).click();
  await page.locator('.tool-result').getByText(/查询完成/).waitFor({ timeout: 90000 });
  assert.match(await page.locator('.tool-result pre').innerText(), /P05067/);
  await page.screenshot({ path: path.join(output, 'tool-query-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: '关闭工具详情' }).click();
  results.push('real tool catalog, schema, invalid JSON, successful live UniProt query');

  await search.fill('this_tool_does_not_exist_9z8y7x');
  await page.getByRole('heading', { name: '没有找到匹配的工具' }).waitFor();
  await page.getByRole('button', { name: '清空工具搜索' }).click();
  await page.locator('.tool-card').first().waitFor();
  await page.screenshot({ path: path.join(output, 'tools-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: '切换深色主题' }).click();
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
  await page.getByRole('button', { name: /开启新对话/ }).click();
  await page.screenshot({ path: path.join(output, 'home-dark.png'), fullPage: true });
  await page.getByRole('button', { name: '切换浅色主题' }).click();
  results.push('empty search state, theme toggle');

  const response = await context.request.post(`${base}/api/sessions`, { headers: { 'X-Workbench-Client': '1' }, data: { title: '浏览器验证会话' } });
  assert.equal(response.ok(), true);
  const session = await response.json();
  try {
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '浏览器验证会话', exact: true }).click();
    await page.getByRole('button', { name: '管理会话 浏览器验证会话' }).click();
    await page.getByRole('button', { name: '重命名', exact: true }).click();
    await page.getByRole('textbox', { name: '会话名称' }).fill('已验证历史会话');
    await page.getByRole('button', { name: '保存名称' }).click();
    await page.getByRole('button', { name: '已验证历史会话', exact: true }).waitFor();
    await page.getByRole('button', { name: '管理会话 已验证历史会话' }).click();
    await page.getByRole('button', { name: '删除会话', exact: true }).click();
    await page.getByRole('button', { name: '删除记录', exact: true }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    results.push('persistent session create, rename, delete');
  } finally { await context.request.delete(`${base}/api/sessions/${session.id}`, { headers: { 'X-Workbench-Client': '1' } }); }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.locator('body').evaluate(el => el.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(output, 'home-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: '打开导航' }).click();
  await page.getByRole('button', { name: /^连接设置/ }).click();
  await page.getByRole('heading', { name: '连接你的研究伙伴' }).waitFor();
  assert.equal(await page.locator('body').evaluate(el => el.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(output, 'connections-mobile.png'), fullPage: true });
  results.push('mobile navigation, settings, no horizontal overflow');
  assert.deepEqual(pageErrors, []);
  await writeFile(path.join(output, 'browser-results.json'), JSON.stringify({ passed: true, checks: results, pageErrors }, null, 2));
  console.log(JSON.stringify({ passed: true, checks: results }, null, 2));
} finally { await browser.close(); }
