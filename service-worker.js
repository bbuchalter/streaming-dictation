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
    // Only this app's caches. CacheStorage is scoped to the origin, not to this
    // worker's scope, and bbuchalter.github.io is shared with every other Pages
    // project on the account — an unfiltered purge would delete their caches too,
    // and any worker of theirs doing the same would delete ours right back.
    await Promise.all(names.filter((n) => n !== CACHE && n.startsWith('dictation-'))
                           .map((n) => caches.delete(n)));
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
        // fetch() rejects only on network failure. A 404, a 503 and a captive
        // portal's 200 login page all arrive here as ordinary responses, and
        // Cache.put stores any of them as willingly as the real app — which
        // poisons the offline fallback for every later launch.
        if (!fresh.ok) {
          const cached = await caches.match('./');
          if (cached) return cached;   // a page that worked beats an error page
          return fresh;                // nothing better to offer
        }
        // Both clones are taken now, before this response is returned. Once
        // respondWith takes it the body is disturbed and clone() throws — which
        // is why the cache refresh below silently never ran (measured: a changed
        // index.html was served correctly and never reached the cache).
        const forCache = fresh.clone();
        // Venue wifi that answers 200 at our own URL passes every other test
        // there is, so check that this really is the app before keeping it.
        // CLIENT_EPOCH is the marker because check_login.py already asserts it
        // against the deployed index.html, so it cannot quietly disappear.
        const isApp = (await fresh.clone().text()).includes('CLIENT_EPOCH');
        if (isApp) {
          // waitUntil, not fire-and-forget: the worker can be terminated as soon as
          // respondWith settles, which would abandon the write mid-flight.
          e.waitUntil(caches.open(CACHE).then((c) => c.put('./', forCache)));
        }
        // Serve it either way. If a portal intercepted us the operator has to see
        // its login page to get past it — the rule is never to *keep* it.
        return fresh;
      } catch (_) {
        return (await caches.match('./')) || Response.error();   // offline fallback
      }
    })());
    return;
  }

  e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
