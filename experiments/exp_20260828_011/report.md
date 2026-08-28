# exp_20260828_011 report

The narrow recovery successor validates and pointer-adopts exactly the four
complete attempt001 leaves without copying raw rows. It rejects raw/receipt,
size/hash, schema, page-chain, date-manifest and contiguous-prefix drift.

Focused tests pass 7/7. Python compilation, PowerShell syntax parsing and the
read-only dry recovery plan pass. No broad regression suite was run. The dry
plan reports recovery-registry SHA-256
`cce055f98ad209faa6fb448f5697df4fcafa631e2a96f7b6395885f5c050e2a6`,
exactly 458 remaining network dates, zero overlap with the four adopted dates,
and first network request 2024-07-08 under attempt002.

No key or network was used during development. The 23-file candidate is frozen
under SHA-256
`a18f6c6e4738d2db56f79f668d3ddc0181e3054ecfd679aa7c87610532c3acc1`.
The launcher SHA-256 is
`7e9d053ef118b457aa5d9fe006f6dc82d88d741cd51248c8aa995249629564bd`;
the exact 393-byte formal command SHA-256 is
`57690b3a3d681329fa041897080ea90c33050b81b1d977c10ee73a1c50274d8c`.

## Formal recovery 002

The exact frozen command executed once with zero retries. It correctly began at
2024-07-08, never requested the four adopted dates, and published the immutable
2024-07 shard: 17 new dates, 74,396 rows and 20,631,378 raw bytes; raw-tree
SHA-256
`0ae120f541c3ed1acd87d4bab25c07ce8b6838e8bdb6b1808dd865b49677e96d`.

During 2024-08 it preserved seven more complete HTTP 200 leaves for 2024-08-01
through 2024-08-09: 30,694 rows and 8,523,586 raw bytes; partial raw-tree
SHA-256
`fc65a71502014073e75ccdbc6f983b3dc88070bf396de0a4d203f9c9897604f8`.
The eighth August send attempt stopped on
`socket.gaierror: [Errno 11002] getaddrinfo failed`, producing no response/body.

Across attempt001 adoption and attempt002, 28 of 462 network dates are locally
available and 434 remain. Including the three bootstrap reuse dates, 31 of 465
official sessions have source leaves. The next missing date is 2024-08-13.
Network inventory SHA-256 is
`96fbac65c2e4b03f44e85241f9ccc54a10463016f129bab057cbdf68aaf5dc88`.

One month is final, August remains append-only staging, and no global catalog
exists. The key is absent from the process environment after exit and raw data
remain Git-ignored. Terminal state:
`INCONCLUSIVE / DNS_RESOLUTION_FAILURE_ONE_MONTH_FINAL_NEXT_MONTH_PARTIAL`.
