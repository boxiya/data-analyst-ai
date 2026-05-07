import os
import pandas as pd

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def save_file(filename: str, content: bytes) -> str:
    """把上传的文件保存到 data/ 目录，返回文件路径"""
    file_path = os.path.join(UPLOAD_DIR, filename)
    with open(file_path, "wb") as f:
        f.write(content)
    return file_path


def load_dataframe(file_path: str) -> pd.DataFrame:
    """根据扩展名读取文件为 DataFrame"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        for enc in ["utf-8", "gbk", "utf-8-sig"]:
            try:
                return pd.read_csv(file_path, encoding=enc)
            except UnicodeDecodeError:
                continue
    elif ext in [".xlsx", ".xls"]:
        return pd.read_excel(file_path)
    raise ValueError(f"不支持的格式：{ext}")


def extract_df_info(df: pd.DataFrame, filename: str) -> dict:
    """提取 DataFrame 的结构信息，后面给 LLM 用"""
    return {
        "filename": filename,
        "rows": len(df),
        "cols": len(df.columns),
        "columns": list(df.columns),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "sample": df.head(3).to_dict(orient="records"),
    }
