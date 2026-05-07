import pandas as pd
import numpy as np
import traceback


def execute_code(code: str, df: pd.DataFrame) -> dict:
    """
    在受限环境里执行 LLM 生成的分析代码
    返回 {"success": bool, "result": dict, "error": str}
    """
    # 执行环境：只给必要的库，防止乱跑危险代码
    exec_env = {
        "df": df.copy(),
        "pd": pd,
        "np": np,
        "result": None,
        "fig": None,
    }

    # 尝试导入 plotly
    try:
        import plotly.express as px
        import plotly.graph_objects as go
        exec_env["px"] = px
        exec_env["go"] = go
    except ImportError:
        pass

    try:
        exec(code, exec_env)

        result = exec_env.get("result") or {}
        fig = exec_env.get("fig")

        # 如果有图表，转成 JSON 字符串传给前端
        if fig is not None:
            result["chart"] = fig.to_json()

        # 处理 numpy 类型，避免 JSON 序列化报错
        result = _clean(result)

        return {"success": True, "result": result, "error": None}

    except Exception as e:
        return {"success": False, "result": None, "error": traceback.format_exc()}


def _clean(obj):
    """递归把 numpy 类型转成 Python 原生类型"""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean(i) for i in obj]
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    elif isinstance(obj, pd.Series):
        return obj.to_dict()
    else:
        return obj
