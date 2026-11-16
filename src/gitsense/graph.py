"""Lay out a commit list as a lane graph.

- Input rows are in display order (children before parents).
- Each row gets a column, a color index and the edge segments to draw in it.
- Each edge segment also says which commits it connects, so the page can
  highlight the lines between a commit and its ancestors or descendants.
"""

from __future__ import annotations

from typing import Any

# An edge is [from_col, to_col, color, kind, parent_row, child_rows].
# - parent_row is the row number of the commit the line runs down to
# - parent_row is NO_ROW when that commit is not in the list
# - child_rows are the row numbers of the commits the line comes down from
NO_ROW = -1

# Edge kinds.
TOP = 0  # from the row's top edge at from_col down to the node at to_col
BOTTOM = 1  # from the node at from_col down to the row's bottom edge at to_col
THROUGH = 2  # straight through the row at from_col (no node contact)


def _first_free(lanes: list[Any]) -> int:
    for i, lane in enumerate(lanes):
        if lane is None:
            return i
    lanes.append(None)
    return len(lanes) - 1


def layout(rows: list[dict[str, Any]]) -> int:
    """Add 'col', 'color' and 'edges' to every row. Returns the widest column count."""
    # A lane is (sha it is waiting for, color, rows of the children that feed it).
    lanes: list[tuple[str, int, tuple[int, ...]] | None] = []
    unresolved: dict[str, list[list[Any]]] = {}  # edges waiting to learn their parent's row
    next_color = 0
    width = 0

    for at, row in enumerate(rows):
        sha = row["sha"]
        for edge in unresolved.pop(sha, ()):
            edge[4] = at
        before = list(lanes)
        waiting = [i for i, lane in enumerate(lanes) if lane is not None and lane[0] == sha]
        edges: list[list[Any]] = []

        if waiting:
            col = waiting[0]
            color = lanes[col][1]  # type: ignore[index]
        else:
            col = _first_free(lanes)
            color = next_color
            next_color += 1

        # Every lane waiting for this commit ends at its node.
        for i in waiting:
            edges.append([i, col, lanes[i][1], TOP, at, list(lanes[i][2])])  # type: ignore[index]
            lanes[i] = None

        for n, parent in enumerate(row.get("parents") or []):
            existing = next(
                (i for i, lane in enumerate(lanes) if lane is not None and lane[0] == parent),
                None,
            )
            if existing is not None:
                # The parent already has a lane: join it.
                _, lane_color, feeders = lanes[existing]  # type: ignore[misc]
                edge = [col, existing, color if n == 0 else lane_color, BOTTOM, NO_ROW, [at]]
                lanes[existing] = (parent, lane_color, feeders + (at,))
            else:
                if n == 0:
                    target, parent_color = col, color
                else:
                    target = _first_free(lanes)
                    parent_color = next_color
                    next_color += 1
                lanes[target] = (parent, parent_color, (at,))
                edge = [col, target, parent_color, BOTTOM, NO_ROW, [at]]
            edges.append(edge)
            unresolved.setdefault(parent, []).append(edge)

        for i, lane in enumerate(before):
            if lane is not None and lane[0] != sha:
                edge = [i, i, lane[1], THROUGH, NO_ROW, list(lane[2])]
                edges.append(edge)
                unresolved.setdefault(lane[0], []).append(edge)

        while lanes and lanes[-1] is None:
            lanes.pop()

        row["col"] = col
        row["color"] = color
        row["edges"] = edges
        width = max(width, len(before), len(lanes), col + 1)

    return width
