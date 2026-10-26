/* 0.1.3 / 0.1.3.2 后端版端到端：多词书选择、种子、保存、预览播放与首页 */
const { spawn } = require("child_process");
const os = require("os");
const path = require("path");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.LF_BASE || "http://127.0.0.1:5055";
const PASSWORD = process.env.LF_PASSWORD || "test-settings-password";
const PORT = Number(process.env.LF_CDP_PORT || 9337);
const profile = path.join(os.tmpdir(), "lf-013-" + Date.now());

const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
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

  async function goto(url) {
    const loaded = new Promise((resolve) => {
      const fn = (msg) => {
        if (msg.sessionId === sessionId && msg.method === "Page.loadEventFired") {
          listeners.splice(listeners.indexOf(fn), 1);
          resolve();
        }
      };
      listeners.push(fn);
    });
    await send("Page.navigate", { url }, sessionId);
    await loaded;
    await sleep(600);
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

  async function waitFor(expression, timeout) {
    const deadline = Date.now() + (timeout || 8000);
    while (Date.now() < deadline) {
      if (await evaluate(expression)) return true;
      await sleep(200);
    }
    return false;
  }

  /* 登录 */
  await goto(BASE + "/settings");
  await evaluate(
    "document.getElementById('settingsPassword').value=" +
      JSON.stringify(PASSWORD) +
      ";document.getElementById('loginForm').requestSubmit();true"
  );
  await sleep(1500);
  await waitFor(
    "document.querySelector('meta[name=\"settings-mode\"]') && document.querySelector('meta[name=\"settings-mode\"]').content === 'server'",
    10000
  );
  check(
    "管理员登录后进入服务器模式",
    (await evaluate(
      "document.querySelector('meta[name=\"settings-mode\"]').content"
    )) === "server"
  );

  /* 先恢复默认设置做基线，保证用例可以重复跑 */
  await evaluate(
    "fetch('/api/settings/reset',{method:'POST',headers:{'X-CSRF-Token':document.querySelector('meta[name=\"csrf-token\"]').content}}).then(r=>r.json()).then(d=>!!d.ok)"
  );
  await goto(BASE + "/settings");
  await sleep(600);

  check(
    "设置页显示多词书选择器",
    (await evaluate("document.querySelectorAll('#bookPicker .book-pick').length")) === 7
  );
  check(
    "管理员可见音频预缓存开关",
    await evaluate("!!document.getElementById('audioPrefetchToggle')")
  );
  check(
    "刚进设置页时预览是暂停的",
    (await evaluate("document.getElementById('previewBadgeText').textContent")) === "已暂停" &&
      (await evaluate(
        "document.getElementById('previewPlay').getAttribute('aria-pressed')"
      )) === "false"
  );

  const before = await evaluate(
    "fetch('/api/bootstrap').then(r=>r.json()).then(d=>d.preferences.selections.length)"
  );

  /* 勾选第二本词书：单元默认一个都不选 */
  await evaluate(
    "var b=document.querySelectorAll('#bookPicker .book-pick')[1].querySelector('.book-pick__check');b.checked=true;b.dispatchEvent(new Event('change',{bubbles:true}));true"
  );
  await sleep(500);
  const autoUnits = await evaluate(
    "document.querySelectorAll('#bookPicker .book-pick')[1].querySelectorAll('.unit-pick__check:checked').length"
  );
  check("勾选词书后单元默认全不选", autoUnits === 0, "自动勾选单元数 " + autoUnits);
  const pendingText = await evaluate(
    "document.getElementById('pickerSummary').textContent"
  );
  check(
    "摘要提示还有词书没选单元",
    pendingText.indexOf("还没选单元") !== -1,
    pendingText
  );
  check(
    "只勾词书的卡片标记为待选",
    await evaluate(
      "document.querySelectorAll('#bookPicker .book-pick')[1].classList.contains('is-empty')"
    )
  );

  /* 再点这本书的第一个单元 */
  await evaluate(
    "var u=document.querySelectorAll('#bookPicker .book-pick')[1].querySelector('.unit-pick__check');u.checked=true;u.dispatchEvent(new Event('change',{bubbles:true}));true"
  );
  await sleep(900);
  check(
    "补选单元后保存状态恢复",
    await waitFor(
      "document.getElementById('saveStatus').dataset.state === 'saved'",
      8000
    )
  );
  const selections = await evaluate(
    "fetch('/api/bootstrap').then(r=>r.json()).then(d=>d.preferences.selections.length)"
  );
  check("多选结果写入服务器", selections === 2, before + " → " + selections);
  check(
    "选择摘要显示两本词书",
    (await evaluate("document.getElementById('pickerSummary').textContent")).indexOf("2 本词书") !== -1,
    await evaluate("document.getElementById('pickerSummary').textContent")
  );

  /* 随机种子 */
  await evaluate(
    "var o=document.querySelector('input[name=\"order\"][value=\"random\"]');o.checked=true;o.dispatchEvent(new Event('change',{bubbles:true}));true"
  );
  await sleep(500);
  check(
    "随机顺序显示种子输入",
    await evaluate("document.getElementById('seedField').hidden === false")
  );
  const seed1 = await evaluate("document.getElementById('seedInput').value");
  check("自动生成种子", seed1.length >= 4, seed1);
  await evaluate("document.getElementById('seedRefresh').click();true");
  await sleep(800);
  const seed2 = await evaluate("document.getElementById('seedInput').value");
  check("换一个会生成新种子", seed2 !== seed1, seed1 + " → " + seed2);
  const savedSeed = await evaluate(
    "fetch('/api/bootstrap').then(r=>r.json()).then(d=>d.preferences.seed)"
  );
  check("种子已保存到服务器", savedSeed === seed2, savedSeed);

  /* 预览窗：默认暂停 + 播放/上一张/下一张 */
  check(
    "预览默认暂停",
    (await evaluate("document.getElementById('previewBadgeText').textContent")) === "已暂停",
    await evaluate("document.getElementById('previewBadgeText').textContent")
  );
  check(
    "预览底部显示张数",
    (await evaluate("document.getElementById('previewIndex').textContent")).indexOf("/") !== -1,
    await evaluate("document.getElementById('previewIndex').textContent")
  );
  const previewFirst = await evaluate(
    "document.getElementById('previewWord').textContent"
  );
  await evaluate("document.getElementById('previewNext').click();true");
  await sleep(400);
  const previewSecond = await evaluate(
    "document.getElementById('previewWord').textContent"
  );
  check(
    "下一张切换到新卡片",
    previewSecond !== previewFirst,
    previewFirst + " → " + previewSecond
  );
  await evaluate("document.getElementById('previewPrev').click();true");
  await sleep(400);
  check(
    "上一张回到原卡片",
    (await evaluate("document.getElementById('previewWord').textContent")) === previewFirst,
    previewFirst
  );

  // 关掉发音，避免 headless 里请求音频产生网络错误日志
  await evaluate(
    "var a=document.getElementById('audioToggle');a.checked=false;a.dispatchEvent(new Event('change',{bubbles:true}));true"
  );
  await sleep(400);
  await evaluate("document.getElementById('previewPlay').click();true");
  await sleep(300);
  check(
    "播放后徽标变为演示中",
    (await evaluate("document.getElementById('previewBadgeText').textContent")) === "演示中",
    await evaluate("document.getElementById('previewBadgeText').textContent")
  );
  check(
    "播放按钮进入按下状态",
    (await evaluate(
      "document.getElementById('previewPlay').getAttribute('aria-pressed')"
    )) === "true"
  );
  await evaluate("document.getElementById('previewPlay').click();true");
  await sleep(300);
  check(
    "再次点击恢复暂停",
    (await evaluate("document.getElementById('previewBadgeText').textContent")) === "已暂停"
  );
  await evaluate(
    "var a=document.getElementById('audioToggle');a.checked=true;a.dispatchEvent(new Event('change',{bubbles:true}));true"
  );
  await sleep(500);

  /* 刷新后保持 */
  await goto(BASE + "/settings");
  check(
    "刷新后仍是两本词书",
    (await evaluate(
      "document.querySelectorAll('#bookPicker .book-pick__check:checked').length"
    )) === 2
  );
  check(
    "刷新后种子保持",
    (await evaluate("document.getElementById('seedInput').value")) === seed2
  );

  /* 首页按多本词书加载 */
  await goto(BASE + "/");
  check(
    "首页加载多词书单词",
    await waitFor("document.getElementById('wordText').textContent.length > 0", 12000)
  );
  const countText = await evaluate("document.getElementById('bookCount').textContent");
  const total = parseInt(countText, 10);
  // 默认 6 个单元约 387 词，加一本书的一个单元后应明显变多
  check("首页单词数覆盖两本词书", total > 400, countText);
  check(
    "首页标题显示多本词书",
    (await evaluate("document.getElementById('bookTitle').textContent")).indexOf("多本词书") !== -1
  );
  const first = await evaluate("document.getElementById('wordText').textContent");
  await goto(BASE + "/");
  await waitFor("document.getElementById('wordText').textContent.length > 0", 12000);
  const second = await evaluate("document.getElementById('wordText').textContent");
  check("相同种子首词一致", first === second, first + " / " + second);

  check("页面无 JS 报错", errors.length === 0, errors.slice(0, 2).join(" | "));
  console.log("\n结果：" + (failures === 0 ? "全部通过" : failures + " 项失败"));

  ws.close();
  chrome.kill();
  process.exit(failures === 0 ? 0 : 1);
})().catch((error) => {
  console.error("0.1.3 E2E 失败：", error.message);
  chrome.kill();
  process.exit(2);
});
