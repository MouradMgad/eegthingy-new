"""Exact cell-by-cell layouts used by the original robot experiments.

The experiment log maps maze numbers 1--4 to historical seeds 11--14.  The
full grid path is frozen (not merely its left/right labels), so the simulator,
classical models, deep models, and MIRepNet all show the identical geometry.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StandardMaze:
    number: int
    seed: int
    path: str

    @property
    def cells(self) -> tuple[tuple[int, int], ...]:
        vectors = {"E": (1, 0), "N": (0, 1), "W": (-1, 0), "S": (0, -1)}
        cells = [(0, 0)]
        for letter in self.path:
            dx, dy = vectors[letter]
            x, y = cells[-1]
            cells.append((x + dx, y + dy))
        return tuple(cells)

    @property
    def route(self) -> str:
        vectors = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(self.cells, self.cells[1:])]
        headings = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}
        route = []
        for before, after in zip(vectors, vectors[1:]):
            if before == after:
                continue
            old, new = headings[before], headings[after]
            route.append("L" if (old + 1) % 4 == new else "R")
        return "".join(route)

    @property
    def directions(self) -> tuple[str, ...]:
        return tuple("LEFT" if letter == "L" else "RIGHT" for letter in self.route)

    @property
    def labels(self) -> tuple[int, ...]:
        return tuple(1 if letter == "L" else 2 for letter in self.route)


STANDARD_MAZES = {
    1: StandardMaze(1, 11, "EEEENNENNNEENNNEESSESSEEESEENNNWWNNNWNNEENENNNWWWNNEENNNEEESEENEENNEENNNWNNEENENNW"),
    2: StandardMaze(2, 12, "EEEESSSESSWSSWSSWWNWWWNNEENEENNNWWWSWSWWSSSWWSSSESSESSWSSWWSSWSWWSSSESEESSSE"),
    3: StandardMaze(3, 13, "EEEEEESSWWSWWWNWWSWWSWSSSESSSEESSSWWWSSSEESEEENNNEENNENNEENNWWWNNNEEESEEENNWNNENEEENE"),
    4: StandardMaze(4, 14, "EEEENENEEENEEESSWSSSESSEENNENNENEENNNWNNNWNWNNNWNNNENNNEENNWNNEEENNENEESE"),
}


def get_standard_maze(number: int) -> StandardMaze:
    try:
        return STANDARD_MAZES[int(number)]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("standard maze must be 1, 2, 3, or 4") from error
