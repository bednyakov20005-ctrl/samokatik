#!/usr/bin/env python3
import os
import pg8000.native
import requests
import re
from flask import Flask, request, Response
from flask_cors import CORS
from urllib.parse import urljoin

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
        return "Нет ключа (?key=XXXX)", 401
    
    session_token = get_session_token(key)
    if not session_token:
        return "Токен не найден или сдох", 403
    
    proxy_cookies = {
        "__Secure-next-auth.session-token": session_token,
        "_sv": "SV1.18515db0-6b60-47d9-aa85-93e9af748ad6.1772916992",
    }
    
    target_url = urljoin(PROXY_TARGET + "/", path)
    if request.query_string:
        target_url += "?" + request.query_string.decode()
    
    headers = {k: v for k, v in request.headers if k.lower() not in ["host"]}
    headers["Host"] = "samokat.ru"
    headers["Referer"] = "https://samokat.ru/"
    
    try:
        resp = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            cookies=proxy_cookies,
            data=request.get_data(),
            allow_redirects=False,
            timeout=(5, 60)  # 5 сек на connect, 60 на read — не убьёт воркер
        )
        
        if 300 <= resp.status_code < 400 and "location" in resp.headers:
            loc = resp.headers["location"]
            if loc.startswith("/"):
                loc = request.host_url.rstrip("/") + loc
            elif "samokat.ru" in loc:
                loc = loc.replace("https://samokat.ru", request.host_url.rstrip("/"))
            return Response(status=resp.status_code, headers={"Location": loc})
        
        content = resp.content
        content_type = resp.headers.get("content-type", "").lower()
        
        if any(t in content_type for t in ["html", "javascript", "css", "json"]):
            try:
                content = content.decode("utf-8", errors="replace")
                content = re.sub(r'(https?://)?samokat\.ru', request.host, content, flags=re.IGNORECASE)
                content = content.replace("samokat.ru", request.host)
                content = content.encode("utf-8")
            except:
                pass
        
        response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ["content-encoding", "transfer-encoding"]}
        response_headers["Set-Cookie"] = f'sk_key={key}; Path=/; Max-Age=86400; Secure; SameSite=Lax; HttpOnly'
        
        return Response(
            content,
            status=resp.status_code,
            headers=response_headers,
            mimetype=resp.headers.get("content-type")
        )
    
    except requests.Timeout:
        return "Samokat.ru слишком медленно отвечает. Попробуй позже.", 504
    except Exception as e:
        print(f"Proxy error: {str(e)}")
        return f"Ошибка: {str(e)}", 502

@app.route("/activate")
def activate():
    key = request.args.get("key", "").strip().upper()
    if not key:
        return "Ключ обязателен", 400
    return f"""
    <meta http-equiv="refresh" content="0;url=/?key={key}">
    <p>Загружаем Самокат...</p>
    """, 200

# Твои API роуты (/api/keys и т.д.) — вставь их сюда без изменений

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
