"""Bundle fee-schedule source files into one paginated PDF (docs/specs/fee_model_spec.md, amendment 2026-10-23).

    venv/bin/python scripts/bundle_fee_sources.py <out.pdf> <text page.md> <image.png> ...

Page 1 is the text file, verbatim (characters outside Latin-1 transliterated). Each following page is one
image. Every page states its original file name and sha256, so the bundle can be checked against the
originals committed beside it. Standard library plus macOS `sips` (PNG to JPEG). Deterministic: no
timestamps are written.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

TRANSLIT = {"—": "-", "–": "-", "×": "x", "≥": ">=", "≤": "<=", "’": "'",
            "“": '"', "”": '"', "→": "->"}
PAGE_W, PAGE_H = 612, 792
MARGIN = 36
FOOTER_Y = 22


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def latin1(text):
    return "".join(TRANSLIT.get(ch, ch) if ord(ch) > 255 else ch for ch in text).encode("latin-1", "replace")


def pdf_str(b):
    return b"(" + b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def jpeg_size(data):
    i = 2
    while i < len(data):
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + length
    raise ValueError("no JPEG frame header")


def text_ops(lines, size, top):
    ops = [b"BT", b"/F1 %d Tf" % size, b"%d TL" % (size + 2), b"%d %d Td" % (MARGIN, top)]
    for ln in lines:
        ops.append(pdf_str(ln) + b" '")
    ops.append(b"ET")
    return b"\n".join(ops)


def build(out, text_file, images):
    objs = []

    def add(body):
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    total = 1 + len(images)
    pages = []
    raw = Path(text_file).read_text(encoding="utf-8")
    body = []
    for para in raw.splitlines():
        body += textwrap.wrap(para, 165) or [""]
    footer = [f"Page 1 of {total}. Source: {Path(text_file).name}, sha256 {sha256(text_file)}.",
              "Text reproduced verbatim; characters outside Latin-1 transliterated (em dash as -)."]
    content = text_ops([latin1(x) for x in body], 7, PAGE_H - MARGIN) + b"\n" + \
        text_ops([latin1(x) for x in footer], 7, FOOTER_Y)
    pages.append((content, {}))
    with tempfile.TemporaryDirectory() as tmp:
        for n, img in enumerate(images, start=2):
            jpg = Path(tmp) / f"{n}.jpg"
            subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "best", str(img), "--out", str(jpg)],
                           check=True, capture_output=True)
            data = jpg.read_bytes()
            w, h = jpeg_size(data)
            xobj = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
                       b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" % (w, h, len(data))
                       + data + b"\nendstream")
            scale = min((PAGE_W - 2 * MARGIN) / w, (PAGE_H - 4 * MARGIN) / h)
            dw, dh = w * scale, h * scale
            img_ops = b"q %.2f 0 0 %.2f %d %.2f cm /Im%d Do Q" % (dw, dh, MARGIN, PAGE_H - MARGIN - dh, n)
            footer = [f"Page {n} of {total}. Source: {Path(img).name}, sha256 {sha256(img)}.",
                      "Screenshot of kalshi.com/fee-schedule taken by the owner on 2026-10-23."]
            pages.append((img_ops + b"\n" + text_ops([latin1(x) for x in footer], 7, FOOTER_Y), {n: xobj}))
    pages_id = len(objs) + 1 + 2 * len(pages)
    kids = []
    for content, xobjs in pages:
        c = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        xo = b"".join(b"/Im%d %d 0 R " % (k, v) for k, v in xobjs.items())
        res = b"<< /Font << /F1 %d 0 R >>" % font + (b" /XObject << " + xo + b">>" if xo else b"") + b" >>"
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Resources %s /Contents %d 0 R >>"
                        % (pages_id, PAGE_W, PAGE_H, res, c)))
    assert add(b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)) \
        == pages_id
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    out_b = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out_b))
        out_b += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out_b)
    out_b += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out_b += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out_b += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, catalog, xref)
    Path(out).write_bytes(bytes(out_b))
    return total


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    n = build(sys.argv[1], sys.argv[2], sys.argv[3:])
    print(f"BUNDLE OK {sys.argv[1]} pages={n} sha256={sha256(sys.argv[1])}")
