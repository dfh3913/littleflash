/* 截图脚本：登录页、学习档案、用户管理、设置页（桌面尺寸） */
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.LF_BASE || "http://127.0.0.1:5055";
const PORT = Number(process.env.LF_CDP_PORT || 9335);
const OUT = path.resolve(__dirname, "shots");
const profile = path.join(os.tmpdir(), "lf-shot-" + Date.now());

fs.mkdirSync(OUT, { recursive: true });

const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--remote-debugging-port=" + PORT,
    "--remote-allow-origins=*",
    "--user-data-dir=" + profile,
    "about:blank",
  ],
  { stdio: "ignore" }
);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  let version = null;
  for (let i = 0; i < 40; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      version = await res.json();
      break;
    } catch (e) {
      await sleep(250);
    }
  }
  if (!version) throw new Error("Chrome 启动失败");

  const ws = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.onopen = res;
    ws.onerror = rej;
  });

  let seq = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  };
  const send = (method, params, sessionId) =>
    new Promise((resolve) => {
      const id = ++seq;
      pending.set(id, resolve);
      ws.send(JSON.stringify({ id, method, params: params || {}, sessionId }));
    });

  const created = await send("Target.createTarget", { url: "about:blank" });
  const attached = await send("Target.attachToTarget", {
    targetId: created.result.targetId,
    flatten: true,
  });
  const sessionId = attached.result.sessionId;

  await send("Page.enable", {}, sessionId);
  await send("Runtime.enable", {}, sessionId);
  await send(
    "Emulation.setDeviceMetricsOverride",
    { width: 1440, height: 950, deviceScaleFactor: 1, mobile: false },
    sessionId
  );

  async function goto(url) {
    await send("Page.navigate", { url }, sessionId);
    await sleep(1600);
  }
  async function evaluate(expression) {
    const r = await send(
      "Runtime.evaluate",
      { expression, awaitPromise: true, returnByValue: true },
      sessionId
    );
    if (r.result && r.result.exceptionDetails) {
      throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
    }
    return r.result.result.value;
  }
  async function shot(name) {
    const r = await send(
      "Page.captureScreenshot",
      { format: "png", captureBeyondViewport: false },
      sessionId
    );
    fs.writeFileSync(path.join(OUT, name + ".png"), Buffer.from(r.result.data, "base64"));
    console.log("saved", name);
  }
  async function loginAs(username, password) {
    await goto(BASE + "/login");
    await evaluate(
      "document.getElementById('loginUsername').value=" +
        JSON.stringify(username) +
        ";document.getElementById('loginPassword').value=" +
        JSON.stringify(password) +
        ";document.getElementById('loginForm').requestSubmit();true"
    );
    await sleep(2200);
  }

  await goto(BASE + "/login");
  await shot("login");

  await loginAs("240101", "240101");
  await goto(BASE + "/account");
  await shot("account");
  await goto(BASE + "/settings");
  await shot("settings-user");

  await evaluate("document.getElementById('logoutBtn').click();true");
  await sleep(1500);
  await loginAs("admin", "DevAdmin2026");
  await goto(BASE + "/admin");
  await shot("admin");

  ws.close();
  chrome.kill();
  process.exit(0);
})().catch((error) => {
  console.error("截图失败：", error.message);
  chrome.kill();
  process.exit(2);
});
