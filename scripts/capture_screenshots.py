"""Capture genuine app screenshots via the Chrome DevTools Protocol (host-side helper for docs).

Requires a local Chrome/Edge and `pip install websocket-client`. The app must be running.
Usage: python scripts/capture_screenshots.py <chrome.exe> name=url [name=url ...]
Images go to docs/images/<name>.png.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

OUT = Path(__file__).resolve().parents[1] / "docs" / "images"
PORT = 9333


def cdp(ws, method, _id=[0], **params):
    _id[0] += 1
    ws.send(json.dumps({"id": _id[0], "method": method, "params": params}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _id[0]:
            return msg.get("result", {})


def main(chrome: str, targets: list[str]) -> None:
    profile = tempfile.mkdtemp(prefix="lab-shots-")
    proc = subprocess.Popen([chrome, "--headless=new", f"--remote-debugging-port={PORT}", "--hide-scrollbars",
                             f"--user-data-dir={profile}", "--window-size=1440,1000", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                page = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
        cdp(ws, "Page.enable")
        for spec in targets:
            name, url = spec.split("=", 1)
            cdp(ws, "Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False)
            cdp(ws, "Page.navigate", url=url)
            time.sleep(9)  # Streamlit renders over a websocket after load
            h = cdp(ws, "Runtime.evaluate", expression=(
                "Math.max(...[...document.querySelectorAll('section, div')].map(e => e.scrollHeight))"),
                returnByValue=True)["result"]["value"]
            h = int(min(max(h, 1000), 4000))
            cdp(ws, "Emulation.setDeviceMetricsOverride", width=1440, height=h, deviceScaleFactor=1, mobile=False)
            time.sleep(2)
            data = cdp(ws, "Page.captureScreenshot", format="png")["data"]
            (OUT / f"{name}.png").write_bytes(base64.b64decode(data))
            print(f"{name}: {h}px")
    finally:
        proc.terminate()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
