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
    # Pfad zur Datenbank - basierend auf docker-compose.yml
    script_dir = Path(__file__).parent
    db_path = script_dir / "tradesv3_v3_prod_20251221.sqlite"
    
    # Falls nicht gefunden, versuche alternative Pfade
    if not db_path.exists():
        db_path = script_dir / "tradesv3.sqlite"
    
    if not db_path.exists():
        project_root = script_dir.parent.parent
        db_path = project_root / "ft_userdata" / "user_data" / "tradesv3_v3_prod_20251221.sqlite"
    
    if not db_path.exists():
        db_path = project_root / "ft_userdata" / "user_data" / "tradesv3.sqlite"
    
    print(f"Datenbankpfad: {db_path}")
    print(f"Fee-Werte: Open={FEE_OPEN_RATE*100:.2f}%, Close={FEE_CLOSE_RATE*100:.2f}%")
    print("Aktualisiere Fee-Informationen in der Datenbank...\n")
    
    update_fee_in_database(db_path)


if __name__ == '__main__':
    main()
