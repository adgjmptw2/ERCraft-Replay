# Whole-file optimization, 2026-09-24

Same local 15,410,594-byte 12.1.0 replay; 68,430 records and 439,764 decoded items.
Single-thread setting (`GOMAXPROCS=1`), GC enabled, alternating baseline and optimized
executables. All runs read the original .er and perform every payload decode; no
decoded cache, packet filter, or result projection is used. Full JSON export is excluded.

## Observed process wall time

| Variant | Trial | Start KST | End KST | Seconds |
| --- | ---: | --- | --- | ---: |
| Before | 1 | 18:20:04.343 | 18:20:07.705 | 3.358021 |
| Optimized | 1 | 18:20:07.733 | 18:20:08.949 | 1.215496 |
| Before | 2 | 18:20:08.953 | 18:20:11.874 | 2.920057 |
| Optimized | 2 | 18:20:11.875 | 18:20:12.882 | 1.006969 |
| Before | 3 | 18:20:12.884 | 18:20:15.801 | 2.916817 |
| Optimized | 3 | 18:20:15.802 | 18:20:16.833 | 1.030259 |

Medians: **2.920057s -> 1.030259s**, **2.83x faster / 64.7% less wall time**.
The earlier Python measurement (unchanged implementation) was 7.324749s;
that comparison is historical, not a simultaneous rerun of Python.
Filesystem caches were not flushed. First-process launch overhead is included.

## Evidence and changes

CPU profiling showed about 31% in Windows system calls and substantial allocation/GC
work. The allocation profile attributed most allocated bytes to repeated Brotli
reader creation and decoder buffers. Changes were measured incrementally:

1. Reuse a Brotli Reader with Reset per independent stream. Returned byte slices are
   independently owned; recovery after corrupt/limited streams is regression-tested.
2. Buffer record reads and track logical offsets, removing a Seek and small OS reads
   per record on both passes.
3. Decode vectors into fixed typed arrays, avoiding one interface allocation per coordinate.
4. Reserve wrapper collection capacity once after validating minimum wire size,
   avoiding repeated large slice growth/copying.

BenchmarkDecodeFile allocation accounting:

| State | Cumulative bytes allocated / replay | Allocation count / replay |
| --- | ---: | ---: |
| Before | 4,929,486,482 | 8,267,159 |
| Optimized | 436,423,832 | 6,409,637 |

This is **cumulative heap allocation**, not peak RAM or process working set.
Allocation volume decreased about 91.1%. The final in-process benchmark was 0.935s;
the process-wall measurement above includes program startup and summary output.

## Correctness

- The optimized full NDJSON output was compared against the original Python decoder:
  all 439,764 values, raw packets, record metadata, states and ordering matched.
- Existing unit/CLI tests and all 10,937 generated Python-oracle fixtures pass.
- New tests cover Brotli reuse, retained output ownership, invalid-stream recovery,
  and reset after the decompressed-size limit is reached.
- go vet passes. Original .er and original Python implementation remain unchanged.

Local evidence (match data is not committed):

- `C:/Users/kjg/ERCraft-Replay-benchmark/optimized-observed-timings.json`
- `C:/Users/kjg/ERCraft-Replay-benchmark/optimized-differential.json`
- `C:/Users/kjg/ERCraft-Replay-benchmark/optimized-export-summary.json`
- `C:/Users/kjg/ERCraft-Replay-benchmark/before-cpu.prof`
- `C:/Users/kjg/ERCraft-Replay-benchmark/before-mem.prof`

Reproduce profiling with a local input:

```powershell
$env:GOMAXPROCS = '1'
$env:ER_GO_REPLAY = 'C:\path\match.er'
go test ./replay -run '^$' -bench BenchmarkDecodeFile -benchtime 3x
```

These measurements still exclude combat/skill calculations, SQLite integration and
viewer export. Actual-match equivalence is verified on one 12.1.0 replay; other
supported versions retain synthetic fixture coverage.
