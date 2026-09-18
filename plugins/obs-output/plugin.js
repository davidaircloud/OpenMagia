// Standalone sandbox bridge: no network services, credentials or host scripts.
let sequence=0;
const pending=new Map(),button=document.querySelector('#open'),status=document.querySelector('#status');
window.addEventListener('message',event=>{
  if(event.source!==parent||event.data?.source!=='openmagia-host')return;
  const message=event.data;
  if(message.type==='init'){
    button.disabled=!(message.plugin.permissions||[]).includes('output.open');
    status.textContent=button.disabled?'Enable the Open program output permission in Plugins.':'Ready to open your live picture.';
  }
  if(message.type==='response'&&pending.has(message.requestId)){
    pending.delete(message.requestId);button.disabled=false;
    status.textContent=message.error||'Preview window open. Select Preview in OBS, then press Play in the editor.';
  }
});
button.addEventListener('click',()=>{
  const requestId='obs-output-'+(++sequence);pending.set(requestId,true);button.disabled=true;
  parent.postMessage({source:'openmagia-plugin',type:'request',requestId,method:'output.open',params:{}},'*');
  setTimeout(()=>{if(pending.delete(requestId)){button.disabled=false;status.textContent='No response. Reopen the plugin, or click Output in the playback toolbar.';}},10000);
});
parent.postMessage({source:'openmagia-plugin',type:'ready'},'*');
