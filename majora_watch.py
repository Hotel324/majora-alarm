#!/usr/bin/env python3
"""
Majora's Mask Remake Alarm
--------------------------
Watches Nintendo's official YouTube channel(s) for a Majora's Mask
announcement and blares an alarm on your phone via ntfy and/or Pushover.

Only official Nintendo channels are checked, so rumor headlines
("Majora's Mask remake CONFIRMED?!") can't set it off.

Env vars (set as GitHub Actions secrets):
  NTFY_TOPIC         - your secret ntfy topic name (e.g. majora-alarm-x7k2q9)
  PUSHOVER_USER      - Pushover user key        (optional, best for iPhone)
  PUSHOVER_TOKEN     - Pushover app API token   (optional)
  TEST_ALARM=1       - fire a test alarm and exit
"""

import json
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.message import EmailMessage

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# Official Nintendo YouTube channels (public RSS feeds, no API key needed)
CHANNELS = {
    "Nintendo of America": "UCGIY_O-8vW4rfX98KlMkvRg",
}

# A video title must mention Majora...
MUST_MATCH = re.compile(r"majora", re.I)
# ...and look like a NEW game announcement...
ANNOUNCE_HINTS = re.compile(
    r"switch\s*2|remake|announce|reveal|teaser|hd\b|coming|release date|pre-?order|direct",
    re.I,
)
# ...and NOT be about the old versions or other promos Nintendo already runs.
EXCLUDE = re.compile(
    r"nintendo switch online|\bNSO\b|nintendo 64|\bN64\b|3ds|nintendo music|nintendo today|"
    r"icons?|cards?|anniversary concert|symphony|amiibo",
    re.I,
)

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen": [], "fired": False, "last_keepalive": None}


def save_state(state):
    state["seen"] = state["seen"][-500:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "majora-alarm/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)
    for entry in root.findall("atom:entry", NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=NS)
        title = entry.findtext("atom:title", default="", namespaces=NS)
        link_el = entry.find("atom:link", NS)
        link = link_el.get("href") if link_el is not None else f"https://youtu.be/{vid}"
        yield vid, title, link


def is_announcement(title):
    return bool(
        MUST_MATCH.search(title)
        and ANNOUNCE_HINTS.search(title)
        and not EXCLUDE.search(title)
    )


def ntfy_publish(topic, params, body):
    url = f"https://ntfy.sh/{urllib.parse.quote(topic)}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
    urllib.request.urlopen(req, timeout=20).read()


def send_email(subject, body):
    """Send the alarm email through Gmail. Needs GMAIL_USER, GMAIL_APP_PASSWORD, ALARM_EMAIL."""
    to = (os.environ.get("ALARM_EMAIL") or "").strip()
    user = (os.environ.get("GMAIL_USER") or "").strip()
    pw = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "")
    if not (to and user and pw):
        print("Email skipped (ALARM_EMAIL / GMAIL_USER / GMAIL_APP_PASSWORD not all set)")
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(user, pw)
            s.send_message(msg)
        print(f"Alarm email sent to {to}")
    except Exception as e:
        # Never let an email problem stop the phone alerts
        print(f"Email failed: {e}", file=sys.stderr)


def alarm(title, link, test=False):
    # Keep the word MAJORA in both: the iPhone Shortcut matches on it.
    headline = "🌕 MAJORA TEST ALARM 🌕" if test else "🌕 MAJORA'S MASK REMAKE CONFIRMED 🌕"
    body = f"{title}\n{link}"
    sent = False

    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        # ntfy on iPhone can't repeat until acknowledged like Pushover can,
        # so send a burst of alerts instead (default: 10, 30 seconds apart).
        repeats = 2 if test else int(os.environ.get("NTFY_REPEATS") or 10)
        for i in range(repeats):
            params = {
                # Sent in the URL (not headers) so the emoji title can't break the request
                "title": f"{headline} ({i + 1}/{repeats})",
                "priority": "5",  # max: insistent on Android, time-sensitive on iPhone
                "tags": "rotating_light,full_moon",
                "click": link,
            }
            if i == 0:
                # Email first: an iPhone Shortcut turns it into a real ringing alarm
                send_email(headline, body)
            try:
                ntfy_publish(topic.strip(), params, body)
            except urllib.error.HTTPError as e:
                print(f"ntfy rejected it: {e.code} {e.read().decode(errors='replace')}", file=sys.stderr)
                raise
            print(f"ntfy alarm sent ({i + 1}/{repeats})")
            if i < repeats - 1:
                time.sleep(30)
        sent = True

    user, token = os.environ.get("PUSHOVER_USER"), os.environ.get("PUSHOVER_TOKEN")
    if user and token:
        data = urllib.parse.urlencode(
            {
                "token": token,
                "user": user,
                "title": headline,
                "message": body,
                "url": link,
                "priority": 2,      # emergency: repeats until you acknowledge
                "retry": 30,        # every 30 seconds
                "expire": 3600,     # for up to an hour
                "sound": "siren",
            }
        ).encode()
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data, timeout=20).read()
        print("Pushover emergency alarm sent")
        sent = True

    if not sent:
        print("No NTFY_TOPIC or PUSHOVER_* configured - nothing sent!", file=sys.stderr)
        sys.exit(1)


def main():
    if os.environ.get("TEST_ALARM") == "1":
        alarm("This is only a test. The moon is not falling (yet).",
              "https://www.youtube.com/@NintendoAmerica", test=True)
        return

    state = load_state()
    first_run = not state["seen"]
    seen = set(state["seen"])

    for name, cid in CHANNELS.items():
        try:
            xml_bytes = fetch(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
        except Exception as e:
            print(f"[{name}] fetch failed: {e}", file=sys.stderr)
            continue

        for vid, title, link in parse_feed(xml_bytes):
            if vid in seen:
                continue
            seen.add(vid)
            state["seen"].append(vid)
            if first_run:
                continue  # don't alarm on whatever's already on the channel
            print(f"[{name}] new video: {title}")
            if is_announcement(title) and not state["fired"]:
                alarm(title, link)
                state["fired"] = True

    # Keep the repo "active" so GitHub doesn't pause the schedule after 60 days
    now = datetime.now(timezone.utc)
    last = state.get("last_keepalive")
    if not last or (now - datetime.fromisoformat(last)).days >= 30:
        state["last_keepalive"] = now.isoformat()

    save_state(state)
    print("Checked. Dawn of the... still waiting." if not state["fired"] else "Already fired.")


if __name__ == "__main__":
    main()
