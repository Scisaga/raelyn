export function mediaAvatarText(value) {
  const raw = String(value || "").trim() || "?";
  const withoutAt = raw.startsWith("@") ? raw.slice(1) : raw;
  const cleaned = withoutAt.replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, "");
  if (!cleaned) return "?";
  return cleaned.slice(0, 2).toUpperCase();
}

export function resolveMediaItemsByIds(index, ids) {
  const selectedIds = Array.isArray(ids) ? ids : [];
  if (!selectedIds.length) return [];
  const lookup = new Map((Array.isArray(index) ? index : []).map((media) => [String(media.id), media]));
  return selectedIds.map((id) => lookup.get(String(id))).filter(Boolean);
}

export function filterUnselectedMediaOptions({ index, selectedIds, query, displayName }) {
  const selected = new Set(Array.isArray(selectedIds) ? selectedIds : []);
  let items = Array.isArray(index) ? index : [];
  items = items.filter((media) => media && !selected.has(String(media.id)));

  const normalizedQuery = String(query || "").trim().toLowerCase();
  if (!normalizedQuery) return items.slice(0, 50);

  return items
    .filter((media) => {
      const name = String(displayName(media) || "").toLowerCase();
      const provider = String(media.provider || "").toLowerCase();
      return name.includes(normalizedQuery) || provider.includes(normalizedQuery);
    })
    .slice(0, 50);
}

export function addUniqueMediaId(list, mediaId) {
  const id = String(mediaId || "").trim();
  if (!id) return Array.isArray(list) ? list.slice() : [];
  const next = Array.isArray(list) ? list.slice() : [];
  if (!next.includes(id)) next.push(id);
  return next;
}

export function removeMediaId(list, mediaId) {
  const id = String(mediaId || "").trim();
  if (!id) return Array.isArray(list) ? list.slice() : [];
  return (Array.isArray(list) ? list : []).filter((item) => String(item) !== id);
}
