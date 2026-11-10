from openpyxl import Workbook

from cxquality.roster import load_roster_xlsx, normalize_alias, suggest_alias


def test_roster_loads_unheaded_alias_column_and_leave_status(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(["الموظف", "الرقم الوظيفي", "الدولة", "الفرع", None])
    sheet.append(["عبدالله عماد", 2447, "HO", "HO", "abdallahemad.dnp"])
    sheet.append(["محمود سامح", "إجازة", "HO", "HO", "mahmoud.dnp1"])
    path = tmp_path / "roster.xlsx"
    book.save(path)
    records = load_roster_xlsx(path)
    assert records[0].staff_id == "2447"
    assert records[0].canonical_name_en == "Abdallahemad"
    assert records[1].staff_id is None and not records[1].active


def test_manual_override_wins_and_alias_matches(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(["الموظف", "الرقم الوظيفي", "الدولة", "الفرع", "alias"])
    sheet.append(["احمد ممدوح", "إجازة", "الامارات", "الرمس", "alramsdnp"])
    path = tmp_path / "roster.xlsx"
    book.save(path)
    records = load_roster_xlsx(
        path,
        manual_overrides=[
            {
                "canonical_name": "احمد ممدوح",
                "canonical_name_en": "Ahmed Mamdouh",
                "staff_id": "1406",
                "branch": "Al Rams",
                "aliases": ["DNP Al Rams"],
                "active": True,
            }
        ],
    )
    assert records[0].staff_id == "1406"
    assert records[0].branch == "Al Rams"
    assert records[0].active
    suggestion = suggest_alias("alramsdnp", records)
    assert suggestion.status == "matched"
    assert suggestion.agent.staff_id == "1406"


def test_ambiguous_or_low_similarity_is_not_silently_merged(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(["name", "id", "country", "branch", "alias"])
    sheet.append(["Ahmed One", 1, "AE", "A", "ahmed.one"])
    sheet.append(["Ahmed Two", 2, "AE", "B", "ahmed.two"])
    path = tmp_path / "roster.xlsx"
    book.save(path)
    records = load_roster_xlsx(path)
    assert suggest_alias("completely different", records).status == "unmatched"
    assert normalize_alias(" Ahmed.One ") == "ahmedone"

