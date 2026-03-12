from flask import Flask, request, make_response, redirect

app = Flask(__name__)

@app.route("/")
def home():
    key = request.args.get("key") or request.cookies.get("sk_key") or "не передан"
    resp = make_response(f"Flask поймал запрос на /. Ключ: {key}\n\nЕсли видишь это — прокси-роут работает!")
    resp.set_cookie("sk_key", key, max_age=86400, secure=True, httponly=True, samesite="Lax")
    return resp

@app.route("/activate")
def activate():
    key = request.args.get("key", "").strip()
    if not key:
        return "Ключ обязателен", 400
    resp = make_response(redirect("/?key=" + key))
    resp.set_cookie("sk_key", key, max_age=86400, secure=True, httponly=True, samesite="Lax")
    return resp

@app.route("/test")
def test():
    return "Тестовый роут живой. Всё ок."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
