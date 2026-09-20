import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const templateUrl = new URL("../../../templates/app/views/mcp-guide.html", import.meta.url);

test("智能体接入页提供 ChatGPT 和 Claude 路径密钥配置", async () => {
  const source = await readFile(templateUrl, "utf8");

  assert.match(source, /toolTab: 'chatgpt'/);
  assert.match(source, /在 ChatGPT 中添加插件/);
  assert.match(source, /Settings → Security and login/);
  assert.match(source, /进入 ChatGPT Plugins/);
  assert.match(source, /在 Claude 中添加自定义连接器/);
  assert.match(source, /\/mcp\/&lt;MCP_ROUTE_SECRET&gt;/);
  assert.match(source, /No authentication/);
  assert.match(source, /No sign-in/);
  assert.match(source, /操作路径/);
  assert.match(source, /xl:grid-cols-\[220px_minmax\(0,1fr\)\]/);
  assert.match(source, /class="raelyn-mcp-client-workspace min-w-0"/);
  assert.match(source, /border-slate-700\/80/);
  assert.match(source, /max-w-\[1440px\]/);
  assert.match(source, /xl:grid-cols-\[minmax\(0,1fr\)_420px\]/);
  assert.match(source, /连接参数/);
  assert.match(source, /brand\/official\/chatgpt\.svg/);
  assert.match(source, /brand\/official\/openai\.png/);
  assert.match(source, /brand\/official\/claude\.svg/);
  assert.match(source, /brand\/official\/vscode\.ico/);
  assert.doesNotMatch(source, /material-symbols/);
});

test("智能体接入页移除 OpenClaw 并说明只读能力边界", async () => {
  const source = await readFile(templateUrl, "utf8");

  assert.doesNotMatch(source, /OpenClaw|openclaw/);
  assert.match(source, /查看安全与只读范围/);
  assert.match(source, /不发布 sync_media/);
  assert.match(source, /sync_media/);
  assert.match(source, /generate_brief/);
  assert.doesNotMatch(source, /get_playlist_context/);
  assert.match(source, /get_playlist_summary/);
});
