"""True negatives — same Flask shapes done correctly.

debug=True is gated behind an env var; SECRET_KEY is read from os.environ;
no detector should fire.
"""

from __future__ import annotations

import os

from flask import Flask


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ["FLASK_SECRET_KEY"]

    @app.route("/")
    def index():  # py-flask-routes (still legitimate inventory)
        return "ok"

    return app


def run():
    app = create_app()
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )


if __name__ == "__main__":
    run()
