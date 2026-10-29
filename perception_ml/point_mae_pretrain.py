"""Asymmetric patch-masked autoencoder with 60% random masking."""
import numpy as np
import torch
from torch import nn
from .hybrid_3d_network import neighbor_indices,mlp,MultiTaskVoxelSceneFlowNet,POINTNET_FPS_CAP,POINTNET_RADII

MAE_PATCH_POINTS=16
MAE_CENTER_CAP=96


class PointMaskedAutoencoder(nn.Module):
    def __init__(self,patch_points=MAE_PATCH_POINTS,hidden=256):
        super().__init__();self.patch_points=patch_points
        self.backbone=MultiTaskVoxelSceneFlowNet()
        self.position=mlp([3,64,hidden])
        self.encoder=nn.TransformerEncoder(nn.TransformerEncoderLayer(hidden,4,256,batch_first=True,dropout=0),2)
        self.decoder=nn.TransformerDecoder(nn.TransformerDecoderLayer(hidden,4,256,batch_first=True,dropout=0),2)
        self.mask_token=nn.Parameter(torch.zeros(1,1,hidden));self.reconstruct=nn.Linear(hidden,patch_points*3)
    def forward(self,points,mask_ratio=.6,generator=None):
        if len(points)<4:raise ValueError('MAE needs at least four observed points')
        centers=points[torch.linspace(0,len(points)-1,min(MAE_CENTER_CAP,max(4,len(points)//16)),device=points.device).long()]
        idx=neighbor_indices(centers,points,self.patch_points)
        if idx.shape[1]<self.patch_points:idx=idx[:,torch.arange(self.patch_points,device=points.device)%idx.shape[1]]
        patches=points[idx]-centers[:,None]
        order=torch.randperm(len(centers),device=points.device,generator=generator)
        nmask=max(1,min(len(centers)-1,int(len(centers)*mask_ratio)))
        mask=order[:nmask];visible=order[nmask:]
        local=patches[visible]
        # Same learned point encoder and spatial hierarchy used by downstream model.
        padded=torch.cat([local,torch.zeros((*local.shape[:2],9),device=points.device)],-1)
        tokens=self.backbone.vfe.point(padded).max(1).values
        tokens=self.backbone.stage1(centers[visible],tokens)
        tokens=self.backbone.stage2(centers[visible],tokens)
        memory=self.encoder((tokens+self.position(centers[visible]))[None])
        queries=self.mask_token.expand(1,nmask,-1)+self.position(centers[mask])[None]
        decoded=self.decoder(queries,memory)
        recon=self.reconstruct(decoded)[0].reshape(nmask,self.patch_points,3)
        # Local patch matrices only: bounded by patch_points squared, never full cloud squared.
        d=torch.cdist(recon,patches[mask]).square()
        loss=d.min(1).values.mean()+d.min(2).values.mean()
        return loss,recon,patches[mask]


def pretrain(frames,output,epochs=2,device='cpu',seed=7):
    torch.manual_seed(seed);model=PointMaskedAutoencoder().to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=1e-3)
    history=[]
    for epoch in range(epochs):
        losses=[]
        for points in frames:
            p=torch.as_tensor(np.asarray(points)[:,:3].copy(),dtype=torch.float32,device=device)
            optimizer.zero_grad();loss,_,_=model(p);loss.backward();optimizer.step();losses.append(float(loss.detach()))
        history.append(float(np.mean(losses)))
    torch.save({'model':model.state_dict(),'backbone':model.backbone.state_dict(),'history':history,'metadata':{'seed':seed,'mask_ratio':.6,'task':'Point-MAE',
        'sampling_constants':{'pointnet_fps_cap':POINTNET_FPS_CAP,'pointnet_radii_m':list(POINTNET_RADII),
            'mae_patch_points':model.patch_points,'mae_center_cap':MAE_CENTER_CAP}}},output)
    return history
