# CHECKPOINTS.md — PDF Fetch Bot

| % | What was added | File-wrapper URL | AI Drive mirror |
|---|---|---|---|
| 50 | Full codebase: bot.py (aiogram + health server), scraper/ (base, generic adapters, pdf compiler), requirements.txt, render.yaml, .env.example, README.md | https://www.genspark.ai/api/files/s/4ADTz6h2 | /pdfbot_checkpoints/PDFBot_Recovery_50pct.zip |
| 100 | Hardened scraper (retry/backoff, fault-tolerant downloads), live test suite test_live.py — TEST1 direct PDF PASS (1,016,315 bytes), TEST2 lazy-load extract PASS, TEST3 images→PDF PASS (3 pages, valid %PDF). This ledger. | (see final message) | /pdfbot_checkpoints/PDFBot_Recovery_100pct.zip |

## Test results (live, this sandbox)
- TEST1 direct PDF: PASS via raw.githubusercontent.com — 1,016,315 bytes, %PDF magic verified
- TEST2 lazy-load bypass: PASS — data-src placeholders + srcset-largest selection, 3/3 URLs
- TEST3 images→PDF compile: PASS — 3 pages, 38,150 bytes, %PDF magic verified
- Note: w3.org and picsum.photos are unreachable from this sandbox's egress; code path was still exercised and passes on reachable hosts.
