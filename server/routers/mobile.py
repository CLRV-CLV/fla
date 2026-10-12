"""手机端分发
- 安卓: desktop/bin/FLA-cast.apk (mobile/android/build_apk.sh 无 Gradle 直接构建)
- iOS : 描述文件 (.mobileconfig Web Clip) —— 安装后主屏出现全屏 "FLA 投屏" 图标, 无需上架/签名 IPA
- 鸿蒙: 纯血鸿蒙用卓易通/出境易安装 APK, 或直接用网页版
"""
from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

router = APIRouter(prefix="/api/app", tags=["mobile"])
ROOT = Path(__file__).resolve().parents[2]
APK = ROOT / "desktop" / "bin" / "FLA-cast.apk"
ICON = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res" / "mipmap-xxhdpi" / "ic_launcher.png"
APK_VERSION = "1.3.1"
APK_CODE = 5            # 与 mobile/android/app/build.gradle versionCode 一致, App 据此自动更新
IPA = ROOT / "desktop" / "bin" / "FLA-ios.ipa"
IPA_VERSION = "1.0.0"


@router.get("/info")
def app_info():
    ok = APK.exists() and APK.stat().st_size > 10000
    return {
        "android": {"available": ok, "url": "/api/app/android", "size": APK.stat().st_size if ok else 0,
                    "version": APK_VERSION, "code": APK_CODE},
        "ios": {"available": True, "profile": "/api/app/ios.mobileconfig", "web": "/cast.html",
                "ipa": "/api/app/ios.ipa" if IPA.exists() else "", "ipa_version": IPA_VERSION,
                "ipa_size": IPA.stat().st_size if IPA.exists() else 0},
        "harmony": {"web": "/cast.html", "hint": "纯血鸿蒙(HarmonyOS NEXT)请用「卓易通」或「出境易」安装安卓包"},
    }


@router.get("/android")
def download_apk():
    if not APK.exists():
        raise HTTPException(404, "安卓安装包尚未构建，请稍后再试")
    return FileResponse(str(APK), filename="FLA-cast.apk", media_type="application/vnd.android.package-archive",
                        headers={"Content-Disposition": 'attachment; filename="FLA-cast.apk"', "Cache-Control": "no-cache"})


@router.get("/ios.ipa")
def download_ipa():
    if not IPA.exists():
        raise HTTPException(404, "iOS 安装包尚未构建")
    return FileResponse(str(IPA), filename="FLA-ios.ipa", media_type="application/octet-stream",
                        headers={"Content-Disposition": 'attachment; filename="FLA-ios.ipa"', "Cache-Control": "no-cache"})


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _profile(base: str) -> bytes:
    host = base.split("://", 1)[-1]
    # UUID 按域名固定 -> 重复安装会覆盖而不是叠加多个图标
    pid = str(uuid.uuid5(uuid.NAMESPACE_URL, "fla-profile:" + host)).upper()
    cid = str(uuid.uuid5(uuid.NAMESPACE_URL, "fla-clip:" + host)).upper()
    icon = base64.b64encode(ICON.read_bytes()).decode() if ICON.exists() else ""
    icon_xml = f"<key>Icon</key><data>{icon}</data>" if icon else ""
    url = _esc(base + "/cast.html")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>PayloadContent</key><array><dict>
<key>FullScreen</key><true/>
<key>IgnoreManifestScope</key><true/>
<key>IsRemovable</key><true/>
{icon_xml}
<key>Label</key><string>FLA</string>
<key>PayloadDescription</key><string>在主屏幕添加 FLA (课件库 · 上传 · 聊天 · 投屏 · 遥控)</string>
<key>PayloadDisplayName</key><string>FLA</string>
<key>PayloadIdentifier</key><string>top.clrv.fla.webclip</string>
<key>PayloadType</key><string>com.apple.webClip.managed</string>
<key>PayloadUUID</key><string>{cid}</string>
<key>PayloadVersion</key><integer>1</integer>
<key>Precomposed</key><true/>
<key>URL</key><string>{url}</string>
</dict></array>
<key>PayloadDescription</key><string>安装后主屏幕会出现「FLA」图标，可随时在 设置-通用-VPN与设备管理 中删除。本描述文件只添加一个网页图标，不收集任何数据。</string>
<key>PayloadDisplayName</key><string>FLA ({_esc(host)})</string>
<key>PayloadIdentifier</key><string>top.clrv.fla.profile</string>
<key>PayloadOrganization</key><string>FLA</string>
<key>PayloadRemovalDisallowed</key><false/>
<key>PayloadType</key><string>Configuration</string>
<key>PayloadUUID</key><string>{pid}</string>
<key>PayloadVersion</key><integer>1</integer>
</dict></plist>
"""
    return xml.encode("utf-8")


def _sign(data: bytes) -> bytes:
    """可选: 用站点 HTTPS 证书签名, iOS 会显示「已验证」. 未配置则返回未签名(仍可安装, 显示「未签名」)."""
    cert = os.environ.get("FLA_PROFILE_CERT", "/etc/fla/ssl/fullchain.pem")
    key = os.environ.get("FLA_PROFILE_KEY", "/etc/fla/ssl/privkey.pem")
    try:
        if not (os.path.isfile(cert) and os.path.isfile(key)):
            return data
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.serialization import pkcs7
        certs = x509.load_pem_x509_certificates(Path(cert).read_bytes())
        pk = serialization.load_pem_private_key(Path(key).read_bytes(), None)
        b = pkcs7.PKCS7SignatureBuilder().set_data(data).add_signer(certs[0], pk, hashes.SHA256())
        for c in certs[1:]:
            b = b.add_certificate(c)
        return b.sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
    except Exception:
        return data


@router.get("/ios.mobileconfig")
def ios_profile(request: Request):
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    base = f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"
    return Response(_sign(_profile(base)), media_type="application/x-apple-aspen-config",
                    headers={"Content-Disposition": 'attachment; filename="FLA.mobileconfig"', "Cache-Control": "no-cache"})


@router.get("/get")
def smart_get(request: Request):
    """手机端中心二维码: 按系统自动跳转 (安卓/鸿蒙 → APK, iPhone/iPad → 描述文件, 其他 → 网页版)"""
    from fastapi.responses import RedirectResponse
    ua = (request.headers.get("user-agent") or "").lower()
    if "micromessenger" in ua or " qq/" in ua:      # 微信/QQ 内置浏览器拦截下载 → 引导用浏览器打开
        return RedirectResponse("/#/mobile?wx=1", status_code=302)
    if "iphone" in ua or "ipad" in ua or ("macintosh" in ua and "mobile" in ua):
        return RedirectResponse("/api/app/ios.mobileconfig", status_code=302)
    if "android" in ua or "harmony" in ua or "openharmony" in ua:
        return RedirectResponse("/api/app/android", status_code=302)
    return RedirectResponse("/cast.html", status_code=302)
