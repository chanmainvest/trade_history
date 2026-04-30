import sqlite3
c = sqlite3.connect('data/ledger.sqlite')
def q(sql):
    return c.execute(sql).fetchall()
print('source_files by status:', q("select parse_status, count(*) from source_files group by 1"))
print('per institution (statements):')
for r in q("""select i.code, count(distinct s.statement_id) from statements s
              join accounts a on a.account_id=s.account_id
              join institutions i on i.institution_id=a.institution_id group by 1 order by 1"""):
    print('  ', r)
print('source_file relpath prefixes:')
for r in q("""select substr(relpath,1,30), parse_status, count(*) from source_files
              group by 1,2 order by 1,2"""):
    print('  ', r)
print('statements:', q('select count(*) from statements')[0])
print('txns:', q('select count(*) from transactions')[0])
print('positions:', q('select count(*) from position_snapshots')[0])
print('cash_balances:', q('select count(*) from cash_balances')[0])
print('quarantine:', q('select count(*) from quarantine_transactions')[0])
print('accounts:')
for r in q("""select i.code, a.name, a.base_currency, a.account_number
              from accounts a join institutions i on i.institution_id=a.institution_id"""):
    print('  ', r)
print('txn_types:')
for r in q('select txn_type, count(*) from transactions group by 1 order by 2 desc'):
    print('  ', r)
print('asset_types:', q('select asset_type, count(*) from instruments group by 1 order by 2 desc'))
