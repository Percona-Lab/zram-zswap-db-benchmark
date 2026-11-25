# zram and zswap for MySQL / InnoDB: can compressed memory let the buffer pool grow past RAM?

Can Linux zram or zswap (lz4 or zstd) give an I/O-bound InnoDB workload more throughput by letting
`innodb_buffer_pool_size` go beyond what fits in RAM? Three rounds of benchmarks on one bare-metal server, a survey of
how well 16 benchmarks' data compresses, and lz4 / zstd page latency next to storage latency.

Results: v1 (exploratory) https://percona-lab.github.io/zram-zswap-db-benchmark/v1/ ; v2 (consistent protocol) in preparation

**This study was run and written up by an AI agent (Claude) under the direction of a person. The numbers are
measured, but the write-up and conclusions are AI-generated and were spot-checked only; they may contain mistakes.**

## Summary

* **When the data fits in RAM only once compressed, zram with lz4 is a large win**: a 40 GB uniform sysbench
  read/write data set on a 31 GB server ran at **2,286 tps with a 40 GB buffer pool on zram lz4 vs 882 tps for the best
  pool without compression (26 GB): +159%, p95 latency 50 ms vs 170 ms** (round 3; repeat runs within 0.6%).
* **zram beat zswap** (zswap +93%): zswap's compressed pool is capped (max_pool_percent 50) and spills to the swap
  device. **lz4 beat zstd** (zram zstd +100%): once lz4 fits the data, zstd's better ratio buys nothing and its CPU cost
  limits throughput.
* **It depends entirely on how compressible the pages are.** Common OLTP benchmarks' InnoDB pages compress only
  1.1-1.4x with lz4 (sysbench, YCSB, HammerDB, TPC-C). With HammerDB (round 1) zram could only grow the pool a little
  (+10%), and most of zswap's +21% came from its swap file being on a second SSD. Even real data sets compress only
  ~1.7x with lz4 (~2.7x zstd).
* **The hot set must exceed RAM** for any of this to matter: with BenchBase AuctionMark (round 2) the working set fit in
  RAM and every setup tied.
* **Compressed swap makes an oversized pool safe; disk swap does not**: past the best size, disk swap cost 13-22%
  (rounds 2 and 3), zram/zswap nothing.
* **lz4 decompresses a 4 KiB page in ~1 us** (~4 us for a 16 KiB InnoDB page) vs ~50-200 us for an idle SSD read and
  ~3.3 ms for this server's SATA SSD under the benchmark load.

For round 3, stock sysbench's data was made compressible with a small patch to its Lua scripts:
[Percona-Lab/sysbench-compressible](https://github.com/Percona-Lab/sysbench-compressible) (`--data-random-pct`).

## Setup

* Server: bare metal, Intel Xeon E3-1270 v6 (4 cores / 8 threads, 3.8 GHz), 31 GB RAM, 2 x 240 GB Intel SATA SSD
  (datadir on one, the "second SSD" used by the disk2 and zswap modes' swap file on the other).
* Ubuntu 24.04, kernel 6.8.0-151, Percona Server 8.4.11, binary log disabled.
* The same server settings for every point (`innodb_redo_log_capacity=8G`, `innodb_io_capacity=2000/4000`,
  8 buffer pool instances, `O_DIRECT`); only the buffer pool size and the swap setup change. Pristine data restored
  before every point; benchmark clients run on the same server.
* Runner: `scripts/zstudy.sh` (`setup`, `run mode:bp ...`, `teardown`) with `scripts/zstudy_point.py`; per-point
  results in `data/*.jsonl`. The script assumes a dedicated lab host (it moves the datadir aside and changes swap and
  sysctls); read it before running anywhere else.

Modes (all rounds):

| Mode | Setup |
|---|---|
| disk | the host's own 8 GB swap file (datadir drive), zswap off, kernel defaults, swappiness 60 |
| disk2 | control for zswap: 64 GB swap file on the second SSD, zswap off, the compressed modes' sysctls |
| zswap-lz4 / zswap-zstd | zswap (zsmalloc, max_pool_percent 50) in front of the same 64 GB swap file |
| zram-lz4 / zram-zstd | zram only (32 GB device in round 1, 64 GB after), priority 100, no disk swap, zswap off |

Compressed modes and disk2 use the zram tuning from the Arch Linux wiki: `vm.page-cluster=0`,
`vm.watermark_boost_factor=0`, `vm.watermark_scale_factor=125`; swappiness 180 (zram) / 100 (zswap, disk2).

## Round 1: HammerDB TPROC-C 1000 W (~100 GB)

Workload: HammerDB 6.0 TPROC-C, 1000 warehouses (84 GB of tables, ~93 GB on disk, ~3x RAM), all-warehouse mode,
64 virtual users co-located, binlog off, 5 min ramp-up + 15 min measured, pristine data restored before every point.
Server settings the same for every point (redo 8 GB, io_capacity 2000/4000, 8 buffer pool instances, O_DIRECT); only
the buffer pool size and the swap setup change.

Modes:

| Mode | Setup |
|---|---|
| disk | the host's own 8 GB swap file (datadir drive), zswap off, kernel defaults, swappiness 60 |
| disk2 | control for zswap: 64 GB swap file on the second SSD, zswap off, the compressed modes' sysctls |
| zswap | zswap (lz4, zsmalloc, max_pool_percent 50) in front of the same 64 GB swap file on the second SSD |
| zram | zram only (lz4, 32 GB device, priority 100), no disk swap, zswap off |

Compressed modes and disk2 use the Arch-wiki zram sysctls: `vm.page-cluster=0`, `vm.watermark_boost_factor=0`,
`vm.watermark_scale_factor=125`; swappiness 180 (zram) / 100 (zswap, disk2).

| Mode | BP GB | NOPM | vs disk 26 GB | NEW_ORDER p95 ms | CPU % per 1k NOPM | notes |
|---|---|---|---|---|---|---|
| disk | 20 | 15,420 | -42% | 262 | 1.23 | |
| disk | 24 | 24,863 | -7% | 183 | 1.31 | |
| disk | 26 | 26,695 / 26,428 | (base) | 160 / 165 | 1.30 | repeat within 1% |
| disk | 28 | 27,696 / 27,314 | +3% | 184 / 175 | 1.35 | 1.5 GB of mysqld swapped |
| disk2 | 32 | 29,149 | +10% | 155 | 1.61 | 8k swap-ins/s from the 2nd SSD |
| disk2 | 36 | 30,326 | +14% | 154 | 1.80 | |
| disk2 | 40 | 30,367 | +14% | 145 | 2.06 | 20k swap-ins/s |
| zswap | 28 | 27,298 | +2% | 156 | 1.82 | |
| zswap | 32 | 28,876 | +8% | 161 | 2.00 | |
| zswap | 36 | 30,828 | +16% | 144 | 2.01 | |
| zswap | 40 | **32,327 / 32,363** | **+21%** | 139 / 133 | 2.04 | repeat within 0.1% |
| zram | 28 | 27,977 | +5% | 158 | 1.33 | |
| zram | 32 | 29,391 / 28,927 | +10% | 155 / 152 | 1.94 | 240 MB/s in and out of zram |
| zram | 36, 40 | OOM-killed | | | | compressed pages no longer fit |

Findings:

1. **lz4 compresses HammerDB's InnoDB pages only 1.22-1.27x** (measured live in zram and zswap). zswap's pool filled
   at ~15 GB holding ~18.5 GB, the rest went to the swap file.
2. **Most of zswap's gain is the second SSD, not compression.** The same swap file without zswap (disk2) reaches +14%;
   zswap adds ~6% more (beyond the ~1% run-to-run noise measured by the repeats). In effect the buffer pool overflowed
   into a second drive that served 13-20k random reads/s at ~0.5 ms, while reads from the data drive fell.
3. **zram cannot grow far with data this incompressible**: +10% at 32 GB, OOM at 36 GB.
4. **The price is CPU**: ~2.0% CPU per 1k NOPM with swapping vs ~1.3% without (page faults, compression, reclaim).

Because the compression ratio, not the mechanism, limited this round, the next question was which benchmark has data
that compresses like real data might.

## Benchmark data compressibility survey

Method (`scripts/survey.sh`, `scripts/bb_small.sh`, `scripts/age.sh`, `tools/pagecomp.py`): on a separate cloud VM (8 dedicated vCPUs,
Percona Server 8.4.11), load a small data set of each benchmark (0.4-10 GB), wait until the buffer pool has no dirty
pages, then read every `.ibd` file in 4 KiB pieces - the unit the kernel compresses in zram/zswap - and compress each
piece on its own. All-zero pieces (unused extents) are left out: zram/zswap store them for free and a working buffer
pool holds real pages. A piece that does not shrink counts at 4096 bytes. Also: 16 KiB (InnoDB page) lz4, and
`mysqldump | lz4 -1` for comparison. "Aged" = after 10 minutes of the workload (32 terminals) on the loaded data.
Raw rows: `data/page-compressibility.jsonl`.

| Benchmark | Data MB | lz4 4 KiB | zstd-1 4 KiB | lz4 16 KiB | mysqldump + lz4 |
|---|---|---|---|---|---|
| BenchBase SmallBank | 2,344 | **3.49** (aged 3.30) | 6.32 (aged 5.71) | 3.77 | 4.00 |
| BenchBase AuctionMark | 1,658 | **2.79** (aged 2.92) | 4.34 (aged 4.50) | 2.88 | 2.19 |
| go-tpc TPC-H SF1 | 2,083 | 2.38 | 3.26 | 2.67 | 2.24 |
| BenchBase SEATS | 1,699 | 2.27 (aged 2.28) | 3.13 | 2.38 | 1.30 |
| BenchBase OT-Metrics | 9,726 | 2.02 | 3.15 | 2.12 | 2.98 |
| BenchBase Twitter | 433 | 1.96 (aged 1.96) | 2.40 | 2.01 | 1.21 |
| employees sample DB (datacharmer/test_db) | 144 | 1.76 | 2.41 | 1.78 | 2.36 |
| IMDb / Join Order Benchmark (real data) | 7,853 | 1.73 | 2.68 | 1.79 | 2.24 |
| BenchBase TATP | 772 | 1.59 | 2.35 | 1.62 | 1.58 |
| BenchBase TPC-C | 2,011 | 1.42 (aged 1.45) | 2.11 | 1.44 | 1.28 |
| BenchBase Epinions | 26 | 1.38 | 1.80 | 1.41 | 1.49 |
| go-tpc TPC-C | 1,749 | 1.36 | 1.84 | 1.38 | 1.25 |
| HammerDB TPROC-C | 1,708 | 1.33 | 1.72 | 1.35 | 1.24 |
| BenchBase Wikipedia | 2,327 | 1.22 | 1.45 | 1.25 | 1.52 |
| BenchBase YCSB | 1,190 | 1.14 | 1.36 | 1.17 | 1.09 |
| sysbench oltp (8 x 1M) | 1,819 | 1.14 | 2.12 | 1.16 | 1.06 |

What it shows:

* The small-scale estimate matches production scale: HammerDB 1.33x here vs 1.22-1.27x seen live in zram/zswap at
  1000 W.
* **mysqldump ratios do not predict page ratios** (OT-Metrics 2.98 vs 2.02; SEATS 1.30 vs 2.27): page layout, free
  space and record headers matter. Measure pages, not dumps.
* **Only SmallBank reaches 3x with lz4, and by an artifact**: account names are 64-character zero-padded numbers
  (`000...0001`); that table alone is 4.55x, while the tables its transactions update (checking, savings) are ~2x.
* **Even real data is ~1.7x with lz4** (IMDb, employees); zstd gives 2.4-2.7x on them.
* **zstd crosses 3x where lz4 does not**: AuctionMark 4.5x, SEATS 3.1x, OT-Metrics 3.2x, TPC-H 3.3x.
* Aging (10 minutes of workload) changed little: page splits and updates moved ratios by -0.2 to +0.13.

Choice for round 2: **BenchBase AuctionMark** (eBay-style auctions: users, items, bids, comments, purchases), the
most compressible OLTP workload without padding tricks (lz4 ~2.9x, zstd ~4.5x), tested with both lz4 and zstd for
zswap and zram.

## Round 2: BenchBase AuctionMark (~86 GB), lz4 and zstd - not I/O-bound enough to answer the question

Scale factor 140 (86 GB on disk; scale factor 300 was aborted at 130 GB, projected ~200 GB: AuctionMark's size is not
linear in the scale factor - plan ~0.6-0.7 GB per SF), 64 terminals co-located, 5 min warm-up + 15 min measured,
pristine data before each point. Raw rows: `data/auctionmark-sf140-results.jsonl`.

| Mode | BP GB | req/s | p95 ms | p99 ms | InnoDB reads/s | swap-ins/s |
|---|---|---|---|---|---|---|
| disk | 20 | 6,184 | 31.8 | 103 | 945 | 0 |
| disk | 24 | 7,357 | 28.3 | 60 | 683 | 0 |
| disk | 26 | 7,507 | 27.6 | 57 | 557 | 8 |
| disk | 28 | 6,503 | 34.8 | 79 | 698 | 3,963 (disk) |
| disk2 | 32 / 40 | 5,847 / 6,079 | 37 | 78 | ~1,000 | 5,400-6,000 (2nd SSD) |
| zswap lz4 | 32 / 40 / 48 | 7,509 / 7,513 / 7,472 | 27.4-27.7 | 56 | ~480 | ~3,500 (zswap) |
| zswap zstd | 32 / 40 / 48 / 56 | 7,487 / 7,502 / 7,503 / 7,468 | 27.5 | 56 | ~485 | ~3,500 (zswap) |
| zram lz4 | 32 / 40 / 48 | 7,544 / 7,534 / 7,553 | 27.2 | 55-57 | ~475 | ~3,500 (zram) |
| zram zstd | 32 / 40 / 48 / 56 | 7,506 / 7,491 / 7,536 / 7,530 | 27.4 | 56 | ~480 | ~3,500 (zram) |

Findings:

1. **This workload is CPU-bound with a hot set that fits in RAM**: at the best plain size (26 GB) the miss rate is
   0.16%, iowait 7.6%, CPU 88% busy (BenchBase's Java client shares the 8 threads). Every compressed setup lands at the
   same ~7,500 req/s as disk 26 GB, whatever the pool size (all within ~1%).
2. **The larger pools never filled with data.** A whole run reads ~1.4M pages (~23 GB); a 48 GB pool's extra memory is
   never-used pages. InnoDB zero-fills the pool at startup, the kernel swaps those zero pages out and zram/zswap store
   them almost for free (reported ratios 6-31x are zero pages, not AuctionMark data), and the swap-ins (~3,500/s) are
   InnoDB claiming a free page. Compression of real AuctionMark pages was never exercised.
3. **Compressed swap does make oversizing safe; disk swap does not**: at 28-40 GB, disk swap costs 13-22% (zero pages
   written to and read back from SSD), while zram/zswap cost nothing.

To test the question, the hot set has to exceed RAM but fit once compressed: round 3.

## Round 3: sysbench with compressible data, 40 GB uniform - fits in RAM only when compressed

Workload: sysbench 1.0.20 `oltp_read_write` with the patched `oltp_common.lua`
([sysbench-compressible](https://github.com/Percona-Lab/sysbench-compressible)), `--data-random-pct=10` (pages ~3x
lz4, ~6x zstd), 16 tables x 11M rows (35.9 GB of tables, 49 GB on disk), `--rand-type=uniform`, 64 threads, 15 min
warm-up + 10 min measured. The design (from the person directing the study): a data set that does not fit in the
buffer pool uncompressed but fits in RAM compressed. Every point records how full the pool was at the start and end of
the window (`bp_free_pct_*`): all pools up to 40 GB were full of data (free < 1%); 44 / 48 GB pools held the whole data
set with pages to spare. Raw rows: `data/sysbench-z40-results.jsonl`.

| Mode | BP GB | tps | vs disk 26 GB | p95 ms | InnoDB reads/s | iowait % | sys CPU % | live ratio |
|---|---|---|---|---|---|---|---|---|
| disk | 16 / 20 / 24 | 511 / 653 / 794 | -42% / -26% / -10% | 253 / 215 / 187 | ~7,700 | 76 / 70 / 65 | 8-9 | |
| disk | 26 | 882 | (base) | 170 | 7,514 | 61 | 10 | |
| disk | 28 | 799 | -9% | 183 | 6,761 | 64 | 10 | |
| disk2 | 32 / 40 | 933 / 1,233 | +6% / +40% | 140 / 92 | 5,542 / 1,350 | 46 / 31 | 17 / 24 | |
| zswap-lz4 | 32 / 36 / 40 | 1,167 / 1,304 / 1,698 | +32% / +48% / +93% | 132 / 121 / 83 | 5,553 / 4,199 / 1,559 | 33 / 25 / 10 | 27 / 32 / 42 | 2.9-3.0 |
| zswap-lz4 | 44 / 48 | 1,682 / 1,632 | +91% / +85% | 83 / 88 | 0 | 9 / 11 | 44 / 43 | |
| zswap-zstd | 32 / 36 / 40 | 1,139 / 1,169 / 1,440 | +29% / +33% / +63% | 137 / 130 / 197 | 5,424 / 4,044 / 36 | 25 / 23 / 12 | 36 / 39 / 46 | 5.4-5.7 |
| zswap-zstd | 44 / 48 | 1,229 / 1,260 | +39% / +43% | 282 / 267 | 0 | 17 | 46 / 45 | |
| zram-lz4 | 32 / 36 | 1,228 / 1,693 | +39% / +92% | 137 / 91 | 5,844 / 3,857 | 40 / 18 | 20 / 31 | 3.4-3.6 |
| **zram-lz4** | **40** | **2,286** | **+159%** | **50** | **2** | **3** | **39** | **3.3** |
| zram-lz4 | 44 / 48 | 2,273 / 2,284 | +158% / +159% | 51 | 0 | 3 | 39 | |
| zram-zstd | 32 / 36 / 40 | 1,195 / 1,203 / 1,761 | +36% / +36% / +100% | 130 / 140 / 72 | 5,690 / 4,068 / 29 | 32 / 23 / 2 | 29 / 39 / 53 | 6.6-7.5 |
| zram-zstd | 44 / 48 | 1,689 / 1,689 | +92% | 74 | 0 | 2 | 55 | |

Findings:

1. **With data that fits compressed, zram lz4 is +159% throughput and 70% lower p95 latency**: at a 40 GB pool all
   data is in memory (InnoDB reads ~0/s), part of it compressed at ~3.3x; 89k pages/s move between the pool and zram.
   The server becomes CPU-bound instead of I/O-bound, and CPU per transaction is no worse than with disk I/O
   (41.7% CPU per 1k tps for zram-lz4 40 GB and for disk 26 GB).
2. **zram beats zswap here**: zswap's pool is capped at 50% of RAM (`max_pool_percent`); beyond it pages are written
   back to the swap file (1-12k/s), and with zstd the swap device is hit hard (13-16k reads/s at 40-48 GB). Raising
   `max_pool_percent` to 80 did not close the gap (40 GB: 1,711 vs 1,698 tps; 44 GB: 1,833): zswap keeps writing cold
   pages back (the zswap shrinker, on by default in 6.8) and reads them from disk on a miss.
3. **lz4 beats zstd once lz4 is enough**: zstd compresses ~2x better but costs ~6x more CPU to decompress; with sys CPU
   at 53-55% it tops out at +100% (zram) and +63% (zswap).
4. **Pools beyond the data set size add nothing** (44 / 48 GB = 40 GB), and **disk swap beyond the best plain size
   hurts** (28 GB: -9%).
5. The second SSD alone (disk2) gives +40% at 40 GB: a fast swap device is a cache tier too; compression adds the rest.

Repeats (second run of the same point): disk 26 GB 882 / 877 tps, zram-lz4 40 GB 2,286 / 2,283, zswap-lz4 40 GB
1,698 / 1,696 - all within 0.6%; zram-zstd 40 GB 1,761 / 1,563 (11%: zstd's CPU-bound runs are less stable).
Follow-ups: zram capped at 12 GB of uncompressed pages (~3.6 GB RAM) with the 2nd-SSD swap file as a lower-priority
fallback (`zramdisk`), the same with zram writeback of pages idle for a minute (`zramwb`), and zswap with
`max_pool_percent` 12 (about the same compressed size):

| Mode | 40 GB tps | 44 GB tps | p95 ms (40) | 2nd-SSD reads/s (40) |
|---|---|---|---|---|
| zram (64 GB device, all data fits) | 2,286 | 2,273 | 50 | 0 |
| zswap-lz4, max_pool_percent 50 / 80 | 1,698 / 1,711 | 1,682 / 1,833 | 83 / 81 | ~1,400 / 1,900 |
| zramdisk-lz4 (zram 12 GB + disk fallback) | 1,566 | 1,804 | 99 | 18,250 |
| zramwb-lz4 (+ writeback every 60 s) | 1,536 | 1,772 | 94 | 20,530 |
| zswap-lz4, max_pool_percent 12 | 1,353 | 1,322 | 76 | 30,730 |

zram with a lower-priority disk swap works as a safety net (no OOM, still +77-105% over normal swap) and beats zswap
with the same compressed size; writeback did not help on this uniform workload (it moved ~1.9 GB to disk and read pages
back about as fast as it wrote them).

## Round 4: other ways to compress (same 40 GB data and workload)

Raw rows: `data/sysbench-z40-results.jsonl` (modes `inno-*`, `zfs-*`, `rocks-*`); orchestration `scripts/round4.sh`.
Kernel swap is the host's normal 8 GB file. Reference: normal swap 26 GB 882 tps; zram lz4 40 GB 2,286 tps.

| Setup | Memory | tps | p95 ms |
|---|---|---|---|
| InnoDB ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=8 (zlib level 6) | 16 / 24 / 26 GB pool | 335 / 712 / 661 | 451 / 215 / 240 |
| InnoDB ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=4 | 16 / 24 / 26 GB pool | 573 / 670 / 650 | 332 / 282 / 282 |
| InnoDB page compression COMPRESSION='lz4' | 26 GB pool | 328 | 435 |
| ZFS lz4 (16K records, 1.93x) | pool + ARC 8+18 / 12+14 / 16+10 / 24+4 GB | 578 / 376 / 280 / 318 | 183 / 357 / 467 / 405 |
| ZFS zstd (3.4-3.7x) | 8+18 / 12+14 / 16+10 GB | 496 / 538 / 382 | 320 / 282 / 326 |
| MyRocks lz4 (12 GB on disk) | 8 / 16 / 24 GB block cache | 1,888 / 1,940 / 1,442 | 49 / 47 / 55 |
| MyRocks zstd (6.7 GB on disk) | 8 / 16 / 24 GB block cache | 1,513 / 1,540 / 1,536 | 60 / 60 / 58 |

Correction (round 6, 2026-11-25): the InnoDB-compression and page-compression runs above were measured while the datadir
SSD was abnormally slow to write (~11 ms per write at ~1,000 writes/s, 84-89% busy; round 6: ~2.2 ms at ~3,700/s,
~50% busy; likely the SSD's internal state after many full rewrites since the last TRIM). Round 6: KEY_BLOCK_SIZE=8 at
24 / 26 GB = 1,386 / 1,454 tps at zlib level 1 and 1,461 tps at level 6 (repeat of 712): table compression is +65% over
uncompressed InnoDB, CPU-bound, and the zlib level made no difference. Page compression saves disk only (its throughput
is not reliable).
ZFS: compressed 16K records are stored in whole 4K sectors, so lz4 gets 1.93x (zram 3.3x) and stays I/O-bound; zstd
fits but is CPU-bound (ARC hits decompress again); ZFS itself was slower than ext4 here (24 GB pool: 318 vs 794 tps).
MyRocks lz4 is the only alternative close to zram (85%, same p95): compressed files stay in the page cache; a block
cache too large crowds them out. Some InnoDB-compression and ZFS runs were not fully settled after the warm-up
(`warm_tail_vs_window_pct` > 10%).

## Round 5: data size sweep (36 -> 74 GB of the same compressible data)

Raw rows: `data/sysbench-z40-results.jsonl`, ids ending `r11m` / `r15m` / `r19m` / `r23m` (16 x 11 / 15 / 19 / 23M rows =
35.7 / 47.9 / 60.2 / 73.5 GB of tables); orchestration `scripts/round5.sh`. Uniform, 64 threads, 20 min warm-up +
10 min measured, fresh load per size. Big pool = 1.1 x data. zram + swap: 32 GB zram device (~10 GB RAM) at priority 100
plus the 2nd-SSD swap file at priority 10; zswap + swap: max_pool_percent 32 (~10 GB); zram alone: 128 GB device.

| Setup | 36 GB | 48 GB | 60 GB | 74 GB |
|---|---|---|---|---|
| zram alone | 2,175 | 1,542 | 1,337 | 594 (host unresponsive 20-30 min afterwards) |
| zram + swap (~10 GB budget) | 2,225 | 1,242 | 600 | 313 (not settled) |
| zswap + swap (~10 GB budget) | 1,492 | 868 | 401 | 326 (not settled) |
| swap file alone | 1,420 | 594 (not settled) | 413 | 351 |
| normal swap, 26 GB pool | 774 | 506 | 430 | 375 |

Findings: zram + swap matches zram alone while the data fits its budget, and beats zswap with the same budget by
43-50% up to 60 GB. Past the budget it degrades instead of failing, but the fixed ~10 GB budget left RAM unused, so zram
alone stayed ahead up to 60 GB. zram alone never OOM-killed: at 74 GB it ran, then left the host unresponsive (SSH timed
out) for 20-30 minutes while MySQL stopped and ~88 GB of swapped pages came back. Size zram's disksize to the RAM you
can give it, with a disk swap behind it.

## Round 6: zlib level 1, repeats, real data (JOB)

* InnoDB KEY_BLOCK_SIZE=8: 24 / 26 GB at zlib level 1 = 1,386 / 1,454 tps; level 6 repeat at 24 GB = 1,461 (round 4: 712,
  slow-SSD period, see the round 4 correction).
* MyRocks lz4 16 GB block cache repeat: 1,963 tps (round 4: 1,940).
* Round 5 repeats at 48 GB: the first attempt ran on a datadir with leftover MyRocks tables and produced no data
  (`r15m-r2` rows); rerun as `r15m-r2b` (`scripts/round6c.sh`).
* JOB (IMDb 2013, 113 queries, 1 stream, 60 s cap), 23 GB reserved as huge pages (~6 GB available), raw rows
  `data/job-imdb-results.jsonl`: normal swap 4 GB pool 996 s total; 9 GB pool with a swap file / zram lz4 / zram zstd /
  zswap: 553 / 545 / 546 / 545 s. The gain comes from the larger pool; only ~3.5-4 GB was swapped and it was pool pages
  the queries never touched (zram "ratios" 86-149x, ~13 swap-ins/s), so compression made no difference here.

## Round 7: zswap shrinker off; zram sized to the box

36 GB of tables (40 GB pool): zram lz4 2,246 tps; zswap lz4 shrinker on (cap 50%) 1,666 with ~1,000 writebacks/s;
shrinker off 1,695 (44 GB pool: 1,840) with no writeback and no disk reads. 48 GB of tables (53 GB pool): zram + swap
with a 64 GB zram device (~13 GB RAM) 1,552 tps = zram alone (1,542 / 1,539); zswap + swap shrinker off cap 50% / 80%
1,237 / 1,286 (shrinker on, cap 32%: 868 / 912). zswap's remaining gap to zram is its swap path, not writeback (likely
the swap cache, which zram as a synchronous device skips). Orchestration `scripts/round7.sh`.

## Page compression and decompression time vs storage latency

`tools/pagecodec_bench.c`: 50,000 real 4 KiB pieces of the round-3 data (`--data-random-pct=10`), one core of the
same Xeon E3-1270 v6, user-space lz4 / libzstd (the kernel's implementations derive from the same code).

| Codec | Compress 4 KiB | Decompress 4 KiB | Decompress one 16 KiB InnoDB page (4 kernel pages) | Ratio |
|---|---|---|---|---|
| lz4 | 4.4 us | 1.0 us | ~4 us | 3.2 |
| zstd -1 | 13.6 us | 5.9 us | ~24 us | 6.5 |
| zstd -3 (kernel default level for zram / zswap) | 14.3 us | 6.0 us | ~24 us | 6.5 |

Reading a 16 KiB page from storage, for comparison: local NVMe ~50-100 us idle, SATA SSD ~100-200 us idle, this
server's SATA SSD under the round-3 load ~3,300 us (measured `r_await`, queueing at ~7.5k reads/s), cloud network
block storage ~500-1,000 us. The whole kernel path costs more than the codec: from the zram-lz4 40 GB run, roughly
25 us of kernel CPU per page swapped out and back in (reclaim, LRU scanning, faults, compression).

## Reproduce

* Round 3: `WL=sbz SBZ_ROWS=11000000 scripts/zstudy.sh setup`, then e.g.
  `WL=sbz SBZ_ROWS=11000000 scripts/zstudy.sh run disk:26 zram-lz4:40 zswap-lz4:40`; the sysbench-compressible scripts
  are expected in `/root/bench/sysbench-z/`.
* Round 1 (HammerDB, `scripts/hdb_*.tcl`): `WL=hdb`; round 2 (BenchBase AuctionMark, `scripts/bb_*`): `WL=am AM_SF=140`.
* Survey: `scripts/survey.sh <bench...>` and `scripts/age.sh`, measuring with `tools/pagecomp.py`.
* `scripts/zstudy.sh teardown` restores the host's swap, sysctls and datadir.

## License

Scripts and tools: Apache License 2.0 (see `LICENSE`). Results and text: CC BY 4.0.
