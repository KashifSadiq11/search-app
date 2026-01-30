import numpy as np
# services/engine/image_ml.py
import torch
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image
import requests
from io import BytesIO

class ImageSimilarityEngine:
    def __init__(self):
        # Load pre-trained ResNet
        self.model = models.resnet50(pretrained=True)
        self.model.eval()
        
        # Remove final layer for feature extraction
        self.feature_extractor = torch.nn.Sequential(
            *list(self.model.children())[:-1]
        )
        
        self.transform = transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225]
            )
        ])
    
    def extract_image_features(self, image_url: str) -> np.ndarray:
        """Extract features from product image."""
        try:
            response = requests.get(image_url)
            img = Image.open(BytesIO(response.content)).convert('RGB')
            
            img_tensor = self.transform(img).unsqueeze(0)
            
            with torch.no_grad():
                features = self.feature_extractor(img_tensor)
                features = features.squeeze().numpy()
            
            return features
        except:
            return np.zeros(2048)  # ResNet50 feature size
    
    def find_visually_similar(self, target_features, all_features, k=10):
        """Find visually similar products."""
        similarities = []
        
        for item_id, features in all_features.items():
            similarity = np.dot(target_features, features) / (
                np.linalg.norm(target_features) * np.linalg.norm(features)
            )
            similarities.append((item_id, similarity))
        
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:k]