import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from server.main import app
from server.desktop_dist import ensure_desktop_exe
from server.routers.ai import MsgIn, _DuckDuckGoParser

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
    assert ver_data["version"] == "3.5.4"

    # Check download endpoint
    dl_resp = client.get("/api/desktop/download")
    assert dl_resp.status_code == 200
    assert dl_resp.content.startswith(b"MZ")
    assert len(dl_resp.content) > 300000

def test_mobile_app_update_version_matches_gradle():
    response = client.get("/api/app/info")
    assert response.status_code == 200
    android = response.json()["android"]
    assert android["version"] == "1.3.1"
    assert android["code"] == 5
    assert android["available"] is True


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

def test_opt_in_web_search_parser_and_request_flag():
    parser = _DuckDuckGoParser()
    parser.feed(
        '<div class="result results_links"><div class="result__body">'
        '<a class="result__a" href="https://example.org/physics"><span>Physics lesson</span></a>'
        '<a class="result__snippet">Open-source <b>simulation</b></a>'
        '</div></div>'
    )
    if parser.current:
        parser._finish()
    assert parser.items == [{
        "title": "Physics lesson", "url": "https://example.org/physics", "snippet": "Open-source simulation"
    }]
    assert MsgIn(content="Explain this", online_search=True).online_search is True
    assert MsgIn(content="Explain this").online_search is False


def test_web_static_assets_serve():
    resp_index = client.get("/")
    assert resp_index.status_code == 200
    assert "FLA" in resp_index.text

    resp_css = client.get("/style.css")
    assert resp_css.status_code == 200
    assert "--ease-out" in resp_css.text
    assert "desktop-hub" in resp_css.text

    resp_js = client.get("/js/app.js")
    assert resp_js.status_code == 200
    assert "viewDesktopCenter" in resp_js.text

