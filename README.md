# ViGo — Passenger Safety Demo

## 1. What this is

ViGo is a passenger safety IoT demo: an **ESP32 device** mounted in a vehicle streams GPS + battery telemetry to a local **FastAPI backend** and raises **SOS events** when the panic button is pressed or a loud sound is detected. A browser **dashboard** (served by the same backend) shows the live map, telemetry feed, and a latching red alert banner. The three pieces live under `D:\projects\Pinky` as `backend\`, `dashboard\`, and `scripts\`. Demo target is tomorrow morning (**Oct 9, 2026, IST**); if the hardware acts up on stage, `scripts\simulate.py` replays the same traffic from the laptop so the dashboard still tells the story.

---

## 2. One-time setup

Run these in PowerShell:

```powershell
cd D:\projects\Pinky
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
pip install requests

# Generate a device token:
python -c "import secrets; print('DEVICE_TOKEN=' + secrets.token_urlsafe(32))"

# Copy .env.example to .env, then paste the DEVICE_TOKEN=... line above into .env.
# The firmware developer needs the SAME token baked into the ESP32 build.
copy .env.example .env
notepad .env
```

`.env` should end up with at minimum:

```
DEVICE_TOKEN=<the token you generated above>
```

---

## 3. Run the demo — exactly 3 terminals

**Terminal 1 — backend (always):**
```powershell
cd D:\projects\Pinky
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
Wait for `VIGO001 seeded` in the logs before continuing.

**Terminal 2 — dashboard (always, just a browser):**
```powershell
start chrome http://localhost:8000/
```
You should see a green `Backend connected` banner and an empty Pune-centered map.

**Terminal 3 — simulator (ONLY if ESP32 fails, otherwise skip):**
```powershell
cd D:\projects\Pinky
.\.venv\Scripts\Activate.ps1
python scripts\simulate.py
```

---

## 4. ESP32 integration

1. On the demo laptop, run `ipconfig` and copy the **IPv4 Address** of the active Wi-Fi adapter (something like `192.168.1.37`).
2. Hand that IP to the firmware developer — the ESP32 should POST to `http://<that-ip>:8000` (same endpoints as the simulator). Both laptop and ESP32 **must be on the same Wi-Fi**.
3. The first time the ESP32 POSTs, Windows Defender Firewall will prompt — click **Allow** for both private and public networks. Or pre-authorize once (admin PowerShell):
   ```powershell
   New-NetFirewallRule -DisplayName "ViGo FastAPI" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
   ```
4. The same `DEVICE_TOKEN` from `.env` must be flashed into the firmware build — mismatches return `401`.

---

## 5. Simulator cheat sheet

| Key | What it does |
|-----|--------------|
| `n` | Set `sos_status` back to `NORMAL` on outgoing telemetry |
| `s` | **Send SOS BUTTON event immediately** (demo money shot) |
| `l` | Send SOS LOUD_SOUND event with random `sound_level` 75–95 |
| `w` | Send WARNING `RASH_DRIVING` event |
| `m` | Nudge GPS by ~50m in a random direction (vehicle moving) |
| `b` | Drop battery by 10% |
| `h` / `?` | Print the help menu |
| `q` | Quit cleanly |

Telemetry fires automatically every **5 seconds** in the background; battery drains 1% every ~30 packets (floor 20%).

---

## 6. Troubleshooting

- **Dashboard WebSocket not connecting** → check Terminal 1 for stack traces; in Chrome DevTools → Network → WS, confirm the WS upgrade returned 101. Hard-refresh with Ctrl+F5.
- **`401 Unauthorized` on POST** → `DEVICE_TOKEN` in `.env` doesn't match the token the firmware or simulator is sending. Compare both; regenerate if needed and reflash.
- **`400` with "missing X-Device-ID"** → header spelling. It is literally `X-Device-ID: VIGO001` (hyphens, capital D, capital ID).
- **Dashboard shows blank map** → open Chrome DevTools console; Leaflet tile errors usually = offline or adblock. Disable adblock for `localhost`.
- **Simulator says "Backend not reachable"** → Terminal 1 isn't running, or it crashed. Scroll Terminal 1 for the traceback.
- **ESP32 can't reach backend** → different Wi-Fi networks, or Windows Firewall blocked port 8000. See Section 4.

---

## 7. Demo flow — exact script for tomorrow

**T−1 min:**
1. Terminal 1: start uvicorn, wait for `VIGO001 seeded`.
2. Terminal 2: `start chrome http://localhost:8000/`. Confirm **green "Backend connected"** banner.
3. Firmware dev confirms ESP32 is powered + joined Wi-Fi + telemetry logs showing on backend. Pin should appear on the map and move slightly every 5s.

**During demo:**
1. **Talk through the map** while telemetry pin moves (vehicle in motion).
2. **Press the physical SOS button** on the device → red banner latches: **"SOS TRIGGERED — BUTTON"**, event appears in the right-hand feed.
3. **Clap loudly near the mic** → red banner: **"SOS TRIGGERED — LOUD SOUND"**, event in feed, screen border flashes red.
4. Click **RESET** on the dashboard to clear the banner (local-only; device stays latched until a resolve API call).

**If ESP32 fails at any point:**
- Open Terminal 3, run `python scripts\simulate.py`, press `s` or `l` on cue. Dashboard behaves identically.
