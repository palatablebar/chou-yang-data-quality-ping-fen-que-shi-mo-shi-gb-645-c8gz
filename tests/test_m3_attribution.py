"""缺失原因组归属：主原因唯一、次要关联不重复计分、闭口、页面与导出同口径。

裁决规则（app/modules/m3_missing/attribution.py）：
- 一个缺失格有且仅有一个主原因；命中多个组时共缺优先，同档按组配置顺序；
- 其余命中组只登记次要关联，保留共缺追查，不进主因合计、不重复扣分；
- 不命中任何组的缺失格进未归属，记录不丢失。
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from app import config
from app.modules.m3_missing import attribution, service as m3_service
from app.modules.m6_score import service as m6_service
from fixtures import dirty_cases, expected


def _pipeline(settings, text, name="attr.csv"):
    profile = dirty_cases.profile(settings, text, name=name)
    return profile, m3_service.build(profile)


def _group_map(payload: dict) -> dict[str, dict]:
    missing = payload["missing"] if "missing" in payload else payload
    return {item["key"]: item for item in missing["attribution"]["groups"]}


def test_dirty_sample_attribution_matches_adjudication(dirty_result):
    attr = dirty_result["missing"]["attribution"]
    groups = _group_map(dirty_result)
    # 区域是重叠字段：受理人+区域共缺 2 行，区域在 G1 计主原因；
    # 区域与满意度无共缺，5 个区域格在 G2 只保留次要关联。
    assert groups["agent_region"]["primary"] == 10
    assert groups["agent_region"]["secondary"] == 0
    assert groups["region_satisfaction"]["primary"] == 1
    assert groups["region_satisfaction"]["secondary"] == 5
    assert groups["region_satisfaction"]["eligible"] == 6
    # 首响缺失不属任何原因组，进未归属，绝不丢记录。
    assert attr["unassigned"]["primary"] == 3
    assert attr["primary_total"] == 11
    assert attr["secondary_total"] == 5
    assert attr["missing_total"] == expected.DIRTY["cells_missing"]


def test_primary_partition_closes_without_double_count(dirty_result):
    attr = dirty_result["missing"]["attribution"]
    assert attr["closed"] is True
    # 主原因 + 未归属恰好等于全部缺失格：每个缺失格只被计一次。
    assert attr["primary_total"] + attr["unassigned"]["primary"] == attr["missing_total"]
    # 命中账：每组命中 = 主原因 + 次要关联。
    for group in attr["groups"]:
        assert group["eligible"] == group["primary"] + group["secondary"]
    # 次要关联不进主因合计，因此主因数不会因重叠而膨胀。
    assert attr["primary_total"] <= attr["eligible_total"]
    assert sum(group["primary"] for group in attr["groups"]) == attr["primary_total"]


def test_each_missing_cell_has_exactly_one_primary(dirty_result):
    attr = dirty_result["missing"]["attribution"]
    evidence = attr["evidence"]
    # 证据行数等于缺失格总数，一格一行，没有丢、没有重。
    assert len(evidence) == expected.DIRTY["cells_missing"]
    primaries = [row for row in evidence if row["role"] == "primary"]
    unassigned = [row for row in evidence if row["role"] == "unassigned"]
    assert len(primaries) + len(unassigned) == len(evidence)
    # 主原因组只可能是配置里登记的组；次要关联一定不同于主原因。
    known = {group["key"] for group in config.MISSING_REASON_GROUPS}
    for row in primaries:
        assert row["primary_group"] in known
        assert row["primary_group"] not in row["secondary_groups"]
        assert row["role_code"] == "K_PRIMARY_REASON"
    for row in unassigned:
        assert row["primary_group"] == config.UNASSIGNED_KEY
        assert row["secondary_groups"] == []
        assert row["role_code"] == "K_UNASSIGNED_MISSING"


def test_co_missing_overlap_prefers_partner_group(settings):
    """同一行区域与满意度共缺时，区域格应主归 G2，G1 不留关联（G1 无共缺伙伴）。"""
    rows = [
        dirty_cases.make_row(ticket="T-2026-700001", customer="00701",
                             agent="陈允", region="", satisfaction=""),
        dirty_cases.make_row(ticket="T-2026-700002", customer="00702",
                             agent="", region="", satisfaction="4"),
    ]
    _, missing = _pipeline(settings, dirty_cases.frame(rows))
    groups = _group_map(missing)
    # 行1：区域+满意度共缺 → 区域、满意度都主归 G2；
    # 行2：受理人+区域共缺 → 受理人、区域都主归 G1，区域在 G2 留 1 条次要关联。
    assert groups["region_satisfaction"]["primary"] == 2
    assert groups["agent_region"]["primary"] == 2
    # 两个区域格各在「非主因」的另一组留一条次要关联，共缺线索不丢但不重复计分。
    assert groups["region_satisfaction"]["secondary"] == 1
    assert groups["agent_region"]["secondary"] == 1
    assert missing["attribution"]["closed"] is True
    # 主因闭口：4 个缺失格（区域计 2 次、受理人 1、满意度 1），各归一个主因。
    assert missing["attribution"]["primary_total"] == 4
    assert missing["attribution"]["unassigned"]["primary"] == 0


def test_single_region_missing_goes_to_configured_first_group(settings):
    """区域单点缺失（无任何伙伴共缺）按配置顺序主归 G1，在 G2 留次要关联。"""
    rows = [dirty_cases.make_row(ticket="T-2026-710001", customer="00711",
                                 agent="陈允", region="", satisfaction="4")]
    _, missing = _pipeline(settings, dirty_cases.frame(rows))
    groups = _group_map(missing)
    assert groups["agent_region"]["primary"] == 1
    assert groups["region_satisfaction"]["primary"] == 0
    assert groups["region_satisfaction"]["secondary"] == 1


def test_attribution_is_order_independent(settings):
    rows = [
        dirty_cases.make_row(ticket=f"T-2026-72{i:04d}", customer=f"0072{i}",
                             agent="" if i % 2 == 0 else "陈允",
                             region="" if i % 3 == 0 else "华东",
                             satisfaction="" if i % 5 == 0 else "4")
        for i in range(12)
    ]
    forward = m3_service.build(dirty_cases.profile(
        settings, dirty_cases.frame(rows)))["attribution"]
    backward = m3_service.build(dirty_cases.profile(
        settings, dirty_cases.frame(list(reversed(rows)))))["attribution"]
    key = lambda payload: [(g["key"], g["primary"], g["secondary"])
                           for g in payload["groups"]]
    assert key(forward) == key(backward)
    assert forward["unassigned"] == backward["unassigned"]


def test_clean_frame_has_no_attribution_rows(settings):
    profile = dirty_cases.profile(settings, dirty_cases.frame(
        dirty_cases.count_rows(4)))
    attr = attribution.build_attribution(profile)
    assert attr["missing_total"] == 0
    assert attr["primary_total"] == 0
    assert attr["secondary_total"] == 0
    assert attr["evidence"] == []
    assert attr["closed"] is True


def test_reconcile_carries_two_attribution_accounts(dirty_result):
    accounts = {item["key"]: item for item in dirty_result["reconcile"]["accounts"]}
    primary_account = accounts["missing_primary"]
    link_account = accounts["missing_link"]
    assert primary_account["balanced"] is True
    assert Decimal(str(primary_account["left"])) == Decimal("14")
    assert Decimal(str(primary_account["right"])) == Decimal("14")
    assert link_account["balanced"] is True
    # 命中 16 = 主原因 11 + 次要关联 5。
    assert Decimal(str(link_account["left"])) == Decimal("16")
    assert Decimal(str(link_account["right"])) == Decimal("16")
    reconciled = dirty_result["reconcile"]["attribution"]
    assert reconciled["closed"] is True
    assert reconciled["primary_total"] == 11
    assert reconciled["secondary_total"] == 5


def test_retry_does_not_add_group_or_double_score(settings, store):
    first = m6_service.run(settings, store, name="tickets_dirty.csv")
    second = m6_service.run(settings, store, name="tickets_dirty.csv")
    assert second["replay"] is True
    assert store.count_runs() == 1
    assert first["run"]["run_id"] == second["run"]["run_id"]
    assert first["score"]["score"] == second["score"]["score"]
    a1, a2 = first["missing"]["attribution"], second["missing"]["attribution"]
    assert len(a1["groups"]) == len(a2["groups"]) == 2
    assert [(g["key"], g["primary"], g["secondary"]) for g in a1["groups"]] == \
           [(g["key"], g["primary"], g["secondary"]) for g in a2["groups"]]


def test_interrupted_write_keeps_previous_run_intact(settings, store, monkeypatch):
    """写入中断：旧版本完整可读，失败的半成品不落运行记录，重试不产生重复。"""
    first = m6_service.run(settings, store, name="tickets_dirty.csv")
    first_id = first["run"]["run_id"]

    # 用一份全新输入走「新建运行」路径，并在落运行记录这一刻模拟崩溃。
    crash_text = dirty_cases.frame(dirty_cases.count_rows(6, agent=""))

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash during persist")

    monkeypatch.setattr(store, "save_run", boom)
    try:
        m6_service.run(settings, store, name="crash.csv",
                       data=crash_text.encode("utf-8"))
        raise AssertionError("应当抛出写入异常")
    except RuntimeError:
        pass

    # 崩溃后上一份完整旧版仍可恢复，半成品没有留下运行记录。
    assert store.count_runs() == 1
    restored = store.load_result(first_id)
    assert restored is not None
    assert restored["run"]["run_id"] == first_id
    assert restored["score"]["score"] == expected.DIRTY["score"]
    assert restored["missing"]["attribution"]["closed"] is True

    monkeypatch.undo()
    # 同一份崩溃输入重试：成功落一条，再重试命中幂等复用，不新增、不重复计分。
    recovered = m6_service.run(settings, store, name="crash.csv",
                               data=crash_text.encode("utf-8"))
    assert recovered["replay"] is False
    assert store.count_runs() == 2
    again = m6_service.run(settings, store, name="crash.csv",
                           data=crash_text.encode("utf-8"))
    assert again["replay"] is True
    assert again["run"]["run_id"] == recovered["run"]["run_id"]
    assert store.count_runs() == 2


def test_attribution_csv_matches_page_scope(client_with_run):
    """页面与导出读同一条快照、同一归属范围：主因/关联数字必须一致。"""
    page = client_with_run.get("/").text
    response = client_with_run.get("/api/export/attribution.csv")
    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    header = rows[0]
    assert header == ["原因组", "组名称", "字段", "主原因格", "次要关联格",
                      "命中格", "归属口径"]
    data = {row[0]: row for row in rows[1:]}
    assert data["agent_region"][3] == "10"      # 主原因
    assert data["region_satisfaction"][3] == "1"
    assert data["region_satisfaction"][4] == "5"  # 次要关联
    assert data["unassigned"][3] == "3"
    # 页面上同样出现这些主因数字，保证报告页与导出同口径。
    assert "受理人与区域缺失" in page
    assert "区域与满意度缺失" in page


def test_attribution_evidence_csv_lists_every_missing_cell(client_with_run):
    response = client_with_run.get("/api/export/attribution_evidence.csv")
    assert response.status_code == 200
    text = response.content.decode("utf-8")
    rows = [line for line in text.splitlines() if line.strip()]
    # 表头 + 每个缺失格一行（脏样例 14 个缺失格）。
    assert len(rows) == expected.DIRTY["cells_missing"] + 1
    assert "K_PRIMARY_REASON" in text
    assert "K_UNASSIGNED_MISSING" in text
    # 区域格主归 G1、关联 G2，5 条次要关联都带 K_SECONDARY_LINK 原因码。
    assert text.count("K_SECONDARY_LINK") == 5
    header = rows[0].lstrip("﻿").split(",")
    assert header[-1] == "次要关联原因码"
    # 抽一条重叠区域格（第 9 行）核对主因与关联同留痕。
    overlap = next(line for line in rows if line.startswith("9,region"))
    assert "agent_region" in overlap and "region_satisfaction" in overlap
    assert "K_SECONDARY_LINK" in overlap


def test_missing_api_exposes_attribution(client_with_run):
    body = client_with_run.get("/api/missing").json()
    attr = body["attribution"]
    assert attr["closed"] is True
    assert attr["missing_total"] == expected.DIRTY["cells_missing"]
    assert {g["key"] for g in attr["groups"]} == {
        "agent_region", "region_satisfaction"}


def test_manifest_includes_attribution_for_offline_reconcile(client_with_run):
    payload = client_with_run.get("/api/export/manifest.json").json()
    attr = payload["attribution"]
    assert attr["closed"] is True
    # 清单里的归属合计与计数器同源，离线也能复算对账。
    assert attr["primary_total"] == payload["counters"]["missing_primary"]
    assert attr["secondary_total"] == payload["counters"]["missing_secondary_links"]
    assert attr["unassigned"]["primary"] == payload["counters"]["missing_unassigned"]
