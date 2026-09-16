# Exact water containment

Routing result version: `1.0.1`.

Every coarse-grid edge, refined-grid edge, and refinement stitch must pass
both the buffered-obstacle collision check and containment in the exact
classified water geometry. The same checks apply to route simplification.
Two navigable endpoints alone do not prove the intervening segment is valid.

Previously, graph edges checked obstacles but omitted water containment.
Disconnected water polygons could therefore acquire an edge across a gap
absent from the explicit obstacle layer. Route simplification also expanded
the water mask by one meter, potentially joining sub-meter gaps. The mask
now uses the original water geometry without expansion.

Permanent synthetic regression fixtures live in
`services/routing/tests/test_water_containment.py`. They cover 20-meter and
0.5-meter gaps with refinement enabled and disabled, rejected refinement
stitches, simplification across a sub-meter gap, and an unobstructed route
that preserves the requested endpoints. These fixtures test topology; they
are not substitutes for actual marine data.

Verification from the repository root in PowerShell:

```powershell
$env:PYTHONPATH = 'services/routing;services/marine-data;services/isochrone;packages/fuel-model;apps/api'
& ./.venv/Scripts/python.exe -m pytest services/routing/tests services/isochrone/tests --import-mode=importlib -q
```

This change does not complete the marine-data classification milestone.
Depth coverage, restrictions, source freshness, and the remaining v1
acceptance criteria still need separate verification and implementation.
