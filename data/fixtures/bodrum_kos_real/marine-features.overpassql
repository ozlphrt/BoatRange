[out:json][timeout:60];
(
  node["seamark:type"~"^(rock|wreck|obstruction)$"](36.60,26.85,37.25,27.85);
  way["seamark:type"~"^(rock|wreck|obstruction|restricted_area)$"](36.60,26.85,37.25,27.85);
);
out tags geom;
