# -*- coding: utf-8 -*-
import json

data = json.load(open("data.json", encoding="utf-8"))

print("== types with 0 items ==")
for c in data["categories"]:
    for t in c["types"]:
        if not t["items"]:
            print(f"  {c['code']} / {t['code']} {t['name']!r}")

print("\n== price rows with missing unit ==")
for c in data["categories"]:
    for t in c["types"]:
        for it in t["items"]:
            for p in it["prices"]:
                if not p["unit"]:
                    print(f"  {t['code']} {it['name'][:50]!r} price={p['price']}")

print("\n== items with no prices (containers?) - first 40 ==")
cnt = 0
for c in data["categories"]:
    for t in c["types"]:
        for it in t["items"]:
            if not it["prices"]:
                cnt += 1
                if cnt <= 40:
                    sec = f"[sec {it.get('section')}] " if it.get("section") else ""
                    print(f"  {t['code']} {sec}{it.get('code') or ''} {it['name'][:60]!r}")
print(f"  ... total containers: {cnt}")

print("\n== items with empty names ==")
cnt = 0
for c in data["categories"]:
    for t in c["types"]:
        for it in t["items"]:
            if not it["name"]:
                cnt += 1
                if cnt <= 20:
                    print(f"  {t['code']} {it}")
print(f"  ... total: {cnt}")
