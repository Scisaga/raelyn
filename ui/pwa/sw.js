const PWA_CACHE = "raelyn-pwa-shell-v1";
const PWA_SHELL = ["/", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(PWA_CACHE);
      await cache.addAll(PWA_SHELL);
      await self.skipWaiting();
    })()
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((key) => key !== PWA_CACHE).map((key) => caches.delete(key)));
      await self.clients.claim();
    })()
  );
});

async function networkFirst(request, fallbackPath = "/") {
  try {
    const response = await fetch(request);
    const cache = await caches.open(PWA_CACHE);
    cache.put(request, response.clone()).catch(() => {});
    return response;
  } catch {
    const cache = await caches.open(PWA_CACHE);
    const fallback = await cache.match(request);
    if (fallback) return fallback;
    const shell = await cache.match(fallbackPath);
    if (shell) return shell;
    throw new Error("network unavailable");
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(PWA_CACHE);
  const cached = await cache.match(request);
  const networkPromise = fetch(request)
    .then((response) => {
      cache.put(request, response.clone()).catch(() => {});
      return response;
    })
    .catch(() => null);
  if (cached) {
    networkPromise.catch(() => {});
    return cached;
  }
  const network = await networkPromise;
  if (network) return network;
  return new Response("", { status: 504, statusText: "Gateway Timeout" });
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  if (request.cache === "only-if-cached" && request.mode !== "same-origin") return;

  const url = new URL(request.url);
  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, "/"));
    return;
  }

  if (
    url.origin === self.location.origin &&
    (url.pathname.startsWith("/static/") ||
      url.pathname === "/manifest.webmanifest" ||
      url.pathname === "/sw.js")
  ) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  event.respondWith(
    fetch(request).catch(async () => {
      const cache = await caches.open(PWA_CACHE);
      const cached = await cache.match(request);
      if (cached) return cached;
      return new Response("", { status: 504, statusText: "Gateway Timeout" });
    })
  );
});
