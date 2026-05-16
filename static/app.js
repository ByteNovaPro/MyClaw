const accessTokenKey = "myclaw_access_token";
const sessionIdKey = "myclaw_session_id";
const conversationIdKey = "myclaw_conversation_id";

let accessToken = "";
let serverSessionId = "";
let conversationId = "";

const auth = document.querySelector("#auth");
const authForm = document.querySelector("#auth-form");
const tokenInputs = Array.from(document.querySelectorAll(".token-item"));
const app = document.querySelector("#app");
const chat = document.querySelector("#chat");
const form = document.querySelector("#form");
const input = document.querySelector("#message");
const send = document.querySelector("#send");
const reset = document.querySelector("#reset");

function saveSession(token, sessionId, nextConversationId) {
  accessToken = token;
  serverSessionId = sessionId;
  conversationId = nextConversationId;
  sessionStorage.setItem(accessTokenKey, token);
  sessionStorage.setItem(sessionIdKey, sessionId);
  sessionStorage.setItem(conversationIdKey, nextConversationId);
}

function loadSession() {
  accessToken = sessionStorage.getItem(accessTokenKey) || "";
  serverSessionId = sessionStorage.getItem(sessionIdKey) || "";
  conversationId = sessionStorage.getItem(conversationIdKey) || "";
}

function clearSession() {
  accessToken = "";
  serverSessionId = "";
  conversationId = "";
  sessionStorage.removeItem(accessTokenKey);
  sessionStorage.removeItem(sessionIdKey);
  sessionStorage.removeItem(conversationIdKey);
}

function goToChat() {
  window.location.replace("/chat.html");
}

function goToAuth() {
  clearSession();
  window.location.replace("/");
}

function showAuth(message) {
  if (!authForm) {
    goToAuth();
    return;
  }

  clearTokenInputs();
  tokenInputs[0].focus();
  const existing = authForm.querySelector(".auth-error");
  if (!message) {
    if (existing) {
      existing.remove();
    }
    return;
  }
  if (existing) {
    existing.textContent = message;
    return;
  }
  const error = document.createElement("p");
  error.className = "auth-error";
  error.textContent = message;
  authForm.appendChild(error);
}

function setAuthLoading(isLoading) {
  const button = authForm.querySelector("button");
  button.disabled = isLoading;
  tokenInputs.forEach((item) => {
    item.disabled = isLoading;
  });
  button.textContent = isLoading ? "验证中..." : "进入智能体";
}

function getTokenFromInputs() {
  return tokenInputs.map((item) => item.value).join("");
}

function clearTokenInputs() {
  tokenInputs.forEach((item) => {
    item.value = "";
  });
}

function fillTokenInputs(value) {
  const digits = value.replace(/\D/g, "").slice(0, tokenInputs.length);
  tokenInputs.forEach((item, index) => {
    item.value = digits[index] || "";
  });
  const nextIndex = Math.min(digits.length, tokenInputs.length - 1);
  tokenInputs[nextIndex].focus();
}

function authHeaders() {
  return {
    "Content-Type": "application/json",
    "X-Access-Token": accessToken,
    "X-Conversation-Id": conversationId,
  };
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (error) {
    return {};
  }
}

async function parseResponse(response) {
  const data = await readJson(response);
  if (response.status === 401) {
    throw new Error(data.reply || "访问口令错误");
  }
  if (!response.ok) {
    throw new Error(data.reply || "请求失败");
  }
  return data;
}

async function refreshConversation() {
  if (!accessToken) {
    return false;
  }
  try {
    const response = await fetch("/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: accessToken }),
    });
    const data = await readJson(response);
    if (!response.ok || !data.ok || !data.conversationId) {
      return false;
    }
    saveSession(accessToken, data.sessionId || serverSessionId, data.conversationId);
    return true;
  } catch (error) {
    return false;
  }
}

async function fetchWithSession(path, options) {
  const response = await fetch(path, options);
  if (response.status !== 401) {
    return response;
  }

  const data = await readJson(response);
  if (!String(data.reply || "").includes("会话已失效")) {
    goToAuth();
    return null;
  }

  const refreshed = await refreshConversation();
  if (!refreshed) {
    goToAuth();
    return null;
  }
  return fetch(path, { ...options, headers: authHeaders() });
}

function scrollChat() {
  chat.scrollTop = chat.scrollHeight;
}

function addMessage(text, className) {
  const node = document.createElement("div");
  node.className = `msg ${className}`;
  node.textContent = text;
  chat.appendChild(node);
  scrollChat();
}

function addNotice(text) {
  addMessage(text, "assistant notice");
}

function createMetaItem(label, value) {
  const item = document.createElement("span");
  item.className = "result-meta-item";
  item.textContent = `${label}: ${value}`;
  return item;
}

function addOutputBlock(parent, title, value, className) {
  if (!value) {
    return;
  }
  const label = document.createElement("div");
  label.className = "result-label";
  label.textContent = title;

  const output = document.createElement("pre");
  output.className = `command-output ${className}`;
  output.textContent = value;

  parent.append(label, output);
}

function addCommandResult(data) {
  const result = data.commandResult || {};
  const node = document.createElement("div");
  node.className = "msg assistant command-result";

  const title = document.createElement("div");
  title.className = "result-title";
  title.textContent = "命令执行结果";

  const command = document.createElement("pre");
  command.className = "command-preview";
  command.textContent = result.command || "";

  const meta = document.createElement("div");
  meta.className = "result-meta";
  meta.append(
    createMetaItem("退出码", result.returncode === null || result.returncode === undefined ? "无" : result.returncode),
    createMetaItem("超时", result.timeout ? `${result.timeoutSeconds} 秒` : "否"),
    createMetaItem("目录", result.cwd || "")
  );
  if (result.truncated) {
    meta.append(createMetaItem("输出", "已截断"));
  }

  node.append(title, command, meta);
  addOutputBlock(node, "stdout", result.stdout || "", "stdout");
  addOutputBlock(node, "stderr", result.stderr || "", "stderr");

  const reply = document.createElement("p");
  reply.className = "result-reply";
  reply.textContent = data.reply || "请继续输入需求";
  node.appendChild(reply);

  chat.appendChild(node);
  scrollChat();
}

function riskLabel(level) {
  if (level === "high") {
    return "高风险";
  }
  if (level === "medium") {
    return "中风险";
  }
  return "低风险";
}

function addCommandConfirmation(data) {
  const node = document.createElement("div");
  const riskLevel = data.riskLevel || "low";
  node.className = `msg assistant confirmation risk-${riskLevel}`;

  const header = document.createElement("div");
  header.className = "confirmation-header";

  const badge = document.createElement("span");
  badge.className = `risk-badge risk-${riskLevel}`;
  badge.textContent = riskLabel(riskLevel);

  const description = document.createElement("p");
  description.className = "confirmation-text";
  description.textContent = data.description || "执行该命令后会在服务所在电脑上完成相应终端操作。";

  header.append(badge, description);

  const riskNote = document.createElement("p");
  riskNote.className = "risk-note";
  riskNote.textContent = data.riskNote || "请确认该命令符合你的预期。";

  const command = document.createElement("pre");
  command.className = "command-preview";
  command.textContent = data.command;

  const actions = document.createElement("div");
  actions.className = "confirmation-actions";

  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = "confirm-button";
  confirm.textContent = "确认执行";

  if (riskLevel === "high") {
    const guard = document.createElement("label");
    guard.className = "risk-check";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    const text = document.createElement("span");
    text.textContent = "我已确认高风险命令的影响";
    guard.append(checkbox, text);
    confirm.disabled = true;
    checkbox.addEventListener("change", () => {
      confirm.disabled = !checkbox.checked;
    });
    node.append(header, riskNote, command, guard);
  } else {
    node.append(header, riskNote, command);
  }

  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "cancel-button";
  cancel.textContent = "取消";

  actions.append(cancel, confirm);
  node.appendChild(actions);
  chat.appendChild(node);
  scrollChat();

  confirm.addEventListener("click", () => submitCommandDecision("/confirm", data.commandId, node));
  cancel.addEventListener("click", () => submitCommandDecision("/cancel", data.commandId, node));
}

async function submitCommandDecision(path, commandId, node) {
  const buttons = node.querySelectorAll("button");
  buttons.forEach((button) => {
    button.disabled = true;
  });

  try {
    const response = await fetchWithSession(path, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ commandId }),
    });
    if (!response) {
      return;
    }
    const data = await parseResponse(response);
    if (!data) {
      return;
    }
    if (data.type === "command_result") {
      addCommandResult(data);
    } else {
      addMessage(data.reply || "请继续输入需求", "assistant");
    }
  } catch (error) {
    addNotice(error.message || "请继续输入需求");
  } finally {
    node.remove();
    input.focus();
  }
}

function bindChatPage() {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) {
      return;
    }

    addMessage(message, "user");
    input.value = "";
    input.disabled = true;
    send.disabled = true;
    send.textContent = "发送中...";

    try {
      const response = await fetchWithSession("/chat", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ message }),
      });
      if (!response) {
        return;
      }
      const data = await parseResponse(response);
      if (!data) {
        return;
      }
      if (data.type === "command_confirmation") {
        addCommandConfirmation(data);
      } else {
        addMessage(data.reply || "请继续输入需求", "assistant");
      }
    } catch (error) {
      addNotice(error.message || "请继续输入需求");
    } finally {
      send.disabled = false;
      input.disabled = false;
      send.textContent = "发送";
      input.focus();
    }
  });

  reset.addEventListener("click", async () => {
    reset.disabled = true;
    send.disabled = true;

    try {
      const response = await fetchWithSession("/reset", {
        method: "POST",
        headers: authHeaders(),
        body: "{}",
      });
      if (!response) {
        return;
      }
      const data = await parseResponse(response);
      if (!data) {
        return;
      }
      chat.replaceChildren();
      addMessage(data.reply || "上下文已清空", "assistant");
    } catch (error) {
      addNotice(error.message || "请继续输入需求");
    } finally {
      reset.disabled = false;
      send.disabled = false;
      input.focus();
    }
  });
}

function bindAuthPage() {
  authForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const token = getTokenFromInputs();
    if (!/^\d{4}$/.test(token)) {
      showAuth("请输入 4 位数字口令");
      return;
    }
    verifyToken(token);
  });

  tokenInputs.forEach((item, index) => {
    item.addEventListener("input", () => {
      const digits = item.value.replace(/\D/g, "");
      item.value = digits.slice(-1);
      if (item.value && index < tokenInputs.length - 1) {
        tokenInputs[index + 1].focus();
      }
    });

    item.addEventListener("keydown", (event) => {
      if (event.key === "Backspace" && !item.value && index > 0) {
        tokenInputs[index - 1].focus();
      }
    });

    item.addEventListener("paste", (event) => {
      event.preventDefault();
      const text = event.clipboardData.getData("text");
      fillTokenInputs(text);
    });
  });
}

async function verifyToken(token) {
  setAuthLoading(true);
  try {
    const response = await fetch("/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const data = await readJson(response);
    if (!response.ok || !data.ok || !data.conversationId) {
      clearSession();
      showAuth(data.reply || "访问口令错误");
      return;
    }
    saveSession(token, data.sessionId || serverSessionId, data.conversationId);
    goToChat();
  } catch (error) {
    showAuth("验证失败，请稍后重试");
  } finally {
    setAuthLoading(false);
  }
}

async function fetchSessionId() {
  const response = await fetch("/session");
  const data = await readJson(response);
  return data.sessionId || "";
}

async function initializeAuth() {
  bindAuthPage();
  try {
    serverSessionId = await fetchSessionId();
  } catch (error) {
    serverSessionId = "";
  }

  loadSession();
  if (serverSessionId && serverSessionId === sessionStorage.getItem(sessionIdKey) && accessToken) {
    verifyToken(accessToken);
  } else {
    clearSession();
    showAuth();
  }
}

async function initializeChat() {
  bindChatPage();
  loadSession();
  try {
    const currentSessionId = await fetchSessionId();
    if (!currentSessionId || currentSessionId !== serverSessionId || !accessToken || !conversationId) {
      goToAuth();
      return;
    }
  } catch (error) {
    goToAuth();
    return;
  }
  input.focus();
}

if (authForm) {
  initializeAuth();
} else if (app) {
  initializeChat();
}
