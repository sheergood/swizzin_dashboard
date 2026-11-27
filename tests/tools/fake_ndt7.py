#!/usr/bin/env python3
"""A tiny stand-in for M-Lab (locate API + ndt7 server) for tests: python3 fake_ndt7.py PORT [CERT KEY].

GET /v2/nearest/ndt/ndt7 returns URLs pointing back here; /ndt/v7/download streams random binary
messages (rate-limited, FAKE_NDT7_MBPS, default 200) for FAKE_NDT7_SECONDS (default 2) with text
measurements; /ndt/v7/upload counts what arrives and reports it in AppInfo/TCPInfo measurements.
FAKE_NDT7_MODE=refuse answers the upgrade with 403, =locate204 makes the locate API return 204.
"""
import base64, hashlib, json, os, socket, ssl, struct, sys, threading, time

PORT = int(sys.argv[1])
TLS = len(sys.argv) > 3
MBPS = float(os.environ.get("FAKE_NDT7_MBPS", "200"))
SECS = float(os.environ.get("FAKE_NDT7_SECONDS", "2"))
MODE = os.environ.get("FAKE_NDT7_MODE", "")
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def frame(op, payload):
    n = len(payload)
    if n < 126:
        h = struct.pack("!BB", 0x80 | op, n)
    elif n < 65536:
        h = struct.pack("!BBH", 0x80 | op, 126, n)
    else:
        h = struct.pack("!BBQ", 0x80 | op, 127, n)
    return h + payload


def recv_exact(c, n):
    out, view, got = bytearray(n), None, 0
    view = memoryview(out)
    while got < n:
        k = c.recv_into(view[got:], n - got)
        if not k:
            raise EOFError()
        got += k
    return bytes(out)


def read_frame(c):
    b0, b1 = struct.unpack("!BB", recv_exact(c, 2))
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack("!H", recv_exact(c, 2))[0]
    elif n == 127:
        n = struct.unpack("!Q", recv_exact(c, 8))[0]
    mask = recv_exact(c, 4) if b1 & 0x80 else None
    assert mask is not None, "client frames must be masked"
    data = recv_exact(c, n)
    if mask != b"\0\0\0\0":
        data = bytes(x ^ mask[i % 4] for i, x in enumerate(data))
    return b0 & 0x0F, data


def meas(start, nbytes):
    us = int((time.time() - start) * 1e6)
    return json.dumps({"AppInfo": {"NumBytes": nbytes, "ElapsedTime": us},
                       "TCPInfo": {"MinRTT": 4200, "ElapsedTime": us, "BytesReceived": nbytes}}).encode()


def handle(c):
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            req += c.recv(4096)
        lines = req.decode().split("\r\n")
        path = lines[0].split()[1]
        hdr = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if l)}
        scheme = "wss" if TLS else "ws"
        if path.startswith("/v2/nearest"):
            if MODE == "locate204":
                c.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
                return
            base = os.environ.get("FAKE_NDT7_BASE") or "{}://127.0.0.1:{}".format(scheme, PORT)
            body = json.dumps({"results": [{"machine": "mlab1-ams00.example", "location": {"city": "Amsterdam", "country": "NL"},
                "urls": {"wss:///ndt/v7/download": base + "/ndt/v7/download?access_token=x",
                         "wss:///ndt/v7/upload": base + "/ndt/v7/upload?access_token=x"}}]}).encode()
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
            return
        if MODE == "refuse" or "access_token=x" not in path or hdr.get("sec-websocket-protocol") != "net.measurementlab.ndt.v7":
            c.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        acc = base64.b64encode(hashlib.sha1(hdr["sec-websocket-key"].encode() + GUID).digest())
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  b"Sec-WebSocket-Accept: " + acc + b"\r\nSec-WebSocket-Protocol: net.measurementlab.ndt.v7\r\n\r\n")
        start = time.time()
        if path.startswith("/ndt/v7/download"):
            blob, sent, lastm = os.urandom(1 << 16), 0, 0
            c.sendall(frame(0x9, b"hi"))                      # client must answer pings
            while time.time() - start < SECS:
                c.sendall(frame(0x2, blob))
                sent += len(blob)
                ahead = sent * 8 / (MBPS * 1e6) - (time.time() - start)
                if ahead > 0:
                    time.sleep(ahead)
                if time.time() - lastm > 0.25:
                    lastm = time.time()
                    c.sendall(frame(0x1, meas(start, sent)))
            c.sendall(frame(0x8, struct.pack("!H", 1000)))
            c.settimeout(3)
            while True:
                op, _ = read_frame(c)
                if op == 0x8:
                    break
        else:
            got, lastm = 0, 0
            c.settimeout(15)
            while True:
                op, data = read_frame(c)
                if op == 0x2:
                    got += len(data)
                elif op == 0x8:
                    c.sendall(frame(0x1, meas(start, got)))
                    c.sendall(frame(0x8, struct.pack("!H", 1000)))
                    break
                if time.time() - lastm > 0.25:
                    lastm = time.time()
                    c.sendall(frame(0x1, meas(start, got)))
    except Exception as e:  # noqa
        sys.stderr.write("fake_ndt7: %r\n" % (e,))
    finally:
        try:
            c.close()
        except Exception:
            pass


def main():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", PORT))
    s.listen(16)
    ctx = None
    if TLS:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(sys.argv[2], sys.argv[3])
    print("ready", flush=True)
    while True:
        c, _ = s.accept()
        if ctx:
            try:
                c = ctx.wrap_socket(c, server_side=True)
            except Exception:
                c.close()
                continue
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
