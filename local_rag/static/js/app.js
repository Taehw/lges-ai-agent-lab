const fileInput = document.getElementById("fileInput");
const uploadBox = document.getElementById("uploadBox");
const uploadStatus = document.getElementById("uploadStatus");
const fileList = document.getElementById("fileList");
const messagesEl = document.getElementById("messages");
const welcome = document.getElementById("welcome");
const chatForm = document.getElementById("chatForm");
const questionInput = document.getElementById("questionInput");
const sendBtn = document.getElementById("sendBtn");
const newChatBtn = document.getElementById("newChatBtn");
const modelStatus = document.getElementById("modelStatus");

const SESSION_KEY = "local_rag_session";

function sessionId() {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

function renderFiles(files) {
  fileList.innerHTML = "";
  if (!files || files.length === 0) {
    fileList.innerHTML = '<li class="empty">아직 업로드된 파일이 없습니다</li>';
    return;
  }
  files.forEach((file) => {
    const item = document.createElement("li");
    item.innerHTML = `<strong>${file.name}</strong><small>${file.pages || 0}페이지 · ${file.chunks || 0} chunks</small>`;
    fileList.appendChild(item);
  });
}

function appendMessage(role, html) {
  welcome?.remove();
  const row = document.createElement("div");
  row.className = `row ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = html;
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function sourceHtml(sources) {
  if (!sources || sources.length === 0) return "";
  const items = sources
    .map(
      (src) =>
        `<div class="source-item"><b>${escapeHtml(src.source)} · p.${escapeHtml(src.page)}</b><div>${escapeHtml(src.summary)}</div></div>`
    )
    .join("");
  return `<div class="sources"><h4>참고한 내용</h4>${items}</div>`;
}

async function refreshStatus() {
  const res = await fetch("/api/status");
  const data = await res.json();
  modelStatus.textContent = data.message || "준비 중";
  renderFiles(data.files || []);
}

uploadBox.addEventListener("dragover", (event) => {
  event.preventDefault();
  uploadBox.classList.add("dragover");
});

uploadBox.addEventListener("dragleave", () => uploadBox.classList.remove("dragover"));

uploadBox.addEventListener("drop", (event) => {
  event.preventDefault();
  uploadBox.classList.remove("dragover");
  if (event.dataTransfer.files.length) {
    uploadFiles(event.dataTransfer.files);
  }
});

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) {
    uploadFiles(fileInput.files);
  }
});

async function uploadFiles(fileListLike) {
  const form = new FormData();
  let pdfCount = 0;
  Array.from(fileListLike).forEach((file) => {
    if (file.name.toLowerCase().endsWith(".pdf")) {
      form.append("files", file);
      pdfCount += 1;
    }
  });
  if (!pdfCount) {
    uploadStatus.textContent = "PDF 파일만 업로드할 수 있습니다.";
    return;
  }
  uploadStatus.textContent = "문서를 처리하는 중입니다…";
  const res = await fetch("/api/upload", { method: "POST", body: form });
  const data = await res.json();
  if (!data.ok) {
    uploadStatus.textContent = data.error || "업로드에 실패했습니다.";
    return;
  }
  renderFiles(data.all_files);
  uploadStatus.textContent = `${data.chunk_count}개 chunk가 저장되었습니다.`;
  fileInput.value = "";
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;

  appendMessage("user", escapeHtml(question));
  questionInput.value = "";
  autosize();
  sendBtn.disabled = true;
  const pending = appendMessage("assistant", '<span class="thinking">답변을 생성하는 중입니다…</span>');

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, session_id: sessionId() }),
    });
    const data = await res.json();
    if (!data.ok) {
      pending.innerHTML = escapeHtml(data.error || "오류가 발생했습니다.");
    } else {
      const noInfo = data.answer === "정보가 없어서 답변할 수 없습니다";
      pending.innerHTML = `${escapeHtml(data.answer)}${noInfo ? "" : sourceHtml(data.sources)}`;
    }
  } catch (error) {
    pending.innerHTML = "서버와 통신하지 못했습니다.";
  } finally {
    sendBtn.disabled = false;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
});

newChatBtn.addEventListener("click", async () => {
  await fetch("/api/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId() }),
  });
  messagesEl.innerHTML =
    '<div class="welcome" id="welcome"><h3>새 대화를 시작합니다</h3><p>업로드한 문서는 그대로 두고, 대화 맥락만 초기화했습니다.</p></div>';
});

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

function autosize() {
  questionInput.style.height = "auto";
  questionInput.style.height = `${Math.min(questionInput.scrollHeight, 160)}px`;
}

questionInput.addEventListener("input", autosize);
refreshStatus();
setInterval(refreshStatus, 4000);
