# 🍎 AI Calorie Tracker

A Streamlit app where you log food in plain English ("two eggs and toast for breakfast", "yesterday I had pasta for dinner, ~700 kcal") and a Gemini-powered agent turns it into structured entries saved in SQLite, per user, per day.

**Stack:** Python 3.11+, Streamlit, Google Gemini (`google-genai`), SQLAlchemy + SQLite, bcrypt, Pandas + Plotly.

> Calorie and nutrition values are estimates and are not a substitute for professional medical or dietary advice.

## 1. Clone the project

```bash
git clone <your-repo-url> ai-calorie-tracker
cd ai-calorie-tracker
```

## 2. Create a virtual environment

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Get a Gemini API key

1. Go to <https://aistudio.google.com/apikey> and sign in with a Google account.
2. Click **Create API key** and copy it.

## 5. Configure `.env`

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

Edit `.env`:

```env
GEMINI_API_KEY=your_api_key_here
# Optional:
# GEMINI_MODEL=gemini-2.5-flash
# DATABASE_URL=sqlite:///calorie_tracker.db
# DEFAULT_TIMEZONE=Asia/Kolkata
```

The key is read server-side only; it is never sent to the browser. Never commit `.env` (it is git-ignored).

## 6. Initialise the database

Tables are created automatically on first start. To do it explicitly:

```bash
python -m database.db
```

## 7. Run the app

```bash
streamlit run app.py
```

## 8. Create an account

Open the **Create Account** tab, choose a username, email, timezone and a password (8+ characters mixing letters and numbers/symbols), then start on the Dashboard. Set your daily calorie (and optional macro) targets under **⚙️ Profile**. Your timezone decides what "today" and "yesterday" mean.

## 9. How the AI agent works

```
user message ─► context from DB ─► Gemini ─► JSON plan ─► Python validates ─► Python writes DB ─► reply
```

1. **Context** (`agent/calorie_agent.py`): the app sends Gemini only what's needed: the current local date/time and weekday (so "yesterday" / "last Monday" resolve reliably), your targets, today's total, the last 7 daily totals, today's/yesterday's entries (with ids) and the last few chat messages. Never your whole history.
2. **Structured output** (`agent/prompts.py`): Gemini must return one JSON object with an `intent` (`add_food`, `update_food`, `delete_food`, `query_day`, `query_history`, `nutrition_question`, `general_chat`), dates, and a `foods` list (name, quantity, unit, meal, calories, protein/carbs/fat, whether calories were `user`-provided or `estimated`).
3. **Validation**: the JSON is parsed and validated with pydantic (ranges, enums), dates are checked (no future dates), and missing calories are rejected with a clarifying question. Invalid JSON is retried once, then a friendly error is shown.
4. **Database writes**: only Python code writes to the DB, inside a transaction, always scoped by your `user_id`. Gemini cannot run SQL. New foods are *appended*; a day is never overwritten.
5. **Queries**: for "What did I eat yesterday?" the app fetches the records, computes totals in Python and asks Gemini to phrase the answer from that data (with a plain fallback if Gemini fails).
6. **Confirmation**: replies include an authoritative summary of what was actually saved and the new day total. `~` marks estimates.

The chat page and the manual editor (**📅 History → Daily log**) update the same `food_logs` table.

### Project layout

```
app.py                    entry point, navigation, session gate
auth/                     bcrypt hashing, signup/login/logout, login UI
agent/                    Gemini client, system prompt, validation + execution
database/                 models, engine/session, repository (all queries)
pages/                    dashboard, chat, history, profile
utils/                    config (.env), helpers, shared UI bits
```

### Security notes

- Passwords: bcrypt over a SHA-256 pre-hash (no 72-byte truncation); no plaintext storage; basic per-session login throttling.
- SQL injection: SQLAlchemy parameterised queries only; every food/chat query filters by the logged-in `user_id`, so users can't read or modify each other's data.
- Login state lives in Streamlit session state (a browser refresh signs you out).
- Secrets come from environment variables only.

## 10. Deploy

- **Streamlit Community Cloud:** push to GitHub, create the app pointing at `app.py`, and add `GEMINI_API_KEY` (and optionally `DATABASE_URL`) under *Secrets / Environment variables*. Its filesystem is ephemeral, so use a hosted database (below) for persistence.
- **Docker / VM:** `pip install -r requirements.txt && streamlit run app.py --server.port 8501 --server.address 0.0.0.0` behind an HTTPS reverse proxy (nginx, Caddy). Mount a persistent volume for the SQLite file.
- **Production database:** SQLite is fine for a single instance. For multiple instances or heavier use, switch to PostgreSQL: `pip install psycopg[binary]` and set `DATABASE_URL=postgresql+psycopg://user:pass@host/dbname`. No code changes are needed because all access goes through SQLAlchemy (add Alembic for migrations).
- Always serve over HTTPS and keep `.env` out of version control.

## Troubleshooting

| Message | Fix |
|---|---|
| "GEMINI_API_KEY is missing" / "invalid" | Set a valid key in `.env`, restart Streamlit. |
| "model wasn't found" | Set `GEMINI_MODEL` to a model available to your key. |
| "rate-limited or out of quota" | Wait, or check your Gemini quota. |
