# -*- coding: utf-8 -*-
"""
Extract text from the labor cost PDF with a font-aware encoding fix.

Background:
  - THSarabunPSK (regular): ToUnicode CMap is missing the nikkhahit (U+0E4D)
    glyph, so a genuine "ำ" (sara am) is extracted as " า" (space + sara aa).
  - THSarabunPSK-Bold: ToUnicode CMap lacks the sara-aa glyph; a genuine "า"
    is extracted as "ำ" (U+0E33), while a genuine "ำ" is extracted as " ำ"
    (space + U+0E33).

Fix rule (applied per line, tracking char before the vowel):
  - ' ' followed by U+0E32  -> remove the space, emit U+0E33 (genuine ำ)
  - ' ' followed by U+0E33  -> remove the space, emit U+0E33 (genuine ำ)
  - U+0E33 in bold, not preceded by a space -> emit U+0E32 (corrupted า)
  - everything else unchanged
"""
import pymupdf

PDF = "บัญชีค่าแรงสำหรับถอดแบบคำนวฯราคากลางงานก่อสร้าง.pdf"

VOWEL_AA = "\u0e32"   # า
VOWEL_AM = "\u0e33"   # ำ


def fix_line(segments):
    """segments: list of (font, text). Returns fixed text for the whole line."""
    out = []
    prev = ""
    for font, text in segments:
        is_bold = "Bold" in font
        for c in text:
            if c == VOWEL_AM:
                if prev == " ":
                    # genuine ำ: drop the artifact space, keep ำ
                    if out and out[-1] == " ":
                        out.pop()
                    out.append(VOWEL_AM)
                    prev = VOWEL_AM
                elif is_bold:
                    # corrupted า in bold font
                    out.append(VOWEL_AA)
                    prev = VOWEL_AA
                else:
                    out.append(c)
                    prev = c
            elif c == VOWEL_AA:
                if prev == " ":
                    # genuine ำ rendered as ' า' in the regular font
                    if out and out[-1] == " ":
                        out.pop()
                    out.append(VOWEL_AM)
                    prev = VOWEL_AM
                else:
                    out.append(c)
                    prev = c
            else:
                out.append(c)
                prev = c
    return "".join(out)


def page_lines(page):
    d = page.get_text("dict")
    lines = []
    for block in d["blocks"]:
        for line in block.get("lines", []):
            segs = [(s["font"], s["text"]) for s in line["spans"]]
            lines.append((segs, fix_line(segs)))
    return lines


doc = pymupdf.open(PDF)
print(f"Pages: {doc.page_count}")

all_lines = []
for i, page in enumerate(doc):
    lines = page_lines(page)
    all_lines.append(lines)

with open("extracted.txt", "w", encoding="utf-8") as f:
    for i, lines in enumerate(all_lines):
        f.write(f"\n===== PAGE {i+1} =====\n")
        for segs, fixed in lines:
            if fixed.strip():
                f.write(fixed + "\n")
print("written extracted.txt")
