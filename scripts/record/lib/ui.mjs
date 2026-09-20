// 录制用的页面驱动 helper：Alpine 状态桥、稳定等待、必要点击与面板滚动。
// 所有交互都必须「可失败但不中断」：真实 DOM 里少一个按钮，只警告并继续，
// 不让一次选择器漂移毁掉整条录制。

export const warnings = [];

function warn(shotId, message) {
  const line = `[warn] ${shotId || "-"} :: ${message}`;
  warnings.push(line);
  console.warn(line);
}

// 录制卫生：隐藏滚动条、停掉 caret 闪烁、关掉会打断镜头的过渡提示。
export const hygieneInitScript = `
(() => {
  const css = document.createElement("style");
  css.textContent = [
    "::-webkit-scrollbar{width:0!important;height:0!important}",
    "*{caret-color:transparent!important}",
    "html{scrollbar-width:none!important}",
  ].join("");
  const put = () => document.head && document.head.appendChild(css);
  if (document.head) put(); else addEventListener("DOMContentLoaded", put, { once: true });
})();
`;

export function createUi(page, shotId, timeScale = 1, recorder = null) {
  return {
    // 解析 "text=..." / "css=..." 两种写法，未命中返回 null 而不是抛错。
    async first(selector, { optional = true } = {}) {
      if (!selector) return null;
      try {
        const locator = page.locator(selector).first();
        await locator.waitFor({ state: "visible", timeout: 3500 });
        return locator;
      } catch {
        if (!optional) throw new Error(`selector not found: ${selector}`);
        warn(shotId, `选择器未命中，跳过该动作: ${selector}`);
        return null;
      }
    },

    // 只记录内容滚动本身，不模拟鼠标移动轨迹。
    async scrollPanel(selector, distance, seconds) {
      seconds *= timeScale;
      const target = await this.first(selector);
      if (!target) {
        await page.waitForTimeout(seconds * 1000);
        return;
      }
      // 滚动只需要若干关键位置；最终组装会倍速，逐视频帧截图只会重复消耗 WebGL 合成。
      const steps = Math.max(1, Math.round(seconds * (recorder ? 2 : 12)));
      for (let i = 0; i < steps; i += 1) {
        await target.evaluate((el, d) => {
          const scroller = el.scrollHeight > el.clientHeight
            ? el
            : el.querySelector("[class*='overflow-y'],[style*='overflow']") || el;
          scroller.scrollTop += d;
        }, distance / steps);
        if (recorder) await recorder.step(seconds / steps);
        else await page.waitForTimeout((seconds * 1000) / steps);
      }
    },

    // 读 Alpine 根组件状态，用于判断星域首帧是否真的画完了。
    async state(field) {
      return page.evaluate((key) => {
        try {
          const root = document.querySelector("[x-data]");
          if (!root) return null;
          const data = window.Alpine?.$data
            ? window.Alpine.$data(root)
            : root._x_dataStack?.[0];
          if (!data) return null;
          return key ? data[key] ?? null : null;
        } catch {
          return null;
        }
      }, field);
    },

    // 星域就绪：场景和标签元数据都完成，并且确实加载了配置指定的历史快照。
    // 超时必须失败；继续录制只会得到空场景或错误快照，不能用固定等待掩盖。
    async waitFieldReady({ timeoutMs = 45000, snapshotId = "" } = {}) {
      const started = Date.now();
      while (Date.now() - started < timeoutMs) {
        const status = await page.evaluate((expectedSnapshotId) => {
          try {
            const root = document.querySelector("[x-data]");
            if (!root) return null;
            const data = window.Alpine?.$data
              ? window.Alpine.$data(root)
              : root._x_dataStack?.[0];
            if (!data) return null;
            return {
              sceneComplete: data._playlistEventMapSceneComplete === true,
              metadataComplete: data._playlistEventMapMetadataComplete === true,
              snapshotId: String(data.playlistEventMapSnapshotId || ""),
              error: String(data.playlistEventMapError || ""),
              matchesSnapshot: !expectedSnapshotId
                || String(data.playlistEventMapSnapshotId || "") === expectedSnapshotId,
            };
          } catch {
            return null;
          }
        }, snapshotId);
        if (status?.error) throw new Error(`星域加载失败: ${status.error}`);
        if (status?.sceneComplete && status?.metadataComplete && status?.matchesSnapshot) return status;
        await page.waitForTimeout(500);
      }
      throw new Error(`${shotId}: 星域未在 ${timeoutMs}ms 内完成场景与标签加载，或未加载指定快照 ${snapshotId}`);
    },
  };
}

export function createCursor(page, ui, shotId, timeScale = 1, recorder = null) {
  return {
    async clickFirst(selector, { optional = true, settle = 0.25 } = {}) {
      const locator = await ui.first(selector, { optional });
      if (!locator) return false;
      await locator.click({ timeout: 3000 }).catch(() => warn(shotId, `点击失败: ${selector}`));
      if (recorder) await recorder.hold(settle * timeScale);
      else await page.waitForTimeout(settle * timeScale * 1000);
      return true;
    },

    async clickText(selector, options = {}) {
      return this.clickFirst(selector, options);
    },
  };
}

export function sleepFactory(page, timeScale = 1, recorder = null) {
  return (seconds) => recorder
    ? recorder.hold(seconds * timeScale)
    : page.waitForTimeout(seconds * timeScale * 1000);
}
