"""Test fixture that intentionally enables Flask debug mode."""

from flask import Flask


def test_debug_server():
    app = Flask(__name__)
    app.run(debug=True)
