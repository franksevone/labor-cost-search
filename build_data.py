# -*- coding: utf-8 -*-
"""
Parse the labor cost PDF into structured JSON.

Hierarchy: หมวดหมู่ (category, bold "N") -> ประเภท (type, bold "N.M")
-> items (with price tiers).  X.Y.Z codes that group deeper codes become
"section" headers; English lines like "Ceiling mounted Type" become "group"
labels; tier notes ("จำนวน...", "ขนาด...") attach to prices.
"""
import json
import re
import pymupdf
from collections import Counter

PDF = "บัญชีค่าแรงสำหรับถอดแบบคำนวฯราคากลางงานก่อสร้าง.pdf"
VOWEL_AA = "\u0e32"
VOWEL_AM = "\u0e33"

MISPLACED_TITLES = {
    "3.3": "งานเดินท่อเหล็กเคลือบสังกะสี มอก. 277 ระบบระบายน้ำ",
}

# --------------------------------------------------------------------------
# 1. Extract lines with bold flags (font-aware encoding fix)
# --------------------------------------------------------------------------
def fix_line(segments):
    out, prev = [], ""
    for font, text in segments:
        is_bold = "Bold" in font
        for c in text:
            if c == VOWEL_AM:
                if prev == " ":
                    if out and out[-1] == " ":
                        out.pop()
                    out.append(VOWEL_AM)
                    prev = VOWEL_AM
                elif is_bold:
                    out.append(VOWEL_AA)
                    prev = VOWEL_AA
                else:
                    out.append(c)
                    prev = c
            elif c == VOWEL_AA and prev == " ":
                if out and out[-1] == " ":
                    out.pop()
                out.append(VOWEL_AM)
                prev = VOWEL_AM
            else:
                out.append(c)
                prev = c
    return "".join(out)


TYPE_CODE_RE = re.compile(r"^\d+\.\d+$")


def page_lines(page):
    d = page.get_text("dict")
    out = []
    for block in d["blocks"]:
        for line in block.get("lines", []):
            segs = [(s["font"], s["text"]) for s in line["spans"]]
            txt = fix_line(segs)
            bold = all("Bold" in f for f, _ in segs)
            x0 = min(s["bbox"][0] for s in line["spans"])
            y0 = line["bbox"][1]
            if txt.strip():
                out.append((txt.strip(), bold, x0, y0))
    # Table footnotes ("หมายเหตุ <text>") are printed below the table they
    # annotate, but pymupdf sometimes emits them in a later block than their
    # visual position (e.g. the 2.8 painting footnote lands after the 2.9
    # header).  Reposition each such line to just before the next type header
    # (2-part code) at or below it, so it attaches to the type whose table it
    # follows instead of the type parsed next in stream order.
    footnotes = [ln for ln in out
                 if ln[0].startswith("หมายเหตุ") and len(ln[0]) > len("หมายเหตุ")]
    if footnotes:
        result = list(out)
        for fn in sorted(footnotes, key=lambda ln: ln[3]):
            target = next((o for o in out
                           if o is not fn and o[3] >= fn[3] - 1.0
                           and TYPE_CODE_RE.match(o[0])), None)
            if target is None:
                continue
            fi = result.index(fn)
            ti = result.index(target)
            if fi > ti:
                result.pop(fi)
                result.insert(ti, fn)
        out = result
    return [(t, b, x) for t, b, x, _y in out]


doc = pymupdf.open(PDF)
stream = []
for page in doc:
    stream.extend(page_lines(page))

BOILER = {
    "ลำดับที่", "รายการ", "หน่วย", "ค่าแรง/หน่วย", "(บาท)", "หมายเหตุ",
    "บัญชีค่าแรงงาน/ดำเนินการสำหรับถอดแบบคำนวณราคากลางงานก่อสร้าง",
}
SKIP_EXACT = set(MISPLACED_TITLES.values())

PRICE_RE = re.compile(r"^\d[\d,]*$")
CODE_RE = re.compile(r"^\d+(\.\d+)+$")
CODE_LINE_RE = re.compile(r"^(\d+(?:\.\d+)+)(?:\s+(.*))?$")
# The source PDF has a typo "2.68" where it means item "2.6.8"
CODE_TYPOS = {"2.68": "2.6.8"}

# --------------------------------------------------------------------------
# 2. Unit vocabulary
# --------------------------------------------------------------------------
unit_counts = Counter()
for i, (txt, _b, _x0) in enumerate(stream[:-1]):
    if PRICE_RE.match(stream[i + 1][0]) and len(txt) <= 6 and not PRICE_RE.match(txt):
        unit_counts[txt] += 1
KNOWN_UNITS = {"เมตร", "ตร.ม.", "ชุด", "ต้น", "ชิ้น", "ลบ.ม.", "ตร.ฟ.", "บ่อ", "กก.",
               "ตัน", "ตัว", "ขั้น", "ท่อน", "ถัง", "จุด", "ดวง", "วงจร", "รายการ",
               "คู่", "แผ่น", "เส้น", "มัด", "ลูก", "หลัง", "บาน", "ห้อง", "เฟส",
               "เซ็ต", "เครื่อง", "ปล่อง", "องค์", "กิโลวัตต์", "KW"}
UNIT_SET = {u for u, n in unit_counts.items() if n >= 2} | KNOWN_UNITS
print("Units:", sorted(UNIT_SET))

# --------------------------------------------------------------------------
# 3. Helpers
# --------------------------------------------------------------------------
def has_thai(s):
    return any("\u0e01" <= c <= "\u0e5b" for c in s)

def next_line(stream, i):
    """First non-blank line after index i (immediate lookahead)."""
    j = i + 1
    while j < len(stream) and not stream[j][0]:
        j += 1
    return stream[j][0] if j < len(stream) else None

def scan_anchor(stream, i):
    """First structural anchor after i: bold, code, price, unit, dash item."""
    j = i + 1
    while j < len(stream):
        t, b, _x = stream[j]
        if b:
            return ("bold", t)
        if CODE_RE.match(t):
            return ("code", t)
        if PRICE_RE.match(t):
            return ("price", t)
        if t in UNIT_SET:
            return ("unit", t)
        if t.startswith("- "):
            return ("dash", t)
        j += 1
    return (None, None)

# Column boundary: descriptive text starting at x0 >= NOTE_X is in the
# หมายเหตุ column of the table (verified against the PDF: item column
# starts at x0~72, note column at x0~389, with zero lines crossing x0=370).
# Lines in the note column are always notes attached to the current row's
# price; lines in the item column are (new) item names.
NOTE_X = 370.0

# Item-description continuation openers: text in the item column starting
# with these (and followed by a unit) is a second description line of the
# current item's tier rows (e.g. "(เสาเข็ม คอร. รูปตัวไอ, รูปสี่เหลี่ยมตัน)",
# "หรืออื่น ๆ ที่คุณลักษณะเทียบเท่า", "ขนาด 6 มม., 8 มม."), not a new item.
# Verified against the PDF: every such line is a continuation; genuine item
# names starting with these are consumed by the code-line name loop.
CONTINUATION_OPENERS = ("(", "หรือ", "ชนิด", "ขนาด")

# --------------------------------------------------------------------------
# 4. State machine
# --------------------------------------------------------------------------
categories = []
cur_cat = None
cur_type = None
cur_item = None
cur_price = None
pending_unit = None
cur_section = None       # {"code","name","notes":[]}
cur_group = None         # English group label
type_notes = []

def new_item(code=None, name=None):
    global cur_item, cur_price, pending_unit
    cur_item = {"code": code, "name": name or "", "prices": []}
    cur_price = None
    pending_unit = None

def add_price(unit, price):
    global cur_price
    cur_price = {"unit": unit, "price": price, "note": ""}
    cur_item["prices"].append(cur_price)

def add_note(note_text):
    global cur_price
    if cur_price is not None:
        cur_price["note"] = (cur_price["note"] + " " + note_text).strip()
    elif cur_item is not None:
        cur_item["name"] = (cur_item["name"] + " " + note_text).strip()
    elif cur_section is not None:
        cur_section["notes"].append(note_text)
    else:
        type_notes.append(note_text)

def flush_item():
    global cur_item, cur_price
    if cur_item is None:
        return
    if cur_item["prices"] or cur_item["name"] or cur_item["code"]:
        if cur_section is not None:
            cur_item["section"] = cur_section["code"]
            cur_item["section_name"] = cur_section["name"]
        if cur_group is not None:
            cur_item["group"] = cur_group
        cur_type["items"].append(cur_item)
    cur_item = None
    cur_price = None

def add_footnote(footnote_text):
    """Attach a table-footnote line to the current type's notes."""
    if cur_type is not None:
        cur_type.setdefault("notes", []).append(footnote_text)
    else:
        type_notes.append(footnote_text)

i = 0
n = len(stream)
while i < n:
    txt, bold, x0 = stream[i]

    if txt in BOILER or txt.startswith("สิ่งที่ส่งมาด้วย") or txt in SKIP_EXACT:
        i += 1
        continue

    if bold:
        if PRICE_RE.match(txt):
            # bold number alone -> category header
            flush_item()
            cur_cat = {"code": txt, "name": "", "types": []}
            categories.append(cur_cat)
            cur_type = None
            cur_section = None
            cur_group = None
            i += 1
            if i < n and stream[i][1] and stream[i][0] not in BOILER:
                cur_cat["name"] = stream[i][0]
                i += 1
            continue
        if CODE_RE.match(txt):
            parts = txt.split(".")
            if len(parts) == 2:
                # type header
                flush_item()
                cur_type = {"code": txt, "name": "", "items": [], "notes": []}
                if cur_cat is None:
                    cur_cat = {"code": "?", "name": "", "types": []}
                    categories.append(cur_cat)
                cur_cat["types"].append(cur_type)
                cur_section = None
                cur_group = None
                i += 1
                name_parts = []
                while i < n and stream[i][1] and stream[i][0] not in BOILER:
                    name_parts.append(stream[i][0])
                    i += 1
                cur_type["name"] = " ".join(name_parts) if name_parts \
                    else MISPLACED_TITLES.get(txt, "")
                continue
        if CODE_RE.match(txt):
            parts = txt.split(".")
            if len(parts) == 2:
                # type header
                flush_item()
                cur_type = {"code": txt, "name": "", "items": [], "notes": []}
                if cur_cat is None:
                    cur_cat = {"code": "?", "name": "", "types": []}
                    categories.append(cur_cat)
                cur_cat["types"].append(cur_type)
                cur_section = None
                cur_group = None
                type_notes = []
                i += 1
                name_parts = []
                while i < n and stream[i][1] and stream[i][0] not in BOILER:
                    name_parts.append(stream[i][0])
                    i += 1
                cur_type["name"] = " ".join(name_parts) if name_parts \
                    else MISPLACED_TITLES.get(txt, "")
                continue
            # 3+ part code (bold) — treat like a regular code line
            # fall through to shared code handling below
        else:
            i += 1
            continue

    # ---- shared code-line handling (bold 3+ part and regular codes) ----
    m = CODE_LINE_RE.match(txt)
    if m:
        flush_item()
        code = m.group(1)
        inline = (m.group(2) or "").strip()
        nparts = len(code.split("."))
        if code in CODE_TYPOS:
            # "2.68" in the source is really item "2.6.8"
            code = CODE_TYPOS[code]
            new_item(code=code, name=inline)
            j = i + 1
            if not inline:
                # consume the first descriptive line as the name
                while j < n:
                    t2, b2, _x2 = stream[j]
                    if b2 or t2 in BOILER or CODE_RE.match(t2) or PRICE_RE.match(t2) \
                       or t2 in UNIT_SET or t2.startswith("- ") or t2 in SKIP_EXACT:
                        break
                    cur_item["name"] = t2
                    j += 1
                    break
            i = j
            continue
        if nparts == 2:
            # type header (bold or not — e.g. "3.15" is regular)
            cur_type = {"code": code, "name": "", "items": [], "notes": []}
            if cur_cat is None:
                cur_cat = {"code": "?", "name": "", "types": []}
                categories.append(cur_cat)
            cur_cat["types"].append(cur_type)
            cur_section = None
            cur_group = None
            j = i + 1
            if j < n and not stream[j][1] and stream[j][0] not in BOILER \
               and not CODE_RE.match(stream[j][0]) \
               and not PRICE_RE.match(stream[j][0]) \
               and stream[j][0] not in UNIT_SET \
               and not stream[j][0].startswith("- "):
                cur_type["name"] = stream[j][0]
                i = j + 1
            else:
                cur_type["name"] = MISPLACED_TITLES.get(code, "")
                i = j
            continue
        kind, anchor = scan_anchor(stream, i)
        if (nparts == 3 and kind == "code"
                and len(anchor.split(".")) > 3):
            # section header
            flush_item()
            cur_section = {"code": code, "name": inline, "notes": []}
            cur_group = None
            cur_type.setdefault("sections", []).append(cur_section)
            j = i + 1
            if not cur_section["name"]:
                # consume the first descriptive line as the section name
                while j < n:
                    t2, b2, _x2 = stream[j]
                    if b2 or t2 in BOILER or CODE_RE.match(t2) or PRICE_RE.match(t2) \
                       or t2 in UNIT_SET or t2.startswith("- ") or t2 in SKIP_EXACT:
                        break
                    cur_section["name"] = t2
                    j += 1
                    break
            i = j
            continue            # regular item with a code
        new_item(code=code, name=inline)
        j = i + 1
        if not inline:
            # consume the first descriptive line as the name
            while j < n:
                t2, b2, _x2 = stream[j]
                if b2 or t2 in BOILER or CODE_RE.match(t2) or PRICE_RE.match(t2) \
                   or t2 in UNIT_SET or t2.startswith("- ") or t2 in SKIP_EXACT:
                    break
                cur_item["name"] = t2
                j += 1
                break
        i = j
        continue

    # ---- regular text ----
    if PRICE_RE.match(txt):
        u = pending_unit
        if cur_item is None:
            new_item()
        add_price(u or "", int(txt.replace(",", "")))
        pending_unit = None
        i += 1
        continue

    if txt in UNIT_SET:
        pending_unit = txt
        i += 1
        continue

    # ---- descriptive line ----
    if txt.startswith("หมายเหตุ") and len(txt) > len("หมายเหตุ"):
        # table footnote (e.g. "หมายเหตุ มากกว่า 5,000 ตร.ม. ...") that the
        # PDF prints below the table; keep it as a type-level note, never as
        # a price note
        add_footnote(txt)
        i += 1
        continue
    if x0 >= NOTE_X:
        # หมายเหตุ column: always a note for the current row's price
        add_note(txt)
        i += 1
        continue
    nxt = next_line(stream, i)
    if txt.startswith("- "):
        if nxt in UNIT_SET:
            flush_item()
            new_item(name=txt[2:].strip())
        elif "หมายถึง" in txt:
            # table footnote defining terms (e.g. "- รื้อกอง หมายถึง...")
            add_footnote(txt)
        else:
            add_note(txt)
        i += 1
        continue
    if cur_item is not None and cur_item["prices"] and nxt not in UNIT_SET:
        add_note(txt)
        i += 1
        continue
    if nxt in UNIT_SET:
        if cur_item is not None and txt.startswith(CONTINUATION_OPENERS):
            if cur_item["name"] and \
               cur_item["name"].count("(") > cur_item["name"].count(")"):
                # the item name still has an unclosed "(" — this line is the
                # second line of the item name, not a tier note
                cur_item["name"] = (cur_item["name"] + " " + txt).strip()
            else:
                # description continuation of the current item (tier row)
                add_note(txt)
        else:
            flush_item()
            new_item(name=txt)
        i += 1
        continue
    if cur_item is not None and cur_item["prices"]:
        add_note(txt)
        i += 1
        continue
    if not has_thai(txt):
        # English group header
        flush_item()
        cur_group = txt
        i += 1
        continue
    if cur_section is not None:
        cur_section["notes"].append(txt)
        i += 1
        continue
    if cur_item is not None and cur_item["name"] and not cur_item["prices"]:
        cur_item["name"] += " " + txt
        i += 1
        continue
    type_notes.append(txt)
    i += 1

flush_item()

# --------------------------------------------------------------------------
# 5. Output
# --------------------------------------------------------------------------
# items without a name (the PDF names the item after the type) inherit the
# type name
for cat in categories:
    for t in cat["types"]:
        for it in t["items"]:
            if not it.get("name") and not it.get("code"):
                it["name"] = t["name"]

# prune empty sections
for cat in categories:
    for t in cat["types"]:
        t["sections"] = [s for s in t.get("sections", []) if s.get("name")]
        if not t["sections"]:
            t.pop("sections", None)
        if not t.get("notes"):
            t.pop("notes", None)

data = {
    "meta": {
        "title": "บัญชีค่าแรงงาน/ดำเนินการสำหรับถอดแบบคำนวณราคากลางงานก่อสร้าง",
        "source": "หนังสือกรมบัญชีกลาง ด่วนที่สุด ที่ กค 0433.2/ว 480 ลงวันที่ 26 มิถุนายน 2569",
        "unit": "ราคาเป็นบาทต่อหน่วย",
    },
    "categories": categories,
}

with open("data.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)

with open("data.js", "w", encoding="utf-8") as f:
    f.write("window.LABOR_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")

total_items = sum(len(t["items"]) for c in categories for t in c["types"])
total_prices = sum(len(it["prices"]) for c in categories for t in c["types"]
                   for it in t["items"])
missing_unit = sum(1 for c in categories for t in c["types"] for it in t["items"]
                   for p in it["prices"] if not p["unit"])
print(f"categories={len(categories)} "
      f"types={sum(len(c['types']) for c in categories)} "
      f"items={total_items} prices={total_prices} missing_unit={missing_unit}")
for cat in categories:
    print(f"  {cat['code']:>2} {cat['name']}: {len(cat['types'])} types")
