"""
Repair search for the BFS + beam search + GA hybrid (paper §V).

  view         GraphView: a read-only overlay of candidate outputs on a GraphStore
  constraints  validate_plan: the hard constraints a repaired plan must meet
  objective    the four-term objective and the feasibility-first (Deb) ordering

The existing Replanner (E1, B1, B2) does not use this package.
"""
