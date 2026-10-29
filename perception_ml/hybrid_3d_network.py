"""Sparse, local multimodal PointNet++ backbone; no V-by-V distance allocation."""
from __future__ import annotations
import numpy as np
from scipy.spatial import cKDTree
import torch
from torch import nn
from torch.nn import functional as F

POINTNET_FPS_CAP=256
POINTNET_RADII=(.6,1.2)


def neighbor_indices(query, support, k):
    if len(support)==0: return torch.empty((len(query),0),dtype=torch.long,device=query.device)
    _,idx=cKDTree(support.detach().cpu().numpy()).query(query.detach().cpu().numpy(),k=min(k,len(support)))
    return torch.as_tensor(np.asarray(idx).reshape(len(query),-1),device=query.device,dtype=torch.long)


def mlp(widths):
    layers=[]
    for i,(a,b) in enumerate(zip(widths[:-1],widths[1:])):
        layers.append(nn.Linear(a,b))
        if i<len(widths)-2: layers.extend([nn.LayerNorm(b),nn.GELU()])
    return nn.Sequential(*layers)


def _positive_neighbors(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,np.integer)) or value<1:
        raise ValueError(f'{name} must be a positive integer')
    return int(value)


class CameraFeatureCNN(nn.Module):
    def __init__(self,channels=16):
        super().__init__()
        self.encoder=nn.Sequential(nn.Conv2d(4,24,3,padding=1),nn.GroupNorm(4,24),nn.GELU(),
            nn.Conv2d(24,32,3,padding=1),nn.GroupNorm(4,32),nn.GELU(),nn.Conv2d(32,channels,1))
        self.semantic=nn.Conv2d(channels,7,1)
    def forward(self,rgb_depth):
        feature=self.encoder(rgb_depth)
        return feature,self.semantic(feature)


class RangeImageEncoder(nn.Module):
    def __init__(self,channels=16,height=24,width=96):
        super().__init__()
        self.height=_positive_neighbors(height,'range_image_height')
        self.width=_positive_neighbors(width,'range_image_width')
        self.encoder=nn.Sequential(nn.Conv2d(4,24,3,padding=1),nn.GELU(),nn.Conv2d(24,channels,3,padding=1))
    def forward(self,coords,features):
        height,width=self.height,self.width
        if len(coords)==0: return coords.new_empty((0,16))
        r=coords.norm(dim=1).clamp_min(1e-5)
        u=((torch.atan2(coords[:,1],coords[:,0])+torch.pi)/(2*torch.pi)*width).long().clamp(0,width-1)
        v=((torch.asin((coords[:,2]/r).clamp(-1,1))+torch.pi/2)/torch.pi*height).long().clamp(0,height-1)
        idx=v*width+u
        vals=torch.stack([r/12,features[:,3],coords[:,2]/4,torch.ones_like(r)],1)
        img=coords.new_zeros((height*width,4)); count=coords.new_zeros((height*width,1))
        img.index_add_(0,idx,vals); count.index_add_(0,idx,torch.ones_like(r[:,None]))
        img=(img/count.clamp_min(1)).T.reshape(1,4,height,width)
        out=self.encoder(img)[0].permute(1,2,0)
        return out[v,u]


class LearnedVoxelEncoder(nn.Module):
    def __init__(self):
        super().__init__(); self.point=mlp([12,64,64])
    def forward(self,features,mapping,n):
        out=features.new_full((n,64),-torch.inf)
        if len(features):
            values=self.point(features)
            out.scatter_reduce_(0,mapping[:,None].expand(-1,64),values,reduce='amax',include_self=True)
        return torch.where(torch.isfinite(out),out,torch.zeros_like(out))


class SparseVoxelKNNLayer(nn.Module):
    def __init__(self,in_channels,out_channels,k_neighbors=16):
        super().__init__(); self.k=_positive_neighbors(k_neighbors,'k_neighbors')
        self.edge=mlp([in_channels*2+9,out_channels,out_channels])
        self.attn=mlp([in_channels+9,32,1])
        self.shortcut=nn.Linear(in_channels,out_channels)
    def forward(self,coords,features,indices=None):
        if not len(coords): return self.shortcut(features)
        idx=neighbor_indices(coords,coords,self.k) if indices is None else indices
        rel=coords[idx]-coords[:,None]
        pe=torch.cat([rel,torch.sin(rel*torch.pi),torch.cos(rel*torch.pi)],-1)
        center=features[:,None].expand(-1,idx.shape[1],-1)
        edge=self.edge(torch.cat([center,features[idx]-center,pe],-1))
        weights=self.attn(torch.cat([features[idx],pe],-1)).softmax(1)
        return self.shortcut(features)+(weights*edge).sum(1)+edge.max(1).values


class MultiScaleSetAbstraction(nn.Module):
    """Deterministic FPS, radius neighborhoods and three-neighbor propagation."""
    def __init__(self,in_channels=128,out_channels=256,neighbors=16):
        super().__init__()
        self.k=_positive_neighbors(neighbors,'neighbors')
        self.scales=nn.ModuleList([mlp([in_channels+3,128,out_channels//2]) for _ in range(2)])
        self.fuse=mlp([out_channels,out_channels,out_channels])
    def forward(self,coords,features,spatial_indices=None):
        n=len(coords)
        if n==0: return features.new_empty((0,256))
        if spatial_indices is None:
            m=min(POINTNET_FPS_CAP,max(1,n//4))
            xyz=coords.detach().cpu().numpy(); dist=np.full(n,np.inf); ids=[]; current=0
            for _ in range(m):
                ids.append(current); dist=np.minimum(dist,np.sum((xyz-xyz[current])**2,1)); current=int(dist.argmax())
            ids=torch.as_tensor(ids,device=coords.device)
        else:ids=spatial_indices.fps
        centers=coords[ids]
        values=[]
        for scale,(net,k,radius) in enumerate(zip(self.scales,(self.k,2*self.k),POINTNET_RADII)):
            idx=neighbor_indices(centers,coords,k) if spatial_indices is None else (spatial_indices.scale_small if scale==0 else spatial_indices.scale_large)
            rel=coords[idx]-centers[:,None]
            encoded=net(torch.cat([features[idx],rel],-1))
            valid=rel.norm(dim=-1)<=radius
            encoded=encoded.masked_fill(~valid[:,:,None],-1e6)
            values.append(encoded.max(1).values)
        sampled=self.fuse(torch.cat(values,-1))
        idx=neighbor_indices(coords,centers,3) if spatial_indices is None else spatial_indices.propagation
        d=(coords[:,None]-centers[idx]).norm(dim=-1)
        w=(1/(d+1e-6)); w=w/w.sum(1,keepdim=True)
        return (sampled[idx]*w[:,:,None]).sum(1)


class CrossFrameFlowCorrelation(nn.Module):
    def __init__(self,feat_dim=256,k_flow=12):
        super().__init__(); self.k=_positive_neighbors(k_flow,'k_flow')
        self.q=nn.Linear(feat_dim,64); self.key=nn.Linear(feat_dim,64)
        self.msg=mlp([feat_dim+3,feat_dim,feat_dim])
    def forward(self,coords,features,previous_coords=None,previous_features=None,indices=None):
        if previous_coords is None or not len(previous_coords):
            return features,torch.zeros_like(coords),torch.zeros((len(coords),1),device=coords.device)
        idx=neighbor_indices(coords,previous_coords,self.k) if indices is None else indices
        disp=coords[:,None]-previous_coords[idx]
        score=(self.q(features)[:,None]*self.key(previous_features[idx])).sum(-1)/8
        score=score-disp.square().sum(-1)/.08
        weight=score.softmax(1)
        msg=(self.msg(torch.cat([previous_features[idx],disp],-1))*weight[:,:,None]).sum(1)
        base=(weight[:,:,None]*disp).sum(1)
        validity=(disp.norm(dim=-1).min(1).values<1.).float()[:,None]
        return features+msg,base*validity,validity


class MultiTaskVoxelSceneFlowNet(nn.Module):
    def __init__(self,voxel_in_dim=12,eigen_in_dim=10,num_classes=7,neighbors=16,temporal_neighbors=12,
                 range_image_height=24,range_image_width=96):
        super().__init__()
        self.neighbors=_positive_neighbors(neighbors,'neighbors')
        self.temporal_neighbors=_positive_neighbors(temporal_neighbors,'temporal_neighbors')
        self.camera=CameraFeatureCNN(16); self.range=RangeImageEncoder(16,range_image_height,range_image_width)
        self.vfe=LearnedVoxelEncoder()
        self.stem=mlp([voxel_in_dim+eigen_in_dim+2,64,64])
        self.query=nn.Linear(64,64); self.modality=nn.Linear(16,64)
        self.cross_attention=nn.MultiheadAttention(64,4,batch_first=True)
        self.stage1=SparseVoxelKNNLayer(64,128,self.neighbors)
        self.stage2=MultiScaleSetAbstraction(128,256,self.neighbors)
        self.flow_corr=CrossFrameFlowCorrelation(256,self.temporal_neighbors)
        self.semantic_head=mlp([384,128,num_classes])
        self.scene_flow_head=mlp([388,128,3])
        nn.init.zeros_(self.scene_flow_head[-1].weight); nn.init.zeros_(self.scene_flow_head[-1].bias)
        self.register_buffer('feature_scale',torch.tensor([6,6,3,1,1,4,1,4,1,1,1,3],dtype=torch.float32))
    def forward(self,coords_t,voxel_feats_t,eigen_feats_t,coords_prev=None,cached_stage2_prev=None,
                context=None,point_features=None,point_to_voxel=None,camera_features=None,sensor_coords=None,spatial_indices=None):
        n=len(coords_t)
        if not n:
            return coords_t.new_empty((0,7)),coords_t.new_empty((0,3)),coords_t.new_empty((0,256))
        if context is None: context=coords_t.new_zeros((n,2))
        x=self.stem(torch.cat([voxel_feats_t/self.feature_scale,eigen_feats_t,context],-1))
        if point_features is not None: x=x+self.vfe(point_features,point_to_voxel,n)
        rf=self.range(coords_t if sensor_coords is None else sensor_coords,voxel_feats_t)
        cf=torch.zeros_like(rf) if camera_features is None else camera_features
        tokens=self.modality(torch.stack([cf,rf],1))
        attn,_=self.cross_attention(self.query(x)[:,None],tokens,tokens,need_weights=False)
        x=x+attn[:,0]
        x1=self.stage1(coords_t,x,None if spatial_indices is None else spatial_indices.stage1)
        x2=self.stage2(coords_t,x1,spatial_indices)
        temporal,base,valid=self.flow_corr(coords_t,x2,coords_prev,cached_stage2_prev,None if spatial_indices is None else spatial_indices.temporal)
        multi=torch.cat([temporal,x1],-1)
        logits=self.semantic_head(multi)
        flow=base+self.scene_flow_head(torch.cat([multi,base,valid],-1))
        return logits,flow,x2


def lovasz_softmax(logits,labels):
    """Multiclass Lovasz extension of mean IoU; absent classes are excluded."""
    probs=logits.softmax(-1); losses=[]
    for c in range(logits.shape[1]):
        fg=(labels==c).float()
        if fg.sum()==0: continue
        errors=(fg-probs[:,c]).abs(); errors,perm=errors.sort(descending=True); fg=fg[perm]
        intersection=fg.sum()-fg.cumsum(0); union=fg.sum()+(1-fg).cumsum(0)
        grad=1-intersection/union.clamp_min(1e-6)
        grad=torch.cat([grad[:1],grad[1:]-grad[:-1]])
        losses.append(torch.dot(errors,grad))
    return torch.stack(losses).mean() if losses else logits.sum()*0


def multitask_loss(logits,flow,labels,target_flow,flow_valid,coords,instances=None):
    if not len(logits): return logits.sum()*0,{}
    counts=torch.bincount(labels,minlength=7).float()
    weights=(counts.sum()/counts.clamp_min(1)).sqrt(); weights=(weights/weights.mean()).clamp(.1,5)
    ce=F.cross_entropy(logits,labels,reduction='none',weight=weights)
    p=logits.softmax(-1).gather(1,labels[:,None])[:,0]
    semantic=((1-p).square()*ce).mean()+lovasz_softmax(logits,labels)
    valid=flow_valid.bool()
    dynamic=(labels==2)|(labels==3)
    flow_weights=torch.where(dynamic,10.,1.)
    err=F.smooth_l1_loss(flow,target_flow,reduction='none',beta=.05).mean(1)
    motion=(err[valid]*flow_weights[valid]).mean() if valid.any() else flow.sum()*0
    rigid=flow.sum()*0
    if instances is not None:
        idx=neighbor_indices(coords,coords,8)
        same=(instances[:,None]==instances[idx])&(instances[:,None]>=0)
        if same.any(): rigid=(flow[:,None]-flow[idx]).square().sum(-1)[same].mean()
    total=semantic+5*motion+.05*rigid
    return total,dict(semantic=float(semantic.detach()),flow=float(motion.detach()),rigidity=float(rigid.detach()))
