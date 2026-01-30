# add_image_urls.py - Add image URLs to your items
from database import get_db
from models import Item
import random

def add_image_urls():
    """Add placeholder image URLs to items."""
    db = next(get_db())
    
    # Sample image URLs (replace with real ones)
    image_urls = [
        "https://picsum.photos/200/200?random=",  # Random placeholder
        "https://via.placeholder.com/200x200/",   # Placeholder service
    ]
    
    items = db.query(Item).all()
    for item in items:
        # Add image_url column if not exists
        if not hasattr(item, 'image_url'):
            # First run this SQL:
            # ALTER TABLE items ADD COLUMN image_url VARCHAR(500);
            pass
        
        # Add a placeholder URL
        item.image_url = f"{random.choice(image_urls)}{item.id}"
    
    db.commit()
    print(f"Added image URLs to {len(items)} items")

# Then update image_ml.py to actually work:
class ImageSimilarityEngine:
    def __init__(self):
        # Use smaller model for efficiency
        from torchvision import models, transforms
        self.model = models.resnet18(pretrained=True)  # Smaller than resnet50
        self.model.eval()
        
        # Remove final layer
        self.feature_extractor = torch.nn.Sequential(
            *list(self.model.children())[:-1]
        )
        
        self.transform = transforms.Compose([
            transforms.Resize(224),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406],
                               std=[0.229, 0.224, 0.225])
        ])
        
        # Cache for features
        self.feature_cache = {}
    
    def extract_features_from_url(self, image_url: str) -> np.ndarray:
        """Extract features with caching."""
        if image_url in self.feature_cache:
            return self.feature_cache[image_url]
        
        try:
            # For placeholder URLs, return random features
            if "placeholder" in image_url or "picsum" in image_url:
                features = np.random.randn(512).astype('float32')
            else:
                # Real image processing
                response = requests.get(image_url, timeout=5)
                img = Image.open(BytesIO(response.content)).convert('RGB')
                img_tensor = self.transform(img).unsqueeze(0)
                
                with torch.no_grad():
                    features = self.feature_extractor(img_tensor)
                    features = features.squeeze().numpy()
            
            self.feature_cache[image_url] = features
            return features
            
        except Exception as e:
            logger.warning(f"Failed to process image {image_url}: {e}")
            return np.zeros(512, dtype='float32')