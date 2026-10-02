import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateAtlas, connections } from '../templates/distribution/landing-page/atlas-model.mjs';

const real = JSON.parse(readFileSync(new URL('../templates/distribution/landing-page/atlas-data.json', import.meta.url), 'utf8'));
test('shipped snapshot has a complete valid graph shape', () => assert.equal(validateAtlas(real), real));
test('outgoing is uses, incoming is used by, without claiming transitive impact', () => {
  const data = {nodes:[{id:'a'},{id:'b'},{id:'c'},{id:'d'}],edges:[{source:'a',target:'b'},{source:'c',target:'a'},{source:'b',target:'d'}]};
  assert.deepEqual([...connections(data,'a','outgoing').visible],['a','b']);
  assert.deepEqual([...connections(data,'a','incoming').visible],['a','c']);
  assert.deepEqual([...connections(data,'a').visible],['a','b','c']);
  assert.equal(connections(data,'d','outgoing').edges.length,0);
  assert.throws(() => connections(data,'missing'));
  assert.throws(() => connections(data,'a','unsupported'));
});
for (const [name, mutate] of [
  ['empty data', data => { data.nodes=[]; }],
  ['dangling edge', data => { data.edges[0].source='missing'; }],
  ['duplicate area', data => { data.nodes.push(data.nodes[0]); }],
  ['missing provenance', data => { delete data.source_sha256; }],
  ['invalid denominator', data => { data.files_scanned=0; }],
  ['unsafe shape', data => { data.nodes[0].examples=[{}]; }],
  ['duplicate directed edge', data => { data.edges.push({...data.edges[0]}); }],
  ['coincident connected areas', data => {
    const edge=data.edges[0], a=data.nodes.find(node => node.id===edge.source), b=data.nodes.find(node => node.id===edge.target);
    b.x=a.x; b.y=a.y;
  }],
  ['overflowing layout', data => { data.nodes[0].x=Number.MAX_VALUE; }],
  ['understated import denominator', data => { data.module_connections=0; }],
  ['unsafe integer denominator', data => { data.module_connections=Number.MAX_SAFE_INTEGER+1; }],
  ['missing metric definition', data => { delete data.definition; }],
  ['empty accessible label', data => { data.nodes[0].label='   '; }],
]) test(`reject ${name} instead of drawing a false complete map`, () => {
  const data=structuredClone(real); mutate(data); assert.throws(() => validateAtlas(data));
});

test('retain reciprocal imports, isolated areas, and within-area import counts', () => {
  const data=structuredClone(real);
  const edge=data.edges[0];
  data.edges=[edge, {source:edge.target, target:edge.source, count:2}];
  data.module_connections=edge.count+3;
  assert.equal(validateAtlas(data),data);
  data.edges=[]; data.module_connections=0;
  assert.equal(validateAtlas(data),data);
});
