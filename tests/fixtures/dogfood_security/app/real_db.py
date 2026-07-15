"""A real sqlite data-access module — genuine DB reads and writes."""

import sqlite3


def save_user(conn, name):
    cur = conn.cursor()
    cur.execute("INSERT INTO users(name) VALUES (?)", (name,))
    conn.commit()


def read_user(conn, uid):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=?", (uid,))
    return cur.fetchone()
