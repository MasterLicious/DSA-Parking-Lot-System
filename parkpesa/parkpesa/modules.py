"""
modules.py
----------
This file holds the ALGORITHMS + DATA STRUCTURES for every module identified
from the client's terms of reference (see README.md section 2 for the full
write-up). Each class below is one module. Each method's docstring states
the algorithm in plain steps and its Big-O complexity, as required by
Task One parts (a) and (b).

Overview of the 8 modules implemented here:
  1. SlotDisplayModule      -> live availability for the entrance board / web view
  2. SlotAllocationModule   -> assigns a bay to an arriving vehicle
  3. VehicleEntryModule     -> records plate, time-in, allocated bay
  4. FeeCalculationModule   -> computes duration + amount payable at exit
  5. PaymentModule          -> collects payment (M-Pesa/card/cash)
  6. BarrierControlModule   -> opens the barrier only on confirmed payment
  7. RateManagementModule   -> lets admin change fees without touching code
  8. ReportingModule        -> auditable record of shillings collected + VAT
"""

from collections import deque
from datetime import datetime
import random
import string

from database import get_connection


# ---------------------------------------------------------------------------
# MODULE 2 & 1: Slot Allocation + Slot Display
# ---------------------------------------------------------------------------
class SlotAllocationModule:
    """
    Data structure used: a QUEUE (collections.deque) of available slot ids.

    Why a queue and not just "scan the table every time"?
      - Allocating a bay = pop from the LEFT of the queue  -> O(1)
      - Freeing a bay (on exit) = push to the RIGHT of the queue -> O(1)
      - This guarantees FIFO fairness (bays are reused in a predictable
        rotation rather than always slamming the same bay) and avoids
        re-scanning all N slots on every single arrival, which would be
        O(n) per car and get slow as the car park grows.
      - The queue is rebuilt from the database on startup, so the database
        table `slots` remains the single source of truth; the queue is just
        a fast in-memory index on top of it.

    Algorithm - allocate_slot(plate_number):
        1. If the queue of available slot ids is empty -> return None
           (lot full; caller shows "Lot Full" to the driver).
        2. Pop a slot_id from the front of the queue.            O(1)
        3. Mark that slot as 'occupied' in the `slots` table.    O(1) (indexed PK)
        4. Return the slot's details.
    Algorithm - release_slot(slot_id):
        1. Mark the slot as 'available' in the `slots` table.
        2. Push slot_id onto the back of the queue.               O(1)
    """

    def __init__(self):
        self._available_queue = deque()
        self._load_from_db()

    def _load_from_db(self):
        conn = get_connection()
        rows = conn.execute(
            "SELECT slot_id FROM slots WHERE status='available' ORDER BY slot_id"
        ).fetchall()
        conn.close()
        self._available_queue = deque(r["slot_id"] for r in rows)

    def allocate_slot(self):
        """Return the allocated slot row, or None if the lot is full."""
        if not self._available_queue:
            self._load_from_db()  # self-heal in case another process changed it
            if not self._available_queue:
                return None
        slot_id = self._available_queue.popleft()
        conn = get_connection()
        conn.execute("UPDATE slots SET status='occupied' WHERE slot_id=?", (slot_id,))
        conn.commit()
        slot = conn.execute("SELECT * FROM slots WHERE slot_id=?", (slot_id,)).fetchone()
        conn.close()
        return slot

    def release_slot(self, slot_id):
        conn = get_connection()
        conn.execute("UPDATE slots SET status='available' WHERE slot_id=?", (slot_id,))
        conn.commit()
        conn.close()
        self._available_queue.append(slot_id)


class SlotDisplayModule:
    """
    Module 1: live availability, for the entrance board and the web/mobile view.

    Algorithm - get_board():
        1. SELECT all slots grouped by zone.                 O(n), n = number of slots
        2. Count available vs occupied per zone.
        3. Return a simple list structure the template can render as a
           coloured board (green = free, red = occupied).

    Data structure used: a plain Python list of dicts. A car park's slot
    count is small (tens to low hundreds), so a full read each time the
    board refreshes is cheap and always 100% accurate (no stale cache).
    """

    def get_board(self):
        conn = get_connection()
        # Order by zone, then by the NUMERIC part of the bay code (cast as an
        # integer). Sorting slot_code as plain text would put "A10" before
        # "A2" (text comparison looks at the '1' before the '2'), so we
        # strip the single leading zone letter and sort what's left as a
        # number instead: A1, A2, ... A9, A10.
        rows = conn.execute("""
            SELECT * FROM slots
            ORDER BY zone, CAST(substr(slot_code, 2) AS INTEGER)
        """).fetchall()
        conn.close()
        board = [dict(r) for r in rows]
        total = len(board)
        free = sum(1 for s in board if s["status"] == "available")
        return {"slots": board, "total": total, "free": free, "occupied": total - free}


# ---------------------------------------------------------------------------
# MODULE 3: Vehicle Entry
# ---------------------------------------------------------------------------
class VehicleEntryModule:
    """
    Algorithm - record_arrival(plate_number):
        1. Normalise the plate (uppercase, strip spaces) so "kdd 123a" and
           "KDD123A" are treated as the same vehicle.
        2. Ask SlotAllocationModule for a free bay.   O(1)
        3. If none available -> raise LotFullError.
        4. INSERT a new row into `sessions` with entry_time = now().  O(1)
        5. Return the session (used to print/display an entry ticket).
    """

    def __init__(self, slot_module: SlotAllocationModule):
        self.slot_module = slot_module

    def record_arrival(self, plate_number):
        plate_number = plate_number.strip().upper().replace(" ", "")
        if not plate_number:
            raise ValueError("Number plate cannot be empty.")

        # Guard against the same vehicle being checked in twice while it is
        # still inside the lot (e.g. operator mistyping, or someone trying
        # to register a plate that never properly exited).
        conn = get_connection()
        existing = conn.execute(
            "SELECT s.session_id, sl.slot_code FROM sessions s "
            "JOIN slots sl ON s.slot_id = sl.slot_id "
            "WHERE s.plate_number=? AND s.status='PARKED'",
            (plate_number,),
        ).fetchone()
        conn.close()
        if existing is not None:
            raise DuplicateEntryError(
                f"{plate_number} is already parked in bay {existing['slot_code']}."
            )

        slot = self.slot_module.allocate_slot()
        if slot is None:
            raise LotFullError("No bays available. Please try again later.")

        entry_time = datetime.now().isoformat(timespec="seconds")
        conn = get_connection()
        cur = conn.execute(
            "INSERT INTO sessions (plate_number, slot_id, entry_time, status) "
            "VALUES (?, ?, ?, 'PARKED')",
            (plate_number, slot["slot_id"], entry_time),
        )
        conn.commit()
        session_id = cur.lastrowid
        session = conn.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        conn.close()
        return dict(session), dict(slot)


class LotFullError(Exception):
    """Raised when a vehicle arrives but no bay is free (exception handling)."""
    pass


class DuplicateEntryError(Exception):
    """Raised when a plate already has an active (PARKED) session."""
    pass


# ---------------------------------------------------------------------------
# MODULE 7: Rate Management (dynamic pricing, no code change needed)
# ---------------------------------------------------------------------------
class RateManagementModule:
    """
    Algorithm - get_rates(): return the tier list, sorted, from the DB.
    Algorithm - update_rate(rate_id, new_amount): UPDATE one row.
    This is what makes pricing "dynamic": the tiers are DATA, not constants
    in the Python code, so management edits them from /admin/rates.
    """

    def get_rates(self):
        conn = get_connection()
        rows = conn.execute("SELECT * FROM rates ORDER BY sort_order").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_rate(self, rate_id, new_amount):
        conn = get_connection()
        conn.execute("UPDATE rates SET amount=? WHERE rate_id=?", (float(new_amount), rate_id))
        conn.commit()
        conn.close()

    def get_vat_percent(self):
        conn = get_connection()
        row = conn.execute("SELECT value FROM settings WHERE key='vat_percent'").fetchone()
        conn.close()
        return float(row["value"]) if row else 0.0

    def set_vat_percent(self, value):
        conn = get_connection()
        conn.execute("UPDATE settings SET value=? WHERE key='vat_percent'", (str(float(value)),))
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# MODULE 4: Duration & Fee Calculation
# ---------------------------------------------------------------------------
class FeeCalculationModule:
    """
    Algorithm - calculate(entry_time, exit_time, rate_tiers):
        1. duration_minutes = exit_time - entry_time                O(1)
        2. Walk the rate tiers IN ORDER (they are pre-sorted by
           up_to_minutes ascending, with the last tier's up_to_minutes = -1
           meaning "no limit"). The first tier whose ceiling covers the
           duration is the one that applies.                        O(k)
           (k = number of tiers, a small constant ~5, so effectively O(1))
        3. Return (duration_minutes, amount_due, matched_tier_label).

    Data structure used: a small ORDERED LIST of tier dicts pulled fresh
    from the `rates` table on every calculation, so a rate change by the
    admin takes effect immediately on the very next car that exits.
    """

    def __init__(self, rate_module: RateManagementModule):
        self.rate_module = rate_module

    def calculate(self, entry_time_iso, exit_time_dt=None):
        entry_time = datetime.fromisoformat(entry_time_iso)
        exit_time = exit_time_dt or datetime.now()
        duration_minutes = max(0, int((exit_time - entry_time).total_seconds() // 60))

        tiers = self.rate_module.get_rates()
        for tier in tiers:
            if tier["up_to_minutes"] == -1 or duration_minutes <= tier["up_to_minutes"]:
                return duration_minutes, tier["amount"], tier["label"]

        # Should not happen because the last seeded tier has up_to_minutes = -1,
        # but fail safe to the most expensive tier rather than charging nothing.
        last = tiers[-1]
        return duration_minutes, last["amount"], last["label"]


# ---------------------------------------------------------------------------
# MODULE 5 & 6: Payment + Barrier Control
# ---------------------------------------------------------------------------
class PaymentModule:
    """
    Algorithm - take_payment(session_id, amount, method):
        1. Generate a reference number (simulated M-Pesa code / receipt no).
        2. INSERT a row into `payments` (the permanent audit trail).
        3. UPDATE the session: status='PAID', store duration & amount.
        4. Return success -> caller (BarrierControlModule) opens the barrier.

    In a real deployment, step 1 would instead call the Safaricom M-Pesa
    Daraja STK Push API and only proceed to step 2/3 once Safaricom's
    callback confirms payment. That network call is simulated here so the
    system is demonstrable offline, but the module boundary is drawn
    exactly where the real API call would go.
    """

    def _generate_reference(self, method):
        prefix = {"MPESA": "MPESA", "CARD": "CARD", "CASH": "CASH"}.get(method, "PAY")
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        return f"{prefix}-{suffix}"

    def take_payment(self, session_id, amount, method, duration_minutes):
        reference = self._generate_reference(method)
        paid_at = datetime.now().isoformat(timespec="seconds")

        conn = get_connection()
        conn.execute(
            "INSERT INTO payments (session_id, amount, method, reference, paid_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, amount, method, reference, paid_at),
        )
        conn.execute(
            "UPDATE sessions SET status='PAID', exit_time=?, duration_min=?, amount_due=? "
            "WHERE session_id=?",
            (paid_at, duration_minutes, amount, session_id),
        )
        conn.commit()
        conn.close()
        return reference


class BarrierControlModule:
    """
    Algorithm - open_if_paid(session):
        1. Check session['status'] == 'PAID'.
        2. If yes -> release the bay back to the availability queue and
           return True ("barrier open" signal for the UI/hardware relay).
        3. If no -> return False (barrier stays shut). This is the rule
           straight from the brief: "open the barrier only on confirmed
           payment".
    """

    def __init__(self, slot_module: SlotAllocationModule):
        self.slot_module = slot_module

    def open_if_paid(self, session):
        if session["status"] != "PAID":
            return False
        self.slot_module.release_slot(session["slot_id"])
        return True


# ---------------------------------------------------------------------------
# MODULE 8: Reporting / Audit (for reconciliation and VAT)
# ---------------------------------------------------------------------------
class ReportingModule:
    """
    Algorithm - daily_summary():
        1. SUM(amount) from `payments`, GROUP BY method and GROUP BY date.  (SQL does the O(n) scan)
        2. Compute VAT: since prices are VAT-inclusive, VAT = amount * rate/(100+rate).
        3. Return totals + the full itemised list for auditors.
    Every row in `payments` is immutable (never edited or deleted from the
    UI) so the table itself IS the audit trail required by the brief:
    "Produce an auditable record of every shilling collected."
    """

    def __init__(self, rate_module: RateManagementModule):
        self.rate_module = rate_module

    def currently_parked(self):
        """
        Module 8 (extended): list every vehicle currently inside the lot.

        Algorithm:
            1. SELECT sessions with status='PARKED', joined to slots for
               the bay code/zone.                                O(n) rows currently parked
            2. For each row, compute "minutes so far" = now - entry_time,
               purely for display (does not affect the stored fee, which
               is only calculated for real at the exit gate).
        """
        conn = get_connection()
        rows = conn.execute("""
            SELECT s.session_id, s.plate_number, s.entry_time,
                   sl.slot_code, sl.zone
            FROM sessions s JOIN slots sl ON s.slot_id = sl.slot_id
            WHERE s.status = 'PARKED'
            ORDER BY s.entry_time ASC
        """).fetchall()
        conn.close()

        result = []
        now = datetime.now()
        for r in rows:
            entry_time = datetime.fromisoformat(r["entry_time"])
            minutes_so_far = int((now - entry_time).total_seconds() // 60)
            result.append({
                "session_id": r["session_id"],
                "plate_number": r["plate_number"],
                "slot_code": r["slot_code"],
                "zone": r["zone"],
                "entry_time": r["entry_time"],
                "minutes_so_far": minutes_so_far,
            })
        return result

    def transactions(self):
        conn = get_connection()
        rows = conn.execute("""
            SELECT p.payment_id, p.reference, p.amount, p.method, p.paid_at,
                   s.plate_number, s.duration_min
            FROM payments p JOIN sessions s ON p.session_id = s.session_id
            ORDER BY p.paid_at DESC
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def summary(self):
        vat_percent = self.rate_module.get_vat_percent()
        txns = self.transactions()
        gross_total = sum(t["amount"] for t in txns)
        vat_total = gross_total * (vat_percent / (100 + vat_percent)) if vat_percent else 0.0
        by_method = {}
        for t in txns:
            by_method[t["method"]] = by_method.get(t["method"], 0.0) + t["amount"]
        return {
            "gross_total": round(gross_total, 2),
            "vat_total": round(vat_total, 2),
            "net_total": round(gross_total - vat_total, 2),
            "vat_percent": vat_percent,
            "by_method": by_method,
            "count": len(txns),
        }
