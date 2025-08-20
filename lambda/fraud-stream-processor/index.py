import json
import boto3
import os
from datetime import datetime, timedelta
from decimal import Decimal
from collections import defaultdict
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
cloudwatch = boto3.client('cloudwatch')

METRICS_TABLE = os.environ['METRICS_TABLE']
CLOUDWATCH_NAMESPACE = os.environ.get('CLOUDWATCH_NAMESPACE', 'FraudAnalytics')

metrics_table = dynamodb.Table(METRICS_TABLE)

def handler(event, context):
    """Process DDB Stream records and update metrics"""
    
    logger.info(f"Processing {len(event['Records'])} stream records")
    
    # Aggregate metrics from batch
    metrics = {
        'total': 0,
        'approved': 0,
        'declined': 0,
        'high_risk': 0,
        'medium_risk': 0,
        'low_risk': 0,
        'total_amount': Decimal(0),
        'fraud_amount_prevented': Decimal(0),
        'recent_transactions': []
    }
    
    for record in event['Records']:
        if record['eventName'] in ['INSERT', 'MODIFY']:
            new_image = record['dynamodb'].get('NewImage', {})
            
            # Extract transaction data - keep amount as Decimal for DynamoDB
            amount_decimal = Decimal(new_image.get('amount', {}).get('N', '0'))
            risk_score = int(new_image.get('riskScore', {}).get('N', 0))
            
            # Create two versions: one for aggregation (with native types) and one for DynamoDB storage
            transaction_for_aggregation = {
                'transactionId': new_image.get('transactionId', {}).get('S', ''),
                'timestamp': new_image.get('timestamp', {}).get('S', ''),
                'amount': float(amount_decimal),  # float for aggregation
                'status': new_image.get('status', {}).get('S', ''),
                'riskScore': risk_score,
                'riskLevel': new_image.get('riskLevel', {}).get('S', ''),
                'userId': new_image.get('userId', {}).get('S', '')
            }
            
            # DynamoDB-compatible version with Decimal
            transaction_for_storage = {
                'transactionId': new_image.get('transactionId', {}).get('S', ''),
                'timestamp': new_image.get('timestamp', {}).get('S', ''),
                'amount': amount_decimal,  # Decimal for DynamoDB
                'status': new_image.get('status', {}).get('S', ''),
                'riskScore': risk_score,
                'riskLevel': new_image.get('riskLevel', {}).get('S', ''),
                'userId': new_image.get('userId', {}).get('S', '')
            }
            
            # Update aggregates
            metrics['total'] += 1
            
            if transaction_for_aggregation['status'] == 'APPROVED':
                metrics['approved'] += 1
            elif transaction_for_aggregation['status'] == 'DECLINED':
                metrics['declined'] += 1
                if transaction_for_aggregation['riskScore'] >= 50:
                    metrics['fraud_amount_prevented'] += amount_decimal
            
            # Risk level counts
            risk_level = transaction_for_aggregation['riskLevel']
            if risk_level == 'HIGH':
                metrics['high_risk'] += 1
            elif risk_level == 'MEDIUM':
                metrics['medium_risk'] += 1
            elif risk_level == 'LOW' or (risk_level == '' and transaction_for_aggregation['riskScore'] < 50):
                # Count as LOW if explicitly marked or if score < 50
                metrics['low_risk'] += 1
            
            metrics['total_amount'] += amount_decimal
            
            # Keep last 20 transactions for display (use storage version)
            if len(metrics['recent_transactions']) < 20:
                metrics['recent_transactions'].append(transaction_for_storage)
    
    # Calculate rates
    if metrics['total'] > 0:
        fraud_rate = (metrics['declined'] / metrics['total']) * 100
    else:
        fraud_rate = 0
    
    # Update DynamoDB metrics table
    current_time = datetime.utcnow()
    timestamp = int(current_time.timestamp())
    
    try:
        # Get existing current metrics to accumulate
        existing_current = metrics_table.get_item(
            Key={'metricType': 'CURRENT', 'timestamp': 0}
        )
        
        if 'Item' in existing_current:
            # Accumulate with existing metrics
            existing = existing_current['Item']
            metrics['total'] += int(existing.get('totalTransactions', 0))
            metrics['approved'] += int(existing.get('approvedCount', 0))
            metrics['declined'] += int(existing.get('declinedCount', 0))
            metrics['high_risk'] += int(existing.get('highRiskCount', 0))
            metrics['medium_risk'] += int(existing.get('mediumRiskCount', 0))
            metrics['low_risk'] += int(existing.get('lowRiskCount', 0))
            metrics['total_amount'] += existing.get('totalAmount', Decimal(0))
            metrics['fraud_amount_prevented'] += existing.get('fraudAmountPrevented', Decimal(0))
            
            # Merge recent transactions (keep last 20)
            existing_recent = existing.get('recentTransactions', [])
            all_transactions = metrics['recent_transactions'] + existing_recent
            metrics['recent_transactions'] = all_transactions[:20]
            
            # Recalculate fraud rate with accumulated totals
            if metrics['total'] > 0:
                fraud_rate = (metrics['declined'] / metrics['total']) * 100
    except Exception as e:
        logger.warning(f"Could not retrieve existing metrics: {str(e)}")
    
    # Store current snapshot
    metrics_table.put_item(Item={
        'metricType': 'CURRENT',
        'timestamp': 0,  # Fixed timestamp for current metrics
        'fraudRate': Decimal(str(fraud_rate)),
        'totalTransactions': metrics['total'],
        'approvedCount': metrics['approved'],
        'declinedCount': metrics['declined'],
        'highRiskCount': metrics['high_risk'],
        'mediumRiskCount': metrics['medium_risk'],
        'lowRiskCount': metrics['low_risk'],
        'totalAmount': metrics['total_amount'],
        'fraudAmountPrevented': metrics['fraud_amount_prevented'],
        'recentTransactions': metrics['recent_transactions'],
        'lastUpdated': current_time.isoformat(),
        'ttl': timestamp + 3600  # Expire after 1 hour
    })
    
    # Store time-series data for charts (1-minute buckets)
    minute_bucket = timestamp - (timestamp % 60)
    metrics_table.put_item(Item={
        'metricType': 'TIMESERIES',
        'timestamp': minute_bucket,
        'fraudRate': Decimal(str(fraud_rate)),
        'transactionCount': metrics['total'],
        'ttl': timestamp + 86400  # Keep for 24 hours
    })
    
    # Send to CloudWatch for operational monitoring
    try:
        cloudwatch.put_metric_data(
            Namespace=CLOUDWATCH_NAMESPACE,
            MetricData=[
                {
                    'MetricName': 'FraudRate',
                    'Value': fraud_rate,
                    'Unit': 'Percent',
                    'Timestamp': current_time
                },
                {
                    'MetricName': 'TransactionCount', 
                    'Value': metrics['total'],
                    'Unit': 'Count',
                    'Timestamp': current_time
                },
                {
                    'MetricName': 'FraudAmountPrevented',
                    'Value': float(metrics['fraud_amount_prevented']),
                    'Unit': 'None',
                    'Timestamp': current_time
                }
            ]
        )
    except Exception as e:
        logger.error(f"Failed to send CloudWatch metrics: {str(e)}")
    
    logger.info(f"Processed {len(event['Records'])} records successfully")
    return {'statusCode': 200, 'body': json.dumps({'processed': len(event['Records'])})}