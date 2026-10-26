/* 本地端到端检查：登录、设置保存到账号、学习进度同步、管理员页面 */
const { spawn } = require("child_process");
const os = require("os");
const path = require("path");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.LF_BASE || "http://127.0.0.1:5055";
const PORT = Number(process.env.LF_CDP_PORT || 9334);
const profile = path.join(os.tmpdir(), "lf-chrome-" + Date.now());

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
  const targetId = created.result.targetId;
  const attached = await send("Target.attachToTarget", { targetId, flatten: true });
  const sessionId = attached.result.sessionId;

  const errors = [];
  listeners.push((msg) => {
    if (msg.sessionId !== sessionId) return;
    if (msg.method === "Runtime.exceptionThrown") {
      const d = msg.params.exceptionDetails;
      errors.push((d.exception ? d.exception.description : d.text) + " @ " + (d.url || ""));
    }
    if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
      errors.push(msg.params.entry.text + " @ " + (msg.params.entry.url || ""));
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
    await sleep(700);
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

  async function loginAs(username, password) {
    await goto(BASE + "/login");
    await evaluate(
      "document.getElementById('loginUsername').value=" +
        JSON.stringify(username) +
        ";document.getElementById('loginPassword').value=" +
        JSON.stringify(password) +
        ";document.getElementById('loginForm').requestSubmit();true"
    );
    return waitFor("location.pathname === '/' && !!document.querySelector('.header-username')", 12000);
  }

  /* ------------------------------------------------------------ 1. 登录 */
  check("登录页可打开", await (async () => {
    await goto(BASE + "/login");
    return (await evaluate("document.body.innerText")).includes("登录闪词");
  })());

  check("普通用户可通过表单登录", await loginAs("240101", "240101"));
  check(
    "页头显示当前用户名",
    (await evaluate("document.querySelector('.header-username').textContent")).trim() === "240101",
    await evaluate("document.querySelector('.header-username').textContent")
  );

  /* ------------------------------------------------------- 2. 首页与进度 */
  // 固定为「先英后中 · 顺序 · 手动」，让后续断言可复现
  await evaluate(
    "fetch('/api/settings',{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':window.LF.csrfToken},body:JSON.stringify({front:'en',order:'sequence',mode:'manual',unit_id:'all'})}).then(r=>r.json())"
  );

  await goto(BASE + "/");
  check(
    "首页加载出单词",
    await waitFor("document.getElementById('wordText') && document.getElementById('wordText').textContent.length > 0", 12000)
  );
  const firstWord = await evaluate("document.getElementById('wordText').textContent");
  check("首页显示首个单词", !!firstWord, firstWord);

  await sleep(13000);
  const progress = await evaluate(
    "fetch('/api/progress',{credentials:'same-origin'}).then(r=>r.json())"
  );
  check(
    "学习时长已上报到服务器",
    progress && progress.progress && progress.progress.totalSeconds >= 10,
    progress && progress.progress ? progress.progress.totalSeconds + " 秒" : "无"
  );

  // 用已知的第 4 个单词写入进度（在无进度上报的页面操作，避免被 pagehide 覆盖），刷新后应回到该词
  await goto(BASE + "/account");
  const target = await evaluate(
    "fetch('/api/bootstrap',{credentials:'same-origin'}).then(r=>r.json()).then(function(d){return fetch('/api/wordbooks/'+encodeURIComponent(d.preferences.wordbook_id),{credentials:'same-origin'}).then(function(r){return r.json();}).then(function(b){var words=[];(b.book.units||[]).forEach(function(u){(u.words||[]).forEach(function(w){words.push(w.en);});});var index=3;return fetch('/api/progress',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':window.LF.csrfToken},body:JSON.stringify({wordbook_id:b.book.id,unit_id:'all',word_index:index,word_en:words[index],rounds:2,seconds_delta:0})}).then(function(){return {word:words[index],index:index};});});})"
  );

  await goto(BASE + "/");
  await waitFor(
    "document.getElementById('wordText') && document.getElementById('wordText').textContent.length > 0",
    10000
  );
  const resumedWord = await evaluate("document.getElementById('wordText').textContent");
  check(
    "刷新后回到上次学习的位置",
    !!target && resumedWord === target.word,
    (target ? target.word + " (#" + target.index + ")" : "") + " / 实际 " + resumedWord
  );
  check(
    "轮次也一并恢复",
    (await evaluate("document.getElementById('roundBadge').textContent")).indexOf("2") !== -1,
    await evaluate("document.getElementById('roundBadge').textContent")
  );

  await goto(BASE + "/account");
  const secondsText = await evaluate("document.getElementById('statSeconds').textContent");
  check("学习档案显示累计时长", secondsText.indexOf("分") !== -1 && secondsText.indexOf("0 分") !== 0, secondsText.trim());
  check(
    "学习档案显示当前词书与进度",
    (await evaluate("document.getElementById('statBook').textContent")).trim().length > 0 &&
      (await evaluate("document.getElementById('statWord').textContent")).indexOf("第 4 个") !== -1,
    await evaluate("document.getElementById('statWord').textContent")
  );

  /* --------------------------------------------------------- 3. 设置页 */
  await goto(BASE + "/settings");
  check(
    "已登录时设置页显示账号模式",
    (await evaluate("document.body.innerText")).includes("已登录 · 设置会保存到你的账号")
  );
  check(
    "设置模式标记为 server",
    (await evaluate("document.querySelector('meta[name=\"settings-scope\"]').content")) === "user"
  );
  check(
    "保存状态显示已保存到我的账号",
    (await evaluate("document.getElementById('saveStatusText').textContent")).includes("我的账号")
  );

  await evaluate(
    "document.querySelector('input[name=\"front\"][value=\"zh\"]').click();true"
  );
  const savedToAccount = await waitFor(
    "document.getElementById('saveStatusText').textContent.includes('我的账号') && document.getElementById('saveStatus').dataset.state === 'saved'",
    8000
  );
  check("修改设置后自动保存", savedToAccount);

  const serverPrefs = await evaluate(
    "fetch('/api/bootstrap',{credentials:'same-origin'}).then(r=>r.json()).then(d=>d.preferences)"
  );
  check("设置已写入账号", serverPrefs && serverPrefs.front === "zh", serverPrefs && serverPrefs.front);

  /* ------------------------------------------------------------ 4. 退出 */
  await evaluate("document.getElementById('logoutBtn').click();true");
  await sleep(1500);
  check(
    "退出后回到本地模式",
    (await evaluate("document.body.innerText")).includes("本地模式")
  );
  check(
    "未登录访客仍是站点默认（先英后中）",
    (await evaluate(
      "fetch('/api/bootstrap',{credentials:'same-origin'}).then(r=>r.json()).then(d=>d.preferences.front)"
    )) === "en"
  );

  /* ------------------------------------------------------- 5. 管理员 */
  check("管理员可登录", await loginAs("admin", "DevAdmin2026"));
  await goto(BASE + "/admin");
  check("用户管理页加载数据", await waitFor("document.querySelectorAll('#userRows tr').length > 0", 12000));
  const rowCount = await evaluate("document.querySelectorAll('#userRows tr').length");
  check("用户表格有数据行", rowCount > 0, rowCount + " 行");
  check(
    "统计卡片已填充",
    (await evaluate("document.getElementById('summaryTotal').textContent")) !== "—"
  );

  await evaluate(
    "var s=document.getElementById('userSearch');s.value='240101';s.dispatchEvent(new Event('input',{bubbles:true}));true"
  );
  await sleep(500);
  const filtered = await evaluate("document.querySelectorAll('#userRows tr').length");
  check("搜索可筛选账号", filtered === 1, filtered + " 行");

  await evaluate("document.querySelector('#userRows .row-button').click();true");
  await sleep(300);
  check(
    "重置密码对话框可打开",
    await evaluate("document.getElementById('passwordDialog').open === true")
  );
  check(
    "对话框显示目标账号",
    (await evaluate("document.getElementById('passwordDialogLead').textContent")).includes("240101")
  );
  await evaluate("document.getElementById('cancelResetPassword').click();true");
  await sleep(200);
  check(
    "对话框可关闭",
    await evaluate("document.getElementById('passwordDialog').open === false")
  );

  /* ------------------------------------------------------- 6. 用户中心 */
  await goto(BASE + "/account");
  check("管理员的空档案也能打开", await waitFor("!!document.getElementById('statSeconds')"));
  const adminSecondsText = await evaluate("document.getElementById('statSeconds').textContent");
  check("无进度时累计时长为 0", adminSecondsText.trim() === "0 分", adminSecondsText.trim());

  check("页面无 JS 报错", errors.length === 0, errors.slice(0, 2).join(" | "));

  console.log("\n结果：" + (failures === 0 ? "全部通过" : failures + " 项失败"));
  ws.close();
  chrome.kill();
  process.exit(failures === 0 ? 0 : 1);
})().catch((error) => {
  console.error("E2E 失败：", error.message);
  chrome.kill();
  process.exit(2);
});
