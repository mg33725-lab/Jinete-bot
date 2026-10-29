import sqlite3
import difflib
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any

DB_PATH = "argenmu.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=30000;")

    # Tabla de jugadores
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS players (
        pj_name TEXT PRIMARY KEY COLLATE NOCASE,
        discord_id INTEGER DEFAULT NULL,
        current_points INTEGER DEFAULT 0,
        total_points_history INTEGER DEFAULT 0,
        darks_paid INTEGER DEFAULT 0,
        events_attended INTEGER DEFAULT 0,
        raffle_streak_lost INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_event_at TIMESTAMP DEFAULT NULL
    )
    """)

    # Tabla de eventos
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_name TEXT NOT NULL,
        darks_count INTEGER NOT NULL,
        total_points INTEGER NOT NULL,
        fixed_per_pj INTEGER NOT NULL,
        leftover_points INTEGER NOT NULL,
        registered_by TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Tabla de participantes de eventos
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS event_participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL,
        pj_name TEXT NOT NULL COLLATE NOCASE,
        fixed_points INTEGER NOT NULL,
        raffle_points INTEGER NOT NULL,
        won_raffle INTEGER NOT NULL, -- 0 o 1
        roll_score REAL DEFAULT 0.0,
        FOREIGN KEY (event_id) REFERENCES events (id) ON DELETE CASCADE,
        FOREIGN KEY (pj_name) REFERENCES players (pj_name)
    )
    """)

    # Tabla de pagos de darks
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pj_name TEXT NOT NULL COLLATE NOCASE,
        darks_paid INTEGER NOT NULL,
        points_deducted INTEGER NOT NULL,
        registered_by TEXT NOT NULL,
        note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (pj_name) REFERENCES players (pj_name)
    )
    """)

    # Tabla de custodia/baúl de darks (quién tiene las darks físicas del gremio)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dark_vault (
        holder TEXT PRIMARY KEY COLLATE NOCASE,
        darks_count INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_by TEXT DEFAULT '',
        note TEXT DEFAULT ''
    )
    """)

    # Tabla de historial de transacciones y movimientos del baúl (almacenamiento)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS vault_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action_type TEXT NOT NULL, -- 'SET', 'ADD', 'TRANSFER', 'PAYOUT', 'RESET'
        from_holder TEXT DEFAULT NULL COLLATE NOCASE,
        to_holder TEXT DEFAULT NULL COLLATE NOCASE,
        darks_count INTEGER NOT NULL,
        registered_by TEXT NOT NULL,
        note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Tabla de administradores autorizados para cargar eventos y modificar puntos
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admin_users (
        discord_id INTEGER PRIMARY KEY,
        username TEXT NOT NULL,
        added_by TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Tabla de alias de personajes (para personas con doble personaje como Spark/Chispa)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS player_aliases (
        alias TEXT PRIMARY KEY COLLATE NOCASE,
        canonical_pj_name TEXT NOT NULL COLLATE NOCASE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (canonical_pj_name) REFERENCES players (pj_name) ON DELETE CASCADE
    )
    """)

    # Migración: columna clan en players
    cursor.execute("PRAGMA table_info(players)")
    p_cols = [c["name"] for c in cursor.fetchall()]
    if "clan" not in p_cols:
        cursor.execute("ALTER TABLE players ADD COLUMN clan TEXT DEFAULT 'Mentores'")

    # Migración: columna clan en event_participants
    cursor.execute("PRAGMA table_info(event_participants)")
    ep_cols = [c["name"] for c in cursor.fetchall()]
    if "clan" not in ep_cols:
        cursor.execute("ALTER TABLE event_participants ADD COLUMN clan TEXT DEFAULT 'Mentores'")

    conn.commit()
    conn.close()

def resolve_clan_name(clan: Optional[str]) -> str:
    """Normaliza nombres de clanes para que DxMENTOR y PolloKill nunca se dupliquen o difieran por mayúsculas."""
    if not clan:
        return "DxMENTOR"
    c = str(clan).strip()
    if not c:
        return "DxMENTOR"
    low = c.lower()
    if "pollo" in low:
        return "PolloKill"
    if "mentor" in low:
        return "DxMENTOR"
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT clan FROM players WHERE clan = ? COLLATE NOCASE", (c,))
        row = cursor.fetchone()
        conn.close()
        if row and row["clan"]:
            return row["clan"]
    except Exception:
        pass
    return c

def add_alias(alias: str, canonical_pj_name: str) -> Tuple[bool, str]:
    alias = alias.strip()
    canonical_pj_name = canonical_pj_name.strip()
    if not alias or not canonical_pj_name:
        return False, "El alias y el personaje principal no pueden estar vacíos."
    if alias.lower() == canonical_pj_name.lower():
        return False, "El alias no puede ser idéntico al personaje principal."

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT pj_name FROM players WHERE pj_name = ? COLLATE NOCASE", (canonical_pj_name,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, f"El personaje principal '{canonical_pj_name}' no existe en la base de datos."
    canonical_real = row["pj_name"]

    cursor.execute("SELECT current_points FROM players WHERE pj_name = ? COLLATE NOCASE", (alias,))
    alias_p = cursor.fetchone()
    conn.close()

    # Si el alias ya existía como personaje registrado con puntos, usar link_players para no perder puntos
    if alias_p:
        ok, msg, _ = link_players(alias, canonical_real, unified_name=canonical_real)
        return ok, msg

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO player_aliases (alias, canonical_pj_name)
        VALUES (?, ?)
        ON CONFLICT(alias) DO UPDATE SET canonical_pj_name = excluded.canonical_pj_name
    """, (alias, canonical_real))
    conn.commit()
    conn.close()
    return True, f"✅ Se vinculó el alias **'{alias}'** al personaje principal **'{canonical_real}'**."

def rename_player(old_name: str, new_name: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Cambia el nombre principal (canónico) de un personaje en toda la base de datos
    y registra el nombre anterior como alias para no perder historial ni futuros eventos.
    Incluso funciona si el nuevo nombre ya figuraba como alias del viejo.
    """
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name or not new_name:
        return False, "Debes especificar el nombre actual y el nuevo nombre.", {}

    can_old = resolve_pj_name(old_name)
    can_new = resolve_pj_name(new_name)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (can_old,))
    p_old = cursor.fetchone()
    if not p_old:
        cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (old_name,))
        p_old = cursor.fetchone()
        if p_old:
            can_old = p_old["pj_name"]

    if not p_old:
        conn.close()
        return False, f"❌ No se encontró al personaje **'{old_name}'** en la base de datos.", {}

    # Si el nuevo nombre pertenece a OTRO jugador distinto que ya existe en players, unificarlos bajo new_name
    cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (can_new,))
    p_new = cursor.fetchone()
    if p_new and can_old.lower() != can_new.lower():
        conn.close()
        return link_players(can_old, can_new, unified_name=new_name)

    # Renombrar directamente el registro principal de can_old a new_name
    pts = p_old["current_points"]
    tot = p_old["total_points_history"]
    darks = p_old["darks_paid"]
    att = p_old["events_attended"]
    clan = resolve_clan_name(p_old["clan"] if p_old["clan"] else "DxMENTOR")
    disc_id = p_old["discord_id"]

    if can_old.lower() == new_name.lower():
        # Cambio solo de mayúsculas/minúsculas
        cursor.execute("UPDATE players SET pj_name = ? WHERE pj_name = ? COLLATE NOCASE", (new_name, can_old))
    else:
        cursor.execute("""
            INSERT INTO players (pj_name, discord_id, current_points, total_points_history, darks_paid, events_attended, clan)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pj_name) DO UPDATE SET
                discord_id = COALESCE(excluded.discord_id, players.discord_id),
                current_points = excluded.current_points,
                total_points_history = excluded.total_points_history,
                darks_paid = excluded.darks_paid,
                events_attended = excluded.events_attended,
                clan = excluded.clan
        """, (new_name, disc_id, pts, tot, darks, att, clan))
        cursor.execute("DELETE FROM players WHERE pj_name = ? COLLATE NOCASE", (can_old,))

    cursor.execute("UPDATE event_participants SET pj_name = ? WHERE pj_name = ? COLLATE NOCASE", (new_name, can_old))
    cursor.execute("UPDATE payouts SET pj_name = ? WHERE pj_name = ? COLLATE NOCASE", (new_name, can_old))

    cursor.execute("SELECT holder, darks_count, note FROM dark_vault WHERE holder = ? COLLATE NOCASE", (can_old,))
    v_old = cursor.fetchone()
    if v_old and can_old.lower() != new_name.lower():
        cursor.execute("DELETE FROM dark_vault WHERE holder = ? COLLATE NOCASE", (can_old,))
        cursor.execute("""
            INSERT INTO dark_vault (holder, darks_count, updated_by, note)
            VALUES (?, ?, 'Sistema', ?)
            ON CONFLICT(holder) DO UPDATE SET darks_count = dark_vault.darks_count + excluded.darks_count
        """, (new_name, v_old["darks_count"], v_old["note"] or "Renombrado"))

    cursor.execute("UPDATE vault_transactions SET from_holder = ? WHERE from_holder = ? COLLATE NOCASE", (new_name, can_old))
    cursor.execute("UPDATE vault_transactions SET to_holder = ? WHERE to_holder = ? COLLATE NOCASE", (new_name, can_old))

    cursor.execute("UPDATE player_aliases SET canonical_pj_name = ? WHERE canonical_pj_name = ? COLLATE NOCASE", (new_name, can_old))
    cursor.execute("DELETE FROM player_aliases WHERE alias = ? COLLATE NOCASE", (new_name,))

    for a in {can_old, old_name}:
        if a and a.lower() != new_name.lower():
            cursor.execute("""
                INSERT INTO player_aliases (alias, canonical_pj_name)
                VALUES (?, ?)
                ON CONFLICT(alias) DO UPDATE SET canonical_pj_name = excluded.canonical_pj_name
            """, (a, new_name))

    conn.commit()
    conn.close()

    info = {
        "canonical": new_name,
        "combined_points": pts,
        "pj1": can_old,
        "pj2": new_name,
        "clan": clan
    }
    return True, f"✅ Se cambió el nombre de **'{can_old}'** a **'{new_name}'** (conservando sus **{pts} pts** y **{darks} Darks cobradas**).", info

def link_players(pj1: str, pj2: str, unified_name: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Vincula dos personajes que pertenecen a la misma persona (ej: Themami con Stormfly).
    Unifica puntos, historial de eventos, pagos, custodia de baúl y registra alias bidireccionales.
    """
    pj1 = pj1.strip()
    pj2 = pj2.strip()
    if not pj1 or not pj2:
        return False, "Debes especificar ambos personajes para vincularlos.", {}
    if pj1.lower() == pj2.lower():
        if pj1 != pj2:
            return rename_player(pj1, pj2)
        return False, "No puedes vincular un personaje consigo mismo.", {}

    conn = get_connection()
    cursor = conn.cursor()

    can1 = resolve_pj_name(pj1)
    can2 = resolve_pj_name(pj2)

    if can1.lower() == can2.lower():
        conn.close()
        # Si el usuario pidió renombrar a un alias que ya apuntaba a can1 (ej: cambiar 'pepe' a 'Peperulo')
        desired = unified_name.strip() if unified_name else None
        if desired and desired != can1:
            return rename_player(can1, desired)
        if pj2 != can1 and pj1.lower() == can1.lower():
            return rename_player(can1, pj2)
        return True, f"ℹ️ Los personajes **'{pj1}'** y **'{pj2}'** ya se encuentran vinculados bajo **'{can1}'**.", {"canonical": can1}

    cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (can1,))
    p1 = cursor.fetchone()
    cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (can2,))
    p2 = cursor.fetchone()

    # Si ninguno existe en players
    if not p1 and not p2:
        target = unified_name.strip() if unified_name else can1
        conn.close()
        add_player(target, clan="DxMENTOR")
        add_alias(can1, target)
        add_alias(can2, target)
        return True, f"✅ Se vincularon **{can1}** y **{can2}** bajo **{target}**.", {"canonical": target, "points": 0}

    # Determinar nombre unificado priorizando al personaje principal con mayor trayectoria si no se forzó uno
    if unified_name and unified_name.strip():
        target = unified_name.strip()
    elif "/" in can2:
        target = can2
    elif "/" in can1:
        target = can1
    elif p1 and p2:
        score1 = ((p1["darks_paid"] or 0) * 10 + (p1["current_points"] or 0), p1["events_attended"] or 0, len(can1))
        score2 = ((p2["darks_paid"] or 0) * 10 + (p2["current_points"] or 0), p2["events_attended"] or 0, len(can2))
        target = can1 if score1 >= score2 else can2
    elif p1:
        target = can1
    else:
        target = can2

    pts1 = p1["current_points"] if p1 else 0
    pts2 = p2["current_points"] if p2 else 0
    tot1 = p1["total_points_history"] if p1 else 0
    tot2 = p2["total_points_history"] if p2 else 0
    darks1 = p1["darks_paid"] if p1 else 0
    darks2 = p2["darks_paid"] if p2 else 0
    att1 = p1["events_attended"] if p1 else 0
    att2 = p2["events_attended"] if p2 else 0
    clan = resolve_clan_name(p2["clan"] if p2 and p2["clan"] else (p1["clan"] if p1 and p1["clan"] else "DxMENTOR"))
    disc_id = (p2["discord_id"] if p2 and p2["discord_id"] else (p1["discord_id"] if p1 and p1["discord_id"] else None))

    combined_pts = pts1 + pts2
    combined_tot = tot1 + tot2
    combined_darks = darks1 + darks2
    combined_att = att1 + att2

    cursor.execute("""
        INSERT INTO players (pj_name, discord_id, current_points, total_points_history, darks_paid, events_attended, clan)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pj_name) DO UPDATE SET
            discord_id = COALESCE(excluded.discord_id, players.discord_id),
            current_points = excluded.current_points,
            total_points_history = excluded.total_points_history,
            darks_paid = excluded.darks_paid,
            events_attended = excluded.events_attended,
            clan = excluded.clan
    """, (target, disc_id, combined_pts, combined_tot, combined_darks, combined_att, clan))

    cursor.execute("UPDATE event_participants SET pj_name = ? WHERE pj_name = ? COLLATE NOCASE", (target, can1))
    cursor.execute("UPDATE event_participants SET pj_name = ? WHERE pj_name = ? COLLATE NOCASE", (target, can2))

    cursor.execute("UPDATE payouts SET pj_name = ? WHERE pj_name = ? COLLATE NOCASE", (target, can1))
    cursor.execute("UPDATE payouts SET pj_name = ? WHERE pj_name = ? COLLATE NOCASE", (target, can2))

    cursor.execute("SELECT holder, darks_count FROM dark_vault WHERE holder = ? COLLATE NOCASE", (can1,))
    v1 = cursor.fetchone()
    cursor.execute("SELECT holder, darks_count FROM dark_vault WHERE holder = ? COLLATE NOCASE", (can2,))
    v2 = cursor.fetchone()
    v_total = (v1["darks_count"] if v1 else 0) + (v2["darks_count"] if v2 else 0)
    if v1 or v2:
        cursor.execute("DELETE FROM dark_vault WHERE holder = ? COLLATE NOCASE", (can1,))
        cursor.execute("DELETE FROM dark_vault WHERE holder = ? COLLATE NOCASE", (can2,))
        cursor.execute("""
            INSERT INTO dark_vault (holder, darks_count, updated_by, note)
            VALUES (?, ?, 'Sistema', 'Unificación de personajes')
            ON CONFLICT(holder) DO UPDATE SET darks_count = excluded.darks_count
        """, (target, v_total))

    cursor.execute("UPDATE vault_transactions SET from_holder = ? WHERE from_holder = ? COLLATE NOCASE", (target, can1))
    cursor.execute("UPDATE vault_transactions SET from_holder = ? WHERE from_holder = ? COLLATE NOCASE", (target, can2))
    cursor.execute("UPDATE vault_transactions SET to_holder = ? WHERE to_holder = ? COLLATE NOCASE", (target, can1))
    cursor.execute("UPDATE vault_transactions SET to_holder = ? WHERE to_holder = ? COLLATE NOCASE", (target, can2))

    cursor.execute("UPDATE player_aliases SET canonical_pj_name = ? WHERE canonical_pj_name = ? COLLATE NOCASE", (target, can1))
    cursor.execute("UPDATE player_aliases SET canonical_pj_name = ? WHERE canonical_pj_name = ? COLLATE NOCASE", (target, can2))

    cursor.execute("DELETE FROM player_aliases WHERE alias = ? COLLATE NOCASE", (target,))

    for a in [can1, can2, pj1, pj2]:
        if a and a.lower() != target.lower():
            cursor.execute("""
                INSERT INTO player_aliases (alias, canonical_pj_name)
                VALUES (?, ?)
                ON CONFLICT(alias) DO UPDATE SET canonical_pj_name = excluded.canonical_pj_name
            """, (a, target))

    if "/" in target:
        for part in target.split("/"):
            p_clean = part.strip()
            if p_clean and p_clean.lower() != target.lower():
                cursor.execute("""
                    INSERT INTO player_aliases (alias, canonical_pj_name)
                    VALUES (?, ?)
                    ON CONFLICT(alias) DO UPDATE SET canonical_pj_name = excluded.canonical_pj_name
                """, (p_clean, target))

    if can1.lower() != target.lower():
        cursor.execute("DELETE FROM players WHERE pj_name = ? COLLATE NOCASE", (can1,))
    if can2.lower() != target.lower():
        cursor.execute("DELETE FROM players WHERE pj_name = ? COLLATE NOCASE", (can2,))

    conn.commit()
    conn.close()

    info = {
        "canonical": target,
        "combined_points": combined_pts,
        "pts1": pts1,
        "pts2": pts2,
        "pj1": can1,
        "pj2": can2,
        "clan": clan
    }
    return True, f"✅ Se vincularon **{can1}** y **{can2}** como la misma persona bajo el perfil **'{target}'**.\n📊 Puntos unificados: **{pts1}** + **{pts2}** = **{combined_pts} pts**.", info

def remove_alias(alias: str) -> Tuple[bool, str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM player_aliases WHERE alias = ? COLLATE NOCASE", (alias.strip(),))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if deleted:
        return True, f"🗑️ Se eliminó el alias **'{alias}'**."
    return False, f"❌ No se encontró ningún alias registrado con el nombre '{alias}'."

def get_aliases() -> List[sqlite3.Row]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT alias, canonical_pj_name, created_at FROM player_aliases ORDER BY canonical_pj_name ASC, alias ASC")
    rows = cursor.fetchall()
    conn.close()
    return rows

RESERVED_PLAYER_WORDS = {
    'dark', 'darks', 'joya', 'joyas', 'su', 'sus', 'un', 'una', 'dos', 'tres', 'cuatro', 'cinco',
    'seis', 'diez', 'al', 'del', 'el', 'la', 'los', 'las', 'baul', 'baúl', 'clan', 'alianza',
    'evento', 'party', 'bc', 'ds', 'cc', 'admin', 'admins', 'puntos', 'punto', 'le', 'les',
    'me', 'nos', 'te', 'se', 'ya', 'hoy', 'ayer', 'guardada', 'guardadas', 'almacenada', 'almacenadas',
    'mas', 'más', 'no', 'pero', 'si', 'sí', 'eso', 'esto'
}

def resolve_pj_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or cleaned.lower() in RESERVED_PLAYER_WORDS:
        return cleaned
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Buscar en aliases
    cursor.execute("SELECT canonical_pj_name FROM player_aliases WHERE alias = ? COLLATE NOCASE", (cleaned,))
    row = cursor.fetchone()
    if row:
        canonical = row["canonical_pj_name"]
        conn.close()
        return canonical

    # 2. Buscar en tabla players (case-insensitive)
    cursor.execute("SELECT pj_name FROM players WHERE pj_name = ? COLLATE NOCASE", (cleaned,))
    row = cursor.fetchone()
    if row:
        canonical = row["pj_name"]
        conn.close()
        return canonical

    # Obtener todos los jugadores para análisis de sub-partes y difflib
    cursor.execute("SELECT pj_name FROM players")
    all_players = cursor.fetchall()
    
    no_digits = re.sub(r'\d+$', '', cleaned).strip()

    # 3. Revisar si algún jugador existente tiene formato Pj1/Pj2 y coincide con cleaned o no_digits
    for sp in all_players:
        if "/" in sp["pj_name"]:
            parts = [p.strip().lower() for p in sp["pj_name"].split("/") if p.strip()]
            if cleaned.lower() in parts or (no_digits and no_digits.lower() in parts):
                canonical = sp["pj_name"]
                if cleaned.lower() not in RESERVED_PLAYER_WORDS:
                    cursor.execute("INSERT OR REPLACE INTO player_aliases (alias, canonical_pj_name) VALUES (?, ?)", (cleaned, canonical))
                    conn.commit()
                conn.close()
                return canonical

    # 4. Tolerancia a errores de tipeo y alias parciales (ej: 'Carmelnango' -> 'Camarlengo', 'Storfly' -> 'stormfly')
    if len(cleaned) >= 4 and cleaned.lower() not in RESERVED_PLAYER_WORDS:
        candidates = {}
        for p in all_players:
            full = p["pj_name"]
            candidates[full.lower()] = full
            if "/" in full:
                for sub in full.split("/"):
                    sub_clean = sub.strip()
                    if sub_clean:
                        candidates[sub_clean.lower()] = full

        queries = [cleaned.lower()]
        if no_digits and no_digits.lower() != cleaned.lower() and len(no_digits) >= 4:
            queries.append(no_digits.lower())

        for q in queries:
            if q in RESERVED_PLAYER_WORDS:
                continue
            close_matches = difflib.get_close_matches(q, list(candidates.keys()), n=1, cutoff=0.80)
            if close_matches:
                canonical = candidates[close_matches[0]]
                cursor.execute("INSERT OR REPLACE INTO player_aliases (alias, canonical_pj_name) VALUES (?, ?)", (cleaned, canonical))
                conn.commit()
                conn.close()
                return canonical

    conn.close()
    return cleaned

def get_or_create_player(pj_name: str, discord_id: Optional[int] = None) -> sqlite3.Row:
    pj_name = resolve_pj_name(pj_name)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (pj_name,))
    player = cursor.fetchone()
    if not player:
        cursor.execute("""
            INSERT INTO players (pj_name, discord_id, current_points, total_points_history, darks_paid, events_attended, raffle_streak_lost)
            VALUES (?, ?, 0, 0, 0, 0, 0)
        """, (pj_name, discord_id))
        conn.commit()
        cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (pj_name,))
        player = cursor.fetchone()
    elif discord_id is not None and player["discord_id"] != discord_id:
        cursor.execute("UPDATE players SET discord_id = ? WHERE pj_name = ? COLLATE NOCASE", (discord_id, pj_name))
        conn.commit()
        cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (pj_name,))
        player = cursor.fetchone()
    conn.close()
    return player

def link_discord_account(pj_name: str, discord_id: int):
    pj_name = resolve_pj_name(pj_name)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (pj_name,))
    if not cursor.fetchone():
        cursor.execute("""
            INSERT INTO players (pj_name, discord_id, current_points, total_points_history, darks_paid, events_attended, raffle_streak_lost)
            VALUES (?, ?, 0, 0, 0, 0, 0)
        """, (pj_name, discord_id))
    else:
        cursor.execute("UPDATE players SET discord_id = ? WHERE pj_name = ? COLLATE NOCASE", (discord_id, pj_name))
    conn.commit()
    conn.close()

def get_player(pj_name: str) -> Optional[sqlite3.Row]:
    pj_name = resolve_pj_name(pj_name)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (pj_name.strip(),))
    row = cursor.fetchone()
    conn.close()
    return row

def get_player_by_discord(discord_id: int) -> List[sqlite3.Row]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE discord_id = ?", (discord_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def record_event(
    event_name: str,
    darks_count: int,
    total_points: int,
    fixed_per_pj: int,
    leftover_points: int,
    registered_by: str,
    participant_results: List[Dict]
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO events (event_name, darks_count, total_points, fixed_per_pj, leftover_points, registered_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (event_name, darks_count, total_points, fixed_per_pj, leftover_points, registered_by, now))
    event_id = cursor.lastrowid

    for p in participant_results:
        pj = resolve_pj_name(p["pj_name"])
        fixed = p["fixed"]
        raffle = p["raffle"]
        total_gained = fixed + raffle
        won = 1 if p["won_raffle"] else 0
        roll = p.get("roll", 0.0)
        clan = resolve_clan_name(p.get("clan") or "DxMENTOR")

        cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (pj,))
        existing = cursor.fetchone()
        if not existing:
            cursor.execute("""
                INSERT INTO players (pj_name, current_points, total_points_history, darks_paid, events_attended, raffle_streak_lost, last_event_at, clan)
                VALUES (?, ?, ?, 0, 1, ?, ?, ?)
            """, (pj, total_gained, total_gained, 0 if won else 1, now, clan))
        else:
            new_streak = 0 if won else (existing["raffle_streak_lost"] + (1 if leftover_points > 0 else 0))
            cursor.execute("""
                UPDATE players
                SET current_points = current_points + ?,
                    total_points_history = total_points_history + ?,
                    events_attended = events_attended + 1,
                    raffle_streak_lost = ?,
                    last_event_at = ?,
                    clan = ?
                WHERE pj_name = ? COLLATE NOCASE
            """, (total_gained, total_gained, new_streak, now, clan, pj))

        cursor.execute("""
            INSERT INTO event_participants (event_id, pj_name, fixed_points, raffle_points, won_raffle, roll_score, clan)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (event_id, pj, fixed, raffle, won, roll, clan))

    conn.commit()
    conn.close()
    return event_id

def record_payout(pj_name: str, darks_to_pay: int, registered_by: str, note: str = "", from_holder: Optional[str] = None) -> Tuple[bool, str]:
    pj_name = resolve_pj_name(pj_name)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (pj_name.strip(),))
    player = cursor.fetchone()
    if not player:
        conn.close()
        return False, f"El personaje '{pj_name}' no existe en la base de datos."

    real_pj_name = player["pj_name"]
    points_needed = darks_to_pay * 10
    if player["current_points"] < points_needed:
        darks_posibles = player["current_points"] // 10
        conn.close()
        return False, f"'{real_pj_name}' tiene {player['current_points']} puntos. Solo puede cobrar hasta {darks_posibles} darks."

    cursor.execute("""
        UPDATE players
        SET current_points = current_points - ?,
            darks_paid = darks_paid + ?
        WHERE pj_name = ? COLLATE NOCASE
    """, (points_needed, darks_to_pay, real_pj_name))

    # Si se especificó quién entregó la dark de su almacenamiento/baúl
    holder_msg = ""
    if from_holder:
        resolved_holder = resolve_pj_name(from_holder)
        cursor.execute("SELECT darks_count FROM dark_vault WHERE holder = ? COLLATE NOCASE", (resolved_holder,))
        vault_row = cursor.fetchone()
        if vault_row:
            current_vault = vault_row["darks_count"]
            new_vault = max(0, current_vault - darks_to_pay)
            cursor.execute("""
                UPDATE dark_vault
                SET darks_count = ?, updated_at = CURRENT_TIMESTAMP, updated_by = ?
                WHERE holder = ? COLLATE NOCASE
            """, (new_vault, registered_by, resolved_holder))
            cursor.execute("""
                INSERT INTO vault_transactions (action_type, from_holder, to_holder, darks_count, registered_by, note, created_at)
                VALUES ('PAYOUT', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (resolved_holder, real_pj_name, darks_to_pay, registered_by, f"Entrega de {darks_to_pay} Dark(s) por puntos"))
            holder_msg = f"\n🏛️ Se descontaron **{darks_to_pay} Dark{'s' if darks_to_pay != 1 else ''}** del almacenamiento/baúl de **{resolved_holder}** (le quedan **{new_vault}**)."
        else:
            holder_msg = f"\n⚠️ Nota: **{resolved_holder}** no figuraba con darks guardadas en el baúl."

    full_note = f"{note} (Entregado por {from_holder})" if from_holder and from_holder not in note else note
    cursor.execute("""
        INSERT INTO payouts (pj_name, darks_paid, points_deducted, registered_by, note)
        VALUES (?, ?, ?, ?, ?)
    """, (real_pj_name, darks_to_pay, points_needed, registered_by, full_note))

    conn.commit()
    conn.close()
    return True, f"Se registraron {darks_to_pay} darks pagadas a '{real_pj_name}'. Se descontaron {points_needed} puntos.{holder_msg}"

def get_pending_debts(clan: Optional[str] = None) -> List[Dict]:
    """Retorna jugadores que tienen al menos 10 puntos (es decir, al menos 1 dark por cobrar)."""
    conn = get_connection()
    cursor = conn.cursor()
    if clan:
        cursor.execute("""
            SELECT pj_name, discord_id, current_points, darks_paid, events_attended, clan
            FROM players
            WHERE current_points >= 10 AND clan = ? COLLATE NOCASE
            ORDER BY current_points DESC
        """, (clan.strip(),))
    else:
        cursor.execute("""
            SELECT pj_name, discord_id, current_points, darks_paid, events_attended, clan
            FROM players
            WHERE current_points >= 10
            ORDER BY current_points DESC
        """)
    rows = cursor.fetchall()
    debts = []
    for r in rows:
        darks_owed = r["current_points"] // 10
        rem_points = r["current_points"] % 10
        debts.append({
            "pj_name": r["pj_name"],
            "discord_id": r["discord_id"],
            "current_points": r["current_points"],
            "darks_owed": darks_owed,
            "rem_points": rem_points,
            "darks_paid": r["darks_paid"],
            "events_attended": r["events_attended"],
            "clan": r["clan"] if "clan" in r.keys() and r["clan"] else "Mentores"
        })
    conn.close()
    return debts

def get_all_players_ranking(limit: int = 50, clan: Optional[str] = None) -> List[sqlite3.Row]:
    conn = get_connection()
    cursor = conn.cursor()
    if clan:
        cursor.execute("""
            SELECT pj_name, discord_id, current_points, total_points_history, darks_paid, events_attended, raffle_streak_lost, clan
            FROM players
            WHERE clan = ? COLLATE NOCASE
            ORDER BY current_points DESC, events_attended DESC
            LIMIT ?
        """, (clan.strip(), limit))
    else:
        cursor.execute("""
            SELECT pj_name, discord_id, current_points, total_points_history, darks_paid, events_attended, raffle_streak_lost, clan
            FROM players
            ORDER BY current_points DESC, events_attended DESC
            LIMIT ?
        """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_player_event_history(pj_name: str, limit: int = 10) -> List[Dict]:
    """Retorna el historial de eventos en los que participó un PJ."""
    pj_name = resolve_pj_name(pj_name)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.id, e.event_name, e.darks_count, e.created_at,
               ep.fixed_points, ep.raffle_points, ep.won_raffle, ep.roll_score
        FROM event_participants ep
        JOIN events e ON ep.event_id = e.id
        WHERE ep.pj_name = ? COLLATE NOCASE
        ORDER BY e.created_at DESC
        LIMIT ?
    """, (pj_name.strip(), limit))
    rows = cursor.fetchall()
    history = []
    for r in rows:
        history.append({
            "event_id": r["id"],
            "event_name": r["event_name"],
            "darks_count": r["darks_count"],
            "date": r["created_at"][:16].replace("T", " ") if r["created_at"] else "",
            "fixed": r["fixed_points"],
            "raffle": r["raffle_points"],
            "total_earned": r["fixed_points"] + r["raffle_points"],
            "won_raffle": bool(r["won_raffle"]),
            "roll": r["roll_score"]
        })
    conn.close()
    return history

def get_player_payout_history(pj_name: str, limit: int = 10) -> List[Dict]:
    pj_name = resolve_pj_name(pj_name)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, darks_paid, points_deducted, registered_by, note, created_at
        FROM payouts
        WHERE pj_name = ? COLLATE NOCASE
        ORDER BY created_at DESC
        LIMIT ?
    """, (pj_name.strip(), limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ====================================================================
# CONTROL DE CUSTODIA / BAÚL DE DARKS FÍSICAS
# ====================================================================

def set_dark_custody(holder: str, count: int, updated_by: str, note: str = "") -> Tuple[bool, str]:
    holder = resolve_pj_name(holder).strip()
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("SELECT * FROM dark_vault WHERE holder = ? COLLATE NOCASE", (holder,))
    existing = cursor.fetchone()
    count = max(0, count)
    if existing:
        cursor.execute("""
            UPDATE dark_vault
            SET darks_count = ?, updated_at = ?, updated_by = ?, note = ?
            WHERE holder = ? COLLATE NOCASE
        """, (count, now, updated_by, note, holder))
    else:
        cursor.execute("""
            INSERT INTO dark_vault (holder, darks_count, updated_at, updated_by, note)
            VALUES (?, ?, ?, ?, ?)
        """, (holder, count, now, updated_by, note))

    cursor.execute("""
        INSERT INTO vault_transactions (action_type, from_holder, to_holder, darks_count, registered_by, note, created_at)
        VALUES ('SET', NULL, ?, ?, ?, ?, ?)
    """, (holder, count, updated_by, note or f"Custodia fijada en {count} Darks", now))

    conn.commit()
    conn.close()
    return True, f"Se actualizó la custodia: **{holder}** tiene **{count} Dark{'s' if count != 1 else ''}**."

def add_dark_custody(holder: str, delta: int, updated_by: str, note: str = "") -> Tuple[bool, str]:
    holder = resolve_pj_name(holder).strip()
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("SELECT * FROM dark_vault WHERE holder = ? COLLATE NOCASE", (holder,))
    existing = cursor.fetchone()
    if existing:
        new_count = max(0, existing["darks_count"] + delta)
        cursor.execute("""
            UPDATE dark_vault
            SET darks_count = ?, updated_at = ?, updated_by = ?, note = ?
            WHERE holder = ? COLLATE NOCASE
        """, (new_count, now, updated_by, note, holder))
    else:
        new_count = max(0, delta)
        cursor.execute("""
            INSERT INTO dark_vault (holder, darks_count, updated_at, updated_by, note)
            VALUES (?, ?, ?, ?, ?)
        """, (holder, new_count, now, updated_by, note))

    cursor.execute("""
        INSERT INTO vault_transactions (action_type, from_holder, to_holder, darks_count, registered_by, note, created_at)
        VALUES ('ADD', NULL, ?, ?, ?, ?, ?)
    """, (holder, delta, updated_by, note or f"Se sumaron {delta} Darks al baúl", now))

    conn.commit()
    conn.close()
    return True, f"Se ajustó la custodia: **{holder}** ahora tiene **{new_count} Dark{'s' if new_count != 1 else ''}**."

def transfer_dark_custody(from_holder: str, to_holder: str, count: int, registered_by: str, note: str = "") -> Tuple[bool, str]:
    if count <= 0:
        return False, "❌ La cantidad de darks a transferir debe ser mayor a 0."

    from_h = resolve_pj_name(from_holder).strip()
    to_h = resolve_pj_name(to_holder).strip()

    if from_h.lower() == to_h.lower():
        return False, "❌ El personaje de origen y de destino no pueden ser el mismo."

    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    cursor.execute("SELECT darks_count FROM dark_vault WHERE holder = ? COLLATE NOCASE", (from_h,))
    row_from = cursor.fetchone()
    current_from = row_from["darks_count"] if row_from else 0

    if current_from < count:
        conn.close()
        return False, f"❌ **{from_h}** solo tiene **{current_from} Dark{'s' if current_from != 1 else ''}** en su baúl. No puede transferir {count}."

    new_from = current_from - count
    cursor.execute("""
        UPDATE dark_vault
        SET darks_count = ?, updated_at = ?, updated_by = ?, note = ?
        WHERE holder = ? COLLATE NOCASE
    """, (new_from, now, registered_by, f"Transferido a {to_h}", from_h))

    cursor.execute("SELECT darks_count FROM dark_vault WHERE holder = ? COLLATE NOCASE", (to_h,))
    row_to = cursor.fetchone()
    if row_to:
        new_to = row_to["darks_count"] + count
        cursor.execute("""
            UPDATE dark_vault
            SET darks_count = ?, updated_at = ?, updated_by = ?, note = ?
            WHERE holder = ? COLLATE NOCASE
        """, (new_to, now, registered_by, f"Recibido de {from_h}", to_h))
    else:
        new_to = count
        cursor.execute("""
            INSERT INTO dark_vault (holder, darks_count, updated_at, updated_by, note)
            VALUES (?, ?, ?, ?, ?)
        """, (to_h, new_to, now, registered_by, f"Recibido de {from_h}"))

    cursor.execute("""
        INSERT INTO vault_transactions (action_type, from_holder, to_holder, darks_count, registered_by, note, created_at)
        VALUES ('TRANSFER', ?, ?, ?, ?, ?, ?)
    """, (from_h, to_h, count, registered_by, note or f"Transferencia de custodia", now))

    conn.commit()
    conn.close()
    return True, f"🔄 **Transferencia de Custodia Exitosa:**\nSe transfirieron **{count} Dark{'s' if count != 1 else ''}** de **{from_h}** a **{to_h}**.\n• **{from_h}**: le quedan **{new_from} Darks**\n• **{to_h}**: ahora tiene **{new_to} Darks**."

def reset_dark_vault(registered_by: str = "") -> Tuple[bool, str]:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("DELETE FROM dark_vault")
    cursor.execute("""
        INSERT INTO vault_transactions (action_type, from_holder, to_holder, darks_count, registered_by, note, created_at)
        VALUES ('RESET', NULL, NULL, 0, ?, 'Reinicio total del baúl a 0', ?)
    """, (registered_by, now))
    conn.commit()
    conn.close()
    return True, "✅ Se vació y reinició completamente el almacenamiento de darks del baúl. Ahora está en 0."

def get_dark_vault_history(limit: int = 20) -> List[Dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, action_type, from_holder, to_holder, darks_count, registered_by, note, created_at
        FROM vault_transactions
        ORDER BY created_at DESC, id DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_dark_vault() -> List[Dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT holder, darks_count, updated_at, updated_by, note
        FROM dark_vault
        WHERE darks_count > 0
        ORDER BY darks_count DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def bulk_set_dark_custody(entries: List[Tuple[str, int, str]], updated_by: str) -> Tuple[bool, str, int]:
    """
    Reemplaza y actualiza la lista completa de custodios del baúl desde el editor masivo de texto.
    entries: lista de tuplas (holder_name, darks_count, optional_note)
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    # Limpiar tabla previa para aplicar la nueva lista exacta
    cursor.execute("DELETE FROM dark_vault")

    total_darks = 0
    added_count = 0

    for raw_holder, count, note in entries:
        holder = resolve_pj_name(raw_holder).strip()
        if not holder or holder.lower() in RESERVED_PLAYER_WORDS:
            continue
        if count <= 0:
            continue

        cursor.execute("""
            INSERT INTO dark_vault (holder, darks_count, updated_at, updated_by, note)
            VALUES (?, ?, ?, ?, ?)
        """, (holder, count, now, updated_by, note))
        total_darks += count
        added_count += 1

    cursor.execute("""
        INSERT INTO vault_transactions (action_type, from_holder, to_holder, darks_count, registered_by, note, created_at)
        VALUES ('SET', NULL, 'BAÚL_GENERAL', ?, ?, 'Actualización masiva de custodia vía editor', ?)
    """, (total_darks, updated_by, now))

    conn.commit()
    conn.close()
    return True, f"✅ Baúl actualizado: **{added_count} miembros** tienen en total **{total_darks} Darks**.", total_darks

def delete_player(pj_name: str) -> Tuple[bool, str]:
    conn = get_connection()
    pj_name = resolve_pj_name(pj_name)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (pj_name.strip(),))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        return False, f"El personaje '{pj_name}' no existe en la base de datos."

    cursor.execute("DELETE FROM event_participants WHERE pj_name = ? COLLATE NOCASE", (pj_name.strip(),))
    cursor.execute("DELETE FROM payouts WHERE pj_name = ? COLLATE NOCASE", (pj_name.strip(),))
    cursor.execute("DELETE FROM dark_vault WHERE holder = ? COLLATE NOCASE", (pj_name.strip(),))
    cursor.execute("DELETE FROM player_aliases WHERE canonical_pj_name = ? COLLATE NOCASE OR alias = ? COLLATE NOCASE", (pj_name.strip(), pj_name.strip()))
    cursor.execute("DELETE FROM players WHERE pj_name = ? COLLATE NOCASE", (pj_name.strip(),))
    conn.commit()
    conn.close()
    return True, f"Se eliminó completamente el personaje **{pj_name}** de la base de datos."

def update_player_points(pj_name: str, new_points: Optional[int] = None, clan: Optional[str] = None, darks_paid: Optional[int] = None) -> Tuple[bool, str]:
    pj_name = resolve_pj_name(pj_name)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (pj_name.strip(),))
    existing = cursor.fetchone()
    now = datetime.now().isoformat()

    if not existing:
        pts = max(0, new_points) if new_points is not None else 0
        cl = resolve_clan_name(clan or "DxMENTOR")
        dp = max(0, darks_paid) if darks_paid is not None else 0
        total_hist = pts + (dp * 10)
        cursor.execute("""
            INSERT INTO players (pj_name, current_points, total_points_history, darks_paid, events_attended, raffle_streak_lost, last_event_at, clan)
            VALUES (?, ?, ?, ?, 0, 0, ?, ?)
        """, (pj_name.strip(), pts, total_hist, dp, now, cl))
        conn.commit()
        conn.close()
        d_str = f" ({dp}D cobradas)" if dp > 0 else ""
        return True, f"Se creó el personaje **{pj_name}** `[{cl}]` con **{pts} pts**{d_str}."

    target_pts = max(0, new_points) if new_points is not None else existing["current_points"]
    target_clan = resolve_clan_name(clan) if clan else (existing["clan"] or "DxMENTOR")
    target_darks = max(0, darks_paid) if darks_paid is not None else existing["darks_paid"]
    total_hist = max(existing["total_points_history"], target_pts + (target_darks * 10))

    cursor.execute("""
        UPDATE players
        SET current_points = ?, total_points_history = ?, darks_paid = ?, last_event_at = ?, clan = ?
        WHERE pj_name = ? COLLATE NOCASE
    """, (target_pts, total_hist, target_darks, now, target_clan, pj_name.strip()))
    conn.commit()
    conn.close()

    d_info = f" ({target_darks}D cobrada{'s' if target_darks > 1 else ''})" if target_darks > 0 else ""
    return True, f"Se actualizó **{pj_name}** `[{target_clan}]`: **{target_pts} pts**{d_info}."

def bulk_import_points(items: List[Dict], mode: str = "ADD", updated_by: str = "") -> Tuple[int, List[str]]:
    """
    items = [{"pj_name": "masterr", "points": 15, "clan": "DxMENTOR"}, ...]
    mode: "ADD" (sumar a los existentes) o "SET" (reemplazar/fijar)
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    imported_count = 0
    summary_lines = []

    for item in items:
        raw_pj = str(item.get("pj_name", "")).strip()
        pts = int(item.get("points", 0))
        clan = resolve_clan_name(str(item.get("clan") or "DxMENTOR").strip())
        if not raw_pj:
            continue

        pj = resolve_pj_name(raw_pj)

        # Si el nombre contiene '/', auto-registrar cada parte como alias del nombre canónico
        parts = [p.strip() for p in pj.split("/") if p.strip()]
        if len(parts) > 1:
            for part in parts:
                if part.lower() != pj.lower():
                    cursor.execute("""
                        INSERT INTO player_aliases (alias, canonical_pj_name)
                        VALUES (?, ?)
                        ON CONFLICT(alias) DO UPDATE SET canonical_pj_name = excluded.canonical_pj_name
                    """, (part, pj))

        raw_note = str(item.get("note", "")).strip()
        item_darks_paid = item.get("darks_paid")
        if item_darks_paid is None and raw_note:
            d_match = re.search(r'(\d+)\s*[dD]?', raw_note)
            item_darks_paid = int(d_match.group(1)) if d_match else None

        cursor.execute("SELECT * FROM players WHERE pj_name = ? COLLATE NOCASE", (pj,))
        existing = cursor.fetchone()

        if existing:
            if mode == "ADD":
                target_darks = max(existing["darks_paid"], item_darks_paid or 0)
                new_curr = existing["current_points"] + pts
                new_hist = existing["total_points_history"] + pts
                target_clan = clan or existing["clan"] or "DxMENTOR"
                cursor.execute("""
                    UPDATE players
                    SET current_points = ?, total_points_history = ?, darks_paid = ?, last_event_at = ?, clan = ?
                    WHERE pj_name = ? COLLATE NOCASE
                """, (new_curr, new_hist, target_darks, now, target_clan, pj))
                d_info = f" ({target_darks}D cobrada)" if target_darks == 1 else (f" ({target_darks}D cobradas)" if target_darks > 1 else "")
                summary_lines.append(f"• **{pj}** `[{target_clan}]`: +{pts} pts{d_info} (Nuevo saldo: **{new_curr} pts**)")
            else: # SET
                target_darks = item_darks_paid if item_darks_paid is not None else existing["darks_paid"]
                target_clan = clan if item.get("clan") else (existing["clan"] or "DxMENTOR")
                total_hist = max(existing["total_points_history"], pts + (target_darks * 10))
                cursor.execute("""
                    UPDATE players
                    SET current_points = ?, total_points_history = ?, darks_paid = ?, last_event_at = ?, clan = ?
                    WHERE pj_name = ? COLLATE NOCASE
                """, (pts, total_hist, target_darks, now, target_clan, pj))
                d_info = f" ({target_darks}D cobrada)" if target_darks == 1 else (f" ({target_darks}D cobradas)" if target_darks > 1 else "")
                summary_lines.append(f"• **{pj}** `[{target_clan}]`: fijado en **{pts} pts**{d_info}")
        else:
            target_darks = item_darks_paid or 0
            target_clan = clan or "DxMENTOR"
            total_hist = pts + (target_darks * 10)
            cursor.execute("""
                INSERT INTO players (pj_name, current_points, total_points_history, darks_paid, events_attended, raffle_streak_lost, last_event_at, clan)
                VALUES (?, ?, ?, ?, 0, 0, ?, ?)
            """, (pj, pts, total_hist, target_darks, now, target_clan))
            d_info = f" ({target_darks}D cobrada)" if target_darks == 1 else (f" ({target_darks}D cobradas)" if target_darks > 1 else "")
            summary_lines.append(f"• **{pj}** `[{target_clan}]`: creado con **{pts} pts**{d_info}")

        imported_count += 1

    conn.commit()
    conn.close()
    return imported_count, summary_lines

# ====================================================================
# GESTIÓN DE USUARIOS ADMINISTRADORES (PERMISOS DE CARGA)
# ====================================================================

def add_admin(discord_id: int, username: str, added_by: str) -> Tuple[bool, str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM admin_users WHERE discord_id = ?", (discord_id,))
    if cursor.fetchone():
        conn.close()
        return False, f"⚠️ El usuario **{username}** ya está registrado como administrador."

    cursor.execute("INSERT INTO admin_users (discord_id, username, added_by) VALUES (?, ?, ?)", (discord_id, username, added_by))
    conn.commit()
    conn.close()
    return True, f"✅ El usuario **{username}** fue autorizado exitosamente como administrador para cargar eventos y puntos."

def remove_admin(discord_id: int) -> Tuple[bool, str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM admin_users WHERE discord_id = ?", (discord_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "❌ El usuario no figura en la lista de administradores autorizados."

    username = row["username"]
    cursor.execute("DELETE FROM admin_users WHERE discord_id = ?", (discord_id,))
    conn.commit()
    conn.close()
    return True, f"🗑️ Se revocaron los permisos de administrador para **{username}**."

def get_admins() -> List[sqlite3.Row]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM admin_users ORDER BY created_at ASC")
    rows = cursor.fetchall()
    conn.close()
    return rows

def is_admin(discord_id: int, username: str = "", is_guild_admin: bool = False, is_guild_owner: bool = False) -> bool:
    """
    Determina si un usuario tiene permisos de administrador para cargar eventos o modificar puntos.
    1. Si es dueño del servidor o tiene permisos de Administrador en Discord -> True.
    2. Si ADMIN_USERS en .env tiene su ID o username -> True.
    3. Si está en la tabla admin_users de la base de datos -> True.
    4. Si NO hay administradores configurados en la DB ni en .env -> True (abierto hasta registrar el primero).
    """
    if is_guild_owner or is_guild_admin:
        return True

    import os
    env_admins = os.getenv("ADMIN_USERS", "")
    if env_admins:
        admins_list = [a.strip().lower() for a in env_admins.split(",") if a.strip()]
        if str(discord_id) in admins_list or (username and username.lower() in admins_list):
            return True

    admins_in_db = get_admins()
    if not admins_in_db and not env_admins:
        return True

    for a in admins_in_db:
        if a["discord_id"] == discord_id:
            return True
        if username and a["username"] and a["username"].lower() == username.lower():
            return True

    return False

# ====================================================================
# REINICIO DE PUNTOS E HISTORIAL (LIMPIEZA GENERAL)
# ====================================================================

def reset_points_and_history(delete_players: bool = True) -> Tuple[bool, str]:
    """
    Reinicia todos los puntos, borra el historial de eventos y los pagos registrados.
    No toca la tabla admin_users ni dark_vault.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM event_participants")
        cursor.execute("DELETE FROM events")
        cursor.execute("DELETE FROM payouts")
        if delete_players:
            cursor.execute("DELETE FROM players")
        else:
            cursor.execute("""
                UPDATE players
                SET current_points = 0,
                    total_points_history = 0,
                    darks_paid = 0,
                    events_attended = 0,
                    raffle_streak_lost = 0,
                    last_event_at = NULL
            """)
        conn.commit()
        conn.close()
        return True, "Se han eliminado todos los eventos, pagos registrados y se reiniciaron los puntos acumulados."
    except Exception as e:
        conn.close()
        return False, f"Error al reiniciar la base de datos: {e}"

# Inicializar tablas y migraciones automáticamente al cargar el módulo
init_db()

