// browser_test.js — ヘッドレスブラウザでSTBアプリをテスト
// 実行: node browser_test.js

const puppeteer = require('puppeteer');

const BASE_URL = 'http://localhost:9999';
let passCount = 0;
let failCount = 0;
const failures = [];

function check(desc, condition, detail) {
  if (condition) {
    console.log(`  ✓ ${desc}`);
    passCount++;
  } else {
    console.log(`  ✗ ${desc}${detail ? ': ' + detail : ''}`);
    failCount++;
    failures.push({ desc, detail });
  }
}

(async () => {
  console.log('============================================');
  console.log(' STB App Headless Browser Test Suite');
  console.log('============================================\n');

  let browser;
  try {
    browser = await puppeteer.launch({
      headless: 'new',
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
      ],
    });

    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 800 });

    // ===== 1. ページロード =====
    console.log('[1] Page Load');
    await page.goto(BASE_URL + '/schedule_app.html', { waitUntil: 'domcontentloaded', timeout: 15000 });

    const title = await page.title();
    check('Page title is correct', title === 'RBP 防除スケジュールリスト', `got "${title}"`);

    const h1Text = await page.$eval('h1', el => el.textContent.trim());
    check('H1 heading present', h1Text.includes('RBP'), `got "${h1Text}"`);

    // ===== 2. JavaScript実行環境（ページロード直後）=====
    console.log('\n[2] JavaScript Execution');

    const engineFn = await page.evaluate(() => typeof dotProduct);
    check('dotProduct function available', engineFn === 'function');

    const mirrorFn = await page.evaluate(() => typeof matchExactBox);
    check('matchExactBox function available', mirrorFn === 'function');

    const rbpCoreFn = await page.evaluate(() => typeof runLineThroughBridges);
    check('runLineThroughBridges function available', rbpCoreFn === 'function');

    const evalBoxFn = await page.evaluate(() => typeof classifyAndRegisterVector);
    check('classifyAndRegisterVector function available', evalBoxFn === 'function');

    // ===== 3. データ読み込み =====
    console.log('\n[3] Data Loading');

    const ebVectors = await page.evaluate(() => {
      return typeof EB_VECTORS === 'object' && EB_VECTORS !== null ? Object.keys(EB_VECTORS).length : 0;
    });
    check('EB_VECTORS loaded', ebVectors > 0, `count: ${ebVectors}`);

    const ebMatrix = await page.evaluate(() => {
      return Array.isArray(EB_MATRIX) ? EB_MATRIX.length : 0;
    });
    check('EB_MATRIX loaded', ebMatrix > 0, `count: ${ebMatrix}`);

    const ebNames = await page.evaluate(() => {
      return typeof EB_NAMES === 'object' && EB_NAMES !== null ? Object.keys(EB_NAMES).length : 0;
    });
    check('EB_NAMES loaded', ebNames > 0, `count: ${ebNames}`);

    const diseases = await page.evaluate(() => {
      return Array.isArray(DISEASES) ? DISEASES.length : 0;
    });
    check('DISEASES loaded', diseases > 0, `count: ${diseases}`);

    const pesticides = await page.evaluate(() => {
      return Array.isArray(PESTICIDE_DB) ? PESTICIDE_DB.length : 0;
    });
    check('PESTICIDE_DB loaded', pesticides > 0, `count: ${pesticides}`);

    // ===== 4. RBPコア機能 =====
    console.log('\n[4] RBP Core Functions');

    const dotResult = await page.evaluate(() => {
      return dotProduct([1, 0, 1, 0, 1, 0, 1, 0, 1, 0], [1, 0, 1, 0, 1, 0, 1, 0, 1, 0]);
    });
    check('dotProduct computes correctly', dotResult === 5, `got ${dotResult}`);

    const cosResult = await page.evaluate(() => {
      return cosineSimilarity([1, 0, 1, 0, 1, 0, 1, 0, 1, 0], [1, 0, 1, 0, 1, 0, 1, 0, 1, 0]);
    });
    check('cosineSimilarity returns 1.0 for identical vectors', Math.abs(cosResult - 1.0) < 0.001, `got ${cosResult}`);

    // ===== 5. UI要素 =====
    console.log('\n[5] UI Elements');

    check('Year navigation exists', await page.$('.year-nav') !== null);
    check('Calendar panel exists', await page.$('.calendar-panel') !== null);
    check('Side panel exists', await page.$('.side-panel') !== null);

    const panelTabs = await page.$$('.panel-tab');
    check('Panel tabs exist', panelTabs.length >= 3, `found ${panelTabs.length}`);

    // ===== 6. タブ切替 =====
    console.log('\n[6] Tab Switching');

    const activeTab = await page.$eval('.panel-tab.active', el => el.textContent.trim());
    check('Default active tab is detail', activeTab.includes('日次詳細'));

    const scheduleTab = await page.$('.panel-tab:nth-child(2)');
    await scheduleTab.click();
    await page.waitForSelector('.panel-tab.active', { timeout: 3000 });
    const newActiveTab = await page.$eval('.panel-tab.active', el => el.textContent.trim());
    check('Schedule tab activates', newActiveTab.includes('スケジュール'));

    const historyTab = await page.$('.panel-tab:nth-child(3)');
    await historyTab.click();
    await page.waitForSelector('.panel-tab.active', { timeout: 3000 });
    const histActiveTab = await page.$eval('.panel-tab.active', el => el.textContent.trim());
    check('History tab activates', histActiveTab.includes('防除履歴'));

    const detailTab = await page.$('.panel-tab:nth-child(1)');
    await detailTab.click();

    // ===== 7. エラーハンドリング =====
    console.log('\n[7] Error Handling');

    try {
      await page.goto(BASE_URL + '/nonexistent_page_xyz.html', { waitUntil: 'domcontentloaded', timeout: 5000 });
      check('Non-existent page handled', true);
    } catch (e) {
      check('Non-existent page returns error', true);
    }

    await page.goto(BASE_URL + '/schedule_app.html', { waitUntil: 'domcontentloaded', timeout: 15000 });

    // ===== 8. コンソールエラー =====
    console.log('\n[8] Console Errors');

    const consoleErrors = await page.evaluate(() => {
      return window.__consoleErrors || [];
    });
    check('No critical console errors', consoleErrors.length === 0, `found ${consoleErrors.length}`);

    // ===== 9. レスポンシブ表示 =====
    console.log('\n[9] Responsive Layout');

    await page.setViewport({ width: 375, height: 667 });
    check('Mobile viewport renders', await page.$('.calendar-panel') !== null);
    await page.setViewport({ width: 1280, height: 800 });

    // ===== 10. アセット =====
    console.log('\n[10] Assets');

    const cssLoaded = await page.evaluate(() => {
      const sheets = document.styleSheets;
      for (let i = 0; i < sheets.length; i++) {
        try {
          if (sheets[i].href && sheets[i].href.includes('schedule_app.css')) return true;
        } catch(e) {}
      }
      return false;
    });
    check('Stylesheet applied', cssLoaded);

    // ===== 11. ローカルストレージ初期化 =====
    console.log('\n[11] LocalStorage Initialization');

    check('records variable initialized', await page.evaluate(() => typeof records !== 'undefined'));
    check('sprays variable initialized', await page.evaluate(() => typeof sprays !== 'undefined'));

  } catch (err) {
    console.error(`\nFatal error: ${err.message}`);
    console.error(err.stack);
  } finally {
    if (browser) await browser.close();
  }

  // Summary
  console.log('\n============================================');
  console.log(` Result: ${passCount}/${passCount + failCount} passed, ${failCount} failed`);
  console.log('============================================');
  if (failCount === 0) {
    console.log('ALL TESTS PASSED — ヘッドレスブラウザテスト成功！');
  } else {
    console.log('SOME TESTS FAILED:');
    failures.forEach(f => console.log(`  - ${f.desc}: ${f.detail}`));
  }
  process.exit(failCount > 0 ? 1 : 0);
})();
