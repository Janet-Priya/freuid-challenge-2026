import os
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torchvision import transforms
import timm
from PIL import Image
from tqdm import tqdm
from torch.utils.data import Dataset, DataLoader

DATA_DIR   = '/data'
OUTPUT_DIR = '/submissions'
MODEL_DIR  = '/app/models'
IMG_SIZE   = 320
BATCH_SIZE = 16
DEVICE     = 'cuda' if torch.cuda.is_available() else 'cpu'

class FREUIDModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.backbone = timm.create_model('efficientnet_b2', pretrained=False, num_classes=0)
        feat_dim = self.backbone.num_features
        self.classifier = nn.Sequential(
            nn.Linear(feat_dim, 256), nn.ReLU(),
            nn.Dropout(0.5), nn.Linear(256, 1))
    def forward(self, img):
        return self.classifier(self.backbone(img)).squeeze(1)

class TestDataset(Dataset):
    def __init__(self, paths, transform):
        self.paths = paths
        self.transform = transform
    def __len__(self): return len(self.paths)
    def __getitem__(self, idx):
        path = self.paths[idx]
        img_id = os.path.splitext(os.path.basename(path))[0]
        img = self.transform(Image.open(path).convert('RGB'))
        return img, img_id

transform = transforms.Compose([
    transforms.Resize((IMG_SIZE, IMG_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize([0.485,0.456,0.406],[0.229,0.224,0.225])
])

models = []
for fold in range(5):
    path = os.path.join(MODEL_DIR, f'best_model_unfreeze3456_fold{fold}.pth')
    m = FREUIDModel().to(DEVICE)
    m.load_state_dict(torch.load(path, map_location=DEVICE))
    m.eval()
    models.append(m)
    print(f"Loaded fold {fold}")

exts = {'.jpeg','.jpg','.png','.webp','.bmp','.tif','.tiff'}
paths = [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR)
         if os.path.splitext(f)[1].lower() in exts]
print(f"Found {len(paths)} images")

dataset = TestDataset(paths, transform)
loader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=4)

preds = {}
with torch.no_grad():
    for imgs, ids in tqdm(loader):
        imgs = imgs.to(DEVICE)
        batch = np.zeros(len(ids))
        for m in models:
            batch += torch.sigmoid(m(imgs)).cpu().numpy()
        batch /= len(models)
        for i, img_id in enumerate(ids):
            preds[img_id] = float(batch[i])

os.makedirs(OUTPUT_DIR, exist_ok=True)
pd.DataFrame(list(preds.items()), columns=['id','label'])\
  .to_csv(os.path.join(OUTPUT_DIR, 'submission.csv'), index=False)
print(f"Saved {len(preds)} predictions")
