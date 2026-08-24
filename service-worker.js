// Bump CACHE in lockstep with CLIENT_EPOCH in index.html.
const CACHE  = 'dictation-e2';
// Relative, so they resolve against this worker's scope (/streaming-dictation/).
// The previous version used root-absolute paths, which 404'd and disabled the
// whole worker. manifest.json already gets this right.
const ASSETS = ['./', './index.html', './manifest.json', './icon-192.png', './icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    // Individually rather than addAll: one missing asset must degrade offline
    // coverage, not fail the install and disable the worker entirely.
    await Promise.allSettled(ASSETS.map((u) => c.add(new Request(u, { cache: 'reload' }))));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
    // Take control of already-open windows. For a standalone PWA, waiting for
    // every window to close could mean waiting forever.
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (new URL(req.url).origin !== self.location.origin) return;  // never touch modal.run

  if (req.mode === 'navigate') {
    e.respondWith((async () => {
      try {
        // cache:'reload' bypasses the 600s GitHub Pages HTTP cache, so a
        // controlled client is never stale.
        const fresh = await fetch(req.url, { cache: 'reload' });
        // waitUntil, not fire-and-forget: the worker can be terminated as soon as
        // respondWith settles, which would abandon the write mid-flight.
        e.waitUntil(caches.open(CACHE).then((c) => c.put('./', fresh.clone())));
        return fresh;
      } catch (_) {
        return (await caches.match('./')) || Response.error();   // offline fallback
      }
    })());
    return;
  }

  e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
