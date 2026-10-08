def load_locomo(
    cache_path: Optional[str] = None,
    limit: int = 3,
    max_queries_per_conversation: int = 20,
) -> List[Scenario]:
    """Load the first ``limit`` conversations as Scenarios."""
    path = cache_path or cache_path_default()
    if not os.path.exists(path):
        raise RuntimeError(
            "LoCoMo cache not found at %s - run download() first (needs network once)" % path
        )
    with open(path, "r", encoding="utf-8") as handle:
        records = json.load(handle)
    if not isinstance(records, list):
        raise RuntimeError("unexpected LoCoMo payload: top level is %s" % type(records).__name__)
    scenarios = []
    for index, record in enumerate(records[: max(0, limit)]):
        if isinstance(record, dict):
            scenario = convert(record, index, max_queries_per_conversation)
            if scenario.queries:
                scenarios.append(scenario)
    return scenarios
