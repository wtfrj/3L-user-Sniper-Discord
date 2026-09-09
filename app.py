import requests
import random
import string
import time
import threading
from datetime import datetime
import urllib3
from collections import deque
from flask import Flask, jsonify, render_template, request, send_file, session, redirect, url_for
import os
import sys
from functools import wraps

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

PORT = 0000
PASSWORD = "r1"
app.secret_key = os.urandom(24)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = False

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def api_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        return render_template('login.html', error="Invalid password")
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

settings = {
    "threads": 200,
    "min_proxies": 500,
    "timeout": 4,
    "lengths": [3, 4, 5],
    "custom_base": "xric",
}
REFRESH_INTERVAL = 10  

running = False
worker_threads = []
stop_event = threading.Event()

log_buffer = deque(maxlen=300)
available_list = deque(maxlen=200)

stats = {
    "total": 0,
    "available": 0,
    "taken": 0,
    "unavailable": 0,
}
stats_lock = threading.Lock()

tried_set = set()
tried_lock = threading.Lock()

proxy_list = []
proxy_lock = threading.Lock()
last_refresh_attempt = 0

custom_allowed_lengths = None

PROXY_SOURCES = [
    "https://api.proxyscrape.com/?request=displayproxies&proxytype=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://www.proxy-list.download/api/v1/get?type=http",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
]

ALLOWED_CHARS = string.ascii_lowercase + string.digits + "_."
API_URL = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"

TOTAL_COUNTS = {
    3: 54797,
    4: 2080880,
    5: 79020049
}

total_possible = sum(TOTAL_COUNTS[l] for l in settings["lengths"])
last_rate = 0.0

def fetch_proxies_from_url(url, proxy=None):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(url, timeout=15, proxies=proxies)
        if resp.status_code == 200:
            raw = resp.text.strip()
            return [p.strip() for p in raw.replace('\r\n', '\n').split('\n') if p.strip() and ':' in p]
        return []
    except Exception:
        return []

def fetch_free_proxies(use_proxy=None):
    all_proxies = []
    for url in PROXY_SOURCES:
        log(f"   Fetching from {url.split('?')[0]}..." + (f" via proxy {use_proxy}" if use_proxy else ""))
        proxies = fetch_proxies_from_url(url, proxy=use_proxy)
        if proxies:
            log(f"   Got {len(proxies)} proxies from this source.")
            all_proxies.extend(proxies)
        time.sleep(1)
    unique = list(dict.fromkeys(all_proxies))
    return unique

def refresh_proxy_pool():
    global proxy_list, last_refresh_attempt
    with proxy_lock:
        if len(proxy_list) >= settings["min_proxies"]:
            return
        now = time.time()
        if now - last_refresh_attempt < REFRESH_INTERVAL:
            return
        last_refresh_attempt = now
        fetch_proxy = random.choice(proxy_list) if proxy_list else None

    log(f"🔄 Proxy pool low ({len(proxy_list)}/{settings['min_proxies']}), fetching fresh proxies...")
    new_proxies = fetch_free_proxies(use_proxy=fetch_proxy)
    if new_proxies:
        with proxy_lock:
            existing = set(proxy_list)
            unique_new = [p for p in new_proxies if p not in existing]
            proxy_list.extend(unique_new)
            log(f"✅ Added {len(unique_new)} new proxies. Total: {len(proxy_list)}")
    else:
        log(f"⚠️ Could not fetch proxies. Will retry in {REFRESH_INTERVAL}s.")

def get_proxy():
    refresh_proxy_pool()
    with proxy_lock:
        if not proxy_list:
            return None
        p = random.choice(proxy_list)
        proxy_list.remove(p)
        return p

def return_proxy(p):
    if p:
        with proxy_lock:
            proxy_list.append(p)

def build_proxy_dict(p):
    if not p:
        return None
    return {"http": f"http://{p}", "https": f"http://{p}"}

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    log_buffer.append(line)

def get_filename(length, category):
    return f"{category}_{length}.txt"

def load_tried_usernames():
    global tried_set
    for length in settings["lengths"]:
        for cat in ("available", "taken", "unavailable"):
            try:
                with open(get_filename(length, cat), "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.strip().split("] ")
                        if len(parts) == 2:
                            tried_set.add(parts[1])
            except FileNotFoundError:
                continue
    log(f"Loaded {len(tried_set)} previously checked usernames")

def check_username(username, session):
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/register",
        "X-Discord-Locale": "ko",
        "X-Discord-Timezone": "Asia/Seoul"
    }
    payload = {"username": username}
    try:
        resp = session.post(API_URL, json=payload, headers=headers, timeout=settings["timeout"])
        if resp.status_code == 200:
            data = resp.json()
            if "taken" in data:
                return (not data["taken"]), "available" if not data["taken"] else "taken"
            return False, "unavailable"
        elif resp.status_code == 429:
            return False, "rate_limit"
        else:
            return False, "unavailable"
    except Exception:
        return False, "unavailable"

def save_result(username, length, result):
    with open(get_filename(length, result), "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {username}\n")
    if result == "available":
        available_list.append({"username": username, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

def generate_random_username(length):
    while True:
        u = ''.join(random.choice(ALLOWED_CHARS) for _ in range(length))
        if ".." in u:
            continue
        with tried_lock:
            if u in tried_set:
                continue
            tried_set.add(u)
            return u

LEET_MAP = {
    'a': ['4'],
    'e': ['3'],
    'i': ['1'],
    'o': ['0'],
    's': ['5'],
    't': ['7', '1'],
    'z': ['2'],
    'g': ['6', '9'],
    'b': ['8'],
    'l': ['1'],
    'r': ['2']
}

def generate_candidates(base):
    base = base.lower()
    candidates = set()
    for i, ch in enumerate(base):
        if ch in LEET_MAP:
            for repl in LEET_MAP[ch]:
                cand = base[:i] + repl + base[i+1:]
                if cand not in candidates:
                    candidates.add(cand)
                    yield cand
    indices = [i for i, ch in enumerate(base) if ch in LEET_MAP]
    for i1 in range(len(indices)):
        i = indices[i1]
        for repl1 in LEET_MAP[base[i]]:
            cand_list = list(base)
            cand_list[i] = repl1
            for i2 in range(i1+1, len(indices)):
                j = indices[i2]
                for repl2 in LEET_MAP[base[j]]:
                    cand_list2 = cand_list.copy()
                    cand_list2[j] = repl2
                    cand = ''.join(cand_list2)
                    if cand not in candidates:
                        candidates.add(cand)
                        yield cand
    for i, ch in enumerate(base):
        if ch.isalpha():
            for digit in '0123456789':
                cand = base[:i] + digit + base[i+1:]
                if cand not in candidates:
                    candidates.add(cand)
                    yield cand
    for pos in range(len(base)+1):
        for digit in '0123456789':
            cand = base[:pos] + digit + base[pos:]
            if cand not in candidates:
                candidates.add(cand)
                yield cand
    for pos in range(len(base)+1):
        for punct in ('_', '.'):
            cand = base[:pos] + punct + base[pos:]
            if cand not in candidates:
                candidates.add(cand)
                yield cand
    for n in range(1, 100):
        cand = base + str(n)
        if cand not in candidates:
            candidates.add(cand)
            yield cand
        cand = str(n) + base
        if cand not in candidates:
            candidates.add(cand)
            yield cand
    for punct in ('_', '.'):
        cand = base + punct
        if cand not in candidates:
            candidates.add(cand)
            yield cand
        cand = punct + base
        if cand not in candidates:
            candidates.add(cand)
            yield cand
    for i in range(len(base)):
        for j in range(i+1, len(base)):
            if base[i].isalpha() and base[j].isalpha():
                for d1 in '0123456789':
                    for d2 in '0123456789':
                        cand_list = list(base)
                        cand_list[i] = d1
                        cand_list[j] = d2
                        cand = ''.join(cand_list)
                        if cand not in candidates:
                            candidates.add(cand)
                            yield cand
    for pos1 in range(len(base)+1):
        for pos2 in range(pos1, len(base)+2):
            for digit1 in '0123456789':
                for digit2 in '0123456789':
                    cand = base[:pos1] + digit1 + base[pos1:pos2] + digit2 + base[pos2:]
                    if cand not in candidates:
                        candidates.add(cand)
                        yield cand
            for punct1 in ('_', '.'):
                for punct2 in ('_', '.'):
                    cand = base[:pos1] + punct1 + base[pos1:pos2] + punct2 + base[pos2:]
                    if cand not in candidates:
                        candidates.add(cand)
                        yield cand
    for i, ch in enumerate(base):
        if ch in LEET_MAP:
            for repl in LEET_MAP[ch]:
                for n in range(1, 100):
                    cand = base[:i] + repl + base[i+1:] + str(n)
                    if cand not in candidates:
                        candidates.add(cand)
                        yield cand
                    cand = str(n) + base[:i] + repl + base[i+1:]
                    if cand not in candidates:
                        candidates.add(cand)
                        yield cand
    for letter in string.ascii_lowercase:
        cand = base + letter
        if cand not in candidates:
            candidates.add(cand)
            yield cand
        cand = letter + base
        if cand not in candidates:
            candidates.add(cand)
            yield cand

custom_gen = None

def init_custom_gen(base):
    global custom_gen
    custom_gen = generate_candidates(base)

def get_next_custom():
    global custom_gen, custom_allowed_lengths
    with tried_lock:
        try:
            cand = next(custom_gen)
            while cand in tried_set or (custom_allowed_lengths is not None and len(cand) not in custom_allowed_lengths):
                cand = next(custom_gen)
            tried_set.add(cand)
            return cand
        except StopIteration:
            return None

def worker_random(thread_id):
    global running, stats, total_possible
    while running and not stop_event.is_set():
        p = get_proxy()
        if not p:
            time.sleep(0.5)
            continue
        session = requests.Session()
        session.proxies = build_proxy_dict(p)
        session.verify = False
        length = random.choice(settings["lengths"])
        username = generate_random_username(length)
        is_avail, result = check_username(username, session)
        if result == "rate_limit":
            time.sleep(1)
            continue
        elif result in ("available", "taken"):
            return_proxy(p)
        with stats_lock:
            stats["total"] += 1
            if result == "available":
                stats["available"] += 1
                save_result(username, length, "available")
                log(f"✅ Available ({length}) {username}")
            elif result == "taken":
                stats["taken"] += 1
                save_result(username, length, "taken")
                log(f"❌ Taken ({length}) {username}")
            else:
                stats["unavailable"] += 1
                save_result(username, length, "unavailable")
                log(f"❌ Unavailable ({length}) {username}")

found_custom = None

def worker_custom(thread_id):
    global running, stats, found_custom
    while running and not stop_event.is_set() and found_custom is None:
        p = get_proxy()
        if not p:
            time.sleep(0.5)
            continue
        session = requests.Session()
        session.proxies = build_proxy_dict(p)
        session.verify = False
        username = get_next_custom()
        if username is None:
            log("No more custom candidates.")
            break
        is_avail, result = check_username(username, session)
        if result == "rate_limit":
            time.sleep(1)
            continue
        elif result in ("available", "taken"):
            return_proxy(p)
        with stats_lock:
            stats["total"] += 1
            if result == "available":
                stats["available"] += 1
                found_custom = username
                with open("custom_found.txt", "a") as f:
                    f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {username}\n")
                available_list.append({"username": username, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                log(f"✅ CUSTOM FOUND: {username}")
                stop_event.set()
            elif result == "taken":
                stats["taken"] += 1
                with open("custom_taken.txt", "a") as f:
                    f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {username}\n")
                log(f"❌ Custom Taken {username}")
            else:
                stats["unavailable"] += 1
                with open("custom_unavailable.txt", "a") as f:
                    f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {username}\n")
                log(f"❌ Custom Unavailable {username}")
        with open("custom_checked.txt", "a") as f:
            f.write(username + "\n")

def monitor_thread_func():
    global last_rate
    last_3min = time.time()
    last_count = 0
    while running:
        time.sleep(2)
        with stats_lock:
            now = time.time()
            if now - last_3min >= 180:
                delta = stats["total"] - last_count
                rate = delta / 3.0
                last_rate = rate
                log(f"⏱️ Last 3 min: {delta} checks ({rate:.1f}/min)")
                last_3min = now
                last_count = stats["total"]

def start_random():
    global running, stop_event, worker_threads, found_custom, total_possible
    stop_event.clear()
    running = True
    found_custom = None
    total_possible = sum(TOTAL_COUNTS[l] for l in settings["lengths"])
    load_tried_usernames()
    log(f"Starting random scan. Total possible: {total_possible}")
    for i in range(settings["threads"]):
        t = threading.Thread(target=worker_random, args=(i+1,), daemon=True)
        t.start()
        worker_threads.append(t)
    t_mon = threading.Thread(target=monitor_thread_func, daemon=True)
    t_mon.start()
    return {"status": "started", "mode": "random"}

def start_custom(base, allowed_lengths=None):
    global running, stop_event, worker_threads, found_custom, custom_gen, custom_allowed_lengths
    stop_event.clear()
    running = True
    found_custom = None
    custom_allowed_lengths = set(allowed_lengths) if allowed_lengths else None
    init_custom_gen(base)
    log(f"Starting custom search for '{base}' with lengths {allowed_lengths if allowed_lengths else 'any'}...")
    for i in range(settings["threads"]):
        t = threading.Thread(target=worker_custom, args=(i+1,), daemon=True)
        t.start()
        worker_threads.append(t)
    t_mon = threading.Thread(target=monitor_thread_func, daemon=True)
    t_mon.start()
    return {"status": "started", "mode": "custom"}

def stop_all():
    global running, worker_threads, found_custom, custom_allowed_lengths
    running = False
    stop_event.set()
    for t in worker_threads:
        t.join(timeout=1)
    worker_threads.clear()
    custom_allowed_lengths = None
    log("Stopped all threads.")
    return {"status": "stopped"}

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/stats')
@api_login_required
def api_stats():
    with stats_lock:
        return jsonify({
            "total": stats["total"],
            "available": stats["available"],
            "taken": stats["taken"],
            "unavailable": stats["unavailable"],
            "available_list": list(available_list)
        })

@app.route('/api/logs')
@api_login_required
def api_logs():
    return jsonify(list(log_buffer))

@app.route('/api/files')
@api_login_required
def api_files():
    files = [f for f in os.listdir('.') if f.endswith('.txt') and f != 'requirements.txt']
    return jsonify({"files": files})

@app.route('/api/file/<filename>')
@api_login_required
def api_file_content(filename):
    if not filename.endswith('.txt') or filename == 'requirements.txt':
        return jsonify({"error": "Invalid file"}), 400
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({"content": content})
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404

@app.route('/download/<filename>')
@api_login_required
def download_file(filename):
    return send_file(filename, as_attachment=True)

@app.route('/api/settings', methods=['POST'])
@api_login_required
def api_settings():
    data = request.json
    with stats_lock:
        settings["threads"] = data.get("threads", 12)
        settings["min_proxies"] = data.get("min_proxies", 50)
        settings["timeout"] = data.get("timeout", 4)
        settings["lengths"] = data.get("lengths", [3,4,5])
        global total_possible
        total_possible = sum(TOTAL_COUNTS[l] for l in settings["lengths"])
    return jsonify({"status": "ok"})

@app.route('/api/start/random', methods=['POST'])
@api_login_required
def api_start_random():
    global running
    if running:
        return jsonify({"status": "already running"})
    if not proxy_list:
        log("Initializing proxy pool before start...")
        refresh_proxy_pool()
    start_random()
    return jsonify({"status": "started", "mode": "random"})

@app.route('/api/start/custom', methods=['POST'])
@api_login_required
def api_start_custom():
    global running
    if running:
        return jsonify({"status": "already running"})
    data = request.json
    base = data.get("base", "xric")
    lengths = data.get("lengths", [])
    if not proxy_list:
        log("Initializing proxy pool before start...")
        refresh_proxy_pool()
    start_custom(base, lengths if lengths else None)
    return jsonify({"status": "started", "mode": "custom"})

@app.route('/api/stop', methods=['POST'])
@api_login_required
def api_stop():
    stop_all()
    return jsonify({"status": "stopped"})

@app.route('/api/progress')
@api_login_required
def api_progress():
    with stats_lock:
        checked = stats["total"]
        total = total_possible
        rate = last_rate
        if rate > 0:
            eta = (total - checked) / rate * 60
        else:
            eta = -1
        return jsonify({
            "total_possible": total,
            "checked": checked,
            "rate_per_min": rate,
            "eta_seconds": eta if eta > 0 else -1
        })

@app.route('/ping')
def ping():
    return "pong"

if __name__ == "__main__":
    log("Fetching initial proxy list directly...")
    init_proxies = fetch_free_proxies(use_proxy=None)
    if init_proxies:
        with proxy_lock:
            proxy_list.extend(init_proxies)
            random.shuffle(proxy_list)
        log(f"✅ Loaded {len(proxy_list)} proxies.")
    else:
        log("⚠️ No proxies fetched. Will retry on demand.")
    try:
        with open("/tmp/flask.pid", "w") as f:
            f.write(str(os.getpid()))
        print("PID written")
    except Exception as e:
        print(f"PID error: {e}")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
