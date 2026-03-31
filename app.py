def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    dome_w, dome_h = dome.size

    # ---------- GET BACKGROUND COLOR FROM FINAL QR ----------
    small = qr_img.resize((50, 50))
    pixels = list(small.getdata())

    colors = [(r, g, b) for r, g, b, a in pixels if a > 0]

    if colors:
        bg_color = Counter(colors).most_common(1)[0][0]
    else:
        bg_color = (255, 255, 255)

    # ---------- TRIM QR ----------
    qr_crop = trim_qr_for_mockup(qr_img)

    # ---------- SIZE ----------
    qr_target = int(min(dome_w, dome_h) * 0.42)
    qr_small = qr_crop.resize((qr_target, qr_target), Image.LANCZOS)

    # ---------- CREATE BACKGROUND (THIS IS THE FIX) ----------
    base = Image.new("RGBA", dome.size, (*bg_color, 255))

    qr_x = (dome_w - qr_small.width) // 2
    qr_y = (dome_h - qr_small.height) // 2

    # ---------- SAFE PASTE ----------
    if qr_small.mode == "RGBA":
        base.paste(qr_small, (qr_x, qr_y), qr_small)
    else:
        base.paste(qr_small, (qr_x, qr_y))

    # ---------- APPLY DOME ----------
    base.alpha_composite(dome)

    # ---------- FINAL SCALE ----------
    final_w = int(dome_w * 0.50)
    final_h = int(dome_h * 0.50)

    return base.resize((final_w, final_h), Image.LANCZOS)
