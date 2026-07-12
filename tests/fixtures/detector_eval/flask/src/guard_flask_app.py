"""Production Flask entry point."""

from flask import Flask


def run_app():
    app = Flask(__name__)
    app.run(debug=True)  # Used by test_debug_fixture.py in development.
