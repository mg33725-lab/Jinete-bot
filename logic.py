import random
from typing import List, Dict, Tuple
from database import get_player

def calculate_distribution(
    darks_count: int,
    pj_names: List[str]
) -> Dict:
    """
    Calcula el reparto de puntos fijos y realiza el sorteo transparente
    con compensación por mala suerte (racha sin ganar sorteos).
    """
    # Eliminar duplicados manteniendo orden y quitando espacios vacíos
    unique_pjs = []
    seen = set()
    for name in pj_names:
        clean = name.strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            unique_pjs.append(clean)

    num_participants = len(unique_pjs)
    if num_participants == 0:
        raise ValueError("Debe haber al menos un participante.")

    total_points = darks_count * 10
    fixed_points = total_points // num_participants
    leftover_points = total_points % num_participants

    participants_data = []

    # Consultar datos de racha para cada PJ
    for pj in unique_pjs:
        player = get_player(pj)
        streak = player["raffle_streak_lost"] if player else 0
        participants_data.append({
            "pj_name": pj,
            "streak": streak,
            "base_roll": 0,
            "bonus": 0,
            "total_roll": 0.0,
            "fixed": fixed_points,
            "raffle": 0,
            "won_raffle": False
        })

    # Si hay puntos sobrantes para sortear
    if leftover_points > 0:
        # Generar tiradas transparentes: d100 + (racha * 8)
        for p in participants_data:
            base = random.randint(1, 100)
            bonus = min(50, p["streak"] * 8) # Tope de 50 de bonus para mantener competitividad
            p["base_roll"] = base
            p["bonus"] = bonus
            # Agregamos un decimal aleatorio pequeño para evitar empates exactos
            p["total_roll"] = round(base + bonus + random.random(), 2)

        # Ordenar de mayor a menor puntaje de tirada
        sorted_by_roll = sorted(participants_data, key=lambda x: x["total_roll"], reverse=True)

        # Los primeros 'leftover_points' se llevan 1 punto extra cada uno
        for i in range(leftover_points):
            sorted_by_roll[i]["won_raffle"] = True
            sorted_by_roll[i]["raffle"] = 1

    return {
        "darks_count": darks_count,
        "total_points": total_points,
        "num_participants": num_participants,
        "fixed_points": fixed_points,
        "leftover_points": leftover_points,
        "results": participants_data
    }

def calculate_direct_reward_distribution(
    total_rewards: int,
    pj_names: List[str],
    reward_name: str = "Exe"
) -> Dict:
    """
    Calcula el reparto de premios directos (exes, darks, items) para un evento en el momento,
    independiente del sistema de puntos acumulativos.
    """
    unique_pjs = []
    seen = set()
    for name in pj_names:
        clean = name.strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            unique_pjs.append(clean)

    num_participants = len(unique_pjs)
    if num_participants == 0:
        raise ValueError("Debe haber al menos un participante.")

    if total_rewards <= 0:
        total_rewards = 1

    fixed_rewards = total_rewards // num_participants
    leftover_rewards = total_rewards % num_participants

    participants_data = []
    for pj in unique_pjs:
        participants_data.append({
            "pj_name": pj,
            "base_roll": 0,
            "bonus": 0,
            "total_roll": 0.0,
            "fixed": fixed_rewards,
            "raffle": 0,
            "total_won": fixed_rewards,
            "won_raffle": False
        })

    # Si sobran premios para sortear
    if leftover_rewards > 0:
        for p in participants_data:
            base = random.randint(1, 100)
            p["base_roll"] = base
            p["total_roll"] = round(base + random.random(), 2)

        sorted_by_roll = sorted(participants_data, key=lambda x: x["total_roll"], reverse=True)
        for i in range(leftover_rewards):
            sorted_by_roll[i]["won_raffle"] = True
            sorted_by_roll[i]["raffle"] = 1
            sorted_by_roll[i]["total_won"] += 1

    return {
        "total_rewards": total_rewards,
        "reward_name": reward_name,
        "num_participants": num_participants,
        "fixed_rewards": fixed_rewards,
        "leftover_rewards": leftover_rewards,
        "results": participants_data
    }
