"""缺失原因组归属裁决：一格一主原因，重叠只留次要关联。

背景：抽检有两个原因组——「受理人与区域缺失」(agent/region) 与
「区域与满意度缺失」(region/satisfaction)。区域格同时命中两组，若全量重复归属，
同一格的扣分与整改量会被算两遍；若只归一个组又会切断仍需追查的共缺关系。

裁决（确定性，与行序无关，唯一裁决入口）：对每个缺失格，取所有命中的候选组——
1. 本行里该组还有别的伙伴字段也缺失（共缺命中）的组优先于单点命中的组；
2. 同档按 config.MISSING_REASON_GROUPS 的配置顺序（priority 小者优先）；
3. 仍并列再按列名兜底，保证任何输入结果唯一。

排第一的组是这一格的**主原因**：扣分与整改量只按主原因统计一次；
其余命中组只登记为**次要关联**，保留共缺追查线索，不重复计分、不重复派整改。
不命中任何组的缺失格进「未归属」桶，任何一格都不会丢失。

闭口恒等式（attribution["closed"] 强制校验）：
  缺失格总数 = 各主原因计数之和（含未归属）
  各原因组命中格 = 该组主原因 + 该组次要关联
"""
from __future__ import annotations

from ... import config, reasons
from .cells import is_missing


def _group_defs() -> list[dict]:
    """按配置顺序物化原因组；列名全部回收到列契约，未知列直接忽略。"""
    known = set(config.COLUMN_NAMES)
    defs = []
    for priority, group in enumerate(config.MISSING_REASON_GROUPS):
        columns = tuple(name for name in group["columns"] if name in known)
        defs.append({"key": group["key"], "label": group["label"],
                     "priority": priority, "columns": columns,
                     "column_set": frozenset(columns)})
    return defs


def _labels(profile: dict) -> dict[str, str]:
    return {column["name"]: column["label"] for column in profile["columns"]}


def _candidate_rank(group: dict, column: str, missing_here: frozenset) -> tuple:
    """共缺命中优先（0），单点命中次之（1）；同档比配置优先级，再比列名兜底。"""
    partner_missing = bool((group["column_set"] & missing_here) - {column})
    return (0 if partner_missing else 1, group["priority"], column)


def build_attribution(profile: dict) -> dict:
    groups = _group_defs()
    labels = _labels(profile)
    columns = [column["name"] for column in profile["columns"]]

    primary_counts = {group["key"]: 0 for group in groups}
    secondary_counts = {group["key"]: 0 for group in groups}
    eligible_counts = {group["key"]: 0 for group in groups}
    unassigned = 0
    evidence: list[dict] = []

    for row in profile["rows"]:
        missing_here = frozenset(
            name for name in columns if is_missing(row["cells"][name]))
        for column in sorted(missing_here):
            cell = row["cells"][column]
            candidates = [group for group in groups
                          if column in group["column_set"]]
            for group in candidates:
                eligible_counts[group["key"]] += 1
            if not candidates:
                unassigned += 1
                evidence.append({
                    "lineno": row["lineno"], "column": column,
                    "column_label": labels[column], "raw": cell["raw"],
                    "code": cell["code"], "primary_group": config.UNASSIGNED_KEY,
                    "primary_label": config.UNASSIGNED_LABEL,
                    "role": "unassigned",
                    "role_code": "K_UNASSIGNED_MISSING",
                    "role_label": reasons.label("K_UNASSIGNED_MISSING"),
                    "secondary_groups": [],
                })
                continue

            ordered = sorted(candidates,
                             key=lambda group: _candidate_rank(
                                 group, column, missing_here))
            winner = ordered[0]
            primary_counts[winner["key"]] += 1
            secondary = [group["key"] for group in ordered[1:]]
            for group in ordered[1:]:
                secondary_counts[group["key"]] += 1
            evidence.append({
                "lineno": row["lineno"], "column": column,
                "column_label": labels[column], "raw": cell["raw"],
                "code": cell["code"], "primary_group": winner["key"],
                "primary_label": winner["label"],
                "role": "primary",
                "role_code": "K_PRIMARY_REASON",
                "role_label": reasons.label("K_PRIMARY_REASON"),
                "secondary_groups": secondary,
                "secondary_role_code": "K_SECONDARY_LINK",
                "secondary_role_label": reasons.label("K_SECONDARY_LINK"),
            })

    group_ledger = []
    for group in groups:
        primary = primary_counts[group["key"]]
        secondary = secondary_counts[group["key"]]
        group_ledger.append({
            "key": group["key"],
            "label": group["label"],
            "priority": group["priority"],
            "columns": list(group["columns"]),
            "column_labels": [labels[name] for name in group["columns"]],
            "primary": primary,
            "secondary": secondary,
            "eligible": eligible_counts[group["key"]],
        })

    missing_total = sum(primary_counts.values()) + unassigned
    # 主原因账：每个缺失格恰好一个主原因（含未归属），不重不漏。
    primary_partition_balanced = (
        sum(item["primary"] for item in group_ledger) + unassigned
        == missing_total)
    # 命中账：每组命中格 = 主原因 + 次要关联，次要关联不进主原因合计。
    eligible_balanced = all(
        item["eligible"] == item["primary"] + item["secondary"]
        for item in group_ledger)
    account_missing = _account_missing_total(profile)
    closed = (primary_partition_balanced and eligible_balanced
              and missing_total == account_missing)

    return {
        "groups": group_ledger,
        "unassigned": {
            "key": config.UNASSIGNED_KEY,
            "label": config.UNASSIGNED_LABEL,
            "primary": unassigned,
        },
        "missing_total": missing_total,
        "primary_total": sum(item["primary"] for item in group_ledger),
        "secondary_total": sum(item["secondary"] for item in group_ledger),
        "eligible_total": sum(item["eligible"] for item in group_ledger),
        "closed": closed,
        "evidence": evidence,
        "rule": ("一格一主原因：共缺命中优先，同档按原因组配置顺序；"
                 "重叠组只登记次要关联，不重复计分；不命中任何组进未归属"),
    }


def _account_missing_total(profile: dict) -> int:
    return sum(1 for row in profile["rows"]
               for column in profile["columns"]
               if is_missing(row["cells"][column["name"]]))


def evidence_rows(attribution: dict, limit: int = 200) -> list[dict]:
    """逐格归属证据：行号 + 列 + 主原因 + 角色 + 次要关联，页面与导出共用。"""
    return attribution["evidence"][:limit]
