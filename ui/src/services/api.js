export async function apiRequest(path, options, context = null) {
  const url = `/api${path}`;
  const resp =
    context && typeof context.fetchWithApiAuth === "function"
      ? await context.fetchWithApiAuth(url, options || {})
      : await fetch(url, options || {});

  if (resp.status === 401 && context && typeof context.handleApiUnauthorized === "function") {
    context.handleApiUnauthorized({});
  }

  if (!resp.ok) {
    const ct = resp.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      try {
        const payload = await resp.json();
        const detail = payload && typeof payload === "object" ? payload.detail : null;
        if (detail !== undefined && detail !== null) {
          const detailText = typeof detail === "string" ? detail : JSON.stringify(detail);
          throw new Error(`${resp.status}: ${detailText}`);
        }
        throw new Error(`${resp.status}: ${JSON.stringify(payload)}`);
      } catch (e) {
        const msg = e && e.message ? e.message : String(e);
        if (String(msg).startsWith(`${resp.status}:`)) throw new Error(String(msg));
        throw new Error(`${resp.status}: ${msg}`);
      }
    }
    const text = await resp.text();
    throw new Error(`${resp.status}: ${text}`);
  }
  const ct = resp.headers.get("content-type") || "";
  return ct.includes("application/json") ? resp.json() : resp.text();
}

export function isAbortError(e) {
  try {
    if (!e) return false;
    if (e.name === "AbortError") return true;
    const msg = e && e.message ? String(e.message) : String(e);
    return msg.toLowerCase().includes("abort");
  } catch {
    return false;
  }
}

export function abortCtrl(target, name) {
  try {
    const ctrl = target && name ? target[name] : null;
    if (ctrl && typeof ctrl.abort === "function") ctrl.abort();
  } catch {
    // ignore
  }
  try {
    if (target && name) target[name] = null;
  } catch {
    // ignore
  }
}

export function createApiMethods() {
  return {
    api(path, options) {
      return apiRequest(path, options, this);
    },

    _isAbortError(error) {
      return isAbortError(error);
    },

    _abortCtrl(name) {
      return abortCtrl(this, name);
    },
  };
}
