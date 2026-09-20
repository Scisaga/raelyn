// Raelyn 产品导览录制配置 —— 分镜、README 截图、画面字幕、配音稿、镜头转场与配乐的唯一真源。
//
// 所有时间单位为秒。改分镜只改这个文件；record-tour.mjs / make-subtitles.mjs /
// make-narration.mjs / compose-tour.mjs 都从这里读取，保证视频与文案时间线永远对齐。

export const output = {
  // 原始界面录屏与最终输出都按 1440×960 准备。
  viewport: { width: 1440, height: 960 },
  fps: 30,
  // 无头环境按帧主动采样；最终 1.5× 后约 12 个独立画面/秒，README GIF 的帧率由此推出。
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
  // MP4 默认使用软字幕；ASS 保留人工确认过的中英文硬字幕样式。
  styles: {
    zh: {
      fontName: "Microsoft YaHei UI",
      fontSize: 44,
      spacing: 0.4,
      outlineColour: "&HA00A0D12",
      backColour: "&HA00A0D12",
      marginH: 120,
      marginV: 52,
      urlFontSize: 28,
    },
    en: {
      fontName: "Noto Sans CJK SC",
      fontSize: 48,
      spacing: 0.6,
      outlineColour: "&HA00A0D12",
      backColour: "&HA0000000",
      marginH: 140,
      marginV: 56,
    },
  },
  // 单条字幕最短停留，低于此值会在构建时报警。
  minHoldSeconds: 1.2,
};

// README 三联图从已经审片的单镜头视频精确取帧。时间基于 out/shots/*.mp4，
// 不重新访问页面，确保静态展示与产品导览来自同一批素材。
export const readmeScreenshots = {
  outputDir: "docs/assets/tour",
  width: 1440,
  height: 960,
  quality: 82,
  frames: [
    { id: "story", shotId: "stories-page", atSeconds: 8.2, file: "readme-story.webp" },
    { id: "playlist", shotId: "playlist", atSeconds: 8.2, file: "readme-playlist.webp" },
    { id: "mcp", shotId: "mcp", atSeconds: 0.8, file: "readme-mcp.webp" },
  ],
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
// caption  { at, zh, en, hold, crossShot? } 相对本镜头起点的字幕；只有人工对时确实需要
//          跨过镜头边界时才设置 crossShot。
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
      { at: 0.3, hold: 5.6, zh: "这里是 Raelyn，你的私人语义观测站\nRaelyn让持续发生的事件，不再散落在信息洪流里", en: "This is Raelyn, your private semantic observatory.\nIt keeps unfolding events from getting lost in the stream." },
      { at: 6.0, hold: 5.8, zh: "从视频中提取事实，再把分散的相关事件\n汇聚成一片可回放、可探索的星域", en: "It extracts facts from video and gathers related events\ninto a starfield you can replay and explore." },
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
      { at: 0.2, hold: 3.3, zh: "事件按语义聚合，海量信息自然形成结构", en: "Events cluster by meaning, giving information structure." },
      { at: 3.6, hold: 3.3, zh: "从任意星群深入事件，都能回到原始来源和证据", en: "Drill into any cluster and return to its sources and evidence." },
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
      { at: 0.3, hold: 3.4, zh: "这些有据可查的事件关联，又会进一步串联成故事", en: "Evidence-backed links connect these events into stories." },
      { at: 3.8, hold: 3.1, zh: "故事跨快照持续演进，并始终可以回溯到证据", en: "Stories evolve across snapshots, always tied to evidence." },
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
      { at: 0.3, hold: 3.9, zh: "选定一段时间，观测简报就把它整理成可引用的完整脉络", en: "Choose a time span. Observation briefs turn it\ninto a complete, citable narrative." },
      { at: 4.3, hold: 4.3, crossShot: true, zh: "其中的每个解释，都能追溯到原始视频、转写文本和上下文", en: "Explanations lead back to video, transcripts, and context." },
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
      { at: 0.2, hold: 3.2, zh: "持续监测的所有信源，也会统一汇入资料库", en: "Every source under continuous watch flows into one library." },
      { at: 3.5, hold: 3.3, zh: "素材由此沉淀为可检索、可追溯的长期记忆", en: "Material becomes searchable, traceable long-term memory." },
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
      { at: 0.2, hold: 4.1, zh: "从采集、处理到分析，整个工作流程始终清晰可观测", en: "Work from collection to analysis stays fully observable." },
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
      { at: 0.2, hold: 2.9, zh: "调用、模型用量、下载和存储统一记账", en: "Calls, model usage, downloads, and storage share one ledger." },
      { at: 3.2, hold: 2.6, zh: "让长期观测的成本，始终清晰可控", en: "Long-term observation costs stay clear and controlled." },
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
      { at: 0.2, hold: 3.3, zh: "通过 MCP，智能体也能进入同一个语义世界", en: "Through MCP, agents enter the same semantic world." },
      { at: 3.6, hold: 3.4, zh: "欢迎在 GitHub 上探索 Raelyn\nhttps://github.com/scisaga/raelyn", en: "Explore Raelyn on GitHub\nhttps://github.com/scisaga/raelyn" },
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
