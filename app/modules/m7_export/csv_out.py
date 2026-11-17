"""CSV 导出：页面上的每个数都能在这里找到对应的原始行与原因码。

约定：UTF-8 带 BOM（Excel 直接打开不乱码）、CRLF、Decimal 按列刻度输出为
字符串、行号是源文件物理行号。
"""
from __future__ import annotations

import csv
import io

BOM = "\ufeff"


def csv_text(header: list[str], rows: list[list]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if cell is None else str(cell) for cell in row])
    return BOM + buffer.getvalue()


def report_csv(result: dict) -> str:
    score = result["score"]
    counters = result["manifest"]["counters"]
    rows = [
        ["总分", score["score"], "分", "刻度 0.01"],
        ["等级", score["grade"], "", score["grade_label"]],
        ["扣分合计", score["penalty_sum"], "分", "总分 + 扣分 = 100"],
    ]
    for item in score["dims"]:
        rows.append([f"{item['label']}扣分", item["penalty"], "分",
                     f"比率 {item['rate']} 容忍度 {item['tolerance']} 封顶 {item['cap']}"])
    rows.extend([
        ["原始记录", counters["raw_records"], "行", "含空行与隔离行"],
        ["采纳行", counters["accepted"], "行", ""],
        ["唯一行", counters["rows_unique"], "行", "全行哈希去重后"],
        ["重复行", counters["rows_dup"], "行", ""],
        ["隔离行", counters["quarantine"], "行", ""],
        ["空行", counters["blank"], "行", ""],
        ["单元格", counters["cells_total"], "格", ""],
        ["缺失单元格", counters["cells_missing"], "格", ""],
        ["离群单元格", counters["outlier_cells"], "格", "有值的子集"],
    ])
    # 归属口径与报告页「缺失原因归属」同源：主因计扣分/整改，次要关联不重复计分。
    attribution = result["missing"]["attribution"]
    for group in attribution["groups"]:
        rows.append([f"缺失主因·{group['label']}", group["primary"], "格",
                     f"次要关联 {group['secondary']} 格不计分；命中 {group['eligible']} 格"])
    rows.append(["缺失未归属", attribution["unassigned"]["primary"], "格",
                 "不属任何原因组，记录保留不丢失"])
    rows.append(["缺失次要关联合计", attribution["secondary_total"], "格",
                 "只留共缺追查线索，不进主因合计、不重复扣分"])
    return csv_text(["项目", "数值", "单位", "备注"], rows)


def profile_csv(result: dict) -> str:
    rows = []
    for item in result["profile"]["profile"]:
        rows.append([
            item["name"], item["label"], item["kind"], item["total"],
            item["filled"], item["missing"], item["missing_rate"],
            item["format_bad"], item["format_rate"], item["distinct"],
            item.get("min") or "", item.get("max") or "",
        ])
    return csv_text(["列名", "列标题", "类型", "单元格", "有值", "缺失", "缺失率",
                     "格式不合格", "格式率", "基数", "最小", "最大"], rows)


def missing_csv(result: dict) -> str:
    summary = {item["column"]: item for item in result["missing"]["run_summary"]}
    rows = []
    for item in result["missing"]["account"]["by_column"]:
        extra = summary.get(item["name"], {})
        rows.append([item["name"], item["label"], item["missing"], item["filled"],
                     item["missing_rate"], extra.get("runs", 0),
                     extra.get("longest", 0)])
    return csv_text(["列名", "列标题", "缺失", "有值", "缺失率", "连续段数",
                     "最长段"], rows)


def attribution_csv(result: dict) -> str:
    """原因组归属账：与报告页同一归属范围，主原因与次要关联分栏，不重复计分。"""
    attribution = result["missing"]["attribution"]
    rows = []
    for group in attribution["groups"]:
        rows.append([group["key"], group["label"],
                     " / ".join(group["column_labels"]),
                     group["primary"], group["secondary"], group["eligible"],
                     "主原因计扣分与整改；次要关联只留追查线索"])
    rows.append([attribution["unassigned"]["key"],
                 attribution["unassigned"]["label"], "—",
                 attribution["unassigned"]["primary"], 0,
                 attribution["unassigned"]["primary"],
                 "不属任何原因组，保留记录不丢失"])
    rows.append(["__total__", "缺失格合计（主因闭口）", "—",
                 attribution["primary_total"]
                 + attribution["unassigned"]["primary"],
                 attribution["secondary_total"], attribution["eligible_total"],
                 "主原因+未归属 = 缺失格总数 "
                 f"{attribution['missing_total']}"])
    return csv_text(["原因组", "组名称", "字段", "主原因格", "次要关联格",
                     "命中格", "归属口径"], rows)


def attribution_evidence_csv(result: dict) -> str:
    """逐格归属证据：每个缺失格一行，主原因唯一、次要关联可追。"""
    rows = []
    for item in result["missing"]["attribution"]["evidence"]:
        secondary_code = ("K_SECONDARY_LINK" if item["secondary_groups"] else "")
        rows.append([item["lineno"], item["column"], item["column_label"],
                     item["raw"], item["code"], item["role"], item["role_code"],
                     item["primary_group"], item["primary_label"],
                     " ".join(item["secondary_groups"]), secondary_code])
    return csv_text(["行号", "列名", "列标题", "原值", "缺失原因码", "归属角色",
                     "归属原因码", "主原因组", "主原因组名称", "次要关联组",
                     "次要关联原因码"], rows)


def duplicates_csv(result: dict) -> str:
    duplicates = result["duplicates"]
    rows = []
    for item in duplicates["hash_groups"]:
        rows.append(["全行哈希", item["hash"], item["count"],
                     " ".join(str(lineno) for lineno in item["linenos"]), ""])
    for item in duplicates["key_repeats"]:
        rows.append(["业务键重复", item["key"], item["count"],
                     " ".join(str(lineno) for lineno in item["linenos"]), ""])
    for item in duplicates["conflicts"]:
        rows.append(["业务键冲突", item["key"], item["count"],
                     " ".join(str(lineno) for lineno in item["linenos"]),
                     " ".join(item["fields"])])
    return csv_text(["账", "组或键", "行数", "行号", "冲突列"], rows)


def outliers_csv(result: dict) -> str:
    stats = {item["name"]: item for item in result["outliers"]["columns"]}
    rows = []
    for item in result["outliers"]["cells"]:
        column = stats.get(item["column"], {})
        rows.append([item["column"], column.get("label", item["column"]),
                     item["lineno"], item["raw"], item["value"], item["z"],
                     column.get("method", ""), "K_OUTLIER"])
    return csv_text(["列名", "列标题", "行号", "原值", "归一值", "稳健z", "方法",
                     "原因码"], rows)


def quarantine_csv(result: dict) -> str:
    rows = []
    for item in result["dataset"]["quarantine"]:
        rows.append([item["lineno"], item["segment"], item["code"],
                     item["field"], item["raw_value"], item["detail"]])
    return csv_text(["行号", "段", "原因码", "字段", "原值", "说明"], rows)


def flags_csv(result: dict) -> str:
    rows = []
    for item in result["dataset"]["flags"]:
        rows.append([item["code"], item["label"], item["level"], item["count"]])
    return csv_text(["原因码", "含义", "级别", "次数"], rows)
