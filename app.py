"""
Gmail → Single Markdown Aggregator
Fetches all email threads matching a search query and writes
their full content into one markdown file. No AI classification.
Uses checklist.json to skip already-fetched threads on re-runs.
"""

import base64
import json
import os
import re
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Config ────────────────────────────────────────────────────────────────────
SCOPES           = ["https://www.googleapis.com/auth/gmail.readonly"]
CHECKLIST_FILE   = "checklist.json"
OUTPUT_DIR       = Path("output")
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE       = "token.json"
MAX_WORKERS      = 5
DEFAULT_QUERY    = "AI interview Prep"
OUTPUT_FILE      = "emails.md"

OUTPUT_DIR.mkdir(exist_ok=True)

# ── Retry ─────────────────────────────────────────────────────────────────────

def call_with_backoff(fn, max_retries: int = 6, base_delay: float = 1.0):
    """Call fn(); retry with exponential backoff on rate-limit and SSL/network errors."""
    from googleapiclient.errors import HttpError
    for attempt in range(max_retries):
        try:
            return fn()
        except HttpError as e:
            status = e.resp.status
            if status in (429, 500, 503) and attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
            else:
                raise
        except (ssl.SSLError, ConnectionResetError, ConnectionAbortedError, TimeoutError) as e:
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
            else:
                raise

# ── Persistence ───────────────────────────────────────────────────────────────

def load_checklist() -> dict:
    if Path(CHECKLIST_FILE).exists():
        with open(CHECKLIST_FILE) as f:
            return json.load(f)
    return {}


def save_checklist(data: dict):
    with open(CHECKLIST_FILE, "w") as f:
        json.dump(data, f, indent=2)


def output_path(filename: str) -> Path:
    return OUTPUT_DIR / filename


def init_output_file(filename: str, query: str):
    p = output_path(filename)
    if not p.exists():
        p.write_text(
            f"# Gmail Thread Aggregator\n"
            f"> Query: `{query}`  \n"
            f"> Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"---\n\n",
            encoding="utf-8",
        )


def append_thread_to_file(entry: dict, filename: str, lock: threading.Lock):
    subject = entry.get("subject", "(no subject)")
    sender  = entry.get("sender", "")
    date    = entry.get("date", "")
    body    = entry.get("body", "").strip()

    # Strip tracking/unsubscribe noise
    body = re.sub(r"Unsubscribe https?://\S+", "", body)
    body = re.sub(r"View this post on the web at https?://\S+", "", body)
    body = re.sub(r"\s{3,}", "\n\n", body).strip()

    block = (
        f"## {subject}\n\n"
        f"**From:** {sender}  \n"
        f"**Date:** {date}\n\n"
        f"{body}\n\n"
        f"---\n\n"
    )

    with lock:
        with open(output_path(filename), "a", encoding="utf-8") as f:
            f.write(block)


# ── Gmail Auth ────────────────────────────────────────────────────────────────

def get_gmail_service():
    creds = None
    if Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(CREDENTIALS_FILE).exists():
                return None, (
                    "`credentials.json` not found. "
                    "Download it from Google Cloud Console → APIs & Services → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False), None


def build_service_for_thread() -> object:
    """Build a fresh Gmail service per worker thread to avoid shared SSL connections."""
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ── Gmail Fetch ───────────────────────────────────────────────────────────────

def search_threads(service, query: str, max_results: int = 200) -> list[dict]:
    results, page_token = [], None
    while len(results) < max_results:
        params = dict(
            userId="me",
            q=query,
            maxResults=min(50, max_results - len(results)),
        )
        if page_token:
            params["pageToken"] = page_token
        resp  = call_with_backoff(lambda p=params: service.users().threads().list(**p).execute())
        batch = resp.get("threads", [])
        if not batch:
            break
        for t in batch:
            meta = call_with_backoff(lambda tid=t["id"]: service.users().threads().get(
                userId="me", id=tid,
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute())
            msgs = meta.get("messages", [])
            if not msgs:
                continue
            hdrs = {h["name"]: h["value"]
                    for h in msgs[0].get("payload", {}).get("headers", [])}
            results.append({
                "threadId": t["id"],
                "subject":  hdrs.get("Subject", "(no subject)"),
                "sender":   hdrs.get("From", ""),
                "date":     hdrs.get("Date", ""),
                "snippet":  msgs[0].get("snippet", ""),
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def fetch_body(service, thread_id: str) -> str:
    thread = call_with_backoff(lambda: service.users().threads().get(
        userId="me", id=thread_id, format="full",
    ).execute())
    parts = []
    for msg in thread.get("messages", []):
        text = _extract_text(msg.get("payload", {}))
        if text:
            parts.append(text.strip())
    return "\n\n".join(parts)


def _extract_text(payload: dict) -> str:
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    elif mime.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _extract_text(part)
            if text:
                return text
    return ""


# ── Worker ────────────────────────────────────────────────────────────────────

def process_thread(service, thread, filename, checklist, file_lock, checklist_lock, log_queue):
    tid     = thread["threadId"]
    subject = thread["subject"]
    try:
        log_queue.append(("fetching", subject))
        worker_svc = build_service_for_thread()
        body  = fetch_body(worker_svc, tid)
        entry = {**thread, "body": body}
        append_thread_to_file(entry, filename, file_lock)

        with checklist_lock:
            checklist[tid] = {
                "subject":     subject,
                "sender":      thread.get("sender", ""),
                "date":        thread.get("date", ""),
                "status":      "done",
                "processedAt": datetime.utcnow().isoformat(),
            }
            save_checklist(checklist)
        log_queue.append(("done", subject))
    except Exception as e:
        with checklist_lock:
            checklist[tid] = {**checklist.get(tid, {}), "status": "error", "error": str(e)}
            save_checklist(checklist)
        log_queue.append(("error", subject, str(e)))


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="Gmail → Markdown", page_icon="📧", layout="wide")
    st.title("📧 Gmail → Markdown Aggregator")
    st.caption(
        "Fetches every email thread matching your search query "
        "and dumps the full content into a single markdown file."
    )

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("⚙️ Settings")

        st.subheader("Gmail OAuth")
        st.markdown(
            "Place `credentials.json` (from "
            "[Google Cloud Console](https://console.cloud.google.com/)) "
            "next to `app.py`, then click **Authenticate**."
        )
        if st.button("🔐 Authenticate Gmail"):
            with st.spinner("Opening browser for OAuth…"):
                svc, err = get_gmail_service()
            if err:
                st.error(err)
            else:
                st.success("✅ Authenticated!")

        st.divider()
        query       = st.text_input("Gmail search query", value=DEFAULT_QUERY)
        max_results = st.number_input("Max threads", 1, 500, 100)
        max_workers = st.slider("Parallel fetch threads", 1, 10, MAX_WORKERS)
        output_name = st.text_input("Output filename", value=OUTPUT_FILE)

        st.divider()
        if st.button("🗑️ Clear checklist & output"):
            Path(CHECKLIST_FILE).unlink(missing_ok=True)
            (OUTPUT_DIR / output_name).unlink(missing_ok=True)
            st.success("Cleared — next run starts fresh.")

    # ── Stats ─────────────────────────────────────────────────────────────────
    checklist  = load_checklist()
    done_count = sum(1 for v in checklist.values() if v.get("status") == "done")
    err_count  = sum(1 for v in checklist.values() if v.get("status") == "error")

    c1, c2, c3 = st.columns(3)
    c1.metric("Tracked threads", len(checklist))
    c2.metric("✅ Done",         done_count)
    c3.metric("❌ Errors",       err_count)

    # ── Run ───────────────────────────────────────────────────────────────────
    if st.button("▶ Fetch & aggregate", type="primary", use_container_width=True):
        svc, err = get_gmail_service()
        if err:
            st.error(err)
            st.stop()

        with st.status("🔍 Searching Gmail…", expanded=True) as status:
            st.write(f"Query: `{query}`")
            threads = search_threads(svc, query, max_results=int(max_results))
            st.write(f"Found **{len(threads)}** thread(s).")

            checklist = load_checklist()
            pending   = [t for t in threads
                         if checklist.get(t["threadId"], {}).get("status") != "done"]
            skipped   = len(threads) - len(pending)
            st.write(f"**{len(pending)}** to fetch · **{skipped}** already done (skipped).")

            if not pending:
                status.update(label="✅ All threads already fetched!", state="complete")
                st.stop()

            init_output_file(output_name, query)

            status.update(label=f"⚡ Fetching {len(pending)} thread(s) in parallel…")
            progress   = st.progress(0)
            log_box    = st.empty()
            logs: list[str] = []

            log_queue      = []
            file_lock      = threading.Lock()
            checklist_lock = threading.Lock()

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        process_thread,
                        svc, thread, output_name,
                        checklist, file_lock, checklist_lock, log_queue,
                    ): thread
                    for thread in pending
                }
                completed = 0
                for _ in as_completed(futures):
                    completed += 1
                    while log_queue:
                        msg = log_queue.pop(0)
                        if msg[0] == "done":
                            logs.append(f"✅ {msg[1]}")
                        elif msg[0] == "error":
                            logs.append(f"❌ {msg[1]}: {msg[2]}")
                        elif msg[0] == "fetching":
                            logs.append(f"↓ {msg[1]}")
                    progress.progress(completed / len(pending))
                    log_box.markdown("\n\n".join(logs[-15:]))

            status.update(label="🎉 Done!", state="complete")
        st.rerun()

    st.divider()

    # ── Checklist ─────────────────────────────────────────────────────────────
    with st.expander(f"📋 Checklist ({len(checklist)} threads)", expanded=False):
        if checklist:
            st.dataframe(
                [
                    {
                        "Subject":   v.get("subject", tid),
                        "From":      v.get("sender", ""),
                        "Date":      v.get("date", ""),
                        "Status":    "✅" if v.get("status") == "done"
                                     else ("❌" if v.get("status") == "error" else "⏳"),
                        "Thread ID": tid,
                    }
                    for tid, v in checklist.items()
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No threads fetched yet.")

    # ── Download ──────────────────────────────────────────────────────────────
    out = OUTPUT_DIR / output_name
    if out.exists():
        st.divider()
        content    = out.read_text(encoding="utf-8")
        word_count = len(content.split())
        st.subheader(f"📄 {output_name}")
        st.caption(f"{done_count} threads · ~{word_count:,} words · {len(content):,} chars")
        st.download_button(
            label=f"⬇️ Download {output_name}",
            data=content,
            file_name=output_name,
            mime="text/markdown",
            use_container_width=True,
        )
        with st.expander("👁 Preview (first 3 000 chars)"):
            st.markdown(content[:3000] + ("…" if len(content) > 3000 else ""))


if __name__ == "__main__":
    main()
