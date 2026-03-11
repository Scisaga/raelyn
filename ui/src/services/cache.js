export function getCachedValue(cache, key) {
  try {
    if (!cache || typeof cache.get !== "function") return null;
    const item = cache.get(key);
    if (!item) return null;
    const expiresAt = Number(item.expiresAt || 0);
    if (expiresAt && Date.now() > expiresAt) {
      try {
        cache.delete(key);
      } catch {
        // ignore
      }
      return null;
    }
    return item.value;
  } catch {
    return null;
  }
}

export function setCachedValue(cache, key, value, ttlMs) {
  try {
    if (!cache || typeof cache.set !== "function") return;
    const ttl = Number(ttlMs || 0);
    const expiresAt = ttl > 0 ? Date.now() + ttl : 0;
    cache.set(key, { value, expiresAt });
  } catch {
    // ignore
  }
}
