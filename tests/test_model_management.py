import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


class ModelManagementTests(unittest.TestCase):
    def test_empty_registry_does_not_mark_recommendation_installed(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(server, "MODEL_REGISTRY_FILE", Path(td) / "registry.json"), \
             mock.patch.object(server, "H3_MODEL", str(Path(td) / "missing-model")), \
             mock.patch.object(server, "hardware_profile", return_value={
                 "os":"darwin", "architecture":"arm64", "platform":"darwin-arm64",
                 "memory_gb":64, "gpu":"", "vram_gb":0, "gpu_driver":"", "disk_free_gb":300}):
            state = server.model_management_state()
        self.assertEqual(state["installations"], [])
        self.assertTrue(next(item for item in state["catalog"] if item["recommended"])["installed"] is False)

    def test_only_integrated_backend_is_advertised_as_installable(self):
        self.assertEqual([item["id"] for item in server.MODEL_BACKENDS], ["h3-metal"])
        self.assertEqual(server.MODEL_BACKENDS[0]["install_component"], "h3")

    def test_catalog_does_not_recommend_unintegrated_streaming_backend(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(server, "MODEL_REGISTRY_FILE", Path(td) / "registry.json"), \
             mock.patch.object(server, "hardware_profile", return_value={
                 "os":"darwin", "architecture":"arm64", "platform":"darwin-arm64",
                 "memory_gb":24, "gpu":"", "vram_gb":0, "gpu_driver":"", "disk_free_gb":200}):
            state = server.model_management_state()
        recommended = [item["id"] for item in state["catalog"] if item["recommended"]]
        self.assertEqual(recommended, [])

    def test_catalog_does_not_recommend_unintegrated_cuda_backend(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(server, "MODEL_REGISTRY_FILE", Path(td) / "registry.json"), \
             mock.patch.object(server, "hardware_profile", return_value={
                 "os":"linux", "architecture":"x86_64", "platform":"linux-x86_64",
                 "memory_gb":64, "gpu":"NVIDIA RTX 4090", "vram_gb":24,
                 "gpu_driver":"560", "disk_free_gb":300}):
            state = server.model_management_state()
        recommended = [item["id"] for item in state["catalog"] if item["recommended"]]
        self.assertEqual(recommended, [])

    def test_catalog_does_not_recommend_untested_backend_for_16gb_mac(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(server, "MODEL_REGISTRY_FILE", Path(td) / "registry.json"), \
             mock.patch.object(server, "hardware_profile", return_value={
                 "os":"darwin", "architecture":"arm64", "platform":"darwin-arm64",
                 "memory_gb":16, "gpu":"", "vram_gb":0, "gpu_driver":"", "disk_free_gb":100}):
            state = server.model_management_state()
        self.assertFalse(any(item["recommended"] for item in state["catalog"]))

    def test_h3_metal_does_not_claim_or_accept_lora_support(self):
        backend = server.MODEL_BACKENDS[0]
        self.assertNotIn("loras", backend["supports"])
        with self.assertRaisesRegex(ValueError, "does not support LoRA"):
            server.import_lora("adapter.safetensors", "h3-metal")

    def test_uninstall_can_remove_active_model_and_leave_generation_empty(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); target = root / "models" / "MiniMax-H3"; target.mkdir(parents=True)
            registry = root / "registry.json"
            registry.write_text(json.dumps({"installations":[{"id":"install-test", "path":str(target),
                "managed":True, "receipt":[str(target)]}], "loras":[]}))
            sources = root / "sources.json"
            defaults = dict(server.DEFAULTS, model_root=str(target))
            with mock.patch.object(server, "ROOT", root), mock.patch.object(server, "MODEL_REGISTRY_FILE", registry), \
                 mock.patch.object(server, "MODEL_SOURCE_FILE", sources), mock.patch.object(server, "DEFAULTS", defaults), \
                 mock.patch.object(server, "H3_MODEL", str(target)), mock.patch.object(server, "_saved_model_sources", {"h3_model":str(target)}):
                result = server.uninstall_managed_model("install-test")
            self.assertTrue(result["active_removed"])
            self.assertFalse(target.exists())

    def test_uninstall_removes_only_receipted_inactive_model(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); target = root / "models" / "old-h3"; target.mkdir(parents=True)
            active = root / "models" / "active-h3"; active.mkdir()
            registry = root / "registry.json"
            registry.write_text(json.dumps({"installations":[{"id":"install-old", "path":str(target),
                "managed":True, "receipt":[str(target)]}], "loras":[]}))
            with mock.patch.object(server, "ROOT", root), mock.patch.object(server, "MODEL_REGISTRY_FILE", registry), \
                 mock.patch.object(server, "H3_MODEL", str(active)):
                server.uninstall_managed_model("install-old")
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
