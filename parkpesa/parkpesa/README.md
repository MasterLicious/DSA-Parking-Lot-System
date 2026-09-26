# ParkPesa — A Modern Parking Management System

**Multimedia University of Kenya — Data Structures and Algorithms**
**Task One: Analysis & Design** and **Task Two: Actual System Development (Python)**

Author: Author: NJEHU LEWIS KIRUTHI
        CIT-223-096/2025

---

## 1. Introduction

The client wants to automate a parking lot in Kenya. Drivers must see slot
availability before entry, vehicles are logged on arrival, the system
calculates duration and fee automatically on exit, and the barrier opens
only once payment is confirmed. This document analyses the brief, proposes
modules, gives each module's algorithm, justifies the data structures used,
and designs the dynamic database. Task Two implements all of this as a
working Flask (Python) web application, `ParkPesa`.

---

## 2. Terms of Reference — Objectives Restated

From the client brief:
1. Show live slot availability on a display board at the entrance and on a web/mobile view.
2. Record each vehicle on arrival: number plate, time of entry, allocated bay.
3. Calculate duration and amount payable automatically at exit.
4. Collect payment by M-Pesa, card or cash; open the barrier only on confirmed payment.
5. Let management change parking rates at any time without a software change.
6. Produce an auditable record of every shilling collected, for reconciliation and VAT.

**In scope:** entry lane control, slot monitoring/display, slot allocation,
duration & fee computation, payment collection, exit barrier control,
exception handling, administrative reporting.
**Out of scope (this phase):** online pre-booking, valet operations, loyalty
scheme integration, automated number-plate blacklisting.

---

## 3. Proposed Modules

Analysing the objectives above yields eight modules, each mapped to one or
more objectives:

| # | Module | Satisfies objective(s) |
|---|--------|------------------------|
| 1 | Slot Display Module | 1 |
| 2 | Slot Allocation Module | 2 |
| 3 | Vehicle Entry Module | 2 |
| 4 | Duration & Fee Calculation Module | 3 |
| 5 | Payment Module | 4 |
| 6 | Barrier Control Module | 4 |
| 7 | Rate Management Module | 5 |
| 8 | Reporting / Audit Module | 6 |

Each module is implemented as its own Python class in `modules.py`, kept
separate from the web layer (`app.py`) so each can be understood, tested and
marked independently.

---

## 4. Algorithms (Task One, part a)

Pseudocode for each module (full working Python is in `modules.py`):

### 4.1 Slot Display Module
```
FUNCTION get_board():
    slots <- SELECT all rows FROM slots table, ordered by zone
    free_count <- count of slots where status = 'available'
    RETURN slots, free_count, total_count
```

### 4.2 Slot Allocation Module
```
FUNCTION allocate_slot():
    IF available_queue is empty: RETURN "lot full"
    slot_id <- dequeue from front of available_queue     # O(1)
    mark slot_id as 'occupied' in database
    RETURN slot_id

FUNCTION release_slot(slot_id):
    mark slot_id as 'available' in database
    enqueue slot_id to back of available_queue            # O(1)
```

### 4.3 Vehicle Entry Module
```
FUNCTION record_arrival(plate_number):
    plate_number <- normalise (uppercase, remove spaces)
    slot <- SlotAllocationModule.allocate_slot()
    IF slot is null: RAISE LotFullError
    INSERT new session (plate_number, slot_id, entry_time = now(), status='PARKED')
    RETURN session, slot
```

### 4.4 Duration & Fee Calculation Module
```
FUNCTION calculate(entry_time, exit_time):
    duration_minutes <- (exit_time - entry_time) in minutes
    FOR each tier in rate_tiers (sorted ascending by ceiling):
        IF tier.ceiling = "no limit" OR duration_minutes <= tier.ceiling:
            RETURN duration_minutes, tier.amount, tier.label
```

### 4.5 Payment Module
```
FUNCTION take_payment(session_id, amount, method):
    reference <- generate_reference(method)     # simulated M-Pesa/card/cash receipt
    INSERT payment record (session_id, amount, method, reference, timestamp)
    UPDATE session SET status='PAID', exit_time=now(), amount_due=amount
    RETURN reference
```

### 4.6 Barrier Control Module
```
FUNCTION open_if_paid(session):
    IF session.status != 'PAID': RETURN false     # barrier stays shut
    SlotAllocationModule.release_slot(session.slot_id)
    RETURN true                                    # barrier opens
```

### 4.7 Rate Management Module
```
FUNCTION update_rate(rate_id, new_amount):
    UPDATE rates table SET amount = new_amount WHERE rate_id = rate_id
    # takes effect on the very next fee calculation — no code/deploy needed
```

### 4.8 Reporting / Audit Module
```
FUNCTION summary():
    gross_total <- SUM(amount) FROM payments
    vat_total <- gross_total * vat_percent / (100 + vat_percent)
    by_method <- SUM(amount) FROM payments GROUP BY method
    RETURN gross_total, vat_total, by_method, full transaction list
```

---

## 5. Data Structures Used and Reasons (Task One, part b)

| Data structure | Where used | Why chosen |
|---|---|---|
| **Queue** (`collections.deque`) | Available-slot pool in Slot Allocation Module | Allocating a bay (dequeue front) and freeing a bay (enqueue back) are both **O(1)**. A queue also gives FIFO fairness — bays rotate evenly instead of always reassigning the same one. Scanning a list of N slots for the first free one on every arrival would cost O(n) and get slower as the car park grows; the queue avoids that. |
| **Hash-indexed lookup** (SQL `PRIMARY KEY` / dict via `sqlite3.Row`) | Looking up a session by `plate_number` or `session_id`, looking up a slot by `slot_id` | Indexed primary-key lookups are effectively **O(1)**, which matters at the exit gate where a driver should not wait while the system searches every past session. |
| **Ordered list of tier records** | Fee Calculation Module (`rates` table read fresh each time) | The tiers are few (≈5) and must be checked **in order** (smallest ceiling first) to find the correct bracket — a simple ordered scan is O(k) with k constant, i.e. effectively O(1), and is far easier to audit than nested if/else logic hard-coded in Python. |
| **Relational tables (rows), not constants in code** | `rates`, `slots`, `settings` | This is what makes the system *dynamic*: management edits data, not code, to change a fee or add a bay, directly satisfying "let management change parking rates at any time without a software change." |
| **Append-only log (`payments` table)** | Reporting/Audit Module | Never updated or deleted from the UI, so the table itself is the tamper-evident audit trail needed for reconciliation and VAT filing. SQL `GROUP BY`/`SUM` then does the aggregation in a single indexed pass. |
| **Exception object (`LotFullError`)** | Vehicle Entry Module | Represents the "lot full" edge case explicitly instead of a magic return value, so the web layer can catch it and show a clear message to the driver — this is the system's exception-handling mechanism. |

---

## 6. Dynamic Database Design (Task One, part c)

"Dynamic" here means: (1) it is a real database, not a flat file, so it
supports concurrent reads/aggregation; and (2) all business-changeable
values (fees, VAT %, bay inventory) are **data rows**, never hard-coded, so
they can change without editing or redeploying code.

### 6.1 Entity-Relationship overview

```
 slots (1) ───< sessions (1) ───< payments
                    |
                    | (fee looked up from, not stored as FK — see note)
                 rates (read at calculation time)

 settings  (standalone key/value: e.g. vat_percent)
```

### 6.2 Tables

**slots** — physical bays
| Column | Type | Notes |
|---|---|---|
| slot_id | INTEGER PK | |
| slot_code | TEXT | e.g. "A1", unique |
| zone | TEXT | groups bays for the display board |
| status | TEXT | 'available' \| 'occupied' |

**sessions** — one row per vehicle visit
| Column | Type | Notes |
|---|---|---|
| session_id | INTEGER PK | |
| plate_number | TEXT | normalised uppercase |
| slot_id | INTEGER FK → slots | |
| entry_time | TEXT (ISO datetime) | |
| exit_time | TEXT (ISO datetime) | NULL until paid |
| duration_min | INTEGER | filled at exit |
| amount_due | REAL | filled at exit |
| status | TEXT | PARKED \| PAID |

**rates** — the dynamic pricing table (management-editable)
| Column | Type | Notes |
|---|---|---|
| rate_id | INTEGER PK | |
| label | TEXT | e.g. "Up to 2 hours" |
| up_to_minutes | INTEGER | ceiling for this tier; -1 = no limit |
| amount | REAL | fee in Kshs. |
| sort_order | INTEGER | evaluation order |

**payments** — audit trail
| Column | Type | Notes |
|---|---|---|
| payment_id | INTEGER PK | |
| session_id | INTEGER FK → sessions | |
| amount | REAL | |
| method | TEXT | MPESA \| CARD \| CASH |
| reference | TEXT | receipt / M-Pesa code |
| paid_at | TEXT (ISO datetime) | |

**settings** — single-row key/value store
| Column | Type | Notes |
|---|---|---|
| key | TEXT PK | e.g. "vat_percent" |
| value | TEXT | |

### 6.3 Why this design satisfies "dynamic"
- Adding a new zone or bay = `INSERT` into `slots` — no code change.
- Changing a fee or VAT % = `UPDATE` a row in `rates`/`settings` (done through
  the `/admin/rates` screen) — no code change, takes effect on the next car.
- The schema is normalised (3NF): fees are not duplicated into every
  session row; they are computed at exit time by joining against the
  current `rates`, so historic sessions always used the rate that was
  active *at that exit time* once paid, while future exits automatically
  see any new rate.

---

## 7. System Architecture (Task Two)

- **Language:** Python 3, **Framework:** Flask (web-based, as required)
- **Database:** SQLite (file `parkpesa.db`), created by `database.py`
- **Layers:**
  - `database.py` — schema + seed data (the dynamic database)
  - `modules.py` — the 8 modules / algorithms (business logic, unit-testable on its own)
  - `app.py` — Flask routes (thin web layer, no business logic)
  - `templates/` — HTML pages (display board, entry, exit, payment, admin rates, reports)
  - `static/style.css` — styling

### 7.1 Running it
```bash
pip install -r requirements.txt
python app.py
```
Open `http://127.0.0.1:5000/` for the entrance display board.

### 7.2 Walkthrough
1. `/` — entrance display board (auto-shows free/occupied bays).
2. `/entry` — gate operator types a plate number → system allocates a bay and prints a ticket.
3. `/exit` — type the plate number → system computes duration + fee from the live `rates` table.
4. Choose M-Pesa / Card / Cash → payment is recorded, barrier opens, bay is released.
5. `/admin/rates` — management edits fees/VAT any time (dynamic pricing).
6. `/admin/reports` — auditable log of every payment, totals by method, VAT breakdown.

### 7.3 Fee tiers seeded (from the client's tariff card)
| Duration | Fee |
|---|---|
| Up to 30 minutes | Free |
| Up to 2 hours | Kshs. 50 |
| Up to 4 hours | Kshs. 100 |
| Up to 6 hours | Kshs. 300 |
| Over 6 hours | Kshs. 500 |

These are rows in the `rates` table, editable from `/admin/rates` — not
constants in the code.

---

## 8. Notes on the payment simulation

Real M-Pesa collection requires Safaricom's Daraja STK Push API and a
publicly reachable callback URL, which is outside what can be demonstrated
offline for coursework. `PaymentModule.take_payment()` simulates a
successful payment and generates a reference code, at exactly the point
where the real API call would be inserted in production — the module
boundary is deliberate so this is a drop-in replacement later, not a
redesign.
