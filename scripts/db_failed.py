import sqlite3

connection = sqlite3.connect("data/ledger.sqlite")
for row in connection.execute("select relpath, parser_name, parse_status from source_files where parse_status='failed' order by relpath"):
    print(row[2], row[1], row[0])
