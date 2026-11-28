"""Outils de graphe : regroupe des segments isolés en chaînes (polylignes)."""
from __future__ import annotations

from collections import defaultdict
from typing import Hashable, Iterable, List, Tuple


def edges_to_chains(edges: Iterable[Tuple[Hashable, Hashable]]) -> List[List[Hashable]]:
    """Décompose un ensemble d'arêtes en chaînes de nœuds.

    Chaque arête est utilisée exactement une fois (doublons et boucles triviales
    supprimés). Moins d'objets graphiques à dessiner qu'un segment par visée.
    """
    seen = set()
    unique: List[Tuple[Hashable, Hashable]] = []
    for a, b in edges:
        if a == b:
            continue
        key = frozenset((a, b))
        if key in seen:
            continue
        seen.add(key)
        unique.append((a, b))

    adjacency = defaultdict(list)
    for i, (a, b) in enumerate(unique):
        adjacency[a].append(i)
        adjacency[b].append(i)

    used = [False] * len(unique)

    def extend(node: Hashable) -> List[Hashable]:
        out: List[Hashable] = []
        while True:
            nxt = None
            for edge_index in adjacency[node]:
                if not used[edge_index]:
                    nxt = edge_index
                    break
            if nxt is None:
                return out
            used[nxt] = True
            a, b = unique[nxt]
            node = b if a == node else a
            out.append(node)

    chains: List[List[Hashable]] = []
    for i, (a, b) in enumerate(unique):
        if used[i]:
            continue
        used[i] = True
        forward = extend(b)
        backward = extend(a)
        chains.append(list(reversed(backward)) + [a, b] + forward)
    return chains
