from app.database.repository import normalize_text
from app.models.ingredient_normalization import (
    NormalizeIngredientsRequest,
    RawIngredientFormula,
)
from app.models.user_context import (
    BuildUserContextRequest,
    ContextIngredientIdentity,
    ContextRoutineProduct,
    UserContextResponse,
    UserPreferenceContext,
    UserRoutineContext,
    UserSkinContext,
)
from app.services.ingredient_normalization import normalize_ingredients


SKIN_TYPES = {
    "yagli": "oily", "oily": "oily",
    "kuru": "dry", "dry": "dry",
    "karma": "combination", "combination": "combination",
    "normal": "normal",
    "bilmiyorum": "unknown", "unknown": "unknown",
}
SENSITIVITIES = {
    "hassas-degil": "not_sensitive", "not-sensitive": "not_sensitive",
    "not_sensitive": "not_sensitive",
    "biraz-hassas": "somewhat_sensitive", "somewhat-sensitive": "somewhat_sensitive",
    "somewhat_sensitive": "somewhat_sensitive",
    "cok-hassas": "very_sensitive", "very-sensitive": "very_sensitive",
    "very_sensitive": "very_sensitive",
    "bilmiyorum": "unknown", "unknown": "unknown",
}
APPEARANCES = {
    "kuruluk-gorunumu": "dryness", "dryness": "dryness",
    "pullanma": "flaking", "flaking": "flaking",
    "yagli-parlak-gorunum": "oily_or_shiny", "oily-or-shiny": "oily_or_shiny",
    "oily_or_shiny": "oily_or_shiny",
    "kizariklik-gorunumu": "redness", "redness": "redness",
    "sivilce-kusur-gorunumu": "blemish_appearance",
    "blemish-appearance": "blemish_appearance", "blemish_appearance": "blemish_appearance",
    "leke-ton-esitsizligi": "uneven_tone", "uneven-tone": "uneven_tone",
    "uneven_tone": "uneven_tone",
    "belirgin-gozenek-gorunumu": "visible_pores", "visible-pores": "visible_pores",
    "visible_pores": "visible_pores",
    "puruzlu-duzensiz-doku": "rough_or_uneven_texture",
    "rough-or-uneven-texture": "rough_or_uneven_texture",
    "rough_or_uneven_texture": "rough_or_uneven_texture",
    "gerginlik-hissi": "tightness", "tightness": "tightness",
    "hicbiri": "none", "none": "none",
    "emin-degilim": "unknown", "unknown": "unknown",
}
GOALS = {
    "sivilce-kusur-gorunumu": "blemish_appearance",
    "blemish-appearance": "blemish_appearance", "blemish_appearance": "blemish_appearance",
    "leke-ton-esitsizligi": "uneven_tone", "uneven-tone": "uneven_tone",
    "uneven_tone": "uneven_tone",
    "kizariklik-gorunumu": "redness", "redness": "redness",
    "gozenek-gorunumu": "visible_pores", "visible-pores": "visible_pores",
    "visible_pores": "visible_pores",
    "kuruluk-nem": "hydration", "hydration": "hydration",
    "yaglanma-parlama": "oil_control", "oil-control": "oil_control",
    "oil_control": "oil_control",
    "cilt-dokusu": "texture", "texture": "texture",
    "ince-cizgi-gorunumu": "fine_lines_appearance",
    "fine-lines-appearance": "fine_lines_appearance",
    "fine_lines_appearance": "fine_lines_appearance",
    "genel-bakim": "general_care", "general-care": "general_care",
    "general_care": "general_care",
}
NO_AVOID_PREFERENCE = {
    "bilmiyorum-tercihim-yok", "tercihim-yok", "none", "unknown",
}


def build_user_context(request: BuildUserContextRequest) -> UserContextResponse:
    skin_type = _single_value(request.profile.skin_type, SKIN_TYPES)
    sensitivity = _single_value(request.profile.sensitivity, SENSITIVITIES)
    appearance = _appearance_values(request.profile.appearance)
    goals = _many_values(request.profile.goals, GOALS)
    avoid = _normalize_names([
        value for value in request.profile.avoid_ingredients
        if normalize_text(value) not in NO_AVOID_PREFERENCE
    ])

    morning: list[ContextRoutineProduct] = []
    evening: list[ContextRoutineProduct] = []
    for item in request.routine:
        ingredients = _routine_ingredients(item.ingredients)
        product = ContextRoutineProduct(
            brand=item.brand,
            name=item.name,
            category=item.category,
            ingredients=ingredients,
        )
        if item.routine_time in {"morning", "both"}:
            morning.append(product)
        if item.routine_time in {"evening", "both"}:
            evening.append(product.model_copy(deep=True))

    known = []
    unknown = []
    availability = {
        "skin.skin_type": skin_type != "unknown",
        "skin.sensitivity": sensitivity != "unknown",
        "skin.appearance": bool(appearance) and appearance != ["unknown"],
        "skin.goals": bool(goals),
        "preferences.avoid_ingredients": bool(request.profile.avoid_ingredients),
        "routine": bool(request.routine),
    }
    for field, available in availability.items():
        (known if available else unknown).append(field)

    return UserContextResponse(
        skin=UserSkinContext(
            skin_type=skin_type,
            sensitivity=sensitivity,
            appearance=appearance or ["unknown"],
            goals=goals,
        ),
        preferences=UserPreferenceContext(avoid_ingredients=avoid),
        routine=UserRoutineContext(morning=morning, evening=evening),
        known_fields=known,
        unknown_fields=unknown,
    )


def _single_value(value: str | None, mapping: dict[str, str]) -> str:
    return mapping.get(normalize_text(value), "unknown")


def _many_values(values: list[str], mapping: dict[str, str]) -> list[str]:
    return list(dict.fromkeys(
        mapped for value in values if (mapped := mapping.get(normalize_text(value)))
    ))


def _appearance_values(values: list[str]) -> list[str]:
    mapped = _many_values(values, APPEARANCES)
    concrete = [value for value in mapped if value not in {"none", "unknown"}]
    if concrete:
        return concrete
    if "none" in mapped:
        return ["none"]
    if "unknown" in mapped:
        return ["unknown"]
    return []


def _normalize_names(values: list[str]) -> list[ContextIngredientIdentity]:
    if not values:
        return []
    normalized = normalize_ingredients(NormalizeIngredientsRequest(
        provenance="manual",
        formulas=[RawIngredientFormula(name=None, ingredients=values)],
    ))
    return [
        ContextIngredientIdentity(
            raw_name=item.raw_name,
            canonical_name=item.canonical_name,
        )
        for item in normalized.formulas[0].ingredients
    ]


def _routine_ingredients(values) -> list[ContextIngredientIdentity]:
    result: list[ContextIngredientIdentity] = []
    unresolved = []
    unresolved_indexes = []
    for value in values:
        if value.canonical_name:
            result.append(ContextIngredientIdentity(
                raw_name=value.raw_name,
                canonical_name=value.canonical_name,
            ))
        else:
            unresolved_indexes.append(len(result))
            unresolved.append(value.raw_name)
            result.append(ContextIngredientIdentity(raw_name=value.raw_name, canonical_name=None))
    locally_normalized = _normalize_names(unresolved)
    for index, item in zip(unresolved_indexes, locally_normalized, strict=True):
        result[index] = item
    return result
