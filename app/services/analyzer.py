from app.models.analyze import (
    AnalyzeProductRequest,
    AnalyzeProductResponse,
    ProductIdentity,
    CompatibilityBreakdown,
)


def analyze_product(
    request: AnalyzeProductRequest,
) -> AnalyzeProductResponse:

    return AnalyzeProductResponse(
        product=ProductIdentity(
            brand="CeraVe",
            product_name="SA Smoothing Cleanser",
            category="Cleanser",
            confidence=0.96,
        ),
        ingredients=[
            "Water",
            "Glycerin",
            "Salicylic Acid",
            "Niacinamide",
        ],
        score=86,
        verdict="Çok Uygun",
        compatibility=CompatibilityBreakdown(
            skin_type=90,
            sensitivity=72,
            goals=92,
            appearance=84,
            routine=None,
        ),
        strengths=[
            "Bakım hedeflerinle iyi eşleşiyor.",
            "Nem desteği sağlayan içerikler bulunuyor.",
        ],
        cautions=[
            "Hassas ciltlerde salisilik asit dikkatli kullanılabilir.",
        ],
        recommended_usage="Akşam rutininde kullanılabilir.",
    )