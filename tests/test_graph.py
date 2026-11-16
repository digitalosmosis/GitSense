"""Tests for graph.layout, the commit lane layout."""

from __future__ import annotations

from gitsense import graph
from gitsense.graph import BOTTOM, NO_ROW, THROUGH, TOP


def rows_from(spec: list[tuple[str, list[str]]]) -> list[dict]:
    return [{"sha": sha, "parents": list(parents)} for sha, parents in spec]


def shapes(edges: list[list]) -> list[list]:
    """Edges without the commits they connect: [from_col, to_col, color, kind]."""
    return [e[:4] for e in edges]


def check_links(rows: list[dict]) -> None:
    """Rules for the commits each edge says it connects.

    - A TOP edge runs down to its own row and a BOTTOM edge comes from its own row.
    - Every child an edge names really has the edge's parent as a parent.
    - An edge has no parent row only when that parent is not in the list.
    - Every child to parent link between listed commits has a BOTTOM and a TOP edge.
    """
    at = {row["sha"]: i for i, row in enumerate(rows)}
    seen: set[tuple[int, int, int]] = set()
    for r, row in enumerate(rows):
        for _frm, _to, _color, kind, parent, children in row["edges"]:
            assert children and all(0 <= c <= r for c in children)
            if kind == TOP:
                assert parent == r
            elif kind == BOTTOM:
                assert children == [r]
            if parent == NO_ROW:
                targets = [set(rows[c]["parents"]) - set(at) for c in children]
                assert set.intersection(*targets)
                continue
            assert parent >= r
            for c in children:
                assert rows[parent]["sha"] in rows[c]["parents"]
                seen.add((kind, c, parent))
    for c, row in enumerate(rows):
        for sha in row["parents"]:
            if sha in at and at[sha] > c:
                assert (BOTTOM, c, at[sha]) in seen
                assert (TOP, c, at[sha]) in seen


def check_invariants(rows: list[dict], width: int) -> None:
    """Structural rules every layout must satisfy.

    - A BOTTOM edge into lane X is continued in the next row by TOP or THROUGH at X.
    - A TOP or THROUGH edge at lane X is fed by BOTTOM or THROUGH at X in the row above.
    - TOP edges end at the row's node and BOTTOM edges start at it.
    - No two lanes wait in the same column at once.
    - Every column is inside the reported width.
    """
    for r, row in enumerate(rows):
        col = row["col"]
        assert 0 <= col < width
        for frm, to, color, kind in shapes(row["edges"]):
            assert 0 <= frm < width and 0 <= to < width
            assert isinstance(color, int)
            if kind == TOP:
                assert to == col
            elif kind == BOTTOM:
                assert frm == col
            else:
                assert kind == THROUGH and frm == to and frm != col

        leaving = [e[1] for e in row["edges"] if e[3] in (BOTTOM, THROUGH)]
        throughs = [e[0] for e in row["edges"] if e[3] == THROUGH]
        assert len(throughs) == len(set(throughs))

        if r + 1 < len(rows):
            nxt = rows[r + 1]
            entering = {e[0] for e in nxt["edges"] if e[3] in (TOP, THROUGH)}
            assert set(leaving) == entering, f"row {r} -> {r + 1}"
        else:
            # Only lanes for parents missing from the list may run off the end.
            shown = {x["sha"] for x in rows}
            dangling = [p for x in rows for p in x["parents"] if p not in shown]
            if not dangling:
                assert leaving == []

    if rows:
        first_entering = [e for e in rows[0]["edges"] if e[3] in (TOP, THROUGH)]
        assert first_entering == []
    check_links(rows)


def test_empty_list():
    assert graph.layout([]) == 0


def test_linear_history_stays_in_one_lane():
    rows = rows_from([("c", ["b"]), ("b", ["a"]), ("a", [])])
    width = graph.layout(rows)
    assert width == 1
    assert [r["col"] for r in rows] == [0, 0, 0]
    assert len({r["color"] for r in rows}) == 1
    assert shapes(rows[0]["edges"]) == [[0, 0, 0, BOTTOM]]
    assert shapes(rows[1]["edges"]) == [[0, 0, 0, TOP], [0, 0, 0, BOTTOM]]
    assert shapes(rows[2]["edges"]) == [[0, 0, 0, TOP]]
    check_invariants(rows, width)


def test_branch_and_merge():
    # m merges f2 into main; main has its own commit m1 since the fork at base.
    rows = rows_from(
        [
            ("m", ["m1", "f2"]),
            ("f2", ["f1"]),
            ("m1", ["base"]),
            ("f1", ["base"]),
            ("base", []),
        ]
    )
    width = graph.layout(rows)
    assert width == 2
    by = {r["sha"]: r for r in rows}
    assert by["m"]["col"] == 0
    assert by["f2"]["col"] == 1
    assert by["f1"]["col"] == 1
    assert by["m1"]["col"] == 0
    assert by["base"]["col"] == 0
    # The merge row opens a second lane with a new color.
    assert shapes([e for e in by["m"]["edges"] if e[3] == BOTTOM]) == [[0, 0, 0, BOTTOM], [0, 1, 1, BOTTOM]]
    # The feature lane joins main's lane at f1, because base already has a lane.
    assert [1, 0, 1, BOTTOM] in shapes(by["f1"]["edges"])
    assert shapes(by["base"]["edges"]) == [[0, 0, 0, TOP]]
    check_invariants(rows, width)


def test_multiple_children_converge_on_one_parent():
    rows = rows_from([("x", ["base"]), ("y", ["base"]), ("z", ["base"]), ("base", [])])
    width = graph.layout(rows)
    # Later children join the lane already waiting for base, so column 1 is reused.
    assert width == 2
    assert [r["col"] for r in rows] == [0, 1, 1, 0]
    assert [1, 0, rows[1]["color"], BOTTOM] in shapes(rows[1]["edges"])
    assert [1, 0, rows[2]["color"], BOTTOM] in shapes(rows[2]["edges"])
    assert shapes(rows[3]["edges"]) == [[0, 0, 0, TOP]]
    assert len({rows[0]["color"], rows[1]["color"], rows[2]["color"]}) == 3
    check_invariants(rows, width)


def test_octopus_merge_opens_one_lane_per_extra_parent():
    rows = rows_from(
        [
            ("o", ["p1", "p2", "p3"]),
            ("p3", ["base"]),
            ("p2", ["base"]),
            ("p1", ["base"]),
            ("base", []),
        ]
    )
    width = graph.layout(rows)
    assert width == 3
    bottoms = [e for e in rows[0]["edges"] if e[3] == BOTTOM]
    assert [e[1] for e in bottoms] == [0, 1, 2]
    assert len({e[2] for e in bottoms}) == 3
    by = {r["sha"]: r for r in rows}
    assert (by["p1"]["col"], by["p2"]["col"], by["p3"]["col"]) == (0, 1, 2)
    check_invariants(rows, width)


def test_wip_row_sits_on_top_of_head_lane():
    rows = rows_from([("WIP", ["c2"]), ("c2", ["c1"]), ("c1", [])])
    width = graph.layout(rows)
    assert width == 1
    assert rows[0]["col"] == rows[1]["col"] == 0
    assert rows[0]["color"] == rows[1]["color"]
    check_invariants(rows, width)


def test_wip_on_unborn_head_has_no_edges():
    rows = rows_from([("WIP", [])])
    assert graph.layout(rows) == 1
    assert rows[0]["edges"] == []


def test_freed_lane_is_reused():
    # Lane 1 is freed when side1 joins main; side2 later takes it again.
    rows = rows_from(
        [
            ("m2", ["m1", "side1"]),
            ("side1", ["m1"]),
            ("m1", ["m0"]),
            ("side2", ["m0"]),
            ("m0", []),
        ]
    )
    width = graph.layout(rows)
    assert width == 2
    by = {r["sha"]: r for r in rows}
    assert by["side1"]["col"] == 1
    assert by["side2"]["col"] == 1
    check_invariants(rows, width)


def test_through_edges_keep_their_color():
    rows = rows_from([("a", ["base"]), ("c", ["b"]), ("b", ["base"]), ("base", [])])
    # "c" opens a second lane while a's lane passes through c and b.
    width = graph.layout(rows)
    a_color = rows[0]["color"]
    for row in rows[1:3]:
        through = [e for e in row["edges"] if e[3] == THROUGH and e[0] == 0]
        assert shapes(through) == [[0, 0, a_color, THROUGH]]
    check_invariants(rows, width)


def test_missing_parent_lane_runs_to_the_end():
    rows = rows_from([("c", ["b"]), ("b", ["not-loaded"])])
    width = graph.layout(rows)
    assert width == 1
    assert [0, 0, 0, BOTTOM] in shapes(rows[1]["edges"])


def test_first_parent_joining_an_existing_lane_frees_the_column():
    # Both x and y have first parent "base"; y joins x's lane, so its own column is free.
    rows = rows_from([("x", ["base"]), ("y", ["base"]), ("z", ["q"]), ("q", []), ("base", [])])
    width = graph.layout(rows)
    by = {r["sha"]: r for r in rows}
    assert by["y"]["col"] == 1
    assert [1, 0, by["y"]["color"], BOTTOM] in shapes(by["y"]["edges"])
    assert by["z"]["col"] == 1
    check_invariants(rows, width)


def test_layout_of_a_large_random_dag_keeps_invariants():
    import random

    rng = random.Random(1234)
    shas = [f"c{i}" for i in range(300)]
    spec = []
    # Rows are children before parents, so parents are always later in the list.
    for i, sha in enumerate(shas):
        later = shas[i + 1 :]
        if not later:
            spec.append((sha, []))
            continue
        count = rng.choice([1, 1, 1, 1, 2, 2, 3])
        parents = rng.sample(later[:20], min(count, len(later[:20])))
        spec.append((sha, parents))
    rows = rows_from(spec)
    width = graph.layout(rows)
    check_invariants(rows, width)
    # A node never shares its column with a lane passing through it.
    for row in rows:
        assert all(e[0] != row["col"] for e in row["edges"] if e[3] == THROUGH)


def test_edges_name_the_commits_they_connect():
    rows = rows_from([("c", ["b"]), ("b", ["a"]), ("a", [])])
    graph.layout(rows)
    assert rows[0]["edges"] == [[0, 0, 0, BOTTOM, 1, [0]]]
    assert rows[1]["edges"] == [[0, 0, 0, TOP, 1, [0]], [0, 0, 0, BOTTOM, 2, [1]]]
    assert rows[2]["edges"] == [[0, 0, 0, TOP, 2, [1]]]


def test_a_shared_lane_names_every_child_that_joined_it():
    rows = rows_from([("x", ["base"]), ("y", ["base"]), ("z", ["base"]), ("base", [])])
    graph.layout(rows)
    # x's lane to base passes y alone, then carries y as well, then z.
    assert [e[5] for e in rows[1]["edges"] if e[3] == THROUGH] == [[0]]
    assert [e[5] for e in rows[2]["edges"] if e[3] == THROUGH] == [[0, 1]]
    assert rows[3]["edges"] == [[0, 0, 0, TOP, 3, [0, 1, 2]]]


def test_a_line_to_a_commit_outside_the_list_has_no_parent_row():
    rows = rows_from([("c", ["b"]), ("x", ["not-loaded"]), ("b", ["not-loaded"])])
    graph.layout(rows)
    assert [e[4] for e in rows[0]["edges"]] == [2]
    assert [e[4:] for e in rows[1]["edges"] if e[3] == BOTTOM] == [[NO_ROW, [1]]]
    assert [e[4:] for e in rows[2]["edges"] if e[3] == BOTTOM] == [[NO_ROW, [2]]]
