from kompas.modules.knowledge.scales import ScaleDefinition, visible_items


def complete_answers(
    definition: ScaleDefinition, overrides: dict[int | str, int] | None = None, *, maximum: bool = False
) -> dict[int | str, int]:
    """Fill the visible form with valid boundary answers, retaining explicit scenario answers."""
    answers = dict(overrides or {})
    for item in definition.items:
        if item not in visible_items(definition, answers) or item.id in answers:
            continue
        if item.input:
            answers[item.id] = item.max if maximum and item.max is not None else item.min
        else:
            answers[item.id] = (max if maximum else min)(o.value for o in item.options)
    return answers
