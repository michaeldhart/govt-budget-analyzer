# Govt Budget Analyzer

Tax revenue analysis for the City of Belvidere, IL, built from the city's budget ordinances (FY2015-FY2027) and audited annual financial reports (FY2012-FY2026). Started as a proof of concept that produces PDF reports; the goal is a website that visualizes full government budgets (budgeted revenue and expenses vs. audited actuals) across years, and later other local government bodies.

See `CLAUDE.md` for the data pipeline, methodology and findings.

## Layout
- `scripts/`: extraction (OCR), series building and report scripts
- `data/`: extracted and derived CSVs
- `output/`: generated PDF reports
- `sources/`: FY2027 budget; older budget/audit PDFs are not committed (download from the city site)

## License
Apache License 2.0. See `LICENSE` and `NOTICE`.
