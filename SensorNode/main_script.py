"""
main.py - Pico W sensor node with an HTTP JSON API.

Files needed on the Pico:
    AHT30.py, CD4051.py, main.py, mixer.json (made by mapper.py)
    wifi.json is created automatically on first run.

GET http://<pico-ip>/  (or /data)  ->  JSON with 16 analog channels,
AHT30 temperature/humidity and the Pico CPU temperature.
"""
import gc
import json
import socket
import time

import network
from machine import ADC, Pin

from AHT30 import Aht30
from CD4051 import Cd4051

import math, os, machine

BOOT_T = time.time()
STATS = {"requests": 0, "wifi_connects": 0, "aht_errors": 0}

def dew_point(t, rh):
    if t is None or rh is None or rh <= 0:
        return None
    a, b = 17.62, 243.12
    g = math.log(rh / 100) + a * t / (b + t)
    return b * g / (a - g)


# ---------------- user settings ----------------
NODE_ID = "node-01"      # change per microcontroller
HTTP_PORT = 80

WIFI_FILE = "wifi.json"
MIXER_FILE = "mixer.json"

NUM_SAMPLES = 10         # samples per datapoint
DROP = 2                 # farthest-from-average samples to discard
NUM_CHANNELS = 16
# -----------------------------------------------

# Pico W: force the on-board SMPS into PWM mode to reduce ADC noise.
try:
    Pin("WL_GPIO1", Pin.OUT).value(1)
except Exception:
    pass

muxes = (
    Cd4051(pin_a=2, pin_b=3, pin_c=4, adc_pin=28),      # raw channels 0-7
    Cd4051(pin_a=19, pin_b=20, pin_c=21, adc_pin=27),   # raw channels 8-15
)
cpu_adc = ADC(4)
_aht = None
wlan = network.WLAN(network.STA_IF)


# ---------------- measurement helpers ----------------
def trimmed_mean(samples, drop=DROP):
    """Average, drop the `drop` samples farthest from that average, re-average."""
    n = len(samples)
    if n == 0:
        return None
    if n > drop + 2:
        mean = sum(samples) / n
        ranked = sorted(samples, key=lambda x: abs(x - mean))
        samples = ranked[:-drop]
    return sum(samples) / len(samples)


def get_aht():
    global _aht
    if _aht is None:
        try:
            _aht = Aht30(sda_pin=14, scl_pin=15, i2c_id=1)
        except OSError as e:
            print("AHT30 init failed:", e)
    return _aht


def read_aht():
    sensor = get_aht()
    if sensor is None:
        return None, None
    temps, hums = [], []
    for _ in range(NUM_SAMPLES):
        try:
            t, h = sensor.read()
            temps.append(t)
            hums.append(h)
        except OSError:
            STATS["aht_errors"] += 1
    if not temps:
        try:
            sensor.soft_reset()
        except OSError:
            pass
    return trimmed_mean(temps), trimmed_mean(hums)


def sample_channel(idx):
    """Trimmed-mean raw ADC value (0-65535) for raw channel idx (0-15)."""
    mux = muxes[idx >> 3]
    ch = idx & 7
    mux.select_channel(ch)
    mux.adc.read_u16()  # discard first read after switching
    return trimmed_mean([mux.read_channel(ch) for _ in range(NUM_SAMPLES)])


def cpu_temp_c():
    v = cpu_adc.read_u16() * 3.3 / 65535
    return 27 - (v - 0.706) / 0.001721


def _r(x, digits):
    return None if x is None else round(x, digits)


def load_mixer():
    """mixer.json maps logical channel (1-16) -> raw channel index (0-15)."""
    try:
        with open(MIXER_FILE) as f:
            data = json.load(f)
        mixer = {int(k): int(v) for k, v in data.items()}
        print("Loaded {} ({} channels mapped)".format(MIXER_FILE, len(mixer)))
        return mixer
    except (OSError, ValueError):
        print("No valid {}; using 1-16 -> raw 0-15. Run mapper.py.".format(MIXER_FILE))
        return {n: n - 1 for n in range(1, NUM_CHANNELS + 1)}


MIXER = load_mixer()


def collect_data():
    temp, hum = read_aht()
    channels = {}
    t0 = time.ticks_ms()
    for n in range(1, NUM_CHANNELS + 1):
        idx = MIXER.get(n)
        if idx is None:
            channels[str(n)] = None
            continue
        raw = sample_channel(idx)
        channels[str(n)] = {"raw": round(raw, 1), "volts": round(raw * 3.3 / 65535, 4)}
    try:
        rssi = wlan.status("rssi")
    except Exception:
        rssi = None
    return {
        "node_id": NODE_ID,
        "temperature_c": _r(temp, 2),
        "humidity_pct": _r(hum, 2),
        "cpu_temp_c": round(cpu_temp_c(), 2),
        "wifi_rssi_dbm": rssi,
        "channels": channels,
        "dew_point_c": _r(dew_point(temp, hum), 2),
        "uptime_s": time.time() - BOOT_T,
        "reset_cause": machine.reset_cause(),   # 1=power-on, 3=watchdog
        "free_mem_b": gc.mem_free(),
        "ip": wlan.ifconfig()[0],
        "fw": os.uname().release,
        "collect_ms": time.ticks_diff(time.ticks_ms(), t0),
        "stats": STATS,
    }


# ---------------- WiFi ----------------
def load_wifi():
    try:
        with open(WIFI_FILE) as f:
            d = json.load(f)
        return d["ssid"], d["password"]
    except (OSError, ValueError, KeyError):
        return None


def prompt_wifi():
    ssid = input("WiFi SSID: ").strip()
    password = input("WiFi password: ").strip()
    with open(WIFI_FILE, "w") as f:
        json.dump({"ssid": ssid, "password": password}, f)
    print("Saved to", WIFI_FILE)
    return ssid, password


def wifi_connect(ssid, password, timeout_s=20):
    wlan.active(True)
    try:
        wlan.config(pm=network.WLAN.PM_NONE)  # no power save: more reliable server
    except Exception:
        pass
    if wlan.isconnected():
        return True
    wlan.connect(ssid, password)
    t0 = time.ticks_ms()
    while not wlan.isconnected():
        if wlan.status() < 0:  # wrong password / no AP / failed
            break
        if time.ticks_diff(time.ticks_ms(), t0) > timeout_s * 1000:
            break
        time.sleep_ms(250)
    return wlan.isconnected()


# ---------------- HTTP server ----------------
def start_server():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(socket.getaddrinfo("0.0.0.0", HTTP_PORT)[0][-1])
    s.listen(2)
    s.settimeout(1)  # so the main loop can keep checking WiFi
    return s


def send(conn, status, body, ctype="application/json"):
    body = body.encode()
    hdr = ("HTTP/1.1 {}\r\nContent-Type: {}\r\nContent-Length: {}\r\n"
           "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n"
           ).format(status, ctype, len(body))
    conn.sendall(hdr.encode() + body)


def handle(conn):
    try:
        conn.settimeout(3)
        req = conn.recv(1024)
        parts = req.split(b"\r\n", 1)[0].decode().split(" ")
        method, path = parts[0], parts[1].split("?")[0]
        if method != "GET":
            send(conn, "405 Method Not Allowed", '{"error":"GET only"}')
        elif path in ("/", "/data"):
            STATS["requests"] += 1
            send(conn, "200 OK", json.dumps(collect_data()))
        else:
            send(conn, "404 Not Found", '{"error":"not found"}')
    except Exception as e:
        print("Request error:", e)
        try:
            send(conn, "500 Internal Server Error", '{"error":"server error"}')
        except Exception:
            pass
    finally:
        conn.close()
        gc.collect()


def main():
    creds = load_wifi() or prompt_wifi()
    ssid, password = creds
    srv = None
    delay = 2
    STATS["wifi_connects"] += 1

    while True:
        if not wlan.isconnected():
            if srv:
                srv.close()
                srv = None
            print("WiFi down - connecting to '{}'...".format(ssid))
            if not wifi_connect(ssid, password):
                print("Connect failed, retrying in {}s".format(delay))
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            delay = 2
            print("Connected. Node '{}' at http://{}:{}/".format(
                NODE_ID, wlan.ifconfig()[0], HTTP_PORT))

        if srv is None:
            try:
                srv = start_server()
            except OSError as e:
                print("Server start failed:", e)
                time.sleep(2)
                continue

        try:
            conn, _ = srv.accept()
        except OSError as e:
            if e.args and e.args[0] in (110, 11):  # timeout, just loop
                continue
            print("Accept error:", e)
            srv.close()
            srv = None
            continue
        handle(conn)


main()