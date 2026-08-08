self.addEventListener('install',event=>{self.skipWaiting();event.waitUntil(caches.keys().then(keys=>Promise.all(keys.map(k=>caches.delete(k)))))});
self.addEventListener('activate',event=>{event.waitUntil(Promise.all([caches.keys().then(keys=>Promise.all(keys.map(k=>caches.delete(k)))),self.registration.unregister(),self.clients.claim()]))});
self.addEventListener('fetch',()=>{});
