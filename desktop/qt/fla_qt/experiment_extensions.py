"""FLA 管理的教学实验扩展。

只从固定的上游项目地址获取明确列出的包；原始安装包校验后安装在当前用户的
FLA/Extensions 目录，不打开上游下载页面、不绕过操作系统权限提示，也不执行网络返回的任意命令。
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import core

Progress = Callable[[int, int], None]


@dataclass(frozen=True)
class Simulation:
    id: str
    subject: str
    name: str
    description: str
    slug: str
    license: str = "PhET 互动模拟；离线文件保留原始署名/许可信息"

    @property
    def url(self) -> str:
        # PhET's HTML5 builds are single-file simulations which can run from file://.
        return f"https://phet.colorado.edu/sims/html/{self.slug}/latest/{self.slug}_en.html"


@dataclass(frozen=True)
class NativePackage:
    platform: str
    version: str
    filename: str
    url: str
    size: int
    git_blob_sha1: str
    sha256: str


@dataclass(frozen=True)
class NativeExtension:
    id: str
    subject: str
    name: str
    description: str
    license: str
    packages: dict[str, NativePackage]


SIMULATIONS = (
    Simulation("phet-forces", "物理", "力与运动：基础", "用拔河、摩擦力、速度和加速度模拟探索牛顿运动定律。", "forces-and-motion-basics"),
    Simulation("phet-circuits", "物理", "直流电路实验室", "搭建电池、灯泡、电阻和开关，观察电流与电压变化。", "circuit-construction-kit-dc-virtual-lab"),
    Simulation("phet-energy-skate-park", "物理", "滑板公园：能量", "观察动能、势能、摩擦与运动轨迹之间的关系。", "energy-skate-park-basics"),
    Simulation("phet-states-of-matter", "化学", "物质状态：基础", "加热或冷却粒子，观察固体、液体、气体及相变。", "states-of-matter-basics"),
    Simulation("phet-molecule-shapes", "化学", "分子形状", "用电子域排斥模型构建分子并比较三维结构。", "molecule-shapes"),
    Simulation("phet-natural-selection", "生物", "自然选择", "观察环境变化如何影响种群性状与自然选择。", "natural-selection"),
)

# GitHub Git-blob object IDs pin the exact upstream installer bytes. The releases
# live in the official Tracker website repository; install versions are per-OS.
_TRACKER_FILES = {
    "windows": NativePackage(
        "windows", "6.2.0", "TrackerUpgrade-6.2.0-windows-x64-installer.exe",
        "https://github.com/OpenSourcePhysics/tracker-website/raw/main/docs/installers/upgrade/TrackerUpgrade-6.2.0-windows-x64-installer.exe",
        18_350_101, "c3b9c2ef49daebb57eb2745f8ffb5f30123ccc34",
        "6b35032102749725a3eab7a4eb3425ce05eee92d0fa977cdb98aa5f6aeb3e622",
    ),
    "macos": NativePackage(
        "macos", "6.2.0", "TrackerUpgrade-6.2.0-osx-installer.dmg",
        "https://github.com/OpenSourcePhysics/tracker-website/raw/main/docs/installers/upgrade/TrackerUpgrade-6.2.0-osx-installer.dmg",
        18_227_651, "e1729e2c2d453521b5f48a77cb946f469787a68c",
        "8b54543c312c47535809805ecca12a8682ecb77dd384b120d456af265b0c7c3c",
    ),
    "linux": NativePackage(
        "linux", "6.3.5", "TrackerUpgrade-6.3.5-linux-x64-installer.run",
        "https://github.com/OpenSourcePhysics/tracker-website/raw/main/docs/installers/upgrade/TrackerUpgrade-6.3.5-linux-x64-installer.run",
        15_809_637, "e77c8e51bfb7f16d6f2a5f6207fbac76df34f6c1",
        "60d1ad332389f7358a02378947ffd67ef9a71b9aa9ffe044d723c71206459dbe",
    ),
}

TRACKER = NativeExtension(
    "tracker", "物理", "Tracker 视频分析与建模",
    "按当前系统下载官方安装包并安装到 FLA 扩展目录；可逐帧追踪实验视频，分析位置、速度和加速度。",
    "GNU GPL v3",
    _TRACKER_FILES,
)

# Avogadro publishes per-OS portable packages on its official GitHub release.
# Windows currently has only an administrator-level NSIS installer, so it is
# intentionally not advertised as a managed FLA extension on Windows.
_AVOGADRO_PACKAGES = {
    "linux-x86_64": {
        "filename": "Avogadro2-x86_64.AppImage",
        "url": "https://github.com/OpenChemistry/avogadroapp/releases/download/2.0.0/Avogadro2-x86_64.AppImage",
        "size": 110_445_760,
        "sha256": "9792b8a81069d3c7b855374ba580acd9950ca24c8c2173320c7841aa6c82452e",
    },
    "linux-aarch64": {
        "filename": "Avogadro2-aarch64.AppImage",
        "url": "https://github.com/OpenChemistry/avogadroapp/releases/download/2.0.0/Avogadro2-aarch64.AppImage",
        "size": 111_165_824,
        "sha256": "f7cfac39aa8df5d2ff712b5178c90ef87a49972b919e38b003015e9b3dd3c518",
    },
    "macos-x86_64": {
        "filename": "Avogadro2-2.0.0-Darwin.dmg",
        "url": "https://github.com/OpenChemistry/avogadroapp/releases/download/2.0.0/Avogadro2-2.0.0-Darwin.dmg",
        "size": 96_567_779,
        "sha256": "68730783f6d141faaa23bb9972e87fd83d35f6a0173ea7295d675c54a0e5d160",
    },
    "macos-arm64": {
        "filename": "Avogadro2-2.0.0-Darwin-arm64.dmg",
        "url": "https://github.com/OpenChemistry/avogadroapp/releases/download/2.0.0/Avogadro2-2.0.0-Darwin-arm64.dmg",
        "size": 91_594_821,
        "sha256": "6605ec8934ff3206b60bde53f9a3624faf1bda73aa140ba6fbaa0593378b3a42",
    },
}


@dataclass(frozen=True)
class PortableExtension:
    id: str
    subject: str
    name: str
    description: str
    license: str
    package_key: str
    kind: str
    version: str = "2.0.0"


AVOGADRO = PortableExtension(
    "avogadro", "化学", "Avogadro 2 分子编辑器",
    "按系统与处理器架构下载官方便携包，安装到 FLA 扩展目录；用于分子编辑和三维可视化。",
    "BSD 3-Clause", "avogadro", "portable",
)


EXTENSION_ORDER = tuple(s.id for s in SIMULATIONS) + (TRACKER.id, AVOGADRO.id)
_SIM_BY_ID = {s.id: s for s in SIMULATIONS}


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    """Return a stable OS/CPU key used to select a compatible extension package."""
    system = (system or sys.platform).lower()
    machine = (machine or platform.machine()).lower().replace(" ", "")
    if system.startswith("win"):
        return "windows-arm64" if machine in ("arm64", "aarch64") else "windows-x86_64"
    if system == "darwin" or system.startswith("mac"):
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x86_64"
    if system.startswith("linux"):
        return "linux-aarch64" if machine in ("arm64", "aarch64") else "linux-x86_64"
    return f"{system}-{machine}"


def platform_label(key: str | None = None) -> str:
    key = key or platform_key()
    labels = {
        "windows-x86_64": "Windows 64 位",
        "windows-arm64": "Windows ARM64",
        "macos-x86_64": "macOS Intel",
        "macos-arm64": "macOS Apple 芯片",
        "linux-x86_64": "Linux x86_64",
        "linux-aarch64": "Linux ARM64",
    }
    return labels.get(key, key)


def extension_root(root: str | os.PathLike | None = None) -> Path:
    base = Path(root) if root is not None else Path(core.data_dir())
    return base / "Extensions"


def extension_dir(extension_id: str, root: str | os.PathLike | None = None) -> Path:
    if extension_id not in EXTENSION_ORDER:
        raise ValueError("未知实验扩展")
    return extension_root(root) / extension_id


def _manifest_path(path: Path) -> Path:
    return path / "extension.json"


def installed_entry(extension_id: str, root: str | os.PathLike | None = None) -> Path | None:
    dest = extension_dir(extension_id, root)
    manifest = _manifest_path(dest)
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        rel = data.get("entry")
        if not isinstance(rel, str) or not rel:
            return None
        entry = (dest / rel).resolve()
        if dest.resolve() not in entry.parents or not entry.exists():
            return None
        return entry
    except (OSError, ValueError, TypeError):
        return None


def is_installed(extension_id: str, root: str | os.PathLike | None = None) -> bool:
    return installed_entry(extension_id, root) is not None


def _write_manifest(extension_id: str, dest: Path, entry: Path, version: str,
                    source: str, digest: str, license_text: str) -> None:
    data = {
        "id": extension_id,
        "version": version,
        "platform": platform_key(),
        "entry": entry.relative_to(dest).as_posix(),
        "source": source,
        "sha256": digest,
        "license": license_text,
        "installed_at": int(time.time()),
    }
    tmp = _manifest_path(dest).with_suffix(".json.part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _manifest_path(dest))


def _download(url: str, dest: Path, progress: Progress | None = None,
              max_size: int = 150 * 1024 * 1024, expected_size: int | None = None,
              git_blob_sha1: str = "", expected_sha256: str = "",
              trusted_hosts: tuple[str, ...] = ()) -> str:
    headers = {"User-Agent": f"FLA-Desktop/{core.VERSION}", "Accept": "application/octet-stream, text/html;q=0.9, */*;q=0.8"}
    if "api.github.com/repos/" in url and "/git/blobs/" in url:
        headers["Accept"] = "application/vnd.github.raw"
    request = urllib.request.Request(url, headers=headers)
    tmp = dest.with_suffix(dest.suffix + ".part")
    sha256 = hashlib.sha256()
    # GitHub's blob OID is SHA-1 over the exact Git object header and content.
    git_hasher = hashlib.sha1()
    if expected_size is not None:
        git_hasher.update(f"blob {expected_size}\0".encode("ascii"))
    got = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, tmp.open("wb") as out:
            final_host = (urllib.parse.urlparse(response.geturl()).hostname or "").lower()
            if trusted_hosts and not any(final_host == h or final_host.endswith("." + h) for h in trusted_hosts):
                raise ValueError("扩展下载被重定向到非官方域名，已拒绝")
            total = int(response.headers.get("Content-Length") or 0)
            if total > max_size or (expected_size is not None and total and total != expected_size):
                raise ValueError("扩展包大小与清单不符")
            while True:
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                got += len(chunk)
                if got > max_size or (expected_size is not None and got > expected_size):
                    raise ValueError("扩展包超过允许大小")
                out.write(chunk)
                sha256.update(chunk)
                if expected_size is not None:
                    git_hasher.update(chunk)
                if progress:
                    progress(got, total or expected_size or 0)
        if expected_size is not None and got != expected_size:
            raise IOError("扩展包下载不完整")
        if git_blob_sha1 and git_hasher.hexdigest() != git_blob_sha1:
            raise ValueError("上游扩展包校验失败（Git blob SHA-1 不匹配）")
        digest = sha256.hexdigest()
        if expected_sha256 and digest.lower() != expected_sha256.lower():
            raise ValueError("上游扩展包校验失败（SHA-256 不匹配）")
        os.replace(tmp, dest)
        return digest
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _install_simulation(sim: Simulation, root: str | os.PathLike | None,
                        progress: Progress | None) -> Path:
    dest = extension_dir(sim.id, root)
    dest.mkdir(parents=True, exist_ok=True)
    entry = dest / f"{sim.slug}_en.html"
    if is_installed(sim.id, root):
        return entry
    digest = _download(
        sim.url, entry, progress, max_size=50 * 1024 * 1024,
        trusted_hosts=("phet.colorado.edu",),
    )
    if entry.stat().st_size < 100 * 1024:
        entry.unlink(missing_ok=True)
        raise ValueError("下载内容不像是完整的离线模拟文件")
    with entry.open("rb") as stream:
        prefix = stream.read(128 * 1024).lower()
    if b"<html" not in prefix or (sim.slug.encode("ascii") not in prefix and b"phet" not in prefix):
        entry.unlink(missing_ok=True)
        raise ValueError("下载文件不是预期的 PhET 离线模拟")
    _write_manifest(sim.id, dest, entry, "latest", sim.url, digest, sim.license)
    return entry


def _tracker_package() -> NativePackage:
    key = platform_key().split("-", 1)[0]
    package = _TRACKER_FILES.get(key)
    if not package:
        raise RuntimeError(f"当前系统 {platform_label()} 暂无 Tracker 安装包")
    if key == "windows" and platform_key() != "windows-x86_64":
        raise RuntimeError("Tracker 扩展目前没有 Windows ARM64 便携安装包")
    if key == "linux" and platform_key() != "linux-x86_64":
        raise RuntimeError("Tracker 扩展目前没有 Linux ARM64 安装包")
    if key == "macos" and platform_key() != "macos-x86_64":
        raise RuntimeError("Tracker 扩展目前没有 macOS ARM64 安装包")
    return package


def _run_installbuilder(installer: Path, target: Path) -> None:
    if sys.platform == "darwin":
        mount = Path(tempfile.mkdtemp(prefix="fla-tracker-dmg-"))
        mounted = False
        try:
            subprocess.run(["hdiutil", "attach", "-quiet", "-nobrowse", "-readonly",
                            "-mountpoint", str(mount), str(installer)],
                           check=True, timeout=90, capture_output=True)
            mounted = True
            apps = sorted(mount.rglob("*.app"), key=lambda p: len(p.parts))
            candidate = None
            for app in apps:
                macos = app / "Contents" / "MacOS"
                if macos.is_dir():
                    executables = [p for p in macos.iterdir() if p.is_file() and os.access(p, os.X_OK)]
                    if executables:
                        candidate = sorted(executables, key=lambda p: p.name.lower())[0]
                        break
            if not candidate:
                raise RuntimeError("Tracker 官方 macOS 安装包中没有找到安装程序")
            command = [str(candidate), "--mode", "unattended", f"--prefix={target}"]
        finally:
            if mounted:
                subprocess.run(["hdiutil", "detach", "-quiet", str(mount)],
                               check=False, timeout=60, capture_output=True)
            shutil.rmtree(mount, ignore_errors=True)
    else:
        if sys.platform != "win32":
            installer.chmod(installer.stat().st_mode | stat.S_IXUSR)
        command = [str(installer), "--mode", "unattended", f"--prefix={target}"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    result = subprocess.run(command, check=False, timeout=900, creationflags=flags,
                            capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "安装程序未返回成功状态").strip()[-600:]
        raise RuntimeError(f"Tracker 安装失败（{result.returncode}）：{details}")


def _find_tracker_entry(dest: Path) -> Path | None:
    # Known launch names only: never execute a file selected from arbitrary metadata.
    if sys.platform == "darwin":
        apps = sorted(dest.rglob("Tracker.app"), key=lambda p: len(p.parts))
        if apps:
            return apps[0]
    wanted = ("tracker.exe",) if sys.platform == "win32" else ("tracker", "tracker.sh")
    for path in dest.rglob("*"):
        if path.is_file() and path.name.lower() in wanted:
            if sys.platform != "win32":
                path.chmod(path.stat().st_mode | stat.S_IXUSR)
            return path
    return None


def _install_tracker(root: str | os.PathLike | None, progress: Progress | None) -> Path:
    package = _tracker_package()
    dest = extension_dir(TRACKER.id, root)
    existing = installed_entry(TRACKER.id, root)
    if existing:
        return existing
    dest.mkdir(parents=True, exist_ok=True)
    installer = dest / package.filename
    digest = _download(
        package.url, installer, progress, max_size=package.size,
        expected_size=package.size, git_blob_sha1=package.git_blob_sha1,
        expected_sha256=package.sha256,
        trusted_hosts=("github.com", "raw.githubusercontent.com", "githubusercontent.com"),
    )
    install_dir = dest / "program"
    if install_dir.exists():
        shutil.rmtree(install_dir, ignore_errors=True)
    install_dir.mkdir(parents=True)
    try:
        _run_installbuilder(installer, install_dir)
    except Exception:
        shutil.rmtree(install_dir, ignore_errors=True)
        raise
    finally:
        installer.unlink(missing_ok=True)
    entry = _find_tracker_entry(install_dir)
    if not entry:
        shutil.rmtree(install_dir, ignore_errors=True)
        raise RuntimeError("Tracker 安装器已结束，但没有在 FLA 扩展目录找到启动程序")
    _write_manifest(TRACKER.id, dest, entry, package.version, package.url, digest, TRACKER.license)
    return entry


def _avogadro_package() -> dict:
    key = platform_key()
    if key not in _AVOGADRO_PACKAGES:
        if key.startswith("windows"):
            raise RuntimeError("Avogadro 的 Windows 版仅提供需要管理员授权的系统安装器；暂不作为 FLA 扩展安装")
        raise RuntimeError(f"当前系统 {platform_label()} 暂无 Avogadro 便携包")
    return _AVOGADRO_PACKAGES[key]


def _mount_app_dmg(dmg: Path, dest: Path) -> Path:
    mount = Path(tempfile.mkdtemp(prefix="fla-avogadro-dmg-"))
    mounted = False
    try:
        subprocess.run(["hdiutil", "attach", "-quiet", "-nobrowse", "-readonly",
                        "-mountpoint", str(mount), str(dmg)],
                       check=True, timeout=120, capture_output=True)
        mounted = True
        apps = sorted(mount.rglob("Avogadro2.app"), key=lambda p: len(p.parts))
        if not apps:
            raise RuntimeError("Avogadro 官方磁盘映像中没有找到应用程序")
        target = dest / "Avogadro2.app"
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(apps[0], target, symlinks=True)
        return target
    finally:
        if mounted:
            subprocess.run(["hdiutil", "detach", "-quiet", str(mount)],
                           check=False, timeout=60, capture_output=True)
        shutil.rmtree(mount, ignore_errors=True)


def _install_avogadro(root: str | os.PathLike | None, progress: Progress | None) -> Path:
    package = _avogadro_package()
    dest = extension_dir(AVOGADRO.id, root)
    existing = installed_entry(AVOGADRO.id, root)
    if existing:
        return existing
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / package["filename"]
    digest = _download(
        package["url"], archive, progress, max_size=130 * 1024 * 1024,
        expected_size=package["size"], expected_sha256=package["sha256"],
        trusted_hosts=("github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com", "githubusercontent.com"),
    )
    try:
        key = platform_key()
        if key.startswith("linux"):
            entry = dest / "Avogadro2.AppImage"
            os.replace(archive, entry)
            entry.chmod(entry.stat().st_mode | stat.S_IXUSR)
        elif key.startswith("macos"):
            entry = _mount_app_dmg(archive, dest)
        else:
            raise RuntimeError("当前系统不支持此 Avogadro 扩展包格式")
    finally:
        archive.unlink(missing_ok=True)
    _write_manifest(AVOGADRO.id, dest, entry, AVOGADRO.version, package["url"], digest, AVOGADRO.license)
    return entry


def catalog() -> list[dict]:
    """UI-facing catalog, filtered only for whether this system has a package."""
    current = platform_key()
    items = []
    for sim in SIMULATIONS:
        items.append({
            "id": sim.id, "subject": sim.subject, "name": sim.name,
            "description": sim.description, "license": sim.license,
            "kind": "html", "version": "PhET 最新离线版", "available": True,
            "installed": is_installed(sim.id),
        })
    tracker_available = (current in ("windows-x86_64", "macos-x86_64", "linux-x86_64"))
    tracker_package = _TRACKER_FILES.get(current.split("-", 1)[0])
    items.append({
        "id": TRACKER.id, "subject": TRACKER.subject, "name": TRACKER.name,
        "description": TRACKER.description, "license": TRACKER.license,
        "kind": "native", "version": tracker_package.version if tracker_package else "",
        "available": tracker_available, "installed": is_installed(TRACKER.id),
    })
    avogadro_available = current in _AVOGADRO_PACKAGES
    items.append({
        "id": AVOGADRO.id, "subject": AVOGADRO.subject, "name": AVOGADRO.name,
        "description": AVOGADRO.description, "license": AVOGADRO.license,
        "kind": "native", "version": AVOGADRO.version,
        "available": avogadro_available, "installed": is_installed(AVOGADRO.id),
    })
    return items


def install(extension_id: str, root: str | os.PathLike | None = None,
            progress: Progress | None = None) -> Path:
    if extension_id in _SIM_BY_ID:
        return _install_simulation(_SIM_BY_ID[extension_id], root, progress)
    if extension_id == TRACKER.id:
        return _install_tracker(root, progress)
    if extension_id == AVOGADRO.id:
        return _install_avogadro(root, progress)
    raise ValueError("未知实验扩展")


def open_installed(extension_id: str, root: str | os.PathLike | None = None) -> Path:
    entry = installed_entry(extension_id, root)
    if entry is None:
        raise RuntimeError("此扩展尚未安装")
    if extension_id in _SIM_BY_ID:
        # The caller opens this local file through QDesktopServices, using the OS browser.
        return entry
    if sys.platform == "darwin" and entry.suffix.lower() == ".app":
        subprocess.Popen(["open", str(entry)], close_fds=True)
    elif sys.platform == "win32":
        os.startfile(str(entry))  # type: ignore[attr-defined]
    else:
        subprocess.Popen([str(entry)], cwd=str(entry.parent), close_fds=True)
    return entry


def uninstall(extension_id: str, root: str | os.PathLike | None = None) -> None:
    dest = extension_dir(extension_id, root)
    base = extension_root(root).resolve()
    resolved = dest.resolve()
    if resolved.parent != base or resolved == base:
        raise ValueError("扩展目录校验失败")
    shutil.rmtree(resolved, ignore_errors=False)
