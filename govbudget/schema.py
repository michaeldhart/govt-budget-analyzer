"""Canonical vocabulary and table layouts shared by every entity.

Tables (CSV, one set per entity under data/<entity_id>/):
  documents.csv  one row per source document (budget ordinance, audit, revision)
  accounts.csv   one row per (flow, account_key) seen in any document, with its mapping
  facts.csv      one row per source line x period x basis: the only table consumers should read amounts from
Config (committed, hand-edited, under entities/<entity_id>/):
  entity.json, categories.csv, account_map.csv, audit_map.csv, comparable_groups.csv, exceptions.csv
"""

# What a number is. Every amount in facts.csv has exactly one basis.
BASES = {
    "adopted":          "the budget document's own current-year column (request as adopted by the ordinance)",
    "prior_doc_budget": "a later budget document's prior-year plain Budget column (may be restated or stale)",
    "revised":          "a later budget document's REVISED prior-year column; or a document's own amended / current budget column (the park's amended budget)",
    "requested":        "a draft budget's requested column, before the board adopted it (documents with status draft)",
    "appropriated":     "the appropriation ordinance's authority to spend (a legal ceiling, not a spending plan)",
    "actual_prelim":    "the actual column of a budget document (preliminary; superseded by the audit)",
    "audit_original":   "the audit's original budget column",
    "audit_final":      "the audit's final (amended) budget column",
    "actual_audited":   "the audit's actual column",
}
SOURCE_TYPES = ("budget", "audit")

# flow: revenue and expense are what they say. contra_expense is a reimbursement or rebate that the source prints
# in the expense block (so it is NOT in the printed revenue total) but that offsets an item in the revenue block.
FLOWS = ("revenue", "expense", "contra_expense")

# treatment: how an account counts in the tax analysis.
#   tax = counted in gross taxes; offset = negative amount netted against taxes; excluded_fee = looks like a tax
#   but is a fee (kept visible, never summed); non_tax = everything else (not yet classified further);
#   expense = an expense line (the tax treatments do not apply).
TREATMENTS = ("tax", "offset", "excluded_fee", "non_tax", "expense")

EXCEPTION_KINDS = (
    "definitional",          # a category or measure means something slightly different from its name
    "classification",        # a judgement call on how a line is categorized (record who decided and when)
    "source_typo",           # the source document itself is wrong or inconsistent
    "stale_column",          # a later document's prior-year column does not hold the adopted figure
    "omitted_from_total",    # the source leaves a row out of its own printed total
    "structural_change",     # account renumbered, relabeled, merged or moved between documents
    "not_comparable",        # budget and audit cannot be put on the same footing for this line
    "ocr_correction",        # value corrected by eye against the page image (detail in data/<entity>/overrides)
    "substitute_document",   # another document stands in for one that could not be read (say which, and for what)
)
EXCEPTION_SCOPES = ("account", "group", "doc", "entity")
EXCEPTION_EFFECTS = ("excluded", "included", "adjusted", "informational")

FACT_COLUMNS = [
    "entity_id", "doc_id", "source_type", "row_seq", "period_fy", "basis", "flow", "fund_code", "dept_code",
    "account_key", "account_printed", "audit_section", "label", "amount", "source_column", "page_label",
    "pdf_page", "flag", "exception_ids",
]
DOC_STATUSES = ("draft", "adopted", "final")  # a budget as proposed / as adopted, an audit as issued
DOCUMENT_COLUMNS = ["entity_id", "doc_id", "doc_type", "fiscal_year", "status", "pdf_path", "sha256", "source_url", "note"]
ACCOUNT_COLUMNS = ["entity_id", "flow", "account_key", "label_current", "dept_code", "department", "category_id",
                   "treatment", "first_fy", "last_fy", "note"]
CATEGORY_COLUMNS = ["category_id", "scheme", "name", "flow", "color", "sort", "note"]
ACCOUNT_MAP_COLUMNS = ["flow", "account_key", "label", "department", "category_id", "treatment", "note"]
AUDIT_MAP_COLUMNS = ["audit_label", "comparable_group"]
GROUP_COLUMNS = ["comparable_group", "report_group", "budget_members", "note"]
DEPARTMENT_COLUMNS = ["fund_code", "dept_code", "name", "function", "note"]
# Totals the source prints (department totals, subtotals, NET lines, fund totals): control totals for reconciling facts.
TOTAL_KINDS = ("fund_revenues", "fund_expenses", "fund_net", "dept_revenues", "dept_expenses", "dept_net", "subtotal",
               "beginning_cash", "ending_cash",
               # other budgeted funds and the Ida Public Library budget (fund-scoped names in `name`)
               "other_fund_revenues", "other_fund_expenses", "rollup_beginning_cash", "rollup_revenues", "rollup_expenses",
               "rollup_ending_cash", "library_revenue_total", "library_expense_total", "library_subtotal",
               # park district budget layouts: a printed section total, and a report-level net (surplus) line
               "section_total", "rollup_net")
# Values the OCR read that an eyeballed override or an automatic cross-reading then replaced: one row per corrected cell (target "line", keyed
# by doc_id + account_printed + source_column) or printed total (target "total", keyed by label = the total's name). raw_value is what the OCR
# produced (account text for method account_override; "a|b|c" for a whole row; empty for add_row, a line no OCR reading had), final_value is
# what facts.csv / control_totals.csv hold. methods: row_override (eyeballed from the page image; also single cells), cell_override,
# total_override, total_line_override, account_override, add_row, auto_resolved (another OCR prep or the neighbouring document made a failing
# printed total tie), auto_resolved_digit, auto_resolved_sign, placed_by_sum, from_prep_vote and prep_vote (plurality of the OCR preps).
CORRECTION_METHODS = ("row_override", "cell_override", "total_override", "total_line_override", "account_override", "add_row", "auto_resolved",
                      "auto_resolved_digit", "auto_resolved_sign", "placed_by_sum", "from_prep_vote", "prep_vote")
CORRECTION_COLUMNS = ["entity_id", "doc_id", "source_type", "target", "fund", "account_printed", "account_key", "label", "source_column",
                      "page_label", "pdf_page", "raw_value", "final_value", "method", "detail", "ocr_line"]
CONTROL_TOTAL_COLUMNS = ["entity_id", "doc_id", "period_fy", "basis", "kind", "name", "amount", "page_label"]
# Audited fund-level statements (balance sheet; statement of revenues, expenditures and changes in fund balances), one row per
# line x fund column. line_id is a stable name for the lines every audit has (cash, total_assets, total_fund_balance, ...).
STATEMENTS = ("balance_sheet", "changes_in_fund_balance", "ws_net_position",
              "combining_balance_sheet", "combining_changes_in_fund_balance",      # nonmajor governmental funds, one column per fund
              "ws_stmt_net_position", "ws_stmt_income", "ws_stmt_cash_flows")      # Water / Sewer proprietary fund statements
FUND_STATEMENT_COLUMNS = ["entity_id", "doc_id", "period_fy", "statement", "fund", "section", "subsection", "line_item", "line_id",
                          "amount", "pdf_page", "flag"]
FUND_STATEMENT_MAP_COLUMNS = ["statement", "label_pattern", "line_id", "note"]
# Audited long-term debt, pension and OPEB disclosures (the audits' notes and required supplementary information); long tables, one
# row per printed figure, tied by the checks in data/<entity_id>/audit_debt_pension_checks.csv. exception_ids use '|' like facts.csv.
DEBT_PENSION_TABLES = {
    "debt_balances": ["entity_id", "doc_id", "period_fy", "activity", "line_id", "line_item", "measure", "amount", "pdf_page", "flag", "exception_ids"],
    "debt_issue_terms": ["entity_id", "doc_id", "period_fy", "activity", "issue", "interest_rate_text", "maturity_text", "ending_balance",
                         "current_portion", "description", "pdf_page", "flag", "exception_ids"],
    "debt_service_schedule": ["entity_id", "doc_id", "period_fy", "activity", "instrument", "line_id", "year_ending", "principal", "interest",
                              "total", "row_kind", "pdf_page", "flag", "exception_ids"],
    "pension_rollforward": ["entity_id", "doc_id", "period_fy", "plan_id", "source", "measurement_date", "column_year", "component", "line_id",
                            "amount", "pdf_page", "flag", "exception_ids"],
    "pension_summary": ["entity_id", "doc_id", "period_fy", "plan_id", "measure", "period_label", "value", "value_text", "unit", "source",
                        "pdf_page", "flag", "exception_ids"],
    "opeb_rollforward": ["entity_id", "doc_id", "period_fy", "plan_id", "source", "measurement_date", "column_year", "line_id", "amount",
                         "pdf_page", "flag", "exception_ids"],
}

# Property tax and debt capacity schedules some audits print (the park district's): optional tables, validated when the file exists.
TAX_DEBT_TABLES = {
    "legal_debt_margin": ["entity_id", "doc_id", "period_fy", "schedule", "line_item", "column", "amount", "pdf_page", "flag", "exception_ids"],
    "assessed_valuations": ["entity_id", "doc_id", "period_fy", "tax_year", "section", "line_item", "amount", "pdf_page", "flag", "exception_ids"],
}

EXCEPTION_COLUMNS = ["exception_id", "kind", "scope", "scope_id", "fy_from", "fy_to", "bases", "effect", "summary", "detail",
                     "decided_on", "evidence"]

# Category schemes: where a category_id is used.
SCHEMES = ("budget_category", "report_group", "expense_object", "function_group")
