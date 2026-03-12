#!/usr/bin/env python3
import os
import pg8000.native
import httpx
import re
from flask import Flask, request, Response, stream_with_context
from flask_cors import CORS
from urllib.parse import urljoin, urlparse, urlencode

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
    conn = get_db()
    rows = conn.run("SELECT session_token FROM keys WHERE key = :key", key=key)
    conn.close()
    return rows[0][0] if rows else None

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def proxy(path):
    key = request.args.get("key") or request.cookies.get("sk_key")
    
    if not key:
        return "Нет ключа. Добавь ?key=XXXX в URL", 401
    
    session_token = get_session_token(key)
    if not session_token:
        return "Ключ недействителен или токен сдох", 403
    
    # Куки, которые подставляем в каждый запрос к samokat.ru
    proxy_cookies = {
        "__Secure-next-auth.session-token": session_token,
        "_sv": "SV1.18515db0-6b60-47d9-aa85-93e9af748ad6.1772916992",  # твой из дампа
        # Добавь сюда другие куки из дампа, если они критичны (spid, spjs, adtech_uid и т.д.)
    }
    
    # Полный целевой URL
    target_url = urljoin(PROXY_TARGET + "/", path)
    if request.query_string:
        target_url += "?" + request.query_string.decode()
    
    # Копируем заголовки клиента
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ["host", "content-length", "transfer-encoding", "connection"]
    }
    headers["Host"] = "samokat.ru"
    headers["Referer"] = "https://samokat.ru/"  # антидетект
    
    try:
        client = httpx.Client(
            cookies=proxy_cookies,
            follow_redirects=False,
            timeout=30.0,
            http2=True
        )
        
        resp = client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=request.get_data(),
            allow_redirects=False
        )
        
        # Обрабатываем редиректы (меняем Location)
        if 300 <= resp.status_code < 400 and "location" in resp.headers:
            loc = resp.headers["location"]
            if loc.startswith("/"):
                loc = request.host_url.rstrip("/") + loc
            elif "samokat.ru" in loc:
                loc = loc.replace("https://samokat.ru", request.host_url.rstrip("/"))
            return Response(status=resp.status_code, headers={"Location": loc})
        
        # Стрим контента
        def generate():
            for chunk in resp.iter_bytes(chunk_size=8192):
                yield chunk
        
        # Подмена доменов в текстовом контенте (HTML, JS, CSS)
        content_type = resp.headers.get("content-type", "").lower()
        is_text = any(t in content_type for t in ["html", "javascript", "css", "json"])
        
        excluded_headers = ["content-encoding", "content-length", "transfer-encoding"]
        response_headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded_headers]
        
        if is_text:
            content = b"".join(generate()).decode("utf-8", errors="replace")
            
            # Жёсткая замена всех упоминаний samokat.ru
            content = re.sub(r'(https?://)?samokat\.ru', request.host, content, flags=re.IGNORECASE)
            content = content.replace("samokat.ru", request.host)
            content = content.replace("//samokat.ru", "//" + request.host)
            # Для внутренних API (если используешь bypass-like)
            content = content.replace("https://api-web.samokat.ru", request.host_url.rstrip("/"))
            
            # Сохраняем ключ в куки для будущих запросов (чтобы не таскать ?key= каждый раз)
            resp_cookie = f'sk_key={key}; Path=/; Max-Age=86400; Secure; SameSite=Lax; HttpOnly'
            response_headers.append(("Set-Cookie", resp_cookie))
            
            return Response(
                content.encode("utf-8"),
                status=resp.status_code,
                headers=dict(response_headers),
                mimetype=resp.headers.get("content-type")
            )
        
        # Бинарные файлы (картинки, шрифты и т.д.) — просто стрим
        return Response(
            stream_with_context(generate()),
            status=resp.status_code,
            headers=dict(response_headers)
        )
    
    except Exception as e:
        return f"Прокси сломался: {str(e)}", 502

# Оставляем твои старые роуты /activate, /api/keys и т.д.
@app.route("/activate")
def activate():
    key = request.args.get("key", "").strip().upper()
    if not key:
        return "Ключ обязателен", 400
    # Просто редиректим на корень с ключом
    return f"""
    <meta http-equiv="refresh" content="0;url=/?key={key}">
    <p>Загружаем Самокат...</p>
    """, 200

# ... весь твой остальной код API (api_list_keys, api_add и т.д.) ...

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
