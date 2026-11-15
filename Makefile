.PHONY: run run-memory test numbers numbers-hydradb wikidata mcp
run:              ## app against HydraDB (HYDRADB_URL, HYDRADB_AUTH_TOKEN)
	uvicorn app:app --port 8000
run-memory:       ## app with the in-memory reference store
	PHYSARUM_STORE=memory uvicorn app:app --port 8000
test:
	python -m pytest -q tests
numbers:          ## regenerate static/numbers.json + NUMBERS.md
	python scripts/make_numbers.py --seeds 5
numbers-hydradb:  ## same, plus latency measured on a running HydraDB
	python scripts/make_numbers.py --seeds 5 --hydradb
wikidata:         ## refresh the real 2020->2026 changes from live Wikidata
	python scripts/fetch_wikidata_changes.py
mcp:
	python mcp_server.py
