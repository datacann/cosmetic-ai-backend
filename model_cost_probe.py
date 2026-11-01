import json
import os

from app.models.compatibility_analysis import (
    AnalyzeCompatibilityRequest,
    CompatibilityIngredient,
    CompatibilityProduct,
    CompatibilitySkinProfile,
)
from app.services.analysis_metrics import AnalysisMetricsCollector
from app.services.compatibility_analysis import analyze_compatibility
from app.services.cosing_cache import lookup_cosing_ingredients
from app.services.open_beauty_facts import lookup_open_beauty_facts


external = lookup_open_beauty_facts("3560070791460")
knowledge = lookup_cosing_ingredients(external.acquisition.ingredients)
request = AnalyzeCompatibilityRequest(
    product=CompatibilityProduct(
        brand=external.product.brand,
        product_name=external.product.product_name,
        category=external.product.category,
    ),
    ingredients=[
        CompatibilityIngredient(
            input_name=item.input_name,
            canonical_name=item.canonical_name,
            functions=item.functions,
            flags=item.flags,
            evidence_status=item.evidence_status,
        )
        for item in knowledge.values()
    ],
    skin_profile=CompatibilitySkinProfile(
        skin_type="Karma",
        sensitivity="Biraz hassas",
        goals=["Nem desteği", "Cilt bariyeri"],
    ),
    routine=[],
)

results = []
for model in ("gpt-5-nano", "gpt-5.4-nano", "gpt-5.6-luna"):
    os.environ["OPENAI_COMPATIBILITY_MODEL"] = model
    collector = AnalysisMetricsCollector(request_key=model, flow_type="model_probe")
    try:
        response = analyze_compatibility(request)
        results.append({
            "model": model,
            "ok": True,
            "assessments": {
                name: getattr(response.compatibility, name).assessment
                for name in ("skin_type", "sensitivity", "goals", "appearance", "routine")
            },
            "usage": collector.model_usage(),
        })
    except Exception as exc:
        results.append({"model": model, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
print(json.dumps(results, ensure_ascii=False))
