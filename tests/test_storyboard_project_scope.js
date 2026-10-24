const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(require('path').join(__dirname, '..', 'app.js'), 'utf8');
const ensure = source.slice(source.indexOf('function ensureStoryboardDraft(){'), source.indexOf('\n}', source.indexOf('function ensureStoryboardDraft(){')) + 2);
const start = source.indexOf('function scheduleStoryboardSave(){');
const schedule = source.slice(start, source.indexOf('\n', start));
const requests = [], timers = [];
const context = {state:{slug:'first',storyboard_draft:{scenes:[{prompt:'First project'},{prompt:'Second shot'}]}},
  storyboardDraft:null,storyboardProjectSlug:null,storyboardSaveTimer:null,
  clearTimeout:()=>{},setTimeout:cb=>{timers.push(cb);return timers.length},
  captureStoryboardFields:()=>{}, api:(url,opts)=>{requests.push({url,...opts.body});return Promise.resolve({})},
  JSON,encodeURIComponent};
vm.createContext(context);vm.runInContext(ensure+'\n'+schedule,context);
vm.runInContext('ensureStoryboardDraft();scheduleStoryboardSave();',context);
context.state={slug:'second',storyboard_draft:{scenes:[{prompt:'Different project'},{prompt:'Different ending'}]}};
vm.runInContext('ensureStoryboardDraft()',context);
assert.strictEqual(context.storyboardDraft.scenes[0].prompt,'Different project');
timers[0]();
assert.strictEqual(requests[0].url,'/api/projects/first/storyboard');
assert.strictEqual(requests[0].project_slug,'first','pending saves must retain the original project identity');
assert.strictEqual(requests[0].storyboard_draft.scenes[0].prompt,'First project','pending saves must snapshot the original draft');
console.log('Storyboard project-scope validation passed');
