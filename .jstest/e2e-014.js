/* 0.1.4 端到端：首次向导、词性转换选择树、卡片正反面、跳转与进度、字号 */
const { spawn } = require("child_process");
const os = require("os");
const path = require("path");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.LF_BASE || "http://127.0.0.1:5055";
const PASSWORD = process.env.LF_PASSWORD || "test-settings-password";
const PORT = Number(process.env.LF_CDP_PORT || 9339);
const profile = path.join(os.tmpdir(), "lf-014-" + Date.now());

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

  /* ---------------------------------------------------------- 首次向导 */
  await goto(BASE + "/");
  await sleep(600);
  check(
    "首次访问从首页跳到设置页向导",
    (await evaluate("location.pathname")) === "/settings" &&
      (await evaluate("location.search")).indexOf("wizard=1") !== -1,
    await evaluate("location.pathname + location.search")
  );
  check(
    "向导弹窗自动打开",
    await waitFor("document.getElementById('wizard') && document.getElementById('wizard').open")
  );
  check(
    "向导有 3 个步骤",
    (await evaluate("document.querySelectorAll('#wizardSteps li').length")) === 3
  );
  check(
    "当前是第 1 步",
    (await evaluate("document.querySelector('#wizardSteps li.is-active').textContent")).indexOf(
      "选择词书"
    ) !== -1
  );
  await evaluate("document.getElementById('wizardNext').click();true");
  await sleep(500);
  check(
    "进入第 2 步",
    (await evaluate("document.querySelector('#wizardSteps li.is-active').textContent")).indexOf(
      "闪示节奏"
    ) !== -1
  );
  await evaluate("document.getElementById('wizardNext').click();true");
  await sleep(500);
  check(
    "最后一步按钮变成开始学习",
    (await evaluate(
      "document.querySelector('#wizardNext .primary-action__label').textContent"
    )) === "开始学习"
  );
  await evaluate("document.getElementById('wizardPull').click();true");
  await waitFor(
    "document.getElementById('toastRegion') && document.getElementById('toastRegion').textContent.indexOf('服务器') !== -1",
    8000
  );
  check(
    "向导可以从服务器拉取配置",
    (await evaluate("document.getElementById('toastRegion').textContent")).indexOf("服务器") !== -1,
    await evaluate("document.getElementById('toastRegion').textContent")
  );
  await evaluate("document.getElementById('wizardNext').click();true");
  await waitFor("location.pathname === '/'", 8000);
  check("向导完成后回到闪卡页", (await evaluate("location.pathname")) === "/");
  check(
    "向导完成标记写入本机",
    (await evaluate("localStorage.getItem('littleflash.wizard.v1')")) === "done"
  );

  /* ------------------------------------------------------- 管理员设置页 */
  await goto(BASE + "/settings");
  await evaluate(
    "document.getElementById('settingsPassword').value=" +
      JSON.stringify(PASSWORD) +
      ";document.getElementById('loginForm').requestSubmit();true"
  );
  check(
    "管理员登录成功",
    await waitFor(
      "document.querySelector('meta[name=settings-mode]') && document.querySelector('meta[name=settings-mode]').content === 'server'",
      10000
    )
  );
  await evaluate(
    "fetch('/api/settings/reset',{method:'POST',headers:{'X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content}}).then(r=>r.json()).then(d=>!!d.ok)"
  );
  await goto(BASE + "/settings");
  await waitFor("document.querySelectorAll('#bookPicker .book-pick').length >= 8");

  check(
    "词性转换有独立的树状选择区",
    (await evaluate("document.querySelectorAll('.book-pick--formation .formation-group').length")) === 4
  );
  check(
    "分组标题包含四个大类",
    (await evaluate(
      "Array.from(document.querySelectorAll('.formation-group__title')).map(n=>n.textContent).join(',')"
    )) === "名词空,形容词空,代词,动词空",
    await evaluate(
      "Array.from(document.querySelectorAll('.formation-group__title')).map(n=>n.textContent).join(',')"
    )
  );
  check(
    "变副词区分常规与非常规",
    (await evaluate(
      "Array.from(document.querySelectorAll('.book-pick--formation .formation-unit')).filter(n=>n.textContent.indexOf('形容词变副词')===0).length"
    )) === 2
  );
  check(
    "代词只剩一组",
    (await evaluate(
      "Array.from(document.querySelectorAll('.formation-group')).find(g=>g.textContent.indexOf('代词')!==-1).querySelectorAll('.formation-unit').length"
    )) === 1
  );

  check(
    "未选中的大组默认折叠，省出纵向空间",
    (await evaluate(
      "Array.from(document.querySelectorAll('.formation-group')).every(g=>!g.classList.contains('is-open'))"
    ))
  );
  await evaluate(
    "document.querySelector('.formation-group .formation-group__toggle').click();true"
  );
  await sleep(300);
  check(
    "点标题可以展开大组",
    (await evaluate(
      "document.querySelector('.formation-group').classList.contains('is-open')"
    ))
  );
  check(
    "展开后组内横向排列",
    (await evaluate(
      "getComputedStyle(document.querySelector('.formation-group__units')).display"
    )) === "grid"
  );
  await evaluate(
    "document.querySelector('.formation-group .formation-group__toggle').click();true"
  );
  await sleep(200);

  /* 勾选「名词空」整组 */
  await evaluate(
    "var b=document.querySelector('.book-pick--formation .book-pick__check');b.checked=true;b.dispatchEvent(new Event('change',{bubbles:true}));true"
  );
  await sleep(400);
  await evaluate(
    "var g=Array.from(document.querySelectorAll('.formation-group')).find(x=>x.textContent.indexOf('名词空')!==-1);var c=g.querySelector('.formation-group__check');c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}));true"
  );
  check(
    "组头全选会勾上整组单元",
    await waitFor(
      "Array.from(document.querySelectorAll('.formation-group')).find(x=>x.textContent.indexOf('名词空')!==-1).querySelectorAll('.unit-pick__check:checked').length === 9",
      6000
    )
  );
  check(
    "组头显示为已选",
    await evaluate(
      "Array.from(document.querySelectorAll('.formation-group')).find(x=>x.textContent.indexOf('名词空')!==-1).querySelector('.formation-group__check').checked"
    )
  );
  check(
    "保存成功",
    await waitFor("document.getElementById('saveStatus').dataset.state === 'saved'", 10000)
  );

  const savedSel = await evaluate(
    "fetch('/api/bootstrap').then(r=>r.json()).then(d=>{var s=d.preferences.selections.find(x=>x.wordbook_id==='pep2019-word-formation');return s?s.unit_ids.length:0})"
  );
  check("9 个单元写入服务器", savedSel === 9, String(savedSel));

  /* 字号与卡片行为 */
  await evaluate(
    "fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content},body:JSON.stringify({selections:[{wordbook_id:'pep2019-word-formation',unit_ids:['deck-01','deck-02','deck-03','deck-04','deck-05','deck-06','deck-07','deck-08','deck-09']}],mode:'manual',interval_seconds:30,audio_enabled:false,font_scale:1.4})}).then(r=>r.json()).then(d=>!!d.ok)"
  );
  await goto(BASE + "/");
  await waitFor("document.getElementById('cardMain') && !document.getElementById('cardMain').hidden");
  check(
    "首页使用服务器上选中的词性转换",
    (await evaluate("document.getElementById('bookTitle').textContent")).indexOf("词性转换") !== -1
  );
  const frontWord = await evaluate("document.getElementById('cardLinesWord').textContent");
  check("卡片正面显示原词", frontWord === "finance", frontWord);
  check(
    "正面标注 PROMPT",
    (await evaluate("document.getElementById('faceLabelText').textContent")).indexOf("正面") !== -1
  );
  check(
    "字号设置生效",
    (await evaluate(
      "getComputedStyle(document.documentElement).getPropertyValue('--lf-scale').trim()"
    )) === "1.4"
  );
  await evaluate("document.getElementById('flashcard').click();true");
  await sleep(400);
  check(
    "卡片背面显示变形词",
    (await evaluate("document.getElementById('cardLinesWord').textContent")) === "financial"
  );
  check(
    "背面显示构词方式",
    (await evaluate("document.getElementById('cardLinesExtra').textContent")).indexOf("-cial") !== -1
  );

  /* 跳到第 30 个词 */
  await evaluate(
    "document.getElementById('jumpInput').value='30';document.getElementById('jumpForm').requestSubmit();true"
  );
  await sleep(600);
  check(
    "跳转到第 30 个单词",
    (await evaluate("document.getElementById('bookProgressText').textContent")).indexOf("30 / 90") !== -1,
    await evaluate("document.getElementById('bookProgressText').textContent")
  );

  await goto(BASE + "/");
  await waitFor("document.getElementById('cardMain') && !document.getElementById('cardMain').hidden");
  check(
    "刷新后恢复学习进度",
    (await evaluate("document.getElementById('bookProgressText').textContent")).indexOf("30 / 90") !== -1,
    await evaluate("document.getElementById('bookProgressText').textContent")
  );

  /* 回归：普通词书仍正常 */
  await goto(BASE + "/settings");
  await waitFor("document.querySelector('meta[name=csrf-token]') !== null");
  await evaluate(
    "fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name=csrf-token]').content},body:JSON.stringify({selections:[{wordbook_id:'pep2019-optional-4',unit_ids:['u1']}],font_scale:1})}).then(r=>r.json()).then(d=>!!d.ok)"
  );
  await goto(BASE + "/");
  await waitFor("document.getElementById('cardMain') && !document.getElementById('cardMain').hidden");
  check(
    "普通词书仍显示大号单词",
    await evaluate(
      "!document.getElementById('wordBlock').hidden && document.getElementById('wordText').textContent.length > 0"
    )
  );
  check(
    "普通词书不显示卡片块",
    await evaluate("document.getElementById('cardLines').hidden")
  );

  check("页面没有 JS 异常", errors.length === 0, errors.join(" | ").slice(0, 400));

  console.log(failures === 0 ? "\nALL PASS" : "\n" + failures + " FAILED");
  chrome.kill();
  process.exit(failures === 0 ? 0 : 1);
})().catch((error) => {
  console.error(error);
  chrome.kill();
  process.exit(1);
});
