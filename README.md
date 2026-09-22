# Robot Joint Current Monitor

CSCN8010 · Data Streaming and Visualization Workshop · Robot predictive maintenance

**Problem.** A Kawasaki material-handling robot lost 480 minutes of production to a worn torque tube
that nobody saw coming, because nothing watched the robot's health while it ran. This project replays
the controller's per-joint current as a live stream, stores it in Neon PostgreSQL, shows it on a live
dashboard, and turns the history into maintenance alerts.

**Dataset.** `dataset/RMBR4-2_export_test.csv`: one robot, 39,672 readings, 17–18 Oct 2022. Supplied
as CSCN8010 course material; it is not public, so no link or licence applies.

## Team - Group 2

| Member  | Student ID |
| ------- | ---------- |
| Koushik Balne| 9087547    |
| Ubaid Ullah  | 9110715    |
| Sukhchain Singh    | 9111541    |

## Installation

Needs **Python 3.14** (pinned in `.python-version`) and a Neon PostgreSQL database.

**2. Install the dependencies.** From the project folder:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\activate` instead.

**3. Add the database connection string.** Copy `.env.example` to `.env` and paste in your Neon
connection string:

```text
DATABASE_URL=postgresql://<user>:<password>@<host>/<database>?sslmode=require
```

**4. Load the database** (optional, since the notebook does this on its first run). `seed` creates the
`cat_dc` schema and copies the CSV in; `check` prints `MATCH` when Neon holds exactly what the CSV does.

```bash
python -m persistence.bootstrap_db seed
python -m persistence.bootstrap_db check
```

**5. Run the notebook.** Start Jupyter from the project folder, open
`DataStreamVisualization_workshop.ipynb` and run every cell. A full run takes about 3 minutes; the
live stream in Step 2 accounts for 2 of them.

```bash
jupyter lab
```

**6. Run the web monitor** (optional):

```bash
python -m monitor_web.server
```

Then open [http://127.0.0.1:8060](http://127.0.0.1:8060). Stop it with `Ctrl+C`.
