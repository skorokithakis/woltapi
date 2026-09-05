import runpy
from pathlib import Path

import pytest


extract_authorization = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "examples" / "check_session.py")
)["extract_authorization"]


def test_extracts_exact_token_without_executing_command():
    assert (
        extract_authorization(
            "curl 'https://example.invalid' \\\n"
            "  -H 'accept: application/json' \\\n"
            "  --header 'authorization: Bearer synthetic.token-signature' "
            "--data-raw '$(never-execute-this)'"
        )
        == "Bearer synthetic.token-signature"
    )


@pytest.mark.parametrize(
    "command",
    [
        "curl -H 'accept: application/json'",
        "curl -H 'authorization: Bearer one' -H 'Authorization: Bearer two'",
        "curl -H 'authorization: Basic synthetic'",
        "curl -H 'authorization: Bearer ' ",
        "curl -H 'authorization: Bearer synthetic\ninjection'",
        "curl -H 'authorization: Bearer synthetic\r'",
        "curl -H 'authorization: Bearer synthetic",
        "not-curl -H 'authorization: Bearer synthetic'",
    ],
)
def test_rejects_invalid_input_without_disclosing_token(command):
    with pytest.raises(ValueError) as exc:
        extract_authorization(command)
    assert "synthetic" not in str(exc.value)
