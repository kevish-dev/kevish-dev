#!/usr/bin/env python3
"""Post-process GitAscii SVGs so GitHub can render them.

1. Merge duplicate attributes (e.g. two style="..." on one tag) -> valid XML.
2. Remove "--" from XML comments -> valid XML.
3. Embed the Showcase Cards GIFs as small base64 data URIs (external URLs
   can't load inside an SVG shown through <img>, and the full GIFs are too big).
4. Fail if any SVG is still not well-formed.

usage: fix_svg.py <gitascii.json> <svg> [<svg> ...]
"""
import base64, json, os, re, subprocess, sys, tempfile
import xml.dom.minidom as minidom

# slot -> (config key, target width px, fps, max base64 bytes). Full GIF duration is kept.
SLOTS = {
    "left": ("leftGifUrl", 480, 10, 900_000),
    "r1": ("card1GifUrl", 330, 10, 500_000),
    "r2": ("card2GifUrl", 330, 10, 500_000),
}


def fetch_small_gif(url, width, fps, max_b64):
    with tempfile.TemporaryDirectory() as d:
        src, pal, out = (os.path.join(d, n) for n in ("src", "pal.png", "out.gif"))
        subprocess.run(["curl", "-fsSL", "--max-time", "90", "-A", "Mozilla/5.0", "-o", src, url], check=True)
        for colors, w in ((64, width), (48, width), (32, width), (32, int(width * 0.85)), (24, int(width * 0.7)), (16, int(width * 0.6))):
            vf = f"fps={fps},scale={w}:-1:flags=lanczos"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src,
                            "-vf", f"{vf},palettegen=max_colors={colors}", pal], check=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-i", pal,
                            "-lavfi", f"{vf}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4",
                            "-loop", "0", out], check=True)
            data = open(out, "rb").read()
            if len(data) * 4 / 3 <= max_b64:
                break
        return "data:image/gif;base64," + base64.b64encode(data).decode()


def merge_dup_attrs(svg):
    attr = re.compile(r'\s([\w:.-]+)\s*=\s*("[^"]*"|\'[^\']*\')')

    def fix_tag(m):
        tag = m.group(0)
        if tag.startswith(("<!", "<?", "</")):
            return tag
        seen, dups = {}, {}
        for a in attr.finditer(tag):
            seen.setdefault(a.group(1), []).append(a)
        for name, ms in seen.items():
            if len(ms) > 1:
                dups[name] = ms
        if not dups:
            return tag
        # process from the end so offsets stay valid
        for name, ms in dups.items():
            vals = [x.group(2)[1:-1].strip().rstrip(";") for x in ms]
            merged = ";".join(v for v in vals if v) if name == "style" else vals[-1]
            for x in reversed(ms[1:]):
                tag = tag[:x.start()] + tag[x.end():]
            first = ms[0]
            tag = tag[:first.start()] + f' {name}="{merged}"' + tag[first.end():]
        return tag

    return re.sub(r"<[^<>]*>", fix_tag, svg)


def fix_comments(svg):
    def fix(m):
        body = re.sub(r"-{2,}", "-", m.group(1)).rstrip("-")
        return f"<!--{body}-->"
    return re.sub(r"<!--(.*?)-->", fix, svg, flags=re.S)


def minify(svg):
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
    svg = re.sub(r"@import url\([^)]*\);?", "", svg)
    svg = re.sub(r">\s*\n\s*<", "><", svg)
    return re.sub(r"\n\s*\n+", "\n", svg)


def minify_inner_svgs(svg):
    def fix(m):
        try:
            inner = minify(base64.b64decode(m.group(2)).decode("utf-8"))
        except Exception:
            return m.group(0)
        return m.group(1) + base64.b64encode(inner.encode()).decode() + m.group(3)
    return re.sub(r'(href="data:image/svg\+xml;base64,)([^"]*)(")', fix, svg)


def embed_gifs(svg, gifs):
    for slot, uri in gifs.items():
        pat = re.compile(
            r'(<g clip-path="url\(#ab-clip-%s-widget_[^)]*\)">\s*(?:<!--.*?-->\s*)?'
            r'<image[^>]*?href=")data:[^"]*(")' % slot, re.S)
        svg, n = pat.subn(lambda m: m.group(1) + uri + m.group(2), svg, count=1)
        print(f"  gif slot {slot}: {'embedded' if n else 'slot not found'}")
    return svg


def main():
    cfg_path, svgs = sys.argv[1], sys.argv[2:]
    cfg = json.load(open(cfg_path))
    gifs = {}
    for w in cfg.get("widgets", []):
        if w.get("widgetId") == "codeweb-showcase-cards" and w.get("visible", True):
            for slot, (key, width, fps, max_b64) in SLOTS.items():
                url = w.get("config", {}).get(key)
                if url:
                    try:
                        gifs[slot] = fetch_small_gif(url, width, fps, max_b64)
                        print(f"gif {slot}: {len(gifs[slot]) // 1024} KB")
                    except Exception as e:
                        print(f"warning: gif {slot} failed: {e}")
    bad = 0
    for path in svgs:
        svg = open(path, encoding="utf-8").read()
        svg = fix_comments(merge_dup_attrs(svg))
        svg = embed_gifs(svg, gifs)
        svg = minify_inner_svgs(minify(svg))
        try:
            minidom.parseString(svg.encode("utf-8"))
        except Exception as e:
            print(f"ERROR {path}: still invalid XML: {e}")
            bad += 1
            continue
        open(path, "w", encoding="utf-8").write(svg)
        print(f"{path}: ok ({len(svg) // 1024} KB)")
    sys.exit(1 if bad else 0)


main()
