/* 截图小工具：headless Chrome + CDP。
   用法：node .jstest/shot.js <url> <out.png> [prepJS]
   prepJS 会在文档加载前注入（例如设置 localStorage），方便绕过首次向导。 */
const { spawn } = require("child_process");
const os = require("os");
const path = require("path");
const fs = require("fs");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const URL_ = process.argv[2];
const OUT = process.argv[3];
const PREP = process.argv[4] || "";
const PORT = Number(process.env.LF_CDP_PORT || 9341);
const WIDTH = Number(process.env.LF_W || 1280);
const HEIGHT = Number(process.env.LF_H || 900);
const SCALE = Number(process.env.LF_SCALE || 2);
const profile = path.join(os.tmpdir(), "lf-shot-" + Date.now());

const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--force-device-scale-factor=" + SCALE,
    "--window-size=" + WIDTH + "," + HEIGHT,
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
  for (let i = 0; i < 60; i++) {
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
  const listeners = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    } else if (msg.method) {
      listeners.forEach((fn) => fn(msg));
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
    { width: WIDTH, height: HEIGHT, deviceScaleFactor: SCALE, mobile: false },
    sessionId
  );

  const errors = [];
  listeners.push((msg) => {
    if (msg.sessionId !== sessionId) return;
    if (msg.method === "Runtime.exceptionThrown") {
      const d = msg.params.exceptionDetails;
      errors.push(d.exception ? d.exception.description : d.text);
    }
  });

  if (PREP) {
    await send(
      "Page.addScriptToEvaluateOnNewDocument",
      { source: PREP },
      sessionId
    );
  }

  const loaded = new Promise((resolve) => {
    const fn = (msg) => {
      if (msg.sessionId === sessionId && msg.method === "Page.loadEventFired") {
        listeners.splice(listeners.indexOf(fn), 1);
        resolve();
      }
    };
    listeners.push(fn);
  });
  await send("Page.navigate", { url: URL_ }, sessionId);
  await loaded;
  await sleep(1600);

  const shot = await send(
    "Page.captureScreenshot",
    { format: "png", captureBeyondViewport: false },
    sessionId
  );
  fs.writeFileSync(path.resolve(OUT), Buffer.from(shot.result.data, "base64"));
  if (errors.length) console.log("JS errors:\n" + errors.join("\n"));
  console.log("saved " + path.resolve(OUT));
  chrome.kill();
  process.exit(0);
})().catch((error) => {
  console.error(error);
  chrome.kill();
  process.exit(1);
});
