import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

// Opt in with STOCK_TEST_CDP_URL pointing to a fresh isolated Chromium profile.
// Serve the production build locally; STOCK_TEST_PREVIEW_URL defaults below.
// Uses Node's built-in WebSocket; no browser package or download is required.
const endpoint = process.env.STOCK_TEST_CDP_URL;
test('mobile observation links reveal and position the timeline after rendering', { skip: !endpoint, timeout: 30000 }, async t => {
  const preview = process.env.STOCK_TEST_PREVIEW_URL || 'http://127.0.0.1:8767/stock.html';
  const target = await fetch(`${endpoint}/json/new?about:blank`, { method: 'PUT' }).then(r => r.json());
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  t.after(async () => { socket.close(); await fetch(`${endpoint}/json/close/${target.id}`); });
  await new Promise<void>((resolve, reject) => { socket.onopen = () => resolve(); socket.onerror = reject; });
  let serial = 0;
  let nextSnapshot: (() => void) | undefined;
  const pending = new Map<number, { resolve: (value: any) => void; reject: (error: Error) => void }>();
  const send = (method: string, params: object = {}) => new Promise<any>((resolve, reject) => {
    const id = ++serial; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params }));
  });
  const fixture = readFileSync(new URL('../../tests/fixtures/stock-preview.json', import.meta.url), 'utf8');
  socket.onmessage = event => {
    const message = JSON.parse(String(event.data));
    if (message.id) {
      const request = pending.get(message.id); pending.delete(message.id);
      if (message.error) request?.reject(Error(JSON.stringify(message.error))); else request?.resolve(message.result);
    } else if (message.method === 'Fetch.requestPaused') {
      void send('Fetch.fulfillRequest', { requestId: message.params.requestId, responseCode: 200,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(fixture).toString('base64') })
        .then(() => { nextSnapshot?.(); nextSnapshot = undefined; });
    }
  };
  const evaluate = async (expression: string) => {
    const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    assert.equal(result.exceptionDetails, undefined, JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const wait = (expression: string) => evaluate(`new Promise((resolve,reject)=>{
    const deadline=performance.now()+8000;
    const check=()=>{if(${expression})resolve(true);else if(performance.now()>deadline)reject(Error('Expected rendered state: '+${JSON.stringify(expression)}));else requestAnimationFrame(check)};check();
  })`);
  await send('Page.enable'); await send('Runtime.enable');
  await send('Fetch.enable', { patterns: [{ urlPattern: '*stock.json', requestStage: 'Request' }] });
  for (const height of [480, 844]) {
    await t.test(`390x${height}: expanded observation and direct link`, async () => {
      await send('Emulation.setDeviceMetricsOverride', { width: 390, height, deviceScaleFactor: 1, mobile: true });
      await send('Page.navigate', { url: `${preview}?mode=items&item=1000&country=arg` });
      await wait("document.querySelector('.mobile-observation')");
      await evaluate('document.fonts.ready.then(()=>true)');
      await evaluate("[...document.querySelectorAll('.mobile-observation')].find(n=>n.innerText.includes('Argentina')).click()");
      await wait("document.querySelector('#details-arg') && !document.querySelector('#details-arg').hidden");
      await evaluate("document.querySelector('#details-arg .detail-timeline').scrollIntoView({block:'center'})");
      const href = await evaluate("document.querySelector('#details-arg .detail-timeline').href");
      assert.equal(new URL(href).searchParams.get('panel'), 'timeline');
      await evaluate("document.querySelector('#details-arg .detail-timeline').click()");
      await wait("getComputedStyle(document.querySelector('#timeline')).display!=='none'");
      const positioned = `(()=>{const heading=document.querySelector('#timeline-title').getBoundingClientRect();return heading.top>=0&&heading.bottom<=document.querySelector('.mobile-bottom-nav').getBoundingClientRect().top})()`;
      assert.equal(await evaluate(positioned), true, 'normal activation must leave the timeline heading in view');
      assert.equal(await evaluate("getComputedStyle(document.querySelector('.stock-section')).display"), 'none');
      await send('Page.navigate', { url: href });
      await wait("document.querySelector('.endpoint-chart')");
      assert.equal(await evaluate(positioned), true, 'opening the actual link directly must reveal its timeline');
      await send('Page.reload');
      await wait("document.querySelector('.endpoint-chart')");
      assert.equal(await evaluate(positioned), true, 'refreshing the direct link must retain timeline positioning');
      // Routine age/feed rerenders must not pull the reader back after scrolling.
      const refreshed = new Promise<void>(resolve => { nextSnapshot = resolve; });
      await evaluate("window.scrollTo(0,0);document.dispatchEvent(new Event('visibilitychange'))");
      await refreshed;
      await evaluate('new Promise(resolve=>requestAnimationFrame(()=>resolve(true)))');
      await wait("!document.querySelector('.refresh').disabled");
      assert.equal(await evaluate('scrollY'), 0, 'a snapshot refresh must preserve the reader scroll position');
    });
  }
});
