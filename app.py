from flask import Flask, request, send_file, Response
from io import BytesIO
import requests
from PIL import Image, ImageDraw
import segno

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Repo-Style QR Art (Centered + Fast)</title>
<style>
  body { font-family: Arial, sans-serif; margin: 40px; }
  h1 { margin-bottom: 8px; }
  label { display:block; margin-top: 16px; font-weight: 700; }
  input { width: 780px; max-width: 95vw; padding: 10px; font-size: 16px; }
  .row { margin-top: 14px; display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
  button { margin-top: 18px; padding: 10px 18px; font-size: 18px; cursor:pointer; }
</style>
</head>
<body>
  <h1>Repo-Style QR Art (Centered + Fast)</h1>

  <form action="/generate" method="get">
    <label>QR Data (URL or text)</label>
    <input type="text" name="data" placeholder="https://..." required />

    <label>Artwork Image URL (optional)</label>
    <input type="text" name="art" placeholder="https://.../image.png" />

    <label>Dot Size (0.55–0.92). Default 0.78</label>
    <input type="text" name="dot" value="0.78" />

    <div class="row">
      <button type="submit">Generate QR</button>
      <a href="/health">health</a>
    </div>
  </form>
</body>
</html>
"""

def clamp(v, lo, hi, default):
    try:
        x = float(v)
        return max(lo, min(hi, x))
    except:
        return default

def fetch_ima_
