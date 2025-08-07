import json
import random
import asyncio
import time
import os
from datetime import datetime
from typing import Dict, List, Any
import boto3
import requests
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Patch all AWS SDK calls for X-Ray tracing
patch_all()

# Initialize AWS clients
secrets_client = boto3.client('secretsmanager')

# Environment variables
API_GATEWAY_URL = os.environ['API_GATEWAY_URL']
API_KEY_SECRET_ARN = os.environ['API_KEY_SECRET_ARN']

# Sample data for realistic transactions
SAMPLE_USERS = [
    'user-001', 'user-002', 'user-003', 'user-004', 'user-005',
    'user-006', 'user-007', 'user-008', 'user-009', 'user-010',
    'user-011', 'user-012', 'user-013', 'user-014', 'user-015'
]

SAMPLE_MERCHANTS = [
    'amazon-retail', 'starbucks-cafe', 'shell-gas', 'walmart-store',
    'target-retail', 'mcdonalds-fast', 'whole-foods', 'costco-wholesale',
    'uber-rides', 'netflix-streaming', 'spotify-music', 'apple-store',
    'home-depot', 'bestbuy-tech', 'cvs-pharmacy'
]

CURRENCIES = ['USD', 'EUR', 'GBP', 'CAD', 'AUD']
LOCATIONS = ['US-CA', 'US-NY', 'US-TX', 'US-FL', 'EU-GB', 'EU-DE', 'CA-ON', 'AU-NSW']

# Transaction patterns for different scenarios
TRANSACTION_PATTERNS = {
    'normal': {
        'weight': 70,
        'amount_range': [5, 500],
        'velocity': 1
    },
    'high_value': {
        'weight': 15,
        'amount_range': [500, 5000],
        'velocity': 1
    },
    'suspicious': {
        'weight': 10,
        'amount_range': [1000, 15000],  # High amounts
        'velocity': 3  # Multiple transactions
    },
    'fraudulent': {
        'weight': 5,
        'amount_range': [5000, 20000],  # Very high amounts
        'velocity': 5  # Many rapid transactions
    }
}

@xray_recorder.capture('get_api_key')
def get_api_key() -> str:
    """Get API key from Secrets Manager"""
    try:
        response = secrets_client.get_secret_value(SecretId=API_KEY_SECRET_ARN)
        secret = json.loads(response['SecretString'])
        return secret['apiKey']
    except Exception as error:
        logger.error(f"Failed to get API key: {error}")
        raise error

def get_random_element(array: List[Any]) -> Any:
    """Get random element from array"""
    return random.choice(array)

def get_random_int(min_val: int, max_val: int) -> int:
    """Get random integer between min and max"""
    return random.randint(min_val, max_val)

def get_random_float(min_val: float, max_val: float) -> float:
    """Get random float between min and max, rounded to 2 decimals"""
    return round(random.uniform(min_val, max_val), 2)

def select_transaction_pattern() -> Dict[str, Any]:
    """Select transaction pattern based on weighted probabilities"""
    random_val = random.random() * 100
    cumulative = 0
    
    for pattern_name, config in TRANSACTION_PATTERNS.items():
        cumulative += config['weight']
        if random_val <= cumulative:
            return {'name': pattern_name, **config}
    
    return {'name': 'normal', **TRANSACTION_PATTERNS['normal']}

def generate_transaction_batch(pattern: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate batch of transactions based on pattern"""
    transactions = []
    user_id = get_random_element(SAMPLE_USERS)
    
    # Generate multiple transactions for velocity-based patterns
    for _ in range(pattern['velocity']):
        transaction = {
            'userId': user_id,
            'amount': get_random_float(pattern['amount_range'][0], pattern['amount_range'][1]),
            'currency': get_random_element(CURRENCIES),
            'merchantId': get_random_element(SAMPLE_MERCHANTS),
            'location': get_random_element(LOCATIONS)
        }
        
        transactions.append(transaction)
    
    return transactions

@xray_recorder.capture('send_transaction')
def send_transaction(transaction: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    """Send transaction to API Gateway"""
    try:
        url = f"{API_GATEWAY_URL.rstrip('/')}/transactions"
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': api_key
        }
        
        response = requests.post(url, json=transaction, headers=headers, timeout=30)
        
        if response.status_code in range(200, 300):
            logger.info(f"Transaction successful: {transaction['userId']}, ${transaction['amount']}")
            return response.json()
        else:
            logger.error(f"Transaction failed: {response.status_code}, {response.text}")
            raise Exception(f"HTTP {response.status_code}: {response.text}")
            
    except Exception as error:
        logger.error(f"Request error: {error}")
        raise error

@xray_recorder.capture('generate_transactions')
def generate_transactions(api_key: str) -> int:
    """Generate and send transactions"""
    number_of_transactions = get_random_int(10, 30)  # Generate 10-30 transactions per invocation
    successful_count = 0
    failed_count = 0
    
    for i in range(number_of_transactions):
        try:
            pattern = select_transaction_pattern()
            transactions = generate_transaction_batch(pattern)
            
            for transaction in transactions:
                try:
                    send_transaction(transaction, api_key)
                    successful_count += 1
                    
                    # Add small delay between requests to avoid overwhelming the system
                    time.sleep(random.uniform(0.1, 0.5))
                    
                except Exception as error:
                    logger.error(f"Failed to send transaction: {error}")
                    failed_count += 1
                    
        except Exception as error:
            logger.error(f"Error generating transaction batch {i}: {error}")
            failed_count += 1
    
    logger.info(f"Transaction results: {successful_count} successful, {failed_count} failed")
    return successful_count

@xray_recorder.capture('seed_fraud_rules')
def seed_fraud_rules():
    """Seed fraud rules into DynamoDB table"""
    try:
        # Initialize DynamoDB client for seeding
        import boto3
        dynamodb = boto3.resource('dynamodb')
        
        # Get the fraud rules table name from the environment
        # For data generator, we'll construct it based on the API URL pattern
        api_url = os.environ.get('API_GATEWAY_URL', '')
        if 'AgenticDemoAppStack' in api_url or 'agentic-demo-app' in api_url:
            fraud_rules_table_name = 'FraudRulesTable'
        else:
            # Fallback - try to get from environment or use default
            fraud_rules_table_name = os.environ.get('FRAUD_RULES_TABLE', 'FraudRulesTable')
        
        table = dynamodb.Table(fraud_rules_table_name)
        
        # Default fraud rules to populate
        fraud_rules = [
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
            {
                'ruleId': 'velocity-check-high',
                'ruleType': 'VELOCITY_CHECK',
                'threshold': 10,
                'penalty': 35,
                'action': 'FLAG',
                'priority': 1,
                'description': 'Flag users with more than 10 transactions per hour'
            },
            {
                'ruleId': 'velocity-check-medium',
                'ruleType': 'VELOCITY_CHECK',
                'threshold': 5,
                'penalty': 20,
                'action': 'REVIEW',
                'priority': 2,
                'description': 'Review users with more than 5 transactions per hour'
            },
            {
                'ruleId': 'velocity-check-low',
                'ruleType': 'VELOCITY_CHECK',
                'threshold': 3,
                'penalty': 10,
                'action': 'MONITOR',
                'priority': 3,
                'description': 'Monitor users with more than 3 transactions per hour'
            },
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
        
        # Insert rules into DynamoDB
        rules_inserted = 0
        for rule in fraud_rules:
            try:
                # Check if rule already exists
                response = table.get_item(Key={'ruleId': rule['ruleId']})
                if 'Item' not in response:
                    table.put_item(Item=rule)
                    rules_inserted += 1
                    logger.info(f"Inserted fraud rule: {rule['ruleId']}")
                else:
                    logger.info(f"Fraud rule already exists: {rule['ruleId']}")
            except Exception as rule_error:
                logger.error(f"Failed to insert rule {rule['ruleId']}: {rule_error}")
        
        logger.info(f'Successfully seeded {rules_inserted} fraud rules into {fraud_rules_table_name}')
        return rules_inserted
        
    except Exception as error:
        logger.error(f'Error seeding fraud rules: {error}')
        # Don't fail the entire seeding process if fraud rules fail
        return 0

@xray_recorder.capture('generate_seed_data')
def generate_seed_data(api_key: str) -> int:
    """Generate a larger batch of initial transactions"""
    batch_size = 20
    total_batches = 5
    total_generated = 0
    
    for batch in range(total_batches):
        logger.info(f"Generating batch {batch + 1}/{total_batches}")
        batch_successful = 0
        batch_failed = 0
        
        for i in range(batch_size):
            try:
                pattern = select_transaction_pattern()
                transactions = generate_transaction_batch(pattern)
                
                for transaction in transactions:
                    try:
                        send_transaction(transaction, api_key)
                        batch_successful += 1
                    except Exception as error:
                        logger.error(f"Failed to send transaction: {error}")
                        batch_failed += 1
                        
            except Exception as error:
                logger.error(f"Error in batch {batch}, transaction {i}: {error}")
                batch_failed += 1
        
        total_generated += batch_successful
        logger.info(f"Batch {batch + 1} completed: {batch_successful} successful, {batch_failed} failed")
        
        # Wait between batches to avoid rate limiting
        if batch < total_batches - 1:
            time.sleep(2)
    
    return total_generated

@xray_recorder.capture('lambda_handler')
def handler(event, context):
    """Lambda handler function"""
    logger.info('Data generator started')
    
    try:
        # Get API key from Secrets Manager
        api_key = get_api_key()
        
        # Generate and send transactions
        transactions_generated = generate_transactions(api_key)
        
        logger.info(f"Successfully generated {transactions_generated} transactions")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Generated {transactions_generated} transactions',
                'timestamp': datetime.utcnow().isoformat()
            })
        }
        
    except Exception as error:
        logger.error(f"Error in data generator: {error}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Failed to generate transactions',
                'message': str(error)
            })
        }

@xray_recorder.capture('seed_handler')
def seed_handler(event, context):
    """Seed handler for initial data population"""
    logger.info('Seed data generator started')
    
    try:
        # Get API key from Secrets Manager
        api_key = get_api_key()
        
        # Generate initial fraud rules data
        fraud_rules_seeded = seed_fraud_rules()
        
        # Generate a larger batch of initial transactions
        total_generated = generate_seed_data(api_key)
        
        logger.info(f"Seed data generation completed: {fraud_rules_seeded} fraud rules, {total_generated} total transactions")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Seed data generated: {fraud_rules_seeded} fraud rules, {total_generated} transactions',
                'timestamp': datetime.utcnow().isoformat()
            })
        }
        
    except Exception as error:
        logger.error(f"Error in seed data generator: {error}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Failed to generate seed data',
                'message': str(error)
            })
        }