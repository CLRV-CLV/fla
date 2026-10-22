"""课堂工具辅助接口: 名单解析 (Excel / CSV / TXT)."""
import csv
import io
import os
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

router = APIRouter(prefix="/api/tools", tags=["tools"])


def _parse_xlsx(content: bytes) -> list[str]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except Exception:
        return []

    shared = []
    if "xl/sharedStrings.xml" in zf.namelist():
        try:
            tree = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in tree.findall("{*}si"):
                t = "".join(si.itertext()).strip()
                shared.append(t)
        except Exception:
            pass

    rows = []
    if "xl/worksheets/sheet1.xml" in zf.namelist():
        try:
            stree = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            for row_el in stree.findall(".//{*}row"):
                row_dict = {}
                for c in row_el.findall("{*}c"):
                    ref = c.attrib.get("r", "")
                    col_letters = "".join(ch for ch in ref if ch.isalpha())
                    col_i = 0
                    for ch in col_letters:
                        col_i = col_i * 26 + (ord(ch.upper()) - ord("A") + 1)
                    col_i -= 1
                    t_attr = c.attrib.get("t")
                    v_el = c.find("{*}v")
                    val = ""
                    if t_attr == "s" and v_el is not None and v_el.text:
                        idx = int(v_el.text)
                        if 0 <= idx < len(shared):
                            val = shared[idx]
                    elif v_el is not None and v_el.text:
                        val = v_el.text
                    if val:
                        row_dict[col_i] = val.strip()
                if row_dict:
                    max_c = max(row_dict.keys())
                    r_list = [row_dict.get(i, "") for i in range(max_c + 1)]
                    rows.append(r_list)
        except Exception:
            pass

    if not rows:
        return [s for s in shared if 1 < len(s) <= 20 and not any(k in s for k in ("姓名", "学号", "Sheet", "Table"))]

    header = rows[0]
    col_idx = 0
    found = False
    for idx, h in enumerate(header):
        if any(k in h for k in ("姓名", "名字", "学生", "name", "Name", "NAME")):
            col_idx = idx
            found = True
            break
    if not found:
        for idx in range(len(header)):
            col_vals = [r[idx] for r in rows[1:] if len(r) > idx]
            if any(v and not v.replace(".", "").isdigit() for v in col_vals):
                col_idx = idx
                break

    start = 1 if len(rows) > 1 and any(k in header[col_idx] for k in ("姓名", "名字", "学生", "name", "学号", "id")) else 0
    names = []
    for r in rows[start:]:
        if col_idx < len(r) and r[col_idx]:
            val = r[col_idx].strip()
            if val and len(val) <= 40:
                names.append(val)
    return names


def _parse_csv(content: bytes) -> list[str]:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin1"):
        try:
            text = content.decode(enc)
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if not lines:
                continue
            rows = []
            for ln in lines:
                parts = [p.strip().strip("\"'").strip() for p in ln.replace("\t", ",").split(",") if p.strip()]
                if parts:
                    rows.append(parts)
            if not rows:
                continue
            header = rows[0]
            col_idx = 0
            found = False
            for idx, h in enumerate(header):
                if any(k in h for k in ("姓名", "名字", "学生", "name", "Name", "NAME")):
                    col_idx = idx
                    found = True
                    break
            if not found:
                for idx in range(len(header)):
                    col_vals = [r[idx] for r in rows[1:] if len(r) > idx]
                    if any(v and not v.replace(".", "").isdigit() for v in col_vals):
                        col_idx = idx
                        break
            start = 1 if len(rows) > 1 and any(k in header[col_idx] for k in ("姓名", "名字", "学生", "name", "学号", "id")) else 0
            names = []
            for r in rows[start:]:
                if col_idx < len(r) and r[col_idx]:
                    val = r[col_idx].strip()
                    if val and len(val) <= 40:
                        names.append(val)
            if names:
                return names
        except Exception:
            continue
    return []


@router.post("/parse-roster")
async def parse_roster(file: UploadFile = File(...)):
    """解析上传的学生名单表格 (.xlsx, .csv, .txt)."""
    filename = (file.filename or "").lower()
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件过大，请上传 10MB 以内的名单文件")

    names = []
    if filename.endswith(".xlsx"):
        names = _parse_xlsx(content)
    else:
        names = _parse_csv(content)

    # 去重并保持顺序
    seen = set()
    deduped = []
    for n in names:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            deduped.append(n)

    return {"ok": True, "count": len(deduped), "names": deduped}


@router.get("/download-desktop")
def download_desktop():
    """提供桌面客户端供教师或管理员直接下载使用 (返回单文件可执行程序 FLA.exe)."""
    from server.desktop_dist import ensure_desktop_exe
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

