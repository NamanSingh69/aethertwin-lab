"""AIDE2 C49: export_twin_assets wrote manifest.json with Path.write_text, which emits CRLF on Windows, so the same twin
exported to different bytes per OS (found in the C48 showcase fixture)."""
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]


def test_exported_asset_manifest_has_lf_line_endings(tmp_path):
    from core.pipeline import load_commissioned_assets
    from command_center.assets import export_twin_assets
    twin,_,_=load_commissioned_assets(ROOT/'assets/sample_recording/twin')
    export_twin_assets(twin,tmp_path/'assets',revision:='rev-c49',url_prefix='')
    data=(tmp_path/'assets'/'manifest.json').read_bytes()
    assert b'\r' not in data and data.startswith(b'{\n')
