import json
import boto3
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
import logging

# Configure logging for Lambda
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Patch all AWS SDK calls for X-Ray tracing
patch_all()

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
logs_client = boto3.client('logs')

# Environment variables
SCENARIO_CONFIG_TABLE = os.environ['SCENARIO_CONFIG_TABLE']
RECENT_TRANSACTIONS_FLOW_TABLE = os.environ.get('RECENT_TRANSACTIONS_FLOW_TABLE')
TRANSACTION_LOG_GROUP_NAME = os.environ.get('TRANSACTION_LOG_GROUP_NAME')

# Get DynamoDB table references
scenario_table = dynamodb.Table(SCENARIO_CONFIG_TABLE)
flow_table = dynamodb.Table(RECENT_TRANSACTIONS_FLOW_TABLE) if RECENT_TRANSACTIONS_FLOW_TABLE else None

# Default scenario configuration
DEFAULT_SCENARIO = {
    'configId': 'current',
    'scenario': 'normal',
    'tps': 12,
    'duration': 300,
    'pattern': {
        'type': 'steady'
    },
    'lastUpdated': datetime.utcnow().isoformat()
}

PREDEFINED_SCENARIOS = {
    'normal': {
        'scenario': 'normal',
        'tps': 12,
        'duration': 300,
        'pattern': {'type': 'steady'},
        'description': 'Normal steady traffic (12 transactions per minute)'
    },
    'demo_throttling': {
        'scenario': 'demo_throttling',
        'tps': 5,  # Normal TPS (burst mode sends 50 concurrent)
        'duration': 30,  # Quick 30-second demo
        'pattern': {
            'type': 'steady'  # Simple pattern
        },
        'description': 'Quick DynamoDB throttling burst - 10 seconds of chaos, done!',
        'demo_callouts': {
            '0': '🔴 BURST MODE: 50 concurrent requests triggering throttling NOW',
            '10': '📊 Throttling complete - observe the 503 errors',
            '20': '✅ System recovered - demo complete'
        }
    }
}

@xray_recorder.capture('get_transaction_flow')
def get_transaction_flow() -> Dict[str, Any]:
    """Get recent transaction flow from DynamoDB flow table for real-time display"""
    try:
        # Use flow table if available
        if flow_table:
            return get_transaction_flow_from_dynamodb()
        
        # Fallback to CloudWatch Logs if flow table not configured
        logger.info("Flow table not configured, falling back to CloudWatch Logs")
        return get_transaction_flow_from_cloudwatch()
        
    except Exception as error:
        logger.error(f"Error getting transaction flow: {error}")
        return {
            'transactions': [],
            'query_status': 'error',
            'message': str(error),
            'total_found': 0
        }

def get_transaction_flow_from_dynamodb() -> Dict[str, Any]:
    """Get recent transactions from DynamoDB flow table"""
    try:
        # Query flow table for today's transactions
        today = datetime.utcnow().strftime('%Y-%m-%d')
        
        response = flow_table.query(
            KeyConditionExpression='pk = :pk',
            ExpressionAttributeValues={
                ':pk': f'FLOW#{today}'
            },
            ScanIndexForward=False,  # Newest first
            Limit=100  # Get last 100 transactions
        )
        
        transactions = []
        for item in response.get('Items', []):
            transactions.append({
                'id': item.get('correlationId', 'unknown')[-8:],  # Last 8 chars
                'status': item.get('status', 'UNKNOWN'),
                'riskLevel': item.get('riskLevel', 'N/A'),
                'amount': item.get('amount', 0),
                'userId': item.get('userId', 'unknown'),
                'timestamp': item.get('timestamp', '')[:19],  # YYYY-MM-DD HH:MM:SS
                'error': item.get('error')
            })
        
        logger.info(f"Found {len(transactions)} transactions in flow table")
        
        return {
            'transactions': transactions,
            'query_status': 'success',
            'total_found': len(transactions),
            'source': 'dynamodb',
            'message': 'Real-time transaction data from flow table'
        }
        
    except Exception as error:
        logger.error(f"Error querying flow table: {error}")
        # Fall back to CloudWatch Logs
        return get_transaction_flow_from_cloudwatch()

def get_transaction_flow_from_cloudwatch() -> Dict[str, Any]:
    """Get recent transaction flow using CloudWatch Logs Insights (fallback)"""
    try:
        # Use environment variable for log group name (deployment-agnostic)
        if not TRANSACTION_LOG_GROUP_NAME:
            logger.error("TRANSACTION_LOG_GROUP_NAME environment variable not set")
            return {
                'transactions': [],
                'query_status': 'error',
                'message': 'Transaction log group name not configured',
                'total_found': 0
            }
        
        # Strategy: Try recent logs first, then fall back to older indexed logs
        transactions = []
        query_status = 'success'
        
        # First attempt: Recent logs (last 5 minutes) - may not be indexed yet
        recent_transactions, recent_status = _query_logs_for_timerange(
            TRANSACTION_LOG_GROUP_NAME, 
            minutes_back=5,
            description="recent"
        )
        
        if recent_transactions:
            transactions = recent_transactions
            logger.info(f"Found {len(recent_transactions)} transactions in recent logs")
        else:
            # Fallback: Older logs (5-15 minutes ago) - should be indexed
            logger.info("Recent logs not indexed yet, trying older logs...")
            fallback_transactions, fallback_status = _query_logs_for_timerange(
                TRANSACTION_LOG_GROUP_NAME, 
                start_minutes_back=15,
                end_minutes_back=5,
                description="fallback"
            )
            
            transactions = fallback_transactions
            query_status = fallback_status
            if fallback_transactions:
                logger.info(f"Found {len(fallback_transactions)} transactions in fallback period")
            else:
                logger.warning("No transactions found in either recent or fallback periods")
        
        return {
            'transactions': transactions,
            'query_status': query_status,
            'total_found': len(transactions),
            'source': 'cloudwatch',
            'time_range': {
                'start': (datetime.utcnow() - timedelta(minutes=15)).isoformat(),
                'end': datetime.utcnow().isoformat()
            },
            'message': f'Found transactions using {"recent" if len(transactions) > 0 and query_status == "success" else "fallback"} query'
        }
        
    except Exception as error:
        logger.error(f"Error getting transaction flow from CloudWatch: {error}")
        return {
            'transactions': [],
            'query_status': 'error',
            'message': str(error),
            'total_found': 0
        }

def _query_logs_for_timerange(
    log_group_name: str, 
    minutes_back: int = None, 
    start_minutes_back: int = None, 
    end_minutes_back: int = None,
    description: str = "query"
) -> tuple:
    """
    Query CloudWatch Logs for a specific timerange
    Returns: (transactions_list, status_string)
    """
    try:
        # Calculate time range
        end_time = datetime.utcnow()
        if minutes_back:
            start_time = end_time - timedelta(minutes=minutes_back)
        else:
            start_time = end_time - timedelta(minutes=start_minutes_back)
            end_time = end_time - timedelta(minutes=end_minutes_back)
        
        # CloudWatch Logs Insights query - include both successful and throttled transactions
        query_string = """
        fields @timestamp, @message
        | filter (@message like /Transaction completed/ and @message like /correlationId/) or @message like /DynamoDB throttling/
        | sort @timestamp desc
        | limit 15
        """
        
        logger.info(f"Starting {description} query: {start_time} to {end_time}")
        
        # Start the query
        response = logs_client.start_query(
            logGroupName=log_group_name,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            queryString=query_string
        )
        
        query_id = response['queryId']
        
        # Poll for results (with timeout)
        max_attempts = 20  # Reduced from 30 for faster fallback
        attempt = 0
        
        while attempt < max_attempts:
            result = logs_client.get_query_results(queryId=query_id)
            
            if result['status'] == 'Complete':
                transactions = []
                for row in result['results']:
                    # Convert CloudWatch Logs result format to dict
                    row_data = {}
                    for field in row:
                        row_data[field['field']] = field['value']
                    
                    # Extract transaction info from log message
                    message = row_data.get('@message', '')
                    timestamp = row_data.get('@timestamp', '')
                    
                    # Parse transaction data from the log message
                    try:
                        import re
                        import json
                        
                        # Handle throttling errors
                        if 'Transaction failed due to DynamoDB throttling' in message or 'DynamoDB throttling' in message:
                            # Extract correlation ID from throttling message
                            correlation_match = re.search(r'\[([^\]]+)\]', message)
                            correlation_id = correlation_match.group(1) if correlation_match else 'unknown'
                            
                            transactions.append({
                                'id': correlation_id[-8:] if len(correlation_id) > 8 else correlation_id,
                                'status': 'THROTTLED',
                                'riskLevel': 'N/A',
                                'amount': 'N/A',
                                'userId': 'N/A', 
                                'timestamp': timestamp[:19] if timestamp else '',
                                'error': '503 Service Unavailable'
                            })
                        
                        # Handle successful transactions with JSON data
                        else:
                            # Look for JSON pattern in the log message  
                            json_match = re.search(r'\{[^}]*"correlationId"[^}]*\}', message)
                            if json_match:
                                transaction_data = json.loads(json_match.group())
                                
                                transactions.append({
                                    'id': transaction_data.get('correlationId', 'unknown')[-8:],  # Last 8 chars
                                    'status': transaction_data.get('status', 'UNKNOWN'),
                                    'riskLevel': transaction_data.get('riskLevel', 'N/A'),
                                    'amount': transaction_data.get('amount', '0'),
                                    'userId': transaction_data.get('userId', 'unknown'),
                                    'timestamp': timestamp[:19] if timestamp else ''  # YYYY-MM-DD HH:MM:SS
                                })
                                
                    except Exception as parse_error:
                        logger.warning(f"Failed to parse transaction data from log: {parse_error}")
                        continue
                
                logger.info(f"{description.capitalize()} query completed: {len(transactions)} transactions found")
                return transactions, 'success'
                
            elif result['status'] == 'Failed':
                logger.error(f"{description.capitalize()} query failed: {result}")
                return [], 'error'
                
            # Wait before checking again
            time.sleep(0.3)
            attempt += 1
        
        # Query timeout
        logger.warning(f"{description.capitalize()} query timed out")
        return [], 'timeout'
        
    except Exception as error:
        logger.error(f"Error in {description} query: {error}")
        return [], 'error'

@xray_recorder.capture('get_current_scenario')
def get_current_scenario() -> Dict[str, Any]:
    """Get current scenario configuration from DynamoDB"""
    try:
        response = scenario_table.get_item(
            Key={'configId': 'current'}
        )
        
        if 'Item' in response:
            return response['Item']
        else:
            # Initialize with default scenario if not exists
            scenario_table.put_item(Item=DEFAULT_SCENARIO)
            return DEFAULT_SCENARIO
            
    except Exception as error:
        logger.error(f"Error getting current scenario: {error}")
        return DEFAULT_SCENARIO

@xray_recorder.capture('set_scenario')
def set_scenario(scenario_name: str, custom_config: Dict[str, Any] = None) -> Dict[str, Any]:
    """Set current scenario configuration"""
    try:
        if scenario_name in PREDEFINED_SCENARIOS:
            config = PREDEFINED_SCENARIOS[scenario_name].copy()
        else:
            config = custom_config or DEFAULT_SCENARIO
        
        # Add metadata
        config['configId'] = 'current'
        config['lastUpdated'] = datetime.utcnow().isoformat()
        config['startTime'] = datetime.utcnow().isoformat()
        
        # Store in DynamoDB
        scenario_table.put_item(Item=config)
        
        logger.info(f"Scenario set to: {scenario_name}")
        return {
            'status': 'success',
            'scenario': config,
            'message': f'Scenario changed to {scenario_name}'
        }
        
    except Exception as error:
        logger.error(f"Error setting scenario {scenario_name}: {error}")
        return {
            'status': 'error',
            'message': str(error)
        }

@xray_recorder.capture('get_scenario_status')
def get_scenario_status() -> Dict[str, Any]:
    """Get current scenario status with timing information"""
    try:
        current = get_current_scenario()
        
        # Calculate elapsed time if scenario has start time
        elapsed_seconds = 0
        if 'startTime' in current:
            start_time = datetime.fromisoformat(current['startTime'].replace('Z', '+00:00'))
            elapsed_seconds = int((datetime.utcnow().replace(tzinfo=start_time.tzinfo) - start_time).total_seconds())
        
        # Determine current phase for curve patterns
        current_phase = None
        time_remaining = current.get('duration', 0) - elapsed_seconds
        
        if current.get('pattern', {}).get('type') == 'curve':
            phases = current['pattern'].get('phases', [])
            phase_start = 0
            for i, phase in enumerate(phases):
                phase_duration = phase.get('duration', 0)
                if phase_start <= elapsed_seconds < phase_start + phase_duration:
                    current_phase = {
                        'index': i,
                        'name': phase.get('name', f'phase_{i}'),
                        'tps': phase.get('tps'),
                        'phase_remaining': phase_start + phase_duration - elapsed_seconds
                    }
                    break
                phase_start += phase_duration
        
        # Check for demo callouts
        demo_tip = None
        callouts = current.get('demo_callouts', {})
        for time_key, tip in callouts.items():
            if elapsed_seconds >= int(time_key) and elapsed_seconds < int(time_key) + 30:
                demo_tip = tip
                break
        
        return {
            'current_scenario': current.get('scenario', 'unknown'),
            'description': current.get('description', ''),
            'elapsed_seconds': elapsed_seconds,
            'time_remaining': max(0, time_remaining),
            'current_phase': current_phase,
            'demo_tip': demo_tip,
            'config': current
        }
        
    except Exception as error:
        logger.error(f"Error getting scenario status: {error}")
        return {
            'current_scenario': 'error',
            'message': str(error)
        }

@xray_recorder.capture('reset_scenario')
def reset_scenario() -> Dict[str, Any]:
    """Reset to normal scenario"""
    return set_scenario('normal')

@xray_recorder.capture('list_scenarios')
def list_scenarios() -> Dict[str, Any]:
    """List all available predefined scenarios"""
    scenarios = []
    for name, config in PREDEFINED_SCENARIOS.items():
        scenarios.append({
            'name': name,
            'description': config.get('description', ''),
            'tps': config.get('tps'),
            'duration': config.get('duration'),
            'pattern_type': config.get('pattern', {}).get('type')
        })
    
    return {
        'scenarios': scenarios,
        'current': get_current_scenario().get('scenario', 'unknown')
    }

def create_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Create API Gateway response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key'
        },
        'body': json.dumps(body, default=str)
    }

@xray_recorder.capture('lambda_handler')
def handler(event, context):
    """Lambda handler for scenario control API"""
    logger.info(f"Scenario control request: {json.dumps(event)}")
    
    try:
        # Extract HTTP method and path
        method = 'GET'
        path = '/demo/scenario'
        body = {}
        
        if 'requestContext' in event and 'http' in event['requestContext']:
            # API Gateway v2.0
            method = event['requestContext']['http']['method']
            path = event['requestContext']['http']['path']
            body = json.loads(event.get('body', '{}')) if event.get('body') else {}
        elif 'requestContext' in event:
            # API Gateway v1.0
            method = event.get('httpMethod', 'GET')
            path = event.get('path', '/demo/scenario')
            body = json.loads(event.get('body', '{}')) if event.get('body') else {}
        
        logger.info(f"Processing {method} {path}")
        
        # Route handling
        if path == '/demo/scenario' and method == 'GET':
            # Get current scenario
            return create_response(200, get_current_scenario())
            
        elif path == '/demo/scenario' and method == 'POST':
            # Set scenario
            scenario_name = body.get('scenario')
            custom_config = body.get('config')
            
            if not scenario_name:
                return create_response(400, {'error': 'scenario parameter required'})
            
            result = set_scenario(scenario_name, custom_config)
            status_code = 200 if result['status'] == 'success' else 400
            return create_response(status_code, result)
            
        elif path == '/demo/status' and method == 'GET':
            # Get scenario status with timing
            return create_response(200, get_scenario_status())
            
        elif path == '/demo/reset' and method == 'POST':
            # Reset to normal
            result = reset_scenario()
            status_code = 200 if result['status'] == 'success' else 400
            return create_response(status_code, result)
            
        elif path == '/demo/scenarios' and method == 'GET':
            # List available scenarios
            return create_response(200, list_scenarios())
            
        elif path == '/demo/flow' and method == 'GET':
            # Get transaction flow from CloudWatch Logs
            return create_response(200, get_transaction_flow())
        
        # Route not found
        return create_response(404, {'error': 'Route not found'})
        
    except Exception as error:
        logger.error(f"Error in scenario control: {error}")
        return create_response(500, {
            'error': 'Internal server error',
            'message': str(error)
        })