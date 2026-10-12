"""FLA 桌面客户端分发与版本自动检测接口.
所有接口无需登录即可公开访问 (支持官网一键免登录直连下载与客户端原地静默更新).
"""
from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse

from server.desktop_dist import ensure_desktop_exe

router = APIRouter(prefix="/api/desktop", tags=["desktop"])

CURRENT_VERSION = "3.5.6"
CHANGELOG = [
    "v3.5.6: 修复 Windows 平板触控模式下未标记触控按下事件导致滑动漏判；按显示器物理边界正确换算混合 DPI 坐标，并修复主窗口最大化后的还原位置；AI 输入框统一为 Fluent 风格",
    "v3.5.5: 修复切换分辨率后桌面主窗口越界/尺寸异常；强化 Windows 触屏滑动识别并放行透明批注层下方内容",
    "v3.5.4: 重做桌面 AI 助教界面，适配 FLA Fluent 全局 UI；支持历史对话恢复/删除、Markdown 回复与快捷提问",
    "v3.5.3: 修复分辨率/显示器变化后舞台画布、遮罩与工具栏错位；Windows 触屏左右滑动可翻页，并过滤工具栏与批注操作",
    "v3.5.2: 实验工具改为 FLA 扩展中心，按系统/架构下载并校验离线 PhET、Tracker 和支持平台的 Avogadro；支持一键安装、打开与卸载，不再跳转项目官网",
    "v3.5.1: 手机画面改为置顶可移动/缩放的浮窗, 最小化后仍可从悬浮控制条结束投屏; 桌面 AI 支持主动联网搜索; 缓存/课件目录可配置; 工具栏尺寸与显隐可定制; 增加开源实验工具目录",
    "v3.5.0: 公告单独一栏并自动弹出(重要公告需阅读5秒); 管理员可在客户端管理用户/邀请码/公告/板块/设置; 客户端背景与透明度可由管理员设置",
    "v3.4.4: 客户端内置 聊天 / 论坛 / 个人中心 (发消息、图片文件、发帖回帖、改资料密码、公告), 不再跳浏览器",
    "v3.4.3: 课件库可直接上传课件(按钮/拖拽), 首页一键打开网页端 课件库/聊天/论坛/个人中心(免登录)",
    "v3.4.2: 手机投屏二维码默认走服务器, 任何网络都能扫 (同一 Wi-Fi 仍自动直连)",
    "v3.4.1: 修复手机批注与电脑显示位置不一致/出现两条线 (PPT 放映区域、多显示器、缩放屏幕)",
    "v3.4.1: 自动更新: 每次启动自动检查并在后台下载新版, 可立即重启或退出时自动安装",
    "v3.4.1: 支持 iPhone 整屏投屏 (FLA iOS App, 系统录屏直播到电脑)",
    "v3.4.0: 手机观看电脑更清晰: 画面静止时自动补发原画质高清帧, 运动时分辨率/质量也提高",
    "v3.4.0: 手机投屏面板去掉无线投屏(Miracast), 改为 FLA 手机端 App 整屏投屏; App 可直接扫面板二维码连接",
    "v3.3.0: 投屏大幅降延迟: WebSocket 实时推送 + 端到端流控(不堆积旧帧), 同一 Wi-Fi 下手机与电脑直连不绕服务器",
    "v3.3.0: 新增安卓「FLA 投屏」App, 整个手机屏幕实时投到电脑; 网站首页新增手机端下载",
    "v3.2.0: 自动识别 PowerPoint/WPS 放映，切换为 PPT 专用工具栏，画布按幻灯片页保存，翻页播放动画",
    "v3.2.0: 未放映时翻页按钮把翻页键发给当前窗口；桌面画布自动保存到本地",
    "v3.2.0: 工具栏新增拖动把手；手机整屏无线镜像入口；修复扫码登录二维码加载失败",
    "v3.1 手机投屏：扫码即可在手机上实时观看电脑屏幕，并用画笔/荧光笔/激光笔/橡皮直接在大屏批注、翻页",
    "v3.1 手机摄像头/照片一键投到大屏，大屏上可继续批注",
    "修复：画笔状态下工具栏区域永不被画布遮挡，任何时候都能点工具栏退出（托盘菜单也可“退出批注”）",
    "橡皮：再点一次可选大小（小/中/大）与对象橡皮/像素橡皮",
    "画笔/荧光笔：再点一次弹出颜色与粗细",
    "新增撤销按钮；服务器线路仅可选 t.clrv.top / t.fyx.best",
]


@router.get("/version")
def get_desktop_version():
    """获取当前最新客户端版本信息及更新说明 (供客户端启动时自动比对自更新)."""
    exe_path = ensure_desktop_exe()
    file_size = exe_path.stat().st_size if exe_path.exists() else 0
    return {
        "ok": True,
        "version": CURRENT_VERSION,
        "name": "FLA 课堂助手",
        "download_url": "/api/desktop/download",
        "release_date": "2026-10-12",
        "size": file_size,
        "changelog": CHANGELOG,
    }


@router.get("/download")
def download_desktop_exe():
    """免登录直接下载 Windows 桌面客户端单文件可执行程序 (FLA.exe)."""
    exe_path = ensure_desktop_exe()
    return FileResponse(
        path=str(exe_path),
        filename="FLA.exe",
        media_type="application/vnd.microsoft.portable-executable",
        headers={
            "Content-Disposition": 'attachment; filename="FLA.exe"',
            "Cache-Control": "no-cache, must-revalidate",
        },
    )
