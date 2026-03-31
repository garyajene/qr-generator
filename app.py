# ONLY change is create_dome_mockup — everything else remains your master logic

def create_dome_mockup(qr_img):
    dome = Image.open("static/dome_piece1.png").convert("RGBA")
    dome_w, dome_h = dome.size

    qr_crop = trim_qr_for_mockup(qr_img)

    qr_target = int(min(dome_w, dome_h) * 0.44)
    qr_small = qr_crop.resize((qr_target, qr_target), Image.LANCZOS)

    zoom = 1.15
    qr_small = qr_small.resize(
        (int(qr_small.width * zoom), int(qr_small.height * zoom)),
        Image.LANCZOS
    )

    # Place QR on transparent canvas (NO background fill)
    qr_canvas = Image.new("RGBA", dome.size, (0, 0, 0, 0))

    qr_x = (dome_w - qr_small.width) // 2
    qr_y = (dome_h - qr_small.height) // 2

    qr_canvas.paste(qr_small, (qr_x, qr_y), qr_small)

    # 🔥 TRUE MASKING (this is the key fix)
    dome_alpha = dome.split()[-1]

    masked = Image.new("RGBA", dome.size, (0, 0, 0, 0))
    masked.paste(qr_canvas, (0, 0), dome_alpha)

    # Apply dome gloss AFTER masking
    masked.alpha_composite(dome)

    final_w = int(dome_w * 0.50)
    final_h = int(dome_h * 0.50)

    return masked.resize((final_w, final_h), Image.LANCZOS)
