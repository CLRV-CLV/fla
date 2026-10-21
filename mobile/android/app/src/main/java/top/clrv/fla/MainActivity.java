package top.clrv.fla;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.content.res.ColorStateList;
import android.media.projection.MediaProjectionManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Vibrator;
import android.text.InputFilter;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
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
 * FLA 手机端: 原生 Material 风格首页、原生投屏/配对 UI 与屏幕采集服务。
 * 课件、AI、聊天等服务端模块目前在独立 WebView 功能页中运行；首页和主导航不再由网页驱动。
 * Gradle 发行链使用开源 Material Components 与 ZXing；独立 APK 构建保留系统控件降级路径。
 */
public class MainActivity extends Activity {
    static final String HOME = "https://app.fla/home.html";
    // Keep update comparisons in lockstep with app/build.gradle to avoid re-offering the same APK.
    static final String VERSION = BuildConfig.VERSION_NAME;
    static final int VERSION_CODE = BuildConfig.VERSION_CODE;
    static final String[] SERVERS = {"https://t.clrv.top", "https://t.fyx.best"};
    static final int REQ_CAPTURE = 7, REQ_CAM = 8, REQ_FILE = 9, REQ_SCAN = 0x0000c0de;

    WebView web;
    FrameLayout contentHost;
    LinearLayout bottomNav;
    TextView homeStatusView, castStatusView;
    EditText pairCodeView;
    String activePage = "home", activeTab = "home", activeServer = SERVERS[0];
    boolean scanFallback;
    final Handler ui = new Handler(Looper.getMainLooper());
    String sid, code, server;
    ArrayList<String> lanUrls = new ArrayList<>();
    PermissionRequest pendingPerm;
    ValueCallback<Uri[]> fileCb;
    volatile String curUrl = "app.fla://native";
    boolean autoStart;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Window w = getWindow();
        w.setStatusBarColor(Color.parseColor("#0c0d10"));
        w.setNavigationBarColor(Color.parseColor("#111318"));
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 1);

        activeServer = getPreferences(MODE_PRIVATE).getString("server", SERVERS[0]);
        if (!allowedServer(activeServer)) activeServer = SERVERS[0];
        LinearLayout shell = new LinearLayout(this);
        shell.setOrientation(LinearLayout.VERTICAL);
        shell.setBackgroundColor(Color.parseColor("#0c0d10"));
        contentHost = new FrameLayout(this);
        shell.addView(contentHost, new LinearLayout.LayoutParams(-1, 0, 1f));
        bottomNav = new LinearLayout(this);
        bottomNav.setOrientation(LinearLayout.HORIZONTAL);
        bottomNav.setGravity(Gravity.CENTER);
        bottomNav.setBackgroundColor(Color.parseColor("#15171c"));
        shell.addView(bottomNav, new LinearLayout.LayoutParams(-1, dp(64)));
        setContentView(shell);

        CastService.listener = new CastService.Listener() {
            @Override public void on(final String st) {
                ui.post(new Runnable() { @Override public void run() { pushStatus(st); } });
            }
        };
        renderTab("home");
        Intent i = getIntent();
        if (i != null && i.getData() != null && "fla".equals(i.getData().getScheme())) handleUri(i.getData());
        ui.postDelayed(new Runnable() { @Override public void run() { checkUpdate(); } }, 1500);
    }

    private int dp(float value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private TextView label(String text, float size, int color, boolean bold) {
        TextView v = new TextView(this);
        v.setText(text);
        v.setTextSize(TypedValue.COMPLEX_UNIT_SP, size);
        v.setTextColor(color);
        if (bold) v.setTypeface(android.graphics.Typeface.DEFAULT, android.graphics.Typeface.BOLD);
        return v;
    }

    private GradientDrawable rounded(int color, int stroke, int radius) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(radius));
        if (stroke != Color.TRANSPARENT) d.setStroke(dp(1), stroke);
        return d;
    }

    /** Use MaterialCardView from the Gradle build when present; the standalone builder keeps a native fallback. */
    private FrameLayout materialCard(int background, int stroke, int radius) {
        try {
            Class<?> type = Class.forName("com.google.android.material.card.MaterialCardView");
            FrameLayout card = (FrameLayout) type.getConstructor(Context.class).newInstance(this);
            type.getMethod("setRadius", float.class).invoke(card, (float) dp(radius));
            type.getMethod("setCardBackgroundColor", int.class).invoke(card, background);
            type.getMethod("setStrokeWidth", int.class).invoke(card, dp(1));
            type.getMethod("setStrokeColor", ColorStateList.class).invoke(card, ColorStateList.valueOf(stroke));
            return card;
        } catch (Throwable ignored) {
            FrameLayout card = new FrameLayout(this);
            card.setBackground(rounded(background, stroke, radius));
            card.setElevation(dp(2));
            return card;
        }
    }

    private void renderTab(String tab) {
        activeTab = tab;
        updateBottomNav();
        if ("home".equals(tab)) renderHome();
        else if ("features".equals(tab)) renderFeatures();
        else renderProfile();
    }

    private void updateBottomNav() {
        if (bottomNav == null) return;
        bottomNav.removeAllViews();
        addNavItem("⌂\n首页", "home", "home".equals(activeTab));
        addNavItem("▦\n功能", "features", "features".equals(activeTab) || "cast".equals(activePage) || "web".equals(activePage));
        addNavItem("●\n我的", "profile", "profile".equals(activeTab));
    }

    private void addNavItem(String text, final String tab, boolean selected) {
        TextView item = label(text, 11, Color.parseColor(selected ? "#f4f5f7" : "#858993"), selected);
        item.setGravity(Gravity.CENTER);
        item.setLineSpacing(dp(2), 1f);
        item.setClickable(true);
        item.setFocusable(true);
        item.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { renderTab(tab); }
        });
        bottomNav.addView(item, new LinearLayout.LayoutParams(0, -1, 1f));
    }

    private ScrollView newScrollColumn(LinearLayout[] columnOut) {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        scroll.setBackgroundColor(Color.parseColor("#0c0d10"));
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setPadding(dp(20), dp(18), dp(20), dp(28));
        scroll.addView(column, new ScrollView.LayoutParams(-1, -2));
        columnOut[0] = column;
        return scroll;
    }

    private void addSection(LinearLayout column, String title, String detail) {
        TextView h = label(title, 17, Color.parseColor("#f1f2f4"), true);
        column.addView(h, new LinearLayout.LayoutParams(-1, -2));
        if (detail != null && !detail.isEmpty()) {
            TextView d = label(detail, 12, Color.parseColor("#858993"), false);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
            lp.topMargin = dp(4);
            column.addView(d, lp);
        }
    }

    private FrameLayout featureCard(String icon, String title, String subtitle, final Runnable action, int minHeight) {
        FrameLayout card = materialCard(Color.parseColor("#171a20"), Color.parseColor("#292d35"), 18);
        card.setPadding(dp(15), dp(13), dp(15), dp(13));
        card.setMinimumHeight(dp(minHeight));
        card.setClickable(true);
        card.setFocusable(true);
        card.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { action.run(); } });
        LinearLayout inner = new LinearLayout(this);
        inner.setOrientation(LinearLayout.VERTICAL);
        TextView glyph = label(icon, 23, Color.parseColor("#d8e3ff"), true);
        TextView name = label(title, 15, Color.parseColor("#f5f6f8"), true);
        TextView hint = label(subtitle, 11.5f, Color.parseColor("#9297a2"), false);
        LinearLayout.LayoutParams gp = new LinearLayout.LayoutParams(-1, -2);
        inner.addView(glyph, gp);
        LinearLayout.LayoutParams np = new LinearLayout.LayoutParams(-1, -2);
        np.topMargin = dp(7);
        inner.addView(name, np);
        LinearLayout.LayoutParams hp = new LinearLayout.LayoutParams(-1, -2);
        hp.topMargin = dp(3);
        inner.addView(hint, hp);
        card.addView(inner, new FrameLayout.LayoutParams(-1, -2, Gravity.CENTER_VERTICAL));
        return card;
    }

    private void addGridRow(LinearLayout column, FrameLayout left, FrameLayout right) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams rowLp = new LinearLayout.LayoutParams(-1, -2);
        rowLp.topMargin = dp(10);
        column.addView(row, rowLp);
        LinearLayout.LayoutParams cell = new LinearLayout.LayoutParams(0, -2, 1f);
        cell.rightMargin = dp(5);
        row.addView(left, cell);
        LinearLayout.LayoutParams cell2 = new LinearLayout.LayoutParams(0, -2, 1f);
        cell2.leftMargin = dp(5);
        row.addView(right, cell2);
    }

    private void renderHome() {
        activePage = "home";
        curUrl = "app.fla://native";
        homeStatusView = null;
        contentHost.removeAllViews();
        LinearLayout[] holder = new LinearLayout[1];
        ScrollView scroll = newScrollColumn(holder);
        LinearLayout column = holder[0];
        TextView brand = label("FLA 课堂助手", 27, Color.parseColor("#f6f7f9"), true);
        column.addView(brand, new LinearLayout.LayoutParams(-1, -2));
        TextView welcome = label("课件、课堂交流、AI 助教，一站使用", 13, Color.parseColor("#999eaa"), false);
        LinearLayout.LayoutParams wp = new LinearLayout.LayoutParams(-1, -2);
        wp.topMargin = dp(5);
        column.addView(welcome, wp);

        FrameLayout statusCard = materialCard(Color.parseColor("#1c2028"), Color.parseColor("#2c333e"), 18);
        statusCard.setPadding(dp(16), dp(14), dp(16), dp(14));
        LinearLayout statusBox = new LinearLayout(this);
        statusBox.setOrientation(LinearLayout.VERTICAL);
        statusBox.addView(label("课堂状态", 12, Color.parseColor("#a2a8b3"), false));
        homeStatusView = label(lastStatus.isEmpty() ? "准备就绪 · 连接电脑即可开始" : lastStatus, 13, Color.parseColor("#eef0f4"), true);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(-1, -2);
        sp.topMargin = dp(5);
        statusBox.addView(homeStatusView, sp);
        statusCard.addView(statusBox, new FrameLayout.LayoutParams(-1, -2, Gravity.CENTER_VERTICAL));
        LinearLayout.LayoutParams scp = new LinearLayout.LayoutParams(-1, -2);
        scp.topMargin = dp(22);
        column.addView(statusCard, scp);

        LinearLayout.LayoutParams sectionLp = new LinearLayout.LayoutParams(-1, -2);
        sectionLp.topMargin = dp(26);
        TextView section = label("常用功能", 17, Color.parseColor("#f1f2f4"), true);
        column.addView(section, sectionLp);
        addGridRow(column,
            featureCard("▣", "课件库", "查看 · 上传 · 放映", new Runnable() { @Override public void run() { openFeature("library", "课件库"); } }, 118),
            featureCard("✦", "AI 助教", "备课 · 答疑 · 写作", new Runnable() { @Override public void run() { openFeature("ai", "AI 助教"); } }, 118));
        addGridRow(column,
            featureCard("◉", "课堂交流", "同事消息与讨论", new Runnable() { @Override public void run() { openFeature("chat", "课堂交流"); } }, 118),
            featureCard("▤", "教学论坛", "公告 · 经验分享", new Runnable() { @Override public void run() { openFeature("forum", "教学论坛"); } }, 118));

        FrameLayout cast = materialCard(Color.parseColor("#202936"), Color.parseColor("#41526a"), 20);
        cast.setPadding(dp(17), dp(15), dp(17), dp(15));
        cast.setMinimumHeight(dp(94));
        cast.setClickable(true);
        cast.setFocusable(true);
        cast.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { showCast(); } });
        LinearLayout castBox = new LinearLayout(this);
        castBox.setOrientation(LinearLayout.VERTICAL);
        castBox.addView(label("▱  手机投屏 / 课堂遥控", 16, Color.parseColor("#f5f7fa"), true));
        TextView castHint = label("作为课堂功能入口，不影响首页使用", 12, Color.parseColor("#acb7c8"), false);
        LinearLayout.LayoutParams chp = new LinearLayout.LayoutParams(-1, -2);
        chp.topMargin = dp(5);
        castBox.addView(castHint, chp);
        cast.addView(castBox, new FrameLayout.LayoutParams(-1, -2, Gravity.CENTER_VERTICAL));
        LinearLayout.LayoutParams cp = new LinearLayout.LayoutParams(-1, -2);
        cp.topMargin = dp(20);
        column.addView(cast, cp);
        contentHost.addView(scroll, new FrameLayout.LayoutParams(-1, -1));
        updateBottomNav();
    }

    private void renderFeatures() {
        activePage = "features";
        curUrl = "app.fla://native";
        contentHost.removeAllViews();
        LinearLayout[] holder = new LinearLayout[1];
        ScrollView scroll = newScrollColumn(holder);
        LinearLayout column = holder[0];
        column.addView(label("功能", 27, Color.parseColor("#f6f7f9"), true));
        TextView intro = label("选择一项课堂任务继续", 13, Color.parseColor("#9297a2"), false);
        LinearLayout.LayoutParams ilp = new LinearLayout.LayoutParams(-1, -2);
        ilp.topMargin = dp(5);
        column.addView(intro, ilp);
        addSection(column, "教学与协作", "");
        addFeatureRow(column, "▣", "课件库", "查看、上传与放映课件", "library");
        addFeatureRow(column, "✦", "AI 助教", "提问、备课与写作辅助", "ai");
        addFeatureRow(column, "◉", "课堂交流", "与同事进行消息交流", "chat");
        addFeatureRow(column, "▤", "教学论坛", "查看公告与经验分享", "forum");
        addFeatureRow(column, "▱", "手机投屏 / 课堂遥控", "连接电脑、投放屏幕或远程控制", "cast");
        contentHost.addView(scroll, new FrameLayout.LayoutParams(-1, -1));
        updateBottomNav();
    }

    private void addFeatureRow(LinearLayout column, String icon, String title, String hint, final String route) {
        FrameLayout row = featureCard(icon, title, hint, new Runnable() {
            @Override public void run() {
                if ("cast".equals(route)) showCast(); else openFeature(route, titleForRoute(route));
            }
        }, 84);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(10);
        column.addView(row, lp);
    }

    private String titleForRoute(String route) {
        if ("library".equals(route)) return "课件库";
        if ("ai".equals(route)) return "AI 助教";
        if ("chat".equals(route)) return "课堂交流";
        if ("forum".equals(route)) return "教学论坛";
        return "FLA";
    }

    private void renderProfile() {
        activePage = "profile";
        curUrl = "app.fla://native";
        contentHost.removeAllViews();
        LinearLayout[] holder = new LinearLayout[1];
        ScrollView scroll = newScrollColumn(holder);
        LinearLayout column = holder[0];
        column.addView(label("我的", 27, Color.parseColor("#f6f7f9"), true));
        TextView intro = label("FLA 课堂助手 · 手机端", 13, Color.parseColor("#9297a2"), false);
        LinearLayout.LayoutParams ilp = new LinearLayout.LayoutParams(-1, -2);
        ilp.topMargin = dp(5);
        column.addView(intro, ilp);
        addFeatureRow(column, "♙", "账号与个人资料", "登录、修改资料与密码", "profile");
        addFeatureRow(column, "⇧", "客户端更新", "当前版本 v" + VERSION, "mobile");
        TextView server = label("当前线路：" + activeServer, 12, Color.parseColor("#9297a2"), false);
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(-1, -2);
        slp.topMargin = dp(18);
        column.addView(server, slp);
        TextView loginHint = label("需要登录的课件、AI 与社区功能会在打开后要求登录。", 12, Color.parseColor("#777d88"), false);
        LinearLayout.LayoutParams hlp = new LinearLayout.LayoutParams(-1, -2);
        hlp.topMargin = dp(18);
        column.addView(loginHint, hlp);
        contentHost.addView(scroll, new FrameLayout.LayoutParams(-1, -1));
        updateBottomNav();
    }

    private void showCast() {
        activeTab = "features";
        activePage = "cast";
        curUrl = "app.fla://native";
        contentHost.removeAllViews();
        LinearLayout[] holder = new LinearLayout[1];
        ScrollView scroll = newScrollColumn(holder);
        LinearLayout column = holder[0];
        TextView back = label("‹  返回", 15, Color.parseColor("#cbd5e1"), true);
        back.setPadding(0, 0, 0, dp(16));
        back.setClickable(true);
        back.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { renderTab("home"); } });
        column.addView(back, new LinearLayout.LayoutParams(-1, -2));
        column.addView(label("手机投屏", 27, Color.parseColor("#f6f7f9"), true));
        TextView intro = label("投屏是 FLA 的一项课堂功能，首页和其他功能仍可独立使用。", 12, Color.parseColor("#9297a2"), false);
        LinearLayout.LayoutParams introLp = new LinearLayout.LayoutParams(-1, -2);
        introLp.topMargin = dp(5);
        column.addView(intro, introLp);

        FrameLayout status = materialCard(Color.parseColor("#1c2028"), Color.parseColor("#2c333e"), 18);
        status.setPadding(dp(16), dp(14), dp(16), dp(14));
        castStatusView = label(lastStatus.isEmpty() ? "请先在电脑端打开「手机」面板" : lastStatus, 13, Color.parseColor("#e8ebef"), true);
        status.addView(castStatusView, new FrameLayout.LayoutParams(-1, -2, Gravity.CENTER_VERTICAL));
        LinearLayout.LayoutParams statusLp = new LinearLayout.LayoutParams(-1, -2);
        statusLp.topMargin = dp(20);
        column.addView(status, statusLp);

        if (sid == null || code == null || server == null) {
            addSection(column, "连接教室电脑", "先在电脑端工具栏点「手机」，再扫码或输入 4 位配对码。");
            LinearLayout line = new LinearLayout(this);
            line.setOrientation(LinearLayout.HORIZONTAL);
            line.setGravity(Gravity.CENTER_VERTICAL);
            TextView lineOne = serverChoice("线路一", 0);
            TextView lineTwo = serverChoice("线路二", 1);
            line.addView(lineOne, new LinearLayout.LayoutParams(0, dp(42), 1f));
            LinearLayout.LayoutParams lineTwoLp = new LinearLayout.LayoutParams(0, dp(42), 1f);
            lineTwoLp.leftMargin = dp(8);
            line.addView(lineTwo, lineTwoLp);
            LinearLayout.LayoutParams lineLp = new LinearLayout.LayoutParams(-1, -2);
            lineLp.topMargin = dp(12);
            column.addView(line, lineLp);

            pairCodeView = new EditText(this);
            pairCodeView.setSingleLine(true);
            pairCodeView.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
            pairCodeView.setFilters(new InputFilter[]{new InputFilter.LengthFilter(4)});
            pairCodeView.setHint("输入电脑上的 4 位配对码");
            pairCodeView.setHintTextColor(Color.parseColor("#747b86"));
            pairCodeView.setTextColor(Color.WHITE);
            pairCodeView.setTextSize(18);
            pairCodeView.setGravity(Gravity.CENTER);
            pairCodeView.setBackground(rounded(Color.parseColor("#171a20"), Color.parseColor("#363b45"), 14));
            LinearLayout.LayoutParams codeLp = new LinearLayout.LayoutParams(-1, dp(54));
            codeLp.topMargin = dp(14);
            column.addView(pairCodeView, codeLp);

            LinearLayout actions = new LinearLayout(this);
            actions.setOrientation(LinearLayout.HORIZONTAL);
            TextView scan = actionButton("扫码连接", true);
            scan.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { openQrScan(); } });
            TextView pair = actionButton("连接", false);
            pair.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { pairNative(activeServer, pairCodeView.getText().toString().trim()); } });
            actions.addView(scan, new LinearLayout.LayoutParams(0, dp(48), 1f));
            LinearLayout.LayoutParams pairLp = new LinearLayout.LayoutParams(0, dp(48), 1f);
            pairLp.leftMargin = dp(8);
            actions.addView(pair, pairLp);
            LinearLayout.LayoutParams actionsLp = new LinearLayout.LayoutParams(-1, -2);
            actionsLp.topMargin = dp(10);
            column.addView(actions, actionsLp);
        } else {
            addSection(column, "电脑已连接", "配对码 " + code + " · " + server);
            TextView castAction = actionButton("开始整屏投屏", true);
            castAction.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { startCapture(); } });
            LinearLayout.LayoutParams castLp = new LinearLayout.LayoutParams(-1, dp(50));
            castLp.topMargin = dp(16);
            column.addView(castAction, castLp);
            TextView viewAction = actionButton("打开课堂遥控与观看", false);
            viewAction.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { openCastWeb(); } });
            LinearLayout.LayoutParams viewLp = new LinearLayout.LayoutParams(-1, dp(50));
            viewLp.topMargin = dp(9);
            column.addView(viewAction, viewLp);
            TextView stopAction = actionButton("结束手机投屏", false);
            stopAction.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) {
                stopService(new Intent(MainActivity.this, CastService.class));
                CastService.running = false;
                pushStatus("已停止整屏投屏");
            } });
            LinearLayout.LayoutParams stopLp = new LinearLayout.LayoutParams(-1, dp(50));
            stopLp.topMargin = dp(9);
            column.addView(stopAction, stopLp);
        }
        contentHost.addView(scroll, new FrameLayout.LayoutParams(-1, -1));
        updateBottomNav();
    }

    private TextView serverChoice(String title, final int index) {
        TextView choice = actionButton(title, activeServer.equals(SERVERS[index]));
        choice.setText(title + (index == 0 ? " · t.clrv.top" : " · t.fyx.best"));
        choice.setTextSize(11);
        choice.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                activeServer = SERVERS[index];
                getPreferences(MODE_PRIVATE).edit().putString("server", activeServer).apply();
                showCast();
            }
        });
        return choice;
    }

    private TextView actionButton(String text, boolean primary) {
        TextView button = label(text, 14, primary ? Color.parseColor("#0b1018") : Color.parseColor("#e8ebef"), true);
        button.setGravity(Gravity.CENTER);
        button.setBackground(rounded(primary ? Color.parseColor("#c7d8f3") : Color.parseColor("#242831"),
            primary ? Color.parseColor("#c7d8f3") : Color.parseColor("#363b45"), 14));
        button.setClickable(true);
        button.setFocusable(true);
        return button;
    }

    private void openQrScan() {
        // Gradle builds bundle ZXing's open-source Android scanner. The no-Gradle build falls back
        // to the existing jsQR camera page so the downloadable APK remains self-contained.
        try {
            Class<?> type = Class.forName("com.google.zxing.integration.android.IntentIntegrator");
            Object integrator = type.getConstructor(Activity.class).newInstance(this);
            type.getMethod("setPrompt", String.class).invoke(integrator, "将电脑上的二维码放入框内");
            type.getMethod("setBeepEnabled", boolean.class).invoke(integrator, false);
            type.getMethod("setOrientationLocked", boolean.class).invoke(integrator, false);
            type.getMethod("initiateScan").invoke(integrator);
            return;
        } catch (Throwable ignored) { }
        scanFallback = true;
        ensureWebView();
        showWebContainer("扫码连接电脑");
        web.loadUrl(HOME);
    }

    private void openFeature(String route, String title) {
        scanFallback = false;
        ensureWebView();
        showWebContainer(title);
        String base = activeServer == null ? SERVERS[0] : activeServer;
        web.loadUrl(base + "/#" + route);
    }

    private void openCastWeb() {
        if (sid == null || code == null || server == null) return;
        ensureWebView();
        showWebContainer("课堂遥控与观看");
        web.loadUrl(server + "/cast.html?sid=" + Uri.encode(sid) + "&code=" + Uri.encode(code) + "&tab=cast");
    }

    private void showWebContainer(String title) {
        activePage = scanFallback ? "scan" : "web";
        activeTab = "features";
        contentHost.removeAllViews();
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Color.parseColor("#0c0d10"));
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(16), dp(8), dp(16), dp(8));
        bar.setBackgroundColor(Color.parseColor("#15171c"));
        TextView back = label("‹", 29, Color.parseColor("#e8ebef"), false);
        back.setGravity(Gravity.CENTER);
        back.setOnClickListener(new View.OnClickListener() { @Override public void onClick(View v) { if (scanFallback) showCast(); else renderTab("features"); } });
        bar.addView(back, new LinearLayout.LayoutParams(dp(42), dp(42)));
        TextView heading = label(title, 16, Color.parseColor("#f5f6f8"), true);
        bar.addView(heading, new LinearLayout.LayoutParams(0, -2, 1f));
        page.addView(bar, new LinearLayout.LayoutParams(-1, dp(56)));
        if (web.getParent() != null) ((ViewGroup) web.getParent()).removeView(web);
        page.addView(web, new LinearLayout.LayoutParams(-1, 0, 1f));
        contentHost.addView(page, new FrameLayout.LayoutParams(-1, -1));
        updateBottomNav();
    }

    private void ensureWebView() {
        if (web != null) return;
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0c0d10"));
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        settings.setUserAgentString(settings.getUserAgentString() + " FLA-App/" + VERSION);
        web.addJavascriptInterface(new Bridge(), "FLA");
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest r) {
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
            @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                String scheme = u.getScheme();
                if ("fla".equals(scheme)) { handleUri(u); return true; }
                if ("http".equals(scheme) || "https".equals(scheme)) {
                    String path = u.getPath() == null ? "" : u.getPath();
                    if (path.startsWith("/api/app/")) {
                        try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception ignored) { }
                        return true;
                    }
                    return false;
                }
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception ignored) { }
                return true;
            }
            @Override public void onPageStarted(WebView v, String url, android.graphics.Bitmap f) { curUrl = url; }
            @Override public void onPageFinished(WebView v, String url) {
                curUrl = url;
                pushStatus();
                if (autoStart && url.contains("/cast.html") && sid != null) {
                    autoStart = false;
                    startCapture();
                }
                if (scanFallback && url.startsWith(HOME)) {
                    v.evaluateJavascript("setTimeout(function(){var b=document.getElementById('scanBtn');if(b)b.click()},250)", null);
                }
            }
            @Override public void onReceivedError(WebView v, WebResourceRequest r, android.webkit.WebResourceError e) {
                if (r.isForMainFrame()) {
                    toastJs("网络连接失败，请检查网络或切换线路");
                    if (scanFallback) showCast(); else renderTab("features");
                }
            }
        });
        web.setDownloadListener(new android.webkit.DownloadListener() {
            @Override public void onDownloadStart(String url, String ua, String cd, String mime, long len) {
                try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))); } catch (Exception e) { toastJs("无法打开下载"); }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onPermissionRequest(final PermissionRequest r) {
                ui.post(new Runnable() { @Override public void run() {
                    if (checkSelfPermission("android.permission.CAMERA") == PackageManager.PERMISSION_GRANTED) r.grant(r.getResources());
                    else { pendingPerm = r; requestPermissions(new String[]{"android.permission.CAMERA"}, REQ_CAM); }
                } });
            }
            @Override public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams params) {
                if (fileCb != null) fileCb.onReceiveValue(null);
                fileCb = cb;
                try {
                    Intent i = new Intent(Intent.ACTION_GET_CONTENT);
                    i.addCategory(Intent.CATEGORY_OPENABLE);
                    i.setType("image/*");
                    startActivityForResult(Intent.createChooser(i, "选择图片"), REQ_FILE);
                } catch (Exception e) { fileCb = null; return false; }
                return true;
            }
        });
    }

    private void pairNative(final String srv, final String pairCode) {
        if (pairCode == null || !pairCode.matches("[0-9]{4}")) {
            toastJs("请输入电脑显示的 4 位配对码");
            return;
        }
        if (!allowedServer(srv)) {
            toastJs("连接线路无效");
            return;
        }
        new Thread(new Runnable() { @Override public void run() {
            try {
                final JSONObject result = get(srv + "/api/remote/pair/" + Uri.encode(pairCode));
                ui.post(new Runnable() { @Override public void run() {
                    sid = result.optString("session_id", "");
                    if (sid.isEmpty()) { toastJs("配对失败，请检查电脑上的配对码"); return; }
                    code = pairCode;
                    server = srv;
                    activeServer = srv;
                    getPreferences(MODE_PRIVATE).edit().putString("server", srv).apply();
                    showCast();
                    pushStatus("已连接电脑，请选择投屏或课堂遥控");
                } });
            } catch (final Exception e) {
                ui.post(new Runnable() { @Override public void run() {
                    String message = String.valueOf(e.getMessage());
                    toastJs(message.contains("HTTP 4") ? "配对码无效，请确认电脑已打开「手机」面板" : "网络不通，请检查网络或切换线路");
                } });
            }
        } }).start();
    }

    private boolean allowedServer(String value) {
        try {
            Uri u = Uri.parse(value);
            String host = u.getHost();
            if (host == null || !("https".equals(u.getScheme()) || "http".equals(u.getScheme()))) return false;
            return host.equals("t.clrv.top") || host.equals("t.fyx.best") || host.endsWith(".clrv.top") || host.endsWith(".fyx.best")
                || host.startsWith("192.168.") || host.startsWith("10.")
                || host.matches("^172\\.(1[6-9]|2\\d|3[01])\\..*");
        } catch (Exception e) { return false; }
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

    /** fla://cast?sid=..&code=..&srv=.. is the desktop client's direct cast hand-off. */
    void handleUri(Uri u) {
        String newSid = u.getQueryParameter("sid");
        String newCode = u.getQueryParameter("code");
        String srv = u.getQueryParameter("srv");
        if ((srv == null || srv.isEmpty()) && u.getHost() != null && !"cast".equals(u.getHost()))
            srv = u.getScheme() + "://" + u.getHost();
        if (newSid == null || newCode == null || !allowedServer(srv)) { goHome(); return; }
        sid = newSid;
        code = newCode;
        server = srv;
        activeServer = srv;
        showCast();
        startCapture();
    }

    private void handleQrText(String text) {
        String value = text == null ? "" : text.trim();
        if (value.matches("[0-9]{4}")) {
            pairNative(activeServer, value);
            return;
        }
        try {
            Uri u = Uri.parse(value);
            String c = u.getQueryParameter("code");
            String srv = u.getQueryParameter("srv");
            if (srv == null || srv.isEmpty()) {
                String authority = u.getHost() + (u.getPort() > 0 ? ":" + u.getPort() : "");
                srv = u.getScheme() + "://" + authority;
            }
            if (c != null && c.matches("[0-9]{4}") && allowedServer(srv)) {
                pairNative(srv, c);
                return;
            }
        } catch (Exception ignored) { }
        toastJs("这不是 FLA 电脑端的配对二维码");
    }

    @Override
    public void onBackPressed() {
        if ("scan".equals(activePage)) { showCast(); return; }
        if ("web".equals(activePage)) {
            if (web != null && web.canGoBack()) { web.goBack(); return; }
            renderTab("features");
            return;
        }
        if ("cast".equals(activePage)) { renderTab("home"); return; }
        if (!"home".equals(activePage)) { renderTab("home"); return; }
        moveTaskToBack(true);
    }

    void goHome() {
        scanFallback = false;
        renderTab("home");
    }

    void toastJs(String s) {
        final String message = s == null ? "" : s;
        if (web == null || "home".equals(activePage) || "cast".equals(activePage) || "features".equals(activePage) || "profile".equals(activePage)) {
            Toast.makeText(this, message, Toast.LENGTH_LONG).show();
            return;
        }
        final String js = "setTimeout(function(){var m=document.getElementById('msg');if(m){m.textContent=" + JSONObject.quote(message)
            + ";m.style.display='block';setTimeout(function(){m.style.display='none'},3000)}},600)";
        ui.post(new Runnable() { @Override public void run() { if (web != null) web.evaluateJavascript(js, null); } });
    }

    String lastStatus = "";
    void pushStatus(String s) { lastStatus = s; pushStatus(); }
    void pushStatus() {
        String text = lastStatus.isEmpty() ? "准备就绪 · 连接电脑即可开始" : lastStatus;
        if (CastService.running && lastStatus.isEmpty()) text = "正在把手机屏幕投到电脑";
        if (homeStatusView != null) homeStatusView.setText(text);
        if (castStatusView != null) castStatusView.setText(text);
        if (web != null) {
            String js = "window.flaStatus&&window.flaStatus(" + JSONObject.quote(text) + "," + CastService.running + ")";
            web.evaluateJavascript(js, null);
        }
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
            if (!trusted() || !allowedServer(srv)) return;
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
        if (sid == null || code == null || server == null) {
            toastJs("请先连接电脑");
            return;
        }
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
        if (req == REQ_SCAN) {
            if (res == RESULT_OK && data != null) {
                String scanned = data.getStringExtra("SCAN_RESULT");
                if (scanned == null) scanned = data.getStringExtra("com.google.zxing.client.android.SCAN_RESULT");
                handleQrText(scanned);
            }
            return;
        }
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
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
