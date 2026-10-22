import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from server.main import app
from server.desktop_dist import ensure_desktop_exe
from server.routers.ai import MsgIn, _web_search

client = TestClient(app)

def test_remote_pair_endpoint():
    # 1. Create a remote session
    resp = client.post("/api/remote/create", json={"title": "测试放映", "page": 1, "total": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    sid = data["session_id"]
    code = data["code"]

    # 2. Look up the session by 4-digit code
    pair_resp = client.get(f"/api/remote/pair/{code}")
    assert pair_resp.status_code == 200
    pair_data = pair_resp.json()
    assert pair_data["ok"] is True
    assert pair_data["session_id"] == sid
    assert pair_data["code"] == code
    assert pair_data["title"] == "测试放映"

    # 3. Check QR PNG generation endpoint
    qr_resp = client.get(f"/api/remote/{sid}/qr")
    assert qr_resp.status_code == 200
    assert qr_resp.headers["content-type"] == "image/png"
    assert qr_resp.content.startswith(b"\x89PNG")

    # 4. Invalid code returns 404
    inv_resp = client.get("/api/remote/pair/0000")
    assert inv_resp.status_code == 404

def test_desktop_version_and_download():
    # Check version endpoint
    ver_resp = client.get("/api/desktop/version")
    assert ver_resp.status_code == 200
    ver_data = ver_resp.json()
    assert ver_data["ok"] is True
    assert ver_data["version"] == "3.5.6"

    # Check download endpoint
    dl_resp = client.get("/api/desktop/download")
    assert dl_resp.status_code == 200
    assert dl_resp.content.startswith(b"MZ")
    assert len(dl_resp.content) > 300000

def test_mobile_app_update_version_matches_gradle():
    response = client.get("/api/app/info")
    assert response.status_code == 200
    android = response.json()["android"]
    assert android["version"] == "1.4.1"
    assert android["code"] == 7
    assert android["size"] > 10_000
    assert android["available"] is True
    ios = response.json()["ios"]
    assert ios["ipa_version"] == "1.1.1"
    assert ios["ipa_size"] > 10_000


def test_ios_ipa_bundle_is_arm64_device_ready_and_signing_safe():
    from mobile.ios.validate_ipa import validate

    info = validate(REPO_ROOT / "desktop" / "bin" / "FLA-ios.ipa")
    assert info["version"] == "1.1.1"
    assert info["extension_bundle_id"] == "top.clrv.fla.app.broadcast"
    assert "unsigned" in info["signature"]
    script = (REPO_ROOT / "mobile" / "ios" / "build_ipa.sh").read_text(encoding="utf-8")
    assert '"NSExtensionAttributes": {"RPBroadcastProcessMode"' in script
    assert "application-identifier" not in script
    assert "com.apple.security.application-groups" not in script
    assert not (REPO_ROOT / "mobile" / "ios" / "entitlements.plist").exists()


def test_native_qr_pairing_preserves_non_default_server_ports():
    android = (REPO_ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" /
               "top" / "clrv" / "fla" / "MainActivity.java").read_text(encoding="utf-8")
    ios = (REPO_ROOT / "mobile" / "ios" / "App.m").read_text(encoding="utf-8")
    assert "u.getPort() > 0 ? \":\" + u.getPort() : \"\"" in android
    assert "parts.port ? [NSString stringWithFormat:@\"%@:%@\"" in ios


def test_file_list_query_is_indexed_and_frontend_recovers_from_errors():
    import sqlite3
    from server.db import SCHEMA

    con = sqlite3.connect(":memory:")
    try:
        con.executescript(SCHEMA)
        plan = con.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM files WHERE user_id=? ORDER BY id DESC", (1,)
        ).fetchall()
        assert any("ix_files_owner_order" in row[3] for row in plan)
    finally:
        con.close()

    app_js = (REPO_ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
    assert "timeout: 30000" in app_js
    assert "showFileListLoadError(e)" in app_js
    assert "页面渲染失败" in app_js
    assert "重新加载" in app_js

    api_js = (REPO_ROOT / "web" / "js" / "api.js").read_text(encoding="utf-8")
    request_body = api_js.split("async req(path, opts) {", 1)[1].split("get(p)", 1)[0]
    assert request_body.index("data = await r.json()") < request_body.index("finally {")
    assert "if (timer) clearTimeout(timer);" in request_body


def test_experiment_extensions_are_local_and_platform_aware(tmp_path):
    from desktop.qt.fla_qt import experiment_extensions as extensions

    assert len(extensions.SIMULATIONS) >= 6
    assert all(sim.url.startswith("https://phet.colorado.edu/sims/html/") for sim in extensions.SIMULATIONS)
    assert extensions.platform_key("win32", "AMD64") == "windows-x86_64"
    assert extensions.platform_key("darwin", "arm64") == "macos-arm64"
    assert extensions.platform_key("linux", "aarch64") == "linux-aarch64"
    assert extensions._TRACKER_FILES["windows"].git_blob_sha1 == "c3b9c2ef49daebb57eb2745f8ffb5f30123ccc34"
    assert extensions.extension_dir("phet-forces", tmp_path) == tmp_path / "Extensions" / "phet-forces"
    assert "windows-x86_64" not in extensions._AVOGADRO_PACKAGES


def test_physics_simulation_installs_as_local_offline_extension(tmp_path, monkeypatch):
    import io
    from desktop.qt.fla_qt import experiment_extensions as extensions

    sim = extensions.SIMULATIONS[0]
    payload = (f"<!doctype html><html><head><title>PhET {sim.slug}</title></head><body>".encode()
               + b"simulation" * 12000 + b"</body></html>")

    class FakeResponse(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}

        def geturl(self):
            return sim.url

    monkeypatch.setattr(extensions.urllib.request, "urlopen",
                        lambda _request, timeout=60: FakeResponse(payload))
    entry = extensions.install(sim.id, root=tmp_path)
    assert entry.exists()
    assert extensions.installed_entry(sim.id, root=tmp_path) == entry.resolve()
    assert extensions.open_installed(sim.id, root=tmp_path) == entry.resolve()
    manifest = extensions._manifest_path(extensions.extension_dir(sim.id, tmp_path))
    assert "PhET" in manifest.read_text(encoding="utf-8")
    extensions.uninstall(sim.id, root=tmp_path)
    assert not extensions.extension_dir(sim.id, tmp_path).exists()


def test_csharp_client_architecture():
    cs_path = REPO_ROOT / "desktop" / "FLA_Client.cs"
    assert cs_path.exists()
    content = cs_path.read_text(encoding="utf-8")
    assert "class FloatingDockForm" in content
    assert "class ScreenOverlayForm" in content
    assert "class MainForm" in content
    assert "class CloudFileItem" in content
    assert "LaunchOfficePresentation" in content
    assert "InitCastingTab" in content
    assert "StartSeewoInterceptor" in content
    assert "FindPresentationApp" in content

def test_file_rename_patch():
    # Attempt login with possible passwords or reset
    with TestClient(app) as c:
        import re
        pw = "smoke-admin-pw"
        init_pw_file = REPO_ROOT / "data" / "initial_admin_password.txt"
        if init_pw_file.exists():
            m = re.search(r"密码:\s*(\S+)", init_pw_file.read_text())
            if m:
                pw = m.group(1)

        login_resp = c.post("/api/auth/login", json={"username": "admin", "password": pw})
        if login_resp.status_code != 200:
            login_resp = c.post("/api/auth/login", json={"username": "admin", "password": "smoke-admin-pw"})
        if login_resp.status_code != 200:
            login_resp = c.post("/api/auth/login", json={"username": "admin", "password": "pw-smoke-456"})

        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        token = login_resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create a board file
        board_resp = c.post("/api/files/board", headers=headers, json={})
        assert board_resp.status_code == 200
        fid = board_resp.json()["id"]

        # Rename file
        patch_resp = c.patch(f"/api/files/{fid}", headers=headers, json={"name": "新重命名白板.fla"})
        assert patch_resp.status_code == 200
        data = patch_resp.json()
        assert data["ok"] is True
        assert data["name"] == "新重命名白板.fla"

        # Verify updated name via GET
        get_resp = c.get("/api/files", headers=headers)
        assert get_resp.status_code == 200
        files = get_resp.json()
        matched = [f for f in files if f["id"] == fid]
        assert len(matched) == 1
        assert matched[0]["name"] == "新重命名白板.fla"

        # Test empty name fails
        bad_resp = c.patch(f"/api/files/{fid}", headers=headers, json={"name": "   "})
        assert bad_resp.status_code == 400

        # Cleanup
        del_resp = c.delete(f"/api/files/{fid}", headers=headers)
        assert del_resp.status_code == 200

def test_opt_in_web_search_uses_open_source_ddgs(monkeypatch):
    import sys
    import types

    calls = {}

    class FakeDDGS:
        def __init__(self, timeout):
            calls["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def text(self, query, **kwargs):
            calls["query"] = query
            calls["kwargs"] = kwargs
            return [
                {"title": "Physics lesson", "href": "https://example.org/physics#top",
                 "body": "Open-source simulation"},
                {"title": "Unsafe", "href": "javascript:alert(1)", "body": "not a web result"},
            ]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
    results, error = _web_search("  Explain this  ", limit=5)
    assert error is None
    assert results == [{
        "title": "Physics lesson", "url": "https://example.org/physics", "snippet": "Open-source simulation"
    }]
    assert calls["query"] == "Explain this"
    assert calls["kwargs"] == {
        "region": "wt-wt", "safesearch": "moderate", "max_results": 5, "backend": "auto"
    }
    assert calls["timeout"] == 8
    assert _web_search("  ")[0] == []
    assert MsgIn(content="Explain this", online_search=True).online_search is True
    assert MsgIn(content="Explain this").online_search is False


def test_web_static_assets_serve():
    resp_index = client.get("/")
    assert resp_index.status_code == 200
    assert "FLA" in resp_index.text
    assert "js/app.js?v=159" in resp_index.text
    assert "js/api.js?v=136" in resp_index.text

    resp_css = client.get("/style.css")
    assert resp_css.status_code == 200
    assert "--ease-out" in resp_css.text
    assert "desktop-hub" in resp_css.text

    resp_js = client.get("/js/app.js")
    assert resp_js.status_code == 200
    assert "viewDesktopCenter" in resp_js.text



# ---------------------------------------------------------------- 前端全局命名冲突 ---
# web/js/*.js 全部以「经典 <script>」加载到同一个全局作用域: 函数/变量重名会互相覆盖。
# 曾经 ai.js 的 refreshList() 覆盖了 app.js 的同名函数, 导致课件库永远停在「加载中…」。
def _top_level_declarations(src: str):
    import re
    s = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    s = re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`", '""', s)
    names, depth = set(), 0
    for line in s.splitlines():
        if depth == 0:
            stripped = line.strip()
            m = re.match(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", stripped)
            if m:
                names.add(m.group(1))
            m = re.match(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)", stripped)
            if m:
                names.add(m.group(1))
        depth = max(0, depth + line.count("{") - line.count("}"))
    return names


def test_web_js_no_global_name_collision():
    web_js = REPO_ROOT / "web" / "js"
    owners = {}
    for f in sorted(web_js.glob("*.js")):
        if f.name.endswith(".min.js"):
            continue
        for name in _top_level_declarations(f.read_text(encoding="utf-8")):
            owners.setdefault(name, []).append(f.name)
    dup = {k: v for k, v in owners.items() if len(v) > 1}
    assert dup == {}, f"这些全局名在多个脚本里重复声明, 会互相覆盖: {dup}"


def test_library_file_list_not_shadowed():
    app_js = (REPO_ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
    ai_js = (REPO_ROOT / "web" / "js" / "ai.js").read_text(encoding="utf-8")
    assert "async function refreshList()" in app_js
    assert "async function loadLibraryFiles()" in app_js
    # ai.js 不得再声明裸的 refreshList / view, 否则会覆盖课件库与后台的同名函数
    assert "function refreshList(" not in ai_js
    assert "function view(" not in ai_js


# ---------------------------------------------------------------- 转换失败可诊断 ---
def test_convert_selfcheck_endpoint():
    r = client.get("/api/health/convert")
    assert r.status_code == 200
    d = r.json()
    assert set(d) >= {"libreoffice", "ffmpeg", "timeout"}
    assert isinstance(d["libreoffice"]["ok"], bool)
    assert d["libreoffice"]["detail"]          # 不可用时也要有可读原因


def test_converter_error_messages_are_actionable():
    from server import converter

    class R:
        def __init__(self, stderr="", code=1):
            self.stderr = stderr
            self.stdout = ""
            self.returncode = code

    cases = {
        "no such file": "LibreOffice",
        "Error: source file could not be loaded": "损坏",
        "Permission denied": "权限",
        "conversion timed out after 900s": "超时",
    }
    for stderr, want in cases.items():
        msg = converter.lo_error(R(stderr))
        assert want in msg, (stderr, msg)
        assert "退出码" in msg
    # 完全无输出时也要给出可读文字, 不能是空串
    assert converter.lo_error(R("")).strip()


def test_library_shows_convert_error():
    app_js = (REPO_ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
    # 失败原因必须能展示给用户(角标 tooltip + 详情抽屉 + 重试按钮)
    assert "f.error" in app_js
    assert "fd-error" in app_js
    assert "fd-act-retry" in app_js
