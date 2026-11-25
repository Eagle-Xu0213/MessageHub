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
  const document = {documentElement: root, querySelectorAll: () => links, addEventListener() {}};
  const localStorage = {getItem: () => saved, setItem(key, value) { saved = value; }};
  vm.runInNewContext(source, {window, document, localStorage, location: {search: ''}, URLSearchParams});
  return {appearance: window.MessageHubAmbient, links, root};
}

const {appearance, links} = loadAppearance();
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
appearance.save({skin: 'classic', theme: 'dark'});
assert.deepStrictEqual(links.map(link => link.href), ['/static/glass-classic.css', '/static/workbench-classic.css']);
const reloaded = loadAppearance();
assert.strictEqual(reloaded.appearance.getSettings().skin, 'classic');
assert.strictEqual(reloaded.root.dataset.theme, 'dark');
assert.deepStrictEqual(reloaded.links.map(link => link.href), links.map(link => link.href));
reloaded.appearance.save({skin: 'modern'});
assert.deepStrictEqual(reloaded.links.map(link => link.href), ['/static/glass.css', '/static/workbench.css']);
assert.strictEqual(reloaded.root.dataset.theme, 'dark');
console.log('Appearance: skin switching, persistence and independent theme passed');
