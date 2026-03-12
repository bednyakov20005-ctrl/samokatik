#!/usr/bin/env python3
import os
import pg8000.native
import requests
import re
from flask import Flask, request, Response
from flask_cors import CORS
from urllib.parse import urljoin, urlparse

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.environ.get("DATABASE_URL")
API_SECRET   = os.environ.get("API_SECRET", "samokat_secret_2024")
PROXY_TARGET = "https://samokat.ru"

def get_db():
    url = DATABASE_URL.replace("postgresql://", "").replace("postgres://", "")
    userinfo, rest = url.split("@")
    user, password = userinfo.split(":")
    hostport, dbname = rest.split("/")
    if ":" in hostport:
        host, port = hostport.split(":")
        port = int(port)
    else:
        host, port = hostport, 5432
    return pg8000.native.Connection(user=user, password=password, host=host, port=port, database=dbname, ssl_context=True)

def get_session_token(key):
    key = key.upper().strip()
    try:
        conn = get_db()
        rows = conn.run("SELECT session_token FROM keys WHERE key = :key", key=key)
        conn.close()
        return rows[0][0] if rows else None
    except Exception as e:
        print(f"DB error: {e}")
        return None

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def proxy(path):
    key = request.args.get("key") or request.cookies.get("sk_key")
    
    if not key:
        return "Нет ключа в URL (?key=XXXX) или куки. Введи через бота.", 401
    
    session_token = get_session_token(key)
    if not session_token:
        return "Ключ недействителен или токен просрочен.", 403
    
    proxy_cookies = {
        "__Secure-next-auth.session-token": session_token,
        "_sv": "SV1.18515db0-6b60-47d9-aa85-93e9af748ad6.1772916992",  # из твоего дампа
        # Добавь сюда остальные куки из дампа, если нужно
    }
    
    target_url = urljoin(PROXY_TARGET + "/", path)
    if request.query_string:
        target_url += "?" + request.query_string.decode()
    
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ["host", "content-length", "transfer-encoding", "connection"]
    }
    headers["Host"] = "samokat.ru"
    headers["Referer"] = "https://samokat.ru/"
    headers["User-Agent"] = request.headers.get("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    
    try:
        resp = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            cookies=proxy_cookies,
            data=request.get_data(),
            allow_redirects=False,
            timeout=(5, 45)  # connect 5 сек, read 45 сек — чтобы не таймаутить
        )
        
        # Ручная обработка редиректов
        if 300 <= resp.status_code < 400 and "location" in resp.headers:
            loc = resp.headers["location"]
            if loc.startswith("/"):
                loc = request.host_url.rstrip("/") + loc
            elif "samokat.ru" in loc:
                loc = loc.replace("https://samokat.ru", request.host_url.rstrip("/"))
                loc = loc.replace("http://samokat.ru", request.host_url.rstrip("/"))
            return Response(status=resp.status_code, headers={"Location": loc})
        
        content = resp.content
        content_type = resp.headers.get("content-type", "").lower()
        
        if any(t in content_type for t in ["html", "javascript", "css", "json"]):
            try:
                content = content.decode("utf-8", errors="replace")
                
                # Подмена доменов
                content = re.sub(r'(https?://)?samokat\.ru', request.host, content, flags=re.IGNORECASE)
                content = content.replace("samokat.ru", request.host)
                content = content.replace("//samokat.ru", "//" + request.host)
                content = content.replace("https://api-web.samokat.ru", request.host_url.rstrip("/"))
                
                content = content.encode("utf-8")
            except Exception as e:
                print(f"Content decode error: {e}")
        
        response_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in ["content-encoding", "transfer-encoding", "connection"]
        }
        
        # Сохраняем ключ в куки для будущих заходов без ?key
        response_headers["Set-Cookie"] = f'sk_key={key}; Path=/; Max-Age=86400; Secure; SameSite=Lax; HttpOnly'
        
        return Response(
            content,
            status=resp.status_code,
            headers=response_headers,
            mimetype=resp.headers.get("content-type")
        )
    
    except requests.Timeout:
        return "Samokat.ru слишком долго отвечает (timeout). Попробуй позже или добавь прокси.", 504
    except requests.RequestException as e:
        print(f"Requests error: {str(e)}")
        return f"Ошибка прокси: {str(e)}", 502

# Старый activate — редирект на корень с ключом
@app.route("/activate")
def activate():
    key = request.args.get("key", "").strip().upper()
    if not key:
        return "Ключ обязателен: /activate?key=XXXX", 400
    
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="0;url=/?key={key}">
        <title>Загрузка Самоката...</title>
        <style>body{{background:#000;color:#fff;font-family:sans-serif;text-align:center;padding:100px;}}</style>
    </head>
    <body>
        <h1>Открываем Самокат...</h1>
        <p>Секунду, авторизуем...</p>
    </body>
    </html>
    """, 200

# Оставь все свои /api/keys, /api/stats и т.д. роуты как есть ниже
# ... твой старый API код ...

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
