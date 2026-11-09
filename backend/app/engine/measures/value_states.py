"""Mixed is distinct from missing; ordered selectors carry the selected cell's state."""


def mixed_group(method, value, state, ordering=None):
    if method in {"first", "last"}:
        function = "argMinIf" if method == "first" else "argMaxIf"
        eligible = f"(isNotNull({value}) OR ({state})=2)"
        return f"tupleElement({function}(tuple({state}),{ordering},{eligible}),1)=2"
    result = f"max({state})=2"
    if method == "single_value":
        result += f" OR uniqExact({value})>1"
    return result
