import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.models import AnalysisMetric
from app.services.analysis_metrics import AnalysisMetricsCollector, save_analysis_metric
from tests.test_database_repository import analysis_response


class AnalysisMetricsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    @patch("app.services.analysis_metrics.monotonic", side_effect=[0.1, 0.3, 0.5])
    def test_collector_measures_each_stage(self, _clock):
        collector = AnalysisMetricsCollector(request_key="analysis-metric", flow_type="vision", started_at=0.0)
        collector.stage("recognizing_product")
        collector.stage("acquiring_ingredients")

        total, stages, current = collector.snapshot()

        self.assertEqual(total, 500)
        self.assertEqual(stages["recognizing_product"], 200)
        self.assertEqual(stages["acquiring_ingredients"], 200)
        self.assertEqual(current, "acquiring_ingredients")

    def test_metric_persists_only_operational_metadata(self):
        response = analysis_response(["Aqua", "Glycerin"])
        response.warnings.append("ingredient_analysis_loaded_from_cache")
        response.ingredient_acquisition.source.type = "database"
        collector = AnalysisMetricsCollector(request_key="analysis-persist", flow_type="barcode")
        collector.stage("matching_profile")

        save_analysis_metric(
            self.session,
            collector=collector,
            user_id=None,
            status="complete",
            response=response,
        )

        metric = self.session.scalar(select(AnalysisMetric))
        self.assertEqual(metric.flow_type, "barcode")
        self.assertTrue(metric.formula_cache_used)
        self.assertTrue(metric.ingredient_cache_used)
        self.assertEqual(
            metric.model_usage,
            {"calls": [], "input_tokens": 0, "output_tokens": 0, "estimated_usd": 0},
        )
        self.assertIsNone(metric.error_type)


if __name__ == "__main__":
    unittest.main()
