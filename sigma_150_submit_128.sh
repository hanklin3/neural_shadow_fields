#!/bin/bash
#SBATCH -n 20 --gres=gpu:volta:1

# Loading the required module
source /etc/profile
#module load anaconda/2021a #cuda/11.1
source activate nerf_pl

# Run the script
python train_efficient_sm.py --dataset_name efficient_sm --root_dir ../../datasets/volumetric/results_500_v2_distance_transform_150/ --N_importance 64 --N_samples 64 --num_gpus 0 --img_wh 128 128 --noise_std 0 --num_epochs 200 --batch_size 1024 --optimizer adam --lr 0.00001 --exp_name NEW_2__SIGMA_Nimp_64_128x128_grad_on_light_LN_32_shadowmethod2 --Light_N_importance 32 --shadow_method shadow_method_2 --grad_on_light > ./sigma/NEW_2__SIGMA_Nimp_64_128x128_grad_on_light_LN_32_shadowmethod2.log