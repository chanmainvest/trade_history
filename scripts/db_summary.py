import sqlite3

connection = sqlite3.connect("data/ledger.sqlite")


def q(sql):

    return connection.execute(sql).fetchall()


print("source_files by status:", q("select parse_status, count(*) from source_files group by 1"))
print("per institution (statements):")
for row in q("""select i.code, count(distinct s.statement_id) from statements s
              join accounts a on a.account_id=s.account_id
              join institutions i on i.institution_id=a.institution_id group by 1 order by 1"""):
    print("  ", row)
print("source_file relpath prefixes:")
for row in q("""select substr(relpath,1,30), parse_status, count(*) from source_files
              group by 1,2 order by 1,2"""):
    print("  ", row)
print("statements:", q("select count(*) from statements")[0])
print("txns:", q("select count(*) from transactions")[0])
print("positions:", q("select count(*) from position_snapshots")[0])
print("cash_balances:", q("select count(*) from cash_balances")[0])
print("quarantine:", q("select count(*) from quarantine_transactions")[0])
print("accounts:")
for row in q("""select i.code, a.name, a.base_currency, a.account_number
              from accounts a join institutions i on i.institution_id=a.institution_id"""):
    print("  ", row)
print("txn_types:")
for row in q("select txn_type, count(*) from transactions group by 1 order by 2 desc"):
    print("  ", row)
print("asset_types:", q("select asset_type, count(*) from instruments group by 1 order by 2 desc"))
