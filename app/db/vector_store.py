"""
ChromaDB Vector Store Manager with Multi-Tenant Security.
Implements secure data isolation per user.
"""

from typing import Dict, Any, Optional
import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.utils import embedding_functions
import pandas as pd
import time
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class VectorStoreManager:
    """
    Manages ChromaDB vector store with multi-tenant security.

    Critical Security Feature: Each user's data is stored with user_id metadata
    and retrieval is strictly filtered by user_id to prevent data leakage.
    """

    def __init__(self):
        """Initialize ChromaDB client and collection."""
        self._initialize_client()
        self._initialize_collection()

    def _initialize_client(self) -> None:
        """Initialize persistent ChromaDB client."""
        try:
            self.client = chromadb.PersistentClient(
                path=settings.chroma_persist_dir,
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                ),
            )
            logger.info(f"ChromaDB client initialized at {settings.chroma_persist_dir}")
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB client: {e}")
            raise

    def _initialize_collection(self) -> None:
        """Initialize or get the vector collection."""
        try:
            # Use sentence-transformers for embeddings (free, local)
            self.embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=settings.embedding_model
            )

            # Get or create collection
            self.collection = self.client.get_or_create_collection(
                name=settings.vector_collection_name,
                embedding_function=self.embedding_function,
                metadata={"hnsw:space": "cosine"},
            )

            logger.info(f"Collection '{settings.vector_collection_name}' initialized")
        except Exception as e:
            logger.error(f"Failed to initialize collection: {e}")
            raise

    def ingest_user_data(self, user_id: str, dataframe: pd.DataFrame) -> Dict[str, Any]:
        """
        Ingest user transactions into vector store with metadata tagging.

        Args:
            user_id: Unique user identifier
            dataframe: Pandas DataFrame with columns: date, amount, description, category

        Returns:
            Dictionary with ingestion statistics

        Security:
            Each document is tagged with user_id in metadata for strict isolation
        """
        start_time = time.time()

        try:
            # Validate DataFrame
            required_columns = {"date", "amount", "description", "category"}
            if not required_columns.issubset(dataframe.columns):
                missing = required_columns - set(dataframe.columns)
                raise ValueError(f"Missing required columns: {missing}")

            # Prepare documents and metadata
            documents = []
            metadatas = []
            ids = []

            for idx, row in dataframe.iterrows():
                # Create rich text representation for embedding
                doc_text = (
                    f"Date: {row['date']} | "
                    f"Amount: ${row['amount']:.2f} | "
                    f"Category: {row['category']} | "
                    f"Description: {row['description']}"
                )
                documents.append(doc_text)

                # CRITICAL: Add user_id to metadata for multi-tenant filtering
                metadatas.append(
                    {
                        "user_id": user_id,
                        "date": str(row["date"]),
                        "amount": float(row["amount"]),
                        "category": str(row["category"]),
                        "description": str(row["description"]),
                    }
                )

                # Generate unique ID
                ids.append(f"{user_id}_{idx}_{int(time.time() * 1000)}")

            # Batch insert into ChromaDB
            self.collection.add(documents=documents, metadatas=metadatas, ids=ids)

            elapsed = time.time() - start_time

            logger.info(
                f"Ingested {len(documents)} transactions for user {user_id} " f"in {elapsed:.2f}s"
            )

            return {
                "success": True,
                "user_id": user_id,
                "transactions_count": len(documents),
                "embedding_time_seconds": round(elapsed, 2),
            }

        except Exception as e:
            logger.error(f"Ingestion failed for user {user_id}: {e}")
            raise

    def retrieve_context(self, user_id: str, query: str, top_k: int = 10) -> Dict[str, Any]:
        """
        Retrieve relevant transaction context for a user query.

        Args:
            user_id: User identifier (CRITICAL for security filtering)
            query: Natural language query
            top_k: Number of results to retrieve

        Returns:
            Dictionary with retrieved documents and metadata

        Security:
            Uses where filter to ONLY retrieve documents belonging to user_id
        """
        try:
            # CRITICAL SECURITY: Filter by user_id to prevent data leakage
            results = self.collection.query(
                query_texts=[query],
                n_results=top_k,
                where={"user_id": user_id},  # Multi-tenant security filter
            )

            if not results["documents"] or not results["documents"][0]:
                logger.warning(f"No context found for user {user_id}")
                return {"documents": [], "metadatas": [], "distances": [], "count": 0}

            # Extract and format results
            documents = results["documents"][0]
            metadatas = results["metadatas"][0]
            distances = results["distances"][0]

            logger.info(f"Retrieved {len(documents)} context items for user {user_id}")

            return {
                "documents": documents,
                "metadatas": metadatas,
                "distances": distances,
                "count": len(documents),
            }

        except Exception as e:
            logger.error(f"Context retrieval failed for user {user_id}: {e}")
            raise

    def get_user_transactions(self, user_id: str) -> pd.DataFrame:
        """
        Retrieve all transactions for a specific user as DataFrame.

        Args:
            user_id: User identifier

        Returns:
            DataFrame with all user transactions
        """
        try:
            # Get all documents for user
            results = self.collection.get(where={"user_id": user_id}, include=["metadatas"])

            if not results["metadatas"]:
                logger.warning(f"No transactions found for user {user_id}")
                return pd.DataFrame()

            # Convert to DataFrame
            df = pd.DataFrame(results["metadatas"])

            # Convert types
            df["date"] = pd.to_datetime(df["date"])
            df["amount"] = df["amount"].astype(float)

            logger.info(f"Retrieved {len(df)} transactions for user {user_id}")

            return df

        except Exception as e:
            logger.error(f"Failed to get transactions for user {user_id}: {e}")
            raise

    def delete_user_data(self, user_id: str) -> bool:
        """
        Delete all data for a specific user (GDPR compliance).

        Args:
            user_id: User identifier

        Returns:
            True if successful
        """
        try:
            # Get all IDs for the user
            results = self.collection.get(where={"user_id": user_id})

            if results["ids"]:
                self.collection.delete(ids=results["ids"])
                logger.info(f"Deleted {len(results['ids'])} records for user {user_id}")
            else:
                logger.info(f"No data to delete for user {user_id}")

            return True

        except Exception as e:
            logger.error(f"Failed to delete data for user {user_id}: {e}")
            raise

    def get_collection_stats(self) -> Dict[str, Any]:
        """Get statistics about the vector collection."""
        try:
            count = self.collection.count()
            return {
                "collection_name": settings.vector_collection_name,
                "total_documents": count,
                "embedding_model": settings.embedding_model,
            }
        except Exception as e:
            logger.error(f"Failed to get collection stats: {e}")
            return {}


# Global vector store instance
_vector_store: Optional[VectorStoreManager] = None


def get_vector_store() -> VectorStoreManager:
    """Get or create the global vector store instance."""
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStoreManager()
    return _vector_store
