export function assetDirectModeEnabled(assetDelivery) {
  const delivery = assetDelivery && typeof assetDelivery === "object" ? assetDelivery : {};
  return String(delivery.mode || "").trim() === "direct" && delivery.presignEnabled === true;
}

export function assetPresignQueryValue(assetDelivery) {
  return assetDirectModeEnabled(assetDelivery) ? "true" : "false";
}
