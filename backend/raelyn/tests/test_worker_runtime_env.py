from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).resolve().parents[3]
_NUMERIC_THREAD_ENV = (
    "ANALYSIS_CPU_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
)


class WorkerRuntimeEnvTests(unittest.TestCase):
    def _base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for name in _NUMERIC_THREAD_ENV:
            env.pop(name, None)
        backend_path = str(_ROOT / "backend")
        current_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{backend_path}{os.pathsep}{current_pythonpath}"
            if current_pythonpath
            else backend_path
        )
        return env

    def _thread_pool_probe(
        self,
        *,
        role: str | None,
        worker_types: str | None = None,
        configured_threads: str | None = None,
        numeric_thread_env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        env = self._base_env()
        if role is not None:
            env["WORKER_ROLE"] = role
        else:
            env.pop("WORKER_ROLE", None)
        if worker_types is not None:
            env["WORKER_TYPES"] = worker_types
        else:
            env.pop("WORKER_TYPES", None)
        if configured_threads is not None:
            env["ANALYSIS_CPU_THREADS"] = configured_threads
        if numeric_thread_env:
            env.update(numeric_thread_env)
        probe = """
import json
import os

import raelyn.worker
import numpy as np
from sklearn.cluster import MiniBatchKMeans
from threadpoolctl import threadpool_info

matrix = np.arange(128, dtype=np.float32).reshape(32, 4)
MiniBatchKMeans(
    n_clusters=2,
    batch_size=8,
    n_init=1,
    random_state=0,
).fit(matrix)

print(json.dumps({
    "env": {
        name: os.environ.get(name)
        for name in (
            "ANALYSIS_CPU_THREADS",
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
        )
    },
    "thread_pools": [
        {
            "user_api": item.get("user_api"),
            "num_threads": item.get("num_threads"),
        }
        for item in threadpool_info()
        if item.get("user_api") in {"blas", "openmp"}
    ],
}))
"""
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(completed.stdout.splitlines()[-1])

    def _assert_thread_pool_limit(self, result: dict[str, Any], expected: int) -> None:
        pools = result["thread_pools"]
        self.assertTrue({"blas", "openmp"}.issubset({pool["user_api"] for pool in pools}))
        self.assertTrue(all(pool["num_threads"] == expected for pool in pools))

    def test_analysis_defaults_numeric_thread_pools_to_two(self) -> None:
        result = self._thread_pool_probe(role="analysis")

        self.assertEqual(set(result["env"].values()), {"2"})
        self._assert_thread_pool_limit(result, 2)

    def test_analysis_thread_limit_can_be_overridden(self) -> None:
        result = self._thread_pool_probe(role="analysis", configured_threads="1")

        self.assertEqual(set(result["env"].values()), {"1"})
        self._assert_thread_pool_limit(result, 1)

    def test_all_types_worker_is_limited_because_it_can_claim_analysis(self) -> None:
        result = self._thread_pool_probe(role=None)

        self.assertEqual(set(result["env"].values()), {"2"})
        self._assert_thread_pool_limit(result, 2)

    def test_explicit_analysis_worker_type_is_limited(self) -> None:
        result = self._thread_pool_probe(
            role="sync",
            worker_types="playlist.build_event_map_snapshot",
        )

        self.assertEqual(set(result["env"].values()), {"2"})
        self._assert_thread_pool_limit(result, 2)

    def test_non_analysis_worker_keeps_existing_numeric_thread_environment(self) -> None:
        result = self._thread_pool_probe(
            role="sync",
            configured_threads="7",
            numeric_thread_env={
                "OMP_NUM_THREADS": "3",
                "OPENBLAS_NUM_THREADS": "4",
                "MKL_NUM_THREADS": "5",
            },
        )

        self.assertEqual(
            result["env"],
            {
                "ANALYSIS_CPU_THREADS": "7",
                "OMP_NUM_THREADS": "3",
                "OPENBLAS_NUM_THREADS": "4",
                "MKL_NUM_THREADS": "5",
            },
        )

    def test_invalid_analysis_thread_limit_fails_before_worker_imports(self) -> None:
        env = self._base_env()
        env["WORKER_ROLE"] = "analysis"
        env["ANALYSIS_CPU_THREADS"] = "invalid"
        completed = subprocess.run(
            [sys.executable, "-c", "import raelyn.worker"],
            cwd=_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "ANALYSIS_CPU_THREADS must be a positive integer",
            completed.stderr,
        )


if __name__ == "__main__":
    unittest.main()
