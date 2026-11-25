const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('src/messagehub/static/ambient.js', 'utf8');
let saved = null;
function loadAppearance() {
  const links = ['glass', 'workbench'].map(name => ({
    href: `/static/${name}.css`,
    dataset: {classic: `/static/${name}-classic.css`},
    getAttribute() { return this.href; },
    setAttribute(attribute, value) { this.href = value; }
  }));
  const root = {dataset: {}};
  const window = {};
  const controls = Object.fromEntries(['skin', 'theme'].map(key => [key + '-mode', {
    addEventListener(type, listener) { this[type] = listener; }
  }]));
  const events = {};
  const document = {
    documentElement: root,
    querySelectorAll: () => links,
    getElementById: id => controls[id],
    addEventListener(type, listener) { events[type] = listener; }
  };
  const localStorage = {getItem: () => saved, setItem(key, value) { saved = value; }};
  vm.runInNewContext(source, {window, document, localStorage, location: {search: ''}, URLSearchParams});
  events.DOMContentLoaded();
  return {appearance: window.MessageHubAmbient, links, root, controls};
}

const {appearance, links, controls} = loadAppearance();
const {calculate} = appearance;
for(const boundary of [0,6,10,14,17,20,24]){
 const left=calculate(boundary-1/60),right=calculate(boundary+1/60);
 assert(Math.abs(left.x-right.x)<.1);
 assert(left.first.every((v,i)=>Math.abs(v-right.first[i])<=1));
 assert(left.second.every((v,i)=>Math.abs(v-right.second[i])<=1));
}
assert.deepStrictEqual(JSON.stringify(calculate(0)),JSON.stringify(calculate(24)));
console.log('Ambient interpolation: 8 checks passed');

assert.deepStrictEqual(links.map(link => link.href), ['/static/glass.css', '/static/workbench.css']);
assert.strictEqual(controls['skin-mode'].value, 'paper');
controls['skin-mode'].value = 'classic';
controls['skin-mode'].change();
controls['theme-mode'].value = 'dark';
controls['theme-mode'].change();
assert.deepStrictEqual(links.map(link => link.href), ['/static/glass-classic.css', '/static/workbench-classic.css']);
const reloaded = loadAppearance();
assert.strictEqual(reloaded.appearance.getSettings().skin, 'classic');
assert.strictEqual(reloaded.root.dataset.theme, 'dark');
assert.strictEqual(reloaded.root.dataset.skin, 'classic');
assert.deepStrictEqual(reloaded.links.map(link => link.href), links.map(link => link.href));
assert.strictEqual(reloaded.controls['skin-mode'].value, 'classic');
reloaded.controls['skin-mode'].value = 'modern';
reloaded.controls['skin-mode'].change();
assert.deepStrictEqual(reloaded.links.map(link => link.href), ['/static/glass.css', '/static/workbench.css']);
assert.strictEqual(reloaded.root.dataset.theme, 'dark');
assert.strictEqual(reloaded.root.dataset.skin, 'modern');
reloaded.controls['skin-mode'].value = 'paper';
reloaded.controls['skin-mode'].change();
assert.strictEqual(reloaded.root.dataset.skin, 'paper');
assert.deepStrictEqual(reloaded.links.map(link => link.href), ['/static/glass.css', '/static/workbench.css']);
console.log('Appearance: skin switching, persistence and independent theme passed');
