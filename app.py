#!/usr/bin/env python3
import os
import sqlite3
from flask import Flask, request

app = Flask(__name__)
DB_PATH = "/tmp/samokat_keys.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
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
    conn.commit()
    conn.close()

HTML_ACTIVATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Вход в Самокат...</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,sans-serif;background:#f5f5f5;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:white;border-radius:24px;padding:48px 32px;max-width:380px;width:100%;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.12)}}
.logo{{font-size:64px;margin-bottom:20px}}
h1{{font-size:24px;color:#1a1a1a;margin-bottom:10px;font-weight:700}}
p{{color:#888;font-size:15px;margin-bottom:32px;line-height:1.5}}
.spinner{{width:44px;height:44px;border:4px solid #f0f0f0;border-top:4px solid #FF4B4B;border-radius:50%;animation:spin .8s linear infinite;margin:0 auto 32px}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.btn{{display:inline-block;padding:16px 40px;background:#FF4B4B;color:white;border-radius:14px;text-decoration:none;font-weight:700;font-size:17px}}
</style>
</head>
<body>
<div class="card">
  <div class="logo">🛒</div>
  <h1>Входим в Самокат...</h1>
  <p>Секунду, устанавливаем сессию</p>
  <div class="spinner"></div>
  <a href="https://samokat.ru" class="btn">Открыть Самокат →</a>
</div>
<script>
document.cookie = "__Secure-next-auth.session-token={token}; domain=samokat.ru; path=/; expires=Fri, 01 Jan 2027 00:00:00 GMT; SameSite=Lax; Secure";
document.cookie = "next-auth.session-token={token}; domain=.samokat.ru; path=/; expires=Fri, 01 Jan 2027 00:00:00 GMT; SameSite=Lax";
setTimeout(function(){{ window.location.href = "https://samokat.ru"; }}, 1200);
</script>
</body>
</html>"""

HTML_ERROR = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ошибка</title>
<style>body{{font-family:-apple-system,sans-serif;background:#f5f5f5;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:white;border-radius:24px;padding:48px 32px;max-width:380px;width:100%;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.12)}}
.logo{{font-size:64px;margin-bottom:20px}}h1{{font-size:22px;color:#1a1a1a;margin-bottom:10px}}p{{color:#888;font-size:15px}}</style>
</head><body><div class="card"><div class="logo">❌</div><h1>{title}</h1><p>{msg}</p></div></body></html>"""

@app.route("/")
def index():
    return "OK", 200

@app.route("/activate")
def activate():
    key = request.args.get("key", "").strip().upper()
    if not key:
        return HTML_ERROR.format(title="Нет ключа", msg="Ключ не указан в ссылке"), 400
    conn = get_db()
    row = conn.execute("SELECT * FROM keys WHERE key=?", (key,)).fetchone()
    conn.close()
    if not row:
        return HTML_ERROR.format(title="Ключ не найден", msg="Проверь ключ и попробуй снова"), 404
    token = row["session_token"]
    return HTML_ACTIVATE.format(token=token), 200

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
