const accessTokenKey = "myclaw_access_token";
const sessionIdKey = "myclaw_session_id";
let accessToken = "";
let serverSessionId = "";

function saveAccessToken(token) {
  accessToken = token;
  sessionStorage.setItem(accessTokenKey, token);
  sessionStorage.setItem(sessionIdKey, serverSessionId);
}

function clearAccessToken() {
  accessToken = "";
  sessionStorage.removeItem(accessTokenKey);
  sessionStorage.removeItem(sessionIdKey);
}

const auth = document.querySelector("#auth");
const authForm = document.querySelector("#auth-form");
const tokenInputs = Array.from(document.querySelectorAll(".token-item"));
const app = document.querySelector("#app");
const chat = document.querySelector("#chat");
const form = document.querySelector("#form");
const input = document.querySelector("#message");
const send = document.querySelector("#send");
const reset = document.querySelector("#reset");

function showApp() {
  window.location.replace("/chat.html");
}

function showAuth(message) {
  if (!auth) {
    window.location.replace("/");
    return;
  }
  auth.classList.remove("is-hidden");
  window.scrollTo(0, 0);
  clearTokenInputs();
  tokenInputs[0].focus();
  const existing = authForm.querySelector(".auth-error");
  if (!message) {
    if (existing) {
      existing.remove();
    }
    return;
  }
  if (message) {
    if (existing) {
      existing.textContent = message;
      return;
    }
    const error = document.createElement("p");
    error.className = "auth-error";
    error.textContent = message;
    authForm.appendChild(error);
  }
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
  };
}

function redirectToAuth() {
  clearAccessToken();
  window.location.replace("/");
}

async function parseResponse(response) {
  const data = await response.json();
  if (response.status === 401) {
    redirectToAuth();
    return null;
  }
  return data;
}

function addMessage(text, className) {
  const node = document.createElement("div");
  node.className = `msg ${className}`;
  node.textContent = text;
  chat.appendChild(node);
  chat.scrollTop = chat.scrollHeight;
}

function addCommandConfirmation(data) {
  const node = document.createElement("div");
  node.className = "msg assistant confirmation";

  const description = document.createElement("p");
  description.className = "confirmation-text";
  description.textContent = data.description || "执行该命令后会在服务所在电脑上完成相应终端操作。";

  const command = document.createElement("pre");
  command.className = "command-preview";
  command.textContent = data.command;

  const actions = document.createElement("div");
  actions.className = "confirmation-actions";

  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = "confirm-button";
  confirm.textContent = "确认执行";

  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "cancel-button";
  cancel.textContent = "取消";

  actions.append(confirm, cancel);
  node.append(description, command, actions);
  chat.appendChild(node);
  chat.scrollTop = chat.scrollHeight;

  confirm.addEventListener("click", () => submitCommandDecision("/confirm", data.commandId, node));
  cancel.addEventListener("click", () => submitCommandDecision("/cancel", data.commandId, node));
}

async function submitCommandDecision(path, commandId, node) {
  const buttons = node.querySelectorAll("button");
  buttons.forEach((button) => {
    button.disabled = true;
  });

  try {
    const response = await fetch(path, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ commandId }),
    });
    const data = await parseResponse(response);
    if (data) {
      addMessage(data.reply || "请继续输入需求", "assistant");
    }
  } catch (error) {
    addMessage("请继续输入需求", "assistant");
  } finally {
    node.remove();
    input.focus();
  }
}

if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) {
      return;
    }

    addMessage(message, "user");
    input.value = "";
    send.disabled = true;

    try {
      const response = await fetch("/chat", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ message }),
      });
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
      addMessage("请继续输入需求", "assistant");
    } finally {
      send.disabled = false;
      input.focus();
    }
  });
}

if (reset) {
  reset.addEventListener("click", async () => {
    reset.disabled = true;
    send.disabled = true;

    try {
      const response = await fetch("/reset", {
        method: "POST",
        headers: authHeaders(),
        body: "{}",
      });
      const data = await parseResponse(response);
      if (!data) {
        return;
      }
      chat.replaceChildren();
      addMessage(data.reply || "上下文已清空", "assistant");
    } catch (error) {
      addMessage("请继续输入需求", "assistant");
    } finally {
      reset.disabled = false;
      send.disabled = false;
      input.focus();
    }
  });
}

if (authForm) {
  authForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const token = getTokenFromInputs();
    if (!/^\d{4}$/.test(token)) {
      showAuth("请输入 4 位数字口令");
      return;
    }
    verifyToken(token);
  });
}

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

async function verifyToken(token) {
  setAuthLoading(true);
  try {
    const response = await fetch("/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      showAuth(data.reply || "访问口令错误");
      return;
    }
    serverSessionId = data.sessionId || serverSessionId;
    saveAccessToken(token);
    showApp();
  } catch (error) {
    showAuth("验证失败，请稍后重试");
  } finally {
    setAuthLoading(false);
  }
}

async function initializeAuth() {
  try {
    const response = await fetch("/session");
    const data = await response.json();
    serverSessionId = data.sessionId || "";
  } catch (error) {
    serverSessionId = "";
  }

  const storedSessionId = sessionStorage.getItem(sessionIdKey) || "";
  const storedToken = sessionStorage.getItem(accessTokenKey) || "";
  if (serverSessionId && storedSessionId === serverSessionId && storedToken) {
    verifyToken(storedToken);
  } else {
    clearAccessToken();
    showAuth();
  }
}

async function initializeChat() {
  try {
    const response = await fetch("/session");
    const data = await response.json();
    serverSessionId = data.sessionId || "";
  } catch (error) {
    redirectToAuth();
    return;
  }

  const storedSessionId = sessionStorage.getItem(sessionIdKey) || "";
  const storedToken = sessionStorage.getItem(accessTokenKey) || "";
  if (!serverSessionId || storedSessionId !== serverSessionId || !storedToken) {
    redirectToAuth();
    return;
  }

  accessToken = storedToken;
  input.focus();
}

if (authForm) {
  initializeAuth();
} else if (app) {
  initializeChat();
}
