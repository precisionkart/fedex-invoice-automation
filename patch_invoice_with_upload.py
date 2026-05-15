"""
Tiny one-liner script that adds the Drive upload step to generate_invoice.py.
Run this once; it edits generate_invoice.py in place.
"""

import re

with open("generate_invoice.py", "r") as f:
    code = f.read()

# Add imports for the drive_upload helper
if "from drive_upload import upload_invoice" not in code:
    code = code.replace(
        "from dotenv import load_dotenv",
        "from dotenv import load_dotenv\nfrom drive_upload import upload_invoice",
    )

# After "✅ Invoice written to: ..." add the upload step
upload_block = '''
    # --- Upload to Google Drive ---
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if folder_id:
        print(f"☁️  Uploading to Google Drive...")
        try:
            from datetime import datetime
            order_dt = datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00"))
            result = upload_invoice(output_path, folder_id, order_date=order_dt)
            print(f"   ✅ Uploaded to /FedEx Invoices/{result['folder']}/{result['name']}")
            print(f"   🔗 {result['link']}")
        except Exception as e:
            print(f"   ⚠️  Upload failed: {e}")
            print(f"   PDF is still on disk at {output_path}")
    else:
        print(f"ℹ️  GOOGLE_DRIVE_FOLDER_ID not set in .env — skipping Drive upload.")
'''

# Insert right before "if __name__"
if "☁️  Uploading to Google Drive" not in code:
    code = code.replace(
        'if __name__ == "__main__":',
        upload_block + '\n\nif __name__ == "__main__":'
    )

with open("generate_invoice.py", "w") as f:
    f.write(code)

print("✅ generate_invoice.py patched with Drive upload step.")
