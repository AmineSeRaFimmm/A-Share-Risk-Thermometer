from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.storage.json_store import dumps_json, write_json


class JsonStoreTests(unittest.TestCase):
    def test_non_finite_and_pandas_missing_values_become_null(self) -> None:
        text = dumps_json(
            {
                "qvix_source": float("nan"),
                "nested": [math.inf, -math.inf, pd.NA],
            }
        )

        self.assertNotIn("NaN", text)
        self.assertNotIn("Infinity", text)
        self.assertEqual(json.loads(text), {"qvix_source": None, "nested": [None, None, None]})

    def test_write_json_produces_strict_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.json"
            write_json({"qvix_source": pd.NA}, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"qvix_source": None})


if __name__ == "__main__":
    unittest.main()
