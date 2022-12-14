import torch
from torch.utils.data import Dataset
import json
import numpy as np
import os
from PIL import Image
from torchvision import transforms as T

from .ray_utils import *

class BlenderDataset(Dataset):
    def __init__(self, root_dir, split='train', img_wh=(800, 800), hparams=None):
        self.root_dir = root_dir
        self.split = split
        assert img_wh[0] == img_wh[1], 'image width must equal image height!'
        self.img_wh = img_wh
        print("Training Image size:", img_wh)
        self.define_transforms()

        # self.white_back = True
        self.white_back = False # Setting it to False (!)
        self.hparams = hparams
        self.black_and_white = False
        if self.hparams is not None and self.hparams.black_and_white_test:
            self.black_and_white = True
        self.read_meta()

    def read_meta(self):
        with open(os.path.join(self.root_dir,
                               f"transforms_{self.split}.json"), 'r') as f:
            self.meta = json.load(f)

        w, h = self.img_wh
        self.focal = 0.5*800/np.tan(0.5*self.meta['camera_angle_x']) # original focal length
                                                                     # when W=800

        self.focal *= self.img_wh[0]/800 # modify focal length to match size self.img_wh

        # bounds, common for all scenes
        self.near = 1.0
        self.far = 200.0
        print("Z NEAR AND FAR BOUNDS ARE: {},{}".format(self.near, self.far))
        if not (input("PRESS y to continue!!!: ") == 'y'): 
            raise ValueError("Z NEAR AND FAR BOUNDS ARE: {},{}".format(self.near, self.far))
        # bounds, common for all scenes
        # self.near = 2.0
        # self.far = 6.0
        self.bounds = np.array([self.near, self.far])
        
        # ray directions for all pixels, same for all images (same H, W, focal)
        self.directions = \
            get_ray_directions(h, w, self.focal) # (h, w, 3)
            
        if self.split == 'train': # create buffer of all rays and rgb data
            self.image_paths = []
            self.poses = []
            self.all_rays = []
            self.all_rgbs = []
            for frame in self.meta['frames']:
                pose = np.array(frame['transform_matrix'])[:3, :4]
                self.poses += [pose]
                c2w = torch.FloatTensor(pose)

                image_path = os.path.join(self.root_dir, f"{frame['file_path']}.png")
                self.image_paths += [image_path]
                
                img = Image.open(image_path)
                if self.black_and_white:
                    img = img.resize(self.img_wh, Image.LANCZOS).convert('L')
                    img = self.transform(img) # (1, H, W)
                    img = torch.cat([img, img, img], dim=0) # (3, H, W)
                    img = img.view(3, -1).permute(1, 0) # (h*w, 3) RGBA
                else:
                    img = img.resize(self.img_wh, Image.LANCZOS)
                    img = self.transform(img) # (4, h, w)
                    img = img.view(4, -1).permute(1, 0) # (h*w, 4) RGBA
                    img = img[:, :3]*img[:, -1:] + (1-img[:, -1:]) # blend A to RGB

                self.all_rgbs += [img]
                rays_o, rays_d = get_rays(self.directions, c2w) # both (h*w, 3)
                self.all_rays += [torch.cat([rays_o, rays_d, 
                                             self.near*torch.ones_like(rays_o[:, :1]),
                                             self.far*torch.ones_like(rays_o[:, :1])],
                                             1)] # (h*w, 8)

            self.all_rays = torch.cat(self.all_rays, 0) # (len(self.meta['frames])*h*w, 3)
            self.all_rgbs = torch.cat(self.all_rgbs, 0) # (len(self.meta['frames])*h*w, 3)
        
            print("Dataset Shapes cam_rays: {}, rgbs: {}".format(
                self.all_rays.shape, self.all_rgbs.shape))


    def define_transforms(self):
        self.transform = T.ToTensor()

    def __len__(self):
        if self.split == 'train':
            return len(self.all_rays)
        if self.split == 'val':
            return 8 # only validate 8 images (to support <=8 gpus)
        return len(self.meta['frames'])

    def __getitem__(self, idx):
        if self.split == 'train': # use data in the buffers
            sample = {'rays': self.all_rays[idx],
                      'rgbs': self.all_rgbs[idx]}

        else: # create data for each image separately
            frame = self.meta['frames'][idx]
            c2w = torch.FloatTensor(frame['transform_matrix'])[:3, :4]

            img = Image.open(os.path.join(self.root_dir, f"{frame['file_path']}.png"))
            if self.black_and_white:
                img = img.resize(self.img_wh, Image.LANCZOS).convert('L')
                img = self.transform(img) # (1, H, W)
                img = torch.cat([img, img, img], dim=0) # (3, H, W)
                img = img.view(3, -1).permute(1, 0) # (h*w, 3) RGBA
                valid_mask = (img[-1]>0).flatten() # (H*W) valid color area
            else:
                img = img.resize(self.img_wh, Image.LANCZOS)
                img = self.transform(img) # (4, H, W)
                valid_mask = (img[-1]>0).flatten() # (H*W) valid color area
                img = img.view(4, -1).permute(1, 0) # (H*W, 4) RGBA
                img = img[:, :3]*img[:, -1:] + (1-img[:, -1:]) # blend A to RGB
                
            rays_o, rays_d = get_rays(self.directions, c2w)

            rays = torch.cat([rays_o, rays_d, 
                              self.near*torch.ones_like(rays_o[:, :1]),
                              self.far*torch.ones_like(rays_o[:, :1])],
                              1) # (H*W, 8)

            sample = {'rays': rays,
                      'rgbs': img,
                      'c2w': c2w,
                      'valid_mask': valid_mask}

        return sample



# CUDA_LAUNCH_BLOCKING=0 python3 train_shadows.py --root_dir ../datasets/volumetric/results_500/ --dataset blender --num_gpus 4 --exp_name 'blender_cuboid_2_new_znf_bounds' --batch_size 1024 --N_importance 64 --num_epochs 100 --img_wh 400 400 --optimizer adam --lr 5e-4 --lr_scheduler steplr --decay_step 2 4 8 --decay_gamma 0.5