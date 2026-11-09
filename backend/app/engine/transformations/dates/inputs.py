def calendar_source_ids(objects, transaction):
    by_id = {o["objectId"]: o for o in objects}
    configs = [
        a["dateRange"]
        for a in (transaction["definition"].get("resolution") or {}).get("axes", [])
        if a.get("dateRange")
    ]
    if transaction["definition"].get("calendarResolution"):
        configs.append(transaction["definition"]["calendarResolution"])
    return list(
        dict.fromkeys(
            by_id[c["memberCatalogId"]]["definition"]["sourceId"] for c in configs
        )
    )


def strict_date(expression):
    text = f"toString({expression})"
    parsed = f"toDate32OrNull({text})"
    return f"if(match({text},'^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$') AND toString({parsed})={text},{parsed},NULL)"
