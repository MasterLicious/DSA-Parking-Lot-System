"""
database.py
-----------
This file is the DATABASE MODULE for ParkPesa.

Why SQLite?
  - It is a real relational (SQL) database, not a text file, so it gives us
    proper tables, keys and fast queries (needed for reporting/VAT later).
  - It ships built into Python (no server to install) so it is perfect for a
    student project that must "just run" on the marker's laptop.
  - It is file-based (parkpesa.db), so it is easy to put on GitHub and easy
    to reset for a demo.

Why is this called a "DYNAMIC" database (see Task One, part c)?
  - Nothing about prices, zones or slot counts is hard-coded in the Python
    logic. They all live as ROWS in tables (rates, slots, settings).
  - Management can change a parking fee, add a new slot/zone, or change the
    VAT percentage by editing rows in the `rates`, `slots` or `settings`
    tables (e.g. through the /admin/rates page) -- WITHOUT touching or
    redeploying any code. That satisfies the client requirement:
    "Let management change parking rates at any time without a software
    change."

Tables (see README.md for the full ER diagram and explanation):
  slots        -> every physical parking bay and its live status
  sessions     -> one row per vehicle visit (entry -> exit)
  rates        -> fee tiers, editable by admin, this IS the pricing engine
  payments     -> one row per payment attempt (audit trail for reconciliation/VAT)
  settings     -> single-row key/value table for things like VAT %
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "parkpesa.db")


def get_connection():
    """Open a connection to the database.

    Data structure note: sqlite3.Row lets us access columns by NAME
    (row["plate_number"]) instead of by position (row[2]), which makes the
    rest of the code far easier to read and less error-prone.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(reset=False):
    """Create all tables and seed starter data.

    reset=True wipes the database file first -- handy when demoing so you
    always start from a clean set of bays.
    """
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = get_connection()
    cur = conn.cursor()

    # --- slots: the physical bays -------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            slot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_code   TEXT UNIQUE NOT NULL,   -- e.g. "A1"
            zone        TEXT NOT NULL,          -- e.g. "A"
            status      TEXT NOT NULL DEFAULT 'available'  -- available | occupied
        )
    """)

    # --- sessions: one row per vehicle visit ---------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_number    TEXT NOT NULL,
            slot_id         INTEGER NOT NULL,
            entry_time      TEXT NOT NULL,       -- ISO timestamp
            exit_time       TEXT,                -- NULL until vehicle exits
            duration_min    INTEGER,             -- filled in at exit
            amount_due      REAL,                -- filled in at exit
            status          TEXT NOT NULL DEFAULT 'PARKED', -- PARKED|AWAITING_PAYMENT|PAID
            FOREIGN KEY (slot_id) REFERENCES slots(slot_id)
        )
    """)

    # --- rates: the DYNAMIC pricing table -------------------------------
    # Management edits THESE ROWS (via /admin/rates) to change fees.
    # up_to_minutes = -1 means "no upper bound" (over six hours tier).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rates (
            rate_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            label          TEXT NOT NULL,
            up_to_minutes  INTEGER NOT NULL,
            amount         REAL NOT NULL,
            sort_order     INTEGER NOT NULL
        )
    """)

    # --- payments: audit trail for every payment attempt ----------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            payment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   INTEGER NOT NULL,
            amount       REAL NOT NULL,
            method       TEXT NOT NULL,      -- MPESA | CARD | CASH
            reference    TEXT NOT NULL,      -- simulated M-Pesa code / receipt no.
            paid_at      TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)

    # --- settings: single-row key/value store (e.g. VAT %) --------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key    TEXT PRIMARY KEY,
            value  TEXT NOT NULL
        )
    """)

    conn.commit()

    # Seed slots only if empty (3 zones x 10 bays = 30 bays demo lot)
    if cur.execute("SELECT COUNT(*) FROM slots").fetchone()[0] == 0:
        for zone in ("A", "B", "C"):
            for n in range(1, 11):
                cur.execute(
                    "INSERT INTO slots (slot_code, zone, status) VALUES (?, ?, 'available')",
                    (f"{zone}{n}", zone),
                )

    # Seed rates from the client's Task Two tariff card, if empty
    if cur.execute("SELECT COUNT(*) FROM rates").fetchone()[0] == 0:
        seed_rates = [
            ("First 30 minutes",  30,  0.0,   1),
            ("Up to 2 hours",     120, 50.0,  2),
            ("Up to 4 hours",     240, 100.0, 3),
            ("Up to 6 hours",     360, 300.0, 4),
            ("Over 6 hours",      -1,  500.0, 5),
        ]
        cur.executemany(
            "INSERT INTO rates (label, up_to_minutes, amount, sort_order) VALUES (?, ?, ?, ?)",
            seed_rates,
        )

    if cur.execute("SELECT COUNT(*) FROM settings WHERE key='vat_percent'").fetchone()[0] == 0:
        cur.execute("INSERT INTO settings (key, value) VALUES ('vat_percent', '16')")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db(reset=True)
    print(f"Database created at {DB_PATH}")
