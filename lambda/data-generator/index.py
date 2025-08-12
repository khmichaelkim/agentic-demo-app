import json
import random
import asyncio
import time
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import boto3
import requests
import logging

# Import scenario management
from scenarios import (
    get_scenario_config, 
    calculate_current_tps, 
    get_demo_callout,
    get_transaction_mix,
    should_use_special_features,
    DEFAULT_SCENARIO
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Initialize AWS clients
secrets_client = boto3.client('secretsmanager')
dynamodb = boto3.resource('dynamodb')

# Environment variables
API_GATEWAY_URL = os.environ['API_GATEWAY_URL']
API_KEY_SECRET_ARN = os.environ['API_KEY_SECRET_ARN']
SCENARIO_CONFIG_TABLE = os.environ.get('SCENARIO_CONFIG_TABLE', 'ScenarioConfigTable')

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
HIGH_RISK_LOCATIONS = ['XX-XX', 'UNKNOWN', 'CN-XX', 'RU-XX']

# User pools for different scenarios
CONCENTRATED_USERS = ['user-001', 'user-002', 'user-003']  # For velocity testing
NORMAL_USERS = SAMPLE_USERS  # All users for normal scenarios

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

def get_current_scenario() -> Dict[str, Any]:
    """Get current scenario configuration from DynamoDB"""
    try:
        scenario_table = dynamodb.Table(SCENARIO_CONFIG_TABLE)
        response = scenario_table.get_item(
            Key={'configId': 'current'}
        )
        
        if 'Item' in response:
            return response['Item']
        else:
            # Return default scenario if not configured
            logger.info("No scenario configured, using default")
            return DEFAULT_SCENARIO
            
    except Exception as error:
        logger.error(f"Error getting scenario config: {error}")
        logger.info("Using default scenario due to error")
        return DEFAULT_SCENARIO

def get_scenario_timing(scenario_config: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate scenario timing information"""
    try:
        start_time = scenario_config.get('startTime')
        if start_time:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            elapsed_seconds = int((datetime.utcnow().replace(tzinfo=start_dt.tzinfo) - start_dt).total_seconds())
        else:
            elapsed_seconds = 0
        
        duration = scenario_config.get('duration', 300)
        remaining_seconds = max(0, duration - elapsed_seconds)
        
        return {
            'elapsed_seconds': elapsed_seconds,
            'remaining_seconds': remaining_seconds,
            'is_active': remaining_seconds > 0
        }
    except Exception as error:
        logger.error(f"Error calculating scenario timing: {error}")
        return {
            'elapsed_seconds': 0,
            'remaining_seconds': 300,
            'is_active': True
        }

def reset_scenario_to_normal():
    """Reset the scenario configuration to normal operation"""
    try:
        scenario_table = dynamodb.Table(SCENARIO_CONFIG_TABLE)
        
        # Set current scenario to normal with fresh start time
        reset_config = {
            'configId': 'current',
            'scenario': 'normal',
            'status': 'active',
            'startTime': datetime.utcnow().isoformat() + 'Z',
            'duration': 86400,  # 24 hours (essentially permanent)
            'description': 'Auto-reset to normal operation after scenario expiration'
        }
        
        scenario_table.put_item(Item=reset_config)
        logger.info("✅ Successfully reset scenario configuration to 'normal'")
        
    except Exception as error:
        logger.error(f"❌ Error resetting scenario to normal: {error}")
        # Continue with default scenario if reset fails

def cleanup_expired_scenarios():
    """Proactive cleanup of any expired scenarios that weren't auto-reset"""
    try:
        scenario_config = get_current_scenario()
        scenario_timing = get_scenario_timing(scenario_config)
        
        if not scenario_timing['is_active'] and scenario_config.get('scenario') != 'normal':
            logger.info(f"🧹 Cleaning up expired scenario: {scenario_config.get('scenario')}")
            reset_scenario_to_normal()
            return True
        return False
    except Exception as error:
        logger.error(f"Error during scenario cleanup: {error}")
        return False

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

def select_transaction_value_based_on_mix(transaction_mix: Dict[str, float]) -> float:
    """Select transaction amount based on scenario mix"""
    random_val = random.random()
    cumulative = 0
    
    # Low value transactions (< $100)
    cumulative += transaction_mix.get('low_value', 0.7)
    if random_val <= cumulative:
        return get_random_float(5.0, 99.99)
    
    # Medium value transactions ($100-$1000)  
    cumulative += transaction_mix.get('medium_value', 0.25)
    if random_val <= cumulative:
        return get_random_float(100.0, 999.99)
    
    # High value transactions (>$1000)
    return get_random_float(1000.0, 10000.0)

def generate_scenario_transaction(scenario_config: Dict[str, Any], special_features: Dict[str, bool]) -> Dict[str, Any]:
    """Generate single transaction based on scenario configuration"""
    
    # Select user pool based on scenario
    if special_features.get('concentrate_users'):
        user_pool = CONCENTRATED_USERS
    else:
        user_pool = NORMAL_USERS
    
    user_id = get_random_element(user_pool)
    
    # Get transaction value based on scenario mix
    transaction_mix = get_transaction_mix(scenario_config)
    amount = select_transaction_value_based_on_mix(transaction_mix)
    
    # Select location based on scenario
    if special_features.get('high_risk_locations', 0) > 0 and random.random() < special_features['high_risk_locations']:
        location = get_random_element(HIGH_RISK_LOCATIONS)
    else:
        location = get_random_element(LOCATIONS)
    
    # Generate suspicious amounts for fraud scenarios
    if special_features.get('suspicious_amounts'):
        suspicious_amounts = [999.99, 1000.00, 5000.00, 9999.99, 10000.00]
        if random.random() < 0.3:  # 30% chance of suspicious amount
            amount = get_random_element(suspicious_amounts)
    
    transaction = {
        'userId': user_id,
        'amount': amount,
        'currency': get_random_element(CURRENCIES),
        'merchantId': get_random_element(SAMPLE_MERCHANTS),
        'location': location
    }
    
    return transaction

def calculate_transactions_to_generate(current_tps: int, last_run_time: float = None) -> int:
    """Calculate how many transactions to generate based on TPS and time since last run"""
    # Default to 5-minute interval if no last run time provided
    time_interval = 5 * 60  # 5 minutes in seconds
    
    if last_run_time:
        time_interval = min(time_interval, time.time() - last_run_time)
    
    # Calculate transactions needed for this interval
    target_transactions = int(current_tps * time_interval / 60)  # TPS * minutes
    
    # Add some randomization to avoid perfectly regular patterns
    variance = max(1, target_transactions // 4)  # 25% variance
    actual_transactions = target_transactions + random.randint(-variance, variance)
    
    # Ensure we generate at least 1 transaction
    return max(1, actual_transactions)

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
            return {'status': 'success', 'response': response.json()}
        elif response.status_code == 503:
            logger.warning(f"Transaction throttled (503): {transaction['userId']}")
            return {'status': 'throttled', 'response': response.json() if response.text else {}}
        else:
            logger.error(f"Transaction failed: {response.status_code}, {response.text}")
            return {'status': 'failed', 'error': f"HTTP {response.status_code}"}
            
    except Exception as error:
        logger.error(f"Request error: {error}")
        return {'status': 'error', 'error': str(error)}

def send_burst_transactions(transactions: List[Dict[str, Any]], api_key: str, max_workers: int = 20) -> Dict[str, Any]:
    """Send multiple transactions concurrently using ThreadPoolExecutor"""
    results = {
        'successful': 0,
        'throttled': 0,
        'failed': 0,
        'errors': []
    }
    
    logger.info(f"Starting burst of {len(transactions)} transactions with {max_workers} workers")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all transactions concurrently
        future_to_transaction = {
            executor.submit(send_transaction, txn, api_key): txn 
            for txn in transactions
        }
        
        # Process results as they complete
        for future in as_completed(future_to_transaction):
            try:
                result = future.result(timeout=10)
                if result['status'] == 'success':
                    results['successful'] += 1
                elif result['status'] == 'throttled':
                    results['throttled'] += 1
                else:
                    results['failed'] += 1
                    if 'error' in result:
                        results['errors'].append(result['error'])
            except Exception as error:
                results['failed'] += 1
                results['errors'].append(str(error))
                logger.error(f"Burst transaction error: {error}")
    
    logger.info(f"Burst complete: {results['successful']} successful, {results['throttled']} throttled, {results['failed']} failed")
    return results

def reset_dynamodb_wcu_to_minimum():
    """Reset DynamoDB TransactionsTable WCU to 1 for throttling demo"""
    try:
        import boto3
        dynamodb_client = boto3.client('dynamodb')
        
        # Update TransactionsTable WCU to 1
        response = dynamodb_client.update_table(
            TableName='TransactionsTable',
            ProvisionedThroughput={
                'ReadCapacityUnits': 1,
                'WriteCapacityUnits': 1
            }
        )
        
        logger.info("✅ Successfully reset TransactionsTable WCU to 1 for throttling demo")
        return True
        
    except Exception as error:
        logger.error(f"❌ Failed to reset DynamoDB WCU: {error}")
        return False

 
def generate_forced_scenario_transactions(api_key: str, force_scenario: str) -> Dict[str, Any]:
    """Generate transactions for a forced scenario (bypasses DynamoDB scenario config)"""
    logger.info(f"🎯 Executing forced scenario: {force_scenario}")
    
    # Get scenario configuration from scenarios.py
    from scenarios import get_scenario_config
    scenario_config = get_scenario_config(force_scenario)
    
    # Set a fresh start time for this forced execution
    scenario_config['startTime'] = datetime.utcnow().isoformat() + 'Z'
    
    # Calculate scenario timing based on forced config
    scenario_timing = get_scenario_timing(scenario_config)
    
    # Get scenario info
    scenario_name = scenario_config.get('scenario', force_scenario)
    elapsed_seconds = 0  # Fresh start for forced scenario
    
    # Calculate current TPS based on scenario and timing
    current_tps = calculate_current_tps(scenario_config, elapsed_seconds)
    
    # Calculate how many transactions to generate
    number_of_transactions = calculate_transactions_to_generate(current_tps)
    
    # Get special features for this scenario
    special_features = should_use_special_features(scenario_config)
    
    # Get demo callouts
    demo_callout = get_demo_callout(scenario_config, elapsed_seconds)
    
    logger.info(f"Forced scenario '{scenario_name}' - TPS: {current_tps}, "
                f"Generating: {number_of_transactions} transactions, "
                f"Duration: {scenario_config.get('duration', 0)}s")
    
    if demo_callout:
        logger.info(f"Demo callout: {demo_callout}")
    
    # Execute the forced scenario logic (same as regular scenario execution)
    return execute_scenario_logic(api_key, scenario_config, scenario_timing, scenario_name, 
                                 current_tps, number_of_transactions, special_features, 
                                 demo_callout, elapsed_seconds)

def execute_scenario_logic(api_key: str, scenario_config: Dict[str, Any], scenario_timing: Dict[str, Any], 
                          scenario_name: str, current_tps: int, number_of_transactions: int, 
                          special_features: Dict[str, bool], demo_callout: str, elapsed_seconds: int) -> Dict[str, Any]:
    """Common execution logic for both regular and forced scenarios"""
    
    successful_count = 0
    failed_count = 0
    throttled_count = 0
    
    # Check if we should use burst mode for throttling demo
    use_burst_mode = False
    
    if scenario_name == 'demo_throttling':
        use_burst_mode = True
        logger.info(f"🔥 Throttling scenario active - executing burst mode")
        
        # Reset DynamoDB WCU to 1 before starting burst
        if special_features.get('reset_wcu'):
            logger.info("🔧 Resetting DynamoDB WCU to 1 for throttling demo...")
            reset_dynamodb_wcu_to_minimum()
            time.sleep(2)  # Wait for table update to take effect
    
    if use_burst_mode:
        # SIMPLE BURST: Single wave of concurrent transactions
        burst_size = special_features.get('burst_size', 500)
        
        logger.info(f"🔥 SIMPLE BURST: Launching {burst_size} concurrent transactions")
        
        # Generate batch of transactions for burst
        transactions_to_send = []
        for _ in range(burst_size):
            transaction = generate_scenario_transaction(scenario_config, special_features)
            transactions_to_send.append(transaction)
        
        # Send single burst of concurrent transactions
        burst_results = send_burst_transactions(transactions_to_send, api_key, max_workers=burst_size)
        
        successful_count = burst_results['successful']
        throttled_count = burst_results['throttled']
        failed_count = burst_results['failed']
        
        if throttled_count > 0:
            logger.info(f"🔴 THROTTLING SUCCESS: {throttled_count}/{burst_size} transactions throttled (503 errors)")
        else:
            logger.info(f"⚠️  No throttling detected: All {successful_count} transactions succeeded")
        
        logger.info(f"Burst results: {successful_count} successful, {throttled_count} throttled, {failed_count} failed")
        
    else:
        # Normal sequential mode for other scenarios
        for i in range(number_of_transactions):
            try:
                transaction = generate_scenario_transaction(scenario_config, special_features)
                
                # For velocity scenarios, occasionally generate multiple transactions from same user
                if special_features.get('velocity_patterns') and random.random() < 0.3:
                    # Generate 1-3 additional transactions from same user
                    for _ in range(random.randint(1, 3)):
                        velocity_transaction = generate_scenario_transaction(scenario_config, special_features)
                        velocity_transaction['userId'] = transaction['userId']  # Same user
                        
                        result = send_transaction(velocity_transaction, api_key)
                        if result['status'] == 'success':
                            successful_count += 1
                        elif result['status'] == 'throttled':
                            throttled_count += 1
                        else:
                            failed_count += 1
                        time.sleep(random.uniform(0.1, 0.3))  # Quick succession
                
                # Send the main transaction
                result = send_transaction(transaction, api_key)
                if result['status'] == 'success':
                    successful_count += 1
                elif result['status'] == 'throttled':
                    throttled_count += 1
                else:
                    failed_count += 1
                
                # Variable delay based on scenario predictability
                if special_features.get('predictable_patterns'):
                    delay = 60 / max(current_tps, 1)  # More predictable timing
                else:
                    delay = random.uniform(0.1, 0.8)  # Random delay
                    
                time.sleep(delay)
                    
            except Exception as error:
                logger.error(f"Error generating transaction {i}: {error}")
                failed_count += 1

    result = {
        'successful_count': successful_count,
        'failed_count': failed_count,
        'throttled_count': throttled_count,
        'scenario': scenario_name,
        'current_tps': current_tps,
        'elapsed_seconds': elapsed_seconds,
        'remaining_seconds': scenario_timing.get('remaining_seconds', 0),
        'demo_callout': demo_callout,
        'burst_mode': use_burst_mode
    }
    
    total_sent = successful_count + throttled_count + failed_count
    logger.info(f"Scenario results: {successful_count} successful, {throttled_count} throttled, {failed_count} failed (Total: {total_sent})")
    return result

def generate_scenario_transactions(api_key: str) -> Dict[str, Any]:
    """Generate and send transactions based on current scenario configuration"""
    
    # Proactive cleanup of expired scenarios
    cleanup_expired_scenarios()
    
    # Get current scenario configuration
    scenario_config = get_current_scenario()
    scenario_timing = get_scenario_timing(scenario_config)
    
    # Get scenario info
    scenario_name = scenario_config.get('scenario', 'normal')
    elapsed_seconds = scenario_timing['elapsed_seconds']
    
    # Calculate current TPS based on scenario and timing
    current_tps = calculate_current_tps(scenario_config, elapsed_seconds)
    
    # Calculate how many transactions to generate
    number_of_transactions = calculate_transactions_to_generate(current_tps)
    
    # Get special features for this scenario
    special_features = should_use_special_features(scenario_config)
    
    # Check for demo callouts
    demo_callout = get_demo_callout(scenario_config, elapsed_seconds)
    
    logger.info(f"Scenario '{scenario_name}' - TPS: {current_tps}, "
                f"Generating: {number_of_transactions} transactions, "
                f"Elapsed: {elapsed_seconds}s, Remaining: {scenario_timing['remaining_seconds']}s")
    
    if demo_callout:
        logger.info(f"Demo callout: {demo_callout}")
    
    # Check if scenario is still active - auto-reset to normal when expired
    if not scenario_timing['is_active']:
        logger.info(f"🔄 Scenario '{scenario_name}' has EXPIRED after {elapsed_seconds}s, auto-resetting to 'normal'")
        
        # Automatically reset scenario to normal
        reset_scenario_to_normal()
        
        # Re-fetch scenario config after reset
        scenario_config = get_current_scenario()
        scenario_timing = get_scenario_timing(scenario_config)
        scenario_name = scenario_config.get('scenario', 'normal')
        
        # Recalculate with normal scenario
        current_tps = calculate_current_tps(scenario_config, 0)  # Reset elapsed time
        number_of_transactions = calculate_transactions_to_generate(current_tps)
        
        logger.info(f"✅ Auto-reset complete: Now running '{scenario_name}' scenario with {current_tps} TPS")
    
    # Use shared execution logic
    return execute_scenario_logic(api_key, scenario_config, scenario_timing, scenario_name, 
                                 current_tps, number_of_transactions, special_features, 
                                 demo_callout, elapsed_seconds)

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

def select_transaction_pattern() -> str:
    """Select a transaction pattern for seeding"""
    patterns = ['normal', 'high_value', 'suspicious']
    weights = [70, 20, 10]  # Weighted selection
    return random.choices(patterns, weights=weights)[0]

def generate_transaction_batch(pattern: str) -> List[Dict[str, Any]]:
    """Generate a batch of transactions based on pattern"""
    batch = []
    pattern_config = TRANSACTION_PATTERNS.get(pattern, TRANSACTION_PATTERNS['normal'])
    
    # Generate 1-3 transactions based on velocity
    num_transactions = random.randint(1, pattern_config.get('velocity', 1))
    
    for _ in range(num_transactions):
        user_id = get_random_element(SAMPLE_USERS)
        amount_range = pattern_config['amount_range']
        amount = get_random_float(amount_range[0], amount_range[1])
        
        transaction = {
            'userId': user_id,
            'amount': amount,
            'currency': get_random_element(CURRENCIES),
            'merchantId': get_random_element(SAMPLE_MERCHANTS),
            'location': get_random_element(LOCATIONS)
        }
        
        # Add suspicious patterns for fraud testing
        if pattern == 'fraudulent':
            transaction['location'] = get_random_element(HIGH_RISK_LOCATIONS)
            transaction['amount'] = get_random_element([9999.99, 10000.00, 15000.00])
        
        batch.append(transaction)
    
    return batch

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

def handler(event, context):
    """Lambda handler function - enhanced for scenario-based generation"""
    logger.info('Scenario-aware data generator started')
    
    try:
        # Get API key from Secrets Manager
        api_key = get_api_key()
        
        # Check for forced scenario override from EventBridge
        force_scenario = event.get('forceScenario')
        if force_scenario:
            logger.info(f"🔄 EventBridge forced scenario override: {force_scenario}")
            result = generate_forced_scenario_transactions(api_key, force_scenario)
        else:
            # Generate and send transactions based on current scenario
            result = generate_scenario_transactions(api_key)
        
        logger.info(f"Scenario generation completed: {result}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f"Scenario '{result['scenario']}': {result['successful_count']} successful, {result.get('throttled_count', 0)} throttled",
                'scenario': result['scenario'],
                'successful_count': result['successful_count'],
                'failed_count': result['failed_count'],
                'throttled_count': result.get('throttled_count', 0),
                'current_tps': result['current_tps'],
                'elapsed_seconds': result['elapsed_seconds'],
                'remaining_seconds': result['remaining_seconds'],
                'demo_callout': result.get('demo_callout'),
                'burst_mode': result.get('burst_mode', False),
                'timestamp': datetime.utcnow().isoformat()
            }, default=str)
        }
        
    except Exception as error:
        logger.error(f"Error in scenario data generator: {error}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Failed to generate scenario transactions',
                'message': str(error)
            })
        }

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