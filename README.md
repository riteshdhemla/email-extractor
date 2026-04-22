# Gmail → Markdown Aggregator

Fetch emails matching any Gmail search query, auto-categorise with Claude,
and write themed markdown study files — with duplicate-skip across sessions.

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Get Gmail OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable the **Gmail API**
3. Create **OAuth 2.0 credentials** (Desktop App type)
4. Download `credentials.json` → place it in this folder


### 3. Run
```bash
streamlit run app.py
```

On first run, a browser will open for Google OAuth. After authorising,
`token.json` is saved locally — subsequent runs skip the browser prompt.

---

## How It Works

| Step | What happens |
|------|-------------|
| **Search** | Gmail API searched with your query (e.g. `"AI interview Prep"`) |
| **Checklist** | `checklist.json` loaded — threads already `done` are skipped |
| **Parallel fetch** | Up to N threads fetch full email bodies simultaneously |
| **Categorise** | Claude reads each email and picks the best theme + formats notes |
| **Write** | Markdown appended to `output/<Theme>.md` immediately after each email |
| **Persist** | `checklist.json` updated atomically — safe to interrupt & resume |

---

## Output files

All markdown files land in `./output/`:
```
output/
├── LLM_System_Design.md
├── Advanced_Deep_Learning.md
├── LLM_Agents.md
└── ...
```

The checklist is stored at `./checklist.json`.

---

## Customising

All settings are in the sidebar:
- **Gmail search query** — any Gmail search syntax works (`from:`, `subject:`, `after:`, etc.)
- **Themes** — one per line, Claude picks the best match
- **Max emails** — hard limit on how many threads to search
- **Parallel threads** — how many emails to fetch simultaneously (default 5)

To re-process emails that already ran, click **Clear checklist** in the sidebar.
