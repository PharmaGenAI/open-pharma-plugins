"""Geographic utilities: haversine, distance matrix, clustering, routing."""

from __future__ import annotations

import math

_EARTH_RADIUS_KM = 6371.0


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two points."""
    lat1, lng1, lat2, lng2 = (math.radians(v) for v in (lat1, lng1, lat2, lng2))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def travel_minutes(km: float, speed_kmh: float = 40.0) -> float:
    """Rough urban travel estimate."""
    if km <= 0:
        return 0.0
    return (km / speed_kmh) * 60.0


def distance_matrix(
    points: list[tuple[float, float]],
) -> list[list[float]]:
    """Pairwise haversine distances (km) between points."""
    n = len(points)
    mat = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine(points[i][0], points[i][1], points[j][0], points[j][1])
            mat[i][j] = d
            mat[j][i] = d
    return mat


def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Arithmetic mean of lat/lng points."""
    if not points:
        return 0.0, 0.0
    lat = sum(p[0] for p in points) / len(points)
    lng = sum(p[1] for p in points) / len(points)
    return lat, lng


def grid_cluster(
    points: list[tuple[float, float, str]],
    target_per_cluster: int = 6,
) -> list[list[str]]:
    """Simple grid-based clustering. Each point is (lat, lng, id).

    Returns a list of clusters, each a list of point IDs.
    """
    if not points:
        return []

    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)

    n = len(points)
    num_clusters = max(1, round(n / target_per_cluster))
    grid_side = max(1, math.ceil(math.sqrt(num_clusters)))

    lat_step = (max_lat - min_lat + 1e-9) / grid_side
    lng_step = (max_lng - min_lng + 1e-9) / grid_side

    cells: dict[tuple[int, int], list[str]] = {}
    for lat, lng, pid in points:
        r = min(int((lat - min_lat) / lat_step), grid_side - 1)
        c = min(int((lng - min_lng) / lng_step), grid_side - 1)
        cells.setdefault((r, c), []).append(pid)

    clusters = list(cells.values())

    merged: list[list[str]] = []
    for cluster in clusters:
        if len(cluster) < 2 and merged:
            merged[-1].extend(cluster)
        else:
            merged.append(cluster)

    return merged if merged else [list(cells.values())[0]] if cells else []


def nearest_neighbor_route(
    points: list[tuple[float, float]],
    start: tuple[float, float] | None = None,
) -> tuple[list[int], float]:
    """Nearest-neighbor TSP heuristic. Returns (visit_order, total_km)."""
    if not points:
        return [], 0.0
    if len(points) == 1:
        d = haversine(start[0], start[1], points[0][0], points[0][1]) if start else 0.0
        return [0], d

    n = len(points)
    visited = [False] * n
    order: list[int] = []
    total_km = 0.0

    if start:
        dists = [haversine(start[0], start[1], p[0], p[1]) for p in points]
        current_idx = min(range(n), key=lambda i: dists[i])
        total_km += dists[current_idx]
    else:
        current_idx = 0

    visited[current_idx] = True
    order.append(current_idx)

    for _ in range(n - 1):
        best_dist = float("inf")
        best_idx = -1
        for j in range(n):
            if visited[j]:
                continue
            d = haversine(
                points[current_idx][0],
                points[current_idx][1],
                points[j][0],
                points[j][1],
            )
            if d < best_dist:
                best_dist = d
                best_idx = j
        visited[best_idx] = True
        order.append(best_idx)
        total_km += best_dist
        current_idx = best_idx

    return order, total_km


def two_opt_improve(
    points: list[tuple[float, float]],
    route: list[int],
    max_iterations: int = 100,
    start: tuple[float, float] | None = None,
    return_to_start: bool = False,
) -> tuple[list[int], float]:
    """Improve a route using 2-opt swaps."""
    n = len(route)

    def route_distance(r: list[int]) -> float:
        total = sum(
            haversine(points[r[i]][0], points[r[i]][1], points[r[i + 1]][0], points[r[i + 1]][1])
            for i in range(len(r) - 1)
        )
        if start and r:
            total += haversine(start[0], start[1], points[r[0]][0], points[r[0]][1])
            if return_to_start:
                total += haversine(points[r[-1]][0], points[r[-1]][1], start[0], start[1])
        return total

    if n < 4:
        return route, route_distance(route)

    best = list(route)
    best_dist = route_distance(best)

    for _ in range(max_iterations):
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                new_route = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                new_dist = route_distance(new_route)
                if new_dist < best_dist - 0.01:
                    best = new_route
                    best_dist = new_dist
                    improved = True
        if not improved:
            break

    return best, best_dist
