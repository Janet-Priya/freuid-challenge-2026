# FREUID Challenge 2026

## Setup
```bash
pip install -r requirements.txt
```

## Training
```bash
python train_unfreeze456_320.py
```

## Inference
```bash
python final_inference_fast.py
```

## Docker
```bash
docker build -t freuid-repro:local .
docker run --rm --network none --gpus all \
  -v /path/to/test/images:/data:ro \
  -v $(pwd)/out:/submissions \
  freuid-repro:local
```

## Hardware
NVIDIA GTX 1650 (4GB), CUDA 12.1
