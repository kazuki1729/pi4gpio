#!/usr/bin/env python3
"""SQLite正本を読み取り専用で開き、direct_baseline.sqlを実行する。"""

import argparse
import sqlite3
from pathlib import Path
from urllib.parse import quote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("sql", type=Path)
    args = parser.parse_args()

    database_uri = "file:" + quote(str(args.database.resolve())) + "?mode=ro"
    query = args.sql.read_text(encoding="utf-8")
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(query).fetchone()
    if row is None:
        raise SystemExit("baseline query returned no row")
    print(row[0])


if __name__ == "__main__":
    main()
