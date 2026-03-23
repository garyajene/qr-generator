from flask import Flask, request, Response
from io import BytesIO
import base64
import requests
from PIL import Image, ImageDraw, ImageStat, UnidentifiedImageError
import segno

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

def render_page(qr_img_b64=None):
    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>QR Generator</title>
<style>
body {{ font-family: Arial; padding: 30px; }}
#dropzone {{
    width: 300px;
    height: 200px;
    border: 2px dashed #999;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    margin-bottom: 10px;
}}
#dropzone.hover {{ border-color: #000; }}
img {{ max-width: 300px; margin-top: 10px; display:block; }}
.result {{ margin-top: 30px; }}
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

<div class="result">
{f'<h2>Generated QR</h2><img src="data:image/png;base64,{qr_img_b64}"/>' if qr_img_b64 else ''}
</div>

<script>
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("artfile");
const preview = document.getElementById("preview");

dropzone.onclick = () => fileInput.click();

fileInput.onchange = () => {{
    const file = fileInput.files[0];
    if (file) preview.src = URL.createObjectURL(file);
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
        preview.src = URL.createObjectURL(e.dataTransfer.files[0]);
    }}
}});
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

def load_uploaded_image(file_storage):
    try:
        data = file_storage.read()
        img = Image.open(BytesIO(data))
        img.load()
        return img.convert("RGBA")
    except:
        return None

def analyze_complexity(img):
    gray = img.convert("L")
    stat = ImageStat.Stat(gray)
    stddev = stat.stddev[0] / 255.0
    extrema = gray.getextrema()
    range_val = (extrema[1] - extrema[0]) / 255.0
    return min(1.0, (stddev * 0.7) + (range_val * 0.3))

def get_adaptive_dot_scale(c):
    if c < 0.25: return 0.46
    elif c < 0.45: return 0.48
    elif c < 0.65: return 0.50
    elif c < 0.80: return 0.52
    else: return 0.54

def matrix_from_segno(qr):
    return [[bool(v) for v in row] for row in qr.matrix]

def is_protected(r, c, n):
    return (r <= 8 and c <= 8) or (r <= 8 and c >= n-9) or (r >= n-9 and c <= 8)

@app.route("/")
def home():
    return render_page()

@app.route("/generate", methods=["POST"])
def generate():
    data = request.form.get("data", "").strip()
    art_url = request.form.get("arturl", "").strip()
    art_file = request.files.get("artfile")

    qr = segno.make(data, error=ERROR_LEVEL)
    matrix = matrix_from_segno(qr)
    n = len(matrix)

    size = (n + 2 * QUIET) * BOX
    canvas = Image.new("RGBA", (size, size), (255,255,255,255))
    draw = ImageDraw.Draw(canvas)

    art = None
    if art_file and art_file.filename:
        art = load_uploaded_image(art_file)
    elif art_url:
        try: art = fetch_image(art_url)
        except: pass

    dot_scale = 0.48

    if art:
        c = analyze_complexity(art)
        dot_scale = get_adaptive_dot_scale(c)
        art = art.resize((n*BOX, n*BOX))
        canvas.paste(art, (QUIET*BOX, QUIET*BOX), art)

    def draw_dot(x0,y0,x1,y1,s,color):
        pad = (1-s)*BOX/2
        draw.ellipse([x0+pad,y0+pad,x1-pad,y1-pad], fill=color)

    for r in range(n):
        for c in range(n):
            x0 = (QUIET+c)*BOX
            y0 = (QUIET+r)*BOX
            x1 = x0+BOX
            y1 = y0+BOX

            if is_protected(r,c,n):
                draw.rectangle([x0,y0,x1,y1], fill=(0,0,0) if matrix[r][c] else (255,255,255))
                continue

            if matrix[r][c]:
                draw_dot(x0,y0,x1,y1,dot_scale,(0,0,0))
            else:
                draw_dot(x0,y0,x1,y1,dot_scale*WHITE_SCALE_FACTOR,(255,255,255))

    out = BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    img_b64 = base64.b64encode(out.getvalue()).decode()

    return render_page(img_b64)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
