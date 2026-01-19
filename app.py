from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import os
import pandas as pd
from datetime import datetime
import base64
import uuid
SERVER_INSTANCE_ID = uuid.uuid4().hex

app = Flask(__name__)
app.config.update(
    SESSION_PERMANENT=False,
    SESSION_REFRESH_EACH_REQUEST=True
)

app.secret_key = "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET"

# CTRL+F: BASE_DIR
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# CTRL+F: DATA_DIR
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
MAINT_FILE = os.path.join(DATA_DIR, "maintenance.xlsx")
WASH_FILE = os.path.join(DATA_DIR, "wash.xlsx")
USERS_FILE = os.path.join(DATA_DIR, "users.xlsx")
SIG_DIR = os.path.join(DATA_DIR, "signatures")

# ==============================
# CTRL+F: COLUMNS
# Unified schema for both files
# ==============================
COLUMNS = [
    "id",
    "service_key",         # maintenance | wash
    "service_type",        # صيانة | غسيل
    "description",         # وصف الصيانة (أو '-' في الغسيل)
    "note",                # ملاحظة للغسيل (اختياري)
    "date",                # yyyy-mm-dd
    "employee",
    "vehicle_id",          # اللوحة/الهيكل
    "start_time",          # HH:MM
    "end_time",            # HH:MM
    "total_minutes",
    "total_text",
    "signature_file",
    "created_at"
]

# ===== CREATE/UPGRADE EXCEL SCHEMAS =====
def ensure_dirs_and_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(SIG_DIR, exist_ok=True)

    # Create/upgrade maintenance + wash schemas
    for path in [MAINT_FILE, WASH_FILE]:
        if not os.path.exists(path):
            pd.DataFrame(columns=COLUMNS).to_excel(path, index=False)
        else:
            df = pd.read_excel(path)
            changed = False
            for col in COLUMNS:
                if col not in df.columns:
                    df[col] = ""
                    changed = True
            # Keep only columns we care about (and in same order)
            df = df[COLUMNS]
            if changed:
                df.to_excel(path, index=False)

    # Create users file if missing
    if not os.path.exists(USERS_FILE):
        users_df = pd.DataFrame([
            {"username": "maint1", "password": "1234", "role": "maintenance", "department": "ورشة"},
            {"username": "wash1",  "password": "1234", "role": "wash",        "department": "مغسلة"},
            {"username": "admin",  "password": "admin", "role": "admin",     "department": "الإدارة"},
        ])
        users_df.to_excel(USERS_FILE, index=False)


def load_users():
    return pd.read_excel(USERS_FILE, dtype=str).fillna("")


def auth_required(roles=None):
    def wrapper(fn):
        def inner(*args, **kwargs):
            # لا توجد جلسة
            if "user" not in session:
                return redirect(url_for("login"))

            # الجلسة من تشغيل سيرفر قديم
            if session.get("server_id") != SERVER_INSTANCE_ID:
                session.clear()
                return redirect(url_for("login"))

            # صلاحية غير مسموحة
            if roles and session.get("role") not in roles:
                return redirect(url_for("work"))

            return fn(*args, **kwargs)
        inner.__name__ = fn.__name__
        return inner
    return wrapper



def parse_hhmm(t):
    """t = 'HH:MM' -> (H, M)"""
    hh, mm = t.split(":")
    return int(hh), int(mm)


def calc_total_minutes_from_time(start_hhmm, end_hhmm):
    sh, sm = parse_hhmm(start_hhmm)
    eh, em = parse_hhmm(end_hhmm)
    start = sh * 60 + sm
    end = eh * 60 + em

    diff = end - start
    # دعم عبور منتصف الليل (إذا احتجته)
    if diff < 0:
        diff += 24 * 60
    return diff


def minutes_to_text(total_minutes):
    h = total_minutes // 60
    m = total_minutes % 60
    if h == 0:
        return f"{m} دقيقة"
    if m == 0:
        return f"{h} ساعة"
    return f"{h} ساعة و {m} دقيقة"


def save_signature(data_url):
    # data_url: "data:image/png;base64,...."
    if not data_url or "base64," not in data_url:
        return ""
    b64 = data_url.split("base64,", 1)[1]
    img_bytes = base64.b64decode(b64)
    filename = f"sig_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    out_path = os.path.join(SIG_DIR, filename)
    with open(out_path, "wb") as f:
        f.write(img_bytes)
    return filename


def append_record(excel_path, row_dict):
    df = pd.read_excel(excel_path)
    # upgrade if needed
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLUMNS]

    df.loc[len(df)] = [row_dict.get(c, "") for c in COLUMNS]
    df.to_excel(excel_path, index=False)


def read_all_records():
    ensure_dirs_and_files()

    maint = pd.read_excel(MAINT_FILE)
    wash = pd.read_excel(WASH_FILE)

    # ضع أعمدة مفقودة إن وجدت
    for df0 in (maint, wash):
        for c in COLUMNS:
            if c not in df0.columns:
                df0[c] = ""
        df0 = df0[COLUMNS]

    # مصدر ثابت حسب الملف (حتى لو service_key فاضي)
    maint["source"] = "maintenance"
    wash["source"] = "wash"

    df = pd.concat([maint, wash], ignore_index=True)

    # source النهائي: service_key إذا صحيح وإلا source الأصلي
    def resolve_source(row):
        sk = str(row.get("service_key", "")).strip()
        if sk in ("maintenance", "wash"):
            return sk
        return str(row.get("source", "")).strip()

    df["source"] = df.apply(resolve_source, axis=1)

    # normalize types
    df["date"] = df.get("date", "").astype(str)
    df["employee"] = df.get("employee", "").astype(str)
    df["vehicle_id"] = df.get("vehicle_id", "").astype(str)
    df["start_time"] = df.get("start_time", "").astype(str)
    df["end_time"] = df.get("end_time", "").astype(str)
    df["created_at"] = df.get("created_at", "").astype(str)

    df["total_minutes"] = pd.to_numeric(df.get("total_minutes", 0), errors="coerce").fillna(0).astype(int)

    # enforce schema after merge
    for c in COLUMNS + ["source"]:
        if c not in df.columns:
            df[c] = ""
    df = df.fillna("")

    return df

def filter_records(df, args):
    hour = args.get("hour", "")
    service = args.get("service", "all")  # all/maintenance/wash
    date_from = args.get("date_from", "")
    date_to = args.get("date_to", "")
    employee = args.get("employee", "")
    vehicle_id = args.get("vehicle_id", "")
    min_minutes = args.get("min_minutes", "")
    max_minutes = args.get("max_minutes", "")
    q = args.get("q", "")

    if hour:
        hh = hour.zfill(2)
        df = df[df["start_time"].astype(str).str.startswith(hh)]

    if service in ["maintenance", "wash"]:
        df = df[df["source"] == service]

    # date filtering assumes yyyy-mm-dd
    def to_date_safe(x):
        try:
            return datetime.strptime(str(x)[:10], "%Y-%m-%d")
        except:
            return None

    if date_from:
        dfrom = to_date_safe(date_from)
        if dfrom:
            df = df[df["date"].apply(lambda v: to_date_safe(v) is not None and to_date_safe(v) >= dfrom)]

    if date_to:
        dto = to_date_safe(date_to)
        if dto:
            df = df[df["date"].apply(lambda v: to_date_safe(v) is not None and to_date_safe(v) <= dto)]

    if employee:
        df = df[df["employee"].str.contains(employee, case=False, na=False)]

    if vehicle_id:
        df = df[df["vehicle_id"].str.contains(vehicle_id, case=False, na=False)]

    if min_minutes:
        try:
            mn = int(min_minutes)
            df = df[df["total_minutes"] >= mn]
        except:
            pass

    if max_minutes:
        try:
            mx = int(max_minutes)
            df = df[df["total_minutes"] <= mx]
        except:
            pass

    if q:
        ql = q.lower()
        def row_match(r):
            s = " ".join([
                str(r.get("description","")),
                str(r.get("note","")),
                str(r.get("employee","")),
                str(r.get("vehicle_id","")),
                str(r.get("service_type","")),
                str(r.get("date","")),
            ]).lower()
            return ql in s
        df = df[df.apply(row_match, axis=1)]

    return df


@app.route("/")
def home():
    if "user" in session:
        return redirect(url_for("work"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    ensure_dirs_and_files()
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","").strip()
        users = load_users()
        hit = users[(users["username"] == username) & (users["password"] == password)]
        if len(hit) == 1:
            session.clear()
            row = hit.iloc[0].to_dict()
            session["user"] = row["username"]
            session["role"] = row["role"]
            session["department"] = row.get("department", "")
            session["server_id"] = SERVER_INSTANCE_ID
            return redirect(url_for("work"))
        return render_template("login.html", error="بيانات الدخول غير صحيحة")
    return render_template("login.html", error="")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/work")
@auth_required(roles=["maintenance","wash","admin"])
def work():
    role = session.get("role")

    if role == "admin":
        return redirect(url_for("dashboard"))

    return render_template("work.html", role=role, user_name=session.get("user",""))

# ==============================
# CTRL+F: SUBMIT ROUTES
# Your forms use /submit/maintenance and /submit/wash
# ==============================
@app.route("/submit/maintenance", methods=["POST"])
@auth_required(roles=["maintenance","admin"])
def submit_maintenance():
    ensure_dirs_and_files()

    description = (request.form.get("description") or "").strip()
    date = (request.form.get("date") or "").strip()
    employee = (request.form.get("employee") or "").strip()
    vehicle_id = (request.form.get("vehicle_id") or "").strip()
    start_time = (request.form.get("start_time") or "").strip()
    end_time = (request.form.get("end_time") or "").strip()
    signature_data = (request.form.get("signature_data") or "").strip()

    if not (description and date and employee and vehicle_id and start_time and end_time and signature_data):
        return jsonify({"ok": False, "msg": "فضلاً أكمل جميع الحقول المطلوبة واعتمد التوقيع."}), 400

    total_minutes = calc_total_minutes_from_time(start_time, end_time)
    total_text = minutes_to_text(total_minutes)
    sig_file = save_signature(signature_data)

    row = {
        "id": uuid.uuid4().hex[:10],
        "service_key": "maintenance",
        "service_type": "صيانة",
        "description": description,
        "note": "",
        "date": date,
        "employee": employee,
        "vehicle_id": vehicle_id,
        "start_time": start_time,
        "end_time": end_time,
        "total_minutes": int(total_minutes),
        "total_text": total_text,
        "signature_file": sig_file,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    append_record(MAINT_FILE, row)

    return jsonify({"ok": True, "msg": "تم حفظ عملية الصيانة بنجاح", "total_text": total_text})


@app.route("/submit/wash", methods=["POST"])
@auth_required(roles=["maintenance","admin"])

def submit_wash():
    ensure_dirs_and_files()

    date = (request.form.get("date") or "").strip()
    employee = (request.form.get("employee") or "").strip()
    vehicle_id = (request.form.get("vehicle_id") or "").strip()
    start_time = (request.form.get("start_time") or "").strip()
    end_time = (request.form.get("end_time") or "").strip()
    note = (request.form.get("note") or "").strip()
    signature_data = (request.form.get("signature_data") or "").strip()

    if not (date and employee and vehicle_id and start_time and end_time and signature_data):
        return jsonify({"ok": False, "msg": "فضلاً أكمل جميع الحقول المطلوبة واعتمد التوقيع."}), 400

    total_minutes = calc_total_minutes_from_time(start_time, end_time)
    total_text = minutes_to_text(total_minutes)
    sig_file = save_signature(signature_data)

    row = {
        "id": uuid.uuid4().hex[:10],
        "service_key": "wash",
        "service_type": "غسيل",
        "description": "-",
        "note": note,
        "date": date,
        "employee": employee,
        "vehicle_id": vehicle_id,
        "start_time": start_time,
        "end_time": end_time,
        "total_minutes": int(total_minutes),
        "total_text": total_text,
        "signature_file": sig_file,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    append_record(WASH_FILE, row)

    return jsonify({"ok": True, "msg": "تم حفظ عملية الغسيل بنجاح", "total_text": total_text})

@app.route("/signature/<filename>")
@auth_required(roles=["admin"])
def view_signature(filename):
    path = os.path.join(SIG_DIR, filename)
    if not os.path.exists(path):
        return "Not Found", 404
    return send_file(path)

# ==============================
# DASHBOARD (ADMIN ONLY)
# ==============================
@app.route("/dashboard")
@auth_required(roles=["admin"])
def dashboard():
    return render_template("dashboard.html", user_name=session.get("user",""))


# CTRL+F: api_analytics
@app.route("/api/analytics")
@auth_required(roles=["admin"])
def api_analytics():
    df = read_all_records()
    df = filter_records(df, request.args)

    total = len(df)
    maint_count = int((df["source"] == "maintenance").sum())
    wash_count = int((df["source"] == "wash").sum())

    if total > 0:
        avg_minutes = int(df["total_minutes"].mean())
        med_minutes = int(df["total_minutes"].median())
        min_minutes = int(df["total_minutes"].min())
        max_minutes = int(df["total_minutes"].max())
        sum_minutes = int(df["total_minutes"].sum())
    else:
        avg_minutes = med_minutes = min_minutes = max_minutes = sum_minutes = 0

    # daily trend
    daily = (
        df.groupby(["date"])["id"].count()
          .reset_index()
          .rename(columns={"id":"count"})
          .sort_values("date")
          .to_dict(orient="records")
    )

    # service trend (daily by service)
    svc_daily = (
        df.groupby(["date","source"])["id"].count()
          .reset_index()
          .rename(columns={"id":"count"})
          .sort_values(["date","source"])
          .to_dict(orient="records")
    )

    # top employees by count + avg duration
    if total > 0:
        emp = (
            df.groupby("employee")
              .agg(count=("id","count"), avg=("total_minutes","mean"), sum=("total_minutes","sum"))
              .reset_index()
        )
        emp["avg"] = emp["avg"].round(0).astype(int)
        emp["sum"] = emp["sum"].astype(int)
        emp = emp.sort_values("count", ascending=False).head(15)
        top_employees = emp.to_dict(orient="records")
    else:
        top_employees = []

    # top vehicles
    if total > 0:
        top_vehicles = (
            df.groupby("vehicle_id")["id"].count()
              .sort_values(ascending=False)
              .head(15)
              .reset_index()
              .rename(columns={"id":"count"})
              .to_dict(orient="records")
        )
    else:
        top_vehicles = []

    # buckets (SLA)
    def bucket(m):
        if m <= 30: return "0-30"
        if m <= 60: return "31-60"
        if m <= 120: return "61-120"
        return "120+"

    if total > 0:
        tmp = df.copy()
        tmp["bucket"] = tmp["total_minutes"].apply(bucket)
        buckets = (
            tmp.groupby("bucket")["id"].count()
               .reindex(["0-30","31-60","61-120","120+"], fill_value=0)
               .reset_index()
               .rename(columns={"id":"count"})
               .to_dict(orient="records")
        )
    else:
        buckets = [{"bucket":"0-30","count":0},{"bucket":"31-60","count":0},{"bucket":"61-120","count":0},{"bucket":"120+","count":0}]

    # hours distribution (by start_time hour)
    # hours distribution (by start_time hour)
    def hour_of(t):
        try:
            return int(str(t).split(":")[0])
        except:
            return None

    if total > 0:
        df2 = df.copy()
        df2["hour"] = df2["start_time"].apply(hour_of)
        df2 = df2[df2["hour"].notna()]

        hours = (
            df2.groupby("hour")
            .size()
            .reset_index(name="count")
            .sort_values("hour")
            .to_dict(orient="records")
        )
    else:
        hours = []

    # recent rows

    df["_created"] = pd.to_datetime(df["created_at"], errors="coerce")
    rows = df.sort_values("_created", ascending=False).head(2000).drop(columns=["_created"]).to_dict(orient="records")

    resp = jsonify({
        "ok": True,
        "stats": {
            "total": total,
            "maintenance": maint_count,
            "wash": wash_count,
            "avg_minutes": avg_minutes,
            "avg_text": minutes_to_text(avg_minutes),
            "median_text": minutes_to_text(med_minutes),
            "min_text": minutes_to_text(min_minutes),
            "max_text": minutes_to_text(max_minutes),
            "sum_text": minutes_to_text(sum_minutes),
        },
        "daily": daily,
        "svc_daily": svc_daily,
        "top_employees": top_employees,
        "top_vehicles": top_vehicles,
        "buckets": buckets,
        "hours": hours,
        "rows": rows
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.route("/export")
@auth_required(roles=["admin"])
def export():
    df = read_all_records()
    df = filter_records(df, request.args)
    out_path = os.path.join(DATA_DIR, f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
    df.to_excel(out_path, index=False)
    return send_file(out_path, as_attachment=True)


app.run(
    host="0.0.0.0",
    port=5000,
    debug=False
)



