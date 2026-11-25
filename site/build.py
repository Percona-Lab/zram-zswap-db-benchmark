#!/usr/bin/env python3
"""Build docs/index.html (GitHub Pages) from data/*.jsonl. usage: python3 site/build.py"""
import json, os, html
R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def rows(f): return [json.loads(l) for l in open(os.path.join(R, "data", f)) if l.strip()]

# round 3: first runs per mode/size (repeats listed separately)
r3 = rows("sysbench-z40-results.jsonl")
MODES = [("zram-lz4", "zram lz4"), ("zswap-lz4", "zswap lz4"), ("zram-zstd", "zram zstd"), ("zswap-zstd", "zswap zstd"),
         ("disk2", "swap file on 2nd SSD, no compression"), ("disk", "normal swap (baseline)")]
series = []
for key, label in MODES:
    pts = sorted([(r["bp_gb"], r["tps"], r.get("p95_ms")) for r in r3 if r["mode"] == key and r["id"].endswith("-r1") and r.get("tps")])
    series.append({"key": key, "label": label, "pts": pts})
reps = {}
for r in r3:
    if r["id"].endswith("-r1") or not r.get("tps"): continue
    reps[r["id"]] = r
base = next(r["tps"] for r in r3 if r["id"] == "disk-26-r1")
best = max((r for r in r3 if r.get("tps")), key=lambda r: r["tps"])

surv = [json.loads(l) for l in open(os.path.join(R, "data", "page-compressibility.jsonl")) if l.strip()]
NAMES = {"bb-smallbank": "BenchBase SmallBank*", "bb-auctionmark": "BenchBase AuctionMark", "go-tpc-tpch": "TPC-H (go-tpc)",
         "bb-seats": "BenchBase SEATS", "bb-otmetrics": "BenchBase OT-Metrics", "bb-twitter": "BenchBase Twitter",
         "employees": "employees sample DB", "imdb-job": "IMDb (real data)", "bb-tatp": "BenchBase TATP",
         "bb-tpcc": "BenchBase TPC-C", "go-tpc-tpcc": "TPC-C (go-tpc)", "hammerdb-tpcc": "HammerDB TPROC-C",
         "bb-wikipedia": "BenchBase Wikipedia", "bb-ycsb": "BenchBase YCSB", "sysbench": "sysbench (stock)"}
sv = [{"name": NAMES[s["bench"]], "lz4": s["lz4_4k"], "zstd": s["zstd1_4k"]} for s in surv if s["bench"] in NAMES and s["data_mb"] >= 100]
sv.append({"name": "sysbench, --data-random-pct=10", "lz4": 2.96, "zstd": 5.90, "ours": True})
sv.sort(key=lambda x: -x["lz4"])

lat = [{"name": "lz4 decompress (16 KiB page)", "us": 4.2, "kind": "mem"},
       {"name": "zstd decompress (16 KiB page)", "us": 24, "kind": "mem"},
       {"name": "Local NVMe SSD read, idle", "us": 75, "kind": "disk"},
       {"name": "SATA SSD read, idle", "us": 150, "kind": "disk"},
       {"name": "Cloud network block storage read", "us": 750, "kind": "disk"},
       {"name": "This server's SATA SSD under load", "us": 3300, "kind": "disk"}]

SIZES = {"r11m": 35.7, "r15m": 47.9, "r19m": 60.2, "r23m": 73.5}
R5 = [("zram", "zram alone"), ("zramdisk", "zram + swap (~10 GB zram budget)"), ("zswap-lz4", "zswap + swap (~10 GB pool)"),
      ("disk2", "swap file alone"), ("disk", "normal swap, 26 GB pool")]
r5 = []
for key, label in R5:
    pts = []
    for r in r3:
        t = r["id"].rsplit("-", 1)[-1]
        if r["mode"] == key and t in SIZES and r.get("tps"):
            pts.append([SIZES[t], r["tps"], r.get("p95_ms"), r.get("warm_tail_vs_window_pct"), r.get("sda_r_s")])
    r5.append({"label": label, "pts": sorted(pts)})
r5rows = "".join(f"<tr><td>{html.escape(lbl)}</td>" + "".join(
    f"<td>{next((f'{p[1]:,.0f}' + (' †' if p[3] is not None and abs(p[3]) > 10 else '') for p in sr['pts'] if p[0] == g), '')}</td>" for g in SIZES.values()) + "</tr>"
    for sr, (_, lbl) in zip(r5, R5))
DATA = json.dumps({"series": series, "survey": sv, "lat": lat, "r5": r5})
pct = lambda v: f"{(v / base - 1) * 100:+.0f}%"
def rep_line(i1, i2):
    a = next((r for r in r3 if r["id"] == i1), None); b = reps.get(i2)
    if not a: return ""
    return f"<tr><td>{html.escape(i1[:-3])}</td><td>{a['tps']:,.0f}</td><td>{(b['tps'] if b else float('nan')):,.0f}</td><td>{(abs(b['tps']/a['tps']-1)*100 if b else float('nan')):.1f}%</td></tr>" if b else f"<tr><td>{html.escape(i1[:-3])}</td><td>{a['tps']:,.0f}</td><td>pending</td><td></td></tr>"
reprows = "".join(rep_line(a, b) for a, b in [("disk-26-r1", "disk-26-r2"), ("zram-lz4-40-r1", "zram-lz4-40-r2"),
                                              ("zram-zstd-40-r1", "zram-zstd-40-r2"), ("zswap-lz4-40-r1", "zswap-lz4-40-r2")])
extra = [r for r in r3 if (r["id"].endswith(("-p80", "-p12")) or r["mode"].startswith(("zramdisk", "zramwb"))) and r["id"].endswith(("-r1", "-p80", "-p12"))]
extrarows = "".join(f"<tr><td>{html.escape(r['id'])}</td><td>{r['tps']:,.0f}</td><td>{pct(r['tps'])}</td><td>{r.get('p95_ms','')}</td></tr>" for r in extra if r.get("tps"))
r3table = "".join(f"<tr><td>{html.escape(lbl)}</td>" + "".join(f"<td>{next((f'{t:,.0f}' for b,t,_ in s['pts'] if b==g), '')}</td>" for g in (16,20,24,26,28,32,36,40,44,48)) + "</tr>"
                  for s, (_, lbl) in zip(series, MODES))
svtable = "".join(f"<tr><td>{html.escape(s['name'])}</td><td>{s['lz4']:.2f}</td><td>{s['zstd']:.2f}</td></tr>" for s in sv)

R4 = {"inno-kbs8": "InnoDB ROW_FORMAT=COMPRESSED, KEY_BLOCK_SIZE=8 (zlib)", "inno-kbs4": "InnoDB ROW_FORMAT=COMPRESSED, KEY_BLOCK_SIZE=4 (zlib)",
      "inno-pclz4": "InnoDB page compression, COMPRESSION='lz4'", "zfs-lz4": "ZFS lz4, compressed ARC", "zfs-zstd": "ZFS zstd, compressed ARC",
      "rocks-lz4": "MyRocks lz4", "rocks-zstd": "MyRocks zstd"}
def r4row(r):
    mem = f"{r['bp_gb']:.0f} GB pool" if not r["mode"].startswith("rocks") else f"{r['bp_gb']:.0f} GB block cache"
    if r["mode"].startswith("zfs"): mem += " + " + r["id"].split("-")[-1].replace("arc", "") + " GB ARC"
    wt = r.get("warm_tail_vs_window_pct")
    flag = " †" if wt is not None and abs(wt) > 10 else ""
    if r["mode"].startswith("inno-") and r["id"].endswith("-r1"): flag += " ‡"
    name = R4[r["mode"]] + (" — zlib level 1 (round 6)" if r["id"].endswith("-L1") else " — repeat (round 6)" if r["id"].endswith("-r2") else "")
    return (f"<tr><td>{html.escape(name)}</td><td>{mem}</td><td>{r['tps']:,.0f}{flag}</td><td>{pct(r['tps'])}</td>"
            f"<td>{r.get('p95_ms', 0):.0f}</td><td>{r.get('cpu_wa', '')}</td></tr>")
r4rows = "".join(r4row(r) for r in r3 if r["mode"] in R4 and r.get("tps")) or "<tr><td colspan=6>running</td></tr>"
page = open(os.path.join(R, "site", "template.html")).read()
page = page.replace("@R4ROWS@", r4rows)
for k, v in {"@DATA@": DATA, "@BASE@": f"{base:,.0f}", "@BEST@": f"{best['tps']:,.0f}", "@BESTPCT@": pct(best["tps"]),
             "@BESTP95@": f"{best['p95_ms']:.0f}", "@BASEP95@": f"{next(r['p95_ms'] for r in r3 if r['id']=='disk-26-r1'):.0f}",
             "@REPROWS@": reprows, "@EXTRAROWS@": extrarows or "<tr><td colspan=4>running</td></tr>",
             "@R3TABLE@": r3table, "@R5ROWS@": r5rows, "@SVTABLE@": svtable}.items():
    page = page.replace(k, v)
open(os.path.join(R, "docs", "index.html"), "w").write(page)
print("wrote docs/index.html")
