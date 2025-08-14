import json
import uuid
import boto3
import os
import time
import random
import requests
import inspect
from datetime import datetime
from typing import Dict, Any, Optional
from decimal import Decimal
import logging
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from urllib.parse import urlparse
from opentelemetry import trace

# Configure logging for Lambda
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients with retry configuration
from botocore.config import Config
from botocore.exceptions import ClientError

# Configure boto3 with NO retries to surface throttling exceptions immediately
retry_config = Config(
    retries={
        'max_attempts': 1,
        'mode': 'standard',
        'total_max_attempts': 1
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
lambda_client = boto3.client('lambda')
sqs_client = boto3.client('sqs')
cloudwatch_client = boto3.client('cloudwatch')

# Environment variables
TRANSACTIONS_TABLE = os.environ['DYNAMODB_TABLE_TRANSACTIONS']
FRAUD_RULES_TABLE = os.environ['DYNAMODB_TABLE_FRAUD_RULES']
RECENT_TRANSACTIONS_FLOW_TABLE = os.environ.get('RECENT_TRANSACTIONS_FLOW_TABLE')
SQS_QUEUE_URL = os.environ['SQS_QUEUE_URL']
FRAUD_FUNCTION_NAME = os.environ['LAMBDA_FRAUD_FUNCTION_NAME']
REWARDS_FUNCTION_NAME = os.environ.get('REWARDS_FUNCTION_NAME')
CARD_VERIFICATION_URL = os.environ.get('CARD_VERIFICATION_URL')

# Get DynamoDB table references
transactions_table = dynamodb.Table(TRANSACTIONS_TABLE)
fraud_rules_table = dynamodb.Table(FRAUD_RULES_TABLE)
flow_table = dynamodb.Table(RECENT_TRANSACTIONS_FLOW_TABLE) if RECENT_TRANSACTIONS_FLOW_TABLE else None

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

def convert_floats_to_decimal(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert float values to Decimal for DynamoDB storage"""
    converted_data = {}
    for key, value in data.items():
        if isinstance(value, float):
            converted_data[key] = Decimal(str(value))
        elif isinstance(value, dict):
            converted_data[key] = convert_floats_to_decimal(value)
        else:
            converted_data[key] = value
    return converted_data

def validate_transaction_data(data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Validate transaction request data"""
    required_fields = ['userId', 'amount', 'currency', 'merchantId']
    
    # Check required fields
    for field in required_fields:
        if field not in data:
            return False, f"Missing required field: {field}"
    
    # Validate amount is positive
    try:
        amount = float(data['amount'])
        if amount <= 0:
            return False, "Amount must be positive"
    except (ValueError, TypeError):
        return False, "Amount must be a valid number"
    
    # Validate currency is 3 characters
    currency = data.get('currency', '')
    if len(currency) != 3:
        return False, "Currency must be 3 characters"
    
    # Set default location if not provided
    if 'location' not in data:
        data['location'] = 'US-XX'
    
    return True, None

def calculate_rewards(transaction: Dict[str, Any]) -> Dict[str, Any]:
    """Call rewards eligibility service"""
    if not REWARDS_FUNCTION_NAME:
        return None
    
    try:
        payload = json.dumps(transaction)
        
        response = lambda_client.invoke(
            FunctionName=REWARDS_FUNCTION_NAME,
            InvocationType='RequestResponse',
            Payload=payload
        )
        
        result = json.loads(response['Payload'].read())
        logger.info(f"[{transaction['correlationId']}] Rewards calculated: {result}")
        return result
        
    except Exception as error:
        logger.error(f"[{transaction['correlationId']}] Rewards calculation failed: {error}")
        return None

def verify_card(transaction: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Call card verification service via HTTP with IAM authentication (external service pattern)"""
    # Skip if verification URL is not configured
    if not CARD_VERIFICATION_URL:
        logger.warning(f"[{transaction['correlationId']}] Card verification URL not configured, skipping verification")
        return True, None
        
    try:
        # Prepare verification payload
        payload = {
            'correlationId': transaction['correlationId'],
            'transactionId': transaction['transactionId']
        }
        payload_json = json.dumps(payload)
        
        # Parse the URL
        parsed_url = urlparse(CARD_VERIFICATION_URL)
        
        # Create AWS request for SigV4 signing
        request = AWSRequest(
            method='POST',
            url=CARD_VERIFICATION_URL,
            data=payload_json,
            headers={
                'Content-Type': 'application/json',
                'Host': parsed_url.netloc
            }
        )
        
        # Get credentials from boto3 session
        session = boto3.Session()
        credentials = session.get_credentials()
        region = session.region_name or 'us-east-1'
        
        # Sign the request with SigV4
        SigV4Auth(credentials, 'lambda', region).add_auth(request)
        
        # Call card verification service with signed request
        logger.info(f"[{transaction['correlationId']}] Calling card verification service with IAM auth")
        response = requests.post(
            CARD_VERIFICATION_URL,
            data=payload_json,
            headers=dict(request.headers),
            timeout=5.0
        )
        
        # Process response
        if response.status_code == 200:
            logger.info(f"[{transaction['correlationId']}] Card verification successful")
            return True, None
        elif response.status_code == 403:
            error_msg = f"Card verification failed - IAM authentication error (403)"
            logger.error(f"[{transaction['correlationId']}] {error_msg}: {response.text}")
            return False, error_msg
        else:
            error_msg = f"Card verification failed with status {response.status_code}"
            logger.error(f"[{transaction['correlationId']}] {error_msg}: {response.text}")
            return False, error_msg
            
    except requests.RequestException as error:
        error_msg = f"Card verification service unavailable: {str(error)}"
        logger.error(f"[{transaction['correlationId']}] {error_msg}")
        return False, error_msg
    except Exception as error:
        error_msg = f"Card verification error: {str(error)}"
        logger.error(f"[{transaction['correlationId']}] {error_msg}")
        return False, error_msg

def perform_fraud_check(transaction: Dict[str, Any]) -> Dict[str, Any]:
    """Call fraud detection Lambda function"""
    try:
        payload = json.dumps(transaction)
        
        response = lambda_client.invoke(
            FunctionName=FRAUD_FUNCTION_NAME,
            InvocationType='RequestResponse',
            Payload=payload
        )
        
        result = json.loads(response['Payload'].read())
        logger.info(f"[{transaction['correlationId']}] Fraud check result: {result}")
        return result
        
    except Exception as error:
        logger.error(f"[{transaction['correlationId']}] Fraud check failed: {error}")
        # Return default risk assessment if fraud service fails
        return {
            'transactionId': transaction['transactionId'],
            'riskScore': 50,
            'riskLevel': 'MEDIUM',
            'timestamp': datetime.utcnow().isoformat()
        }

def apply_business_rules(transaction: Dict[str, Any], fraud_result: Dict[str, Any]) -> str:
    """Apply business rules to determine transaction status"""
    risk_level = fraud_result.get('riskLevel', 'MEDIUM')
    amount = transaction.get('amount', 0)
    
    # Simple business rules
    if risk_level == 'HIGH':
        return 'DECLINED'
    
    if risk_level == 'MEDIUM' and amount > 5000:
        return 'DECLINED'

    return 'APPROVED'

def store_transaction(transaction: Dict[str, Any]) -> None:
    """Store transaction in DynamoDB with throttling handling"""
    correlation_id = transaction.get('correlationId', 'unknown')
    
    # Configurable retry configuration for demo scenarios
    max_retries = int(os.environ.get('MAX_RETRIES', '0'))  # No retries - surface throttling immediately for demo
    base_delay = float(os.environ.get('BASE_DELAY_MS', '0.01'))
    
    retry_count = 0
    
    # Convert floats to Decimal for DynamoDB
    transaction_for_storage = convert_floats_to_decimal(transaction)
    
    while retry_count <= max_retries:
        try:
            transactions_table.put_item(Item=transaction_for_storage)
            
            if retry_count > 0:
                logger.info(f"[{correlation_id}] Transaction stored after {retry_count} retries")
                send_throttling_metric('DynamoDBRetrySuccess', 1, correlation_id)
            else:
                logger.info(f"[{correlation_id}] Transaction stored successfully")
            
            return
            
        except ClientError as error:
            error_code = error.response['Error']['Code']
            
            if error_code == 'ProvisionedThroughputExceededException':
                retry_count += 1
                send_throttling_metric('DynamoDBThrottling', 1, correlation_id)
                
                if retry_count <= max_retries:
                    # Exponential backoff with jitter
                    delay = base_delay * (2 ** retry_count) + random.uniform(0, 0.1)
                    logger.warning(f"[{correlation_id}] DynamoDB throttling detected, retry {retry_count}/{max_retries} after {delay:.2f}s")
                    time.sleep(delay)
                else:
                    logger.error(f"[{correlation_id}] DynamoDB throttling: max retries exhausted")
                    send_throttling_metric('DynamoDBRetryExhausted', 1, correlation_id)
                    raise DynamoDBThrottlingError(f"DynamoDB throttling after {max_retries} retries")
            else:
                logger.error(f"[{correlation_id}] DynamoDB error: {error_code} - {error}")
                raise
                
        except Exception as error:
            logger.error(f"[{correlation_id}] Unexpected error storing transaction: {error}")
            raise

class DynamoDBThrottlingError(Exception):
    """Custom exception for DynamoDB throttling scenarios"""
    pass

def send_throttling_metric(metric_name: str, value: float, correlation_id: str) -> None:
    """Send throttling-specific metrics to CloudWatch"""
    try:
        cloudwatch_client.put_metric_data(
            Namespace='TransactionProcessing/Throttling',
            MetricData=[{
                'MetricName': metric_name,
                'Value': value,
                'Unit': 'Count',
                'Timestamp': datetime.utcnow(),
                'Dimensions': [
                    {'Name': 'Service', 'Value': 'TransactionService'}
                ]
            }]
        )
    except Exception as error:
        logger.error(f"[{correlation_id}] Failed to send throttling metric {metric_name}: {error}")

def send_notification(transaction: Dict[str, Any]) -> None:
    """Send notification to SQS for all transactions"""
    try:
        # Determine message type based on transaction outcome
        if transaction['status'] == 'APPROVED':
            message_type = 'TRANSACTION_SUCCESS'
        else:
            message_type = 'TRANSACTION_ALERT'
        
        message = {
            'type': message_type,
            'transactionId': transaction['transactionId'],
            'userId': transaction['userId'],
            'status': transaction['status'],
            'riskLevel': transaction.get('riskLevel'),
            'amount': transaction['amount'],
            'correlationId': transaction['correlationId'],
            'timestamp': datetime.utcnow().isoformat()
        }

        sqs_client.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody=json.dumps(message),
            MessageAttributes={
                'TransactionId': {
                    'DataType': 'String',
                    'StringValue': transaction['transactionId']
                },
                'MessageType': {
                    'DataType': 'String', 
                    'StringValue': message_type
                },
                'Status': {
                    'DataType': 'String',
                    'StringValue': transaction['status']
                }
            }
        )
        
        logger.info(f"[{transaction['correlationId']}] {message_type} notification queued")
            
    except Exception as error:
        logger.error(f"[{transaction['correlationId']}] Failed to send notification: {error}")
        # Don't fail the transaction if notification fails

def send_metrics(transaction: Dict[str, Any], processing_time: float) -> None:
    """Send metrics to CloudWatch"""
    try:
        metric_data = [
            {
                'MetricName': 'TransactionCount',
                'Value': 1.0,
                'Unit': 'Count',
                'Dimensions': [
                    {'Name': 'Status', 'Value': transaction['status']},
                    {'Name': 'RiskLevel', 'Value': str(transaction.get('riskLevel', 'UNKNOWN'))}
                ]
            },
            {
                'MetricName': 'ProcessingTime',
                'Value': processing_time,
                'Unit': 'Milliseconds',
                'Dimensions': [
                    {'Name': 'Status', 'Value': transaction['status']}
                ]
            },
            {
                'MetricName': 'TransactionAmount',
                'Value': float(transaction['amount']),  # Convert Decimal back to float for CloudWatch
                'Unit': 'None',
                'Dimensions': [
                    {'Name': 'Currency', 'Value': transaction['currency']},
                    {'Name': 'Status', 'Value': transaction['status']}
                ]
            }
        ]

        cloudwatch_client.put_metric_data(
            Namespace='TransactionProcessing',
            MetricData=[{**metric, 'Timestamp': datetime.utcnow()} for metric in metric_data]
        )
        
    except Exception as error:
        logger.error(f"Failed to send metrics: {error}")
        # Don't fail the transaction if metrics fail

def write_to_flow_table(transaction: Dict[str, Any]) -> None:
    """Write transaction to flow table for real-time display"""
    if not flow_table:
        return  # Flow table not configured
    
    try:
        # Create flow record with simplified data for frontend
        timestamp = datetime.utcnow()
        date_key = timestamp.strftime('%Y-%m-%d')
        
        flow_record = {
            'pk': f'FLOW#{date_key}',
            'sk': f'{timestamp.isoformat()}#{transaction["correlationId"]}',
            'transactionId': transaction['transactionId'],
            'correlationId': transaction['correlationId'],
            'status': transaction.get('status', 'UNKNOWN'),
            'riskLevel': transaction.get('riskLevel', 'N/A'),
            'amount': Decimal(str(transaction.get('amount', 0))),
            'userId': transaction.get('userId', 'unknown'),
            'timestamp': timestamp.isoformat(),
            'error': transaction.get('error'),
            'ttl': int(timestamp.timestamp()) + 3600  # Expire after 1 hour
        }
        
        # Remove None values
        flow_record = {k: v for k, v in flow_record.items() if v is not None}
        
        flow_table.put_item(Item=flow_record)
        logger.info(f"[{transaction['correlationId']}] Transaction written to flow table")
        
    except Exception as error:
        logger.error(f"[{transaction.get('correlationId', 'unknown')}] Failed to write to flow table: {error}")
        # Don't fail the transaction if flow table write fails

def send_metric(metric_name: str, value: float, correlation_id: str) -> None:
    """Send individual metric to CloudWatch"""
    try:
        cloudwatch_client.put_metric_data(
            Namespace='TransactionProcessing',
            MetricData=[{
                'MetricName': metric_name,
                'Value': value,
                'Unit': 'Count',
                'Timestamp': datetime.utcnow(),
                'Dimensions': [
                    {'Name': 'Service', 'Value': 'TransactionService'}
                ]
            }]
        )
    except Exception as error:
        logger.error(f"[{correlation_id}] Failed to send metric {metric_name}: {error}")

def get_rejection_reason(fraud_result: Dict[str, Any]) -> str:
    """Get rejection reason based on fraud result"""
    risk_level = fraud_result.get('riskLevel', 'UNKNOWN')
    
    if risk_level == 'HIGH':
        return 'High fraud risk detected'
    return 'Transaction declined by business rules'

def create_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Create API Gateway response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amzn-Trace-Id'
        },
        'body': json.dumps(body)
    }

def process_transaction(body: Dict[str, Any], correlation_id: str, start_time: float) -> Dict[str, Any]:
    """Process transaction logic"""
    try:
        # Validate request
        is_valid, error_message = validate_transaction_data(body)
        if not is_valid:
            send_metric('ValidationError', 1, correlation_id)
            return create_response(400, {
                'error': 'Invalid request',
                'details': error_message,
                'correlationId': correlation_id
            })
        
        transaction_data = {
            **body,
            'transactionId': str(uuid.uuid4()),
            'timestamp': datetime.utcnow().isoformat(),
            'correlationId': correlation_id,
            'status': 'PROCESSING'
        }
        
        logger.info(f"[{correlation_id}] Generated transaction: {json.dumps(transaction_data)}")
        
        # Call card verification service - fails fast if card verification fails
        is_verified, verification_error = verify_card(transaction_data)
        if not is_verified:
            # Card verification failed, return error to client
            send_metric('CardVerificationError', 1, correlation_id)
            write_to_flow_table({
                **transaction_data,
                'status': 'DECLINED',
                'error': verification_error
            })
            return create_response(503, {
                'error': 'Card verification failed',
                'reason': verification_error,
                'correlationId': correlation_id,
                'transactionId': transaction_data['transactionId']
            })

        # Call fraud detection Lambda
        fraud_result = perform_fraud_check(transaction_data)

        # Apply business rules and determine final status
        final_status = apply_business_rules(transaction_data, fraud_result)
        
        # Update transaction with fraud results
        transaction_data['status'] = final_status
        transaction_data['riskScore'] = fraud_result.get('riskScore')
        transaction_data['riskLevel'] = fraud_result.get('riskLevel')

        # Calculate rewards (non-critical path)
        rewards_result = calculate_rewards(transaction_data)
        if rewards_result:
            transaction_data['rewards'] = rewards_result

        # Store transaction in DynamoDB with throttling handling
        try:
            store_transaction(transaction_data)
        except DynamoDBThrottlingError as throttling_error:
            logger.error(f"[{correlation_id}] Transaction failed due to DynamoDB throttling: {throttling_error}")
            send_throttling_metric('TransactionThrottled', 1, correlation_id)
            
            # Write throttled transaction to flow table
            throttled_transaction = {
                **transaction_data,
                'status': 'THROTTLED',
                'error': '503 Service Unavailable'
            }
            write_to_flow_table(throttled_transaction)
            
            return create_response(503, {
                'error': 'Service Unavailable',
                'reason': 'Database temporarily unavailable due to high load',
                'correlationId': correlation_id,
                'retry_after': '30',  # Suggest client retry after 30 seconds
                'transactionId': transaction_data['transactionId']
            })

        # Write successful transaction to flow table for real-time display
        write_to_flow_table(transaction_data)

        # Send notification to SQS if needed
        send_notification(transaction_data)

        # Send custom metrics
        processing_time = (datetime.utcnow().timestamp() * 1000) - start_time
        send_metrics(transaction_data, processing_time)

        # Return response
        response = {
            'transactionId': transaction_data['transactionId'],
            'status': transaction_data['status'],
            'riskScore': transaction_data['riskLevel'],
            'correlationId': transaction_data['correlationId']
        }
        
        if 'rewards' in transaction_data:
            response['rewards'] = transaction_data['rewards']
        
        if transaction_data['status'] == 'DECLINED':
            response['reason'] = get_rejection_reason(fraud_result)

        # Log transaction completion with detailed info for flow visualization
        flow_data = {
            'correlationId': correlation_id,
            'status': transaction_data['status'], 
            'riskLevel': str(transaction_data.get('riskLevel', 'UNKNOWN')),
            'amount': str(transaction_data['amount']),
            'userId': transaction_data['userId'],
            'transactionId': transaction_data['transactionId']
        }
        logger.info(f"[{correlation_id}] Transaction completed: {json.dumps(flow_data)}")
        
        # Use 200 for all successful API calls, status in response body
        # 201 for resource creation (approved transactions that create a new resource)
        # 200 for declined transactions (successfully processed but declined)
        status_code = 201 if transaction_data['status'] == 'APPROVED' else 200
        return create_response(status_code, response)

    except DynamoDBThrottlingError as throttling_error:
        logger.error(f"[{correlation_id}] Transaction failed due to DynamoDB throttling: {throttling_error}")
        send_throttling_metric('TransactionThrottled', 1, correlation_id)
        
        return create_response(503, {
            'error': 'Service Unavailable', 
            'reason': 'Database temporarily unavailable due to high load',
            'correlationId': correlation_id,
            'retry_after': '30'
        })

    except Exception as error:
        logger.error(f"[{correlation_id}] Error processing transaction: {error}")
        send_metric('ProcessingError', 1, correlation_id)
        
        return create_response(500, {
            'error': 'Internal server error',
            'correlationId': correlation_id,
            'message': str(error)
        })

def handler(event, context):
    """Lambda handler function"""
    # Add code location attributes to the auto-instrumented server span
    add_code_location_attributes()
    
    logger.info(f"Event received: {json.dumps(event)}")
    
    correlation_id = str(uuid.uuid4())
    start_time = datetime.utcnow().timestamp() * 1000
    
    try:
        # Handle different event sources (API Gateway, ALB, etc.)
        body = {}
        path = '/'
        http_method = 'GET'
        
        if 'requestContext' in event and 'http' in event['requestContext']:
            # API Gateway v2.0
            body = json.loads(event.get('body', '{}')) if event.get('body') else {}
            path = event['requestContext']['http']['path']
            http_method = event['requestContext']['http']['method']
        elif 'requestContext' in event:
            # API Gateway v1.0
            body = json.loads(event.get('body', '{}')) if event.get('body') else {}
            path = event.get('path', event.get('resource', '/'))
            http_method = event.get('httpMethod', 'GET')
        else:
            # Direct Lambda invocation
            body = event
            path = '/transactions'
            http_method = 'POST'

        logger.info(f"[{correlation_id}] Processing {http_method} {path} request: {json.dumps(body)}")

        # Route handling
        if path == '/health' and http_method == 'GET':
            return create_response(200, {
                'status': 'healthy',
                'timestamp': datetime.utcnow().isoformat(),
                'service': 'transaction-processing'
            })

        if path == '/transactions' and http_method == 'POST':
            return process_transaction(body, correlation_id, start_time)

        # Route not found
        return create_response(404, {
            'error': 'Route not found',
            'correlationId': correlation_id
        })

    except Exception as error:
        logger.error(f"[{correlation_id}] Unhandled error: {error}")
        send_metric('ProcessingError', 1, correlation_id)
        
        return create_response(500, {
            'error': 'Internal server error',
            'correlationId': correlation_id,
            'message': str(error)
        })