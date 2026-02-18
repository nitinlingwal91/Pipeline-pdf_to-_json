import json
import re
from pathlib import Path
import pdfplumber
import pandas as pd


def extract_tables_from_page(page):
    table_settings = {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "intersection_tolerance": 5,
        "snap_tolerance": 5,
        "join_tolerance": 2,
        "edge_min_length": 3,
    }

    tables = page.extract_tables(table_settings=table_settings)
    if not tables:
        return None

    candidate = None
    for t in tables:
        if not t or not t[0]:
            continue
        header_row = " ".join(c for c in t[0] if c)
        if "Items" in header_row and "Quantity" in header_row:
            candidate = t
            break

    if candidate is None:
        candidate = max(tables, key=lambda t: len(t))

    if len(candidate) < 2:
        return None

    header = [h or "" for h in candidate[0]]
    header_len = len(header)

    cleaned_rows = []
    for row in candidate[1:]:
        if row is None:
            continue
        r = list(row)
        if len(r) < header_len:
            r = r + [""] * (header_len - len(r))
        elif len(r) > header_len:
            r = r[:header_len]

        if any(cell not in (None, "", " ") for cell in r):
            cleaned_rows.append(r)

    if not cleaned_rows:
        return None

    df = pd.DataFrame(cleaned_rows, columns=header)
    return df


def parse_items_from_text(page_text: str):
    items = []
    lines = [l.strip() for l in page_text.splitlines() if l.strip()]

    header_idx = None
    for i, line in enumerate(lines):
        if ("Items" in line and "Quantity" in line) or line == "Items":
            header_idx = i
            break

    if header_idx is None:
        return items

    start_idx = header_idx
    while start_idx < len(lines) and any(
        h in lines[start_idx]
        for h in ["Items", "Quantity", "Unit Price", "Total"]
    ):
        start_idx += 1

    for line in lines[start_idx:]:
        if re.search(r"\bSubtotal\b|\bTax\b|\bTotal\b", line, re.IGNORECASE):
            break

        nums = re.findall(r"\$?\d[\d.,]*", line)
        if len(nums) < 2:
            continue

        amount = nums[-1]
        unit_price = nums[-2]
        qty = ""
        if len(nums) >= 3:
            qty = nums[0]

        desc = line
        for n in nums:
            desc = desc.replace(n, "")
        desc = desc.strip(" -:,")

        if desc:
            items.append(
                {
                    "description": desc,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "amount": amount,
                }
            )

    return items


def extract_header_fields(words):
    full_text = " ".join(w["text"] for w in words)

    def find(pattern):
        m = re.search(pattern, full_text, flags=re.IGNORECASE)
        return m.group(1).strip() if m else None

    invoice_number = find(r"Invoice\s*#\s*([A-Za-z0-9\-]+)") or \
                     find(r"Invoice\s*#\s*([A-Za-z0-9\-]+)\s*Date")

    invoice_date = find(r"Date\s*:\s*([\d]{1,2}[\/\-][\d]{1,2}[\/\-][\d]{2,4})")
    due_date = find(r"Pay\s*by\s*:\s*([\d]{1,2}[\/\-][\d]{1,2}[\/\-][\d]{2,4})")
    billed_to = find(r"Billed\s*to\s*:\s*([A-Za-z0-9 ,.\-]+)")

    return {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "due_date": due_date,
        "billed_to": billed_to,
    }


def extract_totals(words):
    totals = {}
    lines = {}
    for w in words:
        y_mid = int(round((w["top"] + w["bottom"]) / 2))
        lines.setdefault(y_mid, []).append(w)

    def find_amount_for_label(label_patterns):
        for _, line_words in lines.items():
            line_text = " ".join(lw["text"] for lw in line_words)
            if any(re.search(pat, line_text, re.IGNORECASE) for pat in label_patterns):
                nums = re.findall(r"\$?\d[\d.,]*", line_text)
                if nums:
                    return nums[-1]
        return None

    subtotal = find_amount_for_label([r"\bsubtotal\b"])
    tax = find_amount_for_label([r"\btax\b"])
    total = find_amount_for_label([r"^total\b", r"\btotal\b"])

    if subtotal:
        totals["subtotal"] = subtotal
    if tax:
        totals["tax"] = tax
    if total:
        totals["total"] = total

    return totals


def parse_pdf(path: str):
    path = Path(path)
    result = {
        "document_type": "invoice",
        "file_name": path.name,
        "pages": 0,
        "header_fields": {},
        "line_items": [],
        "totals": {},
    }

    with pdfplumber.open(path) as pdf:
        result["pages"] = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            words = page.extract_words(
                use_text_flow=True,
                keep_blank_chars=False,
            )
            page_text = page.extract_text() or ""

            if i == 0:
                result["header_fields"] = extract_header_fields(words)

            totals = extract_totals(words)
            result["totals"].update(totals)

            df = extract_tables_from_page(page)
            if df is not None and not df.empty:
                items = []
                for row in df.to_dict(orient="records"):
                    item = {
                        "description": row.get("Items", ""),
                        "quantity": row.get("Quantity", ""),
                        "unit_price": row.get("Unit Price", ""),
                        "amount": row.get("Total", ""),
                    }
                    if item["description"] and item["amount"]:
                        items.append(item)
                if items:
                    result["line_items"] = items
                    break  
            if not result["line_items"]:
                items = parse_items_from_text(page_text)
                if items:
                    result["line_items"] = items
                    break 

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python parser.py <pdf_path>")
        raise SystemExit(1)
    data = parse_pdf(sys.argv[1])
    print(json.dumps(data, indent=2, ensure_ascii=False))
