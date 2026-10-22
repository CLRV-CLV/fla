/* ============================================================================
 * FLA Desktop 2.0 — 纯 Win32 原生客户端 (零运行时依赖)
 * 只使用 Windows 系统组件 (user32/gdi32/shell32/winhttp/ws2_32/comctl32/advapi32),
 * CRT 静态链接: 单文件 FLA.exe, 双击即跑, 无需 .NET / 无需安装任何环境。
 * 设计语言与 Web 端一致: 黑白极简 + 圆角卡片。
 * ==========================================================================*/
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601
#endif
#ifndef WINVER
#define WINVER 0x0601
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <tlhelp32.h>
#include <winhttp.h>
#include <commctrl.h>
#include <shellapi.h>
#include <wchar.h>
#include <wctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* MSVC 风格的免 tag 引用 */
typedef struct sockaddr sockaddr;
typedef struct sockaddr_in sockaddr_in;

#pragma region 主题与全局常量

#define FLA_VERSION L"2.0.0"          /* build 脚本从这里解析版本 (单一来源) */
#define FLA_PORT 8307
#define WM_APP_TOOL     (WM_APP+1)    /* wParam = ToolType */
#define WM_APP_SLIDE    (WM_APP+2)    /* wParam = +1/-1 */
#define WM_APP_BOARD    (WM_APP+3)
#define WM_APP_FILES    (WM_APP+4)    /* lParam = heap json utf16 */
#define WM_APP_LOGIN    (WM_APP+5)    /* wParam = ok, lParam = heap msg */
#define WM_APP_HEALTH   (WM_APP+6)    /* wParam = ok */
#define WM_APP_UPDINFO  (WM_APP+7)    /* lParam = heap msg (已最新/失败) */
#define WM_APP_UPDNEW   (WM_APP+8)    /* lParam = heap msg (发现新版本提示) */
#define WM_APP_CLEAR    (WM_APP+9)    /* 清空当前页板书 */
#define WM_APP_DOCKTOGGLE (WM_APP+10) /* 显示/隐藏悬浮盒 */
#define WM_APP_SYNC     (WM_APP+11)   /* 悬浮盒按钮高亮同步, wParam = tool */
#define WM_APP_STAGE    (WM_APP+12)   /* PPT 已拉起, 切换到演示舞台 */

static const COLORREF C_INK    = RGB(9,9,11);
static const COLORREF C_INK2   = RGB(24,24,27);
static const COLORREF C_INK3   = RGB(39,39,42);
static const COLORREF C_SUBTLE = RGB(244,244,245);
static const COLORREF C_HOVER  = RGB(235,235,237);
static const COLORREF C_LINE   = RGB(228,228,231);
static const COLORREF C_MUT    = RGB(113,113,122);
static const COLORREF C_MUT2   = RGB(161,161,170);
static const COLORREF C_DANGER = RGB(220,38,38);

static HINSTANCE g_hInst;
static HWND g_main, g_dock, g_overlay, g_hud, g_timer, g_palette;
static HFONT g_font, g_fontB, g_fontS, g_fontH1, g_fontMono, g_fontTiny;
static wchar_t g_server[512] = L"http://127.0.0.1:8306";
static wchar_t g_token[1024] = L"";
static wchar_t g_user[128] = L"";
static BOOL g_seewo = TRUE;
static BOOL g_running = TRUE;

/* 前置声明 (跨部件) */
static void OverlayApplyTool(int tool, BOOL announce);
static void OverlayToggleBoard(void);
static void OverlaySlide(int delta);
static void OverlayCreate(void);
static void DockShow(void);
static void DockCollapse(void);
static void DockExpand(void);
static void DockCreate(void);
static void DockToolsMenu(void);
static void DockTogglePalette(void);
static void DockSyncActive(int tool);
static void PaletteCreate(void);
static void TimerLaunch(void);
static int  OverlayCurrentTool(void);
static void OverlaySetPen(COLORREF c, int w);
static void ShowHud(const wchar_t* text);
static void MainSwitchTab(int tab);
static void LaunchOfficePresentation(const wchar_t* fileUrl, const wchar_t* name, const wchar_t* token);
static void GdiLine(HDC dc, int x1, int y1, int x2, int y2);
static void PaletteApply(int colorIdx, int widthIdx);
#pragma endregion

#pragma region 基础工具

static void* xmalloc(size_t n) { void* p = malloc(n ? n : 1); return p; }
static void* xrealloc(void* p, size_t n) { return realloc(p, n ? n : 1); }

/* 细线段 (图标/画笔共用) */
static void GdiLine(HDC dc, int x1, int y1, int x2, int y2) {
    MoveToEx(dc, x1, y1, NULL);
    LineTo(dc, x2, y2);
}

static wchar_t* utf8_to_wide(const char* s) {
    if (!s) { wchar_t* e = (wchar_t*)xmalloc(sizeof(wchar_t)); e[0] = 0; return e; }
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, NULL, 0);
    wchar_t* w = (wchar_t*)xmalloc((size_t)(n ? n : 1) * sizeof(wchar_t));
    MultiByteToWideChar(CP_UTF8, 0, s, -1, w, n);
    return w;
}
static char* wide_to_utf8(const wchar_t* s) {
    int n = WideCharToMultiByte(CP_UTF8, 0, s, -1, NULL, 0, NULL, NULL);
    char* b = (char*)xmalloc((size_t)(n ? n : 1));
    WideCharToMultiByte(CP_UTF8, 0, s, -1, b, n, NULL, NULL);
    return b;
}
static wchar_t* wcat2(const wchar_t* a, const wchar_t* b) {
    size_t la = lstrlenW(a), lb = lstrlenW(b);
    wchar_t* r = (wchar_t*)xmalloc((la + lb + 1) * sizeof(wchar_t));
    CopyMemory(r, a, la * sizeof(wchar_t));
    CopyMemory(r + la, b, (lb + 1) * sizeof(wchar_t));
    return r;
}
static void wappend(wchar_t** buf, size_t* len, size_t* cap, const wchar_t* s) {
    size_t sl = lstrlenW(s);
    if (*len + sl + 1 > *cap) {
        *cap = (*len + sl + 1) * 2;
        *buf = (wchar_t*)xrealloc(*buf, *cap * sizeof(wchar_t));
    }
    CopyMemory(*buf + *len, s, (sl + 1) * sizeof(wchar_t));
    *len += sl;
}
/* 最多一行的 int 转字符串 */
static wchar_t* itow(long v) {
    wchar_t* r = (wchar_t*)xmalloc(24 * sizeof(wchar_t));
    _snwprintf(r, 24, L"%ld", v);
    return r;
}

/* ---- 极简 JSON 值提取 (UTF-16 JSON; 支持 \" \\ \n 转义; 数字原样) ---- */
static wchar_t* JsonFind(const wchar_t* json, const wchar_t* key) {
    wchar_t pat[256];
    _snwprintf(pat, 256, L"\"%s\":", key);
    const wchar_t* p = json;
    size_t patLen = lstrlenW(pat);
    while ((p = wcsstr(p, pat)) != NULL) {
        const wchar_t* v = p + patLen;
        if (*v == L'"') {
            v++;
            size_t cap = 64, n = 0;
            wchar_t* out = (wchar_t*)xmalloc(cap * sizeof(wchar_t));
            while (*v && *v != L'"') {
                wchar_t c = *v;
                if (c == L'\\' && v[1]) {
                    v++;
                    switch (*v) {
                        case L'"': c = L'"'; break;
                        case L'\\': c = L'\\'; break;
                        case L'/': c = L'/'; break;
                        case L'n': c = L'\n'; break;
                        case L't': c = L'\t'; break;
                        case L'r': c = L'\r'; break;
                        default: c = *v; break;
                    }
                }
                if (n + 2 > cap) { cap *= 2; out = (wchar_t*)xrealloc(out, cap * sizeof(wchar_t)); }
                out[n++] = c;
                v++;
            }
            out[n] = 0;
            return out;
        } else {
            const wchar_t* e = v;
            while (*e && *e != L',' && *e != L'}' && *e != L']') e++;
            size_t n = (size_t)(e - v);
            wchar_t* out = (wchar_t*)xmalloc((n + 1) * sizeof(wchar_t));
            CopyMemory(out, v, n * sizeof(wchar_t));
            out[n] = 0;
            /* 去空格 */
            wchar_t* t = out; while (*t == L' ') t++;
            return t;
        }
    }
    return NULL;
}
/* 在 JSON 中枚举对象: 每次返回下一对象的 [start,end) 片段指针 */
typedef struct { const wchar_t* s; const wchar_t* e; } JsonSlice;
static BOOL JsonNextObject(const wchar_t** cursor, JsonSlice* out) {
    const wchar_t* p = *cursor;
    while (*p && *p != L'{') p++;
    if (!*p) return FALSE;
    int depth = 0; BOOL inStr = FALSE;
    const wchar_t* start = p;
    while (*p) {
        wchar_t c = *p;
        if (inStr) {
            if (c == L'\\') p++;
            else if (c == L'"') inStr = FALSE;
        } else {
            if (c == L'"') inStr = TRUE;
            else if (c == L'{') depth++;
            else if (c == L'}') { depth--; if (depth == 0) { out->s = start; out->e = p + 1; *cursor = p + 1; return TRUE; } }
        }
        p++;
    }
    return FALSE;
}
/* 对象片段内的 JsonFind */
static wchar_t* SliceFind(JsonSlice* sl, const wchar_t* key) {
    wchar_t* buf = (wchar_t*)xmalloc(((size_t)(sl->e - sl->s) + 1) * sizeof(wchar_t));
    CopyMemory(buf, sl->s, (size_t)(sl->e - sl->s) * sizeof(wchar_t));
    buf[sl->e - sl->s] = 0;
    wchar_t* r = JsonFind(buf, key);
    free(buf);
    return r;
}
static wchar_t* JsonEscape(const wchar_t* s) {
    size_t n = lstrlenW(s);
    wchar_t* out = (wchar_t*)xmalloc((n * 2 + 3) * sizeof(wchar_t));
    size_t j = 0;
    for (size_t i = 0; i < n; i++) {
        if (s[i] == L'"' || s[i] == L'\\') out[j++] = L'\\';
        out[j++] = s[i];
    }
    out[j] = 0;
    return out;
}
#pragma endregion

#pragma region HTTP (WinHTTP, 支持 https / 自签容错 / Bearer)

typedef struct { BYTE* data; int len; int status; } HttpResult;

static void HttpFree(HttpResult* r) { if (r->data) { free(r->data); r->data = NULL; } }

/* body==NULL → GET; 返回的 data 额外带一个 NUL (文本用) */
static BOOL HttpEx(const wchar_t* absUrl, const wchar_t* method, const char* body,
                   const wchar_t* bearer, HttpResult* out) {
    ZeroMemory(out, sizeof(*out));
    URL_COMPONENTS uc;
    ZeroMemory(&uc, sizeof(uc));
    uc.dwStructSize = sizeof(uc);
    wchar_t host[256] = L""; wchar_t path[2048] = L"";
    uc.lpszHostName = host; uc.dwHostNameLength = 256;
    uc.lpszUrlPath = path; uc.dwUrlPathLength = 2048;
    if (!WinHttpCrackUrl(absUrl, 0, 0, &uc)) return FALSE;

    BOOL https = (uc.nScheme == INTERNET_SCHEME_HTTPS);
    HINTERNET ses = WinHttpOpen(L"FLA-Desktop/" FLA_VERSION, WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!ses) return FALSE;
    WinHttpSetTimeouts(ses, 10000, 10000, 15000, 300000);
    HINTERNET con = WinHttpConnect(ses, host, uc.nPort, 0);
    HINTERNET req = NULL;
    BOOL ok = FALSE;
    if (con) {
        req = WinHttpOpenRequest(con, method, path[0] ? path : L"/", NULL,
                                 WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
                                 https ? WINHTTP_FLAG_SECURE : 0);
    }
    if (req) {
        if (https) {
            DWORD flags = SECURITY_FLAG_IGNORE_UNKNOWN_CA | SECURITY_FLAG_IGNORE_CERT_CN_INVALID |
                          SECURITY_FLAG_IGNORE_CERT_DATE_INVALID | SECURITY_FLAG_IGNORE_CERT_WRONG_USAGE;
            WinHttpSetOption(req, WINHTTP_OPTION_SECURITY_FLAGS, &flags, sizeof(flags));
        }
        wchar_t hdrs[1200];
        lstrcpynW(hdrs, L"Content-Type: application/json\r\n", 1200);
        if (bearer && bearer[0]) {
            wchar_t auth[1100];
            _snwprintf(auth, 1100, L"Authorization: Bearer %s\r\n", bearer);
            lstrcatW(hdrs, auth);
        }
        if (WinHttpSendRequest(req, hdrs, -1L, (LPVOID)body, body ? (DWORD)lstrlenA(body) : 0,
                               body ? (DWORD)lstrlenA(body) : 0, 0)) {
            if (WinHttpReceiveResponse(req, NULL)) {
                DWORD st = 0, stSize = sizeof(st);
                WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                    WINHTTP_HEADER_NAME_BY_INDEX, &st, &stSize, WINHTTP_NO_HEADER_INDEX);
                out->status = (int)st;
                size_t cap = 65536, len = 0;
                BYTE* buf = (BYTE*)xmalloc(cap);
                DWORD rd = 0;
                for (;;) {
                    if (len + 65536 > cap) { cap *= 2; buf = (BYTE*)xrealloc(buf, cap); }
                    if (!WinHttpReadData(req, buf + len, 65536, &rd) || rd == 0) break;
                    len += rd;
                }
                buf[len] = 0;
                if (cap < len + 1) buf = (BYTE*)xrealloc(buf, len + 1);
                out->data = buf; out->len = (int)len;
                ok = TRUE;
            }
        }
    }
    if (req) WinHttpCloseHandle(req);
    if (con) WinHttpCloseHandle(con);
    WinHttpCloseHandle(ses);
    return ok;
}

/* GET 并把响应转为 UTF-16 文本 (heap) ; 失败返回 NULL */
static wchar_t* ApiGetText(const wchar_t* path) {
    wchar_t* url = wcat2(g_server, path);
    HttpResult r;
    BOOL ok = HttpEx(url, L"GET", NULL, g_token, &r);
    free(url);
    if (!ok || !r.data || r.status >= 400) { HttpFree(&r); return NULL; }
    wchar_t* w = utf8_to_wide((const char*)r.data);
    HttpFree(&r);
    return w;
}
static BOOL ApiGetTextEx(const wchar_t* path, wchar_t** outText, int* outStatus) {
    wchar_t* url = wcat2(g_server, path);
    HttpResult r;
    BOOL ok = HttpEx(url, L"GET", NULL, g_token, &r);
    free(url);
    if (!ok || !r.data) return FALSE;
    if (outStatus) *outStatus = r.status;
    if (outText) { *outText = utf8_to_wide((const char*)r.data); }
    HttpFree(&r);
    return TRUE;
}
static BOOL ApiPostJson(const wchar_t* path, const wchar_t* jsonBodyUtf16, wchar_t** outText, int* outStatus) {
    char* body = wide_to_utf8(jsonBodyUtf16);
    wchar_t* url = wcat2(g_server, path);
    HttpResult r;
    BOOL ok = HttpEx(url, L"POST", body, g_token, &r);
    free(url); free(body);
    if (!ok || !r.data) return FALSE;
    if (outStatus) *outStatus = r.status;
    if (outText) *outText = utf8_to_wide((const char*)r.data);
    HttpFree(&r);
    return TRUE;
}
/* 下载到文件 (流式) */
static BOOL DownloadToFile(const wchar_t* absUrl, const wchar_t* bearer, const wchar_t* filePath) {
    URL_COMPONENTS uc;
    ZeroMemory(&uc, sizeof(uc));
    uc.dwStructSize = sizeof(uc);
    wchar_t host[256] = L""; wchar_t path[2048] = L"";
    uc.lpszHostName = host; uc.dwHostNameLength = 256;
    uc.lpszUrlPath = path; uc.dwUrlPathLength = 2048;
    if (!WinHttpCrackUrl(absUrl, 0, 0, &uc)) return FALSE;
    BOOL https = (uc.nScheme == INTERNET_SCHEME_HTTPS);
    HINTERNET ses = WinHttpOpen(L"FLA-Desktop/" FLA_VERSION, WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!ses) return FALSE;
    WinHttpSetTimeouts(ses, 10000, 10000, 15000, 600000);
    HINTERNET con = WinHttpConnect(ses, host, uc.nPort, 0);
    BOOL ok = FALSE;
    HINTERNET req = NULL;
    if (con) req = WinHttpOpenRequest(con, L"GET", path[0] ? path : L"/", NULL,
                                      WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
                                      https ? WINHTTP_FLAG_SECURE : 0);
    if (req) {
        if (https) {
            DWORD flags = SECURITY_FLAG_IGNORE_UNKNOWN_CA | SECURITY_FLAG_IGNORE_CERT_CN_INVALID |
                          SECURITY_FLAG_IGNORE_CERT_DATE_INVALID | SECURITY_FLAG_IGNORE_CERT_WRONG_USAGE;
            WinHttpSetOption(req, WINHTTP_OPTION_SECURITY_FLAGS, &flags, sizeof(flags));
        }
        wchar_t hdrs[1200] = L"";
        if (bearer && bearer[0]) _snwprintf(hdrs, 1200, L"Authorization: Bearer %s\r\n", bearer);
        if (WinHttpSendRequest(req, hdrs, -1L, WINHTTP_NO_REQUEST_DATA, 0, 0, 0) &&
            WinHttpReceiveResponse(req, NULL)) {
            DWORD st = 0, stSize = sizeof(st);
            WinHttpQueryHeaders(req, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                WINHTTP_HEADER_NAME_BY_INDEX, &st, &stSize, WINHTTP_NO_HEADER_INDEX);
            if (st == 200) {
                HANDLE f = CreateFileW(filePath, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
                if (f != INVALID_HANDLE_VALUE) {
                    BYTE buf[65536]; DWORD rd = 0, wr = 0;
                    ok = TRUE;
                    for (;;) {
                        if (!WinHttpReadData(req, buf, sizeof(buf), &rd) || rd == 0) break;
                        if (!WriteFile(f, buf, rd, &wr, NULL) || wr != rd) { ok = FALSE; break; }
                    }
                    CloseHandle(f);
                    if (!ok) DeleteFileW(filePath);
                }
            }
        }
    }
    if (req) WinHttpCloseHandle(req);
    if (con) WinHttpCloseHandle(con);
    WinHttpCloseHandle(ses);
    return ok;
}
#pragma endregion

#pragma region 配置 / 协议注册

static void ConfigDir(wchar_t* out, int cch, BOOL ensure) {
    wchar_t lad[MAX_PATH];
    GetEnvironmentVariableW(L"LOCALAPPDATA", lad, MAX_PATH);
    _snwprintf(out, cch, L"%s\\FLA", lad);
    if (ensure) CreateDirectoryW(out, NULL);
}
static void ConfigFile(wchar_t* out, int cch) {
    wchar_t dir[MAX_PATH];
    ConfigDir(dir, MAX_PATH, TRUE);
    _snwprintf(out, cch, L"%s\\config.ini", dir);
}
static void LoadConfig(void) {
    wchar_t path[MAX_PATH];
    ConfigFile(path, MAX_PATH);
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    LARGE_INTEGER sz;
    GetFileSizeEx(f, &sz);
    DWORD rd = 0;
    char* buf = (char*)xmalloc((size_t)sz.QuadPart + 1);
    ReadFile(f, buf, (DWORD)sz.QuadPart, &rd, NULL);
    buf[rd] = 0;
    CloseHandle(f);
    /* 简易 ini 解析 (UTF-8) */
    char* ctx = NULL;
    char* line = strtok_s(buf, "\r\n", &ctx);
    while (line) {
        if (strncmp(line, "server_url=", 11) == 0) {
            wchar_t* w = utf8_to_wide(line + 11);
            lstrcpynW(g_server, w, 512);
            free(w);
        } else if (strncmp(line, "token=", 6) == 0) {
            wchar_t* w = utf8_to_wide(line + 6);
            lstrcpynW(g_token, w, 1024);
            free(w);
        } else if (strncmp(line, "user=", 5) == 0) {
            wchar_t* w = utf8_to_wide(line + 5);
            lstrcpynW(g_user, w, 128);
            free(w);
        } else if (strncmp(line, "seewo=", 6) == 0) {
            g_seewo = (strncmp(line + 6, "1", 1) == 0);
        }
        line = strtok_s(NULL, "\r\n", &ctx);
    }
    free(buf);
}
static void SaveConfig(void) {
    wchar_t path[MAX_PATH];
    ConfigFile(path, MAX_PATH);
    char* su = wide_to_utf8(g_server);
    char* tk = wide_to_utf8(g_token);
    char* us = wide_to_utf8(g_user);
    char* buf = (char*)xmalloc(lstrlenA(su) + lstrlenA(tk) + lstrlenA(us) + 128);
    sprintf(buf, "server_url=%s\ntoken=%s\nuser=%s\nseewo=%d\n", su, tk, us, g_seewo ? 1 : 0);
    HANDLE f = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f != INVALID_HANDLE_VALUE) {
        DWORD wr;
        WriteFile(f, buf, (DWORD)lstrlenA(buf), &wr, NULL);
        CloseHandle(f);
    }
    free(su); free(tk); free(us); free(buf);
}
static void RegisterProtocol(void) {
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(NULL, exe, MAX_PATH);
    HKEY k;
    if (RegCreateKeyExW(HKEY_CURRENT_USER, L"Software\\Classes\\fla", 0, NULL, 0, KEY_WRITE, NULL, &k, NULL) == ERROR_SUCCESS) {
        RegSetValueExW(k, L"", 0, REG_SZ, (const BYTE*)L"URL:FLA Protocol", (DWORD)(lstrlenW(L"URL:FLA Protocol") + 1) * sizeof(wchar_t));
        RegSetValueExW(k, L"URL Protocol", 0, REG_SZ, (const BYTE*)L"", sizeof(wchar_t));
        RegCloseKey(k);
        HKEY ck;
        if (RegCreateKeyExW(HKEY_CURRENT_USER, L"Software\\Classes\\fla\\shell\\open\\command", 0, NULL, 0, KEY_WRITE, NULL, &ck, NULL) == ERROR_SUCCESS) {
            wchar_t cmd[MAX_PATH + 16];
            _snwprintf(cmd, MAX_PATH + 16, L"\"%s\" \"%%1\"", exe);
            RegSetValueExW(ck, L"", 0, REG_SZ, (const BYTE*)cmd, (DWORD)(lstrlenW(cmd) + 1) * sizeof(wchar_t));
            RegCloseKey(ck);
        }
    }
}
static BOOL VersionNewer(const wchar_t* remote) {
    int r1 = 0, r2 = 0, r3 = 0, c1 = 0, c2 = 0, c3 = 0;
    swscanf(remote, L"%d.%d.%d", &r1, &r2, &r3);
    swscanf(FLA_VERSION, L"%d.%d.%d", &c1, &c2, &c3);
    if (r1 != c1) return r1 > c1;
    if (r2 != c2) return r2 > c2;
    return r3 > c3;
}
#pragma endregion

#pragma region 本地桥接服务 (127.0.0.1:8307)

static void SendAll(SOCKET s, const char* buf, int len) {
    int off = 0;
    while (off < len) {
        int n = send(s, buf + off, len - off, 0);
        if (n <= 0) break;
        off += n;
    }
}
static void BridgeRespond(SOCKET s, const char* json) {
    char head[256];
    _snprintf(head, 256,
              "HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n"
              "Access-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
              "Access-Control-Allow-Headers: Content-Type\r\nContent-Length: %d\r\nConnection: close\r\n\r\n",
              lstrlenA(json));
    SendAll(s, head, lstrlenA(head));
    SendAll(s, json, lstrlenA(json));
}
/* UTF-8 body → utf16, 提取字段 */
static wchar_t* BodyValue(const char* body, const char* key) {
    char pat[128];
    _snprintf(pat, 128, "\"%s\":\"", key);
    const char* p = strstr(body, pat);
    if (!p) {
        _snprintf(pat, 128, "\"%s\": \"", key);
        p = strstr(body, pat);
        if (!p) return NULL;
    }
    p += lstrlenA(pat);
    const char* e = p;
    while (*e && *e != '"') { if (*e == '\\' && e[1]) e++; e++; }
    int n = (int)(e - p);
    char* tmp = (char*)xmalloc((size_t)n + 1);
    CopyMemory(tmp, p, (size_t)n);
    tmp[n] = 0;
    wchar_t* w = utf8_to_wide(tmp);
    free(tmp);
    return w;
}
static void ExecuteControl(const wchar_t* cmd) {
    if (!cmd || !cmd[0]) return;
    if (lstrcmpW(cmd, L"next") == 0) { keybd_event(VK_NEXT, 0, 0, 0); keybd_event(VK_NEXT, 0, KEYEVENTF_KEYUP, 0); }
    else if (lstrcmpW(cmd, L"prev") == 0) { keybd_event(VK_PRIOR, 0, 0, 0); keybd_event(VK_PRIOR, 0, KEYEVENTF_KEYUP, 0); }
    else if (lstrcmpW(cmd, L"first") == 0) { keybd_event(VK_HOME, 0, 0, 0); keybd_event(VK_HOME, 0, KEYEVENTF_KEYUP, 0); }
    else if (lstrcmpW(cmd, L"last") == 0) { keybd_event(VK_END, 0, 0, 0); keybd_event(VK_END, 0, KEYEVENTF_KEYUP, 0); }
    else if (lstrcmpW(cmd, L"black") == 0) { keybd_event('B', 0, 0, 0); keybd_event('B', 0, KEYEVENTF_KEYUP, 0); }
    else if (lstrcmpW(cmd, L"white") == 0) { keybd_event('W', 0, 0, 0); keybd_event('W', 0, KEYEVENTF_KEYUP, 0); }
    else if (lstrcmpW(cmd, L"laser") == 0) PostMessageW(g_overlay, WM_APP_TOOL, 2, 0);   /* Laser */
    else if (lstrcmpW(cmd, L"pen") == 0) PostMessageW(g_overlay, WM_APP_TOOL, 3, 0);
    else if (lstrcmpW(cmd, L"eraser") == 0) PostMessageW(g_overlay, WM_APP_TOOL, 5, 0);
    else if (lstrcmpW(cmd, L"clear") == 0) PostMessageW(g_overlay, WM_APP_CLEAR, 0, 0);
    else if (lstrcmpW(cmd, L"dock_toggle") == 0) PostMessageW(g_dock, WM_APP_DOCKTOGGLE, 0, 0);
}
static DWORD WINAPI BridgeConnThread(LPVOID param) {
    SOCKET s = (SOCKET)(UINT_PTR)param;
    char buf[8192];
    int n = 0, got = 0;
    /* 读请求头 */
    for (;;) {
        got = recv(s, buf + n, (int)(sizeof(buf) - 1 - n), 0);
        if (got <= 0) { closesocket(s); return 0; }
        n += got; buf[n] = 0;
        if (strstr(buf, "\r\n\r\n")) break;
        if (n > (int)sizeof(buf) - 2048) break;
    }
    char method[16] = {0}, path[512] = {0};
    sscanf(buf, "%15s %511s", method, path);
    /* 读 body (若有) */
    char* body = NULL;
    { /* Content-Length */
        char* h = buf;
        while (h && *h) {
            if (_strnicmp(h, "Content-Length:", 15) == 0) {
                int len = atoi(h + 15);
                if (len > 0) {
                    char* bodyStart = strstr(buf, "\r\n\r\n");
                    if (bodyStart) {
                        bodyStart += 4;
                        int have = n - (int)(bodyStart - buf);
                        char* bb = (char*)xmalloc((size_t)len + 1);
                        CopyMemory(bb, bodyStart, (size_t)(have < len ? have : len));
                        while (have < len) {
                            got = recv(s, bb + have, len - have, 0);
                            if (got <= 0) break;
                            have += got;
                        }
                        bb[len] = 0;
                        body = bb;
                    }
                }
                break;
            }
            h = strchr(h, '\n');
            if (h) h++;
        }
    }
    if (strcmp(method, "OPTIONS") == 0) {
        const char* resp = "HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\n"
                           "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
                           "Access-Control-Allow-Headers: Content-Type\r\nConnection: close\r\n\r\n";
        SendAll(s, resp, lstrlenA(resp));
    } else if (strcmp(path, "/api/status") == 0) {
        char out[192];
        char ver[32];
        char* v8 = wide_to_utf8(FLA_VERSION);
        lstrcpynA(ver, v8, 32); free(v8);
        _snprintf(out, 192, "{\"ok\":true,\"version\":\"%s\",\"status\":\"running\",\"service\":\"fla_desktop\"}", ver);
        BridgeRespond(s, out);
    } else if (strcmp(path, "/api/open") == 0 && body) {
        wchar_t* url = BodyValue(body, "url");
        wchar_t* name = BodyValue(body, "name");
        wchar_t* token = BodyValue(body, "token");
        if (url && url[0]) {
            wchar_t* name2 = name && name[0] ? name : (wchar_t*)xmalloc(64 * sizeof(wchar_t));
            if (!name || !name[0]) lstrcpynW(name2, L"presentation.pptx", 64);
            wchar_t* nameCopy = (wchar_t*)xmalloc((lstrlenW(name2) + 1) * sizeof(wchar_t));
            lstrcpynW(nameCopy, name2, lstrlenW(name2) + 1);
            wchar_t* urlCopy = (wchar_t*)xmalloc((lstrlenW(url) + 1) * sizeof(wchar_t));
            lstrcpynW(urlCopy, url, lstrlenW(url) + 1);
            wchar_t* tokCopy = (wchar_t*)xmalloc((lstrlenW(token ? token : L"") + 1) * sizeof(wchar_t));
            lstrcpynW(tokCopy, token ? token : L"", lstrlenW(token ? token : L"") + 1);
            /* 先应答再调起: 下载耗时不能拖垮网页端 1.2s 探测超时 */
            BridgeRespond(s, "{\"ok\":true,\"msg\":\"正在调起本地放映并激活工具盒\"}");
            /* 桥接线程本身就是工作线程, 直接同步执行下载+调起 */
            LaunchOfficePresentation(urlCopy, nameCopy, tokCopy[0] ? tokCopy : g_token);
            free(nameCopy); free(urlCopy); free(tokCopy);
            if (!name || !name[0]) free(name2);
        } else {
            BridgeRespond(s, "{\"ok\":false,\"msg\":\"缺少 url 参数\"}");
        }
        free(url); free(name); free(token);
    } else if (strcmp(path, "/api/control") == 0 && body) {
        wchar_t* cmd = BodyValue(body, "command");
        ExecuteControl(cmd);
        free(cmd);
        BridgeRespond(s, "{\"ok\":true}");
    } else {
        BridgeRespond(s, "{\"ok\":true}");
    }
    free(body);
    closesocket(s);
    return 0;
}
static DWORD WINAPI BridgeServerThread(LPVOID) {
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return 0;
    SOCKET ls = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (ls == INVALID_SOCKET) return 0;
    BOOL reuse = TRUE;
    setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, (char*)&reuse, sizeof(reuse));
    sockaddr_in addr;
    ZeroMemory(&addr, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(FLA_PORT);
    if (bind(ls, (sockaddr*)&addr, sizeof(addr)) == 0 && listen(ls, 8) == 0) {
        while (g_running) {
            sockaddr_in cli; int cl = sizeof(cli);
            SOCKET c = accept(ls, (sockaddr*)&cli, &cl);
            if (c == INVALID_SOCKET) break;
            HANDLE t = CreateThread(NULL, 0, BridgeConnThread, (LPVOID)(UINT_PTR)c, 0, NULL);
            if (t) CloseHandle(t);
        }
    }
    closesocket(ls);
    WSACleanup();
    return 0;
}
#pragma endregion

#pragma region 希沃白板压制线程

struct FindCtx { DWORD pid; HWND hwnd; };
typedef struct FindCtx FindCtx;
static BOOL CALLBACK HideEasiNoteEnum(HWND hwnd, LPARAM lp) {
    FindCtx* ctx = (FindCtx*)lp;
    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    if (pid == ctx->pid && IsWindowVisible(hwnd) && GetWindow(hwnd, GW_OWNER) == NULL) {
        wchar_t cls[64];
        if (GetClassNameW(hwnd, cls, 64) && lstrcmpW(cls, L"ApplicationFrameWindow") != 0) {
            ShowWindow(hwnd, SW_HIDE);
        }
    }
    return TRUE;
}
static DWORD WINAPI SeewoThread(LPVOID) {
    while (g_running) {
        if (g_seewo) {
            HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
            if (snap != INVALID_HANDLE_VALUE) {
                PROCESSENTRY32W pe;
                pe.dwSize = sizeof(pe);
                if (Process32FirstW(snap, &pe)) {
                    do {
                        if (lstrcmpiW(pe.szExeFile, L"EasiNote.exe") == 0) {
                            FindCtx ctx;
                            ctx.pid = pe.th32ProcessID;
                            ctx.hwnd = NULL;
                            EnumWindows(HideEasiNoteEnum, (LPARAM)&ctx);
                        }
                    } while (Process32NextW(snap, &pe));
                }
                CloseHandle(snap);
            }
        }
        Sleep(3000);
    }
    return 0;
}
#pragma endregion
