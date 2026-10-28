const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const html = fs.readFileSync(path.join(__dirname, '..', 'dashboard.html'), 'utf8');
const start = html.indexOf('let accountProxyState = null;');
const end = html.indexOf('// issue #69', start);
assert(start > 0 && end > start);
const nodes = {};
function node(id) {
  return nodes[id] || (nodes[id] = {value: '', textContent: '', disabled: false,
    style: {}, focus() {}, classList: {add() {}, remove() {}}});
}
let saved = {uid: 'fixture', nickname: 'Fixture', proxyMode: 'custom', proxyMasked: 'http://***:***@saved.example:8080'};
let posts = [], confirmResult = true, failRead = false, failSave = false, reads = 0;
const ctx = {window: {ACCOUNTS: [{uid: 'fixture', proxyMode: 'inherit'}]},
  document: {getElementById: node}, renderAccounts() {}, toast() {},
  confirm: () => confirmResult,
  async getJSON() { reads++; if(failRead) throw Error('read failed'); return {accounts: [{...saved}]}; },
  async postJSON(url, payload) {
    posts.push({url, payload: {...payload}});
    if(failSave) throw Error('save failed');
    if(url.endsWith('/test')) return {ok: true, status: 404, latencyMs: 1};
    saved = {...saved, proxyMode: payload.proxy === 'direct' ? 'direct' : payload.proxy === '' ? 'inherit' : 'custom',
      proxyMasked: payload.proxy === 'direct' || payload.proxy === '' ? '' : 'http://***:***@new.example:8080'};
    return {ok: true, account: {...saved}};
  }
};
vm.createContext(ctx); vm.runInContext(html.slice(start, end), ctx);
let checks = 0;
async function check(name, fn) { await fn(); checks++; console.log('PASS ' + name); }
(async () => {
  await check('open reads persisted config instead of stale browser account', async () => {
    await ctx.editAccountProxy('fixture');
    assert.equal(reads, 1); assert.equal(node('accountProxyMode').value, 'custom');
    assert.equal(node('accountProxySaved').style.display, '');
    assert.equal(node('accountProxyCurrent').textContent, saved.proxyMasked);
    assert.equal(node('accountProxyCustom').style.display, 'none');
    assert.equal(node('accountProxyUrl').value, '');
  });
  await check('unchanged save preserves credentials without posting masked URL', async () => {
    await ctx.saveAccountProxy(); assert.equal(posts.length, 0);
    await ctx.editAccountProxy('fixture'); assert.equal(node('accountProxyCurrent').textContent, saved.proxyMasked);
  });
  await check('explicit replacement requires a new URL and cancel preserves saved config', async () => {
    ctx.replaceAccountProxy(); assert.equal(node('accountProxyCustom').style.display, '');
    assert.throws(() => ctx.accountProxyPayload());
    node('accountProxyUrl').value = 'http://fixture:password@new.example:8080';
    ctx.closeAccountProxy(); await ctx.editAccountProxy('fixture');
    assert.equal(node('accountProxyUrl').value, ''); assert(saved.proxyMasked.includes('saved.example'));
  });
  await check('replacement is saved and reopening shows the new persisted address', async () => {
    ctx.replaceAccountProxy(); node('accountProxyUrl').value = 'http://fixture:password@new.example:8080';
    await ctx.saveAccountProxy(); await ctx.editAccountProxy('fixture');
    assert(node('accountProxyCurrent').textContent.includes('new.example'));
    assert.equal(node('accountProxyCustom').style.display, 'none');
    assert(!posts[0].payload.proxy.includes('***'));
  });
  await check('test uses saved route when editor is hidden', async () => {
    await ctx.testAccountProxy(); assert.equal(posts.at(-1).url, '/accounts/proxy/test');
    assert(!Object.hasOwn(posts.at(-1).payload, 'proxy'));
  });
  await check('cancel deletion leaves persisted config unchanged', async () => {
    const count = posts.length; confirmResult = false;
    await ctx.removeAccountProxy(); assert.equal(posts.length, count); assert.equal(saved.proxyMode, 'custom');
  });
  await check('confirmed deletion clears proxy and explicitly selects direct mode', async () => {
    confirmResult = true; await ctx.removeAccountProxy();
    assert.equal(posts.filter(p => p.url === '/accounts/proxy').at(-1).payload.proxy, 'direct');
    await ctx.editAccountProxy('fixture'); assert.equal(node('accountProxyMode').value, 'direct');
    assert.equal(node('accountProxySaved').style.display, 'none'); assert.equal(saved.proxyMasked, '');
  });
  await check('failed read disables edits rather than saving stale configuration', async () => {
    ctx.closeAccountProxy(); failRead = true; await ctx.editAccountProxy('fixture');
    assert.equal(node('accountProxySave').disabled, true);
    assert.equal(node('accountProxyRemove').disabled, true);
    assert.equal(node('accountProxyCurrent').textContent, '');
    failRead = false; ctx.closeAccountProxy();
  });
  await check('failed save keeps original persisted proxy', async () => {
    await ctx.editAccountProxy('fixture'); ctx.replaceAccountProxy();
    node('accountProxyUrl').value = 'http://new.example:8080'; failSave = true;
    await ctx.saveAccountProxy(); assert.equal(saved.proxyMode, 'direct');
    assert.equal(node('accountProxyResult').textContent, 'save failed');
    assert.equal(node('accountProxySave').disabled, false);
  });
  console.log(`SUMMARY: TOTAL ${checks} checks, ${checks} passed, 0 failed`);
})().catch(e => { console.error(e); process.exitCode = 1; });
