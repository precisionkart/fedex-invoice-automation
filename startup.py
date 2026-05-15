"""
Runs at startup on Railway to materialise Google credential JSON files
from environment variables. Locally these files already exist on disk.
"""
import os
import json


def write_if_env(env_name, filename):
    val = os.getenv(env_name)
    if val and not os.path.exists(filename):
        try:
            # Validate it's JSON, then write the raw string
            json.loads(val)
            with open(filename, "w") as f:
                f.write(val)
            print(f"✅ Wrote {filename} from {env_name}")
        except Exception as e:
            print(f"❌ {env_name} is set but invalid JSON: {e}")
    elif not val:
        print(f"ℹ️  {env_name} not set — skipping {filename}")
    else:
        print(f"ℹ️  {filename} already exists — skipping")


write_if_env("GOOGLE_OAUTH_CLIENT_JSON", "google-oauth-client.json")
write_if_env("GOOGLE_TOKEN_JSON", "google-token.json")
