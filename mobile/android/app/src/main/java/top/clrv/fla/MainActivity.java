package top.clrv.fla;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.projection.MediaProjectionManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Vibrator;
import android.view.View;
import android.view.Window;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;

/**
 * FLA 手机端: WebView 外壳 + 原生整屏录制。
 *  - 首页 https://app.fla/home.html (内置 assets, 离线可用): 选线路 / 扫码 / 输配对码
 *  - 连接后加载 线路/cast.html (观看电脑·批注·翻页·摄像头投屏), 与网页版同步更新
 *  - window.FLA.startScreen() → MediaProjection 整屏 → CastService 推流到电脑
 */
public class MainActivity extends Activity {
    static final String HOME = "https://app.fla/home.html";
    // Keep update comparisons in lockstep with app/build.gradle to avoid re-offering the same APK.
    static final String VERSION = BuildConfig.VERSION_NAME;
    static final int VERSION_CODE = BuildConfig.VERSION_CODE;
    static final String[] SERVERS = {"https://t.clrv.top", "https://t.fyx.best"};
    static final int REQ_CAPTURE = 7, REQ_CAM = 8, REQ_FILE = 9;

    WebView web;
    final Handler ui = new Handler(Looper.getMainLooper());
    String sid, code, server;
    ArrayList<String> lanUrls = new ArrayList<>();
    PermissionRequest pendingPerm;
    ValueCallback<Uri[]> fileCb;
    volatile String curUrl = HOME;
    boolean autoStart;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Window w = getWindow();
        w.setStatusBarColor(Color.parseColor("#0b0b0c"));
        w.setNavigationBarColor(Color.parseColor("#0b0b0c"));
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 1);

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0b0b0c"));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        s.setUserAgentString(s.getUserAgentString() + " FLA-App/" + VERSION);
        web.addJavascriptInterface(new Bridge(), "FLA");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                if (!"app.fla".equals(u.getHost())) return null;
                String path = u.getPath() == null || u.getPath().equals("/") ? "home.html" : u.getPath().substring(1);
                try {
                    String mime = path.endsWith(".js") ? "application/javascript" : path.endsWith(".png") ? "image/png" : "text/html";
                    return new WebResourceResponse(mime, "UTF-8", getAssets().open(path));
                } catch (Exception e) {
                    return new WebResourceResponse("text/plain", "UTF-8", 404, "Not Found", null, null);
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                String sc = u.getScheme();
                if ("fla".equals(sc)) { handleUri(u); return true; }
                if ("http".equals(sc) || "https".equals(sc)) {
                    String p = u.getPath() == null ? "" : u.getPath();
                    if (p.startsWith("/api/app/")) {           // 下载链接交给系统浏览器
                        startActivity(new Intent(Intent.ACTION_VIEW, u));
                        return true;
                    }
                    return false;
                }
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception ignored) { }
                return true;
            }

            @Override
            public void onPageStarted(WebView v, String url, android.graphics.Bitmap f) { curUrl = url; }

            @Override
            public void onPageFinished(WebView v, String url) {
                curUrl = url;
                pushStatus();
                if (autoStart && url.contains("/cast.html") && sid != null) {
                    autoStart = false;
                    startCapture();
                }
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, android.webkit.WebResourceError e) {
                if (r.isForMainFrame() && !r.getUrl().toString().startsWith(HOME)) {
                    toastJs("网络连接失败，请检查网络或切换线路");
                    v.loadUrl(HOME);
                }
            }
        });
        // 网站里的文件下载 (课件等) → 交给系统浏览器/下载器
        web.setDownloadListener(new android.webkit.DownloadListener() {
            @Override public void onDownloadStart(String url, String ua, String cd, String mime, long len) {
                try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))); } catch (Exception e) { toastJs("无法打开下载"); }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(final PermissionRequest r) {
                ui.post(new Runnable() { @Override public void run() {
                    if (checkSelfPermission("android.permission.CAMERA") == PackageManager.PERMISSION_GRANTED) {
                        r.grant(r.getResources());
                    } else {
                        pendingPerm = r;
                        requestPermissions(new String[]{"android.permission.CAMERA"}, REQ_CAM);
                    }
                } });
            }

            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (fileCb != null) fileCb.onReceiveValue(null);
                fileCb = cb;
                try {
                    Intent i = new Intent(Intent.ACTION_GET_CONTENT);
                    i.addCategory(Intent.CATEGORY_OPENABLE);
                    i.setType("image/*");
                    startActivityForResult(Intent.createChooser(i, "选择图片"), REQ_FILE);
                } catch (Exception e) {
                    fileCb = null;
                    return false;
                }
                return true;
            }
        });
        setContentView(web);

        CastService.listener = new CastService.Listener() {
            @Override public void on(final String st) { ui.post(new Runnable() { @Override public void run() { pushStatus(st); } }); }
        };
        Intent i = getIntent();
        if (i != null && i.getData() != null && "fla".equals(i.getData().getScheme())) handleUri(i.getData());
        else web.loadUrl(HOME);
        ui.postDelayed(new Runnable() { @Override public void run() { checkUpdate(); } }, 1500);
    }

    // ================= 自动更新: 每次打开检查 → 后台下载 → 下载好后弹窗安装 =================
    boolean updating;
    void checkUpdate() {
        if (updating) return;
        updating = true;
        new Thread(new Runnable() { @Override public void run() {
            try {
                JSONObject info = null;
                String base = null;
                for (String s : SERVERS) {
                    try { info = get(s + "/api/app/info"); base = s; break; } catch (Exception ignored) { }
                }
                if (info == null) return;
                JSONObject a = info.optJSONObject("android");
                if (a == null || !a.optBoolean("available")) return;
                int code = a.optInt("code", 0);
                final String ver = a.optString("version", "");
                if (code <= VERSION_CODE) return;
                long size = a.optLong("size", 0);
                java.io.File f = UpdateProvider.apkFile(MainActivity.this);
                java.io.File part = new java.io.File(getCacheDir(), "update.part");
                HttpURLConnection c = (HttpURLConnection) new URL(base + a.optString("url", "/api/app/android")).openConnection();
                c.setConnectTimeout(10000);
                c.setReadTimeout(30000);
                c.setRequestProperty("User-Agent", "FLA-Android/" + VERSION);
                if (c.getResponseCode() != 200) return;
                InputStream in = c.getInputStream();
                java.io.FileOutputStream out = new java.io.FileOutputStream(part);
                byte[] buf = new byte[16384];
                long got = 0;
                int n;
                while ((n = in.read(buf)) > 0) { out.write(buf, 0, n); got += n; }
                out.close();
                in.close();
                c.disconnect();
                if (size > 0 && got != size) { part.delete(); return; }
                f.delete();
                if (!part.renameTo(f)) return;
                ui.post(new Runnable() { @Override public void run() { askInstall(ver); } });
            } catch (Exception ignored) {
            } finally {
                updating = false;
            }
        } }).start();
    }

    void askInstall(String ver) {
        if (isFinishing()) return;
        new AlertDialog.Builder(this).setTitle("新版本已下载")
            .setMessage("FLA 手机端 v" + ver + " 已下载完成（当前 v" + VERSION + "），现在安装？")
            .setPositiveButton("立即安装", new android.content.DialogInterface.OnClickListener() {
                @Override public void onClick(android.content.DialogInterface d, int w) { install(); }
            })
            .setNegativeButton("稍后", null).show();
    }

    void install() {
        if (Build.VERSION.SDK_INT >= 26 && !getPackageManager().canRequestPackageInstalls()) {
            toastJs("请允许「FLA 手机端」安装应用，返回后再点安装");
            try {
                startActivity(new Intent("android.settings.MANAGE_UNKNOWN_APP_SOURCES", Uri.parse("package:" + getPackageName())));
            } catch (Exception ignored) { }
            pendingInstall = true;
            return;
        }
        Intent i = new Intent(Intent.ACTION_VIEW);
        i.setDataAndType(Uri.parse("content://" + UpdateProvider.AUTH + "/update.apk"), "application/vnd.android.package-archive");
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        try { startActivity(i); } catch (Exception e) { toastJs("无法打开安装器：" + e.getMessage()); }
    }

    boolean pendingInstall;
    @Override
    protected void onResume() {
        super.onResume();
        if (pendingInstall && UpdateProvider.apkFile(this).exists()) {
            pendingInstall = false;
            if (Build.VERSION.SDK_INT < 26 || getPackageManager().canRequestPackageInstalls()) install();
        }
    }

    @Override
    protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        if (i != null && i.getData() != null && "fla".equals(i.getData().getScheme())) handleUri(i.getData());
    }

    /** fla://cast?sid=..&code=..&srv=..  浏览器网页一键唤起 → 打开会话页并开始整屏投屏 */
    void handleUri(Uri u) {
        String s = u.getQueryParameter("sid"), c = u.getQueryParameter("code"), srv = u.getQueryParameter("srv");
        if (s == null || c == null || srv == null || !srv.startsWith("http")) { web.loadUrl(HOME); return; }
        sid = s; code = c; server = srv;
        autoStart = true;
        web.loadUrl(srv + "/cast.html?sid=" + Uri.encode(s) + "&code=" + Uri.encode(c) + "&tab=cast");
    }

    @Override
    public void onBackPressed() {
        String u = curUrl == null ? "" : curUrl;
        if (u.startsWith(HOME)) { moveTaskToBack(true); return; }
        // 在网站页面 (课件库/聊天/论坛...) 时: 返回键 = 网页后退
        if (!u.contains("/cast.html") && web.canGoBack()) { web.goBack(); return; }
        if (!u.contains("/cast.html")) { web.loadUrl(HOME); return; }
        new AlertDialog.Builder(this).setMessage("断开与电脑的连接并返回首页？")
            .setPositiveButton("返回首页", new android.content.DialogInterface.OnClickListener() {
                @Override public void onClick(android.content.DialogInterface d, int w) { goHome(); }
            })
            .setNegativeButton("取消", null).show();
    }

    void goHome() {
        if (CastService.running) stopService(new Intent(this, CastService.class));
        web.loadUrl(HOME);
    }

    void toastJs(String s) {
        final String js = "setTimeout(function(){var m=document.getElementById('msg');if(m){m.textContent=" + JSONObject.quote(s)
            + ";m.style.display='block';setTimeout(function(){m.style.display='none'},3000)}},600)";
        ui.post(new Runnable() { @Override public void run() { web.evaluateJavascript(js, null); } });
    }

    String lastStatus = "";
    void pushStatus(String s) { lastStatus = s; pushStatus(); }
    void pushStatus() {
        String js = "window.flaStatus&&window.flaStatus(" + JSONObject.quote(lastStatus.isEmpty()
            ? "PPT、相册、任何 App 都能实时显示在大屏上" : lastStatus) + "," + CastService.running + ")";
        web.evaluateJavascript(js, null);
    }

    /** 只信任我们的线路和局域网电脑 (桥接方法会触发录屏) */
    boolean trusted() {
        try {
            String h = Uri.parse(curUrl).getHost();
            if (h == null) return false;
            return h.equals("app.fla") || h.endsWith("clrv.top") || h.endsWith("fyx.best")
                || h.startsWith("192.168.") || h.startsWith("10.") || h.matches("^172\\.(1[6-9]|2\\d|3[01])\\..*");
        } catch (Exception e) { return false; }
    }

    class Bridge {
        @JavascriptInterface public String version() { return VERSION; }

        @JavascriptInterface public void vibrate() {
            try { ((Vibrator) getSystemService(VIBRATOR_SERVICE)).vibrate(40); } catch (Exception ignored) { }
        }

        @JavascriptInterface public void home() { ui.post(new Runnable() { @Override public void run() { goHome(); } }); }

        @JavascriptInterface public void pair(final String srv, final String c) {
            new Thread(new Runnable() { @Override public void run() {
                String js;
                try {
                    JSONObject p = get(srv + "/api/remote/pair/" + Uri.encode(c));
                    js = "onPair(true," + JSONObject.quote(srv) + "," + JSONObject.quote(p.getString("session_id")) + "," + JSONObject.quote(c) + ")";
                } catch (Exception e) {
                    String m = String.valueOf(e.getMessage());
                    js = "onPair(false,'','','', " + JSONObject.quote(m.contains("HTTP 4") ? "配对码无效，请确认电脑已打开「手机」面板" : "网络不通，请检查网络或切换线路") + ")";
                }
                final String f = js;
                ui.post(new Runnable() { @Override public void run() { web.evaluateJavascript(f, null); } });
            } }).start();
        }

        @JavascriptInterface public void startScreen(final String s, final String c, final String srv) {
            if (!trusted()) return;
            ui.post(new Runnable() { @Override public void run() {
                sid = s; code = c; server = srv;
                startCapture();
            } });
        }

        @JavascriptInterface public void stopScreen() {
            ui.post(new Runnable() { @Override public void run() {
                stopService(new Intent(MainActivity.this, CastService.class));
                CastService.running = false;
                pushStatus("已停止整屏投屏");
            } });
        }
    }

    void startCapture() {
        pushStatus("正在查找电脑…");
        new Thread(new Runnable() { @Override public void run() {
            lanUrls.clear();
            try {
                JSONObject info = get(server + "/api/remote/" + sid + "/info?code=" + code);
                JSONArray lan = info.optJSONArray("lan");
                if (lan != null) for (int k = 0; k < lan.length(); k++) lanUrls.add(lan.getString(k));
            } catch (Exception ignored) { }
            ui.post(new Runnable() { @Override public void run() {
                pushStatus("请在系统弹窗中选择「整个屏幕」并点「立即开始」");
                MediaProjectionManager m = (MediaProjectionManager) getSystemService(Context.MEDIA_PROJECTION_SERVICE);
                startActivityForResult(m.createScreenCaptureIntent(), REQ_CAPTURE);
            } });
        } }).start();
    }

    JSONObject get(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(8000);
        c.setReadTimeout(10000);
        c.setRequestProperty("User-Agent", "FLA-Android/" + VERSION);
        try {
            int rc = c.getResponseCode();
            if (rc != 200) throw new Exception("HTTP " + rc);
            InputStream in = c.getInputStream();
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) > 0) b.write(buf, 0, n);
            return new JSONObject(new String(b.toByteArray(), "UTF-8"));
        } finally {
            c.disconnect();
        }
    }

    @Override
    public void onRequestPermissionsResult(int req, String[] p, int[] g) {
        if (req == REQ_CAM && pendingPerm != null) {
            if (g.length > 0 && g[0] == PackageManager.PERMISSION_GRANTED) pendingPerm.grant(pendingPerm.getResources());
            else pendingPerm.deny();
            pendingPerm = null;
        }
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req == REQ_FILE) {
            if (fileCb != null) fileCb.onReceiveValue(res == RESULT_OK && data != null && data.getData() != null ? new Uri[]{data.getData()} : null);
            fileCb = null;
            return;
        }
        if (req != REQ_CAPTURE) return;
        if (res != RESULT_OK || data == null) { pushStatus("已取消屏幕录制授权"); return; }
        Intent s = new Intent(this, CastService.class);
        s.putExtra("result", res);
        s.putExtra("data", data);
        s.putExtra("sid", sid);
        s.putExtra("code", code);
        s.putExtra("server", server);
        s.putStringArrayListExtra("lan", lanUrls);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(s); else startService(s);
        pushStatus("正在连接电脑…");
        moveTaskToBack(true);     // 回到桌面, 打开 PPT / 相册即可投到大屏
    }

    @Override
    protected void onDestroy() {
        CastService.listener = null;
        web.destroy();
        super.onDestroy();
    }
}
