"""Safe offline regression tests for delayed Instagram container processing."""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

os.environ.setdefault("SESSION_SECRET","secure-unit-test-session-secret-not-for-production")
from app import meta_platform as meta


class ProviderPollingTests(unittest.TestCase):
    def fake_job(self, age=2):
        return {"id":17,"container_id":"178987000123",
                "updated_at":datetime.utcnow()-timedelta(minutes=age)}

    def test_in_progress_stops_after_five_minutes(self):
        db=Mock()
        with patch.object(meta,"graph",return_value={
                "status_code":"IN_PROGRESS","status":"Processing"}) as graph, \
             patch.object(meta,"record_provider_check",return_value="IN_PROGRESS"), \
             patch.object(meta,"set_state") as set_state:
            meta.interpret_container_state(db,self.fake_job(age=7),"token","17840000")
            graph.assert_called_once()
            self.assertEqual(set_state.call_args.args[:4],
                             (db,17,"processing","needs_review"))
            self.assertIn("5 minutos",set_state.call_args.kwargs["error_hint"])

    def test_new_in_progress_is_not_falsely_failed(self):
        db=Mock()
        with patch.object(meta,"graph",return_value={"status_code":"IN_PROGRESS"}), \
             patch.object(meta,"record_provider_check",return_value="IN_PROGRESS"), \
             patch.object(meta,"set_state") as set_state:
            meta.interpret_container_state(db,self.fake_job(age=1),"token","17840000")
            set_state.assert_not_called()

    def test_meta_error_records_readable_failure_without_retry(self):
        db=Mock()
        with patch.object(meta,"graph",return_value={
                "status_code":"ERROR","status":"Video is not valid"}), \
             patch.object(meta,"record_provider_check",return_value="ERROR"), \
             patch.object(meta,"set_state") as set_state:
            meta.interpret_container_state(db,self.fake_job(age=3),"token","17840000")
            self.assertEqual(set_state.call_args.args[:4],
                             (db,17,"processing","failed"))
            self.assertIn("Video is not valid",set_state.call_args.kwargs["error_hint"])

    def test_published_container_is_not_published_twice(self):
        db=Mock()
        with patch.object(meta,"graph",return_value={"status_code":"PUBLISHED"}), \
             patch.object(meta,"record_provider_check",return_value="PUBLISHED"), \
             patch.object(meta,"set_state") as set_state:
            meta.interpret_container_state(db,self.fake_job(age=2),"token","17840000")
            self.assertEqual(set_state.call_args.args[:4],
                             (db,17,"processing","needs_review"))

    def test_ready_container_only_publishes_after_atomic_claim(self):
        db=Mock()
        with patch.object(meta,"graph",return_value={"status_code":"FINISHED"}), \
             patch.object(meta,"record_provider_check",return_value="FINISHED"), \
             patch.object(meta,"set_state",return_value=False) as set_state:
            meta.interpret_container_state(db,self.fake_job(age=1),"token","17840000")
            set_state.assert_called_once()
            self.assertEqual(set_state.call_args.args[:4],
                             (db,17,"processing","publishing"))

    def test_worker_runs_for_publications_and_metrics_are_separate(self):
        self.assertTrue(callable(meta.run_publish_cycle))
        self.assertTrue(callable(meta.run_metrics_cycle))
        routes={route.path for route in meta.app.routes}
        self.assertIn("/posts/{post_id}/check",routes)
        self.assertIn("/api/posts/status",routes)


if __name__ == "__main__":
    unittest.main()
