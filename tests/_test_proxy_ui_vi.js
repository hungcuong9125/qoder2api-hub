/* Regression tests for proxy UI translation and truthful reachability labels. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'dashboard.html'), 'utf8');
const pack = JSON.parse(fs.readFileSync(path.join(root, 'i18n/vi.json'), 'utf8'));
const start = html.indexOf('/* BEGIN-PURE-I18N-CORE');
const end = html.indexOf('/* END-PURE-I18N-CORE */');
assert(start >= 0 && end > start);
const core = new Function('"use strict";' + html.slice(start, end) + '\nreturn {t, load};')();
core.load(pack);
let count = 0;
function check(label, fn) { fn(); count++; console.log('PASS ' + label); }
check('all static popup labels have Vietnamese translations', () => {
  const popup = html.slice(html.indexOf('<div class="modal-mask" id="accountProxyModal"'), html.indexOf('<div class="modal-mask" id="importModal"'));
  const texts = [...popup.matchAll(/>([^<>]+)</g)].map(m => m[1].trim()).filter(t => /[\u3400-\u9fff]/.test(t));
  assert(texts.length >= 10);
  for (const text of texts) assert(!/[\u3400-\u9fff]/.test(core.t(text)), text);
});
check('proxy result is only IP and area', () => {
  assert(html.includes('showProxyInfo(result.proxyInfo)'));
  assert(!html.includes('result.status === 404'));
});
check('saved proxy is an explicit persisted summary', () => {
  assert.equal(core.t('已保存的代理'), 'Proxy đang được lưu');
  assert(html.includes('id="accountProxySaved"'));
});
check('dynamic errors and status labels translate', () => {
  for(const text of ['请选择账号', '请输入代理地址', '正在测试连接…', '代理设置已保存', '专用代理已配置', '检查代理失败']) {
    assert(!/[\u3400-\u9fff]/.test(core.t(text)), text);
  }
});
check('account table has explicit proxy column', () => {
  const render = html.slice(html.indexOf('function renderAccounts(){'), html.indexOf('async function loadAccounts(){'));
  assert(render.includes('>Proxy</th>'));
  assert(!html.includes('用于后续请求；模型错误请查看账号状态</div></td>'));
  assert(html.includes('display:none;color:var(--dim);font-size:10px;margin-top:2px'));
  assert(html.includes('loadAccountTestModels()'));
  assert(!html.includes('accountTestFilter'));
  assert(!html.includes('accountTestSearch'));
  assert(html.includes('info.latencyMs'));
  assert(html.includes("ip.style.color = '#1fae7a'"));
  assert(html.includes('showProxyInfo(result.proxyInfo)'));
});
check('settings accurately documents current per-account configuration', () => {
  assert(html.includes('id="accountProxySettings"'));
  assert(html.includes("onclick=\"switchMainTab('accounts')\""));
  const text = core.t('当前按账号保存代理地址，尚未提供共享代理槽。请在账号列表点击 Proxy 配置；保存后用于后续请求。');
  assert(text.includes('chưa có slot proxy dùng chung'));
});
console.log(`SUMMARY: TOTAL ${count} checks, ${count} passed, 0 failed`);
