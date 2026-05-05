import os
import json
import uuid
import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, session
from flask_cors import CORS

app = Flask(__name__, static_folder='.')
app.secret_key = os.urandom(24)
CORS(app)

SHOPIER_TOKEN = open('api.txt').read().strip()
SHOPIER_API = 'https://api.shopier.com/v1'

SHOPIER_PRODUCTS = {
    10:  46904741,
    20:  46904742,
    50:  46904743,
    100: 46904744,
    200: 46904746,
    500: 46904747,
}

USERS_FILE = 'users.json'
SESSIONS_FILE = 'pay_sessions.json'

def load_users():
    try:
        with open(USERS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f)

def load_pay_sessions():
    try:
        with open(SESSIONS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_pay_sessions(sessions):
    with open(SESSIONS_FILE, 'w') as f:
        json.dump(sessions, f)

def shopier_headers():
    return {
        'Authorization': 'Bearer ' + SHOPIER_TOKEN,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/png/<path:filename>')
def serve_png(filename):
    return send_from_directory('png', filename)

@app.route('/api/create-payment', methods=['POST'])
def create_payment():
    data = request.get_json()
    amount = float(data.get('amount', 0))
    email = data.get('email', '').strip()
    name = data.get('name', '').strip()

    if amount < 10:
        return jsonify({'error': 'Minimum tutar ₺10'}), 400
    if not email:
        return jsonify({'error': 'E-posta gerekli'}), 400

    product_id = None
    for amt in sorted(SHOPIER_PRODUCTS.keys()):
        if int(amount) == amt:
            product_id = SHOPIER_PRODUCTS[amt]
            break

    if not product_id:
        closest = min(SHOPIER_PRODUCTS.keys(), key=lambda x: abs(x - amount))
        product_id = SHOPIER_PRODUCTS[closest]
        amount = closest

    session_id = str(uuid.uuid4())
    pay_sessions = load_pay_sessions()
    pay_sessions[session_id] = {
        'email': email,
        'name': name,
        'amount': amount,
        'product_id': product_id,
        'status': 'pending'
    }
    save_pay_sessions(pay_sessions)

    dev_domain = os.environ.get('REPLIT_DEV_DOMAIN', '')
    if dev_domain:
        callback_base = f'https://{dev_domain}'
    else:
        callback_base = request.host_url.rstrip('/')

    payment_url = f'https://www.shopier.com/{product_id}'

    return jsonify({
        'payment_url': payment_url,
        'session_id': session_id,
        'amount': amount,
        'product_id': product_id
    })

@app.route('/api/payment-verify', methods=['POST'])
def payment_verify():
    data = request.get_json()
    session_id = data.get('session_id', '')
    order_id = data.get('order_id', '')

    pay_sessions = load_pay_sessions()
    sess = pay_sessions.get(session_id)
    if not sess:
        return jsonify({'error': 'Oturum bulunamadı'}), 404

    if sess['status'] == 'completed':
        return jsonify({'success': True, 'amount': sess['amount'], 'already_done': True})

    verified = False
    if order_id:
        try:
            resp = requests.get(
                f'{SHOPIER_API}/orders/{order_id}',
                headers=shopier_headers(),
                timeout=10
            )
            if resp.ok:
                order = resp.json()
                if order.get('paymentStatus') == 'paid':
                    verified = True
        except Exception:
            pass

    if not verified:
        try:
            resp = requests.get(
                f'{SHOPIER_API}/orders',
                headers=shopier_headers(),
                timeout=10
            )
            if resp.ok:
                orders = resp.json()
                for o in orders:
                    if (o.get('paymentStatus') == 'paid' and
                        o.get('shippingInfo', {}).get('email') == sess['email'] and
                        float(o.get('totals', {}).get('total', 0)) == float(sess['amount'])):
                        verified = True
                        break
        except Exception:
            pass

    if verified:
        sess['status'] = 'completed'
        save_pay_sessions(pay_sessions)

        users = load_users()
        email = sess['email']
        if email not in users:
            users[email] = {'balance': 0}
        users[email]['balance'] = round(users[email].get('balance', 0) + sess['amount'], 2)
        save_users(users)

        return jsonify({'success': True, 'amount': sess['amount'], 'new_balance': users[email]['balance']})

    return jsonify({'success': False, 'message': 'Ödeme henüz doğrulanamadı'})

@app.route('/api/payment-success', methods=['GET'])
def payment_success():
    session_id = request.args.get('sid', '')
    order_id = request.args.get('order_id', '') or request.args.get('orderId', '')

    pay_sessions = load_pay_sessions()
    sess = pay_sessions.get(session_id)
    if sess and sess['status'] == 'pending':
        sess['status'] = 'completed'
        save_pay_sessions(pay_sessions)

        users = load_users()
        email = sess['email']
        if email not in users:
            users[email] = {'balance': 0}
        users[email]['balance'] = round(users[email].get('balance', 0) + sess['amount'], 2)
        save_users(users)

    return redirect(f'/?payment_status=success&sid={session_id}')

@app.route('/api/payment-cancel', methods=['GET'])
def payment_cancel():
    return redirect('/?payment_status=cancel')

@app.route('/api/shopier-webhook', methods=['POST'])
def shopier_webhook():
    try:
        data = request.get_json(force=True) or {}
        order_id = str(data.get('orderId') or data.get('id') or '')
        email = data.get('email') or data.get('buyer_email') or ''
        amount = float(data.get('total') or data.get('amount') or 0)
        status = data.get('paymentStatus') or data.get('status') or ''

        if status == 'paid' and email and amount > 0:
            users = load_users()
            if email not in users:
                users[email] = {'balance': 0}
            users[email]['balance'] = round(users[email].get('balance', 0) + amount, 2)
            save_users(users)

        return jsonify({'received': True}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/balance', methods=['GET'])
def get_balance():
    email = request.args.get('email', '').strip()
    if not email:
        return jsonify({'error': 'E-posta gerekli'}), 400
    users = load_users()
    balance = users.get(email, {}).get('balance', 0)
    return jsonify({'email': email, 'balance': balance})

@app.route('/api/sync-balance', methods=['POST'])
def sync_balance():
    data = request.get_json()
    email = data.get('email', '').strip()
    local_balance = float(data.get('local_balance', 0))
    if not email:
        return jsonify({'error': 'E-posta gerekli'}), 400
    users = load_users()
    if email not in users:
        users[email] = {'balance': local_balance}
        save_users(users)
    server_balance = users[email].get('balance', 0)
    final_balance = max(server_balance, local_balance)
    if final_balance != server_balance:
        users[email]['balance'] = final_balance
        save_users(users)
    return jsonify({'balance': final_balance})

@app.route('/api/orders', methods=['GET'])
def get_orders():
    try:
        resp = requests.get(f'{SHOPIER_API}/orders', headers=shopier_headers(), timeout=15)
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
