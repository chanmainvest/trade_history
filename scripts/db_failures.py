import sqlite3
c = sqlite3.connect('data/ledger.sqlite')
for r in c.execute("select relpath, parse_status, parse_error from source_files where parse_status in ('failed','partial') order by relpath"):
    print(r[1], '|', r[0], '|', (r[2] or '')[:140])
