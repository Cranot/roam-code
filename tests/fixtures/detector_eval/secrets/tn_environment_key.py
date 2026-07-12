"""TN: environment-backed configuration is the nearest miss for a secret."""

import os

STRIPE_KEY = os.environ["STRIPE_KEY"]
