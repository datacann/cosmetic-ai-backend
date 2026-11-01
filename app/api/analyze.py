import os
import time

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from uuid import uuid4
from pydantic import TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool

from app.models.full_analysis import BarcodeAnalysisRequest, BarcodeLookupResponse, CatalogAnalysisRequest, CatalogFormulaDetail, CatalogIngredientItem, CatalogProductDetail, CatalogProductItem, CatalogProductPage, FullProductAnalysisResponse, RewardedAnalysisClaim, UsageStatusResponse
from app.api.dependencies import CurrentUser, DatabaseSession, OptionalCurrentUser
from app.database.auth_repository import AuthRepository
from app.models.product_recognition import ProductRecognitionResponse
from app.models.ingredient_acquisition import IngredientAcquisitionResponse
from app.models.ingredient_normalization import (
    NormalizeIngredientsRequest,
    NormalizeIngredientsResponse,
)
from app.models.ingredient_knowledge import IngredientKnowledgeResponse
from app.models.user_context import BuildUserContextRequest, UserContextResponse
from app.models.compatibility_assessment import (
    AnalyzeContextCompatibilityRequest,
    AnalyzeContextCompatibilityResponse,
)
from app.models.suitability_scoring import (
    CalculateSuitabilityScoreRequest,
    SuitabilityScoreResponse,
)
from app.models.analysis_orchestration import EndToEndAnalysisResponse
from app.models.ingredient_normalization import RawIngredientFormula
from app.models.ingredient_analysis import (
    AnalyzeIngredientsRequest,
    AnalyzeIngredientsResponse,
)
from app.models.compatibility_analysis import (
    AnalyzeCompatibilityRequest,
    AnalyzeCompatibilityResponse,
)
from app.models.compatibility_scoring import (
    ScoreCompatibilityRequest,
    ScoreCompatibilityResponse,
)
from app.models.compatibility_analysis import (
    CompatibilityRoutineProduct,
    CompatibilitySkinProfile,
)
from app.services.ingredient_acquisition import (
    IngredientAcquisitionConfigurationError,
    IngredientAcquisitionUnavailableError,
    acquire_ingredients as acquire_ingredients_service,
)
from app.services.ingredient_analysis import (
    IngredientAnalysisConfigurationError,
    IngredientAnalysisUnavailableError,
    analyze_ingredients as analyze_ingredients_service,
)
from app.services.compatibility_analysis import (
    CompatibilityAnalysisConfigurationError,
    CompatibilityAnalysisUnavailableError,
    analyze_compatibility as analyze_compatibility_service,
)
from app.services.compatibility_scoring import (
    ScoringInsufficientDataError,
    score_compatibility as score_compatibility_service,
)
from app.services.product_recognition import (
    ProductRecognitionConfigurationError,
    ProductRecognitionUnavailableError,
    recognize_product as recognize_product_service,
)
from app.services.analysis_persistence import (
    BarcodeProductNotFoundError,
    CatalogProductNotFoundError,
    DatabaseUnavailableError,
    run_and_persist_barcode_analysis,
    run_and_persist_catalog_analysis,
    run_and_persist_full_analysis,
)
from app.database.repository import CosmeticRepository, normalize_barcode
from app.services.analysis_progress import claim_progress, get_progress, update_progress
from app.services.usage_limits import ConcurrentAnalysisError, RewardedAnalysisUnavailableError, UsageLimitExceededError, finish_usage_event, grant_rewarded_analysis, guest_key_for_ip, reserve_vision_analysis, usage_status
from app.services.analysis_metrics import AnalysisMetricsCollector, save_analysis_metric
from app.services.open_beauty_facts import lookup_open_beauty_facts
from app.services.cosing_cache import lookup_cosing_ingredients, resolve_ingredient_knowledge
from app.services.ingredient_normalization import normalize_ingredients
from app.services.ingredient_knowledge import attach_ingredient_knowledge
from app.services.user_context import build_user_context
from app.services.compatibility_assessment import (
    CompatibilityAssessmentConfigurationError,
    CompatibilityAssessmentUnavailableError,
    analyze_context_compatibility,
)
from app.services.suitability_scoring import (
    SuitabilityScoringInsufficientDataError,
    calculate_suitability_score,
)
from app.services.admob_ssv import AdMobVerificationError, verify_callback_query
from app.services.analysis_orchestration import (
    AnalysisPipelineDependencies,
    run_end_to_end_analysis,
)


router = APIRouter(
    prefix="/v1",
    tags=["Analysis"],
)


@router.post("/build-user-context", response_model=UserContextResponse)
def build_user_context_endpoint(request: BuildUserContextRequest):
    return build_user_context(request)


@router.post(
    "/analyze-context-compatibility",
    response_model=AnalyzeContextCompatibilityResponse,
)
async def analyze_context_compatibility_endpoint(
    request: AnalyzeContextCompatibilityRequest,
):
    try:
        return await run_in_threadpool(analyze_context_compatibility, request)
    except CompatibilityAssessmentConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except CompatibilityAssessmentUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/calculate-score", response_model=SuitabilityScoreResponse)
def calculate_score_endpoint(request: CalculateSuitabilityScoreRequest):
    try:
        return calculate_suitability_score(request)
    except SuitabilityScoringInsufficientDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 25 * 1024 * 1024


@router.post("/analyze", response_model=EndToEndAnalysisResponse)
async def end_to_end_analysis_endpoint(
    request: Request,
    current_user: OptionalCurrentUser,
    session: DatabaseSession,
    product_image: UploadFile = File(...),
    ingredient_images: list[UploadFile] | None = File(default=None),
    manual_ingredients: str = Form(default="[]"),
    profile: str = Form(default="{}"),
    routine: str = Form(default="[]"),
    market: str = Form(default="TR"),
    allow_online_fallback: bool = Form(default=False),
    allow_knowledge_research: bool = Form(default=False),
):
    """Canonical product-to-personalized-result pipeline.

    Controlled pipeline outcomes are returned in the response body; only malformed
    multipart input is mapped to an HTTP 4xx response.
    """
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
    if product_image.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Desteklenmeyen ürün görseli formatı.")
    product_bytes = await product_image.read()
    if not product_bytes:
        raise HTTPException(status_code=400, detail="Ürün görseli boş.")
    if len(product_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Ürün görseli en fazla 10 MB olabilir.")
    uploads = ingredient_images or []
    if len(uploads) > 3:
        raise HTTPException(status_code=400, detail="En fazla 3 içerik görseli yüklenebilir.")
    ingredient_payloads = []
    for upload in uploads:
        if upload.content_type not in allowed_types:
            raise HTTPException(status_code=400, detail="Desteklenmeyen içerik görseli formatı.")
        content = await upload.read()
        if not content:
            raise HTTPException(status_code=400, detail="İçerik görsellerinden biri boş.")
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Her içerik görseli en fazla 10 MB olabilir.")
        ingredient_payloads.append((content, upload.content_type))
    if len(product_bytes) + sum(len(item[0]) for item in ingredient_payloads) > MAX_TOTAL_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Yüklenen görsellerin toplamı en fazla 25 MB olabilir.")
    try:
        manual = TypeAdapter(list[RawIngredientFormula]).validate_json(manual_ingredients)
        context_request = BuildUserContextRequest(
            profile=TypeAdapter(dict).validate_json(profile),
            routine=TypeAdapter(list).validate_json(routine),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Profil, rutin veya manuel içerik JSON verisi geçersiz.") from exc

    guest_key = None if current_user else guest_key_for_ip(request.client.host if request.client else "unknown")
    try:
        usage_event = reserve_vision_analysis(
            session, user=current_user, guest_key=guest_key, request_key=f"pipeline:{uuid4()}"
        )
    except UsageLimitExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ConcurrentAnalysisError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    repository = CosmeticRepository(session)
    dependencies = AnalysisPipelineDependencies(
        cached_acquisition=repository.find_cached_acquisition,
        acquisition_cache_writer=repository.save_product_acquisition,
        normalization_resolver=lambda **kwargs: resolve_ingredient_knowledge(repository, **kwargs),
        knowledge_cache_resolver=repository.find_cached_ingredient_items,
        cosing_resolver=lambda **kwargs: lookup_cosing_ingredients(kwargs["ingredients"]),
        knowledge_researcher=analyze_ingredients_service if allow_knowledge_research else None,
        knowledge_cache_writer=repository.save_cached_ingredient_items,
    )
    result = await run_in_threadpool(
        run_end_to_end_analysis,
        product_image=(product_bytes, product_image.content_type),
        ingredient_images=ingredient_payloads,
        manual_formulas=manual,
        user_request=context_request,
        market=market,
        allow_online_fallback=allow_online_fallback,
        dependencies=dependencies,
    )
    if result.outcome != "success":
        # A repository/cache failure may leave SQLAlchemy's transaction aborted.
        # Clear it before updating the independently committed usage reservation.
        session.rollback()
    if result.outcome == "success":
        try:
            result.analysis_id = repository.save_orchestrated_analysis(
                response=result,
                market=market,
                user_id=current_user.id if current_user else None,
            )
            if result.analysis_id is None:
                raise RuntimeError("analysis persistence returned no id")
        except Exception:
            session.rollback()
            result.outcome = "temporary_service_error"
            result.analysis_id = None
            result.personal_suitability = []
            result.summary = None
            result.diagnostics.indicators.append("analysis_persistence_failed")
    finish_usage_event(
        session,
        usage_event.id,
        charged=result.outcome == "success",
        status="completed" if result.outcome == "success" else "refunded",
    )
    return result


@router.post(
    "/analyze-product",
    response_model=FullProductAnalysisResponse,
)
async def analyze_product_endpoint(
    request: Request,
    current_user: OptionalCurrentUser,
    session: DatabaseSession,
    product_image: UploadFile = File(...),
    skin_profile: str | None = Form(default=None),
    routine: str = Form(default="[]"),
    market: str = Form(default="TR"),
    ingredient_images: list[UploadFile] | None = File(default=None),
    progress_id: str | None = Form(default=None),
    barcode: str | None = Form(default=None),
):
    allowed_types = {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    }
    if product_image.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Desteklenmeyen ürün görseli formatı.")
    product_bytes = await product_image.read()
    if not product_bytes:
        raise HTTPException(status_code=400, detail="Ürün görseli boş.")
    if len(product_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Ürün görseli en fazla 10 MB olabilir.")

    uploads = ingredient_images or []
    if len(uploads) > 3:
        raise HTTPException(status_code=400, detail="En fazla 3 içerik görseli yüklenebilir.")
    ingredient_payloads = []
    for image in uploads:
        if image.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Desteklenmeyen içerik görseli formatı: {image.content_type}",
            )
        image_bytes = await image.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="İçerik görsellerinden biri boş.")
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Her içerik görseli en fazla 10 MB olabilir.")
        ingredient_payloads.append((image_bytes, image.content_type))
    if len(product_bytes) + sum(len(payload[0]) for payload in ingredient_payloads) > MAX_TOTAL_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Yüklenen görsellerin toplamı en fazla 25 MB olabilir.")

    try:
        if skin_profile is not None:
            parsed_profile = CompatibilitySkinProfile.model_validate_json(skin_profile)
        elif current_user is not None:
            stored_profile = AuthRepository(session).get_skin_profile(current_user.id)
            if stored_profile is None:
                raise HTTPException(
                    status_code=400,
                    detail="Kayıtlı cilt profili bulunamadı; önce profil oluşturun.",
                )
            parsed_profile = CompatibilitySkinProfile(
                skin_type=stored_profile.skin_type,
                sensitivity=stored_profile.sensitivity,
                appearance=stored_profile.appearance,
                goals=stored_profile.goals,
                avoid_ingredients=stored_profile.avoid_ingredients,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="Giriş yapmadan analiz için skin_profile alanı gereklidir.",
            )
        parsed_routine = TypeAdapter(list[CompatibilityRoutineProduct]).validate_json(routine)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail="skin_profile veya routine JSON biçimi geçersiz.",
        ) from exc

    request_key = progress_id or f"analysis-{uuid4()}"
    metrics = AnalysisMetricsCollector(request_key=request_key, flow_type="vision")
    usage_event = None
    usage_finished = False
    try:
        if progress_id and (len(progress_id) > 100 or not progress_id.startswith("analysis-")):
            raise HTTPException(status_code=400, detail="Analiz takip kimliği geçersiz.")
        if not claim_progress(progress_id):
            raise HTTPException(status_code=409, detail="Bu analiz isteği zaten başlatılmış.")
        usage_event = reserve_vision_analysis(
            session,
            user=current_user,
            guest_key=None if current_user else guest_key_for_ip(request.client.host if request.client else "unknown"),
            request_key=request_key,
        )
        response = await run_in_threadpool(
            run_and_persist_full_analysis,
            product_image=(product_bytes, product_image.content_type),
            ingredient_images=ingredient_payloads,
            skin_profile=parsed_profile,
            routine=parsed_routine,
            market=market.strip().upper(),
            user_id=current_user.id if current_user else None,
            progress_callback=lambda stage: (metrics.stage(stage), update_progress(progress_id, stage)),
            barcode=barcode,
        )
        finish_usage_event(
            session,
            usage_event.id,
            charged=response.status in {"complete", "partial"},
            status="completed",
        )
        usage_finished = True
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status=response.status, response=response)
        update_progress(progress_id, "complete", "complete")
        return response
    except (UsageLimitExceededError, ConcurrentAnalysisError) as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="rejected", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (
        ProductRecognitionConfigurationError,
        IngredientAcquisitionConfigurationError,
        IngredientAnalysisConfigurationError,
        CompatibilityAnalysisConfigurationError,
    ) as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except (
        ProductRecognitionUnavailableError,
        IngredientAcquisitionUnavailableError,
        IngredientAnalysisUnavailableError,
        CompatibilityAnalysisUnavailableError,
    ) as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ScoringInsufficientDataError as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DatabaseUnavailableError as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        raise
    except Exception as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise
    finally:
        if usage_event is not None and not usage_finished:
            finish_usage_event(session, usage_event.id, charged=False, status="failed")


@router.get("/usage", response_model=UsageStatusResponse)
def get_usage_status(request: Request, current_user: OptionalCurrentUser, session: DatabaseSession):
    result = usage_status(
        session,
        user=current_user,
        guest_key=None if current_user else guest_key_for_ip(request.client.host if request.client else "unknown"),
    )
    return UsageStatusResponse(**{**result, "resets_at": result["resets_at"].isoformat()})


@router.post("/usage/rewarded", response_model=UsageStatusResponse)
def claim_rewarded_analysis(payload: RewardedAnalysisClaim, current_user: CurrentUser, session: DatabaseSession):
    if os.getenv("APP_ENV", "development").casefold() == "production":
        raise HTTPException(status_code=404, detail="Bulunamadı.")
    try:
        result = grant_rewarded_analysis(session, user=current_user, claim_id=payload.claim_id)
    except RewardedAnalysisUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UsageStatusResponse(**{**result, "resets_at": result["resets_at"].isoformat()})


@router.get("/ads/rewarded/ssv", status_code=200)
def admob_rewarded_callback(request: Request, session: DatabaseSession):
    params = request.query_params
    signature = params.get("signature", "")
    key_id = params.get("key_id", "")
    user_id = params.get("user_id", "")
    transaction_id = params.get("transaction_id", "")
    ad_unit = params.get("ad_unit", "")
    try:
        timestamp_ms = int(params.get("timestamp", "0"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Geçersiz AdMob zamanı.") from exc
    if not signature or not key_id or not user_id or not transaction_id:
        raise HTTPException(status_code=400, detail="Eksik AdMob callback alanı.")
    if abs(time.time() - timestamp_ms / 1000) > 86_400:
        raise HTTPException(status_code=400, detail="AdMob callback süresi geçersiz.")
    allowed_units = {value.strip() for value in os.getenv("ADMOB_REWARDED_AD_UNIT_IDS", "").split(",") if value.strip()}
    if allowed_units and ad_unit not in allowed_units:
        raise HTTPException(status_code=400, detail="AdMob reklam birimi tanınmıyor.")
    try:
        verify_callback_query(request.scope.get("query_string", b""), signature=signature, key_id=key_id)
    except AdMobVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user = AuthRepository(session).get_user(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı.")
    try:
        grant_rewarded_analysis(session, user=user, claim_id=transaction_id)
    except RewardedAnalysisUnavailableError as exc:
        # Returning 200 prevents Google from retrying a valid callback whose daily quota is already full.
        return {"received": True, "rewarded": False, "reason": str(exc)}
    return {"received": True, "rewarded": True}


@router.get("/products/barcode/{barcode}", response_model=BarcodeLookupResponse)
def lookup_product_by_barcode(barcode: str, session: DatabaseSession):
    normalized = normalize_barcode(barcode)
    if normalized is None:
        raise HTTPException(status_code=400, detail="Barkod 8–14 rakamdan oluşmalıdır.")
    product = CosmeticRepository(session).find_product_by_barcode(normalized)
    if product is None:
        external = lookup_open_beauty_facts(normalized)
        product = external.product if external else None
    return BarcodeLookupResponse(found=product is not None, barcode=normalized, product=product)


@router.get("/products", response_model=CatalogProductPage)
def list_catalog_products(
    session: DatabaseSession,
    q: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
):
    products, total, categories = CosmeticRepository(session).list_catalog_products(
        query=q, category=category, limit=limit, offset=offset
    )
    return CatalogProductPage(
        items=[
            CatalogProductItem(
                id=product.id,
                brand=product.brand,
                product_name=product.product_name,
                variant=product.variant,
                category=product.category,
                barcode=product.barcode,
                formula_count=len(product.formulas),
                ingredients_available=bool(product.formulas),
            )
            for product in products
        ],
        total=total,
        limit=limit,
        offset=offset,
        categories=categories,
    )


@router.get("/products/{product_id}", response_model=CatalogProductDetail)
def get_catalog_product(product_id: str, session: DatabaseSession):
    product = CosmeticRepository(session).get_catalog_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Ürün katalogda bulunamadı.")
    formulas = sorted(product.formulas, key=lambda item: item.formula_version, reverse=True)
    return CatalogProductDetail(
        id=product.id,
        brand=product.brand,
        product_name=product.product_name,
        variant=product.variant,
        category=product.category,
        barcode=product.barcode,
        market=product.market,
        updated_at=product.updated_at.isoformat(),
        formulas=[
            CatalogFormulaDetail(
                id=formula.id,
                name=formula.formula_name,
                version=formula.formula_version,
                status=formula.status,
                source_type=formula.source_type,
                source_url=formula.source_url,
                source_accessed_at=formula.source_accessed_at.isoformat() if formula.source_accessed_at else None,
                created_at=formula.created_at.isoformat(),
                ingredients=[
                    CatalogIngredientItem(
                        name=link.raw_name,
                        canonical_name=link.ingredient.inci_name,
                        functions=link.ingredient.functions,
                        flags=link.ingredient.flags,
                        verification_status=link.verification_status,
                    )
                    for link in sorted(formula.ingredient_links, key=lambda item: item.position)
                ],
            )
            for formula in formulas
        ],
    )
@router.post("/products/{product_id}/analyze", response_model=FullProductAnalysisResponse)
async def analyze_catalog_product(
    product_id: str,
    request: CatalogAnalysisRequest,
    current_user: OptionalCurrentUser,
    session: DatabaseSession,
):
    if request.skin_profile is not None:
        parsed_profile = request.skin_profile
    elif current_user is not None:
        stored_profile = AuthRepository(session).get_skin_profile(current_user.id)
        if stored_profile is None:
            raise HTTPException(status_code=400, detail="Kayıtlı cilt profili bulunamadı; önce profil oluşturun.")
        parsed_profile = CompatibilitySkinProfile(
            skin_type=stored_profile.skin_type,
            sensitivity=stored_profile.sensitivity,
            appearance=stored_profile.appearance,
            goals=stored_profile.goals,
            avoid_ingredients=stored_profile.avoid_ingredients,
        )
    else:
        raise HTTPException(status_code=400, detail="Giriş yapmadan analiz için skin_profile alanı gereklidir.")
    progress_id = request.progress_id
    if progress_id and (len(progress_id) > 100 or not progress_id.startswith("analysis-")):
        raise HTTPException(status_code=400, detail="Analiz takip kimliği geçersiz.")
    if not claim_progress(progress_id):
        raise HTTPException(status_code=409, detail="Bu analiz isteği zaten başlatılmış.")
    metrics = AnalysisMetricsCollector(
        request_key=progress_id or f"analysis-{uuid4()}", flow_type="catalog"
    )
    try:
        response = await run_in_threadpool(
            run_and_persist_catalog_analysis,
            product_id=product_id,
            skin_profile=parsed_profile,
            routine=request.routine,
            market=request.market.strip().upper(),
            user_id=current_user.id if current_user else None,
            progress_callback=lambda stage: (metrics.stage(stage), update_progress(progress_id, stage)),
        )
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status=response.status, response=response)
        update_progress(progress_id, "complete", "complete")
        return response
    except CatalogProductNotFoundError as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="not_found", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DatabaseUnavailableError as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise


@router.post("/analyze-barcode", response_model=FullProductAnalysisResponse)
async def analyze_barcode_endpoint(
    request: BarcodeAnalysisRequest,
    current_user: OptionalCurrentUser,
    session: DatabaseSession,
):
    normalized = normalize_barcode(request.barcode)
    if normalized is None:
        raise HTTPException(status_code=400, detail="Barkod 8–14 rakamdan oluşmalıdır.")
    if request.skin_profile is not None:
        parsed_profile = request.skin_profile
    elif current_user is not None:
        stored_profile = AuthRepository(session).get_skin_profile(current_user.id)
        if stored_profile is None:
            raise HTTPException(status_code=400, detail="Kayıtlı cilt profili bulunamadı; önce profil oluşturun.")
        parsed_profile = CompatibilitySkinProfile(
            skin_type=stored_profile.skin_type,
            sensitivity=stored_profile.sensitivity,
            appearance=stored_profile.appearance,
            goals=stored_profile.goals,
            avoid_ingredients=stored_profile.avoid_ingredients,
        )
    else:
        raise HTTPException(status_code=400, detail="Giriş yapmadan analiz için skin_profile alanı gereklidir.")
    progress_id = request.progress_id
    if progress_id and (len(progress_id) > 100 or not progress_id.startswith("analysis-")):
        raise HTTPException(status_code=400, detail="Analiz takip kimliği geçersiz.")
    if not claim_progress(progress_id):
        raise HTTPException(status_code=409, detail="Bu analiz isteği zaten başlatılmış.")
    metrics = AnalysisMetricsCollector(
        request_key=progress_id or f"analysis-{uuid4()}",
        flow_type="barcode",
    )
    try:
        response = await run_in_threadpool(
            run_and_persist_barcode_analysis,
            barcode=normalized,
            skin_profile=parsed_profile,
            routine=request.routine,
            market=request.market,
            user_id=current_user.id if current_user else None,
            progress_callback=lambda stage: (metrics.stage(stage), update_progress(progress_id, stage)),
        )
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status=response.status, response=response)
        update_progress(progress_id, "complete", "complete")
        return response
    except BarcodeProductNotFoundError as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="not_found", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DatabaseUnavailableError as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (
        IngredientAnalysisConfigurationError,
        CompatibilityAnalysisConfigurationError,
    ) as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except (
        IngredientAnalysisUnavailableError,
        CompatibilityAnalysisUnavailableError,
    ) as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ScoringInsufficientDataError as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        save_analysis_metric(session, collector=metrics, user_id=current_user.id if current_user else None, status="failed", error=exc)
        update_progress(progress_id, "failed", "failed")
        raise


@router.get("/analysis-progress/{progress_id}")
def analysis_progress_endpoint(progress_id: str):
    progress = get_progress(progress_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Analiz ilerlemesi bulunamadı.")
    return progress


@router.post(
    "/recognize-product",
    response_model=ProductRecognitionResponse,
)
async def recognize_product_endpoint(
    image: UploadFile = File(...)
):
    allowed_types = [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    ]

    if image.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Desteklenmeyen görsel formatı.",
        )

    image_bytes = await image.read()

    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail="Görsel boş.",
        )

    try:
        product = await run_in_threadpool(
            recognize_product_service,
            image_bytes=image_bytes,
            content_type=image.content_type,
        )
    except ProductRecognitionConfigurationError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc
    except ProductRecognitionUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    return {
        "filename": image.filename,
        "content_type": image.content_type,
        "size": len(image_bytes),
        "product": product,
    }


@router.post(
    "/acquire-ingredients",
    response_model=IngredientAcquisitionResponse,
)
async def acquire_ingredients_endpoint(
    brand: str = Form(...),
    product_name: str = Form(...),
    variant: str | None = Form(default=None),
    market: str = Form(default="TR"),
    images: list[UploadFile] | None = File(default=None),
):
    if not brand.strip() or not product_name.strip():
        raise HTTPException(
            status_code=400,
            detail="Marka ve ürün adı boş olamaz.",
        )

    uploaded_images = images or []
    if len(uploaded_images) > 3:
        raise HTTPException(
            status_code=400,
            detail="En fazla 3 içerik görseli yüklenebilir.",
        )

    allowed_types = {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    }
    image_payloads: list[tuple[bytes, str]] = []
    for image in uploaded_images:
        if image.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Desteklenmeyen görsel formatı: {image.content_type}",
            )
        image_bytes = await image.read()
        if not image_bytes:
            raise HTTPException(
                status_code=400,
                detail="İçerik görsellerinden biri boş.",
            )
        image_payloads.append((image_bytes, image.content_type))

    try:
        return await run_in_threadpool(
            acquire_ingredients_service,
            brand=brand.strip(),
            product_name=product_name.strip(),
            variant=variant.strip() if variant else None,
            market=market.strip().upper(),
            images=image_payloads,
        )
    except IngredientAcquisitionConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except IngredientAcquisitionUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/normalize-ingredients",
    response_model=NormalizeIngredientsResponse,
)
def normalize_ingredients_endpoint(
    request: NormalizeIngredientsRequest,
    session: DatabaseSession,
):
    repository = CosmeticRepository(session)
    return normalize_ingredients(
        request,
        resolver=lambda **kwargs: resolve_ingredient_knowledge(repository, **kwargs),
    )


@router.post(
    "/ingredient-knowledge",
    response_model=IngredientKnowledgeResponse,
)
def ingredient_knowledge_endpoint(
    request: NormalizeIngredientsResponse,
    session: DatabaseSession,
    market: str = Query(default="TR"),
):
    repository = CosmeticRepository(session)
    try:
        result = attach_ingredient_knowledge(
            request,
            cache_resolver=repository.find_cached_ingredient_items,
            cosing_resolver=lambda **kwargs: lookup_cosing_ingredients(kwargs["ingredients"]),
            researcher=analyze_ingredients_service,
            cache_writer=repository.save_cached_ingredient_items,
            market=market.strip().upper(),
        )
        session.commit()
        return result
    except IngredientAnalysisConfigurationError as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except IngredientAnalysisUnavailableError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/analyze-ingredients",
    response_model=AnalyzeIngredientsResponse,
)
async def analyze_ingredients_endpoint(request: AnalyzeIngredientsRequest):
    try:
        return await run_in_threadpool(analyze_ingredients_service, request)
    except IngredientAnalysisConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except IngredientAnalysisUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/analyze-compatibility",
    response_model=AnalyzeCompatibilityResponse,
)
async def analyze_compatibility_endpoint(request: AnalyzeCompatibilityRequest):
    try:
        return await run_in_threadpool(analyze_compatibility_service, request)
    except CompatibilityAnalysisConfigurationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except CompatibilityAnalysisUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/score-compatibility",
    response_model=ScoreCompatibilityResponse,
)
def score_compatibility_endpoint(request: ScoreCompatibilityRequest):
    try:
        return score_compatibility_service(request)
    except ScoringInsufficientDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
