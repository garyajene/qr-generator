def create_card_mockup(qr_img):
    card = Image.open("static/blackcard.png").convert("RGBA")
    qr_crop = trim_qr_for_mockup(qr_img)

    card_w, card_h = card.size

    qr_target_w = int(card_w * 0.32)
    qr_target_h = qr_target_w
    qr_small = qr_crop.resize((qr_target_w, qr_target_h), Image.LANCZOS)

    # --- NEW: rounded corners only ---
    radius = int(qr_target_w * 0.08)

    rounded_mask = Image.new("L", (qr_target_w, qr_target_h), 0)
    mask_draw = ImageDraw.Draw(rounded_mask)

    mask_draw.rounded_rectangle(
        (0, 0, qr_target_w, qr_target_h),
        radius=radius,
        fill=255
    )

    rounded_qr = Image.new(
        "RGBA",
        (qr_target_w, qr_target_h),
        (0, 0, 0, 0)
    )

    rounded_qr.paste(qr_small, (0, 0))
    rounded_qr.putalpha(rounded_mask)

    margin_x = int(card_w * 0.05)
    margin_y = int(card_h * 0.07)

    qr_x = card_w - qr_target_w - margin_x
    qr_y = card_h - qr_target_h - margin_y

    card.paste(
        rounded_qr,
        (qr_x, qr_y),
        rounded_qr
    )

    return card
