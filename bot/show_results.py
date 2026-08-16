# show_results.py

import pandas as pd
from pathlib import Path

symbols = [
    'BTC_USDT', 'ETH_USDT', 'BNB_USDT', 'XRP_USDT',
    'ADA_USDT', 'DOGE_USDT', 'SOL_USDT'
]

print("\n" + "=" * 80)
print("RESEARCH RESULTS FOR ALL 7 SYMBOLS")
print("=" * 80)

for symbol in symbols:
    print(f"\n{symbol}")
    print("-" * 80)

    val_file = f'research_data/{symbol}_validated_features.csv'

    try:
        df = pd.read_csv(val_file)

        if df.empty:
            print(f"  ✗ File is empty — confidence_level bug not yet fixed")
            continue

        # Show counts by recommendation
        total = len(df)
        strong_keep = (df['recommendation'] == 'STRONG KEEP').sum()
        keep        = (df['recommendation'] == 'KEEP').sum()
        review      = (df['recommendation'] == 'REVIEW').sum()
        delete      = (df['recommendation'] == 'DELETE').sum()

        print(f"  Total features tested : {total}")
        print(f"  ✓✓ STRONG KEEP        : {strong_keep}")
        print(f"  ✓  KEEP               : {keep}")
        print(f"  ⚠  REVIEW             : {review}")
        print(f"  ✗  DELETE             : {delete}")
        print()

        # Show each feature with correct column names
        print(f"  {'Feature':<30} | {'Recommendation':<12} | {'p-value':<10} | {'IC':>7} | {'IC Rank'}")
        print(f"  {'-'*30}-+-{'-'*12}-+-{'-'*10}-+-{'-'*7}-+-{'-'*10}")

        for _, row in df.iterrows():
            print(
                f"  {row['feature']:<30} | "
                f"{row['recommendation']:<12} | "
                f"{row['p_value_mannwhitney']:.6f}   | "
                f"{row['ic_overall']:>+.4f} | "
                f"{row['ic_rank']}"
            )

    except FileNotFoundError:
        print(f"  ✗ File not found: {val_file}")
        print(f"    Run: python -m bot.run_research_all")
    except KeyError as e:
        print(f"  ✗ Column not found: {e}")
        print(f"    Available columns: {list(pd.read_csv(val_file).columns)}")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)