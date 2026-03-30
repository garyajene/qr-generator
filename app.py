from flask import Flask, request
from io import BytesIO
import base64
from PIL import Image, ImageDraw, ImageStat
import segno

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ERROR_LEVEL = "h"
BOX = 16
QUIET = 6
WHITE_SCALE_FACTOR = 0.88


def render_page(qr_img_b64=None, card_mockup_b64=None, dome_mockup_b64=None):
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
    background: #f3f3f3;
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
    max-width: 220px;
    height: auto;
    display: block;
    margin-top: 12px;
}}

.subhead {{
    font-weight: bold;
    margin-bottom: 8px;
}}
</style>
</head>
<body>

<h1>QR Generator</h1>

<form action="/" method="post" enctype="multipart/form-data">
    <div class="label">QR Data</div>
    <input type="text" name="data" required placeholder="Enter QR Data"><br><br>

    <div class="label">Upload Artwork (optional)</div>
    <div id="dropzone">
        <span id="droptext">Drop Image Here or Click</span>
        <img id="preview" />
    </div>
    <input type="file" id="artfile" name="artfile" accept="image/*" style="display:none">

    <br>
    <button type="submit">Generate</button>
</form>

<div class="results">
    {f'''
    <div class="result-block">
        <h2>Generated QR</h2>
        <img class="generated-qr" src="data:image/png;base64,{qr_img_b64}">
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

<script>
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("artfile");
const preview = document.getElementById("preview");
const droptext = document.getElementById("droptext");

dropzone.onclick = () => fileInput.click();

fileInput.onchange = () => {{
    const file = fileInput.files[0];
    if (file) {{
        preview.src = URL.createObjectURL(file);
        preview.style.display = "block";
        droptext.style.display = "none";
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
        preview.src = URL.createObjectURL(file);
        preview.style.display = "block";
        droptext.style.display = "none";
    }}
}});
</script>

</body>
</html>
"""


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


def average_region(img, cx, cy, radius=6):
    x0 = max(0, cx - radius)
    y0 = max(0, cy - radius)
    x1 = min(img.width, cx + radius + 1)
    y1 = min(img.height, cy + radius + 1)
    region = img.crop((x0, y0, x1, y1)).convert("RGBA")

    pixels = list(region.getdata())
    valid = []
    for r, g, b, a in pixels:
        if a > 0:
            valid.append((r, g, b))

    if not valid:
        return (255, 255, 255)

    count = len(valid)
    return (
        sum(p[0] for p in valid) // count,
        sum(p[1] for p in valid) // count,
        sum(p[2] for p in valid) // count,
    )


def color_distance(c1, c2):
    return ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2) ** 0.5


def luminance(rgb):
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def choose_background_color(art):
    if not art:
        return (255, 255, 255)

    test = art.convert("RGBA").resize((240, 240), Image.LANCZOS)

    top_center = average_region(test, test.width // 2, int(test.height * 0.14))
    mid_center = average_region(test, test.width // 2, int(test.height * 0.50))

    if color_distance(top_center, mid_center) > 70:
        avg = (
            (top_center[0] + mid_center[0]) // 2,
            (top_center[1] + mid_center[1]) // 2,
            (top_center[2] + mid_center[2]) // 2,
        )
        if luminance(avg) < 128:
            return (0, 0, 0)
        return (255, 255, 255)

    return (
        (top_center[0] + mid_center[0]) // 2,
        (top_center[1] + mid_center[1]) // 2,
        (top_center[2] + mid_center[2]) // 2,
    )


def tinted_light_color(bg):
    return (
        int(255 * 0.88 + bg[0] * 0.12),
        int(255 * 0.88 + bg[1] * 0.12),
        int(255 * 0.88 + bg[2] * 0.12),
    )


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


def generate_branded_qr(data, art=None):
    qr = segno.make(data, error=ERROR_LEVEL)
    matrix = matrix_from_segno(qr)
    version = int(qr.version)
    n = len(matrix)

    bg_color = choose_background_color(art)
    light_color = tinted_light_color(bg_color)
    dark_color = (0, 0, 0)

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
                white_scale = max(0.35, min(0.85, dot_scale * WHITE_SCALE_FACTOR))
                draw_dot(x0, y0, x1, y1, white_scale, (*light_color, 255))

    qpx = QUIET * BOX
    draw.rectangle([0, 0, size, qpx], fill=(*bg_color, 255))
    draw.rectangle([0, size - qpx, size, size], fill=(*bg_color, 255))
    draw.rectangle([0, 0, qpx, size], fill=(*bg_color, 255))
    draw.rectangle([size - qpx, 0, size, size], fill=(*bg_color, 255))

    return canvas.convert("RGBA")


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
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    dome_w, dome_h = dome.size

    qr_crop = trim_qr_for_mockup(qr_img)

    qr_target = int(min(dome_w, dome_h) * 0.44)
    qr_small = qr_crop.resize((qr_target, qr_target), Image.LANCZOS)

    zoom = 1.15
    zoomed_w = int(qr_small.width * zoom)
    zoomed_h = int(qr_small.height * zoom)
    qr_small = qr_small.resize((zoomed_w, zoomed_h), Image.LANCZOS)

    base = Image.new("RGBA", dome.size, (255, 255, 255, 0))

    qr_x = (dome_w - qr_small.width) // 2
    qr_y = (dome_h - qr_small.height) // 2

    base.paste(qr_small, (qr_x, qr_y), qr_small)
    base.alpha_composite(dome, (0, 0))

    final_w = int(dome_w * 0.50)
    final_h = int(dome_h * 0.50)
    return base.resize((final_w, final_h), Image.LANCZOS)


@app.route("/", methods=["GET", "POST"])
def home():
    qr_b64 = None
    card_mockup_b64 = None
    dome_mockup_b64 = None

    if request.method == "POST":
        data = (request.form.get("data") or "").strip()
        art_file = request.files.get("artfile")

        if data:
            art = fetch_uploaded_image(art_file)
            qr_img = generate_branded_qr(data, art)

            qr_b64 = image_to_base64(qr_img)

            card_mockup = create_card_mockup(qr_img)
            dome_mockup = create_dome_mockup(qr_img)

            card_mockup_b64 = image_to_base64(card_mockup)
            dome_mockup_b64 = image_to_base64(dome_mockup)

    return render_page(
        qr_img_b64=qr_b64,
        card_mockup_b64=card_mockup_b64,
        dome_mockup_b64=dome_mockup_b64,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
