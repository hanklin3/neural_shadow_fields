import torch
from torch.utils.data import Dataset
import json
import numpy as np
import os
from PIL import Image
from torchvision import transforms as T
from models.camera import Camera

from .ray_utils import *

class BlenderEfficientShadows(Dataset):
    def __init__(self, root_dir, split='train', img_wh=(800, 800), hparams=None):
        self.root_dir = root_dir
        self.split = split
        assert img_wh[0] == img_wh[1], 'image width must equal image height!'
        self.img_wh = img_wh
        print("Training Image size:", img_wh)
        self.define_transforms()

        self.white_back = True
        # self.white_back = False # Setting it to False (!)
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

        self.light_camera_focal = 0.5*800/np.tan(0.5*self.meta['light_camera_angle_x']) # original focal length
                                                                     # when W=800
        self.light_camera_focal *= self.img_wh[0]/800 # modify focal length to match size self.img_wh

        # bounds, common for all scenes
        self.near = 1.0
        self.far = 200.0

        # probably need to change this 
        self.light_near = 1.0
        self.light_far = 200.0

        self.bounds = np.array([self.near, self.far])
        
        # ray directions for all pixels, same for all images (same H, W, focal)
        self.directions = \
            get_ray_directions(h, w, self.focal) # (h, w, 3)
        
        ### Light Camera Matrix 
        pose = np.array(self.meta['light_camera_transform_matrix'])[:3, :4]
        l2w = torch.FloatTensor(pose)
        pixels_u = torch.arange(0, w, 1)
        pixels_v = torch.arange(0, h, 1)
        i, j = np.meshgrid(pixels_v.numpy(), pixels_u.numpy(), indexing='xy')
        i = torch.tensor(i) + 0.5 #.unsqueeze(2) 
        j = torch.tensor(j)+ 0.5 #.unsqueeze(2)
        self.light_pixels = torch.stack([i,j, torch.ones_like(i)], axis=-1).view(-1, 3) # (H*W,3)

        light_directions = get_ray_directions(h, w, self.light_camera_focal) # (h, w, 3)
        rays_o, rays_d = get_rays(light_directions, l2w) # both (h*w, 3)
        self.light_rays = torch.cat([rays_o, rays_d, 
                                        self.light_near*torch.ones_like(rays_o[:, :1]),
                                        self.light_far*torch.ones_like(rays_o[:, :1])],
                                        1) # (h*w, 8)


        hfov = self.meta['light_camera_angle_x'] * 180./np.pi
        self.light_ppc = Camera(hfov, (h, w))
        self.light_ppc.set_pose_using_blender_matrix(l2w)
        ### Light Camera Matrix 

        if self.split == 'train': # create buffer of all rays and rgb data
            self.image_paths = []
            self.poses = []
            self.all_rays = []
            self.all_rgbs = []
            self.all_ppc = []
            self.all_pixels = []
            
            for frame in self.meta['frames']:
                pose = np.array(frame['transform_matrix'])[:3, :4]
                self.poses += [pose]
                c2w = torch.FloatTensor(pose)
                hfov = self.meta['camera_angle_x'] * 180./np.pi
                ppc = Camera(hfov, (h, w))
                ppc.set_pose_using_blender_matrix(c2w)
                self.all_ppc.extend([ppc]*h*w)

                #### change it to load the shadow map
                file_path = frame['file_path'].split('/')
                file_path = 'sm_'+ file_path[-1]
                ################
                image_path = os.path.join(self.root_dir, f"{file_path}.png")
                self.image_paths += [image_path]

                img = Image.open(image_path)
                img = img.resize(self.img_wh, Image.LANCZOS)
                img = self.transform(img) # (4, h, w)
                img = img.view(3, -1).permute(1, 0) # (h*w, 4) RGBA

                # Figure out where the rays originated from 
                pixels_u = torch.arange(0, w, 1)
                pixels_v = torch.arange(0, h, 1)
                i, j = np.meshgrid(pixels_v.numpy(), pixels_u.numpy(), indexing='xy')
                i = torch.tensor(i) + 0.5 #.unsqueeze(2) 
                j = torch.tensor(j)+ 0.5 #.unsqueeze(2)
                pixels = torch.stack([i,j, torch.ones_like(i)], axis=-1).view(-1, 3) # (H*W,3)

                rays_o, rays_d = get_rays(self.directions, c2w)
                rays = torch.cat([rays_o, rays_d, 
                                self.near*torch.ones_like(rays_o[:, :1]),
                                self.far*torch.ones_like(rays_o[:, :1])],
                                1) # (H*W, 8)

                self.all_rgbs += [img]
                self.all_rays += [rays]
                self.all_pixels += [pixels]

            self.all_rays = torch.cat(self.all_rays, 0) # (len(self.meta['frames])*h*w, 3)
            self.all_pixels = torch.cat(self.all_pixels, 0) # (len(self.meta['frames])*h*w, 3)
            self.all_rgbs = torch.cat(self.all_rgbs, 0) # (len(self.meta['frames])*h*w, 3)
            print("self.all_rgbs.shape, self.all_rays.shape, self.all_pixels.shape, all_ppc.shape", 
                    self.all_rgbs.shape, self.all_rays.shape, self.all_pixels.shape, len(self.all_ppc))

    def define_transforms(self):
        self.transform = T.ToTensor()

    def __len__(self):
        if self.split == 'train':
            return len(self.all_rays)
        elif self.split == 'val':
            return 8 # only validate 8 images (to support <=8 gpus)
        else:
            return len(self.meta['frames'])

    def __getitem__(self, idx):
        """
        Processes and return rays, rgbs PER image
        instead of on a ray by ray basis. Albeit slower, 
        Implementation of shadow mapping is easier this way.
        """
        if self.split == 'train': # use data in the buffers
            # pose = self.poses[idx]
            # c2w = torch.FloatTensor(pose)

            sample = {'rays': self.all_rays[idx], # (8) Ray originating from pixel (i,j)
                      'pixels': self.all_pixels[idx], # pixel where the ray originated from 
                      'rgbs': self.all_rgbs[idx], # (h*w,3)
                    #   'ppc': [self.all_ppc[idx].eye_pos, self.all_ppc[idx].camera], 
                    #   'light_ppc': [self.light_ppc.eye_pos, self.light_ppc.camera],
                      'ppc': {
                          'eye_pos': self.all_ppc[idx].eye_pos, 
                          'camera': self.all_ppc[idx].camera,
                      },
                      'light_ppc': {
                          'eye_pos': self.light_ppc.eye_pos, 
                          'camera': self.light_ppc.camera,
                      },
                    #   'c2w': pose, # (3,4)
                    # pixel where the light ray originated from  
                      'light_pixels': self.light_pixels, #(h*w, 3)  
                    # light rays 
                      'light_rays': self.light_rays, #(h*w,8)
                    }

        else: # create data for each image separately
            frame = self.meta['frames'][idx]
            file_path = frame['file_path'].split('/')
            file_path = 'sm_'+ file_path[-1]

            c2w = torch.FloatTensor(frame['transform_matrix'])[:3, :4]
            ###########
            w, h = self.img_wh
            hfov = self.meta['camera_angle_x'] * 180./np.pi
            ppc = Camera(hfov, (h, w))
            ppc.set_pose_using_blender_matrix(c2w)
            ###########
            img = Image.open(os.path.join(self.root_dir, f"{file_path}.png"))
            img = img.resize(self.img_wh, Image.LANCZOS)
            img = self.transform(img) # (3, H, W)
            img = img.view(3, -1).permute(1, 0) # (H*W, 3) RGBA
            # img = img[:, :3]*img[:, -1:] + (1-img[:, -1:]) # blend A to RGB

            pixels_u = torch.arange(0, w, 1)
            pixels_v = torch.arange(0, h, 1)
            i, j = np.meshgrid(pixels_v.numpy(), pixels_u.numpy(), indexing='xy')
            i = torch.tensor(i) + 0.5 #.unsqueeze(2) 
            j = torch.tensor(j)+ 0.5 #.unsqueeze(2)
            pixels = torch.stack([i,j, torch.ones_like(i)], axis=-1).view(-1, 3) # (H*W,3)

            rays_o, rays_d = get_rays(self.directions, c2w)

            rays = torch.cat([rays_o, rays_d, 
                              self.near*torch.ones_like(rays_o[:, :1]),
                              self.far*torch.ones_like(rays_o[:, :1])],
                              1) # (H*W, 8)
            # valid_mask = (img[-1]>0).flatten() # (H*W) valid color area

            sample = {'rays': rays,
                      'pixels': pixels, # pixel where rays originated from 
                      'rgbs': img,
                      'ppc': {
                          'eye_pos': ppc.eye_pos, 
                          'camera': ppc.camera,
                      },
                      'light_ppc': {
                          'eye_pos': self.light_ppc.eye_pos, 
                          'camera': self.light_ppc.camera,
                      },
                    # pixel where the light ray originated from  
                      'light_pixels': self.light_pixels, #(h*w, 3)  
                    # light rays 
                      'light_rays': self.light_rays, #(h*w,8)
                    }

        return sample
