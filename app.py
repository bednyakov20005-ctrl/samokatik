from flask import Flask, request, Response, make_response
import requests
import re
import json

app = Flask(__name__)
storage = {}
dyn_cookies = {}  # key → {name: value}

TARGET_DOMAINS = [
    'samokat.ru', 'www.samokat.ru', 'api.samokat.ru',
    'api-web.samokat.ru', 'game.samokat.ru'
]
ALL_PROXY_DOMAINS = TARGET_DOMAINS + ['servicepipe.ru', 'static.servicepipe.ru']

# JS который инжектируем в страницу — перехватывает document.cookie и шлёт нам
COOKIE_INTERCEPTOR = r"""
<script>
(function() {
  var _key = document.cookie.match(/samokat_key=([^;]+)/);
  if (!_key) return;
  var proxyKey = _key[1];
  var origDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie') ||
                       Object.getOwnPropertyDescriptor(HTMLDocument.prototype, 'cookie');
  if (!origDescriptor) return;
  Object.defineProperty(document, 'cookie', {
    set: function(val) {
      origDescriptor.set.call(document, val);
      // Отправляем новую куку на сервер
      var parts = val.split(';');
      var kv = parts[0].trim();
      fetch('/sync-cookie', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({key: proxyKey, cookie: kv})
      }).catch(function(){});
    },
    get: function() {
      return origDescriptor.get.call(document);
    }
  });
})();
</script>
"""

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/store', methods=['POST'])
def store():
    data = request.json
    storage[data['key']] = data['cookie_header']
    dyn_cookies[data['key']] = {}
    return {'ok': True}

@app.route('/sync-cookie', methods=['POST'])
def sync_cookie():
    """Принимаем куки которые поставил servicepipe JS"""
    data = request.json
    key = data.get('key')
    cookie = data.get('cookie', '')
    if key and key in storage and '=' in cookie:
        name, value = cookie.split('=', 1)
        if key not in dyn_cookies:
            dyn_cookies[key] = {}
        dyn_cookies[key][name.strip()] = value.strip()
    return {'ok': True}

@app.route('/activate')
def activate():
    key = request.args.get('key')
    if key in storage:
        resp = make_response("""
        <html><body>
        ✅ Активировано!<br><br>
        <a href='/?url=https://samokat.ru/' style='font-size:20px'>👉 Открыть самокат</a>
        </body></html>
        """)
        resp.set_cookie('samokat_key', key, max_age=30*24*3600, httponly=False, samesite='Lax', path='/')
        return resp
    return "Ключ не найден", 401

def build_cookie_header(key):
    base = storage.get(key, '')
    extra = dyn_cookies.get(key, {})
    if not extra:
        return base
    base_dict = {}
    for part in base.split(';'):
        part = part.strip()
        if '=' in part:
            k, v = part.split('=', 1)
            base_dict[k.strip()] = v.strip()
    base_dict.update(extra)
    return '; '.join(f'{k}={v}' for k, v in base_dict.items())

def save_response_cookies(key, headers):
    if key not in dyn_cookies:
        dyn_cookies[key] = {}
    for k, v in headers.items():
        if k.lower() == 'set-cookie':
            part = v.split(';')[0].strip()
            if '=' in part:
                ck, cv = part.split('=', 1)
                dyn_cookies[key][ck.strip()] = cv.strip()

def rewrite_urls(text, host):
    base = f"http://{host}/?url="
    for domain in ALL_PROXY_DOMAINS:
        text = re.sub(
            rf'(https?://){re.escape(domain)}',
            lambda m, d=domain: f'{base}https://{d}',
            text,
            flags=re.IGNORECASE
        )
        text = re.sub(
            rf'(?<![:/])//{re.escape(domain)}',
            lambda m, d=domain: f'{base}https://{d}',
            text,
            flags=re.IGNORECASE
        )
    return text

@app.route('/<path:path>', methods=['GET','POST','PUT','DELETE','PATCH','OPTIONS'])
@app.route('/', defaults={'path': ''})
def proxy(path):
    if request.method == 'OPTIONS':
        resp = Response('')
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,PATCH,OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = '*'
        return resp

    key = request.cookies.get('samokat_key')
    if not key or key not in storage:
        return Response('<a href="/activate?key=bro123">Сначала активируй</a>', status=401, mimetype='text/html')

    target = request.args.get('url')
    if not target:
        qs = request.query_string.decode('utf-8')
        target = f"https://samokat.ru/{path}"
        if qs:
            target += f"?{qs}"
    else:
        # Убираем двойное оборачивание
        while 'localhost' in target or '127.0.0.1' in target:
            inner = re.search(r'\?url=(https?://.+)', target)
            if inner:
                target = inner.group(1)
            else:
                break

    if not target.startswith('http'):
        target = f"https://samokat.ru/{target}"

    is_servicepipe = 'servicepipe.ru' in target

    req_headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in ['host', 'cookie', 'content-length', 'origin', 'referer']}
    req_headers['Cookie'] = build_cookie_header(key)
    req_headers['User-Agent'] = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
    req_headers['Origin'] = 'https://samokat.ru'
    req_headers['Referer'] = 'https://samokat.ru/'
    req_headers['Accept-Language'] = 'ru-RU,ru;q=0.9'
    req_headers['Accept-Encoding'] = 'identity'

    if is_servicepipe:
        req_headers['Accept'] = '*/*'
        req_headers['sec-fetch-dest'] = 'script'
        req_headers['sec-fetch-mode'] = 'no-cors'
        req_headers['sec-fetch-site'] = 'cross-site'
    else:
        req_headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        req_headers['sec-fetch-dest'] = 'document'
        req_headers['sec-fetch-mode'] = 'navigate'
        req_headers['sec-fetch-site'] = 'same-origin'

    try:
        r = requests.request(
            request.method, target,
            headers=req_headers,
            data=request.get_data(),
            allow_redirects=False,
            timeout=20
        )
    except Exception as e:
        return f"Proxy error: {e}", 502

    save_response_cookies(key, r.headers)

    content = r.content
    ct = r.headers.get('Content-Type', '').lower()

    if any(x in ct for x in ['html', 'javascript', 'json', 'text']):
        try:
            text = content.decode('utf-8', errors='ignore')
            text = rewrite_urls(text, request.host)
            # Инжектируем перехватчик кук в HTML страницы
            if 'html' in ct:
                text = text.replace('<head>', '<head>' + COOKIE_INTERCEPTOR, 1)
                if COOKIE_INTERCEPTOR not in text:
                    text = COOKIE_INTERCEPTOR + text
            content = text.encode('utf-8')
        except:
            pass

    excluded = [
        'content-length', 'transfer-encoding', 'connection',
        'content-encoding', 'access-control-allow-origin',
        'access-control-allow-methods', 'access-control-allow-headers',
        'set-cookie'
    ]
    out_headers = [(k, v) for k, v in r.headers.items()
                   if k.lower() not in excluded]

    location = r.headers.get('location', '')
    if location:
        if location.startswith('/'):
            location = f"http://{request.host}/?url=https://samokat.ru{location}"
        elif not ('localhost' in location or '127.0.0.1' in location):
            for d in ALL_PROXY_DOMAINS:
                if d in location:
                    location = re.sub(
                        rf'https?://[^/?]*{re.escape(d)}',
                        f'http://{request.host}/?url=https://{d}',
                        location
                    )
        out_headers = [h for h in out_headers if h[0].lower() != 'location']
        out_headers.append(('Location', location))

    out_headers.append(('Access-Control-Allow-Origin', '*'))
    out_headers.append(('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,PATCH,OPTIONS'))
    out_headers.append(('Access-Control-Allow-Headers', '*'))

    return Response(content, status=r.status_code, headers=out_headers)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
