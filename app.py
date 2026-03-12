from flask import Flask, request, Response, make_response, redirect
import os
import pg8000.native
import requests
import re
import random
from urllib.parse import urljoin
from functools import lru_cache

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
PROXY_TARGET = "https://samokat.ru"

PROXY_LIST = [
    "socks5://0ktuhalt9j-res-country-RU-state-536203-city-498817-hold-session-session-69b3036213658:BHOdByDtlrFaqcH0@62.112.8.229:443",
    # Добавь остальные свои прокси сюда
]

SKIP_REQ_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "te", "trailers", "upgrade"}
SKIP_RESP_HEADERS = {"content-encoding", "transfer-encoding", "connection", "keep-alive", "content-length"}

def get_random_proxy():
    proxy_str = random.choice(PROXY_LIST)
    return {"http": proxy_str, "https": proxy_str}

def build_headers(incoming_headers, jwt_token=None):
    headers = {k: v for k, v in incoming_headers.items() if k.lower() not in SKIP_REQ_HEADERS}
    headers["Host"] = "samokat.ru"
    headers["Referer"] = "https://samokat.ru/"
    headers["Origin"] = "https://samokat.ru"
    headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    headers["Accept-Language"] = "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
    headers["Accept-Encoding"] = "gzip, deflate, br"
    headers["Sec-Fetch-Site"] = "same-origin"
    headers["Sec-Fetch-Mode"] = "navigate"
    headers["Sec-Fetch-Dest"] = "document"
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"
    return headers

@lru_cache(maxsize=512)
def get_cached_resource(path):
    try:
        proxies = get_random_proxy()
        url = urljoin(PROXY_TARGET, path)
        resp = requests.get(url, proxies=proxies, timeout=(5, 15),
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code in (200, 304):
            return resp.content, resp.headers.get("content-type", "application/octet-stream")
    except Exception as e:
        print(f"Cache resource error: {e}")
    return b"", "text/plain"

def get_db():
    url = DATABASE_URL.replace("postgresql://", "").replace("postgres://", "")
    userinfo, rest = url.split("@")
    user, password = userinfo.split(":", 1)
    hostport, dbname = rest.split("/", 1)
    if ":" in hostport:
        host, port = hostport.split(":")
        port = int(port)
    else:
        host, port = hostport, 5432
    return pg8000.native.Connection(
        user=user, password=password, host=host,
        port=port, database=dbname, ssl_context=True
    )

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

def rewrite_content(content_bytes, content_type, my_host, my_origin):
    try:
        text = content_bytes.decode("utf-8", errors="replace")
        text = re.sub(r'https?://(?:api-web\.)?samokat\.ru(?::\d+)?', my_origin, text, flags=re.IGNORECASE)
        text = re.sub(r'//(?:api-web\.)?samokat\.ru(?::\d+)?', '//' + my_host, text, flags=re.IGNORECASE)
        text = text.replace("samokat.ru", my_host)
        return text.encode("utf-8")
    except Exception as e:
        print(f"Rewrite error: {e}")
        return content_bytes

def set_sk_cookie(response, key):
    response.headers.add("Set-Cookie", f"sk_key={key}; Path=/; Max-Age=86400; Secure; SameSite=None; HttpOnly")

def do_proxy_request(target_url, session_token, key, strip_key_from_qs=True):
    """Основная функция проксирования."""
    my_origin = request.host_url.rstrip("/")
    my_host = request.host

    headers = build_headers(request.headers, jwt_token=session_token)
    proxies = get_random_proxy()

    # Убираем key= из query string чтобы не слать самокату
    qs = request.query_string.decode()
    if strip_key_from_qs and qs:
        qs = re.sub(r'(?:^|&)key=[^&]*', '', qs).strip('&')
    if qs:
        target_url += "?" + qs

    print(f"→ {request.method} {target_url}")

    resp = requests.request(
        method=request.method,
        url=target_url,
        headers=headers,
        data=request.get_data(),
        allow_redirects=False,
        timeout=(5, 30),
        proxies=proxies,
        stream=True,
    )

    print(f"← {resp.status_code} для {target_url}")

    # Редирект — переписываем location
    if 300 <= resp.status_code < 400 and "location" in resp.headers:
        loc = resp.headers["location"]
        if loc.startswith("/"):
            loc = my_origin + loc
        else:
            loc = re.sub(r'https?://(?:api-web\.)?samokat\.ru(?::\d+)?', my_origin, loc, flags=re.IGNORECASE)
        flask_resp = Response(status=resp.status_code)
        flask_resp.headers["Location"] = loc
        set_sk_cookie(flask_resp, key)
        return flask_resp

    content = resp.content
    content_type = resp.headers.get("content-type", "").lower()

    if "text/html" in content_type or "javascript" in content_type or "css" in content_type:
        content = rewrite_content(content, content_type, my_host, my_origin)

    response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in SKIP_RESP_HEADERS}

    flask_resp = Response(content, status=resp.status_code, headers=response_headers)
    flask_resp.headers["Content-Type"] = content_type or "application/octet-stream"
    set_sk_cookie(flask_resp, key)
    return flask_resp


# ─────────────────────────────────────────────
# /session-by-key  — вход по ключу (как у конкурентов)
# ─────────────────────────────────────────────
@app.route("/session-by-key")
def session_by_key():
    key = (request.args.get("key") or "").strip().upper()
    partner = request.args.get("partner", "")
    if not key:
        return "Ключ обязателен", 400
    session_token = get_session_token(key)
    if not session_token:
        return "Ключ не найден", 403

    print(f"session-by-key: key={key}, partner={partner}")
    resp = make_response(redirect("/"))
    resp.set_cookie("sk_key", key, max_age=86400, secure=True, httponly=False, samesite="None")
    return resp


# ─────────────────────────────────────────────
# /test-proxy  — диагностика
# ─────────────────────────────────────────────
@app.route("/test-proxy")
def test_proxy():
    try:
        proxies = get_random_proxy()
        resp = requests.get(
            "https://samokat.ru/",
            proxies=proxies,
            timeout=(5, 15),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            allow_redirects=False,
        )
        return f"OK: {resp.status_code}, размер: {len(resp.content)} байт, proxy: {proxies['https']}"
    except Exception as e:
        return f"ОШИБКА: {str(e)}", 500


# ─────────────────────────────────────────────
# /activate  — старый способ входа
# ─────────────────────────────────────────────
@app.route("/activate")
def activate():
    key = request.args.get("key", "").strip().upper()
    if not key:
        return "Ключ обязателен", 400
    resp = make_response(redirect("/"))
    resp.set_cookie("sk_key", key, max_age=86400, secure=True, httponly=False, samesite="None")
    return resp


# ─────────────────────────────────────────────
# Основной прокси
# ─────────────────────────────────────────────
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def proxy(path):
    # служебные маршруты уже определены выше — сюда не попадут
    key = request.args.get("key") or request.cookies.get("sk_key")
    print(f"Proxy: /{path}, key={key}")

    # Статика без ключа
    if not key:
        if path.endswith((".ico", ".png", ".css", ".js", ".svg", ".woff", ".woff2", ".ttf", ".map")):
            content, ct = get_cached_resource("/" + path)
            return Response(content, mimetype=ct, status=200 if content else 404)
        return Response("Нет ключа — используй ?key= или /session-by-key", status=401)

    session_token = get_session_token(key)
    if not session_token:
        return "Токен не найден", 403

    # Статика с ключом — из кеша
    if path.endswith((".css", ".js", ".ico", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2", ".ttf", ".map")):
        content, ct = get_cached_resource("/" + path)
        if content:
            return Response(content, mimetype=ct)

    target_url = urljoin(PROXY_TARGET, "/" + path)

    try:
        return do_proxy_request(target_url, session_token, key)
    except requests.Timeout:
        return "Samokat таймаутит", 504
    except Exception as e:
        print(f"Proxy error: {e}")
        return f"Ошибка прокси: {e}", 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
