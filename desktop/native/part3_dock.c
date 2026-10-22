
#pragma region 悬浮工具盒 (Dock)

static HWND g_dockBtns[20];   /* 按 dock id-300 索引 */
static BOOL g_paletteOpen = FALSE;

static LRESULT CALLBACK DockProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC dc = BeginPaint(h, &ps);
            RECT r;
            GetClientRect(h, &r);
            HDC mem = CreateCompatibleDC(dc);
            HBITMAP bmp = CreateCompatibleBitmap(dc, r.right, r.bottom);
            HGDIOBJ ob = SelectObject(mem, bmp);
            HBRUSH ink = CreateSolidBrush(C_INK);
            FillRect(mem, &r, ink);
            DeleteObject(ink);
            HPEN bp = CreatePen(PS_SOLID, 1, C_INK3);
            HGDIOBJ op = SelectObject(mem, bp);
            RoundRect(mem, 0, 0, r.right, r.bottom, 58, 58);
            SelectObject(mem, op);
            DeleteObject(bp);
            /* 拖动把手圆点 */
            HBRUSH grip = CreateSolidBrush(RGB(82, 82, 91));
            int gy = (r.bottom - 16) / 2;
            for (int gx = 0; gx < 2; gx++)
                for (int gy2 = 0; gy2 < 3; gy2++) {
                    RECT dot = { 11 + gx * 5, gy + gy2 * 6, 14 + gx * 5, gy + gy2 * 6 + 3 };
                    FillRect(mem, &dot, grip);
                }
            DeleteObject(grip);
            BitBlt(dc, 0, 0, r.right, r.bottom, mem, 0, 0, SRCCOPY);
            SelectObject(mem, ob);
            DeleteObject(bmp);
            DeleteDC(mem);
            EndPaint(h, &ps);
            return 0;
        }
        case WM_ERASEBKGND:
            return 1;
        case WM_NCHITTEST: {
            LRESULT hit = DefWindowProcW(h, m, w, l);
            if (hit == HTCLIENT) return HTCAPTION; /* 空白区可拖动 */
            return hit;
        }
        case WM_EXITSIZEMOVE: {
            RECT wr, wa;
            GetWindowRect(h, &wr);
            SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0);
            int thresh = 40;
            if (wr.left < wa.left + thresh) wr.left = wa.left + 8;
            if (wr.right > wa.right - thresh) wr.left = wa.right - (wr.right - wr.left) - 8;
            if (wr.bottom > wa.bottom - thresh) wr.top = wa.bottom - (wr.bottom - wr.top) - 8;
            if (wr.top < wa.top + thresh) wr.top = wa.top + 8;
            SetWindowPos(h, NULL, wr.left, wr.top, 0, 0, SWP_NOSIZE | SWP_NOZORDER);
            return 0;
        }
        case WM_COMMAND: {
            int id = LOWORD(w);
            if (id >= 300 && id <= 312) g_dockBtns[id - 300] = (HWND)l;
            switch (id) {
                case 300: PostMessageW(g_overlay, WM_APP_TOOL, 1, 0); return 0;   /* 鼠标 */
                case 301: PostMessageW(g_overlay, WM_APP_TOOL, 2, 0); return 0;   /* 激光 */
                case 302:                                                          /* 画笔+调色盘 */
                    PostMessageW(g_overlay, WM_APP_TOOL, 3, 0);
                    DockTogglePalette();
                    return 0;
                case 303: PostMessageW(g_overlay, WM_APP_TOOL, 4, 0); return 0;   /* 荧光 */
                case 304: PostMessageW(g_overlay, WM_APP_TOOL, 5, 0); return 0;   /* 橡皮 */
                case 305: PostMessageW(g_overlay, WM_APP_CLEAR, 0, 0); return 0;  /* 清屏 */
                case 306:                                                          /* 上一页 */
                    keybd_event(VK_PRIOR, 0, 0, 0); keybd_event(VK_PRIOR, 0, KEYEVENTF_KEYUP, 0);
                    PostMessageW(g_overlay, WM_APP_SLIDE, -1, 0);
                    return 0;
                case 307:                                                          /* 下一页 */
                    keybd_event(VK_NEXT, 0, 0, 0); keybd_event(VK_NEXT, 0, KEYEVENTF_KEYUP, 0);
                    PostMessageW(g_overlay, WM_APP_SLIDE, 1, 0);
                    return 0;
                case 308: OverlayToggleBoard(); return 0;                          /* 白板 */
                case 309: {                                                        /* 手机遥控 */
                    ShowWindow(g_main, SW_RESTORE);
                    ShowWindow(g_main, SW_SHOW);
                    SetForegroundWindow(g_main);
                    MainSwitchTab(1);
                    return 0;
                }
                case 310: TimerLaunch(); return 0;                                 /* 计时器 */
                case 311: DockToolsMenu(); return 0;                               /* 更多工具 */
                case 312: DockCollapse(); return 0;                                /* 收起 */
                case 313: DockExpand(); return 0;                                  /* 展开标签 */
            }
            return 0;
        }
        case WM_APP_DOCKTOGGLE:
            if (IsWindowVisible(h)) ShowWindow(h, SW_HIDE);
            else DockShow();
            return 0;
        case WM_APP_SYNC: { /* wParam = 当前工具 (1..5), 高亮 */
            int tool = (int)w;
            for (int i = 0; i < 13; i++) {
                if (!g_dockBtns[i]) continue;
                int kind = -1;
                if (i == 0) kind = 1; else if (i == 1) kind = 2; else if (i == 2) kind = 3;
                else if (i == 3) kind = 4; else if (i == 4) kind = 5;
                BOOL active = (kind == tool);
                BtnSetColors(g_dockBtns[i],
                             active ? RGB(255, 255, 255) : C_INK2,
                             active ? C_INK : RGB(255, 255, 255),
                             active ? RGB(255, 255, 255) : C_INK3);
            }
            return 0;
        }
    }
    return DefWindowProcW(h, m, w, l);
}

static void DockInitButtons(HWND h) {
    int x = 26;
    struct { int id; int icon; const wchar_t* tip; int w; } defs[] = {
        { 300, IC_CURSOR, L"鼠标 / 选择", 42 }, { 301, IC_LASER, L"激光笔", 42 },
        { 302, IC_PEN, L"画笔 (点击选颜色)", 42 }, { 303, IC_HL, L"荧光笔", 42 },
        { 304, IC_ERASER, L"橡皮", 42 }, { 305, IC_TRASH, L"清空本页板书", 42 },
        { 306, IC_CHEVL, L"上一页", 36 }, { 307, IC_CHEVR, L"下一页", 36 },
        { 308, IC_BOARD, L"白板 / 恢复", 42 }, { 309, IC_PHONE, L"手机投屏遥控", 42 },
        { 310, IC_CLOCK, L"课堂计时器 / 秒表", 42 }, { 311, IC_GRID, L"更多工具", 42 },
        { 312, IC_CLOSE, L"收起", 34 },
    };
    int n = sizeof(defs) / sizeof(defs[0]);
    int bh = 42;
    for (int i = 0; i < n; i++) {
        int w = defs[i].w;
        int by = (w < bh) ? (58 - w) / 2 : (58 - bh) / 2;
        RECT r2 = { x, by, x + w, by + w };
        HWND b = NewBtn(h, L"", defs[i].id, r2, C_INK2, RGB(255, 255, 255), C_INK3, C_INK, 10, defs[i].icon, g_font, FALSE);
        g_dockBtns[defs[i].id - 300] = b;
        x += w + 6;
    }
    RECT wrap;
    GetWindowRect(h, &wrap);
    int wantW = x + 12;
    if (wrap.right - wrap.left != wantW) {
        SetWindowPos(h, NULL, 0, 0, wantW, 58, SWP_NOMOVE | SWP_NOZORDER);
        HRGN rg = CreateRoundRectRgn(0, 0, wantW, 58, 29, 29);
        SetWindowRgn(h, rg, TRUE);
    }
}

static void DockCreate(void) {
    WNDCLASSW wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc = DockProc;
    wc.hInstance = g_hInst;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.lpszClassName = L"FLADOCK";
    RegisterClassW(&wc);
    RECT wa;
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0);
    g_dock = CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, L"FLADOCK", NULL,
                             WS_POPUP, wa.left + (wa.right - wa.left) / 2 - 328, wa.bottom - 92,
                             656, 58, NULL, NULL, g_hInst, NULL);
    DockInitButtons(g_dock);
    HRGN rg = CreateRoundRectRgn(0, 0, 656, 58, 29, 29);
    SetWindowRgn(g_dock, rg, FALSE);
}
static void DockShow(void) {
    ShowWindow(g_dock, SW_SHOW);
    SetWindowPos(g_dock, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
    PostMessageW(g_dock, WM_APP_SYNC, (WPARAM)OverlayCurrentTool(), 0);
}
static void DockCollapse(void) {
    for (int i = 0; i < 20; i++) {
        if (g_dockBtns[i]) { DestroyWindow(g_dockBtns[i]); g_dockBtns[i] = NULL; }
    }
    RECT wa;
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0);
    SetWindowPos(g_dock, HWND_TOPMOST, wa.left + (wa.right - wa.left) / 2 - 28, wa.bottom - 64, 56, 56, SWP_NOZORDER);
    HRGN rg = CreateRoundRectRgn(0, 0, 56, 56, 17, 17);
    SetWindowRgn(g_dock, rg, TRUE);
    RECT rc = { 2, 2, 54, 54 };
    NewBtn(g_dock, L"FLA", 313, rc, C_INK, RGB(255, 255, 255), C_INK3, C_INK, 17, IC_NONE, g_fontB, FALSE);
    InvalidateRect(g_dock, NULL, TRUE);
}
static void DockExpand(void) {
    for (int i = 0; i < 20; i++) g_dockBtns[i] = NULL;
    RECT wa;
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0);
    SetWindowPos(g_dock, HWND_TOPMOST, wa.left + (wa.right - wa.left) / 2 - 328, wa.bottom - 92, 656, 58, SWP_NOZORDER);
    DockInitButtons(g_dock);
    HRGN rg = CreateRoundRectRgn(0, 0, 656, 58, 29, 29);
    SetWindowRgn(g_dock, rg, TRUE);
    InvalidateRect(g_dock, NULL, TRUE);
}
static void DockSyncActive(int tool) {
    PostMessageW(g_dock, WM_APP_SYNC, (WPARAM)tool, 0);
}

/* ---- 工具菜单 ---- */
static void DockToolsMenu(void) {
    HMENU m = CreatePopupMenu();
    AppendMenuW(m, MF_STRING, 600, L"课堂计时器 / 秒表");
    AppendMenuW(m, MF_STRING, 601, L"黑屏幕布 (B)");
    AppendMenuW(m, MF_STRING, 602, L"白屏幕布 (W)");
    AppendMenuW(m, MF_SEPARATOR, 0, NULL);
    AppendMenuW(m, MF_STRING, 603, L"返回主控制台");
    RECT r;
    GetWindowRect(g_dock, &r);
    int id = TrackPopupMenu(m, TPM_RETURNCMD | TPM_NONOTIFY, r.left + 380, r.top - 140, 0, g_dock, NULL);
    DestroyMenu(m);
    if (id == 600) TimerLaunch();
    else if (id == 601) { keybd_event('B', 0, 0, 0); keybd_event('B', 0, KEYEVENTF_KEYUP, 0); }
    else if (id == 602) { keybd_event('W', 0, 0, 0); keybd_event('W', 0, KEYEVENTF_KEYUP, 0); }
    else if (id == 603) {
        ShowWindow(g_main, SW_RESTORE);
        ShowWindow(g_main, SW_SHOW);
        SetForegroundWindow(g_main);
    }
}
#pragma endregion

#pragma region 画笔调色盘 (Palette)

static LRESULT CALLBACK PaletteProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC dc = BeginPaint(h, &ps);
            RECT r;
            GetClientRect(h, &r);
            HDC mem = CreateCompatibleDC(dc);
            HBITMAP bmp = CreateCompatibleBitmap(dc, r.right, r.bottom);
            HGDIOBJ ob = SelectObject(mem, bmp);
            HBRUSH ink = CreateSolidBrush(C_INK);
            FillRect(mem, &r, ink);
            DeleteObject(ink);
            SetBkMode(mem, TRANSPARENT);
            SelectObject(mem, g_fontTiny);
            SetTextColor(mem, C_MUT2);
            TextOutW(mem, 14, 8, L"笔色", 4);
            TextOutW(mem, 14, 38, L"粗细", 4);
            BitBlt(dc, 0, 0, r.right, r.bottom, mem, 0, 0, SRCCOPY);
            SelectObject(mem, ob);
            DeleteObject(bmp);
            DeleteDC(mem);
            EndPaint(h, &ps);
            return 0;
        }
        case WM_ERASEBKGND:
            return 1;
        case WM_ACTIVATE:
            if (LOWORD(w) == WA_INACTIVE) {
                ShowWindow(h, SW_HIDE);
                g_paletteOpen = FALSE;
            }
            return 0;
        case WM_COMMAND: {
            int id = LOWORD(w);
            
            if (id >= 400 && id < 407) { PaletteApply(id - 400, -1); return 0; }
            if (id >= 410 && id < 413) { PaletteApply(-1, id - 410); return 0; }
            return 0;
        }
    }
    return DefWindowProcW(h, m, w, l);
}
static const COLORREF g_palColors[7] = {
    RGB(255, 255, 255), RGB(239, 68, 68), RGB(250, 204, 21), RGB(16, 185, 129),
    RGB(59, 130, 246), RGB(217, 70, 239), RGB(24, 24, 27)
};
static int g_palColor = 1, g_palWidth = 0;
static HWND g_palSwatch[7], g_palWidthBtn[3];
static const int g_widths[3] = { 3, 6, 12 };

static void RefreshPalButtons(void) {
    for (int i = 0; i < 7; i++)
        if (g_palSwatch[i]) InvalidateRect(g_palSwatch[i], NULL, FALSE);
    for (int i = 0; i < 3; i++)
        if (g_palWidthBtn[i])
            BtnSetColors(g_palWidthBtn[i], i == g_palWidth ? RGB(255, 255, 255) : C_INK2,
                         i == g_palWidth ? C_INK : RGB(255, 255, 255), C_INK3);
}
static void PaletteApply(int colorIdx, int widthIdx) {
    if (colorIdx >= 0) g_palColor = colorIdx;
    if (widthIdx >= 0) g_palWidth = widthIdx;
    OverlaySetPen(g_palColors[g_palColor], g_widths[g_palWidth]);
    RefreshPalButtons();
}
static void PaletteCreate(void) {
    WNDCLASSW wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc = PaletteProc;
    wc.hInstance = g_hInst;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.lpszClassName = L"FLAPAL";
    RegisterClassW(&wc);
    g_palette = CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, L"FLAPAL", NULL,
                                WS_POPUP, 100, 100, 388, 66, NULL, NULL, g_hInst, NULL);
    for (int i = 0; i < 7; i++) {
        RECT rc = { 58 + i * 32, 5, 58 + i * 32 + 24, 29 };
        HWND b = NewBtn(g_palette, L"", 400 + i, rc, g_palColors[i], RGB(255, 255, 255), g_palColors[i], C_INK, 0, IC_NONE, g_font, FALSE);
        BtnData* d = (BtnData*)GetWindowLongPtrW(b, GWLP_USERDATA);
        d->circle = TRUE;
        d->border = RGB(90, 90, 96);
        g_palSwatch[i] = b;
    }
    const wchar_t* wn[3] = { L"细", L"中", L"粗" };
    for (int i = 0; i < 3; i++) {
        RECT rc = { 58 + i * 64, 34, 58 + i * 64 + 54, 58 };
        g_palWidthBtn[i] = NewBtn(g_palette, wn[i], 410 + i, rc, C_INK2, RGB(255, 255, 255), C_INK3, C_INK, 11, IC_NONE, g_fontS, FALSE);
    }
    HRGN rg = CreateRoundRectRgn(0, 0, 388, 66, 14, 14);
    SetWindowRgn(g_palette, rg, FALSE);
    RefreshPalButtons();
}
static void DockTogglePalette(void) {
    if (g_paletteOpen) {
        ShowWindow(g_palette, SW_HIDE);
        g_paletteOpen = FALSE;
        return;
    }
    RECT dr;
    GetWindowRect(g_dock, &dr);
    POINT pt = { dr.left + 110, dr.top - 66 - 12 };
    if (pt.y < 8) pt.y = 8;
    SetWindowPos(g_palette, HWND_TOPMOST, pt.x, pt.y, 0, 0, SWP_NOSIZE | SWP_SHOWWINDOW);
    SetForegroundWindow(g_palette);
    g_paletteOpen = TRUE;
}
#pragma endregion

#pragma region 课堂计时器 / 秒表

static BOOL g_tmCountdown = TRUE;
static int g_tmRemain = 300, g_tmPreset = 300;
static BOOL g_tmRunning = FALSE;
static ULONGLONG g_tmSwBase = 0, g_tmSwAccum = 0;
static HWND g_tmDigits, g_tmRun;
static HWND g_tmPresetBtns[5];

static void TimerRender(void) {
    wchar_t txt[32];
    if (g_tmCountdown) {
        _snwprintf(txt, 32, L"%02d:%02d", g_tmRemain / 60, g_tmRemain % 60);
        SetWindowTextW(g_tmDigits, txt);
    } else {
        ULONGLONG ms = g_tmSwAccum + (g_tmRunning ? (GetTickCount64() - g_tmSwBase) : 0);
        _snwprintf(txt, 32, L"%02d:%02d", (int)(ms / 60000), (int)((ms / 1000) % 60));
        SetWindowTextW(g_tmDigits, txt);
    }
}
static void TimerRefreshRun(void) {
    BtnSetText(g_tmRun, g_tmRunning ? L"暂停" : L"开始");
}
static void TimerReset(void) {
    if (g_tmCountdown) g_tmRemain = g_tmPreset;
    else g_tmSwAccum = 0;
    g_tmRunning = FALSE;
    TimerRender();
    TimerRefreshRun();
}
static LRESULT CALLBACK TimerProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC dc = BeginPaint(h, &ps);
            RECT r;
            GetClientRect(h, &r);
            HDC mem = CreateCompatibleDC(dc);
            HBITMAP bmp = CreateCompatibleBitmap(dc, r.right, r.bottom);
            HGDIOBJ ob = SelectObject(mem, bmp);
            HBRUSH ink = CreateSolidBrush(C_INK);
            FillRect(mem, &r, ink);
            DeleteObject(ink);
            BitBlt(dc, 0, 0, r.right, r.bottom, mem, 0, 0, SRCCOPY);
            SelectObject(mem, ob);
            DeleteObject(bmp);
            DeleteDC(mem);
            EndPaint(h, &ps);
            return 0;
        }
        case WM_ERASEBKGND:
            return 1;
        case WM_TIMER:
            if (!g_tmRunning) return 0;
            if (g_tmCountdown) {
                g_tmRemain--;
                if (g_tmRemain <= 0) {
                    g_tmRemain = 0;
                    g_tmRunning = FALSE;
                    MessageBeep(MB_ICONEXCLAMATION);
                    ShowHud(L"时间到！");
                    TimerRefreshRun();
                }
                TimerRender();
            } else {
                TimerRender();
            }
            return 0;
        case WM_NCHITTEST: {
            LRESULT hit = DefWindowProcW(h, m, w, l);
            return hit == HTCLIENT ? HTCAPTION : hit;
        }
        case WM_COMMAND: {
            int id = LOWORD(w);
            if (id == 500) { /* 切模式 */
                g_tmCountdown = !g_tmCountdown;
                BtnSetText((HWND)l, g_tmCountdown ? L"切到秒表" : L"切到倒计时");
                TimerReset();
            } else if (id == 501) {
                DestroyWindow(h);
                g_timer = NULL;
            } else if (id >= 510 && id <= 514) {
                int presets[5] = { 60, 180, 300, 600, 1200 };
                g_tmCountdown = TRUE;
                g_tmPreset = presets[id - 510];
                TimerReset();
                for (int i = 0; i < 5; i++)
                    if (g_tmPresetBtns[i])
                        BtnSetColors(g_tmPresetBtns[i], i == (id - 510) ? RGB(255, 255, 255) : C_INK2,
                                     i == (id - 510) ? C_INK : C_MUT2, C_INK3);
            } else if (id == 520) { /* 开始/暂停 */
                if (g_tmCountdown && g_tmRemain <= 0) g_tmRemain = g_tmPreset;
                g_tmRunning = !g_tmRunning;
                if (g_tmRunning && !g_tmCountdown) g_tmSwBase = GetTickCount64();
                if (!g_tmRunning && !g_tmCountdown) g_tmSwAccum += GetTickCount64() - g_tmSwBase;
                TimerRefreshRun();
            } else if (id == 521) {
                TimerReset();
            }
            return 0;
        }
        case WM_DESTROY:
            KillTimer(h, 1);
            return 0;
    }
    return DefWindowProcW(h, m, w, l);
}
static void TimerLaunch(void) {
    if (g_timer && IsWindow(g_timer)) {
        ShowWindow(g_timer, SW_SHOW);
        SetForegroundWindow(g_timer);
        return;
    }
    WNDCLASSW wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc = TimerProc;
    wc.hInstance = g_hInst;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.lpszClassName = L"FLATIMER";
    RegisterClassW(&wc);
    RECT wa;
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0);
    g_timer = CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, L"FLATIMER", NULL,
                              WS_POPUP, wa.right - 304 - 24, wa.top + 24, 304, 208,
                              NULL, NULL, g_hInst, NULL);
    HRGN rg = CreateRoundRectRgn(0, 0, 304, 208, 18, 18);
    SetWindowRgn(g_timer, rg, FALSE);

    RECT rc0 = { 20, 10, 20 + 80, 34 };
    NewBtn(g_timer, L"切到秒表", 500, rc0, C_INK2, C_MUT2, C_INK3, C_INK, 12, IC_NONE, g_fontS, FALSE);
    RECT rcx = { 264, 10, 288, 34 };
    NewBtn(g_timer, L"", 501, rcx, C_INK2, RGB(255, 255, 255), C_DANGER, C_INK, 12, IC_CLOSE, g_fontS, FALSE);
    g_tmDigits = CreateWindowExW(0, L"STATIC", L"05:00", WS_CHILD | SS_CENTER,
                                 0, 44, 304, 74, g_timer, NULL, g_hInst, NULL);
    SendMessageW(g_tmDigits, WM_SETFONT, (WPARAM)g_fontMono, TRUE);
    const wchar_t* pn[5] = { L"1分", L"3分", L"5分", L"10分", L"20分" };
    for (int i = 0; i < 5; i++) {
        RECT rc = { 22 + i * 52, 126, 22 + i * 52 + 46, 152 };
        g_tmPresetBtns[i] = NewBtn(g_timer, pn[i], 510 + i, rc, C_INK2, i == 2 ? RGB(255, 255, 255) : C_MUT2,
                                   C_INK3, C_INK, 13, IC_NONE, g_fontS, FALSE);
    }
    RECT rcr = { 22, 162, 142, 196 };
    g_tmRun = NewBtn(g_timer, L"开始", 520, rcr, RGB(255, 255, 255), C_INK, C_LINE, C_INK, 17, IC_NONE, g_fontB, FALSE);
    RECT rcrr = { 150, 162, 220, 196 };
    NewBtn(g_timer, L"归零", 521, rcrr, C_INK2, RGB(255, 255, 255), C_INK3, C_INK, 17, IC_NONE, g_font, FALSE);
    ShowWindow(g_timer, SW_SHOW);
    SetTimer(g_timer, 1, 250, NULL);
    SetForegroundWindow(g_timer);
}
#pragma endregion
