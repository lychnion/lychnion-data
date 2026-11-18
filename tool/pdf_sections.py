#!/usr/bin/env python3
"""Cuts chosen sections out of a long PDF manual into a text snapshot.

    python3 -I tool/pdf_sections.py MANUAL.pdf OUT.txt --from "16-1. " --to "18-1. " \\
        --keep "FIRST AID FOR A SNAKEBITE" --keep "HYPOTHERMIA" ...

The text between the first line starting with --from and the first line
starting with --to is split at headings (lines in capitals). Only sections
whose heading is in --keep are written, each as "## HEADING" followed by
its text, so tool/build_package.py splits passages there (use the result
as a "snapshot" in a package manifest). A kept section runs until the
next capitals heading of any kind. --drop-para "16-6." leaves out one
numbered paragraph.

Used for public-domain US government manuals (17 U.S.C. 105) where only
some chapters suit the app, e.g. first aid without the combat parts.
"""
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADING = re.compile(r"^[A-Z][A-Z0-9 ,()/'-]{3,70}$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--keep", action="append", required=True)
    # Numbered paragraphs to leave out, e.g. "16-6." (a fact that is wrong
    # for Japan, such as rabies); the paragraph runs to the next number.
    ap.add_argument("--drop-para", action="append", default=[])
    a = ap.parse_args()
    text = subprocess.run(["swift", str(ROOT / "tool" / "pdf2txt.swift"), a.pdf],
                          check=True, capture_output=True, text=True).stdout
    lines = text.splitlines()
    s = next(i for i, l in enumerate(lines) if l.startswith(a.start))
    e = next(i for i, l in enumerate(lines) if i > s and l.startswith(a.end))
    keep, out, current = set(a.keep), [], None
    dropping = False
    para = re.compile(r"^\d+-\d+\. ")
    for line in lines[s:e]:
        if para.match(line) or HEADING.match(line.strip()):
            dropping = any(line.startswith(d) for d in a.drop_para)
        if dropping:
            continue
        if HEADING.match(line.strip()):
            current = line.strip() if line.strip() in keep else None
            if current:
                out.append(f"\n## {current}")
            continue
        if current:
            out.append(line)
    missing = keep - {l.strip()[3:] for l in out if l.strip().startswith("## ")}
    if missing:
        raise SystemExit(f"headings not found: {sorted(missing)}")
    Path(a.out).write_text("\n".join(out).strip() + "\n", encoding="utf-8")
    print(f"wrote {a.out}: {len(keep)} sections, {len(out)} lines")


if __name__ == "__main__":
    main()
