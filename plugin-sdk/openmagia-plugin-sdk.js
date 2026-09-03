/** OpenMagia Plugin SDK v1 — dependency-free iframe bridge. */
export function createOpenMagiaPlugin() {
  let sequence = 0, init = null;
  const pending = new Map(), listeners = new Set();
  const ready = new Promise(resolve => { init = resolve; });
  window.addEventListener('message', event => {
    const message = event.data || {};
    if (message.source !== 'openmagia-host') return;
    if (message.type === 'init') init(message);
    if (message.type === 'event') listeners.forEach(listener => listener(message.event));
    if (message.type === 'response') {
      const request = pending.get(message.requestId); if (!request) return;
      pending.delete(message.requestId);
      message.error ? request.reject(new Error(message.error)) : request.resolve(message.result);
    }
  });
  const request = (method, params = {}) => new Promise((resolve, reject) => {
    const requestId = `openmagia-${++sequence}`;
    pending.set(requestId, {resolve, reject});
    parent.postMessage({source:'openmagia-plugin', type:'request', requestId, method, params}, '*');
  });
  parent.postMessage({source:'openmagia-plugin', type:'ready'}, '*');
  return {
    ready,
    context: {get: () => request('context.get')},
    storage: {get: () => request('settings.get'), set: settings => request('settings.set', {settings})},
    notifications: {send: (channel, title, message) => request('notifications.send', {channel, title, message})},
    log: (level, message, detail = {}) => request('log', {level, message, detail}),
    close: () => request('ui.close'),
    onGeneration(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
}
