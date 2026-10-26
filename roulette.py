import math
import os
from typing import List, Tuple, Optional, Union
from PIL import Image, ImageDraw, ImageFont

# Paleta de colores atractiva para los sectores de la ruleta
SLICE_COLORS = [
    "#E74C3C",  # Rojo
    "#2980B9",  # Azul
    "#27AE60",  # Verde
    "#F39C12",  # Dorado/Naranja
    "#8E44AD",  # Violeta
    "#16A085",  # Turquesa
    "#D35400",  # Cobre
    "#C0392B",  # Carmesí
    "#1F618D",  # Azul noche
    "#229954",  # Esmeralda
    "#B7950B",  # Oro
    "#6C3483",  # Púrpura
]

def get_font(size: int):
    # Intentar cargar fuentes estándar de Windows o fallback a la predeterminada
    font_paths = [
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf"
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

def draw_base_wheel(participants: List[str], size: int = 400) -> Image.Image:
    """
    Dibuja la rueda base de la ruleta en alta resolución.
    """
    # Dibujar al doble de tamaño para antialiasing (supersampling)
    scale = 2
    img_size = size * scale
    img = Image.new("RGBA", (img_size, img_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    center = (img_size // 2, img_size // 2)
    radius = int(img_size * 0.46)
    n = len(participants)
    angle_per_slice = 360.0 / n

    # 1. Borde exterior dorado con remaches
    draw.ellipse([center[0] - radius - 16, center[1] - radius - 16, center[0] + radius + 16, center[1] + radius + 16], fill="#B8860B")
    draw.ellipse([center[0] - radius - 8, center[1] - radius - 8, center[0] + radius + 8, center[1] + radius + 8], fill="#1E1F22")
    draw.ellipse([center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius], fill="#D4AF37")

    font_size = max(18, min(32, int(240 / max(1, math.sqrt(n)))))
    font = get_font(font_size)

    # 2. Dibujar sectores (slices) y nombres
    for i, name in enumerate(participants):
        start_angle = i * angle_per_slice
        end_angle = (i + 1) * angle_per_slice
        color = SLICE_COLORS[i % len(SLICE_COLORS)]

        # Dibujar porción de pastel (pie slice)
        draw.pieslice(
            [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
            start=start_angle,
            end=end_angle,
            fill=color,
            outline="#FFFFFF",
            width=2
        )

        # 3. Dibujar texto del nombre rotado hacia el centro
        mid_angle = math.radians(start_angle + angle_per_slice / 2)
        text_radius = radius * 0.65
        tx = center[0] + text_radius * math.cos(mid_angle)
        ty = center[1] + text_radius * math.sin(mid_angle)

        # Crear una pequeña imagen para rotar el texto de este sector
        display_name = name[:12] + ("." if len(name) > 12 else "")
        # Medir tamaño del texto
        bbox = draw.textbbox((0, 0), display_name, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        txt_img = Image.new("RGBA", (tw + 20, th + 20), (0, 0, 0, 0))
        txt_draw = ImageDraw.Draw(txt_img)
        # Sombra y texto blanco
        txt_draw.text((11, 11), display_name, font=font, fill="#000000")
        txt_draw.text((10, 10), display_name, font=font, fill="#FFFFFF")

        # Ángulo de rotación del texto (orientado hacia afuera)
        deg = math.degrees(mid_angle)
        if 90 < (deg % 360) < 270:
            rotated_txt = txt_img.rotate(deg + 180, expand=True, resample=Image.BICUBIC)
        else:
            rotated_txt = txt_img.rotate(deg, expand=True, resample=Image.BICUBIC)

        rx = int(tx - rotated_txt.width / 2)
        ry = int(ty - rotated_txt.height / 2)
        img.paste(rotated_txt, (rx, ry), rotated_txt)

    # 4. Centro / Eje de la ruleta (Hub dorado y negro)
    hub_radius = int(radius * 0.22)
    draw.ellipse([center[0] - hub_radius, center[1] - hub_radius, center[0] + hub_radius, center[1] + hub_radius], fill="#D4AF37", outline="#FFFFFF", width=3)
    draw.ellipse([center[0] - hub_radius + 8, center[1] - hub_radius + 8, center[0] + hub_radius - 8, center[1] + hub_radius - 8], fill="#1E1F22")

    hub_font = get_font(22)
    hub_text = "MU"
    hbbox = draw.textbbox((0, 0), hub_text, font=hub_font)
    draw.text((center[0] - (hbbox[2] - hbbox[0]) // 2, center[1] - (hbbox[3] - hbbox[1]) // 2 - 2), hub_text, font=hub_font, fill="#D4AF37")

    return img.resize((size, size), Image.LANCZOS)

def generate_roulette_gif(
    participants: List[str],
    winner_name: Optional[str] = None,
    winner_names: Optional[List[str]] = None,
    output_path: str = "ruleta.gif",
    size: int = 400,
    is_loser_mode: bool = False,
    loser_label: str = "NO SUMA",
    winner_label: str = "TIRO"
) -> str:
    """
    Genera un GIF animado de la ruleta.
    Si hay múltiples objetivos (ganadores o perdedores),
    realiza giros sucesivos para CADA uno, mostrando cada tiro.
    Si is_loser_mode es True, muestra la ruleta indicando quiénes no suman punto.
    """
    n = len(participants)
    if n == 0:
        return ""

    targets: List[str] = []
    if winner_names:
        targets = [str(w).strip() for w in winner_names if str(w).strip()]
    elif winner_name:
        targets = [winner_name.strip()]

    if not targets:
        return ""

    angle_per_slice = 360.0 / n
    base_wheel = draw_base_wheel(participants, size=size)

    frames = []
    durations = []

    def ease_out(t: float) -> float:
        return 1.0 - math.pow(1.0 - t, 3.2)

    current_start_angle = 0.0
    total_spins = len(targets)

    # Si hay muchos ganadores/perdedores (ej: más de 2), animar hasta un máximo de 2 giros
    # para evitar GIFs pesados (>5 MB) que demoran varios segundos y provocan timeouts en Discord.
    spins_to_render = targets[:2] if len(targets) > 2 else targets

    for spin_idx, target_w in enumerate(spins_to_render):
        winner_idx = 0
        for idx, p in enumerate(participants):
            if p.lower() == target_w.lower():
                winner_idx = idx
                break

        winner_slice_center = winner_idx * angle_per_slice + (angle_per_slice / 2.0)
        target_final_angle = (winner_slice_center + 90.0) % 360.0

        diff = (target_final_angle - (current_start_angle % 360.0)) % 360.0
        if diff < 120.0:
            diff += 360.0
        spin_degrees = 360.0 * 2 + diff

        spin_frames = 18
        for frame_i in range(spin_frames):
            progress = frame_i / float(spin_frames - 1)
            angle = current_start_angle + spin_degrees * ease_out(progress)

            rotated_wheel = base_wheel.rotate(angle, resample=Image.BICUBIC)
            canvas = Image.new("RGBA", (size, size + 50), (30, 31, 34, 255))
            canvas.paste(rotated_wheel, (0, 35), rotated_wheel)

            draw = ImageDraw.Draw(canvas)

            top_center = size // 2
            needle_top = 10
            needle_tip = 55
            needle_width = 16

            draw.polygon(
                [(top_center - needle_width, needle_top),
                 (top_center + needle_width, needle_top),
                 (top_center, needle_tip)],
                fill="#E74C3C",
                outline="#F1C40F",
            )

            if frame_i >= spin_frames - 3:
                banner_y = size + 10
                banner_font = get_font(14 if total_spins > 1 else 18)
                is_last_render = (spin_idx == len(spins_to_render) - 1)
                extra_cnt = total_spins - len(spins_to_render)

                if is_loser_mode:
                    if is_last_render and extra_cnt > 0:
                        banner_text = f"💀 {target_w.upper()} (+{extra_cnt} más afuera)"
                    elif total_spins > 1:
                        banner_text = f"💀 {loser_label} ({spin_idx + 1}/{total_spins}): {target_w.upper()}"
                    else:
                        banner_text = f"💀 {loser_label}: {target_w.upper()}"
                    fill_color = "#C0392B"
                    txt_color = "#FFFFFF"
                else:
                    if is_last_render and extra_cnt > 0:
                        banner_text = f"🎯 {target_w.upper()} (+{extra_cnt} ganadores más)"
                    elif total_spins > 1:
                        suffix = " (+1 pt)" if winner_label == "TIRO" else ""
                        banner_text = f"🎯 {winner_label} {spin_idx + 1}/{total_spins}: {target_w.upper()}{suffix}"
                    else:
                        banner_text = f"🏆 ¡GANADOR: {target_w.upper()}!"
                    fill_color = "#F1C40F"
                    txt_color = "#000000"

                t_bbox = draw.textbbox((0, 0), banner_text, font=banner_font)
                tw = t_bbox[2] - t_bbox[0]
                draw.rectangle([top_center - tw // 2 - 12, banner_y - 2, top_center + tw // 2 + 12, banner_y + 24], fill=fill_color, outline="#000000")
                draw.text((top_center - tw // 2, banner_y), banner_text, font=banner_font, fill=txt_color)

            frames.append(canvas.convert("RGB"))

            if frame_i < 6:
                durations.append(40)
            elif frame_i < 12:
                durations.append(70)
            elif frame_i < spin_frames - 1:
                durations.append(120)
            else:
                durations.append(1600 if total_spins > 1 else 2200)

        current_start_angle += spin_degrees

    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True
    )

    return output_path
