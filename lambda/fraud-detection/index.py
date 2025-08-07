import json
import boto3
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List
from decimal import Decimal
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Patch all AWS SDK calls for X-Ray tracing
patch_all()

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

def decimal_default(obj):
    """JSON serializer for Decimal objects"""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

@xray_recorder.capture('get_fraud_rules')
def get_fraud_rules() -> List[Dict[str, Any]]:
    """Get fraud rules from DynamoDB"""
    try:
        response = fraud_rules_table.scan()
        return response.get('Items', [])
    except Exception as error:
        logger.error(f"Error fetching fraud rules: {error}")
        # Return default rules if DynamoDB fails
        return get_default_fraud_rules()

def get_default_fraud_rules() -> List[Dict[str, Any]]:
    """Get default fraud rules if DynamoDB is unavailable"""
    return [
        {
            'ruleId': 'default-amount-high',
            'ruleType': 'AMOUNT_THRESHOLD',
            'threshold': 10000,
            'penalty': 40,
            'action': 'FLAG',
            'priority': 1
        },
        {
            'ruleId': 'default-amount-medium',
            'ruleType': 'AMOUNT_THRESHOLD',
            'threshold': 5000,
            'penalty': 20,
            'action': 'REVIEW',
            'priority': 2
        },
        {
            'ruleId': 'default-velocity',
            'ruleType': 'VELOCITY_CHECK',
            'threshold': 5,
            'penalty': 25,
            'action': 'FLAG',
            'priority': 1
        }
    ]

@xray_recorder.capture('check_amount_rules')
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

@xray_recorder.capture('check_velocity_rules')
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
            }
        )
        
        recent_transaction_count = len(response.get('Items', []))
        
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

@xray_recorder.capture('check_location_rules')
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

@xray_recorder.capture('calculate_risk_score')
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

@xray_recorder.capture('lambda_handler')
def handler(event, context):
    """Lambda handler function"""
    logger.info(f"Fraud detection request: {json.dumps(event, default=decimal_default)}")

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