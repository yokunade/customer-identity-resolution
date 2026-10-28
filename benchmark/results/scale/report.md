# Scale benchmark: 5,000,000 records, 2,500,000 seeded first

2,194,837 generated people; 3.5 people per postcode; large buildings on; 12 workers.

| | Seed | Measure |
|---|---:|---:|
| Records | 2,500,000 | 2,500,000 |
| Commits | asynchronous | durable |
| Throughput (rec/s) | 2042.6 | 1205.1 |
| Latency p50 / p95 / p99 (ms) | 5.78 / 8.6 / 15.5 | 8.29 / 25.29 / 48.11 |
| Finding candidates p50 / p95 / p99 (ms) | 1.93 / 3.74 / 10.81 | 2.52 / 6.24 / 26.94 |
| Candidate pool p50 / p95 / max | 3 / 15 / 504 | 10 / 36 / 940 |
| Wrong links at arrival | 0.206% | 0.121% |
| Missed links at arrival | 8.156% | 6.017% |
| Records with a block skipped | 0 | 0 |
| Errors | 0 | 0 |

## Latency as the database grows

| Arrivals | Phase | rec/s | p50 ms | p95 ms | p99 ms | finding p50 / p95 ms | pool p95 |
|---|---|---:|---:|---:|---:|---:|---:|
| 0-250,000 | seed | 2475.1 | 4.83 | 6.86 | 8.11 | 0.89 / 2.11 | 2 |
| 250,000-500,000 | seed | 2231.3 | 5.3 | 7.54 | 9.36 | 1.58 / 2.58 | 5 |
| 500,000-750,000 | seed | 2076.4 | 5.51 | 7.88 | 10.63 | 1.74 / 2.87 | 7 |
| 750,000-1,000,000 | seed | 1940.0 | 5.7 | 8.2 | 12.3 | 1.86 / 3.14 | 10 |
| 1,000,000-1,250,000 | seed | 1825.4 | 5.83 | 8.5 | 14.18 | 1.96 / 3.45 | 12 |
| 1,250,000-1,500,000 | seed | 1709.0 | 6.0 | 8.99 | 15.97 | 2.07 / 3.82 | 14 |
| 1,500,000-1,750,000 | seed | 1615.9 | 6.12 | 9.25 | 17.8 | 2.14 / 4.05 | 16 |
| 1,750,000-2,000,000 | seed | 1563.0 | 6.14 | 9.38 | 19.6 | 2.25 / 4.36 | 18 |
| 2,000,000-2,250,000 | seed | 1520.0 | 6.19 | 9.38 | 21.15 | 2.32 / 4.53 | 21 |
| 2,250,000-2,500,000 | seed | 1512.0 | 6.18 | 9.51 | 22.28 | 2.35 / 4.71 | 23 |
| 2,500,000-2,750,000 | measure | 1325.2 | 7.9 | 22.64 | 43.8 | 2.19 / 4.67 | 25 |
| 2,750,000-3,000,000 | measure | 1296.1 | 7.94 | 24.71 | 37.17 | 2.25 / 4.83 | 27 |
| 3,000,000-3,250,000 | measure | 1213.4 | 8.05 | 26.83 | 50.5 | 2.33 / 5.15 | 29 |
| 3,250,000-3,500,000 | measure | 1262.7 | 8.21 | 22.01 | 38.62 | 2.43 / 5.61 | 32 |
| 3,500,000-3,750,000 | measure | 1138.8 | 8.23 | 26.71 | 51.76 | 2.51 / 5.93 | 34 |
| 3,750,000-4,000,000 | measure | 1136.4 | 8.32 | 25.17 | 44.51 | 2.6 / 6.2 | 36 |
| 4,000,000-4,250,000 | measure | 1075.8 | 8.56 | 27.44 | 51.59 | 2.68 / 6.53 | 38 |
| 4,250,000-4,500,000 | measure | 1081.7 | 8.51 | 23.66 | 49.66 | 2.77 / 6.84 | 40 |
| 4,500,000-4,750,000 | measure | 1056.7 | 8.59 | 22.47 | 43.93 | 2.85 / 7.11 | 42 |
| 4,750,000-5,000,000 | measure | 999.4 | 8.88 | 28.62 | 57.76 | 2.93 / 7.46 | 45 |

## Cache hit ratio during measure

- database: 98.29%
- blocking_key_pkey: 100.0%
- ix_blocking_key_lookup: 99.62%
- source_record_pkey: 100.0%

## Database after seed: 8416 MB

| Table | Data | Indexes | Total |
|---|---:|---:|---:|
| source_record | 4929 MB | 254 MB | 5183 MB |
| blocking_key | 827 MB | 1225 MB | 2051 MB |
| resolution_event | 808 MB | 186 MB | 995 MB |
| customer | 112 MB | 66 MB | 178 MB |
| merge_candidate | 344 kB | 136 kB | 480 kB |
| schema_migrations | 48 kB | 16 kB | 64 kB |
| review_example | 8192 bytes | 24 kB | 32 kB |
| review_session | 8192 bytes | 16 kB | 24 kB |

Rows: customer 1,659,292, source_record 2,500,000, blocking_key 12,025,204, merge_candidate 428

Largest blocks (records sharing one blocking key):

- B_DOB_LAST_NYSIIS: 23, 23, 22, 21, 21
- B_DOB_LAST_SOUNDEX: 24, 23, 22, 21, 21
- B_EMAIL: 12, 11, 11, 11, 11
- B_PHONE: 9, 9, 9, 8, 8
- B_POSTCODE: 396, 365, 346, 324, 322
- B_PREMISE_STREET: 37, 30, 30, 29, 26
- B_UPRN: 8, 6, 6, 6, 6

## Database after measure: 16 GB

| Table | Data | Indexes | Total |
|---|---:|---:|---:|
| source_record | 9861 MB | 485 MB | 10 GB |
| blocking_key | 1653 MB | 2242 MB | 3896 MB |
| resolution_event | 1963 MB | 350 MB | 2314 MB |
| customer | 162 MB | 92 MB | 254 MB |
| merge_candidate | 536 kB | 424 kB | 960 kB |
| schema_migrations | 48 kB | 16 kB | 64 kB |
| review_example | 8192 bytes | 24 kB | 32 kB |
| review_session | 8192 bytes | 16 kB | 24 kB |

Rows: customer 2,380,134, source_record 5,000,000, blocking_key 24,052,392, merge_candidate 2,879

Largest blocks (records sharing one blocking key):

- B_DOB_LAST_NYSIIS: 40, 39, 37, 37, 36
- B_DOB_LAST_SOUNDEX: 40, 39, 38, 37, 37
- B_EMAIL: 20, 20, 20, 19, 19
- B_PHONE: 14, 14, 14, 12, 12
- B_POSTCODE: 747, 720, 711, 640, 624
- B_PREMISE_STREET: 71, 55, 54, 52, 49
- B_UPRN: 10, 9, 8, 8, 8
