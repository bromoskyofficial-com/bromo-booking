import os
import json
import random
import string
from datetime import datetime

import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv

import smtplib
from email.message import EmailMessage
from werkzeug.utils import secure_filename


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "dev_secret")

# ===== CONFIG =====
GAS_WEBAPP_URL = os.getenv("GAS_WEBAPP_URL", "").strip()

SMTP_EMAIL = os.getenv("SMTP_EMAIL", "").strip()
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123").strip()

DP_PERCENT = float(os.getenv("DP_PERCENT", "0.3"))  # 0.3 = 30%

# Upload bukti transfer
UPLOAD_FOLDER = os.path.join("static", "uploads", "bukti")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}


# ===== STATUS INDONESIA =====
STATUS_MENUNGGU = "MENUNGGU"
STATUS_SUDAH_BAYAR = "SUDAH BAYAR"
STATUS_DIKONFIRMASI = "DIKONFIRMASI"
STATUS_DIBATALKAN = "DIBATALKAN"

STATUS_OPTIONS = [STATUS_MENUNGGU, STATUS_SUDAH_BAYAR, STATUS_DIKONFIRMASI, STATUS_DIBATALKAN]


# ===== PAKET & HARGA =====
PAKET_PRICING = {
    "Open Trip 300.000/Orang": {"type": "per_orang", "price": 300000},
    "Open Trip Dokumentasi 350.000/Orang": {"type": "per_orang", "price": 350000},
    "Private Trip 1.750.000/Jeep Maximal 6 Orang": {"type": "per_jeep", "price": 1750000, "max": 6},
    "Private Trip Dokumentasi 1.950.000/Jeep Maximal 5 Orang": {"type": "per_jeep", "price": 1950000, "max": 5},
}


def rupiah(n: int) -> str:
    s = f"{n:,}".replace(",", ".")
    return f"Rp {s}"


def indonesian_date(dt: datetime) -> str:
    bulan = [
        "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember"
    ]
    return f"{dt.day:02d} {bulan[dt.month-1]} {dt.year}"


def format_tanggal(tanggal_str: str) -> str:
    """
    Terima string ISO seperti 2026-02-03T17:00:00.000Z atau 2026-02-03
    output: 03 Februari 2026
    """
    try:
        s = (tanggal_str or "").strip()
        if not s:
            return ""
        # kalau ada Z, ubah jadi +00:00 biar fromisoformat aman
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return indonesian_date(dt)
    except:
        # fallback kalau format tidak dikenal
        try:
            dt = datetime.strptime(tanggal_str[:10], "%Y-%m-%d")
            return indonesian_date(dt)
        except:
            return tanggal_str


def generate_invoice_id() -> str:
    today = datetime.now().strftime("%y%m%d")
    rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"BSM-{today}-{rand}"


def calc_total(paket: str, jumlah: int) -> int:
    info = PAKET_PRICING.get(paket)
    if not info:
        return 0

    if info["type"] == "per_orang":
        return info["price"] * jumlah

    return info["price"]


def calc_dp_sisa(total: int) -> tuple[int, int]:
    dp = int(round(total * DP_PERCENT))
    sisa = max(total - dp, 0)
    return dp, sisa


def allowed_file(filename: str) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXT


def send_email(to_email: str, subject: str, body: str) -> None:
    if not (SMTP_EMAIL and SMTP_APP_PASSWORD and to_email):
        return

    msg = EmailMessage()
    msg["From"] = SMTP_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
        server.send_message(msg)


def build_status_email(inv: dict, new_status: str) -> tuple[str, str]:
    invoice_id = inv.get("invoice_id", "")
    nama = inv.get("nama", "")
    paket = inv.get("paket", "")
    jumlah = inv.get("jumlah", "")
    tanggal = format_tanggal(str(inv.get("tanggal", "")))
    total = int(inv.get("total") or 0)
    dp = int(inv.get("dp") or 0)
    sisa = int(inv.get("sisa") or 0)

    if new_status == STATUS_SUDAH_BAYAR:
        judul = "Pembayaran Diterima"
        catatan = (
            "Terima kasih! Pembayaran kamu sudah kami terima.\n"
            "Tim kami akan melakukan verifikasi, lalu status akan berubah menjadi DIKONFIRMASI.\n"
        )
    elif new_status == STATUS_DIKONFIRMASI:
        judul = "Booking Dikonfirmasi"
        catatan = (
            "Booking kamu sudah DIKONFIRMASI.\n"
            "Silakan tunggu informasi meeting point / rundown dari admin.\n"
        )
    elif new_status == STATUS_DIBATALKAN:
        judul = "Booking Dibatalkan"
        catatan = (
            "Booking kamu DIBATALKAN.\n"
            "Jika ini tidak sesuai, balas email ini atau hubungi admin Bromo Sky.\n"
        )
    else:
        judul = "Menunggu Konfirmasi"
        catatan = (
            "Booking kamu sudah masuk dan sedang MENUNGGU konfirmasi.\n"
            "Admin akan memproses secepatnya.\n"
        )

    subject = f"[{new_status}] Invoice {invoice_id} - Bromo Sky Aventra"

    body = f"""Halo {nama},

Update status booking kamu:

STATUS: {new_status} - {judul}

Rincian Booking:
- Invoice: {invoice_id}
- Tanggal Trip: {tanggal}
- Paket: {paket}
- Jumlah: {jumlah}
- Total: {rupiah(total)}
- DP: {rupiah(dp)}
- Sisa: {rupiah(sisa)}

Catatan:
{catatan}

Terima kasih,
Bromo Sky Aventra
"""
    return subject, body


def save_to_sheet(payload: dict) -> tuple[bool, str]:
    if not GAS_WEBAPP_URL:
        return False, "GAS_WEBAPP_URL belum diisi di .env"

    try:
        resp = requests.post(
            GAS_WEBAPP_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=20
        )
        data = resp.json()
        return (data.get("ok") is True, data.get("message") or data.get("error") or "Unknown")
    except Exception as e:
        return False, str(e)


def get_invoice(invoice_id: str) -> tuple[bool, dict | None, str]:
    if not GAS_WEBAPP_URL:
        return False, None, "GAS_WEBAPP_URL belum diisi di .env"

    try:
        resp = requests.get(GAS_WEBAPP_URL, params={"invoice_id": invoice_id}, timeout=20)
        data = resp.json()
        if data.get("ok") is True:
            return True, data.get("data"), "OK"
        return False, None, data.get("error", "Invoice tidak ditemukan")
    except Exception as e:
        return False, None, str(e)


def list_invoices() -> tuple[bool, list | None, str]:
    if not GAS_WEBAPP_URL:
        return False, None, "GAS_WEBAPP_URL belum diisi di .env"

    try:
        resp = requests.get(GAS_WEBAPP_URL, params={"action": "list"}, timeout=20)
        data = resp.json()
        if data.get("ok") is True:
            return True, data.get("data"), "OK"
        return False, None, data.get("error", "Gagal ambil data list")
    except Exception as e:
        return False, None, str(e)


def update_status(invoice_id: str, status: str) -> tuple[bool, str]:
    if not GAS_WEBAPP_URL:
        return False, "GAS_WEBAPP_URL belum diisi di .env"

    try:
        resp = requests.post(
            GAS_WEBAPP_URL,
            params={"action": "update_status"},
            data=json.dumps({"invoice_id": invoice_id, "status": status}),
            headers={"Content-Type": "application/json"},
            timeout=20
        )
        data = resp.json()
        return (data.get("ok") is True, data.get("message") or data.get("error") or "Unknown")
    except Exception as e:
        return False, str(e)


# ================= ROUTES =================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/booking", methods=["GET", "POST"])
def booking():
    if request.method == "GET":
        paket_list = list(PAKET_PRICING.keys())
        # kirim map harga ke JS
        pricing_js = {}
        for k, v in PAKET_PRICING.items():
            pricing_js[k] = v
        return render_template("booking.html", paket_list=paket_list, pricing_js=pricing_js, dp_percent=int(DP_PERCENT * 100))

    nama = request.form.get("nama", "").strip()
    no_hp = request.form.get("no_hp", "").strip()
    email = request.form.get("email", "").strip()
    paket = request.form.get("paket", "").strip()
    tanggal = request.form.get("tanggal", "").strip()
    alamat = request.form.get("alamat", "").strip()

    try:
        jumlah = int(request.form.get("jumlah", "1"))
    except:
        jumlah = 1

    if not nama or not no_hp or not email or not paket or not tanggal or not alamat:
        flash("Mohon lengkapi semua data.", "error")
        return redirect(url_for("booking"))

    if paket not in PAKET_PRICING:
        flash("Paket tidak valid. Silakan pilih dari daftar.", "error")
        return redirect(url_for("booking"))

    info = PAKET_PRICING[paket]
    if info["type"] == "per_jeep" and info.get("max") and jumlah > info["max"]:
        flash(f"Jumlah peserta melebihi kapasitas paket ini (maks {info['max']} orang).", "error")
        return redirect(url_for("booking"))

    invoice_id = generate_invoice_id()
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = calc_total(paket, jumlah)
    dp, sisa = calc_dp_sisa(total)

    # upload bukti transfer (opsional)
    bukti_url = ""
    bukti_file = request.files.get("bukti")
    if bukti_file and bukti_file.filename:
        if not allowed_file(bukti_file.filename):
            flash("Format bukti transfer harus gambar (jpg/png/jpeg/webp).", "error")
            return redirect(url_for("booking"))

        filename = secure_filename(bukti_file.filename)
        ext = filename.rsplit(".", 1)[1].lower()
        new_name = f"{invoice_id}_bukti.{ext}"
        save_path = os.path.join(UPLOAD_FOLDER, new_name)
        bukti_file.save(save_path)
        bukti_url = f"/static/uploads/bukti/{new_name}"

    payload = {
        "invoice_id": invoice_id,
        "created_at": created_at,
        "nama": nama,
        "no_hp": no_hp,
        "email": email,
        "paket": paket,
        "jumlah": jumlah,
        "tanggal": tanggal,
        "alamat": alamat,
        "total": total,
        "dp": dp,
        "sisa": sisa,
        "bukti_url": bukti_url,
        "status": STATUS_MENUNGGU
    }

    ok, msg = save_to_sheet(payload)
    if not ok:
        flash(f"Gagal simpan ke Google Sheets: {msg}", "error")
        return render_template("booking_result.html", success=False, error=msg)

    # Email invoice awal
    send_email(
        email,
        f"Invoice Booking {invoice_id} - Bromo Sky Aventra",
        f"""Halo {nama},

Terima kasih sudah booking di Bromo Sky Aventra.

INVOICE: {invoice_id}
Tanggal Trip: {format_tanggal(tanggal)}
Paket: {paket}
Jumlah: {jumlah}

Total: {rupiah(total)}
DP ({int(DP_PERCENT*100)}%): {rupiah(dp)}
Sisa: {rupiah(sisa)}

Status: {STATUS_MENUNGGU}

Simpan invoice ini untuk cek status.

Salam,
Bromo Sky Aventra
"""
    )

    return render_template(
        "booking_result.html",
        success=True,
        data=payload,
        total_rp=rupiah(total),
        dp_rp=rupiah(dp),
        sisa_rp=rupiah(sisa),
        tanggal_rp=format_tanggal(tanggal),
        bukti_url=bukti_url
    )


@app.route("/invoice_check", methods=["GET", "POST"])
def invoice_check():
    if request.method == "GET":
        return render_template("invoice_check.html")

    invoice_id = request.form.get("invoice_id", "").strip()
    if not invoice_id:
        flash("Masukkan Invoice ID dulu ya.", "error")
        return redirect(url_for("invoice_check"))

    return redirect(url_for("invoice_view", invoice_id=invoice_id))


@app.route("/invoice/<invoice_id>")
def invoice_view(invoice_id):
    ok, data, msg = get_invoice(invoice_id)
    if not ok:
        return render_template("invoice_view.html", found=False, error=msg, invoice_id=invoice_id)

    total = int(data.get("total") or 0)
    dp = int(data.get("dp") or 0)
    sisa = int(data.get("sisa") or 0)
    tanggal_rp = format_tanggal(str(data.get("tanggal", "")))
    bukti_url = str(data.get("bukti_url", "") or "")

    return render_template(
        "invoice_view.html",
        found=True,
        data=data,
        total_rp=rupiah(total),
        dp_rp=rupiah(dp),
        sisa_rp=rupiah(sisa),
        tanggal_rp=tanggal_rp,
        bukti_url=bukti_url
    )


# ================= ADMIN (SESSION) =================

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return render_template("admin_login.html")

    password = request.form.get("password", "").strip()
    if password != ADMIN_PASSWORD:
        flash("Password admin salah.", "error")
        return redirect(url_for("admin_login"))

    session["is_admin"] = True
    flash("Login admin berhasil.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    flash("Berhasil logout.", "success")
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("is_admin"):
        flash("Silakan login admin dulu.", "error")
        return redirect(url_for("admin_login"))

    ok, rows, msg = list_invoices()
    if not ok:
        return render_template("admin_dashboard.html", rows=[], error=msg, status_options=STATUS_OPTIONS)

    # rapikan tanggal + pastikan dp/sisa ada (kalau data lama belum punya)
    for r in rows:
        r["tanggal_rp"] = format_tanggal(str(r.get("tanggal", "")))

        total = int(r.get("total") or 0)
        dp = int(r.get("dp") or 0)
        sisa = int(r.get("sisa") or 0)
        if dp == 0 and total > 0:
            dp, sisa = calc_dp_sisa(total)
        r["dp"] = dp
        r["sisa"] = sisa

        # status default jika kosong
        if not r.get("status"):
            r["status"] = STATUS_MENUNGGU

    return render_template("admin_dashboard.html", rows=rows, error=None, status_options=STATUS_OPTIONS)


@app.route("/admin/update_status", methods=["POST"])
def admin_update_status():
    if not session.get("is_admin"):
        flash("Silakan login admin dulu.", "error")
        return redirect(url_for("admin_login"))

    invoice_id = request.form.get("invoice_id", "").strip()
    new_status = request.form.get("status", "").strip()

    if not invoice_id or not new_status:
        flash("invoice_id dan status wajib diisi.", "error")
        return redirect(url_for("admin_dashboard"))

    # ambil invoice sebelum update untuk cek old_status + data email
    ok_before, inv_before, _ = get_invoice(invoice_id)
    old_status = str(inv_before.get("status") or "").strip() if (ok_before and inv_before) else ""

    ok, msg = update_status(invoice_id, new_status)
    if not ok:
        flash(f"Gagal update status: {msg}", "error")
        return redirect(url_for("admin_dashboard"))

    flash("Status berhasil diupdate.", "success")

    # kirim email kalau status berubah
    if SMTP_EMAIL and SMTP_APP_PASSWORD and ok_before and inv_before and old_status != new_status:
        subject, body = build_status_email(inv_before, new_status)
        try:
            send_email(inv_before.get("email", ""), subject, body)
            flash("Email status terkirim ke customer.", "success")
        except Exception as e:
            flash(f"Status tersimpan, tapi email gagal dikirim: {e}", "error")

    return redirect(url_for("admin_dashboard"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

