#!/usr/bin/env python3
import os
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify

app = Flask(__name__)
DATABASE_URL = os.environ.get("DATABASE_URL")
API_SECRET   = os.environ.get("API_SECRET", "samokat_secret_2024")

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            key           TEXT PRIMARY KEY,
            phone         TEXT,
            session_token TEXT,
            access_token  TEXT,
            proxy         TEXT,
            status        TEXT DEFAULT 'free',
            created_at    TEXT,
            used_at       TEXT
        )
    """)
    conn.commit(); cur.close(); conn.close()

HTML_ACTIVATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Вход в Самокат</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:white;border-radius:24px;padding:48px 32px;max-width:380px;width:100%;text-align:center;box-shadow:0 8px 40px rgba(0,0,0,.12)}
.logo{font-size:64px;margin-bottom:20px}
h1{font-size:24px;color:#1a1a1a;margin-bottom:10px;font-weight:700}
p{color:#999;font-size:15px;margin-bottom:32px;line-height:1.5}
.spinner{width:44px;height:44px;border:4px solid #f0f0f0;border-top:4px solid #FF4B4B;border-radius:50%;animation:spin .8s linear infinite;margin:0 auto 32px}
@keyframes spin{to{transform:rotate(360deg)}}
.btn{display:inline-block;padding:16px 40px;background:#FF4B4B;color:white;border-radius:14px;text-decoration:none;font-weight:700;font-size:17px}
</style>
</head>
<body>
<div class="card">
  <div class="logo">🛒</div>
  <h1>Входим в Самокат...</h1>
  <p>Секунду, авторизуем тебя</p>
  <div class="spinner"></div>
  <a href="https://samokat.ru" class="btn">Открыть Самокат →</a>
</div>
<script>
var t = "{token}";
var e = "Fri, 01 Jan 2027 00:00:00 GMT";
document.cookie = "__Secure-next-auth.session-token=" + t + "; domain=samokat.ru; path=/; expires=" + e + "; SameSite=Lax; Secure";
document.cookie = "next-auth.session-token=" + t + "; domain=.samokat.ru; path=/; expires=" + e + "; SameSite=Lax";
setTimeout(function() { window.location.href = "https://samokat.ru"; }, 1200);
</script>
</body>
</html>"""

HTML_ERROR = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ошибка</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,sans-serif;background:#f5f5f5;display:flex;align-items:center;justify-content:center;min-height:100vh}.card{background:white;border-radius:24px;padding:48px 32px;max-width:380px;width:100%;text-align:center;box-shadow:0 8px 40px rgba(0,0,0,.12)}.logo{font-size:64px;margin-bottom:20px}h1{font-size:22px;color:#1a1a1a;margin-bottom:12px;font-weight:700}p{color:#999;font-size:15px}</style>
</head><body><div class="card"><div class="logo">{icon}</div><h1>{title}</h1><p>{msg}</p></div></body></html>"""

@app.route("/")
def index():
    return "OK", 200

@app.route("/activate")
def activate():
    key = request.args.get("key", "").strip().upper()
    if not key:
        return HTML_ERROR.format(icon="❌", title="Нет ключа", msg="Ключ не указан в ссылке"), 400
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM keys WHERE key=%s", (key,))
        row = cur.fetchone()
        cur.close(); conn.close()
    except Exception as e:
        return HTML_ERROR.format(icon="⚠️", title="Ошибка", msg=str(e)), 500
    if not row:
        return HTML_ERROR.format(icon="🔍", title="Ключ не найден", msg="Проверь ключ и попробуй снова"), 404
    return HTML_ACTIVATE.format(token=row["session_token"]), 200

def auth():
    return request.headers.get("X-Secret") == API_SECRET

@app.route("/api/keys", methods=["GET"])
def api_list():
    if not auth(): return jsonify({"error":"forbidden"}), 403
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT key,phone,proxy,status,created_at FROM keys ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); conn.close()
    return jsonify(rows)

@app.route("/api/keys", methods=["POST"])
def api_add():
    if not auth(): return jsonify({"error":"forbidden"}), 403
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO keys (key,phone,session_token,access_token,proxy,status,created_at) VALUES (%s,%s,%s,%s,%s,'free',%s) ON CONFLICT (key) DO NOTHING",
        (d["key"], d["phone"], d["session_token"], d.get("access_token",""), d.get("proxy",""), d["created_at"])
    )
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/api/keys/<key>", methods=["DELETE"])
def api_delete(key):
    if not auth(): return jsonify({"error":"forbidden"}), 403
    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM keys WHERE key=%s", (key,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/api/reset", methods=["POST"])
def api_reset():
    if not auth(): return jsonify({"error":"forbidden"}), 403
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE keys SET status='free', used_at=NULL")
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/api/delete_all", methods=["POST"])
def api_delete_all():
    if not auth(): return jsonify({"error":"forbidden"}), 403
    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM keys")
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/api/stats", methods=["GET"])
def api_stats():
    if not auth(): return jsonify({"error":"forbidden"}), 403
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) as cnt FROM keys GROUP BY status")
    result = {"free": 0, "used": 0, "total": 0}
    for r in cur.fetchall():
        result[r["status"]] = r["cnt"]
        result["total"] += r["cnt"]
    cur.close(); conn.close()
    return jsonify(result)

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
