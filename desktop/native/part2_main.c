
#pragma region FLABTN — 自绘按钮/圆形色块/导航项

enum IconKind { IC_NONE, IC_CURSOR, IC_LASER, IC_PEN, IC_HL, IC_ERASER, IC_TRASH,
                IC_CHEVL, IC_CHEVR, IC_BOARD, IC_PHONE, IC_CLOCK, IC_GRID, IC_CLOSE };

typedef struct {
    COLORREF bg, fg, hoverBg, pressedBg, border, parentBg;
    wchar_t text[96];
    int icon;
    int radius;
    int id;
    HFONT font;
    BOOL hover, down, circle, alignLeft;
} BtnData;

/* 95×95 逻辑空间的矢量图标 */
static void DrawIconGDI(HDC dc, int kind, RECT r, COLORREF c) {
    float s = (r.right - r.left) / 95.0f;
    int ox = r.left, oy = r.top;
#define MP(lx, ly) (int)(ox + (lx) * s), (int)(oy + (ly) * s)
    HPEN pen = CreatePen(PS_SOLID, (int)(7 * s), c);
    HGDIOBJ old = SelectObject(dc, pen);
    switch (kind) {
        case IC_CURSOR: {
            POINT pts[7] = { {MP(28,14)}, {MP(28,72)}, {MP(44,58)}, {MP(55,82)}, {MP(64,78)}, {MP(54,55)}, {MP(72,54)} };
            HBRUSH b = CreateSolidBrush(c);
            HGDIOBJ ob = SelectObject(dc, b);
            Polygon(dc, pts, 7);
            SelectObject(dc, ob);
            DeleteObject(b);
            break;
        }
        case IC_LASER: {
            HBRUSH b = CreateSolidBrush(c);
            HGDIOBJ ob = SelectObject(dc, b);
            Ellipse(dc, MP(40,40), (int)(ox + 55 * s), (int)(oy + 55 * s));
            SelectObject(dc, ob);
            DeleteObject(b);
            GdiLine(dc, MP(47,12), MP(47,26)); GdiLine(dc, MP(47,69), MP(47,83));
            GdiLine(dc, MP(12,47), MP(26,47)); GdiLine(dc, MP(69,47), MP(83,47));
            GdiLine(dc, MP(22,22), MP(32,32)); GdiLine(dc, MP(62,62), MP(72,72));
            GdiLine(dc, MP(72,22), MP(62,32)); GdiLine(dc, MP(32,62), MP(22,72));
            break;
        }
        case IC_PEN:
            GdiLine(dc, MP(30,65), MP(68,27));
            GdiLine(dc, MP(24,71), MP(30,65));
            GdiLine(dc, MP(21,68), MP(27,74));
            GdiLine(dc, MP(60,23), MP(72,35));
            break;
        case IC_HL: {
            HPEN tp = CreatePen(PS_SOLID, (int)(18 * s), c);
            SelectObject(dc, tp);
            GdiLine(dc, MP(38,52), MP(68,22));
            SelectObject(dc, pen);
            DeleteObject(tp);
            GdiLine(dc, MP(20,72), MP(52,72));
            break;
        }
        case IC_ERASER: {
            POINT pts[4] = { {MP(24,58)}, {MP(48,24)}, {MP(74,40)}, {MP(50,74)} };
            Polygon(dc, pts, 4);
            GdiLine(dc, MP(18,80), MP(76,80));
            break;
        }
        case IC_TRASH:
            GdiLine(dc, MP(22,28), MP(72,28));
            GdiLine(dc, MP(40,28), MP(40,20)); GdiLine(dc, MP(40,20), MP(54,20)); GdiLine(dc, MP(54,20), MP(54,28));
            GdiLine(dc, MP(30,36), MP(33,76)); GdiLine(dc, MP(64,36), MP(61,76));
            GdiLine(dc, MP(33,76), MP(61,76));
            GdiLine(dc, MP(47,40), MP(47,70));
            break;
        case IC_CHEVL: GdiLine(dc, MP(58,24), MP(36,47)); GdiLine(dc, MP(36,47), MP(58,70)); break;
        case IC_CHEVR: GdiLine(dc, MP(36,24), MP(58,47)); GdiLine(dc, MP(58,47), MP(36,70)); break;
        case IC_BOARD:
            Rectangle(dc, MP(18,22), (int)(ox + 76 * s), (int)(oy + 62 * s));
            GdiLine(dc, MP(34,62), MP(34,76)); GdiLine(dc, MP(60,62), MP(60,76));
            break;
        case IC_PHONE:
            Rectangle(dc, MP(32,14), (int)(ox + 62 * s), (int)(oy + 80 * s));
            GdiLine(dc, MP(42,70), MP(52,70));
            break;
        case IC_CLOCK:
            Ellipse(dc, MP(18,18), (int)(ox + 76 * s), (int)(oy + 76 * s));
            GdiLine(dc, MP(47,28), MP(47,48)); GdiLine(dc, MP(47,48), MP(60,56));
            break;
        case IC_GRID:
            Rectangle(dc, MP(20,20), (int)(ox + 42 * s), (int)(oy + 42 * s));
            Rectangle(dc, MP(52,20), (int)(ox + 74 * s), (int)(oy + 42 * s));
            Rectangle(dc, MP(20,52), (int)(ox + 42 * s), (int)(oy + 74 * s));
            Rectangle(dc, MP(52,52), (int)(ox + 74 * s), (int)(oy + 74 * s));
            break;
        case IC_CLOSE: GdiLine(dc, MP(30,30), MP(64,64)); GdiLine(dc, MP(64,30), MP(30,64)); break;
    }
    SelectObject(dc, old);
    DeleteObject(pen);
#undef MP
}

static LRESULT CALLBACK BtnProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    BtnData* d = (BtnData*)GetWindowLongPtrW(h, GWLP_USERDATA);
    switch (m) {
        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC dc = BeginPaint(h, &ps);
            RECT r;
            GetClientRect(h, &r);
            /* 离屏双缓冲 */
            HDC mem = CreateCompatibleDC(dc);
            HBITMAP bmp = CreateCompatibleBitmap(dc, r.right, r.bottom);
            HGDIOBJ ob = SelectObject(mem, bmp);
            /* 父底色兜底 + 圆角主体 */
            HBRUSH pb = CreateSolidBrush(d->parentBg);
            FillRect(mem, &r, pb);
            DeleteObject(pb);
            COLORREF fill = d->down ? d->pressedBg : (d->hover ? d->hoverBg : d->bg);
            HBRUSH b = CreateSolidBrush(fill);
            HPEN bp = CreatePen(PS_SOLID, 1, d->border);
            HGDIOBJ ob2 = SelectObject(mem, b);
            HGDIOBJ op = SelectObject(mem, bp);
            if (d->circle) {
                Ellipse(mem, r.left + 1, r.top + 1, r.right - 1, r.bottom - 1);
                /* 圆形色块选中环 */
                if (d->hover) {
                    HPEN ring = CreatePen(PS_SOLID, 2, RGB(255, 255, 255));
                    HGDIOBJ oring = SelectObject(mem, ring);
                    Ellipse(mem, r.left, r.top, r.right - 1, r.bottom - 1);
                    SelectObject(mem, oring);
                    DeleteObject(ring);
                }
            } else {
                RoundRect(mem, r.left, r.top, r.right, r.bottom, d->radius * 2, d->radius * 2);
                if (d->icon != IC_NONE) {
                    int isz = (r.bottom - r.top) - 14;
                    RECT ir = { (r.right - r.left - isz) / 2, 7, (r.right - r.left + isz) / 2, r.bottom - 7 };
                    if (d->text[0]) { ir.left = 8; ir.top = 8; ir.right = 8 + (r.bottom - r.top) - 16; ir.bottom = r.bottom - 8; }
                    DrawIconGDI(mem, d->icon, ir, d->fg);
                }
                if (d->text[0]) {
                    SetBkMode(mem, TRANSPARENT);
                    SelectObject(mem, d->font);
                    SetTextColor(mem, d->fg);
                    RECT tr = r;
                    if (d->alignLeft) {
                        tr.left += 14;
                        DrawTextW(mem, d->text, -1, &tr, DT_SINGLELINE | DT_VCENTER | DT_LEFT);
                    } else if (d->icon != IC_NONE) {
                        tr.left += (r.bottom - r.top);
                        DrawTextW(mem, d->text, -1, &tr, DT_SINGLELINE | DT_VCENTER | DT_LEFT);
                    } else {
                        DrawTextW(mem, d->text, -1, &tr, DT_SINGLELINE | DT_VCENTER | DT_CENTER);
                    }
                }
            }
            BitBlt(dc, 0, 0, r.right, r.bottom, mem, 0, 0, SRCCOPY);
            SelectObject(mem, ob2);
            SelectObject(mem, op);
            SelectObject(mem, ob);
            DeleteObject(b);
            DeleteObject(bp);
            DeleteObject(bmp);
            DeleteDC(mem);
            EndPaint(h, &ps);
            return 0;
        }
        case WM_ERASEBKGND:
            return 1;
        case WM_MOUSEMOVE: {
            if (!d->hover) {
                d->hover = TRUE;
                InvalidateRect(h, NULL, FALSE);
                TRACKMOUSEEVENT tm;
                tm.cbSize = sizeof(tm);
                tm.dwFlags = TME_LEAVE;
                tm.hwndTrack = h;
                TrackMouseEvent(&tm);
            }
            return 0;
        }
        case WM_MOUSELEAVE:
            d->hover = FALSE;
            d->down = FALSE;
            InvalidateRect(h, NULL, FALSE);
            return 0;
        case WM_LBUTTONDOWN:
            d->down = TRUE;
            InvalidateRect(h, NULL, FALSE);
            SetCapture(h);
            return 0;
        case WM_LBUTTONUP: {
            d->down = FALSE;
            InvalidateRect(h, NULL, FALSE);
            if (GetCapture() == h) ReleaseCapture();
            POINT pt;
            GetCursorPos(&pt);
            RECT r;
            GetClientRect(h, &r);
            MapWindowPoints(h, HWND_DESKTOP, (POINT*)&r, 2);
            if (PtInRect(&r, pt)) {
                HWND parent = GetParent(h);
                if (parent) PostMessageW(parent, WM_COMMAND, MAKEWPARAM(d->id, 0), (LPARAM)h);
            }
            return 0;
        }
        case WM_DESTROY:
            return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

static HWND NewBtn(HWND parent, const wchar_t* text, int id, RECT rc, COLORREF bg, COLORREF fg,
                   COLORREF hoverBg, COLORREF parentBg, int radius, int icon, HFONT font, BOOL alignLeft) {
    HWND h = CreateWindowExW(0, L"FLABTN", NULL, WS_CHILD | WS_VISIBLE,
                             rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top,
                             parent, NULL, g_hInst, NULL);
    BtnData* d = (BtnData*)xmalloc(sizeof(BtnData));
    ZeroMemory(d, sizeof(*d));
    d->bg = bg; d->fg = fg; d->hoverBg = hoverBg; d->pressedBg = hoverBg;
    d->border = bg; d->parentBg = parentBg;
    d->radius = radius; d->icon = icon; d->font = font ? font : g_font;
    d->alignLeft = alignLeft;
    d->id = id;
    lstrcpynW(d->text, text, 96);
    SetWindowLongPtrW(h, GWLP_USERDATA, (LONG_PTR)d);
    return h;
}
static void BtnSetText(HWND h, const wchar_t* text) {
    BtnData* d = (BtnData*)GetWindowLongPtrW(h, GWLP_USERDATA);
    if (!d) return;
    lstrcpynW(d->text, text, 96);
    InvalidateRect(h, NULL, FALSE);
}
static void BtnSetColors(HWND h, COLORREF bg, COLORREF fg, COLORREF hoverBg) {
    BtnData* d = (BtnData*)GetWindowLongPtrW(h, GWLP_USERDATA);
    if (!d) return;
    d->bg = bg; d->fg = fg; d->hoverBg = hoverBg; d->border = bg;
    InvalidateRect(h, NULL, FALSE);
}
#pragma endregion

#pragma region HUD 浮条

static LRESULT CALLBACK HudProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC dc = BeginPaint(h, &ps);
            RECT r;
            GetClientRect(h, &r);
            wchar_t txt[256];
            GetWindowTextW(h, txt, 256);
            HDC mem = CreateCompatibleDC(dc);
            HBITMAP bmp = CreateCompatibleBitmap(dc, r.right, r.bottom);
            HGDIOBJ ob = SelectObject(mem, bmp);
            HBRUSH b = CreateSolidBrush(C_INK);
            FillRect(mem, &r, b);
            DeleteObject(b);
            SetBkMode(mem, TRANSPARENT);
            SelectObject(mem, g_fontB);
            SetTextColor(mem, RGB(255, 255, 255));
            DrawTextW(mem, txt, -1, &r, DT_SINGLELINE | DT_VCENTER | DT_CENTER);
            BitBlt(dc, 0, 0, r.right, r.bottom, mem, 0, 0, SRCCOPY);
            SelectObject(mem, ob);
            DeleteObject(bmp);
            DeleteDC(mem);
            EndPaint(h, &ps);
            return 0;
        }
        case WM_TIMER:
            KillTimer(h, 1);
            ShowWindow(h, SW_HIDE);
            return 0;
    }
    return DefWindowProcW(h, m, w, l);
}
static void ShowHud(const wchar_t* text) {
    if (!g_hud) return;
    SetWindowTextW(g_hud, text);
    HDC dc = GetDC(g_hud);
    RECT sz = { 0, 0, 0, 0 };
    SelectObject(dc, g_fontB);
    DrawTextW(dc, text, -1, &sz, DT_CALCRECT);
    ReleaseDC(g_hud, dc);
    int w = sz.right + 56, ht = 44;
    RECT wa;
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &wa, 0);
    SetWindowPos(g_hud, HWND_TOPMOST, wa.left + ((wa.right - wa.left) - w) / 2, wa.top + 26, w, ht,
                 SWP_SHOWWINDOW | SWP_NOACTIVATE);
    HRGN rg = CreateRoundRectRgn(0, 0, w, ht, 21, 21);
    SetWindowRgn(g_hud, rg, TRUE);
    InvalidateRect(g_hud, NULL, FALSE);
    SetTimer(g_hud, 1, 1400, NULL);
}
#pragma endregion

#pragma region 主窗口

static const int NAV_W = 208, HDR_H = 64;
static int g_tab = 0; /* 0=library 1=remote 2=dockcfg 3=settings */

static HWND g_nav[4];
static HWND g_lib_list, g_lib_refresh, g_lib_web, g_lib_tip;
static HWND g_rem_copy, g_rem_open, g_rem_status, g_rem_info;
static HWND g_dock_show, g_ck_seewo;
static HWND g_ed_url, g_ed_user, g_ed_pass, g_btn_login, g_btn_logout, g_btn_upd, g_lb_status, g_lb_ver;
static HWND g_hdr_dock, g_hdr_hide;

/* 列表数据 (fid/名字 与行号对应) */
static wchar_t** g_fids = NULL;
static wchar_t** g_fnames = NULL;
static int g_fileCount = 0;

static void FreeFileList(void) {
    for (int i = 0; i < g_fileCount; i++) { free(g_fids[i]); free(g_fnames[i]); }
    free(g_fids); free(g_fnames);
    g_fids = NULL; g_fnames = NULL; g_fileCount = 0;
}

static void SetTip(const wchar_t* text, COLORREF color) {
    SetWindowTextW(g_lib_tip, text);
    InvalidateRect(g_lib_tip, NULL, FALSE);
}

static void FillFileList(const wchar_t* json) {
    ListView_DeleteAllItems(g_lib_list);
    FreeFileList();
    int count = 0;
    /* 先数一遍 */
    const wchar_t* cur = json;
    JsonSlice sl;
    while (JsonNextObject(&cur, &sl)) count++;
    if (count == 0) { SetTip(L"云端还没有课件 — 到网页端上传后点「刷新」", C_MUT); return; }
    g_fids = (wchar_t**)xmalloc(sizeof(wchar_t*) * count);
    g_fnames = (wchar_t**)xmalloc(sizeof(wchar_t*) * count);
    cur = json;
    LVITEMW it;
    while (JsonNextObject(&cur, &sl)) {
        wchar_t* name = SliceFind(&sl, L"name");
        if (!name || !name[0]) { free(name); continue; }
        wchar_t* id = SliceFind(&sl, L"id");
        wchar_t* kind = SliceFind(&sl, L"kind");
        wchar_t* pages = SliceFind(&sl, L"pages");
        wchar_t* size = SliceFind(&sl, L"size");
        wchar_t* created = SliceFind(&sl, L"created_at");
        wchar_t kindU[32] = L"—";
        if (kind && kind[0]) { wchar_t* k = kind; while (*k) { *k = towupper(*k); k++; } lstrcpynW(kindU, kind, 32); }
        wchar_t pagesU[32] = L"—";
        if (pages && pages[0] && lstrcmpW(pages, L"0") != 0) { lstrcpynW(pagesU, pages, 28); lstrcatW(pagesU, L" 页"); }
        wchar_t sizeU[48] = L"—";
        if (size && size[0]) {
            long long b = _wtoi64(size);
            if (b < 1024) _snwprintf(sizeU, 48, L"%lld B", b);
            else if (b < 1024 * 1024) _snwprintf(sizeU, 48, L"%.1f KB", b / 1024.0);
            else _snwprintf(sizeU, 48, L"%.1f MB", b / 1024.0 / 1024.0);
        }
        wchar_t createdU[32] = L"";
        if (created && lstrlenW(created) >= 10) { CopyMemory(createdU, created, 10 * sizeof(wchar_t)); createdU[10] = 0; }
        ZeroMemory(&it, sizeof(it));
        it.mask = LVIF_TEXT;
        it.iItem = ListView_GetItemCount(g_lib_list);
        it.pszText = name;
        int idx = ListView_InsertItem(g_lib_list, &it);
        ListView_SetItemText(g_lib_list, idx, 1, kindU);
        ListView_SetItemText(g_lib_list, idx, 2, pagesU);
        ListView_SetItemText(g_lib_list, idx, 3, sizeU);
        ListView_SetItemText(g_lib_list, idx, 4, createdU);
        g_fids[idx] = id ? id : (wchar_t*)xmalloc(sizeof(wchar_t));
        g_fnames[idx] = name;
        free(kind); free(pages); free(size); free(created);
    }
    g_fileCount = ListView_GetItemCount(g_lib_list);
    if (g_fileCount == 0) SetTip(L"云端还没有课件 — 到网页端上传后点「刷新」", C_MUT);
    else SetTip(L"", C_MUT);
}

static DWORD WINAPI LoadFilesThread(LPVOID) {
    wchar_t* j = NULL;
    if (g_token[0]) j = ApiGetText(L"/api/files");
    PostMessageW(g_main, WM_APP_FILES, 0, (LPARAM)j);
    return 0;
}
static void RefreshLibrary(void) {
    CloseHandle(CreateThread(NULL, 0, LoadFilesThread, NULL, 0, NULL));
}

typedef struct { wchar_t user[128], pass[128]; } LoginJob;
static DWORD WINAPI LoginThread(LPVOID p) {
    LoginJob* job = (LoginJob*)p;
    wchar_t* eu = JsonEscape(job->user);
    wchar_t* ep = JsonEscape(job->pass);
    wchar_t body[640];
    _snwprintf(body, 640, L"{\"username\":\"%s\",\"password\":\"%s\"}", eu, ep);
    free(eu); free(ep);
    wchar_t* resp = NULL;
    int status = 0;
    BOOL ok = ApiPostJson(L"/api/auth/login", body, &resp, &status);
    wchar_t* msg;
    BOOL success = FALSE;
    if (!ok) {
        msg = (wchar_t*)xmalloc(128 * sizeof(wchar_t));
        lstrcpynW(msg, L"无法连接服务器，请检查服务地址", 128);
    } else if (status >= 400) {
        wchar_t* detail = resp ? JsonFind(resp, L"detail") : NULL;
        msg = (wchar_t*)xmalloc(256 * sizeof(wchar_t));
        if (detail && detail[0]) _snwprintf(msg, 256, L"登录失败：%s", detail);
        else lstrcpynW(msg, L"登录失败：账号或密码错误", 256);
        free(detail);
    } else {
        wchar_t* token = resp ? JsonFind(resp, L"token") : NULL;
        if (token && token[0]) {
            lstrcpynW(g_token, token, 1024);
            wchar_t* nick = resp ? JsonFind(resp, L"nickname") : NULL;
            lstrcpynW(g_user, (nick && nick[0]) ? nick : job->user, 128);
            free(nick);
            SaveConfig();
            success = TRUE;
            msg = (wchar_t*)xmalloc(192 * sizeof(wchar_t));
            _snwprintf(msg, 192, L"已登录 %s", g_user);
        } else {
            msg = (wchar_t*)xmalloc(128 * sizeof(wchar_t));
            lstrcpynW(msg, L"登录失败：服务器返回异常", 128);
        }
        free(token);
    }
    free(resp);
    PostMessageW(g_main, WM_APP_LOGIN, (WPARAM)success, (LPARAM)msg);
    free(job);
    return 0;
}

static DWORD WINAPI CheckUpdateThread(LPVOID) {
    wchar_t* resp = ApiGetText(L"/api/desktop/version");
    wchar_t* msg = (wchar_t*)xmalloc(256 * sizeof(wchar_t));
    if (!resp) {
        lstrcpynW(msg, L"检查更新失败（无法连接服务器）", 256);
        PostMessageW(g_main, WM_APP_UPDINFO, 0, (LPARAM)msg);
        return 0;
    }
    wchar_t* ver = JsonFind(resp, L"version");
    if (ver && VersionNewer(ver)) {
        _snwprintf(msg, 256, L"%s|%s", ver, g_server);
        /* 弹窗在工作线程执行 (MessageBox 自带消息循环) */
        wchar_t cap[64];
        wchar_t text[256];
        _snwprintf(text, 256, L"发现新版本 v%s，是否立即下载更新？", ver);
        _snwprintf(cap, 64, L"FLA 更新");
        if (MessageBoxW(g_main, text, cap, MB_YESNO | MB_ICONINFORMATION) == IDYES) {
            wchar_t* du = JsonFind(resp, L"download_url");
            if (du) {
                wchar_t* full = wcat2(g_server, du);
                wchar_t tmp[MAX_PATH];
                GetTempPathW(MAX_PATH, tmp);
                wchar_t file[MAX_PATH + 32];
                _snwprintf(file, MAX_PATH + 32, L"%sFLA_update.exe", tmp);
                if (DownloadToFile(full, NULL, file)) {
                    wchar_t exe[MAX_PATH];
                    GetModuleFileNameW(NULL, exe, MAX_PATH);
                    wchar_t old[MAX_PATH + 16];
                    _snwprintf(old, MAX_PATH + 16, L"%s.old", exe);
                    DeleteFileW(old);
                    MoveFileExW(exe, old, MOVEFILE_REPLACE_EXISTING);
                    if (MoveFileExW(file, exe, MOVEFILE_REPLACE_EXISTING | MOVEFILE_COPY_ALLOWED)) {
                        SHELLEXECUTEINFOW sei;
                        ZeroMemory(&sei, sizeof(sei));
                        sei.cbSize = sizeof(sei);
                        sei.lpVerb = L"open";
                        sei.lpFile = exe;
                        sei.nShow = SW_SHOWNORMAL;
                        ShellExecuteExW(&sei);
                        ExitProcess(0);
                    }
                }
                free(full);
                MessageBoxW(g_main, L"更新下载失败，请到官网重新下载", L"FLA 更新", MB_OK | MB_ICONWARNING);
            }
        }
        free(msg);
    } else if (ver) {
        lstrcpynW(msg, L"已是最新版本 ✓", 256);
        PostMessageW(g_main, WM_APP_UPDINFO, 0, (LPARAM)msg);
    } else {
        lstrcpynW(msg, L"", 256);
        PostMessageW(g_main, WM_APP_UPDINFO, 0, (LPARAM)msg);
    }
    free(ver);
    free(resp);
    return 0;
}

static DWORD WINAPI HealthThread(LPVOID) {
    wchar_t* r = ApiGetText(L"/api/health");
    BOOL ok = r && wcsstr(r, L"\"ok\"") != NULL;
    free(r);
    PostMessageW(g_main, WM_APP_HEALTH, (WPARAM)ok, 0);
    return 0;
}

static void MainSwitchTab(int tab) {
    g_tab = tab;
    for (int i = 0; i < 4; i++) {
        BOOL active = (i == tab);
        BtnSetColors(g_nav[i], active ? C_INK : C_SUBTLE, active ? RGB(255, 255, 255) : C_MUT,
                     active ? C_INK3 : C_HOVER);
    }
    BOOL lib = (tab == 0), rem = (tab == 1), dck = (tab == 2), st = (tab == 3);
    ShowWindow(g_lib_list, lib ? SW_SHOW : SW_HIDE);
    ShowWindow(g_lib_refresh, lib ? SW_SHOW : SW_HIDE);
    ShowWindow(g_lib_web, lib ? SW_SHOW : SW_HIDE);
    ShowWindow(g_lib_tip, lib ? SW_SHOW : SW_HIDE);
    ShowWindow(g_rem_copy, rem ? SW_SHOW : SW_HIDE);
    ShowWindow(g_rem_open, rem ? SW_SHOW : SW_HIDE);
    ShowWindow(g_rem_status, rem ? SW_SHOW : SW_HIDE);
    ShowWindow(g_rem_info, rem ? SW_SHOW : SW_HIDE);
    ShowWindow(g_dock_show, dck ? SW_SHOW : SW_HIDE);
    ShowWindow(g_ck_seewo, dck ? SW_SHOW : SW_HIDE);
    ShowWindow(g_ed_url, st ? SW_SHOW : SW_HIDE);
    ShowWindow(g_ed_user, st ? SW_SHOW : SW_HIDE);
    ShowWindow(g_ed_pass, st ? SW_SHOW : SW_HIDE);
    ShowWindow(g_btn_login, st ? SW_SHOW : SW_HIDE);
    ShowWindow(g_btn_logout, st ? SW_SHOW : SW_HIDE);
    ShowWindow(g_btn_upd, st ? SW_SHOW : SW_HIDE);
    ShowWindow(g_lb_status, st ? SW_SHOW : SW_HIDE);
    ShowWindow(g_lb_ver, st ? SW_SHOW : SW_HIDE);
    InvalidateRect(g_main, NULL, TRUE);
}

static void MainLayout(void) {
    RECT r;
    GetClientRect(g_main, &r);
    int W = r.right, H = r.bottom;
    int cx = NAV_W + 26, cw = W - cx - 26;
    HDWP dp = BeginDeferWindowPos(32);
    int y0 = HDR_H + 12;
    DeferWindowPos(dp, g_lib_refresh, NULL, W - 26 - 84, y0 + 2, 84, 32, SWP_NOZORDER);
    DeferWindowPos(dp, g_lib_web, NULL, W - 26 - 204, y0 + 2, 110, 32, SWP_NOZORDER);
    DeferWindowPos(dp, g_lib_list, NULL, cx, y0 + 44, cw, H - y0 - 60, SWP_NOZORDER);
    DeferWindowPos(dp, g_lib_tip, NULL, cx, H / 2 - 40, cw, 48, SWP_NOZORDER);
    /* remote 页卡片内的控件 (卡片矩形在 WM_PAINT 里画) */
    DeferWindowPos(dp, g_rem_status, NULL, cx + 20, y0 + 118, cw - 40, 24, SWP_NOZORDER);
    DeferWindowPos(dp, g_rem_copy, NULL, cx + 20, y0 + 152, 120, 32, SWP_NOZORDER);
    DeferWindowPos(dp, g_rem_open, NULL, cx + 152, y0 + 152, 110, 32, SWP_NOZORDER);
    DeferWindowPos(dp, g_rem_info, NULL, cx + 20, y0 + 246, cw - 40, 130, SWP_NOZORDER);
    /* dockcfg */
    DeferWindowPos(dp, g_dock_show, NULL, cx + 20, y0 + 46, 190, 38, SWP_NOZORDER);
    DeferWindowPos(dp, g_ck_seewo, NULL, cx + 22, y0 + 104, 420, 26, SWP_NOZORDER);
    /* settings */
    DeferWindowPos(dp, g_ed_url, NULL, cx + 20, y0 + 48, 430, 30, SWP_NOZORDER);
    DeferWindowPos(dp, g_ed_user, NULL, cx + 20, y0 + 112, 200, 30, SWP_NOZORDER);
    DeferWindowPos(dp, g_ed_pass, NULL, cx + 240, y0 + 112, 210, 30, SWP_NOZORDER);
    DeferWindowPos(dp, g_lb_status, NULL, cx + 20, y0 + 156, cw - 40, 24, SWP_NOZORDER);
    DeferWindowPos(dp, g_btn_login, NULL, cx + 20, y0 + 190, 130, 36, SWP_NOZORDER);
    DeferWindowPos(dp, g_btn_logout, NULL, cx + 160, y0 + 190, 100, 36, SWP_NOZORDER);
    DeferWindowPos(dp, g_btn_upd, NULL, cx + 270, y0 + 190, 100, 36, SWP_NOZORDER);
    DeferWindowPos(dp, g_lb_ver, NULL, cx + 20, y0 + 246, cw - 40, 24, SWP_NOZORDER);
    /* header 按钮 */
    DeferWindowPos(dp, g_hdr_hide, NULL, W - 120, 14, 96, 36, SWP_NOZORDER);
    DeferWindowPos(dp, g_hdr_dock, NULL, W - 252, 14, 120, 36, SWP_NOZORDER);
    EndDeferWindowPos(dp);
}

static LRESULT CALLBACK MainProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC dc = BeginPaint(h, &ps);
            RECT r;
            GetClientRect(h, &r);
            HDC mem = CreateCompatibleDC(dc);
            HBITMAP bmp = CreateCompatibleBitmap(dc, r.right, r.bottom);
            HGDIOBJ ob = SelectObject(mem, bmp);
            /* 白底 */
            HBRUSH white = CreateSolidBrush(RGB(255, 255, 255));
            FillRect(mem, &r, white);
            DeleteObject(white);
            /* 黑色顶栏 */
            RECT hr = { 0, 0, r.right, HDR_H };
            HBRUSH ink = CreateSolidBrush(C_INK);
            FillRect(mem, &hr, ink);
            SetBkMode(mem, TRANSPARENT);
            SelectObject(mem, g_fontH1);
            SetTextColor(mem, RGB(255, 255, 255));
            TextOutW(mem, 24, 12, L"FLA", 3);
            SelectObject(mem, g_fontS);
            SetTextColor(mem, C_MUT2);
            wchar_t sub[128];
            _snwprintf(sub, 128, L"智慧课堂桌面助手 · v%s", FLA_VERSION);
            TextOutW(mem, 96, 26, sub, lstrlenW(sub));
            /* 侧栏底色 */
            RECT nr = { 0, HDR_H, NAV_W, r.bottom };
            HBRUSH subtle = CreateSolidBrush(C_SUBTLE);
            FillRect(mem, &nr, subtle);
            /* 各页标题与小字 */
            SelectObject(mem, g_fontB);
            SetTextColor(mem, C_INK);
            wchar_t h1[64] = L"";
            wchar_t tip[160] = L"";
            if (g_tab == 0) { lstrcpynW(h1, L"我的课件库 · 一键本地原生放映", 64); lstrcpynW(tip, L"双击课件调用本机 PowerPoint / WPS 全屏放映，自动挂接悬浮工具盒与随页板书。", 160); }
            else if (g_tab == 1) { lstrcpynW(h1, L"手机投屏与扫码遥控", 64); lstrcpynW(tip, L"手机变成激光笔 + 翻页器 + 掌上触控板，与大屏放映毫秒级同步。", 160); }
            else if (g_tab == 2) { lstrcpynW(h1, L"悬浮工具盒", 64); lstrcpynW(tip, L"放映时的画笔、激光、翻页与课堂工具都收纳在屏幕底部的悬浮盒中。", 160); }
            else { lstrcpynW(h1, L"账号与服务设置", 64); lstrcpynW(tip, L"登录 FLA 账号后，桌面端即可同步云端课件库并检查更新。", 160); }
            TextOutW(mem, NAV_W + 26, HDR_H + 16, h1, lstrlenW(h1));
            SelectObject(mem, g_fontS);
            SetTextColor(mem, C_MUT);
            TextOutW(mem, NAV_W + 28, HDR_H + 44, tip, lstrlenW(tip));
            /* 卡片 (remote / dockcfg / settings) */
            if (g_tab >= 1) {
                int cx = NAV_W + 26, cw = r.right - cx - 26;
                int y0 = HDR_H + 12;
                int cardH = (g_tab == 1) ? 236 : 300;
                RECT cr = { cx, y0 + 36, cx + cw, y0 + 36 + cardH };
                HBRUSH w2 = CreateSolidBrush(RGB(255, 255, 255));
                FillRect(mem, &cr, w2);
                DeleteObject(w2);
                HPEN lp = CreatePen(PS_SOLID, 1, C_LINE);
                HGDIOBJ op = SelectObject(mem, lp);
                RoundRect(mem, cr.left, cr.top, cr.right, cr.bottom, 24, 24);
                SelectObject(mem, op);
                DeleteObject(lp);
                /* 卡片内标题 */
                SelectObject(mem, g_fontB);
                SetTextColor(mem, C_INK);
                wchar_t ct[64];
                if (g_tab == 1) lstrcpynW(ct, L"连接信息", 64);
                else if (g_tab == 2) lstrcpynW(ct, L"悬浮盒", 64);
                else lstrcpynW(ct, L"账号", 64);
                TextOutW(mem, cx + 20, y0 + 52, ct, lstrlenW(ct));
                if (g_tab == 1) {
                    SelectObject(mem, g_fontS);
                    SetTextColor(mem, C_INK);
                    wchar_t l1[600];
                    _snwprintf(l1, 600, L"服务地址  %s", g_server);
                    TextOutW(mem, cx + 20, y0 + 88, l1, lstrlenW(l1));
                    SetTextColor(mem, C_MUT);
                    TextOutW(mem, cx + 20, y0 + 112, L"本机桥接  127.0.0.1:8307", 24);
                } else if (g_tab == 2) {
                    SelectObject(mem, g_fontS);
                    SetTextColor(mem, C_MUT);
                    TextOutW(mem, cx + 22, y0 + 150, L"小技巧：悬浮盒可拖动；Esc 可退出画笔模式回到鼠标。", 50);
                } else {
                    SelectObject(mem, g_fontS);
                    SetTextColor(mem, C_MUT);
                    TextOutW(mem, cx + 20, y0 + 88, L"FLA 服务地址", 12);
                    TextOutW(mem, cx + 20, y0 + 92 + 30, L"账号", 4);
                    TextOutW(mem, cx + 240, y0 + 92 + 30, L"密码", 4);
                }
            }
            BitBlt(dc, 0, 0, r.right, r.bottom, mem, 0, 0, SRCCOPY);
            SelectObject(mem, ob);
            DeleteObject(bmp);
            DeleteDC(mem);
            EndPaint(h, &ps);
            return 0;
        }
        case WM_ERASEBKGND:
            return 1;
        case WM_SIZE:
            MainLayout();
            return 0;
        case WM_COMMAND: {
            int id = LOWORD(w);
            if (id >= 100 && id <= 103) { MainSwitchTab(id - 100); return 0; }
            switch (id) {
                case 200: /* 唤起悬浮盒 */
                    DockShow();
                    if (!IsWindowVisible(g_overlay)) ShowWindow(g_overlay, SW_SHOW);
                    ShowWindow(h, SW_MINIMIZE);
                    ShowHud(L"悬浮工具盒已就绪");
                    return 0;
                case 201: /* 收起窗口 */
                    ShowWindow(h, SW_HIDE);
                    return 0;
                case 210: /* 刷新课件 */
                    if (!g_token[0]) { SetTip(L"尚未登录 — 请到「账号与设置」登录后同步云端课件库", C_MUT); return 0; }
                    SetTip(L"正在加载…", C_MUT);
                    RefreshLibrary();
                    return 0;
                case 211: /* 浏览器打开 */
                    ShellExecuteW(h, L"open", g_server, NULL, NULL, SW_SHOWNORMAL);
                    return 0;
                case 220: /* 复制服务地址 */
                    if (OpenClipboard(h)) {
                        EmptyClipboard();
                        char* u8 = wide_to_utf8(g_server);
                        int n = lstrlenA(u8) + 1;
                        HGLOBAL hg = GlobalAlloc(GMEM_MOVEABLE, n);
                        if (hg) {
                            CopyMemory(GlobalLock(hg), u8, n);
                            GlobalUnlock(hg);
                            SetClipboardData(CF_TEXT, hg);
                        }
                        CloseClipboard();
                        free(u8);
                        ShowHud(L"服务地址已复制");
                    }
                    return 0;
                case 221: /* 打开网页 */
                    ShellExecuteW(h, L"open", g_server, NULL, NULL, SW_SHOWNORMAL);
                    return 0;
                case 230: /* 展示悬浮盒 */
                    DockShow();
                    if (!IsWindowVisible(g_overlay)) ShowWindow(g_overlay, SW_SHOW);
                    return 0;
                case 300: { /* 登录 */
                    wchar_t url[512], user[128], pass[128];
                    GetWindowTextW(g_ed_url, url, 512);
                    GetWindowTextW(g_ed_user, user, 128);
                    GetWindowTextW(g_ed_pass, pass, 128);
                    if (!url[0] || !user[0] || !pass[0]) {
                        MessageBoxW(h, L"请填写服务地址、账号与密码", L"FLA", MB_OK | MB_ICONWARNING);
                        return 0;
                    }
                    /* 去尾部 / */
                    int n = lstrlenW(url);
                    while (n > 0 && url[n - 1] == L'/') url[--n] = 0;
                    lstrcpynW(g_server, url, 512);
                    BtnSetText(g_btn_login, L"登录中…");
                    EnableWindow(g_btn_login, FALSE);
                    LoginJob* job = (LoginJob*)xmalloc(sizeof(LoginJob));
                    lstrcpynW(job->user, user, 128);
                    lstrcpynW(job->pass, pass, 128);
                    CloseHandle(CreateThread(NULL, 0, LoginThread, job, 0, NULL));
                    return 0;
                }
                case 301: /* 退出登录 */
                    g_token[0] = 0;
                    g_user[0] = 0;
                    SaveConfig();
                    SetWindowTextW(g_lb_status, L"状态：未登录");
                    SetWindowTextW(g_ed_pass, L"");
                    return 0;
                case 302: /* 检查更新 */
                    BtnSetText(g_btn_upd, L"检查中…");
                    EnableWindow(g_btn_upd, FALSE);
                    CloseHandle(CreateThread(NULL, 0, CheckUpdateThread, NULL, 0, NULL));
                    return 0;
            }
            return 0;
        }
        case WM_APP_FILES: {
            wchar_t* json = (wchar_t*)l;
            if (!json) {
                SetTip(g_token[0] ? L"无法连接服务器，请检查「账号与设置」中的服务地址"
                                  : L"尚未登录 — 请到「账号与设置」登录 FLA 账号后同步云端课件库", C_DANGER);
                return 0;
            }
            FillFileList(json);
            free(json);
            return 0;
        }
        case WM_APP_LOGIN: {
            wchar_t* msg = (wchar_t*)l;
            EnableWindow(g_btn_login, TRUE);
            BtnSetText(g_btn_login, L"登录并保存");
            SetWindowTextW(g_lb_status, msg);
            free(msg);
            if (w) {
                SetWindowTextW(g_ed_url, g_server);
                RefreshLibrary();
            }
            return 0;
        }
        case WM_APP_HEALTH: {
            SetWindowTextW(g_rem_status, w ? L"● 服务器在线" : L"● 无法连接服务器");
            InvalidateRect(g_rem_status, NULL, TRUE);
            return 0;
        }
        case WM_APP_UPDINFO: {
            wchar_t* msg = (wchar_t*)l;
            EnableWindow(g_btn_upd, TRUE);
            BtnSetText(g_btn_upd, L"检查更新");
            if (msg && msg[0]) SetWindowTextW(g_lb_ver, msg);
            free(msg);
            return 0;
        }
        case WM_NOTIFY: {
            NMHDR* nm = (NMHDR*)l;
            if (nm->hwndFrom == g_lib_list && nm->code == NM_DBLCLK) {
                NMITEMACTIVATE* ia = (NMITEMACTIVATE*)l;
                if (ia->iItem >= 0 && ia->iItem < g_fileCount && g_fids && g_fids[ia->iItem]) {
                    wchar_t* path = (wchar_t*)xmalloc(640 * sizeof(wchar_t));
                    _snwprintf(path, 640, L"/api/files/%s/download", g_fids[ia->iItem]);
                    wchar_t* url = wcat2(g_server, path);
                    LaunchOfficePresentation(url, g_fnames[ia->iItem], g_token);
                    free(url);
                    free(path);
                }
            }
            return 0;
        }
        case WM_CTLCOLORSTATIC: {
            HDC dc = (HDC)w;
            SetBkColor(dc, RGB(255, 255, 255));
            SetTextColor(dc, C_MUT);
            static HBRUSH wb = NULL;
            if (!wb) wb = CreateSolidBrush(RGB(255, 255, 255));
            return (LRESULT)wb;
        }
        case WM_CTLCOLOREDIT: {
            HDC dc = (HDC)w;
            SetBkColor(dc, RGB(255, 255, 255));
            SetTextColor(dc, C_INK);
            static HBRUSH wb2 = NULL;
            if (!wb2) wb2 = CreateSolidBrush(RGB(255, 255, 255));
            return (LRESULT)wb2;
        }
        case WM_DESTROY:
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

static void CreateMainWindow(void) {
    WNDCLASSW wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc = MainProc;
    wc.hInstance = g_hInst;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.lpszClassName = L"FLAMAIN";
    RegisterClassW(&wc);

    g_main = CreateWindowExW(0, L"FLAMAIN", L"FLA 智慧课堂桌面助手 · v" FLA_VERSION,
                             WS_OVERLAPPEDWINDOW,
                             CW_USEDEFAULT, CW_USEDEFAULT, 1024, 700,
                             NULL, NULL, g_hInst, NULL);

    /* 导航 */
    const wchar_t* navTxt[4] = { L"课件工作台", L"手机投屏遥控", L"悬浮盒设置", L"账号与设置" };
    for (int i = 0; i < 4; i++) {
        RECT rc = { 14, HDR_H + 18 + i * 50, 14 + 180, HDR_H + 18 + i * 50 + 42 };
        g_nav[i] = NewBtn(g_main, navTxt[i], 100 + i, rc, C_SUBTLE, C_MUT, C_HOVER, C_SUBTLE, 10, IC_NONE, g_fontB, TRUE);
    }
    /* 顶栏按钮 */
    RECT rc1 = { 700, 14, 796, 50 };
    g_hdr_hide = NewBtn(g_main, L"收起窗口", 201, rc1, C_INK2, RGB(255, 255, 255), C_INK3, C_INK, 18, IC_NONE, g_font, FALSE);
    RECT rc2 = { 568, 14, 688, 50 };
    g_hdr_dock = NewBtn(g_main, L"唤起悬浮盒", 200, rc2, RGB(255, 255, 255), C_INK, C_LINE, C_INK, 18, IC_NONE, g_fontB, FALSE);
    /* library */
    RECT rc = { 700, 80, 784, 112 };
    g_lib_refresh = NewBtn(g_main, L"刷新", 210, rc, C_SUBTLE, C_INK, C_HOVER, RGB(255, 255, 255), 10, IC_NONE, g_font, FALSE);
    RECT rc3 = { 580, 80, 690, 112 };
    g_lib_web = NewBtn(g_main, L"在浏览器打开", 211, rc3, C_INK, RGB(255, 255, 255), C_INK3, RGB(255, 255, 255), 10, IC_NONE, g_font, FALSE);
    g_lib_list = CreateWindowExW(WS_EX_CLIENTEDGE, WC_LISTVIEWW, NULL,
                                 WS_CHILD | WS_VISIBLE | LVS_REPORT | LVS_SHOWSELALWAYS,
                                 234, 120, 700, 480, g_main, NULL, g_hInst, NULL);
    ListView_SetExtendedListViewStyle(g_lib_list, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER);
    LVCOLUMNW col;
    ZeroMemory(&col, sizeof(col));
    col.mask = LVCF_TEXT | LVCF_WIDTH;
    col.pszText = (wchar_t*)L"课件名称"; col.cx = 360;
    ListView_InsertColumn(g_lib_list, 0, &col);
    col.pszText = (wchar_t*)L"类型"; col.cx = 80;
    ListView_InsertColumn(g_lib_list, 1, &col);
    col.pszText = (wchar_t*)L"页数"; col.cx = 80;
    ListView_InsertColumn(g_lib_list, 2, &col);
    col.pszText = (wchar_t*)L"体积"; col.cx = 100;
    ListView_InsertColumn(g_lib_list, 3, &col);
    col.pszText = (wchar_t*)L"上传日期"; col.cx = 130;
    ListView_InsertColumn(g_lib_list, 4, &col);
    ListView_SetBkColor(g_lib_list, RGB(255, 255, 255));
    ListView_SetTextBkColor(g_lib_list, RGB(255, 255, 255));
    SendMessageW(g_lib_list, WM_SETFONT, (WPARAM)g_font, TRUE);
    g_lib_tip = CreateWindowExW(0, L"STATIC", L"", WS_CHILD | SS_CENTER,
                                234, 320, 700, 48, g_main, NULL, g_hInst, NULL);
    SendMessageW(g_lib_tip, WM_SETFONT, (WPARAM)g_font, TRUE);
    /* remote */
    RECT rc4 = { 254, 260, 374, 292 };
    g_rem_copy = NewBtn(g_main, L"复制服务地址", 220, rc4, C_SUBTLE, C_INK, C_HOVER, RGB(255, 255, 255), 10, IC_NONE, g_font, FALSE);
    RECT rc5 = { 386, 260, 496, 292 };
    g_rem_open = NewBtn(g_main, L"打开网页", 221, rc5, C_INK, RGB(255, 255, 255), C_INK3, RGB(255, 255, 255), 10, IC_NONE, g_font, FALSE);
    g_rem_status = CreateWindowExW(0, L"STATIC", L"● 检测中…", WS_CHILD,
                                   254, 232, 420, 24, g_main, NULL, g_hInst, NULL);
    SendMessageW(g_rem_status, WM_SETFONT, (WPARAM)g_fontB, TRUE);
    g_rem_info = CreateWindowExW(0, L"STATIC",
                                 L"使用方法：\r\n① 电脑端打开课件放映（悬浮盒自动出现）\r\n② 点击悬浮盒上的「手机」图标\r\n③ 手机浏览器打开服务地址，进入「手机遥控」\r\n④ 扫码或输入 4 位配对码，即连即用",
                                 WS_CHILD, 254, 356, 460, 130, g_main, NULL, g_hInst, NULL);
    SendMessageW(g_rem_info, WM_SETFONT, (WPARAM)g_font, TRUE);
    /* dockcfg */
    RECT rc6 = { 254, 178, 444, 216 };
    g_dock_show = NewBtn(g_main, L"展示悬浮盒与板书画布", 230, rc6, C_INK, RGB(255, 255, 255), C_INK3, RGB(255, 255, 255), 10, IC_NONE, g_fontB, FALSE);
    g_ck_seewo = CreateWindowExW(0, L"BUTTON", L"放映时自动压制希沃白板5侧边栏 (EasiNote)",
                                 WS_CHILD | BS_AUTOCHECKBOX, 256, 236, 420, 26,
                                 g_main, NULL, g_hInst, NULL);
    SendMessageW(g_ck_seewo, WM_SETFONT, (WPARAM)g_font, TRUE);
    SendMessageW(g_ck_seewo, BM_SETCHECK, g_seewo ? BST_CHECKED : BST_UNCHECKED, 0);
    /* settings */
    g_ed_url = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", g_server, WS_CHILD | ES_AUTOHSCROLL,
                               254, 140, 430, 30, g_main, NULL, g_hInst, NULL);
    g_ed_user = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", g_user, WS_CHILD | ES_AUTOHSCROLL,
                                254, 204, 200, 30, g_main, NULL, g_hInst, NULL);
    g_ed_pass = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", L"", WS_CHILD | ES_PASSWORD | ES_AUTOHSCROLL,
                                464, 204, 210, 30, g_main, NULL, g_hInst, NULL);
    SendMessageW(g_ed_url, WM_SETFONT, (WPARAM)g_font, TRUE);
    SendMessageW(g_ed_user, WM_SETFONT, (WPARAM)g_font, TRUE);
    SendMessageW(g_ed_pass, WM_SETFONT, (WPARAM)g_font, TRUE);
    RECT rc7 = { 254, 248, 384, 284 };
    g_btn_login = NewBtn(g_main, L"登录并保存", 300, rc7, C_INK, RGB(255, 255, 255), C_INK3, RGB(255, 255, 255), 10, IC_NONE, g_fontB, FALSE);
    RECT rc8 = { 394, 248, 494, 284 };
    g_btn_logout = NewBtn(g_main, L"退出登录", 301, rc8, C_SUBTLE, C_DANGER, C_HOVER, RGB(255, 255, 255), 10, IC_NONE, g_font, FALSE);
    RECT rc9 = { 504, 248, 604, 284 };
    g_btn_upd = NewBtn(g_main, L"检查更新", 302, rc9, C_SUBTLE, C_INK, C_HOVER, RGB(255, 255, 255), 10, IC_NONE, g_font, FALSE);
    wchar_t stat[192];
    if (g_token[0]) _snwprintf(stat, 192, L"状态：已登录（%s）", g_user[0] ? g_user : L"已保存凭证");
    else lstrcpynW(stat, L"状态：未登录", 192);
    g_lb_status = CreateWindowExW(0, L"STATIC", stat, WS_CHILD, 254, 292, 460, 24, g_main, NULL, g_hInst, NULL);
    SendMessageW(g_lb_status, WM_SETFONT, (WPARAM)g_font, TRUE);
    g_lb_ver = CreateWindowExW(0, L"STATIC", L"", WS_CHILD, 254, 330, 460, 24, g_main, NULL, g_hInst, NULL);
    SendMessageW(g_lb_ver, WM_SETFONT, (WPARAM)g_fontS, TRUE);

    MainLayout();
    MainSwitchTab(0);
    if (!g_token[0]) SetTip(L"尚未登录 — 请到「账号与设置」登录 FLA 账号后同步云端课件库", C_MUT);
    /* 显示窗口后再拉一次课件列表 (布局已就绪) */
    RefreshLibrary();
}
#pragma endregion
