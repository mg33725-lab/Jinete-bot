import os
import sys
import io
import re
import asyncio
import http.server
import threading
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
import socket
from typing import Optional, List, Tuple, Union, Dict, Any
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Evitar múltiples instancias simultáneas del bot (bloqueo por socket local)
_instance_lock = None
def ensure_single_instance(port: int = 48123):
    global _instance_lock
    _instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _instance_lock.bind(('127.0.0.1', port))
    except socket.error:
        print("⚠️ Ya hay una instancia de Jinete Bot ejecutándose en esta computadora.", flush=True)
        print("Cerrando este proceso para evitar respuestas y mensajes duplicados en Discord.", flush=True)
        sys.exit(0)

# ensure_single_instance() se ejecuta bajo if __name__ == '__main__'

import database as db
import logic
import roulette
from ocr_scanner import extract_names_from_image

# Cargar variables de entorno (.env)
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Set para evitar colisiones cuando un usuario está esperando subir una segunda captura
ACTIVE_CAPTURE_WAITERS = set()

# ====================================================================
# SERVIDOR WEB DE MONITOREO (HEALTH CHECK PARA RENDER / 24-7 CLOUD)
# ====================================================================

class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"<h1>Jinete Bot ArgenMU Online 24/7</h1><p>Status: OK</p>")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass # Silenciar logs de pings periodicos en consola

def start_health_check_server():
    port_str = os.environ.get("PORT")
    if not port_str:
        return
    try:
        port = int(port_str)
        server = http.server.HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"🌐 Servidor web de monitoreo activo en puerto {port} (Render / Cloud)", flush=True)
    except Exception as ex:
        print(f"No se pudo iniciar el servidor web de monitoreo: {ex}", flush=True)

# ====================================================================
# PERSISTENCIA Y AUTO-BACKUP DE BASE DE DATOS EN LA NUBE (DISCORD)
# ====================================================================

async def auto_backup_database():
    """Envía una copia de respaldo de argenmu.db al canal de backup si está configurado."""
    backup_id = os.getenv("BACKUP_CHANNEL_ID")
    if not backup_id or not backup_id.strip().isdigit():
        return
    try:
        channel = bot.get_channel(int(backup_id.strip()))
        if not channel:
            channel = await bot.fetch_channel(int(backup_id.strip()))
        if channel and os.path.exists("argenmu.db"):
            await channel.send(
                content=f"💾 **[Auto-Backup] Base de Datos Jinete Bot** (`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`)",
                file=discord.File("argenmu.db", filename="argenmu.db")
            )
    except Exception as e:
        logger.warning(f"Error en auto_backup_database: {e}")

async def auto_restore_database_on_boot():
    """Restaura la última versión de argenmu.db desde el canal de backup si existe."""
    backup_id = os.getenv("BACKUP_CHANNEL_ID")
    if not backup_id or not backup_id.strip().isdigit():
        return
    try:
        channel = bot.get_channel(int(backup_id.strip()))
        if not channel:
            channel = await bot.fetch_channel(int(backup_id.strip()))
        if channel:
            async for msg in channel.history(limit=25):
                for att in msg.attachments:
                    if att.filename == "argenmu.db":
                        print(f"📥 Restaurando última base de datos desde Discord ({att.filename}, {att.size} bytes)...", flush=True)
                        await att.save("argenmu.db")
                        return
    except Exception as e:
        logger.warning(f"Error en auto_restore_database_on_boot: {e}")

# ====================================================================
# CONTROL DE PERMISOS DE ADMINISTRADOR (SOLO USUARIOS AUTORIZADOS)
# ====================================================================

def check_is_admin(user: Union[discord.User, discord.Member], guild: Optional[discord.Guild] = None) -> bool:
    """Verifica si el usuario tiene permiso para cargar eventos o modificar datos."""
    is_guild_admin = False
    is_guild_owner = False
    if isinstance(user, discord.Member):
        is_guild_admin = user.guild_permissions.administrator
        if guild and guild.owner_id == user.id:
            is_guild_owner = True
    elif guild and guild.owner_id == user.id:
        is_guild_owner = True

    return db.is_admin(
        discord_id=user.id,
        username=user.name,
        is_guild_admin=is_guild_admin,
        is_guild_owner=is_guild_owner
    )

GAME_STOP_WORDS = {
    'party', 'captura', 'cayeron', 'cayo', 'callo', 'dark', 'darks', 'evento', 'puntos',
    'bc', 'bc1', 'bc2', 'bc3', 'bc4', 'bc5', 'bc6', 'bc7', 'bc8',
    'ds', 'ds1', 'ds2', 'ds3', 'ds4', 'ds5', 'ds6',
    'cc', 'cc1', 'cc2', 'cc3', 'cc4', 'cc5', 'cc6',
    'sv', 'sv1', 'sv2', 'sv3', 'sv4', 'sv5', 'server', 'srv', 'sub',
    'blood', 'castle', 'chaos', 'frozen', 'invasion', 'kundun', 'selupan', 'medusa',
    'dxmentor', 'jinete', 'mentores', 'pollokill', 'clan', 'alianza',
    'obtuviste', 'ancient', 'coin', 'satan', 'angel', 'guardian', 'seal', 'sustenance',
    'jewel', 'bless', 'soul', 'life', 'creation', 'zen', 'box',
    'fps', 'ping', 'level', 'lvl', 'reset', 'resets',
    'fecha', 'hora', 'hs', 'min', 'seg', 'unica', 'solo', 'para', 'tiene', 'tienen',
    'vendo', 'compro', 'jajaja', 'jaja', 'despues', 'vemos', 'todo', 'todos', 'drop', 'dropearon',
    'nos', 'me', 'te', 'se', 'ya', 'yo', 'tu', 'el', 'la', 'los', 'las', 'que', 'con', 'del', 'al'
}
STOP_WORDS_P2 = GAME_STOP_WORDS

def is_valid_character_name(name: str) -> bool:
    clean = re.sub(r'[^a-zA-Z0-9_\-\./]', '', name).strip(' .-_:')
    if not clean or len(clean) < 3:
        return False
    if clean.lower() in GAME_STOP_WORDS:
        return False
    # Evitar fechas y números (ej: 19/10/26, 6:30, 630-)
    if re.match(r'^\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?$', clean):
        return False
    if re.match(r'^\d+[\-:]?\d*$', clean):
        return False
    if any(term in clean.lower() for term in ['angel', 'satan', 'sustenance', 'ancient', 'jewel', 'vendo', 'jaja', 'cayeron']):
        return False
    return True

def extract_party2_names(txt: str) -> List[str]:
    """Extrae nombres de personajes explícitamente escritos para la Party 2 o agregados."""
    found = []
    lines = txt.split('\n')
    is_p2 = False
    for line in lines:
        cleaned_line = line.strip()
        if re.search(r'\b(?:party\s*2|p2|segunda\s+party|otra\s+party|adem[aá]s)\b', cleaned_line, re.IGNORECASE):
            is_p2 = True
            sub = re.split(r'\b(?:party\s*2|p2|segunda\s+party|otra\s+party|adem[aá]s)\s*[:\-]?', cleaned_line, flags=re.IGNORECASE)
            if len(sub) > 1 and sub[1].strip():
                for token in re.split(r'[/,;\s]+', sub[1]):
                    t = re.sub(r'[^a-zA-Z0-9_\-\[\]]', '', token).strip()
                    if is_valid_character_name(t):
                        found.append(t)
            continue
        if is_p2:
            if re.search(r'\b(bc\d*|ds\d*|cc\d*|frozen|darks?|cayeron|\d{1,2}\s*:\s*\d{2})\b', cleaned_line, re.IGNORECASE):
                is_p2 = False
                continue
            for token in re.split(r'[/,;\s]+', cleaned_line):
                t = re.sub(r'[^a-zA-Z0-9_\-\[\]]', '', token).strip()
                if is_valid_character_name(t):
                    found.append(t)
    return found

def parse_clan_characters(text: str) -> Dict[str, List[str]]:
    """Extrae personajes organizados por clan desde textos con una o varias listas explícitas (ej: Mentores y Otro Clan)."""
    clan_map = {}
    current_clan = None
    has_explicit_clan = False

    lines = text.split("\n")
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue

        m_otro_clan = re.match(r"^(?:otro\s+clan|el\s+otro\s+clan|aliados?|clan\s+aliado)\s*[:\-]?(.*)$", cleaned, re.IGNORECASE)
        m_inline_clan = re.match(r"^(?:clan|alianza)\s+([A-Za-z0-9_]{3,20})\s*[:\-]?(.*)$", cleaned, re.IGNORECASE)
        m_header = re.match(r"^(?:clan\s+|alianza\s+|party\s*\d*\s*[:\-]?\s*)?([A-Za-z0-9_\s]{2,25})\s*[:\-]\s*$", cleaned, re.IGNORECASE)

        if m_otro_clan:
            current_clan = "Otro Clan"
            has_explicit_clan = True
            rest = m_otro_clan.group(1).strip()
            if rest:
                cleaned = rest
            else:
                continue
        elif m_inline_clan:
            cand = m_inline_clan.group(1).strip().title()
            if cand.lower() not in GAME_STOP_WORDS:
                current_clan = cand
                has_explicit_clan = True
            rest = m_inline_clan.group(2).strip()
            if rest:
                cleaned = rest
            else:
                continue
        elif m_header:
            cand = m_header.group(1).strip()
            if cand.lower().startswith("clan "):
                cand = cand[5:].strip()
            if cand.lower() not in GAME_STOP_WORDS and not cand.isdigit() and len(cand) >= 3:
                current_clan = cand.title()
                has_explicit_clan = True
                continue
        elif re.match(r"^(mentores|clan\s*mentores)\b", cleaned, re.IGNORECASE):
            current_clan = "Mentores"
            has_explicit_clan = True
            cleaned = re.sub(r"^(mentores|clan\s*mentores)\s*[:\-]?", "", cleaned, flags=re.IGNORECASE).strip()
            if not cleaned:
                continue

        # Solo procesar personajes si estamos dentro de una sección explícitamente declarada de clan
        if current_clan and has_explicit_clan:
            for token in re.split(r"[/,;\s]+", cleaned):
                if is_valid_character_name(token):
                    t = re.sub(r"[^a-zA-Z0-9_\-\[\]]", "", token).strip()
                    if current_clan not in clan_map:
                        clan_map[current_clan] = []
                    if t.lower() not in [x.lower() for x in clan_map[current_clan]]:
                        clan_map[current_clan].append(t)

    return clan_map

def parse_roster_text(text: str) -> Tuple[List[Dict], Optional[str]]:
    """
    Parsea listas completas de personajes con sus puntos organizadas por clanes y con notas (ej: PolloKill / DxMENTOR).
    Detecta automáticamente dobles personajes (ej: Spark/Chispa), notas de Darks (ej: 24p (2D)) y fecha de corte (ej: 18/10).
    """
    current_clan = "Mentores"
    items = []
    seen = set()
    cutoff_date = None

    # Detectar mención de fecha de corte (ej: 'hasta la fecha del 18/10', 'al 18/10/2026')
    m_date = re.search(r'(?:hasta|al|del|fecha|corte)\s*(?:el|la)?\s*(?:fecha\s*(?:del?)?)?\s*(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)', text, re.I)
    if m_date:
        cutoff_date = m_date.group(1).replace('-', '/')

    for line in text.strip().split("\n"):
        l = line.strip()
        if not l:
            continue
        m = re.match(r"^([A-Za-z0-9_\-\./]+)\s+(\d+)\s*[pP]?\s*(?:\((.*?)\))?$", l)
        if m:
            pj = m.group(1).rstrip(".")
            pts = int(m.group(2))
            note = (m.group(3) or "").strip()
            key = (pj.lower(), current_clan.lower())
            if key not in seen:
                seen.add(key)
                items.append({"pj_name": pj, "points": pts, "note": note, "clan": current_clan})
        else:
            cand = re.sub(r"^(clan|alianza)\s+", "", l, flags=re.I).strip()
            # Si no contiene números con 'p' y no es una frase explicativa ni encabezado de fecha, es un clan
            if (len(cand) >= 2 and not re.search(r"\d+\s*[pP]", cand) and
                not any(cand.lower().startswith(w) for w in [
                    "este es", "hay personas", "una vez", "lo que dice", "asimismo",
                    "recuerden", "nota:", "hasta", "fecha", "actualizado", "lista"
                ])):
                current_clan = cand
    return items, cutoff_date

def build_roster_preview_embed(items: List[Dict], cutoff_date: Optional[str] = None) -> discord.Embed:
    clans_dict = {}
    total_darks_owed = 0
    alias_detected = []

    for item in items:
        c = item.get("clan") or "Mentores"
        clans_dict.setdefault(c, []).append(item)
        if item["points"] >= 10:
            total_darks_owed += item["points"] // 10
        if "/" in item["pj_name"]:
            alias_detected.append(item["pj_name"])

    desc_lines = [
        f"Se detectaron **{len(items)} personajes** organizados en **{len(clans_dict)} clan(es)**."
    ]
    if cutoff_date:
        desc_lines.append(f"📅 **Fecha de corte / actualización:** **{cutoff_date}**")
    desc_lines.extend([
        f"💎 **Darks alcanzadas para cobrar (≥10 pts):** **{total_darks_owed} Dark{'s' if total_darks_owed != 1 else ''}**\n",
        "¿Deseas **Fijar / Inicializar** estos puntos como base del gremio, o **Sumar** a los puntos actuales?"
    ])

    embed = discord.Embed(
        title="📋 Detección de Lista de Personajes y Puntos",
        description="\n".join(desc_lines),
        color=discord.Color.gold()
    )

    for clan_name, members in clans_dict.items():
        icon = "🛡️" if "mentor" in clan_name.lower() else "⚔️"
        lines = []
        for m in members:
            pts = m["points"]
            note_str = f" `({m['note']})`" if m.get("note") else ""
            dark_str = f" ➔ 💎 **{pts // 10}D por cobrar** (quedarán {pts % 10}p)" if pts >= 10 else ""
            lines.append(f"• **{m['pj_name']}**: {pts} pts{note_str}{dark_str}")

        chunk = "\n".join(lines)
        if len(chunk) > 1000:
            chunk = chunk[:990] + "\n...(más)"
        embed.add_field(name=f"{icon} Clan {clan_name} ({len(members)} PJs)", value=chunk, inline=False)

    if alias_detected:
        embed.add_field(
            name="👥 Doble Personaje / Alias Detectados",
            value="Se vincularán automáticamente: " + ", ".join([f"`{a}`" for a in alias_detected]),
            inline=False
        )

    embed.set_footer(text="Elegí una opción con los botones inferiores para registrar en la base de datos.")
    return embed

def extract_event_details(text: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Extrae determinísticamente cantidad de Darks, nombre de evento y custodio desde el texto del usuario."""
    darks = None
    event_name = None
    holder = None

    word_to_num = {'un': 1, 'una': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5, 'seis': 6}

    # 1. Darks
    m_darks = re.search(r'(?:cayeron|salieron|dropearon|hubo|cayo|cayó)\s*(?:(\d+|un|una|dos|tres|cuatro|cinco)\s*)?(?:darks?|d)\b', text, re.IGNORECASE)
    if not m_darks:
        m_darks = re.search(r'\b(\d+|un|una|dos|tres|cuatro|cinco)\s*(?:darks?|d)\b', text, re.IGNORECASE)
    if m_darks:
        val = (m_darks.group(1) or "1").lower()
        darks = word_to_num.get(val, int(val) if val.isdigit() else 1)

    # 2. Custodia / Holder
    m_holder = re.search(r'(?:tiene|las tiene|quedaron a|custodia de|se las qued[oó])\s+([A-Za-z0-9_\[\]]+)', text, re.IGNORECASE)
    if m_holder:
        cand = m_holder.group(1)
        if cand.lower() not in ['las', 'los', 'la', 'el', 'dos', 'tres', 'darks', 'un', 'una']:
            holder = cand
    if not holder:
        m_holder2 = re.search(r'tiene\s+([A-Za-z0-9_\[\]]+)\s+las', text, re.IGNORECASE)
        if m_holder2:
            holder = m_holder2.group(1)

    # 3. Nombre de evento
    m_ev = re.search(r'\b((?:Frozen|Kundun|Medusa|Selupan|Lord|Erohim|Balgass|BC\s*\d*|DS\s*\d*|CC\s*\d*|Invasion|Maya|Nightmare|Kantarus))\b', text, re.IGNORECASE)
    if m_ev:
        raw_ev = m_ev.group(1).strip()
        if re.search(r'\b' + re.escape(raw_ev) + r'\s*darks?\b', text, re.IGNORECASE):
            raw_ev = re.sub(r'\s+\d+$', '', raw_ev).strip()

        # Normalizar prefijo de evento (ej: 'bc8' -> 'BC8', 'kundun' -> 'Kundun')
        parts = raw_ev.split()
        if len(parts) > 0 and re.match(r'^(?:bc|ds|cc)\d*$', parts[0], re.IGNORECASE):
            parts[0] = parts[0].upper()
        elif len(parts) > 0:
            parts[0] = parts[0].capitalize()
        ev_title = " ".join(parts)

        # Buscar sv / server (ej: 'sv2', 'server 1')
        m_sv = re.search(r'\b(?:sv|server)\s*(\d+)\b', text, re.IGNORECASE)
        sv_str = f"Sv{m_sv.group(1)}" if m_sv else ""

        # Buscar hora (ej: '6:30', '8 30 hs', '8:30hs', '18:00', 'de las 22:00')
        m_time = re.search(r'\b(?:de\s+las\s+)?(\d{1,2})\s*[:\s]\s*(\d{2})\s*(?:hs|hrs|horas)?\b', text, re.IGNORECASE)
        time_str = f"{m_time.group(1)}:{m_time.group(2)}" if m_time else ""

        # Armar base
        components = [ev_title]
        if sv_str:
            components.append(sv_str)
        if time_str:
            components.append(time_str)

        event_name = " ".join(components)

    # 4. Fecha del evento (ej: 'del 18/10', '18/10', 'FECHA 19/10/26')
    m_ev_date = re.search(r'\b(?:del?\s+|fecha\s+)?(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b', text, re.IGNORECASE)
    if m_ev_date:
        ev_d = m_ev_date.group(1).replace('-', '/')
        if event_name:
            if ev_d not in event_name:
                event_name = f"{event_name} ({ev_d})"
        else:
            event_name = f"Evento ({ev_d})"

    return darks, event_name, holder

def extract_payout_details(text: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """
    Detecta oraciones de pago como:
    - 'Jinete12 ya le pagó dark a DlChimu, Peperulo, Jinete12, Storfly, Carlitos Gi y Cheteelf'
    - 'Jinete le pagó a masterr' -> holder='Jinete', recips=['masterr'], darks=1
    - 'Jinete le entregó 2 darks a masterr' -> holder='Jinete', recips=['masterr'], darks=2
    - 'ya le pague a masterr' -> holder=None, recips=['masterr'], darks=1
    - 'le pagó la dark a Master' -> holder=None, recips=['Master'], darks=1
    """
    # Si contiene verbos de transferencia de custodia física, NO tratar como pago de puntos
    if re.search(r'\b(?:pas[oó]|pasale|transfiri[oó]|transferir|mand[oó])\b', text, re.I):
        return None, [], 1

    stop = {
        'ya', 'hoy', 'ayer', 'se', 'yo', 'nosotros', 'clan', 'admin',
        'el', 'la', 'los', 'las', 'le', 'les', 'me', 'nos', 'te',
        'dark', 'darks', 'joya', 'joyas', 'premio', 'recompensa',
        'del', 'al', 'a', 'de', 'para', 'en'
    }
    word_to_num = {'un': 1, 'una': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5, 'seis': 6}

    pattern = (
        r'^(?:(?P<holder>[A-Za-z0-9_\-\./]+)\s+)?'
        r'(?:ya\s+)?'
        r'(?:le\s+pag(?:[oó]|u[eé])|pag(?:[oó]|u[eé])|entreg(?:[oó]|u[eé])|le\s+entreg(?:[oó]|u[eé])|le\s+dio|dio|pagamos)'
        r'(?:\s+(?:(?:las?|una?|el)\s+)?(?P<qty>\d+|un|una|dos|tres|cuatro|cinco)?\s*darks?)?'
        r'\s*(?:a\s+)?'
        r'(?P<recipients>.+)$'
    )

    m = re.search(pattern, text.strip(), re.I)
    if not m:
        return None, [], 1

    holder_raw = m.group('holder')
    qty_raw = m.group('qty')
    recips_raw = m.group('recipients')

    holder = holder_raw.strip() if holder_raw and holder_raw.lower() not in stop else None
    darks = 1
    if qty_raw:
        if qty_raw.isdigit():
            darks = int(qty_raw)
        else:
            darks = word_to_num.get(qty_raw.lower(), 1)

    clean_recips = re.sub(r'^(?:(?:a\s+)?(?:los|las)?\s*(?:siguientes?|siguiente|dem[aá]s)?\s*(?:personajes?|pjs?|miembros?|jugadores?)?\s*[:\-])\s*', '', recips_raw.strip(), flags=re.I)
    tokens = re.split(r'\s*(?:,|;|\by\b|\be\b)\s*', clean_recips, flags=re.I)
    recipients = []
    for t in tokens:
        clean_t = re.sub(r'^[aA]\s+', '', t).strip()
        clean_t = re.sub(r'^.*[:\-]\s*', '', clean_t).strip()
        if clean_t and clean_t.lower() not in stop and len(clean_t) >= 2:
            recipients.append(clean_t)

    return holder, recipients, darks

def extract_custody_list(text: str) -> List[Tuple[str, int]]:
    """
    Detecta cargas o declaraciones de custodia para uno o múltiples personajes:
    - Formato lista con dos puntos o guión:
      Jinete12: 10 darks
      Camarlengo: otras 10 darks
    - Formato en frase:
      'Camarlengo tiene 10 darks almacenardas y Jinete12 tiene otras 10 darks almacenadas'
    - Formato cantidad primero:
      '10 darks Jinete12 y 10 darks Carmelengo'
    """
    word_to_num = {'un': 1, 'una': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5, 'seis': 6, 'diez': 10}
    stop_words = {
        'las', 'los', 'la', 'el', 'darks', 'dark', 'para', 'pagar', 'y', 'de', 'en',
        'tiene', 'tienen', 'guarda', 'guardan', 'almacena', 'almacenan', 'cada', 'uno',
        'otra', 'otras', 'otro', 'otros', 'que', 'su', 'sus', 'baul', 'baúl', 'inventario', 'cuenta',
        'siguiente', 'siguientes', 'personaje', 'personajes', 'pj', 'pjs',
        'no', 'nos', 'mas', 'más', 'si', 'sí', 'pero', 'yo', 'tu', 'mi', 'mis', 'eso', 'esto', 'ya'
    }

    def is_invalid_holder(w: str) -> bool:
        clean = w.lower().strip(" ,.:;!?-_")
        if not clean or len(clean) < 2 or clean in stop_words:
            return True
        if re.match(r'^(?:almacen|guard|pag|otr|personaje|siguiente|miembro)', clean):
            return True
        return False

    results = []
    seen = set()

    def add_item(holder_cand: str, qty_cand: int):
        h = holder_cand.strip(" ,.:;!?-_")
        if not is_invalid_holder(h):
            norm = db.resolve_pj_name(h).lower()
            if norm not in seen:
                results.append((h, qty_cand))
                seen.add(norm)

    # 1. Búsqueda línea por línea (ideal para listas como 'Jinete12: 10 darks' o '- Jinete12 - 10 darks')
    for line in text.splitlines():
        line = line.strip()
        m = re.search(r'^[-*•]?\s*([A-Za-z0-9_\-\./]+)\s*[:\-=]\s*(?:tiene|tienen|guarda|guardan|almacena|almacenan)?\s*(?:(?:otras?|sus?)\s+)?(\d+|un|una|dos|tres|cuatro|cinco|diez)\s*darks?', line, re.I)
        if m:
            q_str = m.group(2)
            qty = int(q_str) if q_str.isdigit() else word_to_num.get(q_str.lower(), 1)
            add_item(m.group(1), qty)

    # 2. Búsqueda en texto global con dos puntos o separador (ej: 'Jinete12: 10 darks y Camarlengo: 10 darks')
    for m in re.finditer(r'([A-Za-z0-9_\-\./]+)\s*[:\-=]\s*(?:tiene|tienen|guarda|guardan|almacena|almacenan)?\s*(?:(?:otras?|sus?)\s+)?(\d+|un|una|dos|tres|cuatro|cinco|diez)\s*darks?', text, re.I):
        q_str = m.group(2)
        qty = int(q_str) if q_str.isdigit() else word_to_num.get(q_str.lower(), 1)
        add_item(m.group(1), qty)

    # 3. [Holder] tiene/almacena/guarda [otras/sus] [X] darks
    for m in re.finditer(r'([A-Za-z0-9_\-\./]+)\s+(?:tiene|tienen|guarda|guardan|almacena|almacenan)\s+(?:(?:otras?|sus?)\s+)?(\d+|un|una|dos|tres|cuatro|cinco|diez)\s*darks?', text, re.I):
        q_str = m.group(2)
        qty = int(q_str) if q_str.isdigit() else word_to_num.get(q_str.lower(), 1)
        add_item(m.group(1), qty)

    # 4. [Holder] [X] darks
    for m in re.finditer(r'([A-Za-z0-9_\-\./]+)\s+(?:(?:otras?|sus?)\s+)?(\d+|un|una|dos|tres|cuatro|cinco|diez)\s*darks?', text, re.I):
        q_str = m.group(2)
        qty = int(q_str) if q_str.isdigit() else word_to_num.get(q_str.lower(), 1)
        add_item(m.group(1), qty)

    # 5. [X] darks [tiene] [de/en/a] [Holder]
    for m in re.finditer(r'(\d+|un|una|dos|tres|cuatro|cinco|diez)\s*darks?\s*(?:(?:tiene|tienen|guarda|almacena)\s+)?(?:de\s+|en\s+|a\s+)?([A-Za-z0-9_\-\./]+)', text, re.I):
        q_str = m.group(1)
        qty = int(q_str) if q_str.isdigit() else word_to_num.get(q_str.lower(), 1)
        add_item(m.group(2), qty)

    return results

def extract_transfer_details(text: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """
    Detecta transferencias de custodia de darks en el baúl del clan:
    - 'Buferita le pasó una dark a Bonielf' -> from_holder='Buferita', to_holder='Bonielf', darks=1
    - 'Chispa le pasó su dark almacenada a Bonielf' -> from_holder='Chispa', to_holder='Bonielf', darks=1
    - 'Jinete le pasó 5 darks a Camarlengo' -> from_holder='Jinete', to_holder='Camarlengo', darks=5
    - 'pasale 5 darks de Jinete a Camarlengo' -> from_holder='Jinete', to_holder='Camarlengo', darks=5
    - 'Jinete le transfirió 5 darks a Camarlengo' -> from_holder='Jinete', to_holder='Camarlengo', darks=5
    """
    stop = {
        'ya', 'hoy', 'ayer', 'se', 'yo', 'nosotros', 'clan', 'admin',
        'el', 'la', 'los', 'las', 'del', 'al', 'su', 'sus', 'le', 'les',
        'dark', 'darks', 'joya', 'joyas', 'almacenada', 'almacenadas', 'guardada', 'guardadas',
        'una', 'un', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'diez'
    }
    word_to_num = {'un': 1, 'una': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5, 'seis': 6, 'diez': 10}

    # Patrón 1: "pasale/transferi [X darks] de [ORIGEN] a [DESTINO]"
    m1 = re.search(r'(?:pasale|pas[aá]|transfer[ií]|mandale)\s+(?:(?:las\s+)?(\d+|un|una|dos|tres|cuatro|cinco|seis|diez)?\s*(?:darks?|joyas?)?\s+)?de\s+([A-Za-z0-9_\-\./]+)\s+a\s+([A-Za-z0-9_\-\./]+)', text, re.I)
    if m1:
        d_str = m1.group(1)
        darks = word_to_num.get(d_str.lower(), int(d_str) if d_str and d_str.isdigit() else 1) if d_str else 1
        f_h = m1.group(2).strip()
        t_h = m1.group(3).strip()
        if f_h.lower() not in stop and t_h.lower() not in stop:
            return f_h, t_h, darks

    # Patrón 2: "[ORIGEN] [le] (pasó|transfirió|mandó|entregó) [X darks / su dark almacenada] a [DESTINO]"
    # Ejemplo: "Buferita le pasó una dark a Bonielf", "Chispa le pasó su dark almacenada a Bonielf"
    m2 = re.search(
        r'([A-Za-z0-9_\-\./]+)\s+(?:le\s+)?(?:pas[oó]|transfiri[oó]|mand[oó]|entreg[oó])\s+'
        r'(?:(?:su|sus|las?|el|una?)\s+)?'
        r'(?:(\d+|un|una|dos|tres|cuatro|cinco|seis|diez)?\s*)?'
        r'(?:darks?|joyas?)?'
        r'(?:\s+(?:almacenadas?|guardadas?))?'
        r'\s+a\s+'
        r'([A-Za-z0-9_\-\./]+)',
        text, re.I
    )
    if m2:
        f_h = m2.group(1).strip()
        d_str = m2.group(2)
        t_h = m2.group(3).strip()
        darks = word_to_num.get(d_str.lower(), int(d_str) if d_str and d_str.isdigit() else 1) if d_str else 1
        if f_h.lower() not in stop and t_h.lower() not in stop:
            return f_h, t_h, darks

    # Patrón 3: "[ORIGEN] [le] (pasó|transfirió|mandó) a [DESTINO] [X darks]"
    m3 = re.search(
        r'([A-Za-z0-9_\-\./]+)\s+(?:le\s+)?(?:pas[oó]|transfiri[oó]|mand[oó]|entreg[oó])\s+a\s+([A-Za-z0-9_\-\./]+)'
        r'(?:\s+(?:(?:su|sus|las?|el|una?)\s+)?(\d+|un|una|dos|tres|cuatro|cinco|seis|diez)?\s*(?:darks?|joyas?)?)?',
        text, re.I
    )
    if m3:
        f_h = m3.group(1).strip()
        t_h = m3.group(2).strip()
        d_str = m3.group(3)
        darks = word_to_num.get(d_str.lower(), int(d_str) if d_str and d_str.isdigit() else 1) if d_str else 1
        if f_h.lower() not in stop and t_h.lower() not in stop:
            return f_h, t_h, darks

    # Patrón 4: "[le] (pasó|transfirió) [X darks] a [DESTINO]" (sin origen explícito)
    m4 = re.search(
        r'(?:le\s+)?(?:pas[oó]|transfiri[oó]|mand[oó]|entreg[oó])\s+'
        r'(?:(?:su|sus|las?|el|una?)\s+)?'
        r'(?:(\d+|un|una|dos|tres|cuatro|cinco|seis|diez)?\s*)?'
        r'(?:darks?|joyas?)?'
        r'(?:\s+(?:almacenadas?|guardadas?))?'
        r'\s+a\s+'
        r'([A-Za-z0-9_\-\./]+)',
        text, re.I
    )
    if m4:
        d_str = m4.group(1)
        t_h = m4.group(2).strip()
        darks = word_to_num.get(d_str.lower(), int(d_str) if d_str and d_str.isdigit() else 1) if d_str else 1
        if t_h.lower() not in stop:
            return None, t_h, darks

    return None, None, None

def extract_link_players(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Detecta intenciones de vincular dos personajes como la misma persona:
    - 'vincula a Themami con Stormfly, son la misma persona' -> ('Themami', 'Stormfly')
    - 'uní a Themami y Stormfly' -> ('Themami', 'Stormfly')
    - 'Themami y Stormfly son la misma persona' -> ('Themami', 'Stormfly')
    - 'asociar Themami a Stormfly' -> ('Themami', 'Stormfly')
    """
    clean = text.strip()
    stop = {'a', 'el', 'la', 'las', 'los', 'del', 'al', 'que', 'son', 'de', 'para'}

    # Patrón 1: vincula/asocia/uni/junta [a] PJ1 con/a/y PJ2
    m1 = re.search(r'(?:vincul[aá]|asoci[aá]|un[ií]|junt[aá]|conect[aá])\s+(?:a\s+)?([A-Za-z0-9_\-\./]+)\s+(?:con|a|y)\s+([A-Za-z0-9_\-\./]+)', clean, re.I)
    if m1:
        p1, p2 = m1.group(1).strip(" ,:."), m1.group(2).strip(" ,:.")
        if p1.lower() not in stop and p2.lower() not in stop and len(p1) >= 2 and len(p2) >= 2:
            return p1, p2

    # Patrón 2: PJ1 y PJ2 son la misma persona
    m2 = re.search(r'([A-Za-z0-9_\-\./]+)\s+y\s+([A-Za-z0-9_\-\./]+)\s+son\s+(?:la\s+misma\s+persona|el\s+mismo\s+jugador|la\s+misma\s+cuenta|el\s+mismo)', clean, re.I)
    if m2:
        p1, p2 = m2.group(1).strip(" ,:."), m2.group(2).strip(" ,:.")
        if p1.lower() not in stop and p2.lower() not in stop and len(p1) >= 2 and len(p2) >= 2:
            return p1, p2

    # Patrón 3: son la misma persona PJ1 y PJ2
    m3 = re.search(r'son\s+la\s+misma\s+persona\s+([A-Za-z0-9_\-\./]+)\s+y\s+([A-Za-z0-9_\-\./]+)', clean, re.I)
    if m3:
        p1, p2 = m3.group(1).strip(" ,:."), m3.group(2).strip(" ,:.")
        if p1.lower() not in stop and p2.lower() not in stop and len(p1) >= 2 and len(p2) >= 2:
            return p1, p2

    return None, None

def detect_query_intents(text: str) -> List[str]:
    """Detecta si el mensaje del usuario pide múltiples consultas simultáneas (puntos, deudas y/o baúl)."""
    intents = []
    lower = text.lower()

    # 1. Puntos / Ranking / Lista
    if any(w in lower for w in [
        "lista actual de puntos", "lista de puntos", "lista de ranking", "ranking",
        "clasificacion", "clasificación", "tabla de puntos", "puntos actuales"
    ]) or re.search(r'\b(?:puntos|ranking|lista)\b', lower):
        if not re.search(r'\b(?:cambiale|ajustar|sumar|borrar|pag[oó]|pas[oó]|dropearon)\b', lower):
            intents.append("LIST_POINTS")

    # 2. Deudas / Quiénes faltan pagar / Quiénes tienen por cobrar
    if any(w in lower for w in [
        "quiénes faltan pagar", "quienes faltan pagar", "quién falta pagar", "quien falta pagar",
        "falta pagar", "faltan pagar", "deben cobrar", "debe cobrar", "por cobrar",
        "quién tiene para cobrar", "quien tiene para cobrar", "a quién le debemos", "a quien le debemos",
        "deudas", "pendientes de pago", "quienes cobran", "quiénes cobran", "quien cobra", "quién cobra"
    ]) or re.search(r'\bdeudas?\b', lower):
        intents.append("LIST_DEBTS")

    # 3. Baúl / Quién tiene las darks / Almacenamiento físico
    if any(w in lower for w in [
        "quién tiene las darks", "quien tiene las darks", "quién tiene las dark", "quien tiene las dark",
        "quién las tiene", "quien las tiene", "quién tiene darks", "quien tiene darks",
        "quién guarda", "quien guarda", "quién almacena", "quien almacena",
        "estado del baúl", "estado del baul", "cómo está el baúl", "como esta el baul",
        "en el baúl", "en el baul", "almacenamiento", "custodia"
    ]) or re.search(r'\bba[uú]l(?:es)?\b', lower):
        if not re.search(r'\b(?:reiniciar|vaciar|transferir)\b', lower):
            intents.append("VAULT_STATUS")

    return intents


# ====================================================================
# MODALES Y VISTAS INTERACTIVAS PARA REVISIÓN DE CAPTURAS
# ====================================================================

class EditDarksModal(discord.ui.Modal, title="Modificar Darks y Evento"):
    darks_input = discord.ui.TextInput(
        label="Cantidad de Darks dropeadas",
        placeholder="Ej: 1, 2, 3...",
        required=True,
        max_length=4
    )
    event_input = discord.ui.TextInput(
        label="Nombre del evento",
        placeholder="Ej: Frozen 13:40, Kundun, etc.",
        required=False,
        max_length=60
    )
    holder_input = discord.ui.TextInput(
        label="Quién guarda las Darks (Custodia)",
        placeholder="Ej: Peperulo (opcional)",
        required=False,
        max_length=40
    )

    def __init__(self, parent_view: "PartyReviewView"):
        super().__init__()
        self.parent_view = parent_view
        self.darks_input.default = str(parent_view.darks_count)
        self.event_input.default = parent_view.event_name
        if parent_view.holder:
            self.holder_input.default = parent_view.holder

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(self.darks_input.value.strip())
            if val <= 0:
                await interaction.response.send_message("❌ La cantidad de Darks debe ser mayor a 0.", ephemeral=True)
                return
            self.parent_view.darks_count = val
            if self.event_input.value.strip():
                self.parent_view.event_name = self.event_input.value.strip()
            self.parent_view.holder = self.holder_input.value.strip() or None
            await self.parent_view.update_message(interaction)
        except ValueError:
            await interaction.response.send_message("❌ La cantidad de Darks debe ser un número entero válido.", ephemeral=True)

def parse_reward_input(val: str, default_darks: int = 1) -> Tuple[int, str]:
    """
    Parsea entradas de premios flexibles considerando que 1 Dark = 6 Exe:
    - '6 exe', '6 exes', '6 items' -> 6, 'Exe'
    - '1 dark' -> 6, 'Exe (1 Dark = 6 Exe)'
    - '2 darks' -> 12, 'Exe (2 Darks = 12 Exe)'
    - '2 darks (12 exe)' -> 12, 'Exe (2 Darks)'
    - '12' -> 12, 'Exe'
    """
    v = val.strip().lower()
    m_both = re.search(r'(\d+)\s*darks?.*?(\d+)\s*(?:exe|exes|items?)', v)
    if m_both:
        return int(m_both.group(2)), f"Exe ({m_both.group(1)} Darks)"
    m_exe = re.search(r'(\d+)\s*(?:exe|exes|items?)', v)
    if m_exe:
        return int(m_exe.group(1)), "Exe"
    m_dark = re.search(r'(\d+)\s*darks?', v)
    if m_dark:
        d = int(m_dark.group(1))
        return d * 6, f"Exe ({d} Dark{'s' if d > 1 else ''} = {d*6} Exe)"
    if v.isdigit():
        return int(v), "Exe"
    return max(1, default_darks * 6), "Exe"

class DirectRaffleModal(discord.ui.Modal, title="🎁 Sorteo Directo (Sin Puntos)"):
    def __init__(self, parent_view: "PartyReviewView"):
        super().__init__()
        self.parent_view = parent_view
        default_exes = max(1, self.parent_view.darks_count * 6)
        self.premios_input = discord.ui.TextInput(
            label="Premios / Exes a repartir",
            placeholder="Ej: 6 exe, 1 dark (1 dark = 6 exe)",
            default=f"{default_exes} exe",
            required=True,
            max_length=40
        )
        self.evento_input = discord.ui.TextInput(
            label="Nombre del evento (opcional)",
            placeholder="Ej: BC8, DS, etc.",
            default=self.parent_view.event_name,
            required=False,
            max_length=50
        )
        self.add_item(self.premios_input)
        self.add_item(self.evento_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.parent_view.pjs:
            try:
                await interaction.response.send_message("❌ La lista de personajes está vacía.", ephemeral=True)
            except Exception:
                pass
            return

        self.parent_view.stop()
        interaction_ok = False
        try:
            await interaction.response.defer()
            interaction_ok = True
        except Exception:
            interaction_ok = False

        raw_premios = self.premios_input.value.strip()
        total_rewards, reward_name = parse_reward_input(raw_premios, default_darks=self.parent_view.darks_count)
        ev_name = self.evento_input.value.strip() or self.parent_view.event_name or "Evento ArgenMU"

        embed, gif_path, won_url = await asyncio.to_thread(
            execute_direct_raffle_event,
            event_name=ev_name,
            total_rewards=total_rewards,
            reward_name=reward_name,
            pj_names=self.parent_view.pjs,
            registered_by=interaction.user.name
        )

        view = discord.ui.View()
        if won_url:
            view.add_item(discord.ui.Button(label="🎡 Ver en WheelOfNames (Web)", url=won_url, style=discord.ButtonStyle.link))

        def make_file():
            if gif_path and os.path.exists(gif_path):
                return discord.File(gif_path, filename="ruleta.gif")
            return None

        # 1. Responder la interacción si sigue activa
        if interaction_ok:
            try:
                f1 = make_file()
                if f1:
                    await interaction.edit_original_response(embed=embed, view=view, attachments=[f1])
                else:
                    await interaction.edit_original_response(embed=embed, view=view)
                return
            except Exception as e:
                print(f"Error en direct_raffle edit_original_response: {e}", flush=True)

        # 2. Fallback: editar el mensaje original directamente
        target_msg = getattr(self.parent_view, "message", None) or interaction.message
        if target_msg:
            try:
                f2 = make_file()
                if f2:
                    await target_msg.edit(embed=embed, view=view, attachments=[f2])
                else:
                    await target_msg.edit(embed=embed, view=view)
                return
            except Exception as e:
                print(f"Error en direct_raffle target_msg.edit: {e}", flush=True)

        # 3. Fallback al canal directamente
        try:
            f3 = make_file()
            if f3:
                await interaction.channel.send(embed=embed, file=f3, view=view)
            else:
                await interaction.channel.send(embed=embed, view=view)
        except Exception as e:
            print(f"Error enviando resultado de sorteo directo al canal: {e}", flush=True)

class AddPjModal(discord.ui.Modal, title="Agregar Personajes a Party"):
    pjs_input = discord.ui.TextInput(
        label="Nombres de PJ (separados por coma o espacio)",
        placeholder="Ej: MagoLoco, ElfaBuff",
        required=True
    )
    party_input = discord.ui.TextInput(
        label="Número de Party (1 o 2)",
        placeholder="1 o 2",
        required=False,
        max_length=10
    )
    clan_input = discord.ui.TextInput(
        label="Clan al que pertenecen",
        placeholder="DxMENTOR o PolloKill",
        required=False,
        default="DxMENTOR",
        max_length=40
    )

    def __init__(self, parent_view: "PartyReviewView"):
        super().__init__()
        self.parent_view = parent_view
        default_party = "2" if len(parent_view.pjs) >= 7 else "1"
        self.party_input.default = default_party

    async def on_submit(self, interaction: discord.Interaction):
        text = self.pjs_input.value
        raw_party = self.party_input.value.strip() or "1"
        party_val = f"Party {raw_party}" if raw_party in ["1", "2", "3"] else (raw_party if raw_party.lower().startswith("party") else f"Party {raw_party}")
        clan_val = db.resolve_clan_name(self.clan_input.value.strip() or "DxMENTOR")
        names = [n.strip() for n in re.split(r'[,;\s]+', text) if n.strip()]
        for raw_name in names:
            if is_valid_character_name(raw_name):
                canonical = db.resolve_pj_name(raw_name)
                p_db = db.get_player(canonical)
                final_clan = p_db["clan"] if (p_db and p_db["clan"]) else clan_val
                if canonical.lower() not in [p["name"].lower() for p in self.parent_view.pjs]:
                    self.parent_view.pjs.append({
                        "name": canonical,
                        "clan": db.resolve_clan_name(final_clan),
                        "party": party_val
                    })
        await self.parent_view.update_message(interaction)

class RemovePjModal(discord.ui.Modal, title="Quitar Personajes"):
    pjs_input = discord.ui.TextInput(
        label="Nombre del PJ o N° a quitar",
        placeholder="Ej: MagoLoco o el número 1",
        required=True
    )

    def __init__(self, parent_view: "PartyReviewView"):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        target = self.pjs_input.value.strip().lower()
        if target.isdigit():
            idx = int(target) - 1
            if 0 <= idx < len(self.parent_view.pjs):
                self.parent_view.pjs.pop(idx)
        else:
            self.parent_view.pjs = [p for p in self.parent_view.pjs if p["name"].lower() != target]
        await self.parent_view.update_message(interaction)

class EditFullPartyModal(discord.ui.Modal, title="Editar Lista Completa de Parties"):
    list_input = discord.ui.TextInput(
        label="Participantes por Party (editá, borrá o mové)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=4000
    )

    def __init__(self, parent_view: "PartyReviewView"):
        super().__init__()
        self.parent_view = parent_view
        
        # Armar texto pre-cargado organizado por parties
        parties_dict = {}
        for p in parent_view.pjs:
            party_key = p.get("party") or "Party 1"
            parties_dict.setdefault(party_key, []).append(p["name"])

        lines = []
        for p_name, members in parties_dict.items():
            lines.append(f"{p_name}:")
            for m in members:
                lines.append(f"• {m}")
            lines.append("")

        self.list_input.default = "\n".join(lines).strip()

    async def on_submit(self, interaction: discord.Interaction):
        raw_text = self.list_input.value
        new_pjs = []
        current_party = "Party 1"
        seen = set()

        for line in raw_text.splitlines():
            clean_line = line.strip()
            if not clean_line:
                continue

            # Detectar encabezado de party (ej: "Party 1:", "Party 2:", "P2:", etc.)
            m_party = re.match(r'^(?:🎮\s*)?(party\s*\d+|p\d+|segunda\s+party|otra\s+party)\s*[:\-]?', clean_line, re.I)
            if m_party:
                p_raw = m_party.group(1).lower()
                current_party = "Party 2" if any(x in p_raw for x in ["2", "segunda", "otra"]) else "Party 1"
                sub_parts = re.split(r'[:\-]', clean_line, maxsplit=1)
                if len(sub_parts) > 1 and sub_parts[1].strip():
                    clean_line = sub_parts[1].strip()
                else:
                    continue

            # Extraer nombres en la línea (separados por coma, viñeta o espacio)
            tokens = re.split(r'[,;\s•]+', clean_line)
            for token in tokens:
                t = re.sub(r'[^a-zA-Z0-9_\-\./\[\]]', '', token).strip(' .-_:')
                if is_valid_character_name(t):
                    canonical = db.resolve_pj_name(t)
                    if canonical.lower() not in seen:
                        p_db = db.get_player(canonical)
                        clan = p_db["clan"] if (p_db and p_db["clan"]) else "DxMENTOR"
                        new_pjs.append({
                            "name": canonical,
                            "clan": db.resolve_clan_name(clan),
                            "party": current_party
                        })
                        seen.add(canonical.lower())

        if not new_pjs:
            await interaction.response.send_message("❌ La lista no puede quedar vacía.", ephemeral=True)
            return

        self.parent_view.pjs = new_pjs
        await self.parent_view.update_message(interaction)
        if not interaction.response.is_done():
            await interaction.response.send_message(f"✅ Se actualizó la lista ({len(new_pjs)} personajes).", ephemeral=True)
        else:
            await interaction.followup.send(f"✅ Se actualizó la lista ({len(new_pjs)} personajes).", ephemeral=True)

class EditPjModal(discord.ui.Modal, title="Corregir o Sacar PJ"):
    pj_old = discord.ui.TextInput(
        label="Nombre actual o N° de la lista",
        placeholder="Ej: Bonielf o el número 1",
        required=True,
        max_length=40
    )
    pj_new = discord.ui.TextInput(
        label="Nombre nuevo (vacío o 'borrar' para sacar)",
        placeholder="Dejar vacío o escribir 'borrar' para quitarlo",
        required=False,
        max_length=40
    )
    party_new = discord.ui.TextInput(
        label="Party (1 o 2, opcional)",
        placeholder="Ej: 1 o 2",
        required=False,
        max_length=20
    )
    clan_new = discord.ui.TextInput(
        label="Clan (DxMENTOR / PolloKill, opcional)",
        placeholder="Ej: DxMENTOR o PolloKill",
        required=False,
        max_length=40
    )

    def __init__(self, parent_view: "PartyReviewView"):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        old_val = self.pj_old.value.strip()
        new_val = self.pj_new.value.strip()
        party_change = self.party_new.value.strip()
        clan_change = self.clan_new.value.strip()

        target_idx = None
        if old_val.isdigit():
            idx = int(old_val) - 1
            if 0 <= idx < len(self.parent_view.pjs):
                target_idx = idx
        else:
            for idx, p in enumerate(self.parent_view.pjs):
                if p["name"].lower() == old_val.lower() or old_val.lower() in p["name"].lower():
                    target_idx = idx
                    break

        if target_idx is None:
            await interaction.response.send_message(
                f"⚠️ No se encontró al personaje **'{old_val}'** en la lista actual.",
                ephemeral=True
            )
            return

        old_name = self.parent_view.pjs[target_idx]["name"]

        # Si se dejó vacío o se indicó borrar/sacar -> eliminarlo de la party directamente
        is_removal = not new_val or new_val.lower() in [
            "borrar", "sacar", "eliminar", "quitar", "quitalo", "-", "0", "delete", "remove", "none", "null", "chau"
        ]
        if is_removal:
            self.parent_view.pjs.pop(target_idx)
            await self.parent_view.update_message(interaction)
            msg = f"🗑️ Se quitó a **{old_name}** de la lista."
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
            return

        canonical_new = db.resolve_pj_name(new_val)
        self.parent_view.pjs[target_idx]["name"] = canonical_new
        if party_change:
            p_label = f"Party {party_change}" if party_change in ["1", "2", "3"] else party_change
            self.parent_view.pjs[target_idx]["party"] = p_label
        if clan_change:
            self.parent_view.pjs[target_idx]["clan"] = db.resolve_clan_name(clan_change)
        else:
            p_db = db.get_player(canonical_new)
            if p_db and p_db["clan"]:
                self.parent_view.pjs[target_idx]["clan"] = db.resolve_clan_name(p_db["clan"])

        await self.parent_view.update_message(interaction)
        msg = f"✅ Se corrigió `{old_name}` ➔ **`{canonical_new}`**."
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)

def rebalance_parties_max_7(pjs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Garantiza que ninguna Party tenga más de 7 miembros (máximo oficial del juego).
    Si una party tiene más de 7 personajes, desborda automáticamente a las siguientes partys.
    """
    parties = {}
    for p in pjs:
        p_name = p.get("party") or "Party 1"
        parties.setdefault(p_name, []).append(p)

    rebalanced = []
    party_counter = 1
    for p_name, members in parties.items():
        for i in range(0, len(members), 7):
            chunk = members[i:i + 7]
            curr_party = f"Party {party_counter}"
            for it in chunk:
                c = dict(it)
                c["party"] = curr_party
                rebalanced.append(c)
            party_counter += 1
    return rebalanced

def add_safe_fields(embed: discord.Embed, base_name: str, lines: List[str], max_chars: int = 900):
    """
    Divide listas largas de líneas en múltiples campos de Discord
    garantizando que ningún campo supere jamás el límite de 1024 caracteres.
    """
    if not lines:
        return
    chunks = []
    curr_chunk = []
    curr_len = 0
    for line in lines:
        line_len = len(line) + 1
        if curr_len + line_len > max_chars and curr_chunk:
            chunks.append("\n".join(curr_chunk))
            curr_chunk = [line]
            curr_len = line_len
        else:
            curr_chunk.append(line)
            curr_len += line_len
    if curr_chunk:
        chunks.append("\n".join(curr_chunk))

    for idx, chunk in enumerate(chunks[:25]):
        field_name = base_name if idx == 0 else f"{base_name} (cont. {idx + 1})"
        embed.add_field(name=field_name, value=chunk, inline=False)

class PartyReviewView(discord.ui.View):
    def __init__(self, author_id: int, event_name: str, darks_count: int, pjs: Union[List[str], List[Dict[str, Any]]], holder: Optional[str] = None):
        super().__init__(timeout=900) # 15 minutos para interactuar
        self.author_id = author_id
        self.event_name = event_name
        self.darks_count = darks_count
        
        # Normalizar participantes en dicts con nombre canónico, clan normalizado y party
        normalized = []
        for idx, item in enumerate(pjs):
            if isinstance(item, dict):
                raw_n = str(item.get("name", "")).strip()
                canonical_n = db.resolve_pj_name(raw_n)
                p_db = db.get_player(canonical_n)
                clan_cand = p_db["clan"] if (p_db and p_db["clan"]) else (item.get("clan") or "DxMENTOR")
                clan_norm = db.resolve_clan_name(clan_cand)
                party = item.get("party")
                if not party:
                    party_num = (idx // 7) + 1
                    party = f"Party {party_num}"
                normalized.append({
                    "name": canonical_n,
                    "clan": clan_norm,
                    "party": party
                })
            else:
                raw_n = str(item).strip()
                canonical_n = db.resolve_pj_name(raw_n)
                p_db = db.get_player(canonical_n)
                clan_cand = p_db["clan"] if (p_db and p_db["clan"]) else "DxMENTOR"
                clan_norm = db.resolve_clan_name(clan_cand)
                party_num = (idx // 7) + 1
                normalized.append({
                    "name": canonical_n,
                    "clan": clan_norm,
                    "party": f"Party {party_num}"
                })
        self.pjs: List[Dict[str, Any]] = rebalance_parties_max_7(normalized)
        self.holder = holder
        self.message: Optional[discord.Message] = None

    @property
    def pj_names(self) -> List[str]:
        return [p["name"] for p in self.pjs]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not check_is_admin(interaction.user, interaction.guild):
            try:
                await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden confirmar eventos o modificar participantes.", ephemeral=True)
            except Exception:
                pass
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        target_msg = getattr(self, "message", None)
        if target_msg:
            try:
                await target_msg.edit(view=self)
            except Exception:
                pass

    def build_embed(self) -> discord.Embed:
        desc = (
            f"💎 **Darks:** {self.darks_count} ({self.darks_count * 10} puntos totales)\n"
            f"👥 **PJs Totales:** {len(self.pjs)}\n"
        )
        if self.holder:
            desc += f"🏛️ **Custodia física:** **{self.holder}** almacena las darks\n"
        desc += (
            f"\nRevisá la lista a continuación (organizada por **Party**):\n"
            f"• Presioná **✅ Confirmar** para sumar puntos al gremio.\n"
            f"• O presioná **🎁 Sorteo Directo** para repartir Exes en el momento (sin sumar puntos)."
        )

        embed = discord.Embed(
            title=f"📋 Revisión de Party — Evento: {self.event_name}",
            description=desc,
            color=discord.Color.blue()
        )

        if not self.pjs:
            embed.add_field(name="Participantes en lista", value="*(Lista vacía. Usá el botón '➕ Agregar PJ')*", inline=False)
            return embed

        # Agrupar por Party preservando el orden original
        parties_dict = {}
        for idx, p in enumerate(self.pjs):
            party_key = p.get("party") or "Party 1"
            parties_dict.setdefault(party_key, []).append((idx + 1, p))

        for party_name, members in parties_dict.items():
            lines = []
            for num, p in members:
                clan_str = f" `[{p['clan']}]`" if p.get("clan") else ""
                lines.append(f"`{num}.` **{p['name']}**{clan_str}")
            field_text = "\n".join(lines)
            if len(field_text) > 1000:
                field_text = field_text[:990] + "\n...(lista recortada)"
            embed.add_field(
                name=f"🎮 {party_name} ({len(members)} PJs)",
                value=field_text,
                inline=False
            )

        return embed

    async def update_message(self, interaction: discord.Interaction):
        self.pjs = rebalance_parties_max_7(self.pjs)
        embed = self.build_embed()
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            target_msg = getattr(self, "message", None) or interaction.message
            if target_msg:
                try:
                    await target_msg.edit(embed=embed, view=self)
                except Exception:
                    pass

    @discord.ui.button(label="✅ Confirmar y Sortear", style=discord.ButtonStyle.green, row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.pjs:
            try:
                await interaction.response.send_message("❌ Debe haber al menos 1 personaje en la lista para realizar el evento.", ephemeral=True)
            except Exception:
                pass
            return

        self.stop()
        interaction_ok = False
        try:
            await interaction.response.defer()
            interaction_ok = True
        except Exception:
            interaction_ok = False

        embed, gif_path = await asyncio.to_thread(
            execute_and_record_event,
            event_name=self.event_name,
            darks_count=self.darks_count,
            pj_names=self.pjs,
            registered_by=interaction.user.name,
            holder=self.holder
        )

        def make_file():
            if gif_path and os.path.exists(gif_path):
                return discord.File(gif_path, filename="ruleta.gif")
            return None

        # 1. Responder la interacción si sigue activa en Discord
        if interaction_ok:
            try:
                f1 = make_file()
                if f1:
                    await interaction.edit_original_response(embed=embed, view=None, attachments=[f1])
                else:
                    await interaction.edit_original_response(embed=embed, view=None)
                return
            except Exception as e:
                print(f"Error en edit_original_response: {e}", flush=True)

        # 2. Fallback: editar el mensaje original directamente
        target_msg = getattr(self, "message", None) or interaction.message
        if target_msg:
            try:
                f2 = make_file()
                if f2:
                    await target_msg.edit(embed=embed, view=None, attachments=[f2])
                else:
                    await target_msg.edit(embed=embed, view=None)
                return
            except Exception as e:
                print(f"Error en target_msg.edit: {e}", flush=True)

        # 3. Fallback final: enviar al canal directamente
        try:
            f3 = make_file()
            if f3:
                await interaction.channel.send(embed=embed, file=f3)
            else:
                await interaction.channel.send(embed=embed)
        except Exception as e:
            print(f"Error enviando resultado de evento al canal: {e}", flush=True)

    @discord.ui.button(label="🎁 Sorteo Directo", style=discord.ButtonStyle.primary, row=0, emoji="🎁")
    async def direct_raffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DirectRaffleModal(self))

    @discord.ui.button(label="💎 Cambiar Darks", style=discord.ButtonStyle.secondary, row=0)
    async def edit_darks(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditDarksModal(self))

    @discord.ui.button(label="📸 Agregar Captura", style=discord.ButtonStyle.secondary, row=1)
    async def add_capture(self, interaction: discord.Interaction, button: discord.ui.Button):
        ACTIVE_CAPTURE_WAITERS.add(interaction.user.id)
        existing_parties = set(p.get("party", "Party 1") for p in self.pjs)
        next_party_num = len(existing_parties) + 1
        await interaction.response.send_message(
            f"📸 **Esperando captura de la Party {next_party_num}...**\n"
            "Por favor, enviá la captura de pantalla (o varias) en este canal dentro de los próximos **60 segundos**.\n"
            "El bot las escaneará y sumará los nuevos miembros a esta lista automáticamente.",
            ephemeral=True
        )

        def check(m: discord.Message):
            return (
                m.author.id == interaction.user.id
                and m.channel.id == interaction.channel.id
                and any(att.content_type and att.content_type.startswith("image/") for att in m.attachments)
            )

        try:
            user_msg = await bot.wait_for("message", timeout=60.0, check=check)
        except asyncio.TimeoutError:
            ACTIVE_CAPTURE_WAITERS.discard(interaction.user.id)
            try:
                await interaction.followup.send("⏳ Tiempo agotado: no se recibió ninguna captura.", ephemeral=True)
            except Exception:
                pass
            return
        finally:
            ACTIVE_CAPTURE_WAITERS.discard(interaction.user.id)

        image_atts = [att for att in user_msg.attachments if att.content_type and att.content_type.startswith("image/")]
        gemini_key = os.getenv("GEMINI_API_KEY")
        added_total = []
        existing_names = [p["name"].lower() for p in self.pjs]

        for att_idx, att in enumerate(image_atts):
            img_bytes = await att.read()
            curr_party_label = f"Party {next_party_num + att_idx}"
            new_pjs = []

            if gemini_key:
                from ocr_scanner import parse_with_gemini
                data = await asyncio.to_thread(
                    parse_with_gemini,
                    images_bytes=[img_bytes],
                    user_text=curr_party_label,
                    api_key=gemini_key.strip()
                )
                if data and data.get("characters"):
                    new_pjs = data["characters"]

            if not new_pjs:
                from ocr_scanner import extract_names_from_image
                new_pjs = await asyncio.to_thread(extract_names_from_image, img_bytes)

            for name in new_pjs:
                if is_valid_character_name(name):
                    canonical = db.resolve_pj_name(name)
                    if canonical.lower() not in existing_names:
                        p_db = db.get_player(canonical)
                        c_norm = db.resolve_clan_name(p_db["clan"] if (p_db and p_db["clan"]) else "DxMENTOR")
                        self.pjs.append({"name": canonical, "clan": c_norm, "party": curr_party_label})
                        added_total.append(f"{canonical} ({curr_party_label})")
                        existing_names.append(canonical.lower())

        embed = self.build_embed()
        if self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception:
                pass
        else:
            try:
                await interaction.edit_original_response(embed=embed, view=self)
            except Exception:
                pass

        if added_total:
            await interaction.followup.send(
                f"✅ **¡Capturas procesadas!** Se sumaron **{len(added_total)} PJs** a la lista: **{', '.join(added_total)}**",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "⚠️ No se detectaron personajes nuevos en las capturas o ya estaban incluidos en la lista.",
                ephemeral=True
            )

    @discord.ui.button(label="📝 Editar Lista", style=discord.ButtonStyle.secondary, row=0)
    async def edit_full_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditFullPartyModal(self))

    @discord.ui.button(label="➕ Agregar PJ", style=discord.ButtonStyle.secondary, row=1)
    async def add_pj(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddPjModal(self))

    @discord.ui.button(label="✏️ Corregir / Sacar PJ", style=discord.ButtonStyle.secondary, row=1)
    async def edit_pj(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditPjModal(self))

    @discord.ui.button(label="➖ Quitar PJ", style=discord.ButtonStyle.secondary, row=1)
    async def remove_pj(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RemovePjModal(self))

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        embed = discord.Embed(title="❌ Evento cancelado", description="El registro del evento fue cancelado.", color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=None)

class ConfirmResetView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not check_is_admin(interaction.user, interaction.guild):
            await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden reiniciar los puntos del gremio.", ephemeral=True)
            return False
        if interaction.user.id != self.author_id and not (isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("❌ Solo quien ejecutó el comando puede confirmar esta acción.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⚠️ Sí, reiniciar y borrar historial", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        ok, msg = db.reset_points_and_history(delete_players=True)
        if ok:
            embed = discord.Embed(
                title="🔄 Historial y Puntos Reiniciados",
                description=(
                    "✅ **¡Se han reiniciado todos los puntos y borrado el historial con éxito!**\n\n"
                    "• Se eliminó el registro de todos los eventos anteriores.\n"
                    "• Se borró el historial de pagos.\n"
                    "• La tabla de clasificación y puntos quedó limpia en 0 para un nuevo ciclo.\n\n"
                    "*(Las darks guardadas en custodia física del baúl `/baul` y la lista de administradores se mantienen intactas)*."
                ),
                color=discord.Color.green()
            )
            embed.set_footer(text=f"Reinicio ejecutado por {interaction.user.name} • ArgenMU")
        else:
            embed = discord.Embed(
                title="❌ Error al reiniciar",
                description=msg,
                color=discord.Color.red()
            )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        embed = discord.Embed(
            title="🚫 Operación Cancelada",
            description="El reinicio de puntos y eventos fue cancelado. No se modificó ningún dato.",
            color=discord.Color.light_grey()
        )
        await interaction.response.edit_message(embed=embed, view=None)

class ConfirmVaultResetView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not check_is_admin(interaction.user, interaction.guild):
            await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden reiniciar el baúl de darks.", ephemeral=True)
            return False
        if interaction.user.id != self.author_id and not (isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("❌ Solo quien ejecutó el comando puede confirmar esta acción.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⚠️ Sí, vaciar y reiniciar baúl a 0", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        ok, msg = db.reset_dark_vault(registered_by=interaction.user.name)
        if ok:
            embed = discord.Embed(
                title="🏛️ Baúl Reiniciado a Cero",
                description=(
                    "✅ **¡Se ha vaciado el almacenamiento de Darks con éxito!**\n\n"
                    "• Todos los registros de custodia física de personajes quedaron en 0.\n"
                    "• Se registró el evento en el historial del baúl (`/baul_historial`).\n"
                    "• Ya podés volver a cargar las darks de cada uno usando `/custodia` o en el chat.\n\n"
                    "*(Los puntos de eventos de los miembros y la lista de administradores se mantienen intactos)*."
                ),
                color=discord.Color.green()
            )
            embed.set_footer(text=f"Reinicio de baúl por {interaction.user.name} • ArgenMU")
        else:
            embed = discord.Embed(
                title="❌ Error al reiniciar baúl",
                description=msg,
                color=discord.Color.red()
            )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        embed = discord.Embed(
            title="🚫 Operación Cancelada",
            description="El reinicio del baúl fue cancelado. No se modificó ningún registro de custodia.",
            color=discord.Color.light_grey()
        )
        await interaction.response.edit_message(embed=embed, view=None)

class EditVaultModal(discord.ui.Modal, title="📝 Editar Almacenamiento del Baúl"):
    vault_input = discord.ui.TextInput(
        label="Custodios y Darks (un miembro por línea)",
        style=discord.TextStyle.paragraph,
        placeholder="Ejemplo:\nBonielf: 10\nBuferita: 6\nCamarlengo: 6",
        required=True,
        max_length=4000
    )

    def __init__(self, parent_view: "VaultView"):
        super().__init__()
        self.parent_view = parent_view
        current_vault = db.get_dark_vault()
        lines = []
        for v in current_vault:
            note_str = f" ({v['note']})" if v.get("note") else ""
            lines.append(f"{v['holder']}: {v['darks_count']}{note_str}")
        self.vault_input.default = "\n".join(lines) if lines else "Bonielf: 10\nBuferita: 6\nCamarlengo: 6"

    async def on_submit(self, interaction: discord.Interaction):
        text = self.vault_input.value
        entries = []
        for line in text.splitlines():
            clean = line.strip(" -*•\t")
            if not clean:
                continue
            m1 = re.match(r'^([A-Za-z0-9_\-\./]+)\s*[:\-=]\s*(\d+)(?:\s*darks?)?(?:\s*\(?(.*?)\)?)?$', clean, re.I)
            if m1:
                entries.append((m1.group(1).strip(), int(m1.group(2)), (m1.group(3) or "").strip(" ()")))
                continue
            m2 = re.match(r'^([A-Za-z0-9_\-\./]+)\s+(\d+)(?:\s*darks?)?(?:\s*\(?(.*?)\)?)?$', clean, re.I)
            if m2:
                entries.append((m2.group(1).strip(), int(m2.group(2)), (m2.group(3) or "").strip(" ()")))
                continue
            m3 = re.match(r'^(\d+)\s*(?:darks?)?\s*(?:de|a|para)?\s*([A-Za-z0-9_\-\./]+)$', clean, re.I)
            if m3:
                entries.append((m3.group(2).strip(), int(m3.group(1)), ""))
                continue

        if not entries:
            await interaction.response.send_message("⚠️ No se detectaron líneas válidas. Formato: `Personaje: Cantidad`", ephemeral=True)
            return

        try:
            await interaction.response.defer()
        except Exception:
            pass

        ok, msg, total = db.bulk_set_dark_custody(entries, interaction.user.name)
        await auto_backup_database()
        import ai_assistant
        new_embed = ai_assistant.build_vault_embed()

        try:
            await interaction.edit_original_response(embed=new_embed, view=self.parent_view)
        except Exception:
            target_msg = getattr(self.parent_view, "message", None)
            if target_msg:
                try:
                    await target_msg.edit(embed=new_embed, view=self.parent_view)
                except Exception:
                    pass
            await interaction.followup.send(msg, ephemeral=True)

class QuickCustodyModal(discord.ui.Modal, title="✏️ Asignar o Ajustar Custodia"):
    pj_input = discord.ui.TextInput(
        label="Nombre del Personaje",
        placeholder="Ej: Bonielf o Camarlengo",
        required=True,
        max_length=40
    )
    qty_input = discord.ui.TextInput(
        label="Cantidad de Darks que guarda (0 para sacar)",
        placeholder="Ej: 5 (o 0 para quitar del baúl)",
        required=True,
        max_length=10
    )
    note_input = discord.ui.TextInput(
        label="Nota u observación (opcional)",
        placeholder="Ej: Guardadas en baúl 2",
        required=False,
        max_length=60
    )

    def __init__(self, parent_view: "VaultView"):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        pj = self.pj_input.value.strip()
        raw_qty = self.qty_input.value.strip()
        note = self.note_input.value.strip()
        if not raw_qty.isdigit():
            await interaction.followup.send("❌ La cantidad debe ser un número entero (ej: 0, 1, 5).", ephemeral=True)
            return
        qty = int(raw_qty)
        ok, msg = db.set_dark_custody(pj, qty, interaction.user.name, note)
        await auto_backup_database()
        import ai_assistant
        new_embed = ai_assistant.build_vault_embed()

        try:
            await interaction.edit_original_response(embed=new_embed, view=self.parent_view)
        except Exception:
            target_msg = getattr(self.parent_view, "message", None)
            if target_msg:
                try:
                    await target_msg.edit(embed=new_embed, view=self.parent_view)
                except Exception:
                    pass
            await interaction.followup.send(msg, ephemeral=True)

class VaultView(discord.ui.View):
    def __init__(self, author_id: Optional[int] = None):
        super().__init__(timeout=None) # Nunca expira (persistente)
        self.author_id = author_id
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label="Editar Lista Baúl", style=discord.ButtonStyle.primary, emoji="📝", custom_id="persistent_edit_vault_list")
    async def edit_vault_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_is_admin(interaction.user, interaction.guild):
            await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden modificar la custodia del baúl.", ephemeral=True)
            return
        try:
            await interaction.response.send_modal(EditVaultModal(self))
        except Exception as e:
            await interaction.response.send_message(f"⚠️ Error al abrir el editor: {e}", ephemeral=True)

    @discord.ui.button(label="Ajustar PJ", style=discord.ButtonStyle.secondary, emoji="✏️", custom_id="persistent_quick_custody")
    async def quick_custody(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_is_admin(interaction.user, interaction.guild):
            await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden modificar la custodia del baúl.", ephemeral=True)
            return
        try:
            await interaction.response.send_modal(QuickCustodyModal(self))
        except Exception as e:
            await interaction.response.send_message(f"⚠️ Error al abrir el editor: {e}", ephemeral=True)

    @discord.ui.button(label="Refrescar", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="persistent_refresh_vault")
    async def refresh_vault(self, interaction: discord.Interaction, button: discord.ui.Button):
        import ai_assistant
        new_embed = ai_assistant.build_vault_embed()
        await interaction.response.edit_message(embed=new_embed, view=self)

def parse_points_line(line: str) -> Optional[Tuple[str, int, Optional[str], Optional[int]]]:
    clean = line.strip(" -*•\t")
    if not clean:
        return None
    # Strip leading medals/numbers (e.g. '🥇 ', '1. ', '`1.` ')
    clean = re.sub(r'^[🥇🥈🥉]\s*', '', clean)
    clean = re.sub(r'^`?\d+[\.\)]`?\s*', '', clean)

    # Extract darks cobradas if present (e.g. '(4D cobradas)', '(4 Dark Cobradas)', '(2D)')
    darks_paid = None
    d_match = re.search(r'\((\d+)\s*(?:d\b|darks?\s*(?:cobradas?|pagadas?))', clean, re.I)
    if d_match:
        darks_paid = int(d_match.group(1))
        clean = re.sub(r'\(\s*' + re.escape(d_match.group(0)[1:]) + r'.*?\)', '', clean)

    # Extract clan if present
    clan = None
    if re.search(r'[\(\[]?\s*pollokill\s*[\)\]]?', clean, re.I):
        clan = 'PolloKill'
    elif re.search(r'[\(\[]?\s*(?:dxmentor|mentores?)\s*[\)\]]?', clean, re.I):
        clan = 'DxMENTOR'

    # Remove clan tags and arrow annotations
    clean = re.sub(r'[\(\[]\s*(?:pollokill|dxmentor|mentores?)\s*[\)\]]', '', clean, flags=re.I)
    clean = re.sub(r'➔.*$', '', clean)
    clean = re.sub(r'🎁.*$', '', clean)
    clean = clean.strip(' :;-,')

    # Format A: Name [:=-]? Points [pts]
    m1 = re.search(r'^([A-Za-z0-9_\-\./]+)\s*[:\-=]?\s*(\d+)(?:\s*(?:pts?|puntos?|p))?$', clean, re.I)
    if m1 and not m1.group(1).isdigit():
        return m1.group(1), int(m1.group(2)), clan, darks_paid

    # Format B: Points [pts] Name
    m2 = re.search(r'^(\d+)(?:\s*(?:pts?|puntos?|p))?\s*(?:de|a|para)?\s*([A-Za-z0-9_\-\./]+)$', clean, re.I)
    if m2 and not m2.group(2).isdigit():
        return m2.group(2), int(m2.group(1)), clan, darks_paid

    # Format C: General search for name and number
    m3 = re.search(r'([A-Za-z0-9_\-\./]+)\s*[:\-=]?\s*(\d+)', clean)
    if m3 and not m3.group(1).isdigit():
        return m3.group(1), int(m3.group(2)), clan, darks_paid

    return None

class EditPointsListModal(discord.ui.Modal, title="📝 Editar Lista de Puntos"):
    points_input = discord.ui.TextInput(
        label="Jugadores y Puntos (un PJ por línea)",
        style=discord.TextStyle.paragraph,
        placeholder="Ej: Bonielf: 6 (PolloKill) (4D cobradas)\nCamarlengo: 32 (PolloKill)",
        required=True,
        max_length=4000
    )

    def __init__(self, parent_view: "PointsListView"):
        super().__init__()
        self.parent_view = parent_view
        current_players = db.get_all_players_ranking(limit=self.parent_view.limit, clan=self.parent_view.clan)
        lines = []
        for p in current_players:
            clan_val = p['clan'] if ('clan' in p.keys() and p['clan']) else ""
            clan_str = f" ({clan_val})" if clan_val else ""
            d_paid = p['darks_paid'] if ('darks_paid' in p.keys() and p['darks_paid']) else 0
            d_str = f" ({d_paid}D cobrada{'s' if d_paid > 1 else ''})" if d_paid > 0 else ""
            lines.append(f"{p['pj_name']}: {p['current_points']}{clan_str}{d_str}")
        full_text = "\n".join(lines) if lines else "Bonielf: 6 (PolloKill) (4D cobradas)\nBuferita: 21 (DxMENTOR)"
        self.points_input.default = full_text[:3990]

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception:
            pass

        text = self.points_input.value
        items = []
        for line in text.splitlines():
            res = parse_points_line(line)
            if res:
                name, pts, clan, darks_paid = res
                items.append({
                    "pj_name": name,
                    "points": pts,
                    "clan": clan,
                    "darks_paid": darks_paid
                })

        if not items:
            await interaction.followup.send("⚠️ No se detectaron líneas válidas. Formato: `Personaje: Puntos (Clan)`", ephemeral=True)
            return

        count, summary = db.bulk_import_points(items, mode="SET", updated_by=interaction.user.name)
        await auto_backup_database()
        import ai_assistant
        new_embed = ai_assistant.build_points_list_embed(clan=self.parent_view.clan, limit=self.parent_view.limit)

        try:
            await interaction.edit_original_response(embed=new_embed, view=self.parent_view)
        except Exception:
            target_msg = getattr(self.parent_view, "message", None)
            if target_msg:
                try:
                    await target_msg.edit(embed=new_embed, view=self.parent_view)
                except Exception:
                    pass
            await interaction.followup.send(f"✅ Se actualizaron los puntos de **{count}** personajes con éxito en la base de datos.", ephemeral=True)

class QuickSetPointsModal(discord.ui.Modal, title="✏️ Ajustar Puntos de PJ"):
    pj_input = discord.ui.TextInput(
        label="Nombre del Personaje",
        placeholder="Ej: Bonielf o Camarlengo",
        required=True,
        max_length=40
    )
    points_input = discord.ui.TextInput(
        label="Puntos actuales acumulados",
        placeholder="Ej: 25",
        required=True,
        max_length=10
    )
    clan_input = discord.ui.TextInput(
        label="Clan (opcional)",
        placeholder="Ej: DxMENTOR o PolloKill",
        required=False,
        max_length=40
    )

    def __init__(self, parent_view: "PointsListView"):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        pj = self.pj_input.value.strip()
        raw_pts = self.points_input.value.strip()
        clan = self.clan_input.value.strip() or None
        if not raw_pts.isdigit():
            await interaction.followup.send("❌ Los puntos deben ser un número entero (ej: 0, 10, 25).", ephemeral=True)
            return
        pts = int(raw_pts)
        item = [{"pj_name": pj, "points": pts, "clan": clan or "DxMENTOR"}]
        count, summary = db.bulk_import_points(item, mode="SET", updated_by=interaction.user.name)
        await auto_backup_database()
        import ai_assistant
        new_embed = ai_assistant.build_points_list_embed(clan=self.parent_view.clan, limit=self.parent_view.limit)

        try:
            await interaction.edit_original_response(embed=new_embed, view=self.parent_view)
        except Exception:
            target_msg = getattr(self.parent_view, "message", None)
            if target_msg:
                try:
                    await target_msg.edit(embed=new_embed, view=self.parent_view)
                except Exception:
                    pass
            await interaction.followup.send(f"✅ Se actualizaron los puntos de **{pj}** a **{pts} pts**.", ephemeral=True)

class PointsListView(discord.ui.View):
    def __init__(self, author_id: Optional[int] = None, clan: Optional[str] = None, limit: int = 100):
        super().__init__(timeout=None) # Nunca expira (persistente)
        self.author_id = author_id
        self.clan = clan
        self.limit = limit
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label="Editar Lista de Puntos", style=discord.ButtonStyle.primary, emoji="📝", custom_id="persistent_edit_points_list")
    async def edit_points_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_is_admin(interaction.user, interaction.guild):
            await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden modificar la lista de puntos.", ephemeral=True)
            return
        try:
            await interaction.response.send_modal(EditPointsListModal(self))
        except Exception as e:
            await interaction.response.send_message(f"⚠️ Error al abrir el editor: {e}", ephemeral=True)

    @discord.ui.button(label="Ajustar PJ", style=discord.ButtonStyle.secondary, emoji="✏️", custom_id="persistent_quick_set_points")
    async def quick_set_points(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_is_admin(interaction.user, interaction.guild):
            await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden modificar la lista de puntos.", ephemeral=True)
            return
        try:
            await interaction.response.send_modal(QuickSetPointsModal(self))
        except Exception as e:
            await interaction.response.send_message(f"⚠️ Error al abrir el editor: {e}", ephemeral=True)

    @discord.ui.button(label="Refrescar", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="persistent_refresh_points")
    async def refresh_points(self, interaction: discord.Interaction, button: discord.ui.Button):
        import ai_assistant
        new_embed = ai_assistant.build_points_list_embed(clan=self.clan, limit=self.limit)
        await interaction.response.edit_message(embed=new_embed, view=self)

class ConfirmRosterImportView(discord.ui.View):
    def __init__(self, author_id: int, items: List[Dict], cutoff_date: Optional[str] = None):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.items = items
        self.cutoff_date = cutoff_date

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not check_is_admin(interaction.user, interaction.guild):
            await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden importar o modificar puntos.", ephemeral=True)
            return False
        if interaction.user.id != self.author_id and not (isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("❌ Solo quien envió la lista o un administrador puede confirmar la importación.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="📥 Fijar Puntos (Inicializar Roster)", style=discord.ButtonStyle.primary, emoji="📋")
    async def confirm_set(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        count, summary = db.bulk_import_points(self.items, mode="SET", updated_by=interaction.user.name)
        date_str = f" [Al {self.cutoff_date}]" if self.cutoff_date else ""
        embed = discord.Embed(
            title=f"✅ Roster Inicializado ({count} Personajes){date_str}",
            description=f"Se establecieron los puntajes iniciales y se asociaron los clanes y alias correspondientes.\n\n" + "\n".join(summary[:30]),
            color=discord.Color.green()
        )
        if len(summary) > 30:
            embed.set_footer(text=f"... y {len(summary) - 30} personajes más.")
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="➕ Sumar a Puntos Actuales", style=discord.ButtonStyle.success, emoji="➕")
    async def confirm_add(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        count, summary = db.bulk_import_points(self.items, mode="ADD", updated_by=interaction.user.name)
        date_str = f" [Al {self.cutoff_date}]" if self.cutoff_date else ""
        embed = discord.Embed(
            title=f"✅ Puntos Sumados ({count} Personajes){date_str}",
            description=f"Se sumaron los puntos indicados al acumulado de cada personaje.\n\n" + "\n".join(summary[:30]),
            color=discord.Color.green()
        )
        if len(summary) > 30:
            embed.set_footer(text=f"... y {len(summary) - 30} personajes más.")
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel_import(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        embed = discord.Embed(
            title="🚫 Importación Cancelada",
            description="La carga de la lista fue cancelada. No se modificó ningún dato.",
            color=discord.Color.light_grey()
        )
        await interaction.response.edit_message(embed=embed, view=None)

class ImportRosterModal(discord.ui.Modal, title="📥 Importar Lista de Puntos por Clan"):
    texto = discord.ui.TextInput(
        label="Pegá acá la lista (Clanes y PJs)",
        style=discord.TextStyle.paragraph,
        placeholder="PolloKill\nHisterico 9p\nSpark/Chispa 11p (1D)\n\nDxMENTOR\nMaster 10p (1d)\n(hasta el 18/10)...",
        required=True,
        max_length=4000
    )

    async def on_submit(self, interaction: discord.Interaction):
        items, cutoff_date = parse_roster_text(self.texto.value)
        if not items:
            await interaction.response.send_message("⚠️ No se pudieron detectar personajes con puntos en el texto ingresado. Verificá el formato (ej: `Pj 10p`).", ephemeral=True)
            return

        preview_embed = build_roster_preview_embed(items, cutoff_date=cutoff_date)
        view = ConfirmRosterImportView(author_id=interaction.user.id, items=items, cutoff_date=cutoff_date)
        await interaction.response.send_message(embed=preview_embed, view=view)

# ====================================================================
# FUNCIÓN CENTRAL PARA EJECUTAR Y REGISTRAR EL REPARTO
# ====================================================================

def execute_and_record_event(
    event_name: str,
    darks_count: int,
    pj_names: Union[List[str], List[Dict[str, str]]],
    registered_by: str,
    holder: Optional[str] = None
) -> Tuple[discord.Embed, Optional[discord.File]]:
    clean_pjs = []
    clan_by_pj = {}
    for p in pj_names:
        if isinstance(p, dict):
            name = db.resolve_pj_name(p["name"])
            clan = p.get("clan") or "Mentores"
        else:
            name = db.resolve_pj_name(str(p))
            clan = "Mentores"
        clean_pjs.append(name)
        clan_by_pj[name.lower()] = clan

    dist = logic.calculate_distribution(darks_count, clean_pjs)

    # Preparar datos para registrar en la base de datos
    participant_results = []
    for p in dist["results"]:
        pj = p["pj_name"]
        clan = clan_by_pj.get(pj.lower(), "Mentores")
        participant_results.append({
            "pj_name": pj,
            "fixed": p["fixed"],
            "raffle": p["raffle"],
            "won_raffle": p["won_raffle"],
            "roll": p["total_roll"],
            "clan": clan
        })

    event_id = db.record_event(
        event_name=event_name,
        darks_count=darks_count,
        total_points=dist["total_points"],
        fixed_per_pj=dist["fixed_points"],
        leftover_points=dist["leftover_points"],
        registered_by=registered_by,
        participant_results=participant_results
    )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(auto_backup_database())
    except Exception:
        pass

    # Armar Embed con diseño premium y transparencia total
    embed = discord.Embed(
        title=f"⚔️ Evento Completado: {event_name} (ID #{event_id})",
        color=discord.Color.gold()
    )

    desc_lines = [
        f"💎 **Darks Dropeadas:** {darks_count} ({dist['total_points']} puntos)",
        f"👥 **Participantes:** {dist['num_participants']}",
        f"📌 **Puntos Fijos por PJ:** {dist['fixed_points']} pts ({dist['fixed_points'] * dist['num_participants']} pts repartidos)",
        f"🎲 **Puntos a Sortear:** {dist['leftover_points']} pts"
    ]

    # Asignar custodia física al baúl si se indicó quién las guarda
    if holder:
        try:
            db.add_dark_custody(holder, darks_count, registered_by)
            desc_lines.append(f"🏛️ **Custodia física:** Se sumaron **{darks_count} Dark{'s' if darks_count > 1 else ''}** en el baúl bajo custodia de **{holder}**.")
        except Exception as e:
            print(f"Error registrando custodia en evento: {e}", flush=True)

    embed.description = "\n".join(desc_lines)

    gif_path = None

    # Detalle del sorteo si hubo sobrantes
    if dist["leftover_points"] > 0:
        winners = [p["pj_name"] for p in dist["results"] if p["won_raffle"]]
        # Perdedores ordenados de menor a mayor tirada
        sorted_losers = sorted([p for p in dist["results"] if not p["won_raffle"]], key=lambda x: x["total_roll"])
        losers = [p["pj_name"] for p in sorted_losers]
        all_pjs = [p["pj_name"] for p in dist["results"]]

        # Si hay menos perdedores que ganadores (ej: 4 perdedores vs 10 ganadores),
        # sorteamos a los perdedores para hacer muchos menos giros de ruleta
        sort_by_losers = bool(losers and len(losers) < len(winners))

        raffle_lines = []
        # Mostrar ordenados por su tirada final
        sorted_rolls = sorted(dist["results"], key=lambda x: x["total_roll"], reverse=True)
        for p in sorted_rolls:
            streak_str = f" ⭐ +{p['bonus']} (Racha {p['streak']})" if p['streak'] > 0 else ""
            if p["won_raffle"]:
                raffle_lines.append(f"🏆 **{p['pj_name']}**: 🎲 {p['base_roll']}{streak_str} = **{p['total_roll']}** ➔ **+1 punto extra!**")
            else:
                raffle_lines.append(f"❌ **{p['pj_name']}**: 🎲 {p['base_roll']}{streak_str} = {p['total_roll']}")

        mode_note = f" *(Ruleta girada a los {len(losers)} que quedaron fuera)*" if sort_by_losers else ""
        add_safe_fields(embed, f"🎲 Sorteo Transparente (Tirada d100 + Bono de Racha){mode_note}", raffle_lines)

        # Generar animación de Ruleta en GIF
        if winners and len(all_pjs) > 1:
            try:
                g_path = f"ruleta_ev_{event_id}.gif"
                target_names = losers if sort_by_losers else winners
                roulette.generate_roulette_gif(
                    all_pjs,
                    winner_names=target_names,
                    output_path=g_path,
                    size=360,
                    is_loser_mode=sort_by_losers
                )
                if os.path.exists(g_path):
                    gif_path = g_path
                    embed.set_image(url="attachment://ruleta.gif")
            except Exception as e:
                print(f"Error generando ruleta animada: {e}", flush=True)

    # Resumen de Puntos sumados en este evento
    summary_lines = []
    ready_to_payout = []

    for p in dist["results"]:
        total_p = p["fixed"] + p["raffle"]
        pj = p["pj_name"]
        clan = db.resolve_clan_name(clan_by_pj.get(pj.lower(), "DxMENTOR"))
        player_db = db.get_player(pj)
        curr = player_db["current_points"] if player_db else total_p
        summary_lines.append(f"• **{pj}** `[{clan}]`: +{total_p} pts *(Total acumulado: {curr} pts)*")

        if curr >= 10:
            darks_owed = curr // 10
            ready_to_payout.append(f"💎 **{pj}** `[{clan}]` acumula {curr} pts (Puede cobrar **{darks_owed} Dark{'s' if darks_owed > 1 else ''}**)")

    add_safe_fields(embed, "📊 Resumen de Puntos Ganados", summary_lines)

    if ready_to_payout:
        add_safe_fields(embed, "🎁 ¡Darks Listas para Cobrar! (/pagar)", ready_to_payout)

    embed.set_footer(text=f"Registrado por {registered_by} • ArgenMU Bot")
    return embed, gif_path

def execute_direct_raffle_event(
    event_name: str,
    total_rewards: int,
    reward_name: str,
    pj_names: Union[List[str], List[Dict[str, Any]]],
    registered_by: str
) -> Tuple[discord.Embed, Optional[discord.File], Optional[str]]:
    """
    Ejecuta un reparto directo de premios (exes, darks o items) en el momento,
    realizando ruleta transparente si hay sobrantes o vacantes,
    SIN registrar ni sumar puntos a la tabla general de miembros.
    """
    clean_pjs = []
    clan_by_pj = {}
    for p in pj_names:
        if isinstance(p, dict):
            name = db.resolve_pj_name(p["name"])
            clan = p.get("clan") or "DxMENTOR"
        else:
            name = db.resolve_pj_name(str(p))
            clan = "DxMENTOR"
        clean_pjs.append(name)
        clan_by_pj[name.lower()] = db.resolve_clan_name(clan)

    dist = logic.calculate_direct_reward_distribution(
        total_rewards=total_rewards,
        pj_names=clean_pjs,
        reward_name=reward_name
    )

    embed = discord.Embed(
        title=f"🎁 Sorteo Directo de Evento: {event_name}",
        color=discord.Color.purple()
    )

    desc_lines = [
        f"📦 **Premios a Repartir:** **{total_rewards} {reward_name}** *(reparto directo en el momento)*",
        f"👥 **Participantes:** {dist['num_participants']}",
    ]
    if dist["fixed_rewards"] > 0:
        desc_lines.append(f"📌 **Premios Fijos asegurados:** **{dist['fixed_rewards']} {reward_name}** c/u ({dist['fixed_rewards'] * dist['num_participants']} {reward_name} repartidos)")
    if dist["leftover_rewards"] > 0:
        desc_lines.append(f"🎲 **Premios en Sorteo:** **{dist['leftover_rewards']} {reward_name}**")

    desc_lines.append("\n⚠️ **NOTA:** Este sorteo fue independiente y **NO suma puntos al ranking** general.")
    embed.description = "\n".join(desc_lines)

    gif_file = None
    won_url = None

    if dist["leftover_rewards"] > 0:
        winners = [p["pj_name"] for p in dist["results"] if p["won_raffle"]]
        sorted_losers = sorted([p for p in dist["results"] if not p["won_raffle"]], key=lambda x: x["total_roll"])
        losers = [p["pj_name"] for p in sorted_losers]
        all_pjs = [p["pj_name"] for p in dist["results"]]

        # Si hay menos perdedores que ganadores (ej: 4 perdedores vs 6 ganadores),
        # sorteamos a los perdedores en la ruleta para acelerar y hacer menos giros
        sort_by_losers = bool(losers and len(losers) < len(winners))

        raffle_lines = []
        sorted_rolls = sorted(dist["results"], key=lambda x: x["total_roll"], reverse=True)
        for p in sorted_rolls:
            if p["won_raffle"]:
                raffle_lines.append(f"🏆 **{p['pj_name']}**: 🎲 Tirada **{p['total_roll']}** ➔ **¡Gana {reward_name}!**")
            else:
                raffle_lines.append(f"❌ **{p['pj_name']}**: 🎲 Tirada {p['total_roll']} ➔ *(queda afuera)*")

        mode_note = f" *(Ruleta girada a los {len(losers)} que quedaron afuera)*" if sort_by_losers else ""
        add_safe_fields(embed, f"🎲 Sorteo Transparente (Tirada d100 en Vivo){mode_note}", raffle_lines)

        gif_path = None
        if winners and len(all_pjs) > 1:
            try:
                import uuid
                g_path = f"ruleta_directa_{uuid.uuid4().hex[:8]}.gif"
                target_names = losers if sort_by_losers else winners
                roulette.generate_roulette_gif(
                    all_pjs,
                    winner_names=target_names,
                    output_path=g_path,
                    size=360,
                    is_loser_mode=sort_by_losers,
                    loser_label="QUEDA AFUERA",
                    winner_label="GANA PREMIO"
                )
                if os.path.exists(g_path):
                    gif_path = g_path
                    embed.set_image(url="attachment://ruleta.gif")

                encoded_names = ",".join(all_pjs)
                won_url = f"https://wheelofnames.com/es/?names={encoded_names}"
            except Exception as e:
                print(f"Error generando ruleta animada directa: {e}", flush=True)

    # Resumen de reparto final
    summary_lines = []
    for p in dist["results"]:
        pj = p["pj_name"]
        clan = clan_by_pj.get(pj.lower(), "DxMENTOR")
        cant = p["total_won"]
        if cant > 0:
            summary_lines.append(f"• **{pj}** `[{clan}]`: ➔ 🎁 **{cant} {reward_name}**")
        else:
            summary_lines.append(f"• **{pj}** `[{clan}]`: ➔ *(0 {reward_name})*")

    add_safe_fields(embed, f"📋 Reparto Final de {reward_name}", summary_lines)

    embed.set_footer(text=f"Sorteo directo registrado por {registered_by} • ArgenMU Bot")
    return embed, gif_path, won_url

# ====================================================================
# COMANDOS SLASH (APP COMMANDS)
# ====================================================================

@bot.tree.command(name="evento", description="Registra un evento ganado, reparte puntos fijos y realiza sorteos transparentes.")
@app_commands.describe(
    nombre="Nombre del evento (Ej: Frozen, Narcondra, BC, DS)",
    darks="Cantidad de darks dropeadas (cada una vale 10 puntos)",
    pjs="Lista de nombres de personajes separados por comas o espacios (opcional si subís captura)",
    fecha="Fecha del evento (opcional, ej: 18/10, 18/10 20:30, ayer)",
    captura="Captura de pantalla de la party 1 (opcional)",
    captura_2="Captura de pantalla de la party 2 si eran 2 partys (opcional)"
)
async def cmd_evento(
    interaction: discord.Interaction,
    nombre: str,
    darks: int,
    pjs: Optional[str] = None,
    fecha: Optional[str] = None,
    captura: Optional[discord.Attachment] = None,
    captura_2: Optional[discord.Attachment] = None
):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden registrar eventos y cargar puntos.", ephemeral=True)
        return

    if darks <= 0:
        await interaction.response.send_message("❌ La cantidad de darks debe ser mayor a 0.", ephemeral=True)
        return

    if fecha and fecha.strip() and fecha.strip() not in nombre:
        nombre = f"{nombre} ({fecha.strip()})"

    # Caso 1: Se sube una o dos capturas de pantalla
    if captura or captura_2:
        await interaction.response.defer(thinking=True)
        detected_pjs = []
        gemini_key = os.getenv("GEMINI_API_KEY")

        all_imgs = []
        if captura:
            try:
                all_imgs.append(await captura.read())
            except Exception:
                pass
        if captura_2:
            try:
                all_imgs.append(await captura_2.read())
            except Exception:
                pass

        if gemini_key and all_imgs:
            from ocr_scanner import parse_with_gemini
            data = await asyncio.to_thread(
                parse_with_gemini,
                images_bytes=all_imgs,
                user_text=f"Evento: {nombre}",
                api_key=gemini_key.strip()
            )
            if data and data.get("characters"):
                detected_pjs = data["characters"]

        if not detected_pjs and all_imgs:
            from ocr_scanner import extract_names_from_image
            detected_pjs = await asyncio.to_thread(extract_names_from_image, all_imgs[0])

        # Si además ingresó texto con PJs, unirlos considerando clanes
        final_list = [{"name": n, "clan": "Mentores"} for n in detected_pjs]
        if pjs:
            clan_parsed = parse_clan_characters(pjs)
            existing_names = [p["name"].lower() for p in final_list]
            for c_name, members in clan_parsed.items():
                for m in members:
                    if m.lower() not in existing_names:
                        final_list.append({"name": m, "clan": c_name})
                        existing_names.append(m.lower())
                    else:
                        for p in final_list:
                            if p["name"].lower() == m.lower() and c_name != "Mentores":
                                p["clan"] = c_name

        view = PartyReviewView(
            author_id=interaction.user.id,
            event_name=nombre,
            darks_count=darks,
            pjs=final_list
        )
        embed = view.build_embed()
        msg = await interaction.followup.send(embed=embed, view=view)
        view.message = msg
        return

    # Caso 2: Se ingresó la lista de PJs como texto directo
    if not pjs:
        await interaction.response.send_message(
            "❌ Debes ingresar la lista de `pjs` por texto o adjuntar una `captura` de pantalla de la party.",
            ephemeral=True
        )
        return

    clan_parsed = parse_clan_characters(pjs)
    final_list = []
    for c_name, members in clan_parsed.items():
        for m in members:
            final_list.append({"name": m, "clan": c_name})

    if not final_list:
        names = [n.strip() for n in re.split(r'[,;\s]+', pjs) if n.strip()]
        final_list = [{"name": n, "clan": "Mentores"} for n in names]

    if not final_list:
        await interaction.response.send_message("❌ No se detectó ningún nombre de personaje válido.", ephemeral=True)
        return

    view = PartyReviewView(
        author_id=interaction.user.id,
        event_name=nombre,
        darks_count=darks,
        pjs=final_list
    )
    embed = view.build_embed()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="sorteo_directo", description="Sorteo de premios o exes en el momento por evento SIN sumar puntos a la lista.")
@app_commands.describe(
    premios="Cantidad de premios a repartir (ej: '6 exe', '2 darks', '6', '12')",
    participantes="Nombres de personajes separados por coma o espacio (opcional si subes captura)",
    captura="Captura de pantalla de la party para detectar PJs con IA/OCR (opcional)",
    captura_2="Segunda captura de pantalla para Party 2 (opcional)",
    evento="Nombre del evento (ej: BC8, DS, etc., opcional)"
)
async def cmd_sorteo_directo(
    interaction: discord.Interaction,
    premios: str,
    participantes: Optional[str] = None,
    captura: Optional[discord.Attachment] = None,
    captura_2: Optional[discord.Attachment] = None,
    evento: Optional[str] = None
):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden realizar sorteos.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    detected_pjs = []

    # 1. Si adjuntó capturas, procesar con IA / OCR
    if captura or captura_2:
        all_imgs = []
        if captura:
            try:
                all_imgs.append(await captura.read())
            except Exception:
                pass
        if captura_2:
            try:
                all_imgs.append(await captura_2.read())
            except Exception:
                pass

        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key and all_imgs:
            from ocr_scanner import parse_with_gemini
            data = await asyncio.to_thread(
                parse_with_gemini,
                images_bytes=all_imgs,
                user_text=f"Sorteo directo {evento or ''}",
                api_key=gemini_key.strip()
            )
            if data and data.get("characters"):
                detected_pjs.extend(data["characters"])

        if not detected_pjs and all_imgs:
            from ocr_scanner import extract_names_from_image
            fallback_chars = await asyncio.to_thread(extract_names_from_image, all_imgs[0])
            if fallback_chars:
                detected_pjs.extend(fallback_chars)

    # 2. Si pasó participantes por texto
    if participantes:
        for token in re.split(r'[,;\s]+', participantes):
            token_clean = token.strip(" ,.-_:")
            if is_valid_character_name(token_clean):
                detected_pjs.append(token_clean)

    if not detected_pjs:
        await interaction.followup.send(
            "❌ No se detectaron personajes. Por favor ingresá los nombres en 'participantes' o adjuntá una captura clara de la party.",
            ephemeral=True
        )
        return

    # Normalizar personajes
    seen = set()
    final_pjs = []
    for p in detected_pjs:
        if is_valid_character_name(p):
            canonical = db.resolve_pj_name(p)
            if canonical.lower() not in seen:
                p_db = db.get_player(canonical)
                c_cand = p_db["clan"] if (p_db and p_db["clan"]) else "DxMENTOR"
                final_pjs.append({
                    "name": canonical,
                    "clan": db.resolve_clan_name(c_cand),
                    "party": "Party 1"
                })
                seen.add(canonical.lower())

    total_rewards, reward_name = parse_reward_input(premios)
    ev_title = evento or "Sorteo Directo"

    embed, gif_file, won_url = await asyncio.to_thread(
        execute_direct_raffle_event,
        event_name=ev_title,
        total_rewards=total_rewards,
        reward_name=reward_name,
        pj_names=final_pjs,
        registered_by=interaction.user.name
    )

    view = discord.ui.View()
    if won_url:
        view.add_item(discord.ui.Button(label="🎡 Ver en WheelOfNames (Web)", url=won_url, style=discord.ButtonStyle.link))

    if gif_file:
        await interaction.followup.send(embed=embed, file=gif_file, view=view)
    else:
        await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="deudas", description="Lista los jugadores que acumularon 10 o más puntos y tienen darks pendientes de cobro.")
@app_commands.describe(clan="Filtrar por clan específico (opcional, ej: Mentores)")
async def cmd_deudas(interaction: discord.Interaction, clan: Optional[str] = None):
    import ai_assistant
    embed = ai_assistant.build_debts_embed(clan=clan)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="pagar", description="Registra la entrega de darks a un personaje y descuenta sus puntos (10 pts por Dark).")
@app_commands.describe(
    personaje="Nombre del personaje in-game que recibe el pago",
    darks="Cantidad de darks entregadas",
    desde_baul_de="Personaje que entrega las darks desde su custodia/baúl (opcional)",
    nota="Nota u observación opcional"
)
async def cmd_pagar(
    interaction: discord.Interaction,
    personaje: str,
    darks: int,
    desde_baul_de: Optional[str] = None,
    nota: Optional[str] = ""
):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden registrar pagos de darks.", ephemeral=True)
        return

    if darks <= 0:
        await interaction.response.send_message("❌ La cantidad de darks a pagar debe ser mayor a 0.", ephemeral=True)
        return

    success, msg = db.record_payout(
        pj_name=personaje,
        darks_to_pay=darks,
        registered_by=interaction.user.name,
        note=nota or "",
        from_holder=desde_baul_de
    )

    if not success:
        embed = discord.Embed(title="❌ Error al registrar pago", description=msg, color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    player = db.get_player(personaje)
    desc_lines = [
        f"Se registraron **{darks} Dark{'s' if darks > 1 else ''}** entregadas a **{personaje}**.",
        f"Se descontaron **{darks * 10} puntos**.\n"
    ]
    if desde_baul_de:
        desc_lines.append(f"🏛️ **Almacenamiento:** Se descontaron de la custodia de **{desde_baul_de}**.")
    desc_lines.extend([
        f"📌 **Puntos actuales restantes:** {player['current_points']} pts",
        f"📦 **Total histórico cobrado:** {player['darks_paid']} Darks",
        f"👤 **Registrado por:** {interaction.user.name}"
    ])

    embed = discord.Embed(
        title="✅ Pago de Darks Registrado",
        description="\n".join(desc_lines),
        color=discord.Color.green()
    )
    if nota:
        embed.add_field(name="Nota", value=nota, inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ranking", description="Muestra la tabla de clasificación de puntos y participación de los jugadores.")
@app_commands.describe(
    limite="Cantidad de jugadores a mostrar (por defecto 25, hasta 100)",
    clan="Filtrar por clan específico (opcional, ej: DxMENTOR o PolloKill)"
)
async def cmd_ranking(interaction: discord.Interaction, limite: Optional[int] = 25, clan: Optional[str] = None):
    limite = max(5, min(limite or 25, 100))
    import ai_assistant
    embed = ai_assistant.build_points_list_embed(clan=clan, limit=limite)
    view = PointsListView(author_id=interaction.user.id, clan=clan, limit=limite)
    await interaction.response.send_message(embed=embed, view=view)
    try:
        view.message = await interaction.original_response()
    except Exception:
        pass

@bot.tree.command(name="perfil", description="Consulta las estadísticas, puntos y darks de un personaje.")
@app_commands.describe(personaje="Nombre del personaje a consultar")
async def cmd_perfil(interaction: discord.Interaction, personaje: str):
    player = db.get_player(personaje)
    if not player:
        embed = discord.Embed(
            title="🔍 Personaje no encontrado",
            description=f"El personaje **{personaje}** aún no ha participado en ningún evento registrado.",
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    darks_owed = player["current_points"] // 10
    rem_pts = player["current_points"] % 10

    embed = discord.Embed(
        title=f"👤 Perfil de Personaje: {player['pj_name']}",
        color=discord.Color.teal()
    )
    embed.add_field(name="💰 Puntos Actuales", value=f"**{player['current_points']}** pts", inline=True)
    embed.add_field(name="🎁 Darks por Cobrar", value=f"**{darks_owed}** ({rem_pts} pts restantes)", inline=True)
    embed.add_field(name="📦 Darks ya Cobradas", value=f"**{player['darks_paid']}** Darks", inline=True)

    embed.add_field(name="⚔️ Eventos Asistidos", value=f"**{player['events_attended']}**", inline=True)
    embed.add_field(name="📈 Puntos Históricos", value=f"**{player['total_points_history']}** pts", inline=True)
    embed.add_field(name="🎲 Racha sin ganar Sorteo", value=f"**{player['raffle_streak_lost']}** eventos", inline=True)

    if player["discord_id"]:
        embed.set_footer(text=f"Vinculado a la cuenta de Discord <@{player['discord_id']}>")

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="vincular", description="Vincula tu cuenta de Discord con tu personaje in-game.")
@app_commands.describe(personaje="Nombre de tu personaje principal en ArgenMU")
async def cmd_vincular(interaction: discord.Interaction, personaje: str):
    clean_name = personaje.strip()
    db.link_discord_account(clean_name, interaction.user.id)
    embed = discord.Embed(
        title="🔗 Cuenta Vinculada",
        description=f"Se vinculó tu cuenta de Discord ({interaction.user.mention}) con el personaje **{clean_name}**.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="baul", description="Muestra quiénes tienen en custodia las darks físicas del gremio.")
async def cmd_baul(interaction: discord.Interaction):
    import ai_assistant
    embed = ai_assistant.build_vault_embed()
    view = VaultView(author_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)
    try:
        view.message = await interaction.original_response()
    except Exception:
        pass

@bot.tree.command(name="custodia", description="Registra cuántas darks físicas tiene un miembro en su baúl/inventario.")
@app_commands.describe(
    personaje="Nombre del personaje que guarda las darks",
    darks="Cantidad total de darks que tiene",
    nota="Nota u observación opcional"
)
async def cmd_custodia(interaction: discord.Interaction, personaje: str, darks: int, nota: Optional[str] = ""):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden actualizar la custodia del baúl.", ephemeral=True)
        return

    if darks < 0:
        await interaction.response.send_message("❌ La cantidad de darks no puede ser negativa.", ephemeral=True)
        return
    ok, msg = db.set_dark_custody(personaje, darks, interaction.user.name, nota or "")
    embed = discord.Embed(
        title="🏛️ Custodia de Darks Actualizada",
        description=msg,
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="baul_transferir", description="Transfiere la custodia de darks de un miembro a otro en el baúl del clan.")
@app_commands.describe(
    desde="Personaje que entrega las darks (quien las tenía)",
    hacia="Personaje que recibe las darks (quien las guardará ahora)",
    darks="Cantidad de darks a transferir",
    nota="Nota u observación opcional"
)
async def cmd_baul_transferir(interaction: discord.Interaction, desde: str, hacia: str, darks: int, nota: Optional[str] = ""):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden transferir custodia en el baúl.", ephemeral=True)
        return

    if darks <= 0:
        await interaction.response.send_message("❌ La cantidad de darks debe ser mayor a 0.", ephemeral=True)
        return

    ok, msg = db.transfer_dark_custody(desde, hacia, darks, interaction.user.name, nota or "")
    embed = discord.Embed(
        title="🏛️ Transferencia de Custodia en Baúl",
        description=msg,
        color=discord.Color.green() if ok else discord.Color.red()
    )
    if ok:
        embed.set_footer(text="Consultá /baul para ver el estado general o /baul_historial para ver el registro.")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="baul_historial", description="Muestra el historial de transferencias, cargas y entregas del baúl.")
@app_commands.describe(limite="Cantidad de movimientos a mostrar (por defecto 15)")
async def cmd_baul_historial(interaction: discord.Interaction, limite: Optional[int] = 15):
    import ai_assistant
    embed = ai_assistant.build_vault_history_embed(limit=limite or 15)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="baul_reiniciar", description="Reinicia a cero el baúl físico de darks (para cargar el inventario desde cero).")
async def cmd_baul_reiniciar(interaction: discord.Interaction):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden reiniciar el baúl.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚠️ Confirmación: Reiniciar Baúl de Darks",
        description=(
            "¿Estás seguro de que deseas vaciar y **reiniciar a cero** la custodia del baúl de Darks?\n\n"
            "• Todos los miembros quedarán con 0 darks en almacenamiento.\n"
            "• Esta acción quedará asentada en `/baul_historial`.\n"
            "• *(Los puntos de eventos de los miembros y la lista de administradores se mantienen intactos)*.\n\n"
            "Presioná el botón de confirmación abajo si deseas continuar."
        ),
        color=discord.Color.orange()
    )
    view = ConfirmVaultResetView(author_id=interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="eliminar_pj", description="Elimina un personaje de la base de datos (para borrar errores o personajes de prueba).")
@app_commands.describe(personaje="Nombre del personaje a eliminar")
async def cmd_eliminar_pj(interaction: discord.Interaction, personaje: str):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden eliminar personajes.", ephemeral=True)
        return

    ok, msg = db.delete_player(personaje)
    embed = discord.Embed(
        title="🗑️ Eliminar Personaje",
        description=msg,
        color=discord.Color.green() if ok else discord.Color.red()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ajustar_puntos", description="Modifica manualmente los puntos actuales de un personaje.")
@app_commands.describe(personaje="Nombre del personaje", puntos="Nuevo puntaje actual")
async def cmd_ajustar_puntos(interaction: discord.Interaction, personaje: str, puntos: int):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden modificar puntos manualmente.", ephemeral=True)
        return

    ok, msg = db.update_player_points(personaje, puntos)
    embed = discord.Embed(
        title="✏️ Ajustar Puntos",
        description=msg,
        color=discord.Color.green() if ok else discord.Color.red()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="cargar_puntos_manual", description="Carga o suma una lista manual de personajes con sus puntos.")
@app_commands.describe(
    lista="Texto con los nombres y puntos (ej: Titan: 12, masterr: 8, Buferit4: 15)",
    modo="Elegí si querés sumar los puntos a los que ya tienen o fijar los puntos nuevos"
)
@app_commands.choices(modo=[
    app_commands.Choice(name="Sumar a los puntos actuales (+)", value="ADD"),
    app_commands.Choice(name="Reemplazar / Fijar puntos actuales (=)", value="SET")
])
async def cmd_cargar_puntos_manual(interaction: discord.Interaction, lista: str, modo: app_commands.Choice[str]):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden cargar listas de puntos.", ephemeral=True)
        return

    await interaction.response.defer()
    import ai_assistant
    intent_data = await ai_assistant.process_user_intent(
        user_text=f"Importar esta lista manual con modo {modo.value}: {lista}",
        author_name=interaction.user.name
    )

    items = intent_data.get("import_items", []) if intent_data else []
    if not items:
        # Intento de parseo por expresiones regulares si no vino de la IA
        import re
        lines = re.split(r'[,;\n]+', lista)
        for line in lines:
            m = re.search(r'([a-zA-Z0-9_\-\[\]]+)\s*[:=\s]\s*(\d+)', line)
            if m:
                items.append({"pj_name": m.group(1), "points": int(m.group(2))})

    if not items:
        await interaction.followup.send("❌ No se pudieron detectar nombres y puntos en el texto ingresado. Ejemplo de formato: `Titan: 12, masterr: 8`", ephemeral=True)
        return

    count, summary = db.bulk_import_points(items, mode=modo.value, updated_by=interaction.user.name)
    embed = discord.Embed(
        title=f"📥 Lista Manual Cargada ({count} personajes)",
        description=f"Modo utilizado: **{'Sumar a puntos actuales (+)' if modo.value == 'ADD' else 'Reemplazar / Fijar puntos (=)'}**\n\n" + "\n".join(summary[:25]),
        color=discord.Color.green()
    )
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="autorizar_admin", description="Autoriza a un miembro para que pueda cargar eventos y modificar puntos.")
@app_commands.describe(usuario="Miembro de Discord al que deseas darle permisos de administración")
async def cmd_autorizar_admin(interaction: discord.Interaction, usuario: discord.Member):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Solo los administradores actuales pueden autorizar a nuevos administradores.", ephemeral=True)
        return
    ok, msg = db.add_admin(usuario.id, usuario.name, interaction.user.name)
    await interaction.response.send_message(msg, ephemeral=not ok)

@bot.tree.command(name="desautorizar_admin", description="Revoca los permisos de administración a un miembro.")
@app_commands.describe(usuario="Miembro de Discord al que deseas quitarle los permisos")
async def cmd_desautorizar_admin(interaction: discord.Interaction, usuario: discord.Member):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Solo los administradores actuales pueden revocar permisos.", ephemeral=True)
        return
    ok, msg = db.remove_admin(usuario.id)
    await interaction.response.send_message(msg, ephemeral=not ok)

@bot.tree.command(name="admins", description="Muestra la lista de usuarios autorizados para cargar eventos y modificar puntos.")
async def cmd_admins(interaction: discord.Interaction):
    admins = db.get_admins()
    if not admins:
        embed = discord.Embed(
            title="👥 Administradores Autorizados",
            description=(
                "Aún no hay administradores específicos configurados en la base de datos.\n"
                "*(Cualquier usuario con rol de Administrador en Discord puede usar `/autorizar_admin @usuario` para definir quiénes pueden cargar puntos)*"
            ),
            color=discord.Color.blue()
        )
    else:
        embed = discord.Embed(
            title="👥 Administradores Autorizados para Cargar Puntos",
            description="Solamente estos usuarios tienen permiso para cargar eventos, registrar pagos y modificar puntos:\n\n" + "\n".join([f"• <@{a['discord_id']}> (**{a['username']}**) — *Autorizado por {a['added_by']}*" for a in admins]),
            color=discord.Color.gold()
        )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="reiniciar_historial", description="Reinicia todos los puntos y borra el historial de eventos guardados (pide confirmación).")
async def cmd_reiniciar_historial(interaction: discord.Interaction):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden reiniciar los puntos del gremio.", ephemeral=True)
        return

    view = ConfirmResetView(author_id=interaction.user.id)
    embed = discord.Embed(
        title="⚠️ ¿Estás seguro de reiniciar los puntos y el historial?",
        description=(
            "**¡ATENCIÓN! Esta acción es irreversible.**\n\n"
            "Si confirmas esta acción:\n"
            "• Se **eliminarán todos los eventos** registrados hasta el momento.\n"
            "• Se **borrará el historial de pagos** de darks.\n"
            "• Se **reiniciarán todos los puntos** de los personajes para un nuevo ciclo.\n\n"
            "*(La custodia física de darks del baúl `/baul` y la lista de administradores se mantienen intactas)*.\n\n"
            "Presioná el botón abajo para confirmar o cancelar:"
        ),
        color=discord.Color.red()
    )
    embed.set_footer(text="Tenés 60 segundos para responder")
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="alias_agregar", description="Vincula un personaje secundario o alias a un personaje principal (ej: Chispa -> Spark/Chispa).")
@app_commands.describe(alias="Nombre del personaje secundario o alias (ej: Chispa)", principal="Nombre del personaje principal donde van los puntos (ej: Spark/Chispa)")
async def cmd_alias_agregar(interaction: discord.Interaction, alias: str, principal: str):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Solo los administradores autorizados pueden gestionar alias.", ephemeral=True)
        return
    ok, msg = db.add_alias(alias, principal)
    await interaction.response.send_message(msg, ephemeral=not ok)

@bot.tree.command(name="alias_quitar", description="Elimina la vinculación de un alias.")
@app_commands.describe(alias="Nombre del alias a desvincular")
async def cmd_alias_quitar(interaction: discord.Interaction, alias: str):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Solo los administradores autorizados pueden gestionar alias.", ephemeral=True)
        return
    ok, msg = db.remove_alias(alias)
    await interaction.response.send_message(msg, ephemeral=not ok)

@bot.tree.command(name="aliases", description="Muestra la lista de todos los alias y dobles personajes registrados.")
async def cmd_aliases(interaction: discord.Interaction):
    aliases = db.get_aliases()
    if not aliases:
        embed = discord.Embed(
            title="👥 Alias y Dobles Personajes",
            description="No hay alias registrados manualmente. Se registran automáticamente al cargar personajes con `/` (ej: `Spark/Chispa`) o usando `/alias_agregar`.",
            color=discord.Color.blue()
        )
    else:
        embed = discord.Embed(
            title="👥 Alias y Dobles Personajes Vinculados",
            description="Cualquier punto de evento jugado por el alias se asigna directamente al personaje principal:\n\n" + "\n".join([f"• `{a['alias']}` ➔ **{a['canonical_pj_name']}**" for a in aliases]),
            color=discord.Color.gold()
        )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="vincular_pjs", description="Vincula dos personajes de la misma persona y unifica sus puntos.")
@app_commands.describe(
    personaje_1="Primer personaje (ej: Themami)",
    personaje_2="Segundo personaje (ej: Stormfly)",
    nombre_unificado="Nombre final opcional (por defecto se unifican como PJ1/PJ2)"
)
async def cmd_vincular_pjs(interaction: discord.Interaction, personaje_1: str, personaje_2: str, nombre_unificado: Optional[str] = None):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden vincular personajes.", ephemeral=True)
        return
    ok, msg, info = db.link_players(personaje_1, personaje_2, unified_name=nombre_unificado)
    embed = discord.Embed(
        title="👥 Vinculación de Personajes",
        description=msg,
        color=discord.Color.gold() if ok else discord.Color.red()
    )
    if ok and info.get("canonical"):
        embed.add_field(name="🆔 Perfil Unificado", value=f"**`{info['canonical']}`**", inline=True)
        embed.add_field(name="⭐ Puntos Totales", value=f"**{info.get('combined_points', 0)} pts**", inline=True)
        embed.add_field(name="🏛️ Clan", value=f"{info.get('clan', 'Mentores')}", inline=True)
        embed.set_footer(text="A partir de ahora, cualquier punto o consulta con cualquiera de los nombres se asociará a este perfil.")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="importar_lista", description="Importa una lista completa de personajes con puntos dividida por clanes (abre ventana de texto).")
async def cmd_importar_lista(interaction: discord.Interaction):
    if not check_is_admin(interaction.user, interaction.guild):
        await interaction.response.send_message("⛔ Permiso denegado: Solo los administradores autorizados pueden importar puntos.", ephemeral=True)
        return
    await interaction.response.send_modal(ImportRosterModal())

@bot.tree.command(name="ruleta", description="Gira una ruleta animada en vivo entre los personajes ingresados para elegir un ganador.")
@app_commands.describe(participantes="Lista de nombres de personajes separados por coma o espacio")
async def cmd_ruleta(interaction: discord.Interaction, participantes: str):
    pjs = [n.strip() for n in re.split(r'[,;\s]+', participantes) if n.strip()]
    if len(pjs) < 2:
        await interaction.response.send_message("❌ Debes ingresar al menos 2 personajes para girar la ruleta.", ephemeral=True)
        return

    await interaction.response.defer()
    import random
    winner = random.choice(pjs)

    # Botón a Wheel of Names interactivo web
    encoded_names = ",".join(pjs)
    won_url = f"https://wheelofnames.com/es/?names={encoded_names}"
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="🎡 Ver en WheelOfNames (Web Interactiva)", url=won_url, style=discord.ButtonStyle.link))

    embed = discord.Embed(
        title="🎡 ¡Sorteo de Ruleta en Vivo!",
        description=f"👥 Participantes: **{', '.join(pjs)}**\n\n🎰 **¡Iniciando giro a máxima velocidad...!**",
        color=discord.Color.blue()
    )
    msg = await interaction.followup.send(embed=embed)

    gif_path = f"ruleta_sorteo_{interaction.id}.gif"
    gif_task = asyncio.to_thread(roulette.generate_roulette_gif, pjs, winner_name=winner, output_path=gif_path, size=360)

    # Animación en tiempo real en el chat de Discord
    anim_steps = [
        ("🎰 ⏩ Girando a toda velocidad...", 0.6),
        ("🎰 🌀 Girando por los sectores...", 0.6),
        ("⏳ ⚠️ Desacelerando...", 0.8),
        ("🎯 🛑 ¡Frenando en seco...!", 1.0)
    ]
    for status_label, delay in anim_steps:
        fake_choice = random.choice(pjs)
        embed.description = f"👥 Participantes: **{', '.join(pjs)}**\n\n{status_label}\n👉 Aguja pasando por: **`{fake_choice}`**"
        try:
            await interaction.edit_original_response(embed=embed)
        except Exception:
            pass
        await asyncio.sleep(delay)

    await gif_task
    gif_file = discord.File(gif_path, filename="ruleta.gif")

    embed_final = discord.Embed(
        title="🎉 ¡¡TENEMOS GANADOR DE LA RULETA!! 🎉",
        description=(
            f"👥 Participantes: **{', '.join(pjs)}**\n\n"
            f"🎯 **¡La ruleta se detuvo exactamente en:** 🏆 **`{winner.upper()}`**! 🏆\n\n"
            f"*(Podés abrir también la ruleta interactiva web en WheelOfNames con el botón inferior)*"
        ),
        color=discord.Color.gold()
    )
    embed_final.set_image(url="attachment://ruleta.gif")
    embed_final.set_footer(text=f"Iniciado por {interaction.user.name} • ArgenMU")
    await interaction.edit_original_response(embed=embed_final, attachments=[gif_file], view=view)

# ====================================================================
# PROCESAMIENTO DE MENSAJES EN LENGUAJE NATURAL CON IA
# ====================================================================

@bot.event
async def on_message(message: discord.Message):
    # Ignorar mensajes del propio bot
    if message.author.bot:
        return

    # Si el usuario está subiendo una segunda captura para la PartyReviewView, dejar que wait_for la tome
    if message.author.id in ACTIVE_CAPTURE_WAITERS:
        return

    is_mentioned = bot.user in message.mentions
    has_image = any(att.content_type and att.content_type.startswith("image/") for att in message.attachments)
    
    # 0. Chequeo automático de listas de puntos (Rosters de clanes en texto con >= 3 PJs)
    if not has_image:
        roster_items, cutoff_date = parse_roster_text(message.clean_content)
        if len(roster_items) >= 3:
            if not check_is_admin(message.author, message.guild):
                await message.reply(
                    "⛔ **Permiso denegado:** Solo los administradores autorizados pueden cargar listas de puntos.\n"
                    "*(Podés consultar información libremente usando `/ranking`, `/perfil`, `/baul` o `/deudas`)*"
                )
                return
            preview_embed = build_roster_preview_embed(roster_items, cutoff_date=cutoff_date)
            view = ConfirmRosterImportView(author_id=message.author.id, items=roster_items, cutoff_date=cutoff_date)
            await message.reply(embed=preview_embed, view=view)
            return

    # Si mencionan al bot (@Jinete Bot) o adjuntan una foto
    if is_mentioned or has_image:
        clean_content = message.clean_content.replace(f"@{bot.user.name}", "").strip()
        all_images = []

        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                try:
                    all_images.append(await att.read())
                except Exception:
                    pass

        gemini_key = os.getenv("GEMINI_API_KEY")

        if gemini_key and (clean_content or all_images):
            async with message.channel.typing():
                import ai_assistant
                intent_data = await ai_assistant.process_user_intent(
                    user_text=clean_content or "Evento con captura",
                    author_name=message.author.name,
                    images_bytes=all_images
                )

            # Extraer determinísticamente transferencias de baúl, darks, evento, pagos y vinculaciones desde el texto
            trans_from, trans_to, trans_darks = extract_transfer_details(clean_content)
            p_darks, p_event, p_holder = extract_event_details(clean_content)
            payout_holder, payout_recips, payout_darks = extract_payout_details(clean_content)
            custody_list = extract_custody_list(clean_content)
            link_p1, link_p2 = extract_link_players(clean_content)

            # Si se detectó una transferencia de custodia física (ej: 'Jinete le pasó 5 darks a Camarlengo')
            if trans_to and not has_image and (not intent_data or intent_data.get("intent") in ["CHAT", "PAY_DARK", "TRANSFER_CUSTODY"]):
                intent_data = {
                    "intent": "TRANSFER_CUSTODY",
                    "from_holder": trans_from,
                    "to_holder": trans_to,
                    "darks": trans_darks or 1
                }
            # Si se detectó una orden de pago en lenguaje natural (ej: 'Jinete le pagó dark a ...')
            elif payout_recips and not has_image and (not intent_data or intent_data.get("intent") in ["CHAT", "PAY_DARK"]):
                intent_data = {
                    "intent": "PAY_DARK",
                    "recipients": payout_recips,
                    "pj_name": payout_recips[0] if len(payout_recips) == 1 else None,
                    "from_holder": payout_holder,
                    "darks": payout_darks or 1
                }
            # Si se detectó declaración de custodia / almacenamiento (ej: '10 darks Jinete12 y 10 darks Carmelengo')
            elif custody_list and not has_image and (not intent_data or intent_data.get("intent") in ["CHAT", "SET_CUSTODY", "ADD_CUSTODY"]):
                intent_data = {
                    "intent": "SET_CUSTODY",
                    "custody_items": [{"holder": h, "darks": d} for h, d in custody_list]
                }
            # Si se detectó vinculación de personajes (ej: 'vincula a Themami con Stormfly, son la misma persona')
            elif link_p1 and link_p2 and not has_image and (not intent_data or intent_data.get("intent") in ["CHAT", "LINK_PLAYERS"]):
                intent_data = {
                    "intent": "LINK_PLAYERS",
                    "pj_name": link_p1,
                    "target_pj": link_p2
                }

            # Si se detectó una lista de personajes y puntos pegada directamente en el chat
            pasted_points = []
            if not has_image:
                for line in clean_content.splitlines():
                    parsed_res = parse_points_line(line)
                    if parsed_res:
                        p_name, p_pts, p_clan, p_dp = parsed_res
                        pasted_points.append({"pj_name": p_name, "points": p_pts, "clan": p_clan, "darks_paid": p_dp})

            if len(pasted_points) >= 2 and (not intent_data or intent_data.get("intent") in ["CHAT", "IMPORT_POINTS", "LIST_POINTS"]):
                mode = "SET" if any(w in clean_content.lower() for w in ["fijar", "reemplazar", "actualizar", "cambiar", "nueva lista", "lista actual", "corregir", "puntos"]) or len(pasted_points) >= 3 else "ADD"
                intent_data = {
                    "intent": "IMPORT_POINTS",
                    "mode": mode,
                    "import_items": pasted_points
                }

            # Fallback con OCR si Gemini no detectó personajes en las imágenes
            if has_image and (not intent_data or not intent_data.get("characters")):
                from ocr_scanner import extract_names_from_image
                fallback_parties = {}
                all_fallback_chars = []
                for idx, img_b in enumerate(all_images):
                    party_key = f"Party {idx + 1}"
                    chars = await asyncio.to_thread(extract_names_from_image, img_b)
                    if chars:
                        fallback_parties[party_key] = chars
                        all_fallback_chars.extend(chars)

                if all_fallback_chars:
                    if not intent_data:
                        intent_data = {}
                    intent_data["intent"] = "NEW_EVENT"
                    intent_data["characters"] = all_fallback_chars
                    intent_data["parties"] = fallback_parties
                    intent_data["darks"] = p_darks or 1
                    intent_data["event_name"] = p_event or "Evento ArgenMU"
                    if p_holder:
                        intent_data["holder"] = p_holder

            if not intent_data and has_image:
                await message.reply("⚠️ Recibí la captura, pero no pude detectar los personajes de la party en el recuadro superior derecho. Podés cargar el evento usando `/evento` o revisar que se vean claramente los nombres.")
                return

            if intent_data:
                # Si se adjuntó imagen y hay personajes, SIEMPRE tratar como NEW_EVENT
                if has_image and intent_data.get("characters"):
                    intent_data["intent"] = "NEW_EVENT"

                if p_darks:
                    intent_data["darks"] = p_darks
                if p_event:
                    intent_data["event_name"] = p_event
                if p_holder:
                    intent_data["holder"] = p_holder

                intent = intent_data.get("intent", "CHAT")

                # RESTRICCIÓN DE ACCIONES ADMINISTRATIVAS:
                ADMIN_INTENTS = {"NEW_EVENT", "PAY_DARK", "SET_CUSTODY", "ADD_CUSTODY", "TRANSFER_CUSTODY", "RESET_VAULT", "DELETE_PLAYER", "SET_POINTS", "IMPORT_POINTS", "RESET_HISTORY"}
                if (intent in ADMIN_INTENTS or has_image) and not check_is_admin(message.author, message.guild):
                    await message.reply(
                        "⛔ **Permiso denegado:** Solo los administradores autorizados pueden cargar capturas de eventos, modificar puntos o registrar pagos.\n"
                        "*(Podés consultar información libremente usando `/ranking`, `/perfil`, `/baul` o `/deudas`)*"
                    )
                    return

                # 0. CONSULTAS MÚLTIPLES SIMULTÁNEAS (ej: lista de puntos + quiénes faltan pagar + quién tiene las darks)
                multi_queries = detect_query_intents(clean_content)
                if intent_data.get("intents"):
                    for qi in intent_data["intents"]:
                        if qi in ["LIST_POINTS", "LIST_DEBTS", "VAULT_STATUS"] and qi not in multi_queries:
                            multi_queries.append(qi)

                MUTATION_INTENTS = {"NEW_EVENT", "PAY_DARK", "SET_CUSTODY", "ADD_CUSTODY", "TRANSFER_CUSTODY", "RESET_VAULT", "DELETE_PLAYER", "SET_POINTS", "IMPORT_POINTS", "RESET_HISTORY", "SPIN_ROULETTE"}
                if len(multi_queries) > 1 and not has_image and intent not in MUTATION_INTENTS:
                    embeds_to_send = []
                    for qi in multi_queries:
                        if qi == "LIST_POINTS":
                            embeds_to_send.append(ai_assistant.build_points_list_embed())
                        elif qi == "LIST_DEBTS":
                            embeds_to_send.append(ai_assistant.build_debts_embed())
                        elif qi == "VAULT_STATUS":
                            embeds_to_send.append(ai_assistant.build_vault_embed())

                    if embeds_to_send:
                        await message.reply(embeds=embeds_to_send[:10])
                        return

                # ACCIONES MÚLTIPLES O COMBINADAS EN UN SOLO MENSAJE (ej: borrar varios PJs y unificar otros)
                actions_list = intent_data.get("actions", [])
                if isinstance(actions_list, list) and len(actions_list) > 1 and not has_image:
                    action_results = []
                    for act in actions_list:
                        act_intent = act.get("intent")
                        if act_intent == "DELETE_PLAYER":
                            to_del = act.get("characters") or ([act.get("pj_name")] if act.get("pj_name") else [])
                            for d_name in to_del:
                                ok_del, msg_del = db.delete_player(d_name)
                                action_results.append(f"🗑️ {msg_del}")
                        elif act_intent == "LINK_PLAYERS":
                            lp1 = act.get("pj_name")
                            lp2 = act.get("target_pj")
                            if lp1 and lp2:
                                ok_l, msg_l, inf_l = db.link_players(lp1, lp2, unified_name=lp2)
                                action_results.append(f"👥 {msg_l}")
                        elif act_intent == "SET_POINTS":
                            p_pj = act.get("pj_name")
                            p_pts = act.get("points")
                            p_clan = act.get("clan")
                            p_dp = act.get("darks_paid")
                            if p_pj:
                                ok_sp, msg_sp = db.update_player_points(p_pj, new_points=p_pts, clan=p_clan, darks_paid=p_dp)
                                action_results.append(f"✏️ {msg_sp}")
                        elif act_intent == "PAY_DARK":
                            pay_recips = act.get("recipients") or act.get("characters") or ([act.get("pj_name")] if act.get("pj_name") else [])
                            for r in pay_recips:
                                ok_pd, msg_pd = db.pay_player_dark(r, 1, message.author.name)
                                action_results.append(f"🎁 {msg_pd}")

                    if action_results:
                        await auto_backup_database()
                        embed = discord.Embed(
                            title="✅ Acciones Múltiples Procesadas",
                            description="\n".join(action_results[:25]),
                            color=discord.Color.green()
                        )
                        embed.set_footer(text="Base de datos guardada y actualizada permanentemente.")
                        await message.reply(embed=embed)
                        return

                # 1. LISTA GENERAL DE PUNTOS
                if intent == "LIST_POINTS":
                    embed = ai_assistant.build_points_list_embed()
                    view = PointsListView(author_id=message.author.id, clan=None, limit=100)
                    reply_msg = await message.reply(embed=embed, view=view)
                    view.message = reply_msg
                    return

                # 2. INFORMACIÓN / PUNTOS DE UN PJ
                elif intent == "PLAYER_INFO":
                    pj = intent_data.get("pj_name")
                    if pj:
                        embed = ai_assistant.build_player_info_embed(pj)
                        await message.reply(embed=embed)
                        return

                # 3. HISTORIAL DE UN PJ
                elif intent == "PLAYER_HISTORY":
                    pj = intent_data.get("pj_name")
                    if pj:
                        embed = ai_assistant.build_player_history_embed(pj)
                        await message.reply(embed=embed)
                        return

                # 4. REGISTRAR PAGO DE DARK
                elif intent == "PAY_DARK":
                    recipients = []
                    # 1. Obtener lista de destinatarios
                    if intent_data.get("recipients"):
                        recipients.extend(intent_data["recipients"])
                    elif payout_recips:
                        recipients.extend(payout_recips)
                    elif intent_data.get("pj_name"):
                        recipients.append(intent_data["pj_name"])
                    elif intent_data.get("characters"):
                        recipients.extend(intent_data["characters"])

                    # Filtrar palabras stop o joya si se colaron
                    invalid_names = {'dark', 'darks', 'joya', 'joyas', 'null', 'none'}
                    recipients = [r for r in recipients if r and r.strip().lower() not in invalid_names]

                    from_holder = intent_data.get("from_holder") or payout_holder
                    darks = max(1, int(intent_data.get("darks", payout_darks or 1)))

                    if not recipients:
                        await message.reply("❓ No detecté a qué personaje(s) se le pagó la Dark. Recordá que 'dark' es la joya y no un personaje.")
                        return

                    if len(recipients) == 1:
                        pj = recipients[0]
                        ok, msg = db.record_payout(pj, darks, message.author.name, "Registrado vía chat", from_holder=from_holder)
                        if ok:
                            player = db.get_player(pj)
                            p_name = player["pj_name"] if player else pj
                            p_rem = player["current_points"] if player else 0
                            desc_lines = [
                                f"Se descontaron **{darks * 10} puntos** a **{p_name}**.",
                                f"💎 Darks pagadas: **{darks}**"
                            ]
                            if from_holder:
                                desc_lines.append(f"🏛️ **Almacenamiento:** Descontada(s) de la custodia de **{from_holder}**.")
                            desc_lines.extend([
                                f"📌 Puntos actuales restantes: **{p_rem} pts**",
                                f"👤 Registrado por: **{message.author.name}**"
                            ])
                            embed = discord.Embed(
                                title="✅ Pago de Darks Registrado",
                                description="\n".join(desc_lines),
                                color=discord.Color.green()
                            )
                        else:
                            embed = discord.Embed(title="❌ Error al pagar", description=msg, color=discord.Color.red())
                        await message.reply(embed=embed)
                        return
                    else:
                        # Múltiples destinatarios a la vez
                        success_lines = []
                        fail_lines = []
                        total_darks_paid = 0

                        for r_name in recipients:
                            ok, msg = db.record_payout(r_name, darks, message.author.name, "Pago múltiple vía chat", from_holder=from_holder)
                            if ok:
                                p_obj = db.get_player(r_name)
                                p_canonical = p_obj["pj_name"] if p_obj else r_name
                                p_rem = p_obj["current_points"] if p_obj else 0
                                success_lines.append(f"• **{p_canonical}**: -{darks * 10} pts *(quedan {p_rem} pts)* ➔ 💎 **{darks} Dark** pagada")
                                total_darks_paid += darks
                            else:
                                fail_lines.append(f"• **{r_name}**: {msg}")

                        desc = f"Se procesaron pagos para **{len(recipients)} personajes** (💎 **{darks} Dark** c/u)."
                        if from_holder:
                            desc += f"\n🏛️ **Custodio:** Entregadas desde el baúl de **{from_holder}** (Total descontado: **{total_darks_paid} Darks**)."

                        embed = discord.Embed(
                            title="✅ Pagos de Darks Registrados",
                            description=desc,
                            color=discord.Color.green() if success_lines else discord.Color.red()
                        )
                        if success_lines:
                            chunk = "\n".join(success_lines)
                            if len(chunk) > 1000:
                                chunk = chunk[:990] + "\n...(más)"
                            embed.add_field(name="🎉 Pagos Exitosos", value=chunk, inline=False)
                        if fail_lines:
                            chunk_f = "\n".join(fail_lines)
                            if len(chunk_f) > 1000:
                                chunk_f = chunk_f[:990] + "\n...(más)"
                            embed.add_field(name="⚠️ No se pudieron pagar", value=chunk_f, inline=False)

                        embed.set_footer(text=f"Registrado por {message.author.name} • ArgenMU")
                        await message.reply(embed=embed)
                        return

                # 5. ASIGNAR CUSTODIA DE DARKS (QUIÉN LAS TIENE)
                elif intent == "SET_CUSTODY":
                    custody_items_to_save = []
                    if intent_data.get("custody_items"):
                        custody_items_to_save = [(it["holder"], it["darks"]) for it in intent_data["custody_items"] if it.get("holder")]
                    else:
                        cust_detected = extract_custody_list(clean_content)
                        if cust_detected:
                            custody_items_to_save = cust_detected
                        else:
                            raw_h = intent_data.get("holder") or intent_data.get("pj_name")
                            darks = max(0, int(intent_data.get("darks", 0)))
                            if raw_h:
                                if " y " in raw_h or "," in raw_h:
                                    sub_holders = [h.strip() for h in re.split(r'\s*(?:,|y)\s*', raw_h) if h.strip()]
                                    custody_items_to_save = [(sh, darks) for sh in sub_holders]
                                else:
                                    custody_items_to_save = [(raw_h, darks)]

                    if custody_items_to_save:
                        msgs = []
                        for h, d in custody_items_to_save:
                            resolved_h = db.resolve_pj_name(h)
                            ok, msg = db.set_dark_custody(resolved_h, d, message.author.name)
                            if ok:
                                msgs.append(f"• **{resolved_h}**: **{d} Dark{'s' if d != 1 else ''}**")
                            else:
                                msgs.append(f"• ⚠️ {h}: {msg}")
                        embed = discord.Embed(
                            title="🏛️ Custodia de Darks Actualizada",
                            description="Se registraron las siguientes joyas en almacenamiento del baúl:\n\n" + "\n".join(msgs),
                            color=discord.Color.green()
                        )
                        embed.set_footer(text="Consultá /baul para ver el estado general.")
                        await message.reply(embed=embed)
                        return
                    else:
                        await message.reply("❓ No pude determinar quién tiene las darks o la cantidad a registrar.")
                        return

                # 6. SUMAR DARKS EN CUSTODIA
                elif intent == "ADD_CUSTODY":
                    holder = intent_data.get("holder") or intent_data.get("pj_name")
                    darks = max(1, int(intent_data.get("darks", 1)))
                    if holder:
                        ok, msg = db.add_dark_custody(holder, darks, message.author.name)
                        embed = discord.Embed(title="🏛️ Custodia Ajustada", description=msg, color=discord.Color.green())
                        await message.reply(embed=embed)
                        return

                # 6b. TRANSFERIR CUSTODIA ENTRE MIEMBROS (BAÚL)
                elif intent == "TRANSFER_CUSTODY":
                    from_h = intent_data.get("from_holder") or trans_from
                    to_h = intent_data.get("to_holder") or intent_data.get("holder") or intent_data.get("pj_name") or trans_to
                    darks = max(1, int(intent_data.get("darks", trans_darks or 1)))

                    if not to_h:
                        await message.reply("❓ No entendí a qué personaje deben transferirse las darks.")
                        return

                    if not from_h:
                        vault = db.get_dark_vault()
                        if len(vault) == 1:
                            from_h = vault[0]["holder"]
                        else:
                            await message.reply(f"❓ Por favor indicá quién tenía las darks para pasárselas a **{to_h}** (ej: `'Jinete le pasó {darks} darks a {to_h}'`).")
                            return

                    ok, msg = db.transfer_dark_custody(from_h, to_h, darks, message.author.name)
                    embed = discord.Embed(
                        title="🏛️ Transferencia de Custodia en Baúl",
                        description=msg,
                        color=discord.Color.green() if ok else discord.Color.red()
                    )
                    if ok:
                        embed.set_footer(text="Consultá /baul para ver el estado o /baul_historial para el registro.")
                    await message.reply(embed=embed)
                    return

                # 6c. HISTORIAL DEL BAÚL
                elif intent == "VAULT_HISTORY":
                    embed = ai_assistant.build_vault_history_embed()
                    await message.reply(embed=embed)
                    return

                # 6d. REINICIAR BAÚL A CERO
                elif intent == "RESET_VAULT":
                    embed = discord.Embed(
                        title="⚠️ Confirmación: Reiniciar Baúl de Darks",
                        description=(
                            "¿Estás seguro de que deseas vaciar y **reiniciar a cero** la custodia del baúl de Darks?\n\n"
                            "• Todos los miembros quedarán con 0 darks en almacenamiento.\n"
                            "• Esta acción quedará registrada en el historial del baúl (`/baul_historial`).\n"
                            "• Ya podés volver a cargar las darks desde cero con `/custodia`.\n"
                            "• *(Los puntos de eventos de los miembros se mantienen intactos)*.\n\n"
                            "Presioná el botón de confirmación abajo si deseas continuar."
                        ),
                        color=discord.Color.orange()
                    )
                    view = ConfirmVaultResetView(author_id=message.author.id)
                    await message.reply(embed=embed, view=view)
                    return

                # 7. ESTADO DEL BAÚL / QUIÉN TIENE LAS DARKS
                elif intent == "VAULT_STATUS":
                    embed = ai_assistant.build_vault_embed()
                    view = VaultView(author_id=message.author.id)
                    msg = await message.reply(embed=embed, view=view)
                    view.message = msg
                    return

                # 8. QUIÉN TIENE DARKS POR COBRAR (DEUDAS)
                elif intent == "LIST_DEBTS":
                    embed = ai_assistant.build_debts_embed()
                    await message.reply(embed=embed)
                    return

                # 9. NUEVO EVENTO (CON FOTO O PJS DETECTADOS)
                elif intent == "NEW_EVENT" or (has_image and intent_data.get("characters")):
                    # 1. Extraer personajes organizados por clanes desde el texto del mensaje
                    text_clans = parse_clan_characters(clean_content)

                    # 2. Extraer datos devueltos por Gemini o Fallback
                    ai_chars = intent_data.get("characters", [])
                    ai_clans = intent_data.get("clans", {})
                    ai_parties = intent_data.get("parties", {})

                    final_pjs = []
                    seen_names = set()

                    def add_candidate_pj(raw_name: str, preferred_clan: Optional[str] = None, preferred_party: Optional[str] = None):
                        if not raw_name or not is_valid_character_name(raw_name):
                            return
                        canonical = db.resolve_pj_name(raw_name)
                        if not is_valid_character_name(canonical):
                            return

                        row = db.get_player(canonical)
                        p_data = dict(row) if row else {}
                        raw_c = p_data.get("clan") or preferred_clan or "DxMENTOR"
                        final_clan = db.resolve_clan_name(raw_c)

                        if canonical.lower() in seen_names:
                            for p in final_pjs:
                                if p["name"].lower() == canonical.lower():
                                    if preferred_party:
                                        p["party"] = preferred_party
                                    if preferred_clan:
                                        p["clan"] = final_clan
                            return

                        party_label = preferred_party or "Party 1"
                        final_pjs.append({
                            "name": canonical,
                            "clan": final_clan,
                            "party": party_label
                        })
                        seen_names.add(canonical.lower())

                    # Si Gemini separó parties en ai_parties
                    if ai_parties and isinstance(ai_parties, dict):
                        for p_key, members in ai_parties.items():
                            m_num = re.search(r'\d+', str(p_key))
                            p_num = f"Party {m_num.group(0)}" if m_num else ("Party 2" if "2" in str(p_key) else "Party 1")
                            for m in members:
                                add_candidate_pj(m, preferred_party=p_num)

                    # Si Gemini separó clanes en ai_clans
                    if ai_clans and isinstance(ai_clans, dict):
                        for c_name, members in ai_clans.items():
                            for m in members:
                                add_candidate_pj(m, preferred_clan=c_name)

                    # Si vinieron en ai_chars
                    for item in ai_chars:
                        if isinstance(item, dict):
                            add_candidate_pj(item.get("name", ""), item.get("clan"), item.get("party"))
                        elif isinstance(item, str):
                            add_candidate_pj(item)

                    # Integrar y/o actualizar con los clanes parseados del texto
                    if text_clans:
                        for c_name, members in text_clans.items():
                            for m in members:
                                add_candidate_pj(m, preferred_clan=c_name)

                    # Si además hay Party 2 en texto ("Party 2", "P2", "Además...")
                    extra_p2 = extract_party2_names(clean_content)
                    for p2_name in extra_p2:
                        add_candidate_pj(p2_name, preferred_party="Party 2")

                    # Garantizar que ninguna Party supere los 7 miembros (regla de Mu Online)
                    final_pjs = rebalance_parties_max_7(final_pjs)

                    view = PartyReviewView(
                        author_id=message.author.id,
                        event_name=intent_data.get("event_name") or "Evento ArgenMU",
                        darks_count=max(1, int(intent_data.get("darks", 1))),
                        pjs=final_pjs,
                        holder=intent_data.get("holder")
                    )
                    embed = view.build_embed()
                    embed.set_footer(text="✨ Analizado con Inteligencia Artificial")
                    msg = await message.reply(embed=embed, view=view)
                    view.message = msg
                    return

                # 10. ELIMINAR PERSONAJE
                elif intent == "DELETE_PLAYER":
                    pjs_to_del = intent_data.get("characters") or ([intent_data.get("pj_name")] if intent_data.get("pj_name") else [])
                    if pjs_to_del:
                        del_msgs = []
                        for pj in pjs_to_del:
                            ok, msg = db.delete_player(pj)
                            del_msgs.append(f"• {msg}")
                        await auto_backup_database()
                        embed = discord.Embed(
                            title="🗑️ Eliminación de Personaje(s)",
                            description="\n".join(del_msgs),
                            color=discord.Color.green()
                        )
                        embed.set_footer(text="Base de datos guardada y actualizada permanentemente.")
                        await message.reply(embed=embed)
                        return

                # 11. AJUSTAR PUNTOS MANUALMENTE
                elif intent == "SET_POINTS":
                    pj = intent_data.get("pj_name")
                    raw_pts = intent_data.get("points")
                    pts = int(raw_pts) if raw_pts is not None else None
                    clan = intent_data.get("clan")
                    raw_dp = intent_data.get("darks_paid")
                    darks_paid = int(raw_dp) if raw_dp is not None else None
                    if pj:
                        ok, msg = db.update_player_points(pj, new_points=pts, clan=clan, darks_paid=darks_paid)
                        await auto_backup_database()
                        embed = discord.Embed(
                            title="✏️ Puntos y Perfil Actualizados",
                            description=msg,
                            color=discord.Color.green() if ok else discord.Color.red()
                        )
                        embed.set_footer(text="Base de datos guardada y actualizada permanentemente.")
                        await message.reply(embed=embed)
                        return

                # 12. IMPORTAR LISTA MANUAL DE PUNTOS
                elif intent == "IMPORT_POINTS":
                    items = intent_data.get("import_items", [])
                    mode = intent_data.get("mode", "ADD")
                    lower_msg = message.content.lower()
                    if any(w in lower_msg for w in ["fijar", "reemplazar", "actualizar", "cambiar", "nueva lista", "lista actual", "corregir", "nueva"]):
                        mode = "SET"
                    if items:
                        count, summary = db.bulk_import_points(items, mode=mode, updated_by=message.author.name)
                        await auto_backup_database()
                        embed = discord.Embed(
                            title=f"📥 Lista de Puntos Actualizada ({count} personajes)",
                            description=f"Modo: **{'Sumar a puntos actuales (+)' if mode == 'ADD' else 'Reemplazar / Fijar puntos (=)'}**\n\n" + "\n".join(summary[:25]),
                            color=discord.Color.green()
                        )
                        embed.set_footer(text="Base de datos guardada y actualizada permanentemente.")
                        await message.reply(embed=embed)
                        return

                # 13. GIRAR RULETA ANIMADA
                elif intent == "SPIN_ROULETTE":
                    chars = intent_data.get("characters", [])
                    if len(chars) >= 2:
                        import random
                        winner = random.choice(chars)

                        # Botón a Wheel of Names interactivo web
                        encoded_names = ",".join(chars)
                        won_url = f"https://wheelofnames.com/es/?names={encoded_names}"
                        view = discord.ui.View()
                        view.add_item(discord.ui.Button(label="🎡 Ver en WheelOfNames (Web Interactiva)", url=won_url, style=discord.ButtonStyle.link))

                        embed = discord.Embed(
                            title="🎡 ¡Sorteo de Ruleta en Vivo!",
                            description=f"👥 Participantes: **{', '.join(chars)}**\n\n🎰 **¡Iniciando giro a máxima velocidad...!**",
                            color=discord.Color.blue()
                        )
                        reply_msg = await message.reply(embed=embed)

                        gif_path = f"ruleta_chat_{message.id}.gif"
                        gif_task = asyncio.to_thread(roulette.generate_roulette_gif, chars, winner_name=winner, output_path=gif_path, size=360)

                        anim_steps = [
                            ("🎰 ⏩ Girando a toda velocidad...", 0.6),
                            ("🎰 🌀 Girando por los sectores...", 0.6),
                            ("⏳ ⚠️ Desacelerando...", 0.8),
                            ("🎯 🛑 ¡Frenando en seco...!", 1.0)
                        ]
                        for status_label, delay in anim_steps:
                            fake_choice = random.choice(chars)
                            embed.description = f"👥 Participantes: **{', '.join(chars)}**\n\n{status_label}\n👉 Aguja pasando por: **`{fake_choice}`**"
                            try:
                                await reply_msg.edit(embed=embed)
                            except Exception:
                                pass
                            await asyncio.sleep(delay)

                        await gif_task
                        gif_file = discord.File(gif_path, filename="ruleta.gif")

                        embed_final = discord.Embed(
                            title="🎉 ¡¡TENEMOS GANADOR DE LA RULETA!! 🎉",
                            description=(
                                f"👥 Participantes: **{', '.join(chars)}**\n\n"
                                f"🎯 **¡La ruleta se detuvo exactamente en:** 🏆 **`{winner.upper()}`**! 🏆\n\n"
                                f"*(Podés abrir también la ruleta interactiva web en WheelOfNames con el botón inferior)*"
                            ),
                            color=discord.Color.gold()
                        )
                        embed_final.set_image(url="attachment://ruleta.gif")
                        embed_final.set_footer(text=f"Sorteo pedido por {message.author.name} • ArgenMU")
                        await reply_msg.edit(embed=embed_final, attachments=[gif_file], view=view)
                        return
                    else:
                        await message.reply("❌ Para girar la ruleta debes mencionar al menos 2 personajes.")
                        return

                # 14. REINICIAR PUNTOS E HISTORIAL (CON CONFIRMACIÓN)
                elif intent == "RESET_HISTORY":
                    view = ConfirmResetView(author_id=message.author.id)
                    embed = discord.Embed(
                        title="⚠️ ¿Estás seguro de reiniciar los puntos y el historial?",
                        description=(
                            "**¡ATENCIÓN! Esta acción es irreversible.**\n\n"
                            "Si confirmas esta acción:\n"
                            "• Se **eliminarán todos los eventos** registrados hasta el momento.\n"
                            "• Se **borrará el historial de pagos** de darks.\n"
                            "• Se **reiniciarán todos los puntos** de los personajes para un nuevo ciclo.\n\n"
                            "*(La custodia física de darks del baúl `/baul` y la lista de administradores se mantienen intactas)*.\n\n"
                            "Presioná el botón abajo para confirmar o cancelar:"
                        ),
                        color=discord.Color.red()
                    )
                    embed.set_footer(text="Tenés 60 segundos para responder")
                    await message.reply(embed=embed, view=view)
                    return

                # 14b. VINCULAR DOS PERSONAJES (MISMA PERSONA / DOBLE CUENTA)
                elif intent == "LINK_PLAYERS":
                    p1 = intent_data.get("pj_name")
                    p2 = intent_data.get("target_pj") or intent_data.get("to_holder") or intent_data.get("holder")
                    if not p1 or not p2:
                        p1, p2 = extract_link_players(clean_content)

                    if p1 and p2:
                        if not check_is_admin(message.author, message.guild):
                            await message.reply("⛔ **Permiso denegado:** Solo los administradores autorizados pueden vincular personajes.")
                            return
                        ok, msg, info = db.link_players(p1, p2, unified_name=p2)
                        await auto_backup_database()
                        embed = discord.Embed(
                            title="👥 Vinculación de Personajes",
                            description=msg,
                            color=discord.Color.gold() if ok else discord.Color.red()
                        )
                        if ok and info.get("canonical"):
                            embed.add_field(name="🆔 Perfil Unificado", value=f"**`{info['canonical']}`**", inline=True)
                            embed.add_field(name="⭐ Puntos Totales", value=f"**{info.get('combined_points', 0)} pts**", inline=True)
                            embed.add_field(name="🏛️ Clan", value=f"{info.get('clan', 'Mentores')}", inline=True)
                            embed.set_footer(text="A partir de ahora, cualquier punto o consulta con cualquiera de los nombres se asociará a este perfil.")
                        await message.reply(embed=embed)
                        return
                    else:
                        await message.reply("❓ No pude identificar claramente los dos personajes a vincular (ej: `'vincula a Themami con Stormfly, son la misma persona'`).")
                        return

                # 15. CHAT / PREGUNTA GENERAL
                elif intent_data.get("chat_reply"):
                    await message.reply(intent_data["chat_reply"])
                    return

    await bot.process_commands(message)

# ====================================================================
# EVENTOS DEL BOT
# ====================================================================

@bot.event
async def on_ready():
    await auto_restore_database_on_boot()
    db.init_db()
    # Registrar vistas persistentes para que los botones NUNCA expiren
    bot.add_view(PointsListView())
    bot.add_view(VaultView())
    try:
        synced = await bot.tree.sync()
        print(f"🤖 Bot conectado como {bot.user} | Comandos sincronizados: {len(synced)}", flush=True)
    except Exception as e:
        print(f"Error sincronizando comandos: {e}", flush=True)

if __name__ == "__main__":
    ensure_single_instance()
    start_health_check_server()
    if not TOKEN:
        print("\n" + "="*60, flush=True)
        print("⚠️ ATENCIÓN: No se encontró la variable DISCORD_TOKEN en el archivo .env", flush=True)
        print("Por favor crea un archivo .env y coloca: DISCORD_TOKEN=tu_token_aqui", flush=True)
        print("="*60 + "\n", flush=True)
    else:
        bot.run(TOKEN)
