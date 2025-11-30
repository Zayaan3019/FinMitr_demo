from sentence_transformers import SentenceTransformer
import numpy as np

class TransactionEmbedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """Initialize embedding model (lightweight and fast)"""
        self.model = SentenceTransformer(model_name)
    
    def encode(self, text: str) -> np.ndarray:
        """Generate embedding for text"""
        return self.model.encode(text)
    
    def encode_transaction(self, transaction: dict) -> np.ndarray:
        """Generate embedding for transaction"""
        text = f"{transaction.get('merchant_name', '')} {transaction.get('category', '')} {transaction.get('description', '')}"
        return self.encode(text)
