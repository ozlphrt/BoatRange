from marine_data.pois import load_marine_pois_wgs84


def test_versioned_poi_fixture_contains_explicit_marine_fuel_points():
    pois = load_marine_pois_wgs84()
    assert len(pois) == 33
    assert sum(item.poi_type == "fuel_dock" for item in pois) == 2
    assert all(item.geometry.is_valid and not item.geometry.is_empty for item in pois)
    assert all(item.source_id.startswith("osm:") for item in pois)


def test_fuel_points_only_come_from_explicit_marine_fuel_tags():
    fuel = [item for item in load_marine_pois_wgs84() if item.poi_type == "fuel_dock"]
    assert fuel
    assert all(
        item.tags.get("waterway") == "fuel"
        or item.tags.get("seamark:small_craft_facility:category") == "fuel_station"
        for item in fuel
    )
