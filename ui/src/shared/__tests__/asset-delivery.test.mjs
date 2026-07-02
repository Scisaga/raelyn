import assert from "node:assert/strict";
import test from "node:test";

import { createPlayerModule } from "../../app/modules/player.js";
import { assetDirectModeEnabled, assetPresignQueryValue } from "../asset-delivery.js";
import { createCommonViewMethods } from "../view-helpers.js";

function installWindow({ protocol = "https:", origin = "https://raelyn.local" } = {}) {
  globalThis.window = {
    location: {
      protocol,
      origin,
    },
  };
}

test("proxy 模式下 asset URL 固定走 /api/assets 代理", () => {
  installWindow();
  const methods = createCommonViewMethods();
  const ctx = {
    assetDelivery: {
      mode: "proxy",
      presignEnabled: false,
      proxyBasePath: "/api/assets",
    },
    assetDirectUrlUsable: methods.assetDirectUrlUsable,
  };
  const asset = {
    id: "asset 1",
    presigned_url: "https://s3.example/asset",
    download_presigned_url: "https://s3.example/download",
  };

  assert.equal(methods.assetContentUrl.call(ctx, asset), "/api/assets/asset%201/content");
  assert.equal(methods.assetDownloadUrl.call(ctx, asset), "/api/assets/asset%201/download");
  assert.equal(assetDirectModeEnabled(ctx.assetDelivery), false);
  assert.equal(assetPresignQueryValue(ctx.assetDelivery), "false");
});

test("direct 模式且 presign 启用时保留直连 URL", () => {
  installWindow();
  const methods = createCommonViewMethods();
  const ctx = {
    assetDelivery: {
      mode: "direct",
      presignEnabled: true,
      proxyBasePath: "/api/assets",
    },
    assetDirectUrlUsable: methods.assetDirectUrlUsable,
  };
  const asset = {
    id: "asset-2",
    presigned_url: "https://s3.example/asset",
    download_presigned_url: "https://s3.example/download",
  };

  assert.equal(methods.assetContentUrl.call(ctx, asset), "https://s3.example/asset");
  assert.equal(methods.assetDownloadUrl.call(ctx, asset), "https://s3.example/download");
  assert.equal(assetDirectModeEnabled(ctx.assetDelivery), true);
  assert.equal(assetPresignQueryValue(ctx.assetDelivery), "true");
});

test("视频详情页在代理模式下请求资产时不携带 presign=true", async () => {
  installWindow();
  const module = createPlayerModule();
  const requests = [];
  const ctx = {
    ...module,
    activeView: "video",
    playerPageVideoId: "video-1",
    playerVideo: { id: "video-1" },
    assetDelivery: {
      mode: "proxy",
      presignEnabled: false,
      proxyBasePath: "/api/assets",
    },
    assetContentUrl(asset) {
      return `/content/${asset.id}`;
    },
    assetDownloadUrl(asset) {
      return `/download/${asset.id}`;
    },
    async api(path) {
      requests.push(path);
      if (path.includes("/assets?")) {
        return [{ id: "asset-video", type: "video", format: "mp4", filename: "demo.mp4" }];
      }
      if (path.endsWith("/transcript")) return { ok: false };
      return { id: "video-1", title: "Demo" };
    },
  };

  await ctx.loadVideoPage({ videoId: "video-1" });

  assert.ok(requests.includes("/videos/video-1/assets?presign=false&download=true&localize_title=false"));
  assert.equal(ctx.playerVideoUrl, "/content/asset-video");
  assert.equal(ctx.playerVideoDownloadUrl, "/download/asset-video");
});
