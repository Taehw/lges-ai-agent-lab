const ragBtn = document.getElementById("ragBtn");
const ragInput = document.getElementById("ragInput");
const ragList = document.getElementById("ragList");
const contractBtn = document.getElementById("contractBtn");
const contractInput = document.getElementById("contractInput");
const contractName = document.getElementById("contractName");
const reviewBtn = document.getElementById("reviewBtn");
const messages = document.getElementById("messages");
const welcome = document.getElementById("welcome");
const chatForm = document.getElementById("chatForm");
const questionInput = document.getElementById("questionInput");
const sendBtn = document.getElementById("sendBtn");

let busy = false;

function hideWelcome() {
  if (welcome) {
    welcome.remove();
  }
}

function scrollDown() {
  messages.scrollTop = messages.scrollHeight;
}

function addRow(role) {
  hideWelcome();
  const row = document.createElement("div");
  row.className = `row ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  row.appendChild(bubble);
  messages.appendChild(row);
  scrollDown();
  return bubble;
}

function addUser(text) {
  const bubble = addRow("user");
  bubble.textContent = text;
  return bubble;
}

function addAssistant(text) {
  const bubble = addRow("assistant");
  bubble.textContent = text;
  return bubble;
}

function jobBubble() {
  const bubble = addRow("assistant");
  bubble.textContent = "";
  const meter = document.createElement("div");
  meter.className = "meter";
  const fill = document.createElement("span");
  meter.appendChild(fill);
  const bar = document.createElement("pre");
  bar.className = "tqdm";
  const body = document.createElement("div");
  bubble.append(meter, bar, body);
  return { bubble, fill, bar, body };
}

function setBusy(next) {
  busy = next;
  ragBtn.disabled = next;
  contractBtn.disabled = next;
  reviewBtn.disabled = next;
  sendBtn.disabled = next;
}

function renderFiles(files) {
  ragList.textContent = "";
  if (!files || files.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "업로드된 파일이 없습니다";
    ragList.appendChild(empty);
    return;
  }
  for (const file of files) {
    const item = document.createElement("li");
    const name = document.createElement("strong");
    name.textContent = file.name;
    const meta = document.createElement("span");
    meta.textContent = `${file.pages}페이지 · ${file.chunks}청크`;
    item.append(name, meta);
    ragList.appendChild(item);
  }
}

function showContract(name, ready) {
  if (name) {
    contractName.textContent = name;
  }
  reviewBtn.hidden = !ready;
}

function appendClause(body, original, revised) {
  const clause = document.createElement("div");
  clause.className = "clause";
  const source = document.createElement("div");
  source.className = "original";
  source.textContent = `[원문] ${original}`;
  clause.appendChild(source);
  if (revised) {
    const edited = document.createElement("div");
    edited.className = "revised";
    edited.textContent = `[수정문구] ${revised}`;
    clause.appendChild(edited);
  }
  body.appendChild(clause);
  scrollDown();
}

function appendNote(body, className, text) {
  const note = document.createElement("div");
  note.className = className;
  note.textContent = text;
  body.appendChild(note);
  scrollDown();
}

async function readSse(response, onEvent) {
  if (!response.ok) {
    let message = "요청에 실패했습니다.";
    try {
      const body = await response.json();
      message = body.error || message;
    } catch (_err) {
      message = "요청에 실패했습니다.";
    }
    onEvent("error", { error: message });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      let eventName = "message";
      let data = "";
      for (const line of part.split("\n")) {
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          data += line.slice(5).trim();
        }
      }
      if (!data) {
        continue;
      }
      onEvent(eventName, JSON.parse(data));
    }
  }
}

function applyProgress(view, data) {
  if (typeof data.percent === "number") {
    view.fill.style.width = `${data.percent}%`;
  }
  if (data.bar) {
    view.bar.textContent = data.bar;
  }
  scrollDown();
}

function applyDone(view, data) {
  if (typeof data.percent === "number") {
    view.fill.style.width = "100%";
  }
  if (data.message) {
    appendNote(view.body, "done", data.message);
  }
  if (data.files) {
    renderFiles(data.files);
  }
  showContract(data.contract_name, Boolean(data.contract_ready));
}

async function uploadFiles(url, field, fileList, label) {
  if (busy || fileList.length === 0) {
    return;
  }
  const form = new FormData();
  for (const file of fileList) {
    form.append(field, file);
  }
  setBusy(true);
  addUser(label);
  const view = jobBubble();
  try {
    const response = await fetch(url, { method: "POST", body: form });
    await readSse(response, (eventName, data) => {
      if (eventName === "progress") {
        applyProgress(view, data);
      } else if (eventName === "done") {
        applyDone(view, data);
      } else if (eventName === "error") {
        appendNote(view.body, "error", data.error || "처리 중 오류");
      }
    });
  } catch (_err) {
    appendNote(view.body, "error", "서버와 연결하지 못했습니다.");
  } finally {
    setBusy(false);
  }
}

async function startReview() {
  if (busy || reviewBtn.hidden) {
    return;
  }
  setBusy(true);
  addUser("계약서 검토를 시작합니다.");
  const view = jobBubble();
  try {
    const response = await fetch("/api/contract/review", { method: "POST" });
    await readSse(response, (eventName, data) => {
      if (eventName === "progress") {
        applyProgress(view, data);
      } else if (eventName === "sentence") {
        appendClause(view.body, data.original, data.revised);
      } else if (eventName === "done") {
        applyDone(view, data);
      } else if (eventName === "error") {
        appendNote(view.body, "error", data.error || "검토 중 오류");
      }
    });
  } catch (_err) {
    appendNote(view.body, "error", "서버와 연결하지 못했습니다.");
  } finally {
    setBusy(false);
  }
}

async function sendQuestion(question) {
  if (busy || !question) {
    return;
  }
  setBusy(true);
  addUser(question);
  const bubble = addAssistant("답변을 작성하고 있습니다.");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await response.json();
    bubble.textContent = data.ok ? data.answer : data.error || "답변을 만들지 못했습니다.";
  } catch (_err) {
    bubble.textContent = "서버와 연결하지 못했습니다.";
  } finally {
    setBusy(false);
    scrollDown();
  }
}

ragBtn.addEventListener("click", () => ragInput.click());
contractBtn.addEventListener("click", () => contractInput.click());
reviewBtn.addEventListener("click", startReview);

ragInput.addEventListener("change", () => {
  const files = Array.from(ragInput.files || []);
  ragInput.value = "";
  const label = `가이드라인 PDF ${files.length}개를 업로드합니다.`;
  uploadFiles("/api/rag/upload", "files", files, label);
});

contractInput.addEventListener("change", () => {
  const files = Array.from(contractInput.files || []);
  contractInput.value = "";
  uploadFiles("/api/contract/upload", "file", files.slice(0, 1), "계약서 PDF를 업로드합니다.");
});

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  questionInput.value = "";
  questionInput.style.height = "auto";
  sendQuestion(question);
});

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

questionInput.addEventListener("input", () => {
  questionInput.style.height = "auto";
  questionInput.style.height = `${Math.min(questionInput.scrollHeight, 160)}px`;
});

async function restore() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();
    renderFiles(data.rag_files || []);
    showContract(data.contract_name, Boolean(data.contract_ready));
    if (!data.api_key_set) {
      addAssistant("OPENAI_API_KEY 환경 변수가 없습니다. 키를 설정한 뒤 서버를 다시 시작해 주세요.");
    }
  } catch (_err) {
    addAssistant("서버 상태를 불러오지 못했습니다.");
  }
}

restore();
