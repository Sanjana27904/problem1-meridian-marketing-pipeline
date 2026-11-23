"""Deduplication: 'latest version wins'."""


def latest_wins(items, key_fn, order_fn):
    """Keep one item per key: the one with the biggest order_fn(item).

    If two items tie, the one that arrived later in the list wins.
    Returns (winners, losers). Nothing is lost silently: the caller counts losers.
    """
    best = {}
    losers = []
    for idx, item in enumerate(items):
        key = key_fn(item)
        rank = (order_fn(item), idx)
        if key not in best:
            best[key] = (rank, item)
        elif rank >= best[key][0]:
            losers.append(best[key][1])
            best[key] = (rank, item)
        else:
            losers.append(item)
    return [v[1] for v in best.values()], losers
