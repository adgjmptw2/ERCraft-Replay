# Whole-file observed benchmark

Measured 2026-09-24 (KST), Windows, Ryzen 7 9850X3D, Python 3.11.9 / Go 1.27.1.

Source: local 12.1.0 replay, 15,410,594 bytes. No synthetic/repeated selected-packet input.
All runs decoded 68,430 records and 439,764 packets/snapshots; no failures.

| Engine | Trial | Start (KST) | End (KST) | Process wall seconds |
| --- | ---: | --- | --- | ---: |
| python | 1 | 17:57:17.827 | 17:57:25.154 | 7.324749 |
| go | 1 | 17:57:25.184 | 17:57:28.121 | 2.936573 |
| python | 2 | 17:57:28.124 | 17:57:35.431 | 7.306741 |
| go | 2 | 17:57:35.432 | 17:57:38.182 | 2.749897 |
| python | 3 | 17:57:38.184 | 17:57:45.658 | 7.473610 |
| go | 3 | 17:57:45.659 | 17:57:48.427 | 2.767548 |

Python median: 7.324749s. Go median: 2.767548s.
Observed end-to-end decoding speedup: 2.65x.

Method: three alternating subprocess runs per engine; GOMAXPROCS=1; GC enabled.
Wall time includes startup, schema load, source hash, definitions, record reads,
Brotli, envelopes, every command and snapshot decode, and summary output.
It excludes a full decoded JSON export, combat/skill analytics, viewer generation
and service acquisition. Input filesystem cache was not flushed.
Python uses the unchanged repository decoder through tools/compare_python.py;
Go uses cmd/erdecode, without calling Python. This is a single-process reader
comparison, not a benchmark against the existing multi-process service.

A separate full NDJSON export from Go completed in 7.158s internally, of which
4.763s was record output. It was compared field-by-field with Python:
all 439,764 decoded values, raw packet bytes, record metadata and wire order matched.
That comparison took 12.549s; it is validation time, not Python decoder benchmark time.

The previous 128x figure applied only to three specialized packet decoders.
This port handles all published schema types via compiled shared layouts, so that
microbenchmark is not a prediction for this broader full-file workload.

Local raw observations: C:/Users/kjg/ERCraft-Replay-benchmark/full-observed-timings.json.
Local full comparison report: C:/Users/kjg/ERCraft-Replay-benchmark/full-differential.json.
Raw local match data and decoded outputs were not added to Git.
