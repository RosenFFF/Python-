"""导出 CSV / Excel。"""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from urllib.parse import quote

from fastapi import Response
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from app.core.errors import ValidationFailed

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """防 CSV 公式注入：文本单元格以公式字符开头时加单引号。负数等数值类型不受影响。"""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def to_csv(headers: list[str], rows: Iterable[list]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    # 带 BOM，Excel 直接打开中文不乱码
    return buffer.getvalue().encode("utf-8-sig")


def to_xlsx(headers: list[str], rows: Iterable[list], sheet: str = "数据") -> bytes:
    book = Workbook()
    ws = book.active
    ws.title = sheet[:31]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="25518F")
    for row in rows:
        ws.append([safe_cell(cell) for cell in row])
    ws.freeze_panes = "A2"
    for index, header in enumerate(headers, start=1):
        ws.column_dimensions[ws.cell(1, index).column_letter].width = max(12, len(str(header)) * 2 + 2)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def file_response(filename: str, fmt: str, headers: list[str], rows: list[list]) -> Response:
    if fmt == "csv":
        content, media = to_csv(headers, rows), "text/csv; charset=utf-8"
    elif fmt == "xlsx":
        content, media = to_xlsx(headers, rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        raise ValidationFailed("导出格式必须是 csv 或 xlsx")
    disposition = f"attachment; filename*=UTF-8''{quote(f'{filename}.{fmt}')}"
    return Response(content=content, media_type=media, headers={"Content-Disposition": disposition})
