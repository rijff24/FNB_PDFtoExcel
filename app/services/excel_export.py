from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font


def build_excel_bytes(transactions: list[dict[str, Any]]) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"

    headers = ["Date", "Description", "Amount", "Balance", "Accrued Bank Charges"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for row in transactions:
        ws.append(
            [
                row.get("date"),
                row.get("description"),
                row.get("amount"),
                row.get("balance"),
                row.get("charges"),
            ]
        )

    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 60
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 20

    for col in ("C", "D", "E"):
        for cell in ws[col][1:]:
            cell.number_format = "#,##0.00"

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output
