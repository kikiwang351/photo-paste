#!/usr/bin/env python3
"""
照片黏貼表自動填入工具 v5
By Kiki
"""

import os, sys, shutil, zipfile, copy, re, threading, tempfile
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from lxml import etree
from PIL import Image, ImageTk, ImageDraw, ImageOps
import tkinter as tk
from tkinter import filedialog, messagebox

# ─────────────────────────────────────────────────────────────────────
import platform
IS_MAC = platform.system() == 'Darwin'

VERSION = "1.6"
GITHUB_REPO = "kikiwang351/photo-paste"

def check_for_update(root):
    """背景檢查是否有新版本，有的話跳出提示"""
    import urllib.request, json

    def _check():
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "photo-paste-updater"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            latest = data.get("tag_name", "").lstrip("v")
            if not latest or latest <= VERSION:
                return
            exe_url = next((a["browser_download_url"] for a in data.get("assets", [])
                            if a["name"].endswith(".exe")), None)
            if exe_url:
                root.after(0, lambda: _prompt_update(latest, exe_url))
        except Exception:
            pass  # 網路失敗就靜默跳過，不影響正常使用

    threading.Thread(target=_check, daemon=True).start()

def _prompt_update(latest, exe_url):
    import webbrowser
    ans = messagebox.askyesno(
        "🌿 發現新版本！",
        f"目前版本：v{VERSION}\n最新版本：v{latest}\n\n點「是」開啟下載頁面，下載後覆蓋舊檔案即可完成更新。"
    )
    if ans:
        webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")

DEFAULT_LOCATION = "地點：臺中市西屯區文心路二段588號(偵查第八隊辦公室)"
COLS = 3  # 每排3欄，一次看更多

def fix_ime_entry(entry):
    """修正中文輸入法無法輸入標點符號的問題
    根據測試：中文輸入法送來的符號 keysym 是亂碼，但 char 是正確字元
    策略：char 有值但 keysym 對不上時，直接插入 char
    """
    # 正常英數鍵的 keysym 對照（這些不需要特殊處理）
    NORMAL_KEYSYMS = {
        'period','comma','slash','semicolon','colon','apostrophe','quotedbl',
        'exclam','question','parenleft','parenright','bracketleft','bracketright',
        'braceleft','braceright','at','numbersign','dollar','percent','asciicircum',
        'ampersand','asterisk','minus','plus','equal','less','greater','asciitilde',
        'grave','backslash','bar','space','underscore',
    }
    def on_key(e):
        ch = e.char
        # 沒有字元，或是正常可見 ASCII，或 keysym 是正常鍵，都不處理
        if not ch: return
        # 長度 > 1 的 char 是組合字（如中文字），tkinter 會正常處理
        if len(ch) > 1: return
        # 如果 keysym 是正常英數鍵名，不處理
        if e.keysym in NORMAL_KEYSYMS: return
        # 可列印字元且 keysym 不對應（輸入法送來的符號）
        if ch.isprintable() and e.keysym not in (ch, f'U{ord(ch):04X}'):
            try:
                try:
                    sel_start = entry.index(tk.SEL_FIRST)
                    sel_end   = entry.index(tk.SEL_LAST)
                    entry.delete(sel_start, sel_end)
                    idx = sel_start
                except tk.TclError:
                    idx = entry.index(tk.INSERT)
                entry.insert(idx, ch)
                entry.icursor(idx + 1)
            except Exception:
                pass
            return "break"
    entry.bind("<KeyPress>", on_key, add=True)
    return entry


# 縮圖快取（記憶體）：key=路徑+mtime+尺寸, value=PIL Image bytes
_thumb_cache = {}
_thumb_lock  = threading.Lock()
_load_pool   = ThreadPoolExecutor(max_workers=4)

def get_thumb(path, max_w, max_h):
    """快取縮圖：相同檔案+尺寸只處理一次"""
    try:
        st  = os.stat(path)
        key = f"{path}|{st.st_size}|{st.st_mtime}|{max_w}|{max_h}"
        with _thumb_lock:
            if key in _thumb_cache:
                return _thumb_cache[key].copy()
        img = Image.open(path).convert("RGB")
        img.thumbnail((max_w, max_h), Image.LANCZOS)
        with _thumb_lock:
            if len(_thumb_cache) > 300:
                # 清掉最舊的 100 筆
                keys = list(_thumb_cache.keys())[:100]
                for k in keys: del _thumb_cache[k]
            _thumb_cache[key] = img.copy()
        return img
    except Exception:
        return Image.new("RGB", (min(max_w,4), min(max_h,3)), (220,225,210))


# 皮克敏配色
C = {
    "bg":         "#f0f4e8",
    "bg2":        "#e8edd8",
    "bg3":        "#dde5c8",
    "topbar":     "#4a7c3f",
    "topbar2":    "#5a9249",
    "btnbar":     "#c8dba0",
    "editbar":    "#d4e8b0",
    "btn_green":  "#4a7c3f",
    "btn_brown":  "#7d5a3c",
    "btn_blue":   "#3a6fa8",
    "btn_purple": "#7b4fa8",
    "btn_red":    "#c0392b",
    "btn_gray":   "#8a9a78",
    "btn_dark":   "#5a6a4a",
    "text":       "#2c3e1a",
    "text2":      "#4a5a3a",
    "subtext":    "#6a7a5a",
    "card":       "#ffffff",
    "card_sel":   "#c8e6a0",
    "card_border":"#a0b878",
    "log_bg":     "#2c3e1a",
    "log_fg":     "#a8e06a",
    "entry_bg":   "#ffffff",
    "entry_bd":   "#a0b878",
    "right_bg":   "#eef2e0",
    "sep":        "#b0c890",
    # 向下相容舊引用
    "purple":     "#7b4fa8",
    "green":      "#4a7c3f",
    "red":        "#c0392b",
}

W   = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R   = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP  = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A   = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
REL_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
PKG_REL   = "http://schemas.openxmlformats.org/package/2006/relationships"
EMU_PER_DXA = 635


# ─────────────────────────────────────────────────────────────────────
# 圖片工具
# ─────────────────────────────────────────────────────────────────────

LABEL_CHARS = "abcdefghijklmnopqrstuvwxyz"

def stamp_label(src_path, label):
    """在圖片左上角加上 a/b/c 標示，回傳暫存 jpg 路徑"""
    from PIL import ImageFont
    img = Image.open(src_path).convert("RGB")
    w, h = img.size

    # 字體大小：短邊的 9%，最小 18px
    fsize = max(18, min(w, h) // 11)

    font = None
    for fname in ["arialbd.ttf", "Arial Bold.ttf", "Arial.ttf",
                  "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"]:
        try:
            font = ImageFont.truetype(fname, fsize)
            break
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()

    # 量文字實際寬高
    tmp_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    try:
        bb = tmp_draw.textbbox((0, 0), label, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
    except Exception:
        tw = th = fsize

    pad = max(5, fsize // 5)
    margin = pad

    # 用疊圖方式做半透明底色
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle(
        [margin, margin, margin + tw + pad * 2, margin + th + pad * 2],
        fill=(20, 20, 20, 190)
    )
    img_rgba = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(img_rgba)
    draw.text((margin + pad, margin + pad), label, fill=(255, 255, 255), font=font)

    out = tempfile.mktemp(suffix=".jpg")
    img_rgba.save(out, "JPEG", quality=95)
    return out


def merge_images(paths, layout, out_path):
    """合併多張照片：layout = tb(上下) / lr(左右) / g4(四格2x2)"""
    imgs = [Image.open(p).convert("RGB") for p in paths]

    if layout == "lr" and len(imgs) >= 2:
        h = max(i.height for i in imgs[:2])
        resized = [i.resize((int(i.width*h/i.height), h), Image.LANCZOS) for i in imgs[:2]]
        out = Image.new("RGB", (sum(i.width for i in resized), h), (255,255,255))
        x = 0
        for im in resized: out.paste(im, (x, 0)); x += im.width

    elif layout == "g4" and len(imgs) >= 4:
        cw = max(i.width for i in imgs[:4])
        ch = max(i.height for i in imgs[:4])
        out = Image.new("RGB", (cw*2, ch*2), (255,255,255))
        positions = [(0,0),(cw,0),(0,ch),(cw,ch)]
        for im, (px,py) in zip(imgs[:4], positions):
            im_r = im.resize((cw, int(im.height*cw/im.width)), Image.LANCZOS)
            out.paste(im_r, (px, py))

    else:  # tb 上下（預設）
        w = max(i.width for i in imgs[:2])
        resized = [i.resize((w, int(i.height*w/i.width)), Image.LANCZOS) for i in imgs[:2]]
        out = Image.new("RGB", (w, sum(i.height for i in resized)), (255,255,255))
        y = 0
        for im in resized: out.paste(im, (0, y)); y += im.height

    out.save(out_path, "JPEG", quality=95)
    return out_path


def make_grid_subtable(rIds, img_paths, pic_counter_start, cols, rows,
                       cell_w_dxa=9074, cell_h_dxa=11330):
    """建立 cols x rows 子表格，每格獨立放一張圖，可在 Word 單獨調整大小"""
    CW = cell_w_dxa // cols
    CH = cell_h_dxa // rows

    # 計算統一顯示尺寸：每張各自縮放後，取最小的寬度作為統一寬度
    img_sizes = []
    for p in img_paths[:len(rIds)]:
        try:
            with Image.open(p) as im: img_sizes.append(im.size)
        except: img_sizes.append((3, 4))

    avail_w = CW * EMU_PER_DXA
    avail_h = CH * EMU_PER_DXA

    # 每張圖各自算出最大可放尺寸（各自填滿自己的格子，不統一縮小）
    each_sizes = []
    for iw, ih in img_sizes:
        ratio = min(avail_w / iw, avail_h / ih)
        each_sizes.append((int(iw * ratio), int(ih * ratio)))

    tbl = etree.Element(f"{{{W}}}tbl")
    tblPr = etree.SubElement(tbl, f"{{{W}}}tblPr")
    tblW_el = etree.SubElement(tblPr, f"{{{W}}}tblW")
    tblW_el.set(f"{{{W}}}w", str(cell_w_dxa)); tblW_el.set(f"{{{W}}}type", "dxa")
    etree.SubElement(tblPr, f"{{{W}}}tblLayout").set(f"{{{W}}}type", "fixed")
    tblCellMar = etree.SubElement(tblPr, f"{{{W}}}tblCellMar")
    for side in ("top","bottom","left","right"):
        m = etree.SubElement(tblCellMar, f"{{{W}}}{side}")
        m.set(f"{{{W}}}w", "0"); m.set(f"{{{W}}}type", "dxa")
    tblBorders = etree.SubElement(tblPr, f"{{{W}}}tblBorders")
    for side in ("top","bottom","left","right","insideH","insideV"):
        etree.SubElement(tblBorders, f"{{{W}}}{side}").set(f"{{{W}}}val", "none")
    tblGrid = etree.SubElement(tbl, f"{{{W}}}tblGrid")
    for _ in range(cols):
        etree.SubElement(tblGrid, f"{{{W}}}gridCol").set(f"{{{W}}}w", str(CW))

    pic_counter = pic_counter_start
    for row_idx in range(rows):
        tr = etree.SubElement(tbl, f"{{{W}}}tr")
        trPr = etree.SubElement(tr, f"{{{W}}}trPr")
        trH = etree.SubElement(trPr, f"{{{W}}}trHeight")
        trH.set(f"{{{W}}}val", str(CH)); trH.set(f"{{{W}}}hRule", "exact")
        for col_idx in range(cols):
            idx = row_idx * cols + col_idx
            tc = etree.SubElement(tr, f"{{{W}}}tc")
            tcPr = etree.SubElement(tc, f"{{{W}}}tcPr")
            tcW_el = etree.SubElement(tcPr, f"{{{W}}}tcW")
            tcW_el.set(f"{{{W}}}w", str(CW)); tcW_el.set(f"{{{W}}}type", "dxa")
            tcBorders = etree.SubElement(tcPr, f"{{{W}}}tcBorders")
            for side in ("top","bottom","left","right"):
                etree.SubElement(tcBorders, f"{{{W}}}{side}").set(f"{{{W}}}val", "none")
            etree.SubElement(tcPr, f"{{{W}}}vAlign").set(f"{{{W}}}val", "center")
            p = etree.SubElement(tc, f"{{{W}}}p")
            pPr = etree.SubElement(p, f"{{{W}}}pPr")
            etree.SubElement(pPr, f"{{{W}}}jc").set(f"{{{W}}}val", "center")
            sp = etree.SubElement(pPr, f"{{{W}}}spacing")
            sp.set(f"{{{W}}}before", "0"); sp.set(f"{{{W}}}after", "0")
            if idx < len(rIds):
                emu_w, emu_h = each_sizes[idx] if idx < len(each_sizes) else (int(avail_w), int(avail_h))
                r_elem = etree.SubElement(p, f"{{{W}}}r")
                r_elem.append(make_inline_image_xml(rIds[idx], emu_w, emu_h, pic_counter))
                pic_counter += 1
    return tbl, pic_counter


def make_six_subtable(rIds, img_paths, pic_counter_start, cell_w_dxa=9074, cell_h_dxa=11330):
    return make_grid_subtable(rIds, img_paths, pic_counter_start, 3, 2, cell_w_dxa, cell_h_dxa)


# ─────────────────────────────────────────────────────────────────────
# 核心 DOCX 處理
# ─────────────────────────────────────────────────────────────────────

def calc_image_emu(img_path, cell_w_dxa, cell_h_dxa):
    with Image.open(img_path) as img:
        img_w, img_h = img.size
    avail_w = cell_w_dxa * EMU_PER_DXA
    avail_h = cell_h_dxa * EMU_PER_DXA
    ratio = min(avail_w / img_w, avail_h / img_h)
    return int(img_w * ratio), int(img_h * ratio)


def make_inline_image_xml(rId, emu_cx, emu_cy, pic_id):
    xml_str = f'''<w:drawing xmlns:w="{W}" xmlns:wp="{WP}" xmlns:a="{A}" xmlns:pic="{PIC}" xmlns:r="{R}">
  <wp:inline distT="0" distB="0" distL="0" distR="0">
    <wp:extent cx="{emu_cx}" cy="{emu_cy}"/>
    <wp:effectExtent l="0" t="0" r="0" b="0"/>
    <wp:docPr id="{pic_id}" name="Photo{pic_id}"/>
    <wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="{A}" noChangeAspect="1"/></wp:cNvGraphicFramePr>
    <a:graphic xmlns:a="{A}">
      <a:graphicData uri="{PIC}">
        <pic:pic xmlns:pic="{PIC}">
          <pic:nvPicPr>
            <pic:cNvPr id="{pic_id}" name="Photo{pic_id}"/>
            <pic:cNvPicPr><a:picLocks noChangeAspect="1" noChangeArrowheads="1"/></pic:cNvPicPr>
          </pic:nvPicPr>
          <pic:blipFill>
            <a:blip r:embed="{rId}"/>
            <a:stretch><a:fillRect/></a:stretch>
          </pic:blipFill>
          <pic:spPr bwMode="auto">
            <a:xfrm><a:off x="0" y="0"/><a:ext cx="{emu_cx}" cy="{emu_cy}"/></a:xfrm>
            <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          </pic:spPr>
        </pic:pic>
      </a:graphicData>
    </a:graphic>
  </wp:inline>
</w:drawing>'''
    return etree.fromstring(xml_str)


def process_docx(template, output, pages, desc_text, location_text, start_num, log_cb, label_merged=True):
    tmp_dir  = Path(tempfile.gettempdir()) / "pp_tmp"
    work_dir = Path(tempfile.gettempdir()) / "pp_work"
    for d in (tmp_dir, work_dir):
        if d.exists(): shutil.rmtree(d)
        d.mkdir()

    with zipfile.ZipFile(template, 'r') as z:
        z.extractall(work_dir)

    doc_xml_path       = work_dir / "word" / "document.xml"
    rels_xml_path      = work_dir / "word" / "_rels" / "document.xml.rels"
    content_types_path = work_dir / "[Content_Types].xml"
    media_dir          = work_dir / "word" / "media"
    media_dir.mkdir(exist_ok=True)

    parser    = etree.XMLParser(remove_blank_text=False)
    doc_tree  = etree.parse(str(doc_xml_path), parser)
    doc_root  = doc_tree.getroot()
    rels_tree = etree.parse(str(rels_xml_path), parser)
    rels_root = rels_tree.getroot()
    ct_tree   = etree.parse(str(content_types_path), parser)
    ct_root   = ct_tree.getroot()

    body   = doc_root.find(f"{{{W}}}body")
    tables = body.findall(f"{{{W}}}tbl")
    if not tables: raise RuntimeError("找不到模板表格")

    template_table = copy.deepcopy(tables[0])

    # 自動偵測模板：每頁幾張照片
    all_rows = template_table.findall(f"{{{W}}}tr")
    # 找出所有「照片格」（單欄、高度較大的列）
    # photo_rows: list of (row_idx, height)
    photo_rows = []
    for ri, row in enumerate(all_rows):
        cells = row.findall(f"{{{W}}}tc")
        trPr  = row.find(f"{{{W}}}trPr")
        trH   = trPr.find(f"{{{W}}}trHeight") if trPr is not None else None
        h     = int(trH.get(f"{{{W}}}val")) if trH is not None else 0
        if len(cells) == 1 and h > 1000:
            photo_rows.append((ri, h))

    photos_per_page = len(photo_rows)  # 1 或 2

    # 取第一張照片格的寬高
    first_photo_row = all_rows[photo_rows[0][0]]  # photo_rows[0] = (row_idx, height)
    first_cell = first_photo_row.find(f"{{{W}}}tc")
    tcW_elem   = first_cell.find(f".//{{{W}}}tcW")
    cell_w_dxa = int(tcW_elem.get(f"{{{W}}}w") or 9074) if tcW_elem is not None else 9074
    cell_h_dxa = photo_rows[0][1]  # 直接用 photo_rows 裡的高度

    log_cb(f"📋 模板偵測：每頁 {photos_per_page} 張照片格")

    existing_rids = []
    for rel in rels_root:
        m = re.match(r"rId(\d+)", rel.get("Id", ""))
        if m: existing_rids.append(int(m.group(1)))
    next_rid_num = max(existing_rids, default=15) + 1

    CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
    registered_exts = {e.get("Extension", "").lower() for e in ct_root}

    def ensure_ct(ext_lower, mime):
        if ext_lower not in registered_exts:
            el = etree.SubElement(ct_root, f"{{{CT_NS}}}Default")
            el.set("Extension", ext_lower); el.set("ContentType", mime)
            registered_exts.add(ext_lower)

    def set_cell_text(cell, text, max_sz=28, min_sz=14):
        """清空格子內容並寫入文字，自動縮小字體避免跑版"""
        # 清除所有現有段落
        for p in cell.findall(f"{{{W}}}p"): cell.remove(p)
        # 建立新段落
        p   = etree.SubElement(cell, f"{{{W}}}p")
        pPr = etree.SubElement(p, f"{{{W}}}pPr")
        sp  = etree.SubElement(pPr, f"{{{W}}}spacing")
        sp.set(f"{{{W}}}before", "0"); sp.set(f"{{{W}}}after", "0")
        r_e = etree.SubElement(p, f"{{{W}}}r")
        rPr = etree.SubElement(r_e, f"{{{W}}}rPr")
        rFonts = etree.SubElement(rPr, f"{{{W}}}rFonts")
        rFonts.set(f"{{{W}}}eastAsia", "標楷體")
        rFonts.set(f"{{{W}}}hint",     "eastAsia")
        # 字體大小：文字超過20字就縮小
        sz_val = max_sz
        if text and len(text) > 20:
            sz_val = max(min_sz, max_sz - (len(text) - 20) // 5 * 2)
        sz = etree.SubElement(rPr, f"{{{W}}}sz")
        sz.set(f"{{{W}}}val", str(sz_val))
        sz2 = etree.SubElement(rPr, f"{{{W}}}szCs")
        sz2.set(f"{{{W}}}val", str(sz_val))
        t_e = etree.SubElement(r_e, f"{{{W}}}t")
        if text and (text[0] == ' ' or text[-1] == ' '):
            t_e.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t_e.text = text or ""

    sect_pr = body.find(f"{{{W}}}sectPr")
    for elem in list(body): body.remove(elem)

    # 輔助：插入一張 page 的圖片到 photo_cell
    def insert_page_image(page, photo_cell, cell_h, gidx):
        nonlocal pic_counter, next_rid_num
        paths  = page.get("paths", [])
        layout = page.get("layout", "tb")
        for elem in list(photo_cell):
            tn = elem.tag.split("}")[1] if "}" in elem.tag else elem.tag
            if tn in ("p","tbl"): photo_cell.remove(elem)
        if not paths:
            etree.SubElement(photo_cell, f"{{{W}}}p"); return
        if len(paths) >= 2:
            GRID_CONF = {"tb":(1,2),"lr":(2,1),"g4":(2,2),"g6":(3,2)}
            gc,gr = GRID_CONF.get(layout,(1,len(paths)))
            if gc*gr < len(paths): gr=(len(paths)+gc-1)//gc
            rids=[]
            for k,sp in enumerate(paths):
                # 合併頁：依設定燒上 a/b/c 標示
                if label_merged and k < len(LABEL_CHARS):
                    try: sp = stamp_label(sp, LABEL_CHARS[k])
                    except Exception: pass
                ext=Path(sp).suffix.lower().lstrip(".")
                mime={"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png",
                      "bmp":"image/bmp","gif":"image/gif","tiff":"image/tiff",
                      "webp":"image/webp"}.get(ext,"image/jpeg")
                ensure_ct(ext,mime)
                dn=f"image_p{gidx+1:03d}_{k}.{ext}"
                shutil.copy2(sp, media_dir/dn)
                rId=f"rId{next_rid_num}"; next_rid_num+=1
                nr=etree.SubElement(rels_root,f"{{{PKG_REL}}}Relationship")
                nr.set("Id",rId); nr.set("Type",REL_IMAGE); nr.set("Target",f"media/{dn}")
                rids.append(rId)
            st,pic_counter=make_grid_subtable(rids,paths,pic_counter,gc,gr,cell_w_dxa,cell_h)
            photo_cell.append(st)
            photo_cell.append(etree.fromstring(f'<w:p xmlns:w="{W}"/>'))
        else:
            ap=paths[0]
            ext=Path(ap).suffix.lower().lstrip(".")
            mime={"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png",
                  "bmp":"image/bmp","gif":"image/gif","tiff":"image/tiff"}.get(ext,"image/jpeg")
            ensure_ct(ext,mime)
            dn=f"image_p{gidx+1:03d}.{ext}"
            shutil.copy2(ap, media_dir/dn)
            rId=f"rId{next_rid_num}"; next_rid_num+=1
            nr=etree.SubElement(rels_root,f"{{{PKG_REL}}}Relationship")
            nr.set("Id",rId); nr.set("Type",REL_IMAGE); nr.set("Target",f"media/{dn}")
            ew,eh=calc_image_emu(ap,cell_w_dxa,cell_h)
            np_=etree.SubElement(photo_cell,f"{{{W}}}p")
            pp=etree.SubElement(np_,f"{{{W}}}pPr")
            jc=etree.SubElement(pp,f"{{{W}}}jc"); jc.set(f"{{{W}}}val","center")
            nr2=etree.SubElement(np_,f"{{{W}}}r")
            nr2.append(make_inline_image_xml(rId,ew,eh,pic_counter))
            pic_counter+=1

    def fill_desc_loc(rows, row_offset, page, photo_num):
        di=row_offset+1; li=row_offset+2
        if di<len(rows):
            c1=rows[di].findall(f"{{{W}}}tc")
            if len(c1)>=4:
                pd=page.get("desc")
                set_cell_text(c1[1], pd if pd is not None else desc_text)
                set_cell_text(c1[3], f"{photo_num:02d}")
        if li<len(rows):
            c2=rows[li].findall(f"{{{W}}}tc")
            if c2:
                pl = page.get("loc")
                set_cell_text(c2[0], pl if pl is not None else location_text)

    pic_counter  = 1
    total_pages  = len(pages)
    i = 0

    while i < total_pages:
        page      = pages[i]
        photo_num = start_num + i
        new_table = copy.deepcopy(template_table)
        rows      = new_table.findall(f"{{{W}}}tr")
        pct       = int((i+1)/total_pages*100)
        bar       = "█"*(pct//10)+"░"*(10-pct//10)

        if page.get("type") == "blank":
            pc = rows[photo_rows[0][0]].find(f"{{{W}}}tc")
            for el in list(pc):
                tn=el.tag.split("}")[1] if "}" in el.tag else el.tag
                if tn in ("p","tbl"): pc.remove(el)
            etree.SubElement(pc, f"{{{W}}}p")
            fill_desc_loc(rows, photo_rows[0][0], page, photo_num)
            if photos_per_page == 2:
                pc2 = rows[photo_rows[1][0]].find(f"{{{W}}}tc")
                for el in list(pc2):
                    tn=el.tag.split("}")[1] if "}" in el.tag else el.tag
                    if tn in ("p","tbl"): pc2.remove(el)
                etree.SubElement(pc2, f"{{{W}}}p")
                fill_desc_loc(rows, photo_rows[1][0], page, photo_num+1)
            log_cb(f"⬜ [{bar}]{pct:3d}%  第{i+1}/{total_pages}頁  空白頁  編號{photo_num:02d}")
            i += 1

        elif photos_per_page == 2:
            pc1 = rows[photo_rows[0][0]].find(f"{{{W}}}tc")
            insert_page_image(page, pc1, photo_rows[0][1], i)
            fill_desc_loc(rows, photo_rows[0][0], page, photo_num)
            page2 = pages[i+1] if i+1 < total_pages else None
            pc2   = rows[photo_rows[1][0]].find(f"{{{W}}}tc")
            if page2 and page2.get("type") != "blank":
                insert_page_image(page2, pc2, photo_rows[1][1], i+1)
                fill_desc_loc(rows, photo_rows[1][0], page2, photo_num+1)
                log_cb(f"✅ [{bar}]{pct:3d}%  第{i+1}-{i+2}/{total_pages}頁  編號{photo_num:02d}-{photo_num+1:02d}")
                i += 2
            else:
                for el in list(pc2):
                    tn=el.tag.split("}")[1] if "}" in el.tag else el.tag
                    if tn in ("p","tbl"): pc2.remove(el)
                etree.SubElement(pc2, f"{{{W}}}p")
                log_cb(f"✅ [{bar}]{pct:3d}%  第{i+1}/{total_pages}頁  編號{photo_num:02d}")
                i += 1

        else:
            pc = rows[photo_rows[0][0]].find(f"{{{W}}}tc")
            insert_page_image(page, pc, photo_rows[0][1], i)
            fill_desc_loc(rows, photo_rows[0][0], page, photo_num)
            tag="🔗" if len(page.get("paths",[]))>=2 else "✅"
            log_cb(f"{tag} [{bar}]{pct:3d}%  第{i+1}/{total_pages}頁  編號{photo_num:02d}")
            i += 1

        body.append(new_table)
        if i < total_pages:
            pb=etree.SubElement(body,f"{{{W}}}p")
            pb_r=etree.SubElement(pb,f"{{{W}}}r")
            pb_br=etree.SubElement(pb_r,f"{{{W}}}br")
            pb_br.set(f"{{{W}}}type","page")

    if sect_pr is not None: body.append(sect_pr)

    doc_tree.write(str(doc_xml_path),        xml_declaration=True, encoding="UTF-8", standalone=True)
    rels_tree.write(str(rels_xml_path),       xml_declaration=True, encoding="UTF-8", standalone=True)
    ct_tree.write(str(content_types_path),    xml_declaration=True, encoding="UTF-8", standalone=True)

    output_path = Path(output)
    if output_path.exists(): output_path.unlink()
    with zipfile.ZipFile(str(output_path), 'w', zipfile.ZIP_DEFLATED) as zout:
        for file in work_dir.rglob("*"):
            if file.is_file():
                zout.write(str(file), str(file.relative_to(work_dir)))

    shutil.rmtree(work_dir); shutil.rmtree(tmp_dir)
    log_cb(f"\n🎉 完成！輸出檔案：{output}")


# ─────────────────────────────────────────────────────────────────────
# 裁切視窗
# ─────────────────────────────────────────────────────────────────────

class CropWindow(tk.Toplevel):
    def __init__(self, parent, img_path, callback):
        super().__init__(parent)
        self.title("✂  裁切 / 旋轉照片")
        self.configure(bg=C["bg"])
        self.resizable(True, True)
        self.callback  = callback
        self.orig_img  = Image.open(img_path).convert("RGB")
        self._rect     = self._start = self._end = None
        self._angle    = 0.0
        self._rotated  = self.orig_img.copy()

        # 頂部標題
        hdr = tk.Frame(self, bg=C["topbar"], pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="✂  裁切照片  ·  先調整角度，再拖拉選取保留區域",
                 fg="white", bg=C["topbar"], font=("",11,"bold")).pack(padx=14)

        # 畫布
        self._update_scale(self.orig_img)
        self.cv = tk.Canvas(self, width=self.disp_w, height=self.disp_h,
                            bg="#e8e8e8", cursor="crosshair", highlightthickness=2,
                            highlightbackground=C["card_border"])
        self.cv.pack(padx=14, pady=(10,4))
        self.cv.bind("<ButtonPress-1>",   self._press)
        self.cv.bind("<B1-Motion>",       self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)

        # 旋轉滑桿
        rot_row = tk.Frame(self, bg=C["bg"])
        rot_row.pack(fill="x", padx=14, pady=(0,6))
        tk.Label(rot_row, text="🔄 水平角度：",
                 fg=C["text"], bg=C["bg"], font=("",9,"bold")).pack(side="left")
        self.angle_var = tk.DoubleVar(value=0.0)
        self.angle_lbl = tk.Label(rot_row, text="0.0°",
                                  fg=C["btn_green"], bg=C["bg"],
                                  font=("",10,"bold"), width=6)
        self.angle_lbl.pack(side="right")
        tk.Button(rot_row, text="歸零", command=self._reset_angle,
                  bg=C["btn_gray"], fg="white", relief="flat",
                  padx=8, pady=2, font=("",8), cursor="hand2", bd=0).pack(side="right", padx=4)
        self.slider = tk.Scale(rot_row, from_=-45, to=45, orient="horizontal",
                               resolution=0.5, variable=self.angle_var,
                               command=self._on_angle,
                               bg=C["bg"], fg=C["text"],
                               troughcolor=C["btnbar"],
                               highlightthickness=0, bd=0, showvalue=False)
        self.slider.pack(side="left", fill="x", expand=True, padx=8)

        # 按鈕列：直接放四個選項
        btn_row = tk.Frame(self, bg=C["bg"], pady=8)
        btn_row.pack()
        def btn(text, cmd, color):
            tk.Button(btn_row, text=text, command=cmd, bg=color, fg="white",
                      relief="flat", padx=14, pady=7, font=("",9,"bold"),
                      cursor="hand2", bd=0).pack(side="left", padx=4)
        btn("✅ 取代原圖",   lambda: self._do_confirm("replace"),  C["btn_green"])
        btn("➕ 新增為新頁", lambda: self._do_confirm("new_page"), C["btn_blue"])
        btn("↺ 重選框",     self._reset,                          C["btn_brown"])
        btn("✕ 取消",       self.destroy,                         C["btn_gray"])
        self.grab_set()
        self._redraw()

    def _update_scale(self, img):
        w, h = img.size
        scale = min(800/w, 520/h, 1.0)
        self.disp_w = int(w * scale)
        self.disp_h = int(h * scale)
        self.scale  = scale

    def _on_angle(self, val):
        self._angle = float(val)
        self.angle_lbl.config(text=f"{self._angle:.1f}°")
        if self._rect: self.cv.delete(self._rect)
        self._rect = self._start = self._end = None
        self._redraw()

    def _reset_angle(self):
        self.angle_var.set(0.0); self._angle = 0.0
        self.angle_lbl.config(text="0.0°")
        if self._rect: self.cv.delete(self._rect)
        self._rect = self._start = self._end = None
        self._redraw()

    def _redraw(self):
        self._rotated = self.orig_img.rotate(
            -self._angle, expand=True, resample=Image.BICUBIC,
            fillcolor=(255,255,255)) if self._angle != 0 else self.orig_img.copy()
        self._update_scale(self._rotated)
        self.cv.config(width=self.disp_w, height=self.disp_h)
        disp = self._rotated.resize((self.disp_w, self.disp_h), Image.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(disp)
        self.cv.delete("all")
        self.cv.create_image(0, 0, anchor="nw", image=self._tk_img)

    def _press(self, e):
        self._start = (e.x, e.y)
        if self._rect: self.cv.delete(self._rect)

    def _drag(self, e):
        if self._rect: self.cv.delete(self._rect)
        self._end = (e.x, e.y)
        self._rect = self.cv.create_rectangle(
            self._start[0], self._start[1], e.x, e.y,
            outline="#e67e22", width=2, dash=(5,3))

    def _release(self, e):
        self._end = (e.x, e.y)

    def _reset(self):
        if self._rect: self.cv.delete(self._rect)
        self._rect = self._start = self._end = None
        self._redraw()

    def _do_confirm(self, mode):
        # 計算裁切後圖片
        if not self._start or not self._end:
            if self._angle == 0:
                messagebox.showwarning("提示","請先拖拉選取裁切區域或調整角度",parent=self); return
            tmp = tempfile.mktemp(suffix=".jpg")
            self._rotated.save(tmp,"JPEG",quality=95)
        else:
            x1=int(min(self._start[0],self._end[0])/self.scale)
            y1=int(min(self._start[1],self._end[1])/self.scale)
            x2=int(max(self._start[0],self._end[0])/self.scale)
            y2=int(max(self._start[1],self._end[1])/self.scale)
            if x2-x1<5 or y2-y1<5:
                messagebox.showwarning("提示","選取區域太小",parent=self); return
            tmp = tempfile.mktemp(suffix=".jpg")
            self._rotated.crop((x1,y1,x2,y2)).save(tmp,"JPEG",quality=95)
        self.callback(tmp, mode)
        self.destroy()


# ─────────────────────────────────────────────────────────────────────
# 縮圖卡片
# ─────────────────────────────────────────────────────────────────────

class ThumbCard(tk.Frame):
    def __init__(self, master, page, index, app, card_w=400, **kwargs):
        self.page  = page
        self.index = index
        self.app   = app
        self._selected = False

        self._card_w = card_w
        self._card_w_for_load = card_w
        thumb_w = card_w - 16
        thumb_h = int(thumb_w * 1.5)  # 固定高度
        placeholder = Image.new("RGB", (thumb_w, thumb_h), (220, 225, 210))
        self._photo = ImageTk.PhotoImage(placeholder)
        self._thumb_w = thumb_w
        self._thumb_h = thumb_h

        # 卡片固定總高度：img + 說明+地點+標題+序號 約需 105px
        card_total_h = thumb_h + 105
        super().__init__(master, bd=0, relief="flat", bg=C["card"],
                         width=card_w, height=card_total_h, **kwargs)
        self.pack_propagate(False)  # 鎖定卡片高度，防止 grid 排列錯位

        # 用固定大小的 Frame 包住 img_label，防止縮圖大小不一造成閃爍
        self._img_frame = tk.Frame(self, bg=C["card"],
                                   width=thumb_w, height=thumb_h)
        self._img_frame.pack(pady=(6, 2))
        self._img_frame.pack_propagate(False)  # 鎖定 Frame 大小
        self.img_label = tk.Label(self._img_frame, image=self._photo,
                                  bg=C["card"], anchor="center")
        self.img_label.place(relx=0.5, rely=0.5, anchor="center")

        # 用執行緒池載入縮圖
        _load_pool.submit(self._load_preview_bg)

        if page.get("type") == "blank":
            label = f"#{index+1}  ⬜ 空白頁"
        elif len(page.get("paths", [])) == 6:
            label = f"#{index+1}  🌾 6合1"
        elif len(page.get("paths", [])) == 4:
            label = f"#{index+1}  🔲 4合1"
        elif len(page.get("paths", [])) == 2:
            label = f"#{index+1}  🌱 2合1"
        else:
            label = f"#{index+1}  {Path(page['paths'][0]).name}"
        short = label if len(label) <= 45 else label[:42] + "..."

        self.num_label = tk.Label(self, text="", bg=C["card"])  # 佔位，實際顯示在 info_row

        # 說明輸入框
        self._syncing = False
        # desc=None 表示跟預設同步，初始值填入 app 的預設說明
        _initial_desc = page.get("desc") if page.get("desc") is not None else (app.desc_var.get() if hasattr(app, "desc_var") else "")
        self.desc_var = tk.StringVar(value=_initial_desc)
        self.desc_entry = tk.Entry(self, textvariable=self.desc_var,
                                   bg="#f8faf0", fg=C["text"],
                                   insertbackground="#000000",
                                   relief="solid", bd=1, font=("",8))
        self.desc_entry.pack(fill="x", padx=6, pady=(0,1), ipady=2)
        fix_ime_entry(self.desc_entry)
        self.desc_var.trace_add("write", self._on_desc_change)

        # 地點輸入框
        _initial_loc = page.get("loc") if page.get("loc") is not None else (app.loc_var.get() if hasattr(app, "loc_var") else "")
        self.loc_var2 = tk.StringVar(value=_initial_loc)
        self.loc_entry = tk.Entry(self, textvariable=self.loc_var2,
                                  bg="#f8faf0", fg=C["text"],
                                  insertbackground="#000000",
                                  relief="solid", bd=1, font=("",8))
        self.loc_entry.pack(fill="x", padx=6, pady=(0,2), ipady=1)
        fix_ime_entry(self.loc_entry)
        self.loc_var2.trace_add("write", self._on_loc_change)

        # 排序號和標題放同一列
        info_row = tk.Frame(self, bg=C["card"])
        info_row.pack(fill="x", padx=6, pady=(0,4))

        self.num_label2 = tk.Label(info_row, text=short,
                                   fg=C["text2"], bg=C["card"],
                                   font=("",9,"bold"), anchor="w")
        self.num_label2.pack(side="left", fill="x", expand=True)

        # 排序號輸入框（預設顯示目前頁碼）
        default_sort = page.get("sort_key","") or str(index+1)
        self.sort_var = tk.StringVar(value=default_sort)
        if not page.get("sort_key",""):
            page["sort_key"] = str(index+1)
        # 允許任意字元的驗證器（確保小數點可輸入）
        vcmd = (info_row.register(lambda s: True), '%P')
        sort_entry = tk.Entry(info_row, textvariable=self.sort_var,
                              bg="#e8f0d8", fg=C["btn_green"],
                              relief="solid", bd=1,
                              font=("",9,"bold"), width=6,
                              justify="center",
                              insertbackground=C["btn_green"],
                              validate="key",
                              validatecommand=vcmd)
        sort_entry.pack(side="right", padx=(4,0), ipady=1)
        tk.Label(info_row, text="序:", fg=C["subtext"], bg=C["card"],
                 font=("",8)).pack(side="right")
        sort_entry.bind("<FocusOut>", lambda e: self._on_sort_change())
        sort_entry.bind("<Return>",   lambda e: self._on_sort_change())

        fix_ime_entry(sort_entry)

        for w in (self, self.img_label, self.num_label):
            w.bind("<Button-1>", self._on_click)

    def _make_preview(self, max_w, card_w=None):
        if card_w is None: card_w = max_w
        try:
            if self.page.get("type") == "blank":
                h = max_w * 3 // 4
                img = Image.new("RGB", (max_w, h), (240, 245, 225))
                draw = ImageDraw.Draw(img)
                draw.rectangle([8, 8, max_w-8, h-8], outline=(160, 190, 120), width=2)
                return img

            paths  = self.page.get("paths", [])
            layout = self.page.get("layout", "tb")
            max_h  = int((card_w or max_w) * 1.5)  # 允許直式照片更高

            def fit(img, mw, mh):
                r = min(mw/img.width, mh/img.height)
                return img.resize((int(img.width*r), int(img.height*r)), Image.LANCZOS)

            show_label = (hasattr(self.app, "label_merged_var") and
                          self.app.label_merged_var.get())

            def draw_cell_label(draw, ox, oy, idx, cell_w):
                """在格子左上角畫小標示"""
                if not show_label or idx >= len(LABEL_CHARS): return
                label = LABEL_CHARS[idx]
                fsize = max(10, cell_w // 7)
                pad   = max(3, fsize // 5)
                # 底色半透明矩形（用簡單畫法）
                bw = fsize + pad * 2
                draw.rectangle([ox + 4, oy + 4, ox + 4 + bw, oy + 4 + bw],
                                fill=(20, 20, 20))
                draw.text((ox + 4 + pad, oy + 4 + pad), label,
                          fill=(255, 255, 255))

            if len(paths) == 6:
                # 3x2 網格預覽
                C6, R6, gap = 3, 2, 3
                cw = (max_w - gap*(C6-1)) // C6
                ch = cw * 4 // 3
                combined = Image.new("RGB", (max_w, ch*R6+gap*(R6-1)), (240,242,235))
                draw = ImageDraw.Draw(combined)
                for idx, path in enumerate(paths):
                    try: im = fit(get_thumb(path,cw,ch), cw, ch)
                    except: im = Image.new("RGB",(cw,ch),(200,200,200))
                    col=idx%C6; row=idx//C6
                    px=col*(cw+gap)+(cw-im.width)//2
                    py=row*(ch+gap)+(ch-im.height)//2
                    combined.paste(im,(px, py))
                    draw_cell_label(draw, col*(cw+gap), row*(ch+gap), idx, cw)
                for ci in range(1,C6):
                    x=ci*(cw+gap)-1
                    draw.line([(x,0),(x,ch*R6+gap)],fill=(230,126,34),width=2)
                draw.line([(0,ch+gap//2),(max_w,ch+gap//2)],fill=(230,126,34),width=2)
                return combined

            elif len(paths) == 4:
                # 2x2 網格預覽
                hw=max_w//2; hh=max_h//2
                combined=Image.new("RGB",(max_w,max_h),(235,240,225))
                draw=ImageDraw.Draw(combined)
                for idx,path in enumerate(paths):
                    try: im=fit(get_thumb(path,hw,hh),hw-2,hh-2)
                    except: im=Image.new("RGB",(hw,hh),(200,200,200))
                    col=idx%2; row=idx//2
                    ox=(hw-im.width)//2; oy=(hh-im.height)//2
                    combined.paste(im,(col*hw+ox,row*hh+oy))
                    draw_cell_label(draw, col*hw, row*hh, idx, hw)
                draw.line([(max_w//2,0),(max_w//2,max_h)],fill=(230,126,34),width=2)
                draw.line([(0,max_h//2),(max_w,max_h//2)],fill=(230,126,34),width=2)
                return combined

            elif len(paths) == 2 and layout == "lr":
                # 左右並排
                hw=max_w//2
                il=fit(get_thumb(paths[0],hw,max_h),hw-2,max_h)
                ir=fit(get_thumb(paths[1],hw,max_h),hw-2,max_h)
                h=max(il.height,ir.height)
                combined=Image.new("RGB",(max_w,h),(255,255,255))
                combined.paste(il,((hw-il.width)//2,(h-il.height)//2))
                combined.paste(ir,(hw+(hw-ir.width)//2,(h-ir.height)//2))
                draw=ImageDraw.Draw(combined)
                draw_cell_label(draw, 0, 0, 0, hw)
                draw_cell_label(draw, hw, 0, 1, hw)
                draw.line([(hw,0),(hw,h)],fill=(230,126,34),width=2)
                return combined

            elif len(paths) == 2:
                # 上下排列
                it=fit(get_thumb(paths[0],max_w,max_h//2),max_w,max_h//2)
                ib=fit(get_thumb(paths[1],max_w,max_h//2),max_w,max_h//2)
                combined=Image.new("RGB",(max_w,it.height+ib.height),(255,255,255))
                combined.paste(it,((max_w-it.width)//2,0))
                draw=ImageDraw.Draw(combined)
                draw_cell_label(draw, 0, 0, 0, max_w)
                draw.line([(0,it.height),(max_w,it.height)],fill=(230,126,34),width=2)
                combined.paste(ib,((max_w-ib.width)//2,it.height))
                draw_cell_label(draw, 0, it.height, 1, max_w)
                return combined

            else:
                return fit(get_thumb(paths[0], max_w*2, max_h*2), max_w, max_h)

        except Exception:
            return Image.new("RGB", (max_w, max_w*3//4), (220,230,200))

    def set_selected(self, val):
        self._selected = val
        color = C["card_sel"] if val else C["card"]
        self.config(bg=color, highlightbackground="#3d5afe" if val else C["card"],
                    highlightthickness=2 if val else 0)
        self.img_label.config(bg=color)
        self.num_label.config(bg=color)
        if hasattr(self, "num_label2"): self.num_label2.config(bg=color)
        if hasattr(self, "loc_entry"): self.loc_entry.config(bg="#f0f8e8" if not val else "#d4edba")
        for child in self.winfo_children():
            if isinstance(child, tk.Frame): child.config(bg=color)

    def update_index(self, idx):
        """更新卡片編號顯示（智慧重建時重用卡片用）"""
        self.index = idx
        page = self.page
        n = len(page.get("paths", []))
        if page.get("type") == "blank":
            label = f"#{idx+1}  ⬜ 空白頁"
        elif n == 6: label = f"#{idx+1}  🌾 6合1"
        elif n == 4: label = f"#{idx+1}  🔲 4合1"
        elif n == 2: label = f"#{idx+1}  🌱 2合1"
        elif page.get("paths"): label = f"#{idx+1}  {Path(page['paths'][0]).name}"
        else: label = f"#{idx+1}"
        self.num_label.config(text=label if len(label)<=45 else label[:42]+"...")

    def _load_preview_bg(self):
        """背景執行緒載入縮圖，完成後更新 UI"""
        try:
            card_w  = self._card_w_for_load
            thumb_w = card_w - 16
            thumb_h = self._thumb_h  # 固定高度（初始化時設定）
            preview = self._make_preview(thumb_w, card_w)

            # 強制放入固定尺寸畫布（置中，等比縮放）
            # 這確保所有卡片（單圖、合併圖）高度完全一致，不閃爍不錯位
            r = min(thumb_w / max(preview.width, 1),
                    thumb_h / max(preview.height, 1))
            nw = max(1, int(preview.width  * r))
            nh = max(1, int(preview.height * r))
            resized  = preview.resize((nw, nh), Image.LANCZOS)
            canvas   = Image.new("RGB", (thumb_w, thumb_h), (220, 225, 210))
            ox = (thumb_w - nw) // 2
            oy = (thumb_h - nh) // 2
            canvas.paste(resized, (ox, oy))

            new_photo = ImageTk.PhotoImage(canvas)
            def update():
                if not self.winfo_exists(): return
                self._photo = new_photo
                self.img_label.config(image=self._photo)
            self.after(0, update)
        except Exception:
            pass

    def _on_desc_change(self, *args):
        if self._syncing: return
        val = self.desc_var.get()
        # 如果跟 app 預設說明一樣就設為 None（跟著同步），否則標記為自訂
        default = self.app.desc_var.get().strip()
        self.page["desc"] = None if val == default else val

    def sync_desc(self, default_text):
        """從右側說明同步（只有 desc==None 的才同步）"""
        if self.page.get("desc") is None:
            self._syncing = True
            self.desc_var.set(default_text)
            self._syncing = False

    def _on_loc_change(self, *args):
        if self._syncing: return
        val = self.loc_var2.get()
        default = self.app.loc_var.get().strip() if hasattr(self.app, "loc_var") else ""
        self.page["loc"] = None if val == default else val

    def sync_loc(self, default_text):
        """從右側地點同步（只有 loc==None 的才同步）"""
        if self.page.get("loc") is None:
            self._syncing = True
            self.loc_var2.set(default_text)
            self._syncing = False

    def _on_sort_change(self, *args):
        try:
            self.page["sort_key"] = self.sort_var.get()  # 不 strip，保留輸入中狀態
        except Exception:
            pass

    def _on_click(self, e):
        ctrl_held = (e.state & 0x8) != 0 if IS_MAC else (e.state & 0x4) != 0
        self.app.select_card(self, ctrl=ctrl_held)


# ─────────────────────────────────────────────────────────────────────
# 主視窗
# ─────────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"照片黏貼表自動填入工具 v{VERSION}  ·  By Kiki")
        self.root.configure(bg=C["bg"])
        self.root.minsize(900, 620)

        self.pages          = []
        self.cards          = []
        self._selected      = None
        self._selected_multi = []
        self._canvas_w      = 800
        self._resize_after  = None
        self.template_path  = ""
        self._history       = []   # undo stack (最多20步)
        self._redo_stack    = []   # redo stack
        self._rebuilding    = False
        self._rebuild_timer = None

        self._build_ui()

        # 視窗關閉時清理執行緒池
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 啟動後在背景檢查更新
        check_for_update(self.root)

        # 視窗大小改變時重建縮圖
        self.root.bind("<Configure>", self._on_window_resize)
        self.root.bind_all("<MouseWheel>", self._on_global_scroll)
        self.root.bind_all("<Control-z>", self.undo)
        self.root.bind_all("<Control-y>", self.redo)
        self.root.bind_all("<Control-a>", self.select_all)
        self.root.bind_all("<Control-A>", self.select_all)

    def _build_ui(self):
        # ── 頂部標題列 ──
        top = tk.Frame(self.root, bg=C["topbar"])
        top.pack(fill="x")

        tk.Label(top, text="🌿  照片黏貼表自動填入工具",
                 fg="white", bg=C["topbar"],
                 font=("", 13, "bold")).pack(side="left", padx=14, pady=8)

        def tb(text, cmd, color):
            tk.Button(top, text=text, command=cmd, bg=color, fg="white",
                      relief="flat", padx=12, pady=5,
                      font=("", 9, "bold"), cursor="hand2", bd=0).pack(
                      side="right", padx=4, pady=6)

        tb("▶ 開始製作",   self.run,            C["btn_green"])
        tb("💾 儲存專案",  self.save_project,   C["btn_brown"])
        tb("📥 匯入專案",  self.load_project,   C["btn_blue"])
        tb("📂 選擇模板",  self.pick_template,  C["btn_dark"])

        self.template_lbl = tk.Label(top, text="尚未選擇模板",
                                     fg="#a8d5b5", bg=C["topbar"], font=("", 8))
        self.template_lbl.pack(side="right", padx=6)

        # ── 主體 ──
        main = tk.Frame(self.root, bg=C["bg"])
        main.pack(fill="both", expand=True)

        # ── 左側 ──
        left = tk.Frame(main, bg=C["bg"])
        left.pack(side="left", fill="both", expand=True)

        # 按鈕列
        sec1 = tk.Frame(left, bg=C["btnbar"])
        sec1.pack(fill="x")
        tk.Label(sec1, text=" 🗂  頁面管理", fg=C["text"],
                 bg=C["btnbar"], font=("",9,"bold")).pack(side="left", padx=8, pady=6)

        btn_row = sec1

        def bb(text, cmd, color, row=sec1):
            tk.Button(row, text=text, command=cmd,
                      bg=color, fg="white", relief="flat",
                      padx=9, pady=4, font=("",9),
                      cursor="hand2", bd=0).pack(side="left", padx=2, pady=5)

        bb("＋ 新增照片",  self.add_photos,      C["btn_blue"])   # 加在最後
        bb("📌 插入照片",  self.insert_photos,    "#1565a0")       # 插在選取頁後
        bb("🌿 空白頁",    self.add_blank,        C["btn_dark"])
        bb("← 左移",      self.move_left,        C["btn_brown"])
        bb("→ 右移",      self.move_right,       C["btn_brown"])
        bb("🔗 合併",       self.merge_selected,   C["purple"])
        bb("✂ 拆開",       self.unmerge_selected, "#4e342e")
        bb("✕ 移除",       self.remove_selected,  "#b71c1c")
        bb("🗑 清除全部",   self.clear_all,        "#424242")
        bb("↩ 上一步",     self.undo,             "#5d4037")
        bb("↪ 下一步",     self.redo,             "#4a3728")
        bb("⇅ 顛倒順序",   self.reverse_order,   "#4a5a6a")
        bb("🔢 依序號排",   self.sort_by_key,      "#3a5a4a")

        sec2 = tk.Frame(left, bg=C["editbar"])
        sec2.pack(fill="x")
        tk.Label(sec2, text=" 🖼  圖片編輯", fg=C["text"],
                 bg=C["editbar"], font=("",9,"bold")).pack(side="left", padx=8, pady=6)

        def eb(text, cmd):
            tk.Button(sec2, text=text, command=cmd,
                      bg=C["btn_dark"], fg="white", relief="flat",
                      padx=10, pady=4, font=("",9),
                      cursor="hand2", bd=0).pack(side="left", padx=2, pady=5)

        eb("↺ 左轉90°",  self.rotate_left)
        eb("↻ 右轉90°",  self.rotate_right)
        eb("↔ 左右翻轉", self.flip_h)
        eb("↕ 上下翻轉", self.flip_v)
        eb("✂ 裁切",     self.open_crop)

        hint_row = tk.Frame(left, bg=C["bg"])
        hint_row.pack(fill="x", padx=6, pady=2)
        tk.Label(hint_row,
                 text="  💡 點選縮圖（綠框）後操作；Ctrl+點選可多選再合併",
                 fg=C["subtext"], bg=C["bg"], font=("",8)).pack(side="left")
        self.page_count_lbl = tk.Label(hint_row, text="共 0 頁",
                 fg=C["btn_green"], bg=C["bg"], font=("",9,"bold"))
        self.page_count_lbl.pack(side="right", padx=8)

        # 縮圖畫布
        canvas_frame = tk.Frame(left, bg=C["bg"])
        canvas_frame.pack(fill="both", expand=True, padx=6, pady=4)

        self.canvas = tk.Canvas(canvas_frame, bg=C["bg2"], highlightthickness=0)
        vscroll = tk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.config(yscrollcommand=vscroll.set)
        vscroll.pack(side="right", fill="y")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(
            int(-1*(e.delta/120)), "units"))

        self.thumb_frame = tk.Frame(self.canvas, bg=C["bg2"])
        self.canvas_win  = self.canvas.create_window((0,0), window=self.thumb_frame, anchor="nw")
        self.thumb_frame.bind("<Configure>", lambda e: self.canvas.config(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_configure)


        # ── 右側設定 ──
        right = tk.Frame(main, bg=C["bg3"], width=275)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        def sep():
            tk.Frame(right, bg="#2d3250", height=1).pack(fill="x", padx=10, pady=4)

        def lbl(t):
            tk.Label(right, text=t, fg=C["subtext"], bg=C["bg3"],
                     anchor="w", font=("", 9, "bold")).pack(fill="x", padx=14, pady=(10,2))

        def ent(var, h=5):
            e = tk.Entry(right, textvariable=var, bg=C["entry_bg"], fg=C["text"],
                         insertbackground="#000000", relief="flat",
                         font=("", 10), bd=0)
            e.pack(fill="x", padx=14, ipady=h)
            fix_ime_entry(e)
            return e

        tk.Label(right, text="⚙  設定", fg=C["text"], bg=C["bg3"],
                 font=("", 11, "bold")).pack(pady=(14,4), padx=14, anchor="w")
        sep()

        lbl("📝  說明文字（共用預設）")
        self.desc_var = tk.StringVar(value="說明文字請填寫。")
        self.desc_var.trace_add("write", self._on_default_desc_change)
        ent(self.desc_var)
        tk.Label(right, text="💡 各頁可在縮圖下方單獨修改",
                 fg=C["subtext"], bg=C["right_bg"],
                 font=("",7)).pack(anchor="w", padx=16, pady=(0,2))

        lbl("地點")
        self.loc_var = tk.StringVar(value=DEFAULT_LOCATION)
        self.loc_var.trace_add("write", self._on_default_loc_change)
        ent(self.loc_var)

        sep()
        lbl("起始照片編號")
        self.start_var = tk.IntVar(value=1)
        tk.Spinbox(right, from_=1, to=999, textvariable=self.start_var,
                   bg=C["entry_bg"], fg=C["text"], buttonbackground="#3a3f5c",
                   relief="flat", font=("", 10)).pack(fill="x", padx=14, ipady=4)

        sep()
        lbl("📁  輸出路徑")
        self.output_var = tk.StringVar(value="output_照片黏貼.docx")
        frm_out = tk.Frame(right, bg=C["entry_bd"], bd=1)
        frm_out.pack(fill="x", padx=14, pady=1)
        tk.Entry(frm_out, textvariable=self.output_var,
                 bg=C["entry_bg"], fg=C["text"],
                 insertbackground=C["text"], relief="flat",
                 font=("",9), bd=4).pack(fill="x", ipady=4)

        btn_row_out = tk.Frame(right, bg=C["right_bg"])
        btn_row_out.pack(fill="x", padx=14, pady=2)
        tk.Button(btn_row_out, text="📁 瀏覽",
                  command=self.pick_output,
                  bg=C["btn_gray"], fg="white", relief="flat",
                  font=("",8), cursor="hand2", bd=0).pack(
                  side="left", padx=(0,4), ipady=2, ipadx=6)
        tk.Label(btn_row_out, text="可直接修改路徑中的檔名",
                 fg=C["subtext"], bg=C["right_bg"], font=("",7)).pack(
                 side="left")

        # 完成後開啟 Word
        self.open_after_var = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="完成後自動開啟 Word",
                       variable=self.open_after_var,
                       bg=C["right_bg"], fg=C["text"],
                       selectcolor=C["entry_bg"],
                       activebackground=C["right_bg"],
                       font=("",8), cursor="hand2").pack(
                       anchor="w", padx=14, pady=(2,0))

        # 合併照片自動加標示
        self.label_merged_var = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="合併照片自動加標示 (a / b / c…)",
                       variable=self.label_merged_var,
                       bg=C["right_bg"], fg=C["text"],
                       selectcolor=C["entry_bg"],
                       activebackground=C["right_bg"],
                       font=("",8), cursor="hand2").pack(
                       anchor="w", padx=14, pady=(0,2))

        sep()
        lbl("執行記錄")
        self.log_text = tk.Text(right, bg=C["log_bg"], fg=C["log_fg"],
                                font=("Consolas", 9), state="disabled",
                                relief="flat", bd=0)
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0,10))

    # ── 版面事件 ──
    def _on_default_desc_change(self, *args):
        """右側說明改變時，同步所有未自訂的縮圖說明欄"""
        default = self.desc_var.get().strip()
        for card in self.cards:
            card.sync_desc(default)

    def _on_default_loc_change(self, *args):
        """右側地點改變時，同步所有未自訂的縮圖地點欄"""
        default = self.loc_var.get().strip()
        for card in self.cards:
            if hasattr(card, "sync_loc"):
                card.sync_loc(default)

    def _on_global_scroll(self, e):
        try:
            cx=self.canvas.winfo_rootx(); cy=self.canvas.winfo_rooty()
            cw=self.canvas.winfo_width(); ch=self.canvas.winfo_height()
            if cx<=e.x_root<=cx+cw and cy<=e.y_root<=cy+ch:
                self.canvas.yview_scroll(int(-1*(e.delta/120)),"units")
        except: pass

    def _on_canvas_configure(self, e):
        self.canvas.itemconfig(self.canvas_win, width=e.width)
        if self._rebuilding: return  # 重建中不觸發
        new_w = e.width
        if new_w > 1 and abs(new_w - self._canvas_w) > 20:
            self._canvas_w = new_w
            self._schedule_rebuild(300)

    def _on_window_resize(self, e):
        if e.widget != self.root: return
        if self._rebuilding: return  # 重建中不觸發
        if self._resize_after: self.root.after_cancel(self._resize_after)
        self._resize_after = self.root.after(300, self._schedule_rebuild)

    # ── 模板 / 輸出 ──
    def pick_template(self):
        p = filedialog.askopenfilename(title="選擇模板 DOCX",
            filetypes=[("Word 文件","*.docx"),("所有檔案","*.*")])
        if p:
            self.template_path = p
            self.template_lbl.config(text=f"📄 {Path(p).name}", fg="white")
            # 只有在輸出路徑還是預設值時才自動更新，避免覆蓋使用者已設定的路徑
            current = self.output_var.get().strip()
            is_default = (not current or
                          current == "output_照片黏貼.docx" or
                          Path(current).name == "output_照片黏貼.docx")
            if is_default:
                self.output_var.set(str(Path(p).parent / "output_照片黏貼.docx"))

    def pick_output(self):
        p = filedialog.asksaveasfilename(title="輸出另存為",
            defaultextension=".docx", filetypes=[("Word 文件","*.docx")])
        if p: self.output_var.set(p)

    # ── 頁面管理 ──
    def add_photos(self):
        """新增照片：選檔案或整個資料夾"""
        dlg = tk.Toplevel(self.root)
        dlg.title("新增照片")
        dlg.configure(bg=C["bg"])
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.focus_force()

        tk.Label(dlg, text="請選擇新增方式",
                 fg=C["text"], bg=C["bg"],
                 font=("",11,"bold")).pack(pady=(20,8), padx=24)

        btn_frame = tk.Frame(dlg, bg=C["bg"])
        btn_frame.pack(pady=4, padx=24)
        result = [None]

        def pick_files():
            result[0] = "files"; dlg.destroy()

        def pick_folder():
            result[0] = "folder"; dlg.destroy()

        tk.Button(btn_frame, text="🖼  選擇照片檔案\n（可多選）",
                  command=pick_files,
                  bg=C["btn_blue"], fg="white", relief="flat",
                  padx=20, pady=14, font=("",10,"bold"),
                  cursor="hand2", bd=0, wraplength=160,
                  justify="center").pack(side="left", padx=8)

        tk.Button(btn_frame, text="📁  選擇資料夾\n（匯入全部照片）",
                  command=pick_folder,
                  bg=C["btn_green"], fg="white", relief="flat",
                  padx=20, pady=14, font=("",10,"bold"),
                  cursor="hand2", bd=0, wraplength=160,
                  justify="center").pack(side="left", padx=8)

        tk.Button(dlg, text="✕ 取消",
                  command=dlg.destroy,
                  bg=C["btn_gray"], fg="white", relief="flat",
                  padx=12, pady=6, font=("",9),
                  cursor="hand2", bd=0).pack(pady=(8,16))

        dlg.wait_window()
        if result[0] is None: return

        self._save_history()
        exts = {".jpg",".jpeg",".png",".bmp",".gif",".tiff",".webp"}

        if result[0] == "files":
            paths = filedialog.askopenfilenames(
                title="選擇照片（可複選）",
                filetypes=[("圖片","*.jpg *.jpeg *.png *.bmp *.gif *.tiff"),("所有","*.*")])
            if not paths: return
            final_paths = list(paths)
        else:
            folder = filedialog.askdirectory(title="選擇照片資料夾")
            if not folder: return
            final_paths = sorted([
                str(Path(folder)/f) for f in sorted(os.listdir(folder))
                if Path(f).suffix.lower() in exts
            ])
            if not final_paths:
                messagebox.showinfo("提示", "資料夾內找不到照片"); return

        for p in final_paths:
            self.pages.append({"type":"photo","paths":[p],"orig_path":p,"desc":None,"loc":None,"sort_key":""})
        self._schedule_rebuild()
        self.log(f"＋ 已新增 {len(final_paths)} 張照片（加在最後）")

    def insert_photos(self):
        """插入照片到選取頁後面（可一次多選）"""
        paths = filedialog.askopenfilenames(
            title="選擇要插入的照片（可複選）",
            filetypes=[("圖片","*.jpg *.jpeg *.png *.bmp *.gif *.tiff"),
                       ("所有","*.*")])
        if not paths: return

        self._save_history()
        insert_idx = (self._selected.index + 1) if self._selected else len(self.pages)
        for k, p in enumerate(paths):
            self.pages.insert(insert_idx + k,
                              {"type":"photo","paths":[p],"orig_path":p,"desc":None,"loc":None,"sort_key":""})
        self._schedule_rebuild()
        self.log(f"📌 已插入 {len(paths)} 張照片（從第 {insert_idx+1} 頁開始）")

    def add_blank(self):
        self._save_history()
        if self._selected is not None:
            self.pages.insert(self._selected.index + 1, {"type":"blank","paths":[],"desc":None,"loc":None,"sort_key":""})
        else:
            self.pages.append({"type":"blank","paths":[],"desc":None,"loc":None,"sort_key":""})
        self._schedule_rebuild()

    def remove_selected(self):
        if self._selected is None: return
        self._save_history()
        idxs = self.get_selected_indices()
        for i in sorted(idxs, reverse=True):
            self.pages.pop(i)
        self._selected = None; self._selected_multi = []
        self._schedule_rebuild()

    def clear_all(self):
        self._save_history()
        self.pages.clear(); self._selected = None; self._selected_multi = []
        self._schedule_rebuild()

    def move_left(self):
        if not self._selected or self._selected.index == 0: return
        self._save_history()
        self._selected_multi = []
        i = self._selected.index
        self.pages[i-1], self.pages[i] = self.pages[i], self.pages[i-1]
        sel = self.pages[i-1]
        self._schedule_rebuild()
        for c in self.cards:
            if c.page is sel: self.select_card(c); break

    def move_right(self):
        if not self._selected or self._selected.index >= len(self.pages)-1: return
        self._save_history()
        self._selected_multi = []
        i = self._selected.index
        self.pages[i], self.pages[i+1] = self.pages[i+1], self.pages[i]
        sel = self.pages[i+1]
        self._schedule_rebuild()
        for c in self.cards:
            if c.page is sel: self.select_card(c); break

    def merge_selected(self):
        idxs = self.get_selected_indices()
        idxs = [i for i in idxs if self.pages[i].get("type") != "blank"
                and len(self.pages[i].get("paths",[])) == 1]
        if len(idxs) == 0:
            messagebox.showinfo("🌱 提示",
                "請先點選要合併的照片\nCtrl+點選可多選（支援2、4、6張）"); return

        # 超過6張 → 批次合併
        if len(idxs) > 6:
            self._show_batch_merge_dialog(idxs)
            return

        if len(idxs) == 1:
            i = idxs[0]
            if i >= len(self.pages)-1:
                messagebox.showwarning("🌿 無法合併", "已是最後一頁，無下一張"); return
            if self.pages[i+1].get("type") == "blank" or len(self.pages[i+1].get("paths",[])) != 1:
                messagebox.showwarning("🌿 無法合併", "下一頁無法合併"); return
            idxs = [i, i+1]
        if len(idxs) not in (2, 4, 6):
            messagebox.showwarning("🌿 無法合併",
                f"請選取 2、4 或 6 張（目前選了 {len(idxs)} 張）"); return
        paths = [self.pages[i]["paths"][0] for i in idxs]
        self._show_merge_dialog(idxs, paths)

    def _show_batch_merge_dialog(self, idxs):
        """批次合併：多張照片按組自動配對"""
        dlg = tk.Toplevel(self.root)
        dlg.title("🌱 批次合併")
        dlg.configure(bg=C["bg"])
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text=f"已選取 {len(idxs)} 張照片",
                 fg=C["text"], bg=C["bg"], font=("",12,"bold")).pack(pady=(16,4), padx=24)
        tk.Label(dlg, text="選擇每組幾張及版面，程式自動分配",
                 fg=C["subtext"], bg=C["bg"], font=("",9)).pack(pady=(0,10))

        # 每組幾張
        tk.Label(dlg, text="每組張數：",
                 fg=C["text"], bg=C["bg"], font=("",10,"bold")).pack(anchor="w", padx=24)
        group_var = tk.IntVar(value=2)
        grp_frame = tk.Frame(dlg, bg=C["bg"])
        grp_frame.pack(anchor="w", padx=32, pady=4)
        for n in [2, 4, 6]:
            tk.Radiobutton(grp_frame, text=f"{n} 張一組",
                           variable=group_var, value=n,
                           bg=C["bg"], fg=C["text"],
                           selectcolor=C["entry_bg"],
                           font=("",10)).pack(side="left", padx=10)

        # 版面
        layout_options = {
            2: [("上下排列", "tb"), ("左右並排", "lr")],
            4: [("2×2 四格", "g4"), ("上下排列", "tb"), ("左右並排", "lr")],
            6: [("3×2 六格", "g6")],
        }
        tk.Label(dlg, text="版面：",
                 fg=C["text"], bg=C["bg"], font=("",10,"bold")).pack(anchor="w", padx=24, pady=(8,0))
        layout_var = tk.StringVar(value="tb")
        layout_frame = tk.Frame(dlg, bg=C["bg"])
        layout_frame.pack(anchor="w", padx=32, pady=4)
        radio_widgets = []

        def update_layout_options(*args):
            for w in radio_widgets: w.destroy()
            radio_widgets.clear()
            n = group_var.get()
            opts = layout_options.get(n, layout_options[2])
            layout_var.set(opts[0][1])
            for label, val in opts:
                r = tk.Radiobutton(layout_frame, text=label,
                                   variable=layout_var, value=val,
                                   bg=C["bg"], fg=C["text"],
                                   selectcolor=C["entry_bg"],
                                   font=("",10))
                r.pack(side="left", padx=10)
                radio_widgets.append(r)

        group_var.trace_add("write", update_layout_options)
        update_layout_options()

        # 預覽說明
        info_lbl = tk.Label(dlg, text="", fg=C["subtext"], bg=C["bg"], font=("",9))
        info_lbl.pack(pady=(4,0))

        def update_info(*args):
            n = group_var.get()
            groups = len(idxs) // n
            remain = len(idxs) % n
            txt = f"→ 合併成 {groups} 組"
            if remain: txt += f"，剩餘 {remain} 張獨立（不合併）"
            info_lbl.config(text=txt)

        group_var.trace_add("write", update_info)
        update_info()

        btn_frame = tk.Frame(dlg, bg=C["bg"])
        btn_frame.pack(pady=16, padx=24)

        def do_batch():
            n = group_var.get()
            layout = layout_var.get()
            self._save_history()
            self._selected = None
            self._selected_multi = []
            groups = [idxs[i:i+n] for i in range(0, len(idxs), n)]
            # 從最後一組往前合併，避免 index 位移
            for group in reversed(groups):
                if len(group) < 2: continue
                paths = [self.pages[i]["paths"][0] for i in group]
                first_desc = next((self.pages[i].get("desc") for i in group
                                   if self.pages[i].get("desc") is not None), None)
                first_loc  = next((self.pages[i].get("loc")  for i in group
                                   if self.pages[i].get("loc")  is not None), None)
                new_page = {"type":"photo","paths":paths,"layout":layout,
                            "desc":first_desc,"loc":first_loc,
                            "sort_key":"","orig_path":paths[0]}
                for i in sorted(group, reverse=True):
                    self.pages.pop(i)
                self.pages.insert(group[0], new_page)
            merged = len([g for g in groups if len(g) >= 2])
            self._schedule_rebuild()
            dlg.destroy()
            self.log(f"🌱 批次合併完成：{merged} 組，版面：{layout}")

        tk.Button(btn_frame, text="✅ 開始合併",
                  command=do_batch,
                  bg=C["btn_green"], fg="white", relief="flat",
                  padx=20, pady=10, font=("",10,"bold"),
                  cursor="hand2", bd=0).pack(side="left", padx=8)
        tk.Button(btn_frame, text="✕ 取消",
                  command=dlg.destroy,
                  bg=C["btn_gray"], fg="white", relief="flat",
                  padx=14, pady=10, font=("",9),
                  cursor="hand2", bd=0).pack(side="left", padx=8)

    def _show_merge_dialog(self, idxs, paths):
        n = len(paths)
        dlg = tk.Toplevel(self.root)
        dlg.title("🌱 選擇合併版面")
        dlg.configure(bg=C["bg"])
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text=f"已選取 {n} 張照片，請選擇合併版面：",
                 fg=C["text"], bg=C["bg"], font=("",11,"bold")).pack(pady=(16,4), padx=20)
        tk.Label(dlg, text="合併後共用一個說明欄與照片編號",
                 fg=C["subtext"], bg=C["bg"], font=("",9)).pack(pady=(0,10))

        btn_frame = tk.Frame(dlg, bg=C["bg"])
        btn_frame.pack(pady=6, padx=20)

        def do_merge(layout):
            self._save_history()
            first_desc = None
            first_loc  = None
            for i in idxs:
                if self.pages[i].get("desc") is not None and first_desc is None:
                    first_desc = self.pages[i].get("desc")
                if self.pages[i].get("loc") is not None and first_loc is None:
                    first_loc = self.pages[i].get("loc")
            new_page = {"type":"photo","paths":paths,"layout":layout,
                        "desc":first_desc,"loc":first_loc,"sort_key":"","orig_path":paths[0]}
            for i in sorted(idxs, reverse=True): self.pages.pop(i)
            self.pages.insert(idxs[0], new_page)
            self._selected = None
            self._selected_multi = []
            self._schedule_rebuild()
            dlg.destroy()

        def mbtn(text, layout):
            tk.Button(btn_frame, text=text,
                      command=lambda l=layout: do_merge(l),
                      bg=C["btn_green"], fg="white", relief="flat",
                      padx=18, pady=12, font=("",10,"bold"),
                      cursor="hand2", wraplength=180,
                      justify="center", bd=0).pack(side="left", padx=8)

        if n == 2:
            mbtn("📐  上下排列\n（橫式推薦）", "tb")
            mbtn("📋  左右並排\n（直式推薦）", "lr")
        elif n == 4:
            mbtn("🔲  四格 2×2", "g4")
            mbtn("📐  上下排列", "tb")
            mbtn("📋  左右並排", "lr")
        elif n == 6:
            mbtn("🌾  六格 3×2\n（每格獨立可調整）", "g6")

        tk.Button(dlg, text="✕  取消", command=dlg.destroy,
                  bg=C["btn_gray"], fg="white", relief="flat",
                  padx=14, pady=8, font=("",9),
                  cursor="hand2", bd=0).pack(pady=(8,16))

    def unmerge_selected(self):
        # 多選或全選時，批次拆開所有合併頁
        idxs = self.get_selected_indices()
        merged_idxs = [i for i in idxs if len(self.pages[i].get("paths",[])) >= 2]

        if len(merged_idxs) > 1:
            # 批次拆開
            self._save_history()
            # 從後往前拆，避免 index 位移
            for i in sorted(merged_idxs, reverse=True):
                page  = self.pages[i]
                paths = page["paths"]
                n     = len(paths)
                self.pages[i] = {"type":"photo","paths":[paths[0]],"desc":None,"loc":None,"sort_key":"","orig_path":paths[0]}
                for k in range(1, n):
                    self.pages.insert(i+k, {"type":"photo","paths":[paths[k]],"desc":None,"loc":None,"sort_key":"","orig_path":paths[k]})
            self._selected = None
            self._selected_multi = []
            self._schedule_rebuild()
            self.log(f"✂ 已拆開 {len(merged_idxs)} 個合併頁")
            return

        # 單選
        if self._selected is None:
            messagebox.showinfo("🌱 提示", "請先點選要拆開的合併頁"); return
        page = self._selected.page
        n = len(page.get("paths",[]))
        if n < 2:
            messagebox.showinfo("🌱 提示", "這頁不是合併頁"); return
        i = self._selected.index
        paths = page["paths"]
        self._save_history()
        self.pages[i] = {"type":"photo","paths":[paths[0]],"desc":None,"loc":None,"sort_key":"","orig_path":paths[0]}
        for k in range(1, n):
            self.pages.insert(i+k, {"type":"photo","paths":[paths[k]],"desc":None,"loc":None,"sort_key":"","orig_path":paths[k]})
        self._selected = None
        self._selected_multi = []
        self._schedule_rebuild()

    def select_card(self, card, ctrl=False):
        if ctrl:
            if card in self._selected_multi:
                card.set_selected(False)
                self._selected_multi.remove(card)
                self._selected = self._selected_multi[-1] if self._selected_multi else None
            else:
                if self._selected and self._selected not in self._selected_multi:
                    self._selected_multi.append(self._selected)
                card.set_selected(True)
                self._selected_multi.append(card)
                self._selected = card
        else:
            for c in self._selected_multi: c.set_selected(False)
            self._selected_multi = []
            if self._selected: self._selected.set_selected(False)
            self._selected = card
            card.set_selected(True)

    def get_selected_indices(self):
        if self._selected_multi:
            return sorted(set(c.index for c in self._selected_multi))
        if self._selected:
            return [self._selected.index]
        return []

    # ── 圖片編輯 ──
    def _get_selected_single_path(self):
        if self._selected is None:
            messagebox.showinfo("提示", "請先點選一張照片"); return None
        page = self._selected.page
        if page.get("type") == "blank":
            messagebox.showinfo("提示", "空白頁無法編輯"); return None
        if len(page.get("paths", [])) != 1:
            messagebox.showinfo("提示", "請先拆開合併頁再編輯"); return None
        return page["paths"][0]

    def _apply_transform(self, transform_fn):
        # 多選時套用到所有選取的單張照片
        targets = []
        seen_ids = set()
        if self._selected_multi:
            for card in self._selected_multi:
                page = card.page
                if id(page) not in seen_ids and page.get("type") != "blank" and len(page.get("paths",[])) == 1:
                    targets.append(page)
                    seen_ids.add(id(page))
        if not targets and self._selected:
            page = self._selected.page
            if page.get("type") != "blank" and len(page.get("paths",[])) == 1:
                targets.append(page)

        if not targets:
            messagebox.showinfo("提示", "請先選取照片（合併頁請先拆開）"); return

        self._save_history()
        total = len(targets)
        self.log(f"🔄 開始處理 {total} 張...")
        changed_pages = set()
        for idx, page in enumerate(targets):
            try:
                path = page["paths"][0]
                self.log(f"  [{idx+1}/{total}] 處理中：{Path(path).name}")
                img = Image.open(path).convert("RGB")
                result = transform_fn(img)
                tmp = tempfile.mktemp(suffix=".jpg")
                result.save(tmp, "JPEG", quality=95)
                with _thumb_lock:
                    keys_to_del = [k for k in _thumb_cache if k.startswith(path + "|")]
                    for k in keys_to_del:
                        del _thumb_cache[k]
                page["paths"][0] = tmp
                page["orig_path"] = tmp
                changed_pages.add(id(page))
            except Exception as e:
                self.log(f"[錯誤] {e}")
        self.log(f"✅ 完成，共處理 {len(changed_pages)} 張")
        self._force_rebuild_pages(changed_pages)

    def rotate_left(self):
        self._apply_transform(lambda img: img.rotate(90, expand=True))

    def rotate_right(self):
        self._apply_transform(lambda img: img.rotate(-90, expand=True))

    def flip_h(self):
        self._apply_transform(lambda img: ImageOps.mirror(img))

    def flip_v(self):
        self._apply_transform(lambda img: ImageOps.flip(img))

    def open_crop(self):
        if self._selected is None:
            messagebox.showinfo("🌱 提示", "請先點選一張照片"); return
        page = self._selected.page
        if page.get("type") == "blank":
            messagebox.showinfo("🌱 提示", "空白頁無法編輯"); return
        if len(page.get("paths",[])) != 1:
            messagebox.showinfo("🌱 提示", "請先拆開合併頁再編輯"); return
        # 永遠從原始圖開始裁切
        orig = page.get("orig_path", page["paths"][0])
        sel_idx = self._selected.index
        def on_crop_done(new_path, mode):
            self._save_history()
            if mode == "replace":
                changed_id = id(self._selected.page)
                # 清除快取
                old_path = self._selected.page["paths"][0]
                with _thumb_lock:
                    keys_to_del = [k for k in _thumb_cache if k.startswith(old_path + "|")]
                    for k in keys_to_del: del _thumb_cache[k]
                self._selected.page["paths"][0] = new_path
                self._selected.page["orig_path"] = orig
                self._force_rebuild_pages({changed_id})
            elif mode == "new_page":
                self.pages.insert(sel_idx + 1,
                                   {"type":"photo","paths":[new_path],
                                    "orig_path":orig,"desc":None,"loc":None,"sort_key":""})
                self._schedule_rebuild()
        CropWindow(self.root, orig, on_crop_done)

    # ── 復原 ──
    def _sync_descs_to_pages(self):
        """把縮圖說明欄和地點欄的內容同步回 page"""
        for card in self.cards:
            val = card.desc_var.get()
            default_desc = self.desc_var.get().strip()
            card.page["desc"] = None if val == default_desc else val
            if hasattr(card, "loc_var2"):
                loc_val = card.loc_var2.get()
                default_loc = self.loc_var.get().strip()
                card.page["loc"] = None if loc_val == default_loc else loc_val

    def _save_history(self):
        """每次修改 pages 前呼叫，先同步說明再存快照"""
        self._sync_descs_to_pages()
        snap = copy.deepcopy(self.pages)
        self._history.append(snap)
        if len(self._history) > 20:
            self._history.pop(0)
        # 有新操作時清空 redo stack
        self._redo_stack.clear()

    def undo(self, event=None):
        if not self._history:
            self.log("↩ 已無可復原的步驟")
            return
        # 把目前狀態存到 redo stack
        self._sync_descs_to_pages()
        self._redo_stack.append(copy.deepcopy(self.pages))
        if len(self._redo_stack) > 20:
            self._redo_stack.pop(0)
        restored = self._history.pop()
        self.pages.clear()
        self.pages.extend(restored)
        self._selected = None
        self._selected_multi = []
        self._schedule_rebuild()
        self.log(f"↩ 復原上一步（可復原 {len(self._history)} 步，可重做 {len(self._redo_stack)} 步）")

    def redo(self, event=None):
        if not self._redo_stack:
            self.log("↪ 已無可重做的步驟")
            return
        self._sync_descs_to_pages()
        self._history.append(copy.deepcopy(self.pages))
        if len(self._history) > 20:
            self._history.pop(0)
        restored = self._redo_stack.pop()
        self.pages.clear()
        self.pages.extend(restored)
        self._selected = None
        self._selected_multi = []
        self._schedule_rebuild()
        self.log(f"↪ 重做下一步（可復原 {len(self._history)} 步，可重做 {len(self._redo_stack)} 步）")

    def select_all(self, event=None):
        """Ctrl+A 全選所有頁面"""
        if not self.cards: return
        # 把焦點從 Entry 移走，避免 Entry 攔截
        try: self.root.focus_set()
        except: pass
        for c in self._selected_multi: c.set_selected(False)
        self._selected_multi = []
        if self._selected: self._selected.set_selected(False)
        self._selected = None
        for card in self.cards:
            card.set_selected(True)
            self._selected_multi.append(card)
        if self.cards:
            self._selected = self.cards[-1]
        return "break"  # 防止事件繼續傳遞

    def reverse_order(self):
        if len(self.pages) < 2: return
        self._save_history()
        self.pages.reverse()
        self._schedule_rebuild()

    def sort_by_key(self):
        """依排序號重新排列（支援 1、1.1、1.2、2 等格式）"""
        # 先從所有卡片讀取最新的排序號
        for card in self.cards:
            val = card.sort_var.get().strip()
            card.page["sort_key"] = val

        def parse_key(page):
            k = str(page.get("sort_key","")).strip()
            if not k:
                return (float("inf"), 0)
            try:
                f = float(k)
                return (f, 0)
            except ValueError:
                return (float("inf"), 0)

        before = [p.get("sort_key","") for p in self.pages]
        self._save_history()
        self.pages.sort(key=parse_key)
        for idx, page in enumerate(self.pages):
            page["sort_key"] = str(idx + 1)
        self._schedule_rebuild()
        self.log(f"🔢 已依序號排列並重新編號（共 {len(self.pages)} 頁）")

    # ── 縮圖重建 ──
    def _force_rebuild_pages(self, page_ids):
        """強制重建特定 page 的縮圖卡片（旋轉/翻轉/裁切後用）"""
        self.canvas.update_idletasks()
        actual_w = self.canvas.winfo_width()
        canvas_w = max(600, actual_w if actual_w > 1 else self._canvas_w)
        card_w   = (canvas_w - 30) // COLS
        default_desc = self.desc_var.get() if hasattr(self, "desc_var") else ""

        sel_page = self._selected.page if self._selected else None
        sel_multi = [c.page for c in self._selected_multi]

        for i, card in enumerate(self.cards):
            if id(card.page) in page_ids:
                # 完整重建卡片（保留說明和地點欄的值）
                page = card.page
                old_desc = card.desc_var.get()
                old_loc  = card.loc_var2.get() if hasattr(card, "loc_var2") else ""
                old_sort = card.sort_var.get() if hasattr(card, "sort_var") else ""

                new_card = ThumbCard(self.thumb_frame, page, i, self, card_w=card_w)
                # 還原說明/地點/排序
                new_card._syncing = True
                new_card.desc_var.set(old_desc)
                if hasattr(new_card, "loc_var2"): new_card.loc_var2.set(old_loc)
                if hasattr(new_card, "sort_var"): new_card.sort_var.set(old_sort)
                new_card._syncing = False

                row, col = divmod(i, COLS)
                new_card.grid(row=row, column=col, padx=4, pady=4, sticky="nw")

                if page is sel_page:
                    new_card.set_selected(True)
                    self._selected = new_card
                elif page in sel_multi:
                    new_card.set_selected(True)
                    idx = self._selected_multi.index(card)
                    self._selected_multi[idx] = new_card

                card.destroy()
                self.cards[i] = new_card

        self.thumb_frame.update_idletasks()
        self.canvas.config(scrollregion=self.canvas.bbox("all"))

    def _schedule_rebuild(self, delay=80):
        """防抖重建：delay ms 後執行，期間若再呼叫重置計時"""
        if hasattr(self, "_rebuild_timer") and self._rebuild_timer:
            self.root.after_cancel(self._rebuild_timer)
        self._rebuild_timer = self.root.after(delay, self._do_full_rebuild)

    def _do_full_rebuild(self):
        """完整重建所有卡片（簡單可靠，無差分）"""
        self._rebuild_timer = None
        if self._rebuilding: return
        self._rebuilding = True

        sel_page        = self._selected.page if self._selected else None
        sel_multi_pages = {id(c.page) for c in self._selected_multi}

        self.canvas.update_idletasks()
        actual_w = self.canvas.winfo_width()
        canvas_w = max(600, actual_w if actual_w > 1 else self._canvas_w)
        card_w   = (canvas_w - 30) // COLS
        default_desc = self.desc_var.get() if hasattr(self, "desc_var") else ""
        default_loc  = self.loc_var.get()  if hasattr(self, "loc_var")  else ""

        # 銷毀所有舊卡片
        for card in self.cards:
            card.destroy()
        self.cards.clear()
        self._selected       = None
        self._selected_multi = []

        # 重建所有卡片
        for i, page in enumerate(self.pages):
            card = ThumbCard(self.thumb_frame, page, i, self, card_w=card_w)
            if page.get("desc") is None:
                card._syncing = True
                card.desc_var.set(default_desc)
                card._syncing = False
            if page.get("loc") is None and hasattr(card, "loc_var2"):
                card._syncing = True
                card.loc_var2.set(default_loc)
                card._syncing = False
            sk = page.get("sort_key","") or str(i+1)
            if not page.get("sort_key",""):
                page["sort_key"] = str(i+1)
            if hasattr(card, "sort_var"):
                card.sort_var.set(sk)
            row, col = divmod(i, COLS)
            card.grid(row=row, column=col, padx=4, pady=4, sticky="nw")
            self.cards.append(card)
            if page is sel_page:
                card.set_selected(True)
                self._selected = card
            elif id(page) in sel_multi_pages:
                card.set_selected(True)
                self._selected_multi.append(card)

        self.thumb_frame.update_idletasks()
        self.canvas.config(scrollregion=self.canvas.bbox("all"))
        if hasattr(self, "page_count_lbl"):
            self.page_count_lbl.config(text=f"共 {len(self.pages)} 頁")
        self._rebuilding = False

    # ── 專案存檔 / 匯入 ──
    def save_project(self):
        """儲存專案：輸出 Word + 複製照片 + 存 .phk"""
        if not self.template_path or not Path(self.template_path).exists():
            messagebox.showerror("錯誤", "請先選擇模板 DOCX！"); return
        if not self.pages:
            messagebox.showerror("錯誤", "請先新增照片！"); return

        output = self.output_var.get().strip()
        if not output:
            messagebox.showerror("錯誤", "請設定輸出路徑！"); return
        if not output.lower().endswith(".docx"):
            output += ".docx"
            self.output_var.set(output)

        output_path = Path(output)
        # 資料夾名稱 = Word 檔名（不含副檔名）
        folder_name = output_path.stem
        folder_path = output_path.parent / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)
        photos_dir = folder_path / "photos"
        photos_dir.mkdir(exist_ok=True)

        # 複製所有照片到 photos/，更新 page 路徑
        import pickle
        saved_pages = copy.deepcopy(self.pages)
        copied = {}   # {原路徑: 新路徑}，避免同一張複製多次
        img_counter = [0]

        def copy_photo(src):
            """複製一張照片，相同來源只複製一次"""
            if not src or not Path(src).exists():
                return src
            if src in copied:
                return copied[src]
            img_counter[0] += 1
            ext = Path(src).suffix or ".jpg"
            dst = photos_dir / f"img_{img_counter[0]:04d}{ext}"
            shutil.copy2(src, dst)
            copied[src] = str(dst)
            return str(dst)

        for page in saved_pages:
            page["paths"] = [copy_photo(p) for p in page.get("paths", [])]
            # orig_path 不複製到 photos 資料夾（避免重複）
            # .phk 裡保留原始路徑，讓匯入後仍可繼續編輯
            # photos 資料夾只放最終使用的照片（paths[0]）
            page["orig_path"] = page["paths"][0] if page.get("paths") else None

        # 儲存 .phk
        phk_path = folder_path / f"{folder_name}.phk"
        project_data = {
            "version":       1,
            "pages":         saved_pages,
            "desc":          self.desc_var.get(),
            "location":      self.loc_var.get(),
            "start_num":     self.start_var.get(),
            "template_path": self.template_path,
            "output_path":   str(folder_path / f"{folder_name}.docx"),
        }
        with open(str(phk_path), "wb") as f:
            pickle.dump(project_data, f)

        # 輸出 Word 到資料夾內
        word_out = str(folder_path / f"{folder_name}.docx")
        self.log(f"💾 儲存專案到：{folder_path}")

        def worker():
            try:
                import copy as _copy
                process_docx(self.template_path, word_out, self.pages,
                             self.desc_var.get().strip(),
                             self.loc_var.get().strip(),
                             self.start_var.get(), self.log,
                             label_merged=self.label_merged_var.get())
                self.log(f"📦 專案已儲存：{phk_path}")
                def done():
                    messagebox.showinfo("💾 儲存完成",
                        f"專案已儲存！\n\n資料夾：{folder_path}\n"
                        f"Word：{folder_name}.docx\n"
                        f"存檔：{folder_name}.phk")
                    if self.open_after_var.get():
                        try:
                            import os, sys
                            if sys.platform == "win32":
                                os.startfile(word_out)
                        except Exception as ex:
                            self.log(f"無法開啟：{ex}")
                self.root.after(0, done)
            except Exception as e:
                import traceback
                self.log(f"[ERROR] {e}\n{traceback.format_exc()}")
                self.root.after(0, lambda: messagebox.showerror("錯誤", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def load_project(self):
        """匯入專案：選 .phk 檔，載入設定繼續編輯"""
        phk_path = filedialog.askopenfilename(
            title="選擇專案檔案",
            filetypes=[("照片黏貼專案", "*.phk"), ("所有檔案", "*.*")])
        if not phk_path: return

        try:
            import pickle
            with open(phk_path, "rb") as f:
                data = pickle.load(f)
        except Exception as e:
            messagebox.showerror("錯誤", f"無法讀取專案檔案：{e}"); return

        # 驗證照片是否存在
        missing = []
        for page in data.get("pages", []):
            for p in page.get("paths", []):
                if p and not Path(p).exists():
                    missing.append(p)
        if missing:
            detail = "\n".join(missing[:5])
            if len(missing) > 5:
                detail += f"\n...（共 {len(missing)} 個）"
            ans = messagebox.askyesno("⚠️ 找不到照片",
                f"有 {len(missing)} 個照片檔案找不到：\n\n{detail}\n\n"
                f"可能原因：\n"
                f"• 照片已被移動或刪除\n"
                f"• 換了電腦或磁碟機\n\n"
                f"仍要載入嗎？（找不到的照片會顯示為空白）")
            if not ans: return

        # 套用設定
        self._save_history()
        self.pages.clear()
        self.pages.extend(data.get("pages", []))
        self.desc_var.set(data.get("desc", ""))
        self.loc_var.set(data.get("location", DEFAULT_LOCATION))
        self.start_var.set(data.get("start_num", 1))

        tmpl = data.get("template_path", "")
        if tmpl and Path(tmpl).exists():
            self.template_path = tmpl
            self.template_lbl.config(text=f"📄 {Path(tmpl).name}", fg="white")

        out = data.get("output_path", "")
        if out: self.output_var.set(out)

        self._selected = None
        self._selected_multi = []
        self._schedule_rebuild()
        self.log(f"📥 已匯入專案：{Path(phk_path).name}（{len(self.pages)} 頁）")
        messagebox.showinfo("匯入完成", f"已載入 {len(self.pages)} 頁，可繼續編輯！")

    def _on_close(self):
        """關閉視窗時清理執行緒池，避免程式卡住"""
        try:
            _load_pool.shutdown(wait=False)
        except Exception:
            pass
        self.root.destroy()

    def log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, msg+"\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")
        self.root.update()

    # ── 執行 ──
    def run(self):
        if not self.template_path or not Path(self.template_path).exists():
            messagebox.showerror("錯誤", "請先選擇模板 DOCX！"); return
        if not self.pages:
            messagebox.showerror("錯誤", "請先新增照片！"); return
        output = self.output_var.get().strip()
        if not output:
            messagebox.showerror("錯誤", "請設定輸出路徑！"); return
        # 自動補 .docx
        if not output.lower().endswith(".docx"):
            output += ".docx"
            self.output_var.set(output)

        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")
        self.log(f"開始處理 {len(self.pages)} 頁...")

        def worker():
            try:
                total = len(self.pages)
                self.log(f"🌿 開始處理 {total} 頁...")
                process_docx(self.template_path, output, self.pages,
                             self.desc_var.get().strip(),
                             self.loc_var.get().strip(),
                             self.start_var.get(), self.log,
                             label_merged=self.label_merged_var.get())
                def on_complete():
                    messagebox.showinfo("🌿 完成", f"輸出完成！\n\n{output}")
                    if self.open_after_var.get():
                        import subprocess, sys
                        try:
                            if sys.platform == "win32":
                                import os
                                os.startfile(output)
                            else:
                                subprocess.run(["open", output])
                        except Exception as ex:
                            messagebox.showwarning("提示", f"無法自動開啟：{ex}")
                self.root.after(0, on_complete)
            except Exception as e:
                import traceback
                self.log(f"[ERROR] {e}\n{traceback.format_exc()}")
                self.root.after(0, lambda: messagebox.showerror("錯誤", str(e)))

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
