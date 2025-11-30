import pika
import json
import os
import sys
import time
import psycopg2
from psycopg2.extras import RealDictCursor
import weaviate
from categorizer import TransactionCategorizer
from embedder import TransactionEmbedder
from datetime import datetime
from typing import Dict, Any

# Environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
RABBITMQ_URL = os.getenv("RABBITMQ_URL")
WEAVIATE_URL = os.getenv("WEAVIATE_URL", "http://weaviate:8080")

# Initialize components
categorizer = TransactionCategorizer()
embedder = TransactionEmbedder()

# Weaviate client (will be initialized in main)
weaviate_client = None

class TransactionProcessor:
    """Main transaction processor class"""
    
    def __init__(self):
        self.categorizer = categorizer
        self.embedder = embedder
        self.weaviate_client = None
        self.db_conn = None
        self.stats = {
            "processed": 0,
            "categorized": 0,
            "embedded": 0,
            "errors": 0
        }
    
    def connect_weaviate(self):
        """Connect to Weaviate with retry logic"""
        max_retries = 5
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                self.weaviate_client = weaviate.Client(url=WEAVIATE_URL)
                # Test connection
                self.weaviate_client.schema.get()
                print(f"✅ Connected to Weaviate at {WEAVIATE_URL}")
                self._initialize_weaviate_schema()
                return True
            except Exception as e:
                print(f"⚠️ Weaviate connection attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
        
        print("❌ Failed to connect to Weaviate after all retries")
        return False
    
    def _initialize_weaviate_schema(self):
        """Initialize Weaviate schema if not exists"""
        schema = {
            "class": "Transaction",
            "description": "Financial transaction data with semantic embeddings",
            "vectorizer": "none",
            "properties": [
                {
                    "name": "transaction_id",
                    "dataType": ["string"],
                    "description": "Unique transaction identifier"
                },
                {
                    "name": "user_id",
                    "dataType": ["string"],
                    "description": "User identifier",
                    "indexInverted": True
                },
                {
                    "name": "merchant_name",
                    "dataType": ["string"],
                    "description": "Merchant or business name"
                },
                {
                    "name": "category",
                    "dataType": ["string"],
                    "description": "Transaction category",
                    "indexInverted": True
                },
                {
                    "name": "subcategory",
                    "dataType": ["string"],
                    "description": "Transaction subcategory"
                },
                {
                    "name": "amount",
                    "dataType": ["number"],
                    "description": "Transaction amount"
                },
                {
                    "name": "transaction_date",
                    "dataType": ["string"],
                    "description": "Date of transaction"
                },
                {
                    "name": "description",
                    "dataType": ["text"],
                    "description": "Transaction description or memo"
                },
                {
                    "name": "created_at",
                    "dataType": ["string"],
                    "description": "Timestamp when record was created"
                }
            ]
        }
        
        try:
            existing_schema = self.weaviate_client.schema.get()
            existing_classes = [c['class'] for c in existing_schema.get('classes', [])]
            
            if 'Transaction' not in existing_classes:
                self.weaviate_client.schema.create_class(schema)
                print("✅ Weaviate 'Transaction' schema created")
            else:
                print("✅ Weaviate 'Transaction' schema already exists")
        except Exception as e:
            print(f"⚠️ Schema initialization error: {e}")
    
    def get_db_connection(self):
        """Get database connection with retry logic"""
        max_retries = 5
        retry_delay = 3
        
        for attempt in range(max_retries):
            try:
                conn = psycopg2.connect(DATABASE_URL)
                return conn
            except Exception as e:
                print(f"⚠️ Database connection attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
        
        raise Exception("Failed to connect to database after all retries")
    
    def process_transaction(self, transaction_id: str, user_id: str) -> Dict[str, Any]:
        """Process a single transaction: categorize and embed"""
        result = {
            "success": False,
            "transaction_id": transaction_id,
            "categorized": False,
            "embedded": False,
            "error": None
        }
        
        conn = None
        cursor = None
        
        try:
            # Get database connection
            conn = self.get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Fetch transaction from database
            cursor.execute("""
                SELECT id, merchant_name, category, subcategory, amount, 
                       transaction_date, description, embedding_synced
                FROM transactions
                WHERE id = %s
            """, (transaction_id,))
            
            txn = cursor.fetchone()
            
            if not txn:
                result["error"] = f"Transaction {transaction_id} not found"
                print(f"⚠️ {result['error']}")
                return result
            
            # Step 1: Categorize if needed
            if not txn['category'] or txn['category'] == 'Other':
                category, subcategory, confidence = self.categorizer.categorize(
                    merchant_name=txn['merchant_name'] or '',
                    description=txn['description'] or '',
                    amount=float(txn['amount'])
                )
                
                cursor.execute("""
                    UPDATE transactions
                    SET category = %s, subcategory = %s
                    WHERE id = %s
                """, (category, subcategory, transaction_id))
                
                txn['category'] = category
                txn['subcategory'] = subcategory
                result["categorized"] = True
                
                print(f"✅ Categorized: {txn['merchant_name']} -> {category}/{subcategory} (confidence: {confidence:.2f})")
                self.stats["categorized"] += 1
            
            # Step 2: Generate embedding and store in Weaviate
            if self.weaviate_client and not txn['embedding_synced']:
                # Generate embedding
                text = self._build_embedding_text(txn)
                embedding = self.embedder.encode(text)
                
                # Store in Weaviate
                try:
                    data_object = {
                        "transaction_id": str(txn['id']),
                        "user_id": user_id,
                        "merchant_name": txn['merchant_name'] or "Unknown",
                        "category": txn['category'] or "Other",
                        "subcategory": txn['subcategory'] or "",
                        "amount": float(txn['amount']),
                        "transaction_date": str(txn['transaction_date']),
                        "description": txn['description'] or "",
                        "created_at": datetime.utcnow().isoformat()
                    }
                    
                    self.weaviate_client.data_object.create(
                        data_object=data_object,
                        class_name="Transaction",
                        vector=embedding.tolist()
                    )
                    
                    # Mark as synced in PostgreSQL
                    cursor.execute("""
                        UPDATE transactions
                        SET embedding_synced = TRUE
                        WHERE id = %s
                    """, (transaction_id,))
                    
                    result["embedded"] = True
                    print(f"✅ Embedded: {txn['merchant_name']} (vector dim: {len(embedding)})")
                    self.stats["embedded"] += 1
                    
                except Exception as e:
                    print(f"⚠️ Weaviate insert error for {txn['merchant_name']}: {e}")
                    result["error"] = f"Embedding failed: {str(e)}"
            
            # Commit database changes
            conn.commit()
            
            result["success"] = True
            self.stats["processed"] += 1
            
            return result
            
        except Exception as e:
            print(f"❌ Transaction processing error: {e}")
            result["error"] = str(e)
            self.stats["errors"] += 1
            
            if conn:
                conn.rollback()
            
            return result
            
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def _build_embedding_text(self, txn: Dict) -> str:
        """Build text representation for embedding"""
        parts = []
        
        if txn.get('merchant_name'):
            parts.append(txn['merchant_name'])
        
        if txn.get('category'):
            parts.append(f"category: {txn['category']}")
        
        if txn.get('subcategory'):
            parts.append(f"subcategory: {txn['subcategory']}")
        
        if txn.get('description'):
            parts.append(txn['description'])
        
        # Add amount context
        amount = float(txn.get('amount', 0))
        if amount < 0:
            parts.append(f"expense of ${abs(amount):.2f}")
        else:
            parts.append(f"income of ${amount:.2f}")
        
        return " ".join(parts)
    
    def print_stats(self):
        """Print processing statistics"""
        print("\n" + "="*60)
        print("📊 Transaction Processor Statistics")
        print("="*60)
        print(f"Total Processed:  {self.stats['processed']}")
        print(f"Categorized:      {self.stats['categorized']}")
        print(f"Embedded:         {self.stats['embedded']}")
        print(f"Errors:           {self.stats['errors']}")
        print("="*60 + "\n")


def callback(ch, method, properties, body, processor: TransactionProcessor):
    """RabbitMQ message handler"""
    try:
        message = json.loads(body)
        print(f"\n📥 Received message: {message}")
        
        action = message.get('action')
        
        if action == 'categorize_and_embed':
            transaction_id = message.get('transaction_id')
            user_id = message.get('user_id')
            
            if not transaction_id or not user_id:
                print("⚠️ Missing transaction_id or user_id in message")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return
            
            # Process the transaction
            result = processor.process_transaction(transaction_id, user_id)
            
            if result["success"]:
                print(f"✅ Successfully processed transaction {transaction_id}")
            else:
                print(f"❌ Failed to process transaction {transaction_id}: {result.get('error')}")
        
        elif action == 'sync':
            # Handle bulk sync operation
            user_id = message.get('user_id')
            print(f"🔄 Bulk sync requested for user {user_id}")
            # Could trigger batch processing here
        
        else:
            print(f"⚠️ Unknown action: {action}")
        
        # Acknowledge message
        ch.basic_ack(delivery_tag=method.delivery_tag)
        
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in message: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        
    except Exception as e:
        print(f"❌ Callback error: {e}")
        # Negative acknowledge with requeue for transient errors
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def main():
    """Main worker loop"""
    print("\n" + "="*60)
    print("🚀 FinGuru Transaction Processor Worker")
    print("="*60)
    print(f"Database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'N/A'}")
    print(f"RabbitMQ: {RABBITMQ_URL.split('@')[1] if '@' in RABBITMQ_URL else 'N/A'}")
    print(f"Weaviate: {WEAVIATE_URL}")
    print("="*60 + "\n")
    
    # Initialize processor
    processor = TransactionProcessor()
    
    # Wait for dependencies to be ready
    print("⏳ Waiting for services to be ready...")
    time.sleep(15)
    
    # Connect to Weaviate
    print("🔌 Connecting to Weaviate...")
    weaviate_connected = processor.connect_weaviate()
    
    if not weaviate_connected:
        print("⚠️ Continuing without Weaviate (embeddings disabled)")
    
    # Connect to RabbitMQ
    print("🔌 Connecting to RabbitMQ...")
    max_retries = 10
    retry_delay = 5
    connection = None
    
    for attempt in range(max_retries):
        try:
            connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
            channel = connection.channel()
            print("✅ Connected to RabbitMQ")
            break
        except Exception as e:
            print(f"⚠️ RabbitMQ connection attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print("❌ Failed to connect to RabbitMQ after all retries")
                sys.exit(1)
    
    # Declare queue
    channel.queue_declare(queue='transactions', durable=True)
    print("✅ Queue 'transactions' declared")
    
    # Set QoS - process one message at a time
    channel.basic_qos(prefetch_count=1)
    
    # Setup consumer with processor
    channel.basic_consume(
        queue='transactions',
        on_message_callback=lambda ch, method, properties, body: callback(
            ch, method, properties, body, processor
        )
    )
    
    print("\n✅ Worker ready and waiting for messages...")
    print("Press CTRL+C to exit\n")
    
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down worker...")
        processor.print_stats()
        channel.stop_consuming()
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        processor.print_stats()
    finally:
        if connection:
            connection.close()
        print("👋 Worker stopped")


if __name__ == "__main__":
    main()
