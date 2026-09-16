// Must match SHARED_SECRET in host/host.py
const SHARED_SECRET = "change-me-please";

const video = document.getElementById("video");
const statusEl = document.getElementById("status");
const connectBar = document.getElementById("connectBar");
const hostInput = document.getElementById("hostInput");
const connectBtn = document.getElementById("connectBtn");

let pc = null;
let ws = null;
let dataChannel = null;

connectBtn.addEventListener("click", () => connect(hostInput.value.trim()));

function connect(wsUrl) {
  if (!wsUrl) return;

  connectBar.style.display = "none";
  statusEl.style.display = "block";
  statusEl.textContent = "Connecting…";

  ws = new WebSocket(wsUrl);

  ws.onopen = async () => {
    ws.send(JSON.stringify({ secret: SHARED_SECRET }));
    await startWebRTC();
  };

  ws.onmessage = async (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "answer") {
      await pc.setRemoteDescription(new RTCSessionDescription(msg));
      statusEl.textContent = "Connected";
    }
  };

  ws.onclose = () => {
    statusEl.textContent = "Disconnected";
  };

  ws.onerror = () => {
    statusEl.textContent = "Connection error";
  };
}

async function startWebRTC() {
  pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  });

  pc.ontrack = (event) => {
    video.srcObject = event.streams[0];
    video.style.display = "block";
  };

  dataChannel = pc.createDataChannel("input");
  dataChannel.onopen = () => attachInputHandlers();

  const offer = await pc.createOffer({ offerToReceiveVideo: true });
  await pc.setLocalDescription(offer);

  ws.send(JSON.stringify({ type: offer.type, sdp: offer.sdp }));
}

function sendInput(data) {
  if (dataChannel && dataChannel.readyState === "open") {
    dataChannel.send(JSON.stringify(data));
  }
}

function attachInputHandlers() {
  video.addEventListener("mousemove", (e) => {
    const rect = video.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;
    sendInput({ type: "mousemove", x, y });
  });

  video.addEventListener("mousedown", (e) => {
    sendInput({ type: "mousedown", button: buttonName(e.button) });
  });

  video.addEventListener("mouseup", (e) => {
    sendInput({ type: "mouseup", button: buttonName(e.button) });
  });

  video.addEventListener("wheel", (e) => {
    sendInput({ type: "wheel", deltaY: e.deltaY });
    e.preventDefault();
  }, { passive: false });

  video.addEventListener("contextmenu", (e) => e.preventDefault());

  window.addEventListener("keydown", (e) => {
    sendInput({ type: "keydown", key: mapKey(e.key) });
    e.preventDefault();
  });

  window.addEventListener("keyup", (e) => {
    sendInput({ type: "keyup", key: mapKey(e.key) });
    e.preventDefault();
  });
}

function buttonName(code) {
  return { 0: "left", 1: "middle", 2: "right" }[code] || "left";
}

// Basic browser-key -> pyautogui-key mapping. Extend as needed.
function mapKey(key) {
  const map = {
    " ": "space",
    "ArrowUp": "up",
    "ArrowDown": "down",
    "ArrowLeft": "left",
    "ArrowRight": "right",
    "Escape": "esc",
  };
  return map[key] || key.toLowerCase();
}
