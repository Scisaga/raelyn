// 复用真实构建任务的快照产物，直接运行浏览器中的应用模型与 WebGL 渲染器。
// 运行方式见 ui/README.md；访问层只转发 GET，不允许测试写入业务数据库。
import assert from "node:assert/strict";
import { once } from "node:events";
import { readFile } from "node:fs/promises";
import { createServer, request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("../../../../", import.meta.url));
const upstream = process.env.RAELYN_REAL_UI_BASE_URL || "http://127.0.0.1:8000";
const token = process.env.API_BEARER_TOKEN || "";
const enabled = process.env.RAELYN_RUN_REAL_UI_TESTS === "1";

function upstreamRequest(path, onResponse) {
  const url = new URL(path, upstream);
  // 原生 HTTP 客户端直接连接目标，不读取进程代理变量。
  const request = (url.protocol === "https:" ? httpsRequest : httpRequest)(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    timeout: 30_000,
  }, onResponse);
  request.on("timeout", () => request.destroy(new Error("真实 API 请求超时")));
  request.end();
  return request;
}

function getJson(path) {
  return new Promise((accept, reject) => {
    upstreamRequest(path, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("error", reject);
      response.on("end", () => {
        try {
          accept({ status: response.statusCode, payload: JSON.parse(Buffer.concat(chunks).toString()) });
        } catch (error) { reject(error); }
      });
    }).on("error", reject);
  });
}

async function snapshotInputs() {
  const directory = await getJson("/api/domains?compact=true");
  const jobs = await getJson("/api/jobs?type=playlist.build_event_map_snapshot&status=succeeded&limit=50");
  assert.equal(directory.status, 200, "真实域目录读取失败");
  assert.equal(jobs.status, 200, "真实构建任务读取失败");
  for (const job of jobs.payload) {
    const snapshotId = job.result?.snapshot_id;
    const domain = directory.payload.items.find((item) => item.id === job.params?.playlist_id);
    if (!snapshotId || !domain?.snapshot?.id || snapshotId === domain.snapshot.id) continue;
    const base = `/api/playlists/${domain.id}/events/map/manifest?compact=true`;
    const old = await getJson(`${base}&snapshot_id=${encodeURIComponent(snapshotId)}`);
    if (old.status !== 409) continue;
    const current = await getJson(base);
    assert.equal(current.status, 200);
    if (current.payload.status !== "ready" || !current.payload.canonical_count) continue;
    const retainedJob = jobs.payload.find((item) => (
      item.params?.playlist_id === domain.id
      && item.result?.snapshot_id
      && item.result.snapshot_id !== snapshotId
      && item.result.snapshot_id !== current.payload.snapshot_id
    ));
    const retainedId = retainedJob?.result.snapshot_id;
    const retained = retainedId
      ? await getJson(`${base}&snapshot_id=${encodeURIComponent(retainedId)}`)
      : null;
    return {
      domain,
      expiredId: snapshotId,
      currentId: current.payload.snapshot_id,
      retainedId: retained?.status === 200 ? retainedId : "",
    };
  }
  return null;
}

const harness = `<!doctype html><html><body style="margin:0;background:black">
<div id="map" style="position:absolute;inset:0"></div>
<script type="module">
  import { createAppModel } from "/ui/src/app/model.js";
  window.model = createAppModel();
  window.model.$refs = { playlistEventMap: document.getElementById("map") };
  window.model.activeView = "field";
  window.model.playlistSubview = "analysis";
</script></body></html>`;

function readOnlyServer() {
  return createServer(async (request, response) => {
    if (request.method !== "GET") {
      response.writeHead(405).end();
      return;
    }
    const pathname = new URL(request.url, "http://localhost").pathname;
    if (pathname.startsWith("/api/")) {
      const forwarded = upstreamRequest(request.url, (result) => {
        response.writeHead(result.statusCode, { "Content-Type": result.headers["content-type"] || "application/json" });
        result.pipe(response);
        response.on("close", () => result.destroy());
      });
      forwarded.on("error", () => response.destroy());
      request.on("aborted", () => forwarded.destroy());
      return;
    }
    if (pathname === "/") {
      response.writeHead(200, { "Content-Type": "text/html" }).end(harness);
      return;
    }
    if (pathname !== "/field" && !pathname.startsWith("/ui/") && !pathname.startsWith("/static/")) {
      response.writeHead(404).end();
      return;
    }
    try {
      const body = await readFile(resolve(repo, pathname === "/field" ? "static/index.html" : pathname.slice(1)));
      const contentType = pathname === "/field" ? "text/html"
        : pathname.endsWith(".js") ? "text/javascript"
          : pathname.endsWith(".css") ? "text/css" : "application/octet-stream";
      response.writeHead(200, { "Content-Type": contentType }).end(body);
    } catch (error) {
      if (error.code !== "ENOENT") { response.destroy(error); return; }
      response.writeHead(404).end();
    }
  });
}

test("真实旧快照缓存不会阻止 current 首屏，历史导航仍严格固定版本", {
  skip: enabled ? false : "设置 RAELYN_RUN_REAL_UI_TESTS=1 并启动真实 API 后运行",
  timeout: 120_000,
}, async (t) => {
  const require = createRequire(import.meta.url);
  let chromium;
  try {
    ({ chromium } = require(require.resolve("playwright", { paths: [resolve(repo, "scripts/record")] })));
  } catch (error) {
    if (error.code !== "MODULE_NOT_FOUND") throw error;
    t.skip("请先运行 npm ci --prefix scripts/record，并安装 Playwright Chromium");
    return;
  }
  const inputs = await snapshotInputs();
  if (!inputs) {
    t.skip("缺少真实 ready 星域及已不可用的构建产物；请先完成事件抽取并生成至少三版星域，等待旧版回收");
    return;
  }
  const server = readOnlyServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => { server.closeAllConnections(); server.close(); });
  const browser = await chromium.launch({ headless: true, args: ["--no-proxy-server", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
  const paths = [];
  const failures = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.includes("/events/map/")) paths.push(url);
  });
  page.on("response", (response) => {
    if (response.url().includes("/events/map/") && response.status() >= 400) failures.push(response.status());
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(() => Boolean(window.model));

  await t.test("目录和状态缓存指向不可用旧版时，首请求直接取得 current 并完成真实场景", async () => {
    const result = await page.evaluate(async ({ domain, expiredId }) => {
      const model = window.model;
      model.selectedPlaylistId = domain.id;
      model.playlistPageId = domain.id;
      model.domains = [{ ...domain, snapshot: { ...domain.snapshot, id: expiredId } }];
      model.playlistEventMapStatus = { snapshot_id: expiredId, status: "ready" };
      const loaded = await model.playlistEventMapLoadView({ schedulePolling: false });
      await model.playlistEventMapWaitForCompleteScene({ includeMetadata: true });
      return { loaded, count: model.playlistEventMapScene.count, error: model.playlistEventMapStatusError, polling: Boolean(model.playlistEventMapPollTimer) };
    }, inputs);
    assert.equal(paths[0].pathname.endsWith("/manifest"), true);
    assert.equal(paths[0].searchParams.get("compact"), "true");
    assert.equal(paths[0].searchParams.has("snapshot_id"), false);
    assert.equal(result.loaded.confirmed, true);
    assert.equal(result.loaded.snapshotId, inputs.currentId);
    assert.ok(result.count > 0);
    assert.equal(result.error, "");
    assert.equal(result.polling, false);
    assert.deepEqual(failures, []);
    assert.ok(paths.some((url) => url.pathname.endsWith("/scene") && url.searchParams.has("preview_limit")));
    assert.ok(paths.some((url) => url.pathname.endsWith("/scene") && !url.searchParams.has("preview_limit")));
    assert.ok(paths.some((url) => url.pathname.endsWith("/manifest") && url.searchParams.get("include_coverage") === "false"));
    assert.ok(paths.slice(1).every((url) => url.searchParams.get("snapshot_id") === result.loaded.snapshotId));
  });

  await t.test("forceLatest 在历史参数仍存在时也明确请求 current", async () => {
    const start = paths.length;
    const result = await page.evaluate(async (expiredId) => {
      window.model.fieldRequestedSnapshotId = expiredId;
      return window.model.playlistEventMapLoadView({ forceLatest: true, schedulePolling: false });
    }, inputs.expiredId);
    assert.equal(paths[start].searchParams.has("snapshot_id"), false);
    assert.equal(result.snapshotId, inputs.currentId);
  });

  await t.test("不可用的显式历史版本仍报 409，空画布与徽标显示读取失败", async () => {
    const start = paths.length;
    const result = await page.evaluate(async (expiredId) => {
      const model = window.model;
      model.playlistEventMapReset();
      model.fieldRequestedSnapshotId = expiredId;
      const loaded = await model.playlistEventMapLoadView({ schedulePolling: false });
      return { loaded, error: model.playlistEventMapStatusError, empty: model.playlistEventMapEmptyLabel(), badge: model.playlistEventMapStatusBadgeLabel(), hint: model.playlistEventMapStatusBadgeHint() };
    }, inputs.expiredId);
    assert.equal(paths[start].searchParams.get("snapshot_id"), inputs.expiredId);
    assert.match(result.error, /^409: requested event map snapshot is not available$/);
    assert.equal(result.empty, result.error);
    assert.equal(result.badge, "星域读取失败");
    assert.equal(result.hint, result.error);
    assert.equal(result.loaded, undefined);
  });

  await t.test("保留的历史版本仍按指定 ID 读取", { skip: inputs.retainedId ? false : "近期真实构建任务中没有仍保留的上一版 ready 产物" }, async () => {
    const manifest = await page.evaluate(async (snapshotId) => {
      window.model.fieldRequestedSnapshotId = snapshotId;
      return window.model.playlistEventMapLoadManifest({ compact: true });
    }, inputs.retainedId);
    assert.equal(manifest.snapshot_id, inputs.retainedId);
    assert.equal(manifest.is_current, false);
  });

  await t.test("构建后的完整星域页面能够加载真实点集且首请求不固定旧版本", async () => {
    const smoke = await browser.newPage({ viewport: { width: 1000, height: 700 } });
    const origin = `http://127.0.0.1:${server.address().port}`;
    if (token) await smoke.context().addCookies([{ name: "raelyn_api_token", value: token, url: origin }]);
    await smoke.addInitScript(() => sessionStorage.setItem("raelyn.ui.startupGateSeen", "1"));
    const manifests = [];
    smoke.on("response", (response) => {
      const url = new URL(response.url());
      if (url.pathname.endsWith("/events/map/manifest")) manifests.push({ url, status: response.status() });
    });
    await smoke.goto(`${origin}/field?domain_id=${encodeURIComponent(inputs.domain.id)}`);
    await smoke.waitForFunction(() => {
      const model = window.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
      return model?._playlistEventMapSceneComplete && model?._playlistEventMapMetadataComplete;
    }, null, { timeout: 50_000 });
    const result = await smoke.evaluate(() => {
      const model = window.Alpine.$data(document.querySelector('[x-data="appShell()"]'));
      model.playlistEventMapStopPolling();
      return {
        count: model.playlistEventMapScene.count,
        error: model.playlistEventMapError || model.playlistEventMapStatusError,
        canvasCount: document.querySelectorAll('[x-ref="playlistEventMap"] canvas').length,
      };
    });
    assert.ok(result.count > 0);
    assert.equal(result.error, "");
    assert.equal(result.canvasCount, 1);
    assert.equal(manifests[0].url.searchParams.has("snapshot_id"), false);
    assert.ok(manifests.every((item) => item.status === 200));
    await smoke.close();
  });
});
