# Focused Benchmark removal audit

Read-only inspection completed before source edits, 2026-09-06. Working tree: miner-dashboard-app-store (1.2.28); installed source is an older 1.2.25 copy. No miners were contacted. Installed thermal_locks.json, benchmark_sessions.json and benchmark_restore_profiles.json were empty objects when inspected. No production data was changed.

| Component | Classification and responsibility | Action |
| --- | --- | --- |
| static/benchmark.html, .js, .css | A: standalone tuner page, polling, candidate selection, reports and recovery controls | Remove |
| Dashboard, Miners, Thermal Settings navigation | A: links to /benchmark | Remove links |
| Benchmark page buttons | A: Prepare, Run Candidate, Run Full Benchmark, Cancel Active, Export Report, Refresh, Retry Restore, I Restored It Manually | Remove with page |
| benchmark_profiles.py | A: device matching, frequency/voltage bounds, timing and safety cutoffs | Remove |
| benchmark_engine.py | A: candidate matrix, sampling summaries, safety evaluation and recommendations | Remove |
| benchmark_sessions.py | A: atomic session persistence, transitions, runner state and retention | Remove |
| benchmark_results.py | A: candidate results, summaries and report export | Remove |
| benchmark_restore.py | A: captured miner settings and thermal profile, restore/recovery persistence | Remove |
| app_v2.py Benchmark helpers and globals | A: prepare/run/cancel workers, guarded writes, restore verification, reporting and recovery | Remove |
| app_v2.py miner status/counts/events and diagnostics | B: shared presentation with isolated Benchmark branches/storage metric | Remove only Benchmark additions |
| dashboard.js/.css and TUI operations.py | B: shared views with Benchmark counter, status styling and storage row; no TUI tuner/start controls found | Remove only Benchmark additions |
| thermal_locks.py | B: generic lock persistence and ownership checks used by thermal controller | Preserve unchanged |
| thermal_locks.create_lock/release_lock/write_locks | C: no remaining production caller, but generic lock compatibility API with independent tests | Preserve harmlessly |
| miner_thermal_mode.py | B: normal thermal control, temperature recovery and lock-based suppression | Preserve unchanged |
| miner_api.py, miner_telemetry.py | B: discovery, telemetry and normal thermal setting writes | Preserve unchanged |
| Dockerfile | B: image build copied Benchmark modules explicitly | Remove those copy arguments |
| Benchmark tests; Benchmark portion of test_thermal_settings.py | A: removed behavior | Remove; retain normal thermal tests |
| Production Benchmark JSON files | C: historical data, independent of normal history/runs/configuration | Leave untouched and unreferenced |

## Routes

GET /benchmark, /api/benchmark, /api/benchmark/report?session_id=... . Static /static/benchmark.html, /static/benchmark.js and /static/benchmark.css were also directly served.

POST /api/benchmark/start, /prepare, /cancel, /cancel-active, /run-candidate, /run-full, /retry-restore and /confirm-manual-restore (all under /api/benchmark). Remove handlers and same-origin protected-path entries together; missing paths use existing 404 behavior.

## Shared-state and startup findings

read_miner derived BENCHMARK from a benchmark-owned thermal lock, not session state, and overrode normal online/frequency classification. Normal thermal control skips any owned lock. Removing UI alone would leave active routes, imports, startup miner restoration, persistence cleanup, status overrides and lock effects. Deleting modules alone would break imports and Docker COPY; deleting assets alone would break the page handler and leave stale navigation. Remove each connected piece together.

Benchmark recovery ran independently in __main__ after startup_discovery; report retention was a second independent call. Recovery could write miner settings and release locks; report status/read endpoints could prune persisted records. Neither is needed by normal startup, history collection, discovery, run persistence or temperature recovery. Remove both calls and their implementation. Preserve normal startup and thermal locking. Deployment to another installation with active sessions or unresolved locks would require resolving them before upgrading; never silently clear locks or restore miners during this change.

## Removal plan

1. Remove isolated backend, routes, page/assets, navigation and build references.
2. Remove Benchmark additions to shared presentation; preserve thermal/API behavior and dormant lock compatibility.
3. Retain normal tests, add offline route/removal regression checks, run tests with temporary data and network blocked.
4. Completed the two mobile cleanups in dashboard.css: a compact Page 1 header and readable, scrollable Page 2 table with separate hashrate/frequency columns.

## Validation

- 66 targeted offline checks passed: removed GET/POST/static routes return 404, normal pages remain available, startup leaves legacy persistence untouched, normal miner status and thermal behavior remain functional, and TUI storage renders without Benchmark.
- Full remaining suite before the final two checks: 117 tests, 111 passed, five failures and one error. Unchanged Git HEAD reproduced the same six failing test IDs (222 tests). Existing failures concern stale web layout expectations, TUI overview/offsite expectations, and the removed #system-content selector. They were not changed as part of this focused removal.
- Python AST parsing, dashboard.js syntax, Docker COPY source existence and git diff --check passed.
- Tests used temporary data with socket connections blocked. Discord test output was mocked; no messages were sent. No application services were started or restarted, no miners were contacted, and no production data was written. Changes are in the Git checkout only, not deployed.
- Mobile follow-up completed: 14 targeted checks passed with the three already-known web failures; synthetic browser checks passed at eight viewport sizes, with unchanged desktop measurements at 768, 1280 and 1440 pixels.
