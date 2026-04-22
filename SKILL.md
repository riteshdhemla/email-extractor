---
name: gmail-markdown-aggregator
description: >
  Use this skill whenever the user wants to aggregate, collect, or archive
  emails from Gmail into structured markdown files organised by theme or topic.
  Triggers on phrases like: "collect my emails into markdown", "aggregate Gmail
  emails", "save newsletter emails to files", "export emails by topic", "build
  study notes from emails", "organise my interview prep emails". Also trigger
  when the user mentions fetching email content in bulk, deduplicating email
  fetches, or maintaining a checklist of processed threads. Use this skill even
  if the user only says "pull my emails and categorise them" or similar loose
  phrasing — it covers the full workflow end-to-end.
---

# Gmail → Markdown Aggregator Skill

This skill covers the complete workflow for fetching Gmail emails in bulk,
categorising each one with Claude, and writing themed markdown files — with
persistent deduplication so interrupted runs can be safely resumed.

---

## Workflow Overview

```
Search Gmail ──► Checklist diff ──► Parallel fetch (N threads)
                                          │
                                    Categorise with Claude
                                          │
                             Append to output/<Theme>.md
                                          │
                              Update checklist.json (atomic)
```

---

## Step-by-Step Instructions

### Step 1 — Search Gmail

Use the `Gmail:search_threads` tool with a targeted query.
Prefer `from:` + keyword combos for precision:

```
query: "from:aiinterviewprep@substack.com"
query: "subject:\"AI interview Prep\""
query: "AI interview Prep"
pageSize: 50   # fetch up to 50 per call; paginate with nextPageToken
```

Collect all thread IDs, subjects, senders, dates, and snippets into a list.

### Step 2 — Load checklist

Load `checklist.json` (create if absent). Structure:
```json
{
  "threadId123": {
    "subject": "LLM System Design #29",
    "status": "done",
    "theme": "LLM System Design",
    "processedAt": "2026-04-20T10:00:00Z"
  }
}
```

**Skip any threadId whose status is `"done"`** — this prevents duplicate work
if the session was interrupted.

### Step 3 — Fetch email bodies in parallel

For each pending thread, call `Gmail:get_thread` with:
```
messageFormat: FULL_CONTENT
```

**Parallelism strategy (in an artifact or Streamlit app):**
- Use `ThreadPoolExecutor(max_workers=5)` in Python
- In a React artifact, use `Promise.all` batched in groups of 4–5
- In a sequential Claude session, fetch up to 4 threads per turn

### Step 4 — Categorise with Claude

For each fetched email, call the Anthropic API (or ask Claude directly) with:

```
System: You are categorising an AI interview prep email into structured study notes.
User:
  Subject: {subject}
  Body: {body[:6000]}

  1. Pick the best theme from: {theme_list}
  2. Write clean markdown notes with:
     - ## Subject as first heading
     - The interview question
     - The wrong answer + why it fails
     - The core insight
     - The hiring-ready answer
     - Key concepts (tables, code blocks, bullets)

  Respond ONLY as JSON: {"theme": "...", "markdown": "..."}
```

### Step 5 — Write output files

- One file per theme: `output/LLM_System_Design.md`, `output/Advanced_Deep_Learning.md`, etc.
- File header (on first write):
  ```markdown
  # {Theme Name}
  > AI Interview Prep Notes — aggregated from Gmail

  ---
  ```
- Append each email's formatted markdown followed by `\n\n---\n\n`
- **Write immediately after each email** — don't batch. This lets you resume
  safely if the session terminates mid-run.

### Step 6 — Update checklist

After writing, immediately update `checklist.json`:
```json
{
  "threadId123": {
    "subject": "...",
    "sender": "...",
    "date": "...",
    "theme": "LLM System Design",
    "status": "done",
    "processedAt": "2026-04-20T10:00:00Z"
  }
}
```

On error, set `"status": "error"` with `"error": "<message>"` — these will
be retried on the next run.

---

## Default Theme List

```
LLM System Design
ML System Design
LLM Agents
Advanced Deep Learning
Coding & Algorithms
Behavioral & Career
General AI/ML
Other
```

Adjust themes in the sidebar (Streamlit app) or by editing the prompt.

---

## Deduplication Rules

| Condition | Action |
|-----------|--------|
| `status == "done"` | Skip entirely |
| `status == "error"` | Retry on next run |
| `status == "fetching"` (crashed mid-run) | Retry |
| Not in checklist | Process as new |

---

## Tools Used

| Tool | Purpose |
|------|---------|
| `Gmail:search_threads` | Discover email thread IDs |
| `Gmail:get_thread` | Fetch full email body |
| Anthropic API / Claude | Categorise + format as markdown |
| File system / storage | Write markdown files + checklist |

---

## Handling Paywalled / Truncated Emails

Some newsletters truncate body content for non-subscribers. If `plaintextBody`
is short (< 200 chars) or ends with "…":

1. Use the snippet + subject to infer the topic
2. Note in the markdown: `> ⚠️ Full content paywalled — notes based on preview`
3. Still categorise and write — partial notes are better than skipping

---

## Error Handling

- Wrap each thread fetch in try/except — one failed thread must not block others
- Log errors to checklist with `status: "error"`
- After all threads finish, report: N done, M errors, K skipped
- Errors are automatically retried on the next run

---

## Output File Naming

| Theme | File |
|-------|------|
| `LLM System Design` | `output/LLM_System_Design.md` |
| `Advanced Deep Learning` | `output/Advanced_Deep_Learning.md` |
| `General AI/ML` | `output/General_AI_ML.md` |

Rule: replace spaces with `_`, remove special characters.

---

## Resuming After Interruption

When a session terminates mid-run:
1. Load `checklist.json` — see exactly which threads are `done` vs `pending`
2. Re-run search (results are stable — same thread IDs)
3. Skip `done` threads; re-process `error` and untracked threads
4. Markdown files are append-only — already-written content is safe

This makes the aggregator **idempotent**: running it 10 times produces the
same result as running it once (no duplicates).

---

## Suggested Claude Prompt (for in-session use without Streamlit)

When the user asks to aggregate emails directly in Claude (without running
the Streamlit app), follow this flow:

```
1. Call Gmail:search_threads with the user's query
2. Load checklist from storage (window.storage in artifact, or report state)
3. For each pending thread (batch of 4):
   a. Call Gmail:get_thread
   b. Categorise inline or via Claude API
   c. Append to the appropriate theme section
   d. Mark done in checklist
4. Report summary: X done, Y skipped, Z errors
5. Offer download links for each theme file
```
