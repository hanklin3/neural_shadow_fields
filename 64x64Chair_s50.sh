#!/bin/bash
#SBATCH -n 2 --gres=gpu:volta:1

# Loading the required module
source /etc/profile
#module load anaconda/2021a #cuda/11.1
source activate nerf_pl

# Run the script
python train_efficient_sm.py --dataset_name efficient_sm\
 --root_dir ../datasets/variable_cam/chair_res800_var_cam_v1_sigma50/\
 --N_importance 128 --N_samples 64\
 --num_gpus 0 --img_wh 64 64 --noise_std 0 --num_epochs 500 --optimizer adam --lr 0.00001\
 --exp_name CHAIR_sigma_50_var_cam_run2_e52 --num_sanity_val_steps 1\
 --Light_N_importance 128 --grad_on_light --batch_size 4096\
 --ckpt_path ./eff_sm_updated_light_matrix_NEW_mar02/ckpts/CHAIR_sigma_50_var_cam_run2_e52/epoch=299.ckpt
