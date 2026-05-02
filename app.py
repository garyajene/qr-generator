from flask import Flask, request, redirect
from io import BytesIO
import base64
import random
from collections import Counter
from PIL import Image, ImageDraw, ImageStat
import segno
import html

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ERROR_LEVEL = "h"
BOX = 16
QUIET = 6


def parse_hex_color(value):
    if not value:
        return None

    value = value.strip()
    if not value:
        return None

    if value.startswith("#"):
        value = value[1:]

    if len(value) != 6:
        return None

    try:
        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
        return (r, g, b)
    except ValueError:
        return None


def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(rgb[0], rgb[1], rgb[2])


def image_to_base64(img):
    out = BytesIO()
    img.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


def fetch_uploaded_image(file_storage):
    if not file_storage or file_storage.filename == "":
        return None
    try:
        data = file_storage.read()
        if not data:
            return None
        img = Image.open(BytesIO(data))
        img.load()
        return img.convert("RGBA")
    except Exception:
        return None


def fetch_image_from_hidden_b64(art_data):
    if not art_data:
        return None
    try:
        data = base64.b64decode(art_data)
        img = Image.open(BytesIO(data))
        img.load()
        return img.convert("RGBA")
    except Exception:
        return None


def color_distance(c1, c2):
    return ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2) ** 0.5


def is_near_white(rgb):
    return rgb[0] >= 220 and rgb[1] >= 220 and rgb[2] >= 220


def is_near_black(rgb):
    return rgb[0] <= 35 and rgb[1] <= 35 and rgb[2] <= 35


def sample_region_average(img, x, y, radius=6):
    x0 = max(0, x - radius)
    y0 = max(0, y - radius)
    x1 = min(img.width, x + radius + 1)
    y1 = min(img.height, y + radius + 1)

    region = img.crop((x0, y0, x1, y1)).convert("RGBA")
    pixels = list(region.getdata())

    valid = []
    for r, g, b, a in pixels:
        if a > 0:
            valid.append((r, g, b))

    if not valid:
        return (255, 255, 255)

    counts = Counter(valid)
    most_common = counts.most_common()
    top_count = most_common[0][1]
    tied_colors = [color for color, count in most_common if count == top_count]

    if len(tied_colors) == 1:
        return tied_colors[0]

    return random.choice(tied_colors)


def build_sample_points(width, height):
    points = []

    corner_xs = [
        int(width * 0.08),
        int(width * 0.18),
    ]
    corner_ys = [
        int(height * 0.08),
        int(height * 0.18),
    ]

    for x in corner_xs:
        for y in corner_ys:
            points.append((x, y))

    for x in [int(width * 0.82), int(width * 0.92)]:
        for y in corner_ys:
            points.append((x, y))

    for x in corner_xs:
        for y in [int(height * 0.82), int(height * 0.92)]:
            points.append((x, y))

    for x in [int(width * 0.82), int(width * 0.92)]:
        for y in [int(height * 0.82), int(height * 0.92)]:
            points.append((x, y))

    return points


def choose_background_color(art, bg_override=None):
    override_rgb = parse_hex_color(bg_override)
    if override_rgb is not None:
        return override_rgb

    if not art:
        return (255, 255, 255)

    test = art.convert("RGBA").resize((300, 300), Image.LANCZOS)
    points = build_sample_points(test.width, test.height)

    sampled_colors = []

    for x, y in points:
        rgb = sample_region_average(test, x, y, radius=7)
        sampled_colors.append(rgb)

    counts = Counter(sampled_colors)
    most_common = counts.most_common()

    if not most_common:
        return (255, 255, 255)

    top_count = most_common[0][1]
    tied_colors = [color for color, count in most_common if count == top_count]

    if len(tied_colors) == 1:
        winner = tied_colors[0]
        winner = tuple(max(0, min(255, c)) for c in winner)
        return winner

    winner = random.choice(tied_colors)
    winner = tuple(max(0, min(255, c)) for c in winner)
    return winner


def normalize_artwork_to_square(art, tolerance=0.12, bg_override=None):
    if not art:
        return None

    w, h = art.size
    if w == 0 or h == 0:
        return art

    ratio_diff = abs(w - h) / max(w, h)

    if ratio_diff <= tolerance:
        return art

    bg_color = choose_background_color(art, bg_override=bg_override)
    square_size = max(w, h)
    square = Image.new("RGBA", (square_size, square_size), (*bg_color, 255))

    paste_x = (square_size - w) // 2
    paste_y = (square_size - h) // 2
    square.paste(art, (paste_x, paste_y), art)

    return square


def qr_size_from_version(version):
    return 17 + 4 * version


def alignment_centers(version):
    if version <= 1:
        return []
    n = qr_size_from_version(version)
    num = version // 7 + 2
    if num == 2:
        return [6, n - 7]
    step = (n - 13) // (num - 1)
    if step % 2 == 1:
        step += 1
    centers = [6]
    last = n - 7
    for i in range(num - 2):
        centers.append(last - (num - 3 - i) * step)
    centers.append(last)
    return centers


def in_finder_or_separator(r, c, n):
    return (r <= 8 and c <= 8) or (r <= 8 and c >= n - 9) or (r >= n - 9 and c <= 8)


def in_timing(r, c, n):
    return (r == 6 and 8 <= c <= n - 9) or (c == 6 and 8 <= r <= n - 9)


def in_format_info(r, c, n):
    return (r == 8 and (c <= 8 or c >= n - 9)) or (c == 8 and (r <= 8 or r >= n - 9))


def in_alignment(r, c, version):
    if version <= 1:
        return False
    centers = alignment_centers(version)
    n = qr_size_from_version(version)
    for cy in centers:
        for cx in centers:
            if (cx == 6 and cy == 6) or (cx == 6 and cy == n - 7) or (cx == n - 7 and cy == 6):
                continue
            if abs(r - cy) <= 2 and abs(c - cx) <= 2:
                return True
    return False


def is_protected(r, c, n, version):
    return (
        in_finder_or_separator(r, c, n)
        or in_timing(r, c, n)
        or in_format_info(r, c, n)
        or in_alignment(r, c, version)
    )


def matrix_from_segno(qr):
    return [[bool(v) for v in row] for row in qr.matrix]


def analyze_complexity(img):
    gray = img.convert("L")
    stat = ImageStat.Stat(gray)
    stddev = stat.stddev[0] / 255.0
    extrema = gray.getextrema()
    range_val = (extrema[1] - extrema[0]) / 255.0
    complexity = (stddev * 0.7) + (range_val * 0.3)
    return min(1.0, complexity)


def get_adaptive_dot_scale(complexity):
    if complexity < 0.25:
        return 0.46
    elif complexity < 0.45:
        return 0.48
    elif complexity < 0.65:
        return 0.50
    elif complexity < 0.80:
        return 0.52
    else:
        return 0.54


def generate_branded_qr(data, art=None, bg_override=None):
    qr = segno.make(data, error=ERROR_LEVEL)
    matrix = matrix_from_segno(qr)
    version = int(qr.version)
    n = len(matrix)

    bg_color = choose_background_color(art, bg_override=bg_override)
    dark_color = (0, 0, 0)
    light_color = (255, 255, 255)

    size = (n + 2 * QUIET) * BOX
    canvas = Image.new("RGBA", (size, size), (*bg_color, 255))
    draw = ImageDraw.Draw(canvas)

    dot_scale = 0.48

    if art:
        complexity = analyze_complexity(art)
        dot_scale = get_adaptive_dot_scale(complexity)
        art_resized = art.resize((n * BOX, n * BOX), Image.LANCZOS)
        canvas.paste(art_resized, (QUIET * BOX, QUIET * BOX), art_resized)

    def draw_dot(x0, y0, x1, y1, scale, color):
        pad = (1.0 - scale) * BOX / 2.0
        draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=color)

    for r in range(n):
        for c in range(n):
            x0 = (QUIET + c) * BOX
            y0 = (QUIET + r) * BOX
            x1 = x0 + BOX
            y1 = y0 + BOX

            if is_protected(r, c, n, version):
                draw.rectangle(
                    [x0, y0, x1, y1],
                    fill=(*dark_color, 255) if matrix[r][c] else (*light_color, 255)
                )
                continue

            if matrix[r][c]:
                draw_dot(x0, y0, x1, y1, dot_scale, (*dark_color, 255))
            else:
                white_scale = max(0.35, min(0.85, dot_scale * 0.88))
                draw_dot(x0, y0, x1, y1, white_scale, (*light_color, 255))

    qpx = QUIET * BOX
    draw.rectangle([0, 0, size, qpx], fill=(*bg_color, 255))
    draw.rectangle([0, size - qpx, size, size], fill=(*bg_color, 255))
    draw.rectangle([0, 0, qpx, size], fill=(*bg_color, 255))
    draw.rectangle([size - qpx, 0, size, size], fill=(*bg_color, 255))

    return canvas.convert("RGBA")


def draw_simple_finder(draw, x, y, module_box, stroke_color, fill_color):
    outer_left = x + module_box
    outer_top = y + module_box
    outer_right = x + (7 * module_box)
    outer_bottom = y + (7 * module_box)

    stroke_w = max(2, int(module_box * 0.95))
    radius = max(6, int(module_box * 1.25))

    draw.rounded_rectangle(
        [outer_left, outer_top, outer_right, outer_bottom],
        radius=radius,
        outline=stroke_color,
        width=stroke_w,
    )

    center_x = x + (4 * module_box)
    center_y = y + (4 * module_box)
    center_radius = max(5, int(module_box * 1.5))

    draw.ellipse(
        [
            center_x - center_radius,
            center_y - center_radius,
            center_x + center_radius,
            center_y + center_radius,
        ],
        fill=fill_color,
    )


def generate_simple_qr(data, logo=None):
    """
    Simple QR must stay scanner-safe.
    This uses Segno's standard renderer for the QR itself, then places a small
    logo badge in the center. It does not manually redraw the QR matrix.
    """
    qr = segno.make(data, error=ERROR_LEVEL)

    out = BytesIO()
    qr.save(
        out,
        kind="png",
        scale=BOX,
        border=QUIET,
        dark="black",
        light="white",
    )
    out.seek(0)

    img = Image.open(out).convert("RGBA")
    draw = ImageDraw.Draw(img)

    if logo:
        logo = logo.convert("RGBA")

        # Keep the logo conservative so error correction can still recover.
        max_logo_side = int(img.width * 0.14)
        logo.thumbnail((max_logo_side, max_logo_side), Image.LANCZOS)

        pad = max(8, int(img.width * 0.018))
        badge_w = logo.width + pad * 2
        badge_h = logo.height + pad * 2

        badge_x0 = (img.width - badge_w) // 2
        badge_y0 = (img.height - badge_h) // 2
        badge_x1 = badge_x0 + badge_w
        badge_y1 = badge_y0 + badge_h

        draw.rounded_rectangle(
            [badge_x0, badge_y0, badge_x1, badge_y1],
            radius=max(8, pad),
            fill=(255, 255, 255, 255),
        )

        logo_x = (img.width - logo.width) // 2
        logo_y = (img.height - logo.height) // 2
        img.paste(logo, (logo_x, logo_y), logo)

    return img

def create_dome_only_qr(qr_img, output_size=900):
    bg_color = qr_img.convert("RGB").getpixel((5, 5))
    dome_qr = Image.new("RGBA", (output_size, output_size), (*bg_color, 255))

    qr_x = (output_size - qr_img.width) // 2
    qr_y = (output_size - qr_img.height) // 2

    dome_qr.paste(qr_img, (qr_x, qr_y), qr_img)
    return dome_qr


def trim_qr_for_mockup(img):
    crop_px = max(1, (QUIET * BOX) // 2)
    return img.crop((crop_px, crop_px, img.width - crop_px, img.height - crop_px))


def create_card_mockup(qr_img):
    card = Image.open("static/blackcard.png").convert("RGBA")
    qr_crop = trim_qr_for_mockup(qr_img)

    card_w, card_h = card.size

    qr_target_w = int(card_w * 0.32)
    qr_target_h = qr_target_w
    qr_small = qr_crop.resize((qr_target_w, qr_target_h), Image.LANCZOS)

    margin_x = int(card_w * 0.05)
    margin_y = int(card_h * 0.07)

    qr_x = card_w - qr_target_w - margin_x
    qr_y = card_h - qr_target_h - margin_y

    card.paste(qr_small, (qr_x, qr_y), qr_small)
    return card


def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_mask.png").convert("RGBA")
    dome_w, dome_h = dome.size

    dome_qr = create_dome_only_qr(qr_img, output_size=900)
    dome_base = dome_qr.resize((dome_w, dome_h), Image.LANCZOS)

    dome_base.alpha_composite(dome, (0, 0))

    final_w = int(dome_w * 0.50)
    final_h = int(dome_h * 0.50)
    return dome_base.resize((final_w, final_h), Image.LANCZOS)


def render_page(
    qr_img_b64=None,
    card_mockup_b64=None,
    dome_mockup_b64=None,
    data_value="",
    art_data_b64="",
    bg_override_value="",
    current_bg_hex="#ffffff",
    qr_style="artistic",
):
    safe_data_value = html.escape(data_value or "")
    safe_art_data_b64 = html.escape(art_data_b64 or "")
    safe_bg_override_value = html.escape(bg_override_value or "")
    safe_current_bg_hex = html.escape(current_bg_hex or "#ffffff")
    safe_qr_style = (qr_style or "artistic").strip().lower()

    artistic_selected = "active" if safe_qr_style == "artistic" else ""
    simple_selected = "active" if safe_qr_style == "simple" else ""

    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>QR Generator</title>
<style>
body {{
    font-family: Arial, sans-serif;
    padding: 30px;
    background: #ffffff;
}}

h1 {{
    margin-bottom: 24px;
}}

.label {{
    font-weight: bold;
    margin-bottom: 8px;
}}

input[type="text"] {{
    width: 360px;
    padding: 10px;
    font-size: 16px;
}}

.qr-type-options {{
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
    margin: 8px 0 22px 0;
}}

.qr-type-card {{
    width: 190px;
    min-height: 86px;
    border: 2px solid #d0d0d0;
    border-radius: 14px;
    background: #ffffff;
    padding: 16px;
    text-align: left;
    cursor: pointer;
    transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
}}

.qr-type-card:hover {{
    border-color: #777;
}}

.qr-type-card.active {{
    border-color: #000000;
    box-shadow: 0 0 0 2px rgba(0,0,0,0.08);
    background: #f7f7f7;
}}

.qr-type-title {{
    font-size: 17px;
    font-weight: bold;
    margin-bottom: 6px;
}}

.qr-type-desc {{
    font-size: 13px;
    line-height: 1.35;
    color: #555;
}}

#dropzone {{
    width: 420px;
    height: 220px;
    border: 2px dashed #999;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    margin-top: 10px;
    background: #fff;
    text-align: center;
}}

#dropzone.hover {{
    border-color: #000;
}}

#preview {{
    max-width: 260px;
    max-height: 180px;
    display: none;
}}

button {{
    margin-top: 16px;
    padding: 10px 18px;
    font-size: 16px;
    cursor: pointer;
}}

.results {{
    margin-top: 40px;
}}

.result-block {{
    margin-top: 30px;
}}

.generated-qr {{
    max-width: 360px;
    height: auto;
    display: block;
    margin-top: 12px;
    background: #fff;
}}

.mockups {{
    display: flex;
    gap: 40px;
    flex-wrap: wrap;
    align-items: flex-start;
}}

.mockup-card {{
    max-width: 540px;
    height: auto;
    display: block;
    margin-top: 12px;
}}

.mockup-dome {{
    max-width: 200px;
    height: auto;
    display: block;
    margin-top: 12px;
}}

.subhead {{
    font-weight: bold;
    margin-bottom: 8px;
}}

.bg-tools {{
    margin-top: 20px;
    padding: 18px;
    border: 1px solid #ddd;
    background: #fafafa;
    max-width: 640px;
    border-radius: 10px;
}}

.bg-tools-head {{
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
}}

.bg-current-wrap {{
    display: inline-flex;
    align-items: center;
    gap: 10px;
}}

.bg-swatch {{
    width: 22px;
    height: 22px;
    border: 1px solid #999;
    display: inline-block;
    vertical-align: middle;
    border-radius: 4px;
    background: {safe_current_bg_hex};
}}

.small-note {{
    font-size: 14px;
    color: #555;
    margin-top: 8px;
}}

#bg_tools_panel {{
    display: none;
    margin-top: 18px;
}}

.picker-dialog {{
    width: 500px;
    max-width: 100%;
    background: #f3f3f3;
    border: 1px solid #d7d7d7;
    border-radius: 16px;
    overflow: hidden;
}}

.picker-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 18px 20px;
    border-bottom: 1px solid #d7d7d7;
    background: #f5f5f5;
}}

.picker-title {{
    font-size: 28px;
    font-weight: 700;
    line-height: 1;
}}

.picker-close {{
    font-size: 24px;
    line-height: 1;
    border: none;
    background: transparent;
    margin: 0;
    padding: 0;
    cursor: pointer;
}}

.picker-body {{
    padding: 18px 20px 20px 20px;
}}

.sv-wrap {{
    position: relative;
    width: 100%;
    max-width: 460px;
    aspect-ratio: 1.28 / 1;
    border-radius: 12px;
    overflow: hidden;
    cursor: crosshair;
    background: #ff0058;
}}

#sv_canvas {{
    width: 100%;
    height: 100%;
    display: block;
}}

#sv_knob {{
    position: absolute;
    width: 16px;
    height: 16px;
    border: 2px solid #fff;
    border-radius: 50%;
    box-shadow: 0 0 0 1px rgba(0,0,0,0.3);
    transform: translate(-8px, -8px);
    pointer-events: none;
}}

.hue-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 18px;
}}

.no-color-chip {{
    width: 28px;
    height: 28px;
    border-radius: 50%;
    border: 1px solid #c7c7c7;
    background:
      linear-gradient(135deg, transparent 43%, #c33 43%, #c33 57%, transparent 57%),
      #f4f4f4;
}}

.hue-wrap {{
    position: relative;
    flex: 1;
    height: 18px;
    border-radius: 999px;
    overflow: hidden;
    cursor: pointer;
}}

#hue_canvas {{
    width: 100%;
    height: 100%;
    display: block;
}}

#hue_knob {{
    position: absolute;
    top: 50%;
    width: 16px;
    height: 16px;
    border: 3px solid #fff;
    border-radius: 50%;
    box-shadow: 0 0 0 1px rgba(0,0,0,0.3);
    transform: translate(-8px, -50%);
    pointer-events: none;
}}

.alpha-row {{
    display: flex;
    align-items: center;
    gap: 16px;
    margin-top: 24px;
}}

.dropper-btn-square {{
    width: 100px;
    height: 48px;
    border-radius: 10px;
    border: 1px solid #d0d4da;
    background: #d9dde3;
    margin-top: 0;
}}

.alpha-label-block {{
    flex: 1;
}}

.alpha-label-top {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
}}

.alpha-title {{
    font-size: 20px;
}}

.alpha-percent {{
    font-size: 20px;
}}

.alpha-wrap {{
    position: relative;
    height: 12px;
}}

#alpha_slider {{
    width: 100%;
    margin: 0;
}}

.values-row {{
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 22px;
}}

.value-box {{
    min-width: 95px;
    text-align: center;
}}

.value-box input {{
    width: 100%;
    box-sizing: border-box;
    border: 1px solid #d0d4da;
    background: #f3f4f6;
    border-radius: 14px;
    padding: 10px 8px;
    font-size: 20px;
    text-align: center;
}}

.value-box .lab {{
    margin-top: 6px;
    color: #666;
    font-size: 15px;
}}

.doc-colors-title {{
    margin-top: 26px;
    padding-top: 18px;
    border-top: 1px solid #d7d7d7;
    font-size: 18px;
    font-weight: 500;
}}

.doc-colors {{
    display: flex;
    gap: 14px;
    margin-top: 14px;
    flex-wrap: wrap;
}}

.doc-swatch {{
    width: 38px;
    height: 38px;
    border-radius: 50%;
    border: 2px solid #ddd;
    padding: 0;
    cursor: pointer;
}}

.doc-swatch:hover {{
    border-color: #000;
}}

.apply-row {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    align-items: center;
    margin-top: 22px;
}}

.apply-btn {{
    margin-top: 0;
}}

@media (max-width: 640px) {{
    .picker-title {{
        font-size: 22px;
    }}

    .alpha-title,
    .alpha-percent {{
        font-size: 18px;
    }}
}}
</style>
</head>
<body>

<h1>QR Generator</h1>

<form action="/" method="post" enctype="multipart/form-data">
    <div class="label">QR Data</div>
    <input type="text" name="data" required placeholder="Enter QR Data" value="{safe_data_value}"><br><br>

    <div class="label">QR Type</div>
    <input type="hidden" name="qr_style" id="qr_style" value="{safe_qr_style}">
    <div class="qr-type-options">
        <button type="button" id="simple_qr_card" class="qr-type-card {simple_selected}" onclick="selectQRStyle('simple')">
            <div class="qr-type-title">Simple QR</div>
            <div class="qr-type-desc">Clean black QR code with your logo in the center.</div>
        </button>
        <button type="button" id="branded_qr_card" class="qr-type-card {artistic_selected}" onclick="selectQRStyle('artistic')">
            <div class="qr-type-title">Branded QR</div>
            <div class="qr-type-desc">Custom logo-driven QR code using your artwork and colors.</div>
        </button>
    </div>

    <div class="label">Upload Artwork (optional)</div>
    <div id="dropzone">
        <span id="droptext">Drop Image Here or Click</span>
        <img id="preview" />
    </div>
    <input type="file" id="artfile" name="artfile" accept="image/*" style="display:none">
    <input type="hidden" name="art_data" id="art_data" value="{safe_art_data_b64}">

    <br>
    <button type="submit">Generate</button>

    <div class="results">
        {f'''
        <div class="result-block">
            <h2>Generated QR</h2>
            <img class="generated-qr" src="data:image/png;base64,{qr_img_b64}">
        </div>
        ''' if qr_img_b64 else ''}

        {f'''
        <div class="bg-tools">
            <div class="bg-tools-head">
                <button type="button" id="toggle-bg-tools">Change Background Color</button>
                <span class="bg-current-wrap">
                    <strong>Current:</strong>
                    <span class="bg-swatch" id="current_bg_swatch"></span>
                    <span id="current_bg_label">{safe_current_bg_hex}</span>
                </span>
            </div>

            <div id="bg_tools_panel">
                <div class="picker-dialog">
                    <div class="picker-header">
                        <div class="picker-title">Solid Color</div>
                        <button type="button" class="picker-close" id="picker_close_btn">×</button>
                    </div>

                    <div class="picker-body">
                        <div class="sv-wrap" id="sv_wrap">
                            <canvas id="sv_canvas" width="460" height="360"></canvas>
                            <div id="sv_knob"></div>
                        </div>

                        <div class="hue-row">
                            <div class="no-color-chip"></div>
                            <div class="hue-wrap" id="hue_wrap">
                                <canvas id="hue_canvas" width="420" height="18"></canvas>
                                <div id="hue_knob"></div>
                            </div>
                        </div>

                        <div class="alpha-row">
                            <button type="button" class="dropper-btn-square" id="eyedropper_btn">Pick From Image</button>

                            <div class="alpha-label-block">
                                <div class="alpha-label-top">
                                    <div class="alpha-title">Opacity/Alpha</div>
                                    <div class="alpha-percent">100%</div>
                                </div>
                                <div class="alpha-wrap">
                                    <input type="range" id="alpha_slider" min="100" max="100" value="100">
                                </div>
                            </div>
                        </div>

                        <div class="values-row">
                            <div class="value-box">
                                <input type="text" id="bg_override" name="bg_override" value="{safe_bg_override_value or safe_current_bg_hex}">
                                <div class="lab">HEX</div>
                            </div>

                            <div class="value-box">
                                <input type="text" id="r_val" value="255">
                                <div class="lab">R</div>
                            </div>

                            <div class="value-box">
                                <input type="text" id="g_val" value="255">
                                <div class="lab">G</div>
                            </div>

                            <div class="value-box">
                                <input type="text" id="b_val" value="255">
                                <div class="lab">B</div>
                            </div>
                        </div>

                        <div class="doc-colors-title">Document Colors</div>
                        <div class="doc-colors">
                            <button type="button" class="doc-swatch" data-color="#000000" style="background:#000000;"></button>
                            <button type="button" class="doc-swatch" data-color="#ffffff" style="background:#ffffff;"></button>
                            <button type="button" class="doc-swatch" data-color="{safe_current_bg_hex}" style="background:{safe_current_bg_hex};"></button>
                        </div>

                        <div class="apply-row">
                            <button type="submit" class="apply-btn">Apply Background Color</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        ''' if qr_img_b64 else ''}

        {f'''
        <div class="result-block">
            <h2>Mockups</h2>
            <div class="mockups">
                <div>
                    <div class="subhead">Business Card</div>
                    <img class="mockup-card" src="data:image/png;base64,{card_mockup_b64}">
                </div>
                <div>
                    <div class="subhead">Dome Sticker</div>
                    <img class="mockup-dome" src="data:image/png;base64,{dome_mockup_b64}">
                </div>
            </div>
        </div>
        ''' if card_mockup_b64 and dome_mockup_b64 else ''}
    </div>
</form>

<script>
function selectQRStyle(style) {{
    const qrStyleInput = document.getElementById("qr_style");
    const simpleCard = document.getElementById("simple_qr_card");
    const brandedCard = document.getElementById("branded_qr_card");

    if (qrStyleInput) {{
        qrStyleInput.value = style;
    }}

    if (simpleCard) {{
        simpleCard.classList.toggle("active", style === "simple");
    }}

    if (brandedCard) {{
        brandedCard.classList.toggle("active", style === "artistic");
    }}
}}

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("artfile");
const preview = document.getElementById("preview");
const droptext = document.getElementById("droptext");
const artDataInput = document.getElementById("art_data");

dropzone.onclick = () => fileInput.click();

function loadFileIntoPreview(file) {{
    if (!file) return;
    preview.src = URL.createObjectURL(file);
    preview.style.display = "block";
    droptext.style.display = "none";

    const reader = new FileReader();
    reader.onload = function(e) {{
        const result = e.target.result || "";
        const parts = result.split(",");
        if (parts.length === 2) {{
            artDataInput.value = parts[1];
        }}
    }};
    reader.readAsDataURL(file);
}}

fileInput.onchange = () => {{
    const file = fileInput.files[0];
    if (file) {{
        loadFileIntoPreview(file);
    }}
}};

dropzone.addEventListener("dragover", e => {{
    e.preventDefault();
    dropzone.classList.add("hover");
}});

dropzone.addEventListener("dragleave", () => {{
    dropzone.classList.remove("hover");
}});

dropzone.addEventListener("drop", e => {{
    e.preventDefault();
    dropzone.classList.remove("hover");
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {{
        fileInput.files = e.dataTransfer.files;
        const file = e.dataTransfer.files[0];
        loadFileIntoPreview(file);
    }}
}});

const toggleBgToolsBtn = document.getElementById("toggle-bg-tools");
const bgToolsPanel = document.getElementById("bg_tools_panel");
const pickerCloseBtn = document.getElementById("picker_close_btn");
const currentBgLabel = document.getElementById("current_bg_label");
const currentBgSwatch = document.getElementById("current_bg_swatch");
const bgOverrideInput = document.getElementById("bg_override");
const rVal = document.getElementById("r_val");
const gVal = document.getElementById("g_val");
const bVal = document.getElementById("b_val");
const eyedropperBtn = document.getElementById("eyedropper_btn");
const docSwatches = document.querySelectorAll(".doc-swatch");

const svCanvas = document.getElementById("sv_canvas");
const svWrap = document.getElementById("sv_wrap");
const svKnob = document.getElementById("sv_knob");
const hueCanvas = document.getElementById("hue_canvas");
const hueWrap = document.getElementById("hue_wrap");
const hueKnob = document.getElementById("hue_knob");

let hue = 340;
let sat = 1;
let val = 1;
let alpha = 1;

function normalizeHex(value) {{
    if (!value) return "";
    let v = value.trim();
    if (!v.startsWith("#")) v = "#" + v;
    if (/^#[0-9a-fA-F]{{6}}$/.test(v)) return v.toLowerCase();
    return "";
}}

function clamp(num, min, max) {{
    return Math.min(max, Math.max(min, num));
}}

function hsvToRgb(h, s, v) {{
    let c = v * s;
    let x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    let m = v - c;

    let r1 = 0, g1 = 0, b1 = 0;

    if (h >= 0 && h < 60) {{
        r1 = c; g1 = x; b1 = 0;
    }} else if (h < 120) {{
        r1 = x; g1 = c; b1 = 0;
    }} else if (h < 180) {{
        r1 = 0; g1 = c; b1 = x;
    }} else if (h < 240) {{
        r1 = 0; g1 = x; b1 = c;
    }} else if (h < 300) {{
        r1 = x; g1 = 0; b1 = c;
    }} else {{
        r1 = c; g1 = 0; b1 = x;
    }}

    return {{
        r: Math.round((r1 + m) * 255),
        g: Math.round((g1 + m) * 255),
        b: Math.round((b1 + m) * 255)
    }};
}}

function rgbToHex(r, g, b) {{
    const toHex = (n) => n.toString(16).padStart(2, "0");
    return "#" + toHex(r) + toHex(g) + toHex(b);
}}

function hexToRgb(hex) {{
    const v = normalizeHex(hex);
    if (!v) return null;
    return {{
        r: parseInt(v.slice(1, 3), 16),
        g: parseInt(v.slice(3, 5), 16),
        b: parseInt(v.slice(5, 7), 16),
    }};
}}

function rgbToHsv(r, g, b) {{
    r /= 255;
    g /= 255;
    b /= 255;

    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const d = max - min;

    let h = 0;
    let s = max === 0 ? 0 : d / max;
    let v = max;

    if (d !== 0) {{
        switch (max) {{
            case r:
                h = 60 * (((g - b) / d) % 6);
                break;
            case g:
                h = 60 * (((b - r) / d) + 2);
                break;
            case b:
                h = 60 * (((r - g) / d) + 4);
                break;
        }}
    }}

    if (h < 0) h += 360;

    return {{ h, s, v }};
}}

function drawHueSlider() {{
    if (!hueCanvas) return;
    const ctx = hueCanvas.getContext("2d");
    const w = hueCanvas.width;
    const h = hueCanvas.height;

    const gradient = ctx.createLinearGradient(0, 0, w, 0);
    gradient.addColorStop(0.00, "#ff0000");
    gradient.addColorStop(0.17, "#ffff00");
    gradient.addColorStop(0.33, "#00ff00");
    gradient.addColorStop(0.50, "#00ffff");
    gradient.addColorStop(0.67, "#0000ff");
    gradient.addColorStop(0.83, "#ff00ff");
    gradient.addColorStop(1.00, "#ff0000");

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, w, h);
}}

function drawSVBox() {{
    if (!svCanvas) return;
    const ctx = svCanvas.getContext("2d");
    const w = svCanvas.width;
    const h = svCanvas.height;

    const hueRgb = hsvToRgb(hue, 1, 1);

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = rgbToHex(hueRgb.r, hueRgb.g, hueRgb.b);
    ctx.fillRect(0, 0, w, h);

    const whiteGrad = ctx.createLinearGradient(0, 0, w, 0);
    whiteGrad.addColorStop(0, "rgba(255,255,255,1)");
    whiteGrad.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = whiteGrad;
    ctx.fillRect(0, 0, w, h);

    const blackGrad = ctx.createLinearGradient(0, 0, 0, h);
    blackGrad.addColorStop(0, "rgba(0,0,0,0)");
    blackGrad.addColorStop(1, "rgba(0,0,0,1)");
    ctx.fillStyle = blackGrad;
    ctx.fillRect(0, 0, w, h);
}}

function updateKnobs() {{
    if (svWrap && svKnob) {{
        const rect = svWrap.getBoundingClientRect();
        svKnob.style.left = (sat * rect.width) + "px";
        svKnob.style.top = ((1 - val) * rect.height) + "px";
    }}

    if (hueWrap && hueKnob) {{
        const rect = hueWrap.getBoundingClientRect();
        hueKnob.style.left = ((hue / 360) * rect.width) + "px";
    }}
}}

function updateVisualsFromHSV() {{
    const rgb = hsvToRgb(hue, sat, val);
    const hex = rgbToHex(rgb.r, rgb.g, rgb.b);

    if (bgOverrideInput) bgOverrideInput.value = hex;
    if (currentBgLabel) currentBgLabel.textContent = hex;
    if (currentBgSwatch) currentBgSwatch.style.background = hex;
    if (rVal) rVal.value = rgb.r;
    if (gVal) gVal.value = rgb.g;
    if (bVal) bVal.value = rgb.b;

    drawSVBox();
    updateKnobs();
}}

function setFromHex(hex) {{
    const rgb = hexToRgb(hex);
    if (!rgb) return;
    const hsv = rgbToHsv(rgb.r, rgb.g, rgb.b);
    hue = hsv.h;
    sat = hsv.s;
    val = hsv.v;
    updateVisualsFromHSV();
}}

function handleSVPointer(clientX, clientY) {{
    const rect = svWrap.getBoundingClientRect();
    const x = clamp(clientX - rect.left, 0, rect.width);
    const y = clamp(clientY - rect.top, 0, rect.height);

    sat = x / rect.width;
    val = 1 - (y / rect.height);
    updateVisualsFromHSV();
}}

function handleHuePointer(clientX) {{
    const rect = hueWrap.getBoundingClientRect();
    const x = clamp(clientX - rect.left, 0, rect.width);
    hue = (x / rect.width) * 360;
    updateVisualsFromHSV();
}}

function wirePointerDrag(element, moveHandler) {{
    if (!element) return;

    let dragging = false;

    element.addEventListener("mousedown", (e) => {{
        dragging = true;
        moveHandler(e.clientX, e.clientY);
    }});

    window.addEventListener("mousemove", (e) => {{
        if (!dragging) return;
        moveHandler(e.clientX, e.clientY);
    }});

    window.addEventListener("mouseup", () => {{
        dragging = false;
    }});

    element.addEventListener("touchstart", (e) => {{
        dragging = true;
        const t = e.touches[0];
        moveHandler(t.clientX, t.clientY);
        e.preventDefault();
    }}, {{ passive: false }});

    window.addEventListener("touchmove", (e) => {{
        if (!dragging) return;
        const t = e.touches[0];
        moveHandler(t.clientX, t.clientY);
        e.preventDefault();
    }}, {{ passive: false }});

    window.addEventListener("touchend", () => {{
        dragging = false;
    }});
}}

if (toggleBgToolsBtn && bgToolsPanel) {{
    toggleBgToolsBtn.addEventListener("click", () => {{
        bgToolsPanel.style.display = "block";
    }});
}}

if (pickerCloseBtn && bgToolsPanel) {{
    pickerCloseBtn.addEventListener("click", () => {{
        bgToolsPanel.style.display = "none";
    }});
}}

if (bgOverrideInput) {{
    bgOverrideInput.addEventListener("input", () => {{
        const hex = normalizeHex(bgOverrideInput.value);
        if (hex) setFromHex(hex);
    }});
}}

[rVal, gVal, bVal].forEach((input) => {{
    if (!input) return;
    input.addEventListener("input", () => {{
        const r = clamp(parseInt(rVal.value || "0", 10) || 0, 0, 255);
        const g = clamp(parseInt(gVal.value || "0", 10) || 0, 0, 255);
        const b = clamp(parseInt(bVal.value || "0", 10) || 0, 0, 255);
        setFromHex(rgbToHex(r, g, b));
    }});
}});

docSwatches.forEach(btn => {{
    btn.addEventListener("click", () => {{
        const color = btn.getAttribute("data-color");
        setFromHex(color);
    }});
}});

if (eyedropperBtn) {{
    if (!("EyeDropper" in window)) {{
        eyedropperBtn.disabled = true;
        eyedropperBtn.title = "Pick From Image is not supported in this browser.";
    }} else {{
        eyedropperBtn.addEventListener("click", async () => {{
            try {{
                const eyeDropper = new EyeDropper();
                const result = await eyeDropper.open();
                if (result && result.sRGBHex) {{
                    setFromHex(result.sRGBHex.toLowerCase());
                }}
            }} catch (err) {{
                // user cancelled
            }}
        }});
    }}
}}

wirePointerDrag(svWrap, handleSVPointer);
wirePointerDrag(hueWrap, (x) => handleHuePointer(x));

drawHueSlider();

if (currentBgLabel) {{
    const currentHex = normalizeHex(currentBgLabel.textContent.trim());
    if (currentHex) {{
        setFromHex(currentHex);
    }} else {{
        setFromHex("#ff0058");
    }}
}}
</script>

</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def home():
    qr_b64 = None
    card_mockup_b64 = None
    dome_mockup_b64 = None
    data_value = ""
    art_data_b64 = ""
    bg_override_value = ""
    current_bg_hex = "#ffffff"
    qr_style = "artistic"

    if request.method == "POST":
        data_value = (request.form.get("data") or "").strip()
        bg_override_value = (request.form.get("bg_override") or "").strip()
        art_data_b64 = (request.form.get("art_data") or "").strip()
        qr_style = (request.form.get("qr_style") or "artistic").strip().lower()

        art_file = request.files.get("artfile")
        art = fetch_uploaded_image(art_file)

        if art is None and art_data_b64:
            art = fetch_image_from_hidden_b64(art_data_b64)

        if data_value:
            if qr_style == "simple":
                qr_img = generate_simple_qr(data_value, logo=art)
            else:
                art = normalize_artwork_to_square(art, tolerance=0.12, bg_override=bg_override_value)
                qr_img = generate_branded_qr(data_value, art, bg_override=bg_override_value)

            qr_b64 = image_to_base64(qr_img)

            card_mockup = create_card_mockup(qr_img)
            dome_mockup = create_dome_mockup(qr_img)

            card_mockup_b64 = image_to_base64(card_mockup)
            dome_mockup_b64 = image_to_base64(dome_mockup)

            current_bg_hex = rgb_to_hex(qr_img.convert("RGB").getpixel((5, 5)))

            if art is not None:
                art_data_b64 = image_to_base64(art)

    return render_page(
        qr_img_b64=qr_b64,
        card_mockup_b64=card_mockup_b64,
        dome_mockup_b64=dome_mockup_b64,
        data_value=data_value,
        art_data_b64=art_data_b64,
        bg_override_value=bg_override_value,
        current_bg_hex=current_bg_hex,
        qr_style=qr_style,
    )


# -----------------------------
# BUTTN PROFILE TEST SYSTEM
# -----------------------------
# Temporary in-memory storage for the first working editor test.
# This proves: edit -> save -> public profile.
BUTTN_STORE = {
    "test": {
        "name": "Gary Ajené",
        "title": "T-Shirt Help Desk",
        "phone": "",
        "email": "",
        "background": "soft-blue",
        "logo_color": "#111111",
        "links": [
            {"label": "Visit My Store", "url": "#"},
            {"label": "Watch My YouTube", "url": "#"},
            {"label": "Free T-Shirt Guide", "url": "#"},
        ],
    }
}

BACKGROUND_STYLES = {
    "soft-blue": "linear-gradient(135deg, #eaf4ff 0%, #ffffff 48%, #f2f7ff 100%)",
    "warm-light": "linear-gradient(135deg, #fff4e8 0%, #ffffff 50%, #fffaf2 100%)",
    "clean-gray": "linear-gradient(135deg, #f2f4f7 0%, #ffffff 50%, #eef1f5 100%)",
    "mint": "linear-gradient(135deg, #eafff7 0%, #ffffff 50%, #f1fff8 100%)",
    "lavender": "linear-gradient(135deg, #f3edff 0%, #ffffff 50%, #faf7ff 100%)",
}


def get_buttn_profile(username):
    return BUTTN_STORE.get(username) or BUTTN_STORE["test"]


def render_buttn_profile(username):
    profile = get_buttn_profile(username)
    safe_name = html.escape(profile.get("name", ""))
    safe_title = html.escape(profile.get("title", ""))
    safe_phone = html.escape(profile.get("phone", ""))
    safe_email = html.escape(profile.get("email", ""))
    safe_logo_color = html.escape(profile.get("logo_color", "#111111"))
    bg_key = profile.get("background", "soft-blue")
    bg_style = BACKGROUND_STYLES.get(bg_key, BACKGROUND_STYLES["soft-blue"])

    link_html = ""
    for link in profile.get("links", []):
        label = html.escape(link.get("label", ""))
        url = html.escape(link.get("url", "#"))
        if label:
            link_html += f'<a class="buttn-link" href="{url}">{label}</a>'

    phone_button = f'<a class="action-btn" href="tel:{safe_phone}">Call</a>' if safe_phone else '<span class="action-btn muted">Call</span>'
    email_button = f'<a class="action-btn" href="mailto:{safe_email}">Email</a>' if safe_email else '<span class="action-btn muted">Email</span>'

    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_name} | BUTTN</title>
<style>
body {{
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f4f5f7;
    color: #111;
}}
.phone-shell {{
    max-width: 430px;
    min-height: 100vh;
    margin: 0 auto;
    background: #ffffff;
    box-shadow: 0 0 25px rgba(0,0,0,0.08);
}}
.hero {{
    padding: 34px 22px 28px;
    text-align: center;
    background: {bg_style};
    border-bottom-left-radius: 28px;
    border-bottom-right-radius: 28px;
}}
.logo-circle {{
    width: 96px;
    height: 96px;
    border-radius: 50%;
    background: {safe_logo_color};
    color: white;
    margin: 0 auto 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 34px;
    font-weight: bold;
    box-shadow: 0 8px 20px rgba(0,0,0,0.14);
}}
.name {{
    font-size: 24px;
    font-weight: 800;
    margin-top: 6px;
}}
.title {{
    font-size: 15px;
    color: #555;
    margin-top: 6px;
}}
.actions {{
    display: flex;
    justify-content: center;
    gap: 10px;
    margin-top: 18px;
    flex-wrap: wrap;
}}
.action-btn {{
    display: inline-block;
    text-decoration: none;
    color: #111;
    background: white;
    border: 1px solid #ddd;
    border-radius: 999px;
    padding: 10px 16px;
    font-size: 14px;
    font-weight: 700;
}}
.action-btn.muted {{
    color: #999;
}}
.content {{
    padding: 22px;
}}
.section-title {{
    font-weight: 800;
    font-size: 14px;
    color: #555;
    margin-bottom: 12px;
    text-transform: uppercase;
    letter-spacing: .06em;
}}
.buttn-link {{
    display: block;
    background: #f2f4f7;
    color: #111;
    text-decoration: none;
    padding: 15px 16px;
    border-radius: 16px;
    margin-bottom: 12px;
    font-weight: 700;
    border: 1px solid #e5e7eb;
}}
.footer {{
    text-align: center;
    color: #888;
    font-size: 12px;
    padding: 20px;
}}
.edit-link {{
    display: block;
    text-align: center;
    margin-top: 18px;
    color: #555;
    font-size: 13px;
}}
</style>
</head>
<body>
<div class="phone-shell">
    <div class="hero">
        <div class="logo-circle">{safe_name[:1].upper() if safe_name else 'B'}</div>
        <div class="name">{safe_name}</div>
        <div class="title">{safe_title}</div>
        <div class="actions">
            {phone_button}
            {email_button}
            <span class="action-btn">Save Contact</span>
        </div>
    </div>
    <div class="content">
        <div class="section-title">Links</div>
        {link_html}
        <a class="edit-link" href="/buttn/edit/{html.escape(username)}">Edit test profile</a>
    </div>
    <div class="footer">Powered by BUTTN</div>
</div>
</body>
</html>
"""


@app.route("/buttn/test")
def buttn_test():
    return render_buttn_profile("test")


@app.route("/buttn/<username>")
def buttn_public_profile(username):
    return render_buttn_profile(username)


@app.route("/buttn/edit/test", methods=["GET", "POST"])
def buttn_edit_test():
    username = "test"

    if request.method == "POST":
        links = []
        for i in range(1, 6):
            label = (request.form.get(f"link{i}_label") or "").strip()
            url = (request.form.get(f"link{i}_url") or "").strip()
            if label:
                if url and not (url.startswith("http://") or url.startswith("https://") or url.startswith("mailto:") or url.startswith("tel:")):
                    url = "https://" + url
                links.append({"label": label, "url": url or "#"})

        BUTTN_STORE[username] = {
            "name": (request.form.get("name") or "").strip() or "BUTTN User",
            "title": (request.form.get("title") or "").strip(),
            "phone": (request.form.get("phone") or "").strip(),
            "email": (request.form.get("email") or "").strip(),
            "background": (request.form.get("background") or "soft-blue").strip(),
            "logo_color": (request.form.get("logo_color") or "#111111").strip(),
            "links": links,
        }
        return redirect("/buttn/test")

    profile = get_buttn_profile(username)
    safe_name = html.escape(profile.get("name", ""))
    safe_title = html.escape(profile.get("title", ""))
    safe_phone = html.escape(profile.get("phone", ""))
    safe_email = html.escape(profile.get("email", ""))
    safe_logo_color = html.escape(profile.get("logo_color", "#111111"))
    current_bg = profile.get("background", "soft-blue")
    links = profile.get("links", [])

    def link_value(index, key):
        if index < len(links):
            return html.escape(links[index].get(key, ""))
        return ""

    bg_options = ""
    labels = {
        "soft-blue": "Soft Blue",
        "warm-light": "Warm Light",
        "clean-gray": "Clean Gray",
        "mint": "Mint",
        "lavender": "Lavender",
    }
    for key, label in labels.items():
        selected = "selected" if key == current_bg else ""
        bg_options += f'<option value="{key}" {selected}>{label}</option>'

    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Edit BUTTN Profile</title>
<style>
body {{
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f4f5f7;
    color: #111;
}}
.editor-wrap {{
    max-width: 760px;
    margin: 0 auto;
    padding: 28px;
}}
.card {{
    background: white;
    border-radius: 22px;
    padding: 24px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.08);
}}
h1 {{
    margin-top: 0;
}}
label {{
    display: block;
    font-weight: 800;
    margin-top: 16px;
    margin-bottom: 6px;
}}
input, select {{
    width: 100%;
    box-sizing: border-box;
    padding: 12px;
    border-radius: 12px;
    border: 1px solid #d8dbe0;
    font-size: 16px;
}}
.link-row {{
    display: grid;
    grid-template-columns: 1fr 1.4fr;
    gap: 10px;
    margin-bottom: 10px;
}}
.save-btn {{
    margin-top: 22px;
    width: 100%;
    padding: 14px;
    border: none;
    border-radius: 14px;
    background: #111;
    color: #fff;
    font-size: 17px;
    font-weight: 800;
    cursor: pointer;
}}
.preview-link {{
    display: inline-block;
    margin-top: 18px;
    color: #111;
    font-weight: 700;
}}
.note {{
    color: #666;
    line-height: 1.4;
}}
@media (max-width: 640px) {{
    .editor-wrap {{ padding: 16px; }}
    .link-row {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="editor-wrap">
    <div class="card">
        <h1>Edit BUTTN Profile</h1>
        <p class="note">This is the first test editor. Save your info, then it updates the public BUTTN page.</p>

        <form method="post">
            <label>Name</label>
            <input name="name" value="{safe_name}" placeholder="Your name or brand name">

            <label>Title / Company</label>
            <input name="title" value="{safe_title}" placeholder="Company, title, or short tagline">

            <label>Phone</label>
            <input name="phone" value="{safe_phone}" placeholder="Phone number">

            <label>Email</label>
            <input name="email" value="{safe_email}" placeholder="Email address">

            <label>Logo Color (temporary logo placeholder)</label>
            <input name="logo_color" value="{safe_logo_color}" placeholder="#111111">

            <label>Background Style</label>
            <select name="background">{bg_options}</select>

            <label>Links</label>
            <div class="link-row">
                <input name="link1_label" value="{link_value(0, 'label')}" placeholder="Link label">
                <input name="link1_url" value="{link_value(0, 'url')}" placeholder="https://example.com">
            </div>
            <div class="link-row">
                <input name="link2_label" value="{link_value(1, 'label')}" placeholder="Link label">
                <input name="link2_url" value="{link_value(1, 'url')}" placeholder="https://example.com">
            </div>
            <div class="link-row">
                <input name="link3_label" value="{link_value(2, 'label')}" placeholder="Link label">
                <input name="link3_url" value="{link_value(2, 'url')}" placeholder="https://example.com">
            </div>
            <div class="link-row">
                <input name="link4_label" value="{link_value(3, 'label')}" placeholder="Link label">
                <input name="link4_url" value="{link_value(3, 'url')}" placeholder="https://example.com">
            </div>
            <div class="link-row">
                <input name="link5_label" value="{link_value(4, 'label')}" placeholder="Link label">
                <input name="link5_url" value="{link_value(4, 'url')}" placeholder="https://example.com">
            </div>

            <button class="save-btn" type="submit">Save BUTTN Profile</button>
        </form>
        <a class="preview-link" href="/buttn/test">View public profile</a>
    </div>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
