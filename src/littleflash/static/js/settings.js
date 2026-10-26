/* LittleFlash · settings page */
(function () {
  "use strict";

  const LF = window.LF;
  const $ = function (id) {
    return document.getElementById(id);
  };

  initSettings();

  /* =====================================================================
     Settings form
     ===================================================================== */
  function initSettings() {
    const form = $("settingsForm");
    if (!form) return;

    const dom = {
      form: form,
      wordbook: $("wordbookSelect"),
      unit: $("unitSelect"),
      saveStatus: $("saveStatus"),
      saveStatusText: $("saveStatusText"),
      saveRetry: $("saveRetry"),
      currentBookTitle: $("currentBookTitle"),
      currentBookMeta: $("currentBookMeta"),
      currentBookCount: $("currentBookCount"),
      intervalField: $("intervalField"),
      intervalRange: $("intervalRange"),
      intervalOutput: $("intervalOutput"),
      intervalHint: $("intervalHint"),
      audioToggle: $("audioToggle"),
      volumeField: $("volumeField"),
      volumeRange: $("volumeRange"),
      volumeOutput: $("volumeOutput"),
      resetBtn: $("resetBtn"),
      resetDialog: $("resetDialog"),
      cancelReset: $("cancelReset"),
      confirmReset: $("confirmReset"),
      logoutBtn: $("logoutBtn"),
      previewCard: $("previewCard"),
      previewFaceLabel: $("previewFaceLabel"),
      previewWordBlock: $("previewWordBlock"),
      previewWord: $("previewWord"),
      previewPhonetic: $("previewPhonetic"),
      previewMeaningBlock: $("previewMeaningBlock"),
      previewMeaning: $("previewMeaning"),
      previewWatermark: $("previewWatermark"),
      previewUnit: $("previewUnit"),
      previewCaption: $("previewCaption"),
      previewProgress: $("previewProgress"),
      previewSound: $("previewSound"),
    };

    const modeMeta = document.querySelector('meta[name="settings-mode"]');
    const scopeMeta = document.querySelector('meta[name="settings-scope"]');
    const scope = scopeMeta
      ? scopeMeta.getAttribute("content") || "local"
      : "local";
    const localMode = scope === "local" || !modeMeta;

    const state = {
      hasBooks: Boolean(dom.wordbook && dom.wordbook.options.length > 0),
      localMode: localMode,
      scope: localMode ? "local" : scope,
      saving: false,
      hasUnsaved: false,
      saveFailed: false,
      pendingWhileSaving: false,
      saveTimer: null,
      redirectTimer: null,
      sample: readInitialSample(),
      previewFace: 0,
      previewTimer: null,
      previewHidden: document.hidden,
      bookCache: new Map(),
      unitRequestToken: 0,
      resetBusy: false,
    };

    const previewAudio = new Audio();
    previewAudio.preload = "auto";

    if (!state.hasBooks) {
      disableForm();
      setSaveStatus("saved", "等待词书");
      return;
    }

    bindFormEvents();
    boot();

    async function boot() {
      if (state.localMode) {
        await applyLocalPreferences();
      }
      syncRangeVisuals();
      syncIntervalUi(false);
      syncVolumeUi();
      syncAudioUi();
      updateBookSummaryFromOption();
      renderPreview();
      setupPreviewTimer();
      setSaveStatus("saved", savedLabel());
    }

    function savedLabel() {
      if (state.localMode) return "已保存到本机";
      if (state.scope === "site") return "站点默认已保存";
      return "已保存到我的账号";
    }

    function settingsEndpoint() {
      return state.scope === "site" ? "/api/settings?scope=site" : "/api/settings";
    }

    /* 本机存储里的设置优先于服务端默认值，仅影响当前浏览器。 */
    async function applyLocalPreferences() {
      const stored = LF.localPrefs.read();
      if (!stored) return;

      const bookId = stored.wordbook_id;
      const hasBook =
        bookId &&
        Array.prototype.some.call(dom.wordbook.options, function (option) {
          return option.value === bookId;
        });

      if (hasBook && dom.wordbook.value !== bookId) {
        dom.wordbook.value = bookId;
        updateBookSummaryFromOption();
        try {
          const book = await ensureBook(bookId);
          fillUnits(book, stored.unit_id || "all");
          updateBookSummaryFromBook(book);
          state.sample = sampleFromBook(book, dom.unit.value);
        } catch (error) {
          dom.unit.replaceChildren(makeOption("all", "整本词书"));
        }
      } else if (stored.unit_id) {
        const hasUnit = Array.prototype.some.call(dom.unit.options, function (option) {
          return option.value === stored.unit_id;
        });
        if (hasUnit) dom.unit.value = stored.unit_id;
      }

      if (stored.front) setChoice("front", stored.front);
      if (stored.order) setChoice("order", stored.order);
      if (stored.mode) setChoice("mode", stored.mode);
      if (stored.interval_seconds !== undefined) {
        dom.intervalRange.value = stored.interval_seconds;
      }
      if (typeof stored.audio_enabled === "boolean") {
        dom.audioToggle.checked = stored.audio_enabled;
      }
      if (stored.audio_volume !== undefined) {
        dom.volumeRange.value = Math.round(stored.audio_volume * 100);
      }
    }

    function setChoice(name, value) {
      const input = document.querySelector(
        'input[name="' + name + '"][value="' + value + '"]'
      );
      if (input) input.checked = true;
    }

    /* ---------------------------------------------------------------- events */
    function bindFormEvents() {
      dom.form.addEventListener("submit", function (event) {
        event.preventDefault();
      });

      dom.wordbook.addEventListener("change", handleWordbookChange);
      dom.unit.addEventListener("change", handleUnitChange);

      document.querySelectorAll('input[name="front"]').forEach(function (input) {
        input.addEventListener("change", function () {
          state.previewFace = 0;
          renderPreview();
          setupPreviewTimer();
          queueSave();
        });
      });

      document.querySelectorAll('input[name="order"]').forEach(function (input) {
        input.addEventListener("change", function () {
          queueSave();
        });
      });

      document.querySelectorAll('input[name="mode"]').forEach(function (input) {
        input.addEventListener("change", function () {
          syncIntervalUi(true);
          setupPreviewTimer();
          queueSave();
        });
      });

      dom.intervalRange.addEventListener("input", function () {
        syncIntervalUi(false);
        setupPreviewTimer();
        queueSave();
      });
      dom.intervalRange.addEventListener("change", function () {
        queueSave();
      });

      dom.audioToggle.addEventListener("change", function () {
        syncAudioUi();
        queueSave();
      });

      dom.volumeRange.addEventListener("input", function () {
        syncVolumeUi();
        updatePreviewAudioVolume();
        queueSave();
      });
      dom.volumeRange.addEventListener("change", function () {
        queueSave();
      });

      dom.previewCard.addEventListener("click", function (event) {
        if (event.target instanceof Element && event.target.closest("button")) {
          return;
        }
        flipPreview();
      });

      dom.previewCard.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          flipPreview();
        }
      });

      dom.previewSound.addEventListener("click", function (event) {
        event.stopPropagation();
        playPreviewAudio();
      });

      dom.saveRetry.addEventListener("click", function () {
        state.saveFailed = false;
        queueSave({ immediate: true });
      });

      dom.resetBtn.addEventListener("click", openResetDialog);
      dom.cancelReset.addEventListener("click", function () {
        if (dom.resetDialog && dom.resetDialog.open) dom.resetDialog.close();
      });
      dom.confirmReset.addEventListener("click", confirmReset);

      if (dom.resetDialog) {
        dom.resetDialog.addEventListener("close", function () {
          window.setTimeout(function () {
            dom.resetBtn.focus({ preventScroll: true });
          }, 20);
        });
      }

      if (dom.logoutBtn) {
        dom.logoutBtn.addEventListener("click", handleLogout);
      }

      document.addEventListener("visibilitychange", function () {
        state.previewHidden = document.hidden;
        if (state.previewHidden) {
          clearPreviewTimer();
          previewAudio.pause();
        } else {
          setupPreviewTimer();
        }
      });

      window.addEventListener("beforeunload", function (event) {
        if (state.hasUnsaved || state.saving) {
          event.preventDefault();
          event.returnValue = "";
        }
      });

      previewAudio.addEventListener("ended", function () {
        dom.previewSound.classList.remove("is-playing");
      });
      previewAudio.addEventListener("error", function () {
        dom.previewSound.classList.remove("is-playing");
      });
    }

    /* -------------------------------------------------------------- book/unit */
    async function handleWordbookChange() {
      updateBookSummaryFromOption();
      state.unitRequestToken += 1;
      const token = state.unitRequestToken;
      const bookId = dom.wordbook.value;

      dom.unit.replaceChildren(makeOption("all", "整本词书"));
      dom.unit.value = "all";
      dom.unit.disabled = true;
      queueSave();

      try {
        const book = await ensureBook(bookId);
        if (token !== state.unitRequestToken || dom.wordbook.value !== bookId) return;
        fillUnits(book, "all");
        updateBookSummaryFromBook(book);
        state.sample = sampleFromBook(book, "all");
        state.previewFace = 0;
        renderPreview();
        setupPreviewTimer();
      } catch (error) {
        if (token !== state.unitRequestToken) return;
        LF.toast(
          "词书内容加载失败：" + (error.message || "请稍后重试"),
          { type: "error", duration: 4200 }
        );
      } finally {
        if (token === state.unitRequestToken) {
          dom.unit.disabled = false;
        }
      }
    }

    async function handleUnitChange() {
      queueSave();
      const bookId = dom.wordbook.value;
      const unitId = dom.unit.value || "all";
      try {
        const book = await ensureBook(bookId);
        if (dom.wordbook.value !== bookId || dom.unit.value !== unitId) return;
        state.sample = sampleFromBook(book, unitId);
        state.previewFace = 0;
        renderPreview();
        setupPreviewTimer();
      } catch (error) {
        LF.toast("单元预览加载失败，请稍后再试。", { type: "error" });
      }
    }

    function fillUnits(book, desiredUnit) {
      dom.unit.replaceChildren(makeOption("all", "整本词书"));
      (book.units || []).forEach(function (unit) {
        dom.unit.appendChild(
          makeOption(
            unit.id,
            (unit.title || "未命名单元") + " · " + (unit.wordCount || 0) + " 词"
          )
        );
      });
      const values = Array.prototype.map.call(dom.unit.options, function (option) {
        return option.value;
      });
      dom.unit.value = values.indexOf(desiredUnit) !== -1 ? desiredUnit : "all";
      dom.unit.disabled = false;
    }

    function makeOption(value, label) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      return option;
    }

    async function ensureBook(bookId) {
      if (state.bookCache.has(bookId)) return state.bookCache.get(bookId);
      const response = await LF.api(
        "/api/wordbooks/" + encodeURIComponent(bookId),
        { timeout: 16000 }
      );
      if (!response || !response.book) {
        throw new LF.ApiError("词书内容为空", 0, "invalid_wordbook");
      }
      state.bookCache.set(bookId, response.book);
      return response.book;
    }

    function updateBookSummaryFromOption() {
      const option =
        dom.wordbook.options[dom.wordbook.selectedIndex] ||
        dom.wordbook.options[0];
      if (!option) return;
      const unitCount = option.dataset.unitCount || "—";
      const wordCount = option.dataset.wordCount || "0";
      const meta = [
        LF.formatStageLabel(option.dataset.stage),
        option.dataset.edition,
      ]
        .filter(Boolean)
        .join(" · ");
      dom.currentBookTitle.textContent =
        option.dataset.title || option.textContent.trim();
      dom.currentBookMeta.textContent =
        (meta || "本地词书") + " · " + unitCount + " 个单元";
      dom.currentBookCount.textContent = wordCount + " 词";
    }

    function updateBookSummaryFromBook(book) {
      if (!book) return;
      const meta = [LF.formatStageLabel(book.stage), book.edition]
        .filter(Boolean)
        .join(" · ");
      dom.currentBookTitle.textContent = book.title || "未命名词书";
      dom.currentBookMeta.textContent =
        (meta || "本地词书") + " · " + (book.units || []).length + " 个单元";
      dom.currentBookCount.textContent = String(book.wordCount || 0) + " 词";
      const option =
        dom.wordbook.options[dom.wordbook.selectedIndex] ||
        dom.wordbook.options[0];
      if (option) {
        option.dataset.title = book.title || option.textContent.trim();
        option.dataset.stage = book.stage || "";
        option.dataset.edition = book.edition || "";
        option.dataset.unitCount = String((book.units || []).length);
        option.dataset.wordCount = String(book.wordCount || 0);
      }
    }

    function sampleFromBook(book, unitId) {
      let units = book.units || [];
      if (unitId !== "all") {
        units = units.filter(function (unit) {
          return unit.id === unitId;
        });
      }
      for (let index = 0; index < units.length; index += 1) {
        const unit = units[index];
        if (Array.isArray(unit.words) && unit.words.length > 0) {
          const word = unit.words[0];
          return {
            en: word.en || "",
            zh: word.zh || "",
            phonetic: word.phonetic || "",
            unitTitle: unit.title || "",
          };
        }
      }
      return null;
    }

    /* --------------------------------------------------------------- preview */
    function readInitialSample() {
      const en = dom.previewWord ? dom.previewWord.textContent.trim() : "";
      if (!en) return null;
      return {
        en: en,
        zh: dom.previewMeaning ? dom.previewMeaning.textContent.trim() : "",
        phonetic: dom.previewPhonetic
          ? dom.previewPhonetic.textContent.trim()
          : "",
        unitTitle: dom.previewUnit
          ? dom.previewUnit.textContent.trim()
          : "",
      };
    }

    function getFront() {
      const checked = document.querySelector('input[name="front"]:checked');
      return checked && checked.value === "zh" ? "zh" : "en";
    }

    function getMode() {
      const checked = document.querySelector('input[name="mode"]:checked');
      return checked && checked.value === "manual" ? "manual" : "auto";
    }

    function getInterval() {
      const value = Number(dom.intervalRange.value);
      return isFinite(value) ? Math.min(30, Math.max(1, value)) : 4;
    }

    function getPreviewKindOrder() {
      return getFront() === "zh" ? ["zh", "en"] : ["en", "zh"];
    }

    function renderPreview() {
      if (!state.sample) {
        dom.previewWord.textContent = "—";
        dom.previewPhonetic.textContent = "";
        dom.previewPhonetic.hidden = true;
        dom.previewMeaning.textContent = "暂无预览";
        dom.previewMeaningBlock.hidden = true;
        dom.previewWordBlock.hidden = false;
        return;
      }

      const kinds = getPreviewKindOrder();
      const kind = kinds[state.previewFace];
      const word = state.sample;
      dom.previewCard.dataset.face = kind;
      dom.previewWordBlock.hidden = kind === "zh";
      dom.previewMeaningBlock.hidden = kind !== "zh";
      dom.previewFaceLabel.textContent = kind === "zh" ? "中文释义" : "ENGLISH";
      dom.previewWatermark.textContent = kind === "zh" ? "中" : "Aa";
      dom.previewUnit.textContent = word.unitTitle || "词书示例";

      if (kind === "zh") {
        dom.previewMeaning.textContent = word.zh || "暂无释义";
        setPreviewSize(dom.previewMeaning, word.zh.length, 28, 80, "is-long");
        dom.previewCard.setAttribute(
          "aria-label",
          "设置预览，中文释义：" + word.zh + "，按回车翻面"
        );
      } else {
        dom.previewWord.textContent = word.en;
        setPreviewWordSize(word.en.length);
        const phonetic = (word.phonetic || "").trim();
        dom.previewPhonetic.textContent = phonetic;
        dom.previewPhonetic.hidden = !phonetic;
        dom.previewCard.setAttribute(
          "aria-label",
          "设置预览，英文单词：" + word.en + "，按回车翻面"
        );
      }
      updatePreviewCaption();
    }

    function setPreviewWordSize(length) {
      dom.previewWord.classList.remove("is-long", "is-xlong");
      if (length > 20) {
        dom.previewWord.classList.add("is-xlong");
      } else if (length > 11) {
        dom.previewWord.classList.add("is-long");
      }
    }

    function setPreviewSize(element, length, longThreshold, xlongThreshold, longClass) {
      element.classList.remove(longClass, "is-xlong");
      if (length > xlongThreshold) {
        element.classList.add("is-xlong");
      } else if (length > longThreshold) {
        element.classList.add(longClass);
      }
    }

    function updatePreviewCaption() {
      if (!dom.previewCaption) return;
      const auto = getMode() === "auto";
      const direction = getFront() === "en" ? "英文 → 中文" : "中文 → 英文";
      if (auto) {
        dom.previewCaption.textContent =
          direction + " · 每 " + formatNumber(getInterval()) + " 秒自动切换";
      } else {
        dom.previewCaption.textContent =
          direction + " · 手动模式，点击预览卡翻面";
      }
    }

    function flipPreview() {
      if (!state.sample) return;
      state.previewFace = state.previewFace === 0 ? 1 : 0;
      renderPreview();
      setupPreviewTimer();
    }

    function setupPreviewTimer() {
      clearPreviewTimer();
      if (!state.sample) {
        setPreviewProgress(0);
        return;
      }
      updatePreviewCaption();
      if (state.previewHidden || getMode() !== "auto") {
        setPreviewProgress(getMode() === "auto" ? 100 : 100);
        return;
      }
      const duration = Math.max(1000, getInterval() * 1000);
      animatePreviewProgress(duration);
      state.previewTimer = window.setTimeout(function () {
        state.previewTimer = null;
        state.previewFace = state.previewFace === 0 ? 1 : 0;
        renderPreview();
        setupPreviewTimer();
      }, duration);
    }

    function clearPreviewTimer() {
      if (state.previewTimer !== null) {
        window.clearTimeout(state.previewTimer);
        state.previewTimer = null;
      }
    }

    function animatePreviewProgress(duration) {
      if (!dom.previewProgress) return;
      dom.previewProgress.style.transition = "none";
      dom.previewProgress.style.width = "100%";
      void dom.previewProgress.offsetWidth;
      dom.previewProgress.style.transition = "width " + duration + "ms linear";
      dom.previewProgress.style.width = "0%";
    }

    function setPreviewProgress(percent) {
      if (!dom.previewProgress) return;
      dom.previewProgress.style.transition = "none";
      dom.previewProgress.style.width = percent + "%";
    }

    function playPreviewAudio() {
      if (!dom.audioToggle.checked) {
        LF.toast("先开启单词发音，才能试听。");
        dom.audioToggle.focus();
        return;
      }
      if (!state.sample || !state.sample.en) return;
      const url = LF.url(
        "/api/audio?word=" + encodeURIComponent(state.sample.en)
      );
      const volume = Number(dom.volumeRange.value) / 100;
      try {
        previewAudio.pause();
        previewAudio.src = url;
        previewAudio.volume = isFinite(volume) ? volume : 0.8;
        dom.previewSound.classList.add("is-playing");
        const promise = previewAudio.play();
        if (promise && promise.catch) {
          promise.catch(function () {
            dom.previewSound.classList.remove("is-playing");
            LF.toast("试听失败，请稍后再试。", { type: "error" });
          });
        }
      } catch (error) {
        dom.previewSound.classList.remove("is-playing");
      }
    }

    function updatePreviewAudioVolume() {
      const volume = Number(dom.volumeRange.value) / 100;
      if (isFinite(volume)) previewAudio.volume = volume;
    }

    /* ---------------------------------------------------------- save & status */
    function readPayload() {
      const front = document.querySelector('input[name="front"]:checked');
      const order = document.querySelector('input[name="order"]:checked');
      const mode = document.querySelector('input[name="mode"]:checked');
      return {
        wordbook_id: dom.wordbook.value,
        unit_id: dom.unit.value || "all",
        front: front ? front.value : "en",
        order: order ? order.value : "sequence",
        mode: mode ? mode.value : "auto",
        interval_seconds: getInterval(),
        audio_enabled: dom.audioToggle.checked,
        audio_volume: Number(dom.volumeRange.value) / 100,
      };
    }

    function queueSave(options) {
      if (!state.hasBooks) return;
      const immediate = Boolean(options && options.immediate);
      state.hasUnsaved = true;
      state.saveFailed = false;
      if (dom.saveRetry) dom.saveRetry.hidden = true;
      setSaveStatus("saving", state.localMode ? "正在保存到本机…" : "正在保存更改…");

      window.clearTimeout(state.saveTimer);
      state.saveTimer = null;

      if (state.saving) {
        state.pendingWhileSaving = true;
        return;
      }
      if (immediate) {
        flushSave();
      } else {
        state.saveTimer = window.setTimeout(function () {
          state.saveTimer = null;
          flushSave();
        }, 320);
      }
    }

    async function flushSave() {
      if (state.saving) {
        state.pendingWhileSaving = true;
        return;
      }
      if (!state.hasUnsaved || state.saveFailed) return;

      window.clearTimeout(state.saveTimer);
      state.saveTimer = null;
      state.pendingWhileSaving = false;
      const payload = readPayload();

      if (state.localMode) {
        const stored = LF.localPrefs.write(payload);
        state.hasUnsaved = !stored;
        state.saveFailed = !stored;
        if (stored) {
          setSaveStatus("saved", "已保存到本机");
        } else {
          setSaveStatus("error", "本机存储不可用");
          LF.toast("浏览器存储不可用（可能处于隐私模式），这次修改不会被记住。", {
            type: "error",
            duration: 4600,
          });
        }
        return;
      }

      state.saving = true;

      try {
        const result = await LF.api(settingsEndpoint(), {
          method: "PUT",
          body: payload,
          timeout: 16000,
        });
        if (!result || !result.ok) {
          throw new LF.ApiError("服务器没有确认本次保存", 0, "save_unconfirmed");
        }
        if (state.pendingWhileSaving) {
          setSaveStatus("saving", "正在保存更改…");
        } else {
          state.hasUnsaved = false;
          setSaveStatus("saved", savedLabel());
        }
      } catch (error) {
        state.hasUnsaved = true;
        state.saveFailed = true;
        handleSaveError(error);
      } finally {
        state.saving = false;
        if (state.pendingWhileSaving && !state.saveFailed) {
          state.pendingWhileSaving = false;
          window.setTimeout(flushSave, 30);
        }
      }
    }

    function handleSaveError(error) {
      if (error && error.status === 401) {
        setSaveStatus("error", "登录已过期");
        LF.toast("登录已过期，正在前往登录页…", {
          type: "error",
          duration: 3200,
        });
        redirectSoon(LF.url("/login?next=settings_page"));
        return;
      }
      if (error && error.code === "csrf_failed") {
        setSaveStatus("error", "安全令牌过期");
        LF.toast("页面安全令牌已过期，正在刷新…", { type: "error" });
        redirectSoon(LF.url("/settings"));
        return;
      }
      setSaveStatus("error", "保存失败，未同步");
      if (dom.saveRetry) dom.saveRetry.hidden = false;
      LF.toast(
        (error && error.message) || "保存失败，请检查网络后重试。",
        { type: "error", duration: 4400 }
      );
    }

    function setSaveStatus(stateName, text) {
      if (!dom.saveStatus) return;
      dom.saveStatus.dataset.state = stateName;
      if (dom.saveStatusText) dom.saveStatusText.textContent = text;
      if (dom.saveRetry) dom.saveRetry.hidden = stateName !== "error";
      const labels = {
        saved: "所有更改已保存",
        saving: "正在保存更改…",
        error: "保存失败",
      };
      dom.saveStatus.setAttribute(
        "aria-label",
        labels[stateName] || text || "保存状态"
      );
    }

    function redirectSoon(path) {
      if (state.redirectTimer) return;
      state.redirectTimer = window.setTimeout(function () {
        window.location.href = path;
      }, 1800);
    }

    /* ------------------------------------------------------------- form sync */
    function syncRangeVisuals() {
      LF.updateRangeFill(dom.intervalRange);
      LF.updateRangeFill(dom.volumeRange);
    }

    function syncIntervalUi(restartPreview) {
      const interval = getInterval();
      dom.intervalOutput.textContent = formatNumber(interval);
      LF.updateRangeFill(dom.intervalRange);
      const manual = getMode() === "manual";
      dom.intervalField.classList.toggle("is-inactive", manual);
      dom.intervalHint.textContent = manual
        ? "当前为手动切换；切换到自动模式后此间隔生效。"
        : "每一面停留 " + formatNumber(interval) + " 秒后，自动切换到下一面。";
      if (restartPreview) {
        state.previewFace = 0;
        renderPreview();
      }
      updatePreviewCaption();
      if (restartPreview) setupPreviewTimer();
    }

    function syncAudioUi() {
      const enabled = dom.audioToggle.checked;
      dom.volumeField.classList.toggle("is-inactive", !enabled);
      dom.previewSound.disabled = !enabled;
      dom.previewSound.title = enabled ? "试听单词发音" : "发音已关闭";
      if (!enabled) previewAudio.pause();
    }

    function syncVolumeUi() {
      const volume = Number(dom.volumeRange.value);
      dom.volumeOutput.textContent = String(
        isFinite(volume) ? Math.round(volume) : 80
      );
      LF.updateRangeFill(dom.volumeRange);
      updatePreviewAudioVolume();
    }

    function formatNumber(value) {
      const number = Number(value);
      if (!isFinite(number)) return "4";
      return Number.isInteger(number) ? String(number) : number.toFixed(1);
    }

    /* --------------------------------------------------------------- reset/logout */
    function openResetDialog() {
      if (!dom.resetDialog) return;
      if (dom.resetDialog.open) return;
      dom.resetDialog.showModal();
    }

    async function confirmReset() {
      if (state.resetBusy) return;
      if (state.localMode) {
        state.resetBusy = true;
        LF.localPrefs.clear();
        LF.toast("已清除本机设置，正在恢复站点默认值…", { type: "success" });
        window.setTimeout(function () {
          window.location.reload();
        }, 520);
        return;
      }
      state.resetBusy = true;
      dom.confirmReset.disabled = true;
      dom.cancelReset.disabled = true;
      dom.confirmReset.textContent = "正在恢复…";
      try {
        const result = await LF.api(
          state.scope === "site"
            ? "/api/settings/reset?scope=site"
            : "/api/settings/reset",
          {
            method: "POST",
            timeout: 12000,
          }
        );
        if (!result || !result.ok) {
          throw new LF.ApiError("恢复默认设置失败", 0, "reset_failed");
        }
        LF.toast("已恢复默认设置，正在刷新…", { type: "success" });
        window.setTimeout(function () {
          window.location.reload();
        }, 550);
      } catch (error) {
        if (error && error.status === 401) {
          redirectSoon(LF.url("/settings"));
          return;
        }
        LF.toast(error.message || "恢复默认设置失败，请稍后再试。", {
          type: "error",
        });
        state.resetBusy = false;
        dom.confirmReset.disabled = false;
        dom.cancelReset.disabled = false;
        dom.confirmReset.textContent = "恢复默认";
      }
    }

    async function handleLogout() {
      if (!dom.logoutBtn || dom.logoutBtn.disabled) return;
      dom.logoutBtn.disabled = true;
      if (state.hasUnsaved && !state.saveFailed) {
        try {
          await flushSave();
        } catch (error) {
          /* Logging out should still be possible if a save fails. */
        }
      }
      try {
        await LF.api("/api/auth/logout", {
          method: "POST",
          timeout: 12000,
        });
        window.location.href = LF.url("/settings");
      } catch (error) {
        dom.logoutBtn.disabled = false;
        LF.toast(error.message || "退出登录失败，请稍后再试。", {
          type: "error",
        });
      }
    }

    function disableForm() {
      dom.form.querySelectorAll("input, select, button").forEach(function (element) {
        element.disabled = true;
      });
      if (dom.saveRetry) dom.saveRetry.hidden = true;
    }
  }
})();
