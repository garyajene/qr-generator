from flask import Flask, request, redirect
from io import BytesIO
import base64
import random
from collections import Counter
from PIL import Image, ImageDraw, ImageStat
import segno
import html
import json

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

    radius = int(qr_target_w * 0.08)
    rounded_mask = Image.new("L", (qr_target_w, qr_target_h), 0)
    mask_draw = ImageDraw.Draw(rounded_mask)
    mask_draw.rounded_rectangle(
        (0, 0, qr_target_w, qr_target_h),
        radius=radius,
        fill=255
    )

    rounded_qr = Image.new("RGBA", (qr_target_w, qr_target_h), (0, 0, 0, 0))
    rounded_qr.paste(qr_small, (0, 0))
    rounded_qr.putalpha(rounded_mask)

    margin_x = int(card_w * 0.05)
    margin_y = int(card_h * 0.07)

    qr_x = card_w - qr_target_w - margin_x
    qr_y = card_h - qr_target_h - margin_y

    card.paste(rounded_qr, (qr_x, qr_y), rounded_qr)
    return card



def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_mask.png").convert("RGBA")
    dome_w, dome_h = dome.size

    dome_qr = create_dome_only_qr(qr_img, output_size=900)

    inset = int(dome_w * 0.10)

    # Fill the entire dome base with the selected QR background color first.
    # This gives the dome mockup extra color bleed around the smaller QR artwork
    # so gray/transparent gaps do not show near the circular edge.
    bg_color = qr_img.convert("RGB").getpixel((5, 5))
    dome_base = Image.new("RGBA", (dome_w, dome_h), (*bg_color, 255))

    resized = dome_qr.resize(
        (
            dome_w - (inset * 2),
            dome_h - (inset * 2)
        ),
        Image.LANCZOS
    )
    dome_base.paste(resized, (inset, inset), resized)

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

.preview-and-mockups {{
    display: flex;
    gap: 42px;
    align-items: flex-start;
    flex-wrap: wrap;
}}

.preview-column {{
    flex: 0 0 auto;
}}

.mockups-result {{
    margin-top: 0;
}}

.generated-qr {{
    max-width: 360px;
    height: auto;
    display: block;
    margin-top: 12px;
    background: #fff;
}}

.continue-buttn-setup {{
    display: inline-block;
    margin-top: 16px;
    padding: 12px 18px;
    background: #000;
    color: #fff;
    text-decoration: none;
    border-radius: 10px;
    font-size: 16px;
    font-weight: bold;
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

.dome-mockup-wrap .subhead {{
    font-size: 24px;
    line-height: 1.15;
    margin-bottom: 14px;
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


.hex-preview-row {{
    display: flex;
    align-items: flex-start;
    gap: 12px;
}}

.hex-preview-row .value-box {{
    min-width: 180px;
}}

.large-picked-color {{
    width: 170px;
    height: 42px;
    border: 1px solid #111;
    border-radius: 4px;
    background: {safe_current_bg_hex};
    margin-top: 0;
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

<form action="/generate" method="post" enctype="multipart/form-data">
    <div class="label">QR Data</div>
    <input type="text" name="data" required placeholder="Enter QR Data" value="{safe_data_value}"><br><br>

    <div class="label">QR Type</div>
    <input type="hidden" name="qr_style" id="qr_style" value="{safe_qr_style}">
    <input type="hidden" name="last_rendered_qr_style" id="last_rendered_qr_style" value="{safe_qr_style}">
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
        <div class="preview-and-mockups">
            <div class="preview-column">
                <div class="result-block">
                    <h2>Generated QR</h2>
                    <img class="generated-qr" src="data:image/png;base64,{qr_img_b64}" alt="Click the QR image to sample a color.">
                    <button type="submit" formaction="/buttn/start/test" formmethod="post" formenctype="multipart/form-data" class="continue-buttn-setup">Continue to BUTTN Setup</button>
                </div>
            </div>
        ''' if qr_img_b64 else ''}

        {f'''
            <div class="result-block mockups-result">
                <h2>Mockups</h2>
                <div class="mockups">
                    <div>
                        <div class="subhead">Business Card</div>
                        <img class="mockup-card" src="data:image/png;base64,{card_mockup_b64}">
                    </div>
                    <div class="dome-mockup-wrap">
                        <div class="subhead">Dome Sticker</div>
                        <img class="mockup-dome" src="data:image/png;base64,{dome_mockup_b64}">
                    </div>
                </div>
            </div>
        ''' if card_mockup_b64 and dome_mockup_b64 else ''}

        {'''
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

                        <div class="small-note">Tip: click directly on the generated QR image to sample a color.</div>

                        <div class="values-row">
                            <div class="hex-preview-row">
                                <div class="value-box">
                                    <input type="text" id="bg_override" name="bg_override" value="{safe_bg_override_value or safe_current_bg_hex}">
                                    <div class="lab">HEX</div>
                                </div>
                                <div id="large_picked_color" class="large-picked-color" title="Selected color preview"></div>
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
    </div>
</form>

<script>
function selectQRStyle(style) {{
    const qrStyleInput = document.getElementById("qr_style");
    const lastRenderedStyleInput = document.getElementById("last_rendered_qr_style");
    const simpleCard = document.getElementById("simple_qr_card");
    const brandedCard = document.getElementById("branded_qr_card");
    const styleChanged = qrStyleInput && qrStyleInput.value !== style;

    if (qrStyleInput) {{
        qrStyleInput.value = style;
    }}

    if (styleChanged) {{
        const bgInput = document.getElementById("bg_override");
        if (bgInput) {{
            bgInput.value = "";
        }}
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
const largePickedColor = document.getElementById("large_picked_color");
const bgOverrideInput = document.getElementById("bg_override");
const rVal = document.getElementById("r_val");
const gVal = document.getElementById("g_val");
const bVal = document.getElementById("b_val");
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
    if (largePickedColor) largePickedColor.style.background = hex;
    if (rVal) rVal.value = rgb.r;
    if (gVal) gVal.value = rgb.g;
    if (bVal) bVal.value = rgb.b;

    scheduleGeneratedQrLivePreview(hex);

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

const generatedQrImage = document.querySelector(".generated-qr");
const mockupCardImage = document.querySelector(".mockup-card");
const mockupDomeImage = document.querySelector(".mockup-dome");
let originalGeneratedQrSrc = generatedQrImage ? generatedQrImage.src : "";
let originalMockupCardSrc = mockupCardImage ? mockupCardImage.src : "";
let originalMockupDomeSrc = mockupDomeImage ? mockupDomeImage.src : "";
let originalPreviewBaseHex = currentBgLabel ? normalizeHex(currentBgLabel.textContent.trim()) : "";
let pendingPreviewHex = null;
let previewFrameRequested = false;

function colorDistanceRgb(r1, g1, b1, r2, g2, b2) {{
    return Math.sqrt(
        Math.pow(r1 - r2, 2) +
        Math.pow(g1 - g2, 2) +
        Math.pow(b1 - b2, 2)
    );
}}

function scheduleGeneratedQrLivePreview(hex) {{
    if (!generatedQrImage || !originalGeneratedQrSrc) return;

    const cleanHex = normalizeHex(hex);
    if (!cleanHex) return;

    pendingPreviewHex = cleanHex;

    if (previewFrameRequested) return;

    previewFrameRequested = true;
    window.requestAnimationFrame(() => {{
        previewFrameRequested = false;
        updateGeneratedQrLivePreview(pendingPreviewHex);
    }});
}}

function updateGeneratedQrLivePreview(hex) {{
    updateGeneratedQrEdgePreview(hex);
    updateMockupColorPreview(mockupCardImage, originalMockupCardSrc, hex);
    updateMockupColorPreview(mockupDomeImage, originalMockupDomeSrc, hex);
}}

function updateGeneratedQrEdgePreview(hex) {{
    if (!generatedQrImage || !originalGeneratedQrSrc) return;

    const newRgb = hexToRgb(hex);
    if (!newRgb) return;

    const sourceImg = new Image();

    sourceImg.onload = () => {{
        const imgW = sourceImg.naturalWidth || sourceImg.width;
        const imgH = sourceImg.naturalHeight || sourceImg.height;

        if (!imgW || !imgH) return;

        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d", {{ willReadFrequently: true }});
        if (!ctx) return;

        canvas.width = imgW;
        canvas.height = imgH;
        ctx.drawImage(sourceImg, 0, 0, imgW, imgH);

        let imageData;
        try {{
            imageData = ctx.getImageData(0, 0, imgW, imgH);
        }} catch (err) {{
            return;
        }}

        const data = imageData.data;
        const targetIndex = 0;
        const targetR = data[targetIndex];
        const targetG = data[targetIndex + 1];
        const targetB = data[targetIndex + 2];
        const tolerance = 42;
        const visited = new Uint8Array(imgW * imgH);
        const stack = [];

        function maybePush(x, y) {{
            if (x < 0 || y < 0 || x >= imgW || y >= imgH) return;
            const pos = y * imgW + x;
            if (visited[pos]) return;

            const i = pos * 4;
            const a = data[i + 3];
            if (a === 0) return;

            if (colorDistanceRgb(data[i], data[i + 1], data[i + 2], targetR, targetG, targetB) <= tolerance) {{
                visited[pos] = 1;
                stack.push(pos);
            }}
        }}

        for (let x = 0; x < imgW; x++) {{
            maybePush(x, 0);
            maybePush(x, imgH - 1);
        }}

        for (let y = 0; y < imgH; y++) {{
            maybePush(0, y);
            maybePush(imgW - 1, y);
        }}

        while (stack.length) {{
            const pos = stack.pop();
            const i = pos * 4;

            data[i] = newRgb.r;
            data[i + 1] = newRgb.g;
            data[i + 2] = newRgb.b;
            data[i + 3] = 255;

            const x = pos % imgW;
            const y = Math.floor(pos / imgW);

            maybePush(x + 1, y);
            maybePush(x - 1, y);
            maybePush(x, y + 1);
            maybePush(x, y - 1);
        }}

        ctx.putImageData(imageData, 0, 0);
        generatedQrImage.src = canvas.toDataURL("image/png");
    }};

    sourceImg.src = originalGeneratedQrSrc;
}}

function updateMockupColorPreview(imageEl, originalSrc, hex) {{
    if (!imageEl || !originalSrc || !originalPreviewBaseHex) return;

    const newRgb = hexToRgb(hex);
    const baseRgb = hexToRgb(originalPreviewBaseHex);
    if (!newRgb || !baseRgb) return;

    const sourceImg = new Image();

    sourceImg.onload = () => {{
        const imgW = sourceImg.naturalWidth || sourceImg.width;
        const imgH = sourceImg.naturalHeight || sourceImg.height;

        if (!imgW || !imgH) return;

        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d", {{ willReadFrequently: true }});
        if (!ctx) return;

        canvas.width = imgW;
        canvas.height = imgH;
        ctx.drawImage(sourceImg, 0, 0, imgW, imgH);

        let imageData;
        try {{
            imageData = ctx.getImageData(0, 0, imgW, imgH);
        }} catch (err) {{
            return;
        }}

        const data = imageData.data;
        const tolerance = 58;

        for (let i = 0; i < data.length; i += 4) {{
            const a = data[i + 3];
            if (a === 0) continue;

            if (colorDistanceRgb(data[i], data[i + 1], data[i + 2], baseRgb.r, baseRgb.g, baseRgb.b) <= tolerance) {{
                data[i] = newRgb.r;
                data[i + 1] = newRgb.g;
                data[i + 2] = newRgb.b;
            }}
        }}

        ctx.putImageData(imageData, 0, 0);
        imageEl.src = canvas.toDataURL("image/png");
    }};

    sourceImg.src = originalSrc;
}}

function sampleColorFromGeneratedImage(e) {{
    if (!generatedQrImage) return;

    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", {{ willReadFrequently: true }});

    const imgW = generatedQrImage.naturalWidth || generatedQrImage.width;
    const imgH = generatedQrImage.naturalHeight || generatedQrImage.height;

    if (!imgW || !imgH || !ctx) return;

    canvas.width = imgW;
    canvas.height = imgH;
    ctx.drawImage(generatedQrImage, 0, 0, imgW, imgH);

    const rect = generatedQrImage.getBoundingClientRect();
    const x = Math.floor(clamp(e.clientX - rect.left, 0, rect.width - 1) * (imgW / rect.width));
    const y = Math.floor(clamp(e.clientY - rect.top, 0, rect.height - 1) * (imgH / rect.height));

    try {{
        const pixel = ctx.getImageData(x, y, 1, 1).data;
        const hex = rgbToHex(pixel[0], pixel[1], pixel[2]);
        setFromHex(hex);
    }} catch (err) {{
        // Safely ignore if the image cannot be sampled.
    }}
}}

if (generatedQrImage) {{
    generatedQrImage.style.cursor = "crosshair";
    generatedQrImage.title = "Click the QR image to sample a color.";
    generatedQrImage.addEventListener("click", sampleColorFromGeneratedImage);
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


@app.route("/generate", methods=["GET", "POST"])
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
        last_rendered_qr_style = (request.form.get("last_rendered_qr_style") or qr_style).strip().lower()

        # Issue #1 fix:
        # If the customer switches QR modes without refreshing the page, do not let
        # the previous mode's manual/current background state contaminate the new render.
        # This allows Simple -> Branded -> Simple -> Branded to regenerate cleanly each time.
        if last_rendered_qr_style != qr_style:
            bg_override_value = ""

        art_file = request.files.get("artfile")
        art = fetch_uploaded_image(art_file)

        if art is None and art_data_b64:
            art = fetch_image_from_hidden_b64(art_data_b64)

        if qr_style not in ("simple", "artistic"):
            qr_style = "artistic"

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
# BUTTN PROFILE SYSTEM - SAFE ADD-ON
# This section is intentionally separate from the QR generator above.
# -----------------------------

BUTTN_PROFILES = {
    "test": {
        "buttn_url": "test",
        "name": "Gary Ajené",
        "title": "T-Shirt Help Desk",
        "phone": "",
        "email": "",
        "logo_b64": "",
        "header_image_b64": "",
        "header_bg_color": "#9d5d4d",
        "header_image_opacity": "35",
        "page_bg_color": "#f5f5f5",
        "link_bg_color": "#e8e8ee",
        "link_text_color": "#111111",
        "link_border_color": "#d8dde6",
        "header_name_color": "#111111",
        "header_title_color": "#555555",
        "action_bg_color": "#ffffff",
        "action_text_color": "#111111",
        "action_border_color": "#d8dde6",
        "links": [
            {"icon": "store", "label": "Button Text", "url": ""},
            {"icon": "youtube", "label": "", "url": ""},
            {"icon": "instagram", "label": "", "url": ""},
            {"icon": "facebook", "label": "", "url": ""},
            {"icon": "pinterest", "label": "", "url": ""},
        ],
    }
}


def _clean_hex(value, fallback="#ffffff"):
    parsed = parse_hex_color(value)
    if parsed is None:
        return fallback
    return rgb_to_hex(parsed)


LINK_ICON_OPTIONS = [
    ("whatnot", "Whatnot"),
    ("substack", "Substack"),
    ("custom", "Custom"),
    ("store", "Store"),
    ("website", "Website"),
    ("instagram", "Instagram"),
    ("youtube", "YouTube"),
    ("tiktok", "TikTok"),
    ("facebook", "Facebook"),
    ("x", "X"),
    ("pinterest", "Pinterest"),
    ("threads", "Threads"),
    ("linkedin", "LinkedIn"),
    ("reddit", "Reddit"),
    ("discord", "Discord"),
    ("etsy", "Etsy"),
    ("amazon", "Amazon"),
    ("booking", "Booking"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("cashapp", "Cash App"),
    ("paypal", "PayPal"),
    ("venmo", "Venmo"),
    ("schedule", "Schedule"),
    ("twitch", "Twitch"),
    ("whatsapp", "WhatsApp"),
]

LINK_ICON_MAP = {key: {"label": label} for key, label in LINK_ICON_OPTIONS}

SVG_ICON_MAP = {

    "custom": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M32 4l7.9 17 18.1 2.2-13.4 12.5 3.5 18.1L32 44.7 15.9 53.8l3.5-18.1L6 23.2 24.1 21 32 4z"/></svg>""",
    "store": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M10 24l4-14h36l4 14v4c0 4.4-3.6 8-8 8-2.6 0-4.9-1.2-6.4-3.1C38.1 34.8 35.3 36 32 36s-6.1-1.2-7.6-3.1C22.9 34.8 20.6 36 18 36c-4.4 0-8-3.6-8-8v-4zm8 16c2.3 0 4.4-.7 6-2 2.2 1.3 4.9 2 8 2s5.8-.7 8-2c1.6 1.3 3.7 2 6 2 2.1 0 4.2-.6 6-1.8V58H12V38.2c1.8 1.2 3.9 1.8 6 1.8zm4 6v8h20v-8H22z"/></svg>""",
    "website": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M32 4a28 28 0 100 56 28 28 0 000-56zm18.9 18H42.2c-.7-4.1-2-7.6-3.8-10.2A22.2 22.2 0 0150.9 22zM32 10c2.1 3 3.7 7.1 4.4 12h-8.8c.7-4.9 2.3-9 4.4-12zM10 32c0-1.4.1-2.7.4-4h10.1a39.4 39.4 0 000 8H10.4c-.3-1.3-.4-2.6-.4-4zm3.1 10h8.7c.7 4.1 2 7.6 3.8 10.2A22.2 22.2 0 0113.1 42zm8.7-20h-8.7a22.2 22.2 0 0112.5-10.2A32 32 0 0021.8 22zM32 54c-2.1-3-3.7-7.1-4.4-12h8.8c-.7 4.9-2.3 9-4.4 12zm5.1-18H26.9a30.5 30.5 0 010-8h10.2a30.5 30.5 0 010 8zm1.3 16.2c1.8-2.6 3.1-6.1 3.8-10.2h8.7a22.2 22.2 0 01-12.5 10.2zM43.5 36a39.4 39.4 0 000-8h10.1c.3 1.3.4 2.6.4 4s-.1 2.7-.4 4H43.5z"/></svg>""",
    "instagram": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M20 6h24c7.7 0 14 6.3 14 14v24c0 7.7-6.3 14-14 14H20C12.3 58 6 51.7 6 44V20C6 12.3 12.3 6 20 6zm0 6c-4.4 0-8 3.6-8 8v24c0 4.4 3.6 8 8 8h24c4.4 0 8-3.6 8-8V20c0-4.4-3.6-8-8-8H20zm12 10a10 10 0 110 20 10 10 0 010-20zm0 6a4 4 0 100 8 4 4 0 000-8zm12.8-9.8a3 3 0 110 6 3 3 0 010-6z"/></svg>""",
    "reddit": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M54 29c-1.8 0-3.4.8-4.5 2.1-4-2.5-9.4-4.1-15.4-4.4l2.6-12 8.3 1.8a5 5 0 105.1-3.9c-1.4 0-2.7.6-3.6 1.5L34 11.4 30.7 26.8c-6 .3-11.3 1.9-15.2 4.4A6 6 0 104 36c0 2.2 1.2 4.1 3 5.2v.8c0 8.3 11.2 15 25 15s25-6.7 25-15v-.8a6 6 0 00-3-11.2zM22.5 39a4 4 0 110-8 4 4 0 010 8zm18.7 9.5c-2.4 2.4-5.7 3.5-9.2 3.5s-6.8-1.1-9.2-3.5a2 2 0 012.8-2.8c1.5 1.5 3.8 2.3 6.4 2.3s4.9-.8 6.4-2.3a2 2 0 012.8 2.8zM41.5 39a4 4 0 110-8 4 4 0 010 8z"/></svg>""",
    "etsy": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M16 8h34l2 14h-5c-1.2-5.5-4.2-8-9.2-8H28v15h7.4c3.7 0 5.7-1.7 6.3-5.4h4v17h-4c-.6-3.9-2.6-5.8-6.3-5.8H28V50h10.7c5.2 0 8.7-3.2 10.5-9.5h5L51.8 56H16v-5h5V13h-5V8z"/></svg>""",
    "amazon": r"""<svg class="buttn-svg-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" fill="currentColor"><title>amazon</title>
<path fill="currentColor" d="M24.779 23.456c0.043-0.076 0.094-0.141 0.154-0.198l0-0c0.351-0.247 0.758-0.447 1.196-0.576l0.029-0.007c0.553-0.147 1.197-0.247 1.859-0.279l0.022-0.001c0.038-0.003 0.082-0.005 0.127-0.005 0.125 0 0.246 0.015 0.362 0.042l-0.011-0.002c0.758 0.070 1.225 0.196 1.367 0.385 0.073 0.119 0.116 0.263 0.116 0.418 0 0.013-0 0.026-0.001 0.039l0-0.002v0.175c-0.037 0.767-0.215 1.484-0.51 2.137l0.015-0.037c-0.303 0.776-0.762 1.432-1.344 1.956l-0.005 0.004c-0.060 0.058-0.14 0.096-0.228 0.105l-0.002 0c-0.005 0-0.011 0.001-0.017 0.001-0.031 0-0.062-0.005-0.090-0.015l0.002 0.001c-0.105-0.051-0.125-0.14-0.075-0.28 0.459-0.894 0.79-1.933 0.935-3.031l0.005-0.049c0.001-0.013 0.002-0.029 0.002-0.045 0-0.132-0.038-0.256-0.105-0.359l0.002 0.003c-0.169-0.194-0.642-0.3-1.428-0.3-0.284 0-0.622 0.019-1.015 0.054-0.424 0.052-0.817 0.105-1.167 0.157-0.011 0.001-0.023 0.002-0.036 0.002-0.065 0-0.125-0.020-0.175-0.054l0.001 0.001c-0.035-0.035-0.042-0.055-0.023-0.090 0.003-0.027 0.011-0.052 0.024-0.074l-0 0.001v-0.070zM14.090 15.258c-0.002 0.037-0.003 0.079-0.003 0.122 0 0.555 0.188 1.067 0.503 1.474l-0.004-0.006c0.31 0.367 0.77 0.599 1.284 0.599 0.022 0 0.045-0 0.067-0.001l-0.003 0c0.083-0.003 0.161-0.011 0.238-0.025l-0.010 0.002c0.056-0.013 0.122-0.022 0.19-0.027l0.004-0c0.736-0.205 1.328-0.704 1.654-1.359l0.007-0.015c0.184-0.306 0.328-0.66 0.415-1.037l0.005-0.025c0.079-0.273 0.135-0.592 0.157-0.92l0.001-0.014c0.017-0.227 0.017-0.63 0.017-1.172v-0.63c-0.085-0.003-0.184-0.004-0.284-0.004-0.693 0-1.368 0.078-2.016 0.226l0.061-0.012c-1.285 0.24-2.244 1.353-2.244 2.689 0 0.051 0.001 0.102 0.004 0.153l-0-0.007-0.041-0.023zM9.712 15.769c-0.001-0.044-0.002-0.096-0.002-0.148 0-1.066 0.325-2.056 0.881-2.876l-0.012 0.018c0.593-0.836 1.401-1.485 2.344-1.871l0.036-0.013c0.991-0.407 2.14-0.703 3.339-0.835l0.058-0.005c0.455-0.054 1.205-0.12 2.24-0.203v-0.432c0.010-0.113 0.015-0.245 0.015-0.378 0-0.653-0.134-1.276-0.377-1.84l0.012 0.030c-0.352-0.466-0.905-0.764-1.528-0.764-0.053 0-0.106 0.002-0.158 0.006l0.007-0h-0.212c-0.551 0.042-1.049 0.237-1.461 0.542l0.008-0.005c-0.401 0.31-0.685 0.755-0.785 1.265l-0.002 0.013c-0.020 0.292-0.228 0.53-0.503 0.594l-0.004 0.001-2.94-0.367c-0.289-0.070-0.434-0.21-0.434-0.455 0.001-0.063 0.010-0.123 0.027-0.18l-0.001 0.005c0.213-1.407 0.998-2.599 2.106-3.349l0.017-0.011c1.126-0.711 2.482-1.158 3.937-1.224l0.018-0.001h0.63c0.111-0.007 0.242-0.011 0.373-0.011 1.59 0 3.046 0.573 4.173 1.524l-0.010-0.008c0.157 0.175 0.315 0.35 0.473 0.56 0.121 0.153 0.23 0.325 0.322 0.508l0.008 0.017c0.104 0.192 0.183 0.415 0.226 0.651l0.002 0.014c0.070 0.296 0.122 0.49 0.157 0.595 0.053 0.214 0.085 0.461 0.089 0.715l0 0.003c0.012 0.365 0.023 0.575 0.023 0.645v6.16c-0 0.004-0 0.009-0 0.014 0 0.427 0.070 0.838 0.2 1.222l-0.008-0.027c0.088 0.299 0.213 0.561 0.374 0.796l-0.006-0.010 0.595 0.786c0.089 0.116 0.146 0.26 0.159 0.417l0 0.003c-0.005 0.154-0.087 0.288-0.208 0.365l-0.002 0.001c-1.4 1.225-2.17 1.89-2.29 1.995-0.112 0.085-0.254 0.136-0.408 0.136-0.12 0-0.232-0.031-0.33-0.085l0.003 0.002c-0.222-0.186-0.422-0.375-0.61-0.575l-0.003-0.004-0.362-0.405q-0.192-0.24-0.37-0.49l-0.35-0.508c-0.726 0.878-1.673 1.549-2.756 1.929l-0.044 0.013c-0.589 0.169-1.266 0.267-1.966 0.267-0.060 0-0.119-0.001-0.178-0.002l0.009 0c-0.048 0.002-0.104 0.003-0.16 0.003-1.185 0-2.262-0.46-3.063-1.211l0.002 0.002c-0.785-0.812-1.269-1.919-1.269-3.14 0-0.102 0.003-0.203 0.010-0.304l-0.001 0.014-0.058-0.089zM2.053 23.023c0.084-0.135 0.218-0.145 0.406-0.026 3.914 2.322 8.629 3.694 13.664 3.694 0.065 0 0.13-0 0.195-0.001l-0.010 0c3.548-0.010 6.934-0.692 10.040-1.926l-0.185 0.065 0.367-0.163c0.161-0.070 0.273-0.117 0.342-0.152 0.064-0.035 0.141-0.056 0.223-0.056 0.162 0 0.304 0.082 0.389 0.206l0.001 0.002c0.14 0.203 0.105 0.392-0.14 0.56-0.299 0.222-0.7 0.478-1.174 0.763-1.405 0.841-3.030 1.531-4.75 1.984l-0.132 0.030c-1.608 0.452-3.454 0.711-5.36 0.711-2.668 0-5.216-0.508-7.555-1.433l0.139 0.049c-2.443-0.971-4.542-2.292-6.354-3.925l0.019 0.017c-0.093-0.053-0.159-0.146-0.176-0.255l-0-0.002c0.003-0.058 0.025-0.111 0.060-0.152l-0 0z"></path></svg>""",
    "booking": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M17 8h4v6h22V8h4v6h5a6 6 0 016 6v30a6 6 0 01-6 6H12a6 6 0 01-6-6V20a6 6 0 016-6h5V8zm35 20H12v22h40V28zM18 34h8v8h-8v-8zm12 0h8v8h-8v-8z"/></svg>""",
    "phone": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M20.7 8.4l7 14c.6 1.2.3 2.7-.7 3.6l-4.2 3.8c3.4 6.6 7.8 11 14.4 14.4L41 40c1-.9 2.4-1.2 3.6-.7l14 7c1.4.7 2.1 2.3 1.7 3.8L58 58.5c-.4 1.5-1.8 2.5-3.4 2.5C26.1 61 3 37.9 3 9.4 3 7.8 4 6.4 5.5 6l8.4-2.3c1.5-.4 3.1.3 3.8 1.7z"/></svg>""",
    "cashapp": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M18 6h28c6.6 0 12 5.4 12 12v28c0 6.6-5.4 12-12 12H18C11.4 58 6 52.6 6 46V18C6 11.4 11.4 6 18 6zm16.5 9h-5v5.1c-5.6.8-9.5 4.3-9.5 9.2 0 5.6 4.8 7.7 10.2 9.1 4 1 5.8 1.8 5.8 3.8 0 2.1-2.1 3.3-5.1 3.3-3.2 0-6.5-1.2-9.3-3.3l-3.6 5.2c3 2.4 6.9 3.8 11.2 4.1V57h5v-5.5c6.1-.8 10.1-4.5 10.1-9.8 0-5.8-4.5-7.8-10.6-9.3-4.2-1-5.4-1.9-5.4-3.5 0-1.7 1.7-2.9 4.5-2.9 2.7 0 5.4.9 8.1 2.8l3.5-5.2c-2.8-2.1-6-3.2-9.9-3.5V15z"/></svg>""",
    "paypal": r"""<svg class="buttn-svg-icon" xmlns="http://www.w3.org/2000/svg" viewBox="-1.5 0 20 20" fill="currentColor"><title>paypal [#140]</title>
    <desc>Created with Sketch.</desc>
    
    <g id="Page-1" stroke="currentColor" stroke-width="1" fill="currentColor" fill-rule="evenodd">
        <g id="Dribbble-Light-Preview" transform="translate(-222.000000, -7559.000000)" fill="currentColor">
            <g id="icons" transform="translate(56.000000, 160.000000)">
                <path d="M182.475463,7404.9 C181.260804,7410.117 177.555645,7411 172.578656,7411 L171.078137,7419 L173.825411,7419 C174.325918,7419 174.53555,7418.659 174.627828,7418.179 C175.312891,7413.848 175.216601,7414.557 175.278788,7413.879 C175.337966,7413.501 175.664951,7413 176.049108,7413 C179.698098,7413 182.118387,7411.945 182.857614,7408.158 C183.120405,7406.811 183.034145,7405.772 182.475463,7404.9 M171.134306,7410.86 L170.011926,7417 L166.535456,7417 C166.206465,7417 165.954707,7416.598 166.006864,7416.274 L168.602682,7399.751 C168.670887,7399.319 169.045014,7399 169.484337,7399 L175.718111,7399 C179.409228,7399 181.894714,7400.401 181.319983,7404.054 C180.313953,7410.56 174.737157,7410 172.199514,7410 C171.760191,7410 171.203515,7410.428 171.134306,7410.86" id="paypal-[#140]">

</path>
            </g>
        </g>
    </g></svg>""",
    "venmo": r"""<svg class="buttn-svg-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path fill="none" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round" stroke="currentColor" d="M40.25,4.45a14.26,14.26,0,0,1,2.06,7.8c0,9.72-8.3,22.34-15,31.2H11.91L5.74,6.58,19.21,5.3l3.27,26.24c3.05-5,6.81-12.76,6.81-18.08A14.51,14.51,0,0,0,28,6.94Z"/></svg>""",
    "schedule": r"""<svg class="buttn-svg-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800" fill="currentColor"><path d="M593.3,733.3H206.7c-41.3,0-64,0-85.4-10.9-18.9-9.6-34-24.8-43.7-43.7-10.9-21.4-10.9-44.1-10.9-85.4v-320c0-41.3,0-64,10.9-85.4,9.7-18.9,24.8-34.1,43.7-43.7,20.2-10.3,41.6-10.9,78.7-10.9v-33.3c0-18.4,14.9-33.3,33.3-33.3s33.3,14.9,33.3,33.3v33.3h266.7v-33.3c0-18.4,14.9-33.3,33.3-33.3s33.3,14.9,33.3,33.3v33.3c37.1,0,58.5.6,78.7,10.9,18.9,9.7,34.1,24.8,43.7,43.7,10.9,21.4,10.9,44.1,10.9,85.4v320c0,41.3,0,64-10.9,85.4-9.6,18.9-24.8,34.1-43.7,43.7-21.4,10.9-44.1,10.9-85.4,10.9ZM133.3,333.3v260c0,28.9,0,48,3.6,55.1,3.2,6.3,8.3,11.4,14.6,14.6,7.1,3.6,26.2,3.6,55.1,3.6h386.7c28.9,0,48,0,55.1-3.6,6.3-3.2,11.4-8.3,14.6-14.6,3.6-7.1,3.6-26.2,3.6-55.1v-260H133.3ZM133.3,266.7h533.3c0-25.2-.3-41.9-3.6-48.5-3.2-6.3-8.3-11.4-14.6-14.6-7.1-3.6-26.2-3.6-55.1-3.6H206.7c-28.9,0-48,0-55.1,3.6-6.3,3.2-11.4,8.3-14.6,14.6-3.3,6.6-3.6,23.3-3.6,48.5ZM600,633.3h-66.7c-18.4,0-33.3-14.9-33.3-33.3s14.9-33.3,33.3-33.3h66.7c18.4,0,33.3,14.9,33.3,33.3s-14.9,33.3-33.3,33.3ZM433.3,633.3h-66.7c-18.4,0-33.3-14.9-33.3-33.3s14.9-33.3,33.3-33.3h66.7c18.4,0,33.3,14.9,33.3,33.3s-14.9,33.3-33.3,33.3ZM266.7,633.3h-66.7c-18.4,0-33.3-14.9-33.3-33.3s14.9-33.3,33.3-33.3h66.7c18.4,0,33.3,14.9,33.3,33.3s-14.9,33.3-33.3,33.3ZM600,533.3h-66.7c-18.4,0-33.3-14.9-33.3-33.3s14.9-33.3,33.3-33.3h66.7c18.4,0,33.3,14.9,33.3,33.3s-14.9,33.3-33.3,33.3ZM433.3,533.3h-66.7c-18.4,0-33.3-14.9-33.3-33.3s14.9-33.3,33.3-33.3h66.7c18.4,0,33.3,14.9,33.3,33.3s-14.9,33.3-33.3,33.3ZM266.7,533.3h-66.7c-18.4,0-33.3-14.9-33.3-33.3s14.9-33.3,33.3-33.3h66.7c18.4,0,33.3,14.9,33.3,33.3s-14.9,33.3-33.3,33.3ZM600,433.3h-66.7c-18.4,0-33.3-14.9-33.3-33.3s14.9-33.3,33.3-33.3h66.7c18.4,0,33.3,14.9,33.3,33.3s-14.9,33.3-33.3,33.3ZM433.3,433.3h-66.7c-18.4,0-33.3-14.9-33.3-33.3s14.9-33.3,33.3-33.3h66.7c18.4,0,33.3,14.9,33.3,33.3s-14.9,33.3-33.3,33.3ZM266.7,433.3h-66.7c-18.4,0-33.3-14.9-33.3-33.3s14.9-33.3,33.3-33.3h66.7c18.4,0,33.3,14.9,33.3,33.3s-14.9,33.3-33.3,33.3Z"/>
</svg>""",
    "twitch": r"""<?xml version="1.0" encoding="utf-8"?>
<!-- Uploaded to: SVG Repo, www.svgrepo.com, Generator: SVG Repo Mixer Tools -->
<svg fill="#000000" width="800px" height="800px" viewBox="0 0 32 32" version="1.1" xmlns="http://www.w3.org/2000/svg">
<title>twitch</title>
<path d="M26.711 14.929l-4.284 4.284h-4.285l-3.749 3.749v-3.749h-4.82v-16.067h17.138zM8.502 1.004l-5.356 5.356v19.279h6.427v5.356l5.356-5.356h4.284l9.641-9.64v-14.996zM21.356 6.895h2.142v6.427h-2.142zM15.464 6.895h2.143v6.427h-2.144z"></path>
</svg>""",
    "discord": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 71.45 54.44"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M60.52,4.55A58.57,58.57,0,0,0,45.78,0a36.63,36.63,0,0,0-1.89,3.88,55.17,55.17,0,0,0-16.33,0A42.53,42.53,0,0,0,25.62,0,58.16,58.16,0,0,0,10.87,4.57C1.57,18.44-1,32,.3,45.31a59.4,59.4,0,0,0,18.09,9.13,44.62,44.62,0,0,0,3.87-6.29,38.74,38.74,0,0,1-6.1-2.91c.51-.39,1-.77,1.48-1.16a42.26,42.26,0,0,0,36.17,0c.48.4,1,.79,1.49,1.16a37.67,37.67,0,0,1-6.11,2.93,43.85,43.85,0,0,0,3.87,6.27,58.93,58.93,0,0,0,18.09-9.12A60.23,60.23,0,0,0,60.52,4.55ZM23.85,37.12c-3.53,0-6.42-3.23-6.42-7.2s2.83-7.22,6.42-7.22,6.46,3.23,6.43,7.2S27.44,37.12,23.85,37.12Zm23.74,0c-3.53,0-6.42-3.23-6.42-7.2S44,22.7,47.59,22.7s6.46,3.23,6.42,7.2S51.18,37.12,47.59,37.12Z"/></g></g></svg>""",
    "facebook": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 35.05 65.45"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M23.21,65.45V36.82h9.55L34.57,25H23.21V17.28c0-3.24,1.59-6.4,6.68-6.4h5.16V.8A63,63,0,0,0,25.88,0C16.52,0,10.4,5.67,10.4,15.94v9H0V36.82H10.4V65.45Z"/></g></g></svg>""",
    "linkedin": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60.63 60.49"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M1,20.08H13.58V60.49H1ZM7.29,0A7.28,7.28,0,1,1,0,7.28,7.28,7.28,0,0,1,7.29,0"/><path class="cls-1" d="M21.46,20.08h12v5.53h.16c1.68-3.18,5.78-6.53,11.9-6.53,12.71,0,15.06,8.36,15.06,19.24V60.49H48.08V40.84c0-4.69-.1-10.71-6.53-10.71S34,35.23,34,40.5v20H21.46Z"/></g></g></svg>""",
    "messenger": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 56.45 56.46"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M28.23,0C12.33,0,0,11.65,0,27.38A26.78,26.78,0,0,0,8.86,47.63a2.26,2.26,0,0,1,.76,1.61l.15,5a2.26,2.26,0,0,0,2.33,2.18,2,2,0,0,0,.84-.19l5.61-2.47a2.3,2.3,0,0,1,1.51-.11,30.64,30.64,0,0,0,8.17,1.09c15.9,0,28.22-11.65,28.22-27.38S44.13,0,28.23,0Zm17,21.07L36.89,34.22a4.23,4.23,0,0,1-5.84,1.32,2.9,2.9,0,0,1-.29-.19l-6.6-5a1.7,1.7,0,0,0-2,0l-8.9,6.76a1.34,1.34,0,0,1-1.94-1.78l8.29-13.15a4.23,4.23,0,0,1,5.84-1.33,2.83,2.83,0,0,1,.28.2l6.6,4.94a1.7,1.7,0,0,0,2,0l8.91-6.76A1.34,1.34,0,0,1,45.18,21.07Z"/></g></g></svg>""",
    "pinterest": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52.41 67.68"><defs><style>.cls-1{fill:#010101;fill-rule:evenodd;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M21.92,44.15l-.16.52C19.28,54.41,19,56.58,16.45,61.1a51.73,51.73,0,0,1-4.11,6.14c-.17.22-.33.5-.68.43s-.4-.42-.44-.72A52,52,0,0,1,10.68,58c.13-3.89.61-5.23,5.63-26.33a1.48,1.48,0,0,0-.12-.88A14.9,14.9,0,0,1,15.8,21c2.28-7.2,10.43-7.75,11.86-1.81.88,3.67-1.44,8.48-3.23,15.58-1.48,5.86,5.42,10,11.32,5.75,5.44-3.94,7.55-13.4,7.15-20.1C42.11,7,27.45,4.13,18.16,8.44,7.5,13.37,5.07,26.58,9.89,32.62c.61.76,1.08,1.23.88,2-.31,1.21-.59,2.43-.92,3.63a1.28,1.28,0,0,1-1.9.85A10.71,10.71,0,0,1,3.5,35.78c-4.09-5.06-5.26-15.07.14-23.55C9.63,2.85,20.77-.95,30.93.2,43.07,1.58,50.75,9.88,52.19,19.29c.65,4.29.18,14.86-5.84,22.33C39.43,50.21,28.2,50.78,23,45.51,22.63,45.11,22.31,44.63,21.92,44.15Z"/></g></g></svg>""",
    "snapchat": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 68.75 64.96"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M34.38,65a15.82,15.82,0,0,1-8-2.31c-1-.58-2-1.23-2.93-1.87L22.24,60a6.68,6.68,0,0,0-2.72-1,9,9,0,0,0-1.15-.08,21.37,21.37,0,0,0-2.88.28c-.5.07-1,.14-1.51.19a4.82,4.82,0,0,1-.65,0,3.6,3.6,0,0,1-3.92-3.38c-.05-.89-.06-1.19-1.95-1.4a13,13,0,0,1-3.6-1.08A6.53,6.53,0,0,1,.2,50.28L0,49.84v-2.3l.54-.63A4.87,4.87,0,0,1,3.3,45.17c4.11-.81,7.28-3.29,10-7.79a11.06,11.06,0,0,0,1-1.91,12.62,12.62,0,0,0-2.08-.88,8,8,0,0,1-.81-.29,10.47,10.47,0,0,1-3.12-1.77A4.58,4.58,0,0,1,6.7,28a5.19,5.19,0,0,1,3.94-3.7,5.13,5.13,0,0,1,1.24-.15,8.39,8.39,0,0,1,2.77.57,59.57,59.57,0,0,1-.05-7.38,16.59,16.59,0,0,1,8.52-14A21.93,21.93,0,0,1,34.37,0,21.91,21.91,0,0,1,45.51,3.2,16.6,16.6,0,0,1,54,15.91a48,48,0,0,1,.09,8.71,8.83,8.83,0,0,1,2.71-.51,5.88,5.88,0,0,1,2.36.5,4.88,4.88,0,0,1,3.08,4.44,4.81,4.81,0,0,1-1.73,3.46,11.29,11.29,0,0,1-3.28,1.81l-.24.09c-.32.14-.65.26-1,.38s-.84.3-1.25.49l-.27.14.06.14c2.57,5.42,6,8.47,10.73,9.58a5,5,0,0,1,3,1.89l.45.59v2.26l-.22.46a6.77,6.77,0,0,1-4,3.35l-.33.13a13,13,0,0,1-3.92,1,1.6,1.6,0,0,0-.67.14,1.46,1.46,0,0,0-.2.65,3.81,3.81,0,0,1-4,3.76c-.27,0-.56,0-.89,0a12.18,12.18,0,0,1-1.35-.19,14.67,14.67,0,0,0-2.54-.25,8,8,0,0,0-5.28,1.8,16.87,16.87,0,0,1-1.63,1.13l-.32.21A16.61,16.61,0,0,1,34.38,65Z"/></g></g></svg>""",
    "telegram": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 66.57 55.04"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M4.08,24S33.52,11.89,43.73,7.63C47.65,5.93,60.92.49,60.92.49s6.13-2.39,5.62,3.4c-.18,2.38-1.54,10.72-2.9,19.74-2,12.76-4.25,26.72-4.25,26.72s-.34,3.91-3.24,4.59-7.65-2.38-8.5-3.06C47,51.37,34.88,43.71,30.46,40c-1.19-1-2.55-3.07.17-5.45,6.12-5.61,13.44-12.59,17.87-17,2-2,4.08-6.8-4.43-1-12.08,8.34-24,16.17-24,16.17s-2.73,1.7-7.83.17S1.19,29.25,1.19,29.25-2.9,26.69,4.08,24Z"/></g></g></svg>""",
    "threads": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52.29 60.78"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M39.88,27.81l.79.36a15.51,15.51,0,0,1,7.83,7.77,16.43,16.43,0,0,1-3.84,18.13c-4.58,4.58-10.16,6.64-18,6.71h0c-8.89-.07-15.71-3.06-20.3-8.89C2.18,46.7.08,39.47,0,30.42v-.06c.06-9,2.16-16.28,6.25-21.47C10.85,3.05,17.68.06,26.57,0h0c8.9.06,15.81,3,20.54,8.85a27.6,27.6,0,0,1,5.14,10.34l-5.11,1.37a22.15,22.15,0,0,0-4.08-8.28c-3.69-4.53-9.24-6.86-16.52-6.91S13.91,7.75,10.36,12.25C7,16.47,5.32,22.57,5.25,30.38S7,44.29,10.36,48.52c3.54,4.51,9,6.82,16.22,6.89,6.51,0,10.81-1.6,14.4-5.18a11,11,0,0,0,2.71-12.14,9.74,9.74,0,0,0-4-4.42,16.77,16.77,0,0,1-3.13,8.21,11.88,11.88,0,0,1-9.2,4.46,13.38,13.38,0,0,1-8.09-2,9.44,9.44,0,0,1-4.34-7.51c-.32-6.11,4.52-10.5,12.05-10.94a33.74,33.74,0,0,1,7.49.36,8.94,8.94,0,0,0-1.85-4.46c-1.26-1.48-3.24-2.24-5.84-2.25h-.09a7.71,7.71,0,0,0-6.75,3.33l-4.35-3a12.81,12.81,0,0,1,11.11-5.71h.1c7.93,0,12.65,5,13.13,13.64l0,0ZM20.13,36.52C20.29,39.7,23.72,41.18,27,41s6.91-1.44,7.53-9.27a26.19,26.19,0,0,0-5.49-.55c-.61,0-1.22,0-1.83,0-5.43.3-7.24,2.93-7.11,5.29Z"/></g></g></svg>""",
    "tiktok": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 59.33 69.34"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M59.32,28.05a16,16,0,0,1-1.7.09,18.52,18.52,0,0,1-15.49-8.37v28.5A21.07,21.07,0,1,1,21.07,27.2h0c.44,0,.87,0,1.3.07V37.65a11.22,11.22,0,0,0-1.3-.13,10.75,10.75,0,1,0,0,21.5C27,59,32.25,54.34,32.25,48.41L32.35,0h9.93a18.49,18.49,0,0,0,17,16.51V28.05"/></g></g></svg>""",
    "twitch": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52.39 61.12"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><g id="Layer_1-2-2" data-name="Layer 1-2"><path class="cls-1" d="M10.92,0,0,10.92V50.21H13.1V61.12L24,50.21h8.73L52.39,30.56V0ZM48,28.38l-8.74,8.73H30.56l-7.64,7.64V37.11H13.1V4.37H48Z"/><rect class="cls-1" x="37.11" y="12.01"/><rect class="cls-1" x="25.1" y="12.01"/></g></g></g></svg>""",
    "whatsapp": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60.63 60.78"><defs><style>.cls-1,.cls-2{fill:#010101;}.cls-2{fill-rule:evenodd;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M0,60.78,4.36,44.52a30,30,0,1,1,12,11.88ZM17.15,50.31l1,.61a24.43,24.43,0,1,0-8.35-8.28l.63,1L8,52.75Z"/><path class="cls-2" d="M41.78,34.19c-1.24-.74-2.85-1.56-4.3-1-1.12.46-1.83,2.21-2.56,3.1a1.06,1.06,0,0,1-1.38.3,19.54,19.54,0,0,1-9.72-8.32A1.2,1.2,0,0,1,24,26.67a6.57,6.57,0,0,0,1.77-2.87A6.26,6.26,0,0,0,25,20.41c-.59-1.26-1.24-3.06-2.51-3.77a3.42,3.42,0,0,0-3.72.55,7.56,7.56,0,0,0-2.62,6,8.1,8.1,0,0,0,.23,1.9,15.53,15.53,0,0,0,1.83,4.2,32.86,32.86,0,0,0,1.9,2.9,29.47,29.47,0,0,0,8.26,7.67,25.32,25.32,0,0,0,5.15,2.45c2,.67,3.82,1.36,6,.95a7.29,7.29,0,0,0,5.44-4,3.49,3.49,0,0,0,.25-2.06C44.85,35.72,42.93,34.88,41.78,34.19Z"/></g></g></svg>""",
    "x": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 51.21 52.33"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M30.47,22.16,49.54,0H45L28.47,19.24,15.25,0H0L20,29.09,0,52.33H4.52L22,32,36,52.33H51.21L30.47,22.16Zm-6.18,7.19-2-2.9L6.15,3.4h6.93L26.09,22l2,2.89L45,49.09H38.08L24.29,29.35Z"/></g></g></svg>""",
    "youtube": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 67.41 47.19"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M67.41,14.79A14.79,14.79,0,0,0,52.62,0H14.79A14.79,14.79,0,0,0,0,14.79V32.4A14.79,14.79,0,0,0,14.79,47.19H52.62A14.79,14.79,0,0,0,67.41,32.4ZM45.17,24.91l-17,8.4c-.66.36-2.92-.13-2.92-.88V15.2c0-.76,2.28-1.25,2.94-.87l16.24,8.84C45.14,23.56,45.86,24.54,45.17,24.91Z"/></g></g></svg>""",
    "email": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48.07 38.18"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M40.37,0H7.7A7.72,7.72,0,0,0,0,7.7V26.79A11.4,11.4,0,0,0,11.38,38.18H36.69A11.39,11.39,0,0,0,48.07,26.79V7.7A7.71,7.71,0,0,0,40.37,0ZM39.09,5.1,26.54,19.48a3.32,3.32,0,0,1-5,0L9,5.1Zm-2.4,28H11.38A6.29,6.29,0,0,1,5.1,26.79V8.4L17.69,22.83a8.41,8.41,0,0,0,12.69,0L43,8.4V26.79A6.29,6.29,0,0,1,36.69,33.08Z"/></g></g></svg>""",
    "contact": r"""<svg class="buttn-svg-icon" fill="currentColor" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 73.48 73.48"><defs><style>.cls-1{fill:#010101;}</style></defs><g id="Layer_2" data-name="Layer 2"><g id="Layer_1-2" data-name="Layer 1"><path class="cls-1" d="M36.74,0A36.74,36.74,0,1,0,73.48,36.74,36.74,36.74,0,0,0,36.74,0ZM36.5,14.91a9.18,9.18,0,1,1-9.18,9.17A9.17,9.17,0,0,1,36.5,14.91ZM49.05,58.57H23.94a4.47,4.47,0,0,1-4.46-4.47,17,17,0,0,1,34,0A4.47,4.47,0,0,1,49.05,58.57Z"/></g></g></svg>""",
    "whatnot": r"""<?xml version="1.0" encoding="UTF-8"?>
<svg class="buttn-svg-icon" fill="currentColor" id="Layer_1" xmlns="http://www.w3.org/2000/svg" version="1.1" viewBox="0 0 21.1 14">
  <!-- Generator: Adobe Illustrator 30.4.0, SVG Export Plug-In . SVG Version: 2.1.4 Build 226)  -->
  <defs>
    <style>
      .st0 {
        fill: #2a2527;
      }
    </style>
  </defs>
  <path class="st0" d="M18.8,8.3l-.9,1.4c-1.8,2.8-2.8,3.6-4,3.6s-1.9-.6-3.6-3.3l-1-1.6c-.2-.3-.6-.2-.5.2l.2.7c.6,2.4-.3,4-1.8,4s-2.1-.6-4.1-3.7l-.8-1.3C.9,6.3.5,5.3.5,4S1.7.9,3.5.9s2.3.6,3.1,2.2l.2.4c.2.3.4.3.5,0v-.4c.7-1.5,1.6-2.2,3.1-2.2s2.4.7,3,2.2v.4c.3.3.5.3.7,0l.2-.4c.8-1.6,1.7-2.2,3.1-2.2s3.1,1.4,3.1,3-.5,2.3-1.8,4.3h0Z"/>
</svg>""",
    "substack": r"""<svg class="buttn-svg-icon" fill="currentColor" role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" id="Substack--Streamline-Simple-Icons">
  <desc>
    Substack Streamline Icon: https://streamlinehq.com
  </desc>
  <title>Substack</title>
  <path d="M22.539 8.242H1.46V5.406h21.08v2.836zM1.46 10.812V24L12 18.11 22.54 24V10.812H1.46zM22.54 0H1.46v2.836h21.08V0z" stroke-width="1"></path>
</svg>"""
}


# Normalize SVG icon keys so mismatched capitalization/spaces
# never break icon rendering.
SVG_ICON_MAP = {
    str(key).strip().lower(): value
    for key, value in SVG_ICON_MAP.items()
}


DEFAULT_LINK_ICON = "custom"
MAX_PROFILE_LINKS = 15


def _normalize_link_icon(value):
    value = (value or DEFAULT_LINK_ICON).strip().lower()
    return value if value in LINK_ICON_MAP else DEFAULT_LINK_ICON


def _guess_icon_from_label(label):
    text = (label or "").strip().lower()
    guesses = [
        ("instagram", ["instagram", "insta", "ig"]), ("youtube", ["youtube", "you tube", "yt"]),
        ("tiktok", ["tiktok", "tik tok"]), ("facebook", ["facebook", "fb"]),
        ("pinterest", ["pinterest"]), ("x", ["twitter", " x", "x "]), ("threads", ["threads"]),
        ("linkedin", ["linkedin"]), ("reddit", ["reddit"]), ("discord", ["discord"]),
        ("substack", ["substack", "newsletter"]), ("etsy", ["etsy"]), ("amazon", ["amazon"]),
        ("store", ["store", "shop", "merch"]), ("website", ["website", "site", "web"]),
        ("booking", ["booking", "book", "calendar", "appointment", "calendly"]),
        ("email", ["email"]), ("phone", ["phone", "call"]), ("cashapp", ["cash app", "cashapp"]),
        ("paypal", ["paypal"]), ("venmo", ["venmo"]), ("whatsapp", ["whatsapp", "what's app"]),
    ]
    padded = f" {text} "
    for icon, terms in guesses:
        if any(term in padded or term in text for term in terms):
            return icon
    return DEFAULT_LINK_ICON


def _link_icon_html(icon_key):
    icon_key = _normalize_link_icon(icon_key)

    data = LINK_ICON_MAP.get(
        icon_key,
        LINK_ICON_MAP.get(DEFAULT_LINK_ICON, {"label":"Custom"})
    )

    svg = SVG_ICON_MAP.get(icon_key)

    if svg:
        return f'<span class="buttn-link-icon buttn-icon-{html.escape(icon_key)}" aria-label="{html.escape(data.get("label",""))}">{svg}</span>'

    return '<span class="buttn-link-icon buttn-icon-custom">✦</span>'


def _icon_select_html(name, selected_icon):
    selected_icon = _normalize_link_icon(selected_icon)
    options = []
    for key, label in LINK_ICON_OPTIONS:
        selected = " selected" if key == selected_icon else ""
        options.append(f'<option value="{html.escape(key)}"{selected}>{html.escape(label)}</option>')
    return f'<select class="live-link-icon" name="{html.escape(name)}">' + "".join(options) + '</select>'


RESERVED_BUTTN_URLS = {
    "admin", "login", "signup", "support", "help",
    "api", "settings", "dashboard"
}


def _normalize_buttn_url(value):
    value = (value or "").strip().lower().replace(" ", "-")
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch == "-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def _is_buttn_url_taken(url_value, current_username=None):
    normalized = _normalize_buttn_url(url_value)

    if not normalized:
        return False

    if normalized in RESERVED_BUTTN_URLS:
        return True

    for existing in BUTTN_PROFILES.keys():
        if existing == current_username:
            continue
        if existing == normalized:
            return True

    return False

def _safe_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://") or value.startswith("mailto:") or value.startswith("tel:"):
        return value
    return "https://" + value


def _get_profile(username="test"):
    username = (username or "test").strip().lower().replace(" ", "-")
    return BUTTN_PROFILES.get(username) or BUTTN_PROFILES["test"]


def _profile_logo_html(profile):
    logo_b64 = profile.get("logo_b64", "")
    if logo_b64:
        return f'<img class="profile-logo-img" src="data:image/png;base64,{html.escape(logo_b64)}" alt="Logo">'
    initial = html.escape((profile.get("name") or "B")[:1].upper())
    return f'<div class="profile-logo-fallback">{initial}</div>'


@app.route("/buttn/start/<username>", methods=["POST"])
def buttn_start_from_qr(username):
    username = (username or "test").strip().lower().replace(" ", "-") or "test"

    profile = BUTTN_PROFILES.get(username)
    if profile is None:
        profile = BUTTN_PROFILES["test"].copy()
        profile["links"] = [item.copy() for item in BUTTN_PROFILES["test"].get("links", [])]

    # Carry the uploaded QR artwork/logo forward into the BUTTN profile.
    logo_img = fetch_uploaded_image(request.files.get("artfile"))
    if logo_img is None:
        logo_img = fetch_image_from_hidden_b64((request.form.get("art_data") or "").strip())

    if logo_img is not None:
        logo_img.thumbnail((600, 600), Image.LANCZOS)
        profile["logo_b64"] = image_to_base64(logo_img)

    bg_override = (request.form.get("bg_override") or "").strip()
    parsed_bg = parse_hex_color(bg_override)
    if parsed_bg is not None:
        profile["header_bg_color"] = rgb_to_hex(parsed_bg)

    BUTTN_PROFILES[username] = profile
    return redirect(f"/buttn/edit/{username}")


@app.route("/buttn/<username>")
def buttn_public_profile(username):
    profile = _get_profile(username)
    safe_name = html.escape(profile.get("name", ""))
    safe_title = html.escape(profile.get("title", ""))
    safe_phone = html.escape(profile.get("phone", ""))
    safe_email = html.escape(profile.get("email", ""))
    safe_header_bg = _clean_hex(profile.get("header_bg_color"), "#9d5d4d")
    safe_page_bg = _clean_hex(profile.get("page_bg_color"), "#f5f5f5")
    safe_link_bg = _clean_hex(profile.get("link_bg_color"), "#e8e8ee")
    safe_link_text = _clean_hex(profile.get("link_text_color"), "#111111")
    safe_link_border = _clean_hex(profile.get("link_border_color"), "#d8dde6")
    safe_header_name_color = _clean_hex(profile.get("header_name_color"), "#111111")
    safe_header_title_color = _clean_hex(profile.get("header_title_color"), "#555555")
    safe_action_bg = _clean_hex(profile.get("action_bg_color"), "#ffffff")
    safe_action_text = _clean_hex(profile.get("action_text_color"), "#111111")
    safe_action_border = _clean_hex(profile.get("action_border_color"), "#d8dde6")

    try:
        opacity = max(0, min(100, int(profile.get("header_image_opacity") or 35))) / 100
    except ValueError:
        opacity = 0.35

    header_image_b64 = profile.get("header_image_b64", "")
    header_image_html = ""
    if header_image_b64:
        header_image_html = f'<div class="header-image" style="opacity:{opacity}; background-image:url(data:image/png;base64,{html.escape(header_image_b64)});"></div>'

    action_buttons = ""
    if safe_phone:
        action_buttons += f'<a class="action-btn" href="tel:{safe_phone}">Call</a>'
    if safe_email:
        action_buttons += f'<a class="action-btn" href="mailto:{safe_email}">Email</a>'
    action_buttons += '<a class="action-btn" href="#">Save</a>'

    links_html = ""
    for item in profile.get("links", []):
        label_raw = (item.get("label") or "").strip()
        label = html.escape(label_raw)
        url = html.escape(_safe_url(item.get("url") or ""))
        icon_key = _normalize_link_icon(item.get("icon") or _guess_icon_from_label(label_raw))
        if label and url:
            links_html += f'<a class="buttn-link" href="{url}" target="_blank" rel="noopener">{_link_icon_html(icon_key)}<span class="buttn-link-label">{label}</span></a>'

    if not links_html:
        links_html = '<div class="empty-note">No links have been added yet.</div>'

    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_name} | BUTTN</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: Arial, sans-serif; background: {safe_page_bg}; }}
.phone-shell {{ max-width: 430px; margin: 0 auto; min-height: 100vh; background: {safe_page_bg}; box-shadow: 0 0 28px rgba(0,0,0,0.08); }}
.profile-header {{ position: relative; min-height: 270px; padding: 58px 22px 28px; text-align: center; overflow: hidden; background: {safe_header_bg}; }}
.header-image {{ position:absolute; inset:0; background-size:cover; background-position:center; z-index:0; }}
.header-soft-layer {{ position:absolute; inset:0; background: linear-gradient(to bottom, rgba(255,255,255,0.02), rgba(255,255,255,0.28)); z-index:1; }}
.header-content {{ position: relative; z-index: 2; }}
.profile-logo {{ width: 126px; height: 126px; margin: 0 auto 18px; border-radius: 50%; background:#fff; display:flex; align-items:center; justify-content:center; border: 4px solid rgba(255,255,255,0.85); box-shadow: 0 12px 30px rgba(0,0,0,0.16); overflow:hidden; }}
.profile-logo-img {{ width:100%; height:100%; object-fit:cover; }}
.profile-logo-fallback {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:54px; font-weight:800; color:#111; background:#fff; }}
.profile-name {{ font-size: 25px; font-weight: 800; color:{safe_header_name_color}; }}
.profile-title {{ font-size: 15px; color:{safe_header_title_color}; margin-top: 6px; }}
.actions {{ display:flex; gap:10px; justify-content:center; flex-wrap:wrap; margin-top: 18px; }}
.action-btn {{ text-decoration:none; color:{safe_action_text}; background:{safe_action_bg}; border:1px solid {safe_action_border}; border-radius:999px; padding:10px 17px; font-weight:700; font-size:14px; }}
.links-area {{ padding: 24px 20px 34px; }}
.buttn-link {{ display:flex; align-items:center; justify-content:center; gap:12px; width:100%; text-align:center; text-decoration:none; background:{safe_link_bg}; color:{safe_link_text}; border:2px solid {safe_link_border}; border-radius:16px; padding:16px 14px; margin-bottom:13px; font-weight:800; box-shadow: 0 8px 18px rgba(0,0,0,0.04); }}
.buttn-link-icon {{ width:24px; height:24px; min-width:24px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; font-size:15px; font-weight:900; line-height:1; color:{safe_link_text}; }}\n.buttn-link-icon svg {{ width:22px; height:22px; display:block; fill: currentColor; stroke: currentColor; }}
.buttn-link-label {{ flex:0 1 auto; }}
.empty-note {{ text-align:center; color:#777; padding:18px; }}
.buttn-footer {{ text-align:center; font-size:12px; color:#777; padding: 6px 20px 26px; }}
</style>
</head>
<body>
<div class="phone-shell">
  <div class="profile-header">
    {header_image_html}
    <div class="header-soft-layer"></div>
    <div class="header-content">
      <div class="profile-logo">{_profile_logo_html(profile)}</div>
      <div class="profile-name">{safe_name}</div>
      <div class="profile-title">{safe_title}</div>
      <div class="actions">{action_buttons}</div>
    </div>
  </div>
  <div class="links-area">{links_html}</div>
  <div class="buttn-footer">Powered by BUTTN</div>
</div>
</body>
</html>
"""


@app.route("/buttn/test")
def buttn_test_alias():
    return buttn_public_profile("test")


@app.route("/buttn/edit/<username>", methods=["GET", "POST"])
def buttn_edit_profile(username):
    username = (username or "test").strip().lower().replace(" ", "-") or "test"
    profile = BUTTN_PROFILES.get(username)
    if profile is None:
        profile = BUTTN_PROFILES["test"].copy()
        profile["links"] = [item.copy() for item in BUTTN_PROFILES["test"].get("links", [])]

    if request.method == "POST":
        requested_url = (request.form.get("buttn_url") or username).strip().lower().replace(" ", "-") or "test"
        profile["buttn_url"] = requested_url
        profile["name"] = (request.form.get("name") or "").strip()
        profile["title"] = (request.form.get("title") or "").strip()
        profile["phone"] = (request.form.get("phone") or "").strip()
        profile["email"] = (request.form.get("email") or "").strip()
        profile["header_bg_color"] = _clean_hex(request.form.get("header_bg_color"), "#9d5d4d")
        profile["page_bg_color"] = _clean_hex(request.form.get("page_bg_color"), "#f5f5f5")
        profile["link_bg_color"] = _clean_hex(request.form.get("link_bg_color"), "#e8e8ee")
        profile["link_text_color"] = _clean_hex(request.form.get("link_text_color"), "#111111")
        profile["link_border_color"] = _clean_hex(request.form.get("link_border_color"), "#d8dde6")
        profile["header_name_color"] = _clean_hex(request.form.get("header_name_color"), "#111111")
        profile["header_title_color"] = _clean_hex(request.form.get("header_title_color"), "#555555")
        profile["action_bg_color"] = _clean_hex(request.form.get("action_bg_color"), "#ffffff")
        profile["action_text_color"] = _clean_hex(request.form.get("action_text_color"), "#111111")
        profile["action_border_color"] = _clean_hex(request.form.get("action_border_color"), "#d8dde6")
        try:
            profile["header_image_opacity"] = str(max(0, min(100, int(request.form.get("header_image_opacity") or 35))))
        except ValueError:
            profile["header_image_opacity"] = "35"

        logo = fetch_uploaded_image(request.files.get("logo_file"))
        if logo is not None:
            logo.thumbnail((600, 600), Image.LANCZOS)
            profile["logo_b64"] = image_to_base64(logo)

        header_img = fetch_uploaded_image(request.files.get("header_image_file"))
        if header_img is not None:
            header_img.thumbnail((1400, 900), Image.LANCZOS)
            profile["header_image_b64"] = image_to_base64(header_img)

        links = []
        for i in range(1, MAX_PROFILE_LINKS + 1):
            label_value = (request.form.get(f"link{i}_label") or "").strip()
            url_value = (request.form.get(f"link{i}_url") or "").strip()
            icon_value = _normalize_link_icon(request.form.get(f"link{i}_icon") or _guess_icon_from_label(label_value))
            if label_value or url_value:
                links.append({"icon": icon_value, "label": label_value, "url": url_value})
        if not links:
            links.append({"icon": "custom", "label": "Button Text", "url": ""})
        profile["links"] = links
        BUTTN_PROFILES[requested_url] = profile
        return redirect(f"/buttn/{requested_url}")

    def val(key, fallback=""):
        return html.escape(str(profile.get(key, fallback) or ""))

    link_inputs = ""
    existing_links = profile.get("links", [])
    visible_count = max(5, min(MAX_PROFILE_LINKS, len(existing_links) if existing_links else 5))
    for i in range(1, MAX_PROFILE_LINKS + 1):
        item = existing_links[i - 1] if i - 1 < len(existing_links) else {"icon": "custom", "label": "", "url": ""}
        label_value = item.get("label", "")
        url_value = item.get("url", "")
        icon_value = _normalize_link_icon(item.get("icon") or _guess_icon_from_label(label_value))
        if i == 1 and not label_value:
            label_value = "Button Text"
        hidden_class = " hidden-link-row" if i > visible_count else ""
        link_inputs += f'''
        <div class="link-edit-row{hidden_class}" data-link-row="{i}">
          {_icon_select_html(f"link{i}_icon", icon_value)}
          <input type="text" class="live-link-label" data-link-index="{i}" name="link{i}_label" placeholder="Button text {i}" value="{html.escape(label_value)}">
          <input type="text" class="live-link-url" data-link-index="{i}" name="link{i}_url" placeholder="Link URL {i}" value="{html.escape(url_value)}">
          <button type="button" class="remove-link-btn" data-remove-link="{i}">×</button>
        </div>
        '''



    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BUTTN Setup</title>
<style>
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Arial,sans-serif; background:#f3f5f7; color:#111; }}
.builder-wrap {{ max-width:980px; margin:0 auto; padding:28px; }}
.builder-head {{ margin-bottom:22px; }}
.builder-head h1 {{ margin:0 0 8px; font-size:30px; }}
.builder-head p {{ margin:0; color:#666; }}
.builder-grid {{ display:grid; grid-template-columns:1fr 390px; gap:26px; align-items:start; }}
.panel {{ background:#fff; border:1px solid #dde1e7; border-radius:18px; padding:20px; box-shadow:0 8px 24px rgba(0,0,0,0.04); margin-bottom:18px; }}
.panel h2 {{ margin:0 0 15px; font-size:19px; }}
.field {{ margin-bottom:14px; }}
label {{ display:block; font-weight:700; margin-bottom:7px; }}
input[type="text"], input[type="email"], input[type="tel"], input[type="color"], input[type="file"] {{ width:100%; padding:11px; border:1px solid #cfd5df; border-radius:10px; font-size:15px; }}
input[type="color"] {{ height:46px; padding:4px; }}
input[type="range"] {{ width:100%; }}
.color-grid {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:12px; }}
.link-edit-row {{ display:grid; grid-template-columns:150px 1fr 1fr 44px; gap:10px; margin-bottom:10px; align-items:center; }}
.link-edit-row.hidden-link-row {{ display:none; }}
.live-link-icon {{ width:100%; padding:11px; border:1px solid #cfd5df; border-radius:10px; font-size:15px; background:#fff; }}
.remove-link-btn {{ width:44px; height:44px; border:none; border-radius:10px; background:#f1f1f1; color:#333; font-size:24px; line-height:1; cursor:pointer; margin:0; padding:0; }}
.add-link-btn {{ width:100%; padding:12px; border:1px dashed #9aa4b2; background:#f8fafc; color:#111; font-size:15px; font-weight:800; border-radius:12px; cursor:pointer; margin-top:6px; }}
.save-btn {{ width:100%; padding:15px; border:none; background:#111; color:#fff; font-size:17px; font-weight:800; border-radius:14px; cursor:pointer; }}
.preview-card {{ background:#fff; border-radius:24px; overflow:hidden; border:1px solid #dde1e7; position:sticky; top:20px; }}
.preview-note {{ font-size:13px; color:#666; padding:15px; border-bottom:1px solid #eee; }}
.small-help {{ color:#777; font-size:13px; margin-top:6px; }}
@media (max-width: 860px) {{ .builder-grid {{ grid-template-columns:1fr; }} .preview-card {{ position:static; }} .link-edit-row {{ grid-template-columns:1fr; }} .remove-link-btn {{ width:100%; }} }}
</style>
</head>
<body>
<div class="builder-wrap">
  <div class="builder-head">
    <h1>Create Your BUTTN Profile</h1>
    <p>Set up the page your QR code and NFC button will point to.</p>
  </div>
  <div class="builder-grid">
    <form method="post" enctype="multipart/form-data">
      <div class="panel">
        <h2>Profile Identity</h2>
        <div class="field"><label>BUTTN URL</label><input id="buttn_url_input" type="text" name="buttn_url" value="{val('buttn_url', username)}"><div class="small-help">Example: /buttn/tshirt-help-desk</div></div>
        <div class="field"><label>Name / Brand</label><input id="name_input" type="text" name="name" value="{val('name')}"></div>
        <div class="field"><label>Title / Company</label><input id="title_input" type="text" name="title" value="{val('title')}"></div>
        <div class="field"><label>Phone</label><input id="phone_input" type="tel" name="phone" value="{val('phone')}"></div>
        <div class="field"><label>Email</label><input id="email_input" type="email" name="email" value="{val('email')}"></div>
        <div class="field"><label>Change Logo</label><input id="logo_file_input" type="file" name="logo_file" accept="image/*"><div class="small-help">This logo carries over from the QR generator. Upload here only if you want to change it.</div></div>
      </div>
      <div class="panel">
        <h2>Top Background</h2>
        <div class="field"><label>Header Color</label><input id="header_bg_color_input" type="color" name="header_bg_color" value="{val('header_bg_color', '#9d5d4d')}"></div>
        <div class="field"><label>Optional Header Image</label><input id="header_image_file_input" type="file" name="header_image_file" accept="image/*"></div>
        <div class="field"><label>Header Image Opacity</label><input id="header_image_opacity_input" type="range" name="header_image_opacity" min="0" max="100" value="{val('header_image_opacity', '35')}"></div>
      </div>
      <div class="panel">
        <h2>Links</h2>
        <div class="small-help" style="margin-bottom:12px;">Choose an icon, then customize the button text and URL. You can add up to 15 links.</div>
        <div id="links_editor">
          {link_inputs}
        </div>
        <button type="button" id="add_link_btn" class="add-link-btn">+ Add Link</button>
      </div>
      <div class="panel">
        <h2>Header Text & Actions</h2>
        <div class="color-grid">
          <div class="field"><label>Name Text</label><input id="header_name_color_input" type="color" name="header_name_color" value="{val('header_name_color', '#111111')}"></div>
          <div class="field"><label>Title Text</label><input id="header_title_color_input" type="color" name="header_title_color" value="{val('header_title_color', '#555555')}"></div>
          <div class="field"><label>Action Button</label><input id="action_bg_color_input" type="color" name="action_bg_color" value="{val('action_bg_color', '#ffffff')}"></div>
          <div class="field"><label>Action Text</label><input id="action_text_color_input" type="color" name="action_text_color" value="{val('action_text_color', '#111111')}"></div>
          <div class="field"><label>Action Border</label><input id="action_border_color_input" type="color" name="action_border_color" value="{val('action_border_color', '#d8dde6')}"></div>
        </div>
      </div>
      <div class="panel">
        <h2>Colors</h2>
        <div class="color-grid">
          <div class="field"><label>Page Background</label><input id="page_bg_color_input" type="color" name="page_bg_color" value="{val('page_bg_color', '#f5f5f5')}"></div>
          <div class="field"><label>Button Color</label><input id="link_bg_color_input" type="color" name="link_bg_color" value="{val('link_bg_color', '#e8e8ee')}"></div>
          <div class="field"><label>Button Text</label><input id="link_text_color_input" type="color" name="link_text_color" value="{val('link_text_color', '#111111')}"></div>
          <div class="field"><label>Button Border</label><input id="link_border_color_input" type="color" name="link_border_color" value="{val('link_border_color', '#d8dde6')}"></div>
        </div>
      </div>
      <button class="save-btn" type="submit">Save & Preview</button>
    </form>
    <div class="preview-card">
      <div class="preview-note">Live Preview (updates instantly) — Current public page: <strong>/buttn/{html.escape(username)}</strong></div>
      <div id="live_buttn_preview" style="width:100%; min-height:680px; background:#ffffff;"></div>
    </div>
  </div>
</div>
<script>
const existingLogoData = {json.dumps(profile.get("logo_b64", ""))};
const existingHeaderImageData = {json.dumps(profile.get("header_image_b64", ""))};
const iconOptions = {json.dumps({key: {"label": data["label"], "svg": SVG_ICON_MAP.get(key, "✦")} for key, data in LINK_ICON_MAP.items()})};
let liveLogoData = existingLogoData;
let liveHeaderImageData = existingHeaderImageData;

function getEl(id) {{ return document.getElementById(id); }}
function getVal(id, fallback) {{ const el = getEl(id); return el ? (el.value || fallback || "") : (fallback || ""); }}
function escapeHtml(value) {{
    return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#39;");
}}
function safeUrl(value) {{
    let v = String(value || "").trim();
    if (!v) return "";
    if (v.startsWith("http://") || v.startsWith("https://") || v.startsWith("mailto:") || v.startsWith("tel:")) return v;
    return "https://" + v;
}}
function readImageFile(input, callback) {{
    if (!input || !input.files || !input.files[0]) return;
    const reader = new FileReader();
    reader.onload = function(e) {{
        const result = e.target.result || "";
        const parts = result.split(",");
        callback(parts.length === 2 ? parts[1] : "");
        renderLivePreview();
    }};
    reader.readAsDataURL(input.files[0]);
}}
function collectLinks() {{
    const rows = Array.from(document.querySelectorAll(".link-edit-row"));
    let html = "";
    rows.forEach(function(row, index) {{
        const labelInput = row.querySelector(".live-link-label");
        const urlInput = row.querySelector(".live-link-url");
        const iconSelect = row.querySelector(".live-link-icon");
        let label = labelInput ? (labelInput.value || "").trim() : "";
        const url = urlInput ? (urlInput.value || "").trim() : "";
        const iconKey = iconSelect ? (iconSelect.value || "custom") : "custom";
        const iconData = iconOptions[iconKey] || iconOptions.custom || {{ label: "Custom", svg: "✦" }};

        if (index === 0 && !label) {{ label = "Button Text"; }}
        if (label) {{
            const href = url ? safeUrl(url) : "#";
            html += `<a class="buttn-link" href="${{escapeHtml(href)}}" target="_blank" rel="noopener"><span class="buttn-link-icon buttn-icon-${{escapeHtml(iconKey)}}" aria-label="${{escapeHtml(iconData.label)}}">${{iconData.svg || "✦"}}</span><span class="buttn-link-label">${{escapeHtml(label)}}</span></a>`;
        }}
    }});
    return html || '<div class="empty-note">No links have been added yet.</div>';
}}
function renderLivePreview() {{
    const root = getEl("live_buttn_preview");
    if (!root) return;

    const name = getVal("name_input", "Your Name");
    const title = getVal("title_input", "Title / Company");
    const phone = getVal("phone_input", "");
    const email = getVal("email_input", "");
    const headerBg = getVal("header_bg_color_input", "#9d5d4d");
    const pageBg = getVal("page_bg_color_input", "#f5f5f5");
    const linkBg = getVal("link_bg_color_input", "#e8e8ee");
    const linkText = getVal("link_text_color_input", "#111111");
    const linkBorder = getVal("link_border_color_input", "#d8dde6");
    const headerNameColor = getVal("header_name_color_input", "#111111");
    const headerTitleColor = getVal("header_title_color_input", "#555555");
    const actionBg = getVal("action_bg_color_input", "#ffffff");
    const actionText = getVal("action_text_color_input", "#111111");
    const actionBorder = getVal("action_border_color_input", "#d8dde6");
    const opacityRaw = parseInt(getVal("header_image_opacity_input", "35"), 10);
    const opacity = Math.max(0, Math.min(100, isNaN(opacityRaw) ? 35 : opacityRaw)) / 100;
    const initial = escapeHtml((name || "B").trim().charAt(0).toUpperCase() || "B");
    const logoHtml = liveLogoData
        ? `<img class="profile-logo-img" src="data:image/png;base64,${{liveLogoData}}" alt="Logo">`
        : `<div class="profile-logo-fallback">${{initial}}</div>`;
    const headerImageHtml = liveHeaderImageData
        ? `<div class="header-image" style="opacity:${{opacity}}; background-image:url(data:image/png;base64,${{liveHeaderImageData}});"></div>`
        : "";

    let actions = "";
    if (phone.trim()) actions += `<a class="action-btn" href="tel:${{escapeHtml(phone)}}">Call</a>`;
    if (email.trim()) actions += `<a class="action-btn" href="mailto:${{escapeHtml(email)}}">Email</a>`;
    actions += '<a class="action-btn" href="#">Save</a>';

    root.innerHTML = `
<style>
#live_buttn_preview * {{ box-sizing: border-box; }}
#live_buttn_preview .phone-shell {{ max-width: 430px; margin: 0 auto; min-height: 680px; background: ${{pageBg}}; }}
#live_buttn_preview .profile-header {{ position: relative; min-height: 270px; padding: 58px 22px 28px; text-align: center; overflow: hidden; background: ${{headerBg}}; }}
#live_buttn_preview .header-image {{ position:absolute; inset:0; background-size:cover; background-position:center; z-index:0; }}
#live_buttn_preview .header-soft-layer {{ position:absolute; inset:0; background: linear-gradient(to bottom, rgba(255,255,255,0.02), rgba(255,255,255,0.28)); z-index:1; }}
#live_buttn_preview .header-content {{ position: relative; z-index: 2; }}
#live_buttn_preview .profile-logo {{ width: 126px; height: 126px; margin: 0 auto 18px; border-radius: 50%; background:#fff; display:flex; align-items:center; justify-content:center; border: 4px solid rgba(255,255,255,0.85); box-shadow: 0 12px 30px rgba(0,0,0,0.16); overflow:hidden; }}
#live_buttn_preview .profile-logo-img {{ width:100%; height:100%; object-fit:cover; }}
#live_buttn_preview .profile-logo-fallback {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:54px; font-weight:800; color:#111; background:#fff; }}
#live_buttn_preview .profile-name {{ font-size: 25px; font-weight: 800; color:${{headerNameColor}}; }}
#live_buttn_preview .profile-title {{ font-size: 15px; color:${{headerTitleColor}}; margin-top: 6px; }}
#live_buttn_preview .actions {{ display:flex; gap:10px; justify-content:center; flex-wrap:wrap; margin-top: 18px; }}
#live_buttn_preview .action-btn {{ text-decoration:none; color:${{actionText}}; background:${{actionBg}}; border:1px solid ${{actionBorder}}; border-radius:999px; padding:10px 17px; font-weight:700; font-size:14px; }}
#live_buttn_preview .links-area {{ padding: 24px 20px 34px; }}
#live_buttn_preview .buttn-link {{ display:flex; align-items:center; justify-content:center; gap:12px; width:100%; text-align:center; text-decoration:none; background:${{linkBg}}; color:${{linkText}}; border:2px solid ${{linkBorder}}; border-radius:16px; padding:16px 14px; margin-bottom:13px; font-weight:800; box-shadow: 0 8px 18px rgba(0,0,0,0.04); }}
#live_buttn_preview .buttn-link-icon {{ width:24px; height:24px; min-width:24px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; font-size:15px; font-weight:900; line-height:1; color:${{linkText}}; }}\n#live_buttn_preview .buttn-link-icon svg {{ width:22px; height:22px; display:block; fill: currentColor; stroke: currentColor; }}
#live_buttn_preview .buttn-link-label {{ flex:0 1 auto; }}
#live_buttn_preview .empty-note {{ text-align:center; color:#777; padding:18px; }}
#live_buttn_preview .buttn-footer {{ text-align:center; font-size:12px; color:#777; padding: 6px 20px 26px; }}
</style>
<div class="phone-shell">
  <div class="profile-header">
    ${{headerImageHtml}}
    <div class="header-soft-layer"></div>
    <div class="header-content">
      <div class="profile-logo">${{logoHtml}}</div>
      <div class="profile-name">${{escapeHtml(name)}}</div>
      <div class="profile-title">${{escapeHtml(title)}}</div>
      <div class="actions">${{actions}}</div>
    </div>
  </div>
  <div class="links-area">${{collectLinks()}}</div>
  <div class="buttn-footer">Powered by BUTTN</div>
</div>`;
}}

[
 "buttn_url_input", "name_input", "title_input", "phone_input", "email_input",
 "header_bg_color_input", "header_image_opacity_input", "page_bg_color_input",
 "link_bg_color_input", "link_text_color_input", "link_border_color_input",
 "header_name_color_input", "header_title_color_input", "action_bg_color_input",
 "action_text_color_input", "action_border_color_input"
].forEach(function(id) {{
    const el = getEl(id);
    if (el) el.addEventListener("input", renderLivePreview);
}});
function wireLinkEditorEvents() {{
    document.querySelectorAll(".live-link-label, .live-link-url").forEach(function(el) {{
        el.addEventListener("input", renderLivePreview);
    }});
    document.querySelectorAll(".live-link-icon").forEach(function(el) {{
        el.addEventListener("change", renderLivePreview);
    }});
    document.querySelectorAll(".remove-link-btn").forEach(function(btn) {{
        btn.addEventListener("click", function() {{
            const rowNum = btn.getAttribute("data-remove-link");
            const row = document.querySelector(`[data-link-row="${{rowNum}}"]`);
            if (!row) return;
            const label = row.querySelector(".live-link-label");
            const url = row.querySelector(".live-link-url");
            const icon = row.querySelector(".live-link-icon");
            if (label) label.value = "";
            if (url) url.value = "";
            if (icon) icon.value = "custom";
            if (parseInt(rowNum || "1", 10) > 5) row.classList.add("hidden-link-row");
            renderLivePreview();
        }});
    }});
}}
wireLinkEditorEvents();

const addLinkBtn = getEl("add_link_btn");
if (addLinkBtn) {{
    addLinkBtn.addEventListener("click", function() {{
        const hiddenRows = Array.from(document.querySelectorAll(".link-edit-row.hidden-link-row"));
        if (hiddenRows.length) {{ hiddenRows[0].classList.remove("hidden-link-row"); }}
        if (document.querySelectorAll(".link-edit-row.hidden-link-row").length === 0) {{ addLinkBtn.style.display = "none"; }}
        renderLivePreview();
    }});
}}
const logoInput = getEl("logo_file_input");
const headerInput = getEl("header_image_file_input");
if (logoInput) logoInput.addEventListener("change", function() {{ readImageFile(logoInput, function(data) {{ liveLogoData = data; }}); }});
if (headerInput) headerInput.addEventListener("change", function() {{ readImageFile(headerInput, function(data) {{ liveHeaderImageData = data; }}); }});
renderLivePreview();
</script>
</body>
</html>
"""


@app.route("/buttn/edit/test", methods=["GET", "POST"])
def buttn_edit_test_alias():
    return buttn_edit_profile("test")
