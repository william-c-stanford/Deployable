"""
Seed data: 10 projects across lifecycle stages for the Deployable workforce OS.

Lifecycle stages:
  - Draft         (planning phase, not yet staffed)
  - Staffing      (mobilization, actively seeking technicians)
  - Active        (work in progress)
  - Wrapping Up   (nearing completion)
  - Closed        (completed)
  - On Hold       (paused)

Each project has explicit start/end dates, a partner assignment,
location/region, required role slots, and a realistic fiber/data-center scope.
"""

from datetime import date

# ---------------------------------------------------------------------------
# Partner reference IDs  (must match partners seed data)
# ---------------------------------------------------------------------------
PARTNER_LUMEN = "partner_lumen"
PARTNER_CORNING = "partner_corning"
PARTNER_ZAYO = "partner_zayo"
PARTNER_CROWN_CASTLE = "partner_crown_castle"
PARTNER_FRONTIER = "partner_frontier"
PARTNER_UNITI = "partner_uniti"

# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
PROJECTS = [
    # ── 1. ACTIVE ─────────────────────────────────────────────────────────
    {
        "id": "proj_atl_fiber_ring",
        "name": "Atlanta Metro Fiber Ring Expansion",
        "description": (
            "288-count backbone fiber ring connecting five carrier hotels across "
            "metro Atlanta.  Phase 2 extends lateral drops to 34 enterprise buildings."
        ),
        "status": "Active",
        "partner_id": PARTNER_LUMEN,
        "location_city": "Atlanta",
        "location_region": "GA",
        "start_date": date(2025, 11, 1),
        "end_date": date(2026, 6, 30),
        "budget_hours": 12_000,
        "roles": [
            {
                "id": "role_atl_lead_splicer",
                "name": "Lead Splicer",
                "quantity": 2,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Advanced"},
                    {"skill": "OTDR Testing", "min_level": "Intermediate"},
                ],
                "required_certs": ["FOA CFOT", "OSHA 10"],
                "hourly_rate": 52.00,
                "per_diem": 75.00,
            },
            {
                "id": "role_atl_fiber_tech",
                "name": "Fiber Technician",
                "quantity": 6,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Intermediate"},
                    {"skill": "Cable Pulling", "min_level": "Apprentice"},
                ],
                "required_certs": ["FOA CFOT"],
                "hourly_rate": 38.00,
                "per_diem": 65.00,
            },
            {
                "id": "role_atl_aerial_tech",
                "name": "Aerial Technician",
                "quantity": 3,
                "skill_bundle": [
                    {"skill": "Aerial Construction", "min_level": "Intermediate"},
                    {"skill": "Cable Pulling", "min_level": "Intermediate"},
                ],
                "required_certs": ["OSHA 10", "Bucket Truck Certified"],
                "hourly_rate": 42.00,
                "per_diem": 70.00,
            },
        ],
    },

    # ── 2. ACTIVE ─────────────────────────────────────────────────────────
    {
        "id": "proj_dfw_dc_build",
        "name": "DFW Data Center Structured Cabling",
        "description": (
            "Full structured cabling buildout for a 45 MW hyperscale data center "
            "in the Dallas-Fort Worth corridor.  Includes 2,400 rack positions, "
            "fiber backbone, and copper distribution."
        ),
        "status": "Active",
        "partner_id": PARTNER_CROWN_CASTLE,
        "location_city": "Dallas",
        "location_region": "TX",
        "start_date": date(2025, 9, 15),
        "end_date": date(2026, 5, 15),
        "budget_hours": 18_000,
        "roles": [
            {
                "id": "role_dfw_dc_lead",
                "name": "Data Center Lead",
                "quantity": 1,
                "skill_bundle": [
                    {"skill": "Structured Cabling", "min_level": "Advanced"},
                    {"skill": "Data Center Operations", "min_level": "Advanced"},
                    {"skill": "OTDR Testing", "min_level": "Intermediate"},
                ],
                "required_certs": ["BICSI RCDD", "OSHA 30"],
                "hourly_rate": 62.00,
                "per_diem": 85.00,
            },
            {
                "id": "role_dfw_cable_tech",
                "name": "Cable Technician",
                "quantity": 8,
                "skill_bundle": [
                    {"skill": "Structured Cabling", "min_level": "Intermediate"},
                    {"skill": "Cable Pulling", "min_level": "Intermediate"},
                ],
                "required_certs": ["BICSI Installer 1"],
                "hourly_rate": 36.00,
                "per_diem": 65.00,
            },
            {
                "id": "role_dfw_fiber_splicer",
                "name": "Fiber Splicer",
                "quantity": 3,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Advanced"},
                    {"skill": "Data Center Operations", "min_level": "Apprentice"},
                ],
                "required_certs": ["FOA CFOT"],
                "hourly_rate": 48.00,
                "per_diem": 70.00,
            },
        ],
    },

    # ── 3. STAFFING (mobilization) ────────────────────────────────────────
    {
        "id": "proj_phx_ftth",
        "name": "Phoenix FTTH Neighborhood Rollout",
        "description": (
            "Fiber-to-the-home deployment across 12 subdivisions in the east "
            "Phoenix valley.  Includes trenching, conduit placement, fiber pulling, "
            "and ONT installation for 4,200 residential premises."
        ),
        "status": "Staffing",
        "partner_id": PARTNER_FRONTIER,
        "location_city": "Phoenix",
        "location_region": "AZ",
        "start_date": date(2026, 4, 1),
        "end_date": date(2026, 10, 31),
        "budget_hours": 22_000,
        "roles": [
            {
                "id": "role_phx_lead_splicer",
                "name": "Lead Splicer",
                "quantity": 3,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Advanced"},
                    {"skill": "OTDR Testing", "min_level": "Advanced"},
                ],
                "required_certs": ["FOA CFOT", "OSHA 10"],
                "hourly_rate": 54.00,
                "per_diem": 80.00,
            },
            {
                "id": "role_phx_install_tech",
                "name": "FTTH Install Technician",
                "quantity": 10,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Apprentice"},
                    {"skill": "Underground Construction", "min_level": "Apprentice"},
                ],
                "required_certs": ["OSHA 10"],
                "hourly_rate": 32.00,
                "per_diem": 60.00,
            },
            {
                "id": "role_phx_underground",
                "name": "Underground Technician",
                "quantity": 4,
                "skill_bundle": [
                    {"skill": "Underground Construction", "min_level": "Intermediate"},
                    {"skill": "Cable Pulling", "min_level": "Intermediate"},
                ],
                "required_certs": ["OSHA 10"],
                "hourly_rate": 40.00,
                "per_diem": 70.00,
            },
        ],
    },

    # ── 4. STAFFING (mobilization) ────────────────────────────────────────
    {
        "id": "proj_chi_5g_backhaul",
        "name": "Chicago 5G Small-Cell Backhaul",
        "description": (
            "Fiber backhaul installation connecting 180 small-cell sites across "
            "Chicago's downtown and near-north corridors to three aggregation hubs."
        ),
        "status": "Staffing",
        "partner_id": PARTNER_ZAYO,
        "location_city": "Chicago",
        "location_region": "IL",
        "start_date": date(2026, 4, 15),
        "end_date": date(2026, 9, 30),
        "budget_hours": 9_500,
        "roles": [
            {
                "id": "role_chi_fiber_tech",
                "name": "Fiber Technician",
                "quantity": 5,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Intermediate"},
                    {"skill": "Aerial Construction", "min_level": "Intermediate"},
                ],
                "required_certs": ["FOA CFOT", "OSHA 10"],
                "hourly_rate": 44.00,
                "per_diem": 75.00,
            },
            {
                "id": "role_chi_site_surveyor",
                "name": "Site Surveyor",
                "quantity": 2,
                "skill_bundle": [
                    {"skill": "Network Design", "min_level": "Intermediate"},
                    {"skill": "OTDR Testing", "min_level": "Apprentice"},
                ],
                "required_certs": ["OSHA 10"],
                "hourly_rate": 46.00,
                "per_diem": 80.00,
            },
        ],
    },

    # ── 5. DRAFT (planning) ───────────────────────────────────────────────
    {
        "id": "proj_sea_enterprise",
        "name": "Seattle Enterprise Campus Fiber",
        "description": (
            "Dark fiber and lit services deployment across a 6-building corporate "
            "campus in Redmond, WA.  Scope includes underground duct construction, "
            "96-count fiber installation, and termination at each MDF."
        ),
        "status": "Draft",
        "partner_id": PARTNER_CORNING,
        "location_city": "Redmond",
        "location_region": "WA",
        "start_date": date(2026, 6, 1),
        "end_date": date(2026, 9, 15),
        "budget_hours": 4_800,
        "roles": [
            {
                "id": "role_sea_lead",
                "name": "Project Lead Technician",
                "quantity": 1,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Advanced"},
                    {"skill": "Structured Cabling", "min_level": "Advanced"},
                    {"skill": "Network Design", "min_level": "Intermediate"},
                ],
                "required_certs": ["BICSI RCDD", "FOA CFOT", "OSHA 10"],
                "hourly_rate": 58.00,
                "per_diem": 90.00,
            },
            {
                "id": "role_sea_fiber_tech",
                "name": "Fiber Technician",
                "quantity": 4,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Intermediate"},
                    {"skill": "Underground Construction", "min_level": "Apprentice"},
                ],
                "required_certs": ["FOA CFOT"],
                "hourly_rate": 40.00,
                "per_diem": 75.00,
            },
        ],
    },

    # ── 6. WRAPPING UP (nearing completion) ───────────────────────────────
    {
        "id": "proj_den_long_haul",
        "name": "Denver–Cheyenne Long-Haul Fiber",
        "description": (
            "96-count long-haul fiber route along the I-25 corridor between Denver "
            "and Cheyenne (approx. 100 miles).  Final splice closures and OTDR "
            "acceptance testing in progress."
        ),
        "status": "Wrapping Up",
        "partner_id": PARTNER_ZAYO,
        "location_city": "Denver",
        "location_region": "CO",
        "start_date": date(2025, 6, 1),
        "end_date": date(2026, 3, 31),
        "budget_hours": 15_000,
        "roles": [
            {
                "id": "role_den_lead_splicer",
                "name": "Lead Splicer",
                "quantity": 2,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Advanced"},
                    {"skill": "OTDR Testing", "min_level": "Advanced"},
                ],
                "required_certs": ["FOA CFOT", "OSHA 10"],
                "hourly_rate": 55.00,
                "per_diem": 80.00,
            },
            {
                "id": "role_den_fiber_tech",
                "name": "Fiber Technician",
                "quantity": 4,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Intermediate"},
                    {"skill": "Aerial Construction", "min_level": "Intermediate"},
                ],
                "required_certs": ["FOA CFOT", "OSHA 10"],
                "hourly_rate": 42.00,
                "per_diem": 70.00,
            },
        ],
    },

    # ── 7. CLOSED (completed) ─────────────────────────────────────────────
    {
        "id": "proj_nash_mdu",
        "name": "Nashville MDU Fiber Retrofit",
        "description": (
            "Fiber-to-the-unit retrofit of 28 multi-dwelling-unit properties in "
            "downtown Nashville.  All splicing, testing, and resident activations "
            "completed.  Final timesheets approved."
        ),
        "status": "Closed",
        "partner_id": PARTNER_FRONTIER,
        "location_city": "Nashville",
        "location_region": "TN",
        "start_date": date(2025, 3, 1),
        "end_date": date(2025, 10, 31),
        "budget_hours": 8_500,
        "roles": [
            {
                "id": "role_nash_lead",
                "name": "Lead Installer",
                "quantity": 1,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Advanced"},
                    {"skill": "Structured Cabling", "min_level": "Intermediate"},
                ],
                "required_certs": ["FOA CFOT", "OSHA 10"],
                "hourly_rate": 50.00,
                "per_diem": 75.00,
            },
            {
                "id": "role_nash_install_tech",
                "name": "Install Technician",
                "quantity": 6,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Apprentice"},
                    {"skill": "Structured Cabling", "min_level": "Apprentice"},
                ],
                "required_certs": ["OSHA 10"],
                "hourly_rate": 34.00,
                "per_diem": 60.00,
            },
        ],
    },

    # ── 8. CLOSED (completed) ─────────────────────────────────────────────
    {
        "id": "proj_rdu_campus",
        "name": "RDU Research Triangle Campus Network",
        "description": (
            "Full fiber and copper infrastructure buildout for a biotech campus in "
            "Research Triangle Park, NC.  Three buildings connected via underground "
            "duct bank.  Project completed under budget."
        ),
        "status": "Closed",
        "partner_id": PARTNER_CORNING,
        "location_city": "Durham",
        "location_region": "NC",
        "start_date": date(2025, 1, 15),
        "end_date": date(2025, 8, 30),
        "budget_hours": 6_200,
        "roles": [
            {
                "id": "role_rdu_dc_tech",
                "name": "Structured Cabling Technician",
                "quantity": 4,
                "skill_bundle": [
                    {"skill": "Structured Cabling", "min_level": "Intermediate"},
                    {"skill": "Fiber Splicing", "min_level": "Apprentice"},
                ],
                "required_certs": ["BICSI Installer 1"],
                "hourly_rate": 38.00,
                "per_diem": 65.00,
            },
            {
                "id": "role_rdu_underground",
                "name": "Underground Technician",
                "quantity": 2,
                "skill_bundle": [
                    {"skill": "Underground Construction", "min_level": "Intermediate"},
                    {"skill": "Cable Pulling", "min_level": "Intermediate"},
                ],
                "required_certs": ["OSHA 10"],
                "hourly_rate": 40.00,
                "per_diem": 70.00,
            },
        ],
    },

    # ── 9. ON HOLD ────────────────────────────────────────────────────────
    {
        "id": "proj_mia_subsea",
        "name": "Miami Subsea Cable Landing Station Upgrade",
        "description": (
            "Upgrade fiber termination infrastructure at a subsea cable landing "
            "station in Miami.  Project on hold pending environmental permit "
            "approval from Dade County."
        ),
        "status": "On Hold",
        "partner_id": PARTNER_UNITI,
        "location_city": "Miami",
        "location_region": "FL",
        "start_date": date(2026, 3, 1),
        "end_date": date(2026, 8, 31),
        "budget_hours": 5_000,
        "roles": [
            {
                "id": "role_mia_senior_splicer",
                "name": "Senior Fiber Splicer",
                "quantity": 2,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Advanced"},
                    {"skill": "OTDR Testing", "min_level": "Advanced"},
                    {"skill": "Data Center Operations", "min_level": "Intermediate"},
                ],
                "required_certs": ["FOA CFOT", "FOA CFOS/S", "OSHA 30"],
                "hourly_rate": 60.00,
                "per_diem": 85.00,
            },
            {
                "id": "role_mia_fiber_tech",
                "name": "Fiber Technician",
                "quantity": 3,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Intermediate"},
                    {"skill": "Data Center Operations", "min_level": "Apprentice"},
                ],
                "required_certs": ["FOA CFOT", "OSHA 10"],
                "hourly_rate": 44.00,
                "per_diem": 75.00,
            },
        ],
    },

    # ── 10. ACTIVE ────────────────────────────────────────────────────────
    {
        "id": "proj_slc_rural_fiber",
        "name": "Rural Utah BEAD Fiber Expansion",
        "description": (
            "Federally-funded BEAD program fiber deployment across three rural "
            "Utah counties south of Salt Lake City.  Mix of aerial and underground "
            "construction serving 1,800 unserved locations."
        ),
        "status": "Active",
        "partner_id": PARTNER_LUMEN,
        "location_city": "Provo",
        "location_region": "UT",
        "start_date": date(2025, 12, 1),
        "end_date": date(2026, 8, 31),
        "budget_hours": 14_000,
        "roles": [
            {
                "id": "role_slc_lead",
                "name": "Field Supervisor",
                "quantity": 1,
                "skill_bundle": [
                    {"skill": "Aerial Construction", "min_level": "Advanced"},
                    {"skill": "Underground Construction", "min_level": "Advanced"},
                    {"skill": "Fiber Splicing", "min_level": "Intermediate"},
                ],
                "required_certs": ["OSHA 30", "Bucket Truck Certified"],
                "hourly_rate": 56.00,
                "per_diem": 90.00,
            },
            {
                "id": "role_slc_aerial_tech",
                "name": "Aerial Technician",
                "quantity": 4,
                "skill_bundle": [
                    {"skill": "Aerial Construction", "min_level": "Intermediate"},
                    {"skill": "Cable Pulling", "min_level": "Intermediate"},
                ],
                "required_certs": ["OSHA 10", "Bucket Truck Certified"],
                "hourly_rate": 42.00,
                "per_diem": 75.00,
            },
            {
                "id": "role_slc_splicer",
                "name": "Fiber Splicer",
                "quantity": 3,
                "skill_bundle": [
                    {"skill": "Fiber Splicing", "min_level": "Advanced"},
                    {"skill": "OTDR Testing", "min_level": "Intermediate"},
                ],
                "required_certs": ["FOA CFOT"],
                "hourly_rate": 50.00,
                "per_diem": 75.00,
            },
            {
                "id": "role_slc_underground",
                "name": "Underground Technician",
                "quantity": 3,
                "skill_bundle": [
                    {"skill": "Underground Construction", "min_level": "Intermediate"},
                    {"skill": "Cable Pulling", "min_level": "Apprentice"},
                ],
                "required_certs": ["OSHA 10"],
                "hourly_rate": 38.00,
                "per_diem": 70.00,
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Summary helper — useful for validation and debugging
# ---------------------------------------------------------------------------
def get_project_summary() -> list[dict]:
    """Return a compact summary of all seeded projects."""
    summary = []
    for p in PROJECTS:
        total_headcount = sum(r["quantity"] for r in p["roles"])
        summary.append(
            {
                "id": p["id"],
                "name": p["name"],
                "status": p["status"],
                "region": p["location_region"],
                "partner": p["partner_id"],
                "dates": f"{p['start_date']} → {p['end_date']}",
                "budget_hours": p["budget_hours"],
                "total_headcount": total_headcount,
                "roles": len(p["roles"]),
            }
        )
    return summary


# ---------------------------------------------------------------------------
# Quick validation when run directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from pprint import pprint

    statuses = [p["status"] for p in PROJECTS]
    print(f"Total projects: {len(PROJECTS)}")
    print(f"Status distribution: { {s: statuses.count(s) for s in set(statuses)} }")
    print()
    for s in get_project_summary():
        pprint(s)
        print()
