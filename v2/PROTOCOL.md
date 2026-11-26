# v2 protocol (draft, 2026-11-26)

Goal: on one fixed, dedicated box, show how each method performs as the data grows, **each at its best configuration
for that hardware**, with the sweep behind every best point. No artificial limits (no reserved RAM, no fixed memory
budgets): settings such as zram size or zswap pool size are tuning knobs that are swept like the buffer pool.

## Hardware and OS
- Vultr bare metal **vbm-6c-32gb-amd**: AMD EPYC 4245P (Zen 5, 6 cores / 12 threads), 32 GB DDR5, 2 x 960 GB NVMe.
- Ubuntu 26.04 (installed as 24.04, upgraded in place), all updates applied, one kernel for the whole run.
- **Single-disk focus**: the database (datadir, binlogs, and any disk swap file) on NVMe #2; NVMe #1 holds the OS and
  the pristine data copies used for resets (a reset is an NVMe-to-NVMe copy).
- The canary (tuner-canary) is used only for prototyping and validation.
- Before the series: `fstrim` both disks. Before every run: a 30 s `fio` 16K random-write latency check on the data disk;
  a run whose latency is >20% off the series baseline is flagged (v1 lost a series to a slow-SSD period).
- Calibration first (1 h): per-page cost of a fault served from zram (lz4, zstd), from zswap, from NVMe; raw minor-fault
  cost. Same calibration on the canary for comparison.

## Server
- One build for every engine: Percona Server 9.7.2-3 from source (RelWithDebInfo) with MyRocks and TidesDB
  (EvgeniyPatlan/tidesdb-mysql v0.5.0, TidesDB v10.0.1) built in: `bench/v2/build-ps97-tidesdb.sh` (aituner repo).
- **Binary log on for every engine** (TidesDB hangs all commits with it off: TC_LOG_MMAP overflow, see
  `tidesdb-tc-log-mmap-hang.md`):
  `log_bin`, `sync_binlog = 0`, `binlog_expire_logs_seconds = 60`, `max_binlog_size = 256M`, ROW format;
  `innodb_flush_log_at_trx_commit = 1` (default). Purge keeps only the current binlogs; resets start from clean copies.
- Same server settings for every run except the memory knobs under test.

## Data and workload
- sysbench 1.0.20 with sysbench-compressible, `--data-random-pct=10` (~3x lz4); one size also at 30 (~2.2x).
- Access patterns: **uniform**, and **zipfian** (`--rand-zipfian-exp` 0.8 and 1.0), plus a scrambled zipfian (hot rows
  spread over the table) at two sizes.
- Data sizes chosen from measured compressibility and a miss-ratio curve per pattern, so the compressed working set is
  ~0.5x, 0.8x, 1.0x, 1.3x and 1.8x of available RAM; fresh load per size; loaded size verified.
- oltp_read_write, 64 threads (16 threads for the best points), write conflicts counted.

## Methods (each tuned to its best)
| Method | Swept |
|---|---|
| InnoDB, normal swap | buffer pool |
| InnoDB + zram lz4 + disk swap fallback (same disk) | buffer pool x zram size |
| InnoDB + zswap lz4 (shrinker on and off) | buffer pool x pool cap |
| InnoDB ROW_FORMAT=COMPRESSED (KBS 8) | buffer pool |
| MyRocks lz4 | block cache |
| TidesDB lz4 | block cache (+ memtable) |
zstd only where lz4 does not fit.

## Each run
- Restore from the pristine copy, start, adaptive warm-up (until the pool is full and tps over the last 2 min is within
  3% of the previous 2 min; min 10, max 30 min), 15 min measured.
- Stall watchdog: memory pressure (PSI "full") > 50% for 60 s aborts the run and records a stall.
- Recorded: tps, p95/p99, errors, CPU user/sys/iowait, CPU per 1k tps, disk reads/writes and write latency, swap and
  zram/zswap counters (incl. writeback), PSI, mysqld RSS/swap, pool fill, warm-up stability, binlog disk use.
- Best points: 3 runs; sweep points: 2; order interleaved (not blocked by method); references re-run each session.

## Report
A separate v2 report: main chart (data size -> best tps per method, best settings on hover), drill-down (for a size and
method: tps vs pool/cache size, one line per second knob), uniform / zipfian switch.
