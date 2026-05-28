"""
Canonical chart of accounts.

This is the normalization layer described in the architecture: every source
(XBRL tag, annual-report line item, API field) maps INTO one of these canonical
concept codes. NBFCs/banks and non-financials share some concepts and have their
own. When you ingest a real XBRL filing, you write a mapping table from the
filing's taxonomy tags to these codes, and everything downstream stays clean.
"""

# Common to all company types
NET_WORTH = "NET_WORTH"        # shareholders' equity (₹ cr)
NET_PROFIT = "NET_PROFIT"      # profit after tax (₹ cr)

# Non-financials
REVENUE = "REVENUE"            # total revenue / sales (₹ cr)
NET_DEBT = "NET_DEBT"          # total debt - cash (₹ cr)

# Financials / NBFC-specific
AUM = "AUM"                    # assets under management (₹ cr)
GNPA = "GNPA"                  # gross NPA ratio (fraction)
NNPA = "NNPA"                  # net NPA ratio (fraction)
CRAR = "CRAR"                  # capital adequacy ratio (fraction)
NIM = "NIM"                    # net interest margin (fraction)
ROA = "ROA"                    # return on assets (fraction)

COMMON = [NET_WORTH, NET_PROFIT]
NONFINANCIAL = [REVENUE, NET_DEBT]
FINANCIAL = [AUM, GNPA, NNPA, CRAR, NIM, ROA]

ALL_CONCEPTS = COMMON + NONFINANCIAL + FINANCIAL
