import json
import boto3
import os
import inspect
from datetime import datetime, timedelta
from typing import Dict, Any, List
from decimal import Decimal
import logging
from opentelemetry import trace

# Configure logging - use Lambda's built-in logging system (exactly like transaction service)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')

# Environment variables
FRAUD_RULES_TABLE = os.environ['FRAUD_RULES_TABLE']
TRANSACTIONS_TABLE = os.environ['TRANSACTIONS_TABLE']
RISK_THRESHOLD_HIGH = float(os.environ.get('RISK_THRESHOLD_HIGH', '80'))
RISK_THRESHOLD_MEDIUM = float(os.environ.get('RISK_THRESHOLD_MEDIUM', '50'))

# Get DynamoDB table references
fraud_rules_table = dynamodb.Table(FRAUD_RULES_TABLE)
transactions_table = dynamodb.Table(TRANSACTIONS_TABLE)

# In-memory cache for fraud rules (populated on first use)
_fraud_rules_cache = None
_cache_timestamp = None
CACHE_TTL_MINUTES = 10  # Cache rules for 10 minutes

def add_code_location_attributes():
    """
    Add code location attributes to the current span following OpenTelemetry semantic conventions.
    Adds code.file.path, code.line.number, and code.function.name attributes for observability.
    """
    span = trace.get_current_span()
    if span:
        # Get caller information from the stack
        frame = inspect.currentframe()
        if frame and frame.f_back:
            caller_frame = frame.f_back
            file_name = os.path.basename(caller_frame.f_code.co_filename)
            line_number = caller_frame.f_lineno
            function_name = f"{caller_frame.f_code.co_name}"
            
            # Add attributes using OpenTelemetry semantic conventions
            span.set_attribute("code.file.path", file_name)
            span.set_attribute("code.line.number", line_number)
            span.set_attribute("code.function.name", function_name)

def decimal_default(obj):
    """JSON serializer for Decimal objects"""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

def ensure_fraud_rules_exist() -> List[Dict[str, Any]]:
    """Ensure fraud rules exist in DynamoDB, populate with defaults if empty (lazy initialization)"""
    try:
        # Check if DynamoDB has any rules
        response = fraud_rules_table.scan(Limit=1)
        rule_count = response.get('Count', 0)
        
        if rule_count > 0:
            # Rules exist, get all rules
            full_response = fraud_rules_table.scan()
            rules = full_response.get('Items', [])
            logger.info(f"Found {len(rules)} existing fraud rules in DynamoDB")
            return rules
        else:
            # No rules exist - populate with defaults (lazy initialization)
            logger.info("No fraud rules found in DynamoDB, populating with defaults")
            default_rules = get_default_fraud_rules()
            
            # Use batch writer for efficient insertion
            with fraud_rules_table.batch_writer() as batch:
                for rule in default_rules:
                    batch.put_item(Item=rule)
            
            logger.info(f"Successfully populated {len(default_rules)} default fraud rules in DynamoDB")
            return default_rules
            
    except Exception as error:
        logger.error(f"Error accessing DynamoDB for fraud rules: {error}")
        # Return embedded defaults as fallback
        default_rules = get_default_fraud_rules()
        logger.info(f"Using {len(default_rules)} embedded default fraud rules as fallback")
        return default_rules

def get_fraud_rules() -> List[Dict[str, Any]]:
    """Get fraud rules with in-memory caching for performance"""
    global _fraud_rules_cache, _cache_timestamp
    
    # Check if cache is valid (within TTL)
    current_time = datetime.utcnow()
    if (_fraud_rules_cache is not None and _cache_timestamp is not None and 
        (current_time - _cache_timestamp).total_seconds() < (CACHE_TTL_MINUTES * 60)):
        logger.debug(f"Using cached fraud rules ({len(_fraud_rules_cache)} rules)")
        return _fraud_rules_cache
    
    # Cache is stale/empty - refresh from DynamoDB
    try:
        rules = ensure_fraud_rules_exist()
        
        # Update cache
        _fraud_rules_cache = rules
        _cache_timestamp = current_time
        
        logger.info(f"Refreshed fraud rules cache with {len(rules)} rules")
        return rules
        
    except Exception as error:
        logger.error(f"Error refreshing fraud rules: {error}")
        
        # If we have stale cached data, use it as fallback
        if _fraud_rules_cache is not None:
            logger.warning("Using stale cached fraud rules due to refresh error")
            return _fraud_rules_cache
        
        # Last resort - return embedded defaults
        default_rules = get_default_fraud_rules()
        logger.warning(f"Using {len(default_rules)} embedded default fraud rules as last resort")
        return default_rules

def get_default_fraud_rules() -> List[Dict[str, Any]]:
    """Get comprehensive default fraud rules (embedded constants)"""
    return [
        # Amount Threshold Rules
        {
            'ruleId': 'amount-threshold-high',
            'ruleType': 'AMOUNT_THRESHOLD',
            'threshold': 10000,
            'penalty': 40,
            'action': 'FLAG',
            'priority': 1,
            'description': 'Flag transactions over $10,000 as high risk'
        },
        {
            'ruleId': 'amount-threshold-medium',
            'ruleType': 'AMOUNT_THRESHOLD',
            'threshold': 5000,
            'penalty': 25,
            'action': 'REVIEW',
            'priority': 2,
            'description': 'Review transactions over $5,000'
        },
        {
            'ruleId': 'amount-threshold-low',
            'ruleType': 'AMOUNT_THRESHOLD',
            'threshold': 1000,
            'penalty': 10,
            'action': 'MONITOR',
            'priority': 3,
            'description': 'Monitor transactions over $1,000'
        },
        # Velocity Check Rules
        {
            'ruleId': 'velocity-check-high',
            'ruleType': 'VELOCITY_CHECK',
            'threshold': 15,
            'penalty': 35,
            'action': 'FLAG',
            'priority': 1,
            'description': 'Flag users with more than 15 transactions per hour'
        },
        {
            'ruleId': 'velocity-check-medium',
            'ruleType': 'VELOCITY_CHECK',
            'threshold': 8,
            'penalty': 20,
            'action': 'REVIEW',
            'priority': 2,
            'description': 'Review users with more than 8 transactions per hour'
        },
        {
            'ruleId': 'velocity-check-low',
            'ruleType': 'VELOCITY_CHECK',
            'threshold': 5,
            'penalty': 10,
            'action': 'MONITOR',
            'priority': 3,
            'description': 'Monitor users with more than 5 transactions per hour'
        },
        # Location Check Rules
        {
            'ruleId': 'location-risk-unknown',
            'ruleType': 'LOCATION_CHECK',
            'restrictedLocations': ['XX-XX', 'UNKNOWN'],
            'penalty': 20,
            'action': 'REVIEW',
            'priority': 2,
            'description': 'Review transactions from unknown or risky locations'
        },
        {
            'ruleId': 'location-risk-international',
            'ruleType': 'LOCATION_CHECK',
            'restrictedLocations': ['CN-XX', 'RU-XX', 'KP-XX'],
            'penalty': 30,
            'action': 'FLAG',
            'priority': 1,
            'description': 'Flag transactions from high-risk international locations'
        }
    ]

def check_amount_rules(transaction: Dict[str, Any], fraud_rules: List[Dict[str, Any]]) -> int:
    """Check amount-based fraud rules"""
    amount_rules = [rule for rule in fraud_rules if rule.get('ruleType') == 'AMOUNT_THRESHOLD']
    score = 0
    
    for rule in amount_rules:
        threshold = rule.get('threshold', 0)
        penalty = rule.get('penalty', 30)
        
        if transaction.get('amount', 0) > threshold:
            score += penalty
            logger.info(f"Amount rule triggered: {transaction.get('amount')} > {threshold}, penalty: {penalty}")
    
    # Default amount checks if no rules exist
    if not amount_rules:
        amount = transaction.get('amount', 0)
        if amount > 10000:
            score += 40
        elif amount > 5000:
            score += 20
        elif amount > 1000:
            score += 10
    
    return score

def check_velocity_rules(transaction: Dict[str, Any], fraud_rules: List[Dict[str, Any]]) -> int:
    """Check velocity-based fraud rules (transaction frequency)"""
    velocity_rules = [rule for rule in fraud_rules if rule.get('ruleType') == 'VELOCITY_CHECK']
    score = 0
    
    try:
        # Query recent transactions for this user (last hour)
        one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        
        response = transactions_table.query(
            IndexName='UserTransactionHistoryIndex',
            KeyConditionExpression='userId = :userId AND #timestamp > :timestamp',
            ExpressionAttributeNames={
                '#timestamp': 'timestamp'
            },
            ExpressionAttributeValues={
                ':userId': transaction.get('userId'),
                ':timestamp': one_hour_ago
            },
            Select='COUNT',  # Only return count, not full items
            Limit=50  # Cap at 50 transactions (velocity rules max at 15 anyway)
        )
        
        recent_transaction_count = response.get('Count', 0)  # Use Count instead of len(Items)
        
        # Apply velocity rules
        for rule in velocity_rules:
            threshold = rule.get('threshold', 0)
            penalty = rule.get('penalty', 25)
            
            if recent_transaction_count > threshold:
                score += penalty
                logger.info(f"Velocity rule triggered: {recent_transaction_count} transactions > {threshold}, penalty: {penalty}")
        
        # Default velocity checks if no rules exist
        if not velocity_rules:
            if recent_transaction_count > 10:
                score += 35
            elif recent_transaction_count > 5:
                score += 20
            elif recent_transaction_count > 3:
                score += 10
        
    except Exception as error:
        logger.error(f"Error checking velocity rules: {error}")
        # Assign moderate risk if we can't check velocity
        score += 15
    
    return score

def check_location_rules(transaction: Dict[str, Any], fraud_rules: List[Dict[str, Any]]) -> int:
    """Check location-based fraud rules"""
    location_rules = [rule for rule in fraud_rules if rule.get('ruleType') == 'LOCATION_CHECK']
    score = 0
    
    # Simplified location check
    high_risk_locations = ['XX-XX', 'UNKNOWN']
    transaction_location = transaction.get('location', '')
    
    for rule in location_rules:
        restricted_locations = rule.get('restrictedLocations', [])
        penalty = rule.get('penalty', 25)
        
        if restricted_locations and transaction_location in restricted_locations:
            score += penalty
            logger.info(f"Location rule triggered: {transaction_location} is restricted, penalty: {penalty}")
    
    # Default location checks if no rules exist
    if not location_rules:
        if transaction_location in high_risk_locations:
            score += 20
    
    return score

def calculate_risk_score(transaction: Dict[str, Any], fraud_rules: List[Dict[str, Any]]) -> int:
    """Calculate overall risk score for transaction"""
    risk_score = 0
    
    # Check amount-based rules
    risk_score += check_amount_rules(transaction, fraud_rules)
    
    # Check velocity rules (transaction frequency)
    risk_score += check_velocity_rules(transaction, fraud_rules)
    
    # Check location-based rules (simplified)
    risk_score += check_location_rules(transaction, fraud_rules)
    
    # Cap at 100
    return min(risk_score, 100)

def determine_risk_level(risk_score: int) -> str:
    """Determine risk level based on score"""
    if risk_score >= RISK_THRESHOLD_HIGH:
        return 'HIGH'
    elif risk_score >= RISK_THRESHOLD_MEDIUM:
        return 'MEDIUM'
    else:
        return 'LOW'

def handler(event, context):
    """Lambda handler function"""
    # Add code location attributes to the auto-instrumented server span
    add_code_location_attributes()
    
    # Manual trace/span ID injection for distributed tracing correlation
    ctx = trace.get_current_span().get_span_context()
    trace_id = '{trace:032x}'.format(trace=ctx.trace_id)
    span_id = '{span:016x}'.format(span=ctx.span_id)
    
    logger.info(f"Fraud detection request: {json.dumps(event, default=decimal_default)} trace_id={trace_id} span_id={span_id}")

    try:
        transaction = event
        
        # Validate required fields
        required_fields = ['userId', 'amount', 'transactionId']
        for field in required_fields:
            if field not in transaction:
                raise ValueError(f"Missing required transaction field: {field}")

        # Get fraud rules from DynamoDB
        fraud_rules = get_fraud_rules()
        
        # Calculate risk score
        risk_score = calculate_risk_score(transaction, fraud_rules)
        
        # Determine risk level
        risk_level = determine_risk_level(risk_score)
        
        response = {
            'transactionId': transaction['transactionId'],
            'userId': transaction['userId'],
            'riskScore': risk_score,
            'riskLevel': risk_level,
            'timestamp': datetime.utcnow().isoformat(),
            'correlationId': transaction.get('correlationId')
        }

        logger.info(f"Fraud detection response: {json.dumps(response, default=decimal_default)}")
        return response

    except Exception as error:
        logger.error(f"Error in fraud detection: {error}")
        raise error