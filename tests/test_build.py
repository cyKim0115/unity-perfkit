import json
import tempfile
import unittest
from pathlib import Path

from perfkit.build import package as PK
from perfkit.build.collect import run
from perfkit.diff import resultio
from perfkit.schema import validate
from tests.fakeproj import SHADER_LOG, elf32, elf64, isolated_settings, layout_json, make_aab, make_apk


class ElfTest(unittest.TestCase):
    def test_load_align(self):
        self.assertEqual(PK.elf_load_align(elf64(0x4000)), {"bits": 64, "min_align": 16384, "loads": 2})
        self.assertEqual(PK.elf_load_align(elf64(0x1000))["min_align"], 4096)
        self.assertEqual(PK.elf_load_align(elf32(0x1000)), {"bits": 32, "min_align": 4096, "loads": 1})
        self.assertIn("error", PK.elf_load_align(b"not an elf"))


class BuildCollectTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        (self.d / "build.log").write_text(SHADER_LOG, encoding="utf-8")
        (self.d / "buildlayout.json").write_text(json.dumps(layout_json()), encoding="utf-8")

    def tearDown(self):
        self._td.cleanup()

    def _run(self, apk, **kw):
        with isolated_settings():
            return run(apk, **kw)

    def test_apk_values(self):
        apk = make_apk(self.d / "good.apk")
        res, meta = self._run(apk, log=self.d / "build.log", layout=self.d / "buildlayout.json")
        self.assertEqual(validate(res, "result"), [])
        it = res["items"]
        self.assertEqual(it["BLD-01"]["values"], {"apk_mb": round(apk.stat().st_size / 1e6, 3)})
        self.assertEqual(it["BLD-05"]["values"], {"so_unaligned": 0, "zip_aligned": True})
        so = {r["name"]: r for r in it["BLD-05"]["detail"]["so"]}
        self.assertFalse(so["lib/armeabi-v7a/libold.so"]["required"])  # 32비트는 필수 아님
        self.assertEqual(it["BLD-07"]["values"]["libil2cpp_mb"], round(len(elf64(0x4000)) / 1e6 + 2.0, 3))
        self.assertEqual(it["BLD-03"]["status"], "not_applicable")
        # 셰이더: 블록 합 16+12+2+16+3=49, 고유 최댓값 합 16+12+2+3=33
        self.assertEqual(it["GPU-15"]["values"], {"variants": 49})
        self.assertEqual(it["GPU-15"]["detail"]["variants_unique"], 33)
        self.assertEqual(it["GPU-15"]["detail"]["shaders"], 2)
        # 레이아웃
        self.assertEqual(it["MEM-09"]["values"], {"dup_count": 1, "dup_mb": 0.6})  # 300KB × (3-1)
        self.assertEqual(it["MEM-11"]["values"]["texture_total_mb"], 3.0)  # 2,000,200 + 1,000,100 B
        self.assertEqual(it["MEM-12"]["values"]["mesh_total_mb"], 0.5)
        self.assertEqual(it["MEM-14"]["values"], {"value": 0.1})
        self.assertEqual(it["MEM-17"]["values"], {"value": 1.0})
        self.assertEqual(it["BLD-02"]["values"]["bundles"], [{"name": "group_assets_all.bundle", "mb": 3.0}])
        self.assertEqual(it["GPU-13"]["status"], "not_collected")
        self.assertEqual(res["source"]["apk_sha"], PK.sha256_file(apk))

    def test_unaligned(self):
        apk = make_apk(self.d / "bad.apk", bad_align=True, misaligned_zip=True)
        res, _ = self._run(apk)
        v = res["items"]["BLD-05"]["values"]
        self.assertEqual(v, {"so_unaligned": 1, "zip_aligned": False})
        self.assertEqual(res["items"]["BLD-05"]["violations"], ["lib/arm64-v8a/libsdk.so"])
        from perfkit.diff.compare import diff_results
        with isolated_settings():
            cmp = diff_results(None, res)  # 기준 없이 절대 기준만
        self.assertEqual(cmp["items"]["BLD-05"]["outcome"], "fail")

    def test_aab(self):
        aab = make_aab(self.d / "app.aab")
        res, _ = self._run(aab)
        it = res["items"]
        self.assertEqual(validate(res, "result"), [])
        self.assertEqual(sorted(it["BLD-01"]["values"]), ["aab_base_module_mb", "aab_download_mb"])
        self.assertLess(it["BLD-01"]["values"]["aab_download_mb"], it["BLD-01"]["values"]["aab_base_module_mb"]
                        + it["BLD-03"]["values"]["install_time_total_mb"] + 1e-9)
        self.assertEqual(it["BLD-03"]["values"]["allocation"],
                         [{"name": "pack1", "delivery": "install-time"}, {"name": "pack2", "delivery": "on-demand"}])
        self.assertIn("libil2cpp_mb", it["BLD-07"]["values"])

    def test_same_input_same_bytes(self):
        apk = make_apk(self.d / "good.apk")
        a, _ = self._run(apk, log=self.d / "build.log", layout=self.d / "buildlayout.json")
        b, _ = self._run(apk, log=self.d / "build.log", layout=self.d / "buildlayout.json")
        self.assertEqual(resultio.dumps(a), resultio.dumps(b))
        self.assertNotIn(str(self.d), resultio.dumps(a))


if __name__ == "__main__":
    unittest.main()
