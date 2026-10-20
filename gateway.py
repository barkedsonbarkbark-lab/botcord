"""
gateway.py - minimal realtime Discord Gateway client (bot token, official API).
Gives live messages, typing and voice-state (who is in which voice channel).
Runs in its own thread; reconnects/resumes automatically.
"""
import json
import random
import threading
import time

import websocket

URL = "wss://gateway.discord.gg/?v=10&encoding=json"
# guilds, guild voice states, guild messages, DMs, (+ message content, privileged)
BASE_INTENTS = (1 << 0) | (1 << 7) | (1 << 9) | (1 << 12)
CONTENT_INTENT = 1 << 15
FATAL = {4004: "Invalid bot token", 4010: "Invalid shard", 4011: "Sharding required",
         4012: "Invalid API version", 4013: "Invalid intents"}


class _Closed(Exception):
    def __init__(self, code):
        super().__init__(f"closed {code}")
        self.code = code


class Gateway:
    def __init__(self, token, on_event, on_status):
        self.token = token
        self.on_event = on_event      # (event_name, data) - called from worker thread
        self.on_status = on_status    # (state, text)      - called from worker thread
        self.use_content = True
        self.presence = {"status": "online", "activities": [], "since": None, "afk": False}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ws = None
        self._seq = None
        self._session = None
        self._resume_url = None
        self._ack = True

    # ---- public ----------------------------------------------------- #
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def set_presence(self, status, text=None):
        self.presence = {
            "status": status,
            "activities": [{"name": text, "type": 0}] if text else [],
            "since": None, "afk": False,
        }
        self._send(3, self.presence)

    # ---- internals -------------------------------------------------- #
    def _send(self, op, d, ws=None):
        ws = ws or self._ws
        if not ws:
            return
        try:
            with self._lock:
                ws.send(json.dumps({"op": op, "d": d}))
        except Exception:
            pass

    def _loop(self):
        backoff = 1
        while not self._stop.is_set():
            try:
                self.on_status("connecting", "Connecting...")
                self._run_once()
            except _Closed as e:
                if e.code in FATAL:
                    self.on_status("error", FATAL[e.code])
                    return
                if e.code == 4014:  # disallowed intent -> retry without message content
                    if self.use_content:
                        self.use_content = False
                        self.on_status("nocontent", "Message Content intent is off")
                        continue
                    self.on_status("error", "Intents not allowed")
                    return
                if e.code in (4007, 4009):
                    self._session = None
            except Exception:
                pass
            if self._stop.is_set():
                return
            self.on_status("reconnecting", "Reconnecting...")
            self._stop.wait(min(backoff, 30))
            backoff = min(backoff * 2, 30)

    def _run_once(self):
        url = (self._resume_url + "/?v=10&encoding=json") if (self._session and self._resume_url) else URL
        ws = websocket.create_connection(url, timeout=75)
        self._ws = ws
        hb_stop = threading.Event()
        self._ack = True
        try:
            while not self._stop.is_set():
                opcode, frame = ws.recv_data_frame(True)
                if opcode == websocket.ABNF.OPCODE_CLOSE:
                    code = int.from_bytes(frame.data[:2], "big") if len(frame.data) >= 2 else None
                    raise _Closed(code)
                if opcode not in (websocket.ABNF.OPCODE_TEXT, websocket.ABNF.OPCODE_BINARY):
                    continue
                msg = json.loads(frame.data.decode("utf-8"))
                op, d = msg.get("op"), msg.get("d")
                if msg.get("s") is not None:
                    self._seq = msg["s"]
                if op == 10:
                    threading.Thread(target=self._heartbeat,
                                     args=(ws, d["heartbeat_interval"] / 1000.0, hb_stop),
                                     daemon=True).start()
                    if self._session:
                        self._send(6, {"token": self.token, "session_id": self._session,
                                       "seq": self._seq}, ws)
                    else:
                        self._identify(ws)
                elif op == 11:
                    self._ack = True
                elif op == 1:
                    self._send(1, self._seq, ws)
                elif op == 7:
                    return
                elif op == 9:
                    if not d:
                        self._session = None
                    time.sleep(random.uniform(1, 4))
                    if self._session:
                        self._send(6, {"token": self.token, "session_id": self._session,
                                       "seq": self._seq}, ws)
                    else:
                        self._identify(ws)
                elif op == 0:
                    t = msg.get("t")
                    if t == "READY":
                        self._session = d["session_id"]
                        self._resume_url = d.get("resume_gateway_url")
                        self.on_status("live", "Live")
                    elif t == "RESUMED":
                        self.on_status("live", "Live")
                    self.on_event(t, d)
        finally:
            hb_stop.set()
            try:
                ws.close()
            except Exception:
                pass

    def _identify(self, ws):
        intents = BASE_INTENTS | (CONTENT_INTENT if self.use_content else 0)
        self._send(2, {
            "token": self.token,
            "intents": intents,
            "properties": {"os": "android", "browser": "botcord", "device": "botcord"},
            "presence": self.presence,
        }, ws)

    def _heartbeat(self, ws, interval, stop_evt):
        if stop_evt.wait(interval * random.random()):
            return
        while not stop_evt.is_set() and not self._stop.is_set():
            if not self._ack:      # zombie connection -> force reconnect
                try:
                    ws.close()
                except Exception:
                    pass
                return
            self._ack = False
            self._send(1, self._seq, ws)
            if stop_evt.wait(interval):
                return
