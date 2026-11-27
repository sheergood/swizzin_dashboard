#!/usr/bin/env python3
"""Minimal ndt7 speed test client (M-Lab), standard library only.

Run as its own process by the panel (core/speedtest.py) so a 1 Gbit/s transfer never blocks the
panel's event loop. Prints one JSON object per line on stdout:

    {"phase": "locate"}                      looking up the nearest M-Lab server
    {"phase": "download", "mbps": 812.3}    live progress, about twice a second
    {"phase": "upload", "mbps": 401.0}
    {"done": true, "download": .., "upload": .., "latency_ms": .., "server": {...}}
    {"error": "..."}                         and exit code 1

Protocol: https://github.com/m-lab/ndt-server/blob/main/spec/ndt7-protocol.md
  - locate: GET https://locate.measurementlab.net/v2/nearest/ndt/ndt7 -> URLs with access tokens
  - WebSocket (RFC 6455) over TLS, subprotocol net.measurementlab.ndt.v7
  - download: the server sends binary messages for up to 10 s; speed = bytes received / time
  - upload: the client sends binary messages for 10 s, doubling the message size (from 8 KiB) while
    it is under 1/16 of what has been sent; speed comes from the server's own byte count when it
    reports one (what really arrived), else from what was sent
"""
import base64
import hashlib
import json
import os
import select
import socket
import ssl
import struct
import sys
import time

try:
    from urllib.parse import urlsplit
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    raise SystemExit("python3 required")

LOCATE = os.environ.get("NDT7_LOCATE", "https://locate.measurementlab.net/v2/nearest/ndt/ndt7?client_name=swizzin-panel")
UA = "swizzin-panel-ndt7/1.0 (+https://github.com/sheergood/swizzin_dashboard)"
PROTO = "net.measurementlab.ndt.v7"
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC11B85"
DURATION = float(os.environ.get("NDT7_DURATION", "10"))   # seconds per direction (10 = protocol maximum; tests use less)
HARD_LIMIT = 15.0        # give up on a direction after this long no matter what
MAX_MSG = 1 << 24
INSECURE = os.environ.get("NDT7_INSECURE") == "1"   # tests only (self-signed local server)


def emit(**kw):
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


class WS(object):
    """Just enough RFC 6455 for ndt7: binary/text/ping/close, client frames masked with a zero key."""

    def __init__(self, url, timeout=10.0):
        u = urlsplit(url)
        secure = u.scheme == "wss"
        port = u.port or (443 if secure else 80)
        raw = socket.create_connection((u.hostname, port), timeout=timeout)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if secure:
            ctx = ssl.create_default_context()
            if INSECURE:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=u.hostname)
        self.sock = raw
        self.buf = bytearray()
        key = base64.b64encode(os.urandom(16))
        path = (u.path or "/") + ("?" + u.query if u.query else "")
        req = ("GET {} HTTP/1.1\r\nHost: {}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: {}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: {}\r\n"
               "User-Agent: {}\r\n\r\n").format(path, u.netloc, key.decode(), PROTO, UA)
        self.sock.sendall(req.encode())
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise IOError("connection closed during handshake")
            self.buf += chunk
            if len(self.buf) > 65536:
                raise IOError("handshake response too large")
        head, _, rest = bytes(self.buf).partition(b"\r\n\r\n")
        self.buf = bytearray(rest)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise IOError("server refused the test: " + lines[0])
        hdr = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
        want = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()
        if hdr.get("sec-websocket-accept") != want:
            raise IOError("bad WebSocket handshake")
        if hdr.get("sec-websocket-protocol", PROTO) != PROTO:
            raise IOError("server did not accept the ndt7 protocol")
        self.sock.settimeout(timeout)

    # -- reading -----------------------------------------------------------------------------
    def _fill(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise EOFError()
            self.buf += chunk

    def _take(self, n):
        self._fill(n)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def _skip(self, n):
        """Discard n payload bytes without keeping them (download data); returns n."""
        if len(self.buf) >= n:
            del self.buf[:n]
            return n
        left = n - len(self.buf)
        self.buf = bytearray()
        while left:
            chunk = self.sock.recv(min(left, 1 << 20))
            if not chunk:
                raise EOFError()
            left -= len(chunk)
        return n

    def read_frame(self, keep_binary=False):
        """Returns (opcode, payload or byte count). Control frames are answered here."""
        b0, b1 = struct.unpack("!BB", self._take(2))
        op, masked, n = b0 & 0x0F, b1 & 0x80, b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._take(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._take(8))[0]
        if n > MAX_MSG:
            raise IOError("message too large")
        mask = self._take(4) if masked else None
        if op == 0x2 and not keep_binary and not mask:
            return op, self._skip(n)
        data = self._take(n)
        if mask:
            data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        if op == 0x9:                       # ping -> pong with the same payload
            self.send(0xA, data)
        return op, data

    def readable(self, wait=0.0):
        if self.buf or (hasattr(self.sock, "pending") and self.sock.pending()):
            return True
        return bool(select.select([self.sock], [], [], wait)[0])

    # -- writing -----------------------------------------------------------------------------
    def send_header(self, op, n):
        if n < 126:
            h = struct.pack("!BB", 0x80 | op, 0x80 | n)
        elif n < 65536:
            h = struct.pack("!BBH", 0x80 | op, 0x80 | 126, n)
        else:
            h = struct.pack("!BBQ", 0x80 | op, 0x80 | 127, n)
        return h + b"\x00\x00\x00\x00"      # zero masking key: payload goes out unchanged

    def send(self, op, payload):
        head = self.send_header(op, len(payload))
        if len(payload) > 65536:            # don't copy big upload messages just to prepend 14 bytes
            self.sock.sendall(head)
            self.sock.sendall(payload)
        else:
            self.sock.sendall(head + payload)

    def close(self):
        try:
            self.send(0x8, struct.pack("!H", 1000))
        except Exception:
            pass

    def shutdown(self):
        try:
            self.sock.close()
        except Exception:
            pass


def _measurement(data):
    try:
        m = json.loads(data.decode("utf-8"))
        return m if isinstance(m, dict) else None
    except Exception:
        return None


def download(url):
    ws = WS(url)
    start = last = time.time()
    total, server = 0, None
    try:
        while time.time() - start < HARD_LIMIT:
            op, data = ws.read_frame()
            if op == 0x2:
                total += data
            elif op == 0x1:
                server = _measurement(data) or server
            elif op == 0x8:
                ws.close()
                break
            now = time.time()
            if now - last >= 0.5:
                last = now
                emit(phase="download", mbps=round(total * 8 / (now - start) / 1e6, 1))
    except (EOFError, socket.timeout, ssl.SSLError, OSError):
        pass                                      # the server may just close: keep what we measured
    elapsed = time.time() - start
    ws.shutdown()
    return {"mbps": round(total * 8 / elapsed / 1e6, 1) if elapsed > 0 else None, "bytes": total,
            "seconds": round(elapsed, 2), "server": server}


def upload(url):
    ws = WS(url)
    block = os.urandom(1 << 20)
    size, sent, server, payload = 1 << 13, 0, None, b""
    start = last = time.time()
    try:
        while time.time() - start < DURATION:
            if len(payload) != size:
                payload = block[:size] if size <= len(block) else block * (size // len(block))
            ws.send(0x2, payload)
            sent += size
            if size < MAX_MSG and size < sent / 16:
                size *= 2
            while ws.readable():
                op, data = ws.read_frame()
                if op == 0x1:
                    server = _measurement(data) or server
                elif op == 0x8:
                    raise EOFError()
            now = time.time()
            if now - last >= 0.5:
                last = now
                emit(phase="upload", mbps=_upload_mbps(server, sent, now - start))
        ws.close()
        end = time.time()
        while time.time() - end < 3 and ws.readable(0.5):   # collect the server's final count
            op, data = ws.read_frame()
            if op == 0x1:
                server = _measurement(data) or server
            elif op == 0x8:
                break
    except (EOFError, socket.timeout, ssl.SSLError, OSError):
        pass
    elapsed = time.time() - start
    ws.shutdown()
    return {"mbps": _upload_mbps(server, sent, elapsed), "bytes": sent, "seconds": round(elapsed, 2),
            "server": server, "measured_by": "server" if _server_rate(server) else "client"}


def _server_rate(m):
    """bits/s according to the server's own counters (AppInfo, else TCPInfo.BytesReceived)."""
    if not m:
        return None
    app, tcp = m.get("AppInfo") or {}, m.get("TCPInfo") or {}
    if app.get("NumBytes") and app.get("ElapsedTime"):
        return app["NumBytes"] * 8 / (app["ElapsedTime"] / 1e6)
    if tcp.get("BytesReceived") and tcp.get("ElapsedTime"):
        return tcp["BytesReceived"] * 8 / (tcp["ElapsedTime"] / 1e6)
    return None


def _upload_mbps(server, sent, elapsed):
    rate = _server_rate(server)
    if rate:
        return round(rate / 1e6, 1)
    return round(sent * 8 / elapsed / 1e6, 1) if elapsed > 0 else None


def _min_rtt_ms(*ms):
    vals = [((m or {}).get("TCPInfo") or {}).get("MinRTT") for m in ms]
    vals = [v for v in vals if v]
    return round(min(vals) / 1000.0, 1) if vals else None


def locate():
    req = Request(LOCATE, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        r = urlopen(req, timeout=10)
    except HTTPError as e:
        raise IOError("the M-Lab locate service answered HTTP {}".format(e.code))
    except (URLError, socket.timeout, OSError) as e:
        raise IOError("couldn't reach the M-Lab locate service ({})".format(getattr(e, "reason", None) or e))
    with r:
        if r.status == 204:
            raise IOError("M-Lab has no free server right now; try again in a minute")
        data = json.loads(r.read().decode("utf-8"))
    for res in data.get("results") or []:
        urls = res.get("urls") or {}
        dl, ul = urls.get("wss:///ndt/v7/download"), urls.get("wss:///ndt/v7/upload")
        if dl and ul:
            loc = res.get("location") or {}
            return dl, ul, {"machine": res.get("machine"), "city": loc.get("city"), "country": loc.get("country")}
    raise IOError("M-Lab returned no test server")


def _direction(name, fn, url):
    try:
        return fn(url)
    except ssl.SSLError as e:
        raise IOError("{} test: secure connection to M-Lab failed ({})".format(name, getattr(e, "reason", None) or e))
    except socket.timeout:
        raise IOError("{} test: the M-Lab server didn't answer in time".format(name))
    except (IOError, OSError) as e:
        raise IOError("{} test: {}".format(name, e))


def main():
    try:
        emit(phase="locate")
        dl_url, ul_url, server = locate()
        emit(phase="download", mbps=0, server=server)
        d = _direction("download", download, dl_url)
        emit(phase="upload", mbps=0)
        u = _direction("upload", upload, ul_url)
    except Exception as e:  # noqa: BLE001 - report every failure as a message, never a traceback
        emit(error=str(e) or e.__class__.__name__)
        return 1
    emit(done=True, download=d["mbps"], upload=u["mbps"], latency_ms=_min_rtt_ms(d["server"], u["server"]),
         server=server, upload_measured_by=u["measured_by"], bytes=d["bytes"] + u["bytes"], at=int(time.time()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
