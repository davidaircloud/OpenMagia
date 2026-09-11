// Run with node tests/test_effect_logic.js.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../app.js'), 'utf8');
const node = () => ({children: [], events: {}, appendChild(child) { this.children.push(child); }, addEventListener(name, fn) { this.events[name] = fn; }});
const writes = [];
const tabs = [];
const context = vm.createContext({div: node, document: {createElement: node}, putClip: (clip, fields) => writes.push(fields)});
vm.runInContext(source.slice(source.indexOf('function transitionItems('), source.indexOf('function clipHasAnimation(')), context);
vm.runInContext(source.slice(source.indexOf('function appliedEffectsField('), source.indexOf('function transitionStackField(')), context);
const clip = {id: 'c1', transition: {items: [
  {id: 'old', type: 'wipe', dur: 1},
  {id: 'new', type: 'fade', dur: .2},
]}, color: {enabled: true}, magiaEffects: {color: {}}, magiaRecipe: {name: 'Cinematic'}};
assert.equal(context.enabledTransitions(clip, 'start')[0].id, 'new');
clip.transition.items[1].enabled = false;
assert.equal(context.enabledTransitions(clip, 'start')[0].id, 'old');
const field = context.appliedEffectsField(clip, tab => tabs.push(tab));
const rows = field.children.filter(child => child.children.length === 3);
const color = rows.find(row => row.children[0].textContent === 'Color · Cinematic');
assert.ok(color);
color.children[1].events.click();
color.children[2].events.click();
assert.equal(tabs[0], 'color');
assert.equal(JSON.stringify(writes[0]), JSON.stringify({removeMagiaEffects: ['color']}));
const transitions = rows.find(row => row.children[0].textContent === 'Transitions');
transitions.children[2].events.click();
assert.equal(JSON.stringify(writes[1]), JSON.stringify({transition: {items: []}}));
console.log('Effect selection and inspector control checks passed.');
// Clips sharing media must be independently seekable during compositing.
const mediaContext = vm.createContext({state:{tracks:[{clips:[{id:'a',mediaId:'shared'},{id:'b',mediaId:'shared'}]}]},videoEls:{},pool:{appendChild(){}},mediaUrl:()=>'/fixture.mp4',document:{createElement:()=>({dataset:{},addEventListener(){}})}});
vm.runInContext(source.slice(source.indexOf('function getVideoEl('),source.indexOf('function getAudioEl(')),mediaContext);
const media={id:'shared',kind:'video'};
assert.notEqual(mediaContext.getVideoEl(media,'a'),mediaContext.getVideoEl(media,'b'));
assert.equal(mediaContext.getVideoEl(media),mediaContext.getVideoEl(media,'a'));
assert.equal(mediaContext.getVideoEl(media,'b'),mediaContext.getVideoEl(media,'b'));
console.log('Repeated-media decoder isolation checks passed.');
