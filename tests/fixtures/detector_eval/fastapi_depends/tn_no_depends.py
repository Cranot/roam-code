"""True negative: FastAPI route with no Depends() in the signature."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI()


def helper():
    return "x"


@app.get("/healthz")
def healthz():
    # No Depends() — should NOT fire
    return {"ok": True}


@app.post("/echo")
def echo(payload: dict):
    # Plain body param, no Depends()
    return payload
