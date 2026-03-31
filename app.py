def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    dome_w, dome_h = dome.size

    qr_crop = trim_qr_for_mockup(qr_img)

    # --- NEW: extract background color from QR ---
    bg_color = qr_crop.getpixel((5, 5))[:3]

    # --- NEW: fill entire dome base with that color ---
    base = Image.new("RGBA", dome.size, (*bg_color, 255))

    qr_target = int(min(dome_w, dome_h) * 0.44)
    qr_small = qr_crop.resize((qr_target, qr_target), Image.LANCZOS)

    zoom = 1.15
    zoomed_w = int(qr_small.width * zoom)
    zoomed_h = int(qr_small.height * zoom)
    qr_small = qr_small.resize((zoomed_w, zoomed_h), Image.LANCZOS)

    qr_x = (dome_w - qr_small.width) // 2
    qr_y = (dome_h - qr_small.height) // 2

    base.paste(qr_small, (qr_x, qr_y), qr_small)

    # overlay dome
    base.alpha_composite(dome, (0, 0))

    final_w = int(dome_w * 0.50)
    final_h = int(dome_h * 0.50)

    return base.resize((final_w, final_h), Image.LANCZOS)
