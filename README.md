# Neural Shadow Fields

## Abstract
We present a method that learns neural scene representations from `only` shadows present in the scene. While traditional shape-from-shadow (SfS) algorithms reconstruct geometry from shadows, they assume a fixed scanning setup and fail to generalize to complex scenes. Neural rendering algorithms, on the other hand, rely on photometric consistency between RGB images but largely ignore physical cues such as shadows, which have been shown to provide valuable information about the scene. We observe that shadows are a powerful cue that can constrain neural scene representations to `learn` SfS, and even outperform NeRF to reconstruct otherwise `hidden geometry`. We propose a graphics-inspired differentiable approach to render accurate shadows with volumetric rendering, predicting a shadow map that can be compared to the ground truth shadow. Even with just binary shadow maps, we show that neural rendering can localize the object and estimate coarse geometry. `Our approach reveals that sparse cues in images can be used to estimate geometry using differentiable volumetric rendering`. Moreover, our framework is highly generalizable and can work alongside existing 3D reconstruction techniques that otherwise only use photometric consistency.


Ours rendering algorithm and the shadow renderer is defined `models/rendering_shadows.py` and `models/efficient_shadow_mapping.py`. `models/shadow_mapping_utils.py` also defines a non-efficient renderer to render images as well which can be used to render differntiable shadows in a scene given depth from light and camera of the scene. Currently our implementation only accepts square images. 

Please look at `models/camera.py` to view a custom PPC implementation to encapsulate the projections. The code to generate shadow masks from any rgb image is in `generate_shadow_masks.ipynb` and to generate weighted shadow masks is in the `shadow_weight_mapping.ipynb`. We precompute these to enable faster training. `Memory is chepaer than compute! `

# Training
- `conda create -n nerf_pl python=3.6`
- `conda activate nerf_pl`
- `pip install -r requirements.txt`
- Install `torchsearchsorted` by cd `torchsearchsorted` then `pip install .`
- To train the setup we direct you to the `*.sh` scripts. 



