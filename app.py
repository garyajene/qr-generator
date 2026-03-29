from flask import Flask, request
from io import BytesIO
import base64
from PIL import Image, ImageDraw, ImageStat, ImageChops
import segno

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

ERROR_LEVEL = "h"
BOX = 16
QUIET = 6
WHITE_SCALE_FACTOR = 0.88


# -----------------------------
# PAGE RENDER
# -----------------------------
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
}}

#dropzone.hover {{
    border-color: #000;
}}

#preview {{
    max-width: 260px;
    max-height: 180px;
    display: none;
}}

.mockups {{
    display: flex;
    gap: 40px;
    margin-top: 20px;
}}

.mockup-card {{
    max-width: 520px;
}}

.mockup-dome {{
    max-width: 300px;
}}
</style>
</head>
<body>

<h1>QR Generator</h1>

<form action="/" method="post" enctype="multipart/form-data">
    <input type="text" name="data" required placeholder="Enter QR Data"><br><br>

    <div id="dropzone">
        <span id="droptext">Drop Image Here or Click</span>
        <img id="preview"/>
    </div>
    <input type="file" id="artfile" name="artfile" accept="image/*" style="display:none">

    <br>
    <button type="submit">Generate</button>
</form>

{f'<h2>Generated QR</h2><img src="data:image/png;base64,{qr_img_b64}">' if qr_img_b64 else ""}

{f'''
<h2>Mockups</h2>
<div class="mockups">
    <div>
        <h4>Business Card</h4>
        <img class="mockup-card" src="data:image/png;base64,{card_mockup_b64}">
    </div>
    <div>
        <h4>Dome Sticker</h4>
        <img class="mockup-dome" src="data:image/png;base64,{dome_mockup_b64}">
    </div>
</div>
''' if card_mockup_b64 else ""}

<script>
const dz = document.getElementById("dropzone");
const fi = document.getElementById("artfile");
const pv = document.getElementById("preview");
const dt = document.getElementById("droptext");

dz.onclick = () => fi.click();

fi.onchange = () => {{
    const f = fi.files[0];
    if(f){{
        pv.src = URL.createObjectURL(f);
        pv.style.display="block";
        dt.style.display="none";
    }}
}};

dz.addEventListener("dragover", e=>{{e.preventDefault();dz.classList.add("hover");}});
dz.addEventListener("dragleave", ()=>dz.classList.remove("hover"));
dz.addEventListener("drop", e=>{{
    e.preventDefault();
    fi.files = e.dataTransfer.files;
    const f = fi.files[0];
    pv.src = URL.createObjectURL(f);
    pv.style.display="block";
    dt.style.display="none";
}});
</script>

</body>
</html>
"""


# -----------------------------
# UTIL
# -----------------------------
def image_to_base64(img):
    b = BytesIO()
    img.save(b, format="PNG")
    return base64.b64encode(b.getvalue()).decode()


def fetch_image(f):
    if not f or f.filename == "":
        return None
    try:
        return Image.open(BytesIO(f.read())).convert("RGBA")
    except:
        return None


# -----------------------------
# QR ENGINE (YOUR ORIGINAL SYSTEM)
# -----------------------------
def generate_qr(data, art=None):
    qr = segno.make(data, error=ERROR_LEVEL)
    matrix = [[bool(v) for v in row] for row in qr.matrix]
    n = len(matrix)

    size = (n + 2 * QUIET) * BOX
    img = Image.new("RGBA", (size, size), (255,255,255,255))
    draw = ImageDraw.Draw(img)

    dot = 0.5

    if art:
        art = art.resize((n*BOX, n*BOX))
        img.paste(art, (QUIET*BOX, QUIET*BOX), art)

    for r in range(n):
        for c in range(n):
            x0 = (QUIET+c)*BOX
            y0 = (QUIET+r)*BOX
            x1 = x0+BOX
            y1 = y0+BOX

            if matrix[r][c]:
                pad = (1-dot)*BOX/2
                draw.ellipse([x0+pad,y0+pad,x1-pad,y1-pad], fill=(0,0,0))
    return img


# -----------------------------
# TRIM
# -----------------------------
def trim(img):
    bg = Image.new("RGB", img.size, (255,255,255))
    diff = ImageChops.difference(img.convert("RGB"), bg)
    box = diff.getbbox()
    if not box:
        return img
    l,t,r,b = box
    return img.crop((l-10,t-10,r+10,b+10))


# -----------------------------
# MOCKUPS
# -----------------------------
def create_card(qr):
    card = Image.open("static/blackcard.png").convert("RGBA")
    qr = trim(qr)

    w,h = card.size

    # BIGGER QR (UPDATED)
    q = int(w * 0.34)
    qr = qr.resize((q,q))

    x = w - q - int(w*0.05)
    y = h - q - int(h*0.07)

    card.paste(qr,(x,y),qr)
    return card


def create_dome(qr):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    qr = trim(qr)

    w,h = dome.size

    # MORE CROP / ZOOM (UPDATED)
    q = int(min(w,h)*0.65)
    qr = qr.resize((q,q))

    # extra zoom feel
    zoom = 1.35
    qr = qr.resize((int(q*zoom), int(q*zoom)))

    base = Image.new("RGBA",(w,h),(0,0,0,0))

    x = (w-qr.width)//2
    y = (h-qr.height)//2

    base.paste(qr,(x,y),qr)
    base.alpha_composite(dome)

    # realistic size reduction
    return base.resize((int(w*0.7), int(h*0.7)))


# -----------------------------
# ROUTE
# -----------------------------
@app.route("/", methods=["GET","POST"])
def home():
    qr_b64=None
    card_b64=None
    dome_b64=None

    if request.method=="POST":
        data = request.form.get("data")
        art = fetch_image(request.files.get("artfile"))

        if data:
            qr = generate_qr(data, art)

            qr_b64 = image_to_base64(qr)
            card_b64 = image_to_base64(create_card(qr))
            dome_b64 = image_to_base64(create_dome(qr))

    return render_page(qr_b64, card_b64, dome_b64)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
