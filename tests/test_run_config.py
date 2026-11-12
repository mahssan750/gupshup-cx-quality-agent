from datetime import date

from cxquality.run_config import infer_run_dates


def test_date_range_is_inferred_from_export_parent_folder(tmp_path):
    zips = tmp_path / "gupshup-2026-11-03-to-2026-11-09" / "zips"
    zips.mkdir(parents=True)
    assert infer_run_dates(zips) == ("2026-11-03", "2026-11-09")


def test_date_range_falls_back_to_current_dubai_date(tmp_path):
    zips = tmp_path / "weekly-zips"
    zips.mkdir()
    assert infer_run_dates(zips, today=date(2026, 11, 12)) == (
        "2026-11-12",
        "2026-11-12",
    )
