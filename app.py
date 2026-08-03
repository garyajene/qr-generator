Warning: truncated output (original token count: 65933)
Total output lines: 6007

from flask import Flask, request, redirect, session, Response
from io import BytesIO, StringIO
import csv
import base64
import random
import re
from collections import Counter
from PIL import Image, ImageDraw, ImageStat
import segno
import html
import json
import os
import urllib.parse
import urllib.request
from sqlalchemy import create_engine, text
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY", "buttn-dev-secret-change-later")


BUTTN_LOGO_WHITE_FILE = "/static/buttn_logo_wht.png"
BUTTN_LOGO_BLACK_FILE = "/static/buttn_logo_blk.png"


def _buttn_logo_html(version="white", class_name="buttn-brand-logo", alt="BUTTN"):
    logo_src = BUTTN_LOGO_WHITE_FILE if (version or "white").lower() == "white" else BUTTN_LOGO_BLACK_FILE
    return f'<img class="{html.escape(class_name)}" src="{html.escape(logo_src)}" alt="{html.escape(alt)}">'

TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "").strip()
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "").strip()

# -----------------------------
# STRIPE / BUTTN PRO SETTINGS
# -----------------------------
# These IDs are intentionally kept as defaults so the app works with the
# Stripe product and price you provided. Railway environment variables can
# override them later without changing this file.
STRIPE_PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID", "price_1Tksg0LsMfpRkC1z5MxBG7HD").strip()
STRIPE_PRO_PRODUCT_ID = os.environ.get("STRIPE_PRO_PRODUCT_ID", "prod_UkNQPhy4O10vsf").strip()
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()



def verify_turnstile_response(token, remote_ip=None):
    """
    Validate Cloudflare Turnstile on the server side.
    If keys are missing, fail closed for account creation.
    """
    if not TURNSTILE_SECRET_KEY:
        return False

    token = (token or "").strip()
    if not token:
        return False

    payload = {
        "secret": TURNSTILE_SECRET_KEY,
        "response": token,
    }

    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=data,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return bool(result.get("success"))
    except Exception:
        return False



# -----------------------------
# DATABASE FOUNDATION - SAFE ADD-ON
# PostgreSQL lives in Railway. SQLAlchemy lets this Flask app talk to it.
# This block does not replace the existing BUTTN demo dictionary yet.
# It only creates the first real database tables so we can move toward
# accounts, usernames, profile ownership, and Free/Pro plans safely.
# -----------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# Railway/Postgres URLs may sometimes use postgres://, while SQLAlchemy expects
# postgresql:// with newer drivers.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
_db_initialized = False


def init_database():
    """Create the first real BUTTN database tables if they do not exist."""
    global _db_initialized

    if _db_initialized or engine is None:
        return

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                account_type TEXT NOT NULL DEFAULT 'trial',
                trial_started_at TIMESTAMPTZ DEFAULT NOW(),
                trial_ends_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '30 days'),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS profiles (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                username TEXT UNIQUE NOT NULL,
                name TEXT DEFAULT '',
                title TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                email TEXT DEFAULT '',
                logo_b64 TEXT DEFAULT '',
                header_image_b64 TEXT DEFAULT '',
                header_bg_color TEXT DEFAULT '#9d5d4d',
                header_image_opacity TEXT DEFAULT '35',
                page_bg_color TEXT DEFAULT '#f5f5f5',
                link_bg_color TEXT DEFAULT '#e8e8ee',
                link_text_color TEXT DEFAULT '#111111',
                link_border_color TEXT DEFAULT '#d8dde6',
                header_name_color TEXT DEFAULT '#111111',
                header_title_color TEXT DEFAULT '#555555',
                action_bg_color TEXT DEFAULT '#ffffff',
                action_text_color TEXT DEFAULT '#111111',
                action_border_color TEXT DEFAULT '#d8dde6',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS profile_links (
                id SERIAL PRIMARY KEY,
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                icon TEXT DEFAULT 'custom',
                label TEXT DEFAULT '',
                url TEXT DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS profile_views (
                id SERIAL PRIMARY KEY,
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS link_clicks (
                id SERIAL PRIMARY KEY,
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                link_label TEXT DEFAULT '',
                link_url TEXT DEFAULT '',
                clicked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS lead_capture_enabled BOOLEAN NOT NULL DEFAULT FALSE
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS lead_capture_headline TEXT DEFAULT 'Stay Connected'
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS lead_capture_button_text TEXT DEFAULT 'Submit'
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS spotlight_enabled BOOLEAN NOT NULL DEFAULT FALSE
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS spotlight_image_b64 TEXT DEFAULT ''
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS spotlight_headline TEXT DEFAULT ''
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS spotlight_subtext TEXT DEFAULT ''
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS spotlight_url TEXT DEFAULT ''
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS spotlight_show_play BOOLEAN NOT NULL DEFAULT FALSE
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS spotlight_open_behavior TEXT DEFAULT 'new_tab'
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS spotlight_media_shape TEXT DEFAULT 'vertical'
        """))

        conn.execute(text("""
            ALTER TABLE profiles
            ADD COLUMN IF NOT EXISTS spotlight_autoplay BOOLEAN NOT NULL DEFAULT FALSE
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS profile_leads (
                id SERIAL PRIMARY KEY,
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                name TEXT DEFAULT '',
                email TEXT NOT NULL,
                phone TEXT DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            ALTER TABLE profile_leads
            ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT ''
        """))

        conn.execute(text("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMPTZ DEFAULT NOW()
        """))

        conn.execute(text("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '30 days')
        """))

        conn.execute(text("""
            UPDATE users
            SET
                account_type = 'trial',
                trial_started_at = COALESCE(trial_started_at, NOW()),
                trial_ends_at = COALESCE(trial_ends_at, NOW() + INTERVAL '30 days')
            WHERE LOWER(COALESCE(account_type, 'free')) = 'free'
              AND trial_started_at IS NULL
        """))

        conn.execute(text("""
            UPDATE users
            SET account_type = 'free'
            WHERE LOWER(COALESCE(account_type, 'free')) = 'trial'
              AND trial_ends_at IS NOT NULL
              AND trial_ends_at <= NOW()
        """))

    _db_initialized = True


@app.before_request
def ensure_database_ready():
    # Keep this safe: if DATABASE_URL has not been attached to the web service yet,
    # the current app still runs normally.
    try:
        init_database()
    except Exception:
        pass


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



def relative_luminance(rgb):
    """Return WCAG relative luminance for an RGB color."""
    channels = []
    for value in rgb:
        normalized = value / 255.0
        if normalized <= 0.04045:
            channels.append(normalized / 12.92)
        else:
            channels.append(((normalized + 0.055) / 1.055) ** 2.4)
    return (0.2126 * channels[0]) + (0.7152 * channels[1]) + (0.0722 * channels[2])


def contrast_ratio(luminance_a, luminance_b):
    lighter = max(luminance_a, luminance_b)
    darker = min(luminance_a, luminance_b)
    return (lighter + 0.05) / (darker + 0.05)


def adaptive_qr_module_color(bg_color):
    """Choose the QR module color for the selected background.

    Light and medium backgrounds keep the existing pure black modules. For
    sufficiently dark backgrounds, choose the darkest neutral gray that gives
    phone cameras a small luminance separation from the background while still
    reading visually as black.
    """
    bg_luminance = relative_luminance(bg_color)
    dark_background_luminance = 0.10

    if bg_luminance > dark_background_luminance:
        return (0, 0, 0)

    minimum_contrast = 1.6
    minimum_luminance_delta = 0.035
    darkest_blackish_gray = 112

    for gray in range(1, darkest_blackish_gray + 1):
        gray_luminance = relative_luminance((gray, gray, gray))
        if (
            gray_luminance > bg_luminance
            and contrast_ratio(gray_luminance, bg_luminance) >= minimum_contrast
            and (gray_luminance - bg_luminance) >= minimum_luminance_delta
        ):
            return (gray, gray, gray)

    return (darkest_blackish_gray, darkest_blackish_gray, darkest_blackish_gray)

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
    dark_color = adaptive_qr_module_color(bg_color)
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


QR_DIAGNOSTIC_VARIANTS = [
    {
        "key": "A",
        "name": "Current production renderer",
        "description": "No changes. This calls the same renderer used by /generate.",
    },
    {
        "key": "B",
        "name": "True light-on-dark dots",
        "description": "Dark-background test: QR signal modules are white and opposite modules are the selected dark ground color. Protected structures stay solid and high contrast.",
        "render_mode": "light_on_dark",
    },
    {
        "key": "C",
        "name": "True light-on-dark, stronger dots",
        "description": "Same true light-on-dark renderer as B, with larger data dots to survive camera blur and resizing.",
        "render_mode": "light_on_dark",
        "dot_scale_delta": 0.14,
    },
    {
        "key": "D",
        "name": "True light-on-dark square baseline",
        "description": "Same polarity and protected structures as B, but ordinary data modules use large squares as a conservative scanability baseline.",
        "render_mode": "light_on_dark",
        "data_shape": "square",
        "dot_scale_delta": 0.30,
    },
    {
        "key": "E",
        "name": "True light-on-dark, mask 0",
        "description": "Same renderer as B with QR mask pattern 0 forced for artwork-interference testing.",
        "render_mode": "light_on_dark",
        "dot_scale_delta": 0.14,
        "mask": 0,
    },
    *[
        {
            "key": chr(ord("F") + mask - 1),
            "name": f"True light-on-dark, mask {mask}",
            "description": f"Same renderer as B with QR mask pattern {mask} forced for artwork-interference testing.",
            "render_mode": "light_on_dark",
            "dot_scale_delta": 0.14,
            "mask": mask,
        }
        for mask in range(1, 8)
    ],
]


def generate_branded_qr_diagnostic_variant(data, art=None, bg_override=None, variant=None):
    """
    Developer-only diagnostic clone of generate_branded_qr.

    Version A intentionally delegates to production. Other versions keep the
    same data, normalized artwork, selected background, sizing, QR matrix, and
    export dimensions while changing exactly one rendering variable.
    """
    variant = variant or {}
    if variant.get("key") == "A":
        return generate_branded_qr(data, art, bg_override=bg_override)

    mask = variant.get("mask")
    if mask is None:
        qr = segno.make(data, error=ERROR_LEVEL)
    else:
        qr = segno.make(data, error=ERROR_LEVEL, mask=mask)
    matrix = matrix_from_segno(qr)
    version = int(qr.version)
    n = len(matrix)

    bg_color = choose_background_color(art, bg_override=bg_override)
    dark_color = adaptive_qr_module_color(bg_color)
    light_color = (255, 255, 255)

    size = (n + 2 * QUIET) * BOX
    canvas = Image.new("RGBA", (size, size), (*bg_color, 255))
    draw = ImageDraw.Draw(canvas)

    dot_scale = 0.48

    if art:
        complexity = analyze_complexity(art)
        dot_scale = get_adaptive_dot_scale(complexity)
        art_resample = variant.get("art_resample", Image.LANCZOS)
        art_resized = art.resize((n * BOX, n * BOX), art_resample)
        canvas.paste(art_resized, (QUIET * BOX, QUIET * BOX), art_resized)

    dot_scale = max(0.20, min(0.95, dot_scale + variant.get("dot_scale_delta", 0.0)))
    white_scale_factor = variant.get("white_scale_factor", 0.88)
    protected_shape = variant.get("protected_shape", "rectangle")
    render_mode = variant.get("render_mode", "production")
    data_shape = variant.get("data_shape", "dot")

    def draw_dot(x0, y0, x1, y1, scale, color):
        pad = (1.0 - scale) * BOX / 2.0
        draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=color)

    def draw_square(x0, y0, x1, y1, scale, color):
        pad = (1.0 - scale) * BOX / 2.0
        draw.rectangle([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=color)

    for r in range(n):
        for c in range(n):
            x0 = (QUIET + c) * BOX
            y0 = (QUIET + r) * BOX
            x1 = x0 + BOX
            y1 = y0 + BOX

            if render_mode == "light_on_dark":
                # A real inverted QR maps Segno's signal modules to white and
                # its opposite modules to the selected dark ground. The old
                # renderer did the reverse: gray signal modules disappeared
                # into black while white non-signal dots became visually
                # dominant. Paint every protected cell solid so finder,
                # separator, timing, format, and alignment patterns retain
                # their exact geometry and maximum contrast.
                inverted_fill = (*light_color, 255) if matrix[r][c] else (*bg_color, 255)
                if is_protected(r, c, n, version):
                    draw.rectangle([x0, y0, x1, y1], fill=inverted_fill)
                elif data_shape == "square":
                    draw_square(x0, y0, x1, y1, dot_scale, inverted_fill)
                else:
                    draw_dot(x0, y0, x1, y1, dot_scale, inverted_fill)
                continue

            fill = (*dark_color, 255) if matrix[r][c] else (*light_color, 255)

            if is_protected(r, c, n, version):
                if protected_shape == "dot":
                    draw_dot(x0, y0, x1, y1, dot_scale, fill)
                else:
                    draw.rectangle([x0, y0, x1, y1], fill=fill)
                continue

            if matrix[r][c]:
                draw_dot(x0, y0, x1, y1, dot_scale, fill)
            else:
                white_scale = max(0.35, min(0.85, dot_scale * white_scale_factor))
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

    Work on a per-generation copy of the supplied logo so Pillow operations such
    as thumbnail() never mutate artwork that may be reused by the request while
    rendering previews or preserving the upload field.
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
        logo = logo.copy().convert("RGBA")

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
    manual_override_value = "1" if bg_override_value else ""
    safe_manual_override_value = html.escape(manual_override_value)
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

{_app_nav_css()}

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
{_app_nav_html()}

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
    <button type="submit" name="generate_action" value="generate">Generate</button>

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
                                    <input type="hidden" id="bg_manual_override" name="bg_manual_override" value="{safe_manual_override_value}">
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
                            <button type="submit" class="apply-btn" name="generate_action" value="apply_bg" onclick="document.getElementById('bg_manual_override').value='1';">Apply Background Color</button>
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
        const manualInput = document.getElementById("bg_manual_override");
        if (manualInput) {{
            manualInput.value = "";
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
const manualOverrideInput = document.getElementById("bg_manual_override");

dropzone.onclick = () => fileInput.click();

function clearGeneratedStateForNewArtwork() {{
    document.querySelectorAll(".preview-and-mockups").forEach(el => el.remove());

    if (!manualOverrideInput || manualOverrideInput.value !== "1") {{
        const bgInput = document.getElementById("bg_override");
        if (bgInput) bgInput.value = "";
        document.querySelectorAll(".bg-tools").forEach(el => el.remove());
    }}
}}

function loadFileIntoPreview(file) {{
    if (!file) return;
    clearGeneratedStateForNewArtwork();
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
        const imgW = sourceImg.natura…35933 tokens truncated… action="/buttn/lead/{html.escape(_normalize_buttn_url(username))}">
                <input type="text" name="lead_name" placeholder="Your name">
                <input type="email" name="lead_email" placeholder="Your email" required>
                <input type="tel" name="lead_phone" placeholder="Phone (optional)">
                <button type="submit">{lead_button_text}</button>
              </form>
            </div>
            """

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
.spotlight-card {{ display:block; width:100%; text-align:left; text-decoration:none; color:#111; background:#fff; border:1px solid #dde1e7; border-radius:20px; overflow:hidden; margin-bottom:18px; box-shadow:0 10px 24px rgba(0,0,0,0.08); padding:0; font-family:Arial,sans-serif; cursor:pointer; }}
.spotlight-button {{ appearance:none; -webkit-appearance:none; }}
.spotlight-image-wrap {{ position:relative; width:100%; background:#111; aspect-ratio:9/16; overflow:hidden; }}
.spotlight-shape-landscape {{ aspect-ratio:16/9; }}
.spotlight-shape-square {{ aspect-ratio:1/1; }}
.spotlight-shape-vertical {{ aspect-ratio:9/16; }}
.spotlight-image-wrap img {{ width:100%; height:100%; object-fit:cover; display:block; }}
.spotlight-player-inline iframe {{ width:100%; height:100%; border:0; display:block; }}
.spotlight-inline-trigger {{ display:block; width:100%; border:0; padding:0; margin:0; background:transparent; cursor:pointer; font-family:Arial,sans-serif; }}
.spotlight-inline-trigger .spotlight-image-wrap {{ pointer-events:none; }}
.spotlight-inline-card {{ cursor:default; }}
.spotlight-empty-media {{ display:flex; align-items:center; justify-content:center; min-height:220px; }}
.spotlight-open-original-inline {{ display:block; padding:0 16px 16px; color:#111; font-size:13px; font-weight:900; text-decoration:none; }}
.spotlight-play {{ position:absolute; left:50%; top:50%; transform:translate(-50%,-50%); width:62px; height:62px; border-radius:999px; background:rgba(0,0,0,0.72); color:#fff; display:flex; align-items:center; justify-content:center; font-size:28px; padding-left:4px; box-shadow:0 8px 24px rgba(0,0,0,0.28); }}
.spotlight-copy {{ padding:16px; }}
.spotlight-modal {{ display:none; position:fixed; inset:0; z-index:99999; align-items:center; justify-content:center; padding:18px; }}
.spotlight-modal.show {{ display:flex; }}
.spotlight-modal-backdrop {{ position:absolute; inset:0; background:rgba(0,0,0,0.72); }}
.spotlight-modal-card {{ position:relative; z-index:2; width:min(92vw,430px); background:#111; border-radius:22px; padding:14px; box-shadow:0 24px 70px rgba(0,0,0,0.45); }}
.spotlight-modal-landscape {{ width:min(92vw,760px); }}
.spotlight-player-frame {{ width:100%; background:#000; border-radius:16px; overflow:hidden; }}
.spotlight-player-frame iframe {{ width:100%; height:100%; border:0; display:block; }}
.spotlight-modal-close {{ position:absolute; top:-14px; right:-10px; width:38px; height:38px; border:none; border-radius:999px; background:#fff; color:#111; font-size:26px; font-weight:900; line-height:1; cursor:pointer; }}
.spotlight-open-original {{ display:block; margin-top:12px; color:#fff; text-align:center; font-weight:900; text-decoration:none; }}
.spotlight-kicker {{ font-size:12px; text-transform:uppercase; letter-spacing:.08em; font-weight:900; color:#777; margin-bottom:6px; }}
.spotlight-copy h2 {{ margin:0; font-size:21px; line-height:1.15; }}
.spotlight-subtext {{ margin-top:8px; color:#555; font-size:14px; line-height:1.4; }}
.lead-capture-card {{ background:#fff; border:1px solid #dde1e7; border-radius:18px; padding:18px; margin-top:18px; box-shadow:0 8px 18px rgba(0,0,0,0.04); }}
.lead-capture-card h2 {{ margin:0 0 12px; font-size:20px; text-align:center; }}
.lead-capture-card input {{ width:100%; box-sizing:border-box; padding:13px; border:1px solid #cfd5df; border-radius:12px; font-size:15px; margin-bottom:10px; }}
.lead-capture-card button {{ width:100%; border:none; border-radius:14px; padding:14px; background:#111; color:#fff; font-size:16px; font-weight:900; cursor:pointer; }}
.lead-success-card {{ text-align:center; }}
.lead-success-icon {{ width:42px; height:42px; margin:0 auto 10px; border-radius:999px; background:#111; color:#fff; display:flex; align-items:center; justify-content:center; font-size:24px; font-weight:900; }}
.lead-success-card p {{ margin:0; color:#555; font-size:15px; line-height:1.45; }}
.buttn-footer {{ text-align:center; font-size:12px; color:#777; padding: 6px 20px 26px; }}
{_app_nav_css()}
</style>
</head>
<body>
{_app_nav_html(username)}
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
  <div class="links-area">{spotlight_html}{links_html}{lead_capture_html}</div>
  <div class="buttn-footer">Powered by {_buttn_logo_html("black", "buttn-footer-logo")}</div>
</div>
{spotlight_player_modal_html}
<script>
function loadSpotlightInline(button) {{
    if (!button) return;
    const src = button.getAttribute("data-src") || "";
    const aspect = button.getAttribute("data-aspect") || "9 / 16";
    if (!src) return;
    const frame = document.createElement("div");
    frame.className = "spotlight-image-wrap spotlight-player-inline";
    frame.style.aspectRatio = aspect;
    frame.innerHTML = '<iframe src="' + src.replace(/"/g, "&quot;") + '" title="Featured Spotlight" allow="autoplay; fullscreen; picture-in-picture; encrypted-media" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>';
    button.replaceWith(frame);
}}
</script>
</body>
</html>
"""



@app.route("/buttn/click/<username>/<int:link_index>")
def buttn_track_link_click(username, link_index):
    username = _normalize_buttn_url(username)
    destination = request.args.get("u") or ""
    profile = _get_profile(username)
    label = ""
    raw_url = ""

    try:
        links = profile.get("links", [])
        if 1 <= link_index <= len(links):
            item = links[link_index - 1]
            label = item.get("label", "")
            raw_url = _safe_url(item.get("url", ""))
    except Exception:
        pass

    final_url = destination or raw_url
    if not final_url:
        return redirect(f"/{username}")

    _record_link_click(username, label, final_url)
    return redirect(final_url)




@app.route("/buttn/lead/<username>", methods=["POST"])
def buttn_submit_lead(username):
    username = _normalize_buttn_url(username)
    profile = _get_profile(username)
    if not profile or not profile.get("lead_capture_enabled"):
        return redirect(f"/{username}")
    lead_name = (request.form.get("lead_name") or "").strip()
    lead_email = (request.form.get("lead_email") or "").strip()
    lead_phone = (request.form.get("lead_phone") or "").strip()
    _record_lead(username, lead_name, lead_email, lead_phone)
    return redirect(f"/{username}?lead=thanks")


@app.route("/account/leads/<username>/export")
def account_profile_leads_export(username):
    user_id = _current_user_id()
    if not user_id:
        return redirect("/login")

    username = _normalize_buttn_url(username)
    owner_id = _db_profile_owner_id(username)
    if not owner_id or owner_id != user_id:
        return redirect("/account")

    rows = _get_profile_leads(username, limit=10000)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Email", "Phone", "Date"])

    for row in rows:
        writer.writerow([
            row.get("name") or "",
            row.get("email") or "",
            row.get("phone") or "",
            row.get("created_label") or "",
        ])

    csv_data = output.getvalue()
    filename = f"buttn-leads-{username}.csv"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        },
    )


@app.route("/account/leads/<username>")
def account_profile_leads(username):
    user_id = _current_user_id()
    if not user_id:
        return redirect("/login")

    username = _normalize_buttn_url(username)
    owner_id = _db_profile_owner_id(username)
    if not owner_id or owner_id != user_id:
        return redirect("/account")

    profile = _get_profile(username)
    rows = _get_profile_leads(username)
    safe_username = html.escape(username)
    safe_name = html.escape(profile.get("name") or _display_name_from_username(username) or username)

    lead_rows = ""
    for row in rows:
        lead_name = html.escape(row.get("name") or "No name")
        lead_email = html.escape(row.get("email") or "")
        lead_phone = html.escape(row.get("phone") or "")
        created_label = html.escape(row.get("created_label") or "")
        phone_html = f'<br><a href="tel:{lead_phone}">{lead_phone}</a>' if lead_phone else ""
        lead_rows += f"""
        <div class="lead-row">
            <div><strong>{lead_name}</strong><br><a href="mailto:{lead_email}">{lead_email}</a>{phone_html}</div>
            <div class="lead-date">{created_label}</div>
        </div>
        """

    if not lead_rows:
        lead_rows = '<p class="empty">No leads captured yet.</p>'

    return f"""
<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Leads | BUTTN</title>
<style>
body {{ margin:0; font-family:Arial,sans-serif; background:#f3f5f7; color:#111; }}
.wrap {{ max-width:760px; margin:40px auto; padding:20px; }}
.card {{ background:#fff; border:1px solid #dde1e7; border-radius:18px; padding:24px; box-shadow:0 8px 24px rgba(0,0,0,0.04); margin-bottom:18px; }}
h1 {{ margin:0 0 6px; }} .muted, .empty {{ color:#666; }}
.lead-row {{ display:flex; justify-content:space-between; gap:16px; border-top:1px solid #eee; padding:16px 0; align-items:center; }}
.lead-row:first-child {{ border-top:none; }}
.lead-date {{ color:#666; font-size:13px; text-align:right; }}
a {{ color:#111; font-weight:800; }}\n.export-btn {{ display:inline-block; background:#111; color:#fff; text-decoration:none; border-radius:12px; padding:12px 16px; }}
{_app_nav_css()}
@media (max-width:640px) {{ .lead-row {{ align-items:flex-start; flex-direction:column; }} .lead-date {{ text-align:left; }} }}
</style></head>
<body>{_app_nav_html(username)}
<div class="wrap">
  <div class="card">
    <h1>Leads</h1>
    <div class="muted">{safe_name} /{safe_username}</div>
    <p><a href="/account">Back to Account</a> &nbsp; <a href="/buttn/edit/{safe_username}">Edit Profile</a> &nbsp; <a href="/{safe_username}" target="_blank">View Profile</a></p>\n    <p><a class="export-btn" href="/account/leads/{safe_username}/export">Download Leads CSV</a></p>
  </div>
  <div class="card">
    <h2>Captured Leads ({len(rows)})</h2>
    {lead_rows}
  </div>
</div>
</body></html>
"""

@app.route("/<username>")
def buttn_public_profile_root(username):
    username = _normalize_buttn_url(username)
    if (not username) or username in RESERVED_BUTTN_URLS:
        return redirect("/generate")
    if not (_db_profile_exists(username) or username in BUTTN_PROFILES):
        return redirect("/generate")
    return buttn_public_profile(username)


@app.route("/buttn/test")
def buttn_test_alias():
    return buttn_public_profile("test")


@app.route("/buttn/edit/<username>", methods=["GET", "POST"])
def buttn_edit_profile(username):
    username = _normalize_buttn_url(username or "test") or "test"
    user_id = _current_user_id() if "_current_user_id" in globals() else None
    db_owner_id = _db_profile_owner_id(username) if "_db_profile_owner_id" in globals() else None

    if db_owner_id and db_owner_id != user_id:
        return redirect("/login")

    profile = _load_db_profile(username) if "_load_db_profile" in globals() else None
    if profile is None:
        profile = BUTTN_PROFILES.get(username)
    if profile is None:
        profile = BUTTN_PROFILES["test"].copy()
        profile["links"] = [item.copy() for item in BUTTN_PROFILES["test"].get("links", [])]
        profile["buttn_url"] = username

    if request.method == "POST":
        requested_url = _normalize_buttn_url(request.form.get("buttn_url") or username) or "test"
        if user_id and _is_buttn_url_taken(requested_url, current_username=username):
            return _dashboard_page(url_message="That BUTTN URL is already taken.")
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
        profile["lead_capture_enabled"] = (request.form.get("lead_capture_enabled") == "on")
        profile["lead_capture_headline"] = (request.form.get("lead_capture_headline") or "Stay Connected").strip()[:120]
        profile["lead_capture_button_text"] = (request.form.get("lead_capture_button_text") or "Submit").strip()[:60]
        profile["spotlight_enabled"] = (request.form.get("spotlight_enabled") == "on")
        profile["spotlight_headline"] = (request.form.get("spotlight_headline") or "").strip()[:140]
        profile["spotlight_subtext"] = (request.form.get("spotlight_subtext") or "").strip()[:240]
        profile["spotlight_url"] = (request.form.get("spotlight_url") or "").strip()[:500]
        profile["spotlight_show_play"] = (request.form.get("spotlight_show_play") == "on")
        profile["spotlight_autoplay"] = (request.form.get("spotlight_autoplay") == "on")
        profile["spotlight_open_behavior"] = _normalize_spotlight_open_behavior(request.form.get("spotlight_open_behavior") or profile.get("spotlight_open_behavior"))
        profile["spotlight_media_shape"] = _normalize_spotlight_media_shape(request.form.get("spotlight_media_shape") or profile.get("spotlight_media_shape"))
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

        if (request.form.get("clear_spotlight_image") or "").strip() == "1":
            profile["spotlight_image_b64"] = ""

        spotlight_img = fetch_uploaded_image(request.files.get("spotlight_image_file"))
        if spotlight_img is not None:
            spotlight_img.thumbnail((1200, 1600), Image.LANCZOS)
            profile["spotlight_image_b64"] = image_to_base64(spotlight_img)

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
        profile["buttn_url"] = requested_url

        if user_id:
            saved = _save_db_profile(requested_url, profile, user_id)
            if not saved:
                return _dashboard_page("Profile could not be saved. That URL may already be taken.")
        else:
            BUTTN_PROFILES[requested_url] = profile

        return redirect(f"/{requested_url}")

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
input[type="text"], input[type="email"], input[type="tel"], input[type="color"], input[type="file"], select {{ width:100%; padding:11px; border:1px solid #cfd5df; border-radius:10px; font-size:15px; background:#fff; }}
input[type="color"] {{ height:46px; padding:4px; }}
input[type="range"] {{ width:100%; }}
.color-grid {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:12px; }}
.link-edit-row {{ display:grid; grid-template-columns:150px 1fr 1fr 44px; gap:10px; margin-bottom:10px; align-items:center; }}
.link-edit-row.hidden-link-row {{ display:none; }}
.live-link-icon {{ width:100%; padding:11px; border:1px solid #cfd5df; border-radius:10px; font-size:15px; background:#fff; }}
.remove-link-btn {{ width:44px; height:44px; border:none; border-radius:10px; background:#f1f1f1; color:#333; font-size:24px; line-height:1; cursor:pointer; margin:0; padding:0; }}
.add-link-btn {{ width:100%; padding:12px; border:1px dashed #9aa4b2; background:#f8fafc; color:#111; font-size:15px; font-weight:800; border-radius:12px; cursor:pointer; margin-top:6px; }}
.secondary-btn {{ width:100%; padding:11px; border:1px solid #cfd5df; background:#f8fafc; color:#111; font-size:14px; font-weight:800; border-radius:12px; cursor:pointer; margin-top:8px; }}
.save-btn {{ width:100%; padding:15px; border:none; background:#111; color:#fff; font-size:17px; font-weight:800; border-radius:14px; cursor:pointer; }}
.preview-card {{ background:#fff; border-radius:24px; overflow:hidden; border:1px solid #dde1e7; position:sticky; top:20px; }}
.preview-note {{ font-size:13px; color:#666; padding:15px; border-bottom:1px solid #eee; }}
.small-help {{ color:#777; font-size:13px; margin-top:6px; }}
.toggle-row {{ display:flex; align-items:center; gap:10px; font-weight:800; margin-bottom:14px; }}
.toggle-row input {{ width:auto; }}
.plan-preview-panel {{ background:#111; color:#fff; border-radius:18px; padding:16px 18px; margin:18px 0 22px; display:flex; align-items:center; justify-content:space-between; gap:14px; flex-wrap:wrap; box-shadow:0 8px 24px rgba(0,0,0,0.12); }}
.plan-preview-panel strong {{ font-size:15px; }}
.plan-preview-panel span {{ color:rgba(255,255,255,0.72); font-size:13px; }}
.plan-preview-buttons {{ display:flex; gap:8px; flex-wrap:wrap; }}
.plan-preview-btn {{ border:1px solid rgba(255,255,255,0.35); background:rgba(255,255,255,0.10); color:#fff; border-radius:999px; padding:9px 14px; font-weight:900; cursor:pointer; margin:0; }}
.plan-preview-btn.active {{ background:#fff; color:#111; }}
.pro-feature {{ position:relative; }}
body[data-plan-preview="free"] .pro-feature {{ opacity:.46; filter:grayscale(0.2); }}
body[data-plan-preview="free"] .pro-feature input,
body[data-plan-preview="free"] .pro-feature select,
body[data-plan-preview="free"] .pro-feature button {{ pointer-events:none; }}
body[data-plan-preview="free"] .pro-feature::after {{ content:"🔒 PRO"; position:absolute; top:8px; right:8px; background:#111; color:#fff; border-radius:999px; padding:5px 9px; font-size:11px; font-weight:900; letter-spacing:.04em; }}
.free-preview-note {{ display:none; background:#fff7e6; color:#6b4300; border:1px solid #f0d399; border-radius:12px; padding:10px 12px; font-size:13px; font-weight:800; margin-bottom:12px; }}
body[data-plan-preview="free"] .free-preview-note {{ display:block; }}
.editor-modal-overlay {{ display:none; position:fixed; inset:0; z-index:999999; background:rgba(0,0,0,0.55); align-items:center; justify-content:center; padding:20px; }}
.editor-modal-overlay.show {{ display:flex; }}
.editor-modal-card {{ width:100%; max-width:430px; background:#fff; border-radius:20px; padding:24px; box-shadow:0 24px 70px rgba(0,0,0,0.26); position:relative; }}
.editor-modal-card h2 {{ margin:0 0 10px; font-size:24px; }}
.editor-modal-card p {{ margin:0; color:#444; line-height:1.45; font-size:15px; }}
.editor-modal-close {{ position:absolute; top:12px; right:12px; width:36px; height:36px; border:none; border-radius:999px; background:#f1f1f1; color:#111; font-size:24px; line-height:1; cursor:pointer; }}
.editor-modal-ok {{ width:100%; margin-top:18px; padding:13px; border:none; border-radius:12px; background:#111; color:#fff; font-size:15px; font-weight:900; cursor:pointer; }}
{_app_nav_css()}
@media (max-width: 860px) {{ .builder-grid {{ grid-template-columns:1fr; }} .preview-card {{ position:static; }} .link-edit-row {{ grid-template-columns:1fr; }} .remove-link-btn {{ width:100%; }} }}
</style>
</head>
<body>
{_app_nav_html(username)}
<div class="builder-wrap">
  <div class="builder-head">
    <h1>Create Your BUTTN Profile</h1>
    <p>Set up the page your QR code and NFC button will point to.</p>
  </div>
  <div class="plan-preview-panel" aria-label="Temporary plan preview">
    <div>
      <strong>Temporary Plan Preview</strong><br>
      <span>Switch views while building. This is for testing only and does not change billing.</span>
    </div>
    <div class="plan-preview-buttons">
      <button type="button" class="plan-preview-btn" data-plan-preview-btn="free">Free Mode</button>
      <button type="button" class="plan-preview-btn" data-plan-preview-btn="pro">Pro Mode</button>
    </div>
  </div>
  <div class="builder-grid">
    <form method="post" enctype="multipart/form-data">
      <div class="panel">
        <h2>Profile Identity</h2>
        <div class="field"><label>BUTTN URL</label><input id="buttn_url_input" type="text" name="buttn_url" value="{val('buttn_url', username)}"><div class="small-help">Example: /tshirt-help-desk</div></div>
        <div class="field"><label>Name / Brand</label><input id="name_input" type="text" name="name" value="{html.escape(str(profile.get('name') or _display_name_from_username(username) or ''))}"></div>
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
        <h2>Lead Capture</h2>
        <label class="toggle-row"><input id="lead_capture_enabled_input" type="checkbox" name="lead_capture_enabled" {'checked' if profile.get('lead_capture_enabled') else ''}> Enable Lead Capture</label>
        <div class="field"><label>Headline</label><input id="lead_capture_headline_input" type="text" name="lead_capture_headline" value="{val('lead_capture_headline', 'Stay Connected')}" placeholder="Stay Connected"></div>
        <div class="field"><label>Button Text</label><input id="lead_capture_button_text_input" type="text" name="lead_capture_button_text" value="{val('lead_capture_button_text', 'Submit')}" placeholder="Submit"></div>
        <div class="small-help">When enabled, visitors can leave their name, email, and optional phone number on this profile. No email sending or SMTP needed.</div>
      </div>
      <div class="panel">
        <h2>Featured Spotlight</h2>
        <label class="toggle-row"><input id="spotlight_enabled_input" type="checkbox" name="spotlight_enabled" {'checked' if profile.get('spotlight_enabled') else ''}> Enable Spotlight</label>
        <div class="field">
          <label>Spotlight Image</label>
          <input id="spotlight_image_file_input" type="file" name="spotlight_image_file" accept="image/*">
          <input id="clear_spotlight_image_input" type="hidden" name="clear_spotlight_image" value="0">
          <button type="button" id="clear_spotlight_image_btn" class="secondary-btn">Clear Spotlight Image</button>
          <div class="small-help">Clearing the image lets BUTTN automatically use the link thumbnail when available, or a text-only card.</div>
        </div>
        <div class="field"><label>Headline</label><input id="spotlight_headline_input" type="text" name="spotlight_headline" value="{val('spotlight_headline', '')}" placeholder="New Drop Available"></div>
        <div class="field"><label>Optional Subtext</label><input id="spotlight_subtext_input" type="text" name="spotlight_subtext" value="{val('spotlight_subtext', '')}" placeholder="Tap to shop, book, watch, or learn more."></div>
        <div class="field"><label>Destination Link</label><input id="spotlight_url_input" type="text" name="spotlight_url" value="{val('spotlight_url', '')}" placeholder="https://example.com"></div>
        <div class="free-preview-note">Free Mode preview: Spotlight uses your uploaded image plus a simple clickable destination link. Automatic thumbnails, video playback, autoplay, and advanced video controls are Pro features.</div>
        <div class="field">
          <label>Media Shape</label>
          <select id="spotlight_media_shape_input" name="spotlight_media_shape">
            <option value="vertical" {'selected' if _normalize_spotlight_media_shape(profile.get('spotlight_media_shape')) == 'vertical' else ''}>Vertical / Mobile (9:16)</option>
            <option value="landscape" {'selected' if _normalize_spotlight_media_shape(profile.get('spotlight_media_shape')) == 'landscape' else ''}>Landscape / Standard Video (16:9)</option>
            <option value="square" {'selected' if _normalize_spotlight_media_shape(profile.get('spotlight_media_shape')) == 'square' else ''}>Square (1:1)</option>
          </select>
        </div>
        <div class="pro-feature" data-pro-feature="video-spotlight">
          <div class="field">
            <label>Click Behavior</label>
            <select id="spotlight_open_behavior_input" name="spotlight_open_behavior">
              <option value="new_tab" {'selected' if _normalize_spotlight_open_behavior(profile.get('spotlight_open_behavior')) == 'new_tab' else ''}>Open in New Tab</option>
              <option value="same_page" {'selected' if _normalize_spotlight_open_behavior(profile.get('spotlight_open_behavior')) == 'same_page' else ''}>Open on Same Page</option>
              <option value="play_page" {'selected' if _normalize_spotlight_open_behavior(profile.get('spotlight_open_behavior')) == 'play_page' else ''}>Play Inside Spotlight Card</option>
            </select>
          </div>
          <label class="toggle-row"><input id="spotlight_show_play_input" type="checkbox" name="spotlight_show_play" {'checked' if profile.get('spotlight_show_play') else ''}> Show Play Button Overlay</label>
          <label class="toggle-row"><input id="spotlight_autoplay_input" type="checkbox" name="spotlight_autoplay" {'checked' if profile.get('spotlight_autoplay') else ''}> Autoplay Inside Spotlight Card</label>
          <div class="small-help">Autoplay works best with Play on Page and supported embeds. Vertical is best for TikTok, Instagram Reels, Snapchat, and YouTube Shorts. Landscape is best for standard YouTube. Play Inside Spotlight Card keeps visitors inside the BUTTN profile when the platform allows embedding.</div>
        </div>
        <div id="instagram_autoplay_notice_modal" class="editor-modal-overlay" aria-hidden="true">
          <div class="editor-modal-card">
            <button type="button" id="instagram_autoplay_notice_close" class="editor-modal-close" aria-label="Close">×</button>
            <h2>Instagram Notice</h2>
            <p>Instagram may restrict autoplay and embedded playback on some devices and browsers. Your Spotlight will still work, but autoplay behavior may vary.</p>
            <button type="button" id="instagram_autoplay_notice_ok" class="editor-modal-ok">Got It</button>
          </div>
        </div>
      </div>
      <div class="panel">
        <h2>Header Text & Contact Button</h2>
        <div class="color-grid">
          <div class="field"><label>Name Text</label><input id="header_name_color_input" type="color" name="header_name_color" value="{val('header_name_color', '#111111')}"></div>
          <div class="field"><label>Title Text</label><input id="header_title_color_input" type="color" name="header_title_color" value="{val('header_title_color', '#555555')}"></div>
          <div class="field"><label>Contact Button</label><input id="action_bg_color_input" type="color" name="action_bg_color" value="{val('action_bg_color', '#ffffff')}"></div>
          <div class="field"><label>Contact Button Text</label><input id="action_text_color_input" type="color" name="action_text_color" value="{val('action_text_color', '#111111')}"></div>
          <div class="field"><label>Contact Button Border</label><input id="action_border_color_input" type="color" name="action_border_color" value="{val('action_border_color', '#d8dde6')}"></div>
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
      <div class="preview-note">Live Preview (updates instantly) — Your BUTTN URL: <strong id="current_public_url">https://mybuttn.com/{html.escape(username)}</strong></div>
      <div id="live_buttn_preview" style="width:100%; min-height:680px; background:#ffffff;"></div>
    </div>
  </div>
</div>
<script>
const existingLogoData = {json.dumps(profile.get("logo_b64", ""))};
const existingHeaderImageData = {json.dumps(profile.get("header_image_b64", ""))};
const existingSpotlightImageData = {json.dumps(profile.get("spotlight_image_b64", ""))};
const iconOptions = {json.dumps({key: {"label": data["label"], "svg": SVG_ICON_MAP.get(key, "✦")} for key, data in LINK_ICON_MAP.items()})};
let liveLogoData = existingLogoData;
let liveHeaderImageData = existingHeaderImageData;
let liveSpotlightImageData = existingSpotlightImageData;

function getCurrentPlanPreview() {{
    return document.body.getAttribute("data-plan-preview") || "pro";
}}
function setPlanPreview(mode) {{
    const cleanMode = mode === "free" ? "free" : "pro";
    document.body.setAttribute("data-plan-preview", cleanMode);
    try {{ localStorage.setItem("buttn_plan_preview_mode", cleanMode); }} catch (err) {{}}
    document.querySelectorAll("[data-plan-preview-btn]").forEach(function(btn) {{
        btn.classList.toggle("active", btn.getAttribute("data-plan-preview-btn") === cleanMode);
    }});
    renderLivePreview();
}}
function initPlanPreview() {{
    let saved = "pro";
    try {{ saved = localStorage.getItem("buttn_plan_preview_mode") || "pro"; }} catch (err) {{}}
    setPlanPreview(saved === "free" ? "free" : "pro");
    document.querySelectorAll("[data-plan-preview-btn]").forEach(function(btn) {{
        btn.addEventListener("click", function() {{
            setPlanPreview(btn.getAttribute("data-plan-preview-btn"));
        }});
    }});
}}

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
function spotlightThumbnailProxyUrl(value) {{
    const href = safeUrl(value);
    if (!href || href.startsWith("mailto:") || href.startsWith("tel:")) return "";
    return "/api/spotlight-thumbnail-image?url=" + encodeURIComponent(href);
}}
let instagramSpotlightNoticeShown = false;
function isInstagramUrl(value) {{
    return /(^|\.)instagram\.com/i.test(String(value || ""));
}}
function showInstagramAutoplayNotice() {{
    const modal = getEl("instagram_autoplay_notice_modal");
    if (!modal) {{
        alert("Instagram may restrict autoplay and embedded playback on some devices and browsers. Your Spotlight will still work, but autoplay behavior may vary.");
        return;
    }}
    modal.classList.add("show");
    modal.setAttribute("aria-hidden", "false");
}}
function hideInstagramAutoplayNotice() {{
    const modal = getEl("instagram_autoplay_notice_modal");
    if (!modal) return;
    modal.classList.remove("show");
    modal.setAttribute("aria-hidden", "true");
}}
function maybeShowInstagramAutoplayNotice() {{
    const urlInput = getEl("spotlight_url_input");
    const url = urlInput ? urlInput.value : "";
    if (!instagramSpotlightNoticeShown && isInstagramUrl(url)) {{
        instagramSpotlightNoticeShown = true;
        showInstagramAutoplayNotice();
    }}
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
function normalizeButtnSlug(value) {{
    let v = String(value || "").trim().toLowerCase().replace(/\s+/g, "-");
    v = v.replace(/[^a-z0-9-]/g, "");
    while (v.includes("--")) v = v.replace(/--/g, "-");
    return v.replace(/^-+|-+$/g, "");
}}
function updateCurrentPublicUrl() {{
    const urlInput = getEl("buttn_url_input");
    const urlDisplay = getEl("current_public_url");
    if (!urlDisplay) return;
    const slug = normalizeButtnSlug(urlInput ? urlInput.value : {json.dumps(username)});
    urlDisplay.textContent = "https://mybuttn.com/" + (slug || {json.dumps(username)});
}}

function renderLivePreview() {{
    updateCurrentPublicUrl();
    const root = getEl("live_buttn_preview");
    if (!root) return;

    const defaultDisplayName = {json.dumps(_display_name_from_username(username) or "Your Name")};
    const name = getVal("name_input", defaultDisplayName);
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
    const leadCaptureEnabled = !!(getEl("lead_capture_enabled_input") && getEl("lead_capture_enabled_input").checked);
    const leadHeadline = getVal("lead_capture_headline_input", "Stay Connected");
    const leadButtonText = getVal("lead_capture_button_text_input", "Submit");
    const spotlightEnabled = !!(getEl("spotlight_enabled_input") && getEl("spotlight_enabled_input").checked);
    const spotlightHeadline = getVal("spotlight_headline_input", "");
    const spotlightSubtext = getVal("spotlight_subtext_input", "");
    const spotlightUrl = getVal("spotlight_url_input", "");
    const isFreePlanPreview = getCurrentPlanPreview() === "free";
    let spotlightShowPlay = !!(getEl("spotlight_show_play_input") && getEl("spotlight_show_play_input").checked);
    let spotlightAutoplay = !!(getEl("spotlight_autoplay_input") && getEl("spotlight_autoplay_input").checked);
    let spotlightMediaShape = getVal("spotlight_media_shape_input", "vertical");
    let spotlightOpenBehavior = getVal("spotlight_open_behavior_input", "new_tab");
    if (isFreePlanPreview) {{
        spotlightShowPlay = false;
        spotlightAutoplay = false;
        spotlightOpenBehavior = "new_tab";
    }}
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
    actions += '<a class="action-btn" href="#">Contact Info</a>';

    let spotlightHtml = "";
    if (spotlightEnabled && ((spotlightHeadline || "").trim() || liveSpotlightImageData || (spotlightUrl || "").trim())) {{
        const mediaShape = ["vertical", "landscape", "square"].includes(spotlightMediaShape) ? spotlightMediaShape : "vertical";
        const aspect = mediaShape === "landscape" ? "16 / 9" : (mediaShape === "square" ? "1 / 1" : "9 / 16");
        const autoThumbnailUrl = isFreePlanPreview ? "" : spotlightThumbnailProxyUrl(spotlightUrl);
        const spotlightImage = liveSpotlightImageData
            ? `<div class="spotlight-image-wrap spotlight-shape-${{mediaShape}}" style="aspect-ratio:${{aspect}};"><img src="data:image/png;base64,${{liveSpotlightImageData}}" alt="Featured Spotlight">${{spotlightShowPlay ? '<div class="spotlight-play">▶</div>' : ''}}</div>`
            : (autoThumbnailUrl ? `<div class="spotlight-image-wrap spotlight-shape-${{mediaShape}}" style="aspect-ratio:${{aspect}};"><img src="${{escapeHtml(autoThumbnailUrl)}}" alt="Featured Spotlight" onerror="this.closest('.spotlight-image-wrap').style.display='none';">${{spotlightShowPlay ? '<div class="spotlight-play">▶</div>' : ''}}</div>` : "");
        const spotlightSubtextHtml = (spotlightSubtext || "").trim()
            ? `<div class="spotlight-subtext">${{escapeHtml(spotlightSubtext)}}</div>`
            : "";
        const behaviorLabel = isFreePlanPreview ? "Link only" : (spotlightOpenBehavior === "play_page" ? "Plays in card" : (spotlightOpenBehavior === "same_page" ? "Same page" : "New tab"));
        const autoplayLabel = (!isFreePlanPreview && spotlightAutoplay) ? " • Autoplay" : "";
        const spotlightInner = `${{spotlightImage}}<div class="spotlight-copy"><div class="spotlight-kicker">Featured • ${{behaviorLabel}}${{autoplayLabel}}</div><h2>${{escapeHtml(spotlightHeadline || "Featured")}}</h2>${{spotlightSubtextHtml}}</div>`;
        const href = safeUrl(spotlightUrl);
        spotlightHtml = href
            ? `<a class="spotlight-card" href="${{escapeHtml(href)}}" target="${{spotlightOpenBehavior === "same_page" ? "_self" : "_blank"}}" rel="noopener">${{spotlightInner}}</a>`
            : `<div class="spotlight-card">${{spotlightInner}}</div>`;
    }}

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
#live_buttn_preview .spotlight-card {{ display:block; text-decoration:none; color:#111; background:#fff; border:1px solid #dde1e7; border-radius:20px; overflow:hidden; margin-bottom:18px; box-shadow:0 10px 24px rgba(0,0,0,0.08); }}
#live_buttn_preview .spotlight-image-wrap {{ position:relative; width:100%; background:#111; aspect-ratio:9/16; overflow:hidden; }}
#live_buttn_preview .spotlight-shape-landscape {{ aspect-ratio:16/9; }}
#live_buttn_preview .spotlight-shape-square {{ aspect-ratio:1/1; }}
#live_buttn_preview .spotlight-shape-vertical {{ aspect-ratio:9/16; }}
#live_buttn_preview .spotlight-image-wrap img {{ width:100%; height:100%; object-fit:cover; display:block; }}
#live_buttn_preview .spotlight-play {{ position:absolute; left:50%; top:50%; transform:translate(-50%,-50%); width:62px; height:62px; border-radius:999px; background:rgba(0,0,0,0.72); color:#fff; display:flex; align-items:center; justify-content:center; font-size:28px; padding-left:4px; box-shadow:0 8px 24px rgba(0,0,0,0.28); }}
#live_buttn_preview .spotlight-copy {{ padding:16px; }}
#live_buttn_preview .spotlight-kicker {{ font-size:12px; text-transform:uppercase; letter-spacing:.08em; font-weight:900; color:#777; margin-bottom:6px; }}
#live_buttn_preview .spotlight-copy h2 {{ margin:0; font-size:21px; line-height:1.15; }}
#live_buttn_preview .spotlight-subtext {{ margin-top:8px; color:#555; font-size:14px; line-height:1.4; }}
#live_buttn_preview .lead-capture-card {{ background:#fff; border:1px solid #dde1e7; border-radius:18px; padding:18px; margin-top:18px; box-shadow:0 8px 18px rgba(0,0,0,0.04); }}
#live_buttn_preview .lead-capture-card h2 {{ margin:0 0 12px; font-size:20px; text-align:center; }}
#live_buttn_preview .lead-capture-card input {{ width:100%; box-sizing:border-box; padding:13px; border:1px solid #cfd5df; border-radius:12px; font-size:15px; margin-bottom:10px; }}
#live_buttn_preview .lead-capture-card button {{ width:100%; border:none; border-radius:14px; padding:14px; background:#111; color:#fff; font-size:16px; font-weight:900; cursor:pointer; }}
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
  <div class="links-area">${{spotlightHtml}}${{collectLinks()}}${{leadCaptureEnabled ? `<div class="lead-capture-card"><h2>${{escapeHtml(leadHeadline)}}</h2><form><input type="text" placeholder="Your name"><input type="email" placeholder="Your email" required><input type="tel" placeholder="Phone (optional)"><button type="button">${{escapeHtml(leadButtonText)}}</button></form></div>` : ""}}</div>
  <div class="buttn-footer">Powered by {_buttn_logo_html("black", "buttn-footer-logo")}</div>
</div>`;
}}

[
 "buttn_url_input", "name_input", "title_input", "phone_input", "email_input",
 "header_bg_color_input", "header_image_opacity_input", "page_bg_color_input",
 "link_bg_color_input", "link_text_color_input", "link_border_color_input",
 "header_name_color_input", "header_title_color_input", "action_bg_color_input",
 "action_text_color_input", "action_border_color_input",
 "lead_capture_headline_input", "lead_capture_button_text_input",
 "spotlight_headline_input", "spotlight_subtext_input", "spotlight_url_input",
 "spotlight_media_shape_input", "spotlight_open_behavior_input"
].forEach(function(id) {{
    const el = getEl(id);
    if (el) el.addEventListener("input", renderLivePreview);
}});
const leadCaptureEnabledInput = getEl("lead_capture_enabled_input");
if (leadCaptureEnabledInput) {{
    leadCaptureEnabledInput.addEventListener("change", renderLivePreview);
}}
const spotlightEnabledInput = getEl("spotlight_enabled_input");
if (spotlightEnabledInput) {{
    spotlightEnabledInput.addEventListener("change", renderLivePreview);
}}
const spotlightPlayInput = getEl("spotlight_show_play_input");
if (spotlightPlayInput) {{
    spotlightPlayInput.addEventListener("change", renderLivePreview);
}}
const spotlightAutoplayInput = getEl("spotlight_autoplay_input");
if (spotlightAutoplayInput) {{
    spotlightAutoplayInput.addEventListener("change", function() {{
        renderLivePreview();
        maybeShowInstagramAutoplayNotice();
    }});
}}
const spotlightUrlInputForNotice = getEl("spotlight_url_input");
if (spotlightUrlInputForNotice) {{
    ["input", "paste", "change"].forEach(function(evtName) {{
        spotlightUrlInputForNotice.addEventListener(evtName, function() {{
            window.setTimeout(maybeShowInstagramAutoplayNotice, 0);
        }});
    }});
}}
const instagramNoticeClose = getEl("instagram_autoplay_notice_close");
const instagramNoticeOk = getEl("instagram_autoplay_notice_ok");
const instagramNoticeModal = getEl("instagram_autoplay_notice_modal");
if (instagramNoticeClose) instagramNoticeClose.addEventListener("click", hideInstagramAutoplayNotice);
if (instagramNoticeOk) instagramNoticeOk.addEventListener("click", hideInstagramAutoplayNotice);
if (instagramNoticeModal) {{
    instagramNoticeModal.addEventListener("click", function(e) {{
        if (e.target === instagramNoticeModal) hideInstagramAutoplayNotice();
    }});
}}
const spotlightMediaShapeInput = getEl("spotlight_media_shape_input");
if (spotlightMediaShapeInput) {{
    spotlightMediaShapeInput.addEventListener("change", renderLivePreview);
}}
const spotlightOpenBehaviorInput = getEl("spotlight_open_behavior_input");
if (spotlightOpenBehaviorInput) {{
    spotlightOpenBehaviorInput.addEventListener("change", renderLivePreview);
}}
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
const spotlightImageInput = getEl("spotlight_image_file_input");
const clearSpotlightImageInput = getEl("clear_spotlight_image_input");
const clearSpotlightImageBtn = getEl("clear_spotlight_image_btn");
if (logoInput) logoInput.addEventListener("change", function() {{ readImageFile(logoInput, function(data) {{ liveLogoData = data; }}); }});
if (headerInput) headerInput.addEventListener("change", function() {{ readImageFile(headerInput, function(data) {{ liveHeaderImageData = data; }}); }});
if (spotlightImageInput) spotlightImageInput.addEventListener("change", function() {{
    if (clearSpotlightImageInput) clearSpotlightImageInput.value = "0";
    readImageFile(spotlightImageInput, function(data) {{ liveSpotlightImageData = data; }});
}});
if (clearSpotlightImageBtn) {{
    clearSpotlightImageBtn.addEventListener("click", function() {{
        liveSpotlightImageData = "";
        if (spotlightImageInput) spotlightImageInput.value = "";
        if (clearSpotlightImageInput) clearSpotlightImageInput.value = "1";
        renderLivePreview();
    }});
}}
initPlanPreview();
</script>
</body>
</html>
"""


@app.route("/buttn/edit/test", methods=["GET", "POST"])
def buttn_edit_test_alias():
    return buttn_edit_profile("test")
