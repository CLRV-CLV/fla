package top.clrv.fla;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.nio.charset.Charset;
import java.security.SecureRandom;

import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

/** 极简 WebSocket 客户端 (RFC 6455, ws:// 与 wss://), 无第三方依赖 */
public class WsClient {
    public interface Listener {
        void onOpen();
        void onText(String s);
        void onClose(String reason);
    }

    static final Charset UTF8 = Charset.forName("UTF-8");
    final URI uri;
    final Listener listener;
    final int connectTimeoutMs;
    Socket sock;
    OutputStream out;
    volatile boolean open, closed;
    final SecureRandom rnd = new SecureRandom();

    public WsClient(String url, int connectTimeoutMs, Listener l) {
        this.uri = URI.create(url);
        this.connectTimeoutMs = connectTimeoutMs;
        this.listener = l;
    }

    public void connectAsync() {
        Thread t = new Thread(new Runnable() {
            @Override public void run() { runLoop(); }
        }, "ws");
        t.setDaemon(true);
        t.start();
    }

    void runLoop() {
        String reason = "closed";
        try {
            boolean tls = "wss".equals(uri.getScheme());
            int port = uri.getPort() > 0 ? uri.getPort() : (tls ? 443 : 80);
            Socket raw = new Socket();
            raw.connect(new InetSocketAddress(uri.getHost(), port), connectTimeoutMs);
            raw.setTcpNoDelay(true);
            if (tls) {
                SSLSocket s = (SSLSocket) ((SSLSocketFactory) SSLSocketFactory.getDefault()).createSocket(raw, uri.getHost(), port, true);
                s.startHandshake();
                sock = s;
            } else sock = raw;
            sock.setSoTimeout(0);
            out = sock.getOutputStream();
            byte[] keyBytes = new byte[16];
            rnd.nextBytes(keyBytes);
            String key = android.util.Base64.encodeToString(keyBytes, android.util.Base64.NO_WRAP);
            String path = (uri.getRawPath() == null || uri.getRawPath().isEmpty() ? "/" : uri.getRawPath())
                    + (uri.getRawQuery() != null ? "?" + uri.getRawQuery() : "");
            String host = uri.getHost() + (uri.getPort() > 0 ? ":" + uri.getPort() : "");
            String req = "GET " + path + " HTTP/1.1\r\nHost: " + host + "\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    + "Sec-WebSocket-Key: " + key + "\r\nSec-WebSocket-Version: 13\r\nUser-Agent: FLA-Android/1.0\r\n\r\n";
            synchronized (this) { out.write(req.getBytes(UTF8)); out.flush(); }
            DataInputStream in = new DataInputStream(new BufferedInputStream(sock.getInputStream(), 16384));
            String status = readLine(in);
            if (status == null || !status.contains(" 101")) throw new IOException("握手失败: " + status);
            String line;
            while ((line = readLine(in)) != null && !line.isEmpty()) { /* 跳过响应头 */ }
            open = true;
            listener.onOpen();
            ByteArrayOutputStream msg = new ByteArrayOutputStream();
            int msgOp = 0;
            while (!closed) {
                int b0 = in.readUnsignedByte(), b1 = in.readUnsignedByte();
                boolean fin = (b0 & 0x80) != 0;
                int op = b0 & 0x0F;
                long len = b1 & 0x7F;
                if (len == 126) len = in.readUnsignedShort();
                else if (len == 127) len = in.readLong();
                byte[] mask = null;
                if ((b1 & 0x80) != 0) { mask = new byte[4]; in.readFully(mask); }
                if (len > 16 * 1024 * 1024) throw new IOException("帧过大");
                byte[] payload = new byte[(int) len];
                in.readFully(payload);
                if (mask != null) for (int i = 0; i < payload.length; i++) payload[i] ^= mask[i & 3];
                if (op == 8) { reason = "server close"; break; }
                if (op == 9) { sendFrame(0xA, payload); continue; }
                if (op == 0xA) continue;
                if (op != 0) { msg.reset(); msgOp = op; }
                msg.write(payload);
                if (fin) {
                    if (msgOp == 1) listener.onText(new String(msg.toByteArray(), UTF8));
                    msg.reset();
                }
            }
        } catch (Exception e) {
            reason = String.valueOf(e.getMessage());
        }
        boolean wasOpen = open;
        open = false;
        try { if (sock != null) sock.close(); } catch (IOException ignored) { }
        if (!closed || wasOpen) listener.onClose(reason);
    }

    static String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            if (c != '\r') b.write(c);
        }
        if (c == -1 && b.size() == 0) return null;
        return new String(b.toByteArray(), UTF8);
    }

    public boolean isOpen() { return open; }

    public boolean sendText(String s) { return sendFrame(1, s.getBytes(UTF8)); }

    public boolean sendBinary(byte[] b) { return sendFrame(2, b); }

    /** 客户端帧必须加掩码 */
    synchronized boolean sendFrame(int op, byte[] data) {
        if (!open || out == null) return false;
        try {
            int n = data.length;
            byte[] head;
            if (n < 126) head = new byte[]{(byte) (0x80 | op), (byte) (0x80 | n)};
            else if (n < 65536) head = new byte[]{(byte) (0x80 | op), (byte) (0x80 | 126), (byte) (n >> 8), (byte) n};
            else {
                head = new byte[10];
                head[0] = (byte) (0x80 | op);
                head[1] = (byte) (0x80 | 127);
                for (int i = 0; i < 8; i++) head[9 - i] = (byte) ((long) n >> (8 * i));
            }
            byte[] mask = new byte[4];
            rnd.nextBytes(mask);
            byte[] body = new byte[n];
            for (int i = 0; i < n; i++) body[i] = (byte) (data[i] ^ mask[i & 3]);
            out.write(head);
            out.write(mask);
            out.write(body);
            out.flush();
            return true;
        } catch (IOException e) {
            open = false;
            try { sock.close(); } catch (Exception ignored) { }
            return false;
        }
    }

    public void close() {
        closed = true;
        if (open) sendFrame(8, new byte[]{0x03, (byte) 0xE8});
        open = false;
        try { if (sock != null) sock.close(); } catch (IOException ignored) { }
    }
}
