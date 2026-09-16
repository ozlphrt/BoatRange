"""Marine feature classification, provenance, and real-layer tests."""

from marine_data.features import (
    RestrictionClass,
    classify_osm_tags,
    load_features_wgs84,
)


def test_physical_obstacles_are_hard_no_go():
    for tags in (
        {"man_made": "breakwater"},
        {"man_made": "groyne"},
        {"seamark:type": "rock"},
        {"seamark:type": "wreck"},
        {"seamark:type": "obstruction"},
    ):
        _, classification, _ = classify_osm_tags(tags)
        assert classification is RestrictionClass.HARD_NO_GO


def test_no_entry_restriction_is_hard_no_go():
    _, classification, rule = classify_osm_tags(
        {
            "seamark:type": "restricted_area",
            "seamark:restricted_area:restriction": "no_entry",
        }
    )
    assert classification is RestrictionClass.HARD_NO_GO
    assert "prohibits" in rule


def test_non_navigation_restriction_is_caution_not_blocked():
    _, classification, _ = classify_osm_tags(
        {
            "seamark:type": "restricted_area",
            "seamark:restricted_area:restriction": "no_anchoring;no_fishing",
        }
    )
    assert classification is RestrictionClass.CAUTION_CONDITIONAL


def test_ambiguous_restricted_area_is_caution_not_silently_hard():
    _, classification, _ = classify_osm_tags({"seamark:type": "restricted_area"})
    assert classification is RestrictionClass.CAUTION_CONDITIONAL


def test_real_bodrum_kos_feature_snapshot_is_normalized():
    features = load_features_wgs84()
    assert len(features) == 168
    assert len({feature.source_id for feature in features}) == len(features)
    assert all(not feature.geometry.is_empty for feature in features)
    assert all(feature.classification_rule for feature in features)
    assert all(
        feature.restriction_class is RestrictionClass.HARD_NO_GO
        for feature in features
    )
    counts = {}
    for feature in features:
        counts[feature.feature_type] = counts.get(feature.feature_type, 0) + 1
    assert counts == {
        "breakwater": 56,
        "groyne": 1,
        "obstruction": 1,
        "rock": 109,
        "wreck": 1,
    }

