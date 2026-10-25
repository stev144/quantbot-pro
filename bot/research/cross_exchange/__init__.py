# claude code changed: new package — 2026-10-25, Cross-Exchange Statistical Arbitrage research, PHASE A ONLY:
# a read-only, manually started recorder of public order-book data from Binance, Kraken and Coinbase.
#
# RESEARCH-ONLY. No order, cancel, withdrawal, transfer or account endpoint is used anywhere in this package; no API
# key is read or required; nothing here is imported by the live execution engine, bot_runner.py, the strategy router
# or any scheduler. Phase B (gap study), Phase C (preregistration) and any execution are NOT implemented.
#
#   config.py    instruments, venue-symbol mapping, comparability classes, defaults
#   books.py     pure order-book state + each venue's official synchronisation procedure (unit-tested offline)
#   feeds.py     aiohttp WebSocket clients (public endpoints only) that drive books.py
#   recorder.py  session, sampler, JSON-Lines storage, metadata/provenance, shutdown summary
RECORDER_VERSION = "SQT-XARB-REC-1"
SCHEMA_VERSION = "xarb-obs-v1"
