/* OpenMagia NLE — app.js
   Browser video editor: canvas playback compositor, multi-track timeline,
   media bin, inspector, generation, RAM monitor, export. No dependencies. */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const div = (cls) => { const d = document.createElement('div'); if (cls) d.className = cls; return d; };
let LANE_OFFSET = clamp(parseFloat(localStorage.getItem('openmagia-lane-header-width')) || 128, 96, 320);

/* ---------------- state ---------------- */
let state = null;
let engine = null;
let sel = null;            // {type:'clip'|'media', id}
let selectedKeyframe = null; // {clipId, pointId}, shared by timeline and inspector
let revealSelectedKeyframe = false; // one-shot reveal after explicit timeline selection
let selectedTransition = null; // {clipId, transitionId}
let pxPerSec = 100;
// The editable canvas is intentionally longer than its content and grows as
// the user approaches its right edge. Export duration remains timelineEnd().
let timelineViewSeconds = 0;
let timelineExtendFrame = 0;
let playTime = 0;
let playing = false;
let loop = false;
let globalMute = false;
let lastNow = 0;
let rafId = 0;
let pool = null;           // offscreen container for media elements
let drag = null;           // active timeline drag
let charMediaIds = [];
let charEditId = null;
let promptTemplates = [];
let selectedTemplate = null;
let guideAnswers = {};
let hubView = 'editor';
let settingsModelFingerprint = '';
let modelUninstallState = null;
let modelLicenseState = null;
let assetFilter = 'all';
let skillFilter = 'all';
let skillTypeFilter = 'all';
let assetProjectFilter = 'all';
let assetLibraryState = { assets: [], projects: [] };
let assetLibraryLoaded = false;
let assetLibraryRequest = 0;
let dragTrack = null;
let inspectorTab = 'inspect';
let inspectorClipTab = 'clip';
let effectPreviewRaf = 0;
let activePromptSkill = null;
let selectedPickerItem = null;
let refineMode = 'scene';
let storyboardRefineTarget = null;
let sourceSelection = null;
let pendingSourceFrame = 'last';
let generationSubmitting = false;
let storyboardDraft = null;
let storyboardSaveTimer = 0;
let storyboardSubmitting = false;
let continuityAuditPending = false;
let continuityAuditState = null;
let storyboardPickerState = null;
let magiaCard = null;
let timelineMagiaPlan = null;
let timelineMagiaError = '';
let timelineMagiaSeed = 0;
let timelineMagiaPlanning = false;
let timelineMagiaInterpreting = false;
let timelineMagiaTimer = 0;
let appliedLayoutSlug = null;
let layoutSaveTimer = 0;
let refreshPromise = null;
let refreshTimer = 0;
// Project polling must not replace the clip object while an inspector control
// is actively editing it. Otherwise the thumb and preview jump back to the
// last server value before pointer release can commit the change.
let inspectorControlActive = false;
let clipSavePending = 0;
let clipMutationEpoch = 0;
let pluginCatalog = [];
let pluginTab = 'installed';
let activePlugin = null;
let pluginGenerationSnapshot = new Map();
const pluginBackgroundFrames = new Map();

function layoutStorageKey(){return 'openmagia-layout-'+((state&&state.slug)||'default');}
function collectProjectLayout(){
  const bodyStyle=getComputedStyle(document.body),footer=$('#timeline'),prompt=$('#genPrompt'),style=$('#genStyle');
  return {media_width:parseFloat(bodyStyle.getPropertyValue('--media-w'))||250,inspector_width:parseFloat(bodyStyle.getPropertyValue('--inspector-w'))||330,timeline_height:footer?footer.offsetHeight:230,timeline_maximized:!!(footer&&footer.classList.contains('maximized')),timeline_zoom:pxPerSec,lane_header_width:LANE_OFFSET,prompt_height:prompt?prompt.offsetHeight:150,style_height:style?style.offsetHeight:72,inspector_clip_tab:inspectorClipTab};
}
function applyProjectLayout(){
  if(!state||!state.slug||appliedLayoutSlug===state.slug)return;timelineViewSeconds=0;let local={};try{local=JSON.parse(localStorage.getItem(layoutStorageKey())||'{}')}catch(_){}const layout={...(state.ui_layout||{}),...local};
  if(Number.isFinite(+layout.media_width))document.body.style.setProperty('--media-w',clamp(+layout.media_width,180,420)+'px');
  if(Number.isFinite(+layout.inspector_width))document.body.style.setProperty('--inspector-w',clamp(+layout.inspector_width,280,520)+'px');
  if(Number.isFinite(+layout.timeline_height))document.body.style.setProperty('--timeline-h',clamp(+layout.timeline_height,120,window.innerHeight*.8)+'px');
  if(Number.isFinite(+layout.timeline_zoom)){pxPerSec=clamp(+layout.timeline_zoom,20,400);if($('#zoomVal'))$('#zoomVal').textContent=(pxPerSec/100).toFixed(1)+'×';}
  if(Number.isFinite(+layout.lane_header_width)){LANE_OFFSET=clamp(+layout.lane_header_width,96,320);document.body.style.setProperty('--lane-head-w',LANE_OFFSET+'px');}
  if(['clip','transform','color','animate','transitions','effects'].includes(layout.inspector_clip_tab))inspectorClipTab=layout.inspector_clip_tab;
  const footer=$('#timeline');if(footer)footer.classList.toggle('maximized',!!layout.timeline_maximized);const maxBtn=$('#tlMaxBtn');if(maxBtn)maxBtn.classList.toggle('on',!!layout.timeline_maximized);if($('#tlMaxLabel'))$('#tlMaxLabel').textContent=layout.timeline_maximized?'Collapse':'Expand';
  if(Number.isFinite(+layout.prompt_height)&&$('#genPrompt'))$('#genPrompt').style.height=clamp(+layout.prompt_height,150,window.innerHeight*.7)+'px';if(Number.isFinite(+layout.style_height)&&$('#genStyle'))$('#genStyle').style.height=clamp(+layout.style_height,72,window.innerHeight*.5)+'px';appliedLayoutSlug=state.slug;
}
function saveProjectLayout(){if(!state||!state.slug)return;const layout=collectProjectLayout();state.ui_layout=layout;localStorage.setItem(layoutStorageKey(),JSON.stringify(layout));clearTimeout(layoutSaveTimer);layoutSaveTimer=setTimeout(()=>api('/api/project',{method:'POST',body:{ui_layout:layout}}).catch(()=>{}),220);}

let SKILL_CATALOG = [];
let SKILL_CATALOG_ERRORS = [];
let skillCatalogPromise = null;
async function loadSkillCatalog(force=false){
  if(SKILL_CATALOG.length&&!force)return SKILL_CATALOG;
  if(!skillCatalogPromise)skillCatalogPromise=api('/api/skills').then(result=>{SKILL_CATALOG=result.skills||[];SKILL_CATALOG_ERRORS=result.errors||[];return SKILL_CATALOG;}).catch(error=>{SKILL_CATALOG_ERRORS=['Skills could not be loaded: '+error.message];return SKILL_CATALOG;}).finally(()=>{skillCatalogPromise=null;});
  return skillCatalogPromise;
}
function customSkills(){try{return JSON.parse(localStorage.getItem('openmagia-custom-skills')||'[]');}catch(_){return [];}}
function saveCustomSkills(skills){localStorage.setItem('openmagia-custom-skills',JSON.stringify(skills));}

const videoEls = {};
const audioEls = {};
const audioGains = {};
const waveformCache = {};
let audioContext = null;
let activeAudioIds = new Set();

/* ---------------- api ---------------- */
async function api(path, opts = {}) {
  const o = { method: opts.method || 'GET', headers: {} };
  if (opts.raw) { o.body = opts.raw; }
  else if (opts.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
  if (opts.headers) Object.assign(o.headers, opts.headers);
  const r = await fetch(path, o);
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) { const error=new Error((data && data.error) || ('HTTP ' + r.status));error.status=r.status;error.data=data;throw error; }
  return data;
}

async function refresh(force = false) {
  // Mutations that must verify newly persisted state cannot reuse a GET that
  // began before the mutation. Wait for that stale request, then issue a new
  // one. Background polling continues to coalesce ordinary refreshes.
  if(refreshPromise){
    if(!force)return refreshPromise;
    try{await refreshPromise;}catch(_){}
  }
  refreshPromise=(async()=>{try {
    const mutationEpochAtStart=clipMutationEpoch;
    const inspectorWasBusy=inspectorControlActive||clipSavePending>0;
    const s = await api('/api/state');
    // A response requested before or during an inspector mutation may contain
    // the previous clip value. Never allow it to replace the live preview.
    if(inspectorWasBusy||inspectorControlActive||clipSavePending>0||mutationEpochAtStart!==clipMutationEpoch)return;
    // A skill catalog problem must never prevent the editor from loading.
    await loadSkillCatalog();
    engine = s.engine || {}; delete s.engine;
    state = s;
    applyProjectLayout();
    if ($('#undoBtn')) $('#undoBtn').disabled = !engine.can_undo;
    if (playTime > timelineEnd()) playTime = timelineEnd();
    // Preload only media that is actually used by the timeline. Preloading the
    // entire bin creates duplicate video decoders (especially for generated
    // sheet outputs) and can exhaust Safari's media process.
    const timelineMediaIds = new Set((state.tracks || []).flatMap(t =>
      (t.clips || []).map(c => c.mediaId).filter(Boolean)));
    // Release decoders for media that left the timeline. Safari has a much
    // lower practical decoder ceiling than Chromium, so retaining old hidden
    // elements eventually makes a healthy page appear blank or unresponsive.
    for(const id of Object.keys(videoEls))if(!timelineMediaIds.has(id)){videoEls[id].pause&&videoEls[id].pause();videoEls[id].remove();delete videoEls[id];}
    for(const id of Object.keys(audioEls))if(!timelineMediaIds.has(id)){audioEls[id].pause();if(audioGains[id]){try{audioGains[id].disconnect();}catch(_){}}audioEls[id].remove();delete audioEls[id];delete audioGains[id];}
    for (const id of timelineMediaIds) {
      const m = state.media.find(item => item.id === id);
      if (!m || (m.status && m.status !== 'ready')) continue;
      getVideoEl(m); if (m.kind !== 'image') getAudioEl(m);
    }
    renderAll();
    publishPluginGenerationEvents();
    syncPluginBackgrounds();
    // keep the character modal's portrait grid in sync with live media
    if ($('#modal').classList.contains('on')) renderCharGrid();
    if (!playing) drawNow();
  } catch (e) { console.error('refresh', e); }finally{refreshPromise=null;}})();
  return refreshPromise;
}

function releaseMediaDecoders(){
  activeAudioIds.clear();
  for(const id of Object.keys(videoEls)){const el=videoEls[id];if(el.tagName==='IMG')continue;try{el.pause();el.removeAttribute('src');el.load();}catch(_){}el.remove();delete videoEls[id];}
  for(const id of Object.keys(audioEls)){const el=audioEls[id];try{el.pause();el.removeAttribute('src');el.load();}catch(_){}if(audioGains[id]){try{audioGains[id].disconnect();}catch(_){}}el.remove();delete audioEls[id];delete audioGains[id];}
  if(audioContext&&audioContext.state!=='closed'){try{audioContext.close();}catch(_){}}audioContext=null;
}
function scheduleRefreshPoll(){
  clearTimeout(refreshTimer);refreshTimer=0;
  if(document.hidden)return;
  refreshTimer=setTimeout(async()=>{if(!inspectorControlActive&&!clipSavePending)await refresh();scheduleRefreshPoll();},1500);
}
function handleVisibilityChange(){
  if(document.hidden){clearTimeout(refreshTimer);refreshTimer=0;if(playing)pause();releaseMediaDecoders();return;}
  refresh().finally(scheduleRefreshPoll);
}

/* ---------------- formatting ---------------- */
function fmtTime(s) {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60), sec = Math.floor(s % 60), ms = Math.floor((s % 1) * 1000);
  return String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0') + '.' + String(ms).padStart(3, '0');
}
function fmtDur(s) { s = s || 0; if (s < 60) return s.toFixed(1) + 's'; const m = Math.floor(s / 60); return m + 'm ' + (s % 60).toFixed(0) + 's'; }
function fmtEstimate(s){s=Math.max(0,Math.round(+s||0));if(s<60)return '< 1 min';const m=Math.round(s/60);if(m<60)return 'about '+m+' min';const h=Math.floor(m/60),r=m%60;return 'about '+h+'h'+(r?' '+r+'m':'');}
function fmtBytes(n) { return (n / 1073741824).toFixed(1) + ' GiB'; }
function fmtTick(t) { if (t < 60) return t.toFixed(t < 1 ? 1 : 0) + 's'; const m = Math.floor(t / 60), s = Math.round(t % 60); return m + ':' + String(s).padStart(2, '0'); }
function fmtDate(ts) { if (!ts) return ''; const d = new Date(ts * 1000); const now = new Date(); const sameYear = d.getFullYear() === now.getFullYear(); const opt = { day: 'numeric', month: 'short' }; if (!sameYear) opt.year = 'numeric'; return d.toLocaleDateString(undefined, opt); }

/* ---------------- lookups ---------------- */
function mediaById(id) { return state.media.find(m => m.id === id); }
function trackById(id) { return state.tracks.find(t => t.id === id); }
function findClip(id) { for (const t of state.tracks) { const c = t.clips.find(c => c.id === id); if (c) return c; } return null; }
function trackOfClip(c) { return state.tracks.find(t => t.clips.some(x => x.id === c.id)); }
function clipElById(id) { return document.querySelector('.clip[data-clip="' + id + '"]'); }
function timelineEnd() {
  let end = 0;
  for (const track of state.tracks) for (const clip of track.clips) {
    const start = Number(clip.start), duration = Number(clip.out) - Number(clip.in);
    const clipEnd = start + duration;
    // A queued/deleted clip can briefly arrive without complete timing data.
    // Never let that transient record turn the entire timeline duration into
    // NaN, which used to put the empty-state layer over a valid preview.
    if (Number.isFinite(clipEnd) && Number.isFinite(duration) && duration > 0) end = Math.max(end, clipEnd);
  }
  return end;
}
function hasVisualTimelineContent() {
  // The empty state describes whether the timeline has visual material, not
  // whether every piece of its asynchronously refreshed metadata is ready.
  // If a visual clip exists, keep this layer out of the canvas; otherwise a
  // temporary status/timing gap can cover a frame that is already displayed.
  return state.tracks.some(track => track.kind === 'video' && track.clips.length > 0);
}
function resolvedClipStart(track,movingId,desired,duration){
  desired=Math.max(0,+desired||0);duration=Math.max(.05,+duration||.05);const others=(track.clips||[]).filter(c=>c.id!==movingId),overlaps=start=>others.some(c=>start<c.start+(c.out-c.in)-.000001&&start+duration>c.start+.000001);
  if(!overlaps(desired))return desired;const candidates=[0];for(const c of others){const d=Math.max(.05,c.out-c.in);candidates.push(c.start+d,Math.max(0,c.start-duration));}return candidates.filter(start=>!overlaps(start)).sort((a,b)=>Math.abs(a-desired)-Math.abs(b-desired)||a-b)[0];
}
function niceStep(min) { const s = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120]; for (const x of s) if (x >= min) return x; return 120; }
function timelineCanvasEnd() {
  const scroll=$('#tlScroll'),viewportSeconds=Math.max(1,((scroll&&scroll.clientWidth)||900)-LANE_OFFSET)/pxPerSec;
  const breathingRoom=Math.max(10,viewportSeconds*.5);
  timelineViewSeconds=Math.max(timelineViewSeconds,30,viewportSeconds*2,timelineEnd()+breathingRoom);
  return timelineViewSeconds;
}
function extendTimelineCanvas() {
  const scroll=$('#tlScroll');if(!scroll||timelineExtendFrame)return;
  const threshold=Math.max(160,scroll.clientWidth*.2);
  if(scroll.scrollLeft+scroll.clientWidth<scroll.scrollWidth-threshold)return;
  timelineExtendFrame=requestAnimationFrame(()=>{timelineExtendFrame=0;const left=scroll.scrollLeft;timelineViewSeconds+=Math.max(30,scroll.clientWidth/pxPerSec);renderTimeline();scroll.scrollLeft=left;});
}

/* media absolute path -> served url */
function mediaUrl(m) {
  const url=mediaPathUrl(m&&m.src),version=m&&m.asset_uid;
  return url&&version?url+(url.includes('?')?'&':'?')+'v='+encodeURIComponent(version):url;
}
function mediaPathUrl(src) {
  const s = String(src || '');
  if (!s) return '';
  if (/^(?:https?:|blob:|data:)/.test(s)) return s;
  const k = Math.max(s.lastIndexOf('/uploads/'), s.lastIndexOf('/media/'));
  if (k >= 0) return s.slice(k);
  return '/' + s.replace(/^\.?\//, '');
}

/* ---------------- media elements ---------------- */
function getVideoEl(media) {
  let el = videoEls[media.id];
  if (!el) {
    el = media.kind === 'image' ? document.createElement('img') : document.createElement('video');
    if (media.kind !== 'image') { el.muted = true; el.playsInline = true; el.preload = 'auto'; }
    el.src = mediaUrl(media);
    if (media.kind === 'image') { el.addEventListener('load', () => { if (!playing) drawNow(); }); }
    pool.appendChild(el);
    videoEls[media.id] = el;
  }
  return el;
}
function getAudioEl(media) {
  let el = audioEls[media.id];
  if (!el) {
    el = document.createElement('audio');
    el.preload = 'auto'; el.src = mediaUrl(media); el.muted = false; el.playsInline = true;
    pool.appendChild(el);
    audioEls[media.id] = el;
    // Safari can report a playing hidden media element without delivering it
    // audibly. Route timeline sources through an explicit Web Audio graph;
    // the context is resumed synchronously from the Play user gesture.
    try {
      const AudioCtor = window.AudioContext || window.webkitAudioContext;
      if (AudioCtor) {
        if (!audioContext) audioContext = new AudioCtor();
        const source = audioContext.createMediaElementSource(el);
        const gain = audioContext.createGain();
        source.connect(gain); gain.connect(audioContext.destination);
        audioGains[media.id] = gain;
      }
    } catch (error) { console.warn('Web Audio fallback unavailable', error); }
  }
  return el;
}
function setAudioGain(mediaId, value) {
  const level = clamp(value, 0, 2), node = audioGains[mediaId], element = audioEls[mediaId];
  if (node && audioContext) {
    node.gain.setValueAtTime(level, audioContext.currentTime);
    if (element) element.volume = 1;
  } else if (element) element.volume = Math.min(1,level);
}
function seek(el, pos) {
  if (el.tagName === 'IMG') return;
  if (el.readyState >= 1 && isFinite(pos) && Math.abs(el.currentTime - pos) > 0.04) { try { el.currentTime = pos; } catch (e) {} }
}

/* ---------------- compositor ---------------- */
function drawCover(ctx, el, zoom, canvas, center) {
  const W = canvas.width, H = canvas.height;
  const vw = el.videoWidth || el.naturalWidth || W, vh = el.videoHeight || el.naturalHeight || H;
  const z = Math.max(0.1, zoom || 1);
  const cx = center ? center.x : 0.5, cy = center ? center.y : 0.5;
  const coverScale = Math.max(W / vw, H / vh);
  const f = coverScale * z;                 // total scale (z=1 -> plain cover)
  const sw = vw * f, sh = vh * f;           // scaled image size
  // position so the image point (cx, cy) lands on the canvas center
  ctx.drawImage(el, W / 2 - cx * sw, H / 2 - cy * sh, sw, sh);
}
function colorSettings(c){return {enabled:true,exposure:0,contrast:1,saturation:1,temperature:0,tint:0,highlights:0,shadows:0,...(c.color||{})};}
function clipCanvasFilter(c){
  const v=colorSettings(c),blur=c.blur&&c.blur.enabled!==false?clamp(+c.blur.amount||0,0,40):0;if(v.enabled===false)return blur?`blur(${blur}px)`:'none';
  const number=(value,fallback)=>Number.isFinite(+value)?+value:fallback;
  const brightness=clamp(Math.pow(2,number(v.exposure,0))*(1+number(v.highlights,0)*.12+number(v.shadows,0)*.08),.15,4);
  const sepia=Math.min(.22,Math.abs(+v.temperature||0)*.22),hue=(+v.temperature||0)*-12+(+v.tint||0)*18;
  return `brightness(${brightness}) contrast(${clamp(number(v.contrast,1),0,3)}) saturate(${clamp(number(v.saturation,1),0,3)}) sepia(${sepia}) hue-rotate(${hue}deg)${blur?` blur(${blur}px)`:''}`;
}
let nativeCanvasFilterSupport=null,colorLayerCanvas=null,colorLayerCtx=null;
function supportsNativeCanvasFilter(){
  if(nativeCanvasFilterSupport!==null)return nativeCanvasFilterSupport;
  try{const test=document.createElement('canvas');test.width=test.height=1;const x=test.getContext('2d');x.fillStyle='#fff';x.filter='brightness(0)';x.fillRect(0,0,1,1);nativeCanvasFilterSupport=x.getImageData(0,0,1,1).data[0]<16;}catch(error){nativeCanvasFilterSupport=false;}
  return nativeCanvasFilterSupport;
}
function hasColorCorrection(c){const v=colorSettings(c),contrast=Number.isFinite(+v.contrast)?+v.contrast:1,saturation=Number.isFinite(+v.saturation)?+v.saturation:1;return v.enabled!==false&&(Math.abs(+v.exposure||0)>.0001||Math.abs(contrast-1)>.0001||Math.abs(saturation-1)>.0001||Math.abs(+v.temperature||0)>.0001||Math.abs(+v.tint||0)>.0001||Math.abs(+v.highlights||0)>.0001||Math.abs(+v.shadows||0)>.0001);}
function pixelColorCorrect(canvas,c){
  const v=colorSettings(c),x=colorLayerCtx,w=canvas.width,h=canvas.height,image=x.getImageData(0,0,w,h),d=image.data;
  const brightness=clamp(Math.pow(2,+v.exposure||0)*(1+(+v.highlights||0)*.12+(+v.shadows||0)*.08),.15,4),contrast=clamp(Number.isFinite(+v.contrast)?+v.contrast:1,0,3),saturation=clamp(Number.isFinite(+v.saturation)?+v.saturation:1,0,3),temp=clamp(+v.temperature||0,-1,1)*34,tint=clamp(+v.tint||0,-1,1)*26;
  for(let i=0;i<d.length;i+=4){if(!d[i+3])continue;let r=d[i]*brightness,g=d[i+1]*brightness,b=d[i+2]*brightness;r=(r-128)*contrast+128;g=(g-128)*contrast+128;b=(b-128)*contrast+128;const l=.2126*r+.7152*g+.0722*b;r=l+(r-l)*saturation;g=l+(g-l)*saturation;b=l+(b-l)*saturation;d[i]=clamp(r+temp+tint*.45,0,255);d[i+1]=clamp(g-tint,0,255);d[i+2]=clamp(b-temp+tint*.45,0,255);}
  x.putImageData(image,0,0);
}
function pixelBoxBlur(canvas,amount){
  const ctx=colorLayerCtx,w=canvas.width,h=canvas.height,r=Math.max(1,Math.min(40,Math.round(+amount||0))),image=ctx.getImageData(0,0,w,h),src=image.data,tmp=new Uint8ClampedArray(src.length),dst=new Uint8ClampedArray(src.length),span=r*2+1;
  for(let y=0;y<h;y++)for(let channel=0;channel<4;channel++){let sum=0;for(let k=-r;k<=r;k++)sum+=src[(y*w+clamp(k,0,w-1))*4+channel];for(let x=0;x<w;x++){tmp[(y*w+x)*4+channel]=sum/span;sum-=src[(y*w+clamp(x-r,0,w-1))*4+channel];sum+=src[(y*w+clamp(x+r+1,0,w-1))*4+channel];}}
  for(let x=0;x<w;x++)for(let channel=0;channel<4;channel++){let sum=0;for(let k=-r;k<=r;k++)sum+=tmp[(clamp(k,0,h-1)*w+x)*4+channel];for(let y=0;y<h;y++){dst[(y*w+x)*4+channel]=sum/span;sum-=tmp[(clamp(y-r,0,h-1)*w+x)*4+channel];sum+=tmp[(clamp(y+r+1,0,h-1)*w+x)*4+channel];}}
  image.data.set(dst);ctx.putImageData(image,0,0);
}
function drawClipCover(ctx,c,el,motion){
  const pos=c.position||{},dx=(+pos.x||0)*state.canvas.width/100,dy=(+pos.y||0)*state.canvas.height/100;
  const hasBlur=!!(c.blur&&c.blur.enabled!==false&&+c.blur.amount>0),hasMask=!!(c.mask&&c.mask.enabled!==false&&c.mask.type&&c.mask.type!=='none');
  if((!hasColorCorrection(c)&&!hasBlur&&!hasMask)||((hasColorCorrection(c)||hasBlur)&&supportsNativeCanvasFilter()&&!hasMask)){ctx.save();ctx.filter=clipCanvasFilter(c);ctx.translate(dx,dy);drawCover(ctx,el,motion.zoom,state.canvas,motion.center);ctx.restore();return;}
  if(!colorLayerCanvas){colorLayerCanvas=document.createElement('canvas');colorLayerCtx=colorLayerCanvas.getContext('2d',{willReadFrequently:true});}
  if(colorLayerCanvas.width!==state.canvas.width||colorLayerCanvas.height!==state.canvas.height){colorLayerCanvas.width=state.canvas.width;colorLayerCanvas.height=state.canvas.height;}
  colorLayerCtx.clearRect(0,0,colorLayerCanvas.width,colorLayerCanvas.height);colorLayerCtx.save();colorLayerCtx.filter=clipCanvasFilter(c);colorLayerCtx.translate(dx,dy);drawCover(colorLayerCtx,el,motion.zoom,state.canvas,motion.center);colorLayerCtx.restore();
  try{const nativeFilters=supportsNativeCanvasFilter();if(hasColorCorrection(c)&&!nativeFilters)pixelColorCorrect(colorLayerCanvas,c);if(hasBlur&&!nativeFilters)pixelBoxBlur(colorLayerCanvas,c.blur.amount);if(hasMask)applyCanvasMask(colorLayerCtx,c.mask,colorLayerCanvas.width,colorLayerCanvas.height);ctx.drawImage(colorLayerCanvas,0,0);}catch(error){console.warn('clip effect preview',error);ctx.save();ctx.translate(dx,dy);drawCover(ctx,el,motion.zoom,state.canvas,motion.center);ctx.restore();}
}
function maskSettings(c){return {enabled:true,type:'none',x:50,y:50,width:70,height:70,invert:false,...(c.mask||{})};}
function applyCanvasMask(ctx,mask,W,H){
  const m={...maskSettings({mask})},cx=W*m.x/100,cy=H*m.y/100,w=W*m.width/100,h=H*m.height/100,x=cx-w/2,y=cy-h/2;
  ctx.save();ctx.globalCompositeOperation=m.invert?'destination-out':'destination-in';ctx.fillStyle='#fff';ctx.beginPath();
  if(m.type==='split')ctx.rect(0,0,clamp(cx,1,W),H);
  else if(m.type==='cinematic')ctx.rect(0,cy-h/2,W,h);
  else if(m.type==='ellipse'||m.type==='circle'){const rw=m.type==='circle'?Math.min(w,h)/2:w/2,rh=m.type==='circle'?Math.min(w,h)/2:h/2;ctx.ellipse(cx,cy,Math.max(1,rw),Math.max(1,rh),0,0,Math.PI*2);}
  else if(m.type==='diamond'){ctx.moveTo(cx,cy-h/2);ctx.lineTo(cx+w/2,cy);ctx.lineTo(cx,cy+h/2);ctx.lineTo(cx-w/2,cy);ctx.closePath();}
  else if(m.type==='heart'){ctx.moveTo(cx,cy+h*.42);ctx.bezierCurveTo(cx-w*.58,cy+h*.08,cx-w*.5,cy-h*.38,cx-w*.22,cy-h*.38);ctx.bezierCurveTo(cx,cy-h*.38,cx,cy-h*.16,cx,cy-h*.08);ctx.bezierCurveTo(cx,cy-h*.16,cx,cy-h*.38,cx+w*.22,cy-h*.38);ctx.bezierCurveTo(cx+w*.5,cy-h*.38,cx+w*.58,cy+h*.08,cx,cy+h*.42);ctx.closePath();}
  else if(m.type==='star'){const outer=Math.max(1,Math.min(w,h)/2),inner=outer*.45;for(let i=0;i<10;i++){const angle=-Math.PI/2+i*Math.PI/5,r=i%2?inner:outer,px=cx+Math.cos(angle)*r,py=cy+Math.sin(angle)*r*(h/Math.max(1,w));if(!i)ctx.moveTo(px,py);else ctx.lineTo(px,py);}ctx.closePath();}
  else ctx.rect(x,y,w,h);ctx.fill();ctx.restore();
}
function focusClipPreview(c){const duration=Math.max(.05,c.out-c.in),end=c.start+duration;if(playTime<c.start||playTime>=end)playTime=c.start+Math.min(.08,duration*.5);}

// motion presets for still images (the "Ken Burns" pan-and-zoom effect).
// Returns {zoom, center:{x,y}} at progress p in [0,1].
function motionState(motion, p) {
  p = clamp(p, 0, 1);
  const m = (motion && motion.type) || 'none';
  // zoom + center point over progress. Mirrors the export's zoompan presets.
  switch (m) {
    case 'push-in':   return { zoom: 1.0 + 0.4 * p, center: { x: 0.5, y: 0.5 } };
    case 'pull-out':  return { zoom: 1.4 - 0.4 * p, center: { x: 0.5, y: 0.5 } };
    case 'pan-left':  return { zoom: 1.3, center: { x: 0.6 - 0.2 * p, y: 0.5 } };
    case 'pan-right': return { zoom: 1.3, center: { x: 0.4 + 0.2 * p, y: 0.5 } };
    case 'pan-up':    return { zoom: 1.3, center: { x: 0.5, y: 0.6 - 0.2 * p } };
    case 'pan-down':  return { zoom: 1.3, center: { x: 0.5, y: 0.4 + 0.2 * p } };
    case 'none':
    default:          return { zoom: 1.0, center: { x: 0.5, y: 0.5 } };
  }
}

function clipMotion(clip, localT) {
  const dur = Math.max(0.05, (clip.out - clip.in));
  const p = clamp(localT / dur, 0, 1);
  const points = transformPoints(clip);
  if (points.length) {
    let a = points[0], b = points[points.length-1];
    for (let i=0;i<points.length-1;i++) if (p >= points[i].at && p <= points[i+1].at) { a=points[i]; b=points[i+1]; break; }
    if (p <= points[0].at) a=b=points[0];
    if (p >= points[points.length-1].at) a=b=points[points.length-1];
    const segment = a === b ? 0 : clamp((p-a.at)/Math.max(.0001,b.at-a.at),0,1);
    const ease = segment * segment * (3 - 2 * segment);
    const av = (key, fallback) => Number.isFinite(+a[key]) ? +a[key] : fallback;
    const bv = (key, fallback) => Number.isFinite(+b[key]) ? +b[key] : fallback;
    return { zoom: Math.max(.1, (clip.zoom || 1) * (av('zoom',1) + (bv('zoom',1) - av('zoom',1)) * ease)),
      center: { x: clamp(av('x',.5) + (bv('x',.5) - av('x',.5)) * ease, 0, 1),
        y: clamp(av('y',.5) + (bv('y',.5) - av('y',.5)) * ease, 0, 1) } };
  }
  const st = motionState(clip.motion, p);
  // The user's base zoom may be below 1 to intentionally reveal space around
  // an image. Motion presets scale relative to that chosen size.
  const zoom = Math.max(0.1, (clip.zoom || 1) * st.zoom);
  return { zoom, center: st.center };
}

function transformPoints(clip) {
  const k = clip && clip.keyframes; if (!k) return [];
  if(k.enabled===false)return [];
  return storedTransformPoints(clip);
}
function storedTransformPoints(clip) {
  const k = clip && clip.keyframes; if (!k) return [];
  const base = Math.max(.1,+clip.zoom||1);
  const raw = Array.isArray(k.points) ? k.points : (k.start && k.end ? [{...k.start,zoom:(+k.start.zoom||base)/base,id:k.start.id||'start',at:Number.isFinite(+k.start.at)?+k.start.at:0},{...k.end,zoom:(+k.end.zoom||base)/base,id:k.end.id||'end',at:Number.isFinite(+k.end.at)?+k.end.at:1}] : []);
  return raw.map((p,i)=>({id:p.id||('kf'+i),at:clamp(+p.at||0,0,1),zoom:Number.isFinite(+p.zoom)?+p.zoom:1,x:Number.isFinite(+p.x)?+p.x:.5,y:Number.isFinite(+p.y)?+p.y:.5})).sort((a,b)=>a.at-b.at);
}

function ensureTransformPoints(c) {
  const points = transformPoints(c); c.keyframes = points.length ? {points} : null; return points;
}
function splitTransformKeyframes(c,splitRatio){
  const points=transformPoints(c);if(!points.length)return [null,null];const p=clamp(splitRatio,.0001,.9999);
  const sample=at=>{let a=points[0],b=points[points.length-1];for(let i=0;i<points.length-1;i++)if(at>=points[i].at&&at<=points[i+1].at){a=points[i];b=points[i+1];break;}if(at<=points[0].at)a=b=points[0];if(at>=points[points.length-1].at)a=b=points[points.length-1];const q=a===b?0:clamp((at-a.at)/Math.max(.0001,b.at-a.at),0,1),ease=q*q*(3-2*q),mix=(key,fallback)=>(Number.isFinite(+a[key])?+a[key]:fallback)+((Number.isFinite(+b[key])?+b[key]:fallback)-(Number.isFinite(+a[key])?+a[key]:fallback))*ease;return {zoom:mix('zoom',1),x:mix('x',.5),y:mix('y',.5)};};
  const boundary=sample(p),stamp=Date.now();
  const left=[...points.filter(x=>x.at<p-.00001).map(x=>({...x,at:x.at/p})),{id:'kf-split-left-'+stamp,at:1,...boundary}];
  const right=[{id:'kf-split-right-'+stamp,at:0,...boundary},...points.filter(x=>x.at>p+.00001).map(x=>({...x,id:x.id+'-right-'+stamp,at:(x.at-p)/(1-p)}))];
  return [{points:left},{points:right}];
}
function transitionItems(c){
  const tr=c.transition||{};
  if(Array.isArray(tr.items)) return tr.items.filter(x=>x&&x.type&&x.type!=='cut');
  if(tr.type&&tr.type!=='cut'&&+tr.dur>0) return [{id:tr.id||'tr-legacy-'+c.id,type:tr.type,edge:tr.edge||'start',dur:+tr.dur,enabled:tr.enabled!==false}];
  return [];
}
function transitionPayload(items){return {items:items.map(x=>({...x,dur:+x.dur||0,enabled:x.enabled!==false}))};}
function enabledTransitions(c,edge){return transitionItems(c).filter(x=>x.enabled!==false&&(x.edge||'start')===edge&&+x.dur>0);}
function clipHasAnimation(c){return transformPoints(c).length>0||transitionItems(c).length>0;}
function drawContain(ctx, el, zoom, canvas) {
  const W = canvas.width, H = canvas.height;
  const vw = el.videoWidth || el.naturalWidth || W, vh = el.videoHeight || el.naturalHeight || H;
  const z = Math.max(0.1, zoom || 1);
  const dw = W * z, dh = dw * (vh / vw);
  ctx.drawImage(el, (W - dw) / 2, (H - dh) / 2, dw, dh);
}

function drawFrame(t) {
  const cv = $('#preview');
  if (cv.width !== state.canvas.width || cv.height !== state.canvas.height) { cv.width = state.canvas.width; cv.height = state.canvas.height; }
  // Color correction is applied inside drawClipCover while that clip's canvas
  // state is isolated. Never grade the final canvas element: doing so would
  // affect transparent space and every composited track.
  cv.style.filter='none';
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, cv.width, cv.height);
  // A muted video lane is removed from the visual stack as well as the audio
  // mix. Rebuild the stack from only visible lanes so the next lane below is
  // revealed immediately at the current playhead position.
  const vtracks = state.tracks.filter(x => x.kind === 'video' && !x.muted);
  // Timeline order is camera order: the first (top) video lane must be the
  // last layer painted. Use the lowest occupied lane as the base, then paint
  // upward so moving a lane also changes its visual stacking predictably.
  const base = vtracks.slice().reverse().find(x => x.clips.length) || null;
  if (base) drawBaseTrack(ctx, base, t);
  for (let i = vtracks.length - 1; i >= 0; i--) if (vtracks[i] !== base) drawOverlayTrack(ctx, vtracks[i], t);
}

function drawBaseTrack(ctx, base, t) {
  const clips = base.clips.slice().sort((a, b) => a.start - b.start);
  let cur = null, prev = null;
  for (let i = 0; i < clips.length; i++) {
    const c = clips[i];
    if (t >= c.start - 1e-6 && t < c.start + (c.out - c.in)) { cur = c; prev = clips[i - 1] || null; break; }
  }
  if (!cur) return;
  const media = mediaById(cur.mediaId); if (!media) return;
  const el = getVideoEl(media);
  const localT = t - cur.start;
  const srcPos = cur.in + localT;
  const curM = clipMotion(cur, localT); // motion-aware zoom + center (still images)
  const startTransitions=enabledTransitions(cur,'start'),endTransitions=enabledTransitions(cur,'end');
  const td=startTransitions.length?Math.max(...startTransitions.map(x=>+x.dur||0)):0;
  const endDur=endTransitions.length?Math.max(...endTransitions.map(x=>+x.dur||0)):0;
  // crossfade with the previous (adjacent) base clip during the incoming window
  if (td > 0 && prev) {
    const pd = prev.out - prev.in;
    if (prev.start + pd >= cur.start - 0.05 && localT < td) {
      const xalpha = localT / td;
      const pm = mediaById(prev.mediaId);
      if (pm) {
        const pel = getVideoEl(pm);
        seek(pel, prev.in + pd - (td - localT)); // outgoing tail
        const prevM = clipMotion(prev, pd);
        ctx.globalAlpha = 1 - xalpha; drawClipCover(ctx,prev,pel,prevM);
      }
      seek(el, srcPos);
      ctx.globalAlpha = xalpha; drawClipCover(ctx,cur,el,curM);
      ctx.globalAlpha = 1;
      return;
    }
  }
  seek(el, srcPos);
  if(td>0&&localT<td)ctx.globalAlpha=Math.max(0,localT/td);
  if(endDur>0&&localT>(cur.out-cur.in)-endDur)ctx.globalAlpha=Math.min(ctx.globalAlpha,Math.max(0,((cur.out-cur.in)-localT)/endDur));
  drawClipCover(ctx,cur,el,curM);ctx.globalAlpha=1;
}

function drawOverlayTrack(ctx, tr, t) {
  for (const c of tr.clips) {
    if (t < c.start || t >= c.start + (c.out - c.in)) continue;
    const m = mediaById(c.mediaId); if (!m) continue;
    const el = getVideoEl(m);
    seek(el, c.in + (t - c.start));
    const cm = clipMotion(c, t - c.start);
    const local=t-c.start,d=c.out-c.in,starts=enabledTransitions(c,'start'),ends=enabledTransitions(c,'end'),sd=starts.length?Math.max(...starts.map(x=>+x.dur||0)):0,ed=ends.length?Math.max(...ends.map(x=>+x.dur||0)):0;
    if(sd&&local<sd)ctx.globalAlpha=Math.max(0,local/sd);if(ed&&local>d-ed)ctx.globalAlpha=Math.min(ctx.globalAlpha,Math.max(0,(d-local)/ed));drawClipCover(ctx,c,el,cm);ctx.globalAlpha=1;
  }
}

/* ---------------- audio ---------------- */
function audioSources(t) {
  const out = [];
  const vtracks = state.tracks.filter(x => x.kind === 'video');
  const base = vtracks.slice().reverse().find(x => x.clips.length) || null;
  for (const tr of vtracks) {
    const clips = tr.clips.slice().sort((a, b) => a.start - b.start);
    for (let i = 0; i < clips.length; i++) {
      const c = clips[i]; const d = c.out - c.in;
      if (t < c.start || t >= c.start + d) continue;
      const m = mediaById(c.mediaId);
      if (!m || m.kind === 'image' || !m.hasAudio) continue;
      const localT = t - c.start;
      let gain = 1;
      if (tr === base) {
        const incoming=enabledTransitions(c,'start');const tin=incoming.length?Math.max(...incoming.map(x=>+x.dur||0)):0;
        const nxt = clips[i + 1];
        const outgoing=enabledTransitions(c,'end');const nextIncoming=nxt?enabledTransitions(nxt,'start'):[];const tout=Math.max(outgoing.length?Math.max(...outgoing.map(x=>+x.dur||0)):0,nextIncoming.length?Math.max(...nextIncoming.map(x=>+x.dur||0)):0);
        if (tin > 0) gain = Math.min(gain, localT / tin);
        if (tout > 0) gain = Math.min(gain, (d - localT) / tout);
      }
      const fades=c.audioFade||{},fadeIn=clamp(+fades.in||0,0,d),fadeOut=clamp(+fades.out||0,0,d);
      gain*=clamp(Number.isFinite(+c.volume)?+c.volume:1,0,2);
      if(fadeIn>0)gain=Math.min(gain,localT/fadeIn);
      if(fadeOut>0)gain=Math.min(gain,(d-localT)/fadeOut);
      const on = !globalMute && !tr.muted && !c.muted && !c.detached;
      out.push({ mediaId: c.mediaId, pos: c.in + localT, gain, on });
    }
  }
  for (const tr of state.tracks.filter(x => x.kind === 'audio')) {
    for (const c of tr.clips) {
      const d = c.out - c.in; if (t < c.start || t >= c.start + d) continue;
      const m = mediaById(c.mediaId); if (!m || m.kind === 'image') continue;
      const localT=t-c.start,fades=c.audioFade||{},fadeIn=clamp(+fades.in||0,0,d),fadeOut=clamp(+fades.out||0,0,d);
      let gain=1;if(fadeIn>0)gain=Math.min(gain,localT/fadeIn);if(fadeOut>0)gain=Math.min(gain,(d-localT)/fadeOut);
      gain*=clamp(Number.isFinite(+c.volume)?+c.volume:1,0,2);
      const on = !globalMute && !tr.muted && !c.muted;
      out.push({ mediaId: c.mediaId, pos: c.in + localT, gain, on });
    }
  }
  return out;
}

function updateAudio(t) {
  const srcs = audioSources(t);
  const active = new Set();
  for (const s of srcs) {
    const m = mediaById(s.mediaId); if (!m) continue;
    const a = getAudioEl(m);
    setAudioGain(s.mediaId, s.on ? s.gain : 0);
    if (playing) {
      active.add(s.mediaId);
      // Once started, let the media element's native audio clock run freely.
      // Re-seeking after a slow canvas/UI frame makes Safari decode again and
      // produces audible stutters. Explicit timeline seeks clear the set.
      if (!activeAudioIds.has(s.mediaId)) {
        try { a.currentTime = s.pos; } catch (e) {}
      }
      if (a.paused) a.play().catch(() => {});
    } else if (!a.paused) a.pause();
  }
  if (playing) for (const id in audioEls) if (!active.has(id) && !audioEls[id].paused) audioEls[id].pause();
  activeAudioIds = playing ? active : new Set();
}

/* ---------------- main loop ---------------- */
function tick() {
  // reschedule first so a transient error can never kill the render loop
  rafId = requestAnimationFrame(tick);
  const now = performance.now();
  const dt = (now - lastNow) / 1000; lastNow = now;
  if (!state) return;
  try {
    if (playing) {
      playTime += dt;
      const total = timelineEnd();
      if (playTime >= total) { if (loop) { playTime = 0; activeAudioIds.clear(); } else { playTime = total; pause(); } }
    }
    drawFrame(playTime);
    updateAudio(playTime);
    updatePlayhead();
    updateTimecode();
  } catch (e) { console.error('tick', e); }
}
function updatePlayhead() { $('#playhead').style.left = (LANE_OFFSET + playTime * pxPerSec) + 'px'; }
function updateTimecode() { $('#timecode').innerHTML = fmtTime(playTime) + ' <small>/ ' + fmtTime(timelineEnd()) + '</small>'; }
function drawNow() { drawFrame(playTime); updateAudio(playTime); updatePlayhead(); updateTimecode(); }

/* ---------------- transport ---------------- */
function unlockAudio() {
  // Browsers only allow media.play() after a user gesture. The play button
  // click is that gesture, so start (then let the loop steer) every active
  // audio element here, synchronously.
  const srcs = audioSources(playTime);
  if (audioContext && audioContext.state !== 'running') audioContext.resume().catch(error => console.warn('Audio context resume failed', error));
  for (const s of srcs) {
    const m = mediaById(s.mediaId); if (!m) continue;
    const a = getAudioEl(m);
    setAudioGain(s.mediaId, s.on ? s.gain : 0);
    try { a.currentTime = s.pos; } catch (e) {}
    if (s.on) { activeAudioIds.add(s.mediaId); const pr = a.play(); if (pr) pr.catch(() => {}); }
    else if (!a.paused) a.pause();
  }
}
function play() {
  if (!timelineEnd()) return;
  drawNow();
  if (playTime >= timelineEnd() - 0.01) playTime = 0;
  playing = true; lastNow = performance.now();
  unlockAudio();
  $('#playBtn').classList.add('on');
  $('#playIcon').innerHTML = '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>';
  $('#previewEmpty').style.display = 'none';
}
function pause() {
  playing = false;
  $('#playBtn').classList.remove('on');
  $('#playIcon').innerHTML = '<path d="M8 5v14l11-7z"/>';
  for (const id in audioEls) if (!audioEls[id].paused) audioEls[id].pause();
  activeAudioIds.clear();
}
function togglePlay() { playing ? pause() : play(); }
function syncGlobalMuteButton() {
  const button = $('#muteBtn');
  button.classList.toggle('on', globalMute);
  button.setAttribute('aria-pressed', String(globalMute));
  button.title = globalMute ? 'Turn audio on' : 'Mute all audio';
  button.innerHTML = globalMute
    ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5L6 9H2v6h4l5 4zM23 9l-6 6M17 9l6 6"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5L6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 010 7M18.5 5.5a9 9 0 010 13"/></svg>';
}
function stepFrame(d) { pause(); playTime = clamp(playTime + d / 24, 0, timelineEnd()); }
function seekTo(t) { playTime = clamp(t, 0, timelineEnd()); activeAudioIds.clear(); }

/* ---------------- timeline render ---------------- */
function renderTimeline() {
  const body = $('#tlBody');
  const total = timelineCanvasEnd();
  const width = LANE_OFFSET + total * pxPerSec + 120;
  body.style.width = width + 'px';

  // ruler
  const ruler = $('#tlRuler');
  ruler.style.width = width + 'px';
  ruler.innerHTML = '';
  ruler.onpointerdown = onPlayheadDown;
  const step = niceStep(70 / pxPerSec);
  for (let t = 0; t <= total + 0.001; t += step) {
    const tick = div('tick'); tick.style.left = (LANE_OFFSET + t * pxPerSec) + 'px';
    const lab = document.createElement('label'); lab.textContent = fmtTick(t); tick.appendChild(lab);
    ruler.appendChild(tick);
  }
  const phhit = div('phhit'); phhit.style.left = (LANE_OFFSET + playTime * pxPerSec) + 'px';
  ruler.appendChild(phhit);

  // rows
  const rows = $('#tlRows'); rows.innerHTML = '';
  for (const tr of state.tracks) {
    const hasTransform = tr.kind === 'video' && tr.clips.some(clipHasAnimation);
    const row = div('tlrow' + (tr.kind === 'audio' ? ' audio' : '') + (hasTransform ? ' has-keyframes' : '')+(tr.muted?' muted':''));
    if(tr.kind==='video'){
      const automationRows=Math.max(0,...tr.clips.map(c=>transformPoints(c).length?1+transitionItems(c).length:transitionItems(c).length));
      if(automationRows)row.style.minHeight=(58+automationRows*23)+'px';
    }
    row.dataset.track = tr.id;
    const head = div('tlhead');
    head.title = 'Drag to reorder this lane';
    const nm = div('nm'); nm.textContent = tr.name; head.appendChild(nm);
    nm.title = 'Double-click to rename lane';
    nm.addEventListener('pointerdown',e=>{e.stopPropagation();onTrackDown(e,tr,row);});
    nm.addEventListener('dblclick',e=>{e.stopPropagation();const name=prompt('Rename lane',tr.name);if(name&&name.trim()&&name.trim()!==tr.name)api('/api/tracks/'+tr.id,{method:'PUT',body:{name:name.trim()}}).then(refresh).catch(err=>toast(err.message,'err'));});
    const ctl = div('ctl');
    const mute = document.createElement('button');
    mute.textContent = 'M'; mute.title = 'Mute track';
    if (tr.muted) mute.classList.add('on');
    mute.addEventListener('click', async () => {
      const next = !tr.muted;
      tr.muted = next; mute.classList.toggle('on', next);row.classList.toggle('muted',next);
      drawNow();
      try { await api('/api/tracks/' + tr.id, { method: 'PUT', body: { muted: next } }); } catch (e) { toast(e.message, 'err'); }
    });
    ctl.appendChild(mute);
    const CORE = ['V1','V2','A1'];
    if (!CORE.includes(tr.id)) {
      const rm = document.createElement('button');
      rm.textContent = '×'; rm.className='laneRemove'; rm.title = 'Remove track (and its clips)';
      rm.addEventListener('click', async () => {
        if (!confirm('Remove track "' + tr.name + '" and its clips?')) return;
        try { await api('/api/tracks/' + tr.id, { method: 'DELETE' }); toast('Removed ' + tr.name, 'ok'); refresh(); }
        catch (e) { toast(e.message, 'err'); }
      });
      ctl.appendChild(rm);
    }
    head.appendChild(ctl);ctl.addEventListener('pointerdown',e=>e.stopPropagation());
    const resize=document.createElement('button');resize.className='laneHeaderResize';resize.title='Drag to widen lane names';resize.setAttribute('aria-label','Resize lane headers');resize.addEventListener('pointerdown',beginLaneHeaderResize);head.appendChild(resize);row.appendChild(head);
    head.addEventListener('pointerdown', (e) => onTrackDown(e, tr, row));

    const lane = div('tlane');
    lane.dataset.track = tr.id; lane.dataset.kind = tr.kind;
    const grid = div('grid'); lane.appendChild(grid);
    for (const c of tr.clips) lane.appendChild(renderClip(tr, c));
    lane.addEventListener('dragover', (e) => { if (dragMedia) { e.preventDefault(); lane.classList.add('dropzone'); } });
    lane.addEventListener('dragleave', () => lane.classList.remove('dropzone'));
    lane.addEventListener('drop', (e) => onLaneDrop(e, tr, lane));
    lane.addEventListener('pointerdown', (e) => {
      if (e.target === lane || e.target.classList.contains('grid')) seekFromTimelinePointer(e, lane);
    });
    row.appendChild(lane); rows.appendChild(row);
  }
  updatePlayhead();
}

function beginLaneHeaderResize(e){
  e.preventDefault();e.stopPropagation();const start=e.clientX,initial=LANE_OFFSET;document.body.classList.add('resizing-lane-headers');
  const move=ev=>{LANE_OFFSET=clamp(initial+ev.clientX-start,96,320);document.body.style.setProperty('--lane-head-w',LANE_OFFSET+'px');renderTimeline();};
  const up=()=>{saveProjectLayout();document.body.classList.remove('resizing-lane-headers');window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);};
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);
}

function onTrackDown(e, tr, row) {
  if (e.target.closest('button')) return;
  e.preventDefault(); dragTrack = tr; row.classList.add('track-dragging');
  const ghost = div('track-drag-ghost'); ghost.textContent = '⠿  ' + tr.name; document.body.appendChild(ghost);
  const placeGhost = ev => { ghost.style.left = (ev.clientX + 14) + 'px'; ghost.style.top = (ev.clientY + 10) + 'px'; };
  placeGhost(e);
  let targetTrack = tr;
  const move = (ev) => {
    placeGhost(ev);
    $$('.tlrow.track-drop').forEach(r => r.classList.remove('track-drop'));
    const under = document.elementFromPoint(ev.clientX, ev.clientY);
    const candidateRow = under && under.closest('.tlrow');
    const candidate = candidateRow && state.tracks.find(x => x.id === candidateRow.dataset.track);
    if (candidate && candidate.id !== tr.id && candidate.kind === tr.kind) { targetTrack = candidate; candidateRow.classList.add('track-drop'); }
  };
  const up = async () => {
    window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
    $$('.tlrow').forEach(r => r.classList.remove('track-dragging','track-drop'));
    ghost.remove();
    if (targetTrack.id !== tr.id) {
      const targetIndex = state.tracks.findIndex(x => x.id === targetTrack.id);
      try { await api('/api/tracks/' + tr.id, {method:'PUT',body:{index:targetIndex}}); toast('Lane reordered', 'ok'); }
      catch (err) { toast(err.message, 'err'); }
    }
    dragTrack = null; refresh();
  };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
}

async function waveformPeaks(m) {
  if (!m) return [];
  if (waveformCache[m.id]) return waveformCache[m.id];
  waveformCache[m.id] = (async () => {
    try {
      const response=await fetch(mediaUrl(m)),buffer=await response.arrayBuffer(),Ctor=window.AudioContext||window.webkitAudioContext;
      if(!Ctor)return null;if(!audioContext)audioContext=new Ctor();const decoded=await audioContext.decodeAudioData(buffer.slice(0)),channels=Array.from({length:decoded.numberOfChannels},(_,i)=>decoded.getChannelData(i)),count=Math.max(320,Math.min(2400,Math.ceil(decoded.duration*48))),peaks=[];
      for(let i=0;i<count;i++){const from=Math.floor(i*decoded.length/count),to=Math.max(from+1,Math.floor((i+1)*decoded.length/count));let peak=0;for(const data of channels)for(let p=from;p<to;p+=Math.max(1,Math.floor((to-from)/18)))peak=Math.max(peak,Math.abs(data[p]||0));peaks.push(peak);}
      return {peaks,duration:decoded.duration};
    }catch(error){console.warn('Waveform decode failed',error);return null;}
  })();return waveformCache[m.id];
}
async function renderWaveform(container,m,c){
  const waveform=await waveformPeaks(m);if(!container.isConnected||!waveform||!waveform.peaks.length)return;const peaks=waveform.peaks,duration=Math.max(.001,+waveform.duration||+m.duration||c.out),from=clamp(c.in/duration,0,1),to=clamp(c.out/duration,from,1),start=Math.floor(from*peaks.length),end=Math.max(start+1,Math.ceil(to*peaks.length)),visible=peaks.slice(start,end),bars=Math.min(180,visible.length),svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',`0 0 ${bars*3} 40`);svg.setAttribute('preserveAspectRatio','none');
  for(let i=0;i<bars;i++){const p=visible[Math.min(visible.length-1,Math.floor(i*visible.length/bars))]||0,h=Math.max(2,p*36),r=document.createElementNS(svg.namespaceURI,'rect');r.setAttribute('x',String(i*3));r.setAttribute('y',String(20-h/2));r.setAttribute('width','1.7');r.setAttribute('height',String(h));r.setAttribute('rx','.8');svg.appendChild(r);}container.replaceChildren(svg);
}

function renderClip(tr, c) {
  const m = mediaById(c.mediaId);
  const el = div('clip ' + (tr.kind === 'audio' ? 'audio' : (tr.id === 'V1' ? 'video' : 'overlay')));
  el.dataset.clip = c.id;
  el.style.left = (c.start * pxPerSec) + 'px'; // lane-relative (header already offsets the lane)
  el.style.width = Math.max(6, (c.out - c.in) * pxPerSec) + 'px';
  if (sel && sel.type === 'clip' && sel.id === c.id) el.classList.add('sel');
  if (transitionItems(c).some(x=>x.enabled!==false)) { const b = div('trbadge'); el.appendChild(b); }
  if (c.muted) { const mf = div('mflag'); mf.textContent = 'M'; el.appendChild(mf); }
  const body = div('body');
  if (tr.kind === 'audio') {
    const w = div('wave'); body.appendChild(w); renderWaveform(w,m,c);
    const fades=c.audioFade||{},duration=Math.max(.05,c.out-c.in);
    w.style.setProperty('--fade-in',(clamp(+fades.in||0,0,duration)/duration*100)+'%');
    w.style.setProperty('--fade-out',(clamp(+fades.out||0,0,duration)/duration*100)+'%');
  }
  const cn = div('cn'); cn.textContent = (m ? m.name : '?') + (c.detached ? ' · audio' : ''); body.appendChild(cn);
  if (tr.kind !== 'audio') { const cd = div('cd'), clipZoom = Number.isFinite(+c.zoom) ? +c.zoom : 1; cd.textContent = fmtDur(c.out - c.in) + (clipZoom !== 1 ? ' · ' + clipZoom.toFixed(1) + '×' : ''); body.appendChild(cd); }
  el.appendChild(body);
  const clipPoints = tr.kind === 'video' ? transformPoints(c) : [];
  const clipTransitions = tr.kind==='video'?transitionItems(c):[];
  const automationRows=(clipPoints.length?1:0)+clipTransitions.length;if(automationRows)body.style.bottom=(4+automationRows*23)+'px';
  if (clipPoints.length) {
    const rail = div('keyframeRail transformRail');
    rail.style.bottom=(6+clipTransitions.length*23)+'px';
    const label = div('keyframeLabel');
    const a=clipPoints[0],b=clipPoints[clipPoints.length-1],positionChanged=Math.abs((+a.x||.5)-(+b.x||.5))>.001||Math.abs((+a.y||.5)-(+b.y||.5))>.001;
    label.textContent='Transform · Zoom '+(+a.zoom||1).toFixed(2)+'× → '+(+b.zoom||1).toFixed(2)+'×'+(positionChanged?' · Position':''); rail.appendChild(label);
    const clipPx = Math.max(6,(c.out-c.in)*pxPerSec); const stackedAt = [];
    for (const point of clipPoints) {
      const diamond = document.createElement('button'); diamond.className = 'timelineKeyframe';
      if (selectedKeyframe && selectedKeyframe.clipId === c.id && selectedKeyframe.pointId === point.id) diamond.classList.add('selected');
      diamond.title = 'Transform · ' + point.zoom.toFixed(2) + '× · drag freely';
      diamond.style.left = (point.at * 100) + '%';
      const near = stackedAt.filter(at => Math.abs(at-point.at)*clipPx < 14).length; stackedAt.push(point.at);
      diamond.style.setProperty('--keyframe-stack',String(near));
      diamond.style.zIndex = String(7 + near);
      diamond.addEventListener('pointerdown', e => onKeyframeDown(e, c, point.id, diamond)); rail.appendChild(diamond);
    }
    el.appendChild(rail);
  }
  clipTransitions.forEach((item,index)=>{
    const rail=div('keyframeRail transitionRail'+(item.enabled===false?' disabled':''));rail.dataset.transitionId=item.id;rail.style.bottom=(6+index*23)+'px';
    const ratio=clamp(+item.dur/Math.max(.05,c.out-c.in),0,1),at=(item.edge||'start')==='end'?1-ratio:ratio;
    rail.style.setProperty('--transition-start',(item.edge||'start')==='end'?(at*100)+'%':'0%');rail.style.setProperty('--transition-size',(ratio*100)+'%');
    const label=div('keyframeLabel');label.textContent=((item.edge||'start')==='end'?'End':'Start')+' · '+item.type+' · '+(+item.dur).toFixed(2)+'s'+(item.enabled===false?' · off':'');rail.appendChild(label);
    const diamond=document.createElement('button');diamond.className='timelineKeyframe transitionKeyframe';
    if(selectedTransition&&selectedTransition.clipId===c.id&&selectedTransition.transitionId===item.id)diamond.classList.add('selected');
    diamond.title=((item.edge||'start')==='end'?'End':'Start')+' '+item.type+' · drag duration';diamond.style.left=(at*100)+'%';
    diamond.addEventListener('pointerdown',e=>onTransitionKeyframeDown(e,c,item.id,diamond));rail.appendChild(diamond);el.appendChild(rail);
  });
  const tl = div('trim l'); el.appendChild(tl);
  const trr = div('trim r'); el.appendChild(trr);

  el.addEventListener('pointerdown', (e) => onClipDown(e, tr, c, el));
  tl.addEventListener('pointerdown', (e) => onTrimDown(e, tr, c, 'l'));
  trr.addEventListener('pointerdown', (e) => onTrimDown(e, tr, c, 'r'));
  // double-click to rename (scene-backed clips rename the scene, which syncs
  // the media + timeline label; other clips rename their media)
  el.addEventListener('dblclick', (e) => { e.preventDefault(); renameClip(c, m); });
  return el;
}

function onTransitionKeyframeDown(e,c,transitionId,diamond){
  const items=transitionItems(c).map(x=>({...x})),item=items.find(x=>x.id===transitionId);if(!item)return;
  e.preventDefault();e.stopPropagation();selectedTransition={clipId:c.id,transitionId};selectedKeyframe=null;select({type:'clip',id:c.id});diamond.classList.add('selected');
  const rail=diamond.closest('.transitionRail'),clip=diamond.closest('.clip'),clipDur=Math.max(.05,c.out-c.in),edge=item.edge||'start';
  const dragWidth=Math.max(24,(clip&&clip.getBoundingClientRect().width)||rail.getBoundingClientRect().width||clipDur*pxPerSec),pointerId=e.pointerId;
  let lastX=e.clientX,ratio=clamp((+item.dur||0)/clipDur,0,1),pendingDelta=0,raf=0;diamond.classList.add('dragging');document.body.classList.add('dragging-keyframe');
  const paint=()=>{raf=0;ratio=clamp(ratio+(edge==='end'?-pendingDelta:pendingDelta)/dragWidth,0,1);pendingDelta=0;const dur=ratio*clipDur,at=edge==='end'?1-ratio:ratio;item.dur=dur;c.transition=transitionPayload(items);diamond.style.left=(at*100)+'%';rail.style.setProperty('--transition-start',edge==='end'?(at*100)+'%':'0%');rail.style.setProperty('--transition-size',(ratio*100)+'%');const label=rail.querySelector('.keyframeLabel');if(label)label.textContent=(edge==='end'?'End':'Start')+' · '+item.type+' · '+dur.toFixed(2)+'s'+(item.enabled===false?' · off':'');playTime=c.start+(edge==='end'?clipDur-dur:dur);drawNow();};
  const move=ev=>{if(ev.pointerId!==pointerId||!Number.isFinite(ev.clientX))return;const raw=ev.clientX-lastX;lastX=ev.clientX;if(Math.abs(raw)>Math.max(120,dragWidth*.75))return;pendingDelta+=clamp(raw,-48,48);if(!raf)raf=requestAnimationFrame(paint)};
  const up=()=>{if(raf){cancelAnimationFrame(raf);paint()}diamond.classList.remove('dragging');document.body.classList.remove('dragging-keyframe');window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);putClip(c,{transition:c.transition});};window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);
}

function onKeyframeDown(e, c, pointId, diamond) {
  e.preventDefault(); e.stopPropagation();
  if (document.activeElement && $('#inspector').contains(document.activeElement)) document.activeElement.blur();
  selectedTransition=null;
  const clicked = transformPoints(c).find(p=>p.id===pointId);
  if (clicked) { playTime=c.start+clicked.at*Math.max(.05,c.out-c.in); drawNow(); }
  selectedKeyframe={clipId:c.id,pointId}; revealSelectedKeyframe=true; $$('.timelineKeyframe').forEach(x=>x.classList.toggle('selected',x===diamond)); select({type:'clip',id:c.id});
  if (diamond.setPointerCapture) { try { diamond.setPointerCapture(e.pointerId); } catch (_) {} }
  ensureTransformPoints(c); const rail = diamond.closest('.keyframeRail'); const point = c.keyframes.points.find(p=>p.id===pointId); if (!point) return;
  diamond.classList.add('dragging'); document.body.classList.add('dragging-keyframe');
  const grabOffset = e.clientX - (diamond.getBoundingClientRect().left + diamond.getBoundingClientRect().width/2);
  let pendingX = e.clientX-grabOffset, frame = 0;
  const paint = () => {
    frame = 0; const r = rail.getBoundingClientRect(); point.at = clamp((pendingX-r.left)/Math.max(1,r.width),0,1);
    diamond.style.left = (point.at*100) + '%'; playTime = c.start + point.at*Math.max(.05,c.out-c.in); drawNow();
  };
  const move = ev => {
    pendingX = ev.clientX-grabOffset; if (!frame) frame = requestAnimationFrame(paint);
  };
  const up = () => { if (frame) { cancelAnimationFrame(frame); paint(); } c.keyframes.points.sort((a,b)=>a.at-b.at); diamond.classList.remove('dragging'); document.body.classList.remove('dragging-keyframe'); if (diamond.releasePointerCapture) { try { diamond.releasePointerCapture(e.pointerId); } catch (_) {} } window.removeEventListener('pointermove',move); window.removeEventListener('pointerup',up); putClip(c,{keyframes:c.keyframes}); };
  window.addEventListener('pointermove',move); window.addEventListener('pointerup',up);
}

function renameClip(c, m) {
  const cur = m ? m.name : '';
  const name = prompt('Rename', cur);
  if (name == null) return;
  const nm = name.trim();
  if (!nm || nm === cur) return;
  if (c.sceneId) {
    api('/api/scenes/' + c.sceneId, { method: 'PUT', body: { name: nm } }).then(refresh).catch(e => toast(e.message, 'err'));
  } else if (m) {
    api('/api/media/' + m.id, { method: 'PUT', body: { name: nm } }).then(refresh).catch(e => toast(e.message, 'err'));
  }
}

function laneTime(clientX, lane) {
  const r = lane.getBoundingClientRect();
  return (clientX - r.left) / pxPerSec;
}

/* clip move */
function onClipDown(e, tr, c, el) {
  if (e.target.classList.contains('trim')) return;
  e.preventDefault();
  selectedKeyframe = null;
  const clipRect=el.getBoundingClientRect(),clipDuration=Math.max(.05,c.out-c.in);
  seekTo(c.start+clamp((e.clientX-clipRect.left)/Math.max(1,clipRect.width),0,1)*clipDuration);
  drawNow();
  select({ type: 'clip', id: c.id });
  const startX = e.clientX; const origStart = c.start; const origIn = c.in, origOut = c.out;
  const media = mediaById(c.mediaId); const dur = media ? media.duration : (origOut - origIn);
  let targetTrack = tr; let targetLane = el.closest('.tlane'); const sourceRow = targetLane.closest('.tlrow');
  const move = (ev) => {
    if (c.keyframes && tr.kind === 'video') {
      $$('.tlrow').forEach(row => {
        const track = state.tracks.find(x => x.id === row.dataset.track);
        if (track && track.kind === 'video') row.classList.toggle('has-keyframes', track.clips.some(x => x.id !== c.id && clipHasAnimation(x)));
      });
    }
    const under = document.elementFromPoint(ev.clientX, ev.clientY);
    const candidate = under && under.closest('.tlane');
    $$('.tlane.clip-dropzone').forEach(l => l.classList.remove('clip-dropzone'));
    if (candidate && candidate.dataset.kind === tr.kind) {
      targetLane = candidate; targetTrack = state.tracks.find(x => x.id === candidate.dataset.track) || tr; candidate.classList.add('clip-dropzone');
      if (el.parentElement !== candidate) candidate.appendChild(el);
      if (c.keyframes && tr.kind === 'video') {
        const targetRow = candidate.closest('.tlrow'); targetRow.classList.add('has-keyframes');
        if (targetRow !== sourceRow) sourceRow.classList.toggle('has-keyframes', tr.clips.some(x => x.id !== c.id && clipHasAnimation(x)));
      }
    }
    const dsec = targetTrack.id === tr.id ? (ev.clientX - startX) / pxPerSec : 0;
    const desired=targetTrack.id===tr.id?Math.max(0,origStart+dsec):Math.max(0,laneTime(ev.clientX,targetLane));
    c.start=resolvedClipStart(targetTrack,c.id,desired,clipDuration);
    positionClip(el, c);
  };
  const up = async (ev) => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    $$('.tlane.clip-dropzone').forEach(l => l.classList.remove('clip-dropzone'));
    try { await api('/api/clips/' + c.id, { method: 'PUT', body: { start: c.start, trackId: targetTrack.id } }); if (targetTrack.id !== tr.id) toast('Clip moved to ' + targetTrack.name, 'ok'); } catch (err) { toast(err.message, 'err'); }
    refresh();
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
}
function positionClip(el, c) { el.style.left = (c.start * pxPerSec) + 'px'; }

/* trim */
function onTrimDown(e, tr, c, which) {
  e.preventDefault(); e.stopPropagation();
  select({ type: 'clip', id: c.id });
  const media = mediaById(c.mediaId);
  const dur = media ? (media.kind === 'image' ? 60 : media.duration) : (c.out - c.in);
  const el = clipElById(c.id);
  const originalStart=+c.start||0,originalIn=+c.in||0,originalOut=+c.out||0;
  const fixedRight=originalStart+(originalOut-originalIn);
  const previousEnd=Math.max(0,...(tr.clips||[]).filter(other=>other.id!==c.id&&other.start+(other.out-other.in)<=originalStart+.000001).map(other=>other.start+(other.out-other.in)));
  const startX=e.clientX;
  const move = (ev) => {
    const delta=(ev.clientX-startX)/pxPerSec;
    if (which === 'l') {
      const earliest=Math.max(previousEnd,originalStart-originalIn),nextStart=clamp(originalStart+delta,earliest,fixedRight-.05);
      c.start=nextStart;
      c.in=clamp(originalIn+(nextStart-originalStart),0,originalOut-.05);
      c.out=originalOut;
    } else {
      const next=(tr.clips||[]).filter(other=>other.id!==c.id&&other.start>=originalStart).sort((a,b)=>a.start-b.start)[0];
      const maxOut=next?Math.min(dur,c.in+Math.max(.05,next.start-c.start)):dur;
      c.out = clamp(originalOut+delta, c.in + 0.05, maxOut);
    }
    positionClip(el, c);
    el.style.width = Math.max(6, (c.out - c.in) * pxPerSec) + 'px';
  };
  const up = async () => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    try { await api('/api/clips/' + c.id, { method: 'PUT', body: { start:c.start, in:c.in, out:c.out } }); } catch (err) { toast(err.message, 'err'); }
    refresh();
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
}

/* playhead drag */
function onPlayheadDown(e) {
  e.preventDefault();
  const ruler = $('#tlRuler');
  const move = (ev) => { const r = ruler.getBoundingClientRect(); seekTo((ev.clientX - r.left - LANE_OFFSET) / pxPerSec); };
  const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
  move(e);
}
function seekFromTimelinePointer(e, lane) {
  if (e.button !== 0) return;
  e.preventDefault();
  const r = lane.getBoundingClientRect();
  seekTo((e.clientX - r.left) / pxPerSec);
  drawNow();
}

/* ---------------- selection ---------------- */
function select(s) {
  sel = s;
  $$('.clip').forEach(c => c.classList.remove('sel'));
  $$('.mtile').forEach(c => c.classList.remove('sel'));
  $$('.scene').forEach(c => c.classList.remove('sel'));
  if (s && s.type === 'clip') { const el = clipElById(s.id); if (el) el.classList.add('sel'); }
  if (s && s.type === 'media') { const el = document.querySelector('.mtile[data-media="' + s.id + '"]'); if (el) el.classList.add('sel'); }
  if (s && s.type === 'scene') { const el = document.querySelector('.scene[data-scene="' + s.id + '"]'); if (el) el.classList.add('sel'); }
  if (s) setInspectorTab('inspect');
  const active = document.activeElement;
  const editingInspector = active && $('#inspector').contains(active) && (['INPUT','TEXTAREA','SELECT'].includes(active.tagName) || active.isContentEditable);
  if (!editingInspector) renderInspector();
  updateDetachAudioTool();
}

function updateDetachAudioTool(){
  const tool=$('#detachAudioTool'),button=$('#detachAudioBtn');if(!tool||!button)return;
  const c=sel&&sel.type==='clip'?findClip(sel.id):null,tr=c&&trackOfClip(c),m=c&&mediaById(c.mediaId);
  const available=!!(c&&tr&&tr.kind==='video'&&m&&m.hasAudio&&!c.detached);
  tool.hidden=!available;button.disabled=!available;button.title=available?'Detach audio from '+m.name:'Select a video clip with attached audio';
}

/* ---------------- media bin ---------------- */
let dragMedia = null;
let mediaFolder = '', mediaQuery = '', mediaView = localStorage.getItem('omMediaView') || 'grid';
let projectSort = localStorage.getItem('omProjectSort') || 'modified';

const MEDIA_SORTS = {
  newest: { label: 'Newest', fn: (a, b) => (b.created || 0) - (a.created || 0) },
  oldest: { label: 'Oldest', fn: (a, b) => (a.created || 0) - (b.created || 0) },
  name: { label: 'Name A–Z', fn: (a, b) => (a.name || '').localeCompare(b.name || '') },
  nameDesc: { label: 'Name Z–A', fn: (a, b) => (b.name || '').localeCompare(a.name || '') }
};
let mediaSort = localStorage.getItem('omMediaSort') || 'newest';
let mediaRenderKey = '';

async function mediaMoveTo(ids, folder, conflict='ask') {
  try{await api('/api/media/move',{method:'POST',body:{ids,folder,conflict}});await refresh();return true;}
  catch(error){if(error.status===409&&error.data&&error.data.conflicts){openFolderConflictSheet(ids,folder,error.data.conflicts);return false;}toast(error.message,'err');return false;}
}
function openFolderConflictSheet(ids,folder,conflicts){
  const st={};const names=[...new Set(conflicts.map(x=>x.name))],listed=names.map(name=>'“'+name+'”').join(', ');
  composerSheetTemplate({id:'folder-conflict',mode:'folder-conflict',title:'Already in this folder',subtitle:listed+' '+(names.length===1?'is':'are')+' already in “'+folder+'”. Choose whether to replace '+(names.length===1?'it':'them')+' or keep both.',state:()=>st,
    middleHtml:()=>'',
    actionsHtml:()=>'<div class="sheetActions folderConflictActions"><button class="btn ghost" id="folderConflictCancel">Cancel</button><button class="btn ghost" id="folderConflictBoth">Keep both</button><button class="btn primary" id="folderConflictOverwrite">Overwrite</button></div>',
    bindActions:(box,s,ctl)=>{const choose=async mode=>{try{await api('/api/media/move',{method:'POST',body:{ids,folder,conflict:mode}});ctl.close();toast(mode==='overwrite'?'Existing media replaced':'Media moved and renamed','ok');await refresh();}catch(error){toast(error.message,'err');}};$('#folderConflictCancel').addEventListener('click',ctl.close);$('#folderConflictBoth').addEventListener('click',()=>choose('keep-both'));$('#folderConflictOverwrite').addEventListener('click',()=>choose('overwrite'));}
  });
}

function renderMedia() {
  const grid = $('#mediaGrid'); if (!grid) return;
  const q = mediaQuery.trim().toLowerCase();
  const all = state.media || [];
  const foldersAll = state.mediaFolders || [];
  const backTitle=$('#mediaBackTitle');
  if(backTitle){backTitle.innerHTML=mediaFolder?'<span aria-hidden="true">←</span> Go back':'Media';backTitle.disabled=!mediaFolder;backTitle.onclick=mediaFolder?()=>{mediaFolder='';mediaQuery='';const search=$('#mediaSearch');if(search)search.value='';renderMedia();}:null;}
  const addExisting=$('#mediaAddExisting');
  if(addExisting){addExisting.hidden=!mediaFolder;addExisting.onclick=mediaFolder?()=>openAddToFolderSheet():null;}
  const headBtn = $('#mediaNewFolder');
  if (headBtn) {
    headBtn.textContent = mediaFolder ? 'Edit folder' : 'Folder';
    // Keep the action bound to the current folder state even when the media
    // grid itself is preserved by the anti-blink render guard.
    headBtn.onclick = () => mediaFolder ? openEditFolderSheet() : openFolderComposer();
  }
  // Generation progress is polled every 1.5s. Keep the existing tile and
  // image nodes when the media panel's visible inputs have not changed;
  // replacing them on every poll makes decoded thumbnails flash in Safari.
  const nextRenderKey = JSON.stringify({
    project: state.slug || '', folder: mediaFolder, query: q,
    sort: mediaSort, view: mediaView,
    selected: sel && sel.type === 'media' ? sel.id : '',
    folders: foldersAll,
    folderMeta: state.mediaFolderMeta || {},
    media: all.map(m => [m.id, m.name, m.kind, m.src, m.thumb, m.folder, m.folder_link, m.folder_unique,
      m.status, m.duration, m.source])
  });
  if (nextRenderKey === mediaRenderKey) return;
  mediaRenderKey = nextRenderKey;
  let items, folders = [];
  if (q) items = all.filter(m => (m.folder || '') === mediaFolder && (m.name || '').toLowerCase().includes(q));
  else {
    items = all.filter(m => (m.folder || '') === mediaFolder);
    const prefix = mediaFolder ? mediaFolder + '/' : '';
    folders = foldersAll.filter(f => f.startsWith(prefix) && !f.slice(prefix.length).includes('/'));
  }

  folders.sort((a, b) => {
    const an = a.slice(a.lastIndexOf('/') + 1).toLowerCase(), bn = b.slice(b.lastIndexOf('/') + 1).toLowerCase();
    return an.localeCompare(bn);
  });
  const baseSort=(MEDIA_SORTS[mediaSort] || MEDIA_SORTS.newest).fn;
  const activityRank=m=>m.status==='running'?2:m.status==='queued'?1:0;
  items = items.slice().sort((a,b)=>activityRank(b)-activityRank(a)||baseSort(a,b));
  const hint = document.querySelector('#mediaBin .head .hint');
  if (hint) hint.style.display = mediaFolder ? 'none' : '';

  grid.classList.toggle('list', mediaView === 'list');
  grid.innerHTML = '';

  for (const f of folders) {
    const name = f.slice((mediaFolder ? mediaFolder + '/' : '').length);
    const count = all.filter(m => (m.folder || '') === f).length + foldersAll.filter(x => x.startsWith(f + '/')).length;
    const meta0 = (state.mediaFolderMeta || {})[f] || {};
    const fd = div('ftile');
    if (meta0.description) fd.title = meta0.description;
    fd.innerHTML = '<span class="fthumb">\ud83d\udcc1</span><div class="fmeta"><span class="fname">' + esc(name) + '</span><span class="fcount">' + count + '</span></div>';
    fd.addEventListener('click', () => { mediaFolder = f; mediaQuery = ''; const si = $('#mediaSearch'); if (si) si.value = ''; renderMedia(); });
    fd.addEventListener('dragover', e => { e.preventDefault(); fd.classList.add('dropok'); });
    fd.addEventListener('dragleave', () => fd.classList.remove('dropok'));
    fd.addEventListener('drop', e => {
      e.preventDefault(); fd.classList.remove('dropok');
      const id = e.dataTransfer.getData('text/plain');
      if (id) mediaMoveTo([id], f);
    });
    grid.appendChild(fd);
  }
  if (!items.length && !folders.length) {
    const e = div('empty');
    e.textContent = q ? 'No media matches your search.' : (mediaFolder ? '' : 'No media yet \u2014 import a clip or generate one.');
    if (!q && mediaFolder) return;
    grid.appendChild(e); return;
  }
  for (const m of items) {
    const tile = div('mtile'); tile.dataset.media = m.id;
    if (sel && sel.type === 'media' && sel.id === m.id) tile.classList.add('sel');
    const thumb = div('thumb');
    if (m.status === 'queued' || m.status === 'running') {
      const pending = div('generationPending'); pending.innerHTML = '<span></span><b>' + (m.status === 'running' ? 'Generating…' : 'Waiting in queue') + '</b>';
      thumb.appendChild(pending); tile.classList.add('pending');
    } else if (m.status === 'error') {
      const pending = div('generationPending error'); pending.innerHTML = '<b>Generation failed</b>'; thumb.appendChild(pending);
    } else if (m.kind === 'image') {
      const im = document.createElement('img');
      im.loading = 'lazy'; im.decoding = 'async'; im.src = mediaUrl(m);
      thumb.appendChild(im);
    }
    else {
      const v = document.createElement('video');
      v.src = mediaUrl(m); v.muted = true; v.preload = 'metadata'; v.playsInline = true; v.loop = true;
      if (m.thumb) v.poster = mediaPathUrl(m.thumb);
      if (m.thumb) {
        const poster = document.createElement('img');
        poster.className = 'videoPoster'; poster.loading = 'lazy'; poster.decoding = 'async';
        poster.src = mediaPathUrl(m.thumb); poster.alt = '';
        thumb.appendChild(poster); tile.classList.add('hasVideoPoster');
      }
      const startPreview = () => {
        try { v.currentTime = 0; } catch (_) {}
        const promise = v.play(); if (promise) promise.catch(() => tile.classList.remove('previewing'));
      };
      const stopPreview = () => {
        tile.classList.remove('previewing'); v.pause();
        try { v.currentTime = 0; } catch (_) {}
      };
      v.addEventListener('playing', () => tile.classList.add('previewing'));
      v.addEventListener('error', () => tile.classList.remove('previewing'));
      tile.addEventListener('mouseenter', startPreview);
      tile.addEventListener('mouseleave', stopPreview);
      tile.addEventListener('focusin', startPreview);
      tile.addEventListener('focusout', stopPreview);
      thumb.appendChild(v);
    }
    const kind = div('kind'); kind.textContent = m.kind === 'image' ? 'IMG' : m.kind === 'audio' ? 'AUD' : (m.source === 'generated' ? 'GEN' : 'VID'); thumb.appendChild(kind);
    const reveal = document.createElement('button'); reveal.className='tileReveal'; reveal.title=navigator.platform.toLowerCase().includes('mac')?'Reveal in Finder':'Show in file explorer'; reveal.setAttribute('aria-label',reveal.title); reveal.innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2.75 5.5h5l1.5 1.75h8v7.25a1.5 1.5 0 0 1-1.5 1.5H4.25a1.5 1.5 0 0 1-1.5-1.5z"/><path d="M6.5 11h7m-2.25-2.25L13.5 11l-2.25 2.25"/></svg>';reveal.addEventListener('click',ev=>{ev.stopPropagation();revealMediaFile(m);});thumb.appendChild(reveal);
    const x = div('x'); x.textContent = '×'; x.title = ['queued','running'].includes(m.status) ? 'Cancel generation' : 'Delete media'; x.addEventListener('click', (ev) => { ev.stopPropagation(); deleteMedia(m); });
    thumb.appendChild(x); tile.appendChild(thumb);
    const meta = div('meta');
    const n = div('n'); n.textContent = m.name; meta.appendChild(n);
    const d = div('d'); d.textContent = fmtDur(m.duration); meta.appendChild(d);
    tile.appendChild(meta);
    tile.addEventListener('click', () => {
      const scene = state.scenes.find(s => s.mediaId === m.id || s.id === m.scene_id);
      // Generated stills are first-class media assets. Selecting one should
      // show the image inspector and preview, not the video-scene workflow.
      select(scene && m.kind !== 'image' ? { type: 'scene', id: scene.id } : { type: 'media', id: m.id });
    });
    tile.setAttribute('draggable', m.status && m.status !== 'ready' ? 'false' : 'true');
    tile.addEventListener('dragstart', (e) => {
      dragMedia = m;
      e.dataTransfer.setData('text/plain', m.id);
      e.dataTransfer.effectAllowed = 'copy';
      const ghost = div('dragghost');
      const g = m.kind === 'image' ? document.createElement('img') : document.createElement('video');
      g.src = mediaUrl(m); if (m.kind === 'image') {} ghost.appendChild(g);
      document.body.appendChild(ghost); e.dataTransfer.setDragImage(ghost, 60, 34);
      setTimeout(() => ghost.remove(), 0);
    });
    tile.addEventListener('dragend', () => { dragMedia = null; $$('.tlane').forEach(l => l.classList.remove('dropzone')); });
    grid.appendChild(tile);
  }
}

function revealMediaFile(m){if(!m)return;api('/api/reveal?path='+encodeURIComponent(m.src)).then(()=>toast(navigator.platform.toLowerCase().includes('mac')?'Revealed in Finder':'Shown in file explorer','ok')).catch(err=>toast(err.message,'err'));}
function revealMediaField(m){const f=div('field');const b=document.createElement('button');b.className='btn revealMediaBtn';b.textContent=navigator.platform.toLowerCase().includes('mac')?'Reveal in Finder':'Show in file explorer';b.addEventListener('click',()=>revealMediaFile(m));f.appendChild(b);return f;}

function onLaneDrop(e, tr, lane) {
  e.preventDefault(); lane.classList.remove('dropzone');
  let m = dragMedia;
  if (!m) { const id = e.dataTransfer.getData('text/plain'); m = mediaById(id); }
  if (!m) return;
  const t = laneTime(e.clientX, lane);
  const isImg = m.kind === 'image';
  const clipDuration=isImg?3.0:m.duration;
  const clip = { trackId: tr.id, mediaId: m.id, start: resolvedClipStart(tr,null,Math.max(0,t),clipDuration), in: 0,
    out: isImg ? 3.0 : m.duration, zoom: 1.0,
    motion: isImg ? { type: 'push-in' } : null,
    transition: { type: 'cut', dur: 0 }, muted: false, detached: false };
  api('/api/clips', { method: 'POST', body: clip }).then(() => { toast('Added ' + m.name + ' to ' + tr.name, 'ok'); refresh(); }).catch(err => toast(err.message, 'err'));
}

async function importFiles(files) {
  for (const f of files) {
    try {
      await api('/api/upload', { method: 'POST', raw: f, headers: { 'X-File-Name': encodeURIComponent(f.name), 'Content-Type': 'application/octet-stream' } });
    } catch (e) { toast('Import failed: ' + e.message, 'err'); }
  }
  toast('Imported ' + files.length + ' file' + (files.length > 1 ? 's' : ''), 'ok');
  refresh();
}
async function uploadReferenceFiles(files){
  for(const file of files){
    try{const m=await api('/api/upload',{method:'POST',raw:file,headers:{'X-File-Name':encodeURIComponent(file.name),'Content-Type':'application/octet-stream'}});if(['image','audio'].includes(m.kind))selRefs.add(m.id);else toast(file.name+' is not a supported image or audio reference','err');}
    catch(e){toast('Reference upload failed: '+e.message,'err');}
  }
  await refresh();renderGenerate();if($('#composerPicker').classList.contains('on')){selectedPickerItem=null;$('#composerPicker').classList.remove('detail-open');renderComposerPicker();}toast('References added to Media and this prompt','ok');
}

async function deleteMedia(m) {
  if(m.scene_id&&['queued','running'].includes(m.status)){
    if(!confirm('Cancel '+m.name+'? Any queued scene that depends on its final frame will also be canceled.'))return;
    try{const out=await api('/api/scenes/'+m.scene_id+'/cancel',{method:'POST'});if(sel&&((sel.type==='media'&&sel.id===m.id)||(sel.type==='scene'&&sel.id===m.scene_id)))sel=null;toast('Canceled '+(out.cancelled||[]).join(' and '),'ok');await refresh();}catch(e){toast(e.message,'err');}return;
  }
  if (!confirm('Delete "' + m.name + '" and its clips?')) return;
  try { await api('/api/media/' + m.id, { method: 'DELETE' }); if (sel && sel.type === 'media' && sel.id === m.id) sel = null; toast('Deleted ' + m.name, 'ok'); refresh(); }
  catch (e) { toast(e.message, 'err'); }
}

/* ---------------- inspector ---------------- */
function renderInspector({preserveScroll=false}={}) {
  const scroller=$('#rightCol'),previousScroll=preserveScroll&&scroller?scroller.scrollTop:0;
  const body = $('#inspBody'); const hint = $('#inspHint');
  const priorPrompt=$('.sceneSourcePrompt',body),promptState=priorPrompt?{scrollTop:priorPrompt.scrollTop,start:priorPrompt.selectionStart,end:priorPrompt.selectionEnd}:null;
  const openProvenance=new Set($$('.promptProvenance[open]',body).map(node=>node.dataset.provenance));
  const restoreScroll=()=>{if(preserveScroll&&scroller)scroller.scrollTop=previousScroll;const prompt=$('.sceneSourcePrompt',body);if(prompt&&promptState){prompt.scrollTop=promptState.scrollTop;if(document.activeElement===prompt)prompt.setSelectionRange(promptState.start,promptState.end);}$$('.promptProvenance',body).forEach(node=>{if(openProvenance.has(node.dataset.provenance))node.open=true;});};
  body.className = '';
  body.innerHTML = '';
  if (!sel) { hint.textContent = ''; body.innerHTML = '<div class="insp-empty">Select a <b>clip</b> on the timeline to edit it, or a <b>media</b> item to see its details.</div>'; restoreScroll(); return; }
  if (sel.type === 'media') { renderMediaInsp(body, hint, sel.id); restoreScroll(); return; }
  if (sel.type === 'scene') { renderSceneInsp(body, hint, sel.id); restoreScroll(); return; }
  renderClipInsp(body, hint, sel.id);
  restoreScroll();
}

function inspectorNameField(value, save) {
  const field=div('field'),label=document.createElement('label'),input=document.createElement('input');
  label.textContent='Name';input.className='txt';input.value=value||'';input.spellcheck=false;
  const commit=()=>{const next=input.value.trim();if(!next){input.value=value||'';return;}if(next!==value)save(next);};
  input.addEventListener('change',commit);
  input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();input.blur();}});
  field.appendChild(label);field.appendChild(input);return field;
}

function renderMediaInsp(body, hint, id) {
  const m = mediaById(id); if (!m) { sel = null; return renderInspector(); }
  hint.textContent = 'media';
  body.classList.add('mediaInspector');
  const t = div('inspectorIdentity'); t.innerHTML = '<span class="scopeLabel">MEDIA ASSET</span><div class="t">' + esc(m.name) + '</div><div class="d">' + m.kind + ' · ' + (m.source || '') + '</div>';
  body.appendChild(t);
  body.appendChild(inspectorNameField(m.name,name=>api('/api/media/'+m.id,{method:'PUT',body:{name}}).then(refresh).catch(e=>toast(e.message,'err'))));
  const f = div('field');
  f.classList.add('inspectorSection');
  const details = div('inspectorDetails');
  details.innerHTML = 'Duration: <b>' + fmtDur(m.duration) + '</b><br>Size: ' + m.w + '×' + m.h +
    '<br>Audio: ' + (m.hasAudio ? 'yes' : 'no') + '<br>Stored: ';
  // clickable path -> reveals the file in Finder
  const pathLink = document.createElement('a');
  pathLink.className = 'pathlink';
  pathLink.textContent = m.src; pathLink.title = 'Reveal in Finder';
  pathLink.href = '/api/reveal?path=' + encodeURIComponent(m.src);
  pathLink.addEventListener('click', (e) => { e.preventDefault(); api(pathLink.href).then(() => toast('Revealed in Finder', 'ok')).catch(err => toast(err.message, 'err')); });
  details.appendChild(pathLink);
  const lab = document.createElement('label'); lab.textContent = 'Details';
  f.appendChild(lab); f.appendChild(details);
  body.appendChild(f);
  const preview=div('inspectorMediaPreview');
  if(m.kind==='image'){const image=document.createElement('img');image.src=mediaUrl(m);image.alt=m.name;preview.appendChild(image);}
  else{const video=document.createElement('video');video.src=mediaUrl(m);video.controls=true;video.muted=true;video.playsInline=true;if(m.thumb)video.poster=mediaPathUrl(m.thumb);preview.appendChild(video);}
  body.appendChild(preview);
  const actions=div('inspectorMediaActions');const reveal=document.createElement('button');reveal.className='btn ghost';reveal.textContent=navigator.platform.toLowerCase().includes('mac')?'Reveal in Finder':'Show in explorer';reveal.addEventListener('click',()=>revealMediaFile(m));
  const db=document.createElement('button');db.className='btn ghost danger';db.textContent='Delete media';db.addEventListener('click',()=>deleteMedia(m));actions.appendChild(reveal);actions.appendChild(db);body.appendChild(actions);
  if(m.kind==='video'&&(m.source==='generated'||m.scene_id))body.appendChild(continuityStyleUpdateField(m.id));
}

function continuityStyleUpdateField(mediaId){const f=div('field continuityUpdateField'),copy=document.createElement('p'),button=document.createElement('button');copy.textContent='Use this generation and the project history to rebuild the continuity style.';button.className='btn ghost continuityUpdateBtn';button.textContent='✦ Update project style';button.addEventListener('click',()=>createOrUpdateContinuityStyle(mediaId,button));f.append(copy,button);return f;}

function sceneReferenceField(sc){
  const field=div('field sceneReferences'),label=document.createElement('label'),strip=div('sceneReferenceStrip'),items=[],seen=new Set();label.textContent='References used';
  const add=(media,text,kind)=>{if(!media||seen.has(media.id))return;seen.add(media.id);items.push({media,text,kind});};
  if(sc.source_media_id){const source=mediaById(sc.source_media_id);add(source,(source&&source.name)||'Opening frame','Continuity');}
  const frameOnly=!!sc.source_media_id&&String(sc.continuity_mode||'frame')!=='reference';
  if(!frameOnly){for(const [characterId,ids] of Object.entries(sc.character_reference_ids||{})){const character=state.characters.find(c=>c.id===characterId);for(const id of ids||[])add(mediaById(id),(character&&character.name)||'Cast','Cast');}for(const id of sc.reference_media_ids||[])add(mediaById(id),(mediaById(id)||{}).name||'Reference','Visual');}
  if(!items.length){const empty=div('d');empty.textContent='No visual references were sent.';strip.appendChild(empty);}else for(const item of items){const tile=div('sceneReferenceTile'),src=item.media.thumb?mediaPathUrl(item.media.thumb):mediaUrl(item.media);tile.innerHTML='<img src="'+esc(src)+'" alt=""><span>'+esc(item.text)+'</span><b>'+esc(item.kind)+'</b>';strip.appendChild(tile);}
  field.append(label,strip);return field;
}

function renderSceneInsp(body, hint, id) {
  const sc = state.scenes.find(x => x.id === id);
  if (!sc) { sel = null; return renderInspector(); }
  hint.textContent = '';
  body.classList.add('sceneInspector');
  if(['queued','running'].includes(sc.status))body.appendChild(generationProgressField(sc));
  if(sc.status==='error')body.appendChild(generationErrorField(sc));
  body.appendChild(inspectorNameField(sc.name,name=>api('/api/scenes/'+sc.id,{method:'PUT',body:{name}}).then(refresh).catch(e=>toast(e.message,'err'))));
  const sceneMedia=mediaById(sc.mediaId);if(sceneMedia)body.appendChild(revealMediaField(sceneMedia));

  // Editable source plus immutable execution provenance.
  const pf = div('field scenePromptField'); const pl = document.createElement('label'); pl.textContent = 'Source prompt'; pf.appendChild(pl);
  const pta = document.createElement('textarea'); pta.className = 'txt sceneSourcePrompt'; pta.value = sc.prompt || '';
  pta.addEventListener('change', () => { const v = pta.value.trim(); if (v !== (sc.prompt || '')) api('/api/scenes/' + sc.id, { method: 'PUT', body: { prompt: v } }).then(refresh).catch(e => toast(e.message, 'err')); });
  pf.appendChild(pta); body.appendChild(pf);
  const provenance=[['Refined prompt',sc.refined_prompt],['Final prompt sent to H3',sc.execution_prompt]];
  for(const [label,value] of provenance){if(!value||value===sc.prompt)continue;const details=document.createElement('details');details.className='field promptProvenance';details.dataset.provenance=label;const summary=document.createElement('summary');summary.textContent=label;const text=document.createElement('pre');text.textContent=value;details.append(summary,text);body.appendChild(details);}
  if(sc.skill_compilation&&sc.skill_compilation.id){const meta=div('d promptCompileMeta');meta.textContent='Skill: '+(sc.skill_compilation.name||sc.skill_compilation.id)+' · contract '+sc.skill_compilation.version+(sc.formatter_model?' · refinement '+sc.formatter_model:'');body.appendChild(meta);}

  body.appendChild(sceneReferenceField(sc));
  // style that was chained in (project-level)
  const projectStyle = state.style_profile && state.style_profile.prompt || state.base_prompt || '';
  if (projectStyle) {
    const sf = div('field'); const sl = document.createElement('label'); sl.textContent = 'Style (chained into every scene)'; sf.appendChild(sl);
    const sd = div('d'); sd.style.cssText = 'font-size:12px;color:var(--muted);line-height:1.5'; sd.textContent = projectStyle;
    sf.appendChild(sd); body.appendChild(sf);
  }

  // Character evidence is immutable once work is queued. The native worker
  // snapshots these references before inference, so editing the chips while a
  // job is queued/running would make the inspector disagree with the command.
  const generationLocked = ['queued','running'].includes(sc.status);
  // characters: toggle each cast member on/off for this scene (attach = send ref)
  const cf = div('field'); const cl = document.createElement('label'); cl.textContent = 'Characters (sent as references)'; cf.appendChild(cl);
  const cc = div('mini');
  const attachedCharacters=state.characters.filter(c=>(sc.character_ids||[]).includes(c.id));
  if (!attachedCharacters.length) { const e = div('d'); e.textContent = 'No cast was attached.'; cc.appendChild(e); }
  for (const c of attachedCharacters) {
    const on = (sc.character_ids || []).includes(c.id);
    const b = document.createElement('button'); b.textContent = c.name; if (on) b.classList.add('on');
    b.disabled = generationLocked;
    b.title = generationLocked ? 'Cast is locked while this scene is queued or generating' : (on ? 'Click to detach from this scene' : 'Click to attach to this scene');
    if (!generationLocked) b.addEventListener('click', () => {
      let ids = (sc.character_ids || []).slice();
      if (ids.includes(c.id)) ids = ids.filter(x => x !== c.id); else ids.push(c.id);
      api('/api/scenes/' + sc.id, { method: 'PUT', body: { character_ids: ids } }).then(refresh).catch(e => toast(e.message, 'err'));
    });
    cc.appendChild(b);
  }
  cf.appendChild(cc); body.appendChild(cf);

  // first / last frame stills
  if ((!sceneMedia || sceneMedia.kind !== 'image') && (sc.first_frame || sc.last_frame)) {
    const ff = div('field'); const fl = document.createElement('label'); fl.textContent = 'Frames'; ff.appendChild(fl);
    const wrap = div('sframes');
    if (sc.first_frame) { const b = document.createElement('button'); b.className = 'sframe'; b.title = 'Extract first frame to media'; b.innerHTML = '<img src="' + esc(sc.first_frame) + '"><span>first</span>'; b.addEventListener('click', () => extractSceneFrame(sc, 'first')); wrap.appendChild(b); }
    if (sc.last_frame) { const b = document.createElement('button'); b.className = 'sframe'; b.title = 'Extract last frame to media'; b.innerHTML = '<img src="' + esc(sc.last_frame) + '"><span>last</span>'; b.addEventListener('click', () => extractSceneFrame(sc, 'last')); wrap.appendChild(b); }
    ff.appendChild(wrap); body.appendChild(ff);
  }

  // params
  const d = div('d'); d.style.cssText = 'font-size:11.5px;color:var(--muted);line-height:1.7;margin-top:4px';
  d.innerHTML = 'Status: <b>' + (sc.status || 'idle') + '</b><br>Seed: ' + (sc.params && sc.params.seed) +
    ' · Steps: ' + (sc.params && sc.params.steps) + ((!sceneMedia || sceneMedia.kind !== 'image') ? ' · Frames: ' + (sc.params && sc.params.frames) : '') +
    (sc.chain ? '<br>Chains from previous: yes' : '');
  body.appendChild(d);
  if(sceneMedia)body.appendChild(continuityStyleUpdateField(sceneMedia.id));
}

const generationEstimates=new Map();
function generationProgressField(sc){
  const p=sc.progress||{},total=Math.max(1,+p.total||1),completed=Math.max(0,+p.completed||0),pct=total>1?Math.min(100,Math.round(completed/total*100)):0;
  const now=Date.now();let estimate=generationEstimates.get(sc.id);
  if(!estimate){estimate={seenAt:now,stepAt:now,completed,secondsPerStep:null};generationEstimates.set(sc.id,estimate);}
  if(completed>estimate.completed){const seconds=(now-estimate.stepAt)/1000/Math.max(1,completed-estimate.completed);estimate.secondsPerStep=estimate.secondsPerStep==null?seconds:estimate.secondsPerStep*.65+seconds*.35;estimate.completed=completed;estimate.stepAt=now;}
  const eta=p.eta_seconds!=null?p.eta_seconds:(estimate.secondsPerStep!=null?estimate.secondsPerStep*Math.max(0,total-completed):null);
  const elapsed=p.elapsed_seconds!=null?p.elapsed_seconds:(now-estimate.seenAt)/1000;
  const card=div('generationInspectorProgress');
  const status=sc.status==='queued'?'Waiting in queue':(p.phase||'Preparing generation').replace(/\b\w/g,c=>c.toUpperCase());
  const detail=sc.status==='queued'?'Starts automatically after the active generation.':(total>1?completed+' of '+total+' steps'+(eta!=null?' · '+fmtEstimate(eta)+' remaining':' · estimating after the next step'):'Loading the model and preparing references…');
  const badge=sc.status==='queued'?'Queued':(total>1?pct+'%':'Starting');
  card.innerHTML='<div class="generationProgressHead"><strong>'+esc(status)+'</strong><span class="generationPercent">'+esc(badge)+'</span></div><small>'+esc(detail)+'</small><i><b style="width:'+pct+'%"></b></i>'+(sc.status==='running'?'<small class="generationElapsed">Observed '+esc(fmtEstimate(elapsed).replace('about ',''))+'</small>':'');
  return card;
}

function generationErrorField(sc){
  const source=sc.source_media_id&&mediaById(sc.source_media_id),sourceUnavailable=sc.source_media_id&&(!source||source.status!=='ready');
  const card=div('generationInspectorError'),message=sourceUnavailable?'The continuation video did not finish, so this scene has no valid opening frame. Choose a ready video before retrying.':(sc.error||'The generation failed before producing media.');
  card.innerHTML='<span class="scopeLabel">GENERATION FAILED</span><strong>'+esc(message)+'</strong><div><button class="btn ghost" data-delete-failed>Delete</button><button class="btn primary" data-retry-failed '+(sourceUnavailable?'disabled':'')+'>Retry generation</button></div>';
  $('[data-delete-failed]',card).addEventListener('click',async()=>{if(!confirm('Delete '+sc.name+'?'))return;await api('/api/scenes/'+sc.id,{method:'DELETE'});sel=null;await refresh();});
  $('[data-retry-failed]',card).addEventListener('click',async()=>{try{await api('/api/scenes/'+sc.id+'/generate',{method:'POST'});toast(sc.name+' queued','ok');await refresh();}catch(error){toast(error.message,'err');}});
  return card;
}

function renderClipInsp(body, hint, id) {
  const c = findClip(id); if (!c) { sel = null; return renderInspector(); }
  const tr = trackOfClip(c); const m = mediaById(c.mediaId);
  const isAudio=tr&&tr.kind==='audio',isVideo=tr&&tr.kind==='video';
  hint.textContent = '';
  body.classList.add('clipInspector');
  const head = div('clipInspectorIdentity');
  head.innerHTML = '<div><div class="t">' + esc(m ? m.name : '?') + '</div><div class="d">on ' + (tr ? tr.name : '?') + ' · ' + (m ? m.kind : '') + '</div></div>';
  const identityFields=[head];
  if(m)identityFields.push(inspectorNameField(m.name,name=>{
    const request=c.sceneId?api('/api/scenes/'+c.sceneId,{method:'PUT',body:{name}}):api('/api/media/'+m.id,{method:'PUT',body:{name}});
    request.then(refresh).catch(e=>toast(e.message,'err'));
  }));
  if(m)identityFields.push(revealMediaField(m));
  const definitions=[
    ['clip','Clip','<svg viewBox="0 0 24 24"><path d="M5 4h14v16H5zM9 4v16M15 4v16M5 9h4M15 9h4M5 15h4M15 15h4"/></svg>'],
    ['transform','Transform','<svg viewBox="0 0 24 24"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5M8 8l-5-5M16 8l5-5M8 16l-5 5M16 16l5 5"/></svg>'],
    ['color','Color','<svg viewBox="0 0 24 24"><path d="M12 3s6 6.7 6 11a6 6 0 0 1-12 0c0-4.3 6-11 6-11Z"/></svg>'],
    ['animate','Animate','<svg viewBox="0 0 24 24"><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3ZM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z"/></svg>'],
    ['transitions','Transitions','<svg viewBox="0 0 24 24"><path d="M4 6h10M10 3l4 3-4 3M20 18H10M14 15l-4 3 4 3"/></svg>'],
    ['effects','Effects','<svg viewBox="0 0 24 24"><path d="m15 4 5 5L8 21l-5-5L15 4ZM6 14l5 5M5 4v4M3 6h4M19 15v4M17 17h4"/></svg>']
  ];
  const available=new Set(isAudio?['clip']:['clip','transform',...(isVideo?['color','animate','transitions','effects']:['animate','effects'])]);
  if(!available.has(inspectorClipTab))inspectorClipTab='clip';
  const nav=div('clipInspectorTabs');nav.setAttribute('role','tablist');nav.setAttribute('aria-label','Clip controls');
  const panels=div('clipInspectorPanels'),panelById={};
  const activate=(tab,persist=true)=>{if(!available.has(tab))return;inspectorClipTab=tab;$$('[data-clip-tab]',nav).forEach(button=>{const on=button.dataset.clipTab===tab;button.classList.toggle('on',on);button.setAttribute('aria-selected',String(on));button.tabIndex=on?0:-1;});Object.entries(panelById).forEach(([key,panel])=>panel.hidden=key!==tab);if(persist)saveProjectLayout();};
  definitions.forEach(([key,label,icon])=>{
    if(!available.has(key))return;
    const button=document.createElement('button');button.type='button';button.dataset.clipTab=key;button.title=label;button.setAttribute('aria-label',label);button.setAttribute('role','tab');button.setAttribute('aria-controls','clip-panel-'+key);button.innerHTML=icon;button.addEventListener('click',()=>activate(key));nav.appendChild(button);
    const panel=div('clipInspectorPanel');panel.id='clip-panel-'+key;panel.dataset.clipPanel=key;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-label',label);panelById[key]=panel;panels.appendChild(panel);
  });
  nav.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;const buttons=$$('[data-clip-tab]',nav),current=Math.max(0,buttons.indexOf(document.activeElement));let next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(current+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;event.preventDefault();buttons[next].focus();activate(buttons[next].dataset.clipTab);});
  // The clip tools are secondary navigation, so keep them directly beneath
  // Inspector / Generate / Cast rather than below the selected clip details.
  body.prepend(nav);
  body.appendChild(panels);

  const clipPanel=panelById.clip;
  identityFields.forEach(field=>clipPanel.appendChild(field));
  clipPanel.appendChild(numField('Timeline start', c.start, 0, 9999, 0.01, v => putClip(c, { start: v })));
  clipPanel.appendChild(numField('Trim in', c.in, 0, m ? m.duration : 9999, 0.01, v => putClip(c, { in: Math.min(v, c.out - 0.05) })));
  clipPanel.appendChild(numField('Trim out', c.out, 0, (m && m.kind === 'image') ? 60 : (m ? m.duration : 9999), 0.01, v => putClip(c, { out: Math.max(v, c.in + 0.05) })));
  if(!isAudio){
    panelById.transform.appendChild(zoomAmountField(c));
    panelById.transform.appendChild(positionField(c));
  }
  if(isVideo)panelById.color.appendChild(colorEffectsField(c));
  // motion (pan-and-zoom / "Ken Burns") — only for still images
  if (!isAudio && m && m.kind === 'image') {
    const mf = div('field');
    const ml = document.createElement('label'); ml.textContent = 'Motion (pan & zoom)'; mf.appendChild(ml);
    const seg = div('motionPresets');
    const presets = [['none','None'],['push-in','Push in'],['pull-out','Pull out'],['pan-left','Pan ←'],['pan-right','Pan →'],['pan-up','Pan ↑'],['pan-down','Pan ↓']];
    const cur = (c.motion && c.motion.type) || 'none';
    for (const [val, lab] of presets) {
      const b = document.createElement('button'); b.className = 'motionPreset'; b.dataset.motion=val; b.textContent = lab;
      if (val === 'pan-left') b.style.setProperty('--mx','-8px');
      if (val === 'pan-right') b.style.setProperty('--mx','8px');
      if (val === 'pan-up') b.style.setProperty('--my','-5px');
      if (val === 'pan-down') b.style.setProperty('--my','5px');
      if (cur === val) b.classList.add('on');
      b.addEventListener('pointerenter', () => startEffectPreview(c, val, lab));
      b.addEventListener('pointerleave', () => endEffectPreview(c, false));
      b.addEventListener('click', () => {
        endEffectPreview(c, true);
        c.motion = { type: val }; c.keyframes = null; drawNow();
        putClip(c, { motion: { type: val }, keyframes: null });
        $$('.motionPreset', mf).forEach(x => x.classList.remove('on')); b.classList.add('on');
      });
      seg.appendChild(b);
    }
    mf.appendChild(seg); panelById.animate.appendChild(mf);
  }
  if (isVideo) panelById.animate.appendChild(transformKeyframesField(c));
  if (isVideo) {
    // transition
    const tf = div('field'); const lab = document.createElement('label'); lab.textContent = 'Add transition'; tf.appendChild(lab);
    const seg = div('seg'); const types = ['dissolve', 'fade', 'wipe', 'slide', 'circle'];
    for (const ty of types) {
      const b = document.createElement('button'); b.textContent = ty;
      b.addEventListener('click', () => {
        const items=transitionItems(c).map(x=>({...x})),item={id:'tr-'+Date.now()+'-'+Math.random().toString(36).slice(2,6),type:ty,edge:'start',dur:Math.min(.5,Math.max(.05,c.out-c.in)),enabled:true};items.push(item);selectedTransition={clipId:c.id,transitionId:item.id};putClip(c,{transition:transitionPayload(items)});
      });
      seg.appendChild(b);
    }
    tf.appendChild(seg); panelById.transitions.appendChild(tf);
    if(transitionItems(c).length) panelById.transitions.appendChild(transitionStackField(c));
  }
  if(isAudio){
    clipPanel.appendChild(precisionRangeField('Volume',Number.isFinite(+c.volume)?+c.volume:1,0,2,.01,v=>{c.volume=v;drawNow();},v=>putClip(c,{volume:v},false),'×'));
    clipPanel.appendChild(audioFadeField(c));
  }else{
    const effects=panelById.effects;
    effects.appendChild(blurEffectField(c));
    effects.appendChild(maskEffectField(c));
  }
  clipPanel.appendChild(toggleRow('Mute clip', c.muted, v => putClip(c, { muted: v })));
  const del = div('field'); const db = document.createElement('button'); db.className = 'btn ghost'; db.textContent = 'Delete clip';
  db.style.color = 'var(--err)'; db.addEventListener('click', () => deleteClip(c)); del.appendChild(db); clipPanel.appendChild(del);
  activate(inspectorClipTab,false);
}

function startEffectPreview(c, type, label) {
  if (c._effectPreview) {
    c.motion = { type }; c.keyframes = null; c._effectPreview.started = performance.now();
    $('#previewWrap').dataset.effectPreview = 'Preview · ' + label; drawNow(); return;
  }
  c._effectPreview = { motion: c.motion, keyframes:c.keyframes, time: playTime, wasPlaying: playing, started: performance.now() };
  pause(); c.motion = { type }; c.keyframes = null;
  $('#previewWrap').classList.add('effect-previewing');
  $('#previewWrap').dataset.effectPreview = 'Preview · ' + label;
  const duration = Math.max(.35, c.out - c.in);
  const animate = now => {
    if (!c._effectPreview) return;
    const progress = ((now - c._effectPreview.started) / 1800) % 1;
    playTime = c.start + Math.min(duration - .001, progress * duration);
    drawNow();
    effectPreviewRaf = requestAnimationFrame(animate);
  };
  cancelAnimationFrame(effectPreviewRaf);
  drawNow();
  effectPreviewRaf = requestAnimationFrame(animate);
}

function endEffectPreview(c, commit) {
  const preview = c._effectPreview; if (!preview) return;
  cancelAnimationFrame(effectPreviewRaf); effectPreviewRaf = 0;
  if (!commit) { c.motion = preview.motion; c.keyframes = preview.keyframes; }
  playTime = preview.time;
  delete c._effectPreview;
  $('#previewWrap').classList.remove('effect-previewing');
  delete $('#previewWrap').dataset.effectPreview;
  drawNow();
  if (preview.wasPlaying) play();
}

async function detachAudio(c, on) {
  const m = mediaById(c.mediaId);
  if (on) {
    const aTrack = trackById('A1');
    if (!aTrack) { toast('No audio track', 'err'); return; }
    if (!c.audioClipId) {
      const ac = await api('/api/clips', { method: 'POST', body: {
        trackId: 'A1', mediaId: c.mediaId, start: c.start, in: c.in, out: c.out,
        zoom: 1, transition: { type: 'cut', dur: 0 }, muted: c.muted, detached: false
      } }).catch(e => { toast(e.message, 'err'); return; });
      c.audioClipId = ac.id;
    }
    await api('/api/clips/' + c.id, { method: 'PUT', body: { detached: true, audioClipId: c.audioClipId } });
    toast('Audio moved to Audio track', 'ok');
  } else {
    if (c.audioClipId) { try { await api('/api/clips/' + c.audioClipId, { method: 'DELETE' }); } catch (e) {} }
    await api('/api/clips/' + c.id, { method: 'PUT', body: { detached: false, audioClipId: null } });
    toast('Audio reattached', 'ok');
  }
  refresh();
}

async function putClip(c, fields, refreshUI=true) {
  Object.assign(c, fields);
  clipMutationEpoch++;clipSavePending++;
  let saved=false;
  try{await api('/api/clips/' + c.id, { method: 'PUT', body: fields });saved=true;}
  catch(e){toast(e.message, 'err');}
  finally{clipSavePending=Math.max(0,clipSavePending-1);}
  // Force a GET that begins after the PUT instead of reusing an older poll.
  if(saved&&refreshUI)await refresh(true);
  return saved;
}
async function undoTimeline() {
  try { const result=await api('/api/undo',{method:'POST'}); if(!result.ok){toast('Nothing to undo','err');return;} selectedKeyframe=null; sel=null; toast('Timeline edit undone','ok'); await refresh(); }
  catch(e){toast(e.message,'err');}
}
async function deleteClip(c) {
  try { await api('/api/clips/' + c.id, { method: 'DELETE' }); if (sel && sel.type === 'clip' && sel.id === c.id) sel = null; toast('Clip deleted', 'ok'); refresh(); }
  catch (e) { toast(e.message, 'err'); }
}

function numField(label, val, min, max, step, onInput) {
  const f = div('field'); const lab = document.createElement('label'); lab.textContent = label; f.appendChild(lab);
  const row = div('row'); const inp = document.createElement('input'); inp.className = 'txt'; inp.type = 'number'; inp.step = step; inp.min = min; inp.max = max; inp.value = Number(val.toFixed(3));
  inp.addEventListener('change', () => { const v = parseFloat(inp.value); if (!isNaN(v)) onInput(clamp(v, min, max)); });
  row.appendChild(inp); f.appendChild(row); return f;
}

function audioFadeField(c){
  const duration=Math.max(.05,c.out-c.in),fades={in:0,out:0,...(c.audioFade||{})},field=div('field audioFadeField'),title=document.createElement('label');title.textContent='Audio fades';field.appendChild(title);
  const note=document.createElement('small');note.className='audioFadeNote';note.textContent='Smooth the clip volume at its beginning and end.';field.appendChild(note);
  const add=(key,label)=>{const wrap=div('precisionField'),head=div('precisionHead'),lab=document.createElement('label');lab.textContent=label;head.appendChild(lab);const value=div('precisionValue hasUnit'),number=document.createElement('input');number.type='number';number.className='precisionNumber';number.min=0;number.max=duration;number.step=.01;number.value=clamp(+fades[key]||0,0,duration).toFixed(2);const unit=document.createElement('span');unit.textContent='s';value.append(number,unit);head.appendChild(value);wrap.appendChild(head);const range=document.createElement('input');range.type='range';range.min=0;range.max=duration;range.step=.01;range.value=number.value;let raf=0;const redraw=()=>{raf=0;drawNow();renderTimeline();};const apply=(raw,save)=>{const v=clamp(+raw||0,0,duration);number.value=v.toFixed(2);range.value=String(v);fades[key]=v;c.audioFade={...fades};if(!raf)raf=requestAnimationFrame(redraw);if(save){if(raf){cancelAnimationFrame(raf);raf=0;}redraw();putClip(c,{audioFade:c.audioFade},false);}};range.addEventListener('input',()=>apply(range.value,false));range.addEventListener('change',()=>apply(range.value,true));number.addEventListener('change',()=>apply(number.value,true));wrap.appendChild(range);field.appendChild(wrap);};
  add('in','Fade in');add('out','Fade out');return field;
}
function rangeField(label, val, min, max, step, onInput) {
  const f = div('field'); const lab = document.createElement('label'); lab.textContent = label; f.appendChild(lab);
  const row = div('row');
  const r = document.createElement('input'); r.type = 'range'; r.min = min; r.max = max; r.step = step; r.value = val;
  const v = div('val'); v.textContent = Number(val).toFixed(2);
  r.addEventListener('input', () => { v.textContent = Number(r.value).toFixed(2); onInput(parseFloat(r.value)); });
  row.appendChild(r); row.appendChild(v); f.appendChild(row); return f;
}
function precisionRangeField(label, val, min, max, step, onPreview, onCommit, unitText='') {
  const f = div('field precisionField'); const head = div('precisionHead'); const lab = document.createElement('label'); lab.textContent = label;
  const valueWrap=div('precisionValue'+(unitText?' hasUnit':''));
  const n = document.createElement('input'); n.type = 'number'; n.min = min; n.max = max; n.step = step; n.value = Number(val).toFixed(step < .1 ? 2 : 1); n.className = 'precisionNumber'; valueWrap.appendChild(n);
  if(unitText){const unit=document.createElement('span');unit.textContent=unitText;valueWrap.appendChild(unit);}
  head.appendChild(lab); head.appendChild(valueWrap); f.appendChild(head);
  const r = document.createElement('input'); r.type = 'range'; r.min = min; r.max = max; r.step = step; r.value = val; f.appendChild(r);
  let current = +val,previewRaf=0,pending=current;
  const runPreview=()=>{previewRaf=0;onPreview(pending);};
  const queuePreview=value=>{pending=value;if(!previewRaf)previewRaf=requestAnimationFrame(runPreview);};
  const flushPreview=()=>{if(previewRaf){cancelAnimationFrame(previewRaf);previewRaf=0;}pending=current;onPreview(current);};
  const precision=Math.max(0,(String(step).split('.')[1]||'').length);
  const normalized=raw=>{const parsed=parseFloat(raw);if(!isFinite(parsed))return null;const snapped=Math.round((clamp(parsed,min,max)-min)/step)*step+Number(min);return +clamp(snapped,min,max).toFixed(Math.max(precision,4));};
  const preview = raw => { const next=normalized(raw);if(next===null)return;current=next;r.value=String(current);n.value=current.toFixed(step < .1 ? 2 : 1);queuePreview(current); };
  const commit=()=>{flushPreview();onCommit(current);inspectorControlActive=false;};
  const fromPointer=event=>{const rect=r.getBoundingClientRect(),ratio=clamp((event.clientX-rect.left)/Math.max(1,rect.width),0,1);preview(Number(min)+(Number(max)-Number(min))*ratio);};
  r.addEventListener('input', () => preview(r.value));
  r.addEventListener('change', commit);
  r.addEventListener('pointerdown',event=>{if(event.button!==0)return;inspectorControlActive=true;r.setPointerCapture?.(event.pointerId);fromPointer(event);event.preventDefault();});
  r.addEventListener('pointermove',event=>{if(!inspectorControlActive||!(event.buttons&1))return;fromPointer(event);event.preventDefault();});
  r.addEventListener('pointerup',event=>{if(!inspectorControlActive)return;fromPointer(event);r.releasePointerCapture?.(event.pointerId);commit();event.preventDefault();});
  r.addEventListener('pointercancel',()=>{inspectorControlActive=false;});
  r.addEventListener('keyup',event=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'].includes(event.key))flushPreview();});
  n.addEventListener('focus',()=>{inspectorControlActive=true;});
  n.addEventListener('input', () => { const v = parseFloat(n.value); if (isFinite(v)) { current = clamp(v,min,max); r.value = current; queuePreview(current); } });
  n.addEventListener('change', () => { preview(n.value);commit(); });
  n.addEventListener('blur',()=>{inspectorControlActive=false;});
  return f;
}
function zoomAmountField(c) {
  const toAmount = zoom => Math.round((clamp(+zoom || 1, .1, 8) - 1) * 100);
  const toZoom = amount => clamp(1 + amount / 100, .1, 8);
  const f = div('field precisionField zoomAmountField');
  const head = div('precisionHead'); const lab = document.createElement('label'); lab.textContent = 'Zoom size';
  const valueWrap = div('zoomAmountInput');
  const n = document.createElement('input'); n.type = 'number'; n.min = -90; n.max = 700; n.step = 1; n.value = toAmount(c.zoom); n.className = 'precisionNumber'; n.setAttribute('aria-label','Zoom size adjustment in percent');
  const unit = document.createElement('span'); unit.textContent = '%'; valueWrap.append(n,unit); head.append(lab,valueWrap); f.appendChild(head);
  const r = document.createElement('input'); r.type = 'range'; r.min = -90; r.max = 700; r.step = .1; r.value = toAmount(c.zoom);r.setAttribute('aria-label','Zoom size'); f.appendChild(r);
  const note = document.createElement('small'); note.className = 'zoomAmountNote'; note.textContent = 'Negative shrinks · 0% is original size · positive enlarges'; f.appendChild(note);
  let amount = +r.value;
  let redrawRaf=0;const redraw=()=>{redrawRaf=0;drawNow();};
  const preview = raw => { const parsed=parseFloat(raw); if(!Number.isFinite(parsed))return; amount=clamp(parsed,-90,700); r.value=amount; n.value=Math.round(amount); c.zoom=toZoom(amount);if(!redrawRaf)redrawRaf=requestAnimationFrame(redraw); };
  r.addEventListener('input',()=>preview(r.value));
  r.addEventListener('change',()=>{if(redrawRaf){cancelAnimationFrame(redrawRaf);redrawRaf=0;}drawNow();putClip(c,{zoom:toZoom(amount)},false);});
  n.addEventListener('input',()=>{if(n.value!=='')preview(n.value);});
  n.addEventListener('change',()=>{if(n.value==='')n.value=Math.round(amount);preview(n.value);putClip(c,{zoom:toZoom(amount)},false);});
  return f;
}
function positionField(c) {
  const f=div('field positionField'),head=div('positionFieldHead');
  head.innerHTML='<div><label>Position</label><small>Move the object within the frame</small></div>';
  const reset=document.createElement('button');reset.textContent='Reset';reset.className='colorReset';head.appendChild(reset);f.appendChild(head);
  const values={x:Number.isFinite(+(c.position||{}).x)?+(c.position||{}).x:0,y:Number.isFinite(+(c.position||{}).y)?+(c.position||{}).y:0};
  const update=(key,value,commit)=>{values[key]=value;c.position={...values};drawNow();if(commit)putClip(c,{position:{...values}},false);};
  f.appendChild(precisionRangeField('Position X',values.x,-100,100,1,v=>update('x',v,false),v=>update('x',v,true),'%'));
  f.appendChild(precisionRangeField('Position Y',values.y,-100,100,1,v=>update('y',v,false),v=>update('y',v,true),'%'));
  reset.addEventListener('click',()=>{values.x=values.y=0;c.position={x:0,y:0};drawNow();putClip(c,{position:{x:0,y:0}});renderInspector();});
  return f;
}
function colorEffectsField(c){
  const f=div('field colorEffects'),v=colorSettings(c),head=div('colorEffectsHead');head.innerHTML='<div><label>Color</label><small>Clip-level correction · included in export</small></div>';
  const controls=div('colorHeadControls'),toggle=document.createElement('button');toggle.className='colorBypass transitionToggle'+(v.enabled===false?'':' on');toggle.setAttribute('role','switch');toggle.setAttribute('aria-checked',v.enabled===false?'false':'true');toggle.title='Bypass color effects';toggle.innerHTML='<span></span>';toggle.addEventListener('click',()=>{focusClipPreview(c);v.enabled=v.enabled===false;c.color={...v};drawNow();putClip(c,{color:v});});
  const reset=document.createElement('button');reset.className='colorReset';reset.textContent='Reset';reset.addEventListener('click',()=>{focusClipPreview(c);c.color={enabled:true,exposure:0,contrast:1,saturation:1,temperature:0,tint:0,highlights:0,shadows:0};drawNow();putClip(c,{color:c.color});});controls.append(toggle,reset);head.appendChild(controls);f.appendChild(head);
  const section=div('colorSectionLabel');section.textContent='Base correction';f.appendChild(section);
  const add=(label,key,min,max,step)=>f.appendChild(precisionRangeField(label,v[key],min,max,step,value=>{focusClipPreview(c);v[key]=value;c.color={...v};drawNow();},value=>{v[key]=value;c.color={...v};drawNow();putClip(c,{color:{...v}},false);}));
  add('Exposure', 'exposure',-2,2,.01);add('Contrast','contrast',0,2,.01);add('Saturation','saturation',0,2,.01);add('Temperature','temperature',-1,1,.01);add('Tint','tint',-1,1,.01);
  const expanded=div('colorSectionLabel');expanded.textContent='Tonal range';f.appendChild(expanded);add('Highlights','highlights',-1,1,.01);add('Shadows','shadows',-1,1,.01);return f;
}
function blurEffectField(c){
  const value={enabled:true,amount:0,...(c.blur||{})},f=div('field effectControl'),head=div('effectControlHead');head.innerHTML='<label>Blur</label>';
  const toggle=document.createElement('button');toggle.className='transitionToggle'+(value.enabled!==false?' on':'');toggle.setAttribute('role','switch');toggle.setAttribute('aria-checked',String(value.enabled!==false));toggle.innerHTML='<span></span>';head.appendChild(toggle);f.appendChild(head);
  const update=(amount,commit)=>{value.amount=amount;c.blur={...value};focusClipPreview(c);drawNow();if(commit)putClip(c,{blur:{...value}},false);};
  const control=precisionRangeField('Intensity',value.amount,0,40,.1,v=>update(v,false),v=>update(v,true),'px');f.appendChild(control);
  toggle.addEventListener('click',()=>{value.enabled=value.enabled===false;toggle.classList.toggle('on',value.enabled);toggle.setAttribute('aria-checked',String(value.enabled));c.blur={...value};focusClipPreview(c);drawNow();putClip(c,{blur:{...value}},false);});return f;
}
function maskEffectField(c){
  const value=maskSettings(c),f=div('field effectControl maskControl'),head=div('effectControlHead');head.innerHTML='<label>Mask</label>';
  const toggle=document.createElement('button');toggle.className='transitionToggle'+(value.enabled!==false&&value.type!=='none'?' on':'');toggle.setAttribute('role','switch');toggle.innerHTML='<span></span>';head.appendChild(toggle);f.appendChild(head);
  const type=document.createElement('select');type.className='txt maskType';for(const [id,label] of [['none','None'],['split','Split'],['cinematic','Cinematic Bars'],['rectangle','Rectangle'],['ellipse','Ellipse'],['circle','Circle'],['diamond','Diamond'],['heart','Heart'],['star','Star']]){const option=document.createElement('option');option.value=id;option.textContent=label;type.appendChild(option);}type.value=value.type;f.appendChild(type);
  const save=()=>{c.mask={...value};focusClipPreview(c);drawNow();putClip(c,{mask:{...value}},false);};
  const update=(key,number,commit)=>{value[key]=number;c.mask={...value};focusClipPreview(c);drawNow();if(commit)putClip(c,{mask:{...value}},false);};
  const controls=div('maskControls');for(const [label,key,min,max] of [['Position X','x',0,100],['Position Y','y',0,100],['Width','width',1,100],['Height','height',1,100]])controls.appendChild(precisionRangeField(label,value[key],min,max,1,v=>update(key,v,false),v=>update(key,v,true),'%'));f.appendChild(controls);
  const invert=toggleRow('Invert mask',!!value.invert,v=>{value.invert=v;save();});invert.classList.add('maskInvert');f.appendChild(invert);
  const sync=()=>{const on=value.enabled!==false&&value.type!=='none';toggle.classList.toggle('on',on);toggle.setAttribute('aria-checked',String(on));controls.hidden=value.type==='none';invert.hidden=value.type==='none';};
  type.addEventListener('change',()=>{value.type=type.value;value.enabled=type.value!=='none';sync();save();});toggle.addEventListener('click',()=>{if(value.type==='none'){value.type='rectangle';type.value=value.type;value.enabled=true;}else value.enabled=value.enabled===false;sync();save();});sync();return f;
}
function transformKeyframesField(c) {
  const f=div('field keyframeField'),stored=storedTransformPoints(c),enabled=stored.length>0&&c.keyframes.enabled!==false,top=div('keyframeHead transformKeyframeHead');top.innerHTML='<div><label>Transform animation</label><small>Animate zoom and position with editable points on the timeline.</small></div>';
  const toggle=document.createElement('button');toggle.className='transformToggle transitionToggle'+(enabled?' on':'');toggle.setAttribute('role','switch');toggle.setAttribute('aria-checked',enabled?'true':'false');toggle.setAttribute('aria-label','Enable transform animation');toggle.innerHTML='<span></span>';top.appendChild(toggle);f.appendChild(top);
  toggle.addEventListener('click',()=>{let value;if(!stored.length)value={enabled:true,points:[{id:'kf-'+Date.now(),at:0,zoom:1,x:.5,y:.5},{id:'kf-'+(Date.now()+1),at:1,zoom:1.25,x:.5,y:.5}]};else value={...c.keyframes,enabled:!enabled,points:stored};if(value.enabled)c.motion={type:'none'};putClip(c,{keyframes:value,motion:c.motion});});
  if(!enabled)return f;
  const points=stored;c.keyframes={...c.keyframes,enabled:true,points};
  const addAtPlayhead = document.createElement('button'); addAtPlayhead.className='keyframeAddAt'; addAtPlayhead.textContent='◆ Add keyframe at playhead';
  addAtPlayhead.addEventListener('click',()=>{
    const at=clamp((playTime-c.start)/Math.max(.05,c.out-c.in),0,1), current=clipMotion(c,at);
    const id='kf-'+Date.now()+'-'+Math.random().toString(36).slice(2,7);
    const existing=transformPoints(c).map(p=>({...p}));
    const keyframes={enabled:true,points:[...existing,{id,at,zoom:current.zoom/Math.max(.1,c.zoom||1),x:current.center.x,y:current.center.y}].sort((a,b)=>a.at-b.at)};
    selectedKeyframe={clipId:c.id,pointId:id}; putClip(c,{keyframes});
  }); f.appendChild(addAtPlayhead);
  const list=div('keyframeList');
  points.forEach((point,index)=>{
    const card=div('keyframePoint'); card.innerHTML='<div class="keyframePointHead"><b>◆ Keyframe '+(index+1)+'</b><span>'+((point.at*(c.out-c.in)).toFixed(2))+'s</span></div>';
    card.dataset.keyframeId=point.id;
    if (selectedKeyframe && selectedKeyframe.clipId===c.id && selectedKeyframe.pointId===point.id) card.classList.add('selected');
    card.querySelector('.keyframePointHead').addEventListener('click',()=>{if(document.activeElement&&f.contains(document.activeElement))document.activeElement.blur();selectedKeyframe={clipId:c.id,pointId:point.id};playTime=c.start+point.at*(c.out-c.in);drawNow();renderTimeline();$$('.keyframePoint',f).forEach(x=>x.classList.toggle('selected',x===card));const zoomInput=card.querySelector('input');if(zoomInput){zoomInput.focus({preventScroll:true});zoomInput.select();}});
    const grid=div('keyframeGrid');
    for (const [prop,label,min,max] of [['zoom','Zoom',.1,8],['x','Position X',0,1],['y','Position Y',0,1]]) {
      const wrap=div('keyframeControl'); wrap.innerHTML='<label>'+label+'</label>'; const inp=document.createElement('input'); inp.type='number'; inp.min=min; inp.max=max; inp.step=.01; inp.value=(+point[prop]).toFixed(2);
      inp.addEventListener('focus',()=>{inp.select();card.classList.add('editing');}); inp.addEventListener('blur',()=>card.classList.remove('editing'));
      inp.addEventListener('input',()=>{if(inp.value==='')return;const v=clamp(+inp.value,min,max);if(Number.isFinite(v)){point[prop]=v;playTime=c.start+point.at*(c.out-c.in);drawNow();}}); inp.addEventListener('change',()=>{if(inp.value==='')inp.value=(+point[prop]).toFixed(2);putClip(c,{keyframes:c.keyframes});}); wrap.appendChild(inp); grid.appendChild(wrap);
    }
    const remove=document.createElement('button'); remove.className='keyframeRemove'; remove.textContent='×'; remove.title='Remove this keyframe'; remove.setAttribute('aria-label','Remove keyframe '+(index+1)); remove.addEventListener('click',()=>{if(selectedKeyframe&&selectedKeyframe.pointId===point.id)selectedKeyframe=null;c.keyframes.points=c.keyframes.points.filter(p=>p.id!==point.id);putClip(c,{keyframes:c.keyframes.points.length?c.keyframes:null});});
    card.appendChild(remove); card.appendChild(grid); list.appendChild(card);
  });
  f.appendChild(list);
  if (revealSelectedKeyframe && selectedKeyframe && selectedKeyframe.clipId===c.id) { revealSelectedKeyframe=false; requestAnimationFrame(()=>{const active=f.querySelector('.keyframePoint.selected');if(active)active.scrollIntoView({block:'nearest'});}); }
  return f;
}
function transitionStackField(c) {
  const clipDur=Math.max(.05,c.out-c.in),f=div('field keyframeField transitionEditor'),items=transitionItems(c).map(x=>({...x}));
  const top=div('keyframeHead');top.innerHTML='<div><label>Transition stack</label><small>Choose the clip edge, toggle effects, and edit each diamond independently.</small></div>';f.appendChild(top);
  const list=div('keyframeList');
  items.forEach((item,index)=>{
    const selected=selectedTransition&&selectedTransition.clipId===c.id&&selectedTransition.transitionId===item.id;
    const card=div('keyframePoint transitionPoint'+(selected?' selected':'')+(item.enabled===false?' disabled':''));card.dataset.transitionId=item.id;
    const head=div('keyframePointHead');head.innerHTML='<b>◆ '+esc(item.type)+' '+((item.edge||'start')==='end'?'end':'start')+'</b><span>'+clamp(+item.dur||0,0,clipDur).toFixed(2)+'s</span>';head.addEventListener('click',()=>{selectedTransition={clipId:c.id,transitionId:item.id};playTime=c.start+((item.edge||'start')==='end'?clipDur-item.dur:item.dur);drawNow();renderTimeline();renderInspector();});card.appendChild(head);
    const remove=document.createElement('button');remove.className='keyframeRemove';remove.textContent='×';remove.title='Remove transition';remove.addEventListener('click',()=>{const next=items.filter(x=>x.id!==item.id);if(selectedTransition&&selectedTransition.transitionId===item.id)selectedTransition=null;putClip(c,{transition:transitionPayload(next)});});card.appendChild(remove);
    const edge=div('transitionEdge seg');['start','end'].forEach(value=>{const b=document.createElement('button');b.textContent=value==='start'?'Clip start':'Clip end';if((item.edge||'start')===value)b.classList.add('on');b.addEventListener('click',()=>{item.edge=value;c.transition=transitionPayload(items);selectedTransition={clipId:c.id,transitionId:item.id};putClip(c,{transition:c.transition});});edge.appendChild(b);});card.appendChild(edge);
    const grid=div('keyframeGrid'),control=div('keyframeControl');control.innerHTML='<label>Duration (s)</label>';const input=document.createElement('input');input.type='number';input.min=0;input.max=clipDur;input.step=.01;input.value=clamp(+item.dur||0,0,clipDur).toFixed(2);input.addEventListener('focus',()=>{selectedTransition={clipId:c.id,transitionId:item.id};input.select();card.classList.add('selected');});input.addEventListener('input',()=>{if(input.value==='')return;const dur=clamp(+input.value,0,clipDur);if(Number.isFinite(dur)){item.dur=dur;c.transition=transitionPayload(items);playTime=c.start+((item.edge||'start')==='end'?clipDur-dur:dur);drawNow();renderTimeline();}});input.addEventListener('change',()=>putClip(c,{transition:transitionPayload(items)}));control.appendChild(input);grid.appendChild(control);
    const toggle=div('keyframeControl');toggle.innerHTML='<label>Enabled</label>';const tb=document.createElement('button');tb.className='transitionToggle'+(item.enabled===false?'':' on');tb.setAttribute('role','switch');tb.setAttribute('aria-checked',item.enabled===false?'false':'true');tb.setAttribute('aria-label','Enable '+item.type+' transition');tb.innerHTML='<span></span>';tb.addEventListener('click',()=>{item.enabled=item.enabled===false;tb.classList.toggle('on',item.enabled);tb.setAttribute('aria-checked',item.enabled?'true':'false');c.transition=transitionPayload(items);putClip(c,{transition:c.transition});});toggle.appendChild(tb);grid.appendChild(toggle);card.appendChild(grid);list.appendChild(card);
  });f.appendChild(list);return f;
}
function toggleRow(label, on, onToggle) {
  const f = div('field'); const row = div('togglerow');
  const t = div('t'); t.textContent = label; row.appendChild(t);
  const sw = div('switch' + (on ? ' on' : ''));
  sw.addEventListener('click', () => { const next = !on; sw.classList.toggle('on', next); onToggle(next); });
  row.appendChild(sw); f.appendChild(row); return f;
}
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

/* ---------------- generation ---------------- */
const PROMPT_TIPS = {
  subject: 'Lead with the subject and what they are doing. Be specific: "a woman in a red coat", "a dog running through shallow water". One clear subject per shot keeps results coherent.',
  camera: 'Name the camera move and lens feel: "slow dolly-in", "handheld", "wide establishing shot", "close-up", "over-the-shoulder". This drives composition and motion.',
  light: 'Set the lighting and mood: "golden hour", "soft blue dawn light", "neon glow", "harsh midday sun", "candlelit". Light defines the emotional tone.',
  style: 'Pin the visual style: "cinematic", "3D animated", "documentary", "film grain", "shallow depth of field". Keep it consistent across your scenes for a unified look.',
};
function bindPromptHelp() {
  const help = $('#promptHelp'); const tip = $('#promptTip');
  if (!help) return;
  $$('#promptHelp button').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.k;
    if (tip.dataset.k === k) { tip.dataset.k = ''; tip.textContent = ''; return; }
    tip.dataset.k = k; tip.textContent = PROMPT_TIPS[k] || '';
  }));
}
function bindInfoTooltips() {
  const tip=div('floatingInfoTooltip');tip.setAttribute('role','tooltip');document.body.appendChild(tip);
  const show=icon=>{const copy=icon.dataset.tooltip;if(!copy)return;tip.textContent=copy;tip.classList.add('on');tip.style.left='0px';tip.style.top='0px';const r=icon.getBoundingClientRect(),tr=tip.getBoundingClientRect(),left=clamp(r.left+r.width/2-tr.width/2,8,window.innerWidth-tr.width-8);let top=r.top-tr.height-8;if(top<8)top=r.bottom+8;tip.style.left=Math.round(left)+'px';tip.style.top=Math.round(top)+'px';};
  const hide=()=>tip.classList.remove('on');
  $$('.info[data-tooltip]').forEach(icon=>{icon.addEventListener('pointerenter',()=>show(icon));icon.addEventListener('pointerleave',hide);icon.addEventListener('focus',()=>show(icon));icon.addEventListener('blur',hide);});
  window.addEventListener('scroll',hide,true);window.addEventListener('resize',hide);
}
function updateSecsHint() {
  const f = $('#genFrames'); const hint = $('#genSecsHint');
  if (f && hint) { const v = parseFloat(f.value); const seconds = isFinite(v) ? (v / 24).toFixed(1) : '0'; hint.textContent = '≈ ' + seconds + 's'; if ($('#composerDuration')) $('#composerDuration').textContent = seconds + 's'; }
}
let selChars = new Set();
let selRefs = new Set();
let selectedCharRefs = new Map();
let selCharsInitialized = false;
let chainDefault = false; // legacy scene-chain flag; explicit media sources are used in the composer
function prioritizedCharacterRefs(character){
  const score=id=>{const name=String((mediaById(id)||{}).name||'').toLowerCase();return name.includes('three-quarter')?1:name.includes('front face')?2:name.includes('front')?0:name.includes('left')?3:name.includes('right')?4:name.includes('back')?5:6;};
  return charImageIds(character).filter(id=>mediaById(id)).map((id,index)=>({id,index})).sort((a,b)=>score(a.id)-score(b.id)||a.index-b.index).map(x=>x.id);
}
function normalizeCharacterReferenceSelection(){
  if(!state)return;
  for(const id of Array.from(selectedCharRefs.keys()))if(!selChars.has(id))selectedCharRefs.delete(id);
  for(const id of selChars){const c=state.characters.find(x=>x.id===id);if(!c)continue;const valid=prioritizedCharacterRefs(c);if(!selectedCharRefs.has(id))selectedCharRefs.set(id,new Set(valid));else selectedCharRefs.set(id,new Set(Array.from(selectedCharRefs.get(id)).filter(mid=>valid.includes(mid))));}
  const visual=Array.from(selRefs).filter(id=>state.media.some(m=>m.id===id&&m.kind==='image')).length+(sourceSelection?1:0),budget=Math.max(0,9-visual),ids=Array.from(selChars).filter(id=>selectedCharRefs.has(id));
  let total=ids.reduce((n,id)=>n+selectedCharRefs.get(id).size,0);
  while(total>budget){let candidate=null;for(const id of ids){const size=selectedCharRefs.get(id).size;if(size>1&&(!candidate||size>selectedCharRefs.get(candidate).size))candidate=id;}if(!candidate)break;const ordered=prioritizedCharacterRefs(state.characters.find(c=>c.id===candidate));for(let i=ordered.length-1;i>=0;i--)if(selectedCharRefs.get(candidate).delete(ordered[i])){total--;break;}}
}
function selectedReferenceKinds(ids=Array.from(selRefs)){
  const media=ids.map(mediaById).filter(Boolean),images=media.filter(m=>m.kind==='image'),audio=media.filter(m=>m.kind==='audio');
  return {media,images,audio,audioSeconds:audio.reduce((sum,m)=>sum+(+m.duration||0),0)};
}
function referenceSelectionError(ids=Array.from(selRefs),includeCast=true){
  const refs=selectedReferenceKinds(ids),visual=refs.images.length+(includeCast?Array.from(allocatedCharacterReferenceCounts().values()).reduce((s,n)=>s+n,0):0)+(sourceSelection?1:0);
  if(refs.audio.length>3)return 'MiniMax H3 accepts at most 3 audio references.';
  if(refs.audio.some(m=>(+m.duration||0)<2||(+m.duration||0)>15))return 'Each audio reference must be between 2 and 15 seconds.';
  if(refs.audioSeconds>15.001)return 'Audio references may total at most 15 seconds.';
  if(refs.audio.length&&!visual)return 'Add at least one image or video reference when using audio as an H3 reference.';
  if(sourceSelection&&refs.audio.length)return 'Audio references cannot be combined with an exact opening-frame anchor. Remove the opening frame or use storyboard reference continuity.';
  return '';
}
function allocatedCharacterReferenceCounts(){normalizeCharacterReferenceSelection();return new Map(Array.from(selChars).map(id=>[id,(selectedCharRefs.get(id)||new Set()).size]));
}
function characterReferencePayload(){normalizeCharacterReferenceSelection();return Object.fromEntries(Array.from(selChars).map(id=>[id,Array.from(selectedCharRefs.get(id)||[])]));
}
function recoverStructuredPromptCast(prompt){
  if(!isStructuredH3Prompt(prompt)||selChars.size||!state)return;
  const header=String(prompt).split(/\b(?:summary|retention_analysis|detailed_description):/i)[0];
  const recovered=[];
  for(const character of state.characters){
    const escaped=character.name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    if(new RegExp('<Subject\\s+\\d+>[^\\n.]{0,220}\\b'+escaped+'\\b','i').test(header)){
      selChars.add(character.id);selectedCharRefs.set(character.id,new Set(prioritizedCharacterRefs(character)));recovered.push(character.name);
    }
  }
  if(recovered.length){normalizeCharacterReferenceSelection();renderGenerate();toast('Attached Cast from structured prompt: '+recovered.join(' and '),'ok');}
}
function promptSkillInstruction(skill) {
  if (!skill) return '';
  return skill.instruction || (skill.description + ' Follow this workflow: ' + (skill.steps || []).join('; ') + '. Keep the result chronological, duration-safe, reference-safe, and compliant with MiniMax H3 shot, text, dialogue, soundscape, and music fields.');
}
function renderActivePromptSkill() {
  const wrap = $('#activePromptSkills'); if (!wrap) return; wrap.innerHTML = '';
  if (!activePromptSkill) return;
  const chip = document.createElement('button'); chip.className = 'activeSkillChip';
  chip.innerHTML = '<span>/' + esc(activePromptSkill.id) + '</span><b>×</b>';
  chip.title = 'Remove prompt skill'; chip.addEventListener('click', () => { activePromptSkill = null; renderActivePromptSkill(); });
  wrap.appendChild(chip);
}
const MODEL_CATALOG = [
  { id: 'h3', name: 'MiniMax H3', type: 'Video', detail: 'Local h3.c engine · 24fps · 4–15s', available: true },
  { id: 'music', name: 'MiniMax Music', type: 'Music', detail: 'Music generation · coming soon', available: false }
];
let selectedModel = 'h3';
let composerPickerMode = 'prompt';
let sideSheetOpenToken=0;
function revealSideSheet(root,panel){
  const token=++sideSheetOpenToken;
  root.classList.remove('on');root.setAttribute('aria-hidden','false');
  void panel.offsetWidth;
  requestAnimationFrame(()=>requestAnimationFrame(()=>{if(token===sideSheetOpenToken)root.classList.add('on');}));
}
function closeComposerPicker() {
  sideSheetOpenToken++;
  $('#composerPicker').classList.remove('on','detail-open','template-sheet');
  $('#composerPickerDetail').classList.remove('characterEditor', 'sheetComposer', 'castReferenceEditor');
  $('.sheetHead', $('#composerPicker')).classList.remove('nosub');
  const sub = $('#composerPickerSubtitle');
  if (sub) sub.style.display = '';
  $('#composerPicker').setAttribute('aria-hidden','true');
}
function applyProjectStyle(t) {
  selectedTemplate = t;
  const profile = { name: t.name, prompt: t.style, skill_id: t.id, source: 'OpenMagia H3 style' };
  api('/api/project', { method: 'POST', body: { style_profile: profile, style_enabled:true } }).then(() => {
    state.style_profile = profile; state.style_enabled=true; renderGenerate(); closeComposerPicker(); toast('Project style applied', 'ok');
  }).catch(e => toast(e.message, 'err'));
}
function renderComposerPicker() {
  const wrap = $('#composerPickerList'); wrap.innerHTML = ''; $('#composerPickerDetail').innerHTML = '';
  wrap.classList.toggle('sourcePickerGrid', composerPickerMode === 'source');
  const filters = $('#composerPickerFilters'); filters.innerHTML = '';
  const q = ($('#composerPickerSearch').value || '').toLowerCase();
  if (composerPickerMode === 'models') {
    $('#composerPickerTitle').textContent = 'Models';
    $('#composerPickerSubtitle').textContent = 'Choose the generator for this composer.'; $('#composerPickerSubtitle').style.display = '';
    for (const model of MODEL_CATALOG.filter(m => (m.name + ' ' + m.type + ' ' + m.detail).toLowerCase().includes(q))) {
      const b = document.createElement('button'); b.className = 'pickerRow' + (selectedModel === model.id ? ' on' : ''); b.disabled = !model.available;
      b.innerHTML = '<span class="pickerIcon">' + (model.type === 'Video' ? '▶' : '♫') + '</span><span><strong>' + esc(model.name) + '</strong><small>' + esc(model.type + ' · ' + model.detail) + '</small></span><em>' + (selectedModel === model.id ? 'Selected' : (model.available ? 'Select' : 'Soon')) + '</em>';
      b.addEventListener('click', () => { selectedPickerItem = model; renderPickerDetail(); }); wrap.appendChild(b);
    }
    return;
  }
  if (composerPickerMode === 'source') {
    $('#composerPickerTitle').textContent = 'Continue frame from media';
    $('#composerPickerSubtitle').textContent = 'Choose the exact media frame that should open the new scene.'; $('#composerPickerSubtitle').style.display = '';
    const items = state.media.filter(m => ['image','video'].includes(m.kind) && (!m.status || ['ready','queued','running'].includes(m.status)) && (m.name + ' ' + m.source).toLowerCase().includes(q));
    items.sort((a,b) => (['queued','running'].includes(b.status) ? 1 : 0) - (['queued','running'].includes(a.status) ? 1 : 0));
    for (const m of items) {
      const generating = ['queued','running'].includes(m.status);
      const b = document.createElement('button'); b.className = 'sourcePickerCard' + (sourceSelection && sourceSelection.mediaId === m.id ? ' on' : '');
      const scene = state.scenes.find(s => s.mediaId === m.id);
      const preview = generating ? '<span class="generationPending"><span></span><b>Generating</b></span>' : m.kind === 'image' ? '<img src="' + esc(mediaUrl(m)) + '" alt="">' : '<video src="' + esc(mediaUrl(m)) + '" muted preload="metadata"' + (m.thumb ? ' poster="' + esc(mediaPathUrl(m.thumb)) + '"' : '') + '></video>';
      b.innerHTML = '<span class="sourceThumb">' + preview + '<em>' + (generating ? 'NEXT' : scene ? 'SCENE' : m.kind === 'image' ? 'IMAGE' : 'VIDEO') + '</em></span><span class="sourceMeta"><strong>' + esc(m.name) + '</strong><small>' + esc(generating ? 'Continue from this generating media' : m.kind === 'image' ? 'Still image' : fmtDur(m.duration)) + '</small></span>';
      b.addEventListener('click', () => { selectedPickerItem = m; pendingSourceFrame = m.kind === 'image' ? 'image' : 'last'; renderPickerDetail(); }); wrap.appendChild(b);
    }
    return;
  }
  if (composerPickerMode === 'references') {
    $('#composerPickerTitle').textContent = 'Add references';
    $('#composerPickerSubtitle').textContent = 'Use images for visual identity and audio for music, rhythm, voice timbre, dialogue timing, or sound.'; $('#composerPickerSubtitle').style.display = '';
    const upload=document.createElement('button');upload.className='btn ghost referenceUpload';upload.textContent='＋ Upload image or audio';upload.addEventListener('click',()=>$('#referenceFile').click());filters.appendChild(upload);
    wrap.classList.add('sourcePickerGrid');
    const items=state.media.filter(m=>(!m.status||m.status==='ready')&&['image','audio'].includes(m.kind)&&(m.name+' '+m.source).toLowerCase().includes(q));
    for(const m of items){const b=document.createElement('button');b.className='sourcePickerCard'+(selRefs.has(m.id)?' on':'');const preview=m.kind==='image'?'<img src="'+esc(mediaUrl(m))+'" alt="">':'<span class="audioReferenceIcon">♫</span>';b.innerHTML='<span class="sourceThumb">'+preview+'<em>'+(m.kind==='audio'?'AUDIO':'IMAGE')+'</em></span><span class="sourceMeta"><strong>'+esc(m.name)+'</strong><small>'+(selRefs.has(m.id)?'Added to prompt':m.kind==='audio'?fmtDur(m.duration)+' · H3 audio reference':'Available in Media')+'</small></span>';b.addEventListener('click',()=>{selectedPickerItem=m;renderPickerDetail();});wrap.appendChild(b);}
    if(!items.length)wrap.innerHTML='<div class="gempty"><p>Upload an image or compatible audio file to use it as an H3 reference.</p></div>';
    return;
  }
  if (composerPickerMode === 'cast') {
    $('#composerPickerTitle').textContent = 'Add cast';
    $('#composerPickerSubtitle').textContent = 'Choose characters whose references should be used in this scene.'; $('#composerPickerSubtitle').style.display = '';
    const create=document.createElement('button');create.className='btn ghost referenceUpload';create.textContent='＋ Create character';create.addEventListener('click',()=>openCharacterSheet());filters.appendChild(create);
    for (const character of state.characters.filter(c => c.name.toLowerCase().includes(q))) {
      const count = charImageIds(character).length;
      const b = document.createElement('button'); b.className = 'pickerRow' + (selChars.has(character.id) ? ' on' : '');
      const first=mediaById(charImageIds(character)[0]);
      const thumb=first?'<img src="'+esc(mediaUrl(first))+'" alt="">':'<span>◉</span>';
      b.innerHTML = '<span class="pickerIcon castPickerThumb">'+thumb+'</span><span><strong>' + esc(character.name) + '</strong><small>' + count + ' identity reference' + (count === 1 ? '' : 's') + (character.description?' · identity notes':'') + '</small></span><em>' + (selChars.has(character.id) ? 'Added' : 'Cast') + '</em>';
      b.addEventListener('click', () => { selectedPickerItem = character; renderPickerDetail(); }); wrap.appendChild(b);
    }
    if (!state.characters.length) wrap.innerHTML = '<div class="gempty"><p>No cast members yet. Create one to add ordered identity references.</p></div>';
    return;
  }
  const styleMode = composerPickerMode === 'style';
  $('#composerPickerTitle').textContent = styleMode ? 'Project style skills' : 'Prompt skills';
  $('#composerPickerSubtitle').textContent = styleMode ? 'Persistent visual direction applied to every new scene.' : 'Add a structured workflow to this scene prompt.'; $('#composerPickerSubtitle').style.display = '';
  if (styleMode) {
    for (const t of promptTemplates.filter(t => (t.name + ' ' + t.tagline + ' ' + t.style).toLowerCase().includes(q))) {
      const b = document.createElement('button'); b.className = 'pickerRow skillPickerRow' + (selectedTemplate && selectedTemplate.id === t.id ? ' on' : '');
      b.innerHTML = '<span class="skillPickerPreview skillStyleMini">✦</span><span><strong>' + esc(t.name) + '</strong><small>' + esc(t.tagline) + '</small></span><em>Persistent</em>';
      b.addEventListener('click', () => { closeComposerPicker(); openSkillDetail({...t,scope:'style',description:t.tagline,icon:'✦'}, 'style-picker'); }); wrap.appendChild(b);
    }
  } else {
    for (const skill of [...customSkills(),...SKILL_CATALOG].filter(s => (s.name + ' ' + s.id + ' ' + s.description).toLowerCase().includes(q))) {
      const b = document.createElement('button'); b.className = 'pickerRow skillPickerRow' + (activePromptSkill && activePromptSkill.id === skill.id ? ' on' : '');
      b.innerHTML = '<span class="skillPickerPreview'+(skill.custom?' customSkillArt':'')+'">'+skillPreviewMarkup(skill,'picker')+'</span><span><strong>' + esc(skill.name) + ' <code>/' + esc(skill.id) + '</code></strong><small>' + esc(skill.description) + '</small></span><em>'+(skill.custom?'Custom':'Prompt')+'</em>';
      bindSkillPreviewFallback(b);
      b.addEventListener('click', () => { closeComposerPicker(); openSkillDetail(skill, 'prompt-picker'); }); wrap.appendChild(b);
    }
    kickAutoplay(wrap);
  }
}
function renderPickerDetail() {
  const item = selectedPickerItem; const box = $('#composerPickerDetail'); if (!item) { box.innerHTML = ''; return; }
  const modelMode = composerPickerMode === 'models'; const styleMode = composerPickerMode === 'style'; const sourceMode = composerPickerMode === 'source'; const castMode = composerPickerMode === 'cast'; const referenceMode=composerPickerMode==='references';
  const title = item.name; const summary = item.detail || item.tagline || item.description || '';
  box.classList.toggle('referenceDetail', referenceMode);
  if(castMode){
    const refs=charImageIds(item).map(id=>mediaById(id)).filter(Boolean);
    const draft=new Set(selectedCharRefs.get(item.id)||refs.map(m=>m.id));
    box.classList.add('castReferenceEditor');
    box.innerHTML='<div class="castReferenceBody"><span class="scopeLabel">SCENE CAST</span><h3>'+esc(item.name)+'</h3><p class="castReferenceIntro">Choose the exact views H3 should use for this scene. All views are selected by default; the total cast and visual-reference budget is nine.</p><div class="castDetailRefs">'+refs.map((m,i)=>'<button class="castRefOption '+(draft.has(m.id)?'on':'')+'" data-char-ref="'+esc(m.id)+'"><img src="'+esc(mediaUrl(m))+'" alt="'+esc(m.name||('Reference '+(i+1)))+'"><span>'+esc((m.name||('Reference '+(i+1))).replace(item.name+' · ','').replace(item.name+' - ',''))+'</span><b>'+(draft.has(m.id)?'✓':'')+'</b></button>').join('')+'</div><label class="castIdentityLabel" for="castIdentityNotes">Identity notes <small id="castSaveState">Saved automatically</small></label><textarea id="castIdentityNotes" class="txt" placeholder="Describe stable traits that must remain consistent: hairstyle, wardrobe, materials, accessories, palette…">'+esc(item.description||'')+'</textarea><p class="castModelNote">These notes remain attached to the character. Only the selected images above are sent with this scene.</p><button class="btn ghost" id="castEnrich">✦ Enrich with model</button></div><div class="castReferenceFoot"><button class="btn ghost" id="castRemove">'+(selChars.has(item.id)?'Remove from scene':'Cancel')+'</button><button class="btn primary" id="pickerApply">'+(selChars.has(item.id)?'Save references':'Add to scene')+'</button></div>';
    $$('[data-char-ref]',box).forEach(button=>button.addEventListener('click',()=>{const id=button.dataset.charRef;if(draft.has(id))draft.delete(id);else draft.add(id);button.classList.toggle('on',draft.has(id));$('b',button).textContent=draft.has(id)?'✓':'';}));
    let castSaveTimer=0;const notes=$('#castIdentityNotes'),saveState=$('#castSaveState');
    const saveNotes=async()=>{clearTimeout(castSaveTimer);const description=notes.value.trim();item.description=description;saveState.textContent='Saving…';try{await api('/api/characters/'+item.id,{method:'PUT',body:{description}});saveState.textContent='Saved automatically';}catch(e){saveState.textContent='Save failed';toast(e.message,'err');}};
    notes.addEventListener('input',()=>{saveState.textContent='Unsaved changes';clearTimeout(castSaveTimer);castSaveTimer=setTimeout(saveNotes,350);});notes.addEventListener('blur',e=>{if(e.relatedTarget&&e.relatedTarget.id==='castEnrich')return;saveNotes();});
    $('#castEnrich').addEventListener('click',async()=>{clearTimeout(castSaveTimer);const btn=$('#castEnrich'),description=notes.value.trim();btn.disabled=true;btn.textContent='Formatting locally…';try{const out=await api('/api/characters/'+item.id+'/enrich',{method:'POST',body:{description}});item.description=out.description;notes.value=out.description;saveState.textContent='Saved automatically';toast((out.used_ai?'Enriched':'Structured')+' identity notes','ok');}catch(e){toast(e.message,'err');}finally{btn.disabled=false;btn.textContent='✦ Enrich with model';}});
    $('#castRemove',box).addEventListener('click',()=>{const remove=selChars.has(item.id);closeComposerPicker();if(remove){selChars.delete(item.id);selectedCharRefs.delete(item.id);renderGenerate();}});
    $('#pickerApply',box).addEventListener('click',()=>{if(!draft.size){toast('Select at least one reference image for '+item.name,'err');return;}if(!selChars.has(item.id)&&selChars.size+selRefs.size>=9){toast('The nine-reference budget needs at least one reference per character','err');return;}closeComposerPicker();selChars.add(item.id);selectedCharRefs.set(item.id,draft);normalizeCharacterReferenceSelection();renderGenerate();toast(item.name+' added to scene','ok');});
    $('#composerPicker').classList.add('detail-open');return;
  }
  if(referenceMode){
    const audio=item.kind==='audio';box.innerHTML='<div class="sourceDetailPreview">'+(audio?'<span class="audioReferenceIcon">♫</span>':'<img src="'+esc(mediaUrl(item))+'" alt="">')+'</div><span class="scopeLabel">'+(audio?'AUDIO REFERENCE':'VISUAL REFERENCE')+'</span><h3>'+esc(item.name)+'</h3><p>'+(audio?'Use this clip for music, rhythm, voice timbre, dialogue timing, lip sync, ambience, or sound characteristics explicitly named in your prompt. It requires at least one visual reference.':'Use this image for the product, environment, material, palette, label, or design details named in your prompt.')+'</p><div class="referenceDetailActions"><button class="btn primary" id="pickerApply">'+(selRefs.has(item.id)?'Remove reference':'Add reference')+'</button></div>';
    $('#pickerApply').addEventListener('click',()=>{if(selRefs.has(item.id))selRefs.delete(item.id);else{const next=[...selRefs,item.id],error=referenceSelectionError(next);if(error&&!error.startsWith('Add at least one image')){toast(error,'err');return;}selRefs.add(item.id);}normalizeCharacterReferenceSelection();renderGenerate();closeComposerPicker();});
    $('#composerPicker').classList.add('detail-open');return;
  }
  const body = castMode ? 'Include this character’s ordered identity references in the next generation.' : sourceMode ? (item.kind === 'image' ? 'This still image will become the opening visual reference for the generated scene.' : 'Choose whether the first or last frame of this media should become the opening visual reference.') : modelMode ? (item.id === 'h3' ? 'Creates synchronized video and audio locally through h3.c. Accepts 4–15 seconds at 24fps, up to nine ordered images, and up to three audio references totaling 15 seconds.' : 'Reserved for the MiniMax Music workflow. It will use music-specific skills and controls when the model is installed.')
    : styleMode ? item.style : promptSkillInstruction(item);
  const steps = item.steps || (item.defaults ? Object.entries(item.defaults).map(([k,v]) => k + ': ' + v) : []);
  const sourceGenerating = sourceMode && ['queued','running'].includes(item.status);
  const sourcePreview = sourceMode ? '<div class="sourceDetailPreview">' + (sourceGenerating ? '<span class="generationPending"><span></span><b>Generating media</b><small>The last frame will be used when it is ready</small></span>' : item.kind === 'image' ? '<img src="' + esc(mediaUrl(item)) + '" alt="">' : '<video src="' + esc(mediaUrl(item)) + '" muted controls preload="metadata"' + (item.thumb ? ' poster="' + esc(mediaPathUrl(item.thumb)) + '"' : '') + '></video>') + '</div>' : '';
  box.innerHTML = sourcePreview + '<span class="scopeLabel">' + (castMode ? 'SCENE CAST' : sourceMode ? 'OPENING REFERENCE' : modelMode ? 'MODEL DETAILS' : styleMode ? 'PERSISTENT PROJECT STYLE' : 'SCENE PROMPT SKILL') + '</span><h3>' + esc(title) + '</h3><p>' + esc(sourceGenerating ? 'Continue directly from this generation. OpenMagia will take its last frame when ready and locally refine the next prompt for visual continuity.' : summary) + '</p><div class="pickerPreview">' + esc(body) + '</div>' + (sourceMode && item.kind !== 'image' && !sourceGenerating ? '<div class="frameChoice"><button data-frame="first">First frame</button><button class="on" data-frame="last">Last frame</button></div>' : '') + (steps.length ? '<ul>' + steps.map(s => '<li>' + esc(s) + '</li>').join('') + '</ul>' : '') + '<div class="pickerDetailActions"><button class="btn primary" id="pickerApply"' + (modelMode && !item.available ? ' disabled' : '') + '>' + (castMode ? (selChars.has(item.id) ? 'Remove from scene' : 'Add to scene') : sourceMode ? (sourceGenerating ? 'Continue from this generating media' : 'Use as opening frame') : modelMode ? (item.available ? 'Use this model' : 'Coming soon') : styleMode ? 'Apply as project style' : 'Add to scene prompt') + '</button></div>';
  $$('.frameChoice button', box).forEach(b => b.addEventListener('click', () => { pendingSourceFrame = b.dataset.frame; $$('.frameChoice button', box).forEach(x => x.classList.toggle('on', x === b)); }));
  $('#pickerApply').addEventListener('click', () => {
    if (castMode) {
      if (selChars.has(item.id)) selChars.delete(item.id);
      else {
        if (selChars.size+selRefs.size>=9) { toast('The nine-reference budget needs at least one reference per character', 'err'); return; }
        selChars.add(item.id);
      }
      renderGenerate(); closeComposerPicker();
    }
    else if (sourceMode) { sourceSelection = { mediaId:item.id, frame:pendingSourceFrame, name:item.name }; renderSourceContext(); closeComposerPicker(); }
    else if (modelMode) { selectedModel = item.id; $('#modelPickerBtn').textContent = '▣ Models · H3'; closeComposerPicker(); }
    else if (styleMode) applyProjectStyle(item);
    else { activePromptSkill = item; renderActivePromptSkill(); closeComposerPicker(); $('#genPrompt').focus(); }
  });
  if (sourceMode && sourceSelection) {
    const clear = document.createElement('button'); clear.className = 'btn ghost pickerClear'; clear.textContent = 'Generate without opening reference';
    clear.addEventListener('click', () => { sourceSelection = null; renderSourceContext(); closeComposerPicker(); }); box.appendChild(clear);
  }
  $('#composerPicker').classList.add('detail-open');
}
function openComposerPicker(mode) {
  composerPickerMode = mode; selectedPickerItem = null; $('#composerPickerDetail').classList.remove('characterEditor','castReferenceEditor'); $('#composerPickerSearch').value = ''; renderComposerPicker();
  revealSideSheet($('#composerPicker'),$('.pickerPanel',$('#composerPicker')));
  setTimeout(() => $('#composerPickerSearch').focus(), 0);
}
function openCharacterSheet(character=null) {
  const st={name:character&&character.name||'',notes:character&&character.description||'',selected:charImageIds(character||{}).filter(id=>mediaById(id)),query:'',browserFolder:''};
  composerSheetTemplate({
    id:'character',mode:'character',title:character?'Edit character':'Create character',
    subtitle:'Choose ordered identity references and keep stable character details together.',state:()=>st,
    topHtml:s=>'<label class="castIdentityLabel">Name</label><input id="characterName" class="txt" value="'+esc(s.name)+'" placeholder="Character name">',
    bindTop:(box,s)=>$('#characterName').addEventListener('input',e=>{s.name=e.target.value;}),
    gallery:{folderAware:true,columns:3,
      heading:()=> 'Identity references',small:s=>s.selected.length+' / 9 ordered',items:()=>state.media.filter(m=>m.kind==='image'),
      matches:(m,q)=>(m.name||'').toLowerCase().includes(q),searchPlaceholder:'Search images',wrapId:'characterGridWrap',queryKey:'query',
      gridHtml:(m,s)=>'<button class="sourcePickerCard '+(s.selected.includes(m.id)?'on':'')+'" data-character-media="'+esc(m.id)+'"><span class="sourceThumb"><img src="'+esc(mediaUrl(m))+'" alt="" loading="lazy" decoding="async"><em>'+(s.selected.includes(m.id)?'REF '+(s.selected.indexOf(m.id)+1):'IMAGE')+'</em></span><span class="sourceMeta"><strong>'+esc(m.name)+'</strong></span></button>',
      emptyText:(s,hasItems)=>hasItems?'No images match your search.':'Import or upload an image to create this character.',
      bindGrid:(box,s)=>$$('[data-character-media]',box).forEach(button=>button.addEventListener('click',()=>{const id=button.dataset.characterMedia,i=s.selected.indexOf(id);if(i>=0)s.selected.splice(i,1);else if(s.selected.length<9)s.selected.push(id);else{toast('MiniMax H3 supports at most 9 reference images','err');return;}$$('[data-character-media]',box).forEach(card=>{const order=s.selected.indexOf(card.dataset.characterMedia);card.classList.toggle('on',order>=0);const tag=$('em',card);if(tag)tag.textContent=order>=0?'REF '+(order+1):'IMAGE';});$('.characterGalleryHead small',box).textContent=s.selected.length+' / 9 ordered';}))
    },
    middleHtml:s=>'<label class="castIdentityLabel">Identity notes <small>Optional</small></label><textarea id="characterNotes" class="txt" placeholder="Stable hairstyle, wardrobe, materials, accessories and colors…">'+esc(s.notes)+'</textarea><input id="characterFile" type="file" accept="image/*" multiple hidden><button class="btn ghost characterUpload" id="characterUpload">＋ Upload images</button>',
    bindMiddle:(box,s,ctl)=>{$('#characterNotes').addEventListener('input',e=>{s.notes=e.target.value;});$('#characterUpload').addEventListener('click',()=>$('#characterFile').click());$('#characterFile').addEventListener('change',async e=>{const files=Array.from(e.target.files||[]);if(!files.length)return;try{for(const file of files){const m=await api('/api/upload',{method:'POST',raw:file,headers:{'X-File-Name':encodeURIComponent(file.name),'Content-Type':'application/octet-stream'}});s.selected.push(m.id);}await refresh();ctl.redraw();}catch(error){toast(error.message,'err');}});},
    actionsHtml:()=>'<div class="sheetActions"><button class="btn ghost" id="characterCancel">Cancel</button><button class="btn primary" id="characterSave">'+(character?'Save character':'Create character')+'</button></div>',
    bindActions:(box,s,ctl)=>{$('#characterCancel').addEventListener('click',ctl.close);$('#characterSave').addEventListener('click',async()=>{const name=s.name.trim()||'Character',description=s.notes.trim();if(!s.selected.length){toast('Choose at least one identity reference','err');return;}try{if(character)await api('/api/characters/'+character.id,{method:'PUT',body:{name,description,images:s.selected}});else{const created=await api('/api/characters',{method:'POST',body:{name,description,images:s.selected}});selChars.add(created.id);}await refresh();ctl.close();toast(character?'Character updated':'Character created','ok');}catch(error){toast(error.message,'err');}});}
  });
}
function openCustomSkillSheet(){
  const st={name:'',purpose:'',trigger:'',inputs:'',workflow:'',constraints:'',output:''};
  composerSheetTemplate({
    id:'custom-skill',mode:'custom-skill',title:'Create custom skill',subtitle:'Answer a short brief. The local prompt model will turn it into a reusable prompt workflow.',state:()=>st,
    topHtml:s=>'<label class="castIdentityLabel">Skill name</label><input id="customSkillName" class="txt" value="'+esc(s.name)+'" placeholder="Example: Architectural walkthrough director">',
    bindTop:(box,s)=>$('#customSkillName').addEventListener('input',e=>{s.name=e.target.value;}),
    middleHtml:s=>'<div class="customSkillForm"><label class="wide">What should this skill help create?<textarea id="customSkillPurpose" class="txt" placeholder="The outcome and creative specialty…">'+esc(s.purpose)+'</textarea></label><label>When should it be used?<input id="customSkillTrigger" class="txt" placeholder="Requests or situations that activate it"></label><label>What inputs does it need?<input id="customSkillInputs" class="txt" placeholder="Brief, references, copy, duration…"></label><label class="wide">What workflow should it follow?<textarea id="customSkillWorkflow" class="txt" placeholder="Important questions, decisions, and ordered steps…">'+esc(s.workflow)+'</textarea></label><label>What must it preserve or avoid?<textarea id="customSkillConstraints" class="txt" placeholder="Facts, identity, text, safety, continuity…">'+esc(s.constraints)+'</textarea></label><label>What should the final output contain?<textarea id="customSkillOutput" class="txt" placeholder="Prompt structure, checks, deliverables…">'+esc(s.output)+'</textarea></label></div>',
    bindMiddle:(box,s)=>{for(const [id,key] of [['customSkillPurpose','purpose'],['customSkillTrigger','trigger'],['customSkillInputs','inputs'],['customSkillWorkflow','workflow'],['customSkillConstraints','constraints'],['customSkillOutput','output']])$('#'+id).addEventListener('input',e=>{s[key]=e.target.value;});},
    actionsHtml:()=>'<div class="sheetActions"><button class="btn ghost" id="customSkillCancel">Cancel</button><button class="btn primary" id="customSkillCreate">Create custom skill</button></div>',
    bindActions:(box,s,ctl)=>{$('#customSkillCancel').addEventListener('click',ctl.close);$('#customSkillCreate').addEventListener('click',async()=>{const button=$('#customSkillCreate');if(!s.name.trim()||!s.purpose.trim()){toast('Add a skill name and describe what it should create','err');return;}button.disabled=true;button.textContent='Composing locally…';try{const out=await api('/api/prompt/custom-skill',{method:'POST',body:s});const skill={id:out.id,name:s.name.trim(),description:out.description,category:'custom',type:'video',scope:'prompt',custom:true,icon:'✦',steps:out.steps||[],specification:out.specification};saveCustomSkills([skill,...customSkills().filter(x=>x.id!==skill.id)]);ctl.close();skillFilter='custom';$$('[data-skill-filter]').forEach(x=>x.classList.toggle('on',x.dataset.skillFilter==='custom'));renderSkillsCenter();toast((out.used_ai?'Composed':'Structured')+' custom skill locally','ok');}catch(error){toast(error.message,'err');button.disabled=false;button.textContent='Create custom skill';}});}
  });
}
function openReferenceSheet(){
  // Standalone references may use every slot except one identity view per
  // attached character. Adding one automatically releases lower-priority
  // cast views, so the sheet must not count every saved character image.
  const maxRefs=Math.max(0,12-selChars.size-(sourceSelection?1:0));
  const st={sel:Array.from(selRefs).filter(id=>mediaById(id)),q:'',browserFolder:''};
  composerSheetTemplate({id:'reference',mode:'references',title:'Add references',subtitle:'Choose images for visual guidance and audio for music, rhythm, voice, dialogue timing, lip sync, ambience, or sound.',state:()=>st,
    gallery:{folderAware:true,columns:3,heading:()=> 'Image and audio references',small:s=>s.sel.length+' / '+maxRefs+' selected',items:()=>state.media.filter(m=>['image','audio'].includes(m.kind)),matches:(m,q)=>(m.name||'').toLowerCase().includes(q),searchPlaceholder:'Search images, audio, and folders',wrapId:'referenceGridWrap',queryKey:'q',
      gridHtml:(m,s)=>mediaCardHtml(m,{picked:s.sel.includes(m.id),tag:s.sel.includes(m.id)?'SELECTED':m.kind.toUpperCase(),sub:m.kind==='audio'?fmtDur(m.duration)+' · reference audio':m.folder||'Media'}),emptyText:(s,has)=>has?'No references match your search.':'Import an image or audio file to use it as a reference.',
      bindGrid:(box,s)=>$$('[data-media-card]',box).forEach(card=>card.addEventListener('click',()=>{const id=card.dataset.mediaCard,i=s.sel.indexOf(id);if(i>=0)s.sel.splice(i,1);else{const next=[...s.sel,id],error=referenceSelectionError(next);if(error&&!error.startsWith('Add at least one image')){toast(error,'err');return;}if(s.sel.length<maxRefs)s.sel.push(id);else{toast('MiniMax H3 supports at most 12 mixed reference files','err');return;}}card.classList.toggle('on',s.sel.includes(id));const m=mediaById(id),tag=$('em',card);if(tag)tag.textContent=s.sel.includes(id)?'SELECTED':m.kind.toUpperCase();$('.characterGalleryHead small',box).textContent=s.sel.length+' / '+maxRefs+' selected';}))},
    middleHtml:()=>'<input id="referenceSheetFile" type="file" accept="image/*,audio/wav,audio/mpeg,audio/mp4,audio/aac,audio/flac,audio/ogg,.opus" multiple hidden><button class="btn ghost characterUpload" id="referenceSheetUpload">＋ Upload images or audio</button><p class="pickerDetailNote">Audio: WAV, MP3, M4A, AAC, FLAC, OGG, or Opus · 2–15 seconds each · 15 seconds total.</p>',
    bindMiddle:(box,s,ctl)=>{$('#referenceSheetUpload').addEventListener('click',()=>$('#referenceSheetFile').click());$('#referenceSheetFile').addEventListener('change',async e=>{const files=Array.from(e.target.files||[]);try{for(const file of files){const m=await api('/api/upload',{method:'POST',raw:file,headers:{'X-File-Name':encodeURIComponent(file.name),'Content-Type':'application/octet-stream'}});if(['image','audio'].includes(m.kind)&&s.sel.length<maxRefs)s.sel.push(m.id);}await refresh();ctl.redraw();}catch(error){toast(error.message,'err');}});},
    actionsHtml:()=>'<div class="sheetActions"><button class="btn ghost" id="referenceCancel">Cancel</button><button class="btn primary" id="referenceApply">Add references</button></div>',
    bindActions:(box,s,ctl)=>{$('#referenceCancel').addEventListener('click',ctl.close);$('#referenceApply').addEventListener('click',()=>{const error=referenceSelectionError(s.sel);if(error){toast(error,'err');return;}selRefs=new Set(s.sel);normalizeCharacterReferenceSelection();renderGenerate();ctl.close();toast(s.sel.length+' reference'+(s.sel.length===1?'':'s')+' ready','ok');});}
  });
}
function openSourceSheet(){
  const st={selected:sourceSelection&&sourceSelection.mediaId||'',frame:sourceSelection&&sourceSelection.frame||'last',updateStyle:false,q:'',browserFolder:''};
  composerSheetTemplate({id:'source',mode:'source',title:'Continue frame from media',subtitle:'Use a video edge as the exact opening frame. Cast identity notes remain active.',state:()=>st,
    gallery:{folderAware:true,columns:3,heading:()=> 'Videos',small:s=>s.selected?'1 selected':'Choose one',items:()=>state.media.filter(m=>m.kind==='video'&&(!m.status||['ready','queued','running'].includes(m.status))),matches:(m,q)=>(m.name||'').toLowerCase().includes(q),searchPlaceholder:'Search videos and folders',wrapId:'sourceGridWrap',queryKey:'q',
      gridHtml:(m,s)=>mediaCardHtml(m,{picked:s.selected===m.id,tag:['queued','running'].includes(m.status)?'GENERATING':'VIDEO',sub:['queued','running'].includes(m.status)?'Final frame available when generation finishes':m.folder||fmtDur(m.duration)}),emptyText:(s,has)=>has?'No videos match your search.':'Generate or import a video first.',
      bindGrid:(box,s,ctl)=>$$('[data-media-card]',box).forEach(card=>card.addEventListener('click',()=>{s.selected=card.dataset.mediaCard;s.frame='last';ctl.redraw();}))},
    middleHtml:s=>{const m=mediaById(s.selected);if(!m)return '<p class="pickerDetailNote">Choose a video above.</p>';const pending=['queued','running'].includes(m.status);return '<div class="selectedMediaSummary"><strong>'+esc(m.name)+'</strong><small>'+esc(pending?'The next scene will wait, then use this generation’s exact final frame.':'Choose the exact edge frame that must open the next scene. H3 keeps Cast identity through its saved identity notes in this mode.')+'</small></div>'+(!pending?'<div class="frameChoice"><button data-source-frame="first" class="'+(s.frame==='first'?'on':'')+'">First frame</button><button data-source-frame="last" class="'+(s.frame!=='first'?'on':'')+'">Last frame</button></div>':'')+'<label class="continuityOption"><input id="sourceUpdateStyle" type="checkbox" '+(s.updateStyle?'checked':'')+'><span><b>Update project continuity style</b><small>Optional and separate from selecting the frame.</small></span></label>';},
    bindMiddle:(box,s)=>{$$('[data-source-frame]',box).forEach(button=>button.addEventListener('click',()=>{s.frame=button.dataset.sourceFrame;$$('[data-source-frame]',box).forEach(x=>x.classList.toggle('on',x===button));}));const update=$('#sourceUpdateStyle',box);if(update)update.addEventListener('change',()=>s.updateStyle=update.checked);},
    actionsHtml:()=>'<div class="sheetActions"><button class="btn ghost" id="sourceCancel">Cancel</button><button class="btn primary" id="sourceApply">Use as opening frame</button></div>',
    bindActions:(box,s,ctl)=>{$('#sourceCancel').addEventListener('click',ctl.close);$('#sourceApply').addEventListener('click',()=>{const m=mediaById(s.selected);if(!m){toast('Choose a video first','err');return;}sourceSelection={mediaId:m.id,frame:s.frame||'last',name:m.name};renderSourceContext();ctl.close();if(s.updateStyle)createOrUpdateContinuityStyle(m.id,null);});}
  });
}

function sourceContinuityContext(mediaId){
  const m=mediaById(mediaId),scene=state.scenes.find(s=>s.mediaId===mediaId||s.id===(m&&m.scene_id));if(!m)return '';
  const chars=(scene&&scene.character_ids||[]).map(id=>state.characters.find(c=>c.id===id)).filter(Boolean).map(c=>c.name+': '+(c.description||'identity locked by references'));
  return ['Selected continuation media: '+m.name,scene&&scene.prompt?'Previous scene action: '+compactScenePrompt(scene.prompt):'',chars.length?'Attached character locks: '+chars.join(' | '):'',scene&&scene.guide_answers?'Original refinement context: '+JSON.stringify(scene.guide_answers):''].filter(Boolean).join('\n');
}
function compactScenePrompt(prompt){const text=String(prompt||'').trim();if(text.length<=6000)return text;const start=text.lastIndexOf('CUT 01'),end=text.indexOf('overall_soundscape:',start);return start>=0?text.slice(start,end>start?end:undefined).trim().slice(0,5500):text.slice(-5500);}

function storyboardCurrentReferences(){
  normalizeCharacterReferenceSelection();
  return {character_ids:Array.from(selChars),character_reference_ids:characterReferencePayload(),reference_media_ids:Array.from(selRefs)};
}
function storyboardNewCard(index){
  const refs=storyboardCurrentReferences();
  return {id:'draft-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,7),name:'Scene '+(index+1),prompt:'',...refs,continue_previous:index>0,source_media_id:index===0&&sourceSelection?sourceSelection.mediaId:null,source_name:index===0&&sourceSelection?sourceSelection.name:''};
}
function ensureStoryboardDraft(){
  if(storyboardDraft)return storyboardDraft;
  const saved=state&&state.storyboard_draft;
  if(saved&&Array.isArray(saved.scenes)&&saved.scenes.length>=2)storyboardDraft=JSON.parse(JSON.stringify(saved));
  else storyboardDraft={id:'storyboard-'+Date.now().toString(36),style_profile:{...(state.style_profile||{}),prompt:String($('#genStyle').value||((state.style_profile||{}).prompt||''))},use_project_style:state.style_enabled!==false,optimize_scenes:false,output:{aspect:$('#genAspect').value||canvasAspect(state.canvas),resolution:$('#genResolution').value,quality:$('#genQuality').value,frames:+$('#genFrames').value||56,steps:+$('#genSteps').value||30,seed:+$('#genSeed').value||42,audio_mode:$('#genAudioMode').value,audio_notes:$('#genAudioNotes').value},scenes:[]};
  while(storyboardDraft.scenes.length<2)storyboardDraft.scenes.push(storyboardNewCard(storyboardDraft.scenes.length));
  return storyboardDraft;
}
function storyboardReferenceItems(card){
  const items=[];
  for(const cid of card.character_ids||[]){const character=state.characters.find(c=>c.id===cid);if(!character)continue;const ids=(card.character_reference_ids||{})[cid]||[];for(const mid of ids){const media=mediaById(mid);if(media)items.push({media,label:character.name,type:'cast',characterId:cid});}}
  for(const mid of card.reference_media_ids||[]){const media=mediaById(mid);if(media)items.push({media,label:media.name,type:'reference'});}
  return items;
}
function storyboardPromptSkills(){return [...customSkills(),...SKILL_CATALOG];}
function storyboardPromptSkill(card){return storyboardPromptSkills().find(skill=>skill.id===card.prompt_skill_id)||null;}
async function storyboardSkillWithSpecification(card){
  const skill=storyboardPromptSkill(card);if(!skill)return null;
  // Execution uses the concise catalog contract. Full SKILL.md documents are
  // for the human detail view and must never be injected into the local model.
  return skill;
}
function storyboardReferenceCount(card,index){return storyboardReferenceItems(card).length+((card.source_media_id||(index>0&&card.continue_previous!==false))?1:0);}
function storyboardBudgetLabel(card,index){
  const anchored=!!(card.source_media_id||(index>0&&card.continue_previous!==false));
  const audio=(card.reference_media_ids||[]).filter(id=>(mediaById(id)||{}).kind==='audio').length;
  if(!anchored)return storyboardReferenceItems(card).length+' refs'+(audio?' · '+audio+' audio':'');
  if(audio)return '1 continuity frame · Ref2VA · '+audio+' audio';
  const cast=(card.character_ids||[]).length;
  return '1 frame'+(cast?' · '+cast+' Cast identity note'+(cast===1?'':'s'):'');
}
function constrainStoryboardReferences(card,index){
  let remaining=9-((card.source_media_id||(index>0&&card.continue_previous!==false))?1:0),kept=0;
  const selected={};
  for(const cid of card.character_ids||[]){const ids=((card.character_reference_ids||{})[cid]||[]).slice(0,Math.max(0,remaining));if(ids.length){selected[cid]=ids;remaining-=ids.length;kept+=ids.length;}}
  card.character_reference_ids=selected;card.character_ids=(card.character_ids||[]).filter(cid=>selected[cid]);const audio=(card.reference_media_ids||[]).filter(id=>(mediaById(id)||{}).kind==='audio').slice(0,3),images=(card.reference_media_ids||[]).filter(id=>(mediaById(id)||{}).kind==='image').slice(0,Math.max(0,remaining));card.reference_media_ids=[...images,...audio];kept+=card.reference_media_ids.length;
  return kept;
}
function storyboardAnchorCount(card,index){return (card.source_media_id||(index>0&&card.continue_previous!==false))?1:0;}
function openStoryboardReferencePicker(card,index,mode){
  const selected=new Set(mode==='cast'?Object.values(card.character_reference_ids||{}).flat():mode==='skill'?(card.prompt_skill_id?[card.prompt_skill_id]:[]):(card.reference_media_ids||[]));storyboardPickerState={card,index,mode,selected,q:'',folder:''};
  $('#storyboardPickerTitle').textContent=mode==='cast'?'Add cast':mode==='skill'?'Add skill':'Add references';$('#storyboardPickerSubtitle').textContent=mode==='cast'?'Choose the exact character views used for this scene.':mode==='skill'?'Choose one prompt workflow for this scene. It will be applied during Refine and before generation.':'Choose visual references and up to three audio references for music, rhythm, voice, timing, or sound.';$('#storyboardPickerSearch').value='';$('#storyboardPickerSearch').placeholder=mode==='cast'?'Search cast':mode==='skill'?'Search skills':'Search images and audio';$('#storyboardPickerApply').textContent=mode==='cast'?'Save cast':mode==='skill'?'Use skill':'Save references';
  const picker=$('#storyboardReferencePicker'),panel=$('.storyboardPickerPanel',picker);panel.classList.toggle('skillMode',mode==='skill');panel.classList.toggle('referenceMode',mode==='references');renderStoryboardReferencePicker();picker.classList.add('on');picker.setAttribute('aria-hidden','false');$('#storyboardPickerSearch').focus({preventScroll:true});
}
function closeStoryboardReferencePicker(){storyboardPickerState=null;const picker=$('#storyboardReferencePicker');picker.classList.remove('on');picker.setAttribute('aria-hidden','true');$('.storyboardPickerPanel',picker).classList.remove('skillMode','referenceMode');}
function storyboardPickerFixedCount(){if(!storyboardPickerState)return 0;const {card,index,mode}=storyboardPickerState;const other=mode==='cast'?(card.reference_media_ids||[]).length:Object.values(card.character_reference_ids||{}).reduce((sum,ids)=>sum+ids.length,0);return storyboardAnchorCount(card,index)+other;}
function toggleStoryboardPickerItem(id){const picker=storyboardPickerState;if(!picker)return;if(picker.selected.has(id))picker.selected.delete(id);else{const m=mediaById(id),audio=Array.from(picker.selected).filter(x=>(mediaById(x)||{}).kind==='audio').length;if(m&&m.kind==='audio'&&audio>=3){toast('MiniMax H3 accepts at most 3 audio references','err');return;}const limit=picker.mode==='references'?12:9;if(storyboardPickerFixedCount()+picker.selected.size>=limit){toast('This scene already uses all reference slots','err');return;}picker.selected.add(id);}renderStoryboardReferencePicker();}
function renderStoryboardReferencePicker(){
  const picker=storyboardPickerState;if(!picker)return;const content=$('#storyboardPickerContent'),q=picker.q.toLowerCase();content.innerHTML='';
  if(picker.mode==='skill'){
    const skills=storyboardPromptSkills().filter(skill=>(skill.name+' '+skill.id+' '+skill.description).toLowerCase().includes(q));
    const group=div('storyboardPickerGroup storyboardSkillPickerList');
    group.innerHTML=skills.map(skill=>'<button class="pickerRow skillPickerRow '+(picker.selected.has(skill.id)?'on':'')+'" data-storyboard-skill="'+esc(skill.id)+'"><span class="skillPickerPreview '+(skill.custom?'customSkillArt':'')+'">'+skillPreviewMarkup(skill,'picker')+'</span><span><strong>'+esc(skill.name)+'</strong><small>'+esc(skill.description)+'</small></span><em>'+(picker.selected.has(skill.id)?'Selected':skill.custom?'Custom':'Prompt')+'</em></button>').join('')||'<div class="gempty"><p>No skills match this search.</p></div>';
    content.appendChild(group);bindSkillPreviewFallback(group);kickAutoplay(group);
    $$('[data-storyboard-skill]',content).forEach(button=>button.addEventListener('click',()=>{picker.selected=new Set([button.dataset.storyboardSkill]);renderStoryboardReferencePicker();}));
    $('#storyboardPickerBudget').textContent='';
    return;
  }else if(picker.mode==='cast'){
    for(const character of state.characters){const refs=prioritizedCharacterRefs(character).map(mediaById).filter(Boolean).filter(m=>!q||(character.name+' '+m.name).toLowerCase().includes(q));if(!refs.length)continue;const group=div('storyboardPickerGroup');group.innerHTML='<h3>'+esc(character.name)+'</h3><p>Select only the views this scene needs.</p><div class="storyboardPickerGrid">'+refs.map(m=>'<button class="storyboardPickerItem '+(picker.selected.has(m.id)?'on':'')+'" data-storyboard-pick="'+esc(m.id)+'"><img src="'+esc(mediaUrl(m))+'" alt=""><span>'+esc((m.name||character.name).replace(character.name+' · ','').replace(character.name+' - ',''))+'</span>'+(picker.selected.has(m.id)?'<b>✓</b>':'')+'</button>').join('')+'</div>';content.appendChild(group);}
  }else{
    const folder=picker.folder||'';
    const images=state.media.filter(m=>['image','audio'].includes(m.kind)&&(m.folder||'')===folder&&(!q||(m.name||'').toLowerCase().includes(q)));
    const folderTiles=!folder&&!q?(state.mediaFolders||[]).map(path=>({path,count:state.media.filter(m=>['image','audio'].includes(m.kind)&&(m.folder||'')===path).length})).filter(x=>x.count):[];
    const group=div('storyboardPickerGroup');
    group.innerHTML=(folder?'<div class="storyboardFolderNav"><button class="sheetFolderBack" data-storyboard-folder-back>← All media</button><span class="sheetFolderCurrent">'+esc(folder)+'</span></div>':'')+'<div class="storyboardPickerGrid">'+folderTiles.map(x=>'<button class="sheetFolderCard" data-storyboard-folder="'+esc(x.path)+'"><span>📁</span><strong>'+esc(x.path)+'</strong><small>'+x.count+' reference'+(x.count===1?'':'s')+'</small></button>').join('')+images.map(m=>'<button class="storyboardPickerItem '+(picker.selected.has(m.id)?'on':'')+'" data-storyboard-pick="'+esc(m.id)+'">'+(m.kind==='audio'?'<i class="audioReferenceIcon">♫</i>':'<img src="'+esc(mediaUrl(m))+'" alt="">')+'<span>'+esc(m.name||(m.kind==='audio'?'Audio':'Image'))+'</span>'+(picker.selected.has(m.id)?'<b>✓</b>':'')+'</button>').join('')+'</div>';
    content.appendChild(group);
    $$('[data-storyboard-folder]',content).forEach(button=>button.addEventListener('click',()=>{picker.folder=button.dataset.storyboardFolder;picker.q='';$('#storyboardPickerSearch').value='';renderStoryboardReferencePicker();}));
    const back=$('[data-storyboard-folder-back]',content);if(back)back.addEventListener('click',()=>{picker.folder='';picker.q='';$('#storyboardPickerSearch').value='';renderStoryboardReferencePicker();});
  }
  $$('[data-storyboard-pick]',content).forEach(button=>button.addEventListener('click',()=>toggleStoryboardPickerItem(button.dataset.storyboardPick)));const used=storyboardPickerFixedCount()+picker.selected.size;$('#storyboardPickerBudget').textContent=picker.mode==='references'?'':used+' / 9 references';
}
function applyStoryboardReferencePicker(){
  const picker=storyboardPickerState;if(!picker)return;const {card,index,mode,selected}=picker;
  if(mode==='cast'){const mapping={};for(const character of state.characters){const ids=prioritizedCharacterRefs(character).filter(id=>selected.has(id));if(ids.length)mapping[character.id]=ids;}card.character_reference_ids=mapping;card.character_ids=Object.keys(mapping);}else if(mode==='skill')card.prompt_skill_id=Array.from(selected)[0]||null;else card.reference_media_ids=Array.from(selected);
  if(mode!=='skill')constrainStoryboardReferences(card,index);closeStoryboardReferencePicker();if(card._magia){renderMagiaContext();return;}renderStoryboard();scheduleStoryboardSave();toast(mode==='cast'?'Scene cast updated':mode==='skill'?(card.prompt_skill_id?'Skill added to Scene '+(index+1):'Skill removed from scene'):'Scene references updated','ok');
}
function captureStoryboardFields(){
  if(!storyboardDraft)return;
  const style=$('#storyboardStyle');if(style)storyboardDraft.style_profile={...(storyboardDraft.style_profile||{}),name:(storyboardDraft.style_profile||{}).name||'Storyboard project style',prompt:style.value,source:(storyboardDraft.style_profile||{}).source||'custom'};
  const value=id=>$(id)&&$(id).value;storyboardDraft.output={aspect:value('#storyboardAspect')||'1:1',resolution:value('#storyboardResolution')||'native',quality:value('#storyboardQuality')||'high',frames:+(storyboardDraft.output||{}).frames||56,steps:+value('#storyboardSteps')||30,seed:+value('#storyboardSeed')||42,audio_mode:value('#storyboardAudioMode')||'effects',audio_notes:value('#storyboardAudioNotes')||''};
  storyboardDraft.optimize_scenes=false;
}
function scheduleStoryboardSave(){captureStoryboardFields();clearTimeout(storyboardSaveTimer);storyboardSaveTimer=setTimeout(()=>api('/api/project',{method:'POST',body:{storyboard_draft:storyboardDraft}}).catch(()=>{}),300);}
function renderStoryboard(){
  const draft=ensureStoryboardDraft(),style=draft.style_profile||{};
  draft.use_project_style=true;$('#storyboardStyle').value=style.prompt||'';
  draft.optimize_scenes=false;const out=draft.output||{};$('#storyboardAspect').value=out.aspect||canvasAspect(state.canvas);$('#storyboardResolution').value=out.resolution||'native';$('#storyboardQuality').value=out.quality||'high';$('#storyboardSteps').value=out.steps||30;$('#storyboardSeed').value=out.seed??42;$('#storyboardAudioMode').value=out.audio_mode||'effects';$('#storyboardAudioNotes').value=out.audio_notes||'';
  const wrap=$('#storyboardScenes');wrap.innerHTML='';
  draft.scenes.forEach((card,index)=>{
    const el=div('storyboardScene');el.dataset.cardId=card.id;
    const refs=storyboardReferenceItems(card),continuity=index>0&&card.continue_previous!==false;
    const promptSkill=storyboardPromptSkill(card);
    const castChips=(card.character_ids||[]).map(cid=>{const character=state.characters.find(c=>c.id===cid),count=((card.character_reference_ids||{})[cid]||[]).length;return character&&count?'<button class="storyboardCastChip" data-edit-storyboard-cast><span>'+esc(character.name)+'</span><b>Cast · '+count+' ref'+(count===1?'':'s')+'</b></button>':'';}).join('');
    el.innerHTML='<div class="storyboardSceneHead"><span class="storyboardSceneNumber">'+(index+1)+'</span><input class="txt storyboardSceneName" value="'+esc(card.name||('Scene '+(index+1)))+'" aria-label="Scene name"><button class="storyboardSceneRemove" aria-label="Remove scene" '+(draft.scenes.length<=2?'disabled':'')+'>×</button></div><div class="storyboardSceneBody"><textarea class="txt storyboardScenePrompt" placeholder="Describe this scene in chronological H3 format…">'+esc(card.prompt||'')+'</textarea><div class="storyboardSceneContext">'+(promptSkill?'<button class="storyboardSkillChip" data-edit-storyboard-skill><span>✦ '+esc(promptSkill.name)+'</span><b aria-label="Remove skill">×</b></button>':'')+castChips+'<button class="storyboardContextAdd storyboardAddSkill">✦ Skills</button><button class="storyboardContextAdd storyboardAddCast">＋ Add cast</button><button class="storyboardContextAdd storyboardAddReferences">＋ Add references</button>'+(index>0?'<button class="storyboardCopyPrevious">Copy Scene '+index+' references</button>':'')+'</div><div class="storyboardCardLabel"><span>VISUAL REFERENCES</span></div><div class="storyboardRefs">'+(refs.length?refs.map((r,i)=>'<div class="storyboardRef" data-ref-index="'+i+'"><img src="'+esc(mediaUrl(r.media))+'" alt=""><span>'+esc(r.label)+'</span><button aria-label="Remove reference">×</button></div>').join(''):'<span class="storyboardNoRefs">No Cast or visual-reference images attached.</span>')+'</div><label class="storyboardContinuity"><input type="checkbox" '+(continuity?'checked':'')+' '+(index===0?'disabled':'')+'><span class="storyboardContinuityMark"></span><span><strong>'+(index===0?(card.source_media_id?'Include selected opening frame':'Start a new sequence'):'Include Scene '+index+' last frame')+'</strong><small>'+(index===0?(card.source_media_id?esc(card.source_name||'The selected frame is the opening authority.'):'This scene has no predecessor.'):'Optional. The last frame becomes the visual opening authority; attached Cast remains as identity notes because H3 cannot combine both conditioning modes.')+'</small></span></label><div class="storyboardCardActions"><label class="storyboardDuration">Duration<input class="txt" type="number" min="0.34" max="15" step="0.1" value="'+(((+(card.params||{}).frames||+out.frames||56)/24).toFixed(1))+'"><span>s</span></label><button class="btn ghost storyboardRefine">✦ Refine</button><span class="storyboardBudget">'+storyboardBudgetLabel(card,index)+'</span></div></div>';
    $('.storyboardCardLabel span',el).textContent='REFERENCES';const emptyRefs=$('.storyboardNoRefs',el);if(emptyRefs)emptyRefs.textContent='No Cast, visual, or audio references attached.';$$('.storyboardRef',el).forEach((node,i)=>{if((refs[i].media||{}).kind==='audio'){const img=$('img',node),icon=document.createElement('i');icon.className='audioReferenceIcon';icon.textContent='♫';img.replaceWith(icon);}});if(index>0&&refs.some(r=>(r.media||{}).kind==='audio')){$('.storyboardContinuity small',el).textContent='The previous final frame becomes Picture 1 in Ref2VA, followed by the selected visual and audio references.';}
    $('.storyboardSceneName',el).addEventListener('input',e=>{card.name=e.target.value;scheduleStoryboardSave();});$('.storyboardScenePrompt',el).addEventListener('input',e=>{card.prompt=e.target.value;updateStoryboardSummary();scheduleStoryboardSave();});$('.storyboardSceneRemove',el).addEventListener('click',()=>{if(draft.scenes.length<=2)return;draft.scenes.splice(index,1);draft.scenes.forEach((x,i)=>{if(!x.name||/^Scene \d+$/.test(x.name))x.name='Scene '+(i+1);if(i===0)x.continue_previous=false;});renderStoryboard();scheduleStoryboardSave();});
    $('.storyboardAddSkill',el).addEventListener('click',()=>openStoryboardReferencePicker(card,index,'skill'));const skillChip=$('[data-edit-storyboard-skill]',el);if(skillChip){skillChip.addEventListener('click',event=>{if(event.target.closest('b')){card.prompt_skill_id=null;renderStoryboard();scheduleStoryboardSave();}else openStoryboardReferencePicker(card,index,'skill');});}$('.storyboardAddCast',el).addEventListener('click',()=>openStoryboardReferencePicker(card,index,'cast'));$('.storyboardAddReferences',el).addEventListener('click',()=>openStoryboardReferencePicker(card,index,'references'));$$('[data-edit-storyboard-cast]',el).forEach(button=>button.addEventListener('click',()=>openStoryboardReferencePicker(card,index,'cast')));if(index>0)$('.storyboardCopyPrevious',el).addEventListener('click',()=>{const previous=draft.scenes[index-1];card.character_ids=[...(previous.character_ids||[])];card.character_reference_ids=JSON.parse(JSON.stringify(previous.character_reference_ids||{}));card.reference_media_ids=[...(previous.reference_media_ids||[])];card.prompt_skill_id=previous.prompt_skill_id||card.prompt_skill_id||null;const kept=constrainStoryboardReferences(card,index);renderStoryboard();scheduleStoryboardSave();toast(kept+' reference'+(kept===1?'':'s')+' and scene skill copied from Scene '+index,'ok');});$('.storyboardContinuity input',el).addEventListener('change',e=>{card.continue_previous=e.target.checked;const before=storyboardReferenceItems(card).length,kept=constrainStoryboardReferences(card,index);renderStoryboard();scheduleStoryboardSave();if(kept<before)toast('One reference was removed to reserve the last-frame continuity slot','ok');});
    $$('.storyboardRef',el).forEach(node=>node.querySelector('button').addEventListener('click',()=>{const item=refs[+node.dataset.refIndex];if(item.type==='reference')card.reference_media_ids=(card.reference_media_ids||[]).filter(id=>id!==item.media.id);else{card.character_reference_ids[item.characterId]=(card.character_reference_ids[item.characterId]||[]).filter(id=>id!==item.media.id);if(!card.character_reference_ids[item.characterId].length)card.character_ids=(card.character_ids||[]).filter(id=>id!==item.characterId);}renderStoryboard();scheduleStoryboardSave();}));
    const durationInput=$('.storyboardDuration input',el),effectiveFrames=+(card.params||{}).frames||+out.frames||56;durationInput.type='text';durationInput.inputMode='decimal';durationInput.removeAttribute('min');durationInput.removeAttribute('max');durationInput.removeAttribute('step');$('.storyboardDuration span',el).textContent='seconds';const framesLabel=document.createElement('label');framesLabel.className='storyboardDuration storyboardFrames';framesLabel.innerHTML='Frames<input class="txt" type="text" inputmode="numeric" value="'+effectiveFrames+'"><span>frames</span>';const framesInput=$('input',framesLabel);$('.storyboardCardActions',el).insertBefore(framesLabel,$('.storyboardRefine',el));const timingChanged=()=>{draft.optimized_fingerprint=null;draft.continuity_review=null;scheduleStoryboardSave();};durationInput.addEventListener('input',event=>{const raw=parseFloat(event.target.value);if(!Number.isFinite(raw))return;const frames=clamp(Math.round(clamp(raw,.34,15)*24),8,360);card.params={...(card.params||{}),frames};framesInput.value=frames;timingChanged();});durationInput.addEventListener('change',event=>{event.target.value=(((card.params||{}).frames||effectiveFrames)/24).toFixed(1);});framesInput.addEventListener('input',event=>{const raw=parseInt(event.target.value,10);if(!Number.isFinite(raw))return;const frames=clamp(raw,8,360);card.params={...(card.params||{}),frames};durationInput.value=(frames/24).toFixed(1);timingChanged();});framesInput.addEventListener('change',event=>{event.target.value=String((card.params||{}).frames||effectiveFrames);});$('.storyboardRefine',el).addEventListener('click',()=>refineStoryboardCard(card,el));wrap.appendChild(el);
  });updateStoryboardSummary();
}
function updateStoryboardSummary(){if(!storyboardDraft)return;const label=$('#storyboardGenerate span');if(label)label.textContent='Generate '+storyboardDraft.scenes.length+' scene'+(storyboardDraft.scenes.length===1?'':'s');}
function openStoryboard(){ensureStoryboardDraft();renderStoryboard();$('#storyboardWorkspace').classList.add('on');$('#storyboardWorkspace').setAttribute('aria-hidden','false');document.body.classList.add('storyboardOpen');$('.storyboardBody').scrollTop=0;$('#storyboardClose').focus({preventScroll:true});}
function closeStoryboard(){scheduleStoryboardSave();$('#storyboardWorkspace').classList.remove('on');$('#storyboardWorkspace').setAttribute('aria-hidden','true');document.body.classList.remove('storyboardOpen');}
function renderMagiaContext(){if(!magiaCard)return;const wrap=$('#magiaContext'),skill=storyboardPromptSkill(magiaCard),parts=[];for(const id of magiaCard.character_ids||[]){const character=state.characters.find(item=>item.id===id);if(character)parts.push('<span>'+esc(character.name)+'</span>');}for(const id of magiaCard.reference_media_ids||[]){const media=mediaById(id);if(media)parts.push('<span>'+esc(media.name)+'</span>');}if(skill)parts.push('<span>✦ '+esc(skill.name)+'</span>');wrap.innerHTML=parts.join('')||'<small>No context added</small>';updateMagiaPlan();}
function updateMagiaPlan(){const seconds=Math.max(1,parseFloat($('#magiaDuration')&&$('#magiaDuration').value)||30),block=$('#magiaOptimize')&&$('#magiaOptimize').checked?5:15,count=Math.ceil(seconds/block),plan=$('#magiaPlan');if(plan)plan.textContent=count<2?'Increase duration to create at least 2 scenes':count>24?'Maximum 24 scenes':count+' scenes · '+block+' seconds each'+(seconds%block?' · shorter final scene':'')+' · up to 24';}
function openMagia(){const refs=storyboardCurrentReferences(),memory=+engine.memory_gb||0;magiaCard={...storyboardNewCard(0),...refs,_magia:true,source_media_id:null,source_name:'',continue_previous:false};$('#magiaIdea').value='';$('#magiaDuration').value='30';$('#magiaOptimize').checked=!memory||memory<=128;$('#magiaMemoryHint').textContent=memory?(memory+' GB detected · '+(memory<=128?'Recommended for this computer.':'Optional for shorter, lower-memory scenes.')):'Best for computers with 128 GB RAM or less.';renderMagiaContext();const sheet=$('#magiaSheet');sheet.classList.add('on');sheet.setAttribute('aria-hidden','false');$('#magiaIdea').focus({preventScroll:true});}
function closeMagia(){const sheet=$('#magiaSheet');sheet.classList.remove('on');sheet.setAttribute('aria-hidden','true');magiaCard=null;}
function localMagiaStoryboard(idea,seconds,optimize){
  const block=optimize?5:15,count=Math.ceil(seconds/block),base=Date.now();
  return Array.from({length:count},(_,index)=>{
    const duration=Math.min(block,seconds-index*block),card=storyboardNewCard(index),progress=index/Math.max(1,count-1),beat=index===0?'Establish the protagonist, setting, style, and initial emotional situation through a specific opening action.':index===count-1?'Complete the promised outcome and emotional resolution, then finish on a satisfying final image; do not end mid-action.':progress<.5?'Develop the discovery or pursuit through a new causal action that advances the story.':progress<.8?'Deliver the central encounter and emotional turn through visible behavior and reaction.':'Show the consequences of the emotional turn and prepare the final resolution.';
    return {...card,id:'magia-'+base+'-'+index,name:'Scene '+(index+1),prompt:'Scene '+(index+1)+' of '+count+', '+duration.toFixed(2)+' seconds. '+(index?'Continue from the exact final frame of the previous scene. ':'Begin the story. ')+beat+' Preserve this complete story intent without showing later beats early: '+idea,original_prompt:idea,continue_previous:index>0,params:{...(card.params||{}),frames:Math.max(8,Math.round(duration*24))},duration_seconds:duration,character_ids:[...(magiaCard.character_ids||[])],character_reference_ids:{...(magiaCard.character_reference_ids||{})},reference_media_ids:[...(magiaCard.reference_media_ids||[])],prompt_skill_id:magiaCard.prompt_skill_id||null};
  });
}
async function createMagiaStoryboard(){const idea=$('#magiaIdea').value.trim(),seconds=parseFloat($('#magiaDuration').value),optimize=$('#magiaOptimize').checked;if(!idea){toast('Describe the idea first','err');$('#magiaIdea').focus();return;}if(!Number.isFinite(seconds)||seconds<1){toast('Enter a duration of at least one second','err');return;}const count=Math.ceil(seconds/(optimize?5:15));if(count>24){toast('Magia supports up to 24 scenes for this scene length','err');return;}const button=$('#magiaCreate'),draft=ensureStoryboardDraft(),skill=storyboardPromptSkill(magiaCard);button.disabled=true;button.querySelector('span').textContent='Building storyboard…';try{let result;try{result=await api('/api/storyboards/magia',{method:'POST',body:{idea,duration_seconds:seconds,optimize_five_seconds:optimize,use_ai:true,style:(draft.style_profile||{}).prompt||'',skill_direction:promptSkillInstruction(skill),context:{character_ids:magiaCard.character_ids||[],character_reference_ids:magiaCard.character_reference_ids||{},reference_media_ids:magiaCard.reference_media_ids||[],prompt_skill_id:magiaCard.prompt_skill_id||null}}});}catch(error){if(error.status!==404)throw error;result={scenes:localMagiaStoryboard(idea,seconds,optimize),used_ai:false,compatibility_fallback:true};}draft.scenes=result.scenes;if(result.project_style){draft.style_profile={...(draft.style_profile||{}),name:'Magia style',prompt:result.project_style,skill_id:null,source:'magia'};draft.use_project_style=true;}draft.optimize_scenes=false;draft.continuity_review=null;draft.optimized_fingerprint=null;closeMagia();renderStoryboard();scheduleStoryboardSave();$('.storyboardBody').scrollTop=0;toast(result.compatibility_fallback?'Storyboard created. Restart OpenMagia before AI refinement.':(result.used_ai?'Magia prepared ':'Created ')+result.scenes.length+' scene'+(result.scenes.length===1?'':'s'),result.compatibility_fallback?'warn':'ok');}catch(error){toast(error.message,'err');}finally{button.disabled=false;button.querySelector('span').textContent='Create storyboard';}}
function storyboardIdentityNotes(card){
  const notes=(card.character_ids||[]).map(id=>state.characters.find(c=>c.id===id)).filter(Boolean).map(c=>c.name+': '+String(c.description||c.identity_notes||'Preserve this character’s established identity and anatomy.').trim());
  return notes.length?' Cast identity notes (text only; these are not Picture references): '+notes.join(' '):'';
}
function selectedSceneIdentityNotes(){
  const notes=Array.from(selChars).map(id=>state.characters.find(c=>c.id===id)).filter(Boolean).map(c=>c.name+': '+String(c.description||c.identity_notes||'Preserve this character’s established identity and anatomy.').trim());
  return notes.length?' Cast identity notes (text only; these are not Picture references): '+notes.join(' '):'';
}
function singleSceneFormatContext(){
  const continuation=!!sourceSelection;
  const visualIds=Array.from(selRefs);
  const characterIds=Array.from(selChars);
  const hasReferences=visualIds.length||characterIds.length;
  const continuity=continuation?('Picture 1 is only the selected opening frame and is the exact frame-zero authority. Continue without a visual reset; preserve composition, anatomy, environment, camera axis, pose, motion direction, lighting, visible text, passenger count, seating, props, vehicle state, and spatial relationships; introduce no unlisted element. '+sourceContinuityContext(sourceSelection.mediaId)+selectedSceneIdentityNotes()):'';
  return {continuation,continuity_reference:false,mode:continuation?'i2va':(hasReferences?'ref2va':'t2va'),character_ids:continuation?[]:characterIds,character_reference_ids:continuation?{}:characterReferencePayload(),reference_media_ids:continuation?[]:visualIds,continuity};
}
function storyboardFormatContext(card,index){
  const continuation=!!(card.source_media_id||(index>0&&card.continue_previous!==false));
  const hasAudio=(card.reference_media_ids||[]).some(id=>(mediaById(id)||{}).kind==='audio');
  const continuityMode=continuation?(hasAudio?'reference':String(card.continuity_mode||'frame')):'none',hybrid=continuityMode==='reference';
  const hasLooseReferences=!!((card.character_ids||[]).length||(card.reference_media_ids||[]).length||Object.values(card.character_reference_ids||{}).some(ids=>(ids||[]).length));
  const continuity=continuation?(hybrid?'The previous scene final frame is ordered Picture 1 and is the highest authority for opening state. Cast pictures follow it as identity authorities. Continue the established passenger count, seating, props, vehicle, camera axis, motion direction, lighting, HUD, and spatial relationships; introduce no unlisted element.':'Picture 1 is only the previous scene final frame and is the exact opening authority. Continue without a visual reset; preserve composition, anatomy, environment, camera axis, pose, motion direction, lighting, HUD, passenger count, seating, props, vehicle state, and spatial relationships; introduce no unlisted element.'+storyboardIdentityNotes(card)):'';
  return {continuation,continuity_reference:hybrid,mode:continuation?(hybrid?'ref2va':'i2va'):(hasLooseReferences?'ref2va':'t2va'),character_ids:continuation&&!hybrid?[]:(card.character_ids||[]),character_reference_ids:continuation&&!hybrid?{}:(card.character_reference_ids||{}),reference_media_ids:continuation&&!hybrid?[]:(card.reference_media_ids||[]),continuity};
}
function storyboardCreativeBody(value){
  const text=String(value||'').trim();
  if(!isStructuredH3Prompt(text))return text;
  const marker=text.lastIndexOf('detailed_description:')>=0?'detailed_description:':'integrated_multimodal_description:';
  const start=text.lastIndexOf(marker);if(start<0)return text;
  let body=text.slice(start+marker.length);const stops=['overall_soundscape:','non_diegetic_music:'].map(field=>body.indexOf(field)).filter(index=>index>=0);
  if(stops.length)body=body.slice(0,Math.min(...stops));
  return body.trim();
}
async function refineStoryboardCard(card,el){
  const prompt=String(card.prompt||'').trim();if(!prompt){toast('Write this scene prompt first','err');return;}const button=$('.storyboardRefine',el),draft=ensureStoryboardDraft(),index=storyboardDraft.scenes.indexOf(card);button.disabled=true;button.textContent='Opening…';
  try{storyboardRefineTarget={card,index,skill:await storyboardSkillWithSpecification(card),draft};openPromptSheet('storyboard');}catch(error){storyboardRefineTarget=null;toast(error.message,'err');}finally{button.disabled=false;button.textContent='✦ Refine';}
}
function continuityReviewFingerprint(draft){return JSON.stringify({style:(draft.style_profile||{}).prompt||'',scenes:(draft.scenes||[]).map(s=>({prompt:s.prompt||'',frames:+(s.params||{}).frames||+(draft.output||{}).frames||56,cast:s.character_ids||[],refs:s.character_reference_ids||{},media:s.reference_media_ids||[],continue:s.continue_previous!==false,mode:s.continuity_mode||'frame'}))});}
function closeContinuityReview(){continuityAuditState=null;const sheet=$('#continuityReviewSheet');sheet.classList.remove('on');sheet.setAttribute('aria-hidden','true');}
function renderContinuityReview(result,draft,fingerprint){
  continuityAuditState={result,draft,fingerprint};const issues=result.issues||[],wrap=$('#continuityReviewIssues');wrap.innerHTML='';
  $('#continuityReviewStatus').textContent=issues.length?'Fix or confirm '+issues.length+' transition'+(issues.length===1?'':'s')+' before generating.':'No continuity conflicts found.';
  if(!issues.length)wrap.innerHTML='<div class="continuityIssue continuityClear"><h3>Ready to generate</h3><p>If this scene continues a rendered clip, confirm its last frame matches the intended cast, props, placement, and direction.</p></div>';
  issues.forEach((issue,i)=>{const scene=draft.scenes[issue.scene_index],el=div('continuityIssue');el.innerHTML='<h3>'+esc((scene&&scene.name)||('Scene '+(issue.scene_index+1)))+' · '+esc(issue.title)+'</h3><p>'+esc(issue.detail)+'</p><label>How to fix<select class="txt" data-continuity-resolution="'+i+'"><option value="">Choose a fix…</option><option value="established">Keep — it is visible in the previous frame</option><option value="introduced">Keep — this scene shows how it appears</option><option value="edit">Edit the scene prompt or references</option></select></label>';wrap.appendChild(el);});
  const sheet=$('#continuityReviewSheet');sheet.classList.add('on');sheet.setAttribute('aria-hidden','false');$('#continuityReviewConfirm').textContent=issues.length?'Confirm and generate':'Generate storyboard';$('#continuityReviewConfirm').focus({preventScroll:true});
}
async function requestContinuityReview(draft){
  if(continuityAuditPending)return;continuityAuditPending=true;const button=$('#storyboardGenerate'),original=button.querySelector('span').textContent;button.querySelector('span').textContent='Checking continuity…';let generateAfterAudit=false;
  try{const fingerprint=continuityReviewFingerprint(draft);const result=await api('/api/continuity/audit',{method:'POST',body:{style_profile:draft.style_profile,scenes:draft.scenes,use_ai:true}});if((result.issues||[]).length){renderContinuityReview(result,draft,fingerprint);}else{draft.continuity_review={confirmed_at:new Date().toISOString(),resolutions:[],fingerprint};scheduleStoryboardSave();generateAfterAudit=true;}}catch(error){toast('Continuity review could not run: '+error.message,'err');}finally{continuityAuditPending=false;button.querySelector('span').textContent=original;}
  if(generateAfterAudit)await generateStoryboard();
}
function confirmContinuityReview(){
  const audit=continuityAuditState;if(!audit)return;const issues=audit.result.issues||[],resolutions=[];
  for(let i=0;i<issues.length;i++){const select=$('[data-continuity-resolution="'+i+'"]');if(!select||!select.value){toast('Resolve every continuity item before generating','err');return;}if(select.value==='edit'){closeContinuityReview();toast('Update the scene, then run the continuity review again','ok');return;}resolutions.push({scene_index:issues[i].scene_index,fact:issues[i].fact||issues[i].title,resolution:select.value});}
  for(const item of resolutions){const card=audit.draft.scenes[item.scene_index],prior=Array.isArray((card.guide_answers||{}).continuity_review)?card.guide_answers.continuity_review:[];const statement=(item.resolution==='established'?'Confirmed visible in the approved previous frame: ':'This scene must visibly introduce before it is treated as persistent: ')+item.fact;card.guide_answers={...(card.guide_answers||{}),continuity_review:[...prior,statement]};if(isStructuredH3Prompt(card.prompt)&&card.prompt.includes('detailed_description:')&&!card.prompt.includes(statement))card.prompt=card.prompt.replace('detailed_description:','detailed_description: Continuity ledger — '+statement+'. ');}
  audit.draft.continuity_review={confirmed_at:new Date().toISOString(),resolutions};audit.draft.continuity_review.fingerprint=continuityReviewFingerprint(audit.draft);scheduleStoryboardSave();closeContinuityReview();generateStoryboard();
}
async function generateStoryboard(){
  if(storyboardSubmitting||continuityAuditPending)return;captureStoryboardFields();const draft=ensureStoryboardDraft(),empty=draft.scenes.findIndex(s=>!String(s.prompt||'').trim());if(empty>=0){toast('Scene '+(empty+1)+' needs a prompt','err');return;}const fingerprint=continuityReviewFingerprint(draft);if(!draft.continuity_review||draft.continuity_review.fingerprint!==fingerprint){await requestContinuityReview(draft);return;}const out=draft.output||{},aspect=out.aspect||'1:1',native=out.resolution==='native',landscape=aspect==='16:9',portrait=aspect==='9:16',dims=native?(landscape?[1344,768]:portrait?[768,1344]:[768,768]):(landscape?[896,512]:portrait?[512,896]:[512,512]),quality={balanced:{layers:45,reuse:2},high:{layers:50,reuse:1},reference:{layers:50,reuse:1}}[out.quality]||{layers:45,reuse:2};
  if(native&&(+out.frames||56)>=240&&(out.quality==='high'||out.quality==='reference'))toast('Attempting the selected full '+(out.quality==='reference'?'Reference':'High')+' schedule. Long native renders may take substantially longer; OpenMagia will use one clearly reported stable retry only if the process stalls.','ok');
  storyboardSubmitting=true;$('#storyboardWorkspace').classList.add('storyboardSubmitting');const button=$('#storyboardGenerate');button.querySelector('span').textContent='Queuing storyboard…';
  try{for(let index=0;index<draft.scenes.length;index++){const card=draft.scenes[index],cardFrames=clamp(+(card.params||{}).frames||+out.frames||56,8,360),context=storyboardFormatContext(card,index),structured=isStructuredH3Prompt(card.prompt),migrateContinuation=context.continuation&&structured,skill=await storyboardSkillWithSpecification(card);if(!migrateContinuation&&!skill)continue;button.querySelector('span').textContent=(migrateContinuation?'Preparing continuity and skill contract for':'Applying '+skill.name+' to')+' Scene '+(index+1)+'…';const originalPrompt=card.original_prompt||card.prompt;const refined=await api('/api/prompt/format',{method:'POST',body:{idea:migrateContinuation?storyboardCreativeBody(card.prompt):card.prompt,style:(draft.style_profile||{}).prompt||'',frames:cardFrames,character_ids:context.character_ids,character_reference_ids:context.character_reference_ids,reference_media_ids:context.reference_media_ids,continuity_reference:context.continuity_reference,mode:context.mode,task:'scene',use_ai:!structured,prompt_skill_id:skill&&skill.id,answers:{skill_instruction:promptSkillInstruction(skill),continuity:context.continuity}}});card.original_prompt=originalPrompt;card.refined_prompt=refined.expanded_idea;card.skill_compilation=refined.skill_compilation;card.prompt=refined.prompt;card.guide_answers={...(card.guide_answers||{}),prompt_skill_id:skill&&skill.id,skill_instruction:promptSkillInstruction(skill),continuity:context.continuity};}
    const payload={id:draft.id,style_profile:draft.style_profile,use_project_style:true,output:{width:dims[0],height:dims[1],frames:clamp(+out.frames||56,8,360),steps:clamp(+out.steps||30,1,60),seed:+out.seed||42,quality:out.quality||'high',layers:quality.layers,reuse:quality.reuse,audio_mode:out.audio_mode||'effects',audio_notes:out.audio_notes||''},scenes:draft.scenes};
    button.querySelector('span').textContent='Queuing storyboard…';const result=await api('/api/storyboards/generate',{method:'POST',body:payload});storyboardDraft=null;closeStoryboard();$('#genType').value='video';applyGenerationType();toast(result.scenes.length+' scenes queued in order','ok');await refresh(true);}catch(error){toast(error.message,'err');}finally{storyboardSubmitting=false;$('#storyboardWorkspace').classList.remove('storyboardSubmitting');if(storyboardDraft)updateStoryboardSummary();}
}

async function createOrUpdateContinuityStyle(mediaId,button){
  const original=button&&button.textContent;if(button){button.disabled=true;button.textContent='Building continuity style…';}
  try{const out=await api('/api/continuity/style',{method:'POST',body:{media_id:mediaId,use_ai:true}});state.style_profile={name:out.profile.name,prompt:out.profile.prompt,skill_id:out.profile.skill_id,source:out.profile.source};state.style_enabled=true;state.base_prompt=out.profile.prompt;state.project_style_skills=[...(state.project_style_skills||[]).filter(x=>x.id!==out.profile.id),out.profile];renderGenerate();toast((out.used_ai?'Refined':'Built')+' project continuity style','ok');return true;}
  catch(error){toast('Could not update project style: '+error.message,'err');return false;}
  finally{if(button){button.disabled=false;button.textContent=original;}}
}
function renderGenerate() {
  applyGenerationType();
  // Style (persistent across the whole film) + Continue-from-previous toggle
  const styleEl = $('#genStyle');
  const profile = state.style_profile || { name: state.base_prompt ? 'Custom project style' : 'No project style', prompt: state.base_prompt || '' };
  if (profile.skill_id && (!selectedTemplate || selectedTemplate.id !== profile.skill_id)) selectedTemplate = promptTemplates.find(t => t.id === profile.skill_id) || null;
  if (styleEl && document.activeElement !== styleEl) styleEl.value = profile.prompt || '';
  const hasStyle=!!String(profile.prompt||'').trim(),styleEnabled=hasStyle&&state.style_enabled!==false,toggle=$('#projectStyleToggle');
  toggle.hidden=!hasStyle;toggle.disabled=!hasStyle;toggle.classList.toggle('on',styleEnabled);toggle.setAttribute('aria-checked',styleEnabled?'true':'false');toggle.title=hasStyle?(styleEnabled?'Shared instructions are applied to Refine and generation':'Shared instructions are saved but ignored by Refine and generation'):'Add shared instructions first';
  $('.projectStyleCard').classList.toggle('styleOff',hasStyle&&!styleEnabled);

  // Cast is opt-in per scene; selected members appear as removable context.
  if (!selCharsInitialized) selCharsInitialized = true;
  const wrap = $('#genChars'); wrap.innerHTML = '';
  for (const c of state.characters.filter(c => selChars.has(c.id))) {
    const allocated=allocatedCharacterReferenceCounts().get(c.id)||0;
    const b = document.createElement('button'); b.textContent = c.name + ' · Cast · '+allocated+' ref'+(allocated===1?'':'s');
    if (selChars.has(c.id)) b.classList.add('on');
    b.title = 'Edit the character references used in this scene';
    b.setAttribute('aria-pressed', selChars.has(c.id) ? 'true' : 'false');
    b.addEventListener('click', () => { openComposerPicker('cast'); selectedPickerItem=c; renderPickerDetail(); });
    wrap.appendChild(b);
  }
  const refWrap=$('#genRefs');refWrap.innerHTML='';
  for(const m of state.media.filter(m=>selRefs.has(m.id))){const chip=document.createElement('button');chip.className='referenceChip';chip.innerHTML=(m.kind==='audio'?'<i class="audioReferenceIcon">♫</i>':'<img src="'+esc(mediaUrl(m))+'" alt="">')+'<span>'+esc(m.name)+'</span><b>×</b>';chip.title='Remove '+(m.kind==='audio'?'audio':'visual')+' reference';chip.addEventListener('click',()=>{selRefs.delete(m.id);renderGenerate();});refWrap.appendChild(chip);}
  renderActivePromptSkill();
  renderSourceContext();
  const anchored=!!sourceSelection;
  $('#referencePickerBtn').hidden=false;
  $('#castPickerBtn').title=anchored?'Cast images are sent with the continuity frame through Ref2VA':'';
  const refs = selectedReferenceCount();
  $('#composerRefs').textContent = refs + ' / 9 references';
  $('#composerDuration').textContent = ((+$('#genFrames').value || 56) / 24).toFixed(1) + 's';
  $('#composerMode').textContent = anchored ? 'Ref2VA continuity' : (refs && engine && engine.ref2va ? 'Ref2VA' : 'T2VA');
  // This select is another view of the project canvas setting, not an
  // independent draft field. Keep it synchronized even while it has focus.
  const aspect=$('#genAspect');if(aspect)aspect.value=canvasAspect(state.canvas);
}

function applyGenerationType() {
  const select=$('#genType'),controls=$('#videoGenerationControls'),notice=$('#generationTypeNotice'),button=$('#genBtn');
  if(!select||!controls||!notice||!button)return;
  const type=select.value||'video';
  if(!select.value)select.value='video';
  const isVideo=type==='video',isImage=type==='image',isStoryboard=type==='storyboard';controls.hidden=!(isVideo||isImage);notice.hidden=isVideo||isImage;button.disabled=!(isVideo||isImage)||generationSubmitting;
  $('#generate').classList.toggle('imageMode',isImage);
  button.querySelector('span').textContent=generationSubmitting?'Generating…':(isImage?'Generate image':isVideo?'Generate scene':'Generation unavailable');
  $('#genPrompt').placeholder=isImage?'Describe one finished image: subject, composition, environment, lighting, materials, and exact text…':'Describe the scene, action, camera, dialogue or visible text…';
  if(isImage){notice.innerHTML='';sourceSelection=null;renderSourceContext();}
  else if(isStoryboard){notice.innerHTML='<strong>Build a continuous multi-scene sequence</strong><span>Use one project style and output setup across horizontally arranged scene prompts. Previous-frame continuity is enabled by default.</span><button id="openStoryboardBtn" class="btn primary" type="button">Open storyboard full screen</button>';$('#openStoryboardBtn').addEventListener('click',openStoryboard);}
  else if(!isVideo){notice.innerHTML='<strong>Music generation is coming soon</strong><span>Select Video, Storyboard, or Image to use the currently available generation controls.</span>';}
}

function canvasAspect(canvas){return canvas.width===canvas.height?'1:1':canvas.width<canvas.height?'9:16':'16:9';}
function aspectCanvas(aspect){return aspect==='9:16'?{width:768,height:1344}:aspect==='16:9'?{width:1344,height:768}:{width:768,height:768};}
async function setProjectAspect(aspect){const canvas=aspectCanvas(aspect);state.canvas=canvas;const select=$('#genAspect');if(select)select.value=aspect;renderHeader();renderGenerate();drawNow();try{await api('/api/project',{method:'POST',body:{canvas}});await refresh(true);}catch(error){toast(error.message,'err');}}

function inferPromptSeconds(value) {
  const text = String(value || '');
  const cuts = [...text.matchAll(/CUT\s+\d+\s*\|\s*\d+(?:\.\d+)?\s*-\s*(\d+(?:\.\d+)?)s/gi)];
  if (cuts.length) return +cuts[cuts.length - 1][1];
  const m = text.match(/(?:create\s+(?:a\s+)?)?(\d+(?:\.\d+)?)\s*(?:-?second|seconds|secs|s)\b/i);
  return m ? +m[1] : null;
}

function applyDurationSeconds(seconds) {
  if (!(seconds > 0)) return;
  const frames = clamp(Math.round(Math.min(15, seconds) * 24), 8, 360);
  $('#genFrames').value = frames;
  updateSecsHint(); renderGenerate();
}
function renderSourceContext() {
  const b = $('#sourcePickerBtn'); if (!b) return;
  if (!sourceSelection) { b.textContent = '＋ Continue frame from media'; b.classList.remove('on'); $('#sourceClearBtn').classList.remove('on'); b.title = 'Choose a frame from a scene, video, or image'; const refs = selectedReferenceCount(); $('#composerMode').textContent = refs && engine && engine.ref2va ? 'Ref2VA' : 'T2VA'; return; }
  b.textContent = sourceSelection.name + ' · ' + (sourceSelection.frame === 'first' ? 'First frame' : sourceSelection.frame === 'last' ? 'Last frame' : 'Image');
  b.classList.add('on'); $('#sourceClearBtn').classList.add('on'); b.title = 'Change opening reference';
  $('#composerMode').textContent = 'Ref2VA continuity';
  $('#composerRefs').textContent = selectedReferenceCount()+' / 9 references';
}

function extractSceneFrame(s, which) {
  const mid = s.mediaId;
  if (!mid) { toast('No video yet', 'err'); return; }
  api('/api/frame', { method: 'POST', body: { mediaId: mid, at: which } })
    .then(m => { toast('Extracted ' + which + ' frame', 'ok'); refresh(); })
    .catch(e => toast(e.message, 'err'));
}

function renameScene(s) {
  const name = prompt('Rename scene', s.name);
  if (name == null) return;
  const nm = name.trim();
  if (!nm || nm === s.name) return;
  api('/api/scenes/' + s.id, { method: 'PUT', body: { name: nm } }).then(refresh)
    .catch(e => toast(e.message, 'err'));
}

async function generate() {
  if (generationSubmitting) return;
  const generationType=$('#genType').value==='image'?'image':'video';
  const prompt = $('#genPrompt').value.trim();
  if (!prompt) { toast('Write a prompt first', 'err'); return; }
  recoverStructuredPromptCast(prompt);
  generationSubmitting = true;
  const genButton = $('#genBtn');
  genButton.disabled = true;
  genButton.setAttribute('aria-busy', 'true');
  const selectedAspect=$('#genAspect').value||canvasAspect(state.canvas);
  const landscape = selectedAspect==='16:9';
  const portrait = selectedAspect==='9:16';
  const native = $('#genResolution').value === 'native';
  const dimensions = native
    ? (landscape ? [1344, 768] : portrait ? [768, 1344] : [768, 768])
    : (landscape ? [896, 512] : portrait ? [512, 896] : [512, 512]);
  const quality = {
    balanced: { steps: 20, layers: 45, reuse: 2 },
    high: { steps: 30, layers: 50, reuse: 1 },
    reference: { steps: 50, layers: 50, reuse: 1 }
  }[$('#genQuality').value] || { steps: 30, layers: 50, reuse: 1 };
  const params = { width: dimensions[0], height: dimensions[1],
    frames: generationType==='image'?5:+$('#genFrames').value, steps: clamp(+$('#genSteps').value || quality.steps, 1, 60), layers: quality.layers,
    reuse: quality.reuse, seed: +$('#genSeed').value || 42,
    quality: $('#genQuality').value, audio_mode: $('#genAudioMode').value };
  // persist the style, then create + run the scene. character_ids is sent
  // explicitly (the chips) so what you see attached is what gets sent.
  const chain = false;
  const currentProfile = state.style_profile || {};
  const styleProfile = { name: currentProfile.name || 'Custom project style', prompt: $('#genStyle').value.trim(), skill_id: currentProfile.skill_id || null, source: currentProfile.source || 'custom' };
  const useProjectStyle=!!styleProfile.prompt&&state.style_enabled!==false;
  const activeStylePrompt=useProjectStyle?styleProfile.prompt:'';
  const audioNotes = $('#genAudioNotes').value.trim();
  const audioDirections = {
    effects: { sound: audioNotes || 'clean synchronized physical sound effects and natural ambience with clear dynamics; no distorted or garbled voices', music: 'no non-diegetic music' },
    full: { sound: audioNotes || 'clean synchronized physical sound effects and natural ambience with clear dynamics', music: 'a restrained coherent score mixed beneath the physical sound effects' },
    dialogue: { sound: audioNotes || 'clear intelligible foreground dialogue with natural synchronized ambience and no garbled speech', music: 'no music, or very low music beneath dialogue' },
    silent: { sound: 'silence', music: 'none' }
  };
  const audioGuide = audioDirections[$('#genAudioMode').value] || audioDirections.effects;
  const skillAnswers = { ...guideAnswers, ...audioGuide, prompt_skill_id:activePromptSkill&&activePromptSkill.id, skill_instruction: promptSkillInstruction(activePromptSkill) };
  let submissionAccepted = false;
  try {
    const referenceError=referenceSelectionError();
    if(referenceError)throw new Error(referenceError);
    if(generationType==='image'&&selectedReferenceKinds().audio.length)throw new Error('Audio references are supported for H3 video generation only. Remove the audio reference or switch to Video.');
    let generationPrompt = prompt;
    let refinementResult = null;
    if (generationType==='image' || sourceSelection || activePromptSkill) {
      const formatContext=singleSceneFormatContext();
      toast(generationType==='image'?'Composing the still-image prompt locally…':sourceSelection ? 'Refining frame continuity locally…' : 'Adapting the OpenMagia skill for H3 locally…', 'ok');
      const refined = await api('/api/prompt/format', { method:'POST', body:{ idea:prompt, style:activeStylePrompt, frames:params.frames,
        character_ids:formatContext.character_ids, character_reference_ids:formatContext.character_reference_ids, reference_media_ids:formatContext.reference_media_ids,
        continuity_reference:formatContext.continuity_reference, mode:formatContext.mode, task:generationType==='image'?'image':'scene', use_ai:true,
        prompt_skill_id:activePromptSkill&&activePromptSkill.id, answers:{...skillAnswers, continuity:formatContext.continuity} } });
      generationPrompt = refined.prompt;
      refinementResult = refined;
    }
    await api('/api/project', { method: 'POST', body: { style_profile: styleProfile, canvas: { width: dimensions[0], height: dimensions[1] } } });
    const s = await api('/api/scenes', { method: 'POST', body: { prompt:generationPrompt, original_prompt:prompt, refined_prompt:refinementResult?refinementResult.expanded_idea:prompt, skill_compilation:refinementResult&&refinementResult.skill_compilation, generation_type:generationType, use_project_style:useProjectStyle, character_ids: Array.from(selChars), character_reference_ids:characterReferencePayload(), reference_media_ids:Array.from(selRefs), params, chain,
      template_id: selectedTemplate && selectedTemplate.id, prompt_skill_id: activePromptSkill && activePromptSkill.id, guide_answers: skillAnswers,
      source_media_id: sourceSelection && sourceSelection.mediaId, source_frame: sourceSelection && sourceSelection.frame } });
    await api('/api/scenes/' + s.id + '/generate', { method: 'POST' });
    submissionAccepted = true;
    toast(generationType==='image'?'Image queued':'Scene queued', 'ok');
    $('#genPrompt').value = '';
    // Cast remains attached for the next scene in the sequence. Remove or
    // edit it explicitly when the story changes characters.
    selRefs.clear();
    sourceSelection = null;
    activePromptSkill = null;
    guideAnswers = {};
    renderGenerate();
    await refresh();
  } catch(e) { toast(e.message, 'err'); }
  finally {
    // On success, release only after every per-generation field above has
    // been cleared and the refreshed UI reflects that reset. On failure the
    // user's inputs remain available for a deliberate retry.
    generationSubmitting = false;
    genButton.removeAttribute('aria-busy');
    applyGenerationType();
    if (submissionAccepted) genButton.focus({ preventScroll: true });
  }
}

/* ---------------- H3 prompt templates ---------------- */
function selectedReferenceCount() {
  if(!state)return 0;return Array.from(selRefs).filter(id=>mediaById(id)).length+(sourceSelection?1:0)+Array.from(allocatedCharacterReferenceCounts().values()).reduce((sum,count)=>sum+count,0);
}
function isStructuredH3Prompt(value) {
  const text=String(value||'').trim();
  const video=['detailed_description:','overall_soundscape:','non_diegetic_music:'].every(field=>text.includes(field));
  const image=text.includes('integrated_multimodal_description:');
  return (video||image)&&/^(subject_definitions:|integrated_multimodal_description:|For the target (video|image),|How the reference pictures align)/.test(text);
}
function openPromptSheet(mode = 'scene') {
  refineMode = mode; const styleMode = mode === 'style', storyboardMode=mode==='storyboard', target=storyboardRefineTarget;
  if(storyboardMode&&!target)return;
  if(!styleMode&&!storyboardMode&&isStructuredH3Prompt($('#genPrompt').value)){
    toast('This prompt already has valid H3 structure, so Refine left it unchanged.','ok');
    return;
  }
  const imageMode=!styleMode&&!storyboardMode&&$('#genType').value==='image';
  const skill=storyboardMode?target.skill:activePromptSkill;
  const sourceIdea=storyboardMode?storyboardCreativeBody(target.card.prompt):$('#genPrompt').value;
  const hasProjectStyle=!!String((state.style_profile||{}).prompt||'').trim();
  $('#sheetTitle').textContent = styleMode ? 'Refine shared instructions' : storyboardMode?'Refine storyboard scene':imageMode?'Refine H3 image':'Refine for MiniMax H3';
  $('#promptSheet .sheetHead p').textContent = styleMode ? 'Build reusable visual rules shared by every scene.' : imageMode?'Improve this image prompt with the context already attached.':'Improve this video prompt with the context already attached.';
  $('#promptSheet').classList.toggle('imageRefine',imageMode);
  $('#refineQuestionIntro').hidden=false;
  $('#refineQuestionTitle').textContent = styleMode ? 'Define the shared visual rules' : imageMode ? 'Compose the image before refinement' : (skill ? 'Complete the '+skill.name+' brief' : 'Direct the scene before refinement');
  $('#refineQuestionCopy').textContent = styleMode ? 'Answer only what matters. The model will fill safe visual defaults without inventing story events.' : imageMode ? 'Add subject, setting, camera, and visible text details—or skip and let the model make the strongest still-image decisions.' : (skill ? 'Answer the useful fields below. The local model will combine them with the complete skill and preserve your facts.' : 'Add timing, transitions, camera, text, and audio details—or skip and let the model make the strongest H3-safe decisions.');
  $('#sceneGuideFields').hidden = styleMode; $('#styleGuideFields').hidden = !styleMode;
  $('#guideIdea').value = sourceIdea; $('#styleIdea').value = $('#genStyle').value;
  // Start from this idea only. Empty optional fields are meaningful input to
  // the refiner and must not retain canned or previous-session answers.
  clearRefineOptionalFields();
  const priorAnswers=storyboardMode?(target.card.guide_answers||{}):guideAnswers;
  for(const [key,id] of Object.entries({setting:'guideSetting',camera:'guideCamera',pacing:'guidePacing',cuts:'guideCuts',transitions:'guideTransitions',text:'guideText',sound:'guideSound',music:'guideMusic'}))$('#'+id).value=String(priorAnswers[key]||'');
  const authoredSeconds=storyboardMode?((+(target.card.params||{}).frames||56)/24):(!styleMode&&!imageMode?inferPromptSeconds(sourceIdea):null);
  if(authoredSeconds&&!storyboardMode)applyDurationSeconds(authoredSeconds);
  $('#guideDuration').value = (authoredSeconds || ((+$('#genFrames').value || 56) / 24)).toFixed(2);
  $('#refineUpdateStyleOption').hidden=styleMode||storyboardMode||!hasProjectStyle;
  $('#refineUpdateStyle').checked=false;
  const context=storyboardMode?storyboardFormatContext(target.card,target.index):null;
  const storyboardRefs=context?((context.continuation?1:0)+context.reference_media_ids.length+Object.values(context.character_reference_ids||{}).reduce((sum,ids)=>sum+(ids||[]).length,0)):0;
  const mixedCount=storyboardMode?storyboardRefs:selectedReferenceCount();
  $('#refCount').textContent=mixedCount+' references · up to 9 images / 3 audio';
  $('#applyPrompt').textContent = styleMode?'Refine shared instructions':'Refine prompt';
  $('#promptSheet').classList.add('on'); $('#promptSheet').setAttribute('aria-hidden', 'false');
}
function clearRefineOptionalFields(){
  ['guideSetting','guideCamera','guidePacing','guideCuts','guideTransitions','guideText','guideSound','guideMusic',
   'styleMedium','stylePalette','styleCamera','styleGraphics','styleInvariants'].forEach(id=>{const field=$('#'+id);if(field)field.value='';});
}
function clearRefineSheet(){
  clearRefineOptionalFields();
  ['guideIdea','styleIdea','guideDuration'].forEach(id=>{const field=$('#'+id);if(field)field.value='';});
  $('#refineUpdateStyle').checked=false;
}
function closePromptSheet() { $('#promptSheet').classList.remove('on'); $('#promptSheet').setAttribute('aria-hidden', 'true'); clearRefineSheet(); if(refineMode==='storyboard')storyboardRefineTarget=null; }
function readGuideAnswers() {
  return { setting: $('#guideSetting').value.trim(), camera: $('#guideCamera').value.trim(), pacing: $('#guidePacing').value.trim(),
    cuts: $('#guideCuts').value.trim(), transitions: $('#guideTransitions').value.trim(),
    text: $('#guideText').value.trim(), sound: $('#guideSound').value.trim(), music: $('#guideMusic').value.trim() };
}
async function skipRefineQuestions() {
  const button=$('#skipQuestions');
  // Skip submits unanswered optional fields. The local model composes them
  // from the original vague prompt and attached context instead of exposing
  // generic answers in the form.
  clearRefineOptionalFields();
  button.disabled=true;
  button.textContent='Composing…';
  try { await applyPromptTemplate(); }
  finally { button.disabled=false; button.textContent='Skip · decide for me'; }
}
async function formatFromSheet(useAi) {
  const styleMode = refineMode === 'style', storyboardMode=refineMode==='storyboard', target=storyboardRefineTarget;
  if(storyboardMode&&!target)return null;
  const imageMode=!styleMode&&!storyboardMode&&$('#genType').value==='image';
  const idea = (styleMode ? $('#styleIdea') : $('#guideIdea')).value.trim();
  if (!idea) { toast('Describe what happens first', 'err'); return null; }
  const requestedSeconds=+$('#guideDuration').value;
  if(!styleMode&&!imageMode&&!storyboardMode&&requestedSeconds>0)applyDurationSeconds(requestedSeconds);
  const frames = imageMode ? 5 : storyboardMode?clamp(Math.round((requestedSeconds||((+(target.card.params||{}).frames||56)/24))*24),8,360):clamp(($('#genFrames').value | 0), 8, 360);
  const style = styleMode?$('#genStyle').value.trim():storyboardMode?((target.draft.use_project_style===false?'':(target.draft.style_profile||{}).prompt)||''):(state.style_enabled===false?'':$('#genStyle').value.trim());
  const skill=storyboardMode?target.skill:activePromptSkill;
  const context=storyboardMode?storyboardFormatContext(target.card,target.index):singleSceneFormatContext();
  const answers = styleMode ? { medium: $('#styleMedium').value.trim(), palette: $('#stylePalette').value.trim(), camera: $('#styleCamera').value.trim(), graphics: $('#styleGraphics').value.trim(), invariants: $('#styleInvariants').value.trim() }
    : { ...readGuideAnswers(), prompt_skill_id:skill&&skill.id, skill_instruction: promptSkillInstruction(skill), continuity:context.continuity };
  try {
    const out = await api('/api/prompt/format', { method: 'POST', body: { idea, style, frames, answers, task: styleMode ? 'style' : imageMode?'image':'scene',
      character_ids: context.character_ids, character_reference_ids:context.character_reference_ids, reference_media_ids:context.reference_media_ids,
      continuity_reference:context.continuity_reference, mode:context.mode, use_ai: useAi, prompt_skill_id:skill&&skill.id } });
    if(storyboardMode&&out.frames){target.card.params={...(target.card.params||{}),frames:out.frames};$('#guideDuration').value=(out.duration_seconds||out.frames/24).toFixed(2);}
    else if(!styleMode&&!imageMode&&out.frames){
      $('#genFrames').value=out.frames;
      $('#guideDuration').value=(out.duration_seconds||out.frames/24).toFixed(2);
      updateSecsHint();
    }
    return { out, style, answers, context, skill };
  } catch (e) { toast(e.message, 'err'); return null; }
}
async function applyPromptTemplate() {
  const result = await formatFromSheet(true); if (!result) return;
  const appliedMode=refineMode;
  if (refineMode === 'style') {
    const prompt = result.out.expanded_idea; const current = state.style_profile || {};
    const profile = { name: current.name && current.name !== 'No project style' ? current.name : 'Refined project style', prompt, skill_id: current.skill_id || null, source: 'locally refined' };
    await api('/api/project', { method: 'POST', body: { style_profile: profile } }); state.style_profile = profile; $('#genStyle').value = prompt; renderGenerate();
  } else if(refineMode==='storyboard'){
    const target=storyboardRefineTarget,card=target.card;
    card.original_prompt=card.original_prompt||$('#guideIdea').value.trim();card.refined_prompt=result.out.expanded_idea;card.prompt=result.out.prompt;
    card.skill_compilation=result.out.skill_compilation;card.prompt_skill_id=(result.skill&&result.skill.id)||card.prompt_skill_id||null;
    card.guide_answers={...(card.guide_answers||{}),...result.answers,continuity:result.context.continuity};
    target.draft.continuity_review=null;target.draft.optimized_fingerprint=null;renderStoryboard();scheduleStoryboardSave();
  } else {
    guideAnswers = result.answers; $('#genPrompt').value = result.out.prompt; const seconds = inferPromptSeconds(result.out.expanded_idea); if (seconds) applyDurationSeconds(seconds);
    if($('#refineUpdateStyle').checked){
      const styleResult=await api('/api/continuity/style',{method:'POST',body:{media_id:sourceSelection&&sourceSelection.mediaId,new_prompt:result.out.expanded_idea,new_answers:result.answers,use_ai:true}});
      state.style_profile={name:styleResult.profile.name,prompt:styleResult.profile.prompt,skill_id:styleResult.profile.skill_id,source:styleResult.profile.source};state.base_prompt=styleResult.profile.prompt;state.project_style_skills=[...(state.project_style_skills||[]).filter(x=>x.id!==styleResult.profile.id),styleResult.profile];$('#genStyle').value=styleResult.profile.prompt;renderGenerate();
    }
  }
  closePromptSheet(); toast((result.out.used_ai ? 'Locally refined ' : 'Structured ') + (appliedMode === 'style' ? 'shared instructions applied' : 'H3 prompt applied'), 'ok');
}
async function loadPromptTemplates() {
  try { const r = await api('/api/prompt/templates'); promptTemplates = r.templates || []; }
  catch (e) { console.warn('Prompt templates unavailable', e); }
}

/* ---------------- cast ---------------- */
function charImageIds(c) {
  return (c.images && c.images.length) ? c.images : (c.image ? [c.image] : []);
}
function renderCast() {
  const wrap = $('#castList'); wrap.innerHTML = '';
  $$('.castGuidance').forEach(line=>line.hidden=state.characters.length>0);
  if ($('#composeCharBtn')) $('#composeCharBtn').hidden = !(engine && engine.ref2va);
  // character-sheet drafts: progress, review, and save-to-cast live here
  for (const sh of (state.sheets || [])) wrap.appendChild(sheetDraftCard(sh));
  if (!state.characters.length && !(state.sheets || []).length) { const e = div('castEmpty'); e.innerHTML='<strong>No characters yet</strong><span>Create your first character to reuse it across scenes.</span>'; wrap.appendChild(e); return; }
  for (const c of state.characters) {
    // only show reference images that still exist (deleted media is skipped,
    // never a stale "?" placeholder)
    const ids = charImageIds(c).filter(id => mediaById(id));
    const row = div('castrow');
    const ths = div('castths');
    for (const id of ids) {
      const m = mediaById(id);
      const t = div('castth');
      const im = document.createElement('img'); im.src = mediaUrl(m); t.appendChild(im);
      ths.appendChild(t);
    }
    if (!ids.length) { const e = div('d'); e.style.cssText = 'font-size:11.5px;color:var(--muted)'; e.textContent = 'No reference images'; ths.appendChild(e); }
    const add = div('castth add'); add.textContent = '＋'; add.title = 'Add a reference image';
    add.addEventListener('click', e => { e.stopPropagation(); openCharacterSheet(c); });
    ths.appendChild(add);
    const meta=div('castmeta');
    const nm=div('castnm'),name=document.createElement('strong'),count=document.createElement('span');name.textContent=c.name;name.title=c.name;count.textContent=ids.length+' reference'+(ids.length===1?'':'s');nm.appendChild(name);nm.appendChild(count);meta.appendChild(nm);
    const edit=document.createElement('button');edit.className='castEdit';edit.textContent='Edit';edit.addEventListener('click',e=>{e.stopPropagation();openCharacterSheet(c);});
    const x = document.createElement('button'); x.className = 'castx'; x.textContent = '×'; x.title = 'Remove';
    x.addEventListener('click', e => { e.stopPropagation(); api('/api/characters/' + c.id, { method: 'DELETE' }).then(refresh).catch(error => toast(error.message, 'err')); });
    const actions=div('castrowActions');actions.appendChild(edit);actions.appendChild(x);meta.appendChild(actions);row.appendChild(meta);row.appendChild(ths);
    row.tabIndex=0;row.setAttribute('role','button');row.setAttribute('aria-label','Edit '+c.name);row.addEventListener('click',()=>openCharacterSheet(c));row.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openCharacterSheet(c);}});
    wrap.appendChild(row);
  }
}
function addCharImage(c) {
  // reuse the character modal pre-filled with this character's images
  charMediaIds = charImageIds(c).slice();
  $('#charName').value = c.name;
  charEditId = c.id;
  setCharModalMode(true);
  $('#charPreview').innerHTML = 'Add reference images';
  $('#charSave').disabled = false;
  const strip = $('#charStrip'); if (strip) strip.innerHTML = '';
  renderCharGrid();
  $('#modal').classList.add('on');
}

/* ---------------- character sheet composition ---------------- */
let sheetRecipes = null;   // {recipes, styles, max_references} from the server
let sheetSel = {};         // sheetId -> Set of selected frame media ids

/**
 * composerSheetTemplate — reusable side-sheet shell.
 *
 * Anatomy: title/subtitle/close header, optional fixed top form
 * (.sheetComposerTop), stretchy searchable gallery (.sheetGallery, with only
 * its tile grid scrolling), plus a lower zone with a scrollable form
 * (.sheetBottomScroll) and an always-visible action footer (.sheetActions).
 *
 * cfg = {
 *   id            unique prefix for the search input
 *   mode          composerPickerMode tag
 *   title         panel heading
 *   subtitle      '' hides the subtitle line entirely
 *   state()       mutable state object handed back to every hook
 *   topHtml(st) / bindTop(box, st, ctl)
 *   gallery?: { heading(st), small(st), items(st), matches(item, q),
 *               searchPlaceholder, wrapId,
 *               gridHtml(item, st), emptyText(st, hasItems),
 *               bindGrid(box, st, ctl), queryKey }
 *   middleHtml?(st) / bindMiddle?(box, st, ctl)
 *   bottomNote?(st)
 *   actionsHtml(st) / bindActions(box, st, ctl)
 *   syncActions?(box, st)   called after every grid re-render
 * }
 * ctl = { redraw(), close(), box }
 */
function composerSheetTemplate(cfg) {
  const picker = $('#composerPicker');
  const box = $('#composerPickerDetail');
  const wrapId = (cfg.gallery && cfg.gallery.wrapId) || 'sheetGridWrap';
  composerPickerMode = cfg.mode || 'sheet'; selectedPickerItem = null;
  $('#composerPickerTitle').textContent = cfg.title;
  const sub = $('#composerPickerSubtitle');
  if (cfg.subtitle) {
    sub.textContent = cfg.subtitle; sub.style.display = '';
    $('.sheetHead', picker).classList.remove('nosub');
  } else {
    sub.style.display = 'none';
    $('.sheetHead', picker).classList.add('nosub');
  }
  $('#composerPickerSearch').value = ''; $('#composerPickerList').innerHTML = ''; $('#composerPickerFilters').innerHTML = '';
  box.classList.add('sheetComposer');
  box.classList.remove('characterEditor', 'referenceDetail', 'sheetSimple');

  const ctl = { box, redraw: () => draw(), close: () => closeComposerPicker() };

  function renderGrid() {
    const st = cfg.state(), g = cfg.gallery;
    const q = (st[g.queryKey] || '').trim().toLowerCase();
    const all = g.items(st);
    const folderKey=g.folderKey||'browserFolder',folder=g.folderAware?(st[folderKey]||''):'';
    // Folder-aware sheets mirror the Media panel: root means unfiled media.
    const items = all.filter(m => (!g.folderAware || (m.folder||'')===folder) && (!q || g.matches(m, q)));
    const folderSource=g.folderItems?g.folderItems(st):all;
    const folders=g.folderAware&&!folder&&!q?(state.mediaFolders||[]).map(path=>({path,count:all.filter(m=>(m.folder||'')===path).length,total:folderSource.filter(m=>(m.folder||'')===path).length})).filter(x=>x.total):[];
    const folderTiles=folders.map(x=>'<button class="sheetFolderCard" '+(x.count?'data-sheet-folder="'+esc(x.path)+'"':'disabled')+'><span>📁</span><strong>'+esc(x.path)+'</strong><small>'+(x.count?x.count+' selectable item'+(x.count===1?'':'s'):'Current folder')+'</small></button>').join('');
    const nav=folder?'<button class="sheetFolderBack" data-sheet-folder-back>← All media</button><span class="sheetFolderCurrent">'+esc(folder)+'</span>':'';
    $('#' + wrapId).innerHTML = nav+(items.length||folders.length
      ? '<div class="sourcePickerGrid '+(g.columns===3?'sheetMediaThree':'')+'">' + folderTiles+items.map(m => g.gridHtml(m, st)).join('') + '</div>'
      : '<p class="pickerDetailNote">' + g.emptyText(st, all.length > 0) + '</p>');
    $$('[data-sheet-folder]', $('#' + wrapId)).forEach(button=>button.addEventListener('click',()=>{st[folderKey]=button.dataset.sheetFolder;renderGrid();}));
    const folderBack=$('[data-sheet-folder-back]', $('#' + wrapId));if(folderBack)folderBack.addEventListener('click',()=>{st[folderKey]='';renderGrid();});
    $$('img', $('#' + wrapId)).forEach(image => image.addEventListener('error', () => {
      const thumb=image.closest('.sourceThumb');if(thumb){const fallback=document.createElement('span');fallback.className='pickerMediaState';fallback.textContent='Preview unavailable';thumb.replaceChildren(fallback);}
    }, {once:true}));
    if (g.bindGrid) g.bindGrid(box, st, ctl);
    if (cfg.syncActions) cfg.syncActions(box, st);
  }

  function draw() {
    const st = cfg.state(), g = cfg.gallery;
    const prevScroll = g ? (($('#' + wrapId, box) || {}).scrollTop || 0) : 0;
    const q = g ? (st[g.queryKey] || '') : '';
    const topContent = cfg.topHtml ? cfg.topHtml(st) : '';
    const topZone = topContent ? '<div class="sheetComposerTop">' + topContent + '</div>' : '';
    box.classList.toggle('sheetSimple', !g && !topContent);
    box.innerHTML =
      topZone +
      (g
        ? '<div class="sheetGallery">' +
          '<div class="characterGalleryHead" style="margin-top:2px"><span>' + g.heading(st) + '</span><small>' + g.small(st) + '</small></div>' +
          '<input id="' + cfg.id + 'Search" class="txt sheetGallerySearch" value="' + esc(q) + '" placeholder="' + esc(g.searchPlaceholder || 'Search') + '">' +
          '<div id="' + wrapId + '" class="sheetGalleryGrid"></div>' +
          '</div>'
        : '') +
      '<div class="sheetBottom">' +
      '<div class="sheetBottomScroll">' +
      (cfg.middleHtml ? '<div class="sheetBottomForm">' + cfg.middleHtml(st) + '</div>' : '') +
      (cfg.bottomNote ? '<p class="sheetNote">' + cfg.bottomNote(st) + '</p>' : '') +
      '</div>' +
      cfg.actionsHtml(st) +
      '</div>';
    if (cfg.bindTop) cfg.bindTop(box, st, ctl);
    if (g) {
      const s = $('#' + cfg.id + 'Search');
      if (s) s.addEventListener('input', () => { st[g.queryKey] = s.value; renderGrid(); });
    }
    if (cfg.bindMiddle) cfg.bindMiddle(box, st, ctl);
    if (cfg.bindActions) cfg.bindActions(box, st, ctl);
    if (g) renderGrid();
    if (g) {
      const gw = $('#' + wrapId);
      if (gw && prevScroll) gw.scrollTop = prevScroll;
    }
  }

  draw();
  picker.classList.add('detail-open', 'template-sheet');
  revealSideSheet(picker,$('.pickerPanel',picker));
  return ctl;
}

async function openSheetComposer() {
  try { if (!sheetRecipes) sheetRecipes = await api('/api/sheets/recipes'); }
  catch (e) { toast(e.message, 'err'); return; }
  const maxRefs = sheetRecipes.max_references || 9;
  const st = {
    sel: [], keepNotes: {},
    draftName: '', draftIdentity: '',
    recipe: sheetRecipes.recipes[0].id, style: sheetRecipes.styles[0].id, quality: 'high', seed: 42, steps: 30,
    refQuery: '',refFolder:''
  };
  composerSheetTemplate({
    id: 'sheetC',
    mode: 'sheet',
    title: 'Compose character',
    subtitle: 'One frozen orbit generation becomes a consistent multi-view reference sheet.',
    state: () => st,
    topHtml: s =>
      '<label class="castIdentityLabel">Name</label>' +
      '<input id="sheetCName" class="txt" value="' + esc(s.draftName) + '" placeholder="Character name">' +
      '<label class="castIdentityLabel">Identity <small>Kept verbatim \u2014 wardrobe words are what hold across views.</small></label>' +
      '<textarea id="sheetCIdentity" class="txt" rows="2" placeholder="A weathered knight in blackened mail with a sky-blue cloak\u2026">' + esc(s.draftIdentity) + '</textarea>',
    bindTop: (box, s) => {
      $('#sheetCName').addEventListener('input', e => { s.draftName = e.target.value; });
      $('#sheetCIdentity').addEventListener('input', e => { s.draftIdentity = e.target.value; });
    },
    gallery: {folderAware:true,folderKey:'refFolder',columns:3,
      heading: () => 'Reference images',
      small: s => s.sel.length + ' / ' + maxRefs + ' · one keep-note each',
      items: () => state.media.filter(m => m.kind === 'image' && m.src),
      matches: (m, q) => (m.name || '').toLowerCase().includes(q),
      searchPlaceholder: 'Search images',
      wrapId: 'sheetGridWrap', queryKey: 'refQuery',
      gridHtml: (m, s) =>
        '<button class="sourcePickerCard ' + (s.sel.includes(m.id) ? 'on' : '') + '" data-sheet-media="' + esc(m.id) + '">' +
        '<span class="sourceThumb"><img src="' + esc(mediaUrl(m)) + '" alt="" loading="lazy" decoding="async"><em>' + (s.sel.includes(m.id) ? 'REF ' + (s.sel.indexOf(m.id) + 1) : 'IMAGE') + '</em></span>' +
        '<span class="sourceMeta"><strong>' + esc(m.name) + '</strong></span></button>',
      emptyText: (s, hasItems) => hasItems ? 'No images match your search.' :
        'Import images into the media bin first \u2014 sketches, photos, any rough visual of the character.',
      bindGrid: (box, s, ctl) => {
        $$('[data-sheet-media]', box).forEach(b => b.addEventListener('click', () => {
          s.draftName = $('#sheetCName').value; s.draftIdentity = $('#sheetCIdentity').value;
          const id = b.dataset.sheetMedia, i = s.sel.indexOf(id);
          if (i >= 0) s.sel.splice(i, 1);
          else if (s.sel.length < maxRefs) s.sel.push(id);
          else { toast('MiniMax H3 supports at most ' + maxRefs + ' reference images', 'err'); return; }
          ctl.redraw();
        }));
      }
    },
    middleHtml: s =>
      '<div class="sheetKeepRows">' + s.sel.map((id, i) => {
        const m = mediaById(id);
        return '<div class="sheetKeepRow"><img src="' + esc(mediaUrl(m)) + '" alt="" loading="lazy" decoding="async"><div class="sheetKeepBody">' +
          '<small>REF ' + (i + 1) + ' · keep / ignore note</small>' +
          '<input class="txt" data-sheet-keep="' + esc(id) + '" value="' + esc(s.keepNotes[id] || '') + '" placeholder="keep the coat, ignore the background"></div>' +
          '<button class="sheetKeepX" data-sheet-drop="' + esc(id) + '" title="Remove this reference">×</button></div>';
      }).join('') + '</div>' +
      '<div class="characterGalleryHead"><span>Generation settings</span></div>' +
      '<div class="recipeCards">' + sheetRecipes.recipes.map(r =>
        '<button class="recipeCard ' + (s.recipe === r.id ? 'on' : '') + '" data-sheet-recipe="' + esc(r.id) + '">' +
        '<strong>' + esc(r.name) + '</strong><small>' + esc(r.tagline) + '</small></button>').join('') + '</div>' +
      '<div class="sheetSettings">' +
      '<div class="field"><label>Style </label>' +
      '<select id="sheetCStyle" class="txt">' + sheetRecipes.styles.map(x =>
        '<option value="' + esc(x.id) + '"' + (s.style === x.id ? ' selected' : '') + '>' + esc(x.label || x.name) + '</option>').join('') + '</select></div>' +
      '<div class="field"><label>Quality </label>' +
      '<select id="sheetCQuality" class="txt"><option value="balanced"' + (s.quality === 'balanced' ? ' selected' : '') + '>Balanced</option><option value="high"' + (s.quality === 'high' ? ' selected' : '') + '>High</option><option value="reference"' + (s.quality === 'reference' ? ' selected' : '') + '>Reference</option></select></div>' +
      '</div>' +
      '<div class="sheetSettings">' +
      '<div class="field"><label>Seed </label>' +
      '<input class="txt" type="number" id="sheetCSeed" value="' + s.seed + '"></div>' +
      '<div class="field"><label title="Denoising passes selected by the quality preset.">Steps</label>' +
      '<input class="txt" type="number" id="sheetCSteps" value="' + s.steps + '" min="1" max="60" readonly></div>' +
      '</div>',
    bindMiddle: (box, s, ctl) => {
      $$('[data-sheet-keep]', box).forEach(inp => inp.addEventListener('input', () => { s.keepNotes[inp.dataset.sheetKeep] = inp.value; }));
      $$('[data-sheet-drop]', box).forEach(b => b.addEventListener('click', () => {
        const i = s.sel.indexOf(b.dataset.sheetDrop);
        if (i >= 0) s.sel.splice(i, 1);
        ctl.redraw();
      }));
      $$('[data-sheet-recipe]', box).forEach(b => b.addEventListener('click', () => { s.recipe = b.dataset.sheetRecipe; ctl.redraw(); }));
      $('#sheetCStyle').addEventListener('change', e => { s.style = e.target.value; });
      $('#sheetCQuality').addEventListener('change', e => {
        s.quality = e.target.value;
        s.steps = ({ balanced: 20, high: 30, reference: 50 })[e.target.value] || 30;
        $('#sheetCSteps').value = s.steps;
      });
      $('#sheetCSeed').addEventListener('input', e => { s.seed = parseInt(e.target.value, 10) || 42; });
    },
    actionsHtml: () =>
      '<div class="sheetActions"><button class="btn ghost" id="sheetCCancel">Cancel</button><button class="btn primary" id="sheetCGo">Compose character</button></div>',
    bindActions: (box, s, ctl) => {
      $('#sheetCCancel').addEventListener('click', ctl.close);
      $('#sheetCGo').addEventListener('click', async () => {
        if (!s.sel.length) { toast('Select at least one reference image before composing', 'err'); return; }
        if (s.sel.some(id => !mediaById(id))) { toast('A selected reference is no longer in the media bin', 'err'); return; }
        try {
          await api('/api/sheets', { method: 'POST', body: {
            name: s.draftName.trim() || 'Character', identity: s.draftIdentity.trim(),
            references: s.sel.map(id => ({ mediaId: id, keep: s.keepNotes[id] || '' })),
            recipe: s.recipe, style: s.style, steps: s.steps, seed: s.seed } });
          ctl.close();
          setInspectorTab('cast');
          toast('Composing ' + (s.draftName.trim() || 'character') + ' \u2014 the orbit lands in the media bin when ready', 'ok');
          refresh();
        } catch (e) { toast(e.message, 'err'); }
      });
    }
  });
}
function mediaCardHtml(m, o) {
  o = o || {};
  const unavailable = !m.src || m.status === 'queued' || m.status === 'running' || m.status === 'error';
  const inner = unavailable
    ? '<span class="pickerMediaState">' + (m.status === 'error' ? 'Unavailable' : 'Preparing media…') + '</span>'
    : m.kind === 'image' ? '<img src="' + esc(mediaUrl(m)) + '" alt="" loading="lazy" decoding="async">'
    : m.thumb ? '<img src="' + esc(mediaPathUrl(m.thumb)) + '" alt="" loading="lazy" decoding="async">'
    : '<video src="' + esc(mediaUrl(m)) + '" muted preload="metadata"></video>';
  const tag = o.interactive === false ? 'div' : 'button';
  const data = o.interactive === false ? '' : ' data-media-card="' + esc(m.id) + '"';
  const action=o.folderAction?'<button class="folderMediaAction" data-folder-media-action="'+esc(m.id)+'" type="button">'+esc(o.folderAction)+'</button>':'';
  return '<' + tag + ' class="sourcePickerCard ' + (o.picked ? 'on' : '') + '"' + data + '>' +
    '<span class="sourceThumb">' + inner + (o.tag ? '<em>' + esc(o.tag) + '</em>' : '') + action + '</span>' +
    '<span class="sourceMeta"><strong>' + esc(m.name) + '</strong>' +
    (o.sub ? '<small>' + esc(o.sub) + '</small>' : '') + '</span></' + tag + '>';
}

function openFolderComposer() {
  const st = { sel: [], draftName: '', draftDesc: '', q: '',browserFolder:'' };
  composerSheetTemplate({
    id: 'folderC', mode: 'folder',
    title: 'New folder', subtitle: '',
    state: () => st,
    topHtml: () =>
      '<label class="castIdentityLabel">Name</label>' +
      '<input id="folderCName" class="txt" value="" placeholder="Folder name">' +
      '<label class="castIdentityLabel">Description <small>Optional note shown on the folder tile.</small></label>' +
      '<textarea id="folderCDesc" class="txt" rows="2" placeholder="What belongs in here\u2026"></textarea>',
    bindTop: box => {
      $('#folderCName').addEventListener('input', e => { st.draftName = e.target.value; });
      $('#folderCDesc').addEventListener('input', e => { st.draftDesc = e.target.value; });
    },
    gallery: {folderAware:true,columns:3,folderItems:()=>state.media||[],
      heading: () => 'Move into this folder',
      small: s => s.sel.length + ' selected',
      items: () => state.media || [],
      matches: (m, q) => (m.name || '').toLowerCase().includes(q),
      searchPlaceholder: 'Search media',
      queryKey: 'q',
      gridHtml: (m, s) => mediaCardHtml(m, {
        picked: s.sel.includes(m.id), tag: m.kind === 'image' ? 'IMG' : 'VID',
        sub: (m.folder || '') !== '' ? m.folder : ''
      }),
      emptyText: (s, has) => has ? 'No media matches your search.' :
        'The media bin is empty \u2014 import something first.',
      bindGrid: (box, s, ctl) => {
        $$('[data-media-card]', box).forEach(b => b.addEventListener('click', () => {
          const id = b.dataset.mediaCard, i = st.sel.indexOf(id);
          if (i >= 0) st.sel.splice(i, 1); else st.sel.push(id);
          b.classList.toggle('on', st.sel.includes(id));
          $('.characterGalleryHead small', box).textContent = st.sel.length + ' selected';
        }));
      }
    },
    actionsHtml: () =>
      '<div class="sheetActions"><button class="btn ghost" id="folderCCancel">Cancel</button><button class="btn primary" id="folderCGo">Create folder</button></div>',
    bindActions: (box, s, ctl) => {
      $('#folderCCancel').addEventListener('click', ctl.close);
      $('#folderCGo').addEventListener('click', async () => {
        if (!st.draftName.trim()) { toast('Give the folder a name', 'err'); return; }
        try {
          const r = await api('/api/media/folders/create', { method: 'POST', body: {
            name: st.draftName.trim(), parent: '', description: st.draftDesc.trim(), ids: st.sel } });
          ctl.close();
          mediaFolder = r.path || ''; mediaQuery = '';
          const si = $('#mediaSearch'); if (si) si.value = '';
          toast(st.sel.length ? 'Folder created with ' + st.sel.length + ' item' + (st.sel.length > 1 ? 's' : '') : 'Folder created', 'ok');
          refresh();
        } catch (e) { toast(e.message, 'err'); }
      });
    }
  });
}

function openEditFolderSheet() {
  if (!mediaFolder) return;
  let armed = false, armTimer = null;
  const st = {
    path: mediaFolder,
    desc: ((state.mediaFolderMeta || {})[mediaFolder] || {}).description || '',
    q: ''
  };
  composerSheetTemplate({
    id: 'folderE', mode: 'editFolder',
    title: 'Edit folder',
    subtitle: 'Update this folder and review the media currently stored inside it.',
    state: () => st,
    topHtml: s =>
      '<label class="castIdentityLabel">Name</label>' +
      '<input id="folderEName" class="txt" value="' + esc(s.path) + '" placeholder="Folder name">' +
      '<label class="castIdentityLabel">Description <small>Optional note shown on the folder tile.</small></label>' +
      '<textarea id="folderEDesc" class="txt" rows="2" placeholder="What belongs in here\u2026">' + esc(s.desc) + '</textarea>',
    gallery: {
      heading: () => 'Contents',
      small: s => (state.media || []).filter(m => (m.folder || '') === s.path).length + ' items',
      items: s => (state.media || []).filter(m => (m.folder || '') === s.path),
      matches: (m, q) => (m.name || '').toLowerCase().includes(q),
      searchPlaceholder: 'Search contents',
      wrapId: 'folderEditGridWrap', queryKey: 'q',
      gridHtml: m => mediaCardHtml(m, {
        interactive: false, tag: m.kind==='image'?'IMAGE':'VIDEO', folderAction:'Delete media',
        sub: m.kind === 'video' && m.duration ? fmtDur(m.duration) : 'Stored in this folder'
      }),
      emptyText: (s, has) => has ? 'No items match your search.' : 'This folder is empty.',
      bindGrid: (box,s,ctl) => $$('[data-folder-media-action]',box).forEach(button=>button.addEventListener('click',async()=>{
        const m=mediaById(button.dataset.folderMediaAction);if(!m)return;
        try{
          if(!confirm('Delete "'+m.name+'" permanently?'))return;await api('/api/media/'+m.id,{method:'DELETE'});toast('Deleted '+m.name,'ok');
          await refresh();ctl.redraw();
        }catch(error){toast(error.message,'err');}
      }))
    },
    actionsHtml: () =>
      '<div class="sheetActions"><button class="btn ghost" id="folderEDelete">Delete folder</button><button class="btn primary" id="folderESave">Save changes</button></div>',
    bindActions: (box, s, ctl) => {
      $('#folderESave').addEventListener('click', async () => {
        const name = $('#folderEName').value.trim();
        if (!name) { toast('Give the folder a name', 'err'); return; }
        try {
          const r = await api('/api/media/folders/update', { method: 'POST', body: {
            path: s.path, name, description: $('#folderEDesc').value.trim() } });
          ctl.close();
          mediaFolder = r.path || '';
          toast('Folder updated', 'ok');
          refresh();
        } catch (e) { toast(e.message, 'err'); }
      });
      const del = $('#folderEDelete');
      del.addEventListener('click', async () => {
        if (!armed) {
          armed = true;
          del.textContent = 'Click again to confirm';
          del.classList.add('dangerArmed');
          armTimer = setTimeout(() => { armed = false; del.textContent = 'Delete folder'; del.classList.remove('dangerArmed'); }, 4000);
          return;
        }
        clearTimeout(armTimer);
        try {
          await api('/api/media/folders/delete', { method: 'POST', body: { path: s.path } });
          ctl.close();
          mediaFolder = ''; mediaQuery = '';
          const si = $('#mediaSearch'); if (si) si.value = '';
          toast('Folder deleted \u2014 its items are back in All media', 'ok');
          refresh();
        } catch (e) { toast(e.message, 'err'); }
      });
    }
  });
}

async function openAddToFolderSheet() {
  if (!mediaFolder) return;
  const st = { sel: [], q: '',browserFolder:'' };
  composerSheetTemplate({
    id: 'addC', mode: 'addToFolder',
    title: 'Add media to "' + mediaFolder + '"',
    subtitle: 'Choose existing media. Folder membership changes without creating another media item.',
    state: () => st,
    topHtml: () => '',
    gallery: {folderAware:true,columns:3,folderItems:()=>state.media||[],
      heading: () => 'Available media',
      small: s => s.sel.length + ' selected',
      items: () => (state.media || []).filter(m => (m.folder || '') !== mediaFolder),
      matches: (m, q) => (m.name || '').toLowerCase().includes(q),
      searchPlaceholder: 'Search media',
      wrapId: 'folderAddGridWrap', queryKey: 'q',
      gridHtml: (m, s) => mediaCardHtml(m, { picked: s.sel.includes(m.id), tag: m.kind === 'image' ? 'IMG' : 'VID', sub:(m.folder||'All media') }),
      emptyText: (s, has) => has ? 'No media matches your search.' : 'Every media item is already in this folder.',
      bindGrid: (box, s, ctl) => {
        $$('[data-media-card]', box).forEach(b => b.addEventListener('click', () => {
          const id = b.dataset.mediaCard, i = st.sel.indexOf(id);
          if (i >= 0) st.sel.splice(i, 1); else st.sel.push(id);
          b.classList.toggle('on', st.sel.includes(id));
          $('.characterGalleryHead small', box).textContent = st.sel.length + ' selected';
        }));
      }
    },
    actionsHtml: () =>
      '<div class="sheetActions"><button class="btn ghost" id="addCCancel">Cancel</button><button class="btn primary" id="addCGo">Add to folder</button></div>',
    bindActions: (box, s, ctl) => {
      $('#addCCancel').addEventListener('click', ctl.close);
      $('#addCGo').addEventListener('click', async () => {
        if (!st.sel.length) { toast('Select at least one file to add to this folder', 'err'); return; }
        try {
          const moved=await mediaMoveTo(st.sel,mediaFolder);if(!moved)return;
          ctl.close();
          toast('Added ' + st.sel.length + ' item' + (st.sel.length > 1 ? 's' : '') + ' to "' + mediaFolder + '"', 'ok');
          refresh();
        } catch (e) { toast(e.message, 'err'); }
      });
    }
  });
}
function sheetDraftCard(sh) {
  const card = div('sheetDraft');
  const head = div('sheetDraftHead');
  const nm = div('sheetDraftName'); nm.textContent = '✦ ' + (sh.name || 'Character');
  head.appendChild(nm);
  const x = document.createElement('button'); x.className = 'castx'; x.textContent = '×'; x.title = 'Stop and discard this sheet';
  x.addEventListener('click', async e => {
    e.stopPropagation();
    try { await api('/api/sheets/' + sh.id, { method: 'DELETE' }); delete sheetSel[sh.id]; refresh(); }
    catch (err) { toast(err.message, 'err'); }
  });
  head.appendChild(x);
  card.appendChild(head);

  if (sh.status === 'ready') {
    if (!sheetSel[sh.id]) sheetSel[sh.id] = new Set((sh.frames || []).map(f => f.mediaId));
    const strip = div('frameStrip');
    for (const f of (sh.frames || [])) {
      const m = mediaById(f.mediaId); if (!m) continue;
      const chip = document.createElement('button');
      chip.className = 'frameChip' + (sheetSel[sh.id].has(f.mediaId) ? ' on' : '');
      chip.title = f.label;
      const im = document.createElement('img'); im.src = mediaUrl(m); im.alt = f.label;
      const lab = document.createElement('span'); lab.textContent = f.label;
      chip.appendChild(im); chip.appendChild(lab);
      chip.addEventListener('click', () => {
        const s = sheetSel[sh.id];
        if (s.has(f.mediaId)) s.delete(f.mediaId); else s.add(f.mediaId);
        chip.classList.toggle('on');
        updateFoot();
      });
      strip.appendChild(chip);
    }
    card.appendChild(strip);
    const foot = div('sheetDraftFoot');
    const count = div('d'); count.style.cssText = 'font-size:11.5px;color:var(--muted)';
    const updateFoot = () => { count.textContent = sheetSel[sh.id].size + ' of ' + Math.min(sh.frames.length, 9) + ' views selected'; saveBtn.disabled = !sheetSel[sh.id].size; };
    const redo = document.createElement('button'); redo.className = 'btn ghost'; redo.textContent = 'Redo spin';
    redo.addEventListener('click', async () => {
      try { await api('/api/sheets/' + sh.id + '/generate', { method: 'POST' }); toast('Recomposing ' + (sh.name || 'character'), 'ok'); refresh(); }
      catch (err) { toast(err.message, 'err'); }
    });
    const saveBtn = document.createElement('button'); saveBtn.className = 'btn primary'; saveBtn.textContent = 'Save to cast';
    saveBtn.addEventListener('click', async () => {
      if(saveBtn.disabled||saveBtn.getAttribute('aria-busy')==='true')return;
      saveBtn.setAttribute('aria-busy','true');saveBtn.disabled=true;saveBtn.textContent='Saving…';
      try {
        const c = await api('/api/sheets/' + sh.id + '/save', { method: 'POST', body: {
          name: sh.name, description: sh.identity,
          images: (sh.frames || []).filter(f => sheetSel[sh.id].has(f.mediaId)).map(f => f.mediaId) } });
        selChars.add(c.id);
        delete sheetSel[sh.id];
        // Remove the completed draft immediately. The server has already
        // converted it into Cast; waiting for the next poll can briefly render
        // both structures and makes the Cast panel jump or overlap.
        state.sheets = (state.sheets || []).filter(sheet => sheet.id !== sh.id);
        state.characters = [...(state.characters || []).filter(character => character.id !== c.id), c];
        renderCast();
        toast(c.name + ' joined the cast (' + c.images.length + ' refs)', 'ok');
        await refresh();
      } catch (err) { toast(err.message, 'err');saveBtn.removeAttribute('aria-busy');saveBtn.disabled=false;saveBtn.textContent='Save to cast'; }
    });
    foot.appendChild(count); foot.appendChild(redo); foot.appendChild(saveBtn);
    card.appendChild(foot);
    updateFoot();
  } else if (sh.status === 'error') {
    const err = div('sheetDraftErr'); err.textContent = sh.error || 'Generation failed.';
    card.appendChild(err);
    const foot = div('sheetDraftFoot');
    const retry = document.createElement('button'); retry.className = 'btn ghost'; retry.textContent = 'Retry';
    retry.addEventListener('click', async () => {
      try { await api('/api/sheets/' + sh.id + '/generate', { method: 'POST' }); refresh(); }
      catch (err2) { toast(err2.message, 'err'); }
    });
    foot.appendChild(retry);
    card.appendChild(foot);
  } else {
    const p = sh.progress || {};
    const line = div('sheetDraftProg');
    const pct = p.total > 1 ? Math.round(100 * (p.completed || 0) / p.total) : 0;
    line.innerHTML = '<span>' + esc(p.phase || (sh.status === 'queued' ? 'queued' : 'preparing')) + '</span>' +
      '<i><b style="width:' + pct + '%"></b></i>';
    card.appendChild(line);
  }
  return card;
}


/* ---------------- header ---------------- */
function renderHeader() {
  // canvas picker
  const cp = $('#canvasPick'); cp.innerHTML = '';
  const opts = [[768, 768, '1:1'], [768, 1344, '9:16'], [1344, 768, '16:9']];
  for (const [w, h, lab] of opts) {
    const b = document.createElement('button'); b.textContent = lab;
    if ((state.canvas.width === state.canvas.height && w === h) ||
        (state.canvas.width < state.canvas.height && w < h) ||
        (state.canvas.width > state.canvas.height && w > h)) b.classList.add('on');
    b.addEventListener('click', () => setProjectAspect(lab));
    cp.appendChild(b);
  }
  // export enabled
  $('#exportBtn').disabled = timelineEnd() <= 0;
  // project name
  if (document.activeElement !== $('#projectName')) $('#projectName').value = state.name;
}

/* ---------------- projects gallery ---------------- */
function coverUrl(slug) { return '/api/projects/' + encodeURIComponent(slug) + '/cover'; }

function renderGallery() {
  const grid = $('#galleryGrid');
  if (!grid) return;
  const query = ($('#projectSearch') && $('#projectSearch').value || '').toLowerCase();
  const projects = ((state && state.projects) || []).filter(p => (p.name || '').toLowerCase().includes(query)).slice();
  projects.sort(projectSort === 'name'
    ? (a,b)=>(a.name||'').localeCompare(b.name||'',undefined,{sensitivity:'base'})
    : projectSort === 'created'
      ? (a,b)=>(b.created||0)-(a.created||0)||(b.updated||0)-(a.updated||0)
      : (a,b)=>(b.updated||0)-(a.updated||0));
  // The poll re-renders every 1.5s; rebuilding identical cards would refetch
  // the cover images and make the whole library tab blink. Skip when nothing
  // visible changed.
  const sig = JSON.stringify(projects.map(p => [p.slug, p.name, p.created, p.updated, p.active])) + '|' + query + '|' + projectSort;
  if (grid.dataset.sig === sig) return;
  grid.dataset.sig = sig;
  grid.innerHTML = '';
  if (!projects.length) {
    const e = div('gempty');
    e.innerHTML = '<div class="glyph">▦</div><h2>No projects yet</h2><p>Create your first project to start editing.</p>';
    grid.appendChild(e);
    return;
  }
  for (const p of projects) {
    const card = div('hubCard gcard' + (p.active ? ' current' : ''));
    // cover
    const cover = div('hubThumb gcover');
    const img = document.createElement('img');
    img.loading = 'lazy';
    img.alt = '';
    img.src = coverUrl(p.slug);
    img.onerror = () => { img.style.display = 'none'; cover.classList.add('placeholder'); };
    cover.appendChild(img);
    if (p.active) { const b = div('gbadge'); b.textContent = 'current'; cover.appendChild(b); }
    card.appendChild(cover);
    // meta
    const meta = div('hubMeta gmeta');
    const name = document.createElement('h3'); name.className = 'gname'; name.textContent = p.name || 'Untitled';
    const date = div('gdate'); date.textContent = (projectSort === 'created' ? 'Created ' : 'Modified ') + fmtDate(projectSort === 'created' ? p.created : p.updated);
    meta.appendChild(name); meta.appendChild(date);
    card.appendChild(meta);
    // single, quiet destructive affordance; project name is editable inside the project
    const del = document.createElement('button'); del.className = 'projectDelete'; del.textContent = '×'; del.title = 'Delete project'; del.setAttribute('aria-label', 'Delete ' + (p.name || 'project'));
    del.addEventListener('click', (e) => { e.stopPropagation(); deleteProject(p); });
    card.appendChild(del);
    card.addEventListener('click', () => openProject(p));
    grid.appendChild(card);
  }
}

function openGallery() { setHubView('projects'); }
function closeGallery() { setHubView('editor'); }

function openProject(p) {
  if (p.active) { closeGallery(); return; }
  api('/api/projects/switch', { method: 'POST', body: { slug: p.slug } })
    .then(() => {
      sel = null; playTime = 0; selChars = new Set(); selRefs = new Set(); selectedCharRefs = new Map(); selCharsInitialized = false;
      Object.keys(videoEls).forEach(k => { videoEls[k].remove(); delete videoEls[k]; });
      Object.keys(audioEls).forEach(k => { if(audioGains[k])audioGains[k].disconnect();delete audioGains[k];audioEls[k].remove(); delete audioEls[k]; });
      closeGallery();
      refresh();
    })
    .catch(e => toast(e.message, 'err'));
}

function newProject() {
  const name = prompt('Project name', 'Untitled');
  if (name === null) return;
  api('/api/projects', { method: 'POST', body: { name: name.trim() || 'Untitled' } })
    .then(() => {
      sel = null; playTime = 0; selChars = new Set(); selRefs = new Set(); selectedCharRefs = new Map(); selCharsInitialized = false;
      Object.keys(videoEls).forEach(k => { videoEls[k].remove(); delete videoEls[k]; });
      Object.keys(audioEls).forEach(k => { if(audioGains[k])audioGains[k].disconnect();delete audioGains[k];audioEls[k].remove(); delete audioEls[k]; });
      renderGallery();
      refresh();
    })
    .catch(e => toast(e.message, 'err'));
}

function renameProject(p) {
  const name = prompt('Rename project', p.name);
  if (name === null || !name.trim()) return;
  api('/api/projects/' + encodeURIComponent(p.slug), { method: 'PUT', body: { name: name.trim() } })
    .then(() => { renderGallery(); refresh(); })
    .catch(e => toast(e.message, 'err'));
}

function deleteProject(p) {
  if (!confirm('Delete "' + (p.name || 'Untitled') + '"? This removes its media and cannot be undone.')) return;
  api('/api/projects/' + encodeURIComponent(p.slug), { method: 'DELETE' })
    .then(() => { renderGallery(); refresh(); })
    .catch(e => toast(e.message, 'err'));
}

/* ---------------- split / freeze / zoom ---------------- */
function splitAtPlayhead() {
  // Split the selected clip when it intersects the playhead. With overlapping
  // video and detached-audio lanes, choosing the first clip would silently
  // split a different lane and make the waveform appear out of sync.
  const clips = [];
  for (const t of state.tracks) for (const c of t.clips) clips.push(c);
  let target = sel&&sel.type==='clip'?findClip(sel.id):null;
  if(target){const d=target.out-target.in;if(playTime<target.start||playTime>=target.start+d)target=null;}
  for (const c of clips) {
    if(target)break;
    const d = c.out - c.in;
    if (playTime >= c.start && playTime < c.start + d) { target = c; break; }
  }
  if (!target) { toast('No clip under the playhead', 'err'); return; }
  const tr = trackOfClip(target);
  const localIn = playTime - target.start;
  const newIn = target.in + localIn;
  // left keeps [in, newIn], right is a new clip [newIn, out]
  const leftOut = target.out,originalDuration=Math.max(.05,leftOut-target.in),splitRatio=clamp(localIn/originalDuration,.0001,.9999),[leftKeyframes,rightKeyframes]=splitTransformKeyframes(target,splitRatio),clone=value=>value==null?value:JSON.parse(JSON.stringify(value));
  target.out = newIn;target.keyframes=leftKeyframes;
  const right = { id: null, mediaId: target.mediaId, start: playTime, in: newIn, out: leftOut,
    zoom: target.zoom,position:clone(target.position),motion:clone(target.motion),keyframes:rightKeyframes,color:clone(target.color),audioFade:clone(target.audioFade),volume:target.volume,transition:clone(target.transition)||{type:'cut',dur:0},muted:target.muted,detached:target.detached };
  api('/api/clips/' + target.id, { method: 'PUT', body: { out: target.out,keyframes:leftKeyframes } })
    .then(() => api('/api/clips', { method: 'POST', body: { trackId: tr.id, mediaId: right.mediaId, start: right.start, in: right.in, out: right.out, zoom: right.zoom,position:right.position,motion:right.motion,keyframes:right.keyframes,color:right.color,audioFade:right.audioFade,volume:right.volume,transition:right.transition,muted:right.muted,detached:right.detached } }))
    .then(() => { toast('Split clip', 'ok'); refresh(); })
    .catch(e => toast(e.message, 'err'));
}

function freezeAtPlayhead() {
  // find base-track clip under playhead
  const vtracks = state.tracks.filter(t => t.kind === 'video');
  const base = vtracks.slice().reverse().find(t => t.clips.length);
  if (!base) { toast('No video clip to freeze', 'err'); return; }
  let target = null;
  for (const c of base.clips) { const d = c.out - c.in; if (playTime >= c.start && playTime < c.start + d) { target = c; break; } }
  if (!target) { toast('No clip under the playhead', 'err'); return; }
  const at = target.in + (playTime - target.start);
  api('/api/freeze', { method: 'POST', body: { mediaId: target.mediaId, at, dur: 1.5 } })
    .then(m => api('/api/clips', { method: 'POST', body: { trackId: base.id, mediaId: m.id, start: playTime, in: 0, out: 1.5, zoom: 1, transition: { type: 'cut', dur: 0 }, muted: false, detached: false } }))
    .then(() => { toast('Frame frozen', 'ok'); refresh(); })
    .catch(e => toast(e.message, 'err'));
}

function extractFrameAtPlayhead() {
  // find the base-track clip under the playhead and pull that exact frame
  const vtracks = state.tracks.filter(t => t.kind === 'video');
  const base = vtracks.slice().reverse().find(t => t.clips.length);
  if (!base) { toast('No video clip on the timeline', 'err'); return; }
  let target = null;
  for (const c of base.clips) { const d = c.out - c.in; if (playTime >= c.start && playTime < c.start + d) { target = c; break; } }
  if (!target) { toast('No clip under the playhead', 'err'); return; }
  const at = target.in + (playTime - target.start);
  api('/api/frame', { method: 'POST', body: { mediaId: target.mediaId, at } })
    .then(m => { toast('Extracted frame at ' + at.toFixed(2) + 's', 'ok'); refresh(); })
    .catch(e => toast(e.message, 'err'));
}

function zoomTimeline(dir) {
  pxPerSec = clamp(pxPerSec * (dir > 0 ? 1.25 : 0.8), 20, 400);
  $('#zoomVal').textContent = (pxPerSec / 100).toFixed(1) + '×';
  renderTimeline();
  saveProjectLayout();
}

/* ---------------- export ---------------- */
function doExport() {
  const btn = $('#exportBtn'); btn.disabled = true; btn.textContent = 'Exporting…';
  const startedAt = Date.now();
  postPluginEvent({ name: 'export.started', project: state ? { slug: state.slug, name: state.name } : null });
  api('/api/export', { method: 'POST' })
    .then(r => { btn.textContent = 'Export'; btn.disabled = timelineEnd() <= 0; toast('Export ready', 'ok'); postPluginEvent({ name: 'export.completed', elapsedSeconds: Math.round((Date.now() - startedAt) / 1000), url: r.url, project: state ? { slug: state.slug, name: state.name } : null }); const a = document.createElement('a'); a.href = r.url; a.download = decodeURIComponent((r.url||'').split('/').pop()||'OpenMagia-export.mp4'); document.body.appendChild(a); a.click(); a.remove(); })
    .catch(e => { toast('Export failed: ' + e.message, 'err'); postPluginEvent({ name: 'export.failed', error: e.message || String(e), elapsedSeconds: Math.round((Date.now() - startedAt) / 1000), project: state ? { slug: state.slug, name: state.name } : null }); btn.textContent = 'Export'; btn.disabled = timelineEnd() <= 0; });
}

/* ---------------- timeline Magia ---------------- */
function timelineMagiaOptions(){
  return Object.fromEntries($$('[data-timeline-magia-option]').map(input=>[input.dataset.timelineMagiaOption,input.checked]));
}
function timelineMagiaSelectedClip(){return sel&&sel.type==='clip'?findClip(sel.id):null;}
function timelineMagiaPayload(useAI=false){
  return {seed:timelineMagiaSeed,direction:$('#timelineMagiaDirection').value.trim(),scope:$('#timelineMagiaScope').value,
    selected_clip_id:(timelineMagiaSelectedClip()||{}).id||'',options:timelineMagiaOptions(),use_ai:useAI};
}
function renderTimelineMagiaPlan(plan){
  const host=$('#timelineMagiaPlan');
  if(timelineMagiaPlanning){host.innerHTML='<div class="timelineMagiaLoading">'+(timelineMagiaInterpreting?'Interpreting direction…':'Building edit…')+'</div>';return;}
  if(timelineMagiaError){host.innerHTML='<div class="timelineMagiaEmpty">Preview unavailable. Apply to retry.</div>';return;}
  if(!plan||!(plan.updates||[]).length){const any=Object.values(timelineMagiaOptions()).some(Boolean);host.innerHTML='<div class="timelineMagiaEmpty">'+(any?'No compatible changes for this selection.':'Apply to remove previous Magia effects.')+'</div>';return;}
  const summary=plan.summary||{},facts=[];
  if(summary.transitions)facts.push(summary.transitions+' transition'+(summary.transitions===1?'':'s'));
  if(summary.transforms)facts.push(summary.transforms+' motion edit'+(summary.transforms===1?'':'s'));
  if(summary.color)facts.push(summary.color+' color pass'+(summary.color===1?'':'es'));
  if(summary.trimmed)facts.push(summary.trimmed+' tightened');
  if(summary.overlays)facts.push(summary.overlays+' overlay'+(summary.overlays===1?'':'s'));
  const rows=(plan.updates||[]).slice(0,7).map(update=>{const changes=(update.changes||[]).join(', ')||'audio polish';return '<li><strong>'+esc(update.name||'Clip')+'</strong><span>'+esc(changes.charAt(0).toUpperCase()+changes.slice(1)+'.')+'</span></li>';}).join('');
  host.innerHTML='<div class="timelineMagiaSummary"><strong>'+esc(plan.profile||'Balanced edit')+'</strong><span>'+esc(facts.join(' · ')||((summary.clips||0)+' clips'))+'</span></div>'+(plan.direction&&plan.direction_note?'<p class="timelineMagiaDirectionNote">'+esc(plan.direction_note)+(plan.used_ai?' · Refined':'')+'</p>':'')+'<ul>'+rows+'</ul>'+
    ((plan.updates||[]).length>7?'<small>+'+((plan.updates||[]).length-7)+' more</small>':'');
}
function setTimelineMagiaBusy(busy){
  timelineMagiaPlanning=busy;
  $('#timelineMagiaRemix').disabled=busy;
  $('#timelineMagiaApply').disabled=false;
  renderTimelineMagiaPlan(timelineMagiaPlan);
}
async function requestTimelineMagiaPlan(remix=false,useAI=false){
  if(remix||!timelineMagiaSeed)timelineMagiaSeed=(Date.now()+Math.floor(Math.random()*100000))%2147483647;
  if($('#timelineMagiaScope').value==='selected'&&!timelineMagiaSelectedClip()){
    $('#timelineMagiaScope').value='timeline';
    toast('Select a timeline clip to use that scope','warn');
  }
  timelineMagiaError='';
  timelineMagiaInterpreting=useAI&&!!$('#timelineMagiaDirection').value.trim();
  setTimelineMagiaBusy(true);
  try{timelineMagiaPlan=await api('/api/timeline/magia/plan',{method:'POST',body:timelineMagiaPayload(useAI)});return timelineMagiaPlan;}
  catch(error){timelineMagiaPlan=null;timelineMagiaError=error.status===404?'Restart OpenMagia to load timeline Magia.':error.message;toast(timelineMagiaError,'err');return null;}
  finally{timelineMagiaInterpreting=false;setTimelineMagiaBusy(false);}
}
function scheduleTimelineMagiaPlan(){
  clearTimeout(timelineMagiaTimer);
  timelineMagiaTimer=setTimeout(()=>requestTimelineMagiaPlan(false),180);
}
function openTimelineMagia(){
  const hasVideo=(state.tracks||[]).some(track=>track.kind==='video'&&(track.clips||[]).length);
  if(!hasVideo)return toast('Add a video clip to the timeline first','err');
  const selected=timelineMagiaSelectedClip();
  const selectedOption=$('#timelineMagiaScope option[value="selected"]');
  selectedOption.disabled=!selected;
  $('#timelineMagiaScope').value='timeline';
  timelineMagiaPlan=null;timelineMagiaError='';timelineMagiaSeed=0;
  const sheet=$('#timelineMagiaSheet');sheet.classList.add('on');sheet.setAttribute('aria-hidden','false');
  requestAnimationFrame(()=>$('#timelineMagiaDirection').focus({preventScroll:true}));
  requestTimelineMagiaPlan(true);
}
function closeTimelineMagia(){
  clearTimeout(timelineMagiaTimer);
  const sheet=$('#timelineMagiaSheet');sheet.classList.remove('on');sheet.setAttribute('aria-hidden','true');
}
async function applyTimelineMagia(){
  if(timelineMagiaPlanning)return toast('Magia is still preparing the edit','warn');
  const enabled=Object.entries(timelineMagiaOptions()).filter(([,value])=>value).map(([key])=>key);
  if($('#timelineMagiaDirection').value.trim()){
    const interpreted=await requestTimelineMagiaPlan(false,true);
    if(!interpreted)return;
  }else if(!timelineMagiaPlan){
    const planned=await requestTimelineMagiaPlan(false);
    if(!planned)return;
  }
  if(!(timelineMagiaPlan.updates||[]).length){
    if(enabled.length===1&&enabled[0]==='transitions')return toast('Transitions need at least two clips on the same video track','err');
    if(enabled.length)return toast('No compatible effects are available for this selection','err');
  }
  const button=$('#timelineMagiaApply'),label=button.textContent;button.disabled=true;button.textContent='Applying…';
  try{
    const result=await api('/api/timeline/magia/apply',{method:'POST',body:timelineMagiaPayload(true)});
    closeTimelineMagia();await refresh(true);
    toast('Magia applied to '+result.applied+' clip'+(result.applied===1?'':'s'),'ok');
  }catch(error){toast(error.status===404?'Restart OpenMagia to load timeline Magia.':error.message,'err');}
  finally{button.textContent=label;button.disabled=false;}
}

/* ---------------- toast ---------------- */
let toastTimer = 0;
function toast(msg, type) {
  const t = $('#toast'); t.className = 'on ' + (type || '');
  t.innerHTML = '<span class="ticon">' + (type === 'err' ? '!' : '✓') + '</span>' + esc(msg);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.className = '', 2600);
}

/* ---------------- character modal ---------------- */
function charGridSelected() {
  return charMediaIds;
}
function renderCharGrid() {
  const grid = $('#charMediaGrid'); grid.innerHTML = '';
  const imgs = state.media.filter(m => m.kind === 'image');
  if (!imgs.length) { const e = div('d'); e.style.cssText = 'font-size:12px;color:var(--muted)'; e.textContent = 'No images in your media yet — upload one below.'; grid.appendChild(e); }
  for (const m of imgs) {
    const isSel = charMediaIds.includes(m.id);
    const t = div('charpick' + (isSel ? ' sel' : ''));
    t.dataset.id = m.id;
    const im = document.createElement('img'); im.src = mediaUrl(m); t.appendChild(im);
    if (isSel) {
      const badge = div('charpickbadge'); badge.textContent = String(charMediaIds.indexOf(m.id) + 1); t.appendChild(badge);
    }
    const nm = div('charpicknm'); nm.textContent = m.name; t.appendChild(nm);
    t.addEventListener('click', () => toggleCharMedia(m.id));
    grid.appendChild(t);
  }
  // selection strip
  const stripWrap = $('#charStrip'); stripWrap.innerHTML = '';
  const strip = div('charstrip');
  if (!charMediaIds.length) { const e = div('d'); e.style.cssText = 'font-size:11.5px;color:var(--muted)'; e.textContent = 'Select one or more photos of the same person.'; strip.appendChild(e); }
  else {
    const lab = div('d'); lab.style.cssText = 'font-size:11px;color:var(--muted);font-weight:600'; lab.textContent = charMediaIds.length + ' reference' + (charMediaIds.length>1?'s':'') + ' (in order)'; strip.appendChild(lab);
  }
  stripWrap.appendChild(strip);
}
function toggleCharMedia(id) {
  const i = charMediaIds.indexOf(id);
  if (i >= 0) charMediaIds.splice(i, 1);
  else charMediaIds.push(id);
  renderCharGrid();
  updateCharPreview();
}
function updateCharPreview() {
  const pv = $('#charPreview');
  if (!charMediaIds.length) { pv.innerHTML = 'No portrait selected'; }
  else {
    const m = mediaById(charMediaIds[0]);
    pv.innerHTML = '<img src="' + mediaUrl(m) + '">' + (charMediaIds.length>1 ? '<span class="more">+' + (charMediaIds.length-1) + '</span>' : '');
  }
  $('#charSave').disabled = !charMediaIds.length;
}
function setCharModalMode(editing) {
  $('#charModalTitle').textContent = editing ? 'Edit character' : 'New character';
  $('#charModalSub').textContent = editing
    ? 'Add or remove reference photos. More views of the same person improve consistency.'
    : 'Add a face once — attach it to generated scenes to keep that person consistent.';
  $('#charSave').textContent = editing ? 'Save changes' : 'Add character';
}
function openCharModal() {
  charEditId = null; charMediaIds = []; $('#charName').value = '';
  setCharModalMode(false);
  $('#charPreview').innerHTML = 'No portrait selected';
  $('#charSave').disabled = true;
  const strip = $('#charStrip'); if (strip) strip.innerHTML = '';
  renderCharGrid();
  $('#modal').classList.add('on');
}
function closeCharModal() { $('#modal').classList.remove('on'); }
function pickCharImage(file) {
  api('/api/upload', { method: 'POST', raw: file, headers: { 'X-File-Name': encodeURIComponent(file.name), 'Content-Type': 'application/octet-stream' } })
    .then(m => { refresh().then(() => { if (!charMediaIds.includes(m.id)) charMediaIds.push(m.id); renderCharGrid(); updateCharPreview(); }); })
    .catch(e => toast(e.message, 'err'));
}
function saveChar() {
  const name = $('#charName').value.trim() || 'Character';
  if (!charMediaIds.length) { toast('Choose a portrait first', 'err'); return; }
  if (charMediaIds.length > 9) { toast('MiniMax H3 supports up to 9 ordered references per scene', 'err'); return; }
  const n = charMediaIds.length;
  const done = () => { closeCharModal(); toast((charEditId ? 'Updated ' : 'Added ') + name + ' (' + n + ' reference' + (n>1?'s':'') + ')', 'ok'); refresh(); };
  if (charEditId) {
    api('/api/characters/' + charEditId, { method: 'PUT', body: { name, images: charMediaIds } }).then(done)
      .catch(e => toast(e.message, 'err'));
  } else {
    // Adding a character means "use this person", so attach it to upcoming
    // scenes automatically — no extra chip click required.
    api('/api/characters', { method: 'POST', body: { name, images: charMediaIds } })
      .then(c => { selChars.add(c.id); done(); })
      .catch(e => toast(e.message, 'err'));
  }
}

/* ---------------- universal workspace views ---------------- */
function setInspectorTab(tab) {
  inspectorTab = tab;
  const col = $('#rightCol'); if (!col) return;
  col.dataset.tab = tab;
  $$('[data-inspector-tab]').forEach(b => b.classList.toggle('on', b.dataset.inspectorTab === tab));
}

function bindPanelResize(handle, cssVar, min, max, invert) {
  handle.addEventListener('pointerdown', e => {
    e.preventDefault(); handle.classList.add('dragging');
    const startX = e.clientX;
    const current = parseFloat(getComputedStyle(document.body).getPropertyValue(cssVar)) || min;
    const move = ev => {
      const delta = (ev.clientX - startX) * (invert ? -1 : 1);
      const value = clamp(current + delta, min, Math.min(max, window.innerWidth * .42));
      document.body.style.setProperty(cssVar, value + 'px');
      localStorage.setItem('openmagia' + cssVar, String(value));
    };
    const up = () => { handle.classList.remove('dragging');saveProjectLayout();window.removeEventListener('pointermove', move);window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  });
}

function setHubView(view) {
  hubView = view;
  document.body.classList.toggle('project-open', view === 'editor');
  $('#gallery').classList.toggle('on', view === 'projects');
  $('#assetCenter').classList.toggle('on', view === 'assets');
  $('#assetCenter').setAttribute('aria-hidden', view === 'assets' ? 'false' : 'true');
  $('#skillsCenter').classList.toggle('on', view === 'skills');
  $('#skillsCenter').setAttribute('aria-hidden', view === 'skills' ? 'false' : 'true');
  $('#settingsCenter').classList.toggle('on', view === 'settings');
  $('#settingsCenter').setAttribute('aria-hidden', view === 'settings' ? 'false' : 'true');
  $$('.sideNavItem').forEach(b => b.classList.toggle('active', b.dataset.view === view || (view === 'editor' && b.dataset.view === 'projects')));
  if (view === 'projects') renderGallery();
  if (view === 'assets') renderAssetCenter({force:true});
  if (view === 'skills') renderSkillsCenter();
  if (view === 'settings') renderSettings();
}

function modelSettingsFingerprint(value){const installs=value.model_installs||{};return JSON.stringify([value.h3_bin_ok,value.fl2va,value.ref2va,value.formatter,value.ffmpeg,Object.entries(installs).sort(([a],[b])=>a.localeCompare(b)).map(([id,job])=>[id,job?.status||''])]);}
function updateModelInstallProgress(){
  const installs=engine.model_installs||{};
  for(const [component,verb] of [['h3','Downloading'],['formatter','Installing'],['runtime','Installing']]){
    const job=installs[component]||{};
    if(job.status!=='running')continue;
    const label=verb+'…'+(Number.isFinite(job.progress)?' '+job.progress+'%':'');
    $$('[data-install-model="'+component+'"],[data-install-component="'+component+'"]').forEach(button=>{button.textContent=label;button.disabled=true;});
  }
}

function closeModelUninstall(){modelUninstallState=null;const sheet=$('#modelUninstallSheet');sheet.classList.remove('on');sheet.setAttribute('aria-hidden','true');}
function closeModelLicense(){modelLicenseState=null;const sheet=$('#modelLicenseSheet');sheet.classList.remove('on');sheet.setAttribute('aria-hidden','true');}
function openModelLicense(item){
  modelLicenseState={component:item.install_component||item.id,name:item.name};
  $('#modelLicenseTitle').textContent='Install '+item.name;
  $('#modelLicenseSubtitle').textContent='Review and accept the MiniMax H3 terms before the download begins.';
  $('#modelLicenseBody').innerHTML='<div class="licenseReview"><p>This backend uses MiniMax H3 weights and is governed by the MiniMax H3 Community License. OpenMagia will download the backend into this computer’s managed models folder.</p><a class="btn ghost" href="https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE" target="_blank" rel="noopener">Read the complete license ↗</a><label><input id="modelLicenseAccept" type="checkbox"><span>I have read and accept the MiniMax H3 license.</span></label></div>';
  const confirm=$('#modelLicenseConfirm');confirm.disabled=true;confirm.textContent='Accept and download';
  $('#modelLicenseAccept').addEventListener('change',event=>{confirm.disabled=!event.target.checked;});
  const sheet=$('#modelLicenseSheet');sheet.classList.add('on');sheet.setAttribute('aria-hidden','false');$('#modelLicenseClose').focus({preventScroll:true});
}
async function openModelUninstall(installationId,knownManagement=null){
  let management=knownManagement;
  try{if(!management)management=await api('/api/models/manage');}catch(error){toast(error.message,'err');return;}
  const installation=(management.installations||[]).find(item=>item.id===installationId);if(!installation){toast('That model is no longer installed','err');return;}
  modelUninstallState={installationId,management};
  $('#modelUninstallTitle').textContent='Uninstall '+(installation.name||'model');
  $('#modelUninstallSubtitle').textContent='Remove this model and its managed files from this computer.';
  const alternatives=(management.installations||[]).filter(item=>item.id!==installationId&&item.available);
  const compatible=(management.catalog||[]).filter(item=>item.compatible&&item.id!==installation.backend_id);
  const installedChoices=alternatives.map(item=>'<div class="replacementRow"><span><b>'+esc(item.name)+'</b><small>'+esc(item.path)+'</small></span><button class="btn ghost" data-use-replacement="'+esc(item.id)+'">Use instead</button></div>').join('');
  const availableChoices=compatible.map(item=>'<div class="replacementCard '+(item.recommended?'recommended':'')+'"><span><b>'+esc(item.name)+'</b><small>'+esc(item.summary)+'</small></span>'+(item.install_component?'<button class="btn ghost" data-install-replacement="'+esc(item.install_component)+'">Install</button>':'<a href="'+esc(item.source)+'" target="_blank" rel="noopener">Setup guide ↗</a>')+'</div>').join('');
  $('#modelUninstallBody').innerHTML='<div class="uninstallModelSummary"><b>'+esc(installation.name)+'</b><span>'+esc(installation.path)+'</span></div>'+(installation.active?'<div class="uninstallNotice"><strong>Video generation will be unavailable</strong><p>You can still edit projects and use existing media. Install or connect another model whenever you want to generate again.</p></div>':'')+(installedChoices?'<section class="replacementSection"><h3>Switch first <span>Optional</span></h3>'+installedChoices+'</section>':'')+'<section class="replacementSection"><h3>Other models <span>Optional</span></h3><div class="replacementGrid">'+(availableChoices||'<p>No additional backend is available for this computer.</p>')+'</div></section>';
  const confirm=$('#modelUninstallConfirm');confirm.disabled=false;confirm.textContent='Uninstall and remove files';
  $$('[data-use-replacement]',$('#modelUninstallBody')).forEach(button=>button.addEventListener('click',async()=>{const replacement=alternatives.find(item=>item.id===button.dataset.useReplacement);if(!replacement)return;button.disabled=true;try{await api('/api/models/select',{method:'POST',body:{kind:'h3',path:replacement.path,role:'video_generation'}});toast('Using '+replacement.name,'ok');await refresh();await openModelUninstall(installationId);renderSettings();}catch(error){button.disabled=false;toast(error.message,'err');}}));
  $$('[data-install-replacement]',$('#modelUninstallBody')).forEach(button=>button.addEventListener('click',async()=>{button.disabled=true;try{await api('/api/models/install',{method:'POST',body:{component:button.dataset.installReplacement,accepted_license:true}});localStorage.setItem('openmagiaH3LicenseAccepted','true');toast('Installation started. Keep OpenMagia open until it finishes.','ok');closeModelUninstall();renderSettings();}catch(error){button.disabled=false;toast(error.message,'err');}}));
  const sheet=$('#modelUninstallSheet');sheet.classList.add('on');sheet.setAttribute('aria-hidden','false');$('#modelUninstallClose').focus({preventScroll:true});
}

async function renderSettings() {
  const e = engine || {}; const body = $('#settingsBody'); if (!body) return;
  const versionLabel=e.app_version?(e.app_version+(e.app_build?' · '+e.app_build:'')):'Unavailable · restart OpenMagia';
  let management={hardware:{},catalog:[],installations:[],loras:[]};
  try{management=await api('/api/models/manage');}catch(error){management.error=error.message;}
  settingsModelFingerprint=modelSettingsFingerprint(e);
  const hi=(e.model_installs||{}).h3||{},fi=(e.model_installs||{}).formatter||{},ri=(e.model_installs||{}).runtime||{};
  const installState=(job,ready,verb)=>job.status==='running'?{label:'',button:verb+'…',disabled:true,kind:'running'}:ready?{label:'',button:'Installed',disabled:true,kind:'ready'}:job.status==='error'?{label:'Failed',button:'Try again',disabled:false,kind:'error'}:{label:'',button:'Download',disabled:false,kind:'idle'};
  const hs=installState(hi,!!(e.h3_bin_ok&&e.fl2va&&e.ref2va),'Downloading'),fs=installState(fi,!!e.formatter,'Installing'),rs=installState(ri,!!e.ffmpeg,'Installing');
  const installError=job=>job.status==='error'&&job.message?'<small class="modelInstallError" title="'+esc(job.message)+'">'+esc(job.message.trim().split(/\n/).pop())+'</small>':'';
  const formatterInstall=fs.kind==='ready'?'':'<div class="modelInstallRow '+fs.kind+'"><span><b>Prompt refinement</b><small>Qwen2.5 1.5B · 1 GB</small>'+installError(fi)+'</span><strong>'+fs.label+'</strong><button class="btn ghost modelInstall" data-install-model="formatter" '+(fs.disabled?'disabled':'')+'>'+fs.button+'</button></div>';
  const runtimeInstall=rs.kind==='ready'?'':'<div class="modelInstallRow '+rs.kind+'"><span><b>Generation tools</b><small>FFmpeg</small>'+installError(ri)+'</span><strong>'+rs.label+'</strong><button class="btn ghost modelInstall" data-install-model="runtime" '+(rs.disabled?'disabled':'')+'>'+rs.button+'</button></div>';
  const hw=management.hardware||{},platformName=hw.os==='darwin'&&hw.architecture==='arm64'?'Apple Silicon Mac':hw.os==='darwin'?'Intel Mac':hw.gpu?'NVIDIA computer':(hw.os||'Computer');
  const recommendation=(management.catalog||[]).find(item=>item.recommended);
  const hardware='<section class="modelHardware"><div><b>'+esc(platformName)+'</b><span>'+esc([hw.memory_gb?hw.memory_gb+' GB RAM':'',hw.gpu,hw.vram_gb?hw.vram_gb+' GB VRAM':''].filter(Boolean).join(' · '))+'</span></div><strong>'+esc(recommendation?recommendation.name+' recommended':'No verified local backend recommended')+'</strong><small>'+esc(hw.disk_free_gb?hw.disk_free_gb+' GB available':'Hardware scan unavailable')+'</small></section>';
  const installed=(management.installations||[]).filter(item=>item.available).map(item=>'<div class="managedModelRow"><span><b>'+esc(item.name||'MiniMax H3')+'</b><small>'+esc(item.path||'')+'</small></span><em>'+(item.active?'In use':'Installed')+'</em>'+(item.managed?'<button class="btn ghost danger" data-uninstall-model="'+esc(item.id)+'">Uninstall</button>':'<small>External</small>')+'</div>').join('')||'<p class="modelEmpty">No video models are installed on this computer. Choose one from Available.</p>';
  const availableItems=(management.catalog||[]).filter(item=>item.compatible);
  const available=availableItems.map(item=>{const job=(e.model_installs||{})[item.install_component]||{},running=job.status==='running',failed=job.status==='error',progress=Number.isFinite(job.progress)?' '+job.progress+'%':'';return '<div class="backendCard '+(item.recommended?'recommended ':'')+'"><div class="backendTitle"><b>'+esc(item.name)+'</b>'+(item.recommended?'<strong>Recommended</strong>':'')+'</div><p>'+esc(item.summary)+'</p><small>'+esc((item.memory_min||0)+' GB RAM minimum · '+item.disk_gb+' GB disk · '+item.stability)+'</small>'+(failed?'<small class="modelInstallError">'+esc(job.message||'Installation failed')+'</small>':'')+'<div class="backendActions">'+(item.installed?'<span>Installed</span>':item.requirements_met?'<button class="btn ghost" data-install-backend="'+esc(item.id)+'" data-install-component="'+esc(item.install_component)+'" '+(running?'disabled':'')+'>'+(running?'Downloading…'+progress:'Install')+'</button>':'<span>This computer does not meet the minimum requirements</span>')+'</div></div>';}).join('')||'<div class="modelUnavailable"><b>No integrated local H3 backend is available for this computer.</b><p>OpenMagia only lists runtimes it can install and execute end to end.</p></div>';
  const legacyLoras=(management.loras||[]).map(item=>'<div class="loraRow" data-lora="'+esc(item.id)+'"><span><b>'+esc(item.name)+'</b><small>Stored but inactive</small></span><button class="btn ghost danger" data-remove-lora>Remove file</button></div>').join('');
    body.innerHTML = '<article class="settingsCard modelSettings"><div class="settingsCardHead"><div><h2>Models</h2><small>Recommendations update automatically for the hardware running OpenMagia.</small></div></div>'+hardware+'<div class="modelManagerTabs" role="tablist"><button class="on" data-model-tab="installed">Installed</button><button data-model-tab="available">Available</button><button data-model-tab="addons">Add-ons</button></div><section class="modelManagerPane on" data-model-pane="installed"><div class="managedModelList">'+installed+'</div><details class="modelConnect"><summary>Connect an existing model</summary><section class="modelSelection"><div class="modelSelectionHead"><span>Models already downloaded outside OpenMagia</span><button class="btn ghost" id="detectModels">Scan</button></div><div class="modelSelectionFields"><label><select id="detectedModelSelect" class="txt" aria-label="Installed model" disabled><option>Scanning…</option></select></label><label><select id="detectedModelRole" class="txt" aria-label="Model role" disabled><option>Role</option></select></label><button class="btn primary" id="useSelectedModel" disabled>Use</button></div><div id="detectedModelMeta" class="modelSelectionMeta"></div></section></details></section><section class="modelManagerPane" data-model-pane="available"><div class="backendGrid">'+available+'</div></section><section class="modelManagerPane" data-model-pane="addons"><div class="modelInstallOptions">'+formatterInstall+runtimeInstall+'</div><div class="loraHead"><div><b>LoRA adapters</b><small>The current h3.c engine does not expose LoRA loading, so OpenMagia cannot apply adapters to generation yet.</small></div></div>'+(legacyLoras?'<div class="loraList">'+legacyLoras+'</div>':'')+'</section></article>' +
    '<article class="settingsCard aboutCard"><h2>About OpenMagia</h2><p>OpenMagia is a local-first visual workspace for composing, generating, and editing AI video.</p><div class="appVersionRow"><b>Version</b><span>'+esc(versionLabel)+'</span></div><div><b>Source code</b><a href="https://github.com/davidaircloud/OpenMagia" target="_blank" rel="noopener">GitHub repository ↗</a></div><div><b>License</b><span>AGPL-3.0-only</span></div><small>OpenMagia is free software under the GNU Affero General Public License v3.0 only and comes without warranty.</small></article>'+
    '<article class="settingsCard noticesCard"><h2>Models and open-source notices</h2><div><b>MiniMax H3</b><span>© 2026 MiniMax · Community License</span></div><div><b>Qwen2.5 1.5B Instruct</b><span>Apache License 2.0</span></div><div><b>h3.c</b><span>© 2026 Salvatore Sanfilippo · MIT</span></div><div><b>ccv Metal kernels</b><span>© 2010 Liu Liu · BSD-3-Clause</span></div><div><b>llama.cpp / ggml</b><span>© 2023–2026 ggml authors · MIT</span></div><div><b>FFmpeg</b><span>LGPL 2.1+ / optional GPL components</span></div><small>Remaining application code uses the Python standard library and native browser APIs. Full terms remain with the bundled projects and linked model sources.</small></article>';
  $$('[data-model-tab]',body).forEach(tab=>tab.addEventListener('click',()=>{$$('[data-model-tab]',body).forEach(x=>x.classList.toggle('on',x===tab));$$('[data-model-pane]',body).forEach(x=>x.classList.toggle('on',x.dataset.modelPane===tab.dataset.modelTab));}));
  $$('[data-install-backend]',body).forEach(button=>button.addEventListener('click',()=>{const item=(management.catalog||[]).find(entry=>entry.id===button.dataset.installBackend);if(item)openModelLicense(item);}));
  $$('[data-uninstall-model]',body).forEach(button=>button.addEventListener('click',()=>openModelUninstall(button.dataset.uninstallModel,management)));
  $$('.loraRow',body).forEach(row=>{const id=row.dataset.lora;$('[data-remove-lora]',row).addEventListener('click',async()=>{if(!confirm('Remove this inactive LoRA file from OpenMagia?'))return;try{await api('/api/models/loras/'+id,{method:'DELETE'});toast('LoRA file removed','ok');renderSettings();}catch(error){toast(error.message,'err');}});});
  $$('[data-install-model]',body).forEach(button=>button.addEventListener('click',async()=>{const component=button.dataset.installModel;button.disabled=true;try{await api('/api/models/install',{method:'POST',body:{component,accepted_license:true}});toast('Installation started. Keep Settings open to monitor it.','ok');await refresh();renderSettings();}catch(err){button.disabled=false;toast(err.message,'err');}}));
  const scan=async()=>{const modelSelect=$('#detectedModelSelect'),roleSelect=$('#detectedModelRole'),useButton=$('#useSelectedModel'),meta=$('#detectedModelMeta');if(!modelSelect||!roleSelect||!useButton||!meta)return;modelSelect.disabled=true;roleSelect.disabled=true;useButton.disabled=true;modelSelect.innerHTML='<option>Scanning local models…</option>';meta.textContent='Looking through OpenMagia, LM Studio, MLX-LM, and known local caches…';try{const result=await api('/api/models/discover'),sources=result.sources||[];if(!sources.length){modelSelect.innerHTML='<option>No compatible models found</option>';meta.textContent='Start LM Studio or MLX-LM, or install an OpenMagia default below.';return;}const initial=Math.max(0,sources.findIndex(source=>source.active));modelSelect.innerHTML=sources.map((source,index)=>'<option value="'+index+'" '+(index===initial?'selected':'')+'>'+esc(source.name)+' · '+esc(source.provider)+'</option>').join('');modelSelect.disabled=false;const syncSelection=()=>{const source=sources[+modelSelect.value]||sources[0],roles=source.roles||[];roleSelect.innerHTML=roles.map(role=>'<option value="'+esc(role.id)+'" '+((source.active_role||roles[0]?.id)===role.id?'selected':'')+'>'+esc(role.label)+'</option>').join('')||'<option value="">Compatible role</option>';roleSelect.disabled=!roles.length;const location=source.path||source.endpoint||'';meta.innerHTML='<b>'+esc(source.kind==='h3'?'H3 video engine':source.kind==='formatter_file'?'GGUF refinement model':'Local model server')+'</b><span title="'+esc(location)+'">'+esc(location)+'</span>';const active=!!source.active&&(!source.active_role||source.active_role===roleSelect.value);useButton.disabled=active||!roles.length;useButton.textContent=active?'In use':'Use model';};modelSelect.addEventListener('change',syncSelection);roleSelect.addEventListener('change',syncSelection);useButton.addEventListener('click',async()=>{const source=sources[+modelSelect.value]||sources[0],role=roleSelect.value||'';useButton.disabled=true;try{await api('/api/models/select',{method:'POST',body:{kind:source.kind,path:source.path,endpoint:source.endpoint,model_id:source.id,role}});toast('Using '+source.name+' for '+((source.roles||[]).find(item=>item.id===role)?.label||'OpenMagia'),'ok');await refresh();renderSettings();}catch(error){useButton.disabled=false;toast(error.message,'err');}});syncSelection();}catch(error){modelSelect.innerHTML='<option>Scan unavailable</option>';meta.textContent=error.message;}};
  $('#detectModels').addEventListener('click',scan);scan();
  updateModelInstallProgress();
}
function renderSideProjects() {
  const wrap = $('#sideProjectList'); if (!wrap || !state) return; wrap.innerHTML = '';
  for (const p of state.projects || []) {
    const b = document.createElement('button'); b.className = 'sideProject' + (p.active ? ' on' : ''); b.textContent = p.name || 'Untitled';
    b.addEventListener('click', () => openProject(p)); wrap.appendChild(b);
  }
}
async function renderAssetCenter(options={}) {
  const grid = $('#assetCenterGrid'); if (!grid || !state) return;
  const request=++assetLibraryRequest,scroller=$('#assetCenter'),scrollTop=scroller?scroller.scrollTop:0;
  if(options.force||!assetLibraryLoaded){if(!assetLibraryLoaded)grid.innerHTML = '<div class="gempty"><p>Loading assets…</p></div>';
  try { assetLibraryState = await api('/api/assets/library'); assetLibraryLoaded=true; }
  catch (e) { grid.innerHTML = '<div class="gempty"><h2>Could not load assets</h2><p>' + esc(e.message) + '</p></div>'; return; }
  if(request!==assetLibraryRequest)return;}
  const projects = assetLibraryState.projects || [];
  const filters = $('#assetProjectFilters'); filters.innerHTML = '';
  for (const option of [{slug:'all',name:'All projects'}, ...projects]) {
    const b = document.createElement('button'); b.textContent = option.name; b.classList.toggle('on', assetProjectFilter === option.slug);
    b.addEventListener('click', () => { assetProjectFilter = option.slug; renderAssetCenter(); }); filters.appendChild(b);
  }
  const q = ($('#assetSearch').value || '').toLowerCase();
  const items = (assetLibraryState.assets || []).filter(a =>
    (assetFilter === 'all' || a.kind === assetFilter) &&
    (assetProjectFilter === 'all' || a.assignments.some(x => x.slug === assetProjectFilter)) &&
    (a.name || '').toLowerCase().includes(q));
  grid.innerHTML = '';
  if (!items.length) { grid.innerHTML = '<div class="gempty"><div class="glyph">◈</div><h2>No matching assets</h2><p>Change the project filter or import media.</p></div>'; if(scroller)requestAnimationFrame(()=>{scroller.scrollTop=scrollTop;}); return; }
  for (const item of items) {
    const card = div('hubCard assetLibraryCard'); const thumb = div('hubThumb');
    if (item.status === 'queued' || item.status === 'running') {
      const pending = div('generationPending'); pending.innerHTML = '<span></span><b>' + (item.status === 'running' ? 'Generating' : 'Queued') + '</b>'; thumb.appendChild(pending);
    } else if (item.status === 'error') {
      const pending = div('generationPending error'); pending.innerHTML = '<b>Generation failed</b>'; thumb.appendChild(pending);
    } else {
      const el = document.createElement('img'); el.src = item.preview; el.alt = '';
      if (item.kind === 'video') el.onerror = () => { const v = document.createElement('video'); v.src = item.preview; v.muted = true; v.preload = 'metadata'; v.playsInline = true; el.replaceWith(v); };
      thumb.appendChild(el);
    }
    const meta = div('hubMeta'); const names = item.assignments.map(x => x.name);
    meta.innerHTML = '<h3></h3><p></p><div class="assetAssignments"></div><button class="assetAssignBtn">Manage projects</button>';
    meta.querySelector('h3').textContent = item.name; meta.querySelector('p').textContent = item.status && item.status !== 'ready' ? item.status : item.kind + (item.duration ? ' · ' + item.duration.toFixed(1) + 's' : '');
    const chips = meta.querySelector('.assetAssignments'); names.forEach(name => { const s = document.createElement('span'); s.textContent = name; chips.appendChild(s); });
    meta.querySelector('.assetAssignBtn').addEventListener('click', e => { e.stopPropagation(); openAssetAssignments(item); });
    card.appendChild(thumb); card.appendChild(meta); grid.appendChild(card);
  }
  if(scroller)requestAnimationFrame(()=>{scroller.scrollTop=scrollTop;});
}

function openAssetAssignments(asset) {
  const body = $('#assetAssignBody'); body.innerHTML = '<h2></h2><p class="assignIntro">Choose every project that should contain this asset. Assignments used by a timeline, scene, or character are protected.</p><div class="assignmentChecks"></div><div class="assignActions"><button class="btn ghost assignCancel">Cancel</button><button class="btn primary assignSave">Save assignments</button></div>';
  body.querySelector('h2').textContent = asset.name; const assigned = new Set(asset.assignments.map(x => x.slug));
  const checks = body.querySelector('.assignmentChecks');
  for (const project of assetLibraryState.projects || []) {
    const label = document.createElement('label'); label.className = 'assignmentCheck';
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = project.slug; input.checked = assigned.has(project.slug);
    const text = document.createElement('span'); text.innerHTML = '<b></b><small></small>'; text.querySelector('b').textContent = project.name; text.querySelector('small').textContent = project.active ? 'Current project' : 'Project';
    label.appendChild(input); label.appendChild(text); checks.appendChild(label);
  }
  const close = () => { $('#assetAssignPanel').classList.remove('on'); $('#assetAssignPanel').setAttribute('aria-hidden','true'); };
  body.querySelector('.assignCancel').addEventListener('click', close);
  body.querySelector('.assignSave').addEventListener('click', async () => {
    const projects = [...checks.querySelectorAll('input:checked')].map(x => x.value);
    try { const out = await api('/api/assets/assign', {method:'POST',body:{asset_uid:asset.asset_uid,projects}}); close(); if (out.protected && out.protected.length) toast('Kept in use by ' + out.protected.join(', '), 'ok'); else toast('Project assignments updated', 'ok'); await refresh(); renderAssetCenter({force:true}); }
    catch (e) { toast(e.message,'err'); }
  });
  $('#assetAssignPanel').classList.add('on'); $('#assetAssignPanel').setAttribute('aria-hidden','false');
}
function kickAutoplay(root){
  $$('video[autoplay]', root).forEach(v => {
    v.muted = true; v.defaultMuted = true; v.playsInline = true;
    const p = v.play();
    if (p && p.catch) p.catch(() => {});
  });
}
function skillPreviewMarkup(skill,variant='card'){
  if(skill.custom)return variant==='detail'?'<div class="skillStylePreview customSkillArt">✦</div>':'✦';
  const id=esc(skill.id),controls=variant==='detail'?' controls':'';
  return '<span class="skillPreviewPair '+esc(variant)+'">'+
    '<video src="/assets/skill-previews/'+id+'.mp4" poster="/assets/skill-previews/'+id+'.jpg" muted loop autoplay playsinline'+controls+' preload="metadata"></video>'+
    '<video src="/assets/skill-previews/'+id+'-reels-2.mp4" poster="/assets/skill-previews/'+id+'-reels-2.jpg" muted loop autoplay playsinline'+controls+' preload="metadata"></video>'+
  '</span>';
}
function bindSkillPreviewFallback(root){
  $$('video[src*="/assets/skill-previews/"]',root).forEach(video=>video.addEventListener('error',()=>{
    // Built-in previews always have a deterministic poster. A transient
    // range-request failure must not delete that useful thumbnail and turn
    // the whole skill into a placeholder.
    if(video.getAttribute('poster')){video.removeAttribute('autoplay');video.preload='none';return;}
    const pair=video.closest('.skillPreviewPair');
    const host=pair||video.parentElement;
    video.remove();
    if(pair&&pair.querySelector('video')){pair.classList.add('single');return;}
    if(!host)return;host.classList.add('customSkillArt');host.textContent='✦';
  },{once:true}));
}
function renderSkillsCenter() {
  const grid = $('#skillsGrid'); if (!grid) return; grid.innerHTML = '';
  const notice=$('#skillCatalogNotice');
  if(notice){notice.hidden=!SKILL_CATALOG_ERRORS.length;notice.replaceChildren();if(SKILL_CATALOG_ERRORS.length){const message=document.createElement('span');message.textContent=SKILL_CATALOG_ERRORS.join(' ');const retry=document.createElement('button');retry.className='btn';retry.textContent='Retry';retry.addEventListener('click',async()=>{retry.disabled=true;await loadSkillCatalog(true);renderSkillsCenter();});notice.append(message,retry);}}
  const q = ($('#skillSearch').value || '').toLowerCase();
  const projectStyles=(state.project_style_skills||[]).map(s=>{const owner=s.project_name||state.name,scenes=s.scene_count??state.scenes.length,characters=s.character_count??state.characters.length,anchor=s.anchor_media_name||(s.anchor_media_id&&mediaById(s.anchor_media_id)||{}).name;return {...s,projectStyle:true,custom:true,project_name:owner,description:'Created for '+owner+' from '+scenes+' generated scene'+(scenes===1?'':'s')+', '+characters+' character lock'+(characters===1?'':'s')+(anchor?', anchored to '+anchor:'')+'.'};});
  const catalog = [...projectStyles,...customSkills(), ...SKILL_CATALOG];
  const skills = catalog.filter(s => {
    const group = s.projectStyle ? 'project-style' : (s.custom ? 'custom' : ((s.source || '').includes('community') ? 'community' : 'prompt'));
    const type = s.type || 'video';
    return (skillTypeFilter === 'all' || type === skillTypeFilter) && (skillFilter === 'all' || group === skillFilter) && (s.name + ' ' + s.description).toLowerCase().includes(q);
  });
  if (!skills.length) {
    grid.innerHTML = '<div class="gempty"><div class="glyph">✣</div><h2>' + (skillTypeFilter === 'music' ? 'Music skills are coming next' : 'No matching skills') + '</h2><p>' + (skillTypeFilter === 'music' ? 'MiniMax Music skills will appear here when the music model is added to Generate.' : 'Try another type, scope, or search.') + '</p></div>';
    return;
  }
  for (const s of skills) {
    const card = document.createElement('button'); card.className = 'hubCard'; card.innerHTML = '<div class="skillArt'+(s.custom?' customSkillArt':'')+'">'+skillPreviewMarkup(s,'card')+'</div><div class="hubMeta"><h3></h3><p></p><span class="hubBadge"></span></div>';
    bindSkillPreviewFallback(card); card.querySelector('h3').textContent = s.name; card.querySelector('p').textContent = s.description; card.querySelector('.hubBadge').textContent = s.projectStyle?'Custom · project style':(s.custom ? 'Custom · prompt skill' : 'OpenMagia · prompt skill');
    card.addEventListener('click', () => openSkillDetail(s)); grid.appendChild(card);
  }
  kickAutoplay(grid);
}
let skillDetailRequest=0;
async function openSkillDetail(s, origin='skills-menu') {
  const request=++skillDetailRequest,body=$('#skillDetailBody'),sheet=$('#skillDetail');
  const owner=s.project_name||state.name||'Untitled',anchor=s.anchor_media_name||(s.anchor_media_id&&mediaById(s.anchor_media_id)||{}).name||'Not recorded';
  const updated=s.updated?new Date(s.updated*1000).toLocaleString():'Not recorded';
  const context=s.projectStyle?'<div class="projectStyleContext"><div><span>Created for</span><strong>'+esc(owner)+'</strong></div><div><span>Continuity anchor</span><strong>'+esc(anchor)+'</strong></div><div><span>Evidence</span><strong>'+esc(String(s.scene_count??state.scenes.length))+' generated scenes · '+esc(String(s.character_count??state.characters.length))+' character locks</strong></div><div><span>Last updated</span><strong>'+esc(updated)+'</strong></div></div>':'';
  $('#skillDetailTitle').textContent=s.name;
  $('#skillDetailSubtitle').textContent=s.projectStyle?'Project-specific continuity created for '+owner:(s.custom?'Your custom skill':'OpenMagia skill');
  const detailPreview=skillPreviewMarkup(s,'detail');
  body.innerHTML='<div class="skillDetailScroll">'+detailPreview+(s.projectStyle?'<span class="scopeLabel">PROJECT STYLE · '+esc(owner)+'</span>':'')+'<p class="skillDetailDescription"></p>'+context+'<p class="pickerDetailNote">'+(s.projectStyle?'Loading continuity profile…':'Loading complete skill specification…')+'</p></div>';
  body.querySelector('.skillDetailDescription').textContent=s.description;bindSkillPreviewFallback(body);kickAutoplay(body);
  revealSideSheet(sheet,$('.skillSheetPanel',sheet));
  let specification='';
  if(s.projectStyle)specification=s.prompt||'';
  else if(s.custom) specification=s.specification||('# '+s.name+'\n\n'+s.description);
  else try{const [response,contract]=await Promise.all([fetch('/skills/openmagia/'+encodeURIComponent(s.id)+'/SKILL.md',{cache:'no-store'}),fetch('/skills/openmagia/references/h3-production-contract.md',{cache:'no-store'})]);if(!response.ok||!contract.ok)throw new Error('Skill specification unavailable');specification=(await response.text())+'\n\n---\n\n## Linked H3 production contract\n\n'+(await contract.text());}catch(error){specification='This skill specification could not be loaded. OpenMagia will not substitute a different workflow.\n\n'+error.message;}
  if(request!==skillDetailRequest)return;
  body.innerHTML = '<div class="skillDetailScroll">'+detailPreview+(s.projectStyle?'<span class="scopeLabel">PROJECT STYLE · '+esc(owner)+'</span>':'')+'<p class="skillDetailDescription"></p>'+context+'<h3>'+(s.projectStyle?'Continuity specification':'Complete skill specification')+'</h3><pre class="skillSpec"></pre></div><div class="skillSheetFoot">'+(s.projectStyle?'<button class="btn ghost danger" id="deleteProjectStyle">Delete</button>':'')+'<button class="btn primary" id="useSkillBtn">'+(s.projectStyle?'Use project style':'Use Skill')+'</button></div>';
  body.querySelector('.skillDetailDescription').textContent=s.description;body.querySelector('.skillSpec').textContent=specification;bindSkillPreviewFallback(body);kickAutoplay(body);
  body.querySelector('#useSkillBtn').addEventListener('click', async() => {
    if(s.projectStyle){const profile={name:s.name,prompt:s.prompt,skill_id:s.id,source:'continuity'};await api('/api/project',{method:'POST',body:{style_profile:profile,style_enabled:true}});state.style_profile=profile;state.style_enabled=true;state.base_prompt=s.prompt;closeSkillDetail();setHubView('editor');setInspectorTab('generate');renderGenerate();toast(s.name+' applied to new generations','ok');return;}
    s.specification=specification;activePromptSkill=s;closeSkillDetail();setHubView('editor');setInspectorTab('generate');renderActivePromptSkill();$('#genPrompt').focus();toast(s.name+' attached to the prompt','ok');
  });
  const del=body.querySelector('#deleteProjectStyle');if(del)del.addEventListener('click',async()=>{if(!confirm('Delete this project style? New generations will stop using it if it is active.'))return;await api('/api/project/styles/'+encodeURIComponent(s.id),{method:'DELETE'});state.project_style_skills=(state.project_style_skills||[]).filter(x=>x.id!==s.id);if((state.style_profile||{}).skill_id===s.id){state.style_profile={name:'No project style',prompt:'',skill_id:null,source:'custom'};state.base_prompt='';}closeSkillDetail();renderSkillsCenter();toast('Project style deleted','ok');});
}
function closeSkillDetail(){ skillDetailRequest++;sideSheetOpenToken++;$('#skillDetail').classList.remove('on'); $('#skillDetail').setAttribute('aria-hidden','true'); }

/* ---------------- render all ---------------- */
function renderAll() {
  renderHeader();
  renderGallery();
  renderTimeline();
  renderMedia();
  const active = document.activeElement;
  const editingInspector = active && $('#inspector').contains(active) && (['INPUT','TEXTAREA','SELECT'].includes(active.tagName) || active.isContentEditable);
  if (!editingInspector) renderInspector({preserveScroll:true});
  renderGenerate();
  renderCast();
  renderSideProjects();
  updateModelInstallProgress();
  const modelFingerprint=modelSettingsFingerprint(engine);
  if(hubView==='settings'&&modelFingerprint!==settingsModelFingerprint)renderSettings();
  updateDetachAudioTool();
  // Hub grids own their DOM lifecycle. The generation poll must not rebuild
  // Assets or it resets scroll anchoring and flashes every thumbnail.
  // Do not rebuild the Skills grid during the 1.5s generation poll: replacing
  // its video nodes makes autoplay previews flash and restart.
  // preview empty state
  const hasPreview = hasVisualTimelineContent();
  const empty = $('#previewEmpty');
  empty.style.display = hasPreview ? 'none' : 'flex';
  $('#preview').style.visibility = hasPreview ? 'visible' : 'hidden';
}
/* ---------------- events ---------------- */
/* ---------------- OpenMagia plugin host ---------------- */
const PLUGIN_PERMISSION_COPY={'project.read':['Read project','Read active project metadata.'],'project.write':['Change project','Update project settings through reviewed actions.'],'media.read':['Read media','Read media metadata, folders, and status.'],'media.write':['Change media','Create, rename, move, or remove media.'],'timeline.read':['Read timeline','Read tracks, clips, selection, and playhead.'],'timeline.write':['Change timeline','Add and edit tracks and clips.'],'generation.read':['Read generations','Read queued, running, completed, and failed scenes.'],'generation.create':['Create generations','Submit or cancel generation jobs.'],'generation.events':['Generation events','Receive queued, progress, completed, failed, and cancelled updates.'],'notifications.email':['Send email','Send through the configured SMTP account.'],'notifications.imessage':['Send iMessage','Ask macOS Messages to send to the configured recipient.'],'storage':['Plugin storage','Save this plugin’s settings locally.']};
function pluginAssetUrl(p,f){return p&&p[f]?'/api/plugins/'+encodeURIComponent(p.id)+'/assets/'+String(p[f]).split('/').map(encodeURIComponent).join('/'):'';}
async function loadPluginCatalog(){const r=await api('/api/plugins');pluginCatalog=r.plugins||[];return pluginCatalog;}
function closePluginManager(){const s=$('#pluginSheet');s.classList.remove('on');s.setAttribute('aria-hidden','true');}
async function openPluginManager(){const s=$('#pluginSheet');s.classList.add('on');s.setAttribute('aria-hidden','false');$('#pluginSheetBody').innerHTML='<div class="pluginEmpty"><h3>Loading plugins…</h3></div>';try{await loadPluginCatalog();renderPluginManager();}catch(e){toast(e.message,'err');}}
function pluginCardHtml(p){const cover=pluginAssetUrl(p,'cover');return '<article class="pluginCard" data-plugin="'+esc(p.id||'')+'">'+(cover?'<img class="pluginCover" src="'+esc(cover)+'" alt="">':'<div class="pluginCover"></div>')+'<div class="pluginCardBody"><div><h3>'+esc(p.name||p.id||'Missing plugin')+'</h3><p>'+esc(p.error||p.description||'')+'</p><div class="pluginMeta"><span>v'+esc(p.version||'—')+'</span><span>'+(p.enabled?'Enabled':'Disabled')+'</span>'+(p.missing?'<span>Missing files</span>':'')+'</div></div><div class="pluginCardActions">'+(!p.missing?'<button class="btn ghost" data-plugin-configure>Manage</button>'+(p.enabled?'<button class="btn primary" data-plugin-run>Open</button>':''):'')+'</div></div></article>';}
function renderPluginManager(){const body=$('#pluginSheetBody'),foot=$('#pluginSheetFoot');foot.innerHTML='';if(pluginTab==='store'){body.innerHTML='<div class="pluginEmpty"><div><h3>Plugin Store · Coming soon</h3><p>Browse reviewed community plugins in a future release. Developers can load unpacked plugins today.</p></div></div>';return;}if(pluginTab==='develop'){body.innerHTML='<div class="pluginLoadForm"><div><h3>Load unpacked plugin</h3><p>Enter a plugin folder or its <code>openmagia-plugin.json</code> path. Source stays in its independent repository.</p></div><label>Plugin path<input id="pluginPath" class="txt" placeholder="/absolute/path/to/plugin"></label><div class="pluginDevActions"><button id="pluginLogsBtn" class="btn ghost">View logs</button><button id="pluginLoadBtn" class="btn primary">Load plugin</button></div></div>';$('#pluginLoadBtn').addEventListener('click',async()=>{const path=$('#pluginPath').value.trim();if(!path)return toast('Enter a plugin folder or manifest path.','err');try{const loaded=await api('/api/plugins/load',{method:'POST',body:{path}});await loadPluginCatalog();pluginTab='installed';$$('[data-plugin-tab]').forEach(x=>x.classList.toggle('on',x.dataset.pluginTab==='installed'));renderPluginPermissionReview(loaded.id);}catch(e){toast(e.message,'err');}});$('#pluginLogsBtn').addEventListener('click',renderPluginLogs);return;}body.innerHTML=pluginCatalog.length?'<div class="pluginGrid">'+pluginCatalog.map(pluginCardHtml).join('')+'</div>':'<div class="pluginEmpty"><div><h3>No plugins loaded</h3><p>Open Development and load an unpacked plugin from its repository.</p></div></div>';$$('[data-plugin-configure]',body).forEach(b=>b.addEventListener('click',()=>renderPluginPermissionReview(b.closest('[data-plugin]').dataset.plugin)));$$('[data-plugin-run]',body).forEach(b=>b.addEventListener('click',()=>runPlugin(b.closest('[data-plugin]').dataset.plugin)));}
function renderPluginPermissionReview(id){const p=pluginCatalog.find(x=>x.id===id);if(!p)return renderPluginManager();const permissions=p.permissions||[],grants=new Set(p.grants||[]),body=$('#pluginSheetBody');body.innerHTML='<div class="pluginLoadForm"><div><button id="pluginReviewBack" class="sheetFolderBack">← Installed</button><h3>'+esc(p.name)+'</h3><p>'+esc(p.description)+'</p></div><div><h3>Permissions</h3><p>Approve only what this plugin needs. Disabled capabilities remain outside its sandbox.</p></div><div class="pluginPermissionList">'+permissions.map(permission=>{const copy=PLUGIN_PERMISSION_COPY[permission]||[permission,''];return '<label class="pluginPermission"><input type="checkbox" value="'+esc(permission)+'" '+(grants.has(permission)?'checked':'')+'><span><strong>'+esc(copy[0])+'</strong><small>'+esc(copy[1])+'</small></span></label>';}).join('')+'</div><div class="pluginDevActions"><button id="pluginRemove" class="btn danger">Remove</button><button id="pluginSavePermissions" class="btn primary">'+(p.enabled?'Save permissions':'Approve and enable')+'</button></div></div>';$('#pluginReviewBack').addEventListener('click',renderPluginManager);$('#pluginRemove').addEventListener('click',async()=>{if(!confirm('Remove this plugin from OpenMagia? Its source folder will not be deleted.'))return;await api('/api/plugins/'+encodeURIComponent(id),{method:'DELETE'});await loadPluginCatalog();renderPluginManager();syncPluginBackgrounds();});$('#pluginSavePermissions').addEventListener('click',async()=>{const selected=$$('.pluginPermission input:checked',body).map(i=>i.value);if(selected.length!==permissions.length)return toast('Approve every requested permission to enable this plugin.','err');try{await api('/api/plugins/'+encodeURIComponent(id),{method:'POST',body:{enabled:true,grants:selected}});await loadPluginCatalog();renderPluginManager();syncPluginBackgrounds();toast(p.name+' enabled','ok');}catch(e){toast(e.message,'err');}});}
async function renderPluginLogs(){const body=$('#pluginSheetBody');body.innerHTML='<div class="pluginLoadForm"><button id="pluginLogsBack" class="sheetFolderBack">← Development</button><h3>Plugin log</h3><pre class="pluginLogs">Loading…</pre></div>';$('#pluginLogsBack').addEventListener('click',renderPluginManager);try{const r=await api('/api/plugins/logs');$('.pluginLogs',body).textContent=(r.logs||[]).map(x=>new Date(x.time*1000).toLocaleString()+'  '+String(x.level||'info').toUpperCase()+'  '+x.pluginId+'\n'+x.message).join('\n\n')||'No plugin events yet.';}catch(e){$('.pluginLogs',body).textContent=e.message;}}
function closePluginRunner(){const r=$('#pluginRunner');r.classList.remove('on');r.setAttribute('aria-hidden','true');$('#pluginFrame').src='about:blank';activePlugin=null;}
function runPlugin(id){const p=pluginCatalog.find(x=>x.id===id&&x.enabled);if(!p)return;activePlugin=p;closePluginManager();$('#pluginRunnerTitle').textContent=p.name;$('#pluginRunnerSubtitle').textContent=p.description;$('#pluginFrame').src=pluginAssetUrl(p,'ui');const r=$('#pluginRunner');r.classList.add('on');r.setAttribute('aria-hidden','false');}
function bindPluginWindowDrag(){const handle=$('#pluginRunnerDrag'),win=$('.pluginWindow');let origin=null;handle.addEventListener('pointerdown',event=>{if(event.target.closest('button'))return;const box=win.getBoundingClientRect();origin={x:event.clientX,y:event.clientY,left:box.left,top:box.top};handle.setPointerCapture(event.pointerId);});handle.addEventListener('pointermove',event=>{if(!origin)return;const left=Math.max(0,Math.min(innerWidth-win.offsetWidth,origin.left+event.clientX-origin.x));const top=Math.max(0,Math.min(innerHeight-48,origin.top+event.clientY-origin.y));win.style.left=left+'px';win.style.top=top+'px';win.style.right='auto';});const stop=()=>{origin=null;};handle.addEventListener('pointerup',stop);handle.addEventListener('pointercancel',stop);}
function pluginWorkItems(){
  const scenes=(state&&state.scenes||[]).map(s=>({kind:'scene',id:s.id,name:s.name,status:s.status,error:s.error||null,progress:s.progress||null,generationType:s.generation_type||'video'}));
  const sheets=(state&&state.sheets||[]).map(s=>({kind:'sheet',id:s.id,name:s.name||'Character sheet',status:s.status,error:s.error||null,progress:s.progress||null,generationType:'sheet'}));
  return scenes.concat(sheets);
}
function pluginContext(){const items=pluginWorkItems();return {project:state?{slug:state.slug,name:state.name,aspect:state.aspect}:null,generations:items.filter(i=>i.kind==='scene'),sheets:items.filter(i=>i.kind==='sheet'),pending:items.filter(i=>i.status==='queued'||i.status==='running').length,selection:sel,playhead:playTime};}
// Each plugin must receive an event exactly once. A plugin that has a background
// frame also receives it when its window is open, so delivering to both sent
// every notification twice while the window was in front of the editor.
function postPluginEvent(event){
  const frames=[...pluginBackgroundFrames.values()];
  const covered=new Set(frames.map(f=>f.dataset.pluginId));
  if(activePlugin&&!covered.has(activePlugin.id)&&$('#pluginFrame').contentWindow)frames.push($('#pluginFrame'));
  for(const f of frames)f.contentWindow&&f.contentWindow.postMessage({source:'openmagia-host',type:'event',event},'*');
}
function postPluginInit(frame,p,mode){frame.contentWindow&&frame.contentWindow.postMessage({source:'openmagia-host',type:'init',mode,plugin:{id:p.id,name:p.name,version:p.version,permissions:p.grants||[],settings:p.settings||{}},context:pluginContext()},'*');}
function syncPluginBackgrounds(){const wanted=new Map(pluginCatalog.filter(p=>p.enabled&&(p.grants||[]).includes('generation.events')&&!p.missing).map(p=>[p.id,p]));for(const [id,f] of pluginBackgroundFrames)if(!wanted.has(id)){f.remove();pluginBackgroundFrames.delete(id);}for(const [id,p] of wanted)if(!pluginBackgroundFrames.has(id)){const f=document.createElement('iframe');f.hidden=true;f.sandbox='allow-scripts allow-forms';f.dataset.pluginId=id;f.src=pluginAssetUrl(p,'ui');f.addEventListener('load',()=>postPluginInit(f,p,'background'));document.body.appendChild(f);pluginBackgroundFrames.set(id,f);}}
// Accumulates outcomes between drains so "all done" can report a real tally,
// which is the one message a multi-scene storyboard actually wants.
let pluginRunTally={ready:0,error:0,cancelled:0,startedAt:null};
function publishPluginGenerationEvents(){
  if(!state)return;
  const current=new Map(pluginWorkItems().map(item=>[item.kind+':'+item.id,item]));
  if(!pluginGenerationSnapshot.size){
    pluginGenerationSnapshot=current;
    pluginRunTally.startedAt=current.size?[...current.values()].some(i=>i.status==='queued'||i.status==='running')?Date.now():null:null;
    return;
  }
  const busy=s=>s==='queued'||s==='running';
  for(const [key,item] of current){
    const before=pluginGenerationSnapshot.get(key);
    if(before&&before.status===item.status&&JSON.stringify(before.progress)===JSON.stringify(item.progress))continue;
    const name=before?before.status===item.status?'generation.updated':(before.status==='queued'&&item.status==='running')?'generation.started':(item.status==='ready'?'generation.completed':(item.status==='error'?'generation.failed':'generation.updated')):'generation.queued';
    if(item.status==='ready')pluginRunTally.ready++;else if(item.status==='error')pluginRunTally.error++;
    postPluginEvent({name,generation:item,previousStatus:before?before.status:null,project:{slug:state.slug,name:state.name}});
  }
  // Cancelling deletes the scene record on the server, so a job that vanishes
  // while pending is the only observable signal that it was cancelled.
  for(const [key,before] of pluginGenerationSnapshot){
    if(current.has(key)||!busy(before.status))continue;
    pluginRunTally.cancelled++;
    postPluginEvent({name:'generation.cancelled',generation:before,previousStatus:before.status,project:{slug:state.slug,name:state.name}});
  }
  const stillPending=[...current.values()].some(i=>busy(i.status));
  const hadWork=pluginRunTally.startedAt!==null||[...pluginGenerationSnapshot.values()].some(i=>busy(i.status));
  if(!stillPending&&hadWork&&(pluginRunTally.ready||pluginRunTally.error||pluginRunTally.cancelled)){
    postPluginEvent({name:'queue.drained',tally:{...pluginRunTally},elapsedSeconds:pluginRunTally.startedAt?Math.round((Date.now()-pluginRunTally.startedAt)/1000):null,project:{slug:state.slug,name:state.name}});
    pluginRunTally={ready:0,error:0,cancelled:0,startedAt:null};
  }else if(stillPending&&pluginRunTally.startedAt===null)pluginRunTally.startedAt=Date.now();
  pluginGenerationSnapshot=current;
}
window.addEventListener('message',async event=>{const rf=$('#pluginFrame'),background=[...pluginBackgroundFrames.values()],frame=event.source===rf.contentWindow?rf:background.find(x=>x.contentWindow===event.source);if(!frame)return;const p=frame===rf?activePlugin:pluginCatalog.find(x=>x.id===frame.dataset.pluginId),msg=event.data||{};if(!p||msg.source!=='openmagia-plugin')return;if(msg.type==='ready'){postPluginInit(frame,p,frame===rf?'ui':'background');return;}if(msg.type!=='request')return;const reply={source:'openmagia-host',type:'response',requestId:msg.requestId};try{const grants=new Set(p.grants||[]),params=msg.params||{};if(msg.method==='context.get'){if(!grants.has('generation.read')&&!grants.has('project.read'))throw new Error('Permission denied');reply.result=pluginContext();}else if(msg.method==='settings.get'){if(!grants.has('storage'))throw new Error('Permission denied');reply.result=p.settings||{};}else if(msg.method==='settings.set'){if(!grants.has('storage'))throw new Error('Permission denied');const updated=await api('/api/plugins/'+encodeURIComponent(p.id)+'/settings',{method:'POST',body:{settings:params.settings||{}}});p.settings=updated.settings||{};
            // A background frame only receives settings at init, so without this
            // a preference change would not take effect until the page reloaded —
            // a hidden frame keeps sending with stale rules.
            for(const bg of pluginBackgroundFrames.values())if(bg.dataset.pluginId===p.id)postPluginInit(bg,p,'background');
            reply.result=p.settings;}else if(msg.method==='notifications.send'){if(!grants.has('notifications.'+params.channel))throw new Error('Permission denied');reply.result=await api('/api/plugins/'+encodeURIComponent(p.id)+'/notify',{method:'POST',body:params});}else if(msg.method==='log'){reply.result=await api('/api/plugins/'+encodeURIComponent(p.id)+'/log',{method:'POST',body:params});}else if(msg.method==='ui.close'){closePluginRunner();reply.result={ok:true};}else throw new Error('Unknown plugin API method: '+msg.method);}catch(e){reply.error=e.message||String(e);}event.source.postMessage(reply,'*');});

function bindEvents() {
  $$('[data-inspector-tab]').forEach(b => b.addEventListener('click', () => setInspectorTab(b.dataset.inspectorTab)));
  bindPanelResize($('#mediaResize'), '--media-w', 180, 420, false);
  bindPanelResize($('#inspectorResize'), '--inspector-w', 280, 520, true);
  $('#playBtn').addEventListener('click', togglePlay);
  $('#toStart').addEventListener('click', () => seekTo(0));
  $('#toEnd').addEventListener('click', () => seekTo(timelineEnd()));
  $('#prevFrame').addEventListener('click', () => stepFrame(-1));
  $('#nextFrame').addEventListener('click', () => stepFrame(1));
  $('#loopBtn').addEventListener('click', () => { loop = !loop; $('#loopBtn').classList.toggle('on', loop); });
  $('#muteBtn').addEventListener('click', () => { globalMute = !globalMute; syncGlobalMuteButton(); if (!globalMute && playing) unlockAudio(); else updateAudio(playTime); });
  $('#pluginsBtn').addEventListener('click', openPluginManager);
  $('#pluginSheetClose').addEventListener('click', closePluginManager);$('#pluginSheetScrim').addEventListener('click',closePluginManager);
  $('#pluginRunnerClose').addEventListener('click',closePluginRunner);bindPluginWindowDrag();
  $$('[data-plugin-tab]').forEach(button=>button.addEventListener('click',()=>{pluginTab=button.dataset.pluginTab;$$('[data-plugin-tab]').forEach(x=>x.classList.toggle('on',x===button));renderPluginManager();}));
  $('#splitBtn').addEventListener('click', splitAtPlayhead);
  $('#timelineMagiaBtn').addEventListener('click',openTimelineMagia);
  $('#timelineMagiaClose').addEventListener('click',closeTimelineMagia);$('#timelineMagiaCancel').addEventListener('click',closeTimelineMagia);$('#timelineMagiaScrim').addEventListener('click',closeTimelineMagia);
  $('#timelineMagiaRemix').addEventListener('click',()=>requestTimelineMagiaPlan(true,true));$('#timelineMagiaApply').addEventListener('click',applyTimelineMagia);
  $('#timelineMagiaScope').addEventListener('change',scheduleTimelineMagiaPlan);$('#timelineMagiaDirection').addEventListener('input',scheduleTimelineMagiaPlan);
  $$('[data-timeline-magia-option]').forEach(input=>input.addEventListener('change',scheduleTimelineMagiaPlan));
  $('#undoBtn').addEventListener('click', undoTimeline);
  $('#freezeBtn').addEventListener('click', freezeAtPlayhead);
  $('#frameBtn').addEventListener('click', extractFrameAtPlayhead);
  $('#detachAudioBtn').addEventListener('click',()=>{const c=sel&&sel.type==='clip'?findClip(sel.id):null;if(c)detachAudio(c,true);});
  $('#zoomIn').addEventListener('click', () => zoomTimeline(1));
  $('#zoomOut').addEventListener('click', () => zoomTimeline(-1));
  $('#tlScroll').addEventListener('scroll',extendTimelineCanvas,{passive:true});
  // timeline resize + maximize
  const footer = $('#timeline');
  const resize = $('#tlResize');
  resize.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    const startY = e.clientY; const startH = footer.offsetHeight;
    resize.setPointerCapture && resize.setPointerCapture(e.pointerId);
    const move = (ev) => { const h = clamp(startH + (startY - ev.clientY), 120, window.innerHeight * 0.8); document.body.style.setProperty('--timeline-h', h + 'px'); footer.classList.remove('maximized'); };
    const up = () => {saveProjectLayout();window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  });
  const maxBtn = $('#tlMaxBtn');
  const toggleMax = () => {
    const on = !footer.classList.contains('maximized');
    footer.classList.toggle('maximized', on);
    maxBtn.classList.toggle('on', on);
    $('#tlMaxLabel').textContent = on ? 'Collapse' : 'Expand';
    saveProjectLayout();
  };
  maxBtn.addEventListener('click', toggleMax);
  $('#tlHead .t').addEventListener('dblclick', toggleMax);
  const addTrack = (kind) => api('/api/tracks', { method: 'POST', body: { kind } }).then(() => { toast('Added ' + kind + ' track', 'ok'); refresh(); }).catch(e => toast(e.message, 'err'));
  $('#addVTrack').addEventListener('click', () => addTrack('video'));
  $('#addATrack').addEventListener('click', () => addTrack('audio'));
  $('#exportBtn').addEventListener('click', doExport);
  $('#importBtn').addEventListener('click', () => $('#importFile').click());
  $('#importFile').addEventListener('change', (e) => { if (e.target.files.length) importFiles(Array.from(e.target.files)); e.target.value = ''; });
  // project name
  $('#projectName').addEventListener('change', (e) => api('/api/project', { method: 'POST', body: { name: e.target.value } }).catch(() => {}));
  $('#projectLibraryCrumb').addEventListener('click', () => setHubView('projects'));
  $('#logoHome').addEventListener('click', () => setHubView('projects'));
  // projects gallery
  $('#projectsBtn').addEventListener('click', openGallery);
  $('#galleryNew').addEventListener('click', newProject);
  $$('.sideNavItem').forEach(b => b.addEventListener('click', () => setHubView(b.dataset.view)));
  $('#projectSearch').addEventListener('input', renderGallery);
  const projectSortSelect=$('#projectSort');
  projectSortSelect.value=projectSort;
  projectSortSelect.addEventListener('change',e=>{projectSort=e.target.value;localStorage.setItem('omProjectSort',projectSort);renderGallery();});
  $('#assetSearch').addEventListener('input', renderAssetCenter);
  $('#assetImport').addEventListener('click', () => $('#importFile').click());
  $('#assetAssignPanel .detailScrim').addEventListener('click', () => { $('#assetAssignPanel').classList.remove('on'); $('#assetAssignPanel').setAttribute('aria-hidden','true'); });
  $('#assetAssignPanel .detailClose').addEventListener('click', () => { $('#assetAssignPanel').classList.remove('on'); $('#assetAssignPanel').setAttribute('aria-hidden','true'); });
  $$('[data-asset-filter]').forEach(b => b.addEventListener('click', () => { assetFilter = b.dataset.assetFilter; $$('[data-asset-filter]').forEach(x => x.classList.toggle('on', x === b)); renderAssetCenter(); }));
  $('#skillSearch').addEventListener('input', renderSkillsCenter);
  $('#addCustomSkill').addEventListener('click', openCustomSkillSheet);
  $$('[data-skill-filter]').forEach(b => b.addEventListener('click', () => { skillFilter = b.dataset.skillFilter; $$('[data-skill-filter]').forEach(x => x.classList.toggle('on', x === b)); renderSkillsCenter(); }));
  $$('[data-skill-type]').forEach(b => b.addEventListener('click', () => { skillTypeFilter = b.dataset.skillType; $$('[data-skill-type]').forEach(x => x.classList.toggle('on', x === b)); renderSkillsCenter(); }));
  $('#skillDetail .sheetScrim').addEventListener('click', closeSkillDetail);
  $('#skillDetail .detailClose').addEventListener('click', closeSkillDetail);
  $('#modelUninstallScrim').addEventListener('click',closeModelUninstall);$('#modelUninstallClose').addEventListener('click',closeModelUninstall);$('#modelUninstallCancel').addEventListener('click',closeModelUninstall);
  $('#modelUninstallConfirm').addEventListener('click',async()=>{if(!modelUninstallState)return;const button=$('#modelUninstallConfirm');button.disabled=true;try{const result=await api('/api/models/installations/'+modelUninstallState.installationId,{method:'DELETE'});toast('Removed '+result.removed,'ok');closeModelUninstall();renderSettings();}catch(error){button.disabled=false;toast(error.message,'err');}});
  $('#modelLicenseScrim').addEventListener('click',closeModelLicense);$('#modelLicenseClose').addEventListener('click',closeModelLicense);$('#modelLicenseCancel').addEventListener('click',closeModelLicense);
  $('#modelLicenseConfirm').addEventListener('click',async()=>{if(!modelLicenseState)return;const button=$('#modelLicenseConfirm'),component=modelLicenseState.component,name=modelLicenseState.name;button.disabled=true;try{await api('/api/models/install',{method:'POST',body:{component,accepted_license:true}});localStorage.setItem('openmagiaH3LicenseAccepted','true');toast(name+' download started','ok');closeModelLicense();await refresh();renderSettings();}catch(error){button.disabled=false;toast(error.message,'err');}});
  // generate
  $('#genBtn').addEventListener('click', generate);
  $('#genType').value='video';
  $('#genType').addEventListener('change',applyGenerationType);
  $('#storyboardClose').addEventListener('click',closeStoryboard);
  $('#storyboardPickerClose').addEventListener('click',closeStoryboardReferencePicker);$('#storyboardPickerCancel').addEventListener('click',closeStoryboardReferencePicker);$('#storyboardPickerScrim').addEventListener('click',closeStoryboardReferencePicker);$('#storyboardPickerApply').addEventListener('click',applyStoryboardReferencePicker);$('#storyboardPickerSearch').addEventListener('input',event=>{if(!storyboardPickerState)return;storyboardPickerState.q=event.target.value;renderStoryboardReferencePicker();});
  $('#storyboardAdd').addEventListener('click',()=>{const draft=ensureStoryboardDraft();draft.scenes.push(storyboardNewCard(draft.scenes.length));renderStoryboard();scheduleStoryboardSave();requestAnimationFrame(()=>{const row=$('#storyboardScenes');row.scrollTo({left:row.scrollWidth,behavior:'smooth'});});});
  $('#storyboardMagia').addEventListener('click',openMagia);$('#magiaClose').addEventListener('click',closeMagia);$('#magiaCancel').addEventListener('click',closeMagia);$('#magiaScrim').addEventListener('click',closeMagia);$('#magiaDuration').addEventListener('input',updateMagiaPlan);$('#magiaOptimize').addEventListener('change',updateMagiaPlan);$('#magiaAddCast').addEventListener('click',()=>openStoryboardReferencePicker(magiaCard,0,'cast'));$('#magiaAddReferences').addEventListener('click',()=>openStoryboardReferencePicker(magiaCard,0,'references'));$('#magiaAddSkill').addEventListener('click',()=>openStoryboardReferencePicker(magiaCard,0,'skill'));$('#magiaCreate').addEventListener('click',createMagiaStoryboard);
  $('#storyboardGenerate').addEventListener('click',generateStoryboard);
  $('#continuityReviewClose').addEventListener('click',closeContinuityReview);$('#continuityReviewCancel').addEventListener('click',closeContinuityReview);$('#continuityReviewScrim').addEventListener('click',closeContinuityReview);$('#continuityReviewConfirm').addEventListener('click',confirmContinuityReview);
  $('#storyboardStyle').addEventListener('input',scheduleStoryboardSave);
  ['#storyboardAspect','#storyboardResolution','#storyboardSteps','#storyboardSeed','#storyboardAudioMode','#storyboardAudioNotes'].forEach(id=>$(id).addEventListener('change',scheduleStoryboardSave));
  $('#storyboardQuality').addEventListener('change',()=>{$('#storyboardSteps').value=({balanced:20,high:30,reference:50})[$('#storyboardQuality').value]||30;scheduleStoryboardSave();});
  document.addEventListener('keydown',event=>{if(event.key!=='Escape')return;if($('#modelLicenseSheet').classList.contains('on'))closeModelLicense();else if($('#modelUninstallSheet').classList.contains('on'))closeModelUninstall();else if($('#timelineMagiaSheet').classList.contains('on'))closeTimelineMagia();else if($('#continuityReviewSheet').classList.contains('on'))closeContinuityReview();else if($('#storyboardReferencePicker').classList.contains('on'))closeStoryboardReferencePicker();else if($('#magiaSheet').classList.contains('on'))closeMagia();else if($('#storyboardWorkspace').classList.contains('on')&&!storyboardSubmitting)closeStoryboard();});
  $('#genAspect').addEventListener('change',e=>setProjectAspect(e.target.value));
  $('#projectStyleToggle').addEventListener('click',async()=>{
    if(!String((state.style_profile||{}).prompt||'').trim())return;
    const enabled=state.style_enabled===false;state.style_enabled=enabled;renderGenerate();
    try{await api('/api/project',{method:'POST',body:{style_enabled:enabled}});toast(enabled?'Project style enabled':'Project style disabled for Refine and generation','ok');}
    catch(error){state.style_enabled=!enabled;renderGenerate();toast(error.message,'err');}
  });
  $('#styleRefineBtn').addEventListener('click', () => openPromptSheet('style'));
  $('#modelPickerBtn').addEventListener('click', () => openComposerPicker('models'));
  $('#sourcePickerBtn').addEventListener('click', openSourceSheet);
  $('#castPickerBtn').addEventListener('click', () => openComposerPicker('cast'));
  $('#referencePickerBtn').addEventListener('click', openReferenceSheet);
  $('#referenceFile').addEventListener('change',e=>{const files=Array.from(e.target.files||[]);e.target.value='';if(files.length)uploadReferenceFiles(files);});
  $('#sourceClearBtn').addEventListener('click', () => { sourceSelection = null; renderSourceContext(); });
  $('#guideBtn').addEventListener('click', () => openPromptSheet('scene'));
  $('#skipQuestions').addEventListener('click', skipRefineQuestions);
  $('#sheetClose').addEventListener('click', closePromptSheet);
  $('#sheetScrim').addEventListener('click', closePromptSheet);
  $('#refineCancel').addEventListener('click', closePromptSheet);
  $('#applyPrompt').addEventListener('click', applyPromptTemplate);
  $('#promptSkillBtn').addEventListener('click', () => openComposerPicker('prompt'));
  $('#composerPickerClose').addEventListener('click', closeComposerPicker);
  $('#composerPickerScrim').addEventListener('click', closeComposerPicker);
  $('#composerPickerSearch').addEventListener('input', renderComposerPicker);
  $('#genStyle').addEventListener('change', () => {
    const prompt = $('#genStyle').value.trim();
    const profile = { name: prompt ? 'Custom project style' : 'No project style', prompt, skill_id: null, source: 'custom' };
    api('/api/project', { method: 'POST', body: { style_profile: profile } }).then(() => { state.style_profile = profile; renderGenerate(); }).catch(() => {});
  });
  $('#genStyle').addEventListener('pointerup',()=>setTimeout(saveProjectLayout,0));
  $('#genPrompt').addEventListener('pointerup',()=>setTimeout(saveProjectLayout,0));
  $('#addCharBtn').addEventListener('click', () => openCharacterSheet());
  const ms = $('#mediaSearch');
  if (ms) ms.addEventListener('input', () => { mediaQuery = ms.value; renderMedia(); });
  const sortWrap = $('#mediaSortWrap'), sortBtn = $('#mediaSortBtn'), sortMenu = $('#mediaSortMenu');
  const paintSortUi = () => {
    const lbl = $('#mediaSortLabel');
    if (lbl) lbl.textContent = (MEDIA_SORTS[mediaSort] || MEDIA_SORTS.newest).label;
    if (sortMenu) {
      $$('[data-msort]', sortMenu).forEach(b => b.classList.toggle('on', b.dataset.msort === mediaSort));
      $$('[data-mview]', sortMenu).forEach(b => b.classList.toggle('on', b.dataset.mview === mediaView));
    }
  };
  paintSortUi();
  if (sortBtn && sortMenu && sortWrap) {
    sortBtn.addEventListener('click', e => {
      e.stopPropagation();
      sortMenu.classList.toggle('open');
    });
    sortMenu.addEventListener('click', e => e.stopPropagation());
    document.addEventListener('click', () => sortMenu.classList.remove('open'));
    $$('[data-msort]', sortMenu).forEach(b => b.addEventListener('click', () => {
      mediaSort = b.dataset.msort;
      localStorage.setItem('omMediaSort', mediaSort);
      paintSortUi();
      renderMedia();
      sortMenu.classList.remove('open');
    }));
    $$('[data-mview]', sortMenu).forEach(b => b.addEventListener('click', () => {
      mediaView = b.dataset.mview;
      localStorage.setItem('omMediaView', mediaView);
      paintSortUi();
      renderMedia();
      sortMenu.classList.remove('open');
    }));
  }
  $('#composeCharBtn').addEventListener('click', () => openSheetComposer());
  $('#charCancel').addEventListener('click', closeCharModal);
  $('#charSave').addEventListener('click', saveChar);
  $('#charUpload').addEventListener('click', () => $('#charFile').click());
  $('#charFile').addEventListener('change', (e) => { if (e.target.files[0]) pickCharImage(e.target.files[0]); e.target.value = ''; });
  $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeCharModal(); });
  // window drop (import from desktop)
  window.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('Files')) e.preventDefault(); });
  window.addEventListener('drop', (e) => {
    if (e.dataTransfer.files && e.dataTransfer.files.length) { e.preventDefault(); importFiles(Array.from(e.dataTransfer.files)); }
  });
  // keyboard
  window.addEventListener('keydown', (e) => {
    const path = e.composedPath ? e.composedPath() : [e.target];
    const editing = path.some(x => x && x.nodeType === 1 && (['INPUT','TEXTAREA','SELECT'].includes(x.tagName) || x.isContentEditable)) ||
      (document.activeElement && (['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName) || document.activeElement.isContentEditable));
    if (editing) return;
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase()==='z') { e.preventDefault(); undoTimeline(); return; }
    if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); stepFrame(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); stepFrame(1); }
    else if (e.key === 'Home') { e.preventDefault(); seekTo(0); }
    else if (e.key === 'End') { e.preventDefault(); seekTo(timelineEnd()); }
    else if (e.key === 's' || e.key === 'S') { splitAtPlayhead(); }
    else if (e.key === '+' || e.key === '=') { zoomTimeline(1); }
    else if (e.key === '-' || e.key === '_') { zoomTimeline(-1); }
    else if (e.key === 'Delete' || e.key === 'Backspace') { if (sel && sel.type === 'clip') { const c = findClip(sel.id); if (c) deleteClip(c); } }
  });
}

/* ---------------- init ---------------- */
function init() {
  pool = document.createElement('div');
  pool.style.cssText = 'position:fixed;left:-99999px;top:0;width:2px;height:2px;overflow:hidden;opacity:0;pointer-events:none';
  document.body.appendChild(pool);
  document.body.style.setProperty('--lane-head-w',LANE_OFFSET+'px');
  document.body.classList.remove('nav-collapsed');
  const savedMedia = parseFloat(localStorage.getItem('openmagia--media-w')); if (isFinite(savedMedia)) document.body.style.setProperty('--media-w', clamp(savedMedia, 180, 420) + 'px');
  const savedInspector = parseFloat(localStorage.getItem('openmagia--inspector-w')); if (isFinite(savedInspector)) document.body.style.setProperty('--inspector-w', clamp(savedInspector, 280, 520) + 'px');
  setInspectorTab('inspect');
  hubView = 'projects';
  $('#gallery').classList.add('on');
  bindEvents();
  syncGlobalMuteButton();
  bindPromptHelp();
  bindInfoTooltips();
  updateSecsHint();
  $('#genFrames').addEventListener('input', updateSecsHint);
  $('#genQuality').addEventListener('change', () => {
    $('#genSteps').value = ({ balanced: 20, high: 30, reference: 50 })[$('#genQuality').value] || 30;
  });
  $('#guideDuration').addEventListener('input',()=>{const v=+$('#guideDuration').value;if(v>0){$('#genFrames').value=clamp(Math.round(v*24),8,360);updateSecsHint();}});
  $('#genPrompt').addEventListener('input', () => { const seconds = inferPromptSeconds($('#genPrompt').value); if (seconds) applyDurationSeconds(seconds); });
  loadPromptTemplates();
  loadPluginCatalog().then(syncPluginBackgrounds).catch(error=>console.warn('plugins',error));
  // optional deep-link to a time, e.g. ?t=2.5 (also used for automated checks)
  {
    const q = new URLSearchParams(location.search);
    if (q.has('t')) { const tt = parseFloat(q.get('t')); if (isFinite(tt)) playTime = clamp(tt, 0, 9999); }
  }
  refresh();
  rafId = requestAnimationFrame(tick);
  scheduleRefreshPoll();
  document.addEventListener('visibilitychange',handleVisibilityChange);
  window.addEventListener('pageshow',()=>{if(!document.hidden)refresh().finally(scheduleRefreshPoll);});
  window.addEventListener('pagehide',()=>{clearTimeout(refreshTimer);refreshTimer=0;releaseMediaDecoders();});
}
init();
