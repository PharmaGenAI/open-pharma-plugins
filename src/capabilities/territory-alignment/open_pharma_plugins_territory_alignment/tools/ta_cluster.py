"""ta_cluster — visit clusters and sequencing (operational mode)."""

from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, Field


class AppointmentArg(BaseModel):
    hcp_id: str = Field(description="HCP with a fixed appointment")
    date: datetime.date = Field(description="ISO date, e.g. '2026-08-25'")
    time: datetime.time = Field(default=datetime.time(9), description="HH:MM, e.g. '10:00'")


class ClusterArgs(BaseModel):
    scenario_name: str = Field(description="Saved alignment scenario to plan from")
    rep_id: str = Field(description="Rep to build a visit plan for")
    period: str = Field(
        default="next_week",
        pattern=r"^(?:next_week|next_month|\d{4}-W\d{2})$",
        description="Planning period: 'next_week', 'next_month', or ISO week '2026-W35'",
    )
    appointments: list[AppointmentArg] = Field(
        default_factory=list, description="Fixed appointments to schedule around"
    )
    max_daily_travel_min: int = Field(default=120, ge=1, le=1440, description="Max travel minutes per day")
    remote_threshold_min: int = Field(
        default=60, ge=0, le=1440, description="One-way travel above this suggests a remote alternative"
    )


TOOL: dict[str, Any] = {
    "name": "ta_cluster",
    "description": (
        "Build a visit plan for a rep: cluster their assigned HCPs "
        "geographically, sequence visits within each cluster using a "
        "nearest-neighbor route, and flag remote alternatives where travel "
        "exceeds the threshold. Requires an explicit saved alignment scenario."
    ),
    "args": ClusterArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from ..data import load_scenario
    from ..geo import centroid, grid_cluster, haversine, nearest_neighbor_route, travel_minutes, two_opt_improve
    from ..models import HCP, RemoteAlternative, Rep, VisitCluster, VisitPlan, VisitStop

    try:
        parsed = ClusterArgs.model_validate(arguments)
    except ValueError as exc:
        return [{"type": "text", "text": json.dumps({"error": str(exc)})}]
    args = parsed.model_dump(mode="json")
    scenario_name = args["scenario_name"]
    rep_id = args["rep_id"]
    period = args["period"]
    remote_threshold = args["remote_threshold_min"]
    max_daily_travel = args["max_daily_travel_min"]
    appointments_raw = args["appointments"]

    try:
        scenario = load_scenario(scenario_name)
    except ValueError as exc:
        return [{"type": "text", "text": json.dumps({"error": str(exc)})}]
    if scenario is None:
        return [{"type": "text", "text": json.dumps({"error": f"Scenario '{scenario_name}' not found."})}]
    snapshot = scenario["input_snapshot"]
    reps = [Rep.model_validate(item) for item in snapshot["reps"]]
    rep = next((r for r in reps if r.rep_id == rep_id), None)
    if rep is None:
        return [{"type": "text", "text": json.dumps({"error": f"Rep '{rep_id}' not found."})}]

    rep_hcp_ids = {a["hcp_id"] for a in scenario.get("assignments", []) if a["primary_rep"] == rep_id}

    hcps = [HCP.model_validate(item) for item in snapshot["hcps"]]
    hcp_map = {h.hcp_id: h for h in hcps}
    rep_hcps = [hcp_map[hid] for hid in rep_hcp_ids if hid in hcp_map]

    if not rep_hcps:
        return [
            {
                "type": "text",
                "text": json.dumps({"error": f"No HCPs assigned to rep '{rep_id}' in scenario '{scenario_name}'."}),
            }
        ]

    visitable = [h for h in rep_hcps if h.consent_visit]
    excluded_no_visit_consent = sorted(h.hcp_id for h in rep_hcps if not h.consent_visit)

    geo_hcps = [(h.lat, h.lng, h.hcp_id) for h in visitable if h.lat is not None and h.lng is not None]
    non_geo = [h for h in visitable if h.lat is None or h.lng is None]

    appointment_details: dict[str, tuple[str, str]] = {}
    for appt in appointments_raw:
        hid = appt["hcp_id"] if isinstance(appt, dict) else appt.hcp_id
        date = appt["date"] if isinstance(appt, dict) else appt.date
        time = appt.get("time", "09:00") if isinstance(appt, dict) else appt.time
        time_text = str(time)
        if len(time_text) == 8 and time_text.endswith(":00"):
            time_text = time_text[:5]
        if hid in appointment_details:
            return [{"type": "text", "text": json.dumps({"error": f"Duplicate appointment for HCP '{hid}'."})}]
        appointment_details[hid] = (str(date), time_text)

    days = list(rep.available_days) if rep.available_days else ["mon", "tue", "wed", "thu", "fri"]
    max_calls = rep.max_daily_calls
    try:
        planning_dates = _period_dates(period, days)
    except ValueError as exc:
        return [{"type": "text", "text": json.dumps({"error": str(exc)})}]
    planning_date_set = {day.isoformat() for day in planning_dates}
    for hcp_id, (appointment_date, _appointment_time) in appointment_details.items():
        if hcp_id not in rep_hcp_ids:
            return [
                {
                    "type": "text",
                    "text": json.dumps({"error": f"Appointment HCP '{hcp_id}' is not assigned to rep '{rep_id}'."}),
                }
            ]
        if hcp_id not in {hcp.hcp_id for hcp in visitable}:
            return [
                {"type": "text", "text": json.dumps({"error": f"Appointment HCP '{hcp_id}' has no visit consent."})}
            ]
        if appointment_date not in planning_date_set:
            return [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "error": f"Appointment for HCP '{hcp_id}' falls outside period '{period}' or rep availability."
                        }
                    ),
                }
            ]

    clusters_out: list[VisitCluster] = []
    sequence_out: list[VisitStop] = []
    remote_out: list[RemoteAlternative] = []
    total_km = 0.0

    if geo_hcps:
        cluster_ids = grid_cluster(geo_hcps, target_per_cluster=min(max_calls, 6))

        split_clusters: list[list[str]] = []
        for id_list in cluster_ids:
            while len(id_list) > max_calls:
                split_clusters.append(id_list[:max_calls])
                id_list = id_list[max_calls:]
            split_clusters.append(id_list)

        appointment_day_map = _map_appointments_to_days(
            {hcp_id: detail[0] for hcp_id, detail in appointment_details.items()}, days
        )

        day_assignments: dict[int, str] = {}
        date_assignments: dict[int, str] = {}
        for ci, id_list in enumerate(split_clusters):
            cluster_appointment_dates = {appointment_details[hid][0] for hid in id_list if hid in appointment_details}
            if len(cluster_appointment_dates) > 1:
                return [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "error": (
                                    f"Cluster {ci + 1} contains appointments on multiple dates; "
                                    "reduce cluster size or plan those visits separately."
                                )
                            }
                        ),
                    }
                ]
            for hid in id_list:
                if hid in appointment_day_map:
                    day_assignments[ci] = appointment_day_map[hid]
                    date_assignments[ci] = appointment_details[hid][0]
                    break

        reserved_dates = set(date_assignments.values())
        calls_by_reserved_date: dict[str, int] = {}
        for cluster_index, reserved_date in date_assignments.items():
            calls_by_reserved_date[reserved_date] = calls_by_reserved_date.get(reserved_date, 0) + len(
                split_clusters[cluster_index]
            )
        over_capacity_date = next(
            (date for date, count in calls_by_reserved_date.items() if count > max_calls),
            None,
        )
        if over_capacity_date:
            return [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "error": (
                                f"Appointments on {over_capacity_date} require "
                                f"{calls_by_reserved_date[over_capacity_date]} calls, exceeding "
                                f"rep max_daily_calls={max_calls}."
                            )
                        }
                    ),
                }
            ]
        available_unreserved = [day for day in planning_dates if day.isoformat() not in reserved_dates]
        next_date = 0
        for ci in range(len(split_clusters)):
            if ci in date_assignments:
                continue
            if next_date < len(available_unreserved):
                planned = available_unreserved[next_date]
                next_date += 1
                date_assignments[ci] = planned.isoformat()
                day_assignments[ci] = _DAY_NAMES[planned.weekday()]

        for ci, id_list in enumerate(split_clusters):
            cluster_hcps = [hcp_map[hid] for hid in id_list if hid in hcp_map]
            points = [(h.lat, h.lng) for h in cluster_hcps if h.lat is not None and h.lng is not None]

            if not points:
                continue

            start = (rep.base_lat, rep.base_lng)
            order, route_km = nearest_neighbor_route(points, start=start)
            order, route_km = two_opt_improve(points, order, start=start, return_to_start=True)
            fixed_times = {
                index: appointment_details[hcp.hcp_id][1]
                for index, hcp in enumerate(cluster_hcps)
                if hcp.hcp_id in appointment_details
            }
            if len(fixed_times) > 1:
                route_position = {point_index: position for position, point_index in enumerate(order)}
                order.sort(
                    key=lambda point_index: (
                        0 if point_index in fixed_times else 1,
                        fixed_times.get(point_index, ""),
                        route_position[point_index],
                    )
                )
                order, route_km = two_opt_improve(
                    points,
                    order,
                    max_iterations=0,
                    start=start,
                    return_to_start=True,
                )

            route_min = travel_minutes(route_km)

            total_km += route_km

            c_lat, c_lng = centroid(points)
            cid = f"C{ci + 1:02d}"

            day = day_assignments.get(ci, "")

            warning = ""
            if route_min > max_daily_travel:
                warning = f"route travel {route_min:.0f} min exceeds {max_daily_travel} min daily limit"

            clusters_out.append(
                VisitCluster(
                    cluster_id=cid,
                    hcp_ids=id_list,
                    centroid_lat=round(c_lat, 4),
                    centroid_lng=round(c_lng, 4),
                    estimated_route_km=round(route_km, 1),
                    estimated_travel_min=round(route_min, 1),
                    hcp_count=len(id_list),
                    suggested_day=day,
                    suggested_date=date_assignments.get(ci, ""),
                    travel_warning=warning,
                )
            )

            prev_point = start
            for visit_i, idx in enumerate(order):
                h = cluster_hcps[idx]
                hop_km = haversine(prev_point[0], prev_point[1], points[idx][0], points[idx][1])
                sequence_out.append(
                    VisitStop(
                        hcp_id=h.hcp_id,
                        hcp_name=h.name,
                        visit_order=visit_i + 1,
                        cluster_id=cid,
                        lat=points[idx][0],
                        lng=points[idx][1],
                        travel_km_from_previous=round(hop_km, 1),
                        appointment_date=appointment_details.get(h.hcp_id, ("", ""))[0],
                        appointment_time=appointment_details.get(h.hcp_id, ("", ""))[1],
                    )
                )
                prev_point = points[idx]

                one_way = travel_minutes(haversine(rep.base_lat, rep.base_lng, h.lat, h.lng))
                if one_way > remote_threshold:
                    remote_out.append(
                        RemoteAlternative(
                            hcp_id=h.hcp_id,
                            hcp_name=h.name,
                            distance_km=round(haversine(rep.base_lat, rep.base_lng, h.lat, h.lng), 1),
                            reason=f"one-way travel {one_way:.0f} min exceeds {remote_threshold} min threshold",
                        )
                    )

    for h in non_geo:
        if h.consent_email or h.consent_phone:
            remote_out.append(
                RemoteAlternative(
                    hcp_id=h.hcp_id,
                    hcp_name=h.name,
                    distance_km=0.0,
                    reason="missing coordinates; use a consented remote channel or geocode before planning a visit",
                )
            )

    unplanned_hcp_ids = sorted(
        {
            *(h.hcp_id for h in non_geo),
            *(hcp_id for cluster in clusters_out if not cluster.suggested_date for hcp_id in cluster.hcp_ids),
        }
    )

    plan = VisitPlan(
        scenario_name=scenario_name,
        rep_id=rep_id,
        rep_name=rep.name,
        period=period,
        planning_dates=[day.isoformat() for day in planning_dates],
        clusters=clusters_out,
        visit_sequence=sequence_out,
        remote_alternatives=remote_out,
        total_route_km=round(total_km, 1),
        total_hcps=len(visitable),
        remote_count=len(remote_out),
        excluded_no_visit_consent=excluded_no_visit_consent,
        unplanned_hcp_ids=unplanned_hcp_ids,
    )

    return [{"type": "text", "text": json.dumps(json.loads(plan.model_dump_json()), indent=2)}]


_DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _period_dates(period: str, available_days: list[str], today: datetime.date | None = None) -> list[datetime.date]:
    today = today or datetime.date.today()
    if period == "next_week":
        start = today + datetime.timedelta(days=(7 - today.weekday()))
        end = start + datetime.timedelta(days=6)
    elif period == "next_month":
        if today.month == 12:
            start = datetime.date(today.year + 1, 1, 1)
        else:
            start = datetime.date(today.year, today.month + 1, 1)
        if start.month == 12:
            next_start = datetime.date(start.year + 1, 1, 1)
        else:
            next_start = datetime.date(start.year, start.month + 1, 1)
        end = next_start - datetime.timedelta(days=1)
    else:
        try:
            year_text, week_text = period.split("-W", 1)
            start = datetime.date.fromisocalendar(int(year_text), int(week_text), 1)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid ISO planning week {period!r}") from exc
        end = start + datetime.timedelta(days=6)

    dates: list[datetime.date] = []
    current = start
    while current <= end:
        if _DAY_NAMES[current.weekday()] in available_days:
            dates.append(current)
        current += datetime.timedelta(days=1)
    return dates


def _map_appointments_to_days(
    appointment_hcp_ids: dict[str, str],
    available_days: list[str],
) -> dict[str, str]:
    """Map appointment HCP IDs to a day-of-week string based on the ISO date."""
    result: dict[str, str] = {}
    for hcp_id, date_str in appointment_hcp_ids.items():
        try:
            dt = datetime.date.fromisoformat(date_str)
            dow = _DAY_NAMES[dt.weekday()]
            if dow in available_days:
                result[hcp_id] = dow
        except ValueError:
            continue
    return result
