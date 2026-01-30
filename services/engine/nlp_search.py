# services/engine/nlp_search.py
from typing import Dict, List, Any
import re
import logging

logger = logging.getLogger(__name__)

class NLPSearchEngine:
    def __init__(self):
        # Simple NLP without heavy dependencies for now
        self.intent_keywords = {
            "search": ["find", "search", "look for", "show me"],
            "browse": ["browse", "explore", "see all", "view"],
            "compare": ["compare", "versus", "vs", "difference"],
            "filter": ["under", "below", "above", "between", "less than", "more than"]
        }
        
        self.entity_patterns = {
            "price": r'\$?\d+(?:\.\d{2})?',
            "brand": ["apple", "samsung", "nike", "adidas", "dell", "sony"],
            "color": ["red", "blue", "green", "black", "white", "yellow", "purple"],
            "size": ["small", "medium", "large", "xl", "xxl", "xs"]
        }
    
    def extract_intent(self, query: str) -> Dict:
        """Extract search intent from natural language."""
        query_lower = query.lower()
        
        # Detect intent
        detected_intent = "search"  # default
        for intent, keywords in self.intent_keywords.items():
            if any(keyword in query_lower for keyword in keywords):
                detected_intent = intent
                break
        
        # Extract entities
        entities = {
            "brands": [],
            "colors": [],
            "prices": [],
            "categories": []
        }
        
        # Extract brands
        for brand in self.entity_patterns["brand"]:
            if brand in query_lower:
                entities["brands"].append(brand)
        
        # Extract colors
        for color in self.entity_patterns["color"]:
            if color in query_lower:
                entities["colors"].append(color)
        
        # Extract prices
        prices = re.findall(self.entity_patterns["price"], query)
        entities["prices"] = [float(p.replace('$', '')) for p in prices]
        
        return {
            "intent": detected_intent,
            "entities": entities,
            "original_query": query
        }
    
    def query_expansion(self, query: str) -> List[str]:
        """Expand query with synonyms and related terms."""
        expanded = [query]
        
        # Simple synonym expansion
        synonyms = {
            "phone": ["smartphone", "mobile", "cellphone"],
            "laptop": ["notebook", "computer", "pc"],
            "shoes": ["sneakers", "footwear", "boots"],
            "cheap": ["affordable", "budget", "inexpensive"],
            "expensive": ["premium", "luxury", "high-end"]
        }
        
        query_lower = query.lower()
        for word, syns in synonyms.items():
            if word in query_lower:
                for syn in syns:
                    expanded.append(query_lower.replace(word, syn))
        
        return list(set(expanded))[:5]  # Return up to 5 unique expansions
    
    # def extract_filters_from_nlp(self, query: str) -> Dict[str, Any]:
    #     """Extract filter conditions from natural language."""
    #     filters = {}
        
    #     # Price extraction
    #     if "under" in query or "below" in query or "less than" in query:
    #         price_match = re.search(r'(?:under|below|less than)\s+\$?(\d+)', query.lower())
    #         if price_match:
    #             filters['max_price'] = float(price_match.group(1))
        
    #     if "above" in query or "over" in query or "more than" in query:
    #         price_match = re.search(r'(?:above|over|more than)\s+\$?(\d+)', query.lower())
    #         if price_match:
    #             filters['min_price'] = float(price_match.group(1))
        
    #     # Rating extraction
    #     if "star" in query:
    #         rating_match = re.search(r'(\d+)\s*star', query.lower())
    #         if rating_match:
    #             filters['min_rating'] = float(rating_match.group(1))
        
    #     return filters

    def extract_filters_from_nlp(self, query: str) -> Dict[str, Any]:
        """
        Convert natural-language user query into structured filters.

        These filters are generic and not hardcoded inside FAISS or semantic search.
        Semantic search will obey whatever structure we return here.
        """
        q = (query or "").lower().strip()
        filters: Dict[str, Any] = {}

        include_terms = []
        exclude_terms = []

        # ---------------------------------------
        # CATEGORY DETECTION
        # ---------------------------------------
        if "shirt" in q or "shirts" in q:
            filters["category"] = "shirt"
        elif "jean" in q:
            filters["category"] = "jeans"
        elif "shoe" in q:
            filters["category"] = "shoes"
        # Add more later (kurta, sandals, etc.)

        # ---------------------------------------
        # STYLE DETECTION (formal/casual)
        # ---------------------------------------
        if "formal" in q:
            filters["style"] = "formal"
            include_terms.append("formal")
            exclude_terms.extend(["casual", "hoodie", "t-shirt", "tee", "polo"])

        if "casual" in q:
            filters["style"] = "casual"
            include_terms.append("casual")
            exclude_terms.append("formal")

        # ---------------------------------------
        # ATTACH INCLUDE / EXCLUDE TERMS
        # ---------------------------------------
        if include_terms:
            filters["include_terms"] = include_terms

        if exclude_terms:
            filters["exclude_terms"] = exclude_terms

        return filters