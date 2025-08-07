import json
import uuid
import boto3
import os
from datetime import datetime
from typing import Dict, Any, Optional
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
lambda_client = boto3.client('lambda')
sqs_client = boto3.client('sqs')
cloudwatch_client = boto3.client('cloudwatch')

# Environment variables
TRANSACTIONS_TABLE = os.environ['DYNAMODB_TABLE_TRANSACTIONS']
FRAUD_RULES_TABLE = os.environ['DYNAMODB_TABLE_FRAUD_RULES']
SQS_QUEUE_URL = os.environ['SQS_QUEUE_URL']
FRAUD_FUNCTION_NAME = os.environ['LAMBDA_FRAUD_FUNCTION_NAME']

# Get DynamoDB table references
transactions_table = dynamodb.Table(TRANSACTIONS_TABLE)
fraud_rules_table = dynamodb.Table(FRAUD_RULES_TABLE)

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

@xray_recorder.capture('perform_fraud_check')
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

@xray_recorder.capture('store_transaction')
def store_transaction(transaction: Dict[str, Any]) -> None:
    """Store transaction in DynamoDB"""
    try:
        transactions_table.put_item(Item=transaction)
        logger.info(f"[{transaction['correlationId']}] Transaction stored in database")
    except Exception as error:
        logger.error(f"[{transaction['correlationId']}] Failed to store transaction: {error}")
        raise

@xray_recorder.capture('send_notification')
def send_notification(transaction: Dict[str, Any]) -> None:
    """Send notification to SQS if needed"""
    try:
        if transaction['status'] == 'DECLINED' or transaction.get('riskLevel') == 'HIGH':
            message = {
                'type': 'TRANSACTION_ALERT',
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
                    'RiskLevel': {
                        'DataType': 'String', 
                        'StringValue': transaction.get('riskLevel', 'UNKNOWN')
                    }
                }
            )
            
            logger.info(f"[{transaction['correlationId']}] Notification queued")
            
    except Exception as error:
        logger.error(f"[{transaction['correlationId']}] Failed to send notification: {error}")
        # Don't fail the transaction if notification fails

@xray_recorder.capture('send_metrics')
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
                    {'Name': 'RiskLevel', 'Value': transaction.get('riskLevel', 'UNKNOWN')}
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
                'Value': transaction['amount'],
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

@xray_recorder.capture('process_transaction')
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

        # Call fraud detection Lambda
        fraud_result = perform_fraud_check(transaction_data)

        # Apply business rules and determine final status
        final_status = apply_business_rules(transaction_data, fraud_result)
        
        # Update transaction with fraud results
        transaction_data['status'] = final_status
        transaction_data['riskScore'] = fraud_result.get('riskScore')
        transaction_data['riskLevel'] = fraud_result.get('riskLevel')

        # Store transaction in DynamoDB
        store_transaction(transaction_data)

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
        
        if transaction_data['status'] == 'DECLINED':
            response['reason'] = get_rejection_reason(fraud_result)

        logger.info(f"[{correlation_id}] Transaction completed: {json.dumps(response)}")
        
        status_code = 201 if transaction_data['status'] == 'APPROVED' else 402
        return create_response(status_code, response)

    except Exception as error:
        logger.error(f"[{correlation_id}] Error processing transaction: {error}")
        send_metric('ProcessingError', 1, correlation_id)
        
        return create_response(500, {
            'error': 'Internal server error',
            'correlationId': correlation_id,
            'message': str(error)
        })

@xray_recorder.capture('lambda_handler')
def handler(event, context):
    """Lambda handler function"""
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