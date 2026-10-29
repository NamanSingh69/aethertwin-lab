"""Structural/provenance gates only; fixture ELF headers are not usable wheels."""
import json
from pathlib import Path
import subprocess
import zipfile
import pytest
from deployment.edge import open3d_wheel as helper


def fixture_wheel(directory, *, filename='open3d-0.19.0-cp312-cp312-manylinux_2_31_aarch64.whl', machine=183,
                  metadata_version='0.19.0', wheel_tag='cp312-cp312-manylinux_2_31_aarch64'):
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / filename
    header = bytearray(20); header[:6] = b'\x7fELF\x02\x01'; header[18:20] = machine.to_bytes(2, 'little')
    with zipfile.ZipFile(wheel, 'w') as stream:
        stream.writestr('open3d-0.19.0.dist-info/METADATA', f'Metadata-Version: 2.1\nName: open3d\nVersion: {metadata_version}\n')
        stream.writestr('open3d-0.19.0.dist-info/WHEEL', f'Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: {wheel_tag}\n')
        stream.writestr('open3d/cpu/pybind.so', header)
    return wheel


def fixture_manifest(wheel):
    value = {'schema_version': 1, 'status': 'built_and_validated', 'target': helper.TARGET,
        'source': {'url': helper.SOURCE_URL, 'commit': helper.SOURCE_COMMIT},
        'builder_toolchain': {'python': '3.12.3 (test fixture)', 'machine': 'aarch64', 'gcc': 'test fixture', 'cmake': 'test fixture'},
        'builder_image': {'Id': 'sha256:' + '1' * 64, 'Architecture': 'arm64', 'Os': 'linux'},
        'builder_import': {'version': '0.19.0', 'build_config': {'BUILD_CUDA_MODULE': False, 'BUILD_PYTORCH_OPS': False, 'BUILD_TENSORFLOW_OPS': False}},
        'wheel': helper.inspect_wheel(wheel)}
    path = wheel.parent / 'open3d-build.json'; path.write_text(json.dumps(value))
    return path, value


def test_validates_local_wheel_abi_hash_and_every_elf_header(tmp_path):
    wheel = fixture_wheel(tmp_path)
    manifest, expected = fixture_manifest(wheel)
    loaded, actual = helper.validate_wheel(wheel, manifest)
    assert actual == expected['wheel'] and loaded['source']['commit'] == helper.SOURCE_COMMIT
    assert actual['native_elf_machine'] == 'AArch64 (183)'
    with zipfile.ZipFile(wheel, 'a') as stream:
        header = bytearray(20); header[:6] = b'\x7fELF\x02\x01'; header[18:20] = (62).to_bytes(2, 'little')
        stream.writestr('open3d/libwrong.so.1', header)
    with pytest.raises(ValueError, match='AArch64 ELF'):
        helper.validate_wheel(wheel, manifest)


@pytest.mark.parametrize('changes', [
    {'filename': 'open3d-0.19.0-cp312-cp312-manylinux_2_31_x86_64.whl'},
    {'filename': 'open3d-0.19.0-cp311-cp311-manylinux_2_31_aarch64.whl'},
    {'metadata_version': '0.18.0'}, {'wheel_tag': 'cp312-cp312-manylinux_2_31_x86_64'}, {'machine': 62}])
def test_rejects_relabelled_x86_wrong_python_version_and_inconsistent_metadata(tmp_path, changes):
    with pytest.raises(ValueError):
        helper.inspect_wheel(fixture_wheel(tmp_path, **changes))


@pytest.mark.parametrize('mutation', ['hash', 'source', 'toolchain', 'cuda', 'failed'])
def test_rejects_incomplete_unreviewed_or_mutated_build_provenance(tmp_path, mutation):
    wheel = fixture_wheel(tmp_path); path, manifest = fixture_manifest(wheel)
    if mutation == 'hash':
        with wheel.open('ab') as stream: stream.write(b'changed-after-review')
    elif mutation == 'source': manifest['source']['commit'] = '0' * 40
    elif mutation == 'toolchain': manifest['builder_toolchain']['machine'] = 'x86_64'
    elif mutation == 'cuda': manifest['builder_import']['build_config']['BUILD_CUDA_MODULE'] = True
    else: manifest['status'] = 'failed'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError): helper.validate_wheel(wheel, path)


def test_runtime_rejects_host_x86_instead_of_relabelling_or_substituting(tmp_path, monkeypatch):
    wheel = fixture_wheel(tmp_path); manifest, _ = fixture_manifest(wheel)
    monkeypatch.setattr(helper.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(helper.platform, 'machine', lambda: 'x86_64')
    with pytest.raises(ValueError, match='Linux AArch64 CPython3.12'):
        helper.validate_wheel(wheel, manifest, require_runtime=True)


def test_install_is_local_only_hash_required_and_does_not_resolve_vendor_dependencies(tmp_path, monkeypatch):
    wheel = fixture_wheel(tmp_path); manifest, _ = fixture_manifest(wheel)
    validate = helper.validate_wheel; calls = []
    def allow_test_fixture(path, manifest_path, require_runtime=False):
        assert require_runtime is True
        return validate(path, manifest_path)
    def capture_install(command, **kwargs):
        calls.append(command)
        requirement = Path(command[-1]).read_text()
        assert wheel.resolve().as_uri() in requirement
        assert '--hash=sha256:' + helper.sha256(wheel) in requirement
        assert kwargs['check'] is True
    monkeypatch.setattr(helper, 'validate_wheel', allow_test_fixture)
    monkeypatch.setattr(helper.subprocess, 'run', capture_install)
    helper.install_wheel(wheel, manifest)
    assert len(calls) == 1
    assert all(flag in calls[0] for flag in ('--no-index', '--no-deps', '--require-hashes'))


def mock_builder(monkeypatch, source, output, *, architecture='aarch64', fail=False):
    source.mkdir()
    for name in ('docker/docker_build.sh', 'docker/Dockerfile.openblas', 'CMakeLists.txt'):
        path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('fixture recipe')
    def capture(command, **kwargs):
        if command[:2] == ['git', 'rev-parse']: return helper.SOURCE_COMMIT
        if command[:2] == ['git', 'remote']: return helper.SOURCE_URL
        if command[:2] == ['git', 'status']: return ''
        if command[:2] == ['docker', 'info']: return json.dumps({'OSType': 'linux', 'Architecture': architecture})
        if command[:3] == ['docker', 'image', 'inspect']:
            return json.dumps([{'Id': 'sha256:' + '2' * 64, 'Architecture': 'arm64', 'Os': 'linux'}])
        if 'open3d._build_config' in command[-1]:
            return json.dumps({'version': '0.19.0', 'build_config': {'BUILD_CUDA_MODULE': False, 'BUILD_PYTORCH_OPS': False, 'BUILD_TENSORFLOW_OPS': False}})
        if command[:2] == ['docker', 'run']:
            return json.dumps({'python': '3.12.3 (fixture)', 'machine': 'aarch64', 'gcc': 'fixture', 'cmake': 'fixture'})
        raise AssertionError(command)
    def run(command, **kwargs):
        assert command == ['bash', str(source / 'docker/docker_build.sh'), helper.TARGET]
        assert kwargs['cwd'] == output
        if fail: raise subprocess.CalledProcessError(1, command)
        fixture_wheel(output)
    monkeypatch.setattr(helper, '_capture', capture)
    monkeypatch.setattr(helper.subprocess, 'run', run)


def test_build_flow_records_toolchain_hash_and_status_without_claiming_jetson_test(tmp_path, monkeypatch):
    source = tmp_path / 'upstream'; output = tmp_path / 'wheels'
    mock_builder(monkeypatch, source, output)
    report = helper.build_wheel(source, output)
    assert report['status'] == 'built_and_validated'
    assert report['builder_toolchain']['machine'] == 'aarch64'
    assert len(report['wheel']['sha256']) == 64 and len(report['source']['recipe_sha256']) == 3
    assert report['physical_jetson_tested'] is False
    assert (output / 'open3d-build.log').exists()
    with pytest.raises(FileExistsError): helper.build_wheel(source, output)


def test_failed_build_retains_unusable_evidence(tmp_path, monkeypatch):
    source = tmp_path / 'upstream'; output = tmp_path / 'wheels'
    mock_builder(monkeypatch, source, output, fail=True)
    with pytest.raises(subprocess.CalledProcessError): helper.build_wheel(source, output)
    report = json.loads((output / 'open3d-build.json').read_text())
    assert report['status'] == 'failed' and report['physical_jetson_tested'] is False
    assert (output / 'open3d-build.log').exists()


def test_non_arm_engine_requires_explicit_preconfigured_runner_declaration(tmp_path, monkeypatch):
    source = tmp_path / 'upstream'; output = tmp_path / 'wheels'
    mock_builder(monkeypatch, source, output, architecture='x86_64')
    with pytest.raises(ValueError, match='already configured ARM64'):
        helper.build_wheel(source, output)
    report = helper.build_wheel(source, output, configured_arm64_runner=True)
    assert report['configured_arm64_runner_declared'] is True


def test_docker_requires_local_wheel_before_constraints_and_runtime_resolution():
    dockerfile = (Path(__file__).resolve().parents[1] / 'deployment/edge/Dockerfile').read_text()
    assert dockerfile.index('open3d_wheel.py install') < dockerfile.index('pip freeze') < dockerfile.index('-r deployment/edge/requirements-runtime.txt')
    assert 'ARG OPEN3D_WHEEL' in dockerfile and 'ARG OPEN3D_WHEEL_MANIFEST' in dockerfile
