/* LittleFlash · flashcard experience */
(function () {
  "use strict";

  const LF = window.LF;
  const $ = function (id) {
    return document.getElementById(id);
  };

  const dom = {
    libraryStatus: $("libraryStatus"),
    libraryStatusText: $("libraryStatusText"),
    soundToggle: $("soundToggle"),
    soundOn: document.querySelector("#soundToggle .sound-on"),
    soundOff: document.querySelector("#soundToggle .sound-off"),
    soundWaitDot: document.querySelector("#soundToggle .sound-wait-dot"),
    bookTitle: $("bookTitle"),
    bookStage: $("bookStage"),
    bookCount: $("bookCount"),
    bookProgress: $("bookProgress"),
    bookProgressText: $("bookProgressText"),
    unitLabel: $("unitLabel"),
    paceChip: $("paceChip"),
    paceText: $("paceText"),
    flashcard: $("flashcard"),
    cardState: $("cardState"),
    cardStateEyebrow: $("cardStateEyebrow"),
    cardStateTitle: $("cardStateTitle"),
    cardStateMessage: $("cardStateMessage"),
    stateLoaderUse: document.querySelector("#stateLoader use"),
    retryLoad: $("retryLoad"),
    emptySettings: $("emptySettings"),
    cardMain: $("cardMain"),
    cardContent: $("cardContent"),
    cardWatermark: $("cardWatermark"),
    faceLabelText: $("faceLabelText"),
    replayBtn: $("replayBtn"),
    wordBlock: $("wordBlock"),
    wordText: $("wordText"),
    phoneticText: $("phoneticText"),
    meaningBlock: $("meaningBlock"),
    meaningText: $("meaningText"),
    cardTipText: $("cardTipText"),
    cardWordProgress: $("cardWordProgress"),
    prevBtn: $("prevBtn"),
    nextBtn: $("nextBtn"),
    playBtn: $("playBtn"),
    iconPause: $("iconPause"),
    iconPlay: $("iconPlay"),
    iconAdvance: $("iconAdvance"),
    playLabel: $("playLabel"),
    playerHint: $("playerHint"),
    playerSpaceHint: $("playerSpaceHint"),
    liveDot: $("liveDot"),
    roundBadge: $("roundBadge"),
  };

  if (!dom.flashcard) return;

  const state = {
    ready: false,
    loading: false,
    books: [],
    prefs: null,
    book: null,
    deck: [],
    index: 0,
    face: 0,
    kindOrder: ["en", "zh"],
    mode: "auto",
    order: "sequence",
    seed: "",
    selectionQuery: "",
    intervalSeconds: 4,
    autoRunning: false,
    round: 1,
    timerId: null,
    serverAudioEnabled: true,
    serverAuthenticated: false,
    muted: false,
    audioUnlocked: false,
    audioBlocked: false,
    audioErrorShown: false,
    currentAudioWord: null,
    audioAttempt: 0,
    documentHidden: document.hidden,
    interactionSeen: false,
  };

  const audio = new Audio();
  audio.preload = "auto";

  /* ------------------------------------------------------------------ boot */
  function init() {
    bindEvents();
    bindAudio();
    installAudioUnlock();
    loadHomeData();
  }

  async function loadHomeData() {
    if (state.loading) return;
    state.loading = true;
    state.ready = false;
    state.book = null;
    state.deck = [];
    state.index = 0;
    state.face = 0;
    state.round = 1;
    clearAutoTimer();
    stopAudio();
    state.audioAttempt += 1;
    state.audioBlocked = false;
    state.audioErrorShown = false;
    state.currentAudioWord = null;

    showLoadingCard();
    setControlsEnabled(false);
    setLibraryStatus("loading", "正在连接词库");

    try {
      const bootstrap = await LF.api("/api/bootstrap", { timeout: 12000 });
      if (!bootstrap || !Array.isArray(bootstrap.books)) {
        throw new LF.ApiError("服务器返回的数据不完整", 0, "invalid_payload");
      }
      state.books = bootstrap.books;
      state.prefs = bootstrap.preferences || {};
      state.serverAuthenticated = Boolean(bootstrap.authenticated);
      // 未登录时优先使用这台浏览器自己的设置（设置页免密码保存的那一份）。
      if (!state.serverAuthenticated) {
        const localPrefs = LF.localPrefs.read();
        if (localPrefs) {
          state.prefs = Object.assign({}, state.prefs, localPrefs);
        }
      }
      state.serverAudioEnabled = Boolean(
        bootstrap.server && bootstrap.server.audioEnabled
      );

      if (state.books.length === 0) {
        showEmptyState(
          "词库里还没有单词",
          "把词书 JSON 放进 src/wordbooks，刷新后会被自动识别。"
        );
        return;
      }

      const selections = LF.normalizeSelections(state.prefs, state.books);
      state.selectionQuery = LF.selectionQuery(selections);
      state.book = summarizeSelection(selections, state.books);

      // 一次拿到当前选择范围内的全部单词，避免逐本词书发请求。
      const response = await LF.api(
        "/api/deck?s=" + encodeURIComponent(state.selectionQuery),
        { timeout: 20000 }
      );
      if (!response || !Array.isArray(response.deck)) {
        throw new LF.ApiError("词书内容不完整", 0, "invalid_wordbook");
      }

      applyPreferences();
      prepareDeck(response.deck);
      if (state.deck.length === 0) {
        showEmptyState(
          "这个范围还没有单词",
          "换一个单元或词书试试，学习范围可以在设置页调整。"
        );
        return;
      }

      state.face = 0;
      state.round = 1;
      state.ready = true;

      setupPage();
      dom.cardState.hidden = true;
      dom.cardMain.hidden = false;
      setControlsEnabled(true);
      renderFace(false);
      updateProgress();
      updateAudioUI();
      updatePlayerUI();
      setLibraryStatus("ready", state.books.length + " 本词书在线");
      document.title =
        state.book.id === "__multi__"
          ? "闪词 · 多本词书"
          : "闪词 · " + state.book.title;

      scheduleAuto();
      window.setTimeout(function () {
        if (state.ready) attemptPlayAudio();
      }, 220);
    } catch (error) {
      console.error("LittleFlash load error", error);
      showLoadError(error);
    } finally {
      state.loading = false;
      dom.flashcard.setAttribute("aria-busy", "false");
    }
  }

  function applyPreferences() {
    const prefs = state.prefs || {};
    state.mode = prefs.mode === "manual" ? "manual" : "auto";
    state.order =
      prefs.order === "random" || prefs.order === "shuffle"
        ? "random"
        : "sequence";
    state.seed = LF.sanitizeSeed(prefs.seed, "littleflash");
    state.intervalSeconds = clampNumber(prefs.interval_seconds, 1, 30, 4);
    state.kindOrder = prefs.front === "zh" ? ["zh", "en"] : ["en", "zh"];
    state.autoRunning = state.mode === "auto";
  }

  function summarizeSelection(selections, books) {
    const byId = new Map();
    books.forEach(function (book) {
      byId.set(book.id, book);
    });
    const chosen = [];
    selections.forEach(function (selection) {
      const book = byId.get(selection.wordbook_id);
      if (book) chosen.push(book);
    });
    if (chosen.length === 1) {
      return chosen[0];
    }
    return {
      id: "__multi__",
      title: "多本词书",
      stage: chosen.length + " 本词书",
      edition: "",
      units: [],
    };
  }

  function prepareDeck(items) {
    const deck = [];
    (items || []).forEach(function (word) {
      if (!word || typeof word.en !== "string" || !word.en) return;
      deck.push({
        en: word.en,
        zh: typeof word.zh === "string" ? word.zh : "",
        phonetic: typeof word.phonetic === "string" ? word.phonetic : "",
        bookId: typeof word.bookId === "string" ? word.bookId : "",
        bookTitle: typeof word.bookTitle === "string" ? word.bookTitle : "",
        unitId: typeof word.unitId === "string" ? word.unitId : "",
        unitTitle: word.unitTitle || "未命名单元",
      });
    });
    state.deck = deck;
    state.index = 0;
    if (state.order === "random") {
      LF.shuffleWithSeed(state.deck, roundSeed(1));
    }
  }

  function roundSeed(round) {
    return state.seed + "#" + round;
  }

  function setupPage() {
    const book = state.book;
    const details = [];
    if (book.id === "__multi__") {
      details.push(book.stage);
    } else {
      const stageLabel = LF.formatStageLabel(book.stage);
      if (stageLabel) details.push(stageLabel);
      if (book.edition) details.push(book.edition);
    }

    dom.bookTitle.textContent = book.title;
    dom.bookTitle.title = book.title;
    dom.bookStage.textContent = details.join(" · ") || "本地词书";
    dom.bookCount.textContent = state.deck.length + " 个单词";
  }

  /* --------------------------------------------------------------- card view */
  function currentWord() {
    return state.deck[state.index] || null;
  }

  function renderFace(animate) {
    const word = currentWord();
    if (!word) return;

    const kind = state.kindOrder[state.face];
    dom.flashcard.dataset.face = kind;
    dom.cardWatermark.textContent = kind === "zh" ? "中" : "Aa";

    if (kind === "zh") {
      dom.wordBlock.hidden = true;
      dom.meaningBlock.hidden = false;
      dom.faceLabelText.textContent = "中文释义";
      dom.meaningText.textContent = word.zh || "暂无释义";
      setTextSize(dom.meaningText, word.zh.length, 32, 80);
      dom.cardTipText.textContent =
        state.face === 0 ? "显示英文" : "下一个";
      dom.flashcard.setAttribute(
        "aria-label",
        "中文释义：" +
          (word.zh || "暂无释义") +
          "。按空格或轻触卡片" +
          (state.face === 0 ? "显示英文" : "进入下一个单词")
      );
    } else {
      dom.wordBlock.hidden = false;
      dom.meaningBlock.hidden = true;
      dom.faceLabelText.textContent = "ENGLISH";
      dom.wordText.textContent = word.en;
      setTextSize(dom.wordText, word.en.length, 11, 19);
      const phonetic = (word.phonetic || "").trim();
      dom.phoneticText.textContent = phonetic;
      dom.phoneticText.hidden = !phonetic;
      dom.cardTipText.textContent =
        state.face === 0 ? "点击翻面" : "下一个";
      dom.flashcard.setAttribute(
        "aria-label",
        "英文单词 " +
          word.en +
          (phonetic ? "，音标 " + phonetic : "") +
          "。按空格或轻触卡片" +
          (state.face === 0 ? "查看中文释义" : "进入下一个单词")
      );
    }

    dom.unitLabel.textContent = word.unitTitle;
    dom.unitLabel.title = [word.bookTitle, word.unitTitle]
      .filter(Boolean)
      .join(" · ");

    if (animate && !LF.prefersReducedMotion()) {
      dom.cardContent.classList.remove("is-updating");
      void dom.cardContent.offsetWidth;
      dom.cardContent.classList.add("is-updating");
    } else {
      dom.cardContent.classList.remove("is-updating");
    }
  }

  function setTextSize(element, length, longThreshold, xlongThreshold) {
    if (!element) return;
    element.removeAttribute("data-length");
    if (length > xlongThreshold) {
      element.setAttribute("data-length", "xlong");
    } else if (length > longThreshold) {
      element.setAttribute("data-length", "long");
    }
  }

  function updateProgress() {
    const total = state.deck.length;
    dom.roundBadge.textContent = "第 " + state.round + " 轮";

    if (!total) {
      dom.bookProgressText.textContent = "— / —";
      dom.bookProgress.style.width = "0%";
      dom.cardWordProgress.style.width = "0%";
      return;
    }

    const current = state.index + 1;
    const digits = Math.max(2, String(total).length);
    const percent = Math.round((current / total) * 100);
    const currentLabel = LF.padNumber(current, digits);
    const totalLabel = LF.padNumber(total, digits);

    dom.bookProgressText.textContent = currentLabel + " / " + totalLabel;
    dom.bookProgress.style.width = percent + "%";
    dom.cardWordProgress.style.width = percent + "%";
  }

  /* ---------------------------------------------------------------- actions */
  function showFace(faceIndex) {
    if (!state.ready) return;
    state.face = faceIndex === 1 ? 1 : 0;
    renderFace(true);
    updatePlayerUI();
    attemptPlayAudio();
    scheduleAuto();
  }

  function stepForward() {
    if (!state.ready) return;
    if (state.face === 0) {
      showFace(1);
    } else {
      nextWord();
    }
  }

  function nextWord() {
    if (!state.ready || state.deck.length === 0) return;
    clearAutoTimer();
    const total = state.deck.length;
    const wasLast = state.index === total - 1;
    if (wasLast && total > 1) {
      state.round += 1;
      if (state.order === "random") {
        reshuffleDeck();
      }
      LF.toast("完成一轮，继续保持这个节奏。", {
        type: "success",
        duration: 2800,
      });
    }
    state.index = (state.index + 1) % total;
    state.face = 0;
    renderFace(true);
    updateProgress();
    updatePlayerUI();
    attemptPlayAudio();
    scheduleAuto();
  }

  function previousWord() {
    if (!state.ready || state.deck.length === 0) return;
    clearAutoTimer();
    const total = state.deck.length;
    state.index = (state.index - 1 + total) % total;
    state.face = 0;
    renderFace(true);
    updateProgress();
    updatePlayerUI();
    attemptPlayAudio();
    scheduleAuto();
  }

  function toggleAutoplay() {
    if (!state.ready) return;
    if (state.mode === "manual") {
      stepForward();
      return;
    }
    state.autoRunning = !state.autoRunning;
    if (state.autoRunning) {
      updatePlayerUI();
      attemptPlayAudio({ force: true });
      scheduleAuto();
    } else {
      clearAutoTimer();
      audio.pause();
      updatePlayerUI();
    }
  }

  /* ------------------------------------------------------------------ audio */
  function bindAudio() {
    audio.addEventListener("playing", function () {
      state.audioBlocked = false;
      dom.replayBtn.classList.add("is-playing");
      updateAudioUI();
      updatePlayerUI();
    });
    audio.addEventListener("pause", function () {
      dom.replayBtn.classList.remove("is-playing");
    });
    audio.addEventListener("ended", function () {
      dom.replayBtn.classList.remove("is-playing");
    });
    audio.addEventListener("error", function () {
      handleAudioFailure();
    });
  }

  function installAudioUnlock() {
    function unlock(event) {
      if (state.interactionSeen) return;
      state.interactionSeen = true;
      state.audioUnlocked = true;
      window.removeEventListener("pointerdown", unlock, true);
      window.removeEventListener("keydown", unlock, true);
      const target = event && event.target;
      if (
        target instanceof Element &&
        target.closest("#soundToggle, #replayBtn")
      ) {
        return;
      }
      if (state.ready && state.audioBlocked) {
        attemptPlayAudio({ force: true });
      } else {
        updateAudioUI();
      }
    }
    window.addEventListener("pointerdown", unlock, true);
    window.addEventListener("keydown", unlock, true);
  }

  function attemptPlayAudio(options) {
    const force = Boolean(options && options.force);
    if (!state.ready || !state.prefs) return;
    if (!state.serverAudioEnabled || !state.prefs.audio_enabled) return;
    if (state.muted || state.documentHidden) return;
    if (state.audioBlocked && !state.audioUnlocked && !force) {
      updateAudioUI();
      return;
    }

    const word = currentWord();
    if (!word) return;
    const url = LF.url("/api/audio?word=" + encodeURIComponent(word.en));
    const attemptId = ++state.audioAttempt;

    try {
      if (state.currentAudioWord !== word.en) {
        audio.src = url;
        state.currentAudioWord = word.en;
      } else {
        audio.pause();
        audio.currentTime = 0;
      }
      audio.volume = clampNumber(state.prefs.audio_volume, 0, 1, 0.8);
    } catch (error) {
      return;
    }

    const promise = audio.play();
    if (!promise || typeof promise.then !== "function") return;
    promise
      .then(function () {
        if (attemptId !== state.audioAttempt) return;
        state.audioBlocked = false;
        state.audioUnlocked = true;
        updateAudioUI();
        updatePlayerUI();
      })
      .catch(function (error) {
        if (attemptId !== state.audioAttempt) return;
        if (error && error.name === "AbortError") return;
        if (error && error.name === "NotAllowedError") {
          markAudioBlocked();
          return;
        }
        handleAudioFailure();
      });
  }

  function markAudioBlocked() {
    state.audioBlocked = true;
    updateAudioUI();
    updatePlayerUI();
  }

  function handleAudioFailure() {
    dom.replayBtn.classList.remove("is-playing");
    if (state.audioErrorShown) return;
    state.audioErrorShown = true;
    LF.toast("发音暂时无法加载，请检查服务器网络后重试。", {
      type: "error",
      duration: 4600,
    });
  }

  function stopAudio() {
    try {
      audio.pause();
      audio.removeAttribute("src");
    } catch (error) {
      /* no-op */
    }
    state.currentAudioWord = null;
    dom.replayBtn.classList.remove("is-playing");
  }

  function updateAudioUI() {
    if (!dom.soundToggle) return;
    const preferenceOn = Boolean(state.prefs && state.prefs.audio_enabled);
    const active = Boolean(state.serverAudioEnabled && preferenceOn);
    const sounding = active && !state.muted;
    let label = "关闭发音";
    if (!state.serverAudioEnabled) {
      label = "服务器未启用发音";
    } else if (!preferenceOn) {
      label = "发音已在设置中关闭";
    } else if (state.muted) {
      label = "开启发音";
    }

    dom.soundToggle.setAttribute("aria-pressed", sounding ? "true" : "false");
    dom.soundToggle.setAttribute("aria-label", label);
    dom.soundToggle.title = label;
    LF.setHidden(dom.soundOn, !sounding);
    LF.setHidden(dom.soundOff, sounding);
    if (dom.soundWaitDot) {
      dom.soundWaitDot.hidden = !(sounding && state.audioBlocked);
    }

    dom.replayBtn.disabled = !sounding || !state.ready;
    dom.replayBtn.title = sounding ? "重播发音（R）" : "当前没有可播放的发音";
  }

  /* ------------------------------------------------------------------ timers */
  function clearAutoTimer() {
    if (state.timerId !== null) {
      window.clearTimeout(state.timerId);
      state.timerId = null;
    }
  }

  function scheduleAuto() {
    clearAutoTimer();
    if (!state.ready) return;
    if (state.mode !== "auto" || !state.autoRunning || state.documentHidden) {
      return;
    }
    const duration = Math.max(1000, Math.round(state.intervalSeconds * 1000));
    state.timerId = window.setTimeout(function () {
      state.timerId = null;
      if (!state.ready || state.documentHidden) return;
      if (state.face === 0) {
        showFace(1);
      } else {
        nextWord();
      }
    }, duration);
  }

  /* -------------------------------------------------------------------- ui */
  function updatePlayerUI() {
    if (!state.prefs) return;
    const auto = state.mode === "auto";
    const running = auto && state.autoRunning;
    const interval = formatInterval(state.intervalSeconds);

    dom.paceChip.classList.toggle("is-manual", !auto);
    dom.paceChip.classList.toggle("is-paused", auto && !state.autoRunning);
    dom.paceText.textContent = auto
      ? "自动 · " + interval + " 秒"
      : "手动 · 点击翻开";

    dom.liveDot.classList.toggle("is-paused", !running);
    dom.playBtn.classList.toggle("is-manual", !auto);

    if (auto) {
      LF.setHidden(dom.iconPause, !state.autoRunning);
      LF.setHidden(dom.iconPlay, state.autoRunning);
      LF.setHidden(dom.iconAdvance, true);
      dom.playLabel.textContent = state.autoRunning ? "暂停" : "继续";
    } else {
      LF.setHidden(dom.iconPause, true);
      LF.setHidden(dom.iconPlay, true);
      LF.setHidden(dom.iconAdvance, false);
      dom.playLabel.textContent =
        state.face === 0
          ? state.kindOrder[0] === "zh"
            ? "显示英文"
            : "翻开释义"
          : "下一个单词";
    }

    let hint = "";
    if (auto) {
      hint = state.autoRunning
        ? "自动闪卡 · 每 " + interval + " 秒切换"
        : "已暂停 · 点击继续或轻触卡片翻面";
    } else {
      hint = "手动模式 · 点击卡片翻开";
    }
    if (
      state.audioBlocked &&
      !state.muted &&
      state.serverAudioEnabled &&
      state.prefs.audio_enabled
    ) {
      hint += " · 点击开启声音";
    }
    dom.playerHint.textContent = hint;
    dom.playerSpaceHint.textContent = auto
      ? state.autoRunning
        ? "暂停"
        : "继续"
      : "翻开";
    dom.playBtn.setAttribute("aria-label", dom.playLabel.textContent);
  }

  function setControlsEnabled(enabled) {
    dom.prevBtn.disabled = !enabled;
    dom.nextBtn.disabled = !enabled;
    dom.playBtn.disabled = !enabled;
    dom.flashcard.tabIndex = enabled ? 0 : -1;
    dom.flashcard.setAttribute("aria-busy", enabled ? "false" : "true");
    if (!enabled) {
      dom.replayBtn.disabled = true;
    }
  }

  function setLibraryStatus(kind, text) {
    if (!dom.libraryStatus) return;
    dom.libraryStatus.dataset.state = kind;
    dom.libraryStatusText.textContent = text;
    dom.libraryStatus.title = text;
  }

  /* ---------------------------------------------------------- state screens */
  function showLoadingCard() {
    dom.cardState.hidden = false;
    dom.cardMain.hidden = true;
    dom.cardState.dataset.state = "loading";
    if (dom.stateLoaderUse) dom.stateLoaderUse.setAttribute("href", "#icon-loader");
    dom.cardStateEyebrow.textContent = "WORDS ON THE WAY";
    dom.cardStateTitle.textContent = "正在展开词书";
    dom.cardStateMessage.textContent = "为你整理今天的单词与节奏…";
    dom.retryLoad.hidden = true;
    dom.emptySettings.hidden = true;
    dom.playerHint.textContent = "正在准备学习节奏…";
    dom.liveDot.classList.add("is-paused");
    dom.playerSpaceHint.textContent = "暂停";
  }

  function showLoadError(error) {
    state.ready = false;
    clearAutoTimer();
    stopAudio();
    setControlsEnabled(false);
    setLibraryStatus("error", "词库连接失败");
    dom.cardState.hidden = false;
    dom.cardMain.hidden = true;
    dom.cardState.dataset.state = "error";
    if (dom.stateLoaderUse) dom.stateLoaderUse.setAttribute("href", "#icon-refresh");
    dom.cardStateEyebrow.textContent = "COULDN'T LOAD WORDS";
    dom.cardStateTitle.textContent = "词书没有加载成功";
    dom.cardStateMessage.textContent =
      (error && error.message) || "请检查服务器状态后重新加载。";
    dom.retryLoad.hidden = false;
    dom.emptySettings.hidden = true;
    dom.bookTitle.textContent = "词书加载失败";
    dom.bookStage.textContent = "请检查服务器状态后重试";
    dom.bookCount.textContent = "— 个单词";
    dom.bookProgress.style.width = "0%";
    dom.bookProgressText.textContent = "—";
    dom.playerHint.textContent = "连接失败 · 请重新加载";
    dom.liveDot.classList.add("is-paused");
    dom.playBtn.disabled = true;
    dom.prevBtn.disabled = true;
    dom.nextBtn.disabled = true;
    dom.flashcard.setAttribute("aria-busy", "false");
    dom.flashcard.tabIndex = -1;
  }

  function showEmptyState(title, message) {
    state.ready = false;
    clearAutoTimer();
    stopAudio();
    setControlsEnabled(false);
    setLibraryStatus("loading", "词书目录为空");
    dom.cardState.hidden = false;
    dom.cardMain.hidden = true;
    dom.cardState.dataset.state = "empty";
    if (dom.stateLoaderUse) dom.stateLoaderUse.setAttribute("href", "#icon-book");
    dom.cardStateEyebrow.textContent = "NO WORDS YET";
    dom.cardStateTitle.textContent = title;
    dom.cardStateMessage.textContent = message;
    dom.retryLoad.hidden = false;
    dom.emptySettings.hidden = false;
    if (state.book) {
      dom.bookTitle.textContent = state.book.title;
      dom.bookStage.textContent = "当前范围没有单词";
      dom.bookCount.textContent = "0 个单词";
    } else {
      dom.bookTitle.textContent = "还没有可用的词书";
      dom.bookStage.textContent = "把 JSON 词书放进 src/wordbooks";
      dom.bookCount.textContent = "0 个单词";
    }
    dom.bookProgress.style.width = "0%";
    dom.bookProgressText.textContent = "—";
    dom.playerHint.textContent = "等待词书 · 放入文件后点击重新加载";
    dom.liveDot.classList.add("is-paused");
    dom.flashcard.tabIndex = -1;
    dom.flashcard.setAttribute("aria-busy", "false");
  }

  /* ----------------------------------------------------------------- events */
  function bindEvents() {
    dom.playBtn.addEventListener("click", toggleAutoplay);
    dom.prevBtn.addEventListener("click", previousWord);
    dom.nextBtn.addEventListener("click", nextWord);

    dom.replayBtn.addEventListener("click", function (event) {
      event.stopPropagation();
      state.audioUnlocked = true;
      if (!state.serverAudioEnabled || !state.prefs || !state.prefs.audio_enabled) {
        LF.toast("发音已在设置中关闭，可前往设置页重新开启。");
        return;
      }
      attemptPlayAudio({ force: true });
    });

    dom.soundToggle.addEventListener("click", function () {
      if (!state.serverAudioEnabled) {
        LF.toast("服务器没有启用发音服务。");
        return;
      }
      if (!state.prefs || !state.prefs.audio_enabled) {
        LF.toast("发音已在设置页关闭，开启后即可双语朗读。");
        return;
      }
      state.muted = !state.muted;
      state.audioUnlocked = true;
      if (state.muted) {
        audio.pause();
      } else {
        attemptPlayAudio({ force: true });
      }
      updateAudioUI();
      updatePlayerUI();
    });

    dom.flashcard.addEventListener("click", function (event) {
      if (!state.ready) return;
      if (event.target instanceof Element && event.target.closest("button")) {
        return;
      }
      stepForward();
    });

    dom.flashcard.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      if (!state.ready) return;
      event.preventDefault();
      stepForward();
    });

    dom.retryLoad.addEventListener("click", function () {
      if (!state.loading) loadHomeData();
    });

    document.addEventListener("keydown", handleKeydown);

    document.addEventListener("visibilitychange", function () {
      state.documentHidden = document.hidden;
      if (state.documentHidden) {
        clearAutoTimer();
        audio.pause();
      } else if (state.ready) {
        scheduleAuto();
      }
    });

    window.addEventListener("pagehide", function () {
      clearAutoTimer();
      audio.pause();
    });

    window.addEventListener("pageshow", function (event) {
      if (event.persisted) {
        loadHomeData();
      }
    });
  }

  function handleKeydown(event) {
    if (event.defaultPrevented) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (LF.isEditableTarget(event.target)) return;
    if (!state.ready) return;

    const target = event.target;
    const interactive =
      target instanceof Element &&
      target.closest("button, a, [role='button'], select, input, textarea");

    if (event.code === "Space") {
      if (interactive) return;
      event.preventDefault();
      if (state.mode === "auto") {
        toggleAutoplay();
      } else {
        stepForward();
      }
      return;
    }

    if (event.code === "ArrowRight") {
      event.preventDefault();
      nextWord();
      return;
    }

    if (event.code === "ArrowLeft") {
      event.preventDefault();
      previousWord();
      return;
    }

    if (event.code === "KeyR") {
      event.preventDefault();
      state.audioUnlocked = true;
      attemptPlayAudio({ force: true });
      return;
    }

    if (event.code === "KeyF") {
      event.preventDefault();
      stepForward();
    }
  }

  /* ------------------------------------------------------------------ utils */
  function formatInterval(value) {
    const number = Number(value);
    if (!isFinite(number)) return "4";
    return Number.isInteger(number) ? String(number) : number.toFixed(1);
  }

  function clampNumber(value, minimum, maximum, fallback) {
    const number = Number(value);
    if (!isFinite(number)) return fallback;
    return Math.min(maximum, Math.max(minimum, number));
  }

  /* 随机模式下每开始新一轮都用「种子 + 轮次」重新洗牌，顺序可复现。 */
  function reshuffleDeck() {
    const deck = state.deck;
    if (deck.length < 2) return;
    const previous = deck[state.index];
    LF.shuffleWithSeed(deck, roundSeed(state.round));
    if (deck[0] === previous) {
      deck[0] = deck[1];
      deck[1] = previous;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
