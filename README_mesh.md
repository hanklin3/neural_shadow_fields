## From kwea123
# Reconstruct mesh

Use `extract_mesh.ipynb` (the notebook, **not** the py script) to extract **colored** mesh. The guideline for choosing good parameters is commented in the notebook.
Here, I'll give detailed explanation of how it works. There is also a [video](https://youtu.be/t06qu-gXrxA) that explains the same thing.

## Step 1. Predict occupancy

As the [original repo](https://github.com/bmild/nerf/blob/master/extract_mesh.ipynb), we need to first infer which locations are occupied by the object. This is done by first create a grid volume in the form of a cuboid covering the whole object, then use the nerf model to predict whether a cell is occupied or not. This is the main reason why mesh construction is only available for 360 inward-facing scenes as forward facing scenes would require a **huge** volume to cover the whole space! It is computationally impossible to predict the occupancy for all cells.

## Step 2. Perform marching cube algorithm

After we know which cells are occupied, we can use [marching cube algorithm](https://en.wikipedia.org/wiki/Marching_cubes) to extract mesh. This mesh will only contain vertices and faces, if you don't require color, you can stop here and export the mesh. Until here, the code is the same as the original repo.
