export function mergeModelSegments(...segments) {
  const model = {};
  const owners = new Map();

  for (const segment of segments) {
    const name = segment && segment.name ? String(segment.name) : "unknown";
    const value = segment && segment.value && typeof segment.value === "object" ? segment.value : {};

    for (const [key, entry] of Object.entries(value)) {
      if (owners.has(key)) {
        throw new Error(`[ui] duplicate app model key "${key}" from "${name}" (already defined by "${owners.get(key)}")`);
      }
      owners.set(key, name);
      model[key] = entry;
    }
  }

  return model;
}
