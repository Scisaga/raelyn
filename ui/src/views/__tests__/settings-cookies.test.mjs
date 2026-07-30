import assert from "node:assert/strict";
import test from "node:test";

import { createSettingsViewMethods } from "../settings-model.js";


test("设置页只读取 Cookies 配置状态，不接收或保留明文", async () => {
  const methods = createSettingsViewMethods({
    ytdlpFormatPreset1080: "",
    ytdlpFormatPreset720: "",
  });
  const state = {
    ...methods,
    ytdlpCookiesLoaded: false,
    ytdlpCookiesYoutubeText: "旧明文",
    ytdlpCookiesYoutubeConfigured: false,
    ytdlpCookiesYoutubeError: "",
    ytdlpCookiesBilibiliText: "旧明文",
    ytdlpCookiesBilibiliConfigured: false,
    ytdlpCookiesBilibiliError: "",
    async api() {
      return {
        data: {
          ytdlp_cookies_youtube: { configured: true },
          ytdlp_cookies_bilibili: { configured: false },
        },
      };
    },
  };

  await state.loadYtdlpCookies({ force: true });

  assert.equal(state.ytdlpCookiesYoutubeConfigured, true);
  assert.equal(state.ytdlpCookiesBilibiliConfigured, false);
  assert.equal(state.ytdlpCookiesYoutubeText, "");
  assert.equal(state.ytdlpCookiesBilibiliText, "");
});


test("普通保存拒绝空 Cookie，明确清空才发送空值", async () => {
  const methods = createSettingsViewMethods({
    ytdlpFormatPreset1080: "",
    ytdlpFormatPreset720: "",
  });
  const requests = [];
  const state = {
    ...methods,
    ytdlpCookiesLoaded: true,
    ytdlpCookiesYoutubeText: "",
    ytdlpCookiesYoutubeConfigured: true,
    ytdlpCookiesYoutubeSaving: false,
    ytdlpCookiesYoutubeError: "",
    globalStatus: "",
    async api(path, options) {
      requests.push({ path, options });
      return { ok: true };
    },
    async loadSystemStatus() {},
  };

  await state.saveYtdlpCookiesYoutube();
  assert.equal(requests.length, 0);
  assert.match(state.ytdlpCookiesYoutubeError, /请先粘贴/);
  assert.equal(state.ytdlpCookiesYoutubeConfigured, true);

  await state.clearYtdlpCookiesYoutube();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, "/config/ytdlp_cookies_youtube");
  assert.deepEqual(JSON.parse(requests[0].options.body), { value: { text: "" } });
  assert.equal(state.ytdlpCookiesYoutubeConfigured, false);
  assert.equal(state.globalStatus, "已清空 YouTube Cookies");
});
