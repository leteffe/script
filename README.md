# scripts

Persönliche Sammlung kleiner Helfer-Scripts.

> Hinweis: **KI-generiert** (README / Teile des Codes).

## Struktur

- **`script/update_fee.py`**: Fee-Felder in Freqtrade SQLite DB updaten
- **`script/auto_push.sh`**: schneller Git commit/push helper

## Usage

DB per Pfad:

```bash
python3 script/update_fee.py --db /pfad/zur/tradesv3.sqlite
```

DB per Name (optional mit Ordner):

```bash
python3 script/update_fee.py --db-name tradesv3.sqlite
python3 script/update_fee.py --db-name tradesv3.sqlite --db-dir /pfad/zum/user_data
```
