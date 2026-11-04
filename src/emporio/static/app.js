"use strict";

const log = document.getElementById("log");
const composer = document.getElementById("composer");
const input = document.getElementById("message");
const sendButton = document.getElementById("send");
const customerSelect = document.getElementById("customer");
const newChatButton = document.getElementById("new-chat");
const showToolsToggle = document.getElementById("show-tools");
const statusLine = document.getElementById("status");
const welcomeTemplate = document.getElementById("welcome-template");

const SERVER_UNREACHABLE = "Não consegui falar com o servidor local. Confira se ele continua em execução.";

let sessionId = null;
let busy = false;

async function post(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error(SERVER_UNREACHABLE);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(data?.message ?? SERVER_UNREACHABLE);
    error.code = data?.error;
    throw error;
  }
  return data;
}

function scrollToEnd() {
  log.scrollTop = log.scrollHeight;
}

// A reply taller than the log opens at its first line, or the customer would land on its end.
function revealReply(bubble) {
  if (bubble.offsetHeight > log.clientHeight) {
    bubble.scrollIntoView({ block: "start" });
  } else {
    scrollToEnd();
  }
}

function refreshControls() {
  const canSend = !busy && sessionId !== null;
  input.disabled = !canSend;
  sendButton.disabled = !canSend;
  customerSelect.disabled = busy;
  newChatButton.disabled = busy;
  log.querySelectorAll(".suggestion").forEach((button) => {
    button.disabled = !canSend;
  });
}

// Replies are model output, so they are only ever inserted as text nodes, never parsed as HTML.
function appendRichText(parent, text) {
  const parts = text.split("**");
  const balanced = parts.length % 2 === 1;
  if (!balanced) {
    parent.append(document.createTextNode(text));
    return;
  }
  parts.forEach((part, index) => {
    if (!part) return;
    if (index % 2 === 1) {
      const strong = document.createElement("strong");
      strong.textContent = part;
      parent.append(strong);
    } else {
      parent.append(document.createTextNode(part));
    }
  });
}

function addBubble(role, text) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  if (role === "agent") {
    appendRichText(bubble, text);
  } else {
    bubble.textContent = text;
  }
  log.append(bubble);
  scrollToEnd();
  return bubble;
}

function addNotice(text) {
  const notice = document.createElement("p");
  notice.className = "notice";
  notice.textContent = text;
  log.append(notice);
  scrollToEnd();
}

function describeTool(tool) {
  const result = tool.error ? `erro: ${tool.error}` : "ok";
  const sources = tool.sources.length ? ` · seções ${tool.sources.join(", ")}` : "";
  return { call: `${tool.name}(${tool.arguments})`, result: `${result}${sources}`, failed: Boolean(tool.error) };
}

function addToolDetails(tools) {
  if (!tools.length) return;
  const details = document.createElement("details");
  details.className = "tools";
  const summary = document.createElement("summary");
  summary.textContent = tools.length === 1 ? "1 ferramenta consultada" : `${tools.length} ferramentas consultadas`;
  details.append(summary);
  const list = document.createElement("ul");
  for (const tool of tools) {
    const { call, result, failed } = describeTool(tool);
    const item = document.createElement("li");
    const code = document.createElement("code");
    code.textContent = call;
    const outcome = document.createElement("span");
    outcome.className = failed ? "outcome failed" : "outcome";
    outcome.textContent = result;
    item.append(code, outcome);
    list.append(item);
  }
  details.append(list);
  log.append(details);
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}

function showWelcome() {
  log.replaceChildren(welcomeTemplate.content.cloneNode(true));
  log.querySelectorAll(".suggestion").forEach((button) => {
    button.addEventListener("click", () => send(button.textContent));
  });
}

async function startSession() {
  busy = true;
  sessionId = null;
  refreshControls();
  log.replaceChildren();
  try {
    const value = customerSelect.value;
    const session = await post("/api/session", { customer_id: value ? Number(value) : null });
    sessionId = session.session_id;
    const who = session.customer ? `sessão de ${session.customer.first_name}` : "sem cliente identificado";
    statusLine.textContent = `Assistente virtual · protótipo · ${who} · modelo ${session.model}`;
    showWelcome();
  } catch (error) {
    addNotice(error.message);
  } finally {
    busy = false;
    refreshControls();
  }
  if (sessionId) input.focus();
}

async function send(rawText) {
  const text = rawText.trim();
  if (!text || busy || !sessionId) return;
  busy = true;
  log.querySelector(".welcome")?.remove();
  const userBubble = addBubble("user", text);
  input.value = "";
  resizeInput();
  refreshControls();
  const typing = addBubble("agent typing", "Consultando…");
  try {
    const reply = await post("/api/message", { session_id: sessionId, text });
    typing.remove();
    const bubble = addBubble("agent", reply.text);
    addToolDetails(reply.tools);
    if (reply.failure) {
      addNotice(
        `Esta é uma mensagem de contingência, não uma resposta do modelo (${reply.failure}). ` +
        "Se persistir, confira OPENAI_API_KEY e OPENAI_MODEL no .env.",
      );
    }
    revealReply(bubble);
  } catch (error) {
    typing.remove();
    if (error.code === "session_not_found") {
      sessionId = null;
    } else {
      // The message never reached the agent, so it goes back to the box to be sent again.
      userBubble.remove();
      input.value = text;
      resizeInput();
    }
    addNotice(error.message);
  } finally {
    busy = false;
    refreshControls();
  }
  if (sessionId) input.focus();
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  send(input.value);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send(input.value);
  }
});

input.addEventListener("input", resizeInput);
customerSelect.addEventListener("change", startSession);
newChatButton.addEventListener("click", startSession);
showToolsToggle.addEventListener("change", () => {
  document.body.classList.toggle("show-tools", showToolsToggle.checked);
  scrollToEnd();
});

document.body.classList.toggle("show-tools", showToolsToggle.checked);
startSession();
