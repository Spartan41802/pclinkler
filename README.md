# PC Stream

Stream your PC's screen to a browser tab, with mouse/keyboard control sent back —
no third-party service required. You run an .exe on your PC; the browser
connects to it directly over WebRTC.

**This is a starter/MVP.** It streams your screen and forwards input; it does not
capture game audio yet, and has no encryption beyond a shared-secret password
(don't expose it to the open internet without adding real auth/TLS).

## How it works

- `host/host.py` — runs on your gaming PC. Captures the screen (via `mss`),
  streams it as video over WebRTC (via `aiortc`), and runs its own WebSocket
  signaling server so the browser can find it and negotiate the connection.
  It also listens for mouse/keyboard events sent back from the browser and
  replays them with `pyautogui`.
- `web/index.html` + `web/client.js` — a plain HTML/JS page. Open it in any
  browser, enter your PC's address, and it connects, displays the video feed,
  and forwards your mouse/keyboard input.

## 1. Run the host on your PC

```bash
cd host
pip install -r requirements.txt
python host.py
```

You should see:
```
Starting signaling server on ws://0.0.0.0:8765
Screen: 1920x1080 @ 30fps
```

**Important:** open `host/host.py` and change `SHARED_SECRET` to your own value
before running — anyone who knows this value and can reach your IP/port can
connect.

## 2. Open the web client

Just open `web/index.html` directly in a browser (double-click it, or serve it
with any static file server). In the "Connect to your PC" box, enter:

```
ws://<your-pc-local-ip>:8765
```

Find your PC's local IP with `ipconfig` (Windows) — something like `192.168.1.100`.

- **Same network (e.g. laptop on your home wifi):** this just works.
- **Remote (outside your home network):** you'll need either:
  - Port forward `8765` (and the WebRTC media ports) on your router, or
  - Use a VPN/mesh network like **Tailscale** or **ZeroTier** so your remote
    device can reach your home PC's local IP securely — this is the easier
    and safer option, and avoids opening ports to the public internet.

## 3. Package the host as a standalone .exe

Once it's working via `python host.py`, bundle it so it doesn't require a
Python install on the target machine:

```bash
cd host
pyinstaller --onefile --name PCStreamHost host.py
```

The .exe will be in `host/dist/PCStreamHost.exe`. Copy that anywhere on your
PC and double-click it to start streaming (a console window will show logs).

## Repo structure

```
game-stream-web/
├── host/
│   ├── host.py           # capture + encode + WebRTC + signaling + input
│   └── requirements.txt
├── web/
│   ├── index.html
│   └── client.js
└── README.md
```

## Audio

The host captures **system audio** (whatever your PC is playing — game sound,
etc.) via WASAPI loopback, using `pyaudiowpatch`. This is **Windows-only**.
It captures your speaker/output device, not your microphone.

Requirements:
- `pip install PyAudioWPatch` (already in `requirements.txt`, Windows-only)
- A default playback device must be active/set on the host PC

If audio capture fails to initialize (e.g. running on non-Windows, or no
output device found), the host will log a warning and continue streaming
video-only — it won't crash the whole app.

Browsers require a user gesture before playing audio automatically; since the
video only starts after the person clicks "Connect", this is satisfied
automatically.

## Known limitations / next steps

- **No hardware encoding** — `aiortc`'s default encoder is software VP8/H264,
  fine for testing but not ideal for high FPS/low latency gaming. For real
  low-latency gaming performance, look at swapping the encoding pipeline for
  NVENC via GStreamer, similar to how Sunshine does it.
- **NAT traversal** — only STUN is configured. If both ends are behind strict
  NATs (e.g. connecting from outside your home without port forwarding or a
  VPN), you may need a TURN server (self-host with `coturn`).
- **Security** — the shared secret is very basic. If you expose this to the
  internet, add TLS (`wss://`) and a stronger auth mechanism.
- **Multi-monitor / resolution options** — currently grabs `monitors[1]`
  (primary) at native resolution; add settings for monitor selection and
  downscaling for bandwidth control.
