import logging


def load_rows(client):
    try:
        return client.fetch_rows()
    except Exception:
        logging.exception("row fetch failed")
        raise
