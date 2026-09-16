"""Regression checks for official managed areas and incomplete legal notices."""

from marine_data.features import RestrictionClass
from marine_data.official_areas import classify_managed_area, load_official_areas_wgs84, load_unresolved_official_notices


def test_official_snapshot_contains_kos_natura_provenance():
    features = load_official_areas_wgs84()
    assert len(features) == 8
    assert len({item.source_id for item in features}) == 8
    assert any("GR4210008" in item.source_id for item in features)
    assert all(item.restriction_class is RestrictionClass.INFORMATIONAL for item in features)
    assert all(item.geometry.is_valid and not item.geometry.is_empty for item in features)


def test_protected_area_is_not_silently_treated_as_navigation_ban():
    kos = next(item for item in load_official_areas_wgs84() if "GR4210008" in item.source_id)
    assert kos.feature_type == "protected_area"
    assert "does not itself prohibit navigation" in kos.classification_rule


def test_text_only_prohibitions_remain_unresolved_without_guessed_geometry():
    notices = load_unresolved_official_notices()
    assert len(notices) == 2
    assert all(item["restrictionClass"] == "CAUTION_CONDITIONAL" for item in notices)
    assert all(item["geometryStatus"].startswith("MISSING_AUTHORITATIVE") for item in notices)
    assert any(item["sourceId"] == "hnhs-pilot-d-206-2014" for item in notices)


def test_explicit_active_military_area_is_hard_but_unknown_status_is_conditional():
    active, _ = classify_managed_area("emodnet:militaryareaspoly", {"status": "Active"})
    unknown, _ = classify_managed_area("emodnet:militaryareaspoly", {"status": "Unknown"})
    assert active is RestrictionClass.HARD_NO_GO
    assert unknown is RestrictionClass.CAUTION_CONDITIONAL
