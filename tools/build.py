#!/usr/bin/env python3
"""
Collage template repo tool.

  python3 tools/build.py              validate all templates, create missing thumbnails, rewrite manifest.json
  python3 tools/build.py --thumbs     regenerate ALL placeholder thumbnails (overwrites existing thumb.jpg!)
  python3 tools/build.py --thumbs --only <id>   regenerate the placeholder thumbnail of ONE template

What it does
  1. Validates every templates/<id>/template.json (schema v1).
  2. Computes sha256 of each template.json (and listed assets) and writes manifest.json.
  3. Refuses to continue if a template changed but its "version" was not increased.
  4. Creates placeholder thumbnails (thumb.jpg) for templates that have none.

REFERENCE LAYOUT: layout_slot() below defines the exact geometry the iOS app must reproduce.
Requires: Python 3.9+, Pillow (pip install pillow) for thumbnails only.
"""
import hashlib, json, math, os, re, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "templates")
MANIFEST = os.path.join(ROOT, "manifest.json")
SUPPORTED_SCHEMA = 1
FRAME_STYLES = {"none", "solid", "polaroid", "film"}
CATEGORIES = {"grid", "polaroid", "film", "editorial", "photobooth", "scrapbook"}
COLOR_TOKENS = {"$borderColor", "$backgroundColor"}
SHAPES = {"rect", "ellipse"}
HEX = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
EDGE_EPS = 0.001


# ----------------------------------------------------------------- reference layout
def layout_slot(slot, canvas_w, canvas_h, params):
    """
    Returns dict with pixel geometry for one slot (before rotation):
      outer: (x, y, w, h)  slot rectangle incl. frame
      photo: (x, y, w, h)  rectangle the photo fills
      insets: (left, top, right, bottom) frame thickness in px
      outer_radius, photo_radius, rotation_deg (clockwise, about the centre of `outer`)

    params: borderWidth, gap, cornerRadius, outerPadding (all relative to canvas WIDTH)
    """
    W, H = canvas_w, canvas_h
    pad = params["outerPadding"] * W
    cx0, cy0, cw, ch = pad, pad, W - 2 * pad, H - 2 * pad          # content area
    x = cx0 + slot["x"] * cw
    y = cy0 + slot["y"] * ch
    w = slot["width"] * cw
    h = slot["height"] * ch

    if slot.get("gapAware", False):                                  # gap only between neighbours
        g = params["gap"] * W / 2.0
        left = g if slot["x"] > EDGE_EPS else 0
        top = g if slot["y"] > EDGE_EPS else 0
        right = g if slot["x"] + slot["width"] < 1 - EDGE_EPS else 0
        bottom = g if slot["y"] + slot["height"] < 1 - EDGE_EPS else 0
        x, y = x + left, y + top
        w, h = w - left - right, h - top - bottom

    bw = params["borderWidth"] * W
    style = slot.get("frameStyle", "solid")
    if style == "none":
        ins = (0, 0, 0, 0)
    elif style == "polaroid":
        ins = (bw, bw, bw, bw * slot.get("polaroidBottomRatio", 4.0))
    elif style == "film":
        ins = (bw, bw * 0.25, bw, bw * 0.25)
    else:  # solid and unknown styles fall back to solid
        ins = (bw, bw, bw, bw)

    photo = (x + ins[0], y + ins[1], max(0, w - ins[0] - ins[2]), max(0, h - ins[1] - ins[3]))
    outer_r = params["cornerRadius"] * W
    photo_r = max(0, outer_r - min(ins[0], ins[1], ins[2], ins[3]))
    return {"outer": (x, y, w, h), "photo": photo, "insets": ins,
            "outer_radius": outer_r, "photo_radius": photo_r,
            "rotation_deg": slot.get("rotation", 0)}


# ----------------------------------------------------------------- validation
def err(tid, msg):
    raise ValueError(f"[{tid}] {msg}")


def num(tid, v, name, lo=None, hi=None):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        err(tid, f"{name} must be a number")
    if lo is not None and v < lo or hi is not None and v > hi:
        err(tid, f"{name}={v} out of range [{lo},{hi}]")


def validate(t, folder):
    tid = t.get("id", "?")
    if t.get("schemaVersion") != SUPPORTED_SCHEMA:
        err(tid, "unsupported schemaVersion")
    if tid != folder:
        err(tid, f"id must equal folder name '{folder}'")
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", tid):
        err(tid, "id must be lowercase-kebab-case")
    if not isinstance(t.get("version"), int) or t["version"] < 1:
        err(tid, "version must be an integer >= 1")
    if "en" not in t.get("name", {}):
        err(tid, "name.en is required")
    if t.get("category") not in CATEGORIES:
        err(tid, f"category must be one of {sorted(CATEGORIES)}")
    c = t["canvas"]
    num(tid, c["aspectWidth"], "canvas.aspectWidth", 1)
    num(tid, c["aspectHeight"], "canvas.aspectHeight", 1)
    if not HEX.match(c["backgroundColor"]):
        err(tid, "canvas.backgroundColor must be #RRGGBB or #RRGGBBAA")
    d = t["defaults"]
    for k in ("borderWidth", "gap", "cornerRadius", "outerPadding"):
        num(tid, d[k], f"defaults.{k}", 0, 0.5)
    if not HEX.match(d["borderColor"]):
        err(tid, "defaults.borderColor invalid")
    for k, spec in t.get("customizable", {}).items():
        if isinstance(spec, dict):
            num(tid, spec["min"], f"customizable.{k}.min", 0)
            num(tid, spec["max"], f"customizable.{k}.max", 0, 0.5)
            if k in d and not spec["min"] <= d[k] <= spec["max"]:
                err(tid, f"defaults.{k} outside customizable range")
    slots = t["slots"]
    if not 1 <= len(slots) <= 30:
        err(tid, "1..30 slots required")
    ids = set()
    for s in slots:
        if s["id"] in ids:
            err(tid, f"duplicate slot id {s['id']}")
        ids.add(s["id"])
        for k in ("x", "y", "width", "height"):
            num(tid, s[k], f"slot {s['id']}.{k}", 0, 1)
        if s["x"] + s["width"] > 1.0001 or s["y"] + s["height"] > 1.0001:
            if not s.get("rotation") and s.get("gapAware", False):
                err(tid, f"slot {s['id']} exceeds canvas")
        if s.get("frameStyle", "solid") not in FRAME_STYLES:
            err(tid, f"slot {s['id']} unknown frameStyle (allowed: {sorted(FRAME_STYLES)})")
    for dec in t.get("decorations", []):
        if dec.get("type") not in ("shape", "image"):
            err(tid, "decoration.type must be shape|image")
        if dec["type"] == "shape":
            if dec.get("shape", "rect") not in SHAPES:
                err(tid, f"decoration.shape must be one of {sorted(SHAPES)}")
            if not (HEX.match(dec["color"]) or dec["color"] in COLOR_TOKENS):
                err(tid, f"decoration.color must be hex or one of {sorted(COLOR_TOKENS)}")
        for k in ("x", "y", "width"):
            num(tid, dec[k], f"decoration.{k}", 0, 1)


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ----------------------------------------------------------------- thumbnails
PALETTE = [("#C9B8A6", "#8C7B6B"), ("#A8B5A0", "#6B7A63"), ("#B7C4CF", "#7C8E9C"),
           ("#D8B4A0", "#A67A66"), ("#BDB0C9", "#857794"), ("#C8C39A", "#8F8A5E")]


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def make_thumb(t, out_path):
    from PIL import Image, ImageDraw, ImageFilter
    S = 2
    TW = 600 * S
    aw, ah = t["canvas"]["aspectWidth"], t["canvas"]["aspectHeight"]
    W, H = TW, round(TW * ah / aw)
    bg = hex_rgb(t["canvas"]["backgroundColor"])
    img = Image.new("RGB", (W, H), bg)
    d = t["defaults"]
    params = {k: d[k] for k in ("borderWidth", "gap", "cornerRadius", "outerPadding")}
    items = [("slot", s["zIndex"], i, s) for i, s in enumerate(t["slots"])]
    items += [("deco", x.get("zIndex", 0), 100 + i, x) for i, x in enumerate(t.get("decorations", []))]
    items.sort(key=lambda it: (it[1], it[2]))

    def paste_rotated(layer, cx, cy, rot, shadow=None):
        nonlocal img
        if rot:
            layer = layer.rotate(-rot, expand=True, resample=Image.BICUBIC)  # clockwise-positive
        px, py = round(cx - layer.width / 2), round(cy - layer.height / 2)
        if shadow:
            sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
            alpha = layer.split()[3].point(lambda a: int(a * shadow["opacity"]))
            sh.putalpha(alpha)
            sh = sh.filter(ImageFilter.GaussianBlur(shadow["radius"] * W))
            img.paste(sh, (px, py + round(shadow["offsetY"] * H)), sh)
        img.paste(layer, (px, py), layer)

    for n, (kind, _, _, o) in enumerate(items):
        if kind == "slot":
            g = layout_slot(o, W, H, params)
            ox, oy, ow, oh = g["outer"]
            ow, oh = max(1, round(ow)), max(1, round(oh))
            layer = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            style = o.get("frameStyle", "solid")
            frame_col = hex_rgb(d["borderColor"])
            if style != "none":
                ld.rounded_rectangle([0, 0, ow - 1, oh - 1], radius=g["outer_radius"], fill=frame_col + (255,))
            px_, py_, pw, ph = g["photo"]
            rx, ry = px_ - ox, py_ - oy
            c1, c2 = PALETTE[n % len(PALETTE)]
            photo = Image.new("RGB", (max(1, round(pw)), max(1, round(ph))))
            pd = ImageDraw.Draw(photo)
            a, b = hex_rgb(c1), hex_rgb(c2)
            for yy in range(photo.height):
                k = yy / max(1, photo.height - 1)
                pd.line([(0, yy), (photo.width, yy)], fill=tuple(round(a[i] + (b[i] - a[i]) * k) for i in range(3)))
            m = min(photo.width, photo.height)
            pd.ellipse([photo.width * 0.62, photo.height * 0.14, photo.width * 0.62 + m * 0.18, photo.height * 0.14 + m * 0.18], fill=(255, 255, 255))
            pd.polygon([(0, photo.height), (photo.width * 0.35, photo.height * 0.55), (photo.width * 0.7, photo.height)], fill=tuple(round(v * 0.85) for v in b))
            mask = Image.new("L", photo.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, photo.width - 1, photo.height - 1], radius=g["photo_radius"], fill=255)
            layer.paste(photo, (round(rx), round(ry)), mask)
            if style == "film":
                bw = g["insets"][0]
                hole, pitch = bw * 0.38, bw * 0.8
                count = int((oh - pitch) // pitch)
                start = (oh - count * pitch) / 2 + (pitch - hole) / 2
                for i in range(count):
                    yy = start + i * pitch
                    for xx in (bw * 0.31, ow - bw * 0.31 - hole):
                        ld.rounded_rectangle([xx, yy, xx + hole, yy + hole], radius=hole * 0.2, fill=bg + (255,))
            paste_rotated(layer, ox + ow / 2, oy + oh / 2, o.get("rotation", 0), o.get("shadow"))
        else:
            if o["type"] != "shape":
                continue
            pad = params["outerPadding"] * W
            x, y = pad + o["x"] * (W - 2 * pad), pad + o["y"] * (H - 2 * pad)
            w, h = o["width"] * (W - 2 * pad), o["height"] * (H - 2 * pad)
            col = o["color"]
            col = d["borderColor"] if col == "$borderColor" else t["canvas"]["backgroundColor"] if col == "$backgroundColor" else col
            rgba = hex_rgb(col) + (round(255 * o.get("opacity", 1)),)
            size = (max(1, round(w)), max(1, round(h)))
            layer = Image.new("RGBA", size, (0, 0, 0, 0))
            if o.get("shape", "rect") == "ellipse":
                ImageDraw.Draw(layer).ellipse([0, 0, size[0] - 1, size[1] - 1], fill=rgba)
            else:
                layer = Image.new("RGBA", size, rgba)
            paste_rotated(layer, x + w / 2, y + h / 2, o.get("rotation", 0))
    img = img.resize((W // S, H // S), Image.LANCZOS)
    img.save(out_path, "JPEG", quality=88)


# ----------------------------------------------------------------- main
def main():
    force_thumbs = "--thumbs" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    old = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            old = {e["id"]: e for e in json.load(f).get("templates", [])}
    entries = []
    for folder in sorted(os.listdir(TEMPLATES)):
        tdir = os.path.join(TEMPLATES, folder)
        tpath = os.path.join(tdir, "template.json")
        if not os.path.isfile(tpath):
            continue
        with open(tpath, encoding="utf-8") as f:
            t = json.load(f)
        validate(t, folder)
        digest = sha256(tpath)
        prev = old.get(t["id"])
        if prev and prev["sha256"] != digest and prev["version"] >= t["version"]:
            err(t["id"], f"template.json changed but version is still {t['version']} - bump it")
        thumb = os.path.join(tdir, "thumb.jpg")
        if (force_thumbs and (only is None or only == t["id"])) or not os.path.exists(thumb):
            make_thumb(t, thumb)
            print(f"thumbnail written: {folder}")
        assets = []
        adir = os.path.join(tdir, "assets")
        if os.path.isdir(adir):
            for a in sorted(os.listdir(adir)):
                assets.append({"path": f"templates/{folder}/assets/{a}", "sha256": sha256(os.path.join(adir, a))})
        entries.append({
            "id": t["id"], "version": t["version"], "name": t["name"],
            "category": t["category"], "tags": t.get("tags", []),
            "aspectRatio": f"{t['canvas']['aspectWidth']}:{t['canvas']['aspectHeight']}",
            "photoCount": len(t["slots"]),
            "thumbnail": f"templates/{folder}/thumb.jpg",
            "definition": f"templates/{folder}/template.json",
            "sha256": digest, "assets": assets,
        })
    manifest = {"schemaVersion": SUPPORTED_SCHEMA,
                "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "templates": entries}
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"OK - {len(entries)} templates, manifest.json written")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError) as e:
        print("ERROR:", e)
        sys.exit(1)
