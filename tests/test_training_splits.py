"""Training must not select checkpoints against duplicate or overlapping scenes."""
import copy
from pathlib import Path
import pytest
import yaml
from perception_ml.training import train,validate_synthetic_splits,_configured_model,_sampling_settings,_sampling_constants


def configuration():
    return yaml.safe_load((Path(__file__).resolve().parents[1]/'config/system_config.yaml').read_text())


@pytest.mark.parametrize('change,reason',[
    ({'train_seeds':[1,2],'validation_seeds':[2,3]},'leakage'),
    ({'train_seeds':[1,2],'test_seeds':[1]},'leakage'),
    ({'train_seeds':[1,1]},'Duplicate'),
    ({'train_seeds':['1']},'integer'),
    ({'validation_seeds':[]},'nonempty'),
])
def test_training_rejects_leaky_scene_splits_before_output(tmp_path,change,reason):
    config=configuration();config['training'].update(change)
    destination=tmp_path/'must_not_be_created.pt'
    with pytest.raises(ValueError,match=reason):train(config,destination,epochs=1)
    assert not destination.exists()


@pytest.mark.parametrize('epochs',[0,-1,True,1.2])
def test_nonpositive_or_noninteger_epoch_count_is_rejected(tmp_path,epochs):
    with pytest.raises(ValueError,match='positive integer'):
        train(configuration(),tmp_path/'never.pt',epochs=epochs)


def test_default_splits_and_configured_model_are_valid():
    config=configuration();validate_synthetic_splits(config)
    config['perception'].update(neighbors=7,temporal_neighbors=3,range_image_height=12,range_image_width=48)
    model=_configured_model(config,'cpu')
    assert model.neighbors==model.stage1.k==model.stage2.k==7
    assert model.temporal_neighbors==model.flow_corr.k==3
    assert model.range.height==12 and model.range.width==48
    sampling=_sampling_settings(config,model)
    assert sampling['range_image_height']==12 and sampling['range_image_width']==48
    assert _sampling_constants()=={'pointnet_fps_cap':256,'pointnet_radii_m':[.6,1.2],'mae_patch_points':16,'mae_center_cap':96}
