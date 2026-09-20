// Raelyn 产品导览录制配置 —— 分镜、画面字幕、配音稿、镜头转场与配乐的唯一真源。
//
// 所有时间单位为秒。改分镜只改这个文件；record-tour.mjs / make-subtitles.mjs /
// make-narration.mjs / compose-tour.mjs 都从这里读取，保证视频与文案时间线永远对齐。

export const output = {
  // 原始界面录屏与最终输出都按 1440×960 准备。
  viewport: { width: 1440, height: 960 },
  fps: 30,
  // 无头环境按帧主动采样；最终 1.5× 后约 12 个独立画面/秒，与 README GIF 一致。
  captureFps: 8,
  // 操作按 1.5 倍时长录制，最终组装时再以 1.5 倍速播放。
  playbackRate: 1.5,
  // 最终成片尺寸。
  finalWidth: 1440,
  finalHeight: 960,
  crf: 20,
  preset: "slow",
};

export const target = {
  baseUrl: process.env.RAELYN_BASE_URL || "http://127.0.0.1:8000",
  // 录制用的观测域：数据最完整的一个。
  domainId: "a811f131-365e-44fe-8872-983a280299c7",
  domainName: "你需要关注的财经资讯",
  // 当前 ready 快照（283,719 真实事件 / 401 主题 / 19,359 故事）。
  snapshotId: "c1e5f7d2-bf52-44d0-a734-88b6ca97e4e1",
};

// 分镜中引用的真实对象。全部来自 target.snapshot，均落在默认 12 个月观察窗内，
// 主题选用大宗商品而非地缘政治，避免公开演示片带上政治指向。
export const content = {
  // 一级星域（level 0）：Gold · 黄金 · commodities。
  topicL0: "276dda5e-9167-5ab2-b9a3-f04d17585885",
  // 二级主题（level 1）：同族下钻。
  topicL1: "5e6544a5-c4d0-52fe-96d5-4e4781051124",
  // 星点：白银价格突破 66 美元（2025-12-17，4 条来源记录保守归并）。
  canonical: "638f5aae-3d49-5e0b-a3cd-36cc8cc786cd",
  // 故事：国际金价 23 个真实事件，2026-03-31 → 2026-09-14，established / q=0.899。
  storyId: "43bfc07c-f0d9-50ba-8e7d-7418deccce26",
  storyIdentityId: "5ae872f2-7ae8-5718-94bf-630507775756",
  // 播放列表：该日 90 条可播放记录 + 一篇带 24 条结构化引用的简报。
  playlistDate: "2026-09-10",
  playlistDatePrev: "2026-09-09",
  // 播放镜头固定选择 Reuters，避免默认第一条落到“钱线百分百”。
  playlistVideoId: "ce96159f-4d49-40bf-aaaa-8cb690ec989f",
  // 后半段切换到 CME Group 的 WTI Crude Oil 视频，并从 12 秒处开始播放。
  playlistSecondVideoId: "48dacf65-bac4-44e0-9eb6-621f137a6ddb",
  // 观察窗：显式给定，避免录制当天与日后重录出现不同的默认窗口。
  windowStart: "2025-09-20",
  windowEnd: "2026-09-20",
};

export const transition = {
  // 镜头之间的交叉溶解时长；星域内部相邻镜头设 0，让语义缩放自己承担。
  duration: 0.3,
  kind: "fade",
};

export const music = {
  // 不随仓库分发任何录音：作曲是公有领域，录音未必是。
  // 见 README.md「配乐」一节，把文件放到这里再录。
  file: process.env.RAELYN_TOUR_MUSIC || "assets/music/tour-bed.flac",
  // 音乐起点（秒）：从推荐录音里裁出最贴合 60 秒结构的一段。
  startAt: Number(process.env.RAELYN_TOUR_MUSIC_START || 0),
  gainDb: -14,
  fadeInSeconds: 1.2,
  fadeOutSeconds: 3.0,
};

export const subtitles = {
  // 烧进画面：GIF 无音轨，README 内嵌视频静音自动播放。
  fontName: "Noto Sans CJK SC",
  // 英文版沿用同一字族：系统未必装了独立的 Noto Sans，而 CJK 版自带的拉丁
  // 字形与中文版完全一致，两版成片看起来是同一部片子。
  fontNameEn: "Noto Sans CJK SC",
  // 直接生成带 PlayRes 的 ASS，所以这里就是真实像素高度。
  // 实测 libass 下中文字宽约 0.66×fontSize：48 → 约 32px/字，22 字约占画面宽一半，
  // 视频缩到 README 正文栏（约 830px）时仍有约 18px，可读。
  fontSize: 48,
  marginV: 56,
  // 左右安全边距，强制长句在画面内换行而不是顶到边缘。
  marginH: 140,
  // 单条字幕最短停留，低于此值会在构建时报警。
  minHoldSeconds: 1.2,
};

// ---------------------------------------------------------------------------
// 分镜
//
// url      相对路径 + 查询参数；产品的深链接契约就是录制契约。
// settle   进入后、开始抓帧前的稳定等待（加载、首帧、标签收清都在这里发生，
//          不进入成片，这就是「拼接过滤加载等待」）。
// duration 倍速处理后的成片时长；实际录制时长为 duration × playbackRate。
// act      归属叙事幕，仅用于清单分组，不插入额外画面。
// joinPrev 与上一镜头之间的转场时长，null 表示用 transition.duration。
// perform  抓帧期间执行的必要点击或滚动（可选），拿到 { page, ui, cursor, sleep }。
// caption  { at, zh, en, hold } 相对本镜头起点的字幕。
// narration { at, zh, hold } 相对本镜头起点的中文配音演讲稿；不与画面字幕共用文案。
// ---------------------------------------------------------------------------
export const shots = [
  {
    id: "field-timeline",
    act: "act1",
    url: (t, c) => `/?domain_id=${t.domainId}&snapshot_id=${t.snapshotId}&window_start=${c.windowStart}&window_end=${c.windowEnd}`,
    settle: 5.0,
    // 原片前 12 秒推进时间轴，后 6 秒展示 24H/本周卡片并旋转星域。
    // 待审片只把前半段压成 2×，因此成片前后两部分各占约 50%。
    sourceDuration: 18,
    timelineSourceDuration: 12,
    rotationDuration: 6,
    rotationCaptureFps: 20,
    weekSwitchAt: 1.5,
    duration: 12,
    reviewSegments: [
      { start: 0, end: 12, rate: 2 },
      { start: 12, rate: 1 },
    ],
    joinPrev: 0,
    async ready({ page }) {
      await page.waitForFunction(() => {
        const app = globalThis.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
        return app
          && !app.playlistEventMapHighlightsLoading
          && app.playlistEventMapHighlightAvailable("today")
          && app.playlistEventMapHighlightAvailable("week")
          && !!app.playlistEventMapTodayHighlights
          && !!app.playlistEventMapWeekHighlights;
      }, null, { timeout: 30000 });
    },
    // 录制器沿用产品自身的窗口计算，但逐帧推进状态，不等待 wall-clock 定时器。
    selectors: ["timelinePlay"],
    captions: [
      { at: 0.3, hold: 5.1, zh: "Raelyn：你的私人语义观测站", en: "Raelyn: your private semantic observatory" },
      { at: 6.0, hold: 5.5, zh: "把视频事实凝结成可回放的事件星域", en: "Facts from video become a replayable event starfield." },
    ],
    narration: [
      { at: 0.3, hold: 10.8, zh: "Raelyn 是什么？一座属于你的私人语义观测站。它把公开视频里的事实，变成可回放、可追踪的事件世界。" },
    ],
  },
  {
    id: "field-topic",
    act: "act1",
    url: (t, c) => `/?domain_id=${t.domainId}&snapshot_id=${t.snapshotId}&window_start=${c.windowStart}&window_end=${c.windowEnd}`,
    settle: 5.5,
    duration: 7.0,
    joinPrev: 0.3,
    captions: [
      { at: 0.2, hold: 3.1, zh: "语义邻近，让海量事件形成结构", en: "Semantic proximity gives structure to countless events." },
      { at: 3.6, hold: 3.1, zh: "从星群抵达事件，也回到来源与证据", en: "Drill from a star cluster to an event and its evidence." },
    ],
    narration: [
      { at: 0.2, hold: 6.2, zh: "信息不再只是列表。你可以从全局结构，一路抵达事件与证据。" },
    ],
  },
  {
    id: "stories-page",
    act: "act2",
    // 故事阅读器展示稳定身份的最新修订；快照内航迹由上一镜头固定。
    url: (t, c) => `/stories?domain_id=${t.domainId}&story_id=${c.storyIdentityId}`,
    settle: 3.5,
    duration: 7.0,
    joinPrev: 0.3,
    async perform({ page, ui, cursor, sleep, recorder }) {
      await sleep(1.2);
      await ui.scrollPanel(SEL.storyBody, 420, 3.0);
      await sleep(0.8);
      await cursor.clickFirst(SEL.storyEvidenceEntry, { optional: true, settle: 0.01 });
      // 右侧证据栏使用真实 DOM 过渡；等它完全展开后再截下一帧。
      await page.waitForTimeout(500);
      await recorder.snapshot();
      await sleep(1.4);
    },
    captions: [
      { at: 0.3, hold: 3.2, zh: "故事由有证据的延续、回应与纠正构成", en: "Stories are built from evidence-backed links between events." },
      { at: 3.8, hold: 2.9, zh: "稳定身份跨越快照，每条关系回到证据", en: "Each story persists across snapshots and returns to evidence." },
    ],
    narration: [
      { at: 0.2, hold: 6.2, zh: "有证据的事件彼此连接，长成持续演化、随时可以回溯的故事。" },
    ],
  },
  {
    id: "playlist",
    act: "act2",
    url: (t, c) => `/domains/${t.domainId}/playlist?date=${c.playlistDate}&tab=brief`,
    settle: 4.0,
    duration: 8.5,
    joinPrev: 0.3,
    async ready({ page }) {
      await page.evaluate(async (videoId) => {
        const app = globalThis.Alpine.$data(document.querySelector('[x-data="appShell()"]'));
        const video = app.playlistDayVideos.find((item) => String(item.id) === videoId);
        if (!video || video.media_name !== "Reuters") throw new Error("录制用 Reuters 记录不可用");
        await app.playbackSelectVideo(video, { autoPlay: false });
        await app.playbackSetContentTab("brief");
      }, content.playlistVideoId);
      await page.waitForFunction((videoId) => {
        const app = globalThis.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
        return String(app?.playlistCurrentVideo?.id || "") === videoId
          && app?.playlistCurrentVideo?.media_name === "Reuters"
          && !app?.playlistTranscriptLoading;
      }, content.playlistVideoId, { timeout: 15000 });
    },
    async perform({ page, ui, sleep, recorder }) {
      await sleep(0.7);
      await ui.scrollPanel(SEL.briefBody, 300, 1.5);
      await page.evaluate(async (videoId) => {
        const app = globalThis.Alpine.$data(document.querySelector('[x-data="appShell()"]'));
        await app.playbackSetContentTab("records");
        const video = app.playlistDayVideos.find((item) => String(item.id) === videoId);
        if (!video || video.media_name !== "Reuters") throw new Error("录制用 Reuters 记录不可用");
        await app.playbackSelectVideo(video, { autoPlay: false });
      }, content.playlistVideoId);
      await page.waitForFunction((videoId) => {
        const app = globalThis.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
        return String(app?.playlistCurrentVideo?.id || "") === videoId && !app?.playlistTranscriptLoading;
      }, content.playlistVideoId, { timeout: 15000 });
      await page.waitForTimeout(500);
      await recorder.snapshot();
      await ui.scrollPanel(SEL.recordList, 220, 1.0);
      await sleep(0.3);

      await page.evaluate(async (videoId) => {
        const app = globalThis.Alpine.$data(document.querySelector('[x-data="appShell()"]'));
        const video = app.playlistDayVideos.find((item) => String(item.id) === videoId);
        if (!video || video.media_name !== "CME Group") throw new Error("WTI Crude Oil 记录不可用");
        await app.playbackSelectVideo(video, { autoPlay: false, seekSec: 12 });
      }, content.playlistSecondVideoId);
      await page.waitForFunction((videoId) => {
        const app = globalThis.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
        return String(app?.playlistCurrentVideo?.id || "") === videoId
          && !app?.playlistTranscriptLoading
          && !!app?.playlistPlayerVideoUrl;
      }, content.playlistSecondVideoId, { timeout: 15000 });
      await page.evaluate(async () => {
        const app = globalThis.Alpine.$data(document.querySelector('[x-data="appShell()"]'));
        const video = app.playlistActiveMediaEl();
        video.muted = true;
        app.playlistMediaMuted = true;
        if (Number(video.readyState || 0) < 1) {
          await new Promise((resolve) => video.addEventListener("loadedmetadata", resolve, { once: true }));
        }
        video.currentTime = Math.min(12, Math.max(0, Number(video.duration || 13) - 1));
        await video.play();
      });
      await page.waitForFunction(() => {
        const app = globalThis.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
        const video = app?.playlistActiveMediaEl?.();
        return video && !video.paused && Number(video.currentTime || 0) > 12;
      }, null, { timeout: 10000 });
      await recorder.snapshot();
      for (let frame = 0; frame < 15; frame += 1) {
        await page.waitForTimeout(400);
        await recorder.step(0.5);
      }
    },
    captions: [
      { at: 0.3, hold: 3.5, zh: "观测简报把时间切片变成可引用的脉络", en: "Observation briefs turn slices of time into citable context." },
      { at: 4.3, hold: 3.7, zh: "从解释回到视频、转写与上下文", en: "Trace explanations back to video, transcripts, and context." },
    ],
    narration: [
      { at: 0.2, hold: 7.8, zh: "简报不是结论的终点。每个判断，都保留回到原始记录与上下文的路径。" },
    ],
  },

  {
    id: "library",
    act: "act3",
    url: (t) => `/library?domain_id=${t.domainId}&tab=sources`,
    settle: 3.0,
    duration: 7.0,
    joinPrev: 0,
    async perform({ page, ui, cursor, sleep, recorder }) {
      await sleep(0.5);
      await ui.scrollPanel(SEL.librarySourcesScroller, 520, 2.0);
      await cursor.clickText(SEL.libraryRecordsTab, { optional: true });
      await page.waitForFunction(() => {
        const app = globalThis.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
        return app?.libraryTab === "records" && !app?.videoLoadingList && app?.videoList?.length > 0;
      }, null, { timeout: 15000 });
      await recorder.snapshot();
      await ui.scrollPanel(SEL.libraryRecordsScroller, 520, 2.0);
      await sleep(0.6);
    },
    captions: [
      { at: 0.2, hold: 3.0, zh: "持续监听的信源汇入统一资料库", en: "Continuously watched sources flow into one library." },
      { at: 3.5, hold: 3.1, zh: "资料成为可检索、可追溯的长期记忆", en: "Material becomes searchable, traceable long-term memory." },
    ],
    narration: [
      { at: 0.2, hold: 6.1, zh: "所有信源与材料持续沉淀，成为可检索、可追溯的长期记忆。" },
    ],
  },
  {
    id: "operations",
    act: "act3",
    url: (t) => `/operations?domain_id=${t.domainId}&tab=active`,
    settle: 1.0,
    duration: 4.5,
    joinPrev: 0.3,
    async ready({ page }) {
      await page.waitForFunction(() => {
        const app = globalThis.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
        return app?.jobsTab === "active"
          && !app?.jobsActiveTypeStatsLoading
          && !app?.workersLoading;
      }, null, { timeout: 30000 });
    },
    async perform({ page, ui, sleep, recorder }) {
      await sleep(2.0);
      const succeededTab = await ui.first(SEL.operationsSucceededTab, { optional: false });
      await succeededTab.click({ timeout: 3000 });
      await page.waitForFunction(() => {
        const app = globalThis.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
        return app?.jobsTab === "succeeded"
          && !app?.jobsDoneLoading
          && !app?.jobsSeriesLoading;
      }, null, { timeout: 30000 });
      // 加载过程不进入片段；同一时间点直接替换为完整的“已成功”页面。
      await recorder.snapshot();
      await sleep(2.5);
    },
    captions: [
      { at: 0.2, hold: 4.1, zh: "任务从采集到分析持续运行，全程可观测", en: "Work from collection to analysis stays observable." },
    ],
    narration: [
      { at: 0.1, hold: 4.0, zh: "后台持续运转，也始终透明可控。" },
    ],
  },
  {
    id: "usage",
    act: "act3",
    url: () => "/usage",
    settle: 1.0,
    duration: 6.0,
    joinPrev: 0.3,
    async ready({ page }) {
      await page.waitForFunction(() => {
        const app = globalThis.Alpine?.$data(document.querySelector('[x-data="appShell()"]'));
        return app && !app.usageLoading && app.usagePayload;
      }, null, { timeout: 30000 });
    },
    async perform({ ui, sleep }) {
      await sleep(1.0);
      await ui.scrollPanel(SEL.usageScroller, 760, 3.6);
      await sleep(1.0);
    },
    captions: [
      { at: 0.2, hold: 2.6, zh: "调用、Token、下载与存储进入资源账本", en: "Calls, model tokens, downloads, and storage enter one ledger." },
      { at: 3.1, hold: 2.6, zh: "长期观测的成本始终清晰、可控", en: "The cost of long-term observation stays clear and controlled." },
    ],
    narration: [
      { at: 0.1, hold: 5.5, zh: "每一次资源消耗都有尺度，让长期运行清晰可控。" },
    ],
  },
  {
    id: "mcp",
    act: "act3",
    url: () => "/mcp-guide",
    settle: 2.5,
    duration: 7.0,
    joinPrev: 0.3,
    async perform({ page, ui, sleep, recorder }) {
      const switchClient = async (selector, targetTab) => {
        const button = await ui.first(selector, { optional: false });
        await button.click({ timeout: 3000 });
        await page.waitForFunction((expectedTab) => {
          const root = document.querySelector('.raelyn-view--mcp [x-data*="toolTab"]');
          const workspace = document.querySelector(".raelyn-mcp-client-workspace");
          if (!root || !workspace) return false;
          const state = globalThis.Alpine?.$data(root);
          const visiblePanels = [...workspace.children].filter(
            (element) => getComputedStyle(element).display !== "none",
          );
          return state?.toolTab === expectedTab
            && visiblePanels.length === 1
            && visiblePanels[0].getAttribute("x-show") === `toolTab==='${expectedTab}'`;
        }, targetTab, { timeout: 5000 });
        // x-transition 完成前的错误面板尺寸不进入视频，只保留稳定布局。
        await page.waitForTimeout(100);
        await recorder.snapshot();
      };

      await sleep(0.7);
      await switchClient(SEL.mcpClaude, "claude");
      await sleep(0.8);
      await switchClient(SEL.mcpVscode, "vscode");
      await sleep(0.8);
      await switchClient(SEL.mcpCli, "cli");
      await ui.scrollPanel(SEL.mcpScroller, 520, 2.0);
      await sleep(0.8);
    },
    captions: [
      { at: 0.2, hold: 4.4, zh: "MCP 向智能体开放同一语义世界", en: "MCP opens the same semantic world to agents." },
      { at: 5.0, hold: 2.0, zh: "https://github.com/scisaga/raelyn", en: "https://github.com/scisaga/raelyn" },
    ],
    narration: [
      { at: 0.1, hold: 4.7, zh: "最终，智能体也能进入同一语义世界，沿着证据继续探索。" },
    ],
  },
];

// ---------------------------------------------------------------------------
// 选择器：全部集中在这里。首次运行请先跑 `node record-tour.mjs --check`，
// 它会逐个镜头报告哪个选择器在真实 DOM 里没命中，改这一处即可。
// 文本型选择器写中文可见文案，结构型写 CSS。
// ---------------------------------------------------------------------------
export const SEL = {
  timelinePlay: "css=button[\\@click='playlistEventMapTogglePlayback()']",
  storyBody: "css=.raelyn-view--stories main",
  storyEvidenceEntry: "css=button[\\@click*='storyOpenEdgeInspector']",
  briefBody: "css=article[x-ref='playlistBriefContentEl']",
  recordList: "css=[x-ref='playlistDayListScroller']",
  librarySourcesScroller: "css=.raelyn-view--library > .raelyn-surface-base > .flex-1.overflow-auto",
  libraryRecordsTab: "css=.raelyn-view--library button[\\@click=\"setLibraryTab('records')\"]",
  libraryRecordsScroller: "css=.raelyn-view--library .p-4.flex-1.overflow-auto",
  operationsSucceededTab: "css=.raelyn-view--operations button[\\@click=\"setJobsTab('succeeded')\"]",
  usageScroller: "css=.raelyn-usage-dashboard",
  mcpScroller: "css=.raelyn-view--mcp > .raelyn-surface-base > .flex-1.overflow-auto",
  mcpClaude: "css=.raelyn-view--mcp button[\\@click=\"toolTab='claude'\"]",
  mcpVscode: "css=.raelyn-view--mcp button[\\@click=\"toolTab='vscode'\"]",
  mcpCli: "css=.raelyn-view--mcp button[\\@click=\"toolTab='cli'\"]",
};
