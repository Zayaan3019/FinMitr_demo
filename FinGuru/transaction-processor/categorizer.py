import re
from typing import Tuple, Dict, List

class TransactionCategorizer:
    """
    Advanced rule-based transaction categorizer with multi-level categorization
    """
    
    # Main categories with subcategories
    CATEGORY_RULES = {
        "Food & Dining": {
            "subcategories": {
                "Restaurants": [
                    "restaurant", "cafe", "diner", "bistro", "grill", "tavern",
                    "mcdonald", "burger king", "wendy", "subway", "kfc", "taco bell",
                    "chipotle", "panera", "panda express", "five guys", "shake shack",
                    "chick-fil-a", "popeyes", "domino", "pizza hut", "papa john",
                    "little caesars", "arby", "sonic", "dairy queen", "jack in the box"
                ],
                "Coffee Shops": [
                    "starbucks", "coffee", "espresso", "dunkin", "peet", "caribou",
                    "dutch bros", "tim hortons", "costa coffee", "cafe nero"
                ],
                "Bars & Nightlife": [
                    "bar", "pub", "brewery", "lounge", "nightclub", "tavern",
                    "wine bar", "cocktail"
                ],
                "Fast Food": [
                    "fast food", "drive thru", "drive-thru", "takeout", "take out"
                ]
            }
        },
        
        "Groceries": {
            "subcategories": {
                "Supermarkets": [
                    "whole foods", "trader joe", "safeway", "kroger", "publix",
                    "albertsons", "wegmans", "giant", "stop & shop", "food lion",
                    "harris teeter", "fred meyer", "ralphs", "vons", "pavilions",
                    "randalls", "tom thumb", "jewel", "acme", "shaw", "star market"
                ],
                "Warehouse Stores": [
                    "costco", "sam's club", "bj's wholesale", "warehouse"
                ],
                "Discount Stores": [
                    "walmart", "target", "aldi", "lidl", "dollar general",
                    "family dollar", "dollar tree", "99 cent", "big lots"
                ],
                "Specialty Stores": [
                    "farmers market", "organic", "natural foods", "sprouts",
                    "fresh market", "fresh thyme"
                ]
            }
        },
        
        "Transportation": {
            "subcategories": {
                "Gas Stations": [
                    "shell", "chevron", "exxon", "mobil", "bp", "arco", "valero",
                    "sunoco", "marathon", "conoco", "phillips 66", "speedway",
                    "circle k", "7-eleven", "wawa", "gas", "fuel", "petrol"
                ],
                "Rideshare": [
                    "uber", "lyft", "via", "juno", "curb", "rideshare"
                ],
                "Public Transit": [
                    "transit", "metro", "subway", "bus", "train", "mta", "bart",
                    "cta", "septa", "wmata", "mbta"
                ],
                "Parking": [
                    "parking", "park", "garage", "lot", "meter"
                ],
                "Tolls": [
                    "toll", "turnpike", "ezpass", "fastrak", "sunpass"
                ],
                "Car Services": [
                    "car wash", "oil change", "auto repair", "mechanic", "tire",
                    "jiffy lube", "midas", "pep boys", "autozone", "advance auto"
                ]
            }
        },
        
        "Shopping": {
            "subcategories": {
                "Online Shopping": [
                    "amazon", "ebay", "etsy", "wish", "alibaba", "aliexpress",
                    "wayfair", "overstock", "zappos", "chewy"
                ],
                "Department Stores": [
                    "macy", "nordstrom", "dillard", "jcpenney", "kohl", "sears",
                    "bloomingdale", "neiman marcus", "saks"
                ],
                "Clothing & Accessories": [
                    "h&m", "zara", "gap", "old navy", "banana republic", "uniqlo",
                    "forever 21", "fashion", "clothing", "apparel", "shoes",
                    "nike", "adidas", "foot locker", "dsw"
                ],
                "Electronics": [
                    "best buy", "apple store", "microsoft store", "gamestop",
                    "micro center", "b&h photo", "fry's electronics"
                ],
                "Home Goods": [
                    "home depot", "lowe's", "ikea", "bed bath beyond", "container store",
                    "williams sonoma", "pottery barn", "crate and barrel"
                ]
            }
        },
        
        "Entertainment": {
            "subcategories": {
                "Streaming Services": [
                    "netflix", "hulu", "disney", "disney+", "hbo", "amazon prime",
                    "spotify", "apple music", "youtube premium", "paramount",
                    "peacock", "discovery+"
                ],
                "Gaming": [
                    "steam", "playstation", "xbox", "nintendo", "epic games",
                    "battle.net", "origin", "gaming"
                ],
                "Movies & Theater": [
                    "amc", "regal", "cinemark", "movie", "cinema", "theater",
                    "imax", "alamo drafthouse"
                ],
                "Events & Tickets": [
                    "ticketmaster", "stubhub", "eventbrite", "concert", "show",
                    "sports", "stadium", "arena"
                ]
            }
        },
        
        "Healthcare": {
            "subcategories": {
                "Pharmacy": [
                    "cvs", "walgreens", "rite aid", "pharmacy", "prescription",
                    "drug store", "chemist"
                ],
                "Medical": [
                    "doctor", "physician", "clinic", "hospital", "medical",
                    "urgent care", "health center", "dentist", "dental"
                ],
                "Fitness": [
                    "gym", "fitness", "24 hour fitness", "la fitness", "planet fitness",
                    "equinox", "orangetheory", "crossfit", "yoga", "pilates"
                ]
            }
        },
        
        "Bills & Utilities": {
            "subcategories": {
                "Phone": [
                    "verizon", "at&t", "t-mobile", "sprint", "phone bill",
                    "mobile", "wireless", "cellular"
                ],
                "Internet & Cable": [
                    "comcast", "xfinity", "spectrum", "cox", "fios", "att fiber",
                    "internet", "cable", "broadband"
                ],
                "Utilities": [
                    "electric", "electricity", "power", "gas company", "water",
                    "pge", "sce", "duke energy", "con edison", "pg&e"
                ]
            }
        },
        
        "Housing": {
            "subcategories": {
                "Rent": [
                    "rent", "rental", "lease", "apartment", "housing"
                ],
                "Mortgage": [
                    "mortgage", "home loan", "property payment"
                ],
                "Home Insurance": [
                    "home insurance", "homeowners insurance", "property insurance"
                ],
                "HOA": [
                    "hoa", "homeowners association", "condo fee"
                ]
            }
        },
        
        "Travel": {
            "subcategories": {
                "Lodging": [
                    "hotel", "motel", "resort", "inn", "airbnb", "vrbo",
                    "marriott", "hilton", "hyatt", "ihg", "best western"
                ],
                "Airlines": [
                    "airline", "airways", "flight", "delta", "united", "american airlines",
                    "southwest", "jetblue", "spirit", "frontier", "alaska airlines"
                ],
                "Car Rental": [
                    "hertz", "enterprise", "avis", "budget", "national",
                    "alamo", "thrifty", "car rental"
                ],
                "Travel Services": [
                    "expedia", "booking.com", "hotels.com", "priceline", "kayak",
                    "travelocity", "orbitz"
                ]
            }
        },
        
        "Personal Care": {
            "subcategories": {
                "Salon & Spa": [
                    "salon", "spa", "barber", "haircut", "hair", "massage",
                    "nail", "manicure", "pedicure"
                ],
                "Beauty": [
                    "sephora", "ulta", "cosmetics", "makeup", "beauty"
                ]
            }
        },
        
        "Education": {
            "subcategories": {
                "Tuition": [
                    "tuition", "university", "college", "school fee"
                ],
                "Books & Supplies": [
                    "textbook", "school supplies", "bookstore", "campus store"
                ],
                "Online Learning": [
                    "coursera", "udemy", "skillshare", "linkedin learning",
                    "masterclass", "edx"
                ]
            }
        },
        
        "Financial": {
            "subcategories": {
                "Bank Fees": [
                    "bank fee", "atm fee", "overdraft", "service charge",
                    "monthly fee", "maintenance fee"
                ],
                "Credit Card Payment": [
                    "credit card payment", "cc payment", "card payment"
                ],
                "Investments": [
                    "robinhood", "e*trade", "td ameritrade", "fidelity",
                    "charles schwab", "vanguard", "investment"
                ],
                "Insurance": [
                    "insurance premium", "life insurance", "auto insurance",
                    "health insurance", "geico", "state farm", "allstate",
                    "progressive", "liberty mutual"
                ]
            }
        },
        
        "Pets": {
            "subcategories": {
                "Pet Supplies": [
                    "petco", "petsmart", "pet supplies", "pet food", "pet store"
                ],
                "Veterinary": [
                    "vet", "veterinary", "animal hospital", "pet clinic"
                ]
            }
        }
    }
    
    def __init__(self):
        """Initialize categorizer"""
        self._compile_patterns()
    
    def _compile_patterns(self):
        """Pre-compile regex patterns for efficiency"""
        self.compiled_rules = {}
        
        for category, data in self.CATEGORY_RULES.items():
            self.compiled_rules[category] = {}
            
            for subcategory, keywords in data.get("subcategories", {}).items():
                patterns = [re.compile(r'\b' + re.escape(kw) + r'\b', re.IGNORECASE) 
                           for kw in keywords]
                self.compiled_rules[category][subcategory] = patterns
    
    def categorize(self, merchant_name: str, description: str = "", amount: float = 0.0) -> Tuple[str, str, float]:
        """
        Categorize a transaction
        
        Args:
            merchant_name: Merchant or business name
            description: Transaction description
            amount: Transaction amount (negative for expenses)
        
        Returns:
            Tuple of (category, subcategory, confidence_score)
        """
        # Combine text for matching
        text = f"{merchant_name} {description}".lower()
        
        # Special case: income transactions
        if amount > 0:
            if any(word in text for word in ["salary", "payroll", "wages", "income", "deposit"]):
                return ("Income", "Salary", 1.0)
            elif any(word in text for word in ["refund", "return", "reimbursement"]):
                return ("Income", "Refund", 0.9)
            else:
                return ("Income", "Other Income", 0.7)
        
        # Search through categories
        best_match = None
        best_confidence = 0.0
        
        for category, subcategories in self.compiled_rules.items():
            for subcategory, patterns in subcategories.items():
                # Count matches
                matches = sum(1 for pattern in patterns if pattern.search(text))
                
                if matches > 0:
                    # Calculate confidence based on number of matches
                    confidence = min(0.95, 0.7 + (matches * 0.1))
                    
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = (category, subcategory)
        
        # Return best match or default
        if best_match:
            return (best_match[0], best_match[1], best_confidence)
        else:
            return ("Other", "Uncategorized", 0.5)
    
    def get_category_stats(self) -> Dict[str, int]:
        """Get statistics about available categories"""
        stats = {}
        for category, data in self.CATEGORY_RULES.items():
            subcategory_count = len(data.get("subcategories", {}))
            keyword_count = sum(
                len(keywords) 
                for keywords in data.get("subcategories", {}).values()
            )
            stats[category] = {
                "subcategories": subcategory_count,
                "keywords": keyword_count
            }
        return stats
    
    def get_all_categories(self) -> List[str]:
        """Get list of all main categories"""
        return list(self.CATEGORY_RULES.keys())
    
    def get_subcategories(self, category: str) -> List[str]:
        """Get subcategories for a main category"""
        if category in self.CATEGORY_RULES:
            return list(self.CATEGORY_RULES[category].get("subcategories", {}).keys())
        return []


# Test function
if __name__ == "__main__":
    categorizer = TransactionCategorizer()
    
    # Test cases
    test_cases = [
        ("Whole Foods Market", "", -45.67),
        ("Starbucks", "Morning coffee", -5.50),
        ("Shell Gas Station", "Fuel", -60.00),
        ("Amazon.com", "Online purchase", -89.99),
        ("Netflix", "Monthly subscription", -15.99),
        ("CVS Pharmacy", "Prescription pickup", -25.00),
        ("Payroll Deposit", "Salary", 3500.00),
        ("Random Store", "Unknown", -20.00),
    ]
    
    print("Testing Transaction Categorizer:")
    print("=" * 80)
    
    for merchant, desc, amount in test_cases:
        category, subcategory, confidence = categorizer.categorize(merchant, desc, amount)
        print(f"Merchant: {merchant:30} | Amount: ${amount:8.2f}")
        print(f"→ {category} / {subcategory} (confidence: {confidence:.2f})")
        print("-" * 80)
    
    # Print stats
    print("\nCategory Statistics:")
    stats = categorizer.get_category_stats()
    for category, counts in stats.items():
        print(f"{category:20} - {counts['subcategories']} subcategories, {counts['keywords']} keywords")
