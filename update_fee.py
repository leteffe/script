#!/usr/bin/env python3
"""
freqtrade_fee_fix.py

Update missing fee fields in a Freqtrade SQLite database (table: trades).

What it does
- Updates fee_open_* for open trades (is_open = 1) when missing
- Updates fee_open_* for closed trades (is_open = 0) when missing (retroactive)
- Updates fee_close_* for closed trades (is_open = 0) when missing

Defaults (can be overridden via CLI):
- fee_open  = 0.0002  (0.02%)
- fee_close = 0.0005  (0.05%)
- currency  = USDT

Safety
- Creates a timestamped backup by default
- Supports --dry-run (no changes)
- Asks for confirmation unless --yes
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class FeeConfig:
    fee_open: float
    fee_close: float
    currency: str


REQUIRED_COLUMNS = {
    # base columns
    "is_open",
    "amount",
    "open_rate",
    "close_rate",
    # fee columns
    "fee_open_currency",
    "fee_open",
    "fee_open_cost",
    "fee_close_currency",
    "fee_close",
    "fee_close_cost",
}


def log(msg: str) -> None:
    print(msg)


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fix missing fee_* fields in a Freqtrade SQLite DB (trades table)."
    )
    p.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to the SQLite database file (e.g. user_data/tradesv3.sqlite).",
    )
    p.add_argument("--fee-open", type=float, default=0.0002, help="Fee open rate (default: 0.0002).")
    p.add_argument("--fee-close", type=float, default=0.0005, help="Fee close rate (default: 0.0005).")
    p.add_argument("--currency", type=str, default="USDT", help="Fee currency code (default: USDT).")

    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show what would be updated, do not modify the DB.",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Do not ask for confirmation.",
    )
    p.add_argument(
        "--no-backup",
        action="store_true",
        help="Disable automatic DB backup before modifying.",
    )
    p.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Where to store the backup (default: alongside the DB).",
    )

    p.add_argument(
        "--only",
        choices=["all", "open", "close", "open-closed"],
        default="all",
        help="Which updates to run: "
             "all (default), open (open trades fee_open), close (closed trades fee_close), "
             "open-closed (closed trades fee_open retroactive).",
    )

    return p.parse_args(argv)


def guess_default_db_paths() -> list[Path]:
    """
    Common locations in Freqtrade setups.
    We keep this minimal and generic so it's public-friendly.
    """
    candidates = [
        Path.cwd() / "tradesv3.sqlite",
        Path.cwd() / "user_data" / "tradesv3.sqlite",
        Path.cwd() / "ft_userdata" / "user_data" / "tradesv3.sqlite",
        Path.cwd() / "data" / "tradesv3.sqlite",
    ]
    return candidates


def resolve_db_path(cli_db: Path | None) -> Path:
    if cli_db is not None:
        return cli_db

    for p in guess_default_db_paths():
        if p.exists():
            return p

    eprint("[ERR] No database path provided and no default DB found.")
    eprint("Provide --db /path/to/tradesv3.sqlite")
    eprint("Tried:")
    for p in guess_default_db_paths():
        eprint(f"  - {p}")
    sys.exit(2)


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        eprint(f"[ERR] Database file not found: {db_path}")
        sys.exit(2)

    # Use autocommit OFF (default) and explicit commit/rollback
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def get_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cur.fetchall()}


def require_schema(cur: sqlite3.Cursor) -> None:
    if not table_exists(cur, "trades"):
        eprint("[ERR] Table 'trades' not found in this database.")
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        eprint("Available tables:")
        for t in tables:
            eprint(f"  - {t}")
        sys.exit(2)

    cols = get_columns(cur, "trades")
    missing = sorted(REQUIRED_COLUMNS - cols)
    if missing:
        eprint("[ERR] The 'trades' table is missing required columns:")
        for c in missing:
            eprint(f"  - {c}")
        eprint("This script expects a Freqtrade schema that contains these columns.")
        sys.exit(2)


def backup_db(db_path: Path, backup_dir: Path | None) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = backup_dir if backup_dir is not None else db_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    backup_path = target_dir / f"{db_path.name}.backup_{ts}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def count_rows(cur: sqlite3.Cursor, sql: str, params: tuple) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int(row[0]) if row else 0


def confirm_or_exit(question: str, yes: bool) -> None:
    if yes or not sys.stdin.isatty():
        return
    print(question, end=" ")
    ans = input().strip().lower()
    if ans not in {"y", "yes", "j", "ja"}:
        log("Aborted.")
        sys.exit(0)


def run_updates(conn: sqlite3.Connection, fee: FeeConfig, mode: str, dry_run: bool) -> dict[str, int]:
    """
    Returns dict with affected row counts per update category.
    """
    cur = conn.cursor()

    results: dict[str, int] = {
        "close_fee_closed_trades": 0,
        "open_fee_open_trades": 0,
        "open_fee_closed_trades": 0,
    }

    # Define "missing" as: currency missing OR fee missing OR cost missing/0
    # We treat empty string currency as missing too.

    # 1) Closed trades: fix fee_close_*
    if mode in {"all", "close"}:
        to_fix_close = count_rows(
            cur,
            """
            SELECT COUNT(*)
            FROM trades
            WHERE is_open=0
              AND (
                    fee_close_currency IS NULL OR fee_close_currency=''
                 OR fee_close IS NULL
                 OR fee_close_cost IS NULL OR fee_close_cost=0.0
              )
            """,
            (),
        )
        log(f"Closed trades needing fee_close fix: {to_fix_close}")

        if not dry_run and to_fix_close > 0:
            cur.execute(
                """
                UPDATE trades
                SET
                    fee_close_currency = COALESCE(NULLIF(fee_close_currency, ''), ?),
                    fee_close          = COALESCE(fee_close, ?),
                    fee_close_cost     = CASE
                        WHEN fee_close_cost IS NULL OR fee_close_cost=0.0
                        THEN (amount * close_rate * ?)
                        ELSE fee_close_cost
                    END
                WHERE is_open=0
                  AND (
                        fee_close_currency IS NULL OR fee_close_currency=''
                     OR fee_close IS NULL
                     OR fee_close_cost IS NULL OR fee_close_cost=0.0
                  )
                """,
                (fee.currency, fee.fee_close, fee.fee_close),
            )
            results["close_fee_closed_trades"] = cur.rowcount
            log(f"✓ Updated closed trades (fee_close): {cur.rowcount}")

    # 2) Open trades: fix fee_open_*
    if mode in {"all", "open"}:
        to_fix_open = count_rows(
            cur,
            """
            SELECT COUNT(*)
            FROM trades
            WHERE is_open=1
              AND (
                    fee_open_currency IS NULL OR fee_open_currency=''
                 OR fee_open IS NULL
                 OR fee_open_cost IS NULL OR fee_open_cost=0.0
              )
            """,
            (),
        )
        log(f"Open trades needing fee_open fix: {to_fix_open}")

        if not dry_run and to_fix_open > 0:
            cur.execute(
                """
                UPDATE trades
                SET
                    fee_open_currency = COALESCE(NULLIF(fee_open_currency, ''), ?),
                    fee_open          = COALESCE(fee_open, ?),
                    fee_open_cost     = CASE
                        WHEN fee_open_cost IS NULL OR fee_open_cost=0.0
                        THEN (amount * open_rate * ?)
                        ELSE fee_open_cost
                    END
                WHERE is_open=1
                  AND (
                        fee_open_currency IS NULL OR fee_open_currency=''
                     OR fee_open IS NULL
                     OR fee_open_cost IS NULL OR fee_open_cost=0.0
                  )
                """,
                (fee.currency, fee.fee_open, fee.fee_open),
            )
            results["open_fee_open_trades"] = cur.rowcount
            log(f"✓ Updated open trades (fee_open): {cur.rowcount}")

    # 3) Closed trades: retroactively fix fee_open_*
    if mode in {"all", "open-closed"}:
        to_fix_open_closed = count_rows(
            cur,
            """
            SELECT COUNT(*)
            FROM trades
            WHERE is_open=0
              AND (
                    fee_open_currency IS NULL OR fee_open_currency=''
                 OR fee_open IS NULL
                 OR fee_open_cost IS NULL OR fee_open_cost=0.0
              )
            """,
            (),
        )
        log(f"Closed trades needing fee_open retro-fix: {to_fix_open_closed}")

        if not dry_run and to_fix_open_closed > 0:
            cur.execute(
                """
                UPDATE trades
                SET
                    fee_open_currency = COALESCE(NULLIF(fee_open_currency, ''), ?),
                    fee_open          = COALESCE(fee_open, ?),
                    fee_open_cost     = CASE
                        WHEN fee_open_cost IS NULL OR fee_open_cost=0.0
                        THEN (amount * open_rate * ?)
                        ELSE fee_open_cost
                    END
                WHERE is_open=0
                  AND (
                        fee_open_currency IS NULL OR fee_open_currency=''
                     OR fee_open IS NULL
                     OR fee_open_cost IS NULL OR fee_open_cost=0.0
                  )
                """,
                (fee.currency, fee.fee_open, fee.fee_open),
            )
            results["open_fee_closed_trades"] = cur.rowcount
            log(f"✓ Updated closed trades (fee_open retro): {cur.rowcount}")

    return results


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    db_path = resolve_db_path(args.db)

    fee = FeeConfig(
        fee_open=args.fee_open,
        fee_close=args.fee_close,
        currency=args.currency.strip(),
    )

    log(f"DB: {db_path}")
    log(f"Fee config: open={fee.fee_open*100:.4f}% close={fee.fee_close*100:.4f}% currency={fee.currency}")
    log(f"Mode: {args.only} | dry-run: {args.dry_run} | backup: {not args.no_backup}")

    conn = connect(db_path)
    try:
        require_schema(conn.cursor())

        # Always show counts first (even in dry-run)
        log("")
        log("Scanning database...")
        # Run in dry-run mode just for counts? We already count inside run_updates.
        # We will do a "counts pass" by calling run_updates with dry_run=True first.
        run_updates(conn, fee, args.only, dry_run=True)

        if args.dry_run:
            log("\nDry-run: no changes applied.")
            return 0

        confirm_or_exit("\nApply these changes to the database? [y/N]", yes=args.yes)

        if not args.no_backup:
            backup_path = backup_db(db_path, args.backup_dir)
            log(f"✓ Backup created: {backup_path}")

        # Apply updates within a transaction
        try:
            results = run_updates(conn, fee, args.only, dry_run=False)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        total = sum(results.values())
        log("")
        if total == 0:
            log("✓ Nothing to update.")
        else:
            log(f"✓ Done. Updated rows total: {total}")
            for k, v in results.items():
                log(f"  - {k}: {v}")

        return 0

    except sqlite3.Error as e:
        eprint(f"[ERR] SQLite error: {e}")
        return 1
    except Exception as e:
        eprint(f"[ERR] Unexpected error: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
