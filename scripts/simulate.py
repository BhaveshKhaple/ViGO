"""
ViGo Passenger Safety — Device Simulator
=========================================
Backup for the ESP32 during the Oct 9, 2026 demo. Produces identical
traffic patterns to the real device so the backend + dashboard behave
the same whether the hardware shows up or not.

Usage (PowerShell, from D:\\projects\\Pinky):
    .\\.venv\\Scripts\\Activate.ps1
    python scripts\\simulate.py

Reads DEVICE_TOKEN from D:\\projects\\Pinky\\.env
"""

import json
import os
import platform
import random
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------- requests with urllib fallback ----------
try:
    import requests  # noqa: F401
    HAVE_REQUESTS = True
except ImportError:
    HAVE_REQUESTS = False
    import urllib.request
    import urllib.error

# ---------- Windows single-key input ----------
IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    import msvcrt
else:
    # POSIX fallback (for dev on mac/linux)
    import termios
    import tty

# ---------- Config ----------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
BASE_URL = "http://127.0.0.1:8000"
DEVICE_ID = "VIGO001"
TELEMETRY_INTERVAL_S = 5

# ---------- ANSI colors (best effort) ----------
if IS_WINDOWS:
    # Enable VT processing on Win10+
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass

C_RESET = "\033[0m"
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"
C_BOLD = "\033[1m"


def color(txt: str, c: str) -> str:
    return f"{c}{txt}{C_RESET}"


# ---------- Module state (shared with telemetry thread) ----------
state_lock = threading.Lock()
current_lat = 18.5204
current_lng = 73.8567
current_battery = 85
current_sos_status = "NORMAL"
telemetry_counter = 0
event_counter = 0
telemetry_tick_count = 0  # for battery drain cadence
shutdown_flag = threading.Event()


# ---------- Helpers ----------
def load_device_token() -> str:
    if not ENV_PATH.exists():
        print(color(f"ERROR: .env not found at {ENV_PATH}", C_RED))
        print("Create it with: DEVICE_TOKEN=<your token>")
        sys.exit(1)
    token = None
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() == "DEVICE_TOKEN":
            token = val.strip().strip('"').strip("'")
            break
    if not token:
        print(color("ERROR: DEVICE_TOKEN not set in .env", C_RED))
        sys.exit(1)
    return token


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def auth_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "X-Device-ID": DEVICE_ID,
        "Content-Type": "application/json",
    }


def http_post(url: str, body: dict, headers: dict, timeout: float = 5.0):
    """Returns (status_code, text) or (None, error_message)."""
    data = json.dumps(body).encode("utf-8")
    if HAVE_REQUESTS:
        try:
            r = requests.post(url, data=data, headers=headers, timeout=timeout)
            return r.status_code, r.text
        except requests.exceptions.ConnectionError:
            return None, "connection_error"
        except requests.exceptions.Timeout:
            return None, "timeout"
        except Exception as e:
            return None, f"error:{e}"
    else:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                body_txt = e.read().decode("utf-8", errors="replace")
            except Exception:
                body_txt = ""
            return e.code, body_txt
        except urllib.error.URLError:
            return None, "connection_error"
        except Exception as e:
            return None, f"error:{e}"


def http_get(url: str, timeout: float = 5.0):
    if HAVE_REQUESTS:
        try:
            r = requests.get(url, timeout=timeout)
            return r.status_code, r.text
        except Exception as e:
            return None, f"error:{e}"
    else:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            return None, f"error:{e}"


def log_result(label: str, status, text: str):
    snippet = (text or "")[:140].replace("\n", " ")
    if status is None:
        print(color(f"  [{label}] NO BACKEND — is uvicorn running? ({text})", C_RED))
    elif 200 <= status < 300:
        print(color(f"  [{label}] {status} OK  ", C_GREEN) + snippet)
    elif status in (401, 403):
        print(color(f"  [{label}] {status} AUTH FAIL  ", C_RED) + snippet)
    elif status == 400:
        print(color(f"  [{label}] {status} BAD REQUEST  ", C_RED) + snippet)
    else:
        print(color(f"  [{label}] {status}  ", C_YELLOW) + snippet)


# ---------- Payload builders ----------
def build_telemetry() -> dict:
    global telemetry_counter
    with state_lock:
        telemetry_counter += 1
        mid = telemetry_counter
        lat = current_lat
        lng = current_lng
        bat = current_battery
        sos = current_sos_status
    return {
        "device_id": DEVICE_ID,
        "message_id": f"MSG-{DEVICE_ID}-{mid}",
        "timestamp": iso_now(),
        "latitude": round(lat, 6),
        "longitude": round(lng, 6),
        "battery": bat,
        "sos_status": sos,
        "speed": 24.4,
        "heading": 180,
        "network": "WIFI",
    }


def build_event_sos_button() -> dict:
    global event_counter
    with state_lock:
        event_counter += 1
        eid = event_counter
        lat = current_lat
        lng = current_lng
        bat = max(0, current_battery - 1)
    return {
        "event_id": f"EVT-{DEVICE_ID}-{eid}",
        "device_id": DEVICE_ID,
        "type": "SOS",
        "sos_status": "TRIGGERED",
        "trigger": "BUTTON",
        "timestamp": iso_now(),
        "latitude": round(lat, 6),
        "longitude": round(lng, 6),
        "battery": bat,
    }


def build_event_sos_loud() -> dict:
    global event_counter
    with state_lock:
        event_counter += 1
        eid = event_counter
        lat = current_lat
        lng = current_lng
        bat = max(0, current_battery - 1)
    return {
        "event_id": f"EVT-{DEVICE_ID}-{eid}",
        "device_id": DEVICE_ID,
        "type": "SOS",
        "sos_status": "TRIGGERED",
        "trigger": "LOUD_SOUND",
        "timestamp": iso_now(),
        "latitude": round(lat, 6),
        "longitude": round(lng, 6),
        "battery": bat,
        "metadata": {"sound_level": random.randint(75, 95)},
    }


def build_event_warning() -> dict:
    global event_counter
    with state_lock:
        event_counter += 1
        eid = event_counter
    return {
        "event_id": f"EVT-{DEVICE_ID}-{eid}",
        "device_id": DEVICE_ID,
        "type": "WARNING",
        "sos_status": "WARNING",
        "warning_type": "RASH_DRIVING",
        "timestamp": iso_now(),
    }


# ---------- Background telemetry thread ----------
def telemetry_loop(token: str):
    global current_battery, telemetry_tick_count
    headers = auth_headers(token)
    url = f"{BASE_URL}/api/v1/telemetry"
    while not shutdown_flag.is_set():
        body = build_telemetry()
        status, text = http_post(url, body, headers)
        log_result(f"TELEMETRY #{body['message_id'].split('-')[-1]}", status, text)

        # Battery drain: 1% every 30 telemetry packets, floor at 20
        with state_lock:
            telemetry_tick_count += 1
            if telemetry_tick_count % 30 == 0 and current_battery > 20:
                current_battery -= 1

        # Sleep in small slices so Ctrl+C / quit is responsive
        for _ in range(TELEMETRY_INTERVAL_S * 10):
            if shutdown_flag.is_set():
                return
            time.sleep(0.1)


# ---------- Keyboard input (cross-platform) ----------
def read_key() -> str:
    if IS_WINDOWS:
        ch = msvcrt.getch()
        try:
            return ch.decode("utf-8", errors="replace").lower()
        except Exception:
            return ""
    else:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch.lower()


# ---------- Help menu ----------
def print_help():
    print()
    print(color("================ ViGo Device Simulator ================", C_BOLD + C_CYAN))
    print(color(" Keys:", C_BOLD))
    print("   n   Set sos_status NORMAL (telemetry resumes clean)")
    print(color("   s   SOS: BUTTON press event (demo-critical)", C_RED))
    print(color("   l   SOS: LOUD_SOUND event (clap trigger)", C_RED))
    print(color("   w   WARNING: RASH_DRIVING event", C_YELLOW))
    print("   m   Move GPS ~50m (random nudge)")
    print("   b   Drop battery by 10%")
    print("   h   Show this help menu")
    print("   q   Quit")
    print(color("========================================================", C_BOLD + C_CYAN))
    print()


# ---------- Main ----------
def main():
    global current_lat, current_lng, current_battery, current_sos_status

    print(color("ViGo Simulator starting...", C_BOLD + C_CYAN))
    token = load_device_token()
    print(f"  Loaded DEVICE_TOKEN from {ENV_PATH}")
    print(f"  Backend: {BASE_URL}")
    print(f"  Device:  {DEVICE_ID}")
    if not HAVE_REQUESTS:
        print(color("  (requests not installed — using urllib fallback)", C_YELLOW))

    # Health check
    status, text = http_get(f"{BASE_URL}/health", timeout=3.0)
    if status is None or status >= 400:
        print(color("ERROR: Backend health check failed.", C_RED))
        print(color("Start it first:  python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000", C_RED))
        print(f"  (status={status}, resp={text[:200] if text else ''})")
        sys.exit(2)
    print(color(f"  Health OK: {text[:120]}", C_GREEN))

    print_help()

    # Start telemetry thread
    t = threading.Thread(target=telemetry_loop, args=(token,), daemon=True)
    t.start()

    headers = auth_headers(token)
    tel_url = f"{BASE_URL}/api/v1/telemetry"  # noqa: F841
    evt_url = f"{BASE_URL}/api/v1/events"

    try:
        while True:
            k = read_key()
            if k == "q":
                print(color("Quitting...", C_YELLOW))
                break
            elif k in ("h", "?"):
                print_help()
            elif k == "n":
                with state_lock:
                    current_sos_status = "NORMAL"
                print(color("Setting NORMAL", C_GREEN))
            elif k == "s":
                print(color("-> SOS BUTTON", C_RED + C_BOLD))
                body = build_event_sos_button()
                status, text = http_post(evt_url, body, headers)
                log_result(f"EVENT {body['event_id']}", status, text)
                with state_lock:
                    current_sos_status = "TRIGGERED"
            elif k == "l":
                print(color("-> SOS LOUD_SOUND", C_RED + C_BOLD))
                body = build_event_sos_loud()
                status, text = http_post(evt_url, body, headers)
                log_result(f"EVENT {body['event_id']}", status, text)
                with state_lock:
                    current_sos_status = "TRIGGERED"
            elif k == "w":
                print(color("-> WARNING RASH_DRIVING", C_YELLOW + C_BOLD))
                body = build_event_warning()
                status, text = http_post(evt_url, body, headers)
                log_result(f"EVENT {body['event_id']}", status, text)
            elif k == "m":
                dlat = random.uniform(-0.0005, 0.0005)
                dlng = random.uniform(-0.0005, 0.0005)
                with state_lock:
                    current_lat += dlat
                    current_lng += dlng
                    nl, ng = current_lat, current_lng
                print(color(f"GPS nudged -> {nl:.6f}, {ng:.6f}", C_CYAN))
            elif k == "b":
                with state_lock:
                    current_battery = max(0, current_battery - 10)
                    bat = current_battery
                print(color(f"Battery -> {bat}%", C_YELLOW))
            elif k in ("\x03", "\x04"):  # Ctrl+C / Ctrl+D on POSIX raw mode
                break
            else:
                # ignore stray keys
                pass
    except KeyboardInterrupt:
        print(color("\nInterrupted.", C_YELLOW))
    finally:
        shutdown_flag.set()
        t.join(timeout=2.0)
        print(color("Simulator stopped.", C_CYAN))


if __name__ == "__main__":
    main()
