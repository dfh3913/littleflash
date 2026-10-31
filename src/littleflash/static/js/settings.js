/* LittleFlash · settings page（多词书 / 多单元 / 随机种子 / 音频预缓存） */
(function () {
  "use strict";

  const LF = window.LF;
  const $ = function (id) {
    return document.getElementById(id);
  };

  // 预览窗一次拉取多少张卡，供上一张/下一张切换。
  const PREVIEW_SAMPLE_SIZE = 24;

  // 首次使用向导的三步，既用于文案也用于把对应设置卡片滚动到视野里。
  const WIZARD_STEPS = [
    {
      target: "bookSection",
      title: "第 1 步 · 选择学习范围",
      body: "勾选词书后，在展开的列表里点选至少一个单元。可以同时选多本词书、每本的多个单元。",
    },
    {
      target: "displaySection",
      title: "第 2 步 · 设定闪示节奏",
      body: "普通词书可以选先英后中或先中后英；「词性转换」卡片固定先正面后背面，不受这里影响。",
    },
    {
      target: "audioSection",
      title: "第 3 步 · 确认声音",
      body: "开启后每一面都会朗读当前单词。浏览器首次播放需要一次点击授权，之后会自动播放。",
    },
  ];

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
      formationToggle: $("formationToggle"),
      formationOptions: $("formationOptions"),
      cacheIntervalRange: $("cacheIntervalRange"),
      cacheIntervalOutput: $("cacheIntervalOutput"),
      cacheLimitInput: $("cacheLimitInput"),
      cacheLimitOutput: $("cacheLimitOutput"),
      cacheStats: $("cacheStats"),
      fontRange: $("fontRange"),
      fontOutput: $("fontOutput"),
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
      previewIndex: $("previewIndex"),
      previewCaption: $("previewCaption"),
      previewProgress: $("previewProgress"),
      previewSound: $("previewSound"),
      previewPrev: $("previewPrev"),
      previewPlay: $("previewPlay"),
      previewNext: $("previewNext"),
      previewBadge: $("previewBadge"),
      previewBadgeText: $("previewBadgeText"),
      wizard: $("wizard"),
      wizardTitle: $("wizardTitle"),
      wizardBody: $("wizardBody"),
      wizardSteps: $("wizardSteps"),
      wizardNote: $("wizardNote"),
      wizardBack: $("wizardBack"),
      wizardNext: $("wizardNext"),
      wizardSkip: $("wizardSkip"),
      wizardPull: $("wizardPull"),
      wizardReopen: $("wizardReopen"),
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
      samples: [readInitialSample()],
      sampleIndex: 0,
      previewFace: 0,
      previewPlaying: false,
      previewTimer: null,
      previewHidden: document.hidden,
      resetBusy: false,
      wizardStep: 0,
      reloadAfterSave: false,
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
      buildFormationPickers();
      if (state.localMode) {
        applyLocalPreferences();
      }
      syncBookUi();
      syncRangeVisuals();
      syncIntervalUi(false);
      syncFontUi();
      syncVolumeUi();
      syncAudioUi();
      syncSeedUi();
      syncFormationUi();
      syncCacheUi();
      renderPreview();
      setupPreviewTimer();
      syncPreviewControls();
      updatePreviewCaption();
      setSaveStatus("saved", savedLabel());
      if (dom.seedInput && !LF.sanitizeSeed(dom.seedInput.value) && getOrder() === "random") {
        dom.seedInput.value = LF.generateSeed();
        syncSeedUi();
        queueSave();
      }
      // 首屏先取一小批单词，预览窗才有多张可以切换。
      if (readSelections().length) loadPreview();
      initWizard();
    }

    /* ---------------------------------------------------------- 词性转换 */
    function syncFormationUi() {
      if (!dom.formationToggle) return;
      const enabled = dom.formationToggle.checked;
      if (dom.formationOptions) {
        dom.formationOptions.classList.toggle("is-inactive", !enabled);
        dom.formationOptions.querySelectorAll("input").forEach(function (input) {
          input.disabled = !enabled;
        });
      }
    }

    /* ------------------------------------------------------------ 缓存 */
    function syncCacheUi() {
      if (dom.cacheIntervalRange) {
        LF.updateRangeFill(dom.cacheIntervalRange);
        if (dom.cacheIntervalOutput) {
          dom.cacheIntervalOutput.textContent = formatInterval(
            Number(dom.cacheIntervalRange.value)
          );
        }
      }
      if (dom.cacheLimitInput && dom.cacheLimitOutput) {
        const limit = clampInt(dom.cacheLimitInput.value, 100, 20000, 2000);
        dom.cacheLimitOutput.textContent = String(limit);
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
      if (dom.fontRange) {
        const scale = Number(stored.font_scale);
        if (isFinite(scale) && scale > 0) {
          dom.fontRange.value = String(
            Math.round(Math.min(1.8, Math.max(0.8, scale)) * 100)
          );
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
          if (target.classList.contains("formation-group__check")) {
            const group = target.closest(".formation-group");
            if (group) {
              group.querySelectorAll(".unit-pick__check").forEach(function (check) {
                check.checked = target.checked;
              });
            }
            const bookEl = target.closest(".book-pick");
            if (bookEl) {
              const bookCheck = bookEl.querySelector(".book-pick__check");
              const anyChecked =
                bookEl.querySelector(".unit-pick__check:checked") !== null;
              if (bookCheck) bookCheck.checked = target.checked || anyChecked;
            }
            if (target.checked && group) {
              group.classList.add("is-open");
              const toggle = group.querySelector(".formation-group__toggle");
              if (toggle) toggle.setAttribute("aria-expanded", "true");
            }
            syncBookUi();
            queueSave();
            requestPreview();
            return;
          }
          if (target.classList.contains("book-pick__check")) {
            const bookEl = target.closest(".book-pick");
            // 勾选词书只展开单元列表，默认不预选任何单元；取消勾选才清空本书单元。
            if (!target.checked) {
              bookEl.querySelectorAll(".unit-pick__check").forEach(function (unitCheck) {
                unitCheck.checked = false;
              });
            }
            syncBookUi();
            requestPreview();
            queueSave();
            if (target.checked && !bookEl.querySelector(".unit-pick__check:checked")) {
              const firstUnit = bookEl.querySelector(".unit-pick__check");
              if (firstUnit) firstUnit.focus({ preventScroll: true });
            }
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
          const formationButton = event.target.closest(
            "[data-formation-all], [data-formation-none]"
          );
          if (formationButton) {
            event.preventDefault();
            const bookId =
              formationButton.getAttribute("data-formation-all") ||
              formationButton.getAttribute("data-formation-none");
            const bookEl = dom.bookPicker.querySelector(
              '.book-pick[data-book-id="' + CSS.escape(bookId) + '"]'
            );
            if (!bookEl) return;
            const selectAll = formationButton.hasAttribute("data-formation-all");
            bookEl.querySelectorAll(".unit-pick__check").forEach(function (check) {
              check.checked = selectAll;
            });
            const bookCheck = bookEl.querySelector(".book-pick__check");
            if (bookCheck) bookCheck.checked = selectAll;
            syncBookUi();
            queueSave();
            requestPreview();
            return;
          }
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

      if (dom.formationToggle) {
        dom.formationToggle.addEventListener("change", function () {
          syncFormationUi();
          state.reloadAfterSave = true;
          queueSave({ immediate: true });
        });
      }

      ["formation_group", "formation_filter"].forEach(function (name) {
        document.querySelectorAll('input[name="' + name + '"]').forEach(function (input) {
          input.addEventListener("change", function () {
            state.reloadAfterSave = true;
            requestPreview();
            queueSave({ immediate: true });
          });
        });
      });

      if (dom.cacheIntervalRange) {
        dom.cacheIntervalRange.addEventListener("input", function () {
          syncCacheUi();
          queueSave();
        });
        dom.cacheIntervalRange.addEventListener("change", function () {
          queueSave({ immediate: true });
        });
      }

      if (dom.cacheLimitInput) {
        dom.cacheLimitInput.addEventListener("change", function () {
          dom.cacheLimitInput.value = String(
            clampInt(dom.cacheLimitInput.value, 100, 20000, 2000)
          );
          syncCacheUi();
          queueSave({ immediate: true });
        });
        dom.cacheLimitInput.addEventListener("input", function () {
          if (dom.cacheLimitOutput) {
            dom.cacheLimitOutput.textContent = String(
              clampInt(dom.cacheLimitInput.value, 100, 20000, 2000)
            );
          }
        });
      }

      if (dom.fontRange) {
        dom.fontRange.addEventListener("input", function () {
          syncFontUi();
          queueSave();
        });
        dom.fontRange.addEventListener("change", function () {
          queueSave({ immediate: true });
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
          } else if (event.key === "ArrowLeft") {
            event.preventDefault();
            stepSample(-1, false);
          } else if (event.key === "ArrowRight") {
            event.preventDefault();
            stepSample(1, false);
          }
        });
      }

      if (dom.previewPrev) {
        dom.previewPrev.addEventListener("click", function () {
          stepSample(-1, false);
        });
      }

      if (dom.previewNext) {
        dom.previewNext.addEventListener("click", function () {
          stepSample(1, false);
        });
      }

      if (dom.previewPlay) {
        dom.previewPlay.addEventListener("click", togglePreviewPlay);
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
        const checkedUnits = bookEl.querySelectorAll(".unit-pick__check:checked").length;
        if (bookCheck) {
          bookEl.classList.toggle("is-selected", bookCheck.checked);
          // 已勾选词书但还没点任何单元：提示用户继续选单元。
          bookEl.classList.toggle("is-empty", bookCheck.checked && checkedUnits === 0);
          if (panel) panel.hidden = !bookCheck.checked;
        }
        bookEl.querySelectorAll(".unit-pick").forEach(function (unitEl) {
          const check = unitEl.querySelector(".unit-pick__check");
          unitEl.classList.toggle("is-checked", Boolean(check && check.checked));
        });
      });
      syncFormationGroups(dom.bookPicker);
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

    /* ------------------------------------------------- 词性转换选择界面
       卡片书不按普通词书那样平铺单元，而是按「大类 → 变法（→ 常规/非常规）」
       渲染成一棵可勾选的树，组头支持三态全选。 */
    function buildFormationPickers() {
      document.querySelectorAll(".formation-groups").forEach(function (container) {
        const wrap = container.closest(".book-pick__units");
        if (!wrap) return;
        const bookId = container.getAttribute("data-formation-book") || "";
        const dataNode = wrap.querySelector(".formation-data");
        const selectedNode = wrap.querySelector(".formation-selected");
        let units = [];
        let selected = [];
        try {
          units = JSON.parse(dataNode ? dataNode.textContent : "[]");
        } catch (error) {
          units = [];
        }
        try {
          selected = JSON.parse(selectedNode ? selectedNode.textContent : "[]");
        } catch (error) {
          selected = [];
        }
        if (!Array.isArray(units) || !units.length) return;

        const allSelected = selected.indexOf("all") !== -1;
        const chosen = new Set(
          allSelected
            ? units.map(function (unit) {
                return unit.id;
              })
            : selected
        );

        const groups = new Map();
        units.forEach(function (unit) {
          const key = unit.group || unit.title || "其他";
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key).push(unit);
        });

        container.textContent = "";
        groups.forEach(function (items, title) {
          const section = document.createElement("section");
          section.className = "formation-group";
          const anyChosen = items.some(function (unit) {
            return chosen.has(unit.id);
          });
          section.classList.toggle("is-open", anyChosen);

          const head = document.createElement("div");
          head.className = "formation-group__head";

          const groupCheck = document.createElement("input");
          groupCheck.type = "checkbox";
          groupCheck.className = "formation-group__check";
          groupCheck.setAttribute("data-book", bookId);

          const toggle = document.createElement("button");
          toggle.type = "button";
          toggle.className = "formation-group__toggle";
          toggle.setAttribute("aria-expanded", anyChosen ? "true" : "false");

          const name = document.createElement("span");
          name.className = "formation-group__title";
          name.textContent = title;

          const count = document.createElement("span");
          count.className = "formation-group__count";
          count.textContent =
            items.reduce(function (sum, unit) {
              return sum + (Number(unit.wordCount) || 0);
            }, 0) + " 词";

          const chevron = document.createElement("span");
          chevron.className = "formation-group__chevron";
          chevron.setAttribute("aria-hidden", "true");

          toggle.append(name, count, chevron);
          head.append(groupCheck, toggle);
          section.append(head);

          toggle.addEventListener("click", function () {
            const open = !section.classList.contains("is-open");
            section.classList.toggle("is-open", open);
            toggle.setAttribute("aria-expanded", open ? "true" : "false");
          });

          const list = document.createElement("div");
          list.className = "formation-group__units";
          items.forEach(function (unit) {
            const row = document.createElement("label");
            row.className = "unit-pick formation-unit";
            row.setAttribute("data-word-count", unit.wordCount);

            const input = document.createElement("input");
            input.type = "checkbox";
            input.className = "unit-pick__check";
            input.value = unit.id;
            input.setAttribute("data-book", bookId);
            input.checked = chosen.has(unit.id);

            const label = document.createElement("span");
            label.className = "formation-unit__label";
            label.textContent =
              unit.label && unit.label !== title ? unit.label : "全部";

            row.append(input, label);
            if (unit.type) {
              const badge = document.createElement("span");
              badge.className = "formation-unit__type";
              badge.dataset.type = unit.type === "常规" ? "regular" : "special";
              badge.textContent = unit.type;
              row.append(badge);
            }
            const small = document.createElement("small");
            small.textContent = (Number(unit.wordCount) || 0) + " 词";
            row.append(small);
            list.append(row);
          });
          section.append(list);
          container.append(section);
        });

        syncFormationGroups(container);
      });
    }

    function syncFormationGroups(root) {
      const scope = root || dom.bookPicker;
      if (!scope) return;
      scope.querySelectorAll(".formation-group").forEach(function (group) {
        const head = group.querySelector(".formation-group__check");
        const checks = group.querySelectorAll(".unit-pick__check");
        if (!head) return;
        let checked = 0;
        checks.forEach(function (check) {
          if (check.checked) checked += 1;
        });
        head.checked = checks.length > 0 && checked === checks.length;
        head.indeterminate = checked > 0 && checked < checks.length;
        group.classList.toggle("is-active", checked > 0);
      });
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
      const pendingBooks = dom.bookPicker.querySelectorAll(".book-pick.is-empty").length;
      dom.pickerSummary.classList.toggle("is-empty", selections.length === 0);
      if (selections.length) {
        let text =
          "已选 " +
          selections.length +
          " 本词书 · " +
          units +
          " 个单元 · 约 " +
          words.toLocaleString() +
          " 个单词";
        // 还有勾了却没选单元的词书：已经选中的照常保存，这里只做提醒。
        if (pendingBooks) {
          text += "（另有 " + pendingBooks + " 本还没选单元）";
        }
        dom.pickerSummary.textContent = text;
      } else if (pendingBooks) {
        dom.pickerSummary.textContent =
          "已勾选 " + pendingBooks + " 本词书，还需要在展开的列表里点选至少一个单元。";
      } else {
        dom.pickerSummary.textContent = "还没有选择任何词书，请至少勾选一本。";
      }
      updatePreviewCaption();
    }

    /* 未就绪时的提示语：区分「没勾词书」和「勾了词书但没选单元」。 */
    function selectionHint() {
      if (!dom.bookPicker) return "";
      if (readSelections().length) return "";
      if (dom.bookPicker.querySelector(".book-pick.is-empty")) return "请至少勾选一个单元";
      return "请至少选择一本词书";
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
            encodeURIComponent(unitId) +
            "&limit=" +
            PREVIEW_SAMPLE_SIZE,
          { timeout: 8000 }
        );
        if (token !== state.previewRequest || !result) return;
        const words = Array.isArray(result.words) && result.words.length
          ? result.words
          : result.word
            ? [result.word]
            : [];
        if (!words.length) return;
        state.samples = words;
        state.sampleIndex = 0;
        state.previewFace = 0;
        renderPreview();
        updatePreviewCaption();
        syncPreviewControls();
        setupPreviewTimer();
      } catch (error) {
        /* 预览失败不影响保存 */
      }
    }

    function currentSample() {
      if (!state.samples.length) return {};
      const index = Math.min(Math.max(state.sampleIndex, 0), state.samples.length - 1);
      return state.samples[index] || {};
    }

    function renderPreview() {
      const sample = currentSample();
      if (sample && sample.kind === "cards" && sample.front && sample.back) {
        renderCardPreview(sample);
        return;
      }
      if (dom.previewCard) delete dom.previewCard.dataset.kind;
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
      if (dom.previewUnit) {
        dom.previewUnit.textContent = LF.plainUnitTitle(sample.unitTitle) || "预览";
      }
      if (dom.previewIndex) {
        const total = state.samples.length;
        const position = pad2(state.sampleIndex + 1);
        dom.previewIndex.textContent =
          total > 1 ? position + " / " + pad2(total) : position;
      }

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

    /* 卡片书预览：正面 → 背面，和首页保持一致。 */
    function renderCardPreview(sample) {
      const showingBack = state.previewFace === 1;
      const parts = LF.splitCardLine(showingBack ? sample.back : sample.front);

      if (dom.previewCard) dom.previewCard.dataset.kind = "cards";
      if (dom.previewCard) dom.previewCard.dataset.face = showingBack ? "zh" : "en";
      if (dom.previewFaceLabel) {
        dom.previewFaceLabel.textContent = showingBack ? "背面 · ANSWER" : "正面 · PROMPT";
      }
      if (dom.previewWatermark) {
        dom.previewWatermark.textContent = showingBack ? "答" : "题";
      }
      if (dom.previewWordBlock) dom.previewWordBlock.hidden = false;
      if (dom.previewWord) {
        dom.previewWord.textContent = parts.word || sample.en || "";
        const length = (parts.word || sample.en || "").length;
        dom.previewWord.classList.toggle("is-long", length > 11);
        dom.previewWord.classList.toggle("is-xlong", length > 19);
      }
      if (dom.previewPhonetic) {
        dom.previewPhonetic.textContent = parts.pos || "";
        dom.previewPhonetic.hidden = !parts.pos;
      }
      if (dom.previewMeaningBlock) dom.previewMeaningBlock.hidden = !showingBack;
      if (dom.previewMeaning) {
        const extra = showingBack
          ? [parts.extra, (sample.note || "").trim()].filter(Boolean).join("\n")
          : "";
        dom.previewMeaning.textContent = extra || (showingBack ? "（没有补充说明）" : "");
        dom.previewMeaning.classList.add("is-card");
        dom.previewMeaning.classList.toggle("is-long", extra.length > 32);
        dom.previewMeaning.classList.toggle("is-xlong", extra.length > 80);
      }
      if (dom.previewUnit) {
        dom.previewUnit.textContent = LF.plainUnitTitle(sample.unitTitle) || "预览";
      }
      if (dom.previewIndex) {
        const total = state.samples.length;
        const position = pad2(state.sampleIndex + 1);
        dom.previewIndex.textContent =
          total > 1 ? position + " / " + pad2(total) : position;
      }
      updatePreviewAudioVolume();
    }

    function pad2(number) {
      return String(number).padStart(2, "0");
    }

    function updatePreviewCaption() {
      if (!dom.previewCaption) return;
      const parts = [];
      const sample = currentSample();
      if (sample && sample.kind === "cards") {
        parts.push("正面 → 背面");
      } else {
        parts.push(getFront() === "zh" ? "先中后英" : "先英后中");
      }
      parts.push(getOrder() === "random" ? "随机种子" : "词书顺序");
      parts.push(
        getMode() === "manual"
          ? "手动切换"
          : formatInterval(getInterval()) + " 秒/面"
      );
      const selections = readSelections();
      if (selections.length > 1) parts.push(selections.length + " 本词书");
      parts.push(state.previewPlaying ? "演示中" : "已暂停");
      dom.previewCaption.textContent = parts.join(" · ");
    }

    /* 点卡片：先翻面，已在释义面则切到下一张。 */
    function flipPreview() {
      if (!state.samples.length) return;
      if (state.previewFace === 0) {
        state.previewFace = 1;
        renderPreview();
      } else {
        stepSample(1, false);
        return;
      }
      if (state.previewPlaying) {
        setupPreviewTimer();
      } else {
        setPreviewProgress(0);
      }
    }

    /* 切换上一张 / 下一张卡片；autoplay 为 false 时手动切换不朗读。 */
    function stepSample(delta, autoplay) {
      if (!state.samples.length) return;
      const total = state.samples.length;
      state.sampleIndex = (state.sampleIndex + delta + total) % total;
      state.previewFace = 0;
      renderPreview();
      if (state.previewPlaying) setupPreviewTimer();
      else setPreviewProgress(0);
      if (autoplay) playPreviewAudio();
    }

    function togglePreviewPlay() {
      state.previewPlaying = !state.previewPlaying;
      syncPreviewControls();
      updatePreviewCaption();
      if (state.previewPlaying) {
        setupPreviewTimer();
        playPreviewAudio();
      } else {
        clearPreviewTimer();
        setPreviewProgress(0);
        previewAudio.pause();
        if (dom.previewSound) dom.previewSound.classList.remove("is-playing");
      }
    }

    function syncPreviewControls() {
      const playing = state.previewPlaying;
      if (dom.previewPlay) {
        dom.previewPlay.setAttribute("aria-pressed", playing ? "true" : "false");
        dom.previewPlay.setAttribute("aria-label", playing ? "暂停预览" : "播放预览");
        dom.previewPlay.title = playing ? "暂停预览" : "播放预览";
        LF.setHidden(dom.previewPlay.querySelector(".preview-control__icon-play"), playing);
        LF.setHidden(dom.previewPlay.querySelector(".preview-control__icon-pause"), !playing);
      }
      if (dom.previewBadge) {
        dom.previewBadge.dataset.state = playing ? "playing" : "paused";
      }
      if (dom.previewBadgeText) {
        dom.previewBadgeText.textContent = playing ? "演示中" : "已暂停";
      }
      const single = state.samples.length < 2;
      if (dom.previewPrev) dom.previewPrev.disabled = single;
      if (dom.previewNext) dom.previewNext.disabled = single;
    }

    function setupPreviewTimer() {
      clearPreviewTimer();
      // 预览默认暂停：只有点了播放才开始自动切换。
      if (!state.previewPlaying) {
        setPreviewProgress(0);
        return;
      }
      if (state.previewHidden) return;
      const duration = Math.max(1000, Math.round(getInterval() * 1000));
      animatePreviewProgress(duration);
      state.previewTimer = window.setTimeout(function () {
        state.previewTimer = null;
        if (state.previewFace === 0) {
          state.previewFace = 1;
          renderPreview();
          playPreviewAudio();
          setupPreviewTimer();
        } else {
          stepSample(1, true);
        }
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
      const sample = currentSample();
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
      const formationGroup = document.querySelector(
        'input[name="formation_group"]:checked'
      );
      if (formationGroup) payload.formation_group = formationGroup.value;
      const formationFilter = document.querySelector(
        'input[name="formation_filter"]:checked'
      );
      if (formationFilter) payload.formation_filter = formationFilter.value;
      if (dom.formationToggle) {
        payload.formation_enabled = dom.formationToggle.checked;
      }
      if (dom.cacheIntervalRange) {
        const interval = Number(dom.cacheIntervalRange.value);
        if (isFinite(interval)) {
          payload.cache_interval_seconds = Math.round(interval * 10) / 10;
        }
      }
      if (dom.cacheLimitInput) {
        payload.cache_limit = clampInt(dom.cacheLimitInput.value, 100, 20000, 2000);
      }
      if (dom.fontRange) {
        payload.font_scale = clampInt(dom.fontRange.value, 80, 180, 100) / 100;
      }
      return payload;
    }

    function queueSave(options) {
      if (!state.hasBooks) return;
      const immediate = Boolean(options && options.immediate);
      const hint = selectionHint();
      if (hint) {
        setSaveStatus("error", hint);
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
        if (state.reloadAfterSave) {
          state.reloadAfterSave = false;
          state.hasUnsaved = false;
          setSaveStatus("saved", "词书结构已更新，正在刷新…");
          LF.toast("已更新「词性转换」的展示方式，正在刷新…", {
            type: "success",
            duration: 1800,
          });
          window.setTimeout(function () {
            window.location.reload();
          }, 760);
          return;
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
        error: "请完成选择",
      };
      dom.saveStatus.setAttribute(
        "aria-label",
        text || labels[stateName] || "保存状态"
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
      if (dom.fontRange) LF.updateRangeFill(dom.fontRange);
      if (dom.volumeRange) LF.updateRangeFill(dom.volumeRange);
    }

    function syncFontUi() {
      if (!dom.fontRange) return;
      const value = clampInt(dom.fontRange.value, 80, 180, 100);
      if (dom.fontOutput) dom.fontOutput.textContent = String(value);
      LF.updateRangeFill(dom.fontRange);
      LF.applyFontScale(value / 100);
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

    function clampInt(value, minimum, maximum, fallback) {
      const number = Math.round(Number(value));
      if (!isFinite(number)) return fallback;
      return Math.min(maximum, Math.max(minimum, number));
    }

    /* --------------------------------------------------------- 首次使用向导 */
    function initWizard() {
      if (!dom.wizard) return;
      if (dom.wizardReopen) {
        dom.wizardReopen.addEventListener("click", function () {
          openWizard();
        });
      }
      if (dom.wizardSkip) {
        dom.wizardSkip.addEventListener("click", function () {
          LF.wizard.done();
          closeWizard();
        });
      }
      if (dom.wizardBack) {
        dom.wizardBack.addEventListener("click", function () {
          showWizardStep(state.wizardStep - 1);
        });
      }
      if (dom.wizardNext) {
        dom.wizardNext.addEventListener("click", function () {
          if (state.wizardStep >= WIZARD_STEPS.length - 1) {
            finishWizard();
          } else {
            showWizardStep(state.wizardStep + 1);
          }
        });
      }
      if (dom.wizardPull) {
        dom.wizardPull.addEventListener("click", pullServerConfig);
      }
      if (dom.wizardSteps) {
        dom.wizardSteps.addEventListener("click", function (event) {
          const item = event.target.closest("li[data-target]");
          if (!item) return;
          const index = Array.prototype.indexOf.call(dom.wizardSteps.children, item);
          if (index >= 0) showWizardStep(index);
        });
      }

      const params = new URLSearchParams(window.location.search);
      if (params.get("wizard") === "1" || LF.wizard.needs()) {
        openWizard();
      }
    }

    function openWizard() {
      if (!dom.wizard || dom.wizard.open) return;
      showWizardStep(0);
      if (typeof dom.wizard.showModal === "function") {
        dom.wizard.showModal();
      } else {
        dom.wizard.setAttribute("open", "");
      }
      window.setTimeout(function () {
        if (dom.wizardNext) dom.wizardNext.focus({ preventScroll: true });
      }, 60);
    }

    function closeWizard() {
      if (!dom.wizard) return;
      document.querySelectorAll(".is-wizard-focus").forEach(function (node) {
        node.classList.remove("is-wizard-focus");
      });
      if (typeof dom.wizard.close === "function" && dom.wizard.open) {
        dom.wizard.close();
      } else {
        dom.wizard.removeAttribute("open");
      }
    }

    function showWizardStep(index) {
      const step = WIZARD_STEPS[Math.max(0, Math.min(WIZARD_STEPS.length - 1, index))];
      state.wizardStep = WIZARD_STEPS.indexOf(step);
      if (dom.wizardTitle) dom.wizardTitle.textContent = step.title;
      if (dom.wizardBody) dom.wizardBody.textContent = step.body;

      if (dom.wizardSteps) {
        Array.prototype.forEach.call(dom.wizardSteps.children, function (item, position) {
          const active = position === state.wizardStep;
          item.classList.toggle("is-active", active);
          item.classList.toggle("is-done", position < state.wizardStep);
          item.setAttribute("aria-current", active ? "step" : "false");
        });
      }

      document.querySelectorAll(".is-wizard-focus").forEach(function (node) {
        node.classList.remove("is-wizard-focus");
      });
      const target = document.getElementById(step.target);
      if (target) {
        target.classList.add("is-wizard-focus");
        target.scrollIntoView({
          behavior: LF.prefersReducedMotion() ? "auto" : "smooth",
          block: "start",
        });
      }

      if (dom.wizardBack) dom.wizardBack.disabled = state.wizardStep === 0;
      if (dom.wizardNext) {
        const label = dom.wizardNext.querySelector(".primary-action__label");
        if (label) {
          label.textContent =
            state.wizardStep === WIZARD_STEPS.length - 1 ? "开始学习" : "下一步";
        }
        dom.wizardNext.setAttribute(
          "aria-label",
          state.wizardStep === WIZARD_STEPS.length - 1 ? "开始学习" : "下一步"
        );
      }
      if (dom.wizardNote) dom.wizardNote.textContent = wizardNote();
    }

    function wizardNote() {
      if (state.wizardStep === 0) {
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
        if (!selections.length) return "还没有选择范围，先勾选一本词书吧。";
        return (
          "已选 " + selections.length + " 本词书 · " + units + " 个单元 · 约 " +
          words.toLocaleString() + " 个单词"
        );
      }
      if (state.wizardStep === 1) {
        return [
          getFront() === "zh" ? "先中后英" : "先英后中",
          getOrder() === "random" ? "随机顺序" : "词书顺序",
          getMode() === "manual" ? "手动切换" : "自动 " + formatInterval(getInterval()) + " 秒/面",
        ].join(" · ");
      }
      const audioOn = dom.audioToggle ? dom.audioToggle.checked : true;
      return (
        "发音 " + (audioOn ? "开启" : "关闭") +
        " · 音量 " + (dom.volumeRange ? dom.volumeRange.value : "80") + "%"
      );
    }

    async function pullServerConfig() {
      if (dom.wizardPull) LF.setLoading(dom.wizardPull, true);
      try {
        const result = await LF.api("/api/settings", { timeout: 10000 });
        const prefs = result && result.preferences;
        if (!prefs) throw new LF.ApiError("服务器没有返回配置", 0, "empty_config");
        applySelections(LF.normalizeSelections(prefs, booksFromDom()));
        setChoice("front", prefs.front === "zh" ? "zh" : "en");
        setChoice("order", prefs.order === "random" ? "random" : "sequence");
        setChoice("mode", prefs.mode === "manual" ? "manual" : "auto");
        if (dom.seedInput) dom.seedInput.value = LF.sanitizeSeed(prefs.seed);
        if (dom.intervalRange) {
          dom.intervalRange.value = String(
            Math.min(30, Math.max(1, Number(prefs.interval_seconds) || 4))
          );
        }
        if (dom.audioToggle && typeof prefs.audio_enabled === "boolean") {
          dom.audioToggle.checked = prefs.audio_enabled;
        }
        if (dom.volumeRange) {
          const volume = Number(prefs.audio_volume);
          dom.volumeRange.value = String(
            Math.round(Math.min(1, Math.max(0, isFinite(volume) ? volume : 0.8)) * 100)
          );
        }
        if (dom.audioPrefetchToggle && typeof prefs.audio_prefetch === "boolean") {
          dom.audioPrefetchToggle.checked = prefs.audio_prefetch;
        }
        if (dom.fontRange) {
          const scale = Number(prefs.font_scale);
          if (isFinite(scale) && scale > 0) {
            dom.fontRange.value = String(
              Math.round(Math.min(1.8, Math.max(0.8, scale)) * 100)
            );
          }
        }
        syncBookUi();
        syncRangeVisuals();
        syncIntervalUi(false);
        syncFontUi();
        syncVolumeUi();
        syncAudioUi();
        syncSeedUi();
        renderPreview();
        updatePreviewCaption();
        queueSave({ immediate: true });
        LF.toast("已套用服务器上的配置", { type: "success", duration: 2200 });
        showWizardStep(state.wizardStep);
      } catch (error) {
        LF.toast(
          (error && error.message) || "拉取服务器配置失败，请稍后重试",
          { type: "error", duration: 3600 }
        );
      } finally {
        if (dom.wizardPull) LF.setLoading(dom.wizardPull, false);
      }
    }

    function finishWizard() {
      LF.wizard.done();
      queueSave({ immediate: true });
      closeWizard();
      LF.toast("设置已就绪，开始闪词吧", { type: "success", duration: 2200 });
      goHomeWhenSaved();
    }

    /* 等保存落地再跳走，避免服务器模式下最后一步改动丢失。 */
    function goHomeWhenSaved() {
      const started = Date.now();
      (function wait() {
        const busy = state.saving || (state.hasUnsaved && !state.saveFailed);
        if (busy && Date.now() - started < 4000) {
          window.setTimeout(wait, 120);
          return;
        }
        window.location.href = LF.url("/");
      })();
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
