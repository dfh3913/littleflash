/* LittleFlash standalone · 无后端单文件版
   数据由构建脚本嵌入 #lf-data；设置保存在 localStorage。 */
(function () {
  "use strict";

  const $ = function (id) {
    return document.getElementById(id);
  };

  const dataNode = $("lf-data");
  if (!dataNode) return;

  let RAW;
  try {
    RAW = JSON.parse(dataNode.textContent);
  } catch (error) {
    return;
  }

  const STORE_KEY = "littleflash.standalone.v1";
  const SEED_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const BOOKS = (RAW.books || []).map(function (book) {
    return {
      id: book.i,
      title: book.t,
      stage: book.s || "",
      edition: book.e || "",
      units: (book.u || []).map(function (unit) {
        return {
          id: unit.i,
          title: unit.t,
          words: (unit.w || []).map(function (word) {
            return { en: word[0], zh: word[1] || "", phonetic: word[2] || "" };
          }),
        };
      }),
    };
  });

  const state = {
    prefs: null,
    deck: [],
    index: 0,
    face: 0,
    round: 1,
    mode: "auto",
    order: "sequence",
    seed: "",
    interval: 4,
    kindOrder: ["en", "zh"],
    autoRunning: true,
    settingsOpen: false,
    timer: null,
    ready: false,
    audioWord: "",
  };

  const audio = new Audio();
  audio.preload = "auto";

  function generateSeed() {
    const buffer = new Uint32Array(8);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(buffer);
    } else {
      for (let i = 0; i < buffer.length; i += 1) {
        buffer[i] = Math.floor(Math.random() * 0xffffffff);
      }
    }
    let text = "";
    for (let i = 0; i < buffer.length; i += 1) {
      text += SEED_ALPHABET.charAt(buffer[i] % SEED_ALPHABET.length);
    }
    return text;
  }

  function sanitizeSeed(value, fallback) {
    if (typeof value !== "string") return fallback === undefined ? "" : fallback;
    const text = value
      .replace(/[\u0000-\u001f\u007f]/g, "")
      .trim()
      .slice(0, 64);
    return text || (fallback === undefined ? "" : fallback);
  }

  function defaultPreferences() {
    return {
      selections: BOOKS.length
        ? [{ wordbook_id: BOOKS[0].id, unit_ids: ["all"] }]
        : [],
      front: "en",
      order: "sequence",
      seed: generateSeed(),
      mode: "auto",
      interval_seconds: 4,
      audio_enabled: true,
      audio_volume: 0.8,
    };
  }

  function normalizeSelections(raw) {
    const source = Array.isArray(raw) ? raw : [];
    const byId = new Map();
    BOOKS.forEach(function (book) {
      byId.set(book.id, book);
    });
    const result = [];
    const seen = new Set();
    source.forEach(function (item) {
      if (!item || typeof item !== "object") return;
      const book = byId.get(item.wordbook_id);
      if (!book || seen.has(book.id)) return;
      const known = new Set(
        book.units.map(function (unit) {
          return unit.id;
        })
      );
      let units = [];
      if (Array.isArray(item.unit_ids) && item.unit_ids.indexOf("all") === -1) {
        units = item.unit_ids.filter(function (unitId) {
          return known.has(unitId);
        });
      }
      if (!units.length) units = ["all"];
      seen.add(book.id);
      result.push({ wordbook_id: book.id, unit_ids: units });
    });
    if (!result.length && BOOKS.length) {
      result.push({ wordbook_id: BOOKS[0].id, unit_ids: ["all"] });
    }
    return result;
  }

  function loadPreferences() {
    let stored = null;
    try {
      stored = JSON.parse(window.localStorage.getItem(STORE_KEY) || "null");
    } catch (error) {
      stored = null;
    }
    const base = defaultPreferences();
    if (!stored || typeof stored !== "object") return base;
    return {
      selections: normalizeSelections(stored.selections || (stored.wordbook_id
        ? [{ wordbook_id: stored.wordbook_id, unit_ids: stored.unit_id && stored.unit_id !== "all" ? [stored.unit_id] : ["all"] }]
        : base.selections)),
      front: stored.front === "zh" ? "zh" : "en",
      order: stored.order === "random" || stored.order === "shuffle" ? "random" : "sequence",
      seed: sanitizeSeed(stored.seed, base.seed),
      mode: stored.mode === "manual" ? "manual" : "auto",
      interval_seconds: clampNumber(stored.interval_seconds, 1, 30, 4),
      audio_enabled: stored.audio_enabled !== false,
      audio_volume: clampNumber(stored.audio_volume, 0, 1, 0.8),
    };
  }

  function savePreferences() {
    try {
      window.localStorage.setItem(STORE_KEY, JSON.stringify(state.prefs));
    } catch (error) {
      /* 隐私模式可能不可用 */
    }
  }

  function clampNumber(value, minimum, maximum, fallback) {
    const number = Number(value);
    if (!isFinite(number)) return fallback;
    return Math.min(maximum, Math.max(minimum, number));
  }

  function xmur3(text) {
    let h = 1779033703 ^ text.length;
    for (let i = 0; i < text.length; i += 1) {
      h = Math.imul(h ^ text.charCodeAt(i), 3432918353);
      h = (h << 13) | (h >>> 19);
    }
    return function () {
      h = Math.imul(h ^ (h >>> 16), 2246822507);
      h = Math.imul(h ^ (h >>> 13), 3266489909);
      h ^= h >>> 16;
      return h >>> 0;
    };
  }

  function seededShuffle(array, seed) {
    const next = xmur3(seed);
    let a = next();
    const random = function () {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    for (let i = array.length - 1; i > 0; i -= 1) {
      const j = Math.floor(random() * (i + 1));
      const value = array[i];
      array[i] = array[j];
      array[j] = value;
    }
    return array;
  }

  function roundSeed(round) {
    return state.seed + "#" + round;
  }

  function applyPreferences() {
    const prefs = state.prefs;
    state.mode = prefs.mode;
    state.order = prefs.order;
    state.seed = sanitizeSeed(prefs.seed, generateSeed());
    state.interval = clampNumber(prefs.interval_seconds, 1, 30, 4);
    state.kindOrder = prefs.front === "zh" ? ["zh", "en"] : ["en", "zh"];
    state.autoRunning = prefs.mode === "auto";
  }

  function buildDeck() {
    const deck = [];
    const byId = new Map();
    BOOKS.forEach(function (book) {
      byId.set(book.id, book);
    });
    state.prefs.selections.forEach(function (selection) {
      const book = byId.get(selection.wordbook_id);
      if (!book) return;
      const units =
        selection.unit_ids.indexOf("all") !== -1
          ? book.units
          : book.units.filter(function (unit) {
              return selection.unit_ids.indexOf(unit.id) !== -1;
            });
      units.forEach(function (unit) {
        unit.words.forEach(function (word) {
          if (!word.en) return;
          deck.push({
            en: word.en,
            zh: word.zh,
            phonetic: word.phonetic,
            bookTitle: book.title,
            unitTitle: unit.title,
          });
        });
      });
    });
    state.deck = deck;
    state.index = 0;
    state.face = 0;
    state.round = 1;
    state.ready = deck.length > 0;
    if (state.ready && state.order === "random") {
      seededShuffle(state.deck, roundSeed(1));
    }
    renderAll();
  }

  function currentWord() {
    return state.deck[state.index] || null;
  }

  function renderAll() {
    const word = currentWord();
    if (!word) {
      $("cardState").hidden = false;
      $("cardMain").hidden = true;
      $("cardStateTitle").textContent = "这个范围还没有单词";
      $("cardStateMessage").textContent = "在设置里多选一本词书或几个单元试试。";
      return;
    }
    $("cardState").hidden = true;
    $("cardMain").hidden = false;
    renderFace(false);
    updateProgress();
    updatePace();
    updatePlayer();
    updateBookHeader();
  }

  function updateBookHeader() {
    const selections = state.prefs.selections;
    if (selections.length === 1) {
      const book = BOOKS.find(function (item) {
        return item.id === selections[0].wordbook_id;
      });
      if (book) {
        $("bookTitle").textContent = book.title;
        $("bookStage").textContent = [book.stage, book.edition].filter(Boolean).join(" · ") || "本地词书";
      }
    } else {
      $("bookTitle").textContent = "多本词书";
      $("bookStage").textContent = selections.length + " 本词书";
    }
    $("bookCount").textContent = state.deck.length + " 个单词";
  }

  function renderFace(animate) {
    const word = currentWord();
    if (!word) return;
    const kind = state.kindOrder[state.face];
    $("flashcard").dataset.face = kind;
    $("cardWatermark").textContent = kind === "zh" ? "中" : "Aa";

    if (kind === "zh") {
      $("wordBlock").hidden = true;
      $("meaningBlock").hidden = false;
      $("faceLabelText").textContent = "中文释义";
      $("meaningText").textContent = word.zh || "暂无释义";
      $("cardTipText").textContent = state.face === 0 ? "显示英文" : "下一个";
    } else {
      $("wordBlock").hidden = false;
      $("meaningBlock").hidden = true;
      $("faceLabelText").textContent = "ENGLISH";
      $("wordText").textContent = word.en;
      $("phoneticText").textContent = word.phonetic || "";
      $("phoneticText").hidden = !word.phonetic;
      $("cardTipText").textContent = state.face === 0 ? "点击翻面" : "下一个";
    }
    $("unitLabel").textContent = word.unitTitle;
    $("unitLabel").title = [word.bookTitle, word.unitTitle].filter(Boolean).join(" · ");

    if (animate && !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches)) {
      $("cardContent").classList.remove("is-updating");
      void $("cardContent").offsetWidth;
      $("cardContent").classList.add("is-updating");
    }
  }

  function updateProgress() {
    const total = state.deck.length;
    $("roundBadge").textContent = "第 " + state.round + " 轮";
    if (!total) return;
    const current = state.index + 1;
    const digits = Math.max(2, String(total).length);
    $("bookProgressText").textContent =
      String(current).padStart(digits, "0") + " / " + String(total).padStart(digits, "0");
    const percent = Math.round((current / total) * 100);
    $("bookProgress").style.width = percent + "%";
    $("cardWordProgress").style.width = percent + "%";
  }

  function updatePace() {
    $("paceText").textContent =
      state.mode === "manual" ? "手动 · 点击翻面" : "自动 · " + formatNumber(state.interval) + " 秒";
  }

  function updatePlayer() {
    const playing =
      state.mode === "auto" && state.autoRunning && !state.settingsOpen;
    $("playLabel").textContent = playing ? "暂停" : "继续";
    $("iconPause").hidden = !playing;
    $("iconPlay").hidden = playing;
    $("playerHint").textContent = state.settingsOpen
      ? "设置中 · 闪词已暂停"
      : state.mode === "manual"
        ? "手动模式 · 空格翻面"
        : playing
          ? "正在按节奏切换"
          : "已暂停";
    $("liveDot").classList.toggle("is-paused", !playing);
  }

  function formatNumber(value) {
    const number = Number(value);
    return Number.isInteger(number) ? String(number) : number.toFixed(1);
  }

  function clearTimer() {
    if (state.timer) {
      window.clearTimeout(state.timer);
      state.timer = null;
    }
  }

  function schedule() {
    clearTimer();
    if (!state.ready || state.mode !== "auto" || !state.autoRunning) return;
    if (state.settingsOpen) return;
    state.timer = window.setTimeout(function () {
      state.timer = null;
      if (state.face === 0) {
        state.face = 1;
        renderFace(true);
        updatePlayer();
      } else {
        nextWord();
      }
    }, Math.max(1000, Math.round(state.interval * 1000)));
  }

  function showFace(face) {
    state.face = face === 1 ? 1 : 0;
    renderFace(true);
    playAudio();
    schedule();
  }

  function nextWord() {
    if (!state.ready) return;
    clearTimer();
    const total = state.deck.length;
    if (state.index === total - 1 && total > 1) {
      state.round += 1;
      if (state.order === "random") {
        const previous = state.deck[state.index];
        seededShuffle(state.deck, roundSeed(state.round));
        if (state.deck[0] === previous) {
          state.deck[0] = state.deck[1];
          state.deck[1] = previous;
        }
      }
    }
    state.index = (state.index + 1) % total;
    state.face = 0;
    renderFace(true);
    updateProgress();
    updatePlayer();
    playAudio();
    schedule();
  }

  function previousWord() {
    if (!state.ready) return;
    clearTimer();
    state.index = (state.index - 1 + state.deck.length) % state.deck.length;
    state.face = 0;
    renderFace(true);
    updateProgress();
    updatePlayer();
    playAudio();
    schedule();
  }

  function togglePlay() {
    if (!state.ready) return;
    if (state.mode === "manual") {
      if (state.face === 0) showFace(1);
      else nextWord();
      return;
    }
    state.autoRunning = !state.autoRunning;
    if (state.autoRunning) {
      updatePlayer();
      playAudio();
      schedule();
    } else {
      clearTimer();
      audio.pause();
      updatePlayer();
    }
  }

  function syncSoundUi() {
    const enabled = Boolean(state.prefs && state.prefs.audio_enabled);
    const button = $("soundToggle");
    if (!button) return;
    button.setAttribute("aria-pressed", enabled ? "true" : "false");
    button.setAttribute("aria-label", enabled ? "关闭发音" : "开启发音");
    button.title = enabled ? "关闭发音" : "开启发音";
    const on = button.querySelector(".sound-on");
    const off = button.querySelector(".sound-off");
    if (on) on.hidden = !enabled;
    if (off) off.hidden = enabled;
  }

  function playAudio() {
    const word = currentWord();
    if (!word || !state.prefs.audio_enabled) return;    const url =
      "https://dict.youdao.com/dictvoice?audio=" +
      encodeURIComponent(word.en) +
      "&type=2";
    try {
      if (state.audioWord !== word.en) {
        audio.src = url;
        state.audioWord = word.en;
      } else {
        audio.currentTime = 0;
      }
      audio.volume = clampNumber(state.prefs.audio_volume, 0, 1, 0.8);
      const promise = audio.play();
      if (promise && promise.catch) promise.catch(function () {});
    } catch (error) {
      /* 浏览器可能阻止自动播放 */
    }
  }

  /* ------------------------------------------------------------ 设置面板 */
  function openSettings() {
    // 打开设置即暂停闪词，避免卡片在面板后面自己翻动。
    state.settingsOpen = true;
    clearTimer();
    audio.pause();
    renderSettings();
    updatePlayer();
    $("settingsPanel").hidden = false;
    document.body.style.overflow = "hidden";
  }

  function closeSettings() {
    $("settingsPanel").hidden = true;
    document.body.style.overflow = "";
    state.settingsOpen = false;
    updatePlayer();
    schedule();
  }

  function renderSettings() {
    const prefs = state.prefs;
    $("stFront").value = prefs.front;
    $("stOrder").value = prefs.order;
    $("stMode").value = prefs.mode;
    $("stSeed").value = prefs.seed;
    $("stInterval").value = String(prefs.interval_seconds);
    $("stIntervalValue").textContent = formatNumber(prefs.interval_seconds);
    $("stAudio").checked = prefs.audio_enabled;
    $("stVolume").value = String(Math.round(prefs.audio_volume * 100));
    $("stSeedField").hidden = prefs.order !== "random";

    const container = $("standaloneBooks");
    container.textContent = "";
    BOOKS.forEach(function (book) {
      const selection = prefs.selections.find(function (item) {
        return item.wordbook_id === book.id;
      });
      const wrap = document.createElement("div");
      wrap.className = "standalone-book";

      const head = document.createElement("label");
      head.className = "standalone-book__head";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = Boolean(selection);
      check.setAttribute("data-book", book.id);
      const title = document.createElement("span");
      title.textContent = book.title + " · " + book.units.length + " 单元";
      head.appendChild(check);
      head.appendChild(title);
      wrap.appendChild(head);

      const units = document.createElement("div");
      units.className = "standalone-book__units";
      units.hidden = !selection;
      book.units.forEach(function (unit) {
        const label = document.createElement("label");
        label.className = "standalone-unit";
        const unitCheck = document.createElement("input");
        unitCheck.type = "checkbox";
        unitCheck.value = unit.id;
        unitCheck.setAttribute("data-book", book.id);
        unitCheck.checked =
          Boolean(selection) &&
          (selection.unit_ids.indexOf("all") !== -1 ||
            selection.unit_ids.indexOf(unit.id) !== -1);
        if (unitCheck.checked) label.classList.add("is-checked");
        const text = document.createElement("span");
        text.textContent = unit.title;
        label.appendChild(unitCheck);
        label.appendChild(text);
        units.appendChild(label);
      });
      wrap.appendChild(units);
      container.appendChild(wrap);
    });
    updateSettingsSummary();
  }

  function readSelectionsFromPanel() {
    const result = [];
    document.querySelectorAll("#standaloneBooks .standalone-book").forEach(function (bookEl) {
      const bookCheck = bookEl.querySelector(".standalone-book__head input");
      if (!bookCheck || !bookCheck.checked) return;
      const units = Array.prototype.slice
        .call(bookEl.querySelectorAll(".standalone-unit input"))
        .filter(function (input) {
          return input.checked;
        });
      if (!units.length) return;
      const all = units.length === bookEl.querySelectorAll(".standalone-unit input").length;
      result.push({
        wordbook_id: bookCheck.getAttribute("data-book"),
        unit_ids: all
          ? ["all"]
          : units.map(function (input) {
              return input.value;
            }),
      });
    });
    return result;
  }

  /* 已勾选但还没点选任何单元的词书数量 */
  function pendingBookCount() {
    let count = 0;
    document.querySelectorAll("#standaloneBooks .standalone-book").forEach(function (bookEl) {
      const head = bookEl.querySelector(".standalone-book__head input");
      if (!head || !head.checked) return;
      if (!bookEl.querySelector(".standalone-unit input:checked")) count += 1;
    });
    return count;
  }

  function updateSettingsSummary() {
    const selections = readSelectionsFromPanel();
    let units = 0;
    let words = 0;
    selections.forEach(function (selection) {
      const book = BOOKS.find(function (item) {
        return item.id === selection.wordbook_id;
      });
      if (!book) return;
      book.units.forEach(function (unit) {
        if (
          selection.unit_ids.indexOf("all") !== -1 ||
          selection.unit_ids.indexOf(unit.id) !== -1
        ) {
          units += 1;
          words += unit.words.length;
        }
      });
    });
    const pending = pendingBookCount();
    if (selections.length) {
      let text =
        "已选 " + selections.length + " 本词书 · " + units + " 个单元 · " + words + " 个单词";
      // 勾了却没选单元的词书：已选中的照常生效，这里只做提醒。
      if (pending) text += "（另有 " + pending + " 本还没选单元）";
      $("settingsSummary").textContent = text;
    } else {
      $("settingsSummary").textContent = pending
        ? "已勾选 " + pending + " 本词书，还需要点选至少一个单元"
        : "至少选择一本词书";
    }
  }

  function commitSettings(rebuild) {
    const selections = readSelectionsFromPanel();
    if (!selections.length) {
      toast(pendingBookCount() ? "请至少点选一个单元" : "至少选择一本词书");
      updateSettingsSummary();
      return;
    }
    state.prefs = {
      selections: selections,
      front: $("stFront").value === "zh" ? "zh" : "en",
      order: $("stOrder").value === "random" ? "random" : "sequence",
      seed: sanitizeSeed($("stSeed").value, generateSeed()),
      mode: $("stMode").value === "manual" ? "manual" : "auto",
      interval_seconds: clampNumber($("stInterval").value, 1, 30, 4),
      audio_enabled: $("stAudio").checked,
      audio_volume: clampNumber(Number($("stVolume").value) / 100, 0, 1, 0.8),
    };
    if (state.prefs.order === "random" && !$("stSeed").value) {
      state.prefs.seed = generateSeed();
      $("stSeed").value = state.prefs.seed;
    }
    savePreferences();
    applyPreferences();
    syncSoundUi();
    updateSettingsSummary();
    if (rebuild === false) {
      updatePace();
      updatePlayer();
      renderFace(false);
      schedule();
    } else {
      buildDeck();
      schedule();
    }
    if (state.prefs.audio_enabled) playAudio();
    else audio.pause();
    toast("设置已保存到本机");
  }

  function toast(message) {
    let node = $("standaloneToast");
    if (!node) {
      node = document.createElement("div");
      node.id = "standaloneToast";
      node.className = "standalone-toast";
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.classList.add("is-visible");
    window.clearTimeout(node._timer);
    node._timer = window.setTimeout(function () {
      node.classList.remove("is-visible");
    }, 2200);
  }

  /* --------------------------------------------------------------- 事件 */
  function bind() {
    $("prevBtn").addEventListener("click", previousWord);
    $("nextBtn").addEventListener("click", nextWord);
    $("playBtn").addEventListener("click", togglePlay);
    $("replayBtn").addEventListener("click", playAudio);
    $("flashcard").addEventListener("click", function (event) {
      if (event.target.closest("button")) return;
      if (state.face === 0) showFace(1);
      else nextWord();
    });

    $("settingsBtn").addEventListener("click", openSettings);
    $("closeSettings").addEventListener("click", closeSettings);
    $("settingsPanel").addEventListener("click", function (event) {
      if (event.target === $("settingsPanel")) closeSettings();
    });

    $("soundToggle").addEventListener("click", function () {
      state.prefs.audio_enabled = !state.prefs.audio_enabled;
      savePreferences();
      syncSoundUi();
      if (state.prefs.audio_enabled) playAudio();
      else audio.pause();
    });

    $("standaloneBooks").addEventListener("change", function (event) {
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) return;
      const bookEl = target.closest(".standalone-book");
      if (!bookEl) return;
      if (target.closest(".standalone-book__head")) {
        // 勾选词书只展开单元列表，默认不预选任何单元；取消勾选才清空本书。
        if (!target.checked) {
          bookEl.querySelectorAll(".standalone-unit").forEach(function (unitEl) {
            const input = unitEl.querySelector("input");
            if (input) input.checked = false;
            unitEl.classList.remove("is-checked");
          });
        }
        bookEl.querySelector(".standalone-book__units").hidden = !target.checked;
      } else {
        const unitEl = target.closest(".standalone-unit");
        if (!unitEl) return;
        const any = Array.prototype.some.call(
          bookEl.querySelectorAll(".standalone-unit input"),
          function (input) {
            return input.checked;
          }
        );
        bookEl.querySelector(".standalone-book__head input").checked = any;
        bookEl.querySelector(".standalone-book__units").hidden = !any;
        unitEl.classList.toggle("is-checked", target.checked);
      }
      updateSettingsSummary();
      commitSettings();
    });

    $("stOrder").addEventListener("change", function () {
      $("stSeedField").hidden = $("stOrder").value !== "random";
      if ($("stOrder").value === "random" && !sanitizeSeed($("stSeed").value)) {
        $("stSeed").value = generateSeed();
      }
      commitSettings();
    });

    $("stSeedRefresh").addEventListener("click", function () {
      $("stSeed").value = generateSeed();
      commitSettings();
    });

    $("stSeed").addEventListener("input", function () {
      const clean = sanitizeSeed($("stSeed").value);
      if ($("stSeed").value !== clean) $("stSeed").value = clean;
      commitSettings();
    });

    ["stFront", "stMode"].forEach(function (id) {
      $(id).addEventListener("change", function () {
        commitSettings(false);
      });
    });

    $("stAudio").addEventListener("change", function () {
      commitSettings(false);
    });

    $("stInterval").addEventListener("input", function () {
      $("stIntervalValue").textContent = formatNumber($("stInterval").value);
      commitSettings(false);
    });
    $("stVolume").addEventListener("input", function () {
      commitSettings(false);
    });

    $("stReset").addEventListener("click", function () {
      state.prefs = {
        selections: [{ wordbook_id: BOOKS[0].id, unit_ids: ["all"] }],
        front: "en",
        order: "sequence",
        seed: generateSeed(),
        mode: "auto",
        interval_seconds: 4,
        audio_enabled: true,
        audio_volume: 0.8,
      };
      savePreferences();
      applyPreferences();
      renderSettings();
      buildDeck();
      syncSoundUi();
      toast("已恢复默认设置");
    });

    document.addEventListener("keydown", function (event) {
      if ($("settingsPanel").hidden === false) {
        if (event.key === "Escape") closeSettings();
        return;
      }
      if (event.target instanceof Element && event.target.closest("input, select, textarea")) {
        return;
      }
      if (event.code === "Space") {
        event.preventDefault();
        togglePlay();
      } else if (event.code === "ArrowRight") {
        event.preventDefault();
        nextWord();
      } else if (event.code === "ArrowLeft") {
        event.preventDefault();
        previousWord();
      } else if (event.code === "KeyR") {
        playAudio();
      }
    });

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        clearTimer();
      } else if (state.autoRunning) {
        schedule();
      }
    });
  }

  state.prefs = loadPreferences();
  applyPreferences();
  buildDeck();
  bind();
  syncSoundUi();
  schedule();
})();
