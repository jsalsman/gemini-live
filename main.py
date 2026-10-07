from flask import Flask, send_file, request, redirect, Response
import os

app = Flask(__name__, static_folder=None)

@app.route("/", methods=['GET', 'POST'])
def index():
    if request.method == 'GET':
        api_key = request.cookies.get('gemini_api_key')
        if api_key:
            return send_file('gemini-live.html')
        else:
            return send_file('landing.html')
    elif request.method == 'POST':
        api_key = request.form.get('api_key')
        response = redirect("/")
        response.set_cookie('gemini_api_key', api_key,
            max_age=(31_536_000 * 5))  # five years
        return response

@app.route('/delegation.js')
def delegation():
    return send_file('delegation.js', mimetype='text/javascript')

@app.route('/favicon.ico')
def favicon():
    return send_file('favicon.ico', mimetype='image/x-icon')

@app.route('/screenshot.png')
def screenshot():
    return send_file('screenshot.png', mimetype='image/png')

@app.route('/robots.txt')
def robots():
    return Response("User-agent: *\nAllow: /\n",
                    mimetype='text/plain')

if __name__ == '__main__':
    app.run(port=int(os.environ.get('PORT', 80)))
# Ordinarily should be: python -m flask --app main run -p $PORT
# or gunicorn, etc.
