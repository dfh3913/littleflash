/* 单文件版端到端检查（file:// 直接打开） */
const { spawn } = require("child_process");
const os = require("os");
const path = require("path");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const FILE = process.env.LF_FILE || path.resolve(__dirname, "../dist/littleflash-0.1.3-standalone.html");
const PORT = Number(process.env.LF_CDP_PORT || 9336);
const profile = path.join(os.tmpdir(), "lf-standalone-" + Date.now());
const fileUrl = "file:///" + FILE.replace(/\\/g, "/");

const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--allow-file-access-from-files",
    "--remote-debugging-port=" + PORT,
    "--remote-allow-origins=*",
    "--user-data-dir=" + profile,
    "about:blank",
  ],
  { stdio: "ignore" }
);

let failures = 0;
function check(name, cond, extra) {
  console.log((cond ? "  PASS  " : "  FAIL  ") + name + (extra ? "  " + extra : ""));
  if (!cond) failures++;
}
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

  const errors = [];
  listeners.push((msg) => {
    if (msg.sessionId !== sessionId) return;
    if (msg.method === "Runtime.exceptionThrown") {
      const d = msg.params.exceptionDetails;
      errors.push(d.exception ? d.exception.description : d.text);
    }
    if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
      errors.push(msg.params.entry.text);
    }
  });

  await send("Page.enable", {}, sessionId);
  await send("Runtime.enable", {}, sessionId);
  await send("Log.enable", {}, sessionId);

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

  async function waitFor(expression, timeout) {
    const deadline = Date.now() + (timeout || 8000);
    while (Date.now() < deadline) {
      if (await evaluate(expression)) return true;
      await sleep(200);
    }
    return false;
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
  await send("Page.navigate", { url: fileUrl }, sessionId);
  await loaded;
  await sleep(600);

  check("单文件页面已加载", await evaluate("document.title.indexOf('LittleFlash') !== -1"));
  check(
    "首屏显示单词",
    await waitFor("document.getElementById('wordText').textContent.length > 0", 8000),
    await evaluate("document.getElementById('wordText').textContent")
  );
  const firstWord = await evaluate("document.getElementById('wordText').textContent");
  const firstCount = await evaluate("document.getElementById('bookCount').textContent");

  check(
    "设置面板默认隐藏",
    await evaluate("document.getElementById('settingsPanel').hidden === true")
  );
  await evaluate("document.getElementById('settingsBtn').click();true");
  await sleep(300);
  check(
    "点击设置可打开面板",
    await evaluate("document.getElementById('settingsPanel').hidden === false")
  );
  check(
    "词书选择器已渲染",
    await evaluate("document.querySelectorAll('#standaloneBooks .standalone-book').length > 0"),
    String(await evaluate("document.querySelectorAll('#standaloneBooks .standalone-book').length"))
  );

  // 切换到随机顺序，自动生成种子
  await evaluate(
    "var o=document.getElementById('stOrder');o.value='random';o.dispatchEvent(new Event('change',{bubbles:true}));true"
  );
  await sleep(300);
  const seed = await evaluate("document.getElementById('stSeed').value");
  check("随机顺序自动生成种子", typeof seed === "string" && seed.length >= 4, seed);
  check(
    "种子保存在 localStorage",
    await evaluate(
      "JSON.parse(localStorage.getItem('littleflash.standalone.v1')).seed === document.getElementById('stSeed').value"
    )
  );

  // 选中第二本词书，deck 应该变大
  await evaluate(
    "var heads=document.querySelectorAll('#standaloneBooks .standalone-book__head input'); if(heads.length>1){heads[1].checked=true;heads[1].dispatchEvent(new Event('change',{bubbles:true}));} true"
  );
  await sleep(500);
  const secondCount = await evaluate("document.getElementById('bookCount').textContent");
  check("多选词书后单词数增加", secondCount !== firstCount, firstCount + " → " + secondCount);

  // 关闭面板后仍可继续学习
  await evaluate("document.getElementById('closeSettings').click();true");
  await sleep(200);
  check(
    "设置面板可关闭",
    await evaluate("document.getElementById('settingsPanel').hidden === true")
  );

  check("页面无 JS 报错", errors.length === 0, errors.slice(0, 2).join(" | "));
  console.log("首词:", firstWord);
  console.log("\n结果：" + (failures === 0 ? "全部通过" : failures + " 项失败"));

  ws.close();
  chrome.kill();
  process.exit(failures === 0 ? 0 : 1);
})().catch((error) => {
  console.error("单文件 E2E 失败：", error.message);
  chrome.kill();
  process.exit(2);
});
