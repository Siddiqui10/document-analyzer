// Document Analyzer — frontend logic
// Talks to the FastAPI backend over Server-Sent Events (SSE) so the AI
// response renders progressively instead of appearing all at once.

const API_BASE = ""; // same-origin; change if backend is hosted separately, e.g. "https://api.example.com"

const tabs = document.querySelectorAll(".tab");
const tabPanels = {
  upload: document.getElementById("tab-upload"),
  paste: document.getElementById("tab-paste"),
};
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const fileChip = document.getElementById("fileChip");
const textInput = document.getElementById("textInput");
const modeSelect = document.getElementById("modeSelect");
const customInstructions = document.getElementById("customInstructions");
const analyzeBtn = document.getElementById("analyzeBtn");
const btnText = document.getElementById("btnText");
const btnSpinner = document.getElementById("btnSpinner");
const errorMsg = document.getElementById("errorMsg");
const output = document.getElementById("output");
const copyBtn = document.getElementById("copyBtn");
const clearBtn = document.getElementById("clearBtn");

let activeTab = "upload";
let selectedFile = null;

// ---------------- Tabs ----------------
tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    activeTab = tab.dataset.tab;
    Object.entries(tabPanels).forEach(([key, el]) => {
      el.classList.toggle("hidden", key !== activeTab);
    });
  });
});

// ---------------- File upload ----------------
dropzone.addEventListener("click", (e) => {
  if (e.target !== fileInput) fileInput.click();
});

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) setFile(fileInput.files[0]);
});

["dragover", "dragenter"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) setFile(file);
});

function setFile(file) {
  if (file.type !== "application/pdf") {
    showError("Please choose a PDF file.");
    return;
  }
  selectedFile = file;
  fileChip.hidden = false;
  fileChip.textContent = `${file.name} (${(file.size / 1024).toFixed(0)} KB)`;
}

// ---------------- Helpers ----------------
function showError(msg) {
  errorMsg.hidden = false;
  errorMsg.textContent = msg;
}
function clearError() {
  errorMsg.hidden = true;
  errorMsg.textContent = "";
}
function setLoading(isLoading) {
  analyzeBtn.disabled = isLoading;
  btnSpinner.hidden = !isLoading;
  btnText.textContent = isLoading ? "Analyzing..." : "Analyze Document";
}
function resetOutput() {
  output.innerHTML = '<div class="placeholder"><div class="placeholder-icon">✨</div><p>Your AI-generated result will appear here, streaming in real time.</p></div>';
  copyBtn.hidden = true;
  clearBtn.hidden = true;
}

clearBtn.addEventListener("click", resetOutput);
copyBtn.addEventListener("click", () => {
  navigator.clipboard.writeText(output.dataset.raw || "");
  copyBtn.textContent = "Copied!";
  setTimeout(() => (copyBtn.textContent = "Copy"), 1500);
});

// ---------------- Analyze ----------------
analyzeBtn.addEventListener("click", async () => {
  clearError();

  const mode = modeSelect.value;
  const instructions = customInstructions.value.trim();

  if (activeTab === "upload" && !selectedFile) {
    showError("Please choose a PDF file first.");
    return;
  }
  if (activeTab === "paste" && !textInput.value.trim()) {
    showError("Please paste some text first.");
    return;
  }

  setLoading(true);
  output.innerHTML = "";
  output.dataset.raw = "";
  copyBtn.hidden = true;
  clearBtn.hidden = true;

  const streamEl = document.createElement("div");
  streamEl.className = "cursor-blink";
  output.appendChild(streamEl);

  try {
    let response;
    if (activeTab === "upload") {
      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("mode", mode);
      formData.append("custom_instructions", instructions);
      response = await fetch(`${API_BASE}/api/analyze/pdf`, {
        method: "POST",
        body: formData,
      });
    } else {
      response = await fetch(`${API_BASE}/api/analyze/text`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: textInput.value,
          mode,
          custom_instructions: instructions,
        }),
      });
    }

    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: "Request failed." }));
      throw new Error(err.detail || "Request failed.");
    }

    await readStream(response, streamEl);
  } catch (err) {
    showError(err.message || "Something went wrong.");
    resetOutput();
  } finally {
    setLoading(false);
  }
});

async function readStream(response, streamEl) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullText = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split("\n\n");
    buffer = events.pop(); // keep incomplete chunk for next read

    for (const evt of events) {
      const line = evt.trim();
      if (!line.startsWith("data:")) continue;
      const jsonStr = line.slice(5).trim();
      let payload;
      try {
        payload = JSON.parse(jsonStr);
      } catch {
        continue;
      }

      if (payload.type === "delta") {
        fullText += payload.text;
        streamEl.innerHTML = marked.parse(fullText);
        output.dataset.raw = fullText;
        output.scrollTop = output.scrollHeight;
      } else if (payload.type === "error") {
        throw new Error(payload.message);
      } else if (payload.type === "done") {
        streamEl.classList.remove("cursor-blink");
        copyBtn.hidden = false;
        clearBtn.hidden = false;
      }
    }
  }
}
