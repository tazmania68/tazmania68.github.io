#!/usr/bin/env python3
"""Ninja Zenshin Tracker v23 · MODO SABIO DE LOS SEIS CAMINOS · IMAGENES + BLEED + ONLINE + BOT TELEGRAM.
Uso:  python zenshin_tracker_v22.py                 -> abre http://localhost:8000
      python zenshin_tracker_v22.py --poll 0.5      -> modo turbo (lee cada 0,5 s)
      python zenshin_tracker_v22.py --port 8080     -> otro puerto
Solo libreria estandar (Python 3.8+). Dejalo abierto para acumular historial.
NUEVO v22: alertas por sonido/voz/vibracion/notificaciones del navegador, Telegram,
  temas ninja (Kurama/Sharingan/Sage/Rinnegan), predicciones de ranking final,
  comparador de clanes, exportar CSV, backup/restore, atajos de teclado, filtro
  de eventos, ticker en vivo, puntaje de amenaza, polling adaptativo, logros,
  modo compacto, instalacion PWA, y widget de predicciones."""
import bisect, datetime, gzip, os, socket, ssl, sys, http.client, http.cookiejar, itertools, json, re, threading, time, traceback, urllib.error, urllib.request
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False
from urllib.parse import parse_qs, quote, urljoin, urlparse
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

URL = "https://ninjazenshin.online/?panel=clan-ranking"
POLL, PORT = 1, 8000       # segundos entre lecturas / puerto
POLLS = (0.5, 1, 2, 5, 15, 30, 60)   # velocidades de lectura permitidas (0.5 = turbo)
MEM_WORKERS = 3             # lectores de miembros en paralelo
MEMBERS_URL = ""            # plantilla con {id} o {name}; tambien se configura desde la pestana Conexion
WEBHOOK = ""                # Discord webhook (alertas al celular); tambien desde la pestana Conexion
ALERT_MIN = 100             # puntos minimos para avisar por Discord
HIST = "zenshin_history.json"
SER_FILE = "zenshin_series.json.gz"   # historial de reputacion por clan (comprimido)
START = time.time()

# ==================== v22: NUEVAS CONFIGURACIONES ====================
TELEGRAM = dict(token="", chat="")     # alertas por Telegram: {token: bot token, chat: chat id}
TG_Q, TGL = [], threading.Lock()       # cola de mensajes Telegram
SOUND = dict(on=1, voice=0, vibrate=1, notify=1, vol=0.7, min_rep=50, ticker=1)
THEME = ["kurama"]                     # kurama(naranja) · sharingan(rojo) · sage(verde) · rinnegan(morado) · uchija(oscuro)
RIVALS = []                            # clanes rivales (alertas prioritarias + sonido especial)
ACHV = dict(unlocked=[], attacks=0, losses=0, hours=0, first_ts=0)  # logros desbloqueados
PRED = {}                              # predicciones: clan -> {rep_proy, rank_proy, rate}
THREAT = {}                            # puntaje de amenaza: clan -> score, nivel
ADAPT = dict(base=1.0, cur=1.0, slow_until=0, fails=0, ok=0)        # polling adaptativo
WATCH = {"clans": {}, "players": {}}   # jugadores/clanes vigilados (alias tracking)
STREAK = {}                            # rachas de ataque: clan -> {n, start, last}

# ==================== v23: NUEVAS GLOBALES ====================
BLEED_ST = {}            # bleed acumulado por clan: {clan: {total, n, first, last}}
TG_UPD = {"offset": 0}   # offset del bot de Telegram (comandos interactivos)
DISCORD_EMBED = [True]   # usar embeds ricos en Discord
CARD_CACHE = {"png": None, "ts": 0, "type": ""}  # cache de imagen generada
ONLINE_CACHE = {"data": {}, "ts": 0}
EXTRA_THEMES = ["akatsuki", "pain", "orochimaru", "minato", "tsunade", "rocklee", "sakura", "gaara"]
TG_HELP = """Comandos del bot Ninja Zenshin:
/status - Estado del tracker y top 5
/top - Top 10 clanes por reputacion
/bleed - Clanes sangrando ahora
/online - Jugadores conectados
/attacks - Ultimos ataques
/predict - Predicciones de ranking
/card - Generar imagen del ranking
/help - Esta ayuda"""

class Row(list): rid = ""

class TableParser(HTMLParser):
    def __init__(s):
        super().__init__(); s.tables = []; s.t = s.row = s.cell = None
    def handle_starttag(s, tag, a):
        if tag == "table": s.t = []
        elif tag == "tr" and s.t is not None: s.row = Row()
        elif tag in ("td", "th") and s.row is not None: s.cell = []
        if s.row is not None:  # intenta capturar el ID del clan desde atributos (data-id, onclick...)
            for k, v in a:
                m = v and (k in ("onclick", "href") or re.search(r"(^|-)id$|clan", k)) and (re.search(r"\d+", v) or (k.startswith("data-") and v))
                if m and not s.row.rid: s.row.rid = m.group() if hasattr(m, "group") else v
    def handle_data(s, d):
        if s.cell is not None: s.cell.append(d)
    def handle_endtag(s, tag):
        if tag in ("td", "th") and s.cell is not None:
            s.row.append(" ".join("".join(s.cell).split())); s.cell = None
        elif tag == "tr" and s.row is not None:
            if s.row: s.t.append(s.row)
            s.row = None
        elif tag == "table" and s.t is not None:
            s.tables.append(s.t); s.t = None

num = lambda x: int(re.sub(r"\D", "", x) or 0)
JAR = http.cookiejar.CookieJar(); OPEN = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR)); CSRF = [""]
KA = {"on": True, "new": 0, "reuse": 0, "fb": 0}; _TL = threading.local()   # conexion persistente (keep-alive): sin handshake TLS en cada lectura

def _direct(p):  # sin proxy configurado para este host -> se puede usar la conexion persistente
    try:
        px = urllib.request.getproxies()
        return not px.get(p.scheme) or bool(urllib.request.proxy_bypass(p.hostname or ""))
    except Exception: return True

def _ka_fetch(u, data, h, method):
    p = urlparse(u); key = (p.scheme, p.netloc); pool = getattr(_TL, "pool", None)
    if pool is None: pool = _TL.pool = {}
    for attempt in (0, 1):
        c = pool.get(key); reused = c is not None
        if c is None:
            ctx = ssl.create_default_context(); c = http.client.HTTPSConnection(p.netloc, timeout=12, context=ctx) if p.scheme == "https" else http.client.HTTPConnection(p.netloc, timeout=12)
            pool[key] = c
        req = urllib.request.Request(u, data=data, headers=h, method=method); JAR.add_cookie_header(req)
        hh = dict(req.header_items()); hh["Connection"] = "keep-alive"
        try:
            c.request(req.get_method(), (p.path or "/") + ("?" + p.query if p.query else ""), body=data, headers=hh)
            r = c.getresponse(); b = r.read()
        except Exception:
            try: c.close()
            except Exception: pass
            pool.pop(key, None)
            if attempt or not reused: raise
            continue   # la conexion guardada ya estaba cerrada por el servidor: se abre otra
        KA["reuse" if reused else "new"] += 1
        JAR.extract_cookies(r, req)
        if r.will_close:
            c.close(); pool.pop(key, None)
        return r, b

FETCH_STATS = {"ok": 0, "fail": 0, "retry": 0}
def fetch(u, data=None, api=False, method=None, ct="application/json"):
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", "Accept-Encoding": "gzip", "Referer": URL, "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"}
    if api: h.update({"X-CSRF-TOKEN": CSRF[0], "X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/html, */*"})
    if data is not None: h["Content-Type"] = ct
    p = urlparse(u)
    if KA["on"] and p.scheme in ("http", "https") and _direct(p):
        try:
            r, b = _ka_fetch(u, data, h, method)
            if r.status >= 400: raise urllib.error.HTTPError(u, r.status, r.reason, r.headers, None)
            if r.status < 300:
                return (gzip.decompress(b) if (r.getheader("Content-Encoding") or "").lower() == "gzip" else b).decode("utf-8", "ignore")
        except urllib.error.HTTPError: raise
        except Exception: pass
        KA["fb"] += 1   # redireccion, bloqueo o fallo raro: se usa el metodo clasico
        if KA["fb"] > 25 and KA["reuse"] == 0: KA["on"] = False
    for _att in range(3):
        try:
            r = OPEN.open(urllib.request.Request(u, data=data, headers=h, method=method), timeout=12); b = r.read()
            FETCH_STATS["ok"] += 1
            return (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "ignore")
        except Exception as _e:
            FETCH_STATS["retry"] += 1
            if _att == 2: FETCH_STATS["fail"] += 1; raise
            time.sleep(0.5 * (2 ** _att))

def shown(n, rank):  # nombres hechos solo de caracteres invisibles -> etiqueta legible
    return n if re.search(r"[^\s\u3164\u115f\u1160\u2800\u200b-\u200f\u2060\ufeff]", n) else "👻 Invisible #" + str(num(rank))

def scrape():
    html = fetch(URL); m = re.search(r'csrf[^>]*content=["\']([^"\']+)|content=["\']([^"\']+)["\'][^>]*csrf', html)
    if m: CSRF[0] = m.group(1) or m.group(2)
    p = TableParser(); p.feed(html)
    clans, pvp, pve, seasons = [], [], [], []
    for t in p.tables:
        head = " ".join(t[0]).lower()
        if "reputation" in head and "master" not in head: seasons.append([dict(rank=num(r[0]), clan=r[1], rep=num(r[3])) for r in t[1:] if len(r) >= 4])
        for r in t[1:]:
            if "master" in head and len(r) >= 5:
                clans.append(dict(rank=num(r[0]), name=r[1], master=r[2], members=r[3], rep=num(r[4]), id=r.rid))
            elif "win" in head and len(r) >= 4:
                w = (r[3].split("/") if "/" in r[3] else re.findall(r"\d+", r[3])) + ["0", "0"]   # "1,234 / 56": antes los miles se partian
                pvp.append(dict(name=shown(r[1], r[0]), score=num(r[2]), w=num(w[0]), l=num(w[1]), rank=num(r[0])))
            elif "score" in head and len(r) >= 3:
                nm = shown(r[1], r[0]); pve.append(dict(name=nm, score=num(r[2]), rank=num(r[0]), **({"gh": 1, "sig": len(r[1])} if nm != r[1] else {})))
    text = re.sub(r"<[^>]+>", " ", html); heads = re.findall(r"Season\s+(\d+)\s*[—–:-]?\s*Top\s*10[\s\S]{0,60}?Ended\s+(\d{1,2}\s+\w+\s+\d{4})", text)
    try:
        SEAS[:] = [dict(n=int(n), ended=time.mktime(time.strptime(re.sub(r"\s+", " ", d), "%d %b %Y")), rows=rows) for (n, d), rows in zip(heads, seasons)]
        for x in SEAS:  # "Darkness Of Abys" (recortado en el sitio) -> nombre completo actual
            for r in x["rows"]: r["clan"] = next((c["name"] for c in clans if c["name"] != r["clan"] and len(r["clan"]) >= 10 and c["name"].startswith(r["clan"])), r["clan"])
    except Exception: pass
    LBL[:] = [list(x) for x in re.findall(r"Season\s*(\d+)\s*(?:·|&middot;|&#183;|•|-|–|\|)?\s*Round\s*(\d+)\s*/\s*(\d+)", text)][:4]   # "Season 4 · Round 1/2" (PvE, PvP)
    SEASON["dates"] = re.findall(r"\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?|1[6-9]\d{11}(?!\d)", html)
    return clans, pvp, pve

def find_members(o):  # elige, entre todas las listas del JSON, la que parece de MIEMBROS (nombre + REP + nivel); ignora claves "clan_..."
    best = []
    def walk(x):
        nonlocal best
        if isinstance(x, list) and x and all(isinstance(i, dict) for i in x):
            out = []
            for it in x:
                nm = next((v for k, v in it.items() if isinstance(v, str) and re.search(r"name|nick|char|player|member", k, re.I) and not re.search(r"clan|guild|team", k, re.I)), None)
                rp = next((v for k, v in it.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and re.search(r"rep|contrib|point|score", k, re.I) and not re.search(r"clan", k, re.I)), None)
                lv = next((v for k, v in it.items() if isinstance(v, (int, float)) and re.search(r"^(lv|lvl|level)$", k, re.I)), None)
                if nm is not None and rp is not None: out.append((nm, rp, lv))
            key = lambda L: (sum(1 for t in L if t[2] is not None), len(L))
            if out and key(out) > key(best): best = out
        for v in (x.values() if isinstance(x, dict) else x if isinstance(x, list) else []): walk(v)
    walk(o)
    return [(n, r, l or 0) for n, r, l in best if l is not None] or [(n, r, 0) for n, r, l in best]

def get_members(c):
    raw = fetch(MEMBERS_URL.replace("{name}", quote(c["name"])).replace("{id}", quote(c["id"])))
    try: return find_members(json.loads(raw))
    except ValueError:
        p = TableParser(); p.feed(raw); out = []
        for t in p.tables:
            for r in t[1:]:
                nm = next((x for x in r if re.search(r"[^\W\d_]", x)), None)
                if nm and len(r) >= 2 and re.search(r"\d", r[-1]): out.append((nm, num(r[-1])))
        return out

state = dict(ts=0, clans=[], pvp=[], pve=[], events=[], error=None, endpoints=[], mem_msg="Sin configurar")
snaps, lock, cond, ver = [], threading.Lock(), threading.Condition(), [0]; delay = [POLL]
prev_pvp, prev_pve, mem_prev, sess, sessL, last, pairs, hours = {}, {}, {}, {}, {}, {}, {}, {}

def scrape_resellers():
    try:
        html = fetch(URL); found = []
        for m in re.finditer(r"([A-Za-z0-9_]{2,25})\s*\((ID|MY|PH|LATAM|PE|CO|MX|AR|BR|CL|EC|BO|UY|PY|VE|US|EU|IN|NP|BD|PK)[^)]*\)[\s\S]{0,120}?Discord[:\s]*([A-Za-z0-9_.]{2,40})", html, re.I):
            found.append(dict(name=m.group(1), country=m.group(2).upper(), contact=m.group(3), pay="Discord", rate="Oficial", img="", ok=1, auto=1))
        if found:
            with XL:
                if EXTRAS["shop"].get("auto") or not EXTRAS["shop"].get("res"):
                    EXTRAS["shop"]["res"] = [r for r in EXTRAS["shop"].get("res",[]) if not r.get("auto")] + found[:8]
                    EXTRAS["shop"]["upd"] = time.time(); save_extras()
            print("Resellers auto-detectados:", len(found))
    except Exception as e: LOG.append([time.time(), "resellers: " + str(e)[:60]])

def discover():  # busca en el JS del sitio posibles endpoints de clanes/miembros
    try:
        html = fetch(URL); found = set(); rx = r'["\'`]((?:/|https?://)[^"\'`\s]*(?:clan|member)[^"\'`\s]*)["\'`]'
        found |= set(re.findall(rx, html, re.I))
        for src in re.findall(r'<script[^>]+src=["\']([^"\']+)', html)[:8]:
            try: found |= set(re.findall(rx, fetch(urljoin(URL, src)), re.I))
            except Exception: pass
        state["endpoints"] = sorted(found)[:25]
        print("Endpoints posibles:", *state["endpoints"], sep="\n  ")
    except Exception as e: print("discover:", e)

EP, mem_cache, busy, mstats, SEAS, SEASON, MINE, prev_rank, wacc, summ, MON, tick = {}, {}, [False], {}, [], {}, [""], {}, {}, [], [], {"w": None, "d": None}
CFG = dict(rank=1, atk=1, hit=1, mon=1, move=1, summ=1, daily=1, any=0, pve=0, thr=100, pthr=5000, h0=0, h1=24)  # alertas de Discord
LOG, LAT, rlock, busy2 = [], [0], threading.Lock(), [False]; heat = {}; wake = threading.Event(); pend, rleft, debt, prev_cnt, rjoin = {}, {}, {}, {}, {}; mlog = []; SER = {"f": {"t": [], "d": {}}, "c": {"t": [], "d": {}}}  # registro de errores, latencia, cerrojo de lectura de miembros  # EP = como pedir los miembros de un clan (auto-detectado o aprendido)

RG, PLs, rk_chg = [], [], {}
PEND_HITS = {}  # objetivo -> evento provisional; se completa cuando aparece el atacante   # ganancias recientes sin emparejar / perdidas esperando atacante / ultimo cambio de puesto de cada clan

# ================= V15 =================
EVSEQ, EVLOG_U, EVLOG_E = [0], [], []   # cada evento lleva id y "u" (version): el navegador solo recibe lo nuevo o lo que cambio
CM, REC, ABURST, GSIG, PVE_ST, PVP_ST, pve_rk, prev_pve_rank = {}, {}, {}, {}, {}, {}, {}, {}
LAN, CLIENTS, BASE, LATH, SAVEL = [""], [0], [-1, ""], [], threading.Lock()
fmt = lambda n: "{:,}".format(int(n)).replace(",", ".")

def dur(s):
    s = int(s); return "%ds" % s if s < 90 else "%dm" % round(s / 60) if s < 5400 else "%.1fh" % (s / 3600)

def ev_add(e):   # agrega un evento (siempre con el cerrojo tomado)
    EVSEQ[0] += 1; e["id"] = e["u"] = EVSEQ[0]; state["events"].append(e); EVLOG_U.append(e["u"]); EVLOG_E.append(e)
    if len(EVLOG_U) > 9000: del EVLOG_U[:-6000]; del EVLOG_E[:-6000]
    if len(state["events"]) > 6000: del state["events"][:-4000]

def ev_touch(e):   # un evento ya enviado cambio (p. ej. se identifico al atacante): se reenvia
    if "id" not in e: return
    EVSEQ[0] += 1; e["u"] = EVSEQ[0]; EVLOG_U.append(e["u"]); EVLOG_E.append(e)

def rec_day(now):   # records del dia (se reinician a medianoche, hora de la PC)
    d = time.strftime("%Y-%m-%d", time.localtime(now))
    if REC.get("d") != d:
        REC.clear(); REC.update(d=d, gain={}, loss={}, pl={}, pve={}, pvp={}, hr=[0] * 24, big=None, hit=None, nh=0, npv=0)

def rec_add(key, k, v):
    if REC.get("d"): REC[key][k] = REC[key].get(k, 0) + v

def rec_summary():
    R = REC
    if not R.get("d"): return {}
    top = lambda d, n: sorted(d.items(), key=lambda x: -x[1])[:n]
    return dict(d=R["d"], big=R.get("big"), hit=R.get("hit"), gain=top(R["gain"], 6), loss=top(R["loss"], 6), pl=[k.split("|", 1) + [v] for k, v in top(R["pl"], 8)],
                pve=top(R["pve"], 6), pvp=top(R["pvp"], 6), hr=R["hr"], tg=sum(R["gain"].values()), tl=sum(R["loss"].values()), nh=R.get("nh", 0), npv=R.get("npv", 0))

def burst_alert(cl, amt, rep, rank, now):   # Discord: 1 aviso cuando un clan EMPIEZA a atacar y otro cada 2 min si sigue (no spam)
    b = ABURST.get(cl)
    if not b or now - b["last"] > 90: b = ABURST[cl] = dict(start=now, last=now, tot=0, n=0, sent=0)
    b["tot"] += amt; b["n"] += 1; b["last"] = now; mine = cl == MINE[0]
    if b["tot"] >= CFG["thr"] and (not b["sent"] or now - b["sent"] >= 120):
        first = not b["sent"]; b["sent"] = now
        notify("⚔️ **%s**%s %s · +%s en %s (×%d) · REP **%s** · #%d" % (cl, " (tu clan)" if mine else "", "EMPEZÓ A ATACAR" if first else "sigue atacando", fmt(b["tot"]), dur(now - b["start"]), b["n"], fmt(rep), rank),
               "atk" if mine else ("mon", "any") if cl in MON else "any")
    if len(ABURST) > 40:
        for k in [k for k, v in ABURST.items() if now - v["last"] > 600]: del ABURST[k]

# ---- lectura de miembros: cola con prioridad + varios lectores en paralelo ----
MQ, MQL, MQE, CLK, CLKL, MLAST, MTS = {}, threading.Lock(), threading.Event(), {}, threading.Lock(), {}, []
MGAP = (1.0, 5.0, 60.0)   # minimo entre lecturas del mismo clan: 0 = acaba de atacar/sangrar, 1 = mi clan/★/nuevos, 2 = barrido general

def clan_lock(n):
    with CLKL: return CLK.setdefault(n, threading.Lock())

def mq_push(names, pr):
    now = time.time()
    with MQL:
        for n in names:
            nb = max(now, MLAST.get(n, 0) + MGAP[pr]); cur = MQ.get(n)
            if cur is None or pr < cur[0] or (pr == cur[0] and nb < cur[1]): MQ[n] = [pr, nb]
    MQE.set()

def mq_worker():   # antes: si habia una lectura en curso, los clanes que atacaban se saltaban (hasta 5 min sin saber que jugador ataco)
    while True:
        with MQL:
            now = time.time(); ready = [(v[0], v[1], k) for k, v in MQ.items() if v[1] <= now]
            if ready: pr, _, k = min(ready); del MQ[k]
            else: k = None; wait = min([v[1] - now for v in MQ.values()] + [1.0])
        if k is None:
            MQE.wait(max(.02, wait)); MQE.clear(); continue
        c = CM.get(k) or next((x for x in state["clans"] if x["name"] == k), None)
        if not c or not EP: continue
        lk = clan_lock(k)
        if not lk.acquire(False):
            mq_push([k], pr); time.sleep(.05); continue
        t0 = time.time()
        try: _refresh(c)
        except Exception as e:
            with lock: mem_prev.setdefault(k, {})
            LOG.append([time.time(), "miembros de %s: %s" % (k, str(e)[:60])]); del LOG[:-30]
        finally:
            MLAST[k] = time.time(); MTS.append(time.time() - t0); del MTS[:-30]; lk.release()
        time.sleep(.03)

def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); ip = s.getsockname()[0]; s.close()
        return "" if ip.startswith("127.") else ip
    except Exception: return ""

# ================= RECOMPENSAS (PvE / PvP / CW) + TOKENS Y REVENDEDORES =================
from html import unescape as _unesc
import base64, hashlib
XFILE, IMGDIR = "zenshin_extras.json", "zenshin_img"
EXTRAS = {"manual": {}, "shop": {"tok": [], "res": [], "upd": 0, "auto": 1}, "news": {}, "kyubi": {"snaps": []}}
DEFAULT_RES = [
    dict(name="Hardz", country="ID / MY", contact="avivrusdiawan", pay="Discord · Midtrans IDR · PayPal", rate="Oficial", img="", ok=1, auto=1),
    dict(name="INFLUUX", country="PH", contact="gcd2026", pay="Discord · PayPal · GCash", rate="Oficial", img="", ok=1, auto=1),
    dict(name="blozz17", country="LATAM", contact="blozz17", pay="Discord · Binance · PayPal", rate="Oficial", img="", ok=1, auto=1),
    dict(name="Ninjayex", country="LATAM", contact="ninjayexx", pay="Discord · Binance · Tarjeta · PayPal", rate="Oficial", img="", ok=1, auto=1),
]
DEFAULT_TOK = [
    dict(tok="98.800", price="31.25", cur="USD", note="+Package · Mejor valor · Ninja Emblem", img="", official=1, pkg=1, best=1, per1k="0.32"),
    dict(tok="58.800", price="18.75", cur="USD", note="+Package · Gran ahorro", img="", official=1, pkg=1, per1k="0.32"),
    dict(tok="18.800", price="6.25", cur="USD", note="+Package · Pack popular", img="", official=1, pkg=1, per1k="0.33"),
    dict(tok="7.800", price="3.15", cur="USD", note="Pack estandar", img="", official=1, per1k="0.40"),
    dict(tok="2.200", price="1.25", cur="USD", note="Pack basico", img="", official=1, per1k="0.57"),
    dict(tok="1.000", price="1.25", cur="USD", note="50% OFF · antes $2.50", img="", official=1, discount=50, old="2.50", per1k="1.25"),
    dict(tok="450", price="0.65", cur="USD", note="50% OFF · antes $1.25", img="", official=1, discount=50, old="1.25", per1k="1.44"),
    dict(tok="80", price="0.25", cur="USD", note="Pack minimo", img="", official=1, per1k="3.13"),
]
PAYMENT_METHODS = [
    dict(id="paypal", name="PayPal", icon="🅿️", color="#0070ba", note="Proteccion al comprador"),
    dict(id="card", name="Tarjeta Credito/Debito", icon="💳", color="#4a90d9", note="Visa · Mastercard · AMEX"),
    dict(id="binance", name="Binance Pay", icon="🟡", color="#f0b90b", note="Crypto · USDT · BTC · BNB"),
    dict(id="midtrans", name="Midtrans IDR", icon="🇮🇩", color="#1ba0d7", note="Indonesia · GoPay · OVO · DANA"),
]
BONUS_PACKAGES = ["Refined Chakra Package", "Magic Emissary Package", "Dragon Sovereign Package", "Shadow Ronin Package", "+12.000 Tokens", "Night Raven Package", "Seiryu Ronin Package", "Crimson Forbidden Package", "Shadow Crow ANBU Package"]
KYIMG = {"fox_adv": "data:image/jpeg;base64,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", "fox_std": "data:image/jpeg;base64,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", "ep": "data:image/png;base64,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"}   # iconos de las recompensas del Kyubi (recortados de la ventana del juego)
XV, LBL, XL = [1], [], threading.Lock()
_NW = "https://ninjazenshin.online/"
DEFAULT_REW = {   # lo publicado en la web oficial (se reemplaza solo cuando salga un anuncio nuevo)
    "pve": dict(title="PvE / Cooperative Season 4 – Round 1", url=_NW + "news/34", img="", date="26 Sep 2026", end=None,
                jutsu_adv="S4 Kinjutsu: Advanced Minotaur Combo · CP10 DMG480 CD12 · Lesion Interna -100% ATK 4t", jutsu_norm="S4 Kinjutsu: Minotaur Combo · CP10 DMG270 CD15 · Lesion Interna -60% ATK 3t",
                rows=[dict(cond="50,000+ Score", item="Minotaur Hairstyle"), dict(cond="Rank 1–30", item="Kinjutsu: Clean Beads"), dict(cond="Rank 31–100", item="Kinjutsu: Prison Beads")],
                notes=["To be eligible for the Skill/Jutsu Reward, players must achieve a minimum score of 300,000."], src="base"),
    "pvp": dict(title="PvP Ranking – Season 0 (Trial Season)", url=_NW + "news/32", img=_NW + "storage/news/HQtFwXa7wXL8LmZSNuahS1Y8EVSVZjcMwVVuTmt4.png", date="05 Sep 2026", end=None,
                rows=[dict(cond="Top 1–3", item="Exclusive Outfit"), dict(cond="Top 1–10", item="Exclusive Weapon / Back Item / Jutsu, or a combination of these rewards")],
                notes=["The PvP Ranking Rewards are still currently under development.", "Win: +10 Points · Loss: −5 Points (Quick Match vs real players)"], src="base"),
    "cw": dict(title="Clan War Season 3", url="https://archive.nscommunity.cc/clan-war/3/", img="", rows=[dict(cond="Top 1–3", item="S3 Kinjutsu: Ecstasy of Sound + ShurikenKurosemi + Ropa S3"), dict(cond="Top 4–10", item="ShurikenKurosemi + Ropa S3"), dict(cond="Participación", item="Ropa S3")], notes=["Kinjutsu: Ecstasy of Sound · CP200 DMG0 CD14 · Restringe y -100% ATK 2 turnos (ANIM)", "Arma: ShurikenKurosemi · DMG200 · Entumecimiento 2t · -5% esquiva (ANIM)", "Sin back item en S3", "Archivo completo: archive.nscommunity.cc/clan-war/3/"], src="archive"),
    "raid": dict(title="Tailed Beast Raid – 9 Tails Round 2", url=_NW + "news/31", img=_NW + "storage/news/iOXUijcs8Id0ArOSFDaJj11k1GbcHPNQBDdWsXs4.png", date="03 Sep 2026", end=None,
                 rows=[dict(cond="Rank 1–30 + minimum 100,000 Event Points", item="Advanced Kyubi Chakra Yoroi Jutsu"), dict(cond="Rank 31+ + minimum 50,000 Event Points", item="Kyubi Chakra Yoroi Jutsu")],
                 notes=["Event Points will be reset when a new Round begins.", "Each reward can only be obtained once per character.", "Ranking rewards will be distributed into your in-game mailbox within 1 month after the event has ended."], src="base"),
}
DEFAULT_PKG = [dict(id=30, title="New Exclusive Top-Up Package – Crimson Forbidden", url=_NW + "news/30", img=_NW + "storage/news/hITCGSkmkGUVVwMokNjiPrj5QWtkWqmpO5P7PrLq.png", date="01 Sep 2026"),
               dict(id=24, title="New Exclusive Top-Up Package – Night Raven", url=_NW + "news/24", img=_NW + "storage/news/tuCmcrWVFhGzzz1bvJbGaBsWYAd2h0MO4TEAvFno.png", date="02 Aug 2026")]
_RULE = re.compile(r"\b(rank|top|place|puesto|lugar)\b\s*#?\d|\d[\d,.]*\s*\+|#\s*\d", re.I)
_MON = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}

def _lines(h):   # HTML -> lineas de texto (con viñetas)
    h = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?i)<li[^>]*>", "\n• ", h)
    h = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article|ul|ol|table)>", "\n", h)
    h = _unesc(re.sub(r"<[^>]+>", " ", h))
    return [x for x in (re.sub(r"\s+", " ", y).strip() for y in h.split("\n")) if x]

def _date(s):   # "12 Sep 2026" / "September 5, 2026" -> timestamp
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", s) or None
    if m and m.group(2).lower() in _MON: return time.mktime((int(m.group(3)), _MON[m.group(2).lower()], int(m.group(1)), 0, 0, 0, 0, 0, -1))
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1).lower() in _MON: return time.mktime((int(m.group(3)), _MON[m.group(1).lower()], int(m.group(2)), 0, 0, 0, 0, 0, -1))
    return None

try: DEFAULT_REW["pve"]["end"] = (_date("26 Sep 2026") or time.time()) + 14 * 86400
except Exception: DEFAULT_REW["pve"]["end"] = time.time() + 14 * 86400
try: DEFAULT_REW["raid"]["end"] = (_date("03 Sep 2026") or time.time()) + 28 * 86400
except Exception: DEFAULT_REW["raid"]["end"] = time.time() + 28 * 86400
try: DEFAULT_REW["pvp"]["end"] = (_date("20 Oct 2026") or time.time()) + 86399
except Exception: DEFAULT_REW["pvp"]["end"] = time.time() + 30 * 86400

def news_parse(h, nid, url):
    g = lambda rx: (re.search(rx, h, re.I | re.S) or [None, ""])[1]
    cand = re.findall(r"(?is)<h[12][^>]*>(.*?)</h[12]>", h) + [g(r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)'), g(r"<title[^>]*>(.*?)</title>")]
    cand = [re.sub(r"\s*[|\-–]\s*Ninja Zenshin\s*$", "", re.sub(r"\s+", " ", _unesc(re.sub(r"<[^>]+>", " ", c)))).strip() for c in cand if c]
    cand = [c for c in cand if c and c.lower() != "ninja zenshin"]
    title = next((c for c in cand if re.search(r"pve|pvp|clan war|package|top-?up|cooperative|season|event|update|raid", c, re.I)), cand[0] if cand else "")
    og = g(r'property=["\']og:image["\'][^>]*content=["\']([^"\']+)')
    img = og if "/storage/news/" in og else g(r'src=["\']([^"\']*/storage/news/[^"\']+)["\']') or og
    L = _lines(h); i0 = next((i for i, x in enumerate(L) if title and x.startswith(title[:30])), 0)
    body = L[i0:i0 + 160]; body = body[:next((i for i, x in enumerate(body) if re.search(r"terms of service|privacy policy|all rights reserved", x, re.I)), len(body))]
    date_s = next((m.group(0) for x in body[:8] + L[:40] for m in [re.search(r"\b\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}\b", x)] if m), "")
    rows, notes, end, per = [], [], None, []
    for x in body:
        m = re.match(r"^\s*(?:[•·\-*▪►✅🔹🔸🏆⭐]+\s*)?(.{2,70}?)\s*[:：]\s*(.{2,160})$", x)
        if m and _RULE.search(m.group(1)) and not re.search(r"period|duration|point|search|after|win|loss|lose|level", m.group(1), re.I):
            rows.append(dict(cond=m.group(1).strip(), item=m.group(2).strip().rstrip(".")))
        elif re.search(r"minimum score|eligible|under development|m[ií]nimo", x, re.I) and len(notes) < 4: notes.append(x.lstrip("•·- ").strip())
        p = re.search(r"(?:period|periodo)[^:]*:\s*(.+)", x, re.I)
        if p and re.search(r"[–—-]", p.group(1)):
            e_ = _date(re.split(r"[–—]| - ", p.group(1))[-1] + (" " + re.findall(r"\d{4}", p.group(1))[-1] if re.findall(r"\d{4}", p.group(1)) else ""))
            if e_: per.append((x, e_ + 86399))
    if per: end = next((e_ for l_, e_ in per if re.search(r"season|temporada|ranking", l_, re.I)), per[0][1])
    if end is None:
        d = next((re.search(r"(\d+)\s*days?", x, re.I) for x in body if re.search(r"duration", x, re.I) and re.search(r"\d+\s*days?", x, re.I)), None); t0 = _date(date_s)
        if d and t0: end = t0 + int(d.group(1)) * 86400
    t = title.lower()
    prices = []
    for x in body:
        for m in re.finditer(r"(\$|USD|EUR|COP|MXN|BRL|PEN|ARS|CLP|PHP|IDR|MYR)?\s*(\d[\d.,]{0,8})\s*(USD|EUR|COP|MXN|BRL|PEN|ARS|CLP|PHP|IDR|MYR|\$)?", x, re.I):
            cur = ((m.group(1) or m.group(3) or "").strip("$ ").upper() or "USD")
            try:
                v = float(re.sub(r"[.,](?=\d{2}$)", ".", m.group(2)).replace(",", ""))
                if 0.5 <= v <= 5000: prices.append(dict(price=("%.2f" % v).rstrip("0").rstrip("."), cur=cur))
            except ValueError: pass
    prices = [dict(t) for t in {(p["price"], p.get("cur","USD")): p for p in prices}.values()][:8]
    cats = ["pkg"] if re.search(r"package|top-?up", t) else [c for c, rx in (("cw", r"clan\s*war|\bcw\b"), ("pvp", r"pvp"), ("pve", r"pve|cooperative"), ("raid", r"tailed\s*beast|\braid\b|kyubi|9\s*tails|nine\s*tails")) if re.search(rx, t)]
    return dict(id=nid, url=url, title=title, img=urljoin(url, img) if img else "", date=date_s, rows=rows, notes=notes, end=end, cats=cats, prices=prices, t=time.time())

def news_refresh(force=False):   # lee /news, abre los anuncios nuevos y extrae recompensas + imagen
    ids = set()
    for pg in ("", "?page=2"):
        try: ids |= {int(x) for x in re.findall(r'/news/(\d+)', fetch(urljoin(URL, "/news" + pg)))}
        except Exception as e: LOG.append([time.time(), "noticias: " + str(e)[:60]]); break
        if len(ids) >= 14: break
    todo = [i for i in sorted(ids, reverse=True)[:18] if force or str(i) not in EXTRAS["news"]][:18]
    for i in todo:
        u = urljoin(URL, "/news/%d" % i)
        try:
            a = news_parse(fetch(u), i, u)
            with XL: EXTRAS["news"][str(i)] = a
        except Exception as e: LOG.append([time.time(), "noticia %d: %s" % (i, str(e)[:50])])
        time.sleep(.3)
    if todo:
        with XL:
            detected = []
            for a in EXTRAS["news"].values():
                for p in a.get("prices", []):
                    if p.get("price"): detected.append(dict(tok="Paquete #%s" % a.get("id","?"), price=p["price"], cur=p.get("cur","USD"), note=a.get("title","")[:50], img=a.get("img",""), auto=1))
            if detected and EXTRAS["shop"].get("auto"):
                EXTRAS["shop"]["tok"] = [r for r in EXTRAS["shop"].get("tok",[]) if r.get("official") or not r.get("auto")] + detected[:4]
                EXTRAS["shop"]["upd"] = time.time()
            XV[0] += 1
        save_extras()

def news_loop():
    time.sleep(3)
    while True:
        try: news_refresh()
        except Exception as e: LOG.append([time.time(), "noticias: " + str(e)[:60]])
        time.sleep(900)

def official(m):   # el anuncio mas nuevo de ese modo que traiga recompensas (si no hay, lo verificado de fabrica)
    A = sorted((a for a in EXTRAS["news"].values() if m in a.get("cats", [])), key=lambda a: -a["id"])
    a = next((a for a in A if a.get("rows")), None)
    if a: return dict(a, src="web", newer=[dict(title=b["title"], url=b["url"]) for b in A if b["id"] > a["id"]][:1])
    return dict(DEFAULT_REW[m])

def rew_view():
    out = {}
    for m in ("pve", "pvp", "cw", "raid"):
        r = official(m); man = EXTRAS["manual"].get(m)
        r["orows"] = r.get("rows", [])
        if m == "raid": r["rows"] = _kyimg([dict(x) for x in r.get("rows", [])])
        if man:
            for k in ("rows", "img", "notes", "url", "title"):
                if man.get(k): r[k] = man[k]
            r["man"] = 1; r["mt"] = man.get("t", 0)
        out[m] = r
    return out

def pkgs_view():
    P = sorted((a for a in EXTRAS["news"].values() if "pkg" in a.get("cats", [])), key=lambda a: -a["id"])[:4]
    return [dict(id=a["id"], title=a["title"], url=a["url"], img=a["img"], date=a["date"]) for a in P] or DEFAULT_PKG

def load_extras():
    try:
        d = json.load(open(XFILE, encoding="utf-8"))
        for k in ("manual", "shop", "news"):
            if isinstance(d.get(k), dict): EXTRAS[k].update(d[k])
    except Exception: pass
    with XL:
        if not EXTRAS["shop"].get("res"): EXTRAS["shop"]["res"] = [dict(r) for r in DEFAULT_RES]; EXTRAS["shop"]["auto"] = 1
        # v19: siempre prevalecen los precios oficiales (reemplaza "Auto" antiguos)
        _off = {r["tok"]: r for r in DEFAULT_TOK}
        _tok = []
        for r in EXTRAS["shop"].get("tok", []):
            if r.get("official") or str(r.get("price","")) not in ("Auto", "—", ""): _tok.append(r)
        for r in DEFAULT_TOK:
            if r["tok"] not in {x.get("tok") for x in _tok}: _tok.insert(0, dict(r))
        EXTRAS["shop"]["tok"] = _tok[:12]

def save_extras():
    try:
        with XL: s = json.dumps(EXTRAS)
        with open(XFILE + ".tmp", "w", encoding="utf-8") as f: f.write(s)
        os.replace(XFILE + ".tmp", XFILE)
    except Exception as e: LOG.append([time.time(), "extras: " + str(e)[:60]])

def _clean(v, n=300): return str(v if v is not None else "")[:n].strip()

def _url(v):   # solo enlaces http(s) o imagenes subidas aqui
    v = _clean(v, 600)
    return v if re.match(r"^(https?://|/img/[0-9a-f]{16}\.(png|jpg|gif|webp)$)", v, re.I) else ""

def _kyimg(rows):   # imagen de cada recompensa del Kyubi aunque el anuncio no la traiga
    for r in rows:
        if not r.get("img") and "kyubi" in r.get("item", "").lower(): r["img"] = KYIMG["fox_adv"] if "advanced" in r["item"].lower() else KYIMG["fox_std"]
    return rows

def kyubi_post(d):   # capturas del ranking del evento (leidas en el navegador)
    with XL:
        K = EXTRAS.setdefault("kyubi", {"snaps": []}); K.setdefault("snaps", [])
        if d.get("clear"): K["snaps"] = []
        if d.get("del") is not None:
            try: t0 = float(d["del"]); K["snaps"] = [x for x in K["snaps"] if abs(x["t"] - t0) > .5]
            except (TypeError, ValueError): pass
        s = d.get("add")
        if isinstance(s, dict):
            rows = []
            for r in (s.get("rows") or [])[:300]:
                try: rows.append([int(r[0]), _clean(r[1], 40), int(r[2])])
                except (TypeError, ValueError, IndexError): pass
            try: t = float(s.get("t") or time.time())
            except (TypeError, ValueError): t = time.time()
            num_ = lambda v: int(v) if str(v).isdigit() else 0
            if rows or num_(s.get("c30")) or num_(s.get("c100")):
                K["snaps"].append(dict(t=t, rows=rows, c30=num_(s.get("c30")), c100=num_(s.get("c100")), end=_clean(s.get("end"), 20), me=num_(s.get("me"))))
                K["snaps"] = sorted(K["snaps"], key=lambda x: x["t"])[-600:]
        XV[0] += 1
    save_extras()
    return {"v": XV[0]}

def export_data():   # datos.json para la pagina web: tus recompensas, tokens, revendedores y capturas del Kyubi (imagenes incluidas)
    def inline(v):
        m = re.match(r"^/img/([0-9a-f]{16})\.(png|jpg|gif|webp)$", v or "")
        if not m: return v
        try:
            with open(os.path.join(IMGDIR, m.group(1) + "." + m.group(2)), "rb") as f: b = f.read()
            return "data:image/%s;base64,%s" % ({"jpg": "jpeg"}.get(m.group(2), m.group(2)), base64.b64encode(b).decode())
        except OSError: return ""
    with XL: d = json.loads(json.dumps({k: EXTRAS.get(k) for k in ("manual", "shop", "kyubi")}))
    for m_ in d["manual"].values():
        m_["img"] = inline(m_.get("img"))
        for r in m_.get("rows", []): r["img"] = inline(r.get("img"))
    for r in d["shop"].get("tok", []) + d["shop"].get("res", []): r["img"] = inline(r.get("img"))
    d.update(v=1, t=time.time(), app="ninja-zenshin-tracker")
    return d

def extras_post(d):   # guarda lo que edita el usuario (recompensas / tokens / revendedores)
    with XL:
        if d.get("mode") in ("pve", "pvp", "cw", "raid"):
            m = d["mode"]
            if d.get("reset"): EXTRAS["manual"].pop(m, None)
            else:
                rows = [dict(cond=_clean(r.get("cond"), 60), item=_clean(r.get("item"), 160), img=_url(r.get("img"))) for r in (d.get("rows") or [])[:30] if _clean(r.get("item"))]
                EXTRAS["manual"][m] = dict(rows=rows, img=_url(d.get("img")), url=_url(d.get("url")), title=_clean(d.get("title"), 120), notes=[_clean(x, 240) for x in (d.get("notes") or []) if _clean(x)][:6], t=time.time())
        if isinstance(d.get("shop"), dict):
            s = d["shop"]
            EXTRAS["shop"] = dict(upd=time.time(), cur=_clean(s.get("cur"), 12),
                tok=[dict(tok=_clean(r.get("tok"), 30), price=_clean(r.get("price"), 30), cur=_clean(r.get("cur"), 12), note=_clean(r.get("note"), 80), img=_url(r.get("img"))) for r in (s.get("tok") or [])[:30] if _clean(r.get("tok")) or _clean(r.get("price"))],
                res=[dict(name=_clean(r.get("name"), 60), country=_clean(r.get("country"), 40), contact=_clean(r.get("contact"), 200), pay=_clean(r.get("pay"), 120), rate=_clean(r.get("rate"), 120), img=_url(r.get("img")), ok=1 if r.get("ok") else 0) for r in (s.get("res") or [])[:40] if _clean(r.get("name"))])
        XV[0] += 1
    save_extras()
    if d.get("refresh"): threading.Thread(target=news_refresh, args=(True,), daemon=True).start()

def server_stats():
    try:
        ru = int(time.time() - START); mem = 0
        try:
            with open("/proc/self/status") as f:
                for ln in f:
                    if ln.startswith("VmRSS:"): mem = int(ln.split()[1]) // 1024; break
        except Exception: pass
        return dict(uptime=ru, mem=mem, ver="21", py=sys.version.split()[0], clients=CLIENTS[0], ka=dict(KA), polls=POLL, port=PORT,
                    last_good=state.get("ts",0), errors=len(LOG), news=len(EXTRAS.get("news",{})), clans=len(state.get("clans",[])),
                    threads=threading.active_count(), lan=LAN[0], t=time.time())
    except Exception: return dict(uptime=0, ver="16", t=time.time())

ARCHIVE = {"cw_skills": {}, "pve_skills": {}, "cw_seasons": {}, "pve_seasons": {}, "cd": {}, "upd": 0}
KYUBI_LIVE = {"rows": [
    dict(rank=1, name="TDY Rale", pts=233800), dict(rank=2, name="CHAOS Gopa", pts=190100),
    dict(rank=3, name="azura66656", pts=177100), dict(rank=4, name="raftwolf", pts=176400),
    dict(rank=5, name="Satou Renji", pts=165700), dict(rank=6, name="Mutabahir", pts=164900),
    dict(rank=7, name="DoA Sanemi", pts=164400), dict(rank=8, name="Helluser69", pts=161100),
    dict(rank=9, name="CHAOS goblin", pts=159200), dict(rank=10, name="S", pts=157500),
], "ts": 0, "round": "Round 2", "cd": 0, "err": None, "end": "01 Oct 2026", "source": "oficial"}  # v21: datos reales del ranking
# Base de datos de JUTSUS (Kinjas) extraida del archive - con efecto, CP, DMG, CD, categorias y animacion
CW_SKILLS_DB = {
    1: dict(name="S1 Kinjutsu: Death Touch Palms", cp=10, dmg=248, cd=10, cat=["meridian-seal"], eff="Ataca los meridianos para detener el flujo de CP. El defensor no puede usar jutsu ni cargar por 2 turnos. (Usar taijutsu reduce tu HP un 5%)", anim=True),
    2: dict(name="S2 Kinjutsu: Profusion of Ghost", cp=200, dmg=0, cd=12, cat=["debuff","oblivion","cp-reduce"], eff="Alucina al enemigo haciendole sentir atado por cientos de fantasmas. Reduce CP un 55% y pone la habilidad del objetivo en CD por 2 turnos.", anim=True),
    3: dict(name="S3 Kinjutsu: Ecstasy of Sound", cp=200, dmg=0, cd=14, cat=["restriction"], eff="Alucina al objetivo con sonido de extasis para restringirlo y reducir su dano de ataque un 100% por 2 turnos.", anim=True, weapon="S3 ShurikenKurosemi (DMG 200 · Entumecimiento 2 turnos · -5% esquiva)", back=None),
    4: dict(name="S4 Kinjutsu: Sakura Ryu", cp=10, dmg=328, cd=10, cat=["heal","internal-injuries"], eff="Golpea los organos del objetivo y le impide curarse por 6 turnos. (Usar taijutsu reduce tu HP un 5%)", anim=True),
    5: dict(name="S5 Kinjutsu: Cube Illusion", cp=200, dmg=0, cd=14, cat=["cp-reduce","hp-reduce"], eff="Forma un kekkai alrededor del objetivo. Debe consumir 40% CP extra y pierde 2% HP y CP al atacar con arma. (4 turnos)", anim=True),
    6: dict(name="S6 Kinjutsu: Pishamen Ryu", cp=10, dmg=164, cd=12, cat=["heal"], eff="Tras usarla en un objetivo, cualquiera que ataque al mismo objetivo en los proximos 3 turnos recupera HP (60% del dano infligido).", anim=True),
    7: dict(name="S7 Kinjutsu: Silver Chain Bundle", cp=200, dmg=0, cd=14, cat=["debuff"], eff="Bloquea al objetivo 3 turnos si su HP esta por debajo del 50%; reduce su dano de ataque un 100% si su HP es 50% o superior.", anim=True),
    8: dict(name="S8 Kinjutsu: Yatagarasu", cp=200, dmg=0, cd=14, cat=["stun","restriction","sleep"], eff="El primer enemigo que te ataque tras el buff recibe un estado aleatorio (stun, sueno, restriccion, sello meridianos) por 4 turnos.", anim=True),
    9: dict(name="S9 Kinjutsu: Fiery Night Phoenix", cp=10, dmg=164, cd=10, cat=["burn"], eff="Asesta un puño ardiente e inflige quemadura. Reduce HP un 3% por 4 turnos.", anim=True),
    10: dict(name="S10 Kinjutsu: Dynamic Dragon Strike", cp=10, dmg=328, cd=10, cat=["debuff"], eff="Crea una presion de aire en forma de dragon que aplasta al objetivo y reduce su dano de ataque un 98% por 4 turnos.", anim=True),
    11: dict(name="S11 Kinjutsu: Darkness Chain Seal", cp=200, dmg=0, cd=14, cat=["debuff","dodge-reduce"], eff="Sella el poder del objetivo con cadenas oscuras: reduce 130% su tasa de esquiva, y 130% extra durante su ataque. (2 turnos)", anim=True),
    12: dict(name="S12 Kinjutsu: Kejsumaru", cp=200, dmg=0, cd=14, cat=["heal"], eff="Inflige bloodfeed: recuperas HP tras atacar (130% del dano infligido) por 3 turnos.", anim=True),
}
PVE_SKILLS_DB = {
    4: dict(adv=dict(name="S4 Kinjutsu: Advanced Minotaur Combo", type="Taijutsu LV1", cp=10, dmg=480, cd=12, cat=["internal-injuries","debuff"], eff="El equipo contrario recibe 'Lesion Interna' y reduce su dano de ataque un 100% por 4 turnos.", anim=True),
           norm=dict(name="S4 Kinjutsu: Minotaur Combo", type="Taijutsu LV1", cp=10, dmg=270, cd=15, cat=["internal-injuries","debuff"], eff="Inflige 'Lesion Interna' y reduce el dano de ataque del objetivo un 60% por 3 turnos.", anim=True),
           cloth=dict(name="S4 Minotaur Suit", cond="120,000+ Score", type="Cosmetico LV1")),
}
ARCHIVE["cw_skills"] = {str(k): v for k, v in CW_SKILLS_DB.items()}
ARCHIVE["pve_skills"] = {str(k): v for k, v in PVE_SKILLS_DB.items()}

def kyubi_scrape():
    """v19: Lee el ranking del Kyubi/Tailed Beast Raid EN VIVO del sitio (sin capturas)."""
    try:
        for u in (urljoin(URL, "/event/kyubi"), urljoin(URL, "/event/tailed-beast"), urljoin(URL, "/raid")):
            try:
                h = fetch(u)
                p = TableParser(); p.feed(h)
                for t in p.tables:
                    head = " ".join(t[0]).lower()
                    if any(k in head for k in ("event point", "raid", "kyubi", "tailed", "ranking")) and len(t) > 2:
                        rows = []
                        for r in t[1:50]:
                            if len(r) >= 3:
                                rows.append(dict(rank=num(r[0]), name=shown(r[1], r[0]), pts=num(r[-1])))
                        if rows:
                            KYUBI_LIVE.update(rows=rows, ts=time.time(), err=None)
                            print("Kyubi live: %d jugadores leídos" % len(rows)); return
            except Exception: continue
        KYUBI_LIVE["err"] = "Ranking del Kyubi no disponible en el sitio (solo durante el evento)"
    except Exception as e:
        KYUBI_LIVE["err"] = str(e)[:60]

def archive_scrape():
    """v17: Extrae temporadas y jutsus del archive.nscommunity.cc automaticamente."""
    try:
        # Skills de CW (lista completa)
        h = fetch("https://archive.nscommunity.cc/skills/from/clan-war/")
        for m in re.finditer(r"CW S(\d+)\s+!\[[^\]]*\]\((https://archive\.nscommunity\.cc/skills/[^)]+)\)[\s\S]{0,400}?CP\s*(\d+)\s+DMG\s*(\d+)\s+CD\s*(\d+)[\s\S]{0,200}?\(Kinjutsu\)\s*([^\n]{20,300})", h, re.I):
            sn = m.group(1)
            if sn not in ARCHIVE["cw_skills"]:
                ARCHIVE["cw_skills"][sn] = dict(name="S%s Kinjutsu" % sn, cp=int(m.group(3)), dmg=int(m.group(4)), cd=int(m.group(5)), eff=m.group(6).strip()[:280], anim=True, img=m.group(2), auto=1)
        # Temporadas CW (1 a 12)
        for s in range(1, 13):
            try:
                hs = fetch("https://archive.nscommunity.cc/clan-war/%d/" % s)
                sk = re.search(r"S(\d+)\s+Kinjutsu:\s*([^<\n]{3,60})", hs)
                if sk and str(s) not in ARCHIVE["cw_seasons"]:
                    ARCHIVE["cw_seasons"][str(s)] = dict(season=s, kinjutsu=sk.group(2).strip(), top10=[], auto=1)
                time.sleep(0.2)
            except Exception: pass
        # PvE temporadas
        for s in range(1, 6):
            try:
                hs = fetch("https://archive.nscommunity.cc/pve/%d/" % s)
                cd_m = re.search(r"Round\s*1\s*(\d+)D\s*(\d+)H\s*(\d+)M\s*(\d+)S", hs, re.I)
                if cd_m:
                    ARCHIVE["cd"]["pve_s%d" % s] = time.time() + int(cd_m.group(1))*86400 + int(cd_m.group(2))*3600 + int(cd_m.group(3))*60 + int(cd_m.group(4))
                if str(s) not in ARCHIVE["pve_seasons"]:
                    ARCHIVE["pve_seasons"][str(s)] = dict(season=s, auto=1)
                time.sleep(0.2)
            except Exception: pass
        ARCHIVE["upd"] = time.time()
        print("Archive actualizado: %d skills CW, %d temp CW, %d temp PvE" % (len(ARCHIVE["cw_skills"]), len(ARCHIVE["cw_seasons"]), len(ARCHIVE["pve_seasons"])))
    except Exception as e:
        LOG.append([time.time(), "archive: " + str(e)[:60]])

def archive_loop():
    time.sleep(8)
    while True:
        try: archive_scrape()
        except Exception as e: LOG.append([time.time(), "archive: " + str(e)[:60]])
        try: kyubi_scrape()
        except Exception as e: LOG.append([time.time(), "kyubi: " + str(e)[:60]])
        time.sleep(300)  # cada 5 min (kyubi en vivo)

def save_upload(data_url):   # imagen subida desde la PC/celular -> zenshin_img/xxxx.png (max 4 MB)
    m = re.match(r"^data:image/(png|jpeg|jpg|gif|webp);base64,(.+)$", data_url or "", re.S)
    if not m: raise ValueError("formato no permitido")
    b = base64.b64decode(m.group(2))
    if len(b) > 4 * 1024 * 1024: raise ValueError("imagen muy grande")
    ext = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"GIF8": "gif", b"RIFF": "webp"}
    e = next((v for k, v in ext.items() if b.startswith(k)), None)
    if not e: raise ValueError("no es una imagen")
    os.makedirs(IMGDIR, exist_ok=True); n = hashlib.sha1(b).hexdigest()[:16] + "." + e
    with open(os.path.join(IMGDIR, n), "wb") as f: f.write(b)
    return "/img/" + n

# ================= IDIOMAS: traduccion de toda la pagina =================
from urllib.parse import urlencode as _urlencode
I18N_SRC = ["Abre la página oficial del juego. 2) F12 → pestaña Console → pega el texto de abajo y Enter. 3) Haz clic en cualquier clan. El tracker aprende solo cómo pedir los miembros", "Actividad", "Actividad de ataque por hora (0–23, tu hora local", "Actividad por hora del día", "Actividad semanal", "ACTIVO", "Activo", "activo", "Activo hace", "Activos", "activos (5 min", "activos 1 h", "activos 1h", "activos 1m", "Actualizado el", "Actualizando", "Actualizar ahora", "Agregar capturas", "Agregar fila", "Agregar paquete", "Agregar premio", "Agregar revendedor", "Ahora", "ahora", "ahora en", "Al de arriba", "al puesto", "Al top", "alcanzará a", "Alertas a Discord (al celular", "Alertas de Discord", "alguien", "Anuncio", "Anuncio más nuevo (sin lista de premios", "anuncio oficial", "Apagado", "aportas el", "Aquí pasan en vivo los ataques, adelantamientos y puntos PvE", "arrástralas o toca", "Asistente de conexión", "ataca", "ataca a", "atacando", "Atacando ahora", "atacando ahora", "atacante no identificado", "Atacante no identificado (ningún clan subió", "Atacaste", "atacó", "Ataques", "Ataques identificados", "Ataques registrados", "Atk", "Atk (ventana actual", "Atk -1 (anterior", "ATRASADO", "Auto-conectado", "Avisar solo de", "avisarme cuando ese jugador haga puntos", "Avisarme cuando haga puntos", "Avisarme de sus partidas", "aviso al instante con su REP y puesto", "Avisos", "Avisos en pantalla", "Ayudaste a golpear a", "Aún no hay ataques detectados", "Aún no hay conexión con los miembros. Mira la pestaña", "Aún no hay datos para probar", "Aún no hay jugadores cargados", "Aún no hay revendedores. Toca", "Aún no hay rivalidades detectadas (se afina con el tiempo", "Aún no hay temporadas anteriores en la página", "Aún no se cierra ninguna ventana. Al terminar cada bloque de 30 min aparecerá aquí (y en Discord", "Aún sin ataques registrados de este jugador (se detectan en vivo mientras el tracker está abierto", "Aún sin datos de hoy (se llenan mientras el tracker está abierto", "Aún sin eventos", "Bajaste", "Bajó", "bajó", "Batalla en vivo", "bloque de 30 min de ataque", "Borrar esta captura", "Borrar tus cambios y volver a las recompensas del anuncio oficial", "brecha", "buscando al atacante", "Buscar", "Buscar en la web", "Buscar jugador", "Buscar país o idioma", "Cada vez que alguien hace puntos", "Cada vez que subas capturas nuevas se calcula cuántos puntos hizo cada uno, quién subió de puesto, su ritmo por hora y si llega a la recompensa", "cae", "Cambiar idioma de la página", "Cambio", "Cambio de rank de mi clan", "Cambios de Roster", "Cancelar", "Captura guardada", "Capturas guardadas", "Cargando", "Cargando el lector de imágenes… (la primera vez tarda unos segundos", "Cargando miembros", "cargando miembros… (si no aparecen, mira 🔧 Conexión", "Carrera", "Carrera en vivo · Top", "Cerrar", "Champ", "Cinta de noticias en movimiento", "Clan", "clan", "Clan no encontrado", "Clan War", "Clanes", "clanes débiles o dormidos cerca de ti", "Clanes en la ventana actual", "clanes leídos · nivel prom", "Clanes monitoreados con", "Clanes que más ganaron", "Clanes que más perdieron", "Clanes que pierden REP", "Compra solo a revendedores de confianza: según los términos del juego, las compras son finales y no se devuelven", "Con 300k", "Con el filtro actual", "Con eso el tracker marca quién va ganando cada premio. Las imágenes pueden ser un enlace o una captura que subas con", "Conectado (aprendido", "conectando", "Conexión", "confiable", "Contacto: número de WhatsApp (+51 999 999 999), enlace (Discord, Facebook, Telegram…) o usuario. Varios separados por coma", "Contacto: WhatsApp, enlace de Discord/Facebook", "contra", "Copiado", "Copiar", "corte", "Corte del Top 30 en el tiempo", "Corte Top", "CSV", "Ctrl+V", "Cualquier clan empieza a atacar (1 aviso por ráfaga, sobre el umbral", "Cuando atacan a tu clan", "Cuando empiece una ronda nueva (los puntos se reinician", "Cuando subas dos capturas en momentos distintos verás aquí quién hizo puntos", "Cuando un clan ataca", "Cuando un miembro se va, su REP sale del clan. Esta barra baja a medida que el clan vuelve a ganar puntos atacando", "Cuándo", "Cuándo tomaste la captura: sirve para calcular el ritmo por hora", "Cómo actualizar el ranking", "de", "de nivel 100 · mostrando", "de score", "Dejaste de seguir a", "del", "del clan", "dentro", "Descarga datos.json para publicarlo en la página web", "desde", "desde medianoche (hora de tu PC", "Diagnóstico", "Discord", "Dom", "donde va el clan", "Día", "editado por ti", "Editar", "Editar precios", "Editar revendedores", "Ej: Clan War Season", "Ej: Season 4 – Round", "El sitio está tardando en responder", "El sitio responde con error", "El sonido está apagado: actívalo para oír los avisos", "El tracker lee los miembros de todos los clanes al conectarse (tarda unos 40 s). Si sigue vacío, mira 🩺 Diagnóstico en 🔧 Conexión", "El tracker lee rango, jugador y puntos; tú revisas y guardas", "elegir", "Elige tu clan y aquí verás tu panel de defensa: quién te ataca, cuánto te falta para subir y quién te persigue", "Empezar una ronda nueva? Se borran todas las capturas guardadas del Kyubi", "en 5 min", "En el juego abre el evento", "en línea", "en PvE", "en PvP", "EN VIVO", "en vivo, se actualiza cada 2-3 s", "Enlace", "Enlace del anuncio", "Entradas y salidas de las últimas 24 h (se detectan mientras el tracker está abierto", "Entradas y salidas de miembros (mi clan y", "entró", "entró a", "entró al ranking", "entró al ranking PvE", "Entró al Top 30 (premio especial", "er lugar", "Error miembros", "Escribe la condición como en el juego", "Escribir a mano", "Escritorio", "Escritorio: aviso de Windows aunque la pestaña esté minimizada. El navegador solo deja sonar audio después de un clic en la página", "Español", "Esperando ataques", "Esperando partidas", "Esperando puntos PvE", "estable", "Estado", "estas recompensas son del anuncio anterior y podrían cambiar", "este clan no daría aviso", "Estimación lineal, puede cambiar mucho: los clanes suelen acelerar al final", "estimado", "ESTÁ ATACANDO", "está atacando", "está atacando. Reputación", "está sangrando", "Event Points", "Event Points por hora", "eventos · conexión", "exacto", "Exportar para la web", "Facebook", "Falta", "faltan", "Farmer del momento", "Fecha final de temporada", "Final", "Final de temporada", "Final de temporada (si mantienen el ritmo de la última hora", "Flechas", "ganó", "Golpe más grande", "Guardando", "Guardar", "Guardar captura", "Guardar webhook", "Guerra", "h (tu hora · puede cruzar la medianoche, ej", "ha atacado a", "hace", "Hace cuánto llegó la última lectura del sitio y cuánto tardó en responder. Si sale ATRASADO, el sitio está lento o caído", "Hace falta ver al jugador durante al menos 6 h", "haga puntos", "hora", "Hora de la captura", "Hora más caliente", "Hora pico", "horas", "Hoy", "hoy", "ID del clan (ej", "identificando", "identificando atacante", "Idiomas", "Imagen", "Imagen (enlace, opcional", "imagen no válida", "Imagen principal", "Imagen subida", "Inactivos", "Instagram", "Jue", "Jugador", "Jugadores", "jugadores", "jugadores activos (15 min", "Jugadores leídos", "Jugadores por clan (Top Burn", "Jugadores que más atacaron", "jugando", "Jugando (15 min", "Jugando (5 min", "kyrt tr", "Kyubi", "Kyubi · Tailed Beast Raid", "La otra lectura dio este número", "La página pública solo muestra clanes. Los miembros salen al hacer clic en un clan. Abre la página oficial → F12 → Network → haz clic en un clan → copia la URL de esa petición y pégala aquí (usa", "La página ya muestra", "La web oficial no publica precios. Toca", "La web oficial todavía no publica las recompensas de Clan War. Toca", "las diferencias más cerradas", "Las filas en amarillo pueden tener un número mal leído: compáralas con tu captura", "Las ganancias coinciden exactamente con la pérdida", "Lectura", "lectura", "Lectura cada", "lecturas · miembros leídos", "Leyendo captura", "Logo / foto", "Los datos están atrasados: la última lectura buena fue hace", "lose", "Lun", "Líder", "líder", "manual", "Mapa de guerra", "Mar", "Marcado como confiable por ti", "Master", "Mayor subida de una vez", "Mejor racha", "Mejores jugadores de la ventana", "Mi clan", "Mi clan está atacando (sobre el umbral", "Mi clan sangra / ataques grandes", "Miembros", "miembros", "miembros de", "min", "min atrás", "min · lectura", "misma WiFi", "Mié", "Modo alarma: borde rojo en toda la pantalla + sirena (si el sonido está activo", "Modo claro / oscuro", "Moneda", "Monitorear este clan", "Mostrar", "Mostrar / ocultar el radar en vivo", "MVP 5 min", "Más subió", "Más victorias hoy", "Métodos de pago (Yape, PayPal", "Nadie activo en este intervalo (o aún no hay conexión con los miembros: mira 🔧 Conexión", "Nadie atacando en los últimos 5 min", "Nadie ha ganado REP en esta ventana todavía", "Nadie sumó puntos PvE en 10 min", "nadie todavía", "necesita", "necesitas el webhook de abajo", "Ninguna presa clara ahora mismo", "ninguno aún", "Ninguno detectado (la página no los expone en su JS", "Ningún adelantamiento previsto en las próximas 4 h con el ritmo actual", "Ningún clan atacando en los últimos 2 min", "Ningún clan sangra (últimos 10 min", "Ningún clan tiene reputación pendiente por recuperar", "Nivel", "No encontre la tabla de clanes (la pagina podria cargarse con JavaScript", "No encontré el ranking en esa imagen. Usa capturas de la ventana “Ranking List” del evento", "No encontré ese ID: elige tu clan en la lista", "No encontré la tabla PvE en la página (o todavía está cargando", "No encontré la tabla PvP en la página", "No hay nada que guardar", "No identificado", "No se pudo cargar el lector de imágenes. Revisa tu conexión a internet", "No se pudo exportar", "No se pudo guardar", "No se pudo leer", "No se pudo subir", "No se pudo subir la imagen", "No se pudo traducir ahora", "Nombre", "Nota / bonus", "Notas", "noticias", "Nueva ronda", "o elígelo", "O pega aquí el ranking como texto", "objetivo sin identificar", "Objetivos", "Oct", "Ocultar", "Ofensivas · a quién atacan", "Ordenar: Nivel", "Ordenar: REP", "Ordenar: ⚔ Ventana", "otros", "Palmarés por clan", "Pantalla", "Paquetes oficiales recientes", "para agregarlos con su contacto (WhatsApp, Discord, Facebook", "para cargarlas (con imagen) y el tracker marcará qué clanes las están ganando", "para la final", "Para subir", "Para subir al", "Partidas (5 min", "Pasó los 300.000 (premio por score", "País", "Peleas por el puesto", "Perdiste", "perdió", "perdió a", "Permiso de notificaciones denegado", "Período", "pocos activos", "ponlo dentro de la carpeta de la página y vuelve a subirla a Netlify", "por debajo", "por encima", "Posibles endpoints detectados en el sitio", "Precio", "Precios de tokens", "Premio", "Presas recomendadas", "primero", "Probar avisos", "Proyección", "prueba", "pruebas", "Próximos adelantamientos", "Próximos adelantamientos (ritmo actual", "Puedes corregir, quitar o agregar filas. También puedes pegar texto (una línea por jugador", "puedes fijar la fecha final en 🔧 Conexión", "Puesto", "Puestos", "puntos", "Puntos en 1 h", "Puntos en 5 min", "Puntos estimados al terminar el evento si sigue a este ritmo", "Puntos ganados desde la captura anterior", "Puntos PvE hoy", "Puntos que le faltan para alcanzar al de arriba", "PvE", "PvE ahora", "PvE EN VIVO", "PvE en vivo", "PvE · más puntos hoy", "PvE: entra al Top 30, pasa 300k o suma más del umbral PvE", "PvP", "PvP EN VIVO", "PvP · más victorias hoy", "Pégalas aquí con", "quedan", "Quitar", "Quién ataca a quién", "Quién hizo puntos", "Racha", "racha", "Racha de champ", "Radar", "radar", "Radar en vivo arriba (atacando ahora · batalla · PvE", "Rank", "ranking de Event Points", "Ranking List", "Recibirás avisos aunque la pestaña esté en segundo plano", "Recompensas", "Recompensas guardadas", "reconectando", "recuperado", "recuperado por completo", "Registro de mi clan", "Reintentar", "Reintentar auto-detección", "REP", "Rep", "REP final estimada", "REP ganada (todos", "REP ganada por hora", "REP pendiente por recuperar", "REP perdida (todos", "REP por ráfaga", "Reputación", "Reputación en este periodo", "Reputación ganada por día (últimos", "Reputación perdida por salidas", "Reputación perdida por salidas: falta por recuperar", "Resumen", "Resumen de cada ventana de 30 min", "Resumen diario (medianoche UTC", "Resúmenes de ventanas cerradas", "Revendedores", "Revisa y guarda", "Ritmo", "Rivalidades", "Round", "Récords de hoy", "s (en vivo", "s (🚀 turbo", "s turbo", "Salidas", "salió", "salió de", "sangra", "SANGRANDO", "sangrando", "Sangrando · quién los ataca", "sangró", "Score", "Se descargó", "Se fue con", "Se guarda en tu PC (archivo", "se unió a", "Season", "seguidas", "Seguir a este jugador", "Según la fecha del anuncio", "Sesión", "Si la Clan War terminara ahora, tu clan ganaría", "si no abre, permite Python en el firewall de Windows", "Si ya se sabe quién atacó, verás un solo aviso (el del atacante), salvo que la víctima sea tu clan o uno con", "Sin actividad todavía", "Sin ataques de jugadores en este periodo (¿configuraste la pestaña 🔧 Conexión", "Sin ataques vistos", "Sin auto-conexion", "Sin cambios en las últimas 24 h", "Sin combates en este momento", "sin conectar", "SIN CONEXIÓN", "Sin configurar", "sin datos", "sin datos de jugadores", "Sin datos suficientes todavía para graficar este periodo", "Sin datos todavía", "Sin errores recientes", "Sin miembros cargados (mira 🔧 Conexión", "Sin movimientos de miembros todavía (se detectan mientras el tracker está abierto y conectado a los miembros", "Sin ofensivas activas", "sin precios aún", "Sin respuesta del servidor", "Sin resultados", "sobre", "solo activos", "solo activos (15 min", "Solo jugadores con", "Solo jugadores del Top", "Solo mi clan y", "solo si la auto-detección falla", "Solo ★ y momentos clave (Top 30, 300k, podio", "Sonido", "Sonido ON", "Sonido y voz", "Subida", "SUBIENDO", "Subir imagen desde tu PC o celular", "Subiste", "Subió", "subió", "Suelta aquí las capturas del ranking o pégalas con", "Sugeridos", "superó a", "Sáb", "Tarifa (ej. 1.000 tokens → S", "TDY Rale", "TE ATACAN", "Te atacan", "Te aviso cada vez que", "te persigue a", "Telegram", "Temporada", "Temporada: calculando", "Temporadas", "Tendencia", "Termina", "termina en", "terminó", "TEXTAREA", "TikTok", "toca para cerrar", "Todas las partidas", "Todavía no hay recompensas publicadas para esta ronda", "Todavía no hay recompensas publicadas para este evento", "Todo", "Todos los clanes", "Todos los países", "Tokens", "Tokens (ej", "Tokens y revendedores", "Tokens y revendedores guardados", "Toma captura de pantalla de cada página (1/10, 2/10…). La del botón “Rank 1 - 100” trae además los cortes del puesto 30 y", "Top", "top", "Top Burn", "Total", "total", "Total ganado", "traducción", "Traducción automática · los nombres de clanes y jugadores no se traducen", "TU CLAN", "Tu clan", "Tu clan bajó", "Tu clan subió", "Tu navegador no permite avisos de escritorio", "Tu nombre en el clan", "Título", "Umbral de REP", "Umbral PvE", "una por línea", "Usa el asistente en", "Vas de líder", "Ventaja", "Ventana", "ventana", "Ventana rápida", "venía de", "Ver", "Ver mis estadísticas", "Ver todo", "Vibrar el celular", "victorias", "Vie", "Vigilar a este jugador (aviso cuando ataque", "Visto", "Volver", "Volver a lo oficial", "Volver al español", "Voz", "Voz activada", "Voz ON", "WhatsApp", "win", "y agrega los paquetes que conoces (ej. 1.000 tokens → 5 USD", "y se ve igual en el celular. Deja una fila vacía para no usarla", "Ya recuperadas", "Ábrelo en tu celular", "Última", "Última captura", "última hace", "Última hora", "última hora · el % es la seguridad de que sea ese clan", "última semana", "Últimas 6 horas (barras de 15 min", "Últimas entradas y salidas", "Últimas partidas", "Último ataque hace", "últimos", "Últimos puntos"]   # textos de la interfaz
I18N_BUILTIN = {"en": {"Abre la página oficial del juego. 2) F12 → pestaña Console → pega el texto de abajo y Enter. 3) Haz clic en cualquier clan. El tracker aprende solo cómo pedir los miembros": "Open the official game page. 2) F12 → Console tab → paste the text below and press Enter. 3) Click on any clan. The tracker learns on its own how to request the members", "Actividad": "Activity", "Actividad de ataque por hora (0–23, tu hora local": "Attack activity per hour (0–23, your local time", "Actividad por hora del día": "Activity by hour of day", "Actividad semanal": "Weekly activity", "ACTIVO": "ACTIVE", "Activo": "Active", "activo": "active", "Activo hace": "Running for", "Activos": "Active", "activos (5 min": "active (5 min", "activos 1 h": "active 1 h", "activos 1h": "active 1h", "activos 1m": "active 1m", "Actualizado el": "Updated on", "Actualizando": "Refreshing", "Actualizar ahora": "Refresh now", "Agregar paquete": "Add package", "Agregar premio": "Add reward", "Agregar revendedor": "Add reseller", "Ahora": "Now", "ahora": "now", "ahora en": "now in", "Al de arriba": "To the one above", "al puesto": "to position", "Al top": "To top", "alcanzará a": "will catch", "Alertas a Discord (al celular": "Discord alerts (to your phone", "Alertas de Discord": "Discord alerts", "alguien": "someone", "Anuncio": "Announcement", "Anuncio más nuevo (sin lista de premios": "Newer announcement (no reward list", "anuncio oficial": "official announcement", "Apagado": "Off", "aportas el": "you contribute", "Aquí pasan en vivo los ataques, adelantamientos y puntos PvE": "Live attacks, overtakes and PvE points scroll by here", "Asistente de conexión": "Connection wizard", "ataca": "attacks", "ataca a": "attacks", "atacando": "attacking", "Atacando ahora": "Attacking now", "atacando ahora": "attacking now", "atacante no identificado": "attacker not identified", "Atacante no identificado (ningún clan subió": "Attacker not identified (no clan went up", "Atacaste": "You attacked", "atacó": "attacked", "Ataques": "Attacks", "Ataques identificados": "Identified attacks", "Ataques registrados": "Recorded attacks", "Atk": "Atk", "Atk (ventana actual": "Atk (current window", "Atk -1 (anterior": "Atk -1 (previous", "ATRASADO": "DELAYED", "Auto-conectado": "Auto-connected", "Avisar solo de": "Notify only from", "avisarme cuando ese jugador haga puntos": "notify me when that player scores", "Avisarme cuando haga puntos": "Notify me when they score", "Avisarme de sus partidas": "Notify me of their matches", "aviso al instante con su REP y puesto": "instant alert with their REP and rank", "Avisos": "Alerts", "Avisos en pantalla": "On-screen alerts", "Ayudaste a golpear a": "You helped hit", "Aún no hay ataques detectados": "No attacks detected yet", "Aún no hay conexión con los miembros. Mira la pestaña": "Not connected to the members yet. Check the tab", "Aún no hay datos para probar": "No data to test yet", "Aún no hay jugadores cargados": "No players loaded yet", "Aún no hay revendedores. Toca": "No resellers yet. Tap", "Aún no hay rivalidades detectadas (se afina con el tiempo": "No rivalries detected yet (it gets better over time", "Aún no hay temporadas anteriores en la página": "No previous seasons on the page yet", "Aún no se cierra ninguna ventana. Al terminar cada bloque de 30 min aparecerá aquí (y en Discord": "No window has closed yet. When each 30-min block ends it will show up here (and on Discord", "Aún sin ataques registrados de este jugador (se detectan en vivo mientras el tracker está abierto": "No attacks recorded for this player yet (they are detected live while the tracker is open", "Aún sin datos de hoy (se llenan mientras el tracker está abierto": "No data for today yet (it fills in while the tracker is open", "Aún sin eventos": "No events yet", "Bajaste": "You dropped", "Bajó": "Dropped", "bajó": "dropped", "Batalla en vivo": "Live battle", "bloque de 30 min de ataque": "30-min attack block", "Borrar tus cambios y volver a las recompensas del anuncio oficial": "Delete your changes and go back to the official announcement's rewards", "brecha": "gap", "buscando al atacante": "looking for the attacker", "Buscar": "Search", "Buscar en la web": "Search the website", "Buscar jugador": "Search player", "Buscar país o idioma": "Search country or language", "Cada vez que alguien hace puntos": "Every time someone scores", "cae": "falls", "Cambiar idioma de la página": "Change page language", "Cambio": "Change", "Cambio de rank de mi clan": "My clan's rank change", "Cambios de Roster": "Roster changes", "Cancelar": "Cancel", "Cargando": "Loading", "Cargando miembros": "Loading members", "cargando miembros… (si no aparecen, mira 🔧 Conexión": "loading members… (if they don't appear, check 🔧 Connection", "Carrera": "Race", "Carrera en vivo · Top": "Live race · Top", "Cerrar": "Close", "Champ": "Champ", "Cinta de noticias en movimiento": "Scrolling news ticker", "Clan": "Clan", "clan": "clan", "Clan no encontrado": "Clan not found", "Clan War": "Clan War", "Clanes": "Clans", "clanes débiles o dormidos cerca de ti": "weak or sleeping clans near you", "Clanes en la ventana actual": "Clans in the current window", "clanes leídos · nivel prom": "clans read · avg level", "Clanes monitoreados con": "Clans monitored with", "Clanes que más ganaron": "Clans that gained the most", "Clanes que más perdieron": "Clans that lost the most", "Clanes que pierden REP": "Clans losing REP", "Compra solo a revendedores de confianza: según los términos del juego, las compras son finales y no se devuelven": "Only buy from trusted resellers: according to the game's terms, purchases are final and non-refundable", "Con 300k": "With 300k", "Con el filtro actual": "With the current filter", "Con eso el tracker marca quién va ganando cada premio. Las imágenes pueden ser un enlace o una captura que subas con": "With that, the tracker marks who is winning each reward. Images can be a link or a screenshot you upload with", "Conectado (aprendido": "Connected (learned", "conectando": "connecting", "Conexión": "Connection", "confiable": "trusted", "Contacto: número de WhatsApp (+51 999 999 999), enlace (Discord, Facebook, Telegram…) o usuario. Varios separados por coma": "Contact: WhatsApp number (+51 999 999 999), link (Discord, Facebook, Telegram…) or username. Several separated by commas", "Contacto: WhatsApp, enlace de Discord/Facebook": "Contact: WhatsApp, Discord/Facebook link", "contra": "against", "Copiado": "Copied", "Copiar": "Copy", "corte": "cutoff", "Corte Top": "Top cutoff", "Cualquier clan empieza a atacar (1 aviso por ráfaga, sobre el umbral": "Any clan starts attacking (1 alert per burst, above the threshold", "Cuando atacan a tu clan": "When your clan is attacked", "Cuando un clan ataca": "When a clan attacks", "Cuando un miembro se va, su REP sale del clan. Esta barra baja a medida que el clan vuelve a ganar puntos atacando": "When a member leaves, their REP leaves the clan. This bar goes down as the clan earns points back by attacking", "Cuándo": "When", "de nivel 100 · mostrando": "at level 100 · showing", "de score": "score", "Dejaste de seguir a": "You stopped following", "del clan": "of the clan", "dentro": "inside", "desde": "from", "desde medianoche (hora de tu PC": "since midnight (your PC's time", "Diagnóstico": "Diagnostics", "Dom": "Sun", "donde va el clan": "where the clan goes", "Día": "Day", "de": "of", "del": "from", "editado por ti": "edited by you", "Editar": "Edit", "Editar precios": "Edit prices", "Editar revendedores": "Edit resellers", "Ej: Clan War Season": "E.g.: Clan War Season", "Ej: Season 4 – Round": "E.g.: Season 4 – Round", "El sitio está tardando en responder": "The site is slow to respond", "El sitio responde con error": "The site is responding with an error", "El sonido está apagado: actívalo para oír los avisos": "Sound is off: turn it on to hear the alerts", "El tracker lee los miembros de todos los clanes al conectarse (tarda unos 40 s). Si sigue vacío, mira 🩺 Diagnóstico en 🔧 Conexión": "The tracker reads the members of every clan when it connects (takes about 40 s). If it stays empty, check 🩺 Diagnostics in 🔧 Connection", "elegir": "choose", "Elige tu clan y aquí verás tu panel de defensa: quién te ataca, cuánto te falta para subir y quién te persigue": "Pick your clan and here you'll see your defense panel: who attacks you, how much you need to move up and who is chasing you", "en 5 min": "in 5 min", "en línea": "online", "en PvE": "in PvE", "en PvP": "in PvP", "EN VIVO": "LIVE", "en vivo, se actualiza cada 2-3 s": "live, updates every 2-3 s", "Enlace": "Link", "Enlace del anuncio": "Announcement link", "Entradas y salidas de las últimas 24 h (se detectan mientras el tracker está abierto": "Joins and departures in the last 24 h (detected while the tracker is open", "Entradas y salidas de miembros (mi clan y": "Member joins and departures (my clan and", "entró": "joined", "entró a": "joined", "entró al ranking": "entered the ranking", "entró al ranking PvE": "entered the PvE ranking", "Entró al Top 30 (premio especial": "Entered the Top 30 (special reward", "er lugar": "st place", "Error miembros": "Members error", "Escribe la condición como en el juego": "Write the condition as in the game", "Escritorio": "Desktop", "Español": "Spanish", "Esperando ataques": "Waiting for attacks", "Esperando partidas": "Waiting for matches", "Esperando puntos PvE": "Waiting for PvE points", "estable": "stable", "Estado": "Status", "estas recompensas son del anuncio anterior y podrían cambiar": "these rewards are from the previous announcement and could change", "este clan no daría aviso": "this clan would not trigger an alert", "Estimación lineal, puede cambiar mucho: los clanes suelen acelerar al final": "Linear estimate, it can change a lot: clans usually speed up at the end", "estimado": "estimated", "ESTÁ ATACANDO": "IS ATTACKING", "está atacando": "is attacking", "está atacando. Reputación": "is attacking. Reputation", "está sangrando": "is bleeding", "eventos · conexión": "events · connection", "exacto": "exact", "Falta": "Gap", "faltan": "left", "Farmer del momento": "Top farmer right now", "Fecha final de temporada": "Season end date", "Final": "Final", "Final de temporada": "Season end", "Final de temporada (si mantienen el ritmo de la última hora": "Season end (if they keep the last hour's pace", "Flechas": "Arrows", "ganó": "won", "Golpe más grande": "Biggest hit", "Guardando": "Saving", "Guardar": "Save", "Guardar webhook": "Save webhook", "Guerra": "War", "h (tu hora · puede cruzar la medianoche, ej": "h (your time · can cross midnight, e.g", "ha atacado a": "has attacked", "hace": "ago", "Hace cuánto llegó la última lectura del sitio y cuánto tardó en responder. Si sale ATRASADO, el sitio está lento o caído": "How long ago the last site reading arrived and how long it took to respond. If it says DELAYED, the site is slow or down", "haga puntos": "scores", "hora": "hour", "Hora más caliente": "Hottest hour", "Hora pico": "Peak hour", "horas": "hours", "Hoy": "Today", "hoy": "today", "ID del clan (ej": "Clan ID (e.g", "identificando": "identifying", "identificando atacante": "identifying attacker", "Idiomas": "Languages", "Imagen": "Image", "Imagen (enlace, opcional": "Image (link, optional", "Imagen principal": "Main image", "Imagen subida": "Image uploaded", "Inactivos": "Inactive", "Jue": "Thu", "Jugador": "Player", "Jugadores": "Players", "jugadores": "players", "jugadores activos (15 min": "active players (15 min", "Jugadores por clan (Top Burn": "Players per clan (Top Burn", "Jugadores que más atacaron": "Players who attacked the most", "jugando": "playing", "Jugando (15 min": "Playing (15 min", "Jugando (5 min": "Playing (5 min", "La página pública solo muestra clanes. Los miembros salen al hacer clic en un clan. Abre la página oficial → F12 → Network → haz clic en un clan → copia la URL de esa petición y pégala aquí (usa": "The public page only shows clans. Members appear when you click a clan. Open the official page → F12 → Network → click a clan → copy that request's URL and paste it here (use", "La página ya muestra": "The page already shows", "La web oficial no publica precios. Toca": "The official website doesn't publish prices. Tap", "La web oficial todavía no publica las recompensas de Clan War. Toca": "The official website doesn't publish the Clan War rewards yet. Tap", "las diferencias más cerradas": "the closest gaps", "Las ganancias coinciden exactamente con la pérdida": "The gains match the loss exactly", "Lectura": "Reading", "lectura": "reading", "Lectura cada": "Reading every", "lecturas · miembros leídos": "readings · members read", "Logo / foto": "Logo / photo", "Los datos están atrasados: la última lectura buena fue hace": "The data is delayed: the last good reading was", "Lun": "Mon", "líder": "leader", "manual": "manual", "Mapa de guerra": "War map", "Mar": "Tue", "Marcado como confiable por ti": "Marked as trusted by you", "Master": "Master", "Mayor subida de una vez": "Biggest single gain", "Mejor racha": "Best streak", "Mejores jugadores de la ventana": "Best players of the window", "Mi clan": "My clan", "Mi clan está atacando (sobre el umbral": "My clan is attacking (above the threshold", "Mi clan sangra / ataques grandes": "My clan bleeds / big attacks", "Miembros": "Members", "miembros": "members", "miembros de": "members of", "min": "min", "min atrás": "min ago", "min · lectura": "min · reading", "misma WiFi": "same WiFi", "Mié": "Wed", "Modo alarma: borde rojo en toda la pantalla + sirena (si el sonido está activo": "Alarm mode: red border around the whole screen + siren (if sound is on", "Modo claro / oscuro": "Light / dark mode", "Moneda": "Currency", "Monitorear este clan": "Monitor this clan", "Mostrar": "Show", "Mostrar / ocultar el radar en vivo": "Show / hide the live radar", "MVP 5 min": "MVP 5 min", "Más victorias hoy": "Most wins today", "Métodos de pago (Yape, PayPal": "Payment methods (Yape, PayPal", "Nadie activo en este intervalo (o aún no hay conexión con los miembros: mira 🔧 Conexión": "Nobody active in this interval (or not connected to the members yet: check 🔧 Connection", "Nadie atacando en los últimos 5 min": "Nobody attacking in the last 5 min", "Nadie ha ganado REP en esta ventana todavía": "Nobody has gained REP in this window yet", "Nadie sumó puntos PvE en 10 min": "Nobody scored PvE points in 10 min", "nadie todavía": "nobody yet", "necesita": "needs", "necesitas el webhook de abajo": "you need the webhook below", "Ninguna presa clara ahora mismo": "No clear prey right now", "ninguno aún": "none yet", "Ninguno detectado (la página no los expone en su JS": "None detected (the page doesn't expose them in its JS", "Ningún adelantamiento previsto en las próximas 4 h con el ritmo actual": "No overtakes expected in the next 4 h at the current pace", "Ningún clan atacando en los últimos 2 min": "No clan attacking in the last 2 min", "Ningún clan sangra (últimos 10 min": "No clan is bleeding (last 10 min", "Ningún clan tiene reputación pendiente por recuperar": "No clan has reputation left to recover", "Nivel": "Level", "No encontre la tabla de clanes (la pagina podria cargarse con JavaScript": "Couldn't find the clan table (the page might load with JavaScript", "No encontré ese ID: elige tu clan en la lista": "I couldn't find that ID: choose your clan from the list", "No encontré la tabla PvE en la página (o todavía está cargando": "I couldn't find the PvE table on the page (or it's still loading", "No encontré la tabla PvP en la página": "I couldn't find the PvP table on the page", "No identificado": "Not identified", "No se pudo guardar": "Couldn't save", "No se pudo leer": "Couldn't read", "No se pudo subir": "Couldn't upload", "No se pudo subir la imagen": "Couldn't upload the image", "No se pudo traducir ahora": "Couldn't translate right now", "Nombre": "Name", "Nota / bonus": "Note / bonus", "Notas": "Notes", "noticias": "news", "o elígelo": "or choose it", "objetivo sin identificar": "target not identified", "Objetivos": "Targets", "Ocultar": "Hide", "Ofensivas · a quién atacan": "Offensives · who they attack", "Ordenar: Nivel": "Sort: Level", "Ordenar: REP": "Sort: REP", "Ordenar: ⚔ Ventana": "Sort: ⚔ Window", "otros": "others", "Palmarés por clan": "Honors by clan", "Pantalla": "Screen", "Paquetes oficiales recientes": "Recent official packages", "para agregarlos con su contacto (WhatsApp, Discord, Facebook": "to add them with their contact (WhatsApp, Discord, Facebook", "para cargarlas (con imagen) y el tracker marcará qué clanes las están ganando": "to add them (with images) and the tracker will mark which clans are winning them", "para la final": "until the end", "Para subir": "To move up", "Para subir al": "To move up to", "Partidas (5 min": "Matches (5 min", "Pasó los 300.000 (premio por score": "Passed 300,000 (score reward", "País": "Country", "Peleas por el puesto": "Fights for the spot", "Perdiste": "You lost", "perdió": "lost", "perdió a": "lost", "Permiso de notificaciones denegado": "Notification permission denied", "Período": "Period", "pocos activos": "few active", "por debajo": "below", "por encima": "above", "Posibles endpoints detectados en el sitio": "Possible endpoints detected on the site", "Precio": "Price", "Precios de tokens": "Token prices", "Premio": "Reward", "Presas recomendadas": "Recommended prey", "primero": "first", "Probar avisos": "Test alerts", "prueba": "test", "pruebas": "tries", "Próximos adelantamientos": "Upcoming overtakes", "Próximos adelantamientos (ritmo actual": "Upcoming overtakes (current pace", "puedes fijar la fecha final en 🔧 Conexión": "you can set the end date in 🔧 Connection", "Puesto": "Rank", "Puestos": "Ranks", "puntos": "points", "Puntos en 1 h": "Points in 1 h", "Puntos en 5 min": "Points in 5 min", "Puntos PvE hoy": "PvE points today", "PvE ahora": "PvE now", "PvE EN VIVO": "PvE LIVE", "PvE en vivo": "Live PvE", "PvE · más puntos hoy": "PvE · most points today", "PvE: entra al Top 30, pasa 300k o suma más del umbral PvE": "PvE: enters the Top 30, passes 300k or scores more than the PvE threshold", "PvP EN VIVO": "PvP LIVE", "PvP · más victorias hoy": "PvP · most wins today", "quedan": "left", "Quitar": "Remove", "Quién ataca a quién": "Who attacks whom", "Racha": "Streak", "racha": "streak", "Racha de champ": "Champion streak", "Radar": "Radar", "radar": "radar", "Radar en vivo arriba (atacando ahora · batalla · PvE": "Live radar on top (attacking now · battle · PvE", "Rank": "Rank", "Recibirás avisos aunque la pestaña esté en segundo plano": "You'll get alerts even if the tab is in the background", "Recompensas": "Rewards", "Recompensas guardadas": "Rewards saved", "reconectando": "reconnecting", "recuperado": "recovered", "recuperado por completo": "fully recovered", "Registro de mi clan": "My clan's log", "Reintentar": "Retry", "Reintentar auto-detección": "Retry auto-detection", "REP final estimada": "Estimated final REP", "REP ganada (todos": "REP gained (all", "REP ganada por hora": "REP gained per hour", "REP pendiente por recuperar": "REP left to recover", "REP perdida (todos": "REP lost (all", "REP por ráfaga": "REP per burst", "Reputación": "Reputation", "Reputación en este periodo": "Reputation in this period", "Reputación ganada por día (últimos": "Reputation gained per day (last", "Reputación perdida por salidas": "Reputation lost to departures", "Reputación perdida por salidas: falta por recuperar": "Reputation lost to departures: still to recover", "Resumen": "Summary", "Resumen de cada ventana de 30 min": "Summary of each 30-min window", "Resumen diario (medianoche UTC": "Daily summary (UTC midnight", "Resúmenes de ventanas cerradas": "Summaries of closed windows", "Revendedores": "Resellers", "Ritmo": "Pace", "Rivalidades": "Rivalries", "Récords de hoy": "Today's records", "s (en vivo": "s (live", "Salidas": "Departures", "salió": "left", "salió de": "left", "sangra": "bleeds", "SANGRANDO": "BLEEDING", "sangrando": "bleeding", "Sangrando · quién los ataca": "Bleeding · who attacks them", "sangró": "bled", "Se fue con": "Left with", "Se guarda en tu PC (archivo": "It's saved on your PC (file", "se unió a": "joined", "seguidas": "in a row", "Según la fecha del anuncio": "According to the announcement date", "Sesión": "Session", "Si la Clan War terminara ahora, tu clan ganaría": "If the Clan War ended now, your clan would win", "si no abre, permite Python en el firewall de Windows": "if it doesn't open, allow Python in the Windows firewall", "Si ya se sabe quién atacó, verás un solo aviso (el del atacante), salvo que la víctima sea tu clan o uno con": "If the attacker is already known, you'll see a single alert (the attacker's), unless the victim is your clan or one with", "Sin actividad todavía": "No activity yet", "Sin ataques de jugadores en este periodo (¿configuraste la pestaña 🔧 Conexión": "No player attacks in this period (did you set up the 🔧 Connection tab", "Sin ataques vistos": "No attacks seen", "Sin auto-conexion": "No auto-connection", "Sin cambios en las últimas 24 h": "No changes in the last 24 h", "Sin combates en este momento": "No fights right now", "sin conectar": "not connected", "SIN CONEXIÓN": "OFFLINE", "Sin configurar": "Not configured", "sin datos": "no data", "sin datos de jugadores": "no player data", "Sin datos suficientes todavía para graficar este periodo": "Not enough data yet to chart this period", "Sin datos todavía": "No data yet", "Sin errores recientes": "No recent errors", "Sin miembros cargados (mira 🔧 Conexión": "No members loaded (check 🔧 Connection", "Sin movimientos de miembros todavía (se detectan mientras el tracker está abierto y conectado a los miembros": "No member movements yet (they are detected while the tracker is open and connected to the members", "Sin ofensivas activas": "No active offensives", "sin precios aún": "no prices yet", "Sin respuesta del servidor": "No response from the server", "Sin resultados": "No results", "sobre": "over", "solo activos": "active only", "solo activos (15 min": "active only (15 min", "Solo jugadores con": "Only players with", "Solo jugadores del Top": "Only players in the Top", "Solo mi clan y": "Only my clan and", "solo si la auto-detección falla": "only if auto-detection fails", "Solo ★ y momentos clave (Top 30, 300k, podio": "Only ★ and key moments (Top 30, 300k, podium", "Sonido": "Sound", "Sonido ON": "Sound ON", "Sonido y voz": "Sound and voice", "Subir imagen desde tu PC o celular": "Upload an image from your PC or phone", "Subiste": "You moved up", "Subió": "Gained", "subió": "moved up", "Sugeridos": "Suggested", "superó a": "overtook", "Sáb": "Sat", "TE ATACAN": "YOU'RE UNDER ATTACK", "Te atacan": "You're being attacked", "Te aviso cada vez que": "I'll notify you every time", "te persigue a": "is chasing you at", "Temporada": "Season", "Temporada: calculando": "Season: calculating", "Temporadas": "Seasons", "Tendencia": "Trend", "termina en": "ends in", "terminó": "ended", "toca para cerrar": "tap to close", "Todas las partidas": "All matches", "Todavía no hay recompensas publicadas para esta ronda": "No rewards published for this round yet", "Todo": "All", "Todos los clanes": "All clans", "Todos los países": "All countries", "Tokens": "Tokens", "Tokens (ej": "Tokens (e.g", "Tokens y revendedores": "Tokens and resellers", "Tokens y revendedores guardados": "Tokens and resellers saved", "Top": "Top", "top": "top", "Top Burn": "Top Burn", "Total": "Total", "total": "total", "Total ganado": "Total gained", "traducción": "translation", "Traducción automática · los nombres de clanes y jugadores no se traducen": "Automatic translation · clan and player names are not translated", "TU CLAN": "YOUR CLAN", "Tu clan": "Your clan", "Tu clan bajó": "Your clan dropped", "Tu clan subió": "Your clan moved up", "Tu navegador no permite avisos de escritorio": "Your browser doesn't allow desktop notifications", "Tu nombre en el clan": "Your name in the clan", "Título": "Title", "Umbral de REP": "REP threshold", "Umbral PvE": "PvE threshold", "una por línea": "one per line", "Usa el asistente en": "Use the wizard in", "Vas de líder": "You're the leader", "Ventaja": "Lead", "Ventana": "Window", "ventana": "window", "Ventana rápida": "Quick window", "venía de": "came from", "Ver": "Show", "Ver mis estadísticas": "See my stats", "Ver todo": "See all", "Vibrar el celular": "Vibrate phone", "victorias": "wins", "Vie": "Fri", "Vigilar a este jugador (aviso cuando ataque": "Watch this player (alert when they attack", "Volver": "Back", "Volver a lo oficial": "Back to official", "Volver al español": "Back to Spanish", "Voz": "Voice", "Voz activada": "Voice on", "Voz ON": "Voice ON", "win": "win", "y agrega los paquetes que conoces (ej. 1.000 tokens → 5 USD": "and add the packages you know (e.g. 1,000 tokens → 5 USD", "y se ve igual en el celular. Deja una fila vacía para no usarla": "and looks the same on your phone. Leave a row empty to skip it", "Ya recuperadas": "Already recovered", "Ábrelo en tu celular": "Open it on your phone", "Última": "Last", "última hace": "last", "Última hora": "Last hour", "última hora · el % es la seguridad de que sea ese clan": "last hour · the % is how sure it is that clan", "última semana": "last week", "Últimas 6 horas (barras de 15 min": "Last 6 hours (15-min bars", "Últimas entradas y salidas": "Latest joins and departures", "Últimas partidas": "Latest matches", "Último ataque hace": "Last attack", "últimos": "last", "Últimos puntos": "Latest points", "Escritorio: aviso de Windows aunque la pestaña esté minimizada. El navegador solo deja sonar audio después de un clic en la página": "Desktop: Windows notification even if the tab is minimized. The browser only allows audio after a click on the page", "Tarifa (ej. 1.000 tokens → S": "Rate (e.g. 1,000 tokens → S", "Agregar capturas": "Add screenshots", "Agregar fila": "Add row", "arrástralas o toca": "drag them or tap", "Borrar esta captura": "Delete this screenshot", "Cada vez que subas capturas nuevas se calcula cuántos puntos hizo cada uno, quién subió de puesto, su ritmo por hora y si llega a la recompensa": "Every time you upload new screenshots it calculates how many points each player made, who moved up, their hourly pace and whether they reach the reward", "Captura guardada": "Screenshot saved", "Capturas guardadas": "Saved screenshots", "Cargando el lector de imágenes… (la primera vez tarda unos segundos": "Loading the image reader… (the first time takes a few seconds", "Corte del Top 30 en el tiempo": "Top 30 cutoff over time", "Cuando empiece una ronda nueva (los puntos se reinician": "When a new round starts (points are reset", "Cuando subas dos capturas en momentos distintos verás aquí quién hizo puntos": "When you upload two screenshots taken at different times you will see here who scored points", "Cuándo tomaste la captura: sirve para calcular el ritmo por hora": "When you took the screenshot: used to calculate the hourly pace", "Cómo actualizar el ranking": "How to update the ranking", "Descarga datos.json para publicarlo en la página web": "Download datos.json to publish it on the website", "El tracker lee rango, jugador y puntos; tú revisas y guardas": "The tracker reads rank, player and points; you review and save", "Empezar una ronda nueva? Se borran todas las capturas guardadas del Kyubi": "Start a new round? All saved Kyubi screenshots will be deleted", "En el juego abre el evento": "In the game open the event", "Escribir a mano": "Type it in", "Event Points": "Event Points", "Event Points por hora": "Event Points per hour", "Exportar para la web": "Export for the website", "Guardar captura": "Save screenshot", "Hace falta ver al jugador durante al menos 6 h": "The player must be tracked for at least 6 h", "Hora de la captura": "Screenshot time", "imagen no válida": "invalid image", "Jugadores leídos": "Players read", "Kyubi": "Kyubi", "Kyubi · Tailed Beast Raid": "Kyubi · Tailed Beast Raid", "La otra lectura dio este número": "The other reading gave this number", "Las filas en amarillo pueden tener un número mal leído: compáralas con tu captura": "Rows in yellow may have a misread number: compare them with your screenshot", "Leyendo captura": "Reading screenshot", "Líder": "Leader", "Más subió": "Biggest gain", "No encontré el ranking en esa imagen. Usa capturas de la ventana “Ranking List” del evento": "I couldn't find the ranking in that image. Use screenshots of the event's “Ranking List” window", "No hay nada que guardar": "Nothing to save", "No se pudo cargar el lector de imágenes. Revisa tu conexión a internet": "Couldn't load the image reader. Check your internet connection", "No se pudo exportar": "Couldn't export", "Nueva ronda": "New round", "O pega aquí el ranking como texto": "Or paste the ranking here as text", "ponlo dentro de la carpeta de la página y vuelve a subirla a Netlify": "put it inside the website folder and upload it to Netlify again", "Proyección": "Projection", "Puedes corregir, quitar o agregar filas. También puedes pegar texto (una línea por jugador": "You can fix, remove or add rows. You can also paste text (one line per player", "Puntos estimados al terminar el evento si sigue a este ritmo": "Estimated points at the end of the event if this pace continues", "Puntos ganados desde la captura anterior": "Points gained since the previous screenshot", "Puntos que le faltan para alcanzar al de arriba": "Points needed to catch the player above", "Pégalas aquí con": "Paste them here with", "Quién hizo puntos": "Who scored points", "ranking de Event Points": "Event Points ranking", "Ranking List": "Ranking List", "Revisa y guarda": "Review and save", "Se descargó": "Downloaded", "Seguir a este jugador": "Follow this player", "Subida": "Gain", "SUBIENDO": "CLIMBING", "Suelta aquí las capturas del ranking o pégalas con": "Drop the ranking screenshots here or paste them with", "TDY Rale": "TDY Rale", "kyrt tr": "kyrt tr", "Ctrl+V": "Ctrl+V", "Termina": "Ends", "Todavía no hay recompensas publicadas para este evento": "No rewards have been published for this event yet", "Toma captura de pantalla de cada página (1/10, 2/10…). La del botón “Rank 1 - 100” trae además los cortes del puesto 30 y": "Take a screenshot of each page (1/10, 2/10…). The one with the “Rank 1 - 100” button also shows the cutoffs for places 30 and", "Visto": "Seen", "Última captura": "Last screenshot"}, "pt": {"Abre la página oficial del juego. 2) F12 → pestaña Console → pega el texto de abajo y Enter. 3) Haz clic en cualquier clan. El tracker aprende solo cómo pedir los miembros": "Abra a página oficial do jogo. 2) F12 → aba Console → cole o texto abaixo e pressione Enter. 3) Clique em qualquer clã. O tracker aprende sozinho como pedir os membros", "Actividad": "Atividade", "Actividad de ataque por hora (0–23, tu hora local": "Atividade de ataque por hora (0–23, seu horário local", "Actividad por hora del día": "Atividade por hora do dia", "Actividad semanal": "Atividade semanal", "ACTIVO": "ATIVO", "Activo": "Ativo", "activo": "ativo", "Activo hace": "Ativo há", "Activos": "Ativos", "activos (5 min": "ativos (5 min", "activos 1 h": "ativos 1 h", "activos 1h": "ativos 1h", "activos 1m": "ativos 1m", "Actualizado el": "Atualizado em", "Actualizando": "Atualizando", "Actualizar ahora": "Atualizar agora", "Agregar paquete": "Adicionar pacote", "Agregar premio": "Adicionar prêmio", "Agregar revendedor": "Adicionar revendedor", "Ahora": "Agora", "ahora": "agora", "ahora en": "agora em", "Al de arriba": "Para o de cima", "al puesto": "para a posição", "Al top": "Para o top", "alcanzará a": "vai alcançar", "Alertas a Discord (al celular": "Alertas no Discord (no celular", "Alertas de Discord": "Alertas do Discord", "alguien": "alguém", "Anuncio": "Anúncio", "Anuncio más nuevo (sin lista de premios": "Anúncio mais novo (sem lista de prêmios", "anuncio oficial": "anúncio oficial", "Apagado": "Desligado", "aportas el": "você contribui com", "Aquí pasan en vivo los ataques, adelantamientos y puntos PvE": "Aqui passam ao vivo os ataques, ultrapassagens e pontos PvE", "Asistente de conexión": "Assistente de conexão", "ataca": "ataca", "ataca a": "ataca", "atacando": "atacando", "Atacando ahora": "Atacando agora", "atacando ahora": "atacando agora", "atacante no identificado": "atacante não identificado", "Atacante no identificado (ningún clan subió": "Atacante não identificado (nenhum clã subiu", "Atacaste": "Você atacou", "atacó": "atacou", "Ataques": "Ataques", "Ataques identificados": "Ataques identificados", "Ataques registrados": "Ataques registrados", "Atk": "Atq", "Atk (ventana actual": "Atq (janela atual", "Atk -1 (anterior": "Atq -1 (anterior", "ATRASADO": "ATRASADO", "Auto-conectado": "Conectado automaticamente", "Avisar solo de": "Avisar só das", "avisarme cuando ese jugador haga puntos": "me avisar quando esse jogador pontuar", "Avisarme cuando haga puntos": "Me avisar quando pontuar", "Avisarme de sus partidas": "Me avisar das partidas dele", "aviso al instante con su REP y puesto": "aviso na hora com a REP e a posição", "Avisos": "Avisos", "Avisos en pantalla": "Avisos na tela", "Ayudaste a golpear a": "Você ajudou a atacar", "Aún no hay ataques detectados": "Ainda não há ataques detectados", "Aún no hay conexión con los miembros. Mira la pestaña": "Ainda não há conexão com os membros. Veja a aba", "Aún no hay datos para probar": "Ainda não há dados para testar", "Aún no hay jugadores cargados": "Ainda não há jogadores carregados", "Aún no hay revendedores. Toca": "Ainda não há revendedores. Toque em", "Aún no hay rivalidades detectadas (se afina con el tiempo": "Ainda não há rivalidades detectadas (melhora com o tempo", "Aún no hay temporadas anteriores en la página": "Ainda não há temporadas anteriores na página", "Aún no se cierra ninguna ventana. Al terminar cada bloque de 30 min aparecerá aquí (y en Discord": "Nenhuma janela fechou ainda. Ao terminar cada bloco de 30 min aparecerá aqui (e no Discord", "Aún sin ataques registrados de este jugador (se detectan en vivo mientras el tracker está abierto": "Ainda sem ataques registrados deste jogador (são detectados ao vivo enquanto o tracker está aberto", "Aún sin datos de hoy (se llenan mientras el tracker está abierto": "Ainda sem dados de hoje (são preenchidos enquanto o tracker está aberto", "Aún sin eventos": "Ainda sem eventos", "Bajaste": "Você caiu", "Bajó": "Caiu", "bajó": "caiu", "Batalla en vivo": "Batalha ao vivo", "bloque de 30 min de ataque": "bloco de ataque de 30 min", "Borrar tus cambios y volver a las recompensas del anuncio oficial": "Apagar suas alterações e voltar aos prêmios do anúncio oficial", "brecha": "diferença", "buscando al atacante": "procurando o atacante", "Buscar": "Buscar", "Buscar en la web": "Buscar no site", "Buscar jugador": "Buscar jogador", "Buscar país o idioma": "Buscar país ou idioma", "Cada vez que alguien hace puntos": "Cada vez que alguém pontua", "cae": "cai", "Cambiar idioma de la página": "Mudar o idioma da página", "Cambio": "Mudança", "Cambio de rank de mi clan": "Mudança de rank do meu clã", "Cambios de Roster": "Mudanças no elenco", "Cancelar": "Cancelar", "Cargando": "Carregando", "Cargando miembros": "Carregando membros", "cargando miembros… (si no aparecen, mira 🔧 Conexión": "carregando membros… (se não aparecerem, veja 🔧 Conexão", "Carrera": "Corrida", "Carrera en vivo · Top": "Corrida ao vivo · Top", "Cerrar": "Fechar", "Champ": "Campeão", "Cinta de noticias en movimiento": "Faixa de notícias em movimento", "Clan": "Clã", "clan": "clã", "Clan no encontrado": "Clã não encontrado", "Clan War": "Clan War", "Clanes": "Clãs", "clanes débiles o dormidos cerca de ti": "clãs fracos ou parados perto de você", "Clanes en la ventana actual": "Clãs na janela atual", "clanes leídos · nivel prom": "clãs lidos · nível médio", "Clanes monitoreados con": "Clãs monitorados com", "Clanes que más ganaron": "Clãs que mais ganharam", "Clanes que más perdieron": "Clãs que mais perderam", "Clanes que pierden REP": "Clãs perdendo REP", "Compra solo a revendedores de confianza: según los términos del juego, las compras son finales y no se devuelven": "Compre só de revendedores de confiança: pelos termos do jogo, as compras são finais e não têm reembolso", "Con 300k": "Com 300k", "Con el filtro actual": "Com o filtro atual", "Con eso el tracker marca quién va ganando cada premio. Las imágenes pueden ser un enlace o una captura que subas con": "Com isso o tracker marca quem está ganhando cada prêmio. As imagens podem ser um link ou uma captura que você enviar com", "Conectado (aprendido": "Conectado (aprendido", "conectando": "conectando", "Conexión": "Conexão", "confiable": "confiável", "Contacto: número de WhatsApp (+51 999 999 999), enlace (Discord, Facebook, Telegram…) o usuario. Varios separados por coma": "Contato: número de WhatsApp (+51 999 999 999), link (Discord, Facebook, Telegram…) ou usuário. Vários separados por vírgula", "Contacto: WhatsApp, enlace de Discord/Facebook": "Contato: WhatsApp, link do Discord/Facebook", "contra": "contra", "Copiado": "Copiado", "Copiar": "Copiar", "corte": "corte", "Corte Top": "Corte do Top", "Cualquier clan empieza a atacar (1 aviso por ráfaga, sobre el umbral": "Qualquer clã começa a atacar (1 aviso por rajada, acima do limite", "Cuando atacan a tu clan": "Quando atacam o seu clã", "Cuando un clan ataca": "Quando um clã ataca", "Cuando un miembro se va, su REP sale del clan. Esta barra baja a medida que el clan vuelve a ganar puntos atacando": "Quando um membro sai, a REP dele sai do clã. Esta barra diminui à medida que o clã volta a ganhar pontos atacando", "Cuándo": "Quando", "de nivel 100 · mostrando": "no nível 100 · mostrando", "de score": "de score", "Dejaste de seguir a": "Você deixou de seguir", "del clan": "do clã", "dentro": "dentro", "desde": "a partir de", "desde medianoche (hora de tu PC": "desde a meia-noite (horário do seu PC", "Diagnóstico": "Diagnóstico", "Dom": "Dom", "donde va el clan": "onde vai o clã", "Día": "Dia", "de": "de", "del": "do", "editado por ti": "editado por você", "Editar": "Editar", "Editar precios": "Editar preços", "Editar revendedores": "Editar revendedores", "Ej: Clan War Season": "Ex.: Clan War Season", "Ej: Season 4 – Round": "Ex.: Season 4 – Round", "El sitio está tardando en responder": "O site está demorando para responder", "El sitio responde con error": "O site está respondendo com erro", "El sonido está apagado: actívalo para oír los avisos": "O som está desligado: ligue para ouvir os avisos", "El tracker lee los miembros de todos los clanes al conectarse (tarda unos 40 s). Si sigue vacío, mira 🩺 Diagnóstico en 🔧 Conexión": "O tracker lê os membros de todos os clãs ao conectar (leva uns 40 s). Se continuar vazio, veja 🩺 Diagnóstico em 🔧 Conexão", "elegir": "escolher", "Elige tu clan y aquí verás tu panel de defensa: quién te ataca, cuánto te falta para subir y quién te persigue": "Escolha seu clã e aqui você verá seu painel de defesa: quem te ataca, quanto falta para subir e quem está te perseguindo", "en 5 min": "em 5 min", "en línea": "online", "en PvE": "no PvE", "en PvP": "no PvP", "EN VIVO": "AO VIVO", "en vivo, se actualiza cada 2-3 s": "ao vivo, atualiza a cada 2-3 s", "Enlace": "Link", "Enlace del anuncio": "Link do anúncio", "Entradas y salidas de las últimas 24 h (se detectan mientras el tracker está abierto": "Entradas e saídas das últimas 24 h (detectadas enquanto o tracker está aberto", "Entradas y salidas de miembros (mi clan y": "Entradas e saídas de membros (meu clã e", "entró": "entrou", "entró a": "entrou em", "entró al ranking": "entrou no ranking", "entró al ranking PvE": "entrou no ranking PvE", "Entró al Top 30 (premio especial": "Entrou no Top 30 (prêmio especial", "er lugar": "º lugar", "Error miembros": "Erro nos membros", "Escribe la condición como en el juego": "Escreva a condição como no jogo", "Escritorio": "Área de trabalho", "Español": "Espanhol", "Esperando ataques": "Aguardando ataques", "Esperando partidas": "Aguardando partidas", "Esperando puntos PvE": "Aguardando pontos PvE", "estable": "estável", "Estado": "Status", "estas recompensas son del anuncio anterior y podrían cambiar": "estes prêmios são do anúncio anterior e podem mudar", "este clan no daría aviso": "este clã não daria aviso", "Estimación lineal, puede cambiar mucho: los clanes suelen acelerar al final": "Estimativa linear, pode mudar muito: os clãs costumam acelerar no final", "estimado": "estimado", "ESTÁ ATACANDO": "ESTÁ ATACANDO", "está atacando": "está atacando", "está atacando. Reputación": "está atacando. Reputação", "está sangrando": "está sangrando", "eventos · conexión": "eventos · conexão", "exacto": "exato", "Falta": "Falta", "faltan": "faltam", "Farmer del momento": "Farmer do momento", "Fecha final de temporada": "Data final da temporada", "Final": "Final", "Final de temporada": "Fim da temporada", "Final de temporada (si mantienen el ritmo de la última hora": "Fim da temporada (se mantiverem o ritmo da última hora", "Flechas": "Setas", "ganó": "ganhou", "Golpe más grande": "Maior golpe", "Guardando": "Salvando", "Guardar": "Salvar", "Guardar webhook": "Salvar webhook", "Guerra": "Guerra", "h (tu hora · puede cruzar la medianoche, ej": "h (seu horário · pode passar da meia-noite, ex", "ha atacado a": "atacou", "hace": "há", "Hace cuánto llegó la última lectura del sitio y cuánto tardó en responder. Si sale ATRASADO, el sitio está lento o caído": "Há quanto tempo chegou a última leitura do site e quanto demorou para responder. Se aparecer ATRASADO, o site está lento ou fora do ar", "haga puntos": "pontuar", "hora": "hora", "Hora más caliente": "Hora mais quente", "Hora pico": "Horário de pico", "horas": "horas", "Hoy": "Hoje", "hoy": "hoje", "ID del clan (ej": "ID do clã (ex", "identificando": "identificando", "identificando atacante": "identificando atacante", "Idiomas": "Idiomas", "Imagen": "Imagem", "Imagen (enlace, opcional": "Imagem (link, opcional", "Imagen principal": "Imagem principal", "Imagen subida": "Imagem enviada", "Inactivos": "Inativos", "Jue": "Qui", "Jugador": "Jogador", "Jugadores": "Jogadores", "jugadores": "jogadores", "jugadores activos (15 min": "jogadores ativos (15 min", "Jugadores por clan (Top Burn": "Jogadores por clã (Top Burn", "Jugadores que más atacaron": "Jogadores que mais atacaram", "jugando": "jogando", "Jugando (15 min": "Jogando (15 min", "Jugando (5 min": "Jogando (5 min", "La página pública solo muestra clanes. Los miembros salen al hacer clic en un clan. Abre la página oficial → F12 → Network → haz clic en un clan → copia la URL de esa petición y pégala aquí (usa": "A página pública só mostra clãs. Os membros aparecem ao clicar em um clã. Abra a página oficial → F12 → Network → clique em um clã → copie a URL dessa requisição e cole aqui (use", "La página ya muestra": "A página já mostra", "La web oficial no publica precios. Toca": "O site oficial não publica preços. Toque em", "La web oficial todavía no publica las recompensas de Clan War. Toca": "O site oficial ainda não publica os prêmios da Clan War. Toque em", "las diferencias más cerradas": "as diferenças mais apertadas", "Las ganancias coinciden exactamente con la pérdida": "Os ganhos batem exatamente com a perda", "Lectura": "Leitura", "lectura": "leitura", "Lectura cada": "Leitura a cada", "lecturas · miembros leídos": "leituras · membros lidos", "Logo / foto": "Logo / foto", "Los datos están atrasados: la última lectura buena fue hace": "Os dados estão atrasados: a última leitura boa foi há", "Lun": "Seg", "líder": "líder", "manual": "manual", "Mapa de guerra": "Mapa de guerra", "Mar": "Ter", "Marcado como confiable por ti": "Marcado como confiável por você", "Master": "Líder", "Mayor subida de una vez": "Maior subida de uma vez", "Mejor racha": "Melhor sequência", "Mejores jugadores de la ventana": "Melhores jogadores da janela", "Mi clan": "Meu clã", "Mi clan está atacando (sobre el umbral": "Meu clã está atacando (acima do limite", "Mi clan sangra / ataques grandes": "Meu clã sangra / ataques grandes", "Miembros": "Membros", "miembros": "membros", "miembros de": "membros de", "min": "min", "min atrás": "min atrás", "min · lectura": "min · leitura", "misma WiFi": "mesmo WiFi", "Mié": "Qua", "Modo alarma: borde rojo en toda la pantalla + sirena (si el sonido está activo": "Modo alarme: borda vermelha na tela toda + sirene (se o som estiver ligado", "Modo claro / oscuro": "Modo claro / escuro", "Moneda": "Moeda", "Monitorear este clan": "Monitorar este clã", "Mostrar": "Mostrar", "Mostrar / ocultar el radar en vivo": "Mostrar / ocultar o radar ao vivo", "MVP 5 min": "MVP 5 min", "Más victorias hoy": "Mais vitórias hoje", "Métodos de pago (Yape, PayPal": "Formas de pagamento (Pix, PayPal", "Nadie activo en este intervalo (o aún no hay conexión con los miembros: mira 🔧 Conexión": "Ninguém ativo neste intervalo (ou ainda sem conexão com os membros: veja 🔧 Conexão", "Nadie atacando en los últimos 5 min": "Ninguém atacando nos últimos 5 min", "Nadie ha ganado REP en esta ventana todavía": "Ninguém ganhou REP nesta janela ainda", "Nadie sumó puntos PvE en 10 min": "Ninguém somou pontos PvE em 10 min", "nadie todavía": "ninguém ainda", "necesita": "precisa", "necesitas el webhook de abajo": "você precisa do webhook abaixo", "Ninguna presa clara ahora mismo": "Nenhuma presa clara agora", "ninguno aún": "nenhum ainda", "Ninguno detectado (la página no los expone en su JS": "Nenhum detectado (a página não os expõe no JS", "Ningún adelantamiento previsto en las próximas 4 h con el ritmo actual": "Nenhuma ultrapassagem prevista nas próximas 4 h no ritmo atual", "Ningún clan atacando en los últimos 2 min": "Nenhum clã atacando nos últimos 2 min", "Ningún clan sangra (últimos 10 min": "Nenhum clã sangrando (últimos 10 min", "Ningún clan tiene reputación pendiente por recuperar": "Nenhum clã tem reputação pendente para recuperar", "Nivel": "Nível", "No encontre la tabla de clanes (la pagina podria cargarse con JavaScript": "Não encontrei a tabela de clãs (a página pode carregar com JavaScript", "No encontré ese ID: elige tu clan en la lista": "Não encontrei esse ID: escolha seu clã na lista", "No encontré la tabla PvE en la página (o todavía está cargando": "Não encontrei a tabela PvE na página (ou ainda está carregando", "No encontré la tabla PvP en la página": "Não encontrei a tabela PvP na página", "No identificado": "Não identificado", "No se pudo guardar": "Não foi possível salvar", "No se pudo leer": "Não foi possível ler", "No se pudo subir": "Não foi possível enviar", "No se pudo subir la imagen": "Não foi possível enviar a imagem", "No se pudo traducir ahora": "Não foi possível traduzir agora", "Nombre": "Nome", "Nota / bonus": "Nota / bônus", "Notas": "Notas", "noticias": "notícias", "o elígelo": "ou escolha", "objetivo sin identificar": "alvo não identificado", "Objetivos": "Alvos", "Ocultar": "Ocultar", "Ofensivas · a quién atacan": "Ofensivas · quem eles atacam", "Ordenar: Nivel": "Ordenar: Nível", "Ordenar: REP": "Ordenar: REP", "Ordenar: ⚔ Ventana": "Ordenar: ⚔ Janela", "otros": "outros", "Palmarés por clan": "Conquistas por clã", "Pantalla": "Tela", "Paquetes oficiales recientes": "Pacotes oficiais recentes", "para agregarlos con su contacto (WhatsApp, Discord, Facebook": "para adicioná-los com o contato (WhatsApp, Discord, Facebook", "para cargarlas (con imagen) y el tracker marcará qué clanes las están ganando": "para cadastrá-los (com imagem) e o tracker vai marcar quais clãs estão ganhando", "para la final": "para o final", "Para subir": "Para subir", "Para subir al": "Para subir para", "Partidas (5 min": "Partidas (5 min", "Pasó los 300.000 (premio por score": "Passou dos 300.000 (prêmio por score", "País": "País", "Peleas por el puesto": "Brigas pela posição", "Perdiste": "Você perdeu", "perdió": "perdeu", "perdió a": "perdeu", "Permiso de notificaciones denegado": "Permissão de notificações negada", "Período": "Período", "pocos activos": "poucos ativos", "por debajo": "abaixo", "por encima": "acima", "Posibles endpoints detectados en el sitio": "Possíveis endpoints detectados no site", "Precio": "Preço", "Precios de tokens": "Preços dos tokens", "Premio": "Prêmio", "Presas recomendadas": "Presas recomendadas", "primero": "primeiro", "Probar avisos": "Testar avisos", "prueba": "teste", "pruebas": "tentativas", "Próximos adelantamientos": "Próximas ultrapassagens", "Próximos adelantamientos (ritmo actual": "Próximas ultrapassagens (ritmo atual", "puedes fijar la fecha final en 🔧 Conexión": "você pode definir a data final em 🔧 Conexão", "Puesto": "Posição", "Puestos": "Posições", "puntos": "pontos", "Puntos en 1 h": "Pontos em 1 h", "Puntos en 5 min": "Pontos em 5 min", "Puntos PvE hoy": "Pontos PvE hoje", "PvE ahora": "PvE agora", "PvE EN VIVO": "PvE AO VIVO", "PvE en vivo": "PvE ao vivo", "PvE · más puntos hoy": "PvE · mais pontos hoje", "PvE: entra al Top 30, pasa 300k o suma más del umbral PvE": "PvE: entra no Top 30, passa de 300k ou soma mais que o limite PvE", "PvP EN VIVO": "PvP AO VIVO", "PvP · más victorias hoy": "PvP · mais vitórias hoje", "quedan": "restam", "Quitar": "Remover", "Quién ataca a quién": "Quem ataca quem", "Racha": "Sequência", "racha": "sequência", "Racha de champ": "Sequência de campeão", "Radar": "Radar", "radar": "radar", "Radar en vivo arriba (atacando ahora · batalla · PvE": "Radar ao vivo no topo (atacando agora · batalha · PvE", "Rank": "Rank", "Recibirás avisos aunque la pestaña esté en segundo plano": "Você receberá avisos mesmo com a aba em segundo plano", "Recompensas": "Prêmios", "Recompensas guardadas": "Prêmios salvos", "reconectando": "reconectando", "recuperado": "recuperado", "recuperado por completo": "totalmente recuperado", "Registro de mi clan": "Registro do meu clã", "Reintentar": "Tentar de novo", "Reintentar auto-detección": "Tentar detecção automática de novo", "REP final estimada": "REP final estimada", "REP ganada (todos": "REP ganha (todos", "REP ganada por hora": "REP ganha por hora", "REP pendiente por recuperar": "REP pendente para recuperar", "REP perdida (todos": "REP perdida (todos", "REP por ráfaga": "REP por rajada", "Reputación": "Reputação", "Reputación en este periodo": "Reputação neste período", "Reputación ganada por día (últimos": "Reputação ganha por dia (últimos", "Reputación perdida por salidas": "Reputação perdida por saídas", "Reputación perdida por salidas: falta por recuperar": "Reputação perdida por saídas: falta recuperar", "Resumen": "Resumo", "Resumen de cada ventana de 30 min": "Resumo de cada janela de 30 min", "Resumen diario (medianoche UTC": "Resumo diário (meia-noite UTC", "Resúmenes de ventanas cerradas": "Resumos das janelas fechadas", "Revendedores": "Revendedores", "Ritmo": "Ritmo", "Rivalidades": "Rivalidades", "Récords de hoy": "Recordes de hoje", "s (en vivo": "s (ao vivo", "Salidas": "Saídas", "salió": "saiu", "salió de": "saiu de", "sangra": "sangra", "SANGRANDO": "SANGRANDO", "sangrando": "sangrando", "Sangrando · quién los ataca": "Sangrando · quem os ataca", "sangró": "sangrou", "Se fue con": "Saiu com", "Se guarda en tu PC (archivo": "Fica salvo no seu PC (arquivo", "se unió a": "entrou em", "seguidas": "seguidas", "Según la fecha del anuncio": "Segundo a data do anúncio", "Sesión": "Sessão", "Si la Clan War terminara ahora, tu clan ganaría": "Se a Clan War terminasse agora, seu clã ganharia", "si no abre, permite Python en el firewall de Windows": "se não abrir, permita o Python no firewall do Windows", "Si ya se sabe quién atacó, verás un solo aviso (el del atacante), salvo que la víctima sea tu clan o uno con": "Se já se sabe quem atacou, você verá um só aviso (o do atacante), a não ser que a vítima seja seu clã ou um com", "Sin actividad todavía": "Sem atividade ainda", "Sin ataques de jugadores en este periodo (¿configuraste la pestaña 🔧 Conexión": "Sem ataques de jogadores neste período (você configurou a aba 🔧 Conexão", "Sin ataques vistos": "Nenhum ataque visto", "Sin auto-conexion": "Sem conexão automática", "Sin cambios en las últimas 24 h": "Sem mudanças nas últimas 24 h", "Sin combates en este momento": "Sem combates no momento", "sin conectar": "sem conexão", "SIN CONEXIÓN": "SEM CONEXÃO", "Sin configurar": "Não configurado", "sin datos": "sem dados", "sin datos de jugadores": "sem dados de jogadores", "Sin datos suficientes todavía para graficar este periodo": "Ainda sem dados suficientes para o gráfico deste período", "Sin datos todavía": "Sem dados ainda", "Sin errores recientes": "Sem erros recentes", "Sin miembros cargados (mira 🔧 Conexión": "Sem membros carregados (veja 🔧 Conexão", "Sin movimientos de miembros todavía (se detectan mientras el tracker está abierto y conectado a los miembros": "Sem movimentação de membros ainda (é detectada enquanto o tracker está aberto e conectado aos membros", "Sin ofensivas activas": "Sem ofensivas ativas", "sin precios aún": "sem preços ainda", "Sin respuesta del servidor": "Sem resposta do servidor", "Sin resultados": "Sem resultados", "sobre": "sobre", "solo activos": "só ativos", "solo activos (15 min": "só ativos (15 min", "Solo jugadores con": "Só jogadores com", "Solo jugadores del Top": "Só jogadores do Top", "Solo mi clan y": "Só meu clã e", "solo si la auto-detección falla": "só se a detecção automática falhar", "Solo ★ y momentos clave (Top 30, 300k, podio": "Só ★ e momentos-chave (Top 30, 300k, pódio", "Sonido": "Som", "Sonido ON": "Som ON", "Sonido y voz": "Som e voz", "Subir imagen desde tu PC o celular": "Enviar imagem do seu PC ou celular", "Subiste": "Você subiu", "Subió": "Subiu", "subió": "subiu", "Sugeridos": "Sugeridos", "superó a": "ultrapassou", "Sáb": "Sáb", "TE ATACAN": "ESTÃO TE ATACANDO", "Te atacan": "Estão te atacando", "Te aviso cada vez que": "Vou te avisar cada vez que", "te persigue a": "está te perseguindo a", "Temporada": "Temporada", "Temporada: calculando": "Temporada: calculando", "Temporadas": "Temporadas", "Tendencia": "Tendência", "termina en": "termina em", "terminó": "terminou", "toca para cerrar": "toque para fechar", "Todas las partidas": "Todas as partidas", "Todavía no hay recompensas publicadas para esta ronda": "Ainda não há prêmios publicados para esta rodada", "Todo": "Tudo", "Todos los clanes": "Todos os clãs", "Todos los países": "Todos os países", "Tokens": "Tokens", "Tokens (ej": "Tokens (ex", "Tokens y revendedores": "Tokens e revendedores", "Tokens y revendedores guardados": "Tokens e revendedores salvos", "Top": "Top", "top": "top", "Top Burn": "Top Burn", "Total": "Total", "total": "total", "Total ganado": "Total ganho", "traducción": "tradução", "Traducción automática · los nombres de clanes y jugadores no se traducen": "Tradução automática · os nomes de clãs e jogadores não são traduzidos", "TU CLAN": "SEU CLÃ", "Tu clan": "Seu clã", "Tu clan bajó": "Seu clã caiu", "Tu clan subió": "Seu clã subiu", "Tu navegador no permite avisos de escritorio": "Seu navegador não permite avisos na área de trabalho", "Tu nombre en el clan": "Seu nome no clã", "Título": "Título", "Umbral de REP": "Limite de REP", "Umbral PvE": "Limite PvE", "una por línea": "uma por linha", "Usa el asistente en": "Use o assistente em", "Vas de líder": "Você é o líder", "Ventaja": "Vantagem", "Ventana": "Janela", "ventana": "janela", "Ventana rápida": "Janela rápida", "venía de": "veio de", "Ver": "Ver", "Ver mis estadísticas": "Ver minhas estatísticas", "Ver todo": "Ver tudo", "Vibrar el celular": "Vibrar o celular", "victorias": "vitórias", "Vie": "Sex", "Vigilar a este jugador (aviso cuando ataque": "Vigiar este jogador (aviso quando atacar", "Volver": "Voltar", "Volver a lo oficial": "Voltar ao oficial", "Volver al español": "Voltar para o espanhol", "Voz": "Voz", "Voz activada": "Voz ativada", "Voz ON": "Voz ON", "win": "vitória", "y agrega los paquetes que conoces (ej. 1.000 tokens → 5 USD": "e adicione os pacotes que você conhece (ex. 1.000 tokens → 5 USD", "y se ve igual en el celular. Deja una fila vacía para no usarla": "e aparece igual no celular. Deixe uma linha vazia para não usá-la", "Ya recuperadas": "Já recuperadas", "Ábrelo en tu celular": "Abra no seu celular", "Última": "Última", "última hace": "última há", "Última hora": "Última hora", "última hora · el % es la seguridad de que sea ese clan": "última hora · o % é a certeza de que seja esse clã", "última semana": "última semana", "Últimas 6 horas (barras de 15 min": "Últimas 6 horas (barras de 15 min", "Últimas entradas y salidas": "Últimas entradas e saídas", "Últimas partidas": "Últimas partidas", "Último ataque hace": "Último ataque há", "últimos": "últimos", "Últimos puntos": "Últimos pontos", "Escritorio: aviso de Windows aunque la pestaña esté minimizada. El navegador solo deja sonar audio después de un clic en la página": "Área de trabalho: aviso do Windows mesmo com a aba minimizada. O navegador só deixa tocar áudio depois de um clique na página", "Tarifa (ej. 1.000 tokens → S": "Tarifa (ex. 1.000 tokens → S", "Agregar capturas": "Adicionar capturas", "Agregar fila": "Adicionar linha", "arrástralas o toca": "arraste-as ou toque em", "Borrar esta captura": "Apagar esta captura", "Cada vez que subas capturas nuevas se calcula cuántos puntos hizo cada uno, quién subió de puesto, su ritmo por hora y si llega a la recompensa": "Cada vez que você enviar novas capturas, calcula quantos pontos cada um fez, quem subiu de posição, o ritmo por hora e se alcança a recompensa", "Captura guardada": "Captura salva", "Capturas guardadas": "Capturas salvas", "Cargando el lector de imágenes… (la primera vez tarda unos segundos": "Carregando o leitor de imagens… (a primeira vez leva alguns segundos", "Corte del Top 30 en el tiempo": "Corte do Top 30 ao longo do tempo", "Cuando empiece una ronda nueva (los puntos se reinician": "Quando começar uma nova rodada (os pontos são zerados", "Cuando subas dos capturas en momentos distintos verás aquí quién hizo puntos": "Quando você enviar duas capturas em momentos diferentes verá aqui quem fez pontos", "Cuándo tomaste la captura: sirve para calcular el ritmo por hora": "Quando você tirou a captura: serve para calcular o ritmo por hora", "Cómo actualizar el ranking": "Como atualizar o ranking", "Descarga datos.json para publicarlo en la página web": "Baixa datos.json para publicar no site", "El tracker lee rango, jugador y puntos; tú revisas y guardas": "O tracker lê posição, jogador e pontos; você revisa e salva", "Empezar una ronda nueva? Se borran todas las capturas guardadas del Kyubi": "Começar uma nova rodada? Todas as capturas salvas do Kyubi serão apagadas", "En el juego abre el evento": "No jogo abra o evento", "Escribir a mano": "Digitar à mão", "Event Points": "Event Points", "Event Points por hora": "Event Points por hora", "Exportar para la web": "Exportar para o site", "Guardar captura": "Salvar captura", "Hace falta ver al jugador durante al menos 6 h": "É preciso acompanhar o jogador por pelo menos 6 h", "Hora de la captura": "Hora da captura", "imagen no válida": "imagem inválida", "Jugadores leídos": "Jogadores lidos", "Kyubi": "Kyubi", "Kyubi · Tailed Beast Raid": "Kyubi · Tailed Beast Raid", "La otra lectura dio este número": "A outra leitura deu este número", "Las filas en amarillo pueden tener un número mal leído: compáralas con tu captura": "As linhas em amarelo podem ter um número mal lido: compare com a sua captura", "Leyendo captura": "Lendo captura", "Líder": "Líder", "Más subió": "Mais subiu", "No encontré el ranking en esa imagen. Usa capturas de la ventana “Ranking List” del evento": "Não encontrei o ranking nessa imagem. Use capturas da janela “Ranking List” do evento", "No hay nada que guardar": "Não há nada para salvar", "No se pudo cargar el lector de imágenes. Revisa tu conexión a internet": "Não foi possível carregar o leitor de imagens. Verifique sua conexão com a internet", "No se pudo exportar": "Não foi possível exportar", "Nueva ronda": "Nova rodada", "O pega aquí el ranking como texto": "Ou cole aqui o ranking como texto", "ponlo dentro de la carpeta de la página y vuelve a subirla a Netlify": "coloque-o dentro da pasta do site e envie de novo para o Netlify", "Proyección": "Projeção", "Puedes corregir, quitar o agregar filas. También puedes pegar texto (una línea por jugador": "Você pode corrigir, remover ou adicionar linhas. Também pode colar texto (uma linha por jogador", "Puntos estimados al terminar el evento si sigue a este ritmo": "Pontos estimados ao fim do evento se continuar neste ritmo", "Puntos ganados desde la captura anterior": "Pontos ganhos desde a captura anterior", "Puntos que le faltan para alcanzar al de arriba": "Pontos que faltam para alcançar o de cima", "Pégalas aquí con": "Cole-as aqui com", "Quién hizo puntos": "Quem fez pontos", "ranking de Event Points": "ranking de Event Points", "Ranking List": "Ranking List", "Revisa y guarda": "Revise e salve", "Se descargó": "Foi baixado", "Seguir a este jugador": "Seguir este jogador", "Subida": "Subida", "SUBIENDO": "SUBINDO", "Suelta aquí las capturas del ranking o pégalas con": "Solte aqui as capturas do ranking ou cole-as com", "TDY Rale": "TDY Rale", "kyrt tr": "kyrt tr", "Ctrl+V": "Ctrl+V", "Termina": "Termina", "Todavía no hay recompensas publicadas para este evento": "Ainda não há recompensas publicadas para este evento", "Toma captura de pantalla de cada página (1/10, 2/10…). La del botón “Rank 1 - 100” trae además los cortes del puesto 30 y": "Tire uma captura de cada página (1/10, 2/10…). A do botão “Rank 1 - 100” também traz os cortes das posições 30 e", "Visto": "Visto", "Última captura": "Última captura"}}   # traducciones incluidas: en, pt
I18N_SET = set(I18N_SRC)
I18N, I18NL, I18N_DIR = {}, threading.Lock(), "zenshin_i18n"
I18N_API = os.environ.get("ZENSHIN_I18N_API", "https://translate.googleapis.com/translate_a/single")

def _i18n_file(l): return os.path.join(I18N_DIR, l + ".json")

def i18n_state(lang):
    with I18NL:
        st = I18N.get(lang)
        if st is None:
            d = {}
            try: d.update(json.load(open(_i18n_file(lang), encoding="utf-8")))
            except Exception: pass
            d.update(I18N_BUILTIN.get(lang, {}))
            if lang in I18N_BUILTIN:   # idiomas incluidos: completos sin internet (marcas y nombres quedan igual)
                for s_ in I18N_SRC: d.setdefault(s_, s_)
            st = I18N[lang] = {"d": d, "busy": False, "err": "", "et": 0}
        missing = [s for s in I18N_SRC if s not in st["d"]]
        if missing and not st["busy"] and (not st["err"] or time.time() - st["et"] > 30):
            st["busy"] = True; st["err"] = ""; threading.Thread(target=i18n_job, args=(lang,), daemon=True).start()
        return dict(lang=lang, dict={k: v for k, v in st["d"].items() if k in I18N_SET}, done=not missing, prog=round(1 - len(missing) / max(1, len(I18N_SRC)), 3), err=st["err"], busy=st["busy"])

def _gtx(lines, lang):   # traductor automatico (el mismo de translate.google); lineas separadas por \n
    url = I18N_API + "?" + _urlencode(dict(client="gtx", sl="es", tl=lang, dt="t", q="\n".join(lines)))
    for a in range(4):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20); j = json.loads(r.read().decode("utf-8")); break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < 3: time.sleep(3 * (a + 1)); continue
            raise ValueError("el traductor respondió %d%s" % (e.code, " (idioma no disponible)" if e.code == 400 else ""))
    txt = "".join(seg[0] for seg in (j[0] or []) if seg and isinstance(seg[0], str))
    return [x.strip() for x in txt.split("\n")]

def _tr_batch(batch, lang):
    out = _gtx(batch, lang)
    if len(out) == len(batch): return {k: v or k for k, v in zip(batch, out)}
    if len(batch) == 1: return {batch[0]: " ".join(x for x in out if x) or batch[0]}
    h = len(batch) // 2; r = _tr_batch(batch[:h], lang); r.update(_tr_batch(batch[h:], lang)); return r

def i18n_job(lang):
    st = I18N[lang]
    try:
        todo = [s for s in I18N_SRC if s not in st["d"]]; i = 0
        while i < len(todo):
            batch, n = [], 0
            while i < len(todo) and len(batch) < 60 and (not batch or n + len(todo[i]) < 1400): batch.append(todo[i]); n += len(todo[i]) + 1; i += 1
            res = _tr_batch(batch, lang)
            with I18NL: st["d"].update(res)
            time.sleep(.25)
    except Exception as e:
        st["err"] = str(e)[:120]; st["et"] = time.time(); LOG.append([time.time(), "traducción %s: %s" % (lang, str(e)[:60])])
    finally:
        st["busy"] = False
        try:
            if not st["d"]: return
            os.makedirs(I18N_DIR, exist_ok=True)
            with I18NL: s = json.dumps({k: v for k, v in st["d"].items() if I18N_BUILTIN.get(lang, {}).get(k) != v}, ensure_ascii=False)
            with open(_i18n_file(lang) + ".tmp", "w", encoding="utf-8") as f: f.write(s)
            os.replace(_i18n_file(lang) + ".tmp", _i18n_file(lang))
        except Exception: pass

def tpl(expr):  # expresion JS de URL ('/api/clan/' + id) -> plantilla '/api/clan/{id}'
    s = "".join(re.sub(r"\$\{[^}]*\}", "{id}", lit) if q else "{id}" for q, lit, var in re.findall(r"""(["'`])(.*?)\1|([A-Za-z_$][\w$.\[\]]*)""", expr))
    return re.sub(r"(\{id\})+", "{id}", s)

def set_ep(ep, msg):
    EP.clear(); EP.update(ep); mem_prev.clear(); state["ep"] = state["mem_msg"] = msg; print(msg)

def parse_members(raw):
    try: L = find_members(json.loads(raw))
    except ValueError:
        L = []; p = TableParser(); p.feed("<table>" + raw + "</table>")
        for t in p.tables:
            for r in t:
                r = list(r)[1:] if r and r[0].isdigit() else list(r)
                if len(r) >= 3 and re.search(r"\d", r[-1]): L.append((r[0], num(r[-1]), num(r[-2])))
    return sorted((dict(name=str(n), rep=int(r), lv=int(float(l)) if re.fullmatch(r"\d+(\.0+)?", str(l)) else 0) for n, r, l in L), key=lambda x: -x["rep"])

def members_of(c, ep=None):
    ep = ep or EP; v = str(c[ep["by"]])
    b = ep["bt"].replace("{id}", json.dumps(v)[1:-1]).encode() if ep.get("bt") else None
    ct = "application/json" if b and b[:1] in b"{[" else "application/x-www-form-urlencoded"
    return parse_members(fetch(urljoin(URL, ep["u"].replace("{id}", quote(v))), b, True, ep["m"], ct))

def lastsub(s, f):
    i = s.rfind(f); return s[:i] + "{id}" + s[i + len(f):]

def learn(u, m, b):  # aprende la peticion real capturada en el navegador (asistente de conexion)
    u, b = urljoin(URL, u or ""), b or ""
    if not re.search(r"clan|member", u + b, re.I): return
    for c in state["clans"]:
        for by in ("id", "name", "rank"):
            v = str(c[by] or "")
            for f in {quote(v), v}:
                if v and (f in u or f in b) and (not v.isdigit() or re.search(r"(?<!\d)%s(?!\d)" % v, u + b)):
                    ep = dict(m=m.upper(), u=lastsub(u, f) if f in u else u, bt=(lastsub(b, f) if f not in u else b) or None, by=by)
                    try:
                        if len(members_of(state["clans"][0], ep)) > 1: return set_ep(ep, "✅ Conectado (aprendido): %s %s" % (ep["m"], ep["u"]))
                    except Exception: pass

def autoconnect():  # lee el JS del sitio, deduce las URLs de miembros y prueba unas pocas hasta que una devuelva jugadores
    try:
        c = scrape()[0][0]; html = fetch(URL); js = html
        for src in re.findall(r'<script[^>]+src=["\']([^"\']+)', html)[:10]:
            try: js += "\n" + fetch(urljoin(URL, src))
            except Exception: pass
        cand = [tpl(x) for x in re.findall(r"(?:fetch|axios\.\w+|\$\.(?:get|post|getJSON))\(\s*((?:[^()]|\([^()]*\)){0,160}?)[,)]", js) if re.search(r"clan|member", x, re.I)]
        for L in re.findall(r'["\'`]((?:/|https?://)[^"\'`\s]*(?:clan|member)[^"\'`\s]*)["\'`]', js, re.I):
            cand += [L + "{id}" if L.endswith(("=", "/")) else L, L + "/{id}", L + "/{id}/members"]
        def eps():
            for t in dict.fromkeys(x for x in cand if x.strip()):
                for by in ("id", "name", "rank"):
                    if str(c[by] or ""):
                        for m, bt in [("GET", None)] + [("POST", b) for k in ("clan_id", "id", "clan", "name") for b in ('{"%s": "{id}"}' % k, '{"%s": {id}}' % k)]:
                            yield dict(m=m, u=t, bt=bt, by=by)
        n = 0
        for ep in eps():
            n += 1
            if n > 90: break
            try:
                if len(members_of(c, ep)) > 1: return set_ep(ep, "✅ Auto-conectado: %s %s" % (ep["m"], ep["u"]))
            except Exception: pass
            time.sleep(.05)
        state["mem_msg"] = "Sin auto-conexion (%d pruebas). Usa el asistente en 🔧 Conexión." % n
    except Exception as e: state["mem_msg"] = "Auto-conexion: " + str(e)[:80]

def refresh_members(c):   # un cerrojo por clan: varios clanes se leen a la vez sin contar dos veces el mismo ataque
    with clan_lock(c["name"]):
        t0 = time.time()
        try: return _refresh(c)
        finally: MLAST[c["name"]] = time.time(); MTS.append(time.time() - t0); del MTS[:-30]

def _refresh(c):  # lee miembros de un clan, detecta quien subio REP (= quien ataco) y avisa a la pantalla
    cn = {x["name"] for x in state["clans"]}; L = [m for m in members_of(c) if m["name"] not in cn]; now = time.time(); cur = {m["name"]: m["rep"] for m in L}
    with lock:
        o = mem_prev.get(c["name"])
        if o is not None:
            for w, r in cur.items():
                if r - o.get(w, r) > 0: ev_add(dict(ts=now, t="mem", clan=c["name"], who=w, amt=r - o[w])); rec_add("pl", c["name"] + "|" + w, r - o[w])
        hr = time.localtime(now).tm_hour
        for w, r in cur.items():
            ms = mstats.setdefault(c["name"] + "|" + w, {"h": [0] * 24, "log": [], "rep": r}); ms["rep"] = r
            q15 = int(now // 900); hs = ms.setdefault("s", [])   # fotos de su REP (max. 1 cada 15 min y solo si cambio) -> 6h / 24h / 7d
            if not hs or (hs[-1][1] != r and hs[-1][0] != q15): hs.append([q15, r])
            while len(hs) > 1 and hs[1][0] <= q15 - 8 * 96: hs.pop(0)
            if o is not None and r - o.get(w, r) > 0:
                g = r - o[w]; ms["h"][hr] += g; ms["log"].append([now, g]); del ms["log"][:-500]; k = str(int(now // 1800)); wm = ms.setdefault("w", {}); wm[k] = wm.get(k, 0) + g
        wi0 = int(now // 1800)
        for m in L:  # cada miembro: REP ganada en las 3 ultimas ventanas, cuando estuvo activo y REP por hora (1..6 h atras)
            ms = mstats[c["name"] + "|" + m["name"]]; m["w"] = [ms.get("w", {}).get(str(wi0 - k), 0) for k in (2, 1, 0)]; m["t"] = ms["log"][-1][0] if ms["log"] else 0
            hg = [0] * 6
            for t_, a_ in ms["log"]:
                b = int((now - t_) // 3600)
                if 0 <= b < 6: hg[b] += a_
            m["hg"] = hg; hs = ms.get("s") or []
            for key_, sec_ in (("g6", 21600), ("g24", 86400), ("g7", 604800)):   # REP ganada en 6 h / 24 h / 7 dias
                i_ = bisect.bisect_right(hs, [int((now - sec_) // 900), float("inf")]) - 1
                m[key_] = m["rep"] - hs[max(0, i_)][1] if hs else 0
            m["since"] = hs[0][0] * 900 if hs else now
        moves(c["name"], o, cur, L, now)
        mem_prev[c["name"]] = cur; mem_cache[c["name"]] = (now, L)
    with cond: ver[0] += 1; cond.notify_all()
    return L

def season_info(now):  # dia de temporada y cuenta atras (manual > detectada en la pagina > estimada por temporadas anteriores)
    S = SEASON
    if not SEAS: return
    ends = sorted(x["ended"] for x in SEAS); start = ends[-1]; lens = [b - a for a, b in zip(ends, ends[1:])]
    est = start + (lens[-1] if lens else 35 * 86400); exact = None
    for d in S.get("dates", []):
        try: t = int(d) / 1000 if d.isdigit() else datetime.datetime.fromisoformat(d.replace("Z", "+00:00")).timestamp()
        except ValueError: continue
        if abs(t - est) < 5 * 86400 and t > now: exact = t
    if S.get("manual") and S["manual"] < now - 86400: S["manual"] = None
    end = S.get("manual") or exact or est
    S.update(name="Temporada %d" % (max(x["n"] for x in SEAS) + 1), start=start, end=end, src="manual" if S.get("manual") else "auto" if exact else "estimado",
             day=int((now - start) // 86400) + 1, total=max(1, round((end - start) / 86400)))

def online_list():  # jugadores que ganaron REP en la ultima hora: [clan, jugador, ultima vez, REP ganada en 30 min]
    now = time.time(); out = []
    for k, v in list(mstats.items()):
        if v["log"] and now - v["log"][-1][0] < 86400:
            c, p = k.split("|", 1); out.append([c, p, v["log"][-1][0], sum(a[1] for a in v["log"] if now - a[0] < 1800)])
    return out

def rollover(now):  # al cerrar cada bloque de 30 min -> resumen; al cambiar el dia UTC -> resumen diario
    wi, di = int(now // 1800), int(now // 86400); sp = lambda m: m.split("|", 1)
    if tick["w"] is not None and wi != tick["w"]:
        k = str(tick["w"]); cl = sorted(((c, w.get(k, 0)) for c, w in wacc.items() if w.get(k, 0)), key=lambda x: -x[1])
        mem = sorted(((sp(m)[1], sp(m)[0], v["w"][k]) for m, v in mstats.items() if v.get("w", {}).get(k)), key=lambda x: -x[2])
        r = dict(w=tick["w"], start=tick["w"] * 1800, end=(tick["w"] + 1) * 1800, total=sum(x[1] for x in cl), clans=cl[:8], members=mem[:12]); summ.append(r); del summ[:-48]
        if r["total"]:
            notify("📊 **Resumen %s–%s** · total +%d\n" % (time.strftime("%H:%M", time.localtime(r["start"])), time.strftime("%H:%M", time.localtime(r["end"])), r["total"]) + "\n".join("%d. %s +%d" % (i + 1, c, g) for i, (c, g) in enumerate(cl[:5])) + "\n👤 " + ", ".join("%s (+%d)" % (m[0], m[2]) for m in mem[:6]), "summ")
        for c in wacc.values():
            for x in [x for x in c if int(x) < wi - 48 * 41]: del c[x]
        for v in mstats.values():
            for x in [x for x in v.get("w", {}) if int(x) < wi - 6]: del v["w"][x]
    if tick["d"] is not None and di != tick["d"] and MINE[0]:
        mm = sorted(((sp(k)[1], sum(a[1] for a in v["log"] if now - a[0] < 86400)) for k, v in mstats.items() if k.startswith(MINE[0] + "|")), key=lambda x: -x[1])
        notify("🗓️ **Resumen diario · %s**\n" % MINE[0] + "\n".join("%s: +%d" % x for x in mm[:15] if x[1]), "daily")
    tick["w"], tick["d"] = wi, di

def mcount_changes(clans):  # clanes cuyo numero de miembros cambio (28/30 -> 27/30): alguien salio o entro
    dropped, grew = set(), set()
    for c in clans:
        m = re.match(r"\s*(\d+)", c["members"]); n = int(m.group(1)) if m else None; o = prev_cnt.get(c["name"])
        if n is not None:
            if o is not None and n < o: dropped.add(c["name"])
            elif o is not None and n > o: grew.add(c["name"])
            prev_cnt[c["name"]] = n
    return dropped, grew

def recover(cl, amt):  # cada REP que gana el clan reduce la reputacion pendiente por salidas (la mas antigua primero)
    for d in debt.get(cl, []):
        t = min(d["left"], amt)
        if t > 0: d["left"] -= t; amt -= t

def _confirm(cl):  # segunda lectura para confirmar una salida (evita falsas alarmas)
    c = next((x for x in state["clans"] if x["name"] == cl), None)
    if c and EP:
        try: refresh_members(c)
        except Exception: pass

def moves(cl, o, cur, L, now):  # entradas y salidas de miembros; una salida se confirma si falta en 2 lecturas seguidas
    if o is None or not cur or len(cur) < max(1, len(o) // 2): return   # primera lectura o lista sospechosa
    pd = pend.setdefault(cl, {}); ev = state["events"]; pl = {m["name"]: m["lv"] for m in (mem_cache.get(cl) or (0, []))[1]}; back = {n for n in pd if n in cur}; fresh = False
    for n in back: pd.pop(n)
    for n in o:
        if n not in cur and n not in pd: pd[n] = [o[n], pl.get(n, 0), now, 0]; fresh = True
    for n in list(pd):
        pd[n][3] += 1
        if pd[n][3] >= 2 and now - pd[n][2] >= 3:
            first = pd[n][2]; rep, lv = pd.pop(n)[:2]; e_ = dict(ts=now, t="leave", clan=cl, who=n, rep=rep, lv=lv); ev_add(e_); mlog.append(e_); del mlog[:-300]
            d0 = dict(who=n, rep=rep, left=rep, ts=now, to=None); debt.setdefault(cl, []).append(d0); del debt[cl][:-8]; rleft[n] = (cl, now); mstats.pop(cl + "|" + n, None)
            recover(cl, sum(x["amt"] for x in ev if x["t"] == "gain" and x["clan"] == cl and x["ts"] >= first))   # lo ganado mientras se confirmaba
            j = rjoin.get(n)
            if j and j[0] != cl and now - j[1] < 86400:   # ya lo vimos entrar a otro clan
                d0["to"] = j[0]
                for x in reversed(ev):
                    if x["t"] == "join" and x["who"] == n and x["clan"] == j[0] and not x["frm"]: x["frm"] = cl; ev_touch(x); break
            if cl == MINE[0] or cl in MON: notify("🚪 **%s** salio de **%s** (%d REP)" % (n, cl, rep), "move")
    lvs = {m["name"]: m["lv"] for m in L}
    for n in cur:
        if n not in o and n not in back:
            fr = rleft.get(n); fr = fr[0] if fr and fr[0] != cl and now - fr[1] < 86400 else None
            e_ = dict(ts=now, t="join", clan=cl, who=n, rep=cur[n], lv=lvs.get(n, 0), frm=fr); ev_add(e_); mlog.append(e_); del mlog[:-300]; rjoin[n] = (cl, now)
            for d in debt.get(fr, []):
                if d["who"] == n and not d["to"]: d["to"] = cl
            if cl == MINE[0] or cl in MON: notify("🆕 **%s** se unio a **%s**%s" % (n, cl, " (venia de %s)" % fr if fr else ""), "move")
    if fresh: t = threading.Timer(4, _confirm, [cl]); t.daemon = True; t.start()

def ser_add(now, clans):  # guarda la reputacion de cada clan: 1 punto/min durante 26 h y 1 punto/15 min durante 41 dias
    cur = {c["name"]: c["rep"] for c in clans}
    for key, step, keep in (("f", 60, 26 * 3600), ("c", 900, 41 * 86400)):
        T = SER[key]
        if T["t"] and now - T["t"][-1] < step: continue
        T["t"].append(now); n = len(T["t"])
        for k in set(T["d"]) | set(cur): T["d"].setdefault(k, [None] * (n - 1)).append(cur.get(k))
        if now - T["t"][0] > keep * 1.05:
            i = bisect.bisect_left(T["t"], now - keep); del T["t"][:i]
            for v in T["d"].values(): del v[:i]

def clan_detail(name, rng):  # todo lo del detalle de un clan: lo ganado por periodo, dias, y datos de la grafica
    now = time.time(); wa = dict(wacc.get(name, {})); wi0 = int(now // 1800); tot = lambda lo: sum(v for k, v in wa.items() if int(k) > wi0 - lo)
    since = SER["c"]["t"][0] if SER["c"]["t"] else START
    tiles = dict(w=[wa.get(str(wi0 - i), 0) for i in (2, 1, 0)], h6=tot(12), h24=tot(48), d7=tot(336), season=sum(wa.values()))
    days = {}
    for k, v in wa.items(): t = time.localtime(int(k) * 1800); days[(t.tm_year, t.tm_yday)] = days.get((t.tm_year, t.tm_yday), 0) + v
    dl = []
    for i in range(6, -1, -1): t = time.localtime(now - i * 86400); dl.append(["Lun Mar Mié Jue Vie Sáb Dom".split()[t.tm_wday] + " " + str(t.tm_mday), days.get((t.tm_year, t.tm_yday), 0)])
    T = SER["f"] if 0 < rng <= 86400 else SER["c"]; V = T["d"].get(name, []); i0 = bisect.bisect_left(T["t"], now - rng if rng else 0)
    pts = [(T["t"][i], V[i]) for i in range(i0, len(T["t"])) if i < len(V) and V[i] is not None]
    nb = 40; g, lo = [0] * nb, [0] * nb
    if len(pts) > 1:
        span = (pts[-1][0] - pts[0][0]) or 1
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]): b = min(nb - 1, int((t1 - pts[0][0]) / span * nb)); d = v1 - v0; (g if d > 0 else lo)[b] += abs(d)
    st = max(1, len(pts) // 150); line = pts[::st]
    if pts and line[-1] != pts[-1]: line.append(pts[-1])
    return dict(tiles=tiles, days=dl, since=since, t=[p[0] for p in line], v=[p[1] for p in line], g=g, l=lo)

def burn_detail(cl, sec):  # Top Burn: REP ganada por cada miembro en el periodo (-1 = ventana de 30 min actual, -2 = ventana anterior, o segundos)
    now = time.time(); w0 = int(now // 1800) * 1800
    since, until = (w0, now + 1) if sec == -1 else (w0 - 1800, w0) if sec == -2 else (now - max(60, sec), now + 1)
    mins = max(0.1, (min(now, until) - since) / 60); hb = int(now // 3600); ml = mem_cache.get(cl); out = []
    for i, m in enumerate(ml[1] if ml else []):
        lg = list(mstats.get(cl + "|" + m["name"], {"log": []})["log"])
        g = sum(a[1] for a in lg if since <= a[0] < until)
        if sec in (21600, 86400): g = max(g, m.get("g6" if sec == 21600 else "g24", 0) or 0)   # periodos largos: diferencia de REP (incluye lo no visto en vivo)
        out.append(dict(name=m["name"], lv=m["lv"], rep=m["rep"], rank=i + 1, g=g, rpm=round(g / mins, 1), h=[sum(a[1] for a in lg if int(a[0] // 3600) == hb - j) for j in range(6)], t=lg[-1][0] if lg else 0))
    out.sort(key=lambda x: (-x["g"], -x["rep"])); tot = sum(x["g"] for x in out); act = sum(1 for x in out if x["g"] > 0)
    return dict(members=out, hb=hb, since=since, mins=round(mins, 1), total=tot, act=act, idle=len(out) - act, rpm=round(tot / mins, 1), n=len(out), ht=[sum(x["h"][j] for x in out) for j in range(6)])

def purge(clans):  # quita "jugadores" que en realidad son nombres de clanes (basura guardada en el historial)
    if tick.get("pg") == len(clans): return
    tick["pg"] = len(clans); cn = {c["name"] for c in clans}
    for k in [k for k in mstats if k.split("|", 1)[1] in cn]: del mstats[k]
    state["events"][:] = [e for e in state["events"] if not (e["t"] == "mem" and e.get("who") in cn)]

DQ, DQL, DQE = [], threading.Lock(), threading.Event()

def notify(text, kind=None):   # cola: junta los avisos de ~1 s en un mensaje y respeta el limite de Discord (antes se perdian en rafagas) + Telegram v22
    if kind is not None:
        ks = kind if isinstance(kind, (list, tuple)) else (kind,); h = time.localtime().tm_hour; a, b = int(CFG["h0"]), int(CFG["h1"])
        if not any(CFG.get(k) for k in ks) or not (a == b or (a <= h < b if a < b else h >= a or h < b)): return   # horario que cruza medianoche (22 -> 6)
    if WEBHOOK:
        with DQL: DQ.append(text)
        DQE.set()
    tg_notify(text)   # v22: tambien por Telegram si esta configurado (tg_notify chequea token internamente)

def discord_worker():
    while True:
        DQE.wait(); time.sleep(1.0)
        with DQL: msgs = DQ[:]; DQ.clear(); DQE.clear()
        chunks, cur = [], ""
        for m in msgs:
            m = m[:1900]
            if cur and len(cur) + len(m) + 1 > 1900: chunks.append(cur); cur = m
            else: cur = cur + "\n" + m if cur else m
        if cur: chunks.append(cur)
        for ch in chunks[:6]:
            for _ in range(3):
                try:
                    urllib.request.urlopen(urllib.request.Request(WEBHOOK, json.dumps({"content": ch}).encode(), {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}), timeout=10); break
                except urllib.error.HTTPError as e:
                    if e.code != 429: LOG.append([time.time(), "discord: HTTP %d" % e.code]); break
                    try: w = float(json.loads(e.read().decode() or "{}").get("retry_after", 1))
                    except Exception: w = 1.0
                    time.sleep(min(10.0, w if w < 100 else w / 1000))
                except Exception as e: LOG.append([time.time(), "discord: " + str(e)[:60]]); break
            time.sleep(.35)

def save():   # el historial sobrevive a reinicios; escritura atomica (nunca queda un archivo a medias si se cierra de golpe)
    with SAVEL:
        try:
            with lock:
                a = json.dumps(SER)
                b = json.dumps(dict(ev=state["events"][-2500:], pairs=pairs, hours=hours, sess=sess, sessL=sessL, ms=mstats, sm=SEASON.get("manual"), wa=wacc, su=summ, ht=heat, dt=debt, ml=mlog, snaps=snaps[-1800::30], rec=REC))
        except Exception as e:
            LOG.append([time.time(), "guardar: " + str(e)[:80]]); return
        for fn, data, gz in ((SER_FILE, a, True), (HIST, b, False)):
            try:
                tmp = fn + ".tmp"
                with (gzip.open(tmp, "wt", encoding="utf-8") if gz else open(tmp, "w", encoding="utf-8")) as f: f.write(data)
                os.replace(tmp, fn)
            except Exception as e: LOG.append([time.time(), "guardar %s: %s" % (fn, str(e)[:60])])

def load():
    try: SER.update(json.load(gzip.open(SER_FILE, "rt", encoding="utf-8")))
    except Exception: pass
    try:
        d = json.load(open(HIST, encoding="utf-8")); state["events"] = [e for e in d["ev"] if isinstance(e, dict) and (e.get("t") != "hit" or isinstance(e.get("sus"), list))]; snaps.extend((t, x) for t, x in d["snaps"])
        for v, k in ((pairs, "pairs"), (hours, "hours"), (sess, "sess"), (sessL, "sessL")): v.update(d[k])
        mstats.update(d.get("ms", {})); SEASON["manual"] = d.get("sm"); wacc.update(d.get("wa", {})); summ[:] = d.get("su", []); heat.update(d.get("ht", {})); debt.update(d.get("dt", {})); mlog[:] = d.get("ml", [])
        if isinstance(d.get("rec"), dict) and d["rec"].get("d") and isinstance(d["rec"].get("hr"), list): REC.update(d["rec"])
    except Exception: pass
    for e in state["events"]:
        EVSEQ[0] += 1; e["id"] = e["u"] = EVSEQ[0]
        if e.get("t") == "hit": e["pending"] = False

def sumev(ev, t, clan, now, secs):
    return sum(e["amt"] for e in ev if e["t"] == t and e["clan"] == clan and now - e["ts"] < secs)

def delta(name, cur, secs, now):
    for ts, s in itertools.islice(snaps, bisect.bisect_left(snaps, (now - secs,)), None):
        if name in s: return cur - s[name]

def exact_combo(vals, amt):   # 1, 2 o 3 clanes cuya ganancia suma EXACTAMENTE lo que perdio la victima (ataques simultaneos)
    C = sorted(vals.items(), key=lambda x: -x[1])[:8]
    for n, tol in ((1, round(amt * .01)), (2, 0), (3, 0)):
        hits = [cb for cb in itertools.combinations(C, n) if abs(sum(v for _, v in cb) - amt) <= tol]
        if len(hits) == 1: return list(hits[0])
        if len(hits) > 1: return None   # ambiguo: decide la puntuacion
    return None

def hit_notify(e):   # Discord: se manda UNA vez, cuando ya se sabe el atacante (o se descarto)
    if e.get("nt"): return
    e["nt"] = 1
    if e["to"] == MINE[0] or e["to"] in MON or any(x[0] == MINE[0] for x in e["sus"]) or e["amt"] >= max(ALERT_MIN, CFG["thr"]):
        c = CM.get(e["to"])
        notify("🩸 **%s** SANGRANDO −%s%s\n%s" % (e["to"].upper(), fmt(e["amt"]), " · REP %s (#%d)" % (fmt(c["rep"]), c["rank"]) if c else "",
               "\n".join("⚔️ **%s** ataca a **%s** (+%s, %d%%)" % (x[0], e["to"], fmt(x[1]), x[2]) for x in e["sus"][:3]) or "❓ atacante no identificado"), "hit")

def confirm(ev, sus, consume, agg, now, exact):
    ev.update(sus=sus, pending=False, confirmed=True, ex=exact); ev["rev"] = ev.get("rev", 0) + 1; ev_touch(ev)
    for k, v in consume.items():
        pr = pairs.setdefault(k + "|" + ev["to"], [0, 0, 0]); pr[0] += 1; pr[1] += min(v, ev["amt"]); pr[2] = now
        rest = v
        for g in agg[k][1]:
            t_ = min(g[3], rest); g[3] -= t_; rest -= t_
    PEND_HITS.pop(ev["to"], None); REC["nh"] = REC.get("nh", 0) + 1
    if not REC.get("hit") or ev["amt"] > REC["hit"][0]: REC["hit"] = [ev["amt"], sus[0][0], ev["to"], now]
    hit_notify(ev)

def match_hits(now, gain, loss):  # avisa al instante al perder REP y completa el atacante cuando aparece la ganancia
    W = max(6.0, POLL * 4.0 + 1.0); out = []
    RG[:] = [g for g in RG if now - g[0] <= W and g[3] > 0]
    RG.extend([now, k, v, v] for k, v in gain.items())
    for target, amt in loss.items():   # 1) sangrado inmediato (si ya habia uno pendiente, se suma: antes se perdia)
        p = PEND_HITS.get(target)
        if p and now - p["ts"] < W:
            p["amt"] += amt; p["lt"] = now; ev_touch(p); continue
        ev = dict(ts=now, t="hit", to=target, amt=amt, sus=[], lt=now, pending=True, rev=0)
        PEND_HITS[target] = ev; out.append(ev)
    for target, ev in sorted(PEND_HITS.items(), key=lambda x: x[1]["ts"]):   # 2) empareja con las ganancias cercanas
        amt = ev["amt"]; expired = now - ev["ts"] > W; agg = {}
        for g in RG:
            if g[1] != target and g[3] > 0 and ev["ts"] - W <= g[0] <= ev["lt"] + W:
                a = agg.setdefault(g[1], [0, [], False]); a[0] += g[3]; a[1].append(g); a[2] = a[2] or abs(g[0] - ev["lt"]) <= POLL + .2
        done = False
        if agg:
            combo = exact_combo({k: v[0] for k, v in agg.items()}, amt)
            if combo:
                tot = max(1, sum(v for _, v in combo))
                confirm(ev, [[k, v, min(99, round(100 * v / tot))] for k, v in combo], dict(combo), agg, now, 1); done = True
            else:
                sc = {}
                for k, (v, _, same) in agg.items():
                    s = (.35 + .65 * (1 - abs(v - amt) / max(v, amt, 1))) * (1 + min(pairs.get(k + "|" + target, [0])[0], 5) * .2)
                    if same: s *= 1.2
                    sc[k] = s * (1.6 if len(agg) == 1 else 1)
                tot = max(1, sum(sc.values()))
                sus = [[k, agg[k][0], min(99, round(100 * x / tot))] for k, x in sorted(sc.items(), key=lambda y: -y[1])[:3]]
                lead = len(agg) == 1 or sus[0][2] >= 45 or (len(sus) > 1 and sus[0][2] >= 1.7 * max(1, sus[1][2]))
                if lead or (expired and sus[0][2] >= 30):
                    confirm(ev, sus, {sus[0][0]: amt}, agg, now, 0); done = True
        if not done and expired:   # se acabo el tiempo: se avisa "no identificado" (antes quedaba "identificando…" para siempre)
            PEND_HITS.pop(target, None); ev["pending"] = False; ev_touch(ev); hit_notify(ev)
    return out

def pvp_process(now, pvp, new):
    for p in pvp:
        n = p["name"]; st = PVP_ST.get(n)
        if st is None or p["w"] < st["w0"] or p["l"] < st["l0"]: st = PVP_ST[n] = {"w0": p["w"], "l0": p["l"], "t": 0, "st": 0}
        o = prev_pvp.get(n)
        if o and (p["w"] > o["w"] or p["l"] > o["l"]):
            dw, dl = max(0, p["w"] - o["w"]), max(0, p["l"] - o["l"])
            new.append(dict(ts=now, t="pvp", who=n, w=dw, l=dl, score=p["score"], rank=p.get("rank")))
            st["t"] = now; st["st"] = (max(0, st["st"]) + dw) if dw and not dl else (min(0, st["st"]) - dl) if dl and not dw else 0
            if dw: rec_add("pvp", n, dw)
        p.update(sw=p["w"] - st["w0"], sl=p["l"] - st["l0"], t=st["t"], st=st["st"], wr=round(100 * p["w"] / max(1, p["w"] + p["l"])))

def pve_process(now, pve, new):   # PvE en vivo: quien hace puntos, cuantos, ritmo, puestos, Top 30, 300k
    global prev_pve, prev_pve_rank
    old, oldr = dict(prev_pve), dict(prev_pve_rank); names = {p["name"] for p in pve}
    free = [n for n in old if n.startswith("👻") and n not in names]
    for p in pve:   # nombres invisibles que cambiaron de puesto: se reconocen por su "firma" y su score
        if p.get("gh") and p["name"] not in old and free:
            c = [n for n in free if GSIG.get(n) == p["sig"] and old[n] <= p["score"]]
            if c:
                n = min(c, key=lambda n: p["score"] - old[n]); free.remove(n); old[p["name"]] = old.pop(n); oldr[p["name"]] = oldr.pop(n, None)
                if n in PVE_ST: PVE_ST[p["name"]] = PVE_ST.pop(n)
    for p in pve:
        n, s, r = p["name"], p["score"], p["rank"]; st = PVE_ST.get(n)
        if st is None or s < st["s0"]: st = PVE_ST[n] = {"s0": s, "log": [], "t": 0, "best": r}
        st["seen"] = now; o = old.get(n); orank = oldr.get(n)
        if o is not None and s > o:
            amt = s - o; st["log"].append([now, amt]); del st["log"][:-300]; st["t"] = now
            e = dict(ts=now, t="pve", who=n, amt=amt, score=s, rank=r, orank=orank)
            if o < 300000 <= s: e["gift"] = 1
            if (orank or 999) > 30 >= r: e["t30"] = 1
            new.append(e); rec_add("pve", n, amt); REC["npv"] = REC.get("npv", 0) + amt
            if e.get("t30") or e.get("gift") or amt >= int(CFG.get("pthr", 5000)):
                notify("🏆 **%s** +%s PvE → %s (#%d)%s%s" % (n, fmt(amt), fmt(s), r, " ⭐ entró al Top 30" if e.get("t30") else "", " 🎁 pasó los 300.000" if e.get("gift") else ""), "pve")
        elif o is None and old and not p.get("gh"): new.append(dict(ts=now, t="pvein", who=n, score=s, rank=r))
        if orank and orank != r: pve_rk[n] = [now, orank, r]
        st["best"] = min(st.get("best", r), r); lg = st["log"]
        p.update(g5=sum(a for t_, a in lg if now - t_ <= 300), g60=sum(a for t_, a in lg if now - t_ <= 3600), sess=s - st["s0"], t=st["t"], rk=pve_rk.get(n), best=st["best"])
    for p in pve:   # adelantamientos en PvE ("A supero a B")
        n = p["name"]; orank = oldr.get(n)
        if orank and p["rank"] < orank and not n.startswith("👻"):
            ov = [q["name"] for q in pve if q is not p and not q["name"].startswith("👻") and oldr.get(q["name"]) and oldr[q["name"]] < orank and q["rank"] > p["rank"]]
            if 0 < len(ov) <= 3: new.extend(dict(ts=now, t="pvepass", who=n, over=x, r=p["rank"]) for x in ov)
    prev_pve = {p["name"]: p["score"] for p in pve}; prev_pve_rank = {p["name"]: p["rank"] for p in pve}
    GSIG.clear(); GSIG.update((p["name"], p["sig"]) for p in pve if p.get("gh"))
    if now - tick.get("pvc", 0) > 3600:
        tick["pvc"] = now
        for k in [k for k, v in PVE_ST.items() if now - v.get("seen", now) > 86400]: del PVE_ST[k]

def process(now, clans, pvp, pve):   # todo lo que se calcula en cada lectura (con el cerrojo tomado)
    global prev_pvp
    CM.clear(); CM.update((c["name"], c) for c in clans); rec_day(now)
    new, gain, loss = [], {}, {}
    dropped, grew = mcount_changes(clans)
    if snaps and snaps[-1][0] >= START and now - snaps[-1][0] < 120:   # solo lecturas de esta sesion: tras reiniciar no inventa ataques
        old = snaps[-1][1]
        gain = {c["name"]: c["rep"] - old[c["name"]] for c in clans if c["name"] in old and c["rep"] > old[c["name"]]}
        loss = {c["name"]: old[c["name"]] - c["rep"] for c in clans if c["name"] in old and c["rep"] < old[c["name"]]}
        for g_ in grew: gain.pop(g_, None)   # un miembro que entra suma REP: no es un ataque
        new += [dict(ts=now, t="bleed", clan=k, amt=v, lv=k in dropped, rep=CM[k]["rep"], rank=CM[k]["rank"]) for k, v in loss.items()]
        new += [dict(ts=now, t="gain", clan=k, amt=v, rep=CM[k]["rep"], rank=CM[k]["rank"]) for k, v in gain.items()]
    new += match_hits(now, gain, {k: v for k, v in loss.items() if k not in dropped})   # si bajo por una salida de miembro, no se culpa a nadie
    pvp_process(now, pvp, new); pve_process(now, pve, new)
    prev_pvp = {p["name"]: p for p in pvp}
    oldr = dict(prev_rank)
    for c in clans:
        o = oldr.get(c["name"])
        if o and o != c["rank"] and snaps:
            new.append(dict(ts=now, t="rank", clan=c["name"], a=o, b=c["rank"])); rk_chg[c["name"]] = [now, o, c["rank"]]   # flecha verde / roja
            ov = [d["name"] for d in clans if c["rank"] < o and d["name"] != c["name"] and oldr.get(d["name"]) and oldr[d["name"]] < o and d["rank"] > c["rank"]]
            if len(ov) <= 5: new += [dict(ts=now, t="pass", clan=c["name"], over=d, r=c["rank"]) for d in ov]   # "A supero a B"
            inv = c["name"] == MINE[0] or c["name"] in MON or MINE[0] in ov or any(x in MON for x in ov)
            if ov and len(ov) <= 5 and inv: notify("📈 **%s** superó a **%s** → puesto #%d" % (c["name"], ", ".join(ov), c["rank"]), "rank" if c["name"] == MINE[0] or MINE[0] in ov else "mon")
            elif c["name"] == MINE[0] or c["name"] in MON: notify("%s **%s** paso del puesto #%d al #%d" % ("📈" if c["rank"] < o else "📉", c["name"], o, c["rank"]), "rank" if c["name"] == MINE[0] else "mon")
    prev_rank.update({c["name"]: c["rank"] for c in clans})
    lt = time.localtime(now)
    for e in new:
        ev_add(e)
        if e["t"] == "gain":
            hours.setdefault(e["clan"], [0] * 24)[lt.tm_hour] += e["amt"]
            w = wacc.setdefault(e["clan"], {}); k = str(int(now // 1800)); w[k] = w.get(k, 0) + e["amt"]
            recover(e["clan"], e["amt"]); heat.setdefault(e["clan"], [[0] * 24 for _ in range(7)])[lt.tm_wday][lt.tm_hour] += e["amt"]
            rec_add("gain", e["clan"], e["amt"]); REC["hr"][lt.tm_hour] += e["amt"]
            if not REC.get("big") or e["amt"] > REC["big"][0]: REC["big"] = [e["amt"], e["clan"], now]
            burst_alert(e["clan"], e["amt"], e["rep"], e["rank"], now)
        elif e["t"] == "bleed" and not e["lv"]:
            rec_add("loss", e["clan"], e["amt"])
            try: bleed_alert(e["clan"], e["amt"], e.get("rep", 0), e.get("rank", 0), now)
            except Exception: pass
    ev = state["events"]
    if len(ev) > 4400: del ev[:-4000]
    if EP:   # lectura de miembros: el clan que acaba de moverse va primero
        hot = set(gain) | set(loss) | dropped | grew
        if hot: mq_push(hot, 0)
        cold = [c["name"] for c in clans[:15] if c["name"] not in mem_prev]
        if cold: mq_push(cold, 1)
        if now - tick.get("wt", 0) > 20:
            tick["wt"] = now; wl = [x for x in [MINE[0]] + MON if x in CM]
            if wl: mq_push(wl, 1)
        if now - tick.get("all", 0) > 300: tick["all"] = now; mq_push([c["name"] for c in clans], 2)
    elif now - tick.get("ac", 0) > 60:  # reintenta la auto-conexion si fallo al arrancar
        tick["ac"] = now; threading.Thread(target=autoconnect, daemon=True).start()
    for k, v in gain.items(): sess[k] = sess.get(k, 0) + v; last[k] = now
    for k, v in loss.items(): sessL[k] = sessL.get(k, 0) + v; last[k] = now
    snaps.append((now, {c["name"]: c["rep"] for c in clans}))
    while now - snaps[0][0] > 3700: snaps.pop(0)
    win = [x for ts, x in snaps if now - ts < 3600]; win = win[::max(1, len(win) // 30)]
    mvd = {}
    for x in mlog:
        if now - x["ts"] < 86400:
            m_ = mvd.setdefault(x["clan"], {"o": [], "i": []})
            if x["t"] == "leave": m_["o"].append([x["who"], x["rep"], x["ts"]])
            else: m_["i"].append([x["who"], x["rep"], x["ts"], x.get("frm")])
    act = {}
    for kk, v in mstats.items():
        if v["log"]:
            ago = now - v["log"][-1][0]; a = act.setdefault(kk.split("|", 1)[0], [0, 0, 0, 0])
            for i, t in enumerate((60, 300, 900, 3600)): a[i] += ago <= t
    A, BY, TG, LB = {}, {}, {}, {}   # BY = quien ataca a cada clan, TG = a quien ataca cada clan, LB = ultimo sangrado real
    for e in reversed(ev):
        if now - e["ts"] > 600: break
        if e["t"] == "gain" or (e["t"] == "bleed" and not e.get("lv")):
            a = A.setdefault(e["clan"], [0, 0, 0, 0]); i = 0 if e["t"] == "gain" else 1; a[i] += e["amt"]
            if now - e["ts"] <= 300: a[i + 2] += e["amt"]
            if e["t"] == "bleed": LB.setdefault(e["clan"], e["ts"])
        elif e["t"] == "hit":
            for g_, pts, cf in e["sus"][:2]:
                if cf >= 30:
                    sh = round(e["amt"] * cf / 100); b_ = BY.setdefault(e["to"], {}).setdefault(g_, [0, 0, 0]); b_[0] += sh; b_[1] += 1; b_[2] = max(b_[2], cf)
                    t_ = TG.setdefault(g_, {}).setdefault(e["to"], [0, 0]); t_[0] += sh; t_[1] += 1
    for c in clans:
        n = c["name"]; c["sp"] = [x[n] for x in win if n in x]; c["d5"], c["d60"] = delta(n, c["rep"], 300, now), delta(n, c["rep"], 3600, now); c["dq"] = [delta(n, c["rep"], q, now) for q in (15, 30, 60, 300)]
        a = A.get(n, [0, 0, 0, 0]); c["g10"], c["l10"] = a[0], a[1]
        c["rpm"] = round((a[2] - a[3]) / 5, 1)
        c["sess"], c["sessL"], c["last"] = sess.get(n, 0), sessL.get(n, 0), last.get(n, 0)
        wa = wacc.get(n, {}); wi0 = int(now // 1800); c["w"] = [wa.get(str(wi0 - k), 0) for k in (2, 1, 0)]; c["wr"] = round(c["w"][2] / max(0.5, (now % 1800) / 60), 1)
        c["gs"] = [sum(v for kx, v in wa.items() if int(kx) > wi0 - 12), sum(v for kx, v in wa.items() if int(kx) > wi0 - 48)]; c["mv"] = mvd.get(n); c["act"] = act.get(n, [0, 0, 0, 0]); ml = mem_cache.get(n); c["top"] = [ml[1][0]["name"], ml[1][0]["rep"]] if ml and ml[1] else None
        c["by"] = sorted(([k] + v for k, v in BY.get(n, {}).items()), key=lambda x: -x[1])[:3]; c["tg"] = sorted(([k] + v for k, v in TG.get(n, {}).items()), key=lambda x: -x[1])[:3]; c["lbl"] = LB.get(n, 0); c["tr"] = rk_chg.get(n)
    season_info(now); purge(clans); ser_add(now, clans); rollover(now)
    state.update(ts=now, clans=clans, pvp=pvp, pve=pve, error=None, season={k: v for k, v in SEASON.items() if k != "dates"}, seasons=SEAS)

def poll():
    discover(); tick["ac"] = tick["save"] = time.time(); threading.Thread(target=autoconnect, daemon=True).start()
    for _ in range(MEM_WORKERS): threading.Thread(target=mq_worker, daemon=True).start()
    while True:
        now = time.time()
        try:
            clans, pvp, pve = scrape(); LAT[0] = round(time.time() - now, 2); LATH.append(LAT[0]); del LATH[:-20]
            if not clans: raise ValueError("No encontre la tabla de clanes (la pagina podria cargarse con JavaScript).")
            with lock:
                try:
                    process(now, clans, pvp, pve)
                    update_streaks(now); calc_threat(clans)
                    if now - tick.get("achv", 0) > 300: tick["achv"] = now; achv_check()
                except Exception:
                    tb = traceback.format_exc(); print(tb); LOG.append([time.time(), "proceso: " + tb.strip().splitlines()[-1][:100]]); del LOG[:-30]
            delay[0] = POLL
        except Exception as e:
            with lock: state["error"] = str(e); delay[0] = min(max(delay[0], POLL) * 2, 15); LOG.append([time.time(), "lectura: " + str(e)[:100]]); del LOG[:-30]
        with cond: ver[0] += 1; cond.notify_all()
        if time.time() - tick["save"] > 60: tick["save"] = time.time(); threading.Thread(target=save, daemon=True).start()
        wake.wait(max(0.05, delay[0] - (time.time() - now))); wake.clear()

def base_json():   # todo menos los eventos; se calcula UNA vez por version y se comparte entre pantallas abiertas
    if BASE[0] == ver[0]: return BASE[1]
    v = ver[0]; now = time.time(); d = {k: x for k, x in state.items() if k != "events"}
    d.update(start=START, url=MEMBERS_URL, wh=bool(WEBHOOK), tg=bool(TELEGRAM.get("token")), embed=DISCORD_EMBED[0], cfg=CFG, sound=SOUND, theme=THEME[0], rivals=RIVALS, achv=ACHV, pred=PRED, threat=THREAT, streaks=STREAK, bleed=dict(total=sum(b.get("total",0) for b in BLEED_ST.values()), clans=len(BLEED_ST)), online=online_list(), log=LOG[-8:], debt=debt, pairs=dict(sorted(pairs.items(), key=lambda x: -x[1][0])[:40]), summ=summ[-16:], rec=rec_summary(), lan=LAN[0], xv=XV[0], lb=LBL,
             diag=dict(up=now - START, ep=EP.get("u", ""), ncl=len(mem_cache), nmem=sum(len(x[1]) for x in list(mem_cache.values())), lat=LAT[0], polls=ver[0], ev=len(state["events"]), ncl_tot=len(state["clans"]),
                       poll=POLL, mq=len(MQ), mt=round(sum(MTS) / len(MTS), 2) if MTS else None, ka=dict(n=KA["new"], r=KA["reuse"], on=KA["on"]), cl=CLIENTS[0], lat5=round(sum(LATH) / len(LATH), 2) if LATH else None))
    s = json.dumps(d); BASE[0], BASE[1] = v, s; return s

def snap(lu=-1):   # mensaje para una pantalla: la primera vez todos los eventos, despues solo los nuevos o cambiados
    for _ in range(5):
        try:
            with lock:
                full = lu < 0 or bool(EVLOG_U and lu < EVLOG_U[0] - 1)
                if full: evs = state["events"][-1500:]
                else:
                    i = bisect.bisect_right(EVLOG_U, lu); d = {}
                    for e in EVLOG_E[i:]: d[e["id"]] = e
                    evs = list(d.values())
                cur = EVSEQ[0]; ej = json.dumps(evs); b = base_json()
            return '{"full":%s,"now":%.3f,"events":%s,%s' % ("true" if full else "false", time.time(), ej, b[1:]), cur
        except RuntimeError: time.sleep(.05)
    return '{"full":false,"events":[]}', lu

PAGE = r"""<!doctype html><html lang=es><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name=theme-color content="#ff9f43"><meta name=apple-mobile-web-app-capable content=yes><meta name=apple-mobile-web-app-status-bar-style content=black-translucent><link rel=manifest href='data:application/manifest+json,{"name":"Zenshin Tracker v16","short_name":"Zenshin v16","start_url":"/","display":"standalone","theme_color":"#ff9f43","background_color":"#0f0f1a"}'>
<title>Ninja Zenshin Live V15</title><link id=fav rel=icon href="data:,"><style>
:root{--bg:#0b0b0d;--c:#151518;--l:#26262b;--t:#e8e8ea;--m:#8a8a93;--r:#ff4d4d;--g:#3ddc84;--o:#ff9f43}
*{box-sizing:border-box}body{margin:auto;background:var(--bg);color:var(--t);font:14px system-ui,sans-serif;padding:16px;max-width:1150px}
h1{font-size:20px;margin:0}h3{margin:14px 0 8px}header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between}
nav{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}button,input,select,textarea{background:var(--c);color:var(--t);border:1px solid var(--l);border-radius:8px;padding:7px 12px;font:inherit}
button{cursor:pointer}button.on{background:var(--l);border-color:var(--o)}.card{background:var(--c);border:1px solid var(--l);border-radius:10px;padding:10px 12px;margin-bottom:8px}
table{width:100%;border-collapse:collapse}td,th{padding:7px 8px;border-bottom:1px solid var(--l);text-align:left;white-space:nowrap}th{color:var(--m);font-weight:500}
.wrap{overflow-x:auto}.up{color:var(--g)}.dn{color:var(--r)}.m{color:var(--m)}.bleed{background:#ff4d4d14}.hit{background:#3ddc8410}
.b,.g{padding:2px 8px;border-radius:99px;font-size:12px;font-weight:600}.b{background:#ff4d4d26;color:var(--r)}.g{background:#3ddc8426;color:var(--g)}
.live{animation:p 1s infinite}@keyframes p{50%{opacity:.35}}.star,.nm{cursor:pointer}.star{color:var(--m)}.star.on{color:#ffd43b}.nm:hover{color:var(--o)}
.err{background:#ff4d4d26;padding:8px;border-radius:8px;margin-top:8px}#tk{background:linear-gradient(90deg,#ff4d4d22,#3ddc8422);border-radius:8px;padding:8px 12px;margin-top:10px;min-height:34px;font-weight:600;transition:all .18s ease}
#ts{position:fixed;top:12px;right:12px;display:flex;flex-direction:column;gap:6px;z-index:9;max-width:min(440px,92vw)}#ts>div{animation:tin .25s ease-out;background:var(--c);border-left:4px solid var(--g);padding:9px 14px;border-radius:8px;box-shadow:0 4px 18px #000a}
#md{position:fixed;inset:0;background:#000b;display:none;align-items:center;justify-content:center;z-index:8}#md>div{background:var(--c);border:1px solid var(--l);border-radius:14px;padding:16px;width:min(560px,94vw);max-height:86vh;overflow:auto}
#wr{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px}@media(max-width:800px){#wr{grid-template-columns:1fr}}#wr .card{margin:6px 0 0}
.sus{display:inline-block;background:#ff9f4322;border-radius:8px;padding:2px 8px;margin:4px 4px 0 0}.hc{width:24px;text-align:center;font-size:10px;padding:0}td.hc{height:24px}
.mine{background:#3ddc8422;box-shadow:inset 3px 0 0 #3ddc84}.o{color:var(--o)}.b2{border:1px solid var(--l);border-radius:99px;padding:1px 10px;font-size:12px}.b2.on{border-color:var(--g);color:var(--g)}h4{margin:14px 0 6px}body.light{--bg:#f4f4f6;--c:#ffffff;--l:#d9d9df;--t:#1b1b1f;--m:#6a6a74;--r:#d12b2b;--g:#12894a;--o:#c96a00}
.duel{display:flex;align-items:center;gap:10px;background:linear-gradient(90deg,#3ddc8414,#ff4d4d14);border:1px solid var(--l);border-radius:12px;padding:10px 14px;margin-top:6px}
.duel .s{flex:1;min-width:0}.duel .s.r{text-align:right}.duel b{font-size:17px}.arw{font-size:20px;white-space:nowrap;animation:mv 1s infinite}@keyframes mv{50%{transform:translateX(7px)}}
.alarm{animation:fl .8s infinite;border-color:#ff4d4d!important}@keyframes fl{50%{background:#ff4d4d55}}.eg{stroke-dasharray:8 6;animation:dash 1s linear infinite}@keyframes dash{to{stroke-dashoffset:-14}}
.ic{width:1.05em;height:1.05em;vertical-align:-.16em;display:inline-block}
.fire{display:inline-block;width:1.15em;height:1.25em;vertical-align:-.25em;color:#ff9f43;transform-origin:50% 90%;animation:fk 1s ease-in-out infinite}
.fire svg{width:100%;height:100%;overflow:visible;animation-delay:inherit}.fire .fo{fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.fire .fi{fill:#ffc53d;opacity:.7;transform-box:fill-box;transform-origin:50% 100%;animation:fj .5s ease-in-out infinite alternate;animation-delay:inherit}
.fire.l1{color:#ffb86b}.fire.l2{color:#ff9f43}.fire.l3{color:#ff7a1a;filter:drop-shadow(0 0 3px #ff7a1a99)}.fire.l4{color:#ff5a1f;filter:drop-shadow(0 0 5px #ff5a1fbb);animation-duration:.75s}.fire.l5{color:#ff3b2f;filter:drop-shadow(0 0 7px #ff3b2fdd);animation-duration:.55s}
.fire.l4 .fi,.fire.l5 .fi{fill:#ffe066;opacity:.95}
@keyframes fk{0%,100%{transform:rotate(-4deg) scale(1)}25%{transform:rotate(3deg) scale(1.06,.95)}50%{transform:rotate(-2deg) scale(.95,1.08)}75%{transform:rotate(4deg) scale(1.04,.97)}}
@keyframes fj{from{transform:scale(.72,.65);opacity:.4}to{transform:scale(1.05,1.12);opacity:.95}}
.ra{display:inline-block;width:16px;height:16px;margin-right:4px;vertical-align:-3px}.ra .ic{width:16px;height:16px;vertical-align:0}.ra.up{color:var(--g)}.ra.dn{color:var(--r)}
.ra.up.new{animation:rb .7s ease-in-out infinite}.ra.dn.new{animation:rb2 .7s ease-in-out infinite}@keyframes rb{50%{transform:translateY(-3px) scale(1.3)}}@keyframes rb2{50%{transform:translateY(3px) scale(1.3)}}
td.rk{white-space:nowrap}.ri{margin-right:2px}.ri.t1{color:var(--g)}.ri.t2{color:var(--o)}.ri.t3{color:var(--m)}
.dt{display:inline-block;width:8px;height:8px;border-radius:50%;background:#55555e;vertical-align:1px}.dt.a1{background:var(--g);animation:pu 1.2s infinite}.dt.a2{background:var(--g)}.dt.a3{background:var(--o)}
@keyframes pu{0%{box-shadow:0 0 0 0 #3ddc84cc}100%{box-shadow:0 0 0 8px #3ddc8400}}
.act{display:inline-flex;align-items:center;gap:3px;background:#3ddc8422;color:var(--g);border:1px solid #3ddc8466;border-radius:99px;padding:0 7px;font-size:11px;font-weight:700;vertical-align:1px}.act.lv{animation:p 1s infinite}
.num{text-align:right;font-variant-numeric:tabular-nums}.cb{display:inline-block;background:var(--l);border-radius:99px;padding:1px 8px;font-size:10px;font-weight:700;color:var(--o)}.atr{color:#ff6b6b;font-weight:700}.warn{color:#ffd93d}.ok{color:#6bcb77}.anim-badge{background:linear-gradient(90deg,#c56cf0,#ff9f43);color:#000;border-radius:99px;padding:1px 7px;font-size:9px;font-weight:800;animation:animpulse 1.5s ease-in-out infinite}@keyframes animpulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.7;transform:scale(1.08)}}.jcard:hover{transform:translateY(-1px);box-shadow:0 4px 12px rgba(255,159,67,.2);transition:all .2s}.cd-badge{display:inline-block;animation:cdpulse 2s ease-in-out infinite}@keyframes cdpulse{0%,100%{box-shadow:0 0 0 0 rgba(255,107,107,.5)}50%{box-shadow:0 0 10px 2px rgba(255,107,107,.3)}}
.disc-badge{background:linear-gradient(135deg,#ff6b6b,#ee5a24);color:#fff;border-radius:99px;padding:1px 7px;font-size:9px;font-weight:900;margin-left:4px;animation:discpulse 1.2s ease-in-out infinite}@keyframes discpulse{0%,100%{transform:scale(1)}50%{transform:scale(1.1)}}
.best-badge{background:linear-gradient(135deg,#ffd700,#ffaa00);color:#000;border-radius:99px;padding:1px 8px;font-size:9px;font-weight:900;margin-left:4px;box-shadow:0 0 8px rgba(255,215,0,.5)}
.pkg-badge{background:linear-gradient(135deg,#c56cf0,#9b59b6);color:#fff;border-radius:99px;padding:1px 7px;font-size:9px;font-weight:800;margin-left:4px}
.buy-btn:hover{transform:scale(1.05);transition:transform .15s;box-shadow:0 2px 8px rgba(255,159,67,.4)}
.best-val{animation:bestglow 2s ease-in-out infinite}@keyframes bestglow{0%,100%{box-shadow:inset 0 0 0 0 rgba(255,215,0,0)}50%{box-shadow:inset 0 0 12px 0 rgba(255,215,0,.15)}}
.pay-card:hover{transform:translateY(-2px);transition:all .2s;box-shadow:0 4px 12px rgba(0,0,0,.3)}.gap{color:var(--m);font-size:9px;margin-left:2px;font-weight:400}
.t1{color:var(--g);text-decoration:underline;text-decoration-color:var(--r);text-underline-offset:4px}.t2{color:var(--o)}
th.srt{cursor:pointer;user-select:none}th.srt.on{color:var(--t)}.mt td,.mt th{padding:9px 10px}.bt td,.bt th{padding:8px}
tr.flashg td{background:#3ddc8426}tr.sgr{animation:sg 1.2s infinite}@keyframes sg{50%{background:#ff4d4d38}}
.mh{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap}.rc{width:42px;height:42px;border-radius:50%;border:2px solid var(--o);display:flex;align-items:center;justify-content:center;font-weight:700;flex:none}
.rc2{display:inline-flex;width:24px;height:24px;border-radius:50%;border:1.5px solid var(--g);color:var(--g);align-items:center;justify-content:center;font-size:12px;vertical-align:2px}
.mb{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin:14px 0 6px}.mbtn{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.rbtn{border-color:#ff4d4d88;color:var(--r)}.bbtn{border-color:#ff9f4388;color:var(--o)}.bb{cursor:pointer;color:var(--m)}.bb:hover{color:var(--o)}
.hm{display:grid;grid-template-columns:34px repeat(24,minmax(12px,1fr));gap:3px;min-width:430px}.hx{height:16px;border-radius:3px;background:var(--l)}.hh{font-size:9px;text-align:center}.hd{font-size:11px;line-height:16px}
.sts{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}.sts>div{text-align:center}.sts b{font-size:18px;display:block}
#wrg{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:800px){#wrg{grid-template-columns:1fr}}
.tb1{font-size:15px;font-weight:800;letter-spacing:.3px}#ts>div.big{border-left-width:7px;box-shadow:0 0 0 2px #ff4d4d88,0 4px 18px #000a}.tn{color:var(--m);font-size:12px;font-weight:400}
@keyframes tin{from{transform:translateX(40px);opacity:0}}
body.mo #ts{top:auto;bottom:12px}body.mo #ts>div{padding:6px 12px;font-size:12px}body.mo #ts>div>div+div{display:none}body.mo #ts>div:nth-child(n+4){display:none}body.mo .tb1{font-size:13px}#ts>div{cursor:pointer}
@media(max-width:560px){.act:not(.al){font-size:0;gap:0;padding:1px 5px}.act:not(.al) .ic{font-size:12px}#md>div{padding:12px}.sts b{font-size:15px}.mt td,.mt th{padding:8px 6px}.bt td,.bt th{padding:7px 3px}}

:root{--y:#ffd43b}body.light{--y:#a87800}
header h1{margin-right:auto}.hp{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--l);border-radius:99px;padding:3px 10px;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
.hp i{width:8px;height:8px;border-radius:50%;background:currentColor;display:inline-block}.hp.ok{border-color:#3ddc8466;color:var(--g)}.hp.ok i{animation:pu 1.2s infinite}.hp.bad{border-color:#ff4d4d88;color:var(--r);animation:p 1s infinite}
#tk{display:flex;align-items:center;gap:10px;overflow:hidden}#tkt{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lvb{flex:none;background:#e0282e;color:#fff;font-size:11px;font-weight:800;border-radius:5px;padding:2px 7px;letter-spacing:.5px;animation:p 1.4s infinite}
#tk.fl{animation:tkf 1s ease-out}@keyframes tkf{0%{box-shadow:0 0 0 2px #ff9f43,0 0 24px #ff9f4388;transform:scale(1.01)}100%{box-shadow:none;transform:none}}
#nt{overflow:hidden;white-space:nowrap;border:1px solid var(--l);border-radius:8px;margin-top:6px;height:30px;position:relative;background:var(--c);font-size:13px}
#ntx{position:absolute;left:0;top:0;height:30px;display:flex;align-items:center;gap:26px;will-change:transform}.nti{flex:none}.nti .m{font-size:11px;margin-right:2px}
.ntph{position:absolute;left:10px;top:6px;color:var(--m);font-size:12px;display:none}#ntx:empty+.ntph{display:block}
#wr{grid-template-columns:minmax(0,1.15fr) minmax(0,1fr) minmax(0,1fr)}@media(max-width:980px){#wr{grid-template-columns:minmax(0,1fr)}}
#wr .duel{padding:8px 10px;gap:6px;margin-top:0;margin-bottom:6px}#wr .duel b{font-size:14px}#wr .arw{font-size:15px}
.rt{margin:0 0 6px;font-size:15px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.rad{position:relative;background:var(--c);border:1px solid var(--l);border-radius:10px;padding:7px 10px 9px;margin-bottom:6px;overflow:hidden;opacity:calc(.5 + .5*var(--a,1));transition:border-color .3s}
.rad.on{border-color:#3ddc84aa;box-shadow:0 0 0 1px #3ddc8455,0 0 18px #3ddc8426}.rad.mine{border-color:var(--g)}.rad.pv.on{border-color:#ffd43baa;box-shadow:0 0 18px #ffd43b22}
.rad .rh{display:flex;align-items:center;gap:6px;min-width:0}.rad .rh>b:first-of-type{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.rad .rl{font-size:12px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rbar{position:absolute;left:0;bottom:0;height:3px;background:linear-gradient(90deg,#3ddc84,#ff9f43)}.rad.pv .rbar{background:linear-gradient(90deg,#ffd43b,#ff9f43)}
.cmb{background:#ff9f4333;color:var(--o);border-radius:6px;padding:0 6px;font-size:11px;font-weight:800;white-space:nowrap}
.bub{display:inline-block;color:var(--g);font-weight:800;animation:bub 2.6s ease-out forwards;margin-left:5px;font-size:12px}.bub.dn{color:var(--r)}
@keyframes bub{0%{transform:translateY(5px) scale(.7);opacity:0}12%{transform:none;opacity:1}75%{opacity:1}100%{transform:translateY(-10px);opacity:0}}
tr.pfl td{animation:pfl 2.6s ease-out;animation-delay:var(--d,0s)}tr.pfl.gr td{animation-name:gfl}tr.pfl.rd td{animation-name:rfl}
@keyframes pfl{0%{background:#ffd43b44}100%{background:transparent}}@keyframes gfl{0%{background:#3ddc8444}100%{background:transparent}}@keyframes rfl{0%{background:#ff4d4d44}100%{background:transparent}}
.lvp{display:inline-flex;align-items:center;gap:3px;background:#ffd43b22;color:var(--y);border:1px solid #ffd43b66;border-radius:99px;padding:0 7px;font-size:11px;font-weight:700;animation:p 1s infinite;white-space:nowrap}
#alm{position:fixed;inset:0;pointer-events:none;z-index:7;display:none;box-shadow:inset 0 0 0 5px #ff2d2d,inset 0 0 140px #ff000088;animation:alm 1s infinite}
@keyframes alm{50%{box-shadow:inset 0 0 0 2px #ff2d2d88,inset 0 0 50px #ff000030}}
#almb{position:fixed;left:50%;top:10px;transform:translateX(-50%);z-index:10;display:none;background:#b3121f;color:#fff;font-weight:800;font-size:16px;padding:10px 18px;border-radius:12px;box-shadow:0 6px 30px #000c;cursor:pointer;max-width:92vw;text-align:center;animation:tin .25s ease-out}
.race{position:relative}.rr{position:absolute;left:0;right:0;top:0;height:34px;transition:transform .8s cubic-bezier(.2,.8,.2,1);display:flex;align-items:center;gap:8px}
.rr .rn{width:34px;text-align:right;font-weight:800;font-variant-numeric:tabular-nums;flex:none}.rr .rw{flex:1;position:relative;height:28px;background:var(--l);border-radius:6px;overflow:hidden;min-width:0}
.rr .rb{position:absolute;left:0;top:0;bottom:0;border-radius:6px;transition:width .8s cubic-bezier(.2,.8,.2,1),background .4s;background:linear-gradient(90deg,#ff9f4340,#ff9f43cc)}
.rr.atk .rb{background:linear-gradient(90deg,#3ddc8440,#3ddc84dd)}.rr.bld .rb{background:linear-gradient(90deg,#ff4d4d40,#ff4d4ddd)}.rr.me .rw{box-shadow:0 0 0 2px var(--g)}
.rr .rt2{position:absolute;left:8px;right:6px;top:0;bottom:0;display:flex;align-items:center;gap:6px;font-weight:700;white-space:nowrap;overflow:hidden;color:var(--t)}
.rr .rv{width:112px;text-align:right;font-variant-numeric:tabular-nums;font-weight:700;flex:none}.rr .rg{width:78px;text-align:right;font-size:11px;flex:none}
.fchip{display:inline-block;margin:0 4px 6px 0;padding:3px 10px;border-radius:99px;border:1px solid var(--l);cursor:pointer;font-size:12px;user-select:none}.fchip.on{border-color:var(--o);color:var(--o)}
.hoy{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:10px}.hoy .card{margin:0}
.hb{display:flex;align-items:center;gap:8px;margin:6px 0;font-size:13px}.hb .bw{flex:1;height:8px;background:var(--l);border-radius:4px;overflow:hidden;min-width:40px}.hb .bw>div{height:8px;border-radius:4px}
.pvg{display:grid;grid-template-columns:minmax(0,2.3fr) minmax(0,1fr);gap:12px;align-items:start}@media(max-width:900px){.pvg{grid-template-columns:minmax(0,1fr)}}
.fi2{padding:6px 0;border-bottom:1px solid var(--l);font-size:13px}.fi2.nw{animation:sli .6s ease-out both}@keyframes sli{from{transform:translateX(18px);opacity:0;background:#ffd43b33}}
.set label{display:flex;align-items:center;gap:8px;margin:8px 0;flex-wrap:wrap}.set h4{margin:16px 0 4px}.set p{margin:4px 0}
.wrb{display:inline-block;width:46px;height:6px;background:#ff4d4d55;border-radius:3px;vertical-align:1px;margin-left:6px;overflow:hidden}.wrb>i{display:block;height:6px;background:var(--g)}
#ts>div.atk{border-left-color:#ff9f43}.tl{margin-top:3px;font-size:13px}
#ts{top:auto;bottom:12px;flex-direction:column-reverse;max-width:min(390px,92vw)}#ts>div:nth-child(n+5){display:none}
.hb2{margin:7px 0;font-size:13px}.hb2 .r1{display:flex;align-items:center;gap:6px;min-width:0}.hb2 .r1 .nmx{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.hb2 .bw{height:6px;background:var(--l);border-radius:3px;overflow:hidden;margin:3px 0 0 30px}.hb2 .bw>div{height:6px;border-radius:3px}
@media(max-width:560px){#ts{left:8px;right:8px;bottom:8px;max-width:none}#ts>div{padding:6px 10px;font-size:12px}#ts>div:nth-child(n+3){display:none}#ts>div>div:nth-child(n+4){display:none}#ts .tb1{font-size:13px}#wr>div:nth-child(2){display:none}#wr .rad:nth-of-type(n+4){display:none}.rr .rg{display:none}.rr .rv{width:80px;font-size:12px}.hp{font-size:11px;padding:2px 8px}#almb{font-size:14px}}

#side{display:none}#sstrip{margin-top:8px;border:1px solid var(--l);border-radius:10px;background:var(--c);padding:6px 10px;font-size:13px}
.ssr{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.sst{display:inline-block;background:var(--l);border-radius:6px;padding:1px 7px;margin:2px 3px 2px 0;white-space:nowrap}.ssx{margin-top:8px;border-top:1px solid var(--l);padding-top:8px}
@media(min-width:1500px){#side{display:block;position:fixed;top:12px;right:12px;bottom:12px;width:270px;overflow:auto;background:var(--c);border:1px solid var(--l);border-radius:12px;padding:12px;z-index:5}body{margin-left:auto;margin-right:300px}#sstrip{display:none}#ts{right:300px}}
.sdh{display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:6px;font-size:14px}
button.sm,label.sm,a.sm{padding:3px 9px;font-size:12px;border-radius:7px}a.sm{border:1px solid var(--l);color:var(--t);text-decoration:none;display:inline-block;background:var(--c)}a.sm:hover{border-color:var(--o)}
.tkt td{padding:6px 4px;font-size:13px;white-space:normal}.tki{width:22px;height:22px;object-fit:contain;vertical-align:middle;border-radius:4px}
.rsc{display:flex;gap:8px;padding:8px 0;border-bottom:1px solid var(--l);font-size:13px}.rsl{width:36px;height:36px;border-radius:8px;object-fit:cover;flex:none;display:flex;align-items:center;justify-content:center;background:var(--l);font-size:18px}
.ctb{display:inline-block;border:1px solid var(--l);border-radius:99px;padding:1px 8px;font-size:11px;margin:2px 3px 0 0;color:var(--t);text-decoration:none;cursor:pointer}.ctb:hover{border-color:var(--o)}
.pkc{display:flex;gap:8px;align-items:center;text-decoration:none;color:var(--t);padding:6px 0;border-bottom:1px solid var(--l);font-size:12px}.pkc img{width:86px;height:50px;object-fit:cover;border-radius:6px;flex:none;background:var(--l)}
.rwc{border-color:color-mix(in srgb,var(--rc) 45%,var(--l));background:linear-gradient(135deg,color-mix(in srgb,var(--rc) 10%,var(--c)),var(--c) 65%)}
.rwh{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.rwh>b{font-size:15px}.rwbt{margin-left:auto;display:flex;gap:6px;align-items:center}
.rwsrc{font-size:11px;color:var(--g);border:1px solid #3ddc8455;border-radius:99px;padding:0 7px;white-space:nowrap}.rwsrc.man{color:var(--o);border-color:#ff9f4366}
.rwb{display:flex;gap:14px;margin-top:10px;align-items:flex-start}.rwimg{width:min(320px,40%);border-radius:10px;cursor:zoom-in;border:1px solid var(--l);flex:none;box-shadow:0 4px 16px #0006;background:var(--l)}
.rwl{flex:1;min-width:0}.rwr{display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px dashed var(--l)}.rwr b{min-width:0}
.rwcnd{background:color-mix(in srgb,var(--rc) 20%,transparent);color:var(--rc);border:1px solid color-mix(in srgb,var(--rc) 50%,transparent);border-radius:8px;padding:2px 9px;font-weight:700;font-size:12px;white-space:nowrap}body.light .rwcnd{color:var(--t)}
.rwi{width:42px;height:42px;object-fit:contain;border-radius:8px;background:var(--l);cursor:zoom-in;flex:none}.rwe{width:42px;height:42px;display:flex;align-items:center;justify-content:center;font-size:22px;background:var(--l);border-radius:8px;flex:none}
.rwnote,.rwn{font-size:12px;margin-top:6px}.rwwin{font-size:12px;margin-top:8px}
.rwch{display:inline-flex;align-items:center;gap:3px;background:#ffd43b1f;border:1px solid #ffd43b55;border-radius:99px;padding:0 7px;font-size:11px;white-space:nowrap;margin:1px 2px;vertical-align:1px;font-weight:600}.rwch img{width:16px;height:16px;object-fit:contain;border-radius:3px}.rwch.no{opacity:.55;border-style:dashed}
#lbx{position:fixed;inset:0;background:#000d;z-index:20;display:none;align-items:center;justify-content:center;cursor:zoom-out;padding:16px}#lbx img{max-width:96vw;max-height:92vh;border-radius:10px}
.rwedr{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:6px 0;padding:6px;border:1px solid var(--l);border-radius:8px}.rwedr input{padding:5px 8px}.set .rwedr label{margin:0}
.upb{display:inline-flex!important;align-items:center;margin:0!important;cursor:pointer;border:1px solid var(--l);border-radius:7px;padding:4px 8px;background:var(--c)}
@media(max-width:700px){.rwb{flex-direction:column}.rwimg{width:100%}.rwbt{margin-left:0}}

#lng{position:absolute;top:12px;right:14px;z-index:12}header{padding-right:230px}
#lngb{display:flex;align-items:center;gap:7px;padding:4px 12px 4px 5px;border-radius:99px;font-weight:700;background:linear-gradient(135deg,#1e3a8a,#0e7490);color:#fff;border:1px solid #60a5fa88;box-shadow:0 2px 12px #0007;cursor:pointer}
#lngb:hover{filter:brightness(1.15)}#lngb .lgg svg{width:26px;height:26px;display:block;filter:drop-shadow(0 0 4px #38bdf888)}
#lngb .lgc{font-weight:500;font-size:12px;opacity:.85;max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.flg{width:22px;height:16px;object-fit:cover;border-radius:3px;vertical-align:-3px;box-shadow:0 0 0 1px #0004;flex:none}
#lngm{position:absolute;right:0;top:46px;width:min(390px,92vw);max-height:min(72vh,620px);display:none;flex-direction:column;background:var(--c);border:1px solid var(--l);border-radius:14px;box-shadow:0 14px 44px #000c;overflow:hidden}
#lngm.on{display:flex;animation:tin .18s ease-out}#lngq{margin:10px;width:auto}#lngl{overflow:auto;padding:0 6px 6px;flex:1}
.lgh{font-size:11px;color:var(--m);padding:10px 8px 4px;text-transform:uppercase;letter-spacing:.6px;font-weight:700}
.lgi{display:flex;align-items:center;gap:10px;padding:7px 8px;border-radius:9px;cursor:pointer;font-size:13px;line-height:1.25}.lgi:hover{background:var(--l)}.lgi.on{background:#3ddc8418;box-shadow:inset 3px 0 0 var(--g)}
.lgi .flg{width:28px;height:20px}.lgi .lgt{flex:1;min-width:0}.lgl{font-size:12px;color:var(--o)}.lgck{color:var(--g);font-weight:800}.lgf{font-size:10px;padding:6px 10px;border-top:1px solid var(--l)}
#lngov{position:fixed;left:14px;bottom:14px;z-index:30;background:var(--c);border:1px solid #60a5fa88;border-radius:12px;padding:10px 14px;box-shadow:0 8px 30px #000b;min-width:230px;display:none}
.lgspin svg{width:30px;height:30px;animation:lgsp 2s linear infinite;display:block}@keyframes lgsp{to{transform:rotate(360deg)}}
.lgbar{height:5px;background:var(--l);border-radius:3px;margin-top:8px;overflow:hidden}.lgbar i{display:block;height:5px;background:linear-gradient(90deg,#38bdf8,#34d399);transition:width .4s}
@media(min-width:1500px){#lng{position:fixed;top:12px;right:12px}#side{top:64px}header{padding-right:0}}
@media(max-width:700px){header{padding-right:0;padding-top:46px}#lng{top:10px;right:10px}#lngb .lgc{display:none}}

.kybtn{background:linear-gradient(135deg,#ff7a1a,#c2410c);color:#fff;border-color:#ff9f43;font-weight:700}
.kydrop{border:2px dashed #ff7a1a88;border-radius:12px;padding:12px;text-align:center;color:var(--m);margin-top:10px;font-size:13px;transition:all .2s}.kydrop,.kybtn,#kydrop{display:none !important}.kydrop.on{background:#ff7a1a22;color:var(--t);border-color:#ff7a1a}
.kybusy{display:flex;align-items:center;gap:10px;border-color:#ff7a1a88;margin-top:10px}.kybusy .lgspin svg{width:26px;height:26px}
.kyhelp{border-color:#ff7a1a66;background:linear-gradient(135deg,#ff7a1a14,var(--c) 60%)}
tr.kyfl td{background:#ffd43b26}#kyrt input{padding:5px 8px}

.kyg{display:grid;grid-template-columns:minmax(0,1fr);gap:12px;align-items:start}
.kyg>.wrap{overflow-x:auto}
.kyfeed{margin:0;max-height:360px;overflow:auto}
@media(min-width:1700px){.kyg{grid-template-columns:minmax(0,1fr) 330px}.kyfeed{max-height:720px}}
.kyt td,.kyt th{white-space:nowrap}
</style><header><h1>🥷 Ninja Zenshin · Live V21 🦊🔥</h1><span id=hp class=hp><i></i>conectando…</span><span id=st class=m>conectando…</span><span id=cd class=hp style="background:var(--o);color:#000;font-weight:800"></span><span id=cds style="display:inline-flex;gap:6px;flex-wrap:wrap;margin-left:6px"></span></header><div id=sstrip></div><div id=sb class=card style="margin:10px 0 0"></div><div id=tk><span class=lvb>● EN VIVO</span><span id=tkt>Esperando ataques…</span></div><div id=nt><div id=ntx></div><span class=ntph>📰 Aquí pasan en vivo los ataques, adelantamientos y puntos PvE…</span></div><div id=err></div><div id=stale></div><div id=wr></div><nav><button data-t=mine>🛡️ Mi clan</button><button data-t=clanes>📋 Clanes</button><button data-t=war>⚔️ Guerra</button><button data-t=race>🏁 Carrera</button><button data-t=obj>🎯 Objetivos</button><button data-t=jug>👥 Jugadores</button><button data-t=win>⏱ Ventana</button><button data-t=pve>🏆 PvE en vivo</button><button data-t=pvp>🥊 PvP</button><button data-t=kyubi>🦊 Kyubi</button><button data-t=hoy>🏅 Hoy</button><button data-t=feed>⚡ Actividad</button><button data-t=out>🚪 Salidas</button><button data-t=news>📰 Noticias</button><button data-t=hist>📜 Temporadas</button><button data-t=stats>📊 Estado</button><button data-t=cfg>🔧 Conexión</button>
<input id=q placeholder="Buscar…"><button id=snd onclick="toggleSnd()">🔇 Sonido</button><button id=nsb onclick="openSet()">🔔 Avisos</button></nav>
<div id=tb style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 10px"><button id=rf title="Actualizar ahora" onclick=refreshNow()>🔄</button>
<label class=m>Lectura <select id=sp onchange="setPoll(this.value)"><option value=0.5>🚀 0,5 s turbo</option><option value=1>⚡ 1 s</option><option value=2>2 s</option><option value=5>5 s</option><option value=15>15 s</option><option value=30>30 s</option><option value=60>1 min</option></select></label>
<label class=m>Ventana rápida <select id=sq onchange="setQw(this.value)"><option value=15>15 s</option><option value=30>30 s</option><option value=60>1 min</option><option value=300>5 min</option></select></label>
<label class=m>Clanes <select id=sn onchange="setRows(this.value)"><option>15</option><option>25</option><option>50</option><option>100</option></select></label>
<label class=m>Flechas <select id=sar onchange="setNS('arw',+this.value)"><option value=600>10 min</option><option value=1800>30 min</option><option value=3600>1 h</option><option value=21600>6 h</option></select></label>
<button id=rdb onclick="setNS('radar',!NS.radar)" title="Mostrar / ocultar el radar en vivo">📡 Radar</button>
<button id=th title="Modo claro / oscuro" onclick=toggleTheme()>🌙</button></div><div id=main></div><div id=ts></div><div id=md></div><div id=alm></div><div id=almb onclick="almOff()"></div><div id=side></div><div id=lbx onclick="this.style.display='none'"></div>
<script>
let SK=0;const NOW=()=>Date.now()*.001+SK;   // hora del servidor (el celular puede tener el reloj corrido)
let S={clans:[],events:[],pvp:[],pve:[],endpoints:[]},tab=localStorage.tab||'clanes',fav=new Set(JSON.parse(localStorage.fav||'[]')),snd=false,seen=0,OC=null,per=900;
const $=q=>document.querySelector(q),n=v=>v==null?'–':(v>0?'+':'')+v.toLocaleString(),k=v=>v>0?'up':v<0?'dn':'m',L=v=>v.toLocaleString();
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ago=t=>{const s=Math.max(0,Math.round(NOW()-t));return s<90?s+'s':s<5400?Math.round(s/60)+'m':Math.round(s/3600)+'h'};
let NS=Object.assign({scope:'all',min:1,voice:false,desk:false,arw:3600,atk:'all',amin:1,pve:'hit',pmin:1,pvp:'fav',alarm:true,vib:true,radar:true,nt:true},(()=>{try{return JSON.parse(localStorage.ns||'{}')}catch(e){return{}}})()),favm=new Set((()=>{try{return JSON.parse(localStorage.favm||'[]')}catch(e){return[]}})()),first=true,OB=null,OR=null,bsec=-1,msort={k:'rep',d:-1};
const SEEN=new Set(),VT={},MT={},BD={},BF={},MF={},HT={},ek=e=>e.t+'|'+e.ts+'|'+(e.clan||e.to||'')+'|'+(e.who||e.over||'')+'|'+(e.rev||0);
const PH=()=>'-'+(performance.now()/1000).toFixed(3)+'s';  // desfase de animacion: el fuego no "salta" al redibujar
const SV=p=>'<svg class=ic viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'+p+'</svg>';
const IC={tu:SV('<path d="M22 7l-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>'),td:SV('<path d="M22 17l-8.5-8.5-5 5L2 7"/><path d="M16 17h6v-6"/>'),
 tro:SV('<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6M18 9h1.5a2.5 2.5 0 0 0 0-5H18M4 22h16M10 14.7V17c0 .6-.5 1-1 1.2C7.9 18.8 7 20.2 7 22M14 14.7V17c0 .6.5 1 1 1.2 1.1.6 2 2 2 3.8M18 2H6v7a6 6 0 0 0 12 0z"/>'),
 cr:SV('<path d="M3 8l4.5 4.5L12 5l4.5 7.5L21 8l-2 10H5z"/><path d="M5 21h14"/>'),
 med:SV('<circle cx="12" cy="15" r="5"/><path d="M8.3 11.5 5 3h4l3 6 3-6h4l-3.3 8.5"/>'),
 um:SV('<circle cx="9" cy="7" r="4"/><path d="M2 21v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2M16 11h6"/>'),
 ua:SV('<circle cx="9" cy="7" r="4"/><path d="M2 21v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2M19 8v6M16 11h6"/>'),
 sw:SV('<path d="M14.5 17.5 3 6V3h3l11.5 11.5M13 19l6-6M16 16l4 4M19 21l2-2M14.5 6.5 18 3h3v3l-3.5 3.5M5 14l4 4M7 17l-3 3M3 19l2 2"/>'),
 fl:SV('<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>')};
const FP='<svg viewBox="0 0 24 24"><path class=fi d="M12 21.2c-2.1 0-3.6-1.5-3.6-3.4 0-1.5 1-2.6 1.9-3.8.4 1 1.1 1.6 2 1.7.1-1.6.9-3 2.2-3.9.5 2 2 3 2 5.4 0 2.3-2 4-4.5 4z"/><path class=fo d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z"/></svg>';
const flame=k=>k>0?`<span class="fire l${k}" title="Nivel de ataque ${k}/5" style="animation-delay:${PH()}">${FP}</span>`:'';
const rkIcon=r=>r==1?`<span class="ri t1" title="Top 1">${IC.tro}</span>`:r>1&&r<=3?`<span class="ri t2" title="Top ${r}">${IC.cr}</span>`:r>3&&r<=10?`<span class="ri t3" title="Top ${r}">${IC.med}</span>`:'';
const arrow=c=>{const t=c.tr,age=t?NOW()-t[0]:1e9;if(!t||age>NS.arw)return'<span class=ra></span>';const up=t[2]<t[1];return `<span class="ra ${up?'up':'dn'} ${age<20?'new':''}" title="${up?'Subió':'Bajó'} del #${t[1]} al #${t[2]} hace ${ago(t[0])}">${up?IC.tu:IC.td}</span>`};
const cnt=c=>{const m=String(c.members||'').match(/\d+/);return m?m[0]:'?'};
const dotHtml=x=>{const a=NOW()-(x.t||0),q=!x.t?'':a<60?'a1':a<300?'a2':a<900?'a3':'';return `<span class="dt ${q}" style="animation-delay:${PH()}" title="${x.t?'Último ataque hace '+ago(x.t):'Sin ataques vistos'}"></span>`};
const actBadge=x=>{const a=NOW()-(x.t||0);return x.t&&a<300?`<span class="act ${a<60?'lv':''}" style="animation-delay:${PH()}">${IC.sw} ${a<60?'EN VIVO':'ACTIVO'}</span>`:''};
let AC=null,lastBeep=0,lastSay=0;const TK={};
function beep(f){const t=Date.now();if(t-lastBeep<350)return;lastBeep=t;try{AC=AC||new AudioContext();const o=AC.createOscillator(),g=AC.createGain();g.gain.value=.18;o.connect(g);g.connect(AC.destination);o.frequency.value=f||660;o.start();o.stop(AC.currentTime+.22)}catch(e){}}
function say(t){if(!NS.voice||!window.speechSynthesis)return;const n=Date.now();if(n-lastSay<2500)return;lastSay=n;try{speechSynthesis.cancel();speechSynthesis.speak(Object.assign(new SpeechSynthesisUtterance(LANG!='es'?T_(t):t),{lang:LANG=='es'?'es-ES':bcp(LANG)}))}catch(e){}}
function toast(t,c,key,sys,big){const box=$('#ts');let d=key&&TK[key]&&TK[key].el.isConnected?TK[key]:null;
 if(!d){d={el:document.createElement('div')};if(key)TK[key]=d}else clearTimeout(d.to);
 d.el.innerHTML=t;if(typeof trTree=='function')trTree(d.el);d.el.style.borderColor=c;d.el.classList.toggle('big',!!big);box.prepend(d.el);
 d.to=setTimeout(()=>{d.el.remove();if(key&&TK[key]===d)delete TK[key]},9000);while(box.children.length>5)box.lastChild.remove();
 if(sys&&NS.desk&&document.hidden&&window.Notification&&Notification.permission=='granted')try{new Notification('Ninja Zenshin',{body:d.el.textContent,tag:key||undefined,renotify:!!key})}catch(e){}}
const EVS=new Map();let SSEOK=false;
const src=new EventSource('/stream');src.onerror=()=>{SSEOK=false;$('#st').textContent='🔴 reconectando…'};
src.onopen=()=>{SSEOK=true};
src.onmessage=m=>{let j;try{j=JSON.parse(m.data)}catch(x){return}if(!j||!j.clans)return;SSEOK=true;if(j.now)SK=j.now-Date.now()*.001;if(j.xv!=null&&j.xv!==XV){XV=j.xv;loadX()}
 if(j.full)EVS.clear();const fresh=[],now=NOW();
 for(const e of j.events||[]){if(!e||!e.t||(e.t=='hit'&&!Array.isArray(e.sus)))continue;EVS.set(e.id!=null?e.id:ek(e),e);if(!j.full&&now-e.ts<120)fresh.push(e)}
 while(EVS.size>2500)EVS.delete(EVS.keys().next().value);
 j.events=[...EVS.values()];const P=S;S=j;liveDiff(P);
 if(!localStorage.poll&&j.diag&&document.activeElement!==$('#sp'))$('#sp').value=String(j.diag.poll);
 fresh.sort((a,b)=>(a.t=='gain'?0:1)-(b.t=='gain'?0:1));   // primero las ganancias: asi cada ataque ya tiene su "rafaga" cuando llega el objetivo
 if(fresh.length){const ctx={att:new Set(fresh.filter(x=>x.t=='hit'&&x.sus&&x.sus.length).map(x=>x.to)),hitAtt:new Set(fresh.filter(x=>x.t=='hit'&&x.sus&&x.sus.length).map(x=>x.sus[0][0]))};
  fresh.forEach(e=>{try{onEv(e,ctx)}catch(err){console.error(err)}});try{headline(fresh);fresh.forEach(e=>ntEv(e,ctx))}catch(err){console.error(err)}}
 else if(j.full&&!ntSeeded){ntSeeded=true;try{S.events.slice(-10).forEach(e=>ntEv(e,null))}catch(err){console.error(err)}}
 draw()};
const burn=(cl,sec)=>{const now=NOW(),a={},mins=Math.max(.1,Math.min(sec,now-S.start)/60);
 S.events.filter(e=>e.t=='mem'&&(!cl||e.clan==cl)&&now-e.ts<=sec&&!S.clans.some(c=>c.name==e.who)).forEach(e=>{const kk=e.clan+'|'+e.who,x=a[kk]||(a[kk]={r:0,c:e.clan,w:e.who});x.r+=e.amt});
 return Object.values(a).map(x=>({w:x.w,c:x.c,r:x.r,m:x.r/mins})).sort((x,y)=>y.r-x.r)};
const PS='<select id=per onchange="per=+this.value;this.blur();draw()">'+[[15,'15 s'],[30,'30 s'],[60,'1 min'],[300,'5 min'],[900,'15 min'],[1800,'30 min'],[3600,'1 h'],[86400,'Todo']].map(([v,t])=>`<option value=${v}>${t}`).join('')+'</select>';
const spk=a=>{if(!a||a.length<2)return'';const mn=Math.min(...a),mx=Math.max(...a),r=mx-mn||1,p=a.map((v,i)=>(i*70/(a.length-1)).toFixed(1)+','+(20-(v-mn)*18/r).toFixed(1)).join(' ');return `<svg width=72 height=22><polyline fill=none stroke="${a[a.length-1]<a[0]?'#ff4d4d':'#3ddc84'}" stroke-width=1.6 points="${p}"/></svg>`};
const wars=sec=>{const now=NOW(),V={},A={};S.events.filter(e=>e.t=='hit'&&now-e.ts<sec).forEach(e=>{const v=V[e.to]||(V[e.to]={l:0,a:{}});v.l+=e.amt;e.sus.filter((s,i)=>i==0||s[2]>=30).forEach(([g,pts,cf])=>{const sh=Math.round(e.amt*cf/100),x=v.a[g]||(v.a[g]={n:0,p:0,c:0});x.n++;x.p+=sh;x.c=Math.max(x.c,cf);const y=A[g]||(A[g]={p:0,t:{}});y.p+=sh;y.t[e.to]=(y.t[e.to]||0)+1})});return{V,A}};
function warHtml(){const {V,A}=wars(600),F=Object.entries(V).sort((a,b)=>b[1].l-a[1].l),O=Object.entries(A).sort((a,b)=>b[1].p-a[1].p);
 const L1=F.map(([v,x])=>{const c=S.clans.find(c=>c.name==v)||{};return `<div class=card style="border-color:#ff4d4d66"><b class=dn>🩸 ${esc(v)}</b> <span class=m>#${c.rank||'?'}</span> <span class=dn>−${L(x.l)}</span><br>${Object.entries(x.a).sort((a,b)=>b[1].p-a[1].p).slice(0,3).map(([g,y])=>`<span class=sus>⚔️ <b>${esc(g)}</b> +${L(y.p)} · ×${y.n} · ${y.c}%</span>`).join('')||'<span class=m>Atacante no identificado (ningún clan subió)</span>'}</div>`}).join('')||'<div class=card>🟢 Ningún clan sangra (últimos 10 min)</div>';
 const R1=O.map(([g,x])=>`<div class=card style="border-color:#3ddc8466"><b class=up>⚔️ ${esc(g)}</b> <span class=up>+${L(x.p)}</span><br>${Object.entries(x.t).map(([t,n])=>`<span class=sus>🎯 ${esc(t)} ×${n}</span>`).join('')}</div>`).join('')||'<div class=card>😴 Sin ofensivas activas</div>';
 return `<div><h3 style=margin:0>🩸 Sangrando · quién los ataca</h3>${L1}</div><div><h3 style=margin:0>⚔️ Ofensivas · a quién atacan</h3>${R1}</div>`}
function duels(sec){const now=NOW(),D={},used=new Set();
 S.events.filter(e=>e.t=='hit'&&now-e.ts<sec).forEach(e=>{(e.sus.length?e.sus.filter((s,i)=>i==0||s[2]>=30):[[null,0,100]]).forEach(([g,pts,cf])=>{if(g)used.add(g+e.ts);const key=(g||'?')+'|'+e.to,d=D[key]||(D[key]={a:g,v:e.to,n:0,gain:0,loss:0,c:0,ts:0});d.n++;d.gain+=Math.round(e.amt*cf/100);d.loss+=Math.round(e.amt*cf/100);d.c=Math.max(d.c,cf);d.ts=Math.max(d.ts,e.ts)})});
 return{D:Object.values(D).sort((a,b)=>b.ts-a.ts),free:S.events.filter(e=>e.t=='gain'&&now-e.ts<sec&&!used.has(e.clan+e.ts))}}
function battle(){const now=NOW(),{D,free}=duels(qw),al=new Set();
 const cards=D.slice(0,4).map(d=>{const a=(fav.has(d.v)||isMine(d.v))&&now-d.ts<120;if(a)al.add(d.a||'alguien');
  return `<div class="duel ${a?'alarm':''}"><div class=s><b class="up nm" data-o="${d.a?esc(d.a):''}">${d.a?esc(d.a):'❓ No identificado'}</b> ${d.a?fire(S.clans.find(x=>x.name==d.a)||{}):''}<br><span class=up>${d.gain?'+'+L(d.gain):''}</span></div><div class=arw>⚔️ ➜</div><div class="s r"><b class="dn nm" data-o="${esc(d.v)}">${esc(d.v)}</b><br><span class=dn>−${L(d.loss)}</span> <span class=m>· ×${d.n} · ${d.c}% · hace ${ago(d.ts)}</span></div></div>`}).join('');
 const fr=free.slice(-6).map(e=>`<span class=sus>⚔️ ${esc(e.clan)} +${e.amt} · objetivo sin identificar</span>`).join('');
 ALT=al.size?[...al][0]:'';
 return `<div><h3 class=rt>⚔️ Batalla en vivo <span class=m style="font-weight:400;font-size:12px">(últimos ${QWL[qw]})</span></h3>${cards||'<div class="card m">🟢 Sin combates en este momento</div>'}</div>`}
function mapHtml(){const {D}=duels(per),ns=[];D.forEach(d=>[d.a,d.v].forEach(x=>x&&!ns.includes(x)&&ns.push(x)));S.clans.slice(0,8).forEach(c=>!ns.includes(c.name)&&ns.push(c.name));
 const N=ns.slice(0,16),P={},mx=Math.max(1,...D.map(d=>d.gain||d.loss));N.forEach((x,i)=>{const t=i/N.length*6.2832-1.5708;P[x]=[350+230*Math.cos(t),225+165*Math.sin(t)]});
 const E=D.filter(d=>d.a&&P[d.a]&&P[d.v]).map(d=>{const a=P[d.a],b=P[d.v],dx=b[0]-a[0],dy=b[1]-a[1],l=Math.hypot(dx,dy)||1,ux=dx/l,uy=dy/l,cx=(a[0]+b[0])/2-uy*28,cy=(a[1]+b[1])/2+ux*28;
  return `<path class=eg d="M${a[0]+ux*22},${a[1]+uy*22} Q${cx},${cy} ${b[0]-ux*26},${b[1]-uy*26}" fill=none stroke="#ff9f43" stroke-width="${(1.5+4*(d.gain||d.loss)/mx).toFixed(1)}" marker-end="url(#ar)"><title>${esc(d.a)} → ${esc(d.v)}: +${d.gain} / −${d.loss} (×${d.n})</title></path>`}).join('');
 const V=N.map(x=>{const c=S.clans.find(y=>y.name==x)||{},col=c.l10>0?'#ff4d4d':c.g10>0?'#3ddc84':'#6a6a75';return `<circle cx="${P[x][0]}" cy="${P[x][1]}" r="19" fill="${col}33" stroke="${col}" stroke-width="2"/><text x="${P[x][0]}" y="${P[x][1]+4}" text-anchor="middle" font-size="11" fill="currentColor">${c.rank||''}</text><text x="${P[x][0]}" y="${P[x][1]+34}" text-anchor="middle" font-size="11" fill="currentColor">${esc(x.slice(0,16))}</text>`}).join('');
 return `<h3 style=margin:0>🗺️ Mapa de guerra ${PS}</h3><p class=m>Flecha = quién ataca a quién · grosor = puntos · verde atacando · rojo sangrando.</p><svg viewBox="0 0 700 450" width="100%"><defs><marker id="ar" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L10,5L0,10z" fill="#ff9f43"/></marker></defs>${E}${V}</svg>`}
function rivHtml(){const R=Object.entries(S.pairs||{}).map(([p,v])=>[...p.split('|'),...v]).sort((a,b)=>b[3]-a[3]).slice(0,12);
 return R.length?R.map(([a,b,c,t,l])=>`<div class=card>⚔️ <b class=up>${esc(a)}</b> ha atacado a <b class=dn>${esc(b)}</b> <span class=m>×${c}</span> · <span class=up>+${L(t)}</span> total · <span class=m>última hace ${ago(l)}</span></div>`).join(''):'<p class=m>Aún no hay rivalidades detectadas (se afina con el tiempo).</p>'}
const medal=i=>['🏆','👑','🥈'][i]||i+1;
const burnT=l=>'<div class=wrap><table><tr><th>#<th>Jugador<th>Clan<th>Rep/min<th>Rep</tr>'+(l.map((x,i)=>`<tr><td>${medal(i)}<td><b class=nm onclick="openMember(this.dataset.c,this.dataset.w)" data-c="${esc(x.c)}" data-w="${esc(x.w)}">${esc(x.w)}</b><td class="m nm" data-o="${esc(x.c)}">${esc(x.c)}<td class=up>${x.m.toFixed(1)}<td class=up>+${L(x.r)}</tr>`).join('')||'<tr><td colspan=5 class=m>Sin ataques de jugadores en este periodo (¿configuraste la pestaña 🔧 Conexión?)</tr>')+'</table></div>';
function draw0(){
 const lag=S.ts?Math.max(0,NOW()/1000-S.ts):0;const lagCls=lag>30?'atr':lag>10?'warn':'ok';const lagTxt=lag<1?'ahora':lag<60?Math.round(lag)+'s':Math.round(lag/60)+'m';$('#st').innerHTML=S.clans.length+' clanes · '+(S.online||[]).filter(o=>NOW()-o[2]<=900).length+' jugadores activos (15 min) · <span class="'+lagCls+'">📡 '+lagTxt+'</span>';$('#err').innerHTML=S.error?'<div class=err>⚠️ '+esc(S.error)+'</div>':'';
 {const bt=battle(),n2=NOW();$('#wr').style.display=NS.radar?'':'none';if(NS.radar)$('#wr').innerHTML=radarHtml()+bt+pveNow();$('#nt').style.display=NS.nt?'':'none';const anyA=S.events.some(e=>e.t=='gain'&&n2-e.ts<10);document.title=(UNR?'('+UNR+') ':'')+(ALT?'🚨 ¡TE ATACAN! '+ALT:mineTitle());/* v17: countdowns en header vivo */try{const cdel=$('#cds');if(cdel)cdel.innerHTML=cdHtml('pve')+' '+cdHtml('pvp')+' '+cdHtml('cw')}catch(e){}setFav(ALT?'🩸':anyA?'⚔️':'🥷')}document.querySelectorAll('nav button[data-t]').forEach(b=>b.classList.toggle('on',b.dataset.t==tab));
 const q=$('#q').value.toLowerCase(),now=NOW(),ev=S.events,C=S.clans;let h='';
 const bl=C.filter(c=>c.l10>0).sort((a,b)=>b.l10-a.l10),at=C.filter(c=>c.g10>0).sort((a,b)=>b.g10-a.g10);
 if(tab=='clanes')h=rewCard('cw')+'<div class=wrap><table><tr><th><th>#<th>Clan<th>Atk -2<th>Atk -1<th>Activos<th>Reputación<th>Falta<th>⚔ Ventana<th>'+QWL[qw]+'<th>1 h<th>Tendencia<th>Sesión<th>Estado</tr>'+
  C.map((c,i)=>({c,i})).filter(({c})=>(c.name+c.master).toLowerCase().includes(q)).slice(0,rowsN).map(({c,i})=>{const u=C[i-1],sg=now-(c.lbl||0)<20,f=CF[c.name],fa=f?(Date.now()-f[0])/1000:99,fr=fa<2.6;
  return `<tr class="${c.l10>0?'bleed':c.g10>0?'hit':''} ${sg?'sgr':''} ${isMine(c.name)?'mine':''} ${fr?'pfl '+(f[1]>0?'gr':'rd'):''}" style="${sg?'animation-delay:'+PH()+';':''}${fr?'--d:-'+fa.toFixed(2)+'s':''}"><td><span class="star ${fav.has(c.name)?'on':''}" data-f="${esc(c.name)}">★</span> <span class=bb data-b="${esc(c.name)}" title="Top Burn de ${esc(c.name)}">${IC.fl}</span><td class=rk>${arrow(c)}<b>${c.rank}</b><td>${rkIcon(c.rank)} <b class=nm data-o="${esc(c.name)}" title="${esc(tip(c))}">${esc(c.name)}</b> <span class=m>(${esc(c.master)})</span> <span class=m>[${cnt(c)}]</span> ${mvBadge(c)} ${fire(c)} ${isMine(c.name)?'🛡️':''} ${champ(c.name)} ${debtBadge(c.name)} ${rewChips('cw',c.rank,c.rep,1)}<td>${wcell((c.w||[])[0])}<td>${wcell((c.w||[])[1])}<td>${actPill(c)}<td class=num><b>${L(c.rep)}</b>${fr?`<span class="bub ${f[1]<0?'dn':''}" style="animation-delay:-${fa.toFixed(2)}s">${f[1]>0?'+':''}${L(f[1])}</span>`:''}<td class=m>${u?L(u.rep-c.rep):'–'}<td>${wnow(c)}<td class=${k(dq(c))}>${n(dq(c))}<td class=${k(c.d60)}>${n(c.d60)}<td>${spk(c.sp)}<td><span class=up>${n(c.sess)}</span> <span class=dn>${c.sessL?'−'+L(c.sessL):''}</span><td>${estado(c)}</tr>`}).join('')+'</table></div>';
 if(tab=='mine')h=mineHtml();
 if(tab=='obj')h='<h3>⏳ Próximos adelantamientos</h3>'+etaT()+'<h3>🏁 Final de temporada</h3>'+finalT()+'<h3>🕸️ Rivalidades</h3>'+rivHtml();
 if(tab=='jug')h=onlineHtml()+globalHtml();
 if(tab=='out')h=outHtml();
 if(tab=='war')h=warTab();if(tab=='win')h=winHtml();if(tab=='hist')h=rewCard('cw')+histHtml();if(tab=='kyubi'){try{h=kyubiLiveHtml()+kyHtml()}catch(e){h='<div class=card style="border:2px solid #ff6b6b"><b>⚠️ Error panel Kyubi:</b> '+e.message+'</div>'+kyHtml()}}
 if(tab=='pve')h=pveHtml();if(tab=='pvp')h=pvpHtml();if(tab=='hoy')h=hoyHtml();
 if(tab=='feed'){const ic={leave:'🚪',join:'🆕',rank:'📊',bleed:'🩸',gain:'⚔️',hit:'🎯',pvp:'🥊',pve:'🏆',mem:'🔥',pass:'📈',pvepass:'🏆',pvein:'🆕'},R=x=>x.rep?` <span class=m>→ REP ${L(x.rep)} (#${x.rank})</span>`:'',
  tx=e=>e.t=='pass'?`<b>${esc(e.clan)}</b> <span class=up>${IC.tu} superó a</span> <b>${esc(e.over)}</b> <span class=dn>${IC.td}</span> → #${e.r}`:e.t=='leave'?`<b>${esc(e.who)}</b> salió de <b>${esc(e.clan)}</b> <span class=dn>−${L(e.rep)} REP</span>`:e.t=='join'?`<b>${esc(e.who)}</b> se unió a <b>${esc(e.clan)}</b>${e.frm?' <span class=m>(venía de '+esc(e.frm)+')</span>':''}`:e.t=='rank'?`<b>${esc(e.clan)}</b> ${e.b<e.a?'<span class=up>▲ subió':'<span class=dn>▼ bajó'} #${e.a} → #${e.b}</span>`:e.t=='bleed'?`<b>${esc(e.clan)}</b> perdió <span class=dn>${e.amt}</span>`:e.t=='gain'?`<b class=nm data-o="${esc(e.clan)}">${esc(e.clan)}</b> atacó <span class=up>+${L(e.amt)}</span>${R(e)}`:e.t=='hit'?`<b class=nm data-o="${esc(e.to)}">${esc(e.to)}</b> <span class=dn>−${L(e.amt)}</span> <span class=m>← ${e.sus.map(x=>esc(x[0])+' '+x[2]+'%').join(', ')||(e.pending?'identificando…':'atacante no identificado')}</span>${e.ex?' <span class=g title="Las ganancias coinciden exactamente con la pérdida">exacto</span>':''}`:e.t=='mem'?`<b>${esc(e.who)}</b> <span class=m>(${esc(e.clan)})</span> <span class=up>+${e.amt}</span>`:e.t=='pvp'?`<b>${esc(e.who)}</b> PvP ${e.w?'<span class=up>+'+e.w+' win</span> ':''}${e.l?'<span class=dn>+'+e.l+' lose</span>':''}`:e.t=='pvepass'?`<b>${esc(e.who)}</b> <span class=up>superó a</span> <b>${esc(e.over)}</b> en PvE → #${e.r}`:e.t=='pvein'?`<b>${esc(e.who)}</b> entró al ranking PvE <span class=m>#${e.rank} · ${L(e.score||0)}</span>`:`<b>${esc(e.who)}</b> PvE <span class=up>+${L(e.amt)}</span>${e.score?` <span class=m>→ ${L(e.score)} (#${e.rank})</span>`:''}${e.t30?' ⭐ Top 30':''}${e.gift?' 🎁 300k':''}`;
  const chips=[['all','Todo'],['atk','⚔️ Ataques'],['pve','🏆 PvE'],['pvp','🥊 PvP'],['mem','👥 Miembros'],['rank','📊 Puestos']].map(([x,t])=>`<span class="fchip ${feedF==x?'on':''}" data-ff="${x}">${t}</span>`).join('');
  h='<div>'+chips+'</div>'+([...ev].filter(e=>e.t!='bleed'&&(!FF[feedF]||FF[feedF].includes(e.t))).reverse().slice(0,150).map(e=>`<div class=card>${ic[e.t]||'•'} ${tx(e)} <span class=m style="float:right">hace <span data-ts="${e.ts}">${ago(e.ts)}</span></span></div>`).join('')||'<p class=m>Aún sin eventos.</p>')}
 if(tab=='cfg')h=`${S.lan?`<div class=card>📱 <b>Ábrelo en tu celular</b> (misma WiFi): <code>${esc(S.lan)}</code> <span class=m>· si no abre, permite Python en el firewall de Windows</span></div>`:''}<div class=card><b>Jugadores por clan (Top Burn)</b><p class=m>La página pública solo muestra clanes. Los miembros salen al hacer clic en un clan. Abre la página oficial → F12 → Network → haz clic en un clan → copia la URL de esa petición y pégala aquí (usa <code>{id}</code> o <code>{name}</code> donde va el clan).</p>
  <input id=mu style="width:100%" placeholder="https://ninjazenshin.online/api/clan/{id}/members" value="${esc(S.url||'')}"><br><br><button onclick="fetch('/cfg',{method:'POST',body:JSON.stringify({url:$('#mu').value})}).then(()=>toast('Guardado','#ff9f43'))">Guardar y probar</button>
  <p>Estado: <b>${esc(S.mem_msg||'')}</b></p><p class=m>Posibles endpoints detectados en el sitio:</p>${(S.endpoints||[]).map(u=>`<div class=m><code>${esc(u)}</code></div>`).join('')||'<div class=m>Ninguno detectado (la página no los expone en su JS).</div>'}</div><div class=card><b>🩺 Diagnóstico</b><div id=diag>${diagHtml()}</div></div><div class=card><b>🔔 Alertas de Discord</b> <span class=m>(necesitas el webhook de abajo)</span>${[['rank','Cambio de rank de mi clan'],['atk','Mi clan está atacando (sobre el umbral)'],['hit','Mi clan sangra / ataques grandes'],['mon','Clanes monitoreados con ★'],['summ','Resumen de cada ventana de 30 min'],['daily','Resumen diario (medianoche UTC)'],['move','Entradas y salidas de miembros (mi clan y ★)'],['any','⚔️ Cualquier clan empieza a atacar (1 aviso por ráfaga, sobre el umbral)'],['pve','🏆 PvE: entra al Top 30, pasa 300k o suma más del umbral PvE']].map(([k,t])=>`<label style="display:block;margin:6px 0"><input type=checkbox ${(S.cfg||{})[k]?'checked':''} onchange="setCfg('${k}',this.checked)"> ${t}</label>`).join('')}<p>Umbral de REP <input type=number value="${(S.cfg||{}).thr||100}" style="width:90px" onchange="setCfg('thr',this.value)"> · Umbral PvE <input type=number value="${(S.cfg||{}).pthr||5000}" style="width:100px" onchange="setCfg('pthr',this.value)"> · Avisar solo de <input type=number min=0 max=24 value="${(S.cfg||{}).h0||0}" style="width:60px" onchange="setCfg('h0',this.value)"> a <input type=number min=0 max=24 value="${(S.cfg||{}).h1??24}" style="width:60px" onchange="setCfg('h1',this.value)"> h (tu hora · puede cruzar la medianoche, ej. 22 → 6)</p></div><div class=card><b>📅 Fecha final de temporada</b> <span class=m>(${(S.season||{}).src||'sin datos'})</span><p class=m>Si la cuenta atrás no coincide con la de la página oficial, escribe lo que ella marca (ej: <code>24d 14:07:17</code>).</p><input id=se placeholder="24d 14:07:17"> <button onclick="fetch('/cfg',{method:'POST',body:JSON.stringify({season_end:$('#se').value})}).then(()=>toast('Guardado','#ff9f43'))">Guardar</button></div><div class=card><b>🧲 Asistente de conexión</b> <span class=m>(solo si la auto-detección falla)</span><p class=m>1) Abre la página oficial del juego. 2) F12 → pestaña Console → pega el texto de abajo y Enter. 3) Haz clic en cualquier clan. El tracker aprende solo cómo pedir los miembros.</p><textarea id=snp readonly style="width:100%;height:64px">${esc(SNIP)}</textarea><br><button onclick="navigator.clipboard.writeText($('#snp').value);toast('Copiado','#ff9f43')">Copiar</button> <button onclick="fetch('/cfg',{method:'POST',body:JSON.stringify({retry:1})})">Reintentar auto-detección</button></div><div class=card><b>📱 Alertas a Discord (al celular)</b> ${S.wh?'<span class=up>✅ activo</span>':''}<p class=m>Ajustes del canal → Integraciones → Webhooks → copia la URL. Recibirás cada sangrado con los clanes sospechosos.</p><input id=wh style="width:100%" placeholder="https://discord.com/api/webhooks/..."><br><br><button onclick="fetch('/cfg',{method:'POST',body:JSON.stringify({webhook:$('#wh').value})}).then(()=>toast('Webhook guardado','#ff9f43'))">Guardar webhook</button></div>`;
 if(tab=='mine'&&$('#mcfg')&&!force)$('#mdyn').innerHTML=mineDyn();else if(tab=='jug'&&$('#gcfg')&&!force)$('#gdyn').innerHTML=globalDyn();else if(tab=='race'){if(!$('#race')||force)$('#main').innerHTML=raceShell();raceUpdate()}else if(tab=='news'){$('#main').innerHTML=newsHtml()}else if(tab=='stats'){$('#main').innerHTML=statsHtml()}else if(tab!='cfg'||!$('#mu'))$('#main').innerHTML=h;force=false;document.querySelectorAll('#per').forEach(x=>x.value=per);
}
const MEM={},SNIP="(()=>{const f=window.fetch.bind(window),P=(u,m,b)=>f('"+location.origin+"/cfg',{method:'POST',mode:'no-cors',body:JSON.stringify({sniff:{url:String(u),method:m||'GET',body:b?String(b):null}})}).catch(()=>{});window.fetch=function(u,o){P(u&&u.url||u,o&&o.method,o&&o.body);return f.apply(null,arguments)};const X=XMLHttpRequest.prototype,op=X.open,sd=X.send;X.open=function(m,u){this._m=m;this._u=u;return op.apply(this,arguments)};X.send=function(b){P(this._u,this._m,b);return sd.apply(this,arguments)};console.log('Listo: haz clic en un clan')})()";
function loadMem(n){MEM[n]=MEM[n]||{};return fetch('/members?name='+encodeURIComponent(n)).then(r=>r.json()).then(j=>{const old=(MEM[n]||{}).members;if(old&&j.members)j.members.forEach(x=>{const o=old.find(y=>y.name==x.name);if(o&&x.rep>o.rep)MF[n+'|'+x.name]=[Date.now(),x.rep-o.rep]});MEM[n]=Object.assign(j,{t:Date.now()});if(mineNeed&&j.members){mineNeed=false;force=true}draw()}).catch(()=>{MEM[n]={err:'Sin respuesta del servidor'};draw()})}
async function loadAll(){for(const c of S.clans.filter(c=>c.rep>0))await loadMem(c.name)}
setInterval(()=>{if(OC&&MEM[OC]&&!MEM[OC].err)loadMem(OC);if(OC)loadClanDetail(OC,cdRange)},4000);
let cdRange=21600;const CD={},RANGES=[[1800,'30m'],[21600,'6h'],[86400,'24h'],[604800,'7d'],[0,'Temporada']];
function loadClanDetail(name,range){return fetch('/clan?name='+encodeURIComponent(name)+'&range='+range).then(r=>r.json()).then(j=>{CD[name+'|'+range]=j;if(OC==name&&cdRange==range)draw()}).catch(()=>{})}
function setRange(v){cdRange=+v;if(OC)loadClanDetail(OC,cdRange);draw()}
const tile=(t,v)=>`<div class=card style="margin:0;text-align:center;padding:8px 4px"><span class=m style="font-size:11px">${t}</span><br><b>${v}</b></div>`;
function graphSvg(d){if(!d||!d.t||d.t.length<2)return'<p class=m style="height:90px;display:flex;align-items:center;justify-content:center">Sin datos suficientes todavía para graficar este periodo.</p>';
 const W=300,H=90,vs=d.v.filter(x=>x!=null),mn=Math.min(...vs),mx=Math.max(...vs),rg=(mx-mn)||1,t0=d.t[0],ts=d.t[d.t.length-1]-t0||1;
 const pts=d.t.map((t,i)=>d.v[i]==null?null:[(t-t0)/ts*W,H-6-(d.v[i]-mn)/rg*(H-16)]).filter(Boolean),line=pts.map(p=>p.join(',')).join(' ');
 const bm=Math.max(1,...d.g,...d.l),nb=d.g.length,bw=W/nb;
 const bars=d.g.map((g,i)=>{const l=d.l[i],x=i*bw;return (g?`<rect x="${x.toFixed(1)}" y="${(H-6-g/bm*20).toFixed(1)}" width="${(bw*.7).toFixed(1)}" height="${(g/bm*20).toFixed(1)}" fill="var(--g)" opacity=".55"/>`:'')+(l?`<rect x="${x.toFixed(1)}" y="${(H-6).toFixed(1)}" width="${(bw*.7).toFixed(1)}" height="${(l/bm*20).toFixed(1)}" fill="var(--r)" opacity=".55"/>`:'')}).join('');
 const chg=vs.length>1?vs[vs.length-1]-vs[0]:0;
 return `<div class=m style="font-size:11px;margin:8px 0 2px">📈 Reputación en este periodo: <b class="${chg>=0?'up':'dn'}">${n(chg)}</b></div><svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">${bars}<polyline fill="none" stroke="var(--o)" stroke-width="2" points="${line}"/></svg>`}
const dayBars=days=>{if(!days||!days.length)return'';const mx=Math.max(1,...days.map(x=>x[1]));
 return '<div class=m style="font-size:11px;margin-top:10px">🗓️ Reputación ganada por día (últimos 7)</div><div style="display:flex;gap:6px;align-items:flex-end;height:54px;margin-top:4px">'+days.map(([lb,v])=>`<div style="flex:1;text-align:center"><div title="${lb}: +${L(v)}" style="height:${4+40*v/mx}px;background:var(--o);opacity:${v?.35+.65*v/mx:.15};border-radius:3px 3px 0 0"></div><div class=m style="font-size:9px;margin-top:2px">${lb.split(' ')[0]}</div></div>`).join('')+'</div>'}
const winMins=()=>Math.max(.5,(NOW()%1800)/60);
function burnRows(R){const mins=winMins();
 return R.map((x,i)=>`<tr class="${OC==MY().clan&&x.name==MY().player?'mine':''}"><td>${medal(i)}<td><b class=nm onclick="openMember(OC,this.dataset.w)" data-w="${esc(x.name)}">${esc(x.name)}</b> ${mfire(x,R,OC)} ${dot(x)}<td class=${(x.w||[])[2]?'up':'m'}>${(x.w||[])[2]?(x.w[2]/mins).toFixed(1):'–'}<td class=${(x.w||[])[2]?'up':'m'}>${(x.w||[])[2]?'+'+L(x.w[2]):'0'}<td>${hgBars(x.hg)}</tr>`).join('')}
const hgBars=hg=>{if(!hg||!hg.some(v=>v))return'<span class=m>–</span>';const mx=Math.max(1,...hg);
 return '<div style="display:flex;gap:2px;align-items:flex-end;height:20px">'+hg.slice().reverse().map((v,i)=>`<div title="Hace ${6-i}h: +${L(v)}" style="width:8px;height:${v?4+16*v/mx:2}px;background:var(--g);opacity:${v?.4+.6*v/mx:.15};border-radius:1px"></div>`).join('')+'</div>'}
function closeAll(){OC=null;omem=null;OB=null;OR=null;NSET=false;XEDIT=null;SHEDIT=false;KYR=null;draw()}
function openClan(o){OC=o;OB=null;OR=null;omem=null;cdRange=21600;loadMem(o);loadClanDetail(o,cdRange);loadHeat(o);draw()}
function sortM(q){msort=msort.k==q?{k:q,d:-msort.d}:{k:q,d:q=='name'||q=='i'?1:-1};draw()}
function estado(c){const now=NOW(),lv=now-(c.last||0)<30,sg=now-(c.lbl||0)<20,by=(c.by||[]).slice(0,2),tg=(c.tg||[]).slice(0,2),nm=(x,cl)=>`<b class="${cl} nm" data-o="${esc(x[0])}">${esc(x[0])}</b>`;let s='';
 if(c.l10>0)s+=`<span class="b ${sg?'live':''}" style="animation-delay:${PH()}">🩸 SANGRANDO −${L(c.l10)}</span>`+(by.length?' <span class=m>← ataca:</span> '+by.map(x=>nm(x,'up')).join(', '):'');
 if(c.g10>0)s+=(s?'<br>':'')+`<span class="g ${lv&&!sg?'live':''}" style="animation-delay:${PH()}">⚔️ ${lv?'EN VIVO · ':''}atacando +${L(c.g10)}</span>`+(tg.length?' <span class=m>➜</span> '+tg.map(x=>nm(x,'dn')).join(', '):'');
 return s}
const MCOLS=[['Top','i'],['Nombre','name'],['Atk -2','w0'],['Atk -1','w1'],['Nivel','lv'],['Reputación','rep'],['⚔','w2'],['6h','g6'],['24h','g24'],['7d','g7']];
function memTable(R,cl){const byRep=[...R].sort((a,b)=>b.rep-a.rep),pos={};byRep.forEach((x,i)=>pos[x.name]=i);
 const val=(x,q)=>q=='i'?pos[x.name]:q=='name'?x.name.toLowerCase():q[0]=='w'?(x.w||[])[+q[1]]||0:(x[q]||0);
 const rows=[...R].sort((a,b)=>{const A=val(a,msort.k),B=val(b,msort.k);return (A<B?-1:A>B?1:0)*msort.d||b.rep-a.rep});
 const tops={};['w0','w1','w2','g6','g24','g7'].forEach(q=>tops[q]=[...new Set(R.map(x=>val(x,q)).filter(v=>v>0))].sort((a,b)=>b-a).slice(0,2));
 const cell=(x,q)=>{const v=val(x,q),t=tops[q];return v>0?`<td class="num ${v==t[0]?'t1':v==t[1]?'t2':'m'}">+${L(v)}`:v<0?`<td class="num dn">${L(v)}`:'<td class="num m">0'};
 const me=MY();
 return '<div class=wrap><table class=mt><tr>'+MCOLS.map(([t,q])=>`<th class="srt ${q=='i'||q=='name'?'':'num'} ${msort.k==q?'on':''}" onclick="sortM('${q}')">${t} <span class=m style="font-size:10px">${msort.k==q?(msort.d<0?'▼':'▲'):'⇅'}</span>`).join('')+'</tr>'+rows.map(x=>{const p=pos[x.name],prev=byRep[p-1],f=MF[cl+'|'+x.name],fr=f&&Date.now()-f[0]<4000,fk=cl+'|'+x.name;
  return `<tr class="${isMine(cl)&&x.name==me.player?'mine':''} ${fr?'flashg':''}"><td>${dotHtml(x)} <b>${p+1}</b><td><span class="star ${favm.has(fk)?'on':''}" data-mf="${esc(fk)}" title="Vigilar a este jugador (aviso cuando ataque)">★</span> ${rkIcon(p+1)} <b class=nm onclick="openMember(this.dataset.c,this.dataset.w)" data-c="${esc(cl)}" data-w="${esc(x.name)}">${esc(x.name)}</b> ${mfire(x,byRep,cl)} ${actBadge(x)} ${fr?`<b class=up>+${L(f[1])}</b>`:''}${cell(x,'w0')}${cell(x,'w1')}<td class=num>${x.lv||'–'}<td class=num><b>${L(x.rep)}</b>${prev?`<sup class=gap>-${L(prev.rep-x.rep)}</sup>`:''}${cell(x,'w2')}${cell(x,'g6')}${cell(x,'g24')}${cell(x,'g7')}</tr>`}).join('')+'</table></div><p class=m style="font-size:11px">● verde = atacó hace menos de 5 min · ⚔ = ventana actual de 30 min · 6h / 24h / 7d = REP ganada (se completa mientras el tracker está abierto) · el número pequeño es la distancia con el de arriba · toca una columna para ordenar · ★ = avisarme cuando ataque.</p>'}
function modalHtml(){const C=S.clans,c=C.find(x=>x.name==OC)||{},M=MEM[OC]||{},R=M.members||[],d=CD[OC+'|'+cdRange],tt=d?d.tiles:null,a=c.act||[0,0,0,0],i=C.indexOf(c),u=i>0?C[i-1]:null,dn=i>=0?C[i+1]:null,mv=c.mv||{},no=(mv.o||[]).length,ni=(mv.i||[]).length,st=estado(c);
 return `<div><div class=mh><div style="display:flex;align-items:center;gap:10px;min-width:0"><div class=rc>${c.rank||'?'}</div><div style="min-width:0"><h3 style="margin:0">${arrow(c)}${rkIcon(c.rank)} ${esc(OC)} ${fire(c)} ${isMine(OC)?'🛡️':''}</h3><span class=m>(${esc(c.master||'')}) · [${cnt(c)}] ${mvBadge(c)} ${champ(OC)} ${debtBadge(OC)}</span></div></div><div style="display:flex;gap:10px;align-items:center"><span class="star ${fav.has(OC)?'on':''}" data-f="${esc(OC)}" title="Monitorear este clan" style="font-size:20px">★</span><button onclick="closeAll()">✕</button></div></div>
 ${st?`<div class=card style="margin:10px 0 0">${st}</div>`:''}
 <div class=mb><h3 style=margin:0>Miembros <span class=m>–</span> <span class=rc2>${c.rank||'?'}</span> ${rkIcon(c.rank)} <span class=m style="font-weight:400">${esc(OC)} [${R.length||cnt(c)}]</span></h3><div class=mbtn><button class=rbtn onclick="OR=OC;draw()">${IC.um} Cambios de Roster${no||ni?` <b>${no?'−'+no:''}${no&&ni?' ':''}${ni?'+'+ni:''}</b>`:''}</button><button class=bbtn onclick="openBurn(OC)">${flame(3)} Top Burn</button><span class="b2 ${a[0]?'on':''}">${a[0]} activos 1m</span><span class="b2 ${a[3]?'on':''}">${a[3]} activos 1h</span></div></div>
 ${M.err?`<div class=err>${esc(M.err)}</div>`:!R.length?'<p class=m>Cargando miembros…</p>':memTable(R,OC)}
 <h4>📊 Resumen</h4><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px">${tile('Reputación',L(c.rep||0))}${tile('Puesto','#'+(c.rank||'?'))}${tile('Para subir',u?L(u.rep-c.rep)+'<br><span class=m style="font-size:11px;font-weight:400">a '+esc(u.name)+'</span>':'👑 líder')}${tile('Ventaja',dn?L(c.rep-dn.rep)+'<br><span class=m style="font-size:11px;font-weight:400">sobre '+esc(dn.name)+'</span>':'–')}${tile('Última hora',`<span class=${k(c.d60)}>${n(c.d60)}</span>`)}${tile('Ritmo',(c.rpm||0)+'/min')}</div>
 <h4>↗ Total ganado</h4><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:8px">${tile('Atk -2',n((c.w||[])[0]))}${tile('Atk -1',n((c.w||[])[1]))}${tile('Ventana',n((c.w||[])[2]))}${tile('6h',tt?'+'+L(tt.h6):'…')}${tile('24h',tt?'+'+L(tt.h24):'…')}${tile('7d',tt?'+'+L(tt.d7):'…')}</div>
 <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap">${RANGES.map(([v,l])=>`<button class="${cdRange==v?'on':''}" style="padding:4px 10px;font-size:12px" onclick="setRange(${v})">${l}</button>`).join('')}</div>
 ${graphSvg(d)}${dayBars(d&&d.days)}
 <h4>🗓️ Actividad semanal</h4>${heatHtml(OC)}</div>`}
const BPER=[[-1,'Atk (ventana actual)'],[-2,'Atk -1 (anterior)'],[300,'5 min'],[900,'15 min'],[3600,'1 hora'],[21600,'6 horas'],[86400,'24 horas']];
function loadBurn(){if(!OB)return;const c=OB,s=bsec;fetch('/burn?clan='+encodeURIComponent(c)+'&sec='+s).then(r=>r.json()).then(j=>{const q=c+'|'+s,old=BD[q];if(old)j.members.forEach(x=>{const o=old.members.find(y=>y.name==x.name);if(o&&x.g>o.g)BF[c+'|'+x.name]=[Date.now(),x.g-o.g]});BD[q]=j;if(OB==c)draw()}).catch(()=>{})}
setInterval(loadBurn,2500);
function openBurn(c){OB=c;loadBurn();draw()}
function burnHtml(){const d=BD[OB+'|'+bsec],M=d?d.members:[],pct=M.length?Math.round(100*d.act/M.length):0;
 const head=`<div class=mh><div><h3 style=margin:0>${flame(4)} Top Burn <span class=up>${d?d.act:0}/${M.length}</span> <span class=m style="font-weight:400">(${pct}%)</span></h3><span class=m>${rkIcon((S.clans.find(x=>x.name==OB)||{}).rank)} ${esc(OB)}</span></div><div style="display:flex;gap:6px;align-items:center"><span class=m>Período:</span><select onchange="bsec=+this.value;this.blur();loadBurn();draw()">${BPER.map(([v,t])=>`<option value="${v}" ${v==bsec?'selected':''}>${t}</option>`).join('')}</select><button onclick="OB=null;draw()" title="Volver">✕</button></div></div>`;
 if(!d)return `<div>${head}<p class=m>Cargando miembros…</p></div>`;
 const now=NOW(),live=M.filter(x=>x.t&&now-x.t<300).length;
 return `<div>${head}<div class=sts>${[['Total','+'+L(d.total),'up'],['Inactivos',L(d.idle),'dn'],['Rep/Jugador','+'+L(Math.round(d.total/Math.max(1,d.act))),''],['Rep/Min',d.rpm.toFixed(1),'']].map(([a,b,c])=>`<div><span class=m style="font-size:12px">${a}</span><b class="${c}">${b}</b></div>`).join('')}</div>
 <div class=m style="font-size:12px;margin-bottom:6px">${live?`<span class="act al lv" style="animation-delay:${PH()}">${IC.sw} ${live} atacando ahora</span>`:'😴 Nadie atacando en los últimos 5 min'} · en vivo, se actualiza cada 2-3 s</div>
 <div class=wrap><table class=bt><tr><th>#<th>Nombre<th class=num>R/M<th class=num>Rep</tr>${M.map((x,i)=>{const f=BF[OB+'|'+x.name],fr=f&&Date.now()-f[0]<4000;return `<tr class="${fr?'flashg':''} ${isMine(OB)&&x.name==MY().player?'mine':''}"><td>${i+1} ${rkIcon(i+1)}<td>${dotHtml(x)} <b class=nm onclick="openMember(OB,this.dataset.w)" data-w="${esc(x.name)}">${esc(x.name)}</b> ${actBadge(x)} ${fr?`<b class=up>+${L(f[1])}</b>`:''}<td class="num ${x.g?'':'m'}">${x.g?x.rpm.toFixed(1):'–'}<td class="num ${x.g?'up':'m'}">${x.g?'+'+L(x.g):'0'}</tr>`}).join('')||'<tr><td colspan=4 class=m>Sin miembros cargados (mira 🔧 Conexión).</tr>'}</table></div></div>`}
function rosterHtml(){const c=S.clans.find(x=>x.name==OR)||{},mv=c.mv||{},dl=(S.debt||{})[OR]||[],ev=[...(mv.o||[]).map(x=>({t:'leave',who:x[0],rep:x[1],ts:x[2]})),...(mv.i||[]).map(x=>({t:'join',who:x[0],rep:x[1],ts:x[2],frm:x[3]}))].sort((a,b)=>b.ts-a.ts);
 return `<div><div class=mh><h3 style=margin:0><span class=dn>${IC.um}</span> Cambios de Roster · ${esc(OR)}</h3><button onclick="OR=null;draw()">← Volver</button></div><p class=m>Entradas y salidas de las últimas 24 h (se detectan mientras el tracker está abierto).</p>`+(ev.map(e=>`<div class=card>${e.t=='leave'?`<span class=dn>${IC.um}</span> <b>${esc(e.who)}</b> salió <span class=dn>−${L(e.rep)} REP</span>`:`<span class=up>${IC.ua}</span> <b>${esc(e.who)}</b> entró${e.frm?' <span class=m>(venía de '+esc(e.frm)+')</span>':''} <span class=up>+${L(e.rep)} REP</span>`} <span class=m style="float:right">hace ${ago(e.ts)}</span></div>`).join('')||'<p class=m>Sin cambios en las últimas 24 h.</p>')+(dl.some(x=>x.left>0)?'<h4>🚪 REP pendiente por recuperar</h4>'+dl.filter(x=>x.left>0).map(x=>`<div class=m>${esc(x.who)}: faltan <b class=o>${L(x.left)}</b> de ${L(x.rep)}</div>`).join(''):'')+'</div>'}
function loadHeat(n){fetch('/heat?clan='+encodeURIComponent(n)).then(r=>r.json()).then(j=>{HT[n]=j;if(OC==n)draw()}).catch(()=>{})}
setInterval(()=>{if(OC)loadHeat(OC)},30000);
function heatHtml(n){const H=HT[n];if(!H)return'<p class=m>Cargando…</p>';const mx=Math.max(1,...H.flat()),D=['Lun','Mar','Mié','Jue','Vie','Sáb','Dom'];
 return '<div class=wrap><div class=hm><div></div>'+Array.from({length:24},(_,h)=>`<div class="hh m">${h%3?'':h}</div>`).join('')+H.map((r,d)=>`<div class="hd m">${D[d]}</div>`+r.map((v,h)=>`<div class=hx title="${D[d]} ${h}:00 · +${L(v)}" style="${v?`background:var(--g);opacity:${(.25+.75*v/mx).toFixed(2)}`:''}"></div>`).join('')).join('')+'</div></div><p class=m style="font-size:11px">REP ganada por el clan según día y hora (tu hora local). Más verde = más ataques.</p>'}
function warTab(){const now=NOW(),H=S.events.filter(e=>e.t=='hit'&&now-e.ts<3600).slice(-80).reverse();
 return `<div id=wrg>${warHtml()}</div><h3>📜 Quién ataca a quién <span class=m style="font-weight:400;font-size:12px">(última hora · el % es la seguridad de que sea ese clan)</span></h3>`+(H.map(e=>{const s=e.sus[0];return `<div class=card>${s?`⚔️ <b class="up nm" data-o="${esc(s[0])}">${esc(s[0])}</b> ataca a <b class="dn nm" data-o="${esc(e.to)}">${esc(e.to)}</b> · <span class=dn>−${L(e.amt)}</span> <span class=up>+${L(s[1])}</span> <span class=m>${s[2]}%${e.sus.length>1?' · otros: '+e.sus.slice(1).map(x=>esc(x[0])+' '+x[2]+'%').join(', '):''}</span>`:`🩸 <b class="dn nm" data-o="${esc(e.to)}">${esc(e.to)}</b> sangró <span class=dn>−${L(e.amt)}</span> <span class=m>· atacante no identificado</span>`} <span class=m style="float:right">hace ${ago(e.ts)}</span></div>`}).join('')||'<p class=m>Aún no hay ataques detectados.</p>')+`<div class=card style="margin-top:12px">${mapHtml()}</div><h3>🕸️ Rivalidades</h3>${rivHtml()}`}
function vGet(n){let v=VT[n];if(!v||Date.now()-v.t>15000)v=VT[n]={amt:0,n:0,att:{},st:'wait',t:Date.now(),said:0};return v}
function vShow(n,mine){const v=VT[n],c=S.clans.find(x=>x.name==n)||{},at=Object.entries(v.att).sort((a,b)=>b[1]-a[1]).slice(0,2),N=`<b class=nm data-o="${esc(n)}">${esc(n)}</b>`;
 const l2=v.st=='ok'?at.map(([a,cf])=>`⚔️ <b class="up nm" data-o="${esc(a)}">${esc(a)}</b> ataca a ${N} <span class=m>${cf}%</span>`).join('<br>'):v.st=='none'?'<span class=m>❓ atacante no identificado</span>':'<span class=m>🔎 buscando al atacante…</span>';
 toast(`<div class=tb1>🩸 <b class=nm data-o="${esc(n)}">${esc(n.toUpperCase())}</b> <span class=dn>SANGRANDO −${L(v.amt)}</span>${v.n>1?` <span class=tn>×${v.n}</span>`:''}</div>${c.rep?`<div class="tl m">REP ${L(c.rep)} · #${c.rank}</div>`:''}<div style="margin-top:4px">${l2}</div>`,'#ff4d4d','v|'+n,1,mine)}
function vBleed(n,amt,mine){const v=vGet(n);v.amt+=amt;v.n++;v.t=Date.now();vShow(n,mine);snd&&beep(mine?440:560)}
function vHit(e,mine){const v=vGet(e.to);if(!v.n){v.amt+=e.amt;v.n=1}v.t=Date.now();const s=e.sus.filter((x,i)=>x[2]>=25||i==0);
 if(s.length){s.forEach(x=>{v.att[x[0]]=Math.max(v.att[x[0]]||0,x[2])});v.st='ok'}else if(!e.pending&&v.st!='ok')v.st='none';vShow(e.to,mine);
 const top=Object.entries(v.att).sort((a,b)=>b[1]-a[1])[0];if(Date.now()-v.said>8000&&(top||!e.pending)){v.said=Date.now();say(top?top[0]+' ataca a '+e.to:e.to+' está sangrando')}snd&&top&&beep(mine?330:700)}
function onEv(e,ctx){ctx=ctx||{att:new Set(),hitAtt:new Set()};const sus=e.sus||[],mine=isMine(e.clan)||isMine(e.to)||isMine(e.over)||sus.some(x=>isMine(x[0])),rel=mine||[e.clan,e.to,e.over].some(x=>x&&fav.has(x))||sus.some(x=>fav.has(x[0])),want=NS.scope=='all'||(NS.scope=='mine'&&rel);
 if(document.hidden&&(e.t=='gain'&&atkOK(e.clan)||(e.t=='hit'&&isMine(e.to))||(e.t=='pve'&&pveOK(e))))UNR++;
 if(e.t=='bleed'&&!e.lv){if(isMine(e.clan))alarm(e);if(want&&e.amt>=NS.min&&(rel||!ctx.att.has(e.clan)))vBleed(e.clan,e.amt,isMine(e.clan))}
 if(e.t=='hit'){const s=sus[0];if(isMine(e.to))alarm(e);
  sus.forEach((x,i)=>{if(i==0||x[2]>=30){const a=AT[x[0]];if(a&&NOW()-a.last<120){a.tg[e.to]=(a.tg[e.to]||0)+Math.round(e.amt*x[2]/100);atkShow(x[0])}}});
  const vt=VT[e.to]&&Date.now()-VT[e.to].t<15000&&VT[e.to].n;if(want&&e.amt>=NS.min&&(vt||rel||!s||!atkOK(s[0])))vHit(e,isMine(e.to))}
 if(e.t=='gain')atkEv(e);
 if(e.t=='mem'){const a=AT[e.clan];if(a&&NOW()-a.last<120){a.pl[e.who]=(a.pl[e.who]||0)+e.amt;atkShow(e.clan)}
  if(NS.scope!='off'&&favm.has(e.clan+'|'+e.who)){const q='m|'+e.clan+'|'+e.who,v=MT[q]&&Date.now()-MT[q].t<20000?MT[q]:(MT[q]={amt:0});v.amt+=e.amt;v.t=Date.now();toast(`${flame(3)} <b>${esc(e.who)}</b> <span class=m>(${esc(e.clan)})</span> está atacando <span class=up>+${L(v.amt)}</span>`,'#ff9f43',q,1)}}
 if(e.t=='pass'&&want){toast(`<span class=up>${IC.tu}</span> <b class=nm data-o="${esc(e.clan)}">${esc(e.clan)}</b> superó a <b class=nm data-o="${esc(e.over)}">${esc(e.over)}</b> <span class=dn>${IC.td}</span> · ahora <b>#${e.r}</b>`,'#3ddc84','p|'+e.clan+'|'+e.over,1,mine);say(e.clan+' superó a '+e.over);snd&&beep(mine?900:760)}
 if(e.t=='rank'&&isMine(e.clan)&&NS.scope!='off'){const up=e.b<e.a,t=(up?'📈 ¡Tu clan subió':'📉 Tu clan bajó')+' al puesto #'+e.b;toast(t,up?'#3ddc84':'#ff4d4d','r|mine',1,1);snd&&beep(up?880:330);say(t.replace(/[^\wÁÉÍÓÚáéíóúñ# ]/g,''))}
 if(e.t=='leave'&&(want||mine)){toast(`<span class=dn>${IC.um}</span> <b>${esc(e.who)}</b> salió de <b class=nm data-o="${esc(e.clan)}">${esc(e.clan)}</b> <span class=dn>−${L(e.rep)} REP</span>`,'#ff9f43','l|'+e.clan+'|'+e.who,1,mine);if(rel)say(e.who+' salió de '+e.clan)}
 if(e.t=='join'&&rel&&NS.scope!='off')toast(`<span class=up>${IC.ua}</span> <b>${esc(e.who)}</b> se unió a <b class=nm data-o="${esc(e.clan)}">${esc(e.clan)}</b>${e.frm?' <span class=m>(venía de '+esc(e.frm)+')</span>':''}`,'#3ddc84','j|'+e.clan+'|'+e.who);
 if(e.t=='pve'||e.t=='pvepass'||e.t=='pvein')pveEv(e);
 if(e.t=='pvp')pvpEv(e)}
function setNS(q,v){NS[q]=v;try{localStorage.ns=JSON.stringify(NS)}catch(e){}syncNS();unfocus();if(q=='voice'&&v){lastSay=0;say('Voz activada')}if(q=='nt'&&!v){$('#ntx').innerHTML='';NT.x=0}draw()}
function syncNS(){const s=(q,f)=>{const e=$(q);if(e)f(e)};s('#sar',e=>e.value=NS.arw);s('#rdb',e=>e.classList.toggle('on',!!NS.radar));s('#nsb',e=>e.classList.toggle('on',NS.atk!='off'))}
function toggleDesk(){if(NS.desk)return setNS('desk',false);if(!window.Notification)return toast('Tu navegador no permite avisos de escritorio','#ff4d4d');Notification.requestPermission().then(p=>{if(p=='granted'){setNS('desk',true);toast('💻 Recibirás avisos aunque la pestaña esté en segundo plano','#3ddc84')}else toast('Permiso de notificaciones denegado','#ff4d4d')})}
let dT=0;function draw(){if(!dT)dT=setTimeout(draw1,40)}
function draw1(){dT=0;const a=document.activeElement;if(a&&a.tagName=='SELECT'){dT=setTimeout(draw1,300);return}
 const m=$('#md'),sc=m.firstChild?m.firstChild.scrollTop:0,vw=NSET?'n':XEDIT?'x':SHEDIT?'s':KYR?'k':omem?'m':OB?'b':OR?'r':OC?'c':'',same=vw&&vw==m.dataset.v;draw0();if(tab=='cfg'&&$('#diag'))$('#diag').innerHTML=diagHtml();
 document.body.classList.toggle('mo',!!vw);if((vw=='n'||vw=='x'||vw=='s'||vw=='k')&&same)return;   // el panel de avisos no se redibuja mientras escribes
 const H=vw=='n'?setHtml():vw=='x'?rwEdHtml():vw=='s'?shEdHtml():vw=='k'?kyRvHtml():vw=='m'?memberHtml():vw=='b'?burnHtml():vw=='r'?rosterHtml():vw=='c'?modalHtml():'';m.dataset.v=vw;
 if(vw){m.innerHTML=H;m.style.display='flex';m.firstChild.style.width=vw=='c'?'min(1180px,97vw)':vw=='x'||vw=='s'||vw=='k'?'min(900px,96vw)':'min(620px,94vw)';if(same)m.firstChild.scrollTop=sc}else{m.style.display='none';m.innerHTML=''}}
let omem=null,force=false,mineNeed=false;const MS={};
const MY=()=>{try{return JSON.parse(localStorage.mine||'{}')}catch(e){return{}}},isMine=x=>!!x&&MY().clan==x,same=(a,b)=>a==b||(a.length>8&&b.length>8&&(a.startsWith(b)||b.startsWith(a)));
const fire=c=>{const mx=Math.max(1,...S.clans.map(x=>x.d60||0)),r=(c.d60||0)/mx,k=r>=.8?5:r>=.6?4:r>=.4?3:r>=.2?2:r>=.12?1:0;return flame(Math.max(k,c.g10>0&&NOW()-(c.last||0)<60?1:0))};
const champ=nm=>{let c=0,t=0;(S.seasons||[]).forEach(s=>{const r=s.rows.find(y=>same(y.clan,nm));if(r&&r.rank==1)c++;else if(r&&r.rank==2)t++});return(c?'🏆×'+c:'')+(t?' 👑×'+t:'')};
const hstrip=nm=>{const a=(S.hours||{})[nm];if(!a)return'';const mx=Math.max(1,...a);return '<div style="display:flex;gap:2px;align-items:flex-end;height:34px;margin:6px 0">'+a.map((v,i)=>`<div title="${i}:00 · ${L(v)}" style="flex:1;background:#ff9f43;opacity:${0.15+0.85*v/mx};height:${4+30*v/mx}px"></div>`).join('')+'</div><div class=m style="font-size:10px">Actividad de ataque por hora (0–23, tu hora local)</div>'};
function sbHtml(){const s=S.season||{};if(!s.end)return'📅 Temporada: calculando… <span class=m>(puedes fijar la fecha final en 🔧 Conexión)</span>';
 const r=Math.max(0,s.end-NOW()),d=Math.floor(r/86400),h=Math.floor(r%86400/3600),m=Math.floor(r%3600/60),x=Math.floor(r%60),p=Math.min(100,Math.max(0,100*(1-r/Math.max(1,s.end-s.start)))),z=v=>String(v).padStart(2,'0');
 return `<b>📅 ${esc(s.name)}</b> · Día <b>${s.day}</b> de ~${s.total} · faltan <b class=dn>${d}d ${z(h)}:${z(m)}:${z(x)}</b> para la final ${s.src=='estimado'?'<span class=m>(≈ estimado)</span>':'<span class=up>✓</span>'}${d<7&&r>0?' <b class=dn>🚨 ¡última semana!</b>':''}<div style="height:6px;background:var(--l);border-radius:4px;margin-top:6px"><div style="height:6px;width:${p}%;background:linear-gradient(90deg,#3ddc84,#ff9f43,#ff4d4d);border-radius:4px"></div></div>`}
setInterval(()=>{try{$('#sb').innerHTML=sbHtml()+wbHtml();const se=(XT.rew||{});const end=(se.pve&&se.pve.end)||0;const el=$('#cd');if(el){if(end&&end>NOW()/1000){const d=Math.max(0,end-NOW()/1000);el.textContent='⏳ PvE S4: '+Math.floor(d/86400)+'d '+String(Math.floor(d%86400/3600)).padStart(2,'0')+':'+String(Math.floor(d%3600/60)).padStart(2,'0')+':'+String(Math.floor(d%60)).padStart(2,'0')}else el.textContent=''}}catch(e){}},1000);
function setMine(o){localStorage.mine=JSON.stringify(o);fetch('/cfg',{method:'POST',body:JSON.stringify({mine:o.clan||''})});mineNeed=true;force=true;if(o.clan)loadMem(o.clan);draw()}
function pickId(v){v=v.trim();const z=x=>String(x).replace(/^0+/,''),c=v&&(S.clans.find(c=>c.id&&z(c.id)==z(v))||S.clans.find(c=>c.name.toLowerCase()==v.toLowerCase()));if(c)setMine({clan:c.name});else toast('No encontré ese ID: elige tu clan en la lista','#ff4d4d')}
function mineHtml(){const m=MY(),C=S.clans,R=(MEM[m.clan]||{}).members||[];
 return `<div id=mcfg class=card><b>1️⃣ Tu clan</b><br><input id=mid placeholder="ID del clan (ej: 00082)" style="width:210px" onchange="pickId(this.value)"> <span class=m>o elígelo:</span> <select onchange="this.blur();setMine({clan:this.value})"><option value="">— elegir —</option>${C.map(x=>`<option ${x.name==m.clan?'selected':''}>${esc(x.name)}</option>`).join('')}</select>${m.clan?`<br><br><b>2️⃣ Tu nombre en el clan</b> <span class=m>(${R.length} miembros)</span><br><select id=mp onchange="this.blur();setMine(Object.assign(MY(),{player:this.value}))"><option value="">— elegir —</option>${R.map(x=>`<option ${x.name==m.player?'selected':''}>${esc(x.name)}</option>`).join('')}</select> ${R.length?'':'<span class=m>cargando miembros… (si no aparecen, mira 🔧 Conexión)</span>'}`:''}</div><div id=mdyn>${mineDyn()}</div>`}
function mineDyn(){const m=MY(),C=S.clans,c=C.find(x=>x.name==m.clan);if(!c)return'<p class=m>Elige tu clan y aquí verás tu panel de defensa: quién te ataca, cuánto te falta para subir y quién te persigue.</p>';
 const i=C.indexOf(c),u=C[i-1],d=C[i+1],R=(MEM[c.name]||{}).members||[],me=R.find(x=>x.name==m.player),now=NOW(),th=[...S.events].reverse().find(e=>e.t=='hit'&&e.to==c.name&&now-e.ts<600),
 st=c.l10>0?`<span class=b>🩸 sangrando −${L(c.l10)}</span>`:c.g10>0?`<span class=g>⚔️ atacando +${L(c.g10)}</span>`:'<span class=g>🟢 estable</span>';
 return `<div class="card mine"><h3 style=margin:0>🛡️ ${esc(c.name)} <span class=m>#${c.rank}</span> ${fire(c)} ${champ(c.name)} ${st} ${debtBadge(c.name)}</h3><p>Reputación <b>${L(c.rep)}</b> · ${n(c.d5)} (5 min) · ${n(c.d60)} (1 h) · ${c.rpm}/min</p><p>${u?`⬆️ Para subir al #${u.rank} (<b>${esc(u.name)}</b>) faltan <b>${L(u.rep-c.rep)}</b>`:'👑 Vas de líder'}<br>${d?`⬇️ <b>${esc(d.name)}</b> te persigue a <b>${L(c.rep-d.rep)}</b> puntos`:''}${etas().filter(x=>x.a==c.name||x.b==c.name).map(x=>`<br>🎯 <b>${esc(x.a)}</b> alcanzará a <b>${esc(x.b)}</b> en ~${Math.round(x.m)} min`).join('')}</p>${th?`<p class=dn>🚨 Te atacan: ${th.sus.map(x=>'<b>'+esc(x[0])+'</b> '+x[2]+'%').join(', ')||'atacante no identificado'}</p>`:''}${cwMine(c)}</div><div class=card><b>🎯 Presas recomendadas</b> <span class=m>(clanes débiles o dormidos cerca de ti)</span>${preyHtml(c)}</div><div class=card><b>📜 Registro de mi clan</b>${myLog(c)}</div>${me?`<div class=card><h3 style=margin:0>👤 ${esc(me.name)} <span class=m>LV ${me.lv} · #${R.indexOf(me)+1} de ${R.length}</span> ${mfire(me,R,c.name)}</h3><p>REP <b>${L(me.rep)}</b> · aportas el <b>${Math.round(100*me.rep/Math.max(1,R.reduce((a,x)=>a+x.rep,0)))}%</b> del clan</p><button onclick="openMember(MY().clan,MY().player)">📊 Ver mis estadísticas</button></div>`:''}`}
function loadMS(c,w){return fetch('/member?clan='+encodeURIComponent(c)+'&who='+encodeURIComponent(w)).then(r=>r.json()).then(j=>{MS[c+'|'+w]=j;draw()}).catch(()=>{})}
function openMember(c,w){omem={c,w};loadMS(c,w);if(!(MEM[c]&&MEM[c].members))loadMem(c);draw()}
setInterval(()=>{if(omem)loadMS(omem.c,omem.w)},4000);
function memberHtml(){const c=omem.c,w=omem.w,R=(MEM[c]||{}).members||[],x=R.find(y=>y.name==w)||{},D=MS[c+'|'+w]||{},now=NOW(),lg=D.log||[],sm=t=>lg.filter(a=>now-a[0]<=t).reduce((a,b)=>a+b[1],0),H=D.h||Array(24).fill(0),hm=Math.max(1,...H),pk=H.indexOf(Math.max(...H)),B=Array(24).fill(0);
 lg.forEach(a=>{const k=Math.floor((now-a[0])/900);if(k<24)B[23-k]+=a[1]});const bm=Math.max(1,...B),tot=lg.reduce((a,b)=>a+b[1],0),fl=mfire(x,R,c),
 bars=(A,mx,f)=>'<div style="display:flex;gap:3px;align-items:flex-end;height:70px">'+A.map((v,i)=>`<div title="${f(i)} · +${L(v)}" style="flex:1;background:#3ddc84;opacity:${v?0.35+0.65*v/mx:0.12};height:${3+64*v/mx}px"></div>`).join('')+'</div>';
 return `<div><div style="display:flex;justify-content:space-between"><h3 style=margin:0>👤 ${esc(w)} ${fl}</h3><button onclick="omem=null;draw()">← Volver</button></div><p class=m>${esc(c)} · #${R.indexOf(x)+1||'?'} de ${R.length}</p><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">${[['Nivel',x.lv||'–'],['Reputación',L(x.rep||0)],['Hora pico',tot?pk+':00':'–'],['+30 min',n(sm(1800))],['+6 h',n(sm(21600))],['+24 h',n(sm(86400))]].map(([a,b])=>`<div class=card style="margin:0;text-align:center"><span class=m>${a}</span><br><b>${b}</b></div>`).join('')}</div><h4>Últimas 6 horas (barras de 15 min)</h4>${bars(B,bm,i=>(23-i)*15+' min atrás')}<h4>Actividad por hora del día</h4>${bars(H,hm,i=>i+':00')}<h4>Ataques registrados <span class=m>(${lg.length} · total +${L(tot)})</span></h4><div class=wrap><table><tr><th>Cuándo<th>REP</tr>${[...lg].reverse().slice(0,25).map(a=>`<tr><td>${new Date(a[0]*1000).toLocaleString()}<td class=up>+${L(a[1])}</tr>`).join('')||'<tr><td colspan=2 class=m>Aún sin ataques registrados de este jugador (se detectan en vivo mientras el tracker está abierto).</tr>'}</table></div></div>`}
function histHtml(){const SS=[...(S.seasons||[])].sort((a,b)=>b.n-a.n);if(!SS.length)return'<p class=m>Aún no hay temporadas anteriores en la página.</p>';const cl={};
 SS.forEach(s=>s.rows.forEach(r=>{const x=cl[r.clan]||(cl[r.clan]={c:0,a:0,b:0,t:0,st:0});r.rank==1?x.c++:r.rank==2?x.a++:r.rank==3?x.b++:x.t++}));
 Object.entries(cl).forEach(([k,x])=>{for(const s of SS){const r=s.rows.find(y=>y.clan==k);if(r&&r.rank==1)x.st++;else break}});
 return SS.map(s=>`<div class=card><b>Temporada ${s.n}</b> <span class=m>· terminó ${new Date(s.ended*1000).toLocaleDateString()}</span><br>${s.rows.slice(0,3).map((r,i)=>['🏆','👑','🥉'][i]+' <b>'+esc(r.clan)+'</b> <span class=m>'+L(r.rep)+'</span>').join(' &nbsp; ')}</div>`).join('')+'<h3>Palmarés por clan</h3><div class=wrap><table><tr><th>Clan<th>🏆 Champ<th>👑 Top 2<th>🥉 Top 3<th>Top 4–10<th>Racha de champ</tr>'+Object.entries(cl).sort((a,b)=>b[1].c-a[1].c||b[1].a-a[1].a||b[1].b-a[1].b||b[1].t-a[1].t).map(([k,x])=>`<tr><td><b class=nm data-o="${esc(k)}">${esc(k)}</b><td>${x.c||''}<td>${x.a||''}<td>${x.b||''}<td>${x.t||''}<td>${x.st>1?'🔥 '+x.st+' seguidas':x.st?'1':''}</tr>`).join('')+'</table></div>'}
let onl=0;
const tip=c=>`Master: ${c.master}\nMiembros: ${c.members} (${(c.act||[])[3]||0} activos 1 h)\nTop: ${c.top?c.top[0]+' ('+L(c.top[1])+' rep)':'—'}`;
const wcell=v=>v?`<span class=${v>=1000?'o':'up'}>+${L(v)}</span>`:'<span class=m>0</span>';
const wnow=c=>c.w&&c.w[2]?`<b class=o>+${L(c.w[2])}</b> <span class=m style="font-size:10px">${c.wr} r/m</span>`:'<span class=m>0</span>';
const wnow2=x=>x.w&&x.w[2]?`<b class=o>+${L(x.w[2])}</b>`:'<span class=m>0</span>';
const actPill=c=>{const a=c.act||[0,0,0,0];return `<span class="b2 ${a[0]?'on':''}">${a[0]}</span><sup class=m>${a[3]||''}</sup>`};
const dot=x=>{const a=NOW()-(x.t||0);return !x.t||a>=900?'':dotHtml(x)+' <span class=m style="font-size:11px">'+ago(x.t)+'</span>'};
const mfire=(x,R,cn)=>{const o=(S.online||[]).find(z=>z[0]==cn&&z[1]==x.name),g=Math.max(o?o[3]:0,(x.hg||[])[0]||0,(x.w||[])[2]||0),k=g>=3000?5:g>=1500?4:g>=800?3:g>=300?2:g>=50?1:0;return flame(k)};
function wbHtml(){const t=NOW(),a=Math.floor(t/1800)*1800,r=a+1800-t,f=x=>new Date(x*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}),z=v=>String(v).padStart(2,'0');
 return `<div style="margin-top:8px">⏱ <b>Ventana ${f(a)} → ${f(a+1800)}</b> · quedan <b class=dn>${z(Math.floor(r/60))}:${z(Math.floor(r%60))}</b> <span class=m>(bloque de 30 min de ataque)</span><div style="height:4px;background:var(--l);border-radius:3px;margin-top:4px"><div style="height:4px;width:${100*(1-r/1800)}%;background:#ff9f43;border-radius:3px"></div></div></div>`}
function etas(){const C=S.clans,o=[];for(let i=1;i<Math.min(C.length,16);i++){const a=C[i],b=C[i-1],dv=(a.rpm||0)-(b.rpm||0),gap=b.rep-a.rep;if(dv>0&&gap>0){const m=gap/dv;if(m<=240)o.push({a:a.name,b:b.name,m,gap})}}return o.sort((x,y)=>x.m-y.m)}
const etaT=()=>etas().map(x=>`<div class=card>🎯 <b class=up>${esc(x.a)}</b> alcanzará a <b class=dn>${esc(x.b)}</b> en ~<b>${x.m<60?Math.round(x.m)+' min':(x.m/60).toFixed(1)+' h'}</b> <span class=m>(brecha ${L(x.gap)})</span></div>`).join('')||'<p class=m>Ningún adelantamiento previsto en las próximas 4 h con el ritmo actual.</p>';
function onlineHtml(){const now=NOW(),O=(S.online||[]).map(o=>({c:o[0],w:o[1],t:o[2],g:o[3],a:now-o[2]})),cnt=t=>O.filter(o=>o.a<=t).length,sel=onl||[60,300,900,3600,86400].find(t=>cnt(t)>0)||900,by={},rk={};S.clans.forEach(c=>rk[c.name]=c.rank);O.filter(o=>o.a<=sel).forEach(o=>(by[o.c]=by[o.c]||[]).push(o));
 const box=(t,l)=>`<div class="card ${sel==t?'mine':''}" style="flex:1;cursor:pointer;text-align:center;margin:0" onclick="onl=${t};draw()"><b class=up style="font-size:22px">${cnt(t)}</b><br><span class=m>${l}</span></div>`;
 return `<div style="display:flex;gap:8px;margin-bottom:10px">${box(60,'60 s')}${box(300,'5 min')}${box(900,'15 min')}${box(3600,'1 h')}${box(86400,'24 h')}</div><p class=m>"Activo" = ganó REP (atacó) en ese intervalo. Toca un jugador para ver sus estadísticas.</p>`+(Object.entries(by).sort((a,b)=>(rk[a[0]]||99)-(rk[b[0]]||99)).map(([c,R2])=>`<div class=card><b class=nm data-o="${esc(c)}">#${rk[c]||'?'} ${esc(c)}</b> ${fire(S.clans.find(x=>x.name==c)||{})} <span class=up style="float:right">${R2.length} en línea</span><br>${R2.sort((a,b)=>a.a-b.a).map(o=>`<span class=sus style="cursor:pointer" onclick="openMember(this.dataset.c,this.dataset.w)" data-c="${esc(o.c)}" data-w="${esc(o.w)}">${esc(o.w)} <span class=${o.a<60?'up':o.a<300?'o':'m'}>● ${ago(o.t)}</span>${o.g?' <span class=o>+'+L(o.g)+'</span>':''}</span>`).join('')}</div>`).join('')||'<div class=card>😴 Nadie activo en este intervalo (o aún no hay conexión con los miembros: mira 🔧 Conexión).</div>')}
function winHtml(){const C=[...S.clans].sort((a,b)=>(b.w?b.w[2]:0)-(a.w?a.w[2]:0)),f=x=>new Date(x*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
 return '<h3>🏅 Mejores jugadores de la ventana</h3>'+mvp()+'<h3>⏱ Clanes en la ventana actual</h3><div class=wrap><table><tr><th>#<th>Clan<th>Atk -2<th>Atk -1<th>⚔ Ahora<th>Ritmo</tr>'+C.filter(c=>c.w&&(c.w[0]||c.w[1]||c.w[2])).map((c,i)=>`<tr><td>${i+1}<td><b class=nm data-o="${esc(c.name)}">${esc(c.name)}</b> ${fire(c)}<td>${wcell(c.w[0])}<td>${wcell(c.w[1])}<td>${wnow(c)}<td class=m>${c.wr} r/m</tr>`).join('')+'</table></div><h3>📊 Resúmenes de ventanas cerradas</h3>'+([...(S.summ||[])].reverse().map(r=>`<div class=card><b>${f(r.start)} → ${f(r.end)}</b> · total <span class=up>+${L(r.total)}</span><br>${r.clans.slice(0,5).map((x,i)=>(i+1)+'. <b>'+esc(x[0])+'</b> <span class=up>+'+L(x[1])+'</span>').join(' &nbsp; ')}<br><span class=m>👤 ${r.members.slice(0,6).map(m=>esc(m[0])+' (+'+L(m[2])+')').join(' · ')||'sin datos de jugadores'}</span></div>`).join('')||'<p class=m>Aún no se cierra ninguna ventana. Al terminar cada bloque de 30 min aparecerá aquí (y en Discord).</p>')}
const setCfg=(k,v)=>fetch('/cfg',{method:'POST',body:JSON.stringify({alerts:{[k]:+v}})});
setTimeout(()=>{const o={mine:MY().clan||'',mon:[...fav]};if(localStorage.poll)o.poll=+localStorage.poll;fetch('/cfg',{method:'POST',body:JSON.stringify(o)})},1500);
let GL={},glf={q:'',cl:'',act:false,sort:'rep'};
function loadGL(){return fetch('/all').then(r=>r.json()).then(j=>{GL=j||{};if(tab=='jug')draw()}).catch(()=>{})}
loadGL();setInterval(loadGL,15000);
const allP=()=>{const A=[];Object.entries(GL).forEach(([cl,v])=>(v.members||[]).forEach(x=>A.push({...x,cl})));return A};
const csv=(f,rows)=>{const b=new Blob(['\ufeff'+rows.map(r=>r.map(v=>'"'+String(v).replace(/"/g,'""')+'"').join(',')).join('\n')],{type:'text/csv'}),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=f;a.click()};
const exportGL=()=>csv('jugadores.csv',[['Jugador','Clan','Nivel','REP','Atk -2','Atk -1','Ahora']].concat(allP().sort((a,b)=>b.rep-a.rep).map(x=>[x.name,x.cl,x.lv,x.rep,...(x.w||[0,0,0])])));
const diagHtml=()=>{const d=S.diag||{};return `<p class=m>Activo hace ${Math.round((d.up||0)/60)} min · lectura ${d.lat||0} s · ${d.polls||0} lecturas · miembros leídos: ${d.ncl||0}/${d.ncl_tot||0} clanes, ${d.nmem||0} jugadores · ${d.ev||0} eventos · conexión: ${esc(d.ep||'sin conectar')}</p>`+((S.log||[]).map(l=>`<div class=m>⚠️ ${new Date(l[0]*1000).toLocaleTimeString()} ${esc(l[1])}</div>`).join('')||'<div class=up>✓ Sin errores recientes</div>')};
function globalHtml(){return `<div id=gcfg class=card style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><input id=gq placeholder="🔎 Buscar jugador…" value="${esc(glf.q)}" oninput="glf.q=this.value;draw()"><select id=gc onchange="this.blur();glf.cl=this.value;draw()"><option value="">Todos los clanes</option>${S.clans.map(c=>`<option ${c.name==glf.cl?'selected':''}>${esc(c.name)}</option>`).join('')}</select><select onchange="this.blur();glf.sort=this.value;draw()"><option value=rep ${glf.sort=='rep'?'selected':''}>Ordenar: REP</option><option value=lv ${glf.sort=='lv'?'selected':''}>Ordenar: Nivel</option><option value=w ${glf.sort=='w'?'selected':''}>Ordenar: ⚔ Ventana</option></select><label><input type=checkbox ${glf.act?'checked':''} onchange="glf.act=this.checked;draw()"> solo activos (15 min)</label><button onclick="exportGL()">⬇️ CSV</button></div><div id=gdyn>${globalDyn()}</div>`}
function globalDyn(){const now=NOW();let A=allP();const tot=A.length,nc=Object.keys(GL).length;
 if(glf.cl)A=A.filter(x=>x.cl==glf.cl);if(glf.q)A=A.filter(x=>x.name.toLowerCase().includes(glf.q.toLowerCase()));if(glf.act)A=A.filter(x=>x.t&&now-x.t<=900);
 A.sort((a,b)=>glf.sort=='lv'?b.lv-a.lv||b.rep-a.rep:glf.sort=='w'?((b.w||[])[2]||0)-((a.w||[])[2]||0)||b.rep-a.rep:b.rep-a.rep);
 const st=tot?`${tot} jugadores · ${nc}/${S.clans.length} clanes leídos · nivel prom. ${Math.round(A.reduce((s,x)=>s+x.lv,0)/Math.max(1,A.length))} · ${A.filter(x=>x.lv>=100).length} de nivel 100 · mostrando ${Math.min(200,A.length)} de ${A.length}`:`<b class=dn>Aún no hay jugadores cargados.</b> ${esc(S.mem_msg||'')} <span class=m>El tracker lee los miembros de todos los clanes al conectarse (tarda unos 40 s). Si sigue vacío, mira 🩺 Diagnóstico en 🔧 Conexión.</span>`;
 return `<p class=m>${st}</p><div class=wrap><table><tr><th>#<th>Jugador<th>Clan<th>LV<th>REP<th>Atk -1<th>⚔ Ahora<th>Activo</tr>`+A.slice(0,200).map((x,i)=>`<tr class="${isMine(x.cl)&&x.name==MY().player?'mine':''}"><td>${i+1}<td><b class=nm onclick="openMember(this.dataset.c,this.dataset.w)" data-c="${esc(x.cl)}" data-w="${esc(x.name)}">${esc(x.name)}</b> ${mfire(x,(GL[x.cl]||{}).members||[],x.cl)}<td class=nm data-o="${esc(x.cl)}">${esc(x.cl)}<td class=m>${x.lv||'–'}<td>${L(x.rep)}<td>${wcell((x.w||[])[1])}<td>${wnow2(x)}<td>${dot(x)}</tr>`).join('')+'</table></div>'}
const mvp=()=>{const A=allP().filter(x=>(x.w||[])[2]>0).sort((a,b)=>b.w[2]-a.w[2]).slice(0,10),mins=Math.max(.5,(NOW()%1800)/60);return A.length?'<div class=wrap><table><tr><th>#<th>Jugador<th>Clan<th>+REP<th>Ritmo</tr>'+A.map((x,i)=>`<tr><td>${medal(i)}<td><b class=nm onclick="openMember(this.dataset.c,this.dataset.w)" data-c="${esc(x.cl)}" data-w="${esc(x.name)}">${esc(x.name)}</b><td class=m>${esc(x.cl)}<td class=o>+${L(x.w[2])}<td class=m>${(x.w[2]/mins).toFixed(1)} r/m</tr>`).join('')+'</table></div>':'<p class=m>Nadie ha ganado REP en esta ventana todavía.</p>'};
function preyList(me){return S.clans.filter(c=>c.name!=me.name&&c.rep>0).map(c=>{let s=0;const why=[],a=c.act||[0,0,0,0],gap=c.rep-me.rep;if(c.l10>0){s+=3;why.push('sangra −'+L(c.l10))}if(!a[1]){s+=2;why.push('0 activos (5 min)')}else if(a[3]<=1){s+=1;why.push('pocos activos')}if((c.d60||0)<0){s+=1;why.push('cae '+L(-c.d60)+'/h')}if(gap>0&&gap<3000){s+=1.5;why.push('a '+L(gap)+' por encima')}else if(gap<=0&&gap>-3000){s+=.5;why.push('a '+L(-gap)+' por debajo')}return{c,s,why}}).filter(x=>x.s>=2).sort((a,b)=>b.s-a.s).slice(0,5)}
const preyHtml=c=>preyList(c).map(x=>`<div style="margin:4px 0"><b>${esc(x.c.name)}</b> <span class=m>#${x.c.rank}</span> ${x.why.map(w=>`<span class=sus>${w}</span>`).join('')}</div>`).join('')||'<div class=m>Ninguna presa clara ahora mismo.</div>';
const finalT=()=>{const e=(S.season||{}).end;if(!e||!S.clans.length)return'';const mins=Math.max(0,(e-NOW())/60),P=S.clans.slice(0,12).map(c=>({c,v:Math.round(c.rep+((c.d60||0)/60)*mins)})).sort((a,b)=>b.v-a.v);
 return '<h3>🏁 Final de temporada (si mantienen el ritmo de la última hora)</h3><div class=wrap><table><tr><th>Final<th>Clan<th>Hoy<th>REP final estimada<th>Cambio</tr>'+P.map((x,i)=>{const d=x.c.rank-(i+1);return `<tr><td>${medal(i)}<td><b>${esc(x.c.name)}</b><td>#${x.c.rank}<td>${L(x.v)}<td>${d>0?'<span class=up>▲'+d+'</span>':d<0?'<span class=dn>▼'+(-d)+'</span>':'<span class=m>=</span>'}</tr>`}).join('')+'</table></div><p class=m>Estimación lineal, puede cambiar mucho: los clanes suelen acelerar al final.</p>'};
const myLog=c=>{const R=S.events.filter(e=>(e.t=='hit'&&(e.to==c.name||e.sus.some(x=>x[0]==c.name)))||(e.t=='gain'&&e.clan==c.name)||(e.t=='rank'&&e.clan==c.name)).slice(-12).reverse().map(e=>{const w=`<span class=m>hace ${ago(e.ts)}</span>`;
  return e.t=='rank'?`<div>📊 ${e.b<e.a?'<span class=up>Subiste':'<span class=dn>Bajaste'} del #${e.a} al #${e.b}</span> ${w}</div>`:e.t=='gain'?`<div>⚔️ Atacaste <span class=up>+${L(e.amt)}</span> ${w}</div>`:e.to==c.name?`<div>🩸 Perdiste <span class=dn>${L(e.amt)}</span> · ${e.sus.map(x=>esc(x[0])+' '+x[2]+'%').join(', ')||'atacante no identificado'} ${w}</div>`:`<div>🎯 Ayudaste a golpear a <b>${esc(e.to)}</b> ${w}</div>`});return R.join('')||'<div class=m>Sin actividad todavía.</div>'};
let qw=+(localStorage.qw||30),rowsN=+(localStorage.rows||100);const QWL={15:'15 s',30:'30 s',60:'1 min',300:'5 min'},dq=c=>(c.dq||[])[[15,30,60,300].indexOf(qw)];
const unfocus=()=>{const a=document.activeElement;a&&a.blur&&a.blur()};
function setQw(v){unfocus();qw=+v;localStorage.qw=v;draw()}
function setRows(v){unfocus();rowsN=+v;localStorage.rows=v;draw()}
function setPoll(v){unfocus();localStorage.poll=v;fetch('/cfg',{method:'POST',body:JSON.stringify({poll:+v})});toast('⏱ Lectura cada '+(+v<1?'0,5 s (🚀 turbo)':+v==1?'1 s (en vivo)':+v==60?'1 min':v+' s'),'#ff9f43')}
function refreshNow(){fetch('/cfg',{method:'POST',body:JSON.stringify({refresh:1})});loadGL();if(OC)loadMem(OC);const b=$('#rf');b.disabled=true;setTimeout(()=>{b.disabled=false},1500);toast('🔄 Actualizando…','#ff9f43')}
function toggleTheme(){const l=document.body.classList.toggle('light');localStorage.theme=l?'light':'dark';$('#th').textContent=l?'☀️':'🌙'}
$('#sq').value=qw;$('#sn').value=rowsN;$('#sp').value=localStorage.poll||'1';if(localStorage.theme=='light'){document.body.classList.add('light');$('#th').textContent='☀️'}
syncNS();
const mvBadge=c=>{const mv=c.mv;if(!mv)return'';const now=NOW(),o=(mv.o||[]).filter(x=>now-x[2]<10800),i=(mv.i||[]).filter(x=>now-x[2]<10800);let s='';
 if(o.length)s+=`<span class=dn title="Salió: ${o.map(x=>esc(x[0])+' (−'+L(x[1])+')').join(', ')}">${IC.um}${o.length>1?'<sup>'+o.length+'</sup>':''}</span> `;
 if(i.length)s+=`<span class=up title="Se unió: ${i.map(x=>esc(x[0])).join(', ')}">${IC.ua}${i.length>1?'<sup>'+i.length+'</sup>':''}</span>`;return s};
const debtBadge=n=>{const l=((S.debt||{})[n]||[]).reduce((a,d)=>a+d.left,0);return l>0?`<span class=b title="Reputación perdida por salidas: falta por recuperar">🚪 −${L(l)}</span>`:''};
const mineTitle=()=>{const c=S.clans.find(x=>x.name==MY().clan);return c?`🛡️ #${c.rank} ${c.name}${(c.w||[])[2]?' ⚔+'+L(c.w[2]):''} · Ninja Zenshin`:'Ninja Zenshin Live'};
function outHtml(){const rows=[];Object.entries(S.debt||{}).forEach(([c,a])=>a.forEach(d=>rows.push({c,...d})));rows.sort((a,b)=>b.ts-a.ts);
 const bar=d=>{const rec=d.rep-d.left,p=d.rep?100*rec/d.rep:100;return `<div class=card><b class=nm data-o="${esc(d.c)}">${esc(d.c)}</b> perdió a <b>${esc(d.who)}</b>${d.to?` <span class=m>→ ahora en <b>${esc(d.to)}</b></span>`:''} <span class=m>· hace ${ago(d.ts)}</span><br>Se fue con <span class=dn>−${L(d.rep)}</span> · recuperado <span class=up>${L(rec)}</span> (${Math.round(p)}%) · ${d.left>0?`faltan <b class=o>${L(d.left)}</b>`:'<b class=up>✅ recuperado por completo</b>'}<div style="height:6px;background:var(--l);border-radius:4px;margin-top:6px"><div style="height:6px;width:${p}%;background:var(--g);border-radius:4px"></div></div></div>`},
 ev=S.events.filter(e=>e.t=='leave'||e.t=='join').slice(-30).reverse();
 return '<h3>🚪 Reputación perdida por salidas</h3><p class=m>Cuando un miembro se va, su REP sale del clan. Esta barra baja a medida que el clan vuelve a ganar puntos atacando.</p>'+(rows.filter(d=>d.left>0).map(bar).join('')||'<p class=m>Ningún clan tiene reputación pendiente por recuperar.</p>')+(rows.some(d=>d.left<=0)?'<h4>Ya recuperadas</h4>'+rows.filter(d=>d.left<=0).slice(0,5).map(bar).join(''):'')+'<h3>Últimas entradas y salidas</h3>'+(ev.map(e=>`<div class=card>${e.t=='leave'?'🚪':'🆕'} <b>${esc(e.who)}</b> ${e.t=='leave'?'salió de':'se unió a'} <b>${esc(e.clan)}</b>${e.frm?` <span class=m>(venía de ${esc(e.frm)})</span>`:''} <span class=${e.t=='leave'?'dn':'up'}>${L(e.rep)} REP</span> <span class=m style="float:right">hace ${ago(e.ts)}</span></div>`).join('')||'<p class=m>Sin movimientos de miembros todavía (se detectan mientras el tracker está abierto y conectado a los miembros).</p>')}
document.addEventListener('click',e=>{const t=e.target,b=t.closest('button[data-t]'),fe=t.closest('[data-f]'),oe=t.closest('[data-o]'),be=t.closest('[data-b]'),me=t.closest('[data-mf]'),fp=t.closest('[data-fp]'),ff=t.closest('[data-ff]'),ps=t.closest('[data-ps]'),vs=t.closest('[data-vs]'),t2=t.closest('[data-t2]'),o=oe&&oe.dataset.o;
 if(b){tab=b.dataset.t;try{localStorage.tab=tab}catch(x){}$('#q').value='';draw();if(innerWidth<700)setTimeout(()=>$('nav').scrollIntoView({behavior:'smooth',block:'start'}),60)}
 if(t2){tab=t2.dataset.t2;try{localStorage.tab=tab}catch(x){}draw()}
 if(be){openClan(be.dataset.b);openBurn(be.dataset.b)}else if(o)openClan(o);
 if(t.id=='md')closeAll();
 const te=t.closest('#ts>div');if(te&&!oe)te.remove();
 if(fe){const f=fe.dataset.f;fav.has(f)?fav.delete(f):fav.add(f);localStorage.fav=JSON.stringify([...fav]);fetch('/cfg',{method:'POST',body:JSON.stringify({mon:[...fav]})});draw()}
 if(me){const q=me.dataset.mf;favm.has(q)?favm.delete(q):favm.add(q);localStorage.favm=JSON.stringify([...favm]);draw()}
 if(fp){const q=fp.dataset.fp;favp.has(q)?favp.delete(q):favp.add(q);try{localStorage.favp=JSON.stringify([...favp])}catch(x){}toast(favp.has(q)?'★ Te aviso cada vez que <b>'+esc(q)+'</b> haga puntos':'Dejaste de seguir a <b>'+esc(q)+'</b>','#ffd43b');draw()}
 if(ff){feedF=ff.dataset.ff;draw()}if(ps){pveSort=ps.dataset.ps;draw()}if(vs){pvpSort=vs.dataset.vs;draw()}});
document.addEventListener('keydown',e=>{if(e.key=='Escape'){if($('#lbx').style.display=='flex')$('#lbx').style.display='none';else if(KYR)KYR=null;else if(XEDIT)XEDIT=null;else if(SHEDIT)SHEDIT=false;else if(NSET)NSET=false;else if(omem)omem=null;else if(OB)OB=null;else if(OR)OR=null;else OC=null;draw()}});
let _sq=0;$('#q').oninput=()=>{clearTimeout(_sq);_sq=setTimeout(draw,150)};

/* ===================== V15 · EN VIVO ===================== */
const favp=new Set((()=>{try{return JSON.parse(localStorage.favp||'[]')}catch(e){return[]}})());
let ALT='',UNR=0,FAVI='',NSET=false,feedF='all',pveSort='rank',pveAct=false,pvpSort='rank',RN=+(localStorage.rn||10),ntSeeded=false,lastTone=0,ALM=0,lastSiren=0;
const AT={},PT={},PF={},VF={},CF={},RC={},NT={x:0,last:0,run:false,hov:false},HL={p:0,t:0},RW=120;
const FF={all:null,atk:['hit','gain'],pve:['pve','pvepass','pvein'],pvp:['pvp'],mem:['mem','leave','join'],rank:['rank','pass']};
const hhmmss=t=>new Date(t*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});
const clanOf=n=>S.clans.find(x=>x.name==n)||{};

// que cambio desde el mensaje anterior -> destellos y burbujas "+N"
function liveDiff(P){const t=Date.now();if(!P)return;
 if(P.pve&&S.pve){const o={};P.pve.forEach(p=>o[p.name]=p.score);S.pve.forEach(p=>{const d=p.score-(o[p.name]??p.score);if(d>0)PF[p.name]=[t,d]})}
 if(P.pvp&&S.pvp){const o={};P.pvp.forEach(p=>o[p.name]=p);S.pvp.forEach(p=>{const q=o[p.name];if(q&&(p.w>q.w||p.l>q.l))VF[p.name]=[t,p.w-q.w,p.l-q.l]})}
 if(P.clans&&S.clans){const o={};P.clans.forEach(c=>o[c.name]=c.rep);S.clans.forEach(c=>{const d=c.rep-(o[c.name]??c.rep);if(d)CF[c.name]=[t,d]})}}

// sonidos distintos: ataque de cualquiera, PvE, sirena si te atacan
function tone(seq,vol){if(!snd)return;const n=Date.now();if(n-lastTone<200)return;lastTone=n;try{AC=AC||new AudioContext();if(AC.state=='suspended')AC.resume();let t=AC.currentTime+.01;
 seq.forEach(([f,d,ty])=>{const o=AC.createOscillator(),g=AC.createGain();o.type=ty||'sine';o.frequency.setValueAtTime(f,t);g.gain.setValueAtTime(vol||.16,t);g.gain.exponentialRampToValueAtTime(.001,t+d);o.connect(g);g.connect(AC.destination);o.start(t);o.stop(t+d+.02);t+=d*.92})}catch(e){}}
const blip=m=>tone(m?[[660,.1],[990,.2]]:[[880,.08],[1175,.12]]),chime=()=>tone([[1046,.1],[1318,.1],[1568,.2]],.11);
function siren(){if(Date.now()-lastSiren<3500)return;lastSiren=Date.now();lastTone=0;tone([[600,.28,'sawtooth'],[900,.28,'sawtooth'],[600,.28,'sawtooth'],[900,.34,'sawtooth']],.09)}
function toggleSnd(){snd=!snd;const b=$('#snd');if(b){b.textContent=snd?'🔊 Sonido ON':'🔇 Sonido';b.classList.toggle('on',snd)}if(snd){lastBeep=0;lastTone=0;tone([[880,.12],[1320,.16]])}}

// ⚔️ ALERTA INSTANTANEA: "CLAN X ESTA ATACANDO" + su REP, puesto, rafaga, objetivo y jugadores
function atkOK(cl){if(NS.atk=='off')return false;if(isMine(cl)||fav.has(cl)||NS.atk=='all')return true;const r=clanOf(cl).rank||999;return NS.atk=='top10'?r<=10:NS.atk=='top25'?r<=25:false}
function atkEv(e){let a=AT[e.clan];if(!a||e.ts-a.last>90)a=AT[e.clan]={tot:0,n:0,t0:e.ts,last:e.ts,tg:{},pl:{},an:0};a.tot+=e.amt;a.n++;a.last=Math.max(a.last,e.ts);
 if(!atkOK(e.clan)||a.tot<NS.amin)return;const mine=isMine(e.clan);atkShow(e.clan,!a.an||mine);   // aviso de Windows solo al EMPEZAR la rafaga (o si es tu clan)
 if(!a.an){a.an=1;const c=clanOf(e.clan);blip(mine);say((mine?'Tu clan ':'')+e.clan+' está atacando. Reputación '+Math.round(c.rep||e.rep||0).toLocaleString('es-ES'))}else if(mine||fav.has(e.clan))tone([[990,.06]],.08)}
function atkShow(cl,sys){const a=AT[cl];if(!a||!atkOK(cl)||a.tot<NS.amin)return;const c=clanOf(cl),i=S.clans.indexOf(c),u=i>0?S.clans[i-1]:null,mine=isMine(cl),
 tg=Object.entries(a.tg).sort((x,y)=>y[1]-x[1]).slice(0,2),pl=Object.entries(a.pl).sort((x,y)=>y[1]-x[1]).slice(0,3);
 toast(`<div class=tb1>⚔️ <b class=nm data-o="${esc(cl)}">${esc(cl.toUpperCase())}</b> ${mine?'<span class=g>TU CLAN</span> ':''}<span class=up>ESTÁ ATACANDO +${L(a.tot)}</span>${a.n>1?` <span class=cmb>×${a.n}</span>`:''}</div>`+
  `<div class=tl>REP <b>${L(c.rep||0)}</b> · <b>#${c.rank||'?'}</b>${(c.w||[])[2]?' · ventana <span class=o>+'+L(c.w[2])+'</span>':''}${u?` · <span class=m>a ${L(u.rep-c.rep)} del #${u.rank}</span>`:c.rank==1?' · 👑 líder':''}</div>`+
  (tg.length?`<div class=tl>➜ contra ${tg.map(([v,p])=>`<b class="dn nm" data-o="${esc(v)}">${esc(v)}</b> <span class=dn>−${L(p)}</span>`).join(' · ')}</div>`:'')+
  (pl.length&&(mine||fav.has(cl))?`<div class="tl m">👤 ${pl.map(([w,p])=>esc(w)+' <span class=up>+'+L(p)+'</span>').join(' · ')}</div>`:''),mine?'#3ddc84':'#ff9f43','a|'+cl,sys?1:0,mine)}

// 🚨 modo alarma cuando atacan a TU clan
function alarm(e){if(!NS.alarm)return;const s=(e.sus||[])[0],c=clanOf(e.to||e.clan);ALM=Date.now();const b=$('#almb');
 b.innerHTML=`🚨 ¡TE ATACAN! ${s?'<b>'+esc(s[0])+'</b> ➜ ':''}${esc(e.to||e.clan)} <span style="color:#ffd0d0">−${L(e.amt)} REP</span><div style="font-size:12px;font-weight:500;opacity:.9">REP ${L(c.rep||0)} · #${c.rank||'?'} · toca para cerrar</div>`;
 b.style.display='block';$('#alm').style.display='block';siren();if(NS.vib&&navigator.vibrate)try{navigator.vibrate([250,120,250,120,400])}catch(x){}}
function almOff(){ALM=0;$('#alm').style.display='none';$('#almb').style.display='none'}
setInterval(()=>{if(ALM&&Date.now()-ALM>9000)almOff()},500);

// 🏆 PvE y 🥊 PvP en vivo: avisos
function pveOK(e){if(NS.pve=='off')return false;if(favp.has(e.who))return true;if(NS.pve=='all')return e.t!='pve'||e.amt>=NS.pmin;
 if(NS.pve=='top30')return (e.rank||e.r||99)<=30&&(e.t!='pve'||e.amt>=NS.pmin);return !!(e.t30||e.gift||(e.t=='pvepass'&&e.r<=3))}
function pveEv(e){if(!pveOK(e))return;
 if(e.t=='pve'){const q=e.who,v=PT[q]&&Date.now()-PT[q].t<30000?PT[q]:(PT[q]={amt:0,n:0});v.amt+=e.amt;v.n++;v.t=Date.now();const up=(S.pve||[])[e.rank-2];
  toast(`<div class=tb1>🏆 <b>${esc(e.who)}</b> <span class=up>+${L(v.amt)} PvE</span>${v.n>1?` <span class=cmb>×${v.n}</span>`:''}${e.gift?' 🎁':''}${e.t30?' ⭐':''}</div><div class=tl>Score <b>${L(e.score||0)}</b> · <b>#${e.rank}</b>${e.orank&&e.orank!=e.rank?(e.rank<e.orank?' <span class=up>▲'+(e.orank-e.rank)+'</span>':' <span class=dn>▼'+(e.rank-e.orank)+'</span>'):''}${up?` · <span class=m>a ${L(up.score-e.score)} del #${up.rank}</span>`:e.rank==1?' · 👑 primero':''}</div>${e.t30?'<div class="tl up">⭐ ¡Entró al Top 30 (premio especial)!</div>':''}${e.gift?'<div class="tl o">🎁 ¡Pasó los 300.000 (premio por score)!</div>':''}`,'#ffd43b','pve|'+q,favp.has(e.who)?1:0);chime()}
 else if(e.t=='pvepass')toast(`🏆 <b>${esc(e.who)}</b> <span class=up>${IC.tu} superó a</span> <b>${esc(e.over)}</b> en PvE → <b>#${e.r}</b>`,'#ffd43b','pp|'+e.who+'|'+e.over);
 else if(e.t=='pvein')toast(`🆕 <b>${esc(e.who)}</b> entró al ranking PvE → <b>#${e.rank}</b> <span class=m>(${L(e.score||0)})</span>`,'#ffd43b','pi|'+e.who)}
function pvpEv(e){if(NS.pvp=='off'||(NS.pvp=='fav'&&!favp.has(e.who)))return;const p=(S.pvp||[]).find(x=>x.name==e.who)||{};
 toast(`🥊 <b>${esc(e.who)}</b> ${e.w?'<span class=up>ganó'+(e.w>1?' ×'+e.w:'')+'</span>':''}${e.l?' <span class=dn>perdió'+(e.l>1?' ×'+e.l:'')+'</span>':''} <span class=m>· ${L(p.w||0)}W / ${L(p.l||0)}L (${p.wr||0}%)</span>${p.st>=3?' 🔥 racha '+p.st:''}`,'#7aa2ff','pvp|'+e.who)}

// titular grande + cinta de noticias estilo TV
function tkText(e){const R=n=>{const x=clanOf(n);return x.rep?` <span class=m>· REP ${L(x.rep)} (#${x.rank})</span>`:''};
 if(e.t=='hit'){const s=(e.sus||[])[0];return s?`⚔️ <b class="up nm" data-o="${esc(s[0])}">${esc(s[0])}</b> ataca a <b class="dn nm" data-o="${esc(e.to)}">${esc(e.to)}</b> <span class=dn>−${L(e.amt)}</span>${R(s[0])}`:`🩸 <b class="dn nm" data-o="${esc(e.to)}">${esc(e.to)}</b> perdió <b class=dn>−${L(e.amt)}</b> <span class=m>· ${e.pending?'identificando atacante…':'atacante no identificado'}</span>`}
 if(e.t=='gain')return `⚔️ <b class="up nm" data-o="${esc(e.clan)}">${esc(e.clan)}</b> está atacando <span class=up>+${L(e.amt)}</span>${R(e.clan)}`;
 if(e.t=='pass')return `📈 <b class=nm data-o="${esc(e.clan)}">${esc(e.clan)}</b> superó a <b class=nm data-o="${esc(e.over)}">${esc(e.over)}</b> → #${e.r}`;
 if(e.t=='pve')return `🏆 <b>${esc(e.who)}</b> <span class=up>+${L(e.amt)}</span> PvE → ${L(e.score||0)} <span class=m>(#${e.rank})</span>${e.t30?' ⭐':''}${e.gift?' 🎁':''}`;
 if(e.t=='pvepass')return `🏆 <b>${esc(e.who)}</b> superó a <b>${esc(e.over)}</b> en PvE → #${e.r}`;
 if(e.t=='pvp')return `🥊 <b>${esc(e.who)}</b> ${e.w?'<span class=up>ganó</span>':'<span class=dn>perdió</span>'} en PvP`;
 return ''}
function headline(F){const pr=e=>{const s=(e.sus||[])[0];if(e.t=='hit'&&isMine(e.to))return 100;if(e.t=='gain'&&isMine(e.clan))return 90;if(e.t=='hit'&&s)return 70;if(e.t=='gain')return 60+Math.min(9,e.amt/1000);if(e.t=='hit')return 55;if(e.t=='pass')return 50;if(e.t=='pve')return 40;if(e.t=='pvepass')return 38;if(e.t=='pvp')return 20;return 0};
 let b=null,bp=0;F.forEach(e=>{const p=pr(e);if(p>bp){b=e;bp=p}});if(!b)return;if(bp<HL.p&&Date.now()-HL.t<6000)return;const h=tkText(b);if(!h)return;HL.p=bp;HL.t=Date.now();$('#tkt').innerHTML=h+` <span class=m style="font-weight:400;font-size:12px">${hhmmss(b.ts)}</span>`;const k=$('#tk');k.classList.remove('fl');void k.offsetWidth;k.classList.add('fl')}
function ntPush(h){const box=$('#ntx'),cw=$('#nt').clientWidth;if(!cw)return;if(!box.children.length)NT.x=cw;const sw=box.scrollWidth,back=NT.x+sw-cw;if(back>cw*6)return;   // demasiada cola: se salta
 const gap=cw-(NT.x+sw);if(box.children.length&&gap>0){const sp=document.createElement('span');sp.style.cssText='flex:none;width:'+gap+'px';box.appendChild(sp)}
 const s=document.createElement('span');s.className='nti';s.innerHTML=h;box.appendChild(s);if(!NT.run){NT.run=true;NT.last=performance.now();requestAnimationFrame(ntLoop)}}
function ntLoop(t){const box=$('#ntx'),cw=$('#nt').clientWidth,dt=Math.min(100,t-NT.last);NT.last=t;const back=Math.max(0,NT.x+box.scrollWidth-cw);NT.x-=(NT.hov?0:(70+Math.min(260,back/5)))*dt/1000;
 let f=box.firstElementChild;while(f&&NT.x+f.offsetWidth+26<0){NT.x+=f.offsetWidth+26;f.remove();f=box.firstElementChild}
 box.style.transform=`translateX(${NT.x.toFixed(1)}px)`;if(box.children.length)requestAnimationFrame(ntLoop);else NT.run=false}
function ntEv(e,ctx){if(!NS.nt)return;if(e.t=='gain'&&ctx&&ctx.hitAtt.has(e.clan))return;if(e.t=='hit'&&e.pending)return;if(e.t=='rank'&&!isMine(e.clan))return;
 if(!['hit','gain','pass','pve','pvepass','pvein','pvp','leave','join','rank'].includes(e.t))return;
 const h=tkText(e)||(e.t=='leave'?`🚪 <b>${esc(e.who)}</b> salió de <b class=nm data-o="${esc(e.clan)}">${esc(e.clan)}</b>`:e.t=='join'?`🆕 <b>${esc(e.who)}</b> entró a <b class=nm data-o="${esc(e.clan)}">${esc(e.clan)}</b>`:e.t=='pvein'?`🆕 <b>${esc(e.who)}</b> entró al ranking PvE #${e.rank}`:e.t=='rank'?`📊 Tu clan ${e.b<e.a?'subió':'bajó'} al #${e.b}`:'');
 if(h)ntPush(`<span class=m>${hhmmss(e.ts)}</span> ${h}`)}
setTimeout(()=>{const n=$('#nt');if(n){n.onmouseenter=()=>NT.hov=true;n.onmouseleave=()=>NT.hov=false}},0);

// 📡 radar: clanes atacando AHORA (rafagas), batalla y PvE
function bursts(){const now=NOW(),B={};for(const e of S.events){if(e.t!='gain'||now-e.ts>900)continue;let b=B[e.clan];if(!b||e.ts-b.last>90)b=B[e.clan]={clan:e.clan,start:e.ts,last:e.ts,tot:0,n:0,tg:{},pl:{}};b.tot+=e.amt;b.n++;b.last=e.ts}
 for(const e of S.events){if(now-e.ts>900)continue;if(e.t=='hit'&&e.sus&&e.sus.length)e.sus.forEach((s,i)=>{const b=B[s[0]];if(b&&(i==0||s[2]>=30)&&e.ts>=b.start-8)b.tg[e.to]=(b.tg[e.to]||0)+Math.round(e.amt*s[2]/100)});else if(e.t=='mem'){const b=B[e.clan];if(b&&e.ts>=b.start-3)b.pl[e.who]=(b.pl[e.who]||0)+e.amt}}
 return Object.values(B).filter(b=>now-b.last<=RW).sort((a,b)=>b.last-a.last)}
const topE=(o,n)=>Object.entries(o).sort((a,b)=>b[1]-a[1]).slice(0,n);
function radarHtml(){const now=NOW(),B=bursts(),mv={};S.events.forEach(e=>{if(e.t=='mem'&&now-e.ts<300){const q=e.clan+'|'+e.who;mv[q]=(mv[q]||0)+e.amt}});const M=topE(mv,1)[0];
 const rows=B.slice(0,5).map(b=>{const c=clanOf(b.clan),age=now-b.last,tg=topE(b.tg,2),pl=topE(b.pl,3),mine=isMine(b.clan);
  return `<div class="rad ${age<6?'on':''} ${mine?'mine':''}" style="--a:${Math.max(0,1-age/RW).toFixed(2)}"><div class=rh><span class="dt ${age<60?'a1':'a3'}" style="animation-delay:${PH()}"></span><b class="nm up" data-o="${esc(b.clan)}">${esc(b.clan)}</b><span class=m>#${c.rank||'?'}</span>${fire(c)}${mine?'🛡️':''}<span style="margin-left:auto;white-space:nowrap"><b class=up>+${L(b.tot)}</b>${b.n>1?` <span class=cmb>×${b.n}</span>`:''}</span></div>`+
   `<div class=rl>REP <b>${L(c.rep||0)}</b>${tg.length?' · ➜ '+tg.map(([v,p])=>`<b class="dn nm" data-o="${esc(v)}">${esc(v)}</b> <span class=dn>−${L(p)}</span>`).join(', '):''} <span class=m>· hace <span data-ts="${b.last}">${ago(b.last)}</span></span></div>`+
   (pl.length?`<div class="rl m">👤 ${pl.map(([w,p])=>esc(w)+' <span class=up>+'+L(p)+'</span>').join(' · ')}</div>`:'')+`<div class=rbar style="width:${Math.max(0,100-100*age/RW).toFixed(1)}%"></div></div>`}).join('');
 return `<div><h3 class=rt>⚡ Atacando ahora ${B.length?`<span class="act al lv" style="animation-delay:${PH()}">${B.length} clan${B.length>1?'es':''}</span>`:''}</h3>${rows||'<div class="card m">😴 Ningún clan atacando en los últimos 2 min</div>'}${M?`<div class=m style="font-size:12px">👤 MVP 5 min: <b class=up>${esc(M[0].split('|')[1])}</b> (${esc(M[0].split('|')[0])}) +${L(M[1])}</div>`:''}</div>`}
function pveNow(){const now=NOW(),ag={};S.events.forEach(e=>{if(e.t=='pve'&&now-e.ts<600){const x=ag[e.who]||(ag[e.who]={who:e.who,amt:0,n:0,last:0,score:0,rank:0});x.amt+=e.amt;x.n++;x.last=e.ts;x.score=e.score||x.score;x.rank=e.rank||x.rank}});
 const R=Object.values(ag).sort((a,b)=>b.last-a.last),act=R.filter(x=>now-x.last<300).length;
 return `<div><h3 class=rt>🏆 PvE ahora ${act?`<span class=lvp style="animation-delay:${PH()}">${act} jugando</span>`:''}</h3>`+(R.slice(0,5).map(x=>{const age=now-x.last;return `<div class="rad pv ${age<6?'on':''}" style="--a:${Math.max(0,1-age/600).toFixed(2)}"><div class=rh><b style="cursor:pointer" data-t2=pve>${esc(x.who)}</b><span class=m>#${x.rank}</span>${favp.has(x.who)?'<span style="color:#ffd43b">★</span>':''}<span style="margin-left:auto;white-space:nowrap"><b style="color:var(--y)">+${L(x.amt)}</b>${x.n>1?` <span class=cmb>×${x.n}</span>`:''}</span></div><div class=rl>Score <b>${L(x.score)}</b> <span class=m>· hace <span data-ts="${x.last}">${ago(x.last)}</span></span></div><div class=rbar style="width:${Math.max(0,100-100*age/600).toFixed(1)}%"></div></div>`}).join('')||'<div class="card m">😴 Nadie sumó puntos PvE en 10 min</div>')+'</div>'}

// 🏁 carrera en vivo (barras que se mueven y cambian de lugar)
function raceShell(){return `<div class=card><div class=mh><h3 style=margin:0>🏁 Carrera en vivo · Top <select onchange="RN=+this.value;localStorage.rn=RN;this.blur();force=true;draw()">${[10,15,20].map(v=>`<option ${v==RN?'selected':''}>${v}</option>`).join('')}</select></h3><span class=m style="font-size:12px">verde = atacando · rojo = sangrando · la burbuja muestra lo que acaba de cambiar</span></div><div id=race class=race style="height:${RN*38}px;margin-top:12px"></div></div><div class=card id=race2></div>`}
function raceUpdate(){const box=$('#race');if(!box)return;const C=S.clans.slice(0,RN);if(!C.length)return;const mx=C[0].rep,mn=C[C.length-1].rep,lo=Math.max(0,mn-(mx-mn)*.35),sp=Math.max(1,mx-lo),now=NOW(),seen=new Set();
 C.forEach((c,i)=>{seen.add(c.name);let el=RC[c.name];if(!el||!el.isConnected){el=document.createElement('div');el.className='rr';el.innerHTML='<span class=rn></span><div class=rw><div class=rb></div><div class=rt2></div></div><span class=rv></span><span class="rg m"></span>';el.style.transform=`translateY(${RN*38}px)`;box.appendChild(el);RC[c.name]=el;void el.offsetWidth}
  let atk=c.g10>0&&now-(c.last||0)<30,bld=c.l10>0&&now-(c.lbl||0)<60;if(atk&&bld){if(c.g10>=c.l10)bld=false;else atk=false}el.className='rr'+(atk?' atk':'')+(bld?' bld':'')+(isMine(c.name)?' me':'');
  el.style.transform=`translateY(${i*38}px)`;el.querySelector('.rn').innerHTML=i<3?['🥇','🥈','🥉'][i]:'#'+c.rank;el.querySelector('.rb').style.width=(8+92*(c.rep-lo)/sp).toFixed(2)+'%';
  const f=CF[c.name],fa=f?(Date.now()-f[0])/1000:99;
  el.querySelector('.rt2').innerHTML=`${arrow(c)}<b class=nm data-o="${esc(c.name)}">${esc(c.name)}</b> ${fire(c)}${fa<2.6?`<span class="bub ${f[1]<0?'dn':''}" style="animation-delay:-${fa.toFixed(2)}s">${f[1]>0?'+':''}${L(f[1])}</span>`:''}`;
  el.querySelector('.rv').textContent=L(c.rep);const u=C[i-1];el.querySelector('.rg').textContent=u?'−'+L(u.rep-c.rep):'líder'});
 Object.keys(RC).forEach(k=>{if(!seen.has(k)){RC[k].remove();delete RC[k]}});const r2=$('#race2');if(r2)r2.innerHTML=raceStats(C)}
function raceStats(C){const cl=[];for(let i=1;i<C.length;i++)cl.push({a:C[i],b:C[i-1],g:C[i-1].rep-C[i].rep});cl.sort((x,y)=>x.g-y.g);
 return '<b>🔥 Peleas por el puesto</b> <span class=m>(las diferencias más cerradas)</span>'+cl.slice(0,5).map(x=>`<div class=hb><span class=m style="width:74px;flex:none">#${x.a.rank} vs #${x.b.rank}</span><span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><b class=nm data-o="${esc(x.a.name)}">${esc(x.a.name)}</b> <span class=m>→</span> <b class=nm data-o="${esc(x.b.name)}">${esc(x.b.name)}</b></span><b class=${x.g<500?'dn':'o'}>${L(x.g)}</b></div>`).join('')+'<h4>⏳ Próximos adelantamientos (ritmo actual)</h4>'+etaT()}

// 🏆 pestaña PvE en vivo
const pArrow=p=>{const t=p.rk,age=t?NOW()-t[0]:1e9;if(!t||age>NS.arw)return'<span class=ra></span>';const up=t[2]<t[1];return `<span class="ra ${up?'up':'dn'} ${age<20?'new':''}" title="${up?'Subió':'Bajó'} del #${t[1]} al #${t[2]} hace ${ago(t[0])}">${up?IC.tu:IC.td}</span>`};
function pveHtml(){const A=S.pve||[],now=NOW(),q=$('#q').value.toLowerCase();if(!A.length)return rewCard('pve')+'<p class=m>No encontré la tabla PvE en la página (o todavía está cargando).</p>';
 const t30=A[29],mx=Math.max(1,...A.map(x=>x.g5||0)),g5=A.reduce((s,p)=>s+(p.g5||0),0),g60=A.reduce((s,p)=>s+(p.g60||0),0),act=A.filter(p=>p.t&&now-p.t<300).length,topF=[...A].sort((a,b)=>(b.g5||0)-(a.g5||0))[0];
 let P=A.filter(p=>p.name.toLowerCase().includes(q));if(pveAct)P=P.filter(p=>p.t&&now-p.t<900);const key={g5:'g5',g60:'g60',sess:'sess'}[pveSort];if(key)P=[...P].sort((a,b)=>(b[key]||0)-(a[key]||0)||a.rank-b.rank);
 const pf=p=>{const r=(p.g5||0)/mx;return flame(!p.g5?0:r>=.8?5:r>=.6?4:r>=.4?3:r>=.2?2:1)};
 const chips=[['rank','# Puesto'],['g5','⚡ +5 min'],['g60','+1 h'],['sess','Sesión']].map(([x,t])=>`<span class="fchip ${pveSort==x?'on':''}" data-ps="${x}">${t}</span>`).join('')+`<label class=m style="font-size:12px"><input type=checkbox ${pveAct?'checked':''} onchange="pveAct=this.checked;draw()"> solo activos</label>`;
 const tiles=[['⚡ Jugando (5 min)',`<span class=up>${act}</span>`],['📈 Puntos en 5 min','<span class=up>+'+L(g5)+'</span>'],['⏱ Puntos en 1 h','+'+L(g60)],['🥇 Farmer del momento',topF&&topF.g5?esc(topF.name)+'<br><span class=up style="font-size:12px">+'+L(topF.g5)+' en 5 min</span>':'–'],['🎯 Corte Top 30',t30?L(t30.score):'–'],['🎁 Con 300k+',A.filter(p=>p.score>=300000).length]].map(([a,b])=>tile(a,b)).join('');
 const rows=P.map(p=>{const f=PF[p.name],fa=f?(Date.now()-f[0])/1000:99,fr=fa<2.6,live=p.t&&now-p.t<60,up=A[p.rank-2],g=t30&&p.rank>30?t30.score-p.score:0,fv=favp.has(p.name);
  return `<tr class="${fr?'pfl':''} ${fv?'mine':''}" ${fr?`style="--d:-${fa.toFixed(2)}s"`:''}><td class=rk>${pArrow(p)}<b>${p.rank}</b><td><span class="star ${fv?'on':''}" data-fp="${esc(p.name)}" title="Avisarme cuando haga puntos">★</span> ${rkIcon(p.rank)} <b>${esc(p.name)}</b> ${pf(p)} ${live?`<span class=lvp style="animation-delay:${PH()}">⚡ EN VIVO</span>`:p.t&&now-p.t<1800?`<span class=m style="font-size:11px">hace <span data-ts="${p.t}">${ago(p.t)}</span></span>`:''}`+
   `<td class=num><b>${L(p.score)}</b>${fr?`<span class=bub style="animation-delay:-${fa.toFixed(2)}s">+${L(f[1])}</span>`:''}<td class="num ${p.g5?'up':'m'}">${p.g5?'+'+L(p.g5):'–'}<td class="num ${p.g60?'up':'m'}">${p.g60?'+'+L(p.g60):'–'}<td class="num ${p.sess>0?'o':'m'}">${p.sess>0?'+'+L(p.sess):'–'}<td class="num m">${p.g5?L(Math.round(p.g5/5))+'/min':'–'}<td class="num m">${up?L(up.score-p.score):'👑'}<td>${rewChips('pve',p.rank,p.score)||((p.rank<=30?'⭐':'')+' '+(p.score>=300000?'🎁':''))}<td class=m>${g>0?'faltan '+L(g+1):p.rank<=30?'dentro':'–'}</tr>`}).join('');
 const F=S.events.filter(e=>e.t=='pve'||e.t=='pvepass'||e.t=='pvein').slice(-40).reverse().map(e=>{const a=now-e.ts;return `<div class="fi2 ${a<.6?'nw':''}" ${a<.6?`style="animation-delay:-${Math.max(0,a).toFixed(2)}s"`:''}>${e.t=='pve'?`🏆 <b>${esc(e.who)}</b> <span class=up>+${L(e.amt)}</span> <span class=m>→ ${L(e.score||0)} · #${e.rank}</span>${e.orank&&e.orank>e.rank?` <span class=up>▲${e.orank-e.rank}</span>`:''}${e.t30?' ⭐':''}${e.gift?' 🎁':''}`:e.t=='pvepass'?`📈 <b>${esc(e.who)}</b> superó a <b>${esc(e.over)}</b> → #${e.r}`:`🆕 <b>${esc(e.who)}</b> entró al ranking #${e.rank}`}<span class=m style="float:right;font-size:11px"><span data-ts="${e.ts}">${ago(e.ts)}</span></span></div>`}).join('')||'<p class=m>Esperando puntos PvE…</p>';
 return rewCard('pve')+`<div class=mh><h3 style=margin:0>🏆 PvE EN VIVO <span class=m style="font-weight:400;font-size:12px">★ = avisarme cuando ese jugador haga puntos</span></h3><div>${chips}</div></div><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:10px 0">${tiles}</div><div class=pvg><div class=wrap><table class=mt><tr><th>#<th>Jugador<th class=num>Score<th class=num>+5 min<th class=num>+1 h<th class=num>Sesión<th class=num>Ritmo<th class=num>Al de arriba<th>Premio<th>Al top 30</tr>${rows}</table></div><div class=card style="margin:0;max-height:680px;overflow:auto"><b>📡 Últimos puntos</b>${F}</div></div><p class=m style="font-size:11px">⭐ Top 30 = premio especial · 🎁 300.000+ = premio por score · 👻 = nombre invisible · "Sesión" = lo que sumó desde que abriste el tracker · la fila brilla cuando alguien hace puntos.</p>`}

// 🥊 pestaña PvP
function pvpHtml(){const A=S.pvp||[],now=NOW(),q=$('#q').value.toLowerCase(),E=S.events.filter(e=>e.t=='pvp'),m5=E.filter(e=>now-e.ts<300),act=new Set(E.filter(e=>now-e.ts<900).map(e=>e.who)),R=S.rec||{};if(!A.length)return rewCard('pvp')+'<p class=m>No encontré la tabla PvP en la página.</p>';
 let P=A.filter(p=>p.name.toLowerCase().includes(q));if(pvpSort=='wr')P=[...P].sort((a,b)=>(b.wr||0)-(a.wr||0));if(pvpSort=='sess')P=[...P].sort((a,b)=>((b.sw||0)-(b.sl||0))-((a.sw||0)-(a.sl||0)));if(pvpSort=='st')P=[...P].sort((a,b)=>(b.st||0)-(a.st||0));
 const bs=[...A].sort((a,b)=>(b.st||0)-(a.st||0))[0],tiles=[['⚔️ Partidas (5 min)',m5.reduce((s,e)=>s+(e.w||0)+(e.l||0),0)],['🔥 Jugando (15 min)',act.size],['🏆 Más victorias hoy',(R.pvp||[])[0]?esc(R.pvp[0][0])+' ('+R.pvp[0][1]+')':'–'],['🎯 Mejor racha',bs&&bs.st>1?esc(bs.name)+' 🔥'+bs.st:'–']].map(([a,b])=>tile(a,b)).join('');
 const chips=[['rank','# Puesto'],['wr','% victorias'],['sess','Sesión'],['st','Racha']].map(([x,t])=>`<span class="fchip ${pvpSort==x?'on':''}" data-vs="${x}">${t}</span>`).join('');
 const rows=P.map((p,i)=>{const f=VF[p.name],fa=f?(Date.now()-f[0])/1000:99,fr=fa<2.6,fv=favp.has(p.name);
  return `<tr class="${fr?'pfl '+(f[1]>0?'gr':'rd'):''} ${fv?'mine':''}" ${fr?`style="--d:-${fa.toFixed(2)}s"`:''}><td>${p.rank||i+1}<td><span class="star ${fv?'on':''}" data-fp="${esc(p.name)}" title="Avisarme de sus partidas">★</span> ${rkIcon(p.rank)} <b>${esc(p.name)}</b> ${p.t&&now-p.t<60?`<span class="act lv" style="animation-delay:${PH()}">${IC.sw} EN VIVO</span>`:''}${fr?`<span class="bub ${f[1]>0?'':'dn'}" style="animation-delay:-${fa.toFixed(2)}s">${f[1]>0?'+'+f[1]+' W':'+'+f[2]+' L'}</span>`:''}<td class=num>${L(p.score)}<td class="num up">${L(p.w)}<td class="num dn">${L(p.l)}<td class=num>${p.wr||0}%<span class=wrb><i style="width:${p.wr||0}%"></i></span><td class=num>${p.sw||p.sl?`<span class=up>+${p.sw}</span> <span class=dn>−${p.sl}</span>`:'<span class=m>–</span>'}<td>${p.st>1?'🔥 '+p.st:p.st<-1?'🧊 '+(-p.st):'<span class=m>–</span>'}<td class=m>${p.t?'hace <span data-ts="'+p.t+'">'+ago(p.t)+'</span>':'–'}<td>${rewChips('pvp',p.rank||i+1,p.score)}</tr>`}).join('');
 const F=E.slice(-30).reverse().map(e=>`<div class=fi2>🥊 <b>${esc(e.who)}</b> ${e.w?'<span class=up>ganó'+(e.w>1?' ×'+e.w:'')+'</span>':''}${e.l?' <span class=dn>perdió'+(e.l>1?' ×'+e.l:'')+'</span>':''} <span class=m>· ${L(e.score||0)}</span><span class=m style="float:right;font-size:11px"><span data-ts="${e.ts}">${ago(e.ts)}</span></span></div>`).join('')||'<p class=m>Esperando partidas…</p>';
 return rewCard('pvp')+`<div class=mh><h3 style=margin:0>🥊 PvP EN VIVO</h3><div>${chips}</div></div><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0">${tiles}</div><div class=pvg><div class=wrap><table class=mt><tr><th>#<th>Jugador<th class=num>Score<th class=num>W<th class=num>L<th class=num>% victorias<th class=num>Sesión<th>Racha<th>Última<th>Premio</tr>${rows}</table></div><div class=card style="margin:0;max-height:620px;overflow:auto"><b>📡 Últimas partidas</b>${F}</div></div>`}

// 🏅 pestaña Hoy (records del dia)
function hoyHtml(){const R=S.rec||{};if(!R.d)return'<p class=m>Aún sin datos de hoy (se llenan mientras el tracker está abierto).</p>';
 const hr=R.hr||[],hm=Math.max(1,...hr),pk=hr.indexOf(Math.max(...hr)),H=R.hit,B=R.big,sm='<span class=m style="font-size:11px;font-weight:400">';
 const bl=(A,col,cls)=>{if(!A||!A.length)return'<p class=m>Sin datos todavía.</p>';const mx=Math.max(1,...A.map(x=>x[x.length-1]));return A.map((x,i)=>{const v=x[x.length-1],nm=x.length>2?`<b>${esc(x[1])}</b> <span class=m>· ${esc(x[0])}</span>`:cls=='c'?`<b class=nm data-o="${esc(x[0])}">${esc(x[0])}</b>`:`<b>${esc(x[0])}</b>`;
  return `<div class=hb2><div class=r1><span style="width:24px;flex:none">${medal(i)}</span><span class=nmx>${nm}</span><b style="color:var(--${col})">${L(v)}</b></div><div class=bw><div style="width:${(100*v/mx).toFixed(1)}%;background:var(--${col})"></div></div></div>`}).join('')};
 return `<h3>🏅 Récords de hoy <span class=m style="font-weight:400;font-size:12px">${esc(R.d)} · desde medianoche (hora de tu PC)</span></h3><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px">`+
  tile('💥 Golpe más grande',H?`<span class=dn>−${L(H[0])}</span><br>${sm}${esc(H[1])} ➜ ${esc(H[2])} · ${new Date(H[3]*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</span>`:'–')+tile('⚡ Mayor subida de una vez',B?`<span class=up>+${L(B[0])}</span><br>${sm}${esc(B[1])}</span>`:'–')+tile('⚔️ REP ganada (todos)','<span class=up>+'+L(R.tg||0)+'</span>')+tile('🩸 REP perdida (todos)','<span class=dn>−'+L(R.tl||0)+'</span>')+tile('🎯 Ataques identificados',L(R.nh||0))+tile('🔥 Hora más caliente',hr.some(v=>v)?pk+':00<br>'+sm+'+'+L(hr[pk])+'</span>':'–')+tile('🏆 Puntos PvE hoy',L(R.npv||0))+
  `</div><div class=hoy style="margin-top:12px"><div class=card><b>⚔️ Clanes que más ganaron</b>${bl(R.gain,'g','c')}</div><div class=card><b>🩸 Clanes que más perdieron</b>${bl(R.loss,'r','c')}</div><div class=card><b>👤 Jugadores que más atacaron</b>${bl(R.pl,'g','p')}</div><div class=card><b>🏆 PvE · más puntos hoy</b>${bl(R.pve,'o','p')}</div><div class=card><b>🥊 PvP · más victorias hoy</b>${bl(R.pvp,'o','p')}</div>`+
  `<div class=card><b>🕐 REP ganada por hora</b><div style="display:flex;gap:2px;align-items:flex-end;height:70px;margin-top:8px">${hr.map((v,i)=>`<div title="${i}:00 · +${L(v)}" style="flex:1;height:${(3+64*v/hm).toFixed(1)}px;background:var(--o);opacity:${v?(.35+.65*v/hm).toFixed(2):.15};border-radius:2px 2px 0 0"></div>`).join('')}</div><div class=m style="display:flex;justify-content:space-between;font-size:10px"><span>0h</span><span>6h</span><span>12h</span><span>18h</span><span>23h</span></div></div></div>`}

// 🔔 panel de avisos
function openSet(){NSET=true;draw()}
function setHtml(){const o=(v,t,c)=>`<option value="${v}" ${v==c?'selected':''}>${t}</option>`,ck=(k,t)=>`<label><input type=checkbox ${NS[k]?'checked':''} onchange="setNS('${k}',this.checked)"> ${t}</label>`,num=(k,w)=>`<input type=number min=1 value="${NS[k]}" style="width:${w||90}px" onchange="setNS('${k}',Math.max(1,+this.value||1))">`;
 return `<div class=set><div class=mh><h3 style=margin:0>🔔 Avisos en pantalla</h3><button onclick="NSET=false;draw()">✕</button></div>
 <h4>⚔️ Cuando un clan ataca <span class=m style="font-weight:400">(aviso al instante con su REP y puesto)</span></h4><label>Mostrar <select onchange="setNS('atk',this.value)">${o('all','Todos los clanes',NS.atk)}${o('top10','Top 10',NS.atk)}${o('top25','Top 25',NS.atk)}${o('fav','Solo mi clan y ★',NS.atk)}${o('off','Apagado',NS.atk)}</select> desde ${num('amin')} REP por ráfaga</label>
 <h4>🩸 Clanes que pierden REP</h4><label>Mostrar <select onchange="setNS('scope',this.value)">${o('all','Todos los clanes',NS.scope)}${o('mine','Solo mi clan y ★',NS.scope)}${o('off','Apagado',NS.scope)}</select> desde ${num('min')} REP</label><p class=m style="font-size:12px">Si ya se sabe quién atacó, verás un solo aviso (el del atacante), salvo que la víctima sea tu clan o uno con ★.</p>
 <h4>🏆 PvE</h4><label><select onchange="setNS('pve',this.value)">${o('all','Cada vez que alguien hace puntos',NS.pve)}${o('top30','Solo jugadores del Top 30',NS.pve)}${o('hit','Solo ★ y momentos clave (Top 30, 300k, podio)',NS.pve)}${o('off','Apagado',NS.pve)}</select> desde ${num('pmin',100)} puntos</label>
 <h4>🥊 PvP</h4><label><select onchange="setNS('pvp',this.value)">${o('all','Todas las partidas',NS.pvp)}${o('fav','Solo jugadores con ★',NS.pvp)}${o('off','Apagado',NS.pvp)}</select></label>
 <h4>🚨 Cuando atacan a tu clan</h4>${ck('alarm','Modo alarma: borde rojo en toda la pantalla + sirena (si el sonido está activo)')}${ck('vib','📳 Vibrar el celular')}
 <h4>📺 Pantalla</h4>${ck('radar','📡 Radar en vivo arriba (atacando ahora · batalla · PvE)')}${ck('nt','📰 Cinta de noticias en movimiento')}
 <h4>🔊 Sonido y voz</h4><label><button onclick="toggleSnd();this.textContent=snd?'🔊 Sonido ON':'🔇 Sonido'">${snd?'🔊 Sonido ON':'🔇 Sonido'}</button><button onclick="setNS('voice',!NS.voice);this.textContent=NS.voice?'🗣 Voz ON':'🗣 Voz'">${NS.voice?'🗣 Voz ON':'🗣 Voz'}</button><button onclick="toggleDesk()">💻 Escritorio${NS.desk?' ON':''}</button></label><p class=m style="font-size:12px">Escritorio: aviso de Windows aunque la pestaña esté minimizada. El navegador solo deja sonar audio después de un clic en la página.</p>
 <p style="margin-top:14px"><button onclick="testAlerts()">▶️ Probar avisos</button></p></div>`}
function testAlerts(){const c=S.clans[0];if(!c)return toast('Aún no hay datos para probar','#ff4d4d');const o=AT[c.name];AT[c.name]={tot:250,n:3,t0:NOW(),last:NOW(),tg:S.clans[1]?{[S.clans[1].name]:250}:{},pl:{},an:1};
 const ok=atkOK(c.name);atkShow(c.name);if(o)AT[c.name]=o;else delete AT[c.name];if(!ok)toast('Con el filtro actual ("'+NS.atk+'") este clan no daría aviso','#ff9f43');blip(false);say(c.name+' está atacando. Reputación '+Math.round(c.rep).toLocaleString('es-ES'));
 const p=(S.pve||[])[1];if(p)setTimeout(()=>{toast(`<div class=tb1>🏆 <b>${esc(p.name)}</b> <span class=up>+5.000 PvE</span> <span class=m>(prueba)</span></div><div class=tl>Score <b>${L(p.score)}</b> · <b>#${p.rank}</b></div>`,'#ffd43b','pvetest');chime()},900);if(!snd)toast('🔇 El sonido está apagado: actívalo para oír los avisos','#ff9f43')}

// salud de la conexion, tiempos "hace Xs" en vivo, icono y titulo de la pestana
setInterval(()=>{const el=$('#hp');if(!el)return;const d=S.diag||{},age=S.ts?NOW()-S.ts:1e9,pl=+d.poll||1,lim=Math.max(6,pl*3+3),ok=SSEOK&&age<lim&&!S.error;
 el.className='hp '+(ok?'ok':'bad');el.innerHTML=`<i></i>${ok?'EN VIVO':!SSEOK?'SIN CONEXIÓN':'ATRASADO'} · ${age>1e8?'–':age<1?'ahora':age.toFixed(age<10?1:0)+' s'} · lectura ${d.lat!=null?d.lat+' s':'–'}${pl<1?' · 🚀':''}`;
 el.title='Hace cuánto llegó la última lectura del sitio y cuánto tardó en responder. Si sale ATRASADO, el sitio está lento o caído.';
 const s=$('#stale');if(s)s.innerHTML=S.ts&&SSEOK&&age>=lim?`<div class=err>⏳ Los datos están atrasados: la última lectura buena fue hace ${Math.round(age)} s. ${S.error?'El sitio responde con error.':'El sitio está tardando en responder.'}</div>`:''},250);
setInterval(()=>document.querySelectorAll('[data-ts]').forEach(x=>{x.textContent=ago(+x.dataset.ts)}),1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){UNR=0;draw()}});
function setFav(ic){if(ic==FAVI)return;FAVI=ic;const l=$('#fav');if(l)l.href='data:image/svg+xml,'+encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="86">${ic}</text></svg>`)}
if(!document.querySelector('nav button[data-t="'+tab+'"]'))tab='clanes';

/* ===================== RECOMPENSAS (PvE / PvP / CW) + TOKENS Y REVENDEDORES ===================== */
let XV=-1,XT={rew:{},shop:{tok:[],res:[]},pkgs:[],lb:[],news:[],server:{},archive:{cw_skills:{},pve_skills:{},cd:{}},payments:[],bonus_pkgs:[],kyubi_live:{rows:[]}},XEDIT=null,SHEDIT=false,UPN=0;
const RMODE={pve:['🏆','PvE','#ffd43b'],pvp:['🥊','PvP','#7aa2ff'],cw:['⚔️','Clan War','#ff9f43'],raid:['🦊','Kyubi','#ff7a1a']};
function loadX(){return fetch('/extras').then(r=>r.json()).then(j=>{XT=j;XV=j.v;renderSide();try{kyCheck()}catch(e){}draw()}).catch(()=>{})}
const icoFor=t=>/hair|peinado/i.test(t)?'💇':/outfit|costume|traje|ropa|\bset\b/i.test(t)?'👘':/weapon|arma|sword|espada/i.test(t)?'🗡️':/back|espalda/i.test(t)?'🎒':/jutsu|skill|kinjutsu|técnica/i.test(t)?'📜':/pet|mascota/i.test(t)?'🐾':/token/i.test(t)?'💎':/gold|oro/i.test(t)?'🪙':/emblem|premium/i.test(t)?'👑':/title|título/i.test(t)?'🏷️':'🎁';
function rrule(c){c=String(c||'');const q={};let m;   // "Rank 1–30", "Top 1-3", "Rank 31+", "50,000+ Score", "Rank 1–30 + minimum 100,000 Event Points"
 if((m=c.match(/(?:rank|top|puesto|lugar|place|#)\s*#?(\d+)\s*(?:[–\-—~]|to|al?)\s*#?(\d+)/i))){q.a=+m[1];q.b=+m[2]}
 else if((m=c.match(/(?:rank|top|puesto|lugar|place|#)\s*#?(\d+)\s*\+/i))){q.a=+m[1];q.b=1e9}
 else if((m=c.match(/(?:rank|top|puesto|lugar|place|#)\s*#?(\d+)/i)||c.match(/(\d+)\s*(?:st|nd|rd|th|°|º|er|do|ro|to)?\s*(?:lugar|puesto|place)/i))){q.a=+m[1];q.b=+m[1]}
 else if((m=c.match(/^\s*(\d+)\s*[–\-—]\s*(\d+)\s*$/))){q.a=+m[1];q.b=+m[2]}
 m=c.match(/(?:minimum|mínimo|minimo|min\.?|al menos|at least)\s*([\d.,]+)/i)||c.match(/(\d{1,3}(?:[.,]\d{3})+|\d{4,})\s*\+/)||(q.a==null?c.match(/(\d[\d.,]*)\s*\+/)||c.match(/(?:score|puntos|rep|points?)\D{0,4}(\d[\d.,]*)/i):null);
 if(m)q.s=+m[1].replace(/\D/g,'');return q.a!=null||q.s!=null?q:null}
const rwOk=(q,rank,score)=>(q.a==null||(rank>=q.a&&rank<=q.b))&&(q.s==null||score>=q.s);
const rewMin=r=>{for(const x of (r&&r.notes)||[]){const m=String(x).match(/minimum score of\s*([\d.,]+)|m[ií]nimo[^\d]{0,20}([\d.,]+)/i);if(m)return +(m[1]||m[2]).replace(/\D/g,'')}return 0};
function rewFor(m,rank,score){const r=(XT.rew||{})[m];if(!r||!r.rows)return[];const mn=rewMin(r);
 return r.rows.map(x=>{const q=rrule(x.cond);if(!q)return null;if(!rwOk(q,rank,score))return null;return Object.assign({},x,{blk:!!(mn&&/jutsu|skill|kinjutsu/i.test(x.item)&&score<mn)})}).filter(Boolean)}
const shortItem=t=>{t=String(t).replace(/^Kinjutsu:\s*/i,'').replace(/,?\s*or a combination.*$/i,'');return t.length>24?t.slice(0,23)+'…':t};
function rewChips(m,rank,score,compact){const mn=rewMin((XT.rew||{})[m]);return rewFor(m,rank,score).map(x=>`<span class="rwch notr ${x.blk?'no':''}" title="${esc(x.cond+' → '+x.item)}${x.blk?' · necesita '+L(mn)+' de score':''}">${x.img?`<img src="${esc(x.img)}" referrerpolicy=no-referrer onerror="this.style.display='none'" alt="">`:icoFor(x.item)}${compact?'':' '+esc(shortItem(x.item))}${x.blk?' ⚠️':''}</span>`).join('')}
function rwWho(m,x){const q=rrule(x.cond);if(!q)return'';const A=m=='pve'?(S.pve||[]).map(p=>[p.name,p.rank,p.score]):m=='pvp'?(S.pvp||[]).map((p,i)=>[p.name,p.rank||i+1,p.score]):m=='raid'?kyBoard().map(p=>[p.n,p.rank,p.p]):S.clans.map(c=>[c.name,c.rank,c.rep]);
 if(m=='raid'&&!A.length)return'';const W=A.filter(a=>rwOk(q,a[1],a[2]));if(!W.length)return'<span class=m>· nadie todavía</span>';const last=W.reduce((a,b)=>b[1]>a[1]?b:a);
 return `<span class=m>· ahora: ${W.length<=3?W.map(a=>'<b class="'+(m=='cw'?'nm':'')+'" '+(m=='cw'?'data-o="'+esc(a[0])+'"':'')+'>'+esc(a[0])+'</b>').join(', '):W.length+' '+(m=='cw'?'clanes':'jugadores')}${q.b&&q.b<1e9&&W.length>3?' · corte '+L(last[2]):''}</span>`}
function endTxt(r){if(!r.end)return'';const s=r.end-NOW();if(s<=0)return'<span class=dn>· terminó</span>';const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),mi=Math.floor(s%3600/60);return `<span class=o title="Según la fecha del anuncio">· termina en ${d?d+'d ':''}${h}h${d?'':' '+mi+'m'}</span>`}
/* ===== v17: Tarjeta de JUTSU con efecto, stats y animacion ===== */
function jutsuCard(s,compact){if(!s)return '';const catC={debuff:'#ff6b6b',heal:'#6bcb77',stun:'#ffd93d',burn:'#ff9f43',restriction:'#c56cf0',cp_reduce:'#4dabf7',dodge_reduce:'#ff6b9d',internal_injuries:'#e17055',aoe:'#fd79a8',oblivion:'#a29bfe'};
 const cats=(s.cat||[]).map(c=>`<span style="background:${catC[c.replace(/-/g,'_')]||'#888'};color:#000;border-radius:99px;padding:1px 7px;font-size:9px;font-weight:800;margin-right:3px">${c}</span>`).join('');
 return `<div class="jcard ${compact?'jc':''}" style="border-left:3px solid ${s.dmg>300?'#ff6b6b':s.dmg>0?'#ffd93d':'#c56cf0'};background:linear-gradient(135deg,rgba(255,159,67,.08),transparent);border-radius:8px;padding:8px 10px;margin:6px 0">
  <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap"><b style="color:var(--o)">${esc(s.name||'')}</b>${s.anim?'<span class="anim-badge" title="Tiene animacion">🎬 ANIM</span>':''}${s.type?`<span class=m>${esc(s.type)}</span>`:''}</div>
  <div style="display:flex;gap:8px;margin:4px 0;font-size:11px;font-weight:700">${s.cp!==undefined?`<span title="Chakra Points">🔵 CP ${s.cp}</span>`:''}${s.dmg!==undefined?`<span title="Daño">⚔️ DMG ${s.dmg}</span>`:''}${s.cd!==undefined?`<span title="Enfriamiento">⏱️ CD ${s.cd}s</span>`:''}</div>
  ${cats?`<div style="margin:3px 0">${cats}</div>`:''}
  ${s.eff?`<div class=m style="font-size:11px;line-height:1.4;margin-top:4px">💫 ${esc(s.eff)}</div>`:''}
  ${s.weapon?`<div class=m style="font-size:11px;margin-top:4px">🗡️ ${esc(s.weapon)}</div>`:''}
  ${s.back===null?'<div class=m style="font-size:11px;margin-top:2px">🎒 Sin back item esta temporada</div>':s.back?`<div class=m style="font-size:11px;margin-top:2px">🎒 ${esc(s.back)}</div>`:''}
 </div>`}
/* ===== v17: Countdown en vivo para PvE / PvP / CW ===== */
function cdHtml(mode){const A=XT.archive||{},cd=A.cd||{},R=XT.rew||{};let end=0,label='';
 if(mode=='pve'){end=cd.pve_s4||(R.pve&&R.pve.end)||0;label='🏆 PvE S4'}
 else if(mode=='pvp'){end=(R.pvp&&R.pvp.end)||0;label='🥊 PvP S0'}
 else if(mode=='cw'){end=cd.cw||(R.cw&&R.cw.end)||0;label='⚔️ Clan War'}
 if(!end||end<NOW()/1000)return `<span class=m>${label}: fecha no publicada</span>`;
 const d=Math.max(0,end-NOW()/1000);return `<span class="cd-badge" style="background:linear-gradient(90deg,#ff9f43,#ff6b6b);color:#000;font-weight:800;border-radius:99px;padding:3px 12px;font-size:12px">${label} · ${Math.floor(d/86400)}d ${String(Math.floor(d%86400/3600)).padStart(2,'0')}:${String(Math.floor(d%3600/60)).padStart(2,'0')}:${String(Math.floor(d%60)).padStart(2,'0')}</span>`}
function rewCard(m){const r=(XT.rew||{})[m]||{},M=RMODE[m],open=localStorage['rw_'+m]!='0',rows=r.rows||[],lb=m=='pve'?(XT.lb||[])[0]:m=='pvp'?(XT.lb||[])[1]:null;
 /* v17: inyecta jutsu del archive y countdown */
 const A=(XT.archive||{}),jutsu=(m=='pve'&&A.pve_skills&&A.pve_skills['4'])?(A.pve_skills['4'].adv||''):(m=='cw'&&A.cw_skills)?(A.cw_skills['3']||''):'';
 const jutsuHtml=jutsu?jutsuCard(jutsu):'';const cd=cdHtml(m);
 let warn='';if(lb&&r.title&&!r.man){const t=r.title.match(/season\s*(\d+)(?:\D+?round\s*(\d+))?/i);if(t&&(+t[1]!=+lb[0]||(t[2]&&+t[2]!=+lb[1])))warn=`<div class="rwn o">⚠️ La página ya muestra <b>Season ${lb[0]} · Round ${lb[1]}/${lb[2]}</b>: estas recompensas son del anuncio anterior y podrían cambiar.</div>`}
 const nw=(r.newer||[])[0],src=r.man?'<span class="rwsrc man">✏️ editado por ti</span>':rows.length?'<span class=rwsrc>✓ anuncio oficial</span>':'';
 const list=rows.length?rows.map(x=>`<div class=rwr><span class="rwcnd notr">${esc(x.cond)}</span>${x.img?`<img class=rwi src="${esc(x.img)}" referrerpolicy=no-referrer onclick="lbx(this.src)" onerror="this.style.display='none'" alt="">`:`<span class=rwe>${icoFor(x.item)}</span>`}<span style="min-width:0"><b class=notr>${esc(x.item)}</b> ${rwWho(m,x)}</span></div>`).join('')
  :`<p class=m>${m=='cw'?'La web oficial todavía no publica las recompensas de Clan War. Toca <b>✏️ Editar</b> para cargarlas (con imagen) y el tracker marcará qué clanes las están ganando.':m=='raid'?'Todavía no hay recompensas publicadas para este evento.':'Todavía no hay recompensas publicadas para esta ronda.'}</p>`;
 const body=`<div class=rwb>${r.img?`<img class=rwimg src="${esc(r.img)}" alt="${esc(r.title||'')}" loading=lazy referrerpolicy=no-referrer onclick="lbx(this.src)" onerror="this.style.display='none'">`:''}<div class=rwl>${list}${(r.notes||[]).map(x=>`<div class="rwnote m">ℹ️ <span class=notr>${esc(x)}</span></div>`).join('')}${warn}${nw?`<div class="rwn m">📰 Anuncio más nuevo (sin lista de premios): <a href="${esc(nw.url)}" target=_blank rel=noopener>${esc(nw.title)}</a></div>`:''}</div></div>`;
 return `<div class="card rwc" style="--rc:${M[2]};margin-bottom:10px"><div class=rwh><b>🎁 Recompensas ${M[0]} ${M[1]}</b>${r.title?`<span class="m notr">${esc(r.title)}</span>`:''}${endTxt(r)}${src}<span class=rwbt>${r.url?`<a class=sm href="${esc(r.url)}" target=_blank rel=noopener>📰 Anuncio ↗</a>`:''}<button class=sm onclick="rwEdit('${m}')">✏️ Editar</button><button class=sm onclick="rwTog('${m}')">${open?'▴ Ocultar':'▾ Ver'}</button></span></div>${open?body:''}</div>`}
function rwTog(m){try{localStorage['rw_'+m]=localStorage['rw_'+m]=='0'?'1':'0'}catch(e){}force=true;draw()}
function lbx(u){const b=$('#lbx');b.innerHTML=`<img src="${esc(u)}" referrerpolicy=no-referrer alt="">`;b.style.display='flex'}
const cwMine=c=>{const ch=rewChips('cw',c.rank,c.rep);return ch?`<p>🎁 Si la Clan War terminara ahora, tu clan ganaría: ${ch}</p>`:''};

// ---- subir imagenes (se guardan en la carpeta zenshin_img junto al tracker) ----
const upBtn=id=>`<label class="sm upb" title="Subir imagen desde tu PC o celular">📷<input type=file accept="image/*" style="display:none" onchange="upImg(this,'${id}')"></label>`;
function shrink(du,type){if(/gif/.test(type))return Promise.resolve(du);return new Promise(res=>{const im=new Image();im.onload=()=>{const k=Math.min(1,900/Math.max(im.width,im.height));if(k>=1&&du.length<900000)return res(du);const c=document.createElement('canvas');c.width=Math.round(im.width*k);c.height=Math.round(im.height*k);c.getContext('2d').drawImage(im,0,0,c.width,c.height);res(c.toDataURL(/png/.test(type)?'image/png':'image/jpeg',.88))};im.onerror=()=>res(du);im.src=du})}
function upImg(inp,id){const f=inp.files[0];if(!f)return;const rd=new FileReader();rd.onload=()=>{shrink(rd.result,f.type).then(du=>fetch('/upload',{method:'POST',body:JSON.stringify({data:du})})).then(r=>r.json()).then(j=>{if(j.url){$('#'+id).value=j.url;toast('📷 Imagen subida','#3ddc84')}else toast('No se pudo subir: '+esc(j.err||''),'#ff4d4d')}).catch(()=>toast('No se pudo subir la imagen','#ff4d4d'))};rd.readAsDataURL(f)}
const xpost=d=>fetch('/extras',{method:'POST',body:JSON.stringify(d)}).then(r=>r.json()).then(j=>{XV=j.v;return loadX()}).catch(()=>toast('No se pudo guardar','#ff4d4d'));

// ---- editor de recompensas ----
function rwEdit(m){XEDIT=m;draw()}
const rwRowHtml=x=>{const id='ri'+(++UPN);return `<div class=rwedr><input class=f1 placeholder="Rank 1–30" value="${esc(x.cond||'')}" style="width:130px"><input class=f2 placeholder="Premio" value="${esc(x.item||'')}" style="flex:1;min-width:150px"><input class=f3 id=${id} placeholder="Imagen (enlace, opcional)" value="${esc(x.img||'')}" style="width:180px">${upBtn(id)}<button title="Quitar" onclick="this.parentNode.remove()">🗑</button></div>`};
function rwEdHtml(){const m=XEDIT,r=(XT.rew||{})[m]||{},M=RMODE[m],rows=r.rows&&r.rows.length?r.rows:[{},{},{}];
 return `<div class=set><div class=mh><h3 style=margin:0>✏️ Recompensas ${M[0]} ${M[1]}</h3><button onclick="XEDIT=null;draw()">✕</button></div>
 <p class=m style="font-size:12px">Escribe la condición como en el juego: <code>Rank 1–30</code>, <code>Top 1–3</code>, <code>50,000+ Score</code> o <code>1er lugar</code>. Con eso el tracker marca quién va ganando cada premio. Las imágenes pueden ser un enlace o una captura que subas con 📷.</p>
 <label>Título <input id=rwt style="flex:1" value="${esc(r.title||'')}" placeholder="${m=='cw'?'Ej: Clan War Season 4':'Ej: Season 4 – Round 1'}"></label>
 <label>Imagen principal <input id=rwimg style="flex:1" value="${esc(r.img||'')}" placeholder="https://… o súbela">${upBtn('rwimg')}</label>
 <div id=rwrows>${rows.map(rwRowHtml).join('')}</div><button onclick="$('#rwrows').insertAdjacentHTML('beforeend',rwRowHtml({}))">➕ Agregar premio</button>
 <h4>Notas <span class=m style="font-weight:400">(una por línea)</span></h4><textarea id=rwnotes style="width:100%;height:60px">${esc((r.notes||[]).join('\n'))}</textarea>
 <label>Enlace del anuncio <input id=rwurl style="flex:1" value="${esc(r.url||'')}" placeholder="https://…"></label>
 <p style="margin-top:14px;display:flex;gap:6px;flex-wrap:wrap"><button onclick="rwSave()">💾 Guardar</button>${r.man?'<button onclick="rwReset()">↩️ Volver a lo oficial</button>':''}<button onclick="xpost({refresh:1});toast('🔄 Buscando anuncios nuevos en la web…','#ff9f43')">🔄 Buscar en la web</button><button onclick="XEDIT=null;draw()">Cancelar</button></p></div>`}
function rwSave(){const rows=[...document.querySelectorAll('#rwrows .rwedr')].map(d=>({cond:d.querySelector('.f1').value,item:d.querySelector('.f2').value,img:d.querySelector('.f3').value}));
 xpost({mode:XEDIT,rows,img:$('#rwimg').value,url:$('#rwurl').value,title:$('#rwt').value,notes:$('#rwnotes').value.split('\n')}).then(()=>{toast('💾 Recompensas guardadas','#3ddc84');XEDIT=null;force=true;draw()})}
function rwReset(){if(!confirm(T_('¿Borrar tus cambios y volver a las recompensas del anuncio oficial?')))return;xpost({mode:XEDIT,reset:1}).then(()=>{XEDIT=null;force=true;draw()})}

// ---- panel de tokens y revendedores (lateral en pantallas anchas, franja arriba en pantallas chicas) ----
function contactBtn(c){return String(c).split(/[,;\n]+/).map(x=>x.trim()).filter(Boolean).slice(0,4).map(x=>{let u='',ic='👤',t=x;
 if(/^https?:\/\//i.test(x)){u=x;[ic,t]=/wa\.me|whatsapp/i.test(x)?['🟢','WhatsApp']:/discord/i.test(x)?['💬','Discord']:/facebook|fb\.com|m\.me/i.test(x)?['📘','Facebook']:/t\.me|telegram/i.test(x)?['✈️','Telegram']:/instagram/i.test(x)?['📸','Instagram']:/tiktok/i.test(x)?['🎵','TikTok']:['🔗','Enlace']}
 else if(/^\+?[\d\s().\-]{7,}$/.test(x)){u='https://wa.me/'+x.replace(/\D/g,'');ic='🟢';t='WhatsApp '+x}else if(/^@|#\d{4}$/.test(x))ic='💬';
 return u?`<a class=ctb href="${esc(u)}" target=_blank rel="noopener noreferrer">${ic} ${esc(t)}</a>`:`<span class=ctb title="Copiar" data-cp="${esc(x)}">${ic} ${esc(t)}</span>`}).join('')}
function sideHtml(){const s=XT.shop||{},T=s.tok||[],R=s.res||[],P=XT.pkgs||[];
 const tok=(T.length?T:[]).length?`<div style="background:linear-gradient(90deg,rgba(255,215,0,.15),transparent);border:1px solid rgba(255,215,0,.3);border-radius:8px;padding:6px 10px;margin-bottom:8px;font-size:11px"><b>⭐ Ninja Emblem Active</b> <span class=m>— precios premium aplicados</span></div><table class=tkt>${T.map((x,i)=>`<tr class="${x.best?'best-val':''}" style="${x.best?'background:linear-gradient(90deg,rgba(255,215,0,.12),transparent);border-left:3px solid #ffd700':''}"><td>${x.img?`<img class=tki src="${esc(x.img)}" onerror="this.style.display='none'" alt="">`:'💎'} <b>${esc(x.tok)} Tokens</b>${x.pkg?' <span class="pkg-badge" title="Incluye paquete bonus">📦 +Package</span>':''}${x.discount?`<span class="disc-badge">-${x.discount}%</span>`:''}${x.best?' <span class="best-badge">🏆 MEJOR VALOR</span>':''}${x.note?`<div class=m style="font-size:11px">${esc(x.note)}</div>`:''}${x.per1k?`<div class=m style="font-size:10px;opacity:.7">$${esc(x.per1k)}/1k tokens</div>`:''}<td class=num style="white-space:nowrap">${x.old?`<span class=m style="text-decoration:line-through;opacity:.5">$${esc(x.old)}</span> `:''}<b class=up style="font-size:15px">$${esc(x.price)}</b> <span class=m>${esc(x.cur||'USD')}</span><br><button class="buy-btn" onclick="checkout(${i})" style="margin-top:4px;padding:3px 14px;font-size:11px;border-radius:99px;background:var(--o);color:#000;border:none;font-weight:800;cursor:pointer">🛒 Comprar</button></span></tr>`).join('')}</table>`:`<p class=m style="font-size:12px">La web oficial no publica precios. Toca <b>✏️</b> y agrega los paquetes que conoces (ej. 1.000 tokens → 5 USD).</p>`;
 const res=R.length?R.map(x=>`<div class=rsc>${x.img?`<img class=rsl src="${esc(x.img)}" onerror="this.style.display='none'" alt="">`:'<span class=rsl>🛒</span>'}<div style="min-width:0;flex:1"><b class=notr>${esc(x.name)}</b>${x.ok?' <span class=g title="Marcado como confiable por ti">✔ confiable</span>':''}${x.country?` <span class=m>· ${esc(x.country)}</span>`:''}${x.rate?`<div class=notr style="font-size:12px">💱 ${esc(x.rate)}</div>`:''}${x.pay?`<div class="m notr" style="font-size:11px">💳 ${esc(x.pay)}</div>`:''}${x.contact?`<div style="margin-top:3px">${contactBtn(x.contact)}</div>`:''}</div></div>`).join(''):`<p class=m style="font-size:12px">Aún no hay revendedores. Toca <b>✏️</b> para agregarlos con su contacto (WhatsApp, Discord, Facebook…).</p>`;
 const pk=P.map(x=>`<a class=pkc href="${esc(x.url)}" target=_blank rel=noopener>${x.img?`<img src="${esc(x.img)}" loading=lazy referrerpolicy=no-referrer onerror="this.style.visibility='hidden'" alt="">`:''}<span class=notr><b>${esc(String(x.title).replace(/^New Exclusive Top-Up Package\s*[–-]\s*/i,'📦 '))}</b><br><span class=m style="font-size:11px">${esc(x.date||'')}</span></span></a>`).join('');
 return `<div class=sdh><b>💎 Precios de tokens</b> <span class=m>(oficiales)</span><button class=sm onclick="shEdit()" title="Editar precios">✏️</button></div>${tok}${payHtml()}<div class=sdh style="margin-top:14px"><b>🛒 Revendedores oficiales</b><button class=sm onclick="shEdit()" title="Editar revendedores">✏️</button></div>${res}${pk?`<div class=sdh style="margin-top:14px"><b>📦 Paquetes oficiales recientes</b></div>${pk}`:''}<p class=m style="font-size:10px;margin-top:10px">⚠️ Compra solo a revendedores de confianza: según los términos del juego, las compras son finales y no se devuelven.${s.upd?' · Actualizado el '+new Date(s.upd*1000).toLocaleDateString():''}</p>`}
/* ===== v16: NOTICIAS & UPDATES ===== */
function newsHtml(){const N=(XT.news||[]).slice().sort((a,b)=>(b.id||0)-(a.id||0));
 const CB={pve:'🏆 PvE',pvp:'🥊 PvP',cw:'⚔️ CW',raid:'🦊 Raid',pkg:'💎 Paquete'};
 return `<div class=card><div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><b style="font-size:16px">📰 Noticias & Actualizaciones</b><span class=m>Auto: ${N.length} anuncios de la web oficial</span><button class=sm onclick="xpost({refresh:1}).then(()=>toast('🔄 Actualizando…','#ff9f43'))">🔄 Refrescar</button></div></div>`+
 (N.length?N.map(a=>`<div class=card style="display:flex;gap:12px;align-items:flex-start">${a.img?`<img src="${esc(a.img)}" loading=lazy referrerpolicy=no-referrer style="width:120px;height:80px;object-fit:cover;border-radius:8px;flex:none" onerror="this.style.display='none'">`:''}<div style="min-width:0;flex:1"><div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:4px">${(a.cats||[]).map(c=>`<span class=cb>${CB[c]||'📢'}</span>`).join('')}<span class=m>${esc(a.date||'')}</span></div><b><a href="${esc(a.url)}" target=_blank rel=noopener style="color:var(--o)">${esc(a.title||'Sin título')}</a></b>${a.notes&&a.notes.length?`<div class=m style="margin-top:4px">${a.notes.slice(0,2).map(x=>esc(x)).join(' · ')}</div>`:''}${a.prices&&a.prices.length?`<div style="margin-top:6px">${a.prices.slice(0,4).map(p=>`<span class=sst>💲 ${esc(p.price)} ${esc(p.cur||'')}</span>`).join('')}</div>`:''}${a.rows&&a.rows.length?`<details style="margin-top:6px"><summary class=m>🎁 Recompensas (${a.rows.length})</summary><table>${a.rows.map(r=>`<tr><td>${esc(r.cond)}</td><td><b>${esc(r.item)}</b></td></tr>`).join('')}</table></details>`:''}</div></div>`).join(''):'<div class=card><span class=m>Cargando noticias… se actualizan solas cada 15 min. Toca 🔄 Refrescar.</span></div>')}
/* ===== v16: ESTADO DEL SERVIDOR ===== */
function statsHtml(){const S=XT.server||{};const row=(k,v)=>`<tr><td class=m>${k}</td><td style="text-align:right;font-weight:600">${v}</td></tr>`;
 const up=S.uptime?`${Math.floor(S.uptime/3600)}h ${Math.floor(S.uptime%3600/60)}m`:'—';
 return `<div class=card><b style="font-size:16px">📊 Estado del Tracker v16 ✨</b></div><div class=card><table>${row('Versión','v16 (100+ mejoras)')}${row('Activo',up)}${row('Python',esc(S.py||'—'))}${row('Clientes',S.clients||0)}${row('Clanes',S.clans||0)}${row('Noticias',S.news||0)}${row('Hilos',S.threads||'—')}${row('RAM',(S.mem?S.mem+' MB':'—'))}${row('Última lectura',S.last_good?new Date(S.last_good*1000).toLocaleTimeString():'—')}${row('Errores',S.errors||0)}${row('Poll',S.polls+'s')}${row('Puerto',S.port||8000)}${row('KA reuse',((S.ka&&S.ka.reuse)||0)+' / '+((S.ka&&S.ka.new)||0))}${row('LAN',S.lan?`<a href="${esc(S.lan)}" target=_blank>${esc(S.lan)}</a>`:'—')}</table></div><div class=card><b>🔗 Accesos rápidos</b><p style="margin:8px 0">${S.lan?`Celular (misma WiFi): <a href="${esc(S.lan)}" target=_blank><b>${esc(S.lan)}</b></a>`:'—'}</p><button onclick="navigator.clipboard.writeText(location.origin);toast('📋 URL copiada','#3ddc84')">📋 Copiar URL</button> <button onclick="location.href='/health'">❤️ Health</button> <button onclick="xpost({refresh:1}).then(()=>toast('🔄 Refrescando…','#ff9f43'))">🔄 Refrescar</button> <button onclick="alert('✨ TRACKER v16 - 100+ MEJORAS:\n• PvE Season 4 (S3 removida)\n• Precios automáticos\n• Resellers oficiales auto\n• 📰 Noticias & Updates\n• 📊 Panel de estado\n• GZIP + retry backoff\n• /health + favicon\n• Countdown temporada\n• Frescura de datos\n• Atajos teclado R / 1-9\n• Pull-to-refresh + swipe\n• Vibración en alertas\n• Exportar/Importar config\n• Limpieza memoria\n• Headers seguridad\n• Watchdog auto-reconexión\n• PWA + add-to-homescreen\n• Search debounce\n• Y mucho más…')">📝 Changelog</button></div>`}
/* ===== v18: Metodos de pago + Checkout modal ===== */
let CHKOUT=null,SELPay=null;
const PAY_RES={paypal:['Ninjayex'],binance:['Ninjayex','Hardz','blozz17'],midtrans:['INFLUUX'],card:['Ninjayex','Hardz']};
function selectPay(pid){SELPay=pid;const names=PAY_RES[pid]||[];const R=(XT.shop||{}).res||[];const found=R.filter(r=>names.some(n=>r.name&&r.name.toLowerCase().includes(n.toLowerCase())));
 const box=$('#payres');if(!box)return;
 box.innerHTML=found.length?`<div style="margin-top:10px;padding:10px;background:rgba(61,220,132,.1);border:1px solid rgba(61,220,132,.3);border-radius:10px"><b style="color:#3ddc84">✅ Revendedores que aceptan este método:</b>${found.map(r=>`<div style="display:flex;align-items:center;gap:8px;margin-top:6px;padding:6px 8px;background:var(--c);border-radius:8px"><span style="font-weight:800">${esc(r.name)}</span><span class=m>${esc(r.country||'')}</span><span style="margin-left:auto;font-size:11px;color:var(--o)">💬 ${esc(r.contact||'')}</span><button onclick="navigator.clipboard.writeText('${esc(r.contact||'')}');toast('📋 Contacto copiado','#3ddc84')" style="padding:2px 8px;font-size:10px;border-radius:6px;background:var(--o);color:#000;border:none;font-weight:700;cursor:pointer">Copiar</button></div>`).join('')}<p class=m style="margin-top:6px;font-size:10px">Contacta al revendedor por Discord para completar tu compra de forma segura.</p></div>`:'<div class=m style="margin-top:8px">Sin revendedor registrado para este método — usa el oficial del juego.</div>';}
function payHtml(){const P=XT.payments||[];if(!P.length)return '';
 return `<div class=sdh style="margin-top:14px"><b>💳 Métodos de pago</b></div><div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">${P.map(p=>`<div class="pay-card" onclick="toast('${esc(p.name)} · ${esc(p.note)}','${p.color}')" style="background:${p.color}15;border:1px solid ${p.color}55;border-radius:8px;padding:6px 10px;cursor:pointer;flex:1;min-width:90px;text-align:center" title="${esc(p.note)}"><div style="font-size:18px">${p.icon}</div><div style="font-size:10px;font-weight:700;color:${p.color}">${esc(p.name)}</div></div>`).join('')}</div>`}
function checkout(i){const T=(XT.shop||{}).tok||[];const x=T[i];if(!x)return;CHKOUT=x;
 const P=XT.payments||[],B=XT.bonus_pkgs||[];
 const html=`<div class=set style="z-index:9999"><div class=mh><h3 style=margin:0>🛒 Checkout · ${esc(x.tok)} Tokens</h3><button onclick="CHKOUT=null;draw()">✕</button></div>
  <div style="text-align:center;padding:14px 0"><div style="font-size:36px;font-weight:900;color:var(--o)">$${esc(x.price)} <span class=m style="font-size:14px">${esc(x.cur||'USD')}</span></div>${x.old?`<div class=m style="text-decoration:line-through;opacity:.5">Antes $${esc(x.old)}</div>`:''}<div class=m>${esc(x.tok)} Tokens${x.pkg?' + 📦 Paquete Bonus':''}</div></div>
  ${x.pkg&&B.length?`<div class=card><b>🎁 Paquetes bonus disponibles:</b><div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:6px">${B.map(b=>`<span class=cb>${esc(b)}</span>`).join('')}</div><p class=m style="margin-top:6px;font-size:11px">Selecciona tu personaje en el juego para recibir los items bonus.</p></div>`:''}
  <div class=card><b>💳 Elige método de pago:</b><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">${P.map(p=>`<button onclick="SELPay='${p.id}';selectPay('${p.id}');this.style.borderColor='${p.color}'" style="flex:1;min-width:110px;padding:10px;border-radius:10px;border:2px solid ${p.color}44;background:${p.color}15;cursor:pointer;font-weight:700;font-size:12px"><span style="font-size:20px">${p.icon}</span><br>${esc(p.name)}<br><span class=m style="font-size:10px">${esc(p.note)}</span></button>`).join('')}</div></div>
  <div id=payres></div><p class=m style="text-align:center;font-size:11px">🔒 Compra segura · Los tokens se acreditan al instante · Soporte 24/7</p></div>`;
 const ov=document.createElement('div');ov.id='chkoverlay';ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9998;display:flex;align-items:center;justify-content:center;padding:12px';ov.onclick=e=>{if(e.target.id=='chkoverlay'){CHKOUT=null;ov.remove()}};
 const inner=document.createElement('div');inner.style.cssText='max-width:420px;width:100%;max-height:90vh;overflow-y:auto';inner.innerHTML=html;ov.appendChild(inner);document.body.appendChild(ov);}
function renderSide(){const sd=$('#side'),st=$('#sstrip');if(sd)sd.innerHTML=sideHtml();if(!st)return;const T=(XT.shop||{}).tok||[],R=(XT.shop||{}).res||[],op=localStorage.sstrip=='1';
 st.innerHTML=`<div class=ssr><b>💎 Tokens:</b> ${T.length?T.slice(0,4).map(x=>`<span class=sst>${esc(x.tok)} → <b class=up>${esc(x.price)}</b> ${esc(x.cur||'')}</span>`).join(''):'<span class=m>sin precios aún</span>'} <b style="margin-left:6px">🛒 Revendedores:</b> ${R.length?R.slice(0,3).map(x=>`<span class=sst>${esc(x.name)}${x.ok?' ✔':''}</span>`).join('')+(R.length>3?` <span class=m>+${R.length-3}</span>`:''):'<span class=m>ninguno aún</span>'}<span style="margin-left:auto;display:flex;gap:6px"><button class=sm onclick="shEdit()">✏️ Editar</button><button class=sm onclick="try{localStorage.sstrip=localStorage.sstrip=='1'?'0':'1'}catch(e){}renderSide()">${op?'▴ Cerrar':'▾ Ver todo'}</button></span></div>${op?`<div class=ssx>${sideHtml()}</div>`:''}`}
function shEdit(){SHEDIT=true;draw()}
const tkRow=x=>{const id='tk'+(++UPN);return `<div class=rwedr><input class=f1 placeholder="Tokens (ej. 1.000)" value="${esc(x.tok||'')}" style="width:130px"><input class=f2 placeholder="Precio" value="${esc(x.price||'')}" style="width:90px"><input class=f3 placeholder="Moneda" value="${esc(x.cur||'')}" style="width:80px"><input class=f4 placeholder="Nota / bonus" value="${esc(x.note||'')}" style="flex:1;min-width:110px"><input class=f5 id=${id} placeholder="Imagen" value="${esc(x.img||'')}" style="width:120px">${upBtn(id)}<button title="Quitar" onclick="this.parentNode.remove()">🗑</button></div>`};
const rsRow=x=>{const id='rs'+(++UPN);return `<div class=rwedr><input class=f1 placeholder="Nombre" value="${esc(x.name||'')}" style="width:140px"><input class=f2 placeholder="País" value="${esc(x.country||'')}" style="width:100px"><input class=f3 placeholder="Contacto: WhatsApp, enlace de Discord/Facebook…" value="${esc(x.contact||'')}" style="flex:1;min-width:200px"><input class=f4 placeholder="Métodos de pago (Yape, PayPal…)" value="${esc(x.pay||'')}" style="width:200px"><input class=f5 placeholder="Tarifa (ej. 1.000 tokens → S/ 18)" value="${esc(x.rate||'')}" style="width:200px"><input class=f6 id=${id} placeholder="Logo / foto" value="${esc(x.img||'')}" style="width:120px">${upBtn(id)}<label style="display:inline-flex;gap:4px;align-items:center"><input type=checkbox class=f7 ${x.ok?'checked':''}> confiable</label><button title="Quitar" onclick="this.parentNode.remove()">🗑</button></div>`};
function shEdHtml(){const s=XT.shop||{},T=s.tok&&s.tok.length?s.tok:[{},{},{}],R=s.res&&s.res.length?s.res:[{}];
 return `<div class=set><div class=mh><h3 style=margin:0>💎 Tokens y revendedores</h3><button onclick="SHEDIT=false;draw()">✕</button></div><p class=m style="font-size:12px">Se guarda en tu PC (archivo <code>zenshin_extras.json</code>) y se ve igual en el celular. Deja una fila vacía para no usarla.</p>
 <h4>💎 Precios de tokens</h4><div id=tkrows>${T.map(tkRow).join('')}</div><button onclick="$('#tkrows').insertAdjacentHTML('beforeend',tkRow({}))">➕ Agregar paquete</button>
 <h4>🛒 Revendedores</h4><p class=m style="font-size:12px">Contacto: número de WhatsApp (+51 999 999 999), enlace (Discord, Facebook, Telegram…) o usuario. Varios separados por coma.</p><div id=rsrows>${R.map(rsRow).join('')}</div><button onclick="$('#rsrows').insertAdjacentHTML('beforeend',rsRow({}))">➕ Agregar revendedor</button>
 <p style="margin-top:14px;display:flex;gap:6px"><button onclick="shSave()">💾 Guardar</button><button onclick="SHEDIT=false;draw()">Cancelar</button></p></div>`}
function shSave(){const v=(d,c)=>{const e=d.querySelector('.'+c);return e?(e.type=='checkbox'?e.checked:e.value):''};
 const tok=[...document.querySelectorAll('#tkrows .rwedr')].map(d=>({tok:v(d,'f1'),price:v(d,'f2'),cur:v(d,'f3'),note:v(d,'f4'),img:v(d,'f5')}));
 const res=[...document.querySelectorAll('#rsrows .rwedr')].map(d=>({name:v(d,'f1'),country:v(d,'f2'),contact:v(d,'f3'),pay:v(d,'f4'),rate:v(d,'f5'),img:v(d,'f6'),ok:v(d,'f7')}));
 xpost({shop:{tok,res}}).then(()=>{toast('💾 Tokens y revendedores guardados','#3ddc84');SHEDIT=false;draw()})}
/* v16: atajos teclado R=refrescar, /=buscar, 1-9=pestana */
document.addEventListener('keydown',e=>{if(e.target.tagName=='INPUT'||e.target.tagName=='TEXTAREA')return;if(e.key=='r'||e.key=='R'){xpost({refresh:1});toast('🔄 Refrescando…','#ff9f43')}else if(e.key=='/'){e.preventDefault();$('#q').focus()}else if(/^[1-9]$/.test(e.key)){const b=document.querySelectorAll('nav button[data-t]')[parseInt(e.key)-1];if(b)b.click()}});
/* v16: pull-to-refresh + swipe entre pestanas */
let _ptr=0,_swx=0,_swy=0;document.addEventListener('touchstart',e=>{if(window.scrollY==0)_ptr=e.touches[0].clientY;_swx=e.touches[0].clientX;_swy=e.touches[0].clientY});
document.addEventListener('touchend',e=>{const dx=e.changedTouches[0].clientX-_swx,dy=e.changedTouches[0].clientY-_swy;if(_ptr&&dy>80&&Math.abs(dx)<60){xpost({refresh:1});toast('🔄 Refrescando…','#ff9f43')}else if(Math.abs(dx)>80&&Math.abs(dy)<60){const btns=[...document.querySelectorAll('nav button[data-t]')];const cur=btns.findIndex(b=>b.classList.contains('on'));if(cur>=0){const nx=dx<0?Math.min(cur+1,btns.length-1):Math.max(cur-1,0);btns[nx].click()}}_ptr=0;_swx=0;_swy=0});
document.addEventListener('click',e=>{const c=e.target.closest('[data-cp]');if(c){try{navigator.clipboard.writeText(c.dataset.cp)}catch(x){}toast('📋 Copiado: '+esc(c.dataset.cp),'#ff9f43')}});
setTimeout(()=>{try{renderSide()}catch(e){console.error("renderSide:",e)}},0);

/* ===================== 🦊 EVENTO KYUBI · Tailed Beast Raid (ranking de Event Points) ===================== */
const KYI={"fox_adv": "data:image/jpeg;base64,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", "fox_std": "data:image/jpeg;base64,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", "ep": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEgAAAAsCAIAAABJ6mlcAAAN2ElEQVR42t1aSYxdx3U991a94U/9f09kDyTFQRQlNkVRkiPLkmVZkAxnMLIxJG8DZFgkyCqLbJJd9tl4nW2SheEYMAQYUBwDdqBIoihTokWKlMSpJ5I9/Om9/4aqe7P43WS32JwUSgj8UEBPr6rr1L333HNvfXrjxD48yKMAFAoFQAAR7fyaQqHMzMwAmJmIRERVAYj3+Iof+wCQFCAYgmEyzEzkRQonoroVngKGYA0bNjCsZAUoi1y8D8LQGOu9B/4/ATNMALxq5sWLlIpB6fu5cyK8HVjAqAa2HoX1aiW01qjEzZaxtshzcZ6IxDsRuZO1vz5gospEhqCqnaxY7OUrad7OXOIFSjc9FBi6J6AaGBpvVPdPBtMNWzfy6Nzx8anZMyf/Z+3aUhBGzhUiAujG+18/MFE1RFVrSi8LvezTbpYOykpZWudnFA2VlsAohEAEJYBBQZCbsAgiiqvGVnpRozrWnHjs+N6pqevzl1yWkglEhCgTEVVR0S3n8nUBM8TM6DtZ7mcXV/rrnXQX8HiIWcKMwV6L2QAhk1MwGzFGrUUQpOA2B+u12nwcdhujM0eOTx86Wg1gq42wNhJYW+a5qnrx4r16L+KHpPJ1AFNAREZCmzn9ZKX3u9VkV1n+VQXPRLBAVMAMUDMYmd5tKlUpHBuGtbCBiuRrq620O1OvHIiq1KqOHTowd2xutdtZK7nvMN2opFFUupLVqPfOOS0BEVX5yoGJwjIqgVnM/Xw7tZ3+d1z5VIQ/qvGMkwwIjuyrP/+d6vFneWoKQUiiIIJ4dc5nmV+57jttEaEwrMzuqzz5zOj0lK3WnnzuxZWZ2Rr5kbGF9sqN9upqZ/W6d46ZvXwtFjMMBbqlXFnr+3bvFbjXGpiyNCCaLymeGNn1wiuTf/43zaf/AN6rd2QsALDZynPl2lq5voIgCFpjGoRjY6Pff/XVywsLi0uLtdGxWuOKMbbIB2mvJ+IfepjZnTxQR0Kzlrv3lzv7k/QH1j9mMGbIexVI1Ko1jh6rHTzcP31q7a03/WAAKEQBxLP7qo8diaZm6kfmAARjY5KlxfVlEjWVCtug7oqwLFMn65kvhEbGx6NKZenq5evzV1Q8MT/ESLO3eSDFlhcG5fxaMtNPXiV5IYAhJIBTWGhA3nc73ffezm9cSz47r64EMURAFE3Nxo/sj3dP1w4/Xj/2VO3xOWLmWp2rNQ5C126vvfMbfP6Z6w7evXQ1SzuPVDkOrHqvePi0vw0YAwoMvL+0lgRr3TcieTpADpCCoCEDgE8G/TNneh+eIQabm1OJmMurl4qrF7tBRGFUf3xu8o//tD73VDixC96Lc/nSwvrbv7bnPx7xvHp5+ZPBYLlqZ+o2ZLAx6vXhEqPdopg0tqZT+nOr/fFe8pqVJwwiwkB3YEz1IAUTNqCRwns47z2KzCklOPOB63XCiV9E0zP1I3Mjz37TdzvltWVZWWna4HnXX+j2z/ZNhRtTteCrz2OErHRJJ33BlS/XKIAmAN8SFBvfccg24hLa8Zoq/MY7KkSVkCcMGaIsz5ML5wafXwiao9nli/nyIowpV67neRaV+Tcl/21ZnHO0moRN+5VIK3tTCBFRt5AsKw6LO8yoMEoF9IvOrwCJKGRdcNlhReCwYbcSOs5yPMAogYgpjjmOQciXFlynrUSuvVoMMvaukSZHnDtd8tKqVlj2jFQEGKaMhwlMFcxkCKuDIkmLbxjsM+yhuiMqAECmuOpw3sMpzKYnCZAqCsHRAHsDdWXpieC9SxKsrYIAVRUpy9J7PxfgeZWfp9n1rLK/iVx1KN8evisy0C18muSjpa9HKAS0E1cZoACWPZYFqcIADMim2TPFFaWGR8topB5F6Zz7wiLOew/sC/CM4peZ5k4Mc8zIMeTHh0OQQ2BKICIqvc+9UEg2AO0U0MNaywmWBF1FuE3SgwEPFKJrgjWPSaPWO/E7FnYaMfYynjW4UpZLSV4LbEAPk/PtxsYIIEJZchRFh/aFfkDLC3AOt1EwAQqUioHAAzVDAUiGC6gmhS6XmFRUIxioqO64Vwb6ijHG6zX8W5r91yU/XguPjlV21WOAShFRyP2xP92BeuxNUyhQYWglSid20+xUa/lq7/T7RTu3VYLITVQOCIC9BqniqseNXDfrfRhGVIuP7Bl9zKW1XocYQrSj5QnIFRHhcIhvl3554OfL4iPvJzK/qxI2Qw6NiQ2DcCd0QziiWoqI7MA6Zm6qCRqaDKLiBU7N0e9+79gzT3c+eC9Z7AcNg02FSoAHAsI4IyYkijaQWfKGvCGydGD35B8ePXyQpLixBgYx3ylHDf+QKfYHOBbCqX6WlPNJ7kWH/4WJPdSJOt1piDpRrwrszKV2w7tUnepEs57l6yfPf/4aB5PPf/vqSFOwzJa8u8WHQ1ckoknV56r2xPReNJtkLBFJ0q+m/eblT6XX54AAxV3dSTdKPuyz+AGwn3Ha+fPd5KN+5pgrka0GbIhvW0IJWjgFoVmtHGxGk3GQOXGiTDuxoqjWgiC23E3Sc6fevzp3uPriK9W17uDqUhTBREacH8ITIAcssMegHiAMAAsCXIBk0Otcb4vCVgiCu0uKIev2FQFwyGLS0LjTkUwuFtIR9LPcGAqZdOsMVWJia2OQLwq33l8c1IuJkdE4CJjclpC2WyaRK8tqtfLITOt3v/7Pn4T0J6+/MVEU53/8L2wQRbS1rUGqsBiUbnDp4i1XUAXAIXgoqO/vCQACeoJV0UjxrQjfjZEK5gUD1WCr0YnUKVsTT7R2HThIImc/PP2rG/35Qk5MN3dXQye3coWZm2puzoKIWmMa1fjC54sr/eTll7/TbNQvfXqhv7bunHJsWcVu+rQCqhAPcRAP8VABCER0TwWx9Yws4BVrHh0FgJhQIUSEOmGUMM4YZYwxxhjjTKOKicg8cuDAC3/2F8/98EcNwucXL33W7hOjFoeRYRCpgmgLsOEpG6ZqYNbyrJ1k1WQwOz09+djB7uq11aXVshRvSA2LKBEYsAw2ZAJmS8YSGSLG/Sha3hzDzNEVtBUFAKAAUkEOMBAQmGEIBjBM1rDkiCp2z4svPfW3fzf2xNzKBydPnjp1qZ+y5VYtrgVm6DdE20UwEVS0FHdwdtfS8sq//vRncRT86PUfFlme5j9tX7jUK1EhqnpUGfHG5C9TbtycUAjagu4tJQ0DmE17fyFIlbgsfH2kOf3SK9XxyfULn5z6xZufLq/4KGzVomrAG6mPtrviTQEhqpUwMExrWfbJxcVu0n/qxZcOnTje73SWLsynAzU1WzCnxANFvomLCIZgCbTFIF8YhsAED2SCRNFTdASJosQOSkpvZxqmfKBTz5741j/+U+/GtTf/4e/f/s1/n8ud1OKD4/WxOHCiN3vu24BhS7SFYVAba168uvjx+bN2YnetNT5Sr0xPjQZKvfZKP5Ekl8yrYwhTCSoUJeAUDnBAuX0Mf5MrckWq6Av6gkSRAbJZHOzImRuDWb2q18busf3f+/748afP/OTf/+Off/xe5nuVYKpZmWnEkWEnt5rtOwAbEjqpBtCxVr2veOud98+89+7egwde+Mu/ro3WF95/R0tvLNhCCQVooEgVPUFfkClSRbJ9pIq+oiMbJioAt1kr3I8+JGZx6gtMPXti5PCRj3/51rs//9l7SyungGqzenS8HlvjdVvZs5PFtoR4JQ7DwLisv7yenF1pX7ix5vPBgf37jzz3jakn56gSt+cX+z2IwAk8YXjd4LYYym3/0W/Kt7uoeL2D3UAoSa8tLJw9+c6vLlz5rSJuVB4dr+2qxUTw2xfcGRhtxq73vh4GeydHqRKdvbxw8vRHN1ZWKvsO0egYqjVSDYvBSKU6MlaLLAXqjerNnen2Mdyc2brRHbr+2ziTN+d6hTByg8vXeqeuXPuwk54D+Vp0dKI+24jBfPtJ0T3vxzY1FJLcLXb6l7vFIJdZY+Za1af3zBzbt2esMZL1ut3LF3vzV/IkVQBM2OTKB6pE6LaIyBXZcAAdxYLDuQKXgbFG9MSu5kQtDIzxO9UQ975tGUqI0PB4LQ4DGwT9pZVOOyneXRqc7Q8mVnsz9epkmYfdNqVFQ7DbUEQAyBIIKor76Yby0GkVCjigAAqBEBVAT3HD6bzDokeHgFp0YKS6pxFNxIE1fKe2ON3njeYwRVjmyBqneq2fL/YGS510vZ9HwDQwahAyasBuQosQESJCzIgIljZy1F3SmioKoFQASBTrglQwGHIPoadYJ06ZTWhnm5XD4/VqYNPS36wlv/zF3/BqT1Vz5xVoxXYkrD86Wk1L107yJHcZsFr4tTTPCmkATYIF6oxJgxYjvmt1XCoK3WgKEdARrCkyQUexrhCDahhMNuJDI5WROAiYmSj3cnfdZh9IMcjQNQkBkQ3YsG1VwmYc9UvvVTPnW7nrl16cwPtctFuUi6WPvdp7Les2szwUxhoODDHVQtsKrDEcW9OKbSsKYsteUXrx269R/6/AaDPAnWgpMnQESxiPLBGIAkOkirR0SekLL+uDYj1zTrS417K8WcWqaj2yjTAILbXiYDQODZFX9apONC39sFPI99LZFl/qIdrm2x4KBSk5KADDXI9YFc043LslAdwPHyrAREwgwBCVouXNTypsRsRDvly/m39uJF1V6PBEDQ3PlQ09cD9t2MlRhah6kc3i/cEaxg8BGG35ctOMfnjKovrgfXnautyX7X8/BGB3R0v4Cj/zcPfE+Pv5/N4C+1/UJLXp+wVfigAAAABJRU5ErkJggg=="};
let KYR=null,KYBUSY='',kySort='rank',KYLAST=null,KYDO=false;
/* v19: Kyubi EN VIVO (auto, sin capturas) */
function kyubiLiveHtml(){const K=XT.kyubi_live||{},rows=K.rows||[];
 const endTs=K.end?new Date(K.end+" 23:59:59").getTime()/1000:0;const left=endTs?Math.max(0,endTs-NOW()/1000):0;
 const cdTxt=left?`${Math.floor(left/86400)}d ${String(Math.floor(left%86400/3600)).padStart(2,"0")}:${String(Math.floor(left%3600/60)).padStart(2,"0")}:${String(Math.floor(left%60)).padStart(2,"0")}`:"—";
 const ptsInfo=`<div style="display:flex;gap:10px;flex-wrap:wrap;margin:8px 0"><div style="flex:1;min-width:120px;background:linear-gradient(135deg,rgba(255,80,80,.15),transparent);border:1px solid rgba(255,80,80,.3);border-radius:10px;padding:8px;text-align:center"><div style="font-size:24px">🟥</div><b style="color:#ff5050">Mostruo Rojo</b><div style="font-size:20px;font-weight:900">+300 pts</div></div><div style="flex:1;min-width:120px;background:linear-gradient(135deg,rgba(120,180,255,.15),transparent);border:1px solid rgba(120,180,255,.3);border-radius:10px;padding:8px;text-align:center"><div style="font-size:24px">🗡️</div><b style="color:#78b4ff">Kunai</b><div style="font-size:20px;font-weight:900">+100 pts</div></div></div>`;
 const rewards=`<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px"><div style="flex:1;min-width:130px;background:linear-gradient(135deg,rgba(255,215,0,.12),transparent);border:1px solid rgba(255,215,0,.35);border-radius:10px;padding:8px"><b style="color:#ffd700">🏆 Top 1-30</b><div class=m style="font-size:11px;margin-top:2px">Advanced Kyubi Chakra Yoroi Jutsu</div><div class=m style="font-size:10px">+100,000 pts mínimos</div></div><div style="flex:1;min-width:130px;background:linear-gradient(135deg,rgba(150,100,255,.12),transparent);border:1px solid rgba(150,100,255,.35);border-radius:10px;padding:8px"><b style="color:#b48cff">🥈 Top 31-100</b><div class=m style="font-size:11px;margin-top:2px">Kyubi Chakra Yoroi Jutsu</div><div class=m style="font-size:10px">+50,000 pts mínimos</div></div></div>`;
 if(!rows.length)return `<div class=card><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><b style="font-size:16px">🦊 Kyubi · Tailed Beast Raid</b><span class="cd-badge">⏳ Termina: ${cdTxt}</span><span class="cb" style="background:#3ddc84">AUTO · SIN CAPTURAS</span></div>${ptsInfo}${rewards}<p class=m style="margin-top:8px">${K.err?esc(K.err):'Cargando ranking en vivo… se actualiza solo. Sin necesidad de capturas.'}</p></div>`;
 const top=rows.slice(0,15),max=Math.max(...rows.map(r=>r.pts||1),1);
 return `<div class=card><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><b style="font-size:16px">🦊 Kyubi EN VIVO · ${esc(K.round||'Round 2')}</b><span class="cd-badge">⏳ ${cdTxt}</span><span class="cb" style="background:#3ddc84">✓ AUTO · SIN CAPTURAS</span><span class=m>${rows.length} jugadores</span></div>${ptsInfo}<div style="margin-top:8px">${top.map((r,i)=>`<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--l);${i<3?'background:linear-gradient(90deg,rgba(255,215,0,'+(0.12-i*0.03)+'),transparent);border-radius:6px;padding-left:6px':''}"><span style="width:30px;font-weight:900;font-size:14px;color:${i<3?'#ffd700':'var(--m)'}">${i<3?['🥇','🥈','🥉'][i]:'#'+r.rank}</span><span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700;font-size:13px">${esc(r.name)}</span><div style="width:32%;height:16px;background:var(--l);border-radius:8px;overflow:hidden"><div style="width:${(r.pts/max*100).toFixed(1)}%;height:100%;background:linear-gradient(90deg,#ff9f43,#ff6b6b,#c2410c);border-radius:8px;animation:kyglow 2s ease-in-out infinite"></div></div><span style="font-weight:900;font-variant-numeric:tabular-nums;min-width:75px;text-align:right;font-size:13px;color:${r.pts>=100000?'#ffd700':r.pts>=50000?'#b48cff':'var(--t)'}">${(r.pts||0).toLocaleString()}</span></div>`).join('')}</div>${rewards}<style>@keyframes kyglow{0%,100%{filter:brightness(1)}50%{filter:brightness(1.3)}}</style></div>`}
const kyNorm=s=>String(s||'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'').replace(/[^\p{L}\p{N}]/gu,'');
function lev(a,b){if(a==b)return 0;const m=a.length,n=b.length;if(!m||!n)return m||n;let p=Array.from({length:n+1},(_,j)=>j);for(let i=1;i<=m;i++){const c=[i];for(let j=1;j<=n;j++)c[j]=Math.min(p[j]+1,c[j-1]+1,p[j-1]+(a[i-1]==b[j-1]?0:1));p=c}return p[n]}
const simil=(a,b)=>1-lev(a,b)/Math.max(a.length,b.length,1);
const kySnaps=()=>(((XT||{}).kyubi||{}).snaps||[]).slice().sort((a,b)=>a.t-b.t);
function kyEnd(){const s=kySnaps().filter(x=>x.end).pop();if(s){const d=Date.parse(s.end+' 23:59:59');if(!isNaN(d))return d/1000}const r=((XT.rew||{}).raid||{});return r.end?r.end+86399:0}
// tablero: une todas las capturas (cada jugador con su historial de puntos)
function kyBoard(){const P=[];
 /* v19: si hay datos en vivo auto, mostrarlos arriba */
 for(const s of kySnaps()){const U=new Set();for(const [r,n,p] of s.rows||[]){const k=kyNorm(n);let x=P.find(y=>y.k==k&&!U.has(y));
  if(!x&&k.length>=3){let best=null,bs=0;for(const y of P){if(U.has(y))continue;const v=simil(y.k,k);if(v>bs&&v>=.72&&p>=y.p-1){bs=v;best=y}}x=best}
  if(!x)x=P.find(y=>!U.has(y)&&y.p==p&&Math.abs(y.obs[y.obs.length-1][2]-r)<=2&&simil(y.k,k)>=.4);   // mismo puntaje y puesto: el mismo jugador mal leído
  if(!x){x={k,n,obs:[],p:0};P.push(x)}U.add(x);x.obs.push([s.t,p,r]);if(p>=x.p){x.p=p;x.n=n}}}
 const now=NOW(),end=kyEnd();
 P.forEach(x=>{const o=x.obs,l=o[o.length-1];let pv=null;for(let i=o.length-2;i>=0;i--)if(o[i][0]<l[0]-1){pv=o[i];break}
  x.t=l[0];x.rr=l[2];x.dp=pv?x.p-pv[1]:0;x.dt=pv?l[0]-pv[0]:0;x.pr=pv?pv[2]:null;
  const ref=o.find(q=>q[0]>=l[0]-86400&&q[0]<=l[0]-600);x.rate=ref&&l[1]>ref[1]?(l[1]-ref[1])/((l[0]-ref[0])/3600):0;
  x.span=ref?l[0]-ref[0]:0;x.proj=end&&x.rate&&x.span>=6*3600&&end>now?Math.round(x.p+x.rate*(end-now)/3600):0;
  x.g24=(()=>{const q=o.find(z=>z[0]>=now-86400);return q?x.p-q[1]:0})()});
 P.sort((a,b)=>b.p-a.p||a.rr-b.rr);P.forEach((x,i)=>x.rank=i+1);return P}
function kyCut(B,n,key){const s=kySnaps().filter(x=>x[key]).pop(),fromB=B.length>=n?B[n-1].p:0;return s&&(!fromB||s.t>=B[n-1].t-60)?s[key]:fromB||(s?s[key]:0)}
function kyFeed(B){const F=[];B.forEach(x=>{for(let i=1;i<x.obs.length;i++){const a=x.obs[i-1],b=x.obs[i];if(b[1]>a[1])F.push({t:b[0],n:x.n,d:b[1]-a[1],p:b[1],r:b[2],o:a[2]})}});return F.sort((a,b)=>b.t-a.t).slice(0,50)}
function kyCheck(){const S2=kySnaps(),last=S2[S2.length-1];if(!last)return;if(KYLAST===null){KYLAST=last.t;return}if(last.t<=KYLAST)return;KYLAST=last.t;
 const B=kyBoard(),G=B.filter(x=>x.t==last.t&&x.dp>0).sort((a,b)=>b.dp-a.dp);
 G.slice(0,3).concat(G.filter(x=>favp.has(x.n)).slice(0,3)).filter((x,i,a)=>a.indexOf(x)==i).forEach(x=>toast(`<div class=tb1>🦊 <b class=notr>${esc(x.n)}</b> <span class=up>+${L(x.dp)} EP</span></div><div class=tl>Event Points <b>${L(x.p)}</b> · <b>#${x.rank}</b>${x.pr&&x.pr!=x.rr?(x.rr<x.pr?' <span class=up>▲'+(x.pr-x.rr)+'</span>':' <span class=dn>▼'+(x.rr-x.pr)+'</span>'):''}</div>`,'#ff7a1a','ky|'+x.k,favp.has(x.n)?1:0));
 if(G.length)chime()}

// ---- pestaña ----
function kyHtml(){const B=kyBoard(),now=NOW(),end=kyEnd(),q=$('#q').value.toLowerCase(),S2=kySnaps(),last=S2[S2.length-1],c30=kyCut(B,30,'c30'),c100=kyCut(B,100,'c100'),F=kyFeed(B);
 const cutS=S2.filter(x=>x.c30),cutD=cutS.length>1?cutS[cutS.length-1].c30-cutS[cutS.length-2].c30:0,top=B[0],gain=[...B].filter(x=>x.t==(last&&last.t)).sort((a,b)=>b.dp-a.dp)[0];
 const left=end?end-now:0,dl=left>0?`${Math.floor(left/86400)}d ${Math.floor(left%86400/3600)}h`:'';
 const tiles=[['⏳ Termina',end?`${new Date((end-86399)*1000).toLocaleDateString([],{day:'2-digit',month:'short'})}${dl?' · <span class=o>'+dl+'</span>':' · <span class=dn>terminó</span>'}`:'–'],['👥 Jugadores leídos',B.length||'–'],['🥇 Líder',top?`<span class=notr>${esc(top.n)}</span><br><span class=up style="font-size:12px">${L(top.p)} EP</span>`:'–'],['🎯 Corte Top 30',c30?L(c30)+(cutD?` <span class=up style="font-size:11px">+${L(cutD)}</span>`:''):'–'],['🎯 Corte Top 100',c100?L(c100):'–'],['⚡ Más subió',gain&&gain.dp?`<span class=notr>${esc(gain.n)}</span><br><span class=up style="font-size:12px">+${L(gain.dp)}</span>`:'–'],['📸 Última captura',last?'hace <span data-ts="'+last.t+'">'+ago(last.t)+'</span>':'–']].map(([a,b])=>tile(a,b)).join('');
 let R=B.filter(x=>x.n.toLowerCase().includes(q));if(kySort=='dp')R=[...R].sort((a,b)=>b.dp-a.dp||a.rank-b.rank);if(kySort=='rate')R=[...R].sort((a,b)=>b.rate-a.rate||a.rank-b.rank);
 const chips=[['rank','# Puesto'],['dp','⚡ Subida'],['rate','Ritmo']].map(([x,t])=>`<span class="fchip ${kySort==x?'on':''}" data-ks="${x}">${t}</span>`).join('');
 const rows=R.map(x=>{const up=B[x.rank-2],need=c30&&x.rank>30?c30-x.p+1:0,fresh=last&&x.t==last.t&&x.dp>0&&now-x.t<1800,mv=x.pr&&x.pr!=x.rr?(x.rr<x.pr?`<span class="ra up" title="Subió del #${x.pr} al #${x.rr}">${IC.tu}</span>`:`<span class="ra dn" title="Bajó del #${x.pr} al #${x.rr}">${IC.td}</span>`):'<span class=ra></span>',fv=favp.has(x.n);
  return `<tr class="${fresh?'pfl':''} ${fv?'mine':''}"><td class=rk>${mv}<b>${x.rank}</b><td><span class="star ${fv?'on':''}" data-fp="${esc(x.n)}" title="Seguir a este jugador">★</span> ${rkIcon(x.rank)} <b class=notr>${esc(x.n)}</b> ${fresh?'<span class=lvp>🦊 SUBIENDO</span>':''}<td class=num><b>${L(x.p)}</b>${KYI.ep?` <img src="${KYI.ep}" alt="" style="width:20px;vertical-align:-4px">`:''}<td class="num ${x.dp?'up':'m'}">${x.dp?'+'+L(x.dp):'–'}<td class="num ${x.g24?'o':'m'}">${x.g24?'+'+L(x.g24):'–'}<td class="num m">${x.rate?L(Math.round(x.rate))+'/h':'–'}<td class="num m">${x.proj?'≈ '+L(x.proj):x.rate?'<span title="Hace falta ver al jugador durante al menos 6 h">…</span>':'–'}<td class="num m">${up?L(up.p-x.p):'👑'}<td class=m>${need>0?'faltan '+L(need):x.rank<=30&&c30?'dentro':'–'}<td>${rewChips('raid',x.rank,x.p,1)}<td class=m style="font-size:11px">hace <span data-ts="${x.t}">${ago(x.t)}</span></tr>`}).join('');
 const feed=F.map(e=>`<div class=fi2>🦊 <b class=notr>${esc(e.n)}</b> <span class=up>+${L(e.d)}</span> <span class=m>→ ${L(e.p)} · #${e.r}</span>${e.o&&e.o>e.r?` <span class=up>▲${e.o-e.r}</span>`:''}<span class=m style="float:right;font-size:11px"><span data-ts="${e.t}">${ago(e.t)}</span></span></div>`).join('')||'<p class=m>Cuando subas dos capturas en momentos distintos verás aquí quién hizo puntos.</p>';
 const hist=cutS.length>1?`<div class=card style="margin-top:10px"><b>📈 Corte del Top 30 en el tiempo</b>${kySpark(cutS)}</div>`:'';
 const help=`<div class="card kyhelp"><b>📸 Cómo actualizar el ranking</b><ol style="margin:6px 0 0 18px;padding:0;font-size:13px"><li>En el juego abre el evento → <b>Ranking List</b> → <b>Rank 1 - 100</b>.</li><li>Toma captura de pantalla de cada página (1/10, 2/10…). La del botón “Rank 1 - 100” trae además los cortes del puesto 30 y 100.</li><li>Pégalas aquí con <b>Ctrl+V</b>, arrástralas o toca <b>📸 Agregar capturas</b>. El tracker lee rango, jugador y puntos; tú revisas y guardas.</li><li>Cada vez que subas capturas nuevas se calcula cuántos puntos hizo cada uno, quién subió de puesto, su ritmo por hora y si llega a la recompensa.</li></ol></div>`;
 return rewCard('raid')+`<div class=mh><h3 style=margin:0>🦊 Kyubi · Tailed Beast Raid <span class=m style="font-weight:400;font-size:12px">ranking de Event Points</span></h3><div class=mbtn><button class=kybtn onclick="kyPick()">📸 Agregar capturas</button><button onclick="kyOpen(null)">✍️ Escribir a mano</button><button onclick="kyExport()" title="Descarga datos.json para publicarlo en la página web">🌐 Exportar para la web</button><button onclick="kyClear()" title="Cuando empiece una ronda nueva (los puntos se reinician)">🗑 Nueva ronda</button></div></div>`+
  (KYBUSY?`<div class="card kybusy"><span class=lgspin>${GLOBE}</span> <b>${esc(KYBUSY)}</b></div>`:'')+
  `<div id=kydrop class=kydrop>🖼️ Suelta aquí las capturas del ranking o pégalas con <b>Ctrl+V</b></div><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:10px 0">${tiles}</div>`+
  (B.length?`<div class=mh><span></span><div>${chips}</div></div><div class=kyg><div class=wrap><table class="mt kyt"><tr><th>#<th>Jugador<th class=num>Event Points<th class=num title="Puntos ganados desde la captura anterior">Subió<th class=num>24 h<th class=num title="Event Points por hora">Ritmo<th class=num title="Puntos estimados al terminar el evento si sigue a este ritmo">Proyección<th class=num title="Puntos que le faltan para alcanzar al de arriba">Al de arriba<th>Top 30<th>Premio<th>Visto</tr>${rows}</table></div><div class="card kyfeed"><b>📡 Quién hizo puntos</b>${feed}</div></div>${hist}${kySnapList(S2)}`:help)}
function kySpark(S2){const v=S2.map(s=>s.c30),t=S2.map(s=>s.t),mn=Math.min(...v),mx=Math.max(...v),r=mx-mn||1,t0=t[0],ts=t[t.length-1]-t0||1;
 return `<svg viewBox="0 0 300 70" width="100%" height="70"><polyline fill=none stroke="#ff7a1a" stroke-width="2" points="${S2.map((s,i)=>((s.t-t0)/ts*296+2).toFixed(1)+','+(64-(s.c30-mn)/r*56).toFixed(1)).join(' ')}"/></svg><div class=m style="font-size:11px;display:flex;justify-content:space-between"><span>${L(mn)}</span><span>${L(mx)}</span></div>`}
function kySnapList(S2){return `<details class=card style="margin-top:10px" ${KYDO?'open':''} ontoggle="KYDO=this.open"><summary><b>📸 Capturas guardadas (${S2.length})</b></summary>${S2.slice().reverse().map(s=>`<div class=hb2><div class=r1><span class=m style="width:150px;flex:none">${new Date(s.t*1000).toLocaleString([],{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}</span><span class=nmx>${s.rows.length} jugadores${s.rows.length?' (#'+Math.min(...s.rows.map(r=>r[0]))+'–#'+Math.max(...s.rows.map(r=>r[0]))+')':''}${s.c30?' · corte 30: '+L(s.c30):''}${s.c100?' · corte 100: '+L(s.c100):''}</span><button class=sm onclick="kyDel(${s.t})" title="Borrar esta captura">🗑</button></div></div>`).join('')}</details>`}

// ---- lectura de capturas (OCR en el navegador con Tesseract.js) ----
let TESS=null,TESSP=null;
function tessLoad(){if(window.Tesseract)return Promise.resolve();return new Promise((ok,no)=>{const s=document.createElement('script');s.src='https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js';s.onload=ok;s.onerror=()=>no(new Error('No se pudo cargar el lector de imágenes. Revisa tu conexión a internet.'));document.head.appendChild(s)})}
function tessWorker(){if(TESS)return Promise.resolve(TESS);if(!TESSP)TESSP=tessLoad().then(()=>Tesseract.createWorker('eng')).then(w=>TESS=w).catch(e=>{TESSP=null;throw e});return TESSP}
async function ocr(cv,p){const w=await tessWorker();await w.setParameters(Object.assign({tessedit_pageseg_mode:'6',tessedit_char_whitelist:''},p||{}));const r=await w.recognize(cv);return(r&&r.data&&r.data.text)||''}
const KYW1=(r,g,b)=>r>160&&g>160&&b>160&&Math.max(r,g,b)-Math.min(r,g,b)<60,KYW2=(r,g,b)=>r>190&&g>190&&b>190&&Math.max(r,g,b)-Math.min(r,g,b)<60,
 KYCY=(r,g,b)=>(b>170&&g>130&&r<140)||(r>200&&g>200&&b>200),KYGR=(r,g,b)=>g>180&&r<150&&b<170,KYRD=(r,g,b)=>r>180&&g<90&&b<90;
function kyMask(img,fn,o){o=Object.assign({x0:0,x1:1,y0:0,y1:1,scale:3,blur:0},o||{});const W=img.naturalWidth||img.width,H=img.naturalHeight||img.height,sx=Math.round(W*o.x0),sw=Math.max(1,Math.round(W*(o.x1-o.x0))),sy=Math.round(H*o.y0),sh=Math.max(1,Math.round(H*(o.y1-o.y0)));
 const c=document.createElement('canvas');c.width=sw;c.height=sh;const g=c.getContext('2d');g.drawImage(img,sx,sy,sw,sh,0,0,sw,sh);const d=g.getImageData(0,0,sw,sh),a=d.data;
 for(let i=0;i<a.length;i+=4){const v=fn(a[i],a[i+1],a[i+2])?0:255;a[i]=a[i+1]=a[i+2]=v;a[i+3]=255}g.putImageData(d,0,0);
 const k=Math.min(o.scale,4000/Math.max(sw,sh)),out=document.createElement('canvas');out.width=Math.round(sw*k);out.height=Math.round(sh*k);const go=out.getContext('2d');go.fillStyle='#fff';go.fillRect(0,0,out.width,out.height);go.imageSmoothingEnabled=true;go.imageSmoothingQuality='high';if(o.blur)go.filter='blur('+o.blur+'px)';go.drawImage(c,0,0,out.width,out.height);return out}
const KYMAP={O:'0',o:'0',D:'0',s:'8',S:'8',e:'6',i:'1',I:'1',l:'1','|':'1'};
function kyRows(text){const out=[];String(text).split('\n').forEach(l=>{const m=l.match(/^[^\p{L}\p{N}]*(\d{1,3})[\s.,)\]]+(.+?)\s+([0-9OoDsSeiIl|]{4,7})(?=[^\p{L}\p{N}]|$)/u);if(!m)return;const p=+m[3].replace(/./g,ch=>KYMAP[ch]||ch).replace(/\D/g,''),r=+m[1];
 const n=m[2].replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu,'').trim();if(r>=1&&r<=1000&&p>0&&n)out.push([r,n,p])});return out}
function kyLoadImg(f){return new Promise((ok,no)=>{const u=URL.createObjectURL(f),im=new Image();im.onload=()=>ok(im);im.onerror=()=>no(new Error('imagen no válida'));im.src=u})}
async function kyRead(files){files=[...files].filter(f=>/^image\//.test(f.type));if(!files.length||KYBUSY)return;const M={},res={c30:0,c100:0,end:'',me:0,t:0},lm=Math.min(...files.map(f=>f.lastModified||0));if(lm&&lm/1e3>Date.now()/1e3-7*86400&&lm/1e3<Date.now()/1e3+60)res.t=lm/1e3;
 try{KYBUSY='Cargando el lector de imágenes… (la primera vez tarda unos segundos)';draw();await tessWorker();
  for(let i=0;i<files.length;i++){KYBUSY=`Leyendo captura ${i+1} de ${files.length}…`;draw();const img=await kyLoadImg(files[i]);
   const A=kyRows(await ocr(kyMask(img,KYW1,{x1:.62,scale:3,blur:1}))),B=kyRows(await ocr(kyMask(img,KYW2,{x1:.62,scale:2})));
   const V={};A.forEach(r=>V[r[0]]={a:r});B.forEach(r=>(V[r[0]]=V[r[0]]||{}).b=r);
   Object.keys(V).forEach(k=>{const {a,b}=V[k],x=a||b;M[k]=[x[0],x[1],x[2],a&&b&&a[2]==b[2]?0:1,a&&b&&a[2]!=b[2]?b[2]:0]});
   const lab=await ocr(kyMask(img,KYGR,{x0:.6,scale:2})),nums=((await ocr(kyMask(img,KYCY,{x0:.6,scale:2}),{tessedit_char_whitelist:'0123456789'})).match(/\d{3,7}/g)||[]).map(Number);
   if(/current/i.test(lab)){const v=nums.filter(x=>x>=100).sort((a,b)=>b-a);if(v[0])res.c30=v[0];if(v[1])res.c100=v[1]}else if(/event\s*point/i.test(lab)&&nums.length)res.me=nums[0];
   const red=await ocr(kyMask(img,KYRD,{x0:.55,y1:.4,scale:2})),md=red.match(/(\d{1,2})\s*([A-Za-z]{3})[a-z]*\.?\s*(\d{4})/);if(md)res.end=md[1]+' '+md[2]+' '+md[3]}
 }catch(e){KYBUSY='';draw();toast('⚠️ '+esc(e.message||e),'#ff4d4d');return}
 KYBUSY='';const rows=Object.values(M).sort((a,b)=>a[0]-b[0]);
 for(let i=1;i<rows.length;i++){const a=rows[i-1],b=rows[i];if(b[0]>a[0]&&b[2]>a[2]){if(b[4]&&b[4]<=a[2]){b[2]=b[4];b[4]=0}else{b[3]=1;a[3]=1}}}   // los puntos no pueden subir al bajar de puesto
 if(!rows.length&&!res.c30)toast('No encontré el ranking en esa imagen. Usa capturas de la ventana “Ranking List” del evento.','#ff9f43');kyOpen(Object.assign(res,{rows}))}
function kyPick(){const i=document.createElement('input');i.type='file';i.accept='image/*';i.multiple=true;i.onchange=()=>kyRead(i.files);i.click()}
document.addEventListener('paste',e=>{if(tab!='kyubi'||NSET||XEDIT||SHEDIT||KYR)return;const f=[...(e.clipboardData||{}).items||[]].filter(x=>x.kind=='file').map(x=>x.getAsFile()).filter(Boolean);if(f.length){e.preventDefault();kyRead(f)}});
document.addEventListener('dragover',e=>{if(tab=='kyubi'){e.preventDefault();const d=$('#kydrop');if(d)d.classList.add('on')}});
document.addEventListener('dragleave',e=>{const d=$('#kydrop');if(d&&!e.relatedTarget)d.classList.remove('on')});
document.addEventListener('drop',e=>{if(tab!='kyubi')return;e.preventDefault();const d=$('#kydrop');if(d)d.classList.remove('on');kyRead(e.dataTransfer.files)});

// ---- revisar y guardar ----
const kyDT=t=>{const d=new Date(t*1000);return new Date(d-d.getTimezoneOffset()*6e4).toISOString().slice(0,16)};
function kyOpen(r){KYR=r||{rows:[],c30:0,c100:0,end:'',me:0,t:0};if(!KYR.rows.length&&!r)KYR.rows=Array.from({length:10},(_,i)=>[i+1,'',0,0,0]);draw()}
const kyRowHtml=r=>`<tr class="${r[3]?'kyfl':''}"><td><input class=k1 type=number min=1 value="${r[0]||''}" style="width:64px"><td><input class=k2 value="${esc(r[1]||'')}" style="width:100%;min-width:140px"><td><input class=k3 type=number min=0 value="${r[2]||''}" style="width:120px">${r[4]?` <span class=o title="La otra lectura dio este número">¿${L(r[4])}?</span>`:''}<td><button title="Quitar" onclick="this.closest('tr').remove()">🗑</button></tr>`;
function kyRvHtml(){const r=KYR;return `<div class=set><div class=mh><h3 style=margin:0>📸 Revisa y guarda</h3><button onclick="KYR=null;draw()">✕</button></div>
 <p class=m style="font-size:12px">${r.rows.some(x=>x[3])?'⚠️ Las filas en amarillo pueden tener un número mal leído: compáralas con tu captura. ':''}Puedes corregir, quitar o agregar filas. También puedes pegar texto (una línea por jugador: <code>1 TDY Rale 232900</code>).</p>
 <div style="display:flex;gap:10px;flex-wrap:wrap"><label>🎯 Corte Top 30 <input id=kyc30 type=number value="${r.c30||''}" style="width:110px"></label><label>🎯 Corte Top 100 <input id=kyc100 type=number value="${r.c100||''}" style="width:110px"></label><label>⏳ Termina <input id=kyend value="${esc(r.end||'')}" placeholder="01 Oct 2026" style="width:120px"></label><label title="Cuándo tomaste la captura: sirve para calcular el ritmo por hora">🕒 Hora de la captura <input id=kyt type=datetime-local value="${kyDT(r.t||Date.now()/1e3)}"></label></div>
 <div class=wrap><table class=mt id=kyrt><tr><th>#<th>Jugador<th>Event Points<th></tr>${r.rows.map(kyRowHtml).join('')}</table></div>
 <p style="display:flex;gap:6px;flex-wrap:wrap"><button onclick="$('#kyrt').insertAdjacentHTML('beforeend',kyRowHtml([0,'',0,0,0]))">➕ Agregar fila</button></p>
 <textarea id=kytx placeholder="O pega aquí el ranking como texto…" style="width:100%;height:60px" oninput="kyPaste(this.value)"></textarea>
 <p style="margin-top:12px;display:flex;gap:6px;flex-wrap:wrap"><button class=kybtn onclick="kySave()">💾 Guardar captura</button><button onclick="KYR=null;draw()">Cancelar</button></p></div>`}
function kyPaste(t){const R=kyRows(t),tb=$('#kyrt');tb.querySelectorAll('tr.kytx').forEach(x=>x.remove());if(!R.length)return;tb.querySelectorAll('tr:not(:first-child)').forEach(x=>{if(!x.querySelector('.k2').value)x.remove()});R.forEach(r=>tb.insertAdjacentHTML('beforeend',kyRowHtml([r[0],r[1],r[2],0,0]).replace('<tr class="','<tr class="kytx ')))}
function kySave(){const D={};[...document.querySelectorAll('#kyrt tr')].slice(1).map(t=>[+t.querySelector('.k1').value,t.querySelector('.k2').value.trim(),+t.querySelector('.k3').value]).filter(r=>r[0]>0&&r[1]&&r[2]>=0).forEach(r=>D[r[0]+'|'+kyNorm(r[1])]=r);const rows=Object.values(D).sort((a,b)=>a[0]-b[0]);
 const c30=+$('#kyc30').value||0,c100=+$('#kyc100').value||0;if(!rows.length&&!c30&&!c100)return toast('No hay nada que guardar','#ff9f43');
 const tv=$('#kyt')&&$('#kyt').value,tt=tv?Math.min(NOW(),new Date(tv).getTime()/1e3+SK):NOW();
 fetch('/kyubi',{method:'POST',body:JSON.stringify({add:{t:tt,rows,c30,c100,end:$('#kyend').value.trim(),me:KYR.me||0}})}).then(r=>r.json()).then(j=>{XV=j.v;KYR=null;toast('💾 Captura guardada: '+rows.length+' jugadores','#3ddc84');return loadX()}).catch(()=>toast('No se pudo guardar','#ff4d4d'))}
function kyDel(t){if(!confirm(T_('¿Borrar esta captura?')))return;fetch('/kyubi',{method:'POST',body:JSON.stringify({del:t})}).then(r=>r.json()).then(j=>{XV=j.v;loadX()})}
function kyClear(){if(!confirm(T_('¿Empezar una ronda nueva? Se borran todas las capturas guardadas del Kyubi.')))return;fetch('/kyubi',{method:'POST',body:JSON.stringify({clear:1})}).then(r=>r.json()).then(j=>{XV=j.v;KYLAST=null;loadX()})}
function kyExport(){fetch('/export').then(r=>r.blob()).then(b=>{const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='datos.json';a.click();toast('🌐 Se descargó <b>datos.json</b>: ponlo dentro de la carpeta de la página y vuelve a subirla a Netlify','#3ddc84')}).catch(()=>toast('No se pudo exportar','#ff4d4d'))}
document.addEventListener('click',e=>{const k=e.target.closest('[data-ks]');if(k){kySort=k.dataset.ks;draw()}});

/* ===================== IDIOMAS (toda la pagina traducida, con bandera por pais) ===================== */
const LANGMAP='AD:ca|AE:ar|AF:ps,fa|AG:en|AL:sq|AM:hy|AO:pt|AR:es|AT:de|AU:en|AZ:az|BA:bs,hr,sr|BB:en|BD:bn|BE:nl,fr,de|BF:fr|BG:bg|BH:ar|BI:fr|BJ:fr,yo|BN:ms|BO:es,qu,ay|BR:pt|BS:en|BT:dz|BW:en,tn|BY:be,ru|BZ:en|CA:en,fr|CD:fr,ln|CF:fr|CG:fr|CH:de,fr,it|CI:fr|CL:es|CM:fr,en|CN:zh-CN,ug,bo|CO:es|CR:es|CU:es|CV:pt|CY:el,tr|CZ:cs|DE:de|DJ:fr,so|DK:da|DM:en|DO:es|DZ:ar,fr|EC:es,qu|EE:et|EG:ar|ER:ti|ES:es,ca,gl,eu|ET:am,om|FI:fi,sv|FJ:en,fj|FM:en|FR:fr,co,br,oc|GA:fr|GB:en,cy,gd|GD:en|GE:ka|GH:en,ak,ee|GM:en|GN:fr|GQ:es|GR:el|GT:es|GW:pt|GY:en|HK:yue,zh-TW,en|HN:es|HR:hr|HT:ht,fr|HU:hu|ID:id,jw,su|IE:en,ga|IL:iw,ar,yi|IN:hi,en,bn,te,mr,ta,ur,gu,kn,ml,pa,or,as,mai,sa,gom,bho,doi,mni-Mtei,lus|IQ:ar,ckb,ku|IR:fa|IS:is|IT:it|JM:en|JO:ar|JP:ja|KE:sw,en|KG:ky,ru|KH:km|KI:en|KM:fr,ar|KN:en|KP:ko|KR:ko|KW:ar|KZ:kk,ru|LA:lo,hmn|LB:ar,fr|LC:en|LI:de|LK:si,ta|LR:en|LS:st,en|LT:lt|LU:lb,fr,de|LV:lv|LY:ar|MA:ar,fr|MC:fr|MD:ro|ME:sr|MG:mg,fr|MH:en|MK:mk,sq|ML:fr,bm|MM:my|MN:mn|MO:zh-TW,pt|MR:ar,fr|MT:mt,en|MU:en,fr|MV:dv|MW:ny,en|MX:es|MY:ms,en,zh-CN|MZ:pt|NA:en,af|NE:fr,ha|NG:en,ha,yo,ig|NI:es|NL:nl,fy|NO:no|NP:ne|NR:en|NZ:en,mi|OM:ar|PA:es|PE:es,qu,ay|PG:en|PH:tl,en,ceb,ilo|PK:ur,en,pa,sd,ps|PL:pl|PR:es,en|PS:ar|PT:pt|PW:en|PY:es,gn|QA:ar|RO:ro|RS:sr|RU:ru,tt|RW:rw,en,fr|SA:ar|SB:en|SC:en,fr|SD:ar|SE:sv|SG:en,zh-CN,ms,ta|SI:sl|SK:sk|SL:en,kri|SM:it|SN:fr,wo|SO:so|SR:nl|SS:en|ST:pt|SV:es|SY:ar|SZ:en,ss|TD:fr,ar|TG:fr,ee|TH:th|TJ:tg|TL:pt|TM:tk|TN:ar,fr|TO:en,to|TR:tr,ku|TT:en|TV:en|TW:zh-TW|TZ:sw,en|UA:uk|UG:en,lg|US:en,es,haw|UY:es|UZ:uz|VA:it,la|VC:en|VE:es|VN:vi|VU:en,fr|WS:sm,en|XK:sq,sr|YE:ar|ZA:en,af,zu,xh,st,nso,ts,tn|ZM:en,ny|ZW:en,sn|UN:eo';
const LSUG=[['ES','es'],['US','en'],['BR','pt'],['PH','tl'],['ID','id'],['AL','sq'],['MY','ms'],['VN','vi'],['TH','th'],['FR','fr'],['DE','de'],['IT','it'],['RU','ru'],['TR','tr'],['SA','ar'],['IN','hi'],['JP','ja'],['KR','ko'],['CN','zh-CN']];
const I18NV='262cf95f87';
const LBCP={iw:'he',jw:'jv',tl:'fil','mni-Mtei':'mni','zh-CN':'zh-Hans','zh-TW':'zh-Hant',no:'nb'};
const LNAT={ceb:'Cebuano',hmn:'Hmoob',haw:'ʻŌlelo Hawaiʻi',ilo:'Ilokano',kri:'Krio',bho:'भोजपुरी',doi:'डोगरी',gom:'कोंकणी',lus:'Mizo ṭawng',mai:'मैथिली',mni:'ꯃꯩꯇꯩꯂꯣꯟ',ckb:'کوردیی ناوەندی',ku:'Kurdî',sa:'संस्कृतम्',nso:'Sesotho sa Leboa',ts:'Xitsonga',ak:'Twi',ee:'Eʋegbe',ln:'Lingála',lg:'Luganda',om:'Afaan Oromoo',ay:'Aymar aru',gn:"Avañe'ẽ",qu:'Runa Simi',yue:'粵語',dz:'རྫོང་ཁ',bo:'བོད་སྐད',fj:'Na Vosa Vakaviti',to:'Lea fakatonga',tn:'Setswana',ss:'SiSwati',eo:'Esperanto',la:'Latina',oc:'Occitan',br:'Brezhoneg',co:'Corsu',sm:'Gagana Samoa',mi:'Te Reo Māori',ug:'ئۇيغۇرچە',tt:'Татар',wo:'Wolof',bm:'Bamanankan',st:'Sesotho',ny:'Chichewa',sn:'chiShona',rw:'Ikinyarwanda',ht:'Kreyòl ayisyen'};
let DL={};const mcase=(x,t)=>{const a=x.charAt(0),b=t.charAt(0);return a&&a===a.toLowerCase()&&a!==a.toUpperCase()?b.toLowerCase()+t.slice(1):a===a.toUpperCase()&&a!==a.toLowerCase()?b.toUpperCase()+t.slice(1):t};
const dget=q=>{const t=DICT[q];if(t)return t;const u=DL[q.toLowerCase()];return u?mcase(q,u):null};
let LANG=(()=>{try{return localStorage.lang||'es'}catch(e){return'es'}})(),LCC=(()=>{try{return localStorage.langcc||''}catch(e){return''}})(),DICT=null,TRX=null,TRV=new Set(),MOB=null,LGLIST=null;
const TRC=new Map(),TRD=new WeakMap(),TRA=new WeakMap();
const lsGet=k=>{try{return JSON.parse(localStorage[k]||'null')}catch(e){return null}},lsSet=(k,v)=>{try{localStorage[k]=JSON.stringify(v)}catch(e){}};
const bcp=l=>LBCP[l]||l;
function dn(type,code,inl){try{const r=new Intl.DisplayNames([bcp(inl)],{type}).of(type=='language'?bcp(code):code);return r&&r!=bcp(code)&&r!=code?r:null}catch(e){return null}}
const langNative=l=>{const r=dn('language',l,l);return r?r.charAt(0).toLocaleUpperCase(bcp(l))+r.slice(1):LNAT[bcp(l)]||LNAT[l]||l};
const flagEmoji=cc=>cc&&cc.length==2&&cc!='UN'?cc.toUpperCase().replace(/./g,ch=>String.fromCodePoint(127397+ch.charCodeAt(0))):'🌐';
const flagImg=cc=>cc&&cc!='UN'?`<img class=flg src="https://flagcdn.com/w40/${esc(cc.toLowerCase())}.png" alt="${flagEmoji(cc)}" loading=lazy onerror="this.replaceWith(document.createTextNode(this.alt))">`:'<span class=flg style="display:inline-flex;align-items:center;justify-content:center">🌐</span>';
const GLOBE='<svg viewBox="0 0 32 32" aria-hidden="true"><defs><radialGradient id="lgg" cx="35%" cy="30%" r="75%"><stop offset="0" stop-color="#7dd3fc"/><stop offset=".55" stop-color="#2563eb"/><stop offset="1" stop-color="#1e3a8a"/></radialGradient></defs><circle cx="16" cy="16" r="14" fill="url(#lgg)"/><g fill="#34d399" opacity=".9"><path d="M9 7.5c3-2 6.5-1.2 7.5 1s-1 3.2-3.2 3.2-2 2.2-4.3 2.2-3.2-3.3 0-6.4z"/><path d="M18 14c2.2-1.2 5.3 0 6.3 2.2s0 5.2-2.1 6.2-3.2 3.2-5.3 2.1 0-4.2-1-5.3 0-4.1 2.1-5.2z"/><path d="M6.5 18c1 0 3.2 1 3.2 3.1s-1 3.2-2.1 2.1-2.2-4.2-1.1-5.2z"/></g><g fill="none" stroke="#e0f2fe" stroke-opacity=".6" stroke-width=".8"><circle cx="16" cy="16" r="14"/><ellipse cx="16" cy="16" rx="6" ry="14"><animate attributeName="rx" values="1;13;1" dur="6s" repeatCount="indefinite"/></ellipse><path d="M2 16h28M4.5 9h23M4.5 23h23"/></g></svg>';

// ---- traducir texto: frase exacta del catalogo o, si no, las frases conocidas que contenga ----
function trStr(s,sub){if(!DICT||!s)return s;const ck=(sub?'1':'0')+s;let r=TRC.get(ck);if(r!==undefined)return r;r=s;
 const m=s.match(/^([^\p{L}]*)([\s\S]*?)([^\p{L}]*)$/u),c=m?m[2].replace(/\s+/g,' '):'';
 if(c&&!TRV.has(c)){const t=dget(c);if(t)r=m[1]+t+m[3];else{const ps=s.split(/([^\p{L}\s'’-]+)/u);let ch=0;for(let i=0;i<ps.length;i+=2){const q=ps[i].trim().replace(/\s+/g,' '),t2=q&&dget(q);if(t2&&t2!==q){ps[i]=ps[i].replace(ps[i].trim(),t2);ch=1}}if(ch)r=ps.join('');if(sub&&TRX)r=r.replace(TRX,x=>{const t3=dget(x);return t3?t3:x})}}
 if(TRC.size>30000)TRC.clear();TRC.set(ck,r);return r}
const NOSUB='[data-o],[data-w],[data-c],[data-fp],[data-mf],.nm,code,b,.sst,.rsc';
function trNode(n){const s=n.data;if(!s||TRD.get(n)===s||!/\p{L}/u.test(s))return;const p=n.parentElement;if(p&&p.closest('.notr,textarea,script,style'))return;const r=trStr(s,!(p&&p.closest(NOSUB)));TRD.set(n,r);if(r!==s)n.data=r}
function trAttr(e){if(!e.getAttribute)return;for(const a of ['title','placeholder']){const v=e.getAttribute(a);if(!v||!/\p{L}/u.test(v))continue;const o=TRA.get(e)||{};if(o[a]===v)continue;const r=trStr(v,1);o[a]=r;TRA.set(e,o);if(r!==v)e.setAttribute(a,r)}}
function trTree(r){if(!DICT||!r)return;if(r.nodeType==3)return trNode(r);if(r.nodeType==1&&r.tagName=='TEXTAREA'&&!r.closest('.notr'))return trAttr(r);if(r.nodeType!=1||(r.closest&&r.closest('.notr,script,style,textarea')))return;trAttr(r);
 const w=document.createTreeWalker(r,5,{acceptNode:n=>{if(n.nodeType!=1)return 1;if(n.tagName=='TEXTAREA'&&!n.classList.contains('notr'))trAttr(n);return n.classList.contains('notr')||/^(SCRIPT|STYLE|TEXTAREA)$/.test(n.tagName)?2:1}});let n;while((n=w.nextNode()))n.nodeType==3?trNode(n):trAttr(n)}
function onMut(ms){for(const m of ms){if(m.type=='childList')m.addedNodes.forEach(trTree);else if(m.type=='characterData')trNode(m.target);else trAttr(m.target)}}
const T_=s=>DICT?trStr(s,1):s;   // para confirm(), voz, etc.
function applyDict(d){DICT=d;TRC.clear();TRV=new Set(Object.values(d));DL={};for(const k in d)if(d[k]&&!DL[k.toLowerCase()])DL[k.toLowerCase()]=d[k];const ks=Object.keys(d).filter(k=>k.length>=4&&d[k]&&d[k]!==k).sort((a,b)=>b.length-a.length);
 try{TRX=ks.length?new RegExp('(?<!\\p{L})('+ks.map(k=>k.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|')+')(?!\\p{L})','giu'):null}catch(e){TRX=null}
 document.documentElement.lang=bcp(LANG);trTree(document.documentElement);
 if(!MOB){MOB=new MutationObserver(onMut);MOB.observe(document.documentElement,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['title','placeholder']})}lngBtn()}

// ---- cargar el diccionario del idioma (la 1a vez se traduce en tu PC y queda guardado) ----
function lngOv(h){let o=$('#lngov');if(!o){o=document.createElement('div');o.id='lngov';document.body.appendChild(o)}o.style.display=h?'block':'none';if(h)o.innerHTML=h}
function loadLang(){if(LANG=='es')return;const c=lsGet('i18n_'+LANG);if(c&&c.v===I18NV&&c.d){applyDict(c.d);return}
 const nm=langNative(LANG),poll=()=>fetch('/i18n?lang='+encodeURIComponent(LANG)).then(r=>r.json()).then(j=>{
  if(j.done){lsSet('i18n_'+LANG,{v:I18NV,d:j.dict});lngOv('');if(DICT)location.reload();else applyDict(j.dict);return}
  if(j.err&&!j.busy){lngOv(`<b class=notr>${flagImg(LCC)} ${esc(nm)}</b><br><span class=dn>⚠️ No se pudo traducir ahora</span> <span class=m style="font-size:11px">(${esc(j.err)})</span><div style="margin-top:6px;display:flex;gap:6px"><button class=sm onclick="lngOv('');loadLang()">🔄 Reintentar</button><button class=sm onclick="setLang('es','ES')">Volver al español</button></div>`);return}
  lngOv(`<div class=notr style="display:flex;align-items:center;gap:8px"><span class=lgspin>${GLOBE}</span><div><b>${flagImg(LCC)} ${esc(nm)}</b><br><span class=m style="font-size:12px">🌐 ${Math.round(100*(j.prog||0))}%</span></div></div><div class=lgbar><i style="width:${Math.round(100*(j.prog||0))}%"></i></div>`);setTimeout(poll,1200)}).catch(()=>setTimeout(poll,3000));
 poll()}
function setLang(l,cc){lngClose();const prev=LANG;try{localStorage.lang=l;localStorage.langcc=cc||''}catch(e){}
 if(l==prev){LCC=cc||'';lngBtn();return}if(prev!='es'||l=='es'){location.reload();return}LANG=l;LCC=cc||'';loadLang();lngBtn()}

// ---- boton "🌐 Idiomas" arriba a la derecha + lista de paises con bandera ----
function lngList(){if(LGLIST)return LGLIST;const out=[];LANGMAP.split('|').forEach(x=>{const [cc,ls]=x.split(':');ls.split(',').forEach(l=>{const cn=cc=='UN'?'—':dn('region',cc,LANG)||dn('region',cc,'es')||cc,cnN=cc=='UN'?'':dn('region',cc,l)||'',ln=langNative(l),les=dn('language',l,LANG)||'';
 out.push({cc,l,cn,cnN,ln,les,k:(cn+' '+cnN+' '+ln+' '+les+' '+l+' '+cc).toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'')})})});
 out.sort((a,b)=>a.cn.localeCompare(b.cn,bcp(LANG)));return LGLIST=out}
const lgItem=x=>`<div class="lgi notr ${x.l==LANG&&(!LCC||x.cc==LCC)?'on':''}" data-lg="${esc(x.l)}" data-cc="${esc(x.cc)}">${flagImg(x.cc)}<span class=lgt><b>${esc(x.cn=='—'?x.ln:x.cn)}</b>${x.cnN&&x.cnN!=x.cn?` <span class=m>${esc(x.cnN)}</span>`:''}<br><span class=lgl>${esc(x.ln)}${x.les&&x.les.toLowerCase()!=x.ln.toLowerCase()?' · '+esc(x.les):''}${x.l=='es'?' · original':''}</span></span>${x.l==LANG&&(!LCC||x.cc==LCC)?'<span class=lgck>✓</span>':''}</div>`;
function lngRender(){const q=(($('#lngq')||{}).value||'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'').trim(),A=lngList(),box=$('#lngl');if(!box)return;
 if(q){const R=A.filter(x=>x.k.includes(q));box.innerHTML=R.map(lgItem).join('')||'<div class="m" style="padding:12px">Sin resultados</div>';return}
 const nav=(navigator.languages||[navigator.language||'']).map(x=>String(x));let sug=LSUG.slice();const bl=nav.find(x=>!/^es\b/i.test(x));
 if(bl){const [l0,r0]=bl.split('-'),lo=l0.toLowerCase(),sg=LSUG.find(p=>p[1].split('-')[0]==lo),hit=A.find(x=>x.cc==(r0||'').toUpperCase()&&x.l.split('-')[0]==lo)||(sg&&A.find(x=>x.cc==sg[0]&&x.l==sg[1]))||A.find(x=>x.l.split('-')[0]==lo);if(hit)sug=[[hit.cc,hit.l]].concat(sug.filter(s=>!(s[0]==hit.cc&&s[1]==hit.l)))}
 const S2=sug.map(([cc,l])=>A.find(x=>x.cc==cc&&x.l==l)).filter(Boolean);
 box.innerHTML=`<div class=lgh>⭐ Sugeridos</div>${S2.map(lgItem).join('')}<div class=lgh>🌍 Todos los países · ${A.length}</div>${A.map(lgItem).join('')}`}
function lngOpen(){const m=$('#lngm');if(m.classList.contains('on'))return lngClose();m.classList.add('on');lngRender();setTimeout(()=>{const q=$('#lngq');if(q&&innerWidth>700)q.focus()},30)}
function lngClose(){const m=$('#lngm');if(m)m.classList.remove('on')}
function lngBtn(){const b=$('#lngb');if(!b)return;const cur=LANG=='es'?'Español':langNative(LANG);b.innerHTML=`<span class=lgg>${GLOBE}</span><span class=lgw>Idiomas</span>${flagImg(LCC||(LANG=='es'?'ES':''))}<span class="lgc notr">${esc(cur)}</span>`}
(function(){const w=document.createElement('div');w.id='lng';w.innerHTML=`<button id=lngb title="Cambiar idioma de la página" onclick="lngOpen()"></button><div id=lngm><input id=lngq placeholder="🔎 Buscar país o idioma…" oninput="lngRender()"><div id=lngl></div><div class="lgf m">Traducción automática · los nombres de clanes y jugadores no se traducen</div></div>`;document.body.appendChild(w);lngBtn();
 document.addEventListener('click',e=>{const it=e.target.closest('.lgi');if(it){setLang(it.dataset.lg,it.dataset.cc);return}if(!e.target.closest('#lng'))lngClose()});
 document.addEventListener('keydown',e=>{if(e.key=='Escape')lngClose()});loadLang()})();

/* ==================== v22: MODO KURAMA — INYECCION FRONTEND ==================== */
(function(){
'use strict';
var V22 = {soundOn:true, voice:false, vibrate:true, notify:true, vol:0.7, minRep:50, theme:'kurama', compact:false, filterClan:'', filterType:'', filterMin:0, seenKeys:{}, tickerOn:true};
try{var s=JSON.parse(localStorage.getItem('zenshin_v22')||'{}'); for(var k in s) V22[k]=s[k];}catch(e){}
function save(){try{localStorage.setItem('zenshin_v22', JSON.stringify({soundOn:V22.soundOn,voice:V22.voice,vibrate:V22.vibrate,notify:V22.notify,vol:V22.vol,theme:V22.theme,compact:V22.compact,tickerOn:V22.tickerOn}));}catch(e){}}

/* --- CSS inyectado --- */
var css = document.createElement('style');
css.textContent = [
'.v22-ticker{position:sticky;top:0;z-index:50;background:linear-gradient(90deg,#1a1a2e,#16213e);border-bottom:2px solid var(--o,#ff9f43);overflow:hidden;white-space:nowrap;height:28px;line-height:28px;font-size:12px}',
'.v22-ticker-inner{display:inline-block;padding-left:100%;animation:v22scroll 40s linear infinite}',
'@keyframes v22scroll{0%{transform:translateX(0)}100%{transform:translateX(-100%)}}',
'.v22-toast{position:fixed;top:60px;right:12px;z-index:9999;background:linear-gradient(135deg,#ff9f43,#ff6b35);color:#fff;padding:12px 18px;border-radius:10px;box-shadow:0 8px 30px rgba(255,107,53,.4);font-weight:600;max-width:320px;animation:v22in .3s ease, v22out .5s ease 4.5s forwards;pointer-events:none}',
'.v22-toast.achv{background:linear-gradient(135deg,#f0b90b,#ff9f43)}',
'.v22-toast.danger{background:linear-gradient(135deg,#ff4d4d,#c0392b)}',
'@keyframes v22in{from{opacity:0;transform:translateX(40px)}to{opacity:1;transform:translateX(0)}}',
'@keyframes v22out{to{opacity:0;transform:translateX(40px)}}',
'.v22-btn{background:var(--l,#26262b);color:var(--t,#e8e8ea);border:1px solid var(--l);border-radius:6px;padding:4px 10px;font-size:12px;cursor:pointer;margin:2px}',
'.v22-btn:hover{background:var(--o,#ff9f43);color:#000}',
'.v22-btn.active{background:var(--o,#ff9f43);color:#000;font-weight:700}',
'.v22-panel{background:var(--c,#151518);border:1px solid var(--l,#26262b);border-radius:10px;padding:12px;margin:8px 0}',
'.v22-filterbar{display:flex;gap:6px;flex-wrap:wrap;align-items:center;padding:6px;background:var(--c);border-radius:8px;margin:6px 0}',
'.v22-filterbar input,.v22-filterbar select{background:var(--bg);border:1px solid var(--l);color:var(--t);border-radius:5px;padding:3px 8px;font-size:12px}',
'.v22-badge{display:inline-block;padding:1px 6px;border-radius:8px;font-size:10px;font-weight:700;margin-left:4px}',
'.v22-threat-S{background:#ff4d4d;color:#fff}.v22-threat-A{background:#ff9f43;color:#000}.v22-threat-B{background:#f0b90b;color:#000}.v22-threat-C{background:#3ddc84;color:#000}.v22-threat-D{background:#555;color:#fff}',
'.v22-modal{position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:9998;display:flex;align-items:center;justify-content:center;padding:16px}',
'.v22-modal-inner{background:var(--c);border:1px solid var(--o);border-radius:14px;padding:20px;max-width:600px;width:100%;max-height:85vh;overflow:auto}',
'.v22-pred-row{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--l);font-size:13px}',
'.v22-cmp-col{flex:1;padding:10px;border-radius:8px;margin:4px}',
'.v22-cmp-a{background:rgba(255,159,67,.15);border:1px solid #ff9f43}',
'.v22-cmp-b{background:rgba(61,220,132,.15);border:1px solid #3ddc84}',
'body.v22-compact .v22-hide-compact{display:none}',
'body.v22-compact{font-size:12px}',
'.v22-kbd{background:var(--l);padding:1px 5px;border-radius:3px;font-size:10px;font-family:monospace}',
/* temas */
'body.theme-kurama{--o:#ff9f43;--r:#ff6b35}',
'body.theme-sharingan{--o:#ff4d4d;--r:#c0392b;--bg:#1a0a0a;--c:#2a1010}',
'body.theme-sage{--o:#3ddc84;--r:#27ae60;--bg:#0a1a0f;--c:#102a1a}',
'body.theme-rinnegan{--o:#a855f7;--r:#7c3aed;--bg:#0f0a1a;--c:#1a102a}',
'body.theme-uchija{--o:#e8e8ea;--r:#8a8a93;--bg:#000;--c:#0a0a0a}',
'.v22-theme-dot{width:18px;height:18px;border-radius:50%;display:inline-block;cursor:pointer;margin:2px;border:2px solid transparent}',
'.v22-theme-dot.active{border-color:#fff}',
'.v22-flash{animation:v22flash 1s ease}',
'@keyframes v22flash{0%,100%{background:transparent}50%{background:rgba(255,77,77,.3)}}'
].join('\n');
document.head.appendChild(css);

/* --- Motor de sonido (Web Audio) --- */
var AC = null;
function ac(){ if(!AC){ try{ AC = new (window.AudioContext||window.webkitAudioContext)(); }catch(e){} } return AC; }
function beep(freq, dur, type, vol){
    if(!V22.soundOn) return;
    var a = ac(); if(!a) return;
    var o = a.createOscillator(), g = a.createGain();
    o.type = type||'sine'; o.frequency.value = freq;
    g.gain.setValueAtTime(0, a.currentTime);
    g.gain.linearRampToValueAtTime((vol||V22.vol)*0.3, a.currentTime+0.01);
    g.gain.exponentialRampToValueAtTime(0.001, a.currentTime+(dur||0.2));
    o.connect(g); g.connect(a.destination); o.start(); o.stop(a.currentTime+(dur||0.2)+0.05);
}
function sAttack(){ beep(880,0.15,'square'); setTimeout(function(){beep(1100,0.2,'square')},120); }
function sLoss(){ beep(220,0.3,'sawtooth',0.9); setTimeout(function(){beep(165,0.4,'sawtooth',0.9)},200); }
function sGain(){ beep(660,0.1,'sine'); setTimeout(function(){beep(880,0.15,'sine')},80); setTimeout(function(){beep(1100,0.2,'sine')},160); }
function sRank(){ beep(523,0.1); setTimeout(function(){beep(659,0.1)},80); setTimeout(function(){beep(784,0.15)},160); setTimeout(function(){beep(1047,0.3)},240); }
function sAchv(){ [523,659,784,1047,1319].forEach(function(f,i){setTimeout(function(){beep(f,0.15,'triangle')},i*90)}); }
function speak(text){
    if(!V22.voice || !('speechSynthesis' in window)) return;
    try{ var u = new SpeechSynthesisUtterance(text); u.lang='es-ES'; u.rate=1.1; u.volume=V22.vol; speechSynthesis.cancel(); speechSynthesis.speak(u); }catch(e){}
}
function vibrate(pat){ if(V22.vibrate && navigator.vibrate){ try{navigator.vibrate(pat||[200,100,200]);}catch(e){} } }
function browserNotify(title, body){
    if(!V22.notify) return;
    if(!('Notification' in window)) return;
    if(Notification.permission === 'granted'){ try{ new Notification(title, {body:body, icon:''}); }catch(e){} }
    else if(Notification.permission !== 'denied'){ Notification.requestPermission(); }
}
function toast(msg, type){
    var d = document.createElement('div');
    d.className = 'v22-toast' + (type==='achv'?' achv':type==='danger'?' danger':'');
    d.textContent = msg;
    document.body.appendChild(d);
    setTimeout(function(){ d.remove(); }, 5000);
}

/* --- Ticker tape --- */
var ticker = document.createElement('div');
ticker.className = 'v22-ticker';
ticker.innerHTML = '<div class="v22-ticker-inner" id="v22ticker">⚡ Cargando eventos en vivo...</div>';
if(V22.tickerOn){ document.body.insertBefore(ticker, document.body.firstChild); }
function updateTicker(events){
    if(!events || !events.length) return;
    var txt = events.slice(0,15).map(function(e){
        var amt = e.amt||0;
        var sign = amt>0?'+':'';
        return '['+(e.k||'')+'] '+(e.clan||'')+' '+(e.txt||'')+(amt?' ('+sign+amt+')':'');
    }).join('  ◆  ');
    var t = document.getElementById('v22ticker');
    if(t) t.textContent = '⚡ ' + txt;
}

/* --- Deteccion de nuevos eventos (hook al SSE existente via MutationObserver) --- */
var lastEventCount = 0;
function checkNewEvents(){
    var evs = document.querySelectorAll('[data-ev], .event, [class*=event]');
    // fallback: usar el contenedor de eventos por texto
    var container = document.querySelector('#events, .events, [id*=event]');
    if(container){
        var items = container.children;
        if(items.length > lastEventCount && lastEventCount > 0){
            for(var i=0; i<items.length - lastEventCount; i++){
                var txt = items[i] ? items[i].textContent : '';
                handleEventText(txt);
            }
        }
        lastEventCount = items.length;
    }
}
function handleEventText(txt){
    if(!txt) return;
    var isLoss = /perdi|perdio|-[0-9]|bajo|atacado|ataque/i.test(txt);
    var isGain = /gano|\+[0-9]|subio|ganancia/i.test(txt);
    var isRank = /rango|puesto|#/i.test(txt) && /subio|bajo|cambio/i.test(txt);
    var isAchv = /logro|LOGRO|achievement/i.test(txt);
    var mine = window.V22_MINE || '';
    var isMine = mine && txt.indexOf(mine) >= 0;
    if(isAchv){ sAchv(); toast(txt, 'achv'); vibrate([100,50,100,50,200]); return; }
    if(isLoss && isMine){ sLoss(); speak('Ataque detectado. '+txt.substring(0,80)); vibrate([300,100,300,100,300]); browserNotify('⚠️ Ataque a tu clan', txt.substring(0,100)); toast('⚠️ '+txt.substring(0,80), 'danger'); document.body.classList.add('v22-flash'); setTimeout(function(){document.body.classList.remove('v22-flash')},1000); }
    else if(isGain){ sGain(); if(isMine){ speak('Tu clan gano reputacion'); vibrate([100,50,100]); } }
    else if(isRank){ sRank(); }
    else if(isLoss){ sAttack(); }
}

/* --- Barra de herramientas v22 --- */
var bar = document.createElement('div');
bar.className = 'v22-panel';
bar.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:8px';
bar.innerHTML = [
'<b style="color:var(--o)">⚡ v22 Kurama:</b>',
'<button class="v22-btn" id="v22snd">🔊 Sonido: ON</button>',
'<button class="v22-btn" id="v22voi">🗣️ Voz: OFF</button>',
'<button class="v22-btn" id="v22not">🔔 Notif: ON</button>',
'<button class="v22-btn" id="v22tik">📰 Ticker: ON</button>',
'<button class="v22-btn" id="v22cmp">📊 Compacto</button>',
'<button class="v22-btn" id="v22pred">🔮 Predicciones</button>',
'<button class="v22-btn" id="v22comp">⚔️ Comparar Clanes</button>',
'<button class="v22-btn" id="v22csv">⬇️ CSV</button>',
'<button class="v22-btn" id="v22bak">💾 Backup</button>',
'<span id="v22themes" style="margin-left:8px">Tema:',
'<span class="v22-theme-dot active" data-t="kurama" style="background:#ff9f43"></span>',
'<span class="v22-theme-dot" data-t="sharingan" style="background:#ff4d4d"></span>',
'<span class="v22-theme-dot" data-t="sage" style="background:#3ddc84"></span>',
'<span class="v22-theme-dot" data-t="rinnegan" style="background:#a855f7"></span>',
'<span class="v22-theme-dot" data-t="uchija" style="background:#e8e8ea"></span>',
'</span>',
'<span style="margin-left:auto;font-size:10px;color:var(--m)">Atajos: <span class="v22-kbd">M</span> sonido <span class="v22-kbd">V</span> voz <span class="v22-kbd">F</span> filtro <span class="v22-kbd">C</span> compacto <span class="v22-kbd">P</span> predicciones</span>'
].join(' ');
// insertar despues del header/nav
var nav = document.querySelector('nav') || document.querySelector('header');
if(nav) nav.parentNode.insertBefore(bar, nav.nextSibling);
else document.body.insertBefore(bar, document.body.firstChild);

/* --- Filtro de eventos --- */
var filt = document.createElement('div');
filt.className = 'v22-filterbar';
filt.id = 'v22filt';
filt.style.display = 'none';
filt.innerHTML = [
'<b>🔍 Filtro:</b>',
'<input id="v2fclan" placeholder="Clan..." style="width:120px">',
'<select id="v2ftype"><option value="">Todos</option><option value="hit">Ataques</option><option value="gain">Ganancias</option><option value="loss">Pérdidas</option><option value="move">Movimientos</option><option value="achv">Logros</option></select>',
'<input id="v2fmin" type="number" placeholder="Rep min" style="width:70px" value="0">',
'<button class="v22-btn" id="v2fapply">Aplicar</button>',
'<button class="v22-btn" id="v2fclear">Limpiar</button>'
].join('');
bar.parentNode.insertBefore(filt, bar.nextSibling);

/* --- Botones de accion --- */
function setBtn(id, label, on){ var b=document.getElementById(id); if(b){b.textContent=label; b.classList.toggle('active', on);} }
document.getElementById('v22snd').onclick = function(){ V22.soundOn=!V22.soundOn; setBtn('v22snd','🔊 Sonido: '+(V22.soundOn?'ON':'OFF'), V22.soundOn); if(V22.soundOn)sGain(); save(); };
document.getElementById('v22voi').onclick = function(){ V22.voice=!V22.voice; setBtn('v22voi','🗣️ Voz: '+(V22.voice?'ON':'OFF'), V22.voice); if(V22.voice)speak('Voz activada'); save(); };
document.getElementById('v22not').onclick = function(){ V22.notify=!V22.notify; setBtn('v22not','🔔 Notif: '+(V22.notify?'ON':'OFF'), V22.notify); if(V22.notify && 'Notification' in window) Notification.requestPermission(); save(); };
document.getElementById('v22tik').onclick = function(){ V22.tickerOn=!V22.tickerOn; ticker.style.display=V22.tickerOn?'block':'none'; setBtn('v22tik','📰 Ticker: '+(V22.tickerOn?'ON':'OFF'), V22.tickerOn); save(); };
document.getElementById('v22cmp').onclick = function(){ V22.compact=!V22.compact; document.body.classList.toggle('v22-compact', V22.compact); save(); };
document.getElementById('v22csv').onclick = function(){
    var t = prompt('Exportar CSV: clans | pvp | pve | events', 'clans');
    if(t) window.open('/csv?type='+encodeURIComponent(t), '_blank');
};
document.getElementById('v22bak').onclick = function(){ window.open('/backup', '_blank'); };

/* --- Temas --- */
function applyTheme(t){
    V22.theme = t;
    ['kurama','sharingan','sage','rinnegan','uchija'].forEach(function(x){ document.body.classList.toggle('theme-'+x, x===t); });
    document.querySelectorAll('.v22-theme-dot').forEach(function(d){ d.classList.toggle('active', d.dataset.t===t); });
    save();
    // enviar al servidor
    try{ fetch('/', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({theme:t})}); }catch(e){}
}
document.querySelectorAll('.v22-theme-dot').forEach(function(d){ d.onclick = function(){ applyTheme(d.dataset.t); }; });
applyTheme(V22.theme);

/* --- Modal de predicciones --- */
document.getElementById('v22pred').onclick = function(){
    var m = document.createElement('div'); m.className='v22-modal';
    m.innerHTML = '<div class="v22-modal-inner"><h3 style="color:var(--o)">🔮 Predicciones de Ranking — Fin de Temporada</h3><div id="v22predbody">Cargando...</div><button class="v22-btn" onclick="this.closest(\'.v22-modal\').remove()">Cerrar</button></div>';
    document.body.appendChild(m);
    m.onclick = function(e){ if(e.target===m) m.remove(); };
    fetch('/predict').then(function(r){return r.json()}).then(function(d){
        var b = document.getElementById('v22predbody');
        if(d.err){ b.innerHTML = '<p style="color:var(--r)">'+d.err+'</p>'; return; }
        var html = '<p style="color:var(--m)">Tiempo restante: <b>'+(d.hours_left||0).toFixed(1)+' h</b> · Fin estimado</p>';
        html += '<div class="v22-pred-row" style="font-weight:700;color:var(--o)"><span>#</span><span>Clan</span><span>Rep proyectada</span><span>Rate/h</span></div>';
        (d.top||[]).forEach(function(r, i){
            var mine = r.name === (window.V22_MINE||'');
            html += '<div class="v22-pred-row'+(mine?' style="color:var(--o);font-weight:700"':'')+'"><span>'+(i+1)+'</span><span>'+r.name+'</span><span>'+(r.rep_proj||0).toLocaleString()+'</span><span>+'+(r.rate||0)+'/h</span></div>';
        });
        b.innerHTML = html;
    }).catch(function(){ document.getElementById('v22predbody').innerHTML='<p style="color:var(--r)">Error al cargar predicciones</p>'; });
};

/* --- Modal comparar clanes --- */
document.getElementById('v22comp').onclick = function(){
    var a = prompt('Nombre del clan A:', '');
    if(!a) return;
    var b = prompt('Nombre del clan B:', '');
    if(!b) return;
    var m = document.createElement('div'); m.className='v22-modal';
    m.innerHTML = '<div class="v22-modal-inner"><h3 style="color:var(--o)">⚔️ Comparador de Clanes</h3><div id="v22cmpbody">Cargando...</div><button class="v22-btn" onclick="this.closest(\'.v22-modal\').remove()">Cerrar</button></div>';
    document.body.appendChild(m);
    m.onclick = function(e){ if(e.target===m) m.remove(); };
    fetch('/compare?a='+encodeURIComponent(a)+'&b='+encodeURIComponent(b)).then(function(r){return r.json()}).then(function(d){
        var bd = document.getElementById('v22cmpbody');
        if(d.err){ bd.innerHTML = '<p style="color:var(--r)">'+d.err+'</p>'+(d.found&&d.found.length?'<p>Sugerencias: '+d.found.join(', ')+'</p>':''); return; }
        function col(c, cls){
            return '<div class="v22-cmp-col '+cls+'"><h4>'+c.name+'</h4>'+
                '<p>🏆 Rango: <b>#'+c.rank+'</b> (proy: #'+c.pred_rank+')</p>'+
                '<p>⭐ Rep: <b>'+(c.rep||0).toLocaleString()+'</b></p>'+
                '<p>👥 Miembros: '+c.members+'</p>'+
                '<p>👑 Master: '+c.master+'</p>'+
                '<p>📈 Hoy: +'+(c.gain_today||0)+' / -'+(c.loss_today||0)+' (net: +'+(c.net||0)+')</p>'+
                '<p>⚡ Rate: +'+(c.rate||0)+'/h</p>'+
                '<p>🔥 Racha: '+c.streak+' ataques</p>'+
                '<p>⚠️ Amenaza: <span class="v22-badge v22-threat-'+c.level+'">'+c.level+' ('+c.threat+')</span></p></div>';
        }
        bd.innerHTML = '<div style="display:flex;gap:8px">'+col(d.a,'v22-cmp-a')+col(d.b,'v22-cmp-b')+'</div>'+
            '<p style="text-align:center;margin-top:10px">Diferencia de REP: <b style="color:var(--o)">'+(d.rep_diff||0).toLocaleString()+'</b> · Diferencia de rango: <b>'+d.rank_diff+'</b></p>';
    }).catch(function(){ document.getElementById('v22cmpbody').innerHTML='<p style="color:var(--r)">Error</p>'; });
};

/* --- Filtro --- */
document.getElementById('v2fapply').onclick = function(){
    V22.filterClan = document.getElementById('v2fclan').value.toLowerCase();
    V22.filterType = document.getElementById('v2ftype').value;
    V22.filterMin = parseInt(document.getElementById('v2fmin').value)||0;
    toast('Filtro aplicado: clan="'+V22.filterClan+'" tipo='+V22.filterType+' min='+V22.filterMin);
    applyFilterToDOM();
};
document.getElementById('v2fclear').onclick = function(){
    V22.filterClan=''; V22.filterType=''; V22.filterMin=0;
    document.getElementById('v2fclan').value=''; document.getElementById('v2ftype').value=''; document.getElementById('v2fmin').value='0';
    applyFilterToDOM();
};
function applyFilterToDOM(){
    var container = document.querySelector('#events, .events, [id*=event]');
    if(!container) return;
    Array.prototype.forEach.call(container.children, function(el){
        var txt = el.textContent || '';
        var show = true;
        if(V22.filterClan && txt.toLowerCase().indexOf(V22.filterClan)<0) show=false;
        if(V22.filterType && txt.toLowerCase().indexOf(V22.filterType)<0 && txt.indexOf(V22.filterType)<0) show=false;
        var m = txt.match(/[+-]?(\d[\d.,]*)/);
        if(V22.filterMin>0 && m && Math.abs(parseFloat(m[1].replace(/[.,]/g,''))) < V22.filterMin) show=false;
        el.style.display = show ? '' : 'none';
    });
}

/* --- Atajos de teclado --- */
document.addEventListener('keydown', function(e){
    if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.tagName==='SELECT') return;
    var k = e.key.toLowerCase();
    if(k==='m'){ document.getElementById('v22snd').click(); }
    else if(k==='v'){ document.getElementById('v22voi').click(); }
    else if(k==='n'){ document.getElementById('v22not').click(); }
    else if(k==='f'){ e.preventDefault(); var f=document.getElementById('v22filt'); f.style.display = f.style.display==='none'?'flex':'none'; }
    else if(k==='c'){ document.getElementById('v22cmp').click(); }
    else if(k==='p'){ document.getElementById('v22pred').click(); }
    else if(k==='t'){ document.getElementById('v22tik').click(); }
    else if(k>='1' && k<='9'){ var btns=document.querySelectorAll('nav button, nav a'); if(btns[parseInt(k)-1]) btns[parseInt(k)-1].click(); }
});

/* --- Obtener mi clan del estado --- */
function fetchMine(){
    fetch('/quick').then(function(r){return r.json()}).then(function(d){
        if(d && d.mine) window.V22_MINE = d.mine;
        if(d && d.sound){ V22.soundOn = d.sound.on!==0; V22.voice = !!d.sound.voice; V22.vol = d.sound.vol||0.7; }
        if(d && d.theme) applyTheme(d.theme);
        if(d && d.events) updateTicker(d.events);
    }).catch(function(){});
}
fetchMine();
setInterval(fetchMine, 15000);

/* --- Polling de eventos para sonido --- */
var lastEvLen = 0;
setInterval(function(){
    fetch('/quick').then(function(r){return r.json()}).then(function(d){
        if(d && d.events){
            var evs = d.events;
            if(evs.length > lastEvLen && lastEvLen > 0){
                var newEvs = evs.slice(lastEvLen - evs.length);
                newEvs.forEach(function(ev){
                    if(ev.k === 'achv'){ sAchv(); toast(ev.txt||'Logro!', 'achv'); }
                    else if(ev.amt < -V22.minRep){
                        var isMine = window.V22_MINE && ev.clan === window.V22_MINE;
                        if(isMine || (ev.txt && ev.txt.indexOf(window.V22_MINE||'')>=0)){
                            sLoss(); speak('Ataque a tu clan. '+ (ev.clan||'') +' perdio '+Math.abs(ev.amt)+' reputacion');
                            vibrate([300,100,300]); browserNotify('⚠️ '+ev.clan+' perdio '+Math.abs(ev.amt)+' REP', ev.txt||'');
                            toast('⚠️ '+ev.clan+': -'+Math.abs(ev.amt)+' REP', 'danger');
                        } else { sAttack(); }
                    } else if(ev.amt > V22.minRep){ sGain(); }
                });
            }
            lastEvLen = evs.length;
            updateTicker(evs);
        }
    }).catch(function(){});
}, 3000);

/* --- PWA install --- */
window.addEventListener('beforeinstallprompt', function(e){
    e.preventDefault();
    setTimeout(function(){ toast('📱 Instala la app: usa "Añadir a pantalla de inicio" en tu navegador'); }, 3000);
});

/* --- Bienvenida --- */
setTimeout(function(){
    toast('⚡ v22 Modo Kurama activado · Sonido+Voz+Predicciones listos');
    sGain();
}, 1500);

})();


/* ==================== v23: SABIO DE LOS SEIS CAMINOS — FRONTEND ==================== */
(function(){
'use strict';
var V23 = {bleedSound:true, onlinePanel:true};
try{var s=JSON.parse(localStorage.getItem('zenshin_v23')||'{}'); for(var k in s) V23[k]=s[k];}catch(e){}
function save(){try{localStorage.setItem('zenshin_v23', JSON.stringify(V23));}catch(e){}}

/* --- CSS v23 --- */
var css = document.createElement('style');
css.textContent = [
'.v23-online-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px;margin:10px 0}',
'.v23-clan-card{background:var(--c);border:1px solid var(--l);border-radius:12px;padding:10px;position:relative}',
'.v23-clan-card.mine{border:2px solid var(--o)}',
'.v23-clan-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;font-weight:700;font-size:13px}',
'.v23-clan-name{color:var(--o);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%}',
'.v23-online-count{color:var(--g);font-size:12px}',
'.v23-player-row{display:flex;justify-content:space-between;padding:2px 0;font-size:12px}',
'.v23-player-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%}',
'.v23-ago{color:var(--m);font-size:11px;font-family:monospace}',
'.v23-dot-68s{color:#3ddc84}.v23-dot-5m{color:#f0b90b}.v23-dot-15m{color:#8a8a93}',
'.v23-bleed-banner{background:linear-gradient(90deg,rgba(255,77,77,.2),rgba(255,77,77,.05));border:1px solid #ff4d4d;border-radius:10px;padding:10px;margin:8px 0;animation:v23pulse 2s infinite}',
'@keyframes v23pulse{0%,100%{box-shadow:0 0 0 0 rgba(255,77,77,.4)}50%{box-shadow:0 0 20px 4px rgba(255,77,77,.3)}}',
'.v23-img-btn{background:linear-gradient(135deg,#a855f7,#6366f1);color:#fff;border:none;border-radius:8px;padding:6px 14px;font-size:12px;cursor:pointer;margin:2px;font-weight:600}',
'.v23-img-btn:hover{opacity:.85}',
'.v23-tg-help{background:var(--c);border:1px solid #229ed9;border-radius:10px;padding:12px;margin:8px 0;font-size:12px}',
'.v23-tg-help a{color:#229ed9;text-decoration:none}',
'.v23-tg-help code{background:var(--bg);padding:2px 6px;border-radius:4px;font-family:monospace}',
/* temas extra v23 */
'body.theme-akatsuki{--o:#ff4d4d;--r:#c0392b;--bg:#1a0505;--c:#2a0a0a}',
'body.theme-pain{--o:#f0b90b;--r:#ff9f43;--bg:#0a0a1a;--c:#141428}',
'body.theme-orochimaru{--o:#a3e635;--r:#65a30d;--bg:#0a1a05;--c:#14280a}',
'body.theme-minato{--o:#60a5fa;--r:#3b82f6;--bg:#050a1a;--c:#0a1428}',
'body.theme-tsunade{--o:#f472b6;--r:#ec4899;--bg:#1a0a14;--c:#280a1e}',
'body.theme-rocklee{--o:#fb923c;--r:#ea580c;--bg:#1a1205;--c:#281c0a}',
'body.theme-sakura{--o:#f9a8d4;--r:#ec4899;--bg:#1a0a12;--c:#280a1a}',
'body.theme-gaara{--o:#c084fc;--r:#a855f7;--bg:#120a1a;--c:#1e0a28}'
].join('\n');
document.head.appendChild(css);

/* --- Sonido de BLEED (goteo) --- */
function bleedSound(){
    if(!window.V22 || !V22.soundOn) return;
    var a = ac(); if(!a) return;
    [400, 350, 300, 250].forEach(function(f,i){
        setTimeout(function(){
            var o = a.createOscillator(), g = a.createGain();
            o.type='sine'; o.frequency.value=f;
            g.gain.setValueAtTime(0, a.currentTime);
            g.gain.linearRampToValueAtTime(V22.vol*0.25, a.currentTime+0.02);
            g.gain.exponentialRampToValueAtTime(0.001, a.currentTime+0.15);
            o.connect(g); g.connect(a.destination); o.start(); o.stop(a.currentTime+0.2);
        }, i*180);
    });
}

/* --- Panel ONLINE NOW --- */
var onlinePanel = document.createElement('div');
onlinePanel.id = 'v23online';
onlinePanel.className = 'v23-panel';
onlinePanel.innerHTML = '<h3 style="color:var(--o);margin:0 0 8px">⚡ ONLINE AHORA <span id="v23oncount" style="color:var(--g);font-size:14px">cargando...</span></h3><div class="v23-online-grid" id="v23grid"></div>';
// Insertar despues de la barra v22
var ref = document.querySelector('.v22-panel') || document.querySelector('nav');
if(ref) ref.parentNode.insertBefore(onlinePanel, ref.nextSibling);
else document.body.appendChild(onlinePanel);

function agoBadge(sec){
    sec = parseInt(sec);
    if(sec < 120) return '<span class="v23-dot-68s">● '+sec+'s</span>';
    if(sec < 600) return '<span class="v23-dot-5m">● '+Math.round(sec/60)+'m</span>';
    return '<span class="v23-dot-15m">● '+Math.round(sec/60)+'m</span>';
}
function loadOnline(){
    fetch('/online').then(function(r){return r.json()}).then(function(d){
        var og = d.online||{};
        document.getElementById('v23oncount').textContent = Object.keys(og).length+' clanes · '+(d.total||0)+' jugadores';
        var grid = document.getElementById('v23grid');
        var html = '';
        var clans = Object.keys(og).slice(0, 12);
        clans.forEach(function(cnm, i){
            var players = og[cnm];
            var mine = cnm === (window.V22_MINE||'');
            html += '<div class="v23-clan-card'+(mine?' mine':'')+'">';
            html += '<div class="v23-clan-header"><span class="v23-clan-name">#'+(i+1)+' '+cnm+'</span><span class="v23-online-count">● '+players.length+'</span></div>';
            players.slice(0,7).forEach(function(p){
                html += '<div class="v23-player-row"><span class="v23-player-name">'+(p.name||'').replace(/</g,'&lt;')+'</span><span class="v23-ago">'+agoBadge(p.ago||0)+'</span></div>';
            });
            if(players.length > 7) html += '<div style="color:var(--m);font-size:11px;text-align:center">+'+(players.length-7)+' más...</div>';
            html += '</div>';
        });
        if(!clans.length) html = '<p style="color:var(--m);text-align:center;padding:20px">Esperando datos de jugadores... (configura la URL de miembros)</p>';
        grid.innerHTML = html;
    }).catch(function(){});
}
loadOnline();
setInterval(loadOnline, 10000);

/* --- Banner de BLEED --- */
var bleedBanner = document.createElement('div');
bleedBanner.id = 'v23bleed';
bleedBanner.style.display = 'none';
bleedBanner.className = 'v23-bleed-banner';
bleedBanner.innerHTML = '<b style="color:#ff4d4d">🩸 ALERTA SANGRANDO:</b> <span id="v23bleedtxt"></span>';
var ref2 = document.querySelector('.v22-panel') || document.querySelector('nav');
if(ref2) ref2.parentNode.insertBefore(bleedBanner, ref2.nextSibling);

/* --- Botones de imagen --- */
var imgBar = document.createElement('div');
imgBar.className = 'v22-panel';
imgBar.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:8px';
imgBar.innerHTML = [
'<b style="color:#a855f7">🖼️ Imagen compartible:</b>',
'<button class="v23-img-btn" data-t="ranking">🏆 Ranking</button>',
'<button class="v23-img-btn" data-t="bleed">🩸 Sangrando</button>',
'<button class="v23-img-btn" data-t="online">⚡ Online</button>',
'<button class="v23-img-btn" data-t="attacks">⚔️ Ataques</button>',
'<button class="v22-btn" id="v23copy">📋 Copiar</button>',
'<button class="v22-btn" id="v23dl">⬇️ Descargar PNG</button>',
'<span id="v23imgstatus" style="color:var(--m);font-size:11px"></span>'
].join(' ');
var ref3 = document.querySelector('.v22-panel') || document.querySelector('nav');
if(ref3) ref3.parentNode.insertBefore(imgBar, ref3.nextSibling);
var currentImgType = 'ranking';
var currentImgUrl = null;
function genImg(type){
    currentImgType = type;
    currentImgUrl = '/card.png?type='+type+'&_='+Date.now();
    document.getElementById('v23imgstatus').textContent = 'Generando '+type+'...';
    var img = new Image();
    img.onload = function(){ document.getElementById('v23imgstatus').textContent = '✅ '+type+' lista ('+Math.round(img.width)+'x'+img.height+')'; };
    img.onerror = function(){ document.getElementById('v23imgstatus').textContent = '❌ Error al generar'; };
    img.src = currentImgUrl;
}
imgBar.querySelectorAll('.v23-img-btn').forEach(function(b){
    b.onclick = function(){ genImg(b.dataset.t); };
});
document.getElementById('v23copy').onclick = function(){
    if(!currentImgUrl){ alert('Primero genera una imagen'); return; }
    fetch(currentImgUrl).then(function(r){return r.blob()}).then(function(blob){
        if(navigator.clipboard && window.ClipboardItem){
            navigator.clipboard.write([new ClipboardItem({'image/png': blob})]).then(function(){
                document.getElementById('v23imgstatus').textContent = '✅ Copiado al portapapeles! Pega en cualquier chat';
            }).catch(function(){
                document.getElementById('v23imgstatus').textContent = 'Copia no soportada, usa Descargar';
            });
        } else {
            document.getElementById('v23imgstatus').textContent = 'Tu navegador no soporta copiar imagen. Usa Descargar.';
        }
    });
};
document.getElementById('v23dl').onclick = function(){
    if(!currentImgUrl){ alert('Primero genera una imagen'); return; }
    var a = document.createElement('a'); a.href = currentImgUrl; a.download = 'zenshin_'+currentImgType+'.png'; a.click();
};
genImg('ranking');

/* --- Ayuda de Telegram --- */
var tgHelp = document.createElement('div');
tgHelp.className = 'v23-tg-help';
tgHelp.innerHTML = '<b style="color:#229ed9">🤖 Bot de Telegram (comandos):</b> Crea tu bot con <a href="https://t.me/BotFather" target="_blank">@BotFather</a> (<code>/newbot</code>), pega el token en Conexión. Luego escribe <code>/start</code> a tu bot. Comandos: <code>/status</code> <code>/top</code> <code>/bleed</code> <code>/online</code> <code>/attacks</code> <code>/predict</code> <code>/card</code> <code>/help</code> — El bot te responde con datos en vivo y hasta te ENVÍA LA IMAGEN.';
var ref4 = document.querySelector('.v22-panel') || document.querySelector('nav');
if(ref4) ref4.parentNode.insertBefore(tgHelp, ref4.nextSibling);

/* --- Más temas (agregar dots) --- */
var moreThemes = {akatsuki:'#ff4d4d', pain:'#f0b90b', orochimaru:'#a3e635', minato:'#60a5fa', tsunade:'#f472b6', rocklee:'#fb923c', sakura:'#f9a8d4', gaara:'#c084fc'};
var themeContainer = document.getElementById('v22themes');
if(themeContainer){
    Object.keys(moreThemes).forEach(function(t){
        var dot = document.createElement('span');
        dot.className = 'v22-theme-dot'; dot.dataset.t = t; dot.style.background = moreThemes[t];
        dot.title = t;
        dot.onclick = function(){ applyTheme(t); };
        themeContainer.appendChild(dot);
    });
}

/* --- Detectar bleed_alert events para sonido especial --- */
var lastBleed = 0;
setInterval(function(){
    fetch('/quick').then(function(r){return r.json()}).then(function(d){
        var evs = d.events||[];
        // bleed banner
        fetch('/bleed').then(function(r2){return r2.json()}).then(function(bd){
            var bl = bd.bleed||{};
            var keys = Object.keys(bl);
            if(keys.length){
                var txt = keys.slice(0,3).map(function(k){return k+' -'+Math.round(bl[k].total)}).join(', ');
                if(keys.length>3) txt += ' y '+(keys.length-3)+' más';
                document.getElementById('v23bleedtxt').textContent = txt+' (total: -'+Math.round(bd.total||0)+' REP)';
                bleedBanner.style.display = 'block';
            } else {
                bleedBanner.style.display = 'none';
            }
        }).catch(function(){});
        // sonido de bleed
        evs.forEach(function(e){
            if(e.t === 'bleed_alert' && (!lastBleed || e.ts > lastBleed)){
                lastBleed = e.ts || lastBleed;
                bleedSound();
                if(window.V22 && V22.voice) speak('Alerta. '+ (e.clan||'') +' esta sangrando. Perdio '+Math.abs(e.amt||0)+' reputacion');
                if(window.V22 && V22.vibrate && navigator.vibrate) navigator.vibrate([100,50,100,50,100,50,200]);
            }
        });
    }).catch(function(){});
}, 4000);

/* Bienvenida v23 */
setTimeout(function(){
    if(window.toast) toast('🔥 v23 Sabio de los Seis Caminos · Imagenes + Bleed + Online + Bot Telegram activados');
    genImg('ranking');
}, 2500);

})();

</script></html>"""

# ==================== v22: FUNCIONES BACKEND NUEVAS ====================
def tg_notify(text):
    if not TELEGRAM.get("token") or not TELEGRAM.get("chat"): return
    with TGL:
        TG_Q.append(text)
        if len(TG_Q) > 50: del TG_Q[:-40]

def telegram_worker():
    while True:
        with TGL:
            if not TG_Q:
                TGL.release() if False else None
                msg = None
            else:
                msg = TG_Q.pop(0)
        if msg is None:
            time.sleep(2); continue
        try:
            body = json.dumps({"chat_id": TELEGRAM["chat"], "text": msg[:4000], "parse_mode": "Markdown", "disable_web_page_preview": True}).encode()
            req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % TELEGRAM["token"], data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:
            LOG.append([time.time(), "telegram: " + str(e)[:60]])
        time.sleep(0.4)

def notify_all(text, kind=None):
    """Dispara Discord + Telegram + marca para sonido en el navegador."""
    notify(text, kind)
    tg_notify(text)

def csv_export(kind):
    """Exporta datos a CSV descargable."""
    import io
    buf = io.StringIO()
    w = lambda *a: buf.write(";".join('"%s"' % str(x).replace('"', "'") for x in a) + "\n")
    now = time.strftime("%Y-%m-%d %H:%M")
    if kind == "clans":
        w("Rango", "Clan", "Master", "Miembros", "Reputacion", "Amenaza")
        for c in sorted(state["clans"], key=lambda x: x["rank"]):
            w(c["rank"], c["name"], c["master"], c["members"], c["rep"], THREAT.get(c["name"], {}).get("score", 0))
    elif kind == "pvp":
        w("Rango", "Jugador", "Puntos", "Ganadas", "Perdidas")
        for p in state["pvp"]: w(p["rank"], p["name"], p["score"], p["w"], p["l"])
    elif kind == "pve":
        w("Rango", "Jugador", "Puntos")
        for p in state["pve"]: w(p["rank"], p["name"], p["score"])
    elif kind == "events":
        w("Timestamp", "Tipo", "Clan", "Detalle", "Cantidad")
        for e in state["events"][-500:]:
            w(time.strftime("%H:%M:%S", time.localtime(e.get("t", 0))), e.get("k", ""), e.get("clan", ""), e.get("txt", "")[:60], e.get("amt", 0))
    else:
        w("Seccion no valida. Usa ?type=clans|pvp|pve|events")
    return buf.getvalue().encode("utf-8-sig")

def clan_compare(a, b):
    """Comparacion lado a lado de dos clanes."""
    ca = next((c for c in state["clans"] if c["name"].lower() == a.lower()), None)
    cb = next((c for c in state["clans"] if c["name"].lower() == b.lower()), None)
    if not ca or not cb: return {"err": "Clan no encontrado", "found": [c["name"] for c in state["clans"] if a.lower() in c["name"].lower() or b.lower() in c["name"].lower()][:10]}
    def stats(c):
        nm = c["name"]; g = sum(v for k, v in REC.get("gain", {}).items() if k == nm)
        l = sum(v for k, v in REC.get("loss", {}).items() if k == nm)
        return dict(rank=c["rank"], name=nm, master=c["master"], members=c["members"], rep=c["rep"],
                    threat=THREAT.get(nm, {}).get("score", 0), level=THREAT.get(nm, {}).get("level", "-"),
                    gain_today=g, loss_today=l, net=g - l, rate=PRED.get(nm, {}).get("rate", 0),
                    streak=STREAK.get(nm, {}).get("n", 0), pred_rank=PRED.get(nm, {}).get("rank", "?"))
    return {"a": stats(ca), "b": stats(cb), "rep_diff": abs(ca["rep"] - cb["rep"]), "rank_diff": abs(ca["rank"] - cb["rank"])}

def predict_rankings():
    """Predice reputacion y ranking al final de la temporada segun ritmo actual."""
    now = time.time(); end = SEASON.get("manual") or (SEASON.get("dates") and time.mktime(time.strptime(SEASON["dates"][-1][:16], "%Y-%m-%dT%H:%M"))) or (now + 7 * 86400)
    hours_left = max(0.1, (end - now) / 3600)
    rates = {}
    for c in state["clans"]:
        nm = c["name"]; ser = SER["f"]["d"].get(nm, [])
        if len(ser) >= 2:
            t0, r0 = SER["f"]["t"][max(0, len(ser) - 60)], ser[-60] if len(ser) >= 60 else ser[0]
            dt = max(300, now - t0); rates[nm] = (c["rep"] - r0) / dt * 3600  # rep/hora
        else:
            rates[nm] = 0
    proj = sorted([(c["name"], c["rep"] + rates[c["name"]] * hours_left, rates[c["name"]]) for c in state["clans"]], key=lambda x: -x[1])
    rank_of = {nm: i + 1 for i, (nm, _, _) in enumerate(proj)}
    for nm, rep_proj, rate in proj:
        PRED[nm] = dict(rep=int(rep_proj), rank=rank_of[nm], rate=round(rate, 1), hours_left=round(hours_left, 1))
    return dict(end=end, hours_left=round(hours_left, 1), top=[dict(name=n, rep_proj=int(r), rate=round(rt, 1), rank=rank_of[n]) for n, r, rt in proj[:15]], mine=MINE[0])

def calc_threat(clans):
    """Puntaje de amenaza 0-100 basado en ataques recientes, ganancia promedio, miembros y racha."""
    now = time.time()
    for c in clans:
        nm = c["name"]; score = 0
        # ataques en ultima hora (desde events)
        recent = [e for e in state["events"][-300:] if e.get("clan") == nm and e.get("t", 0) > now - 3600 and e.get("amt", 0) > 0]
        atk_n = len(recent); atk_tot = sum(e.get("amt", 0) for e in recent)
        score += min(40, atk_n * 4) + min(30, atk_tot / 500)
        score += min(15, (num(c["members"].split("/")[0]) if "/" in str(c["members"]) else 0) / 2)
        st = STREAK.get(nm, {})
        if st and now - st.get("last", 0) < 600: score += min(15, st.get("n", 0) * 3)
        score = min(100, int(score))
        level = "S" if score >= 80 else "A" if score >= 60 else "B" if score >= 40 else "C" if score >= 20 else "D"
        THREAT[nm] = dict(score=score, level=level, attacks=atk_n, total=atk_tot)
    return THREAT

def backup_data():
    """Backup completo de configuracion e historial (descargable)."""
    return dict(version=22, ts=time.time(), cfg=CFG, sound=SOUND, theme=THEME[0], mine=MINE[0],
                mon=MON, rivals=RIVALS, webhook=WEBHOOK, telegram=dict(token=TELEGRAM["token"][:6] + "...", chat=TELEGRAM["chat"]),
                members_url=MEMBERS_URL, poll=POLL, season_manual=SEASON.get("manual"),
                history=json.loads(open(HIST, encoding="utf-8").read()) if os.path.exists(HIST) else {},
                extras=EXTRAS, achv=ACHV)

def restore_data(d):
    """Restaura configuracion desde backup."""
    global WEBHOOK, POLL
    if "cfg" in d: CFG.update({k: int(v) for k, v in d["cfg"].items() if k in CFG})
    if "sound" in d: SOUND.update(d["sound"])
    if "theme" in d: THEME[0] = d["theme"]
    if "mine" in d: MINE[0] = d["mine"]
    if "mon" in d: MON[:] = d["mon"][:5]
    if "rivals" in d: RIVALS[:] = d["rivals"][:10]
    if "webhook" in d: WEBHOOK = d["webhook"]
    if "poll" in d: POLL = d["poll"]; delay[0] = POLL
    if "season_manual" in d: SEASON["manual"] = d["season_manual"]
    if "extras" in d: EXTRAS.update(d["extras"]); save_extras()
    return dict(ok=True)

def achv_check():
    """Desbloquea logros segun estadisticas acumuladas."""
    now = time.time()
    if not ACHV.get("first_ts"): ACHV["first_ts"] = now
    ACHV["hours"] = int((now - ACHV["first_ts"]) / 3600)
    ACHV["attacks"] = sum(1 for e in state["events"] if e.get("amt", 0) > 0 and e.get("k") in ("hit", "gain"))
    ACHV["losses"] = sum(1 for e in state["events"] if e.get("amt", 0) < 0)
    def unlock(key, name, desc):
        if key not in ACHV["unlocked"]:
            ACHV["unlocked"].append(key)
            ev_add(dict(ts=now, t="achv", k="achv", txt="LOGRO DESBLOQUEADO: %s — %s" % (name, desc), amt=0, clan="Sistema"))
    if ACHV["hours"] >= 1: unlock("h1", "Genin Vigilante", "1 hora de monitoreo continuo")
    if ACHV["hours"] >= 24: unlock("h24", "Chunny Nocturno", "24 horas sin dormir vigilando")
    if ACHV["attacks"] >= 100: unlock("a100", "Sensor de Chakra", "100 ataques detectados")
    if ACHV["attacks"] >= 1000: unlock("a1000", "Byakugan Activado", "1000 ataques detectados")
    if ACHV["losses"] >= 50: unlock("l50", "Cicatriz de Guerra", "50 perdidas de reputacion registradas")
    if len(state["clans"]) >= 30: unlock("c30", "Maestro de Clanes", "30 clanes en el ranking")
    if any(THREAT.get(c["name"], {}).get("score", 0) >= 80 for c in state["clans"]): unlock("s", "Peligro Rango S", "Detectado un clan con amenaza nivel S")

def adaptive_poller():
    """Ajusta la velocidad de lectura segun salud del sitio: si falla, frena; si hay ataques, acelera."""
    while True:
        time.sleep(5)
        now = time.time()
        if now < ADAPT["slow_until"]:
            delay[0] = max(delay[0], 5.0); continue
        # si hubo ataques en los ultimos 60s, acelerar
        recent_atk = any(e.get("ts", 0) > now - 60 and e.get("amt", 0) > 50 for e in state["events"][-50:])
        target = 0.5 if recent_atk else ADAPT["base"]
        delay[0] = target
        # si muchos fallos, frenar
        if FETCH_STATS["fail"] > ADAPT["fails"] + 3:
            ADAPT["fails"] = FETCH_STATS["fail"]; ADAPT["slow_until"] = now + 30
            LOG.append([now, "polling adaptativo: sitio lento, frenando a 5s por 30s"])

def update_streaks(now):
    """Actualiza rachas de ataque: si un clan ataca seguido, sube la racha."""
    for c in state["clans"]:
        nm = c["name"]
        recent = [e for e in state["events"][-20:] if e.get("clan") == nm and e.get("amt", 0) > 0 and e.get("ts", 0) > now - 300]
        if recent:
            st = STREAK.setdefault(nm, dict(n=0, start=now, last=0))
            if now - st["last"] < 120: st["n"] += 1
            else: st["n"] = 1; st["start"] = now
            st["last"] = now
        elif nm in STREAK and now - STREAK[nm]["last"] > 600:
            del STREAK[nm]


# ==================== v23: FUNCIONES BACKEND NUEVAS ====================
def online_grouped():
    """Jugadores online agrupados por clan (como el panel ONLINE NOW): {clan: [{name, ago, rep30}], ...}"""
    now = time.time(); out = {}
    for c in state["clans"]:
        nm = c["name"]; members = mem_cache.get(nm)
        if not members: continue
        _, ml = members if isinstance(members, (list, tuple)) and len(members) == 2 else (None, members)
        if not isinstance(ml, dict): continue
        active = []
        for pname, data in ml.items():
            if not isinstance(data, dict): continue
            last = data.get("last", 0) or data.get("t", 0) or 0
            ago = now - last
            if ago < 900:  # 15 min = conectado
                active.append(dict(name=pname, ago=ago, rep30=data.get("r30", 0) or 0))
        if active:
            active.sort(key=lambda x: x["ago"])
            out[nm] = active
    return dict(sorted(out.items(), key=lambda x: -len(x[1])))

def ago_str(s):
    s = int(s)
    if s < 90: return "%ds" % s
    if s < 5400: return "%dm" % round(s/60)
    return "%.1fh" % (s/3600)

def bleed_now():
    """Clanes sangrando ahora (perdida de REP sin atacante identificado en ultimos 10 min)."""
    now = time.time(); out = {}
    for e in state["events"][-200:]:
        if e.get("t") == "bleed" and not e.get("lv") and now - e.get("ts", 0) < 600:
            nm = e.get("clan", "?"); b = out.setdefault(nm, dict(total=0, n=0, last=0, rep=0, rank=0))
            b["total"] += abs(e.get("amt", 0)); b["n"] += 1; b["last"] = e.get("ts", 0)
            b["rep"] = e.get("rep", 0); b["rank"] = e.get("rank", 0)
    return out

def bleed_alert(cl, amt, rep, rank, now):
    """Alerta dedicada de BLEED: Discord embed + Telegram + sonido especial."""
    b = BLEED_ST.setdefault(cl, dict(total=0, n=0, first=now, last=0))
    b["total"] += abs(amt); b["n"] += 1; b["last"] = now
    mine = cl == MINE[0]
    if b["total"] >= CFG.get("thr", 100) and (not b.get("sent") or now - b["sent"] >= 180):
        first = not b.get("sent"); b["sent"] = now
        msg = "🩸 **%s**%s SANGRANDO · -%s en %s (×%d) · REP **%s** · #%d" % (cl, " (tu clan)" if mine else "", fmt(b["total"]), dur(now - b["first"]), b["n"], fmt(rep), rank)
        notify(msg, "hit" if mine else "any")
        # evento especial para sonido de bleed en el navegador
        ev_add(dict(ts=now, t="bleed_alert", k="bleed_alert", clan=cl, amt=-abs(amt), txt=msg, rep=rep, rank=rank))

# ---- Generador de imagenes PNG (Pillow) ----
def _font(size, bold=False):
    paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
    for p in paths:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except Exception: pass
    return ImageFont.load_default()

def generate_card(ctype="ranking", clan=None):
    """Genera una imagen PNG compartible. Tipos: ranking, bleed, online, attacks."""
    if not PIL_OK: return None
    W, H = (1080, 1350) if ctype == "online" else (1080, 720)
    bg = (15, 15, 21); card = (26, 26, 32); accent = (255, 159, 67); red = (255, 77, 77); green = (61, 220, 132); txt = (232, 232, 234); muted = (138, 138, 147)
    img = Image.new("RGB", (W, H), bg); d = ImageDraw.Draw(img)
    f_title = _font(42, True); f_sub = _font(26); f_row = _font(28); f_small = _font(22); f_badge = _font(18)
    # Header
    d.rectangle([0, 0, W, 110], fill=(26, 26, 32))
    d.rectangle([0, 106, W, 110], fill=accent)
    titles = {"ranking": "🏆 TOP CLanes · Ninja Zenshin", "bleed": "🩸 CLANES SANGRANDO", "online": "⚡ JUGADORES CONECTADOS", "attacks": "⚔️ ÚLTIMOS ATAQUES"}
    d.text((30, 30), titles.get(ctype, titles["ranking"]), font=f_title, fill=accent)
    d.text((30, 78), time.strftime("%d/%m/%Y %H:%M") + " · Tracker v23", font=f_small, fill=muted)
    y = 140
    if ctype == "ranking":
        clans = sorted(state["clans"], key=lambda x: x["rank"])[:12]
        for i, c in enumerate(clans):
            cy = y + i * 46
            if i % 2 == 0: d.rectangle([20, cy, W-20, cy+42], fill=card)
            rank_color = (255, 215, 0) if i < 3 else txt
            d.text((35, cy+8), "#%d" % c["rank"], font=f_row, fill=rank_color)
            nm = c["name"][:28]
            d.text((110, cy+8), nm, font=f_row, fill=green if c["name"]==MINE[0] else txt)
            d.text((W-280, cy+8), fmt(c["rep"]), font=f_row, fill=accent)
            th = THREAT.get(c["name"], {}).get("level", "-")
            d.text((W-120, cy+10), th, font=f_small, fill=red if th in ("S","A") else green)
    elif ctype == "bleed":
        bl = bleed_now()
        if not bl:
            d.text((30, 200), "✅ Ningún clan sangra ahora mismo", font=f_sub, fill=green)
        else:
            for i, (nm, b) in enumerate(sorted(bl.items(), key=lambda x: -x[1]["total"])[:12]):
                cy = y + i * 46
                d.rectangle([20, cy, W-20, cy+42], fill=card if i%2 else (40, 20, 20))
                d.text((35, cy+8), "🩸", font=f_row, fill=red)
                d.text((90, cy+8), nm[:30], font=f_row, fill=txt)
                d.text((W-300, cy+8), "-%s REP" % fmt(b["total"]), font=f_row, fill=red)
                d.text((W-120, cy+10), "×%d" % b["n"], font=f_small, fill=muted)
    elif ctype == "online":
        og = online_grouped()
        total = sum(len(v) for v in og.values())
        d.text((30, y-30), "%d clanes · %d jugadores online" % (len(og), total), font=f_sub, fill=green)
        clans_list = list(og.items())[:12]
        for i, (cnm, players) in enumerate(clans_list):
            col = i % 3; row = i // 3
            cx = 20 + col * 350; cy = y + row * 200
            d.rounded_rectangle([cx, cy, cx+330, cy+185], radius=12, fill=card, outline=accent if cnm==MINE[0] else (60,60,70), width=2)
            d.text((cx+12, cy+8), "#%d %s" % (i+1, cnm[:18]), font=f_small, fill=accent)
            d.text((cx+280, cy+8), "●%d" % len(players), font=f_small, fill=green)
            for j, p in enumerate(players[:6]):
                py = cy + 38 + j * 22
                color = green if p["ago"] < 120 else (255,215,0) if p["ago"] < 300 else muted
                d.text((cx+12, py), p["name"][:20], font=f_badge, fill=color)
                d.text((cx+240, py), ago_str(p["ago"]), font=f_badge, fill=muted)
    elif ctype == "attacks":
        evs = [e for e in reversed(state["events"][-60:]) if e.get("amt", 0) != 0][:12]
        for i, e in enumerate(evs):
            cy = y + i * 46
            if i % 2 == 0: d.rectangle([20, cy, W-20, cy+42], fill=card)
            tstr = time.strftime("%H:%M", time.localtime(e.get("ts", 0)))
            d.text((35, cy+10), tstr, font=f_small, fill=muted)
            amt = e.get("amt", 0); c = green if amt > 0 else red
            d.text((130, cy+8), (e.get("clan","?")[:25]), font=f_row, fill=txt)
            d.text((W-200, cy+8), "%s%s" % ("+" if amt>0 else "", fmt(amt)), font=f_row, fill=c)
    # Footer
    d.rectangle([0, H-50, W, H], fill=card)
    d.text((30, H-38), "Generado por Ninja Zenshin Tracker v23 · Modo Sabio de los Seis Caminos", font=f_small, fill=muted)
    import io as _io
    buf = _io.BytesIO(); img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# ---- Bot de Telegram interactivo (comandos) ----
def tg_send(chat_id, text, img=None):
    try:
        if img:
            boundary = "----zenshin%d" % int(time.time())
            body = ("--%s\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n%s\r\n" % (boundary, chat_id)).encode()
            body += ("--%s\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"card.png\"\r\nContent-Type: image/png\r\n\r\n" % boundary).encode() + img + ("\r\n--%s--\r\n" % boundary).encode()
            req = urllib.request.Request("https://api.telegram.org/bot%s/sendPhoto" % TELEGRAM["token"], data=body, headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
        else:
            body = json.dumps({"chat_id": chat_id, "text": text[:4000], "parse_mode": "Markdown", "disable_web_page_preview": True}).encode()
            req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % TELEGRAM["token"], data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        LOG.append([time.time(), "tg_send: " + str(e)[:60]])

def tg_handle_cmd(chat_id, text):
    text = (text or "").lower().strip()
    clans = sorted(state["clans"], key=lambda x: x["rank"])
    if text.startswith("/start") or text.startswith("/help"):
        tg_send(chat_id, TG_HELP)
    elif text.startswith("/status"):
        top5 = "\n".join("*#%d* %s — %s REP" % (c["rank"], c["name"], fmt(c["rep"])) for c in clans[:5])
        bl = bleed_now(); online = online_grouped()
        tg_send(chat_id, "⚡ *Estado en vivo*\n\n%s\n\n🩸 Sangrando: %d clanes\n👥 Online: %d jugadores\n⏱️ Uptime: %s" % (top5, len(bl), sum(len(v) for v in online.values()), dur(time.time()-START)))
    elif text.startswith("/top"):
        top = "\n".join("%d. %s — *%s*" % (c["rank"], c["name"], fmt(c["rep"])) for c in clans[:10])
        tg_send(chat_id, "🏆 *Top 10 Clanes*\n\n" + top)
    elif text.startswith("/bleed"):
        bl = bleed_now()
        if not bl: tg_send(chat_id, "✅ Ningún clan sangra ahora")
        else:
            msg = "\n".join("🩸 %s: -*%s* (×%d)" % (nm, fmt(b["total"]), b["n"]) for nm, b in sorted(bl.items(), key=lambda x:-x[1]["total"])[:8])
            tg_send(chat_id, "🩸 *Clanes sangrando*\n\n" + msg)
    elif text.startswith("/online"):
        og = online_grouped()
        msg = "\n".join("● *%s* (%d)" % (nm, len(pl)) for nm, pl in list(og.items())[:8])
        tg_send(chat_id, "⚡ *Online ahora* (%d jugadores)\n\n%s" % (sum(len(v) for v in og.values()), msg))
    elif text.startswith("/attacks"):
        evs = [e for e in reversed(state["events"][-40:]) if e.get("amt",0)!=0][:8]
        msg = "\n".join("%s %s %s%s" % (time.strftime("%H:%M", time.localtime(e.get("ts",0))), e.get("clan","?")[:20], "+" if e.get("amt",0)>0 else "", fmt(e.get("amt",0))) for e in evs)
        tg_send(chat_id, "⚔️ *Últimos ataques*\n\n" + (msg or "Sin datos"))
    elif text.startswith("/predict"):
        p = predict_rankings()
        msg = "\n".join("#%d %s → %s (+%s/h)" % (r["rank"], r["name"][:18], fmt(r["rep_proj"]), r["rate"]) for r in p.get("top", [])[:5])
        tg_send(chat_id, "🔮 *Predicciones fin de temporada* (%.1fh restantes)\n\n%s" % (p.get("hours_left", 0), msg))
    elif text.startswith("/card"):
        png = generate_card("ranking")
        if png: tg_send(chat_id, "🏆 Imagen del ranking:", img=png)
        else: tg_send(chat_id, "No se pudo generar la imagen (Pillow no disponible)")
    else:
        tg_send(chat_id, "Comando no reconocido. Escribe /help para ver los disponibles.")

def telegram_bot_loop():
    """Polling de comandos del bot de Telegram (getUpdates)."""
    while True:
        time.sleep(3)
        if not TELEGRAM.get("token"): continue
        try:
            url = "https://api.telegram.org/bot%s/getUpdates?offset=%d&timeout=10" % (TELEGRAM["token"], TG_UPD["offset"])
            r = json.loads(urllib.request.urlopen(url, timeout=15).read().decode())
            for upd in r.get("result", []):
                TG_UPD["offset"] = max(TG_UPD["offset"], upd["update_id"] + 1)
                msg = upd.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                if chat_id and text.startswith("/"):
                    if not TELEGRAM.get("chat"): TELEGRAM["chat"] = str(chat_id)  # auto-configura el chat al primer comando
                    threading.Thread(target=tg_handle_cmd, args=(chat_id, text), daemon=True).start()
        except Exception as e:
            if "urlopen" not in str(e): LOG.append([time.time(), "tg_bot: " + str(e)[:50]])
            time.sleep(5)

# ---- Discord con embeds ricos ----
def discord_embed(text, color=16753984):
    """Envia un embed rico a Discord (si esta habilitado)."""
    if not WEBHOOK or not DISCORD_EMBED[0]: return False
    try:
        embed = {"title": "⚡ Ninja Zenshin Alert", "description": text[:2000], "color": color, "timestamp": datetime.datetime.utcnow().isoformat(), "footer": {"text": "Tracker v23 · Sabio de los Seis Caminos"}}
        body = json.dumps({"embeds": [embed]}).encode()
        urllib.request.urlopen(urllib.request.Request(WEBHOOK, data=body, headers={"Content-Type": "application/json"}), timeout=10).read()
        return True
    except Exception: return False


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        try: self._get()
        except (OSError, ValueError): pass

    def _get(self):
        if self.path.startswith("/stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache"); self.end_headers()
            lastv, lu = -1, -1; CLIENTS[0] += 1
            try:
                while True:
                    with cond:
                        if ver[0] == lastv: cond.wait(20)
                        lastv = ver[0]
                    body, lu = snap(lu)   # la 1a vez todo; despues solo eventos nuevos (mas liviano = mas rapido)
                    self.wfile.write(("data: " + body + "\n\n").encode()); self.wfile.flush()
            finally: CLIENTS[0] -= 1
        elif self.path.startswith("/burn?"):
            q = parse_qs(urlparse(self.path).query); cl = q.get("clan", [""])[0]; c = next((x for x in state["clans"] if x["name"] == cl), None)
            if c and EP and (cl not in mem_cache or time.time() - mem_cache[cl][0] > 2.5):   # Top Burn abierto: lee los miembros al momento (en vivo)
                try: refresh_members(c)
                except Exception: pass
            body = json.dumps(burn_detail(cl, int(q.get("sec", ["-1"])[0]))).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/clan?"):
            q = parse_qs(urlparse(self.path).query); body = json.dumps(clan_detail(q.get("name", [""])[0], int(q.get("range", ["21600"])[0]))).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/heat?"):
            body = json.dumps(heat.get(parse_qs(urlparse(self.path).query).get("clan", [""])[0], [[0] * 24 for _ in range(7)])).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/all"):
            body = json.dumps({n: dict(t=v[0], members=v[1]) for n, v in list(mem_cache.items())}).encode()
            if "gzip" in (self.headers.get("Accept-Encoding") or ""):
                body = gzip.compress(body); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Encoding", "gzip"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            else:
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/member?"):
            q = parse_qs(urlparse(self.path).query); body = json.dumps(mstats.get(q.get("clan", [""])[0] + "|" + q.get("who", [""])[0], {})).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/members"):
            n = parse_qs(urlparse(self.path).query).get("name", [""])[0]
            c = next((x for x in state["clans"] if x["name"] == n), None); r = {"err": "Clan no encontrado"}
            try:
                if c and not EP: r = {"err": state["mem_msg"] if "Sin auto" in state["mem_msg"] else "Aún no hay conexión con los miembros. Mira la pestaña 🔧 Conexión."}
                elif c: r = {"members": mem_cache[n][1] if n in mem_cache and time.time() - mem_cache[n][0] < 3 else refresh_members(c)}
            except Exception as e: r = {"err": "No se pudo leer: " + str(e)[:100]}
            body = json.dumps(r).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/i18n"):   # diccionario del idioma elegido (se traduce la 1a vez y queda guardado)
            l = parse_qs(urlparse(self.path).query).get("lang", [""])[0]
            r = i18n_state(l) if re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,4})?", l) else {"err": "idioma no valido", "done": False}
            body = json.dumps(r, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/extras"):   # recompensas + tokens + revendedores
            with XL:
                # v20: SIEMPRE fusionar precios y resellers oficiales (garantiza que se muestren)
                _shop = dict(EXTRAS["shop"])
                _tok = [dict(r) for r in _shop.get("tok", []) if str(r.get("price","")) not in ("Auto","—","") and r.get("tok")]
                _have = {r.get("tok") for r in _tok}
                for r in DEFAULT_TOK:
                    if r["tok"] not in _have: _tok.insert(0, dict(r))
                _shop["tok"] = _tok[:12]
                if not _shop.get("res"): _shop["res"] = [dict(r) for r in DEFAULT_RES]
                # v20: fechas de fin garantizadas (fallback si el scraper no las encuentra)
                _now = time.time()
                ARCHIVE.setdefault("cd", {})
                if not ARCHIVE["cd"].get("pve_s4") or ARCHIVE["cd"]["pve_s4"] < _now:
                    ARCHIVE["cd"]["pve_s4"] = _now + 12*86400  # ~12 dias restantes
                if not ARCHIVE["cd"].get("cw") or ARCHIVE["cd"]["cw"] < _now:
                    ARCHIVE["cd"]["cw"] = _now + 7*86400
                body = json.dumps(dict(v=XV[0], rew=rew_view(), shop=_shop, pkgs=pkgs_view(), lb=LBL, kyubi=EXTRAS.get("kyubi") or {"snaps": []}, news=sorted(EXTRAS["news"].values(), key=lambda a: -a.get("id",0))[:24], server=server_stats(), archive=ARCHIVE, payments=PAYMENT_METHODS, bonus_pkgs=BONUS_PACKAGES, kyubi_live=KYUBI_LIVE)).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/export"):   # datos.json para publicar en la pagina web (Netlify)
            body = json.dumps(export_data(), ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Disposition", 'attachment; filename="datos.json"'); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/health"):
            body = json.dumps(dict(ok=True, **server_stats())).encode()
        elif self.path.startswith("/csv"):
            q = parse_qs(urlparse(self.path).query); kind = q.get("type", ["clans"])[0]
            body = csv_export(kind); self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8"); self.send_header("Content-Disposition", 'attachment; filename="zenshin_%s.csv"' % kind)
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        elif self.path.startswith("/compare"):
            q = parse_qs(urlparse(self.path).query); r = clan_compare(q.get("a", [""])[0], q.get("b", [""])[0])
            body = json.dumps(r).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        elif self.path.startswith("/predict"):
            body = json.dumps(predict_rankings()).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        elif self.path.startswith("/threat"):
            calc_threat(state["clans"]); body = json.dumps(dict(threat=THREAT, streaks=STREAK)).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        elif self.path.startswith("/quick"):
            with lock: evs = state["events"][-25:]
            body = json.dumps(dict(events=evs, mine=MINE[0], sound=SOUND, theme=THEME[0], threat={k: v for k, v in list(THREAT.items())[:20]}, ts=time.time())).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-cache"); self.end_headers(); self.wfile.write(body); return
        elif self.path.startswith("/online"):
            body = json.dumps(dict(online=online_grouped(), total=sum(len(v) for v in online_grouped().values()), clans=len(online_grouped()))).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Cache-Control", "no-cache"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        elif self.path.startswith("/bleed"):
            body = json.dumps(dict(bleed=bleed_now(), total=sum(b["total"] for b in bleed_now().values()))).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Cache-Control", "no-cache"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        elif self.path.startswith("/card.png") or self.path.startswith("/card"):
            q = parse_qs(urlparse(self.path).query); ctype = q.get("type", ["ranking"])[0]
            try: png = generate_card(ctype)
            except Exception as _e:
                LOG.append([time.time(), "card: " + str(_e)[:80]]); self.send_response(500); self.end_headers(); return
            if not png:
                self.send_response(503); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Content-Disposition", 'inline; filename="zenshin_%s.png"' % ctype)
            self.send_header("Cache-Control", "no-cache"); self.send_header("Content-Length", str(len(png))); self.end_headers(); self.wfile.write(png); return
        elif self.path.startswith("/backup"):
            body = json.dumps(backup_data()).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Disposition", 'attachment; filename="zenshin_backup.json"'); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return

            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/favicon"):
            ico = b"\x00\x00\x01\x00\x01\x00\x10\x10\x00\x00\x01\x00\x08\x00\x68\x04\x00\x00\x16\x00\x00\x00" + bytes([0x1b,0x1b,0x2b,0xff]*256) + bytes([0]*1024)
            self.send_response(200); self.send_header("Content-Type", "image/x-icon"); self.send_header("Content-Length", str(len(ico))); self.send_header("Cache-Control", "max-age=86400"); self.end_headers(); self.wfile.write(ico)
        elif self.path.startswith("/img/"):   # imagenes subidas por ti (carpeta zenshin_img)
            n = self.path[5:].split("?")[0]; fp = os.path.join(IMGDIR, n)
            if not re.fullmatch(r"[0-9a-f]{16}\.(png|jpg|gif|webp)", n) or not os.path.isfile(fp): self.send_response(404); self.end_headers(); return
            with open(fp, "rb") as f: body = f.read()
            self.send_response(200); self.send_header("Content-Type", "image/" + {"jpg": "jpeg"}.get(n.rsplit(".", 1)[1], n.rsplit(".", 1)[1])); self.send_header("Cache-Control", "max-age=86400"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        else:
            body = PAGE.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-cache"); self.send_header("X-Frame-Options", "SAMEORIGIN"); self.send_header("X-Content-Type-Options", "nosniff"); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        try:
            if self.path.startswith(("/extras", "/upload", "/kyubi")): return self._xpost()
            self._post()
        except Exception as e: LOG.append([time.time(), "config: " + str(e)[:80]]); self.send_response(400); self.end_headers()

    def _xpost(self):   # guardar recompensas / tokens / revendedores, o subir una imagen
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > 8 * 1024 * 1024: raise ValueError("demasiado grande")
        d = json.loads(self.rfile.read(n) or b"{}")
        try: r = {"url": save_upload(d.get("data"))} if self.path.startswith("/upload") else kyubi_post(d) if self.path.startswith("/kyubi") else (extras_post(d) or {"v": XV[0]})
        except ValueError as e: r = {"err": str(e)}
        body = json.dumps(r).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _post(self):
        global MEMBERS_URL, WEBHOOK, POLL
        d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if "webhook" in d: WEBHOOK = d["webhook"].strip(); notify("✅ Ninja Zenshin Tracker conectado")
        if d.get("url", "").strip():
            u = d["url"].strip(); set_ep(dict(m="GET", u=u.replace("{name}", "{id}"), bt=None, by="name" if "{name}" in u else "id"), "Manual: " + u)
        if "sniff" in d: learn(d["sniff"].get("url"), d["sniff"].get("method") or "GET", d["sniff"].get("body"))
        if d.get("retry"): threading.Thread(target=autoconnect, daemon=True).start()
        if "mine" in d: MINE[0] = d["mine"]
        if "alerts" in d: CFG.update({k: int(v) for k, v in d["alerts"].items() if k in CFG})
        if "mon" in d: MON[:] = [x for x in d["mon"] if isinstance(x, str)][:5]
        if "telegram" in d:
            TELEGRAM["token"] = d["telegram"].get("token", "").strip(); TELEGRAM["chat"] = d["telegram"].get("chat", "").strip()
            if TELEGRAM["token"]: tg_notify("✅ Ninja Zenshin Tracker v22 conectado a Telegram")
        if "sound" in d: SOUND.update({k: (int(v) if isinstance(v, (int, float)) else v) for k, v in d["sound"].items() if k in SOUND})
        if "theme" in d: THEME[0] = d["theme"] if d["theme"] in ("kurama", "sharingan", "sage", "rinnegan", "uchija") else "kurama"
        if "rivals" in d: RIVALS[:] = [x for x in d["rivals"] if isinstance(x, str)][:10]
        if d.get("restore"):
            try: restore_data(d["restore"])
            except Exception as e: LOG.append([time.time(), "restore: " + str(e)[:60]])
        if d.get("achv_check"): achv_check()
        if d.get("discord_embed") is not None: DISCORD_EMBED[0] = bool(d["discord_embed"])
        if d.get("bleed_test"): bleed_alert(MINE[0] or "TestClan", -200, 100000, 5, time.time())

        if d.get("poll") in POLLS: POLL = d["poll"]; delay[0] = POLL; wake.set()   # frecuencia de lectura elegida en pantalla
        if d.get("refresh"): tick["all"] = 0; wake.set()                                     # boton de refrescar
        m = re.search(r"(\d+)\s*d\w*\s*(\d+):(\d+)(?::(\d+))?", d.get("season_end", ""))
        if m: SEASON["manual"] = time.time() + int(m[1]) * 86400 + int(m[2]) * 3600 + int(m[3]) * 60 + int(m[4] or 0)
        self.send_response(204); self.end_headers()
    def log_message(self, *a): pass

class Srv(ThreadingHTTPServer):
    daemon_threads = True
    def handle_error(self, request, client_address):
        if not issubclass(sys.exc_info()[0] or Exception, OSError): super().handle_error(request, client_address)

if __name__ == "__main__":
    _av = sys.argv[1:]
    def _arg(k, d):
        return _av[_av.index(k) + 1] if k in _av and _av.index(k) + 1 < len(_av) else d
    try:
        PORT = int(_arg("--port", PORT)); URL = _arg("--url", URL); POLL = float(_arg("--poll", POLL)); POLL = int(POLL) if POLL == int(POLL) else POLL; delay[0] = POLL
    except ValueError: sys.exit("Opciones: --port 8000  --poll 1  --url URL")
    load(); load_extras(); _ip = lan_ip(); LAN[0] = "http://%s:%d" % (_ip, PORT) if _ip else ""
    threading.Thread(target=poll, daemon=True).start(); threading.Thread(target=discord_worker, daemon=True).start(); threading.Thread(target=news_loop, daemon=True).start(); threading.Thread(target=scrape_resellers, daemon=True).start(); threading.Thread(target=telegram_worker, daemon=True).start(); threading.Thread(target=telegram_bot_loop, daemon=True).start(); threading.Thread(target=adaptive_poller, daemon=True).start()
    def _watchdog():
        while True:
            time.sleep(60)
            try:
                if state.get("error") and time.time() - state.get("ts",0) > 120:
                    print("Watchdog: reconectando…"); JAR.clear(); CSRF[0] = ""; KA["on"] = True; wake.set()
            except Exception: pass
    threading.Thread(target=_watchdog, daemon=True).start(); threading.Thread(target=archive_loop, daemon=True).start()
    print("Tracker v23 · SABIO DE LOS SEIS CAMINOS listo -> abre http://localhost:%d  (Ctrl+C para salir)" % PORT)
    if LAN[0]: print("En el celular (misma WiFi): " + LAN[0])
    try: Srv(("", PORT), H).serve_forever()
    except KeyboardInterrupt: print("\nGuardando historial..."); save()