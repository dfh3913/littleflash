/* LittleFlash · shared browser helpers */
(function () {
  "use strict";

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = csrfMeta ? csrfMeta.getAttribute("content") || "" : "";

  /* 应用可能部署在子路径（如 /littleflash）下，base 由服务端注入。 */
  const baseMeta = document.querySelector('meta[name="app-base"]');
  const basePath = ((baseMeta && baseMeta.getAttribute("content")) || "")
    .replace(/\/+$/, "");

  function url(path) {
    if (!path) return basePath || "/";
    if (/^[a-z][a-z0-9+.-]*:/i.test(path) || path.slice(0, 2) === "//") {
      return path;
    }
    return basePath + (path.charAt(0) === "/" ? path : "/" + path);
  }

  /* --------------------------------------------------------- 本机学习设置
     未登录时设置页把偏好写进 localStorage：只影响当前浏览器，
     不会同步给其他访问者，也不会写进服务器配置。 */
  const LOCAL_PREFS_KEY = "littleflash.preferences.v1";
  const SEED_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const MAX_SEED_LENGTH = 64;

  function sanitizeSeed(value, fallback) {
    if (typeof value !== "string") return fallback === undefined ? "" : fallback;
    const text = value
      .replace(/[\u0000-\u001f\u007f]/g, "")
      .trim()
      .slice(0, MAX_SEED_LENGTH);
    if (text) return text;
    return fallback === undefined ? "" : fallback;
  }

  function generateSeed(length) {
    const size = Math.max(4, Math.min(MAX_SEED_LENGTH, length || 8));
    const buffer = new Uint32Array(size);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(buffer);
    } else {
      for (let i = 0; i < size; i += 1) {
        buffer[i] = Math.floor(Math.random() * 0xffffffff);
      }
    }
    let result = "";
    for (let i = 0; i < size; i += 1) {
      result += SEED_ALPHABET.charAt(buffer[i] % SEED_ALPHABET.length);
    }
    return result;
  }

  function sanitizeSelections(value) {
    if (!Array.isArray(value)) return null;
    const result = [];
    const seen = new Set();
    value.forEach(function (item) {
      if (!item || typeof item !== "object" || Array.isArray(item)) return;
      const bookId = item.wordbook_id;
      if (typeof bookId !== "string" || !bookId || seen.has(bookId)) return;
      let units = [];
      if (Array.isArray(item.unit_ids)) {
        units = item.unit_ids
          .filter(function (unitId) {
            return typeof unitId === "string" && unitId.length > 0;
          })
          .slice(0, 200);
      }
      if (!units.length) units = ["all"];
      seen.add(bookId);
      result.push({ wordbook_id: bookId, unit_ids: units });
    });
    return result.length ? result.slice(0, 50) : null;
  }

  function normalizeSelections(prefs, books) {
    const list = Array.isArray(books) ? books : [];
    const byId = new Map();
    list.forEach(function (book) {
      if (book && typeof book.id === "string") byId.set(book.id, book);
    });
    const source =
      prefs && typeof prefs === "object" && Array.isArray(prefs.selections)
        ? prefs.selections
        : prefs && typeof prefs === "object" && prefs.wordbook_id
        ? [
            {
              wordbook_id: prefs.wordbook_id,
              unit_ids: prefs.unit_id && prefs.unit_id !== "all" ? [prefs.unit_id] : ["all"],
            },
          ]
        : [];

    const result = [];
    const seen = new Set();
    source.forEach(function (item) {
      if (!item || typeof item !== "object") return;
      const book = byId.get(item.wordbook_id);
      if (!book || seen.has(book.id)) return;
      const knownUnits = new Set(
        (book.units || []).map(function (unit) {
          return unit.id;
        })
      );
      let unitIds = [];
      if (Array.isArray(item.unit_ids) && item.unit_ids.indexOf("all") === -1) {
        unitIds = item.unit_ids.filter(function (unitId) {
          return knownUnits.has(unitId);
        });
      }
      if (!unitIds.length) unitIds = ["all"];
      seen.add(book.id);
      result.push({ wordbook_id: book.id, unit_ids: unitIds });
    });

    if (!result.length && list.length) {
      result.push({ wordbook_id: list[0].id, unit_ids: ["all"] });
    }
    return result;
  }

  function selectionQuery(selections) {
    return (Array.isArray(selections) ? selections : [])
      .map(function (selection) {
        const units =
          selection.unit_ids.indexOf("all") !== -1
            ? "all"
            : selection.unit_ids.join(",");
        return selection.wordbook_id + "~" + units;
      })
      .join("|");
  }

  function hashSeed(value) {
    const text = String(value == null ? "" : value);
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

  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function seededRandom(seed) {
    return mulberry32(hashSeed(seed)());
  }

  function shuffleWithSeed(array, seed) {
    const random = seededRandom(seed);
    for (let i = array.length - 1; i > 0; i -= 1) {
      const j = Math.floor(random() * (i + 1));
      const value = array[i];
      array[i] = array[j];
      array[j] = value;
    }
    return array;
  }

  function sanitizePreferences(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return null;
    }
    const clean = {};
    if (typeof value.wordbook_id === "string" && value.wordbook_id) {
      clean.wordbook_id = value.wordbook_id;
    }
    if (typeof value.unit_id === "string" && value.unit_id) {
      clean.unit_id = value.unit_id;
    }
    const selections = sanitizeSelections(value.selections);
    if (selections) clean.selections = selections;
    if (value.front === "en" || value.front === "zh") {
      clean.front = value.front;
    }
    if (
      value.order === "sequence" ||
      value.order === "random" ||
      value.order === "shuffle"
    ) {
      clean.order = value.order === "shuffle" ? "random" : value.order;
    }
    const seed = sanitizeSeed(value.seed);
    if (seed) clean.seed = seed;
    if (value.mode === "auto" || value.mode === "manual") {
      clean.mode = value.mode;
    }
    const interval = Number(value.interval_seconds);
    if (isFinite(interval)) {
      clean.interval_seconds = Math.min(
        30,
        Math.max(1, Math.round(interval * 2) / 2)
      );
    }
    if (typeof value.audio_enabled === "boolean") {
      clean.audio_enabled = value.audio_enabled;
    }
    if (typeof value.audio_prefetch === "boolean") {
      clean.audio_prefetch = value.audio_prefetch;
    }
    const volume = Number(value.audio_volume);
    if (isFinite(volume)) {
      clean.audio_volume = Math.min(1, Math.max(0, Math.round(volume * 100) / 100));
    }
    return Object.keys(clean).length > 0 ? clean : null;
  }

  const localPrefs = {
    read: function () {
      let raw = null;
      try {
        raw = window.localStorage.getItem(LOCAL_PREFS_KEY);
      } catch (error) {
        return null;
      }
      if (!raw) return null;
      try {
        return sanitizePreferences(JSON.parse(raw));
      } catch (error) {
        return null;
      }
    },
    write: function (value) {
      const clean = sanitizePreferences(value);
      if (!clean) return false;
      try {
        window.localStorage.setItem(LOCAL_PREFS_KEY, JSON.stringify(clean));
        return true;
      } catch (error) {
        return false;
      }
    },
    clear: function () {
      try {
        window.localStorage.removeItem(LOCAL_PREFS_KEY);
      } catch (error) {
        /* 隐私模式下可能不可用，忽略即可 */
      }
    },
    key: LOCAL_PREFS_KEY,
  };

  class ApiError extends Error {
    constructor(message, status, code, payload, retryAfter) {
      super(message);
      this.name = "ApiError";
      this.status = status || 0;
      this.code = code || "request_failed";
      this.payload = payload || null;
      this.retryAfter = retryAfter || 0;
    }
  }

  async function api(path, options) {
    const settings = Object.assign(
      { method: "GET", body: undefined, timeout: 16000 },
      options || {}
    );
    const headers = { Accept: "application/json" };
    let body;
    if (settings.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(settings.body);
    }
    if (settings.method !== "GET" && settings.method !== "HEAD") {
      if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(function () {
      controller.abort();
    }, settings.timeout);

    let response;
    try {
      response = await fetch(url(path), {
        method: settings.method,
        headers: headers,
        body: body,
        credentials: "same-origin",
        signal: controller.signal,
        cache: "no-store",
      });
    } catch (error) {
      if (error && error.name === "AbortError") {
        throw new ApiError("请求超时，请检查网络后重试", 0, "timeout");
      }
      throw new ApiError("网络连接失败，请稍后重试", 0, "network_error");
    } finally {
      window.clearTimeout(timer);
    }

    const contentType = response.headers.get("Content-Type") || "";
    let payload = null;
    if (contentType.indexOf("application/json") !== -1) {
      try {
        payload = await response.json();
      } catch (error) {
        payload = null;
      }
    }

    if (!response.ok) {
      const message =
        (payload && payload.error) || "请求未能完成，请稍后再试";
      const retryAfter = Number(response.headers.get("Retry-After") || 0);
      throw new ApiError(
        message,
        response.status,
        payload && payload.code,
        payload,
        retryAfter
      );
    }
    return payload;
  }

  let toastRegion = null;

  function getToastRegion() {
    if (!toastRegion) {
      toastRegion = document.getElementById("toastRegion");
    }
    if (!toastRegion) {
      toastRegion = document.createElement("div");
      toastRegion.id = "toastRegion";
      toastRegion.className = "toast-region";
      toastRegion.setAttribute("role", "status");
      toastRegion.setAttribute("aria-live", "polite");
      document.body.appendChild(toastRegion);
    }
    return toastRegion;
  }

  function toast(message, options) {
    if (!message) return null;
    const config = Object.assign(
      { type: "info", duration: 3600 },
      options || {}
    );
    const region = getToastRegion();
    while (region.children.length >= 3) {
      region.removeChild(region.firstElementChild);
    }
    const item = document.createElement("div");
    item.className = "toast toast--" + (config.type || "info");
    item.textContent = message;
    region.appendChild(item);

    window.requestAnimationFrame(function () {
      item.classList.add("is-visible");
    });

    const timeout = window.setTimeout(function () {
      dismissToast(item);
    }, config.duration);
    item._dismissTimer = timeout;
    return item;
  }

  function dismissToast(item) {
    if (!item || !item.parentNode) return;
    if (item._dismissTimer) window.clearTimeout(item._dismissTimer);
    item.classList.remove("is-visible");
    window.setTimeout(function () {
      if (item.parentNode) item.parentNode.removeChild(item);
    }, 260);
  }

  function setLoading(button, loading) {
    if (!button) return;
    button.classList.toggle("is-loading", Boolean(loading));
    button.setAttribute("aria-busy", loading ? "true" : "false");
    if (loading) {
      button.dataset.wasDisabled = button.disabled ? "1" : "0";
      button.disabled = true;
    } else {
      button.disabled = button.dataset.wasDisabled === "1";
      delete button.dataset.wasDisabled;
    }
  }

  function debounce(fn, wait) {
    let timer = null;
    return function () {
      const args = arguments;
      const context = this;
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        fn.apply(context, args);
      }, wait);
    };
  }

  function updateRangeFill(input) {
    if (!input) return;
    const minimum = Number(input.min || 0);
    const maximum = Number(input.max || 100);
    const value = Number(input.value || minimum);
    const span = maximum - minimum || 1;
    const percent = ((value - minimum) / span) * 100;
    input.style.setProperty("--fill", percent.toFixed(2) + "%");
  }

  function prefersReducedMotion() {
    return (
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  function isEditableTarget(target) {
    if (!target || !(target instanceof Element)) return false;
    if (target.isContentEditable) return true;
    const tag = target.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
  }

  function padNumber(value, width) {
    return String(value).padStart(width, "0");
  }

  function setHidden(element, hidden) {
    if (!element) return;
    element.toggleAttribute("hidden", Boolean(hidden));
  }

  const STAGE_LABELS = {
    senior: "高中",
    junior: "初中",
    primary: "小学",
    college: "大学",
    university: "大学",
    high: "高中",
    middle: "初中",
  };

  function formatStageLabel(stage) {
    if (!stage) return "";
    const key = String(stage).trim().toLowerCase();
    return STAGE_LABELS[key] || String(stage);
  }

  window.LF = {
    api: api,
    url: url,
    base: basePath,
    localPrefs: localPrefs,
    ApiError: ApiError,
    toast: toast,
    setLoading: setLoading,
    debounce: debounce,
    updateRangeFill: updateRangeFill,
    prefersReducedMotion: prefersReducedMotion,
    isEditableTarget: isEditableTarget,
    padNumber: padNumber,
    setHidden: setHidden,
    formatStageLabel: formatStageLabel,
    generateSeed: generateSeed,
    sanitizeSeed: sanitizeSeed,
    normalizeSelections: normalizeSelections,
    sanitizeSelections: sanitizeSelections,
    selectionQuery: selectionQuery,
    shuffleWithSeed: shuffleWithSeed,
    seededRandom: seededRandom,
    csrfToken: csrfToken,
  };
})();
