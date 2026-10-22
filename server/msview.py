"""FLA v1.27 - 微软 Office 在线视图 (view.officeapps.live.com) 对接

为什么需要这个模块:
  微软在线视图能"原样"打开/放映 Office 文档(真字体、真动画), 但它是跨域 iframe,
  父页面【读不到也控制不了】它当前在第几页 —— 这正是"板书画布不能随翻页切换"的根因。

解法(本模块负责服务端那一半):
  1. 从 pptx 包里取出真实的 slide id 列表(ppt/presentation.xml 的 <p:sldIdLst>),
     顺序即放映顺序;
  2. 为每一页生成一个"深链"URL: 同一个 src, 只改 wdStartOn / wdSlideId 参数,
     于是【翻页 = 换 iframe.src】, 我方页码永远是真相 → 画布严格随页切换;
     src 不变, 微软侧的转换缓存可复用, 翻页不需要重新转换(配合前端双 iframe 预载,
     前进/后退几乎是瞬时的);
  3. Word/Excel 同理(Word 用 wdStartOn=页码);
  4. 微软要求 src 是"域名 + 80/443"的公开直链, 所以这里一并做可用性判定(ms_ok)。
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse

# 微软在线视图入口(embed = 精简嵌入版, 无微软自己的页头/广告)
MS_EMBED = "https://view.officeapps.live.com/op/embed.aspx"
MS_VIEW = "https://view.officeapps.live.com/op/view.aspx"

PPT_LIKE = {"ppt", "pptx", "pps", "ppsx", "pot", "potx", "odp", "dps"}
WORD_LIKE = {"doc", "docx", "dot", "dotx", "rtf", "odt", "txt", "wps"}
CELL_LIKE = {"xls", "xlsx", "csv", "ods", "et"}
ZIP_PPT = {"pptx", "ppsx", "potx"}          # 只有 OOXML 包里能读到 slide id

_SLDIDLST = re.compile(r"<p:sldIdLst>(.*?)</p:sldIdLst>", re.S)
_SLDID = re.compile(r"<p:sldId\b[^>]*?\bid=\"(\d+)\"")


def ms_family(ext: str) -> str:
    ext = (ext or "").lower()
    if ext in PPT_LIKE:
        return "ppt"
    if ext in WORD_LIKE:
        return "word"
    if ext in CELL_LIKE:
        return "cell"
    return ""


def supports_ms(ext: str) -> bool:
    """该扩展名是否值得走微软在线视图(其余类型本地渲染更好)"""
    return ms_family(ext) in ("ppt", "word", "cell")


def slide_ids(path: str | Path, cache_dir: str | Path | None = None) -> list[int]:
    """读取 pptx 的真实 slide id 列表(失败返回空表, 前端会退回按序号深链).

    结果缓存到 cache_dir/slideids.json, 避免每次打开都解压。
    """
    p = Path(path)
    if cache_dir:
        cf = Path(cache_dir) / "slideids.json"
        if cf.exists():
            try:
                d = json.loads(cf.read_text(encoding="utf-8"))
                if isinstance(d, list):
                    return [int(x) for x in d]
            except Exception:
                pass
    ids: list[int] = []
    try:
        if p.exists() and p.suffix.lower().lstrip(".") in ZIP_PPT:
            with zipfile.ZipFile(p) as z:
                xml = z.read("ppt/presentation.xml").decode("utf-8", "replace")
            m = _SLDIDLST.search(xml)
            if m:
                ids = [int(x) for x in _SLDID.findall(m.group(1))]
    except Exception:
        ids = []
    if cache_dir and ids:
        try:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            (Path(cache_dir) / "slideids.json").write_text(json.dumps(ids), encoding="utf-8")
        except Exception:
            pass
    return ids


def slide_size(path: str | Path) -> tuple[int, int] | None:
    """pptx 的页面尺寸(EMU), 用于算宽高比 → 前端把画布对准幻灯区域"""
    p = Path(path)
    try:
        if p.exists() and p.suffix.lower().lstrip(".") in ZIP_PPT:
            with zipfile.ZipFile(p) as z:
                xml = z.read("ppt/presentation.xml").decode("utf-8", "replace")
            m = re.search(r"<p:sldSz\b[^>]*?\bcx=\"(\d+)\"[^>]*?\bcy=\"(\d+)\"", xml)
            if m:
                return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None


def ms_ok(direct_url: str) -> bool:
    """微软抓取器的硬性要求: http(s) + 域名(非 IP/非 .local) + 80/443 端口"""
    try:
        u = urlparse(direct_url or "")
        host = (u.hostname or "").lower()
        return bool(u.scheme in ("http", "https") and host
                    and not re.match(r"^\d+\.\d+\.\d+\.\d+$", host)
                    and ":" not in host                       # IPv6 字面量
                    and not host.endswith(".local")
                    and not host.startswith("127.")
                    and (u.port is None or u.port in (80, 443)))
    except Exception:
        return False


def embed_url(direct: str, ext: str, page: int = 1, slide_id: int = 0,
              aspect: float = 0.0, deep: bool = True) -> str:
    """生成微软在线视图 URL.

    page      : 1-based 页码/幻灯序号
    slide_id  : pptx 里的真实 slide id(有则一并带上, 命中率更高)
    deep      : True = 带定位参数(我方翻页驱动微软); False = 只加载文档首页
    """
    from urllib.parse import quote
    fam = ms_family(ext)
    q = [f"src={quote(direct, safe='')}"]
    if fam == "ppt":
        # wdAr: 宽高比(微软用它决定放映区域); wdStartOn/wdSlideId: 定位到第几页
        if aspect:
            q.append(f"wdAr={aspect!r}".replace("'", ""))
        if deep:
            q.append(f"wdStartOn={max(1, int(page))}")
            if slide_id:
                q.append(f"wdSlideId={int(slide_id)}")
        q += ["wdPrint=0", "wdEmbedCode=0"]
    elif fam == "word":
        if deep:
            q.append(f"wdStartOn={max(1, int(page))}")
        q += ["wdPrint=0", "wdEmbedCode=0"]
    else:
        q += ["wdPrint=0", "wdEmbedCode=0"]
    q.append("ui=zh-CN")
    q.append("sftc=1")  # 启用微软 Office Online postMessage 双向握手通信
    return MS_EMBED + "?" + "&".join(q)


def public_base(request=None) -> str:
    """公开访问基地址: 管理后台设置 > 部署配置(deploy/.env) > 当前请求 origin"""
    from . import db
    base = (db.get_setting("public_base_url") or "").strip().rstrip("/")
    if not base:
        base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    return base
