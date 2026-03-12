from flask import Flask, request, Response, make_response, redirect
import os
import pg8000.native
import requests
import re
from urllib.parse import urljoin
from functools import lru_cache

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
PROXY_TARGET = "https://samokat.ru"

@lru_cache(maxsize=512)
def get_cached_resource(path):
    try:
        url = urljoin(PROXY_TARGET, path)
        resp = requests.get(url, timeout=(2, 6))
        if resp.status_code == 200:
            return resp.content, resp.headers.get("content-type", "application/octet-stream")
        return b"", "text/plain"
    except:
        return b"", "text/plain"

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
    print(f"Proxy called: path={path}, key={key}")

    # Быстрый fail без ключа — не тратим время на спам
    if not key:
        # Статические файлы — из кэша, чтобы favicon.ico не убивал
        if path in ["favicon.ico", "apple-touch-icon.png"] or path.endswith((".ico", ".png", ".css", ".js", ".svg", ".woff")):
            content, ct = get_cached_resource(path)
            return Response(content, mimetype=ct or "application/octet-stream")
        return "Нет ключа (?key=XXXX или куки sk_key)", 401
    
    session_token = get_session_token(key)
    if not session_token:
        return "Токен не найден или просрочен", 403
    
    proxy_cookies = {
        "__Secure-next-auth.session-token": session_token,
        "_sv": "SV1.18515db0-6b60-47d9-aa85-93e9af748ad6.1772916992",
    }
    
    target_url = urljoin(PROXY_TARGET, "/" if not path else path)
    if request.query_string:
        target_url += "?" + request.query_string.decode()
    
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host"]}
    headers["Host"] = "samokat.ru"
    headers["Referer"] = "https://samokat.ru/"
    
    # Статические — из кэша
    if any(ext in path.lower() for ext in [".css", ".js", ".ico", ".png", ".jpg", ".svg", ".woff", ".ttf"]):
        content, ct = get_cached_resource(path)
        if content:
            return Response(content, mimetype=ct)
    
    try:
        resp = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            cookies=proxy_cookies,
            data=request.get_data(),
            allow_redirects=False,
            timeout=(4, 12)  # быстро фейлим, если тормозит
        )
        
        print(f"Samokat status: {resp.status_code} для {target_url}")
        
        if 300 <= resp.status_code < 400 and "location" in resp.headers:
            loc = resp.headers["location"]
            if loc.startswith("/"):
                loc = request.host_url.rstrip("/") + loc
            else:
                loc = loc.replace("https://samokat.ru", request.host_url.rstrip("/"))
                loc = loc.replace("http://samokat.ru", request.host_url.rstrip("/"))
            return Response(status=resp.status_code, headers={"Location": loc})
        
        content = resp.content
        content_type = resp.headers.get("content-type", "").lower()
        
        # Подмена только для HTML/JS/CSS
        if "text/html" in content_type or "javascript" in content_type or "css" in content_type:
            try:
                content = content.decode("utf-8", errors="replace")
                
                my_host = request.host_url.rstrip("/")
                # Максимальная подмена
                content = re.sub(r'(https?://)?samokat\.ru(:\d+)?', my_host, content, flags=re.IGNORECASE)
                content = content.replace("samokat.ru", request.host)
                content = content.replace("//samokat.ru", "//" + request.host)
                content = content.replace("api-web.samokat.ru", request.host)
                content = content.replace('"https://samokat.ru', f'"{my_host}')
                content = content.replace("'https://samokat.ru", f"'{my_host}")
                content = content.encode("utf-8")
            except Exception as decode_err:
                print(f"Decode error: {decode_err}")
        
        response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ["content-encoding", "transfer-encoding"]}
        response_headers["Set-Cookie"] = f'sk_key={key}; Path=/; Max-Age=86400; Secure; SameSite=Lax; HttpOnly'
        
        return Response(content, status=resp.status_code, headers=response_headers)
    
    except requests.Timeout:
        return "Samokat тормозит — попробуй позже", 504
    except Exception as e:
        print(f"Proxy error: {str(e)}")
        return f"Ошибка прокси: {str(e)}", 502

@app.route("/activate")
def activate():
    key = request.args.get("key", "").strip().upper()
    if not key:
        return "Ключ обязателен", 400
    
    resp = make_response(redirect("/?key=" + key))
    resp.set_cookie("sk_key", key, max_age=86400, secure=True, httponly=True, samesite="Lax")
    return resp

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
