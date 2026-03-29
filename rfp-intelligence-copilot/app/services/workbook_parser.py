import pandas as pd
from pathlib import Path

def parse_workbook(file_path: str):
    """Parse Excel workbook and return structure"""
    try:
        xls = pd.ExcelFile(file_path)
        sheets = {}
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(file_path, sheet_name=sheet_name, nrows=10)
            sheets[sheet_name] = {
                "columns": [str(col) for col in df.columns],
                "row_count": len(df),
                "sample_data": df.head(3).fillna("").to_dict(orient="records")
            }
        return {
            "sheet_count": len(xls.sheet_names),
            "sheets": sheets,
            "total_rows": sum(s["row_count"] for s in sheets.values())
        }
    except Exception as e:
        return {"error": str(e)}
