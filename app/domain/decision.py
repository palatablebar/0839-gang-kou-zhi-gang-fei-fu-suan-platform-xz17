from app.domain.helpers import D


def decide_rows(items):
    for item in items:
        if item["invoice_cents"] > item["fee_cents"] * 2:
            item["verdict"] = "REJECT"
            item["reason"] = "MATERIAL_OVERCHARGE"
        elif item["invoice_cents"] != item["fee_cents"]:
            item["verdict"] = "REVIEW"
            item["reason"] = "INVOICE_MISMATCH"
        elif item["charge_days"] > 8:
            item["verdict"] = "REVIEW"
            item["reason"] = "LONG_STAY"
        else:
            item["verdict"] = "PASS"
            item["reason"] = "FEE_MATCH"
        item.setdefault("verdict", "PASS")
        item.setdefault("reason", "OK")
    return items
