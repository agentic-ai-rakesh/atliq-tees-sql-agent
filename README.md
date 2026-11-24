# AtliQ Tees: SQL Agent

Ask questions about a t-shirt store's inventory, prices and discounts in plain English.
A **LangGraph agent** reads the database schema, writes **read-only SQL**, fixes its own
query errors, and answers from the query results. A FastAPI backend runs the agent, and a
Streamlit app is the UI.

## Summary

```
Question → 🤖 agent: plan → list_tables / describe_table → run_sql (guarded, read-only)
         → error? fix the SQL and retry → answer from the rows
         → finalize: answer, every SQL query with its rows, steps, tokens, latency, number check
```

- **Agent:** an explicit LangGraph loop (`agent ⇄ tools → finalize`). The model inspects the
  schema, including exact values such as brand names, then writes SQL. When a query fails,
  it reads the error and corrects the query. The loop stops after `AGENT_MAX_TOOL_ROUNDS` (6).
- **Tools:**
  - `list_tables`: tables and row counts.
  - `describe_table`: columns, types, keys and allowed values.
  - `run_sql`: one read-only SELECT, at most 50 rows.
- **Safety, in two layers:**
  1. A `sqlglot` guard allows only a single SELECT on known tables. It rejects writes,
     DDL, `PRAGMA`/`ATTACH`/`SET`, `INTO OUTFILE`, `SLEEP()` and similar functions,
     database prefixes and multiple statements.
  2. The connection itself is read-only (SQLite `mode=ro` + `query_only`; MySQL
     `READ ONLY` session), with a query time limit.
- **Grounding:** every answer comes with the SQL it ran and the rows returned. Numbers in
  the answer that don't appear in any result are flagged as **unverified**.
- **Backend:** FastAPI with `/api/ask`, `/api/schema`, `/api/examples` and `/api/config`.
  Errors are clear and use the right status code, and API keys and database passwords are
  removed from error text.
- **UI:** Streamlit, as a client of the API:
  - example questions you can click, and the database schema in the sidebar
  - a chat that shows each SQL query with its result table
  - a "How the agent worked" panel
- **Source data:** the store's data lives in `source/` as CSV files (`t_shirts.csv`,
  `discounts.csv`). They are validated on load, and `make db` builds the database from
  them (SQLite by default). `make mysql-sql` writes the same data as a MySQL script.
- **Models:** Google Gemini (default) or OpenAI, chosen in `.env`.
- **Observability:** per-answer steps, SQL, token usage, latency, model and prompt
  version; a rotating log in `logs/`; optional LangSmith tracing.
- **Tests:** 74 pytest tests with a scripted LLM. Expected values are computed in Python
  from the source CSVs, independently of SQL. No API key or network needed.
- **Make:** `make setup`, `make run` and `make test`, plus `make check` (lint + tests +
  `pip check`) and `make verify` (a clean-room install and test, as a new user would run it).
- **Tech:** Python 3.11 · LangGraph · LangChain (core, Google GenAI, OpenAI) · SQLAlchemy ·
  sqlglot · SQLite / MySQL (PyMySQL) · FastAPI · Streamlit · pytest · ruff

## Quick start

Needs Python 3.11 or newer. `make setup` uses [uv](https://docs.astral.sh/uv/) if it is
installed; otherwise it uses `python3.11 -m venv` and pip.

```bash
git clone https://github.com/agentic-ai-rakesh/atliq-tees-sql-agent.git && cd atliq-tees-sql-agent
make setup     # .venv + requirements.txt, .env from .env.example, builds data/atliq_tshirts.db
# edit .env: GOOGLE_API_KEY=...  (or LLM_PROVIDER=openai and OPENAI_API_KEY=...)
make test      # 74 tests, no API key needed
make run       # backend on :8000 + Streamlit on :8501 (Ctrl+C stops both)
```

Or run them in two terminals: `make backend`, then `make frontend`. The API docs are at
http://127.0.0.1:8000/docs.

### Make commands

| Command | What it does |
|---|---|
| `make setup` | Create `.venv` (Python 3.11), install `requirements.txt`, create `.env` from `.env.example`, build the database |
| `make db` | (Re)build `data/atliq_tshirts.db` from `source/*.csv` |
| `make mysql-sql` | Regenerate `source/db_creation_atliq_t_shirts.sql` from `source/*.csv` |
| `make backend` / `make frontend` / `make run` | FastAPI on :8000, Streamlit on :8501, or both |
| `make test` | Run the 74 tests |
| `make lint` / `make format` | Ruff lint and format check / auto-fix |
| `make check` | Lint, format check, tests and `pip check` (requirements are consistent) |
| `make verify` | Copy exactly the files git publishes into an empty folder, then run `make setup`, lint, the tests and `pip check` there. Writes `reports/test_results.txt`. |
| `make clean` | Remove the generated database, logs, reports and caches |
| `make help` | List the commands |

Without make:

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                      # add your API key
.venv/bin/python -m tshirt_agent.seed_data  # build the SQLite database from source/
.venv/bin/uvicorn api.main:app --port 8000
.venv/bin/streamlit run frontend/app.py
```

### Using MySQL instead of SQLite

```bash
make mysql-sql   # (re)writes source/db_creation_atliq_t_shirts.sql from source/*.csv
mysql -u root -p < source/db_creation_atliq_t_shirts.sql
```

Then create a read-only user (the script shows how) and set
`DATABASE_URL=mysql+pymysql://atliq_reader:<password>@localhost:3306/atliq_tshirts` in `.env`.

## How it works

### 1. Source data (`source/`, loaded by `tshirt_agent/seed_data.py`)

| File | Contents |
|---|---|
| `source/t_shirts.csv` | 80 t-shirts: `t_shirt_id, brand, color, size, price, stock_quantity` |
| `source/discounts.csv` | 10 discounts: `discount_id, t_shirt_id, pct_discount` |
| `source/db_creation_atliq_t_shirts.sql` | The same data as a MySQL script (generated by `make mysql-sql`) |

- **`t_shirts`:** `t_shirt_id`, `brand` (Van Heusen, Levi, Nike, Adidas), `color` (Red,
  Blue, Black, White), `size` (XS–XL), `price` (10–50), `stock_quantity` (10–100).
  There is one row for every brand × color × size combination: 80 rows.
- **`discounts`:** `discount_id`, `t_shirt_id` → `t_shirts`, `pct_discount` (0–100).
  There are 10 rows, for t-shirts 1–10 (Van Heusen, Red and Blue, all sizes), so other
  brands have no discount: for example, Levi's revenue is the same with or without discounts.
- **Validated on load:** column names, types, allowed brand/color/size values, price
  10–50, stock ≥ 0, unique ids, unique brand/color/size, discounts that point to an
  existing t-shirt (at most one each), and 0–100%. An error names the file and line.
- **Atomic build:** the database is built in a temporary file and swapped in, so a bad
  CSV never breaks the existing database.
- **Same for everyone:** the data is fixed, so answers and tests can be checked. To use
  your own data, edit the CSVs and run `make db`.
- The generated database `data/atliq_tshirts.db` is git-ignored.

### 2. SQL agent (`POST /api/ask`)

```
START → agent ──tool calls──▶ tools ──▶ agent … ──no tool calls──▶ finalize → END
```

- **Input check:** an empty question, or one over 500 characters, is rejected before
  any model call.
- **agent node:** the chat model gets the system prompt (`agent/prompts.py`, versioned).
  The prompt holds the rules, the SQL dialect and worked question→SQL examples
  (`agent/examples.py`). The model decides which tool to call next or writes the answer.
- **tools node:**
  - Runs `list_tables`, `describe_table` or `run_sql` and counts the round.
  - `run_sql` passes the query through the guard (`sql_guard.py`), then executes it on
    the read-only engine (`database.py`) with a time limit and row cap.
  - A rejected or failed query comes back as `ERROR: …`, so the agent can fix it.
- **Cap:** after the tool-round limit, the model loses its tools and must answer.
- **finalize node:** collects:
  - the answer
  - every query with its SQL, columns, rows (or error) and timing
  - the steps and the token usage summed over all model calls
  - `grounded` (at least one query succeeded)
  - `self_corrected` (a failed query was followed by a working one)
  - `unverified_numbers`

### 3. UI (Streamlit → API)
- **Sidebar:** the active model and limits, clickable example questions, and the schema
  with allowed values and keys.
- **Chat:** answer, then unverified-number warning (if any), then each SQL query (✅/❌)
  with its result table, then **How the agent worked**: steps, tokens, latency, model
  and prompt version.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/api/config` | Provider, model, database type, limits and whether the API key is set |
| GET | `/api/schema` | Tables, columns, allowed values, keys and row counts |
| GET | `/api/examples` | Sample questions |
| POST | `/api/ask` | `{"question": "..."}` → answer, queries, steps, usage, latency, checks |

**Error codes:**
- **422:** invalid question.
- **503:** API key missing, or database missing (with the fix: `make db`).
- **502:** the model call failed. Secrets are removed from the message.

## Configuration

Everything is set in `.env` (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `google` | `google` (Gemini) or `openai` |
| `GOOGLE_API_KEY` / `OPENAI_API_KEY` | none | Key for the chosen provider (required) |
| `LLM_MODEL` | `gemini-3.5-flash` / `gpt-5.5` | Model (default depends on the provider) |
| `LLM_TEMPERATURE` | empty (model default) | 0–2 |
| `LLM_MAX_TOKENS` / `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | 2000 / 60 / 2 | Model call limits |
| `AGENT_MAX_TOOL_ROUNDS` | 6 | Tool rounds before the agent must answer |
| `MAX_RESULT_ROWS` / `QUERY_TIMEOUT_SECONDS` | 50 / 10 | Per-query limits |
| `DATABASE_URL` | `sqlite:///data/atliq_tshirts.db` | SQLite file or `mysql+pymysql://…` |
| `LOG_DIR` | `logs` | Log folder (git-ignored) |
| `TSHIRT_AGENT_API_URL` | `http://127.0.0.1:8000` | Backend address used by Streamlit |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | off | Optional LangSmith traces |

Invalid values stop startup with a message naming the variable.

## Tests

```bash
make test
```

- **Model:** a scripted chat model makes the agent's tool calls predictable.
- **Database:** the tests build their own SQLite database from `source/`.
- **Expected values:** computed in plain Python from the source CSV rows, never with SQL.
  This also proves the worked SQL examples given to the agent are correct.
- No API key or network is needed.

| File | Covers |
|---|---|
| `test_sql_guard.py` | Allowed SELECT / CTE / JOIN / UNION / subquery; refused DELETE, UPDATE, DROP, INSERT, multiple statements, PRAGMA, ATTACH, unknown table, db prefix, SLEEP, load_extension, invalid and over-long SQL, MySQL `INTO OUTFILE`, write inside a CTE |
| `test_source_data.py` | The committed CSVs load (80 + 10 rows); each kind of bad row is rejected with file and line (brand, color, size, price, negative stock, non-number, duplicate id, duplicate combination, unknown t-shirt, discount over 100, second discount, bad percent); wrong header; missing file; a failed build leaves the old database untouched |
| `test_database.py` | Source data complete and valid, MySQL script matches the source, committed MySQL script up to date, schema with values and keys, example queries return the Python truth, row cap, DB errors, slow-query stop, connection read-only even without the guard, missing DB file |
| `test_agent.py` | Schema → SQL → answer, self-correction after a SQL error, write request refused, unverified numbers flagged, tool-round cap, invalid questions, tools directly, number matching with rounding, zero and code spans ignored |
| `test_api.py` | Health, config, schema, examples, ask, 422, 502 with secrets removed, 503 for missing DB and missing key |
| `test_config.py` | Defaults, provider switch, MySQL URL and limits, invalid values, missing or placeholder key |

### Test cases and results

`make verify` copies exactly the files git publishes into an empty folder (no `.venv`,
`.env` or database), then runs `make setup`, lint, the tests and `pip check` there. It ran
twice: with uv, and with plain `python3.11 -m venv` + pip from `requirements.txt` (no uv).
Both times on Python 3.11.18: **74 passed in 2.19s**, lint clean, and `pip check` found no
broken requirements.

**Live run** (OpenAI GPT-5.5, the default SQLite data), each answer checked against
values computed in Python:

| Question | Agent's steps | Answer | Expected |
|---|---|---|---|
| How many Nike t-shirts in XS and white are left? | describe_table → run_sql | 43 | 43 ✅ |
| Revenue from all Levi's t-shirts after discounts? | describe_table ×2 → run_sql (LEFT JOIN discounts) | $33,112.00 | 33,112 ✅ |
| Which brand has the highest stock value, compared with Nike? | list_tables → describe_table → run_sql (CTE) | Adidas 34,157 vs Nike 34,098 (+59) | 34,157 / 34,098 ✅ |
| Delete all the discounts | none | Refused: the database is read-only | ✅ |

Each answer took 2–8 seconds and 0.8k–4.4k tokens. No numbers were flagged as unverified.

<details>
<summary>All 74 test cases</summary>

| File | Test case | Result |
|---|---|---|
| `test_agent` | Agent inspects schema runs sql and answers from results | ✅ passed |
| `test_agent` | Agent fixes its own sql error | ✅ passed |
| `test_agent` | Write requests are refused by the tool | ✅ passed |
| `test_agent` | Numbers not in the results are flagged | ✅ passed |
| `test_agent` | Tool rounds are capped | ✅ passed |
| `test_agent` | Invalid questions never reach the model: empty / blank | ✅ passed |
| `test_agent` | Invalid questions never reach the model: question over 500 characters | ✅ passed |
| `test_agent` | Tools directly | ✅ passed |
| `test_agent` | Unverified number matching allows rounding | ✅ passed |
| `test_agent` | Zero and code spans are not claims | ✅ passed |
| `test_api` | Health config schema examples | ✅ passed |
| `test_api` | Ask returns answer queries and steps | ✅ passed |
| `test_api` | Empty question is 422 | ✅ passed |
| `test_api` | Model errors are 502 with secrets removed | ✅ passed |
| `test_api` | Missing database is 503 with the fix | ✅ passed |
| `test_api` | Missing API key is 503 with the fix | ✅ passed |
| `test_config` | Defaults | ✅ passed |
| `test_config` | Switching to OpenAI picks its default model and key | ✅ passed |
| `test_config` | MySQL URL and limits from env | ✅ passed |
| `test_config` | Invalid values name the variable: `LLM_PROVIDER=palm` | ✅ passed |
| `test_config` | Invalid values name the variable: `MAX_RESULT_ROWS=lots` | ✅ passed |
| `test_config` | Invalid values name the variable: `LLM_TEMPERATURE=5` | ✅ passed |
| `test_config` | Missing or placeholder key is refused: empty / blank | ✅ passed |
| `test_config` | Missing or placeholder key is refused: `your_google_api_key_here` | ✅ passed |
| `test_database` | Source data is complete and valid | ✅ passed |
| `test_database` | MySQL script has the same data as source | ✅ passed |
| `test_database` | Committed MySQL script is up to date | ✅ passed |
| `test_database` | Schema lists tables values and keys | ✅ passed |
| `test_database` | Example query matches the Python ground truth: Nike XS white stock = 43 | ✅ passed |
| `test_database` | Example query matches the Python ground truth: S-size inventory value = 26157 | ✅ passed |
| `test_database` | Example query matches the Python ground truth: Levi revenue after discounts = 33112 | ✅ passed |
| `test_database` | Example query matches the Python ground truth: Levi revenue without discounts = 33112 | ✅ passed |
| `test_database` | Top brand example | ✅ passed |
| `test_database` | Rows are capped and marked truncated | ✅ passed |
| `test_database` | Database errors become query errors | ✅ passed |
| `test_database` | Slow queries are stopped | ✅ passed |
| `test_database` | Connection is read only even without the guard | ✅ passed |
| `test_database` | Missing database file says run make db | ✅ passed |
| `test_source_data` | The committed source files load | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: brand 'Puma' is not one of | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: color 'Green' | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: size 'XXL' | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: price must be between 10 and 50 | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: cannot be negative | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: price must be a whole number | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: duplicate t_shirt_id 1 | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: duplicate brand/color/size | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: t_shirt_id 9 does not exist | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: between 0 and 100 | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: already has a discount | ✅ passed |
| `test_source_data` | Invalid rows are rejected with file and line: must be a number | ✅ passed |
| `test_source_data` | Wrong header and missing file | ✅ passed |
| `test_source_data` | A failed build leaves the old database untouched | ✅ passed |
| `test_sql_guard` | Read only selects are allowed: `SELECT SUM(stock_quantity) FROM t_shirts WHERE brand = 'Nike'` | ✅ passed |
| `test_sql_guard` | Read only selects are allowed: `select brand, count(*) from t_shirts group by brand;` | ✅ passed |
| `test_sql_guard` | Read only selects are allowed: `WITH levi AS (SELECT * FROM t_shirts WHERE brand = 'Levi') SELECT S…` | ✅ passed |
| `test_sql_guard` | Read only selects are allowed: `SELECT t.brand FROM t_shirts t LEFT JOIN discounts d ON d.t_shirt_i…` | ✅ passed |
| `test_sql_guard` | Read only selects are allowed: `SELECT brand FROM t_shirts UNION SELECT 'x'` | ✅ passed |
| `test_sql_guard` | Read only selects are allowed: `SELECT (SELECT MAX(pct_discount) FROM discounts) AS top` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: empty / blank | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `DELETE FROM t_shirts` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `UPDATE t_shirts SET price = 1` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `DROP TABLE discounts` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `INSERT INTO discounts VALUES (1, 1, 1)` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `SELECT 1; DROP TABLE t_shirts` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `PRAGMA query_only = OFF` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `ATTACH DATABASE 'x.db' AS x` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `SELECT * FROM users` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `SELECT * FROM main.t_shirts` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `SELECT SLEEP(5)` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `SELECT load_extension('x')` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: `SELECT nonsense FROM` | ✅ passed |
| `test_sql_guard` | Unsafe or invalid SQL is refused: SQL longer than 4000 characters | ✅ passed |
| `test_sql_guard` | Mysql dialect and write inside cte are refused | ✅ passed |

</details>

## Project structure

```
research_sqldb_t-shirts/
├── api/main.py                    FastAPI backend
├── frontend/app.py                Streamlit UI (API client)
├── tshirt_agent/
│   ├── config.py                  Settings from .env, validated
│   ├── logging_setup.py           Console + rotating file log
│   ├── seed_data.py               Load + validate source/*.csv → SQLite / MySQL script
│   ├── sql_guard.py               Read-only SQL guard (sqlglot)
│   ├── database.py                Read-only engine, schema, guarded query execution
│   ├── models.py                  Gemini / OpenAI chat model
│   └── agent/
│       ├── examples.py            Worked question → SQL examples
│       ├── prompts.py             Versioned system prompt
│       ├── tools.py               list_tables, describe_table, run_sql
│       ├── graph.py               LangGraph agent ⇄ tools → finalize, number check
│       └── sql_agent.py           ask(): validate, build tools, run graph
├── source/                        Source data
│   ├── t_shirts.csv               80 t-shirts
│   ├── discounts.csv              10 discounts
│   └── db_creation_atliq_t_shirts.sql   Same data for MySQL (make mysql-sql)
├── docs/first_version_ui.png      Screenshot of the first version's UI
├── notebooks/t_shirt_sales_llm.ipynb         Original prototype (reference only)
├── tests/                         74 tests + fakes and ground truth
├── requirements.txt · Makefile · pyproject.toml · .env.example · .gitignore
```

## What changed from the first version

- **Agent:** the single `SQLDatabaseChain` call is now a LangGraph agent. It inspects the
  schema, corrects its own SQL errors and shows its work.
- **Model:** Google PaLM (retired) is replaced by Gemini or OpenAI through current
  LangChain packages. The old `langchain==0.0.284` imports no longer exist.
- **Database access:** the hard-coded MySQL `root`/`root` login is replaced by a
  configurable `DATABASE_URL`. SQLite is the default, so `make setup` works without a
  database server.
- **Data:** random data (`RAND()`, a different dataset on every install) is replaced by
  validated source CSV files in `source/`. The brand name typo "Van Huesen" is fixed.
  The first version's UI is kept as a screenshot in `docs/first_version_ui.png`.
- **Few-shot examples:** made-up answers such as "Answer: 290" for different questions
  are replaced by worked SQL examples that tests verify against the real data.
- **Safety:** any SQL used to reach the database. Now there is a SELECT-only guard, a
  read-only connection, a time limit and a row cap.
- **Answers:** answers used to come without their SQL. Now every answer shows its queries
  and rows, and numbers are checked against them.
- **Setup:** unused heavy dependencies are removed (Chroma, sentence-transformers, FAISS,
  pinned protobuf), and requirements are pinned to working ranges.
- **Secrets:** a Google API key hard-coded in the notebook is removed. `.gitignore` and
  `.env.example` are added.
- **New:** FastAPI backend, Makefile with `make setup`, tests and logging.
