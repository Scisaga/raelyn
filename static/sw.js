self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  // Chrome 在 Service Worker 下会发 only-if-cached 的内部请求，直接 fetch 会抛错。
  if (request.cache === "only-if-cached" && request.mode !== "same-origin") return;
  event.respondWith(fetch(request));
});
