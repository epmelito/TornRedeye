// Execute the exact inline deployment code, after the managed policy as AWS documents.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = JSON.parse(fs.readFileSync('dashboard-template.json', 'utf8'));
const resources = template.Resources;
const code = resources.ViewerSecurityHeaders.Properties.FunctionCode;
const context = vm.createContext({});
new vm.Script(code).runInContext(context);
const security = resources.SecurityHeaders.Properties.ResponseHeadersPolicyConfig.SecurityHeadersConfig;
for (const config of Object.values(security)) assert.equal(config.Override, true);
const expected = {
  'content-security-policy': security.ContentSecurityPolicy.ContentSecurityPolicy,
  'x-frame-options': security.FrameOptions.FrameOption,
  'referrer-policy': security.ReferrerPolicy.ReferrerPolicy,
  'strict-transport-security': `max-age=${security.StrictTransportSecurity.AccessControlMaxAgeSec}`,
  'x-content-type-options': 'nosniff',
};
function managedHeaders() {
  return {
    'referrer-policy': {value: 'strict-origin-when-cross-origin'},
    'strict-transport-security': {value: 'max-age=31536000'},
    'x-content-type-options': {value: 'nosniff'},
    'x-frame-options': {value: 'SAMEORIGIN'},
    'x-xss-protection': {value: '1; mode=block'},
  };
}
for (const statusCode of [200, 204, 301, 304]) {
  for (const method of ['GET', 'HEAD']) {
    const headers = {...managedHeaders(), 'cache-control': {value: 'public,max-age=15'},
      'content-type': {value: 'application/json'}, etag: {value: '"original"'}};
    headers['content-security-policy'] = {value: 'unsafe-origin-value'};
    const response = {statusCode, headers, cookies: {session: {value: 'untouched'}}, body: 'unchanged'};
    assert.equal(context.handler({request: {method}, response}), response);
    for (const [name, value] of Object.entries(expected)) assert.equal(response.headers[name].value, value);
    assert.equal(response.statusCode, statusCode);
    assert.equal(response.body, 'unchanged');
    assert.equal(response.cookies.session.value, 'untouched');
    assert.equal(response.headers['cache-control'].value, 'public,max-age=15');
    assert.equal(response.headers.etag.value, '"original"');
    assert.equal(response.headers['x-xss-protection'], undefined);
  }
}
// AWS bypasses viewer-response functions on origin >=400. Explicitly retain the gap.
for (const statusCode of [400, 403, 404, 429, 500, 502, 503, 504]) {
  const response = {statusCode, headers: managedHeaders()};
  assert.equal(response.headers['content-security-policy'], undefined);
  assert.notEqual(response.headers['x-frame-options'].value, expected['x-frame-options']);
  assert.notEqual(response.headers['referrer-policy'].value, expected['referrer-policy']);
  assert.equal(response.headers['strict-transport-security'].value, expected['strict-transport-security']);
  assert.equal(response.headers['x-content-type-options'].value, expected['x-content-type-options']);
  assert.equal(response.headers['x-xss-protection'].value, '1; mode=block');
}
console.log('CloudFront function: 8 success/redirect/conditional cases passed; 8 origin error gaps confirmed by model.');
