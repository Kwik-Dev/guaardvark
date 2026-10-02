#!/usr/bin/env python3
"""Resolve conflict hunks by keeping the 'theirs' side, preserving all
auto-merged (non-conflicting) content around them."""
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").read().split("\n")
out, state = [], "normal"
dropped = kept = 0

for ln in lines:
    if ln.startswith("<<<<<<< "):
        state = "ours"
        continue
    if state == "ours" and ln == "=======":
        state = "theirs"
        continue
    if state == "theirs" and ln.startswith(">>>>>>> "):
        state = "normal"
        continue
    if state == "ours":
        dropped += 1
        continue
    if state == "theirs":
        kept += 1
    out.append(ln)

assert state == "normal", f"unterminated conflict in {path}"
open(path, "w", encoding="utf-8").write("\n".join(out))
print(f"{path}: dropped {dropped} ours-line(s), kept {kept} theirs-line(s)")
