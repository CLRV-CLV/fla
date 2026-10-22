"""iPhone 整屏投屏: 内置 RTMP 接收端 (纯 asyncio, 不依赖 nginx-rtmp / SRS)

老师在 iPhone 上用 App Store 免费 App (PRISM Live Studio / Larix Screencaster 等) 推流到
    rtmp://<服务器>:1935/live/<个人投屏密钥>
个人密钥由手机端 (描述文件版 FLA 投屏) 生成并保存; 每次扫码连接电脑时, 网页把 密钥 → 当前会话 绑定。
服务器收 H.264 / HEVC → 解码 (PyAV, 无则用 ffmpeg 子进程) → JPEG → 以 'C' 帧推给该会话的电脑端,
电脑端按「手机画面」显示, 与安卓投屏走同一条通道。
"""
import asyncio
import io
import logging
import os
import queue
import struct
import subprocess
import threading
import time

log = logging.getLogger("fla.rtmp")

RTMP_PORT = int(os.environ.get("RTMP_PORT", "1935"))
FPS = float(os.environ.get("RTMP_FPS", "15"))
MAX_SIDE = int(os.environ.get("RTMP_MAX_SIDE", "1600"))

# 密钥 → {"sid":..., "t":...}
BINDINGS: dict = {}
# 密钥 → 状态 (给手机端显示)
LIVE: dict = {}


# 手机公网 IP → 会话 (老师在 PRISM 里只填通用地址时, 按「刚扫码的那台手机的 IP」自动对上电脑)
IP_BINDINGS: dict = {}


def bind(key: str, sid: str, ip: str = ""):
    BINDINGS[key] = {"sid": sid, "t": time.time()}
    if ip:
        IP_BINDINGS[ip] = {"sid": sid, "t": time.time(), "key": key}


def _pc_polling(sid: str) -> bool:
    try:
        from .routers import remote
        return time.time() - (remote.SESSIONS.get(sid) or {}).get("pc_poll", 0) < 20
    except Exception:
        return False


def debug() -> list:
    out = []
    for k, st in LIVE.items():
        b = BINDINGS.get(k) or {}
        d = {x: y for x, y in st.items() if x != "dec"}
        dec = st.get("dec")
        d["decoder"] = dec.kind if dec else "未收到视频头"
        d["err"] = dec.err if dec else ""
        d["key"] = k[:4] + "…"
        d["sid"] = b.get("sid")
        out.append(d)
    return out


def status(key: str) -> dict:
    st = LIVE.get(key) or {}
    b = BINDINGS.get(key)
    dec = st.get("dec")
    h = {}
    try:
        from .routers import remote
        h = remote.HUBS.get(b["sid"], {}) if b else {}
    except Exception:
        pass
    return {"live": bool(st), "fps": st.get("fps", 0), "codec": st.get("codec", ""),
            "bound": bool(b), "sid": b["sid"] if b else None,
            "video": st.get("vmsg", 0), "decoded": st.get("decoded", 0), "sent": st.get("sent", 0),
            "pc_online": (len(h.get("pc", ())) if h else 0) + (1 if b and _pc_polling(b["sid"]) else 0),
            "decoder": (dec.kind if dec else ("" if not st else "未收到视频头")), "err": dec.err if dec else ""}


# ---------------------------------------------------------------- AMF0
def amf_read(b: bytes, i: int):
    t = b[i]; i += 1
    if t == 0:
        return struct.unpack(">d", b[i:i + 8])[0], i + 8
    if t == 1:
        return bool(b[i]), i + 1
    if t == 2:
        n = struct.unpack(">H", b[i:i + 2])[0]; i += 2
        return b[i:i + n].decode("utf-8", "replace"), i + n
    if t in (3, 8):
        if t == 8:
            i += 4
        o = {}
        while i + 3 <= len(b):
            n = struct.unpack(">H", b[i:i + 2])[0]; i += 2
            k = b[i:i + n].decode("utf-8", "replace"); i += n
            if b[i] == 9:
                return o, i + 1
            o[k], i = amf_read(b, i)
        return o, i
    if t in (5, 6):
        return None, i
    if t == 10:
        n = struct.unpack(">I", b[i:i + 4])[0]; i += 4
        a = []
        for _ in range(n):
            v, i = amf_read(b, i); a.append(v)
        return a, i
    if t == 12:
        n = struct.unpack(">I", b[i:i + 4])[0]; i += 4
        return b[i:i + n].decode("utf-8", "replace"), i + n
    raise ValueError("amf type %d" % t)


def amf_all(b: bytes) -> list:
    out, i = [], 0
    while i < len(b):
        try:
            v, i = amf_read(b, i)
        except Exception:
            break
        out.append(v)
    return out


def amf_enc(v) -> bytes:
    if v is None:
        return b"\x05"
    if isinstance(v, bool):
        return b"\x01" + (b"\x01" if v else b"\x00")
    if isinstance(v, (int, float)):
        return b"\x00" + struct.pack(">d", float(v))
    if isinstance(v, str):
        e = v.encode()
        return b"\x02" + struct.pack(">H", len(e)) + e
    if isinstance(v, dict):
        out = b"\x03"
        for k, x in v.items():
            e = k.encode()
            out += struct.pack(">H", len(e)) + e + amf_enc(x)
        return out + b"\x00\x00\x09"
    raise TypeError(type(v))


# ---------------------------------------------------------------- 解码 → JPEG
class Decoder(threading.Thread):
    """后台线程: 收压缩帧, 按 FPS 输出 JPEG (回调在事件循环里执行)"""

    def __init__(self, codec: str, config: bytes, on_jpeg, loop):
        super().__init__(daemon=True)
        self.codec, self.config, self.on_jpeg, self.loop = codec, config, on_jpeg, loop
        self.q: "queue.Queue" = queue.Queue(maxsize=240)
        self.alive = True
        self.need_key = False
        self.last = 0.0
        self.frames = 0
        self.kind = ""
        self.err = ""

    def feed(self, data: bytes, key: bool):
        if self.need_key and not key:
            return
        self.need_key = False
        try:
            self.q.put_nowait((data, key))
        except queue.Full:
            # 积压 → 清空, 等下一个关键帧 (宁可跳帧也不要越来越慢)
            with self.q.mutex:
                self.q.queue.clear()
            self.need_key = True

    def stop(self):
        self.alive = False
        try:
            self.q.put_nowait(None)
        except queue.Full:
            pass

    def _emit(self, img):
        now = time.time()
        if now - self.last < 1.0 / FPS:
            return
        self.last = now
        w, h = img.size
        s = min(1.0, MAX_SIDE / max(w, h))
        if s < 1:
            img = img.resize((int(w * s), int(h * s)))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=72)
        self.frames += 1
        self.loop.call_soon_threadsafe(self.on_jpeg, buf.getvalue())

    def run(self):
        try:
            import av  # PyAV (pip install av)
        except Exception:
            av = None
        if av is not None:
            self.kind = "pyav"
            self._run_av(av)
        else:
            self._run_ffmpeg()

    def _run_av(self, av):
        ctx = av.CodecContext.create("hevc" if self.codec == "hevc" else "h264", "r")
        ctx.extradata = self.config
        try:
            ctx.thread_type = "AUTO"
        except Exception:
            pass
        while self.alive:
            it = self.q.get()
            if it is None:
                break
            try:
                for fr in ctx.decode(av.Packet(it[0])):
                    if time.time() - self.last >= 1.0 / FPS:
                        self._emit(fr.to_image())
            except Exception as e:
                self.err = "decode: %s" % e
                log.debug("decode: %s", e)

    def _run_ffmpeg(self):
        """无 PyAV: 转 Annex-B 喂给 ffmpeg, 读 MJPEG 输出"""
        nal_len, params = _parse_config(self.codec, self.config)
        fmt = "hevc" if self.codec == "hevc" else "h264"
        try:
            p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-fflags", "nobuffer", "-flags", "low_delay",
                                  "-probesize", "32", "-analyzeduration", "0", "-f", fmt, "-i", "pipe:0",
                                  "-vf", "scale='min(%d,iw)':-2" % MAX_SIDE, "-q:v", "6", "-f", "mjpeg", "pipe:1"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        except Exception as e:
            self.kind = "none"
            self.err = "PyAV 和 ffmpeg 都不可用: %s" % e
            log.warning("ffmpeg 不可用: %s", e)
            return
        self.kind = "ffmpeg"

        def reader():
            buf = b""
            while True:
                d = p.stdout.read(65536)
                if not d:
                    break
                buf += d
                while True:
                    a = buf.find(b"\xff\xd8")
                    z = buf.find(b"\xff\xd9", a + 2) if a >= 0 else -1
                    if a < 0 or z < 0:
                        break
                    jpg, buf = buf[a:z + 2], buf[z + 2:]
                    now = time.time()
                    if now - self.last < 1.0 / FPS:
                        continue
                    self.last = now
                    self.frames += 1
                    self.loop.call_soon_threadsafe(self.on_jpeg, jpg)
        threading.Thread(target=reader, daemon=True).start()
        sc = b"\x00\x00\x00\x01"
        try:
            p.stdin.write(b"".join(sc + x for x in params))
            while self.alive:
                it = self.q.get()
                if it is None:
                    break
                d, i, out = it[0], 0, []
                while i + nal_len <= len(d):
                    n = int.from_bytes(d[i:i + nal_len], "big"); i += nal_len
                    out.append(sc + d[i:i + n]); i += n
                p.stdin.write(b"".join(out))
                p.stdin.flush()
        except Exception:
            pass
        finally:
            try:
                p.stdin.close(); p.kill()
            except Exception:
                pass


def _parse_config(codec: str, cfg: bytes):
    """avcC / hvcC → (NAL 长度字节数, [参数集])"""
    ps = []
    try:
        if codec == "hevc":
            nal_len = (cfg[21] & 3) + 1
            n, i = cfg[22], 23
            for _ in range(n):
                cnt = struct.unpack(">H", cfg[i + 1:i + 3])[0]; i += 3
                for _ in range(cnt):
                    ln = struct.unpack(">H", cfg[i:i + 2])[0]; i += 2
                    ps.append(cfg[i:i + ln]); i += ln
        else:
            nal_len = (cfg[4] & 3) + 1
            n, i = cfg[5] & 31, 6
            for _ in range(n):
                ln = struct.unpack(">H", cfg[i:i + 2])[0]; i += 2
                ps.append(cfg[i:i + ln]); i += ln
            n = cfg[i]; i += 1
            for _ in range(n):
                ln = struct.unpack(">H", cfg[i:i + 2])[0]; i += 2
                ps.append(cfg[i:i + ln]); i += ln
    except Exception:
        nal_len = 4
    return nal_len, ps


# ---------------------------------------------------------------- RTMP 连接
class Conn:
    def __init__(self, r: asyncio.StreamReader, w: asyncio.StreamWriter):
        self.r, self.w = r, w
        self.in_chunk = 128
        self.out_chunk = 4096
        self.streams = {}          # csid → [ts, len, type, sid, buf]
        self.recv = 0
        self.ack_win = 2500000
        self.last_ack = 0
        self.key = None
        self.dec = None
        self.codec = ""
        self.t0 = time.time()
        self.app = ""
        try:
            self.ip = (w.get_extra_info("peername") or ("",))[0]
            if self.ip.startswith("::ffff:"):
                self.ip = self.ip[7:]
        except Exception:
            self.ip = ""

    async def read(self, n):
        d = await self.r.readexactly(n)
        self.recv += n
        if self.recv - self.last_ack >= self.ack_win:
            self.last_ack = self.recv
            self.send(2, 3, 0, struct.pack(">I", self.recv & 0xFFFFFFFF))
        return d

    def send(self, csid, typ, sid, payload: bytes):
        hdr = bytes([csid & 63]) + b"\x00\x00\x00" + len(payload).to_bytes(3, "big") + bytes([typ]) + struct.pack("<I", sid)
        out = [hdr]
        for i in range(0, len(payload), self.out_chunk):
            if i:
                out.append(bytes([0xC0 | (csid & 63)]))
            out.append(payload[i:i + self.out_chunk])
        if not payload:
            pass
        self.w.write(b"".join(out))

    def cmd(self, sid, *vals):
        self.send(3 if sid == 0 else 5, 20, sid, b"".join(amf_enc(v) for v in vals))

    async def handshake(self):
        c0c1 = await self.read(1537)
        if c0c1[0] != 3:
            raise ValueError("not rtmp")
        s1 = struct.pack(">II", int(time.time()) & 0xFFFFFFFF, 0) + os.urandom(1528)
        self.w.write(b"\x03" + s1 + c0c1[1:])
        await self.w.drain()
        await self.read(1536)

    async def run(self):
        await self.handshake()
        while True:
            b0 = (await self.read(1))[0]
            fmt, csid = b0 >> 6, b0 & 63
            if csid == 0:
                csid = 64 + (await self.read(1))[0]
            elif csid == 1:
                x = await self.read(2); csid = 64 + x[0] + x[1] * 256
            st = self.streams.setdefault(csid, [0, 0, 0, 0, b"", 0])
            if fmt <= 2:
                h = await self.read(11 if fmt == 0 else 7 if fmt == 1 else 3)
                ts = int.from_bytes(h[0:3], "big")
                if fmt <= 1:
                    st[1] = int.from_bytes(h[3:6], "big"); st[2] = h[6]
                if fmt == 0:
                    st[3] = struct.unpack("<I", h[7:11])[0]
                st[5] = 1 if ts == 0xFFFFFF else 0
                if st[5]:
                    ts = struct.unpack(">I", await self.read(4))[0]
                st[0] = ts if fmt == 0 else st[0] + ts
            elif st[5]:
                await self.read(4)
            need = min(self.in_chunk, st[1] - len(st[4]))
            st[4] += await self.read(need)
            if len(st[4]) >= st[1]:
                msg, st[4] = st[4], b""
                await self.on_msg(st[2], st[3], msg)

    async def on_msg(self, typ, sid, m: bytes):
        if typ == 1:
            self.in_chunk = struct.unpack(">I", m[:4])[0] & 0x7FFFFFFF
        elif typ == 5:
            self.ack_win = max(4096, struct.unpack(">I", m[:4])[0])
        elif typ in (20, 17):
            await self.on_cmd(amf_all(m[1:] if typ == 17 else m))
        elif typ == 9 and self.key:
            self.on_video(m)
        elif typ == 22 and self.key:          # Aggregate: 内含多个 FLV tag
            i = 0
            while i + 11 <= len(m):
                t = m[i]; n = int.from_bytes(m[i + 1:i + 4], "big")
                body = m[i + 11:i + 11 + n]
                if t == 9:
                    self.on_video(body)
                i += 11 + n + 4

    async def on_cmd(self, a: list):
        if not a:
            return
        name, tid = a[0], a[1] if len(a) > 1 else 0
        if name == "connect":
            try:
                self.app = str((a[2] or {}).get("app") or "")
            except Exception:
                self.app = ""
            self.send(2, 5, 0, struct.pack(">I", 2500000))
            self.send(2, 6, 0, struct.pack(">IB", 2500000, 2))
            self.send(2, 1, 0, struct.pack(">I", self.out_chunk))
            self.cmd(0, "_result", tid, {"fmsVer": "FMS/3,0,1,123", "capabilities": 31.0},
                     {"level": "status", "code": "NetConnection.Connect.Success", "description": "FLA", "objectEncoding": 0.0})
        elif name == "createStream":
            self.cmd(0, "_result", tid, None, 1.0)
        elif name in ("releaseStream", "FCPublish", "FCUnpublish", "deleteStream"):
            if name == "FCPublish":
                self.cmd(0, "onFCPublish", 0.0, None, {"code": "NetStream.Publish.Start", "description": a[3] if len(a) > 3 else ""})
            else:
                self.cmd(0, "_result", tid, None, None)
        elif name == "publish":
            name_ = str(a[3] if len(a) > 3 else "").split("?")[0].strip("/")
            cands = [name_] + [x for x in self.app.split("?")[0].split("/") if x]
            key = next((c for c in cands if c in BINDINGS), None)
            if not key:
                # 通用地址: 按手机 IP 找最近 6 小时内扫码的会话
                ib = IP_BINDINGS.get(self.ip)
                if ib and time.time() - ib["t"] < 6 * 3600:
                    key = ib["key"]
            if not key:
                self.cmd(1, "onStatus", 0.0, None, {"level": "error", "code": "NetStream.Publish.BadName",
                                                    "description": "scan the QR in FLA first"})
                await self.w.drain()
                raise ConnectionError("bad key")
            self.key = key
            LIVE[key] = {"fps": 0, "codec": "", "t": time.time()}
            self.cmd(1, "onStatus", 0.0, None, {"level": "status", "code": "NetStream.Publish.Start", "description": "FLA"})
            log.info("rtmp publish key=%s… sid=%s", key[:6], BINDINGS[key]["sid"])
        await self.w.drain()

    def on_video(self, m: bytes):
        st = LIVE.get(self.key)
        if st is not None:
            st["vmsg"] = st.get("vmsg", 0) + 1
            if st["vmsg"] <= 3:
                st.setdefault("hdr", []).append(m[:6].hex())
        if len(m) < 2:
            return
        b0 = m[0]
        key = (b0 >> 4) & 7 == 1
        if b0 & 0x80:                    # Enhanced RTMP
            pt, fourcc = b0 & 15, m[1:5]
            codec = "hevc" if fourcc in (b"hvc1", b"hev1") else "h264" if fourcc == b"avc1" else ""
            if not codec:
                return
            if pt == 0:
                self.start_dec(codec, m[5:])
            elif pt == 1:
                self.dec and self.dec.feed(m[8:], key)
            elif pt == 3:
                self.dec and self.dec.feed(m[5:], key)
            return
        cid = b0 & 15
        if cid not in (7, 12) or len(m) < 5:
            return
        codec = "hevc" if cid == 12 else "h264"
        if m[1] == 0:
            self.start_dec(codec, m[5:])
        elif m[1] == 1 and self.dec:
            self.dec.feed(m[5:], key)

    def start_dec(self, codec, cfg):
        if self.dec:
            self.dec.stop()
        self.codec = codec
        LIVE.get(self.key, {})["codec"] = codec
        self.dec = Decoder(codec, cfg, self.on_jpeg, asyncio.get_running_loop())
        self.dec.start()
        LIVE.get(self.key, {})["dec"] = self.dec

    def on_jpeg(self, jpg: bytes):
        b = BINDINGS.get(self.key)
        st = LIVE.get(self.key)
        if st is not None and self.dec:
            dt = max(1.0, time.time() - self.t0)
            st["fps"] = round(self.dec.frames / dt, 1)
            st["decoded"] = self.dec.frames
        if not b:
            return
        try:
            from .routers import remote
            # 1) 存为会话的「手机画面」: 电脑端 WebSocket 连不上 (反代未开 WebSocket 等) 时走 HTTP 轮询也能拿到
            sess = remote.SESSIONS.get(b["sid"])
            if sess is not None:
                frames = sess.setdefault("frames", {})
                frames["phone"] = (frames.get("phone", (0, b"", 0))[0] + 1, jpg, time.time())
                sess["last_active"] = time.time()
            # 2) 电脑在 WebSocket 中继上: 直接推送 (更低延迟)
            h = remote.HUBS.get(b["sid"])
            if not h:
                if st is not None:
                    st["pcs"] = 0
                return
            data = b"C" + jpg
            if st is not None:
                st["pcs"] = len(h.get("pc", ()))
                st["sent"] = st.get("sent", 0) + 1
            for ws in list(h.get("pc", ())):
                asyncio.create_task(remote._hub_safe(ws, data, True))
        except Exception as e:
            log.debug("forward: %s", e)

    def close(self):
        if self.dec:
            self.dec.stop()
        if self.key:
            LIVE.pop(self.key, None)
        try:
            self.w.close()
        except Exception:
            pass


async def _handle(r, w):
    c = Conn(r, w)
    try:
        await c.run()
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    except Exception as e:
        log.info("rtmp conn error: %s", e)
    finally:
        c.close()


_server = None


async def start():
    global _server
    if _server or os.environ.get("RTMP_DISABLE"):
        return
    try:
        _server = await asyncio.start_server(_handle, "0.0.0.0", RTMP_PORT)
        log.info("RTMP 接收端已启动 :%d", RTMP_PORT)
    except OSError as e:
        log.warning("RTMP 端口 %d 无法监听: %s", RTMP_PORT, e)
