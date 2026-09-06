# RevivePay — AI Revenue Recovery Platform

A full-stack Flask + SQLite project for a hackathon/demo. It includes login + OTP, persistent transactions, explainable recovery decisions, analytics, audit logs, and optional Razorpay **Test Mode** Checkout.

## Run

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

Demo login:
- Email: `admin@revivepay.com`
- Password: `123456`
- OTP: `123456`

## Razorpay Test Mode

Copy `.env.example` to `.env` and add your **Test Mode** Key ID and Key Secret.

```env
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
```

Restart Flask. The **Pay via Razorpay Test Mode** button will appear for pending transactions.

The server creates an Order, sends its `order_id` to Checkout, then verifies the returned payment signature server-side before marking the transaction recovered.

Do not commit `.env` or live credentials.

## Important

Without Razorpay test credentials, the app still runs fully in local demo mode; the demo recovery button updates the SQLite database and audit log. Razorpay Checkout requires your own Test Mode credentials.
