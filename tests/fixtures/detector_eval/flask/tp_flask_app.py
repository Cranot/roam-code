"""True positives for the Flask detector triad.

Routes get inventoried, debug=True fires py-flask-debug-true, the
literal SECRET_KEY fires py-flask-secret-key-literal.
"""

from __future__ import annotations

from flask import Flask


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "this-must-not-be-a-literal"  # py-flask-secret-key-literal

    @app.route("/")
    def index():  # py-flask-routes (route /)
        return "ok"

    @app.route("/users/<int:user_id>")
    def get_user(user_id):  # py-flask-routes (route /users/<int:user_id>)
        return {"id": user_id}

    return app


def run():
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)  # py-flask-debug-true


if __name__ == "__main__":
    run()
