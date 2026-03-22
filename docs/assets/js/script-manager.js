document.addEventListener("DOMContentLoaded", () => {
  const BOT_API_BASE = (window.SMOKEBOT_API_BASE || "https://botapi.sm0ke.org").replace(/\/$/, "");
  const DISCORD_CLIENT_ID = "1375925201191178300";
  const TOKEN_STORAGE_KEY = "smokebot-discord-access-token-v2";
  const SELECTED_GUILD_STORAGE_KEY = "smokebot-selected-guild-v1";

  const configForm = document.getElementById("api-config-form");
  const scriptForm = document.getElementById("script-form");
  const list = document.getElementById("script-list");
  const refreshBtn = document.getElementById("refresh-list");
  const resetFormBtn = document.getElementById("reset-form");
  const loginBtn = document.getElementById("discord-login");
  const logoutBtn = document.getElementById("discord-logout");
  const guildSelect = document.getElementById("guild_id");
  const userField = document.getElementById("discord_user");
  const status = document.getElementById("api-status");
  const templateSelect = document.getElementById("quick_template");
  const searchInput = document.getElementById("script_search");

  const fields = {
    name: document.getElementById("name"),
    event: document.getElementById("event"),
    pattern: document.getElementById("pattern"),
    matchType: document.getElementById("match_type"),
    channelIds: document.getElementById("channel_ids"),
    enabled: document.getElementById("enabled"),
    code: document.getElementById("code"),
  };

  if (
    !configForm ||
    !scriptForm ||
    !list ||
    !refreshBtn ||
    !resetFormBtn ||
    !loginBtn ||
    !logoutBtn ||
    !guildSelect ||
    !userField ||
    !status ||
    !templateSelect ||
    !searchInput
  ) {
    return;
  }

  let editingName = null;
  let cachedScripts = {};

  const templateLibrary = {
    send_message: {
      event: "message",
      match_type: "contains",
      pattern: "hello bot",
      code: "send('Hey there!')",
    },
    auto_react: {
      event: "message",
      match_type: "contains",
      pattern: "nice",
      code: "react('🔥')",
    },
    mod_ping: {
      event: "message",
      match_type: "regex",
      pattern: "help|mod",
      code: "send('<@&ROLE_ID_HERE> new request in ' + channel.mention)",
    },
  };

  const oauthRedirectUri = `${window.location.origin}${window.location.pathname}`;

  const setStatus = (message, isError = false) => {
    status.textContent = message;
    status.classList.toggle("text-red-400", isError);
    status.classList.toggle("text-green-400", !isError);
  };

  const getOAuthResultFromHash = () => {
    const hash = window.location.hash.replace(/^#/, "");
    if (!hash) {
      return { accessToken: "", scope: "" };
    }

    const params = new URLSearchParams(hash);
    return {
      accessToken: params.get("access_token") || "",
      scope: params.get("scope") || "",
    };
  };

  const getStoredToken = () => localStorage.getItem(TOKEN_STORAGE_KEY) || "";

  const setToken = (token) => {
    if (token) {
      localStorage.setItem(TOKEN_STORAGE_KEY, token);
      return;
    }

    localStorage.removeItem(TOKEN_STORAGE_KEY);
  };

  const getSelectedGuild = () => localStorage.getItem(SELECTED_GUILD_STORAGE_KEY) || "";

  const setSelectedGuild = (guildId) => {
    if (guildId) {
      localStorage.setItem(SELECTED_GUILD_STORAGE_KEY, guildId);
      return;
    }

    localStorage.removeItem(SELECTED_GUILD_STORAGE_KEY);
  };

  const loginWithDiscord = () => {
    const authUrl = new URL("https://discord.com/oauth2/authorize");
    authUrl.searchParams.set("client_id", DISCORD_CLIENT_ID);
    authUrl.searchParams.set("response_type", "token");
    authUrl.searchParams.set("redirect_uri", oauthRedirectUri);
    authUrl.searchParams.set("scope", "identify guilds");
    authUrl.searchParams.set("prompt", "consent");
    window.location.href = authUrl.toString();
  };

  const apiRequest = async (path, options = {}) => {
    const token = getStoredToken();
    if (!token) {
      throw new Error("You must sign in with Discord first.");
    }

    const response = await fetch(`${BOT_API_BASE}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...(options.headers || {}),
      },
    });

    const json = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (
        response.status === 401 &&
        (json.error === "Discord authentication failed" || json.error === "Unauthorized")
      ) {
        setToken("");
        throw new Error("Discord sign-in expired or is missing required access. Sign in again.");
      }
      throw new Error(json.error || `Request failed (${response.status})`);
    }
    return json;
  };

  const parseChannelIds = (value) =>
    [...new Set(
      value
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean)
        .map((part) => (part.match(/\d+/) || [null])[0])
        .filter(Boolean)
        .map((id) => Number(id))
        .filter((id) => Number.isInteger(id)),
    )];

  const channelIdsToText = (ids = []) => ids.map((id) => String(id)).join(", ");

  const setFormEnabled = (enabled) => {
    scriptForm.querySelectorAll("input,select,textarea,button").forEach((element) => {
      element.disabled = !enabled;
    });
    searchInput.disabled = !enabled;
  };

  const resetForm = () => {
    scriptForm.reset();
    fields.enabled.checked = true;
    fields.event.value = "message";
    fields.matchType.value = "contains";
    templateSelect.value = "";
    editingName = null;
  };

  const loadIntoForm = (name, entry) => {
    fields.name.value = name;
    fields.event.value = entry.event || "message";
    fields.pattern.value = entry.pattern || "";
    fields.matchType.value = entry.match_type || "contains";
    fields.channelIds.value = channelIdsToText(entry.channel_ids || []);
    fields.enabled.checked = entry.enabled !== false;
    fields.code.value = entry.code || "";
    templateSelect.value = "";
    editingName = name;
    scriptForm.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const renderGuildOptions = (guilds) => {
    const preferredGuild = getSelectedGuild();
    guildSelect.innerHTML = '<option value="">Select a server</option>';

    guilds.forEach((guild) => {
      const option = document.createElement("option");
      option.value = guild.id;
      option.textContent = guild.name;
      guildSelect.appendChild(option);
    });

    if (preferredGuild && guilds.some((guild) => guild.id === preferredGuild)) {
      guildSelect.value = preferredGuild;
      return;
    }

    if (guilds.length > 0) {
      guildSelect.value = guilds[0].id;
      setSelectedGuild(guilds[0].id);
      return;
    }

    setSelectedGuild("");
  };

  const renderList = () => {
    const query = searchInput.value.trim().toLowerCase();
    const names = Object.keys(cachedScripts)
      .filter((name) => {
        if (!query) {
          return true;
        }

        const entry = cachedScripts[name] || {};
        const haystack = `${name} ${entry.event || ""} ${entry.pattern || ""}`.toLowerCase();
        return haystack.includes(query);
      })
      .sort((a, b) => a.localeCompare(b));

    if (!names.length) {
      list.innerHTML =
        '<div class="band"><p class="text-subtle">No script triggers found for this server.</p></div>';
      return;
    }

    list.innerHTML = "";
    names.forEach((name) => {
      const entry = cachedScripts[name];
      const wrapper = document.createElement("article");
      wrapper.className = "band";
      wrapper.innerHTML = `
        <div class="flex flex-col md:flex-row md:items-start md:justify-between gap-3">
          <div>
            <h3 class="text-lg text-white font-semibold">${name}</h3>
            <p class="text-subtle">event: ${entry.event || "message"} • match: ${entry.match_type || "contains"} • enabled: ${entry.enabled !== false ? "yes" : "no"}</p>
            <p class="text-subtle">pattern: <code>${entry.pattern || "(none)"}</code></p>
          </div>
          <div class="flex gap-2">
            <button class="btn-retro secondary hover-crt text-sm" data-action="edit" data-name="${name}" type="button">Edit</button>
            <button class="btn-retro secondary hover-crt text-sm" data-action="delete" data-name="${name}" type="button">Delete</button>
          </div>
        </div>
      `;
      list.appendChild(wrapper);
    });
  };

  const ensureGuildSelected = () => {
    const guildId = guildSelect.value;
    if (!guildId) {
      throw new Error("Choose a server first.");
    }
    return guildId;
  };

  const fetchScripts = async () => {
    const guildId = ensureGuildSelected();
    const response = await apiRequest(`/api/script-triggers/${guildId}`);
    cachedScripts = response.triggers || {};
    renderList();
    setStatus("Loaded scripts from hosted bot API.");
  };

  const fetchIdentity = async () => {
    const token = getStoredToken();
    if (!token) {
      userField.value = "";
      return;
    }

    const response = await fetch("https://discord.com/api/v10/users/@me", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      setToken("");
      throw new Error("Discord session expired. Please sign in again.");
    }

    const user = await response.json();
    const discriminator =
      user.discriminator && user.discriminator !== "0" ? `#${user.discriminator}` : "";
    userField.value = `${user.username}${discriminator}`;
  };

  const fetchManageableGuilds = async () => {
    const response = await apiRequest("/api/script-triggers/guilds");
    renderGuildOptions(response.guilds || []);
  };

  configForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    try {
      setSelectedGuild(guildSelect.value);
      await fetchScripts();
      setFormEnabled(true);
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  refreshBtn.addEventListener("click", async () => {
    try {
      await fetchManageableGuilds();
      if (guildSelect.value) {
        await fetchScripts();
        setFormEnabled(true);
        return;
      }

      cachedScripts = {};
      renderList();
      setFormEnabled(false);
      setStatus("Choose a server to load scripts.");
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  loginBtn.addEventListener("click", () => {
    loginWithDiscord();
  });

  logoutBtn.addEventListener("click", () => {
    setToken("");
    setSelectedGuild("");
    guildSelect.innerHTML = '<option value="">Select a server</option>';
    userField.value = "";
    cachedScripts = {};
    resetForm();
    renderList();
    setFormEnabled(false);
    setStatus("Signed out.");
  });

  guildSelect.addEventListener("change", () => {
    setSelectedGuild(guildSelect.value);
  });

  scriptForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const name = fields.name.value.trim();
    if (!name) {
      setStatus("Script name is required.", true);
      return;
    }

    const payload = {
      event: fields.event.value,
      pattern: fields.pattern.value.trim(),
      match_type: fields.matchType.value,
      channel_ids: parseChannelIds(fields.channelIds.value),
      enabled: Boolean(fields.enabled.checked),
      code: fields.code.value,
    };

    try {
      const guildId = ensureGuildSelected();
      await apiRequest(`/api/script-triggers/${guildId}/${encodeURIComponent(name)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });

      if (editingName && editingName !== name) {
        await apiRequest(`/api/script-triggers/${guildId}/${encodeURIComponent(editingName)}`, {
          method: "DELETE",
        });
      }

      editingName = name;
      await fetchScripts();
      setStatus(`Saved script trigger "${name}".`);
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  list.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }

    const action = button.dataset.action;
    const name = button.dataset.name;
    if (!name) {
      return;
    }

    if (action === "edit") {
      const entry = cachedScripts[name];
      if (entry) {
        loadIntoForm(name, entry);
      }
      return;
    }

    if (action === "delete") {
      try {
        const confirmed = window.confirm(`Delete "${name}"? This cannot be undone.`);
        if (!confirmed) {
          return;
        }

        const guildId = ensureGuildSelected();
        await apiRequest(`/api/script-triggers/${guildId}/${encodeURIComponent(name)}`, {
          method: "DELETE",
        });
        if (editingName === name) {
          resetForm();
        }
        await fetchScripts();
        setStatus(`Deleted script trigger "${name}".`);
      } catch (error) {
        setStatus(error.message, true);
      }
    }
  });

  resetFormBtn.addEventListener("click", () => {
    resetForm();
  });

  templateSelect.addEventListener("change", () => {
    const selected = templateLibrary[templateSelect.value];
    if (!selected) {
      return;
    }

    fields.event.value = selected.event;
    fields.matchType.value = selected.match_type;
    fields.pattern.value = selected.pattern;
    fields.code.value = selected.code;
  });

  searchInput.addEventListener("input", () => {
    renderList();
  });

  resetForm();
  renderList();

  (async () => {
    setFormEnabled(false);

    const oauthResult = getOAuthResultFromHash();
    if (oauthResult.accessToken) {
      const grantedScopes = new Set(
        oauthResult.scope
          .split(" ")
          .map((scope) => scope.trim())
          .filter(Boolean),
      );
      if (!grantedScopes.has("guilds")) {
        setToken("");
        setStatus('Discord did not grant the required "guilds" scope. Sign in again.', true);
        history.replaceState({}, document.title, oauthRedirectUri);
        return;
      }

      setToken(oauthResult.accessToken);
      history.replaceState({}, document.title, oauthRedirectUri);
    }

    if (!getStoredToken()) {
      setStatus("Sign in with Discord to begin.");
      return;
    }

    try {
      await fetchIdentity();
      await fetchManageableGuilds();
      if (guildSelect.value) {
        await fetchScripts();
        setFormEnabled(true);
      } else {
        setStatus("Choose a server to load scripts.");
      }
    } catch (error) {
      setStatus(error.message, true);
    }
  })();
});
