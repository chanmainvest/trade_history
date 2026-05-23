import sqlite3

connection = sqlite3.connect("data/ledger.sqlite")
for row in connection.execute("select relpath, parse_status, parse_error from source_files where parse_status in ('failed','partial') order by relpath"):
    print(row[1], "|", row[0], "|", (row[2] or "")[:140])
