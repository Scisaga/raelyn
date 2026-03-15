export function wsUrl(path, token = "") {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = new URL(String(path || ""), `${proto}://${window.location.host}`);
  const resolvedToken = String(token || "").trim();
  if (resolvedToken && url.pathname.startsWith("/api/ws/")) url.searchParams.set("token", resolvedToken);
  return url.toString();
}
