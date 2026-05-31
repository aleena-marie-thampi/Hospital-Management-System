from flask import Flask, render_template, request, redirect, session, jsonify
import mysql.connector
from functools import wraps
from datetime import date, datetime, timedelta
from contextlib import contextmanager

app = Flask(__name__)
app.secret_key = "secret"

MIN_STOCK = 10  # fixed minimum stock threshold


# ─────────────────────────────────────────────
#  DATABASE — context manager (always closes)
# ─────────────────────────────────────────────
import os   # add this at the very top of app.py

@contextmanager
def get_db():
    conn = mysql.connector.connect(
    host=os.environ.get("MYSQLHOST", "localhost"),
    user=os.environ.get("MYSQLUSER", "root"),
    password=os.environ.get("MYSQLPASSWORD", "aleena@2006"),
    database=os.environ.get("MYSQLDATABASE", "hms1"),
    port=int(os.environ.get("MYSQLPORT", 3306)),
    autocommit=False,
    connection_timeout=10,
)
    )
    cursor = conn.cursor(dictionary=True)
    try:
        yield conn, cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


# ─────────────────────────────────────────────
#  AUTH DECORATORS
# ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect("/")
        return f(*args, **kwargs)
    return wrapper


def role_required(role):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "user" not in session or session["user"]["Role"] != role:
                return redirect("/")
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ─────────────────────────────────────────────
#  LOGIN / LOGOUT
# ─────────────────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        session.clear()

    if "user" in session:
        role = session["user"]["Role"]
        if role == "Admin":   return redirect("/admin")
        if role == "Doctor":  return redirect("/doctor")
        if role == "Patient": return redirect("/patient")

    error = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()

        with get_db() as (db, cursor):
            cursor.execute(
                "SELECT * FROM user WHERE Username=%s AND Password=%s", (u, p)
            )
            user = cursor.fetchone()

        if user:
            session["user"] = user
            role = user["Role"]
            if role == "Admin":   return redirect("/admin?msg=Login+Successful")
            if role == "Doctor":  return redirect("/doctor?msg=Login+Successful")
            if role == "Patient": return redirect("/patient?msg=Login+Successful")
        else:
            error = "Invalid username or password"

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ─────────────────────────────────────────────
#  ADMIN DASHBOARD
# ─────────────────────────────────────────────
@app.route("/admin")
@login_required
@role_required("Admin")
def admin():
    with get_db() as (db, cursor):
        cursor.execute("SELECT COUNT(*) AS total FROM patient")
        patients_count = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM doctor")
        doctors_count = cursor.fetchone()["total"]

        cursor.execute(
            "SELECT COUNT(*) AS total FROM appointment WHERE Date=%s AND Status='Pending'",
            (date.today(),)
        )
        today_appts = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM bill WHERE Status='Pending'")
        pending_bills = cursor.fetchone()["total"]

        cursor.execute("SELECT * FROM patient")
        patients = cursor.fetchall()

        cursor.execute("SELECT * FROM doctor")
        doctors = cursor.fetchall()

        from datetime import timedelta as _td
        def _td_str(v):
            if not isinstance(v, _td): return v
            s = int(v.total_seconds())
            return f"{s//3600:02d}:{(s%3600)//60:02d}"
        doctors = [{k: _td_str(v) for k, v in row.items()} for row in doctors]

        cursor.execute("SELECT * FROM medicine ORDER BY Name")
        medicines = cursor.fetchall()

        cursor.execute("""
            SELECT a.*, p.Name AS PatientName, d.Name AS DoctorName
            FROM appointment a
            JOIN patient p ON a.PatientID = p.PatientID
            JOIN doctor  d ON a.DoctorID  = d.DoctorID
            ORDER BY a.Date DESC, a.Time DESC
        """)
        all_appointments = cursor.fetchall()

        cursor.execute("""
            SELECT b.*, p.Name AS PatientName
            FROM bill b JOIN patient p ON b.PatientID = p.PatientID
            ORDER BY b.BillID DESC
        """)
        all_bills = cursor.fetchall()

        stock_alerts = []
        try:
            cursor.execute("SELECT * FROM stock_alerts ORDER BY AlertTime DESC LIMIT 20")
            stock_alerts = cursor.fetchall()
        except Exception:
            pass

        pending_dues = []
        try:
            cursor.execute("""
                SELECT pd.*, p.Name AS PatientName
                FROM pending_dues pd
                JOIN patient p ON pd.PatientID = p.PatientID
                ORDER BY pd.CreatedAt DESC
            """)
            pending_dues = cursor.fetchall()
        except Exception:
            pass

    return render_template(
        "admin.html",
        patients=patients,
        doctors=doctors,
        medicines=medicines,
        all_appointments=all_appointments,
        all_bills=all_bills,
        stock_alerts=stock_alerts,
        pending_dues=pending_dues,
        today=date.today(),
        min_stock=MIN_STOCK,
        stats={
            "patients":      patients_count,
            "doctors":       doctors_count,
            "appointments":  today_appts,
            "pending_bills": pending_bills
        },
        new_patient_id=request.args.get("new_patient_id"),
        new_patient_username=request.args.get("new_patient_username"),
        new_doctor_id=request.args.get("new_doctor_id"),
        new_doctor_username=request.args.get("new_doctor_username"),
    )


# ─────────────────────────────────────────────
#  PATIENT CRUD  (no delete)
# ─────────────────────────────────────────────
@app.route("/add_patient", methods=["POST"])
@login_required
@role_required("Admin")
def add_patient():
    username = request.form["Username"]
    with get_db() as (db, cursor):
        cursor.execute(
            "INSERT INTO user (Username, Password, Role) VALUES (%s, %s, 'Patient')",
            (username, request.form["Password"])
        )
        db.commit()
        user_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO patient (Name, Age, Gender, Phone, UserID) VALUES (%s, %s, %s, %s, %s)",
            (request.form["Name"], request.form["Age"],
             request.form["Gender"], request.form["Phone"], user_id)
        )
        db.commit()
        new_patient_id = cursor.lastrowid
    return redirect(f"/admin?tab=patients&new_patient_id={new_patient_id}&new_patient_username={username}&msg=Patient+added+successfully")


# ─────────────────────────────────────────────
#  DOCTOR CRUD  (no delete)
# ─────────────────────────────────────────────
@app.route("/add_doctor", methods=["POST"])
@login_required
@role_required("Admin")
def add_doctor():
    username = request.form["Username"]
    with get_db() as (db, cursor):
        cursor.execute(
            "INSERT INTO user (Username, Password, Role) VALUES (%s, %s, 'Doctor')",
            (username, request.form["Password"])
        )
        db.commit()
        user_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO doctor (Name, Specialization, ConsultationFee, UserID) VALUES (%s, %s, %s, %s)",
            (request.form["Name"], request.form["Specialization"],
             request.form.get("ConsultationFee", 0), user_id)
        )
        db.commit()
        new_doctor_id = cursor.lastrowid
    return redirect(f"/admin?tab=doctors&new_doctor_id={new_doctor_id}&new_doctor_username={username}&msg=Doctor+added+successfully")


# ─────────────────────────────────────────────
#  MEDICINE — upsert: if name+dosage+form exists → add stock; else insert
# ─────────────────────────────────────────────
@app.route("/add_medicine", methods=["POST"])
@login_required
@role_required("Admin")
def add_medicine():
    name   = request.form["Name"].strip()
    dosage = request.form.get("Dosage", "").strip()
    form   = request.form.get("Form", "").strip()
    stock  = int(request.form.get("Stock", 0))
    price  = float(request.form.get("Price", 0))

    with get_db() as (db, cursor):
        cursor.execute(
            "SELECT MedicineID, Stock FROM medicine WHERE Name=%s AND Dosage=%s AND Form=%s",
            (name, dosage, form)
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                "UPDATE medicine SET Stock = Stock + %s, MinStock = %s, Price = %s WHERE MedicineID = %s",
                (stock, MIN_STOCK, price, existing["MedicineID"])
            )
            msg = f"Stock+updated+for+{name.replace(' ','+')}+(added+{stock}+units)"
        else:
            cursor.execute(
                "INSERT INTO medicine (Name, Stock, MinStock, Dosage, Form, Price) VALUES (%s,%s,%s,%s,%s,%s)",
                (name, stock, MIN_STOCK, dosage, form, price)
            )
            msg = f"{name.replace(' ','+')}+added+to+inventory"

    return redirect(f"/admin?tab=medicines&msg={msg}")


# ─────────────────────────────────────────────
#  MEDICINE — check if exists (AJAX, called on name/dosage/form change)
# ─────────────────────────────────────────────
@app.route("/api/medicine_check")
@login_required
@role_required("Admin")
def medicine_check():
    name   = request.args.get("name", "").strip()
    dosage = request.args.get("dosage", "").strip()
    form   = request.args.get("form", "").strip()
    if not name:
        return jsonify({"found": False})
    with get_db() as (db, cursor):
        cursor.execute(
            "SELECT MedicineID, Name, Dosage, Form, Stock, Price FROM medicine WHERE Name=%s AND Dosage=%s AND Form=%s",
            (name, dosage, form)
        )
        med = cursor.fetchone()
    if med:
        return jsonify({"found": True, **med})
    return jsonify({"found": False})


# ─────────────────────────────────────────────
#  MEDICINE — manual stock adjust (add or remove units)
# ─────────────────────────────────────────────
@app.route("/adjust_stock", methods=["POST"])
@login_required
@role_required("Admin")
def adjust_stock():
    med_id = int(request.form["MedicineID"])
    action = request.form["action"]   # "add" or "remove"
    qty    = int(request.form["qty"])

    with get_db() as (db, cursor):
        cursor.execute("SELECT Name, Stock FROM medicine WHERE MedicineID=%s", (med_id,))
        med = cursor.fetchone()
        if not med:
            return redirect("/admin?tab=medicines&msg=Medicine+not+found")

        if action == "remove":
            if qty > med["Stock"]:
                return redirect(f"/admin?tab=medicines&msg=Cannot+remove+{qty}+units.+Only+{med['Stock']}+in+stock")
            cursor.execute(
                "UPDATE medicine SET Stock = Stock - %s, MinStock = %s WHERE MedicineID = %s",
                (qty, MIN_STOCK, med_id)
            )
            msg = f"Removed+{qty}+units+from+{med['Name'].replace(' ', '+')}"
        else:
            cursor.execute(
                "UPDATE medicine SET Stock = Stock + %s, MinStock = %s WHERE MedicineID = %s",
                (qty, MIN_STOCK, med_id)
            )
            msg = f"Added+{qty}+units+to+{med['Name'].replace(' ', '+')}"

    return redirect(f"/admin?tab=medicines&msg={msg}")


@app.route("/low_stock")
@login_required
@role_required("Admin")
def low_stock():
    with get_db() as (db, cursor):
        cursor.execute("SELECT * FROM medicine WHERE Stock < %s", (MIN_STOCK,))
        meds = cursor.fetchall()
    return jsonify(meds)


# ─────────────────────────────────────────────
#  APPOINTMENTS  (Admin schedules)
# ─────────────────────────────────────────────
@app.route("/add_appointment", methods=["POST"])
@login_required
@role_required("Admin")
def add_appointment():
    patient_id = request.form["PatientID"]
    doctor_id  = request.form["DoctorID"]
    date_      = request.form["Date"]
    time_      = request.form["Time"]

    with get_db() as (db, cursor):
        cursor.execute(
            "SELECT AppointmentID FROM appointment WHERE DoctorID=%s AND Date=%s AND Time=%s AND Status='Pending'",
            (doctor_id, date_, time_)
        )
        if cursor.fetchone():
            return redirect("/admin?msg=Doctor+already+booked+at+this+time")

        cursor.execute(
            "INSERT INTO appointment (PatientID, DoctorID, Date, Time, Status) VALUES (%s,%s,%s,%s,'Pending')",
            (patient_id, doctor_id, date_, time_)
        )

    return redirect("/admin?msg=Appointment+scheduled+successfully")


# ─────────────────────────────────────────────
#  DOCTOR AVAILABILITY API
# ─────────────────────────────────────────────
@app.route("/api/doctor_availability/<int:doctor_id>")
@login_required
def doctor_availability(doctor_id):
    with get_db() as (db, cursor):
        cursor.execute(
            "SELECT AvailableDays, StartTime, EndTime, SlotDuration FROM doctor WHERE DoctorID=%s",
            (doctor_id,)
        )
        doc = cursor.fetchone()

        if not doc or not doc["AvailableDays"]:
            return jsonify({"error": "No availability set for this doctor. Please contact admin."})

        available_days = [d.strip() for d in doc["AvailableDays"].split(",")]

        def td_to_str(td):
            if td is None: return None
            s = int(td.total_seconds())
            return f"{s//3600:02d}:{(s%3600)//60:02d}"

        start_time    = td_to_str(doc["StartTime"])
        end_time      = td_to_str(doc["EndTime"])
        slot_duration = doc["SlotDuration"] or 30

        def generate_slots(start, end, duration):
            slots, cur = [], datetime.strptime(start, "%H:%M")
            end_dt = datetime.strptime(end, "%H:%M")
            while cur < end_dt:
                slots.append(cur.strftime("%H:%M"))
                cur += timedelta(minutes=duration)
            return slots

        all_slots = generate_slots(start_time, end_time, slot_duration)
        today     = date.today()
        end_range = today + timedelta(days=60)

        cursor.execute("""
            SELECT Date, TIME_FORMAT(Time, '%%H:%%i') AS Time
            FROM appointment
            WHERE DoctorID=%s AND Date BETWEEN %s AND %s AND Status='Pending'
        """, (doctor_id, today.isoformat(), end_range.isoformat()))
        booked_rows = cursor.fetchall()

    booked_slots = {}
    for row in booked_rows:
        booked_slots.setdefault(str(row["Date"]), []).append(row["Time"])

    fully_booked_dates = [
        d for d, taken in booked_slots.items()
        if set(all_slots).issubset(set(taken))
    ]

    next_available = None
    check = date.today()
    for _ in range(90):
        if check.strftime("%A") in available_days:
            d_str = check.isoformat()
            if d_str not in fully_booked_dates:
                next_available = d_str
                break
        check += timedelta(days=1)

    return jsonify({
        "available_days": available_days, "start_time": start_time,
        "end_time": end_time, "slot_duration": slot_duration,
        "all_slots": all_slots, "booked_slots": booked_slots,
        "fully_booked_dates": fully_booked_dates, "next_available": next_available
    })


# ─────────────────────────────────────────────
#  BOOK APPOINTMENT  (Patient)
# ─────────────────────────────────────────────
@app.route("/book_appointment", methods=["POST"])
@login_required
@role_required("Patient")
def book_appointment():
    user = session["user"]

    with get_db() as (db, cursor):
        cursor.execute("SELECT PatientID FROM patient WHERE UserID=%s", (user["UserID"],))
        pat = cursor.fetchone()
        if not pat:
            return redirect("/patient?msg=Patient+profile+not+found")

        patient_id = pat["PatientID"]
        doctor_id  = request.form["DoctorID"]
        date_      = request.form["Date"]
        time_      = request.form["Time"]

        cursor.execute(
            "SELECT AvailableDays, StartTime, EndTime FROM doctor WHERE DoctorID=%s", (doctor_id,)
        )
        doc = cursor.fetchone()
        if not doc or not doc["AvailableDays"]:
            return redirect("/patient?msg=Doctor+availability+not+configured.+Contact+admin.")

        chosen_day     = datetime.strptime(date_, "%Y-%m-%d").strftime("%A")
        available_days = [d.strip() for d in doc["AvailableDays"].split(",")]

        if chosen_day not in available_days:
            return redirect(f"/patient?msg=Dr.+is+not+available+on+{chosen_day}.+Available:+{','.join(available_days).replace(' ', '+')}")

        def td_to_str(td):
            s = int(td.total_seconds())
            return f"{s//3600:02d}:{(s%3600)//60:02d}"

        start_str   = td_to_str(doc["StartTime"])
        end_str     = td_to_str(doc["EndTime"])
        chosen_time = datetime.strptime(time_, "%H:%M")
        start_time  = datetime.strptime(start_str, "%H:%M")
        end_time    = datetime.strptime(end_str,   "%H:%M")

        if not (start_time <= chosen_time < end_time):
            return redirect(f"/patient?msg=Doctor+available+only+between+{start_str}+and+{end_str}")

        cursor.execute(
            "SELECT * FROM appointment WHERE DoctorID=%s AND Date=%s AND Time=%s AND Status='Pending'",
            (doctor_id, date_, time_)
        )
        if cursor.fetchone():
            cursor.execute("""
                SELECT TIME_FORMAT(Time,'%%H:%%i') AS Time FROM appointment
                WHERE DoctorID=%s AND Date=%s AND Status='Pending' ORDER BY Time
            """, (doctor_id, date_))
            booked_times = [r["Time"] for r in cursor.fetchall()]
            check_time = start_time
            next_slot = None
            while check_time < end_time:
                t_str = check_time.strftime("%H:%M")
                if t_str not in booked_times:
                    next_slot = t_str
                    break
                check_time += timedelta(minutes=30)
            if next_slot:
                return redirect(f"/patient?msg=Slot+booked.+Next+available:+{next_slot}")
            return redirect("/patient?msg=Doctor+fully+booked+today.+Try+another+date.")

        cursor.execute(
            "INSERT INTO appointment (PatientID, DoctorID, Date, Time, Status) VALUES (%s,%s,%s,%s,'Pending')",
            (patient_id, doctor_id, date_, time_)
        )

    return redirect("/patient?msg=Appointment+booked+successfully")


# ─────────────────────────────────────────────
#  DOCTOR DASHBOARD
# ─────────────────────────────────────────────
@app.route("/doctor")
@login_required
@role_required("Doctor")
def doctor():
    user = session["user"]

    with get_db() as (db, cursor):
        cursor.execute("SELECT DoctorID, Name FROM doctor WHERE UserID=%s", (user["UserID"],))
        doc = cursor.fetchone()
        if not doc:
            return "Doctor profile not found. Contact Admin."

        cursor.execute("""
            SELECT a.*, p.Name AS PatientName
            FROM appointment a JOIN patient p ON a.PatientID = p.PatientID
            WHERE a.DoctorID = %s AND a.Status = 'Pending'
            ORDER BY a.Date, a.Time
        """, (doc["DoctorID"],))
        appointments = cursor.fetchall()

        cursor.execute(
            "SELECT COUNT(*) AS total FROM appointment WHERE DoctorID=%s AND Date=%s AND Status='Pending'",
            (doc["DoctorID"], date.today())
        )
        today_count = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM medicalrecord WHERE DoctorID=%s", (doc["DoctorID"],))
        records_written = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM appointment WHERE DoctorID=%s AND Status='Pending'", (doc["DoctorID"],))
        pending_count = cursor.fetchone()["total"]

        cursor.execute("""
            SELECT mr.*, p.Name AS PatientName FROM medicalrecord mr
            JOIN patient p ON mr.PatientID = p.PatientID
            WHERE mr.DoctorID = %s ORDER BY mr.RecordID DESC LIMIT 20
        """, (doc["DoctorID"],))
        past_records = cursor.fetchall()

        cursor.execute("SELECT MedicineID, Name, Dosage, Form, Stock, MinStock FROM medicine ORDER BY Name")
        medicines = cursor.fetchall()

    return render_template(
        "doctor.html", appointments=appointments, medicines=medicines,
        past_records=past_records, today=date.today(),
        stats={"today": today_count, "written": records_written, "pending": pending_count, "name": doc["Name"]}
    )


# ─────────────────────────────────────────────
#  CHECK PATIENT  (AJAX)
# ─────────────────────────────────────────────
@app.route("/check_patient/<int:patient_id>")
@login_required
@role_required("Doctor")
def check_patient(patient_id):
    user = session["user"]
    with get_db() as (db, cursor):
        cursor.execute("SELECT DoctorID FROM doctor WHERE UserID=%s", (user["UserID"],))
        doc = cursor.fetchone()
        if not doc:
            return jsonify({"valid": False, "reason": "Doctor profile not found"})
        cursor.execute("""
            SELECT p.Name FROM patient p JOIN appointment a ON p.PatientID = a.PatientID
            WHERE p.PatientID = %s AND a.DoctorID = %s AND a.Status = 'Pending' LIMIT 1
        """, (patient_id, doc["DoctorID"]))
        patient = cursor.fetchone()
    if patient:
        return jsonify({"valid": True, "name": patient["Name"]})
    return jsonify({"valid": False, "reason": "No pending appointment found with this patient"})


# ─────────────────────────────────────────────
#  ADD RECORD + PRESCRIPTION
# ─────────────────────────────────────────────
@app.route("/add_record_with_prescription", methods=["POST"])
@login_required
@role_required("Doctor")
def add_record_with_prescription():
    user            = session["user"]
    patient_id      = request.form.get("PatientID", "").strip()
    diagnosis       = request.form.get("Diagnosis", "").strip()
    treatment       = request.form.get("Treatment", "").strip()
    medicine_ids    = request.form.getlist("MedicineID")
    quantities      = request.form.getlist("Quantity")
    frequencies     = request.form.getlist("Frequency")
    timing_patterns = request.form.getlist("TimingPattern")
    food_instrs     = request.form.getlist("FoodInstruction")

    if not patient_id or not diagnosis or not treatment:
        return redirect("/doctor?msg=Patient+ID,+Diagnosis+and+Treatment+are+required")

    with get_db() as (db, cursor):
        cursor.execute("SELECT DoctorID FROM doctor WHERE UserID=%s", (user["UserID"],))
        doc = cursor.fetchone()
        if not doc:
            return redirect("/doctor?msg=Doctor+profile+not+found")

        cursor.execute("SELECT PatientID FROM patient WHERE PatientID=%s", (patient_id,))
        if not cursor.fetchone():
            return redirect("/doctor?msg=Invalid+Patient+ID")

        cursor.execute("""
            SELECT AppointmentID FROM appointment
            WHERE PatientID=%s AND DoctorID=%s AND Status='Pending' LIMIT 1
        """, (patient_id, doc["DoctorID"]))
        appt = cursor.fetchone()
        if not appt:
            return redirect("/doctor?msg=This+patient+has+no+pending+appointment+with+you")

        cursor.execute(
            "INSERT INTO medicalrecord (PatientID, DoctorID, Diagnosis, Treatment) VALUES (%s,%s,%s,%s)",
            (patient_id, doc["DoctorID"], diagnosis, treatment)
        )
        db.commit()
        record_id = cursor.lastrowid

        n = len(medicine_ids)
        def pad(lst): return lst + [''] * (n - len(lst))
        quantities      = pad(quantities)
        frequencies     = pad(frequencies)
        timing_patterns = pad(timing_patterns)
        food_instrs     = pad(food_instrs)

        filled = [
            {"med_id": int(medicine_ids[i].strip()), "qty": int(quantities[i].strip()),
             "freq": frequencies[i].strip() or None, "timing": timing_patterns[i].strip() or None,
             "food": food_instrs[i].strip() or None}
            for i in range(n) if medicine_ids[i].strip() and quantities[i].strip()
        ]

        if filled:
            for row in filled:
                cursor.execute(
                    "INSERT INTO prescription (RecordID, Frequency, TimingPattern, FoodInstruction) VALUES (%s,%s,%s,%s)",
                    (record_id, row["freq"], row["timing"], row["food"])
                )
                db.commit()
                pid = cursor.lastrowid
                cursor.execute(
                    "INSERT INTO prescription_medicine (PrescriptionID, MedicineID, Quantity) VALUES (%s,%s,%s)",
                    (pid, row["med_id"], row["qty"])
                )
            db.commit()
            msg = "Record+and+prescription+saved+successfully"
        else:
            msg = "Medical+record+saved+(no+prescription+added)"

        cursor.execute("UPDATE appointment SET Status='Completed' WHERE AppointmentID=%s", (appt["AppointmentID"],))

    return redirect(f"/doctor?msg={msg}")


# ─────────────────────────────────────────────
#  PATIENT DASHBOARD
# ─────────────────────────────────────────────
@app.route("/patient")
@login_required
@role_required("Patient")
def patient():
    user = session["user"]

    with get_db() as (db, cursor):
        cursor.execute("SELECT PatientID FROM patient WHERE UserID=%s", (user["UserID"],))
        pat = cursor.fetchone()
        if not pat:
            return "Patient profile not found. Contact Admin."

        patient_id = pat["PatientID"]

        cursor.execute("SELECT * FROM bill WHERE PatientID=%s", (patient_id,))
        bills = cursor.fetchall()

        cursor.execute("""
            SELECT mr.*, d.Name AS DoctorName FROM medicalrecord mr
            JOIN doctor d ON mr.DoctorID = d.DoctorID
            WHERE mr.PatientID = %s ORDER BY mr.RecordID DESC
        """, (patient_id,))
        records = cursor.fetchall()

        cursor.execute("""
            SELECT pm.Quantity, m.Name AS MedicineName, pr.Frequency,
                   pr.TimingPattern, pr.FoodInstruction, mr.Diagnosis, d.Name AS DoctorName
            FROM prescription pr
            JOIN prescription_medicine pm ON pr.PrescriptionID = pm.PrescriptionID
            JOIN medicine m               ON pm.MedicineID     = m.MedicineID
            JOIN medicalrecord mr         ON pr.RecordID       = mr.RecordID
            JOIN doctor d                 ON mr.DoctorID       = d.DoctorID
            WHERE mr.PatientID = %s ORDER BY pr.PrescriptionID DESC
        """, (patient_id,))
        prescriptions = cursor.fetchall()

        cursor.execute("""
            SELECT a.*, d.Name AS DoctorName, d.Specialization FROM appointment a
            JOIN doctor d ON a.DoctorID = d.DoctorID
            WHERE a.PatientID = %s ORDER BY a.Date DESC, a.Time DESC
        """, (patient_id,))
        my_appointments = cursor.fetchall()

        cursor.execute(
            "SELECT DoctorID, Name, Specialization, AvailableDays, StartTime, EndTime FROM doctor ORDER BY Name"
        )
        doctors = cursor.fetchall()
        for d in doctors:
            for field in ("StartTime", "EndTime"):
                if d[field] is not None:
                    s = int(d[field].total_seconds())
                    d[field] = f"{s//3600:02d}:{(s%3600)//60:02d}"

    pending_bills = sum(1 for b in bills if b.get("Status") == "Pending")
    total_amount  = sum(float(b.get("TotalAmount", 0)) for b in bills)

    return render_template(
        "patient.html", bills=bills, records=records, prescriptions=prescriptions,
        my_appointments=my_appointments, doctors=doctors, today=date.today(),
        summary={"records": len(records), "pending_bills": pending_bills, "total_amount": total_amount}
    )


# ─────────────────────────────────────────────
#  BILLING
# ─────────────────────────────────────────────
@app.route("/add_bill", methods=["POST"])
@login_required
@role_required("Admin")
def add_bill():
    patient_id   = request.form["PatientID"]
    hospital_fee = 300.00

    with get_db() as (db, cursor):
        cursor.execute("""
            SELECT a.AppointmentID, a.DoctorID FROM appointment a
            WHERE a.PatientID = %s AND a.Status = 'Completed'
            ORDER BY a.Date DESC, a.Time DESC LIMIT 1
        """, (patient_id,))
        appt = cursor.fetchone()
        if not appt:
            return redirect("/admin?tab=billing&msg=No+completed+appointment+found+for+this+patient")

        cursor.execute("SELECT ConsultationFee FROM doctor WHERE DoctorID = %s", (appt["DoctorID"],))
        doc = cursor.fetchone()
        doctor_fee = float(doc["ConsultationFee"]) if doc and doc["ConsultationFee"] else 0.0

        cursor.execute("""
            SELECT RecordID FROM medicalrecord
            WHERE PatientID = %s AND DoctorID = %s ORDER BY RecordID DESC LIMIT 1
        """, (patient_id, appt["DoctorID"]))
        rec = cursor.fetchone()

        medicine_fee = 0.0
        prescription_items = []
        if rec:
            cursor.execute("""
                SELECT pm.MedicineID, pm.Quantity, COALESCE(m.Price,0) AS Price
                FROM prescription pr
                JOIN prescription_medicine pm ON pr.PrescriptionID = pm.PrescriptionID
                JOIN medicine m               ON pm.MedicineID     = m.MedicineID
                WHERE pr.RecordID = %s
            """, (rec["RecordID"],))
            prescription_items = cursor.fetchall()
            for item in prescription_items:
                medicine_fee += float(item["Price"]) * int(item["Quantity"])

        total = doctor_fee + hospital_fee + medicine_fee

        cursor.execute("""
            INSERT INTO bill (PatientID, DoctorFee, HospitalFee, MedicineFee, TotalAmount, Status)
            VALUES (%s,%s,%s,%s,%s,'Pending')
        """, (patient_id, doctor_fee, hospital_fee, medicine_fee, total))
        db.commit()

        for item in prescription_items:
            cursor.execute(
                "UPDATE medicine SET Stock = Stock - %s WHERE MedicineID = %s",
                (item["Quantity"], item["MedicineID"])
            )

    return redirect(f"/admin?tab=billing&msg=Bill+created.+Total+Rs.{total:.2f}")


@app.route("/mark_paid/<int:bill_id>")
@login_required
@role_required("Admin")
def mark_paid(bill_id):
    with get_db() as (db, cursor):
        cursor.execute("UPDATE bill SET Status='Paid' WHERE BillID=%s", (bill_id,))
    return redirect("/admin?tab=billing&msg=Bill+marked+as+paid+(cash)")


@app.route("/api/patient_bill/<int:patient_id>")
@login_required
@role_required("Admin")
def patient_bill(patient_id):
    with get_db() as (db, cursor):
        cursor.execute("""
            SELECT b.*, p.Name AS PatientName FROM bill b
            JOIN patient p ON b.PatientID = p.PatientID
            WHERE b.PatientID = %s ORDER BY b.BillID DESC LIMIT 1
        """, (patient_id,))
        bill = cursor.fetchone()
    if not bill:
        return jsonify({"found": False})
    bill["found"] = True
    for k in ("DoctorFee", "HospitalFee", "MedicineFee", "TotalAmount"):
        if bill.get(k) is not None:
            bill[k] = float(bill[k])
    return jsonify(bill)


# ─────────────────────────────────────────────
#  PAYMENT
# ─────────────────────────────────────────────
@app.route("/pay", methods=["POST"])
@login_required
@role_required("Patient")
def pay():
    bill_id = request.form["BillID"]
    amount  = float(request.form["Amount"])
    if amount <= 0:
        return redirect("/patient?msg=Invalid+payment+amount")

    with get_db() as (db, cursor):
        cursor.execute(
            "INSERT INTO payment (BillID, AmountPaid, PaymentDate) VALUES (%s,%s,CURDATE())",
            (bill_id, amount)
        )
        cursor.execute("SELECT TotalAmount FROM bill WHERE BillID=%s", (bill_id,))
        result = cursor.fetchone()
        if not result:
            return redirect("/patient?msg=Bill+not+found")

        total = float(result["TotalAmount"])
        cursor.execute("SELECT SUM(AmountPaid) AS paid FROM payment WHERE BillID=%s", (bill_id,))
        paid = float(cursor.fetchone()["paid"] or 0)
        if paid >= total:
            cursor.execute("UPDATE bill SET Status='Paid' WHERE BillID=%s", (bill_id,))

    return redirect("/patient?msg=Payment+processed+successfully")


# ─────────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
