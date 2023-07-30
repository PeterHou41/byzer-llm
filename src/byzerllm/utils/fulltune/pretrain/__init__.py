from typing import List, Optional, Tuple,Any,Dict
from transformers import AutoTokenizer, AutoModelForCausalLM,BitsAndBytesConfig
import ray
import torch
import deepspeed
import deepspeed.comm as dist
import sentencepiece as spm
import numpy as np
import json
import os
from ray.air.util.torch_dist import (
    ActorHandle,
    _get_node_and_gpu_ids,
    _init_torch_distributed,
    get_address_and_port,
)
import dataclasses
from ray.train.constants import DEFAULT_NCCL_SOCKET_IFNAME
from ..base_model.modeling_baichuan import BaiChuanForCausalLM
from ..base_model.configuration_baichuan import BaiChuanConfig


DEFUALT_CONFIG = '''
{
  "gradient_accumulation_steps": 1,
  "train_micro_batch_size_per_gpu": 1,
  "prescale_gradients": false,
  "zero_allow_untested_optimizer": true,
  "optimizer": {
    "type": "AdamW",
    "params": {
      "lr": 1e-8,
      "eps": 1.0e-8,
      "betas": [
        0.9,
        0.95
      ],
      "weight_decay": 0.1
    }
  },
  "tensorboard": {
    "enabled": true,
    "output_path": "logs/",
    "job_name": "baichuan-7b-pt"
  },
  "zero_optimization": {
    "stage": 2,
    "offload_optimizer": {
         "device": "cpu"
     },
    "contiguous_gradients": true,
    "allgather_bucket_size": 1e8,
    "reduce_bucket_size": 1e8,
    "overlap_comm": true,
    "reduce_scatter": true
  },
  "steps_per_print": 16,
  "gradient_clipping": 1.0,
  "wall_clock_breakdown": true,
  "bf16": {
    "enabled": true
  }
}

'''

@dataclasses.dataclass
class TrainArgs:
    steps_per_epoch: int = 4096
    checkpoint_saving_path: str = "/mnt/nvme0n1/byzerllm/data/checkpoints"
    max_length: int = 1024
    data_dir: str = "/home/byzerllm/data/raw_data"
    model_path: str = "/home/byzerllm/models/baichuan-7B"
    tokenizer_path: str = "/home/byzerllm/models/baichuan-7B/tokenizer.model"

@dataclasses.dataclass
class DeviceID:
    node_id: int
    gpu_ids: List[int]
    rank: int

class DataEngine():
    def __init__(self, data_dir, tokenizer_path, micro_batch_size, max_length):
        self.MIN_TEXT_LEN = 20
        self.EOS_TOKEN_ID = 2
        self.data_dir = data_dir
        self.sp = spm.SentencePieceProcessor()
        self.sp.Load(tokenizer_path)
        self.micro_batch_size = micro_batch_size
        self.max_length = max_length
        self.data = []
        self.global_input_paths = [self.data_dir + "/" + x
                                   for x in os.listdir(self.data_dir)]
        self.local_input_paths = [x for i, x in
                                  enumerate(self.global_input_paths)
                                  if i % dist.get_world_size() == dist.get_rank()]

    def load_data(self):
        for file_path in self.local_input_paths:
            data = []
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                for line_id, line in enumerate(f):
                    cc = self.sp.EncodeAsIds(line.strip()) + [self.EOS_TOKEN_ID]
                    if len(cc) < self.MIN_TEXT_LEN:
                        cc = []
                    data.extend(cc)
                    if len(data) >= self.micro_batch_size * (self.max_length + 1):
                        index = self.micro_batch_size * (self.max_length + 1)
                        self.data.append(data[:index])
                        data = []
        return

    def get_data(self):
        data = self.data.pop(0)
        seq = np.asarray(data).reshape(self.micro_batch_size, self.max_length + 1)
        data = torch.LongTensor(seq)
        data = data.cuda(non_blocking=True)
        return data

class ParallelConfig:
    """Configuration for the distributed execution.    
    """

    def __init__(
        self,
        num_workers:int,            
        ds_config:Dict[Any,Any], 
        gpu_ids: List[int] = [],
        train_args = TrainArgs(),     
        backend: str = "nccl",              
    ) -> None:
        self.world_size = num_workers        
        self.backend = backend
        self.ds_config = ds_config if ds_config else json.loads(DEFUALT_CONFIG)
        self.train_args = train_args
    
def _init_distributed_environment(
        parallel_config: ParallelConfig,
        rank: int,
        distributed_init_method: str,
        gpu_ids: List[int],
    ) -> None:        
        if parallel_config.backend == "nccl":
            # Same as in Ray Train
            os.environ["NCCL_ASYNC_ERROR_HANDLING"] = "1"
            # All workers on a same node should share the same set of
            # visible GPUs. Otherwise they can't talk among themselves.
            os.environ["CUDA_VISIBLE_DEVICES"] = ",".join(str(gid) for gid in gpu_ids)
            if "NCCL_SOCKET_IFNAME" not in os.environ:
                os.environ["NCCL_SOCKET_IFNAME"] = DEFAULT_NCCL_SOCKET_IFNAME

        os.environ["RANK"] = str(rank)
        os.environ["LOCAL_RANK"] = str(rank)
        os.environ["WORLD_SIZE"] = str(parallel_config.world_size)
        os.environ["LOCAL_WORLD_SIZE"] = str(parallel_config.world_size)        
        print(f'deepspeed inference worker after:rank:{rank} CUDA_VISIBLE_DEVICES:{os.environ["CUDA_VISIBLE_DEVICES"]}',flush=True)
        # torch.cuda.set_device(rank)
        """Initialize the distributed environment."""
        deepspeed.init_distributed(
            dist_backend="nccl",
            auto_mpi_discovery=False,
            verbose=True,
            init_method=distributed_init_method,
            rank=rank,
            world_size=parallel_config.world_size,
        )
        # torch.distributed.init_process_group(
        #     backend="nccl",
        #     world_size=parallel_config.world_size,
        #     rank=rank,
        #     init_method=distributed_init_method,            
        # )
        # # A small all_reduce for warmup.
        # torch.distributed.all_reduce(torch.zeros(1).cuda())

        


class Worker:
    
    def __init__(
        self,        
        parallel_config: ParallelConfig,        
        rank: int,
        distributed_init_method: str,
       
    ) -> None:
        self.parallel_config = parallel_config        
        self.rank = rank
        self.distributed_init_method = distributed_init_method        
        self.ds_config = self.parallel_config.ds_config
        self.model = None
        self.tokenizer = None
    
    def get_node_and_gpu_ids(self):
        """Returns the node and GPU ids of the current worker."""
        node_id, gpu_ids = _get_node_and_gpu_ids()
        return DeviceID(node_id, gpu_ids, self.rank)

    def _train(self,data_engine, model_engine):
        model_engine.train()
        step = 0
        while step < self.parallel_config.train_args.steps_per_epoch:
            data = data_engine.get_data()
            loss = model_engine(data, labels=data).loss
            model_engine.backward(loss)
            model_engine.step()
            step += 1
        return   
    
    def train(self):        
        model_engine = self.prepare_model([0,1,2,3])
        data_engine = self.prepare_data()
        epoch = 0
        while True:
            self._train(data_engine, model_engine)
            epoch += 1
            model_engine.save_checkpoint(f"{self.parallel_config.train_args.checkpoint_saving_path}",
                                        tag=f"Epoch-{epoch}")
    def prepare_data(self):
        data_dir = self.parallel_config.train_args.data_dir
        tokenizer_path = self.parallel_config.train_args.tokenizer_path        
        micro_batch_size = self.ds_config["train_micro_batch_size_per_gpu"]
        max_length = self.parallel_config.train_args.max_length
        data_engine = DataEngine(data_dir, tokenizer_path, micro_batch_size, max_length)
        data_engine.load_data()
        return data_engine
    
    def set_ds_envs(self,gpu_ids:List[int],local_rank:int,local_world_size:int):        
        os.environ["CUDA_VISIBLE_DEVICES"] = ",".join(str(gid) for gid in gpu_ids)
        os.environ["LOCAL_RANK"] = str(local_rank)
        os.environ["LOCAL_WORLD_SIZE"] = str(local_world_size)                  
        print(f'''
deepspeed worker config:
              RANK:{self.rank} 
              WORLD_SIZE:{self.parallel_config.world_size}
              CUDA_VISIBLE_DEVICES:{os.environ["CUDA_VISIBLE_DEVICES"]} 
              LOCAL_RANK:{os.environ["LOCAL_RANK"]}
              LOCAL_WORLD_SIZE:{os.environ["LOCAL_WORLD_SIZE"]}
''',flush=True)  
    
    def prepare_model(self,gpu_ids:List[int]):
        # Initialize the distributed environment.
        _init_distributed_environment(self.parallel_config, self.rank,
                                      self.distributed_init_method,gpu_ids)
        
        # check the enabled parameter here: https://github.com/microsoft/DeepSpeed/issues/3234
        
        with deepspeed.zero.Init(config_dict_or_path=self.ds_config,
                             enabled=self.ds_config["zero_optimization"]["stage"] == 3,
                             mem_efficient_linear=False,
                             mpu=None):
            model = BaiChuanForCausalLM(BaiChuanConfig())

            model_parameters = filter(lambda p: p.requires_grad, model.parameters())
            model_engine, _, _, _ = deepspeed.initialize(model=model,
                                                         config=self.ds_config,
                                                        optimizer=None,
                                                        model_parameters=model_parameters)
            return model_engine
            
           

class DeepSpeedTrain:
    def __init__(self,parallel_config: ParallelConfig):    

        master_addr, master_port = get_address_and_port()        
        distributed_init_method = f"tcp://{master_addr}:{master_port}"  
        print(f"deepspeed: master_addr:{master_addr},master_port:{master_port}",flush=True)
        workers = []

        # for simplicity, we assume that the gpu_ids are the same for all workers
        gpu_ids = parallel_config.gpu_ids
        gpu_ids_str = ",".join([str(gpu) for gpu in gpu_ids])
        
        for rank in range(parallel_config.world_size):    
            worker_cls = Worker  
            # deepspeed will use rank as the device id, and the 
            # ray will automatically set CUDA_VISIBLE_DEVICES for each worker according to the num_gpus
            # for example, suppose we have 0,1,2,4 gpus, and we have 4 workers, then the CUDA_VISIBLE_DEVICES 
            # for the last worker will be 3, and the deepspeed will use 3 as the device id, which is wrong because
            # he can only see one gpu. So we need to set CUDA_VISIBLE_DEVICES to 0,1,2,3 for each worker.
            runtime_env = {"env_vars": {
              # "RAY_EXPERIMENTAL_NOSET_CUDA_VISIBLE_DEVICES":"true",
              # "CUDA_VISIBLE_DEVICES":gpu_ids_str
            }}    
            worker_cls = ray.remote(
                        num_cpus=0,
                        num_gpus=1,
                        # resources={f"node:{master_addr}": 1e-3},
                        runtime_env=runtime_env,                        
                    )(worker_cls).remote
            worker = worker_cls(parallel_config,rank,distributed_init_method)
            workers.append(worker)
        self.workers  = workers        
        self.node_id_to_workers = {}
        self.node_id_to_gpus = {}
        for worker in self.workers:
            device = ray.get(worker.get_node_and_gpu_ids.remote())
            
            if device.node_id not in self.node_id_to_workers:
                self.node_id_to_workers[device.node_id] = []
            
            if device.node_id not in self.node_id_to_gpus:
                self.node_id_to_gpus[device.node_id] = []    
            
            self.node_id_to_workers[device.node_id].append(worker)    
            self.node_id_to_gpus[device.node_id].extend(device.gpu_ids).sort()

        for node_id, workers in self.node_id_to_workers.items():
            for local_rank,worker in enumerate(workers):                
                ray.get(worker.set_ds_envs.remote(
                    gpu_ids=self.node_id_to_gpus[node_id],
                    local_rank=local_rank,
                    local_world_size=len(self.node_id_to_gpus[node_id])
                ))               
    
              
    def _run_workers(
        self,
        method: str,
        *args,
        get_all_outputs: bool = False,
        **kwargs,
    ) -> Any:
        """Runs the given method on all workers."""
        all_outputs = []
        for worker in self.workers:
            executor = getattr(worker, method)
            executor = executor.remote
            output = executor(*args, **kwargs)
            all_outputs.append(output)

        all_outputs = ray.get(all_outputs)            
        
        if get_all_outputs:
            return all_outputs

        # Make sure all workers have the same results.
        output = all_outputs[0]
        for other_output in all_outputs[1:]:
            assert output == other_output
        return output  



