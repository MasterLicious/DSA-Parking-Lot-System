"""
app.py
------
ParkPesa - A Modern Parking Management System (Task Two: functional system).

This is the WEB layer only. It does not contain any business logic itself --
it just receives HTTP requests, calls the correct module from modules.py,
and renders a template. Keeping logic out of the web layer is intentional:
it is what lets each module be understood (and marked) on its own, matching
the modules identified in Task One.

Run with:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000 in a browser.
"""

from flask import Flask, render_template, request, redirect, url_for, flash

from database import init_db
from modules import (
    SlotAllocationModule,
    SlotDisplayModule,
    VehicleEntryModule,
    RateManagementModule,
    FeeCalculationModule,
    PaymentModule,
    BarrierControlModule,
    ReportingModule,
    LotFullError,
    DuplicateEntryError,
    get_connection,
)

app = Flask(__name__)
app.secret_key = "parkpesa-dev-secret"  # fine for a student demo project

# The database (parkpesa.db) and its tables must exist BEFORE any module
# tries to read from them. init_db() is safe to call every time the app
# starts: it only creates tables/seed data if they don't already exist,
# it never wipes existing data on a normal run.
init_db()

# One shared instance of each module for the lifetime of the app.
# (SlotAllocationModule holds the in-memory queue described in modules.py.)
slot_alloc = SlotAllocationModule()
slot_display = SlotDisplayModule()
rate_mgmt = RateManagementModule()
fee_calc = FeeCalculationModule(rate_mgmt)
entry_module = VehicleEntryModule(slot_alloc)
payment_module = PaymentModule()
barrier = BarrierControlModule(slot_alloc)
reporting = ReportingModule(rate_mgmt)


# ---------------------------------------------------------------------------
# Module 1: Entrance display board / web view
# ---------------------------------------------------------------------------
@app.route("/")
def display_board():
    board = slot_display.get_board()
    return render_template("display.html", board=board)


# ---------------------------------------------------------------------------
# Module 3: Vehicle entry (gate operator / kiosk screen)
# ---------------------------------------------------------------------------
@app.route("/entry", methods=["GET", "POST"])
def vehicle_entry():
    if request.method == "POST":
        plate = request.form.get("plate_number", "")
        try:
            session, slot = entry_module.record_arrival(plate)
            return render_template("entry.html", ticket={"session": session, "slot": slot})
        except LotFullError as e:
            flash(str(e), "error")
        except DuplicateEntryError as e:
            flash(str(e), "error")
        except ValueError as e:
            flash(str(e), "error")
    return render_template("entry.html", ticket=None)


# ---------------------------------------------------------------------------
# Module 4: Exit gate - duration & fee calculation
# ---------------------------------------------------------------------------
@app.route("/exit", methods=["GET", "POST"])
def vehicle_exit():
    if request.method == "POST":
        plate = request.form.get("plate_number", "").strip().upper().replace(" ", "")
        conn = get_connection()
        session = conn.execute(
            "SELECT * FROM sessions WHERE plate_number=? AND status='PARKED' "
            "ORDER BY entry_time DESC LIMIT 1",
            (plate,),
        ).fetchone()
        conn.close()

        if session is None:
            flash("No active parking session found for that plate.", "error")
            return render_template("exit.html", bill=None)

        duration_minutes, amount_due, tier_label = fee_calc.calculate(session["entry_time"])
        bill = {
            "session_id": session["session_id"],
            "plate_number": session["plate_number"],
            "entry_time": session["entry_time"],
            "duration_minutes": duration_minutes,
            "amount_due": amount_due,
            "tier_label": tier_label,
        }
        return render_template("exit.html", bill=bill)
    return render_template("exit.html", bill=None)


# ---------------------------------------------------------------------------
# Module 5 & 6: Payment + barrier control
# ---------------------------------------------------------------------------
@app.route("/pay/<int:session_id>", methods=["POST"])
def pay(session_id):
    amount = float(request.form.get("amount_due"))
    duration_minutes = int(request.form.get("duration_minutes"))
    method = request.form.get("method", "CASH")

    reference = payment_module.take_payment(session_id, amount, method, duration_minutes)

    conn = get_connection()
    session = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    conn.close()

    barrier_open = barrier.open_if_paid(dict(session))

    return render_template(
        "payment_result.html",
        reference=reference,
        amount=amount,
        method=method,
        barrier_open=barrier_open,
        plate=session["plate_number"],
    )


# ---------------------------------------------------------------------------
# Module 7: Admin - rate management (dynamic pricing)
# ---------------------------------------------------------------------------
@app.route("/admin/rates", methods=["GET", "POST"])
def admin_rates():
    if request.method == "POST":
        for key, value in request.form.items():
            if key.startswith("rate_"):
                rate_id = int(key.replace("rate_", ""))
                rate_mgmt.update_rate(rate_id, value)
            elif key == "vat_percent":
                rate_mgmt.set_vat_percent(value)
        flash("Rates updated. New fees apply immediately, no restart needed.", "success")
        return redirect(url_for("admin_rates"))

    rates = rate_mgmt.get_rates()
    vat_percent = rate_mgmt.get_vat_percent()
    return render_template("admin_rates.html", rates=rates, vat_percent=vat_percent)


# ---------------------------------------------------------------------------
# Module 8 (extended): currently parked vehicles
# ---------------------------------------------------------------------------
@app.route("/parked")
def parked():
    vehicles = reporting.currently_parked()
    return render_template("parked.html", vehicles=vehicles)


# ---------------------------------------------------------------------------
# Module 8: Reporting / audit trail
# ---------------------------------------------------------------------------
@app.route("/admin/reports")
def admin_reports():
    summary = reporting.summary()
    txns = reporting.transactions()
    return render_template("reports.html", summary=summary, txns=txns)


if __name__ == "__main__":
    app.run(debug=True)
