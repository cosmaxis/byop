"""Exercise the deployed Worker contract against an in-memory R2 binding."""

import shutil
import subprocess
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[1] / "worker.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_worker_routes_and_fail_closed_errors():
    script = r"""
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(process.argv[1]);
const { default: worker } = await import(`data:text/javascript;base64,${source.toString('base64')}`);
globalThis.fetch = () => { throw new Error('Unexpected network request'); };
const version = '2.1.11';
const wheel = `byop-${version}-py3-none-any.whl`;
const sha256 = 'a'.repeat(64);
const manifest = { version, sha256, wheel };
const objects = new Map([
  ['latest.json', JSON.stringify(manifest)],
  [`releases/${version}/manifest.json`, JSON.stringify(manifest)],
  [`releases/${version}/install.sh`, '#!/bin/sh\necho installed\n'],
  [`releases/${version}/${wheel}`, 'wheel bytes'],
]);
const keys = [];
const env = { RELEASES: {
  async get(key) {
    keys.push(key);
    return objects.has(key) ? new Response(objects.get(key)) : null;
  },
} };
const request = (path, method = 'GET', binding = env) =>
  worker.fetch(new Request(`https://byop.cosmaxis.com${path}`, { method }), binding);
const check = async (path, status, body, cache, method = 'GET', binding = env) => {
  const response = await request(path, method, binding);
  assert.equal(response.status, status, `${method} ${path}`);
  assert.equal(await response.text(), body);
  assert.equal(response.headers.get('cache-control'), cache);
  return response;
};

const latest = await check('/latest.json', 200, JSON.stringify(manifest), 'no-store');
assert.match(latest.headers.get('content-type'), /^application\/json/);
await check('/latest.json', 200, '', 'no-store', 'HEAD');
for (const path of ['/install', '/install.sh']) {
  const installer = await check(path, 200, objects.get(`releases/${version}/install.sh`), 'no-store');
  assert.match(installer.headers.get('content-type'), /^text\/plain/);
  await check(path, 200, '', 'no-store', 'HEAD');
}
for (const [name, content] of [
  ['install.sh', objects.get(`releases/${version}/install.sh`)],
  ['manifest.json', JSON.stringify(manifest)],
  [wheel, 'wheel bytes'],
]) {
  const path = `/releases/${version}/${name}`;
  await check(path, 200, content, 'public, max-age=31536000, immutable');
  await check(path, 200, '', 'public, max-age=31536000, immutable', 'HEAD');
}
const wheelResponse = await request(`/releases/${version}/${wheel}`);
assert.equal(wheelResponse.headers.get('content-type'), 'application/octet-stream');
const versionedManifest = await request(`/releases/${version}/manifest.json`);
assert.match(versionedManifest.headers.get('content-type'), /^application\/json/);
assert.deepEqual(keys.slice(0, 2), ['latest.json', 'latest.json']);
assert.equal(keys.at(-1), `releases/${version}/manifest.json`);
await check('/', 200, (await (await request('/')).text()), 'no-store');
for (const path of [
  '/missing', '/releases/2.1.11/other.txt',
  '/releases/2.1.12/byop-2.1.11-py3-none-any.whl',
  '/releases/v2.1.11/install.sh', '/releases/2.1.11/%2e%2e/secret',
  '/releases/2.1.11/%69nstall.sh', '/releases/2.1.11/install.sh/extra',
]) await check(path, 404, 'Not found', 'no-store');
const count = keys.length;
await check('/releases/2.1.11/other.txt', 404, 'Not found', 'no-store');
assert.equal(keys.length, count);
const denied = await check('/install', 405, 'Method not allowed', 'no-store', 'POST');
assert.equal(denied.headers.get('allow'), 'GET, HEAD');
await check('/latest.json', 503, 'Release storage unavailable', 'no-store', 'GET', {});
await check('/install', 503, 'Release storage unavailable', 'no-store', 'HEAD', {});

objects.delete('latest.json');
await check('/latest.json', 503, 'Release metadata unavailable', 'no-store');
await check('/install', 503, 'Release metadata unavailable', 'no-store');
for (const bad of [
  '{', 'null', '{}',
  JSON.stringify({ ...manifest, version: ['2.1.11'] }),
  JSON.stringify({ ...manifest, version: '2.1.11/../../secret' }),
  JSON.stringify({ ...manifest, sha256: 'A'.repeat(64) }),
  JSON.stringify({ ...manifest, wheel: 'other.whl' }),
]) {
  objects.set('latest.json', bad);
  await check('/install', 503, 'Release metadata unavailable', 'no-store');
}
objects.set('latest.json', JSON.stringify(manifest));
objects.set(`releases/${version}/manifest.json`, JSON.stringify({ ...manifest, version: '2.1.12' }));
await check(`/releases/${version}/manifest.json`, 503, 'Release metadata unavailable', 'no-store');
objects.set(`releases/${version}/manifest.json`, '{');
await check(`/releases/${version}/manifest.json`, 503, 'Release metadata unavailable', 'no-store', 'HEAD');
objects.delete(`releases/${version}/manifest.json`);
await check(`/releases/${version}/manifest.json`, 404, 'Not found', 'no-store');
objects.delete(`releases/${version}/install.sh`);
await check('/install.sh', 503, 'Release installer unavailable', 'no-store');
await check(`/releases/${version}/install.sh`, 404, 'Not found', 'no-store');
objects.delete(`releases/${version}/${wheel}`);
await check(`/releases/${version}/${wheel}`, 404, 'Not found', 'no-store', 'HEAD');
const failing = { RELEASES: { get() { throw new Error('secret storage detail'); } } };
const failure = await request('/latest.json', 'GET', failing);
assert.equal(failure.status, 503);
assert.equal(await failure.text(), 'Release storage unavailable');
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(WORKER)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
