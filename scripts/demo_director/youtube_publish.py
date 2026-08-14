#!/usr/bin/env python3
"""Upload walkthrough episodes to YouTube via the Data API v3.

Every upload is forced to privacyStatus=private; flipping a video public is a
deliberate manual step in YouTube Studio. Credentials live outside the repo:

  client secret : ~/.config/guaardvark/youtube_client_secret.json
                  (OAuth "Desktop app" client downloaded from Google Cloud Console)
  stored token  : ~/.config/guaardvark/youtube_token.json
                  (created by `youtube_publish.py auth`, refreshed automatically)

Usage:
  youtube_publish.py auth                            one-time browserless OAuth
  youtube_publish.py upload VIDEO --title T [--description-file F]
                     [--tags a,b,c] [--thumbnail PNG] [--playlist NAME]
  youtube_publish.py update VIDEO_ID [--title T] [--description-file F]
                     [--tags a,b,c] [--thumbnail PNG]
  youtube_publish.py list                            recent uploads + privacy status
"""

import argparse
import json
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config/guaardvark"
CLIENT_SECRET = CONFIG_DIR / "youtube_client_secret.json"
TOKEN_FILE = CONFIG_DIR / "youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube"]

CATEGORY_SCIENCE_TECH = "28"


def get_credentials(interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        if creds.valid:
            return creds
    if not interactive:
        sys.exit("No valid token. Run: youtube_publish.py auth")
    if not CLIENT_SECRET.exists():
        sys.exit(f"Missing {CLIENT_SECRET}\n"
                 "Download an OAuth Desktop-app client JSON from Google Cloud "
                 "Console (APIs & Services > Credentials) and save it there.")
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    # Headless box: print the URL, paste the code back.
    creds = flow.run_local_server(port=0, open_browser=False,
                                  authorization_prompt_message="Open this URL to authorize:\n{url}")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    TOKEN_FILE.chmod(0o600)
    print(f"Token stored at {TOKEN_FILE}")
    return creds


def service(interactive=False):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=get_credentials(interactive))


def upload(args):
    from googleapiclient.http import MediaFileUpload

    video = Path(args.video)
    if not video.exists():
        sys.exit(f"not found: {video}")
    description = Path(args.description_file).read_text() if args.description_file else ""
    body = {
        "snippet": {
            "title": args.title,
            "description": description,
            "tags": args.tags.split(",") if args.tags else [],
            "categoryId": CATEGORY_SCIENCE_TECH,
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        },
    }
    yt = service()
    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        progress, resp = req.next_chunk()
        if progress:
            print(f"\rupload {int(progress.progress() * 100)}%", end="", flush=True)
    print(f"\nvideo id: {resp['id']}  (private)")

    if args.thumbnail:
        yt.thumbnails().set(videoId=resp["id"],
                            media_body=MediaFileUpload(args.thumbnail)).execute()
        print("thumbnail set")
    if args.playlist:
        add_to_playlist(yt, resp["id"], args.playlist)
    return resp["id"]


def add_to_playlist(yt, video_id, playlist_name):
    playlists = yt.playlists().list(part="snippet", mine=True, maxResults=50).execute()
    match = next((p for p in playlists.get("items", [])
                  if p["snippet"]["title"] == playlist_name), None)
    if match:
        playlist_id = match["id"]
    else:
        created = yt.playlists().insert(part="snippet,status", body={
            "snippet": {"title": playlist_name},
            "status": {"privacyStatus": "public"},
        }).execute()
        playlist_id = created["id"]
        print(f"created playlist '{playlist_name}'")
    yt.playlistItems().insert(part="snippet", body={
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }).execute()
    print(f"added to playlist '{playlist_name}'")


def update(args):
    from googleapiclient.http import MediaFileUpload

    yt = service()
    current = yt.videos().list(part="snippet", id=args.video_id).execute()
    if not current.get("items"):
        sys.exit(f"video {args.video_id} not found (is it on this channel?)")
    snippet = current["items"][0]["snippet"]
    if args.title:
        snippet["title"] = args.title
    if args.description_file:
        snippet["description"] = Path(args.description_file).read_text()
    if args.tags:
        snippet["tags"] = args.tags.split(",")
    snippet.setdefault("categoryId", CATEGORY_SCIENCE_TECH)
    yt.videos().update(part="snippet",
                       body={"id": args.video_id, "snippet": snippet}).execute()
    print("metadata updated")
    if args.thumbnail:
        yt.thumbnails().set(videoId=args.video_id,
                            media_body=MediaFileUpload(args.thumbnail)).execute()
        print("thumbnail set")


def list_uploads(_args):
    yt = service()
    channels = yt.channels().list(part="contentDetails", mine=True).execute()
    uploads_pl = channels["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    items = yt.playlistItems().list(part="contentDetails",
                                    playlistId=uploads_pl, maxResults=25).execute()
    ids = [i["contentDetails"]["videoId"] for i in items.get("items", [])]
    if not ids:
        print("no uploads")
        return
    videos = yt.videos().list(part="snippet,status", id=",".join(ids)).execute()
    for v in videos.get("items", []):
        print(f"{v['id']}  [{v['status']['privacyStatus']:8}]  {v['snippet']['title']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth")
    sub.add_parser("list")

    up = sub.add_parser("upload")
    up.add_argument("video")
    up.add_argument("--title", required=True)
    up.add_argument("--description-file")
    up.add_argument("--tags")
    up.add_argument("--thumbnail")
    up.add_argument("--playlist")

    ud = sub.add_parser("update")
    ud.add_argument("video_id")
    ud.add_argument("--title")
    ud.add_argument("--description-file")
    ud.add_argument("--tags")
    ud.add_argument("--thumbnail")

    args = ap.parse_args()
    if args.cmd == "auth":
        get_credentials(interactive=True)
    elif args.cmd == "upload":
        upload(args)
    elif args.cmd == "update":
        update(args)
    elif args.cmd == "list":
        list_uploads(args)


if __name__ == "__main__":
    main()
