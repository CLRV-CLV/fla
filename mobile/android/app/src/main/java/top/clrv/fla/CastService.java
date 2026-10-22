package top.clrv.fla;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.util.DisplayMetrics;
import android.view.WindowManager;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.ArrayList;

/**
 * 整屏采集 → JPEG → WebSocket 二进制帧 (首字节 'C') 发给电脑。
 * 先试局域网直连 (ws://电脑IP:8308), 1.5 秒连不上再走服务器中继 (wss://线路/api/remote/hub)。
 * 流控: 电脑显示完回 {"type":"ack","ch":"C"} 才发下一帧 → 不堆积, 延迟最低。
 */
public class CastService extends Service {
    interface Listener { void on(String s); }
    static Listener listener;

    static final int MAX_SIDE = 1600;
    static volatile boolean running;
    static final int QUALITY = 62;

    MediaProjection projection;
    VirtualDisplay display;
    ImageReader reader;
    HandlerThread thread;
    Handler worker;
    WsClient ws;
    volatile boolean connected, stopped;
    volatile long pendingAt;
    String sid, code, server;
    ArrayList<String> lan;
    int lanIdx;
    boolean viaLan;
    final Runnable pinger = new Runnable() {
        @Override public void run() {
            if (stopped) return;
            WsClient w = ws;
            if (w != null && w.isOpen()) w.sendText("ping");
            worker.postDelayed(this, 15000);
        }
    };

    void say(String s) { if (listener != null) listener.on(s); }

    @Override
    public IBinder onBind(Intent i) { return null; }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) { stopSelf(); return START_NOT_STICKY; }
        startAsForeground();
        running = true;
        sid = intent.getStringExtra("sid");
        code = intent.getStringExtra("code");
        server = intent.getStringExtra("server");
        lan = intent.getStringArrayListExtra("lan");
        if (lan == null) lan = new ArrayList<>();
        int res = intent.getIntExtra("result", 0);
        Intent data = intent.getParcelableExtra("data");
        MediaProjectionManager m = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        projection = m.getMediaProjection(res, data);
        if (projection == null) { say("无法获取屏幕录制权限"); stopSelf(); return START_NOT_STICKY; }
        thread = new HandlerThread("cast");
        thread.start();
        worker = new Handler(thread.getLooper());
        projection.registerCallback(new MediaProjection.Callback() {
            @Override public void onStop() { stopSelf(); }
        }, worker);
        startCapture();
        lanIdx = 0;
        connect();
        worker.postDelayed(pinger, 15000);
        return START_NOT_STICKY;
    }

    void startAsForeground() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26)
            nm.createNotificationChannel(new NotificationChannel("cast", "投屏", NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, "cast") : new Notification.Builder(this);
        Notification n = b.setContentTitle("FLA 正在投屏到电脑").setContentText("点此返回 App 停止投屏")
                .setSmallIcon(android.R.drawable.ic_menu_share).setContentIntent(open).setOngoing(true).build();
        if (Build.VERSION.SDK_INT >= 29) startForeground(1, n, 32 /* ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION */);
        else startForeground(1, n);
    }

    void startCapture() {
        DisplayMetrics dm = new DisplayMetrics();
        ((WindowManager) getSystemService(WINDOW_SERVICE)).getDefaultDisplay().getRealMetrics(dm);
        float s = Math.min(1f, (float) MAX_SIDE / Math.max(dm.widthPixels, dm.heightPixels));
        int w = Math.round(dm.widthPixels * s) & ~1, h = Math.round(dm.heightPixels * s) & ~1;
        reader = ImageReader.newInstance(w, h, PixelFormat.RGBA_8888, 2);
        reader.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() {
            @Override public void onImageAvailable(ImageReader r) { onImage(r); }
        }, worker);
        display = projection.createVirtualDisplay("fla-cast", w, h, dm.densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, reader.getSurface(), null, worker);
    }

    void onImage(ImageReader r) {
        Image img = null;
        try {
            img = r.acquireLatestImage();
            if (img == null) return;
            if (!connected || stopped) return;
            if (pendingAt != 0 && System.currentTimeMillis() - pendingAt < 1500) return;   // 等电脑 ack
            Image.Plane p = img.getPlanes()[0];
            int w = img.getWidth(), h = img.getHeight();
            int rowPad = p.getRowStride() - p.getPixelStride() * w;
            Bitmap bmp = Bitmap.createBitmap(w + rowPad / p.getPixelStride(), h, Bitmap.Config.ARGB_8888);
            ByteBuffer buf = p.getBuffer();
            bmp.copyPixelsFromBuffer(buf);
            if (rowPad != 0) { Bitmap c = Bitmap.createBitmap(bmp, 0, 0, w, h); bmp.recycle(); bmp = c; }
            ByteArrayOutputStream out = new ByteArrayOutputStream(64 * 1024);
            out.write('C');
            bmp.compress(Bitmap.CompressFormat.JPEG, viaLan ? 78 : QUALITY, out);
            bmp.recycle();
            pendingAt = System.currentTimeMillis();
            WsClient sock = ws;
            if (sock == null || !sock.sendBinary(out.toByteArray())) pendingAt = 0;
        } catch (Exception ignored) {
        } finally {
            if (img != null) img.close();
        }
    }

    void connect() {
        if (stopped) return;
        String url;
        if (lanIdx < lan.size()) { url = lan.get(lanIdx); viaLan = true; }
        else {
            url = server.replace("https://", "wss://").replace("http://", "ws://") + "/api/remote/hub/" + sid + "?code=" + code + "&role=phone";
            viaLan = false;
        }
        say(viaLan ? "正在局域网直连电脑…" : "正在经服务器连接电脑…");
        final WsClient[] self = new WsClient[1];
        self[0] = new WsClient(url, viaLan ? 1500 : 8000, new WsClient.Listener() {
            @Override public void onOpen() {
                connected = true; pendingAt = 0;
                self[0].sendText("{\"action\":\"cast_start\",\"data\":{\"source\":\"android\"}}");
                say(viaLan ? "投屏中（局域网直连，极速）" : "投屏中（服务器中转）");
            }
            @Override public void onText(String t) {
                if (t.contains("\"ack\"") && t.contains("\"C\"")) pendingAt = 0;
            }
            @Override public void onClose(String reason) {
                if (self[0] == ws) retry();
            }
        });
        ws = self[0];
        ws.connectAsync();
    }

    void retry() {
        boolean was = connected;
        connected = false;
        if (stopped) return;
        if (!was && lanIdx < lan.size()) {            // 下一个局域网地址 / 最后走服务器
            lanIdx++;
            worker.post(new Runnable() { @Override public void run() { connect(); } });
            return;
        }
        say("连接断开，正在重连…");
        worker.postDelayed(new Runnable() { @Override public void run() { lanIdx = 0; connect(); } }, 2000);
    }

    @Override
    public void onDestroy() {
        stopped = true;
        running = false;
        try { if (ws != null) { ws.sendText("{\"action\":\"cast_stop\"}"); ws.close(); } } catch (Exception ignored) { }
        if (display != null) display.release();
        if (reader != null) reader.close();
        if (projection != null) projection.stop();
        if (thread != null) thread.quitSafely();
        say("已停止投屏");
        super.onDestroy();
    }
}
