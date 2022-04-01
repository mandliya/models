# Merlin Models Example Notebooks

The example notebooks demonstrate how to use Merlin Models with TensorFlow on a variety of datasets.

## Inventory

1. **[Getting Started](01-Getting-started.ipynb)**: It provides a getting started with Merlin Models by training [Facebook's DLRM](https://arxiv.org/pdf/1906.00091.pdf) architecture with only 3 commands.

2. **[Merlin Models and NVTabular: Applying To your own dataset](02-Merlin-Models-and-NVTabular-applying-to-your-own-dataset.ipynb)**: It demonstrates how the `schema` connects the ETL and training step. It will help to apply Merlin Models to your own dataset structures.

3. **[Exploring different Ranking Models](03-Exploring-different-models.ipynb)**: The example will explore different ranking model architectures, such as [Neural Collaborative Filtering (NCF)](https://arxiv.org/pdf/1708.05031.pdf), MLP, [DRLM](https://arxiv.org/abs/1906.00091), and [Deep & Cross Network (DCN)](https://arxiv.org/pdf/1708.05123.pdf).

4. **[Retrieval Model](04-Retrieval-Model.ipynb)**: It trains a [Two Tower architecture](https://dl.acm.org/doi/abs/10.1145/3366423.3380130) as a item retrieval task with Merlin Models.

## Running the Example Notebooks

You can run the example notebooks by [installing Merlin Models](https://github.com/NVIDIA-Merlin/models#installation) and other required libraries. Alternatively, Docker containers are available on http://ngc.nvidia.com/catalog/containers/ with pre-installed versions. Depending on which example you want to run, you should use any one of these Docker containers:
- [Merlin-Tensorflow-Training](https://catalog.ngc.nvidia.com/orgs/nvidia/teams/merlin/containers/merlin-tensorflow-training) (contains Merlin Core, Merlin Models, NVTabular and TensorFlow)
- [Merlin-Inference](https://catalog.ngc.nvidia.com/orgs/nvidia/teams/merlin/containers/merlin-tensorflow-inference) (contains Merlin Core, Merlin Models, Merlin Systems, NVTabular, TensorFlow and Triton Inference Server)

To run the example notebooks using docker containers, do the following:

1. Pull the container by running the following command:
   
   ```shell
   docker run --runtime=nvidia --rm -it -p 8888:8888 -p 8797:8787 -p 8796:8786 --ipc=host <docker container> /bin/bash
   ```

   **NOTES**: 
   
   - If you are running on Docker version 19 and higher, change `--runtime=nvidia` to `--gpus all`.
  
  The container will open a shell when the run command execution is completed. You will have to start JupyterLab on the Docker container. It should look similar to this:
   ```
   root@2efa5b50b909:
   ```
   
2. Start the jupyter-lab server by running the following command:
   
   ```
   jupyter-lab --allow-root --ip='0.0.0.0' --NotebookApp.token='<password>'
   ```

3. Open any browser to access the jupyter-lab server using <MachineIP>:8888.

4. Once in the server, navigate to the ```/models/``` directory and try out the examples.


