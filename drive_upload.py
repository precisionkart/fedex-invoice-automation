"""
Google Drive upload helper.
Uploads a file to a designated folder using the OAuth refresh token
saved by setup_drive_auth.py.

Folders are organised as: FedEx Invoices/{YYYY}/{MM}/
The script auto-creates year and month subfolders if they don't exist.
"""

import os
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_FILE = "google-token.json"


def _get_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _find_or_create_folder(service, name, parent_id):
    """Find a subfolder by name under parent_id, or create it."""
    q = (f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' "
         f"and '{parent_id}' in parents and trashed = false")
    results = service.files().list(q=q, fields="files(id,name)", pageSize=1).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    folder = service.files().create(body={
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }, fields="id").execute()
    return folder["id"]


def upload_invoice(local_path, root_folder_id, order_date=None):
    """
    Upload a PDF to /FedEx Invoices/{YYYY}/{MM}/ in Drive.

    Args:
        local_path:      Path to the PDF on disk.
        root_folder_id:  ID of your top-level 'FedEx Invoices' Drive folder.
        order_date:      datetime — used to pick year/month subfolders.
                         Defaults to now (UTC) if not provided.

    Returns:
        dict with file id and web link.
    """
    if not os.path.exists(local_path):
        raise FileNotFoundError(local_path)

    service = _get_service()
    when = order_date or datetime.utcnow()
    year  = f"{when.year}"
    month = f"{when.month:02d}"

    year_id  = _find_or_create_folder(service, year, root_folder_id)
    month_id = _find_or_create_folder(service, month, year_id)

    filename = os.path.basename(local_path)
    media = MediaFileUpload(local_path, mimetype="application/pdf", resumable=False)
    body = {"name": filename, "parents": [month_id]}

    uploaded = service.files().create(
        body=body, media_body=media,
        fields="id,name,webViewLink",
    ).execute()

    return {
        "id": uploaded["id"],
        "name": uploaded["name"],
        "link": uploaded.get("webViewLink"),
        "folder": f"{year}/{month}",
    }
