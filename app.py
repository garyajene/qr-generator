from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw, ImageStat
import segno

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>QR Generator</title>
<style>
body { font-family: Arial; padding: 30px; }
#dropzone {
    width: 300px;
    height: 200px;
    border: 2px dashed #999;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    margin-bottom: 10px;
}
#dropzone.hover { border-color: #000; }
img { max-width: 300px; margin-top: 10px; }
</style>
</head>
<body>

<h1>QR Generator</h1>

<form action="/generate" method="post" enctype="multipart/form-data">

<label>QR Data</label><br>
<input type="text" name="data" required style="width:300px;"><br><br>

<label>Upload Artwork (optional)</label><br>
<div id="dropzone">Drop Image Here or Click</div>
<input type="file" id="artfile" name="artfile" accept="image/*" style="display:none">
<img id="preview" />

<br><br>

<label>Or Artwork URL (optional)</label><br>
<input type="text" name="arturl" style="width:300px;"><br><br>

<button type="submit">Generate</button>

</form>

<script>
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("artfile");
const preview = document.getElementById("preview");

dropzone.onclick = () => fileInput.click();

fileInput.onchange = () => {
    const file = fileInput.files[0];
    if (file) preview.src = URL.createObjectURL(file);
};

dropzone.addEventListener("dragover", e => {
    e.preventDefault();
    dropzone.classList.add("hover");
});

dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("hover");
});

dropzone.addEventListener("drop", e => {
    e.preventDefault();
    dropzone.classList.remove("hover");

    const file = e.dataTransfer.files[0];
    if (file) {
        fileInput.files = e.dataTransfer.files;
        preview.src = URL.createObjectURL(file);
    }
});
</script>

</body>
</html>
"""

ERROR_LEVEL = "h"
BOX = 16
QUIET = 6
WHITE_SCALE_FACTOR = 0.88


def fetch_image(url):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")


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


@app.route("/")
def home():
    return Response(HTML, mimetype="text/html")


@app.route("/generate", methods=["POST"])
def generate():
    data = (request.form.get("data") or "").strip()
    art_url = (request.form.get("arturl") or "").strip()
    art_file = request.files.get("artfile")

    if not data:
        return "Missing data", 400

    qr = segno.make(data, error=ERROR_LEVEL)
    matrix = matrix_from_segno(qr)
    version = int(qr.version)
    n = len(matrix)

    size = (n + 2 * QUIET) * BOX
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    art = None

    if art_file and art_file.filename != "":
        try:
            art = Image.open(art_file.stream).convert("RGBA")
        except:
            art = None
    elif art_url:
        try:
            art = fetch_image(art_url)
        except:
            art = None

    DOT_SCALE = 0.48

    if art:
        complexity = analyze_complexity(art)
        DOT_SCALE = get_adaptive_dot_scale(complexity)

        art_resized = art.resize((n * BOX, n * BOX), Image.LANCZOS)
        canvas.paste(art_resized, (QUIET * BOX, QUIET * BOX), art_resized)

    def draw_dot(x0, y0, x1, y1, scale, color):
        pad = (1 - scale) * BOX / 2
        draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=color)

    for r in range(n):
        for c in range(n):
            x0 = (QUIET + c) * BOX
            y0 = (QUIET + r) * BOX
            x1 = x0 + BOX
            y1 = y0 + BOX

            if is_protected(r, c, n, version):
                draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0) if matrix[r][c] else (255, 255, 255))
                continue

            if matrix[r][c]:
                draw_dot(x0, y0, x1, y1, DOT_SCALE, (0, 0, 0))
            else:
                white_scale = max(0.35, min(0.85, DOT_SCALE * WHITE_SCALE_FACTOR))
                draw_dot(x0, y0, x1, y1, white_scale, (255, 255, 255))

    qpx = QUIET * BOX
    draw.rectangle([0, 0, size, qpx], fill=(255, 255, 255))
    draw.rectangle([0, size - qpx, size, size], fill=(255, 255, 255))
    draw.rectangle([0, 0, qpx, size], fill=(255, 255, 255))
    draw.rectangle([size - qpx, 0, size, size], fill=(255, 255, 255))

    out = BytesIO()

    # ✅ FIXED PNG EXPORT (NO CORRUPTION)
    canvas.save(out, format="PNG")

    out.seek(0)

    return send_file(out, mimetype="image/png")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
