"""Flask app with an unambiguous SQL-injection and command-injection flow.

taint SHOULD flag:
  * python-sqli: request.args -> cursor.execute (unsanitized string concat)
  * python-command-injection: request.args -> os.system

It currently flags NEITHER (W452: the Python indexer never materialises the
`request.args` / `cursor.execute` / `os.system` symbols, so the graph BFS has
no source or sink nodes to connect).
"""

import os
import subprocess

import requests
import yaml
from flask import Flask, request

app = Flask(__name__)


@app.route("/search")
def search():
    q = request.args.get("q")            # taint SOURCE
    return run_query(q)


def run_query(q):
    import sqlite3

    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM items WHERE name = '" + q + "'")   # taint SINK
    return cursor.fetchall()


@app.route("/ping")
def ping():
    host = request.args.get("host")      # taint SOURCE
    os.system("ping -c 1 " + host)       # taint SINK (command injection)
    return "ok"


def fetch_remote(url):
    return requests.get(url).text        # network effect


def load_manifest(path):
    with open(path) as fh:               # filesystem effect
        return yaml.safe_load(fh)


def run_report(name):
    subprocess.run(["report", name], check=True)
