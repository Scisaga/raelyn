// 复用正在运行的任务和真实覆盖率，只允许读取业务服务。
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("../../../../", import.meta.url));
const upstream = process.env.RAELYN_REAL_UI_BASE_URL || "http://127.0.0.1:8000";
const token = process.env.API_BEARER_TOKEN || "";

test("真实运行中心直接加载覆盖率，任务列表不逐条读取视频详情", {
  skip: process.env.RAELYN_RUN_REAL_UI_TESTS === "1" ? false : "设置 RAELYN_RUN_REAL_UI_TESTS=1 并启动真实 API 后运行",
  timeout: 60_000,
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
  const browser = await chromium.launch({ headless: true, args: ["--no-proxy-server"] });
  t.after(() => browser.close());
  const context = await browser.newContext({
    baseURL: upstream,
    extraHTTPHeaders: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (token) await context.addCookies([{ name: "raelyn_api_token", value: token, url: upstream }]);
  const directory = await context.request.get("/api/domains?compact=true");
  const tasks = await context.request.get("/api/jobs?status_in=pending,running&limit=200");
  assert.equal(directory.status(), 200, "真实域目录读取失败");
  assert.equal(tasks.status(), 200, "真实任务读取失败");
  const domain = (await directory.json()).items?.[0];
  if (!domain || !(await tasks.json()).some((job) => job.params?.video_id)) {
    t.skip("需要观测域和包含视频的活跃任务；请先同步真实信源并启动视频处理任务");
    return;
  }
  const page = await context.newPage();
  const paths = [];
  const failures = [];
  const mutations = [];
  const timings = [];
  await page.route("**/*", (route) => {
    if (route.request().method() === "GET") return route.continue();
    mutations.push(new URL(route.request().url()).pathname);
    return route.abort();
  });
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) paths.push(url);
  });
  page.on("requestfinished", (request) => {
    const url = new URL(request.url());
    if (url.pathname.endsWith("/observation")) timings.push(Math.round(request.timing().responseEnd));
  });
  const started = performance.now();
  await page.goto(`/operations?domain_id=${encodeURIComponent(domain.id)}`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => {
    const root = document.querySelector("[x-data]");
    if (!root || !window.Alpine) return false;
    const model = window.Alpine.$data(root);
    return model.domainObservation?.coverage && model.jobListActive?.length > 0;
  }, null, { timeout: 45_000 });
  const elapsed = Math.round(performance.now() - started);
  const summary = await page.evaluate(() => {
    const model = window.Alpine.$data(document.querySelector("[x-data]"));
    const videoJob = model.jobListActive.find((job) => job.params?.video_id);
    return {
      domainId: model.domainObservation.domain.id,
      coverage: model.domainObservation.coverage,
      taskCount: model.jobListActive.length,
      title: model.jobVideoLabel(videoJob),
      expectedTitle: videoJob.video_title || videoJob.video_provider_video_id,
      url: model.jobVideoUrl(videoJob),
      expectedUrl: videoJob.video_url || "",
      playlistLabels: model.jobListActive.filter((job) => job.params?.playlist_id).map((job) => ({
        actual: model.jobPlaylistLabel(job),
        expected: model.domains.find((domain) => domain.id === job.params.playlist_id)?.name,
      })),
    };
  });
  assert.equal(summary.domainId, domain.id);
  assert.equal(Object.keys(summary.coverage).length, 4);
  assert.equal(summary.title, summary.expectedTitle);
  assert.equal(summary.url, summary.expectedUrl);
  for (const label of summary.playlistLabels) assert.equal(label.actual, label.expected);
  assert.equal(paths.filter((url) => /^\/api\/videos\/[^/]+$/.test(url.pathname)).length, 0,
    "列表已包含展示字段，首屏不应为每个任务请求视频详情");
  assert.equal(paths.filter((url) => /^\/api\/playlists\/[^/]+$/.test(url.pathname)).length, 0,
    "观测域名称已在目录中，列表不应重新读取包含统计的播放列表详情");
  assert.equal(paths.filter((url) => url.pathname === "/api/domains" && url.searchParams.get("compact") !== "true").length, 0,
    "覆盖率首屏只需 compact 域目录");
  assert.deepEqual(failures, []);
  assert.deepEqual(mutations, []);
  assert.ok(elapsed < 6000, `首屏仍耗时 ${elapsed}ms，覆盖率接口 ${timings.join(" / ")}ms`);
  t.diagnostic(`真实首屏覆盖率 ${elapsed}ms，覆盖率接口 ${timings.join(" / ")}ms，显示 ${summary.taskCount} 条任务，视频详情请求 0 次`);
});
