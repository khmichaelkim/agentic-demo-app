import json
import boto3
import os
from datetime import datetime, timedelta
from decimal import Decimal
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
METRICS_TABLE = os.environ['METRICS_TABLE']
metrics_table = dynamodb.Table(METRICS_TABLE)

def decimal_default(obj):
    """JSON serializer for Decimal types"""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

def handler(event, context):
    """Return current metrics and time-series data"""
    
    logger.info(f"Received request: {json.dumps(event)}")
    
    try:
        # Get current metrics (using fixed timestamp 0 as key)
        current_response = metrics_table.get_item(
            Key={'metricType': 'CURRENT', 'timestamp': 0}
        )
        
        # Get time-series data (last 15 minutes)
        fifteen_minutes_ago = int((datetime.utcnow() - timedelta(minutes=15)).timestamp())
        
        timeseries_response = metrics_table.query(
            KeyConditionExpression='metricType = :mt AND #ts > :start',
            ExpressionAttributeNames={'#ts': 'timestamp'},
            ExpressionAttributeValues={
                ':mt': 'TIMESERIES',
                ':start': fifteen_minutes_ago
            },
            ScanIndexForward=True
        )
        
        # Build response
        current_metrics = current_response.get('Item', {})
        
        # Provide defaults if no data yet
        if not current_metrics:
            current_metrics = {
                'fraudRate': 0,
                'totalTransactions': 0,
                'approvedCount': 0,
                'declinedCount': 0,
                'highRiskCount': 0,
                'mediumRiskCount': 0,
                'lowRiskCount': 0,
                'totalAmount': 0,
                'fraudAmountPrevented': 0,
                'recentTransactions': [],
                'lastUpdated': datetime.utcnow().isoformat()
            }
        
        response_data = {
            'current': current_metrics,
            'timeseries': timeseries_response.get('Items', []),
            'timestamp': datetime.utcnow().isoformat()
        }
        
        logger.info(f"Returning {len(response_data['timeseries'])} time series data points")
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Allow-Methods': 'OPTIONS,GET'
            },
            'body': json.dumps(response_data, default=decimal_default)
        }
        
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
        }