"""Production-shaped helper kept under a test fixture tree."""

from flask import Flask


def run_fixture_app():
    app = Flask(__name__)
    app.run(debug=True)
