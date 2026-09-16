"""
Home PC streaming host.

Captures your screen, streams it over WebRTC to a browser, and receives
mouse/keyboard input back from the browser. Runs its own signaling
server (WebSocket) so no third-party service is required.

Run:
    pip install -r requirements.txt
    python host.py

Then open web/index.html in a browser (on the same PC, another device
on your LAN, or remotely if you've port-forwarded / used a VPN like
Tailscale) and point it at ws://<this-pc-ip>:8765
"""

import asyncio
import fractions
import json
import time

import mss
import numpy as np
import pyaudiowpatch as pyaudio
import pyautogui
import websockets
from av import AudioFrame, VideoFrame
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaStreamTrack

# ---- Config ---------------------------------------------------------------

SIGNALING_HOST = "0.0.0.0"
SIGNALING_PORT = 8765
CAPTURE_FPS = 30
# Shared secret so random people who find your IP can't connect.
# Change this, and set the same value in web/client.js
SHARED_SECRET = "change-me-please"

# Public STUN server (fine for LAN/port-forwarded use). For strict NATs
# on both ends you'll eventually want your own TURN server (coturn).
ICE_SERVERS = [RTCIceServer(urls="stun:stun.l.google.com:19302")]

pyautogui.FAILSAFE = False

# ---- Screen capture video track --------------------------------------------


class ScreenCaptureTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, fps=CAPTURE_FPS, monitor_index=1):
        super().__init__()
        self._sct = mss.mss()
        self._monitor = self._sct.monitors[monitor_index]
        self._frame_interval = 1.0 / fps
        self._last_time = None
        self._timestamp = 0

    async def recv(self):
        if self._last_time is not None:
            elapsed = time.time() - self._last_time
            wait = self._frame_interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_time = time.time()

        img = self._sct.grab(self._monitor)
        frame_array = np.array(img)[:, :, :3]  # drop alpha, BGRA -> BGR

        frame = VideoFrame.from_ndarray(frame_array, format="bgr24")
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, 90000)
        self._timestamp += int(90000 * self._frame_interval)
        return frame


class AudioCaptureTrack(MediaStreamTrack):
    """Captures system audio (whatever your PC is playing) via WASAPI loopback.

    Windows only. This grabs speaker/game output, not your microphone.
    """

    kind = "audio"

    def __init__(self, chunk_frames=960):
        super().__init__()
        self._pa = pyaudio.PyAudio()
        self._device = self._get_default_loopback_device()
        self._channels = int(self._device["maxInputChannels"])
        self._rate = int(self._device["defaultSampleRate"])
        self._chunk_frames = chunk_frames  # frames per channel, per packet

        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=self._channels,
            rate=self._rate,
            input=True,
            input_device_index=self._device["index"],
            frames_per_buffer=self._chunk_frames,
        )
        self._timestamp = 0
        print(f"[audio] capturing loopback: {self._device['name']} "
              f"({self._channels}ch @ {self._rate}Hz)")

    def _get_default_loopback_device(self):
        wasapi_info = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = self._pa.get_device_info_by_index(
            wasapi_info["defaultOutputDevice"]
        )
        if not default_speakers.get("isLoopbackDevice"):
            for loopback in self._pa.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    return loopback
            raise RuntimeError(
                "No matching WASAPI loopback device found for default output. "
                "Is a playback device active?"
            )
        return default_speakers

    async def recv(self):
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None, self._stream.read, self._chunk_frames, False
        )

        samples = np.frombuffer(raw, dtype=np.int16)
        layout = "stereo" if self._channels == 2 else "mono"

        frame = AudioFrame.from_ndarray(
            samples.reshape(1, -1), format="s16", layout=layout
        )
        frame.sample_rate = self._rate
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, self._rate)
        self._timestamp += self._chunk_frames

        return frame


# ---- Input handling ---------------------------------------------------------

SCREEN_W, SCREEN_H = pyautogui.size()


def handle_input_event(data: dict):
    """Apply an input event received from the browser."""
    try:
        etype = data.get("type")

        if etype == "mousemove":
            # x, y expected as normalized [0,1] floats from the client
            x = int(data["x"] * SCREEN_W)
            y = int(data["y"] * SCREEN_H)
            pyautogui.moveTo(x, y)

        elif etype == "mousedown":
            pyautogui.mouseDown(button=data.get("button", "left"))

        elif etype == "mouseup":
            pyautogui.mouseUp(button=data.get("button", "left"))

        elif etype == "wheel":
            pyautogui.scroll(int(data.get("deltaY", 0) * -1))

        elif etype == "keydown":
            key = data.get("key")
            if key:
                pyautogui.keyDown(key)

        elif etype == "keyup":
            key = data.get("key")
            if key:
                pyautogui.keyUp(key)

    except Exception as e:
        print(f"[input] failed to apply event {data}: {e}")


# ---- WebRTC / signaling ------------------------------------------------------

pcs = set()


async def handle_connection(websocket):
    print("[signaling] client connected")

    # Simple auth: first message must be the shared secret
    try:
        auth_msg = await asyncio.wait_for(websocket.recv(), timeout=10)
        auth = json.loads(auth_msg)
        if auth.get("secret") != SHARED_SECRET:
            print("[signaling] rejected: bad secret")
            await websocket.close()
            return
    except Exception:
        await websocket.close()
        return

    config = RTCConfiguration(iceServers=ICE_SERVERS)
    pc = RTCPeerConnection(configuration=config)
    pcs.add(pc)

    pc.addTrack(ScreenCaptureTrack())

    try:
        pc.addTrack(AudioCaptureTrack())
    except Exception as e:
        print(f"[audio] disabled, could not start capture: {e}")

    @pc.on("datachannel")
    def on_datachannel(channel):
        @channel.on("message")
        def on_message(message):
            try:
                data = json.loads(message)
                handle_input_event(data)
            except Exception as e:
                print(f"[datachannel] bad message: {e}")

    @pc.on("connectionstatechange")
    async def on_state_change():
        print(f"[webrtc] state: {pc.connectionState}")
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await pc.close()
            pcs.discard(pc)

    try:
        async for message in websocket:
            msg = json.loads(message)

            if msg["type"] == "offer":
                offer = RTCSessionDescription(sdp=msg["sdp"], type=msg["type"])
                await pc.setRemoteDescription(offer)
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                await websocket.send(json.dumps({
                    "type": pc.localDescription.type,
                    "sdp": pc.localDescription.sdp,
                }))

            elif msg["type"] == "ice-candidate":
                # aiortc handles ICE gathering internally in most simple
                # setups; trickle ICE support can be added here if needed.
                pass

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        print("[signaling] client disconnected")
        await pc.close()
        pcs.discard(pc)


async def main():
    print(f"Starting signaling server on ws://{SIGNALING_HOST}:{SIGNALING_PORT}")
    print(f"Screen: {SCREEN_W}x{SCREEN_H} @ {CAPTURE_FPS}fps")
    async with websockets.serve(handle_connection, SIGNALING_HOST, SIGNALING_PORT):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
