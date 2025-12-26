import time
import json
from data_collecting import build_all_matches_once
from arb_bot import find_arbitrage
from ev_calc import calculate_ev
from alerts import notify   # <-- LISÄTTY
from no_vig_calc import compute_fair_and_no_vig
from db_managert import save_to_database
from closing_odds import collect_closing_odds_and_eval_ev
import sys
sys.stdout.reconfigure(line_buffering=True)



REFRESH_INTERVAL = 600  # sekuntia


# ---------------------------------------------------------
#   TULOSTA VAIN PINNACLEN KERTOIMET
# ---------------------------------------------------------

# ---------------------------------------------------------
def main():
    print("Botti käynnistyy. Paina Ctrl+C lopettaaksesi.")

    iteration = 1
    while True:
        print(f"\n========== KIERROS {iteration} ==========\n")

        try:
            # --- 1. HAE KERTOIMET ---
            print("📌 Päivitetään kertoimet...")
            all_matches = build_all_matches_once()
            no_vig_data = compute_fair_and_no_vig(all_matches)

            print(f"➡️ Otteluita ladattu: {len(all_matches)}")

            # Halutessasi tulosta Pinnaclen kertoimet:
            # print_pinnacle_odds(all_matches)

            # --- 2. ARBITRAASIT ---
            print("\n📌 Lasketaan arbitraasit (>1% ROI)...")
            arbs = find_arbitrage(all_matches)
            print("DEBUG arbs type:", type(arbs))
            print("DEBUG arbs[0] type:", type(arbs[0]))


            if not arbs:
                print("❌ Ei arbitraaseja tällä kierroksella.")
            else:
                print(f"✅ Löytyi {len(arbs)} arbitraasia:\n")
                for a in arbs:
                    print(f"{a['match']} ({a['sport']})")
                    print(f"  Markkina: {a['market']}")
                    print(f"  ROI: {a['roi']:.2f}%")
                    print(f"  Profit: {a['profit']:.2f}€")
                    print(f"  Kokonaispanos: {a['total_stake']}€\n")

                    print("  Panosjako:")
                    for outcome, stake in a["stakes"].items():
                        book = a["best_odds"][outcome]["book"]
                        odds = a["best_odds"][outcome]["odds"]

                        print(f"    - {outcome:<8} → Panos: {stake:.2f}€  |  {book} @ {odds}")

                    print()  # tyhjä rivi

            # --- 3. +EV-VELOT ---
            print("\n📌 Lasketaan +EV-vedot (>1% EV)...")
            evs= calculate_ev(all_matches, no_vig_data, min_ev_percent=2.0)

            if not evs:
                print("❌ Ei +EV kohteita tällä kierroksella.")
            else:
                print(f"✅ Löytyi {len(evs)} +EV kohdetta:\n")
                for ev in evs:
                    print(f"{ev['match']} ({ev['sport']})")
                    print(f"  Markkina: {ev['market']}")
                    print(f"  Bookkeri: {ev['book']}")
                    print(f"  Referenssi: {ev['reference_book']}")
                    print(f"  Tarjottu kerroin: {ev['offered_odds']}")
                    print(f"  Tod.näk (fair, no-vig): {ev['probability']:.3f}")
                    print(f"  EV: {ev['ev_percent']:.2f}%")
                    print(f"  Kohde: {ev['outcome']}\n")

            # --- 4. TALLENNA DATABASEEN ---
            try:
           

                save_to_database(
                    all_matches,
                    no_vig_data,
                    evs,
                    high_evs,
                    arbs
                )
                collect_closing_odds_and_eval_ev()

                print("💾 Tallennus tehty.")
            except Exception as e:
                print(f"⚠️ Tallennus epäonnistui: {e}")

            # --- 5. TALLENNA DUMP JSONIIN (debugiin) ---
            try:
                with open("all_matches_dump.json", "w", encoding="utf-8") as f:
                    json.dump(all_matches, f, indent=2, ensure_ascii=False)
                print("💾 all_matches_dump.json tallennettu!")
            except Exception as e:
                print(f"⚠️ JSON-dumpin tallennus epäonnistui: {e}")

            # --- 6. Lähetä TELEGRAM + EMAIL -ILMOITUKSET ---
            try:
                notify(evs, arbs)
                print("📨 Ilmoitukset lähetetty (vain uudet kohteet).")
            except Exception as e:
                print(f"⚠️ Ilmoitusten lähetys epäonnistui: {e}")

        except Exception as e:
            print(f"❌ Virhe kierroksella: {e}")

        # --- 7. SLEEP ---
        print(f"⏳ Odotetaan {REFRESH_INTERVAL} sekuntia...\n")
        try:
            time.sleep(REFRESH_INTERVAL)
        except KeyboardInterrupt:
            break

        iteration += 1


if __name__ == "__main__":
    main()
