"""Throwaway: dump sample_portfolio.xlsx sheets."""
import pandas as pd

p = r"c:\Users\hevan\work\chanmainvest\portfolio_dashboard\sample_portfolio.xlsx"
sheets = pd.read_excel(p, sheet_name=None)
for name, df in sheets.items():
    print(f"--- sheet: {name} ({len(df)} rows) ---")
    print(df.columns.tolist())
    print(df.head(40).to_string())
    print()
