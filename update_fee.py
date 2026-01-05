#!/usr/bin/env python3
"""
Script zum manuellen Update der Fee-Informationen in der Freqtrade Datenbank.

Dieses Skript aktualisiert geschlossene Trades mit fehlenden Fee-Informationen.
Verwendet die Fee-Werte aus der Strategie BtcFreqaiPassiveStrategyV3:
- fee_open: 0.0002 (0.02%)
- fee_close: 0.0005 (0.05%)
"""

import sqlite3
import sys
import argparse
from pathlib import Path

# Fee-Werte aus der Strategie BtcFreqaiPassiveStrategyV3
FEE_OPEN_RATE = 0.0002  # 0.02%
FEE_CLOSE_RATE = 0.0005  # 0.05%
FEE_CURRENCY = "USDT"


def check_table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    """Prüft, ob eine Tabelle in der Datenbank existiert."""
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name=?
    """, (table_name,))
    return cursor.fetchone() is not None


def update_fee_in_database(db_path: Path) -> None:
    """Aktualisiert die Fee-Informationen in der Datenbank."""
    
    if not db_path.exists():
        print(f"Fehler: Datenbankdatei nicht gefunden: {db_path}")
        sys.exit(1)
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Prüfen, ob die Tabelle 'trades' existiert
        if not check_table_exists(cursor, "trades"):
            print(f"Fehler: Tabelle 'trades' existiert nicht in der Datenbank.")
            print("Verfügbare Tabellen:")
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            for table in tables:
                print(f"  - {table[0]}")
            conn.close()
            sys.exit(1)
        
        print("✓ Tabelle 'trades' gefunden.\n")
        
        # 1. Update für geschlossene Trades mit fehlender fee_close_currency
        cursor.execute("""
            SELECT COUNT(*) 
            FROM trades 
            WHERE is_open=0 
            AND (fee_close_currency IS NULL OR fee_close_currency='')
        """)
        count_close = cursor.fetchone()[0]
        
        print(f"Geschlossene Trades mit fehlender fee_close_currency: {count_close}")
        
        rows_affected_close = 0
        if count_close > 0:
            # Berechne fee_close_cost basierend auf fee_close Rate, falls nicht vorhanden
            cursor.execute("""
                UPDATE trades 
                SET fee_close_currency = ?,
                    fee_close = COALESCE(fee_close, ?),
                    fee_close_cost = CASE 
                        WHEN fee_close_cost IS NULL OR fee_close_cost = 0.0 
                        THEN (amount * close_rate * ?)
                        ELSE fee_close_cost 
                    END
                WHERE is_open=0 
                AND (fee_close_currency IS NULL OR fee_close_currency='')
            """, (FEE_CURRENCY, FEE_CLOSE_RATE, FEE_CLOSE_RATE))
            
            rows_affected_close = cursor.rowcount
            print(f"✓ {rows_affected_close} geschlossene Trades aktualisiert (fee_close).")
        
        # 2. Update für offene Trades mit fehlender fee_open_currency
        cursor.execute("""
            SELECT COUNT(*) 
            FROM trades 
            WHERE is_open=1 
            AND (fee_open_currency IS NULL OR fee_open_currency='')
        """)
        count_open = cursor.fetchone()[0]
        
        print(f"Offene Trades mit fehlender fee_open_currency: {count_open}")
        
        rows_affected_open = 0
        if count_open > 0:
            cursor.execute("""
                UPDATE trades 
                SET fee_open_currency = ?,
                    fee_open = COALESCE(fee_open, ?),
                    fee_open_cost = CASE 
                        WHEN fee_open_cost IS NULL OR fee_open_cost = 0.0 
                        THEN (amount * open_rate * ?)
                        ELSE fee_open_cost 
                    END
                WHERE is_open=1 
                AND (fee_open_currency IS NULL OR fee_open_currency='')
            """, (FEE_CURRENCY, FEE_OPEN_RATE, FEE_OPEN_RATE))
            
            rows_affected_open = cursor.rowcount
            print(f"✓ {rows_affected_open} offene Trades aktualisiert (fee_open).")
        
        # 3. Update für geschlossene Trades mit fehlender fee_open_currency (rückwirkend)
        cursor.execute("""
            SELECT COUNT(*) 
            FROM trades 
            WHERE is_open=0 
            AND (fee_open_currency IS NULL OR fee_open_currency='')
        """)
        count_open_closed = cursor.fetchone()[0]
        
        print(f"Geschlossene Trades mit fehlender fee_open_currency: {count_open_closed}")
        
        rows_affected_open_closed = 0
        if count_open_closed > 0:
            cursor.execute("""
                UPDATE trades 
                SET fee_open_currency = ?,
                    fee_open = COALESCE(fee_open, ?),
                    fee_open_cost = CASE 
                        WHEN fee_open_cost IS NULL OR fee_open_cost = 0.0 
                        THEN (amount * open_rate * ?)
                        ELSE fee_open_cost 
                    END
                WHERE is_open=0 
                AND (fee_open_currency IS NULL OR fee_open_currency='')
            """, (FEE_CURRENCY, FEE_OPEN_RATE, FEE_OPEN_RATE))
            
            rows_affected_open_closed = cursor.rowcount
            print(f"✓ {rows_affected_open_closed} geschlossene Trades aktualisiert (fee_open rückwirkend).")
        
        conn.commit()
        conn.close()
        
        total_updated = (rows_affected_close if count_close > 0 else 0) + \
                       (rows_affected_open if count_open > 0 else 0) + \
                       (rows_affected_open_closed if count_open_closed > 0 else 0)
        
        if total_updated == 0:
            print("\n✓ Keine Trades gefunden, die aktualisiert werden müssen.")
        else:
            print(f"\n✓ Update abgeschlossen. Insgesamt wurden {total_updated} Trades aktualisiert.")
        
    except sqlite3.Error as e:
        print(f"Fehler beim Datenbankzugriff: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unerwarteter Fehler: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    """Hauptfunktion."""
    parser = argparse.ArgumentParser(
        description="Aktualisiert Fee-Informationen (fee_open/fee_close + currency/cost) in einer Freqtrade SQLite DB."
    )
    parser.add_argument(
        "--db",
        dest="db",
        help="Voller Pfad zur SQLite Datenbank.",
    )
    parser.add_argument(
        "--db-name",
        dest="db_name",
        help="DB-Dateiname (z.B. tradesv3.sqlite). Wird mit --db-dir (oder aktuellem Ordner) kombiniert.",
    )
    parser.add_argument(
        "--db-dir",
        dest="db_dir",
        help="Verzeichnis, in dem --db-name gesucht wird. Default: aktuelles Working Directory.",
    )
    args = parser.parse_args()

    if args.db and args.db_name:
        print("Fehler: Bitte entweder --db ODER --db-name verwenden (nicht beides).")
        sys.exit(2)

    if args.db:
        db_path = Path(args.db).expanduser().resolve()
    elif args.db_name:
        base_dir = Path(args.db_dir).expanduser().resolve() if args.db_dir else Path.cwd()
        db_path = (base_dir / args.db_name).resolve()
    else:
        print("Fehler: Bitte eine Datenbank angeben: --db /pfad/zur/db.sqlite oder --db-name tradesv3.sqlite [--db-dir /pfad].")
        sys.exit(2)
    
    print(f"Datenbankpfad: {db_path}")
    print(f"Fee-Werte: Open={FEE_OPEN_RATE*100:.2f}%, Close={FEE_CLOSE_RATE*100:.2f}%")
    print("Aktualisiere Fee-Informationen in der Datenbank...\n")
    
    update_fee_in_database(db_path)


if __name__ == '__main__':
    main()