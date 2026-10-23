/* FLA.exe 启动器 (单文件发行)
 * exe 结构: [本启动器] [zip 运行环境: 官方签名 Python 3.11 + PyQt5 + qfluentwidgets + FLA 程序] [8B 偏移][8B "FLAPAYLD"]
 * 首次运行把 zip 解压到 %LOCALAPPDATA%\FLA\runtime\<版本>-<大小>\ (Win10+ 自带 tar.exe), 以后秒开.
 * 然后用 pythonw.exe 启动 FLA, 自身立即退出 (方便自更新替换 FLA.exe). */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

#ifndef FLA_VERSION
#define FLA_VERSION L"3.6.0"
#endif

static wchar_t g_self[MAX_PATH], g_rt[MAX_PATH], g_err[512];
static const wchar_t* ArgsAfterExe(const wchar_t* cl);
static unsigned long long g_off, g_size;

static void Fail(const wchar_t* msg) {
    MessageBoxW(NULL, msg, L"FLA 课堂助手", MB_OK | MB_ICONERROR);
    ExitProcess(1);
}


/* ---------------------------------------------------------------- 单实例
 * 双击多次时只允许一个 FLA 进程: 后来者把参数交给已运行的实例 (命名事件唤醒) 后静默退出。
 * 用命名互斥体而不是 "端口占用" 判断: 进程崩溃/被强杀时内核自动释放, 不会卡死在 "打不开" 状态。
 * 互斥体/事件名必须与 fla_qt/single.py 保持一致。 */
#define FLA_MUTEX_NAME L"Local\\FLA.SingleInstance.v1"
#define FLA_EVENT_NAME L"Local\\FLA.WakeEvent.v1"

static void PendingPath(wchar_t* out) {
    wchar_t lad[MAX_PATH];
    if (!GetEnvironmentVariableW(L"LOCALAPPDATA", lad, MAX_PATH)) GetTempPathW(MAX_PATH, lad);
    _snwprintf(out, MAX_PATH, L"%s\\FLA", lad);
    CreateDirectoryW(out, NULL);
    _snwprintf(out, MAX_PATH, L"%s\\FLA\\pending.json", lad);
}

/* 把命令行参数写成 {"args":["..."]} —— 只做最小的 JSON 转义 (反斜杠/引号/控制字符) */
static void SavePendingArgs(const wchar_t* args) {
    if (!args || !*args) return;
    wchar_t path[MAX_PATH];
    PendingPath(path);

    int need = WideCharToMultiByte(CP_UTF8, 0, args, -1, NULL, 0, NULL, NULL);
    if (need <= 1) return;
    char* utf8 = (char*)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)need * 6 + 32);
    if (!utf8) return;
    WideCharToMultiByte(CP_UTF8, 0, args, -1, utf8, need, NULL, NULL);

    char* out = (char*)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)need * 6 + 64);
    if (out) {
        char* w = out;
        memcpy(w, "{\"args\":[\"", 10); w += 10;   /* 不含结尾 NUL */
        for (char* r = utf8; *r; r++) {
            unsigned char c = (unsigned char)*r;
            if (c == '"' || c == '\\') { *w++ = '\\'; *w++ = (char)c; }
            else if (c == '\n') { *w++ = '\\'; *w++ = 'n'; }
            else if (c == '\r') { *w++ = '\\'; *w++ = 'r'; }
            else if (c == '\t') { *w++ = '\\'; *w++ = 't'; }
            else if (c < 0x20) { w += sprintf(w, "\\u%04x", c); }
            else *w++ = (char)c;
        }
        memcpy(w, "\"],\"ts\":0}", 10); w += 10;
        HANDLE f = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (f != INVALID_HANDLE_VALUE) {
            DWORD wr = 0;
            WriteFile(f, out, (DWORD)(w - out), &wr, NULL);
            CloseHandle(f);
        }
        HeapFree(GetProcessHeap(), 0, out);
    }
    HeapFree(GetProcessHeap(), 0, utf8);
}

/* 已有实例在跑 → 唤醒它。返回 TRUE 表示已把请求交出去 (调用方应立刻退出)。 */
static BOOL WakeExistingInstance(const wchar_t* args) {
    SavePendingArgs(args);
    HANDLE ev = OpenEventW(EVENT_MODIFY_STATE, FALSE, FLA_EVENT_NAME);
    if (!ev) return FALSE;
    SetEvent(ev);
    CloseHandle(ev);
    return TRUE;
}

static BOOL ReadTrailer(void) {
    HANDLE f = CreateFileW(g_self, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return FALSE;
    LARGE_INTEGER sz; GetFileSizeEx(f, &sz);
    LARGE_INTEGER pos; pos.QuadPart = sz.QuadPart - 16;
    unsigned char t[16]; DWORD rd = 0;
    BOOL ok = SetFilePointerEx(f, pos, NULL, FILE_BEGIN) && ReadFile(f, t, 16, &rd, NULL) && rd == 16 &&
              memcmp(t + 8, "FLAPAYLD", 8) == 0;
    if (ok) {
        memcpy(&g_off, t, 8);
        g_size = (unsigned long long)sz.QuadPart - 16 - g_off;
    }
    CloseHandle(f);
    return ok && g_off > 0 && g_size > 0;
}

static BOOL Exists(const wchar_t* p) { return GetFileAttributesW(p) != INVALID_FILE_ATTRIBUTES; }

static DWORD RunWait(wchar_t* cmd) {
    STARTUPINFOW si; PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si)); si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW; si.wShowWindow = SW_HIDE;
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) return 9999;
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1; GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return code;
}

static void RemoveTree(const wchar_t* dir) {
    wchar_t cmd[MAX_PATH * 2];
    _snwprintf(cmd, MAX_PATH * 2, L"cmd.exe /c rmdir /s /q \"%s\"", dir);
    RunWait(cmd);
}

static DWORD WINAPI ExtractThread(LPVOID p) {
    (void)p;
    wchar_t tmpdir[MAX_PATH], zip[MAX_PATH], stage[MAX_PATH], cmd[MAX_PATH * 4], sys[MAX_PATH];
    GetTempPathW(MAX_PATH, tmpdir);
    _snwprintf(zip, MAX_PATH, L"%sFLA_runtime_%lu.zip", tmpdir, GetCurrentProcessId());
    _snwprintf(stage, MAX_PATH, L"%s.tmp%lu", g_rt, GetCurrentProcessId());

    /* 1. 抽出 zip */
    HANDLE in = CreateFileW(g_self, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, 0, NULL);
    HANDLE out = CreateFileW(zip, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, NULL);
    if (in == INVALID_HANDLE_VALUE || out == INVALID_HANDLE_VALUE) { lstrcpyW(g_err, L"无法写入临时目录"); return 1; }
    LARGE_INTEGER pos; pos.QuadPart = (LONGLONG)g_off;
    SetFilePointerEx(in, pos, NULL, FILE_BEGIN);
    static char buf[1 << 20];
    unsigned long long left = g_size;
    while (left) {
        DWORD want = left > sizeof(buf) ? sizeof(buf) : (DWORD)left, rd = 0, wr = 0;
        if (!ReadFile(in, buf, want, &rd, NULL) || rd == 0 || !WriteFile(out, buf, rd, &wr, NULL) || wr != rd) {
            CloseHandle(in); CloseHandle(out); DeleteFileW(zip);
            lstrcpyW(g_err, L"读取安装包失败 (磁盘空间不足或文件损坏)"); return 1;
        }
        left -= rd;
    }
    CloseHandle(in); CloseHandle(out);

    /* 2. 解压: 优先系统 tar.exe (Win10 1803+), 失败退回 PowerShell */
    RemoveTree(stage);
    CreateDirectoryW(stage, NULL);
    GetSystemDirectoryW(sys, MAX_PATH);
    _snwprintf(cmd, MAX_PATH * 4, L"\"%s\\tar.exe\" -xf \"%s\" -C \"%s\"", sys, zip, stage);
    DWORD rc = RunWait(cmd);
    if (rc != 0) {
        RemoveTree(stage);
        CreateDirectoryW(stage, NULL);
        _snwprintf(cmd, MAX_PATH * 4,
                   L"powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "
                   L"\"Expand-Archive -LiteralPath '%s' -DestinationPath '%s' -Force\"", zip, stage);
        rc = RunWait(cmd);
    }
    DeleteFileW(zip);
    wchar_t probe[MAX_PATH];
    _snwprintf(probe, MAX_PATH, L"%s\\python\\pythonw.exe", stage);
    if (rc != 0 || !Exists(probe)) {
        RemoveTree(stage);
        _snwprintf(g_err, 512, L"解压运行环境失败 (代码 %lu)。\n请确认磁盘空间充足，并检查杀毒软件是否拦截。", rc);
        return 1;
    }
    /* 3. 原子落位 + 完成标记 */
    RemoveTree(g_rt);
    if (!MoveFileExW(stage, g_rt, MOVEFILE_REPLACE_EXISTING)) {
        RemoveTree(stage);
        lstrcpyW(g_err, L"无法写入运行环境目录"); return 1;
    }
    wchar_t ready[MAX_PATH];
    _snwprintf(ready, MAX_PATH, L"%s\\ready.txt", g_rt);
    HANDLE r = CreateFileW(ready, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    if (r != INVALID_HANDLE_VALUE) CloseHandle(r);
    return 0;
}

static LRESULT CALLBACK SplashProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_PAINT) {
        PAINTSTRUCT ps; HDC dc = BeginPaint(h, &ps);
        RECT rc; GetClientRect(h, &rc);
        FillRect(dc, &rc, (HBRUSH)GetStockObject(WHITE_BRUSH));
        HFONT f = CreateFontW(-18, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Microsoft YaHei UI");
        HGDIOBJ of = SelectObject(dc, f);
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, RGB(17, 17, 17));
        DrawTextW(dc, L"FLA 课堂助手\n首次启动，正在准备运行环境（约 10–30 秒）…", -1, &rc, DT_CENTER | DT_VCENTER | DT_WORDBREAK);
        SelectObject(dc, of); DeleteObject(f);
        EndPaint(h, &ps);
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

static void ExtractWithSplash(HINSTANCE hi) {
    WNDCLASSW wc; ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc = SplashProc; wc.hInstance = hi; wc.lpszClassName = L"FLASplash";
    wc.hCursor = LoadCursor(NULL, IDC_WAIT);
    RegisterClassW(&wc);
    int W = 460, H = 120;
    HWND h = CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, L"FLASplash", L"FLA", WS_POPUP | WS_BORDER,
                             (GetSystemMetrics(SM_CXSCREEN) - W) / 2, (GetSystemMetrics(SM_CYSCREEN) - H) / 2,
                             W, H, NULL, NULL, hi, NULL);
    ShowWindow(h, SW_SHOW); UpdateWindow(h);
    HANDLE th = CreateThread(NULL, 0, ExtractThread, NULL, 0, NULL);
    for (;;) {
        DWORD r = MsgWaitForMultipleObjects(1, &th, FALSE, INFINITE, QS_ALLINPUT);
        if (r == WAIT_OBJECT_0) break;
        MSG msg;
        while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }
    DWORD code = 1; GetExitCodeThread(th, &code); CloseHandle(th);
    DestroyWindow(h);
    if (code != 0) Fail(g_err);
}

static const wchar_t* ArgsAfterExe(const wchar_t* cl) {
    if (*cl == L'"') { cl++; while (*cl && *cl != L'"') cl++; if (*cl) cl++; }
    else { while (*cl && *cl != L' ' && *cl != L'\t') cl++; }
    while (*cl == L' ' || *cl == L'\t') cl++;
    return cl;
}

int WINAPI wWinMain(HINSTANCE hi, HINSTANCE prev, PWSTR cl, int show) {
    (void)prev; (void)cl; (void)show;
    GetModuleFileNameW(NULL, g_self, MAX_PATH);

    /* --- 严格单实例: 必须在解压/启动 python 之前判断, 否则连点会连开多个 --- */
    HANDLE mutex = CreateMutexW(NULL, FALSE, FLA_MUTEX_NAME);
    BOOL already = (mutex != NULL) && (GetLastError() == ERROR_ALREADY_EXISTS);
    if (already) {
        if (WakeExistingInstance(ArgsAfterExe(GetCommandLineW()))) {
            if (mutex) CloseHandle(mutex);
            return 0;                     /* 已唤起正在运行的 FLA, 自身静默退出 */
        }
        /* 事件打不开 (极老的版本在跑): 提示而不是再开一个 */
        if (mutex) CloseHandle(mutex);
        MessageBoxW(NULL, L"FLA 已经在运行中，请查看任务栏 / 屏幕右下角。", L"FLA 课堂助手",
                    MB_OK | MB_ICONINFORMATION);
        return 0;
    }
    /* mutex 句柄一直持有到进程退出, 由内核保证唯一性; already 分支已 CloseHandle */

    if (!ReadTrailer()) Fail(L"FLA.exe 文件不完整，请重新下载。");

    wchar_t lad[MAX_PATH], root[MAX_PATH];
    if (!GetEnvironmentVariableW(L"LOCALAPPDATA", lad, MAX_PATH)) GetTempPathW(MAX_PATH, lad);
    _snwprintf(root, MAX_PATH, L"%s\\FLA", lad); CreateDirectoryW(root, NULL);
    _snwprintf(root, MAX_PATH, L"%s\\FLA\\runtime", lad); CreateDirectoryW(root, NULL);
    _snwprintf(g_rt, MAX_PATH, L"%s\\%s-%llu", root, FLA_VERSION, g_size);

    wchar_t ready[MAX_PATH], pyw[MAX_PATH], script[MAX_PATH];
    _snwprintf(ready, MAX_PATH, L"%s\\ready.txt", g_rt);
    _snwprintf(pyw, MAX_PATH, L"%s\\python\\pythonw.exe", g_rt);
    _snwprintf(script, MAX_PATH, L"%s\\app\\fla_desktop.py", g_rt);
    if (!Exists(ready) || !Exists(pyw) || !Exists(script)) {
        HANDLE mx = CreateMutexW(NULL, FALSE, L"FLA_Runtime_Extract");
        WaitForSingleObject(mx, INFINITE);         /* 双击两次也只解压一次 */
        if (!Exists(ready) || !Exists(pyw) || !Exists(script)) ExtractWithSplash(hi);
        ReleaseMutex(mx); CloseHandle(mx);
    }

    SetEnvironmentVariableW(L"FLA_EXE", g_self);
    SetEnvironmentVariableW(L"FLA_RUNTIME", g_rt);
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
    SetEnvironmentVariableW(L"PYTHONDONTWRITEBYTECODE", NULL);

    const wchar_t* args = ArgsAfterExe(GetCommandLineW());
    size_t n = wcslen(pyw) + wcslen(script) + wcslen(args) + 16;
    wchar_t* cmd = (wchar_t*)HeapAlloc(GetProcessHeap(), 0, n * sizeof(wchar_t));
    _snwprintf(cmd, n, L"\"%s\" \"%s\" %s", pyw, script, args);
    STARTUPINFOW si; PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si)); si.cb = sizeof(si);
    wchar_t wd[MAX_PATH]; _snwprintf(wd, MAX_PATH, L"%s\\app", g_rt);
    if (!CreateProcessW(pyw, cmd, NULL, NULL, FALSE, 0, NULL, wd, &si, &pi)) {
        /* 运行环境被杀软删了? 清掉标记下次重解 */
        DeleteFileW(ready);
        Fail(L"启动失败：运行环境文件缺失（可能被杀毒软件误删）。\n请把 FLA 加入信任后重新打开。");
    }
    AllowSetForegroundWindow(pi.dwProcessId);
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return 0;
}
