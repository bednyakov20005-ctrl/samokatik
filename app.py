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
    "socks5://0ktuhalt9j-res-country-RU-state-536203-city-498817-hold-session-session-69b3036213658:BHOdByDtlrFaqcH0@62.112.8.229:9999",
]

def get_random_proxy():
    proxy_str = random.choice(PROXY_LIST)
    return {"http": proxy_str, "https": proxy_str}

@lru_cache(maxsize=512)
def get_cached_resource(path):
    try:
        proxies = get_random_proxy()
        url = urljoin(PROXY_TARGET, path)
        resp = requests.get(url, proxies=proxies, timeout=(5, 15))
        if resp.status_code in (200, 304):
            return resp.content, resp.headers.get("content-type", "application/octet-stream")
    except:
        pass
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
    return pg8000.native.Connection(user=user, password=password, host=host, port=port, database=dbname, ssl_context=True)

def get_tokens(key):
    key = key.upper().strip()
    try:
        conn = get_db()
        rows = conn.run("SELECT session_token, access_token FROM keys WHERE key = :key", key=key)
        conn.close()
        if rows:
            return rows[0][0], rows[0][1]
        return None, None
    except Exception as e:
        print(f"DB error: {e}")
        return None, None

def get_session_token(key):
    session_token, _ = get_tokens(key)
    return session_token

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def proxy(path):
    if path == "test-proxy":
        results = {}
        proxies = get_random_proxy()
        # Тест 1: просто проверяем IP через прокси
        try:
            r = requests.get("http://api.ipify.org", proxies=proxies, timeout=(5, 10))
            results["ip_check"] = f"OK: {r.text}"
        except Exception as e:
            results["ip_check"] = f"FAIL: {e}"
        # Тест 2: самокат
        try:
            r = requests.get("https://samokat.ru/", proxies=proxies, timeout=(5, 15),
                             headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=False)
            results["samokat"] = f"OK: {r.status_code}, {len(r.content)}b"
        except Exception as e:
            results["samokat"] = f"FAIL: {e}"
        return f"Proxy: {list(proxies.values())[0]}\n\n" + "\n".join(f"{k}: {v}" for k, v in results.items())

    if path == "session-by-key":
        key = (request.args.get("key") or "").strip().upper()
        partner = request.args.get("partner", "")
        if not key:
            return "Ключ обязателен", 400
        token = get_session_token(key)
        if not token:
            return "Ключ не найден", 403
        print(f"session-by-key: key={key}, partner={partner}")
        resp = make_response(redirect("/"))
        resp.set_cookie("sk_key", key, max_age=86400, secure=True, httponly=False, samesite="None")
        return resp

    if path == "activate":
        key = request.args.get("key", "").strip().upper()
        if not key:
            return "Ключ обязателен", 400
        resp = make_response(redirect("/"))
        resp.set_cookie("sk_key", key, max_age=86400, secure=True, httponly=False, samesite="None")
        return resp

    key = request.args.get("key") or request.cookies.get("sk_key")
    print(f"Proxy called: path={path}, key={key}")

    if not key:
        if path in ["favicon.ico", "apple-touch-icon.png"] or path.endswith((".ico", ".png", ".css", ".js", ".svg", ".woff", ".ttf")):
            content, ct = get_cached_resource("/" + path)
            return Response(content, mimetype=ct or "application/octet-stream", status=200 if content else 404)
        return Response("Нет ключа", status=401)

    session_token, access_token = get_tokens(key)
    if not session_token:
        return "Токен не найден", 403

    proxy_cookies = {
        "__Secure-next-auth.session-token": session_token,
        "_sv": "SV1.18515db0-6b60-47d9-aa85-93e9af748ad6.1772916992",
    }

    target_url = urljoin(PROXY_TARGET, "/" if not path else "/" + path)
    qs = request.query_string.decode()
    if qs:
        qs = re.sub(r'(?:^|&)key=[^&]*', '', qs).strip('&')
    if qs:
        target_url += "?" + qs

    headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length", "connection", "keep-alive"]}
    headers["Host"] = "samokat.ru"
    headers["Referer"] = "https://samokat.ru/"
    headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    headers["Connection"] = "close"
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    if any(ext in path.lower() for ext in [".css", ".js", ".ico", ".png", ".jpg", ".svg", ".woff", ".ttf"]):
        content, ct = get_cached_resource("/" + path)
        if content:
            return Response(content, mimetype=ct)

    try:
        proxies = get_random_proxy()
        print(f"→ {request.method} {target_url} via {list(proxies.values())[0]}")
        session = requests.Session()
        session.keep_alive = False
        resp = session.request(
            method=request.method,
            url=target_url,
            headers=headers,
            cookies=proxy_cookies,
            data=request.get_data(),
            allow_redirects=False,
            timeout=(5, 30),
            proxies=proxies
        )
        session.close()

        print(f"← {resp.status_code} [{resp.headers.get('content-type','')}] {len(resp.content)}b")

        if 300 <= resp.status_code < 400 and "location" in resp.headers:
            loc = resp.headers["location"]
            if loc.startswith("/"):
                loc = request.host_url.rstrip("/") + loc
            else:
                loc = loc.replace("https://samokat.ru", request.host_url.rstrip("/"))
                loc = loc.replace("http://samokat.ru", request.host_url.rstrip("/"))
            r = Response(status=resp.status_code, headers={"Location": loc})
            r.headers.add("Set-Cookie", f"sk_key={key}; Path=/; Max-Age=86400; Secure; SameSite=None; HttpOnly")
            return r

        content = resp.content
        content_type = resp.headers.get("content-type", "").lower()

        if "text/html" in content_type or "javascript" in content_type or "css" in content_type:
            try:
                content = content.decode("utf-8", errors="replace")
                my_host = request.host_url.rstrip("/")
                content = re.sub(r'https?://samokat\.ru(?::\d+)?', my_host, content, flags=re.IGNORECASE)
                content = re.sub(r'//samokat\.ru(?::\d+)?', '//' + request.host, content)
                content = content.replace("samokat.ru", request.host)
                content = content.replace("api-web.samokat.ru", request.host)
                content = content.replace('"https://samokat.ru', f'"{my_host}')
                content = content.replace("'https://samokat.ru", f"'{my_host}")
                content = content.encode("utf-8")
            except Exception as decode_err:
                print(f"Decode error: {decode_err}")

        response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ["content-encoding", "transfer-encoding", "content-length"]}
        response_headers["Set-Cookie"] = f"sk_key={key}; Path=/; Max-Age=86400; Secure; SameSite=None; HttpOnly"

        return Response(content, status=resp.status_code, headers=response_headers)

    except requests.Timeout:
        return "Samokat таймаутит", 504
    except Exception as e:
        print(f"Proxy error: {str(e)}")
        return f"Ошибка прокси: {str(e)}", 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
