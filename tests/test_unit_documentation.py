"""Execute the shared-unit reference examples and verify their displayed output."""

import re
from pathlib import Path

import pytest


def test_unit_reference_examples(capsys: pytest.CaptureFixture[str]) -> None:
    path = Path(__file__).parents[1] / "docs" / "reference" / "units.md"
    document = path.read_text()
    sources = re.findall(r"```python\n(.*?)```", document, flags=re.DOTALL)
    outputs = re.findall(r"```text\n(.*?)```", document, flags=re.DOTALL)
    assert len(sources) == len(outputs) == 3

    namespace: dict[str, object] = {"__name__": "unit_reference_example"}
    for source, expected in zip(sources, outputs, strict=True):
        exec(compile(source, str(path), "exec"), namespace)
        assert capsys.readouterr().out == expected
