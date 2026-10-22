#!/usr/bin/env python3
"""Static sanity checks for the unsigned FLA iOS IPA (no macOS/Xcode required)."""
from __future__ import annotations

import plistlib
import struct
import sys
import zipfile
from pathlib import Path

APP_ID = "top.clrv.fla.app"
APP_PATH = "Payload/FLA.app"
EXT_PATH = APP_PATH + "/PlugIns/FLABroadcast.appex"


def _macho_summary(data: bytes, label: str) -> tuple[int, int]:
    if len(data) < 32:
        raise ValueError(f"{label}: Mach-O header is truncated")
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != 0xFEEDFACF:
        raise ValueError(f"{label}: expected a thin 64-bit little-endian Mach-O (magic={magic:#x})")
    cpu = struct.unpack_from("<I", data, 4)[0]
    file_type = struct.unpack_from("<I", data, 12)[0]
    ncmds, sizeofcmds = struct.unpack_from("<II", data, 16)
    if cpu != 0x0100000C or file_type != 2:
        raise ValueError(f"{label}: expected arm64 executable (cpu={cpu:#x}, type={file_type})")
    offset = 32
    end = offset + sizeofcmds
    if end > len(data):
        raise ValueError(f"{label}: load-command table exceeds file size")
    platform = min_os = 0
    for _ in range(ncmds):
        if offset + 8 > end:
            raise ValueError(f"{label}: malformed load command")
        command, size = struct.unpack_from("<II", data, offset)
        if size < 8 or offset + size > end:
            raise ValueError(f"{label}: invalid load-command size")
        if command == 0x32:  # LC_BUILD_VERSION
            _, _, platform, min_os, _sdk = struct.unpack_from("<IIIII", data, offset)
        offset += size
    if offset != end:
        raise ValueError(f"{label}: load-command size mismatch")
    if platform != 2:  # PLATFORM_IOS (not iOS Simulator / macOS)
        raise ValueError(f"{label}: expected an iOS device binary (platform={platform})")
    return cpu, min_os


def validate(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as ipa:
        broken = ipa.testzip()
        if broken:
            raise ValueError(f"IPA CRC check failed at {broken}")
        names = set(ipa.namelist())
        required = {
            APP_PATH + "/Info.plist",
            APP_PATH + "/FLA",
            EXT_PATH + "/Info.plist",
            EXT_PATH + "/FLABroadcast",
        }
        missing = required - names
        if missing:
            raise ValueError("missing bundle entries: " + ", ".join(sorted(missing)))
        app_info = plistlib.loads(ipa.read(APP_PATH + "/Info.plist"))
        ext_info = plistlib.loads(ipa.read(EXT_PATH + "/Info.plist"))
        if app_info.get("CFBundleIdentifier") != APP_ID:
            raise ValueError("main app bundle identifier does not match the configured ID")
        if app_info.get("CFBundleExecutable") != "FLA":
            raise ValueError("main executable name does not match Info.plist")
        if ext_info.get("CFBundleIdentifier") != APP_ID + ".broadcast":
            raise ValueError("ReplayKit extension bundle identifier is not nested under the app ID")
        extension = ext_info.get("NSExtension") or {}
        attributes = extension.get("NSExtensionAttributes") or {}
        if extension.get("NSExtensionPointIdentifier") != "com.apple.broadcast-services-upload":
            raise ValueError("ReplayKit extension point is missing or incorrect")
        if attributes.get("RPBroadcastProcessMode") != "RPBroadcastProcessModeSampleBuffer":
            raise ValueError("ReplayKit sample-buffer process mode must be under NSExtensionAttributes")
        _macho_summary(ipa.read(APP_PATH + "/FLA"), "FLA")
        _macho_summary(ipa.read(EXT_PATH + "/FLABroadcast"), "FLABroadcast")
        for exe in (APP_PATH + "/FLA", EXT_PATH + "/FLABroadcast"):
            info = ipa.getinfo(exe)
            if not (info.external_attr >> 16) & 0o111:
                raise ValueError(f"{exe}: executable permission bit is missing")
        return {
            "bundle_id": app_info["CFBundleIdentifier"],
            "version": app_info.get("CFBundleShortVersionString", ""),
            "extension_bundle_id": ext_info["CFBundleIdentifier"],
            "signature": "unsigned; must be signed with a provisioning profile before installing",
        }


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("desktop/bin/FLA-ios.ipa")
    try:
        info = validate(path)
    except Exception as exc:
        print(f"IPA validation failed: {exc}", file=sys.stderr)
        return 1
    print("IPA validated: " + ", ".join(f"{key}={value}" for key, value in info.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
