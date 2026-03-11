export function todayIsoLocal() {
  try {
    const date = new Date();
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

export function dateAddDays(iso, days) {
  try {
    const date = new Date(`${iso}T00:00:00Z`);
    if (Number.isNaN(date.getTime())) return iso;
    date.setUTCDate(date.getUTCDate() + Number(days || 0));
    return date.toISOString().slice(0, 10);
  } catch {
    return iso;
  }
}

export function dateDiffDays(aIso, bIso) {
  try {
    const left = new Date(`${aIso}T00:00:00Z`);
    const right = new Date(`${bIso}T00:00:00Z`);
    if (Number.isNaN(left.getTime()) || Number.isNaN(right.getTime())) return 0;
    return Math.round((right.getTime() - left.getTime()) / (24 * 3600 * 1000));
  } catch {
    return 0;
  }
}

export function weekdayZh(iso) {
  try {
    const date = new Date(`${iso}T00:00:00Z`);
    if (Number.isNaN(date.getTime())) return "";
    const weekdays = ["日", "一", "二", "三", "四", "五", "六"];
    return weekdays[date.getUTCDay()] || "";
  } catch {
    return "";
  }
}

export function mdLabel(iso) {
  try {
    const date = new Date(`${iso}T00:00:00Z`);
    if (Number.isNaN(date.getTime())) return iso;
    return `${String(date.getUTCMonth() + 1)}/${String(date.getUTCDate())}`;
  } catch {
    return iso;
  }
}

export function isoParts(iso) {
  const parts = String(iso || "").split("-");
  if (parts.length !== 3) return null;
  const y = Number(parts[0]);
  const m = Number(parts[1]);
  const d = Number(parts[2]);
  if (![y, m, d].every((value) => Number.isFinite(value))) return null;
  return { y, m, d };
}

export function periodStartIso(iso, granularity) {
  const normalized = String(granularity || "day").trim().toLowerCase();
  const source = String(iso || "").trim();
  if (!source) return source;
  if (normalized === "day") return source;

  if (normalized === "month") {
    const parts = isoParts(source);
    if (!parts) return source;
    return `${String(parts.y).padStart(4, "0")}-${String(parts.m).padStart(2, "0")}-01`;
  }

  if (normalized === "week") {
    try {
      const date = new Date(`${source}T00:00:00Z`);
      if (Number.isNaN(date.getTime())) return source;
      const mondayOffset = (date.getUTCDay() + 6) % 7;
      return dateAddDays(source, -mondayOffset);
    } catch {
      return source;
    }
  }

  return source;
}

export function periodAddIso(periodIso, granularity, delta) {
  const normalized = String(granularity || "day").trim().toLowerCase();
  const source = String(periodIso || "").trim();
  const step = Number(delta || 0);
  if (!source || !Number.isFinite(step) || step === 0) return source;
  if (normalized === "day") return dateAddDays(source, step);
  if (normalized === "week") return dateAddDays(source, step * 7);

  if (normalized === "month") {
    const parts = isoParts(source);
    if (!parts) return source;
    const totalMonths = parts.y * 12 + (parts.m - 1) + step;
    const year = Math.floor(totalMonths / 12);
    const month = (totalMonths % 12) + 1;
    return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-01`;
  }

  return source;
}

export function periodDiff(startIso, otherIso, granularity) {
  const normalized = String(granularity || "day").trim().toLowerCase();
  const start = String(startIso || "").trim();
  const other = String(otherIso || "").trim();
  if (!start || !other) return 0;
  if (normalized === "day") return dateDiffDays(start, other);
  if (normalized === "week") return Math.round(dateDiffDays(start, other) / 7);

  if (normalized === "month") {
    const startParts = isoParts(start);
    const otherParts = isoParts(other);
    if (!startParts || !otherParts) return 0;
    return (otherParts.y - startParts.y) * 12 + (otherParts.m - startParts.m);
  }

  return dateDiffDays(start, other);
}

export function isoMs(iso) {
  try {
    return new Date(`${iso}T00:00:00Z`).getTime();
  } catch {
    return NaN;
  }
}

export function periodClampIso(iso, startIso, endIso) {
  const value = isoMs(iso);
  const start = isoMs(startIso);
  const end = isoMs(endIso);
  if ([value, start, end].some((item) => Number.isNaN(item))) return iso;
  if (value < start) return startIso;
  if (value > end) return endIso;
  return iso;
}

export function periodEndIso(periodIso, granularity) {
  const normalized = String(granularity || "day").trim().toLowerCase();
  const source = String(periodIso || "").trim();
  if (!source) return source;
  if (normalized === "day") return source;
  if (normalized === "week") return dateAddDays(source, 6);
  if (normalized === "month") return dateAddDays(periodAddIso(source, "month", 1), -1);
  return source;
}

export function formatIsoRangeShort(startIso, endIso) {
  const start = String(startIso || "").trim();
  const end = String(endIso || "").trim();
  if (!start && !end) return "-";
  if (!start || !end) return mdLabel(start || end);

  const startParts = isoParts(start);
  const endParts = isoParts(end);
  if (startParts && endParts && startParts.y === endParts.y) {
    return `${startParts.m}/${startParts.d} ~ ${endParts.m}/${endParts.d}`;
  }
  if (startParts && endParts) {
    return `${String(startParts.y).slice(-2)}/${startParts.m}/${startParts.d} ~ ${String(endParts.y).slice(-2)}/${endParts.m}/${endParts.d}`;
  }
  return `${mdLabel(start)} ~ ${mdLabel(end)}`;
}
