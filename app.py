import os, sqlite3, hmac, hashlib
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv

load_dotenv()

try:
    import razorpay
except ImportError:
    razorpay = None

app = Flask(__name__)
DB = "revivepay.db"
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
rzp = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) if (razorpay and RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET) else None


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    con = db()
    c = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer TEXT NOT NULL, email TEXT NOT NULL, event TEXT NOT NULL,
        action TEXT NOT NULL, amount REAL NOT NULL, status TEXT NOT NULL,
        reason TEXT NOT NULL, confidence INTEGER NOT NULL,
        razorpay_order_id TEXT, razorpay_payment_id TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS audit_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id INTEGER,
        action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL)""")
    c.execute("INSERT OR IGNORE INTO users(name,email,password) VALUES(?,?,?)",
              ("Demo User","admin@revivepay.com","123456"))
    count = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    if count == 0:
        demo = [
            ("Aarav Sharma","aarav@example.com","Payment Failed","Smart Retry",4999,"Pending","Temporary bank timeout",94),
            ("Neha Kapoor","neha@example.com","Checkout Drop-off","Recovery Reminder",2499,"Pending","Checkout abandoned",87),
            ("Rohan Mehta","rohan@example.com","Subscription Failed","Smart Retry",1299,"Recovered","Temporary bank timeout",92),
            ("Priya Singh","priya@example.com","Payment Failed","Payment Reminder",7500,"Pending","Insufficient funds",89),
            ("Kabir Verma","kabir@example.com","Checkout Drop-off","Alternate Payment",3200,"Recovered","Card expired",92),
            ("Ananya Gupta","ananya@example.com","Payment Failed","Smart Retry",1800,"Pending","Network timeout",91),
        ]
        for row in demo:
            c.execute("""INSERT INTO transactions
                (customer,email,event,action,amount,status,reason,confidence,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (*row, now(), now()))
    con.commit(); con.close()


@app.route("/")
def home():
    return render_template("index.html", razorpay_key=RAZORPAY_KEY_ID)


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    email, password = data.get("email","").strip(), data.get("password","")
    con = db()
    user = con.execute("SELECT name,email FROM users WHERE email=? AND password=?", (email,password)).fetchone()
    con.close()
    if not user:
        return jsonify(success=False, message="Invalid email or password"), 401
    return jsonify(success=True, name=user["name"], email=user["email"], otp="123456")


@app.get("/api/stats")
def stats():
    con = db()
    risk = con.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE status='Pending'").fetchone()[0]
    recovered = con.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE status='Recovered'").fetchone()[0]
    total = con.execute("SELECT COALESCE(SUM(amount),0) FROM transactions").fetchone()[0]
    count = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    actions = con.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    con.close()
    return jsonify(risk=risk, recovered=recovered, total_value=total, total=count,
                   actions=actions, rate=round(recovered/total*100,1) if total else 0,
                   razorpay_configured=bool(rzp))


@app.get("/api/transactions")
def transactions():
    con = db()
    rows = con.execute("SELECT * FROM transactions ORDER BY id DESC").fetchall()
    con.close()
    return jsonify([dict(x) for x in rows])


@app.get("/api/ai/<int:tid>")
def ai(tid):
    con = db()
    t = con.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    con.close()
    if not t: return jsonify(success=False, message="Transaction not found"), 404
    reason = t["reason"].lower()
    if "timeout" in reason or "network" in reason:
        rec, why = "Smart Retry", "The failure looks temporary, so a controlled retry has high recovery potential."
    elif "abandoned" in reason:
        rec, why = "Recovery Reminder", "The customer reached checkout but did not complete payment."
    elif "expired" in reason:
        rec, why = "Alternate Payment", "The stored payment method is expired."
    elif "insufficient" in reason:
        rec, why = "Payment Reminder", "Immediate retry may fail again; remind the customer and retry later."
    else:
        rec, why = "Manual Review", "The reason needs human review before another automated attempt."
    return jsonify(success=True, customer=t["customer"], amount=t["amount"], reason=t["reason"],
                   confidence=t["confidence"], recommendation=rec, rationale=why,
                   stopping_rule="Stop after one automated recovery attempt or immediately after successful payment.")


@app.post("/api/recover/<int:tid>")
def recover(tid):
    con = db()
    t = con.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    if not t:
        con.close(); return jsonify(success=False, message="Transaction not found"), 404
    if t["status"] == "Recovered":
        con.close(); return jsonify(success=False, message="Transaction is already recovered")
    con.execute("UPDATE transactions SET status='Recovered',updated_at=? WHERE id=?", (now(),tid))
    con.execute("INSERT INTO audit_logs(transaction_id,action,details,created_at) VALUES(?,?,?,?)",
                (tid,"AI Recovery Executed",f"Simulated recovery of ₹{t['amount']:,.2f} using {t['action']}.",now()))
    con.commit(); con.close()
    return jsonify(success=True, message=f"₹{t['amount']:,.2f} recovered successfully")


@app.post("/api/orders/<int:tid>")
def create_order(tid):
    if not rzp:
        return jsonify(success=False, configured=False,
                       message="Razorpay Test Mode is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to .env."), 400
    con = db(); t = con.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    if not t:
        con.close(); return jsonify(success=False,message="Transaction not found"),404
    if t["status"] == "Recovered":
        con.close(); return jsonify(success=False,message="Already recovered"),400
    order = rzp.order.create({"amount": int(round(t["amount"]*100)), "currency":"INR",
                              "receipt":f"revivepay_{tid}_{int(datetime.now().timestamp())}",
                              "notes":{"transaction_id":str(tid)}})
    con.execute("UPDATE transactions SET razorpay_order_id=?,updated_at=? WHERE id=?",
                (order["id"],now(),tid))
    con.commit(); con.close()
    return jsonify(success=True, key_id=RAZORPAY_KEY_ID, order_id=order["id"],
                   amount=order["amount"], currency=order["currency"],
                   customer=t["customer"], email=t["email"])


@app.post("/api/verify-payment")
def verify_payment():
    data = request.get_json(silent=True) or {}
    tid = int(data.get("transaction_id",0))
    order_id, payment_id, signature = data.get("razorpay_order_id",""), data.get("razorpay_payment_id",""), data.get("razorpay_signature","")
    con = db(); t = con.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    if not t:
        con.close(); return jsonify(success=False,message="Transaction not found"),404
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), f"{t['razorpay_order_id']}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature) or order_id != t["razorpay_order_id"]:
        con.close(); return jsonify(success=False,message="Payment signature verification failed"),400
    con.execute("""UPDATE transactions SET status='Recovered',razorpay_payment_id=?,updated_at=? WHERE id=?""",
                (payment_id,now(),tid))
    con.execute("INSERT INTO audit_logs(transaction_id,action,details,created_at) VALUES(?,?,?,?)",
                (tid,"Razorpay Test Payment Verified",f"Verified payment {payment_id} for ₹{t['amount']:,.2f}.",now()))
    con.commit(); con.close()
    return jsonify(success=True,message="Payment verified and transaction recovered.")


@app.get("/api/audit")
def audit():
    con = db()
    rows = con.execute("""SELECT a.*,t.customer,t.amount
                         FROM audit_logs a LEFT JOIN transactions t ON a.transaction_id=t.id
                         ORDER BY a.id DESC""").fetchall()
    con.close()
    return jsonify([dict(x) for x in rows])


@app.post("/api/reset-demo")
def reset_demo():
    con = db()
    con.execute("DELETE FROM audit_logs")
    con.execute("UPDATE transactions SET status='Pending',razorpay_order_id=NULL,razorpay_payment_id=NULL,updated_at=?", (now(),))
    con.execute("UPDATE transactions SET status='Recovered',updated_at=? WHERE id=(SELECT MIN(id) FROM transactions)", (now(),))
    con.commit(); con.close()
    return jsonify(success=True,message="Demo data reset successfully.")


@app.errorhandler(404)
def not_found(e):
    return jsonify(success=False,message="Endpoint not found"),404


if __name__ == "__main__":
    init_db()
    print("RevivePay running at http://127.0.0.1:5000")
    print("Demo: admin@revivepay.com / 123456 / OTP 123456")
    app.run(host="127.0.0.1", port=5000, debug=True)
