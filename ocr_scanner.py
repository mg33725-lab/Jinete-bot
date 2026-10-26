import os
import re
import io
import json
from typing import List, Dict, Optional, Tuple
from PIL import Image

_reader = None

EXCLUDED_WORDS = {
    "party", "leader", "member", "level", "lvl", "hp", "mp", "exp", "life", "sd", "ag",
    "lorencia", "noria", "devias", "dungeon", "atlans", "losttower", "tarkan", "aida",
    "icarus", "kanturu", "crywolf", "barracks", "refuge", "elbeland", "raklion", "vulcanus",
    "karutan", "argenmu", "mu", "blood", "castle", "chaos", "bc", "ds", "cc", "server"
}

def crop_party_corner(image: Image.Image) -> Image.Image:
    """
    En MU Online la ventana de Party está en la esquina superior derecha.
    Usamos un recorte generoso (último 32% del ancho y primeros 55% del alto)
    para cubrir partys completas de 7 a 10 miembros y pantallas de PC o Mobile/APK.
    """
    w, h = image.size
    crop_box = (int(w * 0.68), 0, w, int(h * 0.55))
    return image.crop(crop_box)

def parse_with_gemini(
    images_bytes: List[bytes],
    user_text: str,
    api_key: str
) -> Optional[Dict]:
    """
    Usa Gemini 3.5 Flash Lite para procesar en lenguaje natural el mensaje del usuario
    y una o múltiples capturas de pantalla de partys (ej: Party 1 + Party 2).
    Devuelve: {"event_name": str, "darks": int, "characters": [str, ...]}
    """
    try:
        from google import genai
        client = genai.Client(api_key=api_key)

        contents = []

        if isinstance(images_bytes, bytes):
            images_bytes = [images_bytes]

        for b in images_bytes:
            if b:
                image = Image.open(io.BytesIO(b)).convert("RGB")
                cropped_img = crop_party_corner(image)
                contents.append(cropped_img)

        prompt = f"""
Eres un asistente del gremio de ArgenMU (MU Online).
Tu tarea es analizar el mensaje del usuario y la captura de la party (si se adjunta).

Mensaje del usuario: "{user_text}"

Reglas:
1. Extrae el nombre del evento:
   - Ej: "BC 20:30", "Blood Castle", "Frozen", "Narcondra", "DS", "Invasión", etc. Si no se deduce, usa "Evento ArgenMU".
2. Determina la cantidad de Darks caídas:
   - Busca números asociados a 'dark', 'darks', 'joyas' o puntos. Si no especifica cantidad, por defecto es 1.
3. Extrae la lista de personajes (PJs) que participaron:
   - Si hay una imagen adjunta, lee con precisión quirúrgica los nombres dentro de la ventana negra de 'Party' (borde dorado), respetando mayúsculas, minúsculas y números (como chspi74, KodaKai, masterr, Buferit4, cholitagom, unanime2, THEROCKkk).
   - ¡INTEGRACIÓN PARTY 1 + PARTY 2!: Si el usuario envía la captura de la Party 1 y en el texto especifica la Party 2 (ej: "Party 2: TheMami / Lorditachi", "P2: ...") o agrega nombres, DEBES UNIR OBLIGATORIAMENTE los personajes leídos en la imagen con los personajes escritos en el texto en la misma lista "characters".
   - Ignora palabras del juego como: "Obtuviste", "Ancient", "Coin", "Satan", "Angel", "Jewel", "Chaos", "Zen", "Party", "Level", "Server", "captura".
   - Si no hay imagen, extrae todos los nombres escritos en el mensaje del usuario.

Devuelve ÚNICAMENTE un JSON válido (sin bloques markdown de ```json ni comentarios extra) con esta estructura:
{{
  "event_name": "nombre del evento",
  "darks": 1,
  "characters": ["PJ1", "PJ2", "PJ3"]
}}
"""
        contents.append(prompt)

        CANDIDATE_MODELS = [
            "gemini-flash-lite-latest",
            "gemini-3.6-flash",
            "gemini-3.5-flash-lite",
            "gemini-3-flash-preview",
            "gemini-3.1-flash-lite",
        ]
        response = None
        for model_name in CANDIDATE_MODELS:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents
                )
                if response and response.text:
                    break
            except Exception as ex:
                print(f"Modelo {model_name} no disponible: {ex}")
                continue

        if not response or not response.text:
            return None

        raw = response.text.strip()
        # Limpiar posibles bloques ```json ... ```
        if raw.startswith("```"):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

        data = json.loads(raw)
        if isinstance(data, dict) and "characters" in data:
            return {
                "event_name": str(data.get("event_name", "Evento")).strip(),
                "darks": max(1, int(data.get("darks", 1))),
                "characters": [str(c).strip() for c in data.get("characters", []) if str(c).strip()]
            }
    except Exception as e:
        print(f"Error procesando con Gemini 3.5 Flash Lite: {e}")

    return None

def get_easyocr_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _reader

def extract_names_from_image(image_bytes: bytes) -> List[str]:
    """Fallback si solo se invoca OCR directo sobre la imagen."""
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key and gemini_key.strip():
        res = parse_with_gemini(image_bytes, "Extraer nombres de la party", gemini_key.strip())
        if res and res.get("characters"):
            return res["characters"]

    # Fallback EasyOCR
    try:
        import numpy as np
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        cropped = crop_party_corner(image)
        reader = get_easyocr_reader()
        np_img = np.array(cropped)
        results = reader.readtext(np_img)
        detected = []
        seen = set()
        for bbox, text, prob in results:
            cleaned = re.sub(r'[^a-zA-Z0-9_\-\[\]]', '', text.strip())
            if len(cleaned) >= 2 and cleaned.lower() not in EXCLUDED_WORDS and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                detected.append(cleaned)
        return detected
    except Exception as e:
        print(f"Error en EasyOCR: {e}")
        return []
