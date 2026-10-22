
#pragma region 全屏批注与随页板书 (Overlay)

#define OV_CURSOR 1
#define OV_LASER 2
#define OV_PEN 3
#define OV_HL 4
#define OV_ERASER 5
#define OV_KEYCOLOR RGB(255, 0, 255)

typedef struct { COLORREF c; int w; POINT* pts; int n, cap; } Stroke;
typedef struct { int slide; Stroke** items; int n, cap; } SlideStrokes;
static SlideStrokes* g_slides = NULL;
static int g_slidesN = 0, g_slidesCap = 0;
static int g_ovTool = OV_CURSOR;
static COLORREF g_ovPenColor = RGB(239, 68, 68);
static int g_ovPenWidth = 3;
static int g_ovSlide = 1;
static BOOL g_ovBoard = FALSE;
static POINT g_laser = { -1000, -1000 };
static Stroke* g_active = NULL;

static SlideStrokes* SlideGet(int slide, BOOL create) {
    for (int i = 0; i < g_slidesN; i++)
        if (g_slides[i].slide == slide) return &g_slides[i];
    if (!create) return NULL;
    if (g_slidesN >= g_slidesCap) {
        g_slidesCap = g_slidesCap ? g_slidesCap * 2 : 8;
        g_slides = (SlideStrokes*)xrealloc(g_slides, sizeof(SlideStrokes) * g_slidesCap);
    }
    SlideStrokes* s = &g_slides[g_slidesN++];
    s->slide = slide; s->items = NULL; s->n = 0; s->cap = 0;
    return s;
}
static void StrokePushPoint(Stroke* st, int x, int y) {
    if (st->n >= st->cap) {
        st->cap = st->cap ? st->cap * 2 : 32;
        st->pts = (POINT*)xrealloc(st->pts, sizeof(POINT) * st->cap);
    }
    st->pts[st->n].x = x;
    st->pts[st->n].y = y;
    st->n++;
}
static void DrawStrokes(HDC dc, SlideStrokes* ss) {
    if (!ss) return;
    for (int i = 0; i < ss->n; i++) {
        Stroke* st = ss->items[i];
        if (st->n < 2) continue;
        HPEN pen = CreatePen(PS_SOLID, st->w, st->c);
        HGDIOBJ old = SelectObject(dc, pen);
        Polyline(dc, st->pts, st->n);
        SelectObject(dc, old);
        DeleteObject(pen);
    }
}
static void OverlayPaintAll(HDC dc) {
    RECT r;
    GetClientRect(g_overlay, &r);
    HDC mem = CreateCompatibleDC(dc);
    HBITMAP bmp = CreateCompatibleBitmap(dc, r.right, r.bottom);
    HGDIOBJ ob = SelectObject(mem, bmp);
    HBRUSH bg = CreateSolidBrush(g_ovBoard ? RGB(18, 18, 20) : OV_KEYCOLOR);
    FillRect(mem, &r, bg);
    DeleteObject(bg);
    DrawStrokes(mem, SlideGet(g_ovSlide, FALSE));
    if (g_ovTool == OV_LASER && g_laser.x > -500) {
        HBRUSH g1 = CreateSolidBrush(RGB(239, 68, 68));
        HGDIOBJ o1 = SelectObject(mem, g1);
        Ellipse(mem, g_laser.x - 18, g_laser.y - 18, g_laser.x + 18, g_laser.y + 18);
        HBRUSH g2 = CreateSolidBrush(RGB(255, 120, 120));
        SelectObject(mem, g2);
        Ellipse(mem, g_laser.x - 9, g_laser.y - 9, g_laser.x + 9, g_laser.y + 9);
        HBRUSH g3 = CreateSolidBrush(RGB(255, 255, 255));
        SelectObject(mem, g3);
        Ellipse(mem, g_laser.x - 3, g_laser.y - 3, g_laser.x + 3, g_laser.y + 3);
        SelectObject(mem, o1);
        DeleteObject(g1); DeleteObject(g2); DeleteObject(g3);
    }
    BitBlt(dc, 0, 0, r.right, r.bottom, mem, 0, 0, SRCCOPY);
    SelectObject(mem, ob);
    DeleteObject(bmp);
    DeleteDC(mem);
}
static void OverlaySetPassThrough(BOOL pass) {
    int ex = GetWindowLongW(g_overlay, GWL_EXSTYLE);
    if (pass) ex |= WS_EX_TRANSPARENT;
    else ex &= ~WS_EX_TRANSPARENT;
    SetWindowLongW(g_overlay, GWL_EXSTYLE, ex);
}
static const wchar_t* ToolName(int t) {
    switch (t) {
        case OV_CURSOR: return L"鼠标";
        case OV_LASER: return L"激光笔";
        case OV_PEN: return L"画笔";
        case OV_HL: return L"荧光笔";
        case OV_ERASER: return L"橡皮";
    }
    return L"";
}
static int OverlayCurrentTool(void) { return g_ovTool; }
static void OverlaySetPen(COLORREF c, int w) {
    g_ovPenColor = c;
    g_ovPenWidth = w;
}
static void OverlayApplyTool(int tool, BOOL announce) {
    g_ovTool = tool;
    OverlaySetPassThrough(tool == OV_CURSOR);
    InvalidateRect(g_overlay, NULL, FALSE);
    DockSyncActive(tool);
    if (announce) {
        wchar_t txt[64];
        if (tool == OV_PEN) _snwprintf(txt, 64, L"%s · Esc 退出", ToolName(tool));
        else _snwprintf(txt, 64, L"%s", ToolName(tool));
        ShowHud(txt);
    }
}
void OverlayToggleBoard(void) {
    g_ovBoard = !g_ovBoard;
    if (g_ovBoard) {
        OverlaySetPassThrough(FALSE);
        ShowHud(L"白板模式 · Esc 退出");
    } else {
        OverlaySetPassThrough(g_ovTool == OV_CURSOR);
        ShowHud(L"返回课件");
    }
    InvalidateRect(g_overlay, NULL, FALSE);
}
void OverlaySlide(int delta) {
    g_ovSlide += delta;
    if (g_ovSlide < 1) g_ovSlide = 1;
    InvalidateRect(g_overlay, NULL, FALSE);
}
static void OverlayEraseAt(int x, int y) {
    SlideStrokes* ss = SlideGet(g_ovSlide, FALSE);
    if (!ss) return;
    int r2 = 22 * 22;
    for (int i = ss->n - 1; i >= 0; i--) {
        Stroke* st = ss->items[i];
        BOOL hit = FALSE;
        for (int j = 0; j < st->n; j++) {
            int dx = st->pts[j].x - x, dy = st->pts[j].y - y;
            if (dx * dx + dy * dy <= r2) { hit = TRUE; break; }
        }
        if (hit) {
            free(st->pts);
            free(st);
            ss->items[i] = ss->items[ss->n - 1];
            ss->n--;
        }
    }
    InvalidateRect(g_overlay, NULL, FALSE);
}
static LRESULT CALLBACK OverlayProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC dc = BeginPaint(h, &ps);
            OverlayPaintAll(dc);
            EndPaint(h, &ps);
            return 0;
        }
        case WM_ERASEBKGND:
            return 1;
        case WM_LBUTTONDOWN: {
            POINTS p = MAKEPOINTS(l);
            SetFocus(h);
            if (g_ovTool == OV_PEN || g_ovTool == OV_HL) {
                g_active = (Stroke*)xmalloc(sizeof(Stroke));
                g_active->c = (g_ovTool == OV_HL) ? RGB(110, 235, 90) : g_ovPenColor;
                if (g_ovTool == OV_HL) g_active->c = RGB(120, 225, 70);
                g_active->w = (g_ovTool == OV_HL) ? 18 : g_ovPenWidth;
                g_active->pts = NULL; g_active->n = 0; g_active->cap = 0;
                StrokePushPoint(g_active, p.x, p.y);
                SlideStrokes* ss = SlideGet(g_ovSlide, TRUE);
                if (ss->n >= ss->cap) {
                    ss->cap = ss->cap ? ss->cap * 2 : 16;
                    ss->items = (Stroke**)xrealloc(ss->items, sizeof(Stroke*) * ss->cap);
                }
                ss->items[ss->n++] = g_active;
                SetCapture(h);
            } else if (g_ovTool == OV_ERASER) {
                OverlayEraseAt(p.x, p.y);
                SetCapture(h);
            } else if (g_ovTool == OV_LASER) {
                g_laser.x = p.x; g_laser.y = p.y;
                InvalidateRect(h, NULL, FALSE);
                SetCapture(h);
            }
            return 0;
        }
        case WM_MOUSEMOVE: {
            POINTS p = MAKEPOINTS(l);
            if (g_active && (g_ovTool == OV_PEN || g_ovTool == OV_HL)) {
                int n = g_active->n;
                StrokePushPoint(g_active, p.x, p.y);
                if (n >= 1) {
                    HDC dc = GetDC(h);
                    HPEN pen = CreatePen(PS_SOLID, g_active->w, g_active->c);
                    HGDIOBJ old = SelectObject(dc, pen);
                    GdiLine(dc, g_active->pts[n - 1].x, g_active->pts[n - 1].y, p.x, p.y);
                    SelectObject(dc, old);
                    DeleteObject(pen);
                    ReleaseDC(h, dc);
                }
            } else if (g_ovTool == OV_ERASER && (w & MK_LBUTTON)) {
                OverlayEraseAt(p.x, p.y);
            } else if (g_ovTool == OV_LASER) {
                g_laser.x = p.x; g_laser.y = p.y;
                InvalidateRect(h, NULL, FALSE);
            }
            return 0;
        }
        case WM_LBUTTONUP:
            if (GetCapture() == h) ReleaseCapture();
            g_active = NULL;
            if (g_ovTool == OV_LASER) {
                g_laser.x = -1000; g_laser.y = -1000;
                InvalidateRect(h, NULL, FALSE);
            }
            return 0;
        case WM_KEYDOWN:
            if (w == VK_ESCAPE) {
                if (g_ovBoard) OverlayToggleBoard();
                else OverlayApplyTool(OV_CURSOR, TRUE);
            } else if (w == 'Z' && (GetKeyState(VK_CONTROL) & 0x8000)) {
                SlideStrokes* ss = SlideGet(g_ovSlide, FALSE);
                if (ss && ss->n > 0) {
                    free(ss->items[ss->n - 1]->pts);
                    free(ss->items[ss->n - 1]);
                    ss->n--;
                    InvalidateRect(h, NULL, FALSE);
                }
            }
            return 0;
        case WM_APP_TOOL:
            if ((int)w == OV_LASER && g_ovTool == OV_LASER) OverlayApplyTool(OV_CURSOR, TRUE);
            else OverlayApplyTool((int)w, TRUE);
            return 0;
        case WM_APP_SLIDE:
            OverlaySlide((int)w);
            return 0;
        case WM_APP_CLEAR: {
            SlideStrokes* ss = SlideGet(g_ovSlide, FALSE);
            if (ss) {
                for (int i = 0; i < ss->n; i++) { free(ss->items[i]->pts); free(ss->items[i]); }
                ss->n = 0;
            }
            InvalidateRect(h, NULL, FALSE);
            ShowHud(L"已清空本页板书");
            return 0;
        }
    }
    return DefWindowProcW(h, m, w, l);
}
static void OverlayCreate(void) {
    WNDCLASSW wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc = OverlayProc;
    wc.hInstance = g_hInst;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.lpszClassName = L"FLAOVL";
    RegisterClassW(&wc);
    g_overlay = CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE,
                                L"FLAOVL", NULL, WS_POPUP,
                                0, 0, GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN),
                                NULL, NULL, g_hInst, NULL);
    SetLayeredWindowAttributes(g_overlay, OV_KEYCOLOR, 0, LWA_COLORKEY);
    OverlaySetPassThrough(TRUE);
}
#pragma endregion

#pragma region PPT 下载与调起

static void PathFindApp(wchar_t* out, int cch) {
    const wchar_t* cands[] = {
        L"C:\\Program Files\\Microsoft Office\\root\\Office16\\POWERPNT.EXE",
        L"C:\\Program Files (x86)\\Microsoft Office\\root\\Office16\\POWERPNT.EXE",
        L"C:\\Program Files\\Microsoft Office\\Office15\\POWERPNT.EXE",
        L"C:\\Program Files (x86)\\Microsoft Office\\Office15\\POWERPNT.EXE",
        L"C:\\Program Files\\Microsoft Office\\Office14\\POWERPNT.EXE",
        L"C:\\Program Files (x86)\\Microsoft Office\\Office14\\POWERPNT.EXE",
    };
    for (int i = 0; i < 6; i++) {
        if (GetFileAttributesW(cands[i]) != INVALID_FILE_ATTRIBUTES) {
            lstrcpynW(out, cands[i], cch);
            return;
        }
    }
    wchar_t lad[MAX_PATH];
    GetEnvironmentVariableW(L"LOCALAPPDATA", lad, MAX_PATH);
    _snwprintf(out, cch, L"%s\\Local\\Kingsoft\\WPS Office\\ksolaunch.exe", lad);
    if (GetFileAttributesW(out) == INVALID_FILE_ATTRIBUTES)
        _snwprintf(out, cch, L"C:\\Program Files (x86)\\Kingsoft\\WPS Office\\ksolaunch.exe");
    if (GetFileAttributesW(out) == INVALID_FILE_ATTRIBUTES) out[0] = 0;
}
static void LaunchOfficePresentation(const wchar_t* fileUrl, const wchar_t* name, const wchar_t* token) {
    wchar_t lad[MAX_PATH];
    GetEnvironmentVariableW(L"LOCALAPPDATA", lad, MAX_PATH);
    wchar_t cacheDir[MAX_PATH + 16];
    _snwprintf(cacheDir, MAX_PATH + 16, L"%s\\FLA_Cache", lad);
    CreateDirectoryW(cacheDir, NULL);
    /* 文件名安全化 */
    wchar_t safe[MAX_PATH];
    lstrcpynW(safe, (name && name[0]) ? name : L"presentation.pptx", MAX_PATH);
    const wchar_t* bad = L"\\/:*?\"<>|";
    for (wchar_t* c = safe; *c; c++)
        if (wcschr(bad, *c)) *c = L'_';
    wchar_t localPath[MAX_PATH + MAX_PATH];
    _snwprintf(localPath, MAX_PATH + MAX_PATH, L"%s\\%s", cacheDir, safe);
    if (!DownloadToFile(fileUrl, (token && token[0]) ? token : NULL, localPath)) {
        MessageBoxW(g_main, L"课件下载失败：无法连接服务器或登录已失效", L"FLA 提示", MB_OK | MB_ICONWARNING);
        return;
    }
    wchar_t app[MAX_PATH];
    PathFindApp(app, MAX_PATH);
    SHELLEXECUTEINFOW sei;
    ZeroMemory(&sei, sizeof(sei));
    sei.cbSize = sizeof(sei);
    sei.nShow = SW_SHOWNORMAL;
    if (app[0]) {
        wchar_t params[MAX_PATH + 16];
        _snwprintf(params, MAX_PATH + 16, L"/s \"%s\"", localPath);
        sei.lpVerb = L"open";
        sei.lpFile = app;
        sei.lpParameters = params;
    } else {
        sei.lpVerb = L"open";
        sei.lpFile = localPath;
    }
    ShellExecuteExW(&sei);
    Sleep(1200);
    /* 回到 UI 线程唤起悬浮盒 + 画布 */
    PostMessageW(g_main, WM_APP_STAGE, 0, 0);
}
#pragma endregion

#pragma region 主入口

static HANDLE g_mutex = NULL;

static void ForwardProtocol(const wchar_t* url);
static DWORD WINAPI ProtocolLaunchThread(LPVOID p) {
    wchar_t** args = (wchar_t**)p;
    LaunchOfficePresentation(args[0], args[1], args[2][0] ? args[2] : g_token);
    free(args[0]); free(args[1]); free(args[2]);
    free(args);
    return 0;
}

static void ForwardProtocol(const wchar_t* url) {
    wchar_t* e = JsonEscape(url);
    wchar_t body[1200];
    _snwprintf(body, 1200, L"{\"url\":\"%s\"}", e);
    free(e);
    char* b8 = wide_to_utf8(body);
    wchar_t target[128];
    _snwprintf(target, 128, L"http://127.0.0.1:%d/api/open", FLA_PORT);
    URL_COMPONENTS uc;
    ZeroMemory(&uc, sizeof(uc));
    uc.dwStructSize = sizeof(uc);
    wchar_t host[64], path[128];
    uc.lpszHostName = host; uc.dwHostNameLength = 64;
    uc.lpszUrlPath = path; uc.dwUrlPathLength = 128;
    if (WinHttpCrackUrl(target, 0, 0, &uc)) {
        HINTERNET ses = WinHttpOpen(L"FLA", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, NULL, NULL, 0);
        if (ses) {
            HINTERNET con = WinHttpConnect(ses, host, uc.nPort, 0);
            if (con) {
                HINTERNET req = WinHttpOpenRequest(con, L"POST", path, NULL, NULL, NULL, 0);
                if (req) {
                    wchar_t hdrs[] = L"Content-Type: application/json\r\n";
                    WinHttpSendRequest(req, hdrs, -1L, (LPVOID)b8, lstrlenA(b8), lstrlenA(b8), 0);
                    WinHttpReceiveResponse(req, NULL);
                    WinHttpCloseHandle(req);
                }
                WinHttpCloseHandle(con);
            }
            WinHttpCloseHandle(ses);
        }
    }
    free(b8);
}

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE prev, PWSTR cmdline, int show) {
    (void)prev; (void)show;
    g_hInst = hInst;
    SetProcessDPIAware();

    g_mutex = CreateMutexW(NULL, TRUE, L"FLA_Desktop_Mutex_" FLA_VERSION);
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        if (cmdline && wcsstr(cmdline, L"fla://")) {
            /* 已有实例在跑: 从命令行抠出 url= 参数, 转发给它的本地桥 */
            const wchar_t* q = wcsstr(cmdline, L"fla://");
            const wchar_t* u = wcsstr(q, L"url=");
            if (u) {
                u += 4;
                int i = 0;
                while (u[i] && u[i] != L'&') i++;
                wchar_t* url = (wchar_t*)xmalloc((i + 1) * sizeof(wchar_t));
                int di = 0;
                for (int k = 0; k < i; k++) {
                    if (u[k] == L'%' && k + 2 < i) {
                        wchar_t hex[3] = { u[k + 1], u[k + 2], 0 };
                        url[di++] = (wchar_t)wcstol(hex, NULL, 16);
                        k += 2;
                    } else if (u[k] == L'+') url[di++] = L' ';
                    else url[di++] = u[k];
                }
                url[di] = 0;
                ForwardProtocol(url);
                free(url);
            }
        }
        return 0;
    }

    InitCommonControls();
    LoadConfig();
    RegisterProtocol();
    CloseHandle(CreateThread(NULL, 0, BridgeServerThread, NULL, 0, NULL));
    CloseHandle(CreateThread(NULL, 0, HealthThread, NULL, 0, NULL));
    CloseHandle(CreateThread(NULL, 0, SeewoThread, NULL, 0, NULL));

    /* 字体 */
    g_font = CreateFontW(-17, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                         OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Microsoft YaHei UI");
    g_fontB = CreateFontW(-17, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, DEFAULT_CHARSET,
                          OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Microsoft YaHei UI");
    g_fontS = CreateFontW(-15, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                          OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Microsoft YaHei UI");
    g_fontH1 = CreateFontW(-34, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET,
                           OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Microsoft YaHei UI");
    g_fontMono = CreateFontW(-64, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET,
                             OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, FIXED_PITCH, L"Consolas");
    g_fontTiny = CreateFontW(-13, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                             OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Microsoft YaHei UI");

    WNDCLASSW bc;
    ZeroMemory(&bc, sizeof(bc));
    bc.lpfnWndProc = BtnProc;
    bc.hInstance = hInst;
    bc.hCursor = LoadCursor(NULL, IDC_ARROW);
    bc.lpszClassName = L"FLABTN";
    RegisterClassW(&bc);
    WNDCLASSW hc;
    ZeroMemory(&hc, sizeof(hc));
    hc.lpfnWndProc = HudProc;
    hc.hInstance = hInst;
    hc.lpszClassName = L"FLAHUD";
    RegisterClassW(&hc);

    g_hud = CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, L"FLAHUD", NULL,
                            WS_POPUP, 0, 0, 200, 44, NULL, NULL, hInst, NULL);

    CreateMainWindow();
    DockCreate();
    PaletteCreate();
    OverlayCreate();

    ShowWindow(g_main, SW_SHOW);
    UpdateWindow(g_main);

    /* fla:// 冷启动 */
    if (cmdline && wcsstr(cmdline, L"fla://")) {
        const wchar_t* q = wcsstr(cmdline, L"fla://");
        const wchar_t* qq = wcschr(q, L'?');
        if (qq) {
            wchar_t url[1024] = L"";
            const wchar_t* u = wcsstr(qq, L"url=");
            if (u) {
                u += 4;
                int i = 0;
                for (; u[i] && u[i] != L'&'; i++) {}
                if (i > 0 && i < 1024) {
                    CopyMemory(url, u, i * sizeof(wchar_t));
                    url[i] = 0;
                    /* URL 解码 */
                    wchar_t* dec = (wchar_t*)xmalloc((lstrlenW(url) + 1) * sizeof(wchar_t));
                    int di = 0;
                    for (int k = 0; url[k]; k++) {
                        if (url[k] == L'%' && url[k + 1] && url[k + 2]) {
                            wchar_t hex[3] = { url[k + 1], url[k + 2], 0 };
                            dec[di++] = (wchar_t)wcstol(hex, NULL, 16);
                            k += 2;
                        } else dec[di++] = url[k];
                    }
                    dec[di] = 0;
                    wchar_t** args = (wchar_t**)xmalloc(3 * sizeof(wchar_t*));
                    args[0] = dec;
                    args[1] = (wchar_t*)xmalloc(16 * sizeof(wchar_t));
                    lstrcpynW(args[1], L"presentation.pptx", 16);
                    args[2] = (wchar_t*)xmalloc(4 * sizeof(wchar_t));
                    args[2][0] = 0;
                    CloseHandle(CreateThread(NULL, 0, ProtocolLaunchThread, args, 0, NULL));
                }
            }
        }
    }

    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    g_running = FALSE;
    if (g_mutex) ReleaseMutex(g_mutex);
    CloseHandle(g_mutex);
    return 0;
}
#pragma endregion
