from flask import Flask, request
from io import BytesIO
import base64
from collections import Counter
from PIL import Image, ImageDraw, ImageStat
import segno

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ERROR_LEVEL = "h"
BOX = 16
QUIET = 6


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
 max-width: 200px;
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


# =========================
# ALL YOUR QR LOGIC BELOW — UNCHANGED
# =========================

def quantize_color(rgb, bucket=32):
    return (
        int(round(rgb[0] / bucket) * bucket),
        int(round(rgb[1] / bucket) * bucket),
        int(round(rgb[2] / bucket) * bucket),
    )


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

    count = len(valid)
    return (
        sum(p[0] for p in valid) // count,
        sum(p[1] for p in valid) // count,
        sum(p[2] for p in valid) // count,
    )


def build_sample_points(width, height):
    points = []
    left_x = int(width * 0.12)
    right_x = int(width * 0.88)
    top_y = int(height * 0.12)
    bottom_y = int(height * 0.88)

    side_ys = [0.18, 0.34, 0.50, 0.66, 0.82]
    side_xs = [0.18, 0.34, 0.50, 0.66, 0.82]

    for ry in side_ys:
        points.append((left_x, int(height * ry)))
        points.append((right_x, int(height * ry)))

    for rx in side_xs:
        points.append((int(width * rx), top_y))
        points.append((int(width * rx), bottom_y))

    center_points = [
        (0.35, 0.35), (0.50, 0.35), (0.65, 0.35),
        (0.35, 0.50), (0.50, 0.50), (0.65, 0.50),
        (0.42, 0.65), (0.58, 0.65),
    ]

    for rx, ry in center_points:
        points.append((int(width * rx), int(height * ry)))

    return points


def choose_background_color(art):
    if not art:
        return (255, 255, 255)

    test = art.convert("RGBA").resize((300, 300), Image.LANCZOS)
    points = build_sample_points(test.width, test.height)

    sampled_colors = []
    for x, y in points:
        rgb = sample_region_average(test, x, y, radius=7)
        sampled_colors.append(quantize_color(rgb, bucket=32))

    counts = Counter(sampled_colors)
    most_common = counts.most_common()

    if not most_common:
        return (255, 255, 255)

    return most_common[0][0]


# =========================
# 🔥 ONLY CHANGE
# =========================

def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    dome_w, dome_h = dome.size

    qr_crop = trim_qr_for_mockup(qr_img)

    try:
        pixel = qr_crop.convert("RGB").getpixel((5, 5))
        bg_color = (pixel[0], pixel[1], pixel[2])
    except Exception:
        bg_color = (255, 255, 255)

    base = Image.new("RGBA", dome.size, (*bg_color, 255))

    qr_target = int(min(dome_w, dome_h) * 0.44)
    qr_small = qr_crop.resize((qr_target, qr_target), Image.LANCZOS)

    zoom = 1.15
    qr_small = qr_small.resize(
        (int(qr_small.width * zoom), int(qr_small.height * zoom)),
        Image.LANCZOS
    )

    qr_x = (dome_w - qr_small.width) // 2
    qr_y = (dome_h - qr_small.height) // 2

    base.paste(qr_small, (qr_x, qr_y), qr_small)
    base.alpha_composite(dome)

    return base.resize((int(dome_w * 0.5), int(dome_h * 0.5)), Image.LANCZOS)


# =========================

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
