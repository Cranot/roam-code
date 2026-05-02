"""True positive: FastAPI Depends chain — informational, not an anti-pattern."""

from __future__ import annotations

from fastapi import Depends, FastAPI

app = FastAPI()


def get_db():
    return "db"


def get_user(db=Depends(get_db)):
    # py-fastapi-depends: this depends on get_db
    return {"db": db}


def get_admin(user=Depends(get_user)):
    # py-fastapi-depends: chain of two
    return user


@app.get("/me")
def read_me(admin=Depends(get_admin)):
    # py-fastapi-depends: route depends on admin
    return admin
