import os
import re
import json
import asyncio
from typing import Optional, Dict, Any, List
import discord
from google import genai
import database as db

def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key.strip())

SYSTEM_PROMPT = """
Eres el Asistente Inteligente del clan de ArgenMU (MU Online) en Discord.
Tu misión es interpretar la orden o pregunta del usuario y determinar la acción exacta.

REGLA CONCEPTUAL FUNDAMENTAL:
Existen dos conceptos completamente DIFERENTES sobre las Darks:
1. ALMACENAMIENTO / BAÚL FÍSICO: Miembros que guardan las darks del clan en su cuenta/inventario/baúl (solo actúan como depositarios/almacenadores temporales, NO son de ellos).
2. RECOMPENSAS POR PUNTOS: Miembros que sumaron 10 o más puntos jugando eventos y el clan les debe pagar/entregar una Dark.

Posibles intenciones ("intent"):
1. "LIST_POINTS": El usuario pide la lista general de puntos ganados, ranking o puntos acumulados.
2. "PLAYER_INFO": El usuario pregunta por un personaje específico (ej: "cuantos puntos tiene masterr", "info de Buferit4").
3. "PLAYER_HISTORY": El usuario pide el historial de eventos de un personaje (ej: "qué eventos jugó masterr", "historial de Buferit4").
4. "PAY_DARK": El usuario dice que ya se le pagó o entregó una dark a uno o varios jugadores que la tenían ganada por puntos (ej: "ya le pague una dark a masterr", "Jinete le pagó a masterr", "Jinete12 ya le pagó dark a DlChimu, Peperulo, Jinete12, Storfly, Carlitos Gi y Cheteelf", "le dimos 2 darks a Titan").
   - ¡REGLA FUNDAMENTAL!: "dark", "darks", "joya", "joyas" es la MONEDA/JOYA del juego con la que se paga, NUNCA es un nombre de personaje.
   - "pj_name": personaje que RECIBE el pago si es uno solo, o null si son varios.
   - "recipients": lista con todos los nombres de los personajes que reciben el pago (ej: ["DlChimu", "Peperulo", "Jinete12", "Storfly", "Carlitos Gi", "Cheteelf"]).
   - "from_holder": personaje que entrega la dark desde su custodia/baúl si se menciona (ej: "Jinete12").
   - "darks": cantidad de darks entregadas por jugador (por defecto 1).
5. "SET_CUSTODY": El usuario indica quién ALMACENA o guarda las darks físicas del gremio (ej: "masterr tiene 3 darks en su baúl", "cargá que Buferit4 almacena 2 darks").
   - Requiere: "holder" (personaje que las guarda físicamente) y "darks" (cantidad).
6. "ADD_CUSTODY": El usuario dice que alguien levantó o guardó más darks en su almacén (ej: "sumale 1 dark a masterr que la agarró").
7. "VAULT_STATUS": El usuario pregunta por el ALMACENAMIENTO físico, quién las guarda o cuántas hay en baúles (ej: "quién tiene las darks?", "cuántas darks hay guardadas?", "cómo está el baúl?").
8. "LIST_DEBTS": El usuario pregunta a quién hay que PAGARLE por sus puntos ganados (ej: "a quién le debemos darks?", "quién tiene para cobrar?").
9. "NEW_EVENT": Carga de un evento jugado (ej: "Frozen de las 13 40. Cayeron 2 darks. Tiene Peperulo las dos darks", "BC 10:30 cayeron 2 darks", o mensajes con capturas de party).
    - ¡PRIORIDAD ABSOLUTA!: Si el mensaje adjunta una foto de party o relata un evento jugado ("cayeron X darks", "Frozen", "BC", etc.), el intent SIEMPRE ES "NEW_EVENT", INCLUSO si menciona quién guarda las darks físicas (ej: "Tiene Peperulo las dos darks"). En este caso asigna "darks": 2, "event_name": "Frozen 13:40", "holder": "Peperulo", y los "characters" de la captura. NO lo clasifiques como SET_CUSTODY.
    - ¡REGLA FUNDAMENTAL DE INTEGRACIÓN (PARTY 1 EN FOTO + PARTY 2 EN TEXTO)!:
      El usuario con frecuencia manda una captura con la Party 1 y en el mensaje de texto escribe los personajes de la Party 2 (ej: "Party 1 en captura \n Party 2 \n TheMami / Lorditachi", o "Además...", o "sumar a X e Y").
      DEBES OBLIGATORIAMENTE UNIR AMBAS LISTAS: lee todos los personajes de la captura de party MÁS todos los personajes nombrados en el texto (Party 2, sueltos o agregados), y colócalos a TODOS juntos en el array "characters". NUNCA ignores los nombres escritos por texto.
    - ORGANIZACIÓN POR PARTIES ("parties"):
      DEBES organizar los personajes devueltos en el diccionario "parties":
      - Si hay múltiples capturas adjuntas (1, 2, 3 o más):
        - "Party 1": los participantes de la primera captura (o primera party).
        - "Party 2": los participantes de la segunda captura (o segunda party en texto).
        - "Party 3": los participantes de la tercera captura (o tercera party en texto).
        - etc. (pueden ser hasta 4 o más partys).
      - Cada captura contiene hasta 7 jugadores en el recuadro de Party.
      - DEBES extraer los personajes de TODAS las capturas y colocarlos a TODOS juntos en el array principal "characters".
      - Si se especifica en texto una party adicional (ej: "Party 3: ..."), súmala a su party correspondiente.
    - DIFERENCIACIÓN DE CLANES:
      Los clanes oficiales son ÚNICAMENTE "DxMENTOR" y "PolloKill". NUNCA devuelvas "DxMentor", "dxmentor" o "Mentores", siempre normaliza a "DxMENTOR" o "PolloKill".
      Podés retornar en "clans" un diccionario `{"DxMENTOR": ["Pj1", "Pj2"], "PolloKill": ["Pj3", "Pj4"]}`.
    - ¡FILTRADO ESTRICTO DE CAPTURAS DE JUEGO (MU ONLINE / ARGENMU)!:
      1. La ventana de party aparece en el recuadro SUPERIOR DERECHO de la pantalla con las barras de vida. Esos son los participantes principales.
      2. Si hay miembros en el escenario 3D o en el chat que también participaron del evento, inclúyelos ÚNICAMENTE si son personajes reales (ej: Stormfly, BoniElf, etc.).
      3. ¡JAMÁS agregues como personaje!:
         - Palabras del mensaje de texto que describen el evento o fecha (ej: "bc8", "sv2", "6:30", "unica", "party", "FECHA", "19/10/26", "dark", "darks", "tiene").
         - Drops del chat: "Guardian Angel", "Satan", "Seal of Sustenance", "Chaos", "Zen", "Jewel", "Ancient", "Coin".
         - Compradores/vendedores del chat global o post (ej: "VENDO BC7", "EVOSJc00").
         - Textos de interfaz como "FPS", "PING", "Level", "Server".
10. "DELETE_PLAYER": El usuario pide eliminar o borrar un personaje (ej: "borrá a Player_1", "eliminá a TitanBK", "sacá a ese pj").
    - Requiere: "pj_name" (personaje a borrar).
11. "SET_POINTS": El usuario pide ajustar, cambiar o fijar los puntos, clan o darks cobradas de uno o varios personajes manualmente (ej: "cambiale los puntos a masterr a 8", "poné a Camarlengo con 32 puntos (PolloKill)", "ajustá a Bonielf en 26 puntos y 4 darks cobradas", "cambiá el clan de Duraznita a PolloKill").
    - Requiere: "pj_name" (personaje).
    - Opcional: "points" (puntos a fijar), "clan" ("DxMENTOR" o "PolloKill") y "darks_paid" (número de darks cobradas).
12. "IMPORT_POINTS": El usuario pasa una lista manual de personajes con sus puntos (en texto o en imagen/foto de excel, whatsapp o bloc de notas) para cargarlos o sumarlos.
    - Requiere: "import_items": [{"pj_name": "...", "points": 10, "clan": "DxMENTOR", "darks_paid": 0}, ...]
    - "mode": "SET" (si dice reemplazar, fijar, actualizar, corregir o lista actual) o "ADD" (si dice sumar o agregar).
13. "SPIN_ROULETTE": El usuario pide girar una ruleta animada entre una lista de personajes (ej: "girá una ruleta entre masterr, Buferit4 y Hayiku", "ruleta con...").
    - Requiere: "characters": ["nombre1", "nombre2", ...]
14. "RESET_HISTORY": El usuario pide reiniciar todos los puntos, limpiar o borrar el historial de eventos del gremio (ej: "reiniciar todos los puntos", "limpiar historial", "borrar todos los eventos y empezar de nuevo").
15. "TRANSFER_CUSTODY": El usuario dice que un miembro le pasó/transfirió darks de almacenamiento/baúl a otro miembro (ej: "Jinete le pasó 5 darks a Camarlengo", "pasale 5 darks de Jinete a Camarlengo", "Camarlengo recibió 5 darks de Jinete", "Jinete le transfirió 5 darks a Camarlengo", "le pasó las darks a Carmelnango").
    - "from_holder": miembro que entrega o tenía las darks físicas guardadas.
    - "to_holder": miembro que recibe y guardará las darks físicas.
    - "darks": cantidad de darks a transferir.
    - ¡REGLA CRÍTICA!: Esto es una TRANSFERENCIA DE INVENTARIO FÍSICO / CUSTODIA. NO resta puntos por cobrar a nadie ni es un cobro de evento.
16. "VAULT_HISTORY": El usuario pide ver el historial de movimientos, transferencias o cargas del baúl/almacenamiento de darks (ej: "historial del baúl", "movimientos del baúl", "historial de darks almacenadas", "ver transferencias de darks").
17. "RESET_VAULT": El usuario pide vaciar, limpiar o reiniciar a cero el baúl/almacenamiento de darks (ej: "reiniciar baúl", "vaciar baúl", "poner a cero el baúl de darks", "borrar darks almacenadas para empezar de cero").
18. "LINK_PLAYERS": El usuario indica que dos personajes son de la misma persona o pide vincularlos/unirlos (ej: "vincula a Themami con Stormfly, son la misma persona", "uní las cuentas de Spark y Chispa", "Themami y Stormfly son el mismo jugador", "asociá a Themami con Stormfly").
    - "pj_name": primer personaje (ej: "Themami").
    - "target_pj": segundo personaje (ej: "Stormfly").
19. "CHAT": Saludo, ayuda o pregunta general.

¡REGLA PARA CONSULTAS MÚLTIPLES!:
Si el usuario hace varias preguntas o consultas en el mismo mensaje (ej: "Decime la lista actual de puntos. Quiénes faltan pagar y quién tiene las darks", o "ranking y baúl"):
- Devuelve en "intents" un array con TODAS las intenciones que correspondan: ["LIST_POINTS", "LIST_DEBTS", "VAULT_STATUS"].
- En "intent" coloca la primera de ellas.

Responde ÚNICAMENTE un JSON válido (sin markdown ni texto extra):
{
  "intent": "LIST_POINTS",
  "intents": ["LIST_POINTS"],
  "pj_name": "nombre o null",
  "target_pj": "nombre o null",
  "recipients": [],
  "from_holder": "nombre o null",
  "to_holder": "nombre o null",
  "points": 0,
  "darks": 1,
  "holder": "nombre o null",
  "event_name": "nombre del evento o null",
  "characters": [],
  "parties": {},
  "clans": {},
  "import_items": [],
  "mode": "ADD",
  "chat_reply": "respuesta amigable si es CHAT o null"
}
"""

async def process_user_intent(
    user_text: str,
    author_name: str,
    image_bytes: Optional[Any] = None,
    images_bytes: Optional[List[bytes]] = None
) -> Optional[Dict[str, Any]]:
    client = get_gemini_client()
    if not client:
        return None

    contents = []

    # Unificar lista de imágenes
    all_images = []
    if images_bytes:
        all_images.extend(images_bytes)
    elif image_bytes:
        if isinstance(image_bytes, list):
            all_images.extend(image_bytes)
        else:
            all_images.append(image_bytes)

    if all_images:
        from PIL import Image
        import io
        from ocr_scanner import crop_party_corner
        lower_txt = user_text.lower()
        is_list = any(w in lower_txt for w in ["lista", "manual", "excel", "bloc", "cargar", "importar", "estos puntos"])

        for idx, img_b in enumerate(all_images):
            if img_b:
                im = Image.open(io.BytesIO(img_b)).convert("RGB")
                if is_list:
                    contents.append(im)
                else:
                    contents.append(f"Captura #{idx+1} (Party {idx+1}):")
                    contents.append(crop_party_corner(im))

    prompt = f'{SYSTEM_PROMPT}\n\nMensaje del usuario: "{user_text}"'
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
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model_name,
                contents=contents
            )
            if response and response.text:
                break
        except Exception as e:
            continue

    if not response or not response.text:
        return None

    try:
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)

        return json.loads(raw)
    except Exception as e:
        print(f"Error procesando JSON de Gemini Assistant: {e}")
        return None

def build_points_list_embed(clan: Optional[str] = None, limit: int = 100) -> discord.Embed:
    players = db.get_all_players_ranking(limit=limit, clan=clan)
    clan_title = f" [{clan}]" if clan else ""
    clan_suffix = f" en el clan **{clan}**" if clan else ""
    if not players:
        return discord.Embed(
            title=f"📊 Ranking & Puntos Acumulados — ArgenMU{clan_title}",
            description=f"Aún no hay puntos registrados{clan_suffix}.",
            color=discord.Color.blue()
        )

    embed = discord.Embed(
        title=f"🏆 Ranking & Lista de Puntos — ArgenMU{clan_title}",
        description=(
            f"Puntos ganados por cada miembro jugando eventos{clan_suffix} (Total: **{len(players)}** jugadores).\n"
            "*(Cada 10 puntos ganados = 1 Dark que el clan debe entregarle)*\n"
        ),
        color=discord.Color.gold()
    )

    lines = []
    for i, p in enumerate(players):
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"`{i+1}.`"
        darks_owed = p["current_points"] // 10
        debt_str = f" ➔ **🎁 {darks_owed} Dark{'s' if darks_owed > 1 else ''} por cobrar**" if darks_owed > 0 else ""
        clan_str = f" `[{p['clan']}]`" if "clan" in p.keys() and p["clan"] else ""
        paid_count = p["darks_paid"] if "darks_paid" in p.keys() else 0
        paid_str = f" `({paid_count}D cobrada)`" if paid_count == 1 else (f" `({paid_count}D cobradas)`" if paid_count > 1 else "")
        lines.append(f"{medal} **{p['pj_name']}**{clan_str}: **{p['current_points']} pts**{paid_str}{debt_str}")

    # Dividir las líneas en bloques seguros que no superen 950 caracteres por campo (Discord limita a 1024)
    chunks = []
    curr_chunk = []
    curr_len = 0
    for line in lines:
        line_len = len(line) + 1
        if curr_len + line_len > 950 and curr_chunk:
            chunks.append("\n".join(curr_chunk))
            curr_chunk = [line]
            curr_len = line_len
        else:
            curr_chunk.append(line)
            curr_len += line_len
    if curr_chunk:
        chunks.append("\n".join(curr_chunk))

    for idx, chunk in enumerate(chunks[:25]): # Discord soporta hasta 25 campos por embed
        field_name = "Clasificación General" if idx == 0 else f"Posiciones (cont. {idx+1})"
        embed.add_field(name=field_name, value=chunk, inline=False)

    embed.set_footer(text="Puntos actualizados • Usá los botones de abajo para editar o ajustar.")
    return embed

def build_player_info_embed(pj_name: str) -> discord.Embed:
    p = db.get_player(pj_name)
    if not p:
        return discord.Embed(
            title="🔍 Personaje no encontrado",
            description=f"No tengo registros del personaje **{pj_name}**.",
            color=discord.Color.orange()
        )

    darks_owed = p["current_points"] // 10
    rem = p["current_points"] % 10
    clan_name = p["clan"] if "clan" in p.keys() and p["clan"] else "Mentores"

    embed = discord.Embed(
        title=f"👤 Ficha de Personaje: {p['pj_name']}",
        color=discord.Color.teal()
    )
    embed.add_field(name="🛡️ Clan", value=f"**{clan_name}**", inline=True)
    embed.add_field(name="💰 Puntos Ganados Actuales", value=f"**{p['current_points']}** pts", inline=True)
    embed.add_field(name="🎁 Recompensa por Cobrar", value=f"**{darks_owed} Dark{'s' if darks_owed != 1 else ''}** ({rem} pts hacia la próx.)", inline=True)
    embed.add_field(name="📦 Recompensas ya Cobradas", value=f"**{p['darks_paid']}** Darks", inline=True)

    # Verificar si además almacena darks del clan
    vault = db.get_dark_vault()
    custody_entry = next((v for v in vault if v["holder"].lower() == pj_name.lower()), None)
    if custody_entry and custody_entry["darks_count"] > 0:
        embed.add_field(
            name="🏛️ Almacenamiento / Baúl Común",
            value=f"🛡️ Guarda **{custody_entry['darks_count']} Dark{'s' if custody_entry['darks_count'] != 1 else ''}** del clan *(en custodia temporal)*",
            inline=False
        )

    embed.add_field(name="⚔️ Eventos Jugados", value=f"**{p['events_attended']}**", inline=True)
    embed.add_field(name="📈 Puntos Históricos", value=f"**{p['total_points_history']}** pts", inline=True)
    embed.add_field(name="🎲 Racha sin Sorteo", value=f"**{p['raffle_streak_lost']}** eventos", inline=True)
    return embed

def build_player_history_embed(pj_name: str) -> discord.Embed:
    p = db.get_player(pj_name)
    if not p:
        return discord.Embed(
            title="🔍 Personaje no encontrado",
            description=f"No tengo registros del personaje **{pj_name}**.",
            color=discord.Color.orange()
        )

    history = db.get_player_event_history(pj_name, limit=10)
    payouts = db.get_player_payout_history(pj_name, limit=5)

    embed = discord.Embed(
        title=f"📜 Historial de Eventos de {p['pj_name']}",
        description=f"Puntos actuales acumulados: **{p['current_points']} pts** | Total eventos jugados: **{p['events_attended']}**",
        color=discord.Color.purple()
    )

    if history:
        ev_lines = []
        for h in history:
            won_str = " (🏆 Ganó sorteo +1)" if h["won_raffle"] else ""
            ev_lines.append(f"• **{h['event_name']}** ({h['date']}): +{h['total_earned']} pts{won_str}")
        embed.add_field(name="Últimos Eventos Jugados", value="\n".join(ev_lines), inline=False)
    else:
        embed.add_field(name="Últimos Eventos Jugados", value="Sin eventos registrados.", inline=False)

    if payouts:
        pay_lines = []
        for py in payouts:
            date_str = py["created_at"][:16].replace("T", " ") if py["created_at"] else ""
            pay_lines.append(f"• **{py['darks_paid']} Dark{'s' if py['darks_paid'] > 1 else ''}** ({date_str}) entregada por {py['registered_by']}")
        embed.add_field(name="Recompensas de Darks Entregadas", value="\n".join(pay_lines), inline=False)

    return embed

def build_debts_embed(clan: Optional[str] = None) -> discord.Embed:
    debts = db.get_pending_debts(clan=clan)
    clan_suffix = f" en el clan **{clan}**" if clan else ""
    if not debts:
        return discord.Embed(
            title="💎 Darks Pendientes de Pago",
            description=f"🎉 ¡Todo al día! No hay miembros con 10 o más puntos pendientes de cobrar{clan_suffix}.",
            color=discord.Color.green()
        )

    total_darks_owed = sum(d["darks_owed"] for d in debts)
    embed = discord.Embed(
        title="💎 Darks Pendientes de Pago (Recompensas Ganadas)",
        description=(
            f"Miembros que alcanzaron **10 o más puntos** en eventos y deben recibir su Dark{clan_suffix}.\n"
            f"🎁 **Total Darks pendientes de entregar:** **{total_darks_owed} Dark{'s' if total_darks_owed != 1 else ''}**\n"
        ),
        color=discord.Color.gold()
    )

    lines = []
    for d in debts:
        clan_str = f" `[{d['clan']}]`" if d.get("clan") else ""
        lines.append(f"• **{d['pj_name']}**{clan_str}: **{d['darks_owed']} Dark{'s' if d['darks_owed'] > 1 else ''}** por cobrar *(acumula {d['current_points']} pts, restarán {d['rem_points']} pts)*")

    chunks = []
    curr_chunk = []
    curr_len = 0
    for line in lines:
        line_len = len(line) + 1
        if curr_len + line_len > 950 and curr_chunk:
            chunks.append("\n".join(curr_chunk))
            curr_chunk = [line]
            curr_len = line_len
        else:
            curr_chunk.append(line)
            curr_len += line_len
    if curr_chunk:
        chunks.append("\n".join(curr_chunk))

    for idx, chunk in enumerate(chunks[:25]):
        field_name = "Jugadores con Darks por Cobrar" if idx == 0 else f"Jugadores (cont. {idx+1})"
        embed.add_field(name=field_name, value=chunk, inline=False)

    embed.set_footer(text="Para registrar una entrega: '@Jinete Bot [PJ_custodio] ya le pagó dark a [PJ_ganador]'")
    return embed

def build_vault_embed() -> discord.Embed:
    vault = db.get_dark_vault()
    debts = db.get_pending_debts()

    total_storage = sum(v["darks_count"] for v in vault)
    total_owed = sum(d["darks_owed"] for d in debts)
    balance_neto = total_storage - total_owed

    embed = discord.Embed(
        title="🏛️ Estado del Baúl y Almacenamiento del Clan",
        color=discord.Color.dark_teal()
    )

    embed.description = (
        "> ℹ️ **NOTA DE CUSTODIA:** Estas son las joyas físicas que los miembros tienen **guardadas en su inventario/baúl para el clan**. "
        "No son suyas, solo las almacenan temporalmente.\n\n"
        f"📦 **Total Darks en Almacenamiento:** **{total_storage} Darks**\n"
        f"🎁 **Total Darks ganadas por miembros a pagar:** **{total_owed} Darks**\n"
        f"📊 **Stock libre tras pagar:** **{balance_neto} Darks**\n"
    )

    if vault:
        lines = []
        for v in vault:
            note_str = f" *(Nota: {v['note']})*" if v.get("note") else ""
            lines.append(f"• **{v['holder']}**: almacena **{v['darks_count']} Dark{'s' if v['darks_count'] != 1 else ''}**{note_str}")
        embed.add_field(name="📦 ¿Quién las tiene guardadas físicamente?", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="📦 ¿Quién las tiene guardadas físicamente?", value="*No hay registros de darks en almacenamiento actualmente.*", inline=False)

    embed.set_footer(text="Para cargar almacenamiento: '@Jinete Bot cargá que [PJ] tiene [X] darks'")
    return embed

def build_vault_history_embed(limit: int = 15) -> discord.Embed:
    history = db.get_dark_vault_history(limit=limit)
    embed = discord.Embed(
        title="📜 Historial de Movimientos del Baúl",
        description="Registro de transferencias, cargas y entregas de Darks en custodia física del clan.",
        color=discord.Color.dark_teal()
    )

    if not history:
        embed.description += "\n\n*No hay movimientos registrados en el baúl aún.*"
        return embed

    action_names = {
        "TRANSFER": "🔄 Transferencia de Custodia",
        "SET": "📝 Fijar Custodia",
        "ADD": "➕ Carga de Darks",
        "PAYOUT": "🎁 Retiro para Entrega de Premio",
        "RESET": "🗑️ Reinicio de Baúl"
    }

    lines = []
    for h in history:
        date_str = h["created_at"][:16].replace("T", " ") if h["created_at"] else ""
        act_title = action_names.get(h["action_type"], h["action_type"])
        qty = h["darks_count"]
        qty_str = f"**{qty} Dark{'s' if qty != 1 else ''}**"

        if h["action_type"] == "TRANSFER":
            detail = f"{qty_str}: de **{h['from_holder']}** ➔ **{h['to_holder']}**"
        elif h["action_type"] == "PAYOUT":
            detail = f"{qty_str} retiradas del baúl de **{h['from_holder']}** (entregadas a **{h['to_holder']}**)"
        elif h["action_type"] in ("SET", "ADD"):
            holder = h["to_holder"] or h["from_holder"]
            verb = "Fijado en" if h["action_type"] == "SET" else "Sumado a"
            detail = f"{verb} **{holder}**: {qty_str}"
        elif h["action_type"] == "RESET":
            detail = "Se vaciaron todos los registros del baúl a 0"
        else:
            detail = f"{qty_str}"

        note_txt = f" *(Nota: {h['note']})*" if h.get("note") else ""
        by_txt = f" *(por {h['registered_by']})*" if h.get("registered_by") else ""
        lines.append(f"• `[{date_str}]` **{act_title}**\n  ↳ {detail}{note_txt}{by_txt}")

    chunks = []
    curr_chunk = []
    curr_len = 0
    for l in lines:
        line_len = len(l) + 1
        if curr_len + line_len > 950 and curr_chunk:
            chunks.append("\n".join(curr_chunk))
            curr_chunk = [l]
            curr_len = line_len
        else:
            curr_chunk.append(l)
            curr_len += line_len
    if curr_chunk:
        chunks.append("\n".join(curr_chunk))

    for idx, chunk in enumerate(chunks[:25]):
        fname = "Últimos Movimientos" if idx == 0 else f"Movimientos (cont. {idx+1})"
        embed.add_field(name=fname, value=chunk, inline=False)

    embed.set_footer(text="Usá /baul_transferir para pasar darks de un miembro a otro.")
    return embed

