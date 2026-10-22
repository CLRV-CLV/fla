using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace FLA
{
    /* ================================================================
     * 主题常量 — 与 Web 端黑白极简设计一致
     * ================================================================ */
    public static class Theme
    {
        public static readonly Color Ink = Color.FromArgb(9, 9, 11);        // 主黑
        public static readonly Color Ink2 = Color.FromArgb(24, 24, 27);     // 卡片黑
        public static readonly Color Ink3 = Color.FromArgb(39, 39, 42);     // 悬停黑
        public static readonly Color Paper = Color.White;
        public static readonly Color Subtle = Color.FromArgb(244, 244, 245);// 浅灰底
        public static readonly Color Hover = Color.FromArgb(235, 235, 237); // 浅灰悬停
        public static readonly Color Line = Color.FromArgb(228, 228, 231);  // 分隔线
        public static readonly Color Mut = Color.FromArgb(113, 113, 122);   // 次要文字
        public static readonly Color Mut2 = Color.FromArgb(161, 161, 170);  // 弱文字
        public static readonly Color Danger = Color.FromArgb(220, 38, 38);

        public const string FamilyName = "Microsoft YaHei UI";
        public static Font F(float size, bool bold)
        {
            try { return new Font(FamilyName, size, bold ? FontStyle.Bold : FontStyle.Regular); }
            catch { return new Font(FontFamily.GenericSansSerif, size); }
        }
        public static Font F(float size) { return F(size, false); }

        public static GraphicsPath Rounded(Rectangle r, int rad)
        {
            GraphicsPath p = new GraphicsPath();
            if (rad < 1) { p.AddRectangle(r); return p; }
            if (rad * 2 > r.Width) rad = r.Width / 2;
            if (rad * 2 > r.Height) rad = r.Height / 2;
            int d = rad * 2;
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d - 1, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d - 1, r.Bottom - d - 1, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d - 1, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        public static void RoundForm(Form f, int rad)
        {
            try { f.Region = new Region(Rounded(new Rectangle(0, 0, f.Width, f.Height), rad)); } catch { }
        }
    }

    /* ================================================================
     * 扁平圆角按钮 (自绘, 支持悬停/按下/描边)
     * ================================================================ */
    public class FlatButton : Control
    {
        public int Radius = 10;
        public Color HoverColor = Color.FromArgb(39, 39, 42);
        public Color BorderColor = Color.Transparent;
        private bool hovered = false;
        private bool pressed = false;

        public FlatButton()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.ResizeRedraw | ControlStyles.UserPaint | ControlStyles.SupportsTransparentBackColor, true);
            BackColor = Theme.Ink;
            ForeColor = Color.White;
            Font = Theme.F(9f, true);
            Cursor = Cursors.Hand;
            TabStop = false;
        }

        protected override void OnMouseEnter(EventArgs e) { hovered = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hovered = false; pressed = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { if (e.Button == MouseButtons.Left) { pressed = true; Invalidate(); } base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { pressed = false; Invalidate(); base.OnMouseUp(e); }
        protected override void OnTextChanged(EventArgs e) { Invalidate(); base.OnTextChanged(e); }
        protected override void OnEnabledChanged(EventArgs e) { Invalidate(); base.OnEnabledChanged(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            Rectangle r = new Rectangle(0, 0, Width - 1, Height - 1);
            Color bg = !Enabled ? Theme.Line : (pressed ? PressedColor : (hovered ? HoverColor : BackColor));
            using (GraphicsPath p = Theme.Rounded(r, Radius))
            {
                using (SolidBrush b = new SolidBrush(bg)) g.FillPath(b, p);
                if (BorderColor.A > 0)
                {
                    using (Pen pen = new Pen(BorderColor, 1f)) g.DrawPath(pen, p);
                }
            }
            Color fc = Enabled ? ForeColor : Theme.Mut2;
            TextRenderer.DrawText(g, Text, Font, r, fc,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine);
            if (Focused && ShowFocusCues && Enabled)
            {
                using (Pen fp = new Pen(Color.FromArgb(90, ForeColor), 1f))
                    using (GraphicsPath p2 = Theme.Rounded(new Rectangle(2, 2, Width - 5, Height - 5), Math.Max(2, Radius - 2)))
                        g.DrawPath(fp, p2);
            }
        }

        public Color PressedColor = Color.FromArgb(63, 63, 63);
    }

    /* ================================================================
     * 圆角卡片容器 (白色 + 1px 细边)
     * ================================================================ */
    public class Card : Panel
    {
        public int Radius = 14;
        public Card()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.ResizeRedraw | ControlStyles.UserPaint, true);
            BackColor = Color.White;
            Padding = new Padding(20);
        }
        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using (GraphicsPath p = Theme.Rounded(new Rectangle(0, 0, Width - 1, Height - 1), Radius))
            {
                using (SolidBrush b = new SolidBrush(BackColor)) g.FillPath(b, p);
                using (Pen pen = new Pen(Theme.Line, 1f)) g.DrawPath(pen, p);
            }
            base.OnPaint(e);
        }
        protected override void OnBackColorChanged(EventArgs e) { Invalidate(); base.OnBackColorChanged(e); }
    }

    /* ================================================================
     * 深色圆角小圆点状态灯
     * ================================================================ */
    public class StatusDot : Control
    {
        public bool Online = false;
        public StatusDot()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.UserPaint | ControlStyles.SupportsTransparentBackColor, true);
            Size = new Size(12, 12);
            BackColor = Color.Transparent;
        }
        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using (SolidBrush b = new SolidBrush(Online ? Theme.Ink : Theme.Mut2))
                g.FillEllipse(b, 1, 1, Width - 3, Height - 3);
        }
        public void Set(bool online) { Online = online; Invalidate(); }
    }

    /* ================================================================
     * 菜单渲染器 (黑白, 去系统蓝)
     * ================================================================ */
    public class FlaMenuRenderer : ToolStripProfessionalRenderer
    {
        protected override void OnRenderMenuItemBackground(ToolStripItemRenderEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            Rectangle r = new Rectangle(Point.Empty, e.Item.Size);
            if (e.Item.Selected)
            {
                using (SolidBrush b = new SolidBrush(Theme.Ink))
                    g.FillRectangle(b, 1, 1, r.Width - 2, r.Height - 2);
                e.Item.ForeColor = Color.White;
            }
            else
            {
                using (SolidBrush b = new SolidBrush(Color.White))
                    g.FillRectangle(b, 1, 1, r.Width - 2, r.Height - 2);
                e.Item.ForeColor = Theme.Ink;
            }
        }
        protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e)
        {
            using (Pen p = new Pen(Theme.Line, 1f))
                e.Graphics.DrawRectangle(p, 0, 0, e.ToolStrip.Width - 1, e.ToolStrip.Height - 1);
        }
        protected override void OnRenderItemImage(ToolStripItemImageRenderEventArgs e)
        {
            return; // 纯文字菜单
        }
    }

    #region 应用程序主入口与全局配置
    public class Program
    {
        public const string VERSION = "1.37.0";
        public const int PORT = 8307;
        public static string ServerUrl = "http://127.0.0.1:8306";
        public static string Token = "";
        public static string UserName = "";
        public static bool SeewoEnabled = true;
        public static string CacheDir;
        public static MainForm MainWindow;
        public static FloatingDockForm FloatingDock;
        public static ScreenOverlayForm OverlayCanvas;
        internal static ToolTip DockTips = new ToolTip();
        private static HttpListener httpListener;
        private static Thread serverThread;
        private static Thread seewoThread;
        private static bool isRunning = true;

        [STAThread]
        public static void Main(string[] args)
        {
            bool isNew;
            using (Mutex mutex = new Mutex(true, "FLA_Desktop_Mutex_" + VERSION, out isNew))
            {
                if (!isNew)
                {
                    if (args.Length > 0 && args[0].StartsWith("fla://", StringComparison.OrdinalIgnoreCase))
                    {
                        ForwardProtocol(args[0]);
                    }
                    return;
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);

                CacheDir = Path.Combine(Path.GetTempPath(), "FLA_Cache");
                try { if (!Directory.Exists(CacheDir)) Directory.CreateDirectory(CacheDir); } catch { }

                LoadConfig();
                RegisterProtocol();
                StartLocalServer();
                StartSeewoInterceptor();

                MainWindow = new MainForm();
                OverlayCanvas = new ScreenOverlayForm();
                FloatingDock = new FloatingDockForm();

                if (args.Length > 0 && args[0].StartsWith("fla://", StringComparison.OrdinalIgnoreCase))
                {
                    HandleProtocolUrl(args[0]);
                }

                Application.Run(MainWindow);
                isRunning = false;
                try { if (httpListener != null) httpListener.Stop(); } catch { }
            }
        }

        private static string ConfigFile()
        {
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FLA", "config.ini");
        }

        private static void LoadConfig()
        {
            try
            {
                string cfgFile = ConfigFile();
                if (File.Exists(cfgFile))
                {
                    foreach (string line in File.ReadAllLines(cfgFile))
                    {
                        if (line.StartsWith("server_url=", StringComparison.OrdinalIgnoreCase))
                        {
                            string u = line.Substring("server_url=".Length).Trim();
                            if (!string.IsNullOrEmpty(u)) ServerUrl = u;
                        }
                        else if (line.StartsWith("token=", StringComparison.OrdinalIgnoreCase))
                        {
                            Token = line.Substring("token=".Length).Trim();
                        }
                        else if (line.StartsWith("user=", StringComparison.OrdinalIgnoreCase))
                        {
                            UserName = line.Substring("user=".Length).Trim();
                        }
                    }
                }
            }
            catch { }
        }

        public static void SaveConfig()
        {
            try
            {
                string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "FLA");
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                StringBuilder sb = new StringBuilder();
                sb.Append("server_url=").Append(ServerUrl).AppendLine();
                sb.Append("token=").Append(Token).AppendLine();
                sb.Append("user=").Append(UserName).AppendLine();
                File.WriteAllText(Path.Combine(dir, "config.ini"), sb.ToString(), Encoding.UTF8);
            }
            catch { }
        }

        public static void Logout()
        {
            Token = "";
            UserName = "";
            SaveConfig();
        }

        public static void RegisterProtocol()
        {
            try
            {
                string exePath = Application.ExecutablePath;
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(@"Software\Classes\fla"))
                {
                    key.SetValue("", "URL:FLA Protocol");
                    key.SetValue("URL Protocol", "");
                    using (RegistryKey iconKey = key.CreateSubKey("DefaultIcon"))
                    {
                        iconKey.SetValue("", exePath + ",1");
                    }
                    using (RegistryKey cmdKey = key.CreateSubKey(@"shell\open\command"))
                    {
                        cmdKey.SetValue("", "\"" + exePath + "\" \"%1\"");
                    }
                }
            }
            catch { }
        }

        private static void StartLocalServer()
        {
            serverThread = new Thread(() =>
            {
                try
                {
                    httpListener = new HttpListener();
                    httpListener.Prefixes.Add("http://127.0.0.1:" + PORT + "/");
                    httpListener.Start();
                    while (isRunning)
                    {
                        try
                        {
                            HttpListenerContext ctx = httpListener.GetContext();
                            ThreadPool.QueueUserWorkItem(o => HandleHttpRequest(ctx));
                        }
                        catch { if (!isRunning) break; }
                    }
                }
                catch { }
            })
            { IsBackground = true };
            serverThread.Start();
        }

        public static bool BridgeRunning
        {
            get { try { return httpListener != null && httpListener.IsListening; } catch { return false; } }
        }

        private static void HandleHttpRequest(HttpListenerContext ctx)
        {
            try
            {
                ctx.Response.Headers.Add("Access-Control-Allow-Origin", "*");
                ctx.Response.Headers.Add("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
                ctx.Response.Headers.Add("Access-Control-Allow-Headers", "Content-Type");
                if (ctx.Request.HttpMethod == "OPTIONS")
                {
                    ctx.Response.StatusCode = 204;
                    ctx.Response.Close();
                    return;
                }

                string path = ctx.Request.Url.AbsolutePath;
                string respJson = "{\"ok\":true}";

                if (path == "/api/status")
                {
                    respJson = string.Format("{{\"ok\":true,\"version\":\"{0}\",\"status\":\"running\",\"service\":\"fla_desktop\"}}", VERSION);
                }
                else if (path == "/api/open" && ctx.Request.HttpMethod == "POST")
                {
                    using (StreamReader reader = new StreamReader(ctx.Request.InputStream, Encoding.UTF8))
                    {
                        string body = reader.ReadToEnd();
                        string url = ExtractJsonVal(body, "url");
                        string name = ExtractJsonVal(body, "name");
                        string token = ExtractJsonVal(body, "token");
                        if (!string.IsNullOrEmpty(url))
                        {
                            ThreadPool.QueueUserWorkItem(o => LaunchOfficePresentation(url, name, token));
                            respJson = "{\"ok\":true,\"msg\":\"正在调起本地放映并激活工具盒\"}";
                        }
                        else
                        {
                            respJson = "{\"ok\":false,\"msg\":\"缺少 url 参数\"}";
                        }
                    }
                }
                else if (path == "/api/control" && ctx.Request.HttpMethod == "POST")
                {
                    using (StreamReader reader = new StreamReader(ctx.Request.InputStream, Encoding.UTF8))
                    {
                        string body = reader.ReadToEnd();
                        string cmd = ExtractJsonVal(body, "command");
                        ExecuteControlCommand(cmd);
                        respJson = "{\"ok\":true}";
                    }
                }

                byte[] buf = Encoding.UTF8.GetBytes(respJson);
                ctx.Response.ContentType = "application/json; charset=utf-8";
                ctx.Response.ContentLength64 = buf.Length;
                ctx.Response.OutputStream.Write(buf, 0, buf.Length);
                ctx.Response.Close();
            }
            catch { }
        }

        public static void ExecuteControlCommand(string cmd)
        {
            if (string.IsNullOrEmpty(cmd)) return;
            switch (cmd.ToLower())
            {
                case "next": SendKeys.SendWait("{PGDN}"); break;
                case "prev": SendKeys.SendWait("{PGUP}"); break;
                case "first": SendKeys.SendWait("{HOME}"); break;
                case "last": SendKeys.SendWait("{END}"); break;
                case "black": SendKeys.SendWait("b"); break;
                case "white": SendKeys.SendWait("w"); break;
                case "laser":
                    if (OverlayCanvas != null) OverlayCanvas.ToggleLaser();
                    break;
                case "pen":
                    if (OverlayCanvas != null) OverlayCanvas.SetTool(ScreenOverlayForm.ToolType.Pen);
                    break;
                case "eraser":
                    if (OverlayCanvas != null) OverlayCanvas.SetTool(ScreenOverlayForm.ToolType.Eraser);
                    break;
                case "clear":
                    if (OverlayCanvas != null) OverlayCanvas.ClearCurrentPage();
                    break;
                case "dock_toggle":
                    if (FloatingDock != null) FloatingDock.ToggleDock();
                    break;
            }
        }

        public static string ExtractJsonVal(string json, string key)
        {
            try
            {
                string search = "\"" + key + "\":";
                int idx = json.IndexOf(search, StringComparison.OrdinalIgnoreCase);
                if (idx < 0) return "";
                int start = json.IndexOf('"', idx + search.Length);
                if (start < 0) return "";
                int end = json.IndexOf('"', start + 1);
                if (end < 0) return "";
                return json.Substring(start + 1, end - start - 1);
            }
            catch { return ""; }
        }

        /* ---------- 云端 API 访问 (带登录态) ---------- */

        public static string ApiGet(string path)
        {
            return ApiRequest("GET", path, null);
        }

        public static string ApiPost(string path, string jsonBody)
        {
            return ApiRequest("POST", path, jsonBody);
        }

        public static string ApiRequest(string method, string path, string jsonBody)
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(ServerUrl.TrimEnd('/') + path);
            req.Method = method;
            req.Timeout = 10000;
            req.ReadWriteTimeout = 10000;
            req.ContentType = "application/json";
            if (!string.IsNullOrEmpty(Token))
                req.Headers.Add(HttpRequestHeader.Authorization, "Bearer " + Token);
            if (jsonBody != null)
            {
                byte[] data = Encoding.UTF8.GetBytes(jsonBody);
                req.ContentLength = data.Length;
                using (Stream s = req.GetRequestStream()) s.Write(data, 0, data.Length);
            }
            using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
            using (StreamReader sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
            {
                return sr.ReadToEnd();
            }
        }

        public static void ApiDownload(string path, string localFile)
        {
            using (WebClient client = new WebClient())
            {
                client.Encoding = Encoding.UTF8;
                if (!string.IsNullOrEmpty(Token))
                    client.Headers.Add(HttpRequestHeader.Authorization, "Bearer " + Token);
                client.DownloadFile(ServerUrl.TrimEnd('/') + path, localFile);
            }
        }

        private static void ForwardProtocol(string url)
        {
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + PORT + "/api/open");
                req.Method = "POST";
                req.ContentType = "application/json";
                byte[] data = Encoding.UTF8.GetBytes(string.Format("{{\"url\":\"{0}\"}}", url));
                req.ContentLength = data.Length;
                using (Stream s = req.GetRequestStream()) s.Write(data, 0, data.Length);
                req.GetResponse().Close();
            }
            catch { }
        }

        public static void HandleProtocolUrl(string url)
        {
            try
            {
                Uri uri = new Uri(url);
                string query = uri.Query;
                string fileUrl = "";
                string fileName = "presentation.pptx";
                string token = "";
                foreach (string part in query.TrimStart('?').Split('&'))
                {
                    string[] kv = part.Split('=');
                    if (kv.Length == 2)
                    {
                        string k = kv[0].ToLower();
                        string v = Uri.UnescapeDataString(kv[1]);
                        if (k == "url") fileUrl = v;
                        else if (k == "name") fileName = v;
                        else if (k == "token") token = v;
                    }
                }
                if (!string.IsNullOrEmpty(fileUrl))
                {
                    ThreadPool.QueueUserWorkItem(o => LaunchOfficePresentation(fileUrl, fileName, token));
                }
            }
            catch { }
        }

        public static void LaunchOfficePresentation(string fileUrl, string fileName, string token)
        {
            try
            {
                string safeName = fileName;
                foreach (char c in Path.GetInvalidFileNameChars()) safeName = safeName.Replace(c, '_');
                string localPath = Path.Combine(CacheDir, safeName);
                using (WebClient client = new WebClient())
                {
                    if (!string.IsNullOrEmpty(token))
                        client.Headers.Add("Authorization", "Bearer " + token);
                    else if (!string.IsNullOrEmpty(Token))
                        client.Headers.Add("Authorization", "Bearer " + Token);
                    client.DownloadFile(fileUrl, localPath);
                }

                string pptApp = FindPresentationApp();
                ProcessStartInfo psi = new ProcessStartInfo();
                if (!string.IsNullOrEmpty(pptApp) && File.Exists(pptApp))
                {
                    psi.FileName = pptApp;
                    psi.Arguments = "/s \"" + localPath + "\"";
                }
                else
                {
                    psi.FileName = localPath;
                    psi.UseShellExecute = true;
                }
                Process proc = Process.Start(psi);

                Thread.Sleep(1200);
                try
                {
                    if (FloatingDock != null)
                        FloatingDock.BeginInvoke(new Action(() => FloatingDock.ShowDock()));
                }
                catch { }
                try
                {
                    if (OverlayCanvas != null)
                        OverlayCanvas.BeginInvoke(new Action(() => OverlayCanvas.ShowOverlay()));
                }
                catch { }
            }
            catch (Exception ex)
            {
                MessageBox.Show("调起本地放映失败: " + ex.Message, "FLA 提示", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        public static string FindPresentationApp()
        {
            string[] candidates = new string[]
            {
                @"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
                @"C:\Program Files (x86)\Microsoft Office\root\Office16\POWERPNT.EXE",
                @"C:\Program Files\Microsoft Office\Office15\POWERPNT.EXE",
                @"C:\Program Files (x86)\Microsoft Office\Office15\POWERPNT.EXE",
                @"C:\Program Files\Microsoft Office\Office14\POWERPNT.EXE",
                @"C:\Program Files (x86)\Microsoft Office\Office14\POWERPNT.EXE",
                @"C:\Users\" + Environment.UserName + @"\AppData\Local\Kingsoft\WPS Office\ksolaunch.exe",
                @"C:\Program Files (x86)\Kingsoft\WPS Office\ksolaunch.exe"
            };
            foreach (string p in candidates)
            {
                if (File.Exists(p)) return p;
            }
            return "";
        }

        public static void StartSeewoInterceptor()
        {
            seewoThread = new Thread(() =>
            {
                while (isRunning)
                {
                    try
                    {
                        if (SeewoEnabled)
                        {
                            Process[] procs = Process.GetProcessesByName("EasiNote");
                            foreach (Process p in procs)
                            {
                                IntPtr h = p.MainWindowHandle;
                                if (h != IntPtr.Zero)
                                {
                                    ShowWindow(h, 0); // SW_HIDE
                                }
                            }
                        }
                    }
                    catch { }
                    Thread.Sleep(3000);
                }
            })
            { IsBackground = true };
            seewoThread.Start();
        }

        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    }
    #endregion

    #region 云端课件实体类
    public class CloudFileItem
    {
        public int id { get; set; }
        public string name { get; set; }
        public string kind { get; set; }
        public long size { get; set; }
        public int pages { get; set; }
        public string created_at { get; set; }
        public string ext { get; set; }
    }
    #endregion

    #region 主控制台窗口 (MainForm — 单色极简, 与 Web 端一致)
    public class MainForm : Form
    {
        private Panel navPanel;
        private Panel contentPanel;
        private List<FlatButton> navButtons = new List<FlatButton>();

        public MainForm()
        {
            this.Text = "FLA 智慧课堂桌面助手 · v" + Program.VERSION;
            this.Size = new Size(1024, 700);
            this.MinimumSize = new Size(880, 580);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.White;
            this.ForeColor = Theme.Ink;
            this.Font = Theme.F(9.5f);
            this.DoubleBuffered = true;
            this.Icon = null;

            InitLayout();
            SwitchTab("library");
        }

        private void InitLayout()
        {
            // ── 顶栏 ──
            Panel header = new Panel
            {
                Dock = DockStyle.Top,
                Height = 64,
                BackColor = Theme.Ink,
                Padding = new Padding(24, 0, 24, 0)
            };

            Label title = new Label
            {
                Text = "FLA",
                Font = Theme.F(17f, true),
                ForeColor = Color.White,
                AutoSize = true,
                Location = new Point(24, 14),
                BackColor = Color.Transparent
            };
            Label subTitle = new Label
            {
                Text = "智慧课堂桌面助手 · v" + Program.VERSION,
                Font = Theme.F(9f),
                ForeColor = Theme.Mut2,
                AutoSize = true,
                Location = new Point(92, 25),
                BackColor = Color.Transparent
            };

            FlatButton btnHide = new FlatButton
            {
                Text = "收起窗口",
                Size = new Size(96, 36),
                BackColor = Theme.Ink2,
                HoverColor = Theme.Ink3,
                ForeColor = Color.White,
                Radius = 18,
                Font = Theme.F(9f)
            };
            btnHide.Click += (s, e) => { this.Hide(); };

            FlatButton btnLaunchDock = new FlatButton
            {
                Text = "唤起悬浮盒",
                Size = new Size(120, 36),
                BackColor = Color.White,
                HoverColor = Color.FromArgb(228, 228, 231),
                PressedColor = Color.FromArgb(212, 212, 216),
                ForeColor = Theme.Ink,
                Radius = 18,
                Font = Theme.F(9f, true)
            };
            btnLaunchDock.Click += (s, e) => ShowStageTools();

            header.Controls.Add(title);
            header.Controls.Add(subTitle);
            header.Controls.Add(btnHide);
            header.Controls.Add(btnLaunchDock);
            this.Controls.Add(header);

            // 顶栏按钮右侧定位 (Dock 生效前宽度不可信, 由 Resize 驱动)
            EventHandler headLayout = (s, e) =>
            {
                int w = header.ClientSize.Width;
                btnHide.Location = new Point(w - 120, 14);
                btnLaunchDock.Location = new Point(w - 252, 14);
            };
            header.Resize += headLayout;
            headLayout(null, EventArgs.Empty);

            // ── 侧边导航 ──
            navPanel = new Panel
            {
                Dock = DockStyle.Left,
                Width = 208,
                BackColor = Theme.Subtle,
                Padding = new Padding(14, 18, 14, 14)
            };
            AddNavButton("课件工作台", "library", 0);
            AddNavButton("手机投屏遥控", "remote", 1);
            AddNavButton("悬浮盒设置", "dock_cfg", 2);
            AddNavButton("账号与设置", "settings", 3);
            this.Controls.Add(navPanel);

            // ── 内容区 ──
            contentPanel = new Panel
            {
                Dock = DockStyle.Fill,
                BackColor = Color.White,
                Padding = new Padding(26)
            };
            this.Controls.Add(contentPanel);
            contentPanel.BringToFront();
        }

        private void ShowStageTools()
        {
            if (Program.FloatingDock != null) Program.FloatingDock.ShowDock();
            if (Program.OverlayCanvas != null) Program.OverlayCanvas.ShowOverlay();
            this.WindowState = FormWindowState.Minimized;
        }

        private void AddNavButton(string text, string tabKey, int index)
        {
            FlatButton btn = new FlatButton
            {
                Text = text,
                Tag = tabKey,
                Size = new Size(180, 42),
                Location = new Point(14, 18 + index * 50),
                Radius = 10,
                TextAlign = ContentAlignment.MiddleLeft,
                BackColor = Color.Transparent,
                HoverColor = Theme.Hover,
                PressedColor = Theme.Hover,
                ForeColor = Theme.Mut,
                Font = Theme.F(9.5f, true),
                BorderColor = Color.Transparent
            };
            btn.Click += (s, e) => SwitchTab(tabKey);
            navButtons.Add(btn);
            navPanel.Controls.Add(btn);
        }

        public void SwitchTab(string tabKey)
        {
            foreach (FlatButton b in navButtons)
            {
                bool active = (string)b.Tag == tabKey;
                b.BackColor = active ? Theme.Ink : Color.Transparent;
                b.ForeColor = active ? Color.White : Theme.Mut;
                b.HoverColor = active ? Theme.Ink3 : Theme.Hover;
                b.Invalidate();
            }

            contentPanel.Controls.Clear();
            if (tabKey == "library") InitLibraryTab();
            else if (tabKey == "remote") InitCastingTab();
            else if (tabKey == "dock_cfg") InitDockCfgTab();
            else if (tabKey == "settings") InitSettingsTab();
        }

        private static Label Head(string text)
        {
            return new Label
            {
                Text = text,
                Font = Theme.F(14f, true),
                ForeColor = Theme.Ink,
                AutoSize = true,
                Location = new Point(0, 0),
                BackColor = Color.Transparent
            };
        }

        private static Label Tip(string text)
        {
            return new Label
            {
                Text = text,
                Font = Theme.F(9f),
                ForeColor = Theme.Mut,
                AutoSize = false,
                Size = new Size(560, 22),
                Location = new Point(2, 30),
                BackColor = Color.Transparent
            };
        }

        private Action refreshLibrary = null;

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            // 句柄就绪后真正加载一次课件列表 (构造期 Invoke 不可用)
            if (refreshLibrary != null) { try { refreshLibrary(); } catch { } }
        }

        /* ==================== 1. 课件工作台 ==================== */
        private void InitLibraryTab()
        {
            contentPanel.Controls.Add(Head("我的课件库 · 一键本地原生放映"));
            contentPanel.Controls.Add(Tip("双击课件调用本机 PowerPoint / WPS 全屏放映，自动挂接悬浮工具盒与随页板书。"));

            FlatButton btnRefresh = new FlatButton
            {
                Text = "刷新",
                Size = new Size(84, 32),
                BackColor = Theme.Subtle,
                HoverColor = Theme.Hover,
                ForeColor = Theme.Ink,
                BorderColor = Theme.Line,
                Font = Theme.F(9f)
            };
            contentPanel.Controls.Add(btnRefresh);

            FlatButton btnWeb = new FlatButton
            {
                Text = "在浏览器打开",
                Size = new Size(110, 32),
                BackColor = Theme.Ink,
                HoverColor = Theme.Ink3,
                ForeColor = Color.White,
                Font = Theme.F(9f)
            };
            btnWeb.Click += (s, e) => { try { Process.Start(Program.ServerUrl); } catch { } };
            contentPanel.Controls.Add(btnWeb);

            Label emptyTip = new Label
            {
                Text = "",
                Font = Theme.F(10f),
                ForeColor = Theme.Mut,
                TextAlign = ContentAlignment.MiddleCenter,
                BackColor = Color.Transparent
            };
            contentPanel.Controls.Add(emptyTip);

            ListView lv = new ListView
            {
                Location = new Point(0, 62),
                View = View.Details,
                FullRowSelect = true,
                GridLines = false,
                BorderStyle = BorderStyle.None,
                HideSelection = true,
                HeaderStyle = ColumnHeaderStyle.Nonclickable,
                BackColor = Color.White,
                ForeColor = Theme.Ink,
                Font = Theme.F(9.5f)
            };
            lv.Columns.Add("课件名称", 360);
            lv.Columns.Add("类型", 80);
            lv.Columns.Add("页数", 80);
            lv.Columns.Add("体积", 100);
            lv.Columns.Add("上传日期", 130);
            contentPanel.Controls.Add(lv);

            // 布局 (Dock 生效前 ClientSize 不可信, 由 Resize 驱动)
            EventHandler doLayout = (s, e) =>
            {
                int w = contentPanel.ClientSize.Width;
                int h = contentPanel.ClientSize.Height;
                btnRefresh.Location = new Point(w - 84, 6);
                btnWeb.Location = new Point(w - 204, 6);
                lv.Location = new Point(0, 62);
                lv.Size = new Size(w, h - 70);
                emptyTip.Size = new Size(w, 60);
                emptyTip.Location = new Point(0, Math.Max(120, h / 2 - 60));
            };
            contentPanel.Resize += doLayout;
            doLayout(null, EventArgs.Empty);

            Action loadFiles = () =>
            {
                List<ListViewItem> rows = new List<ListViewItem>();
                string err = "";
                try
                {
                    string json = Program.ApiGet("/api/files");
                    string[] items = json.Split(new string[] { "},{" }, StringSplitOptions.None);
                    foreach (string item in items)
                    {
                        string name = ExtractItem(item, "name");
                        if (name == "") continue;
                        string kind = ExtractItem(item, "kind");
                        string pages = ExtractItem(item, "pages");
                        string size = ExtractItem(item, "size");
                        string created = ExtractItem(item, "created_at");
                        if (created.Length > 10) created = created.Substring(0, 10);

                        ListViewItem lvi = new ListViewItem(name);
                        lvi.SubItems.Add(kind != "" ? kind.ToUpper() : "—");
                        lvi.SubItems.Add(pages != "" && pages != "0" ? pages + " 页" : "—");
                        lvi.SubItems.Add(size != "" ? FormatSize(long.Parse(size)) : "—");
                        lvi.SubItems.Add(created);
                        lvi.Tag = ExtractItem(item, "id");
                        rows.Add(lvi);
                    }
                }
                catch (Exception ex)
                {
                    WebException we = ex as WebException;
                    if (we != null && we.Response != null)
                    {
                        HttpWebResponse hr = we.Response as HttpWebResponse;
                        if (hr != null && (int)hr.StatusCode == 401) err = "登录状态已失效，请到「账号与设置」重新登录";
                        else err = "无法连接服务器，请检查「账号与设置」中的服务地址";
                    }
                    else err = "无法连接服务器，请检查「账号与设置」中的服务地址";
                }

                try
                {
                    this.BeginInvoke(new Action(() =>
                    {
                        lv.Items.Clear();
                        foreach (ListViewItem r in rows) lv.Items.Add(r);
                        if (rows.Count == 0)
                        {
                            emptyTip.Text = string.IsNullOrEmpty(Program.Token)
                                ? "尚未登录 — 请到「账号与设置」登录 FLA 账号后同步云端课件库"
                                : (err != "" ? err : "云端还没有课件 — 到网页端上传后点「刷新」");
                            emptyTip.ForeColor = err != "" && !err.Contains("还没有") ? Theme.Danger : Theme.Mut;
                            emptyTip.BringToFront();
                        }
                        else
                        {
                            emptyTip.Text = "";
                            lv.BringToFront();
                        }
                    }));
                }
                catch { }
            };

            btnRefresh.Click += (s, e) => loadFiles();
            refreshLibrary = loadFiles;
            lv.DoubleClick += (s, e) =>
            {
                if (lv.SelectedItems.Count > 0)
                {
                    string fid = lv.SelectedItems[0].Tag as string;
                    string fname = lv.SelectedItems[0].Text;
                    if (!string.IsNullOrEmpty(fid))
                    {
                        string dlUrl = "/api/files/" + fid + "/download";
                        ThreadPool.QueueUserWorkItem(o => Program.LaunchOfficePresentation(Program.ServerUrl.TrimEnd('/') + dlUrl, fname, Program.Token));
                    }
                }
            };

            ThreadPool.QueueUserWorkItem(o => loadFiles());
        }

        /* ==================== 2. 手机投屏遥控 ==================== */
        private void InitCastingTab()
        {
            contentPanel.Controls.Add(Head("手机投屏与扫码遥控"));
            contentPanel.Controls.Add(Tip("手机变成激光笔 + 翻页器 + 掌上触控板，与大屏放映毫秒级同步。"));

            Card c1 = new Card { Location = new Point(0, 66), Size = new Size(470, 160) };
            Label t1 = new Label { Text = "连接信息", Font = Theme.F(10.5f, true), ForeColor = Theme.Ink, AutoSize = true, Location = new Point(20, 16), BackColor = Color.Transparent };
            StatusDot dot = new StatusDot { Location = new Point(96, 20) };
            Label dotTxt = new Label { Text = "检测中…", Font = Theme.F(8.5f), ForeColor = Theme.Mut, AutoSize = true, Location = new Point(112, 19), BackColor = Color.Transparent };
            Label lServer = new Label { Text = "服务地址  " + Program.ServerUrl, Font = Theme.F(9f), ForeColor = Theme.Ink, AutoSize = true, Location = new Point(20, 48), BackColor = Color.Transparent };
            Label lBridge = new Label
            {
                Text = "本机桥接  127.0.0.1:8307 (" + (Program.BridgeRunning ? "运行中" : "未启动") + ")",
                Font = Theme.F(9f),
                ForeColor = Theme.Mut,
                AutoSize = true,
                Location = new Point(20, 72),
                BackColor = Color.Transparent
            };
            FlatButton btnCopy = new FlatButton
            {
                Text = "复制服务地址",
                Size = new Size(110, 30),
                Location = new Point(20, 106),
                BackColor = Theme.Subtle,
                HoverColor = Theme.Hover,
                ForeColor = Theme.Ink,
                BorderColor = Theme.Line,
                Font = Theme.F(8.5f)
            };
            btnCopy.Click += (s, e) =>
            {
                try { Clipboard.SetText(Program.ServerUrl); MessageBox.Show("已复制到剪贴板", "FLA", MessageBoxButtons.OK, MessageBoxIcon.Information); } catch { }
            };
            FlatButton btnOpen = new FlatButton
            {
                Text = "打开网页",
                Size = new Size(96, 30),
                Location = new Point(138, 106),
                BackColor = Theme.Ink,
                HoverColor = Theme.Ink3,
                ForeColor = Color.White,
                Font = Theme.F(8.5f)
            };
            btnOpen.Click += (s, e) => { try { Process.Start(Program.ServerUrl); } catch { } };
            c1.Controls.Add(t1);
            c1.Controls.Add(dot);
            c1.Controls.Add(dotTxt);
            c1.Controls.Add(lServer);
            c1.Controls.Add(lBridge);
            c1.Controls.Add(btnCopy);
            c1.Controls.Add(btnOpen);
            contentPanel.Controls.Add(c1);

            Card c2 = new Card { Location = new Point(0, 238), Size = new Size(470, 186) };
            Label t2 = new Label { Text = "使用方法", Font = Theme.F(10.5f, true), ForeColor = Theme.Ink, AutoSize = true, Location = new Point(20, 16), BackColor = Color.Transparent };
            Label steps = new Label
            {
                Text = "① 电脑端打开课件放映（悬浮盒自动出现）\n② 放映时点击悬浮盒上的「手机」图标生成配对码\n③ 手机浏览器打开服务地址，进入「手机遥控」\n④ 扫码或输入 4 位配对码，即连即用",
                Font = Theme.F(9f),
                ForeColor = Theme.Mut,
                Location = new Point(20, 44),
                Size = new Size(430, 130),
                BackColor = Color.Transparent
            };
            c2.Controls.Add(t2);
            c2.Controls.Add(steps);
            contentPanel.Controls.Add(c2);

            // 后台健康检查
            ThreadPool.QueueUserWorkItem(o =>
            {
                bool ok = false;
                try
                {
                    string r = Program.ApiGet("/api/health");
                    ok = r != null && r.IndexOf("\"ok\"", StringComparison.Ordinal) >= 0;
                }
                catch { }
                try
                {
                    this.BeginInvoke(new Action(() =>
                    {
                        dot.Set(ok);
                        dotTxt.Text = ok ? "服务器在线" : "无法连接服务器";
                        dotTxt.ForeColor = ok ? Theme.Ink : Theme.Danger;
                    }));
                }
                catch { }
            });
        }

        /* ==================== 3. 悬浮盒设置 ==================== */
        private void InitDockCfgTab()
        {
            contentPanel.Controls.Add(Head("悬浮工具盒"));
            contentPanel.Controls.Add(Tip("放映时的画笔、激光、翻页与课堂工具都收纳在屏幕底部的悬浮盒中。"));

            Card c = new Card { Location = new Point(0, 66), Size = new Size(560, 210) };
            FlatButton btnShow = new FlatButton
            {
                Text = "展示悬浮盒与板书画布",
                Size = new Size(190, 38),
                Location = new Point(20, 20),
                BackColor = Theme.Ink,
                HoverColor = Theme.Ink3,
                ForeColor = Color.White,
                Font = Theme.F(9f, true)
            };
            btnShow.Click += (s, e) => ShowStageTools();
            c.Controls.Add(btnShow);

            CheckBox cbAutoDock = new CheckBox
            {
                Text = "拖到屏幕底部时自动收起为迷你标签",
                Checked = FloatingDockForm.AutoCollapse,
                Location = new Point(22, 76),
                AutoSize = true,
                Font = Theme.F(9f),
                ForeColor = Theme.Ink
            };
            cbAutoDock.CheckedChanged += (s, e) => { FloatingDockForm.AutoCollapse = cbAutoDock.Checked; };
            c.Controls.Add(cbAutoDock);

            CheckBox cbSeewo = new CheckBox
            {
                Text = "放映时自动压制希沃白板5侧边栏 (EasiNote)",
                Checked = Program.SeewoEnabled,
                Location = new Point(22, 106),
                AutoSize = true,
                Font = Theme.F(9f),
                ForeColor = Theme.Ink
            };
            cbSeewo.CheckedChanged += (s, e) => { Program.SeewoEnabled = cbSeewo.Checked; };
            c.Controls.Add(cbSeewo);

            Label hint = new Label
            {
                Text = "小技巧：悬浮盒可按住左侧圆点拖动；Esc 可退出画笔模式回到鼠标。",
                Font = Theme.F(8.5f),
                ForeColor = Theme.Mut2,
                Location = new Point(22, 150),
                AutoSize = true,
                BackColor = Color.Transparent
            };
            c.Controls.Add(hint);
            contentPanel.Controls.Add(c);
        }

        /* ==================== 4. 账号与设置 ==================== */
        private void InitSettingsTab()
        {
            contentPanel.Controls.Add(Head("账号与服务设置"));
            contentPanel.Controls.Add(Tip("登录 FLA 账号后，桌面端即可同步云端课件库并使用手机遥控。"));

            Card c = new Card { Location = new Point(0, 66), Size = new Size(560, 300) };

            Label lblUrl = new Label { Text = "FLA 服务地址", Font = Theme.F(9f, true), ForeColor = Theme.Mut, AutoSize = true, Location = new Point(20, 18), BackColor = Color.Transparent };
            TextBox txtUrl = new TextBox { Text = Program.ServerUrl, Location = new Point(20, 40), Size = new Size(430, 28), Font = Theme.F(9.5f), BorderStyle = BorderStyle.FixedSingle };
            c.Controls.Add(lblUrl);
            c.Controls.Add(txtUrl);

            Label lblUser = new Label { Text = "账号", Font = Theme.F(9f, true), ForeColor = Theme.Mut, AutoSize = true, Location = new Point(20, 82), BackColor = Color.Transparent };
            TextBox txtUser = new TextBox { Text = Program.UserName, Location = new Point(20, 104), Size = new Size(200, 28), Font = Theme.F(9.5f), BorderStyle = BorderStyle.FixedSingle };
            Label lblPass = new Label { Text = "密码", Font = Theme.F(9f, true), ForeColor = Theme.Mut, AutoSize = true, Location = new Point(240, 82), BackColor = Color.Transparent };
            TextBox txtPass = new TextBox { Location = new Point(240, 104), Size = new Size(210, 28), Font = Theme.F(9.5f), BorderStyle = BorderStyle.FixedSingle, UseSystemPasswordChar = true };
            c.Controls.Add(lblUser);
            c.Controls.Add(txtUser);
            c.Controls.Add(lblPass);
            c.Controls.Add(txtPass);

            Label lblStatus = new Label
            {
                Text = string.IsNullOrEmpty(Program.Token) ? "状态：未登录" : "状态：已登录 " + (Program.UserName == "" ? "" : "（" + Program.UserName + "）"),
                Font = Theme.F(9f),
                ForeColor = string.IsNullOrEmpty(Program.Token) ? Theme.Mut : Theme.Ink,
                AutoSize = true,
                Location = new Point(20, 146),
                BackColor = Color.Transparent
            };
            c.Controls.Add(lblStatus);

            FlatButton btnLogin = new FlatButton
            {
                Text = "登录并保存",
                Size = new Size(130, 36),
                Location = new Point(20, 180),
                BackColor = Theme.Ink,
                HoverColor = Theme.Ink3,
                ForeColor = Color.White,
                Font = Theme.F(9f, true)
            };
            FlatButton btnLogout = new FlatButton
            {
                Text = "退出登录",
                Size = new Size(96, 36),
                Location = new Point(160, 180),
                BackColor = Theme.Subtle,
                HoverColor = Theme.Hover,
                ForeColor = Theme.Danger,
                BorderColor = Theme.Line,
                Font = Theme.F(9f)
            };
            FlatButton btnUpdate = new FlatButton
            {
                Text = "检查更新",
                Size = new Size(96, 36),
                Location = new Point(266, 180),
                BackColor = Theme.Subtle,
                HoverColor = Theme.Hover,
                ForeColor = Theme.Ink,
                BorderColor = Theme.Line,
                Font = Theme.F(9f)
            };
            Label lblVer = new Label
            {
                Text = "客户端版本 v" + Program.VERSION,
                Font = Theme.F(8.5f),
                ForeColor = Theme.Mut2,
                AutoSize = true,
                Location = new Point(20, 232),
                BackColor = Color.Transparent
            };
            Label lblUpd = new Label
            {
                Text = "",
                Font = Theme.F(8.5f),
                ForeColor = Theme.Mut,
                AutoSize = true,
                Location = new Point(150, 232),
                BackColor = Color.Transparent
            };
            c.Controls.Add(btnLogin);
            c.Controls.Add(btnLogout);
            c.Controls.Add(btnUpdate);
            c.Controls.Add(lblVer);
            c.Controls.Add(lblUpd);

            btnLogin.Click += (s, e) =>
            {
                string url = txtUrl.Text.Trim();
                string user = txtUser.Text.Trim();
                string pass = txtPass.Text;
                if (url == "" || user == "" || pass == "")
                {
                    MessageBox.Show("请填写服务地址、账号与密码", "FLA", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
                Program.ServerUrl = url.EndsWith("/") ? url.TrimEnd('/') : url;
                btnLogin.Enabled = false;
                btnLogin.Text = "登录中…";
                ThreadPool.QueueUserWorkItem(o =>
                {
                    string resultMsg;
                    bool ok = false;
                    try
                    {
                        string body = "{\"username\":\"" + user.Replace("\"", "") + "\",\"password\":\"" + pass.Replace("\"", "").Replace("\\", "\\\\") + "\"}";
                        string resp = Program.ApiPost("/api/auth/login", body);
                        string token = Program.ExtractJsonVal(resp, "token");
                        if (!string.IsNullOrEmpty(token))
                        {
                            Program.Token = token;
                            Program.UserName = Program.ExtractJsonVal(resp, "nickname");
                            if (Program.UserName == "") Program.UserName = user;
                            Program.SaveConfig();
                            ok = true;
                            resultMsg = "已登录 " + Program.UserName;
                        }
                        else resultMsg = "登录失败：服务器返回异常";
                    }
                    catch (Exception ex)
                    {
                        WebException we = ex as WebException;
                        if (we != null && we.Response != null)
                        {
                            try
                            {
                                using (StreamReader sr = new StreamReader(we.Response.GetResponseStream(), Encoding.UTF8))
                                    resultMsg = "登录失败：" + Program.ExtractJsonVal(sr.ReadToEnd(), "detail");
                                if (resultMsg.EndsWith("：")) resultMsg = "登录失败：账号或密码错误";
                            }
                            catch { resultMsg = "登录失败：账号或密码错误"; }
                        }
                        else resultMsg = "无法连接服务器，请检查服务地址";
                    }
                    try
                    {
                        this.BeginInvoke(new Action(() =>
                        {
                            btnLogin.Enabled = true;
                            btnLogin.Text = "登录并保存";
                            lblStatus.Text = "状态：" + resultMsg;
                            lblStatus.ForeColor = ok ? Theme.Ink : Theme.Danger;
                        }));
                    }
                    catch { }
                });
            };

            btnLogout.Click += (s, e) =>
            {
                Program.Logout();
                lblStatus.Text = "状态：未登录";
                lblStatus.ForeColor = Theme.Mut;
                txtPass.Text = "";
            };

            btnUpdate.Click += (s, e) =>
            {
                btnUpdate.Enabled = false;
                ThreadPool.QueueUserWorkItem(o =>
                {
                    string msg = "";
                    try
                    {
                        string resp = Program.ApiGet("/api/desktop/version");
                        string ver = Program.ExtractJsonVal(resp, "version");
                        if (ver == Program.VERSION) msg = "已是最新版本 ✓";
                        else if (ver != "") msg = "有新版本 v" + ver + " — 请到官网重新下载 FLA.exe";
                        else msg = "";
                    }
                    catch { msg = "检查更新失败（无法连接服务器）"; }
                    try
                    {
                        this.BeginInvoke(new Action(() =>
                        {
                            btnUpdate.Enabled = true;
                            lblUpd.Text = msg;
                        }));
                    }
                    catch { }
                });
            };

            contentPanel.Controls.Add(c);
        }

        private static string ExtractItem(string json, string key)
        {
            try
            {
                string search = "\"" + key + "\":";
                int idx = json.IndexOf(search);
                if (idx < 0) return "";
                int start = idx + search.Length;
                if (start >= json.Length) return "";
                if (json[start] == '"')
                {
                    start++;
                    int end = json.IndexOf('"', start);
                    if (end < 0) return "";
                    return json.Substring(start, end - start);
                }
                else
                {
                    int end = json.IndexOfAny(new char[] { ',', '}', ']' }, start);
                    if (end < 0) end = json.Length;
                    return json.Substring(start, end - start).Trim();
                }
            }
            catch { return ""; }
        }

        private static string FormatSize(long b)
        {
            if (b < 1024) return b + " B";
            if (b < 1024 * 1024) return (b / 1024.0).ToString("0.#") + " KB";
            return (b / (1024.0 * 1024.0)).ToString("0.#") + " MB";
        }
    }
    #endregion

    #region 悬浮盒矢量图标
    public enum IconKind { Cursor, Laser, Pen, Highlighter, Eraser, Trash, ChevL, ChevR, Board, Phone, Clock, Grid, Close }

    public static class DockIcons
    {
        public static void Draw(Graphics g, IconKind kind, Rectangle r, Color c)
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            // 逻辑坐标 0..95 映射到目标矩形
            float s = r.Width / 95f;
            Point[] pts;
            using (Pen pen = new Pen(c, 7f * s))
            {
                pen.StartCap = LineCap.Round;
                pen.EndCap = LineCap.Round;
                pen.LineJoin = LineJoin.Round;
                switch (kind)
                {
                    case IconKind.Cursor:
                        pts = new Point[] {
                            Mp(r, 28, 14), Mp(r, 28, 72), Mp(r, 44, 58), Mp(r, 55, 82), Mp(r, 64, 78), Mp(r, 54, 55), Mp(r, 72, 54)
                        };
                        using (SolidBrush b = new SolidBrush(c)) g.FillPolygon(b, pts);
                        break;
                    case IconKind.Laser:
                        using (SolidBrush b2 = new SolidBrush(c))
                            g.FillEllipse(b2, r.X + 40 * s, r.Y + 40 * s, 15 * s, 15 * s);
                        g.DrawLine(pen, Mp(r, 47, 12), Mp(r, 47, 26));
                        g.DrawLine(pen, Mp(r, 47, 69), Mp(r, 47, 83));
                        g.DrawLine(pen, Mp(r, 12, 47), Mp(r, 26, 47));
                        g.DrawLine(pen, Mp(r, 69, 47), Mp(r, 83, 47));
                        g.DrawLine(pen, Mp(r, 22, 22), Mp(r, 32, 32));
                        g.DrawLine(pen, Mp(r, 62, 62), Mp(r, 72, 72));
                        g.DrawLine(pen, Mp(r, 72, 22), Mp(r, 62, 32));
                        g.DrawLine(pen, Mp(r, 32, 62), Mp(r, 22, 72));
                        break;
                    case IconKind.Pen:
                        g.DrawLine(pen, Mp(r, 30, 65), Mp(r, 68, 27));
                        g.DrawLine(pen, Mp(r, 24, 71), Mp(r, 30, 65));
                        g.DrawLine(pen, Mp(r, 27, 74), Mp(r, 21, 68));
                        g.DrawLine(pen, Mp(r, 60, 23), Mp(r, 72, 35));
                        break;
                    case IconKind.Highlighter:
                        using (Pen thick = new Pen(c, 18f * s))
                        {
                            thick.StartCap = LineCap.Flat;
                            thick.EndCap = LineCap.Flat;
                            g.DrawLine(thick, Mp(r, 38, 52), Mp(r, 68, 22));
                        }
                        g.DrawLine(pen, Mp(r, 20, 72), Mp(r, 52, 72));
                        break;
                    case IconKind.Eraser:
                        pts = new Point[] { Mp(r, 24, 58), Mp(r, 48, 24), Mp(r, 74, 40), Mp(r, 50, 74) };
                        g.DrawPolygon(pen, pts);
                        g.DrawLine(pen, Mp(r, 18, 80), Mp(r, 76, 80));
                        break;
                    case IconKind.Trash:
                        g.DrawLine(pen, Mp(r, 22, 28), Mp(r, 72, 28));
                        g.DrawLine(pen, Mp(r, 40, 28), Mp(r, 40, 20));
                        g.DrawLine(pen, Mp(r, 40, 20), Mp(r, 54, 20));
                        g.DrawLine(pen, Mp(r, 54, 20), Mp(r, 54, 28));
                        g.DrawLine(pen, Mp(r, 30, 36), Mp(r, 33, 76));
                        g.DrawLine(pen, Mp(r, 64, 36), Mp(r, 61, 76));
                        g.DrawLine(pen, Mp(r, 33, 76), Mp(r, 61, 76));
                        g.DrawLine(pen, Mp(r, 47, 40), Mp(r, 47, 70));
                        break;
                    case IconKind.ChevL:
                        g.DrawLine(pen, Mp(r, 58, 24), Mp(r, 36, 47));
                        g.DrawLine(pen, Mp(r, 36, 47), Mp(r, 58, 70));
                        break;
                    case IconKind.ChevR:
                        g.DrawLine(pen, Mp(r, 36, 24), Mp(r, 58, 47));
                        g.DrawLine(pen, Mp(r, 58, 47), Mp(r, 36, 70));
                        break;
                    case IconKind.Board:
                        g.DrawRectangle(pen, r.X + 18 * s, r.Y + 22 * s, 58 * s, 40 * s);
                        g.DrawLine(pen, Mp(r, 34, 62), Mp(r, 34, 76));
                        g.DrawLine(pen, Mp(r, 60, 62), Mp(r, 60, 76));
                        break;
                    case IconKind.Phone:
                        g.DrawRectangle(pen, r.X + 32 * s, r.Y + 14 * s, 30 * s, 66 * s);
                        g.DrawLine(pen, Mp(r, 42, 70), Mp(r, 52, 70));
                        break;
                    case IconKind.Clock:
                        g.DrawEllipse(pen, r.X + 18 * s, r.Y + 18 * s, 58 * s, 58 * s);
                        g.DrawLine(pen, Mp(r, 47, 28), Mp(r, 47, 48));
                        g.DrawLine(pen, Mp(r, 47, 48), Mp(r, 60, 56));
                        break;
                    case IconKind.Grid:
                        g.DrawRectangle(pen, r.X + 20 * s, r.Y + 20 * s, 22 * s, 22 * s);
                        g.DrawRectangle(pen, r.X + 52 * s, r.Y + 20 * s, 22 * s, 22 * s);
                        g.DrawRectangle(pen, r.X + 20 * s, r.Y + 52 * s, 22 * s, 22 * s);
                        g.DrawRectangle(pen, r.X + 52 * s, r.Y + 52 * s, 22 * s, 22 * s);
                        break;
                    case IconKind.Close:
                        g.DrawLine(pen, Mp(r, 30, 30), Mp(r, 64, 64));
                        g.DrawLine(pen, Mp(r, 64, 30), Mp(r, 30, 64));
                        break;
                }
            }
        }

        private static Point Mp(Rectangle r, int lx, int ly)
        {
            float s = r.Width / 95f;
            return new Point((int)Math.Round(r.X + lx * s), (int)Math.Round(r.Y + ly * s));
        }
    }
    #endregion

    #region 悬浮盒按钮
    public class DockButton : Control
    {
        public IconKind Kind;
        public bool Active = false;
        private bool hovered = false;

        public DockButton(IconKind kind, string tip)
        {
            Kind = kind;
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.UserPaint | ControlStyles.SupportsTransparentBackColor, true);
            Size = new Size(42, 42);
            BackColor = Color.Transparent;
            Cursor = Cursors.Hand;
            TabStop = false;
            ToolTip tipSrv = Program.DockTips;
            if (tipSrv != null) tipSrv.SetToolTip(this, tip);
        }

        protected override void OnMouseEnter(EventArgs e) { hovered = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hovered = false; Invalidate(); base.OnMouseLeave(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            Rectangle r = new Rectangle(0, 0, Width - 1, Height - 1);
            Color bg = Active ? Color.White : (hovered ? Theme.Ink3 : Theme.Ink2);
            using (GraphicsPath p = Theme.Rounded(r, 10))
            using (SolidBrush b = new SolidBrush(bg))
                g.FillPath(b, p);
            Color glyph = Active ? Theme.Ink : Color.White;
            DockIcons.Draw(g, Kind, new Rectangle(6, 6, Width - 12, Height - 12), glyph);
        }
    }
    #endregion

    #region 悬浮工具盒 (FloatingDockForm — 矢量图标黑胶囊)
    public class FloatingDockForm : Form
    {
        private bool isDragging = false;
        private Point dragCursorPoint;
        private Point dragFormPoint;
        private bool isCollapsed = false;
        private bool paletteOpen = false;
        private PaletteForm palette;
        public static bool AutoCollapse = true;

        public FloatingDockForm()
        {
            this.FormBorderStyle = FormBorderStyle.None;
            this.StartPosition = FormStartPosition.Manual;
            this.Size = new Size(656, 58);
            this.Location = new Point(Screen.PrimaryScreen.WorkingArea.Width / 2 - 328, Screen.PrimaryScreen.WorkingArea.Height - 92);
            this.TopMost = true;
            this.ShowInTaskbar = false;
            this.BackColor = Theme.Ink;
            this.ForeColor = Color.White;
            this.DoubleBuffered = true;

            InitDockUI();
            Theme.RoundForm(this, 29);

            this.MouseDown += OnMouseDownDrag;
            this.MouseMove += OnMouseMoveDrag;
            this.MouseUp += (s, e) => { isDragging = false; SnapToScreenEdge(); };
            if (Program.OverlayCanvas != null) Program.OverlayCanvas.ToolChanged = SyncActiveTool;
        }

        private void InitDockUI()
        {
            this.Controls.Clear();
            int x = 26;

            DockButton bCursor = AddDockItem(IconKind.Cursor, "鼠标 / 选择", x, delegate
            {
                if (Program.OverlayCanvas != null) Program.OverlayCanvas.SetTool(ScreenOverlayForm.ToolType.Cursor);
            });
            x += 48;

            DockButton bLaser = AddDockItem(IconKind.Laser, "激光笔", x, delegate
            {
                if (Program.OverlayCanvas != null) Program.OverlayCanvas.ToggleLaser();
            });
            x += 48;

            DockButton bPen = AddDockItem(IconKind.Pen, "画笔 (点击选颜色)", x, delegate
            {
                if (Program.OverlayCanvas != null)
                {
                    Program.OverlayCanvas.SetTool(ScreenOverlayForm.ToolType.Pen);
                    TogglePalette();
                }
            });
            x += 48;

            DockButton bHl = AddDockItem(IconKind.Highlighter, "荧光笔", x, delegate
            {
                if (Program.OverlayCanvas != null) Program.OverlayCanvas.SetTool(ScreenOverlayForm.ToolType.Highlighter);
            });
            x += 48;

            DockButton bEr = AddDockItem(IconKind.Eraser, "橡皮", x, delegate
            {
                if (Program.OverlayCanvas != null) Program.OverlayCanvas.SetTool(ScreenOverlayForm.ToolType.Eraser);
            });
            x += 48;

            DockButton bClr = AddDockItem(IconKind.Trash, "清空本页板书", x, delegate
            {
                if (Program.OverlayCanvas != null) Program.OverlayCanvas.ClearCurrentPage();
            });
            x += 52;

            x += 8; // 分隔

            DockButton bPrev = AddDockItem(IconKind.ChevL, "上一页", x, delegate
            {
                SendKeys.SendWait("{PGUP}");
                if (Program.OverlayCanvas != null) Program.OverlayCanvas.PrevSlide();
            });
            bPrev.Size = new Size(36, 36);
            bPrev.Location = new Point(x, (this.Height - 36) / 2);
            x += 42;

            DockButton bNext = AddDockItem(IconKind.ChevR, "下一页", x, delegate
            {
                SendKeys.SendWait("{PGDN}");
                if (Program.OverlayCanvas != null) Program.OverlayCanvas.NextSlide();
            });
            bNext.Size = new Size(36, 36);
            bNext.Location = new Point(x, (this.Height - 36) / 2);
            x += 48;

            DockButton bBoard = AddDockItem(IconKind.Board, "白板 / 恢复", x, delegate
            {
                if (Program.OverlayCanvas != null) Program.OverlayCanvas.ToggleWhiteboard();
            });
            x += 48;

            DockButton bPhone = AddDockItem(IconKind.Phone, "手机投屏遥控", x, delegate
            {
                MainForm mw = Program.MainWindow;
                if (mw != null)
                {
                    mw.Show();
                    mw.WindowState = FormWindowState.Normal;
                    mw.BringToFront();
                    mw.SwitchTab("remote");
                }
            });
            x += 48;

            DockButton bTimer = AddDockItem(IconKind.Clock, "课堂计时器 / 秒表", x, delegate
            {
                TimerForm.Launch(this);
            });
            x += 48;

            DockButton bTools = AddDockItem(IconKind.Grid, "更多工具", x, delegate
            {
                ShowToolsMenu(bTools);
            });
            x += 48;

            DockButton bClose = AddDockItem(IconKind.Close, "收起", x, delegate
            {
                CollapseDock();
            });
            bClose.Size = new Size(34, 34);
            bClose.Location = new Point(x, (this.Height - 34) / 2);
            x += 42;

            this.Width = x + 12;
            Theme.RoundForm(this, 29);
        }

        private DockButton AddDockItem(IconKind kind, string tip, int x, Action onClick)
        {
            DockButton btn = new DockButton(kind, tip)
            {
                Location = new Point(x, (this.Height - 42) / 2)
            };
            btn.Click += (s, e) => onClick();
            this.Controls.Add(btn);
            return btn;
        }

        public void SyncActiveTool()
        {
            try
            {
                ScreenOverlayForm.ToolType t = Program.OverlayCanvas != null
                    ? Program.OverlayCanvas.CurrentTool
                    : ScreenOverlayForm.ToolType.Cursor;
                foreach (Control c in this.Controls)
                {
                    DockButton db = c as DockButton;
                    if (db == null) continue;
                    bool active = false;
                    if (t == ScreenOverlayForm.ToolType.Cursor && db.Kind == IconKind.Cursor) active = true;
                    if (t == ScreenOverlayForm.ToolType.Laser && db.Kind == IconKind.Laser) active = true;
                    if (t == ScreenOverlayForm.ToolType.Pen && db.Kind == IconKind.Pen) active = true;
                    if (t == ScreenOverlayForm.ToolType.Highlighter && db.Kind == IconKind.Highlighter) active = true;
                    if (t == ScreenOverlayForm.ToolType.Eraser && db.Kind == IconKind.Eraser) active = true;
                    if (db.Active != active) { db.Active = active; db.Invalidate(); }
                }
            }
            catch { }
        }

        private void TogglePalette()
        {
            if (paletteOpen && palette != null)
            {
                palette.Hide();
                paletteOpen = false;
                return;
            }
            if (palette == null) palette = new PaletteForm(this);
            palette.Location = new Point(this.Left + 110, Math.Max(8, this.Top - palette.Height - 12));
            palette.Show(this);
            palette.BringToFront();
            paletteOpen = true;
        }

        public void ClosePalette()
        {
            paletteOpen = false;
            if (palette != null) palette.Hide();
        }

        private void ShowToolsMenu(Control anchor)
        {
            ContextMenuStrip cm = new ContextMenuStrip();
            cm.Renderer = new FlaMenuRenderer();
            cm.ShowImageMargin = false;
            cm.Font = Theme.F(9.5f);
            cm.Items.Add("课堂计时器 / 秒表", null, delegate { TimerForm.Launch(this); });
            cm.Items.Add("黑屏幕布 (B)", null, delegate { SendKeys.SendWait("b"); });
            cm.Items.Add("白屏幕布 (W)", null, delegate { SendKeys.SendWait("w"); });
            cm.Items.Add(new ToolStripSeparator());
            cm.Items.Add("返回主控制台", null, delegate
            {
                MainForm mw = Program.MainWindow;
                if (mw != null) { mw.Show(); mw.WindowState = FormWindowState.Normal; mw.BringToFront(); }
            });
            cm.Show(anchor, new Point(0, anchor.Height + 4));
        }

        private void CollapseDock()
        {
            isCollapsed = true;
            ClosePalette();
            this.Controls.Clear();
            this.Size = new Size(56, 56);
            Theme.RoundForm(this, 17);

            FlatButton btnExpand = new FlatButton
            {
                Text = "FLA",
                Dock = DockStyle.Fill,
                BackColor = Theme.Ink,
                HoverColor = Theme.Ink3,
                ForeColor = Color.White,
                Font = Theme.F(9f, true),
                Radius = 17
            };
            btnExpand.Click += (s, e) => ExpandDock();
            this.Controls.Add(btnExpand);
        }

        public void ExpandDock()
        {
            isCollapsed = false;
            this.Size = new Size(656, 58);
            InitDockUI();
            Theme.RoundForm(this, 29);
        }

        private void SnapToScreenEdge()
        {
            Screen sc = Screen.FromControl(this);
            int thresh = 40;
            if (this.Left < sc.WorkingArea.Left + thresh) this.Left = sc.WorkingArea.Left + 8;
            if (this.Right > sc.WorkingArea.Right - thresh) this.Left = sc.WorkingArea.Right - this.Width - 8;
            if (this.Bottom > sc.WorkingArea.Bottom - thresh) this.Top = sc.WorkingArea.Bottom - this.Height - 8;
            if (this.Top < sc.WorkingArea.Top + thresh) this.Top = sc.WorkingArea.Top + 8;

            if (AutoCollapse && !isCollapsed && this.Top >= sc.WorkingArea.Bottom - this.Height - 12)
            {
                CollapseDock();
            }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using (Pen borderPen = new Pen(Color.FromArgb(39, 39, 42), 1.5f))
            {
                e.Graphics.DrawPath(borderPen, Theme.Rounded(new Rectangle(0, 0, this.Width - 1, this.Height - 1), 28));
            }
            if (!isCollapsed)
            {
                using (Brush b = new SolidBrush(Color.FromArgb(82, 82, 91)))
                {
                    e.Graphics.FillEllipse(b, 11, 22, 3, 3);
                    e.Graphics.FillEllipse(b, 11, 28, 3, 3);
                    e.Graphics.FillEllipse(b, 11, 34, 3, 3);
                    e.Graphics.FillEllipse(b, 16, 22, 3, 3);
                    e.Graphics.FillEllipse(b, 16, 28, 3, 3);
                    e.Graphics.FillEllipse(b, 16, 34, 3, 3);
                }
            }
        }

        private void OnMouseDownDrag(object s, MouseEventArgs e)
        {
            if (e.Button == MouseButtons.Left)
            {
                isDragging = true;
                dragCursorPoint = Cursor.Position;
                dragFormPoint = this.Location;
            }
        }

        private void OnMouseMoveDrag(object s, MouseEventArgs e)
        {
            if (isDragging)
            {
                Point diff = Point.Subtract(Cursor.Position, new Size(dragCursorPoint));
                this.Location = Point.Add(dragFormPoint, new Size(diff));
            }
        }

        public void ShowDock()
        {
            if (isCollapsed) ExpandDock();
            this.Show();
            this.BringToFront();
            SyncActiveTool();
        }

        public void ToggleDock()
        {
            if (this.Visible) this.Hide();
            else ShowDock();
        }
    }
    #endregion

    #region 画笔调色盘 (PaletteForm — 修复: 旧版面板从未显示)
    public class PaletteForm : Form
    {
        private Color[] colors = new Color[] {
            Color.White,
            Color.FromArgb(239, 68, 68),
            Color.FromArgb(250, 204, 21),
            Color.FromArgb(16, 185, 129),
            Color.FromArgb(59, 130, 246),
            Color.FromArgb(217, 70, 239),
            Color.FromArgb(24, 24, 27)
        };
        private int selColor = 1;
        private int selWidth = 0;
        private int[] widths = new int[] { 3, 6, 12 };
        private List<Control> colorBtns = new List<Control>();
        private List<FlatButton> widthBtns = new List<FlatButton>();

        public PaletteForm(Form owner)
        {
            this.FormBorderStyle = FormBorderStyle.None;
            this.ShowInTaskbar = false;
            this.StartPosition = FormStartPosition.Manual;
            this.Size = new Size(388, 66);
            this.BackColor = Theme.Ink;
            this.TopMost = true;
            this.DoubleBuffered = true;
            Theme.RoundForm(this, 14);
            this.Deactivate += (s, e) => { this.Hide(); FloatingDockForm fd = owner as FloatingDockForm; if (fd != null) fd.ClosePalette(); };

            Label lblColor = new Label { Text = "笔色", ForeColor = Theme.Mut2, Font = Theme.F(8.5f), AutoSize = true, Location = new Point(16, 10), BackColor = Color.Transparent };
            this.Controls.Add(lblColor);
            for (int i = 0; i < colors.Length; i++)
            {
                Control b = MakeSwatch(colors[i], i);
                b.Location = new Point(58 + i * 32, 6);
                this.Controls.Add(b);
                colorBtns.Add(b);
            }

            Label lblWidth = new Label { Text = "粗细", ForeColor = Theme.Mut2, Font = Theme.F(8.5f), AutoSize = true, Location = new Point(16, 38), BackColor = Color.Transparent };
            this.Controls.Add(lblWidth);
            string[] wnames = new string[] { "细", "中", "粗" };
            for (int i = 0; i < widths.Length; i++)
            {
                FlatButton b = new FlatButton
                {
                    Text = wnames[i],
                    Size = new Size(52, 22),
                    Location = new Point(58 + i * 60, 36),
                    Radius = 11,
                    BackColor = Theme.Ink2,
                    HoverColor = Theme.Ink3,
                    ForeColor = Color.White,
                    Font = Theme.F(8.5f),
                    BorderColor = Theme.Ink3
                };
                int idx = i;
                b.Click += (s, e) => { selWidth = idx; Apply(); RefreshWidthBtns(); };
                this.Controls.Add(b);
                widthBtns.Add(b);
            }
            RefreshWidthBtns();
        }

        private Control MakeSwatch(Color c, int idx)
        {
            Control b = new Control
            {
                Size = new Size(26, 26),
                BackColor = Color.Transparent,
                Cursor = Cursors.Hand
            };
            int index = idx;
            b.Paint += (s, e) =>
            {
                Graphics g = e.Graphics;
                g.SmoothingMode = SmoothingMode.AntiAlias;
                Rectangle r = new Rectangle(2, 2, b.Width - 5, b.Height - 5);
                using (SolidBrush br = new SolidBrush(colors[index])) g.FillEllipse(br, r);
                if (selColor == index)
                {
                    using (Pen p = new Pen(Color.White, 2f)) g.DrawEllipse(p, 0, 0, b.Width - 1, b.Height - 1);
                }
                else
                {
                    using (Pen p = new Pen(Color.FromArgb(70, 255, 255, 255), 1f)) g.DrawEllipse(p, r);
                }
            };
            b.MouseEnter += (s, e) => b.Invalidate();
            b.Click += (s, e) => { selColor = index; Apply(); foreach (Control cb in colorBtns) cb.Invalidate(); };
            return b;
        }

        private void RefreshWidthBtns()
        {
            for (int i = 0; i < widthBtns.Count; i++)
            {
                widthBtns[i].BackColor = i == selWidth ? Color.White : Theme.Ink2;
                widthBtns[i].ForeColor = i == selWidth ? Theme.Ink : Color.White;
                widthBtns[i].Invalidate();
            }
        }

        private void Apply()
        {
            ScreenOverlayForm oc = Program.OverlayCanvas;
            if (oc != null)
            {
                oc.CurrentPenColor = colors[selColor];
                oc.CurrentPenWidth = widths[selWidth];
                if (colors[selColor].GetBrightness() < 0.1) oc.HighlightColor = Color.FromArgb(90, 250, 204, 21);
                else oc.HighlightColor = Color.FromArgb(80, colors[selColor]);
            }
        }
    }
    #endregion

    #region 课堂计时器 / 秒表 (TimerForm — 修复: 旧版秒表负数倒计时)
    public class TimerForm : Form
    {
        private bool countdown = true;
        private int remain = 300;
        private int presetSec = 300;
        private DateTime swStart;
        private TimeSpan swAccum = TimeSpan.Zero;
        private bool running = false;
        private Label digits;
        private FlatButton btnRun;
        private System.Windows.Forms.Timer tick;
        private bool drag = false;
        private Point dragPt;

        public static void Launch(IWin32Window owner)
        {
            TimerForm f = new TimerForm();
            f.Show(owner);
        }

        public TimerForm()
        {
            this.FormBorderStyle = FormBorderStyle.None;
            this.ShowInTaskbar = false;
            this.StartPosition = FormStartPosition.Manual;
            this.Size = new Size(304, 208);
            this.Location = new Point(Screen.PrimaryScreen.WorkingArea.Right - this.Width - 24, Screen.PrimaryScreen.WorkingArea.Top + 24);
            this.BackColor = Theme.Ink;
            this.TopMost = true;
            this.DoubleBuffered = true;
            this.Font = Theme.F(9f);
            Theme.RoundForm(this, 18);

            Label title = new Label { Text = "课堂计时", ForeColor = Theme.Mut2, Font = Theme.F(9f, true), AutoSize = true, Location = new Point(20, 12), BackColor = Color.Transparent };
            FlatButton btnMode = new FlatButton
            {
                Text = "切到秒表",
                Size = new Size(80, 24),
                Location = new Point(this.Width - 96, 10),
                Radius = 12,
                BackColor = Theme.Ink2,
                HoverColor = Theme.Ink3,
                ForeColor = Theme.Mut2,
                Font = Theme.F(8f),
                BorderColor = Theme.Ink3
            };
            FlatButton btnX = new FlatButton
            {
                Text = "×",
                Size = new Size(24, 24),
                Radius = 12,
                BackColor = Theme.Ink2,
                HoverColor = Theme.Danger,
                ForeColor = Color.White,
                Font = Theme.F(8f)
            };
            btnMode.Location = new Point(this.Width - 132, 10);
            btnX.Location = new Point(this.Width - 40, 10);
            btnMode.Click += (s2, e2) =>
            {
                countdown = !countdown;
                btnMode.Text = countdown ? "切到秒表" : "切到倒计时";
                running = false;
                ResetToPreset();
                RefreshRun();
            };
            btnX.Click += (s, e) => this.Close();

            digits = new Label
            {
                Text = "05:00",
                Font = new Font("Consolas", 38f, FontStyle.Bold),
                ForeColor = Color.White,
                Location = new Point(0, 44),
                Size = new Size(this.Width, 74),
                TextAlign = ContentAlignment.MiddleCenter,
                BackColor = Color.Transparent
            };

            // 计时预设
            int[] presets = new int[] { 60, 180, 300, 600, 1200 };
            string[] names = new string[] { "1分", "3分", "5分", "10分", "20分" };
            List<FlatButton> presetBtns = new List<FlatButton>();
            for (int i = 0; i < presets.Length; i++)
            {
                FlatButton b = new FlatButton
                {
                    Text = names[i],
                    Size = new Size(46, 26),
                    Location = new Point(22 + i * 52, 126),
                    Radius = 13,
                    BackColor = Theme.Ink2,
                    HoverColor = Theme.Ink3,
                    ForeColor = Theme.Mut2,
                    Font = Theme.F(8f),
                    BorderColor = Theme.Ink3
                };
                int sec = presets[i];
                b.Click += (s, e) =>
                {
                    countdown = true;
                    btnMode.Text = "切到秒表";
                    presetSec = sec;
                    running = false;
                    ResetToPreset();
                    RefreshRun();
                    foreach (FlatButton pb in presetBtns) pb.ForeColor = Theme.Mut2;
                    b.ForeColor = Color.White;
                };
                this.Controls.Add(b);
                presetBtns.Add(b);
            }
            presetBtns[2].ForeColor = Color.White;

            btnRun = new FlatButton
            {
                Text = "开始",
                Size = new Size(120, 34),
                Location = new Point(22, 162),
                Radius = 17,
                BackColor = Color.White,
                HoverColor = Color.FromArgb(228, 228, 231),
                PressedColor = Color.FromArgb(212, 212, 216),
                ForeColor = Theme.Ink,
                Font = Theme.F(9f, true)
            };
            btnRun.Click += (s, e) =>
            {
                if (countdown && remain <= 0) { ResetToPreset(); }
                running = !running;
                if (running && !countdown)
                {
                    swStart = DateTime.UtcNow;
                }
                RefreshRun();
            };
            FlatButton btnReset = new FlatButton
            {
                Text = "归零",
                Size = new Size(70, 34),
                Location = new Point(150, 162),
                Radius = 17,
                BackColor = Theme.Ink2,
                HoverColor = Theme.Ink3,
                ForeColor = Color.White,
                Font = Theme.F(9f),
                BorderColor = Theme.Ink3
            };
            btnReset.Click += (s, e) =>
            {
                running = false;
                ResetToPreset();
                RefreshRun();
            };
            FlatButton btnDrag = new FlatButton
            {
                Text = "拖动",
                Size = new Size(70, 34),
                Location = new Point(228, 162),
                Radius = 17,
                BackColor = Theme.Ink2,
                HoverColor = Theme.Ink3,
                ForeColor = Theme.Mut,
                Font = Theme.F(9f),
                BorderColor = Theme.Ink3
            };
            btnDrag.MouseDown += (s, e) => { drag = true; dragPt = Cursor.Position; };
            btnDrag.MouseMove += (s, e) =>
            {
                if (drag)
                {
                    Point diff = Point.Subtract(Cursor.Position, new Size(dragPt));
                    this.Location = Point.Add(this.Location, new Size(diff));
                    dragPt = Cursor.Position;
                }
            };
            btnDrag.MouseUp += (s, e) => { drag = false; };

            this.Controls.Add(title);
            this.Controls.Add(btnMode);
            this.Controls.Add(btnX);
            this.Controls.Add(digits);
            this.Controls.Add(btnRun);
            this.Controls.Add(btnReset);
            this.Controls.Add(btnDrag);

            // 表体拖动 (点标题/数字区拖)
            this.MouseDown += (s, e) => { if (e.Y < 44) { drag = true; dragPt = Cursor.Position; } };
            this.MouseMove += (s, e) =>
            {
                if (drag)
                {
                    Point diff = Point.Subtract(Cursor.Position, new Size(dragPt));
                    this.Location = Point.Add(this.Location, new Size(diff));
                    dragPt = Cursor.Position;
                }
            };
            this.MouseUp += (s, e) => { drag = false; };
            digits.MouseDown += (s, e) => { drag = true; dragPt = Cursor.Position; };
            digits.MouseMove += (s, e) =>
            {
                if (drag)
                {
                    Point diff = Point.Subtract(Cursor.Position, new Size(dragPt));
                    this.Location = Point.Add(this.Location, new Size(diff));
                    dragPt = Cursor.Position;
                }
            };
            digits.MouseUp += (s, e) => { drag = false; };
            title.MouseDown += (s, e) => { drag = true; dragPt = Cursor.Position; };
            title.MouseMove += (s, e) =>
            {
                if (drag)
                {
                    Point diff = Point.Subtract(Cursor.Position, new Size(dragPt));
                    this.Location = Point.Add(this.Location, new Size(diff));
                    dragPt = Cursor.Position;
                }
            };
            title.MouseUp += (s, e) => { drag = false; };

            tick = new System.Windows.Forms.Timer { Interval = 250 };
            tick.Tick += (s, e) => Step();
            tick.Start();
            RefreshRun();
        }

        private void ResetToPreset()
        {
            if (countdown) { remain = presetSec; digits.ForeColor = Color.White; }
            else { swAccum = TimeSpan.Zero; }
            Render();
        }

        private void Step()
        {
            if (!running) return;
            if (countdown)
            {
                remain--;
                if (remain <= 0)
                {
                    remain = 0;
                    running = false;
                    digits.ForeColor = Theme.Danger;
                    try { System.Media.SystemSounds.Exclamation.Play(); } catch { }
                    HudForm.Show("时间到！");
                }
                Render();
                RefreshRun();
            }
            else
            {
                Render();
            }
        }

        private void Render()
        {
            if (countdown)
            {
                int m = remain / 60;
                int sec = remain % 60;
                digits.Text = string.Format("{0:00}:{1:00}", m, sec);
                if (remain > 0 && remain <= 10) digits.ForeColor = Theme.Danger;
                else if (remain > 10) digits.ForeColor = Color.White;
            }
            else
            {
                TimeSpan el = swAccum;
                if (running) el = swAccum + (DateTime.UtcNow - swStart);
                digits.Text = string.Format("{0:00}:{1:00}", (int)el.TotalMinutes, el.Seconds);
                digits.ForeColor = Color.White;
            }
        }

        private void RefreshRun()
        {
            btnRun.Text = running ? "暂停" : "开始";
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            if (tick != null) { tick.Stop(); tick.Dispose(); }
            base.OnFormClosed(e);
        }
    }
    #endregion

    #region 工具提示浮条 (HudForm)
    public class HudForm : Form
    {
        private static HudForm inst;
        private System.Windows.Forms.Timer hide;
        private Label lbl;

        public static void Show(string text)
        {
            try
            {
                if (inst == null || inst.IsDisposed) inst = new HudForm();
                inst.Display(text);
            }
            catch { }
        }

        public HudForm()
        {
            this.FormBorderStyle = FormBorderStyle.None;
            this.ShowInTaskbar = false;
            this.StartPosition = FormStartPosition.Manual;
            this.TopMost = true;
            this.BackColor = Theme.Ink;
            this.DoubleBuffered = true;
            lbl = new Label
            {
                ForeColor = Color.White,
                Font = Theme.F(10f, true),
                AutoSize = false,
                TextAlign = ContentAlignment.MiddleCenter,
                Bounds = new Rectangle(0, 0, 200, 40),
                BackColor = Color.Transparent
            };
            this.Controls.Add(lbl);
            hide = new System.Windows.Forms.Timer { Interval = 1400 };
            hide.Tick += (s, e) => { this.Hide(); hide.Stop(); };
        }

        private void Display(string text)
        {
            hide.Stop();
            Size ts = TextRenderer.MeasureText(text, lbl.Font);
            int w = ts.Width + 44;
            this.Size = new Size(w, 42);
            lbl.Size = new Size(w, 42);
            Screen sc = Screen.PrimaryScreen;
            this.Location = new Point(sc.WorkingArea.Left + (sc.WorkingArea.Width - w) / 2, sc.WorkingArea.Top + 26);
            lbl.Text = text;
            Theme.RoundForm(this, 21);
            this.Show();
            this.BringToFront();
            hide.Start();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
        }
    }
    #endregion

    #region 全屏批注与随页板书画布 (ScreenOverlayForm)
    public class ScreenOverlayForm : Form
    {
        public enum ToolType { Cursor, Laser, Pen, Highlighter, Eraser }

        public ToolType CurrentTool = ToolType.Cursor;
        public Color CurrentPenColor = Color.FromArgb(239, 68, 68);
        public Color HighlightColor = Color.FromArgb(100, 250, 204, 21);
        public int CurrentPenWidth = 3;
        public int CurrentSlideIndex = 1;
        public Action ToolChanged;

        public class Stroke
        {
            public ToolType Tool;
            public Color StrokeColor;
            public int Width;
            public List<Point> Points = new List<Point>();
        }

        private Dictionary<int, List<Stroke>> slideStrokes = new Dictionary<int, List<Stroke>>();
        private Stroke activeStroke = null;
        private Point laserPos = new Point(-1000, -1000);
        private bool laserOn = false;
        private bool isWhiteboardMode = false;

        public ScreenOverlayForm()
        {
            this.FormBorderStyle = FormBorderStyle.None;
            this.WindowState = FormWindowState.Maximized;
            this.TopMost = true;
            this.ShowInTaskbar = false;
            this.DoubleBuffered = true;
            this.BackColor = Color.Magenta;
            this.TransparencyKey = Color.Magenta;

            this.MouseDown += OnMouseDown;
            this.MouseMove += OnMouseMove;
            this.MouseUp += OnMouseUp;
            this.KeyDown += OnKeyDown;

            SetPassThrough(true);
        }

        private void OnUI(Action a)
        {
            try
            {
                if (InvokeRequired) BeginInvoke(a);
                else a();
            }
            catch { }
        }

        private static string ToolName(ToolType t)
        {
            if (t == ToolType.Cursor) return "鼠标";
            if (t == ToolType.Laser) return "激光笔";
            if (t == ToolType.Pen) return "画笔";
            if (t == ToolType.Highlighter) return "荧光笔";
            if (t == ToolType.Eraser) return "橡皮";
            return "";
        }

        public void SetTool(ToolType tool)
        {
            OnUI(delegate
            {
                CurrentTool = tool;
                SetPassThrough(tool == ToolType.Cursor);
                this.Invalidate();
                if (ToolChanged != null) { try { ToolChanged(); } catch { } }
                string extra = "";
                if (tool == ToolType.Pen) extra = " · Esc 退出";
                HudForm.Show(ToolName(tool) + extra);
            });
        }

        public void ToggleLaser()
        {
            OnUI(delegate
            {
                if (CurrentTool == ToolType.Laser) SetTool(ToolType.Cursor);
                else SetTool(ToolType.Laser);
            });
        }

        public void ToggleWhiteboard()
        {
            OnUI(delegate
            {
                isWhiteboardMode = !isWhiteboardMode;
                if (isWhiteboardMode)
                {
                    this.TransparencyKey = Color.Empty;
                    this.BackColor = Color.FromArgb(18, 18, 20);
                    SetPassThrough(false);
                    HudForm.Show("白板模式 · Esc 退出");
                }
                else
                {
                    this.BackColor = Color.Magenta;
                    this.TransparencyKey = Color.Magenta;
                    SetPassThrough(CurrentTool == ToolType.Cursor);
                    HudForm.Show("返回课件");
                }
                this.Invalidate();
            });
        }

        public void NextSlide()
        {
            OnUI(delegate { CurrentSlideIndex++; this.Invalidate(); });
        }

        public void PrevSlide()
        {
            OnUI(delegate { if (CurrentSlideIndex > 1) CurrentSlideIndex--; this.Invalidate(); });
        }

        public void ClearCurrentPage()
        {
            OnUI(delegate
            {
                if (slideStrokes.ContainsKey(CurrentSlideIndex))
                {
                    slideStrokes[CurrentSlideIndex].Clear();
                    this.Invalidate();
                }
            });
        }

        private void SetPassThrough(bool passThrough)
        {
            try
            {
                int exStyle = GetWindowLong(this.Handle, -20); // GWL_EXSTYLE
                if (passThrough)
                    SetWindowLong(this.Handle, -20, exStyle | 0x20 | 0x80000); // WS_EX_TRANSPARENT | WS_EX_LAYERED
                else
                    SetWindowLong(this.Handle, -20, (exStyle & ~0x20) | 0x80000);
            }
            catch { }
        }

        private void OnMouseDown(object s, MouseEventArgs e)
        {
            if (CurrentTool == ToolType.Pen || CurrentTool == ToolType.Highlighter)
            {
                if (!slideStrokes.ContainsKey(CurrentSlideIndex))
                    slideStrokes[CurrentSlideIndex] = new List<Stroke>();

                Color col = CurrentTool == ToolType.Highlighter ? HighlightColor : CurrentPenColor;
                int w = CurrentTool == ToolType.Highlighter ? 18 : CurrentPenWidth;
                activeStroke = new Stroke { Tool = CurrentTool, StrokeColor = col, Width = w };
                activeStroke.Points.Add(e.Location);
                slideStrokes[CurrentSlideIndex].Add(activeStroke);
                this.Invalidate();
            }
            else if (CurrentTool == ToolType.Eraser)
            {
                EraseAt(e.Location);
            }
            else if (CurrentTool == ToolType.Laser)
            {
                laserOn = true;
                laserPos = e.Location;
                this.Invalidate();
            }
        }

        private void OnMouseMove(object s, MouseEventArgs e)
        {
            if (CurrentTool == ToolType.Laser)
            {
                laserPos = e.Location;
                this.Invalidate();
            }
            else if ((CurrentTool == ToolType.Pen || CurrentTool == ToolType.Highlighter) && activeStroke != null)
            {
                activeStroke.Points.Add(e.Location);
                this.Invalidate();
            }
            else if (CurrentTool == ToolType.Eraser && e.Button == MouseButtons.Left)
            {
                EraseAt(e.Location);
            }
        }

        private void OnMouseUp(object s, MouseEventArgs e)
        {
            activeStroke = null;
            if (CurrentTool == ToolType.Laser)
            {
                laserOn = false;
                laserPos = new Point(-1000, -1000);
                this.Invalidate();
            }
        }

        private void EraseAt(Point pt)
        {
            if (!slideStrokes.ContainsKey(CurrentSlideIndex)) return;
            List<Stroke> list = slideStrokes[CurrentSlideIndex];
            int r2 = 22 * 22;
            for (int i = list.Count - 1; i >= 0; i--)
            {
                bool hit = false;
                foreach (Point p in list[i].Points)
                {
                    int dx = p.X - pt.X;
                    int dy = p.Y - pt.Y;
                    if (dx * dx + dy * dy <= r2) { hit = true; break; }
                }
                if (hit) list.RemoveAt(i);
            }
            this.Invalidate();
        }

        private void OnKeyDown(object s, KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Escape)
            {
                if (isWhiteboardMode) ToggleWhiteboard();
                else SetTool(ToolType.Cursor);
            }
            else if (e.Control && e.KeyCode == Keys.Z)
            {
                if (slideStrokes.ContainsKey(CurrentSlideIndex) && slideStrokes[CurrentSlideIndex].Count > 0)
                {
                    slideStrokes[CurrentSlideIndex].RemoveAt(slideStrokes[CurrentSlideIndex].Count - 1);
                    this.Invalidate();
                }
            }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;

            if (slideStrokes.ContainsKey(CurrentSlideIndex))
            {
                foreach (Stroke st in slideStrokes[CurrentSlideIndex])
                {
                    if (st.Points.Count > 1)
                    {
                        using (Pen pen = new Pen(st.StrokeColor, st.Width))
                        {
                            pen.StartCap = LineCap.Round;
                            pen.EndCap = LineCap.Round;
                            pen.LineJoin = LineJoin.Round;
                            e.Graphics.DrawLines(pen, st.Points.ToArray());
                        }
                    }
                }
            }

            if (CurrentTool == ToolType.Laser && laserOn)
            {
                using (Brush glow = new SolidBrush(Color.FromArgb(70, 239, 68, 68)))
                    e.Graphics.FillEllipse(glow, laserPos.X - 18, laserPos.Y - 18, 36, 36);
                using (Brush mid = new SolidBrush(Color.FromArgb(160, 239, 68, 68)))
                    e.Graphics.FillEllipse(mid, laserPos.X - 10, laserPos.Y - 10, 20, 20);
                using (Brush core = new SolidBrush(Color.White))
                    e.Graphics.FillEllipse(core, laserPos.X - 3.5f, laserPos.Y - 3.5f, 7, 7);
            }
        }

        public void ShowOverlay()
        {
            this.Show();
            this.BringToFront();
        }

        public void HideOverlay()
        {
            this.Hide();
        }

        [DllImport("user32.dll")]
        private static extern int GetWindowLong(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll")]
        private static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);
    }
    #endregion
}
