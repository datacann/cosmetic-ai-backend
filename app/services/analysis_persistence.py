from collections.abc import Callable, Sequence

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database.repository import CosmeticRepository
from app.services.open_beauty_facts import lookup_open_beauty_facts
from app.services.cosing_cache import resolve_ingredient_knowledge
from app.database.session import get_session_factory
from app.models.compatibility_analysis import (
    CompatibilityRoutineProduct,
    CompatibilitySkinProfile,
)
from app.models.full_analysis import FullProductAnalysisResponse
from app.services.full_analysis import run_full_analysis


class DatabaseUnavailableError(RuntimeError):
    pass


class BarcodeProductNotFoundError(RuntimeError):
    pass


class CatalogProductNotFoundError(RuntimeError):
    pass


def run_and_persist_full_analysis(
    *,
    product_image: tuple[bytes, str],
    ingredient_images: list[tuple[bytes, str]],
    skin_profile: CompatibilitySkinProfile,
    routine: Sequence[CompatibilityRoutineProduct],
    market: str,
    user_id: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
    barcode: str | None = None,
) -> FullProductAnalysisResponse:
    session_factory = get_session_factory()
    if session_factory is None:
        return run_full_analysis(
            product_image=product_image,
            ingredient_images=ingredient_images,
            skin_profile=skin_profile,
            routine=list(routine),
            market=market,
            progress_callback=progress_callback,
        )

    with session_factory() as session:
        repository = CosmeticRepository(session)
        try:
            # Fail before any paid AI calls when persistence is unavailable.
            session.execute(text("SELECT 1"))
            response = run_full_analysis(
                product_image=product_image,
                ingredient_images=ingredient_images,
                skin_profile=skin_profile,
                routine=list(routine),
                market=market,
                ingredient_cache_lookup=repository.find_cached_acquisition,
                ingredient_analysis_cache_lookup=repository.find_cached_ingredient_analysis,
                ingredient_item_cache_lookup=lambda **kwargs: resolve_ingredient_knowledge(repository, **kwargs),
                progress_callback=progress_callback,
            )
            if progress_callback is not None:
                progress_callback("saving_result")
            response.analysis_id = repository.save_full_analysis(
                response=response,
                skin_profile=skin_profile,
                routine=routine,
                market=market,
                user_id=user_id,
                barcode=barcode,
            )
            return response
        except SQLAlchemyError as exc:
            session.rollback()
            raise DatabaseUnavailableError(
                "Veritabanı işlemi tamamlanamadı. Bağlantı ve migration durumunu kontrol edin."
            ) from exc


def run_and_persist_barcode_analysis(
    *,
    barcode: str,
    skin_profile: CompatibilitySkinProfile,
    routine: Sequence[CompatibilityRoutineProduct],
    market: str,
    user_id: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> FullProductAnalysisResponse:
    session_factory = get_session_factory()
    if session_factory is None:
        raise DatabaseUnavailableError("Barkod analizi için veritabanı bağlantısı gereklidir.")
    with session_factory() as session:
        repository = CosmeticRepository(session)
        try:
            session.execute(text("SELECT 1"))
            product = repository.find_product_by_barcode(barcode)
            external_acquisition = None
            if product is None:
                external = lookup_open_beauty_facts(barcode, market=market)
                if external is None:
                    raise BarcodeProductNotFoundError(
                        "Bu barkod kendi kataloğumuzda veya Open Beauty Facts'te bulunamadı."
                    )
                product = external.product
                external_acquisition = external.acquisition
            response = run_full_analysis(
                product_image=None,
                recognized_product=product,
                ingredient_images=[],
                skin_profile=skin_profile,
                routine=list(routine),
                market=market,
                ingredient_cache_lookup=repository.find_cached_acquisition,
                ingredient_analysis_cache_lookup=repository.find_cached_ingredient_analysis,
                ingredient_item_cache_lookup=lambda **kwargs: resolve_ingredient_knowledge(repository, **kwargs),
                progress_callback=progress_callback,
                acquired_ingredients=external_acquisition,
            )
            if progress_callback is not None:
                progress_callback("saving_result")
            response.analysis_id = repository.save_full_analysis(
                response=response,
                skin_profile=skin_profile,
                routine=routine,
                market=market,
                user_id=user_id,
                barcode=barcode,
            )
            return response
        except BarcodeProductNotFoundError:
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise DatabaseUnavailableError(
                "Veritabanı işlemi tamamlanamadı. Bağlantı ve migration durumunu kontrol edin."
            ) from exc


def run_and_persist_catalog_analysis(
    *,
    product_id: str,
    skin_profile: CompatibilitySkinProfile,
    routine: Sequence[CompatibilityRoutineProduct],
    market: str,
    user_id: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> FullProductAnalysisResponse:
    session_factory = get_session_factory()
    if session_factory is None:
        raise DatabaseUnavailableError("Katalog analizi için veritabanı bağlantısı gereklidir.")
    with session_factory() as session:
        repository = CosmeticRepository(session)
        try:
            session.execute(text("SELECT 1"))
            product = repository.find_product_by_id(product_id)
            if product is None:
                raise CatalogProductNotFoundError("Ürün katalogda bulunamadı.")
            response = run_full_analysis(
                product_image=None,
                recognized_product=product,
                ingredient_images=[],
                skin_profile=skin_profile,
                routine=list(routine),
                market=market,
                ingredient_cache_lookup=repository.find_cached_acquisition,
                ingredient_analysis_cache_lookup=repository.find_cached_ingredient_analysis,
                ingredient_item_cache_lookup=lambda **kwargs: resolve_ingredient_knowledge(repository, **kwargs),
                progress_callback=progress_callback,
            )
            if progress_callback is not None:
                progress_callback("saving_result")
            response.analysis_id = repository.save_full_analysis(
                response=response,
                skin_profile=skin_profile,
                routine=routine,
                market=market,
                user_id=user_id,
            )
            return response
        except CatalogProductNotFoundError:
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise DatabaseUnavailableError(
                "Veritabanı işlemi tamamlanamadı. Bağlantı ve migration durumunu kontrol edin."
            ) from exc
