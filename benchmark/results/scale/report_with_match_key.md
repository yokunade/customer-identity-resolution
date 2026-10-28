# Scale benchmark: 5,000,000 records, 2,500,000 seeded first

2,194,837 generated people; 3.5 people per postcode; large buildings on; 12 workers.

| | Seed | Measure |
|---|---:|---:|
| Records | 2,500,000 | 2,500,000 |
| Commits | asynchronous | durable |
| Throughput (rec/s) | 1777.2 | 825.8 |
| Latency p50 / p95 / p99 (ms) | 6.6 / 9.82 / 13.36 | 10.05 / 34.33 / 67.36 |
| Finding candidates p50 / p95 / p99 (ms) | 1.69 / 3.15 / 4.57 | 2.84 / 6.38 / 34.1 |
| Candidate pool p50 / p95 / max | 3 / 15 / 507 | 10 / 36 / 939 |
| Wrong links at arrival | 0.206% | 0.121% |
| Missed links at arrival | 8.154% | 6.017% |
| Records with a block skipped | 0 | 0 |
| Errors | 0 | 0 |

## Latency as the database grows

| Arrivals | Phase | rec/s | p50 ms | p95 ms | p99 ms | finding p50 / p95 ms | pool p95 |
|---|---|---:|---:|---:|---:|---:|---:|
| 0-250,000 | seed | 2003.0 | 5.99 | 8.52 | 10.07 | 0.91 / 2.07 | 2 |
| 250,000-500,000 | seed | 1836.4 | 6.42 | 9.57 | 11.82 | 1.36 / 2.63 | 5 |
| 500,000-750,000 | seed | 1776.4 | 6.49 | 9.41 | 11.28 | 1.52 / 2.69 | 7 |
| 750,000-1,000,000 | seed | 1743.2 | 6.55 | 9.41 | 11.49 | 1.63 / 2.78 | 10 |
| 1,000,000-1,250,000 | seed | 1767.8 | 6.48 | 8.97 | 10.53 | 1.71 / 2.88 | 12 |
| 1,250,000-1,500,000 | seed | 1675.4 | 6.6 | 9.5 | 12.29 | 1.8 / 3.1 | 14 |
| 1,500,000-1,750,000 | seed | 1422.5 | 6.91 | 10.7 | 13.85 | 1.95 / 3.58 | 16 |
| 1,750,000-2,000,000 | seed | 1443.9 | 6.98 | 11.36 | 23.51 | 2.02 / 3.89 | 18 |
| 2,000,000-2,250,000 | seed | 1453.1 | 6.86 | 10.41 | 17.11 | 2.03 / 3.38 | 21 |
| 2,250,000-2,500,000 | seed | 1431.5 | 6.89 | 10.54 | 17.3 | 2.09 / 3.48 | 23 |
| 2,500,000-2,750,000 | measure | 1062.7 | 9.17 | 26.91 | 55.54 | 2.29 / 3.67 | 25 |
| 2,750,000-3,000,000 | measure | 967.4 | 9.38 | 30.13 | 57.57 | 2.43 / 4.33 | 27 |
| 3,000,000-3,250,000 | measure | 963.8 | 9.32 | 30.91 | 54.53 | 2.5 / 4.54 | 29 |
| 3,250,000-3,500,000 | measure | 846.0 | 9.76 | 33.63 | 55.98 | 2.62 / 4.87 | 32 |
| 3,500,000-3,750,000 | measure | 832.8 | 9.65 | 31.5 | 59.67 | 2.69 / 4.91 | 34 |
| 3,750,000-4,000,000 | measure | 644.2 | 10.31 | 35.11 | 97.77 | 3.02 / 8.9 | 36 |
| 4,000,000-4,250,000 | measure | 682.4 | 11.03 | 46.36 | 110.85 | 3.48 / 19.46 | 38 |
| 4,250,000-4,500,000 | measure | 745.5 | 10.61 | 32.28 | 63.68 | 3.31 / 6.71 | 40 |
| 4,500,000-4,750,000 | measure | 736.7 | 10.89 | 37.4 | 95.55 | 3.39 / 7.58 | 42 |
| 4,750,000-5,000,000 | measure | 656.0 | 11.16 | 39.23 | 115.46 | 3.57 / 7.85 | 45 |

## Cache hit ratio during measure

- database: 98.18%
- match_key_pkey: 100.0%
- ix_match_key_lookup: 96.11%
- ix_blocking_key_lookup: 99.13%

## Database after seed: 18 GB

| Table | Data | Indexes | Total |
|---|---:|---:|---:|
| match_key | 3708 MB | 6910 MB | 10 GB |
| source_record | 4167 MB | 251 MB | 4418 MB |
| blocking_key | 827 MB | 1223 MB | 2049 MB |
| resolution_event | 808 MB | 184 MB | 993 MB |
| customer | 112 MB | 68 MB | 180 MB |
| merge_candidate | 320 kB | 120 kB | 440 kB |
| schema_migrations | 48 kB | 16 kB | 64 kB |
| review_example | 8192 bytes | 24 kB | 32 kB |

Rows: customer 1,659,279, source_record 2,500,000, match_key 50,339,414, blocking_key 12,025,204, merge_candidate 422

Largest blocks (records sharing one blocking key):

- B_DOB_LAST_NYSIIS: 23, 23, 22, 21, 21
- B_DOB_LAST_SOUNDEX: 24, 23, 22, 21, 21
- B_EMAIL: 12, 11, 11, 11, 11
- B_PHONE: 9, 9, 9, 8, 8
- B_POSTCODE: 396, 365, 346, 324, 322
- B_PREMISE_STREET: 37, 30, 30, 29, 26
- B_UPRN: 8, 6, 6, 6, 6

## Database after measure: 35 GB

| Table | Data | Indexes | Total |
|---|---:|---:|---:|
| match_key | 7415 MB | 13 GB | 20 GB |
| source_record | 8335 MB | 487 MB | 8822 MB |
| blocking_key | 1653 MB | 2241 MB | 3894 MB |
| resolution_event | 1963 MB | 352 MB | 2315 MB |
| customer | 162 MB | 90 MB | 253 MB |
| merge_candidate | 504 kB | 416 kB | 920 kB |
| schema_migrations | 48 kB | 16 kB | 64 kB |
| review_example | 8192 bytes | 24 kB | 32 kB |

Rows: customer 2,380,112, source_record 5,000,000, match_key 100,679,009, blocking_key 24,052,392, merge_candidate 2,867

Largest blocks (records sharing one blocking key):

- B_DOB_LAST_NYSIIS: 40, 39, 37, 37, 36
- B_DOB_LAST_SOUNDEX: 40, 39, 38, 37, 37
- B_EMAIL: 20, 20, 20, 19, 19
- B_PHONE: 14, 14, 14, 12, 12
- B_POSTCODE: 747, 720, 711, 640, 624
- B_PREMISE_STREET: 71, 55, 54, 52, 49
- B_UPRN: 10, 9, 8, 8, 8
