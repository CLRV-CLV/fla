# -*- coding: utf-8 -*-
"""
FLA v1.14 - PPTX 动画解析器(Tier 2 放映引擎服务端)
v1.14 设计变革(用户铁律: 禁止纯静态页):
  - 不认识的效果 → 降级效果(fade/appear), 绝不降级整页
  - 退出动画 → out 步骤(初始可见, 到步消失); 强调 → pulse 脉冲
  - 表格 → HTML 表格元素(公式单元格渲染为图片; 文字用浏览器本机字体, 不再错位)
  - 不支持的形状/图表/SmartArt/组合 → crop 元素(前端从主 PDF 裁剪位图, 仍可动画)
  - 同一形状多次动画 → steps 列表
  - v1.15 全页元素化: 无动画形状也全部成为常驻元素(浏览器本机字体, 拒绝 LibreOffice 字体替换)
  - 占位符颜色/字号从版式/母版继承; 正文占位符默认项目符号
  - 唯一保留静态的情况: 该页没有任何可定位形状(真空页)
"""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.dml import MSO_FILL

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
}


def q(tag):
    return "/".join("{%s}%s" % (NS[seg.split(":")[0]], seg.split(":", 1)[1])
                    for seg in tag.split("/"))


# ---------------- p:timing 解析 ----------------

def _cond_delay(ctn):
    st = ctn.find(q("p:stCondLst"))
    if st is None:
        return 0
    c = st.find(q("p:cond"))
    if c is None:
        return 0
    try:
        return int(float(c.get("delay", "0")))
    except (TypeError, ValueError):
        return 0


def _first_spid(effect_ctn):
    for el in effect_ctn.iter():
        if el.tag == q("p:spTgt"):
            return el.get("spid")
    return None


_SUBTYPE_DIR = {1: (0, -1), 2: (1, 0), 4: (0, 1), 8: (-1, 0),
                3: (1, -1), 6: (1, 1), 9: (-1, -1), 12: (-1, 1)}
_DEGRADE_TO_FADE = {"blinds", "checkerboard", "dissolve", "circle", "box", "diamond",
                    "plus", "randombars", "strips", "wedge", "wheel", "split", "barn",
                    "comb", "random", "pixels", "newsflash"}


def _classify(ctn, cls):
    """效果识别. 返回 (kind, dir, dur, extra). v1.14: 未知一律降级, 不再返回 unsupported."""
    dur = 0
    filt = motion = scale = None
    chl = ctn.find(q("p:childTnLst"))
    if chl is not None:
        for ch in chl:
            tag = ch.tag
            if tag == q("p:animEffect"):
                filt = ch.get("filter", "") or ""
            elif tag == q("p:animMotion"):
                motion = ch.get("path", "") or ""
            elif tag == q("p:animScale"):
                scale = ch
            b = ch.find(q("p:cBhvr/p:cTn"))
            if b is not None:
                try:
                    dur = max(dur, int(float(b.get("dur", "0"))))
                except (TypeError, ValueError):
                    pass

    if cls == "exit":
        return ("fade", "", dur or 400, {})
    if cls == "emph":
        return ("pulse", "", dur or 300, {})
    if cls not in ("", "entr"):
        return ("fade", "", dur or 400, {})

    if filt:
        m = re.match(r"^\s*([a-zA-Z]+)\s*(?:\(([^)]*)\))?", filt)
        if m:
            name = m.group(1).lower()
            arg = (m.group(2) or "").strip().lower()
            if name == "fade":
                return ("fade", "", dur or 500, {})
            if name == "wipe":
                return ("wipe", arg or "right", dur or 500, {})
            if name in _DEGRADE_TO_FADE:
                return ("fade", "", dur or 500, {"degraded": 1})
            if name == "slide":
                return ("fly", arg, dur or 500, {})
        return ("fade", "", dur or 500, {"degraded": 1})

    if motion:
        mm = re.match(r"M\s+([-\d.]+)\s+([-\d.]+)\s+L\s+([-\d.]+)\s+([-\d.]+)", motion)
        if mm:
            dx, dy = float(mm.group(1)), float(mm.group(2))
            if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                return ("appear", "", 1, {})
            return ("fly", "%g,%g" % (dx, dy), dur or 500, {})
        try:
            sub = int(ctn.get("presetSubtype") or 0)
        except ValueError:
            sub = 0
        if sub in _SUBTYPE_DIR:
            dx, dy = _SUBTYPE_DIR[sub]
            return ("fly", "%g,%g" % (dx, dy), dur or 500, {"degraded": 1})
        return ("fade", "", dur or 500, {"degraded": 1})

    if scale is not None:
        f = scale.find(q("p:from"))
        fx = 0.25
        if f is not None:
            try:
                fx = int(f.get("x")) / 100000.0
            except (TypeError, ValueError):
                fx = 0.25
        if not (0 < fx < 1):
            fx = 0.25
        return ("zoom", "", dur or 500, {"scale": round(fx, 3)})

    return ("appear", "", 1, {})


def parse_timing(slide_el):
    """主时间序列 → 效果列表(文档序): [{spid,group,node,delay,dur,kind,dir,cls,...}]"""
    seq_ctn = None
    for el in slide_el.iter(q("p:seq")):
        c = el.find(q("p:cTn"))
        if c is not None and c.get("nodeType") == "mainSeq":
            seq_ctn = c
            break
    if seq_ctn is None:
        return []
    cl = seq_ctn.find(q("p:childTnLst"))
    if cl is None:
        return []
    effects = []
    group = -1
    for par in cl.findall(q("p:par")):
        outer = par.find(q("p:cTn"))
        if outer is None:
            continue
        st = outer.find(q("p:stCondLst"))
        first = st.find(q("p:cond")) if st is not None else None
        d = first.get("delay", "0") if first is not None else "0"
        evt = first.get("evt", "") if first is not None else ""
        is_new_click = (d == "indefinite" or evt in ("onNext", "onClick") or outer.get("nodeType") == "clickEffect")
        if is_new_click:
            group += 1
        g = group if group >= 0 else -1
        for ctn in outer.iter(q("p:cTn")):
            nt = ctn.get("nodeType")
            if nt not in ("clickEffect", "withEffect", "afterEffect"):
                continue
            spid = _first_spid(ctn)
            if not spid:
                continue
            cls = (ctn.get("presetClass") or "entr").strip()
            if g < 0 and nt == "clickEffect":
                group = max(0, group + 1)
                g = group
            kind, dirn, dur, extra = _classify(ctn, cls)
            e = {"spid": spid, "group": g, "node": nt, "cls": cls,
                 "delay": _cond_delay(ctn), "dur": dur or 500,
                 "kind": kind, "dir": dirn}
            e.update(extra)
            effects.append(e)

    # 兜底校验: 若包含多个 clickEffect 但全落在同一个组或负数组，按顺序为各 clickEffect 独立分配组号
    click_nodes = [e for e in effects if e.get("node") == "clickEffect"]
    if len(click_nodes) > 1 and len(set(e.get("group") for e in click_nodes)) == 1:
        cur_g = -1
        for e in effects:
            if e.get("node") == "clickEffect":
                cur_g += 1
            e["group"] = max(0, cur_g)
    return effects


def parse_transition(slide_el):
    tel = None
    for el in slide_el.iter():
        if el.tag.endswith("}transition"):
            tel = el
            break
    if tel is None:
        return None
    dur = tel.get(q("p14:dur"))
    try:
        dur = int(dur)
    except (TypeError, ValueError):
        dur = {"slow": 1000, "fast": 300}.get(tel.get("spd", ""), 600)
    dur = max(150, min(1500, dur))
    child = None
    for ch in tel:
        child = ch
        break
    if child is None:
        return {"type": "none", "dir": "", "dur": 0}
    name = child.tag.split("}")[1]
    dirn = child.get("dir", "") or ""
    typ = "fade"
    if name in ("none", "cut"):
        typ = "none"
    elif name in ("push", "wipe", "cover"):
        typ = name
    return {"type": typ, "dir": dirn, "dur": dur}


# ---------------- 形状提取 ----------------

def _rgb(color_format, default=None):
    try:
        v = color_format.rgb
        if v is not None:
            return "#" + str(v)
    except Exception:
        pass
    return default


# 预设几何(normalized 0-1 多边形顶点): 教学常用形状; 其余走 crop
PRESETS = {
    "diamond": [(50, 0), (100, 50), (50, 100), (0, 50)],
    "parallelogram": [(25, 0), (100, 0), (75, 100), (0, 100)],
    "trapezoid": [(25, 0), (75, 0), (100, 100), (0, 100)],
    "triangle": [(50, 0), (100, 100), (0, 100)],
    "rtTriangle": [(0, 0), (0, 100), (100, 100)],
    "pentagon": [(50, 0), (100, 38), (81, 100), (19, 100), (0, 38)],
    "hexagon": [(25, 0), (75, 0), (100, 50), (75, 100), (25, 100), (0, 50)],
    "octagon": [(29, 0), (71, 0), (100, 29), (100, 71), (71, 100), (29, 100), (0, 71), (0, 29)],
    "star5": [(50, 0), (61, 35), (98, 35), (68, 57), (79, 91), (50, 70), (21, 91), (32, 57), (2, 35), (39, 35)],
    "chevron": [(0, 0), (75, 0), (100, 50), (75, 100), (0, 100), (25, 50)],
    "homePlate": [(0, 0), (75, 0), (100, 50), (75, 100), (0, 100)],
    "rightArrow": [(0, 35), (65, 35), (65, 12), (100, 50), (65, 88), (65, 65), (0, 65)],
    "leftArrow": [(100, 35), (35, 35), (35, 12), (0, 50), (35, 88), (35, 65), (100, 65)],
    "upArrow": [(35, 100), (35, 35), (12, 35), (50, 0), (88, 35), (65, 35), (65, 100)],
    "downArrow": [(35, 0), (65, 0), (65, 65), (88, 65), (50, 100), (12, 65), (35, 65)],
    "plus": [(35, 0), (65, 0), (65, 35), (100, 35), (100, 65), (65, 65), (65, 100), (35, 100), (35, 65), (0, 65), (0, 35), (35, 35)],
}


def _ea_typeface(run):
    rPr = run._r.find(q("a:rPr"))
    if rPr is None:
        return None
    ea = rPr.find(q("a:ea"))
    return ea.get("typeface") if ea is not None else None


_PH_SIZE = {"title": 40, "ctrTitle": 40, "subTitle": 24, "body": 24, "object": 24}


def _default_size(shape):
    try:
        if shape.is_placeholder:
            t = shape.placeholder_format.type
            for k, v in _PH_SIZE.items():
                if k.lower() in str(t).lower():
                    return v
    except Exception:
        pass
    return 18


def _geom(shape):
    try:
        vals = [shape.left, shape.top, shape.width, shape.height]
        if None not in vals:
            return tuple(int(v) for v in vals)
        if shape.is_placeholder:
            idx = shape.placeholder_format.idx
            for lph in shape.slide_layout.placeholders:
                if lph.placeholder_format.idx == idx:
                    vals = [vals[i] if vals[i] is not None else
                            [lph.left, lph.top, lph.width, lph.height][i]
                            for i in range(4)]
                    if None not in vals:
                        return tuple(int(v) for v in vals)
    except Exception:
        pass
    return None


_ALIGN = {PP_ALIGN.CENTER: "ctr", PP_ALIGN.RIGHT: "r", PP_ALIGN.JUSTIFY: "j"}
_ANCHOR = {MSO_ANCHOR.MIDDLE: "ctr", MSO_ANCHOR.BOTTOM: "b"}


def _paras(tf, default_sz, bullets=False):
    out = []
    try:
        for p in tf.paragraphs:
            pr = {"align": _ALIGN.get(p.alignment, "l"), "runs": []}
            # 项目符号/缩进(v1.15): 显式 buNone→无; buChar/buAutoNum→有; 占位符默认•
            pPr = p._p.find(q("a:pPr"))
            bu = None
            marL = ind = 0
            if pPr is not None:
                try:
                    marL = int(pPr.get("marL") or 0)
                    ind = int(pPr.get("indent") or 0)
                except (TypeError, ValueError):
                    pass
                if pPr.find(q("a:buNone")) is not None:
                    bu = ""
                else:
                    bc = pPr.find(q("a:buChar"))
                    ban = pPr.find(q("a:buAutoNum"))
                    if bc is not None:
                        bu = bc.get("char") or "\u2022"
                    elif ban is not None:
                        bu = "num"
            if bu is None and bullets:
                bu = "\u2022"
                if not marL:
                    marL, ind = 342900, -342900
            pr["bu"] = bu
            pr["marL"] = marL
            pr["ind"] = ind
            ls = p.line_spacing
            if isinstance(ls, float) and ls > 0:
                pr["lnSpc"] = round(ls, 3)
            else:
                try:
                    if ls is not None and hasattr(ls, "pt"):
                        pr["lnSpcPt"] = round(ls.pt, 1)
                except Exception:
                    pass
            try:
                if p.space_before is not None:
                    pr["sb"] = round(p.space_before.pt, 1)
            except Exception:
                pass
            try:
                if p.space_after is not None:
                    pr["sa"] = round(p.space_after.pt, 1)
            except Exception:
                pass
            runs = list(p.runs)
            if not runs:
                pr["runs"].append({"t": "", "sz": default_sz, "b": False,
                                   "i": False, "c": None, "f": None, "fe": None})
            for r in runs:
                sz = r.font.size.pt if r.font.size else default_sz
                fn = r.font.name
                if fn in ("+mn-lt", "+mj-lt", "+mn-ea", "+mj-ea"):
                    fn = None
                pr["runs"].append({
                    "t": r.text or "",
                    "sz": sz,
                    "b": True if r.font.bold else False,
                    "i": True if r.font.italic else False,
                    "c": _rgb(r.font.color),
                    "f": fn,
                    "fe": _ea_typeface(r),
                })
            out.append(pr)
    except Exception:
        pass
    return out


def _text_meta(sh):
    """文本框通用元数据: insets/anchor/nowrap/autofit缩放."""
    meta = {}
    tf = sh.text_frame
    try:
        meta["ins"] = [int(tf.margin_left if tf.margin_left is not None else 91440),
                       int(tf.margin_top if tf.margin_top is not None else 45720),
                       int(tf.margin_right if tf.margin_right is not None else 91440),
                       int(tf.margin_bottom if tf.margin_bottom is not None else 45720)]
    except Exception:
        meta["ins"] = [91440, 45720, 91440, 45720]
    try:
        meta["anchor"] = _ANCHOR.get(tf.vertical_anchor, "t")
    except Exception:
        meta["anchor"] = "t"
    try:
        if tf.word_wrap is False:
            meta["nowrap"] = 1
    except Exception:
        pass
    try:
        na = tf._txBody.find(q("a:bodyPr")).find(q("a:normAutofit"))
        if na is not None and na.get("fontScale"):
            meta["fitScale"] = int(na.get("fontScale")) / 100000.0
    except Exception:
        pass
    return meta


_SYSCOLOR = {"windowText": "#000000", "window": "#FFFFFF", "ButtonText": "#000000"}


def _solid_fill_hex(rpr_el):
    """rPr/defRPr 元素 → '#RRGGBB' 或 None."""
    if rpr_el is None:
        return None
    sf = rpr_el.find(q("a:solidFill"))
    if sf is None:
        return None
    srgb = sf.find(q("a:srgbClr"))
    if srgb is not None and srgb.get("val"):
        return "#" + srgb.get("val")
    sysc = sf.find(q("a:sysClr"))
    if sysc is not None:
        return _SYSCOLOR.get(sysc.get("val"),
                             "#" + (sysc.get("lastClr") or "000000"))
    return None


def _is_body_ph(shape):
    try:
        if shape.is_placeholder:
            t = str(shape.placeholder_format.type).upper()
            return "BODY" in t or "OBJ" in t
    except Exception:
        pass
    return False


def _ph_defaults(shape):
    """占位符 → 从版式/母版继承 (颜色, 字号pt). best-effort."""
    color = size = None
    try:
        if not shape.is_placeholder:
            return None, None
        idx = shape.placeholder_format.idx
        typ = str(shape.placeholder_format.type or "")
        slide = shape.part.slide
        layout = slide.slide_layout
        master = getattr(layout, "slide_master", None)
        cands = []
        for src in (layout, master):
            if src is None:
                continue
            for lph in src.placeholders:
                try:
                    if lph.placeholder_format.idx != idx:
                        continue
                except Exception:
                    continue
                tx = lph._element.find(q("p:txBody"))
                if tx is None:
                    continue
                ls = tx.find(q("a:lstStyle"))
                if ls is not None:
                    lvl = ls.find(q("a:lvl1pPr"))
                    if lvl is not None:
                        cands.append(lvl.find(q("a:defRPr")))
        if master is not None:
            ts = master._element.find(q("p:txStyles"))
            if ts is not None:
                tu = typ.upper()
                key = "titleStyle" if "TITLE" in tu else (
                    "bodyStyle" if ("BODY" in tu or "OBJ" in tu) else "otherStyle")
                st = ts.find(q("p:" + key))
                if st is not None:
                    lvl = st.find(q("a:lvl1pPr"))
                    if lvl is not None:
                        cands.append(lvl.find(q("a:defRPr")))
        for d in cands:
            if color is None:
                color = _solid_fill_hex(d)
            if size is None and d is not None and d.get("sz"):
                try:
                    size = int(d.get("sz")) / 100.0
                except (TypeError, ValueError):
                    pass
            if color and size:
                break
    except Exception:
        pass
    return color, size


def _has_omml(el):
    for x in el.iter():
        t = x.tag
        if t.endswith("}oMath") or t.endswith("}oMathPara"):
            return True
    return False


def _fill_info(sh):
    """返回 (fill_color or None, ok)  ok=False → 渐变/图案/图片填充(前端走 crop)."""
    try:
        ft = sh.fill.type
        if ft == MSO_FILL.SOLID:
            return _rgb(sh.fill.fore_color), True
        if ft in (None, MSO_FILL.BACKGROUND):
            return None, True
    except Exception:
        return None, True
    return None, False


def _line_info(sh):
    try:
        lw = sh.line.width.pt if sh.line.width else 0
        lc = _rgb(sh.line.color) if lw else None
        return lc, round(lw, 2)
    except Exception:
        return None, 0


def _save_media(media_dir, media_out, blob, spid):
    ext = "png"
    name = "%s_%s.%s" % (spid, hashlib.sha1(blob).hexdigest()[:8], ext)
    if media_dir is not None:
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / name).write_bytes(blob)
    if media_out is not None:
        media_out.add(name)
    return name


def _table_info(sh, media_dir, media_out):
    """表格 → HTML 表格元素数据(公式单元格→图片)."""
    tbl = sh.table
    info = {"kind": "table"}
    try:
        info["cols"] = [int(c.width or 0) for c in tbl.columns]
    except Exception:
        info["cols"] = []
    rows = []
    omml_img = None
    try:
        from . import omml_img
    except Exception:
        try:
            import omml_img
        except Exception:
            omml_img = None
    for ri in range(len(tbl.rows)):
        row = {"h": 0, "cells": []}
        try:
            row["h"] = int(tbl.rows[ri].height or 0)
        except Exception:
            pass
        for ci in range(len(tbl.columns)):
            try:
                cell = tbl.cell(ri, ci)
            except Exception:
                continue
            if cell.is_spanned:
                continue
            cd = {"cs": int(cell.span_width or 1), "rs": int(cell.span_height or 1)}
            try:
                if cell.fill.type == MSO_FILL.SOLID:
                    cd["fill"] = _rgb(cell.fill.fore_color)
            except Exception:
                pass
            try:
                cd["ins"] = [int(cell.margin_left if cell.margin_left is not None else 91440),
                             int(cell.margin_top if cell.margin_top is not None else 45720),
                             int(cell.margin_right if cell.margin_right is not None else 91440),
                             int(cell.margin_bottom if cell.margin_bottom is not None else 45720)]
            except Exception:
                cd["ins"] = [91440, 45720, 91440, 45720]
            try:
                cd["anchor"] = _ANCHOR.get(cell.vertical_anchor, "ctr")
            except Exception:
                cd["anchor"] = "ctr"
            # 边框
            borders = {}
            try:
                tcPr = cell._tc.find(q("a:tcPr"))
                if tcPr is not None:
                    for tag, side in (("lnL", "l"), ("lnR", "r"), ("lnT", "t"), ("lnB", "b")):
                        ln = tcPr.find(q("a:" + tag))
                        if ln is None:
                            continue
                        if ln.find(q("a:noFill")) is not None:
                            borders[side] = {"w": 0, "c": None}
                            continue
                        w = ln.get("w")
                        c = None
                        srgb = ln.find(q("a:solidFill") + "/" + q("a:srgbClr"))
                        if srgb is None:
                            srgb = ln.find(".//" + q("a:srgbClr"))
                        if srgb is not None:
                            c = "#" + srgb.get("val")
                        try:
                            wv = round(int(w) / 12700.0, 2)  # EMU→pt
                        except (TypeError, ValueError):
                            wv = 0.75
                        borders[side] = {"w": max(0.5, wv), "c": c or "#555555"}
            except Exception:
                pass
            if borders:
                cd["bd"] = borders
            # 文本 / 公式
            tf = cell.text_frame
            if _has_omml(cell._tc):
                rendered = False
                if omml_img is not None:
                    try:
                        txb = cell._tc.find(q("a:txBody"))
                        if txb is None:
                            txb = cell._tc.find(q("p:txBody"))
                        png, emu_w, emu_h = omml_img.render_txbody(txb)
                        if png:
                            cd["img"] = _save_media(media_dir, media_out, png,
                                                     "%d_%d_%d" % (sh.shape_id, ri, ci))
                            cd["iw"] = emu_w
                            cd["ih"] = emu_h
                            rendered = True
                    except Exception:
                        pass
                if not rendered:
                    cd["paras"] = _paras(tf, 16)
            else:
                cd["paras"] = _paras(tf, 16)
            row["cells"].append(cd)
        rows.append(row)
    info["rows"] = rows
    info["tborder"] = 1  # 无显式边框时前端给默认细边框
    return info


def _shape_info(sh, media_dir, media_out):
    """顶层形状 → 播放元素描述. kind: text/pic/rect/ellipse/shape/table/crop"""
    info = {"spid": str(sh.shape_id), "kind": "crop", "kill": str(sh.shape_id)}
    g = _geom(sh)
    if g is not None:
        info["geo"] = [g[0], g[1], g[2], g[3]]
    try:
        info["rot"] = round(sh.rotation or 0, 1)
    except Exception:
        info["rot"] = 0
    st = sh.shape_type

    if st == MSO_SHAPE_TYPE.PICTURE:
        try:
            blob = sh.image.blob
            ext = (sh.image.ext or "png").lower()
            name = "%s_%s.%s" % (info["spid"], hashlib.sha1(blob).hexdigest()[:8], ext)
            if media_dir is not None:
                media_dir.mkdir(parents=True, exist_ok=True)
                (media_dir / name).write_bytes(blob)
            if media_out is not None:
                media_out.add(name)
            info["kind"] = "pic"
            info["img"] = name
            return info
        except Exception:
            return info

    if st == MSO_SHAPE_TYPE.GROUP:
        info["isgrp"] = 1
        return info  # 整组截图

    if st == MSO_SHAPE_TYPE.AUTO_SHAPE:
        prst = None
        try:
            pg = sh._element.spPr.find(q("a:prstGeom"))
            if pg is not None:
                prst = pg.get("prst")
        except Exception:
            pass
        fill, ok = _fill_info(sh)
        if not ok:
            return info  # 渐变/图案填充 → 截图
        if prst == "rect":
            kind = "rect"
        elif prst == "ellipse":
            kind = "ellipse"
        elif prst == "roundRect":
            kind = "shape"
            info["rr"] = 1
        elif prst in PRESETS:
            kind = "shape"
            info["pts"] = PRESETS[prst]
        else:
            return info  # 其余形状 → 截图
        info["kind"] = kind
        info["fill"] = fill
        lc, lw = _line_info(sh)
        info["line"] = lc
        info["lw"] = lw
        if sh.has_text_frame and (sh.text_frame.text or "").strip():
            info.update(_text_meta(sh))
            info["paras"] = _paras(sh.text_frame, 18)
        return info

    if st == MSO_SHAPE_TYPE.TEXT_BOX or sh.has_text_frame:
        # 文本框/占位符; 残留 OMML(预处理失败)→ 尝试整体渲染为图片, 不行则截图
        if _has_omml(sh._element):
            try:
                try:
                    from . import omml_img
                except Exception:
                    import omml_img
                png, emu_w, emu_h = omml_img.render_txbody(
                    sh._element.find(q("p:txBody")))
                if png:
                    info["kind"] = "pic"
                    info["img"] = _save_media(media_dir, media_out, png, info["spid"])
                    info["iw"] = emu_w
                    info["ih"] = emu_h
                    info["natural"] = 1
                    return info
            except Exception:
                pass
            return info
        info["kind"] = "text"
        tc, ph_sz = _ph_defaults(sh)
        if tc:
            info["tc"] = tc
        info.update(_text_meta(sh))
        info["paras"] = _paras(sh.text_frame, ph_sz or _default_size(sh),
                               bullets=_is_body_ph(sh))
        return info

    if getattr(sh, "has_table", False) and sh.has_table:
        info.update(_table_info(sh, media_dir, media_out))
        return info

    return info  # 图表/SmartArt/连接线/OLE → 截图


# ---------------- 清单构建 ----------------

def build_manifest(pptx_path, media_dir=None, media_out=None):
    prs = Presentation(str(pptx_path))
    sw = int(prs.slide_width)
    shh = int(prs.slide_height)
    pages = []
    for i, slide in enumerate(prs.slides):
        page = {"n": i, "mode": "static", "elements": [],
                "transition": parse_transition(slide._element)}
        effects = parse_timing(slide._element)
        shapes = []
        for sh in slide.shapes:
            try:
                si = _shape_info(sh, media_dir, media_out)
            except Exception:
                continue
            if si is None:
                continue
            shapes.append(si)

        by_spid = {}
        for e in effects:
            by_spid.setdefault(e["spid"], []).append(e)

        # 组合形状: 子形状被动画 → 整组一个 crop 元素(首个被动画子形状为代表)
        group_shapes = {}   # 子spid -> 组spid
        group_infos = {}    # 组spid -> 组形状 info
        for si in shapes:
            if si.get("isgrp"):
                group_infos[si["spid"]] = si
        for sh in slide.shapes:
            try:
                if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
                    for ch in sh.shapes:
                        group_shapes[str(ch.shape_id)] = str(sh.shape_id)
            except Exception:
                pass

        def make_element(spid, si, effs, kill_id):
            geo = si.get("geo")
            if geo is None:
                return None  # 无几何: 留背景层(不抠), 效果丢弃
            el = {"spid": spid}
            el["kind"] = si["kind"]
            el["x"] = round(geo[0] / sw, 5)
            el["y"] = round(geo[1] / shh, 5)
            el["w"] = round(geo[2] / sw, 5)
            el["h"] = round(geo[3] / shh, 5)
            for k in ("fill", "line", "lw", "pts", "rr", "img", "iw", "ih",
                      "paras", "ins", "anchor", "nowrap", "fitScale", "cols", "rows",
                      "tborder"):
                if k in si:
                    el[k] = si[k]
            if si.get("rot") and si["kind"] != "crop":
                el["rot"] = si["rot"]
            if effs:
                steps = []
                for e in effs:
                    t = "in" if e["cls"] in ("entr", "") else ("out" if e["cls"] == "exit" else "pulse")
                    st = {"t": t, "e": e["kind"], "dir": e["dir"],
                          "dur": e["dur"], "delay": e["delay"], "g": e["group"]}
                    if "scale" in e:
                        st["scale"] = e["scale"]
                    if "degraded" in e:
                        st["degraded"] = 1
                    steps.append(st)
                el["steps"] = steps
            return el

        # v1.15 全页元素化: 每个形状都是元素(有动画带 steps, 无动画常驻显示)
        elements = []
        kill = []
        matched = set()
        for si in shapes:
            if si.get("isgrp"):
                continue
            spid = si["spid"]
            effs = by_spid.get(spid)
            # 空文本框(无文字无几何意义)跳过但仍抠出, 保持背景干净
            el = make_element(spid, si, effs, si.get("kill", spid))
            if el is None:
                if si.get("geo") is not None:
                    kill.append(si.get("kill", spid))
                continue
            if effs:
                matched.add(spid)
            kill.append(si.get("kill", spid))
            elements.append(el)

        # 组合: 被动画的子形状 → 组 crop 元素(带 steps); 其余组 → 常驻 crop 元素
        used_gids = set()
        for spid in by_spid:
            if spid in matched:
                continue
            gid = group_shapes.get(spid)
            if not gid or gid in used_gids:
                continue
            gsi = group_infos.get(gid)
            if gsi is None:
                continue
            el = make_element(spid, gsi, by_spid[spid], gid)
            if el is None:
                continue
            used_gids.add(gid)
            kill.append(gid)
            elements.append(el)
        for gid, gsi in group_infos.items():
            if gid in used_gids:
                continue
            el = make_element(gid, gsi, None, gid)
            if el is not None:
                kill.append(gid)
                elements.append(el)

        if elements:
            page["mode"] = "elements"
            page["elements"] = elements
            page["kill"] = kill
        pages.append(page)
    return {"v": 3, "slideW": sw, "slideH": shh, "pages": pages}


# ---------------- 背景PDF(删除动画元素) ----------------

def _soffice():
    return shutil.which("soffice") or shutil.which("libreoffice")


def _strip_shapes(src, dst, kill):
    """生成删除指定形状(spid, 含组合/表格框)的副本."""
    prs = Presentation(str(src))
    for i, slide in enumerate(prs.slides):
        ids = kill.get(i)
        if not ids:
            continue
        tree = slide.shapes._spTree
        for el in list(tree):
            cnv = el.find("./*/" + q("p:cNvPr"))
            if cnv is not None and cnv.get("id") in ids:
                tree.remove(el)
    prs.save(str(dst))


def _pdf_pages(p):
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(p)).pages)
    except Exception:
        return 0


def build_and_store(src_path, outdir, ext=None, main_pages=None):
    src = Path(src_path)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    media_dir = outdir / "anim_media"
    media_out = set()

    work = src
    tmp_pptx = None
    if src.suffix.lstrip(".").lower() in ("ppt", "pps", "pot"):
        tmp_pptx = outdir / "_anim_conv.pptx"
        r = subprocess.run([_soffice(), "--headless", "--norestore", "--nologo", "--nolockcheck",
                            "-env:UserInstallation=file:///tmp/lo_anim", "--convert-to", "pptx",
                            "--outdir", str(outdir), str(src)],
                           capture_output=True, text=True, timeout=600)
        cand = outdir / (src.stem + ".pptx")
        if r.returncode != 0 or not cand.exists():
            if tmp_pptx.exists():
                tmp_pptx.unlink()
            return None
        cand.rename(tmp_pptx)
        work = tmp_pptx

    try:
        manifest = build_manifest(work, media_dir, media_out)
        if media_dir.exists():
            for f in media_dir.iterdir():
                if f.name not in media_out:
                    f.unlink()

        element_pages = [p for p in manifest["pages"] if p["mode"] == "elements"]
        bg_ok = False
        if element_pages:
            kill = {p["n"]: p.get("kill", []) for p in element_pages}
            tmp2 = outdir / "_anim_bg.pptx"
            _strip_shapes(work, tmp2, kill)
            try:
                subprocess.run([_soffice(), "--headless", "--norestore", "--nologo", "--nolockcheck",
                                "-env:UserInstallation=file:///tmp/lo_anim", "--convert-to", "pdf",
                                "--outdir", str(outdir), str(tmp2)],
                               capture_output=True, text=True, timeout=900)
                bgcand = outdir / "_anim_bg.pdf"
                if bgcand.exists() and _pdf_pages(bgcand) > 0:
                    bg_pages = _pdf_pages(bgcand)
                    bgcand.rename(outdir / "bg.pdf")
                    bg_ok = True
                    for p in manifest["pages"]:
                        if p.get("mode") == "elements" and p.get("n", 0) >= bg_pages:
                            p["bgfail"] = 1
            finally:
                if tmp2.exists():
                    tmp2.unlink()
        if not bg_ok:
            for p in manifest["pages"]:
                if p["mode"] == "elements":
                    p["bgfail"] = 1

        (outdir / "anim.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        has = bool(element_pages) or \
            any(p.get("transition") and p["transition"]["type"] != "none" for p in manifest["pages"])
        return manifest if has else None
    finally:
        if tmp_pptx is not None and tmp_pptx.exists():
            tmp_pptx.unlink()


if __name__ == "__main__":
    import sys
    src, out = sys.argv[1], sys.argv[2]
    m = build_and_store(src, out)
    print(json.dumps(m, ensure_ascii=False, indent=1) if m else "None")
