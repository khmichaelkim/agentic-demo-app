import json
import boto3
import os
from datetime import datetime
from typing import Dict, Any, List
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Patch all AWS SDK calls for X-Ray tracing
patch_all()

# Initialize AWS clients
cloudwatch_client = boto3.client('cloudwatch')

def send_success_notification(message: Dict[str, Any]) -> None:
    """Handle successful transaction notifications"""
    try:
        logger.info(f"✅ SUCCESS NOTIFICATION: Transaction {message['transactionId']} "
                   f"for user {message['userId']} - ${message['amount']} {message.get('currency', 'USD')} - APPROVED")
        
        # In production, this would:
        # - Send confirmation email to customer
        # - Send SMS notification
        # - Update customer dashboard
        # - Send webhook to merchant
        # - Update loyalty points
        
        # For demo: Log structured data and send metric
        send_notification_metric('SUCCESS', message)
        
    except Exception as error:
        logger.error(f"Failed to send success notification for {message.get('transactionId', 'unknown')}: {error}")

def send_alert_notification(message: Dict[str, Any]) -> None:
    """Handle failure/alert notifications"""
    try:
        risk_level = message.get('riskLevel', 'UNKNOWN')
        status = message.get('status', 'UNKNOWN')
        
        logger.info(f"🚨 ALERT NOTIFICATION: Transaction {message['transactionId']} "
                   f"for user {message['userId']} - ${message['amount']} - {status} (Risk: {risk_level})")
        
        # In production, this would:
        # - Send decline reason to customer
        # - Alert fraud team if high risk
        # - Send notification to merchant
        # - Escalate to manual review if needed
        # - Update user risk profile
        
        # For demo: Log structured data and send metric
        send_notification_metric('ALERT', message)
        
    except Exception as error:
        logger.error(f"Failed to send alert notification for {message.get('transactionId', 'unknown')}: {error}")

@xray_recorder.capture('send_notification_metric')
def send_notification_metric(notification_type: str, message: Dict[str, Any]) -> None:
    """Send custom metrics for notification processing"""
    try:
        cloudwatch_client.put_metric_data(
            Namespace='TransactionProcessing',
            MetricData=[
                {
                    'MetricName': 'NotificationProcessed',
                    'Value': 1.0,
                    'Unit': 'Count',
                    'Timestamp': datetime.utcnow(),
                    'Dimensions': [
                        {
                            'Name': 'NotificationType',
                            'Value': notification_type
                        },
                        {
                            'Name': 'Status',
                            'Value': message.get('status', 'UNKNOWN')
                        },
                        {
                            'Name': 'RiskLevel',
                            'Value': message.get('riskLevel', 'UNKNOWN')
                        }
                    ]
                }
            ]
        )
    except Exception as error:
        logger.error(f"Failed to send notification metric: {error}")

@xray_recorder.capture('process_notification_batch')
def process_notification_batch(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """Process a batch of SQS notification messages"""
    success_count = 0
    alert_count = 0
    error_count = 0
    
    for record in records:
        try:
            # Parse SQS message
            message_body = json.loads(record['body'])
            message_type = message_body.get('type', 'UNKNOWN')
            
            logger.info(f"Processing notification: {message_type} for transaction {message_body.get('transactionId', 'unknown')}")
            
            # Route to appropriate handler
            if message_type == 'TRANSACTION_SUCCESS':
                send_success_notification(message_body)
                success_count += 1
            elif message_type == 'TRANSACTION_ALERT':
                send_alert_notification(message_body)
                alert_count += 1
            else:
                logger.warning(f"Unknown message type: {message_type}")
                error_count += 1
                
        except json.JSONDecodeError as error:
            logger.error(f"Failed to parse SQS message: {error}")
            error_count += 1
        except Exception as error:
            logger.error(f"Failed to process notification: {error}")
            error_count += 1
    
    return {
        'success_notifications': success_count,
        'alert_notifications': alert_count,
        'errors': error_count
    }

@xray_recorder.capture('lambda_handler')
def handler(event, context):
    """Lambda handler for processing SQS notification messages"""
    logger.info(f"Notification processor started with {len(event.get('Records', []))} messages")
    
    try:
        records = event.get('Records', [])
        if not records:
            logger.info("No messages to process")
            return {'statusCode': 200, 'processedCount': 0}
        
        # Process batch of notifications
        results = process_notification_batch(records)
        
        logger.info(f"Notification processing completed: {results}")
        
        # Send batch processing metrics
        total_processed = results['success_notifications'] + results['alert_notifications']
        
        if total_processed > 0:
            cloudwatch_client.put_metric_data(
                Namespace='TransactionProcessing',
                MetricData=[
                    {
                        'MetricName': 'NotificationBatchProcessed',
                        'Value': total_processed,
                        'Unit': 'Count',
                        'Timestamp': datetime.utcnow(),
                        'Dimensions': [
                            {
                                'Name': 'BatchSize',
                                'Value': str(len(records))
                            }
                        ]
                    }
                ]
            )
        
        return {
            'statusCode': 200,
            'processedCount': total_processed,
            'results': results
        }
        
    except Exception as error:
        logger.error(f"Error in notification processor: {error}")
        return {
            'statusCode': 500,
            'error': str(error)
        }