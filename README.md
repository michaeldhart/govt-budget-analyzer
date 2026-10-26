# Govt Budget Analyzer

Tax revenue analysis for the City of Belvidere, IL, built from the city's budget ordinances (FY2015-FY2027) and audited annual financial reports (FY2012-FY2026). Started as a proof of concept that produces PDF reports; the goal is a website that visualizes full government budgets (budgeted revenue and expenses vs. audited actuals) across years, and later other local government bodies.

See `CLAUDE.md` for the data pipeline, methodology and findings.

## Layout
- `govbudget/`: shared, entity-agnostic data model (schema, entity config, validation)
- `entities/<entity>/`: per-government config and mapping tables, including an exceptions registry for data that does not fit the shared model
- `pipelines/<entity>/`: that government's extraction and build scripts
- `data/<entity>/`: extracted and derived CSVs (`facts.csv` is the canonical table)
- `output/<entity>/`: generated PDF reports
- `sources/<entity>/`: FY2027 budget; older budget/audit PDFs are not committed (download from the city site)

See `docs/ROADMAP.md` for what is done and what is left, and `CLAUDE.md` for the architecture, pipeline order and methodology.

## License
Apache License 2.0. See `LICENSE` and `NOTICE`.
