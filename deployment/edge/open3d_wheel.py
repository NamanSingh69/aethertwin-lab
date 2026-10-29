"""Prepare/verify a pinned Open3D CPU wheel for CPython3.12 Linux AArch64.

This helper does not provision Docker, QEMU, operating-system packages or GPUs.
An ARM-capable Docker runner and a clean pinned upstream checkout are required.
"""
from pathlib import Path
import argparse
import datetime
from email.parser import BytesParser
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import zipfile

SOURCE_COMMIT = '1e7b17438687a0b0c1e5a7187321ac7044afe275'
SOURCE_URL = 'https://github.com/isl-org/Open3D.git'
TARGET = 'openblas-arm64-py312'
IMAGE = 'open3d-ci:openblas-arm64-py312-release'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _platform_tag(tag):
    return tag == 'linux_aarch64' or bool(re.fullmatch(r'manylinux_2_\d+_aarch64', tag))


def inspect_wheel(path):
    """Validate name, metadata tags and *every* bundled native ELF machine header."""
    path = Path(path)
    parts = path.name.removesuffix('.whl').split('-')
    if path.suffix != '.whl' or len(parts) != 5 or parts[:4] != ['open3d', '0.19.0', 'cp312', 'cp312']:
        raise ValueError('Expected open3d-0.19.0-cp312-cp312-<Linux AArch64 tag>.whl')
    platforms = parts[4].split('.')
    if not platforms or not all(_platform_tag(tag) for tag in platforms):
        raise ValueError('Wheel platform must be Linux AArch64, not x86/Windows/macOS')
    expected_tags = {f'cp312-cp312-{tag}' for tag in platforms}
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        prefix = 'open3d-0.19.0.dist-info/'
        for name in (prefix + 'METADATA', prefix + 'WHEEL'):
            if names.count(name) != 1 or wheel.getinfo(name).file_size > 1024 * 1024:
                raise ValueError('Missing, duplicate or oversized wheel metadata')
        metadata = BytesParser().parsebytes(wheel.read(prefix + 'METADATA'))
        tags = BytesParser().parsebytes(wheel.read(prefix + 'WHEEL'))
        if metadata.get('Name', '').lower() != 'open3d' or metadata.get('Version') != '0.19.0':
            raise ValueError('Wheel metadata distribution/version does not match the pinned dependency')
        if set(tags.get_all('Tag', [])) != expected_tags or tags.get('Root-Is-Purelib', '').lower() != 'false':
            raise ValueError('Wheel metadata ABI/platform/purity does not match its native filename')
        native = [name for name in names if re.search(r'\.so(?:\.[0-9.]+)?$', name)]
        if not native:
            raise ValueError('Open3D wheel contains no native shared libraries')
        for name in native:
            with wheel.open(name) as stream:
                header = stream.read(20)
            if len(header) < 20 or header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
                raise ValueError(f'Native library is not a 64-bit little-endian AArch64 ELF: {name}')
    return {'filename': path.name, 'sha256': sha256(path), 'size_bytes': path.stat().st_size,
            'distribution': 'open3d', 'version': '0.19.0', 'tags': sorted(expected_tags),
            'native_libraries': native, 'native_elf_machine': 'AArch64 (183)'}


def validate_wheel(path, manifest_path, require_runtime=False):
    if not Path(path).is_file() or not Path(manifest_path).is_file():
        raise ValueError('A local ARM64 Open3D wheel and its completed build manifest are required; no PyPI fallback is available')
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or manifest.get('status') != 'built_and_validated':
        raise ValueError('A completed Open3D build manifest is required')
    if manifest.get('source', {}).get('url') != SOURCE_URL or manifest['source'].get('commit') != SOURCE_COMMIT:
        raise ValueError('Build manifest must identify the reviewed official Open3D source commit')
    image = manifest.get('builder_image', {})
    if manifest.get('target') != TARGET or not manifest.get('builder_toolchain') or not re.fullmatch(r'sha256:[0-9a-f]{64}', image.get('Id', '')) or image.get('Architecture') != 'arm64' or image.get('Os') != 'linux':
        raise ValueError('Build manifest is missing the target, toolchain or immutable builder image identity')
    if not manifest['builder_toolchain'].get('python', '').startswith('3.12.') or manifest['builder_toolchain'].get('machine', '').lower() not in ('aarch64', 'arm64'):
        raise ValueError('Builder toolchain must identify CPython3.12 on AArch64')
    if any(not manifest['builder_toolchain'].get(name) for name in ('gcc', 'cmake')):
        raise ValueError('Builder GCC and CMake versions are required')
    configuration = manifest.get('builder_import', {})
    if configuration.get('version') != '0.19.0' or any(configuration.get('build_config', {}).get(name) is not False for name in ('BUILD_CUDA_MODULE', 'BUILD_PYTORCH_OPS', 'BUILD_TENSORFLOW_OPS')):
        raise ValueError('Build manifest must prove the builder imported the pinned CPU-only Open3D configuration')
    actual = inspect_wheel(path)
    if any(manifest.get('wheel', {}).get(key) != actual[key] for key in ('filename', 'sha256', 'size_bytes', 'tags')):
        raise ValueError('Local wheel content/tag/hash differs from the completed build manifest')
    if require_runtime:
        if platform.system() != 'Linux' or platform.machine().lower() not in ('aarch64', 'arm64') or sys.version_info[:2] != (3, 12):
            raise ValueError('Wheel installation requires Linux AArch64 CPython3.12')
        libc, version = platform.libc_ver()
        if libc != 'glibc' or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', version):
            raise ValueError('The glibc runtime must be identifiable before Open3D installation')
        available = tuple(map(int, version.split('.')[:2]))
        for tag in actual['tags']:
            minimum = re.search(r'manylinux_(2)_(\d+)_aarch64$', tag)
            if minimum and available < tuple(map(int, minimum.groups())):
                raise ValueError('Runtime glibc is older than the locally built wheel platform tag')
    return manifest, actual


def _capture(args, *, cwd=None, env=None):
    return subprocess.check_output(args, cwd=cwd, env=env, text=True).strip()


def build_wheel(source, output, *, docker_context=None, configured_arm64_runner=False):
    source = Path(source).resolve(); output = Path(output).resolve()
    if _capture(['git', 'rev-parse', 'HEAD'], cwd=source) != SOURCE_COMMIT:
        raise ValueError('Open3D checkout is not the reviewed v0.19.0 source commit')
    origin = _capture(['git', 'remote', 'get-url', 'origin'], cwd=source)
    if origin not in (SOURCE_URL, SOURCE_URL.removesuffix('.git'), 'git@github.com:isl-org/Open3D.git'):
        raise ValueError('Use a checkout of the official isl-org/Open3D repository')
    if _capture(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=source):
        raise ValueError('Open3D build source must be clean; do not silently patch the official recipe')
    output.mkdir(parents=True, exist_ok=True)
    if any(output.glob('*.whl')) or (output / 'open3d-build.json').exists():
        raise FileExistsError('Choose an empty wheel output directory; previous build evidence is retained')
    environment = dict(os.environ)
    if docker_context:
        environment['DOCKER_CONTEXT'] = docker_context
    engine = json.loads(_capture(['docker', 'info', '--format', '{{json .}}'], env=environment))
    if engine.get('OSType') != 'linux':
        raise ValueError('Open3D ARM64 build requires a Linux Docker runner')
    if engine.get('Architecture', '').lower() not in ('aarch64', 'arm64') and not configured_arm64_runner:
        raise ValueError('Use an ARM64 Docker engine, or explicitly declare an already configured ARM64 execution runner')
    command = ['bash', str(source / 'docker/docker_build.sh'), TARGET]
    recipe_files = ('docker/docker_build.sh', 'docker/Dockerfile.openblas', 'CMakeLists.txt')
    manifest = {'schema_version': 1, 'status': 'building', 'target': TARGET,
        'source': {'url': SOURCE_URL, 'commit': SOURCE_COMMIT, 'working_tree_clean': True,
                   'recipe_sha256': {name: sha256(source / name) for name in recipe_files}},
        'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'command': command, 'cwd': str(output), 'docker_context': docker_context,
        'engine': {key: engine.get(key) for key in ('Name', 'ServerVersion', 'Architecture', 'OSType', 'OperatingSystem')},
        'configured_arm64_runner_declared': bool(configured_arm64_runner),
        'physical_jetson_tested': False, 'reproducibility_limit': 'Pinned upstream recipe; its OS/Conda dependency resolution is not a hash-locked or bit-reproducible toolchain'}
    manifest_path = output / 'open3d-build.json'
    try:
        with (output / 'open3d-build.log').open('w', encoding='utf-8') as log:
            subprocess.run(command, cwd=output, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
        wheels = list(output.glob('*.whl'))
        if len(wheels) != 1:
            raise ValueError('Official build must publish exactly one wheel to its invocation directory')
        manifest['wheel'] = inspect_wheel(wheels[0])
        image = json.loads(_capture(['docker', 'image', 'inspect', IMAGE], env=environment))[0]
        manifest['builder_image'] = {key: image.get(key) for key in ('Id', 'RepoDigests', 'Architecture', 'Os')}
        if image.get('Architecture') != 'arm64' or image.get('Os') != 'linux':
            raise ValueError('Built Docker image does not identify Linux ARM64')
        probe = 'import json,platform,subprocess,sys;print(json.dumps({"python":sys.version,"machine":platform.machine(),"gcc":subprocess.check_output(["gcc","--version"],text=True),"cmake":subprocess.check_output(["cmake","--version"],text=True)}))'
        manifest['builder_toolchain'] = json.loads(_capture(['docker', 'run', '--rm', IMAGE, 'python', '-c', probe], env=environment))
        probe = 'import json,open3d;print(json.dumps({"version":open3d.__version__,"build_config":open3d._build_config}))'
        manifest['builder_import'] = json.loads(_capture(['docker', 'run', '--rm', IMAGE, 'python', '-c', probe], env=environment))
        manifest['status'] = 'built_and_validated'
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        validate_wheel(wheels[0], manifest_path)
    except Exception as error:
        manifest.update(status='failed', error=f'{type(error).__name__}: {error}')
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        raise
    return manifest


def install_wheel(path, manifest_path):
    _, actual = validate_wheel(path, manifest_path, require_runtime=True)
    with tempfile.TemporaryDirectory(prefix='aethertwin-open3d-') as temporary:
        requirement = Path(temporary) / 'open3d-local.requirements'
        requirement.write_text(f'{Path(path).resolve().as_uri()} --hash=sha256:{actual["sha256"]}\n', encoding='utf-8')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps', '--require-hashes', '-r', str(requirement)], check=True)
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    build = sub.add_parser('build'); build.add_argument('--source', type=Path, required=True); build.add_argument('--output', type=Path, required=True)
    build.add_argument('--docker-context'); build.add_argument('--configured-arm64-runner', action='store_true')
    for name in ('validate', 'install'):
        command = sub.add_parser(name); command.add_argument('--wheel', type=Path, required=True); command.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'build':
        result = build_wheel(args.source, args.output, docker_context=args.docker_context, configured_arm64_runner=args.configured_arm64_runner)
    elif args.mode == 'install':
        result = install_wheel(args.wheel, args.manifest)
    else:
        _, result = validate_wheel(args.wheel, args.manifest)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
