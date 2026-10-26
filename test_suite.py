import os
import sys
import database as db
import logic

# Asegurar soporte de caracteres utf-8 en consola de Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

def run_tests():
    print("=== Iniciando Pruebas del Sistema ArgenMU ===")

    # Usar base de datos limpia de prueba
    if os.path.exists("argenmu.db"):
        os.remove("argenmu.db")

    db.init_db()
    print("✓ Base de datos inicializada correctamente.")

    # -------------------------------------------------------------
    # Caso 1: 2 Darks (20 pts), 6 PJs
    # Debe dar: 3 fijos a cada uno (18 pts) y 2 puntos sorteados a 2 personas distintas
    # -------------------------------------------------------------
    pjs_caso1 = ["TitanBK", "ElfaBuff", "MagoLoco", "LordDL", "SummonerPro", "GlowMG"]
    res1 = logic.calculate_distribution(darks_count=2, pj_names=pjs_caso1)

    assert res1["total_points"] == 20, f"Error en total_points: {res1['total_points']}"
    assert res1["fixed_points"] == 3, f"Error en fixed_points: {res1['fixed_points']}"
    assert res1["leftover_points"] == 2, f"Error en leftover_points: {res1['leftover_points']}"

    winners = [p for p in res1["results"] if p["won_raffle"]]
    assert len(winners) == 2, f"Deben ser exactamente 2 ganadores de sorteo, hay {len(winners)}"
    for w in winners:
        assert w["raffle"] == 1, "Cada ganador debe llevarse exactamente 1 punto de sorteo"

    # Registrar el evento en la BD
    ev_id1 = db.record_event(
        event_name="Frozen",
        darks_count=2,
        total_points=res1["total_points"],
        fixed_per_pj=res1["fixed_points"],
        leftover_points=res1["leftover_points"],
        registered_by="AdminTest",
        participant_results=res1["results"]
    )
    print(f"✓ Caso 1 (2 Darks, 6 PJs) superado. Evento registrado con ID #{ev_id1}")

    # Verificar que los no ganadores tienen racha de mala suerte incrementada a 1
    for p in res1["results"]:
        player = db.get_player(p["pj_name"])
        if p["won_raffle"]:
            assert player["raffle_streak_lost"] == 0, "Ganador debe tener racha 0"
            assert player["current_points"] == 4, "Ganador debe tener 4 puntos (3 fijos + 1 sorteo)"
        else:
            assert player["raffle_streak_lost"] == 1, "No ganador debe tener racha 1"
            assert player["current_points"] == 3, "No ganador debe tener 3 puntos (3 fijos)"
    print("✓ Racha de mala suerte y puntos acumulados verificados correctamente.")

    # -------------------------------------------------------------
    # Caso 2: 1 Dark (10 pts), 15 PJs
    # Debe dar: 0 fijos y 10 puntos sorteados a 10 personas distintas (5 se quedan sin sumar)
    # -------------------------------------------------------------
    pjs_caso2 = [f"Player_{i}" for i in range(1, 16)]
    res2 = logic.calculate_distribution(darks_count=1, pj_names=pjs_caso2)

    assert res2["total_points"] == 10
    assert res2["fixed_points"] == 0
    assert res2["leftover_points"] == 10

    winners2 = [p for p in res2["results"] if p["won_raffle"]]
    non_winners2 = [p for p in res2["results"] if not p["won_raffle"]]
    assert len(winners2) == 10, f"Deben ser 10 ganadores de sorteo, hay {len(winners2)}"
    assert len(non_winners2) == 5, f"Deben ser 5 que no suman, hay {len(non_winners2)}"

    ev_id2 = db.record_event(
        event_name="Narcondra",
        darks_count=1,
        total_points=res2["total_points"],
        fixed_per_pj=res2["fixed_points"],
        leftover_points=res2["leftover_points"],
        registered_by="AdminTest",
        participant_results=res2["results"]
    )
    print(f"✓ Caso 2 (1 Dark, 15 PJs -> 10 suman, 5 quedan en 0) superado. Evento ID #{ev_id2}")

    # -------------------------------------------------------------
    # Caso 3: Acumulación de 10+ puntos y pago de Darks
    # -------------------------------------------------------------
    # Le sumamos varios eventos a TitanBK para que llegue a 15 puntos
    # Ya tiene 4 puntos del Caso 1. Simulamos que suma 11 puntos más
    p_titan = db.get_player("TitanBK")
    db.get_or_create_player("TitanBK")
    conn = db.get_connection()
    conn.execute("UPDATE players SET current_points = 15 WHERE pj_name = 'TitanBK'")
    conn.commit()
    conn.close()

    debts = db.get_pending_debts()
    titan_debt = next((d for d in debts if d["pj_name"] == "TitanBK"), None)
    assert titan_debt is not None, "TitanBK debe aparecer en la lista de deudas"
    assert titan_debt["darks_owed"] == 1, f"TitanBK con 15 puntos debe cobrar 1 Dark, dice {titan_debt['darks_owed']}"
    assert titan_debt["rem_points"] == 5, f"Le deben quedar 5 puntos, dice {titan_debt['rem_points']}"
    print("✓ Detección de deudas (15 puntos -> 1 Dark a cobrar, 5 puntos restantes) superada.")

    # Pagar 1 dark a TitanBK
    ok, msg = db.record_payout("TitanBK", darks_to_pay=1, registered_by="AdminTest", note="Entrega en baúl")
    assert ok is True, f"Fallo al registrar pago: {msg}"

    titan_after = db.get_player("TitanBK")
    assert titan_after["current_points"] == 5, f"Deben quedar 5 puntos tras pagar 1 dark, quedan {titan_after['current_points']}"
    assert titan_after["darks_paid"] == 1, f"Darks pagadas debe ser 1, es {titan_after['darks_paid']}"
    print("✓ Registro de pago de Dark y descuento de 10 puntos superado.")

    print("\n🎉 ¡TODAS LAS PRUEBAS PASARON EXITOSAMENTE CON 100% DE EFICACIA!")

if __name__ == "__main__":
    run_tests()
