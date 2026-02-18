from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw, ImageEnhance
import segno

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Repo-Style QR Art</title>
<style>
  body { font-family: Arial, sans-serif; margin: 40px; }
  label { display:block; margin-top: 16px; font-weight: 700; }
  input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
  button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
</style>
</head>
<body>
  <h1>Repo-Style QR Art</h1>
  <form action="/generate" method="get">
    <label>QR Data</label>
    <input type="text" name="data" required />
    <label>Artwork Image URL (optional)</label>
    <input type="text" name="art" />
    <button type="submit">Generate QR</button>
  </form>
</body>
</html>
"""

@app.route("/")
def home():
    return Response(HTML, mimetype="text/html")

@app.route("/generate")
def generate():
    data = request.args.get("data", "").strip()
    art_url = request.args.get("art", "").strip()

    if not data:
        return "Missing QR data", 400

    qr = segno.make(data, error="h")
    matrix = list(qr.matrix)
    n = len(matrix)

    box = 16
    quiet = 4
    size = (n + 2 * quiet) * box

    # --- Load artwork ---
    if art_url:
        try:
            r = requests.get(art_url, timeout=10)
            r.raise_for_status()
            art = Image.open(BytesIO(r.content)).convert("RGB")
            art = art.resize((size, size), Image.LANCZOS)
        except:
            art = Image.new("RGB", (size, size), "white")
    else:
        art = Image.new("RGB", (size, size), "white")

    canvas = art.copy()
    draw = ImageDraw.Draw(canvas)

    # --- Lighten background under dark modules ---
    light_layer = Image.new("RGB", (size, size), "white")
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)

    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                x = (c + quiet) * box
                y = (r + quiet) * box
                mask_draw.rectangle([x, y, x + box, y + box], fill=180)

    canvas = Image.composite(light_layer, canvas, mask)

    draw = ImageDraw.Draw(canvas)

    # --- Draw dark dots ---
    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                x = (c + quiet) * box
                y = (r + quiet) * box
                draw.ellipse(
                    [x + 3, y + 3, x + box - 3, y + box - 3],
                    fill="black"
                )

    # --- Restore finder patterns ---
    finder = 7
    for (row, col) in [(0,0),
                       (0,n - finder),
                       (n - finder,0)]:

        x = (col + quiet) * box
        y = (row + quiet) * box
        s = finder * box

        draw.rectangle([x, y, x+s, y+s], fill="black")
        draw.rectangle([x+box, y+box, x+s-box, y+s-box], fill="white")
        draw.rectangle([x+2*box, y+2*box, x+s-2*box, y+s-2*box], fill="black")

    out = BytesIO()
    canvas.save(out, format="PNG")
    out.seek(0)

    return send_file(out, mimetype="image/png")
