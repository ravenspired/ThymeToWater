#!/usr/bin/env python3
"""Plant moisture dashboard: polls a Pico sensor node, logs to SQLite, serves a live UI.

Run:  pip install flask && python app.py   ->  http://<this-machine>:5000
"""
import json
import os
import sqlite3
import threading
import time
import urllib.request

from flask import Flask, jsonify, render_template, request

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "plantdata.db")
CFG_PATH = os.path.join(BASE, "config.json")

LOG_INTERVAL_S = 60    # how often a reading is saved to the datalog
LIVE_INTERVAL_S = 5    # how often the node is polled to keep the live view fresh
HTTP_TIMEOUT_S = 4
RANGES = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}

app = Flask(__name__)
cfg_lock = threading.Lock()
STATE = {"data": None, "last_ok": 0.0, "error": None}


# ---------- config ----------
def load_cfg():
    try:
        with open(CFG_PATH) as f:
            c = json.load(f)
    except Exception:
        c = {}
    c.setdefault("node_url", "")
    c.setdefault("plants", [])
    c.setdefault("next_id", 1)
    return c


def save_cfg():
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(CFG, f, indent=2)
    os.replace(tmp, CFG_PATH)


CFG = load_cfg()


def norm_url(s):
    s = (s or "").strip()
    if s and not s.startswith(("http://", "https://")):
        s = "http://" + s
    return s


# ---------- database ----------
def db_run(sql, args=(), many=None):
    c = sqlite3.connect(DB_PATH, timeout=10)
    try:
        if many is not None:
            c.executemany(sql, many)
            c.commit()
            return []
        rows = c.execute(sql, args).fetchall()
        c.commit()
        return rows
    finally:
        c.close()


def init_db():
    db_run("PRAGMA journal_mode=WAL")
    db_run("CREATE TABLE IF NOT EXISTS samples(ts INTEGER NOT NULL, k TEXT NOT NULL, v REAL NOT NULL)")
    db_run("CREATE INDEX IF NOT EXISTS idx_k_ts ON samples(k, ts)")


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def flatten(d):
    """Node JSON -> {key: number}. Channels become c:<ch>:raw / c:<ch>:volts, the rest n:<name>."""
    out = {}
    for k, v in d.items():
        if k == "channels" and isinstance(v, dict):
            for ch, o in v.items():
                for f in ("raw", "volts"):
                    if isinstance(o, dict) and is_num(o.get(f)):
                        out[f"c:{ch}:{f}"] = o[f]
        elif k == "stats" and isinstance(v, dict):
            for s, x in v.items():
                if is_num(x):
                    out[f"n:stats_{s}"] = x
        elif is_num(v):
            out[f"n:{k}"] = v
    return out


# ---------- node polling ----------
def fetch_node(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
        return json.loads(r.read().decode())


def poller():
    last_log = 0.0
    while True:
        url = CFG["node_url"]
        if url:
            now = time.time()
            try:
                d = fetch_node(url)
                STATE.update(data=d, last_ok=now, error=None)
                if now - last_log >= LOG_INTERVAL_S - 0.5:
                    ts = int(now)
                    db_run("INSERT INTO samples VALUES(?,?,?)",
                           many=[(ts, k, v) for k, v in flatten(d).items()])
                    last_log = now
            except Exception as e:
                STATE["error"] = (str(e) or e.__class__.__name__)[:120]
        time.sleep(LIVE_INTERVAL_S)


# ---------- moisture calibration ----------
def to_pct(raw, a20, a80):
    """Straight line through (a20 -> 20%) and (a80 -> 80%), clamped to 0..100."""
    if raw is None or a20 == a80:
        return None
    return max(0.0, min(100.0, 20 + (raw - a20) * 60.0 / (a80 - a20)))


def parse_plant(j, pid=None):
    name = str(j.get("name", "")).strip()[:40]
    if not name:
        return None, "Give the plant a name."
    ch = str(j.get("channel", "")).strip()
    if not ch:
        return None, "Pick a sensor."
    if any(p["channel"] == ch and p["id"] != pid for p in CFG["plants"]):
        return None, "That sensor is already assigned to another plant."
    shelf = j.get("shelf") if j.get("shelf") in ("top", "lower") else "top"
    try:
        a20, a80 = float(j.get("adc20")), float(j.get("adc80"))
    except (TypeError, ValueError):
        return None, "Enter numbers for the 20% and 80% readings."
    if a20 == a80:
        return None, "The 20% and 80% readings must be different."
    try:
        pos = int(j.get("pos"))
    except (TypeError, ValueError):
        pos = max([p["pos"] for p in CFG["plants"] if p["shelf"] == shelf and p["id"] != pid], default=0) + 1
    return {"name": name, "channel": ch, "shelf": shelf, "pos": pos, "adc20": a20, "adc80": a80}, None


# ---------- routes ----------
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def api_state():
    now = time.time()
    d = STATE["data"]
    ch = (d or {}).get("channels") or {}
    plants = []
    for p in sorted(CFG["plants"], key=lambda p: (p["shelf"] != "top", p["pos"], p["id"])):
        o = ch.get(p["channel"]) or {}
        raw = o.get("raw")
        plants.append({**p, "raw": raw, "volts": o.get("volts"), "pct": to_pct(raw, p["adc20"], p["adc80"])})
    used = {p["channel"] for p in CFG["plants"]}
    free = sorted((c for c in ch if c not in used), key=lambda c: (not c.isdigit(), int(c) if c.isdigit() else 0, c))
    return jsonify(
        server_time=now,
        node_url=CFG["node_url"],
        connected=bool(STATE["last_ok"]) and now - STATE["last_ok"] < max(15, LIVE_INTERVAL_S * 3),
        last_ok=STATE["last_ok"],
        age_s=(now - STATE["last_ok"]) if STATE["last_ok"] else None,
        error=STATE["error"],
        node=d,
        plants=plants,
        unassigned=free,
    )


@app.post("/api/node")
def api_node():
    url = norm_url((request.get_json(silent=True) or {}).get("url"))
    with cfg_lock:
        CFG["node_url"] = url
        save_cfg()
    STATE.update(data=None, last_ok=0.0, error=None)
    if not url:
        return jsonify(ok=True, message="Node disconnected.")
    try:
        d = fetch_node(url)
        STATE.update(data=d, last_ok=time.time(), error=None)
        n = len(d.get("channels") or {})
        return jsonify(ok=True, message=f"Connected to {d.get('node_id', 'node')}, {n} sensors reporting.")
    except Exception as e:
        STATE["error"] = (str(e) or e.__class__.__name__)[:120]
        return jsonify(ok=False, error=f"Saved, but could not reach the node: {STATE['error']}")


@app.post("/api/plants")
def api_add_plant():
    with cfg_lock:
        p, err = parse_plant(request.get_json(silent=True) or {})
        if err:
            return jsonify(error=err), 400
        p["id"] = CFG["next_id"]
        CFG["next_id"] += 1
        CFG["plants"].append(p)
        save_cfg()
    return jsonify(p)


@app.put("/api/plants/<int:pid>")
def api_edit_plant(pid):
    with cfg_lock:
        cur = next((x for x in CFG["plants"] if x["id"] == pid), None)
        if not cur:
            return jsonify(error="Plant not found."), 404
        p, err = parse_plant(request.get_json(silent=True) or {}, pid)
        if err:
            return jsonify(error=err), 400
        cur.update(p)
        save_cfg()
    return jsonify(cur)


@app.delete("/api/plants/<int:pid>")
def api_del_plant(pid):
    with cfg_lock:
        CFG["plants"] = [x for x in CFG["plants"] if x["id"] != pid]
        save_cfg()
    return jsonify(ok=True)


@app.get("/api/history")
def api_history():
    span = RANGES.get(request.args.get("range", "24h"), 86400)
    keys = [k for k in request.args.get("keys", "").split(",") if k][:60]
    bucket = max(60, span // 240)
    since = int(time.time()) - span
    out = {}
    for k in keys:
        rows = db_run("SELECT (ts/?)*? AS b, AVG(v) FROM samples WHERE k=? AND ts>=? GROUP BY b ORDER BY b",
                      (bucket, bucket, k, since))
        out[k] = [[b + bucket // 2, v] for b, v in rows]
    return jsonify(out)


if __name__ == "__main__":
    init_db()
    threading.Thread(target=poller, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, threaded=True)