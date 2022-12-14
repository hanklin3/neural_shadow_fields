import os, sys

import imageio
from opt import get_opts
import torch
from collections import defaultdict

from torch.utils.data import DataLoader
from datasets import dataset_dict

# models
from models.nerf import Embedding, NeRF
from models.rendering_shadows import render_rays, shadow_mapping

# optimizer, scheduler, visualization
from utils import *

# losses
from losses import loss_dict

# metrics
from metrics import *

# pytorch-lightning
from pytorch_lightning.callbacks import ModelCheckpoint
from pytorch_lightning import LightningModule, Trainer
from pytorch_lightning.logging import TestTubeLogger

import torch.multiprocessing
torch.multiprocessing.set_sharing_strategy('file_system')

to8b = lambda x : (255*np.clip(x,0,1)).astype(np.uint8)

class NeRFSystem(LightningModule):
    def __init__(self, hparams):
        super(NeRFSystem, self).__init__()
        self.hparams = hparams

        self.loss = loss_dict[hparams.loss_type]()

        self.embedding_xyz = Embedding(3, 10) # 10 is the default number
        self.embedding_dir = Embedding(3, 4) # 4 is the default number
        self.embeddings = [self.embedding_xyz, self.embedding_dir]

        self.nerf_coarse = NeRF()
        self.models = [self.nerf_coarse]
        if hparams.N_importance > 0:
            self.nerf_fine = NeRF()
            self.models += [self.nerf_fine]

    def decode_batch(self, batch):
        rays = batch['rays'].view(-1, 8) # (B, 8)
        rgbs = batch['rgbs'].view(-1, 3) # (B, 3)
        light_rays = batch['light_rays'].view(-1, 8) # (B, 8)
        light_ppc = batch['light_ppc'] # dict
        ppc = batch['ppc'] # dict
        c2w = batch['c2w'] # (B, 8)
        print("rays.shape {}, rgb.shape {}".format(rays.shape, rgbs.shape))
        # print("light_rays: {}, light_ppc: {}".format(light_rays.shape, light_ppc))
        # print("ppc: {}".format(ppc))
        return rays, rgbs, light_rays, light_ppc, ppc, c2w

    def forward(self, rays):
        """Do batched inference on rays using chunk."""
        B = rays.shape[0]
        results = defaultdict(list)
        for i in range(0, B, self.hparams.chunk):
            rendered_ray_chunks = \
                render_rays(self.models,
                            self.embeddings,
                            rays[i:i+self.hparams.chunk],
                            self.hparams.N_samples,
                            self.hparams.use_disp,
                            self.hparams.perturb,
                            self.hparams.noise_std,
                            self.hparams.N_importance,
                            self.hparams.chunk, # chunk size is effective in val mode
                            self.train_dataset.white_back)

            for k, v in rendered_ray_chunks.items():
                results[k] += [v]

        for k, v in results.items():
            # print('start', k, v)
            results[k] = torch.cat(v, 0)
            # print('end', results[k].shape)
        return results

    def prepare_data(self):
        dataset = dataset_dict[self.hparams.dataset_name]
        kwargs = {'root_dir': self.hparams.root_dir,
                  'img_wh': tuple(self.hparams.img_wh), 
                  'hparams': self.hparams
                  }
        if self.hparams.dataset_name == 'llff':
            kwargs['spheric_poses'] = self.hparams.spheric_poses
            kwargs['val_num'] = self.hparams.num_gpus
        self.train_dataset = dataset(split='train', **kwargs)
        self.val_dataset = dataset(split='val', **kwargs)

    def configure_optimizers(self):
        self.optimizer = get_optimizer(self.hparams, self.models)
        scheduler = get_scheduler(self.hparams, self.optimizer)
        
        return [self.optimizer], [scheduler]

    def train_dataloader(self):
        return DataLoader(self.train_dataset,
                          shuffle=True,
                          num_workers=4,
                          batch_size=self.hparams.batch_size,
                          pin_memory=True)

    def val_dataloader(self):
        return DataLoader(self.val_dataset,
                          shuffle=False,
                          num_workers=4,
                          batch_size=1, # validate one image (H*W rays) at a time
                          pin_memory=True)
    
    def training_step(self, batch, batch_nb):
        log = {'lr': get_learning_rate(self.optimizer)}
        rays, rgbs, light_rays, light_ppc, ppc, c2w = self.decode_batch(batch)
        cam_results = self(rays)
        ray_shape = rays.shape
        rays = None
        light_results = self(light_rays)
        light_rays = None

        rays = torch.ones(ray_shape)

        if self.hparams.batch_size == 1: 
            ppc = [ppc]

        cam_results = shadow_mapping(cam_results, light_results, rays, 
                       ppc, light_ppc, 
                       image_shape=self.hparams.img_wh, 
                       batch_size=self.hparams.batch_size, 
                       fine_sampling=(self.hparams.N_importance > 0))

        log['train/loss'] = loss = self.loss(cam_results, rgbs)
        typ = 'fine' if 'rgb_fine' in cam_results else 'coarse'

        with torch.no_grad():
            psnr_ = psnr(cam_results[f'rgb_{typ}'], rgbs)
            log['train/psnr'] = psnr_

        return {'loss': loss,
                'progress_bar': {'train_psnr': psnr_},
                'log': log
               }

    def validation_step(self, batch, batch_nb):
        print("---------------Starting Validation---------------")
        rays, rgbs, light_rays, light_ppc, ppc, c2w = self.decode_batch(batch)
        rays = rays.squeeze() # (H*W,3)
        light_rays = light_rays.squeeze() # (H*W,3)
        rgbs = rgbs.squeeze() # (H*W,3)

        cam_results = self(rays)
        light_results = self(light_rays)

        cam_results = shadow_mapping(cam_results, light_results, rays, 
                       [ppc], light_ppc, 
                       image_shape=self.hparams.img_wh, 
                       batch_size=1, # eval batch size is always 1 
                       fine_sampling=(self.hparams.N_importance > 0))

        log = {'val_loss': self.loss(cam_results, rgbs)}
        typ = 'fine' if 'rgb_fine' in cam_results else 'coarse'
    
        if batch_nb == 0:
            print("---------------Evaluating and saving Images!---------------")
            W, H = self.hparams.img_wh
            img = cam_results[f'rgb_{typ}'].view(H, W, 3).cpu()
            rgb8 = to8b(img.numpy())
            gt8 = to8b(rgbs.view(H, W, 3).cpu().numpy())
            img = img.permute(2, 0, 1) # (3, H, W)
            img_gt = rgbs.view(H, W, 3).permute(2, 0, 1).cpu() # (3, H, W)
            depth8 = visualize_depth(cam_results[f'depth_{typ}'].view(H, W), to_tensor=False) 
            depth = visualize_depth(cam_results[f'depth_{typ}'].view(H, W)) # (3, H, W)
            if not os.path.exists(f'logs_sm/{self.hparams.exp_name}/imgs'):
                os.mkdir(f'logs_sm/{self.hparams.exp_name}/imgs')
            filename = os.path.join(f'logs_sm/{self.hparams.exp_name}/imgs', 'gt_{:03d}.png'.format(self.current_epoch))
            imageio.imwrite(filename, gt8)
            filename = os.path.join(f'logs_sm/{self.hparams.exp_name}/imgs', 'rgb_{:03d}.png'.format(self.current_epoch))
            imageio.imwrite(filename, rgb8)
            filename = os.path.join(f'logs_sm/{self.hparams.exp_name}/imgs', 'depth_{:03d}.png'.format(self.current_epoch))
            imageio.imwrite(filename, depth8)

            stack = torch.stack([img_gt, img, depth]) # (3, 3, H, W)
            self.logger.experiment.add_images('val/GT_pred_depth',
                                               stack, self.global_step)

        log['val_psnr'] = psnr(cam_results[f'rgb_{typ}'], rgbs)
        return log

    def validation_epoch_end(self, outputs):
        mean_loss = torch.stack([x['val_loss'] for x in outputs]).mean()
        mean_psnr = torch.stack([x['val_psnr'] for x in outputs]).mean()

        return {'progress_bar': {'val_loss': mean_loss,
                                 'val_psnr': mean_psnr},
                'log': {'val/loss': mean_loss,
                        'val/psnr': mean_psnr}
               }


if __name__ == '__main__':
    hparams = get_opts()
    system = NeRFSystem(hparams)
    checkpoint_callback = ModelCheckpoint(filepath=os.path.join(f'ckpts_sm/{hparams.exp_name}',
                                                                '{epoch:d}'),
                                          monitor='val/loss',
                                          mode='min',
                                          save_top_k=5,)

    logger = TestTubeLogger(
        save_dir="logs_sm",
        name=hparams.exp_name,
        debug=False,
        create_git_tag=False
    )

    trainer = Trainer(max_epochs=hparams.num_epochs,
                      checkpoint_callback=checkpoint_callback,
                      resume_from_checkpoint=hparams.ckpt_path,
                      logger=logger,
                      early_stop_callback=None,
                      weights_summary=None,
                      progress_bar_refresh_rate=1,
                      gpus=hparams.num_gpus,
                      distributed_backend='ddp' if len(hparams.num_gpus)>1 else None,
                      num_sanity_val_steps=hparams.num_sanity_val_steps,
                      benchmark=True,
                      profiler=hparams.num_gpus==1, 
                      auto_scale_batch_size=False)

    trainer.fit(system)