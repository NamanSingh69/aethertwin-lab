"""Visual-only photo-capture layer: packaging, validation and serving."""
import json
import numpy as np
import pytest
from fastapi.testclient import TestClient

from command_center.appearance import load_appearance,package_appearance,validate_transform
from command_center.service import create_app

VIEWER='v'*40;OPERATOR='o'*40


@pytest.fixture
def model(tmp_path):
    path=tmp_path/'lab.glb';path.write_bytes(b'glTF'+bytes(range(64)));return path


def test_package_writes_a_valid_visual_only_manifest(model,tmp_path):
    turn=np.eye(4);turn[:2,:2]=[[0,-1],[1,0]];turn[:3,3]=[1,2,0]
    manifest=package_appearance(model,tmp_path/'capture',transform=turn,method='measured_points',rmse_m=.031,checked_points=6,
                                provenance={'device':'phone video','tool':'photogrammetry'})
    assert manifest['visual_only'] is True and manifest['kind']=='gltf' and manifest['file']=='lab.glb'
    assert manifest['alignment']=={'method':'measured_points','rmse_m':.031,'checked_points':6}
    assert np.allclose(np.reshape(manifest['transform'],(4,4)),turn)
    assert load_appearance(tmp_path/'capture')==manifest


@pytest.mark.parametrize('change,message',[
    (lambda m:m.update(visual_only=False),'visual only'),
    (lambda m:m.update(kind='gaussian_splat'),'not supported yet'),
    (lambda m:m.update(file='../lab.glb'),'plain .glb'),
    (lambda m:m.update(sha256='0'*64),'sha256'),
    (lambda m:m.update(transform=[2,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]),'uniform scale'),
    (lambda m:m['alignment'].update(method='measured_points',rmse_m=None),'rmse_m'),
])
def test_invalid_manifests_are_rejected(model,tmp_path,change,message):
    package_appearance(model,tmp_path/'capture');path=tmp_path/'capture'/'appearance.json'
    manifest=json.loads(path.read_text());change(manifest);path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match=message):load_appearance(tmp_path/'capture')


def test_uniform_scale_and_rotation_are_accepted():
    matrix=np.eye(4);matrix[:3,:3]*=.5;assert np.allclose(validate_transform(matrix.ravel()),matrix)
    with pytest.raises(ValueError,match='handedness'):validate_transform(np.diag([-1,1,1,1]).ravel())


def test_service_serves_only_the_declared_files(model,tmp_path):
    package_appearance(model,tmp_path/'capture');(tmp_path/'capture'/'secret.glb').write_bytes(b'x')
    app=create_app(host='0.0.0.0',operator_token=OPERATOR,viewer_token=VIEWER,appearance_root=tmp_path/'capture')
    with TestClient(app,base_url='https://testserver') as client:
        assert client.get('/api/v2/appearance/appearance.json').status_code==401
        assert client.post('/api/v2/session',json={'token':VIEWER}).status_code==200
        assert client.get('/api/v2/bootstrap').json()['appearance_url']=='/api/v2/appearance/appearance.json'
        assert client.get('/api/v2/appearance/appearance.json').json()['visual_only'] is True
        response=client.get('/api/v2/appearance/lab.glb')
        assert response.status_code==200 and response.content==model.read_bytes()
        assert response.headers['content-type']=='model/gltf-binary'
        for name in ('secret.glb','..%2Flab.glb','other.json'):
            assert client.get(f'/api/v2/appearance/{name}').status_code==404


def test_no_capture_means_no_url(tmp_path):
    app=create_app(host='0.0.0.0',operator_token=OPERATOR,viewer_token=VIEWER)
    with TestClient(app,base_url='https://testserver') as client:
        client.post('/api/v2/session',json={'token':VIEWER})
        assert client.get('/api/v2/bootstrap').json()['appearance_url'] is None
        assert client.get('/api/v2/appearance/appearance.json').status_code==404


def test_service_refuses_to_start_with_a_broken_capture(model,tmp_path):
    package_appearance(model,tmp_path/'capture');model_copy=tmp_path/'capture'/'lab.glb';model_copy.write_bytes(b'changed')
    with pytest.raises(ValueError,match='sha256'):create_app(appearance_root=tmp_path/'capture')
