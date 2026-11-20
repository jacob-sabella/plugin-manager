"""Find text on an MPC screenshot. The screens are a few flat colours with one clean typeface, so tesseract reads them well once the
picture is enlarged; each word comes back with its box, and neighbouring words on a line are joined so "Plugin Manager" can be asked for."""
import csv, io, re, subprocess
from PIL import Image, ImageOps

SCALE = 2


REGIONS = ((0, 0, 1280, 800), (0, 738, 1280, 800))   # the whole screen, and the button bar again by itself (it is missed inside the full picture)


def words(png):
    """[(text, x, y, w, h, line key)] in screen pixels."""
    full = Image.open(png).convert("L")
    out = []
    for ri, box in enumerate(REGIONS):
        im = full.crop(box); im = im.resize((im.width * SCALE, im.height * SCALE), Image.LANCZOS)
        for vi, variant in enumerate((im, ImageOps.invert(im))):     # light-on-dark and dark-on-light text are both on these screens
            buf = io.BytesIO(); variant.save(buf, "PNG")
            tsv = subprocess.run(["tesseract", "-", "-", "--psm", "11", "tsv"], input=buf.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode("utf-8", "replace")
            for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
                t = (r.get("text") or "").strip()
                if not t or float(r.get("conf") or -1) < 30: continue
                out.append((t, box[0] + int(r["left"]) // SCALE, box[1] + int(r["top"]) // SCALE, int(r["width"]) // SCALE, int(r["height"]) // SCALE,
                            (ri, vi, r["block_num"], r["par_num"], r["line_num"])))
    return out


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def find(png, text, ws=None):
    """Centres (x, y) of each place `text` appears (case and punctuation ignored), top to bottom."""
    ws = words(png) if ws is None else ws
    want = norm(text); hits = []
    for i, w in enumerate(ws):
        acc = ""; x0 = w[1]; y0 = w[2]; x1 = w[1] + w[3]; y1 = w[2] + w[4]
        for v in ws[i:i + 8]:
            if v[5] != w[5]: break
            acc += norm(v[0]); x1 = max(x1, v[1] + v[3]); y0 = min(y0, v[2]); y1 = max(y1, v[2] + v[4])
            if acc == want:
                c = ((x0 + x1) // 2, (y0 + y1) // 2)
                if not any(abs(c[0] - h[0]) < 12 and abs(c[1] - h[1]) < 12 for h in hits): hits.append(c)
                break
            if not want.startswith(acc): break
    return sorted(hits, key=lambda c: (c[1], c[0]))


def text(png):
    return " ".join(w[0] for w in words(png))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        for t in sys.argv[2:]: print(t, find(sys.argv[1], t))
    else:
        print(text(sys.argv[1]))
