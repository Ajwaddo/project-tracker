# Project Tracker Prototype

Streamlit + SQLite prototype for a DA / DS / DE team tracker.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

The first run creates `project_tracker.db` automatically and seeds sample users, projects, tasks, and comments.

## Included

- DA, DS, and DE top-level tabs
- Lane-specific subtabs you can create on the fly
- Task creation and assignment
- Status updates
- Comments
- Dashboard metrics for progress review
- Team management for adding lane users

## Files

- `app.py` Streamlit UI
- `db.py` SQLAlchemy models and SQLite setup
- `project_tracker.db` created on first run
