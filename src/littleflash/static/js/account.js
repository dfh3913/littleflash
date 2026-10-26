/* LittleFlash · login, learning dashboard, admin user management */
(function () {
  "use strict";

  const LF = window.LF;
  const $ = function (id) {
    return document.getElementById(id);
  };

  initLogin();
  initDashboard();
  initAdmin();

  /* =====================================================================
     Login
     ===================================================================== */
  function initLogin() {
    const form = $("loginForm");
    if (!form) return;

    const username = $("loginUsername");
    const password = $("loginPassword");
    const reveal = $("passwordReveal");
    const error = $("loginError");
    const submit = $("loginSubmit");
    const label = submit ? submit.querySelector(".primary-action__label") : null;
    const next = form.getAttribute("data-next") || "";

    let busy = false;
    let lockUntil = 0;
    let lockTimer = null;

    if (reveal && password) {
      reveal.addEventListener("click", function () {
        const show = password.type === "password";
        password.type = show ? "text" : "password";
        reveal.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
        reveal.title = show ? "隐藏密码" : "显示密码";
        LF.setHidden(reveal.querySelector(".reveal-on"), show);
        LF.setHidden(reveal.querySelector(".reveal-off"), !show);
        password.focus();
      });
    }

    if (username) {
      username.addEventListener("input", hideError);
    }
    if (password) {
      password.addEventListener("input", hideError);
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (busy) return;
      if (Date.now() < lockUntil) {
        showCountdown();
        return;
      }

      const name = username ? username.value.trim() : "";
      const secret = password ? password.value : "";
      if (!name) {
        showError("请输入用户名");
        if (username) username.focus();
        return;
      }
      if (!secret) {
        showError("请输入密码");
        if (password) password.focus();
        return;
      }

      busy = true;
      hideError();
      LF.setLoading(submit, true);
      if (label) label.textContent = "正在登录";

      try {
        const result = await LF.api("/api/auth/login", {
          method: "POST",
          body: { username: name, password: secret, next: next },
          timeout: 14000,
        });
        LF.setCsrfToken(result && result.csrfToken);
        if (label) label.textContent = "登录成功";
        window.location.href = (result && result.redirect) || LF.url("/");
      } catch (requestError) {
        busy = false;
        LF.setLoading(submit, false);
        if (label) label.textContent = "登录";
        handleLoginError(requestError);
        if (password) {
          password.focus();
          password.select();
        }
      }
    });

    function handleLoginError(requestError) {
      if (!requestError) {
        showError("登录失败，请稍后再试");
        return;
      }
      showError(requestError.message || "登录失败，请稍后再试");
      if (requestError.status === 429) {
        startLock(Math.max(1, Number(requestError.retryAfter) || 30));
      } else if (requestError.code === "csrf_failed") {
        showError("页面安全令牌已过期，即将刷新…");
        window.setTimeout(function () {
          window.location.reload();
        }, 1400);
      }
    }

    function startLock(seconds) {
      lockUntil = Date.now() + seconds * 1000;
      if (submit) submit.disabled = true;
      showCountdown();
      window.clearInterval(lockTimer);
      lockTimer = window.setInterval(function () {
        if (Date.now() >= lockUntil) {
          window.clearInterval(lockTimer);
          if (submit) submit.disabled = false;
          hideError();
        } else {
          showCountdown();
        }
      }, 1000);
    }

    function showCountdown() {
      const remaining = Math.max(0, Math.ceil((lockUntil - Date.now()) / 1000));
      if (remaining > 0) {
        showError("尝试次数过多，请在 " + remaining + " 秒后重试");
        if (submit) submit.disabled = true;
      }
    }

    function showError(message) {
      if (!error) return;
      error.textContent = message;
      error.hidden = false;
    }

    function hideError() {
      if (!error) return;
      error.textContent = "";
      error.hidden = true;
    }
  }

  /* =====================================================================
     Learning dashboard
     ===================================================================== */
  function initDashboard() {
    const root = document.querySelector('[data-page="dashboard"]');
    if (!root) return;

    const meta = $("identityMeta");
    const lastLogin = meta ? Number(meta.getAttribute("data-last-login")) : 0;
    if (meta) {
      meta.textContent = lastLogin
        ? "上次登录：" + LF.formatDateTime(lastLogin)
        : "欢迎回来，今天也学一点。";
    }

    const secondsNode = $("statSeconds");
    if (secondsNode) {
      const seconds = Number(secondsNode.getAttribute("data-seconds")) || 0;
      secondsNode.textContent = LF.formatDuration(seconds);
      secondsNode.title = seconds + " 秒";
    }

    const recent = $("statRecent");
    if (recent) {
      const stamp = Number(recent.getAttribute("data-last-study")) || 0;
      recent.textContent = stamp
        ? "最近学习：" + LF.formatRelative(stamp)
        : "最近学习：—";
      recent.title = stamp ? LF.formatDateTime(stamp) : "";
    }

    const bar = $("progressBar");
    if (bar) {
      const percent = Math.max(
        0,
        Math.min(100, Number(bar.getAttribute("value")) || 0)
      );
      bar.value = percent;
      bar.setAttribute("aria-label", "当前词书进度 " + Math.round(percent) + "%");
    }

    const logout = $("logoutBtn");
    if (logout) {
      logout.addEventListener("click", async function () {
        logout.disabled = true;
        try {
          await LF.api("/api/auth/logout", { method: "POST", timeout: 10000 });
        } catch (error) {
          /* 即使请求失败也回首页，本地会话会随 cookie 失效 */
        }
        window.location.href = LF.url("/");
      });
    }

    const form = $("passwordForm");
    if (!form) return;

    const current = $("currentPassword");
    const next = $("newPassword");
    const confirm = $("confirmPassword");
    const submit = $("passwordSubmit");
    const errorBox = $("passwordError");
    const successBox = $("passwordSuccess");
    let busy = false;

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (busy) return;
      hideMessages();

      const currentValue = current ? current.value : "";
      const nextValue = next ? next.value : "";
      const confirmValue = confirm ? confirm.value : "";
      if (!currentValue) {
        showError("请输入当前密码");
        if (current) current.focus();
        return;
      }
      if (nextValue.length < 6) {
        showError("新密码至少 6 位");
        if (next) next.focus();
        return;
      }
      if (nextValue !== confirmValue) {
        showError("两次输入的新密码不一致");
        if (confirm) confirm.focus();
        return;
      }
      if (nextValue === currentValue) {
        showError("新密码不能与当前密码相同");
        if (next) next.focus();
        return;
      }

      busy = true;
      LF.setLoading(submit, true);
      try {
        const result = await LF.api("/api/account/password", {
          method: "POST",
          body: { current_password: currentValue, new_password: nextValue },
          timeout: 14000,
        });
        LF.setCsrfToken(result && result.csrfToken);
        form.reset();
        if (successBox) {
          successBox.textContent = "密码已更新，下次请使用新密码登录。";
          successBox.hidden = false;
        }
        LF.toast("密码已更新", { type: "success" });
      } catch (requestError) {
        showError((requestError && requestError.message) || "密码更新失败，请稍后再试");
      } finally {
        busy = false;
        LF.setLoading(submit, false);
      }
    });

    function showError(message) {
      if (!errorBox) return;
      if (successBox) successBox.hidden = true;
      errorBox.textContent = message;
      errorBox.hidden = false;
    }

    function hideMessages() {
      if (errorBox) errorBox.hidden = true;
      if (successBox) successBox.hidden = true;
    }
  }

  /* =====================================================================
     Admin · user list, create, reset password, enable/disable
     ===================================================================== */
  function initAdmin() {
    const root = document.querySelector('[data-page="admin"]');
    if (!root) return;

    const canManageRoles = root.getAttribute("data-can-manage-roles") === "1";

    const dom = {
      total: $("summaryTotal"),
      active: $("summaryActive"),
      today: $("summaryToday"),
      studied: $("summaryStudied"),
      admins: $("summaryAdmins"),
      seconds: $("summarySeconds"),
      caption: $("usersCaption"),
      search: $("userSearch"),
      rows: $("userRows"),
      empty: $("userEmpty"),
      pageLabel: $("pageLabel"),
      prev: $("prevPage"),
      next: $("nextPage"),
      refresh: $("refreshUsers"),
      createForm: $("createUserForm"),
      createUsername: $("createUsername"),
      createPassword: $("createPassword"),
      createDisplayName: $("createDisplayName"),
      createRole: $("createRole"),
      createSubmit: $("createSubmit"),
      createError: $("createError"),
      createSuccess: $("createSuccess"),
      dialog: $("passwordDialog"),
      dialogLead: $("passwordDialogLead"),
      resetForm: $("resetPasswordForm"),
      resetInput: $("resetPasswordInput"),
      resetError: $("resetPasswordError"),
      resetSubmit: $("confirmResetPassword"),
      resetClose: $("cancelResetPassword"),
      generated: $("generatedPassword"),
      generatedValue: $("generatedPasswordValue"),
      copyGenerated: $("copyGeneratedPassword"),
    };

    const state = {
      users: [],
      query: "",
      page: 0,
      pageSize: 20,
      dialogUser: null,
      busy: false,
    };

    loadUsers();
    bindEvents();

    async function loadUsers() {
      if (dom.caption) dom.caption.textContent = "正在读取账号…";
      try {
        const data = await LF.api("/api/admin/users", { timeout: 24000 });
        state.users = Array.isArray(data.users) ? data.users : [];
        state.page = 0;
        renderSummary(data.summary || {});
        render();
      } catch (error) {
        if (dom.caption) {
          dom.caption.textContent =
            (error && error.message) || "读取账号失败，请稍后重试。";
        }
        LF.toast((error && error.message) || "读取账号失败", { type: "error" });
      }
    }

    function renderSummary(summary) {
      const total = Number(summary.total) || 0;
      const active = Number(summary.active) || 0;
      const studied = Number(summary.studied) || 0;
      const admins = Number(summary.admins) || 0;
      const today = Number(summary.activeToday) || 0;
      const seconds = Number(summary.totalSeconds) || 0;

      if (dom.total) dom.total.textContent = String(total);
      if (dom.active) dom.active.textContent = active + " 个正常使用";
      if (dom.today) dom.today.textContent = String(today);
      if (dom.studied) dom.studied.textContent = String(studied);
      if (dom.admins) dom.admins.textContent = admins + " 个管理员";
      if (dom.seconds) {
        dom.seconds.textContent = LF.formatDuration(seconds);
        dom.seconds.title = seconds + " 秒";
      }
    }

    /* 停用/启用后本地重算统计，避免再请求一次列表。 */
    function refreshLocalSummary() {
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      const dayStart = Math.floor(start.getTime() / 1000);
      const stats = {
        total: state.users.length,
        active: 0,
        admins: 0,
        studied: 0,
        activeToday: 0,
        totalSeconds: 0,
      };
      state.users.forEach(function (user) {
        if (user.isActive) stats.active += 1;
        if (user.role === "admin" || user.role === "superadmin") stats.admins += 1;
        const seconds = Number(user.totalSeconds) || 0;
        stats.totalSeconds += seconds;
        if (seconds > 0) stats.studied += 1;
        if (Number(user.lastStudyAt) >= dayStart) stats.activeToday += 1;
      });
      renderSummary(stats);
    }

    function filteredUsers() {
      const query = state.query.trim().toLowerCase();
      if (!query) return state.users;
      return state.users.filter(function (user) {
        const haystack = [
          user.username,
          user.displayName,
          user.roleLabel,
          user.wordbookTitle,
          user.wordbookId,
          user.wordEn,
        ]
          .join(" ")
          .toLowerCase();
        return haystack.indexOf(query) !== -1;
      });
    }

    function render() {
      const users = filteredUsers();
      const totalPages = Math.max(1, Math.ceil(users.length / state.pageSize));
      if (state.page >= totalPages) state.page = totalPages - 1;
      if (state.page < 0) state.page = 0;

      const start = state.page * state.pageSize;
      const slice = users.slice(start, start + state.pageSize);

      if (dom.rows) {
        dom.rows.textContent = "";
        slice.forEach(function (user) {
          dom.rows.appendChild(buildRow(user));
        });
      }
      if (dom.empty) {
        dom.empty.hidden = slice.length > 0;
        dom.empty.textContent = state.users.length
          ? "没有匹配「" + state.query + "」的账号。"
          : "还没有账号。";
      }
      if (dom.pageLabel) {
        dom.pageLabel.textContent =
          "第 " + (start + 1) + "–" + (start + slice.length) + " 个 / 共 " +
          users.length + " 个" + (state.query ? "（已筛选）" : "");
      }
      if (dom.prev) dom.prev.disabled = state.page <= 0;
      if (dom.next) dom.next.disabled = state.page >= totalPages - 1;

      if (dom.caption) {
        const studied = state.users.filter(function (user) {
          return Number(user.totalSeconds) > 0;
        }).length;
        dom.caption.textContent =
          "共 " + state.users.length + " 个账号，" + studied + " 个已有学习记录。";
      }
    }

    function buildRow(user) {
      const row = document.createElement("tr");

      const userCell = document.createElement("td");
      const userBox = document.createElement("div");
      userBox.className = "user-cell";

      const avatar = document.createElement("span");
      avatar.className = "user-cell__avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.textContent = String(user.username || "?").charAt(0).toUpperCase();

      const copy = document.createElement("span");
      copy.className = "user-cell__copy";

      const name = document.createElement("span");
      name.className = "user-cell__name";
      name.textContent = user.username || "—";

      const note = document.createElement("span");
      note.className = "user-cell__note";
      const notes = [];
      if (user.displayName) notes.push(user.displayName);
      notes.push(user.isActive ? "正常" : "已停用");
      note.textContent = notes.join(" · ");

      copy.appendChild(name);
      copy.appendChild(note);
      userBox.appendChild(avatar);
      userBox.appendChild(copy);
      userCell.appendChild(userBox);
      row.appendChild(userCell);

      const roleCell = document.createElement("td");
      const roleChip = document.createElement("span");
      roleChip.className = "role-chip role-chip--" + (user.role || "user");
      roleChip.textContent = user.roleLabel || user.role || "普通用户";
      roleCell.appendChild(roleChip);
      row.appendChild(roleCell);

      const bookCell = document.createElement("td");
      const bookPill = document.createElement("span");
      bookPill.className = "word-pill";
      const bookName = document.createElement("strong");
      bookName.textContent = user.wordbookTitle || user.wordbookId || "—";
      const bookUnit = document.createElement("span");
      bookUnit.textContent = user.wordbookId
        ? user.unitId === "all"
          ? "整本词书"
          : user.unitId
        : "未开始";
      bookPill.appendChild(bookName);
      bookPill.appendChild(bookUnit);
      bookCell.appendChild(bookPill);
      row.appendChild(bookCell);

      const progressCell = document.createElement("td");
      if (Number(user.totalSeconds) > 0 || user.wordEn) {
        const pill = document.createElement("span");
        pill.className = "word-pill";
        const word = document.createElement("strong");
        word.textContent = user.wordEn || "第 " + (Number(user.wordIndex) + 1) + " 个";
        const detail = document.createElement("span");
        detail.textContent =
          "第 " + (Number(user.wordIndex) + 1) + " 个 · 第 " +
          (Number(user.rounds) || 1) + " 轮";
        pill.appendChild(word);
        pill.appendChild(detail);
        progressCell.appendChild(pill);
      } else {
        progressCell.className = "is-muted";
        progressCell.textContent = "未开始";
      }
      row.appendChild(progressCell);

      const secondsCell = document.createElement("td");
      const seconds = Number(user.totalSeconds) || 0;
      secondsCell.textContent = seconds ? LF.formatDuration(seconds) : "—";
      if (seconds) secondsCell.title = seconds + " 秒";
      row.appendChild(secondsCell);

      const lastCell = document.createElement("td");
      lastCell.textContent = user.lastStudyAt
        ? LF.formatRelative(user.lastStudyAt)
        : "—";
      if (user.lastStudyAt) lastCell.title = LF.formatDateTime(user.lastStudyAt);
      row.appendChild(lastCell);

      const actionCell = document.createElement("td");
      const actions = document.createElement("div");
      actions.className = "table-actions";

      const resetButton = document.createElement("button");
      resetButton.type = "button";
      resetButton.className = "row-button";
      resetButton.textContent = "重置密码";
      resetButton.addEventListener("click", function () {
        openPasswordDialog(user);
      });
      actions.appendChild(resetButton);

      if (canManageRoles && user.role !== "superadmin") {
        const toggleButton = document.createElement("button");
        toggleButton.type = "button";
        toggleButton.className = "row-button" + (user.isActive ? " row-button--danger" : "");
        toggleButton.textContent = user.isActive ? "停用" : "启用";
        toggleButton.addEventListener("click", function () {
          toggleActive(user, toggleButton);
        });
        actions.appendChild(toggleButton);
      }

      actionCell.appendChild(actions);
      row.appendChild(actionCell);
      return row;
    }

    async function toggleActive(user, button) {
      if (button.disabled) return;
      button.disabled = true;
      try {
        const result = await LF.api(
          "/api/admin/users/" + encodeURIComponent(user.id) + "/active",
          {
            method: "POST",
            body: { active: !user.isActive },
            timeout: 12000,
          }
        );
        user.isActive = Boolean(result && result.user && result.user.isActive);
        LF.toast(
          user.username + (user.isActive ? " 已启用" : " 已停用"),
          { type: "success" }
        );
        refreshLocalSummary();
        render();
      } catch (error) {
        button.disabled = false;
        LF.toast((error && error.message) || "操作失败", { type: "error" });
      }
    }

    /* -------------------------------------------------- reset password */
    function openPasswordDialog(user) {
      state.dialogUser = user;
      if (dom.dialogLead) {
        dom.dialogLead.textContent =
          "为 " + user.username + " 设置新密码；留空会自动生成 8 位随机密码。";
      }
      if (dom.resetInput) dom.resetInput.value = "";
      if (dom.resetError) dom.resetError.hidden = true;
      if (dom.generated) dom.generated.hidden = true;
      if (dom.generatedValue) dom.generatedValue.textContent = "";
      if (dom.dialog && typeof dom.dialog.showModal === "function") {
        dom.dialog.showModal();
      }
      if (dom.resetInput) dom.resetInput.focus();
    }

    if (dom.resetForm) {
      dom.resetForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        if (state.busy || !state.dialogUser) return;
        const value = dom.resetInput ? dom.resetInput.value.trim() : "";
        if (value && value.length < 6) {
          showResetError("新密码至少 6 位");
          return;
        }

        state.busy = true;
        if (dom.resetError) dom.resetError.hidden = true;
        LF.setLoading(dom.resetSubmit, true);
        try {
          const result = await LF.api(
            "/api/admin/users/" +
              encodeURIComponent(state.dialogUser.id) +
              "/password",
            {
              method: "POST",
              body: { password: value || null },
              timeout: 14000,
            }
          );
          const generated = result && result.generatedPassword;
          if (generated) {
            if (dom.generated) dom.generated.hidden = false;
            if (dom.generatedValue) dom.generatedValue.textContent = generated;
            if (dom.generated) dom.generated.setAttribute("data-password", generated);
            if (dom.generatedValue) {
              dom.generatedValue.setAttribute("data-password", generated);
            }
            LF.toast("新密码已生成，请复制后交给用户", { type: "success" });
          } else {
            closePasswordDialog();
            LF.toast("密码已更新", { type: "success" });
          }
        } catch (error) {
          showResetError((error && error.message) || "重置密码失败");
        } finally {
          state.busy = false;
          LF.setLoading(dom.resetSubmit, false);
        }
      });
    }

    if (dom.resetClose) {
      dom.resetClose.addEventListener("click", closePasswordDialog);
    }

    function closePasswordDialog() {
      if (dom.dialog && dom.dialog.open) dom.dialog.close();
      state.dialogUser = null;
    }

    function showResetError(message) {
      if (!dom.resetError) return;
      dom.resetError.textContent = message;
      dom.resetError.hidden = false;
    }

    if (dom.copyGenerated) {
      dom.copyGenerated.addEventListener("click", async function () {
        const password = dom.generatedValue
          ? dom.generatedValue.getAttribute("data-password") ||
            dom.generatedValue.textContent
          : "";
        if (!password) return;
        try {
          await navigator.clipboard.writeText(password);
          LF.toast("已复制新密码", { type: "success" });
        } catch (error) {
          LF.toast("浏览器不允许自动复制，请手动选择密码", { type: "error" });
        }
      });
    }

    /* ----------------------------------------------------- create user */
    if (dom.createForm) {
      dom.createForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        if (state.busy) return;
        hideCreateMessages();

        const username = dom.createUsername ? dom.createUsername.value.trim() : "";
        const password = dom.createPassword ? dom.createPassword.value : "";
        if (!username) {
          showCreateError("请输入用户名");
          if (dom.createUsername) dom.createUsername.focus();
          return;
        }
        if (password.length < 6) {
          showCreateError("初始密码至少 6 位");
          if (dom.createPassword) dom.createPassword.focus();
          return;
        }

        state.busy = true;
        LF.setLoading(dom.createSubmit, true);
        try {
          const result = await LF.api("/api/admin/users", {
            method: "POST",
            body: {
              username: username,
              password: password,
              display_name: dom.createDisplayName ? dom.createDisplayName.value.trim() : "",
              role: dom.createRole ? dom.createRole.value : "user",
            },
            timeout: 14000,
          });
          dom.createForm.reset();
          if (dom.createSuccess) {
            dom.createSuccess.textContent =
              "已创建账号 " +
              ((result && result.user && result.user.username) || username) +
              "，可以把它交给使用者了。";
            dom.createSuccess.hidden = false;
          }
          LF.toast("账号已创建", { type: "success" });
          await loadUsers();
        } catch (error) {
          showCreateError((error && error.message) || "创建账号失败");
        } finally {
          state.busy = false;
          LF.setLoading(dom.createSubmit, false);
        }
      });
    }

    function showCreateError(message) {
      if (!dom.createError) return;
      if (dom.createSuccess) dom.createSuccess.hidden = true;
      dom.createError.textContent = message;
      dom.createError.hidden = false;
    }

    function hideCreateMessages() {
      if (dom.createError) dom.createError.hidden = true;
      if (dom.createSuccess) dom.createSuccess.hidden = true;
    }

    /* ------------------------------------------------------------ misc */
    function bindEvents() {
      if (dom.refresh) {
        dom.refresh.addEventListener("click", function () {
          loadUsers();
        });
      }
      if (dom.search) {
        const onSearch = LF.debounce(function () {
          state.query = dom.search.value;
          state.page = 0;
          render();
        }, 160);
        dom.search.addEventListener("input", onSearch);
        dom.search.addEventListener("search", onSearch);
      }
      if (dom.prev) {
        dom.prev.addEventListener("click", function () {
          if (state.page > 0) {
            state.page -= 1;
            render();
          }
        });
      }
      if (dom.next) {
        dom.next.addEventListener("click", function () {
          const totalPages = Math.max(
            1,
            Math.ceil(filteredUsers().length / state.pageSize)
          );
          if (state.page < totalPages - 1) {
            state.page += 1;
            render();
          }
        });
      }
    }
  }
})();
