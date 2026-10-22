"""FLA 桌面助手 - 版本自动检测与原地静默更新模块 (Auto Updater)
特性：
- 启动时自动后台检测服务端最新版本 (无需用户手动重新访问网站下载)
- 支持检测到新版本时一键静默下载
- Windows 下利用 .old 重命名机制，实现运行中原地自更新与热重载
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time
from urllib.parse import urljoin
import urllib.request

logger = logging.getLogger("fla.desktop.updater")

CURRENT_VERSION = "3.4.1"
DEFAULT_SERVER = "http://127.0.0.1:8306"


def get_server_url() -> str:
    """获取当前连接的 FLA 服务端地址"""
    # 优先从环境变量或本地缓存配置中读取
    env_url = os.environ.get("FLA_SERVER_URL", "").strip()
    if env_url:
        return env_url
    cfg_path = os.path.join(os.path.expanduser("~"), ".fla_desktop_config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                d = json.load(f)
                if d.get("server_url"):
                    return d["server_url"].rstrip("/")
        except Exception:
            pass
    return DEFAULT_SERVER


def check_version(server_url: str | None = None) -> dict | None:
    """访问服务端检测最新客户端版本信息"""
    base = (server_url or get_server_url()).rstrip("/")
    url = f"{base}/api/desktop/version"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"FLA-Desktop/{CURRENT_VERSION}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                remote_ver = data.get("version", "")
                has_update = is_newer_version(remote_ver, CURRENT_VERSION)
                data["has_update"] = has_update
                data["current_version"] = CURRENT_VERSION
                return data
    except Exception as e:
        logger.debug(f"检查更新失败 ({url}): {e}")
    return None


def is_newer_version(remote_ver: str, current_ver: str) -> bool:
    """比较版本号是否大于当前版本"""
    def parse(v: str):
        return [int(x) for x in v.strip().lstrip("v").split(".") if x.isdigit()]
    try:
        return parse(remote_ver) > parse(current_ver)
    except Exception:
        return False


def perform_update(download_url: str, on_progress=None) -> bool:
    """下载并替换当前运行的可执行文件，重启新版客户端"""
    try:
        exe_path = sys.executable
        # 如果是 python 脚本运行而非打包的 exe，仅提示更新
        if not exe_path.lower().endswith(".exe") or "python" in os.path.basename(exe_path).lower():
            logger.info("当前运行于源码开发模式，跳过本地可执行文件覆盖")
            return False

        temp_download = exe_path + ".download"
        backup_old = exe_path + ".old"

        # 1. 下载新版本
        logger.info(f"正在下载新版本: {download_url}")
        req = urllib.request.Request(download_url, headers={"User-Agent": f"FLA-Desktop/{CURRENT_VERSION}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(temp_download, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress and total_size > 0:
                        on_progress(downloaded / total_size)

        # 2. 原地热替换
        if os.path.exists(backup_old):
            try:
                os.remove(backup_old)
            except Exception:
                pass

        os.rename(exe_path, backup_old)
        os.rename(temp_download, exe_path)

        # 3. 启动新版本并退出当前进程
        logger.info("新版本已就位，正在重启 FLA 客户端…")
        os.startfile(exe_path)
        sys.exit(0)
    except Exception as e:
        logger.error(f"自动更新失败: {e}")
        return False


class AutoUpdaterThread(threading.Thread):
    def __init__(self, callback=None):
        super().__init__(daemon=True)
        self.callback = callback

    def run(self):
        time.sleep(3)  # 启动延迟 3 秒检测
        res = check_version()
        if res and res.get("has_update"):
            logger.info(f"发现新版本: {res.get('version')} (当前: {CURRENT_VERSION})")
            if self.callback:
                self.callback(res)
            # 如果配置了静默自动升级
            cfg_path = os.path.join(os.path.expanduser("~"), ".fla_desktop_config.json")
            auto_apply = True
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        auto_apply = json.load(f).get("auto_update", True)
                except Exception:
                    pass
            if auto_apply:
                dl_url = res.get("download_url")
                if dl_url:
                    base = get_server_url().rstrip("/")
                    full_dl = urljoin(base, dl_url)
                    perform_update(full_dl)
