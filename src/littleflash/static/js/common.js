/* LittleFlash · shared browser helpers */
(function () {
  "use strict";

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  let csrfToken = csrfMeta ? csrfMeta.getAttribute("content") || "" : "";

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
    if (value.front === "en" || value.front === "zh") {
      clean.front = value.front;
    }
    if (value.order === "sequence" || value.order === "shuffle") {
      clean.order = value.order;
    }
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

  function formatDuration(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    if (total < 60) return total > 0 ? "不足 1 分" : "0 分";
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (hours > 0) {
      return minutes > 0 ? hours + " 小时 " + minutes + " 分" : hours + " 小时";
    }
    return minutes + " 分";
  }

  function pad2(value) {
    return String(value).padStart(2, "0");
  }

  function formatDateTime(timestamp) {
    const value = Number(timestamp);
    if (!isFinite(value) || value <= 0) return "—";
    const date = new Date(value * 1000);
    if (isNaN(date.getTime())) return "—";
    return (
      date.getFullYear() +
      "-" +
      pad2(date.getMonth() + 1) +
      "-" +
      pad2(date.getDate()) +
      " " +
      pad2(date.getHours()) +
      ":" +
      pad2(date.getMinutes())
    );
  }

  function formatRelative(timestamp) {
    const value = Number(timestamp);
    if (!isFinite(value) || value <= 0) return "—";
    const diff = Math.floor(Date.now() / 1000) - value;
    if (diff < 60) return "刚刚";
    if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + " 天前";
    return formatDateTime(value);
  }

  function setCsrfToken(value) {
    if (typeof value === "string" && value) csrfToken = value;
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
    formatDuration: formatDuration,
    formatDateTime: formatDateTime,
    formatRelative: formatRelative,
    setCsrfToken: setCsrfToken,
  };

  Object.defineProperty(window.LF, "csrfToken", {
    get: function () {
      return csrfToken;
    },
  });
})();
