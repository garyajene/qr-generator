from flask import Flask, request, redirect, session, Response
from io import BytesIO, StringIO
import csv
import base64
import math
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


DARK_QR_BACKGROUND_LUMINANCE = 0.10


def should_use_light_on_dark_qr(bg_color):
    """Return True when a branded QR needs the high-contrast dark renderer."""
    return relative_luminance(bg_color) <= DARK_QR_BACKGROUND_LUMINANCE


def adaptive_qr_module_color(bg_color):
    """Choose the QR module color for the selected background.

    Light and medium backgrounds keep the existing pure black modules. For
    sufficiently dark backgrounds, choose the darkest neutral gray that gives
    phone cameras a small luminance separation from the background while still
    reading visually as black.
    """
    bg_luminance = relative_luminance(bg_color)

    if not should_use_light_on_dark_qr(bg_color):
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


# Keep artwork classification in step with the tolerance used by the existing
# edge/background recoloring pipeline in the preview.
ARTWORK_BACKGROUND_TOLERANCE = 42
ARTWORK_CLUSTER_TOLERANCE = 52
ARTWORK_MIN_CLUSTER_FRACTION = 0.035
FINDER_PUPIL_MIN_CONTRAST = 3.0


def extract_artwork_colors(art, background_color, limit=3):
    """Return the largest meaningful sampled non-background color regions.

    The background has already been selected by ``choose_background_color``.
    Work from a modest, regular grid of local samples so large regions, rather
    than image resolution or isolated pixels, determine the result.
    """
    if not art:
        return []

    test = art.convert("RGBA").resize((300, 300), Image.LANCZOS)
    grid_size = 30
    sample_radius = 2
    samples = []
    for row in range(grid_size):
        y = int((row + 0.5) * test.height / grid_size)
        for column in range(grid_size):
            x = int((column + 0.5) * test.width / grid_size)
            color = sample_region_average(test, x, y, radius=sample_radius)
            if color_distance(color, background_color) > ARTWORK_BACKGROUND_TOLERANCE:
                samples.append(color)

    if not samples:
        return []

    # Online clustering lets close antialiasing, shading, and compression
    # variants contribute to one representative artwork color.
    clusters = []
    for color in samples:
        closest = None
        closest_distance = None
        for cluster in clusters:
            distance = color_distance(color, cluster["center"])
            if closest_distance is None or distance < closest_distance:
                closest = cluster
                closest_distance = distance

        if closest is not None and closest_distance <= ARTWORK_CLUSTER_TOLERANCE:
            closest["count"] += 1
            count = closest["count"]
            closest["center"] = tuple(
                int(round((closest["center"][channel] * (count - 1) + color[channel]) / count))
                for channel in range(3)
            )
        else:
            clusters.append({"center": color, "count": 1})

    minimum_count = max(3, math.ceil(len(samples) * ARTWORK_MIN_CLUSTER_FRACTION))
    meaningful = [cluster for cluster in clusters if cluster["count"] >= minimum_count]
    meaningful.sort(key=lambda cluster: cluster["count"], reverse=True)
    return [cluster["center"] for cluster in meaningful[:limit]]


def choose_finder_pupil_colors(
    art,
    background_color,
    pupil_count=3,
    fallback_color=(255, 255, 255),
    surrounding_color=None,
):
    """Choose contrasting artwork accents for finder pupils.

    Prefer chromatic candidates when the artwork contains them, then cycle the
    qualifying palette across the three pupils. Neutral artwork keeps the
    high-contrast finder color supplied by the renderer.
    """
    candidates = extract_artwork_colors(art, background_color)
    surrounding_color = surrounding_color or background_color
    surrounding_luminance = relative_luminance(surrounding_color)
    contrasting = [
        color for color in candidates
        if contrast_ratio(relative_luminance(color), surrounding_luminance)
        >= FINDER_PUPIL_MIN_CONTRAST
    ]
    chromatic = [color for color in contrasting if max(color) - min(color) >= 30]
    if chromatic:
        contrasting = chromatic
    if not contrasting:
        return [(*fallback_color, 255)] * pupil_count
    return [(*contrasting[index % len(contrasting)], 255) for index in range(pupil_count)]


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


FINDER_SUPERELLIPSE_EXPONENT = 5.0
FINDER_SUPERSAMPLE = 4


def draw_superellipse(draw, bounds, fill):
    """Draw a clipped, antialiased superellipse without crossing its bounds."""
    left, top, right, bottom = [int(value) for value in bounds]
    width = right - left
    height = bottom - top
    scale = FINDER_SUPERSAMPLE
    mask = Image.new("L", (width * scale, height * scale), 0)
    mask_draw = ImageDraw.Draw(mask)
    center_x = width * scale / 2.0
    center_y = height * scale / 2.0
    radius_x = width * scale / 2.0
    radius_y = height * scale / 2.0
    points = []

    # |x/a|^n + |y/b|^n = 1. The same exponent is used for every
    # finder layer so the outer ring, white ring, and pupil stay concentric.
    for degree in range(360):
        angle = math.radians(degree)
        cos_value = math.cos(angle)
        sin_value = math.sin(angle)
        denominator = (
            abs(cos_value) ** FINDER_SUPERELLIPSE_EXPONENT
            + abs(sin_value) ** FINDER_SUPERELLIPSE_EXPONENT
        ) ** (1.0 / FINDER_SUPERELLIPSE_EXPONENT)
        points.append((
            center_x + radius_x * cos_value / denominator,
            center_y + radius_y * sin_value / denominator,
        ))

    mask_draw.polygon(points, fill=255)
    mask = mask.resize((width, height), Image.LANCZOS)
    draw.bitmap((left, top), mask, fill=fill)


def draw_finder_patterns(draw, matrix_size, outer_color, middle_color, pupil_colors=None):
    """Replace only the three 7x7 finder footprints with 7/5/3 squircles."""
    starts = ((0, 0), (matrix_size - 7, 0), (0, matrix_size - 7))
    for finder_index, (column, row) in enumerate(starts):
        left = (QUIET + column) * BOX
        top = (QUIET + row) * BOX

        # Clear only the original 7x7 footprint. This prevents artwork or the
        # square Segno finder underneath from showing through rounded corners.
        draw.rectangle(
            [left, top, left + 7 * BOX - 1, top + 7 * BOX - 1],
            fill=middle_color,
        )
        draw_superellipse(
            draw,
            (left, top, left + 7 * BOX, top + 7 * BOX),
            outer_color,
        )
        draw_superellipse(
            draw,
            (left + BOX, top + BOX, left + 6 * BOX, top + 6 * BOX),
            middle_color,
        )
        draw_superellipse(
            draw,
            (left + 2 * BOX, top + 2 * BOX, left + 5 * BOX, top + 5 * BOX),
            pupil_colors[finder_index] if pupil_colors else outer_color,
        )


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
    light_on_dark = should_use_light_on_dark_qr(bg_color)
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

    # Version C from the diagnostic lab: larger dots are more resistant to
    # camera blur and resizing. Apply it only to backgrounds classified as
    # dark; light and medium branded QR output stays byte-for-byte unchanged.
    if light_on_dark:
        dot_scale = max(0.20, min(0.95, dot_scale + 0.14))

    def draw_dot(x0, y0, x1, y1, scale, color):
        pad = (1.0 - scale) * BOX / 2.0
        draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=color)

    for r in range(n):
        for c in range(n):
            x0 = (QUIET + c) * BOX
            y0 = (QUIET + r) * BOX
            x1 = x0 + BOX
            y1 = y0 + BOX

            if light_on_dark:
                # Deliberately invert the QR polarity: Segno signal modules
                # become white, while opposite modules use the selected dark
                # ground. Protected structures are solid so finder,
                # separator, timing, format, and alignment geometry remains
                # exact. Ordinary modules stay dotted to preserve branding.
                inverted_fill = (*light_color, 255) if matrix[r][c] else (*bg_color, 255)
                if is_protected(r, c, n, version):
                    draw.rectangle([x0, y0, x1, y1], fill=inverted_fill)
                else:
                    draw_dot(x0, y0, x1, y1, dot_scale, inverted_fill)
                continue

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

    if light_on_dark:
        pupil_colors = choose_finder_pupil_colors(
            art, bg_color, fallback_color=light_color
        ) if art else None
        draw_finder_patterns(
            draw, n, (*light_color, 255), (*bg_color, 255), pupil_colors
        )
    else:
        pupil_colors = choose_finder_pupil_colors(
            art, bg_color, fallback_color=dark_color, surrounding_color=light_color
        ) if art else None
        draw_finder_patterns(
            draw, n, (*dark_color, 255), (*light_color, 255), pupil_colors
        )

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

    if render_mode == "light_on_dark":
        pupil_colors = choose_finder_pupil_colors(
            art, bg_color, fallback_color=light_color
        ) if art else None
        draw_finder_patterns(
            draw, n, (*light_color, 255), (*bg_color, 255), pupil_colors
        )
    else:
        pupil_colors = choose_finder_pupil_colors(
            art, bg_color, fallback_color=dark_color, surrounding_color=light_color
        ) if art else None
        draw_finder_patterns(
            draw, n, (*dark_color, 255), (*light_color, 255), pupil_colors
        )

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
    draw_finder_patterns(
        draw, len(qr.matrix), (0, 0, 0, 255), (255, 255, 255, 255)
    )

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


def render_landing_page():
    """
    Public BUTTN marketing homepage.

    This keeps the first landing-page version inside app.py so the deployment
    stays simple: one full replacement file, no extra template files required.
    """
    if session.get("user_id"):
        primary_cta = '<a class="btn btn-primary" href="/account">Go To My Account</a>'
        secondary_cta = '<a class="btn btn-secondary" href="/generate">Open QR Generator</a>'
        nav_cta = '<a class="nav-pill nav-primary" href="/account">My Account</a>'
    else:
        primary_cta = '<a class="btn btn-primary" href="/register">Create Your BUTTN</a>'
        secondary_cta = '<a class="btn btn-secondary" href="/login">Log In</a>'
        nav_cta = '<a class="nav-pill nav-primary" href="/register">Get Started</a>'

    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BUTTN | Tap. Scan. Connect.</title>
<style>
* {{ box-sizing: border-box; }}
body {{
    margin: 0;
    font-family: Arial, sans-serif;
    background: #f6f4ef;
    color: #111;
}}
a {{ color: inherit; }}
.page {{
    min-height: 100vh;
}}
.top-nav {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
    max-width: 1180px;
    margin: 0 auto;
    padding: 22px 24px;
}}
.brand {{
    display: inline-flex;
    align-items: center;
    gap: 10px;
    text-decoration: none;
    font-weight: 900;
    letter-spacing: -0.03em;
}}
.brand img {{
    height: 36px;
    width: auto;
    display: block;
}}
.nav-links {{
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
}}
.nav-pill {{
    text-decoration: none;
    font-size: 14px;
    font-weight: 900;
    padding: 10px 14px;
    border-radius: 999px;
    border: 1px solid rgba(0,0,0,0.12);
    background: rgba(255,255,255,0.6);
}}
.nav-primary {{
    background: #111;
    color: #fff;
    border-color: #111;
}}
.hero {{
    max-width: 1180px;
    margin: 0 auto;
    padding: 42px 24px 70px;
    display: grid;
    grid-template-columns: minmax(0, 1.05fr) minmax(320px, 0.95fr);
    gap: 46px;
    align-items: center;
}}
.kicker {{
    display: inline-flex;
    align-items: center;
    border: 1px solid rgba(0,0,0,0.12);
    background: rgba(255,255,255,0.72);
    border-radius: 999px;
    padding: 9px 13px;
    font-weight: 900;
    font-size: 13px;
    margin-bottom: 18px;
}}
h1 {{
    margin: 0;
    font-size: clamp(46px, 7vw, 88px);
    line-height: 0.9;
    letter-spacing: -0.075em;
}}
.hero-copy {{
    margin: 22px 0 0;
    max-width: 610px;
    font-size: 20px;
    line-height: 1.45;
    color: #444;
}}
.cta-row {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-top: 30px;
}}
.btn {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 52px;
    padding: 15px 22px;
    border-radius: 999px;
    text-decoration: none;
    font-weight: 900;
    border: 2px solid #111;
}}
.btn-primary {{
    background: #111;
    color: #fff;
}}
.btn-secondary {{
    background: #fff;
    color: #111;
}}
.trust-row {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 26px;
    color: #555;
    font-size: 14px;
    font-weight: 800;
}}
.trust-row span {{
    background: rgba(255,255,255,0.7);
    border: 1px solid rgba(0,0,0,0.08);
    border-radius: 999px;
    padding: 8px 11px;
}}
.product-stage {{
    position: relative;
    min-height: 520px;
}}
.phone-card {{
    position: relative;
    max-width: 360px;
    margin: 0 auto;
    background: #111;
    color: #fff;
    border-radius: 34px;
    padding: 18px;
    box-shadow: 0 30px 80px rgba(0,0,0,0.22);
}}
.phone-screen {{
    background: #f5f5f5;
    border-radius: 24px;
    overflow: hidden;
    color: #111;
}}
.profile-top {{
    min-height: 230px;
    background: linear-gradient(135deg, #111, #8d5b4c);
    padding: 38px 22px 24px;
    text-align: center;
    color: #fff;
}}
.avatar {{
    width: 104px;
    height: 104px;
    margin: 0 auto 14px;
    border-radius: 999px;
    background: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    border: 4px solid rgba(255,255,255,0.75);
}}
.avatar img {{
    max-width: 78px;
    width: 78px;
    height: auto;
}}
.profile-name {{
    font-size: 23px;
    font-weight: 900;
}}
.profile-sub {{
    margin-top: 5px;
    font-size: 14px;
    opacity: 0.78;
}}
.link-stack {{
    padding: 22px 18px 24px;
}}
.fake-link {{
    background: #fff;
    border: 2px solid #e1e4ea;
    border-radius: 15px;
    padding: 14px;
    margin-bottom: 12px;
    text-align: center;
    font-weight: 900;
}}
.qr-card {{
    position: absolute;
    left: 0;
    bottom: 16px;
    width: 170px;
    background: #fff;
    border: 1px solid rgba(0,0,0,0.1);
    border-radius: 22px;
    padding: 16px;
    box-shadow: 0 18px 48px rgba(0,0,0,0.18);
    transform: rotate(-5deg);
}}
.qr-grid {{
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 5px;
}}
.qr-grid i {{
    aspect-ratio: 1;
    border-radius: 3px;
    background: #111;
}}
.qr-grid i:nth-child(3n) {{
    background: transparent;
}}
.dome-card {{
    position: absolute;
    right: 4px;
    top: 26px;
    width: 146px;
    height: 146px;
    border-radius: 999px;
    background: radial-gradient(circle at 32% 25%, #fff, #111 38%, #000 72%);
    box-shadow: 0 18px 50px rgba(0,0,0,0.20);
    border: 8px solid #fff;
}}
.sections {{
    background: #fff;
    border-top: 1px solid rgba(0,0,0,0.08);
}}
.section-inner {{
    max-width: 1180px;
    margin: 0 auto;
    padding: 66px 24px;
}}
.section-title {{
    margin: 0 0 14px;
    font-size: clamp(32px, 4vw, 52px);
    line-height: 1;
    letter-spacing: -0.05em;
}}
.section-copy {{
    margin: 0;
    max-width: 720px;
    color: #555;
    font-size: 18px;
    line-height: 1.45;
}}
.card-grid {{
    display: grid;
    grid-template-columns: repeat(3, minmax(0,1fr));
    gap: 16px;
    margin-top: 30px;
}}
.info-card {{
    background: #f6f4ef;
    border: 1px solid rgba(0,0,0,0.08);
    border-radius: 22px;
    padding: 24px;
}}
.info-card h3 {{
    margin: 0 0 9px;
    font-size: 22px;
}}
.info-card p {{
    margin: 0;
    color: #555;
    line-height: 1.45;
}}
.final-cta {{
    background: #111;
    color: #fff;
    text-align: center;
}}
.final-cta .section-inner {{
    padding-top: 70px;
    padding-bottom: 76px;
}}
.final-cta p {{
    color: rgba(255,255,255,0.72);
    margin-left: auto;
    margin-right: auto;
}}
.final-cta .btn-secondary {{
    border-color: #fff;
}}
@media (max-width: 860px) {{
    .top-nav {{
        align-items: flex-start;
        flex-direction: column;
    }}
    .hero {{
        grid-template-columns: 1fr;
        padding-top: 24px;
    }}
    .product-stage {{
        min-height: 470px;
    }}
    .qr-card {{
        left: 8px;
        bottom: 0;
    }}
    .dome-card {{
        right: 12px;
    }}
    .card-grid {{
        grid-template-columns: 1fr;
    }}
}}
</style>
</head>
<body>
<div class="page">
    <header class="top-nav">
        <a class="brand" href="/" aria-label="BUTTN Home">{_buttn_logo_html("black", "buttn-landing-logo")}</a>
        <nav class="nav-links">
            <a class="nav-pill" href="/login">Log In</a>
            <a class="nav-pill" href="/generate">QR Generator</a>
            {nav_cta}
        </nav>
    </header>

    <main class="hero">
        <section>
            <div class="kicker">QR + NFC + link page for modern brands</div>
            <h1>Tap. Scan. Connect. Instantly.</h1>
            <p class="hero-copy">BUTTN turns your QR code and NFC button into a simple branded profile page for your business, content, products, links, leads, and contact info.</p>
            <div class="cta-row">
                {primary_cta}
                {secondary_cta}
            </div>
            <div class="trust-row">
                <span>No app needed</span>
                <span>Custom profile URL</span>
                <span>QR + NFC ready</span>
            </div>
        </section>

        <section class="product-stage" aria-label="BUTTN product preview">
            <div class="dome-card"></div>
            <div class="phone-card">
                <div class="phone-screen">
                    <div class="profile-top">
                        <div class="avatar">{_buttn_logo_html("black", "buttn-avatar-logo")}</div>
                        <div class="profile-name">Your Brand</div>
                        <div class="profile-sub">mybuttn.com/yourbrand</div>
                    </div>
                    <div class="link-stack">
                        <div class="fake-link">Shop New Drop</div>
                        <div class="fake-link">Watch Video</div>
                        <div class="fake-link">Book A Call</div>
                    </div>
                </div>
            </div>
            <div class="qr-card" aria-hidden="true">
                <div class="qr-grid">
                    {''.join('<i></i>' for _ in range(49))}
                </div>
            </div>
        </section>
    </main>

    <section class="sections">
        <div class="section-inner">
            <h2 class="section-title">One page for every scan.</h2>
            <p class="section-copy">Your customer taps the BUTTN or scans the QR code. They land on your branded page. From there they can shop, follow, book, watch, call, email, or leave their contact info.</p>
            <div class="card-grid">
                <div class="info-card">
                    <h3>For brands</h3>
                    <p>Send people to your store, latest drop, social pages, videos, and offers from one clean profile.</p>
                </div>
                <div class="info-card">
                    <h3>For creators</h3>
                    <p>Put your content, links, bookings, and lead capture in one place without handing out paper cards.</p>
                </div>
                <div class="info-card">
                    <h3>For vendors</h3>
                    <p>Use it at pop-ups, trade shows, vending events, packaging, business cards, and product displays.</p>
                </div>
            </div>
        </div>
    </section>

    <section class="final-cta">
        <div class="section-inner">
            <h2 class="section-title">Build your BUTTN profile first.</h2>
            <p class="section-copy">Then connect the QR code, NFC button, card, sticker, and product experience around it.</p>
            <div class="cta-row" style="justify-content:center;">
                {primary_cta}
                <a class="btn btn-secondary" href="/generate">Try QR Generator</a>
            </div>
        </div>
    </section>
</div>
</body>
</html>
"""


@app.route("/")
def landing_page():
    return render_landing_page()



@app.route("/db-status")
def db_status():
    if engine is None:
        return "Database not connected. DATABASE_URL is missing from this web service.", 500

    try:
        init_database()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "Database connected. BUTTN tables are ready."
    except Exception as exc:
        safe_error = html.escape(str(exc))
        return f"Database connection error: {safe_error}", 500




# -----------------------------
# BUTTN USER ACCOUNT SYSTEM - DATABASE BACKED
# -----------------------------

def _current_user_id():
    return session.get("user_id")


def _current_user_email():
    return session.get("user_email", "")


def _app_nav_css():
    return """
.buttn-admin-nav {
    position: sticky;
    top: 0;
    z-index: 9999;
    background: #111;
    color: #fff;
    border-bottom: 1px solid rgba(255,255,255,0.12);
}
.buttn-admin-nav-inner {
    max-width: 980px;
    margin: 0 auto;
    padding: 10px 18px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
}
.buttn-admin-brand {
    display: inline-flex;
    align-items: center;
    text-decoration: none;
    line-height: 1;
}
.buttn-admin-brand-logo {
    height: 30px;
    width: auto;
    display: block;
}
.buttn-footer-logo {
    height: 18px;
    width: auto;
    display: inline-block;
    vertical-align: middle;
    margin-left: 4px;
}
.buttn-admin-links {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
}
.buttn-admin-links a {
    color: #fff;
    text-decoration: none;
    font-weight: 800;
    font-size: 13px;
    padding: 8px 10px;
    border-radius: 999px;
    background: rgba(255,255,255,0.10);
}
.buttn-admin-links a:hover {
    background: rgba(255,255,255,0.20);
}
@media (max-width: 640px) {
    .buttn-admin-nav-inner {
        align-items: flex-start;
    }
    .buttn-admin-links {
        width: 100%;
    }
    .buttn-admin-links a {
        flex: 1 1 auto;
        text-align: center;
    }
}
"""


def _app_nav_html(username=None):
    user_id = session.get("user_id")
    if not user_id:
        return ""

    username = _normalize_buttn_url(username or "")
    links = [
        '<a href="/account">Account</a>',
        '<a href="/generate">QR Generator</a>',
    ]

    if username:
        owner_id = _db_profile_owner_id(username) if "_db_profile_owner_id" in globals() else None
        if (not owner_id) or owner_id == user_id:
            links.append(f'<a href="/buttn/edit/{html.escape(username)}">Edit Profile</a>')
            links.append(f'<a href="/{html.escape(username)}">View Profile</a>')

    links.append('<a href="/logout">Log Out</a>')

    return f"""
<div class="buttn-admin-nav">
  <div class="buttn-admin-nav-inner">
    <a class="buttn-admin-brand" href="/account" aria-label="BUTTN Home">{_buttn_logo_html("white", "buttn-admin-brand-logo")}</a>
    <div class="buttn-admin-links">{''.join(links)}</div>
  </div>
</div>
"""


def _auth_page(title, message=""):
    safe_title = html.escape(title)
    safe_message = html.escape(message or "")
    message_html = f'<div class="message">{safe_message}</div>' if safe_message else ""
    is_register_page = (title == "Create Account")
    turnstile_script = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>' if is_register_page and TURNSTILE_SITE_KEY else ""
    turnstile_widget = f'<div class="turnstile-wrap"><div class="cf-turnstile" data-sitekey="{html.escape(TURNSTILE_SITE_KEY)}"></div></div>' if is_register_page and TURNSTILE_SITE_KEY else ""
    turnstile_missing = '<div class="message">Turnstile is not configured yet.</div>' if is_register_page and not TURNSTILE_SITE_KEY else ""
    return f"""
<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title} | BUTTN</title>
{turnstile_script}
<style>
body {{ margin:0; font-family:Arial,sans-serif; background:#f3f5f7; color:#111; }}
.auth-wrap {{ max-width:430px; margin:60px auto; background:#fff; border:1px solid #dde1e7; border-radius:18px; padding:26px; box-shadow:0 8px 24px rgba(0,0,0,0.05); }}
h1 {{ margin:0 0 8px; font-size:28px; }} p {{ color:#666; line-height:1.45; }}
.message {{ background:#fff2f2; color:#8a1f1f; border:1px solid #f0caca; padding:12px; border-radius:10px; margin:14px 0; }}
label {{ display:block; font-weight:700; margin:14px 0 7px; }}
input {{ width:100%; box-sizing:border-box; padding:12px; border:1px solid #cfd5df; border-radius:10px; font-size:16px; }}
button {{ width:100%; margin-top:18px; padding:14px; border:none; border-radius:12px; background:#111; color:#fff; font-size:16px; font-weight:800; cursor:pointer; }}
.turnstile-wrap {{ margin-top:16px; display:flex; justify-content:center; }}
.show-pass-row {{ display:flex; align-items:center; gap:8px; margin-top:10px; font-size:14px; font-weight:700; color:#333; }}
.show-pass-row input {{ width:auto; padding:0; margin:0; }}
.nav {{ margin-top:18px; text-align:center; font-size:14px; }} a {{ color:#111; font-weight:800; }}
</style></head><body>
<div class="auth-wrap"><h1>{safe_title}</h1><p>Create or access your BUTTN account.</p>{message_html}{turnstile_missing}
<form method="post"><label>Email</label><input type="email" name="email" required autocomplete="email"><label>Password</label><input id="password_field" type="password" name="password" required autocomplete="current-password"><label class="show-pass-row"><input id="show_password_toggle" type="checkbox"> Show Password</label>{turnstile_widget}<button type="submit">{safe_title}</button></form>
<div class="nav"><a href="/register">Create Account</a> &nbsp; | &nbsp; <a href="/login">Log In</a> &nbsp; | &nbsp; <a href="/account">My Account</a></div>
<script>
const showPasswordToggle = document.getElementById("show_password_toggle");
const passwordField = document.getElementById("password_field");
if (showPasswordToggle && passwordField) {{
    showPasswordToggle.addEventListener("change", function() {{
        passwordField.type = this.checked ? "text" : "password";
    }});
}}
</script>
</div></body></html>
"""


def _dashboard_page(message="", url_message=""):
    user_id = _current_user_id()
    if not user_id:
        return redirect("/login")

    rows = []
    if engine is not None:
        try:
            init_database()
            with engine.connect() as conn:
                rows = conn.execute(text("""
                    SELECT username, name, title, updated_at
                    FROM profiles
                    WHERE user_id = :user_id
                    ORDER BY created_at ASC, id ASC
                """), {"user_id": user_id}).mappings().all()
        except Exception:
            rows = []

    profile_count = len(rows)
    profile_limit = MAX_ACCOUNT_PROFILES
    profile_count_text = f"Profiles: {profile_count} / {profile_limit}"
    plan_status = _current_user_plan_status() if "_current_user_plan_status" in globals() else {"account_type": "free", "trial_days_remaining": 0, "trial_message": ""}
    account_type = plan_status.get("account_type", "free")
    if account_type == "pro":
        plan_label = "BUTTN Pro"
    elif account_type == "trial":
        plan_label = "Pro Trial"
    else:
        plan_label = "Free"
    upgrade_html = "" if account_type == "pro" else '<a class="upgrade-btn" href="/account/upgrade">Upgrade to Pro</a>'
    trial_message = plan_status.get("trial_message", "")
    trial_banner_html = f'<div class="trial-banner">{html.escape(trial_message)}</div>' if trial_message else ""
    is_first_profile = profile_count == 0

    profile_rows = ""
    for idx, row in enumerate(rows, start=1):
        username_raw = row.get("username") or ""
        username = html.escape(username_raw)
        name = html.escape(row.get("name") or _display_name_from_username(username_raw) or "Untitled")
        title = html.escape(row.get("title") or "")
        title_html = f"<br><small>{title}</small>" if title else ""
        profile_rows += f"""
        <div class="profile-row">
            <div class="profile-main">
                <div class="profile-number">{idx}</div>
                <div>
                    <strong>{name}</strong><br>
                    <span>/{username}</span>{title_html}
                </div>
            </div>
            <div class="profile-actions">
                <a href="/buttn/edit/{username}">Edit</a>
                <a href="/{username}" target="_blank">View</a>
                <a href="/account/analytics/{username}">Analytics</a>
                <a href="/account/leads/{username}">Leads ({_get_profile_lead_count(username_raw)})</a>
            </div>
        </div>
        """

    if not profile_rows:
        profile_rows = '<p class="empty">Create your first BUTTN profile to get started.</p>'

    safe_email = html.escape(_current_user_email())
    safe_message = html.escape(message or "")
    safe_url_message = html.escape(url_message or "")
    message_html = f'<div class="message">{safe_message}</div>' if safe_message else ""
    url_message_html = f'<div class="url-message">{safe_url_message}</div>' if safe_url_message else ""
    create_button_text = "Create Profile" if is_first_profile else "+ Create New Profile"
    modal_title = "Create Profile" if is_first_profile else "Create New Profile"
    modal_intro = "Name your first profile and choose your BUTTN URL." if is_first_profile else "Add another profile under this same BUTTN account."
    modal_open_class = " show" if safe_url_message else ""
    create_form_html = f"""
        <button type="button" id="open_create_profile_modal" class="create-profile-btn">{create_button_text}</button>
        {url_message_html}
        <div id="create_profile_modal" class="modal-overlay{modal_open_class}">
            <div class="modal-card">
                <button type="button" id="close_create_profile_modal" class="modal-close">×</button>
                <h2>{modal_title}</h2>
                <p class="muted">{modal_intro}</p>
                <form method="post" action="/account/create-profile">
                    <label>Profile Name</label>
                    <input type="text" name="profile_name" placeholder="Dope Tees" required>
                    <label>BUTTN URL</label>
                    <div class="url-prefix-row">
                        <span>mybuttn.com/</span>
                        <input type="text" name="username" placeholder="dope-tees" required>
                    </div>
                    <button type="submit" class="modal-submit">{modal_title}</button>
                </form>
            </div>
        </div>
    """
    if profile_count >= profile_limit:
        create_form_html = '<div class="limit-message">You have reached the 10 profile limit for this account.</div>'

    return f"""
<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>My BUTTN Account</title>
<style>
body {{ margin:0; font-family:Arial,sans-serif; background:#f3f5f7; color:#111; }} .wrap {{ max-width:760px; margin:40px auto; padding:20px; }}
.card {{ background:#fff; border:1px solid #dde1e7; border-radius:18px; padding:24px; box-shadow:0 8px 24px rgba(0,0,0,0.04); margin-bottom:18px; }}
h1 {{ margin:0 0 6px; }} .muted {{ color:#666; }} .message {{ background:#eefaf0; border:1px solid #c6e8ce; padding:12px; border-radius:10px; margin:14px 0; }}
.profile-row {{ display:flex; justify-content:space-between; gap:16px; border-top:1px solid #eee; padding:18px 0; align-items:center; }} .profile-row:first-child {{ border-top:none; }} .profile-row span {{ color:#555; }}
.profile-main {{ display:flex; align-items:center; gap:14px; }}
.profile-number {{ width:34px; height:34px; border-radius:999px; background:#111; color:#fff; display:flex; align-items:center; justify-content:center; font-weight:900; flex:0 0 34px; }}
.profile-actions {{ display:flex; gap:10px; flex-wrap:wrap; justify-content:flex-end; }}
.profile-actions a {{ background:#f2f4f7; border-radius:999px; padding:8px 11px; text-decoration:none; }}
a {{ color:#111; font-weight:800; }} button, .button {{ display:inline-block; margin-top:12px; padding:12px 16px; border:none; border-radius:12px; background:#111; color:#fff; text-decoration:none; font-weight:800; cursor:pointer; }}
.upgrade-btn {{ display:inline-block; margin-top:12px; margin-left:10px; padding:12px 16px; border-radius:12px; background:#111; color:#fff; text-decoration:none; font-weight:900; }}
.account-danger-footer {{ text-align:center; margin:22px 0 4px; }}
.delete-account-link {{ display:inline-block; border:none; background:transparent; color:#8a1f1f; font-weight:900; text-decoration:underline; cursor:pointer; padding:8px 10px; font-size:14px; font-family:Arial,sans-serif; }}
.delete-account-link:hover {{ color:#5f1010; }}
.delete-modal-card {{ border-top:6px solid #8a1f1f; }}
.danger-title {{ color:#8a1f1f; margin:0 0 8px; }}
.danger-text {{ color:#555; line-height:1.45; }}
.delete-account-btn {{ background:#8a1f1f; color:#fff; width:100%; }}
.cancel-delete-btn {{ background:#f1f1f1; color:#111; width:100%; margin-top:10px; }}
input {{ width:100%; box-sizing:border-box; padding:12px; border:1px solid #cfd5df; border-radius:10px; font-size:16px; }} label {{ display:block; font-weight:700; margin:12px 0 7px; }} .empty {{ color:#666; }}
.url-message, .limit-message {{ margin-top:12px; padding:10px 12px; border-radius:10px; background:#fff2f2; color:#8a1f1f; border:1px solid #f0caca; font-weight:800; }}
.account-stats {{ margin-top:8px; color:#555; font-weight:800; }}
.trial-banner {{ margin-top:14px; padding:12px 14px; border-radius:12px; background:#fff7e6; border:1px solid #ffd38a; color:#5f3b00; font-weight:900; line-height:1.35; }}
.profile-card-head {{ display:flex; justify-content:space-between; gap:14px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }}
.profile-card-head h2 {{ margin:0; }}
.create-profile-btn {{ margin-top:0; }}
.modal-overlay {{ display:none; position:fixed; inset:0; z-index:99999; background:rgba(0,0,0,0.52); align-items:center; justify-content:center; padding:20px; }}
.modal-overlay.show {{ display:flex; }}
.modal-card {{ width:100%; max-width:460px; background:#fff; border-radius:20px; padding:24px; box-shadow:0 24px 70px rgba(0,0,0,0.24); position:relative; }}
.modal-card h2 {{ margin:0 0 8px; }}
.modal-close {{ position:absolute; top:14px; right:14px; width:36px; height:36px; border-radius:999px; padding:0; margin:0; background:#f1f1f1; color:#111; font-size:24px; line-height:1; }}
.url-prefix-row {{ display:flex; align-items:center; border:1px solid #cfd5df; border-radius:10px; overflow:hidden; background:#fff; }}
.url-prefix-row span {{ padding:0 0 0 12px; color:#666; font-weight:800; white-space:nowrap; }}
.url-prefix-row input {{ border:none; border-radius:0; }}
.modal-submit {{ width:100%; padding:14px; }}
{_app_nav_css()}
@media (max-width:640px) {{
    .profile-row {{ align-items:flex-start; flex-direction:column; }}
    .profile-actions {{ justify-content:flex-start; }}
}}
</style></head><body>{_app_nav_html()}<div class="wrap">
  <div class="card"><h1>My BUTTN Account</h1><div class="muted">Signed in as {safe_email}</div><div class="account-stats">Plan: {plan_label} &nbsp; • &nbsp; {profile_count_text}</div>{trial_banner_html}{message_html}<a href="/logout">Log out</a>{upgrade_html}</div>
  <div class="card">
    <div class="profile-card-head"><h2>My Profiles</h2>{create_form_html}</div>
    {profile_rows}
  </div>
  <div class="account-danger-footer">
    <button type="button" id="open_delete_account_modal" class="delete-account-link">Delete Account</button>
  </div>
  <div id="delete_account_modal" class="modal-overlay">
    <div class="modal-card delete-modal-card">
        <button type="button" id="close_delete_account_modal" class="modal-close">×</button>
        <h2 class="danger-title">Delete Account</h2>
        <p class="danger-text">This permanently deletes this account, all profiles, links, analytics, and leads. Type your email to confirm.</p>
        <form method="post" action="/account/delete" onsubmit="return confirm('Delete this BUTTN account permanently? This cannot be undone.');">
            <label>Confirm Email</label>
            <input type="email" name="confirm_email" placeholder="{safe_email}" required>
            <button type="submit" class="delete-account-btn">Delete Account Permanently</button>
            <button type="button" id="cancel_delete_account_modal" class="cancel-delete-btn">Cancel</button>
        </form>
    </div>
  </div>
</div>
<script>
const openCreateProfileModal = document.getElementById("open_create_profile_modal");
const closeCreateProfileModal = document.getElementById("close_create_profile_modal");
const createProfileModal = document.getElementById("create_profile_modal");
const openDeleteAccountModal = document.getElementById("open_delete_account_modal");
const closeDeleteAccountModal = document.getElementById("close_delete_account_modal");
const cancelDeleteAccountModal = document.getElementById("cancel_delete_account_modal");
const deleteAccountModal = document.getElementById("delete_account_modal");
if (openCreateProfileModal && createProfileModal) {{
    openCreateProfileModal.addEventListener("click", function() {{
        createProfileModal.classList.add("show");
    }});
}}
if (closeCreateProfileModal && createProfileModal) {{
    closeCreateProfileModal.addEventListener("click", function() {{
        createProfileModal.classList.remove("show");
    }});
}}
if (createProfileModal) {{
    createProfileModal.addEventListener("click", function(e) {{
        if (e.target === createProfileModal) {{
            createProfileModal.classList.remove("show");
        }}
    }});
}}
if (openDeleteAccountModal && deleteAccountModal) {{
    openDeleteAccountModal.addEventListener("click", function() {{
        deleteAccountModal.classList.add("show");
    }});
}}
if (closeDeleteAccountModal && deleteAccountModal) {{
    closeDeleteAccountModal.addEventListener("click", function() {{
        deleteAccountModal.classList.remove("show");
    }});
}}
if (cancelDeleteAccountModal && deleteAccountModal) {{
    cancelDeleteAccountModal.addEventListener("click", function() {{
        deleteAccountModal.classList.remove("show");
    }});
}}
if (deleteAccountModal) {{
    deleteAccountModal.addEventListener("click", function(e) {{
        if (e.target === deleteAccountModal) {{
            deleteAccountModal.classList.remove("show");
        }}
    }});
}}
</script>
</body></html>
"""


@app.route("/register", methods=["GET", "POST"])
def register():
    if engine is None:
        return _auth_page("Create Account", "Database is not connected yet.")
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        turnstile_token = request.form.get("cf-turnstile-response") or ""
        if not verify_turnstile_response(turnstile_token, request.headers.get("CF-Connecting-IP") or request.remote_addr):
            return _auth_page("Create Account", "Please complete the security check.")
        if not email or not password:
            return _auth_page("Create Account", "Email and password are required.")
        if len(password) < 6:
            return _auth_page("Create Account", "Password must be at least 6 characters.")
        try:
            init_database()
            password_hash = generate_password_hash(password)
            with engine.begin() as conn:
                user = conn.execute(text("""
                    INSERT INTO users (email, password_hash, account_type, trial_started_at, trial_ends_at)
                    VALUES (:email, :password_hash, 'trial', NOW(), NOW() + INTERVAL '30 days')
                    RETURNING id, email
                """), {"email": email, "password_hash": password_hash}).mappings().one()
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            return redirect("/account")
        except Exception as exc:
            msg = str(exc)
            if "duplicate" in msg.lower() or "unique" in msg.lower():
                return _auth_page("Create Account", "That email already has an account. Please log in.")
            return _auth_page("Create Account", "Account could not be created.")
    return _auth_page("Create Account")


@app.route("/login", methods=["GET", "POST"])
def login():
    if engine is None:
        return _auth_page("Log In", "Database is not connected yet.")
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        try:
            init_database()
            with engine.connect() as conn:
                user = conn.execute(text("SELECT id, email, password_hash FROM users WHERE email = :email"), {"email": email}).mappings().first()
            if user and check_password_hash(user["password_hash"], password):
                session["user_id"] = user["id"]
                session["user_email"] = user["email"]
                return redirect("/account")
        except Exception:
            pass
        return _auth_page("Log In", "Email or password is incorrect.")
    return _auth_page("Log In")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/account")
def account_dashboard():
    return _dashboard_page()


@app.route("/account/delete", methods=["POST"])
def account_delete():
    user_id = _current_user_id()
    user_email = (_current_user_email() or "").strip().lower()
    if not user_id:
        return redirect("/login")

    confirm_email = (request.form.get("confirm_email") or "").strip().lower()
    if not confirm_email or confirm_email != user_email:
        return _dashboard_page(message="Account was not deleted. The confirmation email did not match.")

    if engine is None:
        return _dashboard_page(message="Account could not be deleted. Database is not connected.")

    try:
        init_database()
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE id = :user_id"), {"user_id": user_id})
        session.clear()
        return _auth_page("Create Account", "Your BUTTN account has been deleted.")
    except Exception:
        return _dashboard_page(message="Account could not be deleted. Please try again.")



def _normalize_account_type(value):
    value = (value or "free").strip().lower()
    return value if value in {"free", "trial", "pro"} else "free"


def _trial_days_remaining_from_seconds(seconds):
    try:
        seconds = float(seconds or 0)
    except Exception:
        seconds = 0
    if seconds <= 0:
        return 0
    return max(1, int((seconds + 86399) // 86400))


def _current_user_plan_status():
    """
    Returns the logged-in user's live plan state.

    Trial behavior:
    - New users start as trial for 30 days.
    - Existing free users are migrated into a 30-day trial by init_database().
    - Once the trial end date passes, the user is automatically downgraded to Free.
    """
    user_id = _current_user_id()
    status = {
        "account_type": "free",
        "trial_days_remaining": 0,
        "trial_message": "",
    }

    if engine is None or not user_id:
        return status

    try:
        init_database()
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT
                    account_type,
                    trial_started_at,
                    trial_ends_at,
                    EXTRACT(EPOCH FROM (trial_ends_at - NOW())) AS trial_seconds_remaining
                FROM users
                WHERE id = :user_id
            """), {"user_id": user_id}).mappings().first()

            if not row:
                return status

            account_type = _normalize_account_type(row.get("account_type"))

            if account_type == "trial":
                seconds_remaining = row.get("trial_seconds_remaining") or 0
                days_remaining = _trial_days_remaining_from_seconds(seconds_remaining)

                if days_remaining <= 0:
                    conn.execute(text("""
                        UPDATE users
                        SET account_type = 'free'
                        WHERE id = :user_id
                          AND LOWER(COALESCE(account_type, 'free')) = 'trial'
                    """), {"user_id": user_id})
                    status["account_type"] = "free"
                    status["trial_message"] = "Your 30-day Pro Trial has ended. Upgrade to Pro to keep Pro features."
                    return status

                status["account_type"] = "trial"
                status["trial_days_remaining"] = days_remaining

                if days_remaining == 1:
                    status["trial_message"] = "Last day to enjoy all Pro features. Upgrade to Pro to keep them active."
                elif days_remaining == 2:
                    status["trial_message"] = "You have 2 days left to upgrade to Pro."
                elif days_remaining == 3:
                    status["trial_message"] = "You have 3 days left to upgrade to Pro."
                else:
                    status["trial_message"] = f"You are on a 30-day BUTTN Pro Trial. {days_remaining} days remaining."
                return status

            status["account_type"] = account_type
            return status
    except Exception:
        return status


def _current_user_account_type():
    return _current_user_plan_status().get("account_type", "free")


def _account_type_has_pro_access(account_type):
    return _normalize_account_type(account_type) in {"trial", "pro"}


def _set_current_user_pro():
    user_id = _current_user_id()
    if engine is None or not user_id:
        return False
    try:
        init_database()
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE users
                SET account_type = 'pro'
                WHERE id = :user_id
            """), {"user_id": user_id})
        return True
    except Exception:
        return False


def _app_absolute_url(path):
    root = (request.url_root or "").rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return root + path


def _create_stripe_checkout_session(user_id, user_email):
    if not STRIPE_SECRET_KEY:
        return None, "Stripe secret key is missing. Add STRIPE_SECRET_KEY in Railway."
    if not STRIPE_PRO_PRICE_ID:
        return None, "Stripe price ID is missing."

    payload = {
        "mode": "subscription",
        "line_items[0][price]": STRIPE_PRO_PRICE_ID,
        "line_items[0][quantity]": "1",
        "success_url": _app_absolute_url("/account/billing/success?session_id={CHECKOUT_SESSION_ID}"),
        "cancel_url": _app_absolute_url("/account/billing/cancel"),
        "client_reference_id": str(user_id),
        "customer_email": user_email or "",
        "metadata[user_id]": str(user_id),
        "metadata[product_id]": STRIPE_PRO_PRODUCT_ID,
    }

    data = urllib.parse.urlencode(payload).encode("utf-8")
    auth = base64.b64encode((STRIPE_SECRET_KEY + ":").encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        "https://api.stripe.com/v1/checkout/sessions",
        data=data,
        method="POST",
        headers={
            "Authorization": "Basic " + auth,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        checkout_url = result.get("url") or ""
        if not checkout_url:
            return None, "Stripe did not return a checkout URL."
        return checkout_url, ""
    except Exception as exc:
        return None, "Stripe checkout could not be created: " + str(exc)


@app.route("/account/upgrade")
def account_upgrade():
    user_id = _current_user_id()
    if not user_id:
        return redirect("/login")

    checkout_url, error = _create_stripe_checkout_session(user_id, _current_user_email())
    if not checkout_url:
        return _dashboard_page(message=error or "Stripe checkout could not be created.")

    return redirect(checkout_url)


@app.route("/account/billing/success")
def account_billing_success():
    if not _current_user_id():
        return redirect("/login")
    _set_current_user_pro()
    return _dashboard_page(message="Your BUTTN Pro plan is active.")


@app.route("/account/billing/cancel")
def account_billing_cancel():
    if not _current_user_id():
        return redirect("/login")
    return _dashboard_page(message="Checkout was canceled. Your account is still on the free plan.")


def _db_profile_exists(username):
    if engine is None:
        return False
    try:
        init_database()
        with engine.connect() as conn:
            row = conn.execute(text("SELECT id FROM profiles WHERE username = :username"), {"username": username}).first()
        return row is not None
    except Exception:
        return False


def _db_profile_owner_id(username):
    if engine is None:
        return None
    try:
        init_database()
        with engine.connect() as conn:
            row = conn.execute(text("SELECT user_id FROM profiles WHERE username = :username"), {"username": username}).first()
        return row[0] if row else None
    except Exception:
        return None


def _db_profile_account_type(username):
    """
    Return the owning user's live account type for a profile.
    Trial accounts count as Pro access until trial_ends_at passes.
    """
    if engine is None:
        return "pro"

    username = _normalize_buttn_url(username)
    if not username:
        return "free"

    try:
        init_database()
        with engine.begin() as conn:
            row = conn.execute(text("""
                SELECT
                    users.id AS user_id,
                    COALESCE(users.account_type, 'free') AS account_type,
                    users.trial_ends_at AS trial_ends_at
                FROM profiles
                LEFT JOIN users ON users.id = profiles.user_id
                WHERE profiles.username = :username
            """), {"username": username}).mappings().first()

            if not row:
                return "free"

            account_type = _normalize_account_type(row.get("account_type"))
            if account_type == "trial" and row.get("trial_ends_at") is not None:
                expired = conn.execute(text("SELECT (:trial_ends_at <= NOW())"), {"trial_ends_at": row.get("trial_ends_at")}).scalar()
                if expired:
                    conn.execute(text("UPDATE users SET account_type = 'free' WHERE id = :user_id AND LOWER(COALESCE(account_type, 'free')) = 'trial'"), {"user_id": row.get("user_id")})
                    return "free"
            return account_type
    except Exception:
        return "free"


def _profile_has_pro_access(username):
    return _account_type_has_pro_access(_db_profile_account_type(username))


def _user_profile_count(user_id):
    if engine is None or not user_id:
        return 0
    try:
        init_database()
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM profiles WHERE user_id = :user_id"), {"user_id": user_id}).scalar()
        return int(count or 0)
    except Exception:
        return 0


def _load_db_profile(username):
    if engine is None:
        return None
    try:
        init_database()
        with engine.connect() as conn:
            profile = conn.execute(text("SELECT * FROM profiles WHERE username = :username"), {"username": username}).mappings().first()
            if not profile:
                return None
            links = conn.execute(text("""
                SELECT icon, label, url
                FROM profile_links
                WHERE profile_id = :profile_id
                ORDER BY sort_order ASC, id ASC
            """), {"profile_id": profile["id"]}).mappings().all()
        data = dict(profile)
        data["buttn_url"] = data.get("username", username)
        data["links"] = [dict(item) for item in links]
        return data
    except Exception:
        return None


def _save_db_profile(username, profile, user_id):
    if engine is None or not user_id:
        return False
    username = _normalize_buttn_url(username)
    if not username:
        return False
    try:
        init_database()
        save_data = dict(profile)
        for key in ["name", "title", "phone", "email", "logo_b64", "header_image_b64", "header_bg_color", "header_image_opacity", "page_bg_color", "link_bg_color", "link_text_color", "link_border_color", "header_name_color", "header_title_color", "action_bg_color", "action_text_color", "action_border_color", "lead_capture_headline", "lead_capture_button_text", "spotlight_image_b64", "spotlight_headline", "spotlight_subtext", "spotlight_url", "spotlight_open_behavior", "spotlight_media_shape"]:
            save_data.setdefault(key, "")
        save_data["lead_capture_enabled"] = bool(save_data.get("lead_capture_enabled"))
        save_data["spotlight_enabled"] = bool(save_data.get("spotlight_enabled"))
        save_data["spotlight_show_play"] = bool(save_data.get("spotlight_show_play"))
        save_data["spotlight_autoplay"] = bool(save_data.get("spotlight_autoplay"))
        save_data["spotlight_open_behavior"] = _normalize_spotlight_open_behavior(save_data.get("spotlight_open_behavior"))
        save_data["spotlight_media_shape"] = _normalize_spotlight_media_shape(save_data.get("spotlight_media_shape"))
        with engine.begin() as conn:
            existing = conn.execute(text("SELECT id, user_id FROM profiles WHERE username = :username"), {"username": username}).mappings().first()
            if existing and existing["user_id"] != user_id:
                return False
            if not existing:
                profile_count = conn.execute(text("SELECT COUNT(*) FROM profiles WHERE user_id = :user_id"), {"user_id": user_id}).scalar() or 0
                if int(profile_count) >= MAX_ACCOUNT_PROFILES:
                    return False
            if existing:
                profile_id = existing["id"]
                conn.execute(text("""
                    UPDATE profiles SET
                        name=:name, title=:title, phone=:phone, email=:email, logo_b64=:logo_b64, header_image_b64=:header_image_b64,
                        header_bg_color=:header_bg_color, header_image_opacity=:header_image_opacity, page_bg_color=:page_bg_color,
                        link_bg_color=:link_bg_color, link_text_color=:link_text_color, link_border_color=:link_border_color,
                        header_name_color=:header_name_color, header_title_color=:header_title_color, action_bg_color=:action_bg_color,
                        action_text_color=:action_text_color, action_border_color=:action_border_color,
                        lead_capture_enabled=:lead_capture_enabled, lead_capture_headline=:lead_capture_headline,
                        lead_capture_button_text=:lead_capture_button_text,
                        spotlight_enabled=:spotlight_enabled, spotlight_image_b64=:spotlight_image_b64,
                        spotlight_headline=:spotlight_headline, spotlight_subtext=:spotlight_subtext,
                        spotlight_url=:spotlight_url, spotlight_show_play=:spotlight_show_play,
                        spotlight_open_behavior=:spotlight_open_behavior, spotlight_media_shape=:spotlight_media_shape,
                        spotlight_autoplay=:spotlight_autoplay,
                        updated_at=NOW()
                    WHERE id=:profile_id
                """), {**save_data, "profile_id": profile_id})
                conn.execute(text("DELETE FROM profile_links WHERE profile_id=:profile_id"), {"profile_id": profile_id})
            else:
                profile_id = conn.execute(text("""
                    INSERT INTO profiles (user_id, username, name, title, phone, email, logo_b64, header_image_b64, header_bg_color, header_image_opacity, page_bg_color, link_bg_color, link_text_color, link_border_color, header_name_color, header_title_color, action_bg_color, action_text_color, action_border_color, lead_capture_enabled, lead_capture_headline, lead_capture_button_text, spotlight_enabled, spotlight_image_b64, spotlight_headline, spotlight_subtext, spotlight_url, spotlight_show_play, spotlight_open_behavior, spotlight_media_shape, spotlight_autoplay)
                    VALUES (:user_id, :username, :name, :title, :phone, :email, :logo_b64, :header_image_b64, :header_bg_color, :header_image_opacity, :page_bg_color, :link_bg_color, :link_text_color, :link_border_color, :header_name_color, :header_title_color, :action_bg_color, :action_text_color, :action_border_color, :lead_capture_enabled, :lead_capture_headline, :lead_capture_button_text, :spotlight_enabled, :spotlight_image_b64, :spotlight_headline, :spotlight_subtext, :spotlight_url, :spotlight_show_play, :spotlight_open_behavior, :spotlight_media_shape, :spotlight_autoplay)
                    RETURNING id
                """), {**save_data, "user_id": user_id, "username": username}).scalar_one()
            for idx, item in enumerate(profile.get("links", [])):
                conn.execute(text("INSERT INTO profile_links (profile_id, sort_order, icon, label, url) VALUES (:profile_id, :sort_order, :icon, :label, :url)"), {"profile_id": profile_id, "sort_order": idx, "icon": item.get("icon", "custom"), "label": item.get("label", ""), "url": item.get("url", "")})
        return True
    except Exception:
        return False


@app.route("/api/check-url")
def api_check_url():
    username = _normalize_buttn_url(request.args.get("username") or "")
    current = _normalize_buttn_url(request.args.get("current") or "")
    if not username:
        return json.dumps({"available": False, "username": username, "message": "Enter a URL."}), 200, {"Content-Type": "application/json"}
    taken = _is_buttn_url_taken(username, current_username=current or None)
    return json.dumps({"available": not taken, "username": username, "message": "Available" if not taken else "Already taken"}), 200, {"Content-Type": "application/json"}


@app.route("/account/create-profile", methods=["POST"])
def account_create_profile():
    user_id = _current_user_id()
    if not user_id:
        return redirect("/login")

    profile_name = (request.form.get("profile_name") or "").strip()
    username = _normalize_buttn_url(request.form.get("username") or "")

    if not profile_name:
        return _dashboard_page(url_message="Please enter a profile name.")
    if not username:
        return _dashboard_page(url_message="Please enter a valid BUTTN URL.")
    if _is_buttn_url_taken(username):
        return _dashboard_page(url_message="That BUTTN URL is already taken.")
    if _user_profile_count(user_id) >= MAX_ACCOUNT_PROFILES:
        return _dashboard_page(url_message="You have reached the 10 profile limit for this account.")

    profile = BUTTN_PROFILES["test"].copy()
    profile["links"] = [item.copy() for item in BUTTN_PROFILES["test"].get("links", [])]
    profile["buttn_url"] = username
    profile["name"] = profile_name
    profile["title"] = ""
    profile["lead_capture_enabled"] = False
    profile["lead_capture_headline"] = "Stay Connected"
    profile["lead_capture_button_text"] = "Submit"
    profile["spotlight_enabled"] = False
    profile["spotlight_image_b64"] = ""
    profile["spotlight_headline"] = ""
    profile["spotlight_subtext"] = ""
    profile["spotlight_url"] = ""
    profile["spotlight_show_play"] = False
    profile["spotlight_autoplay"] = False
    profile["spotlight_open_behavior"] = "new_tab"
    profile["spotlight_media_shape"] = "vertical"
    _save_db_profile(username, profile, user_id)
    return redirect(f"/buttn/edit/{username}")

def render_generate_test_page(
    diagnostic_results=None,
    data_value="",
    art_data_b64="",
    bg_override_value="",
    current_bg_hex="#ffffff",
):
    diagnostic_results = diagnostic_results or []
    safe_data_value = html.escape(data_value or "")
    safe_art_data_b64 = html.escape(art_data_b64 or "")
    safe_bg_override_value = html.escape(bg_override_value or "")
    safe_current_bg_hex = html.escape(current_bg_hex or "#ffffff")
    safe_manual_override_value = "1" if bg_override_value else ""

    result_cards = ""
    for result in diagnostic_results:
        key = html.escape(result["key"])
        name = html.escape(result["name"])
        description = html.escape(result["description"])
        b64 = result["b64"]
        result_cards += f'''
        <section class="diagnostic-card">
            <h2>Version {key}: {name}</h2>
            <p>{description}</p>
            <img class="diagnostic-qr" src="data:image/png;base64,{b64}" alt="Diagnostic QR version {key}">
        </section>
        '''

    results_html = f'<div class="results-grid">{result_cards}</div>' if result_cards else ''

    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>QR Diagnostic Test Lab</title>
<style>
body {{ font-family: Arial, sans-serif; padding: 30px; background: #ffffff; }}
{_app_nav_css()}
h1 {{ margin-bottom: 8px; }}
.dev-warning {{ max-width: 860px; padding: 14px 16px; border: 1px solid #f0c36d; background: #fff8e5; border-radius: 10px; margin: 16px 0 26px; }}
.label {{ font-weight: bold; margin-bottom: 8px; }}
input[type="text"] {{ width: 360px; padding: 10px; font-size: 16px; }}
#dropzone {{ width: 420px; height: 220px; border: 2px dashed #999; display: flex; align-items: center; justify-content: center; cursor: pointer; margin-top: 10px; background: #fff; text-align: center; }}
#dropzone.hover {{ border-color: #000; }}
#preview {{ max-width: 260px; max-height: 180px; display: none; }}
button {{ margin-top: 16px; padding: 10px 18px; font-size: 16px; cursor: pointer; }}
.bg-row {{ margin-top: 18px; }}
.bg-row input {{ width: 180px; }}
.small-note {{ font-size: 14px; color: #555; margin-top: 8px; max-width: 760px; line-height: 1.4; }}
.results-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 24px; margin-top: 34px; }}
.diagnostic-card {{ border: 1px solid #ddd; border-radius: 14px; padding: 18px; background: #fafafa; }}
.diagnostic-card h2 {{ font-size: 18px; margin: 0 0 8px; }}
.diagnostic-card p {{ color: #555; min-height: 42px; }}
.diagnostic-qr {{ width: 100%; max-width: 360px; height: auto; display: block; background: #fff; }}
</style>
</head>
<body>
{_app_nav_html()}
<h1>QR Diagnostic Test Lab</h1>
<div class="dev-warning"><strong>Developer-only page.</strong> This route is isolated from <code>/generate</code>; it does not save data, change production routes, or replace the customer QR workflow.</div>

<form action="/generate-test" method="post" enctype="multipart/form-data">
    <div class="label">QR Data</div>
    <input type="text" name="data" required placeholder="Enter QR Data" value="{safe_data_value}"><br><br>

    <div class="label">Upload Artwork (optional)</div>
    <div id="dropzone">
        <span id="droptext">Drop Image Here or Click</span>
        <img id="preview" />
    </div>
    <input type="file" id="artfile" name="artfile" accept="image/*" style="display:none">
    <input type="hidden" name="art_data" id="art_data" value="{safe_art_data_b64}">

    <div class="bg-row">
        <div class="label">Background Override (optional)</div>
        <input type="text" id="bg_override" name="bg_override" placeholder="#ffffff" value="{safe_bg_override_value}">
        <input type="hidden" id="bg_manual_override" name="bg_manual_override" value="{safe_manual_override_value}">
        <div class="small-note">All diagnostic versions use the same URL, normalized artwork, background selection, sizing, and export dimensions. Each version changes exactly one rendering variable.</div>
    </div>

    <button type="submit" name="generate_action" value="generate" onclick="document.getElementById('bg_manual_override').value = document.getElementById('bg_override').value ? '1' : '';">Generate Test Versions</button>
</form>

{results_html}

<script>
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("artfile");
const preview = document.getElementById("preview");
const droptext = document.getElementById("droptext");
const artDataInput = document.getElementById("art_data");
dropzone.onclick = () => fileInput.click();
function resetBackgroundStateForNewArtwork() {{
    const bgOverrideInput = document.getElementById("bg_override");
    const manualOverrideInput = document.getElementById("bg_manual_override");
    if (bgOverrideInput) bgOverrideInput.value = "";
    if (manualOverrideInput) manualOverrideInput.value = "";
}}
function loadFileIntoPreview(file) {{
    if (!file) return;
    resetBackgroundStateForNewArtwork();
    preview.src = URL.createObjectURL(file);
    preview.style.display = "block";
    droptext.style.display = "none";
    const reader = new FileReader();
    reader.onload = function(e) {{
        const result = e.target.result || "";
        const parts = result.split(",");
        if (parts.length === 2) artDataInput.value = parts[1];
    }};
    reader.readAsDataURL(file);
}}
fileInput.onchange = () => loadFileIntoPreview(fileInput.files[0]);
dropzone.addEventListener("dragover", e => {{ e.preventDefault(); dropzone.classList.add("hover"); }});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("hover"));
dropzone.addEventListener("drop", e => {{ e.preventDefault(); dropzone.classList.remove("hover"); if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {{ fileInput.files = e.dataTransfer.files; loadFileIntoPreview(e.dataTransfer.files[0]); }} }});
if (artDataInput.value) {{ droptext.textContent = "Previously uploaded artwork is ready. Drop or click to replace."; }}
</script>
</body>
</html>
"""


@app.route("/generate-test", methods=["GET", "POST"])
def generate_test_lab():
    diagnostic_results = []
    data_value = ""
    art_data_b64 = ""
    bg_override_value = ""
    current_bg_hex = "#ffffff"

    if request.method == "POST":
        data_value = (request.form.get("data") or "").strip()
        bg_override_value = (request.form.get("bg_override") or "").strip()
        manual_bg_override = (request.form.get("bg_manual_override") or "").strip() == "1"
        if not manual_bg_override:
            bg_override_value = ""

        art_data_b64 = (request.form.get("art_data") or "").strip()
        art_file = request.files.get("artfile")
        uploaded_art = fetch_uploaded_image(art_file)

        # Developer test lab isolation: selecting a new artwork file starts a
        # completely fresh background-selection project. Ignore any submitted
        # manual override or hidden/current background state that may have been
        # left on the page by the previous diagnostic generation.
        if uploaded_art is not None:
            bg_override_value = ""
            manual_bg_override = False
            original_art = uploaded_art
        elif art_data_b64:
            original_art = fetch_image_from_hidden_b64(art_data_b64)
        else:
            original_art = None

        if data_value:
            generation_art = original_art.copy() if original_art is not None else None
            qr_art = normalize_artwork_to_square(
                generation_art,
                tolerance=0.12,
                bg_override=bg_override_value,
            )

            for variant in QR_DIAGNOSTIC_VARIANTS:
                variant_art = qr_art.copy() if qr_art is not None else None
                qr_img = generate_branded_qr_diagnostic_variant(
                    data_value,
                    variant_art,
                    bg_override=bg_override_value,
                    variant=variant,
                )
                diagnostic_results.append({
                    "key": variant["key"],
                    "name": variant["name"],
                    "description": variant["description"],
                    "b64": image_to_base64(qr_img),
                })

            if diagnostic_results:
                first_img = generate_branded_qr_diagnostic_variant(
                    data_value,
                    qr_art.copy() if qr_art is not None else None,
                    bg_override=bg_override_value,
                    variant=QR_DIAGNOSTIC_VARIANTS[0],
                )
                current_bg_hex = rgb_to_hex(first_img.convert("RGB").getpixel((5, 5)))

            if original_art is not None:
                art_data_b64 = image_to_base64(original_art)

    return render_generate_test_page(
        diagnostic_results=diagnostic_results,
        data_value=data_value,
        art_data_b64=art_data_b64,
        bg_override_value=bg_override_value,
        current_bg_hex=current_bg_hex,
    )

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
        manual_bg_override = (request.form.get("bg_manual_override") or "").strip() == "1"
        if not manual_bg_override:
            bg_override_value = ""
        art_data_b64 = (request.form.get("art_data") or "").strip()
        qr_style = (request.form.get("qr_style") or "artistic").strip().lower()
        last_rendered_qr_style = (request.form.get("last_rendered_qr_style") or qr_style).strip().lower()

        # Issue #1 fix:
        # If the customer switches QR modes without refreshing the page, do not let
        # the previous mode's manual/current background state contaminate the new render.
        # This allows Simple -> Branded -> Simple -> Branded to regenerate cleanly each time.
        if last_rendered_qr_style != qr_style:
            bg_override_value = ""
            manual_bg_override = False

        art_file = request.files.get("artfile")
        original_art = fetch_uploaded_image(art_file)

        if original_art is None and art_data_b64:
            original_art = fetch_image_from_hidden_b64(art_data_b64)

        if qr_style not in ("simple", "artistic"):
            qr_style = "artistic"

        if data_value:
            # Start every generation from a clean, request-local artwork object.
            # Rendering helpers may resize, pad, or thumbnail images; none of those
            # derived objects should be written back as the upload source for a
            # later Generate click.
            generation_art = original_art.copy() if original_art is not None else None

            if qr_style == "simple":
                qr_img = generate_simple_qr(data_value, logo=generation_art)
            else:
                qr_art = normalize_artwork_to_square(
                    generation_art,
                    tolerance=0.12,
                    bg_override=bg_override_value,
                )
                qr_img = generate_branded_qr(data_value, qr_art, bg_override=bg_override_value)

            qr_b64 = image_to_base64(qr_img)

            card_mockup = create_card_mockup(qr_img)
            dome_mockup = create_dome_mockup(qr_img)

            card_mockup_b64 = image_to_base64(card_mockup)
            dome_mockup_b64 = image_to_base64(dome_mockup)

            current_bg_hex = rgb_to_hex(qr_img.convert("RGB").getpixel((5, 5)))

            if original_art is not None:
                art_data_b64 = image_to_base64(original_art)

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
        "lead_capture_enabled": False,
        "lead_capture_headline": "Stay Connected",
        "lead_capture_button_text": "Submit",
        "spotlight_enabled": False,
        "spotlight_image_b64": "",
        "spotlight_headline": "",
        "spotlight_subtext": "",
        "spotlight_url": "",
        "spotlight_show_play": False,
        "spotlight_autoplay": False,
        "spotlight_open_behavior": "new_tab",
        "spotlight_media_shape": "vertical",
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
MAX_ACCOUNT_PROFILES = 10


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


def _display_name_from_username(username):
    username = _normalize_buttn_url(username)
    if not username:
        return ""
    return " ".join(part.capitalize() for part in username.split("-") if part)


def _is_buttn_url_taken(url_value, current_username=None):
    normalized = _normalize_buttn_url(url_value)
    current_username = _normalize_buttn_url(current_username or "")

    if not normalized:
        return False

    if normalized in RESERVED_BUTTN_URLS:
        return True

    if normalized in BUTTN_PROFILES and normalized != current_username:
        return True

    if engine is not None:
        try:
            init_database()
            with engine.connect() as conn:
                row = conn.execute(text("SELECT username FROM profiles WHERE username = :username"), {"username": normalized}).first()
            if row is not None and normalized != current_username:
                return True
        except Exception:
            pass

    return False

def _safe_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://") or value.startswith("mailto:") or value.startswith("tel:"):
        return value
    return "https://" + value


def _normalize_spotlight_open_behavior(value):
    value = (value or "new_tab").strip().lower()
    return value if value in {"new_tab", "same_page", "play_page"} else "new_tab"


def _normalize_spotlight_media_shape(value):
    value = (value or "vertical").strip().lower()
    return value if value in {"vertical", "landscape", "square"} else "vertical"


def _spotlight_aspect_style(shape):
    shape = _normalize_spotlight_media_shape(shape)
    if shape == "landscape":
        return "16 / 9"
    if shape == "square":
        return "1 / 1"
    return "9 / 16"


def _spotlight_embed_url(value, autoplay=True, muted=False):
    """
    Convert normal social/video links into iframe-friendly embed URLs.
    Return an empty string when the platform/link is not safely embeddable so
    the public page can fall back to opening the original URL instead of showing
    a broken iframe.
    """
    url = _safe_url(value)
    if not url:
        return ""

    autoplay = bool(autoplay)
    muted = bool(muted)

    def _query_string(params):
        clean = []
        for key, val in params:
            if val is not None and val != "":
                clean.append((key, str(val)))
        return urllib.parse.urlencode(clean)

    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.netloc or "").lower().replace("www.", "")
        path = parsed.path or ""
        query = urllib.parse.parse_qs(parsed.query or "")

        if "youtube.com" in host:
            video_id = ""
            if path.startswith("/shorts/"):
                video_id = path.split("/shorts/", 1)[1].split("/")[0]
            elif path.startswith("/watch"):
                video_id = (query.get("v") or [""])[0]
            elif path.startswith("/embed/"):
                video_id = path.split("/embed/", 1)[1].split("/")[0]
            if video_id:
                return "https://www.youtube.com/embed/" + urllib.parse.quote(video_id, safe="") + "?" + _query_string([("autoplay", 1 if autoplay else 0), ("mute", 1 if muted else 0), ("playsinline", 1)])

        if "youtu.be" in host:
            video_id = path.strip("/").split("/")[0]
            if video_id:
                return "https://www.youtube.com/embed/" + urllib.parse.quote(video_id, safe="") + "?" + _query_string([("autoplay", 1 if autoplay else 0), ("mute", 1 if muted else 0), ("playsinline", 1)])

        if "tiktok.com" in host:
            parts = [part for part in path.split("/") if part]
            video_id = ""
            if "video" in parts:
                idx = parts.index("video")
                if idx + 1 < len(parts):
                    video_id = parts[idx + 1]
            elif path.startswith("/embed/v2/"):
                video_id = path.split("/embed/v2/", 1)[1].split("/")[0]
            if video_id and video_id.isdigit():
                return "https://www.tiktok.com/embed/v2/" + urllib.parse.quote(video_id, safe="")

        if "instagram.com" in host:
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"p", "reel", "tv"}:
                shortcode = parts[1]
                if shortcode:
                    return "https://www.instagram.com/" + parts[0] + "/" + urllib.parse.quote(shortcode, safe="") + "/embed/"

        if "vimeo.com" in host:
            video_id = path.strip("/").split("/")[-1]
            if video_id.isdigit():
                return "https://player.vimeo.com/video/" + urllib.parse.quote(video_id, safe="") + "?" + _query_string([("autoplay", 1 if autoplay else 0), ("muted", 1 if muted else 0), ("playsinline", 1)])

        if "loom.com" in host:
            parts = [part for part in path.split("/") if part]
            if "share" in parts:
                idx = parts.index("share")
                if idx + 1 < len(parts):
                    return "https://www.loom.com/embed/" + urllib.parse.quote(parts[idx + 1], safe="")
            if "embed" in parts:
                idx = parts.index("embed")
                if idx + 1 < len(parts):
                    return "https://www.loom.com/embed/" + urllib.parse.quote(parts[idx + 1], safe="")

        if "wistia.com" in host or "wi.st" in host:
            parts = [part for part in path.split("/") if part]
            media_id = parts[-1] if parts else ""
            if media_id:
                return "https://fast.wistia.net/embed/iframe/" + urllib.parse.quote(media_id, safe="")

        if "facebook.com" in host or "fb.watch" in host:
            return "https://www.facebook.com/plugins/video.php?href=" + urllib.parse.quote(url, safe="") + "&show_text=false&width=500"

        return ""
    except Exception:
        return ""



def _spotlight_youtube_video_id(url):
    url = _safe_url(url)
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.netloc or "").lower().replace("www.", "")
        path = parsed.path or ""
        query = urllib.parse.parse_qs(parsed.query or "")

        if "youtube.com" in host:
            if path.startswith("/shorts/"):
                return path.split("/shorts/", 1)[1].split("/")[0]
            if path.startswith("/watch"):
                return (query.get("v") or [""])[0]
            if path.startswith("/embed/"):
                return path.split("/embed/", 1)[1].split("/")[0]

        if "youtu.be" in host:
            return path.strip("/").split("/")[0]
    except Exception:
        return ""
    return ""


def _fetch_json_url(url, timeout=7):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; BUTTNBot/1.0; +https://mybuttn.com)",
                "Accept": "application/json,text/html,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(700000)
        return json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return None


def _fetch_text_url(url, timeout=7):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(900000)
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _absolute_url(base_url, maybe_url):
    maybe_url = (maybe_url or "").strip()
    if not maybe_url:
        return ""
    try:
        return urllib.parse.urljoin(base_url, maybe_url)
    except Exception:
        return maybe_url


def _extract_meta_image(page_html, base_url):
    if not page_html:
        return ""

    patterns = [
        r'<meta[^>]+property=["\\\']og:image:secure_url["\\\'][^>]+content=["\\\']([^"\\\']+)["\\\']',
        r'<meta[^>]+content=["\\\']([^"\\\']+)["\\\'][^>]+property=["\\\']og:image:secure_url["\\\']',
        r'<meta[^>]+property=["\\\']og:image["\\\'][^>]+content=["\\\']([^"\\\']+)["\\\']',
        r'<meta[^>]+content=["\\\']([^"\\\']+)["\\\'][^>]+property=["\\\']og:image["\\\']',
        r'<meta[^>]+name=["\\\']twitter:image["\\\'][^>]+content=["\\\']([^"\\\']+)["\\\']',
        r'<meta[^>]+content=["\\\']([^"\\\']+)["\\\'][^>]+name=["\\\']twitter:image["\\\']',
    ]

    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.IGNORECASE)
        if match:
            return _absolute_url(base_url, html.unescape(match.group(1)))

    return ""


def _spotlight_thumbnail_url(value):
    """
    Best-effort automatic thumbnail for Spotlight links.
    Custom uploaded images still win; this only fills the gap when no image is uploaded.
    """
    url = _safe_url(value)
    if not url:
        return ""

    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.netloc or "").lower().replace("www.", "")

        youtube_id = _spotlight_youtube_video_id(url)
        if youtube_id:
            return "https://img.youtube.com/vi/" + urllib.parse.quote(youtube_id, safe="") + "/hqdefault.jpg"

        if "vimeo.com" in host:
            data = _fetch_json_url("https://vimeo.com/api/oembed.json?url=" + urllib.parse.quote(url, safe=""))
            thumb = (data or {}).get("thumbnail_url") or ""
            if thumb:
                return thumb

        if "tiktok.com" in host:
            data = _fetch_json_url("https://www.tiktok.com/oembed?url=" + urllib.parse.quote(url, safe=""))
            thumb = (data or {}).get("thumbnail_url") or ""
            if thumb:
                return thumb

        if "instagram.com" in host:
            page = _fetch_text_url(url)
            thumb = _extract_meta_image(page, url)
            if thumb:
                return thumb

        if "facebook.com" in host or "fb.watch" in host:
            page = _fetch_text_url(url)
            thumb = _extract_meta_image(page, url)
            if thumb:
                return thumb

        if "loom.com" in host:
            data = _fetch_json_url("https://www.loom.com/v1/oembed?url=" + urllib.parse.quote(url, safe=""))
            thumb = (data or {}).get("thumbnail_url") or ""
            if thumb:
                return thumb

        page = _fetch_text_url(url)
        return _extract_meta_image(page, url)
    except Exception:
        return ""


@app.route("/api/spotlight-thumbnail")
def api_spotlight_thumbnail():
    url = request.args.get("url") or ""
    thumbnail = _spotlight_thumbnail_url(url)
    payload = {"thumbnail_url": thumbnail}
    return json.dumps(payload), 200, {"Content-Type": "application/json"}


@app.route("/api/spotlight-thumbnail-image")
def api_spotlight_thumbnail_image():
    url = request.args.get("url") or ""
    thumbnail = _spotlight_thumbnail_url(url)
    if not thumbnail:
        return Response(status=404)
    return redirect(thumbnail)


def _get_profile(username="test"):
    username = _normalize_buttn_url(username or "test") or "test"
    db_profile = _load_db_profile(username) if "_load_db_profile" in globals() else None
    if db_profile:
        return db_profile
    return BUTTN_PROFILES.get(username) or BUTTN_PROFILES["test"]


def _profile_logo_html(profile):
    logo_b64 = profile.get("logo_b64", "")
    if logo_b64:
        return f'<img class="profile-logo-img" src="data:image/png;base64,{html.escape(logo_b64)}" alt="Logo">'
    initial = html.escape((profile.get("name") or "B")[:1].upper())
    return f'<div class="profile-logo-fallback">{initial}</div>'


def _db_profile_id(username):
    if engine is None:
        return None
    username = _normalize_buttn_url(username)
    if not username:
        return None
    try:
        init_database()
        with engine.connect() as conn:
            row = conn.execute(text("SELECT id FROM profiles WHERE username = :username"), {"username": username}).first()
        return row[0] if row else None
    except Exception:
        return None


def _record_profile_view(username):
    profile_id = _db_profile_id(username)
    if not profile_id or engine is None:
        return
    try:
        init_database()
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO profile_views (profile_id) VALUES (:profile_id)"), {"profile_id": profile_id})
    except Exception:
        pass


def _record_link_click(username, link_label, link_url):
    profile_id = _db_profile_id(username)
    if not profile_id or engine is None:
        return
    try:
        init_database()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO link_clicks (profile_id, link_label, link_url)
                VALUES (:profile_id, :link_label, :link_url)
            """), {"profile_id": profile_id, "link_label": link_label or "", "link_url": link_url or ""})
    except Exception:
        pass





def _record_lead(username, lead_name, lead_email, lead_phone=""):
    profile_id = _db_profile_id(username)
    lead_name = (lead_name or "").strip()[:120]
    lead_email = (lead_email or "").strip().lower()[:240]
    lead_phone = (lead_phone or "").strip()[:80]
    if not profile_id or engine is None or not lead_email or "@" not in lead_email:
        return False
    try:
        init_database()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO profile_leads (profile_id, name, email, phone)
                VALUES (:profile_id, :name, :email, :phone)
            """), {"profile_id": profile_id, "name": lead_name, "email": lead_email, "phone": lead_phone})
        return True
    except Exception:
        return False


def _get_profile_lead_count(username):
    profile_id = _db_profile_id(username)
    if not profile_id or engine is None:
        return 0
    try:
        init_database()
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM profile_leads WHERE profile_id = :profile_id"), {"profile_id": profile_id}).scalar()
        return int(count or 0)
    except Exception:
        return 0


def _get_profile_leads(username, limit=200):
    profile_id = _db_profile_id(username)
    if not profile_id or engine is None:
        return []
    try:
        init_database()
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT name, email, phone, to_char(created_at, 'Mon DD, YYYY HH12:MI AM') AS created_label
                FROM profile_leads
                WHERE profile_id = :profile_id
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            """), {"profile_id": profile_id, "limit": int(limit)}).mappings().all()
        return [dict(row) for row in rows]
    except Exception:
        return []

def _get_profile_analytics(username):
    profile_id = _db_profile_id(username)
    data = {
        "views": 0,
        "views_today": 0,
        "views_7_days": 0,
        "views_30_days": 0,
        "clicks_total": 0,
        "clicks_today": 0,
        "clicks_7_days": 0,
        "clicks_30_days": 0,
        "clicks": [],
        "daily_views": [],
        "daily_clicks": [],
    }
    if not profile_id or engine is None:
        return data

    try:
        init_database()
        with engine.connect() as conn:
            data["views"] = conn.execute(text("""
                SELECT COUNT(*)
                FROM profile_views
                WHERE profile_id = :profile_id
            """), {"profile_id": profile_id}).scalar() or 0

            data["views_today"] = conn.execute(text("""
                SELECT COUNT(*)
                FROM profile_views
                WHERE profile_id = :profile_id
                  AND viewed_at >= date_trunc('day', NOW())
            """), {"profile_id": profile_id}).scalar() or 0

            data["views_7_days"] = conn.execute(text("""
                SELECT COUNT(*)
                FROM profile_views
                WHERE profile_id = :profile_id
                  AND viewed_at >= NOW() - INTERVAL '7 days'
            """), {"profile_id": profile_id}).scalar() or 0

            data["views_30_days"] = conn.execute(text("""
                SELECT COUNT(*)
                FROM profile_views
                WHERE profile_id = :profile_id
                  AND viewed_at >= NOW() - INTERVAL '30 days'
            """), {"profile_id": profile_id}).scalar() or 0

            data["clicks_total"] = conn.execute(text("""
                SELECT COUNT(*)
                FROM link_clicks
                WHERE profile_id = :profile_id
            """), {"profile_id": profile_id}).scalar() or 0

            data["clicks_today"] = conn.execute(text("""
                SELECT COUNT(*)
                FROM link_clicks
                WHERE profile_id = :profile_id
                  AND clicked_at >= date_trunc('day', NOW())
            """), {"profile_id": profile_id}).scalar() or 0

            data["clicks_7_days"] = conn.execute(text("""
                SELECT COUNT(*)
                FROM link_clicks
                WHERE profile_id = :profile_id
                  AND clicked_at >= NOW() - INTERVAL '7 days'
            """), {"profile_id": profile_id}).scalar() or 0

            data["clicks_30_days"] = conn.execute(text("""
                SELECT COUNT(*)
                FROM link_clicks
                WHERE profile_id = :profile_id
                  AND clicked_at >= NOW() - INTERVAL '30 days'
            """), {"profile_id": profile_id}).scalar() or 0

            rows = conn.execute(text("""
                SELECT link_label, link_url, COUNT(*) AS click_count
                FROM link_clicks
                WHERE profile_id = :profile_id
                GROUP BY link_label, link_url
                ORDER BY click_count DESC, link_label ASC
            """), {"profile_id": profile_id}).mappings().all()

            daily_views = conn.execute(text("""
                SELECT to_char(day::date, 'Mon DD') AS day_label, COALESCE(COUNT(profile_views.id), 0) AS total
                FROM generate_series(
                    date_trunc('day', NOW()) - INTERVAL '6 days',
                    date_trunc('day', NOW()),
                    INTERVAL '1 day'
                ) AS day
                LEFT JOIN profile_views
                  ON profile_views.profile_id = :profile_id
                 AND profile_views.viewed_at >= day
                 AND profile_views.viewed_at < day + INTERVAL '1 day'
                GROUP BY day
                ORDER BY day ASC
            """), {"profile_id": profile_id}).mappings().all()

            daily_clicks = conn.execute(text("""
                SELECT to_char(day::date, 'Mon DD') AS day_label, COALESCE(COUNT(link_clicks.id), 0) AS total
                FROM generate_series(
                    date_trunc('day', NOW()) - INTERVAL '6 days',
                    date_trunc('day', NOW()),
                    INTERVAL '1 day'
                ) AS day
                LEFT JOIN link_clicks
                  ON link_clicks.profile_id = :profile_id
                 AND link_clicks.clicked_at >= day
                 AND link_clicks.clicked_at < day + INTERVAL '1 day'
                GROUP BY day
                ORDER BY day ASC
            """), {"profile_id": profile_id}).mappings().all()

        data["clicks"] = [dict(row) for row in rows]
        data["daily_views"] = [dict(row) for row in daily_views]
        data["daily_clicks"] = [dict(row) for row in daily_clicks]
    except Exception:
        pass

    return data


@app.route("/account/analytics/<username>")
def account_profile_analytics(username):
    user_id = _current_user_id()
    if not user_id:
        return redirect("/login")

    username = _normalize_buttn_url(username)
    owner_id = _db_profile_owner_id(username)
    if not owner_id or owner_id != user_id:
        return redirect("/account")

    profile = _get_profile(username)
    analytics = _get_profile_analytics(username)
    safe_username = html.escape(username)
    safe_name = html.escape(profile.get("name") or _display_name_from_username(username) or username)

    view_count = int(analytics.get("views") or 0)
    views_today = int(analytics.get("views_today") or 0)
    views_7_days = int(analytics.get("views_7_days") or 0)
    views_30_days = int(analytics.get("views_30_days") or 0)

    clicks_total = int(analytics.get("clicks_total") or 0)
    clicks_today = int(analytics.get("clicks_today") or 0)
    clicks_7_days = int(analytics.get("clicks_7_days") or 0)
    clicks_30_days = int(analytics.get("clicks_30_days") or 0)

    click_rows = ""
    for row in analytics.get("clicks", []):
        label = html.escape(row.get("link_label") or "Untitled Link")
        url = html.escape(row.get("link_url") or "")
        count = int(row.get("click_count") or 0)
        click_rows += f"""
        <div class="analytics-row">
            <div><strong>{label}</strong><br><span>{url}</span></div>
            <div class="count">{count}</div>
        </div>
        """

    if not click_rows:
        click_rows = '<p class="empty">No link clicks recorded yet.</p>'

    max_daily = 1
    for item in analytics.get("daily_views", []):
        max_daily = max(max_daily, int(item.get("total") or 0))
    for item in analytics.get("daily_clicks", []):
        max_daily = max(max_daily, int(item.get("total") or 0))

    daily_rows = ""
    daily_views = analytics.get("daily_views", [])
    daily_clicks = analytics.get("daily_clicks", [])
    for idx, view_item in enumerate(daily_views):
        day_label = html.escape(view_item.get("day_label") or "")
        day_views = int(view_item.get("total") or 0)
        day_clicks = 0
        if idx < len(daily_clicks):
            day_clicks = int(daily_clicks[idx].get("total") or 0)

        view_width = int((day_views / max_daily) * 100) if max_daily else 0
        click_width = int((day_clicks / max_daily) * 100) if max_daily else 0

        daily_rows += f"""
        <div class="daily-row">
            <div class="daily-day">{day_label}</div>
            <div class="daily-bars">
                <div class="bar-line"><span>Views</span><div class="bar-track"><div class="bar-fill" style="width:{view_width}%"></div></div><strong>{day_views}</strong></div>
                <div class="bar-line"><span>Clicks</span><div class="bar-track"><div class="bar-fill bar-fill-clicks" style="width:{click_width}%"></div></div><strong>{day_clicks}</strong></div>
            </div>
        </div>
        """

    if not daily_rows:
        daily_rows = '<p class="empty">No daily data yet.</p>'

    return f"""
<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Analytics | BUTTN</title>
<style>
body {{ margin:0; font-family:Arial,sans-serif; background:#f3f5f7; color:#111; }}
.wrap {{ max-width:860px; margin:40px auto; padding:20px; }}
.card {{ background:#fff; border:1px solid #dde1e7; border-radius:18px; padding:24px; box-shadow:0 8px 24px rgba(0,0,0,0.04); margin-bottom:18px; }}
h1 {{ margin:0 0 6px; }} .muted, .empty {{ color:#666; }}
.stat-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
.stat-card {{ background:#f8fafc; border:1px solid #e1e6ee; border-radius:16px; padding:18px; }}
.stat-card h3 {{ margin:0 0 8px; color:#555; font-size:14px; text-transform:uppercase; letter-spacing:.04em; }}
.big-number {{ font-size:42px; font-weight:900; line-height:1; }}
.small-stats {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:14px; }}
.small-stat {{ background:#fff; border:1px solid #e5e9f0; border-radius:12px; padding:12px; }}
.small-stat span {{ display:block; color:#666; font-size:12px; font-weight:800; margin-bottom:5px; }}
.small-stat strong {{ font-size:22px; }}
.analytics-row {{ display:flex; justify-content:space-between; gap:16px; border-top:1px solid #eee; padding:16px 0; align-items:center; }}
.analytics-row:first-child {{ border-top:none; }}
.analytics-row span {{ color:#666; font-size:13px; word-break:break-all; }}
.count {{ font-size:24px; font-weight:900; }}
.daily-row {{ display:grid; grid-template-columns:86px 1fr; gap:14px; align-items:center; border-top:1px solid #eee; padding:14px 0; }}
.daily-row:first-child {{ border-top:none; }}
.daily-day {{ font-weight:900; color:#444; }}
.bar-line {{ display:grid; grid-template-columns:48px 1fr 34px; gap:10px; align-items:center; margin:5px 0; font-size:13px; color:#555; }}
.bar-track {{ height:10px; background:#eef1f5; border-radius:999px; overflow:hidden; }}
.bar-fill {{ height:10px; background:#111; border-radius:999px; }}
.bar-fill-clicks {{ opacity:.55; }}
a {{ color:#111; font-weight:800; }}
{_app_nav_css()}
@media (max-width:640px) {{
    .stat-grid, .small-stats {{ grid-template-columns:1fr; }}
    .daily-row {{ grid-template-columns:1fr; }}
}}
</style></head>
<body>{_app_nav_html(username)}
<div class="wrap">
  <div class="card">
    <h1>Analytics</h1>
    <div class="muted">{safe_name} /{safe_username}</div>
    <p><a href="/account">Back to Account</a> &nbsp; <a href="/{safe_username}" target="_blank">View Profile</a></p>
  </div>

  <div class="stat-grid">
    <div class="stat-card">
      <h3>Profile Views</h3>
      <div class="big-number">{view_count}</div>
      <div class="small-stats">
        <div class="small-stat"><span>Today</span><strong>{views_today}</strong></div>
        <div class="small-stat"><span>7 Days</span><strong>{views_7_days}</strong></div>
        <div class="small-stat"><span>30 Days</span><strong>{views_30_days}</strong></div>
      </div>
    </div>
    <div class="stat-card">
      <h3>Link Clicks</h3>
      <div class="big-number">{clicks_total}</div>
      <div class="small-stats">
        <div class="small-stat"><span>Today</span><strong>{clicks_today}</strong></div>
        <div class="small-stat"><span>7 Days</span><strong>{clicks_7_days}</strong></div>
        <div class="small-stat"><span>30 Days</span><strong>{clicks_30_days}</strong></div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Last 7 Days</h2>
    {daily_rows}
  </div>

  <div class="card">
    <h2>Top Clicked Links</h2>
    {click_rows}
  </div>
</div>
</body></html>
"""


@app.route("/buttn/contact/<username>")
def buttn_contact_page(username):
    username = _normalize_buttn_url(username or "test") or "test"
    profile = _get_profile(username)

    safe_name = html.escape((profile.get("name") or _display_name_from_username(username) or username).strip())
    safe_title = html.escape((profile.get("title") or "").strip())
    safe_phone = html.escape((profile.get("phone") or "").strip())
    safe_email = html.escape((profile.get("email") or "").strip())
    safe_profile_url = html.escape(f"https://mybuttn.com/{username}")

    safe_header_bg = _clean_hex(profile.get("header_bg_color"), "#9d5d4d")
    safe_page_bg = _clean_hex(profile.get("page_bg_color"), "#f5f5f5")
    safe_action_bg = _clean_hex(profile.get("action_bg_color"), "#ffffff")
    safe_action_text = _clean_hex(profile.get("action_text_color"), "#111111")
    safe_action_border = _clean_hex(profile.get("action_border_color"), "#d8dde6")
    safe_header_name_color = _clean_hex(profile.get("header_name_color"), "#111111")
    safe_header_title_color = _clean_hex(profile.get("header_title_color"), "#555555")

    phone_block = ""
    if safe_phone:
        phone_block = f"""
        <div class="contact-row">
          <div class="contact-label">Phone</div>
          <div class="contact-value">{safe_phone}</div>
          <div class="contact-actions">
            <a class="contact-btn" href="tel:{safe_phone}">Call</a>
            <button type="button" class="contact-btn" onclick="copyText('{safe_phone}')">Copy</button>
          </div>
        </div>
        """

    email_block = ""
    if safe_email:
        email_block = f"""
        <div class="contact-row">
          <div class="contact-label">Email</div>
          <div class="contact-value">{safe_email}</div>
          <div class="contact-actions">
            <a class="contact-btn" href="mailto:{safe_email}">Email</a>
            <button type="button" class="contact-btn" onclick="copyText('{safe_email}')">Copy</button>
          </div>
        </div>
        """

    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_name} Contact | BUTTN</title>
<style>
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Arial,sans-serif; background:{safe_page_bg}; color:#111; }}
.phone-shell {{ max-width:430px; margin:0 auto; min-height:100vh; background:{safe_page_bg}; box-shadow:0 0 28px rgba(0,0,0,0.08); }}
.profile-header {{ background:{safe_header_bg}; padding:44px 22px 28px; text-align:center; }}
.profile-logo {{ width:116px; height:116px; margin:0 auto 16px; border-radius:50%; background:#fff; display:flex; align-items:center; justify-content:center; border:4px solid rgba(255,255,255,0.85); box-shadow:0 12px 30px rgba(0,0,0,0.16); overflow:hidden; }}
.profile-logo-img {{ width:100%; height:100%; object-fit:cover; }}
.profile-logo-fallback {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:50px; font-weight:800; color:#111; background:#fff; }}
.profile-name {{ font-size:25px; font-weight:800; color:{safe_header_name_color}; }}
.profile-title {{ font-size:15px; color:{safe_header_title_color}; margin-top:6px; }}
.contact-area {{ padding:24px 20px 34px; }}
.contact-note {{ background:#fff; border:1px solid #dde1e7; border-radius:16px; padding:15px; color:#555; font-size:14px; line-height:1.4; margin-bottom:16px; }}
.contact-row {{ background:#fff; border:1px solid #dde1e7; border-radius:18px; padding:16px; margin-bottom:14px; box-shadow:0 8px 18px rgba(0,0,0,0.04); }}
.contact-label {{ font-size:13px; color:#777; font-weight:700; margin-bottom:6px; }}
.contact-value {{ font-size:17px; font-weight:800; word-break:break-word; }}
.contact-actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }}
.contact-btn {{ text-decoration:none; color:{safe_action_text}; background:{safe_action_bg}; border:1px solid {safe_action_border}; border-radius:999px; padding:10px 16px; font-weight:800; font-size:14px; cursor:pointer; font-family:Arial,sans-serif; }}
.back-link {{ display:block; text-align:center; margin-top:18px; color:#111; font-weight:800; text-decoration:none; }}
.copy-status {{ text-align:center; color:#555; font-size:13px; min-height:18px; margin-top:12px; }}
.buttn-footer {{ text-align:center; font-size:12px; color:#777; padding:6px 20px 26px; }}
{_app_nav_css()}
</style>
</head>
<body>
{_app_nav_html(username)}
<div class="phone-shell">
  <div class="profile-header">
    <div class="profile-logo">{_profile_logo_html(profile)}</div>
    <div class="profile-name">{safe_name}</div>
    <div class="profile-title">{safe_title}</div>
  </div>
  <div class="contact-area">
    <div class="contact-note">No file will download. Use the buttons below to call, email, or copy the contact details into your phone.</div>
    {phone_block}
    {email_block}
    <div class="contact-row">
      <div class="contact-label">BUTTN Page</div>
      <div class="contact-value">{safe_profile_url}</div>
      <div class="contact-actions">
        <a class="contact-btn" href="/{html.escape(username)}">Open Page</a>
        <button type="button" class="contact-btn" onclick="copyText('{safe_profile_url}')">Copy</button>
      </div>
    </div>
    <div id="copy_status" class="copy-status"></div>
    <a class="back-link" href="/{html.escape(username)}">Back to Profile</a>
  </div>
  <div class="buttn-footer">Powered by {_buttn_logo_html("black", "buttn-footer-logo")}</div>
</div>
<script>
function copyText(value) {{
    const status = document.getElementById("copy_status");
    if (!navigator.clipboard) {{
        if (status) status.textContent = "Copy not available on this browser.";
        return;
    }}
    navigator.clipboard.writeText(value).then(function() {{
        if (status) status.textContent = "Copied.";
    }}).catch(function() {{
        if (status) status.textContent = "Copy failed. Press and hold the text to copy.";
    }});
}}
</script>
</body>
</html>
"""


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
    username = _normalize_buttn_url(username or "test") or "test"
    profile = _get_profile(username)
    profile_has_pro_access = _profile_has_pro_access(username)
    _record_profile_view(username)
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
    action_buttons += f'<a class="action-btn" href="/buttn/contact/{html.escape(_normalize_buttn_url(username))}">Contact Info</a>'

    links_html = ""
    for idx, item in enumerate(profile.get("links", []), start=1):
        label_raw = (item.get("label") or "").strip()
        url_raw = (item.get("url") or "").strip()
        label = html.escape(label_raw)
        safe_destination = _safe_url(url_raw) if url_raw else ""
        if safe_destination:
            quoted_destination = urllib.parse.quote(safe_destination, safe="")
            url = f"/buttn/click/{html.escape(_normalize_buttn_url(username))}/{idx}?u={quoted_destination}"
        else:
            url = "#"
        icon_key = _normalize_link_icon(item.get("icon") or _guess_icon_from_label(label_raw))

        # IMPORTANT BUG FIX:
        # The live editor preview already shows a button when the label exists,
        # even if the URL is blank. The saved/public page must behave the same way.
        # Before this, the public page required BOTH label and URL, so blank-URL
        # buttons disappeared after Save & Preview.
        if label:
            target_attr = ' target="_blank" rel="noopener"' if url != "#" else ""
            links_html += f'<a class="buttn-link" href="{url}"{target_attr}>{_link_icon_html(icon_key)}<span class="buttn-link-label">{label}</span></a>'

    if not links_html:
        links_html = '<div class="empty-note">No links have been added yet.</div>'

    spotlight_html = ""
    spotlight_player_modal_html = ""
    if profile.get("spotlight_enabled"):
        spotlight_headline_raw = (profile.get("spotlight_headline") or "").strip()
        spotlight_image_b64 = (profile.get("spotlight_image_b64") or "").strip()
        spotlight_url_raw = (profile.get("spotlight_url") or "").strip()
        spotlight_shape = _normalize_spotlight_media_shape(profile.get("spotlight_media_shape"))
        spotlight_aspect = _spotlight_aspect_style(spotlight_shape)

        if spotlight_headline_raw or spotlight_image_b64 or spotlight_url_raw:
            spotlight_headline = html.escape(spotlight_headline_raw or "Featured")
            spotlight_subtext = html.escape((profile.get("spotlight_subtext") or "").strip())
            spotlight_subtext_html = f'<div class="spotlight-subtext">{spotlight_subtext}</div>' if spotlight_subtext else ""
            safe_spotlight_url = _safe_url(spotlight_url_raw) if spotlight_url_raw else ""

            # FREE PLAN PUBLIC PROFILE:
            # Keep Spotlight as a simple image/link card only.
            # No automatic thumbnails, no play overlay, no embedded video, no autoplay.
            if not profile_has_pro_access:
                spotlight_image_html = ""
                if spotlight_image_b64:
                    spotlight_image_html = f'<div class="spotlight-image-wrap spotlight-shape-{html.escape(spotlight_shape)}" style="aspect-ratio:{html.escape(spotlight_aspect)};"><img src="data:image/png;base64,{html.escape(spotlight_image_b64)}" alt="Featured Spotlight"></div>'

                spotlight_copy = f'<div class="spotlight-copy"><div class="spotlight-kicker">Featured</div><h2>{spotlight_headline}</h2>{spotlight_subtext_html}</div>'
                spotlight_inner = f'{spotlight_image_html}{spotlight_copy}'

                if safe_spotlight_url:
                    spotlight_html = f'<a class="spotlight-card" href="{html.escape(safe_spotlight_url)}" target="_blank" rel="noopener">{spotlight_inner}</a>'
                else:
                    spotlight_html = f'<div class="spotlight-card">{spotlight_inner}</div>'

            # PRO PLAN PUBLIC PROFILE:
            # Keep the existing full video/thumbnail/autoplay behavior.
            else:
                spotlight_behavior = _normalize_spotlight_open_behavior(profile.get("spotlight_open_behavior"))
                spotlight_autoplay = bool(profile.get("spotlight_autoplay"))
                spotlight_image_html = ""
                spotlight_auto_thumb = _spotlight_thumbnail_url(safe_spotlight_url) if safe_spotlight_url else ""
                play_html = '<div class="spotlight-play">▶</div>' if profile.get("spotlight_show_play") else ""

                if spotlight_image_b64:
                    spotlight_image_html = f'<div class="spotlight-image-wrap spotlight-shape-{html.escape(spotlight_shape)}" style="aspect-ratio:{html.escape(spotlight_aspect)};"><img src="data:image/png;base64,{html.escape(spotlight_image_b64)}" alt="Featured Spotlight">{play_html}</div>'
                elif spotlight_auto_thumb:
                    spotlight_image_html = f'<div class="spotlight-image-wrap spotlight-shape-{html.escape(spotlight_shape)}" style="aspect-ratio:{html.escape(spotlight_aspect)};"><img src="{html.escape(spotlight_auto_thumb)}" alt="Featured Spotlight">{play_html}</div>'

                spotlight_copy = f'<div class="spotlight-copy"><div class="spotlight-kicker">Featured</div><h2>{spotlight_headline}</h2>{spotlight_subtext_html}</div>'

                if safe_spotlight_url and spotlight_behavior == "play_page":
                    autoplay_embed_url = _spotlight_embed_url(safe_spotlight_url, autoplay=spotlight_autoplay, muted=spotlight_autoplay)
                    click_embed_url = _spotlight_embed_url(safe_spotlight_url, autoplay=True, muted=False)
                    open_original_html = f'<a class="spotlight-open-original-inline" href="{html.escape(safe_spotlight_url)}" target="_blank" rel="noopener">Open Original</a>'

                    if autoplay_embed_url:
                        if spotlight_autoplay:
                            spotlight_media_html = f"""
                            <div class="spotlight-image-wrap spotlight-player-inline spotlight-shape-{html.escape(spotlight_shape)}" style="aspect-ratio:{html.escape(spotlight_aspect)};">
                              <iframe src="{html.escape(autoplay_embed_url)}" title="Featured Spotlight" allow="autoplay; fullscreen; picture-in-picture; encrypted-media" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>
                            </div>
                            """
                        else:
                            default_play_html = play_html or '<div class="spotlight-play">▶</div>'
                            poster_html = spotlight_image_html or f'<div class="spotlight-image-wrap spotlight-shape-{html.escape(spotlight_shape)} spotlight-empty-media" style="aspect-ratio:{html.escape(spotlight_aspect)};">{default_play_html}</div>'
                            spotlight_media_html = f"""
                            <button type="button" class="spotlight-inline-trigger" data-src="{html.escape(click_embed_url)}" data-aspect="{html.escape(spotlight_aspect)}" onclick="loadSpotlightInline(this)" aria-label="Play Featured Spotlight">
                              {poster_html}
                            </button>
                            """
                        spotlight_html = f'<div class="spotlight-card spotlight-inline-card">{spotlight_media_html}{spotlight_copy}{open_original_html}</div>'
                    else:
                        spotlight_inner = f'{spotlight_image_html}{spotlight_copy}'
                        spotlight_html = f'<a class="spotlight-card" href="{html.escape(safe_spotlight_url)}" target="_blank" rel="noopener">{spotlight_inner}</a>'
                else:
                    spotlight_inner = f'{spotlight_image_html}{spotlight_copy}'
                    if safe_spotlight_url and spotlight_behavior == "same_page":
                        spotlight_html = f'<a class="spotlight-card" href="{html.escape(safe_spotlight_url)}">{spotlight_inner}</a>'
                    elif safe_spotlight_url:
                        spotlight_html = f'<a class="spotlight-card" href="{html.escape(safe_spotlight_url)}" target="_blank" rel="noopener">{spotlight_inner}</a>'
                    else:
                        spotlight_html = f'<div class="spotlight-card">{spotlight_inner}</div>'


    lead_capture_html = ""
    if profile.get("lead_capture_enabled"):
        if (request.args.get("lead") or "").strip().lower() == "thanks":
            lead_capture_html = """
            <div class="lead-capture-card lead-success-card">
              <div class="lead-success-icon">✓</div>
              <h2>Thanks!</h2>
              <p>We've got your information and will keep in touch.</p>
            </div>
            """
        else:
            lead_headline = html.escape(profile.get("lead_capture_headline") or "Stay Connected")
            lead_button_text = html.escape(profile.get("lead_capture_button_text") or "Submit")
            lead_capture_html = f"""
            <div class="lead-capture-card">
              <h2>{lead_headline}</h2>
              <form method="post" action="/buttn/lead/{html.escape(_normalize_buttn_url(username))}">
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
