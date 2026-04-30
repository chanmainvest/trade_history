import sqlite3
c = sqlite3.connect('data/ledger.sqlite')
for r in c.execute("select relpath, parser_name, parse_status from source_files where parse_status='failed' order by relpath"):
    print(r[2], r[1], r[0])
