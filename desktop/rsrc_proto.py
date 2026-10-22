#!/usr/bin/env python3
"""PE 资源注入原型: 版本信息 + 图标 + 清单 + 校验和 (验证后并入 build_pe.py)"""
import struct
from collections import OrderedDict


def _align(v, a):
    return (v + a - 1) // a * a


def make_icon_entries():
    from PIL import Image, ImageDraw
    entries = []
    ordinals = []
    idx = 1
    for s in (16, 24, 32, 48, 64, 128, 256):
        im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=max(2, int(s * 0.22)), fill=(9, 9, 11, 255))
        w = max(1, s // 26)
        bw, bh = int(s * 0.56), int(s * 0.38)
        x0 = (s - bw) // 2
        y0 = int(s * 0.18)
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=max(1, s // 20), outline=(255, 255, 255, 255), width=w)
        ly = y0 + bh
        cx = s // 2
        d.line([cx, ly, cx, min(s - 2, ly + int(s * 0.18))], fill=(255, 255, 255, 255), width=w)
        d.line([cx - int(s * 0.14), min(s - 2, ly + int(s * 0.18)), cx + int(s * 0.14), min(s - 2, ly + int(s * 0.18))],
               fill=(255, 255, 255, 255), width=w)
        px = im.load()
        # PNG 压缩 (Vista+ 完整支持, 同一图案从 ~350KB 压到 ~10KB)
        import io
        bio = io.BytesIO()
        im.save(bio, format='PNG')
        blob = bio.getvalue()
        entries.append([3, idx, 0x409, blob])
        ordinals.append((s, s, len(blob), idx))
        idx += 1
    grp = struct.pack('<HHH', 0, 1, len(ordinals))
    for w, h, size, oid in ordinals:
        grp += struct.pack('<BBBBHHIH', w % 256, h % 256, 0, 0, 0, 0, size, oid)
    entries.append([14, 1, 0x409, grp])
    return entries


def _blk(key, value, wtype, children=b''):
    keyb = key.encode('utf-16-le') + b'\x00\x00'
    pad = (4 - ((6 + len(keyb)) % 4)) % 4
    body = keyb + b'\x00' * pad + value
    childpad = (4 - (len(body) % 4)) % 4
    total = 6 + len(keyb) + pad + len(value) + childpad + len(children)
    return struct.pack('<HHH', total, len(value), wtype) + body + b'\x00' * childpad + children


def make_version_entry(ver=(1, 37, 0)):
    fw = (ver[0] << 16) | ver[1]
    fixed = struct.pack('<IIIIIIIIIIIII',
                        0xFEEF04BD, 0x10000, fw, ver[2], fw, ver[2], 0x3F, 0, 0x40004, 1, 0, 0, 0)
    strings = [
        ('CompanyName', 'CLRV-FYX'),
        ('FileDescription', 'FLA Desktop Assistant'),
        ('FileVersion', '%d.%d.%d' % ver),
        ('InternalName', 'FLA'),
        ('LegalCopyright', 'CLRV-FYX. All rights reserved.'),
        ('OriginalFilename', 'FLA.exe'),
        ('ProductName', 'FLA Desktop Assistant'),
        ('ProductVersion', '%d.%d.%d' % ver),
    ]
    tbl = b''
    for k, v in strings:
        tbl += _blk(k, v.encode('utf-16-le') + b'\x00\x00', 1)
    sfi = _blk('StringFileInfo', b'', 1, _blk('040904b0', b'', 1, tbl))
    return [16, 1, 0x409, _blk('VS_VERSION_INFO', fixed, 0, sfi)]


def make_manifest_entry(ver=(1, 37, 0)):
    vstr = '%d.%d.%d.0' % ver
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">\n'
        '<assemblyIdentity version="' + vstr + '" processorArchitecture="*" name="FLA.Desktop.Assistant" type="win32"/>\n'
        '<description>FLA Desktop Assistant</description>\n'
        '<trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges>'
        '<requestedExecutionLevel level="asInvoker" uiAccess="false"/></requestedPrivileges></security></trustInfo>\n'
        '<compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1"><application>'
        '<supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>'
        '<supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"/>'
        '<supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}"/>'
        '<supportedOS Id="{35138b9a-5d96-4fbd-8e2d-a2440225f93a}"/>'
        '</application></compatibility>\n'
        '<asmv3:application xmlns:asmv3="urn:schemas-microsoft-com:asm.v3"><asmv3:windowsSettings>'
        '<dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true</dpiAware>'
        '</asmv3:windowsSettings></asmv3:application>\n'
        '</assembly>')
    return [24, 1, 0x409, xml.encode('ascii')]


def build_rsrc_section(entries, section_rva):
    """entries: [(type_id, name_id, lang_id, data)] → (section bytes, total rsrc dir size)"""
    types = OrderedDict()
    for t, n, l, d in entries:
        types.setdefault(t, OrderedDict()).setdefault(n, OrderedDict())[l] = d

    n_types = len(types)
    n_leaves = sum(len(v) for tv in types.values() for v in tv.values())
    root_sz = 16 + 8 * n_types

    # 布局计算
    off = root_sz
    name_dir_off = OrderedDict()
    lang_dir_off = OrderedDict()
    for t, names in types.items():
        name_dir_off[t] = off
        off += 16 + 8 * len(names)
        for n in names:
            lang_dir_off[(t, n)] = off
            off += 16 + 8
    de_base = off
    off += 16 * n_leaves
    blob_off = OrderedDict()
    for t, names in types.items():
        for n, langs in names.items():
            for l, d in langs.items():
                blob_off[(t, n, l)] = off
                off = _align(off + len(d), 4)

    buf = bytearray(off)
    # 根目录
    struct.pack_into('<IIHHHH', buf, 0, 0, 0, 0, 0, 0, n_types)
    for i, t in enumerate(types):
        struct.pack_into('<II', buf, 16 + i * 8, t, 0x80000000 | name_dir_off[t])
    # 各 type 的 name-dir
    for t, names in types.items():
        o = name_dir_off[t]
        struct.pack_into('<IIHHHH', buf, o, 0, 0, 0, 0, 0, len(names))
        for j, n in enumerate(names):
            struct.pack_into('<II', buf, o + 16 + j * 8, n, 0x80000000 | lang_dir_off[(t, n)])
    # lang-dir + data entry + blob
    leaf = 0
    for t, names in types.items():
        for n, langs in names.items():
            o = lang_dir_off[(t, n)]
            struct.pack_into('<IIHHHH', buf, o, 0, 0, 0, 0, 0, 1)
            (l, d), = langs.items()
            struct.pack_into('<II', buf, o + 16, l, de_base + 16 * leaf)
            struct.pack_into('<IIII', buf, de_base + 16 * leaf, section_rva + blob_off[(t, n, l)], len(d), 0, 0)
            buf[blob_off[(t, n, l)]:blob_off[(t, n, l)] + len(d)] = d
            leaf += 1
    dir_size = de_base
    return bytes(buf), dir_size


def write_checksum(data: bytearray, e_lfanew: int):
    csum_off = e_lfanew + 24 + 64
    struct.pack_into('<I', data, csum_off, 0)
    total = len(data)
    s = 0
    n = total - (total % 4)
    for i in range(0, n, 4):
        s += struct.unpack_from('<I', data, i)[0]
        s = (s & 0xFFFF) + (s >> 16)
    if total % 4:
        tail = data[n:] + b'\x00' * (4 - total % 4)
        s += struct.unpack('<I', tail)[0]
        s = (s & 0xFFFF) + (s >> 16)
    s = (s & 0xFFFF) + (s >> 16)
    s = (s + total) & 0xFFFFFFFF
    struct.pack_into('<I', data, csum_off, s)
    return s


def inject_resources(pe_bytes: bytes, ver=(1, 37, 0)):
    entries = make_icon_entries() + [make_version_entry(ver), make_manifest_entry(ver)]
    pe = bytearray(pe_bytes)
    e_lfanew = struct.unpack_from('<I', pe, 0x3C)[0]
    num_sec = struct.unpack_from('<H', pe, e_lfanew + 6)[0]
    opt_size = struct.unpack_from('<H', pe, e_lfanew + 20)[0]
    sh = e_lfanew + 24 + opt_size

    first_raw = min(
        (struct.unpack_from('<I', pe, sh + i * 40 + 20)[0] for i in range(num_sec)
         if struct.unpack_from('<I', pe, sh + i * 40 + 20)[0] > 0),
        default=0)
    if first_raw == 0 or sh + (num_sec + 1) * 40 > first_raw:
        raise RuntimeError('no room for an extra section header')

    max_rva = 0
    for i in range(num_sec):
        vs, va = struct.unpack_from('<II', pe, sh + i * 40 + 8)
        max_rva = max(max_rva, va + max(vs, struct.unpack_from('<I', pe, sh + i * 40 + 16)[0]))
    new_rva = _align(max_rva, 0x1000)

    rsrc, dir_size = build_rsrc_section(entries, new_rva)
    raw_off = _align(len(pe), 0x200)
    pe += b'\x00' * (raw_off - len(pe))
    padded = _align(len(rsrc), 0x200)

    hdr = struct.pack('<8sIIIIIIHHI', b'.rsrc', len(rsrc), new_rva, padded, raw_off, 0, 0, 0, 0, 0x40000040)
    pe[sh + num_sec * 40: sh + num_sec * 40 + 40] = hdr
    struct.pack_into('<H', pe, e_lfanew + 6, num_sec + 1)
    pe += rsrc + b'\x00' * (padded - len(rsrc))
    struct.pack_into('<I', pe, e_lfanew + 24 + 56, _align(new_rva + len(rsrc), 0x1000))
    struct.pack_into('<II', pe, e_lfanew + 136 + 2 * 8, new_rva, padded)
    write_checksum(pe, e_lfanew)
    return bytes(pe)


if __name__ == '__main__':
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else 'bin/FLA.exe'
    dst = sys.argv[2] if len(sys.argv) > 2 else 'bin/FLA_rsrc.exe'
    data = open(src, 'rb').read()
    out = inject_resources(data)
    open(dst, 'wb').write(out)
    print('wrote', dst, len(data), '->', len(out))

    import pefile
    pe = pefile.PE(dst)
    print('checksum valid:', pe.verify_checksum())
    print('sections:', [(s.Name.decode(errors='ignore').strip('\x00'), hex(s.VirtualAddress)) for s in pe.sections])
    if hasattr(pe, 'DIRECTORY_ENTRY_RESOURCE'):
        types_seen = []
        for e in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            types_seen.append(e.id)
        print('  rsrc types:', types_seen)
    try:
        vi = pe.FileInfo[0][0].StringTable[0].entries
        for k in (b'CompanyName', b'FileDescription', b'FileVersion', b'ProductName'):
            print('  ver', k.decode(), '=', vi.get(k))
    except Exception as ex:
        print('  version parse:', ex)
    print('imports:', [dll.dll.decode() for dll in pe.DIRECTORY_ENTRY_IMPORT])
    # icon 数据可用性 (Pillow 能否解码 RT_ICON)
    try:
        from PIL import Image
        import io
        found = 0
        for e in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if e.id == 3:
                for ne in e.directory.entries:
                    for le in ne.directory.entries:
                        img = Image.open(io.BytesIO(le.data.struct.Data))
                        found += 1
        print('icon images decodable:', found)
    except Exception as ex:
        print('icon decode:', ex)
