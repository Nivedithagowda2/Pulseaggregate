"""Create tables and indexes (idempotent).   python -m scripts.init_db"""
from pulse.db import apply_sql_file, connect

if __name__ == "__main__":
    with connect() as conn:
        apply_sql_file(conn, "schema.sql")
        apply_sql_file(conn, "indexes.sql")
    print("schema and indexes ready")
