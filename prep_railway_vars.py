"""
Prepares your Railway environment variables.
Reads .env + the two Google JSON files locally, prints the
combined block ready to paste into Railway's Raw Editor.

Nothing leaves your machine — this just formats the values.
"""

import json
import os
from pathlib import Path
from dotenv import dotenv_values

env = dotenv_values(".env")

print("=" * 60)
print("COPY EVERYTHING BETWEEN THE LINES BELOW INTO RAILWAY")
print("=" * 60)

# Pass through plain .env vars
for key in [
    "SHOPIFY_STORE_DOMAIN",
    "SHOPIFY_CLIENT_ID",
    "SHOPIFY_CLIENT_SECRET",
    "GOOGLE_DRIVE_FOLDER_ID",
]:
    val = env.get(key, "")
    print(f"{key}={val}")

# Webhook secret = same as client secret (Dev Dashboard apps)
print(f"SHOPIFY_WEBHOOK_SECRET={env.get('SHOPIFY_CLIENT_SECRET', '')}")

# Compact the JSON files to single lines
for var_name, filename in [
    ("GOOGLE_OAUTH_CLIENT_JSON", "google-oauth-client.json"),
    ("GOOGLE_TOKEN_JSON", "google-token.json"),
]:
    if not Path(filename).exists():
        print(f"# ⚠️  {filename} not found", flush=True)
        continue
    with open(filename) as f:
        data = json.load(f)
    one_line = json.dumps(data, separators=(",", ":"))
    print(f"{var_name}={one_line}")

print("=" * 60)
print("END — copy everything BETWEEN the lines above")
print("=" * 60)
