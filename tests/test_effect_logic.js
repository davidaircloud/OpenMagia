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
const mediaContext = vm.createContext({state:{tracks:[{clips:[{id:'a',mediaId:'shared'},{id:'b',mediaId:'shared'}]}]},videoEls:{},pool:{appendChild(){}},mediaUrl:()=>'/fixture.mp4',playing:false,drawNow(){},document:{createElement:()=>({dataset:{},events:{},addEventListener(name,fn){this.events[name]=fn;}})}});
vm.runInContext(source.slice(source.indexOf('function getVideoEl('),source.indexOf('function getAudioEl(')),mediaContext);
const media={id:'shared',kind:'video'};
assert.notEqual(mediaContext.getVideoEl(media,'a'),mediaContext.getVideoEl(media,'b'));
assert.equal(mediaContext.getVideoEl(media),mediaContext.getVideoEl(media,'a'));
assert.equal(mediaContext.getVideoEl(media,'b'),mediaContext.getVideoEl(media,'b'));
assert.equal(typeof mediaContext.getVideoEl(media).events.seeked,'function');
console.log('Repeated-media decoder isolation checks passed.');
// Overlapping audio clips reusing one media source need distinct decoders.
const audioContext = vm.createContext({state:{tracks:[{clips:[{id:'a',mediaId:'shared'},{id:'b',mediaId:'shared'}]}]},audioEls:{},audioGains:{},pool:{appendChild(){}},mediaUrl:()=>'/fixture.wav',window:{},document:{createElement:()=>({dataset:{}})}});
vm.runInContext(source.slice(source.indexOf('function getAudioEl('),source.indexOf('function setAudioGain(')),audioContext);
const firstAudio=audioContext.getAudioEl(media,'a'),secondAudio=audioContext.getAudioEl(media,'b');
assert.notEqual(firstAudio,secondAudio);
assert.equal(audioContext.getAudioEl(media),firstAudio);
assert.notEqual(firstAudio.dataset.audioKey,secondAudio.dataset.audioKey);
// Video clip fades must scale the volume, including boosted clips.
const mixContext=vm.createContext({state:{tracks:[{kind:'video',clips:[{id:'v',mediaId:'m',start:0,in:0,out:4,volume:.5,audioFade:{in:1}}]}]},globalMute:false,clamp:(v,a,b)=>Math.max(a,Math.min(b,v)),mediaById:()=>({kind:'video',hasAudio:true})});
vm.runInContext(source.slice(source.indexOf('function audioSources('),source.indexOf('function updateAudio(')),mixContext);
assert.equal(mixContext.audioSources(.5)[0].gain,.25);
// Loop carries frame overshoot and seeks every active audio clip after wrapping.
let cleared=0,pauses=0;
const transport=vm.createContext({programWindow:null,programOutputActive:()=>false,requestAnimationFrame:()=>1,performance:{now:()=>4250},state:{},playing:true,playTime:3.9,lastNow:4000,loop:true,timelineEnd:()=>4,activeAudioIds:{clear:()=>cleared++},pause:()=>pauses++,drawFrame(){},mirrorProgramOutput(){},updateAudio(){},updatePlayhead(){},updateTimecode(){},console});
vm.runInContext(source.slice(source.indexOf('function tick()'),source.indexOf('function updatePlayhead()')),transport);
transport.tick();
assert.ok(Math.abs(vm.runInContext('playTime',transport)-.15)<1e-8);
assert.equal(cleared,1);assert.equal(pauses,0);
vm.runInContext('loop=false;playTime=3.9;lastNow=4000',transport);transport.tick();assert.equal(pauses,1);
console.log('Audio isolation, fade gain and loop-boundary checks passed.');
// Independent transform lanes compound without changing their sibling keyframes.
const motionContext=vm.createContext({clamp:(v,a,b)=>Math.max(a,Math.min(b,v))});
vm.runInContext(source.slice(source.indexOf('function motionState('),source.indexOf('function transitionItems(')),motionContext);
const points=(a,b)=>({points:[{id:'a',at:0,zoom:a,x:.5,y:.5},{id:'b',at:1,zoom:b,x:.5,y:.5}]});
const layered={id:'stack',start:0,in:0,out:4,zoom:1.2,keyframes:points(1,1.5),transformLayers:[{id:'extra',keyframes:points(1,2)}]};
assert.ok(Math.abs(motionContext.clipMotion(layered,2).zoom-2.25)<1e-8);
const original=JSON.stringify(layered.keyframes);
const view=motionContext.transformLayerClip(layered,layered.transformLayers[0]);
view.keyframes=points(2,2);
assert.equal(JSON.stringify(layered.keyframes),original);
assert.equal(motionContext.clipMotion(layered,2).zoom,3);
layered.transformLayers[0].keyframes.enabled=false;
assert.equal(motionContext.clipMotion(layered,2).zoom,1.5);
const halves=motionContext.splitTransformKeyframes(layered,.5);
assert.equal(halves[0].points.at(-1).zoom,1.25);
assert.equal(halves[1].points[0].zoom,1.25);
console.log('Independent transform composition, bypass and split checks passed.');
