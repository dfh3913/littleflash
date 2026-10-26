/* LittleFlash · settings page（多词书 / 多单元 / 随机种子 / 音频预缓存） */
(function () {
  "use strict";

  const LF = window.LF;
  const $ = function (id) {
    return document.getElementById(id);
  };

  initLogin();
  initSettings();

  /* =====================================================================
     管理员登录（0.1.x 仍是单一设置密码）
     ===================================================================== */
  function initLogin() {
    const form = $("loginForm");
    if (!form) return;

    const input = $("settingsPassword");
    const reveal = $("passwordReveal");
    const error = $("loginError");
    const submit = $("loginSubmit");
    const submitLabel = submit ? submit.querySelector(".primary-action__label") : null;
    let busy = false;
    let lockTimer = null;
    let lockUntil = 0;

    if (reveal && input) {
      reveal.addEventListener("click", function () {
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        reveal.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
        reveal.title = show ? "隐藏密码" : "显示密码";
        LF.setHidden(reveal.querySelector(".reveal-on"), show);
        LF.setHidden(reveal.querySelector(".reveal-off"), !show);
        input.focus();
      });
    }

    if (input) {
      input.addEventListener("input", hideError);
      window.setTimeout(function () {
        input.focus({ preventScroll: true });
      }, 250);
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (busy) return;
      if (Date.now() < lockUntil) {
        updateLockMessage();
        return;
      }
      const password = input ? input.value : "";
      if (!password) {
        showError("请输入设置密码");
        if (input) input.focus();
        return;
      }

      busy = true;
      hideError();
      LF.setLoading(submit, true);
      if (submitLabel) submitLabel.textContent = "正在验证";
      try {
        await LF.api("/api/auth/login", {
          method: "POST",
          body: { password: password },
          timeout: 12000,
        });
        if (submitLabel) submitLabel.textContent = "验证成功";
        window.location.reload();
      } catch (requestError) {
        handleLoginError(requestError);
        busy = false;
        LF.setLoading(submit, false);
        if (submitLabel) submitLabel.textContent = "解锁设置";
        if (Date.now() < lockUntil && submit) submit.disabled = true;
        if (input) {
          input.focus();
          input.select();
        }
      }
    });

    function handleLoginError(requestError) {
      if (!requestError) {
        showError("验证失败，请稍后再试");
        return;
      }
      showError(requestError.message || "验证失败，请稍后再试");
      if (requestError.status === 429) {
        startLock(Math.max(1, Number(requestError.retryAfter) || 30));
      } else if (
        requestError.code === "csrf_failed" ||
        (requestError.status === 400 && /令牌/.test(requestError.message || ""))
      ) {
        showError("页面安全令牌已过期，即将为你刷新…");
        window.setTimeout(function () {
          window.location.reload();
        }, 1400);
      }
    }

    function showError(message) {
      if (!error) return;
      error.textContent = message;
      error.hidden = false;
    }

    function hideError() {
      if (!error) return;
      error.hidden = true;
      error.textContent = "";
    }

    function startLock(seconds) {
      lockUntil = Date.now() + seconds * 1000;
      if (submit) submit.disabled = true;
      updateLockMessage();
      window.clearInterval(lockTimer);
      lockTimer = window.setInterval(function () {
        if (Date.now() >= lockUntil) {
          window.clearInterval(lockTimer);
          if (submit) submit.disabled = false;
          hideError();
        } else {
          updateLockMessage();
        }
      }, 1000);
    }

    function updateLockMessage() {
      const remaining = Math.max(0, Math.ceil((lockUntil - Date.now()) / 1000));
      if (remaining > 0) {
        showError("尝试次数过多，请在 " + remaining + " 秒后重试");
        if (submit) submit.disabled = true;
      }
    }
  }

  /* =====================================================================
     设置表单
     ===================================================================== */
  function initSettings() {
    const form = $("settingsForm");
    if (!form) return;

    const dom = {
      form: form,
      bookPicker: $("bookPicker"),
      pickerSummary: $("pickerSummary"),
      saveStatus: $("saveStatus"),
      saveStatusText: $("saveStatusText"),
      saveRetry: $("saveRetry"),
      seedField: $("seedField"),
      seedInput: $("seedInput"),
      seedRefresh: $("seedRefresh"),
      intervalField: $("intervalField"),
      intervalRange: $("intervalRange"),
      intervalOutput: $("intervalOutput"),
      intervalHint: $("intervalHint"),
      audioToggle: $("audioToggle"),
      audioPrefetchToggle: $("audioPrefetchToggle"),
      volumeField: $("volumeField"),
      volumeRange: $("volumeRange"),
      volumeOutput: $("volumeOutput"),
      resetBtn: $("resetBtn"),
      resetDialog: $("resetDialog"),
      cancelReset: $("cancelReset"),
      confirmReset: $("confirmReset"),
      logoutBtn: $("logoutBtn"),
      toggleLogin: $("toggleLogin"),
      adminLogin: $("adminLogin"),
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
    const localMode = !modeMeta || modeMeta.getAttribute("content") !== "server";

    const state = {
      hasBooks: Boolean(dom.bookPicker && dom.bookPicker.querySelector(".book-pick")),
      localMode: localMode,
      saving: false,
      hasUnsaved: false,
      saveFailed: false,
      pendingWhileSaving: false,
      saveTimer: null,
      redirectTimer: null,
      previewRequest: 0,
      sample: readInitialSample(),
      previewFace: 0,
      previewTimer: null,
      previewHidden: document.hidden,
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
        applyLocalPreferences();
      }
      syncBookUi();
      syncRangeVisuals();
      syncIntervalUi(false);
      syncVolumeUi();
      syncAudioUi();
      syncSeedUi();
      renderPreview();
      setupPreviewTimer();
      updatePreviewCaption();
      setSaveStatus("saved", savedLabel());
      if (dom.seedInput && !LF.sanitizeSeed(dom.seedInput.value) && getOrder() === "random") {
        dom.seedInput.value = LF.generateSeed();
        syncSeedUi();
        queueSave();
      }
    }

    function savedLabel() {
      return state.localMode ? "已保存到本机" : "所有更改已保存";
    }

    /* ------------------------------------------------------ 本机设置恢复 */
    function applyLocalPreferences() {
      const stored = LF.localPrefs.read();
      if (!stored) return;

      const selections = LF.normalizeSelections(stored, booksFromDom());
      applySelections(selections);

      setChoice("front", stored.front === "zh" ? "zh" : "en");
      setChoice("order", stored.order === "random" || stored.order === "shuffle" ? "random" : "sequence");
      setChoice("mode", stored.mode === "manual" ? "manual" : "auto");

      if (stored.seed && dom.seedInput) {
        dom.seedInput.value = LF.sanitizeSeed(stored.seed);
      }

      if (dom.intervalRange) {
        const interval = Number(stored.interval_seconds);
        if (isFinite(interval)) {
          dom.intervalRange.value = String(Math.min(30, Math.max(1, interval)));
        }
      }
      if (dom.audioToggle && typeof stored.audio_enabled === "boolean") {
        dom.audioToggle.checked = stored.audio_enabled;
      }
      if (dom.audioPrefetchToggle && typeof stored.audio_prefetch === "boolean") {
        dom.audioPrefetchToggle.checked = stored.audio_prefetch;
      }
      if (dom.volumeRange) {
        const volume = Number(stored.audio_volume);
        if (isFinite(volume)) {
          dom.volumeRange.value = String(Math.round(Math.min(1, Math.max(0, volume)) * 100));
        }
      }
    }

    function booksFromDom() {
      const books = [];
      dom.bookPicker.querySelectorAll(".book-pick").forEach(function (bookEl) {
        const check = bookEl.querySelector(".book-pick__check");
        if (!check) return;
        const units = [];
        bookEl.querySelectorAll(".unit-pick").forEach(function (unitEl) {
          const unitCheck = unitEl.querySelector(".unit-pick__check");
          if (!unitCheck) return;
          units.push({
            id: unitCheck.value,
            title: unitEl.querySelector("span") ? unitEl.querySelector("span").textContent : "",
            wordCount: Number(unitEl.getAttribute("data-word-count")) || 0,
          });
        });
        books.push({
          id: check.value,
          title: bookEl.querySelector(".book-pick__title")
            ? bookEl.querySelector(".book-pick__title").textContent
            : check.value,
          units: units,
        });
      });
      return books;
    }

    /* ------------------------------------------------------------ 词书选择 */
    function bindFormEvents() {
      if (dom.bookPicker) {
        dom.bookPicker.addEventListener("change", function (event) {
          const target = event.target;
          if (!(target instanceof HTMLInputElement)) return;
          if (target.classList.contains("book-pick__check")) {
            const bookEl = target.closest(".book-pick");
            bookEl.querySelectorAll(".unit-pick__check").forEach(function (unitCheck) {
              unitCheck.checked = target.checked;
            });
            syncBookUi();
            queueSave();
            requestPreview();
          } else if (target.classList.contains("unit-pick__check")) {
            const bookEl = target.closest(".book-pick");
            const bookCheck = bookEl.querySelector(".book-pick__check");
            const anyChecked = Array.prototype.some.call(
              bookEl.querySelectorAll(".unit-pick__check"),
              function (check) {
                return check.checked;
              }
            );
            bookCheck.checked = anyChecked;
            syncBookUi();
            queueSave();
            requestPreview();
          }
        });

        dom.bookPicker.addEventListener("click", function (event) {
          const button = event.target.closest("[data-unit-all], [data-unit-none]");
          if (!button) return;
          event.preventDefault();
          const bookId =
            button.getAttribute("data-unit-all") ||
            button.getAttribute("data-unit-none");
          const bookEl = dom.bookPicker.querySelector(
            '.book-pick[data-book-id="' + CSS.escape(bookId) + '"]'
          );
          if (!bookEl) return;
          const selectAll = button.hasAttribute("data-unit-all");
          bookEl.querySelector(".book-pick__check").checked = selectAll;
          bookEl.querySelectorAll(".unit-pick__check").forEach(function (unitCheck) {
            unitCheck.checked = selectAll;
          });
          syncBookUi();
          queueSave();
          requestPreview();
        });
      }

      document.querySelectorAll('input[name="front"]').forEach(function (input) {
        input.addEventListener("change", function () {
          queueSave();
          renderPreview();
          updatePreviewCaption();
        });
      });

      document.querySelectorAll('input[name="order"]').forEach(function (input) {
        input.addEventListener("change", function () {
          syncSeedUi(true);
          queueSave();
          renderPreview();
          updatePreviewCaption();
        });
      });

      document.querySelectorAll('input[name="mode"]').forEach(function (input) {
        input.addEventListener("change", function () {
          syncIntervalUi(true);
          queueSave();
        });
      });

      if (dom.intervalRange) {
        dom.intervalRange.addEventListener("input", function () {
          syncIntervalUi(false);
          setupPreviewTimer();
          queueSave();
        });
        dom.intervalRange.addEventListener("change", function () {
          queueSave();
        });
      }

      if (dom.audioToggle) {
        dom.audioToggle.addEventListener("change", function () {
          syncAudioUi();
          queueSave();
        });
      }

      if (dom.audioPrefetchToggle) {
        dom.audioPrefetchToggle.addEventListener("change", function () {
          queueSave();
        });
      }

      if (dom.volumeRange) {
        dom.volumeRange.addEventListener("input", function () {
          syncVolumeUi();
          queueSave();
        });
        dom.volumeRange.addEventListener("change", function () {
          queueSave();
        });
      }

      if (dom.seedInput) {
        dom.seedInput.addEventListener("input", function () {
          const clean = LF.sanitizeSeed(dom.seedInput.value);
          if (dom.seedInput.value !== clean) dom.seedInput.value = clean;
          queueSave();
        });
        dom.seedInput.addEventListener("change", function () {
          if (!LF.sanitizeSeed(dom.seedInput.value)) {
            dom.seedInput.value = LF.generateSeed();
          }
          queueSave({ immediate: true });
        });
      }

      if (dom.seedRefresh) {
        dom.seedRefresh.addEventListener("click", function () {
          if (!dom.seedInput) return;
          dom.seedInput.value = LF.generateSeed();
          queueSave({ immediate: true });
          LF.toast("已换一个新的随机种子", { type: "success", duration: 2000 });
        });
      }

      if (dom.previewCard) {
        dom.previewCard.addEventListener("click", function (event) {
          if (event.target instanceof Element && event.target.closest("button")) return;
          flipPreview();
        });
        dom.previewCard.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            flipPreview();
          }
        });
      }

      if (dom.previewSound) {
        dom.previewSound.addEventListener("click", function (event) {
          event.stopPropagation();
          playPreviewAudio();
        });
      }

      if (dom.saveRetry) {
        dom.saveRetry.addEventListener("click", function () {
          state.saveFailed = false;
          queueSave({ immediate: true });
        });
      }

      if (dom.resetBtn) dom.resetBtn.addEventListener("click", openResetDialog);
      if (dom.cancelReset) {
        dom.cancelReset.addEventListener("click", function () {
          if (dom.resetDialog && dom.resetDialog.open) dom.resetDialog.close();
        });
      }
      if (dom.confirmReset) dom.confirmReset.addEventListener("click", confirmReset);
      if (dom.resetDialog) {
        dom.resetDialog.addEventListener("close", function () {
          window.setTimeout(function () {
            if (dom.resetBtn) dom.resetBtn.focus({ preventScroll: true });
          }, 20);
        });
      }

      if (dom.logoutBtn) dom.logoutBtn.addEventListener("click", handleLogout);

      if (dom.toggleLogin && dom.adminLogin) {
        dom.toggleLogin.addEventListener("click", function () {
          const open = dom.adminLogin.hidden;
          dom.adminLogin.hidden = !open;
          dom.toggleLogin.setAttribute("aria-expanded", open ? "true" : "false");
          if (open) {
            const input = $("settingsPassword");
            if (input) {
              window.setTimeout(function () {
                input.focus({ preventScroll: true });
              }, 40);
            }
          }
        });
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
        if (dom.previewSound) dom.previewSound.classList.remove("is-playing");
      });
      previewAudio.addEventListener("error", function () {
        if (dom.previewSound) dom.previewSound.classList.remove("is-playing");
      });
    }

    function setChoice(name, value) {
      const input = document.querySelector(
        'input[name="' + name + '"][value="' + value + '"]'
      );
      if (input) input.checked = true;
    }

    function syncBookUi() {
      if (!dom.bookPicker) return;
      dom.bookPicker.querySelectorAll(".book-pick").forEach(function (bookEl) {
        const bookCheck = bookEl.querySelector(".book-pick__check");
        const panel = bookEl.querySelector(".book-pick__units");
        if (bookCheck) {
          bookEl.classList.toggle("is-selected", bookCheck.checked);
          if (panel) panel.hidden = !bookCheck.checked;
        }
        bookEl.querySelectorAll(".unit-pick").forEach(function (unitEl) {
          const check = unitEl.querySelector(".unit-pick__check");
          unitEl.classList.toggle("is-checked", Boolean(check && check.checked));
        });
      });
      updatePickerSummary();
    }

    function readSelections() {
      const result = [];
      if (!dom.bookPicker) return result;
      dom.bookPicker.querySelectorAll(".book-pick").forEach(function (bookEl) {
        const bookCheck = bookEl.querySelector(".book-pick__check");
        if (!bookCheck || !bookCheck.checked) return;
        const unitChecks = Array.prototype.slice.call(
          bookEl.querySelectorAll(".unit-pick__check")
        );
        const checked = unitChecks.filter(function (check) {
          return check.checked;
        });
        if (!checked.length) return;
        const all = checked.length === unitChecks.length;
        result.push({
          wordbook_id: bookCheck.value,
          unit_ids: all
            ? ["all"]
            : checked.map(function (check) {
                return check.value;
              }),
        });
      });
      return result;
    }

    function applySelections(selections) {
      if (!dom.bookPicker) return;
      dom.bookPicker.querySelectorAll(".book-pick").forEach(function (bookEl) {
        const bookCheck = bookEl.querySelector(".book-pick__check");
        const wanted = selections.find(function (selection) {
          return selection.wordbook_id === bookCheck.value;
        });
        bookCheck.checked = Boolean(wanted);
        const units = wanted ? wanted.unit_ids : [];
        bookEl.querySelectorAll(".unit-pick__check").forEach(function (unitCheck) {
          unitCheck.checked =
            Boolean(wanted) &&
            (units.indexOf("all") !== -1 || units.indexOf(unitCheck.value) !== -1);
        });
      });
      syncBookUi();
    }

    function updatePickerSummary() {
      if (!dom.pickerSummary) return;
      const selections = readSelections();
      let units = 0;
      let words = 0;
      selections.forEach(function (selection) {
        const bookEl = dom.bookPicker.querySelector(
          '.book-pick[data-book-id="' + CSS.escape(selection.wordbook_id) + '"]'
        );
        if (!bookEl) return;
        bookEl.querySelectorAll(".unit-pick").forEach(function (unitEl) {
          const check = unitEl.querySelector(".unit-pick__check");
          if (check && check.checked) {
            units += 1;
            words += Number(unitEl.getAttribute("data-word-count")) || 0;
          }
        });
      });
      dom.pickerSummary.classList.toggle("is-empty", selections.length === 0);
      dom.pickerSummary.textContent = selections.length
        ? "已选 " +
          selections.length +
          " 本词书 · " +
          units +
          " 个单元 · 约 " +
          words.toLocaleString() +
          " 个单词"
        : "还没有选择任何词书，请至少勾选一本。";
      updatePreviewCaption();
    }

    /* ------------------------------------------------------------ 随机种子 */
    function getOrder() {
      const checked = document.querySelector('input[name="order"]:checked');
      return checked ? checked.value : "sequence";
    }

    function getMode() {
      const checked = document.querySelector('input[name="mode"]:checked');
      return checked ? checked.value : "auto";
    }

    function getFront() {
      const checked = document.querySelector('input[name="front"]:checked');
      return checked ? checked.value : "en";
    }

    function getInterval() {
      const value = Number(dom.intervalRange ? dom.intervalRange.value : 4);
      return isFinite(value) ? Math.min(30, Math.max(1, value)) : 4;
    }

    function syncSeedUi(forceGenerate) {
      const random = getOrder() === "random";
      if (dom.seedField) dom.seedField.hidden = !random;
      if (!dom.seedInput) return;
      if (random && (forceGenerate || !LF.sanitizeSeed(dom.seedInput.value))) {
        dom.seedInput.value = LF.generateSeed();
      }
      updatePreviewCaption();
    }

    /* -------------------------------------------------------------- 预览 */
    function readInitialSample() {
      return {
        en: domText("previewWord"),
        zh: domText("previewMeaning"),
        phonetic: domText("previewPhonetic"),
        bookTitle: "",
        unitTitle: domText("previewUnit") || "预览",
      };
    }

    function domText(id) {
      const node = $(id);
      return node ? node.textContent.trim() : "";
    }

    let previewDebounce = null;
    function requestPreview() {
      window.clearTimeout(previewDebounce);
      previewDebounce = window.setTimeout(loadPreview, 280);
    }

    async function loadPreview() {
      const selections = readSelections();
      if (!selections.length) return;
      const first = selections[0];
      const bookEl = dom.bookPicker.querySelector(
        '.book-pick[data-book-id="' + CSS.escape(first.wordbook_id) + '"]'
      );
      let unitId = "all";
      if (bookEl) {
        const checked = Array.prototype.find.call(
          bookEl.querySelectorAll(".unit-pick__check"),
          function (check) {
            return check.checked;
          }
        );
        if (checked) unitId = checked.value;
      }
      if (first.unit_ids.indexOf("all") === -1 && first.unit_ids.length) {
        unitId = first.unit_ids[0];
      }

      const token = ++state.previewRequest;
      try {
        const result = await LF.api(
          "/api/preview?book=" +
            encodeURIComponent(first.wordbook_id) +
            "&unit=" +
            encodeURIComponent(unitId),
          { timeout: 8000 }
        );
        if (token !== state.previewRequest || !result || !result.word) return;
        state.sample = result.word;
        state.previewFace = 0;
        renderPreview();
        updatePreviewCaption();
        setupPreviewTimer();
      } catch (error) {
        /* 预览失败不影响保存 */
      }
    }

    function renderPreview() {
      const sample = state.sample || {};
      const front = getFront();
      const kind = state.previewFace === 0 ? front : front === "en" ? "zh" : "en";

      if (dom.previewCard) dom.previewCard.dataset.face = kind;
      if (dom.previewFaceLabel) {
        dom.previewFaceLabel.textContent = kind === "zh" ? "中文释义" : "ENGLISH";
      }
      if (dom.previewWatermark) {
        dom.previewWatermark.textContent = kind === "zh" ? "中" : "Aa";
      }
      if (dom.previewWordBlock) dom.previewWordBlock.hidden = kind === "zh";
      if (dom.previewMeaningBlock) dom.previewMeaningBlock.hidden = kind !== "zh";

      if (dom.previewWord) dom.previewWord.textContent = sample.en || "serendipity";
      if (dom.previewPhonetic) dom.previewPhonetic.textContent = sample.phonetic || "";
      if (dom.previewMeaning) dom.previewMeaning.textContent = sample.zh || "暂无释义";
      if (dom.previewUnit) dom.previewUnit.textContent = sample.unitTitle || "预览";

      if (dom.previewWord) {
        const length = (sample.en || "").length;
        dom.previewWord.classList.toggle("is-long", length > 11);
        dom.previewWord.classList.toggle("is-xlong", length > 19);
      }
      if (dom.previewMeaning) {
        const length = (sample.zh || "").length;
        dom.previewMeaning.classList.toggle("is-long", length > 32);
        dom.previewMeaning.classList.toggle("is-xlong", length > 80);
      }
      updatePreviewAudioVolume();
    }

    function updatePreviewCaption() {
      if (!dom.previewCaption) return;
      const parts = [];
      parts.push(getFront() === "zh" ? "先中后英" : "先英后中");
      parts.push(getOrder() === "random" ? "随机种子" : "词书顺序");
      parts.push(
        getMode() === "manual"
          ? "手动切换"
          : formatInterval(getInterval()) + " 秒/面"
      );
      const selections = readSelections();
      if (selections.length > 1) parts.push(selections.length + " 本词书");
      dom.previewCaption.textContent = parts.join(" · ");
    }

    function flipPreview() {
      if (!state.sample) return;
      state.previewFace = state.previewFace === 0 ? 1 : 0;
      renderPreview();
      setPreviewProgress(100);
      clearPreviewTimer();
      setupPreviewTimer();
    }

    function setupPreviewTimer() {
      clearPreviewTimer();
      if (state.previewHidden) return;
      if (getMode() === "manual") return;
      const duration = Math.max(1000, Math.round(getInterval() * 1000));
      animatePreviewProgress(duration);
      state.previewTimer = window.setTimeout(function () {
        state.previewTimer = null;
        flipPreview();
      }, duration);
    }

    function clearPreviewTimer() {
      if (state.previewTimer) {
        window.clearTimeout(state.previewTimer);
        state.previewTimer = null;
      }
    }

    function animatePreviewProgress(duration) {
      if (!dom.previewProgress) return;
      dom.previewProgress.style.transition = "none";
      dom.previewProgress.style.width = "0%";
      void dom.previewProgress.offsetWidth;
      dom.previewProgress.style.transition = "width " + duration + "ms linear";
      dom.previewProgress.style.width = "100%";
    }

    function setPreviewProgress(percent) {
      if (!dom.previewProgress) return;
      dom.previewProgress.style.transition = "none";
      dom.previewProgress.style.width = Math.max(0, Math.min(100, percent)) + "%";
    }

    function playPreviewAudio() {
      const sample = state.sample || {};
      if (!sample.en || !dom.previewSound) return;
      if (dom.audioToggle && !dom.audioToggle.checked) return;
      try {
        previewAudio.src = LF.url("/api/audio?word=" + encodeURIComponent(sample.en));
        previewAudio.volume = Number(dom.volumeRange ? dom.volumeRange.value : 80) / 100;
      } catch (error) {
        return;
      }
      dom.previewSound.classList.add("is-playing");
      const promise = previewAudio.play();
      if (promise && typeof promise.catch === "function") {
        promise.catch(function () {
          if (dom.previewSound) dom.previewSound.classList.remove("is-playing");
          LF.toast("浏览器阻止了自动播放，请再点一次试听。", { type: "error" });
        });
      }
    }

    function updatePreviewAudioVolume() {
      const volume = Number(dom.volumeRange ? dom.volumeRange.value : 80) / 100;
      if (isFinite(volume)) previewAudio.volume = volume;
    }

    /* ---------------------------------------------------------- 保存状态 */
    function readPayload() {
      const payload = {
        selections: readSelections(),
        front: getFront(),
        order: getOrder(),
        seed: dom.seedInput ? LF.sanitizeSeed(dom.seedInput.value) : "",
        mode: getMode(),
        interval_seconds: getInterval(),
        audio_enabled: dom.audioToggle ? dom.audioToggle.checked : true,
        audio_volume: Number(dom.volumeRange ? dom.volumeRange.value : 80) / 100,
      };
      if (dom.audioPrefetchToggle) {
        payload.audio_prefetch = dom.audioPrefetchToggle.checked;
      }
      return payload;
    }

    function queueSave(options) {
      if (!state.hasBooks) return;
      const immediate = Boolean(options && options.immediate);
      if (!readSelections().length) {
        setSaveStatus("error", "请至少选择一本词书");
        if (dom.saveRetry) dom.saveRetry.hidden = true;
        state.hasUnsaved = true;
        state.saveFailed = true;
        return;
      }
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
        const result = await LF.api("/api/settings", {
          method: "PUT",
          body: payload,
          timeout: 16000,
        });
        if (!result || !result.ok) {
          throw new LF.ApiError("服务器没有确认本次保存", 0, "save_unconfirmed");
        }
        if (result.preferences && result.preferences.seed && dom.seedInput) {
          dom.seedInput.value = result.preferences.seed;
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
        LF.toast("登录已过期，正在返回密码验证页…", {
          type: "error",
          duration: 3200,
        });
        redirectSoon(LF.url("/settings"));
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
      LF.toast((error && error.message) || "保存失败，请检查网络后重试。", {
        type: "error",
        duration: 4400,
      });
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

    /* ------------------------------------------------------------- 表单同步 */
    function syncRangeVisuals() {
      if (dom.intervalRange) LF.updateRangeFill(dom.intervalRange);
      if (dom.volumeRange) LF.updateRangeFill(dom.volumeRange);
    }

    function syncIntervalUi(restartPreview) {
      if (!dom.intervalRange) return;
      const interval = getInterval();
      if (dom.intervalOutput) dom.intervalOutput.textContent = formatInterval(interval);
      LF.updateRangeFill(dom.intervalRange);
      const manual = getMode() === "manual";
      if (dom.intervalField) dom.intervalField.classList.toggle("is-inactive", manual);
      if (dom.intervalHint) {
        dom.intervalHint.textContent = manual
          ? "当前为手动切换；切换到自动模式后此间隔生效。"
          : "每一面停留 " + formatInterval(interval) + " 秒后，自动切换到下一面。";
      }
      if (restartPreview) {
        state.previewFace = 0;
        renderPreview();
      }
      updatePreviewCaption();
      if (restartPreview) setupPreviewTimer();
    }

    function syncAudioUi() {
      const enabled = dom.audioToggle ? dom.audioToggle.checked : true;
      if (dom.volumeField) dom.volumeField.classList.toggle("is-inactive", !enabled);
      if (dom.previewSound) {
        dom.previewSound.disabled = !enabled;
        dom.previewSound.title = enabled ? "试听单词发音" : "发音已关闭";
      }
      if (!enabled) previewAudio.pause();
    }

    function syncVolumeUi() {
      if (!dom.volumeRange) return;
      const volume = Number(dom.volumeRange.value);
      if (dom.volumeOutput) {
        dom.volumeOutput.textContent = String(
          isFinite(volume) ? Math.round(volume) : 80
        );
      }
      LF.updateRangeFill(dom.volumeRange);
      updatePreviewAudioVolume();
    }

    function formatInterval(value) {
      const number = Number(value);
      if (!isFinite(number)) return "4";
      return Number.isInteger(number) ? String(number) : number.toFixed(1);
    }

    /* --------------------------------------------------------------- 重置/退出 */
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
        const result = await LF.api("/api/settings/reset", {
          method: "POST",
          timeout: 12000,
        });
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
          /* 保存失败也应允许退出 */
        }
      }
      try {
        await LF.api("/api/auth/logout", { method: "POST", timeout: 12000 });
        window.location.href = LF.url("/settings");
      } catch (error) {
        dom.logoutBtn.disabled = false;
        LF.toast(error.message || "退出登录失败，请稍后再试。", { type: "error" });
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
