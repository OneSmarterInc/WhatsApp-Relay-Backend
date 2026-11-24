import os
import runpy
from pathlib import Path
from unittest.mock import patch
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from relay import semantic


class RuntimeConfigurationTests(SimpleTestCase):
    def config(self, **values):
        with patch.dict(os.environ, values, clear=True):
            return runpy.run_path(str(Path(__file__).resolve().parents[1] / 'config' / 'runtime.py'))

    def test_models_can_be_selected_independently(self):
        config = self.config(AI_ANSWER_MODEL='custom:latest', AI_TASK_MODEL='tasks:latest')
        self.assertEqual(config['AI_ANSWER_MODEL'], 'custom:latest')
        self.assertEqual(config['AI_TASK_MODEL'], 'tasks:latest')
        self.assertEqual(config['AI_TOPIC_MODEL'], 'qwen3:1.7b')

    def test_invalid_runtime_settings_fail_at_startup(self):
        for values in [
            {'AI_REQUEST_TIMEOUT_SECONDS': 'invalid'},
            {'AI_TEMPERATURE': 'nan'},
            {'OLLAMA_BASE_URL': 'http://user:secret@localhost:11434'},
            {'OLLAMA_BASE_URL': 'http://localhost:11434/api/chat'},
            {'SEARCH_CHUNK_OVERLAP': '800'},
            {'WORKER_STALE_SECONDS': '20'},
        ]:
            with self.subTest(names=list(values)):
                with self.assertRaises(ImproperlyConfigured):
                    self.config(**values)

    def test_embedding_configuration_invalidates_cached_vectors(self):
        with patch.object(semantic, 'local_json', return_value={'models': [{'name': semantic.EMBED_MODEL, 'digest': 'abc'}]}):
            initial = semantic.model_version()
            with override_settings(SEARCH_CHUNK_MAX_BYTES=1600):
                self.assertNotEqual(initial, semantic.model_version())
            with override_settings(OLLAMA_BASE_URL='http://localhost:11435'):
                self.assertNotEqual(initial, semantic.model_version())
